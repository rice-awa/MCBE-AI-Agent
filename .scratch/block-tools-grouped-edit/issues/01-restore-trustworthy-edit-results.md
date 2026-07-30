# 01 — 恢复现有方块编辑的可信结果语义

**What to build:** 修复当前 `edit_blocks` 的预检与结果分类，使 MCBE Chat Agent 能可靠区分无需修改、部分匹配、前置条件失败、确定失败和外部状态未知，并让工具审计在结果字符串化前记录真实状态。

**Blocked by:** None — can start immediately.

**Status:** ready-for-agent

- [ ] 目标已经处于期望方块及 states 时返回 `noop`，且不发送写操作。
- [ ] box 预检匹配数为零、目标又不是期望状态时，在审批前返回 `PRECONDITION_FAILED`，不得映射为 `INTERNAL_ERROR`。
- [ ] box 只匹配部分目标时返回 `partial`，并包含匹配数、跳过数和有界的实际方块类型计数。
- [ ] 确定失败与外部状态未知分别返回 `failed` 和 `unknown`；`unknown` 明确禁止自动重试。
- [ ] 参数错误、前置条件失败、限制超出和内部错误均稳定返回 `fallback_allowed=false`；只有能力不可用类结果允许回退。
- [ ] “玻璃替换已有木板”场景返回可操作的当前类型信息和 `expect` 修复提示，不触发命令回退。
- [ ] 工具审计顶层状态、结果摘要和反馈闭环分类与结构化 `ToolResult` 一致，不受字符串化结果影响。
- [ ] Python 与 Add-on 回归测试覆盖 `noop`、零匹配、`partial`、`failed` 和 `unknown`。
