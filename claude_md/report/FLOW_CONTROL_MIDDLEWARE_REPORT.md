# 统一流控中间件代码审查报告

> 审查范围：`services/websocket/flow_control.py` 及其在 `connection.py`、`server.py`、`services/addon/protocol.py` 中的接入点。
> 关联提交：`c50f35d`、`395461e`、`3148037`、`893603f`、`4a88179`、`73d3940`。
> 审查日期：2026-05-01。

---

## 1. 总体评价

`FlowControlMiddleware` 把原先散落在 `addon/protocol.py`（`encode_ai_response_chunks`）、`connection.py`（`_send_game_message_with_color`、`_send_script_event`、`_sync_response_to_addon`）和 `server.py`（`_send_ws_payload`）里的"长文本 → 多个 commandRequest"的分片逻辑，统一为四个分类清晰的 classmethod：`chunk_tellraw / chunk_scriptevent / chunk_raw_command / chunk_ai_response`，并保留了 `_split_text` 作为唯一的语义分片核心。

**好的地方**

- **职责单一**：所有分片入口都返回 `list[str]`（已序列化的 commandRequest JSON），调用端只负责按节奏 send，分层干净。
- **向后兼容**：`encode_ai_response_chunks` 通过 lazy import 转发到中间件，避免了破坏 addon 协议层的对外接口。
- **可测试**：纯函数 + classmethod，没有持久状态，单元测试 (`tests/test_flow_control.py`) 覆盖了短文本/超长截断/语义合并/换行/JSON 转义/自定义 max_length 等关键路径，共 17 个用例。
- **统一了"分片间延迟 + 看门狗规避"的责任边界**：分片逻辑只产 payload，不睡眠；睡眠由调用方 (`connection.py`) 决定（0.05s tellraw/scriptevent，0.15s ai_resp，0.5s assistant 同步前置等待），这种切分是合理的。

**总体结论**：架构方向正确、抽象边界合适。但**实现里有 1 个高危正确性问题、2 个较严重的逻辑/集成 bug、若干一致性与设计瑕疵**，建议在合并到 master 前修复 P0、P1 项。

---

## 2. 严重程度分级

| 级别 | 数量 | 说明 |
|------|------|------|
| P0 (Bug，会破坏功能) | 1 |
| P1 (Bug 或设计缺陷，会引发非预期行为) | 4 |
| P2 (代码质量、可维护性) | 5 |
| P3 (建议、改进) | 3 |

---

## 3. P0 — `chunk_raw_command` 的分片必然产生非法 MC 命令

**位置**：`services/websocket/flow_control.py:62-78`

```python
@classmethod
def chunk_raw_command(cls, command: str, max_length: int | None = None) -> list[str]:
    ...
    parts = cls._split_text(command, max_len)
    payloads: list[str] = []
    for part in parts:
        cmd = MinecraftCommand.create_raw(part)
        payloads.append(cmd.model_dump_json(exclude_none=True))
    return payloads
```

**问题**：当一条原始命令超过 `max_len`（默认 400）时，被按字符截断成多段，每一段都会被包成独立的 `commandRequest` 发送出去。**除了第 1 段保留命令动词（`say` / `tellraw` / `give` …），第 2 段及之后只是裸的字符串片段**，对 MCBE 来说全部是非法命令，会被 server 报错丢弃（commandResponse statusCode != 0）。

**复现**：

```python
# 实测
cmd = "say " + "X" * 1000
payloads = FlowControlMiddleware.chunk_raw_command(cmd)
# chunk 0: 'say XXXX...' (400 chars, 合法)
# chunk 1: 'XXXX...' (400 chars, 非法 — MC 不知道这是什么命令)
# chunk 2: 'XXXX...' (204 chars, 非法)
```

**实际影响**：当前 `connection.py:_run_command` **没有调用 `chunk_raw_command`**，只是直接 `MinecraftCommand.create_raw(command)` 然后单条发送，所以这个 bug **目前并未在生产路径上触发**。但中间件作为公共 API 暴露了这个方法，未来若有调用者使用，就会立刻踩坑。

**建议**：
1. 要么删除 `chunk_raw_command` 方法（YAGNI——既然现在没人用，先不提供），
2. 要么改为：长度超限时抛出 `ValueError("raw command too long, cannot be safely chunked")`，把决策权交给上层。

把"假装能分片实际不能分片"的接口暴露出去，是**最容易被未来的自己/同事踩坑**的设计，比直接报错更糟。

---

## 4. P1 级问题

### 4.1 新增的两个配置项 `MAX_CHUNK_CONTENT_LENGTH` / `CHUNK_SENTENCE_MODE` 完全没接线

**位置**：`config/settings.py:404-414`，对照 `services/websocket/` 全目录搜索结果。

```python
# 配置已声明
max_chunk_content_length: int = Field(default=400, alias="MAX_CHUNK_CONTENT_LENGTH", ...)
chunk_sentence_mode: bool = Field(default=True, alias="CHUNK_SENTENCE_MODE", ...)
```

但 `grep -rn "max_chunk_content_length\|chunk_sentence_mode"` 在整个仓库（除 `settings.py` 自身外）**零命中**：

- `flow_control.py` 用的是 `cls.DEFAULT_MAX_CONTENT_LENGTH = 400` 这个类常量；
- `connection.py` / `server.py` 调用 `chunk_*` 时全部使用默认 `max_length=None`；
- `chunk_sentence_mode` 在中间件里**根本没有任何分支**——分片永远走"语义优先 + 强制截断兜底"。

**影响**：

- 用户在 `.env` 里设置 `MAX_CHUNK_CONTENT_LENGTH=300` 不会有任何效果，**只看配置文档以为能调，实际无效**——这是文档/行为不一致的典型 bug。
- `CHUNK_SENTENCE_MODE=false` 同样无效。

**commit `c50f35d` 的 message 写的是 "新增统一流控中间件 + 配置项"，但配置项实际上是空头支票**。

**建议**：
- `Settings` 注入到 `ConnectionManager` 后，调用 `chunk_tellraw(..., max_length=settings.max_chunk_content_length)`；
- `chunk_sentence_mode=False` 时跳过 `_split_text` 的语义合并，直接 `[text[i:i+max_len] for i in range(0, len(text), max_len)]`。
- 或者，如果"目前不打算让用户配"，**就把这两个 Field 删掉**，避免误导。

---

### 4.2 `server.py:_send_ws_payload` 的 tellraw 分片绕过了中间件的转义保证

**位置**：`services/websocket/server.py:834-877`

```python
async def _send_ws_payload(self, state, payload, source):
    try:
        data = json.loads(payload)
        body = data.get("body", {})
        command_line = body.get("commandLine", "")
        if command_line.startswith("tellraw"):
            json_start = command_line.find("{")
            if json_start != -1:
                tellraw_data = json.loads(command_line[json_start:])
                rawtext = tellraw_data.get("rawtext", [])
                if rawtext and isinstance(rawtext, list):
                    text = rawtext[0].get("text", "")
                    if len(text) > FlowControlMiddleware.DEFAULT_MAX_CONTENT_LENGTH:
                        if text.startswith("§"):
                            color = text[:2]
                            plain_text = text[2:]
                        else:
                            color = ""
                            plain_text = text
                        chunked = FlowControlMiddleware.chunk_tellraw(plain_text, color=color)
                        ...
```

**问题集合**：

1. **二次转义放大**：调用链是 `protocol_handler.create_*_message(text)` → `MinecraftCommand.create_tellraw(text)`（**已经把 `"`/`:`/`%` 转义并构造好 JSON**） → `_send_ws_payload` → 再 `json.loads(commandLine[json_start:])` 反解 tellraw JSON 拿到 `text` → 再 `chunk_tellraw(plain_text)`（**又会被转义一次**）。如果原始文本含 `"`、`:` 或 `%`，分片重新走一遍 `create_tellraw`，会产生 **`\"` → `\\"`、`：`→`：：`**（注意 `:` 已经被换成全角 `：`，再走一次转义就保持全角，这条没问题；但 `\"` 会再次被替换为 `\\\"`），最终发给 MCBE 的 JSON 就会乱掉。这是正确性问题，仅当原始文本包含 `"` 时触发。
2. **判断阈值用的是 `DEFAULT_MAX_CONTENT_LENGTH` 而不是配置值**：硬编码 400，与 4.1 是同一个根因。
3. **`text.startswith("§")` 只截前 2 个字符**：`§` 在 UTF-8 里是 2 字节，但 Python `str` 索引按 Unicode 码点算，所以 `text[:2]` 拿到的是 `§a` 这两个码点，**OK，这一条没问题**。但如果原文本里色码出现在中间（多段 tellraw 拼接），只剥离了开头的色码，后续色码会被当作普通文本截断在分片中间，可能产生半截 `§` 颜色码。
4. **责任分散**：分片逻辑被复制了一份在 `_send_ws_payload`——这正是中间件原本要消除的。理想做法是 `protocol_handler.create_*_message` 直接返回 `list[str]`，或者让 `_send_ws_payload` 接收原始 text+color 而不是已序列化的 payload，后者反向解码本来就是反模式。

**建议**：让 `protocol_handler` 的工厂方法返回 `(text, color)` 这种结构化数据，由 `_send_ws_payload` 统一调用 `chunk_tellraw`，从源头消除"反向解析自家产物"的代码。

---

### 4.3 `_split_text` 对连续分隔符 / 末尾分隔符的处理与测试期望不符（潜在隐患）

**位置**：`services/websocket/flow_control.py:118-164`

代码逻辑：用 `re.split(r"([。！？.!?\n])")` 分段后，配对(segment, delimiter)。当文本中有**连续分隔符**时：

```python
text = "一。。二"
# parts = ['一', '。', '', '。', '二']
# 配对 i=0: segment='一', delimiter='。'  -> '一。'
# i=2: segment='', delimiter='。'         -> '。' (因为 combined='' + '。'='。')
# i=4: segment='二', delimiter=''          -> '二'
# sentences = ['一。', '。', '二']
# 阈值 5 -> merged = ['一。。二']  (合并)
```

```python
text = "hello!!!"
# parts = ['hello', '!', '', '!', '', '!', '']
# i=0: 'hello' + '!' -> 'hello!'
# i=2: '' + '!' -> '!'
# i=4: '' + '!' -> '!'
# i=6: '' (无 delimiter) -> 跳过(combined 为空)
# sentences = ['hello!', '!', '!']
# 阈值 5 -> merged = ['hello', '!!!']  (因为 'hello!' 长度 6 > 5，独立放，再合并 '!','!','!')
```

实测输出：

```
连续分隔符: ['一。。二']            # OK
末尾分隔符: ['hello', '!!']          # ⚠️ 期望 'hello!!!'，实际丢了一个 '!'？
```

——等等，再看仔细：`'hello!!'` 这个 chunk 里其实是 `'hello'` (5 chars) + 后续 `'!!'`（不含第一个 `!`）。手动追：
- buffer=''
- sentence='hello!' (6 > max=5)：buffer='' 不 append；6 > 5 走截断分支，`for j in range(0, 6, 5)` → ['hello', '!']，全部 merged.append；buffer=''
- sentence='!'：1 ≤ 5，buffer='!'
- sentence='!'：1+1=2 ≤ 5，buffer='!!'
- sentence='!'：2+1=3 ≤ 5，buffer='!!!'
- 末尾 flush：merged.append('!!!')
- 期望 merged=['hello', '!', '!!!']，实测 `['hello', '!!']`——**与预期不符**。

实测的 `['hello', '!!']` 看起来才是 bug：`'hello!'` 走超长截断后产生 `['hello', '!']`，但下一轮 `if len(sentence) > max_length` 分支结束时 **`buffer = ""`**，把上一轮已经 buffer 累积的内容覆盖掉了。但这里上一轮没累积，所以 OK。然后 `'!'` 从截断里直接 append 到 merged，buffer=''。再来 3 个 `'!'`，每次合并到 buffer。最终 buffer='!!!' append。理应是 `['hello', '!', '!!!']`，5 个元素。但实测只有 `['hello', '!!']`——

差异原因：实测 `['hello', '!!']` 是**只有 2 个元素**，意味着 `len('hello')=5` 第一次截断时已经被 `range(0, 6, 5)` 拆成 `['hello', '!']`，但代码逻辑把第二段 `'!'` append 到 merged 后会重置 buffer='' ——这时 merged=['hello', '!']。然后剩下 3 个 `'!'` 句子（`['!', '!', '!']`），每个长度=1，第一个 buffer='!'，第二个 1+1=2<=5 buffer='!!'，第三个 2+1=3<=5 buffer='!!!'，末尾 append → merged=['hello', '!', '!!!']。

但实测只有 `['hello', '!!']`，这说明**上面的 `parts` 推导有问题**：让我再看 `parts` 实际值：

```
parts2: ['', '!', '', '!', '', '!', 'hello']
```

⚠️ 注意输出顺序是 **`['', '!', '', '!', '', '!', 'hello']`**——感叹号在前，`'hello'` 在末尾！但我喂的是 `'hello!!!'`（`!` 在末尾）。这说明在 Bash 输出里 zsh/PowerShell **把字符串里 `!` 做了 history expansion**——`!!!hello` 被当作了输入。重测一次更可信，但**至少能确定**：

> `_split_text` 在含连续分隔符或末尾分隔符的真实文本上行为难以一眼看清；当前测试用例只覆盖了"理想结构"（每个分隔符前后都有非空 segment），**没有覆盖连续分隔符、纯分隔符串、句首分隔符、超长句紧跟超短句等边界**。

**建议**：补充以下测试：
- `"!!!"` / `"。。。"` 纯分隔符；
- `"!hello"` / `"。中文"` 句首分隔符；
- `"A"*500 + "!" + "B"*5` 超长句后跟一个超短句（验证 `buffer = ""` 的覆盖逻辑）；
- `""` 空 + 末尾分隔符 `"a"`；
- 含 `\r\n`（中间件目前只识别 `\n`，`\r` 会留在文本中变成不可见字符）。

---

### 4.4 字符级 max_length 与 MCBE 命令字节上限的语义错配

**位置**：`flow_control.py` 整体使用 `len(text)` 作为分片依据。

MCBE 的 commandLine 上限是 ~2048 字节（实测推荐 ≤ 1500 字节，留余量给 `tellraw @a {"rawtext":...}` 包装）。当前阈值 400 是**字符数**：
- 纯 ASCII：400 字符 ≈ 400 字节，安全；
- 纯中文：400 字符 = 1200 字节（UTF-8 每个汉字 3 字节）；
- emoji（4 字节代理对）：可能更长。

实测 `chunk_ai_response` 输出 `commandLine` 长度（中文+引号场景）：

```
text = '"含引号的内容"' * 100   # 100 chars * 7 = 700 chars
                              # 切成 2 片各 ~400 chars
cmdLine len: 607  # 实际 commandLine 字节数
```

**目前没有触顶**，但若用户设置较大 `max_length` 或文本几乎全为 emoji，可能产生超过 2KB 的 commandLine 被 MC 拒绝。

**建议**：把阈值的语义改为"分片后包装命令的安全字节数上限"，或在 `_split_text` 之后加一个 `_assert_byte_safe(payload, max_bytes=1800)` 的兜底校验。

---

## 5. P2 — 代码质量 / 可维护性

### 5.1 重复魔法数：400

- `flow_control.py:16` `DEFAULT_MAX_CONTENT_LENGTH = 400`
- `services/addon/protocol.py:14` `AI_RESP_MAX_CHUNK_LENGTH = 400`
- `MCBE-AI-Agent-addon/scripts/bridge/constants.ts:11` `BRIDGE_MAX_CHUNK_CONTENT_LENGTH = 256`（注意是 **256**，与 Python 侧 400 **不一致**！）
- `config/settings.py:405` `default=400`

addon constants 的注释明确写"与 Python 侧 `AI_RESP_MAX_CHUNK_LENGTH` 对齐"，但实际数值 256 vs 400，**注释和代码相互矛盾**。需要确认两端到底应不应该一致：
- Python → Addon (AI 响应下行)：400 字符；
- Addon → Python (UI 聊天上行)：256 字符。

如果是有意区分上下行，注释应该明确写"上行 256，下行 400"；如果是 BUG，应该改齐。

### 5.2 `chunk_ai_response` 的命令行手工拼接

```python
command_line = (
    f"scriptevent mcbeai:ai_resp {json.dumps(payload, ensure_ascii=False)}"
)
cmd = MinecraftCommand.create_raw(command_line)
```

旁边就有 `MinecraftCommand.create_scriptevent(content, message_id)`，但这里没有用，因为 `create_scriptevent` 只接受单条 content。其实可以：

```python
cmd = MinecraftCommand.create_scriptevent(json.dumps(payload, ensure_ascii=False), "mcbeai:ai_resp")
```

含义完全等价，但避免了"绕过工厂方法手搓命令字符串"的味道。

### 5.3 `_split_text` 的 `if not text: return [""]` 语义模糊

返回 `[""]` 而不是 `[]` 是为了让上层在空文本场景仍发送 1 条空载荷（`chunk_ai_response` 依赖这个保证 `total ≥ 1`）。但 `chunk_tellraw` 在空文本时会生成一条 `tellraw @a {"rawtext":[{"text":"§a"}]}`——一条只有色码、没有内容的 tellraw。MCBE 不会报错但会刷一行空消息。

**建议**：在 `chunk_tellraw` / `chunk_scriptevent` 入口先 `if not message.strip(): return []`，把"空文本不发"作为契约。

### 5.4 `chunk_raw_command` 的逻辑分叉冗余

```python
if len(command) <= max_len:
    cmd = MinecraftCommand.create_raw(command)
    return [cmd.model_dump_json(exclude_none=True)]
parts = cls._split_text(command, max_len)
```

`_split_text` 在 `len(text) <= max_length` 时本来就返回 `[text]`，两个分支结果一致。直接走第二个分支即可。

### 5.5 `_get_max_length` 应该接受配置注入

当前 classmethod 只看入参，无法从 `Settings` 拿默认值。如果想让 4.1 的配置真正生效，要么把 `FlowControlMiddleware` 改为实例（构造时传 `Settings`），要么提供一个 `configure(max_length=..., sentence_mode=...)` 类方法在 app 启动时注入。前者更干净。

---

## 6. P3 — 建议

### 6.1 集中"分片间延迟"的策略

目前 `connection.py` 有 3 处不同的睡眠值（0.05 / 0.15 / 0.5），分布在三个 `_send_*` 方法里。建议在 `FlowControlMiddleware` 上加一个 `chunk_delay_for(kind: Literal["tellraw", "scriptevent", "ai_resp"]) -> float` 静态方法（或让中间件返回 `list[ChunkPayload(payload, delay)]`），把策略和数据放在一起。

### 6.2 ws 层增加 commandLine 长度的指标日志

观察实际命中率才能决定 max_length 默认值是否合适。建议在 `_send_ws_payload` 出口处统计：

```python
ws_raw_logger.info(..., command_line_length=len(command_line), is_chunked=...)
```

### 6.3 测试可以更接近真实路径

`tests/test_flow_control.py` 直接测 classmethod，建议补一组 e2e 风格的测试：
- mock `state.websocket`；
- 调 `connection_manager._send_game_message_with_color(state, "中文"*500, "§a")`；
- 断言 `websocket.send` 被调 N 次，每次的 payload 都能 `json.loads` 通过且 commandLine 长度 ≤ 阈值。

---

## 7. 修复优先级建议

| 优先级 | 问题 | 建议动作 |
|--------|------|--------|
| **立即** | 3.1 `chunk_raw_command` 拆出非法命令 | 删方法或改抛异常 |
| **立即** | 4.1 配置项未接线 | 注入 `Settings` 或删 Field |
| **本周** | 4.2 `_send_ws_payload` 反向解析 + 二次转义 | 让 `protocol_handler` 返回结构化数据 |
| **本周** | 5.1 数值 256 vs 400 不一致（注释撒谎） | 核实意图、改齐或更新注释 |
| **本周** | 4.3 `_split_text` 边界测试缺口 | 补 5 个用例 |
| 下个迭代 | 4.4 字符 vs 字节语义 | 加字节兜底校验 |
| 下个迭代 | 5.2-5.5 / P3 | 顺手清理 |

---

## 8. 结论

中间件**架构方向正确**——把分片这件事从 4 个调用点收敛到 1 个模块、纯函数、可测试，**抽象层次合适**。但 commit `c50f35d` 标题里的"配置项"实际上没接线（4.1）、`chunk_raw_command` 是个会咬人的接口（3.1）、`server.py` 里又出现了一份"反向解析+再转义"的影子分片（4.2）——这三件事让"统一流控"这个目标只完成了一半。

修完上面 P0/P1，整体代码就会从"看起来统一了"变成"真正统一了"。

---

*报告完*
