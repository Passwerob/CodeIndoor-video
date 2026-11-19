"""Light sources and materials, read from the video and MEASURED where possible.

Why this is its own stage
-------------------------
Asked once, over a contact sheet, the VLM called this pantry "day" (the
window is black glass with the camera operator reflected in it), and called
the white cylindrical pendants "small black spot heads". Given full-resolution
frames and a question about lighting only, the same model got both right.
And no VLM can be trusted with a colour value: the hex it writes is the
pixel it looked at, which mixes the surface with the light falling on it and
the phone's exposure for that frame.

So the split is:
  the VLM DECIDES what can only be judged -- day/night, which fixtures exist,
     their type, count, shape, beam, whether they are on; each material's
     class, finish, pattern; WHERE a clean sample of each material is;
  the code MEASURES what pixels can tell -- colours, from those regions.

Photometric anchor
------------------
A phone re-exposes and re-white-balances continuously, so a pixel value means
nothing across frames. Within ONE frame, though, the ratio of a surface to a
plain white wall in that frame is close to the ratio of their reflectances,
because the same exposure and white balance apply to both. Every measured
colour is therefore expressed as

    albedo = pixel_linear / white_wall_linear * WHITE_ALBEDO      (per channel)

with WHITE_ALBEDO = 0.85 (matte white emulsion), then clamped to the range
physically plausible for the material class. Metals are the exception: what a
metal shows is its reflection, so their base colour comes from the class
prior and only the hue is taken from pixels.

Lights
------
A light is described physically (type, count, beam, colour temperature) and
the phone's auto white balance is described separately (camera.white_balance
_k): Blender 5.2 has both natively (light.temperature and the view's white
balance), so a 4000 K pendant renders as the neutral white the phone recorded
without lying about the lamp. Fixture positions come from the layout stage;
the Blender parameters are derived in light_plan().
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

import material_spec
import video_frames as vf
import vlm

WHITE_ALBEDO = 0.85
CLIP = 0.97
# Plausible linear albedo per class. A measured value outside is evidence of
# a bad sample (highlight, shadow), not of an exotic material.
CLASS_ALBEDO = {"paint": (0.03, 0.90), "plastic": (0.02, 0.88), "wood": (0.05, 0.55),
                "stone": (0.08, 0.88), "tile": (0.08, 0.90), "ceramic": (0.05, 0.90),
                "fabric": (0.03, 0.85), "velvet": (0.02, 0.6), "leather": (0.02, 0.6),
                "rubber": (0.02, 0.25), "screen": (0.004, 0.05), "glass": (0.6, 1.0),
                "shade": (0.3, 0.9), "light": (0.5, 1.0)}
METAL_PRIORS = [("chrome", "#E6E7E8", 0.06), ("mirror", "#E6E7E8", 0.04),
                ("brass", "#C9A15A", 0.3), ("gold", "#D4AF37", 0.25),
                ("copper", "#B87333", 0.3), ("alumin", "#C8CACC", 0.35),
                ("black", "#2E2E30", 0.35), ("stainless", "#B4B6B8", 0.32),
                ("steel", "#B4B6B8", 0.32)]
BEAM_DEG = {"narrow": 50.0, "medium": 75.0, "wide": 110.0}
POWER_W = {"pendant": 40.0, "downlight": 30.0, "spot": 25.0, "track": 25.0,
           "panel": 80.0, "ceiling": 60.0, "strip": 15.0, "wall": 20.0,
           "lamp": 25.0, "globe": 40.0, "chandelier": 90.0, "screen": 0.0}


# --------------------------------------------------------------------------
# colour science (numpy, sRGB / linear)
# --------------------------------------------------------------------------
def srgb_to_lin(a):
    a = np.clip(np.asarray(a, dtype=np.float64), 0.0, 1.0)
    return np.where(a <= 0.04045, a / 12.92, ((a + 0.055) / 1.055) ** 2.4)


def lin_to_srgb(a):
    a = np.clip(np.asarray(a, dtype=np.float64), 0.0, 1.0)
    return np.where(a <= 0.0031308, a * 12.92, 1.055 * a ** (1 / 2.4) - 0.055)


def to_hex(lin) -> str:
    return material_spec.linear_to_hex([float(c) for c in lin])


def region_pixels(img: np.ndarray, bbox1000, shrink: float = 0.15) -> np.ndarray | None:
    """sRGB [0,1] RGB pixels inside a 0..1000 box, shrunk to dodge its edges."""
    h, w = img.shape[:2]
    box = vf.bbox_pixels(bbox1000, w, h, margin=-abs(shrink))
    if box is None:
        box = vf.bbox_pixels(bbox1000, w, h)
    if box is None:
        return None
    x0, y0, x1, y1 = box
    px = img[y0:y1, x0:x1, ::-1].reshape(-1, 3).astype(np.float64) / 255.0
    return px if len(px) else None


def robust_colour(px: np.ndarray, two_tone: bool = False) -> dict | None:
    """Median colour (linear) of the usable pixels; optionally two clusters.

    Clipped pixels (a highlight) and near-black pixels (a crevice) say nothing
    about the surface and are dropped. For patterned materials (tile + grout,
    marble + veins, terrazzo specks, wood grain) a 2-means split on luminance
    returns the dominant colour and the secondary one. A thin secondary
    feature (grout lines two pixels wide in a 544 px frame) is mostly mixed
    pixels, so its colour is taken from the far end of its cluster instead of
    the cluster median, which would read several shades too close to the base.
    """
    if px is None or not len(px):
        return None
    keep = (px.max(axis=1) < CLIP) & (px.max(axis=1) > 0.02)
    clipped = 1.0 - float(keep.mean())
    use = px[keep] if keep.sum() >= max(12, 0.1 * len(px)) else px
    lin = srgb_to_lin(use)
    out = {"n": int(len(use)), "clipped_frac": round(clipped, 3)}
    if not two_tone or len(lin) < 40:
        out["lin"] = np.median(lin, axis=0)
        out["secondary"] = None
        return out
    lum = lin @ np.array([0.2126, 0.7152, 0.0722])
    c = np.percentile(lum, [25, 75])
    for _ in range(12):
        lab = np.abs(lum[:, None] - c[None, :]).argmin(axis=1)
        new = np.array([lum[lab == k].mean() if (lab == k).any() else c[k] for k in (0, 1)])
        if np.allclose(new, c):
            break
        c = new
    sizes = [(lab == k).sum() for k in (0, 1)]
    major = int(np.argmax(sizes))
    out["lin"] = np.median(lin[lab == major], axis=0)
    minor = 1 - major
    frac = sizes[minor] / float(len(lin))
    out["secondary"] = None
    if sizes[minor] >= 0.03 * len(lin):
        sec = lin[lab == minor]
        if frac < 0.15:
            # thin feature: the 20 % of its pixels farthest from the base
            slum = sec @ np.array([0.2126, 0.7152, 0.0722])
            darker = c[minor] < c[major]
            cut = np.percentile(slum, 20 if darker else 80)
            sel = sec[slum <= cut] if darker else sec[slum >= cut]
            out["secondary"] = np.median(sel if len(sel) else sec, axis=0)
        else:
            out["secondary"] = np.median(sec, axis=0)
        out["secondary_darker"] = bool(c[minor] < c[major])
    out["split"] = round(frac, 3)
    return out


def _lum(lin) -> float:
    return float(np.asarray(lin, dtype=np.float64) @ np.array([0.2126, 0.7152, 0.0722]))


def bright_neutral_level(img: np.ndarray) -> float | None:
    """Luminance (linear) of the brightest neutral, unclipped surfaces in a frame.

    The classic white-patch cue: in an interior the brightest low-saturation
    surfaces are white walls, tiles or counters under the light. Used to
    catch a "white wall" box that actually sits in shadow.
    """
    small = img[::4, ::4, ::-1].reshape(-1, 3).astype(np.float64) / 255.0
    mx, mn = small.max(axis=1), small.min(axis=1)
    sat = (mx - mn) / np.maximum(mx, 1e-6)
    keep = (mx < CLIP) & (sat < 0.12) & (mx > 0.15)
    if keep.sum() < 0.02 * len(small):
        return None
    lum = srgb_to_lin(small[keep]) @ np.array([0.2126, 0.7152, 0.0722])
    return float(np.percentile(lum, 95))


def white_anchor(img: np.ndarray, white_patch) -> dict | None:
    if not white_patch:
        return None
    col = robust_colour(region_pixels(img, white_patch.get("bbox"), shrink=0.1))
    if col is None:
        return None
    lin = np.asarray(col["lin"], dtype=np.float64)
    note = None
    level = bright_neutral_level(img)
    y = _lum(lin)
    if level and y < 0.55 * level:
        # The box is on a wall in shadow (or not on a wall): anchoring to it
        # made a white marble counter "3.4x brighter than white". Keep its hue,
        # take the frame's bright-neutral level for the brightness.
        lin = lin * (level / max(y, 1e-6))
        note = f"white box was in shadow ({y:.3f} vs frame white {level:.3f}); frame level used"
    srgb = lin_to_srgb(lin)
    return {"lin": lin, "srgb": srgb, "clipped_frac": col["clipped_frac"], "note": note,
            # a white patch that is itself overexposed only bounds the anchor
            "ok": col["clipped_frac"] < 0.5 and float(srgb.min()) > 0.25}


def _hsv(lin) -> tuple[float, float, float]:
    r, g, b = [float(c) for c in lin_to_srgb(lin)]
    mx, mn = max(r, g, b), min(r, g, b)
    s = 0.0 if mx <= 0 else (mx - mn) / mx
    if mx == mn:
        h = 0.0
    elif mx == r:
        h = (60 * ((g - b) / (mx - mn)) + 360) % 360
    elif mx == g:
        h = 60 * ((b - r) / (mx - mn)) + 120
    else:
        h = 60 * ((r - g) / (mx - mn)) + 240
    return h, s, mx


def consistent_with_vlm(vlm_hex, display_lin) -> tuple[bool, str]:
    """Does the measured region look like what the model said it saw?

    The model points at "a clean patch of oak veneer"; if the pixels there
    are neutral grey, or ten times brighter than the dark ceiling it named,
    the box is on something else and the measurement must not be used.
    """
    try:
        v = np.asarray(material_spec.hex_to_linear(str(vlm_hex)))
    except (ValueError, TypeError, IndexError):
        return True, "no VLM colour to compare"
    m = np.asarray(display_lin, dtype=np.float64)
    ratio = (_lum(m) + 0.01) / (_lum(v) + 0.01)
    if not 1 / 2.5 <= ratio <= 2.5:
        return False, f"region brightness {ratio:.1f}x what the model saw"
    hv, sv, _ = _hsv(v)
    hm, sm, _ = _hsv(m)
    if sv > 0.18:
        dh = min(abs(hv - hm), 360 - abs(hv - hm))
        if sm < 0.35 * sv or dh > 45:
            return False, f"region colour (h{hm:.0f} s{sm:.2f}) is not the one the model saw (h{hv:.0f} s{sv:.2f})"
    return True, "consistent"


def albedo_from(colour_lin, anchor: dict | None, cls: str, residual=None,
                fallback_anchor_lum: float | None = None) -> tuple[np.ndarray, list[str]]:
    """Reflectance estimate of a measured colour.

    Brightness and hue are corrected separately. Brightness: relative to the
    white wall IN THE SAME FRAME (same exposure). Hue: divided by the camera's
    residual cast, measured over every white patch of the video -- not by the
    one local patch, whose tint is dominated by whatever bounces onto it (a
    wall beside oak cabinets reads warm, and dividing by it turned the white
    marble counter cyan).
    """
    notes = []
    lin = np.asarray(colour_lin, dtype=np.float64)
    res = np.asarray(residual if residual is not None else (1.0, 1.0, 1.0), dtype=np.float64)
    res = res / max(float(res.max()), 1e-6)
    chroma = lin / np.maximum(res, 1e-3)
    y = max(_lum(chroma), 1e-6)
    if anchor and anchor.get("ok"):
        ya = max(_lum(np.asarray(anchor["lin"]) / np.maximum(res, 1e-3)), 1e-6)
        target = y / ya * WHITE_ALBEDO
        notes.append("white-anchored (same frame)")
        if anchor["clipped_frac"] > 0.05:
            notes.append("anchor partly clipped: albedo may read high")
    elif fallback_anchor_lum:
        target = y / fallback_anchor_lum * WHITE_ALBEDO
        notes.append("no white wall in this frame: anchored to the video's median white")
    else:
        target = y
        notes.append("no white anchor: display value used as albedo")
    alb = chroma * (target / y)
    # Near-neutral surfaces ARE neutral. What is left of a tint after removing
    # the camera's cast is the colour of the light that happened to reach the
    # sample (warm bounce off oak, cool spill), which the renderer recreates
    # with its own lights -- baking it into the albedo would count it twice.
    if _hsv(alb)[1] < 0.10:
        alb = np.full(3, _lum(alb))
        notes.append("near-neutral: stored as neutral")
    lo, hi = CLASS_ALBEDO.get(cls, (0.02, 0.92))
    lum = _lum(alb)
    if lum > 1e-6 and not lo <= lum <= hi:
        clamp = min(max(lum, lo), hi)
        alb = alb * (clamp / lum)
        notes.append(f"luminance {lum:.2f} clamped into {cls} range [{lo}, {hi}] "
                     f"(sample and white wall lit differently)")
    return np.clip(alb, 0.0, 1.0), notes


def metal_base(description: str, measured_lin=None) -> tuple[str, float]:
    text = (description or "").lower()
    for word, hexa, rough in METAL_PRIORS:
        if word in text:
            return hexa, rough
    if measured_lin is not None:          # unknown metal: keep its hue, lift it
        lin = np.asarray(measured_lin)
        m = float(lin.max()) or 1.0
        return to_hex(lin / m * 0.6), 0.3
    return "#B4B6B8", 0.32


def emission_colour(measured_lin) -> str:
    """The colour of a glowing part: hue of its pixels at full intensity."""
    lin = np.asarray(measured_lin, dtype=np.float64)
    m = float(lin.max()) or 1.0
    return to_hex(lin / m)


def measure(img: np.ndarray, bbox, white_patch, cls: str, two_tone: bool = False,
            camera: dict | None = None) -> dict:
    """Measured colour of one region, as display hex and as anchored albedo.

    `camera` (from camera_model) supplies the residual cast and the video's
    median white level, used when this frame has no white wall of its own.
    """
    col = robust_colour(region_pixels(img, bbox), two_tone=two_tone)
    if col is None:
        return {"ok": False, "note": "no usable pixels in the region"}
    camera = camera or {}
    anchor = white_anchor(img, white_patch)
    kw = {"residual": camera.get("residual_rgb"),
          "fallback_anchor_lum": camera.get("anchor_lum_lin")}
    alb, notes = albedo_from(col["lin"], anchor, cls, **kw)
    out = {"ok": True, "display_hex": to_hex(col["lin"]), "albedo_hex": to_hex(alb),
           "albedo_lin": [round(float(c), 4) for c in alb], "pixels": col["n"],
           "clipped_frac": col["clipped_frac"], "notes": notes,
           "anchored": bool(anchor and anchor.get("ok")),
           "anchor_srgb": [round(float(c), 3) for c in anchor["srgb"]] if anchor else None}
    if anchor and anchor.get("ok"):
        # exposure-free target for the render-and-compare calibration
        out["ratio_to_white"] = round(_lum(col["lin"]) / max(_lum(anchor["lin"]), 1e-6), 4)
    if col.get("secondary") is not None:
        alb2, _ = albedo_from(col["secondary"], anchor, cls, **kw)
        out["secondary_hex"] = to_hex(alb2)
        out["secondary_display_hex"] = to_hex(col["secondary"])
        out["secondary_fraction"] = col.get("split")
        out["secondary_darker"] = col.get("secondary_darker")
    return out


def combine(measurements: list[dict]) -> dict:
    """Several samples of one material -> one: anchored samples win, median."""
    ok = [m for m in measurements if m.get("ok")]
    if not ok:
        return measurements[0] if measurements else {"ok": False, "note": "no sample"}
    anchored = [m for m in ok if m.get("anchored")] or ok
    alb = np.median(np.array([m["albedo_lin"] for m in anchored]), axis=0)
    best = max(anchored, key=lambda m: m.get("pixels", 0))
    out = dict(best)
    out["albedo_lin"] = [round(float(c), 4) for c in alb]
    out["albedo_hex"] = to_hex(alb)
    out["samples"] = len(ok)
    out["samples_anchored"] = sum(1 for m in ok if m.get("anchored"))
    ratios = [m["ratio_to_white"] for m in ok if m.get("ratio_to_white") is not None]
    out["ratio_to_white"] = round(float(np.median(ratios)), 4) if ratios else None
    secs = [m for m in anchored if m.get("secondary_hex")]
    if secs:
        s = max(secs, key=lambda m: m.get("pixels", 0))
        out.update({k: s[k] for k in ("secondary_hex", "secondary_display_hex",
                                      "secondary_fraction", "secondary_darker") if k in s})
    return out


# --------------------------------------------------------------------------
# colour temperature
# --------------------------------------------------------------------------
def cct_to_rgb(kelvin: float) -> tuple:
    """Planckian locus -> linear Rec.709 RGB, normalised to max 1.

    Kim et al. (2002) cubic fits for the CIE 1931 chromaticity of a black
    body, valid 1667-25000 K; then xyY -> XYZ -> linear sRGB.
    """
    T = float(min(max(kelvin, 1667.0), 25000.0))
    if T <= 4000:
        x = -0.2661239e9 / T ** 3 - 0.2343589e6 / T ** 2 + 0.8776956e3 / T + 0.179910
    else:
        x = -3.0258469e9 / T ** 3 + 2.1070379e6 / T ** 2 + 0.2226347e3 / T + 0.240390
    if T <= 2222:
        y = -1.1063814 * x ** 3 - 1.34811020 * x ** 2 + 2.18555832 * x - 0.20219683
    elif T <= 4000:
        y = -0.9549476 * x ** 3 - 1.37418593 * x ** 2 + 2.09137015 * x - 0.16748867
    else:
        y = 3.0817580 * x ** 3 - 5.87338670 * x ** 2 + 3.75112997 * x - 0.37001483
    X, Y, Z = x / y, 1.0, (1 - x - y) / y
    r = 3.2404542 * X - 1.5371385 * Y - 0.4985314 * Z
    g = -0.9692660 * X + 1.8760108 * Y + 0.0415560 * Z
    b = 0.0556434 * X - 0.2040259 * Y + 1.0572252 * Z
    rgb = [max(0.0, c) for c in (r, g, b)]
    m = max(rgb) or 1.0
    return tuple(round(c / m, 4) for c in rgb)


def camera_white_balance(light_k: float, residual_lin) -> float:
    """The white point the phone used, given the lamp and what white looks like.

    The phone divides by its own white: a white wall under a lamp of T_light
    is recorded as rgb(T_light) / rgb(T_cam). Solve for T_cam so that ratio
    has the measured red/blue balance. Residual ~neutral -> T_cam ~= T_light.
    """
    res = np.asarray(residual_lin, dtype=np.float64)
    if res.min() <= 0:
        return light_k
    target = math.log(res[0] / res[2])
    lamp = np.asarray(cct_to_rgb(light_k))
    best, best_err = light_k, 1e9
    for T in range(1800, 12001, 50):
        cam = np.asarray(cct_to_rgb(T))
        seen = lamp / np.maximum(cam, 1e-6)
        err = abs(math.log(seen[0] / seen[2]) - target)
        if err < best_err:
            best, best_err = T, err
    return float(best)


# --------------------------------------------------------------------------
# VLM passes
# --------------------------------------------------------------------------
LIGHT_PROMPT = """These are full frames from one phone video of a single room ({labels}). Your job is ONLY the lighting, so a renderer can reproduce it.
1. Day or night? Judge from what the windows actually show (sky / daylight vs dark glass with reflections), not from how bright the room is.
2. Every light-emitting fixture: type, how many individual fixtures exist in the room (count each physical fixture once even if several views show it), mounting (pendant on cable / recessed / surface / track / wall / free-standing), housing shape, colour and approximate size, drop below the ceiling for pendants, beam (narrow / medium / wide downward, or diffuse), whether it is ON, and the light colour judged from white surfaces lit by it (warm 2700-3000 K, neutral ~4000 K, cool 5000-6500 K). Describe where the fixtures are in the room (e.g. "in a line along the long axis above the walkway, about 1 m apart").
3. Small emitters on appliances (displays, indicator LEDs): which object, colour.
4. How the light falls: bright pools, darkness of the ceiling, softness of shadows, strong reflections.
Give the view labels that show each claim.
Return a json object {{"time_of_day": "day|night", "time_evidence": "...",
 "fixtures": [{{"id": "snake_case", "type": "pendant|downlight|spot|track|panel|ceiling|strip|wall|lamp|chandelier", "count": 1, "mount": "...", "housing": {{"shape": "cylinder|cone|globe|disc|box|bar", "colour_hex": "#RRGGBB", "material": "metal|plastic|glass|fabric", "diameter_m": 0.1, "height_m": 0.2}}, "drop_m": null, "beam": "narrow|medium|wide|diffuse", "on": true, "cct_k": 4000, "cct_evidence": "...", "arrangement": "...", "views": ["view_0"]}}],
 "practicals": [{{"object": "...", "part": "...", "colour_hex": "#RRGGBB"}}],
 "ambient": "...", "shadows": "soft|medium|hard", "distribution": "...", "notes": "..."}}"""

MATERIAL_PROMPT = """These are full frames from one phone video of a single room ({labels}). Describe each surface material below so a renderer can reproduce it, and point at a clean sample of it.
Materials to describe:
{items}
For EACH material:
- class: paint|plastic|metal|glass|ceramic|tile|stone|wood|fabric|leather|rubber|screen
- finish: mirror|polished|glossy|satin|matte  (judge from highlights and reflections)
- metallic: true|false
- pattern: {{"type": "none|wood_grain|tile_grid|marble_veins|speckle|brushed|perforated|weave", "scale_m": <tile edge / grain spacing / speck size in metres or null>, "secondary_hex": "<grout / vein / speck / grain colour>", "direction": "..."}}
- hex: the colour as it appears where it is evenly lit
- samples: up to TWO samples, each in a DIFFERENT view if possible, preferably views that also show plain white wall: {{"view": "<label>", "bbox": [x0, y0, x1, y1]}} in normalised 0..1000 coordinates of that view, covering a flat, evenly lit area of THIS material ONLY, as large as possible, avoiding highlights, shadows, edges, text, labels and other objects. For tiles and patterned surfaces include several repeats of the pattern.
Also, for every view, one box around a flat evenly lit patch of plain WHITE painted wall or ceiling (null if none).
Return a json object {{"materials": [{{"id": "...", "class": "...", "finish": "...", "metallic": false, "pattern": {{...}}, "hex": "#RRGGBB", "translucent": false, "samples": [{{"view": "view_0", "bbox": [0, 0, 0, 0]}}], "notes": "..."}}],
 "white_patches": {{"view_0": [x0, y0, x1, y1]}}}}"""


def _pick_frames(ids: list[str], limit: int) -> list[str]:
    out = []
    for f in ids:
        if f not in out:
            out.append(f)
    if len(out) <= limit:
        return out
    step = len(out) / float(limit)
    return [out[int(i * step)] for i in range(limit)]


def _fixture_family(fx: dict) -> str:
    return fixture_kind(fx) + ("_on" if fx.get("on", True) else "_off")


def lighting_pass(run: Path, inv: dict, frames: list[dict], refresh: bool = False) -> dict:
    """Two independent readings over disjoint frame sets, merged.

    One reading at temperature 0 said three pendants, a re-run on a slightly
    different frame set said two: the third was hidden behind a pipe in the
    frames it got. Missing a fixture is far more common than counting one
    twice when the prompt asks for physical fixtures, so the merge keeps the
    larger count per fixture family and records any disagreement.
    """
    ceiling = list(dict.fromkeys(inv.get("ceiling_frames", [])
                                 + [fid for l in inv.get("light_fixtures", []) for fid in l["frames"]]))
    windows = inv.get("window_frames", [])
    if len(ceiling) < 4:
        ceiling = list(dict.fromkeys(ceiling + [f["id"] for f in frames[::max(1, len(frames) // 6)]]))
    sets = [_pick_frames(ceiling[0::2] + windows[:1], 6), _pick_frames(ceiling[1::2] + windows[1:2], 6)]
    readings = [_lighting_once(run, frames, ids, f"lighting_{k}", refresh)
                for k, ids in enumerate(sets) if ids]
    merged = dict(readings[0])
    fams: dict = {}
    for r in readings:
        for fx in r["fixtures"]:
            fam = _fixture_family(fx)
            if fam not in fams or fx["count"] > fams[fam]["count"]:
                fams[fam] = dict(fx)
    counts = {fam: [sum(f["count"] for f in r["fixtures"] if _fixture_family(f) == fam)
                    for r in readings] for fam in fams}
    merged["fixtures"] = list(fams.values())
    merged["readings"] = [{"frames": r["frames"], "time_of_day": r["time_of_day"],
                           "fixtures": {(_fixture_family(f)): f["count"] for f in r["fixtures"]}}
                          for r in readings]
    merged["disagreements"] = {fam: c for fam, c in counts.items() if len(set(c)) > 1}
    if len({r["time_of_day"] for r in readings}) > 1:
        merged["disagreements"]["time_of_day"] = [r["time_of_day"] for r in readings]
    merged["frames"] = [f for r in readings for f in r["frames"]]
    merged["vlm"] = [r.get("vlm") for r in readings]
    return merged


def _lighting_once(run: Path, frames: list[dict], ids: list[str], stage: str,
                   refresh: bool) -> dict:
    by_id = {f["id"]: f for f in frames}
    views = [(f"view_{i}", run / "frames" / by_id[fid]["file"]) for i, fid in enumerate(ids)]
    ans = vlm.ask_json(LIGHT_PROMPT.format(labels=", ".join(l for l, _ in views)), views,
                       stage=stage, cache_dir=run / "vlm_calls", refresh=refresh,
                       max_tokens=3000)
    data = ans["data"]
    label_to_frame = {f"view_{i}": fid for i, fid in enumerate(ids)}
    fixtures = []
    for fx in data.get("fixtures") or []:
        try:
            count = max(1, int(fx.get("count") or 1))
        except (TypeError, ValueError):
            count = 1
        try:
            cct = float(fx.get("cct_k") or 4000)
        except (TypeError, ValueError):
            cct = 4000.0
        fixtures.append({**fx, "count": count, "cct_k": min(max(cct, 1800.0), 10000.0),
                         "frames": [label_to_frame.get(v) for v in fx.get("views") or []
                                    if label_to_frame.get(v)]})
    return {"time_of_day": "night" if str(data.get("time_of_day")).lower().startswith("n")
            else "day", "time_evidence": data.get("time_evidence"), "fixtures": fixtures,
            "practicals": data.get("practicals") or [], "ambient": data.get("ambient"),
            "shadows": data.get("shadows"), "distribution": data.get("distribution"),
            "frames": ids, "vlm": ans["meta"]}


def _material_requests(inv: dict) -> list[dict]:
    """Survey materials plus the room's own surfaces, which surveys forget.

    A wall that carries a tile splashback is still a painted wall everywhere
    else, so wall paint is requested unless a PAINT material for the walls was
    already listed -- "something is used by the walls" is not enough.
    """
    mats = [dict(m) for m in inv.get("materials") or []]

    def covers(word, kinds):
        for m in mats:
            text = f"{m['id']} {m.get('description', '')}".lower()
            if word in " ".join(map(str, m.get("used_by") or [])).lower() and \
                    any(k in text for k in kinds):
                return True
        return False

    room = inv.get("room") or {}
    for key, word, desc, kinds in (
            ("wall_paint", "wall", room.get("walls"), ("paint", "plaster", "emulsion")),
            ("ceiling_finish", "ceiling", room.get("ceiling"), ("paint", "ceiling", "plaster")),
            ("floor_finish", "floor", room.get("floor"), ("floor", "terrazzo", "vinyl", "tile",
                                                          "wood", "stone", "carpet"))):
        if desc and not covers(word, kinds):
            mats.append({"id": key, "description": desc, "used_by": [word + "s" if word ==
                                                                      "wall" else word],
                         "frames": []})
    return mats


def material_frames(inv: dict, reqs: list[dict], anchored: set, frames: list[dict],
                    limit: int = 8) -> list[str]:
    """Frames for the materials pass: ones that show the material AND a white wall.

    A colour is only measurable against a white wall in the same frame, and
    the frames a survey names for "oak veneer" are typically the close-ups
    looking up at the wall cabinets -- no wall in sight. So candidates come
    from the material's own frames plus the frames of the objects it is used
    by, and anchored frames win.
    """
    obj_frames = {o["id"]: o.get("frames") or [] for o in inv.get("objects") or []}
    order = [f["id"] for f in frames]
    chosen: list[str] = []
    for m in reqs:
        cands = list(m.get("frames") or [])
        for user in m.get("used_by") or []:
            cands += obj_frames.get(str(user), [])
        if not cands:
            cands = order
        good = [f for f in cands if f in anchored] or cands
        # spread over time: the first, and one from later in the video
        for f in (good[0], good[len(good) // 2]) if good else ():
            if f not in chosen:
                chosen.append(f)
    if len(chosen) > limit:
        chosen = _pick_frames(chosen, limit)
    return sorted(chosen, key=order.index)


def materials_vlm(run: Path, inv: dict, frames: list[dict], refresh: bool = False,
                  anchored: set | None = None) -> dict:
    """Ask for class / finish / pattern and sample regions of every material."""
    by_id = {f["id"]: f for f in frames}
    reqs = _material_requests(inv)
    ids = material_frames(inv, reqs, anchored or set(), frames)
    views = [(f"view_{i}", run / "frames" / by_id[fid]["file"]) for i, fid in enumerate(ids)]
    items = "\n".join(f"- {m['id']}: {m.get('description', '')} (used by "
                      f"{', '.join(map(str, m.get('used_by') or [])) or '?'})" for m in reqs)
    ans = vlm.ask_json(MATERIAL_PROMPT.format(labels=", ".join(l for l, _ in views), items=items),
                       views, stage="materials", cache_dir=run / "vlm_calls", refresh=refresh,
                       max_tokens=6000)
    return {"data": ans["data"], "frames": ids, "requests": reqs, "meta": ans["meta"],
            "label_to_frame": {f"view_{i}": fid for i, fid in enumerate(ids)}}


def collect_white_patches(vlm_out: dict, grounding: dict | None) -> dict:
    lf = vlm_out["label_to_frame"]
    white = {}
    for label, box in (vlm_out["data"].get("white_patches") or {}).items():
        if box and label in lf:
            white[lf[label]] = {"bbox": box}
    for fid, rec in ((grounding or {}).get("frames") or {}).items():
        if fid not in white and rec.get("white_patch"):
            white[fid] = rec["white_patch"]
    return white


def measure_materials(run: Path, frames: list[dict], vlm_out: dict, white: dict,
                      camera: dict) -> dict:
    by_id = {f["id"]: f for f in frames}
    lf = vlm_out["label_to_frame"]
    req_by_id = {m["id"]: m for m in vlm_out["requests"]}
    out = {}
    for m in vlm_out["data"].get("materials") or []:
        mid = str(m.get("id") or "").strip()
        if not mid:
            continue
        cls = material_spec.normalise_class(m.get("class"))
        pattern = m.get("pattern") or {}
        ptype = str(pattern.get("type") or "none").lower()
        two_tone = ptype in ("tile_grid", "marble_veins", "speckle", "wood_grain")
        samples = m.get("samples") or ([m["sample"]] if m.get("sample") else [])
        meas = []
        for s in samples[:2]:
            fid = lf.get((s or {}).get("view"))
            if not fid:
                continue
            img = vf.imread(run / "frames" / by_id[fid]["file"])
            one = measure(img, s.get("bbox"), white.get(fid), cls, two_tone, camera)
            one["frame"] = fid
            if one.get("ok") and cls != "metal" and not m.get("metallic"):
                ok, why = consistent_with_vlm(m.get("hex"),
                                              material_spec.hex_to_linear(one["display_hex"]))
                if not ok:
                    one = {"ok": False, "frame": fid, "rejected": why,
                           "display_hex": one["display_hex"]}
            meas.append(one)
        rec = {"id": mid, "class": cls, "finish": m.get("finish"),
               "metallic": bool(m.get("metallic")), "pattern": pattern,
               "vlm_hex": m.get("hex"), "translucent": bool(m.get("translucent")),
               "used_by": (req_by_id.get(mid) or {}).get("used_by") or [],
               "description": (req_by_id.get(mid) or {}).get("description") or m.get("notes"),
               "measured": combine(meas) if meas else {"ok": False, "note": "no sample region"},
               "samples": meas}
        rec["key"], rec["key_notes"] = material_key(rec)
        out[mid] = rec
    return out


def _hex_lum(hexa) -> float | None:
    try:
        return _lum(material_spec.hex_to_linear(str(hexa)))
    except (ValueError, TypeError, IndexError):
        return None


def material_key(rec: dict) -> tuple[str, list[str]]:
    """Parametric key for a described + measured material (see module doc)."""
    cls, meas = rec["class"], rec.get("measured") or {}
    pattern = dict(rec.get("pattern") or {})
    finish = rec.get("finish")
    notes: list[str] = []
    desc = f"{rec.get('description') or ''} {rec.get('id')} {finish or ''}"
    if cls == "metal" or rec.get("metallic"):
        hexa, rough = metal_base(desc, meas.get("albedo_lin"))
        notes.append("metal: base colour from class prior (pixels show reflections)")
        key = material_spec.from_observation(
            "metal", hexa, finish, pattern=pattern,
            roughness=None if finish in material_spec.FINISH_ROUGHNESS else rough)
        return key, notes
    hexa = meas.get("albedo_hex") if meas.get("ok") else rec.get("vlm_hex")
    notes.append("base colour measured" if meas.get("ok") else "base colour from VLM (no sample)")
    ptype = str(pattern.get("type") or "none").lower()
    if ptype != "none" and meas.get("ok") and meas.get("secondary_hex"):
        # Use the measured second colour only when it agrees with what the
        # model saw about direction (dark grout on white tile): a lighting
        # gradient across the sample also splits into two clusters, lighter
        # on one side, and must not become "white specks on grey".
        vb, vs = _hex_lum(rec.get("vlm_hex")), _hex_lum(pattern.get("secondary_hex"))
        vlm_darker = (vs < vb) if (vb is not None and vs is not None) else True
        if bool(meas.get("secondary_darker")) == vlm_darker:
            thin = (meas.get("secondary_fraction") or 1.0) < 0.10
            ms = _hex_lum(meas["secondary_hex"])
            if thin and vs is not None and ms is not None and (
                    (vlm_darker and vs < ms) or (not vlm_darker and vs > ms)):
                # Grout two pixels wide is never resolved: every pixel on it is
                # mixed with the tile, so the measurement can only under-state
                # the contrast. Keep the model's (more extreme) colour.
                notes.append("thin pattern: measured contrast is a lower bound, "
                             "VLM colour kept")
            else:
                pattern["secondary_hex"] = meas["secondary_hex"]
                notes.append("pattern colour measured")
        else:
            notes.append("measured second cluster contradicts the pattern (lighting "
                         "gradient?): pattern colour from VLM")
    if cls == "ceramic" and ptype.startswith("tile"):
        cls = "tile"
    key = material_spec.from_observation(cls, hexa, finish, pattern=pattern,
                                         translucent=rec.get("translucent", False))
    return key, notes


def camera_model(lighting: dict, white_patches: dict, run: Path, frames: list[dict]) -> dict:
    """Exposure anchor and the phone's white balance, from every white patch."""
    by_id = {f["id"]: f for f in frames}
    srgbs, lins, lums = [], [], []
    for fid, wp in white_patches.items():
        if fid not in by_id:
            continue
        a = white_anchor(vf.imread(run / "frames" / by_id[fid]["file"]), wp)
        if a and a["ok"]:
            srgbs.append(a["srgb"])
            lums.append(_lum(a["lin"]))
            lins.append(a["lin"] / max(float(np.max(a["lin"])), 1e-6))
    on = [f for f in lighting.get("fixtures") or [] if f.get("on", True)]
    light_k = float(np.median([f["cct_k"] for f in on])) if on else (
        6500.0 if lighting.get("time_of_day") == "day" else 4000.0)
    if not lins:
        return {"white_balance_k": light_k, "exposure_anchor_srgb": None,
                "residual_rgb": None, "anchors": 0, "dominant_light_k": light_k,
                "anchor_lum_lin": None}
    # The camera's cast is what DIRECTLY lit white looks like. Dimmer white
    # patches are lit by bounce (a wall beside oak cabinets reads warm), so
    # only the brightest third of the patches is used for the hue.
    order = np.argsort(lums)[::-1]
    top = order[:max(1, len(order) // 3)]
    residual = np.median(np.array(lins)[top], axis=0)
    return {"dominant_light_k": light_k,
            "white_balance_k": camera_white_balance(light_k, residual),
            "residual_rgb": [round(float(c), 4) for c in residual],
            "residual_all_rgb": [round(float(c), 4) for c in np.median(np.array(lins), axis=0)],
            "exposure_anchor_srgb": round(float(np.median(np.array(srgbs).mean(axis=1))), 3),
            "anchor_lum_lin": round(float(np.median(lums)), 4),
            "anchors": len(lins)}


SURFACES_PROMPT = """This is one frame of a phone video of a room. For each surface below that is visible, give ONE bounding box [x0, y0, x1, y1] (normalised 0..1000) around a flat, evenly lit area of it, as large as possible, avoiding objects, highlights, deep shadows, text and edges; null if it is not visible:
- walls: plain painted wall (not tiles)
- floor
- ceiling
- splashback: tiled wall area behind a counter
Return a json object {"walls": [x0, y0, x1, y1] or null, "floor": ..., "ceiling": ..., "splashback": ...}"""


def calibration_targets(run: Path, frame: dict, refresh: bool = False) -> dict:
    """Surface / wall brightness ratios measured in the CALIBRATION frame.

    The render camera imitates this frame, so its ratios are the like-for-like
    targets: a floor ratio measured in a frame looking down at the floor,
    against a wall patch in that frame's shadow, read 0.80 where the
    calibration frame shows 0.5.
    """
    path = run / "frames" / frame["file"]
    ans = vlm.ask_json(SURFACES_PROMPT, [(frame["id"], path)],
                       stage=f"surfaces_{frame['id']}", cache_dir=run / "vlm_calls",
                       refresh=refresh, max_tokens=400)
    img = vf.imread(path)
    lum = {}
    for g in ("walls", "floor", "ceiling", "splashback"):
        box = ans["data"].get(g)
        col = robust_colour(region_pixels(img, box, shrink=0.1)) if box else None
        if col is not None:
            lum[g] = _lum(col["lin"])
    if "walls" not in lum:
        return {}
    out = {g: round(v / max(lum["walls"], 1e-6), 4) for g, v in lum.items()}
    out["_frame"] = frame["id"]
    out["_wall_srgb"] = round(float(lin_to_srgb(lum["walls"])), 3)
    return out


# --------------------------------------------------------------------------
# lights -> Blender parameters (positions from the layout stage)
# --------------------------------------------------------------------------
def fixture_kind(fx: dict) -> str:
    text = f"{fx.get('type', '')} {fx.get('mount', '')}".lower()
    for word in POWER_W:
        if word in text:
            return word
    return "pendant" if "cable" in text else "ceiling"


def light_plan(lighting: dict, placements: list[dict], room_height: float) -> list[dict]:
    """One Blender light (+ housing) per placed fixture instance.

    placements: [{"fixture": id, "x", "y"} ...] from video_layout. Power is a
    prior per fixture type; the scene builder calibrates exposure against the
    video's white wall afterwards, so only the RATIOS between lights matter.
    """
    by_id = {f.get("id"): f for f in lighting.get("fixtures") or []}
    out = []
    for i, pl in enumerate(placements):
        fx = by_id.get(pl.get("fixture")) or {}
        kind = fixture_kind(fx)
        housing = fx.get("housing") or {}
        try:
            dia = float(housing.get("diameter_m") or 0.1)
            height = float(housing.get("height_m") or 0.15)
        except (TypeError, ValueError):
            dia, height = 0.1, 0.15
        dia, height = min(max(dia, 0.03), 1.2), min(max(height, 0.02), 1.0)
        try:
            drop = float(fx.get("drop_m")) if fx.get("drop_m") not in (None, "") else (
                0.8 if kind == "pendant" else 0.0)
        except (TypeError, ValueError):
            drop = 0.8 if kind == "pendant" else 0.0
        drop = min(max(drop, 0.0), max(0.0, room_height - 1.9))
        bottom = room_height - drop - (height if kind == "pendant" else 0.0)
        beam = str(fx.get("beam") or "medium").lower()
        if kind in ("panel", "strip"):
            ltype = "AREA"
        elif beam in BEAM_DEG and kind not in ("globe", "chandelier", "lamp"):
            ltype = "SPOT"
        else:
            ltype = "POINT"
        out.append({
            "id": pl.get("id") or f"{fx.get('id', 'light')}_{i + 1}",
            "fixture": fx.get("id"), "kind": kind, "type": ltype,
            "on": bool(fx.get("on", True)),
            "location": [round(float(pl["x"]), 3), round(float(pl["y"]), 3),
                         round(bottom - 0.005, 3)],
            "energy_w": POWER_W.get(kind, 40.0) if fx.get("on", True) else 0.0,
            "temperature_k": float(fx.get("cct_k") or 4000.0),
            "color_rgb": cct_to_rgb(float(fx.get("cct_k") or 4000.0)),
            "spot_size_deg": BEAM_DEG.get(beam, 75.0),
            "spot_blend": 0.35,
            "shadow_soft_size": round(dia * 0.4, 3),
            "area_size": [round(dia, 3), round(max(dia, 0.05), 3)],
            "housing": {"shape": housing.get("shape") or "cylinder", "diameter": dia,
                        "height": height, "colour_hex": housing.get("colour_hex") or "#F2F2F2",
                        "material": housing.get("material") or "metal",
                        "top_z": room_height - drop if kind == "pendant" else room_height,
                        "ceiling_z": room_height, "cable": kind == "pendant" and drop > 0},
            "emitter_key": f"light:#FFFFFF:0.5:strength={8.0 if kind == 'pendant' else 6.0:g}",
        })
    return out


def run_appearance(run: Path, inv: dict, frames: list[dict], grounding: dict | None = None,
                   refresh: bool = False) -> dict:
    lighting = lighting_pass(run, inv, frames, refresh=refresh)
    anchored = {fid for fid, rec in ((grounding or {}).get("frames") or {}).items()
                if rec.get("white_patch")}
    mv = materials_vlm(run, inv, frames, refresh=refresh, anchored=anchored)
    white = collect_white_patches(mv, grounding)
    # the camera model first: every colour measurement needs its residual cast
    camera = camera_model(lighting, white, run, frames)
    materials = measure_materials(run, frames, mv, white, camera)
    out = {"lighting": lighting, "materials": materials, "camera": camera,
           "material_frames": mv["frames"], "white_patches": white,
           "vlm": {"lighting": lighting.pop("vlm"), "materials": mv["meta"]}}
    (run / "appearance.json").write_text(json.dumps(out, ensure_ascii=False, indent=2,
                                                    default=float), encoding="utf-8")
    return out
