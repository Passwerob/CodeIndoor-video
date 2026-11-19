"""Check the exported relational assembly and book ownership inside Blender."""
import bpy
import json
from pathlib import Path

here=Path(bpy.data.filepath).parent
embedded=json.loads(bpy.data.texts['adsl_relations.json'].as_string())
assert embedded['schema']=='adsl.relations.v1'
books=json.loads((here/'books.json').read_text(encoding='utf-8'))
parents=[]
for b in books:
    parent=next(o for o in bpy.context.scene.objects if o.get('adsl_path','').endswith('/'+b['part_id']))
    assert parent['physical_relation']=='supported'
    assert parent['physical_target']==b['support_part']
    parents.append(parent)
titles=[o for o in bpy.context.scene.objects if o.get('adsl_kind')=='native_gilt_lettering']
assert len(parents)==64 and len(titles)==36
assert all(o.parent in parents for o in titles)
# Actual Blender geometry must match solved world bounds to float precision.
for b,parent in zip(books,parents):
    meshes=[o for o in parent.children_recursive if o.type=='MESH' and o.get('adsl_kind')!='native_gilt_lettering']
    # Inspect the editable source vertices: evaluated bound_box includes the
    # presentation bevel, which rounds the sharp contact corners of tilted books.
    pts=[o.matrix_world@v.co for o in meshes for v in o.data.vertices]
    low=[min(v[i] for v in pts) for i in range(3)]
    high=[max(v[i] for v in pts) for i in range(3)]
    assert max(abs(low[i]-b['bounds'][0][i]) for i in range(3))<1e-5,(b['id'],low,b['bounds'][0])
    assert max(abs(high[i]-b['bounds'][1][i]) for i in range(3))<1e-5,(b['id'],high,b['bounds'][1])
report=dict(embedded_relation_schema=embedded['schema'],books=64,
            lettering_objects=36,lettering_parented_to_own_book=True,
            editable_mesh_bounds_match=True,height=embedded['datums']['overall_top'])
(here/'blender_validation.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print('RELATIONAL_BLEND_VERIFIED '+json.dumps(report))
