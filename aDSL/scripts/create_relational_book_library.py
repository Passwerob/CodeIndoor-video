"""One-time provenance-preserving extraction of v1's book geometry only."""
from pathlib import Path
import textwrap

root=Path(__file__).resolve().parents[1]
old=(root/'examples/counterexamples/vintage_bookshelf_v1/model.py').read_text(encoding='utf-8')
palette=old[old.index('PALETTES='):old.index('scene=Asset')]
body=old[old.index('def book('):old.index('\ndef validate():')]
body=body.replace("support_z=top))", "support_z=top,supported_by=f'shelf_{row+1}'))")
body=body.replace("    z=P['shelf_tops'][row]\n    for j", "    z=P['shelf_tops'][row]\n    previous=None\n    for j")
needle="        _,z=book(row,left+rng.uniform(0,.015),'stack',bottom=z,yaw=rng.uniform(-3.5,3.5),height=rng.uniform(.235,.27))"
assert needle in body
body=body.replace(needle,needle+"\n        if previous is not None: manifest[-1]['supported_by']=previous\n        previous=manifest[-1]['id']")
source='''"""Copied v1 book geometry, separated from furniture placement.

Only library-local coordinates live here. Physical support is compiled by the
RelationAssembly in model.py; the seed keeps all 64 books reproducible.
"""
import random
from adsl.core import Asset, Cube, rotate_shape, translate_shape, shape_aabb

'''+palette+'''
def box(parent,name,size,center,color):
    return parent.attach_part(name,Cube(size,center=center,color=color))

def make_library():
    P=dict(shelf_tops=[0.,0.,0.,0.])
    rng=random.Random(20261017)
    manifest=[]
    books=Asset('Book library')
'''+textwrap.indent(body,'    ')+"\n    return books,manifest\n"
(root/'examples/physical_bookshelf/book_library.py').write_text(source,encoding='utf-8')
