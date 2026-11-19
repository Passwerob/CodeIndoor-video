"""Source acquisition prunes proven mismatches without claiming visual success."""
import json
import hashlib
from io import BytesIO
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
import urllib.error
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import general_assets as assets
from run_trellis1_component import face_edge_component_summary


def ornament():
    return SimpleNamespace(id='local_ornament',semantic='Local carved scroll ornament',source=SimpleNamespace(
        query='carved wood scroll ornament',reference_image=None,required_features=['curved local carving'],
        reference_query='single isolated carved scroll ornament with flat mounting back',
        reference_requirements=['complete isolated carving']))


class AssetSearchTests(unittest.TestCase):
    def test_replay_reuses_source_candidates_without_fabricating_blender_receipts(self):
        import run_general_scene
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);prepared=root/'prepared';prepared.mkdir()
            record={'path':'unused.glb','accepted':True,'sha256':'abc'}
            (prepared/'component_resolutions.json').write_text(json.dumps([
                {'component_id':'asset','record':record}]),encoding='utf-8')
            reused=run_general_scene.load_prepared_asset_records(prepared)
            self.assertEqual(reused['asset'],record)
            self.assertFalse((prepared/'build_receipts.json').exists())

    def test_compiled_candidate_reuse_prefers_hashable_build_receipts(self):
        import run_general_scene
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);record={'path':'mesh.glb','accepted':True}
            (root/'build_receipts.json').write_text(json.dumps({'asset':{'asset':record}}),encoding='utf-8')
            self.assertEqual(run_general_scene.load_prepared_asset_records(root),{'asset':record})

    def test_prepared_source_reuse_never_promotes_a_rejection_without_agent_pass(self):
        import numpy as np
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);mesh=root/'normalized.glb';mesh.write_bytes(b'mesh')
            renderer=Path(assets.__file__).with_name('general_render.py')
            (root/'render_manifest.json').write_text(json.dumps({'images':['source.png'],
                'renderer_source_sha256':hashlib.sha256(renderer.read_bytes()).hexdigest()}))
            prior={'path':str(mesh),'accepted':False,'visual_status':'needs_revision'}
            obj=ornament();obj.source.material_requirements=['textured wood']
            def rejected(*args,**kwargs):return {'accepted':False,'status':'needs_revision'}
            with patch.object(assets,'object_size',return_value=np.array([1,1,1])):
                result=assets.reuse_prepared_asset(obj,None,prior,root/'new','profile','realistic',
                    'A realistic ornament',reviewer=rejected)
            self.assertFalse(result['accepted'])
            self.assertTrue(result['inspection_only'])
            self.assertEqual(prior,{'path':str(mesh),'accepted':False,'visual_status':'needs_revision'})
            self.assertTrue((root/'new/asset.json').is_file())

    def test_provider_integrity_retry_bypasses_stale_cdn_and_reuses_valid_file(self):
        expected=b'correct geometry buffer'
        record={'url':'https://assets.test/mesh.bin','md5':hashlib.md5(expected).hexdigest(),'size':len(expected)}
        urls=[]
        def download(url,path):
            urls.append(url)
            Path(path).write_bytes(b'stale bytes' if len(urls)==1 else expected)
        with tempfile.TemporaryDirectory() as directory,patch.object(assets,'fetch',side_effect=download):
            path=Path(directory)/'mesh.bin'
            path.write_bytes(b'previous interrupted download')
            assets.verified_provider_file(record,path)
            self.assertEqual(path.read_bytes(),expected)
            self.assertIn('finescene_integrity=',urls[1])
            assets.verified_provider_file(record,path)
            self.assertEqual(len(urls),2)
            self.assertFalse(path.with_name(path.name+'.download').exists())

    def test_provider_integrity_failure_never_commits_partial_download(self):
        record={'url':'https://assets.test/mesh.bin','md5':hashlib.md5(b'expected').hexdigest()}
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'mesh.bin'
            def download(_url,target):Path(target).write_bytes(b'corrupt')
            with patch.object(assets,'fetch',side_effect=download):
                with self.assertRaisesRegex(ValueError,'mismatch'):
                    assets.verified_provider_file(record,path)
            self.assertFalse(path.exists())
            self.assertFalse(path.with_name(path.name+'.download').exists())

    def test_source_feedback_carries_actual_visual_findings_into_replanning(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'visual.json'
            finding={'severity':'blocking','evidence':'A clamp cannot rest on the tabletop.'}
            assets.save(path,{'status':'needs_revision','decision':{
                'summary':'Wrong mounting interface.','findings':[finding]}})
            feedback=assets.source_visual_feedback({'visual_qa':str(path)})
        self.assertEqual(feedback['status'],'needs_revision')
        self.assertEqual(feedback['findings'],[finding])
        self.assertEqual(feedback['summary'],'Wrong mounting interface.')

    def test_source_planning_proposal_cannot_override_independent_visual_rejection(self):
        obj=ornament();obj.source.front_rotation_deg=0;obj.source.material_requirements=['dark wood']
        proposal=assets.SourceReconciliation(action='revise_inferred_specification',semantic='Carved warm wood ornament',
            required_features=['curved carving'],material_requirements=['warm wood'],
            reason='Observed warm wood is compatible with the original user request.')
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory)
            def render(_command,_log):
                assets.save(folder/'render_manifest.json',{'images':['front.png','side.png','back.png']})
                assets.save(folder/'asset.json',{'path':'normalized.glb'})
            with patch.object(assets,'object_size',return_value=__import__('numpy').array([.2,.1,.3])), \
                 patch.object(assets,'run_logged',side_effect=render), \
                 patch.object(assets,'reconcile_source_specification',return_value=proposal), \
                 patch.object(assets,'review_images',return_value={'accepted':False,'status':'needs_revision'}) as judge:
                result=assets.prepare_candidate(obj,object(),{'path':'source.glb'},folder,'unused','realistic',
                    original_request='A refined warm wood carving')
        self.assertFalse(result['accepted'])
        self.assertNotIn('reconciled_specification',result)
        self.assertEqual(judge.call_count,2)
        self.assertEqual(judge.call_args.args[1]['original_user_request'],'A refined warm wood carving')

    def test_candidate_judge_receives_original_user_requirements(self):
        obj=ornament();obj.source.front_rotation_deg=0
        obj.source.material_requirements=['Agent proposed dark walnut']
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory)
            def render(_command,_log):
                assets.save(folder/'render_manifest.json',{'images':['front.png','side.png','back.png']})
                assets.save(folder/'asset.json',{'path':'normalized.glb'})
            with patch.object(assets,'object_size',return_value=__import__('numpy').array([.2,.1,.3])), \
                 patch.object(assets,'run_logged',side_effect=render), \
                 patch.object(assets,'review_images',return_value={'accepted':True}) as judge:
                result=assets.prepare_candidate(obj,object(),{'path':'source.glb'},folder,'unused','realistic',
                    original_request='A refined carved ornament in warm aged wood')
            request=judge.call_args.args[1]
        self.assertTrue(result['accepted'])
        self.assertEqual(request['original_user_request'],'A refined carved ornament in warm aged wood')
        self.assertEqual(request['material_requirements'],['Agent proposed dark walnut'])
        self.assertIn('Original user request is authoritative',request['requirement_authority'])

    def test_dense_mesh_topology_summary_skips_before_adjacency_allocation(self):
        graph=SimpleNamespace(connected_components=lambda *_args,**_kwargs:
                              self.fail('dense meshes must skip this optional allocation'))
        mesh=SimpleNamespace(faces=[None]*500_001)
        count,fraction,status=face_edge_component_summary(mesh,graph,SimpleNamespace())
        self.assertIsNone(count)
        self.assertIsNone(fraction)
        self.assertEqual(status,'skipped_face_count_limit')

    def test_small_mesh_topology_summary_records_memory_limited_skip(self):
        def fail(*_args,**_kwargs):raise MemoryError('synthetic memory limit')
        graph=SimpleNamespace(connected_components=fail)
        mesh=SimpleNamespace(faces=[None]*10,face_adjacency=[])
        count,fraction,status=face_edge_component_summary(mesh,graph,SimpleNamespace(arange=lambda size:list(range(size))))
        self.assertIsNone(count)
        self.assertIsNone(fraction)
        self.assertEqual(status,'skipped_memory_limit')

    def test_fetch_retries_transient_catalog_rate_limit_once(self):
        url='https://commons.wikimedia.org/w/api.php'
        limited=urllib.error.HTTPError(url,429,'Too Many Requests',{'Retry-After':'0'},None)
        responses=[limited,BytesIO(b'{"ok":true}')]
        with patch.object(assets.urllib.request,'urlopen',side_effect=responses) as open_url, \
             patch.object(assets.time,'sleep') as sleep:
            self.assertEqual(assets.fetch(url),{'ok':True})
        self.assertEqual(open_url.call_count,2)
        sleep.assert_called_once()

    def test_tokens_do_not_match_words_inside_unrelated_catalog_names(self):
        rows=[dict(asset_id='table',label='tall side table'),
              dict(asset_id='furniture',label='wooden cabinet'),
              dict(asset_id='ornament',label='carved scroll')]
        self.assertEqual([r['asset_id'] for r in assets.rank('all scroll',rows)],['ornament'])

    def test_metadata_rejects_mismatch_keeps_uncertain_for_visual_qa(self):
        rows=[dict(asset_id='table',label='side table'),dict(asset_id='carving',label='carved fragment')]
        async def complete(stage,instructions,payload):
            self.assertEqual(payload['context']['candidates'],rows)
            return {'assessments':[dict(asset_id='table',action='reject',reason='Whole table is not a local ornament.'),
                dict(asset_id='carving',action='inspect',reason='Plausible fragment; inspect geometry and style.') ]}
        with tempfile.TemporaryDirectory() as directory:
            selected=assets.metadata_candidates(ornament(),rows,'unused',Path(directory),complete=complete)
            report=json.loads((Path(directory)/'selection.json').read_text())
        self.assertEqual(selected,[rows[1]])
        self.assertEqual(report['status'],'screened_not_visually_accepted')
        self.assertNotIn('accepted',report)

    def test_invented_or_omitted_ids_cannot_silently_prune_catalog(self):
        rows=[dict(asset_id='actual',label='unknown fragment')]
        async def complete(*_):
            return {'assessments':[dict(asset_id='invented',action='reject',reason='No such candidate was supplied.')]}
        with tempfile.TemporaryDirectory() as directory:
            selected=assets.metadata_candidates(ornament(),rows,'unused',Path(directory),complete=complete)
            report=json.loads((Path(directory)/'selection.json').read_text())
        self.assertEqual(selected,rows)
        self.assertEqual(report['fallback'],'bounded_visual_inspection')

    def test_rejected_remote_metadata_is_never_downloaded(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(assets,'fetch',return_value={
            'table':{'name':'Wood table','tags':['wood']}}) as fetch:
            selected=assets.search_polyhaven('wood carving',Path(directory),selector=lambda rows:[])
        self.assertEqual(selected,[])
        self.assertEqual(fetch.call_count,1)  # catalog only, no files/download request

    def test_long_reference_queries_get_same_stage_repair(self):
        calls=[]
        async def complete(stage,instructions,payload):
            calls.append((stage,payload))
            if len(calls)==1:
                return {'queries':['carved gilt walnut pediment scrollwork applique cornice']}
            return {'queries':['carved pediment','baroque scroll ornament']}
        with tempfile.TemporaryDirectory() as directory:
            result=assets.refine_reference_queries(ornament(),'unused',Path(directory),complete=complete,
                search_feedback={'searched_queries':['carved pediment'],
                    'attempts':[{'query':'carved pediment','result':'metadata_context_mismatch'}]})
        self.assertEqual([call[0] for call in calls],['search_queries_0','search_queries_1'])
        self.assertEqual(result,['baroque scroll ornament'])
        self.assertEqual(calls[0][1]['context']['previous_search_attempts'][0]['result'],
                         'metadata_context_mismatch')

    def test_empty_reference_search_runs_one_fresh_evidence_driven_pass(self):
        from general_planner import PlannerScenePlan,to_general_plan
        import copy
        data=json.loads((Path(__file__).resolve().parents[1]/'examples/general_metrology_fixture.json').read_text())
        data['components'][1]['parts']=[]
        data['components'][1]['source']=dict(mode='generate',reason='Create a custom visible relief only after source routing.',
            retrieval_query='',reference_query='isolated carved walnut crest',reference_image=None,
            trellis_required=True,trellis_reason='The layered carved silhouette is a custom appearance.',
            generation_trigger='custom_appearance',reference_requirements=['isolated crest with a complete silhouette'],
            quality_target='prototype')
        plan,_=to_general_plan(PlannerScenePlan.model_validate(data))
        obj=copy.deepcopy(plan.objects[1])
        calls=[]
        def no_candidates(_obj,folder,_offline=False,**kwargs):
            folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
            calls.append(kwargs)
            if kwargs['attempt']==0:
                assets.save(folder/'reference_search.json',{
                    'original_reference_query':'isolated carved walnut crest',
                    'attempts':[{'query':'carved acanthus crest','results':3},
                                {'query':'carved acanthus crest','page_title':'File:crest_design.pdf',
                                 'result':'metadata_context_mismatch','fallback':True}],
                    'searched_queries':['carved acanthus crest','baroque wood carving'],
                    'candidates':[]})
            return []
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(assets,'image_candidates',side_effect=no_candidates):
            with self.assertRaisesRegex(RuntimeError,'Unresolved'):
                assets.resolve_component(obj,plan,Path(directory),[],'profile','prototype')
            evidence=json.loads(next(Path(directory).rglob('resolution.json')).read_text())
        self.assertEqual([call['attempt'] for call in calls],[0,1])
        self.assertEqual(set(calls[1]['exclude_queries']),
                         {'carved acanthus crest','baroque wood carving'})
        self.assertEqual(calls[1]['search_feedback']['attempts'][1]['result'],
                         'metadata_context_mismatch')
        first=evidence['errors'][0]['search_evidence']
        self.assertEqual(first['candidate_count'],0)
        self.assertEqual([item['attempt'] for item in evidence['errors']
                          if item['stage']=='reference_search'],[0,1])

    def test_empty_commons_search_uses_cleveland_and_keeps_metadata_and_license(self):
        obj=ornament()
        def artwork(catalog_id,title,url):
            return {'id':catalog_id,'title':title,'url':f'https://clevelandart.org/art/{catalog_id}',
                    'share_license_status':'CC0','technique':'Carved wood',
                    'tombstone':'Open Access','images':{'web':{'url':url}}}
        def fake_fetch(url,path=None,limit=None):
            if path:
                Path(path).write_bytes(b'\x89PNG\r\n\x1a\n'+b'0'*32)
                return str(path)
            if 'commons.wikimedia.org' in url:
                return {'query':{'pages':{}}}
            if 'openaccess-api.clevelandart.org' in url:
                return {'data':[
                    artwork(1001,'Acanthus bookcase','https://images.test/bookcase.jpg'),
                    artwork(1002,'Acanthus wood applique','https://images.test/applique.jpg')]}
            if 'api.artic.edu/api/v1/artworks/search' in url:return {'data':[],'pagination':{'total':0}}
            raise AssertionError(f'unexpected catalog URL: {url}')
        with tempfile.TemporaryDirectory() as directory,patch.object(assets,'fetch',side_effect=fake_fetch), \
             patch.object(assets,'refine_reference_queries',return_value=[]):
            result=assets.image_candidates(obj,Path(directory),profile='unused')
            self.assertTrue(Path(result[0]['path']).is_file())
            evidence=json.loads((Path(directory)/'reference_search.json').read_text(encoding='utf-8'))
            museum=json.loads((Path(directory)/'cleveland_open_access'/'cleveland_search.json').read_text(encoding='utf-8'))
        self.assertEqual(len(result),1)
        self.assertEqual(result[0]['provider'],'Cleveland Museum of Art Open Access')
        self.assertEqual(result[0]['catalog_id'],'1002')
        self.assertEqual(result[0]['license'],'CC0')
        self.assertEqual(result[0]['source_url'],'https://clevelandart.org/art/1002')
        self.assertTrue(any(item.get('result')=='metadata_context_mismatch'
                            for item in museum['attempts']))
        self.assertEqual(evidence['candidates'][0]['catalog_id'],'1002')
        self.assertTrue(any(item.get('provider')=='Cleveland Museum of Art Open Access'
                            for item in evidence['attempts']))
        self.assertGreaterEqual(len(museum['searched_queries']),2)

    def test_reference_feedback_preserves_museum_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            assets.save(Path(directory)/'reference_search.json',{
                'original_reference_query':'carved crest',
                'searched_queries':['carving from an overmantel'],
                'attempts':[{'query':'carving from an overmantel','provider':'Cleveland Museum of Art Open Access',
                             'catalog_id':'394617','source_url':'https://clevelandart.org/art/1943.654.h',
                             'result':'metadata_context_mismatch'}],
                'candidates':[]})
            feedback=assets._reference_search_feedback(directory)
        self.assertEqual(feedback['attempts'][0]['provider'],'Cleveland Museum of Art Open Access')
        self.assertEqual(feedback['attempts'][0]['catalog_id'],'394617')
        self.assertEqual(feedback['attempts'][0]['source_url'],'https://clevelandart.org/art/1943.654.h')

    def test_cleveland_query_budget_keeps_candidates_from_distinct_terms(self):
        obj=ornament();catalog_queries=[]
        def fake_fetch(url,path=None,limit=None):
            if path:
                Path(path).write_bytes(b'\x89PNG\r\n\x1a\n'+b'0'*32)
                return str(path)
            from urllib.parse import urlparse,parse_qs
            query=parse_qs(urlparse(url).query).get('q',[''])[0]
            catalog_queries.append(query)
            return {'data':[{
                'id':f'{catalog_queries.__len__()}-{i}',
                'title':f'Carved acanthus scroll relief {i}',
                'url':f'https://clevelandart.org/art/{catalog_queries.__len__()}-{i}',
                'share_license_status':'CC0','technique':'Carved wood',
                'images':{'web':{'url':f'https://images.test/{catalog_queries.__len__()}-{i}.jpg'}}
            } for i in range(8)]}
        with tempfile.TemporaryDirectory() as directory,patch.object(assets,'fetch',side_effect=fake_fetch):
            result=assets.search_cleveland_references(obj,Path(directory))
        self.assertEqual(len(result),6)
        self.assertEqual(len(set(item['query'] for item in result)),3)
        self.assertEqual(len(catalog_queries),3)

    def test_artic_only_returns_public_domain_image_candidates_with_provenance(self):
        obj=ornament()
        def fake_fetch(url,path=None,limit=None):
            if path:
                Path(path).write_bytes(b'\x89PNG\r\n\x1a\n'+b'0'*32)
                return str(path)
            if 'api.artic.edu/api/v1/artworks/search' in url:
                return {'pagination':{'total':100},'data':[
                    {'id':1,'title':'Carved Scroll Ornament','artist_title':'Unknown','medium_display':'Carved wood',
                     'date_display':'1700','department_title':'European Decorative Arts','artwork_type_title':'Ornament',
                     'classification_titles':['wood carving'],'is_public_domain':True,'image_id':'scroll-1',
                     'api_link':'https://api.artic.edu/api/v1/artworks/1','credit_line':'Open Access'},
                    {'id':2,'title':'Carved Scroll Ornament','medium_display':'Wood','is_public_domain':False,
                     'image_id':'private-2'},
                    {'id':3,'title':'Portrait','medium_display':'Oil on canvas','is_public_domain':True,
                     'image_id':'portrait-3'}]}
            raise AssertionError(f'unexpected Art Institute image URL: {url}')
        with tempfile.TemporaryDirectory() as directory,patch.object(assets,'fetch',side_effect=fake_fetch):
            result=assets.search_artic_references(obj,Path(directory))
            evidence=json.loads((Path(directory)/'artic_search.json').read_text(encoding='utf-8'))
            self.assertTrue(Path(result[0]['path']).is_file())
        self.assertEqual(len(result),1)
        self.assertEqual(result[0]['provider'],'Art Institute of Chicago Open Access')
        self.assertEqual(result[0]['catalog_id'],'1')
        self.assertEqual(result[0]['license'],'CC0 1.0 (public-domain artwork)')
        self.assertEqual(result[0]['source_url'],'https://www.artic.edu/artworks/1')
        self.assertEqual(result[0]['metadata']['medium_display'],'Carved wood')
        self.assertTrue(any(item.get('result')=='not_public_domain_or_no_image'
                            for item in evidence['attempts']))

    def test_artic_rejects_broad_urn_query_hits_that_are_not_urns(self):
        target=SimpleNamespace(semantic='Central carved urn finial',source=SimpleNamespace(
            reference_query='isolated urn finial with acanthus',required_features=['vertical urn finial']))
        calls=[]
        def fake_fetch(url,path=None,limit=None):
            if path:raise AssertionError('Metadata mismatch must be rejected before downloading the image')
            calls.append(url)
            return {'data':[{'id':7,'title':'Lamp Shade','artist_title':'Tiffany Glass Company',
                'medium_display':'Leaded glass','department_title':'Arts of the Americas',
                'artwork_type_title':'Glass','classification_titles':['decorative arts'],
                'is_public_domain':True,'image_id':'lamp-7'}], 'pagination':{'total':1}}
        with tempfile.TemporaryDirectory() as directory,patch.object(assets,'fetch',side_effect=fake_fetch):
            result=assets.search_artic_references(target,Path(directory),attempt=1)
            evidence=json.loads((Path(directory)/'artic_search.json').read_text(encoding='utf-8'))
        self.assertEqual(result,[])
        self.assertEqual(len(calls),3)
        self.assertTrue(any(item.get('result')=='metadata_shape_mismatch'
                            for item in evidence['attempts']))

    def test_alternate_search_round_keeps_three_providers_inside_six_candidate_budget(self):
        obj=ornament()
        def commons_fetch(url,path=None,limit=None):
            if path:
                Path(path).write_bytes(b'\x89PNG\r\n\x1a\n'+b'0'*32)
                return str(path)
            if 'commons.wikimedia.org' in url:
                return {'query':{'pages':{'1':{'title':'File:Scroll carving.jpg','imageinfo':[{
                    'thumburl':'https://images.test/commons.jpg',
                    'descriptionurl':'https://commons.wikimedia.org/wiki/File:Scroll_carving.jpg',
                    'extmetadata':{'LicenseShortName':{'value':'CC BY-SA 4.0'}}}]}}}}
            raise AssertionError(f'unexpected URL: {url}')
        museum={'path':'museum.png','query':'carved wood','provider':'Cleveland Museum of Art Open Access',
                'catalog_id':'museum-1','source_url':'https://clevelandart.org/art/museum-1'}
        artic={'path':'artic.png','query':'carved wood','provider':'Art Institute of Chicago Open Access',
               'catalog_id':'artic-1','source_url':'https://www.artic.edu/artworks/1'}
        with tempfile.TemporaryDirectory() as directory,patch.object(assets,'fetch',side_effect=commons_fetch), \
             patch.object(assets,'search_cleveland_references',return_value=[museum]), \
             patch.object(assets,'search_artic_references',return_value=[artic]):
            result=assets.image_candidates(obj,Path(directory),profile=None,
                feedback=[{'status':'needs_revision'}],attempt=1)
        self.assertEqual([item['provider'] for item in result],[
            'Art Institute of Chicago Open Access','Cleveland Museum of Art Open Access','Wikimedia Commons'])
        self.assertLessEqual(len(result),6)

    def test_reference_judge_request_focuses_on_visible_geometry(self):
        target=SimpleNamespace(semantic='Acanthus fan crest ornament',source=SimpleNamespace(
            required_features=['one symmetric acanthus fan','flat underside mounting base',
                                'aged gilt highlights on raised leaf tips'],
            reference_requirements=['one complete isolated carved wood object',
                                    'dark walnut with aged gilt high points',
                                    'proportions around 0.68 m wide by 0.32 m high']))
        request=assets.reference_geometry_request(target)
        self.assertEqual(request['required_features'],['one symmetric acanthus fan','raised leaf tips'])
        self.assertEqual(request['reference_requirements'],['one complete isolated carved object'])
        self.assertIn('hidden faces',request['generation_geometry_guidance'])

    def test_reference_judge_keeps_shape_when_removing_material_and_finish_constraints(self):
        target=SimpleNamespace(semantic='Central carved urn finial',source=SimpleNamespace(
            required_features=['one complete isolated carved wood urn finial with acanthus leaf and C-scroll relief',
                               'dark walnut with aged gilt high points'],
            reference_requirements=['single vertical urn finial with acanthus leaf and C-scroll carved relief',
                                    'one complete isolated carved wood object',
                                    'dark walnut with aged gilt high points',
                                    'flat underside mounting base visible',
                                    'proportions around 0.18 m high']))
        request=assets.reference_geometry_request(target)
        self.assertIn('urn finial',request['required_features'][0])
        self.assertIn('acanthus leaf',request['reference_requirements'][0])
        self.assertNotIn('wood',json.dumps(request,ensure_ascii=False).casefold())
        self.assertNotIn('gilt',json.dumps(request,ensure_ascii=False).casefold())
        self.assertEqual(request['reference_requirements'][1:2],['one complete isolated carved object'])

    def test_configured_reference_generator_still_requires_visual_judge_before_trellis(self):
        from general_planner import PlannerScenePlan,to_general_plan
        from general_assets import ComponentResolutionError
        data=json.loads((Path(__file__).resolve().parents[1]/'examples/general_metrology_fixture.json').read_text())
        data['components'][1]['parts']=[]
        data['components'][1]['source']=dict(mode='generate',reason='Custom surface form needs image-conditioned geometry.',
            retrieval_query='',reference_query='isolated machined cylinder',reference_image=None,
            trellis_required=True,trellis_reason='Use only a visually approved reference for TRELLIS.1.',
            generation_trigger='custom_appearance',reference_requirements=['one isolated complete cylinder'],
            quality_target='prototype')
        plan,_=to_general_plan(PlannerScenePlan.model_validate(data))
        obj=next(item for item in plan.objects if item.id.startswith('sample'))
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);adapter=root/'reference_adapter.py';adapter.write_text('# test adapter')
            calls=[];judge_calls=[]
            def run_adapter(command,**kwargs):
                calls.append(command)
                request_path=Path(command[command.index('--request')+1])
                output=Path(command[command.index('--output')+1])
                request=json.loads(request_path.read_text(encoding='utf-8'))
                self.assertEqual(request['schema'],'finescene.reference_generator_request.v1')
                self.assertEqual(request['component_id'],obj.id)
                output.write_bytes(b'\x89PNG\r\n\x1a\n'+b'fake-test-png')
                return SimpleNamespace(returncode=0,stdout='candidate written')
            def reject_reference(images,request,profile_path,output,**kwargs):
                judge_calls.append({'images':images,'request':request,'scope':kwargs.get('scope')})
                return {'accepted':False,'status':'needs_revision','decision':{
                    'summary':'Candidate does not match the requested visible component.', 'findings':[]}}
            with patch.object(assets.subprocess,'run',side_effect=run_adapter), \
                 patch.object(assets,'review_images',side_effect=reject_reference), \
                 patch.object(assets,'run_logged',side_effect=AssertionError('TRELLIS must not run after failed reference QA')):
                with self.assertRaises(ComponentResolutionError):
                    assets.resolve_component(obj,plan,root/'cache',[],'unused','prototype',
                        offline=True,reference_generator=adapter)
            request=json.loads(next(root.rglob('reference_generator_request_00.json')).read_text(encoding='utf-8'))
            search=json.loads(next(root.rglob('reference_search.json')).read_text(encoding='utf-8'))
            generator_log=next(root.rglob('reference_generator_00.log')).read_text(encoding='utf-8')
        self.assertEqual(calls[0][0],assets.sys.executable)
        self.assertIn('--request',calls[0]);self.assertIn('--output',calls[0])
        self.assertEqual(request['reference_query'],'isolated machined cylinder')
        self.assertEqual(len(judge_calls),2)  # rejected candidate is regenerated and judged once on retry
        self.assertEqual([call['scope'] for call in judge_calls],['reference','reference'])
        self.assertEqual(judge_calls[0]['request']['required_features'],
                         ['circular cross section','flat top bottom'])
        self.assertEqual(search['candidates'][0]['provider'],'configured_reference_generator')
        self.assertEqual(generator_log,'candidate written')

    def test_reference_generator_failure_keeps_adapter_log_and_rejects_candidate(self):
        target=ornament()
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);adapter=root/'generator.exe';adapter.write_bytes(b'placeholder')
            with patch.object(assets.subprocess,'run',return_value=SimpleNamespace(returncode=9,stdout='backend unavailable')):
                with self.assertRaisesRegex(RuntimeError,r'failed \(9\)'):
                    assets.generate_reference_candidate(target,root/'run',adapter)
            self.assertEqual((root/'run'/'reference_generator_00.log').read_text(encoding='utf-8'),
                             'backend unavailable')

    def test_reference_generator_rejects_stale_or_invalid_output_images(self):
        target=ornament()
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);adapter=root/'generator.exe';adapter.write_bytes(b'placeholder')
            folder=root/'run';folder.mkdir()
            (folder/'generated_reference_00.png').write_bytes(b'\x89PNG\r\n\x1a\nold candidate')
            with patch.object(assets.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout='no output')):
                with self.assertRaisesRegex(RuntimeError,'did not write its output image'):
                    assets.generate_reference_candidate(target,folder,adapter)
            def invalid_image(command,**kwargs):
                Path(command[command.index('--output')+1]).write_bytes(b'not an image')
                return SimpleNamespace(returncode=0,stdout='bad image')
            with patch.object(assets.subprocess,'run',side_effect=invalid_image):
                with self.assertRaisesRegex(ValueError,'not a supported raster'):
                    assets.generate_reference_candidate(target,folder,adapter,attempt=1)

    def test_scene_entrypoint_passes_reference_generator_to_asset_resolution(self):
        from general_planner import PlannerScenePlan
        import run_general_scene as scene_runner
        import scene_execution
        data=json.loads((Path(__file__).resolve().parents[1]/'examples/general_metrology_fixture.json').read_text())
        data['components'][1].update(quantity=1,instance_positions=[],parts=[])
        data['components'][1]['source']=dict(mode='generate',reason='Custom surface form needs image-conditioned geometry.',
            retrieval_query='',reference_query='isolated machined cylinder',reference_image=None,
            trellis_required=True,trellis_reason='Use only a visually approved reference for TRELLIS.1.',
            generation_trigger='custom_appearance',reference_requirements=['one isolated complete cylinder'],
            quality_target='prototype')
        planner=PlannerScenePlan.model_validate(data)
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);plan_path=root/'plan.json'
            plan_path.write_text(json.dumps(planner.model_dump(mode='json',by_alias=True)),encoding='utf-8')
            adapter=root/'generator.py';adapter.write_text('# adapter path passed through the CLI',encoding='utf-8')
            args=SimpleNamespace(brief='one generated component',out=root/'run',planner='replay',plan=plan_path,
                plan_only=False,max_planning_repairs=0,max_visual_repairs=0,resume_runtime=None,
                model_profile=Path('model-profile.yaml'),vision_profile=Path('vision-profile.yaml'),
                offline=True,reference_generator=adapter)
            resolution_calls=[]
            def resolved(*call_args,**kwargs):
                resolution_calls.append(kwargs)
                return {'accepted':True,'path':'accepted.glb'}
            with patch.object(assets,'local_catalog',return_value=[]), \
                 patch.object(assets,'resolve_component',side_effect=resolved), \
                 patch.object(scene_execution,'execute_with_repairs',return_value={
                    'accepted':True,'geometry':{},'visual':{},'final_plan':{},'iterations':[]}):
                report=scene_runner.run_attempt(args)
            origin=json.loads((args.out/'run_origin.json').read_text(encoding='utf-8'))
            jobs=json.loads((args.out/'component_jobs.json').read_text(encoding='utf-8'))
        self.assertTrue(report['accepted'])
        self.assertEqual(len(resolution_calls),1)
        self.assertEqual(resolution_calls[0]['reference_generator'],adapter)
        self.assertEqual(origin['reference_generator'],str(adapter))
        self.assertEqual(jobs[0]['reference_generator'],str(adapter))

    def test_cleveland_image_is_still_rejected_by_reference_judge_before_generation(self):
        from general_planner import PlannerScenePlan,to_general_plan
        from general_assets import ComponentResolutionError
        data=json.loads((Path(__file__).resolve().parents[1]/'examples/general_metrology_fixture.json').read_text())
        data['components'][1].update(semantic='Local carved scroll ornament',required_features=['curved carved leaves'])
        data['components'][1]['parts']=[]
        data['components'][1]['source']=dict(mode='generate',reason='Custom freeform relief requires image-conditioned geometry.',
            retrieval_query='',reference_query='isolated carved wood scroll ornament',reference_image=None,
            trellis_required=True,trellis_reason='Use an accepted carving photograph as the TRELLIS.1 image condition.',
            generation_trigger='custom_appearance',reference_requirements=['isolated carved scroll with visible relief'],
            quality_target='prototype')
        plan,_=to_general_plan(PlannerScenePlan.model_validate(data))
        obj=next(item for item in plan.objects if item.id.startswith('sample'))
        judge_calls=[]
        def artwork():
            return {'id':1002,'title':'Acanthus wood applique','url':'https://clevelandart.org/art/1002',
                    'share_license_status':'CC0','technique':'Carved wood',
                    'images':{'web':{'url':'https://images.test/applique.jpg'}}}
        def fake_fetch(url,path=None,limit=None):
            if path:
                Path(path).write_bytes(b'\x89PNG\r\n\x1a\n'+b'0'*32)
                return str(path)
            if 'commons.wikimedia.org' in url:return {'query':{'pages':{}}}
            if 'openaccess-api.clevelandart.org' in url:return {'data':[artwork()]}
            if 'api.artic.edu/api/v1/artworks/search' in url:return {'data':[],'pagination':{'total':0}}
            raise AssertionError(f'unexpected catalog URL: {url}')
        def reject_reference(images,request,profile_path,output,**kwargs):
            judge_calls.append({'images':images,'request':request,'scope':kwargs.get('scope')})
            return {'accepted':False,'status':'needs_revision','decision':{
                'summary':'The photograph does not show an isolated relief matching this component.',
                'findings':[]}}
        with tempfile.TemporaryDirectory() as directory,patch.object(assets,'fetch',side_effect=fake_fetch), \
             patch.object(assets,'refine_reference_queries',return_value=[]), \
             patch.object(assets,'review_images',side_effect=reject_reference), \
             patch.object(assets,'run_logged',side_effect=AssertionError('TRELLIS must not run after a failed reference gate')):
            with self.assertRaises(ComponentResolutionError):
                assets.resolve_component(obj,plan,Path(directory),[],'unused','prototype')
        self.assertEqual(len(judge_calls),1)
        self.assertEqual(judge_calls[0]['scope'],'reference')
        self.assertTrue(judge_calls[0]['images'][0].endswith('cleveland_1002.png'))


if __name__=='__main__':unittest.main()
