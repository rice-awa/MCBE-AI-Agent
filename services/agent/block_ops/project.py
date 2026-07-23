"""Model-facing projection for block tool success payloads.

Audit / logger paths may retain full addon payloads; ToolResult.ok for the model
uses only decision fields (place / batch / fill).
"""

from __future__ import annotations

from typing import Any


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
    if was is not None:
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
        out["previous_type_counts"] = counts
    return out


def _project_fill(
    payload: dict[str, Any],
    *,
    authorized_bounds: dict[str, Any] | None,
) -> dict[str, Any]:
    out: dict[str, Any] = {"ok": True, "mode": "fill"}
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
        out["previous_type_counts"] = counts

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

    Non place/batch/fill payloads are returned largely as-is (inspect / legacy).
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

    # inspect / unknown: pass through with ok, drop nothing required
    if payload.get("ok") is True:
        return dict(payload)
    out = dict(payload)
    out.setdefault("ok", True)
    return out
