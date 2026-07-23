"""Drain MessageBroker response queues into SDK outbound delivery."""

from __future__ import annotations

import asyncio
import time
from contextlib import suppress
from typing import Any
from uuid import UUID

from mcbe_ws_sdk import (
    MCBEWS_V1,
    FlowControlSettings,
    McbeOutboundDelivery,
    McbewsV1Delivery,
    McbewsV1Profile,
    SystemNotification as SdkSystemNotification,
)
from mcbe_ws_sdk.gateway.connection import ConnectionState

from config.logging import get_logger
from core.queue import MessageBroker
from models.agent import MCColor, MCPrefix
from core.session import DEFAULT_CONVERSATION_ID
from models.constants import DEFAULT_PLAYER_DISPLAY_NAME
from models.messages import StreamChunk
from models.messages import SystemNotification as HostSystemNotification
from services.gateway.ws_command_runner import WsCommandRunner

logger = get_logger(__name__)


class BrokerResponseBridge:
    """Map host broker responses onto McbeOutboundDelivery / WsCommandRunner."""

    def __init__(
        self,
        broker: MessageBroker,
        flow: FlowControlSettings,
        ws_commands: WsCommandRunner,
        *,
        profile: McbewsV1Profile | None = None,
        log_raw: bool = False,
    ) -> None:
        self._broker = broker
        self._flow = flow
        self._ws_commands = ws_commands
        self._profile = profile if profile is not None else MCBEWS_V1
        self._log_raw = log_raw
        self._tasks: dict[UUID, asyncio.Task[None]] = {}

    def _delivery(self, state: ConnectionState) -> McbeOutboundDelivery | None:
        if state.send_payload is None:
            return None
        return McbeOutboundDelivery(
            connection_id=state.id,
            send_payload=state.send_payload,
            settings=self._flow,
            log_raw_payloads=self._log_raw,
        )

    async def start(self, state: ConnectionState) -> None:
        """Start draining the broker response queue for ``state.id``."""
        if state.id in self._tasks:
            return
        if self._broker.get_response_queue(state.id) is None:
            self._broker.register_connection(state.id)
        self._tasks[state.id] = asyncio.create_task(
            self._loop(state),
            name=f"broker-bridge:{state.id}",
        )

    async def stop(self, connection_id: UUID) -> None:
        """Cancel the drain loop for a connection and fail queued run_command futures."""
        task = self._tasks.pop(connection_id, None)
        if task is None:
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        self._fail_queued_command_futures(connection_id)

    def _fail_queued_command_futures(self, connection_id: UUID) -> None:
        queue = self._broker.get_response_queue(connection_id)
        if queue is None:
            return
        while not queue.empty():
            try:
                response = queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if (
                isinstance(response, dict)
                and response.get("type") == "run_command"
                and isinstance(response.get("result_future"), asyncio.Future)
            ):
                result_future = response["result_future"]
                if not result_future.done():
                    result_future.set_result("命令执行失败: 连接已关闭")

    async def _loop(self, state: ConnectionState) -> None:
        queue = self._broker.get_response_queue(state.id)
        if queue is None:
            logger.warning(
                "broker_bridge_missing_queue",
                connection_id=str(state.id),
            )
            return
        while state.id in self._tasks:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                raise
            try:
                await self._handle(state, item)
            except Exception as exc:
                logger.error(
                    "broker_bridge_handle_error",
                    connection_id=str(state.id),
                    error=str(exc),
                    item_type=type(item).__name__,
                )

    async def _handle(self, state: ConnectionState, item: Any) -> None:
        if isinstance(item, StreamChunk):
            await self._stream_chunk(state, item)
        elif isinstance(item, HostSystemNotification):
            await self._system(state, item)
        elif isinstance(item, dict):
            msg_type = item.get("type")
            if msg_type == "run_command":
                await self._run_command(state, item)
            elif msg_type == "ai_response_sync":
                await self._ai_sync(state, item)
            elif msg_type == "game_message":
                await self._game_message(state, item.get("content", ""))
            else:
                logger.warning(
                    "broker_bridge_unknown_dict",
                    connection_id=str(state.id),
                    msg_type=msg_type,
                )
        else:
            logger.warning(
                "broker_bridge_unknown_item",
                connection_id=str(state.id),
                item_type=type(item).__name__,
            )

    async def _stream_chunk(self, state: ConnectionState, chunk: StreamChunk) -> None:
        """Match legacy ConnectionManager._send_stream_chunk coloring/prefixes."""
        start = time.perf_counter()
        delivery_type = "scriptevent" if chunk.delivery == "scriptevent" else "tellraw"
        target = chunk.target or chunk.player_name or "@a"
        content_len = len(chunk.content or "")

        # Early-return chunk types never leave the host — skip delivery.*.
        if chunk.chunk_type == "thinking_end":
            return

        if chunk.chunk_type == "thinking_start":
            message = f"{MCColor.GRAY}{MCPrefix.THINKING}思考中..."
            color = MCColor.GRAY
        elif chunk.chunk_type == "content":
            message = chunk.content
            color = MCColor.GREEN
        elif chunk.chunk_type == "reasoning":
            message = f"{MCPrefix.THINKING}{chunk.content}"
            color = MCColor.GRAY
        elif chunk.chunk_type == "tool_call":
            message = chunk.content
            color = MCColor.YELLOW
        elif chunk.chunk_type == "tool_result":
            message = chunk.content
            color = MCColor.YELLOW
        elif chunk.chunk_type == "error":
            message = f"{MCPrefix.ERROR}{chunk.content}"
            color = MCColor.RED
        else:
            message = chunk.content
            color = MCColor.GREEN

        if not message:
            return

        # Delivery trace only on failure. Successful delivery has no audit value
        # beyond what trace.completed already records (chunk_count); per-chunk
        # delivery.completed was pure noise on the success path.
        delivery = self._delivery(state)
        if delivery is None:
            self._emit_delivery(
                "delivery.failed",
                chunk,
                status="failed",
                attributes={
                    "target": target,
                    "delivery_type": delivery_type,
                    "chunk_type": chunk.chunk_type,
                    "sequence": chunk.sequence,
                    "reason": "no_delivery",
                    "byte_count": content_len,
                    "chunk_count": 1,
                },
                duration_ms=int((time.perf_counter() - start) * 1000),
            )
            return

        try:
            if chunk.delivery == "scriptevent":
                await delivery.send_scriptevent(message, source="stream_scriptevent")
            else:
                await delivery.send_tellraw(
                    message,
                    color=color,
                    source="stream_tellraw",
                    target=target,
                )
        except Exception as exc:
            self._emit_delivery(
                "delivery.failed",
                chunk,
                status="failed",
                attributes={
                    "target": target,
                    "delivery_type": delivery_type,
                    "chunk_type": chunk.chunk_type,
                    "sequence": chunk.sequence,
                    "reason": type(exc).__name__,
                    "byte_count": len(message),
                    "chunk_count": 1,
                },
                duration_ms=int((time.perf_counter() - start) * 1000),
            )
            raise

    def _emit_delivery(
        self,
        event_name: str,
        chunk: StreamChunk,
        *,
        status: str = "info",
        attributes: dict[str, Any] | None = None,
        duration_ms: int | None = None,
    ) -> None:
        """Emit delivery.* failure events; never include chunk body or raw frames."""
        trace_id = getattr(chunk, "trace_id", None)
        attempt_id = getattr(chunk, "attempt_id", None)
        if not trace_id or not attempt_id:
            return
        try:
            from services.agent.trace import TraceContext, get_trace_recorder

            conversation_id = getattr(chunk, "conversation_id", None) or DEFAULT_CONVERSATION_ID
            context = TraceContext(
                trace_id=str(trace_id),
                run_id=str(trace_id),
                attempt_id=str(attempt_id),
                message_id=str(getattr(chunk, "id", "") or attempt_id),
                connection_id=str(chunk.connection_id),
                player_name=str(chunk.player_name or DEFAULT_PLAYER_DISPLAY_NAME),
                conversation_id=str(conversation_id),
            )
            get_trace_recorder().emit(
                event_name,
                context,
                status=status,  # type: ignore[arg-type]
                duration_ms=duration_ms,
                attributes=dict(attributes or {}),
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("delivery_trace_emit_failed", event=event_name, error=str(exc))

    async def _system(
        self,
        state: ConnectionState,
        notification: HostSystemNotification,
    ) -> None:
        delivery = self._delivery(state)
        if delivery is None:
            return
        await delivery.send_system_notification(
            SdkSystemNotification(
                level=notification.level,
                message=notification.message,
                player_name=notification.player_name,
            )
        )

    async def _game_message(self, state: ConnectionState, content: str) -> None:
        delivery = self._delivery(state)
        if delivery is None or not content:
            return
        await delivery.send_tellraw(
            content,
            color=MCColor.GREEN,
            source="game_message",
            target="@a",
        )

    async def _run_command(self, state: ConnectionState, item: dict[str, Any]) -> None:
        future = item.get("result_future")
        command = item.get("command") or ""
        command_line_bytes = len(str(command).encode("utf-8"))
        try:
            text = await self._ws_commands.run_as_text(state, command)
            if future is not None and not future.done():
                future.set_result(text)
        except Exception as exc:
            # Surface FrameTooLargeError (and similar) with class name so the
            # addon client can fail-fast instead of waiting for a bridge RESP.
            error_text = f"命令执行失败: {type(exc).__name__}: {exc}"
            logger.warning(
                "broker_bridge_run_command_failed",
                connection_id=str(state.id),
                error_type=type(exc).__name__,
                error=str(exc),
                command_line_bytes=command_line_bytes,
                command_prefix=str(command)[:48],
            )
            if future is not None and not future.done():
                future.set_result(error_text)

    async def _ai_sync(self, state: ConnectionState, response: dict[str, Any]) -> None:
        player_name = response.get("player_name", DEFAULT_PLAYER_DISPLAY_NAME)
        role = response.get("role", "assistant")
        text = response.get("text", "")
        if not text:
            return
        delivery = self._delivery(state)
        if delivery is None:
            return
        v1 = McbewsV1Delivery(delivery, profile=self._profile)
        await v1.send_response(
            player_name=player_name,
            role=role,
            text=text,
        )
