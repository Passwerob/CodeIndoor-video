"""Read-only Blender diagnostics; never alter or accept a generated scene."""
import argparse
import json
import sys
from pathlib import Path
import bpy

parser=argparse.ArgumentParser()
parser.add_argument('--scene',type=Path,required=True)
parser.add_argument('--out',type=Path,required=True)
args=parser.parse_args(sys.argv[sys.argv.index('--')+1:])
bpy.ops.wm.open_mainfile(filepath=str(args.scene.resolve()))
records=[]
for obj in bpy.data.objects:
    if obj.type!='MESH':continue
    materials=[]
    for material in obj.data.materials:
        if material is None:continue
        nodes=material.node_tree.nodes if material.use_nodes else []
        images=[{'name':n.image.name,'path':n.image.filepath,'size':list(n.image.size),
                 'has_data':bool(n.image.has_data),'packed':bool(n.image.packed_file),
                 'linked':any(s.is_linked for s in n.outputs)}
                for n in nodes if n.type=='TEX_IMAGE' and n.image]
        materials.append({'name':material.name,'use_nodes':material.use_nodes,'images':images})
    cursor=obj
    while cursor and not cursor.get('adsl_path'):cursor=cursor.parent
    semantic_path=cursor.get('adsl_path','') if cursor else ''
    records.append({'object':obj.name,'path':semantic_path,'kind':obj.get('adsl_kind',''),
                    'uv_layers':len(obj.data.uv_layers),'materials':materials})
args.out.write_text(json.dumps({'scene':str(args.scene),'meshes':records},indent=2),encoding='utf-8')
