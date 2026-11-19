"""Scene usability regressions, independent of asset appearance and generation."""
import copy
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'experiments'))
import numpy as np
from scene_spatial import (SpatialContract, audit_spatial, validate_spatial_contract, coarse_layout,
                           normalize_corner_based_spatial_frame,anchor_centered_architectural_shells,
                           normalize_facing_relations)
from staged_planner import Decomposition, Layout, StagedScenePlanner, coarse_preflight
from general_planner import to_general_plan
from general_scene import judge_general_geometry, planned_envelope_bounds, edit_general
from test_staged_planner import outline_data, recipe_data, layout_data


def contract_data():
    return {'zones': [{'id': 'display_zone', 'purpose': 'display', 'center': [0, 0],
                       'size': [4, 4], 'component_ids': ['base', 'ornament']}],
            'openings': [{'id': 'entrance', 'kind': 'entrance', 'wall': 'y_min', 'offset': 0,
                          'width': 1, 'height': 2.1, 'approach_depth': .8, 'purpose': 'entry'}],
            'access_areas': [{'id': 'viewing', 'owner_id': 'base', 'center': [0, -1.1],
                              'size': [1.1, .9], 'purpose': 'view display'}],
            'routes': [{'id': 'entry_path', 'start': 'entrance', 'end': 'viewing',
                        'points': [[0, -1.6], [0, -1.1]], 'width': .8, 'purpose': 'reach display'}]}


def probe_objects():
    return [SimpleNamespace(id='base', parent=None, support=None, collision_role='solid', access_required=True),
            SimpleNamespace(id='ornament', parent='base', support=None, collision_role='solid', access_required=True)]


class SpatialTests(unittest.TestCase):
    def test_blocked_route_names_segment_measures_gap_and_targets_route(self):
        contract=SpatialContract.model_validate(contract_data())
        blocker=SimpleNamespace(id='blocker',collision_role='solid')
        bounds={'blocker':[[-.1,-1.5,0],[.1,-1.4,1]]}
        poses={'blocker':(0,np.array([0,-1.45,0]))}
        report=audit_spatial(contract,[blocker],[4,4,3],bounds,poses)
        issue=next(f for f in report['findings'] if f.get('code')=='route_blocked')
        self.assertIn('/spatial/routes/0/points',issue['paths'])
        self.assertEqual(issue['measured']['position_frame'],'world_xy')
        measurement=issue['measured']['blockers'][0]
        self.assertEqual(measurement['blocked_segment_indices'],[0])
        self.assertAlmostEqual(measurement['centerline_gap_m'],0)
        self.assertAlmostEqual(measurement['clearance_deficit_m'],.4)
        self.assertEqual(issue['expected']['minimum_centerline_obstacle_distance_m'],.4)

    def test_swing_leaf_cannot_include_fixed_jambs_and_header(self):
        from planning_diagnostics import PlanningValidationError
        data=contract_data()
        data['openings'][0]['swing_leaf_ids']=['entry_door']
        door=SimpleNamespace(id='entry_door',semantic='Door leaf with fixed jambs',parent='shell',
            support=None,collision_role='solid',access_required=False,
            internal_parts=[SimpleNamespace(id='jamb_left',semantic='Fixed left door jamb'),
                            SimpleNamespace(id='door_head',semantic='Door head spanning jambs'),
                            SimpleNamespace(id='leaf_frame',semantic='Frame of the moving leaf')])
        with self.assertRaises(PlanningValidationError) as caught:
            validate_spatial_contract(SpatialContract.model_validate(data),[*probe_objects(),door],[4,4,3])
        issue=next(i for i in caught.exception.measurements['issues'] if i['code']=='opening_swing_assembly')
        self.assertEqual(issue['component_ids'],['entry_door'])
        self.assertEqual(issue['measured']['fixed_part_ids'],['jamb_left','door_head'])
        self.assertEqual(issue['measured']['repair_target'],'decomposition')
        self.assertIn('/objects/2/internal_parts',issue['paths'])
        # A frame that belongs to the moving panel is not a fixed doorway frame.
        door.internal_parts=door.internal_parts[2:]
        validate_spatial_contract(SpatialContract.model_validate(data),[*probe_objects(),door],[4,4,3])

    def test_facing_is_normalized_to_orientation_without_erasing_explicit_distance(self):
        data=contract_data()
        data['relations']=[
            {'id':'chair_faces_desk','kind':'facing','a':'chair','b':'desk',
             'minimum_m':.35,'maximum_m':.85,
             'reason':'The chair faces the desk for seated work.'},
            {'id':'desk_reach','kind':'distance','a':'chair','b':'desk',
             'minimum_m':.2,'maximum_m':.8,
             'reason':'The user explicitly requests an accessible reach interval.'}]
        contract=SpatialContract.model_validate(data)
        normalized,changes=normalize_facing_relations(contract)
        self.assertEqual((normalized.relations[0].minimum_m,normalized.relations[0].maximum_m),(0,None))
        self.assertEqual((normalized.relations[1].minimum_m,normalized.relations[1].maximum_m),(.2,.8))
        self.assertEqual(changes[0]['id'],'chair_faces_desk')
        self.assertEqual((contract.relations[0].minimum_m,contract.relations[0].maximum_m),(.35,.85))

    def test_uniform_corner_frame_is_translated_once_without_touching_owner_local_access(self):
        data=contract_data()
        data['zones'][0].update(center=[2,2],size=[4,4])
        data['openings'][0]['offset']=2
        data['routes'][0]['points']=[[2,.4],[2,.9]]
        data['experience_views']=[{'intent_id':'entry','position':[2,2,1.6],
                                   'target':[2,2,1.0],'lens_mm':28}]
        contract=SpatialContract.model_validate(data)
        normalized,receipt=normalize_corner_based_spatial_frame(contract,[4,4,3])
        self.assertIsNotNone(receipt)
        self.assertEqual(normalized.zones[0].center,(0,0))
        self.assertEqual(normalized.openings[0].offset,0)
        self.assertEqual(normalized.routes[0].points,[(0,-1.6),(0,-1.1)])
        self.assertEqual(normalized.experience_views[0].position,(0,0,1.6))
        self.assertEqual(normalized.access_areas[0].center,(0,-1.1))
        self.assertEqual(contract.zones[0].center,(2,2))
        self.assertEqual(receipt['transform_xy_m'],[-2.0,-2.0])

    def test_centered_plan_in_positive_quadrant_is_not_misclassified_as_corner_frame(self):
        data=contract_data()
        data['zones'][0].update(center=[1,1],size=[.8,.8])
        data['openings'][0]['offset']=.3
        data['routes']=[]
        contract=SpatialContract.model_validate(data)
        normalized,receipt=normalize_corner_based_spatial_frame(contract,[4,4,3])
        self.assertIsNone(receipt)
        self.assertEqual(normalized.zones[0].center,(1,1))

    def test_corner_normalization_cannot_override_an_explicit_opening_offset(self):
        data=contract_data()
        data['zones'][0].update(center=[2,2],size=[4,4])
        data['openings'][0]['offset']=2
        data['routes']=[]
        data['requirements']={'extent':[4,4,3],'openings':[{
            'kind':'entrance','wall':'y_min','offset':.3,'width':1}]}
        contract=SpatialContract.model_validate(data)
        normalized,receipt=normalize_corner_based_spatial_frame(contract,[4,4,3])
        self.assertIsNone(receipt)
        self.assertEqual(normalized.openings[0].offset,2)

    def test_full_room_shell_and_occupied_floor_are_anchored_before_layout(self):
        from types import SimpleNamespace
        from staged_planner import Layout
        outline=SimpleNamespace(extent=(4.2,4.2,2.8),parameters={},objects=[SimpleNamespace(
            id='shell_room',parent=None,collision_role='shell',dimensions=(4.2,4.2,2.8))])
        layout=Layout.model_validate({'placements':[{'id':'shell_room',
            'position':[-2.1,-2.1,-.12],'yaw_deg':17}]})
        normalized,receipt=anchor_centered_architectural_shells(outline,layout)
        self.assertIsNotNone(receipt)
        self.assertEqual(tuple(float(value) for value in normalized.placements[0].position),(0,0,-.05))
        self.assertEqual(normalized.placements[0].yaw_deg,17)
        self.assertEqual(tuple(float(value) for value in layout.placements[0].position),(-2.1,-2.1,-.12))

    def test_enclosing_architecture_can_span_activity_zones_without_a_fake_membership(self):
        objects=probe_objects()+[SimpleNamespace(id='enclosure',parent=None,support=None,
            collision_role='shell',access_required=False)]
        data=contract_data()
        contract=SpatialContract.model_validate(data)
        validate_spatial_contract(contract,objects,[4,4,3])
        data['zones'][0]['component_ids'].remove('base')
        with self.assertRaisesRegex(ValueError,'base'):
            validate_spatial_contract(SpatialContract.model_validate(data),objects,[4,4,3])

    def test_boundary_compiler_preserves_invalid_coordinates_for_spatial_diagnostics(self):
        from scene_spatial import canonical_spatial_bounds
        data=contract_data();data['zones'][0].update(center=[.1,0],size=[4,4])
        data['routes'][0]['points']=[[0,-2],[0,-1.1]]
        original=SpatialContract.model_validate(data)
        fitted=canonical_spatial_bounds(original,[4,4,3])
        self.assertEqual(fitted.routes[0].width,.8)
        self.assertEqual(fitted.routes[0].points,original.routes[0].points)
        self.assertEqual(fitted.zones[0].center,original.zones[0].center)
        self.assertEqual(fitted.zones[0].size,original.zones[0].size)
        self.assertEqual(original.routes[0].points[0],(0,-2))

    def test_opening_offset_diagnostic_defines_signed_centered_wall_interval(self):
        from planning_diagnostics import PlanningValidationError
        data=contract_data()
        data['openings'][0].update(wall='y_max',offset=2.5,width=1.7)
        with self.assertRaises(PlanningValidationError) as caught:
            validate_spatial_contract(SpatialContract.model_validate(data),probe_objects(),[4.2,4.2,3])
        issue=next(item for item in caught.exception.measurements['issues']
                   if item['code']=='opening_extent')
        self.assertEqual(issue['measured']['opening_tangent_bounds_m'],[1.65,3.35])
        self.assertEqual(issue['measured']['legal_offset_interval_m'],[-1.25,1.25])
        self.assertIn('signed coordinate from room center',issue['evidence'])

    def test_zone_extent_diagnostic_reports_centered_bounds_without_autocorrection(self):
        from planning_diagnostics import PlanningValidationError
        data=contract_data()
        data['zones'][0].update(center=[2.1,0],size=[1,1])
        with self.assertRaises(PlanningValidationError) as caught:
            validate_spatial_contract(SpatialContract.model_validate(data),probe_objects(),[4.2,4.2,3])
        issue=next(item for item in caught.exception.measurements['issues']
                   if item['code']=='zone_extent')
        self.assertEqual(issue['measured'],[[1.6,-.5],[2.6,.5]])
        self.assertEqual(issue['expected'],[[-2.1,-2.1],[2.1,2.1]])
        self.assertIn('do not offset by half the extent',issue['evidence'])

    def audit(self, data=None, *, extra=None, yaw=0):
        contract = SpatialContract.model_validate(data or contract_data())
        objects = probe_objects()
        bounds = {'base': [[-.75, -.5, 0], [.75, .5, .2]],
                  'ornament': [[-.15, -.15, .2], [.15, .15, .7]]}
        poses = {'base': (yaw, np.zeros(3)), 'ornament': (yaw, np.array([0, 0, .2]))}
        if extra:
            objects.append(SimpleNamespace(id='obstacle', collision_role='solid'))
            bounds['obstacle'] = extra
            poses['obstacle'] = (0, np.mean(extra, axis=0))
        return audit_spatial(contract, objects, [4, 4, 3], bounds, poses)

    def test_usable_route_and_zone_pass(self):
        contract = SpatialContract.model_validate(contract_data())
        validate_spatial_contract(contract, probe_objects(), [4, 4, 3])
        self.assertEqual(self.audit()['status'], 'passed')

    def test_zone_failure_reports_world_bounds_zone_bounds_and_parent_frame(self):
        data=contract_data()
        data['zones'][0]['center']=[0,0]
        data['zones'][0]['size']=[1,1]
        report=self.audit(data)
        finding=next(item for item in report['findings'] if item['constraint_id']=='display_zone'
                     and item['component_ids']==['base'])
        self.assertEqual(finding['measured']['world_bounds_m'],[[-.75,-.5,0],[.75,.5,.2]])
        self.assertEqual(finding['measured']['zone_bounds_xy_m'],[[-.5,-.5],[.5,.5]])
        self.assertEqual(finding['measured']['position_frame'],'scene_centered')

    def test_parented_zone_failure_includes_parent_world_pose(self):
        data=contract_data()
        data['zones'][0]['center']=[0,0]
        data['zones'][0]['size']=[.2,.2]
        objects=probe_objects()
        bounds={'base':[[-.75,-.5,0],[.75,.5,.2]],
                'ornament':[[1.1,-.15,.2],[1.4,.15,.7]]}
        poses={'base':(0,np.array([0,0,0])),
               'ornament':(0,np.array([1.25,0,.2]))}
        report=audit_spatial(SpatialContract.model_validate(data),objects,[4,4,3],bounds,poses)
        finding=next(item for item in report['findings'] if item['constraint_id']=='display_zone'
                     and item['component_ids']==['ornament'])
        self.assertEqual(finding['measured']['position_frame'],'parent_local')
        self.assertEqual(finding['measured']['parent_id'],'base')
        self.assertEqual(finding['measured']['parent_world_pose']['origin_m'],[0,0,0])

    def test_coarse_overlap_reports_measured_intersection_depth(self):
        from scene_spatial import coarse_layout
        from staged_planner import Layout
        outline=Decomposition.model_validate(outline_data())
        outline.objects[1].parent=None
        outline.objects[1].mount='origin'
        contract=SpatialContract.model_validate(contract_data())
        layout=Layout.model_validate({'placements':[{'id':'base','position':[0,0,0],'yaw_deg':0},
            {'id':'ornament','position':[0,0,0],'yaw_deg':0}]})
        report=coarse_layout(outline,layout,contract)
        finding=next(item for item in report['findings'] if item['evidence']=='Coarse root envelopes overlap')
        self.assertEqual(finding['measured']['overlap_depth_xyz_m'],[.3,.3,.2])

    def test_coarse_preflight_defers_usability_conflicts_but_blocks_out_of_room(self):
        outline=Decomposition.model_validate(outline_data())
        spatial=SpatialContract.model_validate(contract_data())
        rough=layout_data();rough['placements'][0]['position']=[0,-1,0]
        report=coarse_preflight(outline,Layout.model_validate(rough),spatial)
        self.assertEqual(report['status'],'passed')
        self.assertEqual(report['readiness'],'ready_for_3d_audit')
        self.assertTrue(report['deferred_findings'])
        self.assertTrue(any(f.get('constraint_id')=='entry_path' for f in report['deferred_findings']))
        outside=layout_data();outside['placements'][0]['position']=[3,0,0]
        rejected=coarse_preflight(outline,Layout.model_validate(outside),spatial)
        self.assertEqual(rejected['status'],'needs_revision')
        self.assertEqual(rejected['readiness'],'blocked')
        self.assertTrue(any('outside extent' in f['evidence'] for f in rejected['blocking_findings']))

    def test_minimum_only_distance_does_not_invent_an_upper_limit(self):
        data=contract_data()
        data['relations']=[{'id':'minimum_gap','kind':'distance','a':'base','b':'ornament',
            'minimum_m':.1,'maximum_m':None,'reason':'Only a minimum separation is required'}]
        report=self.audit(data)
        self.assertEqual(report['status'],'needs_revision')
        self.assertTrue(any(f['constraint_id']=='minimum_gap' for f in report['findings']))

    def test_zero_minimum_with_unbounded_maximum_represents_no_extra_gap(self):
        data=contract_data()
        data['relations']=[{'id':'no_extra_gap','kind':'distance','a':'base','b':'ornament',
            'minimum_m':0,'maximum_m':None,'reason':'Their zones express the activity grouping'}]
        contract=SpatialContract.model_validate(data)
        validate_spatial_contract(contract,probe_objects(),[4,4,3])
        self.assertEqual(contract.relations[0].minimum_m,0)

    def test_inferred_visual_and_chair_pullback_gaps_are_reported_together(self):
        data=contract_data()
        data['relations']=[
            {'id':'visual_gap','kind':'distance','a':'base','b':'ornament','minimum_m':.8,
             'reason':'A minimum 0.8 m edge gap keeps the central open floor between the study mass and tatami rest mass, preserving the route and visual separation required by the brief'},
            {'id':'chair_tatami_gap','kind':'distance','a':'chair','b':'tatami_platform','minimum_m':.55,
             'reason':'The study chair must pull out and tuck in without intruding into the tatami bedding or mat edge; 0.55 m is the short individual seat-pullback clearance'},
        ]
        data['zones'][0]['component_ids'] += ['chair','tatami_platform']
        objects=probe_objects()+[
            SimpleNamespace(id='chair',semantic='Study chair',parent=None,support=None,
                collision_role='solid',access_required=False),
            SimpleNamespace(id='tatami_platform',semantic='Tatami sleeping platform',parent=None,support=None,
                collision_role='solid',access_required=False),
        ]
        with self.assertRaises(ValueError) as caught:
            validate_spatial_contract(SpatialContract.model_validate(data),objects,[4,4,3])
        self.assertIn('nonfunctional_pairwise_gap',str(caught.exception))
        self.assertIn('visual_gap',str(caught.exception))
        self.assertIn('chair_tatami_gap',str(caught.exception))

    def test_nonintersecting_furniture_can_block_full_route_width(self):
        report = self.audit(extra=[[.2, -1.7, 0], [.4, -1.5, 1.]])
        self.assertTrue(any('entry_path' == f.get('constraint_id') and 'obstacle' in f['component_ids']
                            for f in report['findings']))

    def test_operating_area_cannot_overlap_owner(self):
        data = contract_data()
        data['access_areas'][0]['center'] = [0, 0]
        report = self.audit(data)
        self.assertTrue(any('viewing' == f.get('constraint_id') and 'blocked' in f['evidence']
                            for f in report['findings']))

    def test_owner_local_access_rotates_with_yaw(self):
        data = contract_data()
        data['routes'][0]['points'][-1] = [1.1, 0]
        report = self.audit(data, yaw=90)
        polygon = np.array(report['access_polygons_m']['viewing'])
        np.testing.assert_allclose(polygon.mean(axis=0), [1.1, 0], atol=1e-8)

    def test_face_relative_operating_area_follows_edited_object_width(self):
        data = contract_data()
        data['access_areas'][0]['center'] = ['w/2+0.5', 0]
        data['access_areas'][0]['size'] = [1., .8]
        data['routes'] = []
        contract = SpatialContract.model_validate(data)
        obj = SimpleNamespace(id='base', size=('width', .6, .8), parameters={}, collision_role='solid')
        reports = []
        for width in (1.0, 1.2):
            reports.append(audit_spatial(contract, [obj], [4, 4, 3],
                {'base': [[-width/2, -.3, 0], [width/2, .3, .8]]},
                {'base': (0, np.zeros(3))}, parameters={'width': width}))
        self.assertTrue(all(r['status']=='passed' for r in reports))
        centers = [np.array(r['access_polygons_m']['viewing']).mean(axis=0) for r in reports]
        np.testing.assert_allclose(centers[1]-centers[0], [.1, 0], atol=1e-8)

    def test_disconnected_access_is_rejected_before_layout(self):
        data = contract_data(); data['routes'] = []
        with self.assertRaisesRegex(ValueError, 'disconnected'):
            validate_spatial_contract(SpatialContract.model_validate(data), probe_objects(), [4, 4, 3])

    def test_missing_required_access_rejected(self):
        data = contract_data(); data['access_areas'] = []; data['routes'] = []
        with self.assertRaisesRegex(ValueError, 'missing required'):
            validate_spatial_contract(SpatialContract.model_validate(data), probe_objects(), [4, 4, 3])

    def test_user_minimum_width_and_opening_cannot_be_weakened(self):
        data = contract_data()
        data['requirements'] = {'extent': [4, 4, 3], 'minimum_route_width_m': .9,
                                'openings': [{'kind': 'entrance', 'wall': 'y_min', 'offset': 0, 'width': 1}]}
        with self.assertRaisesRegex(ValueError, 'weakens'):
            validate_spatial_contract(SpatialContract.model_validate(data), probe_objects(), [4, 4, 3])
        data['routes'][0]['width'] = .9
        data['openings'][0]['width'] = 1.1
        with self.assertRaisesRegex(ValueError, 'Missing opening'):
            validate_spatial_contract(SpatialContract.model_validate(data), probe_objects(), [4, 4, 3])

    def test_indoor_scene_cannot_be_only_furniture_and_metadata(self):
        from staged_planner import Decomposition, RequestContract, validate_request_contract
        contract = RequestContract(asset_boundary='scene', quality_target='prototype',
                                  scene_use={'enclosure':'enclosed','extent':[4,4,3]})
        with self.assertRaisesRegex(ValueError, 'architectural shell'):
            validate_request_contract(Decomposition.model_validate(outline_data()), contract)
        spatial = contract_data(); spatial['requirements']={'enclosure':'enclosed'}
        with self.assertRaisesRegex(ValueError, 'architectural shell'):
            validate_spatial_contract(SpatialContract.model_validate(spatial),probe_objects(),[4,4,3])

    def test_route_at_wall_reports_all_invalid_routes_together(self):
        data = contract_data()
        data['routes'][0]['points'][0] = [0, -2]
        other = copy.deepcopy(data['routes'][0]); other['id'] = 'second_route'
        data['routes'].append(other)
        with self.assertRaises(ValueError) as failure:
            validate_spatial_contract(SpatialContract.model_validate(data), probe_objects(), [4, 4, 3])
        self.assertIn('entry_path', str(failure.exception))
        self.assertIn('second_route', str(failure.exception))

    def test_exported_wall_cannot_hide_behind_shell_label(self):
        contract = SpatialContract.model_validate(contract_data())
        objects = probe_objects() + [SimpleNamespace(id='wall', collision_role='shell')]
        bounds = {'base': [[-.75, -.5, 0], [.75, .5, .2]],
                  'ornament': [[-.15, -.15, .2], [.15, .15, .7]],
                  'wall': [[-2, -2, 0], [2, 2, 3]]}
        poses = {id: (0, np.zeros(3)) for id in bounds}
        report = audit_spatial(contract, objects, [4, 4, 3], bounds, poses,
                               shell_parts={'wall': {'closed_panel': [[-1, -1.5, 0], [1, -1.4, 2.5]]}})
        self.assertTrue(any('wall/closed_panel' in f['component_ids'] for f in report['findings']))

    def test_layout_can_reroute_without_weakening_width_or_connectivity(self):
        from staged_planner import spatial_with_layout, Layout
        contract = SpatialContract.model_validate(contract_data())
        layout = layout_data(); layout['route_points'] = {'entry_path': [[0, -1.5], [0, -1.1]]}
        changed = spatial_with_layout(contract, Layout.model_validate(layout), probe_objects(), [4, 4, 3])
        self.assertEqual(changed.routes[0].width, contract.routes[0].width)
        self.assertEqual(changed.routes[0].start, contract.routes[0].start)
        self.assertNotEqual(changed.routes[0].points, contract.routes[0].points)

    def test_rough_layout_conflicts_are_recorded_and_can_enter_3d_stages(self):
        calls = []
        async def complete(stage, instructions, payload):
            calls.append(stage)
            if stage == 'request_contract_0':
                return {'asset_boundary': 'scene', 'quality_target': 'prototype',
                        'scene_use': {'extent': [4, 4, 3], 'minimum_route_width_m': .8}}
            if stage == 'decomposition_0': return outline_data()
            if stage in {'spatial_plan_0', 'spatial_revision_0'}:
                if stage == 'spatial_revision_0':
                    raise AssertionError('A placed-object conflict should be audited after Blender export')
                return contract_data()
            if stage == 'coarse_layout_0':
                frames={row['id']:row for row in payload['context']['placement_frames']}
                self.assertEqual(frames['base']['coordinate_frame'],'scene_centered')
                self.assertEqual(frames['ornament']['coordinate_frame'],'parent_local')
                self.assertEqual(frames['ornament']['parent_id'],'base')
                data = layout_data(); data['placements'][0]['position'] = [0, -1, 0]; return data
            if stage.startswith('coarse_layout_revision_'):
                raise AssertionError('Soft coarse conflicts must not trigger a numerical layout repair loop')
            if stage == 'recipe_base_0': return recipe_data()
            raise AssertionError(stage)
        with tempfile.TemporaryDirectory() as directory:
            plan = StagedScenePlanner('unused', directory, 'scene', complete=complete, max_repairs=0).plan('scene')
            accepted=json.loads((Path(directory)/'coarse_layout_accepted.json').read_text())
            saved=json.loads((Path(directory)/'coarse_layout_checkpoint.json').read_text())['candidate']
            report=json.loads((Path(directory)/'coarse_spatial_judge.json').read_text())
            self.assertEqual(accepted,saved)
            self.assertEqual(accepted['placements'][0]['position'],[0,-1,0])
            self.assertEqual(report['readiness'],'ready_for_3d_audit')
            self.assertTrue(report['deferred_findings'])
        self.assertNotIn('spatial_revision_0', calls)
        self.assertEqual(plan.spatial.requirements.minimum_route_width_m, .8)
        self.assertLess(calls.index('coarse_layout_0'), calls.index('recipe_base_0'))

    def test_visual_and_repair_requests_retain_scene_contract(self):
        from staged_planner import Decomposition, Recipe, Layout, assemble_plan
        from scene_execution import visual_request
        from general_repairs import allowed_repair_paths, _repair_candidate_excerpt
        plan = assemble_plan(Decomposition.model_validate(outline_data()),
                             {'base': Recipe.model_validate(recipe_data())}, Layout.model_validate(layout_data()),
                             spatial=SpatialContract.model_validate(contract_data()))
        request = visual_request(plan, 'scene')
        self.assertEqual(request['spatial_contract']['routes'][0]['width'], .8)
        order = {'stage': 'layout', 'component_ids': ['base'], 'part_ids': []}
        paths = allowed_repair_paths(plan, [order])
        self.assertIn('/spatial/routes/0/points', paths)
        self.assertNotIn('/spatial/routes/0/width', paths)
        self.assertNotIn('/spatial/routes/0/start', paths)
        excerpt = _repair_candidate_excerpt(plan.model_dump(mode='json'), plan, order, paths)
        self.assertEqual(len(excerpt['components']), 2)
        self.assertIsNotNone(excerpt['spatial_contract'])

    def test_recipe_size_mismatch_routes_to_geometry_repair(self):
        from staged_planner import Decomposition, Recipe, Layout, assemble_plan
        from general_scene import compile_general
        from general_repairs import repair_work_orders
        plan = assemble_plan(Decomposition.model_validate(outline_data()),
                             {'base': Recipe.model_validate(recipe_data())}, Layout.model_validate(layout_data()))
        executable, _ = to_general_plan(plan)
        bounds = planned_envelope_bounds(executable)
        bounds['base'][1][0] -= .1
        report = judge_general_geometry(executable, bounds, receipts={})
        mismatch = next(f for f in report['findings'] if 'parametric size' in f['evidence'])
        self.assertEqual(mismatch['repair_target'], 'geometry')
        orders = repair_work_orders(plan, {'findings': [mismatch]}, {'status': 'not_run'})
        self.assertEqual(orders['orders'][0]['stage'], 'geometry')

    def test_route_endpoints_must_physically_meet_named_access(self):
        data = contract_data(); data['routes'][0]['points'][-1] = [1.4, -1.4]
        self.assertTrue(any('endpoint' in f['evidence'] for f in self.audit(data)['findings']))

    def test_fitted_window_components_are_exempt_only_from_their_own_approach_volume(self):
        data=contract_data()
        data['zones'].append({'id':'tiny_study_zone','purpose':'a nearby furniture activity',
            'center':[0,0],'size':[.3,.3],'component_ids':['sash']})
        data['openings'].append({'id':'north_window','kind':'window','wall':'x_max','offset':0,
            'width':1,'height':1,'sill_height':1,'approach_depth':.5,'purpose':'operable sash',
            'fitted_component_ids':['sash'],'fixture_projection_m':.3})
        contract=SpatialContract.model_validate(data)
        objects=probe_objects()+[SimpleNamespace(id='sash',collision_role='solid'),
                                 SimpleNamespace(id='plant',collision_role='solid')]
        bounds={'base':[[-.75,-.5,0],[.75,.5,.2]],'ornament':[[-.15,-.15,.2],[.15,.15,.7]],
            'sash':[[1.8,-.4,1],[2.02,.4,2]],'plant':[[1.55,-.2,1.1],[1.7,.2,1.8]]}
        poses={id:(0,np.mean(value,axis=0)) for id,value in bounds.items()}
        report=audit_spatial(contract,objects,[4,4,3],bounds,poses)
        window_issues=[f for f in report['findings'] if f['constraint_id']=='north_window']
        self.assertTrue(any('plant' in f['component_ids'] for f in window_issues))
        self.assertFalse(any('sash' in f['component_ids'] for f in window_issues))
        self.assertFalse(any(f['constraint_id']=='tiny_study_zone' and 'sash' in f['component_ids']
                             for f in report['findings']))

        bounds['sash']=[[1.45,-.4,1],[1.67,.4,2]]
        poses['sash']=(0,np.mean(bounds['sash'],axis=0))
        too_deep=audit_spatial(contract,objects,[4,4,3],bounds,poses)
        self.assertTrue(any(f['constraint_id']=='north_window' and 'interior_projection_m' in f['evidence']
                            for f in too_deep['findings']))

    def test_fitted_window_diagnostic_computes_parent_local_repair_pose(self):
        data=contract_data()
        data['openings'].append({'id':'east_window','kind':'window','wall':'x_max','offset':0,
            'width':1,'height':1,'sill_height':1,'approach_depth':.5,'purpose':'operable sash',
            'fitted_component_ids':['sash'],'fixture_projection_m':.3})
        contract=SpatialContract.model_validate(data)
        shell=SimpleNamespace(id='room_shell',collision_role='shell',parent=None)
        sash=SimpleNamespace(id='sash',collision_role='solid',parent='room_shell',mount='origin',
            dimensions=(.2,.8,1.76))
        bounds={'room_shell':[[-2,-2,-.2],[2,2,2.8]],
                'sash':[[1.8,-.4,.8],[2.02,.4,1.8]]}
        poses={'room_shell':(0,np.array([0,0,-.2])),
               'sash':(0,np.array([1.91,0,.6]))}
        report=audit_spatial(contract,[shell,sash],[4,4,3],bounds,poses,parameters={},
                             local_positions={'sash':[2,0,'h/2 - 0.08']})
        finding=next(item for item in report['findings'] if item['constraint_id']=='east_window')
        self.assertEqual(finding['measured']['parent_id'],'room_shell')
        self.assertEqual(finding['measured']['position_frame'],'parent_local')
        np.testing.assert_allclose(finding['measured']['current_local_position_m'],[2,0,.8])
        np.testing.assert_allclose(finding['measured']['required_world_center_shift_m'],[-.02,0,.2])
        np.testing.assert_allclose(finding['measured']['recommended_local_position_m'],[1.98,0,1.0])

    def test_fitted_window_diagnostic_includes_wall_normal_parent_local_shift(self):
        data=contract_data()
        data['openings'].append({'id':'north_window','kind':'window','wall':'y_min','offset':0,
            'width':1.4,'height':1,'sill_height':1,'approach_depth':.5,'purpose':'daylight',
            'fitted_component_ids':['north_sash'],'fixture_projection_m':.3})
        contract=SpatialContract.model_validate(data)
        shell=SimpleNamespace(id='room_shell',collision_role='shell',parent=None)
        sash=SimpleNamespace(id='north_sash',collision_role='solid',parent='room_shell',mount='origin')
        bounds={'base':[[-.75,-.5,0],[.75,.5,.2]],
            'ornament':[[-.15,-.15,.2],[.15,.15,.7]],
            'room_shell':[[-2,-2,-.2],[2,2,2.8]],
            'north_sash':[[-.7,-.09,1],[.7,.09,2]]}
        poses={key:(0,np.mean(value,axis=0)) for key,value in bounds.items()}
        report=audit_spatial(contract,[*probe_objects(),shell,sash],[4,4,3],bounds,poses,
            local_positions={'north_sash':[0,0,.8]})
        finding=next(item for item in report['findings'] if item['constraint_id']=='north_window')
        measured=finding['measured']
        np.testing.assert_allclose(measured['required_world_center_shift_m'],[0,-1.91,0])
        np.testing.assert_allclose(measured['recommended_local_position_m'],[0,-1.91,.8])
        self.assertAlmostEqual(measured['target_wall_plane_center_m'],-1.91)
        np.testing.assert_allclose(measured['opening_world_center_m'],[0,-2,1.5])

    def test_wrong_window_yaw_does_not_offer_an_impossible_translation(self):
        data=contract_data()
        data['openings'].append({'id':'east_window','kind':'window','wall':'x_max','offset':0,
            'width':1.4,'height':1,'sill_height':1,'approach_depth':.5,'purpose':'daylight',
            'fitted_component_ids':['sash'],'fixture_projection_m':.3})
        shell=SimpleNamespace(id='room_shell',collision_role='shell',parent=None)
        sash=SimpleNamespace(id='sash',collision_role='solid',parent='room_shell',mount='origin')
        bounds={'room_shell':[[-2,-2,-.2],[2,2,2.8]],'sash':[[.6,-.1,1],[2,.1,2]]}
        poses={'room_shell':(0,np.array([0,0,-.2])),'sash':(90,np.array([1.3,0,1]))}
        report=audit_spatial(SpatialContract.model_validate(data),[shell,sash],[4,4,3],bounds,poses,
            local_positions={'sash':[1.3,0,1.2]})
        issue=next(f for f in report['findings'] if 'does not fit opening' in f['evidence'])
        self.assertFalse(issue['measured']['translation_feasible'])
        self.assertIsNone(issue['measured']['recommended_local_position_m'])
        self.assertIn('Correct yaw',issue['measured']['repair_basis'])

    def test_architectural_shell_does_not_block_opening_or_empty_zone_reservations(self):
        data={'zones':[{'id':'room_zone','purpose':'room','center':[0,0],'size':[4,4],
                'component_ids':['room_shell','plant']}],
            'openings':[{'id':'east_window','kind':'window','wall':'x_max','offset':0,
            'width':1,'height':1,'sill_height':1,'approach_depth':.5,'purpose':'daylight'}],
            'open_spaces':[{'intent_id':'window_light','center':[1.75,0],'size':[.5,1],
                'bottom':1,'height':1}]}
        contract=SpatialContract.model_validate(data)
        objects=[SimpleNamespace(id='room_shell',collision_role='shell',parts=[
                    SimpleNamespace(id='window_frame',semantic='Sash window frame'),
                    SimpleNamespace(id='closed_wall_panel',semantic='East perimeter wall')]),
                 SimpleNamespace(id='plant',collision_role='solid')]
        bounds={'room_shell':[[-2,-2,0],[2,2,3]],'plant':[[1.65,-.1,1.1],[1.85,.1,1.8]]}
        poses={'room_shell':(0,np.zeros(3)),'plant':(0,np.mean(bounds['plant'],axis=0))}
        shell_parts={'room_shell':{
            'window_frame':[[1.8,-.5,1],[2,-.4,2]],
            'closed_wall_panel':[[1.8,-2,0],[2,2,3]]}}
        report=audit_spatial(contract,objects,[4,4,3],bounds,poses,shell_parts=shell_parts)
        window_issues=[f for f in report['findings'] if f['constraint_id']=='east_window']
        reserve_issues=[f for f in report['findings'] if f['constraint_id']=='design_empty_window_light']
        self.assertTrue(any('plant' in f['component_ids'] for f in window_issues))
        self.assertTrue(any('plant' in f['component_ids'] for f in reserve_issues))
        self.assertTrue(any('closed_wall_panel' in f.get('part_ids',[]) for f in window_issues))
        self.assertTrue(all('window_frame' not in f.get('part_ids',[]) for f in window_issues))
        self.assertTrue(all('room_shell' not in f['component_ids'] for f in reserve_issues))

    def test_walkable_floor_covering_can_overlap_seating_and_reserves(self):
        from types import SimpleNamespace as NS
        from staged_planner import Layout
        objects=[NS(id='base',parent=None,support=None,mount='origin',surface_contact=None,
                    collision_role='solid',grounded=True,
                    access_required=True,dimensions=(1.5,1,.2)),
                 NS(id='rug',parent=None,support=None,mount='origin',surface_contact=None,
                    collision_role='floor_covering',grounded=True,
                    access_required=False,dimensions=(2,2,.02))]
        outline=NS(objects=objects,extent=(4,4,3),parameters={},edit_checks=[])
        layout=Layout.model_validate({'placements':[{'id':'base','position':[0,0,0],'yaw_deg':0},
            {'id':'rug','position':[0,0,0],'yaw_deg':0}]})
        report=coarse_layout(outline,layout,SpatialContract.model_validate(contract_data()))
        self.assertEqual(report['status'],'passed',report['findings'])

    def test_entire_footprint_must_fit_zone(self):
        data = contract_data(); data['zones'][0]['size'] = [1, 1]
        self.assertTrue(any('zone' in f['evidence'] for f in self.audit(data)['findings']))

    def test_door_leaf_cannot_face_a_destination_object(self):
        from staged_planner import Decomposition
        from test_staged_planner import outline_data
        outline=Decomposition.model_validate(outline_data())
        door=outline.objects[0].model_copy(update={'id':'entrance_door','category':'architecture'})
        rug=outline.objects[1].model_copy(update={'id':'entry_rug','parent':None,'mount':'origin'})
        objects=[door,rug]
        data={
            'zones':[{'id':'entry_zone','purpose':'entry','center':[0,0],'size':[4,4],
                      'component_ids':['entrance_door','entry_rug']}],
            'openings':[{'id':'entry','kind':'entrance','wall':'y_min','offset':0,'width':1,
                         'height':2.1,'approach_depth':.8,'purpose':'clear entrance'}],
            'relations':[{'id':'door_faces_rug','kind':'facing','a':'entrance_door','b':'entry_rug',
                          'minimum_m':.3,'reason':'The door opens toward the rug.'}]}

        with self.assertRaisesRegex(ValueError,'door leaf is oriented by its hinge/open state'):
            validate_spatial_contract(SpatialContract.model_validate(data),objects,[4,4,3])

    def test_entrance_door_leaf_must_be_tied_to_its_opening_and_jamb(self):
        data=contract_data()
        data['openings'][0]['swing_leaf_ids']=['door_leaf']
        contract=SpatialContract.model_validate(data)
        objects=probe_objects()+[SimpleNamespace(id='door_leaf',semantic='Open entrance door leaf',
            parent=None,collision_role='solid',access_required=False)]
        extent=[4,4,3]
        good_bounds={'base':[[-.75,-.5,0],[.75,.5,.2]],
            'ornament':[[-.15,-.15,.2],[.15,.15,.7]],
            'door_leaf':[[-.85,-2.,0],[-.75,-.74,2.1]]}
        poses={key:(0,np.mean(value,axis=0)) for key,value in good_bounds.items()}
        good=audit_spatial(contract,objects,extent,good_bounds,poses)
        self.assertFalse(any(f['constraint_id']=='entrance_leaf:entrance:door_leaf' for f in good['findings']))

        misplaced={**good_bounds,'door_leaf':[[1.5,-2.,0],[1.6,-.74,2.1]]}
        poses['door_leaf']=(0,np.mean(misplaced['door_leaf'],axis=0))
        bad=audit_spatial(contract,objects,extent,misplaced,poses)
        finding=next(f for f in bad['findings'] if f['constraint_id']=='entrance_leaf:entrance:door_leaf')
        self.assertIn('beside a jamb',finding['evidence'])

    def test_door_like_component_cannot_be_unassociated_with_entrance(self):
        data=contract_data()
        objects=probe_objects()+[SimpleNamespace(id='door_leaf',semantic='Open entrance door leaf',
            parent=None,collision_role='solid',access_required=False)]
        with self.assertRaisesRegex(ValueError,'door_leaf'):
            validate_spatial_contract(SpatialContract.model_validate(data),objects,[4,4,3])

    def test_door_handle_context_is_not_inferred_as_an_extra_swing_leaf(self):
        data=contract_data()
        data['openings'][0]['swing_leaf_ids']=['door_leaf']
        objects=probe_objects()+[
            SimpleNamespace(id='door_leaf', semantic='Open entrance door leaf',
                            parent='shell', collision_role='solid', access_required=False),
            SimpleNamespace(id='entry_handle', category='door_hardware',
                            semantic='Lever handle mounted on the door leaf',
                            parent='door_leaf', collision_role='solid', access_required=False),
        ]
        validate_spatial_contract(SpatialContract.model_validate(data), objects, [4,4,3])

    def test_open_door_diagnostic_reports_parent_relative_edge_on_yaw(self):
        from scene_spatial import audit_spatial
        data={'zones':[{'id':'arrival','purpose':'entry','center':[0,0],
                'size':[4,4],'component_ids':['door_leaf']}],
            'openings':[{'id':'entry','kind':'entrance','wall':'y_max','offset':0,
                'width':1.0,'height':2.1,'approach_depth':.5,'purpose':'entry',
                'swing_leaf_ids':['door_leaf']}]}
        contract=SpatialContract.model_validate(data)
        objects=[SimpleNamespace(id='door_leaf',parent='shell',collision_role='solid')]
        bounds={'door_leaf':[[-.5,1.85,0],[.5,1.95,2.1]]}
        poses={'door_leaf':(0,np.array([0.,1.9,0.])),
               'shell':(0,np.zeros(3))}
        report=audit_spatial(contract,objects,[4,4,3],bounds,poses)
        finding=next(f for f in report['findings'] if f['constraint_id']=='entrance_leaf:entry:door_leaf')
        measured=finding['measured']
        self.assertEqual(measured['parent_world_yaw_deg'],0.)
        self.assertEqual(measured['quarter_turn_yaw_options'][0]['relative_yaw_deg'],-90.)
        self.assertAlmostEqual(measured['quarter_turn_yaw_options'][0]['projected_jamb_axis_thickness_m'],.1)
        self.assertTrue(measured['quarter_turn_yaw_options'][0]['fits_edge_on_limit'])

    def test_directional_group_relation_rejects_a_table_behind_its_seat(self):
        data={'zones':[{'id':'room','purpose':'test room','center':[0,0],
                'size':[10,10],'component_ids':['sofa','table']}],
            'openings':[{'id':'entry','kind':'entrance','wall':'y_min','offset':0,
                'width':1,'height':2.1,'approach_depth':.5,'purpose':'room entry'}],
            'relations':[{'id':'table_in_front_of_sofa','kind':'in_front_of','a':'table','b':'sofa',
                'minimum_m':.1,'reason':'The table belongs between the sofa and the opposing seats.',
                'design_relation_id':'table_in_front_of_sofa'}]}
        contract=SpatialContract.model_validate(data)
        objects=[SimpleNamespace(id='sofa',parent=None,collision_role='solid'),
                 SimpleNamespace(id='table',parent=None,collision_role='solid')]
        # Sofa faces -Y. This candidate puts the table behind it on +Y.
        bounds={'sofa':[[-1.2,-1.45,0],[1.2,-.55,.9]],
                'table':[[-.8,.1,0],[.8,.6,.5]]}
        poses={'sofa':(0,np.array([0.,-1.,0.])),
               'table':(0,np.array([0.,.35,0.]))}
        report=audit_spatial(contract,objects,[10,10,3],bounds,poses)
        finding=next(f for f in report['findings'] if f['constraint_id']=='table_in_front_of_sofa')
        self.assertLess(finding['measured']['directional_gap_m'],-.3)
        self.assertEqual(finding['measured']['design_relation_id'],'table_in_front_of_sofa')
        self.assertEqual(finding['measured']['a_world_yaw_deg'],0.)
        self.assertEqual(finding['measured']['relative_center_in_b_local_xy_m'],[0.0,1.35])
        self.assertEqual(finding['measured']['expected_a_direction_in_b_local_xy'],[0.0,-1.0])

        # Strongly reversed composition is a pre-build gate, so a known bad
        # grouping is corrected before furniture acquisition and full renders.
        from test_staged_planner import outline_data,layout_data
        outline=Decomposition.model_validate(outline_data())
        outline.objects[1].parent=None;outline.objects[1].mount='origin'
        outline.objects[1].access_required=False
        coarse_data=contract_data()
        coarse_data['relations']=[{'id':'ornament_in_front_of_base','kind':'in_front_of',
            'a':'ornament','b':'base','minimum_m':.1,
            'reason':'The detail sits on the viewer side of its anchor for direct examination.',
            'design_relation_id':'display_detail_in_front'}]
        coarse_contract=SpatialContract.model_validate(coarse_data)
        placed=layout_data()
        placed['placements'][1]['position']=[0,1.0,0]
        preflight=coarse_preflight(outline,Layout.model_validate(placed),coarse_contract)
        self.assertEqual(preflight['readiness'],'blocked')
        self.assertTrue(any(f.get('constraint_id')=='ornament_in_front_of_base'
                            for f in preflight['blocking_findings']))

    def test_strong_directional_failure_blocks_even_without_design_relation_id(self):
        from test_staged_planner import outline_data, layout_data
        outline=Decomposition.model_validate(outline_data())
        outline.objects[1].parent=None
        outline.objects[1].mount='origin'
        outline.objects[1].access_required=False
        data=contract_data()
        data['relations']=[{'id':'ornament_in_front_of_base','kind':'in_front_of',
            'a':'ornament','b':'base','minimum_m':.1,
            'reason':'The detail belongs on the viewer side of its support.'}]
        layout=layout_data()
        layout['placements'][1]['position']=[0,1.0,0]
        report=coarse_preflight(outline,Layout.model_validate(layout),SpatialContract.model_validate(data))
        self.assertEqual(report['readiness'],'blocked')
        finding=next(f for f in report['blocking_findings']
                     if f.get('constraint_id')=='ornament_in_front_of_base')
        self.assertLess(finding['measured']['directional_gap_m'],-.30)

    def test_room_spanning_wall_detail_envelope_does_not_block_use_space(self):
        from types import SimpleNamespace as NS
        shell=NS(id='room_shell',parent=None,mount='origin',surface_contact=None,
            dimensions=(4,4,3),category='architecture',internal_parts=[],collision_role='shell',
            grounded=True,access_required=False)
        paneling=NS(id='wall_paneling',parent='room_shell',mount='origin',surface_contact=None,
            dimensions=(4,4,3),category='architecture',internal_parts=[NS(id='panel_bay')],
            collision_role='solid',grounded=False,access_required=False)
        seat=NS(id='seat',parent=None,mount='origin',surface_contact=None,
            dimensions=(1,1,.8),category='furniture',internal_parts=[],collision_role='solid',
            grounded=True,access_required=True)
        outline=NS(objects=[shell,paneling,seat],extent=(4,4,3),parameters={},edit_checks=[])
        layout=Layout.model_validate({'placements':[
            {'id':'room_shell','position':[0,0,0],'yaw_deg':0},
            {'id':'wall_paneling','position':[0,0,0],'yaw_deg':0},
            {'id':'seat','position':[0,1.5,0],'yaw_deg':0}]})
        spatial=SpatialContract.model_validate({
            'zones':[{'id':'seat_zone','purpose':'reading seat','center':[0,0],
                      'size':[4,4],'component_ids':['seat']}],
            'openings':[{'id':'entry','kind':'entrance','wall':'y_min','offset':0,'width':1,
                         'height':2.1,'approach_depth':.8,'purpose':'room entry'}],
            'access_areas':[{'id':'seat_access','owner_id':'seat','center':[0,-1],
                             'size':[.8,.8],'purpose':'enter the seat'}],
            'routes':[{'id':'entry_to_seat','start':'entry','end':'seat_access',
                       'points':[[0,-1.6],[0,.5]],'width':.5,'purpose':'reach the seat'}]})

        report=coarse_layout(outline,layout,spatial)

        self.assertEqual(report['status'],'passed',report['findings'])
        from layout_solver import _route
        paths,failure=_route(outline,layout,spatial)
        self.assertIsNone(failure)
        self.assertIn('entry_to_seat',paths)

        cabinet=NS(id='cabinet',parent=None,mount='origin',surface_contact=None,
            dimensions=(.8,.8,1),category='furniture',internal_parts=[],collision_role='solid',
            grounded=True,access_required=False)
        blocked_outline=NS(objects=[shell,paneling,seat,cabinet],extent=(4,4,3),parameters={},edit_checks=[])
        blocked_layout=Layout.model_validate({'placements':[
            {'id':'room_shell','position':[0,0,0],'yaw_deg':0},
            {'id':'wall_paneling','position':[0,0,0],'yaw_deg':0},
            {'id':'seat','position':[0,1.5,0],'yaw_deg':0},
            {'id':'cabinet','position':[0,.5,0],'yaw_deg':0}]})
        blocked=coarse_layout(blocked_outline,blocked_layout,spatial)
        self.assertTrue(any(f['constraint_id']=='seat_access' and 'cabinet' in f['component_ids']
                            for f in blocked['findings']))

    def test_facing_detects_backwards_object(self):
        data = contract_data()
        data['relations'] = [{'id': 'look_at', 'kind': 'facing', 'a': 'base', 'b': 'ornament', 'reason': 'view target'}]
        contract = SpatialContract.model_validate(data)
        objects = probe_objects()
        bounds = {'base': [[-.3, -.3, 0], [.3, .3, .5]],
                  'ornament': [[-.2, -1.2, 0], [.2, -.8, .5]]}
        poses = {'base': (180, np.zeros(3)), 'ornament': (0, np.array([0, -1, 0]))}
        report = audit_spatial(contract, objects, [4, 4, 3], bounds, poses)
        finding=next(f for f in report['findings'] if f.get('constraint_id')=='look_at')
        self.assertEqual(finding['measured']['a_world_yaw_deg'],180.)
        self.assertGreater(finding['measured']['front_target_angle_deg'],90.)
        self.assertTrue(finding['measured']['minimum_distance_passed'])
        self.assertTrue(finding['measured']['facing_angle_passed'] is False)

    def test_windows_check_sill_height_instead_of_entire_floor_column(self):
        data = contract_data()
        data['openings'].append({'id': 'window', 'kind': 'window', 'wall': 'x_max', 'offset': 0,
                                 'width': 1, 'height': 1, 'sill_height': 1, 'approach_depth': .5, 'purpose': 'daylight'})
        low = self.audit(data, extra=[[1.6, -.4, 0], [1.9, .4, .7]])
        high = self.audit(data, extra=[[1.6, -.4, 0], [1.9, .4, 1.7]])
        self.assertFalse(any(f.get('constraint_id') == 'window' for f in low['findings']))
        self.assertTrue(any(f.get('constraint_id') == 'window' for f in high['findings']))

    def test_spatial_conflicts_reach_blender_geometry_judge_before_repair(self):
        calls = []
        async def complete(stage, instructions, payload):
            calls.append(stage)
            if stage == 'request_contract_0': return {'asset_boundary': 'scene', 'quality_target': 'prototype'}
            if stage == 'decomposition_0': return outline_data()
            if stage == 'spatial_plan_0': return contract_data()
            if stage == 'coarse_layout_0':
                data = layout_data(); data['placements'][0]['position'] = [0, -1, 0]; return data
            if stage == 'coarse_layout_repair_1':
                raise AssertionError('The coarse envelope is not authoritative for route usability')
            if stage == 'recipe_base_0': return recipe_data()
            raise AssertionError(stage)
        with tempfile.TemporaryDirectory() as directory:
            plan = StagedScenePlanner('unused', directory, 'scene', complete=complete).plan('display scene')
            self.assertIsNotNone(plan.spatial)
            self.assertLess(calls.index('coarse_layout_0'), calls.index('recipe_base_0'))
            self.assertTrue((Path(directory) / 'coarse_spatial_judge.json').is_file())
            coarse=json.loads((Path(directory)/'coarse_spatial_judge.json').read_text())
            self.assertEqual(coarse['readiness'],'ready_for_3d_audit')
            executable, _ = to_general_plan(plan,check_layout=False)
            bounds = planned_envelope_bounds(executable)
            planned=judge_general_geometry(executable, bounds)
            self.assertEqual(planned['status'],'needs_revision')
            self.assertTrue(any(f.get('constraint_id')=='entry_path' for f in planned['findings']))
            # Exported bounds (rather than declarations) must trigger the same checks.
            bounds['ornament'] = [[-.15, -1.7, 0], [.15, -1.4, .5]]
            actual = judge_general_geometry(executable, bounds)
            self.assertTrue(any('entry_path' == f.get('constraint_id') for f in actual['findings']))
            with self.assertRaisesRegex(ValueError, 'scene relations'):
                edit_general(executable, 'base_width', 3.0)
            edited=edit_general(executable,'base_width',3.0,check_layout=False)
            self.assertEqual(judge_general_geometry(edited,planned_envelope_bounds(edited))['status'],'needs_revision')
            restored = type(executable).model_validate_json(executable.model_dump_json())
            self.assertEqual(restored.spatial.model_dump(), plan.spatial.model_dump())

    def test_edit_scenario_spatial_conflicts_are_deferred_to_measured_geometry(self):
        data = contract_data()
        data['access_areas'][0].update(center=[1.25, 0], size=[1., 1.])
        data['routes'][0]['points'] = [[0, -1.6], [1.25, -1.6], [1.25, 0]]
        outline = outline_data()
        outline['edit_checks'] = [{'parameter': 'base_width', 'delta': .2, 'intent': 'widen support'}]
        calls = []
        async def complete(stage, instructions, payload):
            calls.append(stage)
            if stage == 'request_contract_0': return {'asset_boundary': 'scene', 'quality_target': 'prototype'}
            if stage == 'decomposition_0': return outline
            if stage == 'spatial_plan_0': return data
            if stage == 'coarse_layout_0': return layout_data()
            if stage == 'coarse_layout_repair_1':
                raise AssertionError('Soft edit-scenario clearances must reach Blender measurement')
            if stage == 'recipe_base_0': return recipe_data()
            raise AssertionError(stage)
        with tempfile.TemporaryDirectory() as directory:
            plan = StagedScenePlanner('unused', directory, 'edit_scene', complete=complete).plan('editable display scene')
            report = json.loads((Path(directory)/'coarse_spatial_judge.json').read_text())
        self.assertEqual(report['status'],'passed')
        self.assertEqual(report['readiness'],'ready_for_3d_audit')
        self.assertEqual(report['edit_scenarios'][0]['status'],'needs_revision')
        self.assertTrue(any(f.get('scenario')=='base_width' for f in report['deferred_findings']))
        self.assertEqual(plan.spatial.access_areas[0].center[0],1.25)


if __name__ == '__main__':
    unittest.main()
