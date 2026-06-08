"""对话标题生成服务测试"""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

from services.agent import title as title_module
from services.agent.title import clean_conversation_title, generate_conversation_title


def test_clean_conversation_title_strips_quotes_and_limits_length():
    assert clean_conversation_title('"建筑计划\n"', "如何建造一个自动农场") == "建筑计划"
    assert clean_conversation_title("", "如何建造一个自动农场和红石收集系统") == "如何建造一个自动农场和红"
    assert clean_conversation_title("一个非常非常非常非常非常长的标题", "备用") == "一个非常非常非常非常非常"


@pytest.mark.asyncio
async def test_generate_conversation_title_uses_agent_run(monkeypatch):
    captured = {}
    model = object()

    class FakeTitleAgent:
        async def run(self, prompt, model):
            captured["prompt"] = prompt
            captured["model"] = model
            return SimpleNamespace(output="红石农场")

    monkeypatch.setattr(title_module, "_TITLE_AGENT", FakeTitleAgent())

    result = await generate_conversation_title("如何建造一个红石自动农场", model)

    assert result == "红石农场"
    assert captured["model"] is model
    assert "Minecraft AI 助手" in captured["prompt"]
    assert "2-12 个中文字符" in captured["prompt"]
    assert "不要标点符号、引号、解释" in captured["prompt"]
    assert "如何建造一个红石自动农场" in captured["prompt"]
