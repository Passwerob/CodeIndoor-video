"""Reproducible asset refinement + actual geometry and image acceptance."""
import argparse,json,shutil,subprocess
from pathlib import Path
import finescene_env as fenv
from constraint_solver import judge_component_bounds
from visual_judge import judge_visual


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--source',type=Path,required=True);parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args();source=args.source.resolve();out=args.out.resolve();out.mkdir(parents=True,exist_ok=False)
    for name in ['resolved_scene_contract.json','baseline_scene_contract.json','scene_contract.json','planner_output.json','adsl_scene.json','model_plan.json']:
        if (source/name).is_file():shutil.copy2(source/name,out/name)
    contract=json.loads((out/'resolved_scene_contract.json').read_text(encoding='utf-8'))
    contract['quality_target']='photorealistic'
    contract['brief']='真实、成熟、尽可能接近实拍的复古书房。使用下载的高质量模型、PBR 材质和可用 TRELLIS.2 资产。真实书籍封面与纸页，自然错落，合理尺度与窗光，避免参数化积木感。'
    (out/'resolved_scene_contract.json').write_text(json.dumps(contract,ensure_ascii=False,indent=2),encoding='utf-8')
    snap=out/'source_snapshot';snap.mkdir()
    for f in Path(__file__).parent.glob('*.py'):shutil.copy2(f,snap/f.name)
    shutil.copy2(fenv.STUDY_ASSETS/'download_manifest.json',out/'download_manifest.json')
    with (out/'blender_refinement.log').open('w',encoding='utf-8') as log:
        result=subprocess.run([fenv.BLENDER,'--background','--python-exit-code','1','--python',str(Path(__file__).with_name('realistic_refiner.py')),'--','--source',str(source),'--output',str(out)],stdout=log,stderr=subprocess.STDOUT)
    if result.returncode:
        (out/'run_report.json').write_text(json.dumps({'accepted':False,'status':'builder_failed','returncode':result.returncode}),encoding='utf-8')
        raise SystemExit(result.returncode)
    contract=json.loads((out/'resolved_scene_contract.json').read_text(encoding='utf-8'))
    bounds=json.loads((out/'blender_world_bounds.json').read_text(encoding='utf-8'))
    geometry=judge_component_bounds(contract,bounds)
    (out/'geometry_judge.json').write_text(json.dumps(geometry,indent=2),encoding='utf-8')
    visual=judge_visual(out,fenv.LLM_CONFIGS/'gpt6astra.yaml')
    textures=json.loads((out/'texture_audit.json').read_text(encoding='utf-8'))
    accepted=geometry['status']=='passed' and visual['accepted'] and not textures['missing_files']
    report={'accepted':accepted,'status':'accepted_realistic_scene' if accepted else 'needs_revision','quality_target':'photorealistic','geometry':geometry['status'],'visual':visual['status'],'textures':textures,'source_candidate':str(args.source),'limitations':['AABB support does not establish gravity/friction stability.','Imported assets are fitted once to explicit envelopes; this is recorded in refinement receipts.']}
    (out/'run_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':main()
