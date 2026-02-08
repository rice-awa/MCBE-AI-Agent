"""Agent 工具函数测试"""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

from services.agent.tools import (
    build_actionbar_command,
    build_tellraw_command,
    build_title_commands,
    escape_command_text,
)


def test_escape_command_text() -> None:
    assert escape_command_text('Hello "MC"') == 'Hello \\"MC\\"'
    assert escape_command_text("Line1\nLine2") == "Line1\\nLine2"


def test_build_title_commands() -> None:
    commands = build_title_commands("主标题", "副标题", 10, 70, 20)
    assert commands[0] == 'title @a title "主标题"'
    assert commands[1] == 'title @a subtitle "副标题"'
    assert commands[2] == "title @a times 10 70 20"


def test_build_actionbar_command() -> None:
    command = build_actionbar_command("提示")
    assert command == 'title @a actionbar "提示"'


def test_build_tellraw_command() -> None:
    command = build_tellraw_command("测试", "§b")
    assert command.startswith("tellraw @a")
    assert "§b" in command
