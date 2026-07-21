"""游戏内工具调用/返回消息截断与格式化。"""

from models.agent import (
    DEFAULT_TOOL_CALL_DISPLAY_MAX_LENGTH,
    MCPrefix,
    format_tool_call_message,
    format_tool_result_message,
    truncate_text,
)


def test_truncate_text_appends_ellipsis():
    assert truncate_text("hello", 10) == "hello"
    assert truncate_text("hello world", 5) == "hello..."


def test_format_tool_call_message_truncates_long_string_args():
    long_cmd = "a" * 80
    msg = format_tool_call_message("run_minecraft_command", {"command": long_cmd})
    assert msg.startswith(MCPrefix.TOOL_CALL + "run_minecraft_command(")
    assert "..." in msg
    assert long_cmd not in msg
    assert len(msg) <= DEFAULT_TOOL_CALL_DISPLAY_MAX_LENGTH + 3  # 允许末尾 suffix


def test_format_tool_call_message_truncates_list_and_dict_args():
    commands = [f"say hello {i} " + ("x" * 40) for i in range(10)]
    msg = format_tool_call_message("run_minecraft_commands", {"commands": commands})
    assert msg.startswith(MCPrefix.TOOL_CALL + "run_minecraft_commands(")
    assert "..." in msg
    assert len(msg) <= DEFAULT_TOOL_CALL_DISPLAY_MAX_LENGTH + 3
    # 完整内容不应原样塞进游戏内消息
    assert commands[0] not in msg


def test_format_tool_call_message_limits_arg_count():
    args = {f"k{i}": f"v{i}" for i in range(6)}
    msg = format_tool_call_message("demo_tool", args)
    assert "k0=" in msg
    assert "k1=" in msg
    assert "k2=" in msg
    assert "k3=" not in msg
    assert "..." in msg


def test_format_tool_call_message_raw_string_args_truncated():
    raw = "not-json " + ("y" * 200)
    msg = format_tool_call_message("demo_tool", raw)
    assert msg.startswith(MCPrefix.TOOL_CALL + "demo_tool(")
    assert msg.endswith("...")
    assert len(msg) <= DEFAULT_TOOL_CALL_DISPLAY_MAX_LENGTH + 3
    assert raw not in msg


def test_format_tool_call_message_no_args():
    assert format_tool_call_message("ping") == f"{MCPrefix.TOOL_CALL}ping"


def test_format_tool_result_message_truncates_long_result():
    result = "z" * 200
    msg = format_tool_result_message("wiki_search", result, max_length=80)
    assert msg.startswith(f"{MCPrefix.TOOL_CALL}wiki_search: ")
    assert msg.endswith("...")
    assert result not in msg
    # 结果部分最多 80 字符 + "..."
    body = msg.split(": ", 1)[1]
    assert body == "z" * 80 + "..."
