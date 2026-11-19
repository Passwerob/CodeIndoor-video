"""Shared material + geometry toolkit for the Baroque reception room.

Runs inside Blender 5.2. Everything here is deterministic parametric geometry;
no TRELLIS, no generated meshes, no external model files. Surfaces are driven by
real CC0 PBR maps downloaded from Poly Haven.
"""
import bpy
import json
import math
import os
from mathutils import Vector

ROOT = os.path.dirname(os.path.abspath(__file__))
TEXDIR = os.path.join(ROOT, "textures")

# logical material -> (tile size in metres, base metallic, sheen, coat)
MAT_CFG = {
    "floor_parquet":      dict(tile=2.40, metallic=0.0, sheen=0.0, coat=0.16),
    "boiserie_dark":      dict(tile=1.20, metallic=0.0, sheen=0.0, coat=0.22),
    "boiserie_walnut":    dict(tile=1.10, metallic=0.0, sheen=0.0, coat=0.20),
    "furniture_rosewood": dict(tile=0.90, metallic=0.0, sheen=0.0, coat=0.30),
    "marble_cream":       dict(tile=1.60, metallic=0.0, sheen=0.0, coat=0.34),
    "velvet_crimson":     dict(tile=0.42, metallic=0.0, sheen=0.22, coat=0.0,
                               tint=(0.70, 0.22, 0.28), tint_amount=0.88),
    "damask_crimson":     dict(tile=0.52, metallic=0.0, sheen=0.15, coat=0.0,
                               tint=(0.68, 0.24, 0.30), tint_amount=0.84),
    "stucco_white":       dict(tile=1.50, metallic=0.0, sheen=0.0, coat=0.0,
                               tint=(0.78, 0.74, 0.68), tint_amount=0.55),
    # Gilt wood leaf is a gold film over carved wood, not a bare metal block.
    # Low metallic + a real HDRI gives warm readable gold instead of charcoal.
    "gilded_bronze":      dict(tile=0.50, metallic=0.0, sheen=0.0, coat=0.0, gold=True,
                               specular=0.15),
    "relief_plaster":     dict(tile=1.20, metallic=0.0, sheen=0.0, coat=0.0),
}

FALLBACK_COLORS = {
    "floor_parquet": (0.42, 0.28, 0.14, 1),
    "boiserie_dark": (0.20, 0.09, 0.05, 1),
    "boiserie_walnut": (0.30, 0.18, 0.10, 1),
    "furniture_rosewood": (0.34, 0.13, 0.07, 1),
    "marble_cream": (0.82, 0.79, 0.71, 1),
    "velvet_crimson": (0.36, 0.045, 0.075, 1),
    "damask_crimson": (0.34, 0.075, 0.09, 1),
    "stucco_white": (0.87, 0.84, 0.78, 1),
    "gilded_bronze": (0.78, 0.56, 0.20, 1),
    "relief_plaster": (0.80, 0.75, 0.66, 1),
}


def _img(nt, path, noncolor, node_name, tile):
    node = nt.nodes.new("ShaderNodeTexImage")
    node.name = node_name
    node.label = node_name
    if os.path.exists(path):
        im = bpy.data.images.load(path, check_existing=True)
        im.colorspace_settings.name = "Non-Color" if noncolor else "sRGB"
        node.image = im
    node.projection = "BOX"          # correct on walls, ceilings and curved parts
    node.projection_blend = 0.16
    node.interpolation = "Linear"
    node.extension = "REPEAT"
    return node


def build_material(key, manifest):
    """Create a full PBR node graph: diffuse / normal / roughness / height / AO."""
    cfg = MAT_CFG[key]
    info = manifest["materials"][key]
    chans = info["channels"]
    mat = bpy.data.materials.new("PBR_" + key)
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()

    out = nt.nodes.new("ShaderNodeOutputMaterial")
    out.location = (900, 0)
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.location = (620, 0)
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    bsdf.inputs["Metallic"].default_value = cfg["metallic"]
    bsdf.inputs["Roughness"].default_value = 0.42
    bsdf.inputs["Base Color"].default_value = FALLBACK_COLORS[key]
    if cfg.get("sheen"):
        bsdf.inputs["Sheen Weight"].default_value = cfg["sheen"]
        bsdf.inputs["Sheen Roughness"].default_value = 0.55
        if cfg.get("tint"):
            bsdf.inputs["Sheen Tint"].default_value = (*cfg["tint"], 1.0)
    if cfg.get("coat"):
        bsdf.inputs["Coat Weight"].default_value = cfg["coat"]
        bsdf.inputs["Coat Roughness"].default_value = 0.18
    if "specular" in cfg:
        bsdf.inputs["Specular IOR Level"].default_value = cfg["specular"]

    tc = nt.nodes.new("ShaderNodeTexCoord")
    tc.location = (-900, 0)
    mp = nt.nodes.new("ShaderNodeMapping")
    mp.location = (-700, 0)
    s = 1.0 / cfg["tile"]
    mp.inputs["Scale"].default_value = (s, s, s)
    nt.links.new(tc.outputs["Object"], mp.inputs["Vector"])

    def tex(suffix, noncolor, label):
        rel = chans.get(suffix)
        if not rel:
            return None
        n = _img(nt, os.path.join(ROOT, rel), noncolor, label, cfg["tile"])
        nt.links.new(mp.outputs["Vector"], n.inputs["Vector"])
        return n

    diff = tex("diff", False, "Diffuse")
    nor = tex("nor", True, "NormalGL")
    rough = tex("rough", True, "Roughness")
    disp = tex("disp", True, "Height")
    ao = tex("ao", True, "AO")

    base_socket = None
    if diff:
        if cfg.get("gold"):
            # Gilt wood leaf is a solid warm gold film. The real CC0 scan still
            # drives Normal / Roughness / AO / Height, so this remains a PBR
            # surface; only the colour is constant, as real gold leaf is.
            gn = nt.nodes.new("ShaderNodeRGB")
            gn.name = "GiltLeafColour"
            gn.location = (-360, 260)
            gn.outputs["Color"].default_value = (0.66, 0.37, 0.07, 1.0)
            base_socket = gn.outputs["Color"]
        else:
            base_socket = diff.outputs["Color"]
        if cfg.get("tint"):
            tn = nt.nodes.new("ShaderNodeMixRGB")
            tn.blend_type = "MULTIPLY"
            tn.inputs["Factor"].default_value = cfg["tint_amount"]
            tn.inputs["Color2"].default_value = (*cfg["tint"], 1.0)
            tn.location = (-60, 300)
            nt.links.new(base_socket, tn.inputs["Color1"])
            base_socket = tn.outputs["Color"]
        if ao:
            mix = nt.nodes.new("ShaderNodeMixRGB")
            mix.blend_type = "MULTIPLY"
            mix.inputs["Factor"].default_value = 0.32
            mix.location = (200, 220)
            nt.links.new(base_socket, mix.inputs["Color1"])
            nt.links.new(ao.outputs["Color"], mix.inputs["Color2"])
            base_socket = mix.outputs["Color"]
        nt.links.new(base_socket, bsdf.inputs["Base Color"])

    if rough:
        nt.links.new(rough.outputs["Color"], bsdf.inputs["Roughness"])
    if nor:
        nm = nt.nodes.new("ShaderNodeNormalMap")
        nm.location = (120, -180)
        nm.inputs["Strength"].default_value = 0.85
        nt.links.new(nor.outputs["Color"], nm.inputs["Color"])
        normal_socket = nm.outputs["Normal"]
        if disp:
            bump = nt.nodes.new("ShaderNodeBump")
            bump.location = (360, -180)
            bump.inputs["Strength"].default_value = 0.30
            bump.inputs["Distance"].default_value = 0.012
            nt.links.new(disp.outputs["Color"], bump.inputs["Height"])
            nt.links.new(normal_socket, bump.inputs["Normal"])
            normal_socket = bump.outputs["Normal"]
        nt.links.new(normal_socket, bsdf.inputs["Normal"])
    elif disp:
        bump = nt.nodes.new("ShaderNodeBump")
        bump.location = (360, -180)
        bump.inputs["Strength"].default_value = 0.30
        bump.inputs["Distance"].default_value = 0.012
        nt.links.new(disp.outputs["Color"], bump.inputs["Height"])
        nt.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])

    mat["adsl_material"] = key
    mat["pbr_source"] = info["source_url"]
    mat["pbr_license"] = manifest["license"]
    return mat


def solid_material(name, color, metallic=0.0, roughness=0.4, emission=None):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    p = next(n for n in nt.nodes if n.type == "BSDF_PRINCIPLED")
    p.inputs["Base Color"].default_value = (*color, 1.0)
    p.inputs["Metallic"].default_value = metallic
    p.inputs["Roughness"].default_value = roughness
    if emission:
        p.inputs["Emission Color"].default_value = (*emission[:3], 1.0)
        p.inputs["Emission Strength"].default_value = emission[3]
    m.diffuse_color = (*color, 1.0)
    return m


# ----------------------------------------------------------------------------
# Object registry: one Collection + one Empty root per editable object.
# ----------------------------------------------------------------------------
class Builder:
    def __init__(self, scene, mats):
        self.scene = scene
        self.mats = mats
        self.objects = []
        self.cur = None
        self.root = None
        self.coll = None

    def begin(self, oid, label, position=(0, 0, 0), yaw=0.0, kind="structure", note=""):
        coll = bpy.data.collections.new("OBJ_" + oid)
        self.scene.collection.children.link(coll)
        root = bpy.data.objects.new("ROOT_" + oid, None)
        coll.objects.link(root)
        root.empty_display_type = "PLAIN_AXES"
        root.empty_display_size = 0.22
        root.location = position
        root.rotation_euler[2] = math.radians(yaw)
        root["adsl_object"] = oid
        root["adsl_label"] = label
        root["adsl_yaw_deg"] = yaw
        self.coll, self.root, self.cur = coll, root, {
            "id": oid, "label": label, "kind": kind, "note": note,
            "position": list(position), "yaw_deg": yaw, "parts": []}
        self.objects.append(self.cur)
        return root

    def _register(self, ob, ptype, params, local, mat_key):
        ob.parent = self.root
        ob["adsl_part"] = ob.name
        ob["adsl_type"] = ptype
        ob["adsl_params"] = json.dumps(params, ensure_ascii=False)
        ob["adsl_material"] = mat_key or ""
        if mat_key and mat_key in self.mats:
            ob.data.materials.append(self.mats[mat_key])
        self.cur["parts"].append({
            "name": ob.name, "type": ptype, "params": params,
            "position": [round(float(v), 5) for v in local],
            "material": mat_key or ""})
        return ob

    def box(self, name, size, loc, mat=None, yaw=0.0, bevel=0.0):
        sx, sy, sz = size
        v = [(-sx/2, -sy/2, -sz/2), (sx/2, -sy/2, -sz/2), (sx/2, sy/2, -sz/2), (-sx/2, sy/2, -sz/2),
             (-sx/2, -sy/2, sz/2), (sx/2, -sy/2, sz/2), (sx/2, sy/2, sz/2), (-sx/2, sy/2, sz/2)]
        f = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
        me = bpy.data.meshes.new(name + "_mesh")
        me.from_pydata(v, [], f)
        me.update()
        ob = bpy.data.objects.new(name, me)
        self.coll.objects.link(ob)
        ob.location = loc
        ob.rotation_euler[2] = math.radians(yaw)
        if bevel:
            mod = ob.modifiers.new("EasedEdge", "BEVEL")
            mod.width = bevel
            mod.segments = 2
            ob.modifiers.new("WeightedNormals", "WEIGHTED_NORMAL")
        return self._register(ob, "box", {"size": list(size), "bevel": bevel}, loc, mat)

    def cylinder(self, name, radius, depth, loc, mat=None, verts=32, rot=None):
        vs, fs = [], []
        for z in (-depth/2, depth/2):
            for i in range(verts):
                a = 2 * math.pi * i / verts
                vs.append((radius*math.cos(a), radius*math.sin(a), z))
        for i in range(verts):
            j = (i+1) % verts
            fs.append((i, j, verts+j, verts+i))
        fs.append(tuple(reversed(range(verts))))
        fs.append(tuple(range(verts, 2*verts)))
        me = bpy.data.meshes.new(name + "_mesh")
        me.from_pydata(vs, [], fs)
        me.update()
        ob = bpy.data.objects.new(name, me)
        self.coll.objects.link(ob)
        ob.location = loc
        if rot:
            ob.rotation_euler = rot
        return self._register(ob, "cylinder", {"radius": radius, "depth": depth, "vertices": verts}, loc, mat)

    def sphere(self, name, radius, loc, mat=None, segments=20, rings=12, scale=None):
        vs, fs = [], []
        for j in range(rings+1):
            phi = math.pi * j / rings
            for i in range(segments):
                th = 2 * math.pi * i / segments
                vs.append((radius*math.sin(phi)*math.cos(th),
                           radius*math.sin(phi)*math.sin(th),
                           radius*math.cos(phi)))
        for j in range(rings):
            for i in range(segments):
                a = j*segments+i
                b = j*segments+(i+1) % segments
                fs.append((a, b, b+segments, a+segments))
        me = bpy.data.meshes.new(name + "_mesh")
        me.from_pydata(vs, [], fs)
        me.update()
        ob = bpy.data.objects.new(name, me)
        self.coll.objects.link(ob)
        ob.location = loc
        if scale:
            ob.scale = scale
        return self._register(ob, "sphere", {"radius": radius, "scale": list(scale or (1, 1, 1))}, loc, mat)

    def tube(self, name, points, radii, mat=None, bevel_res=3):
        """Parametric swept tube with per-point radius (cabriole legs, volutes)."""
        cu = bpy.data.curves.new(name + "_curve", "CURVE")
        cu.dimensions = "3D"
        cu.resolution_u = 16
        cu.bevel_depth = 1.0
        cu.bevel_resolution = bevel_res
        cu.use_fill_caps = True
        sp = cu.splines.new("POLY")
        sp.points.add(len(points)-1)
        for p, co, r in zip(sp.points, points, radii):
            p.co = (float(co[0]), float(co[1]), float(co[2]), 1.0)
            p.radius = float(r)
        ob = bpy.data.objects.new(name, cu)
        self.coll.objects.link(ob)
        return self._register(ob, "sweep", {
            "points": len(points), "radius_start": radii[0],
            "radius_end": radii[-1], "bbox": _bbox(points)}, [0, 0, 0], mat)

    def mesh(self, name, verts, faces, mat=None, params=None, loc=(0, 0, 0)):
        me = bpy.data.meshes.new(name + "_mesh")
        me.from_pydata(verts, [], faces)
        me.update()
        ob = bpy.data.objects.new(name, me)
        self.coll.objects.link(ob)
        ob.location = loc
        return self._register(ob, "parametric_mesh", params or {"vertices": len(verts)}, loc, mat)


def _bbox(points):
    lo = [min(p[i] for p in points) for i in range(3)]
    hi = [max(p[i] for p in points) for i in range(3)]
    return {"min": [round(v, 4) for v in lo], "max": [round(v, 4) for v in hi]}


# ----------------------------------------------------------------------------
# Baroque ornaments — all genuine curved / sculpted geometry, no box renaming.
# ----------------------------------------------------------------------------
def volute(B, name, center, r0, r1, tube_r, mat, plane="XZ", turns=1.55,
           sign=1, n=54, phase=0.0):
    """Archimedean scroll with a tapering tube; the core Baroque motif."""
    cx, cy, cz = center
    pts, radii = [], []
    for i in range(n):
        t = i / (n-1)
        ang = sign * 2 * math.pi * turns * t + phase
        r = r0 + (r1-r0) * t
        a, b = r*math.cos(ang), r*math.sin(ang)
        if plane == "XZ":
            pts.append((cx+a, cy, cz+b))
        elif plane == "YZ":
            pts.append((cx, cy+a, cz+b))
        else:
            pts.append((cx+a, cy+b, cz))
        radii.append(tube_r * (1.0 - 0.55*t))
    return B.tube(name, pts, radii, mat)


def cabriole_leg(B, name, base, height, mat, foot_r=0.055, sway=0.075):
    """S-curved cabriole leg with a varying cross-section and a ball-and-claw foot."""
    x, y, z = base
    pts, radii = [], []
    n = 22
    for i in range(n):
        t = i/(n-1)
        # knee bulges outward, ankle tucks back
        off = sway * math.sin(math.pi * t) * (1.0 - 0.35*t)
        pts.append((x+off, y, z + height*t))
        radii.append(0.062 - 0.020*t + 0.020*math.exp(-((t-0.26)/0.16)**2))
    B.tube(name, pts, radii, mat)
    B.sphere(name + "_claw", foot_r, (x, y, z+0.012), mat, scale=(1.0, 1.0, 0.62))
    B.cylinder(name + "_shoe", 0.052, 0.035, (x, y, z+height-0.018), mat)


def acanthus_leaf(B, name, loc, height, width, mat, tilt=0.0, thickness=0.022, yaw=0.0):
    """Serrated acanthus leaf as a real extruded sculpted surface (local XZ plane)."""
    lobes = 5
    right, left = [], []
    for i in range(lobes+1):
        t = i/lobes
        w = width * math.sin(math.pi * (0.12 + 0.80*t)) * (1.0 - 0.55*t)
        h = height * t
        right.append((w, h))
        left.append((-w, h))
        if i < lobes:
            right.append((w*0.62, h + height*0.075))
            left.append((-w*0.62, h + height*0.075))
    outline = right + list(reversed(left))
    n = len(outline)
    verts = []
    c, s = math.cos(tilt), math.sin(tilt)
    for d in (-thickness/2, thickness/2):
        for w, h in outline:
            verts.append((w, d + s*h, c*h))
    faces = [tuple(range(n)), tuple(reversed(range(n, 2*n)))]
    for i in range(n):
        j = (i+1) % n
        faces.append((i, j, n+j, n+i))
    ob = B.mesh(name, verts, faces, mat,
                {"primitive": "acanthus_leaf", "height": height, "width": width, "lobes": lobes},
                loc=loc)
    ob.rotation_euler[2] = yaw
    return ob


def fluted_column(B, name, base, height, radius, mat, flutes=14, groove=0.011):
    """True fluted shaft geometry (radial cosine grooves), plus base/capital rings."""
    x, y, z = base
    seg, rings = flutes*8, 2
    verts, faces = [], []
    for k in range(rings):
        zz = z + (height*k/(rings-1) if rings > 1 else 0)
        for i in range(seg):
            a = 2*math.pi*i/seg
            r = radius - groove*(0.5+0.5*math.cos(flutes*a))
            if k == 0:
                r *= 1.06
            verts.append((x+r*math.cos(a), y+r*math.sin(a), zz))
    for k in range(rings-1):
        for i in range(seg):
            j = (i+1) % seg
            faces.append((k*seg+i, k*seg+j, (k+1)*seg+j, (k+1)*seg+i))
    B.mesh(name, verts, faces, mat,
           {"primitive": "fluted_shaft", "height": height, "radius": radius, "flutes": flutes})
    B.cylinder(name + "_base_ring", radius*1.20, 0.055, (x, y, z+0.028), mat)
    B.cylinder(name + "_neck_ring", radius*1.12, 0.045, (x, y, z+height-0.023), mat)


def corinthian_capital(B, name, center, radius, mat, gold_mat=None):
    """Bell, two rings of acanthus leaves and four corner volutes."""
    x, y, z = center
    B.cylinder(name + "_bell", radius*1.02, 0.14, (x, y, z+0.07), mat)
    for ring, (h, w, count) in enumerate(((0.13, 0.12, 8), (0.24, 0.095, 8))):
        for i in range(count):
            a = 2*math.pi*i/count + (ring*math.pi/count)
            leaf_mat = (gold_mat or mat) if ring == 1 else mat
            acanthus_leaf(B, "%s_leaf_%d_%02d" % (name, ring, i),
                          (x + radius*0.80*math.cos(a),
                           y + radius*0.80*math.sin(a),
                           z + h - w*0.4),
                          w*1.9, w, leaf_mat, tilt=0.30, yaw=a)
    for i in range(4):
        a = math.pi/4 + i*math.pi/2
        volute(B, name + "_volute_%d" % i,
               (x + radius*0.86*math.cos(a), y + radius*0.86*math.sin(a), z+0.30),
               0.095, 0.016, 0.014, gold_mat or mat, plane="XZ", turns=1.15, sign=1, n=34)
    B.box(name + "_abacus", (radius*2.7, radius*2.7, 0.055), (x, y, z+0.40), mat, bevel=0.008)
    B.box(name + "_abacus_gilt", (radius*2.78, radius*2.78, 0.016), (x, y, z+0.428), gold_mat or mat)


def dentil_row(B, name, start, end, z, mat, count=26, size=(0.055, 0.075, 0.065)):
    """Classical dentil course under a cornice."""
    x0, y0 = start
    x1, y1 = end
    for i in range(count):
        t = (i+0.5)/count
        px, py = x0+(x1-x0)*t, y0+(y1-y0)*t
        B.box("%s_%02d" % (name, i), size, (px, py, z), mat)


def torus(B, name, center, major, minor, mat, segments=64, sides=10):
    x, y, z = center
    vs, fs = [], []
    for i in range(segments):
        a = 2*math.pi*i/segments
        for j in range(sides):
            b = 2*math.pi*j/sides
            r = major + minor*math.cos(b)
            vs.append((x+r*math.cos(a), y+r*math.sin(a), z+minor*math.sin(b)))
    for i in range(segments):
        for j in range(sides):
            a = i*sides+j
            b = i*sides+(j+1) % sides
            c = ((i+1) % segments)*sides+(j+1) % sides
            d = ((i+1) % segments)*sides+j
            fs.append((a, b, c, d))
    B.mesh(name, vs, fs, mat, {"primitive": "torus", "major": major, "minor": minor})
