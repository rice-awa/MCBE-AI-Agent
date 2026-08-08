# Addon UI 全面重构：流式打字机 + 会话管理协议化

## Goal

MCBE AI Agent 的 addon UI（DDUI CustomForm，@minecraft/server-ui v2.1.0）全面重构，对齐主流 AI chat 前端体验：流式打字机效果（逐块实时渲染）、协议级会话管理（对齐 Python 侧会话机制）、token 统计。跨三个代码库：mcbe-ws-sdk（协议扩展）、父仓库 Python（透传与结构化会话入口）、addon 仓库（前端全面重构）。

## Requirements

### 1. 打字机效果（流式实时渲染）
- 逐块追加：每收到一个 `mcbews:text_resp` 块，按 index 排序后追加到正在生成的 AI 回复气泡的 ObservableString，不等全部块到齐
- 生成中显示 `▌` 光标，完成后消失
- 状态行实时更新：`生成中… (x chars)` → 完成后 `✓ 完成 · 本轮 token in/out`
- 生成中禁用发送按钮
- 关闭 UI 时结束当前流（已收部分照常入库）；切到 More 面板时流继续写，返回时显示新内容
- 块乱序处理：维护块缓存，收到新块后按 index 排序，只追加"排序后新增的连续部分"

### 2. 会话管理协议（协议级别，可扩展）
- `mcbews:text_resp` frame 加可选字段：conversation_id / title / usage{input_tokens, output_tokens}（缺省时帧与旧格式兼容）
- `MCBEWS|UI_CHAT` 上行 payload 加 conversation_id（Addon 发聊天时带当前会话）
- 新增 `mcbews:session_req`（addon→Python）/ `mcbews:session_resp`（Python→addon）事件对，结构化 JSON
- session_req：request_id, action, player_name, 可选参数（conversation_id / session_id 等）
- session_resp：request_id, ok, data（会话列表等结构化数据）
- 操作全集（= Python 侧 AGENT 对话命令全集）：new / switch / list / status / clear / save / restore / saved / delete / compress
- 协议不做过多向后兼容：新字段缺失归 default 会话；session_req 无响应时提示"会话同步不可用"
- 协议设计注重可扩展性：可选字段模式、request_id 关联、版本字段预留

### 3. Token 统计
- worker `ai_response_sync` 带 usage（usage_dict 已在 worker.py:1009 作用域），经 broker_bridge 透传到 text_resp
- UI 按玩家+会话维度累计 input/output token
- 全局累计持久化 DynamicProperty（跨会话/重启保留）；会话维度累计跟随活跃会话（内存）
- Stats 面板展示：会话维度（当前会话标题、本轮 token、会话累计）+ 全局维度（总 token、总 token 合计=输入+输出、消息数、打开/发送/响应块数、时间戳）+ 重置

### 4. 主面板布局（agentConsole 重构）
- 标题行：`✦ #short_id · 会话标题 [切换]`（切换入口 → 会话列表表单）
- 状态行：连接状态 / 生成中 / 完成+token
- 最近 5 条完整对话（user/assistant 按角色分组，隐藏 tool 条目）
- 输入框 + `[发送] [新会话] [全部对话] [更多]`
- 新会话不弹确认，直接发 session_req new 并清空显示区
- 生成中禁用发送

### 5. 全量对话预览表单（新）
- 主面板"全部对话"按钮进入
- 每页 5 条、完整显示、只读、时间倒序、翻页（上一页/下一页 + 第 X 页/共 Y 页）
- 数据：内存全量历史 + DynamicProperty 持久化上限从 20 提升到 100 条（PERSISTED_HISTORY_LIMIT）
- 只读，仅展示当前会话全量历史
- showToolEvents 设置控制是否显示 tool 条目

### 6. 会话列表表单（新）
- 主面板标题行"切换"进入
- 每页 5 个会话：`#short_id · 标题 · N轮 · 活跃标记`，点击某条 = 发 switch 并回主面板
- 附"＋ 新会话"按钮
- 删除/保存/恢复不在本表单（放 More 面板）

### 7. More 面板
- 扩展为 4 入口：设置 / 统计（含 token）/ 会话文件管理 / 关闭
- 会话文件管理表单：
  - 顶部 `[保存当前会话]` → 发 save，返回 session_id 后显示"已保存: <session_id>"
  - 已保存会话列表（发 saved，每页 5 条）：标题 · 消息数 · 更新时间，每条附：恢复（Python 语义：恢复进当前会话，恢复后回主面板）/ 删除（二次确认：点删除后该条变"确认删除？[是][否]"）

### 8. 本地数据结构（方案 A 分桶镜像）
- Addon 侧每会话独立 history 桶；text_resp 带 conversation_id 后追加到对应桶
- 会话元数据（标题/轮数/活跃态）以 Python session_resp 为准，本地仅缓存
- 切会话时主面板直接读本地桶
- DynamicProperty v1→v2 迁移：旧单数组 history 归入 default 会话桶
- 持久化：每会话最近 100 条，会话上限 20 个

### 9. Settings 面板调整
- 保留：showToolEvents（改为控制全量对话预览中是否显示 tool 条目）、defaultDelivery、autoSaveHistory
- 删除：responsePreviewLength、maxHistoryItems

### 10. 工具事件
- 主面板隐藏 tool 条目；流式生成期间不穿插工具事件；showToolEvents 只影响全量预览

## Acceptance Criteria

- [ ] 打字机：流式回复逐块显示（非一次全出），块乱序不跳动，▌ 光标 + 状态行 token 正确
- [ ] 生成中发送按钮禁用；关闭 UI 流结束已收部分入库；切面板流继续
- [ ] 主面板：标题行（#short_id + 标题 + 切换入口）、最近 5 条完整、隐藏 tool、底部四按钮
- [ ] 会话管理：session_req/resp 10 个 action 全通；列表/切换/新建/保存/恢复/删除（二次确认）可用
- [ ] text_resp 带 conversation_id/title/usage，Addon 分桶入库
- [ ] UI_CHAT 上行带 conversation_id
- [ ] token 统计：会话维度 + 全局维度 + 总 token 合计，全局持久化
- [ ] 全量预览：每页 5 条完整只读、倒序翻页
- [ ] DynamicProperty v1→v2 迁移：旧数据归 default 桶，不丢失
- [ ] Settings 删 2 项保留 3 项；showToolEvents 只影响全量预览
- [ ] 协议向后兼容：旧 6 字段 text_resp 仍可解析
- [ ] 现有测试通过；新增测试覆盖重构逻辑
- [ ] 协议文档（docs/addon-bridge-protocol.md）更新会话管理协议一节

## Notes

- research 产出：`.trellis/tasks/08-08-ddui-streaming-sessions/research/`（addon-ui-current.md / python-protocol-points.md / constraints.md）
- SDK 源码可直接修改（用户拍板），协议设计注重可扩展性
- 分支策略：addon 仓库 feature/ddui-streaming-sessions；父仓库同名分支；遵守 Conventional Commits，目标 dev
- 实现约束见 constraints.md：hook 非阻塞（asyncio.create_task）、身份只用 event.sender、下行走 SDK delivery、上行块 256 字符限制、会话元数据只能走协议查询（owner 校验严格）
