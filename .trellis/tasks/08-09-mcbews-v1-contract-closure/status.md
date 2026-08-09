# 当前状态 — 2026-08-09

## 已提交

- SDK 独立仓库 `mcbe-ws-sdk`：`6b88ed3 feat(mcbews-v1): 收敛协议权威契约`
- 根仓库 Host：`29077f2 fix(gateway): 闭合 MCBEWS 身份与会话契约`
- 根仓库产品 Addon：`14a1873 fix(addon): 强化 MCBEWS framing 与路由隔离`
- 根仓库发布/文档/CI：`384db5d docs(protocol): 同步 0.2.0 发布与协议契约`

以上提交均只在本地分支，尚未 push、开 PR、打 tag 或发布。

- 根仓库当前分支：`fix/mcbews-v1-contract-closure`，相对远端 ahead 4。
- SDK 提交当前位于既有 `feature/session-text-resp-fields`，相对远端 ahead 1；创建 SDK PR 前仍需按计划确认其真实 integration target，并决定是否把该提交迁移到 `fix/mcbews-v1-contract-closure`。

## 已完成范围

- Phase 1：SDK canonical manifest/vectors、typed codec/control、reference Addon、0.2.0 package 与 clean-wheel gate。
- Phase 2：Host typed ingress/outbound、CID/玩家身份、approval owner/lifecycle、ConversationOperations 与 atomic session delivery。
- Phase 3：产品 Addon 协议资产同步、Unicode/byte-aware framing、有界重组、session/router/registry 与玩家/CID 隔离。
- Phase 4：Host SDK pin、wheel-installed CI contract、权威文档、Trellis spec、release order 与真实 MCBE smoke checklist。

## 验证记录

- SDK：全量 Python `232 passed`；Addon reference `104 passed`；ruff、mypy、lint、typecheck、format、build、twine、check_dist、隔离 wheel contract 全通过。
- Host：Phase 2 focused gate `58 passed`；新增 Phase 2 contract `10 passed`；受影响文件 ruff、compileall、diff-check 通过。
- 根全量 pytest 曾在旧 `origin.type="say"` 断言处得到 `786 passed, 1 failed, 2 deselected`；该断言已按 SDK 0.2 明确契约修正为 `player`，focused gate随后全绿，但尚未重新跑根全量 pytest。
- 产品 Addon：`pnpm test` 21 files / `139 passed`；`pnpm lint`、`pnpm build`、strict protocol asset check 全通过。
- 根 wheel contract：依赖 contract `1 passed`；本地 SDK 0.2.0 wheel、twine、隔离 import contract、JSON/YAML/docs consistency 检查通过。

## 暂停点 / 未完成

- 按用户要求，本轮交付后暂不继续 review；初次 SDK review 的发现已修复，但未做复审，Phase 2-4 未做独立 review。
- `implement.md` Phase 5 全量 integration review 尚未执行；根全量 pytest、跨层 trace 与 legacy/magic-dict 搜索待下一轮继续。
- 真实 Minecraft `/wsserver` smoke 尚未执行。
- SDK `v0.2.0` 尚未发布到 PyPI/GitHub；Host pin 在纯 PyPI 环境中需等 SDK 先发布。
- 根仓库既有 `services/agent/worker.py` ruff 与全项目 mypy 基线债务未在本任务中清理。

任务保持 `in_progress`，未归档。恢复时从 Phase 5 和上述外部门禁继续。
