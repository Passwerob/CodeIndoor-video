"""Developer geometry/export fixture, NOT an agent-generated asset or style result.

Runs the production Blender exporter and measured-geometry checks. Deliberately
does not perform visual acceptance; the normal scene CLI owns visual judging.
"""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from general_planner import PlannerScenePlan
from scene_execution import evaluate_candidate,save


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--out',required=True,type=Path)
    args=parser.parse_args()
    if args.out.exists():parser.error('Use a new output directory')
    fixture=json.loads((Path(__file__).resolve().parents[1]/'examples/general_metrology_fixture.json').read_text())
    fixture['intent'].update(scene_id='sweep_export_test',description='Developer sweep export fixture',extent=[3,3,2])
    fixture['parameters']={};fixture['relations']=[];fixture['edit_plans']=[]
    base=fixture['components'][0]
    base.update(id='assembly',semantic='Generic test assembly',dimensions=[1,.5,1],parts=[
        {'id':'base','primitive':'box','size':[1,.5,.04],'center':[0,0,.02]},
        {'id':'curve','primitive':'sweep','size':[.9,.1,.66],'center':[0,0,.37],
         'path':[[-.4,0,-.3],[-.2,.02,-.1],[0,0,.2],[.4,0,.3]],'radius':.03,
         'rotation':[0,0,20],'finish':'metal'}],
        assembly={'roots':['base'],'relations':[{'kind':'attached_to','part':'curve','target':'base',
                                               'reason':'Measured capsule contact on planar base'}]})
    fixture['components']=[base]
    plan=PlannerScenePlan.model_validate(fixture)
    result=evaluate_candidate(plan,{},args.out.resolve(),plan.intent.description,None,review=False)
    save(args.out/'developer_fixture_result.json',{'origin':'manual geometry regression fixture',**result})
    print(json.dumps({'geometry':result['geometry'],'visual':result['visual']},indent=2))
    if result['geometry']['status']!='passed':raise SystemExit(1)


if __name__=='__main__':main()
