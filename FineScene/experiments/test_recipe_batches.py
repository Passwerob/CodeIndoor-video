"""Category-independent assembly batches preserve verified geometry on repair."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
sys.path.insert(0,str(Path(__file__).resolve().parent))
from staged_planner import StagedScenePlanner


class RecipeBatchTests(unittest.TestCase):
    def test_structure_stage_cannot_duplicate_content_under_other_ids(self):
        from staged_planner import Recipe,InternalPartIntent,validate_structural_batch_ids
        structure=[InternalPartIntent(id='shelf',semantic='supporting shelf',rigidity='parametric',role='structure')]
        contents=[InternalPartIntent(id='book_01',semantic='required rigid book',rigidity='rigid',role='content',support='shelf')]
        recipe=Recipe.model_validate({'parts':[
            dict(id='shelf',primitive='box',size=[1,.3,.03],center=[0,0,.1]),
            dict(id='b01',primitive='book',size=[.08,.2,.25],center=[-.2,0,.25],rigidity='rigid',
                 book={'orientation':'upright','palette_index':1,'binding':'classic'})],
            'assembly':{'roots':['shelf'],'relations':[]}})
        with self.assertRaisesRegex(ValueError,'unexpected=.*b01.*Do not add duplicate contents'):
            validate_structural_batch_ids(recipe,structure,contents)

    def test_verified_support_lowering_preserves_geometry_and_follows_edit(self):
        from test_asset_physics import shelf_plan
        from staged_planner import Decomposition,Recipe,validate_recipe
        from general_scene import compile_general,edit_general,Part
        original=shelf_plan();obj=original.objects[0]
        _,_,receipts=compile_general(original)
        records=receipts[obj.id]['internal_geometry']['parts']
        parts=[p.model_dump() for p in obj.parts]
        for p in parts[-2:]:p.update(parent=None,mount='origin',center=records[p['id']]['center'])
        intents=[dict(id=p.id,semantic=p.id,rigidity=p.rigidity,role='content' if p.id.startswith('book') else 'structure',
                      support=p.parent) for p in obj.parts]
        outline=Decomposition.model_validate(dict(scene_id='support_test',description='generic supported contents',style='technical',
            quality_target='prototype',extent=original.extent,parameters=original.parameters,
            objects=[dict(id=obj.id,semantic=obj.semantic,category='storage',dimensions=obj.size,rigidity='parametric',
                decomposition_reason='One asset with a moving internal support.',required_features=['two supported contents'],material_requirements=['matte'],
                internal_parts=intents,visual_importance=3,acquisition=dict(strategy='parametric',reason='Measured regular structural elements.'))],
            zones=[dict(name='storage',purpose='hold contents',component_ids=[obj.id])],
            requirements=[dict(requirement='supported contents',component_ids=[obj.id],verification='geometry and edits')],
            edit_checks=[dict(parameter='gap',delta=.1,intent='raise upper support')]))
        raw=Recipe.model_validate({'parts':parts,'assembly':obj.assembly.model_dump()})
        lowered=validate_recipe(outline,outline.objects[0],raw)
        self.assertIsNone(raw.parts[-1].parent)
        self.assertEqual(lowered.parts[-1].parent,'shelf')
        obj.parts=[Part.model_validate(p.model_dump()) for p in lowered.parts]
        _,_,after=compile_general(edit_general(original,'gap',.1))
        self.assertAlmostEqual(after[obj.id]['internal_geometry']['parts']['book_high']['center'][2]-records['book_high']['center'][2],.1)
        raw.parts[-1].center=(.24,0.,records['book_high']['center'][2]+.02)
        with self.assertRaises(ValueError) as caught:validate_recipe(outline,outline.objects[0],raw)
        self.assertTrue(any(f['code']=='support_gap' for f in caught.exception.measurements['assembly_findings']))

    def test_request_contract_rejects_boundary_and_inventory_loss(self):
        from staged_planner import RequestContract,Decomposition,validate_request_contract
        from test_staged_planner import outline_data
        data=outline_data();data['objects'][1]['acquisition']={'strategy':'parametric','reason':'Editable geometric content'}
        outline=Decomposition.model_validate(data)
        with self.assertRaisesRegex(ValueError,'ONE editable asset'):
            validate_request_contract(outline,RequestContract(asset_boundary='single_asset',quality_target='prototype'))
        contract=RequestContract(asset_boundary='scene',quality_target='prototype',contents=[{'tag':'samples','description':'test samples','minimum_instances':2}])
        outline.objects[1].requirement_tags=['samples']
        with self.assertRaisesRegex(ValueError,'individually identified'):
            validate_request_contract(outline,contract)

    def test_same_dependency_pipeline_for_different_categories(self):
        for category in ('machine_tool_tray','pantry_organizer'):
            calls=[]
            intents=[dict(id='base',semantic='support surface',rigidity='parametric',role='structure')]+[
                dict(id=f'load_{i}',semantic=f'independent {category} item {i}',rigidity='rigid',role='content',support='base') for i in range(9)]
            outline=dict(scene_id='assembly',description=category,style='functional',quality_target='prototype',extent=[3,3,3],parameters={'width':1.5},
                objects=[dict(id='unit',semantic=category,category=category,dimensions=['width',1,.2],rigidity='parametric',
                    decomposition_reason='Single asset contains editable base and supported rigid contents.',required_features=['nine items'],
                    material_requirements=['matte'],internal_parts=intents,visual_importance=3,
                    acquisition=dict(strategy='parametric',reason='Regular independent solids with explicit contacts.'))],
                zones=[dict(name='storage',purpose='hold contents',component_ids=['unit'])],
                requirements=[dict(requirement='nine supported items',component_ids=['unit'],verification='contact audit')])
            async def complete(stage,instructions,payload):
                calls.append(stage)
                if stage=='request_contract_0':return {'asset_boundary':'single_asset','quality_target':'prototype'}
                if stage=='decomposition_0':return outline
                if stage=='recipe_unit_structure_0':
                    return {'parts':[dict(id='base',primitive='box',size=['w','d',.1],center=[0,0,.05])],
                            'assembly':{'roots':['base'],'relations':[]}}
                if stage.startswith('recipe_unit_contents_'):
                    self.assertIn('existing_interfaces',payload['context'])
                    parts=[];relations=[]
                    for item in payload['context']['assigned_parts']:
                        i=int(item['id'].split('_')[-1])
                        parts.append(dict(id=item['id'],primitive='box',size=[.1,.1,.1],center=[-.6+.15*i,0,0],
                            parent='base',mount='top',rigidity='rigid'))
                        relations.append(dict(kind='supported_by',part=item['id'],target='base',reason='rests on base'))
                    return {'parts':parts,'assembly':{'roots':['base'],'relations':relations}}
                if stage=='layout_0':return {'placements':[dict(id='unit',position=[0,0,0])]}
                raise AssertionError(stage)
            with tempfile.TemporaryDirectory() as tmp:
                result=StagedScenePlanner('unused',tmp,'test',complete=complete).plan(category)
                async def no_calls(stage,instructions,payload):
                    raise AssertionError('Matching accepted batches must revalidate without model calls: '+stage)
                resumed=StagedScenePlanner('unused',str(Path(tmp)/'resume'),'resumed',
                    complete=no_calls,resume_runtime=tmp).plan(category)
                self.assertEqual(result.model_dump(),resumed.model_dump())
                checkpoint=Path(tmp)/'recipe_unit_contents_01_checkpoint.json'
                original_checkpoint=checkpoint.read_text(encoding='utf-8')
                for field in ('previous_geometry','previous_relation'):
                    corrupted=json.loads(original_checkpoint)
                    if field=='previous_geometry':corrupted['candidate']['parts'][0]['center'][2]=.06
                    else:corrupted['candidate']['assembly']['relations'][0]['reason']='changed predecessor'
                    checkpoint.write_text(json.dumps(corrupted),encoding='utf-8')
                    with self.assertRaisesRegex(AssertionError,'Matching accepted batches'):
                        StagedScenePlanner('unused',str(Path(tmp)/field),field,
                            complete=no_calls,resume_runtime=tmp).plan(category)
                    checkpoint.write_text(original_checkpoint,encoding='utf-8')
            self.assertEqual(len(result.components[0].parts),10)
            self.assertEqual(calls,['request_contract_0','decomposition_0','recipe_unit_structure_0','recipe_unit_contents_00_0','recipe_unit_contents_01_0','layout_0'])


if __name__=='__main__':unittest.main()
