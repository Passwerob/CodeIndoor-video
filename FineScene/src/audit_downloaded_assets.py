import bpy,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import finescene_env as fenv
from mathutils import Vector
ROOT=fenv.STUDY_ASSETS
results={}
sources=list(ROOT.glob('*/*.gltf'))+list(ROOT.glob('*/*.blend'))+list(ROOT.glob('*/*.glb'))+[fenv.POLYHAVEN_ASSETS/'book_encyclopedia_set_01_2k'/'book_encyclopedia_set_01_2k.gltf']
for path in sources:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    if path.suffix=='.blend':
        with bpy.data.libraries.load(str(path),link=False) as (a,b): b.objects=a.objects
        for obj in b.objects:
            if obj: bpy.context.scene.collection.objects.link(obj)
    else: bpy.ops.import_scene.gltf(filepath=str(path))
    bpy.context.view_layer.update()
    objects=[]
    for obj in bpy.context.scene.objects:
        if obj.type!='MESH':continue
        pts=[obj.matrix_world@Vector(p) for p in obj.bound_box]
        low=[min(p[i] for p in pts) for i in range(3)];high=[max(p[i] for p in pts) for i in range(3)]
        objects.append({'name':obj.name,'size':[high[i]-low[i] for i in range(3)],'low':low,'high':high,'vertices':len(obj.data.vertices),'materials':[m.name for m in obj.data.materials if m]})
    results[path.parent.name]={'source':str(path),'objects':objects,'images':len(bpy.data.images)}
(ROOT/'blender_asset_audit.json').write_text(json.dumps(results,indent=2),encoding='utf-8')
print('ASSET_AUDIT_COMPLETE',len(results))
