# UI 表单历史与连接状态修复

## 修复内容

- 调整聊天记录面板展示逻辑，历史记录正文改为完整内容展示，不再复用预览长度生成 `...` 截断。
- 新增历史记录完整格式化方法，保留角色与来源标识，避免影响其他仍需预览的区域。
- 修复 UI 状态持久化，保存并恢复 `bridgeStatus`，避免重新打开面板时状态回退到 `disconnected`。
- 响应同步收到 assistant 分片并重组完成后，将内存态与持久态的桥接状态更新为 `ready`。

## 涉及文件

- `MCBE-AI-Agent-addon/scripts/ui/history.ts`
- `MCBE-AI-Agent-addon/scripts/ui/panels/historyPanel.ts`
- `MCBE-AI-Agent-addon/scripts/ui/storage.ts`
- `MCBE-AI-Agent-addon/scripts/bridge/responseSync.ts`

## 验证

- 已运行 `npm --prefix "MCBE-AI-Agent-addon" run build`，构建通过。
