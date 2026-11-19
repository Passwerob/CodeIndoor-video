"""Check design commitments survive lowering and asset evidence stays scoped."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
sys.path.insert(0,str(Path(__file__).resolve().parent))
from scene_design import SceneDesign,validate_design_coverage,scene_design_revision_required
from staged_planner import Decomposition,Acquisition
from test_staged_planner import outline_data
from general_vision import Decision,invalid_image_locators,batch_evidence_request
import material_catalog


class DesignTests(unittest.TestCase):
    def test_concise_chinese_design_text_and_omissions_string_do_not_need_model_repair(self):
        from test_scene_composition import design_data
        raw=design_data()
        raw.update(room_identity='学生卧室',users_and_activities='学习和休息',
                   spatial_composition='榻榻米与书桌分区',focal_hierarchy='榻榻米为主',
                   lighting_atmosphere='柔和顶灯')
        raw['choices'][0].update(activity='休息',selection='榻榻米',quality_reason='适合休息')
        raw['choices'][1].update(activity='陈列',selection='收纳架',quality_reason='收纳书籍')
        raw['composition']['whole_space']='方正寝室，中央留空。'
        raw['composition']['groups'][0].update(role='休息区',organization='榻榻米靠墙。',
            density_and_rhythm='床面简洁。')
        raw['composition']['groups'][0]['relationships'][0]['reason']='架子由底座承托。'
        raw['composition']['open_spaces'][0]['purpose']='中央活动地面。'
        raw['composition']['views'][0]['experience']='入室先见榻榻米。'
        raw['composition']['editing_priorities']='先调整大件。'
        raw['deliberate_omissions']='没有增加独立床，避免重复睡眠功能。'
        design=SceneDesign.model_validate(raw)
        self.assertEqual(design.deliberate_omissions,[raw['deliberate_omissions']])

    def test_design_prompt_scopes_choice_relations_to_their_group(self):
        from scene_design import DESIGN_PROMPT
        self.assertIn('both endpoint choice IDs',DESIGN_PROMPT)
        self.assertIn('Never put a cross-group relationship',DESIGN_PROMPT)
        self.assertIn('Represent a relation between groups with links',DESIGN_PROMPT)
        self.assertIn('entrance door to a window or furnishing',DESIGN_PROMPT)
        self.assertIn('fitted doors/windows/skylights in architectural-envelope groups',DESIGN_PROMPT)
        self.assertIn('the wall-opening contract locates',DESIGN_PROMPT)
        self.assertIn('daylight or arrival intent in composition prose',DESIGN_PROMPT)
        self.assertIn('treat the requested inventory as closed',DESIGN_PROMPT)
        self.assertIn('permission to invent age-based belongings',DESIGN_PROMPT)

    def test_decomposition_prompt_covers_every_retained_scene_design_choice(self):
        from staged_planner import DECOMPOSE
        self.assertIn('Every scene object ID MUST appear in at least one requirements.component_ids entry',DECOMPOSE)
        self.assertIn('Keep design_bindings complete as a separate mapping',DECOMPOSE)
        self.assertIn('ceiling or task light belongs to its relevant lighting/activity requirement',DECOMPOSE)

    def test_layout_failure_keeps_design_but_scene_level_visual_rejection_reopens_it(self):
        self.assertFalse(scene_design_revision_required({
            'planning_failures':[{'stage':'layout','issue_count':72}],
            'detailed_planning_failure':{'stage':'layout','issues':[{'repair_target':'geometry'}]}}))
        self.assertTrue(scene_design_revision_required({'replan_reason':
            'repeated_style_requirement_failure_after_local_render:wall_finish'}))
        self.assertTrue(scene_design_revision_required({'visual':{'decision':{'findings':[{
            'severity':'blocking','repair_target':'decomposition','component_ids':['room_shell'],
            'evidence':'The room does not read as a furnished reception space.'}]}}}))
        self.assertFalse(scene_design_revision_required({'visual':{'decision':{'findings':[{
            'severity':'blocking','repair_target':'layout','component_ids':['chair'],
            'evidence':'The chair blocks an aisle.'}]}}}))

    def test_enclosed_shell_cannot_hide_a_room_resize_outside_numeric_extent(self):
        from staged_planner import RequestContract,validate_request_contract
        from scene_spatial import SceneUseRequirements
        data=outline_data();data['objects']=data['objects'][:1]
        data['objects'][0].update(collision_role='shell',dimensions=['base_width',1,.2])
        data['zones'][0]['component_ids']=['base'];data['requirements'][0]['component_ids']=['base']
        data['extent']=[1.5,1,3]
        data['edit_checks']=[{'parameter':'base_width','delta':.2,'intent':'Unrequested room resize'}]
        outline=Decomposition.model_validate(data,context={'defer_decomposition_semantics':True})
        contract=RequestContract(asset_boundary='scene',quality_target='prototype',
                                 scene_use=SceneUseRequirements(enclosure='enclosed'))
        with self.assertRaisesRegex(ValueError,'single enclosing shell'):validate_request_contract(outline,contract)
        outline.edit_checks=[]
        self.assertIs(validate_request_contract(outline,contract),outline)

    def test_relationship_capacity_and_backend_errors_arrive_in_one_repair(self):
        from staged_planner import StagedScenePlanner
        class EndProbe(RuntimeError):pass
        observed=[]
        choice={'id':'display','layer':'furniture','activity':'display',
                'selection':'Editable support carrying the sculpted ornament',
                'quality_reason':'Keep the support editable and the ornament a separate source.'}
        design=dict(room_identity='An indoor display and viewing space',
            users_and_activities='People approach and view a display',
            spatial_composition='The display has open circulation around it',
            focal_hierarchy='The supported ornament is the visual focus',material_palette=['stone'],
            lighting_atmosphere='Soft ambient illumination',choices=[choice])
        from test_scene_composition import concept_data
        design['composition']=concept_data()
        design['composition']['groups'][0].update(choice_ids=['display'],anchor_choice_id='display')
        design['composition']['groups'][0]['relationships']=[]
        raw=outline_data();raw['quality_target']='realistic';raw['extent']=[1,1,3]
        raw['objects'][0]['internal_parts']=[dict(id='load',semantic='unsupported load',
            rigidity='rigid',role='content',support='absent')]
        raw['design_bindings']={'display':['base','ornament']}
        async def complete(stage,instructions,payload):
            if stage=='request_contract_0':return {'asset_boundary':'scene','quality_target':'realistic'}
            if stage in {'scene_design_0','design_feasibility_0'}:return design
            if stage=='decomposition_0':return raw
            if stage=='decomposition_repair_1':
                observed.extend(payload['diagnostic_report']['issues'])
                raise EndProbe()
            self.fail(stage)
        with tempfile.TemporaryDirectory() as directory,patch('general_assets.local_catalog',return_value=[]), \
                patch('general_assets.planning_source_options',return_value={'candidates':[]}):
            planner=StagedScenePlanner('unused',directory,'probe',complete=complete,allow_generation=False)
            with self.assertRaises(EndProbe):planner.plan('An indoor display')
        self.assertTrue({'internal_support','room_capacity','generation_unavailable'}<=set(i['code'] for i in observed),observed)

    def test_realistic_scene_design_is_refined_before_decomposition(self):
        import asyncio
        from staged_planner import StagedScenePlanner,RequestContract
        design=SceneDesign(room_identity='A refined reading and reception room',
            users_and_activities='Adults sit, talk and read',spatial_composition='Seating forms a group around a low table',
            focal_hierarchy='Main sofa group and book wall',material_palette=['leather and wood'],
            lighting_atmosphere='Warm layered interior light',choices=[{
                'id':'main_seating','layer':'furniture','activity':'conversation',
                'selection':'A refined upholstered sofa with armchairs',
                'quality_reason':'The main social use calls for comfort','source_queries':['antique sofa']}])
        calls=[]
        class EndProbe(RuntimeError):pass
        async def stage(name,schema,instructions,context,validate,allowed=None,**kwargs):
            calls.append((name,instructions))
            if name=='request_contract':return RequestContract(asset_boundary='scene',quality_target='realistic')
            if name in {'scene_design','design_feasibility'}:
                self.assertIn('available_local_assets',context)
                return design
            if name=='decomposition':
                self.assertIn('Tatami sleep/rest assembly',instructions)
                self.assertIn('Lighting fixtures',instructions)
                self.assertIn('Student bedroom composition',instructions)
                self.assertEqual(context['scene_design']['room_identity'],design.room_identity)
                self.assertEqual(context['scene_design_choice_ids'],[choice.id for choice in design.choices])
                self.assertEqual(context['matching_source_options']['candidates'][0]['asset_id'],'polyhaven:sofa_03')
                raise EndProbe()
            self.fail('Geometry/layout must not precede design')
        with tempfile.TemporaryDirectory() as directory,patch('general_assets.local_catalog',return_value=[]), \
                patch('general_assets.planning_source_options',return_value={'candidates':[{'asset_id':'polyhaven:sofa_03'}]}):
            planner=StagedScenePlanner('unused',directory,'probe',complete=lambda *_:None)
            planner.stage=stage
            with self.assertRaises(EndProbe):asyncio.run(planner.execute('高中生卧室，榻榻米、书桌和窗户'))
        self.assertEqual([name for name,_ in calls],['request_contract','scene_design','design_feasibility','decomposition'])
        for name,instructions in calls[1:]:
            self.assertIn('Relevant reusable scene experience',instructions,name)

    def test_layout_replan_reuses_accepted_design_checkpoints(self):
        import asyncio
        from staged_planner import StagedScenePlanner,RequestContract
        from test_scene_composition import design_data
        design=SceneDesign(**design_data())
        feedback={'planning_failures':[{'stage':'layout','status':'failed','issue_count':72}],
                  'detailed_planning_failure':{'stage':'layout','issues':[{'repair_target':'geometry'}]}}
        model_calls=[]
        class EndProbe(RuntimeError):pass
        async def complete(name,instructions,payload):
            model_calls.append(name)
            if name=='request_contract_0':return RequestContract(asset_boundary='scene',quality_target='realistic').model_dump()
            if name in {'scene_design_0','design_feasibility_0'}:return design.model_dump(mode='json')
            self.fail(name)
        with tempfile.TemporaryDirectory() as directory,patch('general_assets.local_catalog',return_value=[]), \
                patch('general_assets.planning_source_options',return_value={'candidates':[]}) as source_search:
            runtime=Path(directory)/'runtime'
            first=StagedScenePlanner('unused',runtime,'first',complete=complete)
            first_stage=first.stage
            async def stop_first(name,schema,instructions,context,validate,allowed=None,**kwargs):
                if name=='decomposition':raise EndProbe()
                return await first_stage(name,schema,instructions,context,validate,allowed,**kwargs)
            first.stage=stop_first
            with self.assertRaises(EndProbe):asyncio.run(first.execute('A refined reading room'))
            self.assertEqual(model_calls,['request_contract_0','scene_design_0','design_feasibility_0'])
            self.assertEqual(source_search.call_count,1)

            second=StagedScenePlanner('unused',Path(directory)/'next','second',complete=complete,
                resume_runtime=runtime,replan_feedback=feedback)
            second_stage=second.stage
            async def stop_second(name,schema,instructions,context,validate,allowed=None,**kwargs):
                if name=='decomposition':
                    self.assertEqual(context['previous_attempt_feedback'],feedback)
                    raise EndProbe()
                return await second_stage(name,schema,instructions,context,validate,allowed,**kwargs)
            second.stage=stop_second
            with self.assertRaises(EndProbe):asyncio.run(second.execute('A refined reading room'))
            self.assertEqual(model_calls,['request_contract_0','scene_design_0','design_feasibility_0'])
            self.assertEqual(source_search.call_count,1)

    def test_missing_designed_furniture_is_rejected_before_layout(self):
        design=SceneDesign(room_identity='reading and reception room',users_and_activities='Adults reading and talking',
            spatial_composition='Grouped seats face a shared focal point.',focal_hierarchy='Main seating group and book wall',
            material_palette=['aged timber'],lighting_atmosphere='Warm local practical lighting',
            choices=[{'id':'comfortable_seating','layer':'furniture','activity':'reception',
                      'selection':'Upholstered sofa and matching armchair','quality_reason':'Long conversations need comfortable seating.'}])
        outline=Decomposition.model_validate(outline_data())
        with self.assertRaises(ValueError) as caught:validate_design_coverage(outline,design)
        self.assertIn('comfortable_seating',str(caught.exception))
        self.assertIn('Accepted choice IDs',str(caught.exception))
        self.assertIn('Do not invent aliases',str(caught.exception))
        outline.design_bindings={'comfortable_seating':['absent_sofa']}
        with self.assertRaisesRegex(ValueError,'actual component'):validate_design_coverage(outline,design)
        outline.design_bindings={'comfortable_seating':['base','ornament']}
        self.assertIs(validate_design_coverage(outline,design),outline)

    def test_exact_asset_and_front_correction_survive_compilation(self):
        from general_planner import to_general_plan
        from test_visual_repair import plan_fixture
        acquisition=Acquisition(strategy='retrieve',asset_id='polyhaven:sofa_03',front_rotation_deg=180,
            retrieval_query='vintage leather sofa',reason='Use an appropriate complete upholstered asset.')
        plan=plan_fixture();component=plan.components[0]
        component.source=acquisition.executable('prototype');component.parts=[];component.rigidity='rigid'
        component.assembly=None;component.dimensions=(.9,.9,1);component.position=(0,0,0)
        plan.parameters={};plan.edit_checks=[];plan.edit_plans=[]
        executable,_=to_general_plan(plan)
        self.assertEqual(executable.objects[0].source.asset_id,'polyhaven:sofa_03')
        self.assertEqual(executable.objects[0].source.front_rotation_deg,180)

    def test_unattached_image_cannot_be_used_as_visual_evidence(self):
        decision=Decision(verdict='needs_revision',summary='Defect',semantic_match=3,visual_quality=3,
            findings=[{'severity':'blocking','component_ids':['base'],'view':'exterior.png',
                       'evidence':'Visible missing wall','correction':'Repair the wall'}])
        self.assertTrue(invalid_image_locators(decision,['overview.png']))
        self.assertFalse(invalid_image_locators(decision,['exterior.png']))

    def test_batch_receives_only_its_images_and_related_spatial_defects(self):
        request={'components':[{'id':'chair'}],
            'render_evidence':{'images':['overview.png','exterior.png'],
                'views':[{'image':'overview.png'},{'image':'exterior.png'}]},
            'measured_geometry_evidence':{'object_world_bounds_m':{'chair':1,'other':2},
                'scene_spatial_audit':{'findings':[{'component_ids':['other']},{'component_ids':['chair']}]}}}
        batch=batch_evidence_request(request,['overview.png'],'component')
        self.assertEqual(batch['render_evidence']['images'],['overview.png'])
        self.assertEqual(batch['measured_geometry_evidence']['scene_spatial_audit']['findings'],[{'component_ids':['chair']}])
        self.assertEqual(len(request['render_evidence']['images']),2)

    def test_provider_surface_discovery_preserves_packed_roughness(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);maps=root/'plaster'/'textures';maps.mkdir(parents=True)
            for suffix in ('diff','arm','nor_gl'):(maps/f'plaster_{suffix}_2k.jpg').write_bytes(b'test')
            (root/'download_manifest.json').write_text(json.dumps({'assets':[{'asset_id':'plaster','license':'CC0','source_url':'https://example.org/plaster'}]}))
            (root/'textures_catalog.json').write_text(json.dumps({'plaster':{
                'name':'Red gingham cotton fabric','category':'textile',
                'description':'High-contrast red and cream checkered cotton weave.',
                'tags':['fabric','gingham','red'],'dimensions':[2000,2000]}}))
            with patch.object(material_catalog,'EXTERNAL',root),patch.object(material_catalog,'ROOT',root/'none'):
                result=material_catalog.catalog()['plaster']
        self.assertEqual(set(result['maps']),{'color','arm','normal'})
        self.assertEqual(result['tile_size_m'],[2,2])
        self.assertEqual(result['surface_family'],'textile')
        self.assertEqual(result['appearance_tags'],['checkered_or_plaid','red','high_contrast'])
        self.assertIn('red and cream checkered',result['visual_description'])

    def test_material_assignment_checks_the_named_surface_part_not_its_whole_component(self):
        materials={
            'rush':{'surface_family':'plant_fiber','appearance_tags':[]},
            'wood':{'surface_family':'wood','appearance_tags':[]},
            'linen':{'surface_family':'textile','appearance_tags':[]},
            'red_plaid':{'surface_family':'textile',
                         'appearance_tags':['checkered_or_plaid','red','high_contrast']},
        }
        tatami={'id':'tatami_platform','semantic':'Low tatami sleeping platform',
                'material_requirements':['muted straw mat with separate neutral linen bedding']}
        parts=[
            {'id':'tatami_mat_top','semantic':'Woven tatami mat face',
             'surface':{'texture_id':'wood'}},
            {'id':'bedding_cover','semantic':'Separate linen bedding cover',
             'surface':{'texture_id':'linen'}},
        ]
        issues=material_catalog.material_assignment_issues(tatami,parts,materials)
        self.assertEqual([(issue['part_id'],issue['texture_id']) for issue in issues],
                         [('tatami_mat_top','wood')])
        parts[0]['surface']={'texture_id':'rush'}
        self.assertEqual(material_catalog.material_assignment_issues(tatami,parts,materials),[])
        parts[0]['surface']=None
        self.assertEqual(material_catalog.material_assignment_issues(tatami,parts,materials),[])

    def test_muted_palette_flags_high_contrast_plaid_unless_requested(self):
        materials={'red_plaid':{'surface_family':'textile',
                                'appearance_tags':['checkered_or_plaid','red','high_contrast']}}
        component={'id':'seat_cushion','semantic':'Seat cushion',
                   'material_requirements':['muted neutral upholstery']}
        part={'id':'cover','semantic':'Upholstery cover',
              'surface':{'texture_id':'red_plaid'}}
        self.assertEqual(len(material_catalog.material_assignment_issues(component,[part],materials)),1)
        component['material_requirements']=['muted neutral upholstery with an explicitly requested red plaid']
        self.assertEqual(material_catalog.material_assignment_issues(component,[part],materials),[])


if __name__=='__main__':unittest.main()
