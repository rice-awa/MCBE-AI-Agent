"""Block-ops host limits from settings.addon.block_tools."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


HARD_MAX_DISCRETE_POSITIONS = 1024
HARD_MAX_FILL_VOLUME = 16384
HARD_MAX_CELLS_PER_TICK = 512
HARD_MAX_LOCKED_TARGETS_ON_WIRE = 1024

DEFAULT_MAX_DISCRETE_POSITIONS = 256
DEFAULT_MAX_FILL_VOLUME = 4096
DEFAULT_CELLS_PER_TICK = 128
# 0 = absolute execute prefers omit locked_targets on the wire (see should_omit_*).
# When >0 and a non-omitted path must ship locked cells, enforce this cap.
DEFAULT_MAX_LOCKED_TARGETS_ON_WIRE = 0
# MCBE commandLine hard budget (empirically ~461 B); mirrors Settings.flow_control.
DEFAULT_COMMAND_LINE_BYTE_BUDGET = 461


@dataclass(frozen=True)
class BlockToolsLimits:
    max_discrete_positions: int = DEFAULT_MAX_DISCRETE_POSITIONS
    max_fill_volume: int = DEFAULT_MAX_FILL_VOLUME
    cells_per_tick: int = DEFAULT_CELLS_PER_TICK
    max_locked_targets_on_wire: int = DEFAULT_MAX_LOCKED_TARGETS_ON_WIRE


def _clamp(value: int, *, minimum: int, hard_max: int) -> int:
    return max(minimum, min(int(value), hard_max))


def get_command_line_byte_budget(settings: Any | None = None) -> int:
    """Read MCBE commandLine byte budget from settings.flow_control (default 461)."""
    if settings is None:
        return DEFAULT_COMMAND_LINE_BYTE_BUDGET
    flow = getattr(settings, "flow_control", None)
    if flow is None:
        return DEFAULT_COMMAND_LINE_BYTE_BUDGET
    if isinstance(flow, dict):
        raw = flow.get("command_line_byte_budget", DEFAULT_COMMAND_LINE_BYTE_BUDGET)
    else:
        raw = getattr(flow, "command_line_byte_budget", DEFAULT_COMMAND_LINE_BYTE_BUDGET)
    try:
        budget = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_COMMAND_LINE_BYTE_BUDGET
    if budget <= 0:
        return DEFAULT_COMMAND_LINE_BYTE_BUDGET
    return budget


def get_block_tools_limits(settings: Any | None = None) -> BlockToolsLimits:
    """Read and clamp limits from settings.addon.block_tools."""
    block_tools = None
    if settings is not None:
        addon = getattr(settings, "addon", None)
        block_tools = getattr(addon, "block_tools", None) if addon is not None else None
        if block_tools is None:
            block_tools = getattr(settings, "block_tools", None)

    max_positions = DEFAULT_MAX_DISCRETE_POSITIONS
    max_fill = DEFAULT_MAX_FILL_VOLUME
    cells = DEFAULT_CELLS_PER_TICK
    max_locked_on_wire = DEFAULT_MAX_LOCKED_TARGETS_ON_WIRE

    if block_tools is not None:
        if isinstance(block_tools, dict):
            max_positions = int(
                block_tools.get("max_discrete_positions", max_positions) or max_positions
            )
            max_fill = int(block_tools.get("max_fill_volume", max_fill) or max_fill)
            cells = int(block_tools.get("cells_per_tick", cells) or cells)
            raw_locked = block_tools.get(
                "max_locked_targets_on_wire", max_locked_on_wire
            )
            # Preserve explicit 0 (omit preference); only fall back when missing/None.
            if raw_locked is None:
                max_locked_on_wire = DEFAULT_MAX_LOCKED_TARGETS_ON_WIRE
            else:
                max_locked_on_wire = int(raw_locked)
        else:
            max_positions = int(
                getattr(block_tools, "max_discrete_positions", max_positions)
                or max_positions
            )
            max_fill = int(getattr(block_tools, "max_fill_volume", max_fill) or max_fill)
            cells = int(getattr(block_tools, "cells_per_tick", cells) or cells)
            raw_locked = getattr(
                block_tools, "max_locked_targets_on_wire", max_locked_on_wire
            )
            if raw_locked is None:
                max_locked_on_wire = DEFAULT_MAX_LOCKED_TARGETS_ON_WIRE
            else:
                max_locked_on_wire = int(raw_locked)

    return BlockToolsLimits(
        max_discrete_positions=_clamp(
            max_positions, minimum=1, hard_max=HARD_MAX_DISCRETE_POSITIONS
        ),
        max_fill_volume=_clamp(max_fill, minimum=1, hard_max=HARD_MAX_FILL_VOLUME),
        cells_per_tick=_clamp(cells, minimum=1, hard_max=HARD_MAX_CELLS_PER_TICK),
        # 0 is intentional (absolute omit preference); clamp only the upper bound.
        max_locked_targets_on_wire=max(
            0, min(int(max_locked_on_wire), HARD_MAX_LOCKED_TARGETS_ON_WIRE)
        ),
    )
