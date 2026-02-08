"""Minecraft Wiki API 工具函数"""

from __future__ import annotations

from typing import Iterable
from urllib.parse import quote


MAX_SEARCH_LIMIT = 50
MIN_SEARCH_LIMIT = 1


def normalize_limit(limit: int | None, default: int = 10) -> int:
    """规范化搜索结果数量限制"""
    if limit is None:
        return default
    if limit < MIN_SEARCH_LIMIT:
        return MIN_SEARCH_LIMIT
    return min(limit, MAX_SEARCH_LIMIT)


def build_search_params(
    query: str,
    limit: int,
    namespaces: Iterable[int | str] | None = None,
    use_cache: bool = True,
    pretty: bool = False,
) -> dict[str, str | int | bool]:
    """构建搜索请求参数"""
    params: dict[str, str | int | bool] = {
        "q": query,
        "limit": limit,
    }
    if namespaces:
        params["namespaces"] = ",".join(str(ns) for ns in namespaces)
    if not use_cache:
        params["useCache"] = "false"
    if pretty:
        params["pretty"] = "true"
    return params


def build_mcwiki_url(base_url: str, path: str) -> str:
    """拼接 Minecraft Wiki API URL"""
    base = base_url.rstrip("/")
    path_part = path.lstrip("/")
    return f"{base}/{path_part}"


def build_page_url(base_url: str, page_name: str) -> str:
    """构建页面内容请求 URL"""
    encoded_name = quote(page_name, safe="")
    return build_mcwiki_url(base_url, f"api/page/{encoded_name}")
