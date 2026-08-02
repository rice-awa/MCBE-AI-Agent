# 方块查看与独立编辑组验收记录

日期：2026-08-02
范围：`inspect_block`、`edit_blocks`、审批恢复、结构化命令回退、工具审计与 Add-on `block_ops` v1。

## 自动化验证

| 项目 | 命令 | 结果 |
| --- | --- | --- |
| Python 全量测试 | `.venv/bin/python -m pytest -q` | 731 passed, 1 skipped, 2 deselected |
| Add-on 测试 | `npm test`（`MCBE-AI-Agent-addon/`） | 13 files, 90 passed |
| Add-on 构建 | `npm run build`（`MCBE-AI-Agent-addon/`） | TypeScript 与 bundle 成功 |

离线验证覆盖：

- `inspect_block` 的点集、box、有界摘要、未知状态和相对坐标；
- `edit_blocks` 的预检、去重、冲突、部分匹配、写后确认、幂等与一次审批恢复；
- 分组聚合结果不会将 `partial`、`failed` 或 `unknown` 说成完成，且完整写入证据只保留在审计路径；
- `ADDON_UNAVAILABLE` / `UNSUPPORTED_CAPABILITY` 的 `fallback_allowed=true` 会穿透分组结果；其余失败不扩大原始命令权限；
- 同一连接中的玩家与任务边界隔离、显式原始命令、以及 `setblock` / `fill` / `clone` 的自动回退限制；
- DeepSeek、OpenAI、Anthropic、Ollama 均只得到 `edit_blocks(edits, dimension?)` 的模型可见 schema，不会看到旧 `mode`、互斥坐标字段或恢复字段；
- `config.example.json`、`addon.block_tools` 与 `get_capabilities` 的 `block_ops` v1 文档一致。

本轮修复了验收前的三项 Issue 6 缺口：分组失败遗漏 `fallback_allowed`、显式 `/setblock` 请求识别过窄、以及错误拦截 `function` / `schedule` 的范围膨胀。

## 实际 Minecraft 验收

状态：**待执行**。本工作区没有已连接的 Minecraft 世界、Add-on 会话或可安全操作的真实服务端，因此未把模拟结果写成实测通过。

在启用 Beta APIs、安装当前 Add-on、完成 `/wsserver` 连接并使用测试世界后，按以下场景记录结果：

1. 让 MCBE Chat Agent 对施工区域执行一次 `inspect_block` box 查询，确认返回有界摘要。
2. 用 1–2 个互不依赖的 `edit_blocks` 主体/墙体组；再分别执行一个门窗组和一个内饰/照明组。记录每次模型请求与工具调用。
3. 专用能力可用时确认没有自动 `setblock`、`fill`、`clone` 回退；请求门等多格方块时确认成功完成，或收到 `UNSUPPORTED_BLOCK_PLACEMENT`。
4. 审批后核对最终回复严格反映聚合的 `applied` / `partial` / `failed` / `unknown` 状态。
5. 从 `logs/runtime_harness_tools.jsonl` 核对预检、审批、执行和审计中同一事件的 `player_name` 一致，且审计顶层状态与结构化结果一致。

验收目标：约 5–7 次工具调用、3–5 次模型请求；相对于设计基线（28 次工具调用、9 次模型请求）记录实际计数和差值。

| 实测日期 | 世界 / Add-on 版本 | 工具调用 | 模型请求 | 自动命令回退 | 多格方块结果 | 审计核对 | 结论 |
| --- | --- | ---: | ---: | ---: | --- | --- | --- |
| 待填写 | 待填写 | — | — | — | — | — | 待执行 |
