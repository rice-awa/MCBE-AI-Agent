# Task 7 工作报告：端到端验收与发布保护

## 状态

DONE_WITH_CONCERNS

离线 P0/P1/P2 验收通过；真实 MCBE 开发世界不可用，六项人工冒烟未运行，已按要求保留为 concern，未伪造通过。

## 责任范围

本 Task 只新增：

- docs/validation/2026-08-02-block-tools-reliability-fix.md
- .superpowers/sdd/task-7-report.md

没有修改生产代码、测试代码或用户未跟踪的计划文档。原始计划文档 docs/superpowers/plans/2026-08-02-block-tools-reliability-fix.md 保持未跟踪、未修改、未提交。

## 命令与准确结果

### 核心回归集

命令：

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

结果：退出码 0；299 passed, 1 skipped, 17 warnings in 6.78s。跳过项是既有 commandLine 461B budget 条件分支；warning 是既有弃用/AsyncMock warning，未为消除 warning 重跑。

### 完整 Python 测试

命令：

~~~bash
.venv/bin/python -m pytest -q
~~~

结果：退出码 0；764 passed, 1 skipped, 2 deselected, 33 warnings in 22.54s。

### Add-on 完整测试

命令：

~~~bash
cd MCBE-AI-Agent-addon
npm test -- --run
~~~

结果：退出码 0；Vitest 4.1.10，13 个测试文件通过，92 个测试通过，耗时 3.83s。

## malformed 回放证据

回放使用现有 tests/test_stream_mode.py 中的真实 FunctionModel malformed fixture：

~~~text
tests/test_stream_mode.py::test_streamed_malformed_tool_args_keep_validation_retry_pair
~~~

只读回放脚本通过 importlib 加载上述现有测试函数，捕获 received[1]，然后分别执行历史旧清洗逻辑、当前 ensure_tool_message_pairs() 和 OpenAI-compatible mapper；退出码 0，没有网络请求或仓库内临时文件。

修复前消息序列：

~~~text
ModelResponse -> ToolCallPart(use_value, id=call-bad, args='{"value":')
ModelRequest  -> RetryPromptPart(use_value, id=call-bad, json_invalid)
~~~

旧清洗器不识别工具级 retry，删除 assistant ToolCallPart 后保留 retry；wire 只剩 role=tool、tool_call_id=call-bad，形成 provider 400 的孤立工具响应。

修复后消息序列：

~~~text
ModelResponse -> ToolCallPart(use_value, id=call-bad, args='{"value":')
ModelRequest  -> RetryPromptPart(use_value, id=call-bad, json_invalid)
~~~

wire 顺序为 assistant tool_calls[id=call-bad] → role=tool[tool_call_id=call-bad]；模型输出 recovered。该回放验证了同 ID 配对、不再产生孤立 role=tool，以及模型可在当前 run 中继续。

核心回归集中还通过了 worker malformed validation retry、partial history orphan retry 清理、无 execution.started 的 validation Trace、修正后 success audit 与 bridge 无请求断言。

首次回放辅助脚本曾因对消息对象误用 model_copy() 退出码 1；该错误只在验收脚本的旧投影辅助代码中，未改动仓库。改用 dataclasses.replace() 后同一 fixture 回放退出码 0。

## canonical PRECONDITION / audit / Trace

实际 Host projection 对 49 个 minecraft:grass_block 的默认 air-only 失败输出：

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

validation audit 稳定投影：

~~~text
tool_name=edit_blocks
tool_call_id=tc-task7
status=failure
error_kind=INVALID_ARGUMENT
failure_reason=json_invalid
execution_stage=validation
external_state_unknown=false
validation_error_locations=[edits]
parameters.api_key=[REDACTED]
audit_count=1
~~~

Trace 稳定投影：

~~~text
event_name=tool.validation.failed
status=failed
tool_call_id=tc-task7
attributes.error_kind=INVALID_ARGUMENT
attributes.execution_stage=validation
attributes.validation_error_type=json_invalid
attributes.validation_error_locations=[edits]
attributes.parameters.api_key=[REDACTED]
include_content=false => payload omitted
~~~

worker fixture 还确认没有 tool.execution.started，malformed 调用未向 bridge 发请求，修正调用单独产生 success audit。审计和 Trace 不包含完整敏感参数或 Add-on 原始错误体。

## MCBE 冒烟

检查命令是只读 Python 环境探测，时间为 2026-08-02T23:24:13.366207+08:00 Asia/Shanghai。

结果：

- 没有 .mcworld 开发世界；只找到 Add-on/SDK 打包产物；
- 没有 MCBE、Bedrock 或 BDS 进程；
- 没有 TCP :8080 listener；
- MCBE_HOST、MCBE_PORT、MCBE_WORLD、MCBE_DEV_WORLD 均未设置。

状态：未运行。未运行的六项是单方块放置、7×7 grass 地板恢复、四面墙 4 edits 限制、positions 门顶、malformed 自动 retry、下一条玩家消息历史隔离。

后续人工步骤已写入验证文档：准备测试世界和当前 Add-on，local-deploy，启动 python cli.py serve --dev，完成 /wsserver 连接后按 1–6 顺序执行并保存真实 trace/audit 证据。

## 敏感信息与工作树扫描

执行命令：

~~~bash
git status --short
git diff --check
rg -n "replace_any=true|expected_previous" services/agent/harness services/agent/block_ops
rg -n "DEEPSEEK_API_KEY|OPENAI_API_KEY|SECRET_KEY|WEBSOCKET_PASSWORD" docs tests
~~~

结果：

- git diff --check 退出码 0，无输出；
- legacy 命中仅在 services/agent/block_ops/tools_impl.py 的内部兼容转换、预检/执行 payload 和 commandLine budget 适配；prompt、catalog、模型可见 tool result 路径无命中；
- 敏感名命中仅为测试环境变量占位符/脱敏 fixture，以及计划/验收文档中的扫描命令文本；没有真实密钥值；
- 模型可见 canonical JSON、audit/Trace 示例只有 canonical 字段和 [REDACTED]；
- git status 在提交前只显示原始计划文档和本 Task 验收文档为未跟踪，没有生产代码/测试代码改动。

## Commit

提交信息必须且将使用：

~~~text
docs(validation): record block tool reliability acceptance
~~~

本报告在提交前生成；最终 commit hash 以提交完成后 git rev-parse HEAD 和最终交付消息为准。

## Concerns

1. 当前环境没有可用 MCBE 开发世界或活跃 Add-on bridge，六项真实游戏冒烟待人工完成；这不是离线测试失败。
2. 正式测试保留既有 warnings 和一个 461B 条件 skip；本 Task 按用户要求没有为 warning 重跑。
3. malformed 回放辅助脚本首次尝试有一个本地脚本类型适配错误，修正后 fixture 回放通过，不影响仓库文件和正式测试结果。
