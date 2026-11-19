"""Create two independent build inputs and an adapted presentation script."""
from pathlib import Path
import json
import sys
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(Path(__file__).resolve().parent))
from model import build,save

parent=ROOT/'outputs/physical_bookshelf_20261017'
template=(ROOT/'examples/counterexamples/vintage_bookshelf_v1/present.py').read_text(encoding='utf-8')
template=template.replace("books=json.loads((HERE/'books.json').read_text(encoding='utf-8'))", """books=json.loads((HERE/'books.json').read_text(encoding='utf-8'))
relations=json.loads((HERE/'relations.json').read_text(encoding='utf-8'))
height=relations['datums']['overall_top']
text=bpy.data.texts.new('adsl_relations.json');text.write(json.dumps(relations,indent=2))
for obj in bpy.context.scene.objects:
    path=obj.get('adsl_path','')
    key=path.partition('/')[2]
    recipe=relations['recipes'].get(key)
    if recipe:
        obj['physical_relation']=recipe.get('relation',recipe['kind'])
        if 'target' in recipe: obj['physical_target']=recipe['target']
        obj['physical_rigid']=recipe['kind'] in ('rigid','supported','attached')
""")
template=template.replace("obj.parent=root;obj['adsl_kind']='native_gilt_lettering'", """volume=next(o for o in bpy.context.scene.objects if o.get('adsl_path','').endswith('/'+b['part_id']))
    obj.parent=volume;obj['adsl_kind']='native_gilt_lettering'""")
template=template.replace('vintage_bookshelf.glb','relational_bookshelf.glb').replace('vintage_bookshelf.blend','relational_bookshelf.blend')
template=template.replace("hero=(2.55,-4.4,2.5);target=(0,-.005,.91)","scale=height/1.847\nhero=tuple(v*scale for v in (2.55,-4.4,2.5));target=(0,-.005,height*.493)")
template=template.replace("view('front',(0,-4.8,1.12),(0,0,.92),2.15)","view('front',(0,-4.8*scale,height*.606),(0,0,height*.498),height*1.164)")
template=template.replace("project='relational_bookshelf.blend'", "relation_schema='adsl.relations.v1',project='relational_bookshelf.blend'")
for name,extra in [('baseline',(0,)*4),('raised_10cm',(.1,)*4)]:
    folder=parent/name;folder.mkdir(parents=True,exist_ok=False)
    result,books=build(extra);save(result,books,folder)
    source="""from pathlib import Path
import sys
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
sys.path.insert(0,str(ROOT/'examples/physical_bookshelf'))
from model import build,save
result,books=build(EXTRA)
scene=save(result,books,HERE)
""".replace('EXTRA',repr(extra))
    (folder/'model.py').write_text(source,encoding='utf-8')
    (folder/'present.py').write_text(template,encoding='utf-8')
    plan=dict(coordinate_frame={'units':'m','up':'+Z','front':'-Y'},
        parameters={'extra_bay_heights':extra},parts=list(result.report['recipes']),
        relations='relations.json',build_order=['datums','structural spans','shelves','supported books','attached lettering'],
        checks=['support contact','containment','default primitive equivalence','single and four bay edits','front and hero render'])
    (folder/'model_plan.json').write_text(json.dumps(plan,indent=2),encoding='utf-8')
print(parent)
