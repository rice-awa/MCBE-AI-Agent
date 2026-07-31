"""Model-facing projection for block tool success payloads.

Audit / logger paths may retain full addon payloads; ToolResult.ok for the model
uses only decision fields (place / batch / fill).

Success projection filters air replacements so the model only sees non-air
``was`` / ``previous_type_counts`` as overwrite signals.
"""

from __future__ import annotations

from typing import Any


def _normalize_type_id(type_id: str) -> str:
    """Normalize a typeId for air comparison (strip, lower, drop namespace)."""
    text = type_id.strip().lower()
    if ":" in text:
        return text.rsplit(":", 1)[-1]
    return text


def is_air(type_id: Any) -> bool:
    """Strict air check: only bare ``air`` after normalize (not cave_air/void_air)."""
    if not isinstance(type_id, str) or not type_id:
        return False
    return _normalize_type_id(type_id) == "air"


def _filter_non_air_counts(counts: dict[str, Any]) -> dict[str, Any]:
    """Drop air keys from previous_type_counts; preserve original key strings."""
    return {key: value for key, value in counts.items() if not is_air(key)}


def _xyz(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    out: dict[str, Any] = {}
    for key in ("x", "y", "z"):
        if key in value and value[key] is not None:
            out[key] = value[key]
    return out if out else None


def _aabb_volume(from_pos: dict[str, Any], to_pos: dict[str, Any]) -> int | None:
    try:
        dx = abs(int(to_pos["x"]) - int(from_pos["x"])) + 1
        dy = abs(int(to_pos["y"]) - int(from_pos["y"])) + 1
        dz = abs(int(to_pos["z"]) - int(from_pos["z"])) + 1
        return dx * dy * dz
    except (KeyError, TypeError, ValueError):
        return None


def _project_inspect(payload: dict[str, Any]) -> dict[str, Any]:
    """Project an inspect payload to model decision fields (issue 02).

    - Single/few points: return full ``blocks`` with type_id/states/waterlogged/
      is_air/is_liquid.
    - Multi-point/box summary: return ``bounds/count/type_counts/unknown_count/
      samples``.
    - Strip internal metadata (targets, player_origin, facing, player_name,
      repairs_applied, coordinate_mode, bridge diagnostics) from model result.
    """
    out: dict[str, Any] = {"ok": True}
    status = payload.get("status")
    if isinstance(status, str) and status:
        out["status"] = status
    else:
        out["status"] = "inspected"

    # Summary path: bounded projection.
    summary = payload.get("summary")
    if isinstance(summary, dict):
        out["bounds"] = summary.get("bounds", {})
        out["count"] = summary.get("count", 0)
        type_counts = summary.get("type_counts")
        if isinstance(type_counts, dict):
            out["type_counts"] = type_counts
        out["unknown_count"] = summary.get("unknown_count", 0)
        samples = summary.get("samples")
        if isinstance(samples, list):
            out["samples"] = samples
        return out

    # Full snapshots path.
    blocks = payload.get("blocks")
    if isinstance(blocks, list):
        out["blocks"] = blocks
    # Dimension only when explicitly relevant (single dimension result).
    dimension = payload.get("dimension")
    if isinstance(dimension, str) and dimension:
        out["dimension"] = dimension
    return out


def _pick_type_id(payload: dict[str, Any]) -> str | None:
    type_id = payload.get("type_id")
    if isinstance(type_id, str) and type_id:
        return type_id
    after = payload.get("after")
    if isinstance(after, dict):
        after_id = after.get("type_id")
        if isinstance(after_id, str) and after_id:
            return after_id
    return None


def _place_was(payload: dict[str, Any]) -> str | None:
    was = payload.get("was")
    if isinstance(was, str) and was:
        return was
    before = payload.get("before")
    if isinstance(before, dict):
        before_id = before.get("type_id")
        if isinstance(before_id, str) and before_id:
            return before_id
    if isinstance(before, list) and before:
        first = before[0]
        if isinstance(first, dict):
            before_id = first.get("type_id")
            if isinstance(before_id, str) and before_id:
                return before_id
    return None


def _place_at(payload: dict[str, Any]) -> dict[str, Any] | None:
    for key in ("at", "position", "target"):
        pos = _xyz(payload.get(key))
        if pos is not None:
            return pos
    targets = payload.get("targets")
    if isinstance(targets, list) and targets:
        return _xyz(targets[0])
    after = payload.get("after")
    if isinstance(after, dict):
        return _xyz(after)
    before = payload.get("before")
    if isinstance(before, dict):
        return _xyz(before)
    return None


def _changed_flag(payload: dict[str, Any]) -> bool | None:
    if "changed" in payload:
        changed = payload["changed"]
        if isinstance(changed, bool):
            return changed
        if isinstance(changed, int):
            return changed > 0
    if "changed_count" in payload:
        try:
            return int(payload["changed_count"]) > 0
        except (TypeError, ValueError):
            return None
    return None


def _project_place(payload: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"ok": True, "mode": "place"}
    status = payload.get("status")
    if isinstance(status, str) and status:
        out["status"] = status
    changed = _changed_flag(payload)
    if changed is not None:
        out["changed"] = changed
    at = _place_at(payload)
    if at is not None:
        out["at"] = at
    type_id = _pick_type_id(payload)
    if type_id is not None:
        out["type_id"] = type_id
    was = _place_was(payload)
    # Only report non-air replacements as overwrite signal for the model.
    if was is not None and not is_air(was):
        out["was"] = was
    states = payload.get("states")
    if isinstance(states, dict) and states:
        out["states"] = states
    # dimension only when payload explicitly marks non-default via include_dimension
    dimension = payload.get("dimension")
    if isinstance(dimension, str) and payload.get("include_dimension") is True:
        out["dimension"] = dimension
    return out


def _project_batch(payload: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"ok": True, "mode": "batch"}
    status = payload.get("status")
    if isinstance(status, str) and status:
        out["status"] = status
    if "changed_count" in payload:
        out["changed_count"] = payload["changed_count"]
    elif isinstance(payload.get("changed"), int):
        out["changed_count"] = payload["changed"]
    if "failed_count" in payload:
        out["failed_count"] = payload["failed_count"]
    type_id = _pick_type_id(payload)
    if type_id is not None:
        out["type_id"] = type_id
    counts = payload.get("previous_type_counts")
    if isinstance(counts, dict) and counts:
        filtered = _filter_non_air_counts(counts)
        if filtered:
            out["previous_type_counts"] = filtered
    return out


def _project_fill(
    payload: dict[str, Any],
    *,
    authorized_bounds: dict[str, Any] | None,
) -> dict[str, Any]:
    out: dict[str, Any] = {"ok": True, "mode": "fill"}
    status = payload.get("status")
    if isinstance(status, str) and status:
        out["status"] = status
    elif isinstance(payload.get("skipped"), int) and payload["skipped"] > 0:
        out["status"] = "partial"
    if "changed_count" in payload:
        out["changed_count"] = payload["changed_count"]
    elif isinstance(payload.get("changed"), int):
        out["changed_count"] = payload["changed"]
    if "skipped" in payload:
        out["skipped"] = payload["skipped"]
    type_id = _pick_type_id(payload)
    if type_id is not None:
        out["type_id"] = type_id
    counts = payload.get("previous_type_counts")
    if isinstance(counts, dict) and counts:
        filtered = _filter_non_air_counts(counts)
        if filtered:
            out["previous_type_counts"] = filtered

    from_pos: dict[str, Any] | None = None
    to_pos: dict[str, Any] | None = None
    volume: int | None = None

    if isinstance(authorized_bounds, dict):
        from_pos = _xyz(authorized_bounds.get("from") or authorized_bounds.get("from_pos"))
        to_pos = _xyz(authorized_bounds.get("to") or authorized_bounds.get("to_pos"))
        if "volume" in authorized_bounds:
            try:
                volume = int(authorized_bounds["volume"])
            except (TypeError, ValueError):
                volume = None

    if from_pos is None:
        from_pos = _xyz(payload.get("from") or payload.get("from_pos"))
    if to_pos is None:
        to_pos = _xyz(payload.get("to") or payload.get("to_pos"))
    if from_pos is None or to_pos is None:
        bounds = payload.get("bounds")
        if isinstance(bounds, dict):
            if from_pos is None:
                from_pos = _xyz(bounds.get("min") or bounds.get("from"))
            if to_pos is None:
                to_pos = _xyz(bounds.get("max") or bounds.get("to"))

    if from_pos is not None:
        out["from"] = from_pos
    if to_pos is not None:
        out["to"] = to_pos

    if volume is None and from_pos is not None and to_pos is not None:
        volume = _aabb_volume(from_pos, to_pos)
    if volume is None and "volume" in payload:
        try:
            volume = int(payload["volume"])
        except (TypeError, ValueError):
            volume = None
    if volume is not None:
        out["volume"] = volume

    return out


def project_block_result_for_model(
    payload: dict[str, Any] | Any,
    *,
    mode: str | None = None,
    authorized_bounds: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Project a successful block-tool payload down to model decision fields.

    Inspect payloads (no ``mode`` or ``status=inspected``) are projected via
    :func:`_project_inspect` which strips internal metadata (targets,
    player_origin, facing, player_name, repairs_applied, coordinate_mode) and
    returns either full ``blocks`` or a bounded ``summary``.
    Full audit fields (targets, before/after, before_samples, verification,
    rollback, phase) are stripped for mutation modes.
    """
    if not isinstance(payload, dict):
        return {"ok": True, "result": payload}

    effective_mode = mode or payload.get("mode")
    if not isinstance(effective_mode, str):
        effective_mode = None

    if effective_mode == "place":
        return _project_place(payload)
    if effective_mode == "batch":
        return _project_batch(payload)
    if effective_mode == "fill":
        return _project_fill(payload, authorized_bounds=authorized_bounds)

    # inspect / unknown: project bounded result, strip internal metadata.
    return _project_inspect(payload)
