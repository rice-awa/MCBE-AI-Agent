# 开发指南

面向本仓库开发者的指南。术语与语言边界见 [`CONTEXT.md`](../CONTEXT.md)，协作与 Git 规范见 [`AGENTS.md`](../AGENTS.md)。

## 环境准备

- Python 3.11+，建议使用虚拟环境
- 依赖安装：`pip install -r requirements.txt`
- 配置初始化：`python cli.py init`（生成 `config.json` 与 `.env`，均不入库）

常用命令：

```bash
python cli.py init           # 初始化配置
python cli.py info           # 查看配置信息
python cli.py test-provider deepseek   # 测试 LLM 连接
python cli.py serve          # 启动服务器
pytest                       # 运行测试（live 类用例默认跳过）
ruff check .                 # 静态检查（以仓库配置为准）
```

## 目录速览

| 路径 | 职责 |
|------|------|
| `cli.py` | 应用入口；组装 `HostGatewayServer` + AgentWorkers |
| `config/` | Pydantic Settings、日志、脱敏 |
| `core/` | `MessageBroker` 队列、会话、对话压缩/存储 |
| `models/` | 常量与 Pydantic 模型 |
| `services/gateway/` | SDK 适配层：server / hook / 命令 / 出站桥 |
| `services/agent/` | PydanticAI Agent、Worker、Tools、Provider 注册表 |
| `MCBE-AI-Agent-addon/` | Minecraft 行为包 + 资源包（构建见其 README） |
| `docs/` | 协议、部署、开发文档 |

## 添加新的 LLM Provider

Provider 注册表在 `services/agent/providers.py` 的 `RuntimeAdapterRegistry`。注册一个 provider 分两步：

1. 新增模型创建方法：

```python
def _create_custom_model(self, config: LLMProviderConfig) -> Model:
    from custom_provider import CustomModel  # 实现 pydantic_ai.models.Model
    return CustomModel(config.model, api_key=config.api_key)
```

2. 在 `get_model()` 中注册分发：

```python
elif provider_name == "custom":
    model = self._create_custom_model(config)
```

随后在 `config/settings.py` 的 provider 配置模型与 `config.example.json` 中补充 `custom` 段的 `api_key` / `base_url` / `model` 字段，并在 `providers.default` 或游戏内 `切换模型` 中选用。

## 添加新的 Agent Tool

内置工具集中在 `services/agent/tools.py` 的 `register_agent_tools(chat_agent, settings)` 中注册。新增工具：

```python
@chat_agent.tool
async def your_tool(ctx: RunContext[AgentDependencies], param: str) -> str:
    """工具描述（会进入模型上下文，务必写清参数与边界）"""
    # 实现逻辑，注意不要阻塞事件循环
    return "结果"
```

要点：

- 工具描述是给模型看的契约，说明参数格式、失败返回约定（尽量返回稳定 `code`）。
- 高风险命令类工具应接入 runtime harness 的审批/拒绝策略（`agent.runtime_harness.*` 配置），不要绕过。
- 工具内禁止长阻塞调用；需要网络/命令执行时用异步封装并受 `run_command_timeout` 等配置约束。

## 自定义游戏内命令

命令的**唯一真源**是 `config/settings.py` 的 `commands` 默认值（前缀 → 命令类型）；`config.json` 的 `minecraft.commands` 可覆盖。游戏内 `帮助` 由 `command_help` 表生成。

1. 在 `config/settings.py` 的 `commands` 默认值中添加条目（类型 + 别名 + 描述 + 用法）：

```python
"AGENT 示例": {
    "type": "example_cmd",
    "aliases": ["AGENT example"],
    "description": "示例命令",
    "usage": "<参数>"
},
```

2. 在 `command_help` 表中补充该类型的帮助文案：

```python
"example_cmd": ("示例命令", "<参数>"),
```

3. 在 `services/gateway/command_handlers.py` 的 `handle_command()` 分发表中挂接处理器：

```python
"example_cmd": lambda: self.handle_example(state, content, player_name=player_name),
```

4. 实现处理器方法；涉及玩家相关状态时**必须显式携带 `player_name`**（多人共享同一 `/wsserver` 连接，禁止依赖 `ConnectionState.player_name`）。

## Git 工作流

- 新功能 / 修复在 `feature/*` / `fix/*` 分支开发，从 `dev` 拉出，合回 `dev`；`master` 只接收已验证的 `dev`。
- 提交信息遵循 Conventional Commits（`feat(chat): ...` / `fix(agent): ...`）。
- 详细规范见 [`AGENTS.md`](../AGENTS.md)。

## 出站消息与流控

- 出站长文本一律走 `BrokerResponseBridge` 或 SDK delivery（`McbeOutboundDelivery` / `McbewsV1Delivery`），不要在调用点自行分片。
- 流控参数（`flow_control.*`）映射到 SDK `FlowControlSettings`；新增下行路径时复用现有 delivery。

## 测试

- `pytest`：离线用例（`-m 'not live'`）覆盖队列、会话隔离、命令、harness 提示词等；live 用例需要真实 LLM/连接。
- 新增功能或修复 bug 时补充相邻测试，改动涉及下行发送路径时验证分片与 player 维度隔离。
