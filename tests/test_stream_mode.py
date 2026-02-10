"""流式输出模式测试"""

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

from models.agent import AgentDependencies
from services.agent import core


async def _noop(_: str) -> None:
    return None


class _FakeResult:
    def __init__(
        self,
        chunks: list[str],
        messages: list[Any] | None = None,
    ) -> None:
        self._chunks = chunks
        self._messages = messages or ["user", "assistant"]

    async def stream_text(self, delta: bool = True):
        for chunk in self._chunks:
            yield chunk

    def usage(self) -> dict[str, int]:
        return {"total_tokens": 12}

    def all_messages(self) -> list[Any]:
        return self._messages


class _FakeStreamContext:
    def __init__(self, result: _FakeResult) -> None:
        self._result = result

    async def __aenter__(self) -> _FakeResult:
        return self._result

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None


class _FakeAgent:
    def __init__(
        self,
        chunks: list[str],
        messages: list[Any] | None = None,
        fallback_output: str = "",
        fallback_messages: list[Any] | None = None,
        fallback_outputs: list[str] | None = None,
        fallback_messages_list: list[list[Any]] | None = None,
    ) -> None:
        self._chunks = chunks
        self._messages = messages
        self._fallback_outputs = fallback_outputs or [fallback_output]
        self._fallback_messages_list = fallback_messages_list or [
            fallback_messages or ["user", "assistant"]
        ]
        self.run_called = False
        self.run_call_count = 0

    def run_stream(self, *args: Any, **kwargs: Any) -> _FakeStreamContext:
        return _FakeStreamContext(_FakeResult(self._chunks, self._messages))

    async def run(self, *args: Any, **kwargs: Any) -> Any:
        self.run_called = True
        self.run_call_count += 1
        index = min(self.run_call_count - 1, len(self._fallback_outputs) - 1)
        return SimpleNamespace(
            output=self._fallback_outputs[index],
            usage=lambda: {"total_tokens": 18},
            all_messages=lambda: self._fallback_messages_list[
                min(index, len(self._fallback_messages_list) - 1)
            ],
        )


def _fake_tool_call_message() -> Any:
    return SimpleNamespace(parts=[SimpleNamespace(part_kind="tool-call")])


def _fake_tool_return_message() -> Any:
    return SimpleNamespace(parts=[SimpleNamespace(part_kind="tool-return")])


def _build_deps(stream_sentence_mode: bool) -> AgentDependencies:
    return AgentDependencies(
        connection_id=uuid4(),
        player_name="Tester",
        settings=SimpleNamespace(stream_sentence_mode=stream_sentence_mode),  # type: ignore[arg-type]
        http_client=None,  # type: ignore[arg-type]
        send_to_game=_noop,
        run_command=_noop,
    )


async def test_stream_sentence_mode_true_should_stream_by_sentence(monkeypatch) -> None:
    monkeypatch.setattr(core, "chat_agent", _FakeAgent(["你好", "，世界。", "再见", "！"]))

    events = [event async for event in core.stream_chat("hi", _build_deps(True), model="fake")]
    content_events = [event for event in events if event.content]

    assert [event.content for event in content_events] == ["你好，世界。", "再见！"]
    assert events[-1].metadata is not None
    assert events[-1].metadata.get("is_complete") is True


async def test_stream_sentence_mode_false_should_batch_after_complete(monkeypatch) -> None:
    sentence_1 = ("甲" * 390) + "。"
    sentence_2 = ("乙" * 390) + "。"
    sentence_3 = ("丙" * 390) + "。"
    chunks = [sentence_1[:120], sentence_1[120:] + sentence_2, sentence_3]

    monkeypatch.setattr(core, "chat_agent", _FakeAgent(chunks))

    events = [event async for event in core.stream_chat("hi", _build_deps(False), model="fake")]
    content_events = [event for event in events if event.content]

    assert [event.content for event in content_events] == [sentence_1 + sentence_2, sentence_3]
    assert events[-1].metadata is not None
    assert events[-1].metadata.get("is_complete") is True


async def test_tool_chain_incomplete_should_trigger_fallback_run(monkeypatch) -> None:
    fake_agent = _FakeAgent(
        chunks=["我来给你钻石。"],
        messages=[_fake_tool_call_message()],
        fallback_output="已执行命令: /give @p diamond",
        fallback_messages=[
            _fake_tool_call_message(),
            _fake_tool_return_message(),
        ],
    )
    monkeypatch.setattr(core, "chat_agent", fake_agent)

    events = [event async for event in core.stream_chat("hi", _build_deps(False), model="fake")]
    content_events = [event for event in events if event.content]

    assert fake_agent.run_called is True
    assert [event.content for event in content_events] == ["已执行命令: /give @p diamond"]
    assert events[-1].metadata is not None
    assert events[-1].metadata.get("tool_calls") == 1
    assert events[-1].metadata.get("tool_returns") == 1
    assert events[-1].metadata.get("tool_fallback_used") is True


def test_iter_sentence_batches_should_fallback_for_long_sentence() -> None:
    long_sentence = ("A" * 20) + "。"
    batches = list(core._iter_sentence_batches(long_sentence, max_chars=10))

    assert len(batches) == 3
    assert "".join(batches) == long_sentence


async def test_fallback_should_stream_remaining_text(monkeypatch) -> None:
    fake_agent = _FakeAgent(
        chunks=["开始。"],
        messages=[_fake_tool_call_message()],
        fallback_output="开始。完成。",
        fallback_messages=[_fake_tool_call_message(), _fake_tool_return_message()],
    )
    monkeypatch.setattr(core, "chat_agent", fake_agent)

    events = [event async for event in core.stream_chat("hi", _build_deps(True), model="fake")]
    content_events = [event for event in events if event.content]

    assert [event.content for event in content_events] == ["开始。", "完成。"]


async def test_tool_chain_should_retry_until_complete(monkeypatch) -> None:
    fake_agent = _FakeAgent(
        chunks=["我来给你钻石。"],
        messages=[_fake_tool_call_message()],
        fallback_outputs=["", "已执行命令: /give @p diamond"],
        fallback_messages_list=[
            [_fake_tool_call_message()],
            [_fake_tool_call_message(), _fake_tool_return_message()],
        ],
    )
    monkeypatch.setattr(core, "chat_agent", fake_agent)

    events = [event async for event in core.stream_chat("hi", _build_deps(False), model="fake")]
    content_events = [event for event in events if event.content]

    assert fake_agent.run_call_count == 2
    assert [event.content for event in content_events] == ["已执行命令: /give @p diamond"]
