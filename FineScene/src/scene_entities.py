"""Materialize semantically independent contents from parametric parent assets."""
from __future__ import annotations

import math
import hashlib
import random
from typing import Any


def _component(component_id: str, kind: str, parent: str, size: tuple[float, float, float], position: tuple[float, float, float], editable: list[str] | None = None) -> dict[str, Any]:
    return {
        "id": component_id,
        "kind": kind,
        "parent": parent,
        "bounds": {"size": list(size), "unit": "m"},
        "transform": {"position": list(position), "rotation_deg": [0.0, 0.0, 0.0], "scale": [1.0, 1.0, 1.0]},
        "anchors": ["center", "bottom_center", "top"],
        "editable": editable or [],
        "style": "inherited_from_parent",
        "source": {"mode": "adsl", "path": None, "license": None, "generator": "FineScene semantic content builder"},
    }


def shelf_profile(task: dict[str, Any]) -> tuple[float, float, float, int, float, float]:
    params = task.get("parameters", {})
    width = float(params.get("width", 1.25))
    depth = float(params.get("depth", 0.42))
    thickness = float(params.get("shelf_thickness", 0.035))
    levels = int(params.get("levels", params.get("shelves", 4)))
    height = params.get("height")
    if params.get("shelf_clearance") is not None:
        clearance = float(params["shelf_clearance"])
    elif height is not None:
        clearance = (float(height) - 0.18 - (levels - 1) * thickness) / levels
    else:
        clearance = 0.5
    if levels < 2 or levels > 8 or min(width, depth, thickness, clearance) <= 0:
        raise ValueError(f"Invalid bookshelf contract for {task['component_id']}: {params}")
    return width, depth, thickness, levels, clearance, float(height) if height is not None else 0.0


def _world_position(task: dict[str, Any], local: tuple[float, float, float]) -> tuple[float, float, float]:
    angle = math.radians(float(task.get("rotation_deg", 0.0)))
    x, y, z = local
    px, py, pz = task["placement"]
    return (
        px + math.cos(angle) * x - math.sin(angle) * y,
        py + math.sin(angle) * x + math.cos(angle) * y,
        pz + z,
    )


def expand_bookshelf_components(contract: dict[str, Any]) -> None:
    """Add editable shelf and per-book IDs to the scene contract.

    Geometry is emitted as separate aDSL objects by the common assembly path;
    stable names allow edits to resize or reposition only the affected contents.
    """
    components = contract["components"]
    relations = contract["relations"]
    dependencies = contract.setdefault("parameter_dependencies", {})
    affected_by_clearance: list[str] = []
    for task in contract.get("furniture_tasks", []):
        if task["kind"] != "bookshelf":
            continue
        # The frame's crown height depends on the same spacing parameter as its
        # children, so the parent builder is part of the dependency closure too.
        affected_by_clearance.append(task["component_id"])
        width, depth, thickness, levels, clearance, _height = shelf_profile(task)
        parent = next((component for component in components if component["id"] == task["component_id"]), None)
        if parent:
            parent["bounds"]["size"] = [width, depth, 0.18 + levels * clearance + (levels - 1) * thickness]
        for index in range(levels):
            top = 0.18 + index * (clearance + thickness)
            shelf_id = f"{task['component_id']}.shelf_{index + 1}"
            shelf = _component(shelf_id, "shelf", task["component_id"], (width - 0.12, depth - 0.035, thickness), _world_position(task, (0, 0, top - thickness)), ["shelf_clearance"] if index else [])
            shelf["transform"]["rotation_deg"][2] = float(task.get("rotation_deg", 0))
            components.append(shelf)
            relations.append({"type": "parent", "from": shelf_id, "to": task["component_id"]})
            if index:
                affected_by_clearance.append(shelf_id)
            rng = random.Random(int(hashlib.sha256(shelf_id.encode()).hexdigest()[:16], 16))
            specs = []
            # Independently seeded left/center/right stack bays vary across cabinets.
            inner = width - .20
            stack_w = min(rng.uniform(.24,.31), inner * .27)
            stack_d = min(.24, depth * .67)
            stack_side = rng.choice((-1,0,1))
            stack_x = stack_side * (inner / 2 - stack_w / 2)
            stack_z = 0.0
            for j in range(2 + rng.randrange(3)):
                dims = (stack_w - j * .012, stack_d - j * .008, rng.uniform(.026, .046))
                specs.append(dict(size=dims, raw=dims, local=[stack_x, -.025, stack_z], lean=0., variant="horizontal_stack", support=len(specs) if j else None))
                stack_z += dims[2]
            # Independent, thin volumes; gaps between groups and a few true Y tilts.
            intervals = []
            if stack_side >= 0:
                intervals.append((-inner/2, stack_x-stack_w/2-.045))
            if stack_side <= 0:
                intervals.append((stack_x+stack_w/2+.045,inner/2))
            ends = []
            n = 0
            for left,right in intervals:
                cursor = left
                while cursor < right - .075:
                    w = rng.uniform(.027,.061)
                    h = min(rng.uniform(.21,.35),clearance*.80)
                    d = min(rng.uniform(.16,.245),depth*.70)
                    lean = rng.choice((-11.,9.,-7.)) if n % 5 == 2 else 0.
                    angle = math.radians(abs(lean))
                    extent_w = w*math.cos(angle)+h*math.sin(angle)
                    extent_h = h*math.cos(angle)+w*math.sin(angle)
                    if cursor+extent_w > right-.04:
                        break
                    specs.append(dict(size=(extent_w,d,extent_h),raw=(w,d,h),local=[cursor+extent_w/2,-depth/2+.035+d/2+rng.uniform(0,.018),0],lean=lean,variant="leaning" if lean else "upright",support=None))
                    cursor += extent_w + (rng.uniform(.02,.045) if n%4==3 else rng.uniform(.003,.008))
                    n += 1
                if cursor+.03 <= right:
                    ends.append((cursor+.015,-.035,0.))
            ids = []
            for j, spec in enumerate(specs):
                book_id = f"{shelf_id}.book_{j+1:02d}"
                ids.append(book_id)
                offset = spec["local"]
                book = _component(book_id, "book", shelf_id, spec["size"], _world_position(task, (offset[0],offset[1],top+offset[2])), ["shelf_clearance"] if index else [])
                book.update(geometry_size=list(spec["raw"]), shelf_local_offset=offset, layout_lean_deg=spec["lean"], layout_variant=spec["variant"], palette_index=rng.randrange(8))
                book["transform"]["rotation_deg"][2] = float(task.get("rotation_deg",0))
                components.append(book)
                support = f"{shelf_id}.book_{spec['support']:02d}" if spec["support"] is not None else shelf_id
                relations.extend([{"type":"parent","from":book_id,"to":shelf_id}, {"type":"supported_by","from":book_id,"to":support,"anchor_from":"bottom_center","anchor_to":"top","axis":"+Z","value":0.}])
            for j,offset in enumerate(ends,1):
                end_id = f"{shelf_id}.bookend_{j}"
                end = _component(end_id,"bookend",shelf_id,(.03,min(.19,depth*.6),.16),_world_position(task,(offset[0],offset[1],top)),["shelf_clearance"] if index else [])
                end["shelf_local_offset"] = list(offset)
                end["transform"]["rotation_deg"][2] = float(task.get("rotation_deg",0))
                components.append(end)
                ids.append(end_id)
                relations.extend([{"type":"parent","from":end_id,"to":shelf_id},{"type":"supported_by","from":end_id,"to":shelf_id}])
            if index:
                affected_by_clearance.extend(ids)
    if affected_by_clearance:
        dependencies["shelf_clearance"] = sorted(set(dependencies.get("shelf_clearance", [])) | set(affected_by_clearance))


def refresh_bookshelf_components(contract: dict[str, Any]) -> None:
    """Re-evaluate shelf boards/books after a spacing edit while preserving IDs."""
    components = {component["id"]: component for component in contract["components"]}
    for task in contract.get("furniture_tasks", []):
        if task["kind"] != "bookshelf":
            continue
        width, depth, thickness, levels, clearance, _height = shelf_profile(task)
        parent = components.get(task["component_id"])
        if parent:
            parent["bounds"]["size"] = [width, depth, 0.18 + levels * clearance + (levels - 1) * thickness]
        for index in range(levels):
            top = 0.18 + index * (clearance + thickness)
            shelf_id = f"{task['component_id']}.shelf_{index+1}"
            shelf = components[shelf_id]
            shelf["bounds"]["size"] = [width-.12,depth-.035,thickness]
            shelf["transform"]["position"] = list(_world_position(task,(0,0,top-thickness)))
            shelf["transform"]["rotation_deg"][2] = float(task.get("rotation_deg",0))
            for book in components.values():
                if book.get("parent") != shelf_id or book["kind"] not in {"book","bookend"}:
                    continue
                x,y,z = book["shelf_local_offset"]
                w,d,h = book["bounds"]["size"]
                if z+h > clearance-.025+1e-6 or abs(x)+w/2 > (width-.12)/2+1e-6 or abs(y)+d/2 > (depth-.035)/2+1e-6:
                    raise ValueError(f"Rigid book {book['id']} does not fit edited shelf")
                book["transform"]["position"] = list(_world_position(task,(x,y,top+z)))
                book["transform"]["rotation_deg"][2] = float(task.get("rotation_deg",0))


__all__ = ["expand_bookshelf_components", "refresh_bookshelf_components", "shelf_profile"]
