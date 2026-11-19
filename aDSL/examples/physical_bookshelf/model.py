"""Four-level bookcase defined by support, span, attachment and fit relations.

Use build(extra_heights=(.1,.1,.1,.1)) to enlarge all four bays by .1 m.
The default recreates v1 geometry without changing books or board thickness.
"""
import json
from pathlib import Path
from adsl.core import Asset, Cube, Cylinder, RelationAssembly, shape_aabb
from book_library import make_library

WOOD=(.17,.075,.029)
HERE=Path(__file__).resolve().parent

def define():
    a=RelationAssembly('The_Antiquarian_Relational_Bookcase')
    board=a.parameter('shelf_thickness',.032)
    bottom=a.parameter('bottom_shelf_top',.19)
    carcass_bottom=a.parameter('carcass_bottom',.16)
    clearances=[a.parameter(f'bay_{i+1}_clearance',c) for i,c in enumerate((.368,.368,.368,.4))]
    tops=[a.datum('shelf_1_top',bottom)]
    for i in range(1,4):
        tops.append(a.datum(f'shelf_{i+1}_top',tops[-1]+clearances[i-1]+board))
    crown=a.datum('crown_seat',tops[-1]+clearances[-1])
    a.datum('overall_top',crown+.057)

    for s,tag in [(-1,'left'),(1,'right')]:
        a.span_box('walnut_carcass/side_'+tag,bottom=carcass_bottom,top=crown,width=.05,depth=.35,x=s*.58,y=.01,color=WOOD)
        a.span_box('walnut_carcass/front_pilaster_'+tag,bottom=carcass_bottom,top=crown,width=.065,depth=.065,x=s*.583,y=-.168,color=WOOD)
        for dx in (-.022,.022):
            a.span_box('walnut_carcass/pilaster_bead_'+tag+str(dx),bottom=carcass_bottom+.025,top=crown-.025,
                       width=.007,depth=.009,x=s*.583+dx,y=-.204,color=WOOD)
        a.box('walnut_carcass/pilaster_foot_'+tag,(.085,.079,.075),(s*.583,-.169,bottom+.0175),WOOD)
        capital=Cube((.085,.079,.065),center=(s*.583,-.169,0),color=WOOD)
        a.attached('walnut_carcass/pilaster_capital_'+tag,capital,to='walnut_carcass/front_pilaster_'+tag,
                   anchor='top',target_anchor='top',offset=(0,-.001,-.0025))
    for i in range(6):
        a.span_box(f'paneled_back/back_board_{i}',bottom=bottom,top=crown,width=1.11/6-.002,depth=.021,
                   x=-.555+(i+.5)*1.11/6,y=.178,color=WOOD)

    # Fixed floor/base dimensions are independent of the bay clearances.
    for i,(w,d,h,z) in enumerate([(1.25,.405,.035,.0775),(1.225,.39,.025,.1075),(1.19,.37,.055,.1475)]):
        a.box(f'plinth_and_feet/plinth_molding_{i}',(w,d,h),(0,-.004,z),WOOD)
    for x in (-.53,.53):
        for y in (-.135,.135):
            a.box(f'plinth_and_feet/bracket_foot_{x}_{y}',(.125,.105,.065),(x,y,.0325),WOOD)
            a.box(f'plinth_and_feet/foot_pad_{x}_{y}',(.135,.115,.013),(x,y,.0065),WOOD)
    for i,(w,d,h,z) in enumerate([(1.19,.36,.028,.007),(1.215,.385,.022,.032),(1.25,.410,.014,.05)]):
        a.box(f'crown/cornice_step_{i}',(w,d,h),(0,0,crown+z),WOOD)
    a.box('crown/frieze',(1.11,.045,.048),(0,-.166,crown-.026),WOOD)
    a.box('crown/frieze_bead',(1.11,.009,.007),(0,-.193,crown-.038),WOOD)

    shelf_ids=[]
    for row,top in enumerate(tops):
        prefix=f'four_shelves/level_{row+1}/'
        shelf_ids.append(a.box(prefix+'shelf_board',(1.11,.353,board),(0,-.005,top-board/2),WOOD))
        # The edge molding is attached to the board, retaining its thickness.
        a.attached(prefix+'front_nosing',Cube((1.13,.034,.041),color=WOOD),to=shelf_ids[-1],
                   anchor='top',target_anchor='front_top',offset=(0,.0025,0))
        a.attached(prefix+'nosing_bead',Cube((1.125,.007,.006),color=WOOD),to=prefix+'front_nosing',
                   anchor='bottom',target_anchor='front_bottom',offset=(0,-.002,.007))
        for s in (-1,1):
            for y in (-.115,.11):
                pin=Cylinder(.004,p0=(s*.544,y,-.041),p1=(s*.558,y,-.041),color=(.4,.24,.08))
                a.rigid(prefix+f'brass_shelf_pin_{s}_{y}',pin,translation=(0,0,top))

    library,manifest=make_library()
    ids={b['id']:f"books/level_{b['level']}_volume_{b['id']:02d}_{b['kind']}" for b in manifest}
    for b,(key,shape) in zip(manifest,library._children.items()):
        row=b['level']-1
        support=b['supported_by']
        target=shelf_ids[row] if isinstance(support,str) else ids[support]
        b['part_id']=ids[b['id']];b['support_part']=target
        a.supported(b['part_id'],shape,on=target)
        ceiling=tops[row+1]-board if row<3 else crown
        a.within(b['part_id'],(-.555,-.181,tops[row]),(.555,.166,ceiling))
    return a,manifest

def build(extra_heights=(0,0,0,0)):
    if len(extra_heights)!=4:raise ValueError('Provide four bay height changes')
    assembly,books=define()
    base=(.368,.368,.368,.4)
    overrides={f'bay_{i+1}_clearance':base[i]+float(extra_heights[i]) for i in range(4)}
    result=assembly.compile(overrides)
    for b in books:
        b['bounds']=[v.tolist() for v in shape_aabb(result.parts[b['part_id']])]
        b['support_z']=float(shape_aabb(result.parts[b['support_part']])[1][2])
    return result,books

def save(result,books,directory):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    for name,data in [('relations.json',result.report),('books.json',books)]:
        (directory/name).write_text(json.dumps(data,indent=2),encoding='utf-8')
    return result.scene

result,book_manifest=build()
scene=result.scene
if __name__=='__main__':
    save(result,book_manifest,HERE)
    print(json.dumps({'books':len(book_manifest),'height':result.values['overall_top'],'checks':len(result.report['checks'])}))
