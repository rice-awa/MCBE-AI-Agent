from services.agent.mcwiki import (
    build_page_url,
    build_search_params,
    normalize_limit,
)


def test_normalize_limit_bounds() -> None:
    assert normalize_limit(None, default=5) == 5
    assert normalize_limit(0, default=5) == 1
    assert normalize_limit(10, default=5) == 10
    assert normalize_limit(100, default=5) == 50


def test_build_search_params() -> None:
    params = build_search_params("钻石", 5, namespaces=[0, 14], use_cache=False, pretty=True)
    assert params["q"] == "钻石"
    assert params["limit"] == 5
    assert params["namespaces"] == "0,14"
    assert params["useCache"] == "false"
    assert params["pretty"] == "true"


def test_build_page_url_encodes() -> None:
    url = build_page_url("https://mcwiki.rice-awa.top", "钻石")
    assert url == "https://mcwiki.rice-awa.top/api/page/%E9%92%BB%E7%9F%B3"
