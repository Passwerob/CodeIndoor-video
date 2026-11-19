"""Numerical layouts must satisfy the independent production preflight."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
sys.path.insert(0,str(Path(__file__).resolve().parent))
from staged_planner import Decomposition,Layout,coarse_preflight
from scene_spatial import SpatialContract,SpatialRelation
from test_staged_planner import outline_data,layout_data
from test_scene_spatial import contract_data
from layout_solver import solve_layout


class SolverTests(unittest.TestCase):
    def test_layout_solver_relocates_a_door_leaf_to_its_declared_entrance_jamb(self):
        import copy
        raw=outline_data();shell=copy.deepcopy(raw['objects'][0])
        shell.update(id='room_shell',semantic='Enclosed architectural room shell',category='architecture',
            dimensions=[4,4,3],collision_role='shell',grounded=True,access_required=False,parent=None)
        door=copy.deepcopy(raw['objects'][1])
        door.update(id='entry_door',semantic='Hinged entrance door leaf',category='architecture',
            dimensions=[1,.1,2.1],parent='room_shell',mount='origin',grounded=False,access_required=False)
        raw.update(extent=[4,4,3],objects=[shell,door],parameters={},edit_checks=[])
        raw['zones']=[{'name':'room','purpose':'Enclosed room','component_ids':['room_shell']}]
        raw['requirements']=[{'requirement':'An enclosed room with a hinged entry leaf',
                              'component_ids':['room_shell','entry_door'],
                              'verification':'Measured entrance and shell geometry'}]
        outline=Decomposition.model_validate(raw,context={'defer_decomposition_semantics':True})
        spatial=SpatialContract.model_validate({'zones':[{'id':'room','purpose':'Room interior',
            'center':[0,0],'size':[4,4],'component_ids':['room_shell']}],
            'openings':[{'id':'entry','kind':'entrance','wall':'y_max','offset':0,'width':1,
                'height':2.1,'approach_depth':.5,'purpose':'Clear entrance with door parked beside a jamb',
                'swing_leaf_ids':['entry_door']} ]})
        initial=Layout.model_validate({'placements':[{'id':'room_shell','position':[0,0,0],'yaw_deg':0},
            {'id':'entry_door','position':[1.4,1.5,0],'yaw_deg':90}]})
        before=coarse_preflight(outline,initial,spatial)
        self.assertEqual(before['readiness'],'blocked')
        solved,receipt=solve_layout(outline,initial,spatial)
        self.assertIsNotNone(solved,receipt)
        after=coarse_preflight(outline,solved,spatial)
        self.assertEqual(after['status'],'passed',after)
        leaf=next(item for item in solved.placements if item.id=='entry_door')
        low,high=after['bounds']['entry_door']
        aperture_low,aperture_high=-.5,.5
        left_gap=aperture_low-high[0]
        right_gap=low[0]-aperture_high
        self.assertTrue((-0.10 <= left_gap <= 0.35) or (-0.10 <= right_gap <= 0.35))
        self.assertAlmostEqual(leaf.position[2],0.0)
        self.assertAlmostEqual(leaf.position[0],.56)

    def test_unresolved_placement_expression_returns_complete_diagnostic_without_crashing(self):
        outline=Decomposition.model_validate(outline_data())
        placed=layout_data();placed['placements'][0]['position']=['missing_extent/2',0,0]
        spatial=SpatialContract.model_validate(contract_data())
        solved,receipt=solve_layout(outline,Layout.model_validate(placed),spatial)
        self.assertIsNone(solved)
        self.assertEqual(receipt['status'],'candidate_needs_revision')
        self.assertIn('base',receipt['unresolved_components'])
        self.assertTrue(any('missing_extent' in item['evidence'] for item in receipt['findings']))

    def test_semantic_access_normal_is_identical_in_solver_and_preflight(self):
        from scene_spatial import canonical_access_geometry
        data=outline_data()
        data['edit_checks']=[{'parameter':'base_width','delta':.2,'intent':'Wider display'}]
        outline=Decomposition.model_validate(data)
        use=contract_data();use['access_areas'][0].update(side='front',along_face=0)
        spatial=canonical_access_geometry(SpatialContract.model_validate(use))
        placed=layout_data();placed['access_centers']={'viewing':[0,'d/2+.475']}
        solved,receipt=solve_layout(outline,Layout.model_validate(placed),spatial)
        self.assertIsNotNone(solved,receipt)
        self.assertEqual(solved.access_centers['viewing'][1],spatial.access_areas[0].center[1])
        self.assertEqual(coarse_preflight(outline,solved,spatial)['status'],'passed')

    def test_tatami_room_experience_is_captured_in_spatial_agent_system_instructions(self):
        from staged_planner import SPATIAL
        from scene_experience import spatial_experience_prompt
        brief='高中生卧室，榻榻米、书桌和窗户'
        instructions=SPATIAL+spatial_experience_prompt(brief)
        self.assertIn('A zero minimum with maximum_m=null is valid and means no extra',instructions)
        self.assertIn('Every nonzero minimum and finite maximum becomes a',instructions)
        self.assertIn('minimum_m is normally zero',instructions)
        self.assertIn('residual floor left by perimeter furniture placement',instructions)
        self.assertIn('Do not add a hard desk-to-tatami or chair-to-tatami distance',instructions)

    def test_rotated_semantic_approach_uses_the_actual_oriented_envelope(self):
        from scene_spatial import canonical_access_geometry
        outline=Decomposition.model_validate(outline_data())
        use=contract_data();use['access_areas'][0].update(side='front',along_face=0)
        spatial=canonical_access_geometry(SpatialContract.model_validate(use))
        placed=layout_data();placed['placements'][0]['yaw_deg']=30
        layout=Layout.model_validate(placed)
        report=coarse_preflight(outline,layout,spatial)
        self.assertFalse(any(i['code']=='viewing' and "blocked by ['base']" in i['evidence'] for i in report['issues']))
        solved,receipt=solve_layout(outline,layout,spatial)
        self.assertIsNotNone(solved,receipt)
        self.assertEqual(solved.placements[0].yaw_deg,30)
        self.assertEqual(coarse_preflight(outline,solved,spatial)['status'],'passed')

    def test_mounting_orientation_comes_from_the_selected_contact_faces(self):
        data=outline_data()
        data['objects'][0]['internal_parts']=[{'id':'slab','semantic':'support slab',
            'role':'structure','rigidity':'parametric'}]
        data['objects'][1].update(mount='origin',access_required=False,
            surface_contact={'parent_part':'slab','parent_face':'y_max','child_face':'y_min'},
            interface_notes=['The backing contacts the selected support face.'])
        outline=Decomposition.model_validate(data)
        placed=layout_data();placed['placements'][1].update(position=[0,.65,0],yaw_deg=180)
        layout=Layout.model_validate(placed)
        spatial=SpatialContract.model_validate(contract_data())
        self.assertTrue(any(i['code']=='mounting_orientation' for i in coarse_preflight(outline,layout,spatial)['issues']))
        solved,receipt=solve_layout(outline,layout,spatial)
        self.assertIsNotNone(solved,receipt)
        self.assertEqual(solved.placements[1].yaw_deg,0)
        self.assertEqual(coarse_preflight(outline,solved,spatial)['status'],'passed')

    def test_mounting_yaw_correction_survives_unrelated_infeasible_constraints(self):
        data=outline_data()
        data['objects'][0]['internal_parts']=[{'id':'slab','semantic':'support slab',
            'role':'structure','rigidity':'parametric'}]
        data['objects'][1].update(mount='origin',access_required=False,
            surface_contact={'parent_part':'slab','parent_face':'y_max','child_face':'y_min'},
            interface_notes=['The backing contacts the selected support face.'])
        outline=Decomposition.model_validate(data)
        placed=layout_data();placed['placements'][1].update(position=[0,.65,0],yaw_deg=180)
        spatial_data=contract_data();spatial_data['zones'][0]['size']=[.1,.1]
        solved,receipt=solve_layout(outline,Layout.model_validate(placed),SpatialContract.model_validate(spatial_data))
        self.assertIsNone(solved)
        self.assertEqual(receipt['status'],'infeasible_current_choices')
        seeded=Layout.model_validate(receipt['candidate'])
        self.assertEqual(seeded.placements[1].yaw_deg,0)

    def test_shell_mounted_child_can_slide_along_its_wall(self):
        data=outline_data()
        data['objects'][0]['collision_role']='shell'
        data['objects'][0]['internal_parts']=[{'id':'slab','semantic':'wall mounting surface',
            'role':'structure','rigidity':'parametric'}]
        data['objects'][1].update(parent='base',mount='origin',grounded=False,
            access_required=False,collision_role='shell',
            surface_contact={'parent_part':'slab','parent_face':'y_min','child_face':'y_max'})
        outline=Decomposition.model_validate(data)
        spatial_data=contract_data()
        spatial_data['zones']=[{'id':'wall_zone','purpose':'wall display','center':[0,0],
                                'size':[2.0,2.0],'component_ids':['base','ornament']}]
        spatial_data['access_areas']=[]
        spatial_data['routes']=[]
        spatial=SpatialContract.model_validate(spatial_data)
        placed=layout_data();placed['placements'][1].update(position=[1.1,-.55,.5],yaw_deg=90)
        layout=Layout.model_validate(placed)

        self.assertNotEqual(coarse_preflight(outline,layout,spatial)['status'],'passed')
        solved,receipt=solve_layout(outline,layout,spatial)

        self.assertIsNotNone(solved,receipt)
        self.assertNotEqual(solved.placements[1].position[0],layout.placements[1].position[0])
        self.assertAlmostEqual(solved.placements[1].position[1],layout.placements[1].position[1])
        self.assertAlmostEqual(solved.placements[1].yaw_deg,0)
        self.assertEqual(coarse_preflight(outline,solved,spatial)['status'],'passed')

    def test_joint_straight_corridor_exports_no_duplicate_turns(self):
        from layout_solver import _solve_fixed_layout
        outline=Decomposition.model_validate(outline_data())
        use=contract_data();use['access_areas'][0]['size']=[.001,.9]
        spatial=SpatialContract.model_validate(use)
        solved,receipt=_solve_fixed_layout(outline,Layout.model_validate(layout_data()),spatial,joint_routes=True)
        self.assertIsNotNone(solved,receipt)
        import numpy as np
        points=solved.route_points['entry_path']
        self.assertTrue(all(np.linalg.norm(np.asarray(a)-b)>=.001 for a,b in zip(points,points[1:])))
        self.assertEqual(coarse_preflight(outline,solved,spatial)['status'],'passed')

    def test_collapsed_symbolic_bends_keep_endpoints_and_turns(self):
        from layout_solver import _compact_route
        self.assertEqual(_compact_route([(0,0),(0,0),(0,1),(0,1)]),[(0,0),(0,1)])
        self.assertEqual(_compact_route([(0,0),(0,.5),(0,1),(1,1)]),[(0,0),(0,1),(1,1)])
        self.assertEqual(_compact_route([(0,0),(0,1),(0,0)]),[(0,0),(0,1),(0,0)])

    def test_supported_child_can_move_on_parent_without_replanning_human_clearance(self):
        from scene_spatial import canonical_access_geometry
        data=outline_data()
        data['objects'][0]['dimensions']=['base_width',.55,1.05]
        data['objects'][1].update(dimensions=[.16,.49,.71],support_footprint=[.16,.16],
                                  access_required=False,
                                  interface_notes=['Verify the weighted base rests on the support surface.'])
        outline=Decomposition.model_validate(data)
        use=contract_data()
        use['access_areas'][0].update(center=[0,0],side='front',size=[.9,.75])
        spatial=canonical_access_geometry(SpatialContract.model_validate(use))
        placed=layout_data();placed['placements'][1]['position']=[-.35,-.08,0]
        layout=Layout.model_validate(placed)
        before=coarse_preflight(outline,layout,spatial)
        self.assertTrue(any(issue.get('constraint_id')=='viewing' and 'ornament' in issue['component_ids']
                            for issue in before['deferred_findings']))
        solved,receipt=solve_layout(outline,layout,spatial)
        self.assertIsNotNone(solved,receipt)
        self.assertNotEqual(solved.placements[1].position[:2],layout.placements[1].position[:2])
        self.assertEqual(solved.placements[1].position[2],0)
        self.assertEqual(coarse_preflight(outline,solved,spatial)['status'],'passed')
        self.assertEqual(spatial.access_areas[0].size,(.9,.75))
        self.assertEqual(spatial.routes[0].width,.8)

    def test_semantic_front_stays_local_and_follows_depth_edits(self):
        from scene_spatial import canonical_access_geometry,_rotation
        from general_scene import scalar
        import numpy as np
        spatial=SpatialContract.model_validate(contract_data())
        area=spatial.access_areas[0];area.side='front';area.along_face=.2;area.center=(99,99)
        canonical=canonical_access_geometry(spatial).access_areas[0]
        offset=np.array([scalar(v,{'w':1.5,'d':1}) for v in canonical.center])
        # A west-wall object faces east at yaw=90, independent of world/local naming.
        world=_rotation(90)@offset
        self.assertGreater(world[0],.9)
        self.assertAlmostEqual(world[1],.2)
        edited=scalar(canonical.center[1],{'w':1.5,'d':1.2})
        self.assertAlmostEqual(edited-offset[1],-.1)

    def test_moves_obstructing_furniture_and_preserves_full_width_route_and_edit(self):
        data=outline_data()
        data['edit_checks']=[{'parameter':'base_width','delta':.2,'intent':'Wider editable display'}]
        outline=Decomposition.model_validate(data)
        spatial=SpatialContract.model_validate(contract_data())
        placed=layout_data();placed['placements'][0]['position']=[0,-1,0]
        layout=Layout.model_validate(placed)
        before=coarse_preflight(outline,layout,spatial)
        self.assertEqual(before['status'],'passed')
        self.assertTrue(before['deferred_findings'])
        solved,receipt=solve_layout(outline,layout,spatial)
        self.assertIsNotNone(solved,receipt)
        report=coarse_preflight(outline,solved,spatial)
        self.assertEqual(report['status'],'passed',report)
        self.assertEqual(report['edit_scenarios'][0]['status'],'passed')
        self.assertEqual(spatial.routes[0].width,.8)
        self.assertNotEqual(solved.placements[0].position,layout.placements[0].position)

    def test_walkable_rug_does_not_block_route_or_furniture_overlap(self):
        import copy
        data=outline_data()
        rug=copy.deepcopy(data['objects'][0])
        rug.update(id='rug',semantic='Low-pile wool floor covering',category='floor_covering',
            dimensions=[2,2,.02],grounded=True,access_required=False,collision_role='floor_covering',
            internal_parts=[],required_features=['Woven low-pile surface'],
            material_requirements=['Wool textile'],
            decomposition_reason='A thin walkable finish defines the furniture grouping.',
            visual_importance=3,parent=None,mount='origin',support_part=None,surface_contact=None,
            support_footprint=None)
        data['objects'].append(rug)
        data['zones'][0]['component_ids'].append('rug')
        data['requirements'][0]['component_ids'].append('rug')
        outline=Decomposition.model_validate(data)
        placements=layout_data()['placements']
        placements.append({'id':'rug','position':[0,0,0],'yaw_deg':0})
        layout=Layout.model_validate({'placements':placements})
        spatial=SpatialContract.model_validate(contract_data())
        spatial.zones[0].component_ids.append('rug')
        spatial.relations.append(SpatialRelation.model_validate({'id':'rug_under_seating','kind':'distance',
            'a':'rug','b':'base','minimum_m':0,'maximum_m':0,
            'reason':'The low-pile floor covering lies beneath the display grouping.'}))
        solved,receipt=solve_layout(outline,layout,spatial)
        self.assertIsNotNone(solved,receipt)
        self.assertEqual(coarse_preflight(outline,solved,spatial)['status'],'passed')
        self.assertEqual(solved.placements[-1].position,(0,0,0))

    def test_returns_named_incompatibility_instead_of_accepting_impossible_zone(self):
        data=contract_data();data['zones'][0]['size']=[.4,.4]
        solved,receipt=solve_layout(Decomposition.model_validate(outline_data()),
            Layout.model_validate(layout_data()),SpatialContract.model_validate(data))
        self.assertIsNone(solved)
        self.assertEqual(receipt['status'],'infeasible_current_choices')
        self.assertTrue(any('zone display_zone: base' in c for c in receipt['incompatible_constraints']))

    def test_rotated_access_is_not_replaced_by_a_larger_axis_aligned_reservation(self):
        placed=layout_data();placed['placements'][0]['yaw_deg']=20
        outline=Decomposition.model_validate(outline_data())
        spatial=SpatialContract.model_validate(contract_data())
        solved,receipt=solve_layout(outline,Layout.model_validate(placed),spatial)
        self.assertIsNotNone(solved,receipt)
        self.assertEqual(coarse_preflight(outline,solved,spatial)['status'],'passed')

    def test_joint_corridor_moves_both_endpoints_and_furniture_and_checks_export_contract(self):
        from layout_solver import _solve_fixed_layout
        outline=Decomposition.model_validate(outline_data())
        spatial=SpatialContract.model_validate(contract_data())
        placed=layout_data();placed['placements'][0]['position']=[0,-1,0]
        result,receipt=_solve_fixed_layout(outline,Layout.model_validate(placed),spatial,joint_routes=True)
        self.assertIsNotNone(result,receipt)
        self.assertEqual(coarse_preflight(outline,result,spatial)['status'],'passed')
        self.assertGreaterEqual(len(result.route_points['entry_path']),2)
        self.assertLessEqual(len(result.route_points['entry_path']),4)

    def test_joint_corridor_routes_through_a_serpentine_aisle_in_one_solve(self):
        import copy
        from layout_solver import _solve_fixed_layout
        from test_scene_spatial import contract_data
        raw=outline_data()
        base=copy.deepcopy(raw['objects'][0])
        base['dimensions']=[.3,.3,.2]
        ornament=copy.deepcopy(raw['objects'][1])
        ornament.update(dimensions=[.1,.1,.1],access_required=False)
        barriers=[]
        for index,position in enumerate(([-.5,-1.1,0],[.5,-.1,0],[-.5,.9,0]),1):
            barrier=copy.deepcopy(base)
            barrier.update(id=f'barrier_{index}',semantic='Fixed aisle barrier',category='architecture',
                dimensions=[2.8,.18,2.2],parent=None,mount='origin',grounded=True,
                access_required=False,support_footprint=None)
            barriers.append(barrier)
        raw.update(extent=[4,4,3],parameters={},objects=[base,ornament,*barriers],edit_checks=[])
        raw['zones'][0]['component_ids']=['base','ornament',*(item['id'] for item in barriers)]
        raw['requirements'][0]['component_ids']=['base','ornament',*(item['id'] for item in barriers)]
        outline=Decomposition.model_validate(raw)
        spatial=SpatialContract.model_validate({
            'zones':[{'id':'room','purpose':'Room interior','center':[0,0],'size':[4,4],
                      'component_ids':['base','ornament',*(item['id'] for item in barriers)]}],
            'openings':[{'id':'entrance','kind':'entrance','wall':'y_min','offset':0,'width':1,
                'height':2.1,'approach_depth':.8,'purpose':'Route enters through the south doorway'}],
            'access_areas':[{'id':'viewing','owner_id':'base','purpose':'Reach the display beyond the barriers',
                'center':[0,-.425],'size':[.5,.5],'height':1.9}],
            'routes':[{'id':'entry_path','start':'entrance','end':'viewing','points':[[0,-1.6],[0,1.425]],
                'width':.4,'height':1.9,'purpose':'Reach the display through the serpentine aisle'}],
            'relations':[{'id':'barrier_stagger','kind':'distance','a':'barrier_1','b':'barrier_2',
                'minimum_m':0,'maximum_m':None,
                'reason':'The staggered barriers define the winding aisle the route must pass through.'}]})
        initial=Layout.model_validate({'placements':[
            {'id':'base','position':[0,1.85,0],'yaw_deg':0},
            {'id':'ornament','position':[0,0,0],'yaw_deg':0},
            {'id':'barrier_1','position':[-.5,-1.1,0],'yaw_deg':0},
            {'id':'barrier_2','position':[.5,-.1,0],'yaw_deg':0},
            {'id':'barrier_3','position':[-.5,.9,0],'yaw_deg':0}]})
        solved,receipt=_solve_fixed_layout(outline,initial,spatial,joint_routes=True,timeout_ms=20000)
        self.assertIsNotNone(solved,receipt)
        points=solved.route_points['entry_path']
        self.assertGreater(len(points),4)
        self.assertEqual(coarse_preflight(outline,solved,spatial)['status'],'passed')

    def test_revised_spatial_contract_revalidates_previous_layout_before_new_model_call(self):
        import tempfile
        from staged_planner import StagedScenePlanner
        from test_staged_planner import recipe_data
        calls=[]
        async def complete(stage,instructions,payload):
            calls.append(stage)
            if stage=='request_contract_0':return {'asset_boundary':'scene','quality_target':'prototype'}
            if stage=='decomposition_0':return outline_data()
            if stage=='spatial_plan_0':
                data=contract_data();data['zones'][0]['size']=[.5,3.5];return data
            if stage=='coarse_layout_0':return layout_data()
            if stage.startswith(('coarse_layout_repair','spatial_revision')):
                self.fail('Zone fit is measured after reconstruction, not repaired from coarse envelopes')
            if stage=='recipe_base_0':return recipe_data()
            self.fail(stage)
        with tempfile.TemporaryDirectory() as directory:
            StagedScenePlanner('unused',directory,'probe',complete=complete).plan('A display')
        self.assertNotIn('spatial_revision_0',calls)
        self.assertNotIn('coarse_layout_repair_1',calls)
        self.assertLess(calls.index('coarse_layout_0'),calls.index('recipe_base_0'))

    def test_incompatible_previous_seed_reaches_layout_agent_with_fresh_evidence(self):
        import tempfile
        from staged_planner import StagedScenePlanner
        from test_staged_planner import recipe_data
        calls=[]
        async def complete(stage,instructions,payload):
            calls.append(stage)
            if stage=='request_contract_0':return {'asset_boundary':'scene','quality_target':'prototype'}
            if stage=='decomposition_0':
                data=outline_data();data['objects'][0]['grounded']=True;return data
            if stage=='spatial_plan_0':
                data=contract_data();data['zones'][0]['size']=[.5,3.5];return data
            if stage=='coarse_layout_0':
                data=layout_data();data['placements'][0]['position'][2]=.25;return data
            if stage in {'coarse_layout_repair_1','coarse_layout_repair_2'}:
                evidence=payload['diagnostic_report']
                self.assertTrue(evidence['issues'])
                return {'reason':'Retain the same floor-height violation for spatial fallback.',
                    'replacements':[{'path':'/placements/0/position','value':[0,0,.25]}]}
            if stage=='spatial_revision_0':return contract_data()
            if stage=='coarse_layout_revision_0':
                evidence=payload['context']['previous_layout_failure_evidence']
                self.assertTrue(any('grounded floor' in i['evidence'] for i in evidence['issues']))
                return layout_data()
            if stage=='recipe_base_0':return recipe_data()
            self.fail(stage)
        with tempfile.TemporaryDirectory() as directory:
            StagedScenePlanner('unused',directory,'probe',complete=complete).plan('A display')
        self.assertIn('coarse_layout_revision_0',calls)


if __name__=='__main__':unittest.main()
