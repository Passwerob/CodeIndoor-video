"""Code-generated shelf with a rigid TRELLIS visual component.

The shelf height is editable; the imported chess piece follows its support
surface without being scaled.  The mesh is visual detail, while the relation
solver uses its explicit local bounds proxy.
"""
from pathlib import Path
import os

from adsl.core import ExternalMesh, RelationAssembly


ROOT = Path(__file__).resolve().parents[2]
TRELLIS_MESH = ROOT / "research" / "trellis_small_components_20261017" / "models" / "chess_queen.glb"
QUEEN_BOUNDS = ((-0.2325506, -0.2332702, -0.5000608), (0.2319439, 0.2322367, 0.5000795))


def define():
    assembly = RelationAssembly("CodeShelf_With_TRELLIS_Decoration")
    shelf_top = assembly.parameter("shelf_top", 0.8)
    assembly.box("shelf", (1.2, 0.55, 0.06), (0, 0, shelf_top - 0.03))
    assembly.span_box("frame/left_side", bottom=0, top=assembly.bound("shelf"), width=0.06, depth=0.55, x=-0.63)
    assembly.span_box("frame/right_side", bottom=0, top=assembly.bound("shelf"), width=0.06, depth=0.55, x=0.63)
    queen = ExternalMesh(TRELLIS_MESH, QUEEN_BOUNDS, label="TRELLIS_ChessQueen")
    assembly.supported("decorations/queen", queen, on="shelf")
    assembly.within("decorations/queen", (-0.58, -0.26, shelf_top), (0.58, 0.26, shelf_top + 1.05))
    return assembly


def build(shelf_top=0.8):
    return define().compile({"shelf_top": shelf_top})


result = build(float(os.environ.get("ADSL_DEMO_SHELF_TOP", "0.8")))
scene = result.scene


if __name__ == "__main__":
    print({"top": result.values["shelf_top"], "checks": len(result.report["checks"])})
