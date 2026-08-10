# 修复 UI 会话切换帧解析 — 执行计划

## 1. 建立分支与红灯

- Host：从最新 `origin/dev` 创建 `fix/ui-session-switch-default`。
- SDK：从最新 `origin/main` 创建 `fix/session-switch-default`。
- SDK 新增 `switch/default` ingress 回归测试并确认修复前失败。
- Host 新增 `ConversationOperations` 回归测试并确认修复前失败。

## 2. 修复 SDK 契约

- 修改 `SessionRequest` validator，以 `model_fields_set` 区分省略和显式默认值。
- 保留缺失/空白 cid 的负例。
- 新增 `session-switch-default` 权威 vector，运行协议资产生成器并检查生成结果。
- 准备 `0.2.1` patch 元数据与 CHANGELOG，不执行发布。

## 3. 修复 Host 与 Addon 门禁

- `ConversationOperations._switch()` 仅拒绝空目标，允许 `default`。
- 补 Host active conversation 与玩家隔离断言。
- 补 Addon `requestSession("switch", cid="default")` 单帧/关联测试。
- 从 SDK 资源同步产品 Addon 协议资产。
- Host 最低 SDK 版本、wheel contract 版本/ref 与相关发布说明更新到 `0.2.1`。

## 4. 验证

SDK：

```bash
python tools/generate_protocol_assets.py --check
pytest -p no:cacheprovider -q
ruff check --no-cache src tests examples
mypy --no-incremental src
python -m build --wheel
```

Host / Addon：

```bash
pytest -q tests/test_gateway_phase2_contract.py tests/test_sdk_dependency.py
cd MCBE-AI-Agent-addon
pnpm protocol:check
pnpm test
pnpm lint
pnpm build
```

用 SDK 新构建的 `0.2.1` wheel 创建隔离 venv，运行 `tools/check_sdk_wheel.py` 与
`tests/test_sdk_dependency.py`，确认没有被 nested editable checkout 掩盖。

最后重跑用户日志帧的原始 `AddonBridgeService` harness，应从 RED 变为 GREEN。

## 5. 收尾

- 运行 `trellis-check`，检查协议资产、版本引用、双玩家身份与两个仓库状态。
- 用 `trellis-update-spec` 记录“合法默认 ID 不等于字段缺失”的可执行契约。
- SDK 与 Host 分别提交 Conventional Commit；不 push、不建 PR、不 tag、不发布。
