# Block Tools Reliability Fix：Task 7 端到端验收

日期：2026-08-02

状态：DONE_WITH_CONCERNS

范围：Task 1–6 的消息配对、partial history 自愈、canonical PRECONDITION_FAILED 反馈、bounded grouped edits、validation audit 与 Agent Trace；本 Task 只记录验收证据，不修改生产代码或测试代码。

## 结论

离线自动化验收通过。核心回归集、完整 Python 测试和 Add-on 完整测试均通过；malformed fixture 回放保留了同 ID 的 assistant tool call 与工具级 retry，模型继续返回 recovered，且 bridge/audit/Trace 的 validation 边界符合契约。

真实 MCBE 开发世界当前不可用，六项游戏内冒烟未运行，因此发布前仍需完成一次人工 MCBE 联调；这也是本报告保留 CONCERNS 的唯一运行环境缺口。没有把 Add-on 模拟器或离线测试写成真实世界通过。

## 1. 自动化命令与结果

### 核心回归集

精确命令：

~~~bash
.venv/bin/python -m pytest -q \
  tests/test_agent_context.py \
  tests/test_stream_mode.py \
  tests/test_agent_worker.py \
  tests/test_runtime_harness_prompt.py \
  tests/test_runtime_harness_catalog.py \
  tests/test_runtime_harness_audit.py \
  tests/test_block_ops.py \
  tests/test_trace_integration.py
~~~

结果：退出码 0；299 passed, 1 skipped, 17 warnings in 6.78s。

1 skipped 是既有的 MCBE commandLine 461B budget 条件分支；warning 为既有的 PydanticAI/stream API 弃用提示和 AsyncMock 未 await 提示，本轮没有为消除 warning 重跑。

### 完整 Python 测试

精确命令：

~~~bash
.venv/bin/python -m pytest -q
~~~

结果：退出码 0；764 passed, 1 skipped, 2 deselected, 33 warnings in 22.54s。

### Add-on 完整测试

精确命令：

~~~bash
cd MCBE-AI-Agent-addon
npm test -- --run
~~~

结果：退出码 0；Vitest 4.1.10，Test Files 13 passed (13)，Tests 92 passed (92)，耗时 3.83s。

## 2. malformed fixture 日志回放

回放使用现有原始 fixture：tests/test_stream_mode.py::test_streamed_malformed_tool_args_keep_validation_retry_pair。执行的是只读 Python heredoc：通过 importlib 加载该测试函数，捕获它的 received[1]，再分别套用历史旧清洗规则和当前 ensure_tool_message_pairs()，最后调用现有 OpenAI-compatible message mapper；没有创建仓库内临时 fixture，也没有访问外部模型。

回放结果：退出码 0；模型输出 recovered；第二次请求的关键消息如下。

### 修复前（历史旧清洗与 wire 投影）

原始 malformed 调用先产生：

~~~text
ModelResponse -> ToolCallPart(tool_name=use_value, tool_call_id=call-bad,
                              args='{"value":')
ModelRequest  -> RetryPromptPart(tool_name=use_value, tool_call_id=call-bad,
                                 validation=json_invalid)
~~~

旧清洗器只把 ToolReturnPart 当作工具响应，于是删除了 assistant ToolCallPart，却留下了工具级 retry：

~~~text
ModelRequest -> RetryPromptPart(tool_name=use_value, tool_call_id=call-bad)
~~~

旧 wire 投影因此只有：

~~~json
{
  "role": "tool",
  "tool_call_id": "call-bad"
}
~~~

这正是会触发 provider “tool response must follow a preceding assistant tool call” 400 的孤立形状。

### 修复后（当前清洗与 wire 投影）

当前历史保留同一 ID 的两个部分：

~~~text
ModelResponse -> ToolCallPart(tool_name=use_value, tool_call_id=call-bad,
                              args='{"value":')
ModelRequest  -> RetryPromptPart(tool_name=use_value, tool_call_id=call-bad,
                                 validation=json_invalid)
~~~

当前 wire 投影为：

~~~json
[
  {
    "role": "assistant",
    "tool_call_ids": ["call-bad"],
    "arguments": ["{\"value\":"]
  },
  {
    "role": "tool",
    "tool_call_id": "call-bad"
  }
]
~~~

因此第二次请求不存在孤立 role=tool，没有重现 DeepSeek 400 配对错误；模型根据 validation feedback 修正 JSON 并继续当前 run。

同一核心回归命令还通过了：

- test_worker_audits_validation_retry_and_later_success_without_execution_start：malformed 调用不进入执行/bridge，修正调用产生一条成功审计；
- test_partial_history_persistence_drops_orphan_retry_and_keeps_visible_context 与上下文配对测试：旧 history 的孤立工具级 retry 会被清理，玩家可见文本保留；
- test_validation_failure_trace_is_gated_and_does_not_start_execution：validation 失败不会产生 tool.execution.started。

## 3. canonical PRECONDITION JSON

使用实际 Host projection 对“默认 air-only、区域内 49 个 grass”结果进行离线投影，稳定字段为：

~~~json
{
  "schema_version": "1",
  "ok": false,
  "code": "PRECONDITION_FAILED",
  "message": "目标方块不满足 expect 前置条件。",
  "retryable": false,
  "external_state_unknown": false,
  "fallback_allowed": false,
  "matched_count": 0,
  "actual_type_counts": {
    "minecraft:grass_block": 49
  },
  "hint": "目标全为 minecraft:grass_block；如确实要替换，请将该 edit 的 expect 设为 minecraft:grass_block。确认允许覆盖任意普通方块时才使用 any（需重新审批）。"
}
~~~

该投影保留实际类型计数，使用模型可见的精确 expect 恢复建议；any 被明确标为需要重新审批的覆盖风险。模型可见的 grouped edit_blocks schema 仍只有 edits 与 dimension，单点门顶示例使用 target.positions，不会生成嵌套 target。

## 4. validation audit 与 Agent Trace

离线调用 extract_tool_validation_failures()、build_validation_failure_audit_record() 和 worker 的 validation Trace 投影，使用 tool_call_id=tc-task7；实际 worker fixture 断言已包含在核心回归集内。

### Audit 投影

~~~json
{
  "tool_name": "edit_blocks",
  "tool_call_id": "tc-task7",
  "status": "failure",
  "error_kind": "INVALID_ARGUMENT",
  "result": {
    "failure_reason": "json_invalid",
    "error_kind": "INVALID_ARGUMENT",
    "external_state_unknown": "false",
    "execution_stage": "validation"
  },
  "validation_error": {
    "type": "json_invalid",
    "locations": ["edits"]
  },
  "parameters": {
    "api_key": "[REDACTED]"
  }
}
~~~

本次 fixture 只写一条 validation failure audit；没有 execution-started 记录，也没有 bridge 请求。修正后的合法调用单独产生一条 success audit，符合“失败一次、修正成功一次”的现有 worker fixture 契约。

### Trace 投影

~~~json
{
  "event_name": "tool.validation.failed",
  "status": "failed",
  "tool_call_id": "tc-task7",
  "attributes": {
    "tool_name": "edit_blocks",
    "error_kind": "INVALID_ARGUMENT",
    "execution_stage": "validation",
    "validation_error_type": "json_invalid",
    "validation_error_locations": ["edits"],
    "parameters": {
      "api_key": "[REDACTED]"
    }
  }
}
~~~

本次 Trace 使用 include_content=false，故没有 payload；审计与 Trace 均没有完整敏感参数或 Add-on 原始错误体。事件分类为 validation failure，而不是 invocation failure、bridge failure 或 external_state_unknown。

## 5. 本地 MCBE 冒烟测试

状态：**未运行**。

检查时间：2026-08-02T23:24:13.366207+08:00（Asia/Shanghai）。检查结果：

- 工作区没有 .mcworld 开发世界；仅发现已生成的 Add-on/SDK 打包产物；
- 没有 MCBE、Bedrock 或 BDS 进程；
- 没有 TCP :8080 listener；
- MCBE_HOST、MCBE_PORT、MCBE_WORLD、MCBE_DEV_WORLD 均未设置。

因此以下项目全部未运行：

1. 全空气区域放置单个方块；
2. 草地 7×7 地板：省略 expect 后观察 grass 反馈，再用精确 expect 恢复；
3. 四面墙且单次不超过 4 edits；
4. 单点 positions 门顶并确认不存在 target.target；
5. 注入 malformed 参数并确认自动 retry 后继续；
6. 重新发送下一条玩家消息并确认历史未污染。

后续人工步骤：准备测试世界并启用当前 Add-on，运行 npm run local-deploy，启动 python cli.py serve --dev，进入世界连接 /wsserver <服务器IP>:8080，确认 Add-on bridge 已连接后按上述 1–6 顺序执行；同时保存对应 trace 和 runtime_harness_tools.jsonl 片段，再确认没有 provider 配对 400。

## 6. 敏感信息、模型可见字段与工作树保护

执行的精确扫描命令：

~~~bash
git status --short
git diff --check
rg -n "replace_any=true|expected_previous" services/agent/harness services/agent/block_ops
rg -n "DEEPSEEK_API_KEY|OPENAI_API_KEY|SECRET_KEY|WEBSOCKET_PASSWORD" docs tests
~~~

在最终文档写入前的扫描结果：

- legacy 扫描仅命中 services/agent/block_ops/tools_impl.py 的内部兼容转换、预检/执行 payload 组装与 MCBE budget 适配；没有命中 Harness prompt、catalog 或模型可见工具结果路径；这是允许保留的内部 legacy 命中；
- 敏感名扫描命中测试中的环境变量占位符/脱敏 fixture，以及原始未跟踪计划文档中的扫描命令文本；未发现真实密钥值；
- 模型可见 JSON、canonical 示例和本报告中的审计/Trace 示例均只保留 canonical 字段或 [REDACTED]，没有把内部恢复字段作为模型示例；
- 计划文档 docs/superpowers/plans/2026-08-02-block-tools-reliability-fix.md 保持未跟踪、未修改、未提交。

文档写入后再次执行同一组扫描：legacy 命中仍只在 tools_impl.py 的内部兼容路径，新增验收文档的命中仅是扫描命令文本本身；敏感名命中仍是测试占位符、原始计划文档/验收文档的扫描命令文本，没有真实密钥值。模型可见 prompt、catalog、tool result 和 canonical 示例没有新增命中。

最终提交前会再次执行 git status --short、git diff --check，并用 git diff --cached --check 检查仅暂存的两份验收文档。

## 7. 发布保护结论

- Task 1–3 的协议配对、流式 malformed retry 和 partial history 自愈通过；
- Task 4–6 的 canonical feedback、bounded grouped guidance、validation audit/Trace 通过；
- P0/P1/P2 的离线证据齐全，未发现代码或测试失败；
- 真实 MCBE 冒烟未运行，需在可用开发世界完成后再观察一轮真实 trace，再按 dev → master 发布顺序推进；
- 本 Task 只提交本文件与 .superpowers/sdd/task-7-report.md，不提交计划文档、日志、配置或密钥。
