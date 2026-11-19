"""Sim2real usability of a forged object: can a hand or a gripper operate it?

A model that looks right can still be useless in simulation: a microwave
handle 13 mm proud of the door (accepted by every visual check) cannot be
grasped, so the door never opens. These checks run on the program's part
boxes in the object frame (front = -Y, origin bottom centre), the same list
spec_check uses, so they fail the dry run before anything is rendered.

Rules (hard):
* every door / drawer / lid that a joint moves must offer a GRASP FEATURE:
  - a handle / pull / knob moving with it that stands >= HANDLE_PROUD proud
    of the panel's outer face and is >= HANDLE_LEN long (knob: >= KNOB_DIAM
    across), or
  - a FINGER GAP: >= FINGER_GAP of free space beyond one edge of the panel
    (its free edge or its top edge -- a J-pull / handle-less door), with the
    panel at least PANEL_THICK thick so the edge can be pinched, or
  - a push latch: a press joint that moves the panel or a part named *latch*.
* grasp hardware is exempt from the envelope by up to HARDWARE_ALLOWANCE
  (the envelope is measured from the body; handles are too thin to measure).
"""
from __future__ import annotations

HANDLE_PROUD = 0.025
HANDLE_LEN = 0.06
KNOB_DIAM = 0.02
FINGER_GAP = 0.020
PANEL_THICK = 0.015
HARDWARE_ALLOWANCE = 0.06
GRASP_WORDS = ("handle", "pull", "knob", "grip")
OPERABLE_KINDS = {"door", "drawer", "lid"}
FACE_NORMAL = {"front": (1, -1), "back": (1, 1), "left": (0, -1), "right": (0, 1),
               "top": (2, 1), "bottom": (2, -1)}


CHANNEL_WORDS = ("channel", "groove", "recess")
GRIP_DEPTH, GRIP_WIDTH, GRIP_LEN = 0.015, 0.015, 0.08


def is_grip_channel(name: str) -> bool:
    """A hidden grip: a channel recessed into the panel (fridge, handle-less fronts)."""
    n = name.lower()
    return any(w in n for w in CHANNEL_WORDS)


def is_grasp_hardware(name: str) -> bool:
    n = name.lower()
    return any(w in n for w in GRASP_WORDS) and "boss" not in n and not is_grip_channel(n)


FLEXIBLE_WORDS = ("cord", "cable", "hose")


def is_flexible(name: str) -> bool:
    """A power cord or hose hangs wherever it falls: it is not part of the rigid
    body the measured box (and the envelope) describe."""
    n = name.lower()
    return any(w in n for w in FLEXIBLE_WORDS)


def body_union(parts: list[dict]):
    """Box of everything that is not grasp hardware or a flexible cord (what the envelope bounds)."""
    body = [p for p in parts if not is_grasp_hardware(p["name"]) and not is_flexible(p["name"])] or parts
    lo = [min(p["min"][k] for p in body) for k in range(3)]
    hi = [max(p["max"][k] for p in body) for k in range(3)]
    return lo, hi


def hardware_overshoot(parts: list[dict], P: dict) -> list[tuple[str, float]]:
    """Grasp hardware sticking out of the envelope by more than the allowance."""
    W, D, H = float(P["width"]), float(P["depth"]), float(P["height"])
    bounds = ((-W / 2, W / 2), (-D / 2, D / 2), (0.0, H))
    out = []
    for p in parts:
        if not is_grasp_hardware(p["name"]):
            continue
        worst = max(max(bounds[i][0] - p["min"][i], p["max"][i] - bounds[i][1]) for i in range(3))
        if worst > HARDWARE_ALLOWANCE:
            out.append((p["name"], worst))
    return out


def _panel(group: list[dict], axis: int):
    """The moving group's main panel: largest area across the face normal."""
    def area(p):
        e = [p["max"][k] - p["min"][k] for k in range(3)]
        return e[(axis + 1) % 3] * e[(axis + 2) % 3]
    cands = [p for p in group if not is_grasp_hardware(p["name"])] or group
    return max(cands, key=area)


def _slot_free(panel: dict, n_axis: int, n_sign: int, k: int, s: int, static: list[dict],
               gap: float = None, hook: float = 0.010) -> bool:
    """Is there room for fingers at one edge of the panel? The slot runs `gap`
    beyond the edge (along axis k, direction s), spans the panel's other in-plane
    extent, and reaches from the panel's outer face to `hook` behind its back
    face -- fingertips must get BEHIND the edge to pull it. Along the edge axis
    the slot also covers the last `hook` of the panel itself, so a carcass right
    behind the door's edge blocks it."""
    gap = FINGER_GAP if gap is None else gap
    lo, hi = list(panel["min"]), list(panel["max"])
    if s > 0:                            # from 10 mm inside the edge (fingertips behind it) to `gap` beyond
        lo[k], hi[k] = panel["max"][k] - hook, panel["max"][k] + gap
    else:
        lo[k], hi[k] = panel["min"][k] - gap, panel["min"][k] + hook
    if n_sign < 0:                       # outer face at min: behind = +n
        hi[n_axis] = panel["max"][n_axis] + hook
    else:
        lo[n_axis] = panel["min"][n_axis] - hook
    for q in static:
        if all(q["max"][a] > lo[a] + 0.001 and q["min"][a] < hi[a] - 0.001 for a in range(3)):
            return False
    return True


def grasp_issues(spec: dict, parts: list[dict], joints: list[dict]) -> list[dict]:
    """One hard violation per operable panel that offers no grasp feature."""
    kinds = {p["id"]: p.get("kind") for p in spec.get("parts") or []}
    by_name = {p["name"]: p for p in parts}
    latch_moves = set()
    for j in joints:
        if j.get("type") == "press":
            latch_moves.update(j.get("moving_parts") or [])
            latch_moves.update(n for n in j.get("parts") or [] if "latch" in n.lower())
    out = []
    for j in joints:
        if j.get("type") not in ("revolute", "prismatic") or not j.get("parts"):
            continue
        moving_kinds = {kinds.get(m) for m in j.get("moving_parts") or []}
        if not moving_kinds & OPERABLE_KINDS:
            continue
        group = [by_name[n] for n in j["parts"] if n in by_name]
        if not group:
            continue
        face = j.get("face") or "front"
        axis, sgn = FACE_NORMAL.get(face, (1, -1))
        panel = _panel(group, axis)
        outer = panel["min"][axis] if sgn < 0 else panel["max"][axis]
        if set(j.get("moving_parts") or []) & latch_moves or any("latch" in n.lower() for n in j["parts"]):
            continue
        is_lid = face in ("top", "bottom")
        # 0. hardware must sit on its panel: a fridge pull ran 4 cm past the door
        #    and out over the top of the cabinet (a lid's lift tab may overhang)
        for p in group:
            if not is_grasp_hardware(p["name"]) or is_lid:
                continue
            over = max(max(panel["min"][k] - p["min"][k], p["max"][k] - panel["max"][k])
                       for k in range(3) if k != axis)
            if over > 0.01:
                out.append({"severity": "hard", "id": f"sim2real.hardware_overhang:{p['name']}",
                            "message": f"'{p['name']}' runs {over * 1000:.0f} mm past the edge of the panel it "
                                       f"is mounted on ('{panel['name']}'); keep handles and their standoffs "
                                       f"within the panel's face (inset >= 20 mm from its edges)"})
        # 1. handle / knob riding on the panel
        best_handle = (0.0, None, 0.0)
        for p in group:
            if not is_grasp_hardware(p["name"]):
                continue
            proud = (outer - p["min"][axis]) if sgn < 0 else (p["max"][axis] - outer)
            ext = sorted(p["max"][k] - p["min"][k] for k in range(3) if k != axis)
            length, width = ext[1], ext[0]
            ok = proud >= HANDLE_PROUD and (length >= HANDLE_LEN or
                                            ("knob" in p["name"].lower() and width >= KNOB_DIAM
                                             and proud >= 0.02))
            if ok:
                best_handle = None
                break
            if proud > best_handle[0]:
                best_handle = (proud, p["name"], length)
        if best_handle is None:
            continue
        # 1a. a hidden grip: a channel part moving with the panel, recessed
        #     >= GRIP_DEPTH behind the panel's outer face, open in front (no other
        #     part of the group covers it), wide and long enough for fingers
        channel_ok, channel_notes = False, []
        for p in group:
            if not is_grip_channel(p["name"]) or any(w in p["name"].lower() for w in ("lip", "edge", "trim")):
                continue
            outer_g = p["min"][axis] if sgn < 0 else p["max"][axis]
            depth = (outer_g - outer) if sgn < 0 else (outer - outer_g)
            ext = sorted(p["max"][k] - p["min"][k] for k in range(3) if k != axis)
            front_lo, front_hi = list(p["min"]), list(p["max"])
            if sgn < 0:
                front_lo[axis], front_hi[axis] = outer, outer_g
            else:
                front_lo[axis], front_hi[axis] = outer_g, outer
            covered = any(q is not p and all(q["max"][a] > front_lo[a] + 0.001 and q["min"][a] < front_hi[a] - 0.001
                                              for a in range(3)) for q in group)
            if depth >= GRIP_DEPTH and ext[0] >= GRIP_WIDTH and ext[1] >= GRIP_LEN and not covered:
                channel_ok = True
                break
            why = []
            if depth < GRIP_DEPTH:
                why.append(f"its face is only {max(depth, 0) * 1000:.0f} mm behind the panel face (needs >= 15)")
            if ext[0] < GRIP_WIDTH:
                why.append(f"it is {ext[0] * 1000:.0f} mm wide (needs >= 15)")
            if ext[1] < GRIP_LEN:
                why.append(f"it is {ext[1] * 1000:.0f} mm long (needs >= 80)")
            if covered:
                cov = [q["name"] for q in group if q is not p and all(
                    q["max"][a] > front_lo[a] + 0.001 and q["min"][a] < front_hi[a] - 0.001 for a in range(3))]
                why.append(f"'{cov[0]}' covers it in front -- the panel must be split around the channel")
            channel_notes.append(f"'{p['name']}': " + "; ".join(why))
        if channel_ok:
            continue
        # 1b. a lid is lifted by a tab / lip that fingers can get UNDER: free space
        #     of >= 15 mm below the grasp part (outside the body it closes)
        if is_lid:
            static_ = [q for q in parts if q["name"] not in j["parts"]]
            lifted = False
            for p in group:
                if not is_grasp_hardware(p["name"]) and p is not panel:
                    continue
                for k in range(3):
                    if k == axis:
                        continue
                    for s_ in (-1, 1):
                        if p is panel and not is_grasp_hardware(p["name"]):
                            # the lid's own rim: overhang beyond the body, with room below
                            if _slot_free(panel, axis, sgn, k, s_, static_, gap=0.0, hook=0.015):
                                lifted = True
                lo_, hi_ = list(p["min"]), list(p["max"])
                hi_[axis], lo_[axis] = p["min"][axis], p["min"][axis] - 0.015
                if is_grasp_hardware(p["name"]) and not any(
                        all(q["max"][a] > lo_[a] + 0.001 and q["min"][a] < hi_[a] - 0.001 for a in range(3))
                        for q in static_):
                    lifted = True
            if lifted:
                continue
        # 2. finger gap beyond an edge of the panel (edges in the face plane)
        static = [p for p in parts if p["name"] not in j["parts"]]
        thick = panel["max"][axis] - panel["min"][axis]
        free_edges = [f"{'-+'[s_ > 0]}{'xyz'[k]}" for k in range(3) if k != axis for s_ in (-1, 1)
                      if _slot_free(panel, axis, sgn, k, s_, static)]
        if free_edges and thick >= PANEL_THICK:
            continue
        name = panel["name"]
        if channel_notes:
            out.append({"severity": "hard", "id": f"sim2real.grip_channel:{j['id']}",
                        "message": f"the hidden grip of '{name}' is not a usable recess: " + " | ".join(channel_notes) +
                                   f". Build it like this: make '{name}' two slabs with a 30 mm GAP along the grip "
                                   f"edge (the gap runs the channel's length), and put the channel part in that gap "
                                   f"with its front face 18-20 mm BEHIND the panel face (a dark back wall), so fingers "
                                   f"can hook in. Both slabs and the channel move with the joint."})
            continue
        h_txt = (f"best handle '{best_handle[1]}' is {best_handle[0] * 1000:.0f} mm proud, "
                 f"{best_handle[2] * 1000:.0f} mm long" if best_handle[1] else "no handle/pull/knob moves with it")
        g_txt = (f"no edge has a {FINGER_GAP * 1000:.0f} mm finger slot reaching behind the panel"
                 if not free_edges else f"the panel is only {thick * 1000:.0f} mm thick to pinch")
        out.append({"severity": "hard", "id": f"sim2real.grasp:{j['id']}",
                    "message": f"'{name}' ({'/'.join(sorted(k for k in moving_kinds if k))}, joint {j['id']}) "
                               f"cannot be grasped by a hand or a gripper: {h_txt}; {g_txt}. Give it a real "
                               f"grasp feature: a handle/pull named '<panel>_handle' that moves with it, stands "
                               f"{HANDLE_PROUD * 1000:.0f}-45 mm proud of its outer face with a finger gap "
                               f"behind the grip (two standoffs + a bar) and is >= {HANDLE_LEN * 1000:.0f} mm "
                               f"long; or, if the video shows a HIDDEN grip, a channel part named '<panel>_grip_channel' "
                               f"moving with it, >= 15 mm wide, >= 80 mm long, its face >= 15 mm behind the panel's "
                               f"face and open in front (split the panel around it); or, if the video shows no handle, "
                               f"a >= {FINGER_GAP * 1000:.0f} mm finger "
                               f"slot along its top or free edge that also reaches 10 mm behind the panel "
                               f"(J-pull: recess the rail / carcass edge behind it), panel >= "
                               f"{PANEL_THICK * 1000:.0f} mm thick. Grasp hardware may stick out of the "
                               f"envelope by up to {HARDWARE_ALLOWANCE * 1000:.0f} mm."})
    return out


def extra_hardware(spec: dict, parts: list[dict]) -> list[dict]:
    """Handles / pulls / knobs the video does not show (the spec lists none for
    that place): a model that bolts a bar onto a handle-less door to pass the
    grasp rule is wrong twice -- it looks wrong and it teaches the wrong grasp."""
    allowed = [p["id"] for p in spec.get("parts") or []
               if p.get("kind") in ("handle", "knob", "lever", "pull") or is_grasp_hardware(p["id"])]
    out = []
    for q in parts:
        n = q["name"]
        if not is_grasp_hardware(n):
            continue
        if any(n == a or n.startswith(a + "_") or n.startswith(a) for a in allowed):
            continue
        out.append({"severity": "hard", "id": f"sim2real.extra_hardware:{n}",
                    "message": f"'{n}' is a handle/pull/knob the video does not show (the specification has none "
                               f"there). Delete it; open the panel the way the specification says (hidden grip "
                               f"channel, finger slot or push latch)."})
    return out


def visible_latches(parts: list[dict], joints: list[dict]) -> list[dict]:
    """A push latch is inside the cabinet: a latch part standing in front of the
    door it releases is a visible stud the video does not show."""
    by = {p["name"]: p for p in parts}
    out = []
    for j in joints:
        if j.get("type") not in ("revolute", "prismatic"):
            continue
        group = [by[n] for n in j.get("parts") or [] if n in by]
        panels = [p for p in group if not is_grasp_hardware(p["name"]) and "latch" not in p["name"].lower()]
        if not panels:
            continue
        front = min(p["min"][1] for p in panels)          # object front is -Y
        for q in parts:
            if "latch" in q["name"].lower() and q["min"][1] < front + 0.005 and \
                    any(q["max"][0] > p["min"][0] and q["min"][0] < p["max"][0] and q["max"][2] > p["min"][2]
                        and q["min"][2] < p["max"][2] for p in panels):
                out.append({"severity": "hard", "id": f"sim2real.visible_latch:{q['name']}",
                            "message": f"'{q['name']}' sits on the face of the door it releases and shows as a stud; "
                                       f"a push latch is hidden INSIDE the carcass behind the door (put it behind "
                                       f"the door's back face, on the carcass)."})
    return list({v["id"]: v for v in out}.values())
