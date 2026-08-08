# 切换会话后响应丢失与 byte budget 修复

## Goal

修复聊天手动切换会话后，AI 响应因 `mcbews:text_resp` 最后一帧超出 461 字节预算而
`FrameTooLargeError`，导致响应无法送达 DDUI 面板的问题。同时修正 addon 侧 token 统计全 0。

## Background

- 后端 `worker._execute_single_request` 把完整 PydanticAI usage dict（含
  `cache_write_tokens`/`cache_read_tokens`/`input_audio_tokens`/`details`/`requests`/
  `tool_calls` 等，约 286 字节）原样放入 `ai_response_sync` 的 `usage` 字段。
- `broker_bridge._ai_sync` → `McbewsV1Delivery.send_response(usage=usage)` →
  `codec.encode_text_response_commands` 把 `usage` 整个 dict 塞进最后一帧的 `u` 字段。
- 切换会话后 `conversation_id` 变长（如 `chat-20260808-172115-742928-d7208e`），
  最后一帧 = frame 头 + 长 cid + 实际文本 + 完整 usage dict，实测可达 492 字节 > 461 预算。
- addon 侧 `responseSync.handleChunk` 解析 `chunk.u.i`/`chunk.u.o`（compact），
  而 Python 传的是 `input_tokens`/`output_tokens`（verbose），字段名不匹配 → token 全 0。

## Requirements

- 后端发送给 addon的 `mcbews:text_resp` 帧的 `u` 字段必须是 compact 格式
  `{"i": <input_tokens>, "o": <output_tokens>}`，不得传完整 usage dict。
- 精简在 `broker_bridge._ai_sync`（出口处），不改动 `worker.py` 的 usage 结构
  （trace/日志仍需完整 usage）。
- 修复后：长 cid + 有 token 用量时，`mcbews:text_resp` 不再抛 `FrameTooLargeError`。
- addon 侧 token 统计正常显示（与后端 usage 的 input_tokens/output_tokens 一致）。

## Constraints

- 不改 SDK `codec.py` 的 `u` 字段编码逻辑（它已正确透传 dict）。
- 不改 `worker.py` 的 `usage_dict`（trace/audit 依赖完整 usage）。
- 精简逻辑要兼容 usage 为 None 或缺字段的场景（部分 provider 不返回 token 用量）。

## Acceptance Criteria

- [ ] 切换会话后发消息，AI 响应正常流式显示到面板，无 `broker_bridge_handle_error` /
      `FrameTooLargeError` 日志。
- [ ] 统计面板 token 数值非 0，与后端 usage 的 input_tokens/output_tokens 一致。
- [ ] usage 为 None 或无 input_tokens/output_tokens 字段时，`u` 字段为 None（不发送），
      不报错。
- [ ] `python -m pytest`（broker_bridge 相关）通过。

## Notes

- 精简后 `u` 字段从 ~286 字节降到 ~17 字节，彻底消除 byte budget 超限余量不足。
- addon 侧 `responseSync.ts` 已在本次会话早先提交中兼容 `u.i/u.o` 与
  `u.input_tokens/u.output_tokens`（commit f65ea8a）；精简为 compact 后两条路径都命中。
