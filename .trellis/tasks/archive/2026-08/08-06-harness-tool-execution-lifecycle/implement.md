# 方案一：收拢运行时 Harness 工具执行生命周期 — 执行计划

## 前置

- 依赖方案二提供的双玩家交错测试作为安全门（该测试与本任务无直接功能耦合，但按报告顺序先落地安全门）。
- 工作分支：从最新 `dev` 创建 `refactor/harness-tool-execution-lifecycle`。

## 实施顺序

1. **读当前代码**：核对 `services/agent/harness/execution.py` 普通执行与 `_resume_approved_block_plan` 两条路径的实际签名与行号（报告行号为 `6ac302a` 快照，已偏移）。
2. **建立特征测试**：为报告"建议验证的行为"六项在 `tests/test_runtime_harness_execution.py` 补测试，覆盖普通执行与恢复执行行为，作为收拢前的行为基线。
3. **提取统一生命周期**：将"预检→决策→暂停/恢复→执行→结果归一→工具审计→追踪"收敛到内部深模块（`execute`）。两条路径分别构造 `ExecutionStart`。
4. **消除重复收尾**：删除恢复路径中镜像主路径的重复实现，改为调用统一收尾链。
5. **验证幂等/审计/追踪一致性**：确认普通执行与恢复执行产生一致的追踪与结果状态；工具审计失败继续主流程。
6. **质量检查**：`pytest -q tests/test_runtime_harness_execution.py tests/test_block_ops.py`，并跑全量 `pytest -q` 确认无新增失败（独立 CLI bug 除外）。

## 验证命令

```bash
pytest -q tests/test_runtime_harness_execution.py tests/test_block_ops.py
pytest -q   # 全量，确认无新增失败
```

## 评审门

- 代码评审：确认无第二条收尾实现；审计/追踪/幂等由同一实现驱动。
- 通过 `trellis-check` 复核，再合入 `dev`。

## 回滚点

- 改动集中在 `execution.py` 内部。若验收门不满足（出现第二份收尾实现或路径行为不一致），回退到合入前提交。
- 若某测试开始绕过公开接口触碰内部实现，停止并复核设计。

## 完成定义

- 验收标准（prd.md）六项全部通过。
- 无第二份收尾实现；两条路径工具箱审计/追踪/幂等一致。
- 全量 pytest 无新增失败。