"""Model-facing projection for block tool success payloads (spec §5).

Audit / logger paths may retain full addon payloads; ToolResult.ok for the model
uses only the slim decision fields per spec §5.1–§5.4:

- place:   ``{ok, status, at: [x, y, z], block, was?}`` (``was`` only when a
  non-air block was replaced)
- fill:    ``{ok, status, changed, skipped, type_counts?, bounds}``
  (``type_counts`` only carries non-air counts; omitted when all air)
- inspect: single ``{ok, block, states, waterlogged, is_air, is_liquid}`` /
  region ``{ok, count, type_counts, samples: [[x, y, z, type_id], ...] ≤ 8}``

Raw addon internals (locked_targets, before/after, verification, rollback,
phase, coordinate_mode, ...) are never mirrored to the model.
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


def _xyz_array(value: Any) -> list[int] | None:
    """Compact ``[x, y, z]`` array from a position dict or array (spec §5.1/§5.2)."""
    if isinstance(value, (list, tuple)) and len(value) == 3:
        if all(
            isinstance(coord, (int, float)) and not isinstance(coord, bool)
            for coord in value
        ):
            return [int(value[0]), int(value[1]), int(value[2])]
        return None
    pos = _xyz(value)
    if pos is None:
        return None
    return [int(pos["x"]), int(pos["y"]), int(pos["z"])]


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


def _place_position(payload: dict[str, Any]) -> list[int] | None:
    for key in ("at", "position", "target"):
        position = _xyz_array(payload.get(key))
        if position is not None:
            return position
    targets = payload.get("targets")
    if isinstance(targets, list) and targets:
        return _xyz_array(targets[0])
    after = payload.get("after")
    if isinstance(after, dict):
        return _xyz_array(after)
    before = payload.get("before")
    if isinstance(before, dict):
        return _xyz_array(before)
    return None


def project_place_result_for_model(payload: dict[str, Any]) -> dict[str, Any]:
    """Project a successful place execute payload (spec §5.1).

    ``was`` is only present when a non-air block was replaced.
    """
    out: dict[str, Any] = {"ok": True}
    status = payload.get("status")
    if isinstance(status, str) and status:
        out["status"] = status
    else:
        out["status"] = "applied"
    at = _place_position(payload)
    if at is not None:
        out["at"] = at
    block = _pick_type_id(payload)
    if block is not None:
        out["block"] = block
    was = _place_was(payload)
    if was is not None and not is_air(was):
        out["was"] = was
    return out


def _changed_count(payload: dict[str, Any]) -> int | None:
    if "changed_count" in payload:
        try:
            return int(payload["changed_count"])
        except (TypeError, ValueError):
            return None
    changed = payload.get("changed")
    if isinstance(changed, bool):
        return 1 if changed else 0
    if isinstance(changed, int):
        return changed
    return None


def _fill_bounds(
    payload: dict[str, Any],
    *,
    authorized_bounds: dict[str, Any] | None,
) -> list[list[int]] | None:
    """Pick the authorized AABB (preferred) or the execute corners as [[min],[max]]."""
    from_pos: dict[str, Any] | None = None
    to_pos: dict[str, Any] | None = None
    if isinstance(authorized_bounds, dict):
        from_pos = _xyz(
            authorized_bounds.get("from") or authorized_bounds.get("from_pos")
        )
        to_pos = _xyz(
            authorized_bounds.get("to") or authorized_bounds.get("to_pos")
        )
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
    if from_pos is None or to_pos is None:
        return None
    return [
        [int(from_pos["x"]), int(from_pos["y"]), int(from_pos["z"])],
        [int(to_pos["x"]), int(to_pos["y"]), int(to_pos["z"])],
    ]


def project_fill_result_for_model(
    payload: dict[str, Any],
    *,
    authorized_bounds: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Project a successful fill execute payload (spec §5.2).

    ``type_counts`` only carries non-air skip/replace counts and is omitted
    when everything was air. ``bounds`` reports the authorized (post-merge)
    AABB when available, never a locked-shrunk volume.
    """
    out: dict[str, Any] = {"ok": True}
    status = payload.get("status")
    if isinstance(status, str) and status:
        out["status"] = status
    else:
        out["status"] = "applied"
    changed = _changed_count(payload)
    if changed is not None:
        out["changed"] = changed
    skipped = payload.get("skipped")
    if isinstance(skipped, int):
        out["skipped"] = skipped
    counts = payload.get("previous_type_counts")
    if not isinstance(counts, dict) or not counts:
        counts = payload.get("type_counts")
    if isinstance(counts, dict) and counts:
        filtered = _filter_non_air_counts(counts)
        if filtered:
            out["type_counts"] = filtered
    bounds = _fill_bounds(payload, authorized_bounds=authorized_bounds)
    if bounds is not None:
        out["bounds"] = bounds
    return out


def _compact_sample(block: Any) -> list[Any] | None:
    """Compact ``[x, y, z, type_id]`` sample (spec §5.3)."""
    if not isinstance(block, dict):
        return None
    x, y, z = block.get("x"), block.get("y"), block.get("z")
    type_id = block.get("type_id")
    if not all(
        isinstance(coord, (int, float)) and not isinstance(coord, bool)
        for coord in (x, y, z)
    ):
        return None
    if not isinstance(type_id, str) or not type_id:
        return None
    return [int(x), int(y), int(z), type_id]


def _compact_samples(blocks: list[Any], limit: int = 8) -> list[list[Any]]:
    """Bound samples to ``limit`` compact arrays."""
    out: list[list[Any]] = []
    for block in blocks:
        if len(out) >= limit:
            break
        sample = _compact_sample(block)
        if sample is not None:
            out.append(sample)
    return out


def _single_block_fields(block: dict[str, Any]) -> dict[str, Any]:
    """Spec §5.3 single-point shape: block/states/waterlogged/is_air/is_liquid."""
    out: dict[str, Any] = {"ok": True}
    type_id = block.get("type_id")
    if isinstance(type_id, str) and type_id:
        out["block"] = type_id
    states = block.get("states")
    out["states"] = states if isinstance(states, dict) else {}
    out["waterlogged"] = bool(block.get("waterlogged"))
    out["is_air"] = bool(block.get("is_air"))
    out["is_liquid"] = bool(block.get("is_liquid"))
    return out


def _region_from_blocks(blocks: list[Any]) -> dict[str, Any]:
    """Aggregate a multi-block list into the spec §5.3 region shape."""
    out: dict[str, Any] = {"ok": True}
    count = 0
    type_counts: dict[str, int] = {}
    for block in blocks:
        if not isinstance(block, dict):
            continue
        count += 1
        type_id = block.get("type_id")
        if isinstance(type_id, str) and type_id:
            type_counts[type_id] = type_counts.get(type_id, 0) + 1
    out["count"] = count
    if type_counts:
        out["type_counts"] = type_counts
    out["samples"] = _compact_samples(blocks)
    return out


def _project_inspect(payload: dict[str, Any]) -> dict[str, Any]:
    """Project an inspect payload to model decision fields (spec §5.3).

    - summary path: ``{ok, count, type_counts, samples}`` with compact,
      bounded samples; status/bounds/unknown_count stay internal.
    - single snapshot: ``{ok, block, states, waterlogged, is_air, is_liquid}``.
    - multi-snapshot: region aggregation (``count``/``type_counts``/``samples``).
    """
    summary = payload.get("summary")
    if isinstance(summary, dict):
        out: dict[str, Any] = {"ok": True}
        count = summary.get("count")
        if isinstance(count, int):
            out["count"] = count
        type_counts = summary.get("type_counts")
        if isinstance(type_counts, dict) and type_counts:
            out["type_counts"] = type_counts
        samples = summary.get("samples")
        out["samples"] = _compact_samples(samples) if isinstance(samples, list) else []
        return out

    blocks = payload.get("blocks")
    if isinstance(blocks, list):
        if len(blocks) == 1 and isinstance(blocks[0], dict):
            return _single_block_fields(blocks[0])
        return _region_from_blocks(blocks)

    block = payload.get("block")
    if isinstance(block, dict):
        return _single_block_fields(block)

    return {"ok": True}


def project_block_result_for_model(
    payload: dict[str, Any] | Any,
    *,
    mode: str | None = None,
    authorized_bounds: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Project a successful block-tool payload down to slim model fields.

    ``mode`` routes place/fill to the spec §5.1/§5.2 projections. Inspect and
    unknown payloads (including legacy ``batch``) are projected via
    :func:`_project_inspect`, which strips internal metadata and never mirrors
    locked_targets / before / after / verification / rollback / phase.
    """
    if not isinstance(payload, dict):
        return {"ok": True, "result": payload}

    effective_mode = mode or payload.get("mode")
    if not isinstance(effective_mode, str):
        effective_mode = None

    if effective_mode == "place":
        return project_place_result_for_model(payload)
    if effective_mode == "fill":
        return project_fill_result_for_model(
            payload,
            authorized_bounds=authorized_bounds,
        )

    # batch / inspect / unknown: bounded projection, strip internal metadata.
    return _project_inspect(payload)


# ---------------------------------------------------------------------------
# Carve-out: grouped edit projections (spec §9.3). edit_blocks_impl keeps using
# these until Task 6 removes the grouped edit tool; keep them verbatim.
# ---------------------------------------------------------------------------


def _per_edit_changed(payload: dict[str, Any]) -> int:
    """Extract a per-edit changed-cell count from an execute payload."""
    if "changed_count" in payload:
        try:
            return int(payload["changed_count"])
        except (TypeError, ValueError):
            return 0
    if "changed" in payload:
        changed = payload["changed"]
        if isinstance(changed, bool):
            return 1 if changed else 0
        try:
            return int(changed)
        except (TypeError, ValueError):
            return 0
    return 0


def _per_edit_skipped(payload: dict[str, Any]) -> int:
    skipped = payload.get("skipped")
    try:
        return int(skipped) if skipped is not None else 0
    except (TypeError, ValueError):
        return 0


def _per_edit_skipped_type_counts(payload: dict[str, Any]) -> dict[str, Any]:
    counts = payload.get("skipped_type_counts")
    if isinstance(counts, dict) and counts:
        return counts
    # Fall back to previous_type_counts when the addon only reports that.
    previous = payload.get("previous_type_counts")
    filtered = _filter_non_air_counts(previous) if isinstance(previous, dict) else {}
    return filtered


def project_group_edit_result_for_model(
    per_edit_results: list[dict[str, Any]],
    *,
    repairs_applied: list[Any] | None = None,
) -> dict[str, Any]:
    """Aggregate per-edit execute outcomes into the group result (spec §9.3).

    ``per_edit_results`` is the ordered list of per-edit outcome dicts produced
    by the host execute loop. Each entry carries at least ``index`` and
    ``status``; successful entries additionally carry ``changed``, ``skipped``
    and ``skipped_type_counts``. The model receives only decision fields; full
    before/after/locked_targets/verification/rollback stay in the audit log.
    """
    edits: list[dict[str, Any]] = []
    changed_total = 0
    warnings: list[str] = []
    any_partial = False
    any_failed = False
    any_unknown = False
    any_applied = False
    failed_outcomes: list[dict[str, Any]] = []

    for outcome in per_edit_results:
        index = outcome.get("index")
        status = outcome.get("status") or "applied"
        changed = int(outcome.get("changed") or 0)
        skipped = int(outcome.get("skipped") or 0)
        changed_total += changed

        entry: dict[str, Any] = {"index": index, "status": status, "changed": changed}
        if skipped > 0:
            entry["skipped"] = skipped
            skipped_counts = outcome.get("skipped_type_counts")
            if isinstance(skipped_counts, dict) and skipped_counts:
                entry["skipped_type_counts"] = skipped_counts
        # Keep a bounded error code on failed/unknown edits so the model can
        # distinguish a definite failure from an unknown external state
        # (spec §9.1/§9.3) instead of describing the group as fully complete.
        if status in ("failed", "unknown"):
            code = outcome.get("code")
            if isinstance(code, str) and code:
                entry["error"] = code
        # Edits that never ran record which edit stopped them (spec §8.3).
        stopped_by = outcome.get("stopped_by_index")
        if isinstance(stopped_by, int):
            entry["stopped_by_index"] = stopped_by
        edits.append(entry)

        if status == "applied" or status == "noop":
            if status == "applied":
                any_applied = True
        elif status == "partial":
            any_partial = True
        elif status == "unknown":
            any_unknown = True
            if outcome.get("stopped_by_index") is None:
                failed_outcomes.append(outcome)
        else:  # failed
            any_failed = True
            if outcome.get("stopped_by_index") is None:
                failed_outcomes.append(outcome)

        if outcome.get("warning"):
            warnings.append(str(outcome["warning"]))

    if any_unknown:
        group_status = "unknown"
    elif any_partial:
        group_status = "partial"
    elif any_failed and not any_applied:
        group_status = "failed"
    elif any_failed:
        group_status = "partial"
    elif any_applied:
        group_status = "applied"
    else:
        # Nothing was applied, skipped, failed or unknown: every edit was a
        # noop. Report the truthful noop group status (spec §9.1).
        group_status = "noop"

    ok = not any_failed and not any_unknown
    if any_partial and not warnings:
        warnings.append("部分位置因前置条件不满足而跳过")
    if any_failed or any_unknown:
        # Only edits that actually failed (not the ones stopped before running)
        # are named in the bounded warning; stopped entries carry
        # ``stopped_by_index`` instead.
        failed_indices = [
            entry.get("index") for entry in edits
            if entry.get("status") in ("failed", "unknown")
            and entry.get("stopped_by_index") is None
        ]
        if failed_indices:
            warnings.append(
                "编辑 " + "、".join(str(i) for i in failed_indices)
                + " 失败或状态未知，未完成的后续编辑已停止"
            )

    result: dict[str, Any] = {
        "ok": ok,
        "status": group_status,
        "changed_total": changed_total,
        "edits": edits,
    }
    if repairs_applied:
        result["repairs_applied"] = repairs_applied[:8]
    if warnings:
        result["warnings"] = warnings
    if failed_outcomes:
        first_code = failed_outcomes[0].get("code")
        result["code"] = first_code if isinstance(first_code, str) and first_code else "INTERNAL_ERROR"
        result["fallback_allowed"] = all(
            bool(outcome.get("fallback_allowed", False)) for outcome in failed_outcomes
        )
    return result
