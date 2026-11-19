"""Regression coverage for reusable scene knowledge reaching the right agents."""
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from scene_experience import planning_experience_prompt, spatial_experience_prompt, visual_experience_prompt
from scene_design import DESIGN_PROMPT, SceneDesign, normalize_facing_relationships
from staged_planner import _functional_bindings_prompt, ACCESS_FACES_PROMPT, LAYOUT, SPATIAL
from general_planner import _INSTRUCTIONS


class SceneExperienceTests(unittest.TestCase):
    def test_tatami_student_room_gets_relevant_planning_priors(self):
        prompt = planning_experience_prompt('高中生卧室，榻榻米，书桌和窗户')
        self.assertIn('Tatami sleep/rest assembly', prompt)
        self.assertIn('muted rush/straw weave', prompt)
        self.assertIn('Student bedroom composition', prompt)
        self.assertIn('Lighting fixtures', prompt)
        self.assertIn('exposed woven surface', prompt)
        self.assertIn('desk/task lamp', prompt)
        self.assertIn('Relevant reusable scene experience', prompt)
        self.assertIn('never create a facing relation from a door leaf', prompt)
        self.assertIn('architectural-envelope group, outside furniture activity groups',prompt)
        self.assertIn('chair owns the floor approach and pullback',prompt)
        self.assertIn('access_required=false',prompt)
        self.assertIn('facing relation encodes orientation only',prompt)
        self.assertIn('minimum_m=0',prompt)
        self.assertIn('finished interior, not a furniture checklist',prompt)
        self.assertIn('do not add a laundry basket, plant, extra chair',prompt)
        self.assertIn('Describe the wall, floor, ceiling, window joinery',prompt)

    def test_component_recipe_only_gets_its_matching_prior(self):
        brief = '高中生卧室，榻榻米，书桌和窗户'
        tatami_recipe = planning_experience_prompt(brief, 'tatami_platform')
        lamp_recipe = planning_experience_prompt(brief, 'desk_lamp')
        window_recipe = planning_experience_prompt(brief, 'north_window')
        self.assertIn('Tatami sleep/rest assembly', tatami_recipe)
        self.assertNotIn('Lighting fixtures', tatami_recipe)
        self.assertNotIn('Window assembly', tatami_recipe)
        self.assertIn('Lighting fixtures', lamp_recipe)
        self.assertNotIn('Tatami sleep/rest assembly', lamp_recipe)
        self.assertNotIn('Student bedroom composition', lamp_recipe)
        self.assertIn('Window assembly', window_recipe)
        self.assertNotIn('Tatami sleep/rest assembly', window_recipe)

    def test_high_frequency_furniture_repairs_are_prevented_in_component_priors(self):
        brief='High school student bedroom with tatami, desk and window'
        desk=planning_experience_prompt(brief,'study_desk')
        tatami=planning_experience_prompt(brief,'tatami_assembly')
        shade=planning_experience_prompt(brief,'window_shade')
        self.assertIn('support=null',desk)
        self.assertIn('mount=bottom',desk)
        self.assertIn('local center.z must be 0',desk)
        self.assertIn('Never encode an underside fastener as supported_by',desk)
        self.assertIn('butt or miter joints',tatami)
        self.assertIn('hanging below it',shade)
        self.assertIn('do not overlap the tube and panel',shade)
        self.assertNotIn('Desk drawer construction',planning_experience_prompt(brief,'tatami_assembly'))

    def test_component_experience_is_prepended_ahead_of_generic_recipe_rules(self):
        from staged_planner import RECIPE, _component_recipe_instructions
        instructions=_component_recipe_instructions('High school bedroom with a desk and window','study_desk')
        self.assertLess(instructions.index('Desk drawer construction'),instructions.index('Return JSON with parts'))
        self.assertLess(instructions.index('Desk drawer construction'),instructions.index('Primitives:'))
        self.assertTrue(instructions.endswith(RECIPE))

    def test_unrelated_briefs_get_no_room_template(self):
        self.assertEqual(planning_experience_prompt('An outdoor stone fountain'), '')
        self.assertEqual(spatial_experience_prompt('An outdoor stone fountain'), '')
        self.assertEqual(visual_experience_prompt('An outdoor stone fountain'), '')

    def test_room_layout_priors_reach_the_spatial_agent_without_numeric_templates(self):
        prompt = spatial_experience_prompt('高中生卧室，榻榻米、书桌和窗户')
        self.assertIn('approachable edge facing shared floor', prompt)
        self.assertIn('task lamp inside the desk group', prompt)
        self.assertIn('orient its front toward the desk', prompt)
        self.assertIn('owner-local back (+Y)',prompt)
        self.assertIn('do not reserve a second desk-front strip',prompt)
        self.assertIn('one connected shared entrance aisle with short branches',prompt)
        self.assertIn('Do not reserve a separate full-length door-to-furniture corridor',prompt)
        self.assertIn('non-overlapping footprints', prompt)
        self.assertIn('edge gap = center distance minus both projected half-widths', prompt)
        self.assertIn('refresh affected routes', prompt)
        self.assertIn('Do not add a hard desk-to-tatami or chair-to-tatami distance', prompt)
        self.assertIn('calm wall surface and a distinct floor material',prompt)
        self.assertIn('qualitative result of perimeter placement', prompt)
        self.assertIn('one connected shared entrance aisle with short branches',prompt)
        self.assertIn('Do not reserve a separate full-length door-to-furniture corridor',prompt)
        self.assertIn('chair-to-tatami minimum distances', prompt)
        self.assertIn('Put a shell-fitted, floor-reaching door leaf at WORLD bottom Z=0', prompt)
        self.assertIn('zero minus the shell world-origin Z', prompt)
        self.assertIn('minimum_m is normally zero unless the user explicitly requires a gap', prompt)
        self.assertIn('recompute its CENTER from that fixed pivot', prompt)
        self.assertIn('moves the hinge and is not a valid swing', prompt)
        self.assertIn('full rotated bounds clear the aperture', prompt)
        self.assertIn('choose the jamb pivot first', prompt)
        self.assertIn('recompute its CENTER from that fixed pivot', prompt)
        self.assertIn('moves the hinge and is not a valid swing', prompt)
        self.assertIn('Never encode a door leaf as facing a window or furniture', prompt)
        self.assertIn('Stable conditional system priors', prompt)
        self.assertIn('not extra objects or hard numeric thresholds', prompt)
        self.assertNotIn('0.8m', prompt)

    def test_student_bedroom_gets_study_layout_priors_without_keyword_expansion(self):
        prompt = spatial_experience_prompt('高中生的卧室，需要榻榻米和窗户')
        self.assertIn('task lamp inside the desk group', prompt)
        self.assertIn('solve the desk, chair and task lamp as one study group', prompt)

    def test_universal_seating_access_and_local_direction_priors_are_in_agent_stages(self):
        self.assertIn('chair own the single floor approach/pullback area',
                      _functional_bindings_prompt(['desk','chair']))
        self.assertIn('entry and pullback are behind the chair',DESIGN_PROMPT)
        self.assertIn('B-local left [-1,0] at yaw 90 degrees becomes world [0,-1]',LAYOUT)
        self.assertIn('B-local left [-1,0] points along world [0,-1]',SPATIAL)
        self.assertIn('facing is orientation only',DESIGN_PROMPT)
        self.assertIn('never translate that root to (-W/2,-D/2)',LAYOUT)

    def test_facing_normalization_removes_only_inferred_gap_from_orientation_relation(self):
        design=SceneDesign.model_validate({
            'room_identity':'student room','users_and_activities':'study and rest',
            'composition':{'whole_space':'ordered room','groups':[{
                'id':'study','role':'study station','choice_ids':['chair','desk'],
                'anchor_choice_id':'desk','organization':'chair at desk','density_and_rhythm':'compact',
                'relationships':[
                    {'id':'chair_faces_desk','a_choice_id':'chair','b_choice_id':'desk',
                     'kind':'facing','minimum_m':.35,'maximum_m':.85,'tolerance_deg':10,
                     'reason':'The chair faces the work surface.'},
                    {'id':'desk_clearance','a_choice_id':'chair','b_choice_id':'desk',
                     'kind':'distance','minimum_m':.1,'maximum_m':.8,'tolerance_deg':30,
                     'reason':'A requested reach interval.'}]}],
                'links':[],'open_spaces':[],'views':[{'id':'study_view','experience':'study','group_ids':['study']}],
                'editing_priorities':'preserve editable furniture'},
            'spatial_composition':'perimeter groups with open floor','focal_hierarchy':'tatami then desk',
            'material_palette':['wood'],'lighting_atmosphere':'warm task light',
            'choices':[{'id':'chair','layer':'furniture','activity':'seated study','selection':'study chair',
                        'quality_reason':'supports study'},
                       {'id':'desk','layer':'furniture','activity':'homework','selection':'study desk',
                        'quality_reason':'supports homework'}]})
        normalized,changes=normalize_facing_relationships(design)
        facing,distance=normalized.composition.groups[0].relationships
        self.assertEqual((facing.minimum_m,facing.maximum_m),(0,None))
        self.assertEqual((distance.minimum_m,distance.maximum_m),(.1,.8))
        self.assertEqual(len(changes),1)
        explicit=design.model_copy(deep=True)
        explicit.composition.groups[0].relationships[0].reason='User explicitly requires a 0.4m gap.'
        preserved,explicit_changes=normalize_facing_relationships(explicit)
        kept=preserved.composition.groups[0].relationships[0]
        self.assertEqual((kept.minimum_m,kept.maximum_m),(.35,.85))
        self.assertEqual(explicit_changes,[])

    def test_window_opening_and_support_experience_reaches_room_planning(self):
        prompt = spatial_experience_prompt('高中生卧室，榻榻米和窗户')
        self.assertIn('not at the room origin', prompt)
        self.assertIn('wall-normal coordinate', prompt)
        self.assertIn('parent-local transform', prompt)
        self.assertIn('do not encode the window and desk as a typed spatial relation', prompt)
        self.assertNotIn('place the desk relative to the actual wall opening', prompt)
        self.assertIn('impossible support group', prompt)

    def test_all_planners_share_unambiguous_scene_centered_room_and_opening_coordinates(self):
        for prompt in (SPATIAL,_INSTRUCTIONS):
            self.assertIn('[-W/2,+W/2]',prompt)
        self.assertIn('legal offset interval',SPATIAL)
        self.assertIn('offset in [-T/2+q/2,+T/2-q/2]',_INSTRUCTIONS)
        self.assertIn('not a distance from a wall corner',SPATIAL)
        self.assertIn('not distances from a corner',_INSTRUCTIONS)
        self.assertIn('SIGNED world y coordinate',SPATIAL)
        self.assertIn('signed world coordinates',_INSTRUCTIONS)
        self.assertIn('For extent=(4.2,4.2,2.8), all room XY bounds are [-2.1,+2.1]',SPATIAL)
        self.assertIn('offset=2.5 is outside the wall',SPATIAL)
        self.assertIn('Do not rely on later clipping',SPATIAL)

    def test_access_face_prompt_gets_closed_frozen_owner_inventory(self):
        self.assertIn('closed set exactly equal to expected_access_owner_ids',ACCESS_FACES_PROMPT)
        self.assertIn('never add a table/surface marked access_required=false',ACCESS_FACES_PROMPT)

    def test_lighting_experience_separates_hanging_appearance_from_mount_contract(self):
        planning = planning_experience_prompt('Bedroom with a tatami and pendant lamp')
        spatial = spatial_experience_prompt('Bedroom with a tatami and pendant lamp')
        self.assertIn('mount=origin', planning)
        self.assertIn('mount=bottom means support beneath the host bottom face', planning)
        self.assertIn('mount=origin with the actual canopy face', spatial)

    def test_visual_priors_are_evidence_cues_without_numeric_thresholds(self):
        prompt = visual_experience_prompt('榻榻米卧室配台灯')
        self.assertIn('woven mat surface, distinct bound rectangular modules', prompt)
        self.assertIn('separate bedding', prompt)
        self.assertIn('Images do not prove physical attachment', prompt)
        self.assertIn('do not estimate a coverage ratio', prompt)
        student_room = visual_experience_prompt('高中生卧室，榻榻米、书桌和窗户')
        self.assertIn('visible fixture reads as the intended', student_room)
        self.assertIn('floating at room center', student_room)
        self.assertIn('Do not reject a scene over an ordinary small clearance',student_room)


if __name__ == '__main__':
    unittest.main()
