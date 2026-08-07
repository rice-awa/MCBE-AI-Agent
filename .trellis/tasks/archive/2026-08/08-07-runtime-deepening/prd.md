# 继续加深 AgentRuntime 消除残余间接依赖

## Goal

盘点方案六后仍存在的运行时间接依赖，评估哪些可进一步加深到 `AgentRuntime`，减少测试中同时替换多个模块级获取函数的需求。

## 背景

方案六折叠了 `get_agent_manager`/`get_mcp_manager`/`get_prompt_manager`/`get_conversation_manager` 四个浅层转发函数，并移除了 `ProviderRegistry.set_runtime_adapters()`。但 `providers.py` 仍通过 `runtime_adapters` 间接访问运行时，部分测试仍在替换多个模块级获取函数。本任务评估这些残余依赖是否可以加深。

## 范围

- `services/agent/providers.py`（`runtime_adapters` 和 `get_agent_runtime()`）
- `services/agent/runtime.py`（`AgentRuntime` 当前出口）
- 相关测试文件

## Acceptance Criteria

- [ ] 盘点当前 `providers.py` 对 `runtime.py` / `AgentRuntime` 的间接依赖并列出
- [ ] 对每条间接依赖，判定是否可加深（或给出保留理由）
- [ ] 实际加深数 > 0（或给出不可行的明确结论）
- [ ] `pytest -q` 无新增失败