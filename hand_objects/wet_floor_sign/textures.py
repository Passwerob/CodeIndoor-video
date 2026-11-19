"""The floor label pair (小心台阶 WATCH YOUR STEP | 小心地滑 CAUTION WET FLOOR), cut
out of the video at t = 63 s, where it lies flat beside the aluminium step nosing."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import texlib as T  # noqa: E402

out = Path(sys.argv[1])
T.save(T.rectify(63.0, [(280, 820), (500, 605), (544, 652.5), (325, 872.5)], 1200, 270), out, "label.png")
print("ok")
