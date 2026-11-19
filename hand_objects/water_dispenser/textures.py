"""Water dispenser graphics. Positions are fractions read off the head close-ups
(closeups/water_dispenser/panel_zoom_front.jpg, closeups/head_t226.0.jpg):
GE roundel top-left, 通用净水 ("GE water purification") / 400-788-7171 top-right, a round blue display in
the middle with two touch keys either side (icons: cup, Y-mark | drop 常温 (room temperature),
gear 设置 (settings); the left two labels are unreadable in the video and drawn as
indistinct marks, not invented words)."""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image, ImageDraw  # noqa: E402
import texlib as T  # noqa: E402

out = Path(sys.argv[1])
HEAD = (0x3A, 0x3D, 0x42)
ICON = (222, 224, 226)
DIM = (150, 152, 156)

# ---- head front decal --------------------
W, H = 1140, 1000                    # the decal is (head width - 5 cm) x (head height - 3 cm)
im = Image.new("RGB", (W, H), HEAD)
d = ImageDraw.Draw(im)
def P(u, v):
    return u * W, v * H
# GE roundel
cx, cy = P(0.17, 0.26); r = 78
d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(196, 198, 201), width=9)
for k in range(4):                                     # the four flourishes of the roundel
    a = math.radians(45 + 90 * k)
    x0, y0 = cx + 0.72 * r * math.cos(a), cy + 0.72 * r * math.sin(a)
    d.arc([x0 - 16, y0 - 16, x0 + 16, y0 + 16], 0, 360, fill=(196, 198, 201), width=5)
d.text((cx, cy), "GE", font=T.font(T.SANS_B, 72), fill=(196, 198, 201), anchor="mm")
# brand line + hotline
d.text(P(0.78, 0.19), "通用净水", font=T.font(T.CJK, 64), fill=(226, 226, 226), anchor="mm")
d.text(P(0.78, 0.265), "400-788-7171", font=T.font(T.SANS, 34), fill=(176, 178, 180), anchor="mm")
# display bezel (the glowing disc itself is a separate emissive decal)
cx, cy = P(0.525, 0.515); r = 0.026 / 0.24 * W * 1.08
d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(24, 25, 28), outline=(92, 94, 98), width=4)
# touch keys
def label_marks(u, v):
    x, y = P(u, v)
    d.rounded_rectangle([x - 34, y - 9, x - 4, y + 9], 4, fill=DIM)
    d.rounded_rectangle([x + 4, y - 9, x + 34, y + 9], 4, fill=DIM)
ky, ly = 0.515, 0.61
# 1: cup with steam
x, y = P(0.15, ky)
d.line([(x - 22, y - 14), (x - 16, y + 24), (x + 16, y + 24), (x + 22, y - 14)], fill=ICON, width=7)
d.arc([x + 12, y - 8, x + 36, y + 14], -90, 90, fill=ICON, width=6)
for dx in (-8, 6):
    d.arc([x + dx - 6, y - 44, x + dx + 6, y - 22], 90, 270, fill=ICON, width=4)
label_marks(0.15, ly)
# 2: Y-mark
x, y = P(0.29, ky)
d.line([(x - 22, y - 24), (x, y)], fill=ICON, width=7)
d.line([(x + 22, y - 24), (x, y)], fill=ICON, width=7)
d.line([(x, y), (x, y + 26)], fill=ICON, width=7)
label_marks(0.29, ly)
# 3: drop, 常温 (room temperature)
x, y = P(0.745, ky)
d.polygon([(x, y - 30), (x - 19, y + 4), (x + 19, y + 4)], fill=ICON)
d.ellipse([x - 20, y - 12, x + 20, y + 26], fill=ICON)
d.text(P(0.745, ly), "常温", font=T.font(T.CJK, 34), fill=(200, 202, 204), anchor="mm")
# 4: gear, 设置 (settings)
x, y = P(0.875, ky)
for k in range(8):
    a = math.radians(45 * k)
    d.line([(x + 18 * math.cos(a), y + 18 * math.sin(a)), (x + 30 * math.cos(a), y + 30 * math.sin(a))],
           fill=ICON, width=9)
d.ellipse([x - 22, y - 22, x + 22, y + 22], outline=ICON, width=8)
d.ellipse([x - 7, y - 7, x + 7, y + 7], fill=ICON)
d.text(P(0.875, ly), "设置", font=T.font(T.CJK, 34), fill=(200, 202, 204), anchor="mm")
T.save(im, out, "head_front.png")

# ---- round display: blue digits on black, emissive --------------------------
S = 512
im = Image.new("RGB", (S, S), (6, 6, 10))
d = ImageDraw.Draw(im)
d.text((S / 2, S * 0.44), "89", font=T.font(T.SANS_B, 230), fill=(92, 80, 255), anchor="mm")
d.text((S * 0.80, S * 0.30), "°C", font=T.font(T.SANS_B, 52), fill=(92, 80, 255), anchor="mm")
d.rounded_rectangle([S * 0.30, S * 0.70, S * 0.70, S * 0.76], 8, fill=(70, 60, 220))
T.save(im, out, "display.png")

# ---- CAUTION sticker (red/white, two pictograms) ----------------------------
w, h = 640, 410
im = Image.new("RGB", (w, h), (246, 246, 244))
d = ImageDraw.Draw(im)
RED = (200, 34, 40)
d.rectangle([0, 0, w - 1, h - 1], outline=RED, width=14)
d.rectangle([0, 0, w, 78], fill=RED)
d.text((w / 2, 40), "CAUTION", font=T.font(T.SANS_B, 52), fill=(255, 255, 255), anchor="mm")
d.rectangle([0, h - 64, w, h], fill=RED)
for x0 in (90, 400):
    d.rectangle([x0 - 10, h - 46, x0 + 150, h - 22], fill=(240, 200, 200))
d.line([(w / 2, 78), (w / 2, h - 64)], fill=RED, width=8)
# pictograms: a hand under a spout | a child reaching
d.line([(120, 120), (220, 120), (220, 150)], fill=RED, width=12)
for k in range(3):
    d.line([(205 + 15 * k, 165), (200 + 15 * k, 200)], fill=RED, width=6)
d.rounded_rectangle([110, 230, 260, 300], 30, outline=RED, width=10)
d.ellipse([440, 120, 500, 180], outline=RED, width=10)
d.line([(470, 180), (470, 270), (430, 320)], fill=RED, width=12)
d.line([(470, 210), (540, 170)], fill=RED, width=10)
d.line([(470, 270), (510, 320)], fill=RED, width=12)
T.save(im, out, "caution.png")

# ---- drip tray grille --------------------------------------------------------
w, h = 1160, 300
im = Image.new("RGB", (w, h), (34, 35, 37))
d = ImageDraw.Draw(im)
for x in range(70, w - 60, 36):
    d.rounded_rectangle([x, 50, x + 16, h - 50], 8, fill=(10, 10, 11))
d.ellipse([w / 2 - 34, h / 2 - 34, w / 2 + 34, h / 2 + 34], fill=(8, 8, 9), outline=(60, 60, 62), width=6)
T.save(im, out, "tray.png")

# ---- small square badge on the lower front -----------------------------------
im = Image.new("RGB", (128, 128), (150, 152, 154))
d = ImageDraw.Draw(im)
d.rounded_rectangle([22, 22, 106, 106], 10, fill=(70, 72, 75))
d.text((64, 66), "J", font=T.font(T.SANS_B, 60), fill=(210, 210, 210), anchor="mm")
T.save(im, out, "badge.png")

# ---- perforated vent, lower east side --------------------------------------
im = Image.new("RGB", (700, 450), (236, 236, 234))
d = ImageDraw.Draw(im)
for r in range(6):
    for c in range(12):
        x, y = 40 + c * 54, 40 + r * 64
        d.rounded_rectangle([x, y, x + 34, y + 40], 10, fill=(150, 152, 154))
T.save(im, out, "vent.png")

# ---- recessed finger grip on the east service panel (F46 close-up) ---------
im = Image.new("RGB", (600, 160), (236, 236, 234))
d = ImageDraw.Draw(im)
d.rounded_rectangle([10, 20, 590, 140], 60, fill=(120, 121, 122))
d.rounded_rectangle([24, 30, 576, 110], 50, fill=(88, 89, 90))
d.rounded_rectangle([30, 108, 570, 132], 12, fill=(250, 250, 250))          # lit lower lip
T.save(im, out, "side_grip.png")

# ---- the stickers on the east side panel, cut out of the video (t = 230 s) ----
side = T.rectify(230.0, [(170, 405), (330, 388), (364, 742), (238, 838)], 620, 880,
                 frames=[230.0])
side = T.white_balance(side, side.crop((5, 5, 40, 60)).resize((1, 1)).getpixel((0, 0)))
T.save(side, out, "side_labels.png")
print("ok")
