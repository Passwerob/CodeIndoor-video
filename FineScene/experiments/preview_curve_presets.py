"""Matplotlib preview of the compiled aDSL primitives for every curve/profile preset.

    python FineScene/experiments/preview_curve_presets.py <repo> <out.png>

Run with the repo venv (adsl from <repo>/aDSL). Not a Blender
render: frustums/spheres are tessellated here directly from primitive params.
"""
import sys
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from mpl_toolkits.mplot3d.art3d import Poly3DCollection  # noqa: E402

REPO = Path(sys.argv[1]).resolve()
OUT = Path(sys.argv[2])
sys.path.insert(0, str(REPO / 'FineScene' / 'src'))
from general_scene import GeneralPlan, compile_local_geometry  # noqa: E402
from curve_presets import CURVE_HELP, PROFILE_HELP, EXPLICIT_PRESETS  # noqa: E402


def frame(axis):
    axis = axis / np.linalg.norm(axis)
    helper = np.array([1, 0, 0]) if abs(axis[0]) < .9 else np.array([0, 1, 0])
    u = np.cross(axis, helper); u /= np.linalg.norm(u)
    return u, np.cross(axis, u)


def tessellate(prim, n=20):
    p, M = prim['params'], np.asarray(prim.get('xform') if prim.get('xform') is not None else np.eye(4))
    quads = []
    if prim['type'] == 'cylinder':
        a, b = np.asarray(p['p0']), np.asarray(p['p1'])
        r0, r1 = p['radius'], p.get('radius_end', p['radius'])
        u, v = frame(b - a)
        ang = np.linspace(0, 2 * np.pi, n + 1)
        ring = lambda c, r: [c + r * (np.cos(t) * u + np.sin(t) * v) for t in ang]
        A, B = ring(a, r0), ring(b, r1)
        quads += [[A[i], A[i + 1], B[i + 1], B[i]] for i in range(n)]
        quads += [A[:-1], B[:-1]]
    elif prim['type'] == 'sphere':
        c, r = np.asarray(p['center']), p['radius']
        th, ph = np.linspace(0, np.pi, 7), np.linspace(0, 2 * np.pi, 11)
        P = lambda i, j: c + r * np.array([np.sin(th[i]) * np.cos(ph[j]), np.sin(th[i]) * np.sin(ph[j]), np.cos(th[i])])
        quads += [[P(i, j), P(i, j + 1), P(i + 1, j + 1), P(i + 1, j)] for i in range(6) for j in range(10)]
    elif prim['type'] == 'cube':
        c, s = np.asarray(p['center']), np.asarray(p['scale']) / 2
        corners = lambda idx: [c + s * np.array(k) for k in idx]
        for ax in range(3):
            for sign in (-1, 1):
                o = [i for i in range(3) if i != ax]
                pts = []
                for su, sv in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
                    k = [0, 0, 0]; k[ax] = sign; k[o[0]] = su; k[o[1]] = sv
                    pts.append(k)
                quads.append(corners(pts))
    out = []
    for q in quads:
        q = np.asarray(q)
        out.append((np.c_[q, np.ones(len(q))] @ M.T)[:, :3])
    return out


def build(part, size):
    plan = GeneralPlan.model_validate({'scene_id': 'preview', 'description': 'preset preview', 'extent': [3, 3, 3],
        'objects': [{'id': 'm', 'semantic': 'preview', 'size': list(size),
                     'parts': [{'id': 'p', 'size': list(size), 'center': [0, 0, size[2] / 2],
                                'color': [.55, .42, .3], **part}]}]})
    shape = compile_local_geometry(plan, plan.objects[0])
    shape = shape[0] if isinstance(shape, tuple) else shape
    return [q for prim in shape.iter_primitives() for q in tessellate(prim)]


cases = []
for preset in CURVE_HELP:
    curve = {'preset': preset}
    size = (.6, .12, .8)
    if preset in EXPLICIT_PRESETS:
        curve['points'] = [[-.2, 0, -.37], [0, 0, .35], [.2, 0, -.1], [.25, 0, .3]]
    if preset == 'helix':
        curve['plane'] = 'xy'; size = (.4, .4, .8)
    part = {'primitive': 'sweep', 'radius': .03, 'curve': curve}
    if preset == 'cabriole':
        part.update(radius=.045, radius_end=.018); size = (.3, .12, .8)
    cases.append((f'sweep · {preset}', part, size))
cases.append(('sweep · taper (bezier)', {'primitive': 'sweep', 'radius': .05, 'radius_end': 0,
              'curve': {'preset': 'bezier', 'points': [[-.25, 0, -.35], [.3, 0, -.3], [-.2, 0, .1], [.2, 0, .35]]}}, (.6, .12, .8)))
for preset in PROFILE_HELP:
    cases.append((f'lathe · {preset}', {'primitive': 'lathe', 'profile': {'preset': preset}}, (.4, .4, .8)))

cols = 6
rows = int(np.ceil(len(cases) / cols))
fig = plt.figure(figsize=(cols * 3.0, rows * 3.3))
for i, (title, part, size) in enumerate(cases):
    ax = fig.add_subplot(rows, cols, i + 1, projection='3d')
    polys = build(part, size)
    ax.add_collection3d(Poly3DCollection(polys, facecolor=(.62, .48, .33), edgecolor=(.3, .22, .15, .25), linewidths=.15))
    pts = np.concatenate(polys)
    lo, hi = pts.min(0), pts.max(0); c = (lo + hi) / 2; r = (hi - lo).max() / 2
    ax.set_xlim(c[0] - r, c[0] + r); ax.set_ylim(c[1] - r, c[1] + r); ax.set_zlim(c[2] - r, c[2] + r)
    view = (8, -60) if part['primitive'] == 'lathe' else ((35, -60) if 'helix' in title else (12, -88))
    ax.view_init(*view); ax.set_axis_off(); ax.set_title(title, fontsize=10)
plt.tight_layout()
fig.savefig(OUT, dpi=80)
print('saved', OUT, len(cases), 'cases')
