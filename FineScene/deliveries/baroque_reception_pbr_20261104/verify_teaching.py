"""Verification harness for the composition / integrity teaching upgrade.

Runs the SAME deterministic metrics on two real artefacts:
  negative: FineScene Agent run complete_discussion_room_20261023_01
            (frozen spatial contract + coarse layout + decomposition)
  positive: this direct-construction baroque delivery (measured .blend AABBs)

It also exercises the live planner adapter with plain stand-in objects, so a
broken hook cannot silently ship. No Agent run is executed or fabricated here.
"""
from __future__ import annotations

import json
import os
import sys
from types import SimpleNamespace

SRC = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "src"))
sys.path.insert(0, SRC)
import composition_metrics as cm
import part_integrity as pi

ROOT = os.path.dirname(os.path.abspath(__file__))
# FineScene agent run used as the negative example (local run output, not in git).
AGENT = os.environ.get("TEACHING_AGENT_RUN") or os.path.join(
    SRC, "..", "runs", "complete_discussion_room_20261023_01", "agent_runtime")


def _read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def agent_scene():
    dec = _read(os.path.join(AGENT, "decomposition_accepted.json"))
    spatial = _read(os.path.join(AGENT, "spatial_plan_accepted.json"))
    raw = open(os.path.join(AGENT, "coarse_layout_0_raw.txt"),
               encoding="utf-8").read()
    try:
        layout = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find('"placements"')
        end = raw.find('"route_points"')
        frag = raw[start:end].rstrip().rstrip(",")
        layout = json.loads("{" + frag + "}")
    poses = {p["id"]: p for p in layout["placements"]}
    params = dec.get("parameters", {})

    def dim(v):
        return float(params.get(v, v) if isinstance(v, str) else v)

    roles = {"floor": "shell", "wall_south": "shell", "wall_north": "shell",
             "wall_east": "shell", "wall_west": "shell",
             "table": "coffee_table", "chair_a": "armchair",
             "chair_b": "armchair", "bookshelf": "bookshelf"}
    objects = []
    for o in dec["objects"]:
        p = poses.get(o["id"])
        if not p:
            continue
        objects.append({"id": o["id"], "role": roles.get(o["id"], "accent"),
                        "position": [float(v) for v in p["position"]],
                        "size": [dim(v) for v in o["dimensions"]],
                        "yaw_deg": float(p.get("yaw_deg", 0) or 0),
                        "semantic": o.get("semantic", "")[:60]})
    # The frozen contract mixes numeric values with symbolic strings such as
    # 'w/2+0.35'. Use the Agent's OWN numerical judge polygons instead of
    # guessing an expression evaluator, and record that substitution.
    judge = _read(os.path.join(AGENT, "coarse_spatial_judge.json"))
    areas = []
    for aid, pts in judge.get("access_polygons_m", {}).items():
        if not aid.startswith("acc_"):
            continue
        xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
        areas.append({"id": aid,
                      "center": [(max(xs) + min(xs)) / 2,
                                 (max(ys) + min(ys)) / 2],
                      "size": [max(xs) - min(xs), max(ys) - min(ys)]})
    extent = [float(v) for v in dec["extent"]]
    return {
        "room": {"width": extent[0], "depth": extent[1], "height": extent[2]},
        "objects": objects,
        "focus_id": "table",
        "enclosure_group": ["chair_a", "chair_b"],
        "entrance": {"position": [0.0, -extent[1] / 2]},
        "access_area_source": "coarse_spatial_judge.json / access_polygons_m "
                              "(frozen contract contains symbolic expressions)",
        "access_areas": areas,
        "circulation_zones": [
            {"id": z["id"], "center": z["center"], "size": z["size"]}
            for z in spatial["zones"] if not z.get("component_ids")
            and all(isinstance(v, (int, float)) for v in z["center"] + z["size"])
        ],
        "routes": [{"id": r["id"], "points": r["points"]}
                   for r in spatial["routes"]],
        "symmetry_pairs": [["chair_a", "chair_b"]],
        "brief": "生成一个会客厅",
    }


def own_scene():
    d = _read(os.path.join(ROOT, "scene_normalized.json"))
    d["brief"] = "西欧巴洛克风格的会客厅"
    return d


def clearances_for_agent():
    """Real numbers recorded by the Agent's own judges.

    coarse_spatial_judge.json measured 0.400 m against a 0.8 m route
    (half-width 0.400); final_visual_qa.json measured 0.395 m on the
    compiled mesh, which is the blocking failure.
    """
    return [
        {"id": "route_entrance_to_chair_a@plan", "required_m": 0.400,
         "measured_m": 0.400, "a": "chair_a", "b": "route_entrance_to_chair_a",
         "margin_m": 0.05},
        {"id": "route_entrance_to_chair_a@mesh", "required_m": 0.400,
         "measured_m": 0.395, "a": "chair_a", "b": "route_entrance_to_chair_a",
         "margin_m": 0.05},
    ]


def hook_smoke_test():
    """Exercise from_finescene with stand-ins: a broken adapter must be caught."""
    outline = SimpleNamespace(
        parameters={},
        extent=[4.0, 4.0, 2.8],
        objects=[
            SimpleNamespace(id="table", dimensions=[1.2, 0.75, 0.45],
                            semantic="low coffee table", category="furniture",
                            collision_role="solid"),
            SimpleNamespace(id="chair_a", dimensions=[0.46, 0.52, 0.9],
                            semantic="upholstered armchair", category="furniture",
                            collision_role="solid"),
            SimpleNamespace(id="chair_b", dimensions=[0.46, 0.52, 0.9],
                            semantic="upholstered armchair", category="furniture",
                            collision_role="solid"),
        ])
    layout = SimpleNamespace(placements=[
        SimpleNamespace(id="table", position=[-0.35, 0, 0], yaw_deg=0),
        SimpleNamespace(id="chair_a", position=[-0.35, -0.69, 0], yaw_deg=180),
        SimpleNamespace(id="chair_b", position=[-0.35, 0.69, 0], yaw_deg=0),
    ])
    spatial = SimpleNamespace(
        access_areas=[SimpleNamespace(id="a1", center=[0, 0], size=[0.8, 0.8]),
                      SimpleNamespace(id="a2", center=[0.2, 0], size=[0.8, 0.8])],
        routes=[SimpleNamespace(id="r1", points=[[0, -2], [0, -1], [0, 0]]),
                SimpleNamespace(id="r2", points=[[0, -2], [0, -1], [1, 0]])],
        openings=[SimpleNamespace(id="door", kind="entrance", wall="y_min",
                                  offset=0.0)])
    return cm.composition_hook(outline, layout, spatial, "生成一个会客厅")


def main():
    a_scene, m_scene = agent_scene(), own_scene()
    a_brief = a_scene["brief"]
    m_brief = m_scene["brief"]

    results = {}
    for label, scene, brief in (("agent_run_20261023_01", a_scene, a_brief),
                                ("baroque_direct_20261104", m_scene, m_brief)):
        inferred = cm.evaluate(scene, brief=brief)
        common = cm.evaluate(scene, archetype=cm.ARCHETYPES["reception_lounge"])
        results[label] = {"inferred_archetype": inferred["archetype"],
                          "inferred": inferred,
                          "common_reception_lounge": common}

    parts = pi.load_parts(os.path.join(ROOT, "parts.json"))
    joints = _read(os.path.join(ROOT, "joint_pairs.json"))
    own_integrity = pi.check(parts, joint_pairs=joints,
                             clearances=[], ground_z=0.0)
    agent_integrity = pi.check([], clearances=clearances_for_agent())
    hook = hook_smoke_test()

    report = {
        "purpose": "deterministic composition / integrity teaching verification",
        "provenance": {
            "agent_run": os.path.join(AGENT, "spatial_plan_accepted.json"),
            "agent_decomposition": os.path.join(AGENT, "decomposition_accepted.json"),
            "agent_layout": os.path.join(AGENT, "coarse_layout_0_raw.txt"),
            "own_geometry": os.path.join(ROOT, "parts.json"),
            "own_normalized": os.path.join(ROOT, "scene_normalized.json"),
            "joints": os.path.join(ROOT, "joint_pairs.json"),
        },
        "honesty_note": (
            "No FineScene Agent run was executed. The Agent sample is a "
            "frozen historical run; the positive sample is real measured "
            "geometry from the delivered .blend. The direct construction has "
            "no frozen access/route contract, so circulation metrics are "
            "reported as not_measured instead of being invented."
        ),
        "composition": results,
        "part_integrity": {
            "own_blend": {"counts": own_integrity["counts"],
                          "status": own_integrity["status"],
                          "findings": own_integrity["findings"]},
            "agent_clearance_only": {
                "counts": agent_integrity["counts"],
                "status": agent_integrity["status"],
                "findings": agent_integrity["findings"]},
            "note": "The historical Agent run has no part-level mesh export; "
                    "only its recorded clearance numbers can be checked here.",
        },
        "design_rules": pi.DESIGN_RULES,
        "injected_prompt": cm.composition_archetype_prompt(a_brief),
        "planner_hook_smoke_test": hook,
    }
    out = os.path.join(ROOT, "teaching_report.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)

    # ------------------------------------------------------------------ print
    print("=" * 78)
    print("COMPOSITION METRICS  (same ruler, two real artefacts)")
    print("=" * 78)
    keys = ["footprint_coverage_pct", "enclosure_members", "focus_distance_cv",
            "focus_facing_max_deg", "azimuth_coverage_deg",
            "symmetry_error_m", "access_area_overlap_m2",
            "shared_trunk_ratio", "focal_corridor_occlusion"]
    print(f"{'metric':<32}{'Agent run':>20}{'This delivery':>20}")
    for k in keys:
        a = results["agent_run_20261023_01"]["inferred"]["metrics"].get(k)
        m = results["baroque_direct_20261104"]["inferred"]["metrics"].get(k)
        fmt = lambda v: ("-" if v is None else
                         (f"{v:.4f}" if isinstance(v, float) else str(v)))
        print(f"{k:<32}{fmt(a):>20}{fmt(m):>20}")
    print()
    for label, r in results.items():
        inf = r["inferred"]
        print(f"[{label}] archetype={inf['archetype']} status={inf['status']} "
              f"issues={inf['issue_count']}")
        for it in inf["issues"]:
            print(f"   - {it['code']:<28} -> {it['repair_target']:<14} "
                  f"{it['evidence'][:96]}")
    print()
    print("UNIFIED archetype=reception_lounge for both (fair comparison):")
    for label, r in results.items():
        c = r["common_reception_lounge"]
        print(f"   {label:<30} status={c['status']:<15} issues={c['issue_count']}")
        for it in c["issues"]:
            print(f"      - {it['code']} -> {it['repair_target']}")
    print()
    print("=" * 78)
    print("PART INTEGRITY (real AABBs from .blend)")
    print("=" * 78)
    print("own blend counts:", json.dumps(own_integrity["counts"],
                                          ensure_ascii=False))
    bycode = pi.by_code(own_integrity)
    for code, items in sorted(bycode.items()):
        print(f"  {code:<28} {len(items):>4}")
        for f in items[:4]:
            print("      ", f["evidence"][:110])
    print("agent clearance-only:", agent_integrity["status"],
          agent_integrity["counts"])
    for f in agent_integrity["findings"]:
        print("   -", f["code"], "->", f["repair_target"], "|",
              f["evidence"][:110])
    print()
    print("planner hook smoke test:", hook["status"],
          "issues=", hook.get("issue_count"),
          "blocked=", hook.get("blocked_reason", "none"))
    print("REPORT", out)


if __name__ == "__main__":
    main()
