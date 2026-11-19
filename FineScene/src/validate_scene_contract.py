"""Validate and apply the first FineScene scene contract without third-party packages."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def fail(message: str) -> None:
    raise ValueError(message)


def finite_triplet(value: Any, label: str, positive: bool = False) -> None:
    if not isinstance(value, list) or len(value) != 3 or not all(isinstance(x, (int, float)) for x in value):
        fail(f"{label} must be a numeric triplet")
    if positive and any(x <= 0 for x in value):
        fail(f"{label} must be positive")


def validate_scene(scene: dict[str, Any]) -> None:
    if scene.get("schema") != "finescene.scene.v1":
        fail("unsupported scene schema")
    if scene.get("units") != "m":
        fail("scene units must be metres")
    components = scene.get("components")
    if not isinstance(components, list) or not components:
        fail("scene must contain components")
    ids = [item.get("id") for item in components]
    if any(not isinstance(item, str) or not item for item in ids):
        fail("every component needs a non-empty id")
    if len(ids) != len(set(ids)):
        fail("component ids must be unique")
    known = set(ids)
    for component in components:
        for key in ("kind", "bounds", "anchors", "editable", "source"):
            if key not in component:
                fail(f"component {component.get('id')} is missing {key}")
        finite_triplet(component["bounds"]["size"], f"{component['id']}.bounds.size", positive=True)
        parent = component.get("parent")
        if parent is not None and parent not in known:
            fail(f"component {component['id']} has unknown parent {parent}")
        source = component["source"]
        if source.get("mode") not in {"adsl", "retrieved", "trellis", "manual"}:
            fail(f"component {component['id']} has unsupported source mode")
        if "transform" in component:
            transform = component["transform"]
            if "position" in transform:
                finite_triplet(transform["position"], f"{component['id']}.transform.position")
            if "rotation_deg" in transform:
                finite_triplet(transform["rotation_deg"], f"{component['id']}.transform.rotation_deg")
            if "scale" in transform:
                finite_triplet(transform["scale"], f"{component['id']}.transform.scale", positive=True)
    for relation in scene.get("relations", []):
        if relation.get("from") not in known or relation.get("to") not in known:
            fail(f"relation references unknown component: {relation}")
    for operation in scene.get("edit_operations", []):
        if operation.get("op") not in {"add", "set"}:
            fail(f"unsupported edit operation {operation.get('op')}")
        if not isinstance(operation.get("parameter"), str):
            fail("edit operation parameter must be a string")
        if not isinstance(operation.get("delta"), (int, float)):
            fail("edit operation delta must be numeric")


def dependency_closure(scene: dict[str, Any], operation: dict[str, Any]) -> list[str]:
    """Return components affected by the declared edit and explicit dependency edges."""
    declared = scene.get("parameter_dependencies", {}).get(operation["parameter"])
    expected = declared if declared is not None else operation.get("expected_affected", [])
    affected = set(expected)
    changed_parameter = operation["parameter"]
    for component in scene["components"]:
        if changed_parameter in component.get("editable", []):
            affected.add(component["id"])
    changed = True
    while changed:
        changed = False
        for relation in scene.get("relations", []):
            if relation.get("type") == "depends_on" and relation.get("to") in affected:
                if relation.get("from") not in affected:
                    affected.add(relation["from"])
                    changed = True
    return sorted(affected)


def apply_operations(scene: dict[str, Any]) -> dict[str, Any]:
    result = json.loads(json.dumps(scene))
    for operation in result.get("edit_operations", []):
        name = operation["parameter"]
        before = result["parameters"].get(name)
        if before is None:
            fail(f"edit references unknown parameter {name}")
        result["parameters"][name] = before + operation["delta"] if operation["op"] == "add" else operation["delta"]
        operation["resolved_affected"] = dependency_closure(result, operation)
        operation["before"] = before
        operation["after"] = result["parameters"][name]
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scene", type=Path, required=True)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    scene = json.loads(args.scene.read_text(encoding="utf-8"))
    validate_scene(scene)
    result = apply_operations(scene)
    output = args.out or args.scene.with_name(args.scene.stem + ".resolved.json")
    output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"status": "ok", "scene_id": scene["scene_id"], "output": str(output), "operations": [{"id": op["id"], "before": op["before"], "after": op["after"], "affected": op["resolved_affected"]} for op in result.get("edit_operations", [])]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
