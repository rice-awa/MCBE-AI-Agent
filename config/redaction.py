"""共享日志/审计脱敏与长度限制 helper。

审计记录与 structlog 原始日志统一使用本模块，避免凭据、完整正文或超长参数泄漏。
"""

from __future__ import annotations

import ast
import json
import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# 默认长度预算（字符）
DEFAULT_TEXT_MAX = 200
DEFAULT_BODY_MAX = 2_000
DEFAULT_EXCEPTION_MAX = 300
DEFAULT_PARAM_MAX = 120

_SENSITIVE_KEYS = frozenset({
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "authorization",
    "auth",
    "cookie",
    "set-cookie",
    "x-api-key",
    "access_key",
    "private_key",
    "credential",
})
_SENSITIVE_KEY_PATTERN = "|".join(
    r"[-_]?".join(re.escape(part) for part in key.replace("-", "_").split("_"))
    for key in _SENSITIVE_KEYS
)

_SENSITIVE_QUERY_KEYS = frozenset(
    {
        "password",
        "passwd",
        "secret",
        "token",
        "api_key",
        "apikey",
        "access_token",
        "refresh_token",
        "key",
        "auth",
        "authorization",
    }
)


def is_sensitive_key(key: Any) -> bool:
    text = str(key).lower().replace("-", "_")
    return any(fragment.replace("-", "_") in text for fragment in _SENSITIVE_KEYS)


def truncate_for_log(
    value: Any,
    max_length: int = DEFAULT_TEXT_MAX,
    *,
    suffix: str = "...",
) -> str:
    """将任意值转为受限长度的日志安全字符串。"""
    if value is None:
        return ""
    text = value if isinstance(value, str) else str(value)
    if len(text) <= max_length:
        return text
    keep = max(0, max_length - len(suffix))
    return text[:keep] + suffix


def redact_text(value: Any, max_length: int = DEFAULT_TEXT_MAX) -> str | None:
    if value is None:
        return None
    return truncate_for_log(value, max_length)


def redact_exception(exc: BaseException | str | None, max_length: int = DEFAULT_EXCEPTION_MAX) -> str | None:
    if exc is None:
        return None
    if isinstance(exc, BaseException):
        prefix = f"{type(exc).__name__}: "
        raw_text = str(exc)
    else:
        prefix = ""
        raw_text = str(exc)
    try:
        parsed = json.loads(raw_text)
    except (TypeError, ValueError):
        try:
            parsed = ast.literal_eval(raw_text)
        except (TypeError, ValueError, SyntaxError):
            text = prefix + _redact_exception_text(raw_text)
        else:
            text = prefix + _stable_redacted_exception_value(parsed, max_length)
    else:
        text = prefix + _stable_redacted_exception_value(parsed, max_length)
    return truncate_for_log(text, max_length)


def _stable_redacted_exception_value(value: Any, max_length: int) -> str:
    return json.dumps(
        _redact_exception_value(value, max_length),
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )


def _redact_exception_value(value: Any, max_length: int) -> Any:
    if isinstance(value, str):
        return truncate_for_log(_redact_exception_text(value), max_length)
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]" if is_sensitive_key(key)
            else _redact_exception_value(item, max_length)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [_redact_exception_value(item, max_length) for item in value]
    if value is None or isinstance(value, (int, float, bool)):
        return value
    return truncate_for_log(_redact_exception_text(str(value)), max_length)


def _redact_exception_text(text: str) -> str:
    text = re.sub(
        rf"(?i)(['\"]?(?:{_SENSITIVE_KEY_PATTERN})['\"]?\s*:\s*)"
        r"(['\"])(?:bearer\s+)?[^'\"]*\2",
        r"\1\2[REDACTED]\2",
        text,
    )
    text = re.sub(
        rf"(?i)\b({_SENSITIVE_KEY_PATTERN})\b\s*[=:]\s*"
        r"(?:bearer\s+)?[^,\s)\]}]+",
        r"\1=[REDACTED]",
        text,
    )
    text = re.sub(r"(?i)\bbearer\s+[^,\s)\]}]+", "Bearer [REDACTED]", text)
    return text


def redact_mapping(
    data: dict[Any, Any] | None,
    *,
    max_length: int = DEFAULT_PARAM_MAX,
    sensitive_keys: frozenset[str] | set[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """脱敏并截断 dict；敏感 key 替换为 [REDACTED]。"""
    if not data:
        return {}
    extra_sensitive = {str(k).lower() for k in (sensitive_keys or ())}
    result: dict[str, Any] = {}
    for key, value in data.items():
        key_str = str(key)
        if key_str.lower() in extra_sensitive or is_sensitive_key(key_str):
            result[key_str] = "[REDACTED]"
            continue
        result[key_str] = _truncate_value(value, max_length)
    return result


def sanitize_headers(headers: dict[str, Any] | None, *, max_length: int = DEFAULT_PARAM_MAX) -> dict[str, str]:
    if not headers:
        return {}
    sanitized: dict[str, str] = {}
    for key, value in headers.items():
        key_str = str(key)
        if is_sensitive_key(key_str):
            sanitized[key_str] = "***"
        else:
            sanitized[key_str] = truncate_for_log(value, max_length)
    return sanitized


def sanitize_url(url: Any, *, max_length: int = DEFAULT_BODY_MAX) -> str:
    """去除 URL query 中的敏感参数并截断。"""
    text = str(url or "")
    if not text:
        return ""
    try:
        parts = urlsplit(text)
        if parts.query:
            cleaned = [
                (k, "[REDACTED]" if k.lower() in _SENSITIVE_QUERY_KEYS or is_sensitive_key(k) else v)
                for k, v in parse_qsl(parts.query, keep_blank_values=True)
            ]
            text = urlunsplit(
                (parts.scheme, parts.netloc, parts.path, urlencode(cleaned), parts.fragment)
            )
    except Exception:
        pass
    return truncate_for_log(text, max_length)


def format_body_for_log(
    content: bytes | str | None,
    *,
    max_length: int = DEFAULT_BODY_MAX,
) -> str | None:
    """格式化 body 摘要：不解析完整语义，仅截断与基础 JSON 紧凑化。"""
    if content is None:
        return None
    if isinstance(content, bytes):
        text = content.decode("utf-8", errors="replace")
    else:
        text = str(content)
    text = text.strip()
    if not text:
        return None
    try:
        import json

        parsed = json.loads(text)
        text = json.dumps(parsed, ensure_ascii=False)
    except Exception:
        pass
    if len(text) > max_length:
        return text[:max_length] + f"...<truncated:{len(text) - max_length}>"
    return text


def _truncate_value(value: Any, max_length: int) -> Any:
    if isinstance(value, str):
        return truncate_for_log(value, max_length)
    if isinstance(value, list):
        return [_truncate_value(item, max_length) for item in value]
    if isinstance(value, tuple):
        return [_truncate_value(item, max_length) for item in value]
    if isinstance(value, dict):
        return redact_mapping(value, max_length=max_length)
    if value is None or isinstance(value, (int, float, bool)):
        return value
    return truncate_for_log(value, max_length)
