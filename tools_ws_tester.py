"""MCBE WebSocket 发包压力测试程序（交互式）。"""

import argparse
import asyncio
import json
import shlex
import time
from dataclasses import dataclass
from typing import Any

from websockets.server import WebSocketServerProtocol, serve

from models.minecraft import MinecraftCommand, MinecraftSubscribe


@dataclass
class SessionStats:
    sent_packets: int = 0
    sent_bytes: int = 0
    disconnected: bool = False
    disconnect_reason: str | None = None


class InteractiveWSTester:
    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self.client: WebSocketServerProtocol | None = None
        self.stats = SessionStats()
        self._client_connected = asyncio.Event()
        self._stop = asyncio.Event()

    async def start(self) -> None:
        async with serve(self._handle_client, self.host, self.port, ping_interval=None):
            print(f"[tester] 服务已启动: ws://{self.host}:{self.port}")
            print("[tester] 等待 MCBE 客户端连接...\n")
            await self._run_console()

    async def _handle_client(self, ws: WebSocketServerProtocol) -> None:
        self.client = ws
        self.stats = SessionStats()
        self._client_connected.set()

        await ws.send(json.dumps({"Result": "true"}))
        await ws.send(MinecraftSubscribe.player_message().model_dump_json(exclude_none=True))
        print("\n[tester] 客户端已连接，可执行发包测试。")

        try:
            async for msg in ws:
                if isinstance(msg, str) and len(msg) < 512:
                    print(f"[recv] {msg}")
        except Exception as exc:
            self.stats.disconnected = True
            self.stats.disconnect_reason = str(exc)
            print(f"\n[tester] 客户端断开: {exc}")
        finally:
            self.client = None
            self._client_connected.clear()

    async def _run_console(self) -> None:
        help_text = (
            "命令:\n"
            "  wait                              等待客户端连接\n"
            "  burst <count> <size> [gap_ms]     连续发包\n"
            "  sweep <start> <step> <max> <size> [gap_ms]  扫描极限发包量\n"
            "  stats                             查看当前统计\n"
            "  quit                              退出\n"
        )
        print(help_text)
        while not self._stop.is_set():
            line = await asyncio.to_thread(input, "tester> ")
            parts = shlex.split(line)
            if not parts:
                continue
            cmd = parts[0].lower()
            try:
                if cmd == "wait":
                    await self._wait_client()
                elif cmd == "burst":
                    await self._cmd_burst(parts)
                elif cmd == "sweep":
                    await self._cmd_sweep(parts)
                elif cmd == "stats":
                    self._print_stats()
                elif cmd in {"quit", "exit"}:
                    self._stop.set()
                else:
                    print(help_text)
            except Exception as exc:
                print(f"[error] {exc}")

    async def _wait_client(self) -> None:
        if self.client:
            print("[tester] 客户端已连接")
            return
        print("[tester] 等待客户端连接...")
        await self._client_connected.wait()
        print("[tester] 已连接")

    async def _send_payload(self, size: int) -> bool:
        if not self.client:
            return False
        txt = "X" * size
        payload = MinecraftCommand.create_tellraw(f"LOAD_TEST:{txt}").model_dump_json(exclude_none=True)
        await self.client.send(payload)
        self.stats.sent_packets += 1
        self.stats.sent_bytes += len(payload.encode("utf-8"))
        return True

    async def _cmd_burst(self, parts: list[str]) -> None:
        if len(parts) < 3:
            raise ValueError("burst <count> <size> [gap_ms]")
        count = int(parts[1])
        size = int(parts[2])
        gap_ms = int(parts[3]) if len(parts) >= 4 else 0
        await self._wait_client()

        start = time.perf_counter()
        for i in range(count):
            if not self.client:
                print(f"[burst] 在第 {i} 包时连接断开")
                break
            await self._send_payload(size)
            if gap_ms > 0:
                await asyncio.sleep(gap_ms / 1000)
        elapsed = time.perf_counter() - start
        pps = self.stats.sent_packets / elapsed if elapsed > 0 else 0
        print(f"[burst] 完成: {count} 包, 耗时 {elapsed:.2f}s, 估算 {pps:.1f} pkt/s")

    async def _cmd_sweep(self, parts: list[str]) -> None:
        if len(parts) < 5:
            raise ValueError("sweep <start> <step> <max> <size> [gap_ms]")
        start_count = int(parts[1])
        step = int(parts[2])
        max_count = int(parts[3])
        size = int(parts[4])
        gap_ms = int(parts[5]) if len(parts) >= 6 else 0

        await self._wait_client()
        safe = 0
        for count in range(start_count, max_count + 1, step):
            if not self.client:
                print("[sweep] 客户端已断开，停止扫描")
                break
            before = self.stats.sent_packets
            print(f"[sweep] 测试 {count} 包 / payload={size} / gap={gap_ms}ms")
            for _ in range(count):
                if not self.client:
                    break
                await self._send_payload(size)
                if gap_ms > 0:
                    await asyncio.sleep(gap_ms / 1000)
            await asyncio.sleep(1.0)
            if self.client:
                safe = count
                print(f"[sweep] ✅ 通过 {count} 包 (本轮新增 {self.stats.sent_packets - before})")
            else:
                print(f"[sweep] ❌ 在 {count} 包时触发断开")
                break
        print(f"[sweep] 当前安全上限（粗略）: {safe} 包")

    def _print_stats(self) -> None:
        print(
            f"[stats] sent_packets={self.stats.sent_packets}, sent_bytes={self.stats.sent_bytes}, "
            f"connected={self.client is not None}, disconnected={self.stats.disconnected}, "
            f"reason={self.stats.disconnect_reason}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="MCBE WebSocket 压测工具")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8081)
    args = parser.parse_args()

    asyncio.run(InteractiveWSTester(args.host, args.port).start())


if __name__ == "__main__":
    main()
