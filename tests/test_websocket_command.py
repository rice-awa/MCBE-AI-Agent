"""WebSocket command parsing tests."""

from services.websocket.command import CommandRegistry, ParsedCommand


COMMANDS = {
    "AGENT 聊天": {
        "type": "chat",
        "aliases": ["ask"],
        "description": "聊天",
        "usage": "<消息>",
    },
    "#登录": {
        "type": "login",
        "aliases": [],
        "description": "登录",
        "usage": "<密码>",
    },
}


def test_resolve_parsed_command_keeps_prefix_type_and_content() -> None:
    registry = CommandRegistry(COMMANDS)

    parsed = registry.resolve_parsed("AGENT 聊天 hello")

    assert parsed == ParsedCommand(
        type="chat",
        content="hello",
        prefix="AGENT 聊天",
        raw="AGENT 聊天 hello",
        matched_alias=None,
    )


def test_resolve_parsed_command_records_alias() -> None:
    registry = CommandRegistry(COMMANDS)

    parsed = registry.resolve_parsed("ask hello")

    assert parsed == ParsedCommand(
        type="chat",
        content="hello",
        prefix="AGENT 聊天",
        raw="ask hello",
        matched_alias="ask",
    )


def test_resolve_parsed_command_returns_none_for_non_command() -> None:
    registry = CommandRegistry(COMMANDS)

    assert registry.resolve_parsed("hello") is None
    assert registry.resolve("hello") == (None, "hello")


def test_resolve_parsed_command_requires_prefix_boundary() -> None:
    registry = CommandRegistry(COMMANDS)

    assert registry.resolve_parsed("AGENT 聊天室 hello") is None
    assert registry.resolve_parsed("#登录密码") is None
    assert registry.resolve_parsed("AGENT 聊天\thello").content == "hello"


def test_resolve_parsed_command_requires_alias_boundary() -> None:
    registry = CommandRegistry(COMMANDS)

    assert registry.resolve_parsed("askew question") is None
    assert registry.resolve_parsed("ask\tquestion").content == "question"
