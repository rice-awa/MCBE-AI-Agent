# 接入 Anthropic 接口并支持自定义 base_url（官方/第三方兼容端点）

## Goal

真正接入 Anthropic AI 接口：让 anthropic provider 支持自定义 base_url，从而同时支持官方 `api.anthropic.com` 与第三方 Anthropic 兼容端点（如 DeepSeek 的 `https://api.deepseek.com/anthropic`）。测试用 DeepSeek 的 Anthropic 兼容接口 + `DEEPSEEK_API_KEY`，不对线上官方 Anthropic 做任何破坏。

## Requirements

- Anthropic provider 支持通过配置传入自定义 `base_url`：
  - 未配置 `base_url` 时，走官方默认 `api.anthropic.com`（当前行为不变）。
  - 配置 `base_url` 时，Anthropic 客户端请求发往该端点（第三方 Anthropic 兼容接口）。
- `base_url` 是普通配置，写入 `config.example.json` 与 `config.json` 的 `providers.anthropic.base_url`，可空。
- 配置穿透链路完整：`config.json` → `Settings` → `LLMProviderConfig` → `RuntimeAdapterRegistry._create_anthropic_model` → `AnthropicProvider(base_url=...)`。
- 模型实例缓存与 HTTP 客户端缓存按 `base_url` 区分（现有 `_build_model_cache_key` / `_build_http_client_cache_key` 已含 base_url，复用即可）。
- 测试用 DeepSeek Anthropic 兼容端点：`DEEPSEEK_API_KEY` 作为 api_key，`base_url = https://api.deepseek.com/anthropic`。

## Acceptance Criteria

- [ ] `config.example.json` 中 `providers.anthropic` 增加 `base_url` 字段（默认 `null`）。
- [ ] `config/settings.py` 增加 `anthropic_base_url` 字段；`get_provider_config("anthropic")` 返回的 `LLMProviderConfig.base_url` 正确携带该值。
- [ ] `services/agent/providers.py` 的 `_create_anthropic_model` 把 `config.base_url` 传给 `AnthropicProvider(base_url=...)`；未配置时退回官方默认。
- [ ] 新增/更新测试：`anthropic_base_url` 默认 `None`；配置后 `LLMProviderConfig.base_url` 字段正确；`_create_anthropic_model` 把 base_url 透传给 `AnthropicProvider`。
- [ ] 配置文档（CLAUDE.md 配置说明）补充 `providers.anthropic.base_url` 为用户可见字段。
- [ ] 用 DeepSeek Anthropic 兼容端点跑通一次真实调用（`python cli.py test-provider anthropic` 或等效验证），确认第三方端点可用、官方端点不受影响。

## Notes

- 这是轻量配置穿透任务（约 3 个文件 + 测试），PRD-only 即可，不需要 design.md / implement.md。
- Anthropic SDK 的 `base_url` 由 `AnthropicProvider` 透传给内部 `AsyncAnthropic`；第三方端点需提供与 Anthropic Messages API 兼容的 `/v1/messages`。
- 不触碰 `mcbe-ws-sdk`、多人会话隔离、流控等无关边界。
- 验证命令需在实施阶段执行，切换 `config.json` 时注意不要把真实密钥写进提交。
- 注意：`cli.py test-provider <provider>` 只创建模型实例并打印配置，**不真正调用 LLM**。真实调用验证需额外写一个一次性 asyncio 脚本，用 `AnthropicModel`（经由 `RuntimeAdapterRegistry.get_model`）对 DeepSeek 兼容端点发一次 `agent.run("你好")`，确认返回文本；验证后删除该临时脚本，不提交。