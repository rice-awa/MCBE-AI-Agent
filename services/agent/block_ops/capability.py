"""Per-connection block-ops capability probe and cache."""

from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol


class BlockCapabilityStatus(StrEnum):
    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"


class _BridgeClient(Protocol):
    async def request(self, capability: str, payload: dict[str, Any]) -> dict[str, Any]:
        ...


@dataclass(frozen=True)
class BlockCapabilityRecord:
    status: BlockCapabilityStatus
    probed_at: float
    detail: str | None = None
    capabilities: dict[str, Any] | None = None


class BlockCapabilityCache:
    """Thread-safe capability cache keyed by connection_id."""

    def __init__(self) -> None:
        self._items: dict[str, BlockCapabilityRecord] = {}
        self._lock = threading.RLock()
        self._inflight: dict[str, asyncio.Future[BlockCapabilityRecord]] = {}

    def get(self, connection_id: str) -> BlockCapabilityRecord | None:
        with self._lock:
            return self._items.get(str(connection_id))

    def set(self, connection_id: str, record: BlockCapabilityRecord) -> None:
        with self._lock:
            self._items[str(connection_id)] = record

    def clear_connection(self, connection_id: str) -> bool:
        with self._lock:
            return self._items.pop(str(connection_id), None) is not None

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self._inflight.clear()

    async def probe(
        self,
        connection_id: str,
        bridge: _BridgeClient | None,
        *,
        force: bool = False,
    ) -> BlockCapabilityRecord:
        cid = str(connection_id)
        if not force:
            cached = self.get(cid)
            if cached is not None:
                return cached

        if bridge is None:
            record = BlockCapabilityRecord(
                status=BlockCapabilityStatus.UNAVAILABLE,
                probed_at=time.time(),
                detail="addon_bridge missing",
            )
            self.set(cid, record)
            return record

        loop = asyncio.get_running_loop()
        with self._lock:
            if not force:
                cached = self._items.get(cid)
                if cached is not None:
                    return cached
            existing = self._inflight.get(cid)
            if existing is not None and not existing.done():
                wait_future = existing
            else:
                wait_future = loop.create_future()
                self._inflight[cid] = wait_future
                wait_future = None  # signal caller to run probe

        if wait_future is not None:
            return await wait_future

        try:
            record = await self._probe_bridge(bridge)
        except Exception as exc:  # pragma: no cover - defensive
            record = BlockCapabilityRecord(
                status=BlockCapabilityStatus.FAILED,
                probed_at=time.time(),
                detail=str(exc),
            )

        self.set(cid, record)
        with self._lock:
            fut = self._inflight.pop(cid, None)
            if fut is not None and not fut.done():
                fut.set_result(record)
        return record

    async def _probe_bridge(self, bridge: _BridgeClient) -> BlockCapabilityRecord:
        try:
            result = await bridge.request("get_capabilities", {})
        except TimeoutError as exc:
            return BlockCapabilityRecord(
                status=BlockCapabilityStatus.UNAVAILABLE,
                probed_at=time.time(),
                detail=str(exc) or "timeout",
            )
        except Exception as exc:
            text = str(exc).lower()
            if any(
                token in text
                for token in ("timeout", "disconnect", "connection", "unavailable", "closed")
            ):
                return BlockCapabilityRecord(
                    status=BlockCapabilityStatus.UNAVAILABLE,
                    probed_at=time.time(),
                    detail=str(exc),
                )
            return BlockCapabilityRecord(
                status=BlockCapabilityStatus.FAILED,
                probed_at=time.time(),
                detail=str(exc),
            )

        if not isinstance(result, dict):
            return BlockCapabilityRecord(
                status=BlockCapabilityStatus.FAILED,
                probed_at=time.time(),
                detail="invalid capability response",
            )

        if result.get("ok") is False:
            payload = result.get("payload") if isinstance(result.get("payload"), dict) else {}
            code = payload.get("code") or payload.get("error") or result.get("error")
            return BlockCapabilityRecord(
                status=BlockCapabilityStatus.FAILED,
                probed_at=time.time(),
                detail=str(code or "ok:false"),
            )

        payload = result.get("payload") if isinstance(result.get("payload"), dict) else result
        capabilities = payload.get("capabilities") if isinstance(payload, dict) else None
        if not isinstance(capabilities, dict):
            # Old addons may return ok without capabilities map.
            return BlockCapabilityRecord(
                status=BlockCapabilityStatus.UNSUPPORTED,
                probed_at=time.time(),
                detail="missing capabilities.block_ops",
            )

        block_ops = capabilities.get("block_ops")
        if not isinstance(block_ops, dict):
            return BlockCapabilityRecord(
                status=BlockCapabilityStatus.UNSUPPORTED,
                probed_at=time.time(),
                detail="missing capabilities.block_ops",
                capabilities=capabilities,
            )

        # Require inspect at minimum for exposure of dedicated tools.
        inspect_ok = bool(block_ops.get("inspect", True))
        if not inspect_ok:
            return BlockCapabilityRecord(
                status=BlockCapabilityStatus.UNSUPPORTED,
                probed_at=time.time(),
                detail="block_ops.inspect disabled",
                capabilities=capabilities,
            )

        return BlockCapabilityRecord(
            status=BlockCapabilityStatus.SUPPORTED,
            probed_at=time.time(),
            capabilities=capabilities,
        )


_GLOBAL_CACHE = BlockCapabilityCache()


def get_block_capability_cache() -> BlockCapabilityCache:
    return _GLOBAL_CACHE


def reset_block_capability_cache() -> None:
    _GLOBAL_CACHE.clear()


def clear_block_capability(connection_id: str) -> bool:
    return _GLOBAL_CACHE.clear_connection(connection_id)


async def ensure_block_capability(
    connection_id: str,
    bridge: _BridgeClient | None,
    *,
    force: bool = False,
) -> BlockCapabilityRecord:
    return await _GLOBAL_CACHE.probe(connection_id, bridge, force=force)
