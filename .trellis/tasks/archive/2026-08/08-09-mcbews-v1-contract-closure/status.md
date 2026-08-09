# 当前状态 — 2026-08-09 Phase 5 完成

## 已提交

- SDK 独立仓库 `mcbe-ws-sdk`：`6b88ed3 feat(mcbews-v1): 收敛协议权威契约`
- SDK Phase 5 格式收尾：`4cecc99 docs(changelog): 清理 0.2.0 发布说明格式`
- 根仓库 Host：`29077f2 fix(gateway): 闭合 MCBEWS 身份与会话契约`
- 根仓库产品 Addon：`14a1873 fix(addon): 强化 MCBEWS framing 与路由隔离`
- 根仓库发布/文档/CI：`384db5d docs(protocol): 同步 0.2.0 发布与协议契约`
- 根仓库 Phase 5 回归修复：`cd950e5 fix(addon): 接受正常顺序的完成帧 usage`
- 根仓库协议别名收尾：`97cc1f3 refactor(protocol): 清理模拟器弃用字段`

以上提交将分别通过 SDK 与 Root PR 合入各自 integration branch；本任务不打 tag 或发布。

- 根仓库当前分支：`fix/mcbews-v1-contract-closure`，upstream 为同名远端分支。
- SDK 提交仍位于既有 `feature/session-text-resp-fields`，upstream 为同名远端分支；创建 SDK PR 前仍需确认真实 integration target，并决定是否迁移到 `fix/mcbews-v1-contract-closure`。

## 已完成范围

- Phase 1：SDK canonical manifest/vectors、typed codec/control、reference Addon、0.2.0 package 与 clean-wheel gate。
- Phase 2：Host typed ingress/outbound、CID/玩家身份、approval owner/lifecycle、ConversationOperations 与 atomic session delivery。
- Phase 3：产品 Addon 协议资产同步、Unicode/byte-aware framing、有界重组、session/router/registry 与玩家/CID 隔离。
- Phase 4：Host SDK pin、wheel-installed CI contract、权威文档、Trellis spec、release order 与真实 MCBE smoke checklist。
- Phase 5：generated asset/legacy/magic-dict 扫描、UI Chat/text response/session/approval 跨层 trace、全量 integration gates 与最终 Trellis check/spec capture。

Phase 5 发现并修复一个产品 Addon 回归：normal-order 多帧响应在完成帧首次携带 usage 时，
`BoundedTextResponseAssembler` 会把它误判为 metadata conflict；现已同时覆盖 normal-order 与
final-first 两种完成帧顺序，并把约束写入 Addon spec。

## 验证记录

- SDK：全量 Python `232 passed`；Addon reference `104 passed`；ruff、mypy、lint、typecheck、build、twine、check_dist 与协议名称检查全通过。
- Host：根全量 pytest `787 passed, 2 deselected`；Phase 5 定向 Host/模拟器回归 `19 passed`，最终模拟器回归 `9 passed`。
- 产品 Addon：`pnpm test` 21 files / `140 passed`；`pnpm protocol:check`、`pnpm lint`、`pnpm build` 全通过。
- Wheel contract：本地构建 SDK `0.2.0` sdist/wheel，twine/check_dist 通过；临时隔离 venv 从 wheel 导入 `site-packages`，Host contract `1 passed`。
- diff-check：根仓库与 SDK 均通过；Phase 5 变更文件 lint 通过。
- 根全局静态基线仍未全绿：`ruff check .` 有 321 项既有仓库/Trellis/tests 债务；使用
  `--explicit-package-bases` 的 mypy source gate 有 240 项既有债务。`services/agent/worker.py`
  当前 15 项 Ruff 结果与基线提交 `4927482` 逐项一致，本任务未新增这些问题。

## 外部门禁 / 未执行

- 真实 Minecraft `/wsserver` smoke 尚未执行。
- SDK `v0.2.0` 尚未发布到 PyPI/GitHub；Host pin 在纯 PyPI 环境中需等 SDK 先发布。
- SDK PR 的 integration target / 分支迁移仍需发布者确认。
- 不在本任务中清理根仓库全局 Ruff/mypy 基线债务。

源码任务范围与 Phase 5 已完成；Trellis 任务在创建 PR 前归档。真实 MCBE smoke、tag 和发布仍
由外部发布流程执行。
