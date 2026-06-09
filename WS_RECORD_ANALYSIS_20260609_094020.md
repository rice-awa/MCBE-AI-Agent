# WebSocket 代理记录分析报告 — 2026-06-09 09:40:20~42:41

## 基本信息

| 项目 | 值 |
|------|------|
| 记录时间 | 2026-06-09 09:40:20 ~ 09:42:41 (约 140s) |
| 记录文件 | `test_logs/ws_records/20260609_094020/packets.json` (6712行, 275条) |
| 玩家 | `fantong7038` |
| Addon 工具玩家 | `MCBEAI_TOOL` |
| AI 模型 | deepseek/deepseek-v4-flash |
| 服务器版本 | MCBE AI Agent v2.2.0 |

## 会话流程总览

```
[连接建立]
  ↳ subscribe PlayerMessage (seq 2)
  ↳ 服务器推送欢迎信息 (seq 3)

[#帮助] 09:40:37
  ↳ 显示帮助菜单 (seq 7-12, 分2段tellraw)

[AI chat hi] 09:40:41
  ↳ ai_resp 回声用户消息
  ↳ (LLM处理 ~8s)
  ↳ 5条 tellraw + 1条 ai_resp 回复
  ↳ seq 16-37

[AI chat 给我64颗钻石] 09:40:57
  ↳ ai_resp 回声用户消息
  ↳ tool_call: run_minecraft_command(give @s diamond 64) ✅
  ↳ 多条 tellraw 回复 + 1条 ai_resp
  ↳ seq 38-71

[AI chat 测试下summon工具] 09:41:29
  ↳ ai_resp 回声用户消息
  ↳ tool_call: run_minecraft_command(summon sheep ...) ✅
  ↳ 多条 tellraw 回复 + 1条 ai_resp
  ↳ seq 72-106

[AI chat 检查一下目前的环境] 09:41:49
  ↳ ai_resp 回声用户消息
  ↳ bridge_request: get_player_snapshot ✅
  ↳ bridge_request: find_entities(sheep) ✅
  ↳ 命令执行: difficulty ⛔ (缺参数)
  ↳ 命令执行: gamerule showcoordinates ✅
  ↳ bridge_request: run_world_command(version) ⛔ (非法命令)
  ↳ 3分片 ai_resp 回复 (i=1/3,2/3,3/3)
  ↳ seq 107-275
```

## 数据包分类统计

| 类型 | 数量 | 占比 |
|------|------|------|
| 实质业务消息 | ~95 | 35% |
| PlayerMessage 回声 | ~120 | 44% |
| commandResponse | ~57 | 21% |
| binary 数据包 | 0 | 0% |

## 已发现的关键问题

### P0 — `commandResponse` 被 Pydantic 校验丢弃

**现象**：所有 57 条 `commandResponse` 消息（包括命令执行成功、失败、语法错误）均被 `MinecraftMessage` 模型拒绝，error 字段记录：

```
Input should be 'subscribe', 'commandRequest' or 'event'
```

**影响**：代理脚本**完全无法记录命令执行结果**。丢失的关键信息包括：
- 命令执行成功/失败状态 (`statusCode`)
- 执行结果描述 (`statusMessage`)
- 实体生成坐标、玩家数据等有效载荷

**根因**：`MinecraftMessage` 的 `header.messagePurpose` 枚举仅支持 `subscribe` / `commandRequest` / `event`，未包含 `commandResponse`。

**来源**：已验证在 `test_logs/ws_records/20260609_094020/packets.json` 和 `packets.jsonl` 中多次匹配到该错误。

---

### P1 — AI 响应双路径不一致

服务器采用两条路径发送 AI 回复：

| 路径 | 格式 | 携带元数据 | 到达时间 |
|------|------|------------|----------|
| **tellraw** | 原始文本分条 | ❌ (无 id/i/n/p/r/c) | ✅ 较快 |
| **scriptevent mcbeai:ai_resp** | 结构化分片 | ✅ (id/i/n/p/r/c) | ❌ 较晚 |

两条路径之间**无绑定关系**，客户端 UI 无法判断 tellraw 文本归属哪个 `resp-id`。当多条消息交叉时可能出现显示混乱。

**后续修复方向**：参考 `protoctl.py` 中 `AI_RESPONSE_TEMPLATE` 定义，考虑让 tellraw 路径也携带响应 ID 前缀，或统一走 ai_resp 路径。

---

### P1 — LLM 文本前缀残留影响可读性

多个 tellraw 消息包含 LLM 工具调用后的过渡词残留：

| 序号 | 文本 | 问题 |
|------|------|------|
| seq 75 | `§aThe好的，我来帮你测试一下 summon 指令！` | "The" 残留 |
| seq 110 | `§aThe好的，我来帮你看看周围的环境情况！` | "The" 残留 |
| seq 128 | `§aLet来看看你当前的环境信息：` | "Let" 残留 |
| seq 89 | `§a🐑Success成功啦！` | "Success" 残留 |
| seq 145 | `§a直接看起来能看到/d好这个让我换个方法试试看` | 多句碎片拼接 |

**根因**：LLM 内部分阶段思考的过渡词（"The好的"→ "好的"、"Let来看看"→ "来看看"）被当成了最终回复的一部分，流式 tellraw 直接输出了未经清理的文本。

**修复方向**：在 LLM system prompt 中增加"不要输出思考过程的过渡词"约束；或在 `worker.py` 中增加文本后处理清洗。

---

### P2 — LLM 发送了不完整/错误的 MC 命令

| 命令 | 结果 | 分析 |
|------|------|------|
| `difficulty` (无参数) | `语法错误：意外的""` | 忘记传值如 `easy`/`normal`/`hard` |
| `version` (通过 bridge) | `语法错误：意外的"version"` | `version` 不是 MCBE 有效命令（Java 版才有） |

**修复方向**：
- 在 `tools.py` 的命令校验层增加参数完整性检查
- 在 LLM 工具定义（tool schema）中强化参数的 required 标记
- 考虑在 bridge 层对非法命令名做预检

---

### P3 — 数据包回声噪声高

每条 `tellraw` 命令产生 **4 条额外消息**：
```
server → tellraw @a
  └→ PlayerMessage(receiver: fantong7038, sender: 外部)
  └→ PlayerMessage(receiver: MCBEAI_TOOL, sender: 外部)
  └→ commandResponse(statusCode: 0)   ← 还被校验丢弃
```

- 275 条记录中约 **65%** 是回声/响应
- 其中 `MCBEAI_TOOL` 为接收者的回声与 `fantong7038` 完全重复，无明显业务价值

**优化方向**：`server.py` 的消息处理器可以考虑过滤掉 receiver 为工具玩家的回声；或在代理脚本侧提供过滤模式。

---

### P3 — LLM 响应延迟波动

| 请求 | 首次 tellraw 延迟 | 完整 ai_resp 就绪 |
|------|------------------|-------------------|
| hi | ~8s | ~8s |
| 给我64颗钻石 | ~2s | ~11s |
| 测试summon | ~3.3s | ~7s |
| 检查环境 | ~3s | ~11s |

- 纯对话延迟较低 (~3-8s)
- 含工具调用时总延迟可达 11s（含命令执行 + 环境探测）

---

## 参考文档

- `models/minecraft.py` — `MinecraftMessage` Pydantic 模型定义
- `services/websocket/flow_control.py` — 统一流控中间件（tellraw/ai_resp 分片）
- `services/agent/worker.py` — AI Worker，LLM 响应处理入口
- `services/agent/tools.py` — MC 命令执行工具
- `services/addon/protocol.py` — Bridge 协议编码定义
- `claude_md/fix/FLOW_CONTROL_MIDDLEWARE_FIX.md` — 流控中间件修复历史
- `claude_md/summary/REFACTOR_SUMMARY.md` — 重构总结
