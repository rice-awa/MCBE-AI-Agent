"""统一工具执行边界：策略、审批、幂等与审计入口。"""

from __future__ import annotations

import asyncio
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
    enqueue_audit_record,
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
DEFAULT_APPROVAL_COMMAND_ROOTS: frozenset[str] = frozenset(
    {
        "clear",
        "clone",
        "damage",
        "fill",
        "kill",
        "replaceitem",
        "setblock",
        "structure",
        "summon",
    }
)
DEFAULT_MAX_BATCH_COMMANDS = 10
DEFAULT_IDEMPOTENCY_TTL_SECONDS = 600.0
DEFAULT_IDEMPOTENCY_MAX_ENTRIES = 2048

_COMMAND_APPROVAL_TOOLS: frozenset[str] = frozenset(
    {"run_minecraft_command", "run_minecraft_commands"}
)

# 省略 target 时的已知工具默认目标（与 tools.py 签名默认值对齐）。
# 仅当默认明确是「当前玩家」时才可自动允许 MEDIUM；@a / 多目标默认必须审批。
# - "@s" / "self": 默认仅当前玩家
# - "@a" / "multi": 默认全服/多目标（不可视为 current player）
_TOOL_TARGET_DEFAULTS: dict[str, str] = {
    "find_entities": "@s",
    "get_inventory_snapshot": "@a",
    "get_player_snapshot": "@a",
    "get_look_block": "self",
}

# 无 target/broadcast 概念的 MEDIUM 工具中，可安全自动允许的极小集合（当前为空）。
# 未列入的（如 send_script_event）默认要求审批。
_MEDIUM_SAFE_AUTO_ALLOW_NO_TARGET: frozenset[str] = frozenset()

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
    approval_command_roots: frozenset[str] = field(
        default_factory=lambda: DEFAULT_APPROVAL_COMMAND_ROOTS
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

        approval_roots = set(DEFAULT_APPROVAL_COMMAND_ROOTS)
        configured_approval_roots = getattr(settings, "approval_command_roots", None) or []
        approval_roots.update(str(root).lower() for root in configured_approval_roots)

        allowlist = getattr(settings, "mcp_tool_allowlist", None) or []
        max_batch = int(getattr(settings, "max_batch_commands", DEFAULT_MAX_BATCH_COMMANDS) or DEFAULT_MAX_BATCH_COMMANDS)
        version = str(getattr(settings, "tool_policy_version", POLICY_VERSION) or POLICY_VERSION)
        return cls(
            hard_deny_tools=frozenset(hard_tools),
            hard_deny_command_roots=frozenset(hard_roots),
            approval_command_roots=frozenset(approval_roots),
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
        denied_root = self._find_command_root(normalized_args, self.hard_deny_command_roots)
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

        approval_root = None
        if tool_name in _COMMAND_APPROVAL_TOOLS:
            approval_root = self._find_command_root(normalized_args, self.approval_command_roots)
            if approval_root is not None:
                if approved:
                    return PolicyDecision(
                        action=PolicyDecisionKind.ALLOW,
                        reason=f"需审批命令根 '{approval_root}' 已获批准",
                        policy_version=self.policy_version,
                        metadata={"command_root": approval_root},
                    )
                return PolicyDecision(
                    action=PolicyDecisionKind.REQUIRE_APPROVAL,
                    reason=f"命令根 '{approval_root}' 需要玩家审批",
                    policy_version=self.policy_version,
                    metadata={"command_root": approval_root},
                )

            return PolicyDecision(
                action=PolicyDecisionKind.ALLOW,
                reason="命令根不在审批列表，自动允许",
                policy_version=self.policy_version,
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

    @staticmethod
    def _find_command_root(
        args: dict[str, Any],
        command_roots: frozenset[str],
    ) -> str | None:
        candidates: list[str] = []
        if "command" in args and isinstance(args["command"], str):
            candidates.append(args["command"])
        commands = args.get("commands")
        if isinstance(commands, list):
            candidates.extend(c for c in commands if isinstance(c, str))
        for command in candidates:
            root = extract_command_root(command)
            if root and root in command_roots:
                return root
        return None

    def _is_current_player_target(
        self,
        tool_name: str,
        args: dict[str, Any],
        player_name: str | None,
    ) -> bool:
        """判断 MEDIUM 工具是否仅作用于当前玩家（可自动允许）。

        规则（保守）：
        - broadcast=true → 非当前玩家
        - 显式 target 按选择器/玩家名判断
        - **省略 target 时绝不默认视为当前玩家**；仅当工具有已知安全默认
          （@s / self）或 send_* 通过 broadcast=false 默认当前玩家时才允许
        - 无 target/broadcast 概念的工具默认 False，除非在极小安全白名单中
        """
        if args.get("broadcast") is True:
            return False

        has_target_key = "target" in args
        target = args.get("target") if has_target_key else None

        # 显式传入 target（含空字符串）
        if has_target_key and target is not None:
            return self._target_selector_is_current_player(target, player_name)

        # target 键存在但值为 None：与省略同等对待（未知）
        # 省略 target：按工具已知默认值推断，未知则 require approval
        if tool_name in _TOOL_TARGET_DEFAULTS:
            default = _TOOL_TARGET_DEFAULTS[tool_name]
            if default in {"@s", "self"}:
                return True
            # 默认 @a / multi 等 → 非当前玩家
            return self._target_selector_is_current_player(default, player_name)

        # send_* 展示类（有 broadcast、无 target）：默认只发给触发玩家
        if tool_name in {
            "send_game_message",
            "send_colored_message",
            "send_title_message",
            "send_actionbar_message",
        }:
            # broadcast 已在上方处理 True；省略或 false 视为当前玩家
            return True

        # 无 target 概念的其它 MEDIUM 工具（send_script_event 等）
        if tool_name in _MEDIUM_SAFE_AUTO_ALLOW_NO_TARGET:
            return True
        return False

    @staticmethod
    def _target_selector_is_current_player(
        target: Any,
        player_name: str | None,
    ) -> bool:
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
        trace_context = getattr(deps, "trace_context", None)
        trace_recorder = getattr(deps, "trace_recorder", None)

        # tool.proposed at the boundary (fail-soft)
        self._trace_tool_proposed(
            trace_recorder,
            trace_context,
            tool_name=name,
            tool_call_id=str(tool_call_id) if tool_call_id else None,
            tool_args=normalized,
        )

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
                self._trace_tool_result(
                    trace_recorder,
                    trace_context,
                    tool_name=name,
                    tool_call_id=str(tool_call_id) if tool_call_id else None,
                    result=cached.result,
                    status="succeeded",
                    duration_ms=_duration_ms(start),
                    attributes={"idempotent_hit": True},
                )
                return materialize_tool_result(cached.result)

        # 2) 策略决策
        # 会话级自动批准（AGENT 同意 对话|永远）视为已批准；
        # pydantic-ai 恢复路径的 tool_call_approved 仍优先。
        session_auto = bool(getattr(deps, "auto_approve_tools", False))
        already_approved = bool(getattr(ctx, "tool_call_approved", False)) or session_auto
        decision = self.policy.decide(
            name,
            normalized,
            player_name=player_name,
            approved=already_approved,
        )
        self._trace_policy_decided(
            trace_recorder,
            trace_context,
            tool_name=name,
            tool_call_id=str(tool_call_id) if tool_call_id else None,
            decision=decision,
            already_approved=already_approved,
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
            self._trace_tool_result(
                trace_recorder,
                trace_context,
                tool_name=name,
                tool_call_id=str(tool_call_id) if tool_call_id else None,
                result=decision.reason,
                status="denied",
                duration_ms=_duration_ms(start),
                attributes={"policy_action": str(decision.action)},
            )
            return ToolDenied(message=decision.reason)

        if decision.action == PolicyDecisionKind.REQUIRE_APPROVAL:
            summary = summarize_args_for_player(name, normalized)
            # 审批挂起：记 not_executed，真正 approval.requested 由 Worker 写
            not_exec_attrs: dict[str, Any] = {
                "policy_action": str(decision.action),
            }
            if trace_recorder is not None and getattr(
                trace_recorder, "include_content", False
            ):
                not_exec_attrs["reason"] = decision.reason
            self._trace_tool_result(
                trace_recorder,
                trace_context,
                tool_name=name,
                tool_call_id=str(tool_call_id) if tool_call_id else None,
                result=None,
                status="not_executed",
                duration_ms=_duration_ms(start),
                attributes=not_exec_attrs,
            )
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
        self._trace_tool_started(
            trace_recorder,
            trace_context,
            tool_name=name,
            tool_call_id=str(tool_call_id) if tool_call_id else None,
        )
        try:
            raw_result = await super().call_tool(name, tool_args, ctx, tool)
        except ApprovalRequired:
            raise
        except asyncio.CancelledError:
            # CancelledError is BaseException in 3.11+; must not fall through
            # as a silent miss of tool.execution.cancelled.
            self._trace_tool_result(
                trace_recorder,
                trace_context,
                tool_name=name,
                tool_call_id=str(tool_call_id) if tool_call_id else None,
                result=None,
                status="cancelled",
                duration_ms=_duration_ms(start),
                attributes={"reason": "CancelledError"},
            )
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
            exec_status = "timeout_unknown" if (
                isinstance(classified, ToolResult) and classified.external_state_unknown
            ) else "failed"
            self._trace_tool_result(
                trace_recorder,
                trace_context,
                tool_name=name,
                tool_call_id=str(tool_call_id) if tool_call_id else None,
                result=classified,
                status=exec_status,
                duration_ms=_duration_ms(start),
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
            exec_status = "timeout_unknown"
        elif success:
            exec_status = "succeeded"
        else:
            exec_status = "failed"
        self._trace_tool_result(
            trace_recorder,
            trace_context,
            tool_name=name,
            tool_call_id=str(tool_call_id) if tool_call_id else None,
            result=raw_result if isinstance(raw_result, ToolResult) else result_for_model,
            status=exec_status,
            duration_ms=_duration_ms(start),
        )
        return result_for_model

    def _trace_tool_proposed(
        self,
        recorder: Any,
        context: Any,
        *,
        tool_name: str,
        tool_call_id: str | None,
        tool_args: dict[str, Any],
    ) -> None:
        if recorder is None or context is None:
            return
        try:
            recorder.record_tool_call(
                context,
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                tool_args=tool_args,
                event_name="tool.proposed",
                status="info",
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("trace_tool_proposed_failed", error=str(exc))

    def _trace_policy_decided(
        self,
        recorder: Any,
        context: Any,
        *,
        tool_name: str,
        tool_call_id: str | None,
        decision: Any,
        already_approved: bool,
    ) -> None:
        if recorder is None or context is None:
            return
        try:
            attrs: dict[str, Any] = {
                "tool_name": tool_name,
                "action": str(getattr(decision, "action", "")),
                "policy_version": getattr(decision, "policy_version", None),
                "already_approved": already_approved,
            }
            # Free-text reason only when content mode is enabled
            if getattr(recorder, "include_content", False):
                attrs["reason"] = getattr(decision, "reason", None)
            recorder.emit(
                "policy.decided",
                context,
                status="info",
                tool_call_id=tool_call_id,
                attributes=attrs,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("trace_policy_decided_failed", error=str(exc))

    def _trace_tool_started(
        self,
        recorder: Any,
        context: Any,
        *,
        tool_name: str,
        tool_call_id: str | None,
    ) -> None:
        if recorder is None or context is None:
            return
        try:
            recorder.emit(
                "tool.execution.started",
                context,
                status="started",
                tool_call_id=tool_call_id,
                attributes={"tool_name": tool_name},
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("trace_tool_started_failed", error=str(exc))

    def _trace_tool_result(
        self,
        recorder: Any,
        context: Any,
        *,
        tool_name: str,
        tool_call_id: str | None,
        result: Any,
        status: str,
        duration_ms: int | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        if recorder is None or context is None:
            return
        try:
            safe_result = result
            if isinstance(result, ToolResult):
                safe_result = {
                    "success": result.is_success,
                    "output": getattr(result, "output", None),
                    "error_kind": getattr(result, "error_kind", None),
                    "diagnostic_summary": getattr(result, "diagnostic_summary", None),
                    "external_state_unknown": getattr(
                        result, "external_state_unknown", False
                    ),
                }
            recorder.record_tool_result(
                context,
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                result=safe_result,
                status=status,
                duration_ms=duration_ms,
                attributes=attributes,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("trace_tool_result_failed", error=str(exc))

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
        entry = get_tool_entry(tool_name)
        record = build_audit_record(
            tool_name=tool_name,
            parameters=parameters,
            ctx=ctx,
            status=status,
            duration_ms=duration_ms,
            result=result,
            exception=exception,
            tool_call_id=getattr(ctx, "tool_call_id", None),
            policy_version=(
                entry.policy_version if entry is not None else self.policy.policy_version
            ),
            run_id=getattr(getattr(ctx, "deps", None), "run_id", None),
        )
        path = getattr(effective, "runtime_harness_audit_path", "logs/runtime_harness_tools.jsonl")
        max_records = getattr(effective, "runtime_harness_audit_max_records", 5000)
        enqueue_audit_record(record, path, max_records)


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
