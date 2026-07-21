"""HostSessionStore unit tests."""

import sys
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.gateway.session_store import HostSessionStore


def test_player_session_isolated_per_player():
    store = HostSessionStore()
    cid = uuid4()
    host = store.create(cid, authenticated=True)
    a = host.get_player_session("Alice")
    b = host.get_player_session("Bob")
    a.current_provider = "deepseek"
    assert b.current_provider is None
    assert host.authenticated is True
