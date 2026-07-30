# 02 — 使用统一 target 完成有界方块查看

**What to build:** 让 MCBE Chat Agent 通过统一 `target` 查询单点、点集或长方体区域，并根据查询规模自动获得完整快照或有界摘要，而不会因大区域查询收到无限增长的结果。

**Blocked by:** None — can start immediately.

**Status:** ready-for-agent

- [ ] 模型可见的 `inspect_block` 顶层输入仅包含必填 `target` 和可选 `dimension`。
- [ ] `target` 支持非空 `positions` 和 `box` 两种互斥形状，单点使用长度为 1 的 `positions`。
- [ ] 世界坐标与玩家相对坐标均可使用；同一 target 混合两种坐标时在调用 Add-on 前拒绝。
- [ ] 玩家相对坐标使用当前事件的 `player_name`、玩家维度和朝向解析，并在后续调用中保持冻结后的绝对坐标。
- [ ] 单点或少量点返回 type ID、states、waterlogged、air 和 liquid 等完整决策字段。
- [ ] 多点或 box 返回边界、总数、方块类型计数、未知数量和有界样本，不完整枚举全部快照。
- [ ] 未加载或无法读取的位置计入 `unknown/unloaded`，不得伪装成空气。
- [ ] 模型结果不包含重复 targets、玩家原点、bridge 诊断等内部元数据；完整证据仍可供工具审计使用。
- [ ] 摘要和样本上限可配置且受硬上限保护，Python 与 Add-on 测试覆盖大小目标和反向 box 角点。
