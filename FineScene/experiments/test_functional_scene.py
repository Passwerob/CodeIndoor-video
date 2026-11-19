"""Functional orientation and an occupied-floor datum inside an actual room."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from asset_physics import PartForm, audit_part_form, rotation_matrix
from general_scene import GeneralPlan, compile_general, judge_general_geometry


class FunctionalSceneTests(unittest.TestCase):
    def test_contact_pass_cannot_hide_a_panel_on_the_wrong_axis(self):
        data=dict(scene_id='panel_test',description='An attached functional panel',extent=[2,2,2],objects=[
            dict(id='frame',semantic='Supported panel',size=[1,.3,.12],parts=[
                dict(id='base',primitive='box',size=[1,.3,.1],center=[0,0,.05]),
                dict(id='face',primitive='box',size=[.8,.08,.02],center=[0,0,.11])],
                assembly=dict(roots=['base'],relations=[dict(kind='attached_to',part='face',target='base',reason='fastened')]))])
        compile_general(GeneralPlan.model_validate(data))  # Original contact-only gate passes.
        data['objects'][0]['parts'][1]['form']=dict(kind='panel',axis='y')
        with self.assertRaisesRegex(ValueError,'part_form'):compile_general(GeneralPlan.model_validate(data))
        data['objects'][0]['parts'][1].update(size=[.8,.02,.08],center=[0,0,.14])
        data['objects'][0]['size']=[1,.3,.18]
        compile_general(GeneralPlan.model_validate(data))

    def test_form_uses_asset_axes_after_rotation(self):
        from types import SimpleNamespace
        panel=SimpleNamespace(form=PartForm(kind='panel',axis='y'))
        record=dict(size=[.02,.8,.08],rotation=rotation_matrix((0,0,90)).tolist())
        self.assertIsNone(audit_part_form(panel,record))
        record['rotation']=rotation_matrix((0,0,45)).tolist()
        self.assertIn('not aligned',audit_part_form(panel,record))

    def test_floor_below_datum_is_allowed_only_for_architecture(self):
        data=dict(scene_id='floor_test',description='Room above the occupied floor',extent=[4,4,2.8],objects=[
            dict(id='room',semantic='Floor and wall',collision_role='shell',access_required=False,
                 size=[4,4,2.9],position=[0,0,-.1],grounded=True,parts=[
                     dict(id='floor',primitive='box',size=[4,4,.1],center=[0,0,.05]),
                     dict(id='wall',primitive='box',size=[4,.1,2.8],center=[0,1.95,1.5])],
                 assembly=dict(roots=['floor'],relations=[dict(kind='attached_to',part='wall',target='floor',reason='wall on floor')]))])
        plan=GeneralPlan.model_validate(data)
        _,bounds,receipts=compile_general(plan)
        self.assertEqual(judge_general_geometry(plan,bounds,receipts)['status'],'passed')
        plan.objects[0].collision_role='solid'
        self.assertEqual(judge_general_geometry(plan,bounds,receipts)['status'],'needs_revision')

    def test_floor_covering_role_cannot_hide_a_thick_platform(self):
        data=dict(scene_id='platform_test',description='Raised display platform',extent=[4,4,3],objects=[
            dict(id='platform',semantic='Raised display platform',size=[1,1,.2],position=[0,0,0],
                 grounded=True,collision_role='floor_covering',source={'mode':'retrieve'})])
        plan=GeneralPlan.model_validate(data)
        report=judge_general_geometry(plan,{'platform':[[ -.5,-.5,0],[.5,.5,.2]]})
        self.assertTrue(any('exceeds 0.08m' in finding['evidence'] for finding in report['findings']))

    def test_frozen_form_is_lowered_and_cannot_be_weakened(self):
        from types import SimpleNamespace
        from staged_planner import Recipe, InternalPartIntent, _lower_frozen_assembly_relations
        obj=SimpleNamespace(internal_parts=[InternalPartIntent(id='panel',semantic='Vertical board',
            rigidity='rigid',role='structure',form={'kind':'panel','axis':'y','minimum_ratio':3})],
            internal_clearances=[])
        recipe=Recipe.model_validate({'parts':[dict(id='panel',primitive='box',size=[1,.1,.4],center=[0,0,.2],
            rigidity='parametric',form={'kind':'panel','axis':'z','minimum_ratio':1.1})]})
        lowered=_lower_frozen_assembly_relations(obj,recipe)
        self.assertEqual(lowered.parts[0].rigidity,'rigid')
        self.assertEqual(lowered.parts[0].form,obj.internal_parts[0].form)
        self.assertEqual(recipe.parts[0].rigidity,'parametric')
        self.assertEqual(recipe.parts[0].form.axis,'z')  # Input remains untouched.

    def test_zone_capacity_catches_impossible_edits_before_position_repairs(self):
        from types import SimpleNamespace
        from planning_diagnostics import PlanningValidationError
        from scene_spatial import validate_zone_capacity
        obj=SimpleNamespace(id='storage',dimensions=['width',.32,1.8],parent=None,collision_role='solid')
        zone=SimpleNamespace(id='storage_zone',size=[.32,.9],component_ids=['storage'])
        contract=SimpleNamespace(zones=[zone])
        with self.assertRaises(PlanningValidationError) as caught:
            validate_zone_capacity(contract,[obj],{'width':.9},[{'parameter':'width','delta':.1}])
        issue=caught.exception.measurements['issues'][0]
        self.assertEqual(issue['code'],'zone_capacity')
        self.assertEqual(issue['scenario'],'width')
        self.assertEqual(issue['paths'],['/zones/0/size'])
        zone.size=[.4,1.1]
        self.assertIs(validate_zone_capacity(contract,[obj],{'width':.9},[{'parameter':'width','delta':.1}]),contract)

    def test_millimetre_frame_drift_is_rejected_before_layout(self):
        from staged_planner import Decomposition, Recipe, validate_recipe
        from experiments.test_staged_planner import outline_data, recipe_data
        outline=Decomposition.model_validate(outline_data())
        recipe=recipe_data();recipe['parts'][0]['center'][1]=.005
        with self.assertRaises(ValueError) as caught:
            validate_recipe(outline,outline.objects[0],Recipe.model_validate(recipe))
        self.assertIn('component_bounds',{i['code'] for i in caught.exception.measurements['issues']})

    def test_shell_opening_obstruction_targets_internal_geometry(self):
        import numpy as np
        from types import SimpleNamespace
        from scene_spatial import audit_spatial,SpatialContract
        shell=SimpleNamespace(id='enclosure',collision_role='shell',access_required=False)
        contract=SpatialContract.model_validate(dict(zones=[dict(id='room',center=[0,0],size=[4,4],
            purpose='architectural boundary',component_ids=['enclosure'])],access_areas=[],routes=[],relations=[],
            openings=[dict(id='window',kind='window',wall='y_max',offset=0,width=1.4,
                           sill_height=.8,height=1.2,approach_depth=.4,purpose='actual clear opening')]))
        report=audit_spatial(contract,[shell],[4,4,2.8],{'enclosure':[[-2,-2,0],[2,2,2.8]]},
            {'enclosure':(0,np.zeros(3))},shell_parts={'enclosure':{'trim':[[-.7,1.85,.8],[-.65,1.95,2.0]]}})
        finding=report['findings'][0]
        self.assertEqual(finding['repair_target'],'geometry')
        self.assertEqual(finding['component_ids'],['enclosure'])
        self.assertEqual(finding['part_ids'],['trim'])


if __name__=='__main__':unittest.main()
