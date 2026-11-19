"""Swap hand-built objects into the assembled scene and render video cameras.

    blender -b measured/scene/scene.blend --python hand_objects/preview.py -- \
        OUT_DIR F00,F01 [oid ...]

Each oid is re-imported from runtime/outputs/hand/<oid>/<oid>.blend with the
scene builder's own placement (measured pose, fit to the measured box), so the
render is what the rebuilt scene will show. A check, not a deliverable."""
import json
import sys
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "adsl_adapter"))
sys.argv = [a for a in sys.argv]          # video_scene_builder reads nothing at import
import video_scene_builder as VSB  # noqa: E402

argv = sys.argv[sys.argv.index("--") + 1:]
out, frames, oids = Path(argv[0]), argv[1].split(","), argv[2:]
pct = 50
samples = 64
for a in list(oids):
    if a.startswith("--"):
        oids.remove(a)
    elif a.startswith("pct="):
        pct = int(a[4:]); oids.remove(a)
    elif a.startswith("spp="):
        samples = int(a[4:]); oids.remove(a)
out.mkdir(parents=True, exist_ok=True)
plan = json.loads((ROOT / "runtime/video/pantry/measured/scene_plan.json").read_text())
entries = {e["id"]: e for e in plan["objects"]}
roots = {o.get("ci_object"): o for o in bpy.data.objects if o.get("ci_object") and o.parent is None}
for oid in oids:
    old = roots.get(oid)
    col = old.users_collection[0] if old else bpy.context.scene.collection
    if old is not None:
        for o in VSB._subtree_meshes_and_empties(old) + [old]:
            bpy.data.objects.remove(o, do_unlink=True)
    e = dict(entries[oid])
    e["blend"] = str(ROOT / f"runtime/outputs/hand/{oid}/{oid}.blend")
    e["forge_status"] = "accepted"
    print("IMPORT", oid, VSB.import_forged(e, col))
if "--refined" in sys.argv:                     # per-frame camera correction (geometry/refine_cameras.py)
    ref = json.loads((ROOT / "runtime/video/pantry/geometry/camera_refine.json").read_text())
    for f, r in ref.items():
        cam = bpy.data.objects.get("CAM_src_" + f)
        if cam is not None:
            cam.location = r["location"]
s = bpy.context.scene
prefs = bpy.context.preferences.addons["cycles"].preferences
prefs.compute_device_type = "CUDA"
prefs.get_devices()
for d in prefs.devices:
    d.use = d.type == "CUDA"
s.cycles.device = "GPU"
s.cycles.samples = samples
s.render.resolution_percentage = pct
for f in frames:
    cam = bpy.data.objects.get("CAM_src_" + f) or bpy.data.objects.get(f)
    if cam is None:
        continue
    s.camera = cam
    s.render.filepath = str(out / f"r_{f}.png")
    bpy.ops.render.render(write_still=True)
if "--save" in sys.argv:
    bpy.ops.wm.save_as_mainfile(filepath=str(out / "preview.blend"))
