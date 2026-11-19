"""Pink bucket with a rag over its rim (beside the dispenser, t = 0-14 s, 232 s).

Salmon-pink moulded pail, tapered, with a rolled rim; a teal-blue cloth folded
over the front-left of the rim, hanging inside and out. (The white tube lying in
it is the dispenser's drain hose, modelled with the dispenser.)
Built at real size. Local frame: front = -y, origin bottom centre.
"""
import math
import random

import hand_lib as H


def build(B, mats, P):
    rnd = random.Random(3)
    pink = H.material("bucket_pink", "#D98F93", rough=0.45, coat=0.1, key="plastic:#D98F93:0.45")
    h, rb, rt = 0.26, 0.112, 0.138
    prof = [(0.0, 0.0), (rb - 0.006, 0.0), (rb, 0.008), (rt, h - 0.012), (rt + 0.006, h - 0.006), (rt + 0.004, h),
            (rt - 0.004, h - 0.002), (rt - 0.006, h - 0.012), (rb - 0.006, 0.012), (0.0, 0.012)]
    H.lathe_mesh(B, "bucket_body", prof, pink, segments=64)
    H.lathe_mesh(B, "rim_roll", [(rt - 0.002, h - 0.016), (rt + 0.008, h - 0.012), (rt + 0.01, h - 0.004),
                                 (rt + 0.004, h + 0.001), (rt - 0.003, h - 0.004)], pink, segments=64)
    # rag: a sheet folded over the rim around azimuth a0, wrinkled
    a0, span = math.radians(-70), 0.17                # front, away from the pier (t = 0-1 s)
    nu, nv = 10, 22
    verts, faces = [], []
    for j in range(nv + 1):
        v = j / nv
        for i in range(nu + 1):
            u = (i / nu - 0.5) * span
            a = a0 + u / rt
            if v < 0.25:                           # inside the bucket, hanging down
                r, z = rt - 0.01, h - 0.10 * (0.25 - v) / 0.25 * 1.0
            elif v < 0.4:                          # over the rim
                f = (v - 0.25) / 0.15
                r, z = rt - 0.01 + 0.03 * f, h + 0.012 * math.sin(math.pi * f)
            else:                                  # outside, down the wall, flaring
                f = (v - 0.4) / 0.6
                r, z = rt + 0.02 + 0.01 * f, h - 0.17 * f
            wob = 0.006 * math.sin(i * 1.7 + j * 0.9) + 0.003 * rnd.uniform(-1, 1)
            verts.append(((r + wob) * math.cos(a), (r + wob) * math.sin(a), z))
    for j in range(nv):
        for i in range(nu):
            k = j * (nu + 1) + i
            faces.append((k, k + 1, k + nu + 2, k + nu + 1))
    ob = H.mesh_object(B, "cloth", verts, faces, "fabric:#5C9EAB:0.85", smooth_deg=80, closed=False)
    ob.modifiers.new("Thick", "SOLIDIFY").thickness = 0.002
    return B.objects_created()
