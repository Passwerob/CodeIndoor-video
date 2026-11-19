"""Preset curves and turned profiles for editable FineScene parts.

Two curved primitives build on these presets:

``sweep``  a circular section swept along a path. The path is either explicit
           (``path``: offsets from the part center) or generated from a named
           ``curve`` preset that is fitted to the part's ``size``. ``radius`` is
           the section radius at the start; ``radius_end`` tapers it linearly
           along the arc length (a cone tip when 0). ``closed`` joins the ends.
``lathe``  a solid of revolution about the part's local +Z axis, from an
           explicit ``profile`` ([radius, z] offsets) or a ``profile_preset``
           fitted to ``size``.

Both compile to aDSL circular cylinders/frustums plus joint spheres, so the
geometry stays parametric: editing ``size`` or any parameter referenced by
the curve re-evaluates the shape. Nothing here is a mesh, a relief or a
non-circular section: those remain unavailable (see style_contract).

Fitted presets live in a canonical box [-1, 1]^3 whose axes are the curve
plane (u, v) and its normal (n); they are scaled to ``size / 2 - radius`` so
the swept tube stays inside the declared part envelope.
"""
from __future__ import annotations

import math
from typing import Callable, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

Scalar = float | str
Vector = tuple[Scalar, Scalar, Scalar]

CURVE_PRIMITIVES = frozenset({'sweep', 'lathe'})

CurvePresetName = Literal['arc', 'ring', 'rounded_rect', 'helix', 'spiral', 'c_scroll', 's_curve', 'cabriole',
                          'gooseneck', 'u_bend', 'wave', 'catenary', 'bezier', 'smooth']
ProfilePresetName = Literal['baluster', 'vase', 'urn', 'bun_foot', 'knob', 'bell_shade', 'drum_shade',
                            'turned_leg', 'column_base', 'finial', 'pedestal', 'tapered_foot']

# Which optional parameters each curve preset reads (anything else is rejected,
# so a planner cannot believe an ignored field changed the shape).
CURVE_PARAMS: dict[str, set[str]] = {
    'arc': {'start_deg', 'end_deg'},
    'ring': {'start_deg'},
    'rounded_rect': {'corner'},
    'helix': {'turns', 'start_deg'},
    'spiral': {'turns', 'inner', 'start_deg'},
    'c_scroll': {'turns', 'inner'},
    's_curve': set(),
    'cabriole': {'knee'},
    'gooseneck': {'spout'},
    'u_bend': {'corner'},
    'wave': {'cycles', 'start_deg'},
    'catenary': {'tension'},
    'bezier': {'points'},
    'smooth': {'points'},
}
CLOSED_PRESETS = {'ring', 'rounded_rect'}
EXPLICIT_PRESETS = {'bezier', 'smooth'}

CURVE_HELP = {
    'arc': 'open circular/elliptic arc in the plane; start_deg..end_deg (default 0..180, 0 = +u, 90 = +v)',
    'ring': 'closed circle/ellipse filling the plane extents (hoops, rims, handle rings)',
    'rounded_rect': 'closed rounded rectangle; corner 0..1 of the smaller half extent (piping, edge beads, frames)',
    'helix': 'helix about the plane normal; turns (default 3) (springs, twisted cords, spiral stair rails)',
    'spiral': 'planar spiral (volute); turns (default 1.75), inner 0..1 = inner/outer radius (default 0.25)',
    'c_scroll': 'C-scroll: an arc whose two ends curl inward; turns of each curl (default 0.9), inner (default 0.3)',
    's_curve': 'S-shaped member from -v to +v; the lateral amplitude is the size along u (S-scrolls, brackets)',
    'cabriole': 'cabriole leg from the top (+v, path start) down to the foot (-v); knee 0.4..0.9 height of the knee bulge (default 0.75); bulge = size along u; radius = knee, radius_end = ankle (thinner)',
    'gooseneck': 'rises along v at -u, arcs over and ends pointing down at +u (faucets, lamp arms); spout 0.1..0.6 length of the downward end as a fraction of the height (default 0.2)',
    'u_bend': 'hairpin: two legs along v joined by a round bend at +v; corner 0..1 bend tightness (handles, hairpin legs)',
    'wave': 'sine along u with amplitude in v; cycles (default 2) (wavy rails, ribbons, trim)',
    'catenary': 'hanging curve between the two top corners, depth = size along v; tension 0.2..3 (default 0.6): low = V-like, high = flat-bottomed (swags, cords, chains)',
    'bezier': 'Bezier curve through explicit control points (offsets from the part center, >= 3 points)',
    'smooth': 'smooth Catmull-Rom curve through explicit points (offsets from the part center, >= 3 points)',
}
PROFILE_HELP = {
    'baluster': 'vase-shaped baluster with plinth, belly and neck rings',
    'vase': 'foot, round belly, slim neck and flared lip',
    'urn': 'stemmed urn with gadrooned belly shoulder and lid knob',
    'bun_foot': 'squashed ball furniture foot',
    'knob': 'drawer/door knob: short stem and mushroom head',
    'bell_shade': 'bell lamp shade, wide flared bottom',
    'drum_shade': 'drum lamp shade with slight taper',
    'turned_leg': 'turned table/chair leg: block top, rings, long taper, small foot',
    'column_base': 'round column base: torus, scotia, torus on a plinth disc',
    'finial': 'pointed decorative finial on a collar',
    'pedestal': 'table pedestal: spread foot, column, flared capital',
    'tapered_foot': 'plain tapered furniture foot',
}


class CurveSpec(BaseModel):
    """A named curve preset fitted to the part size (or explicit control points)."""
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    preset: CurvePresetName
    plane: Literal['xz', 'xy', 'yz'] = 'xz'
    samples: int | None = Field(default=None, ge=4, le=96)
    flip: bool = False
    turns: Scalar | None = None
    start_deg: Scalar | None = None
    end_deg: Scalar | None = None
    inner: Scalar | None = None
    knee: Scalar | None = None
    spout: Scalar | None = None
    corner: Scalar | None = None
    cycles: Scalar | None = None
    tension: Scalar | None = None
    points: list[Vector] = Field(default_factory=list, max_length=24)

    @model_validator(mode='after')
    def check_params(self):
        allowed = CURVE_PARAMS[self.preset]
        given = {k for k in ('turns', 'start_deg', 'end_deg', 'inner', 'knee', 'spout', 'corner', 'cycles', 'tension')
                 if getattr(self, k) is not None}
        if self.points:
            given.add('points')
        extra = given - allowed
        if extra:
            raise ValueError(f'curve preset {self.preset} does not use {sorted(extra)}; '
                             f'it accepts {sorted(allowed) or "no parameters"}')
        if self.preset in EXPLICIT_PRESETS and len(self.points) < 3:
            raise ValueError(f'curve preset {self.preset} needs >= 3 control points (offsets from part center)')
        return self


class ProfileSpec(BaseModel):
    """Lathe profile: a preset fitted to size, or explicit [radius, z] offsets."""
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    preset: ProfilePresetName | None = None
    points: list[tuple[Scalar, Scalar]] = Field(default_factory=list, max_length=64)
    samples: int | None = Field(default=None, ge=8, le=96)

    @model_validator(mode='after')
    def check_source(self):
        if (self.preset is None) == (not self.points):
            raise ValueError('lathe profile needs exactly one of preset or points')
        if self.points and len(self.points) < 2:
            raise ValueError('explicit lathe profile needs >= 2 [radius, z] points')
        return self


def check_curve_fields(part) -> None:
    """Field rules shared by the planner PartSpec and the executable Part."""
    p = part.primitive
    sweep_only = {'radius_end': part.radius_end is not None, 'closed': bool(part.closed), 'curve': part.curve is not None}
    if p == 'sweep':
        if part.radius is None:
            raise ValueError(f'{part.id}: sweep requires radius')
        if (len(part.path) >= 2) == (part.curve is not None):
            raise ValueError(f'{part.id}: sweep requires exactly one of path (>=2 points) or curve preset')
        if part.path and len(part.path) < 2:
            raise ValueError(f'{part.id}: sweep path needs >= 2 points')
        if part.curve is not None and part.curve.preset in CLOSED_PRESETS and part.closed:
            raise ValueError(f'{part.id}: curve preset {part.curve.preset} is already closed; omit closed')
        closed = part.closed or (part.curve is not None and part.curve.preset in CLOSED_PRESETS)
        if closed and part.radius_end is not None:
            raise ValueError(f'{part.id}: a closed sweep has one constant radius; omit radius_end')
        if part.profile is not None:
            raise ValueError(f'{part.id}: profile is only valid for lathe parts')
        return
    if p == 'lathe':
        if part.profile is None:
            raise ValueError(f'{part.id}: lathe requires profile (preset or [radius, z] points)')
        bad = [k for k, v in sweep_only.items() if v] + (['path'] if part.path else []) + (
            ['radius'] if part.radius is not None else [])
        if bad:
            raise ValueError(f'{part.id}: {bad} are only valid for sweep parts')
        return
    bad = [k for k, v in sweep_only.items() if v] + (['path'] if part.path else []) + (
        ['radius'] if part.radius is not None else []) + (['profile'] if part.profile is not None else [])
    if bad:
        raise ValueError(f'{part.id}: {bad} are only valid for sweep/lathe parts')


# --------------------------------------------------------------------------- #
# curve evaluation helpers
# --------------------------------------------------------------------------- #
def _catmull_rom(points: np.ndarray, samples: int, closed: bool = False) -> np.ndarray:
    pts = np.asarray(points, dtype=float)
    if closed:
        ext = np.vstack([pts[-1], pts, pts[0], pts[1]])
        segs = len(pts)
    else:
        ext = np.vstack([2 * pts[0] - pts[1], pts, 2 * pts[-1] - pts[-2]])
        segs = len(pts) - 1
    per = max(2, int(math.ceil(samples / segs)))
    out = []
    for s in range(segs):
        p0, p1, p2, p3 = ext[s], ext[s + 1], ext[s + 2], ext[s + 3]
        for k in range(per):
            t = k / per
            out.append(0.5 * ((2 * p1) + (-p0 + p2) * t + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t * t
                              + (-p0 + 3 * p1 - 3 * p2 + p3) * t ** 3))
    if not closed:
        out.append(pts[-1])
    return np.asarray(out)


def _bezier(ctrl: np.ndarray, samples: int) -> np.ndarray:
    ctrl = np.asarray(ctrl, dtype=float)
    out = []
    for t in np.linspace(0, 1, samples):
        pts = ctrl.copy()
        while len(pts) > 1:
            pts = (1 - t) * pts[:-1] + t * pts[1:]
        out.append(pts[0])
    return np.asarray(out)


def _arc(c, r_u, r_v, a0, a1, n):
    a = np.radians(np.linspace(a0, a1, n))
    return np.stack([c[0] + r_u * np.cos(a), c[1] + r_v * np.sin(a), np.zeros(n)], axis=1)


def _canonical_curve(spec: CurveSpec, get: Callable[[str, float], float], n: int) -> tuple[np.ndarray, bool]:
    """Points in canonical (u, v, n) coordinates within [-1, 1]; returns (points, closed)."""
    p = spec.preset
    if p == 'arc':
        a0, a1 = get('start_deg', 0.0), get('end_deg', 180.0)
        if abs(a1 - a0) < 1 or abs(a1 - a0) > 359:
            raise ValueError('arc span must be between 1 and 359 degrees (use ring for a closed loop)')
        return _arc((0, 0), 1, 1, a0, a1, n), False
    if p == 'ring':
        a0 = get('start_deg', 0.0)
        return _arc((0, 0), 1, 1, a0, a0 + 360 * (n - 1) / n, n), True
    if p == 'rounded_rect':
        c = _unit(get('corner', 0.35), 'corner', lo=0.02)
        per = max(2, n // 4)
        pts = []
        for cx, cy, a0 in ((1 - c, 1 - c, 0), (-(1 - c), 1 - c, 90), (-(1 - c), -(1 - c), 180), (1 - c, -(1 - c), 270)):
            pts.extend(_arc((cx, cy), c, c, a0, a0 + 90, per + 1)[:-1])
        return np.asarray(pts), True
    if p == 'helix':
        turns = _positive(get('turns', 3.0), 'turns', hi=40)
        a0 = math.radians(get('start_deg', 0.0))
        t = np.linspace(0, 1, n)
        a = a0 + 2 * math.pi * turns * t
        return np.stack([np.cos(a), np.sin(a), 2 * t - 1], axis=1), False
    if p == 'spiral':
        turns = _positive(get('turns', 1.75), 'turns', hi=8)
        inner = _unit(get('inner', 0.25), 'inner', lo=0.02, hi=0.95)
        a0 = math.radians(get('start_deg', 0.0))
        t = np.linspace(0, 1, n)
        a = a0 + 2 * math.pi * turns * t
        r = 1 - (1 - inner) * t
        return np.stack([r * np.cos(a), r * np.sin(a), np.zeros(n)], axis=1), False
    if p == 'c_scroll':
        turns = _positive(get('turns', 0.9), 'turns', hi=3)
        inner = _unit(get('inner', 0.3), 'inner', lo=0.05, hi=0.9)
        # Spine: half circle on the -u side traversed counter-clockwise from
        # (0, 1) to (0, -1). Each end continues into a curl of radius cr whose
        # circle is tangent to the spine there, spiralling inward.
        m = max(6, n // 3)
        cr = 0.42
        s = np.linspace(0, 1, m)
        a = np.radians(90 - 360 * turns + 360 * turns * s)
        r = cr * (inner + (1 - inner) * s)
        top = np.stack([r * np.cos(a), 1 - cr + r * np.sin(a), np.zeros(m)], axis=1)
        spine = _arc((0, 0), 1, 1, 90, 270, m)
        a = np.radians(270 + 360 * turns * s)
        r = cr * (1 - (1 - inner) * s)
        bottom = np.stack([r * np.cos(a), -1 + cr + r * np.sin(a), np.zeros(m)], axis=1)
        return np.vstack([top[:-1], spine, bottom[1:]]), False
    if p == 's_curve':
        t = np.linspace(0, 1, n)
        return np.stack([np.sin(2 * math.pi * t), 2 * t - 1, np.zeros(n)], axis=1), False
    if p == 'cabriole':
        knee = _unit(get('knee', 0.75), 'knee', lo=0.4, hi=0.9)
        kv = 2 * knee - 1                 # canonical v of the knee bulge
        # Starts at the top joint, bulges out at the knee (+u), sweeps back in
        # to a slender ankle (-u) and kicks out slightly at the foot, so a
        # radius > radius_end taper reads thick knee -> thin ankle.
        ctrl = np.array([[-0.25, 1, 0], [0.85, kv, 0], [0.15, (kv - 0.72) / 2, 0],
                         [-0.4, -0.72, 0], [0.05, -1, 0]])
        return _catmull_rom(ctrl, n), False
    if p == 'gooseneck':
        spout = _unit(get('spout', 0.2), 'spout', lo=0.1, hi=0.6)
        r_u, r_v = 1.0, 0.35            # bend half-width (u) and bend height (v)
        top = 1 - r_v                    # v of the bend's springing line
        m = max(4, n // 3)
        up = np.stack([np.full(m, -1.0), np.linspace(-1, top, m), np.zeros(m)], axis=1)
        bend = _arc((-1 + r_u, top), r_u, r_v, 180, 0, m + 2)
        end = np.stack([np.full(m, -1 + 2 * r_u), np.linspace(top, top - 2 * spout, m), np.zeros(m)], axis=1)
        return np.vstack([up[:-1], bend, end[1:]]), False
    if p == 'u_bend':
        c = _unit(get('corner', 1.0), 'corner', lo=0.1)
        m = max(4, n // 3)
        top = 1 - c
        left = np.stack([np.full(m, -1.0), np.linspace(-1, top, m), np.zeros(m)], axis=1)
        bend = _arc((0, top), 1, c, 180, 0, m + 2)
        right = np.stack([np.full(m, 1.0), np.linspace(top, -1, m), np.zeros(m)], axis=1)
        return np.vstack([left[:-1], bend, right[1:]]), False
    if p == 'wave':
        cycles = _positive(get('cycles', 2.0), 'cycles', hi=24)
        a0 = math.radians(get('start_deg', 0.0))
        t = np.linspace(0, 1, n)
        return np.stack([2 * t - 1, np.sin(a0 + 2 * math.pi * cycles * t), np.zeros(n)], axis=1), False
    if p == 'catenary':
        tension = _positive(get('tension', 0.6), 'tension', hi=3)
        if tension < 0.2:
            raise ValueError(f'curve tension must be within [0.2, 3], got {tension:g}')
        x = np.linspace(-1, 1, n)
        y = np.cosh(x / tension)
        y = (y - y.min()) / (y.max() - y.min())  # 0 at the lowest point, 1 at the ends
        return np.stack([x, 2 * y - 1, np.zeros(n)], axis=1), False
    raise ValueError(f'unknown curve preset {p}')


def _normalize(pts: np.ndarray) -> np.ndarray:
    lo, hi = pts.min(axis=0), pts.max(axis=0)
    span = np.where(hi - lo > 1e-9, hi - lo, 1)
    out = 2 * (pts - lo) / span - 1
    out[:, (hi - lo) <= 1e-9] = 0
    return out


def _unit(v, name, lo=0.0, hi=1.0):
    if not lo <= v <= hi:
        raise ValueError(f'curve {name} must be within [{lo}, {hi}], got {v:g}')
    return v


def _positive(v, name, hi):
    if not 0 < v <= hi:
        raise ValueError(f'curve {name} must be within (0, {hi}], got {v:g}')
    return v


PLANE_AXES = {'xz': (0, 2, 1), 'xy': (0, 1, 2), 'yz': (1, 2, 0)}
DEFAULT_SAMPLES = {'ring': 32, 'rounded_rect': 32, 'helix': 0, 'spiral': 36, 'c_scroll': 36, 'wave': 0}


def _fit_axis(c: np.ndarray, r: np.ndarray, half: float) -> tuple[float, float]:
    """Scale s and offset o so the tube x = s*c + o (radius r) spans exactly [-half, half]."""
    lo_s, hi_s = 0.0, half / max(np.abs(c).max(), 1e-12)
    for _ in range(60):
        s = (lo_s + hi_s) / 2
        span = (s * c + r).max() - (s * c - r).min()
        lo_s, hi_s = (s, hi_s) if span < 2 * half else (lo_s, s)
    s = lo_s
    o = -((s * c + r).max() + (s * c - r).min()) / 2
    return s, o


def curve_points(spec: CurveSpec, dims: np.ndarray, r0: float, r1: float, scalar: Callable,
                 closed: bool = False) -> tuple[np.ndarray, bool]:
    """Local path (offsets from the part center) and whether it is closed.

    ``closed`` (the part's flag) joins an open preset's ends; ``smooth`` then
    interpolates through the seam, other presets close with a straight span.

    ``scalar`` evaluates a Scalar expression (the scene's bounded evaluator).
    Fitted presets are placed so the swept tube -- radius r0 tapering to r1
    along the arc length -- exactly fills dims along the curve axes.
    """
    max_radius = max(r0, r1)
    def get(name, default):
        value = getattr(spec, name)
        return default if value is None else float(scalar(value))

    if spec.preset in EXPLICIT_PRESETS:
        ctrl = np.array([[float(scalar(c)) for c in point] for point in spec.points])
        n = spec.samples or 24
        pts = _bezier(ctrl, n) if spec.preset == 'bezier' else _catmull_rom(ctrl, n, closed=closed)
        return _dedupe(pts, closed), closed
    n = spec.samples or DEFAULT_SAMPLES.get(spec.preset) or 24
    if spec.preset == 'helix' and not spec.samples:
        n = int(min(96, max(16, 16 * get('turns', 3.0))))
    if spec.preset == 'wave' and not spec.samples:
        n = int(min(96, max(16, 12 * get('cycles', 2.0))))
    canon, preset_closed = _canonical_curve(spec, get, n)
    closed = closed or preset_closed
    # Fit: the curve's own bounding box becomes the part envelope (minus the
    # tube radius), so ``size`` is the editable extent of every preset.
    canon = _normalize(canon)
    if spec.flip:
        canon = canon * np.array([-1, 1, 1])
    u, v, w = PLANE_AXES[spec.plane]
    half = np.asarray(dims, dtype=float) / 2 - max_radius
    used = [u, v] + ([w] if spec.preset == 'helix' else [])
    if np.any(half[used] <= 0):
        raise ValueError(f'curve {spec.preset}: part size must exceed 2 x radius along the curve axes '
                         f'(size {np.round(dims, 4).tolist()}, radius {max_radius:g})')
    if np.asarray(dims)[w] / 2 < max_radius - 1e-9:
        raise ValueError(f'curve {spec.preset}: part thickness along the plane normal must be >= 2 x radius')
    local = np.zeros((len(canon), 3))
    for k, axis in enumerate((u, v, w)):
        if axis in used:
            local[:, axis] = canon[:, k] * half[axis]
    if r0 != r1:
        # A taper changes the radius at the extreme points; refit each axis
        # with the actual per-point radii (radii depend on arc length, so
        # iterate until the placement settles).
        for _ in range(4):
            radii = taper_radii(local, r0, r1)
            for k, axis in enumerate((u, v, w)):
                if axis in used:
                    s, o = _fit_axis(canon[:, k], radii, dims[axis] / 2)
                    local[:, axis] = s * canon[:, k] + o
    return _dedupe(local, closed), closed


def _dedupe(pts: np.ndarray, closed: bool) -> np.ndarray:
    keep = [pts[0]]
    for q in pts[1:]:
        if np.linalg.norm(q - keep[-1]) > 1e-7:
            keep.append(q)
    if closed and len(keep) > 2 and np.linalg.norm(keep[-1] - keep[0]) <= 1e-7:
        keep.pop()
    return np.asarray(keep)


def taper_radii(points: np.ndarray, r0: float, r1: float) -> np.ndarray:
    """Radius at every path point, linear in arc length from r0 to r1."""
    seg = np.linalg.norm(np.diff(points, axis=0), axis=1)
    s = np.concatenate([[0], np.cumsum(seg)])
    t = s / s[-1] if s[-1] > 0 else np.zeros_like(s)
    return r0 + (r1 - r0) * t


# --------------------------------------------------------------------------- #
# lathe profiles  (t: 0 bottom -> 1 top, r: 0..1 of the radial half extent)
# --------------------------------------------------------------------------- #
_PROFILES: dict[str, list[tuple[float, float]]] = {
    'baluster': [(0, .62), (.06, .62), (.07, .5), (.12, .48), (.16, .55), (.3, .92), (.45, .9), (.6, .55),
                 (.74, .3), (.8, .3), (.83, .4), (.86, .3), (.93, .32), (.94, .62), (1, .62)],
    'vase': [(0, .45), (.04, .5), (.08, .4), (.2, .7), (.38, 1), (.55, .92), (.72, .5), (.84, .3),
             (.93, .34), (1, .5)],
    'urn': [(0, .55), (.05, .55), (.07, .3), (.18, .22), (.24, .5), (.36, .95), (.5, 1), (.6, .78),
            (.68, .62), (.7, .7), (.76, .55), (.86, .2), (.92, .24), (.97, .14), (1, 0)],
    'bun_foot': [(0, .7), (.12, .92), (.35, 1), (.6, .95), (.82, .72), (.92, .5), (.94, .55), (1, .55)],
    'knob': [(0, .45), (.06, .45), (.08, .3), (.4, .26), (.5, .5), (.62, .9), (.78, 1), (.92, .82), (1, .35)],
    'bell_shade': [(0, 1), (.04, .98), (.18, .82), (.4, .6), (.65, .46), (.85, .38), (1, .34)],
    'drum_shade': [(0, 1), (1, .88)],
    'turned_leg': [(0, .5), (.03, .62), (.06, .5), (.1, .42), (.62, .62), (.66, .72), (.69, .62), (.72, .75),
                   (.75, .66), (.78, 1), (1, 1)],
    'column_base': [(0, 1), (.2, 1), (.22, .92), (.32, .95), (.42, .86), (.52, .7), (.6, .74), (.66, .8),
                    (.76, .78), (.86, .66), (1, .6)],
    'finial': [(0, .55), (.1, .55), (.12, .4), (.2, .35), (.28, .62), (.42, 1), (.6, .78), (.8, .3), (.92, .12), (1, 0)],
    'pedestal': [(0, 1), (.05, .98), (.12, .6), (.2, .3), (.25, .24), (.75, .2), (.82, .26), (.9, .5), (.96, .9), (1, .92)],
    'tapered_foot': [(0, .55), (.85, 1), (.9, 1), (1, 1)],
}
# Points that start a step (flat shoulder) must not be smoothed through.
_SHARP = {'turned_leg', 'column_base', 'baluster', 'drum_shade', 'tapered_foot', 'pedestal'}


def lathe_profile(spec: ProfileSpec, dims: np.ndarray, scalar: Callable) -> np.ndarray:
    """[[radius, z], ...] in the part frame (z offsets from the center), bottom to top."""
    dims = np.asarray(dims, dtype=float)
    if abs(dims[0] - dims[1]) > 1e-6 * max(dims[0], dims[1]):
        raise ValueError(f'lathe size x and y must be equal (a round footprint), got {dims[0]:g} x {dims[1]:g}')
    radial, height = dims[0] / 2, dims[2]
    if spec.preset is not None:
        norm = np.asarray(_PROFILES[spec.preset], dtype=float)
        if spec.preset not in _SHARP:
            n = spec.samples or 40
            # Smooth in (t, r): parameterize by index so t stays monotone.
            smooth = _catmull_rom(norm, n)
            smooth[:, 0] = np.maximum.accumulate(np.clip(smooth[:, 0], 0, 1))
            smooth[:, 1] = np.clip(smooth[:, 1], 0, 1)
            norm = smooth
        prof = np.stack([norm[:, 1] * radial, -height / 2 + norm[:, 0] * height], axis=1)
    else:
        prof = np.array([[float(scalar(r)), float(scalar(z))] for r, z in spec.points])
    if np.any(prof[:, 0] < 0):
        raise ValueError('lathe profile radius must be >= 0')
    if np.any(np.diff(prof[:, 1]) < -1e-9):
        raise ValueError('lathe profile z must be non-decreasing from bottom to top (no undercut loops)')
    if prof[-1, 1] - prof[0, 1] <= 1e-6:
        raise ValueError('lathe profile needs a positive height')
    if prof[:, 0].max() <= 1e-6:
        raise ValueError('lathe profile needs a positive radius somewhere')
    if prof[:, 0].max() > radial + 1e-6 or np.any(np.abs(prof[:, 1]) > height / 2 + 1e-6):
        raise ValueError('lathe profile exceeds the declared part size (radius <= size_x/2, |z| <= size_z/2)')
    return prof


# --------------------------------------------------------------------------- #
# contact geometry
# --------------------------------------------------------------------------- #
def capsules(record, radius_step: float = 0.0005):
    """Constant-radius capsule segments covering a (possibly tapered) sweep.

    Tapered segments are split so the radius changes by at most
    ``radius_step`` per piece (at most 128 pieces); each piece takes its larger
    end radius, so measured gaps are conservative (never optimistic).
    """
    points = np.asarray(record['path'], dtype=float)
    radii = np.asarray(record.get('radii') or [record['radius']] * len(points), dtype=float)
    out = []
    for (a, b), (ra, rb) in zip(zip(points, points[1:]), zip(radii, radii[1:])):
        pieces = max(1, int(math.ceil(abs(rb - ra) / radius_step)))
        pieces = min(pieces, 128)
        for k in range(pieces):
            t0, t1 = k / pieces, (k + 1) / pieces
            out.append((a + (b - a) * t0, a + (b - a) * t1, max(ra + (rb - ra) * t0, ra + (rb - ra) * t1)))
    return out


def lathe_sdf(points_local: np.ndarray, profile: np.ndarray) -> np.ndarray:
    """Signed distance (negative inside) to a solid of revolution about local +Z."""
    q = np.asarray(points_local, dtype=float).reshape(-1, 3)
    rho, z = np.hypot(q[:, 0], q[:, 1]), q[:, 2]
    prof = np.asarray(profile, dtype=float)
    poly = np.vstack([[0, prof[0, 1]], prof, [0, prof[-1, 1]]])
    a, b = poly, np.roll(poly, -1, axis=0)
    p = np.stack([rho, z], axis=1)[:, None, :]
    e = (b - a)[None]
    w = p - a[None]
    ee = np.maximum((e ** 2).sum(-1), 1e-20)
    t = np.clip((w * e).sum(-1) / ee, 0, 1)
    # The last edge runs along the axis itself: it closes the polygon for the
    # inside test but is not a surface, so it is left out of the distance.
    d = np.sqrt(((w - e * t[..., None]) ** 2).sum(-1))[:, :-1].min(axis=1)
    # even-odd crossing test in the (rho, z) half plane
    ay, by = a[None, :, 1], b[None, :, 1]
    cond = (ay > p[..., 1]) != (by > p[..., 1])
    xint = a[None, :, 0] + (p[..., 1] - ay) * (b[None, :, 0] - a[None, :, 0]) / np.where(by - ay == 0, 1e-20, by - ay)
    inside = (cond & (p[..., 0] < xint)).sum(axis=1) % 2 == 1
    return np.where(inside, -d, d)


def lathe_surface_samples(profile: np.ndarray, step: float = 0.003, azimuth: int = 48) -> np.ndarray:
    """Surface points of a lathe solid (side and both end caps) in its local frame."""
    prof = np.asarray(profile, dtype=float)
    rows = []
    for (r0, z0), (r1, z1) in zip(prof, prof[1:]):
        n = max(1, int(math.ceil(math.hypot(r1 - r0, z1 - z0) / step)))
        for t in np.linspace(0, 1, n, endpoint=False):
            rows.append((r0 + (r1 - r0) * t, z0 + (z1 - z0) * t))
    rows.append(tuple(prof[-1]))
    for r_end, z_end in (prof[0], prof[-1]):
        for r in np.linspace(0, r_end, max(1, int(math.ceil(r_end / step))), endpoint=False):
            rows.append((r, z_end))
    a = np.linspace(0, 2 * math.pi, azimuth, endpoint=False)
    rz = np.asarray(rows)
    return np.concatenate([np.stack([rz[:, 0] * np.cos(x), rz[:, 0] * np.sin(x), rz[:, 1]], axis=1) for x in a])


def box_sdf(points_local: np.ndarray, half: np.ndarray) -> np.ndarray:
    q = np.abs(np.asarray(points_local, dtype=float)) - np.asarray(half, dtype=float)
    return np.linalg.norm(np.maximum(q, 0), axis=1) + np.minimum(q.max(axis=1), 0)


def cylinder_sdf(points_local: np.ndarray, radius: float, half_height: float) -> np.ndarray:
    q = np.asarray(points_local, dtype=float)
    radial = np.hypot(q[:, 0], q[:, 1]) - radius
    axial = np.abs(q[:, 2]) - half_height
    return np.hypot(np.maximum(radial, 0), np.maximum(axial, 0)) + np.minimum(np.maximum(radial, axial), 0)


def box_surface_samples(half: np.ndarray, step: float = 0.004) -> np.ndarray:
    half = np.asarray(half, dtype=float)
    out = []
    for axis in range(3):
        o = [i for i in range(3) if i != axis]
        g = [np.linspace(-half[i], half[i], max(2, int(math.ceil(2 * half[i] / step)) + 1)) for i in o]
        uu, vv = np.meshgrid(*g, indexing='ij')
        for sign in (-1, 1):
            pts = np.zeros((uu.size, 3))
            pts[:, axis] = sign * half[axis]
            pts[:, o[0]], pts[:, o[1]] = uu.ravel(), vv.ravel()
            out.append(pts)
    return np.concatenate(out)


def describe_presets() -> dict:
    """Planner-facing catalog (kept next to the implementation so it cannot drift)."""
    return {'sweep_curve_presets': CURVE_HELP, 'lathe_profile_presets': PROFILE_HELP}
