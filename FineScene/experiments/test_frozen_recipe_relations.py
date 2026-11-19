"""Frozen intent is lowered without spending model repairs or relaxing geometry."""
import copy
import unittest

import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from test_asset_physics import shelf_plan
from staged_planner import Decomposition, Recipe, RecipeValidationError, validate_recipe


def fixture():
    plan=shelf_plan()
    obj=plan.objects[0]
    outline=Decomposition.model_validate(dict(
        scene_id='frozen_relations',description='Editable storage',style='functional',
        quality_target='prototype',extent=plan.extent,parameters=plan.parameters,
        objects=[dict(id=obj.id,semantic=obj.semantic,category='storage',dimensions=obj.size,
            rigidity='parametric',decomposition_reason='One editable structural asset with rigid contents.',
            required_features=['Supported contents and usable headroom'],material_requirements=['matte'],
            visual_importance=3,acquisition=dict(strategy='parametric',reason='Regular editable physical members.'),
            internal_parts=[dict(id=p.id,semantic=p.id,rigidity=p.rigidity,
                role='content' if p.parent else 'structure',support=p.parent) for p in obj.parts],
            internal_clearances=[dict(a=r.part,b=r.target,minimum_m=r.minimum,reason=r.reason)
                                 for r in obj.assembly.relations if r.kind=='clearance'])],
        zones=[dict(name='storage',purpose='Hold items',component_ids=[obj.id])],
        requirements=[dict(requirement='Usable storage',component_ids=[obj.id],verification='Measured geometry')],
        edit_checks=[dict(parameter='gap',delta=.1,intent='Raise supports without resizing contents')]))
    recipe=Recipe.model_validate(dict(parts=[p.model_dump() for p in obj.parts],assembly=obj.assembly.model_dump()))
    return outline,recipe


class FrozenRecipeRelationTests(unittest.TestCase):
    def test_omitted_relations_compile_together_preserve_geometry_and_input(self):
        outline,raw=fixture()
        raw.assembly.relations=[r for r in raw.assembly.relations if r.kind=='attached_to']
        before=copy.deepcopy(raw.model_dump())
        result=validate_recipe(outline,outline.objects[0],raw)
        self.assertEqual(raw.model_dump(),before)
        self.assertEqual([p.model_dump() for p in result.parts],before['parts'])
        self.assertEqual(sum(r.kind=='clearance' for r in result.assembly.relations),2)
        self.assertEqual(sum(r.kind=='supported_by' for r in result.assembly.relations),2)
        again=validate_recipe(outline,outline.objects[0],result)
        self.assertEqual(again.model_dump(),result.model_dump())

    def test_weakened_clearance_is_restored_and_real_violation_still_rejected(self):
        outline,raw=fixture()
        for r in raw.assembly.relations:
            if r.kind=='clearance':r.minimum=0
        result=validate_recipe(outline,outline.objects[0],raw)
        self.assertTrue(all(r.minimum==.02 for r in result.assembly.relations if r.kind=='clearance'))
        outline.parameters['gap']=.265  # lower book has only 5 mm of headroom
        with self.assertRaises(RecipeValidationError) as caught:
            validate_recipe(outline,outline.objects[0],raw)
        self.assertTrue(any(f['code']=='clearance' for f in caught.exception.measurements['assembly_findings']))

    def test_conflicting_support_cannot_replace_frozen_support(self):
        outline,raw=fixture()
        next(r for r in raw.assembly.relations if r.part=='book_high' and r.kind=='supported_by').target='base'
        with self.assertRaisesRegex(ValueError,'Frozen support conflict'):
            validate_recipe(outline,outline.objects[0],raw)

    def test_missing_assembly_recovers_only_frozen_interfaces_and_still_checks_geometry(self):
        outline,raw=fixture();raw.assembly=None
        with self.assertRaises(ValueError) as caught:
            validate_recipe(outline,outline.objects[0],raw)
        self.assertNotIn('missing_support',str(caught.exception))
        self.assertIn('assembly_findings',caught.exception.measurements)

    def test_frozen_bottom_attachment_sets_parent_mount_and_relation(self):
        from types import SimpleNamespace
        from staged_planner import InternalPartIntent, _lower_frozen_assembly_relations
        intent=InternalPartIntent(id='drawer',semantic='Under-desk drawer',rigidity='rigid',role='detail',
            attached_to='desktop',mount='bottom')
        obj=SimpleNamespace(id='desk',internal_parts=[intent,
            InternalPartIntent(id='desktop',semantic='Desktop',rigidity='parametric',role='structure')],
            internal_clearances=[])
        recipe=Recipe.model_validate({'parts':[
            {'id':'drawer','primitive':'box','size':[.4,.4,.1],'center':[0,0,.665],
             'parent':None,'mount':'origin','rigidity':'parametric'},
            {'id':'desktop','primitive':'box','size':[1,.6,.04],'center':[0,0,.7],
             'rigidity':'parametric'}],
            'assembly':{'roots':['drawer','desktop'],'relations':[]}})
        lowered=_lower_frozen_assembly_relations(obj,recipe)
        drawer=next(part for part in lowered.parts if part.id=='drawer')
        self.assertEqual((drawer.parent,drawer.mount,drawer.rigidity),('desktop','bottom','rigid'))
        self.assertEqual(drawer.center,(0,0,0))
        self.assertEqual((recipe.parts[0].parent,recipe.parts[0].mount,recipe.parts[0].center),
                         (None,'origin',(0,0,.665)))
        self.assertEqual(lowered.assembly.roots,['desktop'])
        self.assertTrue(any(r.kind=='attached_to' and r.part=='drawer' and r.target=='desktop'
                            for r in lowered.assembly.relations))

    def test_frozen_attachment_rejects_a_model_authored_support_relation(self):
        from types import SimpleNamespace
        from staged_planner import InternalPartIntent, _lower_frozen_assembly_relations
        obj=SimpleNamespace(id='desk',internal_parts=[
            InternalPartIntent(id='drawer',semantic='Under-desk drawer',rigidity='rigid',role='detail',
                attached_to='desktop',mount='bottom'),
            InternalPartIntent(id='desktop',semantic='Desktop',rigidity='parametric',role='structure')
        ],internal_clearances=[])
        recipe=Recipe.model_validate({'parts':[
            {'id':'drawer','primitive':'box','size':[.4,.4,.1],'center':[0,0,0]},
            {'id':'desktop','primitive':'box','size':[1,.6,.04],'center':[0,0,.7]}],
            'assembly':{'roots':['desktop'],'relations':[
                {'kind':'supported_by','part':'drawer','target':'desktop','reason':'wrong top support'}]}})
        with self.assertRaisesRegex(ValueError,'Frozen attachment conflict'):
            _lower_frozen_assembly_relations(obj,recipe)

    def test_all_collision_findings_survive_the_stage_error_text_limit(self):
        import asyncio
        import json
        import tempfile
        from staged_planner import StagedScenePlanner, PlanningRepairLimit
        outline,raw=fixture()
        for index in range(16):
            raw.parts.append(raw.parts[-1].model_copy(update={'id':f'extra_{index:02d}'},deep=True))
        observed=[]
        async def complete(stage,instructions,payload):
            if stage.endswith('_0'):return raw.model_dump(mode='json')
            observed.append(payload)
            return {'reason':'No correction available','replacements':[
                {'path':'/parts','value':payload['candidate']['parts']}]}
        with tempfile.TemporaryDirectory() as directory:
            planner=StagedScenePlanner('unused',directory,'test',complete=complete,max_repairs=1)
            with self.assertRaises(PlanningRepairLimit):
                asyncio.run(planner.stage('recipe_storage',Recipe,'test',{},
                    lambda r:validate_recipe(outline,outline.objects[0],r),lambda _:['/parts']))
        self.assertEqual(len(observed),1)
        self.assertGreater(len(observed[0]['errors']),6000)
        findings=observed[0]['measurement_evidence']['assembly_findings']
        self.assertGreater(len(json.dumps(findings)),6000)
        self.assertTrue(any(f['part_ids']==['extra_14','extra_15'] and f['code']=='intersection' for f in findings))


if __name__=='__main__':unittest.main()
