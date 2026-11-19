"""Code-generated bookshelf with the locally generated TRELLIS component."""
import json
import importlib.util
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BOOKSHELF_SOURCE = ROOT / "examples" / "physical_bookshelf" / "model.py"
sys.path.insert(0, str(BOOKSHELF_SOURCE.parent))
_spec = importlib.util.spec_from_file_location("physical_bookshelf_base", BOOKSHELF_SOURCE)
if _spec is None or _spec.loader is None:
    raise ImportError(f"Cannot load bookshelf source: {BOOKSHELF_SOURCE}")
bookshelf = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = bookshelf
_spec.loader.exec_module(bookshelf)
from adsl.core import ExternalMesh, scale_shape, shape_aabb


# Generated locally by TRELLIS (not in git). RESEARCH_ROOT defaults to <repo>/research.
TRELLIS_MESH = (Path(os.environ.get("RESEARCH_ROOT") or ROOT.parent / "research")
                / "trellis_generated_windows_20261017" / "trellis_component_raw.glb")
# Bounds are measured from Blender's import of the raw GLB, in its local frame.
TRELLIS_BOUNDS = (
    (-0.21128884, -0.49775735, -0.44165894),
    (0.21295437, 0.49955997, 0.47023201),
)


def define():
    assembly, books = bookshelf.define()
    target = next(book for book in books if book["id"] == 19)
    component = scale_shape(
        ExternalMesh(TRELLIS_MESH, TRELLIS_BOUNDS, label="TRELLIS_LocalGeneratedComponent"),
        0.15,
    )
    component_id = "mounted_components/book_19_trellis_local"
    assembly.attached(
        component_id,
        component,
        to=target["part_id"],
        anchor="bottom",
        target_anchor="top",
        offset=(0, -0.06, 0.004),
    )
    return assembly, books, target["part_id"], component_id


def build(extra_heights=(0, 0, 0, 0)):
    if len(extra_heights) != 4:
        raise ValueError("Provide four bay height changes")
    assembly, books, target_id, component_id = define()
    base = (0.368, 0.368, 0.368, 0.4)
    overrides = {
        f"bay_{index + 1}_clearance": base[index] + float(extra_heights[index])
        for index in range(4)
    }
    result = assembly.compile(overrides)
    for book in books:
        book["bounds"] = [v.tolist() for v in shape_aabb(result.parts[book["part_id"]])]
    return result, books, target_id, component_id


result, book_manifest, mounted_book_id, mounted_component_id = build()
scene = result.scene

if __name__ == "__main__":
    low, high = shape_aabb(result.parts[mounted_component_id])
    print(json.dumps({
        "mounted_on": mounted_book_id,
        "component": mounted_component_id,
        "component_bounds": [low.tolist(), high.tolist()],
        "height": result.values["overall_top"],
        "checks": len(result.report["checks"]),
    }))
