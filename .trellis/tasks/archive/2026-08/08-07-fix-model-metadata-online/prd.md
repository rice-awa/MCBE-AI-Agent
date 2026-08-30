# PRD: 移除硬编码 MODEL_CONTEXT_WINDOWS，完全依赖 models.dev 在线数据

## 背景

`config/settings.py` 中维护了一张硬编码的 `MODEL_CONTEXT_WINDOWS` 字典（第 566-588 行），包含约 18 个模型的上下文窗口值。`get_context_window()` 的查找逻辑是**硬编码表优先**，只有硬编码表查不到时才回退到 models.dev 在线数据。

由于模型迭代频繁，该硬编码表已严重过时：

| 模型 | 硬编码值 | models.dev 实际值 | 差距 |
|------|---------|-------------------|------|
| `deepseek-chat` | 128,000 | 1,000,000 | 差 7.8x |
| `deepseek-v4-flash` | 128,000 | 1,000,000 | 差 7.8x |
| 大量新模型 | 未收录 | 1,000,000 | 完全缺失 |

## 需求

1. **删除 `MODEL_CONTEXT_WINDOWS` 硬编码字典**，不再使用静态表作为优先查找来源。
2. **完全依赖 models.dev 在线数据**：`get_context_window()` 直接查询 `ModelMetadataCache`（在线缓存），不再先查硬编码表。
3. **保留兜底 fallback**：当 models.dev 不可用（离线、超时、缓存为空）时，返回一个合理的默认值 `128_000`（而非当前的 `None` → `DEFAULT_FALLBACK_CONTEXT_WINDOW = 8192`，8192 对现代模型过于保守）。
4. **更新默认模型名**，适配当前最新模型：
   - `deepseek_model`: `deepseek-chat` → `deepseek-v4-flash`（v4 flash 已是主流）
   - `anthropic_model`: `claude-sonnet-4-20250514` → `claude-sonnet-4-5` 或 `claude-sonnet-4-6`（20250514 已被新版本取代）
   - `openai_model`: `gpt-4o` → 保持 `gpt-4o` 或升级到 `gpt-4.1`（根据最新数据确认）
   - `ollama_model`: `llama3` → `llama3.1` 或 `llama3.3`（本地模型建议更新）

## 非需求

- 不改动 models.dev 的拉取/缓存机制（`ModelMetadataService` / `refresh_on_startup` / `cache_path` 保持不变）
- 不改动 `AgentRuntime.initialize_model_metadata` 的启动流程
- 不改动 `resolve_context_window` 在 `context.py` 中的裁剪逻辑，只调整其获取到的原始值

## 验收标准

1. `MODEL_CONTEXT_WINDOWS` 字典从 `settings.py` 中移除
2. `get_context_window()` 不再优先查硬编码表，直接查 `ModelMetadataCache`
3. models.dev 不可用时返回 `128_000` 而非 `None`/`8192`
4. 默认模型名更新为较新的稳定版本
5. `config.example.json` 中的默认模型名同步更新
6. 项目启动时能正常从 models.dev 拉取数据并缓存
7. 现有测试通过，不引入回归