# Task4c Report: AgentRuntime lifecycle + live pytest marker

**Status:** DONE  
**Base:** a21a0a0

## Summary

AgentRuntime now owns PromptManager and ConversationManager, aggregates independent shutdown steps (MCP / Agent / audit / model metadata / providers), and default pytest excludes live provider tests.

## Changes

### Ownership
- `AgentRuntime` caches `prompt_manager` and `conversation_manager`.
- `get_conversation_manager(broker, settings)` recreates when broker/settings identity changes.
- On `shutdown()`, clears prompt/conversation managers and broker/settings refs so restart cannot reuse stale instances.
- Module facades:
  - `services/agent/prompt.py::get_prompt_manager()` → runtime
  - `core/conversation.py::get_conversation_manager()` → runtime

### Shutdown aggregation
`AgentRuntime.shutdown()` independently attempts:
1. MCP shutdown
2. Agent reset/close
3. Audit writer stop/flush (Task4a path folded in)
4. Model metadata close/shutdown (if present)
5. Provider HTTP client close

Each step is wrapped so one failure is logged/aggregated and does not block the rest.

### Live marker
- `pyproject.toml` registers `live` marker and `addopts = "-m 'not live'"`.
- `tests/test_agent_multi_tool_live.py` marked `@pytest.mark.live` (module-level).
- Live assertions use current `tool_events` contract (`tool_name` / `tool_call_id` / `args`); reject legacy `tool_calls` / `tool_returns`.

## Tests

```bash
PYTHONPATH=. ./.venv/bin/python -m pytest \
  tests/test_agent_provider_lifecycle.py \
  tests/test_runtime_harness_audit.py \
  tests/test_conversation_manager.py \
  tests/test_prompt_template.py -q
# 99 passed

PYTHONPATH=. ./.venv/bin/python -m pytest --collect-only -q
# 498/500 tests collected (2 deselected)

PYTHONPATH=. ./.venv/bin/python -m pytest --collect-only -q -m live tests/test_agent_multi_tool_live.py
# 2 tests collected
```

Added coverage:
- `test_agent_runtime_owns_prompt_and_conversation_managers`
- `test_agent_runtime_shutdown_aggregates_errors_and_continues` (MCP failure still runs agent reset, audit stop, metadata close, provider close)
- `test_default_pytest_collection_excludes_live_marker`

## Files
- `services/agent/runtime.py`
- `services/agent/prompt.py`
- `core/conversation.py`
- `pyproject.toml`
- `tests/test_agent_provider_lifecycle.py`
- `tests/test_agent_multi_tool_live.py`

## Notes / non-goals
- Did not rework Task4a audit queue design or Task4b owner checks.
- Did not modify `mcbe-ws-sdk/`.
- Unrelated pre-existing env issue: `tests/test_connection_manager.py::test_run_command_should_drop_cancelled_future_from_pending_map` fails without `config.json` in this worktree; outside Task4c scope.
