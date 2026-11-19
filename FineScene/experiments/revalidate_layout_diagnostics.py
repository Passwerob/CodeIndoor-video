"""Read-only revalidation of a staged Agent layout; never changes its candidate."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from staged_planner import Decomposition,Recipe,Layout,RequestContract,assemble_plan,prune_unwired_proposals
from scene_spatial import SpatialContract
from planning_diagnostics import failure_report


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--runtime',type=Path,required=True)
    parser.add_argument('--failure',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--outline',type=Path,help='Explicit Agent-produced revised mounting outline')
    args=parser.parse_args();root=args.runtime
    outline=Decomposition.model_validate_json(args.outline.read_text(encoding='utf-8')) if args.outline else Decomposition.model_validate(json.loads((root/'frozen_decomposition.json').read_text(encoding='utf-8'))['plan'])
    contract=RequestContract.model_validate_json((root/'request_contract_accepted.json').read_text(encoding='utf-8'))
    recipes={}
    for obj in outline.objects:
        if obj.acquisition.strategy!='parametric':continue
        path=root/f'recipe_{obj.id}_accepted.json'
        if not path.is_file():
            path=sorted(root.glob(f'recipe_{obj.id}_contents_*_accepted.json'))[-1]
        recipes[obj.id]=Recipe.model_validate_json(path.read_text(encoding='utf-8'))
    saved=json.loads(args.failure.read_text(encoding='utf-8'))
    layout=Layout.model_validate(saved['candidate'])
    outline,_=prune_unwired_proposals(outline,recipes,layout)
    spatial=SpatialContract.model_validate(json.loads((root/'frozen_spatial_contract.json').read_text(encoding='utf-8'))['contract'])
    try:
        assemble_plan(outline,recipes,layout,style_requirements=contract.style_requirements,spatial=spatial)
    except ValueError as error:report=failure_report(error,'layout')
    else:report={'status':'passed','issues':[]}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps({'source_failure':str(args.failure),'candidate_modified':False,
        'report':report},ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'status':report['status'],'issue_count':len(report['issues']),
        'first_issue':report['issues'][0] if report['issues'] else None},ensure_ascii=False,indent=2))


if __name__=='__main__':main()
