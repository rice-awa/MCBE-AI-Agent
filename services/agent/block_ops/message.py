"""Add-on bridge payload building for block tools.

Consolidates payload construction for inspect, place/batch, and fill operations
so that ``tools_impl`` and ``target`` focus on validation and normalization
respectively, without owning wire-format assembly.

Extracted from ``tools_impl.build_inspect_payload``, ``tools_impl.build_edit_payload``,
and ``target.build_inspect_payload_from_target``.
"""
from __future__ import annotations

from typing import Any

from services.agent.block_ops.limits import (
    apply_limits_to_payload,
    compact_locked_targets_for_wire,
    should_omit_locked_targets_on_wire,
)
from services.agent.block_ops.target import NormalizedTarget


def build_inspect_payload(
    *,
    coordinate_mode: str,
    dimension: str | None,
    position: dict[str, Any] | None,
    positions: list[dict[str, Any]] | None,
    player_name: str,
    phase: str | None = None,
    locked_targets: list[dict[str, Any]] | None = None,
    limits: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Build the Add-on inspect payload from decomposed target coordinates."""
    payload: dict[str, Any] = {
        "coordinate_mode": coordinate_mode,
        "player_name": player_name,
    }
    if dimension is not None:
        payload["dimension"] = dimension
    if position is not None:
        payload["position"] = position
    if positions is not None:
        payload["positions"] = positions
    if phase is not None:
        payload["phase"] = phase
    if locked_targets is not None:
        payload["locked_targets"] = locked_targets
    apply_limits_to_payload(payload, limits)
    return payload


def build_inspect_payload_from_target(
    normalized: NormalizedTarget,
    *,
    dimension: str | None,
    player_name: str,
    phase: str | None = None,
    locked_targets: list[dict[str, Any]] | None = None,
    limits: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Build the Add-on inspect payload from a normalized unified target.

    The Add-on inspect handler accepts both the legacy
    ``coordinate_mode/position/positions`` shape and the new ``target`` shape.
    We send the unified ``target`` so the Add-on can resolve box volumes and
    auto-summary in one place.
    """
    payload: dict[str, Any] = {
        "coordinate_mode": normalized.coordinate_mode,
        "player_name": player_name,
    }
    if dimension is not None:
        payload["dimension"] = dimension
    if normalized.shape == "positions":
        payload["target"] = {"positions": normalized.positions}
    else:
        payload["target"] = {
            "box": {"from": normalized.box_from, "to": normalized.box_to}
        }
    if phase is not None:
        payload["phase"] = phase
    if locked_targets is not None:
        payload["locked_targets"] = locked_targets
    if limits is not None:
        apply_limits_to_payload(payload, limits)
    return payload


def build_edit_payload(
    *,
    mode: str,
    coordinate_mode: str,
    dimension: str | None,
    position: dict[str, Any] | None,
    positions: list[dict[str, Any]] | None,
    from_pos: dict[str, Any] | None,
    to_pos: dict[str, Any] | None,
    type_id: str,
    states: dict[str, Any] | None,
    replace_any: bool,
    expected_previous: dict[str, Any] | None,
    player_name: str,
    phase: str,
    locked_targets: list[dict[str, Any]] | None = None,
    limits: dict[str, int] | None = None,
    max_locked_targets_on_wire: int | None = None,
) -> dict[str, Any]:
    """Build the Add-on edit payload for place/batch/fill operations."""
    payload: dict[str, Any] = {
        "mode": mode,
        "coordinate_mode": coordinate_mode,
        "type_id": type_id,
        "replace_any": bool(replace_any),
        "player_name": player_name,
        "phase": phase,
    }
    if dimension is not None:
        payload["dimension"] = dimension
    if position is not None:
        payload["position"] = position
    if positions is not None:
        payload["positions"] = positions
    if from_pos is not None:
        payload["from"] = from_pos
    if to_pos is not None:
        payload["to"] = to_pos
    if states is not None:
        payload["states"] = states
    if expected_previous is not None:
        payload["expected_previous"] = expected_previous

    wire_cap = 0 if max_locked_targets_on_wire is None else int(max_locked_targets_on_wire)
    if limits is not None and "max_locked_targets_on_wire" in limits and max_locked_targets_on_wire is None:
        try:
            wire_cap = int(limits["max_locked_targets_on_wire"])
        except (TypeError, ValueError, KeyError):
            wire_cap = 0

    omit_locked = should_omit_locked_targets_on_wire(
        mode=mode,
        phase=phase,
        from_pos=from_pos if isinstance(from_pos, dict) else None,
        to_pos=to_pos if isinstance(to_pos, dict) else None,
        position=position if isinstance(position, dict) else None,
        positions=positions if isinstance(positions, list) else None,
        locked_targets=locked_targets if isinstance(locked_targets, list) else None,
        coordinate_mode=coordinate_mode,
        max_locked_targets_on_wire=wire_cap,
    )
    if locked_targets is not None and not omit_locked:
        payload["locked_targets"] = compact_locked_targets_for_wire(
            locked_targets,
            dimension=dimension,
        )
    # Limits only affect preflight enumeration / host checks. Omitting them on
    # execute saves critical commandLine bytes under the 461 B MCBE budget.
    # A single-cell ``place`` preflight also omits them: the host has already
    # validated the one position against every limit, the Add-on needs no
    # enumeration caps for a single cell, and the extra keys push expect-type
    # frames (``expected_previous``) over the MCBE commandLine budget.
    if phase != "execute" and mode != "place":
        apply_limits_to_payload(payload, limits)
    return payload
