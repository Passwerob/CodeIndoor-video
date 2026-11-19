"""白猫 ("White Cat" brand) dish-soap jug: front label cut out of the video (t = 120 s)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import texlib as T  # noqa: E402

out = Path(sys.argv[1])
T.save(T.rectify(120.0, [(150.5, 548.75), (195.5, 549.5), (195.5, 600), (151.25, 601.25)], 360, 420), out, "label.png")
print("ok")
