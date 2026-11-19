"""Deterministic spatial relations and post-build layout checks for FineScene."""
from __future__ import annotations

import math
from typing import Any


_DEFAULT_SIZE = {
    "bookshelf": (1.25, 0.42, 1.9),
    "desk": (1.5, 0.72, 0.76),
    "chair": (0.58, 0.58, 0.98),
}


def _task_size(task: dict[str, Any]) -> tuple[float, float, float]:
    defaults = _DEFAULT_SIZE.get(task["kind"], tuple(task.get("size", (0.5, 0.5, 0.5))))
    params = task.get("parameters", {})
    size = tuple(float(params.get(axis, defaults[index])) for index, axis in enumerate(("width", "depth", "height")))
    if task["kind"] == "bookshelf":
        size = (size[0] + .08, size[1] + .04, size[2])  # include the plinth
    return size


def _half_footprint(task: dict[str, Any]) -> tuple[float, float]:
    width, depth, _ = _task_size(task)
    angle = math.radians(float(task.get("rotation_deg", 0)))
    return ((abs(math.cos(angle)) * width + abs(math.sin(angle)) * depth) / 2,
            (abs(math.sin(angle)) * width + abs(math.cos(angle)) * depth) / 2)


def _fit_layout(contract: dict[str, Any], resolved: list[dict[str, Any]], *, pack_walls_only: bool = False) -> None:
    """Project floor furniture onto feasible footprints, preserving dimensions.

    Wall groups are packed in order. Other floor objects use the nearest free
    location on a 5 cm lattice. This is a conservative layout solver, not physics.
    """
    room = contract["room"]
    wall = float(room.get("wall_thickness", .12)) + .025
    limits = [(-room[a] / 2 + wall, room[a] / 2 - wall) for a in ("width", "depth")]
    furniture = contract.get("furniture_tasks", [])
    by_id = {c["id"]: c for c in contract["components"]}

    def move(task, xy, reason):
        before = list(task["placement"])
        task["placement"][:2] = xy
        if before != task["placement"]:
            resolved.append({"component_id": task["component_id"], "relation": reason, "before": before, "after": list(task["placement"])})
        by_id[task["component_id"]]["transform"]["position"] = list(task["placement"])

    for side in ("left", "right", "back", "front"):
        axis = 1 if side in {"left", "right"} else 0
        group = sorted((t for t in furniture if t.get("wall_attachment") == side), key=lambda t: t["placement"][axis])
        if not group:
            continue
        halves = [_half_footprint(t)[axis] for t in group]
        low, high = limits[axis]
        if sum(2 * h for h in halves) + .08 * (len(group) - 1) > high - low:
            raise ValueError(f"Furniture cannot fit along {side} wall without resizing")
        positions = [float(t["placement"][axis]) for t in group]
        edge = high
        for i in reversed(range(len(group))):
            positions[i] = min(positions[i], edge - halves[i])
            edge = positions[i] - halves[i] - .08
        edge = low
        for i, task in enumerate(group):
            positions[i] = max(positions[i], edge + halves[i])
            edge = positions[i] + halves[i] + .08
            xy = list(task["placement"][:2])
            xy[axis] = positions[i]
            move(task, xy, "wall_group_clearance")

    if pack_walls_only:
        return
    obstacles = []
    fixed = [t for t in furniture if t.get("wall_attachment") or t["kind"] == "desk" or (t["kind"] == "chair" and t.get("role") != "reading")]
    free = [t for t in furniture if t not in fixed]
    free += [t for t in contract.get("decor_tasks", []) if not t.get("parent_id")]
    for task in fixed:
        hx, hy = _half_footprint(task)
        x, y = task["placement"][:2]
        obstacles.append((x, y, hx, hy))
    for task in free:
        hx, hy = _half_footprint(task)
        lower = [limits[0][0] + hx, limits[1][0] + hy]
        upper = [limits[0][1] - hx, limits[1][1] - hy]
        if any(a > b for a, b in zip(lower, upper)):
            raise ValueError(f"{task['component_id']} cannot fit inside the room")
        original = task["placement"][:2]
        preferred = [min(max(original[a], lower[a]), upper[a]) for a in (0, 1)]
        rug = task["kind"] == "rug"
        def feasible(x, y):
            return rug or all(abs(x - ox) >= hx + ohx + .05 or abs(y - oy) >= hy + ohy + .05 for ox, oy, ohx, ohy in obstacles)
        xy = preferred
        if not feasible(*xy):
            candidates = ((lower[0] + i * .05, lower[1] + j * .05)
                          for i in range(int((upper[0] - lower[0]) / .05) + 1)
                          for j in range(int((upper[1] - lower[1]) / .05) + 1))
            xy = min((p for p in candidates if feasible(*p)), key=lambda p: math.dist(p, original), default=None)
            if xy is None:
                raise ValueError(f"No collision-free floor placement for {task['component_id']}")
        move(task, list(xy), "room_and_furniture_clearance")
        if not rug:
            obstacles.append((*xy, hx, hy))


def solve_spatial_constraints(contract: dict[str, Any]) -> dict[str, Any]:
    """Resolve wall attachments and enforce the small physical relations a
    planner cannot safely leave to free-form coordinates.

    The planner proposes zones and rough placements. This stage owns the
    deterministic facts: a wall-mounted frame must face into the room, a task
    chair must sit in front of its desk, and both objects must be represented by
    the same transform in the task and component records.
    """
    room = contract["room"]
    wall_thickness = float(room.get("wall_thickness", 0.12))
    resolved: list[dict[str, Any]] = []
    by_id = {component["id"]: component for component in contract["components"]}
    for task in contract.get("furniture_tasks", []):
        attachment = task.get("wall_attachment")
        if not attachment:
            continue
        params = task.setdefault("parameters", {})
        if task["kind"] == "bookshelf" and attachment in {"left", "right"}:
            # Some planner outputs describe an already rotated footprint
            # (narrow X by long Y). Normalize to the builder's canonical
            # front-facing width before applying its wall rotation.
            if "width" in params and "depth" in params and float(params["width"]) < float(params["depth"]):
                params["width"], params["depth"] = params["depth"], params["width"]
                component = by_id.get(task["component_id"])
                if component:
                    component["bounds"]["size"][:2] = [params["width"], params["depth"]]
        width, depth, _height = _task_size(task)
        angle_before = float(task.get("rotation_deg", 0.0))
        if task["kind"] in {"bookshelf", "desk"}:
            # The builder's back is local +Y. On the right wall +Y must point
            # toward +X (the wall), hence -90 degrees; the left wall mirrors it.
            task["rotation_deg"] = {"right": -90.0, "left": 90.0, "front": 180.0, "back": 0.0}[attachment]
        theta = math.radians(float(task.get("rotation_deg", 0.0)))
        half_x = (abs(math.cos(theta)) * width + abs(math.sin(theta)) * depth) / 2
        half_y = (abs(math.sin(theta)) * width + abs(math.cos(theta)) * depth) / 2
        placement = list(task["placement"])
        if attachment == "right":
            placement[0] = room["width"] / 2 - wall_thickness - half_x
        elif attachment == "left":
            placement[0] = -room["width"] / 2 + wall_thickness + half_x
        elif attachment == "back":
            placement[1] = room["depth"] / 2 - wall_thickness - half_y
        elif attachment == "front":
            placement[1] = -room["depth"] / 2 + wall_thickness + half_y
        else:
            raise ValueError(f"Unsupported wall attachment: {attachment}")
        before = list(task["placement"])
        task["placement"] = placement
        by_id[task["component_id"]]["transform"]["position"] = placement
        by_id[task["component_id"]]["transform"]["rotation_deg"][2] = float(task.get("rotation_deg", 0.0))
        resolved.append({"component_id": task["component_id"], "relation": f"attached_to_{attachment}_wall", "before": before, "after": placement, "rotation_before_deg": angle_before, "rotation_after_deg": task.get("rotation_deg", 0.0)})

    # A chair in a writing/work zone is a task chair, even when the planner's
    # wording does not explicitly say so. Place it from the desk's oriented
    # front face and make it face the desk. Reading chairs keep their proposed
    # position and can be routed to a refined asset independently.
    _fit_layout(contract, resolved, pack_walls_only=True)
    desks = [task for task in contract.get("furniture_tasks", []) if task["kind"] == "desk"]
    for desk in desks:
        # Clamp the desk BEFORE deriving its chair position. Moving it later
        # makes a second solve shift an unrelated chair during a shelf edit.
        hx, hy = _half_footprint(desk)
        margin = wall_thickness + .025
        for axis, extent, half in ((0, room["width"], hx), (1, room["depth"], hy)):
            low, high = -extent/2+margin+half, extent/2-margin-half
            if low > high:
                raise ValueError(f"Desk {desk['component_id']} does not fit")
            desk["placement"][axis] = min(max(desk["placement"][axis],low),high)
        by_id[desk["component_id"]]["transform"]["position"] = list(desk["placement"])
        desk_width, desk_depth, _desk_height = _task_size(desk)
        desk_angle = math.radians(float(desk.get("rotation_deg", 0.0)))
        front_x, front_y = math.sin(desk_angle), -math.cos(desk_angle)
        candidates = []
        for chair in contract.get("furniture_tasks", []):
            if chair["kind"] != "chair":
                continue
            if chair.get("role") == "reading":
                continue
            text = f"{chair.get('role', '')} {chair.get('zone', '')} {chair.get('component_id', '')}".lower()
            if any(token in text for token in ("desk", "writing", "work", "task", "nook")):
                candidates.append(chair)
        if not candidates:
            candidates = [
                chair for chair in contract.get("furniture_tasks", [])
                if chair["kind"] == "chair" and chair.get("role") != "reading"
                and "reading" not in f"{chair.get('zone', '')} {chair.get('component_id', '')}".lower()
            ]
        if not candidates:
            continue
        chair = min(candidates, key=lambda item: math.dist(tuple(item["placement"][:2]), tuple(desk["placement"][:2])))
        _chair_width, chair_depth, _chair_height = _task_size(chair)
        gap = max(0.18, float(chair.get("front_clearance", 0.2)))
        center_distance = desk_depth / 2 + chair_depth / 2 + gap
        desk_x, desk_y, _desk_z = desk["placement"]
        before = list(chair["placement"])
        chair["placement"] = [desk_x + front_x * center_distance, desk_y + front_y * center_distance, 0.0]
        chair["rotation_deg"] = (float(desk.get("rotation_deg", 0.0)) + 180.0) % 360.0
        component = by_id.get(chair["component_id"])
        if component:
            component["transform"]["position"] = list(chair["placement"])
            component["transform"]["rotation_deg"][2] = chair["rotation_deg"]
        resolved.append({
            "component_id": chair["component_id"],
            "relation": "paired_with_desk",
            "desk_id": desk["component_id"],
            "before": before,
            "after": list(chair["placement"]),
            "rotation_after_deg": chair["rotation_deg"],
            "clearance_m": gap,
        })
        relations = contract.setdefault("relations", [])
        relations[:] = [
            relation for relation in relations
            if not (relation.get("type") == "paired_with" and relation.get("from") == chair["component_id"])
        ]
        relations.append({"type": "paired_with", "from": chair["component_id"], "to": desk["component_id"], "clearance_m": gap})

    _fit_layout(contract, resolved)
    furniture_by_id = {task["component_id"]: task for task in contract.get("furniture_tasks", [])}
    decor_by_id = {task["component_id"]: task for task in contract.get("decor_tasks", [])}
    for item in contract.get("decor_tasks", []):
        parent_id = item.get("parent_id")
        parent = furniture_by_id.get(parent_id) or decor_by_id.get(parent_id)
        if not parent or parent.get("kind") not in {"desk", "side_table"}:
            continue
        parent_width, parent_depth, parent_height = _task_size(parent)
        if parent.get("kind") == "desk":
            child_width, child_depth = float(item["size"][0]), float(item["size"][1])
            angle = math.radians(float(parent.get("rotation_deg", 0.0)))
            px, py, pz = parent["placement"]
            old_x, old_y, _old_z = item["placement"]
            dx, dy = old_x - px, old_y - py
            local_x = math.cos(angle) * dx + math.sin(angle) * dy
            local_y = -math.sin(angle) * dx + math.cos(angle) * dy
            local_x = min(max(local_x, -parent_width / 2 + child_width / 2 + 0.04), parent_width / 2 - child_width / 2 - 0.04)
            local_y = min(max(local_y, -parent_depth / 2 + child_depth / 2 + 0.04), parent_depth / 2 - child_depth / 2 - 0.04)
            item["placement"] = [px + math.cos(angle) * local_x - math.sin(angle) * local_y, py + math.sin(angle) * local_x + math.cos(angle) * local_y, pz + parent_height]
        else:
            table_size = parent.get("size", [0.42, 0.42, 0.55])
            angle = math.radians(float(parent.get("rotation_deg", 0.0)))
            offset_x = -0.10 if item["kind"] == "lamp" else 0.10 if item["kind"] == "book_stack" else 0.0
            px, py, pz = parent["placement"]
            item["placement"] = [px + math.cos(angle) * offset_x, py + math.sin(angle) * offset_x, pz + float(table_size[2])]
        relation = {
            "type": "supported_by", "from": item["component_id"], "to": parent_id,
            "anchor_from": "bottom_center", "anchor_to": "top", "axis": "+Z", "value": 0.0,
        }
        if relation not in contract.setdefault("relations", []):
            contract["relations"].append(relation)
        component = by_id.get(item["component_id"])
        if component:
            component["transform"]["position"] = list(item["placement"])
    return {"resolved_relations": resolved, "status": "passed"}


def judge_component_bounds(
    contract: dict[str, Any],
    world_bounds: dict[str, tuple[list[float], list[float]]],
) -> dict[str, Any]:
    """Check placed components against the room envelope and report large AABB overlaps."""
    room = contract["room"]
    limits = ((-room["width"] / 2, room["width"] / 2), (-room["depth"] / 2, room["depth"] / 2), (0.0, room["height"]))
    task_ids = {task["component_id"] for task in contract.get("furniture_tasks", [])}
    task_ids.update(task["component_id"] for task in contract.get("decor_tasks", []))
    task_ids.update(component["id"] for component in contract.get("components", []) if component["kind"] in {"book", "bookend", "shelf"})
    missing = sorted(task_ids - world_bounds.keys())
    outside = []
    for component_id in sorted(task_ids):
        bounds = world_bounds.get(component_id)
        if not bounds:
            continue
        low, high = bounds
        axes = [axis for axis, (minimum, maximum) in enumerate(limits) if low[axis] < minimum - 1e-4 or high[axis] > maximum + 1e-4]
        if axes:
            outside.append({"component_id": component_id, "axes": axes, "bounds": bounds})

    parent = {component["id"]: component.get("parent") for component in contract["components"]}
    rug_ids = {task["component_id"] for task in contract.get("decor_tasks", []) if task["kind"] == "rug"}
    def related(left: str, right: str) -> bool:
        seen = set()
        while left in parent and left not in seen:
            seen.add(left)
            left = parent[left]
            if left == right:
                return True
        return False

    def root_task(component_id: str) -> str:
        current = component_id
        seen = set()
        while current in parent and current not in seen:
            seen.add(current)
            parent_id = parent[current]
            if parent_id in task_ids:
                current = parent_id
            else:
                break
        return current

    support_failures = []
    for relation in contract.get("relations", []):
        if relation.get("type") != "supported_by":
            continue
        child_id, support_id = relation["from"], relation["to"]
        if child_id not in world_bounds or support_id not in world_bounds:
            support_failures.append({"component_id": child_id, "support_id": support_id, "reason": "missing geometry"})
            continue
        low, high = world_bounds[child_id]
        support_low, support_high = world_bounds[support_id]
        gap = low[2] - support_high[2]
        contained = all(low[a] >= support_low[a] - 1e-5 and high[a] <= support_high[a] + 1e-5 for a in (0, 1))
        if abs(gap) > 1e-5 or not contained:
            support_failures.append({"component_id": child_id, "support_id": support_id, "gap_m": gap, "footprint_contained": contained})
    collisions = []
    root_collision_pairs: set[tuple[str, str]] = set()
    ids = [component_id for component_id in sorted(task_ids) if component_id in world_bounds]
    for index, left_id in enumerate(ids):
        if left_id.startswith("rug_") or left_id in rug_ids:
            continue
        for right_id in ids[index + 1:]:
            if right_id.startswith("rug_") or right_id in rug_ids or related(left_id, right_id) or related(right_id, left_id):
                continue
            roots = tuple(sorted((root_task(left_id), root_task(right_id))))
            if roots[0] != roots[1] and roots in root_collision_pairs:
                continue
            left_low, left_high = world_bounds[left_id]
            right_low, right_high = world_bounds[right_id]
            overlap = [min(left_high[a], right_high[a]) - max(left_low[a], right_low[a]) for a in range(3)]
            if min(overlap) > 0.025 and math.prod(overlap) > 0.003:
                collisions.append({"components": [left_id, right_id], "overlap_m": [round(value, 4) for value in overlap]})
                if left_id == roots[0] and right_id == roots[1]:
                    root_collision_pairs.add(roots)
    return {
        "status": "failed" if outside or missing or support_failures else "passed_with_warnings" if collisions else "passed",
        "missing_geometry": missing,
        "support_failures": support_failures,
        "outside_room": outside,
        "aabb_collision_candidates": collisions,
        "collision_semantics": "AABB candidates are conservative; decorative rug overlap is intentionally excluded.",
    }


__all__ = ["judge_component_bounds", "solve_spatial_constraints"]
