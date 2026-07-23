"""Host-owned WS commandRequest / commandResponse tracker."""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

from mcbe_ws_sdk import FlowControlSettings, McbeOutboundDelivery
from mcbe_ws_sdk.gateway.connection import ConnectionState
from mcbe_ws_sdk.protocol.minecraft import MinecraftCommandResponse

from config.logging import get_logger

logger = get_logger(__name__)

_DEFAULT_TIMEOUT_SECONDS = 5.0


def format_command_response(response: MinecraftCommandResponse) -> str:
    """Format a commandResponse for the agent tool string contract.

    Matches legacy ``WebSocketServer._handle_command_response`` semantics.
    """
    body: dict[str, Any] = response.body if isinstance(response.body, dict) else {}
    status_code = body.get("statusCode")
    status_message = body.get("statusMessage") or ""

    if status_code == 0:
        return status_message or "命令执行成功"
    if status_message:
        return f"命令执行失败(statusCode={status_code}): {status_message}"
    return f"命令执行失败(statusCode={status_code})"


class WsCommandRunner:
    """Track pending WS commandRequest futures keyed by requestId.

    Pattern (same as SDK ``examples/addon-server``):

    1. ``run_raw`` / ``run`` registers a future under the outbound requestId;
    2. ``resolve`` is called from ``on_command_response``;
    3. ``close_connection`` fails leftovers on disconnect.
    """

    def __init__(
        self,
        flow: FlowControlSettings,
        *,
        timeout: float = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._flow = flow
        self._timeout = timeout
        self._pending: dict[UUID, dict[str, asyncio.Future[MinecraftCommandResponse]]] = {}

    def _bucket(
        self,
        connection_id: UUID,
    ) -> dict[str, asyncio.Future[MinecraftCommandResponse]]:
        bucket = self._pending.get(connection_id)
        if bucket is None:
            bucket = {}
            self._pending[connection_id] = bucket
        return bucket

    async def run(
        self,
        state: ConnectionState,
        command: str,
    ) -> MinecraftCommandResponse:
        """Alias for ``run_raw`` (SDK example naming)."""
        return await self.run_raw(state, command)

    async def run_raw(
        self,
        state: ConnectionState,
        command: str,
    ) -> MinecraftCommandResponse:
        if state.send_payload is None:
            raise RuntimeError("connection has no send_payload")

        loop = asyncio.get_running_loop()
        future: asyncio.Future[MinecraftCommandResponse] = loop.create_future()
        delivery = McbeOutboundDelivery(
            connection_id=state.id,
            send_payload=state.send_payload,
            settings=self._flow,
        )
        bucket = self._bucket(state.id)

        def _register(request_id: str) -> None:
            bucket[request_id] = future

            def _cleanup(done: asyncio.Future[MinecraftCommandResponse]) -> None:
                if bucket.get(request_id) is done:
                    bucket.pop(request_id, None)

            future.add_done_callback(_cleanup)

        request_id = await delivery.send_raw_command(
            command,
            source="ws_run_command",
            before_send=_register,
        )
        logger.debug(
            "ws_command_sent",
            connection_id=str(state.id),
            request_id=request_id,
            command=command,
        )
        try:
            return await asyncio.wait_for(future, self._timeout)
        except TimeoutError as exc:
            bucket.pop(request_id, None)
            raise TimeoutError(
                f"WS command timed out after {self._timeout:.0f}s (request_id={request_id})"
            ) from exc

    async def run_as_text(self, state: ConnectionState, command: str) -> str:
        """Run a command and return the legacy agent-facing result string.

        Protocol / size errors (FrameTooLargeError) are re-raised so callers can
        fail-fast; only game-side failures are stringified.
        """
        if state.send_payload is None:
            return "命令执行失败: WebSocket 未连接"
        try:
            response = await self.run_raw(state, command)
            return format_command_response(response)
        except Exception as exc:
            # Preserve structured class name for host mapping (FrameTooLarge etc.).
            logger.warning(
                "ws_command_run_failed",
                connection_id=str(state.id),
                error_type=type(exc).__name__,
                error=str(exc),
                command_line_bytes=len(str(command).encode("utf-8")),
            )
            raise

    def resolve(self, state: ConnectionState, response: MinecraftCommandResponse) -> bool:
        """Complete a pending future if this response is tracked. Returns True if handled."""
        bucket = self._pending.get(state.id)
        if not bucket:
            return False
        future = bucket.pop(response.request_id, None)
        if future is None or future.done():
            return False
        future.set_result(response)
        return True

    def close_connection(self, connection_id: UUID) -> None:
        bucket = self._pending.pop(connection_id, None)
        if bucket is None:
            return
        for future in bucket.values():
            if not future.done():
                future.set_exception(
                    ConnectionError("connection closed before commandResponse")
                )
        bucket.clear()
