"""Dedicated block inspection / mutation tools for the MCBE Chat Agent."""

from services.agent.block_ops.bridge import (
    call_block_capability,
    map_addon_bridge_result,
    map_bridge_exception,
)
from services.agent.block_ops.project import project_block_result_for_model
from services.agent.block_ops.capability import (
    BlockCapabilityCache,
    BlockCapabilityRecord,
    BlockCapabilityStatus,
    clear_block_capability,
    ensure_block_capability,
    get_block_capability_cache,
    reset_block_capability_cache,
)
from services.agent.block_ops.config import (
    BlockToolsLimits,
    DEFAULT_COMMAND_LINE_BYTE_BUDGET,
    get_block_tools_limits,
    get_command_line_byte_budget,
)
from services.agent.block_ops.preflight_cache import (
    PreflightCache,
    clear_preflight_for_connection,
    get_preflight_cache,
    reset_preflight_cache,
)
from services.agent.block_ops.schema import (
    BLOCK_OPS_SCHEMA_VERSION,
    BlockErrorCode,
    build_error_response,
    build_internal_error_response,
    build_state_unknown_response,
    build_success_response,
    dumps_error,
    dumps_payload,
    dumps_success,
)
from services.agent.block_ops.tools_impl import (
    BLOCK_TOOL_NAMES,
    BlockPreflightPlan,
    build_block_preflight_plan,
    check_bridge_command_line_budget,
    edit_blocks_impl,
    estimate_bridge_command_line_bytes,
    inspect_block_impl,
    merge_canonical_from_preflight,
    project_block_execute_args,
    run_block_preflight,
)

__all__ = [
    "BLOCK_OPS_SCHEMA_VERSION",
    "BLOCK_TOOL_NAMES",
    "BlockPreflightPlan",
    "BlockCapabilityCache",
    "BlockCapabilityRecord",
    "BlockCapabilityStatus",
    "BlockErrorCode",
    "BlockToolsLimits",
    "DEFAULT_COMMAND_LINE_BYTE_BUDGET",
    "PreflightCache",
    "build_error_response",
    "build_internal_error_response",
    "build_block_preflight_plan",
    "build_state_unknown_response",
    "build_success_response",
    "call_block_capability",
    "check_bridge_command_line_budget",
    "clear_block_capability",
    "clear_preflight_for_connection",
    "dumps_error",
    "dumps_payload",
    "dumps_success",
    "edit_blocks_impl",
    "ensure_block_capability",
    "estimate_bridge_command_line_bytes",
    "get_block_capability_cache",
    "get_block_tools_limits",
    "get_command_line_byte_budget",
    "get_preflight_cache",
    "inspect_block_impl",
    "map_addon_bridge_result",
    "map_bridge_exception",
    "merge_canonical_from_preflight",
    "project_block_execute_args",
    "project_block_result_for_model",
    "reset_block_capability_cache",
    "reset_preflight_cache",
    "run_block_preflight",
]
