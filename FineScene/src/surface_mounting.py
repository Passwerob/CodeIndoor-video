"""Agent-selected mounting faces, resolved against real local geometry.

No zone boundary or nearest-wall guess is an installation surface. Architectural
wall cells with the compiler's _NNN suffix share their original semantic ID.
"""
import math
import re
from typing import Literal
import numpy as np
from pydantic import BaseModel, ConfigDict, Field

Face = Literal['x_min','x_max','y_min','y_max','z_min','z_max']


class SurfaceMountingError(ValueError):
    def __init__(self,code,message,measurements):
        self.code=code;self.measurements=measurements
        super().__init__(message)


class SurfaceContact(BaseModel):
    model_config=ConfigDict(extra='forbid')
    parent_part: str = Field(min_length=1)
    parent_face: Face
    child_part: str | None = None
    child_face: Face


def rotation(yaw):
    a=math.radians(yaw)
    return np.array([[math.cos(a),-math.sin(a),0],[math.sin(a),math.cos(a),0],[0,0,1]])


def face_info(face):
    axis='xyz'.index(face[0]);side=0 if face.endswith('min') else 1
    normal=np.zeros(3);normal[axis]=-1 if side==0 else 1
    return axis,side,normal


def validate_wall_contact_axis(contact, dimensions, *, tolerance_m=.01):
    """Ensure a whole wall-mounted object contacts through its shallow axis."""
    if contact.child_part is not None:
        return
    parent_axis=face_info(contact.parent_face)[0]
    child_axis=face_info(contact.child_face)[0]
    if parent_axis not in (0,1) or child_axis not in (0,1):
        return
    size=np.asarray(dimensions,dtype=float)
    if size.shape!=(3,) or not np.all(np.isfinite(size)) or np.any(size<=0):
        raise ValueError('Wall contact needs three positive finite component dimensions')
    expected_axis=int(np.argmin(size[:2]))
    other_axis=1-expected_axis
    tolerance=max(tolerance_m,float(size[expected_axis])*.05)
    if child_axis!=expected_axis and size[child_axis]-size[expected_axis]>tolerance:
        raise ValueError(
            f'Wall-mounted component uses child face {contact.child_face} on its '
            f'{"XY"[child_axis]} envelope ({size[child_axis]:.3f} m), but its thinner '
            f'horizontal envelope is {"XY"[expected_axis]} ({size[expected_axis]:.3f} m). '
            'Choose the opposing face on the thinner axis so the broad visible face remains '
            'toward the room; preserve the component dimensions and revise child_face/yaw.'
        )


def validate_wall_functional_face(contact, front_face='y_min'):
    """Keep the canonical functional front away from the supporting wall."""
    if face_info(contact.parent_face)[0] not in (0,1):
        return
    front_axis,front_side,_=face_info(front_face)
    child_axis,child_side,_=face_info(contact.child_face)
    if child_axis==front_axis and child_side==front_side:
        rear_face='xyz'[front_axis]+'_max' if front_side==0 else 'xyz'[front_axis]+'_min'
        raise ValueError(
            f'child_face={contact.child_face} is the component functional front '
            f'({front_face}), so it would face into the wall. Select its rear face '
            f'{rear_face}; the mounting solver derives world yaw from measured surface normals.'
        )


def validate_wall_child_part_face_axis(contact, part_id, *, dimensions=None, normal_axis=None,
                                       tolerance_m=.01):
    """Require a wall contact to use the actual thin/planar axis of its child part."""
    parent_axis=face_info(contact.parent_face)[0]
    if parent_axis not in (0,1):
        return
    if normal_axis is not None:
        if normal_axis not in 'xyz':
            raise ValueError(f'{part_id}: panel normal axis must be x, y or z, got {normal_axis!r}.')
        expected_axis='xyz'.index(normal_axis)
        size=None
    else:
        size=np.asarray(dimensions,dtype=float)
        if size.shape!=(3,) or not np.all(np.isfinite(size)) or np.any(size<=0):
            raise ValueError(f'{part_id}: wall contact needs three positive finite child-part dimensions.')
        expected_axis=int(np.argmin(size[:2]))
        other_axis=1-expected_axis
        if size[other_axis]-size[expected_axis]<=tolerance_m:
            return
    child_axis=face_info(contact.child_face)[0]
    if child_axis!=expected_axis:
        expected_name='xyz'[expected_axis]
        if size is None:
            evidence=f'panel normal axis is {expected_name.upper()}'
        else:
            evidence=(f'part size is {size[0]:.3f} x {size[1]:.3f} x {size[2]:.3f} m; '
                      f'thin horizontal axis is {expected_name.upper()}')
        if expected_axis==2:
            raise ValueError(f'{part_id}: {evidence}; a vertical wall contact needs an X- or Y-normal '
                             'child face that can oppose the wall using yaw rotation.')
        raise ValueError(f'{part_id}: child_face {contact.child_face} uses axis '
                         f'{"XYZ"[child_axis]}, but {evidence}; use {expected_name}_min or '
                         f'{expected_name}_max so the actual panel face contacts the wall.')
    validate_wall_functional_face(contact)


def members(parts, name):
    records=[np.asarray(v.get('bounds',v) if isinstance(v,dict) else v,dtype=float)
             for k,v in parts.items() if k==name or re.fullmatch(re.escape(name)+r'_\d{3}',k)]
    if not records:raise ValueError(f'Mounting surface {name} has no real parent members')
    return records


def face_bounds(bounds, face):
    result=np.asarray(bounds,dtype=float).copy();axis,side,_=face_info(face)
    result[:,axis]=result[side,axis]
    return result


def transformed(bounds, matrix, offset):
    lo,hi=np.asarray(bounds)
    points=np.array([[x,y,z] for x in (lo[0],hi[0]) for y in (lo[1],hi[1]) for z in (lo[2],hi[2])])@matrix.T+offset
    return np.stack((points.min(axis=0),points.max(axis=0)))


def covered(patch, surfaces, axis, tolerance=.003):
    """Exact union coverage of coplanar rectangular wall cells, including holes."""
    tangents=[i for i in range(3) if i!=axis]
    low,high=np.asarray(patch)[:,tangents]
    if np.any(high-low<=1e-7):return False
    rects=[s[:,tangents]+np.array([[-tolerance], [tolerance]]) for s in surfaces]
    cuts=[sorted({low[i],high[i],*[float(np.clip(r[j,i],low[i],high[i])) for r in rects for j in (0,1)]}) for i in range(2)]
    for a,b in zip(cuts[0],cuts[0][1:]):
        for c,d in zip(cuts[1],cuts[1][1:]):
            # Float32 glTF bounds can differ from planned decimal dimensions
            # by nanometers at the existing 3 mm contact margin. Such sliver
            # cells must not turn a covered face into an impossible interface.
            if b-a<=1e-7 or d-c<=1e-7:continue
            p=np.array([(a+b)/2,(c+d)/2])
            if not any(np.all(p>=r[0]) and np.all(p<=r[1]) for r in rects):return False
    return True


def translation_domain(patch, surfaces, axis, normal_offset, body_bounds, yaw, parent_pose, extent):
    """Conservative feasible translations, intersecting support and world bounds.

    The support union's bounding rectangle is an upper bound: an empty domain
    proves impossibility at this yaw, while a nonempty domain still needs exact
    coverage. Solve the two tangential coordinates by half-plane intersections.
    """
    tangents=[i for i in range(3) if i!=axis]
    lower=np.min([s[0,tangents] for s in surfaces],axis=0)-.003-patch[0,tangents]
    upper=np.max([s[1,tangents] for s in surfaces],axis=0)+.003-patch[1,tangents]
    rows=[];limits=[]
    for i in range(2):
        row=np.eye(2)[i];rows.extend((row,-row));limits.extend((upper[i],-lower[i]))
    parent_yaw,parent_origin=parent_pose
    matrix=rotation(parent_yaw)
    body=transformed(body_bounds,rotation(parent_yaw+yaw),np.zeros(3))
    fixed=np.asarray(parent_origin,dtype=float)+matrix[:,axis]*normal_offset
    scene_low=np.array([-extent[0]/2,-extent[1]/2,0])-.015
    scene_high=np.asarray([extent[0]/2,extent[1]/2,extent[2]])+.015
    for i in range(3):
        row=matrix[i,tangents]
        rows.extend((row,-row))
        limits.extend((scene_high[i]-body[1,i]-fixed[i],
                       -scene_low[i]+body[0,i]+fixed[i]))
    rows=np.asarray(rows);limits=np.asarray(limits);vertices=[]
    for i in range(len(rows)):
        for j in range(i+1,len(rows)):
            system=rows[[i,j]]
            if abs(np.linalg.det(system))<1e-10:continue
            point=np.linalg.solve(system,limits[[i,j]])
            if np.all(rows@point<=limits+1e-8):
                position=np.zeros(3);position[axis]=normal_offset;position[tangents]=point
                if not any(np.allclose(position,p,rtol=0,atol=1e-8) for p in vertices):vertices.append(position.tolist())
    return {'tangent_axes':["xyz"[i] for i in tangents],
            'support_translation_bounds':[lower.tolist(),upper.tolist()],
            'scene_feasible_translation_vertices':vertices,
            'domain_kind':'conservative support-union bound at the specified relative yaw'}


def resolve(contact, parent_parts, child_bounds, child_parts, offset, yaw, *, parent_pose=None, scene_extent=None):
    measured={'contact':contact.model_dump(),'requested_parent_local_offset_m':list(map(float,offset)),
              'relative_yaw_deg':float(yaw)}
    def fail(code,message):raise SurfaceMountingError(code,message,measured)
    try:surfaces=members(parent_parts,contact.parent_part)
    except ValueError as error:fail('mounting_surface',str(error))
    measured['parent_surface_bounds']=[s.tolist() for s in surfaces]
    axis,side,normal=face_info(contact.parent_face)
    planes=[s[side,axis] for s in surfaces]
    measured['parent_local_planes_m']=planes
    if max(planes)-min(planes)>.001:fail('mounting_surface','Named mounting members are not coplanar')
    _,_,child_normal=face_info(contact.child_face)
    matrix=rotation(yaw)
    if np.dot(matrix@child_normal,normal)>-.999:
        fail('mounting_orientation','Mounting faces must oppose: revise child yaw or face selection')
    body_bounds=child_bounds
    if contact.child_part:
        if contact.child_part not in child_parts:fail('mounting_child_part','Named child mounting part is missing')
        record=child_parts[contact.child_part]
        child_bounds=record.get('bounds',record) if isinstance(record,dict) else record
    local_patch=transformed(face_bounds(child_bounds,contact.child_face),matrix,np.zeros(3))
    patch=local_patch.copy()
    result=np.asarray(offset,dtype=float).copy()
    result[axis]=planes[0]-patch[0,axis]
    patch+=result
    measured['parent_local_patch']=patch.tolist()
    measured['resolved_parent_local_offset_m']=result.tolist()
    if parent_pose is not None and scene_extent is not None:
        domain=translation_domain(local_patch,surfaces,axis,result[axis],body_bounds,yaw,parent_pose,scene_extent)
        measured.update(domain,scene_extent_m=list(scene_extent),child_local_body_bounds=np.asarray(body_bounds).tolist())
        if not domain['scene_feasible_translation_vertices']:
            fail('mounting_extent_conflict','At this relative yaw, full support coverage and the scene extent '
                 'have no common translation. Moving inward loses support; moving outward crosses the '
                 'boundary. Review the inferred contact parts/faces or physically appropriate yaw; '
                 'preserve geometry, dimensions and support coverage.')
    if not covered(patch,surfaces,axis):
        tangents=[i for i in range(3) if i!=axis]
        support_low=np.min([s[0,tangents] for s in surfaces],axis=0)-.003
        support_high=np.max([s[1,tangents] for s in surfaces],axis=0)+.003
        patch_area=float(np.prod(patch[1,tangents]-patch[0,tangents]))
        support_upper_area=float(np.prod(support_high-support_low))
        measured.update(contact_area_m2=patch_area,support_area_upper_bound_m2=support_upper_area)
        if patch_area>support_upper_area+1e-9:
            fail('mounting_interface_capacity','Selected contact face exceeds the support area upper bound; '
                'no translation or yaw can provide full coverage. Revise the inferred mounting interface, '
                'preserving actual geometry and required support coverage.')
        fail('mounting_coverage','Mounting contact crosses an opening or lies outside actual parent surface; revise tangential placement')
    return result,{'parent_part':contact.parent_part,'parent_face':contact.parent_face,
        'child_part':contact.child_part,'child_face':contact.child_face,
        'parent_local_plane_m':planes[0],'parent_local_patch':patch.tolist()}


def fit_contact_pose(contact,parent_parts,child_bounds,child_parts,offset,yaw,*,parent_pose,scene_extent):
    """Project an Agent's contact intent onto its real feasible pose domain.

    Keep the selected parts/faces and geometry. Derive opposing normals and
    find the closest covered tangential translation, with exact revalidation.
    """
    _,_,normal=face_info(contact.parent_face)
    _,_,child_normal=face_info(contact.child_face)
    yaws=[float(yaw)]
    if abs(normal[2])<.5 and abs(child_normal[2])<.5:
        yaws.append(float(np.degrees(np.arctan2(-normal[1],-normal[0])-
                                    np.arctan2(child_normal[1],child_normal[0]))))
    # Horizontal contact determines height, not azimuth. Preserve the Agent's
    # functional facing/open state rather than rotating furniture or a door
    # to make a smaller translation possible.
    requested=np.asarray(offset,dtype=float);solutions=[]
    for angle in dict.fromkeys(yaws):
        candidates=[requested]
        try:
            fitted,evidence=resolve(contact,parent_parts,child_bounds,child_parts,requested,angle,
                parent_pose=parent_pose,scene_extent=scene_extent)
            solutions.append((fitted,angle,evidence));continue
        except SurfaceMountingError as failure:
            measured=failure.measurements
            vertices=np.asarray(measured.get('scene_feasible_translation_vertices',[]),dtype=float)
            if not vertices.size:continue
            candidates.extend(vertices)
            candidates.append(vertices.mean(axis=0))
            axes=['xyz'.index(axis) for axis in measured['tangent_axes']]
            normal_axis=next(i for i in range(3) if i not in axes)
            lo,hi=np.asarray(measured['support_translation_bounds'])
            projected=requested.copy();projected[axes]=np.clip(projected[axes],lo,hi)
            projected[normal_axis]=vertices[0,normal_axis]
            candidates.append(projected)
            for first in vertices:
                for second in vertices:
                    delta=second-first;length=float(delta@delta)
                    if length>1e-12:
                        candidates.append(first+np.clip(float((requested-first)@delta)/length,0,1)*delta)
        for candidate in candidates:
            try:fitted,evidence=resolve(contact,parent_parts,child_bounds,child_parts,candidate,angle,
                    parent_pose=parent_pose,scene_extent=scene_extent)
            except SurfaceMountingError:continue
            solutions.append((fitted,angle,evidence))
    if not solutions:return None
    def cost(item):
        fitted,angle,_=item
        return float(np.linalg.norm(fitted-requested))+.002*abs((angle-yaw+180)%360-180)
    fitted,angle,evidence=min(solutions,key=cost)
    return {'offset':fitted.tolist(),'yaw_deg':float((angle+180)%360-180),
        'evidence':evidence,'method':'closest_verified_pose_for_agent_selected_contact'}


def audit_measured(plan, contacts, receipts, measured_parts, tolerance=.015):
    findings=[];reports={}
    for obj in plan.objects:
        contact=obj.surface_contact
        if not contact:continue
        try:
            item=contacts[obj.id]
            if item.get('method')!='evaluated_blender_mesh_contact_vertices':raise ValueError('Missing measured mounting vertices')
            if item.get('contact_side')!=contact.child_face or item.get('contact_part')!=contact.child_part:
                raise ValueError('Measured contact does not match selected mounting face/part')
            parent=receipts[obj.parent];child=receipts[obj.id]
            surfaces=members(measured_parts[obj.parent],contact.parent_part)
            axis,side,_=face_info(contact.parent_face)
            relative_yaw=child['world_rotation_deg']-parent['world_rotation_deg']
            offset=rotation(-parent['world_rotation_deg'])@(np.asarray(child['world_origin'])-parent['world_origin'])
            patch=transformed(item['local_bounds'],rotation(relative_yaw),offset)
            planes=[s[side,axis] for s in surfaces]
            if max(planes)-min(planes)>.001:raise ValueError('Measured mounting members are not coplanar')
            # The sampled slab includes bevel vertices behind the true extreme
            # contact face; its thickness is not an installation gap.
            gap=float(abs(patch[1-side,axis]-planes[0]))
            reports[obj.id]={**item,'parent_local_patch':patch.tolist(),'normal_gap_m':gap,
                             'parent_part':contact.parent_part,'parent_face':contact.parent_face}
            if gap>tolerance:raise ValueError(f'Measured mounting gap {gap:.4f} m exceeds {tolerance} m')
            if not covered(patch,surfaces,axis,tolerance):raise ValueError('Measured backplate crosses a void or misses its actual support')
        except (KeyError,TypeError,ValueError) as error:
            findings.append({'component_ids':[obj.id,obj.parent],'repair_target':'layout',
                             'evidence':f'Physical surface mounting: {error}'})
    return findings,reports
