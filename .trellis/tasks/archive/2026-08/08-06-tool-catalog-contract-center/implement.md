# 方案五：让工具目录成为真实契约中心 — 执行计划

## 前置

- 独立于其余候选，可随时并行推进（报告五→无依赖）。
- 工作分支：`refactor/tool-catalog-contract-center`。

## 实施顺序

1. **读当前代码**：核对 `catalog.py` `_TOOL_CATALOG`、`prompting.py`、`prompt.py`、`core.py`、`tools.py` 与 `tests/test_runtime_harness_catalog.py`（报告行号已偏移）。
2. **盘点重复**：记录 `TOOL_USAGE_GUIDE` 分散点、方块工具优先级规则位置、手工 `REGISTERED_AGENT_TOOL_NAMES`。
3. **建立核对测试**：新增"每个内置工具恰有一条目录条目""目录无未注册工具""参数约束引用字段真实存在""提示/风险/审计预览来自同一条目"断言。
4. **统一投影源**：合并 `prompt.py` 与 `core.py` 的 `TOOL_USAGE_GUIDE` 为单一投影；对齐 `prompting.py` 方块工具优先级规则到目录条目。
5. **测试集自动投影**：`REGISTERED_AGENT_TOOL_NAMES` 改为从目录投影驱动，移除手工维护。
6. **确认 MCP 独立**：确认 MCP 工具保留独立来源标记与适配规则。
7. **质量检查**：跑 `tests/test_runtime_harness_catalog.py` 及工具相关测试，并跑全量 `pytest -q`。

## 验证命令

```bash
pytest -q tests/test_runtime_harness_catalog.py
pytest -q   # 全量，确认无新增失败
```

## 评审门

- 代码评审：确认目录为唯一契约入口；提示/风险/审计预览由同一条目投影；测试注册集合由目录投影驱动。
- 通过 `trellis-check` 复核，再合入 `dev`。

## 回滚点

- 改动集中在 `harness/catalog.py` 及投影消费方。若目录与实际注册结果不一致且无法自动核对，回退到合入前提交。

## 完成定义

- 每内置工具在目录中唯一；提示/风险/审计预览由同一条目投影。
- 目录与实际注册结果可通过测试自动核对。
- 全量 pytest 无新增失败。