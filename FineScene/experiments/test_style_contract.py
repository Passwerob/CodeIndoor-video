"""Style must have executable geometry and agent-owned escalation, not labels."""
import copy
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from staged_planner import RequestContract,Decomposition,validate_request_contract
from general_vision import Decision,StyleAssessment,Finding,decision_inconsistent,decision_inconsistent_for_scope
from run_general_scene import run,write_json,planning_feedback,protect_validated_recipes
from test_staged_planner import outline_data


def requirement():
    return {'id':'carving','feature':'Sculpted flowing relief','visible_evidence':'Visible curved lobes and recessed relief depth',
            'geometry_need':'freeform_surface'}


class StyleTests(unittest.TestCase):
    def test_later_scene_defects_invalidate_only_implicated_parametric_recipes(self):
        entries=[{'id':'shell'},{'id':'shelf'},{'id':'rug'}]
        feedback={'validated_parametric_components':copy.deepcopy(entries),
            'validated_parametric_specs':copy.deepcopy(entries),
            'visual':{'decision':{'findings':[
                {'severity':'blocking','repair_target':'geometry','component_ids':['shell']},
                {'severity':'blocking','repair_target':'layout','component_ids':['shelf']}]}},
            'geometry':{'findings':[]}}
        result=protect_validated_recipes(feedback,[{'mode':'retrieve'}])
        self.assertEqual([r['id'] for r in result['validated_parametric_specs']],['shelf','rug'])
        self.assertEqual(result['parametric_recipes_requiring_revision'],['shell'])

    def test_one_unavailable_batch_does_not_hide_known_physical_failure(self):
        calls=[]
        def attempt(args):
            calls.append(args)
            write_json(args.out/'agent_runtime/frozen_request_contract.json',{'asset_boundary':'scene','quality_target':'prototype'})
            if len(calls)==1:
                return {'status':'rejected','accepted':False,'visual':{'status':'unavailable'},
                        'geometry':{'status':'needs_revision','findings':[{
                            'component_ids':['chair'],'repair_target':'layout','evidence':'Measured overlap.'}]}}
            self.assertEqual(args.replan_feedback['geometry']['findings'][0]['evidence'],'Measured overlap.')
            return {'status':'accepted','accepted':True}
        with tempfile.TemporaryDirectory() as directory:
            result=run(SimpleNamespace(out=Path(directory)/'run',resume_runtime=None,planner='llm',
                plan_only=False,max_replans=0),attempt_runner=attempt)
        self.assertTrue(result['accepted']);self.assertEqual(len(calls),2)

    def test_component_incidental_assessments_do_not_invent_frozen_style_ids(self):
        decision=Decision(verdict='passed',summary='A complete real textured chair.',semantic_match=5,
            visual_quality=4,findings=[],style_assessments=[StyleAssessment(
                requirement_id='upholstered seat',status='realized',evidence='Visible green fabric.')])
        self.assertFalse(decision_inconsistent_for_scope(decision,{},'component'))
        self.assertTrue(decision_inconsistent_for_scope(decision,{'style_requirements':[requirement()]},'component'))
        decision.findings=[Finding(severity='blocking',component_ids=['chair'],view='front.png',
            evidence='The backrest is detached.',correction='Replace the defective model.',repair_target='source')]
        self.assertTrue(decision_inconsistent_for_scope(decision,{},'component'))

    def test_reference_judge_uses_geometry_protocol_without_scene_style_ids(self):
        decision=Decision(verdict='needs_revision',summary='Not the requested crest.',semantic_match=2,
            visual_quality=2,findings=[Finding(severity='blocking',component_ids=[],view='ref.png',
                evidence='The photo shows an unrelated full furniture object.',
                correction='Find one isolated crest.',repair_target='source')],
            style_assessments=[StyleAssessment(requirement_id='invented-id',status='missing',
                                                evidence='No matching style.')])
        request={'style_requirements':[requirement()]}
        self.assertFalse(decision_inconsistent_for_scope(decision,request,'reference'))
        self.assertTrue(decision_inconsistent(decision,request))
        inconsistent=decision.model_copy(deep=True)
        inconsistent.verdict='passed'
        self.assertTrue(decision_inconsistent_for_scope(inconsistent,request,'reference'))

    def test_reference_suffix_matches_downloaded_image_mime(self):
        from general_assets import normalize_image_extension,is_supported_image
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'reference.png';data=b'\xff\xd8\xff\xe0'+b'\0'*20
            path.write_bytes(data)
            self.assertTrue(is_supported_image(path))
            normalized=Path(normalize_image_extension(path))
            self.assertEqual(normalized.suffix,'.jpg')
            self.assertEqual(normalized.read_bytes(),data)

    def test_replanning_receives_actual_failed_constraint(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime=Path(directory)/'agent_runtime'
            write_json(runtime/'stage_events.json',[{'stage':'decomposition','status':'failed','error':'Freeform style requires an external child'}])
            write_json(runtime/'decomposition_failure_6.json',{'candidate':{'objects':[{'id':'base','semantic':'ornate box','acquisition':{'strategy':'parametric'}}]}})
            result=planning_feedback(directory)
        self.assertIn('external child',result['planning_failures'][0]['error'])
        self.assertEqual(result['previous_decomposition']['objects'][0]['id'],'base')

    def test_replanning_feedback_marks_validated_parametric_root_when_only_source_failed(self):
        root=Path(__file__).resolve().parents[1]/'runs'/'baroque_agent_repair13_20261022_09'
        result=planning_feedback(root)
        self.assertEqual([item['id'] for item in result['validated_parametric_components']],
                         ['baroque_floor_bookcase'])
        self.assertEqual([item['id'] for item in result['validated_parametric_specs']],
                         ['baroque_floor_bookcase'])
        self.assertIn('internal_part_contract',result['validated_parametric_components'][0])
        self.assertIn('validated_edit_checks',result)

    def test_current_scene_design_does_not_freeze_unselected_old_components(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);runtime=root/'agent_runtime';runtime.mkdir()
            plan={'scene_id':'scene','description':'A reading lounge','style':'vintage','quality_target':'realistic',
                'extent':[4,4,3],'parameters':{},'objects':[
                    {'id':'room_shell','semantic':'Current enclosure','acquisition':{'strategy':'parametric'},
                     'internal_parts':[{'id':'floor'}]},
                    {'id':'console_table','semantic':'Unselected old arrival console','acquisition':{'strategy':'parametric'},
                     'internal_parts':[{'id':'top'}]}],
                'design_bindings':{'architecture':['room_shell'],'arrival':['console_table']}}
            write_json(runtime/'frozen_decomposition.json',{'plan':plan})
            write_json(runtime/'request_contract_accepted.json',{'style_requirements':[]})
            write_json(runtime/'scene_design.json',{'choices':[{'id':'architecture'}]})
            write_json(runtime/'stage_events.json',[{'stage':'recipe_room_shell','status':'validated'},
                {'stage':'recipe_console_table','status':'validated'}])
            write_json(runtime/'recipe_room_shell_accepted.json',{'parts':[{'id':'floor'}]})
            write_json(runtime/'recipe_console_table_accepted.json',{'parts':[{'id':'top'}]})
            result=planning_feedback(root)
        self.assertEqual([item['id'] for item in result['validated_parametric_specs']],['room_shell'])
        self.assertEqual(result['current_scene_design']['choices'][0]['id'],'architecture')

    def test_external_source_replan_preserves_validated_parametric_root(self):
        calls=[]
        def attempt(args):
            calls.append(args)
            args.out.mkdir(parents=True,exist_ok=True)
            runtime=args.out/'agent_runtime'
            write_json(runtime/'frozen_request_contract.json',{'asset_boundary':'single_asset','quality_target':'prototype'})
            write_json(runtime/'stage_events.json',[{'stage':'layout','status':'validated'}])
            write_json(runtime/'frozen_decomposition.json',{'plan':outline_data()})
            if len(calls)==1:
                return {'status':'rejected','accepted':False,'unresolved':[
                    {'component_id':'ornament','mode':'auto','error':'No source passed reference QA.'}]}
            feedback=args.replan_feedback
            self.assertEqual([item['id'] for item in feedback['validated_parametric_components']],['base'])
            self.assertIn('Preserve every validated_parametric_components entry exactly',feedback['instruction'])
            self.assertIn('split that target',feedback['instruction'])
            return {'status':'accepted','accepted':True}
        with tempfile.TemporaryDirectory() as directory:
            args=SimpleNamespace(out=Path(directory)/'run',resume_runtime=None,planner='llm',
                                  plan_only=False,max_replans=1)
            result=run(args,attempt_runner=attempt)
        self.assertTrue(result['accepted'])
        self.assertEqual(len(calls),2)

    def test_validated_parametric_spec_is_restored_before_source_replan_validation(self):
        from staged_planner import restore_validated_parametric_specs
        original=outline_data()
        changed=copy.deepcopy(original)
        changed['objects'][0]['semantic']='rewritten root that must not replace the accepted recipe'
        changed['objects'][0]['required_features']=['rewritten local requirement']
        restored=restore_validated_parametric_specs(
            Decomposition.model_validate(changed),
            {'validated_parametric_specs':[original['objects'][0]],
             'validated_parameters':original['parameters'],
             'validated_edit_checks':original.get('edit_checks',[])})
        expected=Decomposition.model_validate(original).objects[0].model_dump(mode='json')
        self.assertEqual(restored.objects[0].model_dump(mode='json'),expected)

    def test_initial_report_feedback_loads_validated_plan_context_from_run_folder(self):
        captured=[]
        with tempfile.TemporaryDirectory() as directory:
            previous=Path(directory)/'previous'
            runtime=previous/'agent_runtime'
            write_json(previous/'run_report.json',{'status':'rejected','accepted':False,'unresolved':[
                {'component_id':'ornament','mode':'auto','error':'No source passed visual QA.'}]})
            write_json(runtime/'stage_events.json',[{'stage':'layout','status':'validated'}])
            write_json(runtime/'frozen_decomposition.json',{'plan':outline_data()})
            def attempt(args):
                captured.append((args.replan_feedback,args.resume_runtime))
                args.out.mkdir(parents=True,exist_ok=True)
                write_json(args.out/'agent_runtime/frozen_request_contract.json',
                           {'asset_boundary':'single_asset','quality_target':'prototype'})
                return {'status':'planned','accepted':False}
            args=SimpleNamespace(out=Path(directory)/'new_run',resume_runtime=None,planner='llm',
                plan_only=False,max_replans=0,initial_feedback=previous/'run_report.json')
            run(args,attempt_runner=attempt)
        feedback,resume_runtime=captured[0]
        self.assertEqual([item['id'] for item in feedback['validated_parametric_components']],['base'])
        self.assertIn('Preserve every validated_parametric_components entry exactly',feedback['instruction'])
        self.assertEqual(resume_runtime,runtime)

    def test_visually_rejected_sweep_cannot_be_repeated_in_source_replan(self):
        from staged_planner import validate_replan_style_feedback
        data=outline_data()
        data['style_bindings']=[{'requirement_id':'carving','component_ids':['base'],
            'implementation':'A constant-radius sweep implements the flowing relief.',
            'part_ids':['base/rail']}]
        plan=Decomposition.model_validate(data)
        feedback={'previous_decomposition':{
            'objects':[{'id':'base','acquisition':{'strategy':'parametric'}}],
            'style_bindings':[{'requirement_id':'carving','component_ids':['base'],
                               'part_ids':['base/rail']}]},
            'visual':{'decision':{'verdict':'needs_revision','findings':[{
                'severity':'blocking','requirement_ids':['carving'],'evidence':'The sweep reads as a thin tube.'}],
                'style_assessments':[{'requirement_id':'carving','status':'missing','evidence':'No sculpted relief.'}]}}}
        with self.assertRaisesRegex(ValueError,'must bind a real rigid retrieve/auto/generate child'):
            validate_replan_style_feedback(plan,feedback)
        data['style_bindings'][0]['component_ids']=['ornament']
        data['style_bindings'][0]['part_ids']=[]
        plan=Decomposition.model_validate(data)
        self.assertIs(validate_replan_style_feedback(plan,feedback),plan)

    def test_failed_contact_measurement_does_not_raise_secondary_keyerror(self):
        from general_planner import PlannerScenePlan
        from general_repairs import _repair_contact_evidence
        plan_path=Path(__file__).resolve().parents[1]/'runs/agent_baroque_sweep_20261021_03/planner_output.json'
        plan=PlannerScenePlan.model_validate_json(plan_path.read_text(encoding='utf-8'))
        component=next(c for c in plan.components if c.assembly and c.assembly.relations)
        relation=component.assembly.relations[0]
        order={'component_ids':[component.id],'part_ids':[relation.part,relation.target]}
        with patch('general_scene.compile_local_geometry',side_effect=KeyError('parts')):
            evidence=_repair_contact_evidence(plan,[order])
        self.assertTrue(evidence)
        self.assertIn('baseline_validation_error',evidence[0])
        self.assertIn("'parts'",evidence[0]['baseline_validation_error'])

    def test_transient_service_retry_preserves_request_and_does_not_replan(self):
        import asyncio
        from service_retry import call_with_service_retry
        calls=[];events=[]
        async def invoke():
            calls.append('same_request')
            if len(calls)==1:raise TimeoutError('transport')
            return 'response'
        self.assertEqual(asyncio.run(call_with_service_retry(invoke,events.append,base_delay=0)),'response')
        self.assertEqual(calls,['same_request','same_request'])
        self.assertTrue(events[0]['same_request_retry'])
        self.assertIn('elapsed_seconds',events[0])
        calls.clear()
        def attempt(args):
            calls.append(args)
            write_json(args.out/'agent_runtime/frozen_request_contract.json',{'asset_boundary':'scene','quality_target':'prototype'})
            return {'status':'rejected','accepted':False,'error_type':'APIConnectionError','error':'Connection error'}
        with tempfile.TemporaryDirectory() as directory:
            args=SimpleNamespace(out=Path(directory)/'run',resume_runtime=None,planner='llm',plan_only=False,max_replans=2)
            run(args,attempt_runner=attempt)
        self.assertEqual(len(calls),1)

    def test_incomplete_stream_retries_same_request_and_does_not_replan(self):
        import asyncio
        from httpx import RemoteProtocolError
        from service_retry import call_with_service_retry
        calls=[];events=[]
        async def invoke():
            calls.append('same_request')
            if len(calls)==1:raise RemoteProtocolError('incomplete chunked read')
            return 'complete_response'
        self.assertEqual(asyncio.run(call_with_service_retry(invoke,events.append,base_delay=0)),'complete_response')
        self.assertEqual(calls,['same_request','same_request'])
        self.assertTrue(events[0]['same_request_retry'])
        self.assertNotIn('headers',events[0])
        calls.clear()
        def attempt(args):
            calls.append(args)
            write_json(args.out/'agent_runtime/frozen_request_contract.json',{'asset_boundary':'scene','quality_target':'prototype'})
            return {'status':'rejected','accepted':False,'error_type':'RemoteProtocolError',
                    'error':'incomplete chunked read'}
        with tempfile.TemporaryDirectory() as directory:
            args=SimpleNamespace(out=Path(directory)/'run',resume_runtime=None,planner='llm',plan_only=False)
            run(args,attempt_runner=attempt)
        self.assertEqual(len(calls),1)

    def test_service_failure_reports_a_resume_point_for_the_failed_stage(self):
        class APIConnectionError(Exception):pass
        def attempt(args):
            error=APIConnectionError('Connection error.')
            error.finescene_stage='spatial_revision'
            raise error
        with tempfile.TemporaryDirectory() as directory:
            out=Path(directory)/'living_room_run'
            args=SimpleNamespace(out=out,resume_runtime=None,planner='llm',plan_only=False,
                                 brief='一个普通家庭客厅')
            report=run(args,attempt_runner=attempt)
            self.assertEqual(report['stop_reason'],'model_service_unavailable')
            resume=report['resume']
            self.assertEqual(resume['stage'],'spatial_revision')
            self.assertEqual(Path(resume['runtime']),out/'agent_runtime')
            self.assertEqual(Path(resume['out']),Path(directory)/'living_room_run_resume')
            self.assertIn('--resume-runtime',resume['command'])
            self.assertIn("--brief '一个普通家庭客厅'",resume['command'])
            self.assertEqual(json.loads((out/'run_report.json').read_text(encoding='utf-8'))['resume']['stage'],
                             'spatial_revision')
            self.assertNotIn('http',resume)

    def test_reference_search_uses_agent_queries_and_keeps_download_failures_local(self):
        import general_assets
        obj=SimpleNamespace(semantic='sculpted local relief',source=SimpleNamespace(
            reference_image=None,reference_query='very long image specification',query=''))
        queries=[]
        def fetch(url,path=None,limit=None):
            if path:
                if 'broken' in url:raise OSError('download failed')
                Path(path).write_bytes(b'\x89PNG\r\n\x1a\n'+b'\0'*30);return str(path)
            from urllib.parse import urlparse,parse_qs
            query=parse_qs(urlparse(url).query)['gsrsearch'][0];queries.append(query)
            return {'query':{'pages':{} if len(queries)==1 else {
                '1':{'imageinfo':[{'url':'https://example.test/broken.png'}]},
                '2':{'imageinfo':[{'url':'https://example.test/relief.png','descriptionurl':'https://example.test/source'}]}}}}
        with tempfile.TemporaryDirectory() as directory,patch.object(general_assets,'fetch',side_effect=fetch),patch.object(
            general_assets,'refine_reference_queries',return_value=['carved relief']):
            result=general_assets.image_candidates(obj,Path(directory),profile='unused')
        self.assertEqual(queries,['very long image specification','carved relief'])
        self.assertEqual(len(result),1)
        self.assertEqual(result[0]['source_url'],'https://example.test/source')

    def test_style_cannot_be_implemented_by_renaming_boxes(self):
        data=outline_data()
        data['style_bindings']=[{'requirement_id':'carving','component_ids':['base'],
                                'implementation':'Name the box carved flowing relief'}]
        contract=RequestContract(asset_boundary='single_asset',quality_target='prototype',style_requirements=[requirement()])
        with self.assertRaisesRegex(ValueError,'requires freeform geometry'):
            validate_request_contract(Decomposition.model_validate(data),contract)
        data['style_bindings'][0]['component_ids']=['ornament']
        validate_request_contract(Decomposition.model_validate(data),contract)

    def test_style_binding_cannot_drop_or_invent_requirements(self):
        data=outline_data()
        contract=RequestContract(asset_boundary='scene',quality_target='prototype',style_requirements=[requirement()])
        with self.assertRaisesRegex(ValueError,'exactly the frozen'):
            validate_request_contract(Decomposition.model_validate(data),contract)

    def test_external_style_cannot_replace_entire_editable_asset(self):
        data=outline_data();data['objects'][0]['acquisition']=copy.deepcopy(data['objects'][1]['acquisition'])
        data['objects'][0]['rigidity']='rigid'
        with self.assertRaisesRegex(ValueError,'one parametric root'):
            validate_request_contract(Decomposition.model_validate(data),RequestContract(asset_boundary='single_asset',quality_target='prototype'))

    def test_unclear_style_does_not_block_visual_pass(self):
        request={'style_requirements':[requirement()]}
        decision=Decision(verdict='passed',summary='Looks good',semantic_match=4,visual_quality=4,findings=[])
        self.assertTrue(decision_inconsistent(decision,request))
        decision.style_assessments=[__import__('general_vision').StyleAssessment(requirement_id='carving',status='missing',evidence='Only straight surfaces visible')]
        self.assertFalse(decision_inconsistent(decision,request))
        self.assertEqual(decision.style_assessments[0].status,'unclear')
        decision.style_assessments[0].status='realized'
        self.assertFalse(decision_inconsistent(decision,request))

    def test_clearly_missing_user_required_style_still_blocks(self):
        request={'style_requirements':[requirement()]}
        decision=Decision(verdict='needs_revision',summary='The requested carving is absent.',semantic_match=3,
            visual_quality=2,findings=[Finding(severity='blocking',component_ids=['ornament'],
                requirement_ids=['carving'],view='front.png',evidence='Front view shows a flat uncarved panel.',
                correction='Use an ornament with visible carved relief.',repair_target='source')],
            style_assessments=[StyleAssessment(requirement_id='carving',status='missing',
                evidence='The visible front panel is flat.')])
        self.assertFalse(decision_inconsistent(decision,request))

    def test_qualified_internal_part_ids_map_to_exact_local_repair_paths(self):
        from general_vision import normalize_locators,Decision,Finding
        from general_repairs import repair_work_orders,allowed_repair_paths
        from general_planner import PlannerScenePlan
        path=Path(__file__).resolve().parents[1]/'runs/agent_baroque_sweep_20261021_03/planner_output.json'
        plan=PlannerScenePlan.model_validate_json(path.read_text(encoding='utf-8'))
        request={'components':[{'id':c.id,'parts':[{'id':p.id} for p in c.parts]} for c in plan.components]}
        decision=Decision(verdict='needs_revision',summary='Path needs repair',semantic_match=2,visual_quality=2,findings=[
            Finding(severity='blocking',component_ids=['baroque_bookcase'],part_ids=['baroque_bookcase/cornice_serpentine_sweep'],
                requirement_ids=['baroque_curved_silhouette'],view='front.png',evidence='Jagged and floating',
                correction='Smooth the centerline and seat it against the cornice',repair_target='geometry')])
        decision=normalize_locators(decision,request)
        self.assertEqual(decision.findings[0].part_ids,['cornice_serpentine_sweep'])
        work=repair_work_orders(plan,{'findings':[]},{'status':'needs_revision','decision':decision.model_dump()})
        paths=allowed_repair_paths(plan,work['orders'])
        component_index=next(i for i,c in enumerate(plan.components) if c.id=='baroque_bookcase')
        part_index=next(i for i,p in enumerate(plan.components[component_index].parts) if p.id=='cornice_serpentine_sweep')
        self.assertIn(f'/components/{component_index}/parts/{part_index}/path',paths)
        self.assertIn(f'/components/{component_index}/parts/{part_index}/radius',paths)

    def test_style_detail_images_get_style_specific_review_batches(self):
        from general_vision import review_scene_images
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);images=[]
            for name in ('overview','reverse','top','front','detail_style_curve','detail_style_layers'):
                path=root/f'{name}.png';path.write_bytes(b'review fixture');images.append(str(path))
            request={'style_requirements':[
                {'id':'curve','feature':'Curved rails','visible_evidence':'A clearly visible curved rail','geometry_need':'swept_path'},
                {'id':'layers','feature':'Layered mouldings','visible_evidence':'Several visible stepped layers','geometry_need':'regular_solids'}],
                'style_bindings':[{'requirement_id':'curve','component_ids':['case'],'part_ids':['case/rail']},
                                  {'requirement_id':'layers','component_ids':['case'],'part_ids':['case/crown']}],
                'components':[{'id':'case','parts':[]}]}
            calls=[]
            def reviewer(paths,prompt,profile,output,**kwargs):
                calls.append((paths,prompt,kwargs));return {'accepted':True,'status':'passed','decision':{'findings':[]}}
            result=review_scene_images(images,request,'unused',root/'qa.json',reviewer=reviewer)
            self.assertTrue(result['accepted'])
            local=[call for call in calls if any(Path(path).stem.startswith('detail_style_') for path in call[0])]
            self.assertEqual(len(local),1)
            self.assertEqual({item['id'] for item in local[0][1]['style_requirements']},{'curve','layers'})

    def test_normal_entrypoint_replans_with_frozen_request_and_feedback(self):
        calls=[]
        frozen={'asset_boundary':'single_asset','quality_target':'realistic','contents':[],'style_requirements':[requirement()]}
        def attempt(args):
            calls.append(args)
            if len(calls)>1:
                self.assertEqual(args.resume_runtime,calls[0].out/'agent_runtime')
            write_json(args.out/'agent_runtime/frozen_request_contract.json',frozen)
            if len(calls)==1:return {'status':'rejected','accepted':False,'visual':{'status':'needs_revision',
                'decision':{'findings':[{'severity':'blocking','repair_target':'decomposition','evidence':'Missing relief'}]}}}
            self.assertEqual(args.frozen_contract,frozen)
            self.assertIn('Missing relief',json.dumps(args.replan_feedback))
            self.assertEqual(args.brief,calls[0].brief)
            self.assertEqual(args.asset_cache,calls[0].asset_cache)
            return {'status':'accepted','accepted':True}
        with tempfile.TemporaryDirectory() as directory:
            args=SimpleNamespace(out=Path(directory)/'run',resume_runtime=None,planner='llm',plan_only=False,brief='Make a styled asset',max_replans=1)
            result=run(args,attempt_runner=attempt)
            self.assertTrue(result['accepted']);self.assertEqual(len(calls),2)
            self.assertTrue((args.out/'attempt_report.json').is_file())

    def test_later_replan_preserves_original_visual_evidence(self):
        calls=[]
        frozen={'asset_boundary':'single_asset','quality_target':'realistic','contents':[],
                'style_requirements':[requirement()]}
        def attempt(args):
            calls.append(args)
            write_json(args.out/'agent_runtime/frozen_request_contract.json',frozen)
            write_json(args.out/'agent_runtime/frozen_decomposition.json',{'plan':{
                'objects':[{'id':'base','semantic':'editable root','parent':None,
                            'acquisition':{'strategy':'parametric'},'required_features':['keep editable']}],
                'style_bindings':[]}})
            if len(calls)==1:
                return {'status':'rejected','accepted':False,'visual':{'status':'needs_revision','decision':{
                    'findings':[{'severity':'blocking','repair_target':'geometry',
                                 'requirement_ids':['carving'],'evidence':'The sweep is visibly only a thin tube.'}]}}}
            if len(calls)==2:
                return {'status':'rejected','accepted':False,'error':'recipe failed after local repairs'}
            visual=args.replan_feedback['visual']
            self.assertEqual(visual['findings'][0]['evidence'],'The sweep is visibly only a thin tube.')
            self.assertEqual(args.replan_feedback['error'],'recipe failed after local repairs')
            return {'status':'accepted','accepted':True}
        with tempfile.TemporaryDirectory() as directory:
            args=SimpleNamespace(out=Path(directory)/'run',resume_runtime=None,planner='llm',plan_only=False,
                                 max_replans=2,brief='Make one editable styled object')
            result=run(args,attempt_runner=attempt)
        self.assertTrue(result['accepted'],result.get('error'))
        self.assertEqual(len(calls),3)
        self.assertIn('previous_decomposition',calls[2].replan_feedback)

    def test_local_geometry_repair_runs_before_mixed_decomposition_escalation(self):
        from scene_execution import execute_with_repairs
        from general_planner import PlannerScenePlan,to_general_plan
        from general_vision import Finding
        plan_path=Path(__file__).resolve().parents[1]/'runs/agent_baroque_sweep_20261021_03/planner_output.json'
        plan=PlannerScenePlan.model_validate_json(plan_path.read_text(encoding='utf-8'))
        findings=[Finding(severity='blocking',component_ids=['baroque_bookcase'],
            part_ids=['cornice_serpentine_sweep'],requirement_ids=['baroque_curved_silhouette'],
            view='front.png',evidence='Jagged sweep needs smoothing',correction='Smooth it',repair_target='geometry'),
            Finding(severity='blocking',component_ids=['baroque_bookcase'],part_ids=['left_side_panel'],
            requirement_ids=['baroque_curved_silhouette'],view='overview.png',evidence='Rigid side panel remains straight',
            correction='Replan profile',repair_target='decomposition')]
        calls=[]
        first={'accepted':False,'geometry':{'status':'passed','findings':[]},
               'visual':{'status':'needs_revision','accepted':False,'decision':{'findings':[f.model_dump() for f in findings]}},
               'directory':'pending'}
        second={'accepted':True,'geometry':{'status':'passed','findings':[]},
                'visual':{'status':'passed','accepted':True,'decision':{'findings':[]}},'directory':'pending'}
        def evaluator(candidate,assets,directory,*_):
            result=first if not calls else second
            result={**result,'directory':str(directory)}
            return result
        def repairer(*args):
            calls.append(args)
            return plan.model_copy(deep=True),{'status':'candidate_validated_pending_render'}
        # This dispatch test uses a historical plan and an injected geometry
        # evaluator. Real scene metrology is covered by separate integration
        # tests; the historical envelope underfill must not redefine the fixture.
        with tempfile.TemporaryDirectory() as directory, patch('scene_execution.to_general_plan',
                side_effect=lambda value,**kwargs:to_general_plan(value,check_geometry=False,**kwargs)):
            result=execute_with_repairs(plan,{},directory,'brief','profile','vision',max_repairs=1,
                                        evaluator=evaluator,repairer=repairer)
        self.assertTrue(result['accepted'])
        self.assertEqual(len(calls),1)

    def test_repeated_style_failure_after_fresh_render_escalates_to_source_planning(self):
        from scene_execution import execute_with_repairs
        from general_planner import PlannerScenePlan,to_general_plan
        plan_path=Path(__file__).resolve().parents[1]/'runs/agent_baroque_sweep_20261021_03/planner_output.json'
        plan=PlannerScenePlan.model_validate_json(plan_path.read_text(encoding='utf-8'))
        calls=[]
        def evaluator(candidate,assets,directory,*_):
            return {'accepted':False,'geometry':{'status':'passed','findings':[]},
                'visual':{'status':'needs_revision','accepted':False,'decision':{
                    'findings':[{'severity':'blocking','component_ids':['baroque_bookcase'],
                        'part_ids':['cornice_serpentine_sweep'],'requirement_ids':['baroque_curved_silhouette'],
                        'view':'front.png','evidence':'Circular tube remains visually detached',
                        'correction':'A local tube path cannot form the intended broad sculpted profile.',
                        'repair_target':'geometry'}],
                    'style_assessments':[{'requirement_id':'baroque_curved_silhouette','status':'missing',
                                          'evidence':'The crown remains straight and the tube reads as a loose rod.'}]}},
                'directory':str(directory)}
        def repairer(current,*_):
            calls.append(1)
            return current.model_copy(deep=True),{'status':'candidate_validated_pending_render'}
        with tempfile.TemporaryDirectory() as directory, patch('scene_execution.to_general_plan',
                side_effect=lambda value,**kwargs:to_general_plan(value,check_geometry=False,**kwargs)):
            result=execute_with_repairs(plan,{},directory,'brief','profile','vision',max_repairs=2,
                                        evaluator=evaluator,repairer=repairer)
        self.assertEqual(len(calls),1)
        self.assertTrue(result['replan_reason'].startswith('repeated_style_requirement_failure_after_local_render:'))
        self.assertEqual(len(result['iterations']),2)

    def test_visual_service_outage_does_not_trigger_replanning(self):
        calls=[]
        def attempt(args):
            calls.append(args)
            write_json(args.out/'agent_runtime/frozen_request_contract.json',{'asset_boundary':'scene','quality_target':'prototype'})
            return {'status':'rejected','accepted':False,'visual':{'status':'unavailable'}}
        with tempfile.TemporaryDirectory() as directory:
            args=SimpleNamespace(out=Path(directory)/'run',resume_runtime=None,planner='llm',plan_only=False,max_replans=2)
            result=run(args,attempt_runner=attempt)
        self.assertFalse(result['accepted']);self.assertEqual(len(calls),1)

    def test_model_service_error_with_geometry_findings_stops_without_replanning(self):
        calls=[]
        finding={'severity':'blocking','code':'blocked_layout','evidence':'The sofa blocks the required route.'}
        def attempt(args):
            calls.append(args)
            write_json(args.out/'agent_runtime/frozen_request_contract.json',{'asset_boundary':'scene','quality_target':'prototype'})
            return {'status':'rejected','accepted':False,'error_type':'APIStatusError',
                'error':'Error code: 402 - Insufficient Balance',
                'geometry':{'status':'needs_revision','findings':[finding]}}
        with tempfile.TemporaryDirectory() as directory:
            args=SimpleNamespace(out=Path(directory)/'run',resume_runtime=None,planner='llm',plan_only=False)
            result=run(args,attempt_runner=attempt)
        self.assertEqual(len(calls),1)
        self.assertEqual(result['stop_reason'],'model_service_unavailable')
        self.assertTrue(result['preserved_validation_findings'])
        self.assertEqual(result['geometry']['findings'],[finding])

    def test_changed_candidate_hash_does_not_hide_identical_replanning_failure(self):
        calls=[]
        def attempt(args):
            calls.append(args)
            args.out.mkdir(parents=True,exist_ok=True)
            write_json(args.out/'agent_runtime/frozen_request_contract.json',
                       {'asset_boundary':'scene','quality_target':'prototype'})
            write_json(args.out/'planner_output.json',{'candidate_revision':len(calls)})
            return {'status':'rejected','accepted':False,'error_type':'ValueError',
                    'error':'decomposition stopped after repeated failed state'}
        with tempfile.TemporaryDirectory() as directory:
            args=SimpleNamespace(out=Path(directory)/'run',resume_runtime=None,planner='llm',plan_only=False)
            result=run(args,attempt_runner=attempt)
            first=json.loads((calls[0].out/'replan_failure_state.json').read_text(encoding='utf-8'))
            second=json.loads((calls[1].out/'replan_failure_state.json').read_text(encoding='utf-8'))
        self.assertEqual(len(calls),2)
        self.assertEqual(result['stop_reason'],'repeated_replanning_failure')
        self.assertNotEqual(first['candidate_artifacts'],second['candidate_artifacts'])


if __name__=='__main__':unittest.main()
