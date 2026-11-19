"""Verify editability, datablock independence and real PBR links in the .blend.

Run:  blender -b baroque_reception.blend --python validate_blend.py
Out:  validation_report.json
"""
import bpy
import json
import os

ROOT = os.path.dirname(os.path.abspath(__file__))
report = {"blend": "baroque_reception.blend", "checks": {}}


def snapshot(objects):
    bpy.context.view_layer.update()
    return {o.name: tuple(o.matrix_world.translation) for o in objects}


def max_shift(a, b, names):
    return max((sum((a[n][i]-b[n][i])**2 for i in range(3))**0.5 for n in names), default=0.0)


all_parts = [o for o in bpy.data.objects if "adsl_part" in o]
roots = [o for o in bpy.data.objects if o.name.startswith("ROOT_")]
collections = [c for c in bpy.data.collections if c.name.startswith("OBJ_")]

report["counts"] = {
    "editable_objects": len(collections),
    "empty_roots": len(roots),
    "independent_parts": len(all_parts),
    "materials": len(bpy.data.materials),
    "packed_images": sum(1 for i in bpy.data.images if i.packed_file),
    "file_images": sum(1 for i in bpy.data.images if i.source == "FILE"),
}

# 1. every object has its own collection + empty root
missing = [c.name for c in collections
           if not any(o.name.startswith("ROOT_") for o in c.objects)]
report["checks"]["collection_has_root"] = {"passed": not missing, "missing": missing}

# 2. no shared mesh / curve datablocks
seen, shared = {}, []
for o in all_parts:
    key = o.data.name if o.data else None
    if key in seen:
        shared.append([seen[key], o.name])
    else:
        seen[key] = o.name
report["checks"]["unique_datablocks"] = {"passed": not shared, "shared": shared[:10]}

# 3. aDSL metadata on every part
no_meta = [o.name for o in all_parts
           if not all(k in o for k in ("adsl_type", "adsl_params", "adsl_material"))]
report["checks"]["adsl_custom_properties"] = {"passed": not no_meta, "missing": no_meta[:10]}

# 4. real PBR node chains
pbr = {}
for m in bpy.data.materials:
    if not m.name.startswith("PBR_"):
        continue
    if not m.use_nodes:
        pbr[m.name] = {"passed": False, "reason": "no nodes"}
        continue
    nt = m.node_tree
    p = next((n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if p is None:
        pbr[m.name] = {"passed": False, "reason": "no principled"}
        continue
    linked = {i.name: any(l.to_socket == i for l in nt.links) for i in p.inputs}
    images = [n.image.name for n in nt.nodes
              if n.type == "TEX_IMAGE" and n.image is not None]
    pbr[m.name] = {
        "passed": bool(linked.get("Base Color")) and bool(linked.get("Roughness"))
                  and bool(linked.get("Normal")),
        "base_color_linked": bool(linked.get("Base Color")),
        "roughness_linked": bool(linked.get("Roughness")),
        "normal_linked": bool(linked.get("Normal")),
        "texture_nodes": len(images),
    }
report["checks"]["pbr_node_chains"] = {
    "passed": all(v.get("passed") for v in pbr.values()), "materials": pbr}

# 5. single-part edit isolation
target = next((o for o in all_parts
               if o.parent and o.parent.name == "ROOT_sofa_west"), None)
if target:
    before = snapshot(all_parts)
    old = tuple(target.location)
    target.location = (old[0] + 0.30, old[1], old[2])
    after = snapshot(all_parts)
    others = [o.name for o in all_parts if o.name != target.name]
    shift_others = max_shift(before, after, others)
    shift_self = max_shift(before, after, [target.name])
    report["checks"]["single_part_edit_isolation"] = {
        "passed": shift_self > 0.29 and shift_others < 1e-6,
        "part": target.name, "self_shift_m": round(shift_self, 6),
        "max_other_shift_m": round(shift_others, 9)}
    target.location = old
    bpy.context.view_layer.update()
else:
    report["checks"]["single_part_edit_isolation"] = {"passed": False, "reason": "target missing"}

# 6. whole-object edit propagates only to its own parts
sofa_root = bpy.data.objects.get("ROOT_sofa_west")
if sofa_root:
    before = snapshot(all_parts)
    old = tuple(sofa_root.location)
    sofa_root.location = (old[0] + 0.40, old[1], old[2])
    after = snapshot(all_parts)
    own = [o.name for o in all_parts if o.parent and o.parent.name == "ROOT_sofa_west"]
    other = [o.name for o in all_parts if o.name not in own]
    own_shift = min((sum((after[n][i]-before[n][i])**2 for i in range(3))**0.5
                     for n in own), default=0.0)
    other_shift = max_shift(before, after, other)
    report["checks"]["whole_object_edit_isolation"] = {
        "passed": own_shift > 0.39 and other_shift < 1e-6,
        "own_parts": len(own), "min_own_shift_m": round(own_shift, 6),
        "max_other_shift_m": round(other_shift, 9)}
    sofa_root.location = old
    bpy.context.view_layer.update()
else:
    report["checks"]["whole_object_edit_isolation"] = {"passed": False, "reason": "root missing"}

report["all_passed"] = all(
    v.get("passed", False) for k, v in report["checks"].items())
report["provenance"] = ("direct parametric aDSL + real Poly Haven CC0 PBR; "
                        "NO TRELLIS; NOT a FineScene Agent run")

with open(os.path.join(ROOT, "validation_report.json"), "w", encoding="utf-8") as fh:
    json.dump(report, fh, indent=2, ensure_ascii=False)
print("ALL_PASSED", report["all_passed"])
for k, v in report["checks"].items():
    print(" ", k, v.get("passed"))
