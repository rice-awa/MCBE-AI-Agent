# JSON 主配置系统实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将 MCBE AI Agent 的普通配置迁移到 JSON 文件，环境变量只保存密钥、密码等敏感内容，并允许 JSON 通过 `${VAR}` 引用这些环境变量。

**架构：** 保持 `Settings` 对外接口基本不变，新增一个 Pydantic Settings 自定义 JSON source，从 `config.json` 读取普通配置并在读取阶段递归解析 `${VAR}`。`.env` 和进程环境变量只参与占位符解析，不再作为普通配置 source；`get_provider_config()` 和 `list_available_providers()` 继续作为业务入口。

**技术栈：** Python 3.11、Pydantic v2、pydantic-settings v2、Click、pytest。

---

## 文件结构

- 修改：`config/settings.py`
  - 新增 `.env` 简易读取、`${VAR}` 递归解析、JSON 文件 source、JSON 到现有 `Settings` 字段的扁平映射。
  - 移除 `MCP_SERVERS` 与 `MINECRAFT_COMMANDS` 环境变量大 JSON 解析路径。
  - 保持 `Settings.get_provider_config()`、`Settings.list_available_providers()`、`Settings.websocket`、`Settings.minecraft`、`Settings.mcp` 的调用形态。
- 创建：`config.example.json`
  - 提供普通配置模板，包含 server/auth/providers/agent/queue/websocket/minecraft/mcp/logging/dev_mode/flow_control 等分组。
  - 默认 DeepSeek `api_key` 使用 `${DEEPSEEK_API_KEY}`，可选 provider 的 `api_key` 使用 `null`。
- 修改：`.env.example`
  - 只保留 `SECRET_KEY`、`WEBSOCKET_PASSWORD`、`DEEPSEEK_API_KEY`、`OPENAI_API_KEY`、`ANTHROPIC_API_KEY` 等敏感项。
- 修改：`.gitignore`
  - 忽略真实 `config.json`，只提交 `config.example.json`。
- 修改：`cli.py`
  - `init` 同时从 `.env.example` 生成 `.env`、从 `config.example.json` 生成 `config.json`。
  - `info`、`mcp list` 的提示改为指向 `config.json` 和 `.env` 中的密钥变量。
- 修改：`README.md`
  - 更新快速开始与配置说明，说明 JSON 主配置、`.env` 敏感变量、`${VAR}` 规则。
- 修改：`AGENTS.md`
  - 更新配置章节和协作说明。
- 修改：`CLAUDE.md`
  - 更新项目指令中的配置说明。
- 修改：`tests/test_mcp.py`
  - 替换旧 `MCP_SERVERS` / `MINECRAFT_COMMANDS` 环境 JSON 测试。
- 创建：`tests/test_json_settings.py`
  - 集中测试 JSON 加载、占位符替换、缺失变量报错、旧普通环境变量不生效。

## 设计约束

- `config.json` 不提交到 Git；`config.example.json` 是模板。
- 不保留旧普通 `.env` 配置兼容：`HOST`、`PORT`、`DEFAULT_PROVIDER`、`MCP_SERVERS`、`MINECRAFT_COMMANDS` 等环境变量不会影响 `Settings()`。
- 构造参数仍是最高优先级，供测试和 CLI 运行时覆盖使用。
- `${VAR}` 只从 `.env` 与进程环境变量读取；进程环境变量覆盖 `.env` 同名值。
- JSON 字符串中出现 `${VAR}` 时必须能解析到非空值，否则抛出 `ValueError`，错误包含 JSON 路径和变量名。
- 可选密钥不应写 `${VAR}`；不用的 provider 应写 `null` 或省略 `api_key`。

---

### 任务 1：为 JSON 配置加载写失败测试

**文件：**
- 创建：`tests/test_json_settings.py`
- 修改：无
- 测试：`tests/test_json_settings.py`

- [ ] **步骤 1：编写失败的测试**

创建 `tests/test_json_settings.py`，写入以下内容：

```python
import json

import pytest

from config.settings import Settings


def write_json_config(tmp_path, data):
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def write_env(tmp_path, content):
    path = tmp_path / ".env"
    path.write_text(content, encoding="utf-8")
    return path


def test_settings_loads_plain_values_from_config_json(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_json_config(
        tmp_path,
        {
            "server": {"host": "127.0.0.1", "port": 19132},
            "auth": {"jwt_secret": "secret", "default_password": "pass", "jwt_expiration": 60},
            "providers": {
                "default": "ollama",
                "ollama": {"base_url": "http://localhost:11434", "model": "llama3.1"},
            },
            "queue": {"max_size": 50, "llm_worker_count": 1},
            "logging": {"level": "DEBUG", "enable_file_logging": False},
            "flow_control": {"max_chunk_content_length": 123, "chunk_sentence_mode": False},
        },
    )

    settings = Settings()

    assert settings.host == "127.0.0.1"
    assert settings.port == 19132
    assert settings.jwt_secret == "secret"
    assert settings.default_password == "pass"
    assert settings.jwt_expiration == 60
    assert settings.default_provider == "ollama"
    assert settings.ollama_model == "llama3.1"
    assert settings.queue_max_size == 50
    assert settings.llm_worker_count == 1
    assert settings.log_level == "DEBUG"
    assert settings.enable_file_logging is False
    assert settings.max_chunk_content_length == 123
    assert settings.chunk_sentence_mode is False


def test_json_placeholders_resolve_from_dotenv_and_process_env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_env(
        tmp_path,
        "SECRET_KEY=dotenv-secret\n"
        "WEBSOCKET_PASSWORD=dotenv-pass\n"
        "DEEPSEEK_API_KEY=dotenv-deepseek\n",
    )
    monkeypatch.setenv("DEEPSEEK_API_KEY", "process-deepseek")
    write_json_config(
        tmp_path,
        {
            "auth": {"jwt_secret": "${SECRET_KEY}", "default_password": "${WEBSOCKET_PASSWORD}"},
            "providers": {
                "default": "deepseek",
                "deepseek": {
                    "api_key": "${DEEPSEEK_API_KEY}",
                    "base_url": "https://api.deepseek.com",
                    "model": "deepseek-chat",
                },
            },
        },
    )

    settings = Settings()

    assert settings.jwt_secret == "dotenv-secret"
    assert settings.default_password == "dotenv-pass"
    assert settings.deepseek_api_key == "process-deepseek"


def test_missing_placeholder_reports_path_and_variable(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_json_config(
        tmp_path,
        {
            "providers": {
                "default": "deepseek",
                "deepseek": {
                    "api_key": "${DEEPSEEK_API_KEY}",
                    "base_url": "https://api.deepseek.com",
                    "model": "deepseek-chat",
                },
            }
        },
    )
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    with pytest.raises(ValueError) as exc_info:
        Settings()

    message = str(exc_info.value)
    assert "providers.deepseek.api_key" in message
    assert "DEEPSEEK_API_KEY" in message


def test_empty_placeholder_value_reports_path_and_variable(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_env(tmp_path, "DEEPSEEK_API_KEY=\n")
    write_json_config(
        tmp_path,
        {
            "providers": {
                "default": "deepseek",
                "deepseek": {
                    "api_key": "${DEEPSEEK_API_KEY}",
                    "base_url": "https://api.deepseek.com",
                    "model": "deepseek-chat",
                },
            }
        },
    )

    with pytest.raises(ValueError) as exc_info:
        Settings()

    message = str(exc_info.value)
    assert "providers.deepseek.api_key" in message
    assert "DEEPSEEK_API_KEY" in message


def test_old_plain_environment_variables_do_not_override_settings(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOST", "10.0.0.10")
    monkeypatch.setenv("PORT", "25565")
    monkeypatch.setenv("DEFAULT_PROVIDER", "anthropic")
    monkeypatch.setenv("LLM_WORKER_COUNT", "8")

    settings = Settings()

    assert settings.host == "0.0.0.0"
    assert settings.port == 8080
    assert settings.default_provider == "deepseek"
    assert settings.llm_worker_count == 2
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
pytest tests/test_json_settings.py -v
```

预期：FAIL，至少出现以下失败之一：

```text
AssertionError: assert '0.0.0.0' == '127.0.0.1'
```

或：

```text
Failed: DID NOT RAISE <class 'ValueError'>
```

- [ ] **步骤 3：Commit 失败测试**

运行：

```bash
git add tests/test_json_settings.py
git commit -m "test(config): cover json settings loading"
```

预期：创建一个只包含失败测试的提交。

---

### 任务 2：实现 JSON source、占位符解析和 source 优先级

**文件：**
- 修改：`config/settings.py`
- 测试：`tests/test_json_settings.py`

- [ ] **步骤 1：修改 imports**

在 `config/settings.py` 顶部替换 imports 为以下结构，保留已有 `json` 和 `lru_cache`：

```python
import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict
from pydantic_settings.sources import JsonConfigSettingsSource
```

- [ ] **步骤 2：新增 `.env` 与占位符解析工具**

在 `MODEL_CONTEXT_WINDOWS` 前插入以下代码：

```python
CONFIG_FILE = Path("config.json")
DOTENV_FILE = Path(".env")
_ENV_REF_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _parse_dotenv_line(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return None
    key, value = stripped.split("=", 1)
    key = key.strip()
    value = value.strip()
    if not key:
        return None
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    return key, value


def _load_dotenv(path: Path = DOTENV_FILE) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        parsed = _parse_dotenv_line(line)
        if parsed is not None:
            key, value = parsed
            values[key] = value
    return values


def _secret_environment() -> dict[str, str]:
    values = _load_dotenv()
    values.update(os.environ)
    return values


def _resolve_env_refs(value: Any, env: dict[str, str], path: str = "") -> Any:
    if isinstance(value, dict):
        return {
            key: _resolve_env_refs(child, env, f"{path}.{key}" if path else str(key))
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [
            _resolve_env_refs(child, env, f"{path}[{index}]")
            for index, child in enumerate(value)
        ]
    if not isinstance(value, str):
        return value

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        resolved = env.get(name)
        if resolved is None or resolved == "":
            location = path or "<root>"
            raise ValueError(f"missing environment variable {name!r} for config path {location}")
        return resolved

    return _ENV_REF_PATTERN.sub(replace, value)
```

- [ ] **步骤 3：新增 JSON 到 Settings 字段映射**

在 `_resolve_env_refs` 后插入以下代码：

```python
def _flatten_json_config(data: dict[str, Any]) -> dict[str, Any]:
    providers = data.get("providers", {})
    logging_config = data.get("logging", {})
    flow_control = data.get("flow_control", {})
    queue = data.get("queue", {})
    agent = data.get("agent", {})
    auth = data.get("auth", {})
    server = data.get("server", {})

    result: dict[str, Any] = {}

    result.update(server)

    if "jwt_secret" in auth:
        result["jwt_secret"] = auth["jwt_secret"]
    if "jwt_expiration" in auth:
        result["jwt_expiration"] = auth["jwt_expiration"]
    if "default_password" in auth:
        result["default_password"] = auth["default_password"]

    if "default" in providers:
        result["default_provider"] = providers["default"]

    deepseek = providers.get("deepseek", {})
    if "api_key" in deepseek:
        result["deepseek_api_key"] = deepseek["api_key"]
    if "model" in deepseek:
        result["deepseek_model"] = deepseek["model"]
    if "base_url" in deepseek:
        result["deepseek_base_url"] = deepseek["base_url"]

    openai = providers.get("openai", {})
    if "api_key" in openai:
        result["openai_api_key"] = openai["api_key"]
    if "model" in openai:
        result["openai_model"] = openai["model"]
    if "base_url" in openai:
        result["openai_base_url"] = openai["base_url"]

    anthropic = providers.get("anthropic", {})
    if "api_key" in anthropic:
        result["anthropic_api_key"] = anthropic["api_key"]
    if "model" in anthropic:
        result["anthropic_model"] = anthropic["model"]

    ollama = providers.get("ollama", {})
    if "base_url" in ollama:
        result["ollama_base_url"] = ollama["base_url"]
    if "model" in ollama:
        result["ollama_model"] = ollama["model"]

    result.update(agent)

    if "max_size" in queue:
        result["queue_max_size"] = queue["max_size"]
    if "llm_worker_count" in queue:
        result["llm_worker_count"] = queue["llm_worker_count"]

    if "websocket" in data:
        result["websocket"] = data["websocket"]
    if "minecraft" in data:
        result["minecraft"] = data["minecraft"]
    if "mcp" in data:
        result["mcp"] = data["mcp"]

    if "level" in logging_config:
        result["log_level"] = logging_config["level"]
    if "enable_file_logging" in logging_config:
        result["enable_file_logging"] = logging_config["enable_file_logging"]
    if "enable_ws_raw_log" in logging_config:
        result["enable_ws_raw_log"] = logging_config["enable_ws_raw_log"]
    if "enable_llm_raw_log" in logging_config:
        result["enable_llm_raw_log"] = logging_config["enable_llm_raw_log"]

    if "dev_mode" in data:
        result["dev_mode"] = data["dev_mode"]

    if "max_chunk_content_length" in flow_control:
        result["max_chunk_content_length"] = flow_control["max_chunk_content_length"]
    if "chunk_sentence_mode" in flow_control:
        result["chunk_sentence_mode"] = flow_control["chunk_sentence_mode"]

    return result
```

- [ ] **步骤 4：新增自定义 JSON source**

在 `_flatten_json_config` 后插入以下代码：

```python
class EnvInterpolatedJsonConfigSettingsSource(JsonConfigSettingsSource):
    def __init__(self, settings_cls: type[BaseSettings]) -> None:
        super().__init__(
            settings_cls,
            json_file=CONFIG_FILE,
            json_file_encoding="utf-8",
        )

    def __call__(self) -> dict[str, Any]:
        if not CONFIG_FILE.exists():
            return {}
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("config.json root must be an object")
        resolved = _resolve_env_refs(data, _secret_environment())
        return _flatten_json_config(resolved)
```

- [ ] **步骤 5：修改 Settings model_config 和 source 优先级**

在 `Settings` 类中，将 `model_config` 替换为：

```python
    model_config = SettingsConfigDict(
        env_file=None,
        env_nested_delimiter="__",
        extra="ignore",
        populate_by_name=True,
    )
```

在 `model_config` 后插入：

```python
    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            EnvInterpolatedJsonConfigSettingsSource(settings_cls),
            file_secret_settings,
        )
```

- [ ] **步骤 6：移除大 JSON 环境变量字段和 validators**

从 `Settings` 类删除这些字段和方法：

```python
    mcp_enabled: bool = Field(
        default=False,
        alias="MCP_ENABLED",
        description="是否启用 MCP 服务器集成"
    )
    mcp_servers_json: str | None = Field(
        default=None,
        alias="MCP_SERVERS",
        description="MCP 服务器配置 JSON 字符串"
    )
    minecraft_commands_json: str | None = Field(
        default=None,
        alias="MINECRAFT_COMMANDS",
        description="通过 JSON 字符串配置命令 (可选)"
    )
```

同时删除 `merge_minecraft_commands()` 与 `merge_mcp_config()` 两个 `model_validator`。

- [ ] **步骤 7：保留 mcp 启用状态来自嵌套配置**

确认 `Settings` 类中仍有：

```python
    mcp: MCPConfig = Field(default_factory=MCPConfig)
```

确认 `MCPConfig` 中仍有：

```python
class MCPConfig(BaseModel):
    enabled: bool = False
    servers: dict[str, MCPServerConfig] = {}
```

- [ ] **步骤 8：运行新测试验证通过**

运行：

```bash
pytest tests/test_json_settings.py -v
```

预期：PASS，输出包含：

```text
5 passed
```

- [ ] **步骤 9：运行受影响测试**

运行：

```bash
pytest tests/test_mcp.py::TestSettingsMCPIntegration -v
```

预期：FAIL，因为旧 `mcp_servers_json` / `minecraft_commands_json` 测试还未迁移。

- [ ] **步骤 10：Commit 实现**

运行：

```bash
git add config/settings.py tests/test_json_settings.py
git commit -m "feat(config): load settings from json config"
```

预期：提交包含 JSON source、占位符解析和新测试。

---

### 任务 3：迁移 MCP 与 Minecraft 命令配置测试

**文件：**
- 修改：`tests/test_mcp.py`
- 测试：`tests/test_mcp.py::TestSettingsMCPIntegration`

- [ ] **步骤 1：替换 Settings MCP 集成测试类**

在 `tests/test_mcp.py` 中，将 `class TestSettingsMCPIntegration:` 整个类替换为：

```python
class TestSettingsMCPIntegration:
    """Settings MCP 集成测试"""

    def test_mcp_servers_official_format_from_config_json(self, tmp_path, monkeypatch):
        """测试官方格式 MCP servers JSON 文件配置"""
        import json

        monkeypatch.chdir(tmp_path)
        (tmp_path / "config.json").write_text(
            json.dumps(
                {
                    "mcp": {
                        "enabled": True,
                        "servers": {
                            "filesystem": {
                                "command": "npx",
                                "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
                            }
                        },
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        settings = Settings()

        assert settings.mcp.enabled is True
        assert "filesystem" in settings.mcp.servers
        assert settings.mcp.servers["filesystem"].command == "npx"

    def test_mcp_servers_simple_format_from_config_json(self, tmp_path, monkeypatch):
        """测试简化格式 MCP servers JSON 文件配置"""
        import json

        monkeypatch.chdir(tmp_path)
        (tmp_path / "config.json").write_text(
            json.dumps(
                {
                    "mcp": {
                        "enabled": True,
                        "servers": {
                            "filesystem": {
                                "command": "npx",
                                "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
                            }
                        },
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        settings = Settings()

        assert settings.mcp.enabled is True
        assert "filesystem" in settings.mcp.servers

    def test_minecraft_commands_from_config_json(self, tmp_path, monkeypatch):
        """测试 Minecraft 命令可通过 JSON 文件配置"""
        import json

        monkeypatch.chdir(tmp_path)
        (tmp_path / "config.json").write_text(
            json.dumps(
                {
                    "minecraft": {
                        "commands": {
                            "AGENT MCP": {
                                "type": "mcp",
                                "aliases": ["AGENT mcp", "AI MCP", "AI mcp"],
                                "description": "MCP 服务器管理",
                                "usage": "<list/status/reload>",
                            },
                            "测试": "help",
                        }
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        settings = Settings()

        assert "AGENT MCP" in settings.minecraft.commands
        assert settings.minecraft.commands["测试"] == "help"
        mcp_cmd = settings.minecraft.commands["AGENT MCP"]
        assert isinstance(mcp_cmd, dict)
        assert mcp_cmd["type"] == "mcp"
        assert "AGENT mcp" in mcp_cmd["aliases"]

    def test_plain_mcp_environment_json_is_ignored(self, tmp_path, monkeypatch):
        """测试旧 MCP_SERVERS 环境变量不会再配置 MCP"""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv(
            "MCP_SERVERS",
            '{"filesystem": {"command": "npx", "args": ["-y", "server"]}}',
        )
        monkeypatch.setenv("MCP_ENABLED", "true")

        settings = Settings()

        assert settings.mcp.enabled is False
        assert settings.mcp.servers == {}
```

- [ ] **步骤 2：运行迁移后的测试**

运行：

```bash
pytest tests/test_mcp.py::TestSettingsMCPIntegration -v
```

预期：PASS，输出包含：

```text
4 passed
```

- [ ] **步骤 3：运行配置相关测试集合**

运行：

```bash
pytest tests/test_json_settings.py tests/test_mcp.py::TestSettingsMCPIntegration -v
```

预期：PASS，输出包含：

```text
9 passed
```

- [ ] **步骤 4：Commit 测试迁移**

运行：

```bash
git add tests/test_mcp.py
git commit -m "test(config): migrate mcp settings tests to json"
```

预期：提交只包含 MCP 与 Minecraft 命令配置测试迁移。

---

### 任务 4：创建 JSON 与环境变量模板

**文件：**
- 创建：`config.example.json`
- 修改：`.env.example`
- 修改：`.gitignore`
- 测试：`tests/test_json_settings.py`

- [ ] **步骤 1：创建 `config.example.json`**

创建 `config.example.json`，写入：

```json
{
  "server": {
    "host": "0.0.0.0",
    "port": 8080
  },
  "auth": {
    "jwt_secret": "${SECRET_KEY}",
    "jwt_expiration": 1800,
    "default_password": "${WEBSOCKET_PASSWORD}"
  },
  "providers": {
    "default": "deepseek",
    "deepseek": {
      "api_key": "${DEEPSEEK_API_KEY}",
      "base_url": "https://api.deepseek.com",
      "model": "deepseek-chat"
    },
    "openai": {
      "api_key": null,
      "base_url": null,
      "model": "gpt-4o"
    },
    "anthropic": {
      "api_key": null,
      "model": "claude-sonnet-4-20250514"
    },
    "ollama": {
      "base_url": "http://localhost:11434",
      "model": "llama3"
    }
  },
  "agent": {
    "system_prompt": "请始终保持积极和专业的态度。回答尽量保持一段话不要太长，适当添加换行符，尽量不要使用markdown",
    "enable_reasoning_output": true,
    "max_history_turns": 20,
    "stream_sentence_mode": true,
    "llm_warmup_enabled": true,
    "mcwiki_base_url": "https://mcwiki.rice-awa.top",
    "dedup_external_messages": true,
    "tool_response_verbose": false
  },
  "queue": {
    "max_size": 100,
    "llm_worker_count": 2
  },
  "websocket": {
    "ping_interval": 30,
    "ping_timeout": 15,
    "close_timeout": 15,
    "max_size": 10485760,
    "max_queue": 32
  },
  "minecraft": {
    "commands": {
      "#登录": "login",
      "AGENT 聊天": {
        "type": "chat",
        "aliases": ["AGENT chat", "AI 聊天", "AI chat"],
        "description": "与 AI 对话",
        "usage": "<内容>"
      },
      "AGENT 脚本": {
        "type": "chat_script",
        "aliases": ["AGENT script", "AI 脚本", "AI script"],
        "description": "使用脚本事件发送",
        "usage": "<内容>"
      },
      "AGENT 保存": {
        "type": "save",
        "aliases": ["AGENT save", "AI save"],
        "description": "保存对话历史",
        "usage": null
      },
      "AGENT 上下文": {
        "type": "context",
        "aliases": ["AGENT context", "AI 上下文", "AI context"],
        "description": "管理上下文",
        "usage": "<启用/关闭/状态/清除/压缩/保存/恢复/列表/删除>"
      },
      "AGENT 模板": {
        "type": "template",
        "aliases": ["AGENT template", "AI 模板", "AI template"],
        "description": "切换提示词模板",
        "usage": "<模板名/list>"
      },
      "AGENT 设置": {
        "type": "setting",
        "aliases": ["AGENT setting", "AI 设置", "AI setting"],
        "description": "设置管理",
        "usage": "<变量/别名> <子命令>"
      },
      "AGENT MCP": {
        "type": "mcp",
        "aliases": ["AGENT mcp", "AI MCP", "AI mcp"],
        "description": "MCP 服务器管理",
        "usage": "<list/status/reload>"
      },
      "运行命令": {
        "type": "run_command",
        "aliases": ["runcmd", "cmd"],
        "description": "执行游戏命令",
        "usage": "<命令>"
      },
      "切换模型": {
        "type": "switch_model",
        "aliases": ["switch", "模型"],
        "description": "切换 LLM",
        "usage": "<provider>"
      },
      "帮助": {
        "type": "help",
        "aliases": ["help", "?"],
        "description": "显示此帮助",
        "usage": null
      }
    },
    "welcome_message_template": "-----------\n成功连接 MCBE AI Agent v2.2.0\n连接 ID: {connection_id}...\n当前模型: {provider}/{model}\n上下文: {context_status}\n-----------\n使用 \"{help_command}\" 查看可用命令",
    "context_enabled_text": "启用",
    "context_disabled_text": "关闭",
    "error_prefix": "❌ 错误: ",
    "info_prefix": "ℹ ",
    "success_prefix": "✅ ",
    "error_color": "§c",
    "info_color": "§b",
    "success_color": "§a"
  },
  "mcp": {
    "enabled": false,
    "servers": {}
  },
  "logging": {
    "level": "INFO",
    "enable_file_logging": true,
    "enable_ws_raw_log": false,
    "enable_llm_raw_log": false
  },
  "dev_mode": false,
  "flow_control": {
    "max_chunk_content_length": 400,
    "chunk_sentence_mode": true
  }
}
```

- [ ] **步骤 2：精简 `.env.example`**

将 `.env.example` 全文替换为：

```env
# MCBE AI Agent 敏感配置
# 普通配置请编辑 config.json；此文件只保存密钥、密码等敏感内容。

# 认证密钥
SECRET_KEY=change-me-in-production
WEBSOCKET_PASSWORD=123456

# DeepSeek API Key（默认 provider 使用）
DEEPSEEK_API_KEY=

# OpenAI API Key（如需启用，请在 config.json 中把 providers.openai.api_key 设置为 "${OPENAI_API_KEY}"）
OPENAI_API_KEY=

# Anthropic API Key（如需启用，请在 config.json 中把 providers.anthropic.api_key 设置为 "${ANTHROPIC_API_KEY}"）
ANTHROPIC_API_KEY=
```

- [ ] **步骤 3：忽略真实 `config.json`**

在 `.gitignore` 的 env files 区域加入：

```gitignore
config.json
```

该区域最终形态应为：

```gitignore
# env files
.venv/*
.env
.env.local
config.json
!MCBE-AI-Agent-addon/.env
```

- [ ] **步骤 4：验证模板 JSON 可解析**

运行：

```bash
python -m json.tool config.example.json >/dev/null
```

预期：命令退出码为 0，无输出。

- [ ] **步骤 5：Commit 模板文件**

运行：

```bash
git add config.example.json .env.example .gitignore
git commit -m "feat(config): add json config template"
```

预期：提交包含配置模板与忽略规则。

---

### 任务 5：更新 CLI 初始化与配置提示

**文件：**
- 修改：`cli.py`
- 测试：手动运行 `python cli.py init`、`python cli.py info`、`python cli.py mcp list`

- [ ] **步骤 1：修改 `init` 命令**

在 `cli.py` 中，将 `init()` 函数替换为：

```python
def init():
    """初始化配置文件"""
    env_file = Path(".env")
    env_example = Path(".env.example")
    config_file = Path("config.json")
    config_example = Path("config.example.json")

    if env_file.exists():
        click.confirm(".env 已存在，是否覆盖?", abort=True)
    if config_file.exists():
        click.confirm("config.json 已存在，是否覆盖?", abort=True)

    if not env_example.exists():
        click.echo(f"❌ 找不到模板文件: {env_example.absolute()}", err=True)
        sys.exit(1)
    if not config_example.exists():
        click.echo(f"❌ 找不到模板文件: {config_example.absolute()}", err=True)
        sys.exit(1)

    try:
        env_file.write_text(env_example.read_text(encoding="utf-8"), encoding="utf-8")
        config_file.write_text(config_example.read_text(encoding="utf-8"), encoding="utf-8")
        click.echo(f"✅ 敏感配置文件已创建: {env_file.absolute()}")
        click.echo(f"✅ 应用配置文件已创建: {config_file.absolute()}")
        click.echo("\n请编辑 .env 填入密钥，并按需编辑 config.json 调整普通配置")
    except Exception as e:
        click.echo(f"❌ 创建配置文件失败: {e}", err=True)
        sys.exit(1)
```

- [ ] **步骤 2：更新 `mcp list` 提示**

在 `mcp_list()` 中，将：

```python
        click.echo("提示: 设置 MCP_ENABLED=true 启用 MCP")
```

替换为：

```python
        click.echo("提示: 在 config.json 中设置 mcp.enabled=true 启用 MCP")
```

将：

```python
        click.echo("提示: 通过 MCP_SERVERS 环境变量配置服务器")
```

替换为：

```python
        click.echo("提示: 在 config.json 的 mcp.servers 中配置服务器")
```

- [ ] **步骤 3：手动验证 `init`**

运行：

```bash
rm -rf /tmp/mcbe-ai-agent-config-init && mkdir /tmp/mcbe-ai-agent-config-init && cp cli.py .env.example config.example.json /tmp/mcbe-ai-agent-config-init/ && cp -r config services core models /tmp/mcbe-ai-agent-config-init/ && cd /tmp/mcbe-ai-agent-config-init && python cli.py init
```

预期输出包含：

```text
✅ 敏感配置文件已创建:
✅ 应用配置文件已创建:
请编辑 .env 填入密钥，并按需编辑 config.json 调整普通配置
```

- [ ] **步骤 4：手动验证 `info` 可读取模板配置**

运行：

```bash
rm -rf /tmp/mcbe-ai-agent-config-info && mkdir /tmp/mcbe-ai-agent-config-info && cp -r /root/mcbe_ai_agent/* /tmp/mcbe-ai-agent-config-info/ && cd /tmp/mcbe-ai-agent-config-info && cp config.example.json config.json && cat > .env <<'EOF'
SECRET_KEY=test-secret
WEBSOCKET_PASSWORD=test-pass
DEEPSEEK_API_KEY=test-key
OPENAI_API_KEY=
ANTHROPIC_API_KEY=
EOF
python cli.py info
```

预期输出包含：

```text
服务器地址: 0.0.0.0:8080
默认 LLM: deepseek
✓ deepseek: deepseek-chat
✓ ollama: llama3
```

- [ ] **步骤 5：Commit CLI 更新**

运行：

```bash
git add cli.py
git commit -m "feat(cli): initialize json config file"
```

预期：提交包含 `init` 和 MCP 提示更新。

---

### 任务 6：更新项目文档中的配置说明

**文件：**
- 修改：`README.md`
- 修改：`AGENTS.md`
- 修改：`CLAUDE.md`
- 测试：人工检查文档中不再把普通配置列为 `.env` 变量

- [ ] **步骤 1：更新 README 快速开始配置段落**

在 `README.md` 的“3. 初始化配置”段落中，将原 `.env` 示例替换为：

```markdown
这会创建两个本地配置文件：

- `.env`：只保存密钥、密码等敏感内容，不提交到 Git。
- `config.json`：保存普通应用配置，不提交到 Git；模板来自 `config.example.json`。

先编辑 `.env` 填入密钥：

```env
SECRET_KEY=your-secret-key
WEBSOCKET_PASSWORD=your-password
DEEPSEEK_API_KEY=your-api-key-here
```

再按需编辑 `config.json`。JSON 字符串可以使用 `${VAR}` 引用 `.env` 或进程环境变量，例如：

```json
{
  "providers": {
    "deepseek": {
      "api_key": "${DEEPSEEK_API_KEY}",
      "base_url": "https://api.deepseek.com",
      "model": "deepseek-chat"
    }
  }
}
```

如果 `${VAR}` 指向的变量缺失或为空，服务启动会失败并显示对应 JSON 路径和变量名。
```

- [ ] **步骤 2：更新 README 开发模式说明**

将 README 中“方式二：环境变量”对应的开发模式示例：

```markdown
方式二：环境变量
```bash
# 在 .env 文件中设置
DEV_MODE=true
```
```

替换为：

```markdown
方式二：配置文件
```json
{
  "dev_mode": true
}
```
```

- [ ] **步骤 3：更新 AGENTS.md 配置章节**

将 `AGENTS.md` 的“配置”章节替换为：

```markdown
## 配置

项目使用 `config.json` 作为普通配置文件，使用 `.env` 保存敏感内容。运行 `python cli.py init` 会从 `.env.example` 和 `config.example.json` 创建本地文件。

- `.env`：只保存 `SECRET_KEY`、`WEBSOCKET_PASSWORD`、`DEEPSEEK_API_KEY`、`OPENAI_API_KEY`、`ANTHROPIC_API_KEY` 等密钥或密码。
- `config.json`：保存服务器地址、默认 provider、模型名、队列、日志、MCP、Minecraft 命令和流控配置。
- JSON 字符串支持 `${VAR}` 引用 `.env` 或进程环境变量；缺失或空值会导致启动失败并显示 JSON 路径和变量名。
- 不要再把 `HOST`、`PORT`、`DEFAULT_PROVIDER`、`MCP_SERVERS`、`MINECRAFT_COMMANDS` 等普通配置写入 `.env`。
```

- [ ] **步骤 4：更新 CLAUDE.md 配置章节**

将 `CLAUDE.md` 的“Configuration”章节替换为：

```markdown
## Configuration

配置分为普通配置和敏感配置：

- `config.json`：普通应用配置，运行 `python cli.py init` 后由 `config.example.json` 生成；不提交到 Git。
- `.env`：只保存密钥、密码等敏感内容，运行 `python cli.py init` 后由 `.env.example` 生成；不提交到 Git。

`.env` 保留的敏感项：
- `SECRET_KEY` - JWT 密钥
- `WEBSOCKET_PASSWORD` - 连接密码
- `DEEPSEEK_API_KEY` / `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` - LLM API Keys

普通配置在 `config.json` 中维护，包括：
- `server.host` / `server.port`
- `providers.default` 与各 provider 的 `model`、`base_url`、`api_key`
- `queue.llm_worker_count` / `queue.max_size`
- `agent.*`
- `logging.*`
- `mcp.enabled` / `mcp.servers`
- `minecraft.commands`
- `flow_control.max_chunk_content_length` / `flow_control.chunk_sentence_mode`

JSON 字符串可以通过 `${VAR}` 引用 `.env` 或进程环境变量。缺失或空值会导致启动失败并显示 JSON 路径和变量名。新增普通配置应进入 `config.json`，不要添加新的普通 `.env` 字段。
```

- [ ] **步骤 5：检查旧配置说明残留**

运行：

```bash
python - <<'PY'
from pathlib import Path
for path in [Path('README.md'), Path('AGENTS.md'), Path('CLAUDE.md')]:
    text = path.read_text(encoding='utf-8')
    for phrase in ['MCP_SERVERS 环境变量', 'MINECRAFT_COMMANDS', 'DEFAULT_PROVIDER：默认', 'DEV_MODE=true']:
        if phrase in text:
            raise SystemExit(f'{path}: found old config phrase {phrase!r}')
print('documentation config references updated')
PY
```

预期输出：

```text
documentation config references updated
```

- [ ] **步骤 6：Commit 文档更新**

运行：

```bash
git add README.md AGENTS.md CLAUDE.md
git commit -m "docs(config): document json configuration"
```

预期：提交包含三份文档的配置说明更新。

---

### 任务 7：运行完整验证并修正测试隔离问题

**文件：**
- 可能修改：`tests/test_json_settings.py`
- 可能修改：`tests/test_mcp.py`
- 测试：全量 pytest

- [ ] **步骤 1：运行全量测试**

运行：

```bash
pytest -v
```

预期：PASS。如果出现由于当前仓库真实 `config.json` 或 `.env` 影响测试的失败，继续步骤 2。

- [ ] **步骤 2：为直接实例化 `Settings()` 的测试添加 cwd 隔离**

如果某个测试因为仓库根目录的 `config.json` 或 `.env` 被加载而失败，在该测试函数参数加入 `tmp_path, monkeypatch`，并在测试开头加入：

```python
    monkeypatch.chdir(tmp_path)
```

示例修正：

```python
def test_context_compression_threshold(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    settings = Settings(max_history_turns=20)
    assert settings.max_history_turns == 20
```

- [ ] **步骤 3：重新运行全量测试**

运行：

```bash
pytest -v
```

预期：PASS，输出最后一行包含：

```text
passed
```

- [ ] **步骤 4：Commit 测试隔离修正**

如果步骤 2 修改了测试文件，运行：

```bash
git add tests
git commit -m "test(config): isolate settings tests from local config"
```

预期：提交只包含测试隔离修正。

如果步骤 2 没有修改任何文件，运行：

```bash
git status --short
```

预期：不需要创建提交。

---

### 任务 8：执行最终配置行为验证

**文件：**
- 不修改文件
- 测试：CLI 与 pytest 验证

- [ ] **步骤 1：验证旧普通环境变量不会覆盖配置**

运行：

```bash
rm -rf /tmp/mcbe-ai-agent-old-env && mkdir /tmp/mcbe-ai-agent-old-env && cp -r /root/mcbe_ai_agent/* /tmp/mcbe-ai-agent-old-env/ && cd /tmp/mcbe-ai-agent-old-env && HOST=10.1.1.1 PORT=9999 DEFAULT_PROVIDER=anthropic python - <<'PY'
from config.settings import Settings
settings = Settings()
print(settings.host, settings.port, settings.default_provider)
PY
```

预期输出：

```text
0.0.0.0 8080 deepseek
```

- [ ] **步骤 2：验证 JSON 和 `.env` 联合生效**

运行：

```bash
rm -rf /tmp/mcbe-ai-agent-json-env && mkdir /tmp/mcbe-ai-agent-json-env && cp -r /root/mcbe_ai_agent/* /tmp/mcbe-ai-agent-json-env/ && cd /tmp/mcbe-ai-agent-json-env && cp config.example.json config.json && cat > .env <<'EOF'
SECRET_KEY=test-secret
WEBSOCKET_PASSWORD=test-pass
DEEPSEEK_API_KEY=test-deepseek-key
OPENAI_API_KEY=
ANTHROPIC_API_KEY=
EOF
python - <<'PY'
from config.settings import Settings
settings = Settings()
print(settings.jwt_secret)
print(settings.default_password)
print(settings.get_provider_config('deepseek').api_key)
PY
```

预期输出：

```text
test-secret
test-pass
test-deepseek-key
```

- [ ] **步骤 3：验证缺失变量错误清晰**

运行：

```bash
rm -rf /tmp/mcbe-ai-agent-missing-env && mkdir /tmp/mcbe-ai-agent-missing-env && cp -r /root/mcbe_ai_agent/* /tmp/mcbe-ai-agent-missing-env/ && cd /tmp/mcbe-ai-agent-missing-env && cp config.example.json config.json && cat > .env <<'EOF'
SECRET_KEY=test-secret
WEBSOCKET_PASSWORD=test-pass
DEEPSEEK_API_KEY=
OPENAI_API_KEY=
ANTHROPIC_API_KEY=
EOF
python - <<'PY'
from config.settings import Settings
Settings()
PY
```

预期：命令失败，错误包含：

```text
providers.deepseek.api_key
DEEPSEEK_API_KEY
```

- [ ] **步骤 4：运行完整测试套件**

运行：

```bash
pytest -v
```

预期：PASS，输出最后一行包含：

```text
passed
```

- [ ] **步骤 5：检查工作区状态**

运行：

```bash
git status --short
```

预期：只允许存在未提交的计划文档；实现相关文件应已提交。

---

## 自检结果

- 规格覆盖度：
  - JSON 主配置：任务 1、2、4 覆盖。
  - `.env` 仅密钥：任务 2、4、8 覆盖。
  - `${VAR}` 递归替换、缺失/空值报错：任务 1、2、8 覆盖。
  - 不保留旧普通环境变量兼容：任务 1、2、3、8 覆盖。
  - CLI 初始化与提示：任务 5 覆盖。
  - README/AGENTS/CLAUDE 文档：任务 6 覆盖。
  - 测试验证：任务 1、3、7、8 覆盖。
- 占位符扫描：计划未使用未定义实现占位语句；所有代码步骤均包含具体代码或命令。
- 类型一致性：计划中新增函数 `_parse_dotenv_line`、`_load_dotenv`、`_secret_environment`、`_resolve_env_refs`、`_flatten_json_config`、`EnvInterpolatedJsonConfigSettingsSource` 均在任务 2 定义，后续任务不引用未定义类型。

---

## 执行交接

计划已完成并保存到 `docs/superpowers/plans/2026-06-06-json-config.md`。两种执行方式：

**1. 子代理驱动（推荐）** - 每个任务调度一个新的子代理，任务间进行审查，快速迭代。

**2. 内联执行** - 在当前会话中使用 executing-plans 执行任务，批量执行并设有检查点。

选哪种方式？
