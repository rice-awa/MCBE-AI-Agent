"""FlowControlMiddleware 单元测试。"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.websocket.flow_control import FlowControlMiddleware


class TestSplitText:
    """测试语义分片核心 _split_text。"""

    def test_short_text_no_split(self):
        """短文本不应被分片。"""
        result = FlowControlMiddleware._split_text("Hello", max_length=400)
        assert result == ["Hello"]

    def test_empty_text(self):
        """空文本应返回包含空字符串的列表。"""
        result = FlowControlMiddleware._split_text("", max_length=400)
        assert result == [""]

    def test_forced_chunk_long_text(self):
        """超长文本应被强制等长截断。"""
        text = "X" * 1000
        result = FlowControlMiddleware._split_text(text, max_length=400)
        assert len(result) == 3
        assert result[0] == "X" * 400
        assert result[1] == "X" * 400
        assert result[2] == "X" * 200

    def test_semantic_split_by_sentences(self):
        """优先按句子分片（在较小阈值下每句独立）。"""
        text = "第一句。第二句！第三句？"
        result = FlowControlMiddleware._split_text(text, max_length=5)
        assert len(result) == 3
        assert result[0] == "第一句。"
        assert result[1] == "第二句！"
        assert result[2] == "第三句？"

    def test_merge_short_sentences(self):
        """短句应合并到同一分片。"""
        text = "A。B。C。"
        result = FlowControlMiddleware._split_text(text, max_length=10)
        assert len(result) == 1
        assert result[0] == "A。B。C。"

    def test_mixed_long_sentence(self):
        """混合超长句：超长句截断，短句合并。"""
        text = "A" * 500 + "。" + "B" * 100
        result = FlowControlMiddleware._split_text(text, max_length=400)
        assert result[0] == "A" * 400
        assert result[1] == "A" * 100 + "。"
        assert result[2] == "B" * 100

    def test_split_by_newline(self):
        """换行符应作为分隔符（在较小阈值下每行独立）。"""
        text = "第一行\n第二行\n第三行"
        result = FlowControlMiddleware._split_text(text, max_length=5)
        assert len(result) == 3
        assert result[0] == "第一行\n"
        assert result[1] == "第二行\n"
        assert result[2] == "第三行"

    def test_consecutive_delimiters(self):
        """纯分隔符串：连续 '!!!' 应被合并为单分片（不丢字符）。"""
        result = FlowControlMiddleware._split_text("!!!", max_length=10)
        assert result == ["!!!"]
        # 中文连续分隔符同理
        result_zh = FlowControlMiddleware._split_text("。。。", max_length=10)
        assert result_zh == ["。。。"]

    def test_leading_delimiter(self):
        """句首分隔符：'!hello' 不应丢失开头的 '!'。"""
        result = FlowControlMiddleware._split_text("!hello", max_length=10)
        assert "".join(result) == "!hello"
        result_zh = FlowControlMiddleware._split_text("。中文", max_length=10)
        assert "".join(result_zh) == "。中文"

    def test_long_sentence_then_short(self):
        """超长句紧跟短句：验证截断分支后 buffer 正确处理短句。"""
        text = "A" * 500 + "!" + "B" * 5
        result = FlowControlMiddleware._split_text(text, max_length=400)
        # 期望：超长句被切成 ['A*400', 'A*100!']，短句独立成片 'B*5'
        assert result == ["A" * 400, "A" * 100 + "!", "B" * 5]

    def test_trailing_delimiter_only(self):
        """单字符无分隔符：不丢内容也不爆。"""
        result = FlowControlMiddleware._split_text("a", max_length=10)
        assert result == ["a"]

    def test_crlf_in_text(self):
        """文本含 \\r\\n：\\n 仍作为分隔符，\\r 不丢失。"""
        text = "hello\r\nworld"
        result = FlowControlMiddleware._split_text(text, max_length=5)
        # 重组后内容应保持原文（\r 不能丢）
        assert "".join(result) == "hello\r\nworld"


class TestChunkTellraw:
    """测试 tellraw 分片生成。"""

    def test_short_tellraw(self):
        """短消息生成单条 payload。"""
        payloads = FlowControlMiddleware.chunk_tellraw("Hello", color="§a")
        assert len(payloads) == 1
        data = json.loads(payloads[0])
        assert "commandRequest" == data["header"]["messagePurpose"]
        assert "tellraw" in data["body"]["commandLine"]
        assert "§aHello" in data["body"]["commandLine"]

    def test_long_tellraw_chunks(self):
        """长消息生成多条 payload。"""
        text = "X" * 1000
        payloads = FlowControlMiddleware.chunk_tellraw(text, color="§b")
        assert len(payloads) == 3
        for payload in payloads:
            data = json.loads(payload)
            assert "tellraw" in data["body"]["commandLine"]

    def test_tellraw_escaping(self):
        """含特殊字符的文本应生成合法 JSON。"""
        text = '含"引号:冒号%百分号'
        payloads = FlowControlMiddleware.chunk_tellraw(text, color="§a")
        assert len(payloads) == 1
        # 应能正常解析为 JSON
        data = json.loads(payloads[0])
        assert data["header"]["messagePurpose"] == "commandRequest"

    def test_tellraw_preserves_color(self):
        """每个分片应保持相同颜色。"""
        text = "A" * 500 + "。" + "B" * 100
        payloads = FlowControlMiddleware.chunk_tellraw(text, color="§c")
        assert len(payloads) == 3
        for payload in payloads:
            data = json.loads(payload)
            cmd_line = data["body"]["commandLine"]
            assert "§c" in cmd_line


class TestChunkScriptevent:
    """测试 scriptevent 分片生成。"""

    def test_short_scriptevent(self):
        """短 content 生成单条 payload。"""
        payloads = FlowControlMiddleware.chunk_scriptevent("hello", "mcbeai:test")
        assert len(payloads) == 1
        data = json.loads(payloads[0])
        assert "scriptevent" in data["body"]["commandLine"]
        assert "hello" in data["body"]["commandLine"]

    def test_long_scriptevent_chunks(self):
        """长 content 生成多条 payload。"""
        text = "X" * 1000
        payloads = FlowControlMiddleware.chunk_scriptevent(text, "mcbeai:test")
        assert len(payloads) == 3
        for payload in payloads:
            data = json.loads(payload)
            assert "scriptevent" in data["body"]["commandLine"]

    def test_scriptevent_json_content(self):
        """含 JSON 的 content 应生成正确结构。"""
        content = json.dumps({"key": "value"}, ensure_ascii=False)
        payloads = FlowControlMiddleware.chunk_scriptevent(content, "mcbeai:test")
        assert len(payloads) == 1
        data = json.loads(payloads[0])
        assert "scriptevent" in data["body"]["commandLine"]


class TestChunkAiResponse:
    """测试 AI 响应同步分片生成。"""

    def test_short_ai_response(self):
        """短文本生成单条 payload。"""
        payloads = FlowControlMiddleware.chunk_ai_response(
            "Steve", "assistant", "Hello"
        )
        assert len(payloads) == 1
        data = json.loads(payloads[0])
        cmd_line = data["body"]["commandLine"]
        assert "scriptevent mcbeai:ai_resp" in cmd_line
        # 提取 JSON payload
        json_start = cmd_line.find("{")
        inner = json.loads(cmd_line[json_start:])
        assert inner["p"] == "Steve"
        assert inner["r"] == "assistant"
        assert inner["c"] == "Hello"
        assert inner["i"] == 1
        assert inner["n"] == 1

    def test_long_ai_response_chunks(self):
        """长文本生成多条 payload。

        ai_response 包装开销较大（~130 B），文本预算 ≈ 331 B，
        因此 1000 字符 ASCII 会被切成 4 片而不是字符级的 3 片。
        """
        text = "X" * 1000
        payloads = FlowControlMiddleware.chunk_ai_response(
            "Steve", "assistant", text
        )
        assert len(payloads) == 4
        for idx, payload in enumerate(payloads, start=1):
            data = json.loads(payload)
            cmd_line = data["body"]["commandLine"]
            json_start = cmd_line.find("{")
            inner = json.loads(cmd_line[json_start:])
            assert inner["p"] == "Steve"
            assert inner["r"] == "assistant"
            assert inner["i"] == idx
            assert inner["n"] == 4

    def test_ai_response_same_message_id(self):
        """同一批分片应共享相同的 message id。"""
        text = "A" * 500 + "。" + "B" * 100
        payloads = FlowControlMiddleware.chunk_ai_response(
            "Steve", "user", text
        )
        assert len(payloads) == 3
        ids = []
        for payload in payloads:
            data = json.loads(payload)
            cmd_line = data["body"]["commandLine"]
            json_start = cmd_line.find("{")
            inner = json.loads(cmd_line[json_start:])
            ids.append(inner["id"])
        assert ids[0] == ids[1] == ids[2]

    def test_ai_response_empty_text(self):
        """空文本应返回单条空内容 payload。"""
        payloads = FlowControlMiddleware.chunk_ai_response(
            "Steve", "assistant", ""
        )
        assert len(payloads) == 1
        data = json.loads(payloads[0])
        cmd_line = data["body"]["commandLine"]
        json_start = cmd_line.find("{")
        inner = json.loads(cmd_line[json_start:])
        assert inner["c"] == ""


class TestChunkRawCommand:
    """测试原始命令分片。"""

    def test_short_command(self):
        """短命令生成单条 payload。"""
        payloads = FlowControlMiddleware.chunk_raw_command("say hello")
        assert len(payloads) == 1
        data = json.loads(payloads[0])
        assert data["body"]["commandLine"] == "say hello"

    def test_long_command_raises(self):
        """超长命令应抛 ValueError，不允许静默非法分片。"""
        cmd = "say " + "X" * 1000
        with pytest.raises(ValueError, match="raw command too long"):
            FlowControlMiddleware.chunk_raw_command(cmd)


class TestMaxLengthParameter:
    """测试自定义 max_length 参数。"""

    def test_custom_max_length(self):
        """自定义 max_length 应生效。"""
        text = "A" * 150
        payloads = FlowControlMiddleware.chunk_tellraw(text, max_length=100)
        assert len(payloads) == 2

    def test_invalid_max_length_fallback(self):
        """无效 max_length 应回退到默认值。"""
        text = "A" * 500
        # max_length=0 应使用默认值 400
        payloads = FlowControlMiddleware.chunk_tellraw(text, max_length=0)
        assert len(payloads) == 2
        # max_length=None 也应使用默认值
        payloads = FlowControlMiddleware.chunk_tellraw(text, max_length=None)
        assert len(payloads) == 2


class TestEmptyTextContract:
    """空文本契约：tellraw/scriptevent 返回 []，ai_response 仍发 1 条。"""

    def test_chunk_tellraw_empty(self):
        assert FlowControlMiddleware.chunk_tellraw("") == []

    def test_chunk_scriptevent_empty(self):
        assert FlowControlMiddleware.chunk_scriptevent("") == []

    def test_chunk_ai_response_empty_keeps_one(self):
        payloads = FlowControlMiddleware.chunk_ai_response("Steve", "assistant", "")
        assert len(payloads) == 1


class TestConfigureRuntime:
    """configure() 注入运行时默认值。"""

    def test_configure_changes_default_max(self):
        original_max = FlowControlMiddleware.DEFAULT_MAX_CONTENT_LENGTH
        original_mode = FlowControlMiddleware.DEFAULT_SENTENCE_MODE
        try:
            FlowControlMiddleware.configure(max_content_length=100)
            payloads = FlowControlMiddleware.chunk_tellraw("A" * 250)
            assert len(payloads) == 3  # 250 / 100 = 3 片
        finally:
            FlowControlMiddleware.configure(
                max_content_length=original_max,
                sentence_mode=original_mode,
            )

    def test_configure_sentence_mode_off(self):
        """关闭语义分片模式后，应纯按 max_length 等长截断。"""
        original_mode = FlowControlMiddleware.DEFAULT_SENTENCE_MODE
        try:
            FlowControlMiddleware.configure(sentence_mode=False)
            # 含分隔符也应被等长截断
            result = FlowControlMiddleware._split_text("A。B。C。D", max_length=3)
            assert result == ["A。B", "。C。", "D"]
        finally:
            FlowControlMiddleware.configure(sentence_mode=original_mode)

    def test_configure_invalid_values_ignored(self):
        """非法值应被忽略，保留现有默认。"""
        original = FlowControlMiddleware.DEFAULT_MAX_CONTENT_LENGTH
        FlowControlMiddleware.configure(max_content_length=0)
        assert FlowControlMiddleware.DEFAULT_MAX_CONTENT_LENGTH == original
        FlowControlMiddleware.configure(max_content_length=-1)
        assert FlowControlMiddleware.DEFAULT_MAX_CONTENT_LENGTH == original


class TestChunkDelayFor:
    """chunk_delay_for: 分片间延迟策略集中化。"""

    def test_known_kinds(self):
        assert FlowControlMiddleware.chunk_delay_for("tellraw") == 0.05
        assert FlowControlMiddleware.chunk_delay_for("scriptevent") == 0.05
        assert FlowControlMiddleware.chunk_delay_for("ai_resp") == 0.15
        assert FlowControlMiddleware.chunk_delay_for("ai_resp_prelude") == 0.5

    def test_unknown_kind_returns_zero(self):
        """未知 kind 返回 0.0，不抛异常，避免阻塞调用方。"""
        assert FlowControlMiddleware.chunk_delay_for("unknown") == 0.0
        assert FlowControlMiddleware.chunk_delay_for("") == 0.0


class TestByteSafetyAssertion:
    """字节兜底校验：分片后 commandLine 字节数必须 ≤ 461 B。"""

    def test_pure_chinese_no_overflow(self):
        """纯中文文本（每字符 3 字节）应被字节预算约束，不会超出 461 B。"""
        text = "中" * 1000
        payloads = FlowControlMiddleware.chunk_tellraw(text)
        for payload in payloads:
            data = json.loads(payload)
            byte_len = len(data["body"]["commandLine"].encode("utf-8"))
            assert byte_len <= 461, f"commandLine {byte_len} B exceeds budget"

    def test_mixed_emoji_no_overflow(self):
        """含 emoji（4 字节代理对）的文本仍应受字节预算约束。"""
        text = "你好🌍" * 200
        payloads = FlowControlMiddleware.chunk_scriptevent(text, "mcbeai:test")
        for payload in payloads:
            data = json.loads(payload)
            byte_len = len(data["body"]["commandLine"].encode("utf-8"))
            assert byte_len <= 461

    def test_ai_response_byte_budget(self):
        """ai_response 包装开销最大，更需要字节预算保护。"""
        text = "中" * 500
        payloads = FlowControlMiddleware.chunk_ai_response(
            "Steve", "assistant", text
        )
        for payload in payloads:
            data = json.loads(payload)
            byte_len = len(data["body"]["commandLine"].encode("utf-8"))
            assert byte_len <= 461


class TestEndToEndConnectionFlow:
    """e2e 风格：通过 ConnectionManager 走 mock websocket 验证分片实际落地。"""

    @pytest.mark.asyncio
    async def test_send_game_message_chunks_long_chinese(self):
        """长中文文本通过 connection 出口应被分片，每片均合法 JSON。"""
        from unittest.mock import AsyncMock, MagicMock
        from services.websocket.connection import ConnectionManager, ConnectionState

        broker = MagicMock()
        manager = ConnectionManager(broker=broker, dev_mode=True)

        ws = AsyncMock()
        state = ConnectionState(websocket=ws)

        await manager._send_game_message_with_color(state, "中" * 500, "§a")

        assert ws.send.await_count >= 2
        for call in ws.send.await_args_list:
            payload = call.args[0]
            data = json.loads(payload)
            cmd_line = data["body"]["commandLine"]
            assert "tellraw" in cmd_line
            assert len(cmd_line.encode("utf-8")) <= 461

    @pytest.mark.asyncio
    async def test_run_command_does_not_chunk(self):
        """原始命令不应被分片：只发送 1 条 commandRequest。"""
        from unittest.mock import AsyncMock, MagicMock
        from services.websocket.connection import ConnectionManager, ConnectionState

        broker = MagicMock()
        manager = ConnectionManager(broker=broker, dev_mode=True)

        ws = AsyncMock()
        state = ConnectionState(websocket=ws)

        await manager._run_command(state, "say hi", result_future=None)

        assert ws.send.await_count == 1
        payload = ws.send.await_args.args[0]
        data = json.loads(payload)
        assert data["body"]["commandLine"] == "say hi"
