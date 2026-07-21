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


def test_auto_approve_tools_scopes():
    store = HostSessionStore()
    cid = uuid4()
    host = store.create(cid, authenticated=True)

    host.enable_auto_approve_tools_conversation("Alice", "c1")
    assert host.should_auto_approve_tools("Alice", "c1") is True
    assert host.should_auto_approve_tools("Alice", "c2") is False

    host.enable_auto_approve_tools_forever("Alice")
    assert host.should_auto_approve_tools("Alice", "c2") is True
    assert host.should_auto_approve_tools("Bob", "c2") is False
