"""统一流控中间件：将所有出站长文本分片为安全大小的 Minecraft 命令。"""

import json
import re
import uuid

from models.minecraft import MinecraftCommand

# 句子分隔符：中英文句号、问号、感叹号、换行
_SENTENCE_DELIMITER_RE = re.compile(r"([。！？.!?\n])")

# MCBE commandLine 实测安全字节上限（含 tellraw/scriptevent 包装），超过会被 server 拒绝
_COMMAND_LINE_BYTE_BUDGET = 1800


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
        每条 JSON 的 commandLine 中的 text 内容不超过 max_length。
        """
        if not message:
            return []

        max_len = cls._get_max_length(max_length)
        text_parts = cls._split_text(message, max_len)

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
        每条 commandLine 中的 content 部分不超过 max_length。
        """
        if not content:
            return []

        max_len = cls._get_max_length(max_length)
        text_parts = cls._split_text(content, max_len)

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
        """
        max_len = cls._get_max_length(max_length)
        msg_id = f"resp-{uuid.uuid4().hex[:8]}"

        text_parts = cls._split_text(text, max_len) if text else [""]
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
    def _split_text(cls, text: str, max_length: int) -> list[str]:
        """语义分片核心：可选按句子分片，超长句按字符截断。

        sentence_mode=False 时跳过语义合并，纯按 max_length 等长截断。
        """
        if not text:
            return [""]

        if not cls.DEFAULT_SENTENCE_MODE:
            return [
                text[i : i + max_length]
                for i in range(0, len(text), max_length)
            ]

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

        # 合并短句（不超过 max_length），超长句截断
        merged: list[str] = []
        buffer = ""
        for sentence in sentences:
            if len(buffer) + len(sentence) <= max_length:
                buffer += sentence
            else:
                if buffer:
                    merged.append(buffer)
                if len(sentence) > max_length:
                    for j in range(0, len(sentence), max_length):
                        merged.append(sentence[j : j + max_length])
                    buffer = ""
                else:
                    buffer = sentence
        if buffer:
            merged.append(buffer)

        return merged if merged else [""]

    @staticmethod
    def _assert_byte_safe(payload: str) -> None:
        """字节级兜底：分片后 commandLine 字节数必须 ≤ 安全预算。

        中文/emoji 多字节字符可能导致字符数 ≤ max_length 但字节数超标，
        在此提早抛错而不是让 MCBE 拒绝命令导致静默丢消息。
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
                "lower MAX_CHUNK_CONTENT_LENGTH for multibyte-heavy text"
            )
