"""Real mounting and occupied parts must agree with the scene-use contract."""
from pathlib import Path
import sys
import unittest
import asyncio
import json
import tempfile
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from general_scene import GeneralPlan, SceneObject, Part, _planned_envelopes_and_poses, audit_parametric_scene, compile_local_geometry
from scene_spatial import SpatialContract, audit_spatial
from surface_mounting import SurfaceContact
from planning_diagnostics import PlanningValidationError


class MountingSpatialIntegrityTests(unittest.TestCase):
    def test_spatial_recovery_loads_legacy_frozen_request_contract(self):
        from spatial_recovery import load_recovery_request_contract
        with tempfile.TemporaryDirectory() as folder:
            runtime=Path(folder)
            (runtime/'frozen_request_contract.json').write_text(json.dumps({
                'asset_boundary':'scene','quality_target':'realistic'}),encoding='utf-8')
            contract=load_recovery_request_contract(runtime)
            self.assertEqual(contract.asset_boundary,'scene')
            self.assertEqual(contract.quality_target,'realistic')

    def test_spatial_recovery_reuses_saved_recipe_without_compiled_plan(self):
        from spatial_recovery import load_recovery_recipe
        from test_mounting_interface_revision import fixture
        _,recipes,_=fixture()
        with tempfile.TemporaryDirectory() as folder:
            runtime=Path(folder)
            expected=recipes['host']
            (runtime/'recipe_host_reused.json').write_text(expected.model_dump_json(),encoding='utf-8')
            actual=load_recovery_recipe(runtime,'host')
            self.assertEqual(actual.model_dump(mode='json'),expected.model_dump(mode='json'))

    def test_spatial_recovery_prefers_previously_repaired_recipe(self):
        from spatial_recovery import load_recovery_recipe
        from test_mounting_interface_revision import fixture
        _,recipes,_=fixture()
        with tempfile.TemporaryDirectory() as folder:
            runtime=Path(folder)
            recovered=recipes['host'].model_copy(deep=True)
            recovered.parts[0].id='recovered_part'
            stale=recipes['host'].model_copy(deep=True)
            stale.parts[0].id='stale_part'
            (runtime/'recipe_host_recovered.json').write_text(recovered.model_dump_json(),encoding='utf-8')
            (runtime/'recipe_host_accepted.json').write_text(stale.model_dump_json(),encoding='utf-8')
            actual=load_recovery_recipe(runtime,'host')
            self.assertEqual(actual.parts[0].id,'recovered_part')

    def test_spatial_recovery_reuses_latest_failed_candidate_as_seed(self):
        from spatial_recovery import load_recovery_candidate
        with tempfile.TemporaryDirectory() as folder:
            runtime=Path(folder)
            (runtime/'spatial_reconstruction_candidate_failure_0.json').write_text(json.dumps({
                'candidate':{'id':'older'},'error':'older failed state'}),encoding='utf-8')
            (runtime/'spatial_reconstruction_candidate_failure_1.json').write_text(json.dumps({
                'error':'newer failure without a candidate'}),encoding='utf-8')
            candidate,source,unbuilt=load_recovery_candidate(runtime)
            self.assertEqual(candidate,{'id':'older'})
            self.assertTrue(source.endswith('spatial_reconstruction_candidate_failure_0.json'))
            self.assertTrue(unbuilt)

    def test_spatial_recovery_loads_design_from_same_brief_asset_source(self):
        from spatial_recovery import load_recovery_design
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);runtime=root/'current'/'agent_runtime';runtime.mkdir(parents=True)
            source=root/'prepared';source_runtime=source/'agent_runtime';source_runtime.mkdir(parents=True)
            brief='A student room with a tatami platform.'
            (runtime.parent/'run_origin.json').write_text(json.dumps({
                'brief':brief,'prepared_assets_from':str(source)}),encoding='utf-8')
            (source/'run_origin.json').write_text(json.dumps({'brief':brief}),encoding='utf-8')
            design={'room_identity':'Student room','users_and_activities':'Sleep and study.',
                'spatial_composition':'Tatami and desk form separate groups.',
                'focal_hierarchy':'Tatami first, desk second.','material_palette':['wood','rush'],
                'lighting_atmosphere':'Soft daylight and task lighting.',
                'choices':[{'id':'tatami','layer':'furniture','activity':'sleep',
                    'selection':'Low tatami platform','quality_reason':'Provides a distinct sleep surface.'}]}
            (source_runtime/'scene_design_accepted.json').write_text(json.dumps(design),encoding='utf-8')
            actual,path=load_recovery_design(runtime,brief=brief)
            self.assertEqual(actual.room_identity,'Student room')
            self.assertEqual(Path(path),source_runtime/'scene_design_accepted.json')

    def test_spatial_recovery_loads_spatial_contract_from_prior_scene(self):
        from scene_spatial import SpatialContract
        from spatial_recovery import load_recovery_spatial_contract
        contract=SpatialContract.model_validate({
            'zones':[{'id':'room','purpose':'room','center':[0,0],'size':[4,4]}],
            'openings':[{'id':'entry','kind':'entrance','wall':'y_min','offset':0,
                'width':1,'height':2,'approach_depth':0.5,'purpose':'clear entry'}]})
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);runtime=root/'agent_runtime';runtime.mkdir()
            (root/'general_plan.json').write_text(json.dumps({
                'spatial':contract.model_dump(mode='json')}),encoding='utf-8')
            actual,source=load_recovery_spatial_contract(runtime)
            self.assertEqual(actual.model_dump(mode='json'),contract.model_dump(mode='json'))
            self.assertEqual(Path(source),root/'general_plan.json')

    def test_spatial_recovery_promotes_wall_mounted_root_and_localizes_pose(self):
        from spatial_recovery import localize_reparented_placements,revise_recovery_mounting_contacts
        from test_mounting_interface_revision import fixture
        outline,recipes,layout=fixture()
        outline.objects[0].collision_role='shell'
        outline.objects[1].parent=None
        outline.objects[1].surface_contact=None
        contact=SurfaceContact(parent_part='top',parent_face='x_max',child_face='x_min')
        revised,new_parents=revise_recovery_mounting_contacts(outline,recipes,{'fixture':contact})
        self.assertEqual(revised.objects[1].parent,'host')
        self.assertEqual(new_parents,{'fixture':'host'})
        placements={item.id:item for item in layout.placements}
        placements['host'].position=(1,2,0);placements['host'].yaw_deg=90
        placements['fixture'].position=(1,3,0);placements['fixture'].yaw_deg=100
        localize_reparented_placements(revised,layout,new_parents)
        for value,expected in zip(placements['fixture'].position,(1,0,0)):
            self.assertAlmostEqual(value,expected)
        self.assertAlmostEqual(placements['fixture'].yaw_deg,10)

    def test_room_facing_wall_faces_follow_measured_bounds_not_compass_names(self):
        from spatial_recovery import derive_room_facing_wall_faces
        measured=derive_room_facing_wall_faces({'room_shell':{
            'floor_slab':{'bounds':[[-3.8,-3.3,0],[3.8,3.3,.2]]},
            'wall_south_east':{'bounds':[[-3.8,3.1,.2],[-2.03,3.3,3.4]]},
            'wall_north':{'bounds':[[-3.8,-3.3,.2],[3.8,-3.1,3.4]]},
            'wall_east':{'bounds':[[-3.8,-3.1,.2],[-3.6,3.1,3.4]]},
            'wall_west':{'bounds':[[3.6,-3.1,.2],[3.8,3.1,3.4]]},
        }})['room_shell']
        self.assertEqual(measured['wall_south_east']['interior_face'],'y_min')
        self.assertEqual(measured['wall_north']['interior_face'],'y_max')
        self.assertEqual(measured['wall_east']['interior_face'],'x_max')
        self.assertEqual(measured['wall_west']['interior_face'],'x_min')

    def test_fitted_window_uses_aperture_audit_instead_of_single_wall_face(self):
        from types import SimpleNamespace
        from staged_planner import (validate_wall_attachment_intents,_fitted_window_component_ids,
                                    _unattached_shell_children)
        shell=SimpleNamespace(id='room_shell',semantic='architectural room shell',
            collision_role='shell',parent=None,
            internal_parts=[SimpleNamespace(id='window_sill',semantic='wall sill')])
        window=SimpleNamespace(id='window_assembly',semantic='West-wall window assembly',
            collision_role='solid',parent='room_shell',mount='origin',surface_contact=SurfaceContact(parent_part='window_sill',
                parent_face='z_max',child_face='z_min'))
        outline=SimpleNamespace(objects=[shell,window])
        fitted=_fitted_window_component_ids(outline)
        self.assertEqual(fitted,{'window_assembly'})
        self.assertIs(validate_wall_attachment_intents(outline),outline)
        window.id='window_unit'
        window.semantic='Fitted two-pane timber casement window with a clear glazed opening'
        shell.semantic='Room shell with a window opening and room-facing wall panels'
        fitted=_fitted_window_component_ids(outline)
        self.assertEqual(fitted,{'window_unit'})
        self.assertEqual(_unattached_shell_children(outline),[])

    def test_spatial_recovery_rejects_wall_contact_for_fitted_window(self):
        from spatial_recovery import revise_recovery_mounting_contacts
        from test_mounting_interface_revision import fixture
        outline,recipes,_=fixture()
        contact=SurfaceContact(parent_part='top',parent_face='x_max',child_face='x_min')
        with self.assertRaisesRegex(ValueError,'verified against opening geometry'):
            revise_recovery_mounting_contacts(outline,recipes,{'fixture':contact},
                aperture_fitted_ids={'fixture'})

    def test_spatial_recovery_collects_all_invalid_mounts_for_one_repair(self):
        from spatial_recovery import revise_recovery_mounting_contacts
        from test_mounting_interface_revision import fixture
        outline,recipes,_=fixture()
        contacts={
            'fixture':SurfaceContact(parent_part='top',parent_face='x_max',child_face='x_min'),
            'host':SurfaceContact(parent_part='missing_wall',parent_face='y_min',child_face='y_max'),
        }
        _,_,report=revise_recovery_mounting_contacts(outline,recipes,contacts,
            aperture_fitted_ids={'fixture'},collect_errors=True)
        self.assertEqual({issue['code'] for issue in report['issues']},
            {'aperture_fixture_contact','mounting_host'})
        self.assertEqual({issue['paths'][0] for issue in report['issues']},
            {'/mounting_contacts/fixture','/mounting_contacts/host'})

    def test_recovery_report_merges_semantic_and_coarse_findings(self):
        from planning_diagnostics import combine_reports
        report=combine_reports('spatial_recovery',
            {'issues':[{'code':'wall_attachment','evidence':'fixture is not attached',
                'paths':['/mounting_contacts/fireplace'],'component_ids':['fireplace']}]},
            {'issues':[{'code':'route_extent','evidence':'entrance route is too wide',
                'paths':['/spatial/routes/0/width'],'component_ids':[]}],
             'numerical_solver':{'status':'infeasible_current_choices',
                'orientation_proposals':{'chair':{'to_world_deg':90}}}})
        self.assertEqual([item['code'] for item in report['issues']],
            ['wall_attachment','route_extent'])
        self.assertEqual(report['status'],'needs_revision')
        self.assertEqual(report['numerical_solver']['status'],'infeasible_current_choices')

    def test_wall_mounting_contact_must_use_measured_room_facing_face(self):
        from types import SimpleNamespace
        from staged_planner import validate_wall_attachment_intents
        shell=SimpleNamespace(id='room_shell',semantic='architectural room shell',
            collision_role='shell',internal_parts=[SimpleNamespace(id='wall_south_east',
                semantic='South wall segment')])
        mirror=SimpleNamespace(id='entry_mirror',semantic='Wall-mounted mirror on the south wall',parent='room_shell',
            surface_contact=SurfaceContact(parent_part='wall_south_east',
                parent_face='y_max',child_face='y_min'))
        outline=SimpleNamespace(objects=[shell,mirror])
        measured={'room_shell':{'wall_south_east':{
            'bounds_m':[[-3.8,3.1,.2],[-2.03,3.3,3.4]],'interior_face':'y_min'}}}
        with self.assertRaises(PlanningValidationError) as caught:
            validate_wall_attachment_intents(outline,room_facing_wall_faces=measured)
        self.assertEqual(caught.exception.measurements['issues'][0]['code'],
            'wall_attachment_exterior_face')
        mirror.surface_contact.parent_face='y_min'
        mirror.surface_contact.child_face='y_max'
        self.assertIs(validate_wall_attachment_intents(outline,
            room_facing_wall_faces=measured),outline)

    def test_spatial_recovery_layout_projects_route_data_into_spatial_contract(self):
        from spatial_recovery import RecoveryLayout
        self.assertEqual(set(RecoveryLayout.model_json_schema()['properties']),{'placements'})
        layout=RecoveryLayout.model_validate({'placements':[{'id':'chair','position':[0,0,0]}],
            'route_points':{'route_entry_to_chair':[['access_entry','access_chair']]},
            'access_centers':{'access_chair':'access_chair'}})
        self.assertEqual([item.id for item in layout.placements],['chair'])

    def test_planning_escalation_keeps_complete_evidence_and_unrelated_recipes(self):
        from test_mounting_interface_revision import fixture
        from run_general_scene import planning_feedback
        outline,recipes,_=fixture()
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);runtime=root/'agent_runtime';runtime.mkdir()
            (runtime/'frozen_decomposition.json').write_text(json.dumps({'plan':outline.model_dump(mode='json')}),encoding='utf-8')
            for id,recipe in recipes.items():(runtime/f'recipe_{id}_accepted.json').write_text(recipe.model_dump_json(),encoding='utf-8')
            history=[{'stage':'recipe_'+id,'status':'validated'} for id in recipes]
            history.append({'stage':'layout','status':'failed'})
            (runtime/'stage_events.json').write_text(json.dumps(history),encoding='utf-8')
            issues=[{'code':'layout_relation','component_ids':['fixture'],'measured':{'repair_target':'decomposition'},
                'evidence':'Fixed geometry cannot clear the aperture','paths':[],'scenario':'baseline'}]
            (runtime/'layout_failure_0.json').write_text(json.dumps({'candidate':{'kept':'Agent layout'},
                'measurements':{'issues':issues}}),encoding='utf-8')
            feedback=planning_feedback(root)
            self.assertEqual([row['id'] for row in feedback['validated_parametric_specs']],['host'])
            self.assertEqual(feedback['parametric_recipes_requiring_revision'],['fixture'])
            compact=feedback['detailed_planning_failure']['issues'][0]
            self.assertEqual(compact['component_ids'],['fixture'])
            self.assertEqual(compact['repair_target'],'decomposition')
            self.assertEqual(compact['scenarios'],['baseline'])

    def test_planning_feedback_merges_repeated_edit_scenario_findings(self):
        from run_general_scene import compact_planning_failures
        measured={'repair_target':'layout','parent_translation_bounds':[[0,1],[0,1]]}
        report={'candidate':{'placements':[{'id':'fixture','position':[0,0,0]}]},
            'measurements':{'stage':'layout','status':'needs_revision','coverage':'all checks',
                'issues':[{'code':'mounting_extent_conflict','component_ids':['fixture','host'],
                    'evidence':'No common supported translation at this yaw','constraint_id':'mount',
                    'scenario':scenario,'paths':['/placements/0/position'],'measured':measured}
                    for scenario in ('baseline','width_edit','height_edit')]+
                    [{'code':'layout_relation','component_ids':['chair','desk'],'evidence':'Actual occupied parts overlap',
                      'constraint_id':None,'scenario':'baseline','paths':[],'measured':{}}],
                'blocked_checks':[{'check':'visual','reason':'blocked'}]}}
        result=compact_planning_failures(report)
        self.assertEqual(len(result['issues']),2)
        mounting=next(x for x in result['issues'] if x['code']=='mounting_extent_conflict')
        self.assertEqual(mounting['scenarios'],['baseline','width_edit','height_edit'])
        self.assertEqual(len(mounting['observations']),1)
        self.assertEqual(result['candidate'],report['candidate'])
        self.assertTrue(result['duplicate_scenario_diagnostics_merged'])

    def test_planning_feedback_preserves_numeric_measurements(self):
        from run_general_scene import compact_planning_failures
        report={'candidate':{},'measurements':{'stage':'layout','status':'needs_revision',
            'issues':[{'code':'clearance','evidence':'Gap is below the requested minimum.',
                'paths':['/placements/0/position'],'scenario':'baseline','measured':0.12,
                'expected':0.3}]}}
        result=compact_planning_failures(report)
        self.assertEqual(result['issues'][0]['repair_target'],None)
        self.assertEqual(result['issues'][0]['observations'][0]['measured'],0.12)

    def test_planning_feedback_does_not_fail_on_numeric_layout_measurement(self):
        from run_general_scene import planning_feedback
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);runtime=root/'agent_runtime';runtime.mkdir()
            (runtime/'stage_events.json').write_text(json.dumps([
                {'stage':'layout','status':'failed','attempt':0}]),encoding='utf-8')
            (runtime/'layout_failure_0.json').write_text(json.dumps({'candidate':{},
                'measurements':{'stage':'layout','status':'needs_revision','issues':[
                    {'code':'clearance','component_ids':['desk'],'measured':0.12,
                     'expected':0.3,'paths':['/placements/0/position']}],
                    'blocked_checks':[]}}),encoding='utf-8')
            feedback=planning_feedback(root)
            self.assertEqual(feedback['detailed_planning_failure']['issues'][0]['observations'][0]['measured'],0.12)

    def test_runner_does_not_spend_another_replan_after_internal_exception(self):
        from types import SimpleNamespace
        from run_general_scene import run
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);calls=[]
            args=SimpleNamespace(out=root/'run',planner='llm',plan_only=False,resume_runtime=None,
                prepared_assets_from=None,initial_feedback=None,replan_spatial=False)
            def fail_in_pipeline(attempt):
                calls.append(attempt.out)
                runtime=attempt.out/'agent_runtime';runtime.mkdir(parents=True)
                (runtime/'frozen_request_contract.json').write_text(json.dumps({
                    'asset_boundary':'scene','quality_target':'realistic'}),encoding='utf-8')
                return {'status':'rejected','accepted':False,'error_type':'AttributeError',
                    'error':"'FunctionalBindings' object has no attribute 'objects'"}
            result=run(args,attempt_runner=fail_in_pipeline)
            self.assertEqual(len(calls),1)
            self.assertEqual(result['stop_reason'],'internal_pipeline_error')

    def test_runner_does_not_redo_decomposition_after_exhausted_access_face_stage(self):
        from types import SimpleNamespace
        from run_general_scene import run
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);calls=[]
            args=SimpleNamespace(out=root/'run',planner='llm',plan_only=False,resume_runtime=None,
                prepared_assets_from=None,initial_feedback=None,replan_spatial=False)
            def fail_access_faces(attempt):
                calls.append(attempt.out)
                runtime=attempt.out/'agent_runtime';runtime.mkdir(parents=True)
                (runtime/'frozen_request_contract.json').write_text(json.dumps({
                    'asset_boundary':'scene','quality_target':'realistic'}),encoding='utf-8')
                (runtime/'stage_events.json').write_text(json.dumps([
                    {'stage':'access_faces','status':'failed','attempt':0,'issue_count':1},
                    {'stage':'access_faces','status':'stopped','attempts':2}]),encoding='utf-8')
                (runtime/'access_faces_failure_0.json').write_text(json.dumps({
                    'candidate':None,'measurements':{'stage':'access_faces','status':'needs_revision',
                    'issues':[{'id':'extra-face','code':'extra_access_side','evidence':'Remove extra key.',
                        'paths':['/allowed_sides/study_desk'],'component_ids':['study_desk']}],
                    'blocked_checks':[]}}),encoding='utf-8')
                return {'status':'rejected','accepted':False,'error_type':'ValueError',
                    'error':'access_faces stopped after repeated diagnostic state'}
            result=run(args,attempt_runner=fail_access_faces)
            self.assertEqual(len(calls),1)
            self.assertEqual(len(result['generation_rounds']),1)
            self.assertEqual(result['stop_reason'],'access_face_mapping_failure_requires_local_agent_fix')
            self.assertEqual(result['detailed_planning_failure']['stage'],'access_faces')

    def test_resume_restores_agent_selected_contacts_and_revalidates_layout(self):
        from staged_planner import StagedScenePlanner,replacement_paths
        from test_mounting_interface_revision import fixture
        outline,recipes,coarse=fixture();revised=outline.model_copy(deep=True)
        revised.objects[1].surface_contact.child_part='pad'
        async def forbidden(*args):raise AssertionError('Restoring a valid Agent contact must not ask again')
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);runtime=root/'agent_runtime';runtime.mkdir()
            (root/'run_origin.json').write_text(json.dumps({'brief':'A supported assembly'}),encoding='utf-8')
            (runtime/'frozen_decomposition.json').write_text(json.dumps({'plan':outline.model_dump(mode='json')}),encoding='utf-8')
            (runtime/'current_mounting_outline.json').write_text(revised.model_dump_json(),encoding='utf-8')
            (runtime/'layout_failure_0.json').write_text(json.dumps({'candidate':coarse.model_dump(mode='json')}),encoding='utf-8')
            planner=StagedScenePlanner('unused',root/'resumed','probe',complete=forbidden,resume_runtime=runtime)
            plan,result=asyncio.run(planner.finalize_layout(outline,recipes,coarse,None,[],
                {'brief':'A supported assembly','frozen_decomposition':outline.model_dump(mode='json')},replacement_paths))
            self.assertEqual(result.objects[1].surface_contact.child_part,'pad')
            self.assertEqual(plan.components[1].surface_contact.child_part,'pad')

    def test_locked_mount_cannot_clear_aperture_routes_back_to_component_planning(self):
        host=SceneObject(id='host',semantic='Wall pier beside aperture',size=(4,4,3),collision_role='shell',
            access_required=False,parts=[Part(id='pier',primitive='box',size=(1.5,.12,3),center=(-1.25,-1.94,1.5))])
        fixture=SceneObject(id='fixture',semantic='Incorrect closed installation',size=(1.12,.12,2.2),parent='host',
            access_required=False,position=(0,-1.94,0),surface_contact=SurfaceContact(parent_part='pier',
                parent_face='x_max',child_part='left',child_face='x_min'),parts=[
                Part(id='left',primitive='box',size=(.06,.12,2.2),center=(-.53,0,1.1)),
                Part(id='right',primitive='box',size=(.06,.12,2.2),center=(.53,0,1.1)),
                Part(id='panel',primitive='box',size=(1,.04,2.2),center=(0,0,1.1))],
            assembly={'roots':['panel'],'relations':[
                {'kind':'attached_to','part':'left','target':'panel','reason':'joined'},
                {'kind':'attached_to','part':'right','target':'panel','reason':'joined'}]})
        spatial=SpatialContract(zones=[dict(id='zone',purpose='room',center=(0,0),size=(4,4),component_ids=['host','fixture'])],
            openings=[dict(id='entry',kind='entrance',wall='y_min',offset=0,width=1,height=2,
                approach_depth=.6,purpose='clear entry')])
        plan=GeneralPlan(scene_id='blocked_probe',description='A fixed wrong installation',extent=(4,4,3),
            objects=[host,fixture],spatial=spatial)
        report=audit_parametric_scene(plan)
        findings=[f for f in report['findings'] if f.get('repair_target')=='decomposition']
        self.assertEqual(len(findings),1,report['findings'])
        self.assertEqual(findings[0]['component_ids'],['fixture'])
        self.assertIn('full feasible translation region',findings[0]['evidence'])
        self.assertEqual(findings[0]['measured']['feasible_translation_regions_after_clearance'],0)
        self.assertTrue(findings[0]['measured']['scene_feasible_translation_vertices'])

    def test_resume_revalidates_identical_target_when_instruction_prose_changes(self):
        from staged_planner import StagedScenePlanner
        from test_mounting_interface_revision import fixture
        outline,recipes,_=fixture()
        async def forbidden(*args):raise AssertionError('Identical validated geometry must not call the model')
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);runtime=root/'agent_runtime';runtime.mkdir()
            (root/'run_origin.json').write_text(json.dumps({'brief':'A supported assembly'}),encoding='utf-8')
            (runtime/'frozen_decomposition.json').write_text(json.dumps({'plan':outline.model_dump(mode='json')}),encoding='utf-8')
            (runtime/'request_contract_accepted.json').write_text(json.dumps({'asset_boundary':'scene','quality_target':'prototype'}),encoding='utf-8')
            (runtime/'recipe_host_accepted.json').write_text(recipes['host'].model_dump_json(),encoding='utf-8')
            planner=StagedScenePlanner('unused',root/'resumed','probe',complete=forbidden,resume_runtime=runtime)
            result=asyncio.run(planner.component_recipe(outline,outline.objects[0],'A supported assembly'))
            self.assertEqual(result,recipes['host'])
            self.assertEqual(planner.events[-1]['reuse_basis'],'identical_frozen_target_current_validation')
            changed=outline.model_copy(deep=True);changed.objects[0].dimensions=(.9,1,.2)
            with self.assertRaisesRegex(AssertionError,'Identical validated geometry'):
                asyncio.run(planner.component_recipe(changed,changed.objects[0],'A supported assembly'))

    def test_recipe_cache_isolated_from_evidence_edits_and_parameter_changes(self):
        obj=SceneObject(id='cache_probe',semantic='Editable cached geometry',size=('width',.4,.4),
            parts=[Part(id='body',primitive='box',size=('width',.4,.4),center=(0,0,.2))])
        plan=GeneralPlan(scene_id='cache_test',description='Independent reuse',extent=(4,4,3),
            parameters={'width':.4},objects=[obj])
        first={};compile_local_geometry(plan,obj,evidence=first)
        first['parts']['body']['bounds'][0][0]=999
        second={};compile_local_geometry(plan,obj,evidence=second)
        self.assertAlmostEqual(second['parts']['body']['bounds'][0][0],-.2)
        plan.parameters['width']=.8
        third={};compile_local_geometry(plan,obj,evidence=third)
        self.assertAlmostEqual(third['parts']['body']['bounds'][0][0],-.4)

    def test_selected_contact_reports_empty_translation_domain(self):
        host=SceneObject(id='host',semantic='Boundary support',size=(2,2,1),collision_role='shell',
            parts=[Part(id='edge',primitive='box',size=(1,.12,.5),center=(0,.94,.25))])
        child=SceneObject(id='fixture',semantic='Editable mounted fixture',size=(.8,.18,.2),parent='host',
            position=(0,.9,0),surface_contact=SurfaceContact(parent_part='edge',parent_face='z_max',
                child_part='pad',child_face='z_min'),parts=[
                Part(id='body',primitive='box',size=(.8,.18,.16),center=(0,0,.12)),
                Part(id='pad',primitive='box',size=(.8,.04,.04),center=(0,-.03,.02))],
            assembly={'roots':['pad'],'relations':[{'kind':'attached_to','part':'body','target':'pad','reason':'joined'}]})
        plan=GeneralPlan(scene_id='probe',description='A generic mounting conflict',extent=(2,2,2),objects=[host,child])
        with self.assertRaises(PlanningValidationError) as caught:
            _planned_envelopes_and_poses(plan)
        issue=caught.exception.measurements['issues'][0]
        self.assertEqual(issue['code'],'mounting_extent_conflict')
        self.assertEqual(issue['component_ids'],['fixture','host'])
        self.assertEqual(issue['measured']['scene_feasible_translation_vertices'],[])
        self.assertIn('/components/1/surface_contact',issue['paths'])

    def test_parametric_frame_void_does_not_block_aperture_or_route(self):
        frame=SceneObject(id='frame',semantic='An editable open frame',size=(1.3,.1,2.2),
            position=(0,-1.5,0),parts=[
                Part(id='left',primitive='box',size=(.1,.1,2.2),center=(-.6,0,1.1)),
                Part(id='right',primitive='box',size=(.1,.1,2.2),center=(.6,0,1.1)),
                Part(id='header',primitive='box',size=(1.1,.1,.1),center=(0,0,2.15))],
            assembly={'roots':['left'],'relations':[
                {'kind':'attached_to','part':'header','target':'left','reason':'joined'},
                {'kind':'attached_to','part':'right','target':'header','reason':'joined'}]})
        spatial=SpatialContract(zones=[dict(id='zone',purpose='room',center=(0,0),size=(4,4),component_ids=['frame'])],
            openings=[dict(id='entry',kind='entrance',wall='y_min',offset=0,width=1,height=2,
                approach_depth=.8,purpose='entry')],
            access_areas=[dict(id='view',owner_id='frame',center=(0,1),size=(.8,.5),purpose='approach frame')],
            routes=[dict(id='walk',start='entry',end='view',
                points=[(0,-1.6),(0,-.5)],width=.8,height=1.9,purpose='walk through frame')])
        plan=GeneralPlan(scene_id='frame_probe',description='An open framed entry',extent=(4,4,3),objects=[frame],spatial=spatial)
        report=audit_parametric_scene(plan)
        self.assertEqual(report['status'],'passed',report['findings'])
        blocked=spatial.model_copy(deep=True)
        bounds={'frame':[[-.65,-1.55,0],[.65,-1.45,2.2]]}
        part_bounds={'frame':{'leaf':[[-.55,-.05,0],[.55,.05,2]]}}
        report=audit_spatial(blocked,[frame],plan.extent,bounds,{'frame':(0,np.array([0,-1.5,0]))},shell_parts=part_bounds)
        self.assertEqual(report['status'],'needs_revision')
        self.assertTrue(any('leaf' in f['evidence'] for f in report['findings']))

    def test_independent_object_in_frame_void_is_not_solid_intersection(self):
        frame=SceneObject(id='frame',semantic='Independent editable frame',size=(2,1,1),parts=[
            Part(id='left',primitive='box',size=(.1,1,1),center=(-.95,0,.5)),
            Part(id='right',primitive='box',size=(.1,1,1),center=(.95,0,.5)),
            Part(id='top',primitive='box',size=(1.8,1,.1),center=(0,0,.95))],
            assembly={'roots':['left'],'relations':[
                {'kind':'attached_to','part':'top','target':'left','reason':'joined'},
                {'kind':'attached_to','part':'right','target':'top','reason':'joined'}]})
        inset=SceneObject(id='inset',semantic='Independent object inside the opening',size=(.4,.4,.4),
            parts=[Part(id='body',primitive='box',size=(.4,.4,.4),center=(0,0,.2))])
        plan=GeneralPlan(scene_id='nested_probe',description='Separated real members',extent=(4,4,3),objects=[frame,inset])
        report=audit_parametric_scene(plan)
        self.assertEqual(report['status'],'passed',report['findings'])


if __name__=='__main__':unittest.main()
