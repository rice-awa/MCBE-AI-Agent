# Task 4 报告：统一 PRECONDITION_FAILED canonical 反馈

## 状态

DONE_WITH_CONCERNS

## 改动

- Host 白名单投影安全校验并裁剪 `actual_type_counts`：按最高计数保留最多 8 项，过滤不安全 ID、负数和非整数；保留/补齐 `matched_count`。
- Host 与零匹配分类统一生成模型可见的 canonical `expect` recovery hint，不信任 Add-on 原始 hint，不泄漏 `replace_any`、`expected_previous`、`locked_targets` 或 `phase`。
- homogeneous 实际类型优先建议精确 `expect`；mixed 类型列出有界摘要并说明精确条件与 `any` 的重新审批风险；受保护数据失败不继续建议 `any`。
- Add-on fill 的 air-only 零匹配改为依据实际类型生成 hint，并覆盖 homogeneous、mixed 与 protected 场景测试。

## 测试

1. `.venv/bin/python -m pytest -q tests/test_block_ops.py -k "precondition or expect_hint or zero_match"`

   结果：`7 passed, 1 skipped, 168 deselected in 2.68s`

2. `cd MCBE-AI-Agent-addon && npm test -- --run tests/bridge/blocks/fill.test.ts`

   结果：`Test Files 1 passed (1)`，`Tests 11 passed (11)`，耗时 `1.02s`。

## Commit

`fix(block-ops): return canonical precondition recovery hints`

## Concerns

- Host focused 测试仍有 1 个既有的 commandLine 461B budget skip；其余 Task 4 focused 断言均通过。
