"""Deprecated compatibility exports for the SDK MCBEWS/1 DTOs.

The Host no longer owns a second copy of the bridge wire models.  Keep this
module importable for extensions that used the old root path, while making the
SDK's typed models the only source of fields and validation.
"""

from __future__ import annotations

import warnings

from mcbe_ws_sdk.profiles.mcbews_v1.models import (
    AddonBridgeChunk,
    AddonBridgeRequest,
    AddonBridgeResponse,
    ApprovalDecision,
    SessionError,
    SessionRequest,
    SessionResponse,
    TextResponseChunk,
    TextResponseMessage,
    TokenUsage,
    UiChatChunk,
    UiChatMessage,
)

warnings.warn(
    "models.addon_bridge is deprecated; import MCBEWS/1 DTOs from mcbe_ws_sdk",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = [
    "AddonBridgeChunk",
    "AddonBridgeRequest",
    "AddonBridgeResponse",
    "ApprovalDecision",
    "SessionError",
    "SessionRequest",
    "SessionResponse",
    "TextResponseChunk",
    "TextResponseMessage",
    "TokenUsage",
    "UiChatChunk",
    "UiChatMessage",
]
