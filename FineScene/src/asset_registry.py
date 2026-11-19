"""Small local component catalog used by FineScene's deterministic asset resolver."""
from __future__ import annotations

from pathlib import Path

import finescene_env as fenv
from typing import Any


OAKWARDEN_BLEND = fenv.OAKWARDEN_DIR / "review_02" / "medieval_armchair.blend"
OAKWARDEN_GLB = OAKWARDEN_BLEND.with_suffix(".glb")


def resolve_local_assets(contract: dict[str, Any]) -> list[dict[str, str]]:
    resolved = []
    for task in contract.get("furniture_tasks", []):
        if task["kind"] != "chair":
            continue
        role = str(task.get("role") or "").lower()
        zone = str(task.get("zone") or "").lower()
        task_id = str(task.get("component_id") or "").lower()
        is_reading_chair = role == "reading" or "reading" in zone or "reading" in task_id
        # A desk chair is a functional, editable component and stays on its
        # parametric builder. Never replace it with the lounge-chair asset just
        # because both use the broad chair kind.
        if role == "desk" or (not is_reading_chair and not task.get("refined_blend")):
            continue
        requested = task.get("refined_blend")
        if (not requested or "oakwarden" in str(requested).lower()) and OAKWARDEN_BLEND.is_file() and OAKWARDEN_GLB.is_file():
            task["refined_blend"] = str(OAKWARDEN_BLEND)
            task["refined_mesh"] = str(OAKWARDEN_GLB)
            task["asset_id"] = "oakwarden_medieval_armchair"
            resolved.append({"component_id": task["component_id"], "asset_id": task["asset_id"], "source": str(OAKWARDEN_BLEND)})
        elif task.get("refined_blend") and Path(task["refined_blend"]).resolve() == OAKWARDEN_BLEND.resolve():
            task.setdefault("refined_mesh", str(OAKWARDEN_GLB))
            task.setdefault("asset_id", "oakwarden_medieval_armchair")
            resolved.append({"component_id": task["component_id"], "asset_id": task["asset_id"], "source": str(OAKWARDEN_BLEND)})
        if task.get("asset_id") == "oakwarden_medieval_armchair":
            task.setdefault("parameters", {}).update(width=1.06, depth=0.925, height=1.68)
            component = next(c for c in contract["components"] if c["id"] == task["component_id"])
            component["bounds"]["size"] = [1.06, 0.925, 1.68]
            component["source"].update(mode="retrieved", path=str(OAKWARDEN_BLEND))
    return resolved


__all__ = ["resolve_local_assets"]
