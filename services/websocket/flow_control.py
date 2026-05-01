"""统一流控中间件：将所有出站长文本分片为安全大小的 Minecraft 命令。"""

import json
import re
import uuid

from models.minecraft import MinecraftCommand

# 句子分隔符：中英文句号、问号、感叹号、换行
_SENTENCE_DELIMITER_RE = re.compile(r"([。！？.!?\n])")

# MCBE commandLine 实测安全字节上限
# 数据来源: 自动递增压力测试 (200B→512B, step 1B, interval 0ms, 222 包)
#   最大成功 commandLine: 461 B
#   首次失败 commandLine: 462 B
# 取 461 为硬上限；超过此值 server 会拒绝 commandRequest
_COMMAND_LINE_BYTE_BUDGET = 461

# 各类命令的包装开销（实测 + 余量），用于推导文本载荷的字节上限
# 实测值（命令空文本时的 commandLine 字节数）:
#   tellraw + §a: 39 B
#   scriptevent + 'server:data': 24 B
#   scriptevent + 'mcbeai:ai_resp' + 空 ai_resp JSON 包装: 107 B
# 各取 +20 B 余量以容纳玩家名、转义、色码切换等动态字段
_WRAPPER_OVERHEAD_TELLRAW = 60
_WRAPPER_OVERHEAD_SCRIPTEVENT = 50
_WRAPPER_OVERHEAD_AI_RESPONSE = 130


class FlowControlMiddleware:
    """统一流控中间件：将所有出站长文本分片为安全大小的 Minecraft 命令。"""

    DEFAULT_MAX_CONTENT_LENGTH = 400
    DEFAULT_SENTENCE_MODE = True

    @classmethod
    def configure(
        cls,
        max_content_length: int | None = None,
        sentence_mode: bool | None = None,
    ) -> None:
        """从应用启动序注入运行时默认值。

        允许 .env 中的 MAX_CHUNK_CONTENT_LENGTH / CHUNK_SENTENCE_MODE 真正生效。
        未传入或非法值时保留现有默认。
        """
        if max_content_length is not None and max_content_length > 0:
            cls.DEFAULT_MAX_CONTENT_LENGTH = max_content_length
        if sentence_mode is not None:
            cls.DEFAULT_SENTENCE_MODE = bool(sentence_mode)

    @classmethod
    def _get_max_length(cls, max_length: int | None) -> int:
        """获取有效的最大长度值。"""
        if max_length is None or max_length <= 0:
            return cls.DEFAULT_MAX_CONTENT_LENGTH
        return max_length

    @classmethod
    def chunk_tellraw(
        cls, message: str, color: str = "§a", max_length: int | None = None
    ) -> list[str]:
        """将长 tellraw 消息分片为多个 commandRequest JSON 字符串列表。

        空文本约定返回 []，由调用方决定是否仍要发送。
        每条 JSON 的 commandLine 中的 text 内容不超过 max_length 字符，
        且 commandLine 字节数 ≤ 461 B（MCBE 实测安全上限）。
        """
        if not message:
            return []

        max_len = cls._get_max_length(max_length)
        byte_budget = _COMMAND_LINE_BYTE_BUDGET - _WRAPPER_OVERHEAD_TELLRAW
        text_parts = cls._split_text(message, max_len, byte_budget)

        payloads: list[str] = []
        for part in text_parts:
            command = MinecraftCommand.create_tellraw(part, color=color)
            payload = command.model_dump_json(exclude_none=True)
            cls._assert_byte_safe(payload)
            payloads.append(payload)
        return payloads

    @classmethod
    def chunk_scriptevent(
        cls,
        content: str,
        message_id: str = "server:data",
        max_length: int | None = None,
    ) -> list[str]:
        """将长 scriptevent payload 分片为多个 commandRequest JSON 字符串列表。

        空文本约定返回 []，由调用方决定是否仍要发送。
        每条 commandLine 中的 content 部分不超过 max_length 字符，
        且 commandLine 字节数 ≤ 461 B（MCBE 实测安全上限）。
        """
        if not content:
            return []

        max_len = cls._get_max_length(max_length)
        byte_budget = _COMMAND_LINE_BYTE_BUDGET - _WRAPPER_OVERHEAD_SCRIPTEVENT
        text_parts = cls._split_text(content, max_len, byte_budget)

        payloads: list[str] = []
        for part in text_parts:
            command = MinecraftCommand.create_scriptevent(part, message_id)
            payload = command.model_dump_json(exclude_none=True)
            cls._assert_byte_safe(payload)
            payloads.append(payload)
        return payloads

    @classmethod
    def chunk_raw_command(
        cls, command: str, max_length: int | None = None
    ) -> list[str]:
        """包装原始命令为 commandRequest JSON 列表（始终返回单元素）。

        原始命令不能在动词之外的位置被截断，否则后续分片会成为非法命令。
        因此此方法**不进行分片**：长度超限时抛 ValueError，由调用方决策。
        """
        max_len = cls._get_max_length(max_length)
        if len(command) > max_len:
            raise ValueError(
                f"raw command too long ({len(command)} > {max_len}); "
                "cannot be safely chunked — split at the caller level"
            )
        cmd = MinecraftCommand.create_raw(command)
        return [cmd.model_dump_json(exclude_none=True)]

    @classmethod
    def chunk_ai_response(
        cls,
        player_name: str,
        role: str,
        text: str,
        max_length: int | None = None,
    ) -> list[str]:
        """将 AI 响应编码为 scriptevent 分片命令列表。

        每个分片格式: scriptevent mcbeai:ai_resp {JSON}
        JSON 载荷: {"id":"...","i":1,"n":3,"p":"Steve","r":"assistant","c":"..."}
        空文本仍发送 1 条空载荷以保留 total ≥ 1 的契约。
        commandLine 字节数 ≤ 461 B（MCBE 实测安全上限）。
        """
        max_len = cls._get_max_length(max_length)
        msg_id = f"resp-{uuid.uuid4().hex[:8]}"
        byte_budget = _COMMAND_LINE_BYTE_BUDGET - _WRAPPER_OVERHEAD_AI_RESPONSE

        text_parts = cls._split_text(text, max_len, byte_budget) if text else [""]
        if not text_parts:
            text_parts = [""]
        total = len(text_parts)

        payloads: list[str] = []
        for idx, content in enumerate(text_parts, start=1):
            inner = {
                "id": msg_id,
                "i": idx,
                "n": total,
                "p": player_name,
                "r": role,
                "c": content,
            }
            cmd = MinecraftCommand.create_scriptevent(
                json.dumps(inner, ensure_ascii=False),
                "mcbeai:ai_resp",
            )
            payload = cmd.model_dump_json(exclude_none=True)
            cls._assert_byte_safe(payload)
            payloads.append(payload)

        return payloads

    @classmethod
    def _split_text(
        cls,
        text: str,
        max_length: int,
        byte_budget: int | None = None,
    ) -> list[str]:
        """语义分片核心：按句子分片 + 字符上限 + 字节上限三重约束。

        max_length: 单分片字符数上限（向后兼容入参语义）。
        byte_budget: 单分片 UTF-8 字节数上限。默认按 tellraw 包装开销推导，
            chunk_* 方法应显式传入对应场景的预算以获得最大有效载荷。
        sentence_mode=False 时跳过语义合并，仍受双重约束。
        """
        if byte_budget is None:
            byte_budget = _COMMAND_LINE_BYTE_BUDGET - _WRAPPER_OVERHEAD_TELLRAW

        if not text:
            return [""]

        if not cls.DEFAULT_SENTENCE_MODE:
            return cls._chunk_by_limits(text, max_length, byte_budget)

        # 按分隔符拆分，保留分隔符
        parts = _SENTENCE_DELIMITER_RE.split(text)

        # 将文本段与后续分隔符重新组合
        sentences: list[str] = []
        i = 0
        while i < len(parts):
            segment = parts[i]
            delimiter = (
                parts[i + 1]
                if i + 1 < len(parts)
                and _SENTENCE_DELIMITER_RE.match(parts[i + 1])
                else ""
            )
            combined = segment + delimiter if delimiter else segment
            i += 2 if delimiter else 1
            if combined:
                sentences.append(combined)

        if not sentences:
            return [""]

        # 合并短句：受字符上限 max_length 与字节预算 byte_budget 双重约束
        merged: list[str] = []
        buffer = ""
        for sentence in sentences:
            tentative = buffer + sentence
            if (
                len(tentative) <= max_length
                and len(tentative.encode("utf-8")) <= byte_budget
            ):
                buffer = tentative
                continue

            if buffer:
                merged.append(buffer)

            # 单句若超出任一限制，按双重约束切碎
            if (
                len(sentence) > max_length
                or len(sentence.encode("utf-8")) > byte_budget
            ):
                merged.extend(cls._chunk_by_limits(sentence, max_length, byte_budget))
                buffer = ""
            else:
                buffer = sentence
        if buffer:
            merged.append(buffer)

        return merged if merged else [""]

    @staticmethod
    def _chunk_by_limits(
        text: str, max_chars: int, max_bytes: int
    ) -> list[str]:
        """按字符上限和字节上限将文本切分，保证不切坏 UTF-8 多字节字符。"""
        chunks: list[str] = []
        current: list[str] = []
        current_chars = 0
        current_bytes = 0

        for ch in text:
            ch_bytes = len(ch.encode("utf-8"))
            if (
                current_chars + 1 > max_chars
                or current_bytes + ch_bytes > max_bytes
            ):
                if current:
                    chunks.append("".join(current))
                current = [ch]
                current_chars = 1
                current_bytes = ch_bytes
            else:
                current.append(ch)
                current_chars += 1
                current_bytes += ch_bytes

        if current:
            chunks.append("".join(current))

        return chunks if chunks else [""]

    @staticmethod
    def _assert_byte_safe(payload: str) -> None:
        """字节级兜底：分片后 commandLine 字节数必须 ≤ 实测安全预算。

        正常路径下 _split_text 已在源头保证字节安全；此函数作为防御性校验，
        若调用方绕过 chunk_* 直接构造命令时仍能尽早暴露问题。
        """
        try:
            data = json.loads(payload)
            command_line = data.get("body", {}).get("commandLine", "")
        except (json.JSONDecodeError, AttributeError):
            return

        byte_len = len(command_line.encode("utf-8"))
        if byte_len > _COMMAND_LINE_BYTE_BUDGET:
            raise ValueError(
                f"chunked commandLine exceeds byte budget "
                f"({byte_len} > {_COMMAND_LINE_BYTE_BUDGET}); "
                "this indicates a bug in _split_text or wrapper overhead estimate"
            )
