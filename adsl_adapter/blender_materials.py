"""Native Blender materials from parametric keys (Blender side, bpy only).

Shared by forge_runner.py (one object in a studio) and video_scene_builder.py
(the whole room), so a finish read from the video renders the same way on the
object and on the wall next to it. Nothing here decides a number: the class,
colour, roughness and pattern parameters come from material_spec.recipe(),
which is pure Python and tested outside Blender. This file only wires nodes.

Patterns that must have a real-world size (tile grid, terrazzo specks, marble
veins) are driven by world-space position projected along the dominant face
normal, so a 15 cm tile is 15 cm on a wall, a floor or a splashback without
needing UVs. Grain and weave keep the per-object Generated coordinates they
always used, so previously accepted furniture renders unchanged.
"""
from __future__ import annotations

import bpy

import material_spec  # noqa: E402  (pure Python, sibling module)


# --------------------------------------------------------------------------- #
# node helpers
# --------------------------------------------------------------------------- #
def _principled(name: str):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    out.is_active_output = True
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    return mat, nt, bsdf


def _find_bsdf(nt):
    """Locate the Principled BSDF node by TYPE, not by its display name.

    The node's instance name is localised (e.g. '原理化 BSDF' on a zh_CN
    Blender), so a string lookup like nodes['Principled BSDF'] KeyError's on a
    non-English install. The node TYPE is locale-independent."""
    for node in nt.nodes:
        if node.type == "BSDF_PRINCIPLED":
            return node
    raise RuntimeError("no Principled BSDF node in material node tree")


def _set(bsdf, name: str, value) -> None:
    """Set a BSDF input if this Blender has it (socket sets drift by version)."""
    sock = bsdf.inputs.get(name)
    if sock is not None:
        sock.default_value = value


def _noise(nt, scale, detail=3.0, stretch=None):
    tc = nt.nodes.new("ShaderNodeTexCoord")
    vec = tc.outputs["Generated"]
    if stretch:
        mul = nt.nodes.new("ShaderNodeVectorMath")
        mul.operation = "MULTIPLY"
        mul.inputs[1].default_value = stretch
        nt.links.new(vec, mul.inputs[0])
        vec = mul.outputs[0]
    n = nt.nodes.new("ShaderNodeTexNoise")
    n.inputs["Scale"].default_value = scale
    n.inputs["Detail"].default_value = detail
    nt.links.new(vec, n.inputs["Vector"])
    return n


def _ramp(nt, fac_socket, dark, light, p0=0.25, p1=0.80):
    r = nt.nodes.new("ShaderNodeValToRGB")
    r.color_ramp.elements[0].position = p0
    r.color_ramp.elements[0].color = (*dark, 1)
    r.color_ramp.elements[1].position = p1
    r.color_ramp.elements[1].color = (*light, 1)
    nt.links.new(fac_socket, r.inputs[0])
    return r


def _bump(nt, height_socket, bsdf, strength, distance):
    b = nt.nodes.new("ShaderNodeBump")
    b.inputs["Strength"].default_value = strength
    b.inputs["Distance"].default_value = distance
    nt.links.new(height_socket, b.inputs["Height"])
    nt.links.new(b.outputs["Normal"], bsdf.inputs["Normal"])
    return b


def _math(nt, op, a, b=None):
    m = nt.nodes.new("ShaderNodeMath")
    m.operation = op
    for i, v in enumerate((a, b)):
        if v is None:
            continue
        if isinstance(v, (int, float)):
            m.inputs[i].default_value = float(v)
        else:
            nt.links.new(v, m.inputs[i])
    return m.outputs[0]


def _box_vector(nt):
    """World position projected onto the plane of the dominant normal axis.

    Walls, floors and splashbacks are axis-aligned, so exactly one weight is 1
    and the vector is (y,z), (x,z) or (x,y) in metres. That is all a tile grid
    or a terrazzo pattern needs -- no UV unwrap, and the scale is physical.
    """
    geo = nt.nodes.new("ShaderNodeNewGeometry")
    pos = nt.nodes.new("ShaderNodeSeparateXYZ")
    nrm = nt.nodes.new("ShaderNodeSeparateXYZ")
    nt.links.new(geo.outputs["Position"], pos.inputs[0])
    nt.links.new(geo.outputs["Normal"], nrm.inputs[0])
    ax = [_math(nt, "ABSOLUTE", nrm.outputs[i]) for i in range(3)]
    # weight_x = |nx| > |ny| and |nx| > |nz|, etc. (ties resolve to z, the floor)
    wx = _math(nt, "MULTIPLY", _math(nt, "GREATER_THAN", ax[0], ax[1]),
               _math(nt, "GREATER_THAN", ax[0], ax[2]))
    wy = _math(nt, "MULTIPLY", _math(nt, "GREATER_THAN", ax[1], ax[0]),
               _math(nt, "GREATER_THAN", ax[1], ax[2]))
    wz = _math(nt, "SUBTRACT", _math(nt, "SUBTRACT", 1.0, wx), wy)

    def combine(u, v):
        c = nt.nodes.new("ShaderNodeCombineXYZ")
        nt.links.new(u, c.inputs[0])
        nt.links.new(v, c.inputs[1])
        return c.outputs[0]

    proj = [combine(pos.outputs[1], pos.outputs[2]),    # x-facing: (y, z)
            combine(pos.outputs[0], pos.outputs[2]),    # y-facing: (x, z)
            combine(pos.outputs[0], pos.outputs[1])]    # z-facing: (x, y)
    total = None
    for vec, w in zip(proj, (wx, wy, wz)):
        sc = nt.nodes.new("ShaderNodeVectorMath")
        sc.operation = "SCALE"
        nt.links.new(vec, sc.inputs[0])
        nt.links.new(w, sc.inputs["Scale"])
        if total is None:
            total = sc.outputs[0]
        else:
            add = nt.nodes.new("ShaderNodeVectorMath")
            add.operation = "ADD"
            nt.links.new(total, add.inputs[0])
            nt.links.new(sc.outputs[0], add.inputs[1])
            total = add.outputs[0]
    return total


# --------------------------------------------------------------------------- #
# classic finishes (unchanged recipes -- accepted assets depend on them)
# --------------------------------------------------------------------------- #
def fabric(name: str, base, dark, light, weave=210.0, roughness=0.80, sheen=0.30):
    """Woven textile: fine cross-hatch bump + tonal drift, exactly the recipe
    FineScene's present.py uses for upholstery (which is where the Oakwarden's
    leather read came from). No tiled colour map, so no accidental plaid."""
    mat, nt, bsdf = _principled(name)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Sheen Weight"].default_value = sheen
    bsdf.inputs["Sheen Roughness"].default_value = 0.6
    bsdf.inputs["Sheen Tint"].default_value = (1, 1, 1, 1)
    weave_noise = _noise(nt, weave, detail=2.0)
    slub = _noise(nt, 18.0, detail=4.0, stretch=(9.0, 9.0, 0.35))
    mix = nt.nodes.new("ShaderNodeMixRGB")
    mix.blend_type = "OVERLAY"
    mix.inputs["Fac"].default_value = 0.55
    nt.links.new(weave_noise.outputs["Fac"], mix.inputs["Color1"])
    nt.links.new(slub.outputs["Fac"], mix.inputs["Color2"])
    nt.links.new(_ramp(nt, mix.outputs[0], dark, light).outputs[0],
                 bsdf.inputs["Base Color"])
    _bump(nt, weave_noise.outputs["Fac"], bsdf, 0.26, 0.0009)
    mat["adsl_material"] = name
    mat["pbr_source"] = "procedural (noise weave) following FineScene present.py"
    return mat


def wood(name: str, dark, light, grain=5.0, coat=0.25):
    mat, nt, bsdf = _principled(name)
    bsdf.inputs["Roughness"].default_value = 0.38
    bsdf.inputs["Coat Weight"].default_value = coat
    bsdf.inputs["Coat Roughness"].default_value = 0.16
    n = _noise(nt, grain, detail=5.0, stretch=(7.0, 8.0, 0.55))
    nt.links.new(_ramp(nt, n.outputs["Fac"], dark, light, 0.18, 0.84).outputs[0],
                 bsdf.inputs["Base Color"])
    _bump(nt, n.outputs["Fac"], bsdf, 0.20, 0.0016)
    mat["adsl_material"] = name
    mat["pbr_source"] = "procedural (noise grain)"
    return mat


def solid(name: str, rgb, metallic=0.0, roughness=0.5, emission=None):
    mat, nt, bsdf = _principled(name)
    bsdf.inputs["Base Color"].default_value = (*rgb, 1)
    bsdf.inputs["Metallic"].default_value = metallic
    bsdf.inputs["Roughness"].default_value = roughness
    if emission:
        bsdf.inputs["Emission Color"].default_value = (*emission[:3], 1)
        bsdf.inputs["Emission Strength"].default_value = emission[3]
    if metallic > 0.5:
        n = _noise(nt, 40.0, detail=2.0)
        _bump(nt, n.outputs["Fac"], bsdf, 0.10, 0.0004)
    mat["adsl_material"] = name
    return mat


# --------------------------------------------------------------------------- #
# observed-scene patterns
# --------------------------------------------------------------------------- #
def _tile(nt, bsdf, base, pat, rough):
    """Stack-bond tile grid with recessed matte grout, sized in metres."""
    brick = nt.nodes.new("ShaderNodeTexBrick")
    brick.offset = 0.0
    brick.offset_frequency = 2
    brick.squash = 1.0
    brick.squash_frequency = 2
    nt.links.new(_box_vector(nt), brick.inputs["Vector"])
    size = float(pat.get("size", 0.15)) / float(pat.get("scale", 1.0))
    brick.inputs["Scale"].default_value = 1.0
    brick.inputs["Brick Width"].default_value = size
    brick.inputs["Row Height"].default_value = size
    brick.inputs["Mortar Size"].default_value = float(pat.get("gap", 0.003))
    brick.inputs["Mortar Smooth"].default_value = 0.1
    brick.inputs["Bias"].default_value = 0.0
    brick.inputs["Color1"].default_value = (*base, 1)
    brick.inputs["Color2"].default_value = (*[c * 0.97 for c in base], 1)
    brick.inputs["Mortar"].default_value = (*pat["grout"], 1)
    nt.links.new(brick.outputs["Color"], bsdf.inputs["Base Color"])
    # Grout is matte and sits below the glaze: roughness and height follow it.
    rmap = nt.nodes.new("ShaderNodeMapRange")
    rmap.inputs["To Min"].default_value = rough
    rmap.inputs["To Max"].default_value = 0.85
    nt.links.new(brick.outputs["Fac"], rmap.inputs["Value"])
    nt.links.new(rmap.outputs[0], bsdf.inputs["Roughness"])
    _bump(nt, _math(nt, "SUBTRACT", 1.0, brick.outputs["Fac"]), bsdf, 0.35, 0.002)


def _veins(nt, bsdf, base, pat):
    """Marble: thin distorted bands of the vein colour through the base."""
    n = nt.nodes.new("ShaderNodeTexNoise")
    nt.links.new(_box_vector(nt), n.inputs["Vector"])
    n.inputs["Scale"].default_value = 2.2 * float(pat.get("scale", 1.0))
    n.inputs["Detail"].default_value = 9.0
    n.inputs["Distortion"].default_value = 3.5
    r = nt.nodes.new("ShaderNodeValToRGB")
    els = r.color_ramp.elements
    els[0].position, els[0].color = 0.0, (*base, 1)
    els[1].position, els[1].color = 1.0, (*base, 1)
    for pos, col in ((0.475, base), (0.5, pat["vein"]), (0.525, base)):
        e = els.new(pos)
        e.color = (*col, 1)
    nt.links.new(n.outputs["Fac"], r.inputs[0])
    nt.links.new(r.outputs["Color"], bsdf.inputs["Base Color"])


def _speckle(nt, bsdf, base, pat):
    """Terrazzo / speckled vinyl: small specks of a second colour."""
    v = nt.nodes.new("ShaderNodeTexVoronoi")
    nt.links.new(_box_vector(nt), v.inputs["Vector"])
    # speck diameter d (m) and area coverage c: Voronoi cells of size d/(2t)
    # with t = sqrt(c/pi), a speck wherever the F1 distance is below t
    d = float(pat.get("size") or 0.0023)
    t = (float(pat.get("coverage") or 0.08) / 3.14159) ** 0.5
    v.inputs["Scale"].default_value = 2.0 * t / d * float(pat.get("scale", 1.0))
    v.inputs["Randomness"].default_value = 1.0
    r = nt.nodes.new("ShaderNodeValToRGB")
    r.color_ramp.interpolation = "CONSTANT"
    els = r.color_ramp.elements
    els[0].position, els[0].color = 0.0, (*pat["speck"], 1)
    els[1].position, els[1].color = t, (*base, 1)
    nt.links.new(v.outputs["Distance"], r.inputs[0])
    nt.links.new(r.outputs["Color"], bsdf.inputs["Base Color"])


def _terrazzo(nt, bsdf, base, pat):
    """Cast terrazzo (the pantry floor, t = 60-66 s, 190-210 s): chips of three
    tones and sizes with irregular outlines, each chip a slightly different
    shade. Sizes (m) and area fractions read off the close-ups."""
    vec = _box_vector(nt)
    jit = nt.nodes.new("ShaderNodeTexNoise")              # irregular chip outlines
    nt.links.new(vec, jit.inputs["Vector"])
    jit.inputs["Scale"].default_value = 160.0
    jit.inputs["Detail"].default_value = 2.0
    off = nt.nodes.new("ShaderNodeVectorMath")
    off.operation = "SUBTRACT"
    nt.links.new(jit.outputs["Color"], off.inputs[0])
    off.inputs[1].default_value = (0.5, 0.5, 0.5)
    sc = nt.nodes.new("ShaderNodeVectorMath")
    sc.operation = "SCALE"
    nt.links.new(off.outputs[0], sc.inputs[0])
    sc.inputs["Scale"].default_value = 0.0035
    add = nt.nodes.new("ShaderNodeVectorMath")
    add.operation = "ADD"
    nt.links.new(vec, add.inputs[0])
    nt.links.new(sc.outputs[0], add.inputs[1])
    pos = add.outputs[0]
    dark = tuple(pat["speck"])
    mid = tuple(0.5 * (a + b) for a, b in zip(base, dark))
    light = tuple(min(1.0, c * 1.18) for c in base)
    color = None
    prev = None
    # mostly small mid-grey chips, fewer dark ones (t = 60-66 s, 190-210 s: low contrast up close)
    for size, cov, col in ((0.0065, 0.035, dark), (0.0055, 0.13, mid), (0.0038, 0.06, light),
                           (0.0026, 0.06, mid)):
        v = nt.nodes.new("ShaderNodeTexVoronoi")
        nt.links.new(pos, v.inputs["Vector"])
        t = (cov / 3.14159) ** 0.5
        v.inputs["Scale"].default_value = 2.0 * t / size * float(pat.get("scale", 1.0))
        v.inputs["Randomness"].default_value = 1.0
        mask = _math(nt, "LESS_THAN", v.outputs["Distance"], t)
        tint = nt.nodes.new("ShaderNodeMixRGB")            # per-chip shade
        tint.blend_type = "MIX"
        sep = nt.nodes.new("ShaderNodeSeparateColor")
        nt.links.new(v.outputs["Color"], sep.inputs[0])
        nt.links.new(sep.outputs[0], tint.inputs["Fac"])
        tint.inputs["Color1"].default_value = (*[c * 0.8 for c in col], 1)
        tint.inputs["Color2"].default_value = (*[min(1.0, c * 1.2) for c in col], 1)
        mix = nt.nodes.new("ShaderNodeMixRGB")
        mix.blend_type = "MIX"
        nt.links.new(mask, mix.inputs["Fac"])
        if prev is None:
            mix.inputs["Color1"].default_value = (*base, 1)
        else:
            nt.links.new(prev, mix.inputs["Color1"])
        nt.links.new(tint.outputs[0], mix.inputs["Color2"])
        prev = mix.outputs[0]
    nt.links.new(prev, bsdf.inputs["Base Color"])


def from_recipe(name: str, rec: dict):
    """Build one material from a material_spec.recipe() dict."""
    pat = rec.get("pattern") or {}
    ptype = pat.get("type")
    rough = float(rec["roughness"])
    base = tuple(rec["base_rgb"])
    if ptype == "wood":
        mat = wood(name, tuple(pat["dark"]), tuple(pat["light"]), grain=5.5,
                   coat=rec.get("coat", 0.22))
        bsdf = _find_bsdf(mat.node_tree)
        bsdf.inputs["Roughness"].default_value = rough
    elif ptype == "fabric":
        mat = fabric(name, base, tuple(pat["dark"]), tuple(pat["light"]),
                     roughness=rough, sheen=rec.get("sheen", 0.30))
        bsdf = _find_bsdf(mat.node_tree)
    else:
        emission = None
        if rec.get("emission_rgb") is not None and rec.get("emission_strength", 0) > 0:
            emission = (*rec["emission_rgb"], float(rec["emission_strength"]))
        mat = solid(name, base, metallic=float(rec.get("metallic", 0.0)),
                    roughness=rough, emission=emission)
        bsdf = _find_bsdf(mat.node_tree)
        nt = mat.node_tree
        if ptype == "tile":
            _tile(nt, bsdf, base, pat, rough)
        elif ptype == "veins":
            _veins(nt, bsdf, base, pat)
        elif ptype == "speckle":
            _speckle(nt, bsdf, base, pat)
        elif ptype == "terrazzo":
            _terrazzo(nt, bsdf, base, pat)
        elif ptype == "leather":
            n = _noise(nt, 95.0, detail=3.0)
            _bump(nt, n.outputs["Fac"], bsdf, 0.14, 0.0006)
        elif ptype == "brushed":
            n = _noise(nt, 60.0, detail=2.0, stretch=(1.0, 1.0, 40.0))
            _bump(nt, n.outputs["Fac"], bsdf, 0.08, 0.0003)
    _set(bsdf, "Coat Weight", float(rec.get("coat", 0.0)))
    _set(bsdf, "Coat Roughness", float(rec.get("coat_roughness", 0.12)))
    if rec.get("sheen"):
        _set(bsdf, "Sheen Weight", float(rec["sheen"]))
    if rec.get("transmission"):
        _set(bsdf, "Transmission Weight", float(rec["transmission"]))
        _set(bsdf, "IOR", float(rec.get("ior", 1.45)))
    if rec.get("anisotropic"):
        _set(bsdf, "Anisotropic", float(rec["anisotropic"]))
    if float(rec.get("alpha", 1.0)) < 1.0:
        _set(bsdf, "Alpha", float(rec["alpha"]))
    return mat


def parametric_material(spec: dict):
    """Build a native Blender material from a parsed `<class>:#hex[...]` key.

    No image textures: the class picks the shading model, the colour and
    roughness come from the key, and grain / weave / tile / veins / specks are
    procedural. That keeps a finish a parameter of the program (editable by
    changing a string) instead of a file that has to exist on disk.
    """
    mat = from_recipe(spec["name"], material_spec.recipe(spec))
    mat["adsl_material"] = spec["key"]
    mat["adsl_material_class"] = spec["kind"]
    mat["adsl_material_hex"] = spec["hex"]
    mat["pbr_source"] = "parametric (native Principled BSDF, procedural detail)"
    return mat


def material_for_key(key: str, cache: dict):
    """Look up or build the material for a parametric key; None if invalid."""
    if key in cache:
        return cache[key]
    spec = material_spec.parse(key)
    if spec is None:
        return None
    cache[key] = parametric_material(spec)
    return cache[key]
