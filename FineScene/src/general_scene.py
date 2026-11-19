"""Category-independent scene programs, bounded expressions and aDSL compilation."""
from __future__ import annotations

import ast
import math
import operator
import copy
import json
from collections import OrderedDict
from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator
from adsl.core import Asset, Cube, Cylinder, Sphere, ExternalMesh, rotate_shape, scale_shape, shape_aabb, translate_shape
from asset_physics import AssemblyContract, FitSpan, PartForm, audit_assembly, rotation_matrix
from part_details import BookDetail, Finish, SurfaceSpec
from scene_spatial import SpatialContract, validate_spatial_contract, audit_spatial
from surface_mounting import SurfaceContact, resolve as resolve_surface, rotation as surface_rotation
from curve_presets import CurveSpec, ProfileSpec, check_curve_fields, curve_points, lathe_profile, taper_radii
from part_consistency import check_copy_fields, resolve_copies

Scalar = float | str
Vector = tuple[Scalar, Scalar, Scalar]
MIN_COMPONENT_ENVELOPE_UTILIZATION = 0.60


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False, validate_default=True)


class Part(StrictModel):
    id: str = Field(pattern=r'^[A-Za-z][A-Za-z0-9_-]{0,63}$')
    primitive: Literal['box', 'cylinder', 'sphere', 'book', 'sweep', 'lathe']
    size: Vector
    center: Vector
    rotation: tuple[float, float, float] = (0, 0, 0)
    color: tuple[float, float, float] = (.5, .5, .5)
    finish: Finish = 'matte'
    book: BookDetail | None = None
    surface: SurfaceSpec | None = None
    semantic: str = ''
    form: PartForm | None = None
    rigidity: Literal['rigid','parametric'] = 'parametric'
    parent: str | None = None
    mount: Literal['origin','top','bottom'] = 'origin'
    fit_between: list[FitSpan] = Field(default_factory=list,max_length=3)
    path: list[Vector] = Field(default_factory=list, max_length=64)
    radius: Scalar | None = None
    radius_end: Scalar | None = None
    closed: bool = False
    curve: CurveSpec | None = None
    profile: ProfileSpec | None = None
    like: str | None = None
    mirror: Literal['none','x','y','xy'] = 'none'

    @model_validator(mode='after')
    def check_sweep(self):
        check_curve_fields(self)
        check_copy_fields(self)
        return self


class Source(StrictModel):
    mode: Literal['parametric', 'retrieve', 'generate', 'auto'] = 'parametric'
    asset_id: str | None = None
    query: str = ''
    reference_image: str | None = None
    reference_query: str = ''
    front_rotation_deg: float = 0
    required_features: list[str] = Field(default_factory=list)
    material_requirements: list[str] = Field(default_factory=list)
    reference_requirements: list[str] = Field(default_factory=list)
    decision_reason: str = ''
    generation_trigger: str = ''


class SceneObject(StrictModel):
    id: str = Field(pattern=r'^[A-Za-z][A-Za-z0-9_-]{0,63}$')
    semantic: str = Field(min_length=1, max_length=400)
    source: Source = Field(default_factory=Source)
    size: Vector
    position: Vector = (0, 0, 0)
    rotation_deg: float = 0
    parent: str | None = None
    mount: Literal['origin', 'top','bottom'] = 'origin'
    support_part: str | None = None
    surface_contact: SurfaceContact | None = None
    grounded: bool = False
    access_required: bool = True
    support_footprint: tuple[Scalar,Scalar] | None = None
    collision_role: Literal['solid', 'shell', 'floor_covering'] = 'solid'
    parameters: dict[str, float] = Field(default_factory=dict)
    rigidity: Literal['rigid','parametric'] = 'parametric'
    parts: list[Part] = Field(default_factory=list, max_length=160)
    assembly: AssemblyContract | None = None

    @model_validator(mode='after')
    def check_source(self):
        if self.surface_contact and (not self.parent or self.mount!='origin' or self.grounded or self.support_part):
            raise ValueError('surface_contact requires parent, mount=origin, grounded=false, support_part=null')
        if self.surface_contact and self.surface_contact.child_part and self.source.mode!='parametric':
            raise ValueError('External mounting uses a measured mesh face, not invented internal part IDs')
        if self.source.mode == 'parametric' and not self.parts:
            raise ValueError(f'{self.id}: parametric objects require explicit parts, not a fallback box')
        if self.source.mode != 'parametric' and self.parts:
            raise ValueError(f'{self.id}: external geometry cannot silently fall back to parts')
        if self.mount in {'top','bottom'} and self.parent is None:
            raise ValueError('surface mount requires a parent')
        if self.support_part and (self.parent is None or self.mount not in {'top','bottom'}):
            raise ValueError('support_part requires a top/bottom mounted child with an explicit parent')
        if self.support_footprint and any(isinstance(v,(int,float)) and v<=0 for v in self.support_footprint):
            raise ValueError('support footprint dimensions must be positive')
        if len({p.id for p in self.parts}) != len(self.parts):
            raise ValueError('duplicate part IDs')
        parts={p.id:p for p in self.parts}
        dependencies={p.id:([p.parent] if p.parent else [])+[v for span in p.fit_between for v in (span.start,span.end)] for p in self.parts}
        verified=set()
        for part in self.parts:
            if part.mount!='origin' and part.parent is None:raise ValueError('Part surface mount requires parent')
            if part.fit_between and (part.rigidity!='parametric' or part.primitive!='box' or part.parent or any(part.rotation)):
                raise ValueError('Face fitting supports unrotated parametric boxes without a transform parent')
            if len({s.axis for s in part.fit_between})!=len(part.fit_between):raise ValueError('Duplicate fitting axis')
            def visit(id,ancestors):
                if id not in parts or id in ancestors:raise ValueError('Unknown/cyclic part dependency')
                if id in verified:return
                for parent in dependencies[id]:visit(parent,ancestors|{id})
                verified.add(id)
            visit(part.id,set())
        if self.source.mode!='parametric' and self.assembly is not None:
            raise ValueError('External mesh cannot claim an unmeasured internal part assembly')
        return self


class GeneralPlan(StrictModel):
    scene_id: str = Field(pattern=r'^[A-Za-z][A-Za-z0-9_-]{0,63}$')
    description: str
    extent: tuple[float, float, float]
    review_azimuth_deg: float = Field(default=0,ge=-360,le=360)
    parameters: dict[str, float] = Field(default_factory=dict)
    objects: list[SceneObject] = Field(min_length=1, max_length=80)
    relations: list[dict] = Field(default_factory=list, max_length=640)
    edit_plans: list[dict] = Field(default_factory=list, max_length=160)
    edit_checks: list[dict] = Field(default_factory=list, max_length=32)
    assumptions: list[str] = Field(default_factory=list)
    spatial: SpatialContract | None = None

    @model_validator(mode='after')
    def check_graph(self):
        if any(not 0 < v <= 1000 for v in self.extent):
            raise ValueError('extent must be positive metres, at most 1000')
        by_id = {o.id: o for o in self.objects}
        if len(by_id) != len(self.objects):
            raise ValueError('duplicate object IDs')
        for obj in self.objects:
            seen = {obj.id}
            parent = obj.parent
            while parent:
                if parent not in by_id:
                    raise ValueError(f'unknown parent {parent}')
                if parent in seen:
                    raise ValueError('cyclic object dependencies')
                seen.add(parent)
                parent = by_id[parent].parent
            if obj.surface_contact:
                host=by_id[obj.parent]
                if host.source.mode!='parametric':raise ValueError('Surface mounting requires measured named parametric support members')
                from surface_mounting import members
                members({p.id:[[0,0,0],[1,1,1]] for p in host.parts},obj.surface_contact.parent_part)
                import re
                support_members=[p for p in host.parts if p.id==obj.surface_contact.parent_part or
                    re.fullmatch(re.escape(obj.surface_contact.parent_part)+r'_\d{3}',p.id)]
                if any(p.primitive!='box' or any(abs(a)>1e-8 for a in p.rotation) for p in support_members):
                    raise ValueError('Mounting support must consist of actual unrotated box faces in the parent frame')
                if obj.surface_contact.child_part and obj.surface_contact.child_part not in {p.id for p in obj.parts}:
                    raise ValueError('Unknown parametric mounting backplate')
            if obj.support_part:
                parent_obj=by_id[obj.parent]
                if parent_obj.source.mode!='parametric':
                    raise ValueError(f'{obj.id}: named support parts require a parametric parent')
                part=next((part for part in parent_obj.parts if part.id==obj.support_part),None)
                if part is None:
                    raise ValueError(f'{obj.id}: support part {obj.support_part} is not defined by parent {obj.parent}')
                if part.primitive!='box' or any(abs(a)>1e-8 for a in part.rotation[:2]):
                    raise ValueError(f'{obj.id}: named support must be a horizontal parametric box face')
        for relation in self.relations:
            endpoints = [relation.get(k) for k in ("from", "to", "from_id", "to_id") if relation.get(k) is not None]
            if len(endpoints) >= 2 and any(endpoint not in by_id for endpoint in endpoints[:2]):
                raise ValueError('relation references unknown object')
        if self.spatial is not None:
            validate_spatial_contract(self.spatial, self.objects, self.extent)
        return self


def scalar(value: Scalar, params: dict[str, float]) -> float:
    """Arithmetic only; no Python evaluation, attributes or arbitrary calls."""
    if isinstance(value, str):
        value=value.strip()
        if len(value) > 200:
            raise ValueError('expression too long')
        tree = ast.parse(value, mode='eval')
        if len(list(ast.walk(tree))) > 64:
            raise ValueError('expression too complex')
        ops = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv}
        def visit(node):
            if isinstance(node, ast.Expression): return visit(node.body)
            if isinstance(node, ast.Constant) and type(node.value) in (int, float): return node.value
            if isinstance(node, ast.Name) and node.id in params: return params[node.id]
            if isinstance(node, ast.BinOp) and type(node.op) in ops: return ops[type(node.op)](visit(node.left), visit(node.right))
            if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
                return (-1 if isinstance(node.op, ast.USub) else 1) * visit(node.operand)
            raise ValueError(f'unsupported expression: {value}')
        value = visit(tree)
    value = float(value)
    if not math.isfinite(value) or abs(value) > 10000:
        raise ValueError('nonfinite or unbounded scalar')
    return value


def vec(values, params, positive=False):
    result = np.array([scalar(v, params) for v in values])
    if positive and np.any(result <= 0): raise ValueError('dimensions must be positive')
    return result


def object_size(plan, obj):
    return vec(obj.size, {**plan.parameters, **obj.parameters}, positive=True)


def _transform_local_bounds(bounds, angle_deg, origin):
    low,high=np.asarray(bounds,dtype=float)
    corners=np.array([[x,y,z] for x in (low[0],high[0]) for y in (low[1],high[1])
                      for z in (low[2],high[2])])
    angle=math.radians(angle_deg)
    rotation=np.array([[math.cos(angle),-math.sin(angle),0],
                       [math.sin(angle), math.cos(angle),0],[0,0,1]])
    points=corners@rotation.T+np.asarray(origin,dtype=float)
    return [points.min(axis=0).tolist(),points.max(axis=0).tolist()]


def _support_world_bounds(obj, parent_bounds, parent_pose, *, parent_receipt=None, measured_parts=None):
    if not obj.support_part:
        return parent_bounds
    if measured_parts and obj.parent in measured_parts and obj.support_part in measured_parts[obj.parent]:
        local=measured_parts[obj.parent][obj.support_part]
    elif parent_receipt:
        try:local=parent_receipt['internal_geometry']['parts'][obj.support_part]['bounds']
        except (KeyError,TypeError):raise ValueError(f'{obj.id}: measured support part {obj.support_part} is missing from its parent')
    else:
        raise ValueError(f'{obj.id}: support part {obj.support_part} has no compiled parent geometry')
    angle,origin=parent_pose
    return _transform_local_bounds(local,angle,origin)


def _planned_envelopes_and_poses(plan: GeneralPlan, *, diagnostics=None):
    """Placement preflight using declared envelopes and named support interfaces."""
    from planning_diagnostics import Diagnostics
    strict=diagnostics is None
    diagnostics=diagnostics if diagnostics is not None else Diagnostics('placement');failed=set()
    indices={obj.id:i for i,obj in enumerate(plan.objects)}
    poses, bounds = {}, {}
    support_part_bounds={}
    pending=list(plan.objects)
    while pending:
        for obj in pending[:]:
            if obj.parent in failed:
                diagnostics.block('world_pose',f'{obj.id}: parent {obj.parent} has an unresolved mounting pose.',
                    paths=[f'/components/{indices[obj.id]}/position'],scenario=obj.id)
                failed.add(obj.id);pending.remove(obj);continue
            if obj.parent and obj.parent not in poses:continue
            size=object_size(plan,obj)
            params={**plan.parameters,**obj.parameters,**dict(zip(('w','d','h'),size))}
            parent_angle,parent_origin=poses.get(obj.parent,(0.,np.zeros(3)))
            def rot(degrees):
                angle=math.radians(degrees)
                return np.array([[math.cos(angle),-math.sin(angle),0],[math.sin(angle),math.cos(angle),0],[0,0,1]])
            offset=vec(obj.position,params)
            origin=parent_origin+rot(parent_angle)@offset
            support_bounds=bounds.get(obj.parent)
            if obj.support_part:
                key=(obj.parent,obj.support_part)
                if key not in support_part_bounds:
                    parent=next(candidate for candidate in plan.objects if candidate.id==obj.parent)
                    evidence={}
                    compile_local_geometry(plan,parent,evidence=evidence)
                    local=evidence['parts'][obj.support_part]['bounds']
                    parent_angle_value,parent_origin_value=poses[obj.parent]
                    support_part_bounds[key]=_transform_local_bounds(local,parent_angle_value,parent_origin_value)
                support_bounds=support_part_bounds[key]
                origin[:2]+=((np.asarray(support_bounds[0][:2])+np.asarray(support_bounds[1][:2]))/2
                             -parent_origin[:2])
            if obj.mount=='top':origin[2]=support_bounds[1][2]+offset[2]
            if obj.mount=='bottom':origin[2]=support_bounds[0][2]-size[2]+offset[2]
            if obj.surface_contact:
                parent=next(o for o in plan.objects if o.id==obj.parent)
                parent_evidence={};compile_local_geometry(plan,parent,evidence=parent_evidence)
                child_evidence={};child_bounds=[[-size[0]/2,-size[1]/2,0],[size[0]/2,size[1]/2,size[2]]]
                if obj.source.mode=='parametric':
                    _,cl,ch=compile_local_geometry(plan,obj,evidence=child_evidence);child_bounds=[cl,ch]
                try:local_offset,_=resolve_surface(obj.surface_contact,parent_evidence['parts'],child_bounds,
                    child_evidence.get('parts',{}),offset,obj.rotation_deg,
                    parent_pose=poses[obj.parent],scene_extent=plan.extent)
                except ValueError as failure:
                    path=f'/components/{indices[obj.id]}'
                    controls=([path+'/surface_contact'] if getattr(failure,'code',None) in {'mounting_interface_capacity','mounting_extent_conflict'}
                              else [path+'/position',path+'/orientation_deg'])
                    diagnostics.add(getattr(failure,'code','mounting_surface'),
                        f'{obj.id} on {obj.parent}/{obj.surface_contact.parent_part}: {failure}',
                        paths=controls,component_ids=[obj.id,obj.parent],
                        measured=getattr(failure,'measurements',None),
                        expected={'selected_contact':obj.surface_contact.model_dump(),'full_surface_coverage_required':True})
                    failed.add(obj.id);pending.remove(obj);continue
                origin=parent_origin+rot(parent_angle)@local_offset
            angle=parent_angle+obj.rotation_deg
            corners=np.array([[x,y,z] for x in (-size[0]/2,size[0]/2)
                for y in (-size[1]/2,size[1]/2) for z in (0,size[2])])
            points=corners@rot(angle).T+origin
            bounds[obj.id]=[points.min(axis=0).tolist(),points.max(axis=0).tolist()]
            poses[obj.id]=(angle,origin)
            pending.remove(obj)
    if strict:diagnostics.raise_if_any()
    return bounds,poses


def planned_envelope_bounds(plan: GeneralPlan):
    """Placement preflight using declared envelopes, never substitute mesh geometry."""
    return _planned_envelopes_and_poses(plan)[0]


_LOCAL_GEOMETRY_CACHE=OrderedDict()


def compile_local_geometry(plan: GeneralPlan, obj: SceneObject, assets: dict | None = None, *, evidence=None, inspection=False):
    """Reuse identical validated local recipes; world poses never affect this cache.

    Keys include all recipe content and effective parameters. Independent copies
    prevent later assembly transforms or diagnostic edits from poisoning reuse.
    Failed recipes and external assets are never cached.
    """
    if obj.source.mode!='parametric':
        return _compile_local_geometry(plan,obj,assets,evidence=evidence,inspection=inspection)
    local_spec=obj.model_dump(mode='json',exclude={'position','rotation_deg','parent','mount',
        'support_part','surface_contact','grounded','access_required','support_footprint'})
    key=json.dumps([local_spec,plan.parameters],sort_keys=True,separators=(',',':'))
    if key not in _LOCAL_GEOMETRY_CACHE:
        record={}
        try:result=_compile_local_geometry(plan,obj,assets,evidence=record,inspection=inspection)
        except ValueError:
            if evidence is not None:evidence.update(record)
            raise
        _LOCAL_GEOMETRY_CACHE[key]=copy.deepcopy((result,record))
        if len(_LOCAL_GEOMETRY_CACHE)>128:_LOCAL_GEOMETRY_CACHE.popitem(last=False)
    _LOCAL_GEOMETRY_CACHE.move_to_end(key)
    result,record=copy.deepcopy(_LOCAL_GEOMETRY_CACHE[key])
    if evidence is not None:evidence.update(record)
    return result


def _compile_local_geometry(plan: GeneralPlan, obj: SceneObject, assets: dict | None = None, *, evidence=None, inspection=False):
    """Validate the real recipe before acquisition of unrelated components."""
    assets = assets or {}
    size = object_size(plan,obj)
    params = {**plan.parameters,**obj.parameters,**dict(zip(('w','d','h'),size))}
    local = Asset(obj.id)
    if obj.source.mode == 'parametric':
        records={};pending=list(obj.parts)
        # Copies (like/mirror) compile from their source design; inconsistent
        # copies are rejected here with every differing field listed.
        copies=resolve_copies(obj,lambda v:scalar(v,params))
        while pending:
            original=next(p for p in pending if (p.parent is None or p.parent in records)
                      and all(s.start in records and s.end in records for s in p.fit_between))
            part,mirror_m=copies.get(original.id,(original,None))
            dims=vec(part.size,params,positive=True); center=vec(part.center,params)
            for span in part.fit_between:
                axis='xyz'.index(span.axis)
                lo=records[span.start]['bounds'][0 if span.start_face=='min' else 1][axis]+span.inset
                hi=records[span.end]['bounds'][0 if span.end_face=='min' else 1][axis]-span.inset
                if hi-lo<=0:raise ValueError(f'{obj.id}/{part.id}: nonpositive fitted span')
                dims[axis]=hi-lo;center[axis]=(hi+lo)/2
            if any(v < 0 or v > 1 for v in part.color):raise ValueError('colors must be in [0,1]')
            if part.primitive == 'box':shape=Cube(dims,center=center,color=part.color)
            elif part.primitive == 'book':
                from adsl_components import build_simple_detail
                detail=part.book or BookDetail()
                thickness=dims[2] if detail.orientation=='flat' else dims[0]
                if thickness<.012 or min(dims)<.012 or dims[1]<.04:
                    raise ValueError('Book dimensions are too small for the detailed binding recipe')
                book=build_simple_detail({'component_id':part.id,'kind':'book','size':dims,
                    'layout_variant':'horizontal_stack' if detail.orientation=='flat' else 'upright',
                    'palette_index':detail.palette_index,'binding_style':detail.binding,'color':part.color})
                shape=translate_shape(book.scene,center-np.array([0,0,dims[2]/2]))
            elif part.primitive == 'cylinder':
                shape=translate_shape(scale_shape(Cylinder(.5,height=1,color=part.color),dims,center=(0,0,0)),center)
            elif part.primitive == 'sphere':
                shape=translate_shape(scale_shape(Sphere(.5,color=part.color),dims,center=(0,0,0)),center)
            elif part.primitive == 'lathe':
                # Solid of revolution about the part's local +Z: a stack of
                # coaxial frustums following the (preset or explicit) profile.
                profile=lathe_profile(part.profile,dims,lambda v:scalar(v,params))
                shape=Asset(f'{obj.id}_{part.id}_lathe')
                built=0
                for index,((ra,za),(rb,zb)) in enumerate(zip(profile,profile[1:])):
                    if zb-za<=1e-9 or max(ra,rb)<=1e-9:continue  # flat shoulders are end caps
                    p0,p1=center+np.array([0,0,za]),center+np.array([0,0,zb])
                    ring=(Cylinder(ra,p0=p0,p1=p1,radius_end=rb,color=part.color) if ra>1e-9
                          else Cylinder(rb,p0=p1,p1=p0,radius_end=0,color=part.color))
                    shape.attach_part(f'band_{index:02d}',ring);built+=1
                if not built:raise ValueError(f'{obj.id}/{part.id}: lathe profile encloses no volume')
            else:
                # A generic editable curved member.  The path (explicit or a
                # fitted curve preset) remains in the recipe and compiles to
                # real cylinders/frustums plus joint spheres, giving the asset a
                # continuous readable silhouette without pretending that a box
                # is a carved or swept form.
                r0=scalar(part.radius,params)
                r1=r0 if part.radius_end is None else scalar(part.radius_end,params)
                if r0<=0 or r1<0: raise ValueError(f'{obj.id}/{part.id}: sweep radius must be positive (radius_end >= 0)')
                if part.curve is not None:
                    local_points,closed=curve_points(part.curve,dims,r0,r1,lambda v:scalar(v,params),closed=part.closed)
                else:
                    local_points=np.array([vec(point,params) for point in part.path]);closed=part.closed
                if mirror_m is not None:
                    # Reflect the design path; together with the mirrored
                    # rotation and center this is the mirror image M*(R p + c).
                    local_points=local_points@mirror_m
                if closed and np.linalg.norm(local_points[0]-local_points[-1])>1e-8:
                    local_points=np.vstack([local_points,local_points[:1]])
                radii=taper_radii(local_points,r0,r1)
                if np.any(np.abs(local_points)+radii[:,None]>dims/2+.000001):
                    raise ValueError(f'{obj.id}/{part.id}: sweep path plus radius exceeds declared part size; path is relative to center')
                # Bake the rotation into path endpoints. Rotating a primitive's
                # AABB would overestimate a sphere/capsule's actual surface.
                points=local_points@rotation_matrix(part.rotation).T+center
                shape=Asset(f'{obj.id}_{part.id}_sweep')
                for index,(a,b) in enumerate(zip(points,points[1:])):
                    if np.linalg.norm(b-a)<=1e-8:
                        raise ValueError(f'{obj.id}/{part.id}: sweep contains a zero-length segment')
                    ra,rb=radii[index],radii[index+1]
                    tube=(Cylinder(ra,p0=a,p1=b,radius_end=rb,color=part.color) if ra>1e-9
                          else Cylinder(rb,p0=b,p1=a,radius_end=ra,color=part.color))
                    shape.attach_part(f'segment_{index:02d}',tube)
                for index,point in enumerate(points):
                    if radii[index]>1e-9:
                        shape.attach_part(f'joint_{index:02d}',Sphere(radii[index],center=point,color=part.color))
            for axis,angle in zip(('+x','+y','+z'),part.rotation):
                if angle and part.primitive!='sweep':shape=rotate_shape(shape,axis=axis,angle=angle,center=center)
            if part.parent:
                parent=records[part.parent]
                # Offsets and rotations use asset axes, not inherited part axes.
                shift=np.array(parent['center']).copy()
                sl,sh=shape_aabb(shape)
                if part.mount=='top':shift[2]=parent['bounds'][1][2]+center[2]-sl[2]
                if part.mount=='bottom':shift[2]=parent['bounds'][0][2]+center[2]-sh[2]
                shape=translate_shape(shape,shift);center=center+shift
                if part.primitive=='sweep':points+=shift
            local.attach_part(part.id,shape)
            records[part.id]={'primitive':part.primitive,'size':dims.tolist(),'center':center.tolist(),
                             'rotation':rotation_matrix(part.rotation).tolist(),
                             'bounds':[np.asarray(v).tolist() for v in shape_aabb(shape)]}
            if part.primitive=='sweep':
                records[part.id].update(path=points.tolist(),local_path=local_points.tolist(),radius=r0,
                                        radius_end=r1,radii=radii.tolist(),closed=bool(closed),
                                        curve=part.curve.preset if part.curve is not None else None)
            if part.primitive=='lathe':
                records[part.id].update(profile=profile.tolist(),
                                        profile_preset=part.profile.preset)
            if part.like is not None:
                records[part.id].update(like=part.like,mirror=part.mirror)
            pending.remove(original)
        audit=audit_assembly(obj,records)
        if evidence is not None:evidence.update(parts=records,assembly=audit)
        if audit['status']!='passed':
            import json
            raise ValueError(f'{obj.id}: internal assembly failed: '+json.dumps(audit['findings']))
    else:
        if obj.id not in assets:raise ValueError(f'unresolved component {obj.id}: {obj.semantic}')
        asset=assets[obj.id]
        if asset.get('accepted') is not True and not (inspection and asset.get('inspection_only') is True):
            raise ValueError(f'component {obj.id} has not passed visual QA')
        local.attach_part('external',ExternalMesh(asset['path'],asset['bounds']))
    low,high=[np.asarray(v) for v in shape_aabb(local)]
    envelope_low=np.array([-size[0]/2,-size[1]/2,0])
    envelope_high=np.array([size[0]/2,size[1]/2,size[2]])
    if (not np.isfinite([low,high]).all() or np.any(high<=low) or abs(low[2])>.01
        or np.any(low<envelope_low-.015) or np.any(high>envelope_high+.015)):
        raise ValueError(f'{obj.id}: local geometry violates centered envelope/bottom: {low.tolist()}, {high.tolist()} vs {size.tolist()}')
    return local,low,high


def compile_general(plan: GeneralPlan, assets: dict | None = None, *, inspection=False):
    assets = assets or {}
    scene = Asset(plan.scene_id)
    world, poses, receipts = {}, {}, {}
    pending = list(plan.objects)
    while pending:
        for obj in pending[:]:
            if obj.parent and obj.parent not in poses: continue
            size = object_size(plan, obj)
            params = {**plan.parameters, **obj.parameters, **dict(zip(('w','d','h'), size))}
            internal={}
            local,low,high = compile_local_geometry(plan,obj,assets,evidence=internal,inspection=inspection)
            offset = vec(obj.position, params)
            parent_angle, parent_origin = poses.get(obj.parent, (0., np.zeros(3)))
            angle = math.radians(parent_angle)
            rotate_xy = np.array([[math.cos(angle),-math.sin(angle),0],[math.sin(angle),math.cos(angle),0],[0,0,1]])
            origin = parent_origin + rotate_xy @ offset
            support_bounds=world.get(obj.parent)
            if obj.parent:
                support_bounds=_support_world_bounds(obj,support_bounds,poses[obj.parent],
                    parent_receipt=receipts.get(obj.parent))
                if obj.support_part:
                    origin[:2]+=((np.asarray(support_bounds[0][:2])+np.asarray(support_bounds[1][:2]))/2
                                 -parent_origin[:2])
            if obj.mount == 'top': origin[2] = support_bounds[1][2] + offset[2]
            if obj.mount == 'bottom': origin[2] = support_bounds[0][2]-size[2]+offset[2]
            surface_evidence=None
            if obj.surface_contact:
                local_offset,surface_evidence=resolve_surface(obj.surface_contact,
                    receipts[obj.parent]['internal_geometry']['parts'],[low,high],internal.get('parts',{}),offset,obj.rotation_deg)
                origin=parent_origin+rotate_xy@local_offset
            rotation = parent_angle + obj.rotation_deg
            positioned = rotate_shape(local, axis='+z', angle=rotation, center=(0,0,0)) if rotation else local
            positioned = translate_shape(positioned, origin)
            scene.attach_part(obj.id, positioned)
            world[obj.id] = [list(map(float, v)) for v in shape_aabb(positioned)]
            poses[obj.id] = (rotation, origin)
            receipts[obj.id] = {'semantic':obj.semantic,'source':obj.source.model_dump(), 'parent':obj.parent,
                'mount':obj.mount,'support_part':obj.support_part,
                'surface_contact':surface_evidence,
                'support_bounds':support_bounds if obj.parent else None,
                'local_bounds':[low.tolist(),high.tolist()], 'world_origin':origin.tolist(),
                'world_rotation_deg':rotation,'parameters':params,'parts':[p.id for p in obj.parts],
                'internal_geometry':internal,
                'asset':assets.get(obj.id), 'editable':['position','rotation','parameters','parts'] if obj.source.mode=='parametric' else ['position','rotation','replace_asset']}
            pending.remove(obj)
    return scene, world, receipts


def _support_surface_bounds(plan, obj, bounds, receipts=None, measured_parts=None, poses=None):
    if not obj.support_part:
        return bounds[obj.parent]
    receipts=receipts or {};measured_parts=measured_parts or {}
    if obj.parent in measured_parts and obj.support_part in measured_parts[obj.parent]:
        local=measured_parts[obj.parent][obj.support_part]
    elif obj.parent in receipts:
        parent_receipt=receipts[obj.parent]
        try:local=parent_receipt['internal_geometry']['parts'][obj.support_part]['bounds']
        except (KeyError,TypeError):raise ValueError(f'{obj.id}: missing compiled support part {obj.support_part}')
    else:
        parent=next(candidate for candidate in plan.objects if candidate.id==obj.parent)
        evidence={};compile_local_geometry(plan,parent,evidence=evidence)
        local=evidence['parts'][obj.support_part]['bounds']
    if obj.parent in receipts:
        parent_receipt=receipts[obj.parent]
        pose=(parent_receipt['world_rotation_deg'],parent_receipt['world_origin'])
    else:
        if poses is None:_,poses=_planned_envelopes_and_poses(plan)
        pose=poses[obj.parent]
    return _transform_local_bounds(local,pose[0],pose[1])


def _extent_finding(plan, obj, low, high, poses):
    """Report the actual violated axes and a parent-local, extent-only correction."""
    lower=np.array([-plan.extent[0]/2,-plan.extent[1]/2,0.])
    upper=np.array([plan.extent[0]/2,plan.extent[1]/2,plan.extent[2]])
    if obj.collision_role=='shell':lower[2]=-np.inf
    violations=[]
    for axis,name in enumerate('xyz'):
        for side,actual,limit,excess in [('min',low[axis],lower[axis],lower[axis]-low[axis]),
                                        ('max',high[axis],upper[axis],high[axis]-upper[axis])]:
            if excess>.015:violations.append({'axis':name,'side':side,'actual_m':float(actual),
                                              'limit_m':float(limit),'excess_m':float(excess)})
    if not violations:return None
    delta_low=lower-low;delta_high=upper-high
    feasible=bool(np.all(delta_low<=delta_high+.03))
    yaw,origin=poses.get(obj.parent,(0.,np.zeros(3)))
    proposed=None
    if feasible and obj.mount=='origin' and not obj.surface_contact and not obj.support_part:
        delta=np.maximum(delta_low,np.minimum(np.zeros(3),delta_high))
        proposed=(surface_rotation(yaw).T@delta).tolist()
    return {'code':'scene_extent','component_ids':[obj.id],
        'evidence':'outside declared scene extent: '+ '; '.join(
            f'{v["axis"]}_{v["side"]}={v["actual_m"]:.4f}m, limit={v["limit_m"]:.4f}m, excess={v["excess_m"]:.4f}m'
            for v in violations),
        'measured':{'world_bounds_m':[low.tolist(),high.tolist()],'violations':violations,
            'local_position':list(obj.position),'parent_id':obj.parent,
            'parent_world_origin_m':np.asarray(origin).tolist(),'parent_world_yaw_deg':yaw,
            'mount':obj.mount,'translation_feasible':feasible,
            'world_translation_intervals_m':[[float(a) if np.isfinite(a) else None,float(b)]
                                             for a,b in zip(delta_low,delta_high)],
            'suggested_local_translation_m':proposed,
            'correction_scope':'Extent only; recheck opening, grounding, support, collisions and routes. '
                'No independent translation is suggested for constrained support/contact; revise the controlling interface.'},
        'expected':{'world_bounds_m':[[float(v) if np.isfinite(v) else None for v in lower],upper.tolist()],
                    'tolerance_m':.015,'occupied_floor_z_m':0,'position_frame':'parent-local, or world for roots'}}


def judge_general_geometry(plan: GeneralPlan, bounds: dict, receipts=None, measured_parts=None, *, check_spatial=True,
                           placement_state=None):
    from planning_diagnostics import Diagnostics
    diagnostics=Diagnostics('geometry')
    findings = []
    by_id = {o.id:o for o in plan.objects}
    if placement_state is None:
        expected,poses=_planned_envelopes_and_poses(plan,diagnostics=diagnostics)
    else:expected,poses,diagnostics=placement_state
    findings.extend(diagnostics.report()['issues'])
    def support_bounds(obj, low, high):
        if obj.support_footprint is None:return low,high
        angle=obj.rotation_deg;cursor=obj
        while cursor.parent:
            cursor=by_id[cursor.parent];angle+=cursor.rotation_deg
        size=object_size(plan,obj)
        params={**plan.parameters,**obj.parameters,**dict(zip(('w','d','h'),size))}
        x,y=[scalar(v,params) for v in obj.support_footprint]
        if x<=0 or y<=0 or x>size[0]+.015 or y>size[1]+.015:
            raise ValueError(f'{obj.id}: invalid contact footprint')
        angle=math.radians(angle)
        half=np.array([abs(math.cos(angle))*x+abs(math.sin(angle))*y,
                       abs(math.sin(angle))*x+abs(math.cos(angle))*y])/2
        center=(np.array(expected[obj.id][0][:2])+np.array(expected[obj.id][1][:2]))/2
        sl,sh=low.copy(),high.copy();sl[:2]=center-half;sh[:2]=center+half
        return sl,sh
    def fail(ids, reason): findings.append({'component_ids':ids,'evidence':reason})
    for obj in plan.objects:
        if obj.id not in expected:
            diagnostics.block('component_geometry','Placement prerequisite failed; no substitute world bounds used.',
                              scenario=obj.id)
            continue
        if obj.id not in bounds:
            fail([obj.id], 'missing actual geometry');continue
        low, high = np.array(bounds[obj.id], dtype=float)
        if not np.isfinite([low,high]).all() or np.any(high <= low):
            fail([obj.id], 'invalid bounds');continue
        planned_low,planned_high=np.asarray(expected[obj.id],dtype=float)
        if obj.collision_role=='floor_covering':
            thickness=float(planned_high[2]-planned_low[2])
            if obj.parent is not None or not obj.grounded:
                fail([obj.id],'floor_covering must be an independent grounded floor-level object')
            if thickness>.08+1e-6:
                fail([obj.id],f'floor_covering thickness {thickness:.3f}m exceeds 0.08m; classify raised platforms as solid')
        planned_center=(planned_low+planned_high)/2
        actual_center=(low+high)/2
        parametric_measured=obj.source.mode=='parametric' and receipts is not None
        center_error=actual_center[:2]-planned_center[:2]
        if obj.surface_contact and receipts and not parametric_measured:
            # A real imported backplate can differ from its coarse envelope.
            # The measured mounting audit establishes normal contact below.
            normal=surface_rotation(receipts[obj.parent]['world_rotation_deg'])[:, 'xyz'.index(obj.surface_contact.parent_face[0])]
            center_error-=normal[:2]*np.dot(center_error,normal[:2])
        if np.any(np.abs(center_error)>(.001 if parametric_measured else .015)):
            findings.append({'component_ids':[obj.id],'repair_target':'geometry' if parametric_measured else 'layout',
                'evidence':f'Measured geometry center XY {actual_center[:2].tolist()} differs from planned center {planned_center[:2].tolist()}. '
                +('Correct the local recipe frame; world translation moves the expected frame too and cannot remove this offset.' if parametric_measured else '')})
        if obj.source.mode=='parametric' and receipts is not None:
            measured_size=high-low;planned_size=planned_high-planned_low
            if np.any(np.abs(measured_size-planned_size)>.015):
                findings.append({'component_ids':[obj.id],'repair_target':'geometry',
                    'evidence':f'Measured parametric size {measured_size.tolist()} differs from declared envelope {planned_size.tolist()}. '
                               'Repair the local recipe geometry to realize the frozen dimensions; changing world position cannot correct object size.'})
        extent_issue=_extent_finding(plan,obj,low,high,poses)
        if extent_issue:findings.append(extent_issue)
        if obj.grounded:
            if obj.collision_role=='shell':
                if low[2]>.015 or high[2]<-.015:fail([obj.id],'grounded architecture must reach occupied floor Z=0')
            elif abs(low[2])>.015:fail([obj.id],'grounded component must contact floor Z=0')
        if obj.mount == 'bottom' and obj.parent in bounds:
            pl,ph=np.array(_support_surface_bounds(plan,obj,bounds,receipts,measured_parts,poses))
            if abs(high[2]-pl[2])>.015 or np.any(low[:2]<pl[:2]-.015) or np.any(high[:2]>ph[:2]+.015):
                fail([obj.id,obj.parent],'underside attachment gap or footprint exceeds parent')
        if obj.mount == 'top' and obj.parent in bounds:
            pl,ph = np.array(_support_surface_bounds(plan,obj,bounds,receipts,measured_parts,poses))
            sl,sh=support_bounds(obj,low,high)
            if abs(low[2]-ph[2]) > .015 or np.any(sl[:2] < pl[:2]-.015) or np.any(sh[:2] > ph[:2]+.015):
                fail([obj.id,obj.parent], 'top support gap or footprint exceeds parent')
    local_parts=measured_parts
    if local_parts is None and receipts:
        local_parts={id:{part_id:record['bounds'] for part_id,record in
                     receipt.get('internal_geometry',{}).get('parts',{}).items()}
                     for id,receipt in receipts.items()}
    def occupied(obj):
        members=(local_parts or {}).get(obj.id,{})
        if members:
            yaw,origin=poses[obj.id]
            return [(part,np.asarray(_transform_local_bounds(local,yaw,origin))) for part,local in members.items()]
        return [(None,np.asarray(bounds[obj.id]))]
    solids = [o for o in plan.objects if o.collision_role == 'solid' and o.id in bounds and o.id in poses]
    occupied_by_id={o.id:occupied(o) for o in solids}
    def ancestor(a,b):
        while a.parent:
            if a.parent == b.id:return True
            a=by_id[a.parent]
        return False
    if check_spatial:
        for i,a in enumerate(solids):
            for b in solids[i+1:]:
                if ancestor(a,b) or ancestor(b,a):continue
                al,ah=np.asarray(bounds[a.id]);bl,bh=np.asarray(bounds[b.id])
                if not np.all(np.minimum(ah,bh)-np.maximum(al,bl)>.015):continue
                overlaps=[(ap,bp) for ap,(al,ah) in occupied_by_id[a.id] for bp,(bl,bh) in occupied_by_id[b.id]
                          if np.all(np.minimum(ah,bh)-np.maximum(al,bl) > .015)]
                if overlaps:
                    findings.append({'code':'occupied_overlap','component_ids':[a.id,b.id],
                        'repair_target':'layout',
                        'evidence':'overlapping occupied geometry bounds; revise placement or local geometry; '
                                   f'intersecting part pairs={overlaps}',
                        'expected':{'maximum_allowed_penetration_m':.015},
                        'measured':{'position_frame':'world', 'intersections':[
                            {'part_ids':[ap,bp],'world_bounds_a_m':[al.tolist(),ah.tolist()],
                             'world_bounds_b_m':[bl.tolist(),bh.tolist()],
                             'penetration_xyz_m':(np.minimum(ah,bh)-np.maximum(al,bl)).tolist()}
                            for ap,(al,ah) in occupied_by_id[a.id] for bp,(bl,bh) in occupied_by_id[b.id]
                            if (ap,bp) in overlaps]}})
    for r in plan.relations:
        a, b = r.get('from'), r.get('to')
        if a not in expected or b not in expected:
            diagnostics.block('relation','Unresolved placement prerequisite.',scenario=f'{a}->{b}');continue
        if a not in bounds or b not in bounds:
            fail([a,b], 'relation has missing geometry'); continue
        al,ah=np.array(bounds[a]);bl,bh=np.array(bounds[b])
        kind=r.get('type')
        if kind == 'supported_by':
            sl,sh=support_bounds(by_id[a],al,ah)
            support_low,support_high=np.array(_support_surface_bounds(plan,by_id[a],bounds,receipts,measured_parts,poses))
            if (abs(al[2]-support_high[2]) > .015 or np.any(sl[:2] < support_low[:2]-.015) or
                    np.any(sh[:2] > support_high[:2]+.015)):
                fail([a,b], 'declared support gap or footprint exceeds support')
        elif kind == 'clearance':
            if not check_spatial:continue
            distance=float(np.linalg.norm(np.maximum(0,np.maximum(al-bh,bl-ah))))
            if r.get('clearance') is None or distance + .001 < r['clearance']:
                fail([a,b], 'declared clearance not satisfied')
        elif kind == 'parent':
            if by_id[a].parent != b: fail([a,b], 'relation disagrees with transform parent')
        elif kind in {'attached_to','adjacent_to'}:
            if not check_spatial:continue
            distance=float(np.linalg.norm(np.maximum(0,np.maximum(al-bh,bl-ah))))
            tolerance=r.get('clearance') if r.get('clearance') is not None else .015
            if distance > tolerance + .015: fail([a,b], 'attachment/adjacency gap exceeds tolerance')
    if check_spatial and len(poses)==len(plan.objects):
        spatial = audit_spatial(plan.spatial, plan.objects, plan.extent, bounds, poses, shell_parts=local_parts,
                                parameters=plan.parameters,
                                local_positions={obj.id:obj.position for obj in plan.objects})
        findings.extend(spatial['findings'])
    elif check_spatial:
        missing=sorted(set(by_id)-set(poses))
        diagnostics.block('scene_spatial','Unresolved world poses: '+', '.join(missing))
        spatial={'status':'blocked','findings':[],'missing_component_ids':missing}
    else:
        spatial={'status':'deferred_to_blender','findings':[],
                 'reason':'Scene-level zones, access, circulation and furniture spacing require reconstructed geometry.'}
    return {'status':'passed' if not findings and not diagnostics.blocked_checks else 'needs_revision',
            'findings':findings,'spatial':spatial,'blocked_checks':diagnostics.blocked_checks,
            'limits':'Conservative AABB checks; contact footprints are declared interfaces requiring visual review. Shells and ancestor pairs require visual review. No physics certification.'}


def audit_parametric_scene(plan, *, check_spatial=True):
    """Measure available aDSL parts before render; unresolved rigid meshes use envelopes."""
    from planning_diagnostics import Diagnostics
    diagnostics=Diagnostics('placement')
    envelopes,poses=_planned_envelopes_and_poses(plan,diagnostics=diagnostics)
    bounds=copy.deepcopy(envelopes)
    receipts={}
    for obj in plan.objects:
        if obj.source.mode!='parametric' or obj.id not in poses:continue
        evidence={}
        _,low,high=compile_local_geometry(plan,obj,evidence=evidence)
        yaw,origin=poses[obj.id]
        bounds[obj.id]=_transform_local_bounds([low,high],yaw,origin)
        receipts[obj.id]={'world_rotation_deg':yaw,'world_origin':np.asarray(origin).tolist(),
                          'internal_geometry':evidence}
    return judge_general_geometry(plan,bounds,receipts,check_spatial=check_spatial,
                                  placement_state=(envelopes,poses,diagnostics))


def audit_exported_support_contacts(plan: GeneralPlan, bounds: dict, contacts: dict, *,
                                    receipts=None, measured_parts=None, tolerance=.015):
    """Audit measured Blender contact patches for rigid generated/retrieved objects.

    The planning footprint is an interface contract, while the generated mesh
    supplies the evidence.  The audit deliberately allows a smaller real base
    (common for an urn, lamp or plant with an overhanging envelope), but rejects
    an overhanging or displaced base that the declared support cannot contain.
    It also checks that the mesh is not silently far smaller than its declared
    object envelope after uniform asset preparation.
    """
    findings=[];reports={}
    by_id={obj.id:obj for obj in plan.objects}
    receipts=receipts or {};measured_parts=measured_parts or {}

    def fail(ids, evidence, target='layout'):
        findings.append({'component_ids':list(ids),'repair_target':target,'evidence':evidence})

    for obj in plan.objects:
        needs_contact=obj.source.mode!='parametric' and (obj.mount in {'top','bottom'} or obj.grounded)
        if not needs_contact:
            continue
        report={'component_id':obj.id,'mount':obj.mount,'grounded':obj.grounded}
        item=contacts.get(obj.id)
        if not isinstance(item,dict):
            fail([obj.id],'Missing Blender-measured support contact evidence for a rigid external component.')
            reports[obj.id]=report
            continue
        try:
            local=np.asarray(item['local_bounds'],dtype=float)
            world=np.asarray(item['world_bounds'],dtype=float)
            measured=np.asarray(item['measured_footprint'],dtype=float)
            if local.shape!=(2,3) or world.shape!=(2,3) or measured.shape!=(2,):
                raise ValueError('invalid contact record shape')
            if (not np.isfinite(local).all() or not np.isfinite(world).all() or
                    not np.isfinite(measured).all() or np.any(measured<=0)):
                raise ValueError('nonfinite or nonpositive contact record')
        except (KeyError,TypeError,ValueError):
            fail([obj.id],'Invalid Blender-measured support contact evidence.')
            reports[obj.id]=report
            continue
        size=object_size(plan,obj)
        actual=np.asarray(bounds.get(obj.id, [local[0],local[1]]),dtype=float)
        if actual.shape!=(2,3) or not np.isfinite(actual).all() or np.any(actual[1]<=actual[0]):
            fail([obj.id],'Missing or invalid measured external component bounds.')
            reports[obj.id]=report
            continue
        actual_size=actual[1]-actual[0]
        component_bounds=item.get('component_local_bounds')
        if component_bounds is None and obj.id in receipts:
            component_bounds=receipts[obj.id].get('local_bounds')
        if component_bounds is None:
            component_bounds=[actual[0].tolist(),actual[1].tolist()]
        component_bounds=np.asarray(component_bounds,dtype=float)
        if (component_bounds.shape!=(2,3) or not np.isfinite(component_bounds).all() or
                np.any(component_bounds[1]<=component_bounds[0])):
            fail([obj.id],'Invalid measured local component envelope.','decomposition')
            reports[obj.id]=report
            continue
        component_size=component_bounds[1]-component_bounds[0]
        utilization=component_size/size
        report.update(local_bounds=local.tolist(),world_bounds=world.tolist(),
                      component_local_bounds=component_bounds.tolist(),
                      measured_component_size=component_size.tolist(),
                      measured_footprint=measured.tolist(),declared_size=size.tolist(),
                      size_utilization=utilization.tolist(),
                      minimum_size_utilization=MIN_COMPONENT_ENVELOPE_UTILIZATION,
                      declared_footprint=None if obj.support_footprint is None else
                          [scalar(v,{**plan.parameters,**obj.parameters,**dict(zip(('w','d','h'),size))})
                           for v in obj.support_footprint])
        if np.any(component_size>size+tolerance):
            fail([obj.id],f'Measured local external bounds {component_size.tolist()} exceed declared envelope {size.tolist()}.','decomposition')
        if np.any(utilization<MIN_COMPONENT_ENVELOPE_UTILIZATION):
            fail([obj.id],f'Measured local external bounds occupy less than {MIN_COMPONENT_ENVELOPE_UTILIZATION:.0%} of declared envelope {size.tolist()}: {utilization.tolist()}.','decomposition')

        footprint=report['declared_footprint'] or size[:2].tolist()
        footprint=np.asarray(footprint,dtype=float)
        center=(local[0,:2]+local[1,:2])/2
        report['contact_center_local_xy']=center.tolist()
        report['contact_utilization']=(measured/footprint).tolist()
        if np.any(measured>footprint+tolerance):
            fail([obj.id],f'Measured support footprint {measured.tolist()} exceeds declared contact {footprint.tolist()}.','decomposition')
        if np.any(measured<footprint*.5):
            fail([obj.id],f'Measured support footprint {measured.tolist()} is less than half the declared contact {footprint.tolist()}; revise the interface or component envelope.','decomposition')
        if np.any(np.abs(center)>footprint/2+tolerance):
            fail([obj.id],f'Measured support contact is off the declared local footprint: {center.tolist()} within {footprint.tolist()}.')

        if obj.mount=='top':
            if not obj.parent or obj.parent not in bounds:
                fail([obj.id,obj.parent] if obj.parent else [obj.id],'Top-mounted external component has no measured parent support.')
            else:
                parent_bounds=np.asarray(_support_surface_bounds(plan,obj,bounds,receipts,measured_parts),dtype=float)
                support_z=float(parent_bounds[1,2])
                contact_z=float(world[0,2])
                report.update(support_surface_world_bounds=parent_bounds.tolist(),
                              support_plane_z_m=support_z,measured_contact_z_m=contact_z,
                              vertical_gap_m=contact_z-support_z,
                              contact_center_world_xy=((world[0,:2]+world[1,:2])/2).tolist(),
                              support_center_world_xy=((parent_bounds[0,:2]+parent_bounds[1,:2])/2).tolist())
                params={**plan.parameters,**obj.parameters,**dict(zip(('w','d','h'),size))}
                offset=vec(obj.position,params)
                parent_angle=(receipts.get(obj.parent,{}).get('world_rotation_deg')
                              if receipts else None)
                if parent_angle is None:
                    _,poses=_planned_envelopes_and_poses(plan)
                    parent_angle=poses[obj.parent][0]
                angle=math.radians(float(parent_angle))
                rotation=np.array([[math.cos(angle),-math.sin(angle)],
                                   [math.sin(angle),math.cos(angle)]])
                support_center=(parent_bounds[0,:2]+parent_bounds[1,:2])/2
                expected_contact_center=support_center+rotation@offset[:2]
                measured_contact_center=(world[0,:2]+world[1,:2])/2
                center_error=measured_contact_center-expected_contact_center
                report.update(expected_contact_center_world_xy=expected_contact_center.tolist(),
                              contact_center_error_world_xy=center_error.tolist())
                if np.any(np.abs(center_error)>tolerance):
                    fail([obj.id,obj.parent],f'Measured contact center {measured_contact_center.tolist()} differs from planned support offset target {expected_contact_center.tolist()}.')
                if abs(contact_z-support_z)>tolerance:
                    fail([obj.id,obj.parent],f'Measured bottom contact Z {contact_z:.4f} does not meet declared support top Z {support_z:.4f}.')
                if np.any(world[0,:2]<parent_bounds[0,:2]-tolerance) or np.any(world[1,:2]>parent_bounds[1,:2]+tolerance):
                    fail([obj.id,obj.parent],f'Measured contact patch {world[:,:2].tolist()} falls outside declared support surface {parent_bounds[:,:2].tolist()}.')
        elif obj.mount=='bottom':
            if not obj.parent or obj.parent not in bounds:
                fail([obj.id,obj.parent] if obj.parent else [obj.id],'Bottom-mounted external component has no measured parent support.')
            else:
                parent_bounds=np.asarray(_support_surface_bounds(plan,obj,bounds,receipts,measured_parts),dtype=float)
                support_z=float(parent_bounds[0,2])
                contact_z=float(world[1,2])
                report.update(support_surface_world_bounds=parent_bounds.tolist(),
                              support_plane_z_m=support_z,measured_contact_z_m=contact_z,
                              vertical_gap_m=contact_z-support_z,
                              contact_center_world_xy=((world[0,:2]+world[1,:2])/2).tolist(),
                              support_center_world_xy=((parent_bounds[0,:2]+parent_bounds[1,:2])/2).tolist())
                params={**plan.parameters,**obj.parameters,**dict(zip(('w','d','h'),size))}
                offset=vec(obj.position,params)
                parent_angle=(receipts.get(obj.parent,{}).get('world_rotation_deg')
                              if receipts else None)
                if parent_angle is None:
                    _,poses=_planned_envelopes_and_poses(plan)
                    parent_angle=poses[obj.parent][0]
                angle=math.radians(float(parent_angle))
                rotation=np.array([[math.cos(angle),-math.sin(angle)],
                                   [math.sin(angle),math.cos(angle)]])
                support_center=(parent_bounds[0,:2]+parent_bounds[1,:2])/2
                expected_contact_center=support_center+rotation@offset[:2]
                measured_contact_center=(world[0,:2]+world[1,:2])/2
                center_error=measured_contact_center-expected_contact_center
                report.update(expected_contact_center_world_xy=expected_contact_center.tolist(),
                              contact_center_error_world_xy=center_error.tolist())
                if np.any(np.abs(center_error)>tolerance):
                    fail([obj.id,obj.parent],f'Measured contact center {measured_contact_center.tolist()} differs from planned support offset target {expected_contact_center.tolist()}.')
                if abs(contact_z-support_z)>tolerance:
                    fail([obj.id,obj.parent],f'Measured top contact Z {contact_z:.4f} does not meet declared support bottom Z {support_z:.4f}.')
                if np.any(world[0,:2]<parent_bounds[0,:2]-tolerance) or np.any(world[1,:2]>parent_bounds[1,:2]+tolerance):
                    fail([obj.id,obj.parent],f'Measured contact patch {world[:,:2].tolist()} falls outside declared support surface {parent_bounds[:,:2].tolist()}.')
        elif obj.grounded and abs(float(world[0,2]))>tolerance:
            fail([obj.id],f'Measured floor contact Z {float(world[0,2]):.4f} is not at zero.')
        reports[obj.id]=report
    from surface_mounting import audit_measured
    mounting_findings,mounting_reports=audit_measured(plan,contacts,receipts,measured_parts,tolerance)
    findings.extend(mounting_findings);reports.update(mounting_reports)
    return {'status':'passed' if not findings else 'needs_revision','findings':findings,'components':reports,
            'limits':'Measured evaluated Blender vertices are checked against declared envelopes and support interfaces; this is not a stability or dynamics certification.'}


# Geometry beyond size/center/rotation that an edit can change, per primitive.
SHAPE_KEYS={'sweep':('local_path','radii'),'lathe':('profile',)}


def _same(a,b):
    a,b=np.asarray(a,dtype=float),np.asarray(b,dtype=float)
    return a.shape==b.shape and np.allclose(a,b,rtol=0,atol=1e-8)


def edit_general(plan: GeneralPlan, parameter: str, delta: float, *, require_effect=True, check_layout=True):
    edited = plan.model_copy(deep=True)
    if parameter not in edited.parameters: raise ValueError(f'unknown global parameter {parameter}')
    edited.parameters[parameter] = scalar(edited.parameters[parameter]+delta,{})
    edited=GeneralPlan.model_validate(edited.model_dump())
    effect=False
    before_bounds=planned_envelope_bounds(plan)
    after_bounds=planned_envelope_bounds(edited)
    effect=any(not np.allclose(before_bounds[id],after_bounds[id],rtol=0,atol=1e-8) for id in before_bounds)
    for before,after in zip(plan.objects,edited.objects):
        if before.rigidity == 'rigid' and not np.allclose(object_size(plan,before),object_size(edited,after)):
            raise ValueError(f'Edit would resize rigid component {before.id}; replace explicitly instead')
        if before.source.mode=='parametric':
            original={};changed={}
            compile_local_geometry(plan,before,evidence=original)
            compile_local_geometry(edited,after,evidence=changed)
            for part in before.parts:
                a,b=original['parts'][part.id],changed['parts'][part.id]
                shape_keys=SHAPE_KEYS.get(part.primitive,())
                if any(not _same(a[k],b[k]) for k in ('size','center','rotation')+shape_keys):
                    effect=True
                keys=('size','center','rotation') if before.rigidity=='rigid' else ('size','rotation')
                if (before.rigidity=='rigid' or part.rigidity=='rigid') and any(
                    not _same(a[k],b[k]) for k in keys+shape_keys):
                    raise ValueError(f'Edit would deform rigid internal part {before.id}/{part.id}')
    if require_effect and not effect:
        raise ValueError(f'Edit {parameter} {delta:+} has no measured geometry effect. '
                         'Connect the requested editable dimension to actual part sizes/positions; '
                         'zero-multiplied or cancelling parameter references do not implement editability.')
    if check_layout:
        preflight=judge_general_geometry(edited,after_bounds)
        if preflight['status'] != 'passed':raise ValueError('Edit violates scene relations: '+str(preflight['findings']))
    return edited
