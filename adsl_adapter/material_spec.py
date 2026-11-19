"""Parametric material keys: `<class>:<#hex>[:<roughness>][:<modifier>...]`.

Why this exists
---------------
The forge offered a fixed palette of fourteen named materials. A reference
image of a white-lacquered shelf or a sage-green sofa had nothing to map to,
so the Agent picked the nearest name and the result was the right shape in
the wrong finish. Most finishes do not need a texture file at all: a class
(how the surface responds to light) plus a colour is the whole description,
and Blender's native Principled BSDF with procedural grain/weave renders it.

So a material is now also a *parameter*: "paint:#F3F1EC", "wood:#B58A5A",
"fabric:#8FA08A:0.9". Changing a finish is editing a string in the program,
which is the same kind of edit as changing a height.

Modifiers (video input)
-----------------------
A pantry filmed on a phone is not made of paint and timber alone: a white
tile splashback has black grout on a 15 cm grid, the counter is marble with
grey veins, the floor is speckled vinyl, the sink is brushed steel, and the
water dispenser's display glows blue. None of that needs an image texture
either -- each is a procedural pattern with one or two extra numbers -- so the
grammar takes optional `key=value` modifiers after the roughness:

    tile:#F4F4F2:0.12:grout=#1E1E1E:size=0.15:gap=0.004
    stone:#ECECE8:0.15:veins=#9A9A9A
    stone:#D9D9D6:0.45:speckle=#5A5A5A
    metal:#B9BBBD:0.32:brushed
    screen:#0E0E12:0.06:emit=#3355FF:strength=3
    light:#FFFFFF:0.5:strength=12            (a glowing diffuser)

Old keys parse exactly as before; modifiers are only ever additive.

This module is the grammar and the shading *recipe* only -- pure Python, no
bpy -- so the orchestrator can reject a malformed key before Blender starts,
measured colours can be turned into keys anywhere, and tests run everywhere.
blender_materials.py turns a recipe into a node tree.
"""
from __future__ import annotations

import re

# class -> default roughness. The class decides the shading model; the colour
# and (optionally) roughness come from the key.
CLASSES = {
    "paint": 0.35,     # lacquer, painted timber / MDF, enamel, wall paint
    "plastic": 0.50,
    "metal": 0.30,     # colour is the metal's tint: brass, steel, black iron
    "glass": 0.03,     # clear or tinted, transmissive
    "ceramic": 0.30,
    "stone": 0.25,     # marble, terrazzo, concrete (raise the roughness)
    "wood": 0.38,      # procedural grain around the given colour
    "fabric": 0.80,    # procedural weave around the given colour
    "velvet": 0.85,    # fabric with strong sheen
    "leather": 0.45,
    "shade": 0.75,     # lamp shade: the colour also glows faintly
    # ---- added for observed (video) scenes ----
    "tile": 0.12,      # glazed tile on a grid; grout colour / size / gap modifiers
    "rubber": 0.85,    # cables, seals, feet, bin liners
    "screen": 0.06,    # dark glossy display glass; may emit (emit=, strength=)
    "light": 0.50,     # a surface whose job is to glow: diffuser, LED bar
}

# Modifier keys and how their value is read. Colours are sRGB hex like the
# base colour; numbers are plain floats in the unit given.
_COLOUR_MODS = ("emit", "grout", "veins", "speckle", "grain")
_NUMBER_MODS = {
    "strength": (0.0, 1000.0),      # emission strength (Blender units)
    "size": (0.002, 2.0),           # tile edge / speck diameter, metres
    "coverage": (0.001, 0.6),       # speck area fraction
    "gap": (0.0005, 0.05),          # grout width, metres
    "metallic": (0.0, 1.0),
    "alpha": (0.0, 1.0),
    "transmission": (0.0, 1.0),
    "scale": (0.05, 50.0),          # pattern frequency multiplier
}
_FLAG_MODS = ("brushed", "terrazzo")

# Default emission strength per class when emit= is given without strength=.
# A display is read from across the room but lights nothing; a diffuser is
# the visible face of a real light source.
_DEFAULT_STRENGTH = {"screen": 2.5, "light": 10.0, "plastic": 2.0, "glass": 2.0,
                     "shade": 0.45}

_HEX = r"#([0-9a-fA-F]{6})"
_HEAD = re.compile(r"^([a-z]+):" + _HEX + r"$")
_ROUGH = re.compile(r"^[0-9]*\.?[0-9]+$")


def looks_parametric(key: str) -> bool:
    """True for anything shaped like `word:#...`, valid or not -- used to tell
    a mistyped parametric key from an ordinary string in the program."""
    return isinstance(key, str) and re.match(r"^[A-Za-z_]+:#", key) is not None


def srgb_to_linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def linear_to_srgb(c: float) -> float:
    c = max(0.0, min(1.0, c))
    return c * 12.92 if c <= 0.0031308 else 1.055 * (c ** (1.0 / 2.4)) - 0.055


def hex_to_linear(hexa: str) -> tuple:
    hexa = hexa.lstrip("#")
    return tuple(round(srgb_to_linear(int(hexa[i:i + 2], 16) / 255.0), 5)
                 for i in (0, 2, 4))


def linear_to_hex(rgb) -> str:
    return "#" + "".join(f"{int(round(linear_to_srgb(float(c)) * 255)):02X}"
                         for c in rgb[:3])


def _parse_mods(tokens: list[str]) -> dict | None:
    mods: dict = {}
    for tok in tokens:
        if tok in _FLAG_MODS:
            mods[tok] = True
            continue
        if "=" not in tok:
            return None
        key, val = tok.split("=", 1)
        if key in _COLOUR_MODS:
            m = re.match("^" + _HEX + "$", val)
            if not m:
                return None
            mods[key] = "#" + m.group(1).upper()
        elif key in _NUMBER_MODS:
            try:
                num = float(val)
            except ValueError:
                return None
            lo, hi = _NUMBER_MODS[key]
            if not lo <= num <= hi:
                return None
            mods[key] = num
        else:
            return None
    return mods


def parse(key: str) -> dict | None:
    """Parse a parametric key. Returns None when `key` is not one.

    The hex colour is what a person would sample from a photo (sRGB); Blender
    wants linear values, so the conversion happens here, once.
    """
    if not isinstance(key, str):
        return None
    tokens = key.strip().split(":")
    if len(tokens) < 2:
        return None
    m = _HEAD.match(tokens[0] + ":" + tokens[1])
    if not m or m.group(1) not in CLASSES:
        return None
    kind, hexa = m.group(1), m.group(2).upper()
    rest = tokens[2:]
    rough = None
    if rest and _ROUGH.match(rest[0]):
        rough, rest = rest[0], rest[1:]
    mods = _parse_mods(rest)
    if mods is None:
        return None
    roughness = CLASSES[kind] if rough is None else float(rough)
    if not 0.0 <= roughness <= 1.0:
        return None
    spec = {"key": key.strip(), "kind": kind, "hex": "#" + hexa,
            "rgb": hex_to_linear(hexa), "roughness": roughness,
            "mods": mods,
            "name": f"{kind.title()} | #{hexa}"
                    + ("" if rough is None else f" r{roughness:g}")
                    + "".join(f" {k}" if v is True else f" {k}{v}"
                              for k, v in sorted(mods.items()))}
    if "emit" in mods or kind == "light":
        emit_hex = mods.get("emit", "#" + hexa)
        spec["emit_rgb"] = hex_to_linear(emit_hex)
        spec["emit_strength"] = float(mods.get(
            "strength", _DEFAULT_STRENGTH.get(kind, 2.0)))
    return spec


def is_emissive(key) -> bool:
    spec = parse(key) if isinstance(key, str) else None
    return bool(spec and (spec.get("emit_strength", 0) > 0 or spec["kind"] == "shade"))


def explain(key: str) -> str:
    """Why a parametric-looking key was rejected, as Agent feedback."""
    return (f"'{key}' is not a valid material. Use a named key, or the form "
            f"<class>:#RRGGBB[:roughness 0-1][:modifier...] with class one of "
            f"{', '.join(sorted(CLASSES))}; modifiers: emit=#RRGGBB, strength=N, "
            f"grout=#RRGGBB, size=metres, gap=metres, veins=#RRGGBB, "
            f"speckle=#RRGGBB, grain=#RRGGBB, metallic=0-1, alpha=0-1, "
            f"transmission=0-1, brushed -- e.g. 'paint:#F3F1EC', "
            f"'tile:#F4F4F2:0.12:grout=#1E1E1E:size=0.15', "
            f"'screen:#0E0E12:0.06:emit=#3355FF:strength=3'.")


def shades(rgb: tuple, dark: float, light: float) -> tuple[tuple, tuple]:
    """Dark and light variants of a colour, for a grain or weave ramp."""
    return (tuple(max(0.0, c * dark) for c in rgb),
            tuple(min(1.0, c * light) for c in rgb))


# --------------------------------------------------------------------------
# shading recipe: everything Blender needs, decided here in pure Python
# --------------------------------------------------------------------------
def recipe(spec: dict) -> dict:
    """The Principled-BSDF settings and procedural pattern for a parsed key.

    Deciding the numbers here (not in the bpy code) keeps the Blender side a
    thin interpreter and lets tests pin the shading of every class.
    """
    kind, rgb, rough, mods = spec["kind"], spec["rgb"], spec["roughness"], spec["mods"]
    r = {"kind": kind, "base_rgb": rgb, "roughness": rough,
         "metallic": 0.0, "coat": 0.0, "coat_roughness": 0.12, "sheen": 0.0,
         "transmission": 0.0, "ior": 1.45, "alpha": 1.0, "anisotropic": 0.0,
         "emission_rgb": None, "emission_strength": 0.0, "pattern": None}
    if kind == "metal":
        r["metallic"] = 0.92
        if mods.get("brushed"):
            r["anisotropic"] = 0.6
            r["pattern"] = {"type": "brushed"}
    elif kind in ("paint", "ceramic"):
        r["coat"] = 0.30 if kind == "ceramic" else 0.15
    elif kind == "glass":
        r["transmission"], r["ior"] = 1.0, 1.45
    elif kind == "screen":
        r["coat"], r["coat_roughness"] = 0.6, 0.03
    elif kind == "leather":
        r["sheen"] = 0.10
        r["pattern"] = {"type": "leather"}
    elif kind == "wood":
        dark, light = shades(rgb, 0.55, 1.18)
        if "grain" in mods:
            dark = hex_to_linear(mods["grain"])
        r["coat"] = 0.22
        r["pattern"] = {"type": "wood", "dark": dark, "light": light}
    elif kind in ("fabric", "velvet"):
        dark, light = shades(rgb, 0.70, 1.12)
        r["sheen"] = 0.80 if kind == "velvet" else 0.30
        r["pattern"] = {"type": "fabric", "dark": dark, "light": light}
    elif kind == "tile":
        r["coat"] = 0.25
        r["pattern"] = {"type": "tile",
                        "grout": hex_to_linear(mods.get("grout", "#BDBDBA")),
                        "size": float(mods.get("size", 0.15)),
                        "gap": float(mods.get("gap", 0.003))}
    elif kind == "stone":
        if "veins" in mods:
            r["pattern"] = {"type": "veins", "vein": hex_to_linear(mods["veins"])}
            r["coat"] = 0.2
        elif mods.get("terrazzo"):
            # cast terrazzo: three layers of irregular chips (dark, mid grey,
            # near white) of different sizes; the speckle colour is the dark one
            r["pattern"] = {"type": "terrazzo", "speck": hex_to_linear(mods.get("speckle", "#55585A"))}
        elif "speckle" in mods:
            r["pattern"] = {"type": "speckle", "speck": hex_to_linear(mods["speckle"])}
            if "size" in mods:
                r["pattern"]["size"] = float(mods["size"])
            if "coverage" in mods:
                r["pattern"]["coverage"] = float(mods["coverage"])
    elif kind == "shade":
        r["emission_rgb"], r["emission_strength"] = rgb, 0.45
    if r["pattern"] is not None and "scale" in mods:
        r["pattern"]["scale"] = float(mods["scale"])
    for name in ("metallic", "alpha", "transmission"):
        if name in mods:
            r[name] = float(mods[name])
    if "emit_rgb" in spec:
        r["emission_rgb"] = spec["emit_rgb"]
        r["emission_strength"] = spec["emit_strength"]
    if kind == "light":
        # A diffuser: the base colour is the glowing surface as seen when off.
        r["roughness"] = max(rough, 0.4)
    return r


# --------------------------------------------------------------------------
# observation -> key (used by the video stages)
# --------------------------------------------------------------------------
FINISH_ROUGHNESS = {"mirror": 0.04, "polished": 0.08, "glossy": 0.14,
                    "semi-gloss": 0.25, "satin": 0.38, "matte": 0.75,
                    "rough": 0.9}

# Class aliases a VLM actually writes, mapped onto the grammar's classes.
CLASS_ALIASES = {
    "marble": "stone", "granite": "stone", "terrazzo": "stone", "concrete": "stone",
    "quartz": "stone", "porcelain": "ceramic", "enamel": "paint", "lacquer": "paint",
    "laminate": "wood", "veneer": "wood", "mdf": "paint", "steel": "metal",
    "stainless": "metal", "chrome": "metal", "aluminium": "metal", "aluminum": "metal",
    "brass": "metal", "iron": "metal", "polymer": "plastic", "abs": "plastic",
    "acrylic": "plastic", "silicone": "rubber", "textile": "fabric", "cloth": "fabric",
    "display": "screen", "lcd": "screen", "led": "light", "diffuser": "light",
    "mirror": "metal", "paper": "paint", "plaster": "paint", "vinyl": "plastic",
}


def normalise_class(name: str | None) -> str:
    name = (name or "").strip().lower()
    if name in CLASSES:
        return name
    for word, cls in CLASS_ALIASES.items():
        if word in name:
            return cls
    return "plastic"


def _clean_hex(value, default: str = "#808080") -> str:
    m = re.search(_HEX, str(value or ""))
    return "#" + m.group(1).upper() if m else default


def from_observation(cls: str | None, hexa: str | None, finish: str | None = None,
                     *, emissive: bool = False, emit_hex: str | None = None,
                     strength: float | None = None, pattern: dict | None = None,
                     metallic: float | None = None, translucent: bool = False,
                     roughness: float | None = None) -> str:
    """Turn a described surface (class, colour, finish, pattern) into a key.

    Every field is optional and sanitised: a VLM writes 'stainless steel',
    'Satin', '#b8b8b8 (brushed)'; the key that comes out always parses.
    """
    kind = normalise_class(cls)
    base = _clean_hex(hexa)
    if roughness is None:
        roughness = FINISH_ROUGHNESS.get((finish or "").strip().lower(), CLASSES[kind])
    roughness = max(0.02, min(0.98, float(roughness)))
    tokens = [f"{kind}:{base}", f"{roughness:.2f}"]
    pattern = pattern or {}
    ptype = str(pattern.get("type") or "").lower()
    second = pattern.get("secondary_hex")
    if kind == "tile" or "tile" in ptype or "grid" in ptype:
        if kind not in ("tile",):
            kind = "tile"
            tokens[0] = f"tile:{base}"
        tokens.append(f"grout={_clean_hex(second, '#BDBDBA')}")
        size = pattern.get("scale_m") or pattern.get("size_m")
        try:
            size = float(size)
            if 0.005 <= size <= 2.0:
                tokens.append(f"size={size:.3f}")
        except (TypeError, ValueError):
            pass
    elif kind == "stone" and ("vein" in ptype or "marble" in ptype) and second:
        tokens.append(f"veins={_clean_hex(second)}")
    elif kind == "stone" and ("speck" in ptype or "terrazzo" in ptype
                              or "fleck" in ptype) and second:
        tokens.append(f"speckle={_clean_hex(second)}")
    elif kind == "wood" and second:
        tokens.append(f"grain={_clean_hex(second)}")
    elif kind == "metal" and ("brush" in ptype or "brush" in (finish or "").lower()):
        tokens.append("brushed")
    if metallic is not None and kind != "metal":
        try:
            if float(metallic) >= 0.5:
                tokens[0] = f"metal:{base}"
        except (TypeError, ValueError):
            pass
    if translucent and kind in ("plastic", "glass"):
        tokens.append("transmission=0.6" if kind == "plastic" else "alpha=0.9")
    if emissive:
        tokens.append(f"emit={_clean_hex(emit_hex, base)}")
        if strength is not None:
            tokens.append(f"strength={max(0.0, min(1000.0, float(strength))):g}")
    key = ":".join(tokens)
    if parse(key) is None:          # never hand back something that will not parse
        key = f"{kind}:{base}:{roughness:.2f}"
    return key
