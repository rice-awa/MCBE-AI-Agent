# 目录与模块边界

## 当前目录布局

```text
MCBE-AI-Agent/
├── cli.py                         # Click CLI 与服务启动入口
├── config/                        # Settings、日志、脱敏
├── core/                          # 队列、运行时会话、对话历史
├── models/                        # Pydantic 消息和领域模型、常量
├── services/
│   ├── agent/                     # Agent、Worker、Tools、Provider、Harness、Trace
│   ├── gateway/                   # mcbe-ws-sdk 的宿主适配层和命令路由
│   └── auth/                      # JWT 认证
├── tests/                         # Python pytest
├── docs/                          # 协议、开发和部署说明
├── web/trace/                     # Trace 只读工作台静态资源
├── MCBE-AI-Agent-addon/           # 独立的 Minecraft TypeScript Addon 工程
├── config.example.json            # 普通配置模板
└── _version.py                    # 版本唯一真源
```

README 的旧结构说明不能替代这个目录清单；新增模块时以文件系统和本指南为准。

## 模块职责

- `config/` 只负责配置装载、配置验证、日志构建和敏感字段处理。不要把 Agent 业务逻辑放进 Settings。
- `core/` 放不依赖具体 provider 或 Minecraft SDK 的运行时基础设施。`core/queue.py` 的 `MessageBroker` 连接 WebSocket 入站与 Agent Worker，`core/session.py` 定义会话键和锁。
- `models/` 放跨层消息契约和领域数据模型。新增线上的消息字段时，优先在这里扩展 Pydantic 模型，而不是让各消费者自行从 `dict` 取值。
- `services/agent/` 放 PydanticAI Agent、Worker、工具、Provider 生命周期、运行时 Harness 和 Trace。工具的 Minecraft 交互通过既有依赖注入和 Addon bridge，不直接改 Gateway 生命周期。
- `services/gateway/` 是 `mcbe-ws-sdk` 的宿主适配层：`server.py` 组装 Facade，`hook.py` 路由连接/事件，`command_handlers.py` 处理游戏内命令，`broker_bridge.py` 负责 Broker 响应到 SDK delivery 的转换。
- `services/auth/` 只维护 JWT 认证边界；不要在命令处理器中复制 token 解析规则。
- `tests/` 与被测边界保持平行命名，例如 `tests/test_queue_context.py` 覆盖队列/会话上下文，`tests/test_gateway_broker_bridge.py` 覆盖下行桥接。

本项目没有 ORM、数据库迁移或查询层。对话、token 统计、模型元数据和工具审计使用配置指定的 JSON/JSONL 文件；不要为了“整齐”新增 `database-guidelines` 这类虚假的抽象边界。

## 新增代码放置规则

1. 先在现有目录中找到拥有该职责的模块，再决定是否新增文件；不要把所有新逻辑都放到 `cli.py` 或一个“大工具模块”中。
2. 新的外部消息类型放 `models/messages.py`，新的跨模块领域值放 `models/`；仅限单个服务内部的输入投影才留在该服务。
3. 新的 Gateway 行为放 `services/gateway/`，但 WebSocket/分片/桥协议的通用实现仍由 `mcbe-ws-sdk` 拥有。
4. 新的 Agent 工具放 `services/agent/tools.py` 或对应的 `services/agent/block_ops/` 子模块，并补充工具结果、审计和失败路径测试。
5. 运行时数据目录 `data/`、日志目录 `logs/` 和本地 `config.json` / `.env` 不属于源代码提交；测试使用 `tmp_path` 或显式临时路径。

## 工具目录契约中心

`services/agent/harness/catalog.py` 的 `_TOOL_CATALOG` 是内置工具的契约中心。所有工具语义声明（意图、风险、适用/禁用场景、参数约束、审计预览）**必须**在目录中有唯一条目，且与实际注册结果一致。

### 核心规则

1. **一处声明、注册时核对、多个用途自动投影**：
   - 工具实际名称、参数结构、长说明仍来自 `tools.py` 文档字符串/PydanticAI 注册结果，**不在目录中复制完整参数模型**。
   - 目录保存语义声明：`ToolIntent`（改变世界/通知展示/查询世界/查询知识/系统信息）、`ToolRisk`（低/中/高/危险）、`when_to_use`、`when_not_to_use`、`parameter_constraints`、`preview` 策略。
   - MCP 工具保留 `source="mcp"` 标记和 `mcp_server`，不强行改造成内置工具。

2. **投影消费方**：
   - 工具提示（`TOOL_USAGE_GUIDE`）由 `project_tool_usage_guide()` 从目录投影，消除 `prompt.py` 与 `core.py` 的手工副本。
   - 工具决策树与工具卡片由 `harness/prompting.py` 从目录渲染。
   - 测试注册集合由 `list_tool_names()` 从目录投影驱动，移除手工维护的 `REGISTERED_AGENT_TOOL_NAMES`。

3. **校验**（由 `tests/test_runtime_harness_catalog.py` 自动执行）：
   - 每个内置工具在目录中有且只有一个条目。
   - 目录中不存在未注册工具。
   - 参数约束中引用的预览字段真实存在于约束文本。
   - CHANGE_WORLD 意图的工具风险不低于 HIGH。
   - 已移除工具（如 `edit_blocks`）不在目录中残留。

Python 使用 4 空格缩进，模块和函数使用 `snake_case`，类使用 `PascalCase`，常量使用 `UPPER_CASE`。公共导出由模块自己的 `__all__`（已有模块使用时）或明确的顶层定义表达，避免通过循环导入拼装隐式 API。

代表性实现：[`core/session.py`](../../../core/session.py)、[`services/gateway/server.py`](../../../services/gateway/server.py)、[`services/agent/harness/`](../../../services/agent/harness/)。
