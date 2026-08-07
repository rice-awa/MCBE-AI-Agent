"""统一工具执行边界：策略、审批、幂等与审计入口。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any, Literal

from pydantic import ValidationError
from pydantic_ai import ApprovalRequired, RunContext, ToolDenied
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.toolsets import AbstractToolset, ToolsetTool
from pydantic_ai.toolsets.wrapper import WrapperToolset

from config.logging import get_logger
from config.redaction import redact_exception, truncate_for_log
from services.agent.block_ops.preflight_cache import get_preflight_cache
from services.agent.harness.audit import (
    audit_enabled,
    build_audit_record,
    enqueue_audit_record,
)
from services.agent.harness.catalog import (
    POLICY_VERSION,
    ToolRisk,
    get_tool_entry,
    list_tool_names,
)
from services.agent.tool_results import ToolResult

logger = get_logger(__name__)

_BLOCK_OPS_TOOLS: frozenset[str] = frozenset({"inspect_block", "place_block", "fill_block"})

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
DEFAULT_MAX_BATCH_COMMANDS = 20
DEFAULT_IDEMPOTENCY_TTL_SECONDS = 600.0
DEFAULT_IDEMPOTENCY_MAX_ENTRIES = 2048

_COMMAND_APPROVAL_TOOLS: frozenset[str] = frozenset(
    {"run_minecraft_command", "run_minecraft_commands"}
)

_BLOCK_COMMAND_FALLBACK_TOOLS: frozenset[str] = frozenset(
    {"run_minecraft_command", "run_minecraft_commands", "run_world_command"}
)
_BLOCK_COMMAND_FALLBACK_ROOTS: frozenset[str] = frozenset({"setblock", "fill", "clone"})
DEFAULT_BLOCK_COMMAND_FALLBACK_TTL_SECONDS = 600.0
DEFAULT_BLOCK_COMMAND_FALLBACK_MAX_ENTRIES = 2048
_PROMPT_NEGATION_RE = re.compile(
    r"(?:不要|别(?:再|用|执行)?|禁止|不(?:要|用|执行)|无需|不要再)"
    r"|\b(?:do\s+not|don't|dont)\b",
    re.IGNORECASE,
)
_EXPLICIT_COMMAND_ACTION_RE = re.compile(
    r"(?:执行(?:\s*(?:这个|该|以下))?\s*命令|运行(?:\s*(?:这个|该|以下))?\s*命令|"
    r"原始命令|直接执行|请执行|(?:使用|用)(?:\s+\S+){0,12}\s+命令|"
    r"\b(?:execute|run|use)\s+(?:the\s+)?(?:raw\s+)?command\b)",
    re.IGNORECASE,
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


@dataclass(frozen=True)
class BlockCommandFallbackRecord:
    """最近一次方块工具（``place_block`` / ``fill_block`` / ``inspect_block``）的结构化回退结论。"""

    fallback_allowed: bool
    created_at: float
    code: str | None = None
    summary: str | None = None


class BlockCommandFallbackStore:
    """按连接、玩家和 run 隔离的有界 TTL 方块命令回退状态。"""

    def __init__(
        self,
        *,
        ttl_seconds: float = DEFAULT_BLOCK_COMMAND_FALLBACK_TTL_SECONDS,
        max_entries: int = DEFAULT_BLOCK_COMMAND_FALLBACK_MAX_ENTRIES,
    ) -> None:
        self._ttl = float(ttl_seconds)
        self._max_entries = max(1, int(max_entries))
        self._items: OrderedDict[tuple[str, str, str], BlockCommandFallbackRecord] = OrderedDict()
        self._lock = threading.RLock()

    @staticmethod
    def make_key(connection_id: str, player_name: str | None, run_id: str) -> tuple[str, str, str]:
        return (str(connection_id or ""), str(player_name or ""), str(run_id or ""))

    def get(
        self,
        connection_id: str,
        player_name: str | None,
        run_id: str,
    ) -> BlockCommandFallbackRecord | None:
        key = self.make_key(connection_id, player_name, run_id)
        with self._lock:
            self._purge_unlocked()
            record = self._items.get(key)
            if record is not None:
                self._items.move_to_end(key)
            return record

    def put(
        self,
        connection_id: str,
        player_name: str | None,
        run_id: str,
        *,
        fallback_allowed: bool,
        code: str | None = None,
        summary: str | None = None,
    ) -> None:
        key = self.make_key(connection_id, player_name, run_id)
        with self._lock:
            self._purge_unlocked()
            self._items[key] = BlockCommandFallbackRecord(
                fallback_allowed=bool(fallback_allowed),
                created_at=time.time(),
                code=code,
                summary=summary,
            )
            self._items.move_to_end(key)
            while len(self._items) > self._max_entries:
                self._items.popitem(last=False)

    def clear_run(self, connection_id: str, player_name: str | None, run_id: str) -> bool:
        key = self.make_key(connection_id, player_name, run_id)
        with self._lock:
            return self._items.pop(key, None) is not None

    def clear_connection(self, connection_id: str) -> int:
        cid = str(connection_id or "")
        with self._lock:
            self._purge_unlocked()
            keys = [key for key in self._items if key[0] == cid]
            for key in keys:
                del self._items[key]
            return len(keys)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    def _purge_unlocked(self, now: float | None = None) -> None:
        current = now if now is not None else time.time()
        expired = [key for key, record in self._items.items() if current - record.created_at >= self._ttl]
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

    def is_tool_exposed(self, tool_name: str, *, ctx: Any | None = None) -> bool:
        if tool_name in _BLOCK_OPS_TOOLS:
            return self._is_block_tool_exposed(tool_name, ctx)
        if tool_name in list_tool_names():
            return True
        return tool_name in self.mcp_tool_allowlist

    def _is_block_tool_exposed(self, tool_name: str, ctx: Any | None) -> bool:
        """Only expose dedicated block tools when connection capability is SUPPORTED."""
        if tool_name not in list_tool_names():
            return False
        if ctx is None:
            return False
        deps = getattr(ctx, "deps", None)
        if deps is None:
            return False
        connection_id = getattr(deps, "connection_id", None)
        if connection_id is None:
            return False
        from services.agent.block_ops.capability import (
            BlockCapabilityStatus,
            get_block_capability_cache,
        )

        record = get_block_capability_cache().get(str(connection_id))
        if record is None:
            # Lazy sync view: hide until async probe completes via prepare_tools/get_tools.
            return False
        return record.status == BlockCapabilityStatus.SUPPORTED

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
        return tool_name in _MEDIUM_SAFE_AUTO_ALLOW_NO_TARGET

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


def classify_tool_exception(
    exc: BaseException,
    *,
    tool_name: str,
    execution_stage: Literal["projection", "invocation"] | None = None,
) -> ToolResult:
    """Map tool-boundary exceptions without exposing implementation details."""
    text = str(exc) or exc.__class__.__name__
    diagnostic_summary = redact_exception(exc) or exc.__class__.__name__
    lower = text.lower()
    stage = execution_stage or (
        "projection" if "execution contract" in lower else "invocation"
    )
    if tool_name in _BLOCK_OPS_TOOLS and stage == "projection":
        from services.agent.block_ops.schema import (
            build_internal_error_response,
            dumps_payload,
        )

        return ToolResult.failure(
            dumps_payload(build_internal_error_response()),
            error_kind="INTERNAL",
            retryable=False,
            diagnostic_summary=diagnostic_summary,
            error_type=exc.__class__.__name__,
        )
    if "timeout" in lower or "deadline" in lower:
        entry = get_tool_entry(tool_name)
        side_effect = bool(entry.may_have_external_side_effects) if entry is not None else True
        return ToolResult.failure(
            f"工具执行超时: {tool_name}",
            error_kind="TRANSIENT",
            retryable=not side_effect and (entry is not None and entry.risk == ToolRisk.LOW),
            external_state_unknown=side_effect,
            diagnostic_summary=diagnostic_summary,
            error_type=exc.__class__.__name__,
        )
    return ToolResult.failure(
        f"工具执行失败: {tool_name}",
        error_kind="INTERNAL",
        retryable=False,
        diagnostic_summary=diagnostic_summary,
        error_type=exc.__class__.__name__,
    )


def log_tool_execution_failed(
    *,
    tool_name: str,
    ctx: Any,
    result: ToolResult,
    execution_stage: Literal["projection", "invocation"],
    error_type: str | None = None,
) -> None:
    """Emit a correlation-safe failure event for tool boundary failures."""
    deps = getattr(ctx, "deps", None)
    connection_id = str(getattr(deps, "connection_id", "") or "")
    logger.error(
        "tool_execution_failed",
        tool_name=tool_name,
        run_id=getattr(deps, "run_id", None) or getattr(ctx, "run_id", None) or "",
        tool_call_id=getattr(ctx, "tool_call_id", None) or "",
        connection_id_short=connection_id[-8:] if connection_id else "",
        player_name=getattr(deps, "player_name", None),
        error_kind=result.error_kind,
        error_type=result.error_type or error_type or type(result).__name__,
        diagnostic_summary=result.diagnostic_summary or "tool execution failed",
        external_state_unknown=result.external_state_unknown,
        execution_stage=execution_stage,
    )


def _projection_failure(ctx: Any, tool_name: str, exc: BaseException) -> ToolResult:
    """Classify and log an invalid block execution projection once."""
    classified = classify_tool_exception(
        exc, tool_name=tool_name, execution_stage="projection"
    )
    log_tool_execution_failed(
        tool_name=tool_name,
        ctx=ctx,
        result=classified,
        execution_stage="projection",
        error_type=exc.__class__.__name__,
    )
    return classified


def materialize_tool_result(result: Any) -> Any:
    """将 ToolResult 转为模型可消费的文本，其它类型原样返回。"""
    if isinstance(result, ToolResult):
        return str(result)
    return result


def _block_result_observability_status(result: ToolResult) -> tuple[bool, bool]:
    """Classify a cached block group result for audit and tracing.

    A grouped edit with an execution failure remains a successful ``ToolResult``
    so the harness can cache it and prevent duplicate side effects. Its JSON
    body is nevertheless an operation failure and must not be audited or traced
    as a successful edit group.
    """
    success = result.is_success
    unknown = result.external_state_unknown
    try:
        body = json.loads(result.output)
    except (TypeError, ValueError):
        return success, unknown
    if not isinstance(body, dict) or body.get("ok") is not False:
        return success, unknown
    return False, unknown or body.get("status") == "unknown"


_GLOBAL_IDEMPOTENCY = IdempotencyStore()
_GLOBAL_BLOCK_COMMAND_FALLBACK = BlockCommandFallbackStore()


def get_idempotency_store() -> IdempotencyStore:
    return _GLOBAL_IDEMPOTENCY


def reset_idempotency_store() -> None:
    _GLOBAL_IDEMPOTENCY.clear()


def get_block_command_fallback_store() -> BlockCommandFallbackStore:
    return _GLOBAL_BLOCK_COMMAND_FALLBACK


def reset_block_command_fallback_store() -> None:
    _GLOBAL_BLOCK_COMMAND_FALLBACK.clear()


def clear_block_command_fallback_for_connection(connection_id: str) -> int:
    """清理断开连接遗留的方块命令回退状态。"""
    return _GLOBAL_BLOCK_COMMAND_FALLBACK.clear_connection(connection_id)


def _safe_block_error_summary(body: dict[str, Any]) -> str | None:
    """Return a bounded, already-sanitized diagnostic/message summary."""
    for key in ("diagnostic", "message"):
        value = body.get(key)
        if isinstance(value, str) and value.strip():
            return truncate_for_log(value.strip(), 160)
    return None


def _structured_block_edit_outcome(
    result: Any,
) -> tuple[bool, bool, str | None, str | None] | None:
    """Return ``(failed, fallback_allowed, code, summary)`` for structured edit results.

    Registered production tools stringify ``ToolResult`` before this wrapper can
    observe it, while direct test/toolset use can still return ``ToolResult``.
    Treat both representations identically and fail closed for an unstructured
    failed ``ToolResult``.
    """
    tool_result = result if isinstance(result, ToolResult) else None
    payload = tool_result.output if tool_result is not None else result
    if isinstance(payload, str):
        try:
            body = json.loads(payload)
        except (TypeError, ValueError):
            body = None
        if isinstance(body, dict) and isinstance(body.get("ok"), bool):
            if body["ok"]:
                return (False, False, None, None)
            code = body.get("code")
            return (
                True,
                bool(body.get("fallback_allowed", False)),
                str(code) if code else None,
                _safe_block_error_summary(body),
            )
    if tool_result is not None:
        return (not tool_result.is_success, False, None, None)
    return None


def _record_block_edit_fallback_outcome(
    result: Any,
    *,
    connection_id: str,
    player_name: str | None,
    run_id: str,
    store: BlockCommandFallbackStore | None = None,
) -> None:
    outcome = _structured_block_edit_outcome(result)
    if outcome is None:
        return
    failed, fallback_allowed, code, summary = outcome
    target = store if store is not None else get_block_command_fallback_store()
    if failed:
        target.put(
            connection_id,
            player_name,
            run_id,
            fallback_allowed=fallback_allowed,
            code=code,
            summary=summary,
        )
    else:
        target.clear_run(connection_id, player_name, run_id)


def _command_may_be_block_fallback(command: Any) -> bool:
    """Identify direct and ``execute … run`` block mutations.

    Opaque ``function`` and ``schedule`` commands are not raw block commands;
    their existing command-tool risk policy remains responsible for them.
    """
    if not isinstance(command, str):
        return False
    text = command.strip()
    if text.startswith("/"):
        text = text[1:].lstrip()
    if not text:
        return False
    root = extract_command_root(text)
    if root in _BLOCK_COMMAND_FALLBACK_ROOTS:
        return True
    if root != "execute":
        return False
    nested = re.search(r"\brun\s+(.+)$", text, flags=re.IGNORECASE)
    return bool(nested and _command_may_be_block_fallback(nested.group(1)))


def _fallback_commands(tool_name: str, normalized_args: dict[str, Any]) -> list[str]:
    if tool_name in {"run_minecraft_command", "run_world_command"}:
        command = normalized_args.get("command")
        return [command] if isinstance(command, str) and _command_may_be_block_fallback(command) else []
    if tool_name == "run_minecraft_commands":
        commands = normalized_args.get("commands")
        if not isinstance(commands, list):
            return []
        return [command for command in commands if _command_may_be_block_fallback(command)]
    return []


def _is_explicit_raw_command_prompt(ctx: RunContext[Any], commands: list[str]) -> bool:
    """Allow only an exact command quoted in the current, non-negative prompt."""
    prompt = getattr(ctx, "prompt", None)
    if not isinstance(prompt, str) or not prompt:
        return False
    normalized_prompt = " ".join(prompt.casefold().split())
    if _PROMPT_NEGATION_RE.search(normalized_prompt):
        return False
    has_action = (
        normalized_prompt.startswith("/")
        or bool(_EXPLICIT_COMMAND_ACTION_RE.search(normalized_prompt))
        or bool(re.search(r"(?:执行|运行)(?:\s*(?:这个|该|以下))?\s*/", normalized_prompt))
    )
    if not has_action:
        return False
    normalized_commands = [
        " ".join(command.lstrip("/").casefold().split()) for command in commands
    ]
    return bool(normalized_commands) and all(
        command and command in normalized_prompt for command in normalized_commands
    )


def _block_command_fallback_denial(
    *,
    tool_name: str,
    normalized_args: dict[str, Any],
    ctx: RunContext[Any],
    connection_id: str,
    player_name: str | None,
    run_id: str,
    store: BlockCommandFallbackStore | None = None,
) -> PolicyDecision | None:
    """Return a denial for an automatic raw-command fallback, if applicable."""
    if tool_name not in _BLOCK_COMMAND_FALLBACK_TOOLS or getattr(ctx, "tool_call_approved", False):
        return None
    commands = _fallback_commands(tool_name, normalized_args)
    if not commands or _is_explicit_raw_command_prompt(ctx, commands):
        return None

    from services.agent.block_ops.capability import (
        BlockCapabilityStatus,
        get_block_capability_cache,
    )

    capability = get_block_capability_cache().get(connection_id)
    if capability is None or capability.status != BlockCapabilityStatus.SUPPORTED:
        return None
    target = store if store is not None else get_block_command_fallback_store()
    record = target.get(connection_id, player_name, run_id)
    if record is None or record.fallback_allowed:
        return None
    code_text = f"（{record.code}）" if record.code else ""
    if record.summary:
        reason = (
            f"专用方块编辑刚刚失败{code_text}且不允许命令回退；"
            f"诊断: {record.summary}。"
            "请根据结构化错误修正 place_block / fill_block 参数，"
            "或等待明确允许回退的结果。"
        )
    else:
        reason = (
            f"专用方块编辑刚刚失败{code_text}且不允许命令回退；"
            "请根据结构化错误修正 place_block / fill_block 参数，"
            "或等待明确允许回退的结果。"
        )
    return PolicyDecision(
        action=PolicyDecisionKind.DENY,
        reason=reason,
        metadata={"fallback_code": record.code, "command_count": len(commands)},
    )


@dataclass
class HarnessToolset(WrapperToolset[Any]):
    """统一策略/审批/幂等/审计包装层。"""

    policy: PolicyEngine = field(default_factory=PolicyEngine)
    idempotency: IdempotencyStore = field(default_factory=get_idempotency_store)
    fallback_store: BlockCommandFallbackStore = field(
        default_factory=get_block_command_fallback_store
    )

    async def get_tools(self, ctx: RunContext[Any]) -> dict[str, ToolsetTool[Any]]:
        await self._ensure_block_capability(ctx)
        tools = await self.wrapped.get_tools(ctx)
        return {
            name: tool
            for name, tool in tools.items()
            if self.policy.is_tool_exposed(name, ctx=ctx)
        }

    async def call_tool(
        self,
        name: str,
        tool_args: dict[str, Any],
        ctx: RunContext[Any],
        tool: ToolsetTool[Any],
    ) -> Any:
        start = time.perf_counter()
        effective_args = dict(tool_args or {})
        original_normalized = normalize_tool_args(effective_args)
        original_args_hash = hash_normalized_args(original_normalized)
        deps = getattr(ctx, "deps", None)
        player_name = getattr(deps, "player_name", None)
        run_id = getattr(deps, "run_id", None) or getattr(ctx, "run_id", None) or ""
        tool_call_id = getattr(ctx, "tool_call_id", None) or ""
        settings = getattr(deps, "settings", None)
        entry = get_tool_entry(name)
        trace_context = getattr(deps, "trace_context", None)
        trace_recorder = getattr(deps, "trace_recorder", None)
        connection_id = str(getattr(deps, "connection_id", "") or "")

        # tool.proposed at the boundary (fail-soft)
        self._trace_tool_proposed(
            trace_recorder,
            trace_context,
            tool_name=name,
            tool_call_id=str(tool_call_id) if tool_call_id else None,
            tool_args=original_normalized,
        )

        # 0.1) Approved plan_id resume: block ops recovery without hidden kwargs.
        # 恢复负载只有 {plan_id}；执行走 execute_block_plan（frozen canonical
        # args），不再 super().call_tool 注入 status/phase/locked_targets。
        # 仅 tool_call_approved 时生效：新鲜调用即使伪造 plan_id 也不可复用。
        plan_id = effective_args.get("plan_id")
        if (
            name in _BLOCK_OPS_TOOLS
            and bool(getattr(ctx, "tool_call_approved", False))
            and isinstance(plan_id, str)
            and plan_id.strip()
        ):
            return await self._resume_approved_block_plan(
                name=name,
                plan_id=plan_id.strip(),
                ctx=ctx,
                settings=settings,
                player_name=player_name,
                run_id=str(run_id),
                tool_call_id=str(tool_call_id),
                connection_id=connection_id,
                trace_recorder=trace_recorder,
                trace_context=trace_context,
                start=start,
            )

        # 0) Block tools: preflight / relative resolution BEFORE policy & approval.
        # Preflight plans separate authorization, execution projection, and evidence.
        preflight_plan = None
        if name in _BLOCK_OPS_TOOLS:
            preflight_failure, preflight_plan = await self._preflight_block_tool(
                name=name,
                tool_args=effective_args,
                original_args_hash=original_args_hash,
                ctx=ctx,
                run_id=str(run_id),
                tool_call_id=str(tool_call_id),
                connection_id=connection_id,
            )
            if preflight_failure is not None:
                if name in _BLOCK_OPS_TOOLS:
                    _record_block_edit_fallback_outcome(
                        preflight_failure,
                        connection_id=connection_id,
                        player_name=player_name,
                        run_id=str(run_id),
                        store=self.fallback_store,
                    )
                self._audit(
                    settings=settings,
                    tool_name=name,
                    parameters=original_normalized,
                    ctx=ctx,
                    status="failure",
                    duration_ms=_duration_ms(start),
                    result=preflight_failure,
                )
                return materialize_tool_result(preflight_failure)
            if preflight_plan is not None:
                effective_args = preflight_plan.authorized_args

        normalized = normalize_tool_args(effective_args)
        args_hash = hash_normalized_args(normalized)
        execute_args = (
            dict(preflight_plan.execute_args) if preflight_plan is not None else None
        )
        idempotency_args_hash = (
            hash_normalized_args(normalize_tool_args(execute_args))
            if name in _BLOCK_OPS_TOOLS and execute_args is not None
            else args_hash
        )

        # 回退限制必须早于幂等缓存：同一 call-id 的旧命令结果不能绕过
        # 当前任务中新出现的 ``fallback_allowed=false`` 方块编辑失败。
        fallback_denial = _block_command_fallback_denial(
            tool_name=name,
            normalized_args=normalized,
            ctx=ctx,
            connection_id=connection_id,
            player_name=player_name,
            run_id=str(run_id),
            store=self.fallback_store,
        )
        if fallback_denial is not None:
            denied = ToolResult.failure(
                fallback_denial.reason,
                error_kind="DENIED",
                retryable=False,
                diagnostic_summary=fallback_denial.reason,
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
            self._trace_policy_decided(
                trace_recorder,
                trace_context,
                tool_name=name,
                tool_call_id=str(tool_call_id) if tool_call_id else None,
                decision=fallback_denial,
                already_approved=False,
            )
            self._trace_tool_result(
                trace_recorder,
                trace_context,
                tool_name=name,
                tool_call_id=str(tool_call_id) if tool_call_id else None,
                result=fallback_denial.reason,
                status="denied",
                duration_ms=_duration_ms(start),
                attributes={"policy_action": str(fallback_denial.action)},
            )
            return ToolDenied(message=fallback_denial.reason)

        # 1) 方块工具只按实际执行投影幂等；非方块工具保持原参数哈希兼容。
        if run_id and tool_call_id:
            cached = self.idempotency.get(
                str(run_id), str(tool_call_id), idempotency_args_hash
            )
            if name not in _BLOCK_OPS_TOOLS:
                for legacy_hash in (args_hash, original_args_hash):
                    if cached is not None or legacy_hash == idempotency_args_hash:
                        continue
                    cached = self.idempotency.get(
                        str(run_id), str(tool_call_id), legacy_hash
                    )
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
            try:
                execute_args = execute_args or _python_tool_args(name, effective_args)
            except (TypeError, ValueError, KeyError) as exc:
                classified = classify_tool_exception(
                    exc, tool_name=name, execution_stage="projection"
                )
                log_tool_execution_failed(
                    tool_name=name,
                    ctx=ctx,
                    result=classified,
                    execution_stage="projection",
                    error_type=exc.__class__.__name__,
                )
                self._audit(
                    settings=settings, tool_name=name, parameters=normalized, ctx=ctx,
                    status="failure", duration_ms=_duration_ms(start), result=classified,
                )
                return materialize_tool_result(classified)
            summary = summarize_args_for_player(name, normalized)
            self._audit(
                settings=settings,
                tool_name=name,
                parameters=normalized,
                authorized_args=normalized,
                approval_evidence=(
                    preflight_plan.approval_metadata if preflight_plan is not None else {}
                ),
                ctx=ctx,
                status="approval_required",
                duration_ms=_duration_ms(start),
            )
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
                    "execute_args": (
                        preflight_plan.execute_args if preflight_plan is not None else execute_args
                    ),
                    "execution_args_hash": idempotency_args_hash,
                    "approval_metadata": (
                        preflight_plan.approval_metadata if preflight_plan is not None else {}
                    ),
                    "original_args_hash": original_args_hash,
                    "plan_id": preflight_plan.plan_id if preflight_plan is not None else "",
                    "args_summary": summary,
                    "policy_version": decision.policy_version,
                    "reason": decision.reason,
                    "risk": str(entry.risk) if entry is not None else "unknown",
                    "player_name": player_name,
                    "run_id": run_id,
                    "conversation_id": getattr(deps, "conversation_id", None),
                }
            )

        # 3) 执行（使用 canonical args；映射 fill 的 from/to → from_pos/to_pos）
        self._trace_tool_started(
            trace_recorder,
            trace_context,
            tool_name=name,
            tool_call_id=str(tool_call_id) if tool_call_id else None,
        )
        try:
            execute_args = execute_args or _python_tool_args(name, effective_args)
        except (TypeError, ValueError, KeyError) as exc:
            classified = classify_tool_exception(
                exc, tool_name=name, execution_stage="projection"
            )
            log_tool_execution_failed(
                tool_name=name,
                ctx=ctx,
                result=classified,
                execution_stage="projection",
                error_type=exc.__class__.__name__,
            )
            self._audit(
                settings=settings, tool_name=name, parameters=normalized, ctx=ctx,
                status="failure", duration_ms=_duration_ms(start), result=classified,
            )
            return materialize_tool_result(classified)
        try:
            raw_result = await super().call_tool(name, execute_args, ctx, tool)
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
            classified = classify_tool_exception(
                exc, tool_name=name, execution_stage="invocation"
            )
            if name in _BLOCK_OPS_TOOLS:
                _record_block_edit_fallback_outcome(
                    classified,
                    connection_id=connection_id,
                    player_name=player_name,
                    run_id=str(run_id),
                    store=self.fallback_store,
                )
            log_tool_execution_failed(
                tool_name=name,
                ctx=ctx,
                result=classified,
                execution_stage="invocation",
                error_type=exc.__class__.__name__,
            )
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

        # 4) 统一收尾：幂等写入 → 审计 → 追踪（与审批恢复路径共享同一实现）
        if name in _BLOCK_OPS_TOOLS:
            _record_block_edit_fallback_outcome(
                raw_result,
                connection_id=connection_id,
                player_name=player_name,
                run_id=str(run_id),
                store=self.fallback_store,
            )
        return self._finish_tool_execution(
            name=name,
            raw_result=raw_result,
            ctx=ctx,
            settings=settings,
            run_id=str(run_id),
            tool_call_id=str(tool_call_id),
            connection_id=connection_id,
            player_name=player_name,
            trace_recorder=trace_recorder,
            trace_context=trace_context,
            normalized=normalized,
            idempotency_args_hash=idempotency_args_hash,
            entry=entry,
            start=start,
        )

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

    def _finish_tool_execution(
        self,
        *,
        name: str,
        raw_result: Any,
        ctx: RunContext[Any],
        settings: Any,
        run_id: str,
        tool_call_id: str,
        connection_id: str,
        player_name: str | None,
        trace_recorder: Any,
        trace_context: Any,
        normalized: dict[str, Any],
        idempotency_args_hash: str,
        entry: Any | None,
        start: float,
        authorized_args: dict[str, Any] | None = None,
    ) -> Any:
        """统一收尾链：结果分类 → 幂等写入 → 审计 → 追踪 → 返回。

        普通执行与审批恢复共享此方法，确保两条路径的收尾行为一致。
        """
        result_for_model = materialize_tool_result(raw_result)
        external_unknown = False
        success = True
        if isinstance(raw_result, ToolResult):
            if name in _BLOCK_OPS_TOOLS:
                success, external_unknown = _block_result_observability_status(raw_result)
            else:
                success = raw_result.is_success
                external_unknown = raw_result.external_state_unknown
            if not raw_result.is_success and name in _BLOCK_OPS_TOOLS:
                log_tool_execution_failed(
                    tool_name=name,
                    ctx=ctx,
                    result=raw_result,
                    execution_stage="invocation",
                    error_type=raw_result.error_type,
                )
            # 状态未知的副作用：不写入可重放成功缓存之外的自动重试语义
            if raw_result.is_success and run_id and tool_call_id:
                self.idempotency.put(
                    str(run_id),
                    str(tool_call_id),
                    idempotency_args_hash,
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
                pass
        else:
            if run_id and tool_call_id:
                self.idempotency.put(
                    str(run_id),
                    str(tool_call_id),
                    idempotency_args_hash,
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
            authorized_args=authorized_args,
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

    async def _ensure_block_capability(self, ctx: RunContext[Any]) -> None:
        deps = getattr(ctx, "deps", None)
        if deps is None:
            return
        connection_id = getattr(deps, "connection_id", None)
        if connection_id is None:
            return
        from services.agent.block_ops.capability import ensure_block_capability

        await ensure_block_capability(
            str(connection_id),
            getattr(deps, "addon_bridge", None),
        )

    async def _preflight_block_tool(
        self,
        *,
        name: str,
        tool_args: dict[str, Any],
        original_args_hash: str,
        ctx: RunContext[Any],
        run_id: str,
        tool_call_id: str,
        connection_id: str,
    ) -> tuple[ToolResult | None, Any | None]:
        """Run block-ops preflight; return (failure, separated plan)."""
        from services.agent.block_ops.preflight import BlockPreflightPlan, run_block_preflight

        # Reuse cached canonical args on approval recovery (same original hash).
        cache = get_preflight_cache()
        if run_id and tool_call_id:
            cached = cache.get(run_id, tool_call_id, original_args_hash)
            if cached is not None:
                return None, BlockPreflightPlan(
                    dict(cached.canonical_args),
                    dict(cached.execute_args),
                    dict(cached.approval_metadata),
                    plan_id=cached.plan_id,
                )

        try:
            plan_or_args, failure = await run_block_preflight(ctx, name, tool_args)
        except Exception as exc:
            classified = classify_tool_exception(
                exc, tool_name=name, execution_stage="projection"
            )
            log_tool_execution_failed(
                tool_name=name,
                ctx=ctx,
                result=classified,
                execution_stage="projection",
                error_type=exc.__class__.__name__,
            )
            return classified, None

        if failure is not None:
            return failure, None

        if plan_or_args is None:
            try:
                execute_args = _python_tool_args(name, tool_args)
            except (TypeError, ValueError, KeyError) as exc:
                return _projection_failure(ctx, name, exc), None
            return None, BlockPreflightPlan(dict(tool_args), execute_args, {})

        if isinstance(plan_or_args, BlockPreflightPlan):
            plan = plan_or_args
        else:
            canonical = dict(plan_or_args)
            try:
                execute_args = _python_tool_args(name, canonical)
            except (TypeError, ValueError, KeyError) as exc:
                return _projection_failure(ctx, name, exc), None
            plan = BlockPreflightPlan(canonical, execute_args, {})

        if run_id and tool_call_id:
            entry = cache.put(
                run_id=run_id,
                tool_call_id=tool_call_id,
                original_args_hash=original_args_hash,
                canonical_args=plan.authorized_args,
                execute_args=plan.execute_args,
                approval_metadata=plan.approval_metadata,
                preflight_payload=plan.approval_metadata,
                connection_id=connection_id or None,
                tool_name=name,
                plan_id=plan.plan_id,
            )
            if not plan.plan_id:
                # plan 未自带 plan_id（dict/None 直通分支）：用缓存生成的 plan_id 回填
                plan = replace(plan, plan_id=entry.plan_id)
        return None, plan

    async def _resume_approved_block_plan(
        self,
        *,
        name: str,
        plan_id: str,
        ctx: RunContext[Any],
        settings: Any,
        player_name: str | None,
        run_id: str,
        tool_call_id: str,
        connection_id: str,
        trace_recorder: Any,
        trace_context: Any,
        start: float,
    ) -> Any:
        """Approved plan_id 恢复：幂等 → execute_block_plan → 统一收尾。

        与主路径共享同一套收尾链（幂等写入 → 审计 → 追踪），审计参数
        从缓存 canonical args 还原，绝不包含隐藏 kwargs。
        """
        from services.agent.block_ops import execute_block_plan
        from services.agent.block_ops.preflight import state_unknown_result

        entry = get_preflight_cache().get_by_plan_id(plan_id)
        if entry is None:
            result = state_unknown_result(plan_id)
            self._audit(
                settings=settings,
                tool_name=name,
                parameters={"plan_id": plan_id},
                ctx=ctx,
                status="failure",
                duration_ms=_duration_ms(start),
                result=result,
            )
            self._trace_tool_result(
                trace_recorder,
                trace_context,
                tool_name=name,
                tool_call_id=tool_call_id or None,
                result=result,
                status="timeout_unknown",
                duration_ms=_duration_ms(start),
            )
            return materialize_tool_result(result)
        if entry.tool_name and entry.tool_name != name:
            result = _state_unknown_result(plan_id, reason="tool-mismatch")
            self._audit(
                settings=settings,
                tool_name=name,
                parameters={"plan_id": plan_id},
                ctx=ctx,
                status="failure",
                duration_ms=_duration_ms(start),
                result=result,
            )
            self._trace_tool_result(
                trace_recorder,
                trace_context,
                tool_name=name,
                tool_call_id=tool_call_id or None,
                result=result,
                status="timeout_unknown",
                duration_ms=_duration_ms(start),
            )
            return materialize_tool_result(result)

        canonical = dict(entry.canonical_args)
        idempotency_args_hash = hash_normalized_args(normalize_tool_args(canonical))

        # 幂等（同主路径）：同 run+call 同参数只执行一次
        if run_id and tool_call_id:
            cached = self.idempotency.get(
                str(run_id), str(tool_call_id), idempotency_args_hash
            )
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
                    parameters=canonical,
                    ctx=ctx,
                    status="success",
                    duration_ms=_duration_ms(start),
                    result=cached.result,
                    authorized_args=canonical,
                )
                self._trace_tool_result(
                    trace_recorder,
                    trace_context,
                    tool_name=name,
                    tool_call_id=tool_call_id or None,
                    result=cached.result,
                    status="succeeded",
                    duration_ms=_duration_ms(start),
                    attributes={"idempotent_hit": True},
                )
                return materialize_tool_result(cached.result)

        self._trace_tool_started(
            trace_recorder,
            trace_context,
            tool_name=name,
            tool_call_id=tool_call_id or None,
        )
        raw_result = await execute_block_plan(plan_id, ctx)

        # 统一收尾：幂等写入 → 审计 → 追踪（与主路径共享同一实现）
        return self._finish_tool_execution(
            name=name,
            raw_result=raw_result,
            ctx=ctx,
            settings=settings,
            run_id=str(run_id),
            tool_call_id=str(tool_call_id),
            connection_id=connection_id,
            player_name=player_name,
            trace_recorder=trace_recorder,
            trace_context=trace_context,
            normalized=canonical,
            idempotency_args_hash=idempotency_args_hash,
            entry=get_tool_entry(name),
            start=start,
            authorized_args=canonical,
        )

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
        authorized_args: dict[str, Any] | None = None,
        approval_evidence: dict[str, Any] | None = None,
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
            authorized_args=authorized_args,
            approval_evidence=approval_evidence,
        )
        path = getattr(effective, "runtime_harness_audit_path", "logs/runtime_harness_tools.jsonl")
        max_records = getattr(effective, "runtime_harness_audit_max_records", 5000)
        enqueue_audit_record(record, path, max_records)


@dataclass
class HarnessCapability(AbstractCapability[Any]):
    """通过 get_wrapper_toolset 包装全部非输出工具。"""

    policy: PolicyEngine = field(default_factory=PolicyEngine)
    idempotency: IdempotencyStore | None = None
    fallback_store: BlockCommandFallbackStore | None = None

    def get_wrapper_toolset(self, toolset: AbstractToolset[Any]) -> AbstractToolset[Any]:
        store = self.idempotency if self.idempotency is not None else get_idempotency_store()
        fallback_store = (
            self.fallback_store
            if self.fallback_store is not None
            else get_block_command_fallback_store()
        )
        return HarnessToolset(
            wrapped=toolset,
            policy=self.policy,
            idempotency=store,
            fallback_store=fallback_store,
        )

    async def wrap_tool_validate(
        self,
        ctx: RunContext[Any],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: Any,
        handler: Any,
    ) -> Any:
        """Approved block-ops recovery: rewrite {plan_id} → frozen canonical args.

        plan_id 恢复负载只含 {plan_id}，公共参数校验必须在 frozen canonical
        args 上运行（缺失/过期 → 跳过校验，由 call_tool 的 plan_id 分支返回
        STATE_UNKNOWN）。隐藏字段（status/phase/locked_targets）结构性不可达：
        校验与执行都只接触 plan_id / canonical args。

        恢复载荷不能直接交给 ``handler``：公共参数 schema 不含 ``plan_id``
        （``additionalProperties: false``），``{"plan_id": X}`` 本身就无法通过
        schema 校验。因此这里对原始载荷做结构性校验：恢复契约只允许恰好
        ``{"plan_id": <非空字符串>}``，任何额外键都是畸形载荷，必须在校验
        边界拒绝，而不是静默归一化放行（例如
        ``{"plan_id": "missing", "malicious_extra": true}``）。
        """
        plan_id = args.get("plan_id") if isinstance(args, dict) else None
        if (
            tool_def.name in _BLOCK_OPS_TOOLS
            and bool(getattr(ctx, "tool_call_approved", False))
            and isinstance(plan_id, str)
            and plan_id.strip()
        ):
            if set(args.keys()) != {"plan_id"}:
                raise ValidationError.from_exception_data(
                    tool_def.name,
                    [
                        {
                            "type": "extra_forbidden",
                            "loc": (str(key),),
                            "msg": "plan_id 恢复载荷只能包含 plan_id 字段",
                            "input": args[key],
                        }
                        for key in sorted(set(args.keys()) - {"plan_id"})
                    ],
                )
            entry = get_preflight_cache().get_by_plan_id(plan_id.strip())
            if entry is not None and entry.tool_name == tool_def.name:
                # 校验 frozen canonical args；校验结果丢弃，只回传 plan_id
                await handler(dict(entry.canonical_args))
            return {"plan_id": plan_id.strip()}
        return await handler(args)

    async def prepare_tools(
        self,
        ctx: RunContext[Any],
        tool_defs: list[ToolDefinition],
    ) -> list[ToolDefinition]:
        # Probe capability once so block tools can be filtered correctly.
        deps = getattr(ctx, "deps", None)
        if deps is not None:
            connection_id = getattr(deps, "connection_id", None)
            if connection_id is not None:
                from services.agent.block_ops.capability import ensure_block_capability

                await ensure_block_capability(
                    str(connection_id),
                    getattr(deps, "addon_bridge", None),
                )
        exposed = [td for td in tool_defs if self.policy.is_tool_exposed(td.name, ctx=ctx)]
        # The single-op block tools already expose only model-visible fields in
        # their public schemas; no internal-key stripping is needed here.
        return exposed


def build_harness_capability(settings: Any | None = None) -> HarnessCapability:
    return HarnessCapability(policy=PolicyEngine.from_settings(settings))


def _duration_ms(start: float) -> int:
    return max(0, round((time.perf_counter() - start) * 1000))


def _python_tool_args(tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Build Python call args for the public block tool signatures.

    Canonical fill args use the model-visible alias ``from`` for approval,
    audit, and plan storage. The registered Python function instead accepts
    ``from_`` because ``from`` is a reserved keyword, so restore the Python
    parameter name only at this final invocation boundary.
    """
    projected = dict(args or {})
    if tool_name == "fill_block" and "from" in projected and "from_" not in projected:
        projected["from_"] = projected.pop("from")
    return projected
