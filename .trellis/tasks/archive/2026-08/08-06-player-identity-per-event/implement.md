# 方案二：让玩家身份始终来自当前事件 — 执行计划

## 前置

- 本任务为整个架构优化的**安全门**，应最先落地（报告第十一节第一步）。
- 工作分支：`test/player-identity-interleave`（先落安全门测试）→ 合并后再开 `refactor/player-identity-per-event`。

## 实施顺序

### 阶段 A：安全门测试（独立可先行提交）

1. 读 `services/gateway/command_handlers.py` 与 `hook.py` 当前实现，确认 `state._player_name` 读写点及回退分支。
2. 在既有玩家隔离测试文件新增双玩家交错测试，用可控调度顺序构造"A 写入后等待 → B 覆盖 → A 走回退分支"场景。
3. 断言覆盖：请求 `ChatRequest.player_name`、回复目标、界面同步目标、审批归属不串扰。
4. 运行该测试，确认当前代码能**触发**风险（或至少测试可稳定复现交错场景），作为重构前的行为基线。

### 阶段 B：重构（安全门通过后）

5. 逐一处理 `state._player_name` 回退分支：显式传入 `player_name`；缺失时明确失败或用非业务显示默认值。
6. 回复目标、`ChatRequest.sender`、界面同步目标、审批归属改为显式身份来源。
7. 切断 `state._player_name` 对业务身份的兜底作用（删除或明确其非业务属性）。
8. 运行安全门测试 + 既有玩家隔离测试，确认全部通过。

## 验证命令

```bash
pytest -q tests/<player_identity_or_gateway_tests>   # 安全门 + 玩家隔离测试
pytest -q   # 全量，确认无新增失败（独立 CLI bug 除外）
```

## 评审门

- 代码评审：确认所有业务身份不再读取 `state._player_name`；`sender` / 显式 `player_name` 为唯一来源。
- 通过 `trellis-check` 复核，再合入 `dev`。

## 回滚点

- 安全门测试是独立提交，始终保留为回归防护。
- 若重构触发回滚门（业务身份仍可能落到其他桶 / 测试无法可靠复现交错），回退重构提交，回到仅含安全门测试的状态。

## 完成定义

- 双玩家交错测试通过且作为回归防护保留。
- 所有回退分支不再读取 `state._player_name` 作为业务身份来源。
- 对话/模板/变量/模型/上下文按玩家隔离不变。
- 全量 pytest 无新增失败。