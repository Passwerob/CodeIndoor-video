"""Behavioral checks for complete diagnostics, precise pointers and one batch repair."""
import asyncio
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
sys.path.insert(0,str(ROOT/'experiments'))
from planning_diagnostics import (PlanningValidationError, diagnostic_families,
    resolved_diagnostic_families, failure_report)
from scene_spatial import SpatialContract, validate_spatial_contract
from staged_planner import (Decomposition, Recipe, Layout, StagedScenePlanner,
    validate_recipe, validate_request_contract, RequestContract, coarse_preflight,
    CirculationProgram, preserve_circulation_program,apply_circulation_program,
    spatial_checkpoint_mismatches,layout_checkpoint_mismatches,human_access_failure,
    SpatialChoicesInfeasible,DetailedPlanningRevisionRequired)
from test_staged_planner import outline_data,recipe_data,layout_data
from test_scene_spatial import contract_data,probe_objects


class PlanningDiagnosticsTests(unittest.TestCase):
    def test_issue_families_ignore_coordinate_drift_but_keep_component_and_scope(self):
        first={'issues':[{'code':'route_blocked','constraint_id':'reach_wardrobe',
            'component_ids':['wardrobe','chair'],'paths':['/route_points/reach_wardrobe'],
            'evidence':'blocked at y=0.4','measured':{'clearance_m':.02}}]}
        moved={'issues':[{'code':'route_blocked','constraint_id':'reach_wardrobe',
            'component_ids':['chair','wardrobe'],'paths':['/route_points/reach_wardrobe'],
            'evidence':'blocked at y=0.6','measured':{'clearance_m':.08}}]}
        different={'issues':[{'code':'route_blocked','constraint_id':'reach_tatami',
            'component_ids':['chair','tatami'],'paths':['/route_points/reach_tatami'],
            'evidence':'blocked at y=0.6'}]}
        self.assertEqual(diagnostic_families(first),diagnostic_families(moved))
        self.assertNotEqual(diagnostic_families(first),diagnostic_families(different))
        self.assertEqual(resolved_diagnostic_families(diagnostic_families(first),
                         diagnostic_families(moved)),set())
        self.assertEqual(len(resolved_diagnostic_families(diagnostic_families(first),
                         diagnostic_families({}))),1)

    def test_repair_pointers_do_not_repeat_every_descendant_of_an_exact_pointer(self):
        from planning_diagnostics import repair_report
        report={'issues':[{'code':'envelope','paths':['/parts'],'evidence':'Repair full envelope'},
                          {'code':'joint','paths':['/parts/0'],'evidence':'Repair one member'}]}
        result=repair_report(report,['/parts','/parts/0','/parts/0/size','/parts/0/center','/parts/1'])
        self.assertEqual(result['issues'][0]['allowed_repair_paths'],['/parts'])
        self.assertEqual(result['issues'][1]['allowed_repair_paths'],['/parts/0'])

    def test_resume_discards_spatial_and_layout_candidates_with_stale_component_ids(self):
        outline=Decomposition.model_validate(outline_data())
        spatial=contract_data()
        spatial['zones'][0]['component_ids']=['base','ornament']
        self.assertEqual(spatial_checkpoint_mismatches(spatial,outline,None),[])
        stale_spatial=copy.deepcopy(spatial)
        stale_spatial['zones'][0]['component_ids']=['base','old_sofa']
        self.assertTrue(any('absent from the current decomposition' in item
                            for item in spatial_checkpoint_mismatches(stale_spatial,outline,None)))

        layout=layout_data()
        self.assertEqual(layout_checkpoint_mismatches(layout,outline),[])
        stale_layout=copy.deepcopy(layout)
        stale_layout['placements'][1]['id']='old_sofa'
        self.assertEqual(layout_checkpoint_mismatches(stale_layout,outline),
                         ['placement IDs differ from the current decomposition'])

    def test_spatial_revision_cannot_narrow_approved_access_or_route(self):
        spatial=SpatialContract.model_validate(contract_data())
        spatial.access_areas[0].side='front'
        spatial.routes[0].id='reach_base'
        circulation=CirculationProgram.model_validate({'approaches':[{'owner_id':'base','side':'front',
            'along_face':0,'size':[1.1,.9],'final_route_width':.8,
            'reason':'A person stands at the front to inspect the display.'}]})
        contract=RequestContract(asset_boundary='scene',quality_target='realistic')
        self.assertIs(preserve_circulation_program(spatial,circulation,contract),spatial)
        spatial.access_areas[0].size=(1.2,.9)
        self.assertIs(preserve_circulation_program(spatial,circulation,contract),spatial)
        spatial.access_areas[0].size=(1.1,.9)
        spatial.routes[0].width=.3
        with self.assertRaisesRegex(ValueError,'narrowed the approved 0.800m'):
            preserve_circulation_program(spatial,circulation,contract)
        spatial.routes[0].width=.8;spatial.access_areas[0].size=(1.1,.3)
        with self.assertRaisesRegex(ValueError,'weakened the approved'):
            preserve_circulation_program(spatial,circulation,contract)

    def test_spatial_revision_rebinds_authoritative_circulation_instead_of_repairing_it(self):
        spatial=SpatialContract.model_validate(contract_data())
        raw=outline_data();raw['objects'][0]['access_required']=False
        raw['objects'][1]['access_required']=True
        outline=Decomposition.model_validate(raw)
        spatial.access_areas[0].side='back';spatial.access_areas[0].size=(.2,.2)
        spatial.routes[0].width=.1
        circulation=CirculationProgram.model_validate({'approaches':[{'owner_id':'base','side':'front',
            'along_face':0,'size':[1.1,.9],'final_route_width':.8,
            'reason':'A person stands at the front to inspect the display.'}]})
        contract=RequestContract(asset_boundary='scene',quality_target='realistic')
        rebound=apply_circulation_program(spatial,circulation,outline,contract)
        self.assertEqual((rebound.access_areas[0].id,rebound.access_areas[0].side,rebound.access_areas[0].size),
            ('use_base','front',(1.1,.9)))
        self.assertEqual((rebound.routes[0].id,rebound.routes[0].width),('main_aisle',.8))
        self.assertEqual(spatial.routes[0].width,.1)
        unknown_width=RequestContract.model_validate({'asset_boundary':'scene','quality_target':'realistic',
            'scene_use':{'enclosure':'enclosed','minimum_route_width_m':None}})
        self.assertEqual(apply_circulation_program(spatial,circulation,outline,unknown_width).routes[0].width,.8)
        explicit_width=RequestContract.model_validate({'asset_boundary':'scene','quality_target':'realistic',
            'scene_use':{'enclosure':'enclosed','minimum_route_width_m':.9}})
        self.assertEqual(apply_circulation_program(spatial,circulation,outline,explicit_width).routes[0].width,.9)

    def test_circulation_rebinds_composition_links_after_replacing_routes(self):
        from types import SimpleNamespace
        from scene_composition import validate_lowering

        outline=Decomposition.model_validate(outline_data())
        outline.design_bindings={'sleep_choice':['base'],'study_choice':['ornament']}
        design=SimpleNamespace(composition=SimpleNamespace(
            groups=[
                SimpleNamespace(id='sleep',choice_ids=['sleep_choice'],anchor_choice_id='sleep_choice',relationships=[]),
                SimpleNamespace(id='study',choice_ids=['study_choice'],anchor_choice_id='study_choice',relationships=[]),
            ],
            links=[SimpleNamespace(id='sleep_study',a='sleep',b='study',binding_mode='circulation')],
            open_spaces=[],views=[]))
        spatial=SpatialContract.model_validate({
            'zones':[{'id':'room','purpose':'room use','center':[0,0],'size':[4,4],
                      'component_ids':['base','ornament']}],
            'openings':[{'id':'door','kind':'entrance','wall':'x_max','offset':0,'width':1,
                         'height':2.05,'approach_depth':.7,'purpose':'room entrance'}],
            'access_areas':[
                {'id':'old_base','owner_id':'base','purpose':'old access','center':[0,0],
                 'size':[1,.8],'side':'front'},
                {'id':'old_ornament','owner_id':'ornament','purpose':'old access','center':[0,0],
                 'size':[1,.8],'side':'front'}],
            'routes':[
                {'id':'old_base_route','start':'door','end':'old_base','points':[[1.5,0],[0,0]],
                 'width':.4,'purpose':'old route'},
                {'id':'old_ornament_route','start':'door','end':'old_ornament','points':[[1.5,0],[0,0]],
                 'width':.4,'purpose':'old route'}],
            'composition_groups':[
                {'group_id':'sleep','component_ids':['base'],'anchor_id':'base'},
                {'group_id':'study','component_ids':['ornament'],'anchor_id':'ornament'}],
            'composition_links':[{'link_id':'sleep_study','route_ids':['old_base_route','old_ornament_route']}],
        })
        circulation=CirculationProgram.model_validate({'approaches':[
            {'owner_id':owner,'side':'front','along_face':0,'size':[1,.8],
             'final_route_width':.6,'reason':f'Approach {owner} from the room entrance.'}
            for owner in ('base','ornament')]})
        contract=RequestContract(asset_boundary='scene',quality_target='realistic')

        rebound=apply_circulation_program(spatial,circulation,outline,contract,design)

        link=rebound.composition_links[0]
        self.assertEqual(set(link.route_ids),{'reach_ornament'})
        self.assertTrue(set(link.route_ids)<={route.id for route in rebound.routes})
        validate_lowering(rebound,design,outline)

    def test_prompt_evidence_keeps_changed_measurements_and_all_conflict_scenarios(self):
        from planning_diagnostics import prompt_evidence
        issue={'code':'gap','evidence':'Clearance is insufficient','paths':['/placements/0'],
               'component_ids':['base'],'measured':.1,'expected':.3}
        report={'stage':'layout','status':'needs_revision','issues':[
            {**issue,'id':'a','scenario':'baseline'},
            {**issue,'id':'b','scenario':'width'},
            {**issue,'id':'c','scenario':'height','measured':.05}],
            'blocked_checks':[], 'numerical_solver':{'status':'infeasible_current_choices',
                'orientation_proposals':{'base':{'to_world_deg':90}},
                'independent_conflict_groups':[['baseline separation: a/b','baseline boundary: a']],
                'candidate':{'large':'layout'},'incompatible_constraints':[
                    'baseline separation: a/b','width separation: a/b','height boundary: a']}}
        before=copy.deepcopy(report)
        evidence=prompt_evidence(report,scenario_names=['width','height'])
        self.assertEqual(report,before)
        self.assertEqual(len(evidence['issues']),2)
        self.assertEqual(evidence['issues'][0]['scenarios'],['baseline','width'])
        self.assertEqual(evidence['issues'][0]['source_ids'],['a','b'])
        self.assertEqual(evidence['issues'][1]['measured'],.05)
        conflicts=evidence['numerical_solver']['incompatible_constraints']
        self.assertEqual(conflicts,[{'constraint':'separation: a/b','scenarios':['baseline','width']},
            {'constraint':'boundary: a','scenarios':['height']}])
        self.assertNotIn('candidate',evidence['numerical_solver'])
        self.assertEqual(evidence['numerical_solver']['orientation_proposals'],{'base':{'to_world_deg':90}})
        self.assertEqual(evidence['numerical_solver']['independent_conflict_groups'],
                         [['baseline separation: a/b','baseline boundary: a']])

    def test_layout_access_blockage_does_not_reopen_human_use_program(self):
        spatial=SpatialContract.model_validate(contract_data())
        evidence={'issues':[{'code':'viewing','evidence':'Reserved area viewing is blocked by obstacle'}],
            'numerical_solver':{'independent_conflict_groups':[
                ['baseline usable approach boundary: viewing','baseline scene bounds: base']]}}
        self.assertFalse(human_access_failure(evidence,spatial))
        evidence['numerical_solver']['independent_conflict_groups']=[
            ['baseline usable approach boundary: viewing']]
        self.assertTrue(human_access_failure(evidence,spatial))

    def test_solver_feedback_reaches_layout_agent_before_spatial_replan(self):
        bad=layout_data();bad['placements'][0]['position']=[0,0,.25]
        report={'stage':'coarse_layout','status':'needs_revision','coverage':'all checks',
            'issues':[{'id':'a','code':'grounded','evidence':'Base must sit on the floor.',
                'paths':['/placements/0/position'],'component_ids':['base'],'scenario':'baseline'}],
            'blocked_checks':[],'numerical_solver':{'status':'infeasible_current_choices',
                'independent_conflict_groups':[['baseline scene bounds: base']]}}
        calls=[]
        async def complete(stage,instructions,payload):
            calls.append(stage)
            if stage=='coarse_layout_0':return bad
            if stage=='coarse_layout_repair_1':
                self.assertEqual(payload['diagnostic_report']['numerical_solver']['status'],
                    'infeasible_current_choices')
                return {'reason':'Move the base back to the grounded floor origin.',
                    'replacements':[{'path':'/placements/0/position','value':[0,0,0]}]}
            self.fail(f'Unexpected stage {stage}; expected one solver-guided layout repair.')
        def validate(candidate):
            if candidate.placements[0].position[2]!=0:
                raise SpatialChoicesInfeasible(report)
            return candidate
        with tempfile.TemporaryDirectory() as directory:
            result=asyncio.run(StagedScenePlanner('unused',directory,'probe',complete=complete).stage(
                'coarse_layout',Layout,'Place all components.',{},validate,
                lambda _: ['/placements/0/position']))
        self.assertEqual(result.placements[0].position,(0,0,0))
        self.assertEqual(calls,['coarse_layout_0','coarse_layout_repair_1'])

    def test_repeated_solver_conflict_escalates_after_layout_agent_cannot_change_it(self):
        bad=layout_data();bad['placements'][0]['position']=[1,0,0]
        report={'stage':'coarse_layout','status':'needs_revision','coverage':'all checks',
            'issues':[{'id':'a','code':'bounds','evidence':'Base is outside the permitted region.',
                'paths':['/placements/0/position'],'component_ids':['base'],'scenario':'baseline'}],
            'blocked_checks':[],'numerical_solver':{'status':'infeasible_current_choices',
                'incompatible_constraints':['baseline scene bounds: base']}}
        calls=[]
        async def complete(stage,instructions,payload):
            calls.append(stage)
            if stage=='coarse_layout_0':return bad
            if stage=='coarse_layout_repair_1':
                return {'reason':'Move the base partway back toward the permitted region.',
                    'replacements':[{'path':'/placements/0/position','value':[.5,0,0]}]}
            self.fail(f'Unexpected stage {stage}; the unchanged solver conflict should escalate.')
        def validate(candidate):
            if candidate.placements[0].position[0] != 0:
                raise SpatialChoicesInfeasible(report)
            return candidate
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(SpatialChoicesInfeasible):
                asyncio.run(StagedScenePlanner('unused',directory,'repeat',complete=complete).stage(
                    'coarse_layout',Layout,'Place all components.',{},validate,
                    lambda _: ['/placements/0/position']))
        self.assertEqual(calls,['coarse_layout_0','coarse_layout_repair_1'])

    def test_repeated_supported_content_conflict_escalates_to_decomposition(self):
        bad=layout_data();bad['placements'][1]['position']=[.4,0,0]
        report={'stage':'coarse_layout','status':'needs_revision','coverage':'all checks',
            'issues':[{'id':'a','code':'support_footprint','evidence':'Supported content does not fit its surface.',
                'paths':['/placements/1/position'],'component_ids':['base','ornament'],'scenario':'baseline'}],
            'blocked_checks':[],'numerical_solver':{'status':'infeasible_current_choices',
                'independent_conflict_groups':[['baseline support footprint: ornament/base']]}}
        calls=[]
        async def complete(stage,instructions,payload):
            calls.append(stage)
            if stage=='coarse_layout_0':return bad
            if stage=='coarse_layout_repair_1':
                return {'reason':'Move the supported item to an alternate surface position.',
                    'replacements':[{'path':'/placements/1/position','value':[.2,0,0]}]}
            self.fail(f'Unexpected stage {stage}; the stable support conflict should reach decomposition.')
        def validate(candidate):raise SpatialChoicesInfeasible(report)
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(DetailedPlanningRevisionRequired) as caught:
                asyncio.run(StagedScenePlanner('unused',directory,'support',complete=complete).stage(
                    'coarse_layout',Layout,'Place the support group.',
                    {'frozen_decomposition':outline_data()},validate,
                    lambda _: ['/placements/1/position']))
        support_issue=next(issue for issue in caught.exception.measurements['issues']
                           if issue['code']=='repeated_support_group_conflict')
        self.assertEqual(support_issue['repair_target'],'decomposition')
        self.assertEqual(support_issue['component_ids'],['base','ornament'])
        self.assertEqual(calls,['coarse_layout_0','coarse_layout_repair_1'])

    def test_spatial_independent_graph_errors_are_reported_together(self):
        data=contract_data()
        data['zones'][0]['size']=[8,4]
        data['zones'][0]['component_ids']=[]
        data['access_areas'][0]['owner_id']='unknown'
        data['openings'][0].update(sill_height=.1,height=4)
        data['routes'][0]['start']='missing'
        data['routes'][0]['points'][0]=[0,-2]
        with self.assertRaises(PlanningValidationError) as caught:
            validate_spatial_contract(SpatialContract.model_validate(data),probe_objects(),[4,4,3])
        report=caught.exception.measurements
        codes={item['code'] for item in report['issues']}
        self.assertTrue({'zone_extent','zone_membership','access_area','missing_access',
            'opening_height','entrance_floor','route_endpoints','route_extent','disconnected_access'}<=codes)
        self.assertTrue(all(item['paths'] for item in report['issues']))

    def test_decomposition_preserves_nested_diagnostic_details(self):
        data=outline_data()
        data['objects'][0]['parent']='missing'
        data['objects'][1]['rigidity']='parametric'
        data['zones'][0]['component_ids']=['invented']
        data['edit_checks']=[{'parameter':'missing_parameter','delta':0,'intent':'invalid'}]
        try:Decomposition.model_validate(data)
        except ValueError as failure:report=failure_report(failure,'decomposition')
        else:self.fail('Invalid decomposition was accepted')
        self.assertTrue({'missing_parent','external_rigidity','coverage_ids','edit_parameter'}<=
                        {item['code'] for item in report['issues']})
        self.assertTrue(any(item['check']=='support_chains' for item in report['blocked_checks']))

    def test_request_coverage_counts_both_missing_contents(self):
        outline=Decomposition.model_validate(outline_data())
        contract=RequestContract(asset_boundary='scene',quality_target='realistic',contents=[
            {'tag':tag,'description':tag,'minimum_instances':2} for tag in ['books','vases']])
        with self.assertRaises(PlanningValidationError) as caught:validate_request_contract(outline,contract)
        self.assertEqual(sum(item['code']=='content_count' for item in caught.exception.measurements['issues']),2)
        self.assertIn('quality_target',{item['code'] for item in caught.exception.measurements['issues']})

    def test_recipe_reports_inventory_dimensions_and_all_edits_after_frozen_rigidity_lowering(self):
        from staged_planner import _lower_frozen_assembly_relations
        data=outline_data()
        data['parameters'].update(alpha=.1,beta=.1)
        data['edit_checks']=[{'parameter':p,'delta':.02,'intent':'editable support'} for p in ['alpha','beta']]
        data['objects'][0]['internal_parts']=[
            {'id':'slab','semantic':'rigid slab','rigidity':'rigid','role':'structure'},
            {'id':'required_missing','semantic':'required part','rigidity':'rigid','role':'detail'}]
        outline=Decomposition.model_validate(data)
        recipe=recipe_data()
        recipe['parts'][0]['size']=['w-.1','d-.1','h-.05']
        recipe['parts'][0]['center']=[0,0,'(h-.05)/2']
        lowered=_lower_frozen_assembly_relations(outline.objects[0],Recipe.model_validate(recipe))
        self.assertEqual(next(part for part in lowered.parts if part.id=='slab').rigidity,'rigid')
        with self.assertRaises(ValueError) as caught:validate_recipe(outline,outline.objects[0],Recipe.model_validate(recipe))
        issues=caught.exception.measurements['issues']
        self.assertIn('missing_part',{i['code'] for i in issues})
        # Rigidity is copied from the already accepted decomposition before
        # geometry checks, so the recipe stage need not spend a model turn on it.
        self.assertNotIn('part_rigidity',{i['code'] for i in issues})
        self.assertEqual({i['scenario'] for i in issues if i['code']=='component_size'},
                         {'baseline','alpha','beta'})

    def test_recipe_size_is_checked_before_acquisition_on_every_edit(self):
        data=outline_data()
        data['edit_checks']=[{'parameter':'base_width','delta':.2,'intent':'widen base'}]
        outline=Decomposition.model_validate(data)
        recipe=recipe_data();recipe['parts'][0]['size']=['w-.1','d','h']
        with self.assertRaises(ValueError) as caught:validate_recipe(outline,outline.objects[0],Recipe.model_validate(recipe))
        sizes=[i for i in caught.exception.measurements['issues'] if i['code']=='component_size']
        self.assertEqual({i['scenario'] for i in sizes},{'baseline','base_width'})
        self.assertTrue(all('measured' in i and 'expected' in i for i in sizes))

    def test_recipe_reports_invalid_expressions_in_multiple_parts(self):
        outline=Decomposition.model_validate(outline_data())
        recipe=recipe_data()
        recipe['parts'][0]['size']=['undefined_a','d','h']
        recipe['parts'].append({'id':'other','primitive':'box','size':[.1,.1,.1],
                                'center':['undefined_b',0,.1]})
        with self.assertRaises(ValueError) as caught:validate_recipe(outline,outline.objects[0],Recipe.model_validate(recipe))
        errors=[i for i in caught.exception.measurements['issues'] if i['code']=='part_expression']
        self.assertEqual({p for i in errors for p in i['paths']},{'/parts/0/size','/parts/1/center'})

    def test_rigid_edit_errors_in_all_scenarios_are_reported(self):
        data=outline_data();data['parameters'].update(alpha=.1,beta=.1)
        data['edit_checks']=[{'parameter':p,'delta':.02,'intent':'edit size'} for p in ['alpha','beta']]
        outline=Decomposition.model_validate(data)
        recipe=recipe_data()
        recipe['parts'][0].update(size=['w','d',.1],center=[0,0,.05])
        recipe['assembly']={'roots':['slab'],'relations':[]}
        for x,name in [(-.4,'alpha'),(.4,'beta')]:
            recipe['parts'].append({'id':name,'primitive':'box','size':[name,.1,.1],
                'center':[x,0,.15],'rigidity':'rigid'})
            recipe['assembly']['relations'].append({'kind':'supported_by','part':name,
                                                    'target':'slab','reason':'Supported cap.'})
        with self.assertRaises(ValueError) as caught:validate_recipe(outline,outline.objects[0],Recipe.model_validate(recipe))
        issues=caught.exception.measurements['issues']
        self.assertEqual({i['scenario'] for i in issues if i['code']=='rigid_part_edit'},{'alpha','beta'})

    def test_invalid_placements_do_not_hide_independent_root_errors(self):
        data=outline_data();other=copy.deepcopy(data['objects'][0]);other.update(id='other',access_required=False)
        data['objects'].append(other)
        data['zones'][0]['component_ids'].append('other')
        data['requirements'][0]['component_ids'].append('other')
        spatial=contract_data();spatial['zones'][0]['component_ids'].append('other')
        spatial['relations']=[{'id':'look','kind':'facing','a':'base','b':'other','reason':'face peer'}]
        layout=layout_data();layout['placements'][0]['position']=['invalid_a',0,0]
        layout['placements'].append({'id':'other','position':['invalid_b',0,0]})
        report=coarse_preflight(Decomposition.model_validate(data),Layout.model_validate(layout),
                               SpatialContract.model_validate(spatial))
        ids={id for i in report['issues'] if i['code']=='placement_expression' for id in i['component_ids']}
        self.assertEqual(ids,{'base','other'})
        self.assertTrue(any('ornament' in item['reason'] for item in report['blocked_checks']))

    def test_coarse_invalid_route_does_not_hide_occupancy_and_edits(self):
        data=outline_data();data['edit_checks']=[{'parameter':'base_width','delta':.2,'intent':'widen'}]
        layout=layout_data();layout['placements'][0]['position']=[0,-1.2,0]
        spatial=contract_data();spatial['routes'][0]['points'][0]=[0,-2]
        report=coarse_preflight(Decomposition.model_validate(data),Layout.model_validate(layout),
                               SpatialContract.model_validate(spatial))
        self.assertTrue(any(i['code']=='route_extent' for i in report['issues']))
        self.assertTrue(any('blocked' in i['evidence'] for i in report['deferred_findings']))
        scenarios={i['scenario'] for i in report['issues']+report['deferred_findings']}
        self.assertEqual(scenarios,{'baseline','base_width'})

    def test_real_batch_report_drives_single_patch_and_full_revalidation(self):
        outline=Decomposition.model_validate(outline_data())
        invalid=recipe_data();invalid['parts'][0]['size']=['w-.1','d-.1','h-.05']
        invalid['parts'][0]['center']=[0,0,'(h-.05)/2']
        calls=[]
        async def complete(stage,instructions,payload):
            calls.append(stage)
            report=payload['diagnostic_report']
            self.assertEqual(len([i for i in report['issues'] if i['code']=='component_size']),3)
            self.assertTrue(all('/parts' in i['allowed_repair_paths'] for i in report['issues']))
            self.assertIn('ALL independent issues',instructions)
            return {'reason':'Realize all three frozen envelope dimensions together.',
                    'replacements':[{'path':'/parts','value':recipe_data()['parts']}]}
        with tempfile.TemporaryDirectory() as directory:
            planner=StagedScenePlanner('unused',directory,'batch',complete=complete)
            result=asyncio.run(planner.stage('recipe_base',Recipe,'Build the frozen component.',{},
                lambda recipe:validate_recipe(outline,outline.objects[0],recipe),lambda data:['/parts'],
                initial_candidate=invalid))
            metrics=json.loads((Path(directory)/'planning_quality_metrics.json').read_text())
            failure=json.loads((Path(directory)/'recipe_base_failure_0.json').read_text())
        self.assertEqual(result.parts[0].size,('w','d','h'))
        self.assertEqual(calls,['recipe_base_repair_1'])
        self.assertEqual(metrics['repair_calls'],1)
        self.assertEqual(sum(i['code']=='component_size' for i in failure['measurements']['issues']),3)
        self.assertEqual(sum(i['code']=='component_bounds' for i in failure['measurements']['issues']),3)

    def test_diagnostics_after_six_thousand_characters_reach_transport(self):
        from planning_diagnostics import Diagnostics
        async def complete(stage,instructions,payload):
            self.assertEqual(len(payload['diagnostic_report']['issues']),100)
            self.assertIn('last-99',payload['errors'])
            return {'reason':'Fix complete report.','replacements':[{'path':'/placements/0/position','value':[0,0,0]}]}
        def validate(layout):
            if layout.placements[0].position[0]:
                diagnostics=Diagnostics('probe')
                for i in range(100):diagnostics.add('axis',f'last-{i} '+('detail '*15),paths=['/placements/0/position'])
                diagnostics.raise_if_any()
            return layout
        with tempfile.TemporaryDirectory() as directory:
            planner=StagedScenePlanner('unused',directory,'long',complete=complete)
            result=asyncio.run(planner.stage('layout',Layout,'test',{},validate,
                lambda data:['/placements/0/position'],initial_candidate={'placements':[{'id':'base','position':[1,0,0]}]}))
        self.assertEqual(result.placements[0].position,(0,0,0))

    def test_rejected_patch_retains_candidate_geometry_diagnostics(self):
        outline=Decomposition.model_validate(outline_data())
        invalid=recipe_data();invalid['parts'][0]['size']=['w-.1','d','h']
        calls=[]
        async def complete(stage,instructions,payload):
            calls.append(stage)
            if len(calls)==1:
                return {'reason':'Invalid pointer proposal.','replacements':[{'path':'/unrelated','value':0}]}
            report=payload['diagnostic_report']
            self.assertTrue(report['repair_patch_rejected'])
            self.assertIn('component_size',{i['code'] for i in report['issues']})
            self.assertIn('repair_patch',{i['code'] for i in report['issues']})
            return {'reason':'Correct the original geometry.','replacements':[{'path':'/parts','value':recipe_data()['parts']}]}
        with tempfile.TemporaryDirectory() as directory:
            planner=StagedScenePlanner('unused',directory,'patch',complete=complete)
            result=asyncio.run(planner.stage('recipe_base',Recipe,'test',{},
                lambda r:validate_recipe(outline,outline.objects[0],r),lambda d:['/parts'],initial_candidate=invalid))
        self.assertEqual(len(calls),2)
        self.assertEqual(result.parts[0].size,('w','d','h'))

    def test_omitted_defaults_offer_leaf_repairs_without_replacing_inventory(self):
        from staged_planner import replacement_paths
        data=outline_data();data['objects'][0].pop('requirement_tags',None)
        calls=[]
        async def complete(stage,instructions,payload):
            calls.append(stage)
            self.assertIn('/objects/0/requirement_tags',payload['allowed_paths'])
            return {'reason':'Add missing coverage metadata without rebuilding any geometry.',
                    'replacements':[{'path':'/objects/0/requirement_tags','value':['support']} ]}
        def validate(outline):
            if not outline.objects[0].requirement_tags:raise ValueError('Missing support requirement tag')
            return outline
        with tempfile.TemporaryDirectory() as directory:
            planner=StagedScenePlanner('unused',directory,'defaults',complete=complete)
            result=asyncio.run(planner.stage('decomposition',Decomposition,'test',{},validate,
                replacement_paths,initial_candidate=data))
        self.assertEqual(calls,['decomposition_repair_1'])
        self.assertEqual(result.objects[0].requirement_tags,['support'])
        self.assertEqual(result.objects[0].dimensions,Decomposition.model_validate(data).objects[0].dimensions)


if __name__=='__main__':unittest.main()
