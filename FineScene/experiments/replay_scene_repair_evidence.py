"""Revalidate original Agent output and optionally request bounded real Agent repairs.

Never edits source checkpoints or supplies scene-specific corrections.
"""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from staged_planner import (Decomposition,Recipe,Layout,FunctionalBindings,StagedScenePlanner,
    apply_functional_bindings,_functional_bindings_prompt,_functional_bindings_response_schema,
    assemble_plan,replacement_paths,LAYOUT,prune_unwired_proposals)
from scene_spatial import SpatialContract
from scene_experience import spatial_experience_prompt,prepend_experience
from general_planner import to_general_plan
from planning_diagnostics import failure_report


def read(path):return json.loads(path.read_text(encoding='utf-8-sig'))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-runtime',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--live',action='store_true')
    args=parser.parse_args()
    source=args.source_runtime.resolve();out=args.out.resolve()
    out.mkdir(parents=True,exist_ok=False)
    def save(name,data):
        (out/name).write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    snap=out/'system_source';snap.mkdir()
    for path in (ROOT/'src').glob('*.py'):shutil.copy2(path,snap/path.name)
    inputs={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in source.glob('*.json')}
    save('origin.json',{'mode':'unchanged Agent checkpoint replay with optional bounded Agent repair',
        'source_runtime':str(source),'source_json_sha256':inputs,'live':args.live,'accepted_scene':False})
    outline=Decomposition.model_validate(read(source/'frozen_decomposition.json')['plan'])
    spatial=SpatialContract.model_validate(read(source/'frozen_spatial_contract.json')['contract'])
    recipes={o.id:Recipe.model_validate(read(source/f'recipe_{o.id}_accepted.json'))
             for o in outline.objects if o.acquisition.strategy=='parametric'}
    layout=Layout.model_validate(read(source/'layout_failure_0.json')['candidate'])
    outline,_=prune_unwired_proposals(outline,recipes,layout)
    binding_seed=read(source/'functional_bindings_accepted.json')
    reports={}
    try:apply_functional_bindings(outline,FunctionalBindings.model_validate(binding_seed))
    except ValueError as error:reports['original_bindings']=failure_report(error,'functional_bindings')
    else:reports['original_bindings']={'status':'passed'}
    try:assemble_plan(outline,recipes,layout,spatial=spatial)
    except ValueError as error:reports['original_layout']=failure_report(error,'layout')
    else:reports['original_layout']={'status':'passed'}
    save('original_candidate_diagnostics.json',reports)
    if args.live:
        brief=read(source.parent/'run_origin.json')['brief']
        agent=StagedScenePlanner(ROOT/'configs/deepseek-flash.yaml',out/'agent_runtime',out.name,max_repairs=2)
        agent.initialize_runtime()
        ids=[o.id for o in outline.objects]
        async def repair():
            revised=await agent.stage('functional_bindings',FunctionalBindings,
                _functional_bindings_prompt(ids),{'brief':brief,'component_ids':ids,
                    'response_schema':_functional_bindings_response_schema(ids),
                    'components':[o.model_dump(mode='json') for o in outline.objects]},
                lambda b:apply_functional_bindings(outline,b),replacement_paths,
                initial_candidate=binding_seed)
            save('agent_revised_outline.json',revised.model_dump(mode='json'))
            def validate(value):
                plan=assemble_plan(revised,recipes,value,spatial=spatial)
                # This probe establishes detailed placement preflight only. The
                # production pipeline still performs Blender and visual audits.
                return plan
            return await agent.stage('layout',Layout,prepend_experience(LAYOUT,spatial_experience_prompt(brief)),
                {'brief':brief,'frozen_decomposition':revised.model_dump(mode='json'),
                 'frozen_spatial_contract':spatial.model_dump(mode='json'),
                 'recipes':{id:r.model_dump(mode='json') for id,r in recipes.items()}},validate,
                lambda data:[p for p in replacement_paths(data) if p.startswith('/placements/')
                    and (p.endswith('/position') or p.endswith('/yaw_deg'))],
                initial_candidate=layout.model_dump(mode='json'))
        try:plan=asyncio.run(repair())
        except Exception as error:
            reports['live_repair']={'status':'rejected','error_type':type(error).__name__,
                                    'diagnostics':failure_report(error,'live_repair')}
        else:
            save('repaired_planner_output.json',plan.model_dump(mode='json',by_alias=True))
            reports['live_repair']={'status':'detailed_preflight_passed','accepted_scene':False}
            try:to_general_plan(plan,check_layout=True)
            except ValueError as error:reports['full_spatial_audit']=failure_report(error,'full_spatial_audit')
            else:reports['full_spatial_audit']={'status':'passed'}
    reports['source_unchanged']=all(hashlib.sha256((source/name).read_bytes()).hexdigest()==sha
                                    for name,sha in inputs.items())
    save('result.json',reports)
    print(json.dumps({k:({'status':v.get('status'),'issue_count':len(v.get('issues',[]))}
                         if isinstance(v,dict) else v) for k,v in reports.items()},ensure_ascii=False))


if __name__=='__main__':main()
