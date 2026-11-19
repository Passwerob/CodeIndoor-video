"""Spider-plant leaf: green edges, a cream centre stripe (t = 150-160 s)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image, ImageDraw, ImageFilter  # noqa: E402
import texlib as T  # noqa: E402

out = Path(sys.argv[1])
im = Image.new("RGB", (128, 512), (58, 104, 40))
d = ImageDraw.Draw(im)
d.rectangle([44, 0, 84, 512], fill=(214, 226, 168))
d.rectangle([0, 0, 14, 512], fill=(44, 84, 32))
d.rectangle([114, 0, 128, 512], fill=(44, 84, 32))
T.save(im.filter(ImageFilter.GaussianBlur(3)), out, "leaf.png")
print("ok")
