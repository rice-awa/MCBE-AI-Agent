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
        """长文本生成多条 payload。"""
        text = "X" * 1000
        payloads = FlowControlMiddleware.chunk_ai_response(
            "Steve", "assistant", text
        )
        assert len(payloads) == 3
        for idx, payload in enumerate(payloads, start=1):
            data = json.loads(payload)
            cmd_line = data["body"]["commandLine"]
            json_start = cmd_line.find("{")
            inner = json.loads(cmd_line[json_start:])
            assert inner["p"] == "Steve"
            assert inner["r"] == "assistant"
            assert inner["i"] == idx
            assert inner["n"] == 3

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

    def test_long_command_chunks(self):
        """超长命令按长度截断。"""
        cmd = "say " + "X" * 1000
        payloads = FlowControlMiddleware.chunk_raw_command(cmd)
        assert len(payloads) == 3


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
