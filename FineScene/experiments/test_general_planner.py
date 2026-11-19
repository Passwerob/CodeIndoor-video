"""General planning, executable contracts and conditional acquisition regressions."""
import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from general_planner import PlannerScenePlan, to_general_plan, _planner_instructions
from general_scene import (GeneralPlan, SceneObject, Source, compile_general, judge_general_geometry,
                           scalar, edit_general, audit_exported_support_contacts)
from general_scene import Part, compile_local_geometry
import general_assets

FIXTURE = ROOT / "examples/general_metrology_fixture.json"

def raw():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))

def external_data():
    d = raw()
    obj = d["components"][1]
    obj["parts"] = []
    obj["source"] = dict(mode="auto", reason="Try suitable existing examples, then generate a missing custom silhouette.",
        retrieval_query="test sample", reference_query="isolated test sample", trellis_required=False,
        trellis_reason="Generate only if catalog candidates cannot satisfy required visible features.",
        generation_trigger="retrieval_failure", reference_requirements=["complete isolated object"], quality_target="prototype")
    return d

def write_mock_trellis_contract(command):
    output=Path(command[command.index('--output')+1]);output.mkdir(parents=True,exist_ok=True)
    mesh=output/'mesh.glb';mesh.write_bytes(b'glb')
    texture=output/'basecolor.png';texture.write_bytes(b'mock nonblank base-color texture')
    contract={
        'exchange_mesh_path':str(mesh),
        'export_mode':'uv_base_color_glb',
        'pbr':True,
        'pbr_maps':['base_color'],
        'texture_map_path':str(texture),
        'texture_map_sha256':hashlib.sha256(texture.read_bytes()).hexdigest(),
        'texture_evidence':{'textured_meshes':1,'uv_coordinates':1},
    }
    (output/'component_contract.json').write_text(json.dumps(contract),encoding='utf-8')

class GeneralTests(unittest.TestCase):
    def test_legacy_general_planner_also_gets_conditional_room_experience(self):
        instructions=_planner_instructions('高中生卧室，榻榻米和窗户')
        self.assertIn('Tatami sleep/rest assembly',instructions)
        self.assertIn('Lighting fixtures',instructions)
        self.assertIn('never create a facing relation from a door leaf',instructions)
        self.assertEqual(_planner_instructions('An outdoor stone fountain').count('Relevant reusable scene experience'),0)

    def test_complete_plan_rejects_wrong_tatami_map_and_allows_procedural_finish(self):
        data=raw()
        part=data['components'][0]['parts'][0]
        data['components'][0]['semantic']='Low tatami sleeping platform'
        data['components'][0]['material_requirements']=['muted rush-straw mat surface']
        part['semantic']='Woven tatami mat face'
        part['surface']={'texture_id':'wood_map'}
        materials={'wood_map':{'surface_family':'wood','appearance_tags':[]}}
        with patch('material_catalog.catalog',return_value=materials):
            with self.assertRaisesRegex(ValueError,'Incompatible planned surface materials'):
                PlannerScenePlan.model_validate(data)
            part['surface']=None
            PlannerScenePlan.model_validate(data)

    def test_parametric_sweep_compiles_as_real_editable_geometry(self):
        plan = GeneralPlan(scene_id='sweep', description='curved member', extent=(2, 1, 1.5), objects=[
            SceneObject(id='rail', semantic='curved rail', size=(1, .3, 1), source=Source(mode='parametric'), parts=[
                Part(id='curve', primitive='sweep', size=(1, .3, 1), center=(0, 0, .5),
                     path=[(-.4, 0, -.47), (0, .08, -.1), (.4, 0, .2)], radius=.03,
                     color=(.3, .1, .04), semantic='curved scroll rail')])
        ])
        scene, low, high = compile_local_geometry(plan, plan.objects[0])
        self.assertGreater(len(list(scene.iter_primitives())), 3)
        self.assertLess(low[0], -.35)
        self.assertGreater(high[2], .65)

    def test_sweep_rejects_missing_path(self):
        with self.assertRaises(ValueError):
            Part(id='curve', primitive='sweep', size=(1, .3, 1), center=(0, 0, 0), radius=.03)

    def test_parametric_compile_support_and_edit(self):
        plan, _ = to_general_plan(PlannerScenePlan.model_validate(raw()))
        _, before, _ = compile_general(plan)
        self.assertEqual(judge_general_geometry(plan,before)["status"],"passed")
        _, after, _ = compile_general(edit_general(plan,"base_width",.2))
        self.assertNotEqual(before["base"],after["base"])
        self.assertEqual(before["sample_01"],after["sample_01"])
        self.assertEqual(len(plan.relations),2)

    def test_auto_remains_conditional(self):
        plan, decisions = to_general_plan(PlannerScenePlan.model_validate(external_data()))
        self.assertEqual(plan.objects[1].source.mode,"auto")
        self.assertEqual(decisions[1]["generation_trigger"],"retrieval_failure")
        self.assertFalse(decisions[1]["trellis_required"])

    def test_invalid_plans(self):
        mutations = [
            lambda d: d["components"][0].update(parts=[]),
            lambda d: d["components"][1].update(instance_positions=[]),
            lambda d: d["components"][0].update(orientation_deg=[10,0,0]),
            lambda d: d["components"][0].update(parent="sample"),
            lambda d: d["components"][1].update(support="missing"),
            lambda d: d["components"][0]["source"].update(trellis_required=True),
            lambda d: d["intent"].update(coordinate_frame={"up":"+Y"}),
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                d=raw();mutation(d)
                with self.assertRaises(ValueError):PlannerScenePlan.model_validate(d)

    def test_generation_requires_justification(self):
        d=external_data(); s=d["components"][1]["source"]
        s.update(mode="generate",trellis_required=True,generation_trigger="custom_appearance")
        PlannerScenePlan.model_validate(d)
        s["reference_query"]=""
        with self.assertRaises(ValueError):PlannerScenePlan.model_validate(d)

    def test_unresolved_and_rejected_assets(self):
        plan,_=to_general_plan(PlannerScenePlan.model_validate(external_data()))
        with self.assertRaisesRegex(ValueError,"unresolved"):compile_general(plan)
        with self.assertRaisesRegex(ValueError,"visual QA"):
            compile_general(plan,{"sample_01":{"accepted":False}})

    def test_accepted_external_keeps_measured_bounds(self):
        plan,_=to_general_plan(PlannerScenePlan.model_validate(external_data()))
        assets={key:{'accepted':True,'path':str(ROOT/'runs/trellis1_toilet_20261020_01/toilet_trellis1.glb'),
                     'bounds':[[-.1,-.1,0],[.1,.1,.4]]} for key in ('sample_01','sample_02')}
        _,bounds,_=compile_general(plan,assets)
        self.assertAlmostEqual(bounds['sample_01'][1][2],.52)
        self.assertEqual(judge_general_geometry(plan,bounds)['status'],'passed')

    def test_rejected_real_assets_require_explicit_inspection_compilation(self):
        plan,_=to_general_plan(PlannerScenePlan.model_validate(external_data()))
        with tempfile.TemporaryDirectory() as directory:
            mesh=Path(directory)/'inspection.glb'
            mesh.touch()  # Compilation consumes declared bounds, not mesh bytes.
            assets={key:{'accepted':False,'inspection_only':True,'path':str(mesh),
                         'bounds':[[-.1,-.1,0],[.1,.1,.4]]} for key in ('sample_01','sample_02')}
            with self.assertRaisesRegex(ValueError,'visual QA'):compile_general(plan,assets)
            _,bounds,receipts=compile_general(plan,assets,inspection=True)
            self.assertAlmostEqual(bounds['sample_01'][1][2],.52)
            self.assertFalse(receipts['sample_01']['asset']['accepted'])

    def test_geometry_judge_rejects_exported_component_shift_from_planned_position(self):
        plan,_=to_general_plan(PlannerScenePlan.model_validate(external_data()))
        assets={key:{'accepted':True,'path':str(ROOT/'runs/trellis1_toilet_20261020_01/toilet_trellis1.glb'),
                     'bounds':[[-.1,-.1,0],[.1,.1,.4]]} for key in ('sample_01','sample_02')}
        _,bounds,receipts=compile_general(plan,assets)
        shifted=copy.deepcopy(bounds)
        shifted['sample_01']=[[x+.10 for x in bounds['sample_01'][0]],
                              [x+.10 for x in bounds['sample_01'][1]]]
        audit=judge_general_geometry(plan,shifted,receipts)
        self.assertEqual(audit['status'],'needs_revision')
        self.assertTrue(any('differs from planned center' in item['evidence'] for item in audit['findings']))

    def test_exported_external_support_uses_measured_contact_and_declared_envelope(self):
        data=external_data()
        child=data['components'][1]
        child.update(parent='base',support='base',attachment='top',support_footprint=[.16,.16],
                     interface_notes=['Flat circular base contacts the supporting surface.'])
        plan,_=to_general_plan(PlannerScenePlan.model_validate(data))
        assets={key:{'accepted':True,'path':str(ROOT/'runs/trellis1_toilet_20261020_01/toilet_trellis1.glb'),
                     'bounds':[[-.09,-.09,0],[.09,.09,.307]]} for key in ('sample_01','sample_02')}
        _,bounds,_=compile_general(plan,assets)
        contacts={key:{'method':'evaluated_blender_mesh_contact_vertices',
            'contact_side':'bottom','local_bounds':[[-.055,-.055,0],[.055,.055,.003]],
            'world_bounds':[[x-.055,-.055,.12],[x+.055,.055,.123]],
            'measured_footprint':[.11,.11],'declared_footprint':[.16,.16],
            'bottom_tolerance_m':.003,'contact_vertex_count':100,'total_vertex_count':1000}
            for key,x in [('sample_01',-.4),('sample_02',.4)]}
        audit=audit_exported_support_contacts(plan,bounds,contacts)
        self.assertEqual(audit['status'],'passed',audit['findings'])
        self.assertEqual(audit['components']['sample_01']['measured_footprint'],[.11,.11])
        self.assertAlmostEqual(audit['components']['sample_01']['vertical_gap_m'],0,places=6)
        self.assertIn('support_surface_world_bounds',audit['components']['sample_01'])
        self.assertAlmostEqual(audit['components']['sample_01']['contact_center_error_world_xy'][0],0,places=6)

    def test_exported_external_support_rejects_contact_shift_from_agent_target(self):
        data=external_data()
        child=data['components'][1]
        child.update(parent='base',support='base',attachment='top',support_footprint=[.16,.16],
                     interface_notes=['Centered flat underside contact.'])
        plan,_=to_general_plan(PlannerScenePlan.model_validate(data))
        assets={key:{'accepted':True,'path':str(ROOT/'runs/trellis1_toilet_20261020_01/toilet_trellis1.glb'),
                     'bounds':[[-.09,-.09,0],[.09,.09,.307]]} for key in ('sample_01','sample_02')}
        _,bounds,receipts=compile_general(plan,assets)
        contacts={}
        for key,x in [('sample_01',-.36),('sample_02',.44)]:
            contacts[key]={'method':'evaluated_blender_mesh_contact_vertices',
                'local_bounds':[[-.055,-.055,0],[.055,.055,.003]],
                'world_bounds':[[x-.055,-.055,.12],[x+.055,.055,.123]],
                'component_local_bounds':[[-.09,-.09,0],[.09,.09,.307]],
                'contact_side':'bottom','measured_footprint':[.11,.11],
                'declared_footprint':[.16,.16],'bottom_tolerance_m':.003,
                'contact_vertex_count':100,'total_vertex_count':1000}
        audit=audit_exported_support_contacts(plan,bounds,contacts,receipts=receipts)
        self.assertEqual(audit['status'],'needs_revision')
        self.assertTrue(any('differs from planned support offset target' in item['evidence']
                            for item in audit['findings']))

    def test_exported_external_support_rejects_wrong_contact_location_and_size(self):
        data=external_data()
        child=data['components'][1]
        child.update(parent='base',support='base',attachment='top',support_footprint=[.16,.16],
                     interface_notes=['Flat circular base contacts the supporting surface.'])
        plan,_=to_general_plan(PlannerScenePlan.model_validate(data))
        assets={key:{'accepted':True,'path':str(ROOT/'runs/trellis1_toilet_20261020_01/toilet_trellis1.glb'),
                     'bounds':[[-.09,-.09,0],[.09,.09,.307]]} for key in ('sample_01','sample_02')}
        _,bounds,_=compile_general(plan,assets)
        contacts={key:{'method':'evaluated_blender_mesh_contact_vertices',
            'contact_side':'bottom','local_bounds':[[.02,-.04,0],[.20,.04,.003]],
            'world_bounds':[[x+.02,-.04,.12],[x+.20,.04,.123]],
            'measured_footprint':[.18,.08],'declared_footprint':[.16,.16],
            'bottom_tolerance_m':.003,'contact_vertex_count':100,'total_vertex_count':1000}
            for key,x in [('sample_01',-.4),('sample_02',.4)]}
        audit=audit_exported_support_contacts(plan,bounds,contacts)
        self.assertEqual(audit['status'],'needs_revision')
        self.assertGreaterEqual(len(audit['findings']),1)
        self.assertTrue(any('contact' in f['evidence'].lower() or 'footprint' in f['evidence'].lower()
                            for f in audit['findings']))

    def test_exported_external_support_rejects_component_that_is_too_small(self):
        data=external_data()
        child=data['components'][1]
        child.update(parent='base',support='base',attachment='top',support_footprint=[.16,.16],
                     interface_notes=['Flat underside contacts the centered support surface.'])
        plan,_=to_general_plan(PlannerScenePlan.model_validate(data))
        assets={key:{'accepted':True,'path':str(ROOT/'runs/trellis1_toilet_20261020_01/toilet_trellis1.glb'),
                     'bounds':[[-.09,-.09,0],[.09,.09,.307]]} for key in ('sample_01','sample_02')}
        _,bounds,receipts=compile_general(plan,assets)
        contacts={key:{'method':'evaluated_blender_mesh_contact_vertices',
            'local_bounds':[[-.04,-.04,0],[.04,.04,.002]],
            'world_bounds':[[x-.04,-.04,.12],[x+.04,.04,.122]],
            'component_local_bounds':[[-.05,-.05,0],[.05,.05,.30]],
            'contact_side':'bottom','measured_footprint':[.08,.08],
            'declared_footprint':[.16,.16],'bottom_tolerance_m':.002,
            'contact_vertex_count':100,'total_vertex_count':1000}
            for key,x in [('sample_01',-.4),('sample_02',.4)]}
        audit=audit_exported_support_contacts(plan,bounds,contacts,receipts=receipts)
        self.assertEqual(audit['status'],'needs_revision')
        self.assertTrue(any('occupy less than 60%' in item['evidence'] for item in audit['findings']))

    def test_named_support_part_anchors_external_child_to_real_parent_surface(self):
        data=json.loads((ROOT/'runs'/'baroque_agent_repair24_provider_gate_20261022_08'/'general_plan.json').read_text(encoding='utf-8'))
        data['objects']=[o for o in data['objects'] if o['id'] in {'baroque_floor_bookcase','crown_center_urn_finial'}]
        for obj in data['objects']:
            if obj['id']=='crown_center_urn_finial':obj['support_part']='top_rail'
        data['relations']=[r for r in data['relations'] if r.get('from') in {o['id'] for o in data['objects']} and r.get('to') in {o['id'] for o in data['objects']}]
        plan=GeneralPlan.model_validate(data)
        asset=json.loads((ROOT/'runs'/'trellis1_textured_probe_20261022_07'/'preview'/'asset.json').read_text(encoding='utf-8'))
        asset['accepted']=True
        _,bounds,receipts=compile_general(plan,{'crown_center_urn_finial':asset})
        support_bounds=receipts['baroque_floor_bookcase']['internal_geometry']['parts']['top_rail']['bounds']
        self.assertAlmostEqual(bounds['crown_center_urn_finial'][0][2],support_bounds[1][2],places=5)
        self.assertEqual(receipts['crown_center_urn_finial']['support_part'],'top_rail')
        self.assertEqual(judge_general_geometry(plan,bounds,receipts)['status'],'passed')

    def test_named_support_part_must_be_a_parent_box_face(self):
        data=json.loads((ROOT/'runs'/'baroque_agent_repair24_provider_gate_20261022_08'/'general_plan.json').read_text(encoding='utf-8'))
        data['objects']=[o for o in data['objects'] if o['id'] in {'baroque_floor_bookcase','crown_center_urn_finial'}]
        for obj in data['objects']:
            if obj['id']=='crown_center_urn_finial':obj['support_part']='side_scroll_stile_left'
        data['relations']=[r for r in data['relations'] if r.get('from') in {o['id'] for o in data['objects']} and r.get('to') in {o['id'] for o in data['objects']}]
        with self.assertRaisesRegex(ValueError,'horizontal parametric box'):
            GeneralPlan.model_validate(data)

    def test_planned_extent_and_clearance_fail_before_acquisition(self):
        d=raw();d['intent']['extent']=[.2,.2,.2]
        with self.assertRaisesRegex(ValueError,'preflight'):
            to_general_plan(PlannerScenePlan.model_validate(d))

    def test_rigid_size_edit_is_rejected(self):
        d=raw();d['parameters']['sample_height']=.5
        d['components'][1]['dimensions'][2]='sample_height'
        plan,_=to_general_plan(PlannerScenePlan.model_validate(d))
        with self.assertRaisesRegex(ValueError,'rigid'):
            edit_general(plan,'sample_height',.1)

    def test_unsafe_expression(self):
        for expr in ("__import__('os')", "a.x", "2**100", "1/0", "nan"):
            with self.subTest(expr=expr), self.assertRaises((ValueError,ZeroDivisionError)):
                scalar(expr,{})
        self.assertEqual(scalar("w/2 + 0.1",{"w":1}),.6)

    def test_declared_support_is_checked(self):
        plan,_=to_general_plan(PlannerScenePlan.model_validate(raw()))
        _,bounds,_=compile_general(plan)
        bounds['sample_01'][0][2]+=.1
        self.assertEqual(judge_general_geometry(plan,bounds)['status'],'needs_revision')

    def test_auto_rejects_local_then_tries_remote_before_generation(self):
        plan,_=to_general_plan(PlannerScenePlan.model_validate(external_data()))
        obj=plan.objects[1]
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory); mesh=folder/'candidate.glb';mesh.write_bytes(b'test')
            catalog=[{'asset_id':'test-sample','label':'test sample','path':str(mesh)}]
            accepted={'accepted':True,'path':str(mesh)}
            with patch.object(general_assets,'prepare_candidate',side_effect=[{'accepted':False},accepted]) as prepare, \
                 patch.object(general_assets,'search_polyhaven',return_value=catalog) as search, \
                 patch.object(general_assets,'image_candidates') as references:
                receipt=general_assets.resolve_component(obj,plan,folder,catalog,'profile','prototype')
            self.assertEqual(prepare.call_count,2);search.assert_called_once();references.assert_not_called()
            self.assertEqual(receipt['actual_mode'],'retrieve')
            self.assertEqual(receipt['prior_attempts'][0]['stage'],'retrieval_qa')

    def test_auto_reaches_generation_after_failed_retrieval(self):
        plan,_=to_general_plan(PlannerScenePlan.model_validate(external_data()))
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(general_assets,'image_candidates',return_value=[]) as references:
                with self.assertRaisesRegex(RuntimeError,'Unresolved'):
                    general_assets.resolve_component(plan.objects[1],plan,Path(directory),[],'profile','prototype',offline=True)
                references.assert_called_once()
            evidence=json.loads(next(Path(directory).rglob('resolution.json')).read_text())
            self.assertEqual([e['stage'] for e in evidence['errors']],['retrieval','reference_search'])

    def test_reference_retry_uses_judge_evidence_and_excludes_rejected_sources(self):
        data=external_data()
        data['components'][1]['source'].update(mode='generate',trellis_required=True,
            trellis_reason='The required carved silhouette is unavailable as an editable primitive.',
            generation_trigger='custom_appearance')
        plan,_=to_general_plan(PlannerScenePlan.model_validate(data))
        obj=plan.objects[1]
        calls=[]
        def references(_obj,_folder,_offline=False,**kwargs):
            calls.append(kwargs)
            if kwargs['attempt']==0:
                return [
                    {'path':'rejected.png','query':'whole cabinet','source_url':'https://commons.test/rejected',
                     'image_url':'https://img.test/rejected','page_title':'rejected image',
                     'provider':'Cleveland Museum of Art Open Access','catalog_id':'museum-17'},
                    {'path':'unavailable.png','query':'whole cabinet','source_url':'https://commons.test/unavailable',
                     'image_url':'https://img.test/unavailable','page_title':'unavailable image'},
                ]
            return [{'path':'accepted.png','query':'carved crown','source_url':'https://commons.test/accepted',
                     'image_url':'https://img.test/accepted','page_title':'accepted image'}]
        reviews=[
            {'accepted':False,'status':'needs_revision','decision':{'summary':'The image is a full cabinet, not the isolated crest.',
             'findings':[{'severity':'blocking','view':'image','evidence':'Whole cabinet shown.',
                          'correction':'Search for an isolated carved crest.','repair_target':'decomposition'}]}},
            {'accepted':False,'status':'unavailable','error':'judge response was inconsistent'},
            {'accepted':True,'status':'passed','decision':{'summary':'Isolated carved crest.'}},
        ]
        def generate(command, _log):
            write_mock_trellis_contract(command)
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(general_assets,'image_candidates',side_effect=references), \
             patch.object(general_assets,'review_images',side_effect=reviews), \
             patch.object(general_assets,'run_logged',side_effect=generate), \
             patch.object(general_assets,'prepare_candidate',return_value={'accepted':True,'path':str(Path(directory)/'accepted.glb')}):
            accepted_mesh=Path(directory)/'accepted.glb';accepted_mesh.write_bytes(b'accepted')
            receipt=general_assets.resolve_component(obj,plan,Path(directory),[],'profile','prototype')
        self.assertEqual(receipt['actual_mode'],'generate')
        self.assertEqual(len(calls),2)
        self.assertEqual(calls[1]['feedback'][0]['summary'],'The image is a full cabinet, not the isolated crest.')
        self.assertEqual(len(calls[1]['feedback']),1)
        self.assertEqual(calls[1]['feedback'][0]['catalog_id'],'museum-17')
        self.assertEqual(calls[1]['feedback'][0]['provider'],'Cleveland Museum of Art Open Access')
        self.assertIn('https://commons.test/rejected',calls[1]['exclude_sources'])
        self.assertIn('https://img.test/rejected',calls[1]['exclude_sources'])
        self.assertIn('https://commons.test/unavailable',calls[1]['exclude_sources'])

    def test_generation_qa_failure_tries_another_accepted_reference(self):
        data=external_data()
        data['components'][1]['source'].update(mode='generate',trellis_required=True,
            trellis_reason='The required carved silhouette is unavailable as an editable primitive.',
            generation_trigger='custom_appearance')
        plan,_=to_general_plan(PlannerScenePlan.model_validate(data))
        obj=plan.objects[1]
        references=[
            {'path':'reference_a.png','query':'isolated carved crest','source_url':'https://museum.test/crest-a',
             'image_url':'https://img.test/crest-a','page_title':'isolated crest A'},
            {'path':'reference_b.png','query':'baroque wood carving','source_url':'https://museum.test/crest-b',
             'image_url':'https://img.test/crest-b','page_title':'isolated crest B'},
        ]
        def generate(command, _log):
            write_mock_trellis_contract(command)
        with tempfile.TemporaryDirectory() as directory:
            accepted_mesh=Path(directory)/'accepted.glb'
            accepted_mesh.write_bytes(b'accepted')
            generation_qa=[{'accepted':False,'path':'bad-a.glb'},
                           {'accepted':False,'path':'bad-b.glb'},
                           {'accepted':True,'path':str(accepted_mesh)}]
            def prepare(_obj, _plan, source, *args):
                result=generation_qa.pop(0)
                result=dict(result, provenance=source)
                return result
            with patch.object(general_assets,'image_candidates',return_value=references) as search, \
                 patch.object(general_assets,'review_images',return_value={'accepted':True,'status':'passed'}) as review, \
                 patch.object(general_assets,'run_logged',side_effect=generate) as trellis, \
                 patch.object(general_assets,'prepare_candidate',side_effect=prepare):
                receipt=general_assets.resolve_component(obj,plan,Path(directory),[],'profile','prototype')
        self.assertTrue(receipt['accepted'])
        self.assertEqual(receipt['actual_mode'],'generate')
        search.assert_called_once()
        self.assertEqual(review.call_count,2)
        self.assertEqual(trellis.call_count,3)
        self.assertEqual([attempt['stage'] for attempt in receipt['prior_attempts']],
                         ['generation_qa','generation_qa'])
        self.assertEqual(receipt['provenance']['reference']['source_url'],references[1]['source_url'])

    def test_reference_metadata_filter_uses_word_boundaries(self):
        self.assertFalse(general_assets._reference_metadata_forbidden(
            'File:bookcase_crown.jpg','carved bookcase crest','baroque bookcase crown',
            target_text='baroque bookcase crown'))
        self.assertTrue(general_assets._reference_metadata_forbidden(
            'File:book_scan.jpg','scanned book page','baroque bookcase crown',
            target_text='baroque bookcase crown'))
        # Parent context may mention its own furniture, but an unrelated full
        # chaise, mirror, or cabinet is not a reference for an isolated crest.
        context='baroque carved crest mounted on a bookcase; single isolated crest'
        target='baroque carved crest; single isolated crest'
        self.assertTrue(general_assets._reference_metadata_forbidden(
            'File:Chaise (USA).jpg','upholstered chaise with carved crest',context,target))
        self.assertTrue(general_assets._reference_metadata_forbidden(
            'File:Mirror (England).jpg','gilded mirror and crest',context,target))
        self.assertTrue(general_assets._reference_metadata_forbidden(
            'File:Building facade.jpg','baroque window pediment',context,target))
        self.assertTrue(general_assets._reference_metadata_forbidden(
            'File:Stone relief.jpg','marble acanthus ornament',context,target))
        self.assertTrue(general_assets._reference_metadata_forbidden(
            'File:Helmet crest.jpg','Roman helmet with metal crest',context,target))
        self.assertTrue(general_assets._reference_metadata_forbidden(
            'File:Baroque portrait.jpg','oil portrait of a woman in a dress',context,target))
        self.assertTrue(general_assets._reference_metadata_forbidden(
            'File:Totem crests.jpg','wood worm crest and totem carving',context,target))
        self.assertFalse(general_assets._reference_metadata_forbidden(
            'File:Chaise crown.jpg','crest decoration',
            'wooden chaise longue','chaise crown crest'))

    def test_reference_search_filters_context_photos_and_collects_later_fallbacks(self):
        obj=copy.deepcopy(external_data())
        plan,_=to_general_plan(PlannerScenePlan.model_validate(obj))
        target=plan.objects[1]
        queries=[]
        def fake_fetch(url,path=None,limit=None):
            if path:
                Path(path).write_bytes(b'\x89PNG\r\n\x1a\n'+b'0'*32)
                return str(path)
            from urllib.parse import urlparse,parse_qs
            query=parse_qs(urlparse(url).query)['gsrsearch'][0];queries.append(query)
            if query==target.source.reference_query:
                title='File:baroque facade.jpg';obj_name='Baroque facade';url='https://img.test/facade.jpg'
            else:
                index=len([q for q in queries if q!=target.source.reference_query])
                title=f'File:carved ornament {index}.jpg';obj_name='Carved wood ornament';url=f'https://img.test/ornament{index}.jpg'
            return {'query':{'pages':{'1':{'title':title,'imageinfo':[{
                'url':url,'descriptionurl':url,'extmetadata':{
                    'ObjectName':{'value':obj_name},'ImageDescription':{'value':obj_name},
                    'Categories':{'value':'Carved wood ornament'}}}]}}}}
        with tempfile.TemporaryDirectory() as directory,patch.object(general_assets,'fetch',side_effect=fake_fetch), \
             patch.object(general_assets,'refine_reference_queries',return_value=[]):
            result=general_assets.image_candidates(target,Path(directory),profile='unused')
        self.assertEqual(len(result),3)
        self.assertTrue(all('ornament' in item['page_title'] for item in result))
        self.assertGreaterEqual(len(queries),3)

if __name__ == '__main__':unittest.main(verbosity=2)
