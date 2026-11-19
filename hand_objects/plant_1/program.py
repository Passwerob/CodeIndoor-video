"""Spider plant in a white ribbed pot (the NE corner of the worktop, t = 150-160 s).

A white glazed pot with vertical ribs, dark soil, and a rosette of ~64 long
narrow variegated leaves (green edges, cream centre) arching up and over, the
outer ones drooping past the rim. Built at its real size (~0.13 m pot).
Local frame: origin bottom centre.
"""
import math
import random

import hand_lib as H


def build(B, mats, P):
    rnd = random.Random(7)
    white = H.material("pot_white", "#F1F1EE", rough=0.18, coat=0.4, key="ceramic:#F1F1EE:0.18")
    # ---- ribbed pot: rings of radius r(z) + rib(theta) -------------------------
    nseg, ribs = 96, 18
    # outside up, over the rolled rim, inside wall down to the soil: an open pot
    prof = [(0.0, 0.050), (0.004, 0.054), (0.03, 0.058), (0.08, 0.064), (0.108, 0.068), (0.112, 0.071), (0.118, 0.071),
            (0.12, 0.066), (0.116, 0.062), (0.09, 0.058), (0.03, 0.050), (0.012, 0.046)]
    verts, faces = [], []
    for k, (z, r) in enumerate(prof):
        for i in range(nseg):
            a = 2 * math.pi * i / nseg
            outside = k <= 6
            rr = r + (0.0022 * (0.5 + 0.5 * math.cos(ribs * a)) if outside and 0.006 < z < 0.11 else 0.0)
            verts.append((rr * math.cos(a), rr * math.sin(a), z))
    for k in range(len(prof) - 1):
        for i in range(nseg):
            j = (i + 1) % nseg
            faces.append((k * nseg + i, k * nseg + j, (k + 1) * nseg + j, (k + 1) * nseg + i))
    faces.append(tuple(reversed(range(nseg))))                                  # bottom
    faces.append(tuple(range((len(prof) - 1) * nseg, len(prof) * nseg)))       # inner floor (under the soil)
    H.mesh_object(B, "pot_body", verts, faces, white, smooth_deg=60)
    B.cylinder("pot_soil", 0.059, 0.008, (0, 0, 0.098), "fabric:#2B2219:0.95", verts=40)
    # ---- leaves: tapered ribbons along arching curves -------------------------
    leaf = H.image_material("spider_leaf", P["tex_dir"] + "/leaf.png", rough=0.45, key="plastic:#4F7A2E:0.45")
    # the pot stands in the corner of the worktop: leaves grow towards the open
    # side, stop at the walls (bounds, object frame) and keep out of the jug and
    # the glass vessels beside it (avoid boxes: x0, x1, y0, y1, z_top)
    bnd = P.get("bounds") or {}
    avoid = P.get("avoid") or []
    open_dir = math.radians(P.get("open_azimuth_deg", -45.0))

    def blocked(q):
        if q[0] < bnd.get("xmin", -9) or q[0] > bnd.get("xmax", 9) or q[1] < bnd.get("ymin", -9) \
                or q[1] > bnd.get("ymax", 9) or q[2] < 0.002:
            return True
        r = math.hypot(q[0], q[1])
        if q[2] < 0.127 and 0.044 < r < 0.079:                   # through the pot wall / rim
            return True
        return any(b[0] <= q[0] <= b[1] and b[2] <= q[1] <= b[3] and q[2] <= b[4] for b in avoid)

    verts, faces, uvs = [], [], {}
    for n in range(64):
        az = open_dir + rnd.uniform(-1.0, 1.0) * math.radians(115)
        outer = n % 3 != 0
        L = rnd.uniform(0.20, 0.33) if outer else rnd.uniform(0.13, 0.21)
        rise = rnd.uniform(0.6, 1.1) if outer else rnd.uniform(1.0, 1.4)
        w0 = rnd.uniform(0.015, 0.021)
        twist = rnd.uniform(-0.4, 0.4)
        steps = 10
        pts = []
        for s_ in range(steps + 1):
            t = s_ / steps
            # angle from vertical grows along the leaf: up, out, then down
            th = (0.35 + 1.9 * t ** 1.3 * (1.6 - 0.45 * rise)) if outer else (0.25 + 1.1 * t ** 1.5)
            if s_ == 0:
                p = [0.012 * math.cos(az), 0.012 * math.sin(az), 0.103]       # out of the soil
            else:
                ds = L / steps
                p = [p[0] + ds * math.sin(th) * math.cos(az), p[1] + ds * math.sin(th) * math.sin(az),
                     p[2] + ds * math.cos(th)]
            if s_ > 0:                                 # the leaf ends where it (or its edges) would touch something
                w = w0 * (1 - 0.85 * t ** 1.4)
                sa = az + math.pi / 2 + twist * t
                if any(blocked([p[0] + e * w / 2 * math.cos(sa), p[1] + e * w / 2 * math.sin(sa), p[2]])
                       for e in (-1, 0, 1)):
                    break
            pts.append(list(p))
        if len(pts) < 4:
            continue
        m = len(pts) - 1
        start = len(verts)
        for s_, p in enumerate(pts):
            t = s_ / m
            w = w0 * (1 - 0.85 * t ** 1.4)
            sa = az + math.pi / 2 + twist * t
            for side in (-1, 1):
                verts.append((p[0] + side * w / 2 * math.cos(sa), p[1] + side * w / 2 * math.sin(sa), p[2]))
                uvs[len(verts) - 1] = ((side + 1) / 2, t * m / steps)
        for s_ in range(m):
            a = start + 2 * s_
            faces.append((a, a + 1, a + 3, a + 2))
    ob = H.mesh_object(B, "leaf_crown", verts, faces, leaf, smooth_deg=80, closed=False,
                       uvs=[uvs[i] for i in range(len(verts))])
    ob.modifiers.new("Thick", "SOLIDIFY").thickness = 0.0008
    return B.objects_created()
