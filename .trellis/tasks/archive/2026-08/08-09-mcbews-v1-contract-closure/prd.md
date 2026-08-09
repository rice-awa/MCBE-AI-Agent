# MCBEWS/1 契约闭合与协议内核收敛

## Goal

在不改变现有 `mcbews:*` / `MCBEWS|*` wire value、也不重新引入 `mcbeai:*`
兼容分支的前提下，修复
[`docs/review/addon-bridge-architecture-review-20260809.md`](../../../docs/review/addon-bridge-architecture-review-20260809.md)
确认的全部 P0、P1 与文档/发布契约问题，使 SDK、Host 和产品 Addon 对身份、CID、
schema、framing、session、approval 和发布版本形成可执行的单一契约。

用户价值：同一 `/wsserver` 连接中的多玩家、多对话与并发响应不再串桶；中文/emoji、
长会话列表和审批按钮不再产生静默超时；干净安装环境不会被本地 editable SDK 掩盖。

## Background and confirmed facts

- 主仓库基线是本地 `dev@4927482`，其中 review 文档是相对 `origin/dev@927ad97`
  超前的唯一提交；规划阶段工作树原本 clean。
- SDK 是被根仓库忽略的独立 Git 仓库，当前位于
  `feature/session-text-resp-fields@8a98f4c`；两仓库必须分别形成变更和验证记录。
- 运行时只支持 MCBEWS/1。小写 ScriptEvent ID 与大写 ToolPlayer 聊天前缀是两种
  transport 表示，不是需要统一大小写的缺陷。
- 官方 `@minecraft/server` 文档确认 `ChatSendBeforeEvent.sender` 是实际 `Player`；
  ScriptEvent 来源可以是 Block、Entity、NPCDialogue 或 Server。官方未承诺 461 字节
  commandLine 上限，因此它继续作为项目实测兼容预算，而不是官方 API 保证。
- 当前缺陷及证据以 review 第三至第五节为准：P0-1..P0-6、P1-1..P1-6 和六项文档/
  命名漂移均属于本任务范围。

## Requirements

### R1 — MCBEWS/1 协议内核与版本轴

- SDK 必须成为 channel identifier、capability/session schema version、framing version、
  稳定错误码、compact/typed DTO、sender classifier、framing/assembler limits 和 wire vectors
  的权威来源。
- `request_version` 等含混名称迁移为表达语义的名称；旧公共名称只通过明确的 deprecated
  alias 保留一个迁移周期。`AddonBridgeProfile` 不再作为内部可替换 seam；运行时明确使用
  MCBEWS/1 具体契约。
- SDK 内置 Addon 与产品 Addon 必须消费由同一 manifest/vectors 生成或校验的协议资产；
  手写字符串 parity 不能作为唯一 conformance 证明。
- capability request schema、session schema、transport framing 和 DDUI persistence version
  必须在代码和文档中分别命名，不得继续混称“v1/v2”。

### R2 — CID 与身份端到端闭环

- UI Chat 的 `cid` 必须从 Addon payload 经 SDK reassembly/callback、Facade/Hook、Host typed
  request 一直进入 `ChatRequest.conversation_id`；不得回退到接收时的 active conversation。
- user echo、assistant response 和 approval UI event 的 typed outbound message 必须显式携带
  `player_name`、`conversation_id` 和各自的 correlation id；`BrokerResponseBridge` 必须把
  CID/title/usage 交给 SDK delivery。
- 产品 Addon 的 buffer/stream key 至少包含 player + message/response + conversation 语义，
  两名玩家、两个 conversation 交错时互不覆盖。
- capability/UI Chat/session/approval 的 ToolPlayer 聊天入口必须共用可信 sender gate。
  `sender` 只证明 transport；业务 `player_name` / `conversation_id` 必须来自已验证 payload
  或 pending approval record，严禁把 `MCBEWS_BRIDGE` 当作业务玩家。
- 新 approval decision payload 必须携带真实玩家、对话和 approval id；对旧的仅 id 形式如
  保留兼容，只能通过 connection 内唯一 pending record 反查，冲突或 claimed owner 不一致时拒绝。

### R3 — Unicode-safe framing 与有界重组

- 产品 Addon 上行分片必须按 Unicode code point 和最终 `tell @s ...` UTF-8 字节数切分，
  每个 commandLine 不超过配置的实测预算；中文、emoji、空内容和 wrapper 无剩余空间都有测试。
- text response 重组必须有 TTL、buffer 数、chunk 数、单消息字节数、总缓冲字节数上限；
  校验索引全集、metadata consistency 和 duplicate conflict，并能清理 UI 中途关闭的 partial state。
- SDK 内置 Addon 与产品 Addon 对上述行为使用同一组 executable vectors。
- `u` token usage 只允许出现在完成帧；`cid`/`t` 在所有相关 frame 保持一致。

### R4 — Session 契约与 conversation domain result

- session request/response 必须使用 typed schema、稳定错误 code 和 request correlation；
  请求入口验证版本、action、player 与 action-specific 参数。
- 一个普通 `scriptevent` 不得被误当成协议分片。v1 session response 必须单帧原子发送；
  编码超过预算时返回仍可单帧解析的 `SESSION_RESPONSE_TOO_LARGE` 结构化错误，不发送碎 JSON。
- 提取 typed conversation operations result，供聊天命令 renderer 与 session DTO adapter
  分别消费；不得再调用文本 renderer 后用 `§c` 推断领域成功/失败。
- `list.message_count`、`saved`、`status`、`new/switch` 等 data 必须来自真实 Store/Manager，
  并保持 player + conversation 隔离。

### R5 — Typed Host outbound 与 approval 生命周期

- `run_command`、`ai_response_sync`、`session_resp`、`game_message` 的内部 producer/consumer
  必须迁移为可区分的 typed message；内部实现不得继续依赖 magic dict 字段猜测语义。
- `BrokerResponseBridge` 继续作为统一深 dispatcher，SDK delivery 继续拥有下行流控；不得在
  Host 调用点复制分片。
- `text_resp` v1 继续兼容 `role="approval"`，但 decoder 必须显式区分允许的 role/kind，
  unknown role 不得 cast 成 history role。approval frame 发送任务必须跟踪、报告异常并在断线停止时清理。

### R6 — 产品 Addon router 与 capability locality

- Router 必须校验 JSON object、capability schema version、request id、capability 和 payload；
  malformed/version/unsupported/handler failure 返回稳定且有界的错误 code。
- handler 异常不得逃出事件订阅或造成无响应超时；pre-ready queue 必须有界。
- capability handler 与 advertisement metadata 必须由同一 registry 投影，消除双写；
  `multiblock_placement` 的真实值统一为 `command_fallback`。

### R7 — 发布、配置兼容与权威文档

- SDK 版本提升到第一个包含本任务契约的 `0.2.0`，包内版本、Addon package metadata、
  release 检查和 changelog/release notes 同步；Host 依赖限定为 `>=0.2.0,<0.3.0`。
- SDK CI 必须从构建后的 wheel 安装并运行公共契约测试；Host 必须有一个从该 wheel 安装
  而非 editable checkout 的 contract test 流程。
- `AddonProtocolConfig` 只保留显式 deprecated/ignored 的兼容读取或诊断，不再暗示运行时可配；
  根 `models/addon_bridge.py` 改为 SDK typed model 的 deprecated re-export，不保留过期副本。
- 修正 `.trellis/spec/addon/bridge-protocol.md`、`docs/addon-bridge-protocol.md`、README、
  CLAUDE、配置示例中的 session wire、usage 完成帧、DDUI、capability 与版本说明。

## Acceptance Criteria

- [ ] AC1：SDK manifest/vectors 驱动 Python codec、SDK Addon 和产品 Addon 的常量与行为检查；
  任一 ID/schema/error/vector 漂移都会让 CI 失败。（R1、R3、R6）
- [ ] AC2：两名玩家各从两个 conversation 交错发送 UI Chat，四个 `ChatRequest` 和四条
  user/assistant history 均落到原始 player+cid 桶。（R2）
- [ ] AC3：伪造 sender 的 session/approval/capability/UI Chat 均被统一 gate 拒绝；可信
  ToolPlayer 的 session/approval 使用 payload/pending owner，而不是 transport sender。（R2）
- [ ] AC4：新旧 approval decision 的 owner 解析、跨玩家/跨对话拒绝、批量审批、过期、
  面板关闭和连接关闭均有回归测试；连接关闭后没有遗留发送 task。（R2、R5）
- [ ] AC5：256 个中文字符和 surrogate-pair emoji 的上行 payload 可无损重组，所有最终
  `tell @s` commandLine UTF-8 字节数不超过 461。（R3）
- [ ] AC6：乱序、重复同内容、重复冲突、metadata 冲突、缺片、TTL、buffer/chunk/message/
  total-byte 上限和双玩家同 id 均有确定性测试。（R3）
- [ ] AC7：`text_resp` frame 的 cid/title 全程一致，usage 仅完成帧出现；unknown role 被拒绝，
  不进入 history。（R2、R3、R5）
- [ ] AC8：约 1.9KB session result 不再被拆成不可解析 JSON；Addon 收到单帧结构化超限错误，
  正常大小的 list/saved/status/new/switch 响应可解析且 request id 对应正确。（R4）
- [ ] AC9：聊天命令与 session adapter 对同一 conversation operation 得到一致的 typed result；
  list 返回真实 message_count，失败不再依赖颜色判断。（R4）
- [ ] AC10：所有内部 outbound producer 使用 typed message，CID/player/correlation 缺失在构造或
  validation 阶段失败，Broker dispatcher 不再读取本任务四类 magic dict。（R2、R5）
- [ ] AC11：Router 对 malformed JSON、非法 shape、错误 schema version、未知 capability、
  handler throw、response send failure 和 pre-ready overflow 返回/记录稳定结果。（R6）
- [ ] AC12：SDK `0.2.0` wheel build、twine/check_dist、wheel-installed pytest、ruff、mypy、
  SDK Addon test/lint/typecheck/build 全绿。（R1、R7）
- [ ] AC13：Host 从新 wheel 安装后 import/contract tests、全量 pytest、ruff/mypy 通过；产品
  Addon pnpm test/lint/build 通过。（R7）
- [ ] AC14：文档、Trellis spec、配置模板与 executable manifest 对 session prefix、capability
  结果、usage frame、DDUI 现状和四个 version axis 一致。（R7）

## Out of Scope

- 不改变现有 wire value 的大小写或含义，不重新启用 `mcbeai:*` dual-read。
- 不设计或实现 MCBEWS/2，不为 session 引入无限 framed streaming；v1 使用明确的原子大小策略。
- 不把 Host conversation/session domain state 下沉到通用 SDK。
- 不自动 push 两个仓库、不创建远端 PR/tag、不发布 PyPI/GitHub Release；这些动作需要用户或
  发布者的外部授权。源码、版本和 release gate 会准备到可发布状态。
- 不要求真实 Minecraft `/wsserver` e2e 在默认 CI 运行；保留一条发布前手工 smoke checklist。

## Risks and deferred items

- Host pin `>=0.2.0,<0.3.0` 只有在 SDK `v0.2.0` 发布后才能在纯 PyPI 环境安装；实现顺序和
  合入门槛必须先 SDK release、后 Host merge。
- MCBE commandLine 461 字节来自项目实测，Bedrock 更新可能改变；manifest 中必须标注
  empirical，并允许配置降低预算，不能宣称官方稳定保证。
- session 响应超限会返回明确错误而非完整列表；若产品确实需要无上限列表，再单独设计
  additive 的 framed session channel/MCBEWS vNext。

## Planning status

- Blocking open questions: none.
- 本任务是一个跨仓库原子契约迁移：各阶段可单独验证，但只有 SDK、Host、Addon 和文档全部
  通过 integration gate 才算完成，因此不拆成会产生中间漂移的独立 Trellis 子任务。
