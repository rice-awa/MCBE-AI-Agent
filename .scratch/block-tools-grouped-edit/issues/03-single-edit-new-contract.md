# 03 — 用新契约跑通单个方块编辑

**What to build:** 用 `edits`、`target`、`block` 和 `expect` 组成的新契约，端到端完成一个方块编辑的归一化、预检、玩家审批、冻结参数执行和写后确认，同时把旧执行模式隐藏在模块内部。

**Blocked by:** 01 — 恢复现有方块编辑的可信结果语义; 02 — 使用统一 target 完成有界方块查看.

**Status:** ready-for-agent

- [ ] 模型可见的 `edit_blocks` 顶层输入仅包含必填 `edits` 和可选 `dimension`；本票据阶段明确限制为一个 edit。
- [ ] 单个 edit 使用与查看工具一致的 `target`，并支持 `positions` 和 `box`。
- [ ] `block` 接受字符串或 `{type_id, states}`，`expect` 支持默认 `air`、`any`、方块类型和完整 permutation。
- [ ] `positions` 使用严格前置条件语义，`box` 使用过滤语义；默认仍只替换空气。
- [ ] `place`、`batch`、`fill`、`coordinate_mode`、`replace_any` 和 `expected_previous` 不再出现在模型 schema 中。
- [ ] 模块根据规范化目标在内部选择现有单点、离散批量或区域填充适配器，旧参数只允许内部兼容路径使用。
- [ ] 编辑只产生一次预检和一次玩家审批；审批恢复使用 canonical args、冻结后的绝对坐标和当前事件 `player_name`。
- [ ] 执行前重新检查锁定目标，执行后读取并确认期望状态，返回稳定的 `applied`、`noop`、`partial`、`failed` 或 `unknown`。
- [ ] 审批摘要包含目标数、匹配数、跳过数、目标方块、被替换的非空气类型和自动修复，且不暴露完整锁定目标。
- [ ] 模型 schema、审批恢复、幂等键、bridge 字节预算和结果投影均有端到端测试。
