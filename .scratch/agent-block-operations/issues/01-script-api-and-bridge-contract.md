# 01 — 统一 Script API 与 Add-on bridge 能力契约

**What to build:** 建立可向后兼容的方块能力基础：统一 Minecraft Script API 声明、支持异步 bridge capability，并让 Python 宿主能够识别当前 Add-on 是否支持方块工具。

**Blocked by:** None — can start immediately.

**Status:** ready-for-agent

- [ ] Minecraft 模块在包元数据、锁文件和行为包 manifest 中精确对齐为 `@minecraft/server` 2.6.0、`@minecraft/server-ui` 2.0.0 和已确认的 GameTest 预览版本。
- [ ] 行为包初始保留 `min_engine_version` 1.21.80，构建或目标稳定引擎明确拒绝时才上调并记录原因。
- [ ] Add-on bridge router 能够等待异步 capability handler，同时保持现有能力正常工作。
- [ ] 版本化能力握手能区分支持、旧 Add-on、断线和明确失败，并按 `connection_id` 缓存、断线清理。
- [ ] Bridge 响应中的 `ok: false` 在 Agent 侧映射为失败 `ToolResult`，不得记为工具成功。
- [ ] Add-on 单测、构建与现有 Python bridge/Agent 工具测试通过。
