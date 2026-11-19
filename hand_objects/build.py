"""Build hand-written object programs with the forge runner (no LLM in the loop).

    python hand_objects/build.py water_dispenser [microwave ...]

Each object has hand_objects/<oid>/program.py (a normal forge program:
build(B, mats, P)) and optionally textures.py (writes its images into
runtime/video/pantry/hand_tex/<oid>/). The envelope is the measured box from
measured/scene_plan.json; the functional spec (objects/<oid>/spec.json) is
passed so forge_runner checks the parts and rigs the joints. Output:
runtime/outputs/hand/<oid>/<oid>.blend plus three studio views.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "runtime/video/pantry"
OUT = ROOT / "runtime/outputs/hand"
sys.path.insert(0, str(ROOT / "adsl_adapter"))
from codeindoor_env import BLENDER, setting  # noqa: E402  (env -> runtime/.env -> defaults)
PY = setting("TEX_PY") or sys.executable  # python with PIL + OpenCV, runs textures.py

plan = {e["id"]: e for e in json.loads((RUN / "measured/scene_plan.json").read_text())["objects"]}
for oid in sys.argv[1:]:
    src = ROOT / "hand_objects" / oid
    tex = RUN / "hand_tex" / oid
    tex.mkdir(parents=True, exist_ok=True)
    if (src / "textures.py").is_file():
        r = subprocess.run([PY, str(src / "textures.py"), str(tex)], capture_output=True, text=True)
        if r.returncode:
            print(r.stdout[-2000:], r.stderr[-3000:])
            sys.exit(f"{oid}: textures failed")
    e = plan[oid]
    dims = e.get("dims") if e.get("fit_dims") else (e.get("hand_dims") or e.get("dims"))
    params = {"width": dims[0], "depth": dims[1], "height": dims[2], "units": "m",
              "mount": e.get("mount", "floor"), "tex_dir": str(tex), "measured_dims": e.get("measured_dims")}
    params.update(e.get("hand_params") or {})
    od = OUT / oid
    od.mkdir(parents=True, exist_ok=True)
    (od / "params.json").write_text(json.dumps(params, indent=1))
    argv = [BLENDER, "-b", "--python", str(ROOT / "adsl_adapter/forge_runner.py"), "--",
            str(src / "program.py"), str(od / "params.json"), str(od), oid]
    spec = RUN / "objects" / oid / "spec.json"
    if spec.is_file():
        argv.append(str(spec))
    env = dict(os.environ, FINESCENE_ROOT=str(ROOT / "FineScene"), CYCLES_DEVICE="CUDA")
    p = subprocess.run(argv, capture_output=True, text=True, env=env)
    (od / "blender_stdout.txt").write_text(p.stdout[-20000:] + "\n--- STDERR ---\n" + p.stderr[-5000:])
    res = json.loads((od / "forge_result.json").read_text()) if (od / "forge_result.json").is_file() else {}
    if not res.get("ok"):
        print(f"{oid}: FAILED {res.get('error')}\n{res.get('traceback', '')[-1800:]}")
        continue
    rep = res.get("spec_report") or {}
    print(f"{oid}: ok parts={res['objects']} size={res['bounds']['size']} envelope_ok={res.get('envelope_ok')} "
          f"joints={[j['id'] for j in res.get('joints') or []]} hard={len(rep.get('hard') or [])}")
    for v in (rep.get("hard") or [])[:8]:
        print("   HARD:", (v.get("message") if isinstance(v, dict) else v)[:300])
