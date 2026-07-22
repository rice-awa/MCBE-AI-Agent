"""Dedicated block inspection / mutation tools for the MCBE Chat Agent."""

from services.agent.block_ops.bridge import (
    call_block_capability,
    map_addon_bridge_result,
    map_bridge_exception,
)
from services.agent.block_ops.capability import (
    BlockCapabilityCache,
    BlockCapabilityRecord,
    BlockCapabilityStatus,
    clear_block_capability,
    ensure_block_capability,
    get_block_capability_cache,
    reset_block_capability_cache,
)
from services.agent.block_ops.config import BlockToolsLimits, get_block_tools_limits
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
    build_success_response,
    dumps_error,
    dumps_payload,
    dumps_success,
)
from services.agent.block_ops.tools_impl import (
    BLOCK_TOOL_NAMES,
    BlockPreflightPlan,
    build_block_preflight_plan,
    edit_blocks_impl,
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
    "PreflightCache",
    "build_error_response",
    "build_block_preflight_plan",
    "build_success_response",
    "call_block_capability",
    "clear_block_capability",
    "clear_preflight_for_connection",
    "dumps_error",
    "dumps_payload",
    "dumps_success",
    "edit_blocks_impl",
    "ensure_block_capability",
    "get_block_capability_cache",
    "get_block_tools_limits",
    "get_preflight_cache",
    "inspect_block_impl",
    "map_addon_bridge_result",
    "map_bridge_exception",
    "merge_canonical_from_preflight",
    "project_block_execute_args",
    "reset_block_capability_cache",
    "reset_preflight_cache",
    "run_block_preflight",
]
