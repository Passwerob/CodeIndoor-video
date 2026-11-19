"""Fridge graphics cut out of the video: the blue China energy label on the
right door (t = 88 s) and the printed notice in a clear sleeve on the left door
(t = 266 s)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import texlib as T  # noqa: E402

out = Path(sys.argv[1])
T.save(T.rectify(88.0, [(247.5, 652.5), (325, 654), (336, 861), (261, 855)], 300, 800), out, "energy_label.png")
T.save(T.rectify(266.0, [(309.2, 299.2), (430, 290.4), (425, 500), (305, 490.8)], 620, 870), out, "notice.png")
print("ok")
