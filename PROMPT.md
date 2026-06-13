# 任务：完成架构技术债重构

## 上下文
- 项目：MCBE AI Agent。
- 架构约束：遵守 `CLAUDE.md`，尤其是多人会话隔离和 FlowControlMiddleware 出站流控约束。
- 技术债报告：`docs/superpowers/reports/2026-06-13-architecture-tech-debt-scan.md`。

## 目标
按报告完成所有重构指标，优先级如下：
1. 运行时 conversation session Module 加深。
2. MCBE outbound delivery Adapter 集中。
3. 运行时 Harness tool Module 加深。
4. WebSocketServer command Module 加深。
5. 运行时 Adapter lifecycle Module 显式化。
6. Settings Interface 收窄。

## 要求
- 可使用子代理做只读调研、审查或测试验证；代码修改和 commit 由主会话完成。
- 小步重构，保持外部行为不变。
- 每次只处理一个内聚切片，避免大爆炸式改动。
- 做通用修复，不做临时补丁、兼容壳或无意义抽象。
- 不破坏 `(connection_id, player_name)` 多人会话隔离。
- 所有下行长文本路径必须复用统一流控，不重复实现分片。
- 补充或更新必要测试。
- 每完成一个可验证切片后创建一个描述性 git commit。
- 只提交本地 commit，不要 push 到远程。

## 验证
- 每个切片完成后运行相关测试。
- 最终运行完整可用测试套件。
- 如果 `pytest` 不可用，说明原因，并至少运行可用的静态检查或 Python 编译检查。
- 修复所有由本次重构引入的失败。

## 成功标准
- 报告中的 6 个候选重构指标均已处理或明确证明无需改动。
- 测试通过，或有清晰的环境阻塞说明。
- git history 中每个主要重构切片都有独立描述性 commit。
- 没有推送远程。
- 完成后输出 `<promise>DONE</promise>`。
