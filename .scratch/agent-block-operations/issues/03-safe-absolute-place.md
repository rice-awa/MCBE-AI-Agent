# 03 — 打通安全的绝对坐标单格放置

**What to build:** 让 MCBE Chat Agent 能够通过 `edit_blocks` 的 `place` 模式在绝对坐标放置一个默认 permutation 方块，在玩家审批后完成写前检查、写后验证和旧方块返回。

**Blocked by:** 02 — 打通绝对坐标方块查询.

**Status:** ready-for-agent

- [ ] `edit_blocks(mode=place)` 接收绝对坐标、维度和 type ID，且仅替换空气。
- [ ] 工具被编目为高风险改变世界操作，调用会进入现有审批流程，并继续支持对话/永久自动同意。
- [ ] 审批摘要显示最终维度、绝对坐标、目标方块和默认仅空气策略。
- [ ] 审批前预检目标可用、区块已加载且当前为空气；失败时不发生写入。
- [ ] 执行前再次确认预检条件，并发变更返回 `PRECONDITION_CHANGED` 且不写入。
- [ ] 执行后重读目标，返回版本化 JSON，包含 before、after、changed 和 verification。
- [ ] Add-on 明确不可用时不生成专用写入审批、不执行命令，只提示模型可另行调用现有命令工具。
