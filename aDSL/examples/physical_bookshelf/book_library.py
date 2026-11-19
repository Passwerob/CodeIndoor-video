"""Copied v1 book geometry, separated from furniture placement.

Only library-local coordinates live here. Physical support is compiled by the
RelationAssembly in model.py; the seed keeps all 64 books reproducible.
"""
import random
from adsl.core import Asset, Cube, rotate_shape, translate_shape, shape_aabb

PALETTES=[(.16,.025,.026),(.035,.10,.073),(.045,.085,.13),(.24,.12,.045),
          (.31,.23,.105),(.18,.125,.085),(.115,.06,.075),(.30,.28,.21)]

def box(parent,name,size,center,color):
    return parent.attach_part(name,Cube(size,center=center,color=color))

def make_library():
    P=dict(shelf_tops=[0.,0.,0.,0.])
    rng=random.Random(20261017)
    manifest=[]
    books=Asset('Book library')
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
                             bounds=[lo.tolist(),hi.tolist()],support_z=top,supported_by=f'shelf_{row+1}'))
        return float(hi[0]),float(hi[2])

    def run(row,x,n,lean_last=0):
        for j in range(n):
            x,_=book(row,x,angle=lean_last if j==n-1 else 0)
            x+=rng.uniform(.0015,.004)
        return x

    def stack(row,left,n):
        z=P['shelf_tops'][row]
        previous=None
        for j in range(n):
            _,z=book(row,left+rng.uniform(0,.015),'stack',bottom=z,yaw=rng.uniform(-3.5,3.5),height=rng.uniform(.235,.27))
            if previous is not None: manifest[-1]['supported_by']=previous
            previous=manifest[-1]['id']

    # Curated clusters, alternating gaps and differently sized flat stacks.
    run(0,-.538,7,-12); stack(0,-.105,3); run(0,.232,6)
    stack(1,-.525,4); run(1,-.19,8); run(1,.286,4)
    run(2,-.538,6,-14); stack(2,-.16,3); run(2,.174,7)
    run(3,-.538,4); stack(3,-.318,3); run(3,.025,9,-10)

    return books,manifest
