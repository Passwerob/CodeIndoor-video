"""Edit-specific invariants: dependency closure must move, unrelated IDs must remain stable."""
from __future__ import annotations

from typing import Any


def judge_edit(before: dict[str, Any], after: dict[str, Any], operation: dict[str, Any]) -> dict[str, Any]:
    expected = set(operation.get("resolved_affected", []))
    before_components = {item["id"]: item for item in before.get("components", [])}
    after_components = {item["id"]: item for item in after.get("components", [])}
    before_tasks = {item["component_id"]: item for item in before.get("furniture_tasks", [])}
    after_tasks = {item["component_id"]: item for item in after.get("furniture_tasks", [])}
    changed_builders = {
        component_id
        for component_id in before_tasks.keys() & after_tasks.keys()
        if before_tasks[component_id].get("parameters", {}) != after_tasks[component_id].get("parameters", {})
    }
    moved = {
        component_id
        for component_id in before_components.keys() & after_components.keys()
        if before_components[component_id].get("transform", {}) != after_components[component_id].get("transform", {})
    }
    resized = {
        component_id
        for component_id in before_components.keys() & after_components.keys()
        if before_components[component_id].get("bounds", {}) != after_components[component_id].get("bounds", {})
    }
    covered = set(changed_builders) | moved | resized
    missing_expected = sorted(expected - covered)
    unexpected_changed = sorted((changed_builders | moved | resized) - expected)
    rigid_resizes = sorted(component_id for component_id in resized
                           if before_components[component_id].get("kind") in {"book", "bookend"})
    added = sorted(after_components.keys() - before_components.keys())
    removed = sorted(before_components.keys() - after_components.keys())
    status = "passed" if not (missing_expected or unexpected_changed or rigid_resizes or added or removed) else "failed"
    return {
        "status": status,
        "operation": operation["id"],
        "expected_affected": sorted(expected),
        "rebuilt_component_builders": sorted(changed_builders),
        "propagated_component_transforms": sorted(moved),
        "propagated_component_resizes": sorted(resized),
        "stable_component_ids": sorted(set(before_components) & set(after_components) - expected),
        "missing_expected_changes": missing_expected,
        "unexpected_changed_components": unexpected_changed,
        "rigid_component_resizes": rigid_resizes,
        "added_component_ids": added,
        "removed_component_ids": removed,
        "note": "Builder parameter changes trigger deterministic geometry regeneration; this MVP checks parameter bindings and dependency closure, not mesh-level identity.",
    }


__all__ = ["judge_edit"]
