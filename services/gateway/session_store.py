"""Host-owned per-connection / per-player session state for the SDK gateway."""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from core.session import DEFAULT_PLAYER_KEY


@dataclass
class PlayerSession:
    """单个玩家在某连接上的会话状态（按玩家隔离的可变设置）。"""

    player_name: str
    context_enabled: bool = True
    current_provider: str | None = None
    current_template: str = "default"
    custom_variables: dict[str, str] = field(default_factory=dict)


@dataclass
class HostConnectionSession:
    """Host-side session attached to one SDK ConnectionState id."""

    connection_id: UUID
    authenticated: bool = False
    ai_broadcast_all: bool = False
    ai_broadcast_players: set[str] = field(default_factory=set)
    _player_sessions: dict[str, PlayerSession] = field(default_factory=dict)

    def get_player_session(self, player_name: str | None) -> PlayerSession:
        """获取指定玩家的会话状态；不存在则按默认值创建。"""
        key = player_name or DEFAULT_PLAYER_KEY
        session = self._player_sessions.get(key)
        if session is None:
            session = PlayerSession(player_name=key)
            self._player_sessions[key] = session
        return session

    def should_broadcast_ai_chat(self, player_name: str | None) -> bool:
        if self.ai_broadcast_all:
            return True
        return bool(player_name and player_name in self.ai_broadcast_players)

    def all_player_sessions(self) -> list[PlayerSession]:
        """快照式列出连接下所有玩家会话。"""
        return list(self._player_sessions.values())


class HostSessionStore:
    """Create / lookup / remove HostConnectionSession by connection id."""

    def __init__(self) -> None:
        self._sessions: dict[UUID, HostConnectionSession] = {}

    def get(self, connection_id: UUID) -> HostConnectionSession | None:
        return self._sessions.get(connection_id)

    def create(
        self,
        connection_id: UUID,
        *,
        authenticated: bool = False,
    ) -> HostConnectionSession:
        existing = self._sessions.get(connection_id)
        if existing is not None:
            return existing
        session = HostConnectionSession(
            connection_id=connection_id,
            authenticated=authenticated,
        )
        self._sessions[connection_id] = session
        return session

    def remove(self, connection_id: UUID) -> HostConnectionSession | None:
        return self._sessions.pop(connection_id, None)
