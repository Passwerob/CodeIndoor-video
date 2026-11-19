"""Texture helpers for hand-written objects (plain Python + PIL + OpenCV).

rectify(): a flat graphic seen in the video (a sticker, a label, a control
panel) cut out of the frame at time t through the four image corners of a
rectangle on that surface, so it can be put back on the model as a decal.
draw_*(): crisp graphics drawn from what the close-ups show, for things the
544x960 video cannot resolve (key icons, small print)."""
from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "adsl_adapter"))
from codeindoor_env import setting  # noqa: E402  (env -> runtime/.env)

VIDEO = setting("SOURCE_VIDEO")  # the pantry footage rectify() cuts decals from
CJK = setting("FONT_CJK", "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc")
SANS = setting("FONT_SANS", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
SANS_B = setting("FONT_SANS_BOLD", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
_cap = None


def frame(t: float) -> np.ndarray:
    global _cap
    if _cap is None:
        if not VIDEO:
            sys.exit("SOURCE_VIDEO is not set (shell env or runtime/.env, see .env.example)")
        _cap = cv2.VideoCapture(VIDEO)
    _cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
    ok, bgr = _cap.read()
    if not ok:
        raise RuntimeError(f"no frame at {t}s")
    return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)


def rectify(t, quad, out_w, out_h, frames=None, sharpen=0.6):
    """quad = [TL, TR, BR, BL] in frame pixels (x, y). Averages the warped
    patches of nearby frames (frames = list of times) to cut noise."""
    times = frames or [t]
    dst = np.float32([[0, 0], [out_w - 1, 0], [out_w - 1, out_h - 1], [0, out_h - 1]])
    H = cv2.getPerspectiveTransform(np.float32(quad), dst)
    acc = np.zeros((out_h, out_w, 3), np.float32)
    for tt in times:
        acc += cv2.warpPerspective(frame(tt), H, (out_w, out_h), flags=cv2.INTER_CUBIC,
                                   borderMode=cv2.BORDER_REPLICATE).astype(np.float32)
    im = Image.fromarray(np.clip(acc / len(times), 0, 255).astype(np.uint8))
    if sharpen:
        im = im.filter(ImageFilter.UnsharpMask(radius=2, percent=int(sharpen * 150), threshold=2))
    return im


def font(path, px):
    return ImageFont.truetype(path, int(px))


def white_balance(im, ref_rgb, target=(236, 236, 234)):
    """Scale channels so a patch known to be white/grey reads `target`."""
    a = np.asarray(im).astype(np.float32)
    g = np.float32(target) / np.maximum(np.float32(ref_rgb), 1.0)
    return Image.fromarray(np.clip(a * g, 0, 255).astype(np.uint8))


def save(im, out: Path, name: str):
    out.mkdir(parents=True, exist_ok=True)
    im.save(out / name)
    return out / name
