"""Two glass vessels with feathery water plants (the NE corner of the worktop, t = 150-160 s).

A clear cylindrical jar with a rounded shoulder and a round fishbowl, both
two-thirds full of water, each with a tangle of fine green stems carrying
whorls of needle leaves, filling the water and spilling just above the rim.
Built at real size. Local frame: origin bottom centre.
"""
import math
import random

import hand_lib as H


def build(B, mats, P):
    rnd = random.Random(11)
    glass = H.material("vessel_glass", "#F2F5F5", rough=0.02, transmission=1.0, ior=1.5, key="glass:#F2F5F5:0.02")
    water = H.material("vessel_water", "#E3EFEA", rough=0.0, transmission=1.0, ior=1.33, key="glass:#E3EFEA:0.0")
    green = H.material("water_plant", "#4C7A2C", rough=0.5, key="plastic:#4C7A2C:0.5")
    vessels = [("vase_body", (0.06, -0.035), [(0.0, 0.0), (0.042, 0.0), (0.06, 0.018), (0.068, 0.05), (0.064, 0.082),
                                             (0.052, 0.1), (0.046, 0.108), (0.048, 0.112)], 0.075),
               ("vase_body_jar", (-0.075, 0.045), [(0.0, 0.0), (0.05, 0.0), (0.056, 0.01), (0.056, 0.095),
                                                  (0.049, 0.118), (0.042, 0.124), (0.044, 0.13)], 0.085)]
    foliage_v, foliage_f = [], []
    for name, (cx, cy), outer, wl in vessels:
        inner = [(max(r - 0.003, 0.0), z + (0.003 if i == 0 else 0.0)) for i, (r, z) in enumerate(outer)][::-1]
        prof = outer + [(outer[-1][0] - 0.003, outer[-1][1])] + inner[1:]
        H.lathe_mesh(B, name, prof, glass, center=(cx, cy, 0.0), segments=48)
        wprof = [(0.0, 0.004)] + [(max(r - 0.0035, 0.001), z) for r, z in outer[1:] if z < wl] + [(0.0, wl)]
        wprof = [(r, z) for r, z in wprof]
        H.lathe_mesh(B, "water_body" if name == "vase_body" else "water_body_jar",
                     [(0.0, 0.004)] + [(max(r - 0.0035, 0.002), max(z, 0.004)) for r, z in outer[1:] if z < wl]
                     + [(max(outer[-2][0] - 0.004, 0.02), wl), (0.0, wl)], water, center=(cx, cy, 0.0), segments=40)
        # feathery stems: whorls of needles along gently curving stems
        rmax = max(r for r, _ in outer) - 0.012
        for sidx in range(26):
            a0 = rnd.uniform(0, 2 * math.pi)
            r0 = rnd.uniform(0, rmax * 0.7)
            p = [cx + r0 * math.cos(a0), cy + r0 * math.sin(a0), 0.006]
            heading = rnd.uniform(0, 2 * math.pi)
            top = rnd.uniform(wl - 0.01, outer[-1][1] + 0.035)
            n = 9
            for k in range(n):
                q = [p[0] + 0.006 * math.cos(heading), p[1] + 0.006 * math.sin(heading), p[2] + (top - 0.006) / n]
                rq = math.hypot(q[0] - cx, q[1] - cy)
                if rq > rmax:                      # stay inside the glass
                    q[0], q[1] = cx + (q[0] - cx) * rmax / rq, cy + (q[1] - cy) * rmax / rq
                heading += rnd.uniform(-0.6, 0.6)
                for w in range(6):                 # a whorl of needles
                    b = 2 * math.pi * w / 6 + k
                    L = 0.016 * (1 - 0.4 * k / n)
                    tip = (q[0] + L * math.cos(b), q[1] + L * math.sin(b), q[2] + 0.004)
                    i0 = len(foliage_v)
                    foliage_v += [(q[0], q[1], q[2] - 0.0012), (q[0], q[1], q[2] + 0.0012), tip]
                    foliage_f.append((i0, i0 + 1, i0 + 2))
                i0 = len(foliage_v)                # stem segment (a thin quad)
                foliage_v += [(p[0] - 0.0008, p[1], p[2]), (p[0] + 0.0008, p[1], p[2]),
                              (q[0] + 0.0008, q[1], q[2]), (q[0] - 0.0008, q[1], q[2])]
                foliage_f.append((i0, i0 + 1, i0 + 2, i0 + 3))
                p = q
    H.mesh_object(B, "plant_foliage", foliage_v, foliage_f, green, smooth_deg=0, closed=False)
    return B.objects_created()
