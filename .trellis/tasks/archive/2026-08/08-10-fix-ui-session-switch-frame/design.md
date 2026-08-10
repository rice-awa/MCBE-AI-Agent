# 修复 UI 会话切换帧解析 — 技术设计

## 边界与数据流

```text
会话列表点击 default
  → sessionClient.requestSession("switch", {cid: "default", player_name})
  → ToolPlayer: MCBEWS|SESSION|<atomic JSON>
  → SDK AddonBridgeService / SessionRequest
  → HostAddonIngressAdapter
  → CommandHandlers.handle_session_request
  → ConversationOperations._switch("default")
  → typed mcbews:session_resp
  → Addon 更新 activeConversationId
```

`player_name`、`request_id` 与 `cid` 在每个边界显式传递。SDK 只验证/分类线请求；Host
拥有实际会话状态和切换行为；Addon 不增加绕过 SDK 的特殊命令路径。

## 根因修复

### SDK 字段存在性

`SessionRequest.conversation_id` 继续保留 wire 缺省值 `"default"`，以兼容不需要 cid 的
其他 action。`switch` 的 after validator 改为检查 `"conversation_id" in
self.model_fields_set`：

- `{"action":"switch"}`：缺少显式目标，拒绝。
- `{"action":"switch","cid":""}`：字段 validator 拒绝空白。
- `{"action":"switch","cid":"default"}`：显式合法目标，接受。
- `{"action":"switch","cid":"chat-a"}`：接受。

Pydantic 对 alias `cid` 和字段名 `conversation_id` 都在 `model_fields_set` 中记录规范字段名，
由回归测试锁定该行为。

### Host 目标值

`ConversationOperations._switch()` 只拒绝 `None` / 空白，不再拒绝
`DEFAULT_CONVERSATION_ID`。Host 已能按任意归一化 conversation id 分桶，因此切换到
`default` 不需要新状态或迁移。

## 协议资产与版本

SDK `vectors.json` 新增 `session-switch-default` 可执行向量，并通过既有生成器同步：

- SDK reference Addon `protocol.ts`
- SDK Python fixture
- 产品 Addon 的 manifest/vector 副本和 `protocol.generated.ts`

协议线与 schema 版本不变。由于已发布的 `0.2.0` wheel 含有缺陷，SDK 源码准备
`0.2.1` patch，Host 最低依赖与 wheel gate 同步到 `0.2.1`。发布操作本身不在本任务授权内。

## 测试策略

先建立两条失败回归：

1. SDK 用用户日志等价帧断言 trusted ToolPlayer 分类成功；修复前因
   `switch requires cid` 失败。
2. Host 从 `chat-a` 切换到 `default` 并断言 active id；修复前返回
   `INVALID_ARGUMENT`。

随后补充负例（缺失/空白 cid）、Addon 单帧发送关联测试、生成资产检查和隔离 wheel
contract。真实 Minecraft smoke 作为发布后检查，不用它代替自动化门禁。

## 兼容与回滚

- 接受显式 `default` 是对既有合法会话 ID 的恢复，不改变其他 action 或响应结构。
- 仍 fail-closed 拒绝缺失/空白 switch 目标。
- SDK 与 Host 分别保持独立提交；需要回滚时可按仓库回退对应提交，但 Host `>=0.2.1`
  依赖只能在 SDK patch 可用后进入发布线。
