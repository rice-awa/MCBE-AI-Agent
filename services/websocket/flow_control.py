"""统一流控中间件：将所有出站长文本分片为安全大小的 Minecraft 命令。"""

import json
import re
import uuid

from models.minecraft import MinecraftCommand

# 句子分隔符：中英文句号、问号、感叹号、换行
_SENTENCE_DELIMITER_RE = re.compile(r"([。！？.!?\n])")


class FlowControlMiddleware:
    """统一流控中间件：将所有出站长文本分片为安全大小的 Minecraft 命令。"""

    DEFAULT_MAX_CONTENT_LENGTH = 400

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

        每条 JSON 的 commandLine 中的 text 内容不超过 max_length。
        """
        max_len = cls._get_max_length(max_length)
        text_parts = cls._split_text(message, max_len)

        payloads: list[str] = []
        for part in text_parts:
            command = MinecraftCommand.create_tellraw(part, color=color)
            payloads.append(command.model_dump_json(exclude_none=True))
        return payloads

    @classmethod
    def chunk_scriptevent(
        cls,
        content: str,
        message_id: str = "server:data",
        max_length: int | None = None,
    ) -> list[str]:
        """将长 scriptevent payload 分片为多个 commandRequest JSON 字符串列表。

        每条 commandLine 中的 content 部分不超过 max_length。
        """
        max_len = cls._get_max_length(max_length)
        text_parts = cls._split_text(content, max_len)

        payloads: list[str] = []
        for part in text_parts:
            command = MinecraftCommand.create_scriptevent(part, message_id)
            payloads.append(command.model_dump_json(exclude_none=True))
        return payloads

    @classmethod
    def chunk_raw_command(
        cls, command: str, max_length: int | None = None
    ) -> list[str]:
        """将超长原始命令分片。通常无需处理，保留接口一致性。"""
        max_len = cls._get_max_length(max_length)
        if len(command) <= max_len:
            cmd = MinecraftCommand.create_raw(command)
            return [cmd.model_dump_json(exclude_none=True)]

        # 对于原始命令，简单按长度截断（实际场景极少触发）
        parts = cls._split_text(command, max_len)
        payloads: list[str] = []
        for part in parts:
            cmd = MinecraftCommand.create_raw(part)
            payloads.append(cmd.model_dump_json(exclude_none=True))
        return payloads

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
        """
        max_len = cls._get_max_length(max_length)
        msg_id = f"resp-{uuid.uuid4().hex[:8]}"

        text_parts = cls._split_text(text, max_len)
        total = len(text_parts) if text_parts else 1
        safe_parts = text_parts if text_parts else [""]

        payloads: list[str] = []
        for idx, content in enumerate(safe_parts, start=1):
            payload = {
                "id": msg_id,
                "i": idx,
                "n": total,
                "p": player_name,
                "r": role,
                "c": content,
            }
            command_line = (
                f"scriptevent mcbeai:ai_resp {json.dumps(payload, ensure_ascii=False)}"
            )
            cmd = MinecraftCommand.create_raw(command_line)
            payloads.append(cmd.model_dump_json(exclude_none=True))

        return payloads

    @staticmethod
    def _split_text(text: str, max_length: int) -> list[str]:
        """语义分片核心：优先按句子分割，超长句按字符截断。"""
        if not text:
            return [""]

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
