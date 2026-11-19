"""Executable intent and real mounting regressions, independent of room type."""
import copy
from pathlib import Path
import sys
import unittest
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
sys.path.insert(0,str(Path(__file__).resolve().parent))
from scene_design import SceneDesign
from scene_composition import validate_concept,validate_lowering,validate_bound_relationships,lower_typed_relationships
from scene_composition import ExperienceView
from surface_mounting import SurfaceContact,resolve,rotation,audit_measured,fit_contact_pose
from general_scene import GeneralPlan,SceneObject,Part,compile_general,_planned_envelopes_and_poses
from staged_planner import Decomposition,Layout,coarse_preflight
from scene_spatial import SpatialContract,SpatialRelation,validate_spatial_contract
from planning_diagnostics import PlanningValidationError,validate_together
from test_staged_planner import outline_data,layout_data
from test_scene_spatial import contract_data


def concept_data():
    return dict(whole_space='A display mass with a quiet approach and discovery from the side.',
        groups=[dict(id='display',role='A related display group',choice_ids=['structure','detail'],
            anchor_choice_id='structure',organization='The smaller object belongs to the supporting mass.',
            density_and_rhythm='Dense around the support, calm and open on approach.',
            relationships=[dict(id='detail_supported_by_structure',a_choice_id='detail',
                b_choice_id='structure',kind='supported_by',
                reason='The smaller display detail rests on its supporting structure.')])],
        links=[],open_spaces=[dict(id='pause',purpose='A calm pause to read the display without competing masses.')],
        views=[dict(id='arrival',experience='A human arrival reveals the display and open approach.',group_ids=['display'])],
        editing_priorities='Remove competing masses before adding any new detail.')


def design_data():
    return dict(room_identity='A coherent display place',users_and_activities='People approach and examine the exhibit.',
        spatial_composition='A mass and open approach together make a place.',focal_hierarchy='The exhibit precedes its small support.',
        material_palette=['stone'],lighting_atmosphere='Soft directional daylight',composition=concept_data(),
        choices=[dict(id=id,layer='furniture',activity='view',selection='A meaningful display element',
                      quality_reason='It contributes to the chosen display use.') for id in ['structure','detail']])


class CompositionTests(unittest.TestCase):
    def group_link_fixture(self,binding_mode='semantic'):
        raw=design_data()
        raw['choices']=[
            dict(id='envelope',layer='furniture',activity='light',selection='A room light source',
                 quality_reason='It contributes to the room lighting.'),
            dict(id='rest',layer='furniture',activity='sleep',selection='A tatami sleep area',
                 quality_reason='It provides the primary sleep activity.')]
        raw['composition']={
            'whole_space':'A room with a calm sleep area and useful daylight.',
            'groups':[
                dict(id='envelope_group',role='Daylight and room envelope',choice_ids=['envelope'],
                     anchor_choice_id='envelope',organization='The room receives daylight.',
                     density_and_rhythm='The light source remains visually quiet.'),
                dict(id='rest_group',role='Sleep area',choice_ids=['rest'],
                     anchor_choice_id='rest',organization='The tatami defines the sleep area.',
                     density_and_rhythm='Keep the low sleep mass calm.')],
            'links':[dict(id='daylight_to_rest',a='envelope_group',b='rest_group',
                          intent='Daylight supports the tatami sleep area.',binding_mode=binding_mode)],
            'open_spaces':[],
            'views':[dict(id='arrival',experience='A person entering sees the sleep area.',
                          group_ids=['envelope_group','rest_group'])],
            'editing_priorities':'Preserve the useful daylight and a calm sleep area.'}
        design=SceneDesign(**raw)
        outline=Decomposition.model_validate(outline_data())
        outline.design_bindings={'envelope':['base'],'rest':['ornament']}
        spatial=SpatialContract.model_validate(contract_data())
        spatial.experience_views=[ExperienceView(intent_id='arrival',position=(1,1,1.6),target=(0,0,1))]
        return design,outline,spatial

    def test_semantic_group_link_is_preserved_without_inventing_geometry(self):
        design,outline,spatial=self.group_link_fixture()
        lowered=lower_typed_relationships(spatial,design,outline)
        self.assertEqual(len(lowered.composition_links),1)
        self.assertEqual(lowered.composition_links[0].link_id,'daylight_to_rest')
        self.assertEqual(lowered.composition_links[0].relation_ids,[])
        self.assertEqual(lowered.composition_links[0].route_ids,[])
        validate_lowering(lowered,design,outline)

    def test_spatial_group_link_requires_a_real_cross_group_relation(self):
        from planning_diagnostics import PlanningValidationError
        design,outline,spatial=self.group_link_fixture('spatial')
        lowered=lower_typed_relationships(spatial,design,outline)
        with self.assertRaises(PlanningValidationError) as caught:
            validate_lowering(lowered,design,outline)
        codes={item['code'] for item in caught.exception.measurements['issues']}
        self.assertIn('unrealized_group_link',codes)

    def entrance_link_fixture(self):
        design,outline,spatial=self.group_link_fixture('circulation')
        spatial.access_areas[0].owner_id='ornament'
        spatial.openings[0].swing_leaf_ids=['base']
        return design,outline,spatial

    def test_circulation_links_bind_real_entrances_in_either_direction(self):
        for reverse in (False,True):
            for fixture_field in ('swing_leaf_ids','fitted_component_ids'):
                with self.subTest(reverse=reverse,fixture_field=fixture_field):
                    design,outline,spatial=self.entrance_link_fixture()
                    spatial.openings[0].swing_leaf_ids=[]
                    setattr(spatial.openings[0],fixture_field,['base'])
                    if reverse:
                        link=design.composition.links[0]
                        link.a,link.b=link.b,link.a
                    lowered=lower_typed_relationships(spatial,design,outline)
                    self.assertEqual(lowered.composition_links[0].route_ids,['entry_path'])
                    validate_lowering(lowered,design,outline)

    def test_cross_group_relation_does_not_suppress_circulation_binding(self):
        design,outline,spatial=self.entrance_link_fixture()
        spatial.relations=[SpatialRelation(id='cross_group',kind='distance',a='base',b='ornament',
            maximum_m=2,reason='Both groups are close enough for the requested activity.')]
        from scene_composition import LinkBinding
        spatial.composition_links=[LinkBinding(link_id='daylight_to_rest',relation_ids=['cross_group'])]
        lowered=lower_typed_relationships(spatial,design,outline)
        self.assertEqual(lowered.composition_links[0].route_ids,['entry_path'])
        validate_lowering(lowered,design,outline)

    def test_window_unbound_entrance_or_missing_route_cannot_prove_circulation(self):
        for problem in ('window','unbound','missing_route'):
            with self.subTest(problem=problem):
                design,outline,spatial=self.entrance_link_fixture()
                if problem=='window':spatial.openings[0].kind='window'
                elif problem=='unbound':spatial.openings[0].swing_leaf_ids=[]
                else:spatial.routes=[]
                lowered=lower_typed_relationships(spatial,design,outline)
                with self.assertRaisesRegex(PlanningValidationError,'circulation link needs a route'):
                    validate_lowering(lowered,design,outline)

    def test_openings_cannot_be_mixed_into_furniture_groups_or_relations(self):
        from planning_diagnostics import PlanningValidationError
        raw=design_data()
        raw['choices'].extend([
            dict(id='north_window',layer='architecture',activity='daylight',
                 selection='A fitted timber window in the exterior wall',
                 quality_reason='It brings useful daylight into the room.'),
            dict(id='study_desk',layer='furniture',activity='study',
                 selection='A compact desk for focused homework',
                 quality_reason='It supports daily study and writing.')])
        group=raw['composition']['groups'][0]
        group['choice_ids'] += ['north_window','study_desk']
        group['relationships'].extend([
            dict(id='desk_near_display',a_choice_id='study_desk',b_choice_id='structure',kind='distance',
                 minimum_m=.4,maximum_m=1.5,reason='The desk remains near the established focal area.'),
            dict(id='window_behind_desk',a_choice_id='north_window',b_choice_id='study_desk',kind='behind',
                 reason='The window sits behind the desk for daylight.')])
        design=SceneDesign(**raw)
        with self.assertRaises(PlanningValidationError) as caught:
            validate_concept(design)
        codes={item['code'] for item in caught.exception.measurements['issues']}
        self.assertIn('opening_activity_group',codes)
        self.assertIn('opening_furnishing_relation',codes)

    def test_opening_group_can_link_daylight_intent_to_a_separate_activity_group(self):
        raw=design_data()
        raw['choices'].extend([
            dict(id='room_shell',layer='architecture',activity='room enclosure',
                 selection='A finished enclosed room shell',
                 quality_reason='It hosts the fitted openings and ambient fixture.'),
            dict(id='north_window',layer='architecture',activity='daylight',
                 selection='A fitted timber window in the exterior wall',
                 quality_reason='It brings useful daylight into the room.'),
            dict(id='linen_curtain',layer='decoration',activity='light control',
                 selection='A linen curtain fitted to the window',
                 quality_reason='It softens light and belongs to the opening assembly.'),
            dict(id='ceiling_light',layer='lighting',activity='ambient lighting',
                 selection='A flush ceiling light for general illumination',
                 quality_reason='It gives the room even ambient illumination.')])
        raw['composition']['groups'].append(dict(
            id='room_envelope',role='The room shell and its fitted architectural fixtures',
            choice_ids=['room_shell','north_window','linen_curtain','ceiling_light'],anchor_choice_id='room_shell',
            organization='The opening, treatment and ambient light belong to the envelope.',
            density_and_rhythm='Keep the opening legible and visually calm.',
            relationships=[
                dict(id='window_attached_to_shell',a_choice_id='north_window',b_choice_id='room_shell',
                    kind='attached_to',reason='The fitted window belongs to the room shell.'),
                dict(id='curtain_attached_to_window',a_choice_id='linen_curtain',b_choice_id='north_window',
                    kind='attached_to',reason='The curtain is installed directly on the window assembly.'),
                dict(id='light_attached_to_shell',a_choice_id='ceiling_light',b_choice_id='room_shell',
                    kind='attached_to',reason='The flush ambient light mounts to the ceiling shell.')]))
        raw['composition']['links']=[dict(id='daylight_to_display',a='room_envelope',b='display',
            intent='Daylight from the wall opening supports the nearby display activity.')]
        accepted=validate_concept(SceneDesign(**raw))
        self.assertEqual(accepted.composition.groups[-1].choice_ids,
                         ['room_shell','north_window','linen_curtain','ceiling_light'])

    def test_door_leaf_cannot_have_a_typed_facing_relation_in_the_frozen_design(self):
        from planning_diagnostics import PlanningValidationError
        raw=design_data()
        group=raw['composition']['groups'][0]
        group['choice_ids'] += ['entry_door','window']
        raw['choices'].extend([
            dict(id='entry_door',layer='architecture',activity='room entry',
                 selection='A hinged entrance door leaf beside the wall opening',
                 quality_reason='It provides the room entry and must swing clear.'),
            dict(id='window',layer='architecture',activity='daylight',
                 selection='A fitted timber window in the exterior wall',
                 quality_reason='It brings daylight into the room.')])
        group['relationships'].extend([
            dict(id='door_faces_window',a_choice_id='entry_door',b_choice_id='window',kind='facing',
                 minimum_m=0,maximum_m=3,tolerance_deg=30,
                 reason='The door leaf faces the window to show the arrival view.'),
            dict(id='window_near_structure',a_choice_id='window',b_choice_id='structure',kind='distance',
                 minimum_m=.2,maximum_m=2,tolerance_deg=30,
                 reason='The window and display structure occupy the same room wall.')])
        design=SceneDesign(**raw)
        with self.assertRaises(PlanningValidationError) as caught:
            validate_concept(design)
        issue=next(item for item in caught.exception.measurements['issues']
                    if item['code']=='door_facing_relation')
        self.assertEqual(issue['paths'],['/composition/groups/0/relationships/1'])
        self.assertIn('composition prose or a view intent',issue['evidence'])

    def test_aesthetic_central_breathing_room_is_not_a_protected_open_space(self):
        from planning_diagnostics import PlanningValidationError
        raw=design_data()
        raw['composition']['open_spaces']=[dict(id='central_open_floor',
            purpose='A generous visually continuous floor void at the center, separating groups and leaving the room breathing space.')]
        design=SceneDesign(**raw)
        with self.assertRaises(PlanningValidationError) as caught:
            validate_concept(design,'A near-square high-school bedroom with a tatami platform and window.')
        issues=caught.exception.measurements['issues']
        self.assertEqual([item['code'] for item in issues],['nonprogrammatic_open_space'])
        self.assertEqual(issues[0]['paths'],['/composition/open_spaces/0'])
        accepted=validate_concept(design,
            'A near-square study room with tatami, window and a clear central open floor for exercise.')
        self.assertIs(accepted,design)

    def test_natural_language_room_center_and_window_frontage_do_not_become_hard_voids(self):
        from planning_diagnostics import PlanningValidationError
        raw=design_data()
        raw['composition']['open_spaces']=[
            dict(id='center',purpose='The large continuous floor at the room center is intentionally empty, giving a sense of spaciousness.'),
            dict(id='window_frontage',purpose='The floor directly in front of the window is kept free to preserve daylight and a clear view out.')]
        with self.assertRaises(PlanningValidationError) as caught:
            validate_concept(SceneDesign(**raw),'A near-square high-school bedroom with a tatami platform and window.')
        issues=caught.exception.measurements['issues']
        self.assertEqual([item['code'] for item in issues],
                         ['nonprogrammatic_open_space','nonprogrammatic_open_space'])
        self.assertEqual([item['paths'] for item in issues],
                         [['/composition/open_spaces/0'],['/composition/open_spaces/1']])

    def test_floor_contact_solver_preserves_functional_yaw_when_translation_is_needed(self):
        contact=SurfaceContact(parent_part='floor',parent_face='z_max',child_face='z_min')
        floor={'floor':{'bounds':[[-2,-2,-.2],[2,2,0]]}}
        child=[[-.6,-.05,0],[.6,.05,2]]
        fitted=fit_contact_pose(contact,floor,child,{},[0,1.9,0],90,
            parent_pose=(0,np.zeros(3)),scene_extent=(4,4,3))
        self.assertIsNotNone(fitted)
        self.assertAlmostEqual(fitted['yaw_deg'],90)
        self.assertLess(fitted['offset'][1],1.5)
        resolve(contact,floor,child,{},fitted['offset'],fitted['yaw_deg'],
            parent_pose=(0,np.zeros(3)),scene_extent=(4,4,3))

    def test_contact_solver_projects_a_wrong_yaw_and_offset_without_changing_faces(self):
        contact=SurfaceContact(parent_part='wall',parent_face='y_min',child_face='y_max')
        parent={'wall':{'bounds':[[-2,.9,0],[2,1.1,3]]}}
        child=[[-.25,-.05,0],[.25,.05,.5]]
        with self.assertRaisesRegex(ValueError,'Mounting faces must oppose'):
            resolve(contact,parent,child,{},[3,0,1],180,
                parent_pose=(0,[0,0,0]),scene_extent=[8,6,3])
        fitted=fit_contact_pose(contact,parent,child,{},[3,0,1],180,
            parent_pose=(0,[0,0,0]),scene_extent=[8,6,3])
        self.assertIsNotNone(fitted)
        self.assertEqual(fitted['yaw_deg'],0)
        self.assertLessEqual(fitted['offset'][0],1.7531)
        resolve(contact,parent,child,{},fitted['offset'],fitted['yaw_deg'],
            parent_pose=(0,[0,0,0]),scene_extent=[8,6,3])
        self.assertEqual(contact.child_face,'y_max')
        too_wide=[[-3,-.05,0],[3,.05,.5]]
        self.assertIsNone(fit_contact_pose(contact,parent,too_wide,{},[0,0,1],0,
            parent_pose=(0,[0,0,0]),scene_extent=[8,6,3]))

    def test_typed_lowering_copies_parameters_and_resolves_component_membership(self):
        outline=Decomposition.model_validate(outline_data())
        outline.design_bindings={'structure':['base'],'detail':['ornament']}
        raw=design_data();relation=raw['composition']['groups'][0]['relationships'][0]
        relation.update(kind='distance',minimum_m=0,maximum_m=.4)
        design=SceneDesign(**raw)
        spatial=SpatialContract.model_validate(contract_data())
        spatial.relations=[SpatialRelation(id='stale_copy',a='ornament',b='base',kind='distance',
            design_relation_id=relation['id'],minimum_m=.2,maximum_m=4,reason='Incorrect duplicate transcription.')]
        lowered=lower_typed_relationships(spatial,design,outline)
        self.assertEqual(len(lowered.relations),1)
        actual=lowered.relations[0]
        self.assertEqual((actual.a,actual.b,actual.minimum_m,actual.maximum_m),('ornament','base',0,.4))
        self.assertEqual(lowered.composition_groups[0].component_ids,['base','ornament'])
        self.assertEqual(lowered.composition_groups[0].relation_ids,[actual.id])
        self.assertEqual(spatial.relations[0].maximum_m,4)

    def test_recovery_contacts_follow_actual_parent_and_allow_integrated_layers(self):
        outline=Decomposition.model_validate(outline_data())
        outline.design_bindings={'structure':['base'],'detail':['ornament']}
        design=SceneDesign(**design_data())
        validate_bound_relationships(design,outline)
        outline.objects[1].parent=None
        with self.assertRaisesRegex(ValueError,'actual parents'):
            validate_bound_relationships(design,outline)
        outline.design_bindings['detail']=['base']
        validate_bound_relationships(design,outline)
        design.composition.groups[0].relationships[0].kind='distance'
        design.composition.groups[0].relationships[0].maximum_m=1
        validate_bound_relationships(design,outline)

    def test_integrated_design_layers_do_not_become_scene_level_self_relations(self):
        from scene_composition import GroupBinding,OpenSpace,ExperienceView
        outline=Decomposition.model_validate(outline_data())
        outline.design_bindings={'structure':['base'],'detail':['base']}
        raw=design_data()
        intended=raw['composition']['groups'][0]['relationships'][0]
        intended.update(kind='distance',minimum_m=0,maximum_m=.4)
        design=SceneDesign(**raw)
        spatial=SpatialContract.model_validate(contract_data())
        spatial.relations=[SpatialRelation(id='detail_within_base',a='base',b='base',kind='distance',
            minimum_m=0,maximum_m=.4,design_relation_id='detail_within_base',
            reason='The integrated detail stays within the editable base footprint.')]
        spatial.composition_groups=[GroupBinding(group_id='display',component_ids=['base'],
            anchor_id='base',relation_ids=['detail_within_base'])]
        spatial.open_spaces=[OpenSpace(intent_id='pause',center=(1,1),size=(.4,.4))]
        spatial.experience_views=[ExperienceView(intent_id='arrival',position=(1,1,1.6),target=(0,0,1))]

        lowered=lower_typed_relationships(spatial,design,outline)

        self.assertEqual(lowered.relations,[])
        self.assertEqual(lowered.composition_groups[0].relation_ids,[])
        validate_bound_relationships(design,outline)
        validate_lowering(lowered,design,outline)

    def test_inventory_only_design_requires_real_concept_before_production(self):
        raw=design_data();raw.pop('composition')
        with self.assertRaisesRegex(ValueError,'Design whole space'):validate_concept(SceneDesign(**raw))
        self.assertIsNotNone(validate_concept(SceneDesign(**design_data())))

    def test_multichoice_group_requires_typed_relationships_to_anchor(self):
        raw=design_data();raw['composition']['groups'][0]['relationships']=[]
        with self.assertRaisesRegex(ValueError,'connect every choice to anchor'):
            validate_concept(SceneDesign(**raw))

    def test_labels_and_zones_do_not_replace_group_relationships(self):
        outline=Decomposition.model_validate(outline_data());outline.design_bindings={'structure':['base'],'detail':['ornament']}
        spatial=SpatialContract.model_validate(contract_data())
        spatial.composition_groups=[__import__('scene_composition').GroupBinding(group_id='display',
            component_ids=['base','ornament'],anchor_id='base')]
        spatial.open_spaces=[__import__('scene_composition').OpenSpace(intent_id='pause',center=(2,2),size=(.4,.4))]
        spatial.experience_views=[__import__('scene_composition').ExperienceView(intent_id='arrival',position=(2,1,1.6),target=(0,0,1))]
        # The typed design relationship is lowered through the parent graph.
        validate_lowering(spatial,SceneDesign(**design_data()),outline)
        outline.objects[1].parent=None
        with self.assertRaisesRegex(ValueError,'no executable relationship'):validate_lowering(spatial,SceneDesign(**design_data()),outline)

    def test_independent_composition_and_room_failures_are_returned_together(self):
        outline=Decomposition.model_validate(outline_data())
        outline.design_bindings={'structure':['base'],'detail':['ornament']}
        outline.objects[1].parent=None
        outline.objects[1].access_required=False
        spatial=SpatialContract.model_validate(contract_data())
        spatial.zones[0].center=(.5,0)
        spatial.relations=[SpatialRelation(id='display_spacing',kind='distance',a='base',b='ornament',
            minimum_m=.1,reason='The ornament sits beside the display base.')]
        spatial.composition_groups=[__import__('scene_composition').GroupBinding(group_id='display',
            component_ids=['base','ornament'],anchor_id='base',relation_ids=['display_spacing'])]
        spatial.open_spaces=[__import__('scene_composition').OpenSpace(intent_id='pause',center=(0,1.7),size=(.4,.4))]
        spatial.experience_views=[__import__('scene_composition').ExperienceView(intent_id='arrival',
            position=(1,1,1.6),target=(0,0,1))]
        with self.assertRaises(PlanningValidationError) as failure:
            validate_together('spatial_design',[
                ('composition_lowering',lambda:validate_lowering(spatial,SceneDesign(**design_data()),outline)),
                ('spatial_contract',lambda:validate_spatial_contract(spatial,outline.objects,outline.extent)),
            ])
        codes={issue['code'] for issue in failure.exception.measurements['issues']}
        self.assertIn('design_relationship_missing',codes)
        self.assertIn('zone_extent',codes)

    def test_protected_empty_space_blocks_packing_and_solver_preserves_it(self):
        from layout_solver import solve_layout
        outline=Decomposition.model_validate(outline_data());layout=Layout.model_validate(layout_data())
        spatial=SpatialContract.model_validate(contract_data())
        spatial.open_spaces=[__import__('scene_composition').OpenSpace(intent_id='quiet',center=(0,0),size=(1.7,1.2))]
        initial=coarse_preflight(outline,layout,spatial)
        self.assertTrue(any('design_empty_quiet' in f['evidence'] for f in initial['findings']))
        solved,receipt=solve_layout(outline,layout,spatial)
        self.assertIsNotNone(solved,receipt)
        self.assertEqual(coarse_preflight(outline,solved,spatial)['status'],'passed')

    def test_shared_choice_instances_belong_to_their_actual_spatial_groups(self):
        from scene_composition import GroupBinding,OpenSpace,ExperienceView
        raw=outline_data()
        for item in copy.deepcopy(raw['objects']):
            item['id']='other_'+item['id']
            if item.get('parent'):item['parent']='other_'+item['parent']
            raw['objects'].append(item)
        for key in ('zones','requirements'):raw[key][0]['component_ids']+=['other_base','other_ornament']
        outline=Decomposition.model_validate(raw)
        outline.design_bindings={'structure':['base'],'other_structure':['other_base'],
                                 'detail':['ornament','other_ornament']}
        raw_design=design_data();other=copy.deepcopy(raw_design['choices'][0]);other['id']='other_structure'
        raw_design['choices'].append(other)
        other_group=copy.deepcopy(raw_design['composition']['groups'][0]);other_group.update(
            id='other_display',choice_ids=['other_structure','detail'],anchor_choice_id='other_structure')
        other_group['relationships']=[dict(id='other_detail_supported_by_structure',
            a_choice_id='detail',b_choice_id='other_structure',kind='supported_by',
            reason='The second smaller detail rests on its matching support structure.')]
        raw_design['composition']['groups'].append(other_group)
        spatial=SpatialContract.model_validate(contract_data())
        spatial.composition_groups=[GroupBinding(group_id='display',component_ids=['base','ornament'],anchor_id='base'),
            GroupBinding(group_id='other_display',component_ids=['other_base','other_ornament'],anchor_id='other_base')]
        spatial.open_spaces=[OpenSpace(intent_id='pause',center=(2,2),size=(.4,.4))]
        spatial.experience_views=[ExperienceView(intent_id='arrival',position=(2,1,1.6),target=(0,0,1))]
        validate_lowering(spatial,SceneDesign(**raw_design),outline)
        spatial.composition_groups[1].component_ids=['other_base','ornament']
        with self.assertRaisesRegex(ValueError,'other_ornament'):validate_lowering(spatial,SceneDesign(**raw_design),outline)

    def test_high_level_design_receives_previous_visual_failure(self):
        import asyncio,tempfile
        from unittest.mock import patch
        from staged_planner import StagedScenePlanner,RequestContract
        class Done(Exception):pass
        feedback={'visual':{'findings':[{'evidence':'The masses split the main activity.'}]}}
        async def stage(name,schema,instructions,context,validate,*args,**kwargs):
            if name=='request_contract':return RequestContract(asset_boundary='scene',quality_target='realistic')
            self.assertEqual(name,'scene_design');self.assertEqual(context['previous_attempt_feedback'],feedback)
            raise Done()
        with tempfile.TemporaryDirectory() as directory,patch('general_assets.local_catalog',return_value=[]):
            planner=StagedScenePlanner('unused',directory,'test',replan_feedback=feedback,complete=lambda *_:None);planner.stage=stage
            with self.assertRaises(Done):asyncio.run(planner.execute('A place'))

    def test_design_revision_bypasses_layout_only_spatial_recovery(self):
        import asyncio,json,tempfile
        from unittest.mock import patch
        from staged_planner import StagedScenePlanner,RequestContract
        class Done(Exception):pass
        feedback={'visual':{'decision':{'findings':[{'severity':'blocking',
            'repair_target':'decomposition','component_ids':['optional_plant'],
            'evidence':'Optional objects crowd the requested primary group.',
            'correction':'Remove the optional plant.','user_requirement_quote':'keep the primary activity readable'}]}}}
        async def stage(name,*args,**kwargs):
            self.assertEqual(name,'request_contract')
            raise Done()
        async def forbidden_recovery(*args):
            raise AssertionError('An inventory-level issue needs design/decomposition revision')
        with tempfile.TemporaryDirectory() as directory,patch(
                'spatial_recovery.recover_spatial_scene',side_effect=forbidden_recovery):
            previous=Path(directory)/'previous';previous.mkdir()
            (previous/'request_contract_accepted.json').write_text(json.dumps(
                RequestContract(asset_boundary='scene',quality_target='realistic').model_dump()),encoding='utf-8')
            planner=StagedScenePlanner('unused',directory,'test',replan_feedback=feedback,
                replan_spatial=True,resume_runtime=Path(directory)/'previous',complete=lambda *_:None)
            planner.stage=stage
            with self.assertRaises(Done):asyncio.run(planner.execute('A place'))

    def test_room_design_prompts_prioritize_requested_functions_and_open_floor(self):
        from scene_design import DESIGN_PROMPT
        from staged_planner import SPATIAL
        self.assertIn('does not mean that every room needs separate art',DESIGN_PROMPT)
        self.assertIn('Keep a generous, visually continuous area of open floor',DESIGN_PROMPT)
        self.assertIn('Routes already express',SPATIAL)
        self.assertIn('do not duplicate these as overlapping',SPATIAL)

    def test_schema_text_repair_cannot_rewrite_unrelated_inventory(self):
        import asyncio,tempfile
        from staged_planner import StagedScenePlanner,replacement_paths
        raw=design_data();raw['room_identity']='x'
        observed=[]
        async def complete(stage,instructions,payload):
            if stage=='design_probe_0':return raw
            observed.extend(payload['allowed_paths'])
            return {'reason':'Give the existing identity a valid concise name.',
                'replacements':[{'path':'/room_identity','value':'A coherent display place'}]}
        with tempfile.TemporaryDirectory() as directory:
            planner=StagedScenePlanner('unused',directory,'test',complete=complete)
            result=asyncio.run(planner.stage('design_probe',SceneDesign,'Design a place',{},validate_concept,replacement_paths))
        self.assertEqual(observed,['/room_identity'])
        self.assertEqual([c.id for c in result.choices],['structure','detail'])

    def test_experienced_views_receive_whole_scene_judging(self):
        import tempfile
        from general_vision import review_scene_images
        observed=[]
        def reviewer(images,request,profile,output,**kwargs):
            observed.append((list(images),kwargs['scope']))
            return {'status':'passed','accepted':True,'decision':{'findings':[],'style_assessments':[]}}
        with tempfile.TemporaryDirectory() as directory:
            paths=[]
            for name in ['overview','reverse','top','front','experience_arrival','detail_base','side_base']:
                p=Path(directory)/(name+'.png');p.write_bytes(b'unit evidence placeholder');paths.append(str(p))
            review_scene_images(paths,{},'unused',Path(directory)/'result.json',reviewer=reviewer)
        self.assertTrue(any(scope=='scene' and any('experience_arrival' in p for p in images) for images,scope in observed))
        self.assertFalse(any(scope=='component' and any('experience_arrival' in p for p in images) for images,scope in observed))

    def test_camera_repair_preserves_design_intent_and_physical_layout(self):
        from general_repairs import allowed_repair_paths
        from test_visual_repair import plan_fixture
        plan=plan_fixture();plan.spatial=SpatialContract.model_validate(contract_data())
        plan.spatial.experience_views=[__import__('scene_composition').ExperienceView(intent_id='arrival',position=(1,1,1.6),target=(0,0,1))]
        paths=allowed_repair_paths(plan,[{'stage':'camera','component_ids':[]}])
        self.assertIn('/spatial/experience_views/0/position',paths)
        self.assertNotIn('/spatial/experience_views/0/intent_id',paths)
        self.assertFalse(any(p.startswith('/components') for p in paths))


class MountingTests(unittest.TestCase):
    def test_float32_contact_edge_does_not_create_a_nanometer_unsupported_sliver(self):
        from surface_mounting import covered
        surface=np.array([[-.7,-.325,.776],[.7,.325,.776]])
        patch=np.array([[-.5008856,-.28599998,.776],[-.2991144,.328000011,.776]])
        self.assertTrue(covered(patch,[surface],2))
        patch[1,1]=.329
        self.assertFalse(covered(patch,[surface],2))

    def test_mounting_failures_include_every_owner_and_real_surface_measurements(self):
        from general_scene import planned_envelope_bounds
        host=SceneObject(id='host',semantic='A wall',size=(4,.2,3),collision_role='shell',
            parts=[Part(id='wall',primitive='box',size=(4,.2,3),center=(0,0,1.5))])
        fixtures=[SceneObject(id=id,semantic='Mounted fixture',size=(.4,.1,.5),parent='host',
            position=(x,0,1),surface_contact=SurfaceContact(parent_part='wall',parent_face='y_min',child_face='y_max'),
            parts=[Part(id='plate',primitive='box',size=(.4,.1,.5),center=(0,0,.25))])
            for id,x in [('first',3),('second',-3)]]
        dependent=SceneObject(id='dependent',semantic='Child of failed mount',size=(.1,.1,.1),parent='first',
            parts=[Part(id='body',primitive='box',size=(.1,.1,.1),center=(0,0,.05))])
        plan=GeneralPlan(scene_id='mount_batch',description='Independent mounting failures',extent=(10,10,4),
                         objects=[host,*fixtures,dependent])
        with self.assertRaises(PlanningValidationError) as caught:planned_envelope_bounds(plan)
        report=caught.exception.measurements
        self.assertEqual({i['component_ids'][0] for i in report['issues']},{'first','second'})
        self.assertEqual(len(report['issues']),2)
        for issue in report['issues']:
            self.assertIn('parent_local_patch',issue['measured'])
            self.assertIn('parent_surface_bounds',issue['measured'])
            self.assertTrue(any(p.endswith('/position') for p in issue['paths']))
        self.assertTrue(any(b['scenario']=='dependent' for b in report['blocked_checks']))

    def test_detailed_preflight_preserves_mounting_owner_paths(self):
        from general_planner import PlannerScenePlan,to_general_plan
        plan=PlannerScenePlan.model_validate({'intent':{'scene_id':'mount_failure',
            'description':'A failed mounting fixture','style':'plain','extent':[10,10,4]},'components':[
            {'id':'host','semantic':'A wall','category':'structure','dimensions':[4,.2,3],
             'decomposition_reason':'Independent editable structure','required_features':['flat wall'],
             'material_requirements':['matte'],'source':{'mode':'parametric','reason':'A regular editable panel'},
             'collision_role':'shell','parts':[{'id':'wall','primitive':'box','size':[4,.2,3],'center':[0,0,1.5]}]},
            {'id':'fixture','semantic':'Mounted fixture','category':'fixture','dimensions':[.4,.1,.5],
             'decomposition_reason':'Independently mounted fixture','required_features':['plate'],
             'material_requirements':['matte'],'source':{'mode':'parametric','reason':'A regular editable plate'},
             'parent':'host','position':[3,0,1],
             'surface_contact':{'parent_part':'wall','parent_face':'y_min','child_face':'y_max'},
             'parts':[{'id':'plate','primitive':'box','size':[.4,.1,.5],'center':[0,0,.25]}]}]})
        with self.assertRaises(PlanningValidationError) as caught:to_general_plan(plan)
        issue=next(i for i in caught.exception.measurements['issues'] if i['code']=='mounting_coverage')
        self.assertEqual(issue['component_ids'],['fixture','host'])
        self.assertIn('/components/1/position',issue['paths'])
        self.assertIn('parent_local_patch',issue['measured'])

    def test_contact_uses_backplate_not_body_center_or_zone_border(self):
        contact=SurfaceContact(parent_part='wall',parent_face='y_min',child_part='plate',child_face='y_max')
        parent={'wall':{'bounds':[[-2,1.8,0],[2,2,3]]}}
        child={'plate':{'bounds':[[-.2,.27,0],[.2,.3,.5]]}}
        origin,evidence=resolve(contact,parent,[[-.2,-.3,0],[.2,.3,.5]],child,[0,0,1.5],0)
        self.assertAlmostEqual(origin[1],1.5)
        self.assertAlmostEqual(evidence['parent_local_patch'][0][1],1.8)

    def test_wrong_facing_and_window_hole_both_rejected(self):
        contact=SurfaceContact(parent_part='wall',parent_face='y_min',child_face='y_max')
        cells={'wall':{'bounds':[[-2,1.8,0],[-.3,2,3]]},'wall_001':{'bounds':[[.3,1.8,0],[2,2,3]]}}
        with self.assertRaisesRegex(ValueError,'oppose'):resolve(contact,cells,[[-.2,-.3,0],[.2,.3,.5]],{},[1,0,1],180)
        with self.assertRaisesRegex(ValueError,'opening'):resolve(contact,cells,[[-.2,-.3,0],[.2,.3,.5]],{},[0,0,1],0)
        origin,_=resolve(contact,cells,[[-.2,-.3,0],[.2,.3,.5]],{},[1,0,1],0)
        self.assertAlmostEqual(origin[1],1.5)

    def test_compiler_resolves_rotated_parent_and_measurement_rejects_float(self):
        wall=SceneObject(id='host',semantic='An architectural wall',size=(4,.2,3),position=(1,1,0),rotation_deg=37,
            collision_role='shell',parts=[Part(id='wall',primitive='box',size=(4,.2,3),center=(0,0,1.5))])
        fixture=SceneObject(id='fixture',semantic='A mounted light',size=(.4,.6,.5),parent='host',position=(.6,9,1.2),
            surface_contact=SurfaceContact(parent_part='wall',parent_face='y_min',child_part='plate',child_face='y_max'),
            parts=[Part(id='plate',primitive='box',size=(.4,.03,.5),center=(0,.285,.25))])
        plan=GeneralPlan(scene_id='mount_test',description='Mounting with rotated architecture',extent=(10,10,4),objects=[wall,fixture])
        _,bounds,receipts=compile_general(plan)
        expected=np.array([1,1,0])+rotation(37)@np.array([.6,-.4,1.2])
        np.testing.assert_allclose(receipts['fixture']['world_origin'],expected,atol=1e-8)
        poses=_planned_envelopes_and_poses(plan)[1]
        np.testing.assert_allclose(poses['fixture'][1],expected,atol=1e-8)
        measured={'host':{'wall':[[-2,-.1,0],[2,.1,3]]}}
        contacts={'fixture':{'method':'evaluated_blender_mesh_contact_vertices','contact_side':'y_max','contact_part':'plate',
            'local_bounds':[[-.2,.3,0],[.2,.3,.5]]}}
        findings,_=audit_measured(plan,contacts,receipts,measured)
        self.assertFalse(findings,findings)
        displaced=copy.deepcopy(receipts);displaced['fixture']['world_origin']=(expected+rotation(37)@np.array([0,-.2,0])).tolist()
        findings,_=audit_measured(plan,contacts,displaced,measured)
        self.assertTrue(any('mounting gap' in f['evidence'] for f in findings))
        self.assertTrue(audit_measured(plan,{},receipts,measured)[0])


if __name__=='__main__':unittest.main()
