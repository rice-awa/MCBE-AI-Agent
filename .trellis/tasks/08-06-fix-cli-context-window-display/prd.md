# 修复 CLI info 的模型上下文窗口展示

## 目标

修复独立测试失败：`tests/test_cli_config_validation.py::test_info_command_shows_context_window_for_providers` 期望 `deepseek-v4-flash` 显示 `context: 128000`，实际输出 `context: unknown`。

本任务为轻量 bug 修复，独立跟踪，**不随架构重构提交**。

## 范围

- `config/settings.py` 的 `model_metadata`（该模型上下文窗口缺失或映射缺失）。
- CLI `info` 展示路径（`cli.py` 或相关命令展示逻辑）。

## 现状问题

根因指向 `model_metadata` 中 `deepseek-v4-flash` 的上下文窗口值缺失，或未映射到 CLI `info` 展示路径，导致显示 `context: unknown`。属模型元数据或命令行展示问题，与六项架构候选无直接关系。

## 验收标准

- [ ] `python cli.py info`（或对应命令）对 `deepseek-v4-flash` 显示 `context: 128000`。
- [ ] `pytest -q tests/test_cli_config_validation.py` 通过。
- [ ] 不影响其他 provider 的上下文展示。

## 约束

- 独立提交，不混入任何架构重构提交（遵守报告 12 节）。
- 修复后全量 `pytest -q` 应为全绿（该项为当前唯一失败）。

## 验收门 / 回滚门

- **验收**：test 通过，`context` 正确展示。
- **回滚**：若修复引入其他 provider 展示回归，回退到合入前提交。