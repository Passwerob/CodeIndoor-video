"""Declared assembly constraints checked against actual aDSL part geometry.

This is geometric assembly consistency, not a dynamics/material-strength solver.
Boxes use an oriented-box separating-axis check. Circular sweeps (tapered ones as
conservative capsule pieces) and lathe solids use measured surfaces against
boxes/circular cylinders/sweeps/lathes (see sweep_contacts); other curved parts use
conservative oriented envelopes. Undeclared multi-part assemblies fail closed.
"""
from __future__ import annotations

import itertools
from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field


def support_targets(part):
    """Normalize a part's single-or-multi support field to an ordered list.

    Accepts the pydantic intent, any duck-typed part carrying .support, or a
    plain string/list. Consumers must use this rather than reading .support,
    because a straddling member legitimately names several supports.
    """
    value = part if isinstance(part, (str, list, tuple)) else getattr(part, 'support', None)
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    return [str(item) for item in value if item]


class PartRelation(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    kind: Literal['supported_by', 'attached_to', 'clearance']
    part: str
    target: str
    minimum: float = Field(default=0, ge=0, le=10)
    max_penetration: float = Field(default=.002, ge=0, le=.03)
    reason: str = Field(min_length=1)


class AssemblyContract(BaseModel):
    model_config = ConfigDict(extra='forbid')
    roots: list[str] = Field(min_length=1)
    relations: list[PartRelation] = Field(default_factory=list, max_length=640)
    # Declared mirror symmetry of the whole compiled object (local YZ plane = 'x',
    # XZ plane = 'y', both = 'xy'); every part needs a matching mirror partner.
    symmetry: Literal['none','x','y','xy'] = 'none'


class FitSpan(BaseModel):
    """Fit a parametric box between two measured faces along one asset axis."""
    model_config = ConfigDict(extra='forbid',allow_inf_nan=False)
    axis: Literal['x','y','z']
    start: str
    start_face: Literal['min','max'] = 'max'
    end: str
    end_face: Literal['min','max'] = 'min'
    inset: float = Field(default=0,ge=0,le=1)


class PartForm(BaseModel):
    """Functional proportions in the asset frame, frozen before recipe creation."""
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    kind: Literal['panel', 'beam']
    axis: Literal['x', 'y', 'z']
    minimum_ratio: float = Field(default=2, ge=1.1, le=100)


def audit_part_form(part, record):
    form=part.form
    if form is None:return None
    axis='xyz'.index(form.axis)
    rotation=np.asarray(record['rotation'])
    # A world AABB alone cannot prove a panel normal: inspect the actual OBB.
    aligned=int(np.argmax(np.abs(rotation[axis])))
    if abs(rotation[axis,aligned])<1-1e-5:
        return f'{form.kind} {form.axis} axis is not aligned with the intended asset-frame direction.'
    size=np.asarray(record['size'])
    other=np.delete(size,aligned)
    ratio=(float(np.min(other)/size[aligned]) if form.kind=='panel'
           else float(size[aligned]/np.max(other)))
    if ratio+1e-6<form.minimum_ratio:
        meaning='thickness normal' if form.kind=='panel' else 'length direction'
        return (f'{form.kind} {meaning}={form.axis}, measured cross-section ratio={ratio:.3f}, '
                f'required >= {form.minimum_ratio:.3f}; actual local XYZ size={size.tolist()}. '
                'Contact alone does not establish the required functional orientation.')
    return None


def rotation_matrix(angles):
    matrix=np.eye(3)
    for i,degree in enumerate(angles):
        a=np.deg2rad(degree);c,s=np.cos(a),np.sin(a)
        step=(np.array([[1,0,0],[0,c,-s],[0,s,c]]),
              np.array([[c,0,s],[0,1,0],[-s,0,c]]),
              np.array([[c,-s,0],[s,c,0],[0,0,1]]))[i]
        matrix=step@matrix
    return matrix


def corners(record):
    signs=np.array(list(itertools.product((-1,1),repeat=3)))
    return signs*np.array(record['size'])/2@np.array(record['rotation']).T+record['center']


def separating_gap(a,b):
    """Positive means separated; negative is minimum SAT overlap depth."""
    from sweep_contacts import sweep_gap
    measured=sweep_gap(a,b)
    if measured is not None:return measured
    ra,rb=np.array(a['rotation']),np.array(b['rotation'])
    axes=[*ra.T,*rb.T,*[np.cross(x,y) for x in ra.T for y in rb.T]]
    displacement=np.array(b['center'])-a['center']
    gaps=[]
    for axis in axes:
        norm=np.linalg.norm(axis)
        if norm<1e-8:continue
        axis=axis/norm
        radii=sum(np.dot(np.abs(r.T@axis),np.array(record['size'])/2)
                  for r,record in ((ra,a),(rb,b)))
        gaps.append(abs(np.dot(displacement,axis))-radii)
    return max(gaps)


def flat_cap_attachment(a,b,tolerance=.002):
    """Verify a cylinder cap against a box face or contained opposing circular cap."""
    if a['primitive']==b['primitive']=='cylinder':
        sa,sb=np.asarray(a['size']),np.asarray(b['size'])
        if not (np.isclose(sa[0],sa[1],rtol=1e-9,atol=1e-12)
                and np.isclose(sb[0],sb[1],rtol=1e-9,atol=1e-12)):
            return False
        za,zb=np.asarray(a['rotation'])[:,2],np.asarray(b['rotation'])[:,2]
        alignment=float(za@zb)
        if abs(alignment)<1-1e-6:return False
        for ea,eb in itertools.product((-1,1),repeat=2):
            if ea*eb*alignment>-1+1e-6:continue
            ca=np.asarray(a['center'])+ea*za*sa[2]/2
            cb=np.asarray(b['center'])+eb*zb*sb[2]/2
            delta=ca-cb;normal=float(delta@za)
            if abs(normal)>tolerance:continue
            offset=float(np.linalg.norm(delta-normal*za))
            if offset+min(sa[0],sb[0])/2<=max(sa[0],sb[0])/2+.001:
                return True
        return False
    if a['primitive']=='box' and b['primitive']=='cylinder':a,b=b,a
    if a['primitive']!='cylinder' or b['primitive']!='box':return False
    ra,rb=np.array(a['rotation']),np.array(b['rotation'])
    radius=np.array(a['size'])[:2]/2;half=np.array(b['size'])/2
    for axis in range(3):
        if abs(np.dot(ra[:,2],rb[:,axis]))<1-1e-6:continue
        for end in (-1,1):
            cap=np.array(a['center'])+end*ra[:,2]*a['size'][2]/2
            local=(cap-b['center'])@rb
            if abs(abs(local[axis])-half[axis])>tolerance:continue
            tangents=[i for i in range(3) if i!=axis]
            if all(abs(local[i])+np.linalg.norm(radius*(ra[:,:2].T@rb[:,i]))<=half[i]+.001 for i in tangents):
                return True
    return False


def child_span(record):
    """Horizontal XY extent of a record's oriented corners."""
    c=corners(record)[:,:2]
    return [float(v) for v in (c.max(axis=0)-c.min(axis=0))]


def support_rect(record):
    """Axis-aligned XY rectangle of a support's top face in world space."""
    rb=np.array(record['rotation'])
    c=(corners(record)-record['center'])@rb
    half=np.array(record['size'])[:2]/2
    local=np.array([[-half[0],-half[1]],[half[0],-half[1]],[-half[0],half[1]],[half[0],half[1]]])
    world=(local@rb[:2,:2].T)+np.asarray(record['center'],dtype=float)[:2]
    return world.min(axis=0),world.max(axis=0)


def union_coverage(child, entries):
    """Unbalanced overhang, per XY axis, of a child past its supports' combined top.

    Sampled as a 2D grid so two supports meeting under a straddling member count
    as coverage of the span between them, while a member carried by only one of
    its declared supports does not. Each axis reports how much further the union
    stops from one end than from the other: even overhang on both sides is an
    ordinary silhouette overhang, a cantilevered end is not carried at all.
    """
    lo,hi=support_rect(child)
    lo=np.asarray(lo,dtype=float);hi=np.asarray(hi,dtype=float)
    grid=[np.linspace(lo[axis],hi[axis],41) if hi[axis]-lo[axis]>1e-9 else np.array([float(lo[axis])])
          for axis in (0,1)]
    hit=np.zeros((len(grid[0]),len(grid[1])),dtype=bool)
    for entry in entries:
        slo,shi=entry['rect']
        inside=((grid[0][:,None]>=slo[0]-.001)&(grid[0][:,None]<=shi[0]+.001)&
                (grid[1][None,:]>=slo[1]-.001)&(grid[1][None,:]<=shi[1]+.001))
        hit|=inside
    # A sample only counts when it lies in a covered row AND a covered column;
    # two supports clipping opposite corners would otherwise look continuous.
    hit&=hit.any(axis=1)[:,None]&hit.any(axis=0)[None,:]
    balance=[]
    for axis in (0,1):
        column=hit.any(axis=1-axis)
        if not column.any():
            balance.append(0.0);continue
        # What matters is how far the union reaches past the carried span towards
        # each end. Even overhang on both sides is normal clear geometry; a
        # cantilevered end is what a member cannot carry.
        far=float(hi[axis]-grid[axis][column].max())
        near=float(grid[axis][column].min()-lo[axis])
        balance.append(max(0.0,far-near))
    return [float(v) for v in balance]


def audit_assembly(obj, records):
    """records contain measured bounds plus evaluated primitive transforms."""
    findings=[]
    def native(value):
        """Round-trip through JSON so numpy scalars never reach the serializer."""
        if value is None or isinstance(value,(bool,int,float,str)):
            return value
        if isinstance(value,dict):
            return {str(k):native(v) for k,v in value.items()}
        if isinstance(value,(list,tuple)):
            return [native(v) for v in value]
        if hasattr(value,'item'):
            return value.item()
        return str(value)

    def fail(ids,code,evidence,measured=None,expected=None):
        findings.append({'component_ids':[obj.id],'part_ids':ids,'code':code,
                         'repair_target':'geometry','evidence':evidence,
                         'measured':native(measured),'expected':native(expected)})
    ids={p.id for p in obj.parts}
    if not ids:
        return {'status':'not_applicable','findings':[], 'limits':'External mesh internals require separate asset QA.'}
    contract=obj.assembly
    if contract is None:
        if len(ids)>1:
            fail(sorted(ids),'missing_contract','Multi-part aDSL asset requires roots and physical part relations.')
            return {'status':'needs_revision','findings':findings}
        contract=AssemblyContract(roots=list(ids))
    roots=set(contract.roots)
    if len(roots)!=len(contract.roots) or not roots<=ids:
        fail(sorted(roots),'invalid_roots','Assembly roots must be unique existing part IDs.')
    if set(records)!=ids:
        fail(sorted(ids-set(records)),'missing_parts','Measured part set differs from recipe IDs.')
        return {'status':'needs_revision','findings':findings}
    for part in obj.parts:
        error=audit_part_form(part,records[part.id])
        if error:fail([part.id],'part_form',error)
    edges={i:set() for i in ids};physical_edges={i:set() for i in ids}
    joints={};supports=set();seen=set()
    support_footprint_failures={}
    for relation in contract.relations:
        a,b=relation.part,relation.target
        if a==b or a not in ids or b not in ids:
            fail([a,b],'invalid_relation','Part relation needs two distinct existing IDs.');continue
        key=(relation.kind,a,b)
        if key in seen:fail([a,b],'duplicate_relation','Duplicate part relation.')
        seen.add(key)
        al,ah=np.array(records[a]['bounds']);bl,bh=np.array(records[b]['bounds'])
        if relation.kind=='clearance':
            distance=np.linalg.norm(np.maximum(0,np.maximum(al-bh,bl-ah)))
            if distance+.0005<relation.minimum:
                fail([a,b],'clearance',f'Part clearance {distance:.4f}m is below {relation.minimum:.4f}m.')
            continue
        gap=separating_gap(records[a],records[b])
        valid=True
        if relation.kind=='supported_by':
            supports.add((a,b))
            if abs(al[2]-bh[2])>.002:
                gap=al[2]-bh[2]
                # A part spanning wider than its support cannot be fixed by gap
                # alone; report span so the failure is not mistaken for a small slide.
                span=float(max(ah[0]-al[0],ah[1]-al[1]))
                support_span=float(max(bh[0]-bl[0],bh[1]-bl[1]))
                fail([a,b],'support_gap',f'Bottom/top gap is {gap:.4f}m.',
                     measured={'part':a,'support':b,'gap_m':round(gap,4),
                               'part_bottom_z':round(float(al[2]),4),
                               'support_top_z':round(float(bh[2]),4),
                               'part_span_m':round(span,4),'support_span_m':round(support_span,4),
                               'overhang_m':round(span-support_span,4),
                               'fixable_by_translation':abs(gap)<=.002},
                     expected={'gap_m':0.0,'tolerance_m':0.002})
                valid=False
            # Only a verified horizontal flat top is a supported surface.
            rb=np.array(records[b]['rotation'])
            if records[b]['primitive'] in {'sphere','sweep','lathe'} or abs(rb[2,2])<1-1e-6:
                fail([a,b],'unsupported_surface','Support needs a horizontal flat box/cylinder top.',
                     measured={'support':b,'primitive':records[b]['primitive'],
                               'up_axis_dot':round(float(abs(rb[2,2])),6)},
                     expected={'primitive':'box|cylinder','up_axis_dot':1.0})
                valid=False
            footprint=(corners(records[a])-records[b]['center'])@rb
            half=np.array(records[b]['size'])[:2]/2
            if records[b]['primitive']=='cylinder':
                fits=np.all(np.sum((footprint[:,:2]/half)**2,axis=1)<=1.001)
            else:fits=np.all(np.abs(footprint[:,:2])<=half+.001)
            # A member straddling several supports only has to rest on their union,
            # so remember the miss and settle it once every support is known.
            if not fits:
                child_lo,child_hi=footprint[:,:2].min(axis=0),footprint[:,:2].max(axis=0)
                over=(child_hi-half).clip(min=0),(-half-child_lo).clip(min=0)
                excess=[float(max(over[0][i],over[1][i])) for i in range(2)]
                support_footprint_failures.setdefault(a,[]).append({
                    'support':b,'parent_part':a,'excess':excess,
                    'rect':support_rect(records[b]),
                    'part_footprint':[float(v) for v in (child_hi-child_lo)],
                    'support_top':[float(v) for v in (half*2)]})
        else:
            from sweep_contacts import sweep_gap
            verified_sweep=sweep_gap(records[a],records[b])
            if verified_sweep is None and (records[a]['primitive']!='box' or records[b]['primitive']!='box') and not flat_cap_attachment(records[a],records[b],relation.max_penetration+.002):
                fail([a,b],'unverified_curved_joint','Curved attachment contact needs a dedicated surface check; envelope proximity is insufficient.')
                valid=False
            if gap>.002 or gap < -relation.max_penetration-.0005:
                fail([a,b],'attachment',f'Attachment geometric gap {gap:.4f}m exceeds contact/penetration allowance.');valid=False
            joints[frozenset((a,b))]=relation.max_penetration
        if valid:
            # Support transfers load towards its target; fastened joints are bidirectional.
            edges[a].add(b)
            physical_edges[a].add(b);physical_edges[b].add(a)
            if relation.kind=='attached_to':edges[b].add(a)
    # Settle support coverage per child, not per relation: a part resting on
    # several members is valid when their combined top surfaces carry it.
    for child,entries in support_footprint_failures.items():
        if len(entries)<2:
            entry=entries[0]
            fail([child,entry['support']],'support_footprint',
                 'Supported part envelope exceeds actual support top.',
                 measured={'part':child,'support':entry['support'],
                           'part_footprint_m':[round(v,4) for v in entry['part_footprint']],
                           'support_top_m':[round(v,4) for v in entry['support_top']],
                           'excess_m':[round(v,4) for v in entry['excess']],
                           'part_center_xy':[round(float(v),4) for v in records[child]['center'][:2]],
                           'support_center_xy':[round(float(v),4) for v in records[entry['support']]['center'][:2]],
                           'declared_support_count':1,
                           'child_needs_multi_support':max(entry['excess'])>.02},
                 expected={'part_footprint_m':[round(v,4) for v in entry['support_top']]})
            continue
        span=child_span(records[child])
        # A balanced end overhang on both sides is normal. What is not carried is
        # a span the union stops short of at one end only.
        residual=[round(v,4) for v in union_coverage(records[child],entries)]
        if max(residual)<=.02:
            continue
        fail([child,*[entry['support'] for entry in entries]],'support_footprint',
             'Part is cantilevered past the combined top surfaces of its declared supports.',
             measured={'part':child,
                       'supports':[entry['support'] for entry in entries],
                       'declared_support_count':len(entries),
                       'part_footprint_m':[round(v,4) for v in span],
                       'unbalanced_overhang_m':residual,
                       'per_support_excess_m':[{'support':entry['support'],
                                                'excess_m':[round(v,4) for v in entry['excess']]}
                                               for entry in entries],
                       'note':('Each support alone is narrower than the part, which is expected for a '
                               'straddling member; what fails is the union stopping short at one end. '
                               'Add the missing support there, widen a support, or reduce the span.')},
             expected={'unbalanced_overhang_m':[0.0,0.0]})
    for part in obj.parts:
        if part.parent and part.mount=='top' and (part.id,part.parent) not in supports:
            fail([part.id,part.parent],'missing_support_relation','A top-mounted part needs a supported_by relation.')
    grounded={part_id for part_id in ids if abs(records[part_id]['bounds'][0][2])<=.002}
    for root in roots & ids:
        connected={root};pending=[root]
        while pending:
            for neighbor in physical_edges[pending.pop()]-connected:
                connected.add(neighbor);pending.append(neighbor)
        if not connected&grounded:
            fail([root],'floating_root',
                 'Assembly root has no verified contact path to a member reaching local bottom Z=0.')
    for part in sorted(ids-roots):
        reached={part};pending=[part]
        while pending:
            for target in edges[pending.pop()]-reached:
                reached.add(target);pending.append(target)
        if not reached & roots:
            fail([part],'disconnected','No verified contact/attachment path to an assembly root.',
                 measured={'part':part,
                           'reachable_parts':sorted(reached),
                           'roots':sorted(roots),
                           'declared_relations':sorted(
                               f'{r.part}->{r.kind}->{r.target}'
                               for r in contract.relations if part in (r.part,r.target)),
                           'note':('Relations reachable from this part end at a non-root group. '
                                   'Either connect the group to a root or revise which part is a root.')},
                 expected={'reachable_roots':sorted(roots)})
    for a,b in itertools.combinations(sorted(ids),2):
        gap=separating_gap(records[a],records[b])
        allowance=joints.get(frozenset((a,b)),0)
        if gap < -max(.0005,allowance+.0005):
            fail([a,b],'intersection',f'Undeclared/excessive part penetration: {-gap:.4f}m.')
    from part_consistency import audit_symmetry, SYMMETRY_TOLERANCE_M
    for miss in audit_symmetry(records,contract.symmetry):
        fail([miss['part']],'asymmetric_part',
             f"Assembly declares symmetry={contract.symmetry} but {miss['part']} has no mirror partner across the "
             f"{miss['plane']} plane. Make the counterpart a copy: like=<source>, mirror={miss['plane']!r}.",
             measured={'mirrored_bounds':miss['mirrored_bounds']},
             expected={'partner':'a part of the same primitive whose geometry matches the mirrored bounds/path',
                       'tolerance_m':SYMMETRY_TOLERANCE_M})
    return {'status':'passed' if not findings else 'needs_revision','findings':findings,
            'limits':'Geometric contacts, attachment paths, clearances and oriented envelopes only; '
                     'no mass, torque, friction, fastener strength or dynamic stability certification.'}


def audit_exported_parts(plan, receipts, actual):
    """Check Blender-measured part bounds against the compiled assembly."""
    import copy
    findings=[];reports={}
    for obj in plan.objects:
        if obj.source.mode!='parametric':continue
        records=copy.deepcopy(receipts[obj.id]['internal_geometry']['parts'])
        measured=actual.get(obj.id,{})
        if set(records)!=set(measured):
            findings.append({'component_ids':[obj.id],'part_ids':sorted(set(records)^set(measured)),
                'repair_target':'geometry','evidence':'Blender part IDs differ from the compiled assembly.'})
            continue
        for part_id,record in records.items():
            if not np.allclose(record['bounds'],measured[part_id],rtol=0,atol=.003):
                findings.append({'component_ids':[obj.id],'part_ids':[part_id],'repair_target':'geometry',
                                 'evidence':'Blender internal geometry differs from the compiled part bounds.'})
            record['bounds']=measured[part_id]
        report=audit_assembly(obj,records);reports[obj.id]=report;findings.extend(report['findings'])
    return {'status':'passed' if not findings else 'needs_revision','findings':findings,'components':reports}
