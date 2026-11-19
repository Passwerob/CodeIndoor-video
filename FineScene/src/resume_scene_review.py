"""Resume Agent visual review of immutable real renders without rebuilding a room."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path

from general_planner import PlannerScenePlan
from general_vision import review_scene_images
from run_general_scene import planning_feedback,write_json
from scene_execution import visual_request


def resume_review(run_directory,output,vision_profile):
    source=Path(run_directory).resolve();output=Path(output).resolve()
    output.mkdir(parents=True,exist_ok=False)
    def read(name):return json.loads((source/name).read_text(encoding='utf-8'))
    origin=read('run_origin.json')
    plan=PlannerScenePlan.model_validate(read('planner_output.json'))
    manifest=read('visual_review/render_manifest.json')
    blend=source/'visual_review/review.blend'
    if not blend.is_file():raise ValueError('Missing real Blender scene')
    if manifest.get('renderer_source_sha256')!=hashlib.sha256(Path(__file__).with_name('general_render.py').read_bytes()).hexdigest():
        raise ValueError('Renderer changed: regenerate evidence rather than silently reusing old images')
    images=manifest['images']
    evidence={str(blend):hashlib.sha256(blend.read_bytes()).hexdigest()}
    for path in images:evidence[path]=hashlib.sha256(Path(path).read_bytes()).hexdigest()
    write_json(output/'review_origin.json',{'source_run':str(source),'brief':origin['brief'],
        'evidence_sha256':evidence,'reviewer_source_sha256':hashlib.sha256(Path(__file__).with_name('general_vision.py').read_bytes()).hexdigest(),
        'note':'Fresh Agent judgments of existing unchanged real scene evidence. No scene edits or assistant acceptance.'})
    geometry=read('actual_geometry_judge.json');support=read('actual_support_judge.json')
    receipts=read('build_receipts.json');assets={id:row['asset'] for id,row in receipts.items() if row.get('asset')}
    request=visual_request(plan,origin['brief'],actual_geometry=read('visual_review/actual_bounds.json'),
        actual_support=support,actual_spatial=geometry.get('spatial'),prepared_assets=assets)
    request['render_evidence']=manifest
    visual=review_scene_images(images,request,vision_profile,output/'final_visual_qa.json',quality=plan.intent.quality_target)
    previous=read('run_report.json');source_qa=read('source_qa_status.json')
    accepted=geometry.get('status')=='passed' and visual['accepted'] and source_qa.get('status')=='passed'
    report={**previous,'accepted':accepted,'status':'accepted' if accepted else 'rejected',
        'visual':visual,'geometry':geometry,'source_qa':source_qa,'review_resume_directory':str(output)}
    write_json(output/'run_report.json',report)
    # Keep exact accepted aDSL contracts in any subsequent Agent source/design
    # replan, alongside ALL concrete visual and physical findings.
    feedback={**planning_feedback(source),'geometry':geometry,'visual':visual['decision'],
        'unresolved':previous.get('unresolved',[]),
        'instruction':'Resolve all recorded independent failures using the original user request. Preserve validated components that are unrelated to the findings. Source visual unavailability is not evidence that an object must change.'}
    write_json(output/'replan_feedback.json',feedback)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--vision-profile',type=Path,required=True)
    args=parser.parse_args()
    result=resume_review(args.run,args.out,args.vision_profile)
    print(json.dumps({'accepted':result['accepted'],'visual_status':result['visual']['status'],
        'batches':result['visual']['batch_count'],'required_batches':result['visual']['required_batch_count']},ensure_ascii=False))


if __name__=='__main__':main()
