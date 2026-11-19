"""Coupled Agent recovery of composition, spatial design and actual placements.

Stable geometry is revalidated and reused. Recovery produces an inspectable
candidate, never a scene approval; actual Blender and visual gates follow.
"""
import json
import math
from pathlib import Path

from pydantic import Field,model_validator
from general_planner import StrictModel
from scene_composition import CompositionIntent,validate_concept,validate_lowering,validate_bound_relationships,lower_typed_relationships
from scene_spatial import SpatialContract,validate_spatial_contract,validate_zone_capacity,canonical_spatial_bounds
from surface_mounting import (SurfaceContact,validate_wall_child_part_face_axis,
                              validate_wall_contact_axis,validate_wall_functional_face,
                              resolve as resolve_surface_contact,
                              SurfaceMountingError)
from staged_planner import (Layout,Decomposition,Recipe,RequestContract,assemble_plan,
    Placement,validate_recipe,spatial_outline_context,coarse_preflight,prune_unwired_proposals,SPATIAL,LAYOUT,digest)
from staged_planner import spatial_with_layout
from scene_design import SceneDesign,DESIGN_PROMPT
from scene_experience import spatial_experience_prompt
from planning_diagnostics import validate_together,combine_reports,Diagnostics,PlanningValidationError
from general_planner import to_general_plan
from general_scene import planned_envelope_bounds
from general_scene import scalar,vec
from staged_planner import validate_wall_attachment_intents


class SpatialRecovery(StrictModel):
    composition: CompositionIntent
    spatial: SpatialContract
    layout: 'RecoveryLayout'
    mounting_contacts: dict[str,SurfaceContact] = Field(default_factory=dict,
        description='Revised contacts for wall-fixed components only. Omit components already listed in a frozen window opening fitted_component_ids; those are validated against the aperture geometry, not a single wall face. Existing children keep their parent. A root parametric wall-fixed component may attach to one unique existing parametric shell host.')
    reason: str = Field(min_length=20)


class RecoveryLayout(StrictModel):
    placements: list[Placement] = Field(min_length=1,max_length=80,
        description='Every component exactly once in original order. Spatial routes and access areas belong to spatial, not layout.')

    @model_validator(mode='before')
    @classmethod
    def discard_redundant_spatial_overrides(cls,value):
        if isinstance(value,dict):
            return {key:item for key,item in value.items() if key not in {'route_points','access_centers'}}
        return value


SpatialRecovery.model_rebuild()


def derive_room_facing_wall_faces(existing_part_geometry):
    """Infer inward wall faces from measured local bounds, never from compass labels."""
    result={}
    for component_id,parts in existing_part_geometry.items():
        floor=parts.get('floor_slab')
        if floor and floor.get('bounds'):
            floor_bounds=floor['bounds']
            room_center=[(float(floor_bounds[0][axis])+float(floor_bounds[1][axis]))/2
                         for axis in range(2)]
        else:
            room_center=[0.0,0.0]
        measured={}
        for part_id,record in parts.items():
            if 'wall' not in part_id.casefold() and 'partition' not in part_id.casefold():
                continue
            bounds=record.get('bounds')
            if not bounds or len(bounds)!=2 or any(len(point)<2 for point in bounds):
                continue
            low=[float(bounds[0][axis]) for axis in range(3)]
            high=[float(bounds[1][axis]) for axis in range(3)]
            xy_size=[high[axis]-low[axis] for axis in range(2)]
            normal_axis=min(range(2),key=xy_size.__getitem__)
            center=(low[normal_axis]+high[normal_axis])/2
            delta=room_center[normal_axis]-center
            if xy_size[normal_axis]<=0 or abs(delta)<=1e-6:
                continue
            axis='xy'[normal_axis]
            faces=([f'{axis}_min',f'{axis}_max'] if abs(delta)<=1e-6 else
                   [f'{axis}_'+('max' if delta>0 else 'min')])
            measured[part_id]={
                'bounds_m':[low,high],
                'normal_axis':axis,
                'interior_face':faces[0] if len(faces)==1 else None,
                'interior_faces':faces,
                'room_center_xy_m':room_center,
                'part_center_xy_m':[(low[i]+high[i])/2 for i in range(2)],
                'evidence':'measured_blender_internal_part_bounds',
            }
        if measured:
            result[component_id]=measured
    return result


def clear_fitted_window_contacts(outline,spatial):
    """Use aperture membership as the sole mounting contract for fitted fixtures."""
    fitted={component_id for opening in spatial.openings
            for component_id in opening.fitted_component_ids}
    for obj in outline.objects:
        if obj.id in fitted:
            obj.surface_contact=None
            obj.mount='origin'
            obj.grounded=False
            obj.support_part=None
    return fitted


def revise_recovery_mounting_contacts(outline,recipes,contacts,*,aperture_fitted_ids=(),collect_errors=False):
    revised=outline.model_copy(deep=True);objects={obj.id:obj for obj in revised.objects}
    aperture_fitted_ids=set(aperture_fitted_ids)
    new_parents={};diagnostics=Diagnostics('recovery_mounting_contacts')
    for id,contact in contacts.items():
        obj=objects.get(id)
        if obj is None:
            diagnostics.add('unknown_mounting_component',
                'Mounting contact revisions require an existing component: '+id,
                paths=[f'/mounting_contacts/{id}'],component_ids=[id]);continue
        issues_before=len(diagnostics.issues)
        original_parent=obj.parent
        if id in aperture_fitted_ids:
            diagnostics.add('aperture_fixture_contact',
                f'{id}: window-aperture fixtures are verified against opening geometry; '
                'omit them from mounting_contacts and do not replace their aperture fit with a single wall contact.',
                paths=[f'/mounting_contacts/{id}'],component_ids=[id])
            continue
        if not obj.parent:
            if obj.acquisition.strategy!='parametric':
                diagnostics.add('external_root_mounting',
                    f'{id}: a root external mesh cannot be promoted through an aDSL mounting contact.',
                    paths=[f'/mounting_contacts/{id}','/layout/placements'],component_ids=[id])
                continue
            hosts=[]
            for host in revised.objects:
                recipe=recipes.get(host.id)
                if (host.id!=id and host.parent is None and host.collision_role=='shell' and recipe
                        and contact.parent_part in {part.id for part in recipe.parts}):
                    hosts.append(host)
            if len(hosts)!=1:
                diagnostics.add('mounting_host',
                    f'{id}: root mounting contact must identify one real part on a unique existing '
                    f'parametric shell; parent_part={contact.parent_part}, matching_hosts={[host.id for host in hosts]}.',
                    paths=[f'/mounting_contacts/{id}'],component_ids=[id])
                continue
            obj.parent=hosts[0].id
            new_parents[id]=hosts[0].id
        if obj.acquisition.strategy!='parametric' and contact.child_part is not None:
            diagnostics.add('external_child_part',
                f'{id}: externally sourced geometry has no aDSL internal part IDs; '
                'set child_part=null to use the measured mesh face, preserving child_face and the real parent_part.',
                paths=[f'/mounting_contacts/{id}/child_part'],component_ids=[id])
        parent=recipes.get(obj.parent)
        if parent is None:
            diagnostics.add('mounting_parent_recipe',
                f'{id}: parent {obj.parent} has no named aDSL support parts. '
                'Use a real parametric shell part; never borrow a part ID from a different object.',
                paths=[f'/mounting_contacts/{id}','/layout/placements'],component_ids=[id,obj.parent])
        elif contact.parent_part not in {part.id for part in parent.parts}:
            diagnostics.add('mounting_parent_part',
                f'{id}: parent_part={contact.parent_part} does not exist on {obj.parent}; '
                'choose one of '+str([part.id for part in parent.parts]),
                paths=[f'/mounting_contacts/{id}/parent_part'],component_ids=[id,obj.parent])
        if contact.child_part is not None and obj.acquisition.strategy=='parametric':
            child_recipe=recipes.get(id)
            child_part=next((part for part in child_recipe.parts if part.id==contact.child_part),None) if child_recipe else None
            if child_part is None:
                diagnostics.add('mounting_child_part',
                    f'{id}: child_part={contact.child_part} is not a real part in its current recipe.',
                    paths=[f'/mounting_contacts/{id}/child_part'],component_ids=[id],
                    expected={'recipe_parts':[part.id for part in child_recipe.parts] if child_recipe else []})
            else:
                part_size=None
                try:
                    dims=[scalar(value,revised.parameters) for value in obj.dimensions]
                    params={**revised.parameters,**dict(zip(('w','d','h'),dims))}
                    part_size=[scalar(value,params) for value in child_part.size]
                    validate_wall_child_part_face_axis(contact,child_part.id,dimensions=part_size)
                except (ValueError,KeyError,TypeError,ZeroDivisionError,SyntaxError) as error:
                    diagnostics.add('mounting_child_face_axis',f'{id}: {error}',
                        paths=[f'/mounting_contacts/{id}/child_face'],component_ids=[id,obj.parent],
                        measured={'part_id':child_part.id,'part_size_m':part_size,
                                  'selected_child_face':contact.child_face},
                        expected={'child_face_axis':'the part\'s thin horizontal axis'})
        elif contact.child_part is None:
            dims=None
            try:
                dims=[scalar(value,revised.parameters) for value in obj.dimensions]
                validate_wall_contact_axis(contact,dims)
                validate_wall_functional_face(contact)
            except (ValueError,KeyError,TypeError,ZeroDivisionError,SyntaxError) as error:
                diagnostics.add('mounting_child_face_axis',f'{id}: {error}',
                    paths=[f'/mounting_contacts/{id}/child_face'],component_ids=[id,obj.parent],
                    measured={'component_dimensions_m':dims,
                              'selected_child_face':contact.child_face},
                    expected={'child_face_axis':'the component\'s thin horizontal axis'})
        if len(diagnostics.issues)>issues_before:
            if original_parent is None:
                obj.parent=None
                new_parents.pop(id,None)
            continue
        obj.surface_contact=contact
        obj.mount='origin';obj.grounded=False;obj.support_part=None
    contact_report=diagnostics.report()
    if diagnostics.issues and not collect_errors:raise PlanningValidationError(contact_report)
    if collect_errors:return revised,new_parents,contact_report
    return revised,new_parents


def localize_reparented_placements(outline,layout,new_parents):
    objects={obj.id:obj for obj in outline.objects}
    placements={placement.id:placement for placement in layout.placements}
    for id,parent_id in new_parents.items():
        child=placements.get(id);host=placements.get(parent_id)
        if child is None or host is None:
            raise ValueError(f'{id}: reparenting requires placements for both child and shell host {parent_id}.')
        host_object=objects[parent_id]
        if host_object.parent is not None or host_object.collision_role!='shell':
            raise ValueError(f'{id}: promoted mounting parent must be a root architectural shell: {parent_id}.')
        def resolved_position(obj,placement):
            dims=[scalar(value,outline.parameters) for value in obj.dimensions]
            params={**outline.parameters,**dict(zip(('w','d','h'),dims))}
            return [scalar(value,params) for value in placement.position]
        child_world=resolved_position(objects[id],child)
        host_world=resolved_position(host_object,host)
        angle=math.radians(host.yaw_deg)
        dx=child_world[0]-host_world[0];dy=child_world[1]-host_world[1]
        child.position=(math.cos(angle)*dx+math.sin(angle)*dy,
            -math.sin(angle)*dx+math.cos(angle)*dy,child_world[2]-host_world[2])
        child.yaw_deg=(child.yaw_deg-host.yaw_deg)%360


def validate_support_offsets(outline,layout):
    """Catch duplicated support heights together before expensive assembly."""
    placements={p.id:p for p in layout.placements}
    report=Diagnostics('support_offsets')
    for index,obj in enumerate(outline.objects):
        if not obj.parent or obj.mount not in {'top','bottom'} or obj.id not in placements:continue
        dims=[scalar(v,outline.parameters) for v in obj.dimensions]
        params={**outline.parameters,**dict(zip(('w','d','h'),dims))}
        z=scalar(placements[obj.id].position[2],params)
        if abs(z)>.03:
            report.add('duplicated_support_height',f'{obj.id}: mount={obj.mount} already adds the '
                f'height of {obj.parent}/{obj.support_part or "envelope"}; local Z={z:.3f}m '
                'would separate the object from its support. Use local Z=0 for contact.',
                paths=[f'/layout/placements/{index}/position'],component_ids=[obj.id,obj.parent],
                measured={'additional_normal_offset_m':z},expected={'contact_local_z_m':0})
    report.raise_if_any()


def require_reconstruction_preflight(report):
    """Do not let a blocked coupled plan reach Blender as a candidate."""
    if report.get('status')=='passed' and report.get('readiness')=='ready_for_3d_audit':
        return report
    measurements=dict(report)
    measurements.setdefault('stage','spatial_recovery_preflight')
    issues=list(measurements.get('issues') or [])
    if not issues:
        for finding in measurements.get('blocking_findings',[]):
            issues.append({'code':finding.get('constraint_id') or 'coarse_preflight',
                'evidence':finding.get('evidence','Blocking spatial preflight finding.'),
                'paths':finding.get('paths',[]),'component_ids':finding.get('component_ids',[]),
                'scenario':finding.get('scenario','baseline'),
                **({'measured':finding['measured']} if finding.get('measured') is not None else {})})
    if not issues:
        issues.append({'code':'preflight_not_ready',
            'evidence':f"Coarse preflight returned status={report.get('status')!r}, readiness={report.get('readiness')!r}.",
            'paths':['/composition','/spatial','/layout'],'component_ids':[],'scenario':'baseline'})
    measurements['issues']=issues
    raise PlanningValidationError(measurements)


def load_recovery_request_contract(runtime):
    runtime=Path(runtime)
    for name in ('request_contract_accepted.json','frozen_request_contract.json'):
        path=runtime/name
        if path.is_file():
            return RequestContract.model_validate_json(path.read_text(encoding='utf-8'))
    raise FileNotFoundError(f'Spatial recovery runtime has no accepted or frozen request contract: {runtime}')


def load_recovery_recipe(runtime,component_id,previous_components=None):
    component=(previous_components or {}).get(component_id)
    if component is not None:
        return Recipe.model_validate({'parts':component['parts'],'assembly':component.get('assembly')})
    runtime=Path(runtime)
    for name in (f'recipe_{component_id}_recovered.json',f'recipe_{component_id}_reused.json',
                 f'recipe_{component_id}_accepted.json'):
        path=runtime/name
        if path.is_file():
            return Recipe.model_validate_json(path.read_text(encoding='utf-8'))
    raise FileNotFoundError(f'Spatial recovery runtime has no validated recipe for {component_id}: {runtime}')


def load_recovery_design(runtime,explicit_path=None,brief=None):
    """Reuse an accepted design from this run or its same-brief asset source."""
    runtime=Path(runtime)
    candidates=[]
    if explicit_path:candidates.append(Path(explicit_path))
    candidates.extend(runtime/name for name in
        ('scene_design.json','scene_design_accepted.json','scene_design_checkpoint.json'))
    origin_path=runtime.parent/'run_origin.json'
    origin=json.loads(origin_path.read_text(encoding='utf-8')) if origin_path.is_file() else {}
    prepared=origin.get('prepared_assets_from')
    if prepared:
        source=Path(prepared)
        if not source.is_absolute():source=(runtime.parent.parent.parent/source).resolve()
        source_run=source.parent if source.name=='agent_runtime' else source
        source_origin_path=source_run/'run_origin.json'
        source_origin=json.loads(source_origin_path.read_text(encoding='utf-8')) if source_origin_path.is_file() else {}
        if brief and source_origin.get('brief')==brief:
            source_runtime=source if source.name=='agent_runtime' else source/'agent_runtime'
            candidates.extend(source_runtime/name for name in
                ('scene_design.json','scene_design_accepted.json','scene_design_checkpoint.json'))
    for path in candidates:
        if not path.is_file():continue
        data=json.loads(path.read_text(encoding='utf-8'))
        if 'room_identity' not in data and isinstance(data.get('candidate'),dict):
            data=data['candidate']
        try:design=SceneDesign.model_validate(data)
        except (TypeError,ValueError):continue
        return design,str(path)
    raise FileNotFoundError(f'Spatial recovery runtime has no accepted scene design for the current request: {runtime}')


def load_recovery_candidate(runtime):
    """Reuse the newest failed, pre-build spatial candidate as a validation seed."""
    runtime=Path(runtime)
    paths=sorted(runtime.glob('spatial_reconstruction_candidate_failure_*.json'),
        key=lambda path:int(path.stem.rsplit('_',1)[1]),reverse=True)
    for path in paths:
        saved=json.loads(path.read_text(encoding='utf-8'))
        candidate=saved.get('candidate')
        if not isinstance(candidate,dict):continue
        unbuilt=bool(saved.get('measurements') or saved.get('error'))
        return candidate,str(path),unbuilt
    return None,None,False


async def revalidate_or_repair_recipe(agent,outline,obj,recipe,brief,style_requirements):
    """Reuse valid aDSL; hand stale recipes back to their component Agent."""
    try:
        return validate_recipe(outline,obj,recipe,style_requirements),False
    except ValueError as failure:
        measurements=getattr(failure,'measurements',None)
        agent.save(f'recipe_{obj.id}_reuse_validation_failure.json',measurements or {
            'stage':'recipe_'+obj.id,'error':str(failure)})
        repaired=await agent.component_recipe(outline,obj,brief,style_requirements,
            prior_candidate=recipe.model_dump(mode='json'))
        return validate_recipe(outline,obj,repaired,style_requirements),True


def load_recovery_spatial_contract(runtime):
    runtime=Path(runtime)
    frozen=runtime/'frozen_spatial_contract.json'
    accepted=runtime/'spatial_plan_accepted.json'
    actual_plan=runtime.parent/'general_plan.json'
    candidate=runtime/'spatial_reconstruction_candidate_accepted.json'
    if frozen.is_file():
        path=frozen;data=json.loads(path.read_text(encoding='utf-8')).get('contract')
    elif accepted.is_file():
        path=accepted;data=json.loads(path.read_text(encoding='utf-8'))
    elif actual_plan.is_file():
        path=actual_plan;data=json.loads(path.read_text(encoding='utf-8')).get('spatial')
    elif candidate.is_file():
        path=candidate;data=json.loads(path.read_text(encoding='utf-8')).get('spatial')
    else:
        raise FileNotFoundError(f'Spatial recovery runtime has no prior spatial contract: {runtime}')
    if data is None:
        raise ValueError(f'Prior spatial contract is missing in {path}')
    return SpatialContract.model_validate(data),str(path)


async def recover_spatial_scene(agent,brief):
    old=agent.resume_runtime
    def read(name):return json.loads((old/name).read_text(encoding='utf-8'))
    outline=Decomposition.model_validate(read('frozen_decomposition.json')['plan'])
    contract=load_recovery_request_contract(old)
    design_path=(agent.replan_feedback or {}).get('recovery_design_path')
    design,design_source=load_recovery_design(old,design_path,brief)
    prior_spatial,spatial_source=load_recovery_spatial_contract(old)
    fitted_window_ids=clear_fitted_window_contacts(outline,prior_spatial)
    spatial_data=prior_spatial.model_dump(mode='json')
    agent.save('frozen_spatial_contract.json',{'hash':digest(spatial_data),
        'contract':spatial_data,'source':spatial_source})
    recipes={}
    previous_path=old/'validated_plan.json'
    previous_components={}
    if previous_path.is_file():
        previous=json.loads(previous_path.read_text(encoding='utf-8'))
        previous_components={c['id']:c for c in previous['components']}
    for obj in outline.objects:
        if obj.acquisition.strategy!='parametric':continue
        recipe=load_recovery_recipe(old,obj.id,previous_components)
        recipes[obj.id],repaired=await revalidate_or_repair_recipe(
            agent,outline,obj,recipe,brief,contract.style_requirements)
        if repaired:
            agent.save('recipe_'+obj.id+'_recovered.json',recipes[obj.id].model_dump(mode='json'))
        else:
            agent.save('recipe_'+obj.id+'_reused.json',recipes[obj.id].model_dump(mode='json'))
    agent.save('frozen_request_contract.json',contract.model_dump(mode='json'))
    agent.save('frozen_decomposition.json',{'plan':outline.model_dump(mode='json'),
        'source':str(old/'frozen_decomposition.json')})
    agent.save('recovery_provenance.json',{'mode':'coupled_spatial_recovery','source_runtime':str(old),
        'design_source':design_source,
        'spatial_source':spatial_source,
        'reused_geometry_components':list(recipes),'scene_accepted':False})
    fixed=spatial_outline_context(outline)
    fixed={k:v for k,v in fixed.items() if k in {'extent','parameters','objects','authority'}}
    for obj in fixed['objects']:obj.pop('interface_notes',None)
    evidence=agent.spatial_recovery_feedback()
    findings=(evidence.get('geometry') or {}).get('findings',[])
    seen=set();compact=[]
    for finding in findings:
        key=json.dumps({k:finding.get(k) for k in ('component_ids','evidence','constraint_id')},sort_keys=True)
        if key in seen:continue
        seen.add(key);compact.append({k:v for k,v in finding.items() if k not in {'edit_scenario'}})
    evidence['geometry']={'findings':compact}
    visual=evidence.get('visual') or {}
    decision=visual.get('decision',visual)
    evidence['visual']={k:decision[k] for k in
        ('summary','verdict','findings','style_assessments','layout_audits') if k in decision}
    # Batch images, repeated raw responses and transport logs remain on disk;
    # sending them again hides the actual issues and wastes context/tokens.
    evidence.pop('detailed_planning_failure',None)
    context={'brief':brief,'fixed_geometry':fixed,
        'design_bindings':outline.design_bindings,'existing_design':{
            'room_identity':design.room_identity,'composition':design.composition.model_dump(mode='json') if design.composition else None,
            'choices':[{k:c.model_dump(mode='json')[k] for k in ('id','selection','activity')} for c in design.choices]},
        'existing_apertures':[opening.model_dump(mode='json') for opening in prior_spatial.openings],
        'measured_failure_evidence':evidence,
        'original_user_requirements':contract.scene_use.model_dump(mode='json') if contract.scene_use else None,
        'placement_frames':[{'id':o.id,'parent_id':o.parent,'mount':o.mount,
            'support_part':o.support_part,'surface_contact':o.surface_contact.model_dump() if o.surface_contact else None,
            'coordinate_frame':'parent_local' if o.parent else 'scene_centered',
            'contact_z_offset':0 if o.mount in {'top','bottom'} else None} for o in outline.objects],
        'component_part_ids':{id:[p.id for p in r.parts] for id,r in recipes.items()},
        'parametric_shell_mounting_hosts':[{'component_id':obj.id,
            'part_ids':[part.id for part in recipes[obj.id].parts]}
            for obj in outline.objects if obj.collision_role=='shell' and obj.id in recipes]}
    measured_dir=(agent.replan_feedback or {}).get('previous_actual_geometry_directory')
    if measured_dir and (Path(measured_dir)/'build_receipts.json').is_file():
        receipts=json.loads((Path(measured_dir)/'build_receipts.json').read_text(encoding='utf-8'))
        context['existing_part_geometry']={id:{part:{k:record[k] for k in ('bounds','size','center') if k in record}
            for part,record in receipt.get('internal_geometry',{}).get('parts',{}).items()}
            for id,receipt in receipts.items() if id in recipes}
        context['room_facing_wall_faces']=derive_room_facing_wall_faces(context['existing_part_geometry'])
    seed_path=(agent.replan_feedback or {}).get('recovery_layout_path')
    if seed_path:
        seed=json.loads(Path(seed_path).read_text(encoding='utf-8'))
        context['previous_layout']=seed.get('layout',seed)
    def revised_outline(value):
        return revise_recovery_mounting_contacts(outline,recipes,value.mounting_contacts,
            aperture_fitted_ids=fitted_window_ids,collect_errors=True)
    def validate(value):
        current,new_parents,mounting_report=revised_outline(value)
        layout=Layout(placements=[Placement.model_validate(item.model_dump(mode='json'))
            for item in value.layout.placements])
        localize_reparented_placements(current,layout,new_parents)
        composed=design.model_copy(update={'composition':value.composition})
        value.spatial=value.spatial.model_copy(update={'requirements':contract.scene_use})
        value.spatial=canonical_spatial_bounds(value.spatial,current.extent)
        lowering_error=None
        try:value.spatial=lower_typed_relationships(value.spatial,composed,current)
        except (ValueError,KeyError,IndexError) as error:lowering_error=error
        fixed_openings={o['id']:{k:o[k] for k in ('kind','wall','offset','width','sill_height','height')}
            for o in context['existing_apertures']}
        proposed={o.id:{k:o.model_dump()[k] for k in ('kind','wall','offset','width','sill_height','height')}
            for o in value.spatial.openings}
        def check_apertures():
            if proposed==fixed_openings:return
            raise ValueError('Opening apertures are fixed by the reused architectural mesh. Preserve these exact '
                'opening IDs and numeric fields, and move/reorient the separate fixtures to fit them: '+json.dumps(fixed_openings))
        def check_inventory():
            if [p.id for p in layout.placements]!=[o.id for o in current.objects]:
                raise ValueError('Return every fixed component exactly once in its original order.')
        def check_lowering():
            if lowering_error:raise ValueError(str(lowering_error))
            validate_lowering(value.spatial,composed,current)
        semantic_report=None
        try:validate_together('spatial_recovery',[
            ('composition',lambda:validate_concept(composed)),
            ('wall_attachment_intents',lambda:validate_wall_attachment_intents(current,
                fitted_component_ids=fitted_window_ids,
                room_facing_wall_faces=context.get('room_facing_wall_faces'))),
            ('contacts',lambda:validate_bound_relationships(composed,current)),
            ('lowering',check_lowering),
            ('spatial',lambda:validate_spatial_contract(value.spatial,current.objects,current.extent)),
            ('capacity',lambda:validate_zone_capacity(value.spatial,current.objects,current.parameters,current.edit_checks)),
            ('apertures',check_apertures),('inventory',check_inventory),
            ('support_offsets',lambda:validate_support_offsets(current,layout))])
        except PlanningValidationError as failure:
            semantic_report=failure.measurements
        contact_fit_failures=[]
        if context.get('existing_part_geometry'):
            from layout_solver import _frames
            from surface_mounting import fit_contact_pose
            _,poses=_frames(current,layout)
            placements={p.id:p for p in layout.placements};corrections={}
            for obj in current.objects:
                if not obj.surface_contact:continue
                parent_parts=context['existing_part_geometry'].get(obj.parent)
                if not parent_parts:continue
                dims=[scalar(v,current.parameters) for v in obj.dimensions]
                params={**current.parameters,**dict(zip(('w','d','h'),dims))}
                placement=placements[obj.id]
                before=placement.model_dump(mode='json')
                child_parts=context['existing_part_geometry'].get(obj.id,{})
                fitted=fit_contact_pose(obj.surface_contact,parent_parts,
                    [[-dims[0]/2,-dims[1]/2,0],[dims[0]/2,dims[1]/2,dims[2]]],child_parts,
                    vec(placement.position,params),placement.yaw_deg,
                    parent_pose=poses[obj.parent],scene_extent=current.extent)
                if fitted is None:
                    try:
                        resolve_surface_contact(obj.surface_contact,parent_parts,
                            [[-dims[0]/2,-dims[1]/2,0],[dims[0]/2,dims[1]/2,dims[2]]],
                            child_parts,vec(placement.position,params),placement.yaw_deg,
                            parent_pose=poses[obj.parent],scene_extent=current.extent)
                        failure_evidence={'reason':'No feasible orientation/translation was found for the selected contact.'}
                    except SurfaceMountingError as failure:
                        failure_evidence={'code':failure.code,'reason':str(failure),
                                          'measurements':failure.measurements}
                    index=next(i for i,item in enumerate(current.objects) if item.id==obj.id)
                    contact_fit_failures.append({'code':'mounting_contact_unresolved',
                        'evidence':f'{obj.id}: selected physical contact does not fit the measured parent surface; '
                                   'the prior interface was not substituted.',
                        'paths':[f'/mounting_contacts/{obj.id}',f'/layout/placements/{index}/position',
                                 f'/layout/placements/{index}/yaw_deg'],
                        'component_ids':[obj.id,obj.parent],
                        'scenario':'baseline','measured':{'selected_contact':obj.surface_contact.model_dump(mode='json'),
                            'agent_position_m':list(placement.position),'agent_relative_yaw_deg':placement.yaw_deg,
                            'failure':failure_evidence},
                        'expected':{'action':'revise the selected real parent/child faces or their Agent-authored pose',
                            'fallback':'disabled'}})
                    continue
                if fitted:
                    placement.position=tuple(fitted['offset']);placement.yaw_deg=fitted['yaw_deg']
                    if placement.model_dump(mode='json')!=before:
                        corrections[obj.id]={'agent_pose':before,'compiled_pose':placement.model_dump(mode='json'),
                            'method':fitted['method'],'contact':obj.surface_contact.model_dump(mode='json'),
                            'interface_basis':'current_agent_contact'}
            agent.save('compiler_contact_corrections.json',corrections)
        if contact_fit_failures:
            fit_report=Diagnostics('mounting_contact_fit')
            fit_report.issues.extend(contact_fit_failures)
            mounting_report=combine_reports('recovery_mounting_contacts',mounting_report,fit_report.report())
        report=coarse_preflight(current,layout,value.spatial)
        if report.get('status')!='passed':
            from layout_solver import solve_layout
            solved,solver_receipt=solve_layout(current,layout,value.spatial)
            if solved is not None:
                layout=solved
                value.spatial=spatial_with_layout(value.spatial,layout,current.objects,
                    current.extent,validate=False)
                report=coarse_preflight(current,layout,value.spatial)
                agent.save('reconstruction_numerical_layout_solution.json',layout.model_dump(mode='json'))
            elif solver_receipt.get('candidate'):
                layout=Layout.model_validate(solver_receipt['candidate'])
                value.spatial=spatial_with_layout(value.spatial,layout,current.objects,
                    current.extent,validate=False)
                report=coarse_preflight(current,layout,value.spatial)
            report['numerical_solver']=solver_receipt
            agent.save('reconstruction_numerical_layout_solver.json',solver_receipt)
        agent.save('reconstruction_preflight.json',report)
        preflight_report=None
        try:require_reconstruction_preflight(report)
        except PlanningValidationError as failure:
            preflight_report=failure.measurements
        combined=combine_reports('spatial_recovery',mounting_report,semantic_report,preflight_report)
        if combined['issues'] or combined['blocked_checks']:
            raise PlanningValidationError(combined)
        build_outline,_=prune_unwired_proposals(current,recipes,layout)
        try:
            build_plan=assemble_plan(build_outline,recipes,layout,spatial=value.spatial,
                style_requirements=contract.style_requirements,reconstruction_only=True)
            executable,_=to_general_plan(build_plan,check_layout=False,check_geometry=False)
            # A surface normal/contact domain must be constructible before Blender export.
            planned_envelope_bounds(executable)
        except (ValueError,KeyError,TypeError,IndexError,ZeroDivisionError,SyntaxError) as failure:
            raise PlanningValidationError(combine_reports('spatial_recovery',
                failure_report(failure,'reconstruction_geometry'))) from failure
        value.layout.placements=layout.placements
        return value
    seed=None;unbuilt_seed=False
    candidate_path=(agent.replan_feedback or {}).get('recovery_candidate_path')
    if candidate_path:
        saved=json.loads(Path(candidate_path).read_text(encoding='utf-8'))
        seed=saved.get('candidate',saved)
        unbuilt_seed=bool(saved.get('candidate') and (saved.get('measurements') or saved.get('error')))
        agent.save('recovery_candidate_input.json',{'source':candidate_path,'candidate':seed})
    else:
        seed,candidate_path,unbuilt_seed=load_recovery_candidate(old)
        if seed is not None:
            agent.save('recovery_candidate_input.json',{'source':candidate_path,'candidate':seed,
                'reuse_basis':'latest_failed_prebuild_candidate_revalidated'})
    if ((agent.replan_feedback or {}).get('replan_reason')=='coupled_spatial_replan_from_reconstructed_evidence'
            and not unbuilt_seed):
        # A structurally valid checkpoint is still the rejected scene. Ask the
        # Agent for a new coupled proposal informed by the actual scene audit.
        seed=None
    candidate=await agent.stage('spatial_reconstruction_candidate',SpatialRecovery,
        '''Return JSON matching the supplied joint schema. First imagine the whole place, then compose
activity groups, then lower every intended relationship to actual component IDs and poses. Preserve all
choice IDs and their design bindings. In each group, explicit composition relationships must connect every
choice to its anchor through a graph path, including floor, wall, door and other architectural choices; do
not assume they are connected just because they are part of one shell. Lower every design relationship
exactly once to component-level composition links.
The compiler creates composition_groups and the exact component relations from your composition choice
relationships and existing design_bindings. Do NOT transcribe those relationships into spatial.relations.
You may leave composition_groups/composition_links empty: the compiler binds them from choices and real
route connectivity. Only include additional nonduplicated cross-group spatial relations when necessary;
supported_by/attached_to belong in composition relationships and the existing parent graph, NEVER in
spatial.relations. Choices realized by one component may use integrated attached_to. Bind empty-space
intents to genuinely protected open_spaces and views to human experience_views.
Before using east/west/north/south, map each shell side to its world-boundary axis and sign from the shell's
interface notes/semantic and measured wall-part bounds. Apply that one mapping consistently to openings,
zones, placements, relations and mounting hosts. Part IDs alone do not establish compass direction. If frozen
prose and geometry disagree, report the mismatch and follow measured shell geometry; do not silently retain
a cardinal label while placing the group on the opposite side.
All directions are A relative to B in B's local frame: front=-Y, back=+Y, left=-X, right=+X. Facing means
A's local front points toward B. Only distance/facing may set maximum_m; directional/contact relations
use null. Contacts have minimum_m=0. Keep justified intervals broad enough for the actual activity.
Scene X/Y coordinates are centered, Z=0 is the finished floor. Dimensions are unrotated local X/Y/Z.
Root positions are world bottom centers; child positions are parent-local and rotate with parent yaw.
top mounts start on the actual parent support surface; explicit surface_contact resolves normal alignment.
Every required human-use root gets an owner-local access_area with side front/back/left/right, a center
expression based on w/d and access size. Build a connected route graph with one shared entrance aisle to a
primary activity approach and only the short branches needed for other distinct activities; do not create
a separate full-length entrance route for every furniture item. Each route start/end must reference an
existing entrance opening ID or access_area ID, never a component, choice or group ID; every required
access area must be connected to an entrance. An enclosing shell has no full-room access_area.
Routes and zones are world space; do not confuse side with compass. Put route coordinates only in
spatial.routes[*].points and access centers only in spatial.access_areas[*].center. The recovery layout
contains placements only; omit layout.route_points and layout.access_centers, and never put endpoint IDs
inside coordinate arrays.
Separate door leaves belong to the real entrance's swing_leaf_ids; fitted fixtures belong to the window's
fitted_component_ids. A fixture listed in a frozen window opening is verified by its measured fit to that
aperture; omit it from mounting_contacts and do not force its sill/frame into one generic wall contact.
For every other wall fixture, use only room_facing_wall_faces supplied in context. These records are derived
from measured Blender part bounds in the shell's local coordinates; their interior_face is authoritative even
when the wall part's compass name is misleading. Match both the actual parent_part ID and its interior_face,
then orient the fixture's functional face toward the room. The common component frame has functional front
-Y and rear +Y. When the measured thin/back axis is Y, select child_face=y_max: y_min is the front and
mounting it turns the functional side into the wall. The compiler derives world yaw from measured normals.
For a parametric child_part, also select an actual broad planar face on its measured thin axis. A back panel
whose recipe size is wide X x thin Y x tall Z contacts through y_min or y_max, never x_min/x_max. Do not
infer wall sides from names alone.
Architectural contact parts refer to the supplied existing component_part_ids.
Use rich, purposeful groups, intentional density contrasts, legible approaches and representative views.
Natural variation serves the activity; it does not mean random rotations or scattered isolated props.
For every seating/conversation group, reason about the furniture as one arrangement: orient sofa and chairs
toward one shared interaction focus, place the shared table in front of the occupied seats and within
reach, and keep it from splitting the group or blocking seated sightlines. Encode the intended front-facing
relationships and the table-to-seating directional relationship in the spatial contract; do not give every
seat the same yaw by default. For other activity groups, orient each object's functional face toward the
person or shared target that uses it, and verify the whole group rather than each object in isolation.
You are recovering an existing rejected 3D scene, not creating new geometry. Return the supplied joint
schema. Revise composition, spatial contract and placements TOGETHER so invented design constraints
cannot trap a position-only repair. Preserve choice IDs, all existing physical components, dimensions,
sources, materials and user requirements. Infer the whole arrangement from activities, not a catalogue.
Do not make a positive distance constraint from an object to an enclosing shell: their footprints overlap.
Architectural layers realized by one component use integrated attached_to. Supported props must use their
actual parent/interface, not arbitrary global coordinates. Layout positions of children are PARENT-LOCAL.
For mount=top/bottom, local Z MUST be zero for contact: the compiler adds the actual support height.
For support_part, XY is an offset from that support part's center. Never add a table or shelf height
again. A child's yaw is relative to its parent's yaw, not a desired world angle.
Choose aperture-compatible fixture orientations from their actual width/depth; preserve existing apertures.
Entry leaves must stand beside the correct jamb: their long dimension runs along the wall normal and only
their thin edge lies across the aperture tangent. Compute this from world yaw including the parent yaw; yaw=0
is not inherently edge-on. The object front is local -Y. All directional relations
are A relative to B's local frame. A footrest is in_front_of its chair; a table is in_front_of its seating.
Use distance-only relationships for nondirectional adjacency. Do not encode every incidental preference
as a tight numerical constraint. Empty volumes must actually be empty, not include the fixtures they serve.
Avoid an enclosing fixture's back accidentally dominating the room view. Think from arrival and seating
perspectives as well as plan view. Return every component placement in the original inventory order.
Correct mounting contacts against real listed parts. Existing children keep their parent. A root-level
parametric wall-fixed/built-in component may become a child of one unique root parametric shell only when
its mounting contact names a real part on that shell. The compiler converts its scene-centered root pose
to the shell-local frame before checking contact; do not change its geometry, source or dimensions. For
mounting_contacts.parent_part, choose only from parametric_shell_mounting_hosts[component_id].part_ids for
the selected host, never from the mounted child's component_part_ids. Do not change any other parent edge.
Only parametric parents have named aDSL surfaces: never invent a part of an external-mesh parent.
For children on an external source, retain its inherited top/bottom mount with local Z=0 instead.
This is a reconstruction candidate. Actual Blender measurements and the system's visual Judge will decide
quality and return all meaningful issues together; do not claim that your proposal is already accepted.
An explicit north/south/east/west-wall, wall-mounted, built-in, or flush-to-wall intent must contact the
actual room-facing wall part through a vertical surface_contact face. A floor mount or nearby coordinate
does not satisfy wall integration. The preflight must pass before any 3D build is allowed.
''' + spatial_experience_prompt(brief),context,validate,
        lambda data:['/composition','/spatial','/layout','/mounting_contacts','/reason'],initial_candidate=seed)
    outline,_=revised_outline(candidate)
    layout=Layout(placements=[Placement.model_validate(item.model_dump(mode='json'))
        for item in candidate.layout.placements])
    outline,unwired=prune_unwired_proposals(outline,recipes,layout)
    plan=assemble_plan(outline,recipes,layout,spatial=candidate.spatial,
        style_requirements=contract.style_requirements,reconstruction_only=True)
    plan.design_program=design.model_copy(update={'composition':candidate.composition})
    plan.design_bindings=outline.design_bindings
    agent.save('scene_design.json',plan.design_program.model_dump(mode='json'))
    agent.save('reconstruction_candidate_plan.json',plan.model_dump(mode='json',by_alias=True))
    agent.save('planning_summary.json',{'status':'reconstruction_candidate','accepted_scene':False,
        'backend':'coupled_spatial_recovery','model_calls':len(agent.transport_calls),
        'reused_recipe_count':len(recipes),'note':'Physical validation and visual approval require actual reconstruction.'})
    return plan
