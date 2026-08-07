# 执行计划：移除硬编码 MODEL_CONTEXT_WINDOWS，完全依赖 models.dev 在线数据

## 执行清单

### Step 1: 删除 `MODEL_CONTEXT_WINDOWS` 硬编码字典

**文件**: `config/settings.py:566-588`

删除从 `MODEL_CONTEXT_WINDOWS: dict[str, int] = {` 到 `}` 的整段代码（包含 DeepSeek / OpenAI / Anthropic / Ollama 的 18 个硬编码条目）。

---

### Step 2: 简化 `get_context_window()` 查找逻辑

**文件**: `config/settings.py:974-980`

```python
# 旧代码（硬编码优先）：
def get_context_window(provider: str, model: str) -> int | None:
    if model in MODEL_CONTEXT_WINDOWS:
        return MODEL_CONTEXT_WINDOWS[model]
    if self.model_metadata.enabled and self._model_metadata_cache is not None:
        return self._model_metadata_cache.get_context_window(provider, model)
    return None

# 新代码（在线优先，无回退值 —— 让调用方处理 fallback）：
def get_context_window(provider: str, model: str) -> int | None:
    if self.model_metadata.enabled and self._model_metadata_cache is not None:
        return self._model_metadata_cache.get_context_window(provider, model)
    return None
```

`get_context_window` 返回 `None` 时，后续 `context.py` 的 `compute_budget()` 会 fallback 到 `DEFAULT_FALLBACK_CONTEXT_WINDOW`，所以这里不需要给默认值。

---

### Step 3: 更新 `DEFAULT_FALLBACK_CONTEXT_WINDOW`

**文件**: `services/agent/context.py:43`

```python
# 旧值：
DEFAULT_FALLBACK_CONTEXT_WINDOW = 8192

# 新值：
DEFAULT_FALLBACK_CONTEXT_WINDOW = 128_000
```

这个值仅在 models.dev 不可用（离线、超时、缓存为空）时作为兜底。128k 是当前主流模型的合理下限，比 8192 更符合实际情况。

---

### Step 4: 更新默认模型名

**文件**: `config/settings.py:769-783`

| 字段 | 旧值 | 新值 | 依据 |
|------|------|------|------|
| `deepseek_model` | `deepseek-chat` | `deepseek-v4-flash` | 当前主流；config.example.json 已用此值 |
| `openai_model` | `gpt-4o` | `gpt-5.6` | models.dev 最新；config.example.json 已用此值 |
| `anthropic_model` | `claude-sonnet-4-20250514` | `claude-sonnet-5` | models.dev 最新；config.example.json 已用此值 |
| `ollama_model` | `llama3` | `llama3.1` | 本地模型，llama3.1 128k context 是更稳妥的默认值 |

> 注意：config.example.json 的 `openai.model` 已经是 `gpt-5.6`，`anthropic.model` 已经是 `claude-sonnet-5`，但 `ollama.model` 仍是 `llama3`。这里的 Python 默认值与 config.example.json 对齐。

---

### Step 5: 同步 `config.example.json` 中的 ollama 默认模型

**文件**: `config.example.json:30`

```json
# 旧：ollama.model = "llama3"
# 新：ollama.model = "llama3.1"
```

---

### Step 6: 更新测试

**文件**: `tests/test_agent_context.py`

- 所有 `_Settings(context_window=8192)` 替换为 `_Settings(context_window=128_000)`（约 4 处：T86, T341, T475, T671, T737）
- `test_missing_context_window_does_not_use_unlimited_budget()` 中的断言验证 `missing_context_window is True` 的行为不变，但 fallback 窗口值从 8192 变为 128000
- `test_missing_context_window_refuses_oversized_after_trim()` 同理

**文件**: `tests/test_agent_worker.py:1260,1265`

- `context_window=8192` → `context_window=128_000`
- `history_budget=8192` → `history_budget=128_000`

**文件**: `tests/test_cli_config_validation.py:41`

- 该测试断言 `"context: 128000"` 在 cli output 中，如果该值来自 models.dev 缓存（而非硬编码表），需要确认缓存已被正确加载。运行时 context_window 来自 models.dev 缓存，所以该测试可能不需要调整（deepseek-v4-flash 在 models.dev 中是 1,000,000，但硬编码表如果已删除，实际值取决于缓存状态）。

**文件**: `tests/test_json_settings.py:470`

- `assert settings.get_provider_config("openai").context_window == 128000` — 这个值来自 models.dev 缓存（openai:gpt-4o 是 128000），如果测试环境没有缓存会返回 None。需要评估测试环境是否加载了缓存。

---

### Step 7: 验证

```bash
# 运行测试
python -m pytest tests/ -x -q 2>&1 | tail -30

# 手动验证：启动后检查日志
# "model_metadata_status" 日志应显示 enabled=true, refresh_on_startup=true
# "model_metadata_refresh_completed" 应显示拉取到的模型数量
```

## 回滚点

1. 删除 `MODEL_CONTEXT_WINDOWS` 前，可以用 `git diff` 确认删除范围
2. 每个步骤都是独立的，可以按 Step 顺序 `git add --patch` 分段提交
3. 如果测试大面积失败，`git checkout -- config/settings.py services/agent/context.py` 可回滚全部变更

## 变更文件清单

| 文件 | 变更类型 |
|------|---------|
| `config/settings.py` | 删除硬编码字典 + 简化查找逻辑 + 更新默认模型名 |
| `services/agent/context.py` | 更新 fallback 窗口值 |
| `config.example.json` | 更新 ollama 默认模型 |
| `tests/test_agent_context.py` | 更新测试中的窗口值 |
| `tests/test_agent_worker.py` | 更新测试中的窗口值 |