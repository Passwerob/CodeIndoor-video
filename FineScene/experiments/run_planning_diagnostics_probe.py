"""Recheck original Agent candidates; optionally ask the production Agent to repair.

No coordinates or recipe corrections are authored by this experiment. The source
runtime is immutable, and each probe writes a new directory with provenance.
"""
import argparse
import asyncio
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from planning_diagnostics import failure_report
from staged_planner import (StagedScenePlanner, Decomposition, Recipe, Layout, RECIPE,
    component_recipe_context, validate_recipe, coarse_preflight, recipe_repair_paths)
from scene_spatial import SpatialContract


def read(path):return json.loads(path.read_text(encoding='utf-8'))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-runtime',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--repair-component',default=None)
    parser.add_argument('--model-profile',type=Path,default=ROOT/'configs/deepseek-flash.yaml')
    args=parser.parse_args()
    args.out.mkdir(parents=True,exist_ok=False)
    snapshot=args.out/'system_source';snapshot.mkdir()
    for source in (ROOT/'src').glob('*.py'):shutil.copy2(source,snapshot/source.name)
    runtime=args.source_runtime
    frozen=read(runtime/'frozen_decomposition.json')
    outline=Decomposition.model_validate(frozen.get('plan',frozen))
    records=[]
    candidates={}
    legacy_path=runtime.parent/'system_source/staged_planner.py'
    legacy=None
    if legacy_path.is_file():
        spec=importlib.util.spec_from_file_location('previous_staged_planner',legacy_path)
        legacy=importlib.util.module_from_spec(spec)
        sys.modules[spec.name]=legacy
        spec.loader.exec_module(legacy)
    for obj in outline.objects:
        path=runtime/f'recipe_{obj.id}_accepted.json'
        if obj.acquisition.strategy!='parametric' or not path.is_file():continue
        candidate=read(path);candidates[obj.id]=candidate
        record={'component_id':obj.id,'source':str(path.resolve())}
        if legacy:
            try:legacy.validate_recipe(outline,obj,legacy.Recipe.model_validate(candidate))
            except ValueError as failure:record['previous_validator']={'status':'needs_revision','error':str(failure)}
            else:record['previous_validator']={'status':'passed'}
        started=time.monotonic()
        try:validate_recipe(outline,obj,Recipe.model_validate(candidate))
        except ValueError as failure:record['current_validator']=failure_report(failure,'recipe_'+obj.id)
        else:record['current_validator']={'status':'passed','issues':[],'blocked_checks':[]}
        record['elapsed_seconds']=round(time.monotonic()-started,6)
        records.append(record)
    report={'source_runtime':str(runtime.resolve()),'mode':'unchanged_original_agent_candidates',
            'accepted_scene':False,'recipes':records,'limits':[
                'Planning diagnostics only; no new Blender or visual acceptance.',
                'Previous validator source is loaded against the current geometry backend.']}
    coarse_path=runtime/'coarse_layout_failure_0.json'
    spatial_path=runtime/'spatial_plan_accepted.json'
    if coarse_path.is_file() and spatial_path.is_file():
        report['initial_coarse_diagnostics']=coarse_preflight(outline,
            Layout.model_validate(read(coarse_path)['candidate']),SpatialContract.model_validate(read(spatial_path)))
    (args.out/'diagnostics.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    if args.repair_component:
        obj=next(item for item in outline.objects if item.id==args.repair_component)
        planner=StagedScenePlanner(args.model_profile,args.out/'agent_runtime',args.out.name)
        planner.initialize_runtime()
        context=component_recipe_context(outline,obj,outline.description)
        started=time.monotonic()
        try:
            repaired=asyncio.run(planner.stage('recipe_'+obj.id,Recipe,RECIPE,context,
                lambda recipe:validate_recipe(outline,obj,recipe),
                recipe_repair_paths,
                initial_candidate=candidates[obj.id]))
        except ValueError as failure:
            report['agent_repair']={'status':'needs_revision','error':str(failure)}
        else:
            report['agent_repair']={'status':'passed','accepted_scene':False,
                'model_calls':len(planner.transport_calls),'repair_calls':sum('_repair_' in c['stage'] for c in planner.transport_calls),
                'elapsed_seconds':round(time.monotonic()-started,3)}
            (args.out/'repaired_recipe.json').write_text(repaired.model_dump_json(indent=2),encoding='utf-8')
        (args.out/'diagnostics.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'recipes':[{ 'component_id':item['component_id'],
        'previous':item.get('previous_validator',{}).get('status'),
        'current':item['current_validator']['status'],
        'issues':len(item['current_validator']['issues'])} for item in records],
        'coarse_issues':len(report.get('initial_coarse_diagnostics',{}).get('issues',[])),
        'agent_repair':report.get('agent_repair'),'report':str((args.out/'diagnostics.json').resolve())},ensure_ascii=False))


if __name__=='__main__':main()
