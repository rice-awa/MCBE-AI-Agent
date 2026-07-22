# 02 — 打通绝对坐标方块查询

**What to build:** 让 MCBE Chat Agent 能够通过 `inspect_block` 查询单个或多个绝对坐标，并获得可用于后续建造决策的稳定方块快照。

**Blocked by:** 01 — 统一 Script API 与 Add-on bridge 能力契约.

**Status:** ready-for-agent

- [ ] `inspect_block` 支持单个 position 或 positions 列表，absolute 模式必须提供有效维度。
- [ ] 原版维度别名会被规范化，有效的自定义命名空间维度可用。
- [ ] 数值坐标按数学向下取整，包括负数；变更会出现在 `repairs_applied` 中。
- [ ] 每个快照包含版本化的 JSON、维度、最终坐标、type ID、全部 states、含水/空气/液体标记。
- [ ] 超过可配置数量、无效维度、越界或未加载区块返回对应稳定错误码。
- [ ] `inspect_block` 被工具目录标记为低风险查询，无需玩家审批，且写入现有工具审计。
- [ ] 旧 Add-on 或能力缺失时不向模型暴露该工具。
