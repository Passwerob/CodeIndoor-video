"""Compile Flash's measured shelf/book design into one editable aDSL asset.

This is a targeted component compiler demonstration, not evidence that the
unrestricted staged scene planner has completed arbitrary-scene generation.
"""
import argparse
import json
from pathlib import Path
import sys
import math

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'FineScene/src'))
from adsl_components import BOOK_PALETTE
from general_planner import PlannerScenePlan,to_general_plan
from general_scene import compile_general,edit_general
from scene_execution import evaluate_candidate,save


def make_plan(design):
    w,d=design['width'],design['depth']
    gaps=design['clearances'];n=len(gaps);t=.027;base=.12
    if n!=5 or any(not .32<=g<=.4 for g in gaps):raise ValueError('Invalid shelf clearances')
    parameters={f'gap_{i}':v for i,v in enumerate(gaps)}
    top=lambda i:'.12'+''.join(f'+gap_{j}+.027' for j in range(i))
    height=top(n-1)+f'+gap_{n-1}+.045'
    wood=design['wood_color']
    parts=[];relations=[]
    def board(id,size,center,**extra):
        parts.append(dict(id=id,primitive='box',size=size,center=center,semantic=id.replace('_',' '),
                          color=wood,finish='wood',rigidity='parametric',**extra))
    def relation(kind,a,b,**extra):
        relations.append(dict(kind=kind,part=a,target=b,reason='Measured furniture assembly interface',**extra))
    board('plinth',['w','d',base],[0,0,base/2])
    board('crown',['w','d-.012',.045],[0,.006,'h-.0225'])
    for side,sign in [('left',-1),('right',1)]:
        board(side,[.04,'d-.04','h-.165'],[f'{sign}*(w/2-.04)',0,0],
              fit_between=[dict(axis='z',start='plinth',end='crown')])
        relation('attached_to',side,'plinth');relation('attached_to',side,'crown')
    board('back',['w-.12',.018,'h-.165'],[0,'d/2-.029',0],
          fit_between=[dict(axis='z',start='plinth',end='crown')])
    for target in ('plinth','crown','left','right'):relation('attached_to','back',target)
    for i in range(1,n):
        board(f'shelf_{i}',['w-.12','d-.058',t],[0,-.009,top(i)+'-.0135'])
        for target in ('left','right','back'):relation('attached_to',f'shelf_{i}',target)
    for side,sign in [('left',-1),('right',1)]:
        board(f'front_stile_{side}',[.05,.015,'h-.165'],[f'{sign}*(w/2-.04)','-d/2+.0125',0],
              fit_between=[dict(axis='z',start='plinth',end='crown')])
        relation('attached_to',f'front_stile_{side}',side)
    # Crown face moulding is fastened to the top slab, never floating decoration.
    board('crown_lip',['w',.012,.025],[0,'-d/2+.006','h-.0125'])
    relation('attached_to','crown_lip','crown')
    book_count=0
    for level,entry in enumerate(design['levels']):
        support='plinth' if level==0 else f'shelf_{level}'
        ceiling='crown' if level==n-1 else f'shelf_{level+1}'
        groups=entry['groups']
        widths=[sum(b[0]+.001 for b in g['books']) if g['kind']=='upright' else max(b[0] for b in g['books']) for g in groups]
        used=sum(widths)+sum(g['gap_after'] for g in groups[:-1])
        available=w-.14
        if used>available:raise ValueError(f'Level {level}: book groups overflow {used} > {available}')
        spare=available-used
        # Alternate alignment of complete groups. Book sizes never scale to fill a bay.
        cursor=-w/2+.07+(spare*(.15 if level%2==0 else .75))
        for gi,(group,width) in enumerate(zip(groups,widths)):
            previous=None
            for bi,(bw,bd,bh,palette) in enumerate(group['books']):
                if not 0<=palette<=7:raise ValueError('Palette outside supported range')
                id=f'book_L{level+1}_G{gi+1}_{bi+1:02d}'
                flat=group['kind']=='stack'
                if not flat and bh>gaps[level]-.025:raise ValueError('Insufficient book headroom')
                x=cursor+width/2 if flat else cursor+bw/2
                # Variable front setback gives visible depth without random collisions.
                y=-d/2+.048+bd/2+(.003*((bi+2*gi+level)%4) if not flat else 0)
                parent=previous if flat and previous else support
                offset=[0,0,0] if flat and previous else [round(x,6),round(y,6)+(0 if level==0 else .009),0]
                parts.append(dict(id=id,primitive='book',semantic=f'{"Horizontal" if flat else "Upright"} bound book on level {level+1}',
                    size=[bw,bd,bh],center=offset,color=BOOK_PALETTE[palette],book={'orientation':'flat' if flat else 'upright','palette_index':palette},
                    parent=parent,mount='top',rigidity='rigid'))
                relation('supported_by',id,parent)
                relation('clearance',id,ceiling,minimum=.02)
                if flat:previous=id
                else:cursor+=bw+.001
                book_count+=1
            cursor+=(width if group['kind']=='stack' else 0)+group['gap_after']
    component=dict(id='bookshelf',semantic='Full-height walnut bookcase with five occupied levels and detailed bound books',category='furniture',
        dimensions=['w_cab','d_cab',height],parts=parts,assembly={'roots':['plinth'],'relations':relations},
        rigidity='parametric',grounded=True,attachment='floor',
        decomposition_reason='One furniture asset; rigid books have explicit shelf support and headroom, carcass follows shelf clearances.',
        required_features=['five occupied levels',f'{book_count} individually editable detailed books','upright and stacked groups','back panel, crown, plinth and stiles'],
        material_requirements=['directional walnut grain','matte cloth covers and paper edges'],
        source={'mode':'parametric','reason':'Editable planar cabinet structure and reusable detailed book subassemblies.','quality_target':'realistic'})
    return PlannerScenePlan.model_validate(dict(intent={'scene_id':'flash_walnut_bookshelf','description':component['semantic'],
        'style':'restrained traditional walnut furniture','quality_target':'realistic','extent':[4,4,4]},
        parameters={**parameters,'w_cab':w,'d_cab':d},components=[component],
        edit_checks=[{'parameter':'gap_1','delta':.1,'intent':'Raise upper shelves and their rigid books by 10cm; stretch side/back members only.'}],
        assumptions=['Flash supplies dimensions, book inventory and group layout; this targeted compiler supplies cabinet joinery and placement rules.',
                     'Geometric support is checked; friction, mass and structural strength are not simulated.']))


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--render',action='store_true');args=parser.parse_args()
    root=ROOT/'FineScene/runs/flash_bookshelf_20261020_01'
    design=json.loads((root/'repaired_design.json').read_text(encoding='utf-8'))
    save(root/'selected_design.json',{'design':design,'selection_note':'72 books exceeds the advisory 50-65 inventory target; retained because user requested richer books. Geometric requirements remain mandatory.'})
    plan=make_plan(design)
    save(root/'model_plan.json',{'coordinate_frame':{ 'up':'+Z','front':'-Y','right':'+X','units':'metres'},
        'parameters':plan.parameters,'parts':[p.model_dump() for p in plan.components[0].parts],
        'relations':plan.components[0].assembly.model_dump(),'build_order':['plinth','carcass and back','shelves','rigid book groups','trim'],
        'checks':['all parts connected','contact and nonpenetration','book headroom','10cm edit preserves books','front/overview/reverse/top renders']})
    save(root/'compiled_planner_plan.json',plan.model_dump(mode='json',by_alias=True))
    executable,_=to_general_plan(plan)
    scene,_,before=compile_general(executable)
    changed=edit_general(executable,'gap_1',.1)
    _,_,after=compile_general(changed)
    a=before['bookshelf']['internal_geometry']['parts'];b=after['bookshelf']['internal_geometry']['parts']
    save(root/'edit_verification.json',{'before_status':before['bookshelf']['internal_geometry']['assembly']['status'],
        'after_status':after['bookshelf']['internal_geometry']['assembly']['status'],
        'parameter':'gap_1','delta':.1,'primitive_count':len(list(scene.iter_primitives())),
        'books':[{'id':p.id,'unchanged_size':a[p.id]['size']==b[p.id]['size'],
                  'translation':[round(y-x,8) for x,y in zip(a[p.id]['center'],b[p.id]['center'])]} for p in executable.objects[0].parts if p.primitive=='book']})
    if args.render:
        result=evaluate_candidate(plan,{},root/'candidate_01','A realistic full-height walnut bookcase, five occupied shelves, rich varied books and editable support relationships.',ROOT/'FineScene/configs/deepseek-flash.yaml')
        save(root/'candidate_01/evaluation.json',result)
        print(json.dumps({'accepted':result['accepted'],'geometry':result['geometry']['status'],'visual':result['visual']['status']}))
    else:print('Compiled and edit checked',len(executable.objects[0].parts),'semantic parts')


if __name__=='__main__':main()
