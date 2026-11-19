"""Meaningful smoke checks for the native Blender execution backend (no API calls)."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from local_3d import ROOT, find_blender
from adsl.agents.utils.execution import AssetExecutionError, execute_asset_source


SOURCE = '''import adsl
from pathlib import Path
from adsl.core import Asset, Cube, Cylinder, ExternalMesh, Sphere, translate_shape
scene = adsl.Asset('Smoke')
group = Asset('Group')
group.attach_part('translated', translate_shape(Cube((2, 1, 0.5), center=(1, 2, 3)), (3, -1, 2)))
scene.attach_part('nested', group)
scene.attach_part('diagonal', Cylinder(0.2, p0=(0, 0, 0), p1=(1, 2, 3)))
scene.attach_part('ball', Sphere(0.3, center=(-2, 0, 0)))
mesh_path = Path(r'__QUEEN_GLB__')
mesh = ExternalMesh(mesh_path, ((-0.233, -0.234, -0.501), (0.232, 0.233, 0.501)), label='DecorativeQueen')
scene.attach_part('decorative_queen', translate_shape(mesh, (4, 0, 0)))
arm = Cube((0.5, 0.5, 0.5), center=(2, 0, 0))
scene.revolute(arm, axis='+z', origin=(1, 0, 0), initial=1.5707963267948966, joint_name='hinge')
'''

# TRELLIS sample mesh (not in git); override with ADSL_TEST_MESH.
QUEEN_GLB = Path(os.environ.get('ADSL_TEST_MESH')
                 or ROOT / 'research/trellis_small_components_20261017/models/chess_queen.glb')
SOURCE = SOURCE.replace('__QUEEN_GLB__', QUEEN_GLB.as_posix())

INSPECT = '''import bpy, json, math
from mathutils import Vector
meshes = [o for o in bpy.context.scene.objects if o.type == 'MESH']
assert len(meshes) == 5, len(meshes)
def find(path):
    return next(o for o in meshes if o.parent.get('adsl_path') == path)
def close(actual, expected):
    assert (Vector(actual)-Vector(expected)).length < 1e-5, (list(actual), expected)
def center(obj):
    points = [obj.matrix_world @ v.co for v in obj.data.vertices]
    return sum(points, Vector()) / len(points)
close(center(find('Smoke/nested/translated')), (4, 1, 5))
close(center(find('Smoke/hinge')), (1, 1, 0))
close(center(find('Smoke/ball')), (-2, 0, 0))
queen = find('Smoke/decorative_queen')
queen_points = [queen.matrix_world @ v.co for v in queen.data.vertices]
queen_low = Vector(tuple(min(v[i] for v in queen_points) for i in range(3)))
queen_high = Vector(tuple(max(v[i] for v in queen_points) for i in range(3)))
assert ((queen_low + queen_high) / 2 - Vector((4, 0, 0))).length < 0.001
assert queen.get('adsl_kind') == 'external_mesh', queen.get('adsl_kind')
assert all(abs(a-b) < 0.003 for a,b in zip(queen.dimensions, (0.46451, 0.46553, 1.00016))), tuple(queen.dimensions)
cylinder = find('Smoke/diagonal')
direction = Vector((1,2,3)).normalized()
projection = [(cylinder.matrix_world @ v.co).dot(direction) for v in cylinder.data.vertices]
assert abs(min(projection)) < 1e-5
assert abs(max(projection)-math.sqrt(14)) < 1e-5
print('NATIVE_GEOMETRY_CHECKS_PASSED')
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--blender")
    args = parser.parse_args()
    blender = find_blender(args.blender)
    os.environ["ADSL_BLENDER_EXECUTABLE"] = blender
    os.environ["PYTHONUTF8"] = "1"
    parent = ROOT / "outputs"
    parent.mkdir(exist_ok=True)
    # Keep all evidence in a unique task-owned folder for later inspection.
    workspace = Path(tempfile.mkdtemp(prefix="local_verification_", dir=parent))
    source = workspace / "source.py"
    source.write_text(SOURCE, encoding="utf-8")
    result = execute_asset_source(source, workspace / "source_result",
        render=True, render_view_count=2, export_urdf=True, timeout=180)
    report = json.loads((result.output_root / "execution.json").read_text(encoding="utf-8"))
    assert report["mesh_count"] == 5, report
    assert len(result.render_paths) == 2
    assert result.urdf_path and result.urdf_path.is_file()
    urdf_text = result.urdf_path.read_text(encoding='utf-8')
    assert 'chess_queen.glb' in urdf_text and '<mesh' in urdf_text
    for path in (result.glb_path, *result.render_paths):
        assert path.stat().st_size > 100, path
    inspect = workspace / "inspect_blend.py"
    inspect.write_text(INSPECT, encoding="utf-8")
    check = subprocess.run([blender, "--background", report["blend_path"],
        "--python-exit-code", "1", "--python", str(inspect)],
        text=True, encoding="utf-8", capture_output=True, timeout=60)
    (workspace / "inspect.log").write_text(check.stdout + check.stderr, encoding="utf-8")
    assert check.returncode == 0 and "NATIVE_GEOMETRY_CHECKS_PASSED" in check.stdout, check.stdout + check.stderr
    (workspace / "source.log").write_text(result.stdout + result.stderr, encoding="utf-8")
    # JSON round trip must preserve the joint's already-rebased geometry.
    replay = subprocess.run([sys.executable, str(ROOT / "scripts" / "local_3d.py"),
        "build", "--input", str(result.output_root / "asset.json"),
        "--output", str(workspace / "json_result"), "--no-render"],
        text=True, encoding="utf-8", capture_output=True, timeout=90)
    assert replay.returncode == 0, replay.stdout + replay.stderr
    check_json = subprocess.run([blender, "--background", str(workspace / "json_result" / "scene.blend"),
        "--python-exit-code", "1", "--python", str(inspect)],
        text=True, encoding="utf-8", capture_output=True, timeout=60)
    assert check_json.returncode == 0 and "NATIVE_GEOMETRY_CHECKS_PASSED" in check_json.stdout, check_json.stdout + check_json.stderr
    bad = workspace / "bad.py"
    bad.write_text("raise RuntimeError('EXPECTED_WORKER_FAILURE')\n", encoding="utf-8")
    try:
        execute_asset_source(bad, workspace / "bad_result", render=False, export_urdf=False, timeout=60)
    except AssetExecutionError as exc:
        assert "EXPECTED_WORKER_FAILURE" in str(exc), str(exc)
        assert not (workspace / "bad_result" / "execution.json").exists()
    else:
        raise AssertionError("Worker failure was swallowed")
    print(json.dumps({"status": "passed", "workspace": str(workspace), "checks": [
        "official executor uses desktop Blender", "source and JSON round trip",
        "nested transform location", "diagonal cylinder endpoints",
        "joint origin and initial pose", "GLB + URDF + two PNGs",
        "worker failure propagates"]}, indent=2))


if __name__ == "__main__":
    main()
