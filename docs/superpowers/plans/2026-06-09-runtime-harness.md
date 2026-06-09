# 运行时 Harness 实现计划

> **面向 AI 代理的工作者：** 此计划先实现 MCBE Chat Agent 的运行时 Harness 第一阶段。优先完成工具智能化的 feedforward：工具目录、工具提示和 tool schema 增强；工具审计、反馈建议和更强执行边界作为后续阶段推进。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 为 MCBE Chat Agent 建立第一版运行时 Harness，让模型在调用 Minecraft、Wiki、Addon 和系统信息工具时看到一致、简短、可维护的工具决策指引。

**架构：** 在 `services/agent/harness/` 下新增运行时 Harness 模块。工具目录是 Python 真相源，按工具意图组织工具，并为每个工具声明风险级别、适用场景、禁用场景、参数约束和参数预览策略。第一批实现把工具目录渲染为系统提示中的简短决策树和工具卡片，同时用目录内容前缀增强 PydanticAI tool schema description；缺少目录条目的已注册工具在测试中视为失败。

**技术栈：** Python 3.11、PydanticAI、Pydantic Settings、Click、pytest、现有 PromptManager/Agent 工具注册流程。

---

## 术语边界

- 使用 **运行时 Harness** 指在线 MCBE Chat Agent 的上下文、约束、工具执行边界、审计和反馈机制。
- 使用 **开发维护 Harness** 指未来 coding agent 维护本仓库所需的规则、验证流程和文档入口。
- 第一批实现只触碰运行时 Harness，不把 coding agent 维护规则混入工具目录。
- CLI 使用 `runtime-harness` 命名，避免裸用 Harness 与 glossary 冲突。

## 已定设计

- 工具目录：独立 Python catalog，位于 `services/agent/harness/`。
- 工具意图：改变世界、通知展示、查询世界、查询知识、系统信息。
- 风险级别：低、中、高、危险。
- 工具提示：按意图组织的简短决策树 + 每个工具 1-3 行短卡片。
- 工具卡片字段：适用场景、禁用场景、参数约束。
- Schema 增强：用 catalog 生成 description 前缀，保留原函数 docstring。
- 缺省策略：所有已注册 PydanticAI 工具必须有 catalog 条目，测试强一致。
- 配置命名：`agent.runtime_harness.*`。
- 默认开关：第一版提示增强默认开启。

---

## 文件结构

- 新增：`services/agent/harness/__init__.py`
  - 暴露运行时 Harness 的 prompt/schema helper。
- 新增：`services/agent/harness/catalog.py`
  - 定义 `ToolIntent`、`ToolRisk`、`ParameterPreviewPolicy`、`ToolCatalogEntry` 和工具目录。
- 新增：`services/agent/harness/prompting.py`
  - 渲染工具意图决策树、短工具卡片和 schema description 前缀。
- 修改：`services/agent/prompt.py`
  - 将静态 `TOOL_USAGE_GUIDE` 替换或扩展为运行时 Harness 生成的工具提示。
- 修改：`services/agent/tools.py`
  - 注册工具时使用 catalog 前缀增强 PydanticAI tool description。
- 修改：`config/settings.py`
  - 增加 `agent.runtime_harness.*` 配置字段和 JSON flatten 映射。
- 修改：`config.example.json`
  - 在 `agent.runtime_harness` 中加入第一批配置示例。
- 修改：`config.json`
  - 同步加入运行时配置，避免当前运行配置缺失必填路径。
- 新增：`tests/test_runtime_harness_catalog.py`
  - 覆盖 catalog 字段合法性和已注册工具强一致。
- 新增：`tests/test_runtime_harness_prompt.py`
  - 覆盖决策树、短工具卡片和提示注入。
- 新增或修改：`tests/test_agent_tools.py`
  - 覆盖 tool schema description 前缀增强。
- 修改：`tests/test_json_settings.py`
  - 覆盖新增 JSON 配置项可加载。

---

## 第一阶段：工具提示增强

### 任务 1：添加运行时 Harness 配置

**文件：**
- 修改：`config/settings.py`
- 修改：`config.example.json`
- 修改：`config.json`
- 测试：`tests/test_json_settings.py`

- [ ] **步骤 1：编写配置加载测试**

在 `tests/test_json_settings.py` 中新增测试，断言：

```python
settings = Settings()
assert settings.runtime_harness_enabled is True
assert settings.runtime_harness_prompt_enabled is True
assert settings.runtime_harness_schema_enabled is True
```

- [ ] **步骤 2：扩展 Settings 字段**

在 `Settings` 的 Agent 配置区域加入：

```python
runtime_harness_enabled: bool = True
runtime_harness_prompt_enabled: bool = True
runtime_harness_schema_enabled: bool = True
```

在 `REQUIRED_CONFIG_PATHS` 中加入：

```python
"agent.runtime_harness.enabled",
"agent.runtime_harness.prompt_enabled",
"agent.runtime_harness.schema_enabled",
```

- [ ] **步骤 3：处理 JSON flatten**

在 `_flatten_json_config()` 读取 `agent.runtime_harness`，映射为扁平字段：

```python
runtime_harness = agent.get("runtime_harness", {})
if "enabled" in runtime_harness:
    result["runtime_harness_enabled"] = runtime_harness["enabled"]
if "prompt_enabled" in runtime_harness:
    result["runtime_harness_prompt_enabled"] = runtime_harness["prompt_enabled"]
if "schema_enabled" in runtime_harness:
    result["runtime_harness_schema_enabled"] = runtime_harness["schema_enabled"]
```

`result.update(agent)` 保留不变；嵌套 dict 会被 `extra="ignore"` 忽略，不作为 Settings 字段使用。

- [ ] **步骤 4：更新 JSON 配置**

在 `config.example.json` 和 `config.json` 的 `agent` 块中加入：

```json
"runtime_harness": {
  "enabled": true,
  "prompt_enabled": true,
  "schema_enabled": true
}
```

- [ ] **步骤 5：运行配置测试**

```bash
pytest tests/test_json_settings.py -v
```

预期：PASS。

---

### 任务 2：实现工具目录真相源

**文件：**
- 新增：`services/agent/harness/__init__.py`
- 新增：`services/agent/harness/catalog.py`
- 测试：`tests/test_runtime_harness_catalog.py`

- [ ] **步骤 1：定义 catalog 类型**

在 `catalog.py` 中定义：

```python
from enum import StrEnum
from pydantic import BaseModel, Field

class ToolIntent(StrEnum):
    CHANGE_WORLD = "改变世界"
    NOTIFY_DISPLAY = "通知展示"
    QUERY_WORLD = "查询世界"
    QUERY_KNOWLEDGE = "查询知识"
    SYSTEM_INFO = "系统信息"

class ToolRisk(StrEnum):
    LOW = "低"
    MEDIUM = "中"
    HIGH = "高"
    DANGEROUS = "危险"

class ParameterPreviewPolicy(BaseModel):
    include: tuple[str, ...] = ()
    sensitive: tuple[str, ...] = ()
    max_length: int = 120

class ToolCatalogEntry(BaseModel):
    name: str
    intent: ToolIntent
    risk: ToolRisk
    when_to_use: str
    when_not_to_use: str
    parameter_constraints: str
    preview: ParameterPreviewPolicy = Field(default_factory=ParameterPreviewPolicy)
```

- [ ] **步骤 2：补齐现有工具目录**

目录必须覆盖 `services/agent/tools.py` 中所有 PydanticAI 工具：

```text
run_minecraft_command
run_minecraft_commands
send_game_message
send_colored_message
send_title_message
send_actionbar_message
send_script_event
fetch_url_text
mcwiki_search
mcwiki_get_page
list_available_providers
get_player_snapshot
get_inventory_snapshot
find_entities
run_world_command
```

意图建议：

| 工具 | 意图 | 风险 |
| --- | --- | --- |
| run_minecraft_command | 改变世界 | 高 |
| run_minecraft_commands | 改变世界 | 危险 |
| send_game_message | 通知展示 | 低 |
| send_colored_message | 通知展示 | 中 |
| send_title_message | 通知展示 | 中 |
| send_actionbar_message | 通知展示 | 低 |
| send_script_event | 通知展示 | 中 |
| fetch_url_text | 查询知识 | 中 |
| mcwiki_search | 查询知识 | 低 |
| mcwiki_get_page | 查询知识 | 低 |
| list_available_providers | 系统信息 | 低 |
| get_player_snapshot | 查询世界 | 低 |
| get_inventory_snapshot | 查询世界 | 中 |
| find_entities | 查询世界 | 中 |
| run_world_command | 改变世界 | 高 |

- [ ] **步骤 3：提供 catalog API**

实现：

```python
def get_tool_catalog() -> dict[str, ToolCatalogEntry]: ...
def get_tool_entry(name: str) -> ToolCatalogEntry | None: ...
def list_tool_names() -> set[str]: ...
def group_tools_by_intent() -> dict[ToolIntent, list[ToolCatalogEntry]]: ...
```

- [ ] **步骤 4：编写 catalog 测试**

测试内容：

- 每个条目的 name 与 key 一致。
- intent/risk 取值合法。
- when_to_use、when_not_to_use、parameter_constraints 非空。
- 所有现有 PydanticAI 工具名都在 catalog 中。

强一致测试可先在测试中维护 `REGISTERED_AGENT_TOOL_NAMES` 常量，后续如能从 PydanticAI Agent introspect，再替换为自动提取。

- [ ] **步骤 5：运行 catalog 测试**

```bash
pytest tests/test_runtime_harness_catalog.py -v
```

预期：PASS。

---

### 任务 3：渲染工具提示

**文件：**
- 新增：`services/agent/harness/prompting.py`
- 修改：`services/agent/prompt.py`
- 测试：`tests/test_runtime_harness_prompt.py`

- [ ] **步骤 1：实现提示渲染器**

在 `prompting.py` 中实现：

```python
def render_tool_decision_tree() -> str: ...
def render_tool_cards() -> str: ...
def render_runtime_harness_prompt() -> str: ...
```

提示内容保持短卡片风格：

```text
你可以使用工具与 Minecraft 交互。先判断玩家意图，再选择风险最低且能完成目标的工具。

工具意图决策：
- 改变世界：玩家明确要求执行命令、修改世界或改变实体状态时使用。
- 通知展示：玩家要求在游戏中展示消息、标题、actionbar 或脚本事件时使用。
- 查询世界：玩家要求查询当前玩家、背包、实体或世界状态时使用。
- 查询知识：玩家要求查询 Wiki、网页或 Minecraft 知识资料时使用。
- 系统信息：玩家询问可用 provider 等系统状态时使用。

工具卡片：
- run_minecraft_command [改变世界/高]：用于单条明确 Minecraft 命令；不要用于闲聊或不确定命令；command 不带前导斜杠。
```

- [ ] **步骤 2：改造 PromptManager 注入点**

在 `PromptManager.build_system_prompt()` 中根据 settings 控制 `{tool_usage}`：

- `runtime_harness_enabled and runtime_harness_prompt_enabled` 为真时，使用 `render_runtime_harness_prompt()`。
- 否则回退到现有 `TOOL_USAGE_GUIDE`。

`build_system_prompt()` 当前没有 settings 参数。第一批可选择其中一种方式：

1. 推荐：给 `build_system_prompt()` 增加 `settings` 可选参数，并在 `build_dynamic_prompt()` 传入 `ctx.deps.settings`。
2. 或者只在 `build_dynamic_prompt()` 里计算 `tool_usage` 后传入，但需要调整模板变量构建方式。

- [ ] **步骤 3：编写 prompt 测试**

测试内容：

- 默认启用时，系统提示包含 `工具意图决策`。
- 默认启用时，系统提示包含至少一个工具卡片，例如 `run_minecraft_command [改变世界/高]`。
- 关闭 `runtime_harness_prompt_enabled` 时，回退到旧 `TOOL_USAGE_GUIDE` 文案。
- 自定义模板仍能通过 `{tool_usage}` 注入运行时 Harness 工具提示。

- [ ] **步骤 4：运行 prompt 测试**

```bash
pytest tests/test_runtime_harness_prompt.py -v
```

预期：PASS。

---

### 任务 4：增强 PydanticAI tool schema description

**文件：**
- 新增或修改：`services/agent/harness/prompting.py`
- 修改：`services/agent/tools.py`
- 测试：`tests/test_agent_tools.py` 或 `tests/test_runtime_harness_catalog.py`

- [ ] **步骤 1：实现 schema 前缀渲染**

在 `prompting.py` 中实现：

```python
def render_schema_description_prefix(tool_name: str) -> str:
    entry = get_tool_entry(tool_name)
    if entry is None:
        raise KeyError(tool_name)
    return (
        f"[运行时 Harness] 意图: {entry.intent}; 风险: {entry.risk}; "
        f"适用: {entry.when_to_use}; 禁用: {entry.when_not_to_use}; "
        f"参数: {entry.parameter_constraints}\n\n"
    )
```

- [ ] **步骤 2：选择 PydanticAI description 增强方式**

优先使用 PydanticAI 的工具 prepare/schema 修改能力，在工具注册时对 description 加前缀，避免手写重复 docstring。

如果当前 PydanticAI 版本不易在 `@chat_agent.tool` 上直接设置 description，则先用小 helper 包装每个工具的 docstring：

```python
def with_catalog_doc(tool_name: str, doc: str) -> str:
    return render_schema_description_prefix(tool_name) + doc.strip()
```

实现时必须保留原函数 docstring 内容作为前缀之后的补充。

- [ ] **步骤 3：编写 schema 测试**

测试内容：

- `render_schema_description_prefix("run_minecraft_command")` 包含意图、风险、适用、禁用、参数。
- 注册后的 PydanticAI tool description 包含 `[运行时 Harness]` 前缀。
- 关闭 `runtime_harness_schema_enabled` 时，不注入前缀。

如果 PydanticAI Agent 的工具 schema 难以稳定 introspect，则先测试渲染 helper，并在 `register_agent_tools()` 的实现上保持最小可读改动。

- [ ] **步骤 4：运行相关测试**

```bash
pytest tests/test_agent_tools.py tests/test_runtime_harness_catalog.py -v
```

预期：PASS。

---

### 任务 5：第一阶段整体验证

**文件：**
- 修改：`services/agent/prompt.py`
- 修改：`services/agent/tools.py`
- 测试：相关 runtime Harness 测试

- [ ] **步骤 1：运行定向测试**

```bash
pytest tests/test_json_settings.py tests/test_runtime_harness_catalog.py tests/test_runtime_harness_prompt.py tests/test_agent_tools.py -v
```

- [ ] **步骤 2：运行现有 Agent 相关测试**

```bash
pytest tests/test_agent_addon_tools.py tests/test_stream_mode.py tests/test_mcwiki.py -v
```

- [ ] **步骤 3：人工检查生成提示**

用测试或临时 Python 片段构建默认模板，确认：

- 提示内保留当前玩家、provider/model 和上下文信息。
- 工具提示短，不展开完整目录细节。
- 不包含工具审计、JSONL、CLI 等未实现阶段的运行时承诺。

- [ ] **步骤 4：阶段验收**

第一阶段完成条件：

- 所有现有工具都有 catalog 条目。
- 默认系统提示包含运行时 Harness 工具决策树和短卡片。
- tool schema description 可从 catalog 得到增强内容。
- 配置可关闭 prompt/schema 增强。
- 现有 Agent、Wiki、Addon、stream 相关测试不回归。

---

## 第二阶段方向：工具审计

**目标：** 建立结构化摘要审计，为反馈闭环提供数据，不记录完整工具结果。

**设计方向：**

- 新增：`services/agent/harness/audit.py`
- 配置：
  - `agent.runtime_harness.audit_enabled`: 默认 `true`
  - `agent.runtime_harness.audit_path`: 默认 `logs/runtime_harness_tools.jsonl`
  - `agent.runtime_harness.audit_max_records`: 默认 `5000`
- 审计挂载点：工具包装层。
- 审计字段：
  - 基础摘要：timestamp、tool_name、intent、risk、status、duration_ms。
  - 有限身份：player_name、provider、conversation_id、connection_id_short。
  - 参数预览：按 catalog 的 `ParameterPreviewPolicy` 声明 include/sensitive/max_length。
  - 结果摘要：success/failure、result_preview、failure_reason。
- 保留策略：按条数滚动保留。
- 测试：
  - 成功工具调用写 JSONL。
  - 失败工具调用写 JSONL。
  - 参数脱敏和截断生效。
  - 超过最大条数后滚动保留。

**注意：** 第二阶段仍不做硬拦截，继续遵守第一版提示约束。

---

## 第三阶段方向：反馈建议 CLI

**目标：** 用 CLI 聚合最近 N 条工具审计，输出诊断和反馈建议。

**设计方向：**

- CLI 命令：`python cli.py runtime-harness analyze`
- 参数：
  - `--recent N`：默认读取最近配置值。
  - `--json`：可选机器可读输出。
  - `--no-llm`：只输出规则聚合。
- 输出：
  - 总调用数、失败率、平均耗时、风险分布。
  - Top 问题工具：失败率高、危险工具频繁、参数错误集中、耗时高。
  - 反馈建议：先规则聚合，再用默认 provider 的无工具 Agent 生成可执行建议。
- LLM 建议策略：
  - 使用默认 provider。
  - 不注入 Minecraft 工具和 MCP toolsets。
  - 失败时回退规则模板。
  - 不自动修改 catalog、prompt、测试或配置。
- 测试：
  - 最近 N 条聚合正确。
  - Top 问题排序稳定。
  - `--no-llm` 可用。
  - LLM 失败回退规则建议。

---

## 第四阶段方向：执行边界增强

**目标：** 在提示约束之外，为高风险工具建立更可观察、可升级的执行边界，但仍避免一开始做过重权限系统。

**候选方向：**

- 高/危险工具在 prompt 中更明确要求玩家意图必须具体。
- 批量命令、世界修改命令等工具记录更细的风险原因。
- CLI 分析识别危险工具调用频率和失败原因。
- 后续可引入软确认或策略检查，但必须先有审计数据证明需要。

**不在第一版做：**

- 不在代码中硬拦截所有高风险工具。
- 不做玩家权限矩阵。
- 不自动重写玩家命令。

---

## 第五阶段方向：上下文管理约束

**目标：** 把运行时 Harness 从工具智能化扩展到上下文管理，让玩家会话、对话摘要和工具结果进入更清晰的上下文规则。

**候选方向：**

- 明确哪些工具结果可以进入长期对话历史，哪些只作为本轮执行结果。
- 对高噪声工具结果做摘要后再进入历史。
- 在对话压缩摘要中保留关键世界状态、玩家偏好和未完成任务。
- 为不同玩家会话继续保持 `(connection_id, player_name, conversation_id)` 隔离。
- 对 prompt 中的上下文使用情况加入更明确的模型行为约束。

---

## 第六阶段方向：开发维护 Harness 入口

**目标：** 为未来 coding agent 维护本仓库保留入口，但不与当前运行时 Harness 混用。

**候选方向：**

- 在 `CONTEXT.md` 中继续维护运行时 Harness 与开发维护 Harness 的术语边界。
- 后续如需要，新增单独文档说明 coding agent 维护规则、验证清单和高风险改动审查。
- 将项目级规则继续放在 `CLAUDE.md` 或等价 agent 指南中。
- 为重复失败的 coding-agent 维护问题补充测试、lint 或文档，而不是写进运行时工具目录。

**判断标准：** 只要规则服务于在线 MCBE Chat Agent，就属于运行时 Harness；只要规则服务于 coding agent 修改仓库，就属于开发维护 Harness。

---

## 风险与回滚

- Prompt 变长风险：第一批只使用短卡片，并允许通过 `agent.runtime_harness.prompt_enabled=false` 回退。
- Schema 兼容风险：保留原 docstring，catalog 只加前缀；允许通过 `agent.runtime_harness.schema_enabled=false` 回退。
- 目录漂移风险：测试强一致，新增工具必须补 catalog。
- 概念混淆风险：CLI 和配置使用 `runtime-harness` / `runtime_harness` 命名，不裸用 Harness。
- 后续审计隐私风险：只记录结构化摘要、有限身份和目录声明参数预览，不记录完整结果或玩家原始消息。

## 推荐实现顺序

1. 配置字段和 JSON 示例。
2. 工具目录类型与所有现有工具条目。
3. 工具提示渲染与 PromptManager 注入。
4. Schema description 前缀增强。
5. 定向测试和现有 Agent 相关测试。
6. 进入第二阶段工具审计设计与实现。
