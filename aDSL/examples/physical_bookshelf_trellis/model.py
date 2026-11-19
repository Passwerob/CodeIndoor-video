"""The relational bookshelf with a TRELLIS chess piece mounted on book 19.

The chess piece is an independent rigid component.  Its attachment target is
the book, not the shelf: shelf edits propagate shelf -> book -> chess piece.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "examples" / "physical_bookshelf"))

import model as bookshelf
from adsl.core import ExternalMesh, scale_shape, shape_aabb


TRELLIS_MESH = ROOT / "research" / "trellis_small_components_20261017" / "models" / "chess_queen.glb"
QUEEN_BOUNDS = ((-0.2325506, -0.2332702, -0.5000608), (0.2319439, 0.2322367, 0.5000795))
HERE = Path(__file__).resolve().parent


def define():
    assembly, books = bookshelf.define()
    target = next(book for book in books if book["id"] == 19)
    target_id = target["part_id"]

    # The source asset is normalized once for this component instance. Future
    # shelf/book edits translate it, but do not resize its local geometry.
    queen = scale_shape(
        ExternalMesh(TRELLIS_MESH, QUEEN_BOUNDS, label="TRELLIS_ChessQueen"),
        0.15,
    )
    ornament_id = "mounted_components/book_19_chess_queen"
    assembly.attached(
        ornament_id,
        queen,
        to=target_id,
        anchor="bottom",
        target_anchor="top",
        offset=(0, -0.06, 0.004),
    )
    return assembly, books, target_id, ornament_id


def build(extra_heights=(0, 0, 0, 0)):
    if len(extra_heights) != 4:
        raise ValueError("Provide four bay height changes")
    assembly, books, target_id, ornament_id = define()
    base = (0.368, 0.368, 0.368, 0.4)
    overrides = {
        f"bay_{index + 1}_clearance": base[index] + float(extra_heights[index])
        for index in range(4)
    }
    result = assembly.compile(overrides)
    for book in books:
        book["bounds"] = [v.tolist() for v in shape_aabb(result.parts[book["part_id"]])]
    return result, books, target_id, ornament_id


result, book_manifest, mounted_book_id, mounted_component_id = build()
scene = result.scene


if __name__ == "__main__":
    queen_bounds = [v.tolist() for v in shape_aabb(result.parts[mounted_component_id])]
    print(json.dumps({
        "mounted_on": mounted_book_id,
        "component": mounted_component_id,
        "component_bounds": queen_bounds,
        "height": result.values["overall_top"],
        "checks": len(result.report["checks"]),
    }))
