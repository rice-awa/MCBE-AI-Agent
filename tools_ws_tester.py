"""
MCBE WebSocket 发包压力测试程序（交互式，含日志与报告，支持自动递增测试）
修复版：精确 payload 长度 + requestId 匹配确认
"""

import argparse
import asyncio
import json
import shlex
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from websockets.server import WebSocketServerProtocol, serve

# 假设你的 models 模块存在以下类，请根据实际情况调整
from models.minecraft import MinecraftCommand, MinecraftSubscribe


@dataclass
class SessionStats:
    sent_packets: int = 0
    sent_bytes: int = 0
    sent_command_line_bytes: int = 0
    max_payload_bytes: int = 0
    max_command_line_bytes: int = 0
    last_payload_bytes: int = 0
    last_command_line_bytes: int = 0
    last_failed_payload_bytes: int | None = None
    last_failed_command_line_bytes: int | None = None
    max_success_payload_bytes: int = 0
    max_success_command_line_bytes: int = 0
    disconnected: bool = False
    disconnect_reason: str | None = None
    last_command_error: str | None = None


@dataclass
class TestRecord:
    """单次测试记录"""
    timestamp: str
    test_type: str      # burst / sweep / auto
    parameters: dict
    result: str         # success / failed / partial
    actual_packets: int
    duration: float
    pps: float
    error_msg: str | None = None
    details: list | None = None


class InteractiveWSTester:
    def __init__(self, host: str, port: int, log_dir: str = "test_logs"):
        self.host = host
        self.port = port
        self.client: WebSocketServerProtocol | None = None
        self.stats = SessionStats()
        self._client_connected = asyncio.Event()
        self._stop = asyncio.Event()

        # ---------- 新: requestId 跟踪 ----------
        self._pending_acks: Dict[str, asyncio.Event] = {}
        self._ack_results: Dict[str, bool] = {}

        # 日志与报告
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(exist_ok=True)
        self.session_start = datetime.now()
        self.test_records: list[TestRecord] = []

        self.log_file = self.log_dir / f"session_{self.session_start.strftime('%Y%m%d_%H%M%S')}.log"
        self._log(f"=== MCBE 压力测试会话开始 === {self.session_start.isoformat()}")
        self._log(f"服务地址: ws://{host}:{port}")

    # ==================== 日志与工具 ====================
    def _log(self, msg: str) -> None:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        with open(self.log_file, "a", encoding="utf-8") as f:
            f.write(f"[{timestamp}] {msg}\n")

    @staticmethod
    def _get_payload_metrics(payload: str) -> tuple[int, int]:
        payload_bytes = len(payload.encode("utf-8"))
        try:
            data = json.loads(payload)
            command_line = data.get("body", {}).get("commandLine", "")
        except (json.JSONDecodeError, AttributeError):
            command_line = ""
        return payload_bytes, len(command_line.encode("utf-8"))

    @staticmethod
    def _format_bytes(size: int) -> str:
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size < 1024.0:
                return f"{size:.1f} {unit}"
            size /= 1024.0
        return f"{size:.1f} TB"

    # ==================== 连接处理 ====================
    async def start(self) -> None:
        async with serve(self._handle_client, self.host, self.port, ping_interval=None):
            print(f"[tester] 服务已启动: ws://{self.host}:{self.port}")
            print(f"[tester] 日志文件: {self.log_file}")
            print("[tester] 等待 MCBE 客户端连接...\n")
            await self._run_console()

    async def _handle_client(self, ws: WebSocketServerProtocol) -> None:
        self.client = ws
        self.stats = SessionStats()
        self._client_connected.set()

        await ws.send(json.dumps({"Result": "true"}))
        await ws.send(MinecraftSubscribe.player_message().model_dump_json(exclude_none=True))
        self._log("客户端已连接")
        print("\n[tester] 客户端已连接，可执行发包测试。")

        try:
            async for msg in ws:
                if not isinstance(msg, str):
                    continue
                if len(msg) < 512:
                    print(f"[recv] {msg}")  # 短消息直接打印，长消息不刷屏

                try:
                    data = json.loads(msg)
                    header = data.get("header", {})
                    body = data.get("body", {})
                    purpose = header.get("messagePurpose")

                    if purpose == "commandResponse":
                        req_id = header.get("requestId")
                        # 匹配到我们的请求
                        if req_id and req_id in self._pending_acks:
                            success = body.get("statusCode", 0) >= 0
                            self._ack_results[req_id] = success
                            self._pending_acks[req_id].set()
                            if not success:
                                self.stats.last_command_error = body.get(
                                    "statusMessage", "未知命令错误")
                                self._log(
                                    f"命令 {req_id} 失败: {self.stats.last_command_error}, "
                                    f"payload={self.stats.last_payload_bytes}B, "
                                    f"commandLine={self.stats.last_command_line_bytes}B"
                                )
                                print(
                                    f"[tester] ⚠️ 命令错误: {self.stats.last_command_error} "
                                    f"(payload={self.stats.last_payload_bytes}B, "
                                    f"commandLine={self.stats.last_command_line_bytes}B)"
                                )
                except (json.JSONDecodeError, TypeError):
                    pass
        except Exception as exc:
            self.stats.disconnected = True
            self.stats.disconnect_reason = str(exc)
            self._log(f"客户端断开: {exc}")
            print(f"\n[tester] 客户端断开: {exc}")
        finally:
            self.client = None
            self._client_connected.clear()

    # ==================== 命令发送与确认（新） ====================
    def _build_command_payload(self, text: str) -> tuple[str, str]:
        """
        构建命令 JSON，注入自定义 requestId。
        返回 (requestId, payload_json_str)
        """
        cmd = MinecraftCommand.create_tellraw(text)
        cmd_dict = cmd.model_dump(exclude_none=True)
        # 确保 header 存在
        if 'header' not in cmd_dict:
            cmd_dict['header'] = {}
        req_id = str(uuid.uuid4())
        cmd_dict['header']['requestId'] = req_id
        return req_id, json.dumps(cmd_dict)

    async def _send_and_wait_ack(self, text: str, timeout: float = 2.0) -> bool:
        """
        发送 tellraw 命令并等待服务器返回匹配的 commandResponse 且 statusCode >= 0。
        成功返回 True，失败或超时返回 False。
        """
        if not self.client:
            return False

        req_id, payload = self._build_command_payload(text)
        event = asyncio.Event()
        self._pending_acks[req_id] = event
        self._ack_results[req_id] = False  # 默认失败

        payload_bytes, command_line_bytes = self._get_payload_metrics(payload)
        self.stats.last_payload_bytes = payload_bytes
        self.stats.last_command_line_bytes = command_line_bytes
        self.stats.max_payload_bytes = max(self.stats.max_payload_bytes, payload_bytes)
        self.stats.max_command_line_bytes = max(self.stats.max_command_line_bytes, command_line_bytes)

        try:
            await self.client.send(payload)
            self.stats.sent_packets += 1
            self.stats.sent_bytes += payload_bytes
            self.stats.sent_command_line_bytes += command_line_bytes
        except Exception:
            self.stats.last_failed_payload_bytes = payload_bytes
            self.stats.last_failed_command_line_bytes = command_line_bytes
            self._pending_acks.pop(req_id, None)
            self._ack_results.pop(req_id, None)
            return False

        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
            success = self._ack_results.get(req_id, False)
            if success:
                self.stats.max_success_payload_bytes = max(self.stats.max_success_payload_bytes, payload_bytes)
                self.stats.max_success_command_line_bytes = max(self.stats.max_success_command_line_bytes, command_line_bytes)
            else:
                self.stats.last_failed_payload_bytes = payload_bytes
                self.stats.last_failed_command_line_bytes = command_line_bytes
            return success
        except asyncio.TimeoutError:
            self.stats.last_failed_payload_bytes = payload_bytes
            self.stats.last_failed_command_line_bytes = command_line_bytes
            self._log(
                f"命令 {req_id} 超时无响应 ({text[:50]}...), "
                f"payload={payload_bytes}B, commandLine={command_line_bytes}B"
            )
            return False
        finally:
            self._pending_acks.pop(req_id, None)
            self._ack_results.pop(req_id, None)

    # ==================== 交互命令 ====================
    async def _run_console(self) -> None:
        help_text = (
            "命令:\n"
            "  wait                              等待客户端连接\n"
            "  burst <count> <size> [gap_ms]     连续发包 (payload为X*size)\n"
            "  sweep <start> <step> <max> <size> [gap_ms]  扫描极限发包量\n"
            "  auto <start> <end> <step> [count] [gap_ms]   自动递增payload测试\n"
            "  stats                             查看当前统计\n"
            "  report                            生成测试报告\n"
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
                elif cmd == "auto":
                    await self._cmd_auto(parts)
                elif cmd == "stats":
                    self._print_stats()
                elif cmd == "report":
                    self._generate_report()
                elif cmd in {"quit", "exit"}:
                    self._log("用户请求退出")
                    self._generate_report()
                    self._stop.set()
                else:
                    print(help_text)
            except Exception as exc:
                self._log(f"命令执行错误: {exc}")
                print(f"[error] {exc}")

    async def _wait_client(self) -> None:
        if self.client:
            print("[tester] 客户端已连接")
            return
        print("[tester] 等待客户端连接...")
        await self._client_connected.wait()
        print("[tester] 已连接")

    # ==================== Burst 测试 ====================
    async def _cmd_burst(self, parts: list[str]) -> None:
        if len(parts) < 3:
            raise ValueError("burst <count> <size> [gap_ms]")
        count = int(parts[1])
        size = int(parts[2])
        gap_ms = int(parts[3]) if len(parts) >= 4 else 0
        await self._wait_client()

        self._log(f"开始 burst 测试: count={count}, size={size}, gap_ms={gap_ms}")
        start = time.perf_counter()
        sent = 0
        error_occurred = False

        for i in range(count):
            if not self.client:
                error_occurred = True
                print(f"\n[burst] 在第 {i} 包时连接断开")
                break

            # 发送 size 长度的 'X'
            success = await self._send_and_wait_ack("X" * size, timeout=1.0)
            if not success:
                error_occurred = True
                print(
                    f"\n[burst] 第 {i+1} 包失败，停止 "
                    f"(payload={self.stats.last_failed_payload_bytes}B, "
                    f"commandLine={self.stats.last_failed_command_line_bytes}B)"
                )
                break
            sent += 1
            if gap_ms > 0:
                await asyncio.sleep(gap_ms / 1000)

        elapsed = time.perf_counter() - start
        pps = sent / elapsed if elapsed > 0 else 0
        result = "failed" if error_occurred else "success"

        record = TestRecord(
            timestamp=datetime.now().isoformat(),
            test_type="burst",
            parameters={"count": count, "size": size, "gap_ms": gap_ms},
            result=result,
            actual_packets=sent,
            duration=elapsed,
            pps=pps,
            error_msg=self.stats.last_command_error if error_occurred else None
        )
        self.test_records.append(record)
        self._log(f"Burst 完成: {sent}/{count} 包, {elapsed:.2f}s, {pps:.1f} pkt/s, 结果: {result}")
        print(f"\n[burst] 完成: 实际发送 {sent}/{count} 包, 耗时 {elapsed:.2f}s, PPS {pps:.1f}")

    # ==================== Sweep 测试（略作修改，使用确认） ====================
    async def _cmd_sweep(self, parts: list[str]) -> None:
        if len(parts) < 5:
            raise ValueError("sweep <start> <step> <max> <size> [gap_ms]")
        start_count = int(parts[1])
        step = int(parts[2])
        max_count = int(parts[3])
        size = int(parts[4])
        gap_ms = int(parts[5]) if len(parts) >= 6 else 0

        await self._wait_client()
        self._log(f"开始 sweep 测试: start={start_count}, step={step}, max={max_count}, size={size}, gap_ms={gap_ms}")
        safe = 0
        sweep_results = []

        for count in range(start_count, max_count + 1, step):
            if not self.client:
                print("[sweep] 客户端已断开，停止扫描")
                break

            print(f"[sweep] 测试 {count} 包 / payload={size}B / gap={gap_ms}ms")
            round_start = time.perf_counter()
            success = True

            for _ in range(count):
                if not self.client:
                    success = False
                    break
                if not await self._send_and_wait_ack("X" * size, timeout=1.0):
                    success = False
                    break
                if gap_ms > 0:
                    await asyncio.sleep(gap_ms / 1000)

            await asyncio.sleep(1.0)
            round_elapsed = time.perf_counter() - round_start
            actual_sent = count if success else 0  # 简化统计

            if success:
                safe = count
                result = "success"
                print(f"[sweep] ✅ 通过 {count} 包 (耗时 {round_elapsed:.2f}s, commandLine最大成功={self.stats.max_success_command_line_bytes}B)")
                sweep_results.append({
                    "count": count,
                    "result": result,
                    "actual_sent": actual_sent,
                    "duration": round_elapsed,
                    "pps": count / round_elapsed if round_elapsed > 0 else 0,
                    "max_success_payload_bytes": self.stats.max_success_payload_bytes,
                    "max_success_command_line_bytes": self.stats.max_success_command_line_bytes,
                })
            else:
                reason = "连接断开" if not self.client else f"命令错误: {self.stats.last_command_error}"
                print(
                    f"[sweep] ❌ 在 {count} 包时触发失败 ({reason}, "
                    f"payload={self.stats.last_failed_payload_bytes}B, "
                    f"commandLine={self.stats.last_failed_command_line_bytes}B)"
                )
                sweep_results.append({
                    "count": count,
                    "result": "failed",
                    "actual_sent": 0,
                    "duration": round_elapsed,
                    "error": reason,
                    "failed_payload_bytes": self.stats.last_failed_payload_bytes,
                    "failed_command_line_bytes": self.stats.last_failed_command_line_bytes,
                })
                break

        record = TestRecord(
            timestamp=datetime.now().isoformat(),
            test_type="sweep",
            parameters={
                "start": start_count, "step": step, "max": max_count,
                "size": size, "gap_ms": gap_ms
            },
            result="success" if safe >= start_count else "failed",
            actual_packets=safe,
            duration=sum(r.get("duration", 0) for r in sweep_results),
            pps=0,
            error_msg=f"安全上限: {safe} 包" if safe > 0 else None,
            details=sweep_results
        )
        self.test_records.append(record)
        self._log(f"Sweep 完成: 安全上限={safe} 包")
        print(f"\n[sweep] 当前安全上限（粗略）: {safe} 包")

    # ==================== Auto 测试（重点修复） ====================
    async def _cmd_auto(self, parts: list[str]) -> None:
        if len(parts) < 4:
            raise ValueError("auto <start_size> <end_size> <step> [count] [gap_ms]")

        start_size = int(parts[1])
        end_size = int(parts[2])
        step = int(parts[3])
        count = int(parts[4]) if len(parts) >= 5 else 1
        gap_ms = int(parts[5]) if len(parts) >= 6 else 0

        if start_size < 1 or end_size < start_size or step < 1:
            raise ValueError("参数范围错误")

        await self._wait_client()
        sizes = list(range(start_size, end_size + 1, step))
        total_packets = len(sizes) * count

        self._log(f"开始 auto 测试: {start_size}B→{end_size}B (步长{step}B), "
                  f"每大小{count}包, 间隔{gap_ms}ms, 总计{total_packets}包")
        print(f"[auto] 开始递增测试: {start_size}B → {end_size}B")
        print(f"[auto] 步长: {step}B, 每级发包: {count}, 间隔: {gap_ms}ms")
        print(f"[auto] 总计 {len(sizes)} 个级别, {total_packets} 个包")
        print("-" * 60)

        start_time = time.perf_counter()
        auto_results = []
        failed_size = None
        first_failure = None

        for idx, size in enumerate(sizes, 1):
            if not self.client:
                print("\n[auto] 客户端断开，终止测试")
                break

            progress = (idx / len(sizes)) * 100
            print(f"\r[auto] 进度: {progress:.1f}% | 测试 {size}B ({idx}/{len(sizes)})", end="", flush=True)

            size_success = True
            actual_sent = 0
            size_start = time.perf_counter()

            for seq in range(1, count + 1):
                # 构造严格长度为 size 的文本
                prefix = f"LOAD_TEST:SIZE_{size}_SEQ_{seq}"
                if len(prefix) >= size:
                    text = prefix[:size]
                else:
                    text = prefix + "X" * (size - len(prefix))

                if not await self._send_and_wait_ack(text, timeout=1.5):
                    size_success = False
                    break
                actual_sent += 1
                if gap_ms > 0 and seq < count:
                    await asyncio.sleep(gap_ms / 1000)

            size_elapsed = time.perf_counter() - size_start

            if size_success:
                auto_results.append({
                    "size": size,
                    "success": True,
                    "sent": actual_sent,
                    "duration": size_elapsed,
                    "pps": count / size_elapsed if size_elapsed > 0 else 0,
                    "payload_bytes": self.stats.last_payload_bytes,
                    "command_line_bytes": self.stats.last_command_line_bytes,
                })
                if idx % 10 == 0 or idx == len(sizes):
                    print(f"\n[auto] ✅ {size}B: {actual_sent}/{count} 包, "
                          f"{size_elapsed:.2f}s, {count/size_elapsed:.1f} pkt/s, "
                          f"payload={self.stats.last_payload_bytes}B, "
                          f"commandLine={self.stats.last_command_line_bytes}B")
            else:
                failed_size = size
                if first_failure is None:
                    first_failure = {
                        "size": size,
                        "sent": actual_sent,
                        "error": self.stats.last_command_error or "连接断开",
                        "payload_bytes": self.stats.last_failed_payload_bytes,
                        "command_line_bytes": self.stats.last_failed_command_line_bytes,
                    }
                auto_results.append({
                    "size": size,
                    "success": False,
                    "sent": actual_sent,
                    "duration": size_elapsed,
                    "error": self.stats.last_command_error or "连接断开",
                    "payload_bytes": self.stats.last_failed_payload_bytes,
                    "command_line_bytes": self.stats.last_failed_command_line_bytes,
                })
                print(f"\n[auto] ❌ {size}B: 失败 (已发送 {actual_sent}/{count} 包)")
                print(
                    f"[auto] 错误: {self.stats.last_command_error or '连接断开'} "
                    f"(payload={self.stats.last_failed_payload_bytes}B, "
                    f"commandLine={self.stats.last_failed_command_line_bytes}B)"
                )
                break  # 一旦失败立即停止递增

            if gap_ms == 0 and idx % 100 == 0:
                await asyncio.sleep(0.1)

        total_elapsed = time.perf_counter() - start_time
        print("\n" + "-" * 60)

        successful_count = sum(1 for r in auto_results if r["success"])
        failed_count = sum(1 for r in auto_results if not r["success"])
        total_sent = sum(r["sent"] for r in auto_results)
        max_successful_size = max((r["size"] for r in auto_results if r["success"]), default=0)

        print(f"[auto] 测试完成:")
        print(f"  - 成功: {successful_count}/{len(sizes)} 个级别")
        print(f"  - 失败: {failed_count} 个级别")
        print(f"  - 实际发送: {total_sent}/{total_packets} 包")
        print(f"  - 总耗时: {total_elapsed:.2f}s")
        print(f"  - 最大成功的payload: {max_successful_size}B")
        print(f"  - 最大成功WS包: {self.stats.max_success_payload_bytes}B")
        print(f"  - 最大成功commandLine: {self.stats.max_success_command_line_bytes}B")
        if first_failure:
            print(
                f"  - 首次失败: {first_failure['size']}B ({first_failure['error']}, "
                f"payload={first_failure['payload_bytes']}B, "
                f"commandLine={first_failure['command_line_bytes']}B)"
            )

        record = TestRecord(
            timestamp=datetime.now().isoformat(),
            test_type="auto",
            parameters={
                "start_size": start_size,
                "end_size": end_size,
                "step": step,
                "count_per_size": count,
                "gap_ms": gap_ms,
                "total_levels": len(sizes)
            },
            result="success" if not failed_size else ("partial" if successful_count > 0 else "failed"),
            actual_packets=total_sent,
            duration=total_elapsed,
            pps=total_sent / total_elapsed if total_elapsed > 0 else 0,
            error_msg=(
                f"最大成功: {max_successful_size}B, "
                f"最大成功WS包: {self.stats.max_success_payload_bytes}B, "
                f"最大成功commandLine: {self.stats.max_success_command_line_bytes}B"
            ) + (
                f", 首次失败: {first_failure['size']}B, "
                f"失败WS包: {first_failure['payload_bytes']}B, "
                f"失败commandLine: {first_failure['command_line_bytes']}B"
                if first_failure else ""
            ),
            details=auto_results
        )
        self.test_records.append(record)

        self._log(f"Auto 完成: 成功{successful_count}级, 失败{failed_count}级, "
                  f"最大{max_successful_size}B, 发送{total_sent}包")

        if len(auto_results) > 0:
            self._print_auto_summary(auto_results)

    def _print_auto_summary(self, results: list) -> None:
        successful = [r for r in results if r["success"]]
        if len(successful) > 1:
            pps_values = [r.get("pps", 0) for r in successful]
            print(f"\n[auto] 性能趋势:")
            print(f"  - 平均PPS: {sum(pps_values)/len(pps_values):.1f}")
            print(f"  - 最大PPS: {max(pps_values):.1f} (payload={successful[pps_values.index(max(pps_values))]['size']}B)")
            print(f"  - 最小PPS: {min(pps_values):.1f} (payload={successful[pps_values.index(min(pps_values))]['size']}B)")

    # ==================== 报告 ====================
    def _generate_report(self) -> None:
        report_file = self.log_dir / f"report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
        with open(report_file, "w", encoding="utf-8") as f:
            f.write(f"# MCBE WebSocket 压力测试报告\n\n")
            f.write(f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"**会话开始**: {self.session_start.strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"**服务地址**: ws://{self.host}:{self.port}\n\n")

            f.write("## 总体统计\n\n")
            f.write(f"- 测试总次数: {len(self.test_records)}\n")
            f.write(f"- 总发包数: {self.stats.sent_packets}\n")
            f.write(f"- 总数据量: {self._format_bytes(self.stats.sent_bytes)}\n")
            f.write(f"- 总 commandLine 数据量: {self._format_bytes(self.stats.sent_command_line_bytes)}\n")
            f.write(f"- 最大已发送 WS payload: {self.stats.max_payload_bytes} B\n")
            f.write(f"- 最大已发送 commandLine: {self.stats.max_command_line_bytes} B\n")
            f.write(f"- 最大成功 WS payload: {self.stats.max_success_payload_bytes} B\n")
            f.write(f"- 最大成功 commandLine: {self.stats.max_success_command_line_bytes} B\n")
            if self.stats.last_failed_command_line_bytes is not None:
                f.write(f"- 最近失败 WS payload: {self.stats.last_failed_payload_bytes} B\n")
                f.write(f"- 最近失败 commandLine: {self.stats.last_failed_command_line_bytes} B\n")
            f.write(f"- 客户端状态: {'已断开' if self.stats.disconnected else '已连接' if self.client else '未连接'}\n")
            if self.stats.disconnect_reason:
                f.write(f"- 断开原因: {self.stats.disconnect_reason}\n")
            f.write("\n")

            burst_tests = [r for r in self.test_records if r.test_type == "burst"]
            sweep_tests = [r for r in self.test_records if r.test_type == "sweep"]
            auto_tests = [r for r in self.test_records if r.test_type == "auto"]

            if burst_tests:
                f.write("## Burst 测试结果\n\n")
                f.write("| # | 时间 | 目标包数 | Payload | 间隔 | 实际发包 | 耗时 | PPS | 结果 |\n")
                f.write("|---|------|---------|---------|------|---------|------|-----|------|\n")
                for i, test in enumerate(burst_tests, 1):
                    params = test.parameters
                    f.write(f"| {i} | {test.timestamp[11:19]} | {params['count']} | "
                            f"{self._format_bytes(params['size'])} | {params['gap_ms']}ms | "
                            f"{test.actual_packets} | {test.duration:.2f}s | "
                            f"{test.pps:.1f} | {'✅' if test.result == 'success' else '❌'} |\n")
                f.write("\n")

            if sweep_tests:
                f.write("## Sweep 测试结果\n\n")
                for i, test in enumerate(sweep_tests, 1):
                    params = test.parameters
                    f.write(f"### Sweep #{i}: Payload {self._format_bytes(params['size'])}\n\n")
                    f.write(f"- 扫描范围: {params['start']} → {params['max']} (步长 {params['step']})\n")
                    f.write(f"- 发包间隔: {params['gap_ms']}ms\n")
                    f.write(f"- **安全上限: {test.actual_packets} 包**\n")
                    if test.details:
                        f.write("\n详细数据:\n")
                        f.write("| 包数 | 结果 | 实际发送 | 耗时 | PPS | 最大成功WS包 | 最大成功commandLine | 失败WS包 | 失败commandLine |\n")
                        f.write("|------|------|----------|------|-----|-------------|-------------------|----------|----------------|\n")
                        for r in test.details:
                            f.write(f"| {r['count']} | {r['result']} | "
                                    f"{r['actual_sent']} | {r['duration']:.2f}s | "
                                    f"{r.get('pps', 0):.1f} | "
                                    f"{r.get('max_success_payload_bytes', '')} | "
                                    f"{r.get('max_success_command_line_bytes', '')} | "
                                    f"{r.get('failed_payload_bytes', '')} | "
                                    f"{r.get('failed_command_line_bytes', '')} |\n")
                    f.write("\n")

            if auto_tests:
                f.write("## 自动递增测试结果\n\n")
                for i, test in enumerate(auto_tests, 1):
                    params = test.parameters
                    f.write(f"### Auto #{i}: {self._format_bytes(params['start_size'])} → "
                            f"{self._format_bytes(params['end_size'])}\n\n")
                    f.write(f"- 步长: {self._format_bytes(params['step'])}\n")
                    f.write(f"- 每级发包: {params['count_per_size']} 包\n")
                    f.write(f"- 发包间隔: {params['gap_ms']}ms\n")
                    f.write(f"- 测试级别: {params['total_levels']}\n")
                    f.write(f"- 实际发送: {test.actual_packets} 包\n")
                    f.write(f"- 总耗时: {test.duration:.2f}s\n")
                    f.write(f"- **最大成功Payload: {test.error_msg}**\n")
                    if test.details:
                        f.write("\n详细数据 (前20和后20条):\n")
                        f.write("| Payload | 结果 | 发送 | 耗时 | PPS | WS包字节 | commandLine字节 |\n")
                        f.write("|---------|------|------|------|-----|----------|-----------------|\n")
                        details_to_show = test.details[:20] + test.details[-20:] if len(test.details) > 40 else test.details
                        if len(test.details) > 40:
                            f.write("| ... | ... | ... | ... | ... | ... | ... |\n")
                        for d in details_to_show:
                            icon = "✅" if d["success"] else "❌"
                            f.write(f"| {self._format_bytes(d['size'])} | {icon} | "
                                    f"{d['sent']} | {d['duration']:.3f}s | {d.get('pps', 0):.1f} | "
                                    f"{d.get('payload_bytes', '')} | {d.get('command_line_bytes', '')} |\n")
                    f.write("\n")

            f.write("## 性能汇总\n\n")
            if auto_tests:
                for test in auto_tests:
                    if test.details:
                        successful = [r for r in test.details if r["success"]]
                        if successful:
                            max_pps = max(successful, key=lambda x: x.get("pps", 0))
                            f.write(f"- **最大PPS**: {max_pps.get('pps', 0):.1f} pkt/s "
                                    f"(payload: {self._format_bytes(max_pps['size'])})\n")
                            f.write(f"- **最大Payload**: {self._format_bytes(max(r['size'] for r in successful))}\n")
            if burst_tests:
                successful_bursts = [t for t in burst_tests if t.result == "success"]
                if successful_bursts:
                    f.write(f"- **峰值吞吐量**: {max(t.pps for t in successful_bursts):.1f} pkt/s\n")

        self._log(f"报告已生成: {report_file}")
        print(f"\n[report] 报告已生成: {report_file}")
        print(f"[report] 日志文件: {self.log_file}")
        self._print_summary()

    def _print_summary(self) -> None:
        print("\n" + "="*50)
        print("测试摘要")
        print("="*50)
        print(f"测试次数: {len(self.test_records)}")
        print(f"总发包: {self.stats.sent_packets} / 总数据: {self._format_bytes(self.stats.sent_bytes)}")
        auto_tests = [t for t in self.test_records if t.test_type == "auto"]
        if auto_tests:
            for t in auto_tests:
                print(f"Auto [{self._format_bytes(t.parameters['start_size'])}→"
                      f"{self._format_bytes(t.parameters['end_size'])}]: {t.error_msg}")
        print(f"报告文件: {self.log_dir}")
        print("="*50 + "\n")

    def _print_stats(self) -> None:
        print(f"[stats] sent_packets={self.stats.sent_packets}, sent_bytes={self.stats.sent_bytes}, "
              f"command_line_bytes={self.stats.sent_command_line_bytes}, "
              f"max_payload_bytes={self.stats.max_payload_bytes}, "
              f"max_command_line_bytes={self.stats.max_command_line_bytes}, "
              f"max_success_payload_bytes={self.stats.max_success_payload_bytes}, "
              f"max_success_command_line_bytes={self.stats.max_success_command_line_bytes}, "
              f"last_failed_payload_bytes={self.stats.last_failed_payload_bytes}, "
              f"last_failed_command_line_bytes={self.stats.last_failed_command_line_bytes}, "
              f"connected={self.client is not None}, disconnected={self.stats.disconnected}, "
              f"reason={self.stats.disconnect_reason}, last_cmd_error={self.stats.last_command_error}")
        print(f"[stats] 已记录 {len(self.test_records)} 次测试")


def main() -> None:
    parser = argparse.ArgumentParser(description="MCBE WebSocket 压测工具（修复版）")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8081)
    parser.add_argument("--log-dir", default="test_logs", help="日志和报告存储目录")
    args = parser.parse_args()
    asyncio.run(InteractiveWSTester(args.host, args.port, args.log_dir).start())


if __name__ == "__main__":
    main()