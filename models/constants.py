"""跨模块共享的语义常量。

这些值在多个模块中出现，集中定义以避免硬编码字面量漂移。
"""

from core.session import DEFAULT_CONVERSATION_ID, DEFAULT_PLAYER_KEY

__all__ = [
    "DEFAULT_PLAYER_KEY",
    "DEFAULT_CONVERSATION_ID",
    "DEFAULT_PLAYER_DISPLAY_NAME",
]

DEFAULT_PLAYER_DISPLAY_NAME = "Player"
