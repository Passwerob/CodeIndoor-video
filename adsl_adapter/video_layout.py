"""Room box and object poses from the video (VLM judges, code derives).

The same split as scene_layout.py, which is the reason the original project
works: the model decides what only a model can see -- which wall the base
cabinets run along, how far along it the fridge stands, that the microwave
sits on the counter -- and everything that FOLLOWS from those facts is
derived here: x/y from wall + offset + depth, yaw from the wall's inward
normal (a carcass never faces its wall), z from the support's top, a built-in
sink recessed into its counter. A monocular phone video gives no metric
depth, so the room size is the model's estimate from objects of known size
(door 2.1 m, counter 0.9 m, fridge ~1.8 m); the layout is approximate and
the issues list says where it is shaky.

Frame: origin at the floor centre, +x = east, +y = north, +z up, where NORTH
is defined as the wall the camera faces in the first keyframe. Walls are
north (y = +D/2), south (y = -D/2), east (x = +W/2), west (x = -W/2). An
object's local -Y is its front; yaw 0 means it faces south (backs onto the
north wall).
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import vlm

WALLS = ("north", "south", "east", "west")
INWARD = {"north": (0.0, -1.0), "south": (0.0, 1.0), "east": (-1.0, 0.0), "west": (1.0, 0.0)}
WALL_CAB_BOTTOM = 1.45

LAYOUT_PROMPT = """The first image (F00_full) is the FIRST keyframe of ONE phone video walking through ONE room, at full resolution. The other images are contact sheets of all keyframes (frame ids F00, F01, ... on each tile).
Define the walls FROM F00_full: NORTH = the wall straight ahead of the camera in F00; WEST = the wall on the LEFT side of F00; EAST = the wall on the RIGHT side of F00; SOUTH = the wall behind the camera in F00. First write down what you see on the north, west and east walls in F00_full ("walls_in_f00"); then place everything else consistently with that, following the camera as it moves through the contact sheets.
Room facts so far: <<ROOM>>
Objects (id: category, size w x d x h in metres, support):
<<OBJECTS>>
Light fixtures: <<LIGHTS>>
Openings: <<OPENINGS>>

Produce the floor plan. Reason from objects of known size (a door is ~2.1 m tall, a kitchen counter ~0.9 m high, a base cabinet ~0.6 m deep) to estimate the room size.
For every object say which wall it stands against (its back to that wall) and where along it: offset_m = distance from the LEFT end of that wall, as seen standing inside the room FACING that wall, to the object's centre. gap_m = space between the object's back and the wall. Objects standing on another object: give "on" and the offset along the same wall. Wall-mounted objects: give bottom_m (height of their lower edge). Free-standing objects (not against a wall): give position_m [x, y] in metres, with x east and y north from the room centre, and facing (north|south|east|west).
For each light fixture give each fixture's position_m [x, y].
For openings give wall, offset_m (centre, measured the same way), width_m, height_m and sill_m.
Return a json object:
{"walls_in_f00": {"north": "...", "west": "...", "east": "..."},
 "room": {"width_m": <west-east>, "depth_m": <south-north>, "height_m": 0.0, "basis": "..."},
 "walls": {"north": "what runs along it", "south": "...", "east": "...", "west": "..."},
 "openings": [{"id": "...", "kind": "window|door", "wall": "...", "offset_m": 0.0, "width_m": 0.0, "height_m": 0.0, "sill_m": 0.0}],
 "objects": [{"id": "...", "wall": "north|south|east|west|null", "offset_m": 0.0, "gap_m": 0.0, "on": null, "bottom_m": null, "position_m": null, "facing": null}],
 "fixtures": [{"fixture": "<fixture id>", "positions_m": [[0.0, 0.0]]}],
 "notes": "..."}"""


def _num(v, default=None, lo=None, hi=None):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return default
    if lo is not None:
        x = max(lo, x)
    if hi is not None:
        x = min(hi, x)
    return x


def yaw_for_wall(wall: str) -> float:
    nx, ny = INWARD[wall]
    return round(math.degrees(math.atan2(nx, -ny)), 3)


def along(wall: str, offset: float, W: float, D: float) -> tuple[str, float]:
    """(axis, coordinate) of a point `offset` m from the wall's left end,
    left as seen from inside the room facing that wall."""
    return {"north": ("x", -W / 2 + offset), "south": ("x", W / 2 - offset),
            "east": ("y", D / 2 - offset), "west": ("y", -D / 2 + offset)}[wall]


def wall_length(wall: str, W: float, D: float) -> float:
    return W if wall in ("north", "south") else D


def footprint_half(dims, yaw: float) -> tuple[float, float]:
    w, d = dims[0] / 2, dims[1] / 2
    c, s = abs(math.cos(math.radians(yaw))), abs(math.sin(math.radians(yaw)))
    return w * c + d * s, w * s + d * c


def derive(raw: dict, objects: list[dict], dims: dict, fixtures: list[dict]) -> dict:
    """Poses from the model's plan. `dims`: object id -> [w, d, h] (spec first)."""
    room = raw.get("room") or {}
    W = _num(room.get("width_m"), 3.0, 1.2, 30.0)
    D = _num(room.get("depth_m"), 4.0, 1.2, 30.0)
    H = _num(room.get("height_m"), 2.7, 2.0, 6.0)
    issues: list[dict] = []
    by_id = {o["id"]: o for o in objects}
    plan = {str(p.get("id")): p for p in raw.get("objects") or [] if p.get("id")}
    poses: dict = {}

    def issue(kind, oid, msg):
        issues.append({"id": f"{kind}:{oid}", "message": msg})

    # supports first: a microwave needs the counter's pose
    order = sorted(objects, key=lambda o: 1 if (plan.get(o["id"]) or {}).get("on") or o.get("on") else 0)
    for o in order:
        oid = o["id"]
        p = plan.get(oid) or {}
        size = dims.get(oid) or o.get("size_guess_m") or [0.5, 0.5, 0.5]
        w, d, h = [float(v) for v in size]
        support = p.get("on") or o.get("on")
        support = support if support in by_id and support != oid else None
        wall = p.get("wall") if p.get("wall") in WALLS else None
        mount = o.get("mount")
        pose = {"id": oid, "dims": [w, d, h], "mount": mount, "wall": wall, "on": support}
        if wall:
            yaw = yaw_for_wall(wall)
            L = wall_length(wall, W, D)
            off = _num(p.get("offset_m"), L / 2, 0.0, L)
            axis, coord = along(wall, off, W, D)
            gap = _num(p.get("gap_m"), 0.0, 0.0, 1.0)
            nx, ny = INWARD[wall]
            plane = {"north": D / 2, "south": -D / 2, "east": W / 2, "west": -W / 2}[wall]
            depth_off = d / 2 + gap
            if support and support in poses and poses[support].get("wall") == wall:
                # on a counter along the same wall: centred on the counter's depth
                sd = poses[support]["dims"][1]
                depth_off = max(d / 2, sd / 2 + _num(poses[support].get("gap"), 0.0))
            if axis == "x":
                x, y = coord, plane + ny * depth_off
            else:
                x, y = plane + nx * depth_off, coord
            pose.update({"x": x, "y": y, "yaw_deg": yaw, "gap": gap})
        elif p.get("position_m") and len(p["position_m"]) >= 2:
            x, y = _num(p["position_m"][0], 0.0), _num(p["position_m"][1], 0.0)
            facing = p.get("facing") if p.get("facing") in WALLS else "south"
            yaw = {"south": 0.0, "north": 180.0, "east": 90.0, "west": -90.0}[facing]
            pose.update({"x": x, "y": y, "yaw_deg": yaw})
        elif support and support in poses:
            pose.update({"x": poses[support]["x"], "y": poses[support]["y"],
                         "yaw_deg": poses[support]["yaw_deg"]})
        else:
            pose.update({"x": 0.0, "y": 0.0, "yaw_deg": 0.0})
            issue("layout.unplaced", oid, "no wall or position given; placed at the centre")
        # height
        if support and support in poses:
            sp = poses[support]
            top = sp["z"] + sp["dims"][2]
            if sp.get("mount") == "built_in" and sp.get("on") in poses:
                base = poses[sp["on"]]
                top = base["z"] + base["dims"][2]
            pose["z"] = top - h if mount == "built_in" else top
            if mount != "built_in":
                hx, hy = footprint_half(sp["dims"], sp["yaw_deg"])
                if abs(pose["x"] - sp["x"]) > hx + 0.05 or abs(pose["y"] - sp["y"]) > hy + 0.05:
                    issue("layout.support", oid, f"centre is outside {support}'s top; moved onto it")
                    pose["x"] = min(max(pose["x"], sp["x"] - hx + w / 2), sp["x"] + hx - w / 2)
                    pose["y"] = min(max(pose["y"], sp["y"] - hy + d / 2), sp["y"] + hy - d / 2)
        elif mount == "wall":
            bottom = _num(p.get("bottom_m"), WALL_CAB_BOTTOM, 0.0, H - h)
            pose["z"] = bottom
        elif mount == "ceiling":
            pose["z"] = H - h
        else:
            pose["z"] = 0.0
        poses[oid] = pose

    _separate_along_walls(poses, W, D, issues)
    _resolve_corner_overlaps(poses, W, D, issues)
    for oid, pose in poses.items():
        hx, hy = footprint_half(pose["dims"], pose["yaw_deg"])
        if abs(pose["x"]) + hx > W / 2 + 0.02 or abs(pose["y"]) + hy > D / 2 + 0.02:
            issue("layout.bounds", oid, "extends past a wall; clamped inside")
            pose["x"] = max(-W / 2 + hx, min(W / 2 - hx, pose["x"]))
            pose["y"] = max(-D / 2 + hy, min(D / 2 - hy, pose["y"]))
        for k in ("x", "y", "z", "yaw_deg"):
            pose[k] = round(float(pose[k]), 3)
    _overlaps(poses, issues)

    openings = []
    for op in raw.get("openings") or []:
        wall = op.get("wall") if op.get("wall") in WALLS else None
        if not wall:
            continue
        L = wall_length(wall, W, D)
        width = _num(op.get("width_m"), 0.9, 0.3, L)
        off = _num(op.get("offset_m"), L / 2, width / 2, L - width / 2)
        kind = "door" if "door" in str(op.get("kind")).lower() else "window"
        height = _num(op.get("height_m"), 2.1 if kind == "door" else 1.2, 0.3, H)
        sill = 0.0 if kind == "door" else _num(op.get("sill_m"), 0.9, 0.0, H - height)
        openings.append({"id": str(op.get("id") or f"{kind}_{len(openings) + 1}"), "kind": kind,
                         "wall": wall, "offset_m": round(off, 3), "width_m": round(width, 3),
                         "height_m": round(height, 3), "sill_m": round(sill, 3)})

    lights = []
    known = {f.get("id") for f in fixtures}
    for fx in raw.get("fixtures") or []:
        fid = fx.get("fixture") if fx.get("fixture") in known else (
            next(iter(known)) if len(known) == 1 else fx.get("fixture"))
        for k, pos in enumerate(fx.get("positions_m") or []):
            try:
                x, y = float(pos[0]), float(pos[1])
            except (TypeError, ValueError, IndexError):
                continue
            lights.append({"id": f"{fid}_{k + 1}", "fixture": fid,
                           "x": round(max(-W / 2 + 0.1, min(W / 2 - 0.1, x)), 3),
                           "y": round(max(-D / 2 + 0.1, min(D / 2 - 0.1, y)), 3)})
    for fx in fixtures:                     # fixtures the plan forgot: spread them out
        if any(l["fixture"] == fx.get("id") for l in lights):
            continue
        n = int(fx.get("count") or 1)
        long_x = W >= D
        for k in range(n):
            t = (k + 0.5) / n - 0.5
            lights.append({"id": f"{fx.get('id')}_{k + 1}", "fixture": fx.get("id"),
                           "x": round(t * W * 0.8, 3) if long_x else 0.0,
                           "y": 0.0 if long_x else round(t * D * 0.8, 3)})
        issues.append({"id": f"layout.fixture:{fx.get('id')}",
                       "message": "no positions in the plan; spread along the long axis"})
    return {"room": {"width": round(W, 3), "depth": round(D, 3), "height": round(H, 3),
                     "basis": room.get("basis")},
            "walls": raw.get("walls") or {}, "openings": openings,
            "objects": poses, "lights": lights, "issues": issues, "notes": raw.get("notes")}


def _separate_along_walls(poses: dict, W: float, D: float, issues: list) -> None:
    """Floor objects on one wall must not overlap: push apart along the wall,
    keeping the order the video showed, and keep them on the wall."""
    for wall in WALLS:
        row = [p for p in poses.values() if p.get("wall") == wall and not p.get("on")
               and p.get("mount") in ("floor", None, "built_in")]
        if len(row) < 2:
            continue
        axis = "x" if wall in ("north", "south") else "y"
        L = wall_length(wall, W, D)
        row.sort(key=lambda p: p[axis])
        half = [p["dims"][0] / 2 for p in row]           # width runs along the wall
        for i in range(1, len(row)):
            need = row[i - 1][axis] + half[i - 1] + half[i]
            if row[i][axis] < need - 0.005:
                issues.append({"id": f"layout.overlap:{row[i]['id']}",
                               "message": f"overlapped {row[i - 1]['id']} along the {wall} wall "
                                          f"by {need - row[i][axis]:.2f} m; pushed along"})
                row[i][axis] = need
        over = row[-1][axis] + half[-1] - L / 2
        if over > 0.005:                                 # slid off the end: shift the row back
            for p in row:
                p[axis] -= over
        under = -L / 2 - (row[0][axis] - half[0])
        if under > 0.005:
            for p in row:
                p[axis] += under


def _plan_overlap(a: dict, b: dict) -> tuple[float, float]:
    ax, ay = footprint_half(a["dims"], a["yaw_deg"])
    bx, by = footprint_half(b["dims"], b["yaw_deg"])
    ox = min(a["x"] + ax, b["x"] + bx) - max(a["x"] - ax, b["x"] - bx)
    oy = min(a["y"] + ay, b["y"] + by) - max(a["y"] - ay, b["y"] - by)
    return ox, oy


def _resolve_corner_overlaps(poses: dict, W: float, D: float, issues: list) -> None:
    """Objects on two different walls that collide in a corner.

    The pantry put the bin on the south wall right where the fridge stands on
    the east wall, so the bin rendered INSIDE the fridge. Along-wall
    separation only sees one wall at a time; here the smaller object slides
    along its own wall, away from the larger one, until they are clear.
    """
    floor = [p for p in poses.values() if p.get("mount") in ("floor", None) and not p.get("on")
             and p.get("wall")]
    for _ in range(3):
        moved = False
        for i in range(len(floor)):
            for j in range(i + 1, len(floor)):
                a, b = floor[i], floor[j]
                if a["wall"] == b["wall"]:
                    continue
                ox, oy = _plan_overlap(a, b)
                if ox <= 0.01 or oy <= 0.01:
                    continue
                small, big = sorted((a, b), key=lambda p: p["dims"][0] * p["dims"][1])
                axis = "x" if small["wall"] in ("north", "south") else "y"
                push = (ox if axis == "x" else oy) + 0.02
                direction = 1.0 if small[axis] >= big[axis] else -1.0
                half = small["dims"][0] / 2
                limit = (W if axis == "x" else D) / 2 - half
                target = small[axis] + direction * push
                if abs(target) > limit:                     # no room that way: go the other way
                    target = small[axis] - direction * (push + (big["dims"][0] if axis == "x" else 0))
                    target = max(-limit, min(limit, target))
                issues.append({"id": f"layout.corner:{small['id']}",
                               "message": f"{small['id']} ({small['wall']} wall) collided with "
                                          f"{big['id']} ({big['wall']} wall); slid "
                                          f"{target - small[axis]:+.2f} m along its wall"})
                small[axis] = target
                moved = True
        if not moved:
            break


def _overlaps(poses: dict, issues: list) -> None:
    items = [p for p in poses.values() if p.get("mount") in ("floor", None) and not p.get("on")]
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            a, b = items[i], items[j]
            ax, ay = footprint_half(a["dims"], a["yaw_deg"])
            bx, by = footprint_half(b["dims"], b["yaw_deg"])
            ox = min(a["x"] + ax, b["x"] + bx) - max(a["x"] - ax, b["x"] - bx)
            oy = min(a["y"] + ay, b["y"] + by) - max(a["y"] - ay, b["y"] - by)
            if ox > 0.03 and oy > 0.03:
                issues.append({"id": f"layout.collision:{a['id']}:{b['id']}",
                               "message": f"{a['id']} and {b['id']} overlap by "
                                          f"{ox:.2f} x {oy:.2f} m in plan"})


def wall_segments(wall: str, W: float, D: float, H: float, openings: list[dict],
                  t: float = 0.12) -> dict:
    """Solid boxes of one wall with its openings cut out, plus the openings.

    Pure geometry, shared with the Blender builder so it can be tested here:
    returns {"solids": [(lo, hi)], "holes": [{"opening", "lo", "hi"}]} in room
    coordinates. Walls are built OUTSIDE the interior box, so the interior
    dimensions are exactly W x D x H.
    """
    axis = "x" if wall in ("north", "south") else "y"
    L = wall_length(wall, W, D)
    lo_u, hi_u = -L / 2 - t, L / 2 + t           # walls overlap at the corners
    cuts = []
    for op in openings:
        if op.get("wall") != wall:
            continue
        _, c = along(wall, float(op["offset_m"]), W, D)
        a, b = c - op["width_m"] / 2, c + op["width_m"] / 2
        z0 = float(op.get("sill_m") or 0.0)
        cuts.append((max(a, -L / 2), min(b, L / 2), z0, min(H, z0 + op["height_m"]), op))
    cuts.sort(key=lambda c: c[0])
    rects, holes, u = [], [], lo_u
    for a, b, z0, z1, op in cuts:
        if a > u:
            rects.append((u, a, 0.0, H))
        if z0 > 0:
            rects.append((a, b, 0.0, z0))
        if z1 < H:
            rects.append((a, b, z1, H))
        holes.append((a, b, z0, z1, op))
        u = max(u, b)
    if u < hi_u:
        rects.append((u, hi_u, 0.0, H))
    plane = {"north": D / 2, "south": -D / 2, "east": W / 2, "west": -W / 2}[wall]
    outward = 1.0 if wall in ("north", "east") else -1.0
    n0, n1 = sorted((plane, plane + outward * t))

    def box(u0, u1, z0, z1, n_lo=n0, n_hi=n1):
        if axis == "x":
            return [u0, n_lo, z0], [u1, n_hi, z1]
        return [n_lo, u0, z0], [n_hi, u1, z1]

    return {"solids": [box(*r) for r in rects if r[1] - r[0] > 1e-3 and r[3] - r[2] > 1e-3],
            "holes": [{"opening": h[4], "lo": box(*h[:4])[0], "hi": box(*h[:4])[1]}
                      for h in holes],
            "axis": axis, "plane": plane, "outward": outward}


def splashbacks(layout: dict, materials: dict) -> list[dict]:
    """Tile zones behind base-cabinet runs, from the counter top up to the wall
    cabinets on the same wall (or 0.6 m), when a tile material was observed."""
    tile = next((m for m in materials.values() if m.get("class") == "tile"
                 or str(m.get("key", "")).startswith("tile:")), None)
    if not tile:
        return []
    out = []
    objs = layout["objects"]
    for o in objs.values():
        text = o["id"].lower()
        if not o.get("wall") or o.get("mount") not in ("floor", "built_in") or not (
                "base" in text or "counter" in text or "lower" in text):
            continue
        top = o["z"] + o["dims"][2]
        upper = [u for u in objs.values() if u.get("wall") == o["wall"] and u.get("mount") == "wall"]
        z1 = min([u["z"] for u in upper if u["z"] > top + 0.2] or [top + 0.6])
        axis = "x" if o["wall"] in ("north", "south") else "y"
        half = o["dims"][0] / 2
        c = o[axis]
        out.append({"wall": o["wall"], "axis": axis, "from": round(c - half, 3),
                    "to": round(c + half, 3), "z0": round(top, 3), "z1": round(z1, 3),
                    "material": tile["key"], "behind": o["id"]})
    return out


def run_layout(run: Path, inv: dict, sheets: list[dict], specs: dict, appearance: dict,
               refresh: bool = False) -> dict:
    dims = {oid: [s["dimensions_m"][k] for k in ("width", "depth", "height")]
            for oid, s in specs.items()}
    objs = "\n".join(
        f"- {o['id']}: {o['category']}, "
        + (" x ".join(f"{v:.2f}" for v in (dims.get(o['id']) or o.get('size_guess_m') or [])) or "?")
        + f", mount {o['mount']}" + (f", on {o['on']}" if o.get("on") else "")
        for o in inv["objects"])
    fixtures = appearance.get("lighting", {}).get("fixtures") or []
    lights = "; ".join(f"{f.get('id')}: {f.get('count')} x {f.get('type')} ({f.get('arrangement', '')})"
                       for f in fixtures) or "none"
    room = inv.get("room") or {}
    prompt = (LAYOUT_PROMPT
              .replace("<<ROOM>>", f"{room.get('type')}, {room.get('shape')}, size guess "
                                   f"{room.get('size_guess_m')} ({room.get('size_basis', '')})")
              .replace("<<OBJECTS>>", objs).replace("<<LIGHTS>>", lights)
              .replace("<<OPENINGS>>", json.dumps(room.get("openings") or [], ensure_ascii=False)))
    meta = json.loads((run / "frames" / "frames.json").read_text(encoding="utf-8"))
    first = run / "frames" / meta["frames"][0]["file"]
    # Orientation comes from ONE full frame: asked to define north from the
    # contact sheets alone, the model rotated the room by 90 degrees.
    images = [("F00_full", first)] + [(f"sheet_{i}", run / "sheets" / s["file"])
                                     for i, s in enumerate(sheets)]
    ans = vlm.ask_json(prompt, images, stage="layout", cache_dir=run / "vlm_calls",
                       refresh=refresh, max_tokens=5000, max_side=2000)
    layout = derive(ans["data"], inv["objects"], dims, fixtures)
    layout["walls_in_f00"] = ans["data"].get("walls_in_f00")
    layout["vlm"] = ans["meta"]
    layout["raw_plan"] = ans["data"]
    (run / "layout.json").write_text(json.dumps(layout, ensure_ascii=False, indent=2),
                                     encoding="utf-8")
    return layout
