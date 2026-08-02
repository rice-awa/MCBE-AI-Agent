# Task 5 报告：降低 grouped edits 的模型生成复杂度

## 状态

DONE_WITH_CONCERNS

## 改动

- 重写运行时 Harness 的方块编排优先级：保留 grouped edits 的一次预检/一次审批能力，明确按小而完整的施工阶段组织调用、4 个 edits 的模型软上限、不得合并稍后阶段，以及 validation retry 只修失败调用。
- 在提示和 `edit_blocks` 工具描述中加入每种 target 一个最短 canonical 示例，使用 `target.box` / `target.positions`，避免 `target.target` 歧义；同时删减重复的整份契约说明。
- 保持模型可见的 `edit_blocks` 顶层 schema 只有 `edits` 与 `dimension`，恢复字段继续只由 Harness 内部使用；运行时 `max_edits_per_group` 硬上限未改动。
- 增加四种固定 `FunctionModel` 离线调用形状覆盖：7×7 地板单 edit、四面墙不超过 4 edits、门洞上方单点使用 `target.positions`、地板前置条件失败后只重试地板。
- 将一个既有 grouped precondition fixture 对齐当前 canonical `actual_type_counts` hint 契约，未修改运行时执行逻辑。

## 测试

命令：

```bash
.venv/bin/python -m pytest -q tests/test_runtime_harness_prompt.py tests/test_runtime_harness_catalog.py tests/test_block_ops.py -k "schema or grouped or prompt"
```

结果：`43 passed, 149 deselected, 4 warnings in 2.33s`。

## Schema bytes / description chars 快照

以 UTF-8、`ensure_ascii=False`、排序键和紧凑 JSON 序列化计算：

- raw `edit_blocks` schema：`2974` bytes。
- 模型可见 schema（剥离恢复字段）：`2414` bytes，上限 `4096` bytes。
- 注册工具 description：`976` chars，上限 `1600` chars。
- raw 顶层字段：`dimension`, `edits`, `locked_targets`, `locked_targets_by_edit`, `noop_edit_indices`, `phase`, `repairs_applied`。
- 模型可见顶层字段：`dimension`, `edits`。

## Commit

`fix(harness): bound grouped block edit guidance`

## Concerns

- focused 测试无失败；当前 Pydantic AI 环境输出 4 个既有 `retries` deprecated warnings（建议未来迁移到 `tool_retries`），不影响 Task 5 结果。
