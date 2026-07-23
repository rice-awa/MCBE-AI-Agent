# 方块工具审批恢复与错误可观测性修复设计

**日期：** 2026-07-22

**状态：** 已确认，待实施

**关联功能：** `inspect_block` / `edit_blocks`、运行时 Harness 工具审批与工具审计

**适用依赖：** PydanticAI 1.94.0

## 背景

`edit_blocks` 在玩家批准前会通过 Add-on bridge 执行只读预检，解析最终维度和绝对坐标、检查目标方块，并锁定批准后允许修改的目标。预检成功后，运行时 Harness 暂停当前 PydanticAI run；玩家批准时，宿主使用原消息历史和 `DeferredToolResults` 恢复同一个工具调用。

2026-07-22 的一次真实 `fill` 调用出现以下行为：

1. `edit_blocks phase=preflight` 成功，49 个目标均已匹配并锁定；
2. 玩家批准后，同一 `run_id` 和 `tool_call_id` 被恢复；
3. 没有产生 `edit_blocks phase=execute` bridge 请求；
4. 模型与主运行日志只得到 `工具执行失败: edit_blocks`；
5. 工具审计记录了真实异常：

```text
TypeError: register_agent_tools.<locals>.edit_blocks()
got an unexpected keyword argument 'repairs_applied'
```

因此故障不在 Add-on 方块写入、区块状态或角点顺序，而发生在宿主审批恢复后的 Python 工具调用边界。预检已将输入角点规范化为最小/最大边界，反向提供 `from_pos` 和 `to_pos` 是合法输入。

## 根因

当前实现用一个无类型的 canonical args 字典同时承担四种职责：

- 玩家审批参数；
- 策略与幂等哈希输入；
- 工具审计参数；
- Python 工具执行参数。

Add-on 预检响应包含两类字段：

- 操作语义字段，例如 `mode`、`dimension`、`from`、`to`、`type_id`、`locked_targets`；
- 结果与展示元数据，例如 `repairs_applied`、`facing`、`player_origin`、`before_samples`。

`merge_canonical_from_preflight()` 把 `repairs_applied` 和 `facing` 等元数据放入 canonical args。审批恢复时，`_python_tool_args()` 仅把 `from/to` 映射为 `from_pos/to_pos`，没有删除元数据。PydanticAI 随后用这些参数调用公开 Python 工具，但 `edit_blocks()` 的签名不接受 `repairs_applied`，因此在进入 `edit_blocks_impl()` 之前抛出 `TypeError`。

该问题影响范围为：

- `edit_blocks mode=place|batch|fill`：三个预检实现都会返回 `repairs_applied`；
- `edit_blocks coordinate_mode=player_relative`：还可能携带 `facing/player_origin`；
- `inspect_block coordinate_mode=player_relative`：相对坐标预检存在同类参数污染风险；
- `inspect_block coordinate_mode=absolute`：不需要单独预检，因此不受本故障影响。

## 测试缺口

现有测试分别验证了预检产生审批、预检缓存存在和 `edit_blocks_impl()` 可执行，但没有走完整的 PydanticAI 审批恢复边界。

“批准后使用缓存”的测试从缓存取得 canonical args 后，手工挑选合法字段直接调用 `edit_blocks_impl()`。这绕过了 `HarnessToolset.call_tool()`、`_python_tool_args()` 和 PydanticAI 函数参数校验，无法发现多余关键字参数。

本故障已通过以下最小完整链路稳定复现：

```text
FunctionModel
  -> edit_blocks 原始调用
  -> Add-on preflight
  -> DeferredToolRequests
  -> DeferredToolResults(approved)
  -> HarnessToolset 恢复 canonical args
  -> Python 工具参数校验失败
  -> execute bridge 调用数为 0
```

## 目标

- 玩家批准后执行的参数必须与批准时展示的规范化操作完全一致。
- 预检展示元数据不得进入 Python 工具函数调用。
- `place`、`batch` 和 `fill` 在批准后恰好执行一次，并使用锁定目标。
- 相对坐标在批准前解析，批准后不重新读取玩家位置或朝向。
- 审批恢复不再仅依赖进程内预检缓存携带执行参数。
- 主运行日志可通过 `run_id/tool_call_id` 直接定位脱敏后的真实异常类型和原因。
- 模型与玩家收到稳定、可解释的方块工具错误 JSON，但不接收 Python 堆栈、函数限定名或其他内部实现细节。
- 世界修改异常不会被自动重试；只有明确的 `ADDON_UNAVAILABLE` 才允许模型考虑另行审批命令工具。

## 非目标

- 不修改 Add-on 的方块读写算法、保护规则、回滚逻辑或桥协议。
- 不修改 `mcbe-ws-sdk`。
- 不引入跨进程审批恢复或持久化 checkpoint。
- 不放宽 Python 工具签名为任意 `**kwargs`。
- 不把完整异常堆栈或未脱敏参数发送给玩家、模型或普通信息日志。
- 不在本修复中处理方块工具限制字段的 top-level/nested 映射；该改动与本次审批恢复异常相互独立。
- 不重新设计通用 Minecraft 命令工具的审批策略。

## 方案比较

### 方案 A：仅删除已知元数据字段

在 `_python_tool_args()` 中删除 `repairs_applied`、`facing`、`origin` 和 `player_origin`。

优点是改动最小。缺点是使用黑名单维护执行边界，Add-on 以后增加新的预检元数据时会再次发生相同故障；审批恢复仍完全依赖全局进程内缓存。

### 方案 B：契约分离、严格投影并使用 `ToolApproved.override_args`

将预检产物拆分为授权参数、Python 执行参数和审批元数据。执行参数通过明确白名单生成，并在恢复时使用 PydanticAI 的 `ToolApproved(override_args=...)` 传递。预检缓存只用于短期幂等和一致性校验。

该方案改动跨越 block ops、审批存储、gateway 恢复和 worker 类型转换，但直接消除了职责混用，是本设计采用的方案。

### 方案 C：工具函数接受任意额外参数

为公开工具或实现增加 `**extra` 并静默忽略未知字段。

该方案会掩盖宿主与 Add-on 的协议漂移，使拼写错误或未来的安全相关字段被悄悄丢弃，不采用。

## 选定设计

### 预检计划契约

为方块预检引入一个有明确字段的内部结果，例如 `BlockPreflightPlan`：

```python
@dataclass(frozen=True)
class BlockPreflightPlan:
    authorized_args: dict[str, Any]
    execute_args: dict[str, Any]
    approval_metadata: dict[str, Any]
```

三个字段语义如下：

- `authorized_args`：玩家实际批准的规范操作，使用桥协议字段名；参与策略、审批摘要、授权哈希和工具审计。
- `execute_args`：传给 Python 工具的参数，使用 Python 签名字段名；只能由严格投影函数从 `authorized_args` 生成。
- `approval_metadata`：预检证据和展示信息，例如 repairs、朝向、玩家原点、目标计数、原方块摘要；不参与 Python 调用。

`authorized_args` 必须包含：

- 规范化后的 `coordinate_mode="absolute"`；
- 最终 `dimension`；
- 规范化的 `position/positions/from/to`；
- `mode/type_id/states/replace_any/expected_previous`；
- `locked_targets`；
- `phase="execute"`。

`repairs_applied`、`facing`、`player_origin`、`before_samples`、`previous_type_counts`、`volume`、`matched_count` 和 `ready` 等字段不得进入 `authorized_args` 或 `execute_args`。其中需要展示或审计的字段进入 `approval_metadata`。

### 严格执行参数投影

新增单一投影函数，按工具名返回允许进入 Python 调用的字段。不得通过“复制全部后删除已知保留字段”的方式构造执行参数。

`edit_blocks` 白名单固定为：

```text
type_id, mode, coordinate_mode, dimension,
position, positions, from_pos, to_pos,
states, replace_any, expected_previous,
locked_targets, phase
```

其中 `authorized_args.from/to` 映射为 `execute_args.from_pos/to_pos`。

`inspect_block` 白名单固定为：

```text
coordinate_mode, dimension, position, positions,
locked_targets, phase
```

投影函数必须满足：

- 输出 key 是相应 Python 工具签名的子集；
- 未知元数据不会进入输出；
- 缺少执行所需字段时在调用工具前返回内部契约错误；
- 输入相同则输出和哈希稳定；
- 审计仍使用 `authorized_args + approval_metadata`，不因执行投影丢失预检证据。

### 审批存储

`PendingApproval` 保留当前 owner、会话、批次、TTL 和消息历史字段，并明确保存：

- `normalized_args`：改为语义明确的 `authorized_args`；
- `execute_args`：批准后交给 PydanticAI 的严格参数；
- `args_hash`：`authorized_args` 的稳定哈希；
- `execution_args_hash`：`execute_args` 的稳定哈希；
- `metadata`：风险、原因和 `approval_metadata`。

玩家审批文案从 `authorized_args` 生成，并追加有界的修复摘要。大规模 `locked_targets` 不逐项展示，继续显示维度、边界、数量、目标方块和覆写策略；完整锁定目标只保存在进程内审批记录中。

审批记录仍受当前 owner 校验、批次决策和 120 秒 TTL 约束。过期后不得从模型原始参数重新预检并静默执行。

### PydanticAI 恢复

仓库实际安装的 PydanticAI 1.94.0 提供：

```python
ToolApproved(override_args: dict[str, Any] | None = None)
```

gateway 构造审批恢复 payload 时：

- 方块工具批准项使用 `{"kind": "tool-approved", "override_args": execute_args}`；
- 其他不需要改写参数的工具继续使用布尔 `true`；
- 拒绝项继续使用 `tool-denied`。

worker 的 `_coerce_deferred_tool_results()` 必须把 `kind="tool-approved"` 的字典转换为真正的 `ToolApproved` 实例。PydanticAI 随后以 `override_args` 替换原始 `ToolCallPart.args`，并为恢复的调用设置相同 `tool_call_id` 和已批准状态。

恢复后的 `execute_args` 已包含 `phase="execute"` 与 `locked_targets`，所以 Harness 不重新执行预检。预检缓存可以验证同一 `(run_id, tool_call_id)` 的授权哈希，但不得成为恢复执行参数的唯一来源。

### 幂等与授权一致性

幂等记录对实际副作用使用 `execution_args_hash`，键仍绑定：

```text
(run_id, tool_call_id, execution_args_hash)
```

恢复前验证：

- 当前 owner、连接、会话和批次与审批记录一致；
- `tool_call_id` 与原 deferred request 一致；
- `execute_args` 可由保存的 `authorized_args` 确定性投影得到；
- `locked_targets` 非空且审批未过期。

任何验证失败都终止恢复，不重新调用模型生成参数，也不回退到原始 `ToolCallPart.args`。

### 数据流

```text
Model ToolCallPart.args
        |
        v
run_block_preflight
        |
        +-- authorized_args ------> policy / approval / audit / auth hash
        |
        +-- approval_metadata ----> approval evidence / audit only
        |
        +-- execute_args ---------> PendingApproval
                                      |
Player approves                      v
        ----------------------> ToolApproved(override_args=execute_args)
                                      |
                                      v
                            HarnessToolset.call_tool
                                      |
                           strict projection / validation
                                      |
                                      v
                             edit_blocks Python tool
                                      |
                                      v
                         Add-on bridge phase=execute
```

## 错误处理与可观测性

### 内部结构化日志

`HarnessToolset.call_tool()` 捕获非审批异常时，必须在主运行日志记录一个脱敏结构化事件，例如 `tool_execution_failed`，至少包含：

- `tool_name`；
- `run_id`；
- `tool_call_id`；
- `connection_id_short`；
- 当前事件的 `player_name`；
- `error_kind`；
- `error_type`；
- 截断并脱敏后的 `diagnostic_summary`；
- `external_state_unknown`；
- `execution_stage`，取 `projection` 或 `invocation`。

主日志不记录完整参数、玩家聊天正文、堆栈或 bridge payload。工具审计继续记录目录允许的参数预览和脱敏失败原因，并与主日志使用同一 `run_id/tool_call_id`。

### 模型与玩家结果

方块工具内部失败继续使用版本化 JSON 契约：

```json
{
  "schema_version": "1",
  "ok": false,
  "code": "INTERNAL_ERROR",
  "message": "方块工具内部参数处理失败；本次操作未发送到 Add-on",
  "retryable": false,
  "external_state_unknown": false,
  "fallback_allowed": false
}
```

错误发生在严格投影阶段时，可以确认未调用 Add-on，使用 `INTERNAL_ERROR` 和 `external_state_unknown=false`。

一旦进入世界修改工具调用边界后出现未分类异常，应保守返回 `STATE_UNKNOWN`、`external_state_unknown=true`、`retryable=false`。明确的 Add-on 错误响应继续保留其稳定错误码和已知状态，不被统一改写。

只有 `ADDON_UNAVAILABLE` 可以返回 `fallback_allowed=true` 并说明命令工具需要独立审批。`INTERNAL_ERROR` 和 `STATE_UNKNOWN` 不建议模型改用 `setblock/fill` 继续执行，避免在状态未知时扩大副作用。

原始异常不得进入模型或玩家结果。玩家需要反馈问题时，可使用日志中已有的短审批 ID、`run_id` 或 `tool_call_id` 进行关联。

## 测试设计

### 完整审批恢复测试

在最高公共 Agent 契约上增加参数化测试，使用 `FunctionModel` 和受控 bridge，覆盖：

1. `place` 预检、批准、恢复后产生一次 `phase=execute`；
2. `batch` 预检、批准、恢复后产生一次 `phase=execute`；
3. `fill` 使用反向角点，批准时显示规范化边界，恢复后产生一次 `phase=execute`；
4. 三种模式的 execute payload 都携带预检锁定目标；
5. 同一批准结果重复恢复时不产生第二次副作用；
6. 拒绝和过期审批不产生 execute 请求。

这些测试必须使用真实 `DeferredToolRequests -> DeferredToolResults -> agent.run(message_history=...)` 流程，不允许手工调用 `edit_blocks_impl()` 代替恢复边界。

### 参数隔离测试

- Add-on preflight 返回 `repairs_applied/facing/player_origin/before_samples` 时，Python execute args 不包含这些字段。
- `authorized_args.from/to` 确定性映射为 `execute_args.from_pos/to_pos`。
- 投影函数输出字段全部属于公开工具签名。
- 新增未知预检元数据时，审批元数据可以保留，但执行参数不变化。
- 缺少 `locked_targets` 或 `phase=execute` 时，批准恢复在 bridge 调用前失败。

### 相对坐标测试

- `inspect_block coordinate_mode=player_relative` 的预检结果可直接进入查询执行，不受 repairs/facing 元数据影响。
- `edit_blocks coordinate_mode=player_relative` 在玩家审批等待期间移动或转向后，仍执行已保存的绝对目标。
- 当前事件的 `player_name` 从请求到预检、审批记录和恢复执行全程显式传递。

### 错误语义测试

- 投影错误生成 `INTERNAL_ERROR` JSON，标记未发送 Add-on、不可重试、不可 fallback。
- invocation 未分类异常对 `edit_blocks` 生成 `STATE_UNKNOWN`，不可重试。
- 主日志事件包含 `run_id/tool_call_id/error_type/diagnostic_summary`，但模型结果不包含 Python 异常详情。
- 工具审计和主日志可由同一标识关联。
- `ADDON_UNAVAILABLE` 保留唯一允许命令 fallback 的语义。

### 回归门禁

先运行相邻测试：

```bash
PYTHONPATH=. ./.venv/bin/python -m pytest \
  tests/test_block_ops.py \
  tests/test_runtime_harness_execution.py \
  tests/test_tool_approval_command.py -q
```

再运行 Add-on 方块测试和完整 Python 测试：

```bash
cd MCBE-AI-Agent-addon && npm test -- tests/bridge/blocks
PYTHONPATH=. ./.venv/bin/python -m pytest -q
```

真实 Minecraft 验收至少覆盖绝对 `fill`、玩家相对 `place`、拒绝、审批过期和 Add-on 不可用五条路径。

## 预计修改文件

- `services/agent/block_ops/tools_impl.py`
  - 拆分预检计划；实现 authorized/execute/metadata 契约和严格投影。
- `services/agent/block_ops/preflight_cache.py`
  - 缓存授权哈希与执行参数，作为校验和幂等辅助。
- `services/agent/harness/execution.py`
  - 使用严格参数；区分 projection/invocation 错误；记录结构化失败事件。
- `services/agent/harness/approvals.py`
  - 在 `PendingApproval` 中保存 `execute_args` 与执行参数哈希。
- `services/agent/worker.py`
  - 写入新的审批字段；识别并构造 `ToolApproved`。
- `services/gateway/command_handlers.py`
  - 对方块工具批准项生成带 `override_args` 的恢复 payload。
- `services/agent/tools.py`
  - 保持严格公开签名，不增加 `**extra`。
- `services/agent/block_ops/schema.py`
  - 集中构造 `INTERNAL_ERROR` 和 `STATE_UNKNOWN` 的稳定外部响应字段。
- `tests/test_block_ops.py`
  - 增加完整审批恢复、参数隔离和相对坐标覆盖。
- `tests/test_runtime_harness_execution.py`
  - 覆盖 `ToolApproved.override_args`、幂等和错误分类。
- `tests/test_tool_approval_command.py`
  - 覆盖 gateway payload 与 worker 类型恢复。

除非自动化测试证明 Add-on 返回契约存在独立问题，本修复不修改 `MCBE-AI-Agent-addon/` 生产源码。

## 实施顺序

1. 先增加真实 `fill` 审批恢复失败测试，确认 execute bridge 调用为 0。
2. 增加预检计划和严格执行参数投影，使测试只消除参数污染。
3. 扩展 `PendingApproval`、gateway payload 和 worker 转换，接入 `ToolApproved.override_args`。
4. 补齐 place/batch、相对坐标、拒绝、过期和重复恢复测试。
5. 增加主日志事件与方块工具稳定内部错误响应。
6. 运行相邻测试、Add-on 方块测试、完整 Python 测试和真实 Minecraft 验收。

每一步只处理一个可验证行为；不得通过让工具函数接受未知参数使首个失败测试转绿。

## 验收标准

- `place`、`batch`、`fill` 均能在玩家批准后产生且只产生一次 `phase=execute` bridge 请求。
- execute payload 使用预检锁定的绝对目标，审批期间玩家移动不改变目标。
- `repairs_applied/facing/player_origin/before_samples` 等预检元数据不会进入 Python 工具调用。
- 审批恢复使用 PydanticAI `ToolApproved.override_args`，删除预检缓存不会导致回退到未规范化原始参数执行。
- 重复恢复相同 `run_id/tool_call_id/execution_args_hash` 不重复修改世界。
- 主日志能直接看到脱敏异常类型和原因，不再只能看到 `工具执行失败: edit_blocks`。
- 模型与玩家收到版本化稳定错误码，不包含 Python 函数名、堆栈或敏感参数。
- 世界修改的未知状态失败不可自动重试；除 `ADDON_UNAVAILABLE` 外不建议命令 fallback。
- 相邻测试、Add-on 方块测试和完整 Python 测试通过；无法运行的真实 Minecraft 场景在交付说明中明确列出。

## 防止再次发生

本次故障的预防措施不是继续扩充“需要删除的字段”列表，而是建立以下不变量：

1. 审批数据、执行数据和展示元数据在类型与存储上分离；
2. Python 工具参数只能通过签名白名单投影生成；
3. 审批恢复必须走真实 PydanticAI 公共契约测试；
4. 捕获异常的边界必须同时产出安全的外部错误和可关联的内部诊断；
5. 副作用工具的未知状态永不自动重试或隐式 fallback。
