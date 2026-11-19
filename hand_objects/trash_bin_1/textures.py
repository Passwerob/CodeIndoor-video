"""Trash bin graphics (t = 0-1 s, 198-212 s): white 'PUSH' over a right-pointing
open arrow on the swing flap; a white roundel with a figure dropping litter
into a bin on the body front (its ring text is unreadable: drawn as marks)."""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image, ImageDraw  # noqa: E402
import texlib as T  # noqa: E402

out = Path(sys.argv[1])
W, H = 880, 420                                   # 0.22 x 0.105 m
im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
d = ImageDraw.Draw(im)
d.text((W / 2, 130), "PUSH", font=T.font(T.SANS_B, 150), fill=(236, 236, 236, 255), anchor="mm")
d.line([(250, 285), (600, 285)], fill=(236, 236, 236, 255), width=14)      # open arrow: shaft + head
d.line([(250, 330), (600, 330)], fill=(236, 236, 236, 255), width=14)
d.polygon([(600, 250), (690, 308), (600, 366)], outline=(236, 236, 236, 255), width=14)
T.save(im, out, "push.png")

S = 600
im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(im)
c = S / 2
d.ellipse([20, 20, S - 20, S - 20], outline=(226, 226, 226, 255), width=16)
d.ellipse([110, 110, S - 110, S - 110], outline=(226, 226, 226, 255), width=8)
for k in range(10):                                # ring text marks
    a = math.radians(200 + k * 14)
    x, y = c + 205 * math.cos(a), c + 205 * math.sin(a)
    d.ellipse([x - 14, y - 14, x + 14, y + 14], fill=(226, 226, 226, 255))
# figure dropping litter into a bin
d.ellipse([330, 160, 380, 210], fill=(226, 226, 226, 255))
d.line([(350, 210), (335, 330)], fill=(226, 226, 226, 255), width=22)
d.line([(335, 330), (300, 420)], fill=(226, 226, 226, 255), width=18)
d.line([(335, 330), (380, 420)], fill=(226, 226, 226, 255), width=18)
d.line([(345, 240), (270, 260)], fill=(226, 226, 226, 255), width=16)
d.rectangle([180, 300, 270, 420], outline=(226, 226, 226, 255), width=12)
d.ellipse([235, 250, 255, 270], fill=(226, 226, 226, 255))
T.save(im, out, "recycle.png")
print("ok")
