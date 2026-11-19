"""Build/evidence/repair loop; immutable iterations, fail-closed acceptance."""
import json
import hashlib
from pathlib import Path

import finescene_env as fenv
import re
import subprocess

import numpy as np

from general_planner import to_general_plan
from general_scene import compile_general,judge_general_geometry,edit_general,audit_exported_support_contacts
from general_repairs import repair_candidate,repair_work_orders,allowed_repair_paths
from general_vision import review_scene_images
from asset_physics import audit_exported_parts


def save(path,data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2,
        default=lambda x:x.tolist() if hasattr(x,'tolist') else str(x)),encoding='utf-8')


def _requires_coupled_spatial_recovery(plan,geometry,visual):
    """Escalate only findings that cannot be addressed through scoped local fields."""
    spatial_findings=(geometry.get('spatial') or {}).get('findings',[])
    decision=visual.get('decision',visual) if isinstance(visual,dict) else {}
    layout_findings=[finding for finding in decision.get('findings',[])
        if finding.get('severity')=='blocking' and finding.get('repair_target','decomposition')=='layout']
    findings=[*spatial_findings,*layout_findings]
    if not findings:return False
    known={component.id for component in plan.components}
    for finding in findings:
        component_ids=list(dict.fromkeys(
            component_id.split('/')[0]
            for component_id in finding.get('component_ids',[])
            if isinstance(component_id,str)))
        if not component_ids or not set(component_ids)<=known:
            return True
        order={'stage':finding.get('repair_target','layout'),
               'component_ids':component_ids,
               'part_ids':list(finding.get('part_ids',[])),
               'finding_code':finding.get('code')}
        if not allowed_repair_paths(plan,[order]):
            return True
    return False


def visual_request(plan,brief,*,actual_geometry=None,actual_support=None,actual_spatial=None,prepared_assets=None):
    return {'brief':brief,'quality_target':plan.intent.quality_target,
            'scene_design':plan.design_program.model_dump(mode='json') if plan.design_program else None,
            'design_bindings':plan.design_bindings,
            'style_requirements':[r.model_dump() for r in plan.style_requirements],
            'style_bindings':[b.model_dump() for b in plan.style_bindings],
            'spatial_contract':plan.spatial.model_dump(mode='json') if plan.spatial else None,
            'review_camera_azimuth_deg':plan.intent.review_azimuth_deg,
            'source_material_measurements':{id:{
                'base_color_texture_verified':record.get('base_color_texture_verified'),
                'texture_evidence':record.get('texture_evidence'),
                'source_visual_status':record.get('visual_status'),
                'interpretation':'Preparation measured UVs and loaded linked base-color images. This establishes texture presence, not appearance quality, readability at this distance, or final scene acceptance.'}
                for id,record in (prepared_assets or {}).items()},
            'measured_geometry_evidence':{
                'object_world_bounds_m':actual_geometry or {},
                'support_contacts':(actual_support or {}).get('components',{}),
                'scene_spatial_audit':actual_spatial,
                'interpretation':'Blender measurements are authoritative for geometric contact and bounds. Images remain authoritative for visible appearance, semantic identity, style, materials, and whether the contact is legible.'},
            'components':[
        {'id':c.id,'semantic':c.semantic,'required_features':c.required_features,'materials':c.material_requirements,
         'parent':c.parent,'attachment':c.attachment,'support_part':c.support_part,
         'surface_contact':c.surface_contact.model_dump() if c.surface_contact else None,
         'interface_notes':c.interface_notes,
         'parts':[{'id':p.id,'semantic':p.semantic,'rigidity':p.rigidity,
                   'form':p.form.model_dump() if p.form else None} for p in c.parts],
         'assembly':c.assembly.model_dump() if c.assembly else None} for c in plan.components]}


def review_layout_then_details(images,request,profile,output,*,quality,reviewer=review_scene_images,
                              detail_renderer=None):
    """Resolve whole-space problems before paying to rejudge every close-up."""
    overview=[p for p in images if Path(p).stem in {'overview','reverse','front','top','exterior'}
        or Path(p).stem.startswith(('experience_','entrance_','interior_'))]
    if overview and (len(overview)<len(images) or detail_renderer is not None):
        layout_request={**request,'review_phase':'whole_space_before_details',
            'phase_instruction':'Review the whole rebuilt space and report ALL independent visible layout, '
                'activity, facing, scale, obstructed-view and composition problems together. Evaluate the room '
                'from representative experience views. Local material/detail quality has a separate later '
                'review; do not reject hidden joinery or incidental wear from these distant views.'}
        preliminary=reviewer(overview,layout_request,profile,Path(output).with_name('layout_visual_qa.json'),quality=quality)
        if preliminary.get('accepted') is not True:
            result={**preliminary,'accepted':False,'review_phase':'layout_requires_revision',
                'deferred_detail_images':[p for p in images if p not in overview],
                'deferred_detail_render':detail_renderer is not None,
                'note':'Whole-space review did not pass. Close-up review is deferred; this is not final approval.'}
            save(output,result)
            return result
    if detail_renderer is not None:
        manifest=detail_renderer()
        images=manifest['images'];request={**request,'render_evidence':manifest}
    return reviewer(images,request,profile,output,quality=quality)


def _evaluate_edit_geometry(plan,assets,base_actual,check,index,directory,blender,source):
    """Export an edited variant and remeasure contacts without spending on renders or visual QA."""
    parameter=re.sub(r'[^A-Za-z0-9_-]+','_',str(check['parameter'])).strip('_') or 'parameter'
    delta=f"{float(check['delta']):+.3f}".replace('+','p').replace('-','m').replace('.','p')
    target=Path(directory)/f'{index:02d}_{parameter}_{delta}'
    target.mkdir(parents=True,exist_ok=True)
    changed=edit_general(plan,check['parameter'],check['delta'],check_layout=False)
    save(target/'general_plan.json',changed.model_dump(mode='json'))
    scene,bounds,receipts=compile_general(changed,assets,inspection=any(a.get('inspection_only') for a in assets.values()))
    geometry=judge_general_geometry(changed,bounds,receipts)
    save(target/'build_receipts.json',receipts)
    save(target/'geometry_judge.json',geometry)
    from adsl.core.serialize import asset_to_dict
    adsl_path=target/'adsl_scene.json';save(adsl_path,asset_to_dict(scene))
    export=target/'blender_export';measurement=target/'geometry_measurement'
    commands=[('blender_export',[blender,'-b','--factory-startup','--python-exit-code','1',
        '--python',str(source/'blender_worker_fast.py'),'--','--input',str(adsl_path),
        '--output',str(export)]),
        ('geometry_measurement',[blender,'-b','--factory-startup','--python-exit-code','1',
        '--python',str(source/'general_render.py'),'--','--scene',str(export/'scene.blend'),
        '--plan',str(target/'general_plan.json'),'--output',str(measurement),'--geometry-only'])]
    for name,command in commands:
        with (target/(name+'.log')).open('w',encoding='utf-8') as log:
            subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=900)
    actual=json.loads((measurement/'actual_bounds.json').read_text(encoding='utf-8'))
    measured_path=measurement/'actual_part_bounds.json'
    measured=json.loads(measured_path.read_text(encoding='utf-8')) if measured_path.is_file() else {}
    contacts_path=measurement/'actual_support_contacts.json'
    contacts=json.loads(contacts_path.read_text(encoding='utf-8')) if contacts_path.is_file() else {}
    internal=audit_exported_parts(changed,receipts,measured)
    geometry=judge_general_geometry(changed,actual,receipts,measured)
    geometry['findings'].extend(internal['findings'])
    support=audit_exported_support_contacts(changed,actual,contacts,receipts=receipts,measured_parts=measured)
    geometry['findings'].extend(support['findings'])

    rigid_sizes=[]
    for obj in changed.objects:
        if obj.rigidity!='rigid' and obj.source.mode=='parametric':
            continue
        before=base_actual.get(obj.id);after=actual.get(obj.id)
        if before is None or after is None:
            geometry['findings'].append({'component_ids':[obj.id],
                'evidence':'Missing baseline or edited Blender bounds while checking rigid size preservation.'})
            continue
        before_size=np.asarray(before[1],dtype=float)-np.asarray(before[0],dtype=float)
        after_size=np.asarray(after[1],dtype=float)-np.asarray(after[0],dtype=float)
        difference=np.abs(after_size-before_size)
        passed=bool(np.all(difference<=.003))
        rigid_sizes.append({'component_id':obj.id,'baseline_size_m':before_size.tolist(),
                            'edited_size_m':after_size.tolist(),'absolute_difference_m':difference.tolist(),
                            'passed':passed})
        if not passed:
            geometry['findings'].append({'component_ids':[obj.id],
                'repair_target':'decomposition',
                'evidence':f'Rigid component changed measured size after edit {check["parameter"]}: '
                           f'{before_size.tolist()} -> {after_size.tolist()}.'})
    if geometry['findings']:
        geometry['status']='needs_revision'
    save(target/'actual_internal_judge.json',internal)
    save(target/'actual_support_judge.json',support)
    save(target/'actual_geometry_judge.json',geometry)
    save(target/'actual_spatial_judge.json',geometry.get('spatial',{}))
    report={'scenario_id':target.name,'parameter':check['parameter'],'delta':check['delta'],
            'status':geometry['status'],'findings':geometry['findings'],
            'actual_geometry':actual,'actual_support':support,'rigid_size_preservation':rigid_sizes,
            'geometry_directory':str(measurement)}
    save(target/'edit_scenario_report.json',report)
    return report


def evaluate_candidate(plan,assets,directory,brief,vision_profile,*,review=True):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    executable,_=to_general_plan(plan,check_layout=False,check_geometry=False)
    for check in plan.edit_checks:edit_general(executable,check.parameter,check.delta,check_layout=False)
    save(directory/'planner_output.json',plan.model_dump(mode='json',by_alias=True))
    save(directory/'general_plan.json',executable.model_dump(mode='json'))
    source_qa={'status':'needs_revision' if any(a.get('accepted') is not True for a in assets.values()) else 'passed',
        'unaccepted_components':[id for id,a in assets.items() if a.get('accepted') is not True]}
    save(directory/'source_qa_status.json',source_qa)
    scene,bounds,receipts=compile_general(executable,assets,inspection=source_qa['status']!='passed')
    geometry=judge_general_geometry(executable,bounds,receipts)
    save(directory/'geometry_judge.json',geometry);save(directory/'build_receipts.json',receipts)
    save(directory/'spatial_judge.json',geometry.get('spatial',{}))
    from adsl.core.serialize import asset_to_dict
    save(directory/'adsl_scene.json',asset_to_dict(scene))
    # A successfully constructed candidate must remain inspectable even when
    # geometry QA rejects it. Render that real candidate so semantic/visual
    # findings join the same repair batch; acceptance still requires both gates.
    blender=fenv.BLENDER;source=Path(__file__).parent
    export=directory/'blender_export';visual_dir=directory/'visual_review'
    commands=[('blender_export',[blender,'-b','--factory-startup','--python-exit-code','1',
        '--python',str(source/'blender_worker_fast.py'),'--','--input',str(directory/'adsl_scene.json'),
        '--output',str(export)]),
        ('general_render',[blender,'-b','--factory-startup','--python-exit-code','1',
        '--python',str(source/'general_render.py'),'--','--scene',str(export/'scene.blend'),
        '--plan',str(directory/'general_plan.json'),'--style-plan',str(directory/'planner_output.json'),
        '--output',str(visual_dir),'--view-phase','layout' if review else 'all'])]
    for name,command in commands:
        with (directory/(name+'.log')).open('w',encoding='utf-8') as log:
            subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=900)
    actual=json.loads((visual_dir/'actual_bounds.json').read_text(encoding='utf-8'))
    measured=json.loads((visual_dir/'actual_part_bounds.json').read_text(encoding='utf-8'))
    contacts_path=visual_dir/'actual_support_contacts.json'
    contacts=json.loads(contacts_path.read_text(encoding='utf-8')) if contacts_path.is_file() else {}
    internal=audit_exported_parts(executable,receipts,measured)
    geometry=judge_general_geometry(executable,actual,receipts,measured)
    geometry['findings'].extend(internal['findings'])
    support=audit_exported_support_contacts(executable,actual,contacts,receipts=receipts,measured_parts=measured)
    geometry['findings'].extend(support['findings'])
    edit_scenarios=[]
    for index,check in enumerate(plan.edit_checks):
        try:
            report=_evaluate_edit_geometry(executable,assets,actual,check.model_dump(mode='json'),index,
                                           directory/'edit_scenarios',blender,source)
        except Exception as error:
            report={'scenario_id':f'{index:02d}_{check.parameter}','parameter':check.parameter,
                    'delta':check.delta,'status':'unavailable','error_type':type(error).__name__,
                    'error':str(error)[:1200],'findings':[]}
        edit_scenarios.append(report)
        if report['status']=='needs_revision':
            geometry['findings'].extend({**finding,'edit_scenario':report['scenario_id']}
                                        for finding in report.get('findings',[]))
    save(directory/'edit_scenarios.json',{'status':('unavailable' if any(r['status']=='unavailable' for r in edit_scenarios)
          else 'needs_revision' if any(r['status']!='passed' for r in edit_scenarios) else 'passed'),
          'scenarios':edit_scenarios})
    if any(r['status']=='unavailable' for r in edit_scenarios):
        geometry['status']='unavailable'
    elif geometry['findings'] or any(r['status']!='passed' for r in edit_scenarios):
        geometry['status']='needs_revision'
    save(directory/'actual_internal_judge.json',internal);save(directory/'actual_support_judge.json',support)
    save(directory/'actual_geometry_judge.json',geometry)
    save(directory/'actual_spatial_judge.json',geometry.get('spatial',{}))
    manifest=json.loads((visual_dir/'render_manifest.json').read_text(encoding='utf-8'))
    images=manifest['images']
    request=visual_request(plan,brief,actual_geometry=actual,actual_support=support,actual_spatial=geometry.get('spatial'),
                           prepared_assets=assets)
    request['render_evidence']=manifest
    def render_details():
        command=[blender,'-b','--factory-startup','--python-exit-code','1',
            '--python',str(source/'general_render.py'),'--','--scene',str(export/'scene.blend'),
            '--plan',str(directory/'general_plan.json'),'--style-plan',str(directory/'planner_output.json'),
            '--output',str(visual_dir),'--view-phase','details']
        with (directory/'general_render_details.log').open('w',encoding='utf-8') as log:
            subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=900)
        return json.loads((visual_dir/'render_manifest.json').read_text(encoding='utf-8'))
    visual=(review_layout_then_details(images,request,
                vision_profile,directory/'final_visual_qa.json',quality=plan.intent.quality_target,
                detail_renderer=render_details)
            if review else {'status':'not_run','accepted':False,'reason':'Geometry/export experiment only; visual acceptance not requested in this experiment.'})
    images=json.loads((visual_dir/'render_manifest.json').read_text(encoding='utf-8'))['images']
    if not review:save(directory/'final_visual_qa.json',visual)
    return {'accepted':geometry['status']=='passed' and visual['accepted'] and source_qa['status']=='passed',
            'geometry':geometry,'visual':visual,'source_qa':source_qa,'directory':str(directory),'images':images,
            'edit_scenarios':edit_scenarios}


def execute_with_repairs(plan,assets,directory,brief,model_profile,vision_profile,*,max_repairs=None,
                         max_render_rounds=None,evaluator=evaluate_candidate,repairer=repair_candidate,
                         coupled_layout_recovery=False,prior_visual_rejections=None,handoff_dir=None):
    """Rebuild candidates only after validated local changes; never re-resolve assets.

    Every round in which the visual Judge blocks a component counts as one
    failure of that component (carried over from earlier replans through
    prior_visual_rejections). At HANDOFF_ATTEMPTS the loop stops and writes a
    harness handoff request instead of asking the agent again."""
    from handoff_bridge import HANDOFF_ATTEMPTS, FINESCENE_REPAIR_ROUTE, count_visual_rejections, write_handoff
    visual_rejections=dict(prior_visual_rejections or {})
    directory=Path(directory);iterations=[];current=plan
    result=None;previous_visual=None;seen_failures=set();index=0
    # Bound the outer render/repair loop even when every round yields a fresh,
    # distinct failure. max_repairs belongs to the planner's in-stage budget and
    # deliberately does not throttle visual repair, so the render loop carries
    # its own generous cap instead.
    limit=12 if max_render_rounds is None else max(0,int(max_render_rounds))
    while True:
        target=directory if index==0 else directory/f'repair_{index:02d}'
        try:result=evaluator(current,assets,target,brief,vision_profile)
        except Exception as error:
            result={'accepted':False,'geometry':{'status':'unavailable','findings':[]},
                    'visual':{'accepted':False,'status':'unavailable','error_type':type(error).__name__,
                              'error':str(error)[:2000]},'directory':str(target)}
        # Acceptance requires fresh geometry AND vision, not an evaluator's flag alone.
        accepted=(result['geometry'].get('status')=='passed' and result['visual'].get('status')=='passed'
                  and result['visual'].get('accepted') is True
                  and result.get('source_qa',{}).get('status','passed')=='passed')
        result['accepted']=accepted
        save(target/'evaluation.json',result)
        iterations.append({'index':index,'directory':str(target),'accepted':accepted})
        save(directory/'repair_iterations.json',iterations)
        if accepted:break
        if result['visual'].get('status') not in (None,'unavailable','passed'):
            count_visual_rejections(result['visual'],visual_rejections)
            exhausted=sorted(cid for cid,n in visual_rejections.items() if n>=HANDOFF_ATTEMPTS)
            if exhausted:
                decision=result['visual'].get('decision',result['visual'])
                for cid in exhausted:
                    path=write_handoff(handoff_dir or directory,pipeline='finescene',target=cid,stage='visual_judge',
                        attempts=visual_rejections[cid],
                        reason=f'{cid}: blocked by the visual Judge in {visual_rejections[cid]} render rounds',
                        evidence={'evaluation':target/'evaluation.json','iterations':directory/'repair_iterations.json'},
                        last_findings=[f for f in decision.get('findings',[]) if cid in f.get('component_ids',[])],
                        repair_route=FINESCENE_REPAIR_ROUTE)
                result['harness_handoff']=str(path)
                result['replan_reason']='visual_rejection_handoff:'+','.join(exhausted)
                break
        if result['geometry'].get('status')=='unavailable':break
        orders=repair_work_orders(current,result['geometry'],result['visual'])
        save(target/'repair_work_orders.json',orders)
        if coupled_layout_recovery and _requires_coupled_spatial_recovery(
                current,result['geometry'],result['visual']):
            result['replan_reason']='coupled_spatial_replan_from_reconstructed_evidence'
            break
        from style_contract import replan_reason
        reason=replan_reason(current,result['visual'],previous_visual)
        local_paths=allowed_repair_paths(current,orders['orders'])
        # A decomposition-level finding must not skip an independent, validated
        # local repair that can improve existing geometry in the same Judge
        # report. Escalate only when the current plan exposes no local controls.
        repeated_style_failure=bool(reason and reason.startswith('repeated_style_requirement_failure_after_local_render:'))
        if reason and (repeated_style_failure or not local_paths):
            result['replan_reason']=reason
            break
        visual_decision=result['visual'].get('decision',result['visual'])
        state={'plan':current.model_dump(mode='json'),
               'geometry':{'status':result['geometry'].get('status'),
                           'findings':result['geometry'].get('findings',[])},
               'visual':{'status':result['visual'].get('status'),
                         'accepted':result['visual'].get('accepted'),
                         'findings':visual_decision.get('findings',[]),
                         'style_assessments':visual_decision.get('style_assessments',[])}}
        signature=hashlib.sha256(json.dumps(state,sort_keys=True,ensure_ascii=False,
            default=lambda value:value.tolist() if hasattr(value,'tolist') else str(value)).encode()).hexdigest()
        if signature in seen_failures:
            result['replan_reason']='repeated_geometry_visual_failure_after_local_render'
            break
        seen_failures.add(signature)
        # A vision outage must not discard actionable Blender geometry
        # findings. Continue with a layout/geometry repair when the measured
        # report exposes authorized local paths; stop only when vision is the
        # sole unresolved gate.
        if result['visual'].get('status')=='unavailable' and not result['geometry'].get('findings'):
            break
        candidate,execution=repairer(current,result['geometry'],result['visual'],model_profile,target/'repair_runtime')
        if candidate is None:
            result['replan_reason']=reason or 'local_repair_cannot_produce_valid_candidate'
            break
        if index>=limit:
            result['replan_reason']=f'repair_budget_exhausted:{limit}'
            break
        # Also protect against a defective/custom repairer bypassing verification.
        executable,_=to_general_plan(candidate,check_layout=False)
        for check in candidate.edit_checks:edit_general(executable,check.parameter,check.delta,check_layout=False)
        previous_visual=result['visual']
        current=candidate
        index+=1
    result['iterations']=iterations
    result['visual_rejections']=visual_rejections
    result['final_plan']=str(Path(result['directory'])/'planner_output.json')
    save(directory/'final_result.json',result)
    return result
