# 命令审批黑名单设计

## 目标

将 Minecraft 命令的审批从按工具整体拦截改为按命令根拦截。普通命令如 `give`、`time` 直接执行；破坏性命令在执行前要求触发玩家审批。

## 策略

1. 硬拒绝优先：`op`、`deop`、`stop`、`whitelist`、`permission`、`wsserver` 继续永远拒绝，审批不能覆盖。
2. `run_minecraft_command` 和 `run_minecraft_commands` 检查 `approval_command_roots`。命中任一根时，沿用既有 `ApprovalRequired`、待审批保存和精确恢复链路。
3. 批量命令只要任一条命中审批根，整批暂停等待审批；全部未命中则直接执行。
4. 默认审批根为 `clear`、`clone`、`damage`、`fill`、`kill`、`replaceitem`、`setblock`、`structure`、`summon`。配置可在 `agent.runtime_harness.approval_command_roots` 追加根；内置默认值始终保留。

## 验证

策略测试覆盖普通命令直通、破坏性单条命令审批、批量命令任一命中审批、硬拒绝优先级，以及真实 PydanticAI 工具调用链中的直通行为。
