# MCBE AI Agent / mcbe-ws-sdk Addon 桥接架构审查

> 审查日期：2026-08-09  
> 主仓库基线：`dev@927ad971ba7b1e68f429d54c0a619636a4d3a5c4`  
> SDK 基线：`feature/session-text-resp-fields@8a98f4c497c4edf5d2e7152dd65c288714f4a5ba`  
> 范围：主仓库 Host/Gateway、`mcbe-ws-sdk` Python 源码、SDK 内置 Addon、产品 Addon、协议文档、配置和测试  
> 性质：只读架构审查；未修改项目源码

## 一、执行摘要

当前 Addon 桥接的基础骨架并不差。SDK 的每连接 `AddonBridgeSession`、pending future、TTL/容量限制、UTF-8 字节级下行分片、`McbeOutboundDelivery` 和 capability registry 都有真实的 **depth** 与 **leverage**，应当保留。

真正的问题也不只是“协议名称不清晰、大小写混用”，而是：

1. **协议的兼容版本、channel schema 版本、传输形式和持久化版本混在同一个命名空间里。** `McbewsV1Profile` 使用 capability request schema `v=2`，session 又使用 `v=1`，UI 状态还有自己的 `v=2`；这些数字分别代表什么没有被明确命名。
2. **相同 wire contract 被三处实现。** SDK Python、SDK 内置 Addon、产品 Addon 各自维护 codec、分片、重组和常量，且已经发生行为分叉。产品 Addon 获得了 UI/session 能力，却丢失了 SDK 内置 Addon 已有的 Unicode、TTL、容量和元数据一致性保护。
3. **跨层 interface 没有携带完整语义。** `conversation_id` 在 UI Chat 回调和 AI 下行 adapter 中分别被丢弃；approval decision 又没有携带真实玩家身份。已有字段并不等于端到端契约已经闭合。
4. **Host 的控制消息入口没有统一身份策略。** Bridge/UI Chat 会验证 `MCBEWS_BRIDGE`，Session/Approval 却只匹配前缀并混用 transport `sender` 与业务 `player_name`。
5. **“通用发送”被误用为“协议分片”。** SDK 会把长 `session_resp` JSON 拆成多个普通 `scriptevent`，产品 Addon 却对每个片段直接 `JSON.parse`；数据稍大便必然超时。
6. **本地 editable SDK 掩盖了发布契约。** 主仓库依赖仅为 `mcbe-ws-sdk>=0.1.0`，但当前 Host 已调用 tag `v0.1.0` 中不存在的 `usage/conversation_id/title/session_response_message_id` 能力；SDK 源码版本仍然是 `0.1.0`。

最高优先级不是立即重命名 wire value，而是先把 **MCBEWS/1 协议内核收敛为一个深 module**，让 Host 和产品 Addon 只通过明确的 adapter 使用它；同时先以不破坏 wire 的方式闭合 CID、身份、Unicode 分片和 session 响应契约。

## 二、当前真实架构

### 2.1 六条实际 channel

| 语义 channel | Python → Addon | Addon → Python | 当前 ownership |
|---|---|---|---|
| capability request | `mcbews:bridge_req` ScriptEvent | — | SDK codec + 产品 Addon router |
| capability response | — | `MCBEWS\|BRIDGE\|...` ToolPlayer chat | 产品 Addon chunker + SDK session |
| UI chat | — | `MCBEWS\|UI_CHAT\|...` ToolPlayer chat | 产品 Addon + SDK service/facade |
| text response | `mcbews:text_resp` ScriptEvent | — | SDK delivery + 产品 Addon responseSync |
| session | `mcbews:session_resp` ScriptEvent | `MCBEWS\|SESSION\|<json>` ToolPlayer chat | Host 与产品 Addon自定义实现 |
| approval | 复用 `mcbews:text_resp`，以 `r="approval"` 携带内层 JSON | `MCBEWS\|TOOL_APPROVE/DENY\|...` ToolPlayer chat | Host broker + 产品 Addon UI |

关键证据：

- wire 常量集中在产品 Addon 的 [constants.ts](/home/riceawa/Desktop/code/MCBE-AI-Agent/MCBE-AI-Agent-addon/scripts/bridge/constants.ts:1) 和 SDK 的 [profile.py](/home/riceawa/Desktop/code/MCBE-AI-Agent/mcbe-ws-sdk/src/mcbe_ws_sdk/profiles/mcbews_v1/profile.py:17)。
- capability 生命周期由 SDK [service.py](/home/riceawa/Desktop/code/MCBE-AI-Agent/mcbe-ws-sdk/src/mcbe_ws_sdk/addon/service.py:68) 与 [session.py](/home/riceawa/Desktop/code/MCBE-AI-Agent/mcbe-ws-sdk/src/mcbe_ws_sdk/addon/session.py:68) 管理。
- Host 下行在 [broker_bridge.py](/home/riceawa/Desktop/code/MCBE-AI-Agent/services/gateway/broker_bridge.py:144) 按 magic dict `type` 分派。
- 产品 Addon 的接收和 UI 状态处理集中在 [responseSync.ts](/home/riceawa/Desktop/code/MCBE-AI-Agent/MCBE-AI-Agent-addon/scripts/bridge/responseSync.ts:44) 与 [sessionClient.ts](/home/riceawa/Desktop/code/MCBE-AI-Agent/MCBE-AI-Agent-addon/scripts/bridge/sessionClient.ts:353)。

### 2.2 大小写不是首要缺陷

`mcbews:*` 小写用于 Minecraft ScriptEvent ID，`MCBEWS|*` 大写用于模拟玩家聊天隧道，二者是不同 transport adapter 的可识别 wire 形式。这种差异可以保留，机械改成全小写或全大写会制造 breaking change，却不会增加模块深度。

应当修正的是“命名没有表达 transport 和 channel”：

- `bridge` 同时指 capability 通道、SDK 总桥接模块、Host broker 下行 dispatcher、block operation result mapping，语义过载。
- `response_message_id` 实际只表示 `text_response` ScriptEvent。
- `bridge_sender` 实际是可信模拟玩家名称。
- `SESSION_REQ_MESSAGE_ID = "mcbews:session_req"` 已声明但不参与实际 session 请求。
- `AI_RESP_MESSAGE_ID`、`BRIDGE_MESSAGE_ID` 等旧 alias 仍被本地消费者使用。

因此建议保留现有 wire value，但在 module 内部使用下列语义名称；公共符号通过 alias 渐进迁移：

| 当前 wire / 名称 | 推荐的内部语义名称 | 处置 |
|---|---|---|
| `mcbews:bridge_req` | `capability_request_script_event_id` | wire 保留 |
| `MCBEWS\|BRIDGE` | `capability_response_chat_prefix` | wire 保留 |
| `MCBEWS\|UI_CHAT` | `ui_chat_chunk_prefix` | wire 保留 |
| `mcbews:text_resp` | `text_response_script_event_id` | v1 wire 保留；approval 后续拆 channel |
| `MCBEWS\|SESSION` | `session_request_chat_prefix` | wire 暂时保留 |
| `mcbews:session_resp` | `session_response_script_event_id` | wire 保留 |
| `MCBEWS_BRIDGE` | `trusted_bridge_player_name` | wire 保留 |
| `request_version` | `capability_request_schema_version` | 本地改名、兼容旧属性 |

文档应显式使用四个版本轴：

- **协议兼容线**：`MCBEWS/1`，表示一组可互操作 channel。
- **channel schema version**：例如 capability request schema 2、session schema 1。
- **transport framing version**：只有 framing 变化时才增加。
- **persistence schema version**：例如 DDUI state v2；不属于 wire protocol。

`mcbeai:ui_state` 是持久化键，不是遗留 wire channel，不应因 namespace 看起来旧而直接改名。

## 三、已确认的高优先级问题

### P0-1：`conversation_id` 在两条 seam 上丢失

Addon UI Chat payload 已包含 `cid`，[SDK codec](/home/riceawa/Desktop/code/MCBE-AI-Agent/mcbe-ws-sdk/src/mcbe_ws_sdk/profiles/mcbews_v1/codec.py:170) 也解析出 `conversation_id`；但 [SDK callback interface](/home/riceawa/Desktop/code/MCBE-AI-Agent/mcbe-ws-sdk/src/mcbe_ws_sdk/addon/service.py:39) 只有 `(connection_id, player_name, message)`，facade 和 Host hook 继续沿用三参数形式。因此 UI 选择的对话在进入 Host 前已经丢失。

反向路径同样断裂：worker 生成 `conversation_id`，但 [BrokerResponseBridge._ai_sync](/home/riceawa/Desktop/code/MCBE-AI-Agent/services/gateway/broker_bridge.py:376) 只读取 player、role、text、usage，未传给 SDK delivery。产品 Addon 最终在 [responseSync.ts](/home/riceawa/Desktop/code/MCBE-AI-Agent/MCBE-AI-Agent-addon/scripts/bridge/responseSync.ts:221) 回退到 `default`。

实际后果：

- 玩家切换对话后，输入可能进入当前 active conversation，而不是 UI 发送时的 conversation。
- assistant/user echo 可能写入 `default` history。
- `streamingStates` 仅按 `conversation_id` 建 key；多玩家都落入 `default` 时会互相覆盖。

这是 interface 数据契约问题，不是单个调用点 bug。修复应同时覆盖 UI Chat ingress、Host typed message、worker outbound、SDK delivery 和 Addon state key，并用两个玩家、两个 conversation 的交错测试锁定。

### P0-2：Session/Approval 混淆 transport identity 与业务玩家

[HostConnectionHook](/home/riceawa/Desktop/code/MCBE-AI-Agent/services/gateway/hook.py:190) 对 Session/Approval 只匹配消息前缀，没有像 Bridge/UI Chat 一样要求 `sender == MCBEWS_BRIDGE`。Session 还使用 `setdefault`，因此 payload 中伪造的 `player_name` 会优先于事件 sender。

正确模型应区分：

- `sender`：transport identity，只用于证明消息来自可信 ToolPlayer。
- `player_name`：业务目标玩家，来自经过验证的 payload 或 pending record。
- `conversation_id`：该玩家下的目标对话。

Approval 还有一个更直接的闭环问题：产品 UI 让 ToolPlayer 执行 `tell @s ...approval_id`，[hook.py](/home/riceawa/Desktop/code/MCBE-AI-Agent/services/gateway/hook.py:215) 随后把 ToolPlayer 的 sender 当作 `player_name`；而 [handle_tool_approval](/home/riceawa/Desktop/code/MCBE-AI-Agent/services/gateway/command_handlers.py:1213) 会按 `(connection, player, conversation)` 查 pending store。也就是说，DDUI 按钮没有携带原始玩家/对话，和项目已有的玩家隔离模型不一致。

建议建立一个统一的 authenticated ingress adapter：先验证 transport sender，再解码/校验 channel payload，最后产生带 `player_name`、`conversation_id` 和 correlation id 的内部消息。不要在 hook 的各个 `startswith` 分支里分别决定身份规则。

### P0-3：产品 Addon 上行分片不是 UTF-8/命令预算安全的

产品 Addon [chunking.ts](/home/riceawa/Desktop/code/MCBE-AI-Agent/MCBE-AI-Agent-addon/scripts/bridge/chunking.ts:22) 使用 JavaScript `payload.length` 与 `slice`，实际按 UTF-16 code unit 切分；虽然 [constants.ts](/home/riceawa/Desktop/code/MCBE-AI-Agent/MCBE-AI-Agent-addon/scripts/bridge/constants.ts:18) 声明了 461 字节预算和 256 code point，但两者没有参与算法。

本次最小探针已复现：

- 256 个中文字符的内容为 768 UTF-8 字节；加 `tell @s MCBEWS|BRIDGE|...` 包装后约 797 字节，超过实测 461 字节限制。
- 在奇数 code-unit 偏移下按 256 切片，会把 emoji 的 high/low surrogate 分到两个 chunk。

SDK 内置 Addon 已有 code-point 与 UTF-8 byte-aware 的实现及测试：[SDK chunking.ts](/home/riceawa/Desktop/code/MCBE-AI-Agent/mcbe-ws-sdk/addon/scripts/bridge/chunking.ts:8)。产品 Addon 不应维护一份更弱的协议实现。

### P0-4：产品 Addon `responseSync` 的重组状态无界且隔离键不足

[responseSync.ts](/home/riceawa/Desktop/code/MCBE-AI-Agent/MCBE-AI-Agent-addon/scripts/bridge/responseSync.ts:44) 当前：

- chunk buffer 只按 message id，stream state 只按 conversation id。
- 没有 TTL、最大 buffer、最大 chunks、单消息/总字节上限。
- 不检查同一 message id 的 `n/player/role/cid/title` 是否一致。
- 完成条件基于 `buffer.size >= n`，没有验证索引集合恰好为 `1..n`。
- duplicate index 不检查内容冲突。
- UI 关闭时的 partial finalize 与持久化生命周期没有闭合。

SDK 内置 Addon 的 [ResponseAssembler](/home/riceawa/Desktop/code/MCBE-AI-Agent/mcbe-ws-sdk/addon/scripts/bridge/responseSync.ts:55) 已实现这些保护。建议保留产品 `responseSync` 作为 UI facade，但把 framing/reassembly 下沉到共享的有界 assembler，再由 UI/history sink 消费 typed message。这会同时增加 depth、leverage 和 locality。

### P0-5：长 `session_resp` 的泛化分片与消费者不兼容

[BrokerResponseBridge._session_resp](/home/riceawa/Desktop/code/MCBE-AI-Agent/services/gateway/broker_bridge.py:394) 把整个 JSON 交给 `send_scriptevent`；SDK [FlowControlMiddleware.chunk_scriptevent](/home/riceawa/Desktop/code/MCBE-AI-Agent/mcbe-ws-sdk/src/mcbe_ws_sdk/flow/flow_control.py:78) 会按命令预算拆成多个独立 ScriptEvent。产品 Addon [sessionClient.ts](/home/riceawa/Desktop/code/MCBE-AI-Agent/MCBE-AI-Agent-addon/scripts/bridge/sessionClient.ts:68) 却对每个 event.message 直接 `JSON.parse`，没有 reassembly。

本次探针构造了约 1.9KB 的合法 session JSON：SDK 生成 5 个 ScriptEvent，5 个 message fragment 都不是可独立解析的 JSON。`list` / `saved` 数据增长后会稳定失败并等待 timeout。

短期不破坏 wire 的方案是给 session response 设置明确的最大编码字节数，超限返回结构化错误；中期应使用与 text response 相同的 framed protocol kernel，或新增独立的 session response chunk envelope。不能把“任意字符串切段”当作“消息协议分片”。

### P0-6：SDK 发布版本与 Host 最低依赖不匹配

主仓库 [requirements.txt](/home/riceawa/Desktop/code/MCBE-AI-Agent/requirements.txt:3) 仅要求 `mcbe-ws-sdk>=0.1.0`。SDK 唯一 tag `v0.1.0` 的 `McbewsV1Delivery.send_response` 不接受 `usage/conversation_id/title`，也没有最新 session profile 字段；这些能力由 tag 之后的 `3c41cbb` 才加入。SDK 当前 [pyproject.toml](/home/riceawa/Desktop/code/MCBE-AI-Agent/mcbe-ws-sdk/pyproject.toml:7) 仍声明 `0.1.0`。

当前开发环境从嵌套仓库 editable import，因此调用可成功；干净环境按最低约束安装 `0.1.0` 时，Host 的 `usage=` 调用可能直接 `TypeError`。需要：

1. 为 SDK 增加包含这些契约的版本号和 release。
2. Host 最低版本 pin 到第一个包含它们的 release。
3. CI 从构建后的 wheel 安装运行 Host contract tests，避免 editable checkout 掩盖问题。

## 四、重要但可在 P0 后处理的问题

### P1-1：`AddonBridgeProfile` 是浅 seam

[AddonBridgeProfile](/home/riceawa/Desktop/code/MCBE-AI-Agent/mcbe-ws-sdk/src/mcbe_ws_sdk/profiles/__init__.py:10) 只抽象常量和延迟；[service.py](/home/riceawa/Desktop/code/MCBE-AI-Agent/mcbe-ws-sdk/src/mcbe_ws_sdk/addon/service.py:27) 与 [session.py](/home/riceawa/Desktop/code/MCBE-AI-Agent/mcbe-ws-sdk/src/mcbe_ws_sdk/addon/session.py:12) 仍直接 import `mcbews_v1` codec/model，`McbewsV1Delivery` 也接受具体 profile。

删除检验显示：删除 service/session/codec 会让 pending、timeout、chunk validation 和错误处理散回 Host，说明它们是真正的深 module；删除 `AddonBridgeProfile` 只会失去一组属性类型，说明这个 interface 目前暗示了并不存在的可替换性。

建议不要为假设中的第二套协议继续扩展常量袋。近期明确只支持 MCBEWS/1；当真实 vNext 出现时，让 profile-owned adapter 同时拥有 classifier、codec、framing、delivery 和 channel registry，届时才形成真正 seam。

### P1-2：Host broker 使用 untyped magic dict

[BrokerResponseBridge._handle](/home/riceawa/Desktop/code/MCBE-AI-Agent/services/gateway/broker_bridge.py:144) 识别 `run_command`、`ai_response_sync`、`session_resp`、`game_message`，producer 在 worker/handler 中手写字段。CID 被丢失正是这一宽松 interface 的典型后果。

建议建立一个 typed outbound message module，用可区分消息类型承载 player、conversation、correlation、payload 和 delivery semantics；BrokerResponseBridge 保留为深 dispatcher，SDK delivery 作为 wire adapter。这样可以提高 locality，而不是拆掉已经集中了流控的 BrokerResponseBridge。

### P1-3：Session 复用文本命令 renderer，domain result 被颜色推断

[handle_session_req](/home/riceawa/Desktop/code/MCBE-AI-Agent/services/gateway/command_handlers.py:469) 把结构化 action 转回字符串 option，调用聊天命令逻辑，再通过 `§c` 判断失败，随后重新查询状态拼装 JSON。`list.message_count` 还被硬编码为 `0`。

这里已经存在两个真实消费者：聊天命令 renderer 与 Addon session response adapter。因此适合提取一个 conversation operations module，返回 typed domain result；两个 adapter 分别把它渲染为聊天文本或 session DTO。这样比继续解析颜色有更高 depth，也能让权限、错误码和数据查询保持局部一致。

### P1-4：`text_resp` 已不是 text-only channel

Approval 通过 `role="approval"` 复用 `mcbews:text_resp`，并把 JSON 再编码进 `c`。这迫使 Addon 的文本重组模块理解审批 UI，unknown role 也容易被 cast 成 history role。当前 broker 还用未跟踪的 `asyncio.create_task` 发送 approval frame。

v1 可以继续兼容读取；vNext 应把它命名为 typed UI event stream，或将 approval request 拆成独立 channel，以显式 `kind` 表达语义。不要在 v1 中直接重命名 wire ID。

### P1-5：产品 Addon router 失去了 SDK 参考实现的协议保护

产品 [router.ts](/home/riceawa/Desktop/code/MCBE-AI-Agent/MCBE-AI-Agent-addon/scripts/bridge/router.ts:67) 对 `JSON.parse` 结果直接 type cast，不验证版本/字段，不捕获 handler 异常，也没有稳定错误码。SDK 内置 Addon 已有 malformed/version/capability/handler failure 的结构化错误和受限启动队列。

产品 capability registry 本身是好的 seam；新增 capability 的主要扩展成本是 handler registry 与 capability advertisement 需要双写。可以让 capability module 同时导出 handler 与 metadata，避免两处手工登记，但不需要为每个 capability 建立额外抽象层。

### P1-6：跨仓库 parity checker 覆盖面不足

SDK [check_protocol_names.py](/home/riceawa/Desktop/code/MCBE-AI-Agent/mcbe-ws-sdk/tools/check_protocol_names.py:28) 只核对五个基础值，且只扫描 SDK 内置 Addon；它不覆盖产品 Addon、session、approval、可选 frame 字段、版本或行为向量。当前检查通过不能证明产品协议一致。

建议由协议内核维护共享 wire vectors，Python codec、SDK Addon 和产品 Addon 都消费同一批 fixture。字符串 parity 只是其中最浅的一层。

## 五、文档与命名漂移

以下偏差应作为零行为变更的第一批清理：

1. [.trellis/spec/addon/bridge-protocol.md](/home/riceawa/Desktop/code/MCBE-AI-Agent/.trellis/spec/addon/bridge-protocol.md:39) 写 `MCBEWS|SESSION_REQ|request_id|json_payload` 且宣称请求分片；实际代码使用 `MCBEWS|SESSION|<json>`，而 [docs/addon-bridge-protocol.md](/home/riceawa/Desktop/code/MCBE-AI-Agent/docs/addon-bridge-protocol.md:127) 更接近实现。
2. 产品 capability 广告把 `multiblock_placement` 返回为 `command_fallback`，主协议文档仍写 `unsupported`。
3. Trellis runtime spec 说 usage 只出现在完成帧；当前 [codec.py](/home/riceawa/Desktop/code/MCBE-AI-Agent/mcbe-ws-sdk/src/mcbe_ws_sdk/profiles/mcbews_v1/codec.py:198) 把 `u/cid/t` 放在每个 frame。本次探针确认 5/5 个 frame 都携带这些元数据。
4. 主协议文档仍把 UI 描述为旧阶段，产品 Addon 已使用 DDUI。
5. `AddonProtocolConfig` 仍暴露旧 `ai_resp_message_id` 等字段，但运行时强制使用 SDK profile；这是“看起来可配置、实际上不配置”的浅 interface。
6. 根仓库 [models/addon_bridge.py](/home/riceawa/Desktop/code/MCBE-AI-Agent/models/addon_bridge.py:1) 已无运行时引用且缺少新字段；应先做 deprecated re-export，再观察外部引用后删除。

## 六、建议的目标 module 划分

### 6.1 MCBEWS/1 protocol kernel（最高推荐）

Ownership 放在 SDK，但不是继续扩张常量 profile，而是集中以下复杂度：

- channel identifiers、schema version 与稳定错误码。
- 长字段内部 DTO 与 compact wire key 的双向 adapter。
- Unicode/UTF-8 byte-safe framing。
- 有界 assembler：TTL、buffer/chunk/byte limits、metadata consistency、duplicate conflict。
- sender/source classification policy。
- 可执行 wire vectors 与兼容性说明。

产品 Addon 只保留 capability handlers、ToolPlayer 生命周期、UI/history sink、session UI；Host 只保留 domain operations 和 broker dispatch。两端都不重新实现 framing grammar。

若暂时无法共享 TypeScript package，也应先共享生成的 manifest/fixture 和同构 assembler 测试，禁止继续靠手工复制判断一致性。

### 6.2 Host addon ingress adapter

统一接收 Bridge response、UI Chat、Session request 和 Approval decision：

- transport sender 验证集中在一处。
- wire 短字段只在 adapter 内出现。
- 输出 typed internal message，显式携带 connection/player/conversation/correlation。
- hook 只路由，不再在多个 `startswith` 分支中解析 JSON 和决定身份。

### 6.3 Host outbound message module

替换 magic dict producer contract，确保创建消息时必须选择 player、conversation 和 correlation semantics。BrokerResponseBridge 继续负责选择 SDK delivery、流控和生命周期。

### 6.4 Conversation operations module

集中 `list/new/switch/status/clear/save/restore/delete/compress` 的 domain operation 和 typed result。聊天 renderer 与 Addon session adapter 是两个独立 adapter，不再通过文本颜色互相复用。

### 6.5 产品 Addon UI adapters

保留 `responseSync` 和 `sessionClient` 对 UI 的窄 facade，但将 protocol parse/reassembly 委托给 kernel；approval decision 也通过 ToolPlayer transport adapter 发送，不由 UI panel 直接查 world 并拼命令。

## 七、模块删除检验

| module / interface | 删除后复杂度去向 | 结论 |
|---|---|---|
| SDK `AddonBridgeSession` | pending、timeout、chunk consistency 散入 facade/Host | 深 module，保留 |
| `McbeOutboundDelivery` / `McbewsV1Delivery` | 字节预算、流控、frame 编码散入调用点 | 深 module，保留并收敛入口 |
| `BrokerResponseBridge` | broker 消费、发送策略、流控散入 worker/handler | 深 module，保留；输入改 typed |
| 产品 `responseSync` | UI streaming/history/usage/approval 散入面板 | facade 有价值；协议 assembler 下沉 |
| 产品 `sessionClient` | pending/timer/listener 散入各 panel | 深度成立，保留并加强 decoder |
| `AddonBridgeProfile` 常量 interface | 几乎只失去属性类型 | 浅 seam；完成或收回可替换承诺 |
| `AddonProtocolConfig` 镜像 | 运行时几乎无变化 | 浅/误导；迁移后删除或只作诊断 |
| 重复 `AddonBridgeClient` Protocol / root models | 少量 import 变化 | 收敛到一个真实 interface |

## 八、渐进迁移路线

### 阶段 0：止损与契约闭合（不改现有 wire value）

1. 透传 UI Chat 和 text response 的 `conversation_id`；stream key 至少包含 player + response/conversation。
2. Session/Approval 入口增加可信 ToolPlayer gate；approval decision 携带或从 pending record 解析真实 player/conversation。
3. 产品 Addon 采用 byte-aware chunker 和 bounded assembler。
4. Session response 先设置编码大小上限并返回结构化错误，避免发送不可解析的片段。
5. 修复 session 文档、capability 语义、usage frame 说明和 DDUI 现状。
6. 发布新版 SDK，提升 Host 最低依赖，并从 wheel 执行 contract tests。

### 阶段 1：深化现有 module（仍保持 MCBEWS/1 兼容）

1. 建立 protocol kernel 和共享 vectors。
2. 引入 typed Host ingress/outbound messages。
3. 提取 conversation operations module。
4. 产品 Addon router 使用统一 decoder/error model；capability handler 与 metadata 共置。
5. approval 发送进入 ToolPlayer adapter；跟踪并在连接关闭时清理异步发送任务。

### 阶段 2：只有出现真实新需求时才设计 vNext

考虑以下变化时再建立 `MCBEWS/2` 或显式新 channel：

- session response 需要无上限 framed streaming。
- approval、progress、tool result 需要统一 typed UI event stream。
- 需要第二种物理 transport 或第三方 Addon 实现。

新版本应 additive/dual-read 迁移，不能原地改变 `mcbews:*` 和 `MCBEWS|*` 的含义。

### 阶段 3：清理兼容层

在一个明确 deprecation 周期后删除 `BRIDGE_MESSAGE_ID`、`AI_RESP_MESSAGE_ID`、unused `SESSION_REQ_MESSAGE_ID`、旧配置镜像和 root duplicate models；先迁移内部消费者，再删除 alias。

## 九、测试与发布门槛

建议新增一套共享 contract matrix：

| 场景 | 必须验证 |
|---|---|
| capability | malformed JSON、版本拒绝、handler throw、unknown capability、乱序/重复/超时 |
| UI Chat | CJK/emoji、CID round trip、两玩家交错、duplicate conflict、buffer limits |
| text response | cid/title/usage、空文本、metadata conflict、两并发 stream、UI 关闭中途 |
| session | sender gate、action schema、长 list/saved、request correlation、timeout/send failure |
| approval | 原始玩家/对话归属、伪造前缀、面板关闭、批量审批、连接关闭 |
| packaging | 从 wheel 安装的最低 SDK 版本能运行 Host contract tests |

真实 MCBE `/wsserver` e2e 很昂贵，但共享 wire vectors 与一条最小真实联调链路都需要：前者锁定 codec/framing，后者验证 ScriptEvent source、ToolPlayer chat sender 和命令字节限制等模拟测试无法保证的事实。Minecraft 官方文档也说明 ScriptEvent source 可能是 Block、Entity、NPCDialogue 或 Server，因此 source policy 应由实测和显式 adapter 共同定义，而不是依赖隐含假设：<https://learn.microsoft.com/en-us/minecraft/creator/scriptapi/minecraft/server/scripteventsource>。

## 十、本次验证现状

- 主仓库与 SDK 工作树在审查后均保持 clean。
- Host 定向测试：`38 passed, 1 failed`。失败是旧测试仍断言 command origin 为 `say`，当前 SDK 产生 `player`，属于测试/transport contract 漂移。
- 产品 Addon：`118 passed, 1 failed`。失败是源码已有“同意/拒绝”按钮，测试仍只接受旧按钮集合。
- SDK `check_protocol_names.py`：通过；但其扫描范围和字段覆盖不足，不能作为跨仓库 conformance 证明。
- SDK 子审查验证：Python 源码测试 `216 passed, 5 errors`；5 个 error 均为环境缺少 `build` 包的 release build 测试收集错误，ruff/mypy/protocol checker 通过，SDK 内置 Addon 测试通过。
- 已复现 256 中文字符越过 461 字节预算、emoji surrogate 被切断、长 session JSON 被拆成不可独立解析片段、`u/cid/t` 被复制到所有 text response frames。

## 十一、不建议做的事

- 不要为了“看起来统一”直接改 wire 大小写。
- 不要重新启用旧 `mcbeai:*` wire dual-read；`mcbeai:ui_state` 仅作为存储键保留。
- 不要把 Host 的 conversation/session domain state 下沉进通用 SDK。
- 不要拆散 SDK session、delivery 或 BrokerResponseBridge 这些已有深度的 module。
- 不要为每个 capability 创建独立抽象层；registry 已是有效 seam。
- 不要把 WebSocket `requestId`、capability/session `request_id`、text response `id`、UI `msg_id` 和 `conversation_id` 机械合并；它们是不同 correlation scope。
- 不要在没有第二个真实 profile 的情况下继续增加假想可替换接口。

## 十二、最终建议

优先启动一个“**MCBEWS/1 契约闭合与协议内核收敛**”主题，而不是“协议统一改名”主题。

第一批交付应同时包含：CID 全链路、Session/Approval 身份模型、byte-safe chunker、bounded assembler、session response 大小策略、共享 wire vectors、SDK release/pin 和权威文档修正。它们共同解决的是同一个架构根因：协议知识散落在多个浅 adapter 中，缺乏一个拥有 framing、identity、schema 和 compatibility 的深 module。

完成这一批后，再依据真实扩展需求决定是否引入 MCBEWS/2。这样既保留现有 wire 兼容性，也能显著降低新增 channel、字段和 UI 能力时的修改面与回归概率。
