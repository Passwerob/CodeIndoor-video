"""The Antiquarian: four usable shelves, walnut joinery and curated book clusters."""
import json
import math
import random
import itertools
from pathlib import Path
from adsl.core import Asset, Cube, Cylinder, Sphere, rotate_shape, translate_shape, scale_shape, shape_aabb

P = dict(width=1.25, depth=.38, height=1.84, inner_width=1.11,
         shelf_thickness=.032, shelf_tops=[.19,.59,.99,1.39], cap_bottom=1.79)
WOOD=(.17,.075,.029)
PALETTES=[(.16,.025,.026),(.035,.10,.073),(.045,.085,.13),(.24,.12,.045),
          (.31,.23,.105),(.18,.125,.085),(.115,.06,.075),(.30,.28,.21)]
scene=Asset('The_Antiquarian_Four_Tier_Bookcase')
manifest=[]
rng=random.Random(20261017)

def box(parent,name,size,center,color=WOOD):
    return parent.attach_part(name,Cube(size,center=center,color=color))

frame=scene.attach_part('walnut_carcass',Asset('Joined walnut carcass'))
for s,tag in [(-1,'left'),(1,'right')]:
    box(frame,'side_'+tag,(.05,.35,1.63),(s*.58,.01,.975))
    box(frame,'front_pilaster_'+tag,(.065,.065,1.63),(s*.583,-.168,.975))
    # Layered narrow beads on the front stiles.
    for dx in (-.022,.022):
        box(frame,'pilaster_bead_'+tag+str(dx),(.007,.009,1.58),(s*.583+dx,-.204,.975))
    box(frame,'pilaster_foot_'+tag,(.085,.079,.075),(s*.583,-.169,.2075))
    box(frame,'pilaster_capital_'+tag,(.085,.079,.065),(s*.583,-.169,1.755))

back=scene.attach_part('paneled_back',Asset('Six tongue and groove backing boards'))
for i in range(6):
    box(back,f'back_board_{i}',(1.11/6-.002,.021,1.60),(-.555+(i+.5)*1.11/6,.178,.99))

base=scene.attach_part('plinth_and_feet',Asset('Stepped plinth and bracket feet'))
for i,(w,d,h,z) in enumerate([(1.25,.405,.035,.0775),(1.225,.39,.025,.1075),(1.19,.37,.055,.1475)]):
    box(base,f'plinth_molding_{i}',(w,d,h),(0,-.004,z))
for x in (-.53,.53):
    for y in (-.135,.135):
        box(base,f'bracket_foot_{x}_{y}',(.125,.105,.065),(x,y,.0325))
        box(base,f'foot_pad_{x}_{y}',(.135,.115,.013),(x,y,.0065))

crown=scene.attach_part('crown',Asset('Layered vintage cornice'))
for i,(w,d,h,z) in enumerate([(1.19,.36,.028,1.797),(1.215,.385,.022,1.822),(1.25,.410,.014,1.84)]):
    box(crown,f'cornice_step_{i}',(w,d,h),(0,0,z))
box(crown,'frieze',(1.11,.045,.048),(0,-.166,1.764))
box(crown,'frieze_bead',(1.11,.009,.007),(0,-.193,1.752))

shelves=scene.attach_part('four_shelves',Asset('Exactly four book bearing levels'))
for row,top in enumerate(P['shelf_tops']):
    shelf=shelves.attach_part(f'level_{row+1}',Asset(f'Shelf {row+1}'))
    box(shelf,'shelf_board',(1.11,.353,P['shelf_thickness']),(0,-.005,top-P['shelf_thickness']/2))
    box(shelf,'front_nosing',(1.13,.034,.041),(0,-.179,top-.0205))
    box(shelf,'nosing_bead',(1.125,.007,.006),(0,-.198,top-.031))
    for s in (-1,1):
        for y in (-.115,.11):
            shelf.attach_part(f'brass_shelf_pin_{s}_{y}',Cylinder(.004,p0=(s*.544,y,top-.041),p1=(s*.558,y,top-.041),color=(.4,.24,.08)))

books=scene.attach_part('books',Asset('An irregular personal library'))

def book(row,left,kind='upright',bottom=None,angle=0,yaw=0,height=None):
    """Assemble in book coordinates, rotate, then place by transformed AABB."""
    idx=len(manifest); palette=rng.randrange(len(PALETTES)); color=PALETTES[palette]
    t=rng.uniform(.026,.049);d=rng.uniform(.195,.256);h=height or rng.uniform(.236,.332)
    item=Asset(f'Volume_{idx:02d}')
    cover=.0028
    for s in (-1,1):
        box(item,f'cover_{palette}_{s}',(cover,d,h),(s*(t-cover)/2,0,h/2),color)
    box(item,'aged_pages',(t-2*cover,d-.011,h-.012),(0,.003,h/2),(.67,.57,.39))
    box(item,f'spine_{palette}',(t,.009,h),(0,-d/2+.003,h/2),color)
    # Raised bands and quiet gilt double rules are attached to the binding.
    for j,z in enumerate((.035,h*.28,h*.74,h-.033)):
        box(item,f'binding_band_{palette}_{j}',(t+.001,.003,.004),(0,-d/2-.002,z),color)
        if idx%3!=1:
            box(item,f'gilt_rule_{j}',(t-.006,.0012,.0009),(0,-d/2-.0037,z+.004),( .48,.30,.095))
    if idx%4!=0:
        box(item,'spine_label',(max(.014,t-.009),.0016,.039),(0,-d/2-.002,h*.56),(.07,.046,.024))
        for z in (h*.56-.017,h*.56+.017):
            box(item,'gilt_label_'+str(z),(t-.012,.001,.0009),(0,-d/2-.0032,z),(.48,.30,.095))
    # A few paper edges keep horizontal stacks recognizable at a distance.
    for j in range(3):
        box(item,f'page_edge_{j}',(t-2*cover,.0007,.0005),(0,d/2-.002,h*(.23+j*.24)),(.43,.35,.23))
    if kind=='stack':
        item=rotate_shape(item,'+y',90,center=(0,0,0))
    elif angle:
        item=rotate_shape(item,'+y',angle,center=(0,0,0))
    if yaw:item=rotate_shape(item,'+z',yaw,center=(0,0,0))
    low,high=shape_aabb(item)
    top=P['shelf_tops'][row] if bottom is None else bottom
    front=-.169+rng.uniform(0,.027)
    shift=(left-float(low[0]),front-float(low[1]),top-float(low[2]))
    item=translate_shape(item,shift)
    books.attach_part(f'level_{row+1}_volume_{idx:02d}_{kind}',item)
    lo,hi=shape_aabb(item)
    manifest.append(dict(id=idx,level=row+1,kind=kind,palette=palette,lean_degrees=angle,yaw=yaw,
                         bounds=[lo.tolist(),hi.tolist()],support_z=top))
    return float(hi[0]),float(hi[2])

def run(row,x,n,lean_last=0):
    for j in range(n):
        x,_=book(row,x,angle=lean_last if j==n-1 else 0)
        x+=rng.uniform(.0015,.004)
    return x

def stack(row,left,n):
    z=P['shelf_tops'][row]
    for j in range(n):
        _,z=book(row,left+rng.uniform(0,.015),'stack',bottom=z,yaw=rng.uniform(-3.5,3.5),height=rng.uniform(.235,.27))

# Curated clusters, alternating gaps and differently sized flat stacks.
run(0,-.538,7,-12); stack(0,-.105,3); run(0,.232,6)
stack(1,-.525,4); run(1,-.19,8); run(1,.286,4)
run(2,-.538,6,-14); stack(2,-.16,3); run(2,.174,7)
run(3,-.538,4); stack(3,-.318,3); run(3,.025,9,-10)

def validate():
    counts=[sum(b['level']==i for b in manifest) for i in range(1,5)]
    assert len(P['shelf_tops'])==4
    for b in manifest:
        lo,hi=b['bounds'];assert all(math.isfinite(v) for v in lo+hi)
        assert abs(lo[2]-b['support_z'])<1e-7
        assert lo[0]>=-.555 and hi[0]<=.555,(b['id'],'x',lo,hi)
        assert lo[1]>=-.181 and hi[1]<.166,(b['id'],'y',lo,hi)
        ceiling=P['shelf_tops'][b['level']]-.032 if b['level']<4 else P['cap_bottom']
        assert hi[2]<ceiling,(b['id'],'headroom')
    for a,b in itertools.combinations(manifest,2):
        if a['level']!=b['level']:continue
        lo,hi=a['bounds'];low,high=b['bounds']
        overlap=[min(hi[i],high[i])-max(lo[i],low[i]) for i in range(3)]
        assert min(overlap)<.0001,(a['id'],b['id'],'overlapping book bounds')
    return dict(book_count=len(manifest),books_per_level=counts,usable_levels=4,
                primitives=len(list(scene.iter_primitives())),bounds=[a.tolist() for a in shape_aabb(scene)],
                checks=['finite bounds','books within shelf bays','vertical headroom','shelf / stack AABB contact','no overlapping book AABBs'],
                limitations=['Contact uses axis-aligned bounds; no full polygon collision or structural load simulation.'])

if __name__=='__main__':
    here=Path(__file__).resolve().parent
    (here/'books.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    report=validate()
    (here/'validation.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))
