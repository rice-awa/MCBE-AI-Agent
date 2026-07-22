"""Tests for host-owned Minecraft protocol compatibility helpers."""

from models.minecraft import MinecraftCommand


def test_host_tellraw_request_uses_player_origin() -> None:
    command = MinecraftCommand.create_tellraw("测试", target="Alex")

    assert command.body.origin.type == "player"
