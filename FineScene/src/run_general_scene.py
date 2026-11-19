"""CLI for the category-independent FineScene pipeline.

This entry point keeps the research claim honest: planning is separated from
asset acquisition, and unresolved or visually rejected components stop the
assembly.  ``--plan-only`` is useful for inspecting the detailed routing
decision without downloading models or calling a visual model.
"""
from __future__ import annotations

import argparse
import json
import hashlib
import shutil
import copy
import sys
from pathlib import Path

from general_planner import GeneralLLMPlanner, PlannerScenePlan, to_general_plan
from staged_planner import StagedScenePlanner
from planning_diagnostics import diagnostic_families


def write_harness_handoff(directory: Path, report: dict) -> Path:
    """Hand a stage that exhausted its repair budget to the harness model."""
    import re
    error=str(report.get('error',''))
    match=re.match(r'([^:]+): exhausted (\d+) repair calls',error)
    stage,attempts=(match.group(1),int(match.group(2))) if match else ('planning',5)
    kwargs=dict(pipeline='finescene',target=stage,stage=stage,attempts=attempts,
        reason=f'{stage}: {attempts} rejected repair calls (PlanningRepairLimit)',
        evidence={'attempt_report':directory/'attempt_report.json','agent_runtime':directory/'agent_runtime',
                  'planning_failures':directory/'agent_runtime'},
        last_findings=report.get('planning_failures') or report.get('error'),
        repair_route=('hand-write the rejected part of the plan (complete planner_output.json from the latest '
                      'candidate in agent_runtime), then run run_general_scene.py --planner replay --plan <file> '
                      '--harness-repair <notes.json> so run_origin.json records it as harness-authored'))
    from handoff_bridge import write_handoff
    return write_handoff(directory,**kwargs)


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    def serialize(value):
        if hasattr(value,'tolist'):return value.tolist()
        if isinstance(value,Path):return str(value)
        raise TypeError(f'Cannot serialize {type(value).__name__}')
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2,
                               default=serialize),
                    encoding="utf-8")


def compact_planning_failures(report):
    """Retain each distinct physical issue and its edit scenarios once."""
    measurements=report.get('measurements',{})
    issues={}
    for issue in measurements.get('issues',[]):
        identity={key:issue.get(key) for key in ('code','component_ids','evidence','constraint_id')}
        measured=issue.get('measured',{})
        if not isinstance(measured,dict):measured={}
        identity['repair_target']=measured.get('repair_target')
        identity['part_ids']=measured.get('part_ids')
        key=json.dumps(identity,sort_keys=True,ensure_ascii=False,default=str)
        entry=issues.setdefault(key,{**identity,'scenarios':[],'paths':[],'observations':[]})
        scenario=issue.get('scenario','baseline')
        if scenario not in entry['scenarios']:entry['scenarios'].append(scenario)
        for path in issue.get('paths',[]):
            if path not in entry['paths']:entry['paths'].append(path)
        observation={k:v for k,v in issue.items() if k in {'measured','expected','upstream_paths'}}
        if observation and observation not in entry['observations']:entry['observations'].append(observation)
    return {'stage':measurements.get('stage'),'status':measurements.get('status'),
        'coverage':measurements.get('coverage'),
        'candidate':report.get('candidate'),
        'issues':list(issues.values()),
        'blocked_checks':measurements.get('blocked_checks',[]),
        'duplicate_scenario_diagnostics_merged':True}


def planning_feedback(directory):
    """Escalation needs actual constraints, not just a path to a failed stage."""
    runtime=Path(directory)/'agent_runtime';feedback={}
    events=runtime/'stage_events.json'
    if events.is_file():
        history=json.loads(events.read_text(encoding='utf-8'))
        feedback['planning_failures']=[{k:e.get(k) for k in ('stage','status','attempt','issue_count',
            'blocked_check_count','issue_ids','reason','reuse_basis') if k in e}
            |({'error':e['error']} if e.get('error') and len(e['error'])<=1600 else
              {'error':'See detailed_planning_failure for scenario-grouped evidence.'} if e.get('error') else {})
            for e in history if e.get('status') in {'failed','stopped','checkpoint_needs_repair'}][-6:]
    else:history=[]
    failed_stages=[e['stage'] for e in history if e.get('status') in {'failed','checkpoint_needs_repair'}]
    planning_issues=[]
    if failed_stages:
        files=list(runtime.glob(failed_stages[-1]+'_failure_*.json'))
        if files:
            saved=json.loads(max(files,key=lambda p:p.stat().st_mtime).read_text(encoding='utf-8'))
            feedback['detailed_planning_failure']=compact_planning_failures(saved)
            planning_issues=saved.get('measurements',{}).get('issues',[])
    invalidated={id for issue in planning_issues
        if isinstance(issue.get('measured'),dict) and
           issue['measured'].get('repair_target') in {'geometry','materials','decomposition','source'}
        for id in issue.get('component_ids',[])}
    candidates=[runtime/'current_mounting_outline.json',runtime/'frozen_decomposition.json',runtime/'decomposition_checkpoint.json']
    candidates+=sorted(runtime.glob('decomposition_failure_*.json'),key=lambda p:int(p.stem.rsplit('_',1)[1]),reverse=True)
    for path in candidates:
        if not path.is_file():continue
        record=json.loads(path.read_text(encoding='utf-8'))
        candidate=record if 'objects' in record else record.get('plan',record.get('candidate'))
        if not isinstance(candidate,dict):continue
        feedback['previous_decomposition']={
            'objects':[{k:o.get(k) for k in ('id','semantic','parent','acquisition','required_features')}
                       for o in candidate.get('objects',[]) if isinstance(o,dict)],
            'style_bindings':candidate.get('style_bindings',[])}
        design_path=runtime/'scene_design.json'
        active_design_choices=set()
        if design_path.is_file():
            feedback['current_scene_design']=json.loads(design_path.read_text(encoding='utf-8'))
            active_design_choices={item.get('id') for item in feedback['current_scene_design'].get('choices',[])}
        bindings=candidate.get('design_bindings',{})
        explicitly_bound={component for choice,components in bindings.items()
                          if choice in active_design_choices for component in components}
        layout_validated=any(e.get('stage')=='layout' and e.get('status') in {'validated','reused_and_revalidated'}
                             for e in history)
        recipe_validated={e['stage'][7:] for e in history if e.get('stage','').startswith('recipe_')
                          and e.get('status') in {'validated','reused_and_revalidated'}}
        safe_ids=set()
        for obj in candidate.get('objects',[]):
            id=obj.get('id')
            if id in invalidated:continue
            if active_design_choices and id not in explicitly_bound:continue
            if layout_validated:safe_ids.add(id);continue
            if not (id in recipe_validated or any(stage.startswith(id+'_') for stage in recipe_validated)):continue
            recipe_files=[runtime/('recipe_'+id+'_accepted.json')]
            recipe_files+=sorted(runtime.glob('recipe_'+id+'_contents_*_accepted.json'),reverse=True)
            file=next((p for p in recipe_files if p.is_file()),None)
            if file:
                content=json.loads(file.read_text(encoding='utf-8'))
                if {p['id'] for p in obj.get('internal_parts',[])}<={p['id'] for p in content.get('parts',[])}:safe_ids.add(id)
        if safe_ids:
            # Keep a complete copy of every already validated parametric
            # object.  The compact contract below is useful to the LLM, but
            # exact recipe/cache reuse needs the full internal-part inventory,
            # expressions and edit wiring rather than prose summaries.
            feedback['validated_parametric_specs']=[copy.deepcopy(o)
                for o in candidate.get('objects',[])
                if isinstance(o,dict) and o.get('id') in safe_ids and (o.get('acquisition') or {}).get('strategy')=='parametric']
            feedback['validated_parametric_components']=[
                {**{k:o.get(k) for k in ('id','semantic','dimensions','rigidity','parent','acquisition',
                                         'required_features','material_requirements','requirement_tags')},
                 'internal_part_contract':[{k:p.get(k) for k in ('id','rigidity','role','support','requirement_tags')}
                                           for p in o.get('internal_parts',[]) if isinstance(p,dict)],
                 'internal_clearances':o.get('internal_clearances',[])}
                for o in candidate.get('objects',[])
                if isinstance(o,dict) and o.get('id') in safe_ids and (o.get('acquisition') or {}).get('strategy')=='parametric']
            feedback['validated_parameters']=candidate.get('parameters',{})
            feedback['validated_edit_checks']=candidate.get('edit_checks',[])
        if invalidated:feedback['parametric_recipes_requiring_revision']=sorted(invalidated)
        break
    return feedback


def load_prepared_asset_records(prepared_from):
    """Load unchanged source records from a compiled scene or failed candidate."""
    prepared_from=Path(prepared_from)
    receipts_path=prepared_from/'build_receipts.json'
    if receipts_path.is_file():
        return {id:receipt['asset'] for id,receipt in
            json.loads(receipts_path.read_text(encoding='utf-8')).items() if receipt.get('asset')}
    resolutions_path=prepared_from/'component_resolutions.json'
    if not resolutions_path.is_file():
        raise FileNotFoundError(f'Prepared candidate has neither build receipts nor source resolution evidence: {prepared_from}')
    return {item['component_id']:item['record'] for item in
        json.loads(resolutions_path.read_text(encoding='utf-8'))
        if item.get('record',{}).get('path') and item.get('component_id')}


def protect_validated_recipes(feedback, unresolved):
    """Preserve unrelated preflight recipes, honoring later real-scene failures."""
    if not feedback.get('validated_parametric_components'):
        return feedback
    visual=feedback.get('visual') or {}
    if isinstance(visual,dict):visual=visual.get('decision',visual)
    findings=[*(feedback.get('geometry') or {}).get('findings',[]),
              *(visual or {}).get('findings',[])]
    invalidated={id for finding in findings
                 if finding.get('severity','blocking')=='blocking'
                 and finding.get('repair_target','decomposition') in {'geometry','materials','decomposition','source'}
                 for id in finding.get('component_ids',[])}
    if invalidated:
        for key in ('validated_parametric_components','validated_parametric_specs'):
            feedback[key]=[row for row in feedback.get(key,[]) if row.get('id') not in invalidated]
        feedback['parametric_recipes_requiring_revision']=sorted(invalidated)
    if not feedback['validated_parametric_components']:return feedback
    if not any(isinstance(item,dict) and item.get('mode') in {'retrieve','auto','generate'}
               for item in (unresolved or [])):
        return feedback
    extra=(' The previous plan reached layout validation. Preserve only the unrelated validated parametric entries listed below; '
           'later scene geometry/visual findings invalidate implicated recipes. '
           'Preserve every validated_parametric_components entry exactly, including its dimensions, parameter '
           'use, internal part IDs, rigidity and edit behavior; revise only the failed external descendants '
           'and their local requirements. If one external target combines visually distinct forms that the '
           'Judge rejected or search could not retrieve, split that target into a small number of independently '
           'searchable rigid children, keeping each physically continuous shape together. Do not redesign or '
           're-author the validated aDSL root.')
    instruction=feedback.get('instruction','')
    if 'Preserve every validated_parametric_components entry exactly' not in instruction:
        feedback['instruction']=(instruction+extra).strip()
    return feedback


def run_attempt(args) -> dict:
    args.out = args.out.resolve()
    args.out.mkdir(parents=True, exist_ok=False)
    reference_generator=getattr(args,'reference_generator',None)
    reference_generator_record=(str(Path(reference_generator).expanduser().resolve())
                                if reference_generator else None)
    snapshot=args.out/'system_source';snapshot.mkdir()
    fingerprints={}
    for source in Path(__file__).parent.glob('*.py'):
        shutil.copy2(source,snapshot/source.name)
        fingerprints[source.name]=hashlib.sha256(source.read_bytes()).hexdigest()
    write_json(args.out/'run_origin.json',{'entrypoint':'run_general_scene','planner':args.planner,
        'planning_repair_policy':'continue only while complete issue families are being resolved',
        'brief':args.brief,'replay_plan':str(args.plan) if args.plan else None,
        'harness_repair':(json.loads(Path(args.harness_repair).read_text(encoding='utf-8'))
                          if getattr(args,'harness_repair',None) else None),
        'initial_feedback':str(args.initial_feedback) if getattr(args,'initial_feedback',None) else None,
        'replan_spatial':getattr(args,'replan_spatial',False),
        'prepared_assets_from':str(getattr(args,'prepared_assets_from',None)),
        'source_sha256':fingerprints,
        'model_profile':str(args.model_profile),'vision_profile':str(args.vision_profile),
        'reference_generator':reference_generator_record,
        'generation_available':not getattr(args,'disable_generation',False),
        'note':'Executable geometry comes from agent stage outputs or an explicitly declared replay, not a category-specific scene constructor.'})
    if args.planner == "replay":
        if not args.plan:
            raise ValueError("--planner replay requires --plan")
        planner = PlannerScenePlan.model_validate_json(args.plan.read_text(encoding="utf-8"))
    elif args.planner == 'single':
        planner = GeneralLLMPlanner(args.model_profile, args.out / "agent_runtime", args.out.name).plan(args.brief)
    else:
        planner = StagedScenePlanner(args.model_profile, args.out / 'agent_runtime',args.out.name,
                                    replan_feedback=getattr(args,'replan_feedback',None),
                                    frozen_contract=getattr(args,'frozen_contract',None),
                                    allow_generation=not getattr(args,'disable_generation',False),
                                    replan_spatial=getattr(args,'replan_spatial',False),
                                    resume_runtime=args.resume_runtime).plan(args.brief)

    write_json(args.out / "planner_output.json", planner.model_dump(mode="json", by_alias=True))
    executable, decisions = to_general_plan(planner,check_layout=False,
        check_geometry=not getattr(args,'replan_spatial',False))
    write_json(args.out / "source_decisions.json", decisions)
    write_json(args.out / "general_plan.json", executable.model_dump(mode="json"))
    from general_scene import judge_general_geometry,planned_envelope_bounds
    spatial=judge_general_geometry(executable,planned_envelope_bounds(executable)).get('spatial',{})
    write_json(args.out/'planned_spatial_judge.json',spatial)
    if executable.spatial:
        from scene_spatial import write_spatial_preview
        write_spatial_preview(executable.spatial,planned_envelope_bounds(executable),executable.extent,
                              spatial,args.out/'spatial_preview.png')
    write_json(args.out / 'component_jobs.json',[
        {'component_id':c.id,'semantic':c.semantic,'strategy':c.source.mode,
         'plan_file':'general_plan.json','retrieval_query':c.source.retrieval_query,
         'reference_image':c.source.reference_image,
         'reference_generator':reference_generator_record,
         'generation_backend':'TRELLIS.1' if c.source.mode in {'auto','generate'} else None,
         'generation_condition':c.source.generation_trigger,
         'generation_reason':c.source.trellis_reason if c.source.mode in {'auto','generate'} else '',
          'dimensions':c.dimensions,'parent':c.parent,'attachment':c.attachment,'support_part':c.support_part,
         'support_footprint':c.support_footprint,'interface_checks':c.interface_notes,
         'required_features':c.required_features,'materials':c.material_requirements,
         'reference_query':c.source.reference_query,'reference_requirements':c.source.reference_requirements,
         'quality_target':planner.intent.quality_target,'accepted':False,
         'required_gates':['reference_visual' if c.source.mode=='generate' else 'source_resolution',
                           'geometry','component_visual','assembly_geometry','scene_visual']}
        for c in planner.components if c.source.mode!='parametric'])

    summary = {
        "schema": "finescene.general_run.v1",
        "scene_id": executable.scene_id,
        "brief": args.brief,
        "planner_backend": args.planner,
        "components": len(executable.objects),
        "source_decisions": decisions,
        "quality_target": planner.intent.quality_target,
    }
    if args.plan_only:
        summary.update(status="planned", accepted=False,
                       message="Detailed plan and source routing written; assembly intentionally not run.")
        write_json(args.out / "run_report.json", summary)
        return summary

    from general_assets import local_catalog, resolve_component, reuse_prepared_asset
    from general_scene import compile_general, judge_general_geometry,object_size

    assets = {}
    resolutions = []
    unresolved = []
    catalog = local_catalog()
    previous_assets={};previous_objects={};previous_plan=None
    prepared_from=getattr(args,'prepared_assets_from',None)
    if prepared_from:
        from general_scene import GeneralPlan
        prepared_from=Path(prepared_from)
        previous_raw=json.loads((prepared_from/'general_plan.json').read_text(encoding='utf-8'))
        # Asset reuse compares local sources and dimensions, not historical
        # room contracts. Old spatial schemas cannot invalidate an unchanged
        # normalized mesh or masquerade as the current scene's spatial audit.
        previous_plan=GeneralPlan.model_validate({**previous_raw,'spatial':None})
        previous_objects={o.id:o for o in previous_plan.objects}
        previous_assets=load_prepared_asset_records(prepared_from)
    cache = getattr(args,'asset_cache',args.out / 'components')
    def shared_source_key(obj):
        if obj.source.mode!='retrieve' or not obj.source.asset_id:return None
        return (obj.source.asset_id,tuple(object_size(executable,obj)),obj.source.front_rotation_deg)
    groups={};resolved_groups={}
    for obj in executable.objects:
        key=shared_source_key(obj)
        if key is not None:groups.setdefault(key,[]).append(obj)
    for obj in executable.objects:
        decision = next(d for d in decisions if d["component_id"] == obj.id)
        if obj.source.mode == "parametric":
            resolutions.append({"component_id": obj.id, "mode": "parametric", "accepted": False,
                                "status": "recipe_ready_pending_geometry_and_scene_visual_qa",
                                "reason": decision["route_reason"]})
            continue
        try:
            if getattr(args,'disable_generation',False) and obj.source.mode in {'auto','generate'}:
                raise ValueError('Generation is disabled for this run; revise source routing before acquisition.')
            print(f'[FineScene] resolving {obj.id}: {obj.source.mode}',flush=True)
            key=shared_source_key(obj);siblings=groups.get(key,[])
            if key is not None and key in resolved_groups:
                record=copy.deepcopy(resolved_groups[key])
                record['shared_source_reuse_for']=obj.id
            else:
                review_obj=obj.model_copy(deep=True)
                if len(siblings)>1:
                    review_obj.source.required_features=list(dict.fromkeys(f for item in siblings for f in item.source.required_features))
                    review_obj.source.material_requirements=list(dict.fromkeys(f for item in siblings for f in item.source.material_requirements))
                previous_obj=previous_objects.get(obj.id)
                unchanged=(previous_obj is not None and obj.id in previous_assets
                    and previous_obj.source.model_dump()==review_obj.source.model_dump()
                    and object_size(previous_plan,previous_obj).tolist()==object_size(executable,review_obj).tolist())
                if unchanged:
                    record=reuse_prepared_asset(review_obj,executable,previous_assets[obj.id],
                        args.out/'reused_source_qa'/obj.id,args.vision_profile,planner.intent.quality_target,args.brief)
                else:
                    record = resolve_component(review_obj, executable, cache, catalog, args.vision_profile,
                                           planner.intent.quality_target, offline=args.offline,
                                           reference_generator=getattr(args,'reference_generator',None),
                                           original_request=args.brief,
                                           source_instances=[s.id for s in siblings] if len(siblings)>1 else None)
                if key is not None:resolved_groups[key]=copy.deepcopy(record)
            assets[obj.id] = record
            if record.get('reconciled_specification'):
                adjusted=record['reconciled_specification']
                component=next(c for c in planner.components if c.id==obj.id)
                component.semantic=adjusted['semantic']
                component.required_features=adjusted['required_features']
                component.material_requirements=adjusted['material_requirements']
                planner.assumptions.append(f'{obj.id}: source planning reconciliation: '+adjusted['reason'])
            resolutions.append({"component_id": obj.id, "mode": obj.source.mode,
                                "accepted": bool(record.get("accepted")), "record": record})
        except Exception as error:
            print(f'[FineScene] unresolved {obj.id}: see component resolution evidence',flush=True)
            unresolved.append({"component_id": obj.id, "semantic": obj.semantic,
                               "mode": obj.source.mode, "error": str(error)[:1000],
                               'failure_details':getattr(error,'evidence',None)})
            resolutions.append({"component_id": obj.id, "mode": obj.source.mode, "accepted": False,
                                "error": str(error)[:1000]})
            candidate=(getattr(error,'evidence',None) or {}).get('inspection_candidate')
            if candidate and Path(candidate.get('path','')).is_file():assets[obj.id]=candidate
    write_json(args.out / "component_resolutions.json", resolutions)
    missing=[o.id for o in executable.objects if o.source.mode!='parametric' and o.id not in assets]
    if missing:
        summary.update(status="rejected", accepted=False,
                       message="Assembly stopped because one or more components lacked an accepted asset.",
                       unresolved=unresolved)
        summary['pending_components']=missing
        write_json(args.out / "run_report.json", summary)
        return summary

    from scene_execution import execute_with_repairs
    result = execute_with_repairs(planner, assets, args.out, args.brief,
                                  args.model_profile, args.vision_profile,
                                  max_render_rounds=getattr(args,'max_render_rounds',None),
                                  coupled_layout_recovery=getattr(args,'replan_spatial',False),
                                  prior_visual_rejections=getattr(args,'visual_rejections',None),
                                  handoff_dir=getattr(args,'handoff_root',None))
    if unresolved:
        result['accepted']=False
        summary['unresolved']=unresolved
        summary['candidate_status']='Inspection render of actual sourced assets; source rejections remain unresolved and prevent acceptance.'
    summary.update(status="accepted" if result['accepted'] else "rejected",
                   accepted=result['accepted'], geometry=result['geometry'], visual=result['visual'],
                   edit_scenarios=result.get('edit_scenarios',[]),
                   final_plan=result['final_plan'], iterations=result['iterations'],
                   replan_reason=result.get('replan_reason'),
                   visual_rejections=result.get('visual_rejections',{}))
    if result.get('harness_handoff'):summary['harness_handoff']=result['harness_handoff']
    write_json(args.out / 'run_report.json', summary)
    return summary


def resume_hint(args, stage, run_dir):
    """Point at the cheapest continuation after a model-service failure.

    Validated stage checkpoints live in ``agent_runtime``.  A second run with
    ``--resume-runtime`` reuses and revalidates them, so only the failed stage
    and the stages after it are paid for again.  This records no transport
    detail: the failed attempts themselves are already in
    ``<stage>_service_attempts.json``.
    """
    run_dir=Path(run_dir)
    runtime=run_dir/'agent_runtime'
    out=run_dir.parent/(run_dir.name+'_resume')
    hint={'stage':stage,'runtime':str(runtime),'out':str(out)}
    brief=getattr(args,'brief',None)
    if brief:
        def quoted(value):return "'"+str(value).replace("'","''")+"'"
        parts=[quoted(sys.executable),quoted(Path(__file__).resolve()),
               '--brief',quoted(brief),'--out',quoted(out),'--resume-runtime',quoted(runtime)]
        for flag,name in (('--model-profile','model_profile'),('--vision-profile','vision_profile')):
            value=getattr(args,name,None)
            if value:parts+=[flag,quoted(value)]
        hint['command']=' '.join(parts)
    return hint


def run(args, *, attempt_runner=run_attempt) -> dict:
    """Keep the original request frozen while the agent revisits failed source/shape decisions."""
    root=args.out.resolve();rounds=[];feedback=None;frozen=None;resume=args.resume_runtime
    prepared_source=getattr(args,'prepared_assets_from',None)
    initial_feedback=getattr(args,'initial_feedback',None)
    if initial_feedback:
        if isinstance(initial_feedback,(str,Path)):
            feedback_path=Path(initial_feedback)
            feedback=json.loads(feedback_path.read_text(encoding='utf-8'))
            if feedback_path.is_file() and feedback_path.name in {'run_report.json','attempt_report.json'}:
                feedback={**planning_feedback(feedback_path.parent),**feedback}
                if resume is None and (feedback_path.parent/'agent_runtime').is_dir():
                    resume=feedback_path.parent/'agent_runtime'
        else:feedback=copy.deepcopy(initial_feedback)
        feedback=protect_validated_recipes(feedback,feedback.get('unresolved',[]))
    can_replan=args.planner=='llm' and not args.plan_only
    seen=set()
    index=0
    visual_rejections={}
    while True:
        attempt=copy.copy(args)
        attempt.out=root if index==0 else root/f'replan_{index:02d}'
        # Visual rejections accumulate per component over every replan of this run.
        attempt.visual_rejections=dict(visual_rejections);attempt.handoff_root=root
        attempt.asset_cache=(getattr(args,'asset_cache',None) or root/'components').resolve()
        attempt.resume_runtime=resume
        attempt.prepared_assets_from=prepared_source
        attempt.replan_feedback=feedback
        attempt.frozen_contract=frozen
        service_stage=None
        try:report=attempt_runner(attempt)
        except Exception as error:
            service_stage=getattr(error,'finescene_stage',None)
            report={'status':'rejected','accepted':False,'error_type':type(error).__name__,
                    'error':str(error)[:2000]}
        write_json(attempt.out/'attempt_report.json',report)
        rounds.append({'index':index,'directory':str(attempt.out),'status':report['status'],
                       'accepted':report.get('accepted',False)})
        write_json(root/'generation_rounds.json',rounds)
        contract_file=attempt.out/'agent_runtime/frozen_request_contract.json'
        if frozen is None and contract_file.is_file():
            frozen=json.loads(contract_file.read_text(encoding='utf-8'))
        if report.get('accepted') or report['status']=='planned':break
        visual_rejections.update(report.get('visual_rejections') or {})
        if str(report.get('replan_reason') or '').startswith('visual_rejection_handoff'):
            report['stop_reason']='visual_rejection_handoff'
            break
        if report.get('error_type')=='PlanningRepairLimit':
            report['stop_reason']='planning_repair_call_limit'
            report.update(planning_feedback(attempt.out))
            report['harness_handoff']=str(write_harness_handoff(root,report))
            write_json(attempt.out/'attempt_report.json',report)
            break
        # A service outage is not evidence that the plan should change. Keep
        # any completed geometry/visual findings in this report, then stop until
        # the model service is usable instead of resubmitting the same replan.
        from service_retry import SERVICE_ERRORS
        visual=report.get('visual') or {}
        visual_findings=visual.get('decision',visual).get('findings',[])
        known_failures=bool((report.get('geometry') or {}).get('findings')) or any(
            f.get('severity')=='blocking' for f in visual_findings)
        model_request_errors={'APIStatusError','AuthenticationError','PermissionDeniedError','BadRequestError'}
        if report.get('error_type') in (SERVICE_ERRORS|model_request_errors):
            report['stop_reason']='model_service_unavailable'
            report['preserved_validation_findings']=known_failures
            report['resume']=resume_hint(args,service_stage,attempt.out)
            break
        if report.get('error_type') in {'AttributeError','AssertionError','IndexError','KeyError',
                                        'NameError','TypeError','UnboundLocalError'}:
            report['stop_reason']='internal_pipeline_error'
            break
        if visual.get('status')=='unavailable' and not known_failures:break
        if not can_replan or frozen is None:break
        next_feedback={key:report[key] for key in ('error','unresolved','geometry','visual','replan_reason') if key in report}
        next_feedback.update(planning_feedback(attempt.out))
        failed_stage=(next_feedback.get('detailed_planning_failure') or {}).get('stage')
        if failed_stage=='access_faces':
            report['stop_reason']='access_face_mapping_failure_requires_local_agent_fix'
            report['planning_failures']=next_feedback.get('planning_failures',[])
            report['detailed_planning_failure']=next_feedback['detailed_planning_failure']
            break
        if next_feedback.get('error') and 'detailed_planning_failure' in next_feedback:
            # The structured diagnostic retains the independent issue list;
            # the aggregate exception string repeats those same findings.
            next_feedback['error']='See detailed_planning_failure for complete, scenario-grouped geometry evidence.'
        if not next_feedback:break
        # A planning/source retry must retain the original visual evidence as
        # well as failures introduced by the current decomposition attempt.
        prior_feedback=feedback or {}
        merged_feedback=copy.deepcopy(prior_feedback)
        for key,value in next_feedback.items():
            if key=='planning_failures' and key in merged_feedback:
                merged_feedback[key]=[*(merged_feedback[key] or []),*(value or [])][-12:]
            elif key=='visual' and key in merged_feedback:
                history=list(merged_feedback.get('visual_history',[]))
                previous=merged_feedback[key]
                history.append(previous.get('decision',previous) if isinstance(previous,dict) else previous)
                latest=value.get('decision',value) if isinstance(value,dict) else value
                history.append(latest)
                merged_feedback['visual_history']=history[-4:]
                merged_feedback[key]=value
            else:
                merged_feedback[key]=value
        feedback=merged_feedback
        if getattr(args,'replan_spatial',False):
            design=attempt.out/'agent_runtime/scene_design.json'
            layout=attempt.out/'agent_runtime/spatial_reconstruction_candidate_accepted.json'
            if design.is_file():feedback['recovery_design_path']=str(design)
            if layout.is_file():
                feedback['recovery_layout_path']=str(layout)
                feedback['recovery_candidate_path']=str(layout)
            final_plan=report.get('final_plan')
            if final_plan and (Path(final_plan).parent/'build_receipts.json').is_file():
                prepared_source=Path(final_plan).parent
                feedback['previous_actual_geometry_directory']=str(prepared_source)
        # Do not resubmit the entire image/batch history or secrets to the planner.
        if 'visual' in feedback and isinstance(feedback['visual'],dict) and 'decision' in feedback['visual']:
            feedback['visual']=feedback['visual']['decision']
        failure_state={'frozen_request':frozen,'error':report.get('error'),
            'unresolved':report.get('unresolved'),
            'geometry_findings':(report.get('geometry') or {}).get('findings',[]),
            'visual_status':visual.get('status'),'visual_findings':visual_findings,
            'replan_reason':report.get('replan_reason'),
            'planning_failures':[{key:item.get(key) for key in
                ('stage','status','attempt','issue_count','blocked_check_count','issue_ids','error')}
                for item in next_feedback.get('planning_failures',[])],
            'detailed_planning_failure':None}
        detailed=next_feedback.get('detailed_planning_failure') or {}
        if detailed:
            failure_state['detailed_planning_failure']={
                'stage':detailed.get('stage'),'status':detailed.get('status'),
                'issues':[{key:issue.get(key) for key in
                    ('code','component_ids','evidence','constraint_id','repair_target',
                     'part_ids','scenarios','paths','observations')}
                    for issue in detailed.get('issues',[])],
                'blocked_checks':detailed.get('blocked_checks',[])}
        artifacts={}
        for name in ('planner_output.json','source_decisions.json','component_resolutions.json','source_qa.json'):
            path=attempt.out/name
            if path.is_file():artifacts[name]=hashlib.sha256(path.read_bytes()).hexdigest()
        failure_state['candidate_artifacts']=artifacts
        # Candidate files are provenance, not failure identity. A changed
        # artifact with the same complete constraints must not bypass the
        # repeated-state stop and trigger another identical outer replan.
        detailed=next_feedback.get('detailed_planning_failure') or {}
        unresolved=report.get('unresolved') or []
        if isinstance(unresolved,dict):
            unresolved_scope=sorted(diagnostic_families(unresolved))
        elif isinstance(unresolved,list) and unresolved and isinstance(unresolved[0],dict):
            unresolved_scope=sorted(diagnostic_families({'issues':unresolved}))
        else:
            unresolved_scope=sorted(map(str,unresolved)) if isinstance(unresolved,list) else [str(unresolved)]
        decision=visual.get('decision',visual) if isinstance(visual,dict) else {}
        signature_state={
            'frozen_request':hashlib.sha256(json.dumps(frozen,sort_keys=True,ensure_ascii=False,
                default=lambda value:value.tolist() if hasattr(value,'tolist') else str(value)).encode()).hexdigest(),
            'error_type':report.get('error_type'),
            'unresolved_scope':unresolved_scope,
            'geometry_issue_families':sorted(diagnostic_families(report.get('geometry') or {})),
            'visual_status':visual.get('status') if isinstance(visual,dict) else None,
            'visual_verdict':decision.get('verdict') if isinstance(decision,dict) else None,
            'visual_issue_families':sorted(diagnostic_families({'issues':visual_findings})),
            'planning_failure_stages':sorted({(item.get('stage'),item.get('status'))
                for item in next_feedback.get('planning_failures',[])}),
            'detailed_planning_stage':detailed.get('stage'),
            'planning_issue_families':sorted(diagnostic_families(detailed)),
        }
        signature=hashlib.sha256(json.dumps(signature_state,sort_keys=True,ensure_ascii=False,
            default=lambda value:value.tolist() if hasattr(value,'tolist') else str(value)).encode()).hexdigest()
        write_json(attempt.out/'replan_failure_state.json',
                   {**failure_state,'signature':signature,'signature_excludes_candidate_artifact_hashes':True,
                    'signature_basis':'stable issue families, affected components and authorized repair scopes'})
        if signature in seen:
            report['stop_reason']='repeated_replanning_failure';break
        seen.add(signature)
        feedback['instruction']='Revisit decomposition and source routing to resolve the recorded failure. Preserve the original frozen request, style requirements, quality and editability. Do not repeat unavailable representations or replace the entire editable asset with a generated mesh.'
        feedback=protect_validated_recipes(feedback,report.get('unresolved',[]))
        write_json(attempt.out/'replan_feedback.json',feedback)
        runtime_events=attempt.out/'agent_runtime/stage_events.json'
        try:validated_events=json.loads(runtime_events.read_text(encoding='utf-8')) if runtime_events.is_file() else []
        except (OSError,json.JSONDecodeError):validated_events=[]
        has_validated_layout=any(event.get('stage')=='layout' and
            event.get('status') in {'validated','reused_and_revalidated'} for event in validated_events)
        # A failed source-only replan must not replace the last known-good
        # runtime with a newer attempt that may have no accepted recipe files.
        preserved=feedback.get('validated_parametric_specs',[])
        local_runtime=attempt.out/'agent_runtime'
        has_preserved_recipes=bool(preserved) and all(
            (local_runtime/('recipe_'+row['id']+'_accepted.json')).is_file() or
            list(local_runtime.glob('recipe_'+row['id']+'_contents_*_accepted.json')) for row in preserved)
        if resume is None or has_validated_layout or has_preserved_recipes:
            resume=attempt.out/'agent_runtime'
        if getattr(args,'replan_spatial',False):resume=args.resume_runtime
        print(f'[FineScene] escalating to decomposition/source replan {index+1}',flush=True)
        index+=1
    report={**report,'generation_rounds':rounds,'final_attempt_directory':str(attempt.out),
            'replan_policy':'continue while stable issue families are resolved; stop when a full replan leaves the same families',
            'replans':max(0,len(rounds)-1)}
    write_json(root/'run_report.json',report)
    return report


def main() -> None:
    # Windows PowerShell may expose a legacy GBK stdout.  Run reports and
    # Agent evidence contain the original multilingual brief and must still be
    # printable after a rejected run; encoding output cannot affect planning.
    for stream in (sys.stdout, sys.stderr):
        try: stream.reconfigure(encoding='utf-8', errors='backslashreplace')
        except Exception: pass
    parser = argparse.ArgumentParser(description="Plan arbitrary FineScene scenes and route each component to aDSL, retrieval or TRELLIS")
    parser.add_argument("--brief", required=True, help="Natural-language scene request")
    parser.add_argument("--out", type=Path, required=True, help="New output directory")
    parser.add_argument("--planner", choices=("llm", "single", "replay"), default="llm",
                        help='llm: staged planner; single: previous monolithic baseline; replay: explicit saved plan')
    parser.add_argument("--plan", type=Path, help="Replay a saved planner_output.json with --planner replay")
    parser.add_argument("--plan-only", action="store_true", help="Write planning and routing evidence without asset resolution")
    parser.add_argument('--harness-repair',type=Path,help='JSON note for a replayed plan that the harness model hand-wrote after a handoff: '
                        '{"handoff": path, "components": [ids], "summary": text}; recorded in run_origin.json')
    parser.add_argument('--resume-runtime',type=Path,help='Reuse matching validated staged-planner checkpoints; changed stages are rerun')
    parser.add_argument('--replan-spatial',action='store_true',help='Explicit recovery: revalidate existing design choices/geometry, replan coupled composition and layout; requires --resume-runtime')
    parser.add_argument('--max-render-rounds',type=int,default=None,
        help='Bound full render/repair rounds (default 12). Each round renders the whole scene, so this is the '
             'main lever on wall-clock cost; the planner keeps its own separate in-stage repair budget.')
    parser.add_argument('--asset-cache',type=Path,help='Reuse verified assets and downloaded originals across resumed execution runs')
    parser.add_argument('--prepared-assets-from',type=Path,help='Reuse unchanged normalized assets from a previous candidate directory; rejected assets require fresh source Judge review')
    parser.add_argument('--initial-feedback',type=Path,help='Give the staged planner evidence from a previous rejected attempt before the first new plan')
    parser.add_argument("--offline", action="store_true", help="Disable network image/model search; unresolved components remain rejected")
    parser.add_argument('--disable-generation',action='store_true',help='Plan with retrieval and aDSL only, including existing generated samples')
    parser.add_argument("--reference-generator", type=Path,
                        help="Optional executable adapter for generated reference images; it receives --request JSON and writes --output raster")
    parser.add_argument("--model-profile", type=Path, default=Path(__file__).resolve().parents[1]/'configs/deepseek-flash.yaml')
    parser.add_argument("--vision-profile", type=Path, default=Path(__file__).resolve().parents[1]/'configs/deepseek-flash.yaml')
    args = parser.parse_args()
    if args.plan and args.planner not in {"replay"}:
        parser.error("--plan requires --planner replay")
    if args.planner == 'replay' and not args.plan:
        parser.error('--planner replay requires --plan')
    if args.harness_repair:
        if args.planner != 'replay':
            parser.error('--harness-repair marks a hand-written plan and requires --planner replay')
        note=json.loads(args.harness_repair.read_text(encoding='utf-8'))
        if not note.get('components') or not note.get('summary'):
            parser.error('--harness-repair needs "components" (hand-written component IDs) and "summary"')
    if args.replan_spatial and (not args.resume_runtime or args.planner!='llm'):
        parser.error('--replan-spatial requires --planner llm and --resume-runtime')
    if args.out.exists():
        parser.error('--out must be a new directory; previous evidence is preserved')
    try:
        report = run(args)
    except Exception as error:
        report = {'status': 'rejected', 'accepted': False, 'error_type': type(error).__name__,
                  'error': str(error)[:2000]}
        write_json(args.out / 'run_report.json', report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["status"] not in {"accepted", "planned"}:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
