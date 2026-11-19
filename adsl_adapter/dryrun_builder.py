"""Run an Agent program WITHOUT Blender and record what it builds (pure Python).

Why
---
Every forge attempt used to cost a Blender start, a build and three Cycles
renders before anyone learned that the program had no display, four keys
instead of three, or a door glued to the body. Those are facts about the
part LIST, not about pixels, so they can be checked from the program alone.

This module executes `build(B, mats, P)` against a stand-in Builder that
implements the same DSL surface (box / cylinder / sphere / tube / mesh, the
curve tools, the ornament generators) and records, for every call, the part
name, an axis-aligned box and the material key. spec_check then tests the
list against the functional spec. Boxes are conservative approximations
(a rotated cylinder becomes the box of its rotated bounding box), which is
all a count, a presence or a "which face is it on" test needs.

It is a pre-flight, not a judge: Blender still runs the program and its
exact bounds are checked again there. An exception raised by OUR stand-in
(an API corner we do not imitate) is reported as `inconclusive`, never as a
program fault, so the dry run can only ever save an attempt, not cost one.
"""
from __future__ import annotations

import math
import traceback
from types import SimpleNamespace

import material_spec

# The named palette forge_runner.build_materials() provides. Kept in sync by
# hand: a key missing here would make the dry run reject a valid program.
NAMED_KEYS = {
    "linen_sand", "boucle_grey", "linen_charcoal", "oak_joinery", "walnut_joinery",
    "brass_warm", "ceramic_matte", "marble_cream", "lamp_shade", "boiserie_walnut",
    "gilded_bronze", "screen_black", "plastic_black", "steel_brushed",
}
PROGRAM_FILE = "<program>"


class ProgramFault(Exception):
    """An error the program itself caused (bad material key, bad argument)."""


# --------------------------------------------------------------------------
# small vector helpers
# --------------------------------------------------------------------------
def _v3(value, default=(0.0, 0.0, 0.0)):
    try:
        x = [float(c) for c in value]
    except TypeError:
        x = [float(value)] * 3
    while len(x) < 3:
        x.append(0.0)
    return x[:3] if x else list(default)


def _rot_euler(p, rot):
    """Rotate a point by an XYZ Euler (radians), as Blender applies it."""
    x, y, z = p
    rx, ry, rz = rot
    cy_, sy_ = math.cos(rx), math.sin(rx)
    y, z = cy_ * y - sy_ * z, sy_ * y + cy_ * z
    cy_, sy_ = math.cos(ry), math.sin(ry)
    x, z = cy_ * x + sy_ * z, -sy_ * x + cy_ * z
    cy_, sy_ = math.cos(rz), math.sin(rz)
    x, y = cy_ * x - sy_ * y, sy_ * x + cy_ * y
    return [x, y, z]


def _box_of(points):
    pts = list(points)
    return ([min(p[k] for p in pts) for k in range(3)],
            [max(p[k] for p in pts) for k in range(3)])


def _corners(half):
    hx, hy, hz = half
    return [[sx * hx, sy * hy, sz * hz] for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)]


SIGNATURES = {
    "box": "B.box(name, size=(sx, sy, sz), loc=(x, y, z), mat, yaw=0.0, bevel=0.0)",
    "cylinder": "B.cylinder(name, radius, depth, loc=(x, y, z), mat, verts=32, rot=None)",
    "sphere": "B.sphere(name, radius, loc=(x, y, z), mat, segments=20, rings=12, scale=None)",
    "tube": "B.tube(name, points=[(x, y, z), ...], radii=[r, ...], mat, bevel_res=3)",
    "mesh": "B.mesh(name, verts, faces, mat, params=None, loc=(0, 0, 0))",
}


def _need_number(fn, arg, value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProgramFault(f"{fn}: '{arg}' must be a number, got {type(value).__name__} "
                           f"{str(value)[:40]} -- arguments in the wrong order? The signature "
                           f"is {SIGNATURES.get(fn, fn)}")


def _need_vec(fn, arg, value):
    ok = isinstance(value, (list, tuple)) and len(value) == 3 and all(
        isinstance(c, (int, float)) and not isinstance(c, bool) for c in value)
    if not ok:
        raise ProgramFault(f"{fn}: '{arg}' must be an (x, y, z) triple of numbers, got "
                           f"{str(value)[:40]} -- arguments in the wrong order? The signature "
                           f"is {SIGNATURES.get(fn, fn)}")


def _bind(args, kwargs, names, defaults=None):
    """Map positional + keyword arguments onto parameter names."""
    out = dict(defaults or {})
    for name, value in zip(names, args):
        out[name] = value
    out.update(kwargs)
    missing = [n for n in names if n not in out]
    if missing:
        raise ProgramFault(f"missing argument(s) {missing}")
    return out


# --------------------------------------------------------------------------
# stand-ins for Blender objects and the material table
# --------------------------------------------------------------------------
class MockVector(list):
    """list with .x/.y/.z, so `obj.location.z += 0.1` works and is tracked."""

    def __init__(self, values, owner=None):
        super().__init__(_v3(values))
        self._owner = owner

    def _get(i):
        return property(lambda self: self[i], lambda self, v: self._set(i, v))

    x, y, z = _get(0), _get(1), _get(2)

    def _set(self, i, v):
        old = list(self)
        self[i] = float(v)
        if self._owner is not None:
            self._owner._moved(old, list(self))

    def copy(self):
        return MockVector(self)


class MockObject:
    """What a Builder call returns. Tolerates any attribute a program sets."""

    def __init__(self, record):
        object.__setattr__(self, "_rec", record)
        object.__setattr__(self, "name", record["name"])
        centre = [(record["min"][k] + record["max"][k]) / 2 for k in range(3)]
        object.__setattr__(self, "_loc", MockVector(centre, owner=self))
        object.__setattr__(self, "rotation_euler", MockVector((0, 0, 0)))
        object.__setattr__(self, "scale", MockVector((1, 1, 1)))
        object.__setattr__(self, "data", SimpleNamespace(materials=[], vertices=[]))

    def _moved(self, old, new):
        for k in range(3):
            d = new[k] - old[k]
            self._rec["min"][k] += d
            self._rec["max"][k] += d

    @property
    def location(self):
        return self._loc

    @location.setter
    def location(self, value):
        new = _v3(value)
        old = list(self._loc)
        object.__setattr__(self, "_loc", MockVector(new, owner=self))
        self._moved(old, new)

    @property
    def dimensions(self):
        return MockVector([self._rec["max"][k] - self._rec["min"][k] for k in range(3)])

    def __getitem__(self, key):
        return self._rec.get("props", {}).get(key)

    def __setitem__(self, key, value):
        self._rec.setdefault("props", {})[key] = value

    def get(self, key, default=None):
        return self._rec.get("props", {}).get(key, default)

    def __setattr__(self, key, value):
        if key == "location":
            MockObject.location.fset(self, value)
        else:
            object.__setattr__(self, key, value)

    def __getattr__(self, key):          # only for attributes never set
        return _Anything()


class _Anything:
    """Absorbs any attribute access or call (obj.modifiers.new(...), ...).

    A program may touch Blender-side attributes of what the Builder returned;
    in Blender that works, so here it must not look like a program fault."""

    def __getattr__(self, key):
        if key.startswith("__"):
            raise AttributeError(key)
        return self

    def __call__(self, *args, **kwargs):
        return self

    def __iter__(self):
        return iter(())

    def __len__(self):
        return 0

    def __getitem__(self, key):
        return self

    def __setitem__(self, key, value):
        pass


class MockMaterial(str):
    """mats[key] returns the key itself, so either form records the key."""


class DryMats(dict):
    def _check(self, key):
        if not isinstance(key, str):
            raise ProgramFault(f"material must be a key string, got {type(key).__name__}")
        if key in NAMED_KEYS:
            return MockMaterial(key)
        if material_spec.parse(key) is not None:
            return MockMaterial(key)
        raise ProgramFault(material_spec.explain(key))

    def __getitem__(self, key):
        return self._check(key)

    def get(self, key, default=None):
        try:
            return self._check(key)
        except ProgramFault:
            return default

    def __contains__(self, key):
        return self.get(key) is not None

    def keys(self):
        return list(NAMED_KEYS)

    def items(self):
        return [(k, MockMaterial(k)) for k in NAMED_KEYS]


# --------------------------------------------------------------------------
# the stand-in Builder
# --------------------------------------------------------------------------
class DryBuilder:
    def __init__(self, mats: DryMats):
        self.mats = mats
        self.parts: list[dict] = []
        self.objects: list[MockObject] = []
        self.unknown_calls: list[str] = []
        self.coll = SimpleNamespace(objects=SimpleNamespace(link=lambda o: None))

    # bookkeeping -------------------------------------------------------
    def _add(self, name, lo, hi, mat, ptype):
        if mat is not None and not isinstance(mat, MockMaterial):
            mat = self.mats[mat]
        rec = {"name": str(name), "min": [round(v, 5) for v in lo],
               "max": [round(v, 5) for v in hi], "material": str(mat) if mat else None,
               "type": ptype}
        self.parts.append(rec)
        obj = MockObject(rec)
        self.objects.append(obj)
        return obj

    def begin(self, *args, **kwargs):
        return None

    def objects_created(self):
        return list(self.objects)

    def __getattr__(self, name):
        # Mirror the real Builder: anything else raises in Blender, so it is a
        # program fault here too (B.torus slipped through as a no-op before
        # forge_runner exposed the ornaments as methods).
        if name in ORNAMENTS:
            return lambda *a, **k: ORNAMENTS[name](self, *a, **k)
        raise ProgramFault(f"B.{name}() does not exist; the DSL is B.box / cylinder / sphere / "
                           f"tube / mesh, the curve tools and the ornament generators")

    # primitives --------------------------------------------------------
    def box(self, *args, **kwargs):
        a = _bind(args, kwargs, ["name", "size", "loc", "mat"], {"yaw": 0.0, "bevel": 0.0})
        _need_vec("box", "size", a["size"])
        _need_vec("box", "loc", a["loc"])
        _need_number("box", "yaw", a["yaw"])
        size, loc = _v3(a["size"]), _v3(a["loc"])
        half = [abs(s) / 2 for s in size]
        # baroque_lib.Builder.box takes yaw in DEGREES (math.radians(yaw))
        pts = [_rot_euler(c, (0.0, 0.0, math.radians(float(a["yaw"] or 0.0)))) for c in _corners(half)]
        lo, hi = _box_of([[p[k] + loc[k] for k in range(3)] for p in pts])
        return self._add(a["name"], lo, hi, a["mat"], "box")

    def cylinder(self, *args, **kwargs):
        a = _bind(args, kwargs, ["name", "radius", "depth", "loc", "mat"],
                  {"verts": 32, "rot": None})
        _need_number("cylinder", "radius", a["radius"])
        _need_number("cylinder", "depth", a["depth"])
        _need_vec("cylinder", "loc", a["loc"])
        if a["rot"] is not None:
            _need_vec("cylinder", "rot", a["rot"])
        r, d, loc = float(a["radius"]), float(a["depth"]), _v3(a["loc"])
        rot = _v3(a["rot"]) if a["rot"] is not None else (0.0, 0.0, 0.0)
        pts = [_rot_euler(c, rot) for c in _corners((r, r, d / 2))]
        lo, hi = _box_of([[p[k] + loc[k] for k in range(3)] for p in pts])
        return self._add(a["name"], lo, hi, a["mat"], "cylinder")

    def sphere(self, *args, **kwargs):
        a = _bind(args, kwargs, ["name", "radius", "loc", "mat"],
                  {"segments": 20, "rings": 12, "scale": None})
        _need_number("sphere", "radius", a["radius"])
        _need_vec("sphere", "loc", a["loc"])
        if a["scale"] is not None:
            _need_vec("sphere", "scale", a["scale"])
        r, loc = float(a["radius"]), _v3(a["loc"])
        sc = _v3(a["scale"]) if a["scale"] is not None else (1.0, 1.0, 1.0)
        lo = [loc[k] - r * abs(sc[k]) for k in range(3)]
        hi = [loc[k] + r * abs(sc[k]) for k in range(3)]
        return self._add(a["name"], lo, hi, a["mat"], "sphere")

    def tube(self, *args, **kwargs):
        a = _bind(args, kwargs, ["name", "points", "radii", "mat"], {"bevel_res": 3})
        for pt in a["points"]:
            _need_vec("tube", "points[i]", tuple(pt) if isinstance(pt, list) else pt)
        if not isinstance(a["radii"], (list, tuple)) or len(a["radii"]) != len(a["points"]):
            raise ProgramFault("tube: 'radii' must be a list with one radius per point "
                               f"(the real Builder zips them); {SIGNATURES['tube']}")
        return _swept(self, a["name"], a["points"], a["radii"], a["mat"], "tube")

    def mesh(self, *args, **kwargs):
        a = _bind(args, kwargs, ["name", "verts", "faces", "mat"],
                  {"params": None, "loc": (0, 0, 0)})
        loc = _v3(a["loc"])
        verts = [_v3(v) for v in a["verts"]]
        if not verts:
            raise ProgramFault(f"mesh '{a['name']}' has no vertices")
        lo, hi = _box_of([[v[k] + loc[k] for k in range(3)] for v in verts])
        return self._add(a["name"], lo, hi, a["mat"], "mesh")

    # both spellings of the curve tools, like forge_runner exposes them
    def sweep(self, *a, **k):
        return sweep(self, *a, **k)

    def bezier_tube(self, *a, **k):
        return bezier_tube(self, *a, **k)

    def piping_loop(self, *a, **k):
        return piping_loop(self, *a, **k)

    def lathe(self, *a, **k):
        return lathe(self, *a, **k)

    def arc_tube(self, *a, **k):
        return arc_tube(self, *a, **k)


# --------------------------------------------------------------------------
# curve tools and ornaments (geometry mirrors curve_dsl / baroque_lib)
# --------------------------------------------------------------------------
def _swept(B, name, points, radii, mat, ptype="sweep"):
    pts = [_v3(p) for p in points]
    if len(pts) < 2:
        raise ProgramFault(f"'{name}' needs at least 2 points")
    rads = radii if isinstance(radii, (list, tuple)) else [radii] * len(pts)
    r = max(abs(float(x)) for x in rads) if rads else 0.0
    lo, hi = _box_of(pts)
    return B._add(name, [v - r for v in lo], [v + r for v in hi], mat, ptype)


def sweep(B, *args, **kwargs):
    a = _bind(args, kwargs, ["name", "points", "radii", "mat"])
    # curve_dsl.sweep: a single radius, or exactly one per point
    if isinstance(a["radii"], (list, tuple)) and len(a["radii"]) != len(a["points"]):
        raise ProgramFault(f"sweep '{a['name']}': {len(a['radii'])} radii for {len(a['points'])} "
                           f"points -- give one radius per point, or a single number")
    return _swept(B, a["name"], a["points"], a["radii"], a["mat"])


def bezier_tube(B, *args, **kwargs):
    a = _bind(args, kwargs, ["name", "ctrl", "radius", "mat"], {"taper": 1.0})
    r = abs(float(a["radius"])) * max(1.0, abs(float(a["taper"] or 1.0)))
    return _swept(B, a["name"], a["ctrl"], r, a["mat"])


def piping_loop(B, *args, **kwargs):
    a = _bind(args, kwargs, ["name", "center", "half_w", "half_h", "radius", "mat"],
              {"plane": "XZ"})
    c = _v3(a["center"])
    hw, hh, r = float(a["half_w"]), float(a["half_h"]), float(a["radius"])
    ext = {"XZ": (hw, r, hh), "XY": (hw, hh, r), "YZ": (r, hw, hh)}.get(a["plane"])
    if ext is None:
        raise ProgramFault("plane must be XZ, XY or YZ")
    ext = [ext[k] + (r if ext[k] != r else 0.0) for k in range(3)]
    return B._add(a["name"], [c[k] - ext[k] for k in range(3)],
                  [c[k] + ext[k] for k in range(3)], a["mat"], "sweep")


def lathe(B, *args, **kwargs):
    a = _bind(args, kwargs, ["name", "profile", "center", "mat"], {"axis": "Z"})
    c = _v3(a["center"])
    prof = [(abs(float(r)), float(h)) for r, h in a["profile"]]
    if not prof:
        raise ProgramFault(f"lathe '{a['name']}' has an empty profile")
    rmax = max(r for r, _ in prof)
    hmin, hmax = min(h for _, h in prof), max(h for _, h in prof)
    axis = str(a["axis"]).upper()
    if axis == "Z":
        lo, hi = [c[0] - rmax, c[1] - rmax, c[2] + hmin], [c[0] + rmax, c[1] + rmax, c[2] + hmax]
    elif axis == "X":
        lo, hi = [c[0] + hmin, c[1] - rmax, c[2] - rmax], [c[0] + hmax, c[1] + rmax, c[2] + rmax]
    else:
        lo, hi = [c[0] - rmax, c[1] + hmin, c[2] - rmax], [c[0] + rmax, c[1] + hmax, c[2] + rmax]
    return B._add(a["name"], lo, hi, a["mat"], "mesh")


def arc_tube(B, *args, **kwargs):
    a = _bind(args, kwargs, ["name", "center", "radius", "tube_r", "mat"],
              {"start_deg": 0.0, "end_deg": 180.0, "samples": 20, "z_scale": 1.0})
    cx, cy, cz = _v3(a["center"])
    n = max(2, int(a["samples"]))
    pts = []
    for i in range(n):
        ang = math.radians(float(a["start_deg"]) + (float(a["end_deg"]) - float(a["start_deg"]))
                           * i / (n - 1))
        pts.append((cx + float(a["radius"]) * math.cos(ang), cy,
                    cz + float(a["radius"]) * float(a["z_scale"]) * math.sin(ang)))
    return _swept(B, a["name"], pts, float(a["tube_r"]), a["mat"])


def _centred(B, name, center, ext, mat):
    c = _v3(center)
    return B._add(name, [c[k] - ext[k] for k in range(3)],
                  [c[k] + ext[k] for k in range(3)], mat, "ornament")


def volute(B, *args, **kwargs):
    a = _bind(args, kwargs, ["name", "center", "r0", "r1", "tube_r", "mat"], {"plane": "XZ"})
    r = abs(float(a["r0"])) + abs(float(a["tube_r"]))
    t = abs(float(a["tube_r"]))
    ext = {"XZ": (r, t, r), "XY": (r, r, t), "YZ": (t, r, r)}.get(a["plane"], (r, r, r))
    return _centred(B, a["name"], a["center"], ext, a["mat"])


def cabriole_leg(B, *args, **kwargs):
    a = _bind(args, kwargs, ["name", "base", "height", "mat"], {"foot_r": 0.055, "sway": 0.075})
    b = _v3(a["base"])
    w = abs(float(a["foot_r"])) + abs(float(a["sway"]))
    return B._add(a["name"], [b[0] - w, b[1] - w, b[2]],
                  [b[0] + w, b[1] + w, b[2] + float(a["height"])], a["mat"], "ornament")


def acanthus_leaf(B, *args, **kwargs):
    a = _bind(args, kwargs, ["name", "loc", "height", "width", "mat"], {"thickness": 0.022})
    loc = _v3(a["loc"])
    w, t = float(a["width"]) / 2, float(a["thickness"])
    return B._add(a["name"], [loc[0] - w, loc[1] - w, loc[2]],
                  [loc[0] + w, loc[1] + w, loc[2] + float(a["height"])], a["mat"], "ornament")


def torus(B, *args, **kwargs):
    a = _bind(args, kwargs, ["name", "center", "major", "minor", "mat"])
    R = abs(float(a["major"])) + abs(float(a["minor"]))
    return _centred(B, a["name"], a["center"], (R, R, abs(float(a["minor"]))), a["mat"])


def fluted_column(B, *args, **kwargs):
    a = _bind(args, kwargs, ["name", "base", "height", "radius", "mat"])
    b, r = _v3(a["base"]), abs(float(a["radius"]))
    return B._add(a["name"], [b[0] - r, b[1] - r, b[2]],
                  [b[0] + r, b[1] + r, b[2] + float(a["height"])], a["mat"], "ornament")


def dentil_row(B, *args, **kwargs):
    a = _bind(args, kwargs, ["name", "start", "end", "z", "mat"],
              {"size": (0.055, 0.075, 0.065)})
    s, e = _v3(a["start"]), _v3(a["end"])
    sz = _v3(a["size"])
    lo = [min(s[k], e[k]) - sz[k] / 2 for k in range(2)] + [float(a["z"])]
    hi = [max(s[k], e[k]) + sz[k] / 2 for k in range(2)] + [float(a["z"]) + sz[2]]
    return B._add(a["name"], lo, hi, a["mat"], "ornament")


def corinthian_capital(B, *args, **kwargs):
    """baroque_lib: (B, name, center, radius, mat, gold_mat=None); bell + abacus."""
    a = _bind(args, kwargs, ["name", "center", "radius", "mat"], {"gold_mat": None})
    x, y, z = _v3(a["center"])
    r = abs(float(a["radius"])) * 1.39
    return B._add(a["name"], [x - r, y - r, z], [x + r, y + r, z + 0.436], a["mat"], "ornament")


ORNAMENTS = {}          # filled below: B.<ornament>(...) spelling


# --------------------------------------------------------------------------
# entry
# --------------------------------------------------------------------------
ORNAMENTS.update({f.__name__: f for f in (volute, cabriole_leg, acanthus_leaf, torus,
                                          fluted_column, corinthian_capital, dentil_row)})


def run_program(code: str, P: dict) -> dict:
    """Execute `build(B, mats, P)` against the stand-in DSL.

    {"ok": True, "parts": [...], "unknown_calls": [...]} on success;
    {"ok": False, "program_fault": bool, "error": "..."} otherwise. Only a
    program fault should be fed back to the Agent.
    """
    mats = DryMats()
    B = DryBuilder(mats)
    ns = {"B": B, "mats": mats, "P": dict(P), "math": math,
          "volute": volute, "cabriole_leg": cabriole_leg, "acanthus_leaf": acanthus_leaf,
          "torus": torus, "fluted_column": fluted_column,
          "corinthian_capital": corinthian_capital, "dentil_row": dentil_row,
          "sweep": sweep, "bezier_tube": bezier_tube, "piping_loop": piping_loop,
          "lathe": lathe, "arc_tube": arc_tube}
    try:
        exec(compile(code, PROGRAM_FILE, "exec"), ns)  # noqa: S102 (contract-checked code)
        build = ns.get("build")
        if not callable(build):
            return {"ok": False, "program_fault": True,
                    "error": "program does not define build(B, mats, P)"}
        build(B, mats, ns["P"])
    except ProgramFault as exc:
        return {"ok": False, "program_fault": True, "error": str(exc), "parts": B.parts}
    except Exception as exc:  # noqa: BLE001
        tb = traceback.extract_tb(exc.__traceback__)
        innermost = tb[-1] if tb else None
        fault = bool(innermost and innermost.filename == PROGRAM_FILE)
        where = f" (program line {innermost.lineno})" if fault else ""
        return {"ok": False, "program_fault": fault,
                "error": f"{type(exc).__name__}: {exc}{where}", "parts": B.parts}
    names = [p["name"] for p in B.parts]
    dupes = sorted({n for n in names if names.count(n) > 1})
    return {"ok": True, "parts": B.parts, "unknown_calls": sorted(set(B.unknown_calls)),
            "duplicate_names": dupes}
