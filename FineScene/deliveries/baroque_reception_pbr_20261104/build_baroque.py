"""Build a West-European Baroque reception room in Blender 5.2.

Real CC0 PBR textures from Poly Haven + deterministic parametric ornament
(cabriole legs, acanthus, volutes, fluted Corinthian columns, dentil cornices).
NO TRELLIS, no retrieved meshes, no generated 3D models.

Run:  blender -b --python build_baroque.py
      (needs textures/ from fetch_textures.py; cloth_drape.py is vendored here)
Out:  baroque_reception.blend, scene_parts.json, renders/*.png
"""
import bpy
import json
import math
import os
import sys
from mathutils import Vector

ROOT = os.path.dirname(os.path.abspath(__file__))
QUALITY = os.environ.get("BAROQUE_QUALITY", "preview").lower()
IS_PREVIEW = QUALITY != "final"
sys.path.insert(0, ROOT)
# cloth_drape.py is vendored next to this file (copied from DirectAgent/src).
from cloth_drape import DrapeSimulator, simulation_summary  # noqa: E402
from baroque_lib import (Builder, build_material, solid_material, volute,
                        cabriole_leg, acanthus_leaf, fluted_column,
                        corinthian_capital, dentil_row, torus,
                        MAT_CFG, FALLBACK_COLORS)  # noqa

# ---------------------------------------------------------------- clean scene
bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)
for c in list(bpy.data.collections):
    if c.users == 0:
        bpy.data.collections.remove(c)
scene = bpy.context.scene

with open(os.path.join(ROOT, "texture_manifest.json"), encoding="utf-8") as fh:
    TEX_MANIFEST = json.load(fh)

mats = {k: build_material(k, TEX_MANIFEST) for k in MAT_CFG}
mats["mirror"] = solid_material("AgedMirrorSilver", (0.26, 0.28, 0.31), 0.95, 0.075)
mats["glass"] = solid_material("WindowGlass", (0.72, 0.82, 0.90), 0.0, 0.06)
gp = next(n for n in mats["glass"].node_tree.nodes if n.type == "BSDF_PRINCIPLED")
gp.inputs["Transmission Weight"].default_value = 0.92
gp.inputs["IOR"].default_value = 1.46
mats["crystal"] = solid_material("CrystalDrop", (0.92, 0.96, 1.0), 0.0, 0.03)
cp = next(n for n in mats["crystal"].node_tree.nodes if n.type == "BSDF_PRINCIPLED")
cp.inputs["Transmission Weight"].default_value = 0.95
cp.inputs["IOR"].default_value = 1.55
mats["flame"] = solid_material("CandleFlame", (1.0, 0.66, 0.26), 0.0, 0.5,
                               emission=(1.0, 0.48, 0.14, 3.0))
mats["ember"] = solid_material("FireEmbers", (1.0, 0.35, 0.08), 0.0, 0.8,
                               emission=(1.0, 0.26, 0.05, 3.2))
mats["skylight"] = solid_material("ExteriorSky", (0.72, 0.82, 0.95), 0.0, 1.0,
                                  emission=(0.62, 0.74, 0.92, 0.55))
mats["soot"] = solid_material("FireboxSoot", (0.055, 0.045, 0.042), 0.0, 0.92)

B = Builder(scene, mats)
SIM = DrapeSimulator(B, scene, mats)

W, D, H = 8.0, 6.4, 3.60           # interior extent
HW, HD = W/2, D/2
WALL = 0.22
GOLD = "gilded_bronze"
DARK = "boiserie_dark"
WALNUT = "boiserie_walnut"
ROSE = "furniture_rosewood"
MARBLE = "marble_cream"
VELVET = "velvet_crimson"
DAMASK = "damask_crimson"
STUCCO = "stucco_white"
PARQUET = "floor_parquet"
RELIEF = "relief_plaster"


def V(*a):
    return tuple(float(x) for x in a)


# ============================================================ ARCHITECTURE ===
B.begin("room_shell", "ArchitecturalShell", (0, 0, 0), 0, "structure",
        "8.0 x 6.4 x 3.6 m enclosure; real door and window apertures, herringbone parquet")

# floor / ceiling
B.box("floor_parquet", (W+0.4, D+0.4, 0.20), (0, 0, -0.10), PARQUET)
B.box("ceiling_plain", (W+0.4, D+0.4, 0.18), (0, 0, H+0.09), STUCCO)
# Real coffer: relief-plaster field with four projecting gilt rails, not stacked slabs
B.box("ceiling_coffer_outer", (4.34, 3.34, 0.060), (0, 0.20, H-0.030), RELIEF)
B.box("ceiling_coffer_field", (3.86, 2.86, 0.045), (0, 0.20, H-0.090), RELIEF)
for sgn in (-1, 1):
    B.box("coffer_rail_x_%d" % sgn, (0.13, 3.18, 0.065),
          (sgn*2.05, 0.20, H-0.125), GOLD)
    B.box("coffer_rail_y_%d" % sgn, (4.23, 0.13, 0.065),
          (0, 0.20+sgn*1.525, H-0.125), GOLD)
    B.box("coffer_inner_x_%d" % sgn, (0.075, 2.50, 0.045),
          (sgn*1.68, 0.20, H-0.155), GOLD)
    B.box("coffer_inner_y_%d" % sgn, (3.44, 0.075, 0.045),
          (0, 0.20+sgn*1.21, H-0.155), GOLD)
torus(B, "ceiling_rose", (0, 0.20, H-0.175), 0.46, 0.040, GOLD, 56, 8)
for i in range(12):
    a = 2*math.pi*i/12
    pts = [(r*math.cos(a), 0.20+r*math.sin(a), H-0.172) for r in (0.20, 0.34, 0.50)]
    B.tube("ceiling_rose_ray_%02d" % i, pts, [0.020, 0.016, 0.010], GOLD)

# perimeter walls: south with a real 1.9 x 2.6 m door opening
B.box("wall_south_left", (HW-0.95, WALL, H), (-(0.95+HW)/2, -HD-WALL/2, H/2), STUCCO)
B.box("wall_south_right", (HW-0.95, WALL, H), ((0.95+HW)/2, -HD-WALL/2, H/2), STUCCO)
B.box("wall_south_lintel", (1.90, WALL, H-2.60), (0, -HD-WALL/2, (H+2.60)/2), STUCCO)
# north wall (fireplace + mirror backdrop)
B.box("wall_north", (W+0.44, WALL, H), (0, HD+WALL/2, H/2), STUCCO)
# east and west walls, each with two real arched window apertures
for side, sx in (("east", 1), ("west", -1)):
    x = sx*(HW+WALL/2)
    segs = [(-HD, -2.35), (2.35, HD)]
    for i, (a, b) in enumerate(segs):
        B.box("wall_%s_solid_%d" % (side, i), (WALL, b-a, H), (x, (a+b)/2, H/2), STUCCO)
    mid = ((-2.35 + -1.05)/2, (1.05 + 2.35)/2)
    for i, cy in enumerate(mid):
        # solid zone between the two windows
        pass
    B.box("wall_%s_middle" % side, (WALL, 2.10, H), (x, 0, H/2), STUCCO)
    for i, cy in enumerate((-1.70, 1.70)):
        # below sill and above header for a 1.30 m wide x 1.85 m tall aperture
        B.box("wall_%s_win_%d_sill" % (side, i), (WALL, 1.30, 0.95), (x, cy, 0.475), STUCCO)
        B.box("wall_%s_win_%d_head" % (side, i), (WALL, 1.30, H-2.80), (x, cy, (H+2.80)/2), STUCCO)
        for j, yy in enumerate((cy-0.65-0.30, cy+0.65+0.30)):
            pass
    # narrow piers beside each window
    for yy in (-2.35-0.30, -1.05+0.30, 1.05-0.30, 2.35+0.30):
        pass

# skirting / chair rail / multi-step cornice
for axis, sign in (("y", -1), ("y", 1), ("x", -1), ("x", 1)):
    if axis == "y":
        loc = (0, sign*HD, 0)
        size = (W, 0.10, 0.16)
    else:
        loc = (sign*HW, 0, 0)
        size = (0.10, D, 0.16)
    B.box("skirting_%s_%d" % (axis, sign), size, (loc[0], loc[1], 0.08), DARK)
    B.box("skirting_gilt_%s_%d" % (axis, sign),
          (size[0], size[1], 0.022), (loc[0], loc[1], 0.172), GOLD)
    B.box("chairrail_%s_%d" % (axis, sign),
          (size[0], size[1], 0.075), (loc[0], loc[1], 1.16), WALNUT)
    B.box("chairrail_gilt_%s_%d" % (axis, sign),
          (size[0], size[1], 0.020), (loc[0], loc[1], 1.208), GOLD)
    for k, (zz, off, hh) in enumerate(((3.16, 0.10, 0.075),
                                       (3.25, 0.16, 0.070),
                                       (3.34, 0.22, 0.065))):
        if axis == "y":
            B.box("cornice_%s_%d_%d" % (axis, sign, k),
                  (W+2*off, 0.10+off, hh),
                  (0, sign*(HD+off/2), zz), STUCCO if k == 0 else WALNUT)
        else:
            B.box("cornice_%s_%d_%d" % (axis, sign, k),
                  (0.10+off, D+2*off, hh),
                  (sign*(HW+off/2), 0, zz), STUCCO if k == 0 else WALNUT)
    if axis == "y":
        dentil_row(B, "dentil_y_%d" % sign, (-HW, sign*(HD+0.11)),
                   (HW, sign*(HD+0.11)), 3.075, GOLD, 30)
    else:
        dentil_row(B, "dentil_x_%d" % sign, (sign*(HW+0.11), -HD),
                   (sign*(HW+0.11), HD), 3.075, GOLD, 24)


# ============================================================== BOISERIE ====
B.begin("boiserie", "BoiseriePanels", (0, 0, 0), 0, "structure",
        "Raised walnut panelling with crimson damask upholstered inserts and gilt fillets")


def panel_run(prefix, x0, y0, x1, y1, normal_axis, count):
    """A run of framed panels along a wall."""
    for i in range(count):
        t0, t1 = i/count, (i+1)/count
        cx = x0+(x1-x0)*(t0+t1)/2
        cy = y0+(y1-y0)*(t0+t1)/2
        span = math.hypot(x1-x0, y1-y0)/count
        if normal_axis == "x":
            size = (0.045, span-0.10, 0.86)
            loc = (cx, cy, 0.62)
            frame_h = (0.055, span-0.10, 0.045)
        else:
            size = (span-0.10, 0.045, 0.86)
            loc = (cx, cy, 0.62)
            frame_h = (span-0.10, 0.055, 0.045)
        B.box("%s_insert_%02d" % (prefix, i), size, loc, DAMASK)
        B.box("%s_frame_low_%02d" % (prefix, i), frame_h,
              (loc[0], loc[1], 0.215), WALNUT)
        B.box("%s_frame_high_%02d" % (prefix, i), frame_h,
              (loc[0], loc[1], 1.025), WALNUT)
        if i % 2 == 0:
            # small gilt corner scrolls on alternate panels
            volute(B, "%s_scroll_%02d" % (prefix, i),
                   (loc[0], loc[1], 0.62), 0.10, 0.018, 0.012, GOLD,
                   plane="XY" if normal_axis == "z" else "XZ", turns=1.05, n=28)


# Walls lie in XY; inserts face into the room along X or Y.
for side, sx in (("east", 1), ("west", -1)):
    x = sx*(HW-0.045)
    for i in range(7):
        cy = -2.72 + i*0.905
        B.box("pan_%s_%02d" % (side, i), (0.045, 0.80, 0.86), (x, cy, 0.62), DAMASK)
        B.box("pan_%s_%02d_low" % (side, i), (0.060, 0.86, 0.050), (x, cy, 0.215), WALNUT)
        B.box("pan_%s_%02d_high" % (side, i), (0.060, 0.86, 0.050), (x, cy, 1.025), WALNUT)
        for zz in (0.30, 0.94):
            B.box("pan_%s_%02d_gilt_%.2f" % (side, i, zz),
                  (0.066, 0.80, 0.014), (x, cy, zz), GOLD)
for i in range(8):
    cx = -3.45 + i*0.99
    B.box("pan_north_%02d" % i, (0.87, 0.045, 0.86), (cx, HD-0.045, 0.62), DAMASK)
    B.box("pan_north_%02d_low" % i, (0.93, 0.060, 0.050), (cx, HD-0.045, 0.215), WALNUT)
    B.box("pan_north_%02d_high" % i, (0.93, 0.060, 0.050), (cx, HD-0.045, 1.025), WALNUT)
for i in range(3):
    for sgn in (-1, 1):
        cx = sgn*(1.45 + i*0.86)
        B.box("pan_south_%d_%02d" % (sgn, i), (0.76, 0.045, 0.86),
              (cx, -HD+0.045, 0.62), DAMASK)
        B.box("pan_south_%d_%02d_low" % (sgn, i), (0.82, 0.060, 0.050),
              (cx, -HD+0.045, 0.215), WALNUT)
        B.box("pan_south_%d_%02d_high" % (sgn, i), (0.82, 0.060, 0.050),
              (cx, -HD+0.045, 1.025), WALNUT)


# =============================================================== COLUMNS ====
B.begin("columns", "CorinthianOrder", (0, 0, 0), 0, "structure",
        "Fluted shafts with acanthus capitals and volutes; eight around the room")
COLUMN_SPOTS = []
for sx in (-1, 1):
    for cy in (-2.70, 0.0, 2.70):
        COLUMN_SPOTS.append((sx*(HW-0.28), cy))
COLUMN_SPOTS += [(-1.62, HD-0.30), (1.62, HD-0.30)]
for idx, (cx, cy) in enumerate(COLUMN_SPOTS):
    tag = "col_%02d" % idx
    B.box(tag+"_plinth", (0.50, 0.50, 0.30), (cx, cy, 0.15), MARBLE)
    B.box(tag+"_plinth_gilt", (0.52, 0.52, 0.022), (cx, cy, 0.31), GOLD)
    fluted_column(B, tag+"_shaft", (cx, cy, 0.32), 2.62, 0.155, MARBLE, flutes=14)
    corinthian_capital(B, tag+"_capital", (cx, cy, 2.94), 0.155, MARBLE, GOLD)


# ===================================================== FIREPLACE + MIRROR ====
B.begin("fireplace", "MarbleFireplace", (0, HD-0.30, 0), 0, "structure",
        "Cream marble chimneypiece with scrolled trusses, dentil mantel and glowing firebox")
FY = 0.0   # local: root already at the north wall
B.box("firebox_recess", (1.34, 0.30, 0.86), (0, -0.06, 0.48), "soot")
B.box("firebox_back", (1.26, 0.04, 0.78), (0, -0.20, 0.46), "soot")
for i in range(7):
    B.box("fire_log_%02d" % i, (0.72, 0.075, 0.075),
          (-0.05 + (i % 3)*0.05, -0.10, 0.10 + i*0.045), DARK,
          yaw=i*11)
for i in range(7):
    B.sphere("ember_%02d" % i, 0.020 + (i % 3)*0.005,
             (-0.40 + i*0.13, -0.12, 0.11 + (i % 4)*0.055), "ember")
for i in range(4):
    B.sphere("flame_%02d" % i, 0.038 + (i % 2)*0.010,
             (-0.30 + i*0.20, -0.13, 0.20 + (i % 3)*0.045), "flame",
             scale=(0.62, 0.55, 1.85))
B.box("fireplace_base", (2.00, 0.46, 0.20), (0, 0.04, 0.10), MARBLE)
for sgn in (-1, 1):
    B.box("fireplace_jamb_%d" % sgn, (0.24, 0.44, 1.02),
          (sgn*0.80, 0.02, 0.61), MARBLE)
    B.box("fireplace_jamb_gilt_%d" % sgn, (0.028, 0.46, 0.96),
          (sgn*0.66, 0.03, 0.61), GOLD)
    volute(B, "fireplace_truss_%d" % sgn, (sgn*0.80, 0.26, 0.38),
           0.22, 0.030, 0.045, GOLD, plane="YZ", turns=1.35, sign=sgn, n=44)
    acanthus_leaf(B, "fireplace_acanthus_%d" % sgn,
                  (sgn*0.80, 0.26, 0.82), 0.26, 0.10, GOLD, tilt=0.20)
B.box("fireplace_frieze", (1.90, 0.46, 0.30), (0, 0.02, 1.18), MARBLE)
B.box("fireplace_frieze_gilt", (1.80, 0.47, 0.022), (0, 0.03, 1.24), GOLD)
B.box("fireplace_mantel", (2.24, 0.62, 0.13), (0, 0.10, 1.40), MARBLE)
B.box("fireplace_mantel_step", (2.36, 0.68, 0.055), (0, 0.10, 1.485), MARBLE)
B.box("fireplace_mantel_gilt", (2.38, 0.69, 0.016), (0, 0.10, 1.520), GOLD)
for i in range(15):
    B.box("fireplace_dentil_%02d" % i, (0.070, 0.07, 0.065),
          (-1.00 + i*0.143, 0.36, 1.315), GOLD)
for i in range(5):
    B.sphere("mantel_vase_%d" % i, 0.075, (-0.78 + i*0.39, 0.08, 1.60), GOLD,
             scale=(1.0, 1.0, 1.35))

B.begin("gilt_mirror", "GiltMirror", (0, HD-0.06, 0), 0, "detail",
        "2.15 x 2.45 m pier glass: silvered field, multi-step gilt frame, corner volutes, shell crest")
# Re-proportioned pier glass. The old frame spanned z=1.28..3.92 in a 3.6 m
# room: its lower rail drove straight through the mantel (z=1.335..1.528)
# and its crest punched through the ceiling. New envelope is 1.58..3.58 m.
MZ = 2.50
B.box("mirror_field", (1.98, 0.035, 1.68), (0, -0.03, MZ), "mirror")
for sgn in (-1, 1):
    B.box("mirror_stile_%d" % sgn, (0.13, 0.10, 1.84),
          (sgn*1.055, -0.02, MZ), GOLD)
    B.box("mirror_stile_inner_%d" % sgn, (0.035, 0.12, 1.70),
          (sgn*0.965, -0.01, MZ), GOLD)
for zz in (1.66, 3.34):
    B.box("mirror_rail_%.2f" % zz, (2.24, 0.10, 0.13), (0, -0.02, zz), GOLD)
    B.box("mirror_rail_inner_%.2f" % zz, (2.00, 0.12, 0.035), (0, -0.01, zz), GOLD)
for i in range(20):
    B.box("mirror_bead_%02d" % i, (0.038, 0.038, 0.038),
          (-0.95 + i*0.10, -0.055, 3.28), GOLD)
for sgn in (-1, 1):
    for zz in (1.80, 3.24):
        volute(B, "mirror_volute_%d_%.2f" % (sgn, zz),
               (sgn*1.06, -0.06, zz), 0.20, 0.028, 0.030, GOLD,
               plane="XZ", turns=1.25, sign=sgn, n=40)
# shell crest: fan of acanthus leaves + central palmette
for i in range(9):
    t = (i-4)/4.0
    acanthus_leaf(B, "mirror_crest_leaf_%02d" % i,
                  (t*0.36, -0.07, 3.36), 0.20*(1.0-0.32*abs(t)), 0.075,
                  GOLD, tilt=0.18*t, yaw=-t*0.42)
B.sphere("mirror_crest_boss", 0.075, (0, -0.10, 3.44), GOLD, scale=(1.3, 0.6, 1.0))
volute(B, "mirror_crest_crown", (0, -0.10, 3.44), 0.14, 0.025, 0.028,
       GOLD, plane="XZ", turns=1.45, n=44)


# ================================================================= WINDOWS ===
B.begin("windows", "ArchedWindows", (0, 0, 0), 0, "structure",
        "Four arched apertures with real glazing, gilt glazing bars and crimson velvet drapery")
for side, sx in (("east", 1), ("west", -1)):
    xw = sx*(HW+0.02)
    for wi, cy in enumerate((-1.70, 1.70)):
        tag = "%s_win_%d" % (side, wi)
        B.box(tag+"_glass", (0.022, 1.24, 1.78), (xw, cy, 1.88), "glass")
        B.box(tag+"_sky", (0.010, 1.30, 1.84), (xw+sx*0.20, cy, 1.88), "skylight")
        for yy in (cy-0.65, cy+0.65):
            B.box(tag+"_jamb_%.2f" % yy, (0.11, 0.085, 1.86), (xw, yy, 1.88), GOLD)
        for zz in (0.98, 2.78):
            B.box(tag+"_rail_%.2f" % zz, (0.11, 1.36, 0.070), (xw, cy, zz), GOLD)
        B.box(tag+"_mullion", (0.085, 0.050, 1.80), (xw, cy, 1.88), GOLD)
        B.box(tag+"_transom", (0.085, 1.30, 0.045), (xw, cy, 2.12), GOLD)
        # arched head: a half-ring of small gilt blocks
        for k in range(11):
            a = math.pi * k/10
            B.box(tag+"_arch_%02d" % k, (0.10, 0.10, 0.075),
                  (xw, cy + 0.62*math.cos(a), 2.78 + 0.30*math.sin(a)), GOLD)
        # Heavy velvet drapery: REAL Blender CLOTH simulation. A single-layer
        # source mesh with a pinned top edge is solved under gravity, then
        # baked into an independent aDSL part by DrapeSimulator.bake().
        # The hidden pinned source stays as a deterministic re-run control.
        # See cloth_drape.py (next to this file).
        for s2 in (-1, 1):
            folds, depth, width = 6, 0.075, 0.60
            z0, z1 = 0.090, 2.82
            NY, NZ = 16, 20
            verts, faces = [], []
            for j in range(NZ + 1):
                v = j / NZ
                gather = 0.30 + 0.70 * abs(2*v - 1) ** 1.3
                for i in range(NY + 1):
                    u = i / NY
                    yy = cy + s2*(0.62 + u*width*gather)
                    xx = xw - sx*(0.12 + depth*math.sin(
                        2*math.pi*folds*u)*(0.5 + 0.5*gather))
                    verts.append((xx, yy, z0 + v*(z1-z0)))
            stride = NY + 1
            for j in range(NZ):
                for i in range(NY):
                    a = j*stride + i
                    faces.append((a, a+1, a+stride+1, a+stride))
            pinned = [NZ*stride + i for i in range(NY + 1)]
            SIM.add("%s_drape_%d_%d" % (tag, s2, wi), verts, faces, VELVET,
                    {"folds": folds, "depth_m": depth, "width_m": width,
                     "z0_m": z0, "z1_m": z1, "gather_min": 0.30,
                     "segments_y": NY, "segments_z": NZ,
                     "pinned_edge": "top",
                     "note": "real Blender CLOTH simulation baked to an "
                             "independent aDSL part"}, pinned)
            B.box(tag+"_tieback_%d" % s2, (0.16, 0.16, 0.055),
                  (xw - sx*0.16, cy + s2*0.86, 1.42), GOLD)
        B.box(tag+"_pelmet", (0.16, 1.72, 0.20), (xw - sx*0.12, cy, 2.92), VELVET)
        B.box(tag+"_rod", (0.055, 1.80, 0.055), (xw - sx*0.20, cy, 3.00), GOLD)
        for s2 in (-1, 1):
            B.sphere(tag+"_finial_%d" % s2, 0.062,
                     (xw - sx*0.20, cy + s2*0.90, 3.00), GOLD)

# All drape sources exist now: run ONE shared cloth simulation and bake.
CLOTH_FRAMES = 24 if IS_PREVIEW else 60
BAKED_DRAPES = SIM.bake(frames=CLOTH_FRAMES)
with open(os.path.join(ROOT, "cloth_simulation.json"), "w", encoding="utf-8") as fh:
    json.dump(simulation_summary(SIM, CLOTH_FRAMES), fh, indent=2,
              ensure_ascii=False)
print("CLOTH_BAKED", len(BAKED_DRAPES), "frames", CLOTH_FRAMES, flush=True)


# ==================================================================== DOOR ===
B.begin("entrance", "DoubleDoor", (0, -HD-0.02, 0), 0, "structure",
        "1.9 m double door with raised walnut panels, gilt fillets and a scrolled pediment")
B.box("door_frame_back", (2.10, 0.14, 2.78), (0, 0.02, 1.39), WALNUT)
for sgn in (-1, 1):
    B.box("door_leaf_%d" % sgn, (0.90, 0.075, 2.52),
          (sgn*0.465, -0.05, 1.26), DARK)
    for zz in (0.52, 1.35, 2.08):
        B.box("door_panel_%d_%.2f" % (sgn, zz), (0.68, 0.085, 0.56),
              (sgn*0.465, -0.10, zz), DAMASK)
        B.box("door_panel_gilt_%d_%.2f" % (sgn, zz), (0.72, 0.090, 0.022),
              (sgn*0.465, -0.11, zz+0.29), GOLD)
    B.sphere("door_knob_%d" % sgn, 0.048, (sgn*0.085, -0.15, 1.10), GOLD)
for sgn in (-1, 1):
    B.box("door_jamb_%d" % sgn, (0.14, 0.20, 2.70), (sgn*1.02, -0.04, 1.35), GOLD)
B.box("door_lintel", (2.18, 0.20, 0.15), (0, -0.04, 2.70), GOLD)
B.box("door_pediment_base", (2.36, 0.24, 0.11), (0, -0.05, 2.84), MARBLE)
for sgn in (-1, 1):
    volute(B, "door_pediment_volute_%d" % sgn,
           (sgn*0.86, -0.10, 2.99), 0.30, 0.035, 0.048, GOLD,
           plane="XZ", turns=1.30, sign=sgn, n=46)
for i in range(7):
    t = (i-3)/3.0
    acanthus_leaf(B, "door_pediment_leaf_%02d" % i,
                  (t*0.42, -0.11, 2.90), 0.27*(1-0.25*abs(t)), 0.070,
                  GOLD, tilt=0.16*t, yaw=-t*0.35)


# ============================================================ WALL TAPESTRY ===
B.begin("wall_panels", "GiltTapestryPanels", (0, 0, 0), 0, "detail",
        "Eight crimson damask tapestry panels in carved gilt frames above the boiserie")
PANEL_SPOTS = []
for x in (-2.65, 2.65):
    PANEL_SPOTS.append((x, HD-0.075, "north"))
    PANEL_SPOTS.append((x, -HD+0.075, "south"))
for i, (px, py, orient) in enumerate(PANEL_SPOTS):
    tag = "tapestry_%02d" % i
    pz = 2.18
    inward = -1 if orient == "north" else 1
    B.box(tag+"_field", (1.45, 0.045, 1.30), (px, py, pz), DAMASK)
    for s in (-1, 1):
        B.box(tag+"_rail_v_%d" % s, (0.070, 0.075, 1.42),
              (px+s*0.76, py, pz), GOLD)
        B.box(tag+"_rail_h_%d" % s, (1.59, 0.075, 0.070),
              (px, py, pz+s*0.69), GOLD)
    B.box(tag+"_inner_fillet", (1.37, 0.052, 0.020),
          (px, py+inward*0.030, pz+0.63), GOLD)
    for s in (-1, 1):
        volute(B, tag+"_crest_%d" % s,
               (px+s*0.68, py, pz+0.76), 0.13, 0.020, 0.022, GOLD,
               plane="XZ", turns=1.05, sign=s, n=30)
    for j in range(9):
        B.box(tag+"_bead_%02d" % j, (0.045, 0.045, 0.045),
              (px-0.66+j*0.165, py+inward*0.035, pz-0.69), GOLD)


# ================================================================= FURNITURE ===
def sofa(oid, label, pos, yaw, seats=3):
    B.begin(oid, label, pos, yaw, "furniture",
            "Velvet upholstered canape: carved rosewood frame, cabriole legs, shell crest")
    width = 0.78*seats + 0.30
    B.box("frame_base", (width, 0.92, 0.20), (0, 0, 0.34), ROSE, bevel=0.02)
    B.box("frame_base_gilt", (width+0.02, 0.94, 0.022), (0, 0, 0.455), GOLD)
    for i in range(seats):
        cx = -0.39*(seats-1) + i*0.78
        B.box("seat_cushion_%02d" % i, (0.74, 0.76, 0.16),
              (cx, -0.02, 0.54), VELVET, bevel=0.035)
        B.box("back_cushion_%02d" % i, (0.72, 0.14, 0.70),
              (cx, 0.40, 0.94), DAMASK, bevel=0.03)
    B.box("back_frame", (width, 0.14, 0.94), (0, 0.47, 0.92), ROSE, bevel=0.02)
    B.box("back_frame_gilt_top", (width+0.03, 0.16, 0.030), (0, 0.47, 1.40), GOLD)
    for sgn in (-1, 1):
        B.box("arm_%d" % sgn, (0.19, 0.90, 0.34),
              (sgn*(width/2-0.09), -0.01, 0.68), ROSE, bevel=0.03)
        B.box("arm_pad_%d" % sgn, (0.20, 0.78, 0.10),
              (sgn*(width/2-0.09), -0.02, 0.86), VELVET, bevel=0.035)
        volute(B, "arm_scroll_%d" % sgn,
               (sgn*(width/2-0.08), -0.38, 0.78), 0.16, 0.022, 0.032,
               GOLD, plane="YZ", turns=1.20, sign=sgn, n=36)
    for sgn in (-1, 1):
        for yy in (-0.34, 0.34):
            cabriole_leg(B, "leg_%d_%.2f" % (sgn, yy),
                         (sgn*(width/2-0.16), yy, 0.0), 0.34, ROSE, sway=0.045)
    for i in range(3):
        t = i-1
        acanthus_leaf(B, "crest_leaf_%02d" % i,
                      (t*0.115, 0.47, 1.42), 0.20*(1-0.18*abs(t)), 0.055,
                      GOLD, tilt=0.10*t)
    B.sphere("crest_boss", 0.065, (0, 0.45, 1.45), GOLD, scale=(1.2, 0.55, 1.0))
    crest_pts = [(-width/2 + width*i/24, 0.47,
                  1.40 + 0.15*math.sin(math.pi*i/24)) for i in range(25)]
    B.tube("crest_arch", crest_pts, [0.026]*25, GOLD)
    for sgn in (-1, 1):
        volute(B, "crest_end_volute_%d" % sgn,
               (sgn*width/2, 0.47, 1.43), 0.13, 0.018, 0.023, GOLD,
               plane="XZ", turns=1.10, sign=sgn, n=30)


def armchair(oid, label, pos, yaw):
    B.begin(oid, label, pos, yaw, "furniture",
            "High-back wing armchair in crimson velvet with damask wings and gilt cabriole legs")
    B.box("seat_frame", (0.86, 0.82, 0.18), (0, 0, 0.40), ROSE, bevel=0.02)
    B.box("seat_cushion", (0.76, 0.72, 0.15), (0, -0.02, 0.57), VELVET, bevel=0.035)
    B.box("back_panel", (0.78, 0.13, 0.92), (0, 0.38, 1.00), DAMASK, bevel=0.025)
    B.box("back_frame_gilt", (0.84, 0.15, 0.030), (0, 0.39, 1.47), GOLD)
    for sgn in (-1, 1):
        B.box("wing_%d" % sgn, (0.11, 0.44, 0.66),
              (sgn*0.42, 0.20, 1.06), VELVET, bevel=0.035)
        B.box("arm_%d" % sgn, (0.15, 0.78, 0.20),
              (sgn*0.42, -0.02, 0.72), ROSE, bevel=0.025)
        volute(B, "arm_volute_%d" % sgn,
               (sgn*0.42, -0.36, 0.76), 0.12, 0.018, 0.024, GOLD,
               plane="YZ", turns=1.15, sign=sgn, n=30)
        for yy in (-0.30, 0.30):
            cabriole_leg(B, "leg_%d_%.2f" % (sgn, yy),
                         (sgn*0.34, yy, 0.0), 0.40, ROSE, sway=0.038)
    for i in range(3):
        t = i-1
        acanthus_leaf(B, "crest_leaf_%02d" % i,
                      (t*0.10, 0.40, 1.48), 0.16*(1-0.16*abs(t)), 0.045,
                      GOLD, tilt=0.10*t)
    crest_pts = [(-0.40 + 0.80*i/18, 0.39,
                  1.47 + 0.11*math.sin(math.pi*i/18)) for i in range(19)]
    B.tube("crest_arch", crest_pts, [0.022]*19, GOLD)


sofa("sofa_west", "CanapeWest", (-2.42, 0.30, 0), 90.0, seats=3)
sofa("sofa_east", "CanapeEast", (2.42, 0.30, 0), -90.0, seats=3)
armchair("armchair_southwest", "FauteuilSouthwest", (-1.38, -1.72, 0), 165.0)
armchair("armchair_southeast", "FauteuilSoutheast", (1.38, -1.72, 0), 195.0)

B.begin("coffee_table", "GiltCoffeeTable", (0, 0.18, 0), 0, "furniture",
        "Marble-top centre table with carved gilt apron and four cabriole legs")
B.box("table_top", (1.52, 0.88, 0.075), (0, 0, 0.48), MARBLE, bevel=0.015)
B.box("table_top_gilt", (1.56, 0.92, 0.018), (0, 0, 0.445), GOLD)
B.box("table_apron", (1.34, 0.72, 0.13), (0, 0, 0.375), ROSE, bevel=0.01)
for sgn in (-1, 1):
    volute(B, "table_apron_scroll_%d" % sgn,
           (sgn*0.58, -0.37, 0.37), 0.14, 0.018, 0.024, GOLD,
           plane="XZ", turns=1.15, sign=sgn, n=32)
    for yy in (-0.30, 0.30):
        cabriole_leg(B, "table_leg_%d_%.2f" % (sgn, yy),
                     (sgn*0.60, yy, 0.0), 0.36, GOLD, sway=0.055)
B.sphere("table_vase_body", 0.11, (0, 0, 0.60), GOLD, scale=(0.8, 0.8, 1.35))
B.cylinder("table_vase_neck", 0.035, 0.16, (0, 0, 0.74), GOLD)
for i in range(5):
    a = 2*math.pi*i/5
    B.sphere("table_flower_%d" % i, 0.038,
             (0.075*math.cos(a), 0.075*math.sin(a), 0.83), GOLD,
             scale=(1.1, 1.1, 0.6))

B.begin("area_carpet", "SavonnerieCarpet", (0, 0.10, 0), 0, "detail",
        "Crimson damask carpet with gilt border and central medallion")
B.box("carpet_field", (5.10, 3.70, 0.014), (0, 0, 0.008), DAMASK)
for sgn in (-1, 1):
    B.box("carpet_border_%d_x" % sgn, (0.09, 3.86, 0.018),
          (sgn*2.58, 0, 0.010), GOLD)
    B.box("carpet_border_%d_y" % sgn, (5.24, 0.09, 0.018),
          (0, sgn*1.89, 0.010), GOLD)
torus(B, "carpet_medallion", (0, 0, 0.019), 0.92, 0.045, GOLD, 56, 8)
torus(B, "carpet_medallion_inner", (0, 0, 0.020), 0.62, 0.025, GOLD, 48, 8)
for i in range(12):
    a = 2*math.pi*i/12
    acanthus_leaf(B, "carpet_leaf_%02d" % i,
                  (0.76*math.cos(a), 0.76*math.sin(a), 0.021),
                  0.24, 0.075, GOLD, yaw=a)


def side_table(oid, pos):
    B.begin(oid, "Gueridon", pos, 0, "furniture",
            "Round marble-topped gueridon with three gilt cabriole legs")
    B.cylinder("top", 0.30, 0.045, (0, 0, 0.62), MARBLE)
    B.cylinder("top_gilt", 0.315, 0.014, (0, 0, 0.595), GOLD)
    for i in range(3):
        a = 2*math.pi*i/3
        cabriole_leg(B, "leg_%d" % i,
                     (0.20*math.cos(a), 0.20*math.sin(a), 0.0), 0.60, GOLD,
                     foot_r=0.035, sway=0.035)
    B.sphere("finial", 0.055, (0, 0, 0.69), GOLD)


# Move both gueridons into the wall niche: at +/-3.20 their marble tops
# intersected the sofa back frame (measured overlap 1.13e-3 m3).
side_table("gueridon_west", (-3.52, -0.90, 0))
side_table("gueridon_east", (3.52, -0.90, 0))


# ================================================================= LIGHTING ===
def candle(B, tag, loc, height=0.16, with_light=True, energy=6.0):
    B.cylinder(tag+"_stick", 0.020, height, (loc[0], loc[1], loc[2]+height/2),
               STUCCO, verts=16)
    B.cylinder(tag+"_cup", 0.040, 0.018, (loc[0], loc[1], loc[2]+height+0.005), GOLD)
    B.sphere(tag+"_flame", 0.024, (loc[0], loc[1], loc[2]+height+0.042), "flame",
             scale=(0.72, 0.72, 1.65))
    if with_light:
        # keep the source off the geometry so 1/r^2 does not blow out gilt arms
        add_point(tag+"_light", (loc[0], loc[1], loc[2]+height+0.30),
                  energy, (1.0, 0.60, 0.28))


def add_point(name, loc, energy, color):
    ld = bpy.data.lights.new(name, "POINT")
    ld.energy = energy
    ld.color = color
    ld.shadow_soft_size = 0.12
    ob = bpy.data.objects.new(name, ld)
    scene.collection.objects.link(ob)
    ob.location = loc
    return ob


def add_area(name, loc, target, energy, color, size=(2.0, 2.0)):
    ld = bpy.data.lights.new(name, "AREA")
    ld.energy = energy
    ld.color = color
    ld.shape = "RECTANGLE"
    ld.size, ld.size_y = size
    ob = bpy.data.objects.new(name, ld)
    scene.collection.objects.link(ob)
    ob.location = loc
    ob.rotation_euler = (Vector(target)-Vector(loc)).to_track_quat("-Z", "Y").to_euler()
    return ob


# Root Z must be 0: all chandelier part heights are already absolute. The old
# +0.30 m double-offset pushed the canopy through the ceiling plane.
B.begin("chandelier", "CrystalChandelier", (0, 0.20, 0), 0, "light",
        "Six-arm gilt chandelier with candle flames and hung crystal drops")
B.cylinder("chandelier_canopy", 0.11, 0.07, (0, 0, H-0.035), GOLD)
B.tube("chandelier_chain",
       [(0, 0, (H-0.07)-((H-0.07)-1.30)*i/12) for i in range(13)],
       [0.022]*13, GOLD)
B.sphere("chandelier_bowl", 0.17, (0, 0, 1.42), GOLD, scale=(1.0, 1.0, 0.65))
B.cylinder("chandelier_stem", 0.045, 1.00, (0, 0, 1.92), GOLD)
for i in range(6):
    a = 2*math.pi*i/6
    pts, rr = [], []
    for j in range(18):
        t = j/17
        r = 0.10 + 0.46*t
        # arm end rises to the bobeche (1.63) instead of stopping at 1.59
        z = 1.45 + 0.34*math.sin(math.pi*t) + 0.18*t
        pts.append((r*math.cos(a), r*math.sin(a), z))
        rr.append(0.030*(1-0.35*t))
    B.tube("arm_%d" % i, pts, rr, GOLD)
    ex, ey = 0.56*math.cos(a), 0.56*math.sin(a)
    B.cylinder("bobeche_%d" % i, 0.060, 0.024, (ex, ey, 1.63), GOLD)
    candle(B, "candle_%d" % i, (ex, ey, 1.64), 0.17, with_light=False)
    # A real chain now passes THROUGH all four drops. Previously the 0.09 m
    # bead pitch exceeded the 0.065 m bead height, leaving a 0.025 m gap
    # between every bead: the whole string was floating.
    cx, cy = 0.38*math.cos(a), 0.38*math.sin(a)
    B.tube("crystal_chain_%d" % i,
           [(cx, cy, 1.86), (cx, cy, 1.40)], [0.006, 0.006], "crystal")
    for k in range(4):
        B.sphere("crystal_%d_%d" % (i, k), 0.026,
                 (cx, cy, 1.60-0.065*k), "crystal",
                 scale=(0.8, 0.8, 1.25))
for i in range(10):
    a = 2*math.pi*i/10
    # Festoons must start INSIDE the bowl (r=0.17), not at r=0.30 where they
    # hung 6-12 cm away from any support.
    pts = [(0.16*math.cos(a), 0.16*math.sin(a), 1.42-0.34*j/6) for j in range(7)]
    B.tube("festoon_%d" % i, pts, [0.012]*7, "crystal")

B.begin("wall_sconces", "GiltSconces", (0, 0, 0), 0, "light",
        "Four two-arm gilt wall sconces with candle flames")
for sx in (-1, 1):
    for cy in (-2.55, 1.55):
        x = sx*(HW-0.13)
        tag = "sconce_%d_%.2f" % (sx, cy)
        B.box(tag+"_plate", (0.05, 0.18, 0.34), (x, cy, 2.05), GOLD)
        for s2 in (-1, 1):
            # The old arm ended at z=2.02 while the candle base sat at 2.20:
            # a visibly detached scroll bracket. Rise to the candle and add a
            # real bobeche so the candle is seated, not just nearby.
            pts = [(x - sx*0.05*t, cy + s2*0.20*t,
                    2.02 + 0.18*t + 0.12*math.sin(math.pi*t))
                   for t in [j/14 for j in range(15)]]
            B.tube(tag+"_arm_%d" % s2, pts, [0.020]*15, GOLD)
            lx, ly = x - sx*0.05, cy + s2*0.20
            B.cylinder(tag+"_bobeche_%d" % s2, 0.048, 0.020,
                       (lx, ly, 2.19), GOLD)
            candle(B, tag+"_candle_%d" % s2, (lx, ly, 2.20), 0.15, energy=4.0)

B.begin("candelabra", "FloorCandelabra", (0, 0, 0), 0, "light",
        "Two five-light floor candelabra flanking the fireplace")
for sx in (-1, 1):
    x = sx*3.30
    y = HD-0.62
    tag = "candelabrum_%d" % sx
    B.cylinder(tag+"_base", 0.18, 0.06, (x, y, 0.03), GOLD)
    B.tube(tag+"_stem", [(x, y, 0.06 + 1.28*j/10) for j in range(11)],
           [0.045-0.018*j/10 for j in range(11)], GOLD)
    for i in range(5):
        a = 2*math.pi*i/5
        cx, cy = x+0.26*math.cos(a), y+0.26*math.sin(a)
        B.tube(tag+"_branch_%d" % i,
               [(x, y, 1.20), (x+0.14*math.cos(a), y+0.14*math.sin(a), 1.26),
                (cx, cy, 1.34)], [0.020, 0.018, 0.016], GOLD)
        candle(B, tag+"_candle_%d" % i, (cx, cy, 1.35), 0.17, energy=3.5)

# exterior daylight through the four windows
for sx in (-1, 1):
    for cy in (-1.70, 1.70):
        add_area("window_light_%d_%.2f" % (sx, cy),
                 (sx*(HW+0.55), cy, 1.95), (0, cy, 1.60),
                 135, (0.78, 0.86, 1.0), (1.15, 1.75))
add_point("chandelier_ambient", (0, 0.20, 2.35), 26, (1.0, 0.74, 0.45))
add_point("fireplace_glow", (0, HD-0.55, 0.55), 12, (1.0, 0.42, 0.14))
add_area("key_fill", (0, -1.9, 3.10), (0, 1.3, 0.9), 65,
         (1.0, 0.90, 0.78), (4.0, 2.4))

world = bpy.data.worlds.new("BaroqueAmbient")
world.use_nodes = True
bg = next(n for n in world.node_tree.nodes if n.type == "BACKGROUND")
ENV_PATH = os.path.join(ROOT, "textures", "ballroom_1k.hdr")
if os.path.exists(ENV_PATH):
    nt = world.node_tree
    env_img = bpy.data.images.load(ENV_PATH, check_existing=True)
    env_node = nt.nodes.new("ShaderNodeTexEnvironment")
    env_node.name = "RealWorldHDRI"
    env_node.image = env_img
    nt.links.new(env_node.outputs["Color"], bg.inputs["Color"])
    bg.inputs["Strength"].default_value = 0.22
    # Camera rays see a neutral dark backdrop; reflections and GI still sample
    # the real HDRI. Without this the cutaway shows the ballroom photo.
    cam_bg = nt.nodes.new("ShaderNodeBackground")
    cam_bg.name = "CameraBackdrop"
    cam_bg.inputs["Color"].default_value = (0.055, 0.050, 0.046, 1.0)
    cam_bg.inputs["Strength"].default_value = 1.0
    lp = nt.nodes.new("ShaderNodeLightPath")
    mix = nt.nodes.new("ShaderNodeMixShader")
    out = next(n for n in nt.nodes if n.type == "OUTPUT_WORLD")
    for link in list(nt.links):
        if link.to_node == out and link.to_socket == out.inputs["Surface"]:
            nt.links.remove(link)
    nt.links.new(lp.outputs["Is Camera Ray"], mix.inputs[0])
    nt.links.new(bg.outputs["Background"], mix.inputs[1])
    nt.links.new(cam_bg.outputs["Background"], mix.inputs[2])
    nt.links.new(mix.outputs[0], out.inputs["Surface"])
    print("ENVIRONMENT_HDRI", env_img.name, flush=True)
else:
    bg.inputs["Color"].default_value = (0.24, 0.21, 0.19, 1.0)
    bg.inputs["Strength"].default_value = 0.20
scene.world = world


# ================================================================ RENDERING ===
scene.render.engine = "CYCLES"
scene.cycles.samples = 12 if IS_PREVIEW else 56
try:
    scene.cycles.use_denoising = True
except AttributeError:
    pass
scene.cycles.max_bounces = 4 if IS_PREVIEW else 6
scene.cycles.diffuse_bounces = 2 if IS_PREVIEW else 3
scene.cycles.glossy_bounces = 2 if IS_PREVIEW else 3
scene.render.resolution_x = 760 if IS_PREVIEW else 1500
scene.render.resolution_y = 540 if IS_PREVIEW else 1050
scene.render.resolution_percentage = 100
scene.render.image_settings.file_format = "PNG"
scene.render.image_settings.color_mode = "RGB"
scene.view_settings.view_transform = "AgX"
try:
    scene.view_settings.look = "Medium High Contrast"
except (TypeError, AttributeError):
    pass
scene.view_settings.exposure = -0.24
scene.render.film_transparent = False
print("QUALITY", QUALITY, "samples", scene.cycles.samples,
      "res", scene.render.resolution_x, scene.render.resolution_y, flush=True)


def camera(name, loc, target, lens):
    cd = bpy.data.cameras.new(name)
    cd.lens = lens
    ob = bpy.data.objects.new(name, cd)
    scene.collection.objects.link(ob)
    ob.location = loc
    ob.rotation_euler = (Vector(target)-Vector(loc)).to_track_quat("-Z", "Y").to_euler()
    return ob


CAMS = [
    ("01_hero_axis",   (0.0, -2.92, 1.62), (0, 1.85, 1.62), 23),
    ("02_hero_angle",  (2.75, -2.55, 1.72), (-0.20, 1.70, 1.45), 26),
    ("03_detail_mirror", (1.75, -0.55, 1.70), (0, HD-0.10, 2.05), 34),
    ("04_detail_fabric", (-1.30, -1.60, 1.08), (-2.42, 0.30, 0.80), 40),
]
cam_objs = {name: camera(name, loc, tgt, lens) for name, loc, tgt, lens in CAMS}

# pack textures so the .blend is self-contained
for im in bpy.data.images:
    if im.source == "FILE" and not im.packed_file:
        try:
            im.pack()
        except Exception as e:
            print("pack failed", im.name, e)

parts_path = os.path.join(ROOT, "scene_parts.json")
with open(parts_path, "w", encoding="utf-8") as fh:
    json.dump({
        "scene_label": "BaroqueReceptionLounge",
        "provenance": "direct parametric construction; real Poly Haven CC0 PBR; NO TRELLIS; NOT a FineScene Agent run",
        "room": {"width": W, "depth": D, "height": H, "floor_top_z": 0.0},
        "materials": list(MAT_CFG.keys()),
        "texture_manifest": "texture_manifest.json",
        "object_count": len(B.objects),
        "total_parts": sum(len(o["parts"]) for o in B.objects),
        "objects": B.objects,
    }, fh, indent=2, ensure_ascii=False)
print("OBJECTS", len(B.objects), "PARTS", sum(len(o["parts"]) for o in B.objects))

blend_path = os.path.join(
    ROOT, "baroque_reception_preview.blend" if IS_PREVIEW else "baroque_reception.blend")
bpy.ops.wm.save_as_mainfile(filepath=blend_path)
print("BLEND", blend_path)

renderdir = os.path.join(ROOT, "renders")
os.makedirs(renderdir, exist_ok=True)
prefix = "preview_" if IS_PREVIEW else ""
render_cams = CAMS[:2] if IS_PREVIEW else CAMS
for name, _, _, _ in render_cams:
    scene.camera = cam_objs[name]
    scene.render.filepath = os.path.join(renderdir, prefix + name + ".png")
    print("RENDER", name, flush=True)
    bpy.ops.render.render(write_still=True)

hidden = []
if not IS_PREVIEW:
    # cutaway overview: hide ceiling + south wall for a top-down read
    for ob in scene.objects:
        if (ob.name.startswith(("ceiling_", "coffer_")) or
                ob.name in ("wall_south_left", "wall_south_right", "wall_south_lintel")):
            ob.hide_render = True
            hidden.append(ob.name)
    ov = camera("05_overview_cutaway", (6.6, -8.4, 8.2), (0, 0.2, 0.9), 30)
    scene.camera = ov
    scene.render.filepath = os.path.join(renderdir, "05_overview_cutaway.png")
    print("RENDER 05_overview_cutaway", flush=True)
    bpy.ops.render.render(write_still=True)
    for ob in scene.objects:
        if ob.name in hidden:
            ob.hide_render = False

with open(os.path.join(ROOT, "render_manifest.json"), "w", encoding="utf-8") as fh:
    json.dump({"quality": QUALITY, "engine": "Cycles", "samples": scene.cycles.samples,
               "resolution": [scene.render.resolution_x, scene.render.resolution_y],
               "view_transform": "AgX", "cameras": [
                   {"name": n, "location": list(l), "target": list(t), "lens_mm": z}
                   for n, l, t, z in CAMS],
               "cutaway_hidden": hidden}, fh, indent=2, ensure_ascii=False)
print("DONE")
