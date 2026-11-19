"""Microwave (Galanz) graphics, from closeups/microwave*/ (t = 128-143 s).
Control strip: red LED '6 10' at the top, a row '10分 1分 10秒' (10 min, 1 min, 10 s), rows of small
white key legends (unreadable at 544x960: drawn as indistinct marks), two round
keys at the bottom. Top: the energy + warning label pair (cut from t = 140.5 s);
slot vents on the top and the left side."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image, ImageDraw  # noqa: E402
import texlib as T  # noqa: E402

out = Path(sys.argv[1])
BG = (12, 12, 13)
INK = (205, 205, 208)
DIM = (120, 120, 124)

# ---- control strip 0.125 x 0.235 m -> 500 x 940 ----------------------------
W, H = 500, 940
im = Image.new("RGB", (W, H), BG)
d = ImageDraw.Draw(im)
d.rounded_rectangle([40, 60, W - 40, 200], 14, fill=(6, 4, 5))          # LED window
d.text((W / 2, 300), "10分   1分   10秒", font=T.font(T.CJK, 34), fill=INK, anchor="mm")
for r in range(6):                                                    # key legends, two columns
    y = 380 + r * 72
    for x in (130, 360):
        d.rounded_rectangle([x - 60, y - 9, x + 60, y + 9], 6, fill=DIM if (r + x) % 3 else INK)
        d.ellipse([x - 92, y - 7, x - 78, y + 7], fill=INK)
for x, sym in ((150, "stop"), (350, "start")):
    d.ellipse([x - 44, 830 - 44, x + 44, 830 + 44], outline=INK, width=5)
    if sym == "stop":
        d.rectangle([x - 14, 830 - 14, x + 14, 830 + 14], outline=INK, width=4)
    else:
        d.polygon([(x - 12, 830 - 18), (x - 12, 830 + 18), (x + 18, 830)], outline=INK, width=4)
T.save(im, out, "panel.png")

# ---- LED digits (emissive) 0.09 x 0.03 m ------------------------------------
im = Image.new("RGB", (600, 200), (4, 2, 3))
d = ImageDraw.Draw(im)
f = T.font(T.SANS_B, 150)
d.text((150, 100), "6", font=f, fill=(255, 40, 120), anchor="mm")
d.text((300, 100), ":", font=f, fill=(90, 14, 40), anchor="mm")
d.text((440, 100), "10", font=f, fill=(255, 40, 120), anchor="mm")
T.save(im, out, "led.png")

# ---- vents (alpha: slots opaque, rest transparent) -------------------------
def slots(w, h, rects, name):
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    for r in rects:
        d.rounded_rectangle(r, 6, fill=(4, 4, 5, 255))
    T.save(im, out, name)
# top: two short slot pairs at the back, two blocks of long slots behind the
# middle, one small block at front-left (as in t = 136 s); u along the width
rects = []
for bx, by, n, L in ((60, 40, 2, 150), (250, 40, 2, 150), (40, 170, 7, 230), (330, 170, 7, 230), (90, 470, 3, 160)):
    for k in range(n):
        rects.append([bx, by + k * 34, bx + L, by + k * 34 + 14])
slots(640, 600, rects, "top_vent.png")
rects = []
for c in range(4):
    for r in range(7):
        x, y = 30 + c * 70, 30 + r * 52
        rects.append([x, y, x + 46, y + 14])
slots(320, 400, rects, "side_vent.png")

# ---- top label pair, cut from the video ------------------------------------
lab = T.rectify(140.5, [(24, 412.5), (250, 375), (295, 482.5), (37.5, 536)], 900, 390)
T.save(lab, out, "top_label.png")
print("ok")
