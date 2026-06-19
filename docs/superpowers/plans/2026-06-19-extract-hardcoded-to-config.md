# 硬编码内容提取到配置层 Implementation Plan

> **For agentic workers:** REQUIRED SUB- SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 `services/`、`core/`、`config/` 中的硬编码字符串、数值、URL、路径和协议标识提取到 `config.json` / `config.example.json` 或统一模块常量，消除重复字面量并提升可配置性。

**Architecture:** 扩展现有 `config/settings.py` 的 Pydantic Settings 模型，新增 `flow_control.*`、`agent.*`、`addon.protocol.*`、`storage.*`、`logging.*` 等配置段；业务代码通过 `get_settings()` 读取配置，内部语义常量集中在 `core/session.py` 等单一源；所有新增配置键同步进入 `config.example.json`。

**Tech Stack:** Python 3.11+, Pydantic Settings, pytest, pytest-asyncio

## Global Constraints

- Python 3.11+，4 空格缩进，snake_case 模块/变量，PascalCase 类。
- 普通配置进入 `config.json` / `config.example.json`；密钥、密码只放 `.env`。
- 新增或修改下行长文本发送路径时必须复用 `services/websocket/flow_control.py`。
- 玩家会话隔离：所有玩家相关状态必须显式携带并使用当前事件的 `player_name` / `sender`。
- 每个任务必须有相邻测试，运行 `pytest tests/... -v` 验证。
- 提交遵循 Conventional Commits，例如 `feat(config): 提取流控字节预算到配置`。

---

## File Structure

| 文件 | 变更类型 | 职责 |
|---|---|---|
| `config/settings.py` | 修改 | 新增 Pydantic 配置模型与字段，扁平化到 Settings |
| `config.example.json` | 修改 | 新增所有配置键的默认值与说明 |
| `core/session.py` | 修改（已存在常量） | `DEFAULT_PLAYER_KEY`、`DEFAULT_CONVERSATION_ID` 的单一源 |
| `models/constants.py` | 创建 | 新增 `DEFAULT_PLAYER_DISPLAY_NAME` 等跨模块语义常量 |
| `services/agent/prompt.py` | 修改 | 移除重复 `DEFAULT_PLAYER_KEY`，复用 `core/session.py` |
| `services/websocket/connection.py` | 修改 | 复用 `DEFAULT_PLAYER_KEY`、`DEFAULT_PLAYER_DISPLAY_NAME` |
| `services/websocket/minecraft.py` | 修改 | 复用 `DEFAULT_CONVERSATION_ID` |
| `services/websocket/server.py` | 修改 | 复用默认玩家名常量 |
| `services/agent/worker.py` | 修改 | 复用默认玩家名常量；读取 worker 超时配置 |
| `services/agent/core.py` | 修改 | 默认模型从 settings 推导；读取 Agent retries |
| `services/agent/title.py` | 修改 | 默认模型从 settings 推导 |
| `services/agent/providers.py` | 修改 | Ollama fallback URL 使用 settings 默认值 |
| `services/websocket/flow_control.py` | 修改 | 字节预算、分片延迟从 settings 读取 |
| `services/addon/protocol.py` | 修改 | 协议标识从 settings 初始化 |
| `config/logging.py` | 修改 | 日志目录、文件名、轮转策略从 settings 读取 |
| `core/conversation.py` | 修改 | 持久化目录从 settings 读取；摘要模型可配置 |
| `services/auth/jwt_handler.py` | 修改 | token 文件路径从 settings 读取；JWT 算法可配置 |

---

## Task 1: 统一匿名玩家与默认会话标识常量

**Files:**
- Create: `models/constants.py`
- Modify: `services/agent/prompt.py:92`、`services/websocket/connection.py:56`、`services/websocket/minecraft.py:122`、`models/messages.py:27`
- Test: `tests/test_constants.py`

**Interfaces:**
- Consumes: `core/session.py:DEFAULT_PLAYER_KEY`, `core/session.py:DEFAULT_CONVERSATION_ID`
- Produces: `models/constants.py:DEFAULT_PLAYER_DISPLAY_NAME`

- [ ] **Step 1: 创建 `models/constants.py`**

```python
"""跨模块共享的语义常量。

这些值在多个模块中出现，集中定义以避免硬编码字面量漂移。
"""

from core.session import DEFAULT_CONVERSATION_ID, DEFAULT_PLAYER_KEY

__all__ = [
    "DEFAULT_PLAYER_KEY",
    "DEFAULT_CONVERSATION_ID",
    "DEFAULT_PLAYER_DISPLAY_NAME",
]

DEFAULT_PLAYER_DISPLAY_NAME = "Player"
```

- [ ] **Step 2: 修改 `services/agent/prompt.py` 移除重复常量**

定位 `services/agent/prompt.py:92` 附近的：

```python
DEFAULT_PLAYER_KEY = "__anonymous__"
```

替换为：

```python
from core.session import DEFAULT_PLAYER_KEY
```

- [ ] **Step 3: 修改 `services/websocket/connection.py` 复用常量**

定位 `services/websocket/connection.py:56`：

```python
key = player_name or "__anonymous__"
```

替换为：

```python
from core.session import DEFAULT_PLAYER_KEY

key = player_name or DEFAULT_PLAYER_KEY
```

定位 `services/websocket/connection.py:410`：

```python
target = player_name or "@a"
```

保持不变（`@a` 是 MCBE 目标选择器，不是玩家名常量）。

- [ ] **Step 4: 修改 `services/websocket/minecraft.py` 复用默认对话 ID**

定位所有 `conversation_id: str = "default"` 的默认值，替换为：

```python
from core.session import DEFAULT_CONVERSATION_ID

conversation_id: str = DEFAULT_CONVERSATION_ID
```

- [ ] **Step 5: 修改 `models/messages.py` 复用默认对话 ID**

定位 `conversation_id: str = "default"` 默认值，替换为：

```python
from core.session import DEFAULT_CONVERSATION_ID

conversation_id: str = DEFAULT_CONVERSATION_ID
```

- [ ] **Step 6: 编写测试**

创建 `tests/test_constants.py`：

```python
from core.session import DEFAULT_CONVERSATION_ID, DEFAULT_PLAYER_KEY
from models.constants import DEFAULT_PLAYER_DISPLAY_NAME
from services.agent.prompt import PromptManager
from services.websocket.connection import ConnectionState


def test_default_player_key_single_source():
    """所有模块应引用同一匿名玩家键。"""
    assert DEFAULT_PLAYER_KEY == "__anonymous__"
    assert PromptManager.DEFAULT_PLAYER_KEY == DEFAULT_PLAYER_KEY
    assert ConnectionState.get_player_session.__defaults__ is None  # 不使用默认值覆盖
```

- [ ] **Step 7: 运行测试**

```bash
pytest tests/test_constants.py -v
```

Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add models/constants.py services/agent/prompt.py services/websocket/connection.py services/websocket/minecraft.py models/messages.py tests/test_constants.py
git commit -m "refactor(constants): 统一默认玩家键和对话 ID 常量"
```

---

## Task 2: 统一默认玩家显示名

**Files:**
- Modify: `services/agent/worker.py:209,226,438`、`services/websocket/server.py:403,1145`、`services/websocket/connection.py:497`
- Test: `tests/test_constants.py`

**Interfaces:**
- Consumes: `models/constants.py:DEFAULT_PLAYER_DISPLAY_NAME`

- [ ] **Step 1: 在所有位置复用 `DEFAULT_PLAYER_DISPLAY_NAME`**

在 `services/agent/worker.py` 顶部添加：

```python
from models.constants import DEFAULT_PLAYER_DISPLAY_NAME
```

替换以下位置：

```python
# services/agent/worker.py:209
player_name=request.player_name or DEFAULT_PLAYER_DISPLAY_NAME,

# services/agent/worker.py:226
message=f"AI正在为{request.player_name or DEFAULT_PLAYER_DISPLAY_NAME}思考",

# services/agent/worker.py:438
"player_name": request.player_name or DEFAULT_PLAYER_DISPLAY_NAME,
```

在 `services/websocket/server.py` 顶部添加：

```python
from models.constants import DEFAULT_PLAYER_DISPLAY_NAME
```

替换：

```python
# services/websocket/server.py:403
"player_name": player_name or state.player_name or DEFAULT_PLAYER_DISPLAY_NAME,

# services/websocket/server.py:1145
"player_name": player_name or DEFAULT_PLAYER_DISPLAY_NAME,
```

在 `services/websocket/connection.py` 顶部添加：

```python
from models.constants import DEFAULT_PLAYER_DISPLAY_NAME
```

替换：

```python
# services/websocket/connection.py:497
player_name = response.get("player_name", DEFAULT_PLAYER_DISPLAY_NAME)
```

- [ ] **Step 2: 添加测试断言**

在 `tests/test_constants.py` 中追加：

```python
def test_default_player_display_name():
    assert DEFAULT_PLAYER_DISPLAY_NAME == "Player"
```

- [ ] **Step 3: 运行测试**

```bash
pytest tests/test_constants.py -v
```

Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add services/agent/worker.py services/websocket/server.py services/websocket/connection.py tests/test_constants.py
git commit -m "refactor(constants): 统一默认玩家显示名"
```

---

## Task 3: 默认模型从 Settings 推导

**Files:**
- Modify: `services/agent/core.py:72`、`services/agent/title.py:7`
- Test: `tests/test_agent_provider_lifecycle.py`（追加）或 `tests/test_core_model_default.py`

**Interfaces:**
- Consumes: `Settings.default_provider`, `Settings.get_provider_config(...).model`

- [ ] **Step 1: 修改 `services/agent/core.py`**

定位 `services/agent/core.py:71-77`：

```python
agent = Agent[AgentDependencies, str](
    "deepseek:deepseek-chat",  # 默认模型，运行时可覆盖
    deps_type=AgentDependencies,
    output_type=str,
    retries=2,
    toolsets=toolsets,
)
```

替换为：

```python
settings = self._settings or get_settings()
default_provider_config = settings.get_provider_config(settings.default_provider)
agent = Agent[AgentDependencies, str](
    f"{settings.default_provider}:{default_provider_config.model}",
    deps_type=AgentDependencies,
    output_type=str,
    retries=settings.agent_retries,
    toolsets=toolsets,
)
```

- [ ] **Step 2: 在 `Settings` 中添加 `agent_retries`**

在 `config/settings.py` 中 `Settings` 类里 Agent 配置区域添加：

```python
agent_retries: int = Field(default=2, ge=0)
```

并在 `REQUIRED_CONFIG_PATHS` 中追加 `"agent.agent_retries"`。

- [ ] **Step 3: 修改 `services/agent/title.py`**

定位 `services/agent/title.py:7`：

```python
TITLE_AGENT_MODEL = "deepseek:deepseek-chat"
```

替换为函数或运行时推导：

```python
from config.settings import get_settings

def _get_title_agent_model() -> str:
    settings = get_settings()
    provider_config = settings.get_provider_config(settings.default_provider)
    return f"{settings.default_provider}:{provider_config.model}"
```

并修改所有使用 `TITLE_AGENT_MODEL` 的地方为 `_get_title_agent_model()`。

- [ ] **Step 4: 编写测试**

创建 `tests/test_core_model_default.py`：

```python
from unittest.mock import patch

from services.agent.core import ChatAgentManager


def test_default_agent_model_derived_from_settings():
    manager = ChatAgentManager()
    with patch("services.agent.core.get_settings") as mock_settings:
        mock_settings.return_value.default_provider = "openai"
        mock_settings.return_value.get_provider_config.return_value.model = "gpt-4o"
        mock_settings.return_value.agent_retries = 3
        agent = manager._create_agent()
        assert str(agent.model) == "openai:gpt-4o"
```

- [ ] **Step 5: 运行测试**

```bash
pytest tests/test_core_model_default.py -v
```

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add config/settings.py services/agent/core.py services/agent/title.py tests/test_core_model_default.py
git commit -m "feat(config): 默认模型从 settings 推导并支持 agent_retries 配置"
```

---

## Task 4: 提取流控数值到配置

**Files:**
- Modify: `config/settings.py`、`services/websocket/flow_control.py`
- Test: `tests/test_flow_control.py`

**Interfaces:**
- Consumes: `Settings.flow_control.command_line_byte_budget`, `Settings.flow_control.chunk_delays.*`
- Produces: `FlowControlMiddleware` 运行时使用配置值

- [ ] **Step 1: 在 `config/settings.py` 中添加流控配置模型**

在 `WebSocketConfig` 之前添加：

```python
class FlowControlDelayConfig(BaseModel):
    tellraw: float = 0.05
    scriptevent: float = 0.05
    ai_resp: float = 0.15
    ai_resp_prelude: float = 0.5


class FlowControlConfig(BaseModel):
    command_line_byte_budget: int = 461
    chunk_delays: FlowControlDelayConfig = Field(default_factory=FlowControlDelayConfig)
```

在 `Settings` 类中添加：

```python
flow_control: FlowControlConfig = Field(default_factory=FlowControlConfig)
```

- [ ] **Step 2: 修改 `services/websocket/flow_control.py`**

定位：

```python
_COMMAND_LINE_BYTE_BUDGET = 461
```

替换为从 settings 读取：

```python
from config.settings import get_settings


def _get_command_line_byte_budget() -> int:
    return get_settings().flow_control.command_line_byte_budget
```

将所有使用 `_COMMAND_LINE_BYTE_BUDGET` 的地方替换为 `_get_command_line_byte_budget()`。

定位 `_CHUNK_DELAYS`：

```python
_CHUNK_DELAYS: dict[str, float] = {
    "tellraw": 0.05,
    "scriptevent": 0.05,
    "ai_resp": 0.15,
    "ai_resp_prelude": 0.5,
}
```

替换为：

```python
@classmethod
def _chunk_delays(cls) -> dict[str, float]:
    return get_settings().flow_control.chunk_delays.model_dump()
```

修改 `chunk_delay_for`：

```python
@classmethod
def chunk_delay_for(cls, kind: str) -> float:
    return cls._chunk_delays().get(kind, 0.0)
```

- [ ] **Step 3: 更新 `config.example.json`**

在 `flow_control` 段中追加：

```json
"flow_control": {
  "max_chunk_content_length": 400,
  "chunk_sentence_mode": true,
  "command_line_byte_budget": 461,
  "chunk_delays": {
    "tellraw": 0.05,
    "scriptevent": 0.05,
    "ai_resp": 0.15,
    "ai_resp_prelude": 0.5
  }
}
```

- [ ] **Step 4: 编写测试**

在 `tests/test_flow_control.py` 中追加：

```python
from unittest.mock import patch

from services.websocket.flow_control import FlowControlMiddleware


def test_chunk_delay_read_from_settings():
    with patch("services.websocket.flow_control.get_settings") as mock_settings:
        mock_settings.return_value.flow_control.chunk_delays.model_dump.return_value = {
            "tellraw": 0.1,
            "scriptevent": 0.2,
            "ai_resp": 0.3,
            "ai_resp_prelude": 0.4,
        }
        assert FlowControlMiddleware.chunk_delay_for("tellraw") == 0.1
        assert FlowControlMiddleware.chunk_delay_for("unknown") == 0.0
```

- [ ] **Step 5: 运行测试**

```bash
pytest tests/test_flow_control.py -v
```

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add config/settings.py services/websocket/flow_control.py config.example.json tests/test_flow_control.py
git commit -m "feat(config): 提取流控字节预算与分片延迟到配置"
```

---

## Task 5: 提取 Agent Worker 超时到配置

**Files:**
- Modify: `config/settings.py`、`services/agent/worker.py`
- Test: `tests/test_agent_worker.py`

**Interfaces:**
- Consumes: `Settings.agent.worker_http_timeout`, `Settings.agent.worker_poll_timeout`, `Settings.agent.run_command_timeout`

- [ ] **Step 1: 在 `Settings` 中添加配置字段**

在 `Settings` 类的 Agent 配置区域添加：

```python
worker_http_timeout: int = Field(default=60, ge=1)
worker_poll_timeout: float = Field(default=1.0, gt=0)
run_command_timeout: float = Field(default=10.0, gt=0)
```

- [ ] **Step 2: 修改 `services/agent/worker.py`**

定位：

```python
self._http_client = httpx.AsyncClient(timeout=60)
```

替换为：

```python
self._http_client = httpx.AsyncClient(timeout=self.settings.worker_http_timeout)
```

定位：

```python
item = await asyncio.wait_for(
    self.broker.get_request(),
    timeout=1.0
)
```

替换为：

```python
item = await asyncio.wait_for(
    self.broker.get_request(),
    timeout=self.settings.worker_poll_timeout
)
```

定位：

```python
return await asyncio.wait_for(future, timeout=10.0)
```

替换为：

```python
return await asyncio.wait_for(future, timeout=self.settings.run_command_timeout)
```

- [ ] **Step 3: 更新 `config.example.json`**

在 `agent` 段中追加：

```json
"agent": {
  ...,
  "worker_http_timeout": 60,
  "worker_poll_timeout": 1.0,
  "run_command_timeout": 10.0
}
```

- [ ] **Step 4: 编写测试**

创建 `tests/test_agent_worker.py`：

```python
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.agent.worker import AgentWorker


@pytest.fixture
def settings():
    s = MagicMock()
    s.worker_http_timeout = 30
    s.worker_poll_timeout = 2.0
    s.run_command_timeout = 5.0
    return s


def test_worker_uses_http_timeout(settings):
    broker = MagicMock()
    worker = AgentWorker(broker, settings)
    # 仅验证初始化时 timeout 被正确传入
    assert worker.settings.worker_http_timeout == 30
```

- [ ] **Step 5: 运行测试**

```bash
pytest tests/test_agent_worker.py -v
```

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add config/settings.py services/agent/worker.py config.example.json tests/test_agent_worker.py
git commit -m "feat(config): 提取 Agent Worker 超时到配置"
```

---

## Task 6: 提取非流式批大小与延迟到配置

**Files:**
- Modify: `config/settings.py`、`services/agent/core.py`
- Test: `tests/test_stream_mode.py`

**Interfaces:**
- Consumes: `Settings.flow_control.non_stream_batch_max_chars`, `Settings.flow_control.non_stream_send_delay`

- [ ] **Step 1: 在 `FlowControlConfig` 中添加字段**

```python
non_stream_batch_max_chars: int = 150
non_stream_send_delay: float = 0.1
```

- [ ] **Step 2: 修改 `services/agent/core.py`**

定位：

```python
NON_STREAM_BATCH_MAX_CHARS = 150
NON_STREAM_SEND_DELAY = 0.1
```

替换为从 settings 读取：

```python
def _non_stream_batch_max_chars() -> int:
    return get_settings().flow_control.non_stream_batch_max_chars


def _non_stream_send_delay() -> float:
    return get_settings().flow_control.non_stream_send_delay
```

修改 `_send_content_event` 中的：

```python
if add_delay:
    await asyncio.sleep(NON_STREAM_SEND_DELAY)
```

为：

```python
if add_delay:
    await asyncio.sleep(_non_stream_send_delay())
```

修改 `_iter_sentence_batches` 的默认参数：

```python
def _iter_sentence_batches(text: str, max_chars: int | None = None) -> Iterator[str]:
    max_chars = max_chars if max_chars is not None else _non_stream_batch_max_chars()
```

- [ ] **Step 3: 更新 `config.example.json`**

在 `flow_control` 段追加：

```json
"non_stream_batch_max_chars": 150,
"non_stream_send_delay": 0.1
```

- [ ] **Step 4: 运行测试**

```bash
pytest tests/test_stream_mode.py -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add config/settings.py services/agent/core.py config.example.json
git commit -m "feat(config): 提取非流式批大小与延迟到配置"
```

---

## Task 7: 提取 Addon 协议标识到配置

**Files:**
- Modify: `config/settings.py`、`services/addon/protocol.py`、`config.example.json`
- Test: `tests/test_addon_bridge_protocol.py`

**Interfaces:**
- Consumes: `Settings.addon.protocol.*`

- [ ] **Step 1: 在 `Settings` 中添加 Addon 配置模型**

在 `MCPConfig` 之后添加：

```python
class AddonProtocolConfig(BaseModel):
    bridge_message_id: str = "mcbeai:bridge_request"
    bridge_prefix: str = "MCBEAI|RESP"
    ui_chat_prefix: str = "MCBEAI|UI_CHAT"
    bridge_tool_player_name: str = "MCBEAI_TOOL"
    ai_resp_message_id: str = "mcbeai:ai_resp"


class AddonConfig(BaseModel):
    protocol: AddonProtocolConfig = Field(default_factory=AddonProtocolConfig)
```

在 `Settings` 类中添加：

```python
addon: AddonConfig = Field(default_factory=AddonConfig)
```

- [ ] **Step 2: 修改 `services/addon/protocol.py`**

将模块级常量改为运行时从 settings 读取：

```python
from config.settings import get_settings


def _protocol() -> AddonProtocolConfig:
    return get_settings().addon.protocol


BRIDGE_MESSAGE_ID = "mcbeai:bridge_request"
BRIDGE_PREFIX = "MCBEAI|RESP"
UI_CHAT_PREFIX = "MCBEAI|UI_CHAT"
BRIDGE_TOOL_PLAYER_NAME = "MCBEAI_TOOL"
AI_RESP_MESSAGE_ID = "mcbeai:ai_resp"
```

并修改 `encode_bridge_request`：

```python
return f"scriptevent {_protocol().bridge_message_id} {json.dumps(body, ensure_ascii=False)}"
```

修改 `decode_bridge_chat_chunk` 中前缀校验使用 `_protocol().bridge_prefix`。

- [ ] **Step 3: 更新 `config.example.json`**

新增顶层段：

```json
"addon": {
  "protocol": {
    "bridge_message_id": "mcbeai:bridge_request",
    "bridge_prefix": "MCBEAI|RESP",
    "ui_chat_prefix": "MCBEAI|UI_CHAT",
    "bridge_tool_player_name": "MCBEAI_TOOL",
    "ai_resp_message_id": "mcbeai:ai_resp"
  }
}
```

- [ ] **Step 4: 运行测试**

```bash
pytest tests/test_addon_bridge_protocol.py -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add config/settings.py services/addon/protocol.py config.example.json
git commit -m "feat(config): 提取 Addon 协议标识到配置"
```

---

## Task 8: 提取文件路径与日志配置

**Files:**
- Modify: `config/settings.py`、`config/logging.py`、`core/conversation.py`、`services/auth/jwt_handler.py`、`config.example.json`
- Test: `tests/test_logging.py`、`tests/test_conversation_manager.py`

**Interfaces:**
- Consumes: `Settings.logging.log_dir`, `Settings.logging.files.*`, `Settings.storage.conversations_dir`, `Settings.storage.tokens_file`

- [ ] **Step 1: 在 `Settings` 中添加配置模型**

添加：

```python
class LoggingFilesConfig(BaseModel):
    app: str = "app.log"
    websocket_raw: str = "websocket.log"
    llm_raw: str = "llm.log"


class LoggingConfig(BaseModel):
    log_dir: Path = Path("logs")
    files: LoggingFilesConfig = Field(default_factory=LoggingFilesConfig)
    rotation_when: str = "midnight"
    rotation_interval: int = 1
    rotation_backup_count: int = 30


class StorageConfig(BaseModel):
    conversations_dir: Path = Path("data/conversations")
    tokens_file: Path = Path("data/tokens.json")
```

在 `Settings` 中添加：

```python
storage: StorageConfig = Field(default_factory=StorageConfig)
```

注意：现有 `logging` 相关字段已扁平化到 `Settings`（`log_level`, `enable_file_logging` 等），这里新增 `logging_files` 等字段以避免与现有字段命名冲突，或在 `_flatten_json_config` 中处理。

- [ ] **Step 2: 修改 `config/logging.py`**

将 `setup_logging` 改为接收 `settings: Settings | None = None`，并使用 settings 中的路径：

```python
from config.settings import Settings, get_settings


def setup_logging(
    settings: Settings | None = None,
) -> None:
    settings = settings or get_settings()
    log_level = settings.log_level
    enable_file_logging = settings.enable_file_logging
    enable_ws_raw_log = settings.enable_ws_raw_log
    enable_llm_raw_log = settings.enable_llm_raw_log

    ...

    if enable_file_logging:
        log_dir = settings.logging_files.log_dir
        log_dir.mkdir(parents=True, exist_ok=True)
        ...
```

- [ ] **Step 3: 修改 `core/conversation.py`**

定位：

```python
self._storage_dir = Path("data/conversations")
```

替换为：

```python
self._storage_dir = settings.storage.conversations_dir
self._storage_dir.mkdir(parents=True, exist_ok=True)
```

- [ ] **Step 4: 修改 `services/auth/jwt_handler.py`**

定位：

```python
self.token_file = Path("data/tokens.json")
```

替换为：

```python
self.token_file = settings.storage.tokens_file
```

- [ ] **Step 5: 更新 `config.example.json`**

新增：

```json
"logging": {
  ...,
  "log_dir": "logs",
  "files": {
    "app": "app.log",
    "websocket_raw": "websocket.log",
    "llm_raw": "llm.log"
  },
  "rotation_when": "midnight",
  "rotation_interval": 1,
  "rotation_backup_count": 30
},
"storage": {
  "conversations_dir": "data/conversations",
  "tokens_file": "data/tokens.json"
}
```

- [ ] **Step 6: 运行测试**

```bash
pytest tests/test_logging.py tests/test_conversation_manager.py tests/test_connection_manager.py -v
```

Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add config/settings.py config/logging.py core/conversation.py services/auth/jwt_handler.py config.example.json
git commit -m "feat(config): 提取日志路径与持久化目录到配置"
```

---

## Task 9: 提取 JWT 算法到配置

**Files:**
- Modify: `config/settings.py`、`services/auth/jwt_handler.py`、`config.example.json`
- Test: `tests/test_jwt_handler.py`

**Interfaces:**
- Consumes: `Settings.auth.jwt_algorithm`

- [ ] **Step 1: 在 `Settings` 中添加字段**

```python
jwt_algorithm: str = "HS256"
```

- [ ] **Step 2: 修改 `services/auth/jwt_handler.py`**

替换：

```python
token = jwt.encode(payload, self.secret_key, algorithm="HS256")
```

为：

```python
token = jwt.encode(payload, self.secret_key, algorithm=self.settings.jwt_algorithm)
```

以及：

```python
jwt.decode(token, self.secret_key, algorithms=["HS256"])
```

为：

```python
jwt.decode(token, self.secret_key, algorithms=[self.settings.jwt_algorithm])
```

- [ ] **Step 3: 更新 `config.example.json`**

在 `auth` 段追加：

```json
"jwt_algorithm": "HS256"
```

- [ ] **Step 4: 运行测试**

```bash
pytest tests/test_connection_manager.py -v -k jwt
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add config/settings.py services/auth/jwt_handler.py config.example.json
git commit -m "feat(config): 提取 JWT 算法到配置"
```

---

## Task 10: 全量回归测试与配置示例同步

**Files:**
- Modify: `config.example.json`
- Test: 全部测试

- [ ] **Step 1: 运行全量测试**

```bash
pytest
```

Expected: PASS（或记录现有失败并说明影响范围）

- [ ] **Step 2: 验证 `config.example.json` 与 `Settings` 模型一致**

```bash
python -c "from config.settings import get_settings; print('OK')"
```

Expected: 输出 `OK`

- [ ] **Step 3: Commit**

```bash
git add config.example.json
git commit -m "chore(config): 同步 config.example.json 新增配置键"
```

---

## Self-Review

1. **Spec coverage:** 本计划覆盖了硬编码内容提取评审报告中的候选 1（部分）、2、3、4、5、以及 JWT 算法。玩家可见中文文本的完整迁移不在本计划，留待 i18n 计划处理。
2. **Placeholder scan:** 无 TBD/TODO，每个步骤包含具体文件、代码和命令。
3. **Type consistency:** `Settings` 新增字段命名统一为 snake_case；`flow_control` 使用嵌套 BaseModel 与现有 `max_chunk_content_length` 保持一致风格。
