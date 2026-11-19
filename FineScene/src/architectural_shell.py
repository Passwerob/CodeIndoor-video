"""Lower an Agent's rectangular architectural program to editable aDSL parts.

The Agent chooses the program, semantic bindings, materials and profiles. This
compiler computes nonoverlapping wall cells around the accepted spatial openings;
it does not choose a room inventory or furniture placement. Unsupported topology
continues through the general recipe path instead of silently approximating it.
"""
from __future__ import annotations

from typing import Literal
import ast
import numpy as np
from pydantic import Field
from general_planner import StrictModel, PartSpec, Scalar
from part_details import SurfaceSpec
from asset_physics import AssemblyContract, PartRelation, separating_gap, support_targets
from general_scene import scalar
from scene_spatial import SHELL_SLAB_THICKNESS_M


def _affine(value):
    """Compact generated linear expressions without losing parameter wiring."""
    if isinstance(value,(float,int)):return {'':float(value)}
    def read(node):
        if isinstance(node,ast.Constant):return {'':float(node.value)}
        if isinstance(node,ast.Name):return {node.id:1.}
        if isinstance(node,ast.UnaryOp):
            terms=read(node.operand)
            return {k:(-v if isinstance(node.op,ast.USub) else v) for k,v in terms.items()}
        if isinstance(node,ast.BinOp):
            a,b=read(node.left),read(node.right)
            if isinstance(node.op,(ast.Add,ast.Sub)):
                out=dict(a);sign=-1 if isinstance(node.op,ast.Sub) else 1
                for k,v in b.items():out[k]=out.get(k,0)+sign*v
                return out
            if isinstance(node.op,ast.Mult):
                if set(a)<={''}:return {k:v*a.get('',0) for k,v in b.items()}
                if set(b)<={''}:return {k:v*b.get('',0) for k,v in a.items()}
            if isinstance(node.op,ast.Div) and set(b)=={''}:return {k:v/b[''] for k,v in a.items()}
        raise ValueError('Architectural dimensions must be affine parameter expressions')
    return read(ast.parse(str(value),mode='eval').body)


def _arithmetic(a,b,op):
    left,right=_affine(a),_affine(b)
    if op=='*':
        if set(right)<={''}:terms={k:v*right.get('',0) for k,v in left.items()}
        elif set(left)<={''}:terms={k:v*left.get('',0) for k,v in right.items()}
        else:raise ValueError('Nonlinear architectural expression')
    else:
        terms=dict(left)
        for k,v in right.items():terms[k]=terms.get(k,0)+(-v if op=='-' else v)
    terms={k:v for k,v in terms.items() if abs(v)>1e-12}
    chunks=[(f'{v:.12g}*{k}' if k else f'{v:.12g}') for k,v in sorted(terms.items())]
    return '+'.join(chunks) or '0'


class WallSegmentProgram(StrictModel):
    part_id: str
    region: Literal['whole','low_side','high_side','below_opening','above_opening']


class WallProgram(StrictModel):
    wall: Literal['x_min','x_max','y_min','y_max']
    segments: list[WallSegmentProgram] = Field(min_length=1,max_length=4)
    skirting_id: str | None = None
    cornice_id: str | None = None
    panel_trim_id: str | None = None


class OpeningProgram(StrictModel):
    opening_id: str
    frame_id: str | None = None
    low_jamb_id: str | None = None
    high_jamb_id: str | None = None
    head_id: str | None = None
    leaf_id: str | None = None
    handle_id: str | None = None
    sill_id: str | None = None
    width: Scalar
    height: Scalar
    sill_height: Scalar


class ShellProgram(StrictModel):
    floor_id: str
    ceiling_id: str
    slab_thickness: float = Field(default=SHELL_SLAB_THICKNESS_M,
                                  ge=SHELL_SLAB_THICKNESS_M,le=SHELL_SLAB_THICKNESS_M)
    wall_thickness: Scalar
    ceiling_thickness: float = Field(default=.06,gt=.01,le=.2)
    skirting_height: float = Field(default=.14,gt=.03,le=.3)
    cornice_height: float = Field(default=.09,gt=.02,le=.25)
    trim_depth: float = Field(default=.025,gt=.004,le=.06)
    frame_width: float = Field(default=.045,gt=.02,le=.12)
    panel_spacing: float = Field(default=1.5,ge=.6,le=3)
    floor_surface: SurfaceSpec
    wall_surface: SurfaceSpec
    paint_color: tuple[float,float,float]
    floor_color: tuple[float,float,float]
    walls: list[WallProgram] = Field(min_length=4,max_length=4)
    openings: list[OpeningProgram] = Field(default_factory=list)


SHELL_PROMPT = """Return one ShellProgram JSON, no mesh coordinates and no prose.
Choose material/profile specifications and bind EVERY frozen internal_part ID to
its actual architectural function: floor, ceiling, and the wall cells around each opening.
For each wall, map each frozen wall-panel ID exactly once to region=whole when there is no
opening, or to low_side, high_side, below_opening and above_opening where those cells exist.
low_side/high_side follow increasing/decreasing local wall tangent coordinates: x-walls use Y,
y-walls use X. below/above refer to the opening's sill/head. Do not provide coordinates; the
compiler measures these cells from the accepted opening and room dimensions. For openings, bind
optional generic frame_id or explicit low_jamb_id, high_jamb_id and head_id, plus the parked
open door leaf, handle and window sill. Use null for absent features.
If a fitted window is a separate scene component, it supplies its own frame; leave frame_id null.
Use explicit frame member IDs when the frozen plan has separate jamb/head IDs; otherwise bind
one generic frame_id and let the compiler emit its members under that ID.
The occupied-floor slab thickness is fixed at 0.05 m to match the shell placement.
The compiler constructs wall voids, frame members, panel molding and exact joints. Do not use
wall-segment IDs for door leaves or duplicate a separate fitted window's frame.
Preserve the accepted spatial opening IDs, compass directions, width, height and
sill. Use declared dimension parameter expressions wherever they describe these
dimensions, e.g. door_width and window_height; the occupied-floor slab is fixed at 0.05 m.
Never introduce new parameters. w,d,h are the frozen outer shell envelope, bottom centered. Choose
actual available PBR texture IDs for floor and wall. The paint/floor colors are
RGB factors in [0,1]. Other rooms need different styles; do not invent trim IDs
or openings absent from the frozen component. No furniture is generated here.
"""


def shell_program_compatibility(component, spatial=None, outline=None):
    """Check whether frozen panel IDs can bind the compiler's wall cells."""
    panels={axis:[] for axis in 'xyz'}
    members={axis:[] for axis in 'xyz'}
    for part in component.internal_parts:
        form=getattr(part,'form',None)
        if part.role!='structure' or form is None:continue
        semantic=getattr(part,'semantic','').casefold()
        is_header=any(term in semantic for term in ('header','lintel','over opening'))
        # A lintel is still an aperture wall cell when attached to a pier.
        # Other attached members, notably an open door leaf, are hardware.
        if getattr(part,'attached_to',None) and not is_header:continue
        if form.kind=='panel':
            panels[form.axis].append(part.id)
            members[form.axis].append(part.id)
        elif form.kind=='beam' and is_header:
            # Beam axes follow wall tangents; an x beam belongs to a y wall and vice versa.
            wall_normal={'x':'y','y':'x'}.get(form.axis)
            if wall_normal:members[wall_normal].append(part.id)
    counts={wall:0 for wall in ('x_min','x_max','y_min','y_max')}
    for opening in getattr(spatial,'openings',[]) if spatial is not None else []:
        counts[opening.wall]=counts.get(opening.wall,0)+1
    unsupported_walls={wall:count for wall,count in counts.items() if count>1}
    try:
        dimensions=[scalar(v,outline.parameters if outline is not None else {})
                    for v in component.dimensions]
        top=dimensions[2]-.06
    except (AttributeError,IndexError,KeyError,TypeError,ValueError):
        top=None
    expected_by_wall={wall:1 for wall in counts}
    for opening in getattr(spatial,'openings',[]) if spatial is not None else []:
        if counts[opening.wall]>1:continue
        segments=2
        if opening.sill_height>1e-6:segments+=1
        if top is None or SHELL_SLAB_THICKNESS_M+opening.sill_height+opening.height<top-1e-6:
            segments+=1
        expected_by_wall[opening.wall]=segments
    expected={'x':expected_by_wall['x_min']+expected_by_wall['x_max'],
              'y':expected_by_wall['y_min']+expected_by_wall['y_max'],'z':2}
    unsupported_axes={axis:{'ids':ids,'expected':expected[axis]}
                      for axis,ids in members.items() if len(ids)!=expected[axis]}
    supported=not unsupported_walls and not unsupported_axes
    return {'supported':supported,'independent_structural_panels':panels,
            'independent_wall_members':members,
            'apertures_per_wall':counts,'expected_panel_counts':expected,
            'reason':('The structured compiler supports at most one aperture per wall and requires one frozen ID '
                       'per wall cell, with horizontal header beams counted along their wall tangent.' if not supported else
                       'Frozen panel IDs match compiler wall cells; attached door leaves are excluded.')}


def compile_shell(program, outline, component, spatial, placement):
    """Return a Recipe-shaped dict; ordinary recipe/assembly/edit gates still run."""
    if abs(placement.yaw_deg)%360 > 1e-6:
        raise ValueError('Rectangular shell compiler requires an axis-aligned enclosure')
    dims=[scalar(v,outline.parameters) for v in component.dimensions]
    params={**outline.parameters,**dict(zip(('w','d','h'),dims))}
    value=lambda x:scalar(x,params)
    # All expression arithmetic stays symbolic in the generated aDSL recipe.
    add=lambda a,b:_arithmetic(a,b,'+')
    sub=lambda a,b:_arithmetic(a,b,'-')
    mul=lambda a,b:_arithmetic(a,b,'*')
    half=lambda a:mul(a,.5)
    slab=program.slab_thickness;t=program.wall_thickness
    top=sub('h',program.ceiling_thickness)
    origins=[value(v) for v in placement.position]
    if max(abs(origins[0]),abs(origins[1]),abs(origins[2]+value(slab)))>.001:
        raise ValueError('Shell must be centered on its accepted boundary with slab top at world Z=0')
    if sorted(w.wall for w in program.walls)!=['x_max','x_min','y_max','y_min']:
        raise ValueError('Bind each perimeter wall exactly once')
    accepted={o.id:o for o in spatial.openings}
    if {o.opening_id for o in program.openings}!=set(accepted):
        raise ValueError('Architectural program must bind every accepted opening exactly once')
    opening_programs={o.opening_id:o for o in program.openings}
    for o in program.openings:
        expected=accepted[o.opening_id]
        if any(abs(value(expr)-number)>.001 for expr,number in
               ((o.width,expected.width),(o.height,expected.height),(o.sill_height,expected.sill_height))):
            raise ValueError(f'{o.opening_id}: dimensions differ from the frozen spatial aperture')
    intents={p.id:p for p in component.internal_parts}
    used={program.floor_id,program.ceiling_id}
    for wall in program.walls:
        used.update(segment.part_id for segment in wall.segments)
        used.update(i for i in (wall.skirting_id,wall.cornice_id,wall.panel_trim_id) if i)
    for opening in program.openings:
        used.update(i for i in (opening.frame_id,opening.low_jamb_id,opening.high_jamb_id,
                                opening.head_id,opening.leaf_id,opening.handle_id,opening.sill_id) if i)
        if opening.handle_id and not opening.leaf_id:
            raise ValueError(f'{opening.opening_id}: a door handle requires a parked door leaf')
    bindings=[program.floor_id,program.ceiling_id]
    bindings.extend(segment.part_id for wall in program.walls for segment in wall.segments)
    bindings.extend(i for opening in program.openings for i in
                    (opening.frame_id,opening.low_jamb_id,opening.high_jamb_id,opening.head_id,
                     opening.leaf_id,opening.handle_id,opening.sill_id) if i)
    bindings.extend(i for wall in program.walls for i in
                    (wall.skirting_id,wall.cornice_id,wall.panel_trim_id) if i)
    if len(bindings)!=len(set(bindings)):
        raise ValueError('Architectural bindings must use each frozen ID only once')
    if used!=set(intents):
        raise ValueError(f'Architectural bindings must cover frozen IDs exactly; missing={sorted(set(intents)-used)}, unknown={sorted(used-set(intents))}')

    parts=[];records={};counts={}
    def box(binding,lo,hi,*,surface=None,color=None):
        n=counts.get(binding,0);counts[binding]=n+1
        id=binding if n==0 else f'{binding}_{n:03d}'
        if len(id)>64:raise ValueError('Architectural member ID exceeds 64 characters')
        size=tuple(sub(b,a) for a,b in zip(lo,hi));center=tuple(half(add(a,b)) for a,b in zip(lo,hi))
        numeric=np.array([[value(x) for x in lo],[value(x) for x in hi]])
        if np.any(numeric[1]-numeric[0]<=.0001):raise ValueError(f'{id}: invalid architectural member thickness')
        intent=intents.get(binding)
        parts.append(PartSpec(id=id,primitive='box',size=size,center=center,
            semantic=(intent.semantic if intent else binding)+(' member '+str(n) if n else ''),
            rigidity=intent.rigidity if intent else 'parametric',
            form=intent.form if intent and n==0 else None,
            surface=surface,color=color or program.paint_color))
        records[id]={'primitive':'box','bounds':numeric.tolist(),
            'size':(numeric[1]-numeric[0]).tolist(),'center':numeric.mean(axis=0).tolist(),
            'rotation':np.eye(3).tolist()}
        return id
    box(program.floor_id,[mul('w',-.5),mul('d',-.5),0],[half('w'),half('d'),slab],
        surface=program.floor_surface,color=program.floor_color)
    box(program.ceiling_id,[mul('w',-.5),mul('d',-.5),top],[half('w'),half('d'),'h'])

    for wall in program.walls:
        axis=0 if wall.wall.startswith('x') else 1;along=1-axis
        sign=-1 if wall.wall.endswith('min') else 1
        span='d' if axis==0 else 'w';normal='w' if axis==0 else 'd'
        # North/south own corner blocks; east/west meet their inner faces.
        start=add(mul(span,-.5),t) if axis==0 else mul(span,-.5)
        end=sub(half(span),t) if axis==0 else half(span)
        face=mul(sub(half(normal),t),sign)
        outside=mul(half(normal),sign)
        normal_lo,normal_hi=(outside,face) if sign<0 else (face,outside)
        def wall_box(id,a,b,z0,z1,depth=None):
            lo=[0,0,z0];hi=[0,0,z1];lo[along]=a;hi[along]=b
            if depth is None:lo[axis],hi[axis]=normal_lo,normal_hi
            elif sign<0:lo[axis],hi[axis]=face,add(face,depth)
            else:lo[axis],hi[axis]=sub(face,depth),face
            return box(id,lo,hi,surface=program.wall_surface if depth is None else None)
        windows=[o for o in spatial.openings if o.wall==wall.wall]
        if len(windows)>1:
            raise ValueError(f'{wall.wall}: structured wall segmentation supports at most one aperture')
        apertures=[];parked=[]
        for opening in windows:
            op=opening_programs[opening.id]
            a=sub(opening.offset,half(op.width));b=add(opening.offset,half(op.width))
            z0=add(slab,op.sill_height);z1=add(z0,op.height)
            if value(a)<=value(start) or value(b)>=value(end) or value(z1)>=value(top):
                raise ValueError(f'{opening.id}: aperture must lie inside the actual perimeter wall')
            apertures.append((a,b,z0,z1,opening,op))
            if op.leaf_id:
                # Park against a real adjacent wall pier; never in the clear opening.
                left=sub(a,program.frame_width);right=add(b,program.frame_width)
                if value(left)-value(start)>=value(op.width)+.03:parked.append((sub(left,op.width),left,op))
                elif value(end)-value(right)>=value(op.width)+.03:parked.append((right,add(right,op.width),op))
                else:raise ValueError(f'{opening.id}: no adjacent wall pier can park the open leaf')
        cells=[]
        if not apertures:
            cells.append(('whole',start,end,slab,top))
        else:
            a,b,z0,z1,_,_=apertures[0]
            if value(a)>value(start)+1e-6:cells.append(('low_side',start,a,slab,top))
            if value(b)<value(end)-1e-6:cells.append(('high_side',b,end,slab,top))
            if value(z0)>value(slab)+1e-6:cells.append(('below_opening',a,b,slab,z0))
            if value(z1)<value(top)-1e-6:cells.append(('above_opening',a,b,z1,top))
        segment_ids={segment.region:segment.part_id for segment in wall.segments}
        regions=[cell[0] for cell in cells]
        if len(regions)!=len(set(regions)) or set(regions)!=set(segment_ids):
            raise ValueError(f'{wall.wall}: frozen panel IDs must map exactly to wall cells {regions}')
        for region,a,b,z0,z1 in cells:
            member=wall_box(segment_ids[region],a,b,z0,z1)
            intent=intents.get(segment_ids[region])
            if (intent and intent.form and intent.form.kind=='beam' and
                    intent.form.axis in {'x','y'} and region in {'above_opening','below_opening'}):
                part=next(item for item in parts if item.id==member)
                if part.form!=intent.form:
                    part.form=intent.form
        for region,a,b,z0,z1 in cells:
            # Finish members occupy the interior face, never the orthogonal
            # wall's corner volume. The tiny end reveal also avoids trim joins
            # overlapping at a ninety-degree corner.
            inner_start=add(mul(sub(span,mul(t,2)),-.5),program.trim_depth)
            inner_end=sub(half(sub(span,mul(t,2))),program.trim_depth)
            da=inner_start if value(a)<value(inner_start) else a
            db=inner_end if value(b)>value(inner_end) else b
            if value(db)<=value(da):continue
            if wall.skirting_id and abs(value(z0)-value(slab))<1e-6:
                segments=[(da,db)]
                reserved=[(sub(oa,program.frame_width),add(ob,program.frame_width))
                          for oa,ob,oz,_,_,_ in apertures if value(oz)-value(slab)<program.skirting_height]
                reserved.extend((sub(pa,.01),add(pb,.01)) for pa,pb,_ in parked)
                for ra,rb in reserved:
                    next_segments=[]
                    for sa,sb in segments:
                        if value(rb)<=value(sa) or value(ra)>=value(sb):next_segments.append((sa,sb));continue
                        if value(ra)>value(sa)+.001:next_segments.append((sa,ra))
                        if value(rb)<value(sb)-.001:next_segments.append((rb,sb))
                    segments=next_segments
                for sa,sb in segments:
                    wall_box(wall.skirting_id,sa,sb,slab,add(slab,program.skirting_height),program.trim_depth)
            if wall.cornice_id and abs(value(z1)-value(top))<1e-6:
                wall_box(wall.cornice_id,da,db,sub(top,program.cornice_height),top,program.trim_depth)
            if not wall.panel_trim_id:continue
            # Raised rectangular moldings are laid only on solid cells, with no
            # crossing members and a reserved pier for a parked door leaf.
            if any(value(a)<value(pb)+.06 and value(b)>value(pa)-.06 for pa,pb,_ in parked):continue
            a,b=da,db
            length=value(b)-value(a);height=value(z1)-value(z0)
            if length<.55 or height<.6:continue
            count=max(1,int(length/program.panel_spacing))
            pitch=mul(sub(b,a),1/count)
            for n in range(count):
                l=add(add(a,mul(pitch,n)),.14);r=sub(add(a,mul(pitch,n+1)),.14)
                bottom=add(z0,.23);upper=sub(z1,.2);strip=.026
                if value(upper)-value(bottom)<.2:continue
                wall_box(wall.panel_trim_id,l,add(l,strip),bottom,upper,.014)
                wall_box(wall.panel_trim_id,sub(r,strip),r,bottom,upper,.014)
                wall_box(wall.panel_trim_id,add(l,strip),sub(r,strip),bottom,add(bottom,strip),.014)
                wall_box(wall.panel_trim_id,add(l,strip),sub(r,strip),sub(upper,strip),upper,.014)
        fw=program.frame_width
        for a,b,z0,z1,opening,op in apertures:
            if op.frame_id and any((op.low_jamb_id,op.high_jamb_id,op.head_id)):
                raise ValueError(f'{opening.id}: use either generic frame_id or explicit frame member IDs')
            low_id=op.low_jamb_id or op.frame_id
            high_id=op.high_jamb_id or op.frame_id
            head_id=op.head_id or op.frame_id
            if low_id:wall_box(low_id,sub(a,fw),a,z0,z1,program.trim_depth)
            if high_id:wall_box(high_id,b,add(b,fw),z0,z1,program.trim_depth)
            if head_id:wall_box(head_id,sub(a,fw),add(b,fw),z1,add(z1,fw),program.trim_depth)
            if op.sill_id:wall_box(op.sill_id,sub(a,fw),add(b,fw),sub(z0,fw),z0,max(.1,program.trim_depth))
        for a,b,op in parked:
            z0=add(slab,.015);z1=add(z0,sub(op.height,.03))
            wall_box(op.leaf_id,a,b,z0,z1,.045)
            if op.handle_id:
                center=half(add(a,b));handle_z=add(slab,1.02)
                tangent_a=sub(center,.06);tangent_b=add(center,.06)
                if sign<0:
                    handle_lo=add(face,.045);handle_hi=add(face,.085)
                else:
                    handle_hi=sub(face,.045);handle_lo=sub(face,.085)
                lo=[0,0,sub(handle_z,.018)];hi=[0,0,add(handle_z,.018)]
                lo[along]=tangent_a;hi[along]=tangent_b
                lo[axis]=handle_lo;hi[axis]=handle_hi
                box(op.handle_id,lo,hi)

    # A split molding/wall is represented by several physical members. Bind its
    # frozen functional proportion to the largest suitable member rather than
    # whichever grid cell happened to be traversed first.
    for binding,intent in intents.items():
        if intent.form is None or binding not in records:continue
        members=[p for p in parts if p.id==binding or (p.id.startswith(binding+'_') and p.id[len(binding)+1:].isdigit())]
        axis='xyz'.index(intent.form.axis)
        def score(part):
            size=np.array(records[part.id]['size']);other=np.delete(size,axis)
            return float(np.min(other)/size[axis] if intent.form.kind=='panel' else size[axis]/np.max(other))
        best=max(members,key=score);first=next(p for p in members if p.id==binding)
        if best is not first:
            previous=best.id
            best.id,first.id=binding,previous
            records[binding],records[previous]=records[previous],records[binding]
            first.form=None;best.form=intent.form
    if not set(intents)<={p.id for p in parts}:
        raise ValueError('Not enough solid wall area to realize every specified architectural detail')
    # Derive exact physical joints from the generated members, never an invented
    # relation between separated/overlapping parts. Normal validation rechecks it.
    relations=[];items=list(records.items())
    for i,(a,ra) in enumerate(items):
        for b,rb in items[i+1:]:
            gap=separating_gap(ra,rb)
            if gap<-.0005:raise ValueError(f'Architectural compiler intersection: {a}/{b}, {-gap:.4f}m')
            if abs(gap)<.00001:
                relations.append(PartRelation(kind='attached_to',part=a,target=b,
                    reason='Exact shared face/edge in the Agent architectural program'))
    # Preserve explicit frozen interfaces as the authoritative assembly edges.
    declared={p.id:p for p in component.internal_parts}
    interfaces={id for id,p in declared.items() if support_targets(p) or p.attached_to}
    normalized=[]
    for relation in relations:
        if relation.part not in interfaces:
            normalized.append(relation)
        elif relation.target not in interfaces and relation.target!=program.floor_id:
            # The all-pairs pass chooses an arbitrary orientation. For a
            # generated continuation/frame touching a frozen root member,
            # orient the edge from the continuation back to the frozen member.
            normalized.append(PartRelation(kind='attached_to',part=relation.target,target=relation.part,
                reason='Exact shared face/edge to a frozen architectural member'))
    relations=normalized
    for part in component.internal_parts:
        if part.id not in records:continue
        targets=support_targets(part)
        pairs=[('supported_by',target) for target in targets]
        if not pairs and part.attached_to:
            pairs=[('attached_to',part.attached_to)]
        for kind,target in pairs:
            if target not in records:continue
            gap=separating_gap(records[part.id],records[target])
            if abs(gap)>.00001:
                raise ValueError(f'Frozen shell interface {part.id}->{target} is not a physical contact: {gap:.6f}m')
            relations.append(PartRelation(kind=kind,
                part=part.id,target=target,reason='Frozen architectural interface from the semantic shell plan'))
    unique={}
    for relation in relations:
        unique[(relation.kind,relation.part,relation.target)]=relation
    relations=list(unique.values())
    if len(relations)>640:raise ValueError('Architectural program exceeds supported assembly relation count')
    return {'parts':[p.model_dump(mode='json') for p in parts],
        'assembly':AssemblyContract(roots=[program.floor_id],relations=relations).model_dump(mode='json')}
