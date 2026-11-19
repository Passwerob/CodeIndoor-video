"""Regression cases for aperture identity and independent, actionable placement errors."""
import sys
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from general_scene import GeneralPlan, SceneObject, Part, audit_parametric_scene
from staged_planner import _fitted_window_component_ids, _unattached_shell_children


def box(id, size, **kwargs):
    return SceneObject(id=id, semantic=id, size=size, parts=[Part(
        id='body', primitive='box', size=size, center=[0, 0, size[2]/2])], **kwargs)


def placement_case(broken_mount=False):
    shell=box('shell', [4,4,.1], position=[0,0,-.1], collision_role='shell')
    door=box('door', [.8,.04,2], parent='shell')
    objects=[shell, door]
    if broken_mount:
        objects += [box('bad_mount',[.2,.2,.3],parent='shell',position=[3,0,0],
            surface_contact=dict(parent_part='body',parent_face='z_max',child_face='z_min')),
            box('dependent',[.1,.1,.1],parent='bad_mount',mount='top')]
    return GeneralPlan(scene_id='repair_evidence',description='Independent placement errors',
                       extent=[4,4,3],objects=objects)


class SceneRepairEvidenceTests(unittest.TestCase):
    def test_overlap_reports_measured_part_pairs_and_penetration(self):
        plan=GeneralPlan(scene_id='collision',description='Overlapping furniture',extent=[4,4,3],
            objects=[box('seat',[1,1,1]),box('table',[1,1,1],position=[.75,0,0])])
        issue=next(f for f in audit_parametric_scene(plan)['findings'] if f.get('code')=='occupied_overlap')
        self.assertEqual(issue['measured']['intersections'][0]['part_ids'],['body','body'])
        self.assertEqual(issue['measured']['intersections'][0]['penetration_xyz_m'],[.25,1,1])
        self.assertEqual(issue['expected']['maximum_allowed_penetration_m'],.015)

    def test_runner_preserves_diagnostics_and_does_not_reset_repair_budget(self):
        from run_general_scene import run
        from staged_planner import PlanningRepairLimit
        from planning_diagnostics import Diagnostics
        calls=[]
        with tempfile.TemporaryDirectory() as folder:
            args=SimpleNamespace(out=Path(folder)/'run',planner='llm',plan_only=False,
                resume_runtime=None,prepared_assets_from=None,initial_feedback=None,replan_spatial=False)
            def attempt(value):
                calls.append(value.out)
                runtime=value.out/'agent_runtime';runtime.mkdir(parents=True)
                report=Diagnostics('layout')
                report.add('scene_extent','Door remains below floor.',component_ids=['door'],
                    paths=['/placements/1/position'],measured={'z_min_m':-.1},expected={'z_min_m':0})
                (runtime/'frozen_request_contract.json').write_text('{}',encoding='utf-8')
                (runtime/'stage_events.json').write_text(json.dumps([
                    {'stage':'layout','status':'failed','attempt':2}]),encoding='utf-8')
                (runtime/'layout_failure_2.json').write_text(json.dumps({
                    'candidate':{},'measurements':report.report()}),encoding='utf-8')
                raise PlanningRepairLimit('layout',2,report.report())
            result=run(args,attempt_runner=attempt)
            self.assertEqual(len(calls),1)
            self.assertEqual(result['stop_reason'],'planning_repair_call_limit')
            issue=result['detailed_planning_failure']['issues'][0]
            self.assertEqual(issue['code'],'scene_extent')
            self.assertEqual(issue['observations'][0]['measured']['z_min_m'],-.1)

    def test_partial_mapping_can_replace_unrelated_list_length(self):
        from staged_planner import LocalRepair, apply_local_repair
        before={'composition':{'open_spaces':[{'id':'unused'}],'groups':[{'id':'keep'}],
                               'whole_space':'preserve'}}
        repair=LocalRepair.model_validate({'reason':'Remove inferred void, preserve group.', 'replacements':[
            {'path':'/composition','value':{'open_spaces':[]}},
            {'path':'/composition/groups/0/id','value':'keep'}]})
        after=apply_local_repair(before,repair,['/composition'])
        self.assertEqual(after['composition'],{'open_spaces':[], 'groups':[{'id':'keep'}],
                                               'whole_space':'preserve'})

    def test_churning_diagnostics_cannot_bypass_repair_call_limit(self):
        from staged_planner import StagedScenePlanner, StrictModel, PlanningRepairLimit
        from planning_diagnostics import Diagnostics
        class Candidate(StrictModel):
            value:int
        calls=[]
        async def complete(stage,instructions,payload):
            calls.append(stage)
            return {'reason':'Change the candidate.', 'replacements':[
                {'path':'/value','value':len(calls)}]}
        def validate(item):
            report=Diagnostics('churn')
            report.add('changing_'+str(item.value),'Still invalid.',paths=['/value'])
            report.raise_if_any()
        with tempfile.TemporaryDirectory() as folder:
            planner=StagedScenePlanner('unused',folder,'limit',complete=complete,max_repairs=2)
            with self.assertRaises(PlanningRepairLimit) as caught:
                asyncio.run(planner.stage('churn',Candidate,'test',{},validate,
                    lambda _:['/value'],initial_candidate={'value':0}))
            self.assertEqual(len(calls),2)
            self.assertEqual(caught.exception.measurements['issues'][0]['code'],'changing_2')

    def test_side_window_is_aperture_but_window_furniture_is_not(self):
        shell=SimpleNamespace(id='shell',collision_role='shell',parent=None)
        def item(id,semantic,category):
            return SimpleNamespace(id=id,semantic=semantic,category=category,collision_role='solid',
                                   parent='shell',mount='origin',surface_contact=None)
        outline=SimpleNamespace(objects=[shell,
            item('side_window','Tall side window with an integral sheer linen curtain','architecture'),
            item('north_window_seat','Seat below the window','furniture'),
            item('window_display_shelf','Window display shelf','architecture')])
        self.assertEqual(_fitted_window_component_ids(outline),{'side_window'})
        self.assertEqual(set(_unattached_shell_children(outline)),{'north_window_seat','window_display_shelf'})

    def test_extent_names_axis_and_parent_local_correction(self):
        report=audit_parametric_scene(placement_case(),check_spatial=False)
        issue=next(i for i in report['findings'] if i.get('code')=='scene_extent')
        measured=issue['measured']
        self.assertEqual(measured['violations'][0]['axis'],'z')
        self.assertAlmostEqual(measured['violations'][0]['excess_m'],.1)
        self.assertEqual(measured['parent_world_origin_m'],[0,0,-.1])
        self.assertEqual(measured['suggested_local_translation_m'],[0,0,.1])
        self.assertEqual(issue['expected']['occupied_floor_z_m'],0)

    def test_mount_failure_does_not_hide_independent_extent(self):
        report=audit_parametric_scene(placement_case(True),check_spatial=False)
        self.assertEqual(report['status'],'needs_revision')
        self.assertTrue(any(i.get('code')=='mounting_coverage' for i in report['findings']))
        self.assertTrue(any(i.get('code')=='scene_extent' and i['component_ids']==['door']
                            for i in report['findings']))
        self.assertTrue(any(i.get('scenario')=='dependent' for i in report['blocked_checks']))
        self.assertFalse(any(i['component_ids']==['dependent'] for i in report['findings']))

    def test_oversize_is_not_reported_as_solvable_translation(self):
        plan=GeneralPlan(scene_id='oversize',description='Too wide',extent=[4,4,3],
                         objects=[box('wide',[5,1,1])])
        issue=next(i for i in audit_parametric_scene(plan,check_spatial=False)['findings']
                   if i.get('code')=='scene_extent')
        self.assertFalse(issue['measured']['translation_feasible'])
        self.assertIsNone(issue['measured']['suggested_local_translation_m'])

    def test_rotated_parent_correction_uses_parent_axes(self):
        plan=placement_case()
        plan.objects[0].rotation_deg=90
        plan.objects[1].position=[2.2,0,.1]
        issue=next(i for i in audit_parametric_scene(plan,check_spatial=False)['findings']
                   if i.get('code')=='scene_extent')
        self.assertEqual(issue['measured']['violations'][0]['axis'],'y')
        self.assertAlmostEqual(issue['measured']['suggested_local_translation_m'][0],-.6)
        self.assertAlmostEqual(issue['measured']['suggested_local_translation_m'][1],0)


if __name__=='__main__':
    unittest.main()
