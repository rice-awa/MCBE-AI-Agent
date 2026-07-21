"""结构化 Agent 工具返回值与命令执行结果。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ErrorKind = Literal[
    "INVALID_ARGUMENT",
    "MODEL_RETRYABLE",
    "TRANSIENT",
    "DENIED",
    "PERMANENT",
    "CANCELLED",
    "INTERNAL",
]

ToolStatus = Literal["success", "failure"]

CommandStatus = Literal[
    "success",
    "failed",
    "connection_unavailable",
    "timeout_unknown",
]


# 玩家可见的稳定错误类别文案（不包含堆栈/异常类型/原始响应）
PLAYER_ERROR_ADVICE: dict[ErrorKind, str] = {
    "INVALID_ARGUMENT": "参数无效，请检查输入后重试。",
    "MODEL_RETRYABLE": "本次操作可重试，请换一种说法或稍后再试。",
    "TRANSIENT": "服务暂时不可用，请稍后重试。",
    "DENIED": "该操作被拒绝，请确认权限或改用其他命令。",
    "PERMANENT": "操作失败，请检查参数或当前游戏状态。",
    "CANCELLED": "操作已取消。",
    "INTERNAL": "内部错误，请稍后重试；若持续出现请联系管理员。",
}


@dataclass(frozen=True)
class CommandResult:
    """`run_command` 的结构化返回，区分成功/明确失败/断线/超时未知。"""

    status: CommandStatus
    output: str = ""
    diagnostic_summary: str | None = None
    external_state_unknown: bool = False

    @classmethod
    def ok(cls, output: str = "") -> CommandResult:
        return cls(status="success", output=output or "命令执行成功")

    @classmethod
    def failed(cls, output: str, *, diagnostic_summary: str | None = None) -> CommandResult:
        return cls(
            status="failed",
            output=output,
            diagnostic_summary=diagnostic_summary or output,
        )

    @classmethod
    def connection_unavailable(
        cls,
        output: str = "连接不可用",
        *,
        diagnostic_summary: str | None = None,
    ) -> CommandResult:
        return cls(
            status="connection_unavailable",
            output=output,
            diagnostic_summary=diagnostic_summary or output,
        )

    @classmethod
    def timeout_unknown(
        cls,
        output: str = "命令执行超时，外部状态未知",
        *,
        diagnostic_summary: str | None = None,
    ) -> CommandResult:
        return cls(
            status="timeout_unknown",
            output=output,
            diagnostic_summary=diagnostic_summary or output,
            external_state_unknown=True,
        )

    @property
    def is_success(self) -> bool:
        return self.status == "success"


@dataclass(frozen=True)
class ToolResult:
    """统一工具执行结果。

    - `output`：返回给模型的文本
    - `diagnostic_summary`：仅内部诊断/审计用，不面向玩家
    """

    output: str
    status: ToolStatus = "success"
    error_kind: ErrorKind | None = None
    retryable: bool = False
    external_state_unknown: bool = False
    diagnostic_summary: str | None = None
    # 兼容旧字段名（审计/测试）
    failure_reason: str | None = None

    @classmethod
    def ok(cls, text: str) -> ToolResult:
        return cls(output=text, status="success")

    # 兼容旧 API 名
    @classmethod
    def success(cls, text: str) -> ToolResult:
        return cls.ok(text)

    @classmethod
    def failure(
        cls,
        text: str,
        *,
        error_kind: ErrorKind = "PERMANENT",
        retryable: bool = False,
        external_state_unknown: bool = False,
        diagnostic_summary: str | None = None,
        failure_reason: str | None = None,
    ) -> ToolResult:
        reason = failure_reason or text
        return cls(
            output=text,
            status="failure",
            error_kind=error_kind,
            retryable=retryable,
            external_state_unknown=external_state_unknown,
            diagnostic_summary=diagnostic_summary or reason,
            failure_reason=reason,
        )

    @classmethod
    def from_command_result(
        cls,
        command_result: CommandResult,
        *,
        success_text: str | None = None,
        failure_prefix: str = "命令执行失败",
    ) -> ToolResult:
        """将 CommandResult 映射为统一 ToolResult。"""
        if command_result.is_success:
            return cls.ok(success_text or command_result.output or "命令执行成功")

        if command_result.status == "connection_unavailable":
            text = command_result.output or "连接不可用"
            return cls.failure(
                text,
                error_kind="TRANSIENT",
                retryable=True,
                external_state_unknown=False,
                diagnostic_summary=command_result.diagnostic_summary,
                failure_reason=text,
            )

        if command_result.status == "timeout_unknown":
            text = command_result.output or "命令执行超时，外部状态未知"
            return cls.failure(
                text,
                error_kind="TRANSIENT",
                retryable=False,
                external_state_unknown=True,
                diagnostic_summary=command_result.diagnostic_summary,
                failure_reason=text,
            )

        # 明确失败
        text = command_result.output or failure_prefix
        if failure_prefix and not text.startswith(failure_prefix):
            text = f"{failure_prefix}: {text}"
        return cls.failure(
            text,
            error_kind="PERMANENT",
            retryable=False,
            external_state_unknown=False,
            diagnostic_summary=command_result.diagnostic_summary,
            failure_reason=text,
        )

    @property
    def is_success(self) -> bool:
        return self.status == "success"

    @property
    def text(self) -> str:
        """兼容旧字段名。"""
        return self.output

    def player_message(self) -> str:
        """面向玩家的稳定错误建议；成功时返回 output。"""
        if self.status == "success":
            return self.output
        if self.error_kind and self.error_kind in PLAYER_ERROR_ADVICE:
            return PLAYER_ERROR_ADVICE[self.error_kind]
        return self.output

    def __str__(self) -> str:
        return self.output
