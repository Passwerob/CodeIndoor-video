"""Turn actual QA failures into validated, reviewable repair work orders."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path

import finescene_env as fenv


def repair_work_orders(plan, geometry, visual):
    by_id={c.id:c for c in plan.components}
    orders=[]
    def descendants(ids):
        affected=set(ids)
        while True:
            expanded=affected|{c.id for c in plan.components if (c.parent or c.support) in affected}
            if expanded==affected:return sorted(affected)
            affected=expanded
    for finding in geometry.get('findings',[]):
        ids=list(dict.fromkeys(i.split('/')[0] for i in finding.get('component_ids',[]) if i.split('/')[0] in by_id))
        target=finding.get('repair_target','layout')
        part_ids=list(dict.fromkeys(finding.get('part_ids',[])))
        orders.append({'stage':target,'component_ids':ids,'part_ids':part_ids,'evidence':finding['evidence'],
                       'finding_code':finding.get('code'),
                       'repair_part_ids':part_ids[:1] if target=='geometry' and _pinpointed(finding) else part_ids,
                       'needs_decomposition_review':not bool(ids) or (target=='geometry' and not part_ids),
                       'revalidate':descendants(ids),'allowed_fields':['parts'] if target=='geometry' else ['position','orientation_deg'],
                       'frozen_fields':['id','semantic','dimensions','source','assembly','rigidity'],
                       'status':'pending_repair'})
    for finding in visual.get('decision',{}).get('findings',[]):
        if finding.get('severity')!='blocking':continue
        target=finding.get('repair_target','decomposition')
        ids=[i for i in finding.get('component_ids',[]) if i in by_id]
        fields={'layout':['position','orientation_deg'],'geometry':['parts'],
                'source':['source'],'materials':['material_requirements','parts'],
                'camera':['camera_expectations'],'decomposition':['parent','support','attachment','grounded','dimensions']}[target]
        part_ids=list(dict.fromkeys(finding.get('part_ids',[])))
        orders.append({'stage':target,'component_ids':ids,'part_ids':part_ids,'evidence':finding.get('evidence'),
                       'view':finding.get('view'),'correction':finding.get('correction'),
                       'finding_code':finding.get('code'),
                       'repair_part_ids':part_ids[:1] if target=='geometry' and _pinpointed(finding) else part_ids,
                       'allowed_fields':fields,'revalidate':descendants(ids),
                       'status':'pending_repair','needs_decomposition_review':target=='decomposition' or not ids or
                           (target=='geometry' and not finding.get('part_ids'))})
    if visual.get('status')=='unavailable':
        orders.append({'stage':'visual_service','component_ids':[],
                       'evidence':visual.get('error_type','unavailable'),
                       'allowed_fields':[],'status':'retry_review_with_existing_images',
                       'revalidate':[],'instruction':'Preserve scene and assets; service failure is not evidence of bad geometry.'})
    return {'plan_sha256':hashlib.sha256(json.dumps(plan.model_dump(mode='json'),sort_keys=True).encode()).hexdigest(),
            'automatic_execution':False,'orders':orders,
            'rule':'Only new measured geometry and image evidence can change acceptance; these are work orders, not completed fixes.'}


def _pinpointed(finding):
    """Whether a geometry finding names the one part that may legitimately move.

    Contact complaints carry a measured code. A visual complaint has no code at
    all, but it does carry part_ids when the Judge could attribute it to a part,
    and those are equally safe to repair one part at a time.
    """
    if finding.get('code') in {'support_gap','support_footprint','intersection','attachment'}:
        return True
    return not finding.get('code') and bool(finding.get('part_ids'))


def allowed_repair_paths(plan, orders):
    """Freeze topology, interfaces, sources, requirements and rigidity after planning."""
    paths=set()
    for order in orders:
        if order['stage']=='camera':
            paths.add('/intent/review_azimuth_deg')
            if plan.spatial:
                paths.update(f'/spatial/experience_views/{j}/{key}' for j,_ in enumerate(plan.spatial.experience_views)
                             for key in ('position','target','lens_mm'))
        for i,component in enumerate(plan.components):
            if component.id not in order['component_ids']:continue
            # A component-level geometry complaint does not identify which
            # internal dimensions are safe to edit. Keep it out of local
            # geometry repair and let the outer planner revisit decomposition.
            if order['stage']=='geometry' and not order.get('part_ids'):continue
            if order['stage']=='layout':
                if plan.spatial:
                    paths.update(f'/spatial/routes/{j}/points' for j,_ in enumerate(plan.spatial.routes))
                    paths.update(f'/spatial/access_areas/{j}/center' for j,a in enumerate(plan.spatial.access_areas)
                                 if a.owner_id in order['component_ids'])
                if order.get('part_ids'):
                    for j,part in enumerate(component.parts):
                        if part.id in order['part_ids']:
                            paths.update((f'/components/{i}/parts/{j}/center',f'/components/{i}/parts/{j}/rotation'))
                else:paths.update((f'/components/{i}/position',f'/components/{i}/orientation_deg'))
            if component.source.mode=='parametric' and order['stage'] in {'geometry','materials'}:
                for j,part in enumerate(component.parts):
                    if order.get('part_ids') and part.id not in order['part_ids']:continue
                    if order['stage']=='materials':
                        keys=('color','finish','surface')
                    elif order.get('finding_code')=='support_gap':
                        if part.id not in set(order.get('repair_part_ids',[])):continue
                        keys=('center',)
                    elif order.get('finding_code')=='support_footprint':
                        if part.id not in set(order.get('repair_part_ids',[])):continue
                        keys=(('center','rotation') if part.rigidity=='rigid' or component.rigidity=='rigid'
                              else ('size','center','rotation'))
                    elif order.get('finding_code') in {'intersection','attachment'}:
                        keys=('center','rotation')
                    else:
                        keys=(('center','rotation') if part.rigidity=='rigid' or component.rigidity=='rigid'
                              else ('size','center','rotation','path','curve','radius','radius_end') if part.primitive=='sweep'
                              else ('size','center','rotation','profile') if part.primitive=='lathe'
                              else ('size','center','rotation'))
                    paths.update(f'/components/{i}/parts/{j}/{key}' for key in keys)
                _extend_to_copy_families(paths,i,component.parts)
    return sorted(paths)


def _extend_to_copy_families(paths,i,parts):
    """Copies (like/mirror) must stay identical: a repair that may change a design
    field of one family member may change it on every member, and a movable
    source center unlocks its mirrored copies' centers."""
    index={p.id:j for j,p in enumerate(parts)}
    families={}
    for p in parts:
        families.setdefault(p.like or p.id,[]).append(p)
    for root,members in families.items():
        if len(members)<2:continue
        keys={path.rsplit('/',1)[1] for m in members for path in paths
              if path.startswith(f'/components/{i}/parts/{index[m.id]}/')}
        design=keys-{'center'}
        for m in members:
            paths.update(f'/components/{i}/parts/{index[m.id]}/{key}' for key in design)
            if m.mirror!='none' and root in index and f'/components/{i}/parts/{index[root]}/center' in paths:
                paths.add(f'/components/{i}/parts/{index[m.id]}/center')


def _active_repair_orders(plan, orders):
    """Keep every locally actionable finding in the same verified transaction."""
    priority={'geometry':0,'layout':1,'materials':2,'camera':3}
    indexed=sorted(enumerate(orders),key=lambda item:(priority.get(item[1]['stage'],9),item[0]))
    return [order for _,order in indexed if allowed_repair_paths(plan,[order])]


def proposal_order_groups(orders):
    """Reduce repeated prompt scaffolding while preserving each actionable finding."""
    groups={}
    for order in orders:
        stage=order['stage']
        # Scene placement is a coupled problem. One scene-level layout context
        # lets the proposer reason about all locations together instead of
        # receiving the complete room repeated for every pairwise finding.
        if stage=='layout' and not order.get('part_ids'):
            key=(stage,'scene')
        elif stage=='geometry':
            key=(stage,tuple(sorted(order.get('component_ids',[]))),
                 order.get('finding_code'))
        else:
            key=(stage,tuple(sorted(order.get('component_ids',[]))))
        group=groups.get(key)
        if group is None:
            group={**order,'component_ids':[],'part_ids':[],'repair_part_ids':[],'allowed_fields':[],
                   'revalidate':[],'_evidence':[],'_corrections':[],'_views':[],'finding_count':0}
            groups[key]=group
        group['finding_count']+=1
        for field in ('component_ids','part_ids','repair_part_ids','allowed_fields','revalidate'):
            group[field]=list(dict.fromkeys([*group[field],*order.get(field,[])]))
        if order.get('evidence'):
            note=str(order['evidence'])
            if note not in group['_evidence']:group['_evidence'].append(note)
        if order.get('correction'):
            note=str(order['correction'])
            if note not in group['_corrections']:group['_corrections'].append(note)
        if order.get('view'):
            group['_views'].extend(str(order['view']).split('; '))
        group['needs_decomposition_review']=group.get('needs_decomposition_review',False) or order.get('needs_decomposition_review',False)
    result=[]
    for group in groups.values():
        group['evidence']='\n'.join(f'- {item}' for item in group.pop('_evidence'))
        group['correction']='\n'.join(f'- {item}' for item in group.pop('_corrections'))
        group['view']='; '.join(dict.fromkeys(group.pop('_views')))
        result.append(group)
    return result


def _repair_contact_evidence(plan, orders):
    """Measure frozen local interfaces touching the parts in the active work order."""
    import copy
    import numpy as np
    from general_planner import to_general_plan
    from general_scene import compile_local_geometry,object_size
    from asset_physics import separating_gap

    executable,_=to_general_plan(plan,check_layout=False,check_geometry=False)
    objects={obj.id:obj for obj in executable.objects}
    components={component.id:component for component in plan.components}
    result=[]
    for order in orders:
        targets=set(order.get('part_ids',[]))
        for component_id in order.get('component_ids',[]):
            component=components[component_id]
            if component.source.mode!='parametric' or component.assembly is None:
                continue
            obj=objects[component_id];evidence={}
            compile_error=None
            try:compile_local_geometry(executable,obj,evidence=evidence)
            except (ValueError, KeyError, TypeError, IndexError) as error:
                # The failing baseline is exactly when the repair model most
                # needs measured interfaces. compile_local_geometry records
                # primitive transforms and the audit before it raises.
                compile_error=str(error)[:2000]
            # ``compile_local_geometry`` may fail before it can populate the
            # per-part records (for example when a candidate contains an
            # invalid expression or a malformed local repair).  Contact
            # evidence is diagnostic and must never turn that original
            # validation failure into a secondary KeyError; the latter used
            # to discard the fresh visual Judge report before replanning.
            records=evidence.get('parts',{})
            if not records:
                if compile_error:
                    result.append({'component_id':component_id,
                                   'baseline_validation_error':compile_error,
                                   'current_assembly_findings':[]})
                continue
            if not targets:
                envelopes=np.asarray([record['bounds'] for record in records.values()])
                dims=object_size(executable,obj)
                result.append({'component_id':component_id,
                    'measured_local_bounds_m':[envelopes[:,0].min(axis=0).tolist(),envelopes[:,1].max(axis=0).tolist()],
                    'declared_local_bounds_m':[[-dims[0]/2,-dims[1]/2,0],[dims[0]/2,dims[1]/2,dims[2]]],
                    'interpretation':'An owner-local access area follows object placement. Moving the owner cannot fix its own area overlapping its actual shape; correct the access offset and connected route points using these measured bounds.'})
                if order['stage']=='geometry':
                    result[-1]['interpretation']='Repair the local envelope offset first. A uniform asset-local translation of ROOT parts preserves their joints; mounted descendants follow their roots. Do not move the scene instance to fix its local frame.'
            for relation in component.assembly.relations:
                if not targets.intersection((relation.part,relation.target)):
                    continue
                if relation.part not in records or relation.target not in records:
                    continue
                left,right=records[relation.part],records[relation.target]
                item={'component_id':component_id,'relation':relation.model_dump(),
                      'parts':{relation.part:{k:left[k] for k in ('primitive','size','center','rotation','bounds')},
                               relation.target:{k:right[k] for k in ('primitive','size','center','rotation','bounds')}}}
                for name,record in ((relation.part,left),(relation.target,right)):
                    if record['primitive']=='sweep':
                        item['parts'][name].update(path=record['path'],radius=record['radius'],
                                                   radii=record.get('radii'),curve=record.get('curve'))
                    if record['primitive']=='lathe':
                        item['parts'][name].update(profile=record['profile'],profile_preset=record.get('profile_preset'))
                if relation.kind=='attached_to':
                    if left['primitive']=='box' and right['primitive']=='box' and np.allclose(left['rotation'],np.eye(3)) and np.allclose(right['rotation'],np.eye(3)):
                        moving=relation.part if relation.part in targets else relation.target
                        fixed=relation.target if moving==relation.part else relation.part
                        item['box_face_contact_centers_m']={name:{
                            'outside_min_face':float(records[fixed]['bounds'][0][axis]-records[moving]['size'][axis]/2),
                            'outside_max_face':float(records[fixed]['bounds'][1][axis]+records[moving]['size'][axis]/2)}
                            for axis,name in enumerate('xyz')}
                        item['face_contact_note']='These are exact center coordinates for the moving part to touch each host face without penetration. A baseline joint may be on a SIDE face, not the front. Moving onto another host face requires its matching normal coordinate as well as clearing the opening.'
                    item['measured_surface_gap_m']=round(float(separating_gap(left,right)),6)
                    measured=float(separating_gap(left,right))
                    low=-relation.max_penetration-.0005;high=.002
                    if measured<low or measured>high:
                        moving=relation.part if relation.part in targets else relation.target
                        fixed=relation.target if moving==relation.part else relation.part
                        if records[moving]['primitive'] in {'sweep','lathe','box','book'}:
                            dimensions=object_size(executable,obj)
                            origin_low=np.array([-dimensions[0]/2,-dimensions[1]/2,-.015])
                            origin_high=np.array([dimensions[0]/2,dimensions[1]/2,dimensions[2]+.015])
                            suggestions=[]
                            for axis,name in enumerate('xyz'):
                                samples=np.linspace(-.05,.05,41)
                                values=[]
                                for delta in samples:
                                    moved=copy.deepcopy(records[moving])
                                    shift=np.eye(3)[axis]*delta
                                    moved['center']=(np.asarray(moved['center'])+shift).tolist()
                                    moved['bounds']=[(np.asarray(moved['bounds'][0])+shift).tolist(),
                                                     (np.asarray(moved['bounds'][1])+shift).tolist()]
                                    if moved['primitive']=='sweep':moved['path']=(np.asarray(moved['path'])+shift).tolist()
                                    pair_left=moved if moving==relation.part else records[relation.part]
                                    pair_right=moved if moving==relation.target else records[relation.target]
                                    values.append(float(separating_gap(pair_left,pair_right)))
                                for index,(a,b) in enumerate(zip(values,values[1:])):
                                    if (a-.0005)*(b-.0005)>0:continue
                                    lo_delta=float(samples[index]);hi_delta=float(samples[index+1])
                                    lo_gap=a-.0005;hi_gap=b-.0005
                                    for _ in range(28):
                                        mid=(lo_delta+hi_delta)/2
                                        moved=copy.deepcopy(records[moving]);shift=np.eye(3)[axis]*mid
                                        moved['center']=(np.asarray(moved['center'])+shift).tolist()
                                        moved['bounds']=[(np.asarray(moved['bounds'][0])+shift).tolist(),
                                                         (np.asarray(moved['bounds'][1])+shift).tolist()]
                                        if moved['primitive']=='sweep':moved['path']=(np.asarray(moved['path'])+shift).tolist()
                                        pair_left=moved if moving==relation.part else records[relation.part]
                                        pair_right=moved if moving==relation.target else records[relation.target]
                                        mid_gap=float(separating_gap(pair_left,pair_right))-.0005
                                        if lo_gap*mid_gap<=0:hi_delta=mid;hi_gap=mid_gap
                                        else:lo_delta=mid;lo_gap=mid_gap
                                    delta=(lo_delta+hi_delta)/2
                                    moved=copy.deepcopy(records[moving]);shift=np.eye(3)[axis]*delta
                                    moved_low=np.asarray(moved['bounds'][0])+shift
                                    moved_high=np.asarray(moved['bounds'][1])+shift
                                    if np.any(moved_low<origin_low-.000001) or np.any(moved_high>origin_high+.000001):continue
                                    suggestions.append({'part_id':moving,'axis':name,'center_delta_m':round(delta,6),
                                        'predicted_gap_m':.0005,'target_part_id':fixed,
                                        'envelope_safe':True,'method':'measured capsule-contact translation'})
                            if suggestions:
                                item['contact_adjustments']=sorted(suggestions,key=lambda value:abs(value['center_delta_m']))[:3]
                elif relation.kind=='supported_by':
                    item['measured_support_gap_m']=round(float(left['bounds'][0][2]-right['bounds'][1][2]),6)
                else:
                    import numpy as np
                    al,ah=left['bounds'];bl,bh=right['bounds']
                    distance=np.linalg.norm(np.maximum(0,np.maximum(np.asarray(al)-bh,np.asarray(bl)-ah)))
                    item['measured_clearance_m']=round(float(distance),6)
                if compile_error:item['baseline_validation_error']=compile_error
                audit=evidence.get('assembly',{})
                item['current_assembly_findings']=[finding for finding in audit.get('findings',[])
                    if set(finding.get('part_ids',[])) & {relation.part,relation.target}]
                result.append(item)
    return result


def _repair_candidate_excerpt(document,plan,order,allowed_paths):
    """Send only the active object's repair parts and their frozen neighbors."""
    import copy
    if order.get('stage')=='layout' and not order.get('part_ids'):
        return {'intent':copy.deepcopy(document.get('intent',{})),
                'parameters':copy.deepcopy(document.get('parameters',{})),
                'components':[{'index':i,'pointer':f'/components/{i}',
                    **{key:copy.deepcopy(c.get(key)) for key in
                       ('id','dimensions','position','orientation_deg','parent','support','attachment','collision_role')}}
                    for i,c in enumerate(document['components'])],
                'spatial_contract':copy.deepcopy(document.get('spatial')),
                'allowed_paths':allowed_paths}
    component_id=order['component_ids'][0] if order.get('component_ids') else None
    component_index=next((i for i,c in enumerate(plan.components) if c.id==component_id),None)
    if component_index is None:return {'intent':document.get('intent',{}),'allowed_paths':allowed_paths}
    component=document['components'][component_index]
    requested=set(order.get('part_ids',[]))
    related=set(requested)
    source_component=plan.components[component_index]
    relevant_relations=[]
    if source_component.assembly:
        for relation in source_component.assembly.relations:
            if requested.intersection((relation.part,relation.target)):
                related.update((relation.part,relation.target))
                relevant_relations.append(relation.model_dump())
    indexed=[]
    for index,part in enumerate(component.get('parts',[])):
        if not requested or part.get('id') in related:
            indexed.append({'index':index,'pointer':f'/components/{component_index}/parts/{index}',
                            'part':copy.deepcopy(part)})
    component_excerpt={key:copy.deepcopy(component.get(key)) for key in
                       ('id','semantic','dimensions','rigidity','required_features','material_requirements',
                        'interface_notes','source')}
    if component.get('assembly') is not None:
        component_excerpt['assembly']={'roots':list(source_component.assembly.roots) if source_component.assembly else [],
                                       'relations':relevant_relations}
    return {'intent':copy.deepcopy(document.get('intent',{})),
            'parameters':copy.deepcopy(document.get('parameters',{})),
            'component_index':component_index,
            'component':component_excerpt,
            'repair_parts':indexed,'allowed_paths':allowed_paths,
            'scope_note':'This is a measured excerpt of the unchanged full candidate. Pointers address the original full plan; only listed allowed_paths may be returned.'}


def repair_candidate(plan, geometry, visual, model_profile, workspace, *, complete=None, max_attempts=None):
    """Propose and verify a reversible candidate. Acceptance requires a NEW render/Judge."""
    import asyncio
    import sys
    from general_planner import PlannerScenePlan,to_general_plan
    from general_scene import edit_general
    from staged_planner import StagedScenePlanner,LocalRepair,apply_local_repair,digest
    workspace=Path(workspace);workspace.mkdir(parents=True,exist_ok=True)
    all_orders=repair_work_orders(plan,geometry,visual)
    active_orders=_active_repair_orders(plan,all_orders['orders'])
    proposal_orders=proposal_order_groups(active_orders)
    paths=allowed_repair_paths(plan,active_orders)
    receipt={'status':'unresolved','accepted':False,'before_hash':all_orders['plan_sha256'],
             'allowed_paths':paths,'orders':active_orders,
             'proposal_order_groups':len(proposal_orders),
             'deferred_orders':[order for order in all_orders['orders'] if order not in active_orders],
             'attempts':[]}
    def save():
        (workspace/'repair_execution.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf-8')
    if not paths:
        receipt['reason']='Required repair is outside supported local mutations.'
        save();return None,receipt
    # Contact measurements are useful for edits to modeled parts, but they add
    # noise to scene-level layout/material/camera proposals. Keep that evidence
    # scoped to local geometry repairs only.
    geometry_orders=[order for order in proposal_orders if order.get('stage')=='geometry']
    contact_evidence=_repair_contact_evidence(plan,geometry_orders) if geometry_orders else []
    runtime=StagedScenePlanner(model_profile,workspace,workspace.name,complete=complete)
    if complete is None:
        sys.path.insert(0,str(fenv.ADSL_ROOT/'adsl-agents'))
        from utils.runner import AgentRuntime
        runtime.runtime=AgentRuntime(model_profile=Path(model_profile),workspace=workspace,task_id=workspace.name)
        runtime.runtime.model_settings.max_tokens=max(runtime.runtime.profile.max_tokens or 8000,32768)
        if runtime.runtime.profile.model=='deepseek-flash':
            runtime.runtime.model_settings.extra_body={'thinking':{'type':'enabled'},'reasoning_effort':'low'}
    document=plan.model_dump(mode='json',by_alias=True)
    from material_catalog import capabilities
    async def execute():
        error=None;rejected_proposal=None;rejected_measurements=[]
        failed_states=set();attempt=0
        while True:
            candidate=None
            try:
                excerpts=[]
                for order in proposal_orders:
                    scope_paths=allowed_repair_paths(plan,[order])
                    if order.get('stage')=='layout' and not order.get('part_ids'):
                        excerpts.append(_repair_candidate_excerpt(document,plan,order,scope_paths))
                    else:
                        for component_id in order.get('component_ids') or [None]:
                            excerpts.append(_repair_candidate_excerpt(document,plan,
                                {**order,'component_ids':[component_id] if component_id else []},scope_paths))
                excerpt=excerpts[0] if len(excerpts)==1 else {'work_order_scopes':excerpts}
                raw=await runtime.ask(f'visual_repair_{attempt}',
                    ('Repair ALL active work orders coherently in ONE patch using exact allowed JSON pointers. '
                    + ('The visual Judge is temporarily unavailable; use the measured Blender geometry findings '
                       'and spatial evidence as the authoritative repair input, and preserve the unresolved visual '
                       'status for the next evaluation.' if visual.get('status')=='unavailable' else '') + ' '
                    'Act as a scene designer: preserve the whole-space concept and group relationships. '
                    'Make the smallest coordinated change that resolves the measured findings. For a localized '
                    'orientation or placement failure, edit only the named component poses; do not regenerate '
                    'the room inventory or reposition unrelated groups. Use a 180-degree yaw change only when '
                    'the evidence shows that the component is facing backward. Escalate to a new composition '
                    'only when the measured findings cannot be resolved through the authorized component fields. '
                    'Resolve major massing, orientation and negative-space problems before minor details; '
                    'do not default to symmetry, scattering or adding props. The next render rechecks the whole. '
                    'Inspect every finding before selecting changes; do not fix only the first issue. '
                    'A local geometry/frame correction can also resolve dependent layout findings. '
                    'Fix measured local frame drift FIRST and preserve the already validated scene pose, '
                    'access offsets and route points whenever the corrected geometry removes those blockers. '
                    'Do not change an extra field merely to touch each order. An access center is OWNER-LOCAL, '
                    'never a world coordinate; apply owner yaw exactly once when reasoning about its world rectangle. '
                    'Preserve all IDs, semantic requirements, physical assembly relations, rigidity, sources and dimensions. '
                    'Do not weaken clearance or delete support checks. Return LocalRepair JSON. '
                    'Coordinate convention: +Z up, -Y functional front, +Y back, +X right. '
                    'The book primitive spine faces -Y at zero rotation. Distinguish reversed structural '
                    'front/back from a camera issue, using actual geometry and visual evidence together. '
                    'The only parts eligible for geometry changes are those named by the active work orders. '
                    'Sweep path entries are OFFSETS from center, bounded by size/2 minus radius; sweeps have a circular constant-radius section. '
                    'Do not describe or imitate a ribbon/profile sweep that this geometry system cannot build. '
                    'For a swept part with frozen attached_to relations, use the measured contact evidence to seat its actual capsule path '
                    'on each neighbor within max_penetration; a nearby envelope or a thicker radius is not a substitute for contact. '
                    'If previous_attempt_contact_measurements includes a measured capsule-contact translation, it is a numerically evaluated '
                    'center adjustment that reaches the legal contact band while staying inside the asset envelope. Apply that delta only to '
                    'the named part center axis, preserving any existing parameter expression by appending the delta to the expression. '
                    'Do not move or reshape a connected neighbor unless it is covered by the active work orders and allowed_paths. '
                    'For parts with parent and mount=top, center is a PARENT-SURFACE OFFSET: z=0 means contact, '
                    'not the absolute world-space center height. Preserve this convention when moving contents. '
                    'The base candidate is unchanged after a rejected proposal. previous_error describes the '
                    'rejected_proposal, NOT a new defect in the unchanged base candidate. Propose a complete replacement patch against candidate. '
                    'For camera evidence, intent.review_azimuth_deg rotates all review camera directions around scene Z '
                    'without moving any geometry. Use it only to improve evidence; never hide a defect or claim a geometry repair. '
                    'Explicit experience_views use their own position/target/lens_mm; change those allowed fields '
                    'when that particular human view is insufficient. Keep the same view intent and visible whole scene. '
                    'candidate_scope contains excerpts for ALL active orders, with repair parts and measured neighbors. '
                    'Use their original full-plan pointers and return only allowed_paths. If a circular constant-radius sweep '
                    'cannot create the sculpted/variable-section visual mass described by the finding, do not pretend a path tweak solves it. '
                    'Make only a valid local correction; the orchestration loop will escalate a repeated rendered style failure to decomposition/source planning. '
                    'A candidate must pass internal assembly, local geometry and every requested edit-effect check. '
                    'Scene-level access, clearances, routes, support and visual usability are remeasured by the '
                    'Blender geometry and visual Judges after this patch; do not treat declared envelopes as final evidence.'),
                    {'schema':LocalRepair.model_json_schema(),'candidate_scope':excerpt,
                     'active_work_order':proposal_orders[0],
                     'active_work_orders':proposal_orders,
                     'deferred_work_order_count':len(all_orders['orders'])-len(active_orders),
                     'measured_frozen_contacts':contact_evidence,
                     'previous_attempt_contact_measurements':rejected_measurements,
                     'allowed_paths':paths,'previous_error':error,'rejected_proposal':rejected_proposal,
                     'base_geometry_status':geometry.get('status'),
                     'spatial_failure_evidence':geometry.get('spatial',{}),
                     'frozen_spatial_contract':plan.spatial.model_dump(mode='json') if plan.spatial else None,
                     'scene_design':plan.design_program.model_dump(mode='json') if plan.design_program else None,
                     'design_bindings':plan.design_bindings,
                     'measured_spatial_evidence':geometry.get('spatial'),
                     'surface_schema':__import__('part_details').SurfaceSpec.model_json_schema(),
                     'available_materials':capabilities(),
                     'material_instructions':'surface.texture_id selects a verified PBR resource. Set grain_axis and texture_scale, roughness. finish selects matte, wood, metal. Actual render must verify improvement; colors alone cannot supply missing texture.'})
                patch=LocalRepair.model_validate(raw)
                rejected_proposal=patch.model_dump(mode='json')
                changed=apply_local_repair(document,patch,paths)
                candidate=PlannerScenePlan.model_validate(changed)
                executable,_=to_general_plan(candidate,check_layout=False)
                from general_scene import audit_parametric_scene
                measured=audit_parametric_scene(executable,check_spatial=False)
                if measured['status']!='passed':raise ValueError('Measured local aDSL geometry: '+json.dumps(measured,ensure_ascii=False))
                for check in candidate.edit_checks:
                    edited=edit_general(executable,check.parameter,check.delta,check_layout=False)
                    measured=audit_parametric_scene(edited,check_spatial=False)
                    if measured['status']!='passed':raise ValueError('Edited local aDSL geometry '+check.parameter+': '+json.dumps(measured,ensure_ascii=False))
                receipt.update(status='candidate_validated_pending_render',after_hash=digest(changed),
                               changed_paths=[r.path for r in patch.replacements])
                receipt['attempts'].append({'attempt':attempt,'status':'validated'})
                (workspace/'candidate_plan.json').write_text(json.dumps(changed,ensure_ascii=False,indent=2),encoding='utf-8')
                save();return candidate
            except Exception as failure:
                error=f'{type(failure).__name__}: {failure}'
                if candidate is not None:
                    try:
                        rejected_measurements=_repair_contact_evidence(candidate,active_orders)
                    except Exception as measurement_failure:
                        # Diagnostics must not mask the rejected proposal's
                        # validation error or prevent the next repair.
                        rejected_measurements=[{'status':'unavailable',
                            'error':f'{type(measurement_failure).__name__}: {measurement_failure}'[:1000]}]
                else:
                    rejected_measurements=[]
                receipt['attempts'].append({'attempt':attempt,'status':'rejected','error':error,
                                            'contact_measurements':rejected_measurements})
                save()
                failed_state=digest({'base_hash':receipt['before_hash'],'proposal':rejected_proposal,
                    'error':error,'contact_measurements':rejected_measurements})
                if failed_state in failed_states:
                    receipt['stop_reason']='repeated_failed_state'
                    save()
                    break
                failed_states.add(failed_state)
            attempt+=1
        return None
    candidate=asyncio.run(execute())
    return candidate,receipt
