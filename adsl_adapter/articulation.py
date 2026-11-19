"""Structure: which parts move, about which axis, and how far (pure Python).

The representation follows SceneCode's own articulation contract
(sources/scenecode/code_object/agent_framework/prompts/urdf.md): +X right,
-Y front, +Z up; joints are revolute / prismatic / continuous; positive
angles follow the right-hand rule; every joint's current state is 0, i.e.
the object is modelled CLOSED. A `press` joint (a push button) is a prismatic
joint with a few millimetres of travel.

Who decides what
----------------
The video reading (functional_spec 'joints') says which parts move, what kind
of motion it is, which EDGE of the moving part carries the hinge, which way
it opens and how far -- facts visible in the footage (hinges, the handle on
the opposite edge, the door swinging in a frame of the video).

It does NOT give pivot coordinates or the sign of the angle, and must not:
those follow from where the program actually put the door. So they are
derived here from the built part boxes ("derive, don't decide"), the sign is
chosen so the free edge swings toward `opens_toward`, and a swing test then
moves the part to its open limit and reports when it would pass through the
rest of the object -- the typical symptom of a hinge put on the wrong edge.

Edges are named in the OBJECT frame, independent of which face the part is
on: left = -X, right = +X, front = -Y, back = +Y, top = +Z, bottom = -Z
(left/right as seen standing in front of the object).
"""
from __future__ import annotations

import math
import re

DIRS = {"front": (0.0, -1.0, 0.0), "back": (0.0, 1.0, 0.0),
        "left": (-1.0, 0.0, 0.0), "right": (1.0, 0.0, 0.0),
        "up": (0.0, 0.0, 1.0), "down": (0.0, 0.0, -1.0),
        "top": (0.0, 0.0, 1.0), "bottom": (0.0, 0.0, -1.0)}
FACE_AXIS = {"front": 1, "back": 1, "left": 0, "right": 0, "top": 2, "bottom": 2}
EDGE_AXIS = {"left": 0, "right": 0, "front": 1, "back": 1, "top": 2, "bottom": 2}
EDGE_SIDE = {"left": 0, "right": 1, "front": 0, "back": 1, "top": 1, "bottom": 0}
FACE_SIDE = {"front": 0, "back": 1, "left": 0, "right": 1, "top": 1, "bottom": 0}
JOINT_TYPES = ("revolute", "prismatic", "continuous", "press")
DEFAULT_RANGE = {"revolute": 100.0, "continuous": 360.0, "press": 0.003}


# --------------------------------------------------------------------------
# part grouping
# --------------------------------------------------------------------------
def instance_groups(names: list[str], pid: str, count: int = 1) -> list[list[str]]:
    """Program parts belonging to each instance of spec part `pid`.

    Naming contract (stated in the forge prompt): a single part is `pid` or
    pieces `pid_<piece>`; counted parts are `pid_1..pid_n`, with pieces
    `pid_<k>_<piece>`. Returns one list per instance found (possibly empty).
    """
    if count <= 1:
        group = [n for n in names if n == pid or n.startswith(pid + "_")]
        return [group]
    groups = []
    for k in range(1, count + 1):
        head = f"{pid}_{k}"
        groups.append([n for n in names if n == head or n.startswith(head + "_")])
    return groups


def numbered_instances(names: list[str], pid: str) -> list[int]:
    """Instance numbers present for `pid` (pid_1, pid_2_frame -> [1, 2])."""
    found = set()
    pat = re.compile(r"^" + re.escape(pid) + r"_(\d+)(?:_|$)")
    for n in names:
        m = pat.match(n)
        if m:
            found.add(int(m.group(1)))
    return sorted(found)


def union(parts: list[dict]) -> tuple[list[float], list[float]] | None:
    if not parts:
        return None
    lo = [min(p["min"][i] for p in parts) for i in range(3)]
    hi = [max(p["max"][i] for p in parts) for i in range(3)]
    return lo, hi


# --------------------------------------------------------------------------
# geometry helpers
# --------------------------------------------------------------------------
def _rotate(point, pivot, axis_index: int, angle: float):
    p = [point[i] - pivot[i] for i in range(3)]
    c, s = math.cos(angle), math.sin(angle)
    i, j = [(1, 2), (2, 0), (0, 1)][axis_index]     # right-hand rule about x/y/z
    q = list(p)
    q[i] = c * p[i] - s * p[j]
    q[j] = s * p[i] + c * p[j]
    return [q[k] + pivot[k] for k in range(3)]


def _samples(lo, hi, n: int = 3, shrink: float = 0.1):
    pts = []
    for a in range(n):
        for b in range(n):
            for c in range(n):
                t = [(a + 0.5) / n, (b + 0.5) / n, (c + 0.5) / n]
                pts.append([lo[k] + (hi[k] - lo[k]) * (shrink + (1 - 2 * shrink) * t[k])
                            for k in range(3)])
    return pts


def _inside(pt, part, margin: float = 0.006) -> bool:
    return all(part["min"][k] + margin < pt[k] < part["max"][k] - margin for k in range(3))


# --------------------------------------------------------------------------
# derivation
# --------------------------------------------------------------------------
def range_magnitude(value, default: float) -> float:
    """|range| from a number, a "0-110" string or a [lo, hi] pair."""
    if isinstance(value, (list, tuple)):
        nums = []
        for v in value:
            try:
                nums.append(abs(float(v)))
            except (TypeError, ValueError):
                pass
        return max(nums) if nums else default
    if isinstance(value, str):
        found = re.findall(r"[-+]?\d*\.?\d+", value)
        return max((abs(float(f)) for f in found), default=default)
    try:
        return abs(float(value)) if value is not None else default
    except (TypeError, ValueError):
        return default


def _norm_choice(value, allowed, default):
    value = str(value or "").strip().lower()
    return value if value in allowed else default


def derive_joint(joint: dict, parts: list[dict], spec_parts: dict | None = None,
                 envelope: tuple | None = None, instance: int | None = None) -> dict:
    """Turn one spec joint into a concrete joint on the built parts.

    `parts` are the program's parts in the object frame:
    [{"name", "min": [x,y,z], "max": [x,y,z]}]. `spec_parts` maps part id ->
    spec part (for counts, faces and parent links). Returns a dict with
    origin/axis/limits and an `issues` list; `parts` empty means the moving
    part was not found and nothing can be derived.
    """
    spec_parts = spec_parts or {}
    jtype = _norm_choice(joint.get("type"), JOINT_TYPES, "revolute")
    names = [p["name"] for p in parts]
    moving_ids = [m for m in (joint.get("moving_parts") or []) if m]
    # Parts whose spec parent is a moving part ride along (a handle on a door),
    # and so do their children (the PUSH label on the flap on the lid).
    riders: list[str] = []
    carried = set(moving_ids)
    while True:
        new = [pid for pid, sp in spec_parts.items()
               if sp.get("parent") in carried and pid not in carried]
        if not new:
            break
        riders += new
        carried |= set(new)
    group: list[str] = []
    for pid in moving_ids + riders:
        count = int((spec_parts.get(pid) or {}).get("count") or 1)
        groups = instance_groups(names, pid, count)
        if instance is not None and count > 1:
            groups = groups[instance - 1: instance] if instance <= len(groups) else [[]]
        for g in groups:
            group.extend(n for n in g if n not in group)
    jid = joint.get("id") or ("joint_" + "_".join(moving_ids))
    if instance is not None:
        jid = f"{jid}_{instance}"
    out = {"id": jid, "type": jtype, "parts": group, "moving_parts": moving_ids,
           "function": joint.get("function"), "source": "spec", "issues": []}
    if not group:
        out["issues"].append(f"no program part named after {moving_ids} -- the "
                             f"moving part must be a separate, named part")
        return out
    gparts = [p for p in parts if p["name"] in group]
    lo, hi = union(gparts)
    mid = [(lo[k] + hi[k]) / 2 for k in range(3)]
    face = _norm_choice(joint.get("face") or (spec_parts.get(moving_ids[0]) or {}).get("face"),
                        FACE_AXIS, "front")
    toward = _norm_choice(joint.get("opens_toward"), DIRS, face if face in DIRS else "front")
    rng = range_magnitude(joint.get("range_max", joint.get("range")),
                          DEFAULT_RANGE.get(jtype, 0.0))

    if jtype in ("prismatic", "press"):
        direction = DIRS[toward]
        if jtype == "press":
            # a button travels INTO its face
            n = DIRS.get(face, (0.0, -1.0, 0.0))
            direction = tuple(-c for c in n)
            rng = rng if 0 < rng <= 0.02 else 0.003
        elif rng <= 0 or rng > 3.0:
            ax = max(range(3), key=lambda k: abs(direction[k]))
            extent = (envelope[ax] if envelope else (hi[ax] - lo[ax]))
            rng = round(0.65 * extent, 3)
        axis_index = max(range(3), key=lambda k: abs(direction[k]))
        sign = 1.0 if direction[axis_index] > 0 else -1.0
        out.update({"origin": [round(v, 4) for v in mid], "axis_index": axis_index,
                    "axis": [float(k == axis_index) for k in range(3)],
                    "unit": "m", "range": rng, "sign": sign,
                    "lower": min(0.0, sign * rng), "upper": max(0.0, sign * rng),
                    "open_value": sign * rng, "opens_toward": toward, "face": face})
        return out

    # revolute / continuous: pivot on the hinge edge, in the face plane
    edge = _norm_choice(joint.get("hinge_edge") or joint.get("pivot"),
                        set(EDGE_AXIS) | {"center"}, None)
    # an explicit pivot point (object frame, metres) wins: a tap lever turns on
    # its cartridge, a spout on the column axis -- not about their own boxes
    explicit_pivot = joint.get("pivot_local")
    face_axis = FACE_AXIS[face]
    pivot = list(mid)
    if edge in EDGE_AXIS and EDGE_AXIS[edge] != face_axis:
        e_axis = EDGE_AXIS[edge]
        pivot[e_axis] = (lo, hi)[EDGE_SIDE[edge]][e_axis]
        pivot[face_axis] = (lo, hi)[FACE_SIDE[face]][face_axis]
        axis_index = ({0, 1, 2} - {face_axis, e_axis}).pop()
    else:
        if edge not in (None, "center"):
            out["issues"].append(f"hinge edge '{edge}' lies in the plane of face "
                                 f"'{face}'; using the part centre")
        axis_index = face_axis if jtype == "continuous" else 0
    explicit = str(joint.get("axis") or "").strip().lower()
    if explicit in ("x", "y", "z"):
        axis_index = "xyz".index(explicit)
    if isinstance(explicit_pivot, (list, tuple)) and len(explicit_pivot) == 3:
        pivot = [float(v) for v in explicit_pivot]
    if jtype == "continuous":
        rng = rng if rng > 0 else 360.0
    rad = math.radians(min(rng, 360.0))

    # sign: the free side must move toward `opens_toward`
    want = DIRS[toward]
    probe = _rotate(mid, pivot, axis_index, math.radians(30.0))
    disp = [probe[k] - mid[k] for k in range(3)]
    dot = sum(disp[k] * want[k] for k in range(3))
    sign = 1.0 if dot >= 0 else -1.0
    if abs(dot) < 1e-9 and jtype == "revolute":
        out["issues"].append("cannot tell which way it opens from this hinge and "
                             "direction; check hinge_edge/opens_toward")
    out.update({"origin": [round(v, 4) for v in pivot], "axis_index": axis_index,
                "axis": [float(k == axis_index) for k in range(3)], "unit": "rad",
                "range": rad, "sign": sign, "lower": min(0.0, sign * rad),
                "upper": max(0.0, sign * rad), "open_value": sign * rad,
                "opens_toward": toward, "face": face, "hinge_edge": edge or "center"})
    if jtype == "revolute":
        hit = swing_collision(out, parts)
        if hit:
            out["issues"].append(hit)
    return out


def swing_collision(joint: dict, parts: list[dict], max_angle: float | None = None) -> str | None:
    """Rotate the moving group to its open limit; report what it passes through.

    Samples the group's box (27 interior points), rotates them about the
    hinge, and counts how many land inside any part that does not move with
    it. A hinge on the wrong edge, or a sign that swings the door into its own
    carcass, puts most samples inside the body.
    """
    group = set(joint.get("parts") or [])
    gparts = [p for p in parts if p["name"] in group]
    others = [p for p in parts if p["name"] not in group
              and min(p["max"][k] - p["min"][k] for k in range(3)) > 0.004]
    if not gparts or not others:
        return None
    lo, hi = union(gparts)
    limit = joint["open_value"] if max_angle is None else max_angle
    samples = _samples(lo, hi)
    worst, worst_name, worst_angle = 0, None, limit
    # The whole sweep, not just the end pose: a door opened the wrong way can
    # pass straight through the carcass and come out beside it at 110 deg.
    for frac in (0.25, 0.5, 0.75, 1.0):
        angle = limit * frac
        pts = [_rotate(p, joint["origin"], joint["axis_index"], angle) for p in samples]
        for other in others:
            n = sum(1 for p in pts if _inside(p, other))
            if n > worst:
                worst, worst_name, worst_angle = n, other["name"], angle
    if worst >= 5:            # > ~20 % of the moving part ends up inside something
        return (f"opening to {math.degrees(worst_angle):+.0f} deg swings the part "
                f"through '{worst_name}' ({worst}/27 samples inside) -- hinge on the "
                f"wrong edge, or the part overlaps the body")
    return None


def derive_all(spec: dict, parts: list[dict], envelope: tuple | None = None) -> list[dict]:
    """Every joint in the spec, expanded per instance for counted parts."""
    spec_parts = {p["id"]: p for p in spec.get("parts") or []}
    out = []
    for joint in spec.get("joints") or []:
        moving = [m for m in joint.get("moving_parts") or [] if m]
        count = max([int((spec_parts.get(m) or {}).get("count") or 1) for m in moving] or [1])
        if count > 1:
            for k in range(1, count + 1):
                out.append(derive_joint(joint, parts, spec_parts, envelope, instance=k))
        else:
            out.append(derive_joint(joint, parts, spec_parts, envelope))
    return out
