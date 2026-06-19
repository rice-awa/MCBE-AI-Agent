# models.dev 模型上下文元数据实现计划

> **面向 AI 代理的工作者：** 按本计划逐任务实现。步骤使用复选框（`- [ ]`）语法跟踪进度。实现前先写失败测试；不要把普通配置写入 `.env`。

**目标：** 为 MCBE AI Agent 增加启动时从 models.dev 自动获取模型上下文窗口（context window）和基础模型元数据的能力，用于补全 `LLMProviderConfig.context_window`，让上下文使用率显示和后续压缩策略不再只依赖硬编码表。

**架构：** 新增独立 `ModelMetadataService`。应用启动时拉取 `https://models.dev/api.json` 并写入内存缓存，可选持久化到本地缓存文件；聊天请求路径只读缓存，不发网络请求。`MODEL_CONTEXT_WINDOWS` 继续作为稳定兜底，并优先于外部数据。

**技术栈：** Python 3.11、Pydantic Settings、httpx、asyncio、pytest、现有 `Settings` / `AgentRuntime` / `AgentWorker`。

---

## 已确认约束

- 获取策略：启动时自动拉取并缓存。
- 聊天路径不得等待 models.dev 网络请求。
- models.dev 不可用时服务继续启动。
- 普通配置进入 `config.json` / `config.example.json`，不新增普通 `.env` 字段。
- 保持 provider-neutral，不引入 Anthropic/OpenAI SDK 专用逻辑。
- 当前支持 provider：`deepseek`、`openai`、`anthropic`、`ollama`。
- models.dev `api.json` 结构已验证：顶层 provider key；每个 provider 下有 `models`；模型元数据里的 `limit.context` 是上下文窗口，`limit.output` 是最大输出 token。

## 文件结构

- 新增：`services/agent/model_metadata.py`
  - models.dev 客户端、元数据模型、内存缓存、持久化缓存、刷新服务。
- 修改：`config/settings.py`
  - 增加 `ModelMetadataConfig`、JSON 必填路径、flatten 映射、运行时元数据缓存入口。
- 修改：`config.example.json`
  - 增加 `model_metadata` 普通配置示例。
- 修改：`config.json`
  - 本地运行配置同步增加 `model_metadata`，避免当前工作区启动校验失败；是否提交按仓库策略决定。
- 修改：`services/agent/runtime.py`
  - 持有并初始化 `ModelMetadataService`，在 worker 启动前刷新。
- 修改：`cli.py`
  - 确认启动顺序不变：`Application.start()` 初始化 runtime 后再启动 workers。
- 修改：`services/agent/worker.py`
  - 尽量不改业务逻辑；继续读取 `provider_config.context_window`。
- 修改/新增测试：
  - `tests/test_model_metadata.py`
  - `tests/test_json_settings.py`
  - `tests/test_agent_runtime.py` 或现有 runtime/provider lifecycle 测试
  - 必要时扩展 `tests/test_queue_context.py`

---

## 设计细节

### 元数据解析

models.dev `api.json` 示例结构：

```json
{
  "deepseek": {
    "models": {
      "deepseek-chat": {
        "id": "deepseek-chat",
        "name": "DeepSeek Chat",
        "limit": {
          "context": 1000000,
          "output": 384000
        }
      }
    }
  }
}
```

解析规则：

- provider：顶层 key，例如 `deepseek` / `openai` / `anthropic`。
- model：`data[provider]["models"][model]` 的 key。
- context window：`limit.context`。
- max output tokens：`limit.output`。
- 非整数、缺失、负数、零值均视为未知，不抛异常。

### 上下文窗口优先级

`Settings.get_provider_config(provider).context_window` 使用以下优先级：

1. `MODEL_CONTEXT_WINDOWS[model]`。
2. 启动时 models.dev 缓存里的 `(provider, model) -> limit.context`。
3. `None`。

原因：本地静态表代表项目当前已验证行为，避免外部数据突然变化影响运行。

### 缓存策略

- 内存缓存是主路径。
- 可选持久化缓存文件：默认 `data/model_metadata_cache.json`。
- 启动时顺序：
  1. 如果启用持久化缓存，先尝试加载本地缓存。
  2. 如果 `refresh_on_startup=true`，请求 models.dev。
  3. 请求成功后替换内存缓存并写回缓存文件。
  4. 请求失败时保留已加载的本地缓存；若没有本地缓存，使用空缓存。
- 所有失败只记录 warning，不阻断启动。

---

### 任务 1：添加模型元数据配置

**文件：**
- 修改：`config/settings.py`
- 修改：`config.example.json`
- 修改：`config.json`
- 测试：`tests/test_json_settings.py`

- [ ] **步骤 1：编写失败的配置测试**

在 `tests/test_json_settings.py` 中新增测试，验证默认 JSON 配置能加载：

```python
def test_model_metadata_settings_loaded_from_json():
    settings = Settings()

    assert settings.model_metadata.enabled is True
    assert settings.model_metadata.source_url == "https://models.dev/api.json"
    assert settings.model_metadata.refresh_on_startup is True
    assert settings.model_metadata.timeout == 10
    assert str(settings.model_metadata.cache_path) == "data/model_metadata_cache.json"
```

- [ ] **步骤 2：运行测试验证失败**

```bash
./.venv/bin/python -m pytest tests/test_json_settings.py::test_model_metadata_settings_loaded_from_json -v
```

预期：FAIL，字段不存在或 config path 缺失。

- [ ] **步骤 3：扩展 `config/settings.py`**

增加配置模型：

```python
class ModelMetadataConfig(BaseModel):
    enabled: bool = True
    source_url: str = "https://models.dev/api.json"
    refresh_on_startup: bool = True
    timeout: int = 10
    cache_path: Path = Path("data/model_metadata_cache.json")
```

在 `Settings` 中增加：

```python
model_metadata: ModelMetadataConfig = Field(default_factory=ModelMetadataConfig)
```

在 `REQUIRED_CONFIG_PATHS` 中增加：

```python
"model_metadata.enabled",
"model_metadata.source_url",
"model_metadata.refresh_on_startup",
"model_metadata.timeout",
"model_metadata.cache_path",
```

在 `_flatten_json_config()` 中加入：

```python
if "model_metadata" in data:
    result["model_metadata"] = data["model_metadata"]
```

- [ ] **步骤 4：更新 JSON 配置**

在 `config.example.json` 顶层加入：

```json
"model_metadata": {
  "enabled": true,
  "source_url": "https://models.dev/api.json",
  "refresh_on_startup": true,
  "timeout": 10,
  "cache_path": "data/model_metadata_cache.json"
}
```

在本地 `config.json` 做同样修改，避免启动时 `_validate_runtime_config_data()` 失败。

- [ ] **步骤 5：验证配置测试通过**

```bash
./.venv/bin/python -m pytest tests/test_json_settings.py::test_model_metadata_settings_loaded_from_json -v
```

预期：PASS。

---

### 任务 2：实现 models.dev 元数据解析与缓存

**文件：**
- 新增：`services/agent/model_metadata.py`
- 测试：`tests/test_model_metadata.py`

- [ ] **步骤 1：编写解析测试**

新增 `tests/test_model_metadata.py`，覆盖最小样例：

```python
def test_parse_models_dev_api_context_and_output_limits():
    payload = {
        "deepseek": {
            "models": {
                "deepseek-chat": {
                    "id": "deepseek-chat",
                    "name": "DeepSeek Chat",
                    "limit": {"context": 1000000, "output": 384000},
                }
            }
        }
    }

    cache = ModelMetadataCache.from_models_dev_api(payload)

    metadata = cache.get("deepseek", "deepseek-chat")
    assert metadata is not None
    assert metadata.context_window == 1000000
    assert metadata.max_output_tokens == 384000
```

再加缺失和非法值测试：

- provider 缺失返回 `None`。
- model 缺失返回 `None`。
- `limit.context` 不是正整数时返回 `context_window=None`。
- provider/model 查询大小写按现有项目 provider/model 字符串保持精确匹配，不做猜测匹配。

- [ ] **步骤 2：运行测试验证失败**

```bash
./.venv/bin/python -m pytest tests/test_model_metadata.py -v
```

预期：FAIL，模块不存在。

- [ ] **步骤 3：实现数据模型与解析器**

在 `services/agent/model_metadata.py` 中实现：

```python
class ModelMetadata(BaseModel):
    provider: str
    model: str
    name: str | None = None
    context_window: int | None = None
    max_output_tokens: int | None = None

class ModelMetadataCache(BaseModel):
    models: dict[str, ModelMetadata] = Field(default_factory=dict)

    @staticmethod
    def key(provider: str, model: str) -> str:
        return f"{provider}:{model}"

    def get(self, provider: str, model: str) -> ModelMetadata | None:
        return self.models.get(self.key(provider, model))

    def get_context_window(self, provider: str, model: str) -> int | None:
        metadata = self.get(provider, model)
        return metadata.context_window if metadata else None

    @classmethod
    def from_models_dev_api(cls, payload: Mapping[str, Any]) -> "ModelMetadataCache":
        models: dict[str, ModelMetadata] = {}
        # Iterate provider objects, then provider["models"] objects.
        # For each model, copy name, limit.context, and limit.output when valid.
        return cls(models=models)
```

实现要点：

- key 必须使用 `f"{provider}:{model}"`，不做大小写归一化。
- `from_models_dev_api()` 只解析 dict 结构，遇到异常结构跳过。
- `_positive_int(value)` helper 只接受 `int` 且 `> 0`。
- 不在解析器里发网络请求。

- [ ] **步骤 4：实现持久化缓存读写**

在同一模块增加：

```python
async def load_cache(path: Path) -> ModelMetadataCache:
    # Return an empty cache when the file is absent or unreadable.
    # Parse JSON into ModelMetadataCache when present and valid.
    return ModelMetadataCache()

async def save_cache(path: Path, cache: ModelMetadataCache) -> None:
    # Ensure the parent directory exists, then write cache.model_dump(mode="json").
    return None
```

要求：

- 文件不存在返回空缓存。
- JSON 损坏返回空缓存并记录 warning。
- 写入前确保父目录存在。
- 使用 UTF-8 和 Pydantic `model_dump(mode="json")`。

- [ ] **步骤 5：验证解析测试通过**

```bash
./.venv/bin/python -m pytest tests/test_model_metadata.py -v
```

预期：PASS。

---

### 任务 3：实现 `ModelMetadataService` 启动刷新

**文件：**
- 修改：`services/agent/model_metadata.py`
- 测试：`tests/test_model_metadata.py`

- [ ] **步骤 1：编写服务刷新测试**

用 fake async client 或 monkeypatch 覆盖：

- `enabled=false` 时不请求网络，缓存为空。
- 请求成功时解析并更新内存缓存。
- 请求失败时保留已加载的本地缓存。
- 请求失败且无本地缓存时不抛异常。

- [ ] **步骤 2：实现服务类**

增加：

```python
class ModelMetadataService:
    def __init__(self, config: ModelMetadataConfig):
        self.config = config
        self._cache = ModelMetadataCache()

    @property
    def cache(self) -> ModelMetadataCache:
        return self._cache

    async def initialize(self) -> None:
        # Load the local cache first, then refresh from models.dev when enabled.
        return None

    async def refresh(self) -> None:
        # Fetch config.source_url with httpx, parse it, replace the cache, and persist it.
        return None

    def get_context_window(self, provider: str, model: str) -> int | None:
        return self._cache.get_context_window(provider, model)
```

`refresh()` 使用 `httpx.AsyncClient(timeout=config.timeout)` 请求 `config.source_url`。

请求要求：

- 加 `User-Agent`，避免被 models.dev 拒绝普通 Python urllib 风格请求。
- `response.raise_for_status()`。
- `response.json()` 必须是 dict，否则视为失败。

日志建议：

- 成功：`model_metadata_refresh_completed`，包含模型数量。
- 失败：`model_metadata_refresh_failed`，warning，不带完整响应体。
- 本地缓存损坏：`model_metadata_cache_load_failed`。

- [ ] **步骤 3：验证服务测试通过**

```bash
./.venv/bin/python -m pytest tests/test_model_metadata.py -v
```

预期：PASS。

---

### 任务 4：把元数据服务接入运行时启动流程

**文件：**
- 修改：`services/agent/runtime.py`
- 修改：`cli.py`（仅在需要调整顺序时）
- 测试：`tests/test_agent_runtime.py` 或现有 provider lifecycle 测试

- [ ] **步骤 1：编写 runtime 初始化测试**

测试目标：

- `AgentRuntime.initialize(settings)` 会在模型 warmup 前初始化 `ModelMetadataService`。
- metadata refresh 失败不影响 runtime 初始化返回。
- worker 启动前 settings 能查询到 metadata cache 中的 context window。

- [ ] **步骤 2：扩展 `AgentRuntime`**

在 `services/agent/runtime.py` 中：

- 初始化 `self.model_metadata_service`。
- 在 `initialize(settings)` 中，放在 `warmup_models(settings)` 之前：

```python
await self.initialize_model_metadata(settings)
```

- 增加访问器：

```python
def get_model_metadata_service(self) -> ModelMetadataService:
    return self.model_metadata_service
```

- shutdown 不需要关闭长期 client；service 每次 refresh 使用短生命周期 client。

- [ ] **步骤 3：让 `Settings` 能读取运行时缓存**

避免在 `Settings` 内部直接发网络请求。提供一个只设置缓存引用的方法，例如：

```python
def attach_model_metadata_cache(self, cache: ModelMetadataCache | None) -> None:
    self._model_metadata_cache = cache
```

实现注意：

- Pydantic Settings 默认可能禁止额外属性；用 `PrivateAttr` 保存运行时缓存。
- `get_provider_config()` 内只读 `_model_metadata_cache`。
- `Settings` 创建时没有缓存也必须正常工作。

- [ ] **步骤 4：启动流程顺序确认**

`cli.py` 当前 `Application.start()` 顺序是：

1. 配置 flow control。
2. `agent_runtime.initialize(self.settings)`。
3. 创建并启动 workers。
4. 启动 WebSocket server。

保持这个顺序即可；如不必要，不修改 `cli.py`。

- [ ] **步骤 5：验证 runtime 测试通过**

```bash
./.venv/bin/python -m pytest tests/test_agent_runtime.py -v
```

如果没有独立 runtime 测试文件，则运行新增/扩展的对应测试。

---

### 任务 5：调整 `get_provider_config()` 的 context window 解析

**文件：**
- 修改：`config/settings.py`
- 测试：`tests/test_json_settings.py` 或新增 `tests/test_provider_settings.py`

- [ ] **步骤 1：编写优先级测试**

覆盖：

- 静态表命中时优先返回 `MODEL_CONTEXT_WINDOWS`。
- 静态表缺失、metadata cache 命中时返回 models.dev context。
- 两者都缺失时返回 `None`。
- `model_metadata.enabled=false` 时不使用 metadata cache。

示例：

```python
def test_provider_context_window_uses_models_dev_cache_when_static_missing():
    settings = Settings(openai_model="future-model")
    cache = ModelMetadataCache.from_models_dev_api({
        "openai": {"models": {"future-model": {"limit": {"context": 123456}}}}
    })
    settings.attach_model_metadata_cache(cache)

    config = settings.get_provider_config("openai")

    assert config.context_window == 123456
```

- [ ] **步骤 2：实现解析 helper**

把当前局部函数：

```python
def get_context_window(model: str) -> int | None:
    return MODEL_CONTEXT_WINDOWS.get(model)
```

替换为 provider-aware helper：

```python
def get_context_window(provider: str, model: str) -> int | None:
    if model in MODEL_CONTEXT_WINDOWS:
        return MODEL_CONTEXT_WINDOWS[model]
    if self.model_metadata.enabled and self._model_metadata_cache is not None:
        return self._model_metadata_cache.get_context_window(provider, model)
    return None
```

更新四个 provider 分支传入 provider 名称。

- [ ] **步骤 3：验证设置测试通过**

```bash
./.venv/bin/python -m pytest tests/test_json_settings.py tests/test_model_metadata.py -q
```

预期：PASS。

---

### 任务 6：补充 CLI 信息显示与日志可观测性

**文件：**
- 修改：`cli.py`
- 测试：可选，若现有 CLI 测试存在则扩展

- [ ] **步骤 1：扩展 `python cli.py info` 输出**

在 provider 列表里追加 context 信息：

```text
✓ openai: gpt-4o (context: 128000)
```

未知时显示：

```text
context: unknown
```

注意：`info` 命令不启动 runtime，不应联网；它只能显示静态表或当前已附加缓存。默认情况下它多半只显示静态表结果。

- [ ] **步骤 2：启动日志包含 metadata 状态**

在 runtime 初始化后记录：

- metadata 是否启用。
- 缓存模型数量。
- refresh 是否成功。

不要打印完整 catalog，也不要打印 API key 或环境变量。

---

### 任务 7：验证 worker 间接行为

**文件：**
- 修改/新增：`tests/test_queue_context.py` 或 worker 相关测试
- 代码：通常不需要改 `services/agent/worker.py`

- [ ] **步骤 1：写间接测试**

构造 settings：

- 模型不在 `MODEL_CONTEXT_WINDOWS`。
- metadata cache 中有 context。
- 调用 worker 中构建 `ContextInfo` 的路径，断言 `max_tokens` 是 metadata cache 值。

如果当前 worker 测试不易直接覆盖私有闭包，可退而测试 `Settings.get_provider_config()`，并在计划执行报告中说明 worker 已通过现有读取路径复用该结果。

- [ ] **步骤 2：确认 worker 不联网**

检查 `services/agent/worker.py` 不 import `httpx` / `ModelMetadataService`，只读 `settings.get_provider_config()`。

---

### 任务 8：端到端手动验证

**文件：**
- 不一定修改代码

- [ ] **步骤 1：运行定向测试**

```bash
./.venv/bin/python -m pytest tests/test_model_metadata.py -q
./.venv/bin/python -m pytest tests/test_json_settings.py -q
./.venv/bin/python -m pytest tests/test_queue_context.py -q
```

- [ ] **步骤 2：运行全量测试**

```bash
./.venv/bin/python -m pytest -q
```

- [ ] **步骤 3：本地启动验证**

```bash
./.venv/bin/python cli.py info
./.venv/bin/python cli.py serve
```

启动日志应出现 metadata refresh 成功或失败 warning；无论 models.dev 是否可用，服务都应继续启动。

- [ ] **步骤 4：缓存文件验证**

确认成功刷新后生成：

```text
data/model_metadata_cache.json
```

检查文件内容只包含模型公开元数据，不包含密钥。

---

## 风险与处理

- **models.dev 阻止默认 Python 请求：** 使用 `httpx` 并设置明确 `User-Agent`。
- **外部数据变化导致上下文值变化：** 静态表优先，外部数据只补全未知模型。
- **启动网络慢：** 配置 `timeout=10`，失败不阻断启动。
- **缓存文件损坏：** 忽略文件并重新拉取；重新拉取失败则使用空 metadata cache。
- **Pydantic Settings 私有运行时状态：** 使用 `PrivateAttr`，不要把运行时缓存序列化进配置。
- **Ollama 本地模型不在 models.dev：** 继续依赖静态表或返回 `None`，不做远程猜测。

## 不做事项

- 不实现按需联网查询。
- 不新增 CLI 手动刷新命令。
- 不把 pricing、modalities、features 暴露到游戏内命令。
- 不改变 provider 创建逻辑。
- 不更改多人会话隔离逻辑。
- 不新增 `.env` 普通配置字段。

## 最终验收标准

- 启动时 models.dev refresh 成功会补全未知模型的 `context_window`。
- models.dev refresh 失败不会导致服务启动失败。
- 静态表命中时结果保持现有值。
- worker 聊天路径不发起 metadata 网络请求。
- `config.example.json` 与 `config.json` 配置完整。
- 新增测试覆盖解析、失败回退、优先级和配置加载。
- `./.venv/bin/python -m pytest -q` 通过。
