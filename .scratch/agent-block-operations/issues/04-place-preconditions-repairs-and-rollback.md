# 04 — 完善单格条件写入、状态与兜底保护

**What to build:** 将单格放置扩展为可安全修改现有普通方块的完整能力，支持 states、条件写入、显式覆写、删除、低级错误修复与验证失败回滚。

**Blocked by:** 03 — 打通安全的绝对坐标单格放置.

**Status:** ready-for-agent

- [ ] `place` 支持 type ID 和可选 states，无效 state 名或值返回 `STATE_INVALID` 且不猜测。
- [ ] `replace_any=true` 允许覆写普通非空气方块；`expected_previous` 通过 type ID 和声明的部分 states 提供条件覆写。
- [ ] `replace_any` 与 `expected_previous` 互斥，条件不匹配返回 `PRECONDITION_FAILED`。
- [ ] 目标为 `minecraft:air` 时必须已通过显式覆写或条件写入授权。
- [ ] 对 inventory、sign、record player、fluid container 或 dynamic properties 等数据组件方块始终返回 `PROTECTED_BLOCK`。
- [ ] 参数可自动清理空白、补 `minecraft:`、规范维度别名和向下取整，每次调用最多修复并内部重试一次。
- [ ] 未知原版方块 ID 仅在存在唯一编辑距离 1 候选时修复；自定义命名空间和 states 不模糊修复。
- [ ] 修复后的最终参数用于审批、幂等哈希、工具审计和执行，结果保留原值与 `repairs_applied`。
- [ ] 写后验证失败时恢复 before permutation，并返回 verification 和 rollback 状态。
