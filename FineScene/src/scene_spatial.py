"""Frozen scene-use contracts and conservative, backend-independent layout checks.

These checks reserve space for people and activities. They do not certify building
codes or replace image review. Solid AABBs are conservative; architectural shells
need separate opening/mesh evidence and are excluded from occupancy checks.
"""
from __future__ import annotations

import math
import re
from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator
from planning_diagnostics import Diagnostics
from scene_composition import GroupBinding, LinkBinding, OpenSpace, ExperienceView

SHELL_SLAB_THICKNESS_M = 0.05


class SpatialModel(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False, validate_default=True)


class Zone(SpatialModel):
    id: str
    purpose: str = Field(min_length=1)
    center: tuple[float, float]
    size: tuple[float, float]
    component_ids: list[str] = Field(default_factory=list)


class AccessArea(SpatialModel):
    id: str
    owner_id: str
    purpose: str = Field(min_length=1)
    # Owner-local XY coordinates; front is -Y before applying world yaw.
    center: tuple[float | str, float | str]
    size: tuple[float, float]
    height: float = Field(default=1.9, gt=0, le=10)
    side: Literal['front','back','left','right'] | None = None
    along_face: float | str = 0


def canonical_access_geometry(contract):
    """Lower semantic owner-local sides to editable face-relative coordinates."""
    result=contract.model_copy(deep=True)
    for area in result.access_areas:
        if area.side is None:continue  # compatibility with earlier plans
        if area.side in {'front','back'}:
            sign=-1 if area.side=='front' else 1
            area.center=(area.along_face,f'{sign}*(d/2+{area.size[1]/2+.025:.6f})')
        else:
            sign=-1 if area.side=='left' else 1
            area.center=(f'{sign}*(w/2+{area.size[0]/2+.025:.6f})',area.along_face)
    return result


class Opening(SpatialModel):
    id: str
    kind: Literal['entrance', 'window']
    wall: Literal['x_min', 'x_max', 'y_min', 'y_max']
    offset: float
    width: float = Field(gt=0, le=100)
    sill_height: float = Field(default=0, ge=0)
    height: float = Field(gt=0, le=100)
    approach_depth: float = Field(gt=0, le=100)
    purpose: str = Field(min_length=1)
    fitted_component_ids: list[str] = Field(default_factory=list, max_length=16)
    # Hinged leaves stay associated with this entrance while parked beside it.
    swing_leaf_ids: list[str] = Field(default_factory=list, max_length=4)
    fixture_projection_m: float = Field(default=0.3, ge=0, le=2)


class CirculationRoute(SpatialModel):
    id: str
    start: str
    end: str
    points: list[tuple[float, float]] = Field(min_length=2, max_length=32)
    width: float = Field(gt=0, le=20)
    height: float = Field(default=1.9, gt=0, le=10)
    purpose: str = Field(min_length=1)


class SpatialRelation(SpatialModel):
    id: str
    kind: Literal['facing', 'distance', 'left_of', 'right_of', 'in_front_of', 'behind']
    a: str
    b: str
    minimum_m: float = Field(default=0, ge=0, le=100)
    maximum_m: float | None = Field(default=None, ge=0, le=100)
    tolerance_deg: float = Field(default=30, gt=0, le=90)
    reason: str = Field(min_length=1)
    design_relation_id: str | None = Field(default=None, pattern=r'^[A-Za-z][A-Za-z0-9_-]{0,63}$')

    @model_validator(mode='after')
    def interval(self):
        if self.maximum_m is not None and self.maximum_m < self.minimum_m:
            raise ValueError('Spatial distance maximum is smaller than minimum')
        return self


class OpeningRequirement(SpatialModel):
    kind: Literal['entrance', 'window']
    wall: Literal['x_min', 'x_max', 'y_min', 'y_max'] | None = None
    offset: float | None = None
    width: float | None = Field(default=None, gt=0)


class SceneUseRequirements(SpatialModel):
    enclosure: Literal['enclosed', 'open', 'unspecified'] = 'unspecified'
    extent: tuple[float, float, float] | None = None
    minimum_route_width_m: float | None = Field(default=None, gt=0)
    openings: list[OpeningRequirement] = Field(default_factory=list)


class SpatialContract(SpatialModel):
    zones: list[Zone] = Field(min_length=1, max_length=40)
    access_areas: list[AccessArea] = Field(default_factory=list, max_length=80)
    openings: list[Opening] = Field(min_length=1, max_length=40)
    routes: list[CirculationRoute] = Field(default_factory=list, max_length=160)
    relations: list[SpatialRelation] = Field(default_factory=list, max_length=160)
    assumptions: list[str] = Field(default_factory=list, max_length=40)
    requirements: SceneUseRequirements | None = None
    composition_groups: list[GroupBinding] = Field(default_factory=list)
    composition_links: list[LinkBinding] = Field(default_factory=list)
    open_spaces: list[OpenSpace] = Field(default_factory=list)
    experience_views: list[ExperienceView] = Field(default_factory=list)


def normalize_facing_relations(contract):
    """Keep facing semantic-only; requested spacing remains a distinct distance relation."""
    result=contract.model_copy(deep=True)
    changes=[]
    for relation in result.relations:
        if relation.kind!='facing' or (relation.minimum_m==0 and relation.maximum_m is None):
            continue
        changes.append({'id':relation.id,
            'before':{'minimum_m':relation.minimum_m,'maximum_m':relation.maximum_m},
            'after':{'minimum_m':0,'maximum_m':None},
            'reason':'Facing expresses orientation only; explicit metric spacing is a separate distance relation.'})
        relation.minimum_m=0
        relation.maximum_m=None
    return result,changes


def normalize_corner_based_spatial_frame(contract, extent):
    """Translate a consistently corner-authored room plan into the required centered frame."""
    half=np.asarray(extent[:2],dtype=float)/2
    full=np.asarray(extent[:2],dtype=float)
    world_points=[]
    for zone in contract.zones:
        world_points.append(np.asarray(zone.center,dtype=float))
    for space in contract.open_spaces:
        world_points.append(np.asarray(space.center,dtype=float))
    for route in contract.routes:
        world_points.extend(np.asarray(point,dtype=float) for point in route.points)
    for view in contract.experience_views:
        world_points.extend((np.asarray(view.position[:2],dtype=float),
                             np.asarray(view.target[:2],dtype=float)))
    aperture_offsets=[]
    for opening in contract.openings:
        tangent_axis=1 if opening.wall.startswith('x') else 0
        aperture_offsets.append((opening.offset,tangent_axis,opening.width))
    if not world_points:
        return contract,None
    # A room-center plan may legitimately occupy only the positive quadrant.
    # Normalize only when the whole authored world frame fits [0, extent] and
    # subtracting half extents resolves every typed room-boundary violation.
    corner_values=[value for point in world_points for value in point]
    corner_values.extend(value for value,_,_ in aperture_offsets)
    if (min(corner_values)<-.015 or
            any(value>full[axis]+.015 for point in world_points for axis,value in enumerate(point)) or
            any(value>full[axis]+.015 for value,axis,_ in aperture_offsets)):
        return contract,None

    def violations(shift):
        errors=set()
        for zone in contract.zones:
            center=np.asarray(zone.center,dtype=float)-shift
            size=np.asarray(zone.size,dtype=float)
            if np.any(center-size/2 < -half-.015) or np.any(center+size/2 > half+.015):
                errors.add('zones')
        for space in contract.open_spaces:
            center=np.asarray(space.center,dtype=float)-shift
            size=np.asarray(space.size,dtype=float)
            if np.any(center-size/2 < -half-.015) or np.any(center+size/2 > half+.015):
                errors.add('open_spaces')
        for route in contract.routes:
            radius=route.width/2
            for point in route.points:
                center=np.asarray(point,dtype=float)-shift
                if np.any(center < -half+radius-.015) or np.any(center > half-radius+.015):
                    errors.add('routes')
                    break
        for view in contract.experience_views:
            for point in (view.position[:2],view.target[:2]):
                center=np.asarray(point,dtype=float)-shift
                if np.any(center < -half-.015) or np.any(center > half+.015):
                    errors.add('experience_views')
                    break
        for value,axis,width in aperture_offsets:
            tangent_half=half[axis]
            offset=value-shift[axis]
            if offset < -tangent_half+width/2-.015 or offset > tangent_half-width/2+.015:
                errors.add('opening_offsets')
        return errors

    before=violations(np.zeros(2))
    after=violations(half)
    if len(before)<2 or after:
        return contract,None
    requirements=contract.requirements
    if requirements is not None:
        for requirement in requirements.openings:
            if requirement.offset is None:
                continue
            if requirement.wall is None:
                return contract,None
            tangent_axis=1 if requirement.wall.startswith('x') else 0
            matches=[opening for opening in contract.openings
                     if opening.kind==requirement.kind and opening.wall==requirement.wall and
                     (requirement.width is None or abs(opening.width-requirement.width)<=.015)]
            if not any(abs(opening.offset-half[tangent_axis]-requirement.offset)<=.015
                       for opening in matches):
                return contract,None
    result=contract.model_copy(deep=True)
    def shifted_xy(point):
        return tuple(float(value-half[axis]) for axis,value in enumerate(point))
    result.zones=[item.model_copy(update={'center':shifted_xy(item.center)})
                  for item in result.zones]
    result.open_spaces=[item.model_copy(update={'center':shifted_xy(item.center)})
                        for item in result.open_spaces]
    result.routes=[item.model_copy(update={'points':[
        shifted_xy(point) for point in item.points]}) for item in result.routes]
    result.experience_views=[item.model_copy(update={
        'position':(*shifted_xy(item.position[:2]),float(item.position[2])),
        'target':(*shifted_xy(item.target[:2]),float(item.target[2]))})
        for item in result.experience_views]
    result.openings=[item.model_copy(update={'offset':float(item.offset-half[
        1 if item.wall.startswith('x') else 0])}) for item in result.openings]
    return result,{'transform_xy_m':[-float(half[0]),-float(half[1])],
        'violating_families_before':sorted(before),'violating_families_after':[],
        'basis':'All typed world coordinates fit the [0, extent] corner frame; a uniform half-extent translation resolves every room-boundary violation.',
        'owner_local_access_centers_preserved':True,
        'user_requirements_preserved':True,
        'explicit_opening_offsets_match_after_transform':True}


def anchor_centered_architectural_shells(outline, layout):
    """Pin a full-room shell envelope to the centered scene frame before solving furniture poses."""
    from general_scene import scalar
    result=layout.model_copy(deep=True)
    objects={obj.id:obj for obj in outline.objects}
    changes=[]
    placements=[]
    for placement in result.placements:
        obj=objects.get(placement.id)
        if obj is None or obj.parent is not None or obj.collision_role!='shell':
            placements.append(placement)
            continue
        try:
            size=np.asarray([scalar(value,outline.parameters) for value in obj.dimensions[:2]],dtype=float)
            position=np.asarray([scalar(value,outline.parameters) for value in placement.position],dtype=float)
        except (ValueError,KeyError,TypeError,ZeroDivisionError,SyntaxError,OverflowError):
            placements.append(placement)
            continue
        extent=np.asarray(outline.extent[:2],dtype=float)
        matches=np.all(np.abs(size-extent)<=.015) or np.all(np.abs(size-extent[::-1])<=.015)
        if not matches:
            placements.append(placement)
            continue
        target=np.asarray([0.,0.,-SHELL_SLAB_THICKNESS_M])
        if np.max(np.abs(position-target))<=.015:
            placements.append(placement)
            continue
        updated=placement.model_copy(update={'position':tuple(target)})
        placements.append(updated)
        changes.append({'component_id':obj.id,'before_position':list(placement.position),
            'after_position':list(updated.position),
            'reason':'A full-room shell is centered on the scene frame and its 0.05m floor slab top is anchored to the occupied floor datum Z=0.'})
    if not changes:
        return result,None
    return result.model_copy(update={'placements':placements}),{'changes':changes,
        'basis':'Root shell envelope matches the complete scene extent in local XY.',
        'yaw_preserved':True,'occupied_floor_datum_z':0.,
        'slab_thickness_m':SHELL_SLAB_THICKNESS_M}


def canonical_spatial_bounds(contract,extent):
    """Copy the proposal without hiding out-of-bounds planning errors.

    Spatial bounds are constraints, not geometry to clip into a different
    composition. The validators report the original coordinates so the Agent
    can repair all affected zones and routes coherently.
    """
    return contract.model_copy(deep=True)


def validate_zone_capacity(contract, objects, parameters, edit_checks):
    """Detect pose-independent impossibility before spending on position repairs."""
    from general_scene import scalar
    diagnostics=Diagnostics('spatial_zone_capacity')
    scenarios=[('baseline',dict(parameters))]
    for check in edit_checks:
        item=check.model_dump() if hasattr(check,'model_dump') else check
        changed=dict(parameters);changed[item['parameter']]+=item['delta']
        scenarios.append((item['parameter'],changed))
    by_id={obj.id:obj for obj in objects}
    for index,zone in enumerate(contract.zones):
        available=zone.size[0]*zone.size[1]
        for component_id in zone.component_ids:
            obj=by_id.get(component_id)
            if obj is None or obj.parent is not None or obj.collision_role=='shell':continue
            for scenario,values in scenarios:
                try:
                    size=[scalar(v,values) for v in obj.dimensions]
                except (ValueError,KeyError,TypeError,ZeroDivisionError,SyntaxError):continue
                area=size[0]*size[1]
                # Rotation preserves footprint area; no translation/yaw can fix
                # this necessary condition. Passing it is not a layout proof.
                if area>available+1e-6:
                    diagnostics.add('zone_capacity',
                        f'{component_id}: footprint area {area:.4f}m2 exceeds zone {zone.id} area '
                        f'{available:.4f}m2 after {scenario}; no position/yaw can fix it. '
                        'Revise the spatial zone before layout; preserve object dimensions and edits.',
                        paths=[f'/zones/{index}/size'],component_ids=[component_id],scenario=scenario,
                        measured=area,expected={'maximum_area_m2':available})
    diagnostics.raise_if_any()
    return contract


def _rotation(yaw):
    angle = math.radians(yaw)
    return np.array([[math.cos(angle), -math.sin(angle)], [math.sin(angle), math.cos(angle)]])


def _rectangle(center, size, yaw=0):
    half = np.asarray(size, dtype=float) / 2
    return np.array([[-half[0], -half[1]], [half[0], -half[1]],
                     [half[0], half[1]], [-half[0], half[1]]]) @ _rotation(yaw).T + center


def _overlap(a, b):
    for polygon in (a, b):
        for index in range(len(polygon)):
            edge = polygon[(index + 1) % len(polygon)] - polygon[index]
            normal = np.array([-edge[1], edge[0]])
            pa, pb = a @ normal, b @ normal
            if min(pa.max(), pb.max()) - max(pa.min(), pb.min()) <= 1e-8:
                return False
    return True


def _point_inside(point, polygon):
    edges = np.roll(polygon, -1, axis=0) - polygon
    offsets = np.asarray(point) - polygon
    cross = edges[:, 0] * offsets[:, 1] - edges[:, 1] * offsets[:, 0]
    return bool(np.all(cross >= -1e-8) or np.all(cross <= 1e-8))


def _point_segment_distance(p, a, b):
    delta = b - a
    if np.dot(delta, delta) < 1e-16:
        return float(np.linalg.norm(p-a))
    t = np.clip(np.dot(p - a, delta) / np.dot(delta, delta), 0, 1)
    return float(np.linalg.norm(p - a - t * delta))


def _segment_polygon_distance(a, b, polygon):
    if _point_inside(a, polygon) or _point_inside(b, polygon):
        return 0.
    best = float('inf')
    for c, d in zip(polygon, np.roll(polygon, -1, axis=0)):
        matrix = np.column_stack((b - a, c - d))
        if abs(np.linalg.det(matrix)) > 1e-12:
            t, u = np.linalg.solve(matrix, c - a)
            if 0 <= t <= 1 and 0 <= u <= 1:
                return 0.
        best = min(best, _point_segment_distance(c, a, b), _point_segment_distance(d, a, b),
                   _point_segment_distance(a, c, d), _point_segment_distance(b, c, d))
    return best


def opening_polygon(opening, extent):
    axis = 0 if opening.wall.startswith('x') else 1
    sign = -1 if opening.wall.endswith('min') else 1
    center = np.zeros(2)
    center[axis] = sign * (extent[axis] / 2 - opening.approach_depth / 2)
    center[1 - axis] = opening.offset
    size = [opening.width, opening.width]
    size[axis] = opening.approach_depth
    return _rectangle(center, size)


def validate_spatial_contract(contract, objects, extent):
    """Validate the frozen graph before positions or detailed recipes are chosen."""
    diagnostics = Diagnostics('spatial_contract')
    def fail(code, message, *paths, measured=None, expected=None):
        diagnostics.add(code, message, paths=paths, measured=measured, expected=expected)
    by_id = {o.id: o for o in objects}
    requirements = contract.requirements
    if requirements is not None:
        if requirements.enclosure=='enclosed' and not any(o.collision_role=='shell' for o in objects):
            fail('missing_shell', 'An enclosed scene requires explicit architectural shell geometry; an extent and opening record alone do not create a room', '/requirements/enclosure')
        if requirements.extent is not None and not np.allclose(extent, requirements.extent, rtol=0, atol=.001):
            fail('extent', 'Scene extent differs from frozen user dimensions', '/requirements/extent')
        if requirements.minimum_route_width_m is not None:
            for i, route in enumerate(contract.routes):
                if route.width < requirements.minimum_route_width_m - .001:
                    fail('route_width', f'{route.id}: Route width weakens the frozen user minimum', f'/routes/{i}/width')
        remaining = list(contract.openings)
        for required in requirements.openings:
            matches = [o for o in remaining if o.kind == required.kind and
                       (required.wall is None or o.wall == required.wall) and
                       (required.offset is None or abs(o.offset - required.offset) <= .001) and
                       (required.width is None or abs(o.width - required.width) <= .001)]
            if not matches:
                fail('required_opening', 'Missing opening specified in frozen user requirements: ' + str(required.model_dump()), '/openings')
            else:
                remaining.remove(matches[0])
    known = set(by_id)
    groups = [contract.zones, contract.access_areas, contract.openings, contract.routes, contract.relations]
    ids = [item.id for group in groups for item in group]
    if len(ids) != len(set(ids)) or any(not value.strip() for value in ids):
        fail('spatial_ids', 'Spatial contract IDs must be nonempty and unique across all groups', '/zones', '/access_areas', '/openings', '/routes', '/relations')
    half = np.asarray(extent[:2]) / 2
    roots = {o.id for o in objects if not (getattr(o, 'parent', None) or getattr(o, 'support', None))
             and getattr(o,'collision_role','solid')!='shell'}
    membership = []
    for i, zone in enumerate(contract.zones):
        path = f'/zones/{i}'
        if any(v <= 0 for v in zone.size):
            fail('zone_size', f'{zone.id}: zone dimensions must be positive', path+'/size')
        polygon = _rectangle(zone.center, zone.size)
        if np.any(polygon < -half - .001) or np.any(polygon > half + .001):
            bounds=np.asarray([polygon.min(axis=0),polygon.max(axis=0)])
            scene_bounds=np.asarray([-half,half])
            fail('zone_extent',
                 f'{zone.id}: scene-centered bounds {bounds.tolist()} exceed room bounds '
                 f'{scene_bounds.tolist()} for full extent {list(extent[:2])}m. '
                 'Revise the zone center/size in the room-center coordinate frame; do not offset by half the extent.',
                 path+'/center', path+'/size',measured=bounds.tolist(),expected=scene_bounds.tolist())
        if not set(zone.component_ids) <= known or len(zone.component_ids) != len(set(zone.component_ids)):
            fail('zone_members', f'{zone.id}: invalid zone component IDs', path+'/component_ids')
        membership.extend(zone.component_ids)
    invalid_membership={root:membership.count(root) for root in roots if membership.count(root)!=1}
    if invalid_membership:
        fail('zone_membership', 'Every activity/furnishing root needs exactly one primary spatial zone; enclosing architecture spans zones. Invalid root counts: '+
             str(invalid_membership)+'. Keep circulation-only overlay zones empty instead of duplicating furniture assignments.', '/zones')
    for i, area in enumerate(contract.access_areas):
        if area.owner_id not in known or any(v <= 0 for v in area.size):
            fail('access_area', f'{area.id}: invalid access owner or nonpositive size', f'/access_areas/{i}')
        elif getattr(by_id[area.owner_id],'collision_role','solid')=='shell':
            fail('shell_access_area',f'{area.id}: an architectural shell has no dedicated empty operator area. '
                 'Use entrance/window approach openings and routes; remove this artificial whole-room '
                 'access area and reconnect its routes to actual functional access endpoints.',
                 f'/access_areas/{i}','/routes')
    for i, obj in enumerate(objects):
        if getattr(obj, 'quantity', 1) != 1:
            fail('quantity', 'Spatial contracts require explicit individual component IDs, not quantity groups', '/zones')
        if obj.id in roots and getattr(obj, 'collision_role', 'solid') == 'solid' and getattr(obj, 'access_required', True):
            if not any(area.owner_id == obj.id for area in contract.access_areas):
                fail('missing_access', f'{obj.id}: missing required functional access area', '/access_areas')
    for i, opening in enumerate(contract.openings):
        path = f'/openings/{i}'
        polygon = opening_polygon(opening, extent)
        if np.any(polygon < -half - .001) or np.any(polygon > half + .001):
            axis=0 if opening.wall.startswith('x') else 1
            tangent=1-axis
            tangent_half=float(half[tangent])
            legal_offset=[-tangent_half+opening.width/2,tangent_half-opening.width/2]
            wall_plane=(-1 if opening.wall.endswith('min') else 1)*float(half[axis])
            measured={'wall':opening.wall,'wall_plane_m':wall_plane,
                'offset_signed_tangent_m':float(opening.offset),'opening_width_m':float(opening.width),
                'opening_tangent_bounds_m':[float(opening.offset-opening.width/2),
                                             float(opening.offset+opening.width/2)],
                'legal_offset_interval_m':[float(v) for v in legal_offset],
                'approach_depth_m':float(opening.approach_depth),
                'room_extent_xy_m':[float(v) for v in extent[:2]]}
            fail('opening_extent',
                 f'{opening.id}: {opening.wall} opening offset {opening.offset:+.3f}m is a signed coordinate '
                 f'from room center along the wall, not a distance from a corner. For width {opening.width:.3f}m '
                 f'the legal interval is [{legal_offset[0]:+.3f},{legal_offset[1]:+.3f}]m; '
                 f'its approach depth {opening.approach_depth:.3f}m must also fit the room extent '
                 f'{list(extent[:2])}m. Keep the opening on the wall and recenter this plan in scene-centered XY.',
                 path,measured=measured,expected={'offset_signed_tangent_m_inclusive':legal_offset,
                                                  'wall_plane_m':wall_plane})
        if opening.sill_height + opening.height > extent[2] + .001:
            fail('opening_height', f'{opening.id}: opening exceeds scene height', path+'/sill_height', path+'/height')
        if opening.kind == 'entrance' and opening.sill_height > .015:
            fail('entrance_floor', f'{opening.id}: walkable entrance must start at floor level', path+'/sill_height')
        fitted=set(opening.fitted_component_ids)
        swing_leaves=set(opening.swing_leaf_ids)
        if len(fitted)!=len(opening.fitted_component_ids) or not fitted<=known:
            fail('opening_fixtures',f'{opening.id}: fitted_component_ids must be unique existing scene objects',
                 path+'/fitted_component_ids')
        if opening.kind!='window' and fitted:
            fail('opening_fixtures',f'{opening.id}: only windows may declare fitted window components',
                 path+'/fitted_component_ids')
        if (len(swing_leaves)!=len(opening.swing_leaf_ids) or not swing_leaves<=known or
                (opening.kind!='entrance' and swing_leaves)):
            fail('opening_swing_leaves',f'{opening.id}: swing_leaf_ids must be unique existing scene objects and may only be declared for an entrance',path+'/swing_leaf_ids')
        if fitted&swing_leaves:
            fail('opening_component_roles',f'{opening.id}: a component cannot be both a fitted window fixture and an entrance swing leaf',path+'/swing_leaf_ids',path+'/fitted_component_ids')
        if any(by_id[id].collision_role=='shell' for id in swing_leaves if id in by_id):
            fail('opening_swing_leaves',f'{opening.id}: swing leaves must be separate scene objects, not architectural shell parts',path+'/swing_leaf_ids')
        for leaf_id in sorted(swing_leaves & known):
            obj=by_id[leaf_id]
            # swing_leaf_ids rotates a whole component. Fixed doorway members
            # cannot share that transform, even if a recipe has an internal leaf.
            parts=getattr(obj,'internal_parts',None) or getattr(obj,'parts',())
            fixed=[]
            for part in parts:
                label=f'{part.id} {getattr(part,"semantic","")}'.casefold().replace('_',' ').replace('-',' ')
                if re.search(r'\b(?:jamb|lintel|door (?:head|header)|fixed (?:door )?frame)\b|门框|门楣',label):
                    fixed.append(part.id)
            if fixed:
                index=next(j for j,item in enumerate(objects) if item.id==leaf_id)
                diagnostics.add('opening_swing_assembly',
                    f'{leaf_id}: swing_leaf_ids rotates the whole component, but it contains fixed doorway '
                    f'members {fixed}. Split stationary jamb/header geometry from the moving leaf before freezing '
                    'decomposition, and bind only the moving leaf to the entrance. Position/yaw changes cannot '
                    'repair this representation; preserve the requested doorway and its geometry.',
                    paths=[f'/objects/{index}/internal_parts',path+'/swing_leaf_ids'],
                    component_ids=[leaf_id],measured={'fixed_part_ids':fixed,'opening_id':opening.id,
                        'repair_target':'decomposition'},
                    expected={'swing_leaf_scope':'Moving leaf and its attached hardware only; fixed frame stays at the opening.'})
        if any(by_id[id].collision_role=='shell' for id in fitted if id in by_id):
            fail('opening_fixtures',f'{opening.id}: list separate fitted objects; architectural shell parts must prove their own aperture',
                 path+'/fitted_component_ids')
    declared_swing_leaves={leaf for opening in contract.openings if opening.kind=='entrance'
                           for leaf in opening.swing_leaf_ids}
    likely_door_leaves=set()
    for obj in objects:
        if getattr(obj,'collision_role','solid')=='shell':continue
        object_id=obj.id.casefold()
        category=str(getattr(obj,'category','')).casefold()
        semantic=str(getattr(obj,'semantic','')).casefold()
        label=f'{obj.id} {semantic}'
        # Hardware descriptions often mention the panel they are mounted on
        # (for example, "handle mounted on the door leaf").  That context is
        # not evidence that the hardware itself is a swing leaf.  Prefer the
        # structured category/ID when available, and keep the text fallback
        # for legacy objects without those fields.
        hardware_tokens=('hardware','handle','knob','lever','hinge','lock','rose','closer')
        is_hardware=any(token in category or token in object_id for token in hardware_tokens)
        is_leaf=(not is_hardware and any(token in label for token in ('door leaf','door panel')))
        has_door_id=('door' in object_id and not is_hardware and
                     not any(token in object_id for token in ('frame','architrave')))
        if is_leaf or has_door_id:likely_door_leaves.add(obj.id)
    missing_swing_leaves=likely_door_leaves-declared_swing_leaves
    if missing_swing_leaves:
        fail('opening_swing_leaves','Associate every separate door leaf with its actual entrance in swing_leaf_ids; missing: '+
             str(sorted(missing_swing_leaves)),'/openings')
    entrances = {o.id for o in contract.openings if o.kind == 'entrance'}
    if not entrances:
        fail('missing_entrance', 'A scene spatial contract needs at least one entrance (declared as an assumption outdoors)', '/openings')
    nodes = entrances | {a.id for a in contract.access_areas}
    graph = {node: set() for node in nodes}
    for i, route in enumerate(contract.routes):
        path = f'/routes/{i}'
        valid_endpoints = route.start in nodes and route.end in nodes and route.start != route.end
        if not valid_endpoints:
            fail('route_endpoints', f'{route.id}: route endpoints must be distinct entrances/access areas', path+'/start', path+'/end')
        if any(np.linalg.norm(np.asarray(a) - b) < .001 for a, b in zip(route.points, route.points[1:])):
            fail('degenerate_route', f'{route.id}: degenerate route segment', path+'/points')
        points = np.asarray(route.points)
        centerline_low=-half+route.width/2
        centerline_high=half-route.width/2
        outside=np.any(points<centerline_low-.001,axis=1)|np.any(points>centerline_high+.001,axis=1)
        if np.any(outside):
            bad=[{'point_index':int(index),'xy_m':[float(v) for v in points[index]],
                  'outside_axes':[axis for axis,name in enumerate(('x','y'))
                                  if points[index,axis]<centerline_low[axis]-.001 or
                                     points[index,axis]>centerline_high[axis]+.001]}
                 for index in np.flatnonzero(outside)]
            measured={'route_id':route.id,'route_width_m':float(route.width),
                'scene_bounds_xy_m':[[-float(v) for v in half],[float(v) for v in half]],
                'legal_centerline_bounds_xy_m':[centerline_low.tolist(),centerline_high.tolist()],
                'out_of_bounds_points':bad}
            expected={'route_width_m':float(route.width),
                'every_centerline_point_inside_xy_m':[centerline_low.tolist(),centerline_high.tolist()]}
            fail('route_extent',
                f'{route.id}: {len(bad)} centerline point(s) are outside the legal centerline bounds '
                f'{measured["legal_centerline_bounds_xy_m"]} for route width {route.width:.3f}m.',
                path+'/points',path+'/width',measured=measured,expected=expected)
        if route.height > extent[2] + .015:
            fail('route_height', f'{route.id}: route headroom exceeds scene height', path+'/height')
        for node, point in ((route.start, route.points[0]), (route.end, route.points[-1])):
            opening = next((o for o in contract.openings if o.id == node), None)
            if opening:
                approach=opening_polygon(opening,extent)
                half_width=route.width/2
                point_array=np.asarray(point,dtype=float)
                axis=0 if opening.wall.startswith('x') else 1
                tangent=1-axis
                sign=-1 if opening.wall.endswith('min') else 1
                wall_plane=sign*half[axis]
                aperture=[opening.offset-opening.width/2,opening.offset+opening.width/2]
                route_half_width=half_width
                approach_fits=_point_inside(point_array,approach)
                width_fits=route.width<=opening.width+.001
                if not approach_fits or not width_fits:
                    signed_normal=float(-sign*(point_array[axis]-wall_plane))
                    clear_interval=[float(aperture[0]+route_half_width),float(aperture[1]-route_half_width)]
                    expected_normal=[0.0,float(opening.approach_depth)]
                    measured={
                        'route_id':route.id,
                        'opening_id':opening.id,
                        'endpoint_role':'start' if node==route.start else 'end',
                        'endpoint_xy_m':[float(x) for x in point_array],
                        'approach_bounds_xy_m':np.asarray([approach.min(axis=0),approach.max(axis=0)]).tolist(),
                        'route_width_m':float(route.width),
                        'route_half_width_m':float(route_half_width),
                        'opening_aperture_tangent_m':[float(x) for x in aperture],
                        'route_centerline_clear_tangent_m':clear_interval,
                        'opening_normal_axis':'x' if axis==0 else 'y',
                        'opening_wall_plane_m':float(wall_plane),
                        'endpoint_inward_distance_m':signed_normal,
                        'expected_endpoint_inward_distance_range_m':expected_normal,
                        'opening_approach_depth_m':float(opening.approach_depth),
                    }
                    expected={
                        'endpoint_inside_approach':True,
                        'route_width_m_maximum':float(opening.width),
                        'endpoint_centerline_tangent_m':clear_interval,
                        'endpoint_inward_distance_m':expected_normal,
                    }
                    fail('entrance_approach',
                        f'{route.id}: {measured["endpoint_role"]} endpoint at {measured["endpoint_xy_m"]} '
                        f'does not fit {node} approach. Route width {route.width:.3f}m '
                        f'(half-width {route_half_width:.3f}m), opening width {opening.width:.3f}m, '
                        f'clear centerline span {clear_interval}; endpoint inward distance '
                        f'{signed_normal:.3f}m, expected within {expected_normal}m.',
                        path+'/points',path+'/width',measured=measured,expected=expected)
        if valid_endpoints:
            graph[route.start].add(route.end)
            graph[route.end].add(route.start)
    reachable = set(entrances)
    pending = list(entrances)
    while pending:
        for target in graph[pending.pop()] - reachable:
            reachable.add(target)
            pending.append(target)
    if nodes - reachable:
        fail('disconnected_access', 'Access areas disconnected from entrances: ' + ', '.join(sorted(nodes - reachable)), '/routes')
    for i, relation in enumerate(contract.relations):
        if not {relation.a, relation.b} <= known or relation.a == relation.b:
            fail('relation_endpoints', f'{relation.id}: invalid spatial relation endpoints', f'/relations/{i}')
        elif relation.kind=='facing' and 'door' in relation.a.casefold():
            fail('door_facing_relation',
                 f'{relation.id}: a door leaf is oriented by its hinge/open state and clear opening, not by facing a destination object; remove this furniture-facing relation and keep the leaf beside the jamb.',
                 f'/relations/{i}')
        if relation.kind == 'distance' and relation.minimum_m > .001 and {relation.a, relation.b} <= known:
            labels=' '.join(f'{id} {getattr(by_id[id], "semantic", "")}' for id in (relation.a, relation.b)).casefold()
            reason=relation.reason.casefold().replace('-', ' ')
            visual_only=any(term in reason for term in (
                'visual separation', 'visual breathing', 'central open floor',
                'open floor between', 'visually separate'))
            seat_pullback=any(term in labels for term in ('chair','seat')) and any(
                term in labels for term in ('tatami','futon','bedding','sleep platform','bed')) and any(
                term in reason for term in ('pullback','pull back','pull out','pullout'))
            if visual_only or seat_pullback:
                intent='visual separation/open floor' if visual_only else 'chair pull-back'
                fail('nonfunctional_pairwise_gap',
                     f'{relation.id}: the {intent} is not a furniture-to-furniture distance. Use zones for '
                     'composition and access_areas/routes for human use; use minimum_m=0 or remove the inferred '
                     'gap unless the user explicitly requires this exact physical separation.',
                     f'/relations/{i}/minimum_m', '/relations')
    if len(roots) > 1 and not contract.relations:
        fail('missing_relations', 'Multiple scene roots need explicit functional spatial relations', '/relations')
    for i,space in enumerate(contract.open_spaces):
        lo=np.asarray(space.center)-np.asarray(space.size)/2;hi=lo+space.size
        if min(space.size)<=0 or np.any(lo < -half-.001) or np.any(hi > half+.001) or space.bottom+space.height>extent[2]+.015:
            fail('open_space_extent',f'{space.intent_id}: protected empty volume must fit actual scene bounds',f'/open_spaces/{i}')
    for i,view in enumerate(contract.experience_views):
        eye=np.asarray(view.position);target=np.asarray(view.target)
        if np.any(np.abs(eye[:2])>half) or not 0<eye[2]<extent[2] or np.linalg.norm(target-eye)<.1:
            fail('experience_view',f'{view.intent_id}: select a real accessible observation position and distinct target',f'/experience_views/{i}')
    diagnostics.raise_if_any()
    return contract


def distributed_architecture_envelope_ids(objects, extent, parameters=None):
    """Find room-spanning shell-mounted detail whose declared box is not occupied volume."""
    from general_scene import scalar
    by_id={obj.id:obj for obj in objects}
    extent=np.asarray(extent,dtype=float)
    result=set()
    for obj in objects:
        parent=by_id.get(getattr(obj,'parent',None))
        if (parent is None or getattr(parent,'collision_role',None)!='shell' or
                getattr(obj,'category',None)!='architecture' or not getattr(obj,'internal_parts',None)):
            continue
        try:
            size=np.asarray([scalar(value,parameters or {}) for value in obj.dimensions[:2]],dtype=float)
        except (ValueError,KeyError,TypeError,ZeroDivisionError,SyntaxError):
            continue
        if np.all(size>=extent[:2]*.9):result.add(obj.id)
    return result


def audit_spatial(contract, objects, extent, bounds, poses, *, shell_parts=None, parameters=None,
                  footprints=None, coarse_envelope_exempt_ids=(), local_positions=None):
    """Check either coarse envelopes or exported geometry against the same contract."""
    if contract is None:
        return {'status': 'not_applicable', 'findings': [], 'limits': 'No scene-use contract (legacy or single asset).'}
    findings, areas, route_records = [], {}, []
    half = np.asarray(extent[:2]) / 2
    objects_by_id={obj.id:obj for obj in objects}
    local_positions=local_positions or {}
    aperture_component_ids={id for opening in contract.openings
        for id in (*opening.fitted_component_ids,*opening.swing_leaf_ids)}
    def fail(ids, evidence, constraint_id, *, repair_target='layout', part_ids=None, measured=None,
             code=None, expected=None, paths=None):
        findings.append({'component_ids': list(ids), 'evidence': evidence,
                         'constraint_id': constraint_id, 'repair_target': repair_target,
                         **({'part_ids':part_ids} if part_ids else {}),
                         **({'code':code} if code else {}),
                         **({'expected':expected} if expected is not None else {}),
                         **({'paths':paths} if paths is not None else {}),
                         **({'measured':measured} if measured is not None else {})})
    obstacles = {}
    coarse_envelope_exempt_ids=set(coarse_envelope_exempt_ids)
    for obj in objects:
        if obj.id not in bounds or obj.id not in poses:
            fail([obj.id], 'Missing geometry or world pose for spatial verification', 'geometry')
            continue
        if obj.collision_role == 'floor_covering':
            continue
        part_records=(shell_parts or {}).get(obj.id,{})
        if part_records:
            # Every measured parametric assembly occupies its members, including
            # solid door frames and shelving; its enclosing box may contain voids.
            yaw, origin = poses[obj.id]
            for part_id, local in part_records.items():
                low, high = np.asarray(local, dtype=float)
                xy = _rectangle(_rotation(yaw) @ ((low[:2] + high[:2]) / 2), high[:2] - low[:2], yaw)
                xy += np.asarray(origin)[:2]
                wl = np.r_[xy.min(axis=0), low[2] + origin[2]]
                wh = np.r_[xy.max(axis=0), high[2] + origin[2]]
                obstacles[f'{obj.id}/{part_id}'] = (wl, wh, xy)
            continue
        if obj.id in coarse_envelope_exempt_ids:continue
        if obj.collision_role == 'shell':continue
        low, high = np.asarray(bounds[obj.id], dtype=float)
        polygon=(footprints or {}).get(obj.id)
        if polygon is None:polygon=_rectangle((low[:2] + high[:2]) / 2, high[:2] - low[:2])
        obstacles[obj.id] = (low, high, polygon)
    for zone in contract.zones:
        low, high = np.asarray(zone.center) - np.asarray(zone.size) / 2, np.asarray(zone.center) + np.asarray(zone.size) / 2
        for id in zone.component_ids:
            # A fitted frame or swung door leaf may remain semantically grouped
            # with an activity, but its physical location belongs to its opening.
            if id in aperture_component_ids:
                continue
            if id in bounds:
                world_bounds=np.asarray(bounds[id],dtype=float)
                if np.any(world_bounds[0,:2] < low - .015) or np.any(world_bounds[1,:2] > high + .015):
                    obj=objects_by_id.get(id)
                    parent_id=getattr(obj,'parent',None)
                    parent_pose=poses.get(parent_id) if parent_id else None
                    measured={'world_bounds_m':world_bounds.tolist(),
                        'zone_bounds_xy_m':[low.tolist(),high.tolist()],
                        'position_frame':'parent_local' if parent_id else 'scene_centered',
                        'parent_id':parent_id,
                        'world_pose':{'yaw_deg':float(poses[id][0]),
                                      'origin_m':np.asarray(poses[id][1],dtype=float).tolist()}}
                    if parent_pose is not None:
                        measured['parent_world_pose']={'yaw_deg':float(parent_pose[0]),
                            'origin_m':np.asarray(parent_pose[1],dtype=float).tolist()}
                    fail([id], f'Object footprint is outside functional zone {zone.id}', zone.id,
                         measured=measured)
    shell_ids={o.id for o in objects if o.collision_role=='shell'}
    def fitted_window_shell_parts(opening):
        if opening.kind!='window':return set()
        axis=0 if opening.wall.startswith('x') else 1
        along=1-axis;sign=-1 if opening.wall.endswith('min') else 1
        plane=sign*half[axis];margin=.16;ignored=set()
        for obj in objects:
            if obj.id not in shell_ids:continue
            for part in getattr(obj,'parts',[]):
                part_id=getattr(part,'id','')
                label=f'{part_id} {getattr(part,"semantic","")}'.casefold()
                if not any(token in label for token in ('window','sash','glaz','mullion','shutter')):continue
                oid=f'{obj.id}/{part_id}'
                if oid not in obstacles:continue
                low,high,_=obstacles[oid]
                lateral=(low[along]>=opening.offset-opening.width/2-margin and
                         high[along]<=opening.offset+opening.width/2+margin)
                vertical=(low[2]>=opening.sill_height-margin and
                          high[2]<=opening.sill_height+opening.height+margin)
                projection=opening.fixture_projection_m+margin
                normal=(low[axis]>=plane-projection and high[axis]<=plane+margin) if sign>0 else (
                    low[axis]>=plane-margin and high[axis]<=plane+projection)
                if lateral and vertical and normal:ignored.add(oid)
        return ignored

    def shell_member_at_boundary(oid,low,high):
        if oid.split('/',1)[0] not in shell_ids:return False
        for axis in range(2):
            center=(low[axis]+high[axis])/2
            tolerance=min(.30,(high[axis]-low[axis])/2+.15)
            if any(abs(center-sign*half[axis])<=tolerance for sign in (-1,1)):return True
        return False

    def reserve(id, polygon, low_z, high_z, owners, ignored_components=(), *,
                ignored_shell_parts=(), allow_boundary_shell=False):
        if np.any(polygon < -half - .001) or np.any(polygon > half + .001) or high_z > extent[2] + .015:
            fail(owners, f'Reserved area {id} exceeds usable scene extent', id)
        ignored_components=set(ignored_components)
        ignored_shell_parts=set(ignored_shell_parts)
        blockers = [oid for oid, (low, high, rectangle) in obstacles.items()
                    if oid.split('/',1)[0] not in ignored_components
                    if oid not in ignored_shell_parts
                    if not (allow_boundary_shell and shell_member_at_boundary(oid,low,high))
                    if min(high[2], high_z) - max(low[2], low_z) > .015 and _overlap(polygon, rectangle)]
        if blockers:
            shell_blockers=[oid for oid in blockers if oid.split('/')[0] in shell_ids]
            solid_blockers=[oid for oid in blockers if '/' not in oid]
            member_blockers=[oid for oid in blockers if '/' in oid and oid not in shell_blockers]
            if shell_blockers:
                aperture=id in {opening.id for opening in contract.openings}
                for component in sorted({oid.split('/')[0] for oid in shell_blockers}):
                    affected=[oid.split('/',1)[1] for oid in shell_blockers if oid.split('/')[0]==component]
                    fail([component] if aperture else list(dict.fromkeys([*owners,component])),
                         f'Reserved area {id} is blocked by architectural parts {affected}; '+
                         ('repair the local opening/frame geometry, not the entire wall placement.' if aperture else
                          'revise the approach or its owner placement to remain inside the actual room.'),id,
                         repair_target='geometry' if aperture else 'layout',part_ids=affected if aperture else None)
            if solid_blockers:
                fail(dict.fromkeys([*owners, *solid_blockers]), f'Reserved area {id} is blocked by {solid_blockers}', id)
            if member_blockers:
                proved=set()
                for component in {oid.split('/')[0] for oid in member_blockers}:
                    obj=next(o for o in objects if o.id==component)
                    contact=getattr(obj,'surface_contact',None)
                    if not contact or contact.parent_face[0]=='z':continue
                    parent_parts=(shell_parts or {}).get(obj.parent,{})
                    child_parts=(shell_parts or {}).get(component,{})
                    if not parent_parts or not child_parts:continue
                    from surface_mounting import members,face_bounds,transformed,rotation,translation_domain,face_info
                    try:
                        child_bounds=np.array([np.min([np.asarray(v)[0] for v in child_parts.values()],axis=0),
                                               np.max([np.asarray(v)[1] for v in child_parts.values()],axis=0)])
                        child_face=child_parts[contact.child_part] if contact.child_part else child_bounds
                        parent_yaw,parent_origin=poses[obj.parent];yaw,origin=poses[component]
                        relative_yaw=yaw-parent_yaw
                        local_offset=rotation(-parent_yaw)@(np.asarray(origin)-parent_origin)
                        patch=transformed(face_bounds(child_face,contact.child_face),rotation(relative_yaw),np.zeros(3))
                        axis=face_info(contact.parent_face)[0]
                        domain=translation_domain(patch,members(parent_parts,contact.parent_part),axis,
                            local_offset[axis],child_bounds,relative_yaw,poses[obj.parent],extent)
                    except (ValueError,KeyError,TypeError):continue
                    vertices=domain['scene_feasible_translation_vertices']
                    if not vertices:continue
                    local_polygon=vertices
                    # The mount domain is convex. Split it by the SAT separating
                    # half-planes for each actual box member. A surviving point
                    # is an explicit legal placement that clears the reserve;
                    # empty after every member proves that no position repair
                    # can clear it at the selected opposing-face yaw.
                    def hull(points):
                        points=sorted(set(tuple(map(float,p)) for p in points))
                        if len(points)<3:return np.asarray(points)
                        def turn(o,a,b):return (a[0]-o[0])*(b[1]-o[1])-(a[1]-o[1])*(b[0]-o[0])
                        lower=[]
                        for point in points:
                            while len(lower)>=2 and turn(lower[-2],lower[-1],point)<=1e-10:lower.pop()
                            lower.append(point)
                        upper=[]
                        for point in reversed(points):
                            while len(upper)>=2 and turn(upper[-2],upper[-1],point)<=1e-10:upper.pop()
                            upper.append(point)
                        return np.asarray(lower[:-1]+upper[:-1])
                    def clip(poly,normal,bound):
                        if not len(poly):return poly
                        clipped=[]
                        for first,second in zip(poly,np.roll(poly,-1,axis=0)):
                            a=float(np.dot(normal,first)-bound);b=float(np.dot(normal,second)-bound)
                            inside_a=a<=1e-9;inside_b=b<=1e-9
                            if inside_a:clipped.append(first.tolist())
                            if inside_a!=inside_b:
                                clipped.append((first+(second-first)*(a/(a-b))).tolist())
                        return hull(clipped) if clipped else np.empty((0,2))
                    tangent_axes=[j for j in range(3) if j!=axis]
                    feasible=[hull([[point[j] for j in tangent_axes] for point in vertices])];tested=[];unsupported=False
                    parent_rotation=rotation(parent_yaw);child_rotation=rotation(yaw)
                    tangent_world=parent_rotation[:2,tangent_axes]
                    base_origin=np.asarray(parent_origin)+parent_rotation@(
                        np.eye(3)[axis]*local_offset[axis])
                    for oid in sorted(member_blockers):
                        if oid.split('/')[0]!=component:continue
                        part_id=oid.split('/',1)[1]
                        spec=next((p for p in getattr(obj,'parts',[]) if p.id==part_id),None)
                        if spec is None or spec.primitive!='box' or any(abs(v)>1e-8 for v in spec.rotation):
                            unsupported=True;break
                        local=np.asarray(child_parts[part_id],dtype=float)
                        member_low,member_high=local
                        world_low,world_high=transformed(local,child_rotation,base_origin)
                        if min(world_high[2],high_z)-max(world_low[2],low_z)<=.015:continue
                        member_poly=_rectangle(child_rotation[:2,:2]@
                            ((member_low[:2]+member_high[:2])/2),member_high[:2]-member_low[:2],yaw)+base_origin[:2]
                        axes=[]
                        for shape in (np.asarray(polygon),member_poly):
                            for first,second in zip(shape,np.roll(shape,-1,axis=0)):
                                edge=second-first;normal=np.array([-edge[1],edge[0]],dtype=float)
                                length=np.linalg.norm(normal)
                                if length>1e-9:
                                    normal/=length
                                    if not any(abs(float(np.dot(normal,existing)))>1-1e-8 for existing in axes):axes.append(normal)
                        member_options=[]
                        for normal in axes:
                            room_projection=np.asarray(polygon)@normal
                            member_projection=member_poly@normal
                            shift=normal@tangent_world
                            for side in ('negative','positive'):
                                if side=='negative':
                                    bound=float(room_projection.min()-.002-member_projection.max())
                                    member_options.append((shift,bound))
                                else:
                                    bound=float(member_projection.min()-room_projection.max()-.002)
                                    member_options.append((-shift,bound))
                        tested.append({'part_id':part_id,'separation_halfplanes':len(member_options)})
                        next_feasible=[]
                        for poly in feasible:
                            for normal,bound in member_options:
                                candidate=clip(poly,normal,bound)
                                if len(candidate):next_feasible.append(candidate)
                        feasible=next_feasible
                        if not feasible:break
                    if unsupported or feasible:continue
                    proved.add(component)
                    fail([component],f'Mounted geometry has no supported in-bounds position that clears reserved area {id}; '
                         'the full feasible translation region was tested against every vertically intersecting '
                         'occupied box member at the selected horizontal mounting yaw. Revise the inferred '
                         'component plan while preserving the opening and its usable approach.',id,
                         repair_target='decomposition',part_ids=[item['part_id'] for item in tested],
                         measured={**domain,'reserve_polygon_m':np.asarray(polygon).tolist(),
                                   'tested_member_separations':tested,'feasible_translation_regions_after_clearance':0,
                                   'clearance_margin_m':.002})
                remaining=[oid for oid in member_blockers if oid.split('/')[0] not in proved]
                if remaining:
                    fail(dict.fromkeys([*owners,*[oid.split('/')[0] for oid in remaining]]),
                         f'Reserved area {id} is blocked by occupied parts {remaining}',id)
    for area in contract.access_areas:
        if area.owner_id not in poses:
            continue
        yaw, origin = poses[area.owner_id]
        from general_scene import scalar
        owner=next(o for o in objects if o.id==area.owner_id)
        dims=getattr(owner,'size',getattr(owner,'dimensions',None))
        params={**(parameters or {}),**getattr(owner,'parameters',{})}
        try:
            if dims is not None:
                dims=[scalar(v,params) for v in dims]
            else:
                low,high=np.asarray(bounds[area.owner_id]);dims=high-low
            params.update(zip(('w','d','h'),dims))
            offset=np.array([scalar(v,params) for v in area.center])
        except (ValueError,KeyError,TypeError,ZeroDivisionError,SyntaxError) as failure:
            fail([area.owner_id],f'Cannot evaluate access area {area.id}: {failure}',area.id)
            continue
        center = np.asarray(origin)[:2] + _rotation(yaw) @ offset
        polygon = _rectangle(center, area.size, yaw)
        areas[area.id] = polygon
        reserve(area.id, polygon, 0, area.height, [area.owner_id])
    for opening in contract.openings:
        polygon = opening_polygon(opening, extent)
        areas[opening.id] = polygon
        if opening.kind=='entrance' and opening.swing_leaf_ids:
            axis=0 if opening.wall.startswith('x') else 1
            tangent=1-axis;sign=-1 if opening.wall.endswith('min') else 1
            half=np.asarray(extent[:2])/2
            wall_plane=sign*half[axis]
            aperture=(opening.offset-opening.width/2,opening.offset+opening.width/2)
            for leaf_id in opening.swing_leaf_ids:
                if leaf_id not in bounds or leaf_id not in poses:
                    fail([leaf_id],f'Entrance swing leaf {leaf_id} has no measured bounds or world pose',
                         f'entrance_leaf:{opening.id}:{leaf_id}',repair_target='layout')
                    continue
                low,high=np.asarray(bounds[leaf_id],dtype=float)
                yaw,origin=poses[leaf_id]
                footprint=(footprints or {}).get(leaf_id)
                if footprint is None:
                    footprint=_rectangle((low[:2]+high[:2])/2,high[:2]-low[:2],yaw)
                tangent_projection=np.asarray(footprint)[:,tangent]
                normal_projection=np.asarray(footprint)[:,axis]
                tangent_interval=(float(tangent_projection.min()),float(tangent_projection.max()))
                wall_edge=float(normal_projection.max() if sign>0 else normal_projection.min())
                wall_gap=(wall_plane-wall_edge) if sign>0 else (wall_edge-wall_plane)
                if tangent_interval[1]-tangent_interval[0]>.45:
                    center=np.asarray(footprint,dtype=float).mean(axis=0)
                    parent_id=getattr(objects_by_id.get(leaf_id),'parent',None)
                    parent_yaw=poses.get(parent_id,(0.,None))[0] if parent_id else 0.
                    yaw_options=[]
                    for delta in (-90.,90.):
                        candidate_world_yaw=yaw+delta
                        candidate_fp=(np.asarray(footprint,dtype=float)-center)@_rotation(delta)[:2,:2].T+center
                        projection=candidate_fp[:,tangent]
                        width=float(projection.max()-projection.min())
                        relative_yaw=(candidate_world_yaw-parent_yaw+180.)%360.-180.
                        yaw_options.append({'world_yaw_deg':float((candidate_world_yaw+180.)%360.-180.),
                            'relative_yaw_deg':float(relative_yaw),
                            'projected_jamb_axis_thickness_m':width,
                            'fits_edge_on_limit':width<=.45})
                    yaw_options.sort(key=lambda item:item['projected_jamb_axis_thickness_m'])
                    fail([leaf_id],f'Entrance swing leaf {leaf_id} is not parked edge-on beside opening {opening.id}: projected jamb-axis thickness is {tangent_interval[1]-tangent_interval[0]:.3f}m',
                         f'entrance_leaf:{opening.id}:{leaf_id}',repair_target='layout',code='entrance_leaf_orientation',
                         expected={'maximum_jamb_axis_thickness_m':.45},
                         paths=[f'/components/{list(objects_by_id).index(leaf_id)}/{field}'
                                for field in ('position','orientation_deg')],
                         measured={'projected_jamb_axis_thickness_m':tangent_interval[1]-tangent_interval[0],
                                   'opening_interval_m':list(aperture),'leaf_interval_m':list(tangent_interval),
                                   'world_yaw_deg':float(yaw),'parent_world_yaw_deg':float(parent_yaw),
                                   'quarter_turn_yaw_options':yaw_options})
                    continue
                left_gap=aperture[0]-tangent_interval[1]
                right_gap=tangent_interval[0]-aperture[1]
                adjacent=(-.10<=left_gap<=.35) or (-.10<=right_gap<=.35)
                vertically_fitted=(low[2]<=opening.sill_height+.08 and
                                   high[2]>=opening.sill_height+opening.height-.15)
                if not adjacent or not -.05<=wall_gap<=.20 or not vertically_fitted:
                    fail([leaf_id],f'Entrance swing leaf {leaf_id} is not aligned beside a jamb of {opening.id} in an open position: opening tangent interval={list(aperture)}, leaf tangent interval={list(tangent_interval)}, wall-plane gap={wall_gap:.3f}m',
                         f'entrance_leaf:{opening.id}:{leaf_id}',repair_target='layout',
                         measured={'opening_tangent_interval_m':list(aperture),'leaf_tangent_interval_m':list(tangent_interval),
                                   'left_jamb_gap_m':left_gap,'right_jamb_gap_m':right_gap,
                                   'wall_plane_gap_m':wall_gap,'door_world_bounds_m':np.asarray([low,high]).tolist(),
                                   'world_yaw_deg':float(yaw),'world_origin_m':np.asarray(origin,dtype=float).tolist(),
                                   'position_frame':'parent_local' if getattr(objects_by_id.get(leaf_id),'parent',None) else 'scene_centered'})
        fitted=set(opening.fitted_component_ids)
        axis=0 if opening.wall.startswith('x') else 1
        along=1-axis
        half=np.asarray(extent[:2])/2
        sign=-1 if opening.wall.endswith('min') else 1
        for component in sorted(fitted):
            if component not in bounds or component not in poses:
                fail([component],f'Fitted window component {component} has no measured bounds',opening.id)
                continue
            low,high=np.asarray(bounds[component],dtype=float)
            depth=(high[axis]+half[axis]) if sign<0 else (half[axis]-low[axis])
            expected_low=opening.offset-opening.width/2
            expected_high=opening.offset+opening.width/2
            if (low[along]<expected_low-.03 or high[along]>expected_high+.03 or
                    low[2]<opening.sill_height-.03 or
                    high[2]>opening.sill_height+opening.height+.03 or
                    max(0.,float(depth))>opening.fixture_projection_m+.015):
                world_center=(low+high)/2
                required_shift=np.zeros(3,dtype=float)
                required_shift[along]=opening.offset-world_center[along]
                # Fitted aperture fixtures must be brought to the opening's wall
                # plane as well as centered on its tangent and sill. Reporting
                # only tangent/Z corrections leaves a centered-in-room window
                # hundreds of centimetres away from the wall.
                fixture_thickness=high[axis]-low[axis]
                target_normal=sign*(half[axis]-fixture_thickness/2)
                required_shift[axis]=target_normal-world_center[axis]
                required_shift[2]=opening.sill_height+opening.height/2-world_center[2]
                shifted_low=low+required_shift;shifted_high=high+required_shift
                translated_projection=(shifted_high[axis]+half[axis]) if sign<0 else (half[axis]-shifted_low[axis])
                translation_feasible=bool(
                    shifted_low[along]>=expected_low-.03 and shifted_high[along]<=expected_high+.03
                    and shifted_low[2]>=opening.sill_height-.03
                    and shifted_high[2]<=opening.sill_height+opening.height+.03
                    and translated_projection<=opening.fixture_projection_m+.015)
                opening_world_center=np.zeros(3,dtype=float)
                opening_world_center[axis]=sign*half[axis]
                opening_world_center[along]=opening.offset
                opening_world_center[2]=opening.sill_height+opening.height/2
                obj=objects_by_id.get(component)
                parent_id=getattr(obj,'parent',None)
                parent_pose=poses.get(parent_id) if parent_id else None
                measured={'world_bounds_m':np.asarray([low,high]).tolist(),
                    'opening_world_center_m':opening_world_center.tolist(),
                    'opening_aperture_m':{'width':float(opening.width),
                        'z':[float(opening.sill_height),float(opening.sill_height+opening.height)],
                        'allowed_interior_projection':float(opening.fixture_projection_m)},
                    'measured_interior_projection_m':max(0.,float(depth)),
                    'target_wall_plane_center_m':float(target_normal),
                    'required_world_center_shift_m':required_shift.tolist(),
                    'translation_feasible':translation_feasible,
                    'projected_dimensions_normal_tangent_z_m':[float(high[a]-low[a]) for a in (axis,along,2)],
                    'repair_basis':('Translation candidate satisfies aperture dimensions; recheck scene constraints.'
                        if translation_feasible else 'Current projected dimensions cannot fit by this translation. '
                        'Correct yaw from the component local dimensions and wall normal, or revise upstream dimensions; '
                        'do not keep translating the fixture.'),
                    'position_frame':'parent_local' if parent_id else 'scene_centered',
                    'parent_id':parent_id,
                    'world_pose':{'yaw_deg':float(poses[component][0]),
                                  'origin_m':np.asarray(poses[component][1],dtype=float).tolist()}}
                if parent_pose is not None:
                    measured['parent_world_pose']={'yaw_deg':float(parent_pose[0]),
                        'origin_m':np.asarray(parent_pose[1],dtype=float).tolist()}
                current_local=local_positions.get(component)
                if current_local is not None:
                    parent_yaw=parent_pose[0] if parent_pose is not None else 0.
                    local_shift=_rotation(-parent_yaw)@required_shift[:2]
                    try:
                        from general_scene import scalar
                        local_parameters=dict(parameters or {})
                        dimensions=getattr(obj,'dimensions',getattr(obj,'size',None))
                        if dimensions is not None:
                            size=[scalar(value,local_parameters) for value in dimensions]
                            local_parameters.update(zip(('w','d','h'),size))
                        current=np.asarray([scalar(value,local_parameters) for value in current_local],dtype=float)
                    except (ValueError,KeyError,TypeError,ZeroDivisionError,SyntaxError,OverflowError):
                        current=None
                    if current is not None:
                        measured['current_local_position_m']=current.tolist()
                        recommended=current+np.r_[local_shift,required_shift[2]]
                        measured['recommended_local_position_m']=recommended.tolist() if translation_feasible else None
                fail([component],f'Fitted window component {component} does not fit opening {opening.id}: '
                     f'world_bounds={np.asarray([low,high]).tolist()}, aperture_width_m={opening.width:.3f}, '
                     f'aperture_z_m={[opening.sill_height,opening.sill_height+opening.height]}, '
                     f'interior_projection_m={max(0.,float(depth)):.3f}, '
                     f'allowed_projection_m={opening.fixture_projection_m:.3f}',opening.id,measured=measured,
                     code='window_opening_fit',expected={'tangent_interval_m':[expected_low,expected_high],
                         'vertical_interval_m':[opening.sill_height,opening.sill_height+opening.height],
                         'maximum_interior_projection_m':opening.fixture_projection_m},
                     paths=[f'/components/{list(objects_by_id).index(component)}/{field}'
                            for field in ('position','orientation_deg')])
        reserve(opening.id, polygon, opening.sill_height, opening.sill_height + opening.height, [], fitted,
                ignored_shell_parts=fitted_window_shell_parts(opening))
    for space in contract.open_spaces:
        reserve('design_empty_'+space.intent_id,_rectangle(space.center,space.size),space.bottom,
                space.bottom+space.height,[],allow_boundary_shell=True)
    for view in contract.experience_views:
        eye=np.asarray(view.position)
        blockers=[oid for oid,(low,high,polygon) in obstacles.items()
                  if low[2]-.01<=eye[2]<=high[2]+.01 and _point_inside(eye[:2],polygon)]
        if blockers:fail([oid.split('/')[0] for oid in blockers],
            f'Designed observation position {view.intent_id} lies inside geometry {blockers}',view.intent_id)
    owners = {a.id: a.owner_id for a in contract.access_areas}
    for route_index,route in enumerate(contract.routes):
        endpoint_owners = [owners[n] for n in (route.start, route.end) if n in owners]
        for node, point in ((route.start, route.points[0]), (route.end, route.points[-1])):
            if node not in areas or not _point_inside(point, areas[node]):
                fail(endpoint_owners, f'Route {route.id} endpoint does not meet {node}', route.id)
        points = np.asarray(route.points)
        radius = route.width / 2
        if np.any(points - radius < -half - .001) or np.any(points + radius > half + .001) or route.height > extent[2] + .015:
            fail(endpoint_owners, f'Route {route.id} width/headroom exceeds scene extent', route.id)
        blockers = set()
        blocker_measurements=[]
        minimum_gap = float('inf')
        for oid, (low, high, polygon) in obstacles.items():
            if min(high[2], route.height) - max(low[2], 0) <= .015:
                continue
            segment_gaps=[_segment_polygon_distance(a,b,polygon) for a,b in zip(points,points[1:])]
            gap = min(segment_gaps)
            minimum_gap = min(minimum_gap, gap)
            if gap < radius - .001:
                blockers.add(oid)
                blocker_measurements.append({'object_or_part_id':oid,
                    'world_bounds_m':[np.asarray(low).tolist(),np.asarray(high).tolist()],
                    'centerline_gap_m':float(gap),'clearance_deficit_m':float(radius-gap),
                    'blocked_segment_indices':[i for i,g in enumerate(segment_gaps) if g<radius-.001]})
        if blockers:
            fail(dict.fromkeys([*endpoint_owners, *[oid.split('/')[0] for oid in sorted(blockers)], *sorted(blockers)]),
                 f'Route {route.id} requires {route.width:.3f}m width; blocked by {sorted(blockers)}', route.id,
                 code='route_blocked',paths=[f'/spatial/routes/{route_index}/points']+
                    [f'/components/{i}/position' for i,obj in enumerate(objects)
                     if obj.id in {oid.split('/')[0] for oid in blockers} and obj.collision_role!='shell'],
                 measured={'points_world_xy_m':points.tolist(),'blockers':blocker_measurements,
                     'position_frame':'world_xy','route_id':route.id,'width_m':route.width,
                     'legal_centerline_bounds_xy_m':[(-half+radius).tolist(),(half-radius).tolist()]},
                 expected={'minimum_centerline_obstacle_distance_m':float(radius),'tolerance_m':.001})
        route_records.append({'id': route.id, 'required_width_m': route.width,
                              'minimum_centerline_obstacle_distance_m': None if math.isinf(minimum_gap) else minimum_gap})
    for relation in contract.relations:
        if relation.a not in bounds or relation.b not in bounds or relation.a not in poses or relation.b not in poses:
            continue
        al, ah = np.asarray(bounds[relation.a]); bl, bh = np.asarray(bounds[relation.b])
        ca, cb = (al[:2] + ah[:2]) / 2, (bl[:2] + bh[:2]) / 2
        ayaw, _ = poses[relation.a]; byaw, _ = poses[relation.b]
        distance=None
        if relation.kind == 'distance':
            distance = float(np.linalg.norm(np.maximum(0, np.maximum(al[:2] - bh[:2], bl[:2] - ah[:2]))))
            ok = distance >= relation.minimum_m - .001 and (relation.maximum_m is None or distance <= relation.maximum_m + .001)
            detail = f'footprint distance={distance:.3f}m, expected {relation.minimum_m}..{relation.maximum_m}m'
        elif relation.kind == 'facing':
            direction = cb - ca
            front = _rotation(ayaw) @ np.array([0., -1.])
            direction_length=float(np.linalg.norm(direction))
            angle = math.degrees(math.acos(float(np.clip(np.dot(front, direction) / max(direction_length, 1e-12), -1, 1))))
            ok = np.linalg.norm(direction) > .001 and angle <= relation.tolerance_deg
            detail = f'front-to-target angle={angle:.1f}deg, allowed {relation.tolerance_deg}deg'
            distance = float(np.linalg.norm(np.maximum(0, np.maximum(al[:2] - bh[:2], bl[:2] - ah[:2]))))
        else:
            direction = {'left_of': [-1, 0], 'right_of': [1, 0], 'in_front_of': [0, -1], 'behind': [0, 1]}[relation.kind]
            axis = _rotation(byaw) @ direction
            polygon_a=(footprints or {}).get(relation.a)
            polygon_b=(footprints or {}).get(relation.b)
            ap=(np.asarray(polygon_a) if polygon_a is not None else
                _rectangle(ca,ah[:2]-al[:2],ayaw)) @ axis
            bp=(np.asarray(polygon_b) if polygon_b is not None else
                _rectangle(cb,bh[:2]-bl[:2],byaw)) @ axis
            # A floor covering like a rug may intentionally extend under its
            # reference object (e.g. sofa front legs rest on the rug). In that
            # case the directional intent is about the rug's CENTER, not its
            # leading edge: the rug's centre-of-mass must be on the declared
            # side of the reference object's footprint.
            a_role=getattr(objects_by_id.get(relation.a),'collision_role','solid')
            if a_role=='floor_covering':
                a_proj=float(np.dot(ca,axis))
                gap=a_proj-float(bp.max())
            else:
                gap = float(ap.min() - bp.max())
            ok = gap >= relation.minimum_m - .001
            detail = f'directional footprint gap={gap:.3f}m, required {relation.minimum_m}m'
        if relation.kind == 'facing' and (relation.minimum_m > 0 or relation.maximum_m is not None):
            distance = float(np.linalg.norm(np.maximum(0, np.maximum(al[:2] - bh[:2], bl[:2] - ah[:2]))))
            ok = ok and distance >= relation.minimum_m - .001 and (relation.maximum_m is None or distance <= relation.maximum_m + .001)
            detail += f', footprint distance={distance:.3f}m expected {relation.minimum_m}..{relation.maximum_m}'
        if not ok:
            measured={'relation_kind':relation.kind,'design_relation_id':relation.design_relation_id,
                'a_center_world_xy_m':ca.tolist(),'b_center_world_xy_m':cb.tolist(),
                'a_world_yaw_deg':float(ayaw),'b_world_yaw_deg':float(byaw)}
            if relation.kind in {'left_of','right_of','in_front_of','behind'}:
                measured['directional_gap_m']=gap
                declared={'left_of':np.array([-1.,0.]),'right_of':np.array([1.,0.]),
                          'in_front_of':np.array([0.,-1.]),'behind':np.array([0.,1.])}[relation.kind]
                relative_world=ca-cb
                measured['relative_center_world_xy_m']=relative_world.tolist()
                measured['relative_center_in_b_local_xy_m']=(_rotation(-byaw)@relative_world).tolist()
                measured['expected_a_direction_in_b_local_xy']=declared.tolist()
                measured['expected_a_direction_world_xy']=(_rotation(byaw)@declared).tolist()
            else:
                measured['footprint_distance_m']=distance
                measured['minimum_m']=relation.minimum_m
                measured['maximum_m']=relation.maximum_m
                if relation.kind=='facing':
                    measured['front_target_angle_deg']=angle
                    measured['front_direction_world_xy']=front.tolist()
                    measured['target_direction_world_xy']=(direction/max(direction_length,1e-12)).tolist()
                    measured['target_direction_in_a_local_xy']=(_rotation(-ayaw)@direction).tolist()
                    measured['minimum_distance_passed']=distance>=relation.minimum_m-.001
                    measured['maximum_distance_passed']=(relation.maximum_m is None or
                        distance<=relation.maximum_m+.001)
                    measured['facing_angle_passed']=direction_length>.001 and angle<=relation.tolerance_deg
            fail([relation.a, relation.b], f'{relation.id} ({relation.kind}) failed: {detail}', relation.id,
                 measured=measured)
    return {'status': 'passed' if not findings else 'needs_revision', 'findings': findings,
            'access_polygons_m': {id: p.tolist() for id, p in areas.items()}, 'routes': route_records,
            'limits': 'Conservative solid AABBs and floor-level human clearance; shell openings, ergonomics, semantics and style also require visual review. No building-code certification.'}


def coarse_layout(outline, layout, contract):
    """Cheap envelope-only gate before recipes, retrieval, or neural generation."""
    from general_scene import scalar
    by_id = {o.id: o for o in outline.objects}
    if [p.id for p in layout.placements] != list(by_id):
        raise ValueError('Layout must contain exactly the frozen component IDs in the same order')
    placements = {p.id: p for p in layout.placements}
    bounds, poses, sizes = {}, {}, {}
    failed=set();evaluation_findings=[];blocked=[]
    pending = list(outline.objects)
    while pending:
        previous_count=len(pending)
        for obj in pending[:]:
            if obj.parent in failed:
                failed.add(obj.id);pending.remove(obj)
                blocked.append({'check':'world_pose','reason':f'{obj.id}: parent {obj.parent} could not be evaluated.',
                                'paths':[f'/placements/{list(by_id).index(obj.id)}/position']})
                continue
            if obj.parent and obj.parent not in poses:
                continue
            try:
                size = np.array([scalar(v, outline.parameters) for v in obj.dimensions])
                sizes[obj.id] = size
                params = {**outline.parameters, **dict(zip(('w', 'd', 'h'), size))}
                placement = placements[obj.id]
                offset = np.array([scalar(v, params) for v in placement.position])
            except (ValueError,KeyError,TypeError,ZeroDivisionError,SyntaxError) as failure:
                failed.add(obj.id);pending.remove(obj)
                evaluation_findings.append({'component_ids':[obj.id],'repair_target':'layout',
                    'constraint_id':'placement_expression','evidence':f'{obj.id}: cannot evaluate placement: {failure}'})
                blocked.append({'check':'world_pose','reason':f'{obj.id}: invalid placement expressions.',
                                'paths':[f'/placements/{list(by_id).index(obj.id)}/position']})
                continue
            pyaw, porigin = poses.get(obj.parent, (0., np.zeros(3)))
            origin = porigin + np.r_[_rotation(pyaw) @ offset[:2], offset[2]]
            if obj.mount == 'top': origin[2] = bounds[obj.parent][1][2] + offset[2]
            if obj.mount == 'bottom': origin[2] = bounds[obj.parent][0][2] - size[2] + offset[2]
            yaw = pyaw + placement.yaw_deg
            if getattr(obj,'surface_contact',None):
                from surface_mounting import face_info,rotation
                parent_normal=face_info(obj.surface_contact.parent_face)[2]
                child_normal=face_info(obj.surface_contact.child_face)[2]
                if np.dot(rotation(placement.yaw_deg)@child_normal,parent_normal)>-.999:
                    evaluation_findings.append({'component_ids':[obj.id],'repair_target':'layout',
                        'constraint_id':'mounting_orientation','evidence':'Selected mounting faces do not oppose; revise relative yaw or semantic mounting faces.'})
            xy = _rectangle(origin[:2], size[:2], yaw)
            bounds[obj.id] = [np.r_[xy.min(axis=0), origin[2]].tolist(), np.r_[xy.max(axis=0), origin[2] + size[2]].tolist()]
            poses[obj.id] = (yaw, origin)
            pending.remove(obj)
        if len(pending)==previous_count:
            for obj in pending:
                failed.add(obj.id)
                evaluation_findings.append({'component_ids':[obj.id],'repair_target':'layout',
                    'constraint_id':'parent_graph','evidence':f'{obj.id}: cyclic or unavailable parent dependency'})
            break
    # At this stage the geometry IS an oriented declared envelope. Its AABB
    # contains empty corners that must not invalidate a face-relative approach.
    # Exported geometry audits still use measured bounds unless supplied with
    # independently measured footprints.
    footprints={id:_rectangle(origin[:2],sizes[id][:2],yaw) for id,(yaw,origin) in poses.items()}
    report = audit_spatial(contract, outline.objects, outline.extent, bounds, poses,
                           parameters=outline.parameters,footprints=footprints,
                           local_positions={id:placement.position for id,placement in placements.items()},
                           coarse_envelope_exempt_ids=distributed_architecture_envelope_ids(
                               outline.objects,outline.extent,outline.parameters))
    report['findings'].extend(evaluation_findings)
    report['blocked_checks']=blocked
    roots = [o for o in outline.objects if o.parent is None]
    half = np.asarray(outline.extent[:2]) / 2
    for i, obj in enumerate(roots):
        if obj.id not in bounds:continue
        low, high = np.asarray(bounds[obj.id])
        ground_error=(obj.grounded and (abs(low[2])>.015 if obj.collision_role!='shell'
                                       else low[2]>.015 or high[2]<-.015))
        if np.any(low[:2] < -half - .015) or np.any(high[:2] > half + .015) or (low[2] < -.015 and obj.collision_role!='shell') or high[2] > outline.extent[2] + .015 or ground_error:
            report['findings'].append({'component_ids': [obj.id], 'repair_target': 'layout', 'evidence': 'Root envelope is outside extent or violates grounded floor contact'})
        if obj.collision_role == 'shell': continue
        for other in roots[i + 1:]:
            if other.id not in bounds:continue
            if other.collision_role == 'shell': continue
            if obj.collision_role=='floor_covering' or other.collision_role=='floor_covering':continue
            ol, oh = np.asarray(bounds[other.id])
            overlap=np.minimum(high,oh)-np.maximum(low,ol)
            if np.all(overlap>.015):
                report['findings'].append({'component_ids': [obj.id, other.id], 'repair_target': 'layout',
                    'evidence': 'Coarse root envelopes overlap',
                    'measured':{'world_bounds_a_m':[low.tolist(),high.tolist()],
                        'world_bounds_b_m':[ol.tolist(),oh.tolist()],
                        'overlap_depth_xyz_m':overlap.tolist()}})
    report['status'] = 'passed' if not report['findings'] else 'needs_revision'
    report['evidence_kind'] = 'coarse_envelopes_only; named support faces require subsequent recipe and Blender checks'
    report['bounds'] = bounds
    return report


def write_spatial_preview(contract, bounds, extent, report, output):
    """System-generated plan evidence, labeled as envelopes rather than a render."""
    from pathlib import Path
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ModuleNotFoundError:
        return {'status': 'unavailable', 'reason': 'Install Pillow to write optional planning previews.'}
    canvas = Image.new('RGB', (1400, 1120), '#f6f8fa')
    draw = ImageDraw.Draw(canvas)
    font_path = Path('C:/Windows/Fonts/msyh.ttc')
    font = ImageFont.truetype(str(font_path), 17) if font_path.is_file() else ImageFont.load_default()
    small = ImageFont.truetype(str(font_path), 14) if font_path.is_file() else font
    scale = min(960 / extent[0], 940 / extent[1])
    origin = np.array([535., 565.])
    def screen(p): return tuple(origin + np.asarray(p) * [scale, -scale])
    def polygon(points, fill, outline):
        draw.polygon([screen(p) for p in points], fill=fill, outline=outline, width=2)
    polygon(_rectangle([0, 0], extent[:2]), '#ffffff', '#4b5563')
    for zone in contract.zones:
        polygon(_rectangle(zone.center, zone.size), None, '#c4cbd3')
        pos = np.asarray(zone.center) + [-zone.size[0] / 2 + .05, zone.size[1] / 2 - .12]
        draw.text(screen(pos), zone.id, font=small, fill='#667080')
    for id, points in report.get('access_polygons_m', {}).items():
        polygon(points, '#e8f6ed', '#42a36c')
    for route in contract.routes:
        points = [screen(p) for p in route.points]
        draw.line(points, fill='#bddfc9', width=max(1, int(route.width * scale)), joint='curve')
        draw.line(points, fill='#237747', width=3, joint='curve')
    labels=[]
    for id, (low, high) in sorted(bounds.items(), key=lambda item:item[1][1][2]):
        if np.all(np.asarray(high[:2]) - low[:2] >= np.asarray(extent[:2]) - .015):
            continue
        xy = _rectangle((np.asarray(low[:2]) + high[:2]) / 2, np.asarray(high[:2]) - low[:2])
        polygon(xy, '#d9e8ef', '#44758a')
        labels.append(id)
        point=screen([low[0],high[1]])
        draw.text((point[0]+4,point[1]+2),str(len(labels)),font=small,fill='#203d4d',stroke_width=2,stroke_fill='#f6f8fa')
    draw.text((30, 20), 'FineScene | Agent spatial plan | TOP (+Y up)', font=font, fill='#20252b')
    deferred_count=len(report.get('deferred_findings',[]))
    gate=('Ready for Blender audit' if report.get('readiness')=='ready_for_3d_audit'
          else 'Pre-build blockers present' if report.get('readiness')=='blocked'
          else report['status'])
    draw.text((30, 52), f'Envelope only | {extent[0]:g} x {extent[1]:g} x {extent[2]:g} m | {gate} | {deferred_count} checks deferred', font=font, fill='#52606d')
    x, y = 1065, 110
    draw.text((x, y), 'SPACE CONTRACT', font=font, fill='#20252b'); y += 40
    for label in ['Blue: object envelopes', 'Green: reserved access', 'Line: circulation center', 'Wide line: required width']:
        draw.text((x, y), label, font=small, fill='#52606d'); y += 25
    y += 20
    for route in contract.routes:
        draw.text((x, y), f'{route.id[:27]}', font=small, fill='#237747'); y += 22
        draw.text((x, y), f'width {route.width:g} m', font=small, fill='#52606d'); y += 30
        if y > 990: break
    y+=15
    for index,id in enumerate(labels,1):
        draw.text((x,y),f'{index}. {id[:33]}',font=small,fill='#203d4d'); y+=24
        if y > 1030:break
    draw.text((30, 1080), 'Geometry + scene visual Judge required. This plan does not certify appearance or safety codes.', font=small, fill='#52606d')
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output)
