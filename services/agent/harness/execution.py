"""统一工具执行边界：策略、审批、幂等与审计入口。"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal

from pydantic_ai import ApprovalRequired, RunContext, ToolDenied
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.toolsets import AbstractToolset, ToolsetTool
from pydantic_ai.toolsets.wrapper import WrapperToolset
from pydantic_ai.tools import ToolDefinition

from config.logging import get_logger
from services.agent.harness.audit import (
    audit_enabled,
    build_audit_record,
    write_audit_record,
)
from services.agent.harness.catalog import (
    POLICY_VERSION,
    ToolRisk,
    ToolSource,
    get_tool_entry,
    list_tool_names,
)
from services.agent.tool_results import ToolResult

logger = get_logger(__name__)

DEFAULT_HARD_DENY_COMMAND_ROOTS: frozenset[str] = frozenset(
    {"op", "deop", "stop", "whitelist", "permission", "wsserver"}
)
DEFAULT_HARD_DENY_TOOLS: frozenset[str] = frozenset()
DEFAULT_MAX_BATCH_COMMANDS = 10
DEFAULT_IDEMPOTENCY_TTL_SECONDS = 600.0
DEFAULT_IDEMPOTENCY_MAX_ENTRIES = 2048

PolicyAction = Literal["allow", "deny", "require_approval"]


class PolicyDecisionKind(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"


@dataclass(frozen=True)
class PolicyDecision:
    action: PolicyDecisionKind
    reason: str
    policy_version: str = POLICY_VERSION
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class IdempotencyRecord:
    result: Any
    created_at: float
    external_state_unknown: bool = False


class IdempotencyStore:
    """有界、带 TTL 的进程内幂等记录。"""

    def __init__(
        self,
        *,
        ttl_seconds: float = DEFAULT_IDEMPOTENCY_TTL_SECONDS,
        max_entries: int = DEFAULT_IDEMPOTENCY_MAX_ENTRIES,
    ) -> None:
        self._ttl = float(ttl_seconds)
        self._max_entries = max(1, int(max_entries))
        self._items: OrderedDict[tuple[str, str, str], IdempotencyRecord] = OrderedDict()
        self._lock = threading.RLock()

    @staticmethod
    def make_key(run_id: str, tool_call_id: str, args_hash: str) -> tuple[str, str, str]:
        return (str(run_id or ""), str(tool_call_id or ""), str(args_hash or ""))

    def get(self, run_id: str, tool_call_id: str, args_hash: str) -> IdempotencyRecord | None:
        key = self.make_key(run_id, tool_call_id, args_hash)
        with self._lock:
            self._purge_unlocked()
            record = self._items.get(key)
            if record is None:
                return None
            self._items.move_to_end(key)
            return record

    def put(
        self,
        run_id: str,
        tool_call_id: str,
        args_hash: str,
        result: Any,
        *,
        external_state_unknown: bool = False,
    ) -> None:
        key = self.make_key(run_id, tool_call_id, args_hash)
        with self._lock:
            self._purge_unlocked()
            self._items[key] = IdempotencyRecord(
                result=result,
                created_at=time.time(),
                external_state_unknown=external_state_unknown,
            )
            self._items.move_to_end(key)
            while len(self._items) > self._max_entries:
                self._items.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    def _purge_unlocked(self, now: float | None = None) -> None:
        current = now if now is not None else time.time()
        expired = [k for k, v in self._items.items() if current - v.created_at >= self._ttl]
        for key in expired:
            del self._items[key]


def normalize_tool_args(tool_args: dict[str, Any] | None) -> dict[str, Any]:
    """规范化工具参数，保证哈希稳定。"""
    if not tool_args:
        return {}
    return _normalize_value(tool_args)  # type: ignore[return-value]


def hash_normalized_args(normalized_args: dict[str, Any]) -> str:
    payload = json.dumps(normalized_args, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def _normalize_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _normalize_value(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (list, tuple)):
        return [_normalize_value(v) for v in value]
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)


def extract_command_root(command: str | None) -> str | None:
    if not command or not isinstance(command, str):
        return None
    text = command.strip()
    if text.startswith("/"):
        text = text[1:]
    if not text:
        return None
    # 取第一个 token，忽略选择器前缀
    token = re.split(r"\s+", text, maxsplit=1)[0]
    return token.lower() if token else None


def summarize_args_for_player(tool_name: str, normalized_args: dict[str, Any], *, max_len: int = 160) -> str:
    entry = get_tool_entry(tool_name)
    include = list(entry.preview.include) if entry is not None and entry.preview.include else list(normalized_args)[:4]
    parts: list[str] = []
    for key in include:
        if key not in normalized_args:
            continue
        value = normalized_args[key]
        text = json.dumps(value, ensure_ascii=False, default=str) if not isinstance(value, str) else value
        if len(text) > 80:
            text = text[:80] + "..."
        parts.append(f"{key}={text}")
    summary = ", ".join(parts) if parts else json.dumps(normalized_args, ensure_ascii=False, default=str)
    if len(summary) > max_len:
        return summary[:max_len] + "..."
    return summary


@dataclass
class PolicyEngine:
    """第一版保守策略引擎。"""

    hard_deny_tools: frozenset[str] = field(default_factory=lambda: DEFAULT_HARD_DENY_TOOLS)
    hard_deny_command_roots: frozenset[str] = field(
        default_factory=lambda: DEFAULT_HARD_DENY_COMMAND_ROOTS
    )
    max_batch_commands: int = DEFAULT_MAX_BATCH_COMMANDS
    mcp_tool_allowlist: frozenset[str] = field(default_factory=frozenset)
    policy_version: str = POLICY_VERSION

    @classmethod
    def from_settings(cls, settings: Any | None) -> PolicyEngine:
        if settings is None:
            return cls()
        hard_tools = set(DEFAULT_HARD_DENY_TOOLS)
        configured_tools = getattr(settings, "hard_deny_tools", None) or []
        hard_tools.update(str(t) for t in configured_tools)

        hard_roots = set(DEFAULT_HARD_DENY_COMMAND_ROOTS)
        configured_roots = getattr(settings, "hard_deny_command_roots", None) or []
        hard_roots.update(str(r).lower() for r in configured_roots)
        # 内置 deny 集合不可被清空：始终并入默认根
        hard_roots |= set(DEFAULT_HARD_DENY_COMMAND_ROOTS)

        allowlist = getattr(settings, "mcp_tool_allowlist", None) or []
        max_batch = int(getattr(settings, "max_batch_commands", DEFAULT_MAX_BATCH_COMMANDS) or DEFAULT_MAX_BATCH_COMMANDS)
        version = str(getattr(settings, "tool_policy_version", POLICY_VERSION) or POLICY_VERSION)
        return cls(
            hard_deny_tools=frozenset(hard_tools),
            hard_deny_command_roots=frozenset(hard_roots),
            max_batch_commands=max(1, max_batch),
            mcp_tool_allowlist=frozenset(str(x) for x in allowlist),
            policy_version=version,
        )

    def decide(
        self,
        tool_name: str,
        normalized_args: dict[str, Any],
        *,
        player_name: str | None,
        approved: bool = False,
    ) -> PolicyDecision:
        entry = get_tool_entry(tool_name)

        if tool_name in self.hard_deny_tools:
            return PolicyDecision(
                action=PolicyDecisionKind.DENY,
                reason=f"工具 {tool_name} 在硬拒绝列表中",
                policy_version=self.policy_version,
            )

        # 命令根硬拒绝（适用于命令类参数）
        denied_root = self._find_denied_command_root(tool_name, normalized_args)
        if denied_root is not None:
            return PolicyDecision(
                action=PolicyDecisionKind.DENY,
                reason=f"命令根 '{denied_root}' 被硬拒绝",
                policy_version=self.policy_version,
                metadata={"command_root": denied_root},
            )

        if tool_name == "run_minecraft_commands":
            commands = normalized_args.get("commands") or []
            if isinstance(commands, list) and len(commands) > self.max_batch_commands:
                return PolicyDecision(
                    action=PolicyDecisionKind.DENY,
                    reason=f"批量命令超过上限 {self.max_batch_commands}",
                    policy_version=self.policy_version,
                    metadata={"count": len(commands), "max": self.max_batch_commands},
                )

        # 未编目 MCP 工具：默认拒绝执行（且通常不会暴露）
        if entry is None:
            if tool_name in self.mcp_tool_allowlist:
                # allowlist 中的 MCP 工具仍要求审批
                if approved:
                    return PolicyDecision(
                        action=PolicyDecisionKind.ALLOW,
                        reason="MCP allowlist 工具已批准",
                        policy_version=self.policy_version,
                    )
                return PolicyDecision(
                    action=PolicyDecisionKind.REQUIRE_APPROVAL,
                    reason="未编目 MCP 工具需要审批",
                    policy_version=self.policy_version,
                )
            return PolicyDecision(
                action=PolicyDecisionKind.DENY,
                reason=f"工具 {tool_name} 未纳入目录且不在 MCP allowlist",
                policy_version=self.policy_version,
            )

        risk = entry.risk

        if risk == ToolRisk.LOW:
            return PolicyDecision(
                action=PolicyDecisionKind.ALLOW,
                reason="低风险查询/展示自动允许",
                policy_version=self.policy_version,
            )

        if risk == ToolRisk.MEDIUM:
            if self._is_current_player_target(tool_name, normalized_args, player_name):
                return PolicyDecision(
                    action=PolicyDecisionKind.ALLOW,
                    reason="中风险工具目标为当前玩家",
                    policy_version=self.policy_version,
                )
            if approved:
                return PolicyDecision(
                    action=PolicyDecisionKind.ALLOW,
                    reason="中风险跨目标工具已批准",
                    policy_version=self.policy_version,
                )
            return PolicyDecision(
                action=PolicyDecisionKind.REQUIRE_APPROVAL,
                reason="中风险工具目标非当前玩家或为广播，需要审批",
                policy_version=self.policy_version,
            )

        # HIGH / DANGEROUS
        if approved:
            return PolicyDecision(
                action=PolicyDecisionKind.ALLOW,
                reason="高/危险工具已获批准",
                policy_version=self.policy_version,
            )
        return PolicyDecision(
            action=PolicyDecisionKind.REQUIRE_APPROVAL,
            reason=f"{risk} 风险工具需要玩家审批",
            policy_version=self.policy_version,
            metadata={"risk": str(risk)},
        )

    def is_tool_exposed(self, tool_name: str) -> bool:
        if tool_name in list_tool_names():
            return True
        return tool_name in self.mcp_tool_allowlist

    def _find_denied_command_root(self, tool_name: str, args: dict[str, Any]) -> str | None:
        candidates: list[str] = []
        if "command" in args and isinstance(args["command"], str):
            candidates.append(args["command"])
        commands = args.get("commands")
        if isinstance(commands, list):
            candidates.extend(c for c in commands if isinstance(c, str))
        for command in candidates:
            root = extract_command_root(command)
            if root and root in self.hard_deny_command_roots:
                return root
        return None

    def _is_current_player_target(
        self,
        tool_name: str,
        args: dict[str, Any],
        player_name: str | None,
    ) -> bool:
        if args.get("broadcast") is True:
            return False

        target = args.get("target")
        if target is None:
            # 默认发送给触发玩家的展示工具
            if tool_name.startswith("send_") or tool_name in {
                "get_player_snapshot",
                "get_inventory_snapshot",
                "find_entities",
            }:
                return True
            return True

        if not isinstance(target, str):
            return False
        target_text = target.strip()
        if target_text in {"@s", ""}:
            return True
        if player_name and target_text == player_name:
            return True
        # 选择器 @a/@e/@p 等视为非“仅当前玩家”
        if target_text.startswith("@"):
            return False
        return bool(player_name) and target_text == player_name


def classify_tool_exception(exc: BaseException, *, tool_name: str) -> ToolResult:
    text = str(exc) or exc.__class__.__name__
    lower = text.lower()
    if "timeout" in lower or "deadline" in lower:
        entry = get_tool_entry(tool_name)
        side_effect = bool(entry.may_have_external_side_effects) if entry is not None else True
        return ToolResult.failure(
            f"工具执行超时: {tool_name}",
            error_kind="TRANSIENT",
            retryable=not side_effect and (entry is not None and entry.risk == ToolRisk.LOW),
            external_state_unknown=side_effect,
            diagnostic_summary=text,
        )
    return ToolResult.failure(
        f"工具执行失败: {tool_name}",
        error_kind="INTERNAL",
        retryable=False,
        diagnostic_summary=text,
    )


def materialize_tool_result(result: Any) -> Any:
    """将 ToolResult 转为模型可消费的文本，其它类型原样返回。"""
    if isinstance(result, ToolResult):
        return str(result)
    return result


_GLOBAL_IDEMPOTENCY = IdempotencyStore()


def get_idempotency_store() -> IdempotencyStore:
    return _GLOBAL_IDEMPOTENCY


def reset_idempotency_store() -> None:
    _GLOBAL_IDEMPOTENCY.clear()


@dataclass
class HarnessToolset(WrapperToolset[Any]):
    """统一策略/审批/幂等/审计包装层。"""

    policy: PolicyEngine = field(default_factory=PolicyEngine)
    idempotency: IdempotencyStore = field(default_factory=get_idempotency_store)

    async def get_tools(self, ctx: RunContext[Any]) -> dict[str, ToolsetTool[Any]]:
        tools = await self.wrapped.get_tools(ctx)
        return {
            name: tool
            for name, tool in tools.items()
            if self.policy.is_tool_exposed(name)
        }

    async def call_tool(
        self,
        name: str,
        tool_args: dict[str, Any],
        ctx: RunContext[Any],
        tool: ToolsetTool[Any],
    ) -> Any:
        start = time.perf_counter()
        normalized = normalize_tool_args(tool_args)
        args_hash = hash_normalized_args(normalized)
        deps = getattr(ctx, "deps", None)
        player_name = getattr(deps, "player_name", None)
        run_id = getattr(deps, "run_id", None) or getattr(ctx, "run_id", None) or ""
        tool_call_id = getattr(ctx, "tool_call_id", None) or ""
        settings = getattr(deps, "settings", None)
        entry = get_tool_entry(name)

        # 1) 幂等命中
        if run_id and tool_call_id:
            cached = self.idempotency.get(str(run_id), str(tool_call_id), args_hash)
            if cached is not None:
                logger.info(
                    "tool_idempotent_hit",
                    tool=name,
                    run_id=run_id,
                    tool_call_id=tool_call_id,
                )
                self._audit(
                    settings=settings,
                    tool_name=name,
                    parameters=normalized,
                    ctx=ctx,
                    status="success",
                    duration_ms=_duration_ms(start),
                    result=cached.result,
                )
                return materialize_tool_result(cached.result)

        # 2) 策略决策
        decision = self.policy.decide(
            name,
            normalized,
            player_name=player_name,
            approved=bool(getattr(ctx, "tool_call_approved", False)),
        )

        if decision.action == PolicyDecisionKind.DENY:
            denied = ToolResult.failure(
                decision.reason,
                error_kind="DENIED",
                retryable=False,
                diagnostic_summary=decision.reason,
            )
            self._audit(
                settings=settings,
                tool_name=name,
                parameters=normalized,
                ctx=ctx,
                status="failure",
                duration_ms=_duration_ms(start),
                result=denied,
            )
            return ToolDenied(message=decision.reason)

        if decision.action == PolicyDecisionKind.REQUIRE_APPROVAL:
            summary = summarize_args_for_player(name, normalized)
            raise ApprovalRequired(
                metadata={
                    "tool_name": name,
                    "normalized_args": normalized,
                    "args_hash": args_hash,
                    "args_summary": summary,
                    "policy_version": decision.policy_version,
                    "reason": decision.reason,
                    "risk": str(entry.risk) if entry is not None else "unknown",
                    "player_name": player_name,
                    "run_id": run_id,
                    "conversation_id": getattr(deps, "conversation_id", None),
                }
            )

        # 3) 执行
        try:
            raw_result = await super().call_tool(name, tool_args, ctx, tool)
        except ApprovalRequired:
            raise
        except Exception as exc:
            classified = classify_tool_exception(exc, tool_name=name)
            self._audit(
                settings=settings,
                tool_name=name,
                parameters=normalized,
                ctx=ctx,
                status="failure",
                duration_ms=_duration_ms(start),
                result=classified,
                exception=exc,
            )
            return materialize_tool_result(classified)

        # 4) 结果分类与幂等写入
        result_for_model = materialize_tool_result(raw_result)
        external_unknown = False
        success = True
        if isinstance(raw_result, ToolResult):
            success = raw_result.is_success
            external_unknown = raw_result.external_state_unknown
            # 状态未知的副作用：不写入可重放成功缓存之外的自动重试语义
            if raw_result.is_success and run_id and tool_call_id:
                self.idempotency.put(
                    str(run_id),
                    str(tool_call_id),
                    args_hash,
                    raw_result,
                    external_state_unknown=False,
                )
            elif (
                not raw_result.is_success
                and raw_result.retryable
                and entry is not None
                and not entry.may_have_external_side_effects
                and entry.risk == ToolRisk.LOW
            ):
                # 明确幂等的 transient 查询：不缓存失败，允许模型/上层重试
                pass
        else:
            if run_id and tool_call_id:
                self.idempotency.put(
                    str(run_id),
                    str(tool_call_id),
                    args_hash,
                    result_for_model,
                    external_state_unknown=False,
                )

        self._audit(
            settings=settings,
            tool_name=name,
            parameters=normalized,
            ctx=ctx,
            status="success" if success else "failure",
            duration_ms=_duration_ms(start),
            result=raw_result if isinstance(raw_result, ToolResult) else result_for_model,
        )

        if external_unknown:
            logger.warning(
                "tool_external_state_unknown",
                tool=name,
                run_id=run_id,
                tool_call_id=tool_call_id,
            )
        return result_for_model

    def _audit(
        self,
        *,
        settings: Any,
        tool_name: str,
        parameters: dict[str, Any],
        ctx: Any,
        status: str,
        duration_ms: int,
        result: Any = None,
        exception: BaseException | None = None,
    ) -> None:
        effective = settings or getattr(getattr(ctx, "deps", None), "settings", None)
        if not audit_enabled(effective):
            return
        record = build_audit_record(
            tool_name=tool_name,
            parameters=parameters,
            ctx=ctx,
            status=status,
            duration_ms=duration_ms,
            result=result,
            exception=exception,
        )
        # 补充策略版本
        entry = get_tool_entry(tool_name)
        record["policy_version"] = (
            entry.policy_version if entry is not None else self.policy.policy_version
        )
        record["tool_call_id"] = getattr(ctx, "tool_call_id", None)
        record["run_id"] = getattr(getattr(ctx, "deps", None), "run_id", None)
        path = getattr(effective, "runtime_harness_audit_path", "logs/runtime_harness_tools.jsonl")
        max_records = getattr(effective, "runtime_harness_audit_max_records", 5000)
        write_audit_record(record, path, max_records)


@dataclass
class HarnessCapability(AbstractCapability[Any]):
    """通过 get_wrapper_toolset 包装全部非输出工具。"""

    policy: PolicyEngine = field(default_factory=PolicyEngine)
    idempotency: IdempotencyStore | None = None

    def get_wrapper_toolset(self, toolset: AbstractToolset[Any]) -> AbstractToolset[Any]:
        store = self.idempotency if self.idempotency is not None else get_idempotency_store()
        return HarnessToolset(wrapped=toolset, policy=self.policy, idempotency=store)

    async def prepare_tools(
        self,
        ctx: RunContext[Any],
        tool_defs: list[ToolDefinition],
    ) -> list[ToolDefinition]:
        return [td for td in tool_defs if self.policy.is_tool_exposed(td.name)]


def build_harness_capability(settings: Any | None = None) -> HarnessCapability:
    return HarnessCapability(policy=PolicyEngine.from_settings(settings))


def _duration_ms(start: float) -> int:
    return max(0, round((time.perf_counter() - start) * 1000))
