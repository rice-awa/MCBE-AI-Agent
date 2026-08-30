# 修复 UI 会话切换帧解析

## Goal

让玩家能从游戏内会话列表切换回 `default` 会话，同时继续拒绝真正缺少 `cid` 的
`switch` 请求。修复应覆盖 Addon → SDK → Host 全链路，并形成可发布的 SDK patch，避免
`McbeServerFacade` 再把合法请求记录为 `malformed_addon_frame`。

## Background

- 用户日志中的 123 字节请求为
  `MCBEWS|SESSION|{"request_id":"...","v":1,"action":"switch",`
  `"player_name":"fantong7038","cid":"default"}`。
- 将该帧直接送入 `AddonBridgeService.handle_player_message()` 可稳定复现
  `ProtocolError: switch requires cid`。
- `SessionRequest` 当前先把缺失 `cid` 默认成 `"default"`，再以值等于 `"default"`
  判断字段缺失，因此无法区分“显式切换到默认会话”和“没有提供目标”。
- SDK 放行后，Host `ConversationOperations._switch()` 仍会用同样方式拒绝
  `"default"`；两个边界都必须修复。
- Addon 的初始状态、持久化和 Host 的 `DEFAULT_CONVERSATION_ID` 均把 `"default"`
  定义为真实、合法的会话 ID；UI 发送该值符合现有产品契约。

## Requirements

- SDK `SessionRequest` 必须依据 `cid` 是否被显式提供来校验 `switch`，不能依据其值是否
  等于 `"default"`；显式 `cid="default"` 合法，缺失或空白 `cid` 仍非法。
- Host 会话操作必须允许显式目标 `"default"`，但仍拒绝 `None` / 空白目标。
- 会话 schema 仍为 `1`，线格式仍为 `MCBEWS/1`；这是兼容性 bug 修复，不新增协议轴。
- SDK 权威 session vectors 必须包含 `switch/default`，并同步所有生成投影与 fixture。
- 产品 Addon 必须保留单帧原子请求，并用测试锁定 UI client 会发送
  `action="switch", cid="default", player_name=<当前玩家>`。
- SDK 以 patch 版本 `0.2.1` 准备发布；Host 的最低依赖与 wheel contract gate 同步到
  `0.2.1`，但本任务不执行 push、PR、tag 或 PyPI 发布。
- 所有玩家身份继续来自请求中的显式 `player_name`，不得使用 ToolPlayer sender 代替。

## Out of Scope

- 改变会话列表 UI、分页、文案或本地 fallback 策略。
- 修改 SESSION 分片/字节预算、text response 或 approval 协议。
- 发布 SDK、创建远端 tag、推送分支、创建 PR 或合并到长期分支。

## Acceptance Criteria

- [ ] 用户日志中的完整帧经 SDK `AddonBridgeService` 后产生 typed session control
      message，不再抛 `ProtocolError` 或触发 `malformed_addon_frame`。
- [ ] SDK 对 `switch` 的显式 `cid="default"` 与普通 `cid="chat-a"` 均接受；缺失、空白
      `cid` 仍被拒绝。
- [ ] Host 从非默认会话执行 `switch/default` 后返回成功，并把该玩家的 active
      conversation 设为 `default`；缺失目标仍返回 `INVALID_ARGUMENT`。
- [ ] Addon session client 对 `switch/default` 只发送一条预算内的
      `MCBEWS|SESSION|<json>` command，并能按 request id/action 关联响应。
- [ ] SDK protocol asset 生成检查、SDK 单测/ruff/mypy、Host 相邻 pytest、Addon
      protocol/test/lint/build 与隔离 wheel contract 全部通过。
- [ ] SDK/Host 两个仓库的改动分别位于从各自集成线创建的 `fix/*` 分支，且不包含用户的
      无关改动。

## Technical Notes

- Pydantic 2 的 `model_fields_set` 在本地验证中会把 alias `cid` 记录为字段名
  `conversation_id`：省略时集合中没有该字段，显式传入 alias 或字段名时均存在。
- Context7 未返回 `model_fields_set` 的直接片段，因此实现必须由 SDK 单测同时锁定 alias、
  字段名和省略三种输入，不能只依赖文档推断。
