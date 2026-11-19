import bpy
import json
from mathutils import Vector
rows = []
sample = []
for obj in bpy.context.scene.objects:
    if obj.type != "MESH":
        continue
    if len(sample) < 5:
        sample.append({"name": obj.name, "parent": obj.parent.name if obj.parent else None, "properties": {k: str(obj[k]) for k in obj.keys()}})
    points = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    low = [min(p[a] for p in points) for a in range(3)]
    high = [max(p[a] for p in points) for a in range(3)]
    if low[2] < -.09:
        rows.append({"name": obj.name, "path": obj.get("adsl_path"), "component": obj.get("finescene_component_id"), "low": low, "high": high})
print("BELOW_FLOOR", json.dumps(rows))
print("SAMPLE", json.dumps(sample))
obj = next((o for o in bpy.context.scene.objects if o.type == "MESH"), None)
chain=[]
while obj:
    chain.append({"name":obj.name,"adsl_path":obj.get("adsl_path"),"props":list(obj.keys())})
    obj=obj.parent
print("CHAIN",json.dumps(chain))
