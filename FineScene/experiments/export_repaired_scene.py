"""Materialize an Agent-repaired recipe in its original scene for inspection."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from general_planner import PlannerScenePlan
from staged_planner import Recipe
from scene_execution import evaluate_candidate,save


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan',type=Path,required=True)
    parser.add_argument('--recipe',type=Path,required=True)
    parser.add_argument('--component',required=True)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--vision-profile',type=Path,required=True)
    args=parser.parse_args()
    output=args.out.resolve();output.mkdir(parents=True,exist_ok=False)
    source=output/'system_source';source.mkdir()
    for path in (ROOT/'src').glob('*.py'):shutil.copy2(path,source/path.name)
    plan=PlannerScenePlan.model_validate_json(args.plan.read_text(encoding='utf-8'))
    recipe=Recipe.model_validate_json(args.recipe.read_text(encoding='utf-8'))
    data=plan.model_dump(mode='json',by_alias=True)
    index=next(i for i,c in enumerate(plan.components) if c.id==args.component)
    data['components'][index]['parts']=[p.model_dump(mode='json') for p in recipe.parts]
    data['components'][index]['assembly']=recipe.assembly.model_dump(mode='json') if recipe.assembly else None
    updated=PlannerScenePlan.model_validate(data)
    save(output/'provenance.json',{
        'original_plan':str(args.plan.resolve()),
        'original_plan_sha256':hashlib.sha256(args.plan.read_bytes()).hexdigest(),
        'agent_recipe':str(args.recipe.resolve()),
        'agent_recipe_sha256':hashlib.sha256(args.recipe.read_bytes()).hexdigest(),
        'component_id':args.component,
        'operation':'Apply the unchanged Agent-produced recipe; preserve original scene inventory and placements.',
        'accepted_scene':False})
    save(output/'model_plan.json',updated.model_dump(mode='json',by_alias=True))
    result=evaluate_candidate(updated,{},output/'candidate',plan.intent.description,args.vision_profile.resolve())
    save(output/'evaluation.json',result)
    print(json.dumps({'accepted':result['accepted'],'geometry':result['geometry']['status'],
        'visual':result['visual']['status'],
        'blend':str(output/'candidate/visual_review/review.blend')},ensure_ascii=False))


if __name__=='__main__':main()
