"""Behavioral regressions for stage isolation and bounded local repair."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'experiments'))
sys.path.insert(0,str(ROOT/'src'))
from staged_planner import (Decomposition, Recipe, Layout, LocalRepair, apply_local_repair,
                            assemble_plan, validate_recipe, StagedScenePlanner,
                            _functional_binding_id_error, _functional_bindings_prompt,
                            _functional_bindings_response_schema, FunctionalBinding, FunctionalBindings,
                            apply_functional_bindings,apply_functional_binding_result,
                            _fitted_window_component_ids,
                            _unattached_shell_children,
                            apply_json_syntax_patch, normalize_local_repair_proposal, InternalPartIntent,
                            validate_architectural_openings, recipe_repair_paths, spatial_conflict_families,
                            repeated_spatial_conflict, support_group_revision_issues,
                            DetailedPlanningRevisionRequired, SpatialChoicesInfeasible,
                            AccessFaces, validate_access_faces, replacement_paths,
                            latest_failed_stage_candidate, local_repair_stage_guidance, DECOMPOSE,
                            project_access_face_ids, CirculationProgram, validate_approach_owners)
from staged_planner import _repair_guidance
from general_planner import PlannerScenePlan,StrictModel,to_general_plan
from general_scene import compile_general


def outline_data():
    return {'scene_id':'display','description':'Two supported components','style':'technical',
        'quality_target':'prototype','extent':[4,4,3],'parameters':{'base_width':1.5},
        'objects':[
            {'id':'base','semantic':'editable base','category':'support','dimensions':['base_width',1,.2],
             'rigidity':'parametric','decomposition_reason':'Base width is independently editable.',
             'required_features':['flat upper support'],'material_requirements':['matte gray'],
             'visual_importance':3,'acquisition':{'strategy':'parametric','reason':'Exact editable rectangular support.'}},
            {'id':'ornament','semantic':'custom curved ornament','category':'ornament','dimensions':[.3,.3,.5],
             'rigidity':'rigid','decomposition_reason':'Keep the organic ornament a separate rigid object.',
             'required_features':['asymmetrical curved shape'],'material_requirements':['porous stone'],
             'visual_importance':5,'parent':'base','mount':'top',
             'acquisition':{'strategy':'auto','reason':'Search candidates then generate if no suitable appearance exists.',
                'retrieval_query':'curved ornament','reference_query':'isolated curved ornament',
                'generation_reason':'Only after candidate QA fails should the custom form be generated.',
                'reference_requirements':['isolated full silhouette']}}],
        'zones':[{'name':'display','purpose':'display supported ornament','component_ids':['base','ornament']}],
        'requirements':[{'requirement':'editable support with ornament','component_ids':['base','ornament'],'verification':'multiview render and edit check'}]}


def recipe_data():
    return {'parts':[{'id':'slab','primitive':'box','size':['w','d','h'],'center':[0,0,'h/2']}]}


def layout_data():
    return {'placements':[{'id':'base','position':[0,0,0],'yaw_deg':0},
                          {'id':'ornament','position':[0,0,0],'yaw_deg':0}]}


class StagedTests(unittest.TestCase):
    def test_circulation_route_starts_inside_door_approach_for_every_wall(self):
        from types import SimpleNamespace
        from scene_spatial import SpatialContract, validate_spatial_contract
        from staged_planner import apply_circulation_program

        obj=SimpleNamespace(id='base',parent=None,support=None,collision_role='solid',access_required=True)
        circulation=SimpleNamespace(approaches=[SimpleNamespace(owner_id='base',reason='reach base',
            size=(1.0,.8),side='front',along_face=0.0,final_route_width=.75)])
        context=SimpleNamespace(scene_use=None)
        for wall in ('x_min','x_max','y_min','y_max'):
            with self.subTest(wall=wall):
                spatial=SpatialContract.model_validate({
                    'zones':[{'id':'zone','purpose':'room','center':[0,0],'size':[4,4],
                              'component_ids':['base']}],
                    'openings':[{'id':'door','kind':'entrance','wall':wall,'offset':0,'width':1,
                                 'height':2.05,'approach_depth':.7,'purpose':'room entrance'}],
                    'access_areas':[],
                    'routes':[{'id':'placeholder','start':'door','end':'old','points':[[0,0],[.1,0]],
                               'width':.4,'purpose':'placeholder'}]})
                result=apply_circulation_program(spatial,circulation,
                    SimpleNamespace(extent=(4.0,4.0,2.7)),context)
                route=result.routes[0]
                axis=0 if wall.startswith('x') else 1
                sign=-1 if wall.endswith('min') else 1
                self.assertAlmostEqual(sign*route.points[0][axis],1.615)
                validate_spatial_contract(result,[obj],[4.0,4.0,2.7])

    def test_multiple_activity_approaches_share_one_entrance_aisle(self):
        from types import SimpleNamespace
        from scene_spatial import SpatialContract, validate_spatial_contract
        from staged_planner import apply_circulation_program
        objects=[SimpleNamespace(id=id,parent=None,support=None,collision_role='solid',access_required=True)
                 for id in ('tatami','desk_chair','wardrobe')]
        spatial=SpatialContract.model_validate({
            'zones':[{'id':'room','purpose':'bedroom activities','center':[0,0],'size':[4.2,4.2],
                      'component_ids':['tatami','desk_chair','wardrobe']}],
            'openings':[{'id':'door','kind':'entrance','wall':'x_min','offset':0,'width':1,
                         'height':2.1,'approach_depth':.7,'purpose':'room entrance'}],
            'relations':[
                {'id':'rest_to_study','kind':'left_of','a':'tatami','b':'desk_chair',
                 'reason':'Keep rest and study as distinct functional groups.'},
                {'id':'storage_to_study','kind':'right_of','a':'wardrobe','b':'desk_chair',
                 'reason':'Keep clothing storage reachable from the shared floor.'}],
            'access_areas':[],'routes':[]})
        approaches=[SimpleNamespace(owner_id=id,reason=f'Use the {id} from its clear working edge.',
            size=(width,.7),side='front',along_face=0.,final_route_width=route_width)
            for id,width,route_width in (('tatami',1.2,.6),('desk_chair',.8,.65),('wardrobe',1.5,.75))]
        circulation=SimpleNamespace(approaches=approaches,shared_aisle_owner_id='tatami',
                                    shared_aisle_width=.7)
        result=apply_circulation_program(spatial,circulation,
            SimpleNamespace(extent=(4.2,4.2,2.8)),SimpleNamespace(scene_use=None))
        self.assertEqual([route.id for route in result.routes],
                         ['main_aisle','reach_desk_chair','reach_wardrobe'])
        self.assertEqual(sum(route.start=='door' or route.end=='door' for route in result.routes),1)
        self.assertEqual(result.routes[0].width,.7)
        self.assertEqual([route.width for route in result.routes[1:]],[.65,.75])
        validate_spatial_contract(result,objects,[4.2,4.2,2.8])

    def test_recovery_batches_duplicated_support_height_before_building_floaters(self):
        from spatial_recovery import validate_support_offsets
        from planning_diagnostics import PlanningValidationError
        outline=Decomposition.model_validate(outline_data())
        layout=Layout.model_validate(layout_data())
        layout.placements[1].position=(0,0,.2)
        with self.assertRaises(PlanningValidationError) as caught:validate_support_offsets(outline,layout)
        self.assertEqual(caught.exception.measurements['issues'][0]['component_ids'],['ornament','base'])
        # Verify the observed failure in the actual compiler, then the contact
        # after an Agent-style local repair, including an editable support.
        recipes={'base':Recipe.model_validate(recipe_data())}
        bad=assemble_plan(outline,recipes,layout,reconstruction_only=True)
        executable,_=to_general_plan(bad,check_layout=False,check_geometry=False)
        with tempfile.NamedTemporaryFile(suffix='.glb') as source:
            mesh={'ornament':{'path':source.name,'bounds':[[-.15,-.15,0],[.15,.15,.5]],'accepted':True}}
            _,_,receipt=compile_general(executable,mesh)
            self.assertAlmostEqual(receipt['ornament']['world_origin'][2],.4)
            layout.placements[1].position=(0,0,0)
            validate_support_offsets(outline,layout)
            good=assemble_plan(outline,recipes,layout,reconstruction_only=True)
            executable,_=to_general_plan(good,check_layout=False,check_geometry=False)
            _,_,receipt=compile_general(executable,mesh)
            self.assertAlmostEqual(receipt['ornament']['world_origin'][2],.2)

    def test_explicit_wall_intent_rejects_floor_contact_and_accepts_a_real_wall_face(self):
        from types import SimpleNamespace
        from planning_diagnostics import PlanningValidationError
        from staged_planner import validate_wall_attachment_intents
        from surface_mounting import SurfaceContact
        shell=SimpleNamespace(id='room_shell',semantic='architectural room shell',
            collision_role='shell',internal_parts=[
            SimpleNamespace(id='north_wall',semantic='North wall panel')])
        fixture=SimpleNamespace(id='fireplace',semantic='North-wall limestone fireplace',
            parent='room_shell',surface_contact=SurfaceContact(parent_part='floor_slab',
                parent_face='z_max',child_part=None,child_face='z_min'))
        outline=SimpleNamespace(objects=[shell,fixture])
        with self.assertRaises(PlanningValidationError) as caught:
            validate_wall_attachment_intents(outline)
        self.assertEqual(caught.exception.measurements['issues'][0]['code'],'wall_attachment_contact')
        fixture.surface_contact=SurfaceContact(parent_part='north_wall',parent_face='y_min',
            child_part=None,child_face='y_max')
        self.assertIs(validate_wall_attachment_intents(outline),outline)
        fixture.semantic='Built-in fireplace surround'
        fixture.surface_contact=None
        with self.assertRaises(PlanningValidationError):
            validate_wall_attachment_intents(outline)

    def test_entry_door_leaf_is_a_shell_aperture_component_not_a_wall_fixture(self):
        from types import SimpleNamespace
        from staged_planner import (_entrance_door_leaf_ids,_unattached_shell_children,
                                    validate_wall_attachment_intents)
        shell=SimpleNamespace(id='room_shell',semantic='architectural shell',collision_role='shell',
            internal_parts=[],parent=None)
        door=SimpleNamespace(id='entry_door',semantic='Flush wood door fitted into the south-wall entry opening',
            parent='room_shell',mount='origin',surface_contact=None)
        outline=SimpleNamespace(objects=[shell,door])
        fitted=_entrance_door_leaf_ids(outline)
        self.assertEqual(fitted,{'entry_door'})
        self.assertEqual(_unattached_shell_children(outline),[])
        self.assertIs(validate_wall_attachment_intents(outline,fitted_component_ids=fitted),outline)
        door.parent=None
        self.assertEqual(_entrance_door_leaf_ids(outline),set())

    def test_built_in_floor_platform_is_not_assumed_to_be_wall_mounted(self):
        from types import SimpleNamespace
        from staged_planner import validate_wall_attachment_intents
        platform=SimpleNamespace(id='tatami_platform',
            semantic='Low built-in timber tatami sleeping platform resting on the floor.',
            parent=None,surface_contact=None)
        outline=SimpleNamespace(objects=[platform])
        self.assertIs(validate_wall_attachment_intents(outline),outline)

    def test_floor_furniture_along_a_wall_is_not_a_mounting_intent(self):
        from types import SimpleNamespace
        from staged_planner import validate_wall_attachment_intents
        desk=SimpleNamespace(id='study_desk',
            semantic='Natural oak study desk standing on four legs along the east wall.',
            parent=None,surface_contact=None,collision_role='solid')
        cabinet=SimpleNamespace(id='storage_cabinet',
            semantic='Low closed cabinet rests against the south wall on its own feet.',
            parent=None,surface_contact=None,collision_role='solid')
        outline=SimpleNamespace(objects=[desk,cabinet])
        self.assertIs(validate_wall_attachment_intents(outline),outline)

    def test_architectural_shell_is_not_a_wall_mounted_fixture(self):
        from types import SimpleNamespace
        from staged_planner import validate_wall_attachment_intents
        shell=SimpleNamespace(id='room_shell',
            semantic='Enclosed near-square north-wall shell with four walls and a real window opening.',
            collision_role='shell',parent=None,surface_contact=None)
        outline=SimpleNamespace(objects=[shell])
        self.assertIs(validate_wall_attachment_intents(outline),outline)

    def test_spatial_recovery_does_not_return_a_candidate_when_coarse_preflight_is_blocked(self):
        from planning_diagnostics import PlanningValidationError
        from spatial_recovery import require_reconstruction_preflight
        passed={'status':'passed','readiness':'ready_for_3d_audit','issues':[]}
        self.assertIs(require_reconstruction_preflight(passed),passed)
        blocked={'status':'needs_revision','readiness':'blocked','blocking_findings':[
            {'constraint_id':'mounting_orientation','evidence':'fireplace is not attached to a wall',
             'component_ids':['fireplace'],'paths':['/placements/2/yaw_deg']},
            {'constraint_id':'facing','evidence':'chair faces away from the shared seating group',
             'component_ids':['chair','sofa'],'paths':['/placements/5/yaw_deg']}],
            'issues':[{'code':'mounting_orientation','evidence':'fireplace is not attached to a wall',
                       'component_ids':['fireplace'],'paths':['/placements/2/yaw_deg']},
                      {'code':'facing','evidence':'chair faces away from the shared seating group',
                       'component_ids':['chair','sofa'],'paths':['/placements/5/yaw_deg']} ]}
        with self.assertRaises(PlanningValidationError) as caught:
            require_reconstruction_preflight(blocked)
        self.assertEqual([issue['code'] for issue in caught.exception.measurements['issues']],
                         ['mounting_orientation','facing'])
        self.assertIn('chair faces away',str(caught.exception))

    def test_spatial_recovery_requires_runtime_and_preserves_candidate_provenance(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            with self.assertRaisesRegex(ValueError,'previous staged runtime'):
                StagedScenePlanner(root/'profile',root/'new','test',replan_spatial=True)
            old=root/'old';old.mkdir()
            raw=outline_data()
            (old/'frozen_decomposition.json').write_text(json.dumps({'plan':raw}),encoding='utf-8')
            planner=StagedScenePlanner(root/'profile',root/'new','test',
                resume_runtime=old,replan_spatial=True)
            recovered=planner.preserved_stage('decomposition')
            self.assertEqual(recovered,raw)
            evidence=json.loads((root/'new/recovery_decomposition_input.json').read_text())
            self.assertEqual(evidence['source'],str(old/'frozen_decomposition.json'))
            self.assertIn('not a fresh prompt',evidence['policy'])
            recovered['objects'][0]['dimensions']=[9,9,9]
            self.assertEqual(json.loads((old/'frozen_decomposition.json').read_text())['plan'],raw)

    def test_spatial_visual_replan_reuses_frozen_contract_only_for_same_brief(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);old=root/'old'/'agent_runtime';old.mkdir(parents=True)
            brief='A near-square student bedroom with a tatami platform.'
            (old.parent/'run_origin.json').write_text(json.dumps({'brief':brief}),encoding='utf-8')
            (old/'frozen_request_contract.json').write_text(json.dumps({
                'asset_boundary':'scene','quality_target':'realistic'}),encoding='utf-8')
            planner=StagedScenePlanner(root/'profile',root/'new','test',resume_runtime=old,
                replan_spatial=True,replan_feedback={'scene_design_revision_required':True})
            contract=planner.preserved_recovery_request_contract(brief)
            self.assertEqual((contract.asset_boundary,contract.quality_target),('scene','realistic'))
            self.assertEqual(planner.events[0]['reuse_basis'],'same_brief_frozen_user_contract')
            with self.assertRaisesRegex(ValueError,'different brief'):
                planner.preserved_recovery_request_contract('A different room.')

    def test_functional_binding_stage_result_is_applied_to_outline(self):
        outline=Decomposition.model_validate(outline_data())
        bindings=FunctionalBindings(bindings=[
            {'id':'base','parent':None,'mount':'origin','support_part':None,
             'grounded':True,'access_required':True,'access_reason':'Operator stands at the front.',
             'attachment_reason':'This root rests directly on the floor.','support_footprint':None},
            {'id':'ornament','parent':'base','mount':'top','support_part':None,
             'grounded':False,'access_required':False,'access_reason':'No independent access is required.',
             'attachment_reason':'The ornament is supported by the base top.','support_footprint':None},
        ])
        result=apply_functional_binding_result(outline,bindings)
        self.assertIsInstance(result,Decomposition)
        self.assertEqual(result.objects[1].parent,'base')
        self.assertFalse(result.objects[1].access_required)
        with self.assertRaisesRegex(TypeError,'expected FunctionalBindings'):
            apply_functional_binding_result(outline,outline)

    def test_decomposition_rejects_a_compound_frame_frozen_as_one_beam(self):
        outline=Decomposition.model_validate(outline_data())
        outline.objects[0].internal_parts=[InternalPartIntent.model_validate({
            'id':'sash_frame','semantic':'Lower sash frame with two vertical stiles and rails',
            'rigidity':'parametric','role':'structure',
            'form':{'kind':'beam','axis':'x','minimum_ratio':3.0}})]
        with self.assertRaisesRegex(ValueError,'frozen form=beam conflicts') as caught:
            outline.validate_decomposition(None)
        self.assertIn('/objects/0/internal_parts/0/form',
                      caught.exception.measurements['issues'][0]['paths'])
        self.assertIn('An internal_part form describes one physical panel or beam',DECOMPOSE)

    def test_decomposition_rejects_a_solid_plinth_frozen_as_a_panel(self):
        outline=Decomposition.model_validate(outline_data())
        outline.objects[0].internal_parts=[InternalPartIntent.model_validate({
            'id':'statue_plinth','semantic':'Low stone-toned plinth for a classical sculpture',
            'rigidity':'parametric','role':'structure',
            'form':{'kind':'panel','axis':'z','minimum_ratio':3.0}})]
        with self.assertRaisesRegex(ValueError,'solid block, plinth, or pedestal') as caught:
            outline.validate_decomposition(None)
        self.assertIn('/objects/0/internal_parts/0/form',
                      caught.exception.measurements['issues'][0]['paths'])

    def test_decomposition_rejects_a_thick_object_misclassified_as_walkable_floor_covering(self):
        outline=Decomposition.model_validate(outline_data())
        outline.objects[0].collision_role='floor_covering'
        outline.objects[0].grounded=True
        with self.assertRaisesRegex(ValueError,'floor_covering thickness must be at most 0.08m'):
            outline.validate_decomposition(None)

    def test_standalone_fitted_window_cannot_be_misclassified_as_room_shell(self):
        outline=Decomposition.model_validate(outline_data())
        outline.objects=outline.objects[:1]
        outline.zones[0].component_ids=['base']
        outline.requirements[0].component_ids=['base']
        window=outline.objects[0]
        window.category='architecture'
        window.semantic='Tall cased sash window with glazing and a linen blind.'
        window.dimensions=(1.8,.18,1.85)
        window.collision_role='shell'
        with self.assertRaisesRegex(ValueError,'standalone fitted window.*solid fixture') as caught:
            outline.validate_decomposition(None)
        self.assertIn('/objects/0/collision_role',
                      caught.exception.measurements['issues'][0]['paths'])
        self.assertIn('matching window opening',str(caught.exception))
        window.collision_role='solid'
        outline.validate_decomposition(None)

    def test_window_planning_prompts_assign_fitted_fixtures_to_the_opening(self):
        from staged_planner import SPATIAL
        self.assertIn('collision_role=solid',DECOMPOSE)
        self.assertIn('fitted_component_ids',DECOMPOSE)
        self.assertIn('exact object ID',SPATIAL)
        self.assertIn('never repair a world-placement mismatch',SPATIAL)

    def test_shell_opening_diagnostic_targets_only_the_crossing_recipe_part(self):
        from types import SimpleNamespace
        from planning_diagnostics import PlanningValidationError,repair_report
        component=SimpleNamespace(id='room_shell',semantic='room shell',dimensions=(4,4,3))
        recipe=Recipe.model_validate({'parts':[{'id':'threshold_sill','primitive':'box',
            'size':[1,.04,.02],'center':[0,-1.98,.01]}]})
        opening=SimpleNamespace(id='south_entry',kind='entrance',wall='y_min',offset=0,width=1,
            height=2.1,sill_height=0)
        placement=SimpleNamespace(position=(0,0,0),yaw_deg=0)
        with self.assertRaises(PlanningValidationError) as caught:
            validate_architectural_openings(component,recipe,(4,4,3),{},[opening],placement)
        issue=caught.exception.measurements['issues'][0]
        report=repair_report(caught.exception.measurements,recipe_repair_paths(recipe.model_dump()))
        self.assertEqual(issue['code'],'opening_intersection')
        self.assertEqual(issue['component_ids'],['room_shell'])
        self.assertEqual(issue['measured']['vertical_overlap_m'],.02)
        self.assertEqual(issue['measured']['lateral_overlap_m'],1.0)
        self.assertEqual(issue['paths'],['/parts/0/size','/parts/0/center','/parts/0/rotation'])
        self.assertEqual(report['issues'][0]['allowed_repair_paths'],issue['paths'])

    def test_window_fixtures_may_occupy_their_aperture_but_wall_members_may_not(self):
        from types import SimpleNamespace
        from planning_diagnostics import PlanningValidationError
        from staged_planner import _fitted_window_feature
        intent=SimpleNamespace(id='sash_glazing',semantic='Clear glazed sash panel')
        opening=SimpleNamespace(id='east_window',kind='window',wall='x_max',offset=0,width=1.4,
            height=1.8,sill_height=.6)
        self.assertTrue(_fitted_window_feature(intent,[1.98,-.6,.7],[2.0,.6,2.3],opening,0,2,.12))
        self.assertFalse(_fitted_window_feature(intent,[1.39,-.6,.7],[1.41,.6,2.3],opening,0,2,.12))
        component=SimpleNamespace(id='room_shell',semantic='room shell',dimensions=(4,4,3),
            internal_parts=[
                SimpleNamespace(id='sash_glazing',semantic='Clear glazed sash panel'),
                SimpleNamespace(id='east_wall',semantic='East perimeter wall panel')])
        recipe=Recipe.model_validate({'parts':[
            {'id':'sash_glazing','primitive':'box','semantic':'Clear glazed sash panel',
             'size':[.012,1.2,1.6],'center':[1.99,0,1.5]},
            {'id':'east_wall','primitive':'box','semantic':'East perimeter wall panel',
             'size':[.12,4,3],'center':[1.94,0,1.5]}],
            'assembly':{'roots':['east_wall'],'relations':[{'kind':'attached_to',
                'part':'sash_glazing','target':'east_wall','max_penetration':.02,
                'reason':'The glazing is captured in the wall reveal.'}]}})
        placement=SimpleNamespace(position=(0,0,0),yaw_deg=0)
        with self.assertRaises(PlanningValidationError) as caught:
            validate_architectural_openings(component,recipe,(4,4,3),{},[opening],placement)
        self.assertEqual(caught.exception.measurements['issues'][0]['paths'],
            ['/parts/1/size','/parts/1/center','/parts/1/rotation'])

    def test_parser_guided_syntax_patch_changes_only_a_bounded_local_span(self):
        raw='{"longname";"sofa"}'
        error=None
        try:json.loads(raw)
        except json.JSONDecodeError as caught:error=caught
        from staged_planner import JSONSyntaxPatch
        fixed=apply_json_syntax_patch(raw,JSONSyntaxPatch(start=11,end=12,replacement=':',
            reason='Restore the missing key-value delimiter.'),error)
        self.assertEqual(json.loads(fixed),{'longname':'sofa'})
        with self.assertRaisesRegex(ValueError,'within 8 characters'):
            apply_json_syntax_patch(raw,JSONSyntaxPatch(start=0,end=1,replacement='{',
                reason='Keep the existing object opening.'),error)

    def test_ask_uses_a_small_parser_guided_patch_after_repeated_invalid_json(self):
        import asyncio
        calls=[]
        async def complete(stage,instructions,payload):
            calls.append(stage)
            if stage=='syntax_probe_syntax_repair':
                return {'start':7,'end':8,'replacement':':','reason':'Restore the key-value delimiter.'}
            return '{"name";"sofa"}'
        with tempfile.TemporaryDirectory() as directory:
            planner=StagedScenePlanner('unused',directory,'test',complete=complete)
            result=asyncio.run(planner.ask('syntax_probe','Return JSON.',{}))
            attempts=json.loads((Path(directory)/'syntax_probe_response_attempts.json').read_text())
            patch=json.loads((Path(directory)/'syntax_probe_syntax_repair_attempts.json').read_text())
            self.assertEqual(Path(directory,'syntax_probe_syntax_repair_request.json').exists(),True)
        self.assertEqual(result,{'name':'sofa'})
        self.assertEqual(calls,['syntax_probe','syntax_probe','syntax_probe_syntax_repair'])
        self.assertEqual([item['status'] for item in attempts],['invalid_json','syntax_repaired'])
        self.assertEqual(patch[0]['status'],'repaired')

    def test_functional_binding_diagnostic_identifies_nested_ids_and_order_errors(self):
        expected=['room_shell','bookcase','sofa']
        self.assertIsNone(_functional_binding_id_error(expected,expected))
        self.assertIsNone(_functional_binding_id_error(expected,['sofa','room_shell','bookcase']))
        message=_functional_binding_id_error(expected,
            ['room_shell','bookcase','sofa','bookcase_shelf_1','bookcase_shelf_1'])
        self.assertIn('unexpected=["bookcase_shelf_1"]',message)
        self.assertIn('duplicates=["bookcase_shelf_1"]',message)
        self.assertIn('internal_parts are recipe members',message)
        message=_functional_binding_id_error(expected,['sofa','bookcase'])
        self.assertIn('missing=["room_shell"]',message)
        self.assertIn('first_order_mismatch=0',message)

    def test_functional_binding_prompt_formats_count_without_interpolating_schema_examples(self):
        prompt=_functional_bindings_prompt(['room_shell','sofa'])
        self.assertIn('Return exactly 2 bindings',prompt)
        self.assertIn('component_ids list ["room_shell", "sofa"]',prompt)
        self.assertIn('surface_contact={parent_part:<frozen wall/ceiling part ID>',prompt)
        self.assertIn('child_face:<x/y/z_min/max>}',prompt)
        self.assertIn('compass placement describes location only',prompt)
        self.assertIn('the child\'s real contact footprint',prompt)
        self.assertIn('a known impossible support group for layout',prompt)
        self.assertIn('MUST be parented to the actual architectural shell',prompt)
        self.assertIn('character-for-character',prompt)
        schema=_functional_bindings_response_schema(['room_shell','sofa'])
        self.assertEqual(schema['$defs']['FunctionalBinding']['properties']['id']['enum'],['room_shell','sofa'])
        self.assertEqual(schema['properties']['bindings']['minItems'],2)
        self.assertEqual(schema['properties']['bindings']['maxItems'],2)

    def test_functional_binding_preflight_reports_independent_failures_together(self):
        from planning_diagnostics import PlanningValidationError
        from surface_mounting import SurfaceContact

        outline=Decomposition.model_validate(outline_data())
        shell=outline.objects[0]
        shell.semantic='Architectural room shell'
        shell.collision_role='shell'
        shell.dimensions=(4,4,3)
        bindings=FunctionalBindings.model_validate({'bindings':[
            {'id':'base','parent':None,'mount':'origin','support_part':None,'surface_contact':None,
             'grounded':True,'access_required':True,'access_reason':'No independent access is needed.',
             'attachment_reason':'The shell rests on the floor slab.','support_footprint':None},
            {'id':'ornament','parent':'base','mount':'bottom','support_part':None,
             'surface_contact':SurfaceContact(parent_part='west_wall',parent_face='x_min',
                 child_part=None,child_face='y_max'),
             'grounded':False,'access_required':False,'access_reason':'Reached from the room floor.',
             'attachment_reason':'Fixed to the west wall at the stated face.','support_footprint':None},
        ]})
        with self.assertRaises(PlanningValidationError) as caught:
            apply_functional_bindings(outline,bindings)
        issues=caught.exception.measurements['issues']
        self.assertTrue(any(issue['code']=='shell_access' for issue in issues))
        self.assertTrue(any(issue['code']=='decomposition.mounting_contract' for issue in issues))
        mount=next(issue for issue in issues if issue['code']=='decomposition.mounting_contract')
        self.assertIn('/bindings/1',mount['paths'])

    def test_compass_named_window_is_audited_by_its_opening_not_generic_wall_contact(self):
        from types import SimpleNamespace
        shell=SimpleNamespace(id='room_shell',collision_role='shell',parent=None)
        window=SimpleNamespace(id='north_window',semantic='Timber casement window fitted in the north wall',
            collision_role='solid',parent='room_shell',mount='origin',surface_contact=None)
        shutter=SimpleNamespace(id='window_shutter',semantic='Louvered shutter fitted in the window reveal',
            collision_role='solid',parent='room_shell',mount='origin',surface_contact=None)
        outline=SimpleNamespace(objects=[shell,window,shutter])
        self.assertEqual(_fitted_window_component_ids(outline),{'north_window','window_shutter'})
        self.assertEqual(_unattached_shell_children(outline),[])

    def test_unbound_design_repair_distinguishes_removal_from_exact_choice_mapping(self):
        from staged_planner import _repair_guidance
        guidance=_repair_guidance('decomposition',
            'fireplace: object has no accepted design purpose. Accepted choice IDs: ["green_reading_chair"].')
        self.assertIn('remove it using its exact removable_paths entry',guidance)
        self.assertIn('scene_design_choice_ids',guidance)
        self.assertIn('do not restore preliminary-design/category aliases',guidance)

    def test_recipe_receives_world_floor_as_a_component_local_elevation(self):
        import asyncio
        async def complete(stage,instructions,payload):
            self.assertEqual(stage,'recipe_base_0')
            frame=payload['context']['modeling_frame']
            self.assertEqual(frame['world_origin_m'],[0,0,-.15])
            self.assertEqual(frame['world_floor_z_in_component_frame_m'],.15)
            self.assertEqual(frame['local_envelope_bounds_m'][0][2],0)
            return recipe_data()
        with tempfile.TemporaryDirectory() as directory:
            planner=StagedScenePlanner('unused',directory,'frame',complete=complete)
            placed=layout_data();placed['placements'][0]['position']=[0,0,-.15]
            planner.current_coarse=Layout.model_validate(placed)
            outline=Decomposition.model_validate(outline_data())
            result=asyncio.run(planner.component_recipe(outline,outline.objects[0],'Editable support'))
            self.assertEqual(result.parts[0].center,(0,0,'h/2'))

    def test_unused_inferred_quantities_are_provenance_not_fake_edit_controls(self):
        from staged_planner import prune_unwired_proposals
        data=outline_data();data['parameters']['inferred_route_width']=.9
        outline=Decomposition.model_validate(data)
        clean,removed=prune_unwired_proposals(outline,{'base':Recipe.model_validate(recipe_data())},Layout.model_validate(layout_data()))
        self.assertEqual(removed,{'inferred_route_width':.9})
        self.assertEqual(clean.parameters,{'base_width':1.5})
        self.assertEqual(outline.parameters['inferred_route_width'],.9)
        self.assertIn('inferred_route_width',clean.assumptions[-1])

    def test_requested_but_unwired_edits_remain_for_normal_failure_gate(self):
        from staged_planner import prune_unwired_proposals
        data=outline_data();data['parameters']['unwired_edit']=1
        data['edit_checks']=[{'parameter':'unwired_edit','delta':.1,'intent':'Explicit requested control'}]
        outline=Decomposition.model_validate(data)
        clean,removed=prune_unwired_proposals(outline,{'base':Recipe.model_validate(recipe_data())},Layout.model_validate(layout_data()))
        self.assertFalse(removed)
        self.assertIn('unwired_edit',clean.parameters)
        with self.assertRaisesRegex(ValueError,'Unused editable parameter'):
            assemble_plan(clean,{'base':Recipe.model_validate(recipe_data())},Layout.model_validate(layout_data()))

    def test_recipe_repair_guidance_targets_missing_frozen_clearance_relation(self):
        from staged_planner import _repair_guidance
        guidance=_repair_guidance('recipe_display_structure',
            'Frozen internal clearance missing/weakened: shelf_level_1->shelf_level_2 >= 0.3m')
        self.assertIn('kind":"clearance"',guidance)
        self.assertIn('"part":"shelf_level_1"',guidance)
        self.assertIn('"target":"shelf_level_2"',guidance)
        self.assertIn('/assembly replacement',guidance)
        self.assertIn('Do not move, resize',guidance)

    def test_recipe_guidance_removes_duplicate_content_from_structure_stage(self):
        from staged_planner import _repair_guidance
        guidance=_repair_guidance('recipe_bookcase_structure',
            'Structural recipe must contain exactly the frozen structure/detail IDs. missing=[]; unexpected=["b03"]; deferred_content_ids_used=[].')
        self.assertIn('even under renamed IDs',guidance)
        self.assertIn('let the assigned content batches construct the books later',guidance)

    def test_door_facing_repair_guidance_removes_the_unlowerable_relation(self):
        from staged_planner import _repair_guidance
        guidance=_repair_guidance('scene_design',
            'door_facing_relation: an entrance door leaf is oriented by its jamb and swing.')
        self.assertIn('authorized removable path',guidance)
        self.assertIn('Preserve the entrance opening, door leaf',guidance)
        self.assertIn('keep every design group connected to its anchor',guidance)

    def test_ask_retries_empty_response_once_and_keeps_attempt_evidence(self):
        import asyncio
        calls=[]
        async def complete(stage,instructions,payload):
            calls.append((stage,payload))
            return '' if len(calls)==1 else {'ok':True}
        with tempfile.TemporaryDirectory() as directory:
            planner=StagedScenePlanner('unused',directory,'test',complete=complete)
            result=asyncio.run(planner.ask('empty_probe','Return JSON.',{'same':'payload'}))
            attempts=json.loads((Path(directory)/'empty_probe_response_attempts.json').read_text())
            raw=json.loads((Path(directory)/'empty_probe_raw.txt').read_text())
        self.assertEqual(result,{'ok':True})
        self.assertEqual(len(calls),2)
        self.assertEqual(calls[0],calls[1])
        self.assertEqual([item['status'] for item in attempts],['empty','valid_json'])
        self.assertEqual(raw,{'ok':True})

    def test_ask_reports_repeated_empty_response_without_json_decode_traceback(self):
        import asyncio
        from staged_planner import EmptyAgentOutput
        async def complete(*_):return ''
        with tempfile.TemporaryDirectory() as directory:
            planner=StagedScenePlanner('unused',directory,'test',complete=complete)
            with self.assertRaisesRegex(EmptyAgentOutput,'empty or invalid JSON twice'):
                asyncio.run(planner.ask('empty_probe','Return JSON.',{}))
            attempts=json.loads((Path(directory)/'empty_probe_response_attempts.json').read_text())
        self.assertEqual([item['status'] for item in attempts],['empty','empty'])

    def test_ask_retries_truncated_json_before_returning_parseable_output(self):
        import asyncio
        calls=[]
        async def complete(stage,instructions,payload):
            calls.append((stage,instructions,payload))
            return '{"partial":' if len(calls)==1 else '{"ok": true}'
        with tempfile.TemporaryDirectory() as directory:
            planner=StagedScenePlanner('unused',directory,'test',complete=complete)
            result=asyncio.run(planner.ask('truncated_probe','Return JSON.',{'same':'payload'}))
            attempts=json.loads((Path(directory)/'truncated_probe_response_attempts.json').read_text())
        self.assertEqual(result,{'ok':True})
        self.assertEqual(len(calls),2)
        self.assertEqual(calls[0],calls[1])
        self.assertEqual([item['status'] for item in attempts],['invalid_json','valid_json'])

    def test_transport_failure_names_the_failed_stage_for_resume(self):
        import asyncio
        from httpx import ConnectError
        async def complete(*_):raise ConnectError('connection refused')
        with tempfile.TemporaryDirectory() as directory,patch('service_retry.backoff_delay',return_value=0):
            planner=StagedScenePlanner('unused',directory,'test',complete=complete)
            with self.assertRaises(ConnectError) as raised:
                asyncio.run(planner.ask('spatial_revision','Return JSON.',{}))
            attempts=json.loads((Path(directory)/'spatial_revision_service_attempts.json').read_text())
        self.assertEqual(getattr(raised.exception,'finescene_stage',None),'spatial_revision')
        self.assertEqual(len(attempts),3)
        self.assertEqual([item['same_request_retry'] for item in attempts],[True,True,False])
        self.assertIn('elapsed_seconds',attempts[0])
        self.assertNotIn('headers',attempts[0])

    def test_spatial_transport_fallback_really_disables_reasoning_and_keeps_raw(self):
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import patch
        settings=SimpleNamespace(extra_body={'thinking':{'type':'enabled'}},max_tokens=8192)
        seen=[]
        class Result:
            def __init__(self,raw):self.final_output=raw
            async def stream_events(self):
                if False:yield None
        def streamed(*args,**kwargs):
            seen.append((copy.deepcopy(settings.extra_body),settings.max_tokens))
            return Result('{"partial":' if len(seen)==1 else '{"ok":true}')
        with tempfile.TemporaryDirectory() as directory:
            planner=StagedScenePlanner('unused',directory,'test')
            planner.runtime=SimpleNamespace(model_settings=settings,profile=SimpleNamespace(model='deepseek-flash'),
                agent=lambda **kw:SimpleNamespace(name=kw['name']),
                usage=SimpleNamespace(record=lambda **kw:SimpleNamespace(output_tokens=100,reasoning_tokens=80)))
            with patch('agents.Runner.run_streamed',side_effect=streamed):
                result=asyncio.run(planner.ask('spatial_plan_0','Return JSON.',{}))
            self.assertEqual((Path(directory)/'spatial_plan_0_response_0_raw.txt').read_text(),'{"partial":')
        self.assertEqual(result,{'ok':True})
        self.assertEqual([x[0]['thinking']['type'] for x in seen],['enabled','disabled'])
        self.assertGreaterEqual(seen[0][1],65536)
        self.assertEqual(settings.max_tokens,8192)
        self.assertEqual(settings.extra_body,{'thinking':{'type':'enabled'}})

    def test_decomposition_repair_can_add_external_child_without_changing_request(self):
        import asyncio
        from staged_planner import replacement_paths,RequestContract,validate_request_contract
        from test_style_contract import requirement
        fixed=outline_data()
        fixed['style_bindings']=[{'requirement_id':'carving','component_ids':['ornament'],
            'implementation':'Retrieve the real curved sculpted mesh as a rigid child'}]
        initial=copy.deepcopy(fixed)
        initial['objects']=initial['objects'][:1]
        initial['zones'][0]['component_ids']=['base']
        initial['requirements'][0]['component_ids']=['base']
        initial['style_bindings'][0]['component_ids']=['base']
        contract=RequestContract(asset_boundary='single_asset',quality_target='prototype',style_requirements=[requirement()])
        calls=[]
        async def complete(stage,instructions,payload):
            calls.append(stage)
            if len(calls)==1:return initial
            self.assertIn('inventory is NOT frozen',instructions)
            self.assertNotIn('Preserve all IDs',instructions)
            self.assertIn('/objects',payload['allowed_paths'])
            self.assertEqual(payload['frozen_context']['request_contract'],contract.model_dump())
            return {'reason':'Separate sourced relief from the editable root','replacements':[
                {'path':'/'+key,'value':fixed[key]} for key in ('objects','zones','requirements','style_bindings')]}
        with tempfile.TemporaryDirectory() as directory:
            planner=StagedScenePlanner('unused',directory,'test',complete=complete)
            result=asyncio.run(planner.stage('decomposition',Decomposition,'test',
                {'request_contract':contract.model_dump()},lambda d:validate_request_contract(d,contract),replacement_paths))
        self.assertEqual([o.id for o in result.objects],['base','ornament'])
        self.assertEqual(len(calls),2)

    def test_single_asset_rejects_parametric_sibling_contents(self):
        from staged_planner import validate_request_contract,RequestContract,Decomposition
        data=outline_data()
        data['objects'][1]['acquisition']={'strategy':'parametric','reason':'Editable sibling content with explicit dimensions.'}
        data['objects'][1]['requirement_tags']=['books']
        contract=RequestContract(asset_boundary='single_asset',quality_target='prototype',contents=[])
        with self.assertRaisesRegex(ValueError,'parametric sibling'):
            validate_request_contract(Decomposition.model_validate(data),contract)

    def test_shell_header_cannot_declare_floor_support(self):
        from staged_planner import Decomposition
        data=outline_data()
        data['objects'][0].update(semantic='Enclosed rectangular room shell',dimensions=[4,4,3],collision_role='shell',
            internal_parts=[
                {'id':'floor_slab','semantic':'Room floor slab','rigidity':'parametric','role':'structure',
                 'form':{'kind':'panel','axis':'z','minimum_ratio':2}},
                {'id':'ceiling','semantic':'Ceiling slab','rigidity':'parametric','role':'structure',
                 'form':{'kind':'panel','axis':'z','minimum_ratio':2}},
                {'id':'left_wall','semantic':'Left full-height wall','rigidity':'parametric','role':'structure',
                 'support':'floor_slab','form':{'kind':'panel','axis':'x','minimum_ratio':2}},
                {'id':'right_wall','semantic':'Right full-height wall','rigidity':'parametric','role':'structure',
                 'support':'floor_slab','form':{'kind':'panel','axis':'x','minimum_ratio':2}},
                {'id':'window_header','semantic':'Wall header above window opening','rigidity':'parametric',
                 'role':'structure','support':'floor_slab','form':{'kind':'panel','axis':'y','minimum_ratio':2}}])
        data['objects'][1].update(parent=None,mount='origin',support_part=None,access_required=False)
        data['extent']=[4,4,3]
        data['zones'][0]['component_ids']=['base','ornament']
        data['requirements'][0]['component_ids']=['base','ornament']
        with self.assertRaisesRegex(ValueError,'shell_header_support'):
            Decomposition.model_validate(data)
        # After correcting the impossible support, the wrong header form must
        # still be rejected before the expensive shell recipe stage.
        data['objects'][0]['internal_parts'][-1]['support']=None
        with self.assertRaisesRegex(ValueError,'shell_header_form'):
            Decomposition.model_validate(data)
        data['objects'][0]['internal_parts'][-1]['form']={'kind':'beam','axis':'x','minimum_ratio':2}
        data['objects'][0]['internal_parts'][-1]['semantic']='Horizontal door head lintel'
        with self.assertRaisesRegex(ValueError,'shell_door_head_ratio'):
            Decomposition.model_validate(data)

    def test_edit_cannot_pass_with_only_cancelling_parameter_reference(self):
        from general_scene import GeneralPlan,SceneObject,Part,edit_general
        plan=GeneralPlan(scene_id='inert',description='inert declared edit',extent=(3,3,3),parameters={'height':.8},objects=[
            SceneObject(id='base',semantic='base',size=[1,1,.1],position=[0,0,'height-height'],
                parts=[Part(id='slab',primitive='box',size=['w','d','h'],center=[0,0,'h/2'])])])
        with self.assertRaisesRegex(ValueError,'no measured geometry effect'):
            edit_general(plan,'height',.1)
        # A local recipe may legitimately be unaffected by another component's edit.
        edit_general(plan,'height',.1,require_effect=False)
        plan.objects[0].size=(1.,1.,'height')
        edit_general(plan,'height',.1)

    def test_owned_edit_parameter_must_be_wired_in_component_recipe(self):
        data=outline_data()
        data['parameters']['shelf_gap']=.36
        data['edit_checks']=[{'parameter':'shelf_gap','delta':.1,
                              'intent':'raise the editable shelf and supported contents'}]
        data['objects'][0]['semantic']='editable base with shelf_gap-driven middle shelf'
        outline=Decomposition.model_validate(data)
        with self.assertRaisesRegex(ValueError,'must be expressed in the parametric recipe'):
            validate_recipe(outline,outline.objects[0],Recipe.model_validate(recipe_data()))

    def test_owned_edit_parameter_can_be_wired_by_part_expression(self):
        data=outline_data()
        data['parameters']['shelf_gap']=.36
        data['edit_checks']=[{'parameter':'shelf_gap','delta':.1,
                              'intent':'raise the editable shelf and supported contents'}]
        data['objects'][0]['semantic']='editable base with shelf_gap-driven middle shelf'
        outline=Decomposition.model_validate(data)
        recipe=recipe_data()
        recipe['parts'][0]['size']=['w','d','h']
        recipe['parts'][0]['center']=[0,0,'h/2']
        recipe['parts'].append({'id':'editable_inset','primitive':'box','size':['shelf_gap/4',.1,.1],
                                'center':[0,0,'h+.05']})
        # Keep the declared envelope fixed while a real internal size changes.
        recipe['parts'][0]['size']=['w','d','h-.1']
        recipe['parts'][0]['center']=[0,0,'(h-.1)/2']
        recipe['parts'][1]['center']=[0,0,'h-.05']
        recipe['assembly']={'roots':['slab'],'relations':[{'kind':'supported_by',
            'part':'editable_inset','target':'slab','reason':'Inset rests on the base.'}]}
        validated=validate_recipe(outline,outline.objects[0],Recipe.model_validate(recipe))
        self.assertEqual(validated.parts[1].size[0],'shelf_gap/4')

    def test_component_recipe_context_ignores_unrelated_external_siblings(self):
        from staged_planner import component_recipe_context
        first=outline_data()
        second=copy.deepcopy(first)
        extra=copy.deepcopy(second['objects'][1])
        extra['id']='ornament_variant'
        second['objects'].append(extra)
        second['zones'][0]['component_ids'].append('ornament_variant')
        second['requirements'][0]['component_ids'].append('ornament_variant')
        outline_a=Decomposition.model_validate(first)
        outline_b=Decomposition.model_validate(second)
        context_a=component_recipe_context(outline_a,outline_a.objects[0],'same brief')
        context_b=component_recipe_context(outline_b,outline_b.objects[0],'same brief')
        self.assertEqual(context_a,context_b)
        self.assertTrue(context_a['externally_acquired_descendants_are_not_recipe_parts'])

    def test_recipe_agent_gets_component_experience_and_material_appearance_metadata(self):
        import asyncio
        data=outline_data()
        data['objects']=data['objects'][:1]
        data['objects'][0]['id']='tatami_platform'
        data['objects'][0]['semantic']='Low built-in tatami sleeping platform'
        data['objects'][0]['material_requirements']=['muted rush mat and separate linen bedding']
        data['zones'][0]['component_ids']=['tatami_platform']
        data['requirements'][0]['component_ids']=['tatami_platform']
        outline=Decomposition.model_validate(data)
        material_context=[{'id':'rush','surface_family':'plant_fiber',
            'appearance_tags':[],'visual_description':'Muted woven rush surface.',
            'maps':['color'],'source':'local'}]

        class EndProbe(RuntimeError):pass
        async def capture(name,schema,instructions,context,validate,allowed=None,**kwargs):
            self.assertEqual(name,'recipe_tatami_platform')
            self.assertIn('Tatami sleep/rest assembly',instructions)
            self.assertIn('never reuse the bedding fabric',instructions)
            self.assertIn('surface_family',instructions)
            self.assertIn('surface=null',instructions)
            self.assertIn('flush at clean seams',instructions)
            self.assertEqual(context['available_materials'],material_context)
            self.assertIn('surface_family',str(context['available_materials']))
            raise EndProbe()

        with tempfile.TemporaryDirectory() as directory,patch(
                'material_catalog.capabilities',return_value=material_context):
            planner=StagedScenePlanner('unused',directory,'experience',complete=lambda *_:None)
            planner.stage=capture
            with self.assertRaises(EndProbe):
                asyncio.run(planner.component_recipe(outline,outline.objects[0],
                    'High-school student bedroom with tatami and window'))

    def test_recipe_rejects_wrong_tatami_surface_but_accepts_procedural_surface(self):
        data=outline_data()
        data['objects'][0]['semantic']='Low built-in tatami sleeping platform'
        data['objects'][0]['material_requirements']=['muted rush-straw mat surface']
        outline=Decomposition.model_validate(data)
        obj=outline.objects[0]
        recipe=recipe_data()
        recipe['parts'][0].update(id='tatami_mat_top',semantic='Woven tatami mat face',
                                  surface={'texture_id':'wood_map'})
        materials={'wood_map':{'surface_family':'wood','appearance_tags':[]}}
        with patch('material_catalog.catalog',return_value=materials):
            wrong=Recipe.model_validate(recipe)
            with self.assertRaises(ValueError) as caught:
                validate_recipe(outline,obj,wrong)
            issues=caught.exception.measurements['issues']
            self.assertEqual([item['code'] for item in issues],['material_semantic_conflict'])
            self.assertEqual(issues[0]['paths'],['/parts/0/surface'])
            recipe['parts'][0]['surface']=None
            procedural=Recipe.model_validate(recipe)
            accepted=validate_recipe(outline,obj,procedural)
            self.assertIsNone(accepted.parts[0].surface)

    def test_tatami_platform_wooden_support_deck_is_not_a_tatami_face(self):
        from material_catalog import material_assignment_issues
        data=outline_data()
        data['objects'][0]['semantic']='Low tatami sleeping platform with a separate wooden support deck.'
        data['objects'][0]['material_requirements']=['pale straw woven tatami faces','natural wood support deck']
        outline=Decomposition.model_validate(data)
        part={'id':'platform_deck','semantic':'Structural wooden platform deck supporting the tatami mats',
              'surface':{'texture_id':'wood_map'}}
        with patch('material_catalog.catalog',return_value={
                'wood_map':{'surface_family':'wood','appearance_tags':[]}}):
            self.assertEqual(material_assignment_issues(outline.objects[0],[part]),[])

    def test_stale_recipe_reaches_component_agent_with_full_material_diagnostic(self):
        import asyncio
        from spatial_recovery import revalidate_or_repair_recipe
        data=outline_data()
        data['objects'][0]['semantic']='Low tatami sleeping platform'
        data['objects'][0]['material_requirements']=['muted straw woven tatami face']
        outline=Decomposition.model_validate(data)
        obj=outline.objects[0]
        bad=Recipe.model_validate({'parts':[{'id':'woven_mat_face','primitive':'box',
            'size':['w','d','h'],'center':[0,0,'h/2'],'semantic':'Woven tatami mat face',
            'surface':{'texture_id':'fabric_pattern_07'}}]})
        calls=[]
        async def repair(stage,instructions,payload):
            calls.append(stage)
            self.assertEqual(stage,'recipe_base_repair_1')
            self.assertEqual(len(payload['diagnostic_report']['issues']),2)
            self.assertTrue(all(item['code']=='material_semantic_conflict'
                                for item in payload['diagnostic_report']['issues']))
            return {'reason':'Use the specified straw procedural finish because no compatible map is selected.',
                'replacements':[{'path':'/parts/0/surface','value':None}]}
        with tempfile.TemporaryDirectory() as directory,patch('material_catalog.catalog',return_value={
                'fabric_pattern_07':{'source':'fixture','maps':{'color':'fixture.jpg'},
                    'surface_family':'textile','appearance_tags':['red','checkered_or_plaid','high_contrast']}}):
            agent=StagedScenePlanner('unused',directory,'repair',complete=repair)
            repaired,changed=asyncio.run(revalidate_or_repair_recipe(
                agent,outline,obj,bad,'Tatami bedroom',[]))
            report=json.loads((Path(directory)/'recipe_base_reuse_validation_failure.json').read_text())
        self.assertTrue(changed)
        self.assertEqual(calls,['recipe_base_repair_1'])
        self.assertEqual(len(report['issues']),2)
        self.assertIsNone(repaired.parts[0].surface)

    def test_repair_loop_continues_through_successive_validation_errors(self):
        import asyncio
        calls=[]
        async def complete(stage,instructions,payload):
            calls.append(stage)
            if len(calls)==1:return {'placements':[{'id':'base','position':[1,1,1]}]}
            axis=len(calls)-2
            return {'reason':'Resolve the next exposed constraint','replacements':[
                {'path':f'/placements/0/position/{axis}','value':0}]}
        def validate(layout):
            for axis,value in enumerate(layout.placements[0].position):
                if value:raise ValueError(f'Axis {axis} must be zero')
            return layout
        with tempfile.TemporaryDirectory() as directory:
            planner=StagedScenePlanner('unused',directory,'test',complete=complete)
            result=asyncio.run(planner.stage('layout',Layout,'test',{},validate,
                lambda data:[f'/placements/0/position/{i}' for i in range(3)]))
        self.assertEqual(result.placements[0].position,(0,0,0))
        self.assertEqual(len(calls),4)

    def test_validator_signature_error_is_reported_before_calling_the_model(self):
        import asyncio
        calls=[]
        async def complete(*args):calls.append(args)
        with tempfile.TemporaryDirectory() as directory:
            planner=StagedScenePlanner('unused',directory,'test',complete=complete)
            with self.assertRaisesRegex(TypeError,'validator must accept one parsed candidate'):
                asyncio.run(planner.stage('functional_bindings',FunctionalBindings,'test',{},
                    apply_functional_bindings))
        self.assertEqual(calls,[])

    def test_validator_type_error_is_not_sent_back_as_a_scene_repair(self):
        import asyncio
        calls=[]
        async def complete(*args):calls.append(args)
        def broken_validator(_candidate):raise TypeError('validator implementation bug')
        with tempfile.TemporaryDirectory() as directory:
            planner=StagedScenePlanner('unused',directory,'test',complete=complete)
            with self.assertRaisesRegex(TypeError,'validator implementation bug'):
                asyncio.run(planner.stage('layout',Layout,'test',{},broken_validator,
                    initial_candidate=layout_data()))
        self.assertEqual(calls,[])

    def test_repair_loop_stops_when_diagnostic_batch_does_not_change(self):
        import asyncio
        calls=[]
        async def complete(stage,instructions,payload):
            calls.append(stage)
            if len(calls)==1:return {'placements':[{'id':'base','position':[1,0,0]}]}
            return {'reason':'Unproductive oscillation','replacements':[
                {'path':'/placements/0/position/0','value':2 if len(calls)%2==0 else 1}]}
        def validate(layout):raise ValueError('Contact still invalid')
        with tempfile.TemporaryDirectory() as directory:
            planner=StagedScenePlanner('unused',directory,'test',complete=complete)
            with self.assertRaisesRegex(ValueError,'repeated failed state'):
                asyncio.run(planner.stage('layout',Layout,'test',{},validate,
                    lambda data:['/placements/0/position/0']))
            self.assertEqual(planner.events[-1]['reason'],'repeated_diagnostic_batch_without_progress')
        self.assertEqual(len(calls),2)

    def test_partial_object_replacement_preserves_unmentioned_decomposition_fields(self):
        document=outline_data()
        original=copy.deepcopy(document['objects'][0])
        original.pop('visual_importance',None)
        document['objects'][0]=copy.deepcopy(original)
        repair=LocalRepair.model_validate({'reason':'Set the visual priority.', 'replacements':[
            {'path':'/objects/0','value':{'visual_importance':5}}]})
        revised=apply_local_repair(document,repair,['/objects/0'])
        self.assertEqual(revised['objects'][0],{**original,'visual_importance':5})

    def test_decomposition_defaults_visual_importance_like_the_general_planner(self):
        data=outline_data()
        data['objects'][0].pop('visual_importance')
        data['objects'][0]['decomposition_reason']='适合卧室用途'
        outline=Decomposition.model_validate(data)
        self.assertEqual(outline.objects[0].visual_importance,3)

    def test_resume_candidate_selection_skips_newer_schema_corruption(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            valid=outline_data()
            invalid={'objects':[{'visual_importance':4}]}
            (root/'decomposition_failure_0.json').write_text(
                json.dumps({'candidate':valid}),encoding='utf-8')
            (root/'decomposition_failure_1.json').write_text(
                json.dumps({'candidate':invalid}),encoding='utf-8')
            restored=latest_failed_stage_candidate(root,'decomposition',Decomposition)
        self.assertEqual(Path(restored['path']).name,'decomposition_failure_0.json')
        self.assertEqual(restored['candidate']['objects'][0]['id'],valid['objects'][0]['id'])

    def test_recipe_cannot_drop_frozen_functional_clearance(self):
        data=outline_data();obj=data['objects'][0]
        obj['internal_parts']=[{'id':i,'semantic':i,'rigidity':'parametric','role':'structure'} for i in ('slab','upper')]
        obj['internal_clearances']=[{'a':'slab','b':'upper','minimum_m':.02,'reason':'functional gap'}]
        outline=Decomposition.model_validate(data)
        recipe=recipe_data();recipe['parts'].append({**recipe['parts'][0],'id':'upper'})
        with self.assertRaises(ValueError) as caught:
            validate_recipe(outline,outline.objects[0],Recipe.model_validate(recipe))
        self.assertIn('Part clearance',str(caught.exception))

    def test_swept_style_binding_is_checked_during_component_recipe_validation(self):
        from style_contract import StyleRequirement
        data=outline_data();data['style_bindings']=[{'requirement_id':'curve','component_ids':['base'],
            'implementation':'Build the required curved structural member as an editable sweep.',
            'part_ids':['base/slab']}]
        data['objects'][0]['internal_parts']=[{'id':'slab','semantic':'curved rail','rigidity':'parametric','role':'structure'}]
        outline=Decomposition.model_validate(data);obj=outline.objects[0]
        requirements=[StyleRequirement(id='curve',feature='Curved structural member',
            visible_evidence='A continuous visible swept centerline',geometry_need='swept_path')]
        with self.assertRaisesRegex(ValueError,'real sweep or lathe primitive'):
            validate_recipe(outline,obj,Recipe.model_validate(recipe_data()),requirements)

    def test_internal_inventory_and_rigidity_cannot_disappear_in_recipe(self):
        data=outline_data()
        data['objects'][0]['internal_parts']=[{'id':'slab','semantic':'fixed slab','rigidity':'rigid','role':'structure'},
            {'id':'content','semantic':'required rigid contents','rigidity':'rigid','role':'content'}]
        outline=Decomposition.model_validate(data)
        recipe=recipe_data();recipe['parts'][0]['rigidity']='rigid'
        with self.assertRaisesRegex(ValueError,'Missing required internal part content'):
            validate_recipe(outline,outline.objects[0],Recipe.model_validate(recipe))
        recipe['parts'][0]['rigidity']='parametric'
        from staged_planner import _lower_frozen_assembly_relations
        lowered=_lower_frozen_assembly_relations(outline.objects[0],Recipe.model_validate(recipe))
        self.assertEqual(lowered.parts[0].rigidity,'rigid')
        with self.assertRaisesRegex(ValueError,'Missing required internal part content'):
            validate_recipe(outline,outline.objects[0],Recipe.model_validate(recipe))

    def test_grounded_support_extent_accounts_for_requested_edit(self):
        data=outline_data()
        data['parameters']['bench_height']=1.0
        data['extent'][2]=1.55
        leg=copy.deepcopy(data['objects'][0])
        leg.update(id='leg',semantic='under-table support',dimensions=[.1,.1,'bench_height-.2'],
                   parent='base',mount='bottom',grounded=True)
        data['objects'].append(leg)
        data['requirements'][0]['component_ids'].append('leg')
        Decomposition.model_validate(data)  # baseline top = 1.5m fits
        data['edit_checks']=[{'parameter':'bench_height','delta':.2,'intent':'raise tabletop'}]
        with self.assertRaisesRegex(ValueError,'edit bench_height.*grounded support chain'):
            Decomposition.model_validate(data)
        data['extent'][2]=1.75
        Decomposition.model_validate(data)

    def test_visual_service_failure_preserves_assets_and_geometry(self):
        from general_repairs import repair_work_orders
        plan=assemble_plan(Decomposition.model_validate(outline_data()),
            {'base':Recipe.model_validate(recipe_data())},Layout.model_validate(layout_data()))
        original=plan.model_dump()
        result=repair_work_orders(plan,{'findings':[]},{'status':'unavailable','error_type':'TimeoutError'})
        self.assertFalse(result['automatic_execution'])
        self.assertEqual(len(result['orders']),1)
        self.assertEqual(result['orders'][0]['allowed_fields'],[])
        self.assertEqual(result['orders'][0]['status'],'retry_review_with_existing_images')
        self.assertEqual(plan.model_dump(),original)

    def test_repair_work_order_scopes_dependency_revalidation(self):
        from general_repairs import repair_work_orders
        plan=assemble_plan(Decomposition.model_validate(outline_data()),
            {'base':Recipe.model_validate(recipe_data())},Layout.model_validate(layout_data()))
        result=repair_work_orders(plan,{'findings':[]},{'decision':{'findings':[
            {'severity':'blocking','repair_target':'layout','component_ids':['base'],
             'evidence':'support is incorrectly placed','view':'overview.png','correction':'move support'},
            {'severity':'improvement','repair_target':'materials','component_ids':['ornament']}]}})
        self.assertEqual(len(result['orders']),1)
        self.assertEqual(result['orders'][0]['revalidate'],['base','ornament'])
        self.assertEqual(result['orders'][0]['allowed_fields'],['position','orientation_deg'])

    def test_rigid_edit_conflict_is_a_decomposition_error(self):
        data=outline_data();data['objects'][1]['dimensions'][2]='base_width'
        data['edit_checks']=[{'parameter':'base_width','delta':.1,'intent':'widen base without resizing ornament'}]
        with self.assertRaisesRegex(ValueError,'rigid ornament would resize'):Decomposition.model_validate(data)

    def test_impossible_support_clearance_is_not_sent_to_layout(self):
        data=outline_data();data['clearances']=[{'a':'ornament','b':'base','minimum_m':.2,'reason':'space'}]
        with self.assertRaisesRegex(ValueError,'support chain fixes separation'):Decomposition.model_validate(data)

    def test_room_height_is_not_frozen_as_a_shell_internal_clearance(self):
        data=outline_data();shell=data['objects'][0]
        shell.update({'semantic':'Enclosed architectural room shell','dimensions':[4,4,3],
            'collision_role':'shell','internal_parts':[
                {'id':'floor_slab','semantic':'Structural floor slab','rigidity':'parametric','role':'structure'},
                {'id':'ceiling','semantic':'Flat ceiling panel','rigidity':'parametric','role':'structure'}],
            'internal_clearances':[{'a':'floor_slab','b':'ceiling','minimum_m':2.7,
                'reason':'2.7 m room clear height'}]})
        outline=Decomposition.model_validate(data,context={'defer_decomposition_semantics':True})
        with self.assertRaisesRegex(ValueError,'floor-to-ceiling room height is encoded') as caught:
            outline.validate_decomposition(None)
        issue=caught.exception.measurements['issues'][0]
        self.assertEqual(issue['code'],'shell_clearance_as_assembly')
        self.assertEqual(issue['measured']['repair_target'],'decomposition')
        self.assertIn('Room clear height is NOT an internal assembly clearance',DECOMPOSE)

    def test_decomposition_target_escalates_without_another_recipe_model_call(self):
        import asyncio
        from planning_diagnostics import PlanningValidationError
        with tempfile.TemporaryDirectory() as tmp:
            calls=[]
            async def complete(stage,instructions,payload):
                calls.append(stage)
                return recipe_data()
            planner=StagedScenePlanner(Path(tmp)/'profile',Path(tmp)/'run','escalation-test',complete=complete)
            report={'stage':'recipe_base','status':'needs_revision','issues':[{
                'id':'decomposition-required','code':'shell_clearance_as_assembly',
                'evidence':'Room height is encoded as an impossible frozen assembly clearance.',
                'paths':['/objects/0/internal_clearances/0'],'component_ids':['base'],
                'measured':{'repair_target':'decomposition'}}], 'blocked_checks':[]}
            def validate(_candidate):raise PlanningValidationError(report)
            with self.assertRaises(DetailedPlanningRevisionRequired):
                asyncio.run(planner.stage('recipe_base',Recipe,'recipe instructions',{},validate,
                    lambda _candidate:['/parts'],initial_candidate=recipe_data()))
            self.assertEqual(calls,[])
            self.assertEqual(planner.events[-1]['escalation'],'decomposition_revision_required')

    def test_unsatisfiable_fixed_yaws_escalate_without_a_local_layout_repair_call(self):
        import asyncio
        report={'stage':'coarse_layout','status':'needs_revision','issues':[{
            'id':'facing-conflict','code':'facing','evidence':'Fixed-yaw candidate is infeasible.',
            'paths':['/placements/1/yaw_deg'],'component_ids':['chair','desk']}],
            'blocked_checks':[],'numerical_solver':{
                'status':'infeasible_current_choices',
                'independent_conflict_groups':[['baseline functional relation chair_faces_desk']],
                'orientation_proposals':{'chair':{'from_world_deg':0,'to_world_deg':180}}}}
        calls=[]
        async def complete(stage,instructions,payload):
            calls.append(stage)
            return layout_data()
        with tempfile.TemporaryDirectory() as tmp:
            planner=StagedScenePlanner(Path(tmp)/'profile',Path(tmp)/'run','yaw-escalation-test',complete=complete)
            def validate(_candidate):raise SpatialChoicesInfeasible(report)
            with self.assertRaises(SpatialChoicesInfeasible):
                asyncio.run(planner.stage('coarse_layout',Layout,'layout instructions',
                    {'frozen_decomposition':{'objects':[]}},validate,
                    lambda _candidate:['/placements/0/yaw_deg'],initial_candidate=layout_data()))
            self.assertEqual(calls,[])
            self.assertEqual(planner.events[-1]['escalation'],'joint_spatial_revision_required')
            self.assertEqual(planner.events[-1]['orientation_proposals']['chair']['to_world_deg'],180)

    def test_access_face_repair_can_remove_exact_extra_mapping_keys(self):
        import asyncio
        candidate={'allowed_sides':{'tatami_platform':['front'],'study_chair':['back'],
                'wardrobe':['front'],'laundry_basket':['front'],'study_desk':['front']},
            'reasons':{'tatami_platform':'Reach the mat from its open front edge.',
                'study_chair':'Pull the seat from behind while it faces the desk.',
                'wardrobe':'Use the front sliding doors.','laundry_basket':'Reach over the open front rim.',
                'study_desk':'The paired chair owns the seated approach.'}}
        calls=[]
        async def complete(stage,instructions,payload):
            calls.append((stage,payload.get('removable_paths')))
            return {'reason':'Remove the non-human-use desk entries.', 'replacements':[
                {'path':'/allowed_sides/study_desk','operation':'remove'},
                {'path':'/reasons/study_desk','operation':'remove'}]}
        with tempfile.TemporaryDirectory() as tmp:
            planner=StagedScenePlanner(Path(tmp)/'profile',Path(tmp)/'run','access-face-repair',complete=complete)
            result=asyncio.run(planner.stage('access_faces',AccessFaces,'access instructions',
                {'brief':'A student bedroom'},
                lambda value:validate_access_faces(value,
                    {'tatami_platform','study_chair','wardrobe','laundry_basket'}),
                replacement_paths,initial_candidate=candidate))
            self.assertEqual(set(result.allowed_sides),
                {'tatami_platform','study_chair','wardrobe','laundry_basket'})
            self.assertEqual(set(result.reasons),set(result.allowed_sides))
            self.assertEqual(len(calls),1)
            self.assertEqual(set(calls[0][1]),{'/allowed_sides/study_desk','/reasons/study_desk'})
            self.assertIn('Remove every extra allowed_sides/reasons key',
                          local_repair_stage_guidance('access_faces'))

    def test_access_face_projection_drops_non_owner_ids_without_an_llm_repair(self):
        candidate=AccessFaces.model_validate({
            'allowed_sides':{'tatami_platform':['front'],'study_chair':['back'],
                'wardrobe':['front'],'laundry_basket':['front'],'study_desk':['front']},
            'reasons':{'tatami_platform':'Reach the exposed mat edge.',
                'study_chair':'Pull the chair from its clear rear side.',
                'wardrobe':'Use the front sliding doors.','laundry_basket':'Reach over its open rim.',
                'study_desk':'The paired chair owns the seated approach.'}})
        expected=['tatami_platform','study_chair','wardrobe','laundry_basket']
        projected=project_access_face_ids(candidate,expected)
        self.assertEqual(set(projected.allowed_sides),set(expected))
        self.assertEqual(set(projected.reasons),set(expected))
        self.assertIn('study_desk',candidate.allowed_sides)
        validate_access_faces(projected,set(expected),{'study_chair'})

    def test_approach_owner_diagnostic_lists_missing_extra_and_duplicate_ids(self):
        candidate=CirculationProgram.model_validate({'approaches':[
            {'owner_id':'tatami_platform','side':'front','size':[1,.8],'final_route_width':.6,
             'reason':'Enter the exposed mat edge.'},
            {'owner_id':'study_chair','side':'back','size':[1,.8],'final_route_width':.6,
             'reason':'Pull the chair from behind it.'},
            {'owner_id':'study_chair','side':'back','size':[1,.8],'final_route_width':.6,
             'reason':'Pull the chair from behind it.'},
            {'owner_id':'study_desk','side':'front','size':[1,.8],'final_route_width':.6,
             'reason':'The paired chair owns the approach.'}]})
        with self.assertRaisesRegex(ValueError,'missing=.*wardrobe.*laundry_basket') as caught:
            validate_approach_owners(candidate,['tatami_platform','study_chair','wardrobe','laundry_basket'])
        message=str(caught.exception)
        self.assertIn('unexpected=[\'study_desk\']',message)
        self.assertIn('duplicates=[\'study_chair\']',message)

    def test_no_op_repair_is_rejected(self):
        repair=LocalRepair(reason='empty fix',replacements=[{'path':'/placements/0/position','value':[0,0,0]}])
        with self.assertRaisesRegex(ValueError,'no change'):
            apply_local_repair(layout_data(),repair,['/placements/0/position'])

    def test_nested_repair_is_allowed_inside_an_authorized_route_map(self):
        original={'route_points':{'reach_a':[[0,0],[1,1]],'reach_b':[[2,2]]}}
        repair=LocalRepair(reason='adjust route waypoint',replacements=[
            {'path':'/route_points/reach_a/1/0','value':0.5}])
        changed=apply_local_repair(original,repair,['/route_points'])
        self.assertEqual(changed['route_points']['reach_a'],[[0,0],[0.5,1]])
        self.assertEqual(changed['route_points']['reach_b'],[[2,2]])
        with self.assertRaisesRegex(ValueError,'authorized fields'):
            apply_local_repair(original,LocalRepair(reason='escape scope',replacements=[
                {'path':'/placements/0/position','value':[0,0,0]}]),['/route_points'])

    def test_parent_and_child_repairs_preserve_unmentioned_mapping_entries(self):
        original={'access_centers':{'use_fireplace':[0.0,0.2],'use_console':[0.0,0.4]}}
        repair=LocalRepair(reason='revise one access center',replacements=[
            {'path':'/access_centers','value':{'use_console':[0.0,0.3]}},
            {'path':'/access_centers/use_console/1','value':0.25}])
        changed=apply_local_repair(original,repair,['/access_centers'])
        self.assertEqual(changed,{'access_centers':{
            'use_fireplace':[0.0,0.2],'use_console':[0.0,0.25]}})
        self.assertEqual(original['access_centers']['use_console'],[0.0,0.4])

    def test_malformed_sibling_field_in_repair_item_splits_only_when_authorized(self):
        original={'route_points':{'reach_a':[[0,0],[1,1]]},
                  'access_centers':{'use_a':[0.0,0.2]}}
        proposal={'reason':'revise route and access point','replacements':[
            {'path':'/route_points','value':{'reach_a':[[0,0],[0.5,1]]},
             'access_centers':{'use_a':[0.0,0.3]}}]}
        normalized=normalize_local_repair_proposal(
            proposal,original,['/route_points','/access_centers'])
        repair=LocalRepair.model_validate(normalized)
        changed=apply_local_repair(original,repair,['/route_points','/access_centers'])
        self.assertEqual(changed['route_points']['reach_a'],[[0,0],[0.5,1]])
        self.assertEqual(changed['access_centers']['use_a'],[0.0,0.3])
        self.assertEqual(proposal['replacements'][0]['access_centers']['use_a'],[0.0,0.3])
        with self.assertRaisesRegex(ValueError,'Extra inputs are not permitted'):
            LocalRepair.model_validate(normalize_local_repair_proposal(
                proposal,original,['/route_points']))

    def test_full_layout_candidate_is_projected_to_only_authorized_fields(self):
        original={'placements':[{'id':'chair','position':[0,0,0],'yaw_deg':0,
                                 'dimensions':[1,1,1]}],
                  'route_points':{'reach_chair':[[0,0],[1,0]]},
                  'access_centers':{'use_chair':[0.0,-0.4]}}
        full={'placements':[{'id':'renamed','position':[0.5,0,0],'yaw_deg':45,
                             'dimensions':[2,2,2]}],
              'route_points':{'reach_chair':[[0,0],[0.5,0],[1,0]]},
              'access_centers':{'use_chair':[0.0,-0.5]}}
        allowed=['/placements/0/position','/placements/0/yaw_deg',
                 '/route_points','/access_centers']
        repair=LocalRepair.model_validate(normalize_local_repair_proposal(
            full,original,allowed))
        changed=apply_local_repair(original,repair,allowed)
        self.assertEqual(changed['placements'][0]['id'],'chair')
        self.assertEqual(changed['placements'][0]['dimensions'],[1,1,1])
        self.assertEqual(changed['placements'][0]['position'],[0.5,0,0])
        self.assertEqual(changed['placements'][0]['yaw_deg'],45)
        self.assertEqual(changed['route_points'],full['route_points'])
        self.assertEqual(changed['access_centers'],full['access_centers'])

    def test_parent_placement_replacement_projects_only_authorized_children(self):
        original={'placements':[{'id':'chair','position':[0,0,0],'yaw_deg':0,
                                 'dimensions':[1,1,1]}]}
        proposal={'reason':'move chair','replacements':[
            {'path':'/placements/0','value':{'id':'renamed','position':[0.5,0,0],
                                              'yaw_deg':45,'dimensions':[2,2,2]}}]}
        allowed=['/placements/0/position','/placements/0/yaw_deg']
        repair=LocalRepair.model_validate(normalize_local_repair_proposal(proposal,original,allowed))
        changed=apply_local_repair(original,repair,allowed)
        self.assertEqual(changed['placements'][0],{'id':'chair','position':[0.5,0,0],
                                                    'yaw_deg':45,'dimensions':[1,1,1]})

    def test_list_parent_and_nested_position_repairs_merge_without_losing_siblings(self):
        original={'placements':[{'id':'chair','position':[0,0,0],'yaw_deg':0,
                                 'dimensions':[1,1,1]}]}
        proposal={'reason':'move and rotate the chair','replacements':[
            {'path':'/placements','value':[{'id':'chair','position':[1,0,0],
                'yaw_deg':45,'dimensions':[2,2,2]}]},
            {'path':'/placements/0/position/1','value':.25}]}
        allowed=['/placements/0/position','/placements/0/yaw_deg']
        repair=LocalRepair.model_validate(normalize_local_repair_proposal(
            proposal,original,allowed))
        changed=apply_local_repair(original,repair,allowed)
        self.assertEqual(changed['placements'][0],{'id':'chair','position':[1,.25,0],
            'yaw_deg':45,'dimensions':[1,1,1]})

    def test_misnested_mounting_contact_path_is_recovered_to_authorized_root(self):
        original={'mounting_contacts':{'bookcase':{'parent_part':'wall_east',
            'parent_face':'x_max','child_part':'bookcase_back','child_face':'x_max'}}}
        proposal={'reason':'select the measured wall interface','replacements':[
            {'path':'/layout/mounting_contacts','value':{'bookcase':{
                'parent_part':'wall_east','parent_face':'x_max','child_part':'bookcase_back',
                'child_face':'y_max'}}}]}
        allowed=['/mounting_contacts']
        repair=LocalRepair.model_validate(normalize_local_repair_proposal(
            proposal,original,allowed))
        changed=apply_local_repair(original,repair,allowed)
        self.assertEqual(repair.replacements[0].path,'/mounting_contacts')
        self.assertEqual(changed['mounting_contacts']['bookcase']['child_face'],'y_max')

    def test_open_space_identity_and_purpose_are_normalized_against_frozen_intents(self):
        original={'spatial':{'open_spaces':[{'intent_id':'central_loop','center':[0,0],
            'size':[1.5,1.5],'bottom':0,'height':2.0}]}}
        proposal={'reason':'preserve the circulation loop','replacements':[
            {'path':'/spatial/open_spaces','value':[{'id':'central_loop',
                'purpose':'keep a path around the main seating group','center':[.2,.1],
                'size':[1.5,1.5],'bottom':0,'height':2.0}]}]}
        allowed=['/spatial/open_spaces']
        normalized=normalize_local_repair_proposal(proposal,original,allowed)
        replacement=normalized['replacements'][0]['value'][0]
        self.assertEqual(replacement['intent_id'],'central_loop')
        self.assertNotIn('id',replacement)
        self.assertNotIn('purpose',replacement)
        repair=LocalRepair.model_validate(normalized)
        changed=apply_local_repair(original,repair,allowed)
        self.assertEqual(changed['spatial']['open_spaces'][0]['center'],[.2,.1])

    def test_unauthorized_remove_does_not_discard_other_layout_repairs(self):
        original={'placements':[{'id':'lamp','position':[0,0,0]},
                                {'id':'chair','position':[1,0,0]}]}
        proposal={'reason':'move chair','replacements':[
            {'path':'/placements/0/position','operation':'remove'},
            {'path':'/placements/1/position','value':[2,0,0]}]}
        allowed=['/placements/0/position','/placements/1/position']
        normalized=normalize_local_repair_proposal(proposal,original,allowed)
        repair=LocalRepair.model_validate(normalized)
        changed=apply_local_repair(original,repair,allowed)
        self.assertEqual(changed['placements'][0]['position'],[0,0,0])
        self.assertEqual(changed['placements'][1]['position'],[2,0,0])
        self.assertIn('Ignored 1 unauthorized remove',repair.reason)

    def test_spatial_conflict_signature_groups_scenarios_and_supported_contents(self):
        outline_data_value=outline_data()
        outline_data_value['objects'] += [
            {'id':'tea','semantic':'tea service','category':'contents','dimensions':[.5,.3,.1],
             'rigidity':'rigid','decomposition_reason':'A supported service item.',
             'required_features':['cup and pot'],'material_requirements':['ceramic'],'visual_importance':2,
             'parent':'base','mount':'top','acquisition':{'strategy':'parametric','reason':'Editable service proxy.'}},
            {'id':'chess','semantic':'chess set','category':'contents','dimensions':[.4,.4,.1],
             'rigidity':'rigid','decomposition_reason':'A supported game item.',
             'required_features':['board'],'material_requirements':['wood'],'visual_importance':2,
             'parent':'base','mount':'top','acquisition':{'strategy':'parametric','reason':'Editable board proxy.'}}]
        outline_data_value['edit_checks']=[{'parameter':'base_width','delta':.1,
                                            'intent':'widen the support and preserve supported items'}]
        outline={'objects':[{'id':obj['id'],'parent':obj.get('parent')}
                            for obj in outline_data_value['objects']],
                 'edit_checks':outline_data_value['edit_checks']}
        first={'status':'infeasible_current_choices','independent_conflict_groups':[
            ['baseline support footprint: tea/base','baseline supported content separation: tea/chess'],
            ['base_width support footprint: chess/base']]}
        second={'status':'infeasible_current_choices','independent_conflict_groups':[
            ['base_width support footprint: chess/base','baseline supported content separation: tea/chess']]}
        self.assertEqual(spatial_conflict_families(first,outline),['support_group:base'])
        self.assertEqual(spatial_conflict_families(first,outline),
                         spatial_conflict_families(second,outline))

    def test_repeated_support_group_targets_decomposition_with_component_paths(self):
        outline={'objects':[{'id':'table','parent':None},
            {'id':'lamp','parent':'table'},{'id':'books','parent':'table'},
            {'id':'chair','parent':None}]}
        issues=support_group_revision_issues(['support_group:table'],outline)
        self.assertEqual(len(issues),1)
        self.assertEqual(issues[0]['component_ids'],['table','lamp','books'])
        self.assertEqual(issues[0]['repair_target'],'decomposition')
        self.assertIn('/objects/0/dimensions',issues[0]['paths'])
        self.assertIn('/objects/1/support_footprint',issues[0]['paths'])

    def test_spatial_escalation_matches_repeated_conflict_families_with_minor_changes(self):
        original=['scene_bounds:room_shell','zone:zone_library_wall','support_group:reading_side_table',
                  'mounting:entry_mirror','approach_boundary:use_bookcase']
        revised=[*original,'furniture_separation:chair,table']
        novel=['scene_bounds:room_shell','furniture_separation:chair,table']
        self.assertTrue(repeated_spatial_conflict(revised,[original]))
        self.assertFalse(repeated_spatial_conflict(novel,[original]))

    def test_bottom_mount_follows_height_edit_and_stays_grounded(self):
        from general_scene import GeneralPlan,SceneObject,Part,compile_general,edit_general,judge_general_geometry
        plan=GeneralPlan(scene_id='underside',description='table with one central support',extent=(3,3,3),parameters={'height':.8},objects=[
            SceneObject(id='top',semantic='tabletop',size=[1,1,.1],position=[0,0,'height-.1'],
                        parts=[Part(id='slab',primitive='box',size=['w','d','h'],center=[0,0,'h/2'])]),
            SceneObject(id='leg',semantic='support under tabletop',size=[.2,.2,'height-.1'],parent='top',mount='bottom',grounded=True,
                        parts=[Part(id='post',primitive='box',size=['w','d','h'],center=[0,0,'h/2'])])])
        for candidate in (plan,edit_general(plan,'height',.1)):
            _,bounds,_=compile_general(candidate)
            self.assertAlmostEqual(bounds['leg'][0][2],0)
            self.assertAlmostEqual(bounds['leg'][1][2],bounds['top'][0][2])
            self.assertEqual(judge_general_geometry(candidate,bounds)['status'],'passed')
        plan.objects[1].mount='origin'
        _,bounds,_=compile_general(plan)
        self.assertEqual(judge_general_geometry(plan,bounds)['status'],'needs_revision')

    def test_infeasible_support_height_rejected_before_layout(self):
        data=outline_data();data['extent'][2]=.6
        with self.assertRaisesRegex(ValueError,'support-chain height'):Decomposition.model_validate(data)

    def test_named_shelf_height_waits_for_actual_recipe_and_still_checks_it(self):
        from general_scene import planned_envelope_bounds,judge_general_geometry
        data=outline_data();data['extent'][2]=1.2
        data['objects'][0]['dimensions']=['base_width',1,1.1]
        data['objects'][0]['internal_parts']=[{'id':'slab','semantic':'low display shelf',
            'rigidity':'parametric','role':'structure'}]
        data['objects'][1]['support_part']='slab'
        outline=Decomposition.model_validate(data)
        # Actual support at .2m fits although envelope top + child is 1.6m.
        recipe=Recipe.model_validate({'parts':[
            {'id':'slab','primitive':'box','size':['w','d',.2],'center':[0,0,.1]},
            {'id':'post','primitive':'box','size':[.1,.1,'h-.2'],'center':[0,0,'(h+.2)/2']}],
            'assembly':{'roots':['slab'],'relations':[{'kind':'supported_by','part':'post','target':'slab',
                'reason':'The rear upright stands on the low shelf.'}]}})
        plan=assemble_plan(outline,{'base':recipe},Layout.model_validate(layout_data()))
        executable,_=to_general_plan(plan)
        bounds=planned_envelope_bounds(executable)
        self.assertAlmostEqual(bounds['ornament'][1][2],.7)
        self.assertEqual(judge_general_geometry(executable,bounds)['status'],'passed')
        # A real high shelf must still fail the production geometry gate.
        executable.objects[0].parts[0].size=[1.5,1,1.1]
        executable.objects[0].parts[0].center=[0,0,.55]
        executable.objects[0].parts=executable.objects[0].parts[:1]
        executable.objects[0].assembly=None
        self.assertEqual(judge_general_geometry(executable,planned_envelope_bounds(executable))['status'],'needs_revision')

    def test_canopy_overhang_uses_contact_footprint(self):
        data=outline_data();data['objects'][1]['dimensions']=[2,2,.5]
        data['objects'][1]['support_footprint']=[.2,.2]
        data['objects'][1]['interface_notes']=['Verify the .2 metre base sits on the platform.']
        outline=Decomposition.model_validate(data)
        plan=assemble_plan(outline,{'base':Recipe.model_validate(recipe_data())},Layout.model_validate(layout_data()))
        self.assertEqual(to_general_plan(plan)[0].objects[1].support_footprint,(.2,.2))
        data['objects'][1]['support_footprint']=None
        with self.assertRaisesRegex(ValueError,'contact cannot fit'):Decomposition.model_validate(data)

    def test_named_parent_support_part_reaches_compiler_and_anchors_child(self):
        from general_scene import planned_envelope_bounds,judge_general_geometry
        data=outline_data()
        data['objects'][0]['internal_parts']=[{'id':'slab','semantic':'flat upper support slab',
                                                'rigidity':'parametric','role':'structure'}]
        data['objects'][1].update(support_part='slab',support_footprint=[.25,.25],
                                  interface_notes=['Flat ornament base seats on the slab top.'])
        outline=Decomposition.model_validate(data)
        plan=assemble_plan(outline,{'base':Recipe.model_validate(recipe_data())},
                           Layout.model_validate(layout_data()))
        executable,_=to_general_plan(plan)
        bounds=planned_envelope_bounds(executable)
        self.assertAlmostEqual(bounds['ornament'][0][2],.2)
        self.assertEqual(executable.objects[1].support_part,'slab')
        self.assertEqual(judge_general_geometry(executable,bounds)['status'],'passed')

    def test_compile_generates_relations_and_conservative_edit_closure(self):
        outline=Decomposition.model_validate(outline_data())
        plan=assemble_plan(outline,{'base':Recipe.model_validate(recipe_data())},Layout.model_validate(layout_data()))
        self.assertEqual([r.type for r in plan.relations],['parent','supported_by'])
        self.assertEqual(plan.edit_plans[0].affected_components,['base','ornament'])
        self.assertEqual(plan.components[1].source.mode,'auto')
        self.assertEqual(plan.components[1].parts,[])

    def test_unknown_boundary_rejected_in_minimal_reproduction(self):
        data=outline_data();data['clearances']=[{'a':'base','b':'scene_front_boundary','minimum_m':.8,'reason':'aisle'}]
        with self.assertRaisesRegex(ValueError,'real component IDs'):Decomposition.model_validate(data)

    def test_offset_recipe_was_size_valid_but_violates_centered_envelope(self):
        outline=Decomposition.model_validate(outline_data())
        recipe=recipe_data();recipe['parts'][0]['center']=[.5,0,.1]
        with self.assertRaisesRegex(ValueError,'centered envelope') as caught:
            validate_recipe(outline,outline.objects[0],Recipe.model_validate(recipe))
        bounds=next(issue for issue in caught.exception.measurements['issues']
                    if issue['code']=='component_bounds' and issue['scenario']=='baseline')
        self.assertEqual(bounds['measured'],[-.25,1.25])
        self.assertEqual(bounds['expected'],[-.75,.75])
        self.assertEqual(bounds['paths'],['/parts/0/size','/parts/0/center','/parts/0/rotation'])

    def test_repair_cannot_modify_ids_or_sourcing_and_is_atomic(self):
        original=layout_data();before=copy.deepcopy(original)
        patch=LocalRepair(reason='invalid change',replacements=[{'path':'/placements/0/id','value':'new'}])
        with self.assertRaisesRegex(ValueError,'authorized fields'):
            apply_local_repair(original,patch,['/placements/0/position'])
        self.assertEqual(original,before)

    def test_explicit_repair_removes_reported_extra_field(self):
        original={'required':1,'unsupported':[]}
        patch=LocalRepair(reason='remove schema extra',replacements=[
            {'path':'/unsupported','operation':'remove'}])
        with self.assertRaisesRegex(ValueError,'explicitly reported'):
            apply_local_repair(original,patch,['/unsupported'])
        self.assertEqual(apply_local_repair(original,patch,['/unsupported'],
                                            removable_paths=['/unsupported']),{'required':1})
        self.assertEqual(original,{'required':1,'unsupported':[]})

    def test_batch_repair_deletes_array_items_after_original_index_replacements(self):
        original={'objects':[{'id':str(i),'internal_parts':[{'form':'box'},{'form':'panel'}]}
                            for i in range(35)]}
        patch=LocalRepair(reason='Remove the unused object and fix the plinth parts.',replacements=[
            {'path':'/objects/31','operation':'remove'},
            {'path':'/objects/34/internal_parts/1/form','value':None}])
        changed=apply_local_repair(original,patch,['/objects'],
                                   removable_paths=['/objects/31'])
        self.assertEqual(changed['objects'][33]['id'],'34')
        self.assertIsNone(changed['objects'][33]['internal_parts'][1]['form'])
        self.assertEqual(original['objects'][34]['internal_parts'][1]['form'],'panel')

    def test_stage_repair_can_remove_only_pydantic_reported_extra_field(self):
        import asyncio
        from staged_planner import replacement_paths
        class TinyPlan(StrictModel):
            required: str
        calls=[]
        async def complete(stage,instructions,payload):
            calls.append(stage)
            if stage=='probe_0':return {'required':'kept','extra':None}
            self.assertEqual(stage,'probe_repair_1')
            self.assertEqual(payload['removable_paths'],['/extra'])
            return {'reason':'Remove the exact unsupported field.',
                    'replacements':[{'path':'/extra','operation':'remove'}]}
        with tempfile.TemporaryDirectory() as directory:
            planner=StagedScenePlanner('unused',Path(directory),'probe',complete=complete,max_repairs=1)
            result=asyncio.run(planner.stage('probe',TinyPlan,'Validate a strict tiny plan.',{},lambda item:item,replacement_paths))
        self.assertEqual(result.required,'kept')
        self.assertEqual(calls,['probe_0','probe_repair_1'])

    def test_coarse_layout_repair_receives_conditional_room_experience_as_agent_instruction(self):
        import asyncio
        calls=[]
        async def complete(stage,instructions,payload):
            calls.append(stage)
            if stage=='coarse_layout_0':return layout_data()
            self.assertEqual(stage,'coarse_layout_repair_1')
            self.assertIn('Stable conditional system priors for room layout',instructions)
            self.assertIn('edge gap = center distance minus both projected half-widths',instructions)
            self.assertIn('recompute its CENTER from that fixed pivot',instructions)
            return {'reason':'Move the supported placement clear of the invalid area.',
                    'replacements':[{'path':'/placements/0/position','value':[1,0,0]}]}
        validation_calls=0
        def validate(item):
            nonlocal validation_calls
            if validation_calls==0:
                validation_calls+=1
                raise ValueError('coarse candidate intersects a protected room region')
            return item
        with tempfile.TemporaryDirectory() as directory:
            planner=StagedScenePlanner('unused',Path(directory),'room',complete=complete,max_repairs=1)
            plan=asyncio.run(planner.stage('coarse_layout',Layout,
                'Place the entire room layout.',{'brief':'高中生卧室，榻榻米、书桌和窗户'},
                validate,lambda _data:['/placements/0/position']))
        self.assertEqual(plan.placements[0].position,(1,0,0))
        self.assertEqual(calls,['coarse_layout_0','coarse_layout_repair_1'])

    def test_joint_spatial_repair_guidance_uses_nested_paths_and_room_experience(self):
        instructions=local_repair_stage_guidance('spatial_reconstruction_candidate',
            '高中生卧室，榻榻米、书桌、窗户和台灯')
        self.assertIn('zones at /spatial/zones',instructions)
        self.assertIn('routes at /spatial/routes',instructions)
        self.assertIn('placements under /layout/placements',instructions)
        self.assertIn('Never use top-level /zones',instructions)
        self.assertIn('Tatami placement:',instructions)
        self.assertIn('Lighting placement:',instructions)

    def test_open_space_repair_guidance_removes_all_nonprogrammatic_clearance_areas(self):
        guidance=_repair_guidance('design_feasibility',
            'nonprogrammatic_open_space: protected open_spaces cannot encode ordinary central open floor')
        self.assertIn('Remove EVERY generic central/open floor',guidance)
        self.assertIn('chair pull-back',guidance)
        self.assertIn('window-frontage',guidance)
        self.assertIn('in one replacement',guidance)

    def test_failed_layout_repairs_only_position_and_preserves_recipe(self):
        calls=[]
        async def complete(stage,instructions,payload):
            calls.append(stage)
            if stage=='request_contract_0':return {'asset_boundary':'single_asset','quality_target':'prototype'}
            if stage=='decomposition_0':return outline_data()
            if stage=='recipe_base_0':return recipe_data()
            if stage=='layout_0':
                data=layout_data();data['placements'][1]['position']=[0,0,.2];return data
            if stage=='layout_repair_1':
                self.assertIn('/placements/1/position',payload['allowed_paths'])
                self.assertNotIn('/objects',payload['allowed_paths'])
                return {'reason':'top mount already adds support height','replacements':[{'path':'/placements/1/position','value':[0,0,0]}]}
            raise AssertionError(stage)
        with tempfile.TemporaryDirectory() as directory:
            plan=StagedScenePlanner('unused',Path(directory),'test',complete=complete).plan('test')
            events=json.loads((Path(directory)/'stage_events.json').read_text())
            self.assertEqual(plan.components[1].position,(0,0,0))
            self.assertEqual(calls,['request_contract_0','decomposition_0','recipe_base_0','layout_0','layout_repair_1'])
            self.assertEqual([e['paths'] for e in events if 'paths' in e],[['/placements/1/position']])

    def test_unrepairable_layout_stops_after_repeated_failed_state(self):
        async def complete(stage,instructions,payload):
            if stage=='request_contract_0':return {'asset_boundary':'single_asset','quality_target':'prototype'}
            if stage.startswith('decomposition'):return outline_data()
            if stage.startswith('recipe'):return recipe_data()
            if stage=='layout_0':
                data=layout_data();data['placements'][1]['position']=[0,0,2];return data
            return {'reason':'unauthorized attempt to drop object','replacements':[{'path':'/placements','value':[]}]}
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError,'repeated failed state'):
                StagedScenePlanner('unused',Path(directory),'test',complete=complete,max_repairs=3).plan('test')
            self.assertFalse((Path(directory)/'validated_plan.json').exists())

    def test_layout_stops_when_repair_changes_measurements_but_resolves_no_issue_family(self):
        calls=[]
        class DriftingMeasurementFailure(ValueError):
            def __init__(self,attempt):
                self.measurements={'stage':'layout_probe','status':'needs_revision','issues':[{
                    'id':'support_offset',
                    'code':'support_contact','constraint_id':'support_offset',
                    'component_ids':['ornament','base'],'paths':['/placements/1/position'],
                    'scenario':'baseline','evidence':f'measured offset is {attempt/100:.2f}m',
                    'measured':{'offset_m':attempt/100}}], 'blocked_checks':[],
                    'coverage':'All relevant checks'}
                super().__init__(self.measurements['issues'][0]['evidence'])
        def validate_with_drifting_measurement(layout):
            offset=float(layout.placements[1].position[2])
            raise DriftingMeasurementFailure(round(offset*100))
        async def complete(stage,instructions,payload):
            calls.append(stage)
            if stage=='layout_probe_repair_1':
                self.assertIn('Address all independent actionable issues in one coherent patch',
                              payload['diagnostic_report']['repair_instruction'])
                return {'reason':'Reduce the measured support offset.','replacements':[
                    {'path':'/placements/1/position','value':[0,0,.1]}]}
            self.fail(f'No model call is allowed without resolving an issue family: {stage}')
        with tempfile.TemporaryDirectory() as directory:
            import asyncio
            planner=StagedScenePlanner('unused',Path(directory),'test',complete=complete,max_repairs=20)
            initial=layout_data();initial['placements'][1]['position']=[0,0,.2]
            with self.assertRaisesRegex(ValueError,'same issue families without resolution'):
                asyncio.run(planner.stage('layout_probe',Layout,'layout rules',{'brief':'test'},
                    validate_with_drifting_measurement,replacement_paths,initial_candidate=initial))
            self.assertEqual(calls,['layout_probe_repair_1'])
            events=json.loads((Path(directory)/'stage_events.json').read_text())
            self.assertTrue(any(event.get('repair_progress')=='no_issue_family_resolved' for event in events),events)
            stopped=next(event for event in events if event.get('repair_progress')=='no_issue_family_resolved')
            self.assertEqual(stopped['status'],'failed')

    def test_resume_reuses_only_matching_validated_stages(self):
        async def complete(stage,instructions,payload):
            if stage=='request_contract_0':return {'asset_boundary':'single_asset','quality_target':'prototype'}
            if stage=='decomposition_0':return outline_data()
            if stage=='recipe_base_0':return recipe_data()
            if stage=='layout_0':return layout_data()
            raise AssertionError(stage)
        async def unexpected(*args):raise AssertionError('Matching checkpoint should prevent model call')
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory)
            first=StagedScenePlanner('unused',base/'first','first',complete=complete).plan('same brief')
            second=StagedScenePlanner('unused',base/'second','second',complete=unexpected,
                                      resume_runtime=base/'first').plan('same brief')
            self.assertEqual(first.model_dump(),second.model_dump())
            events=json.loads((base/'second/stage_events.json').read_text())
            self.assertTrue(all(e['status']=='reused_and_revalidated' for e in events))
            with self.assertRaises(AssertionError):
                StagedScenePlanner('unused',base/'third','third',complete=unexpected,
                                  resume_runtime=base/'first').plan('different brief')

    def test_failed_agent_candidate_is_restored_only_as_a_revalidation_seed(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime=Path(directory)
            (runtime/'spatial_plan_failure_0.json').write_text(json.dumps(
                {'candidate':{'zones':[{'id':'old'}]},'error':'first rejection'}),encoding='utf-8')
            (runtime/'spatial_plan_failure_2.json').write_text(json.dumps(
                {'candidate':{'zones':[{'id':'latest'}]},'error':'latest rejection','request_hash':'old'}),encoding='utf-8')
            restored=latest_failed_stage_candidate(runtime,'spatial_plan')
            self.assertEqual(restored['candidate']['zones'][0]['id'],'latest')
            self.assertEqual(restored['error'],'latest rejection')
            self.assertTrue(restored['path'].endswith('spatial_plan_failure_2.json'))

    def test_spatial_checkpoint_rejects_failed_seed_with_stale_component_ids(self):
        from staged_planner import spatial_checkpoint_mismatches
        from test_scene_spatial import contract_data
        outline=Decomposition.model_validate(outline_data())
        candidate=contract_data()
        candidate['zones'][0]['component_ids'].append('window_unit')
        mismatch=spatial_checkpoint_mismatches(candidate,outline,None)
        self.assertEqual(len(mismatch),1)
        self.assertIn('absent from the current decomposition',mismatch[0])
        self.assertIn('window_unit',mismatch[0])

    def test_final_layout_can_revalidate_matching_checkpoint_before_coarse_seed(self):
        import asyncio
        class TinyPlan(StrictModel):
            value: int
        async def unexpected(*args):raise AssertionError('No model call needed')
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory)
            first=StagedScenePlanner('unused',base/'first','first',complete=unexpected)
            asyncio.run(first.stage('layout',TinyPlan,'same',{},lambda item:item,
                initial_candidate={'value':7}))
            resumed=StagedScenePlanner('unused',base/'resumed','resumed',complete=unexpected,resume_runtime=base/'first')
            item=asyncio.run(resumed.stage('layout',TinyPlan,'same',{},lambda item:item,
                initial_candidate={'value':1},prefer_matching_checkpoint=True))
            self.assertEqual(item.value,7)
            default=StagedScenePlanner('unused',base/'default','default',complete=unexpected,resume_runtime=base/'first')
            item=asyncio.run(default.stage('layout',TinyPlan,'same',{},lambda item:item,
                initial_candidate={'value':1}))
            self.assertEqual(item.value,1)
            changed=StagedScenePlanner('unused',base/'changed','changed',complete=unexpected,resume_runtime=base/'first')
            item=asyncio.run(changed.stage('layout',TinyPlan,'different',{},lambda item:item,
                initial_candidate={'value':2},prefer_matching_checkpoint=True))
            self.assertEqual(item.value,2)

    def test_resume_failed_stage_repairs_without_regenerating_accepted_stages(self):
        async def first(stage,instructions,payload):
            if stage=='request_contract_0':return {'asset_boundary':'single_asset','quality_target':'prototype'}
            if stage=='decomposition_0':return outline_data()
            if stage=='recipe_base_0':return recipe_data()
            if stage=='layout_0':
                data=layout_data();data['placements'][1]['position']=[0,0,.2];return data
            raise AssertionError(stage)
        async def resume(stage,instructions,payload):
            self.assertEqual(stage,'layout_repair_0')
            return {'reason':'Restore contact','replacements':[{'path':'/placements/1/position','value':[0,0,0]}]}
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory)
            with self.assertRaisesRegex(AssertionError,'layout_repair_1'):
                StagedScenePlanner('unused',base/'first','first',complete=first).plan('same')
            plan=StagedScenePlanner('unused',base/'resume','resume',complete=resume,resume_runtime=base/'first').plan('same')
            self.assertEqual(plan.components[1].position,(0,0,0))

    def test_source_replan_reuses_full_validated_parametric_recipe(self):
        import asyncio
        from staged_planner import Decomposition,Recipe
        outline=Decomposition.model_validate(outline_data())
        recipe=Recipe.model_validate(recipe_data())
        planned=assemble_plan(outline,{'base':recipe},Layout.model_validate(layout_data()))
        async def unexpected(*_):raise AssertionError('A validated source-only recipe should be reused')
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'previous';root.mkdir()
            (root/'validated_plan.json').write_text(
                json.dumps(planned.model_dump(mode='json',by_alias=True)),encoding='utf-8')
            workspace=Path(directory)/'replan'
            planner=StagedScenePlanner('unused',workspace,'replan',complete=unexpected,
                resume_runtime=root,replan_feedback={'validated_parametric_specs':[
                    outline.objects[0].model_dump(mode='json')]})
            result=asyncio.run(planner.component_recipe(outline,outline.objects[0],'same brief'))
            events=json.loads((workspace/'stage_events.json').read_text())
        self.assertEqual(result.model_dump(),recipe.model_dump())
        self.assertEqual(events[0]['status'],'reused_and_revalidated')
        self.assertEqual(events[0]['reuse_basis'],'validated_parametric_spec')

if __name__=='__main__':unittest.main(verbosity=2)
