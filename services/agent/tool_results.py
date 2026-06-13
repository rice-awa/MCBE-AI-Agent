"""结构化 Agent 工具返回值。"""

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class ToolResult:
    text: str
    success: bool = True
    failure_reason: str | None = None

    @classmethod
    def success(cls, text: str) -> "ToolResult":
        return cls(text=text, success=True)

    @classmethod
    def failure(cls, text: str, failure_reason: str | None = None) -> "ToolResult":
        return cls(text=text, success=False, failure_reason=failure_reason or text)

    @property
    def status(self) -> Literal["success", "failure"]:
        return "success" if self.success else "failure"

    def __str__(self) -> str:
        return self.text
