"""FineScene cube-heavy export adapter; the original aDSL worker is unchanged."""
import importlib.util
from pathlib import Path
import sys
import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent))
import finescene_env as fenv
from mathutils import Matrix, Vector

SOURCE = fenv.ADSL_ROOT / "scripts/blender_worker.py"
spec = importlib.util.spec_from_file_location("finescene_original_worker", SOURCE)
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)
original_cube = worker.geometry._make_cube


def make_cube(prim):
    p = prim["params"]
    center, size = p["center"], p["scale"]
    transform = Matrix(prim["xform"]) if prim.get("xform") is not None else Matrix.Identity(4)
    signs = [(-1,-1,-1),(-1,-1,1),(-1,1,-1),(-1,1,1),(1,-1,-1),(1,-1,1),(1,1,-1),(1,1,1)]
    vertices = [transform @ Vector(tuple(center[a]+v[a]*size[a]/2 for a in range(3))) for v in signs]
    faces = [(0,2,6,4),(1,5,7,3),(0,4,5,1),(2,3,7,6),(0,1,3,2),(4,6,7,5)]
    mesh = bpy.data.meshes.new("FineSceneCube")
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    obj = bpy.data.objects.new("Cube",mesh)
    bpy.context.scene.collection.objects.link(obj)
    mesh.materials.append(worker.geometry._make_material("mat_cube",prim["color"],prim.get("alpha")))
    # Keep thin book covers exact: beveling their tilted contact corner would
    # lift the book by ~0.04 mm and invalidate its explicit support relation.
    if min(size) > .012:
        bevel = obj.modifiers.new("FineScene edge finish", "BEVEL")
        bevel.width = min(.003, min(size)*.08)
        bevel.segments = 2
    return obj


def self_test():
    from adsl.core import Cube, rotate_shape, translate_shape
    shapes = [Cube((.04,.22,.3),center=(.1,-.2,.15),color=(.19,.035,.025))]
    shapes.append(rotate_shape(shapes[0],axis="+y",angle=-11,center=(0,0,0)))
    shapes.append(translate_shape(rotate_shape(shapes[1],axis="+z",angle=-90,center=(0,0,0)),(2.56,.65,1.2)))
    for shape in shapes:
        prim = list(shape.iter_primitives())[0]
        old,new = original_cube(prim),make_cube(prim)
        bpy.context.view_layer.update()
        a = [old.matrix_world @ vertex.co for vertex in old.data.vertices]
        b = [new.matrix_world @ vertex.co for vertex in new.data.vertices]
        assert max(min((x-y).length for y in b) for x in a) < 1e-6, (a,b)
        assert old.data.materials[0] == new.data.materials[0]
    print("FineScene fast cube parity: 3 transforms passed")


if __name__ == "__main__":
    argv = sys.argv[sys.argv.index("--")+1:] if "--" in sys.argv else []
    if argv == ["--self-test"]:
        self_test()
    else:
        worker.geometry._make_cube = make_cube
        raise SystemExit(worker.main(argv))
