"""Production exporter/Blender measurement smoke test, not a user scene."""
import json
from pathlib import Path
import subprocess
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from general_scene import GeneralPlan,SceneObject,Part,compile_general,audit_exported_support_contacts
from surface_mounting import SurfaceContact
from asset_physics import AssemblyContract,PartRelation
from adsl.core.serialize import asset_to_dict


def main():
    out=Path(sys.argv[1]).resolve();out.mkdir(parents=True,exist_ok=True)
    def save(name,value):(out/name).write_text(json.dumps(value,indent=2,default=lambda x:x.tolist()),encoding='utf-8')
    host=SceneObject(id='host',semantic='Rotated support fixture for compiler test',size=(4,.2,3),
        position=(1,1,0),rotation_deg=37,collision_role='shell',
        parts=[Part(id='wall',primitive='box',size=(4,.2,3),center=(0,0,1.5))])
    child=SceneObject(id='mounted',semantic='Backplate fixture for measured contact test',size=(.4,.6,.5),
        parent='host',position=(.6,7,1.2),
        surface_contact=SurfaceContact(parent_part='wall',parent_face='y_min',child_part='backplate',child_face='y_max'),
        parts=[Part(id='backplate',primitive='box',size=(.4,.03,.5),center=(0,.285,.25)),
               Part(id='body',primitive='box',size=(.2,.57,.2),center=(0,-.015,.25))],
        assembly=AssemblyContract(roots=['backplate'],relations=[PartRelation(kind='attached_to',part='body',
            target='backplate',reason='Body meets the mounting plate at its inner face')]))
    plan=GeneralPlan(scene_id='mount_diagnostic',description='Test evidence only',extent=(10,10,4),objects=[host,child])
    scene,bounds,receipts=compile_general(plan)
    save('general_plan.json',plan.model_dump(mode='json'));save('build_receipts.json',receipts)
    save('adsl_scene.json',asset_to_dict(scene))
    src=Path(__file__).resolve().parents[1]/'src';sys.path.insert(0,str(src));import finescene_env;blender=finescene_env.BLENDER
    commands=[('export',[blender,'-b','--factory-startup','--python-exit-code','1','--python',str(src/'blender_worker_fast.py'),
        '--','--input',str(out/'adsl_scene.json'),'--output',str(out/'export')]),
        ('measure',[blender,'-b','--factory-startup','--python-exit-code','1','--python',str(src/'general_render.py'),
        '--','--scene',str(out/'export/scene.blend'),'--plan',str(out/'general_plan.json'),
        '--geometry-only','--output',str(out/'measured')])]
    for name,cmd in commands:
        with (out/(name+'.log')).open('w',encoding='utf-8') as stream:
            subprocess.run(cmd,stdout=stream,stderr=subprocess.STDOUT,check=True,timeout=180)
    read=lambda name:json.loads((out/'measured'/name).read_text())
    report=audit_exported_support_contacts(plan,read('actual_bounds.json'),read('actual_support_contacts.json'),
        receipts=receipts,measured_parts=read('actual_part_bounds.json'))
    save('mount_audit.json',report)
    assert report['status']=='passed',report
    assert report['components']['mounted']['normal_gap_m']<.001,report
    print(json.dumps({'status':report['status'],'normal_gap_m':report['components']['mounted']['normal_gap_m'],
        'evidence':str(out/'mount_audit.json')}))


if __name__=='__main__':main()
