"""Hand-authoring toolkit for object programs (runs inside Blender, via forge_runner).

The forge DSL (B.box / B.cylinder / B.sphere / B.tube) builds hard boxes. Objects
filmed in a real room are mostly *rounded* plastic and metal shells, flat graphics
(logos, key icons, labels) and a few swept tubes. This module adds exactly that,
in the same registration scheme, so a program written by hand stays a normal
forge program (parts named after the functional spec, joints rigged by
forge_runner, materials as keys where a key exists):

    import hand_lib as H
    H.loft(B, "body", [H.sec(0, .34, .40, .04), H.sec(.8, .34, .40, .04)], "plastic:#EEEEEC:0.35")
    H.rounded_block(B, "head", (.31, .37, .25), (0, 0, 1.19), mat, r_plan=.035, r_edge=.012)
    H.decal(B, "head_graphics", H.image_material("disp_panel", path, rough=.3), .29, .22,
            (0, -.186, 1.31), facing="-y")
    H.text(B, "logo", "Galanz", .03, (0, -.2, .1), "paint:#D8D8D8:0.4", facing="-y")

Image materials are packed into the .blend, so the delivered scene stays self-contained.
"""
from __future__ import annotations

import math
import os

import bpy
from mathutils import Euler, Matrix, Vector

FONT_LATIN = None
FONT_CJK = None


# --------------------------------------------------------------------------- #
# registration
# --------------------------------------------------------------------------- #
def register(B, ob, mat=None, ptype="hand_mesh", params=None):
    """Parent `ob` to the object root and record it like any B.* part.
    `mat` is a material key (str, resolved by the forge's table) or a bpy
    material (appended directly)."""
    if ob.name not in B.coll.objects:
        B.coll.objects.link(ob)
    key = mat if isinstance(mat, str) else None
    B._register(ob, ptype, params or {}, tuple(ob.location), key)
    if mat is not None and not isinstance(mat, str):
        ob.data.materials.append(mat)
    return ob


def smooth(ob, angle_deg=35.0):
    me = ob.data
    for p in me.polygons:
        p.use_smooth = True
    if hasattr(me, "set_sharp_from_angle"):
        me.set_sharp_from_angle(angle=math.radians(angle_deg))
    return ob


def mesh_object(B, name, verts, faces, mat=None, loc=(0, 0, 0), smooth_deg=35.0, uvs=None,
                params=None, closed=True):
    me = bpy.data.meshes.new(name + "_mesh")
    me.from_pydata([tuple(v) for v in verts], [], [tuple(f) for f in faces])
    me.validate(clean_customdata=False)
    if closed:                      # outward normals whatever the winding
        import bmesh
        bm = bmesh.new()
        bm.from_mesh(me)
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
        bm.to_mesh(me)
        bm.free()
    me.update()
    if uvs is not None:
        uv = me.uv_layers.new(name="UVMap")
        for poly in me.polygons:
            for li in poly.loop_indices:
                uv.data[li].uv = uvs[me.loops[li].vertex_index]
    ob = bpy.data.objects.new(name, me)
    ob.location = loc
    B.coll.objects.link(ob)
    if smooth_deg:
        smooth(ob, smooth_deg)
    return register(B, ob, mat, params=params or {"vertices": len(verts)})


# --------------------------------------------------------------------------- #
# rounded-rectangle sections and lofts
# --------------------------------------------------------------------------- #
def rrect(w, d, r, n=6, cx=0.0, cy=0.0):
    """Counter-clockwise rounded rectangle (w along x, d along y), 4*(n+1) points,
    starting at the +x/-y corner. r is clamped to the half sizes."""
    r = max(0.0, min(r, w / 2 - 1e-5, d / 2 - 1e-5))
    hw, hd = w / 2 - r, d / 2 - r
    pts = []
    for k, (ccx, ccy, a0) in enumerate(((hw, -hd, -90.0), (hw, hd, 0.0), (-hw, hd, 90.0), (-hw, -hd, 180.0))):
        for i in range(n + 1):
            a = math.radians(a0 + 90.0 * i / n)
            pts.append((cx + ccx + r * math.cos(a), cy + ccy + r * math.sin(a)))
    return pts


def sec(z, w, d, r, cx=0.0, cy=0.0):
    return {"z": z, "w": w, "d": d, "r": r, "cx": cx, "cy": cy}


def loft(B, name, sections, mat, n=6, cap_bottom=True, cap_top=True, smooth_deg=35.0, loc=(0, 0, 0)):
    """Stack of rounded-rectangle sections joined into one closed shell."""
    rings = [rrect(s["w"], s["d"], s["r"], n, s.get("cx", 0.0), s.get("cy", 0.0)) for s in sections]
    m = len(rings[0])
    verts, faces = [], []
    for s, ring in zip(sections, rings):
        verts += [(x, y, s["z"]) for x, y in ring]
    for k in range(len(rings) - 1):
        a, b = k * m, (k + 1) * m
        for i in range(m):
            j = (i + 1) % m
            faces.append((a + i, a + j, b + j, b + i))
    if cap_bottom:
        faces.append(tuple(reversed(range(m))))
    if cap_top:
        top = (len(rings) - 1) * m
        faces.append(tuple(range(top, top + m)))
    return mesh_object(B, name, verts, faces, mat, loc, smooth_deg, closed=cap_bottom and cap_top)


def fillet_profile(h, r_bot, r_top, steps=4):
    """(z, inset) pairs for a block of height h whose top and bottom edges are
    rounded with radii r_top / r_bot (inset = how far the section shrinks)."""
    out = []
    # z ascending: bottom fillet from (z=0, inset=r_bot) to (z=r_bot, inset=0),
    # then the top fillet from (z=h-r_top, inset=0) to (z=h, inset=r_top)
    for i in (range(steps, -1, -1) if r_bot > 0 else [0]):
        a = math.radians(90.0 * i / steps)
        out.append((r_bot * (1 - math.sin(a)), r_bot * (1 - math.cos(a))))
    for i in (range(steps + 1) if r_top > 0 else [0]):
        a = math.radians(90.0 * i / steps)
        out.append((h - r_top + r_top * math.sin(a), r_top * (1 - math.cos(a))))
    cleaned = []
    for z, ins in out:
        if not cleaned or abs(z - cleaned[-1][0]) > 1e-6:
            cleaned.append((z, ins))
    return cleaned


def rounded_block(B, name, size, base, mat, r_plan=0.02, r_edge=0.006, r_edge_bot=None, n=6,
                  taper=(0.0, 0.0), steps=4, smooth_deg=35.0):
    """Box with rounded vertical edges (r_plan) and filleted top/bottom edges.
    `base` is the centre of the BOTTOM face. taper = (dw, dd): the top is that
    much narrower/shallower than the bottom (linear)."""
    w, d, h = size
    rb = r_edge if r_edge_bot is None else r_edge_bot
    secs = []
    for z, ins in fillet_profile(h, rb, r_edge, steps):
        f = z / h if h else 0.0
        ww, dd = w - taper[0] * f - 2 * ins, d - taper[1] * f - 2 * ins
        secs.append(sec(base[2] + z, ww, dd, max(0.0005, r_plan - ins), base[0], base[1]))
    return loft(B, name, secs, mat, n=n, smooth_deg=smooth_deg)


def profile_extrude(B, name, pts2d, thickness, mat, plane="yz", offset=0.0, smooth_deg=30.0):
    """Extrude a closed 2D outline. plane 'yz': outline in (y, z), extruded along
    x from offset - t/2 to offset + t/2; 'xz': (x, z) along y; 'xy': (x, y) along z."""
    m = len(pts2d)
    verts = []
    for s in (-0.5, 0.5):
        c = offset + s * thickness
        for a, b in pts2d:
            verts.append({"yz": (c, a, b), "xz": (a, c, b), "xy": (a, b, c)}[plane])
    faces = [tuple(range(m)), tuple(range(2 * m - 1, m - 1, -1))]
    for i in range(m):
        j = (i + 1) % m
        faces.append((i, m + i, m + j, j))
    return mesh_object(B, name, verts, faces, mat, smooth_deg=smooth_deg)


def _signed_area(p):
    return 0.5 * sum(p[i][0] * p[(i + 1) % len(p)][1] - p[(i + 1) % len(p)][0] * p[i][1] for i in range(len(p)))


def arc_pts(cx, cy, r, a0, a1, n=8):
    return [(cx + r * math.cos(math.radians(a0 + (a1 - a0) * i / n)),
             cy + r * math.sin(math.radians(a0 + (a1 - a0) * i / n))) for i in range(n + 1)]


def rrect2d(w, h, r, cx=0.0, cy=0.0, n=6, radii=None):
    """2D rounded rectangle in its own plane; radii = (br, tr, tl, bl) overrides r."""
    br, tr, tl, bl = radii or (r, r, r, r)
    hw, hh = w / 2, h / 2
    pts = []
    pts += arc_pts(cx + hw - br, cy - hh + br, br, -90, 0, n) if br > 0 else [(cx + hw, cy - hh)]
    pts += arc_pts(cx + hw - tr, cy + hh - tr, tr, 0, 90, n) if tr > 0 else [(cx + hw, cy + hh)]
    pts += arc_pts(cx - hw + tl, cy + hh - tl, tl, 90, 180, n) if tl > 0 else [(cx - hw, cy + hh)]
    pts += arc_pts(cx - hw + bl, cy - hh + bl, bl, 180, 270, n) if bl > 0 else [(cx - hw, cy - hh)]
    return pts


def tube(B, name, points, radius, mat, samples=12, closed=False):
    """Round tube through 3D points (Catmull-Rom smoothed), as a real mesh."""
    from curve_dsl import _catmull
    pts = _catmull([tuple(p) for p in points], samples) if len(points) > 2 else [tuple(p) for p in points]
    ring = 10
    verts, faces = [], []
    prev_n = None
    for i, p in enumerate(pts):
        p = Vector(p)
        t = (Vector(pts[min(i + 1, len(pts) - 1)]) - Vector(pts[max(i - 1, 0)])).normalized()
        if prev_n is None:
            ref = Vector((0, 0, 1)) if abs(t.z) < 0.9 else Vector((1, 0, 0))
            prev_n = t.cross(ref).normalized()
        nrm = (prev_n - t * prev_n.dot(t)).normalized()
        bin_ = t.cross(nrm)
        prev_n = nrm
        for k in range(ring):
            a = 2 * math.pi * k / ring
            verts.append(tuple(p + radius * (math.cos(a) * nrm + math.sin(a) * bin_)))
    m = len(pts)
    for i in range(m - 1):
        for k in range(ring):
            a, b = i * ring + k, i * ring + (k + 1) % ring
            faces.append((a, b, b + ring, a + ring))
    faces.append(tuple(reversed(range(ring))))
    faces.append(tuple(range((m - 1) * ring, m * ring)))
    return mesh_object(B, name, verts, faces, mat, smooth_deg=80.0)


def lathe_mesh(B, name, profile, mat, center=(0, 0, 0), segments=48, smooth_deg=40.0, cap=True):
    """Revolve (r, z) profile points around the z axis."""
    verts, faces = [], []
    m = len(profile)
    for s in range(segments):
        a = 2 * math.pi * s / segments
        for r, z in profile:
            verts.append((center[0] + r * math.cos(a), center[1] + r * math.sin(a), center[2] + z))
    for s in range(segments):
        t = (s + 1) % segments
        for i in range(m - 1):
            faces.append((s * m + i, t * m + i, t * m + i + 1, s * m + i + 1))
    if cap and profile[0][0] > 1e-6:
        faces.append(tuple(s * m for s in reversed(range(segments))))
    if cap and profile[-1][0] > 1e-6:
        faces.append(tuple(s * m + m - 1 for s in range(segments)))
    return mesh_object(B, name, verts, faces, mat, smooth_deg=smooth_deg, closed=False)


# --------------------------------------------------------------------------- #
# images, decals and text
# --------------------------------------------------------------------------- #
def _image(path):
    img = bpy.data.images.load(path, check_existing=True)
    if not img.packed_file:
        try:
            img.pack()
        except Exception:  # noqa: BLE001
            pass
    return img


def image_material(name, path, rough=0.4, metallic=0.0, emit=0.0, alpha=False, coat=0.0,
                   specular=0.5, emit_path=None, key=None):
    """Principled material driven by an image (sRGB). emit > 0 makes the image
    glow (displays); emit_path is a separate glow mask/colour image."""
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    bsdf = next(n for n in nt.nodes if n.type == "BSDF_PRINCIPLED")
    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.image = _image(path)
    tex.extension = "EXTEND"
    tex.interpolation = "Cubic"
    nt.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
    bsdf.inputs["Roughness"].default_value = rough
    bsdf.inputs["Metallic"].default_value = metallic
    if "Specular IOR Level" in bsdf.inputs:
        bsdf.inputs["Specular IOR Level"].default_value = specular
    if coat and "Coat Weight" in bsdf.inputs:
        bsdf.inputs["Coat Weight"].default_value = coat
        bsdf.inputs["Coat Roughness"].default_value = 0.05
    if emit:
        src = tex
        if emit_path:
            src = nt.nodes.new("ShaderNodeTexImage")
            src.image = _image(emit_path)
            src.extension = "EXTEND"
        nt.links.new(src.outputs["Color"], bsdf.inputs["Emission Color"])
        bsdf.inputs["Emission Strength"].default_value = emit
    if alpha:
        nt.links.new(tex.outputs["Alpha"], bsdf.inputs["Alpha"])
        if hasattr(m, "surface_render_method"):
            m.surface_render_method = "DITHERED"
        m.blend_method = "HASHED" if hasattr(m, "blend_method") else None
    # the key the spec check reads (class + dominant colour); an image has none
    m["adsl_material"] = key or (f"screen:#101020:{rough}:emit=#5C50FF" if emit else f"plastic:#808080:{rough}")
    m["ci_image"] = os.path.basename(path)
    return m


_FACING = {
    # facing -> (u axis, v axis, normal)
    "-y": (Vector((1, 0, 0)), Vector((0, 0, 1)), Vector((0, -1, 0))),
    "+y": (Vector((-1, 0, 0)), Vector((0, 0, 1)), Vector((0, 1, 0))),
    "+x": (Vector((0, 1, 0)), Vector((0, 0, 1)), Vector((1, 0, 0))),
    "-x": (Vector((0, -1, 0)), Vector((0, 0, 1)), Vector((-1, 0, 0))),
    "+z": (Vector((1, 0, 0)), Vector((0, 1, 0)), Vector((0, 0, 1))),
    "-z": (Vector((1, 0, 0)), Vector((0, -1, 0)), Vector((0, 0, -1))),
}


def decal(B, name, mat, w, h, center, facing="-y", tilt_deg=0.0, thickness=0.0, round_r=0.0,
          uv_rect=(0.0, 0.0, 1.0, 1.0)):
    """Flat graphic of w x h metres centred at `center`, its visible side facing
    `facing`; tilt_deg rotates it about its own u axis (a panel leaning back).
    thickness > 0 makes it a thin slab (stickers, labels); round_r rounds the
    corners. The image maps to uv_rect (u0, v0, u1, v1) of the texture."""
    u, v, nrm = _FACING[facing]
    if tilt_deg:
        rot = Matrix.Rotation(math.radians(tilt_deg), 3, u)
        v, nrm = rot @ v, rot @ nrm
    outline = rrect2d(w, h, round_r, n=5) if round_r > 0 else [(w / 2, -h / 2), (w / 2, h / 2),
                                                               (-w / 2, h / 2), (-w / 2, -h / 2)]
    c = Vector(center)
    u0, v0, u1, v1 = uv_rect
    verts, uvs = [], []
    layers = (0.0,) if thickness <= 0 else (0.0, -thickness)
    for depth in layers:
        for a, b in outline:
            verts.append(tuple(c + a * u + b * v + depth * nrm))
            uvs.append((u0 + (a / w + 0.5) * (u1 - u0), v0 + (b / h + 0.5) * (v1 - v0)))
    m = len(outline)
    faces = [tuple(range(m))]                  # CCW in (u, v): normal = u x v = nrm
    if thickness > 0:
        faces.append(tuple(range(2 * m - 1, m - 1, -1)))
        for i in range(m):
            j = (i + 1) % m
            faces.append((j, m + j, m + i, i))
    return mesh_object(B, name, verts, faces, mat, smooth_deg=0, uvs=uvs, closed=thickness > 0)


def _font(cjk=False):
    global FONT_LATIN, FONT_CJK
    if cjk:
        if FONT_CJK is None:
            for p in ("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc", "/usr/share/fonts/wqy-microhei/wqy-microhei.ttc",
                      "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"):
                if os.path.isfile(p):
                    FONT_CJK = bpy.data.fonts.load(p, check_existing=True)
                    break
        return FONT_CJK
    return FONT_LATIN


def text(B, name, body, size, center, mat, facing="-y", extrude=0.0004, tilt_deg=0.0, cjk=False,
         align="CENTER", bold_offset=0.0, spacing=1.0):
    """Text converted to a mesh (so the scene import keeps it), lying on the
    plane through `center` and facing `facing`."""
    cu = bpy.data.curves.new(name + "_txt", "FONT")
    cu.body = body
    cu.size = size
    cu.align_x = align
    cu.align_y = "CENTER"
    cu.extrude = extrude
    cu.offset = bold_offset
    cu.space_character = spacing
    f = _font(cjk or any(ord(ch) > 0x2E80 for ch in body))
    if f is not None:
        cu.font = f
    tmp = bpy.data.objects.new(name + "_tmp", cu)
    B.coll.objects.link(tmp)
    u, v, nrm = _FACING[facing]
    if tilt_deg:
        rot = Matrix.Rotation(math.radians(tilt_deg), 3, u)
        v, nrm = rot @ v, rot @ nrm
    basis = Matrix((u, v, nrm)).transposed().to_4x4()
    tmp.matrix_world = Matrix.Translation(Vector(center) + nrm * extrude) @ basis
    bpy.context.view_layer.update()
    dg = bpy.context.evaluated_depsgraph_get()
    me = bpy.data.meshes.new_from_object(tmp.evaluated_get(dg))
    mw = tmp.matrix_world.copy()
    bpy.data.objects.remove(tmp, do_unlink=True)
    me.transform(mw)
    ob = bpy.data.objects.new(name, me)
    B.coll.objects.link(ob)
    return register(B, ob, mat, ptype="text", params={"text": body, "size": size})


# --------------------------------------------------------------------------- #
# booleans (holes in shells: vents, sink bowls, slots)
# --------------------------------------------------------------------------- #
def cut(target, cutters, apply=True, solver="EXACT"):
    """Boolean-difference each cutter from target, then delete the cutters
    (cutters are bpy objects; they are removed from the part list too)."""
    for c in cutters:
        mod = target.modifiers.new("cut_" + c.name, "BOOLEAN")
        mod.operation = "DIFFERENCE"
        mod.object = c
        if hasattr(mod, "solver"):
            mod.solver = solver
        c.hide_render = True
        c.hide_viewport = True
    if apply:
        bpy.context.view_layer.objects.active = target
        for mod in list(target.modifiers):
            if mod.type == "BOOLEAN":
                with bpy.context.temp_override(object=target, active_object=target):
                    bpy.ops.object.modifier_apply(modifier=mod.name)
        for c in cutters:
            bpy.data.objects.remove(c, do_unlink=True)
    return target


def cutter_box(name, size, loc, yaw=0.0):
    """A bare box object (not a registered part) to cut with."""
    sx, sy, sz = size
    v = [(-sx / 2, -sy / 2, -sz / 2), (sx / 2, -sy / 2, -sz / 2), (sx / 2, sy / 2, -sz / 2), (-sx / 2, sy / 2, -sz / 2),
         (-sx / 2, -sy / 2, sz / 2), (sx / 2, -sy / 2, sz / 2), (sx / 2, sy / 2, sz / 2), (-sx / 2, sy / 2, sz / 2)]
    f = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    me = bpy.data.meshes.new(name + "_mesh")
    me.from_pydata(v, [], f)
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    ob.location = loc
    ob.rotation_euler[2] = math.radians(yaw)
    return ob


def bevel(ob, width, segments=3, angle_deg=40.0):
    mod = ob.modifiers.new("Bevel", "BEVEL")
    mod.width = width
    mod.segments = segments
    mod.limit_method = "ANGLE"
    mod.angle_limit = math.radians(angle_deg)
    ob.modifiers.new("WeightedNormals", "WEIGHTED_NORMAL")
    return ob


def material(name, rgb_hex, rough=0.4, metallic=0.0, coat=0.0, transmission=0.0, ior=1.45,
             alpha=1.0, specular=0.5, emit_hex=None, emit=0.0, aniso=0.0, cls="plastic", key=None):
    """A plain Principled material when a key cannot say it (clear coat on
    lacquer, tinted glass with a coat, anisotropic brushed steel)."""
    def lin(h):
        c = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        return tuple((x / 12.92) if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in c) + (1.0,)
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    b = next(n for n in m.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    b.inputs["Base Color"].default_value = lin(rgb_hex)
    b.inputs["Roughness"].default_value = rough
    b.inputs["Metallic"].default_value = metallic
    if "Specular IOR Level" in b.inputs:
        b.inputs["Specular IOR Level"].default_value = specular
    if coat:
        b.inputs["Coat Weight"].default_value = coat
        b.inputs["Coat Roughness"].default_value = 0.03
    if transmission:
        b.inputs["Transmission Weight"].default_value = transmission
        b.inputs["IOR"].default_value = ior
    if alpha < 1.0:
        b.inputs["Alpha"].default_value = alpha
    if emit_hex:
        b.inputs["Emission Color"].default_value = lin(emit_hex)
        b.inputs["Emission Strength"].default_value = emit
    if aniso and "Anisotropic" in b.inputs:
        b.inputs["Anisotropic"].default_value = aniso
    m["adsl_material"] = key or f"{cls}:{rgb_hex}:{rough}"
    return m
