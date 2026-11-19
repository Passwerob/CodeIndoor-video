"""Final review must drive scoped, validated candidates and fresh evidence."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parent))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from general_planner import PlannerScenePlan
from general_repairs import repair_candidate
from scene_execution import execute_with_repairs
from general_vision import review_scene_images


def plan_fixture():
    from test_asset_physics import shelf_plan
    p=shelf_plan();obj=p.objects[0]
    return PlannerScenePlan.model_validate({'intent':{'scene_id':p.scene_id,'description':p.description,
        'style':'prototype','quality_target':'prototype','extent':p.extent},'parameters':p.parameters,
        'components':[{'id':obj.id,'semantic':obj.semantic,'category':'storage','dimensions':obj.size,
            'rigidity':'parametric','decomposition_reason':'Keep one editable furniture asset with internal constraints.',
            'required_features':['two bays','two rigid books'],'material_requirements':['brown frame'],
            'parts':[part.model_dump() for part in obj.parts],'assembly':obj.assembly.model_dump(),
            'source':{'mode':'parametric','reason':'Editable board assembly with explicit internal interfaces.',
                      'quality_target':'prototype'}}],
        'edit_checks':[{'parameter':'gap','delta':.1,'intent':'increase clearance while keeping rigid books'}]})


def defect(part='book_high',target='geometry'):
    return {'accepted':False,'status':'needs_revision','decision':{'findings':[
        {'severity':'blocking','component_ids':['shelf_unit'],'part_ids':[part],
         'repair_target':target,'view':'overview.png','evidence':'Book placement needs correction.',
         'correction':'Move the upper book to the left half while preserving support.'}]}}


class VisualRepairTests(unittest.TestCase):
    def test_isolated_source_context_keeps_target_identity_and_review_boundary(self):
        from general_vision import visual_judge_context
        request={'original_user_request':'Create a complete reading and reception room.',
            'id':'lounge_chair','semantic':'Textured upholstered armchair',
            'required_features':['upholstered seating'],'material_requirements':['leather']}
        context=visual_judge_context(request,['overview.png','reverse.png','top.png'],'component')
        self.assertEqual(context['components'][0]['id'],'lounge_chair')
        self.assertEqual(context['components'][0]['semantic'],'Textured upholstered armchair')
        self.assertEqual(context['evidence_scope'],'isolated_source_asset')
        self.assertIn('after scene assembly',context['scope_instruction'])
        whole=visual_judge_context({'brief':'A room','review_phase':'whole_space_before_details',
            'phase_instruction':'Review all layout problems together.'},['overview.png'],'scene')
        self.assertEqual(whole['review_phase'],'whole_space_before_details')
        self.assertNotIn('evidence_scope',whole)

    def test_rejected_layout_does_not_render_details_and_passing_layout_still_requires_them(self):
        from scene_execution import review_layout_then_details
        for passed in (False,True):
            renders=[];reviews=[]
            def render():
                renders.append(True)
                return {'images':['overview.png','detail_prop.png']}
            def review(images,*args,**kwargs):
                reviews.append(list(images))
                return {'accepted':passed if len(reviews)==1 else False,'status':'passed' if passed and len(reviews)==1 else 'needs_revision'}
            with tempfile.TemporaryDirectory() as directory:
                result=review_layout_then_details(['overview.png'],{},'profile',Path(directory)/'qa.json',
                    quality='realistic',reviewer=review,detail_renderer=render)
            self.assertEqual(len(renders),int(passed))
            self.assertEqual(len(reviews),1+int(passed))
            self.assertFalse(result['accepted'])

    def test_whole_space_rejection_defers_closeups_without_claiming_final_approval(self):
        from scene_execution import review_layout_then_details
        calls=[]
        def reviewer(images,request,profile,output,**kwargs):
            calls.append(list(images))
            return {'accepted':False,'status':'needs_revision','decision':{'findings':[]}}
        with tempfile.TemporaryDirectory() as directory:
            result=review_layout_then_details(['overview.png','experience_entry.png','detail_prop.png'],
                {},'profile',Path(directory)/'final.json',quality='realistic',reviewer=reviewer)
            self.assertEqual(calls,[['overview.png','experience_entry.png']])
            self.assertFalse(result['accepted'])
            self.assertEqual(result['deferred_detail_images'],['detail_prop.png'])

    def test_scene_blockers_must_quote_the_original_user_brief(self):
        from general_vision import Decision,Finding,StyleAssessment,calibrate_blocking_findings
        decision=Decision(verdict='needs_revision',summary='Visual issues',semantic_match=3,visual_quality=3,
            findings=[
                Finding(severity='blocking',component_ids=['bookcase'],view='overview.png',
                    evidence='The bookcase is visibly almost empty.',correction='Add varied books.',
                    repair_target='decomposition',
                    user_requirement_quote='书籍要大小、装帧和摆放方式丰富'),
                Finding(severity='blocking',component_ids=['rug'],view='overview.png',
                    evidence='The rug is not old-rose and moss.',correction='Change its colors.',
                    repair_target='materials',
                    user_requirement_quote='old-rose and moss wool')],
            style_assessments=[StyleAssessment(requirement_id='palette',status='missing',evidence='Color mismatch')])
        brief='书籍要大小、装帧和摆放方式丰富，有成组竖放、少量平放与留白。不要使用普通木餐椅。'
        calibrated=calibrate_blocking_findings(decision,{'brief':brief,'style_requirements':[{'id':'palette'}]},'scene')
        self.assertEqual([finding.severity for finding in calibrated.findings],['blocking','improvement'])
        self.assertEqual(calibrated.verdict,'needs_revision')
        self.assertEqual(calibrated.style_assessments[0].status,'unclear')

    def test_finding_with_quote_from_agent_design_does_not_count_as_user_requirement(self):
        from general_vision import Decision,Finding,calibrate_blocking_findings
        decision=Decision(verdict='needs_revision',summary='Color mismatch',semantic_match=3,visual_quality=2,
            findings=[Finding(severity='blocking',component_ids=['rug'],view='overview.png',
                evidence='Pink rug',correction='Change the rug color.',repair_target='materials',
                user_requirement_quote='Use an old-rose and moss wool rug.')])
        calibrated=calibrate_blocking_findings(decision,{
            'brief':'Create a refined vintage reading lounge.',
            'style_requirements':[{'id':'palette'}]},'scene')
        self.assertEqual(calibrated.findings[0].severity,'improvement')
        self.assertEqual(calibrated.verdict,'passed')

    def test_scene_with_only_unsupported_visual_opinions_is_not_rejected(self):
        from general_vision import Decision,Finding,calibrate_blocking_findings
        decision=Decision(verdict='needs_revision',summary='Style mismatch',semantic_match=4,visual_quality=3,
            findings=[Finding(severity='blocking',component_ids=['rug'],view='overview.png',
                evidence='The rug should be moss green.',correction='Recolor it.',repair_target='materials',
                user_requirement_quote='')])
        calibrated=calibrate_blocking_findings(decision,{'brief':'Create a refined vintage reading room.'},'scene')
        self.assertEqual(calibrated.findings[0].severity,'improvement')
        self.assertEqual(calibrated.verdict,'passed')

    def test_visual_judge_receives_a_compact_view_scoped_context(self):
        from general_vision import visual_judge_context
        request={'brief':'Make a room with real walls and a full bookcase.',
            'quality_target':'realistic','components':[{'id':'bookcase','semantic':'bookcase',
                'required_features':['densely filled shelves'],'materials':['blue velvet']}],
            'style_requirements':[{'id':'books','feature':'Varied books',
                'visible_evidence':'Mixed sizes and a few horizontal stacks','geometry_need':'books'}],
            'scene_design':{'choices':[{'id':str(i)} for i in range(30)]},
            'spatial_contract':{'routes':[{'width_m':.2}]},
            'measured_geometry_evidence':{'support_contacts':{'bookcase':{'gap_m':.3}}},
            'render_evidence':{'views':[{'image':'overview.png','presentation':'cutaway',
                'temporarily_hidden_shell_meshes':['wall_a'],'design_view_intent':'whole_room'}]}}
        context=visual_judge_context(request,['C:/renders/overview.png'],'scene')
        self.assertEqual(context['original_user_request'],request['brief'])
        self.assertEqual(context['components'][0]['id'],'bookcase')
        self.assertEqual(context['render_views'][0],{'image':'overview.png','presentation':'cutaway','intent':'whole_room'})
        self.assertNotIn('scene_design',context)
        self.assertNotIn('spatial_contract',context)
        self.assertNotIn('measured_geometry_evidence',context)
        self.assertNotIn('temporarily_hidden_shell_meshes',str(context))
        self.assertEqual(context['layout_audit_categories'],[
            'seating_group','architectural_integration','primary_use_composition','access_and_sightlines'])

    def test_scene_layout_audit_requires_all_categories_and_materializes_visible_issues(self):
        from general_vision import (Decision,Finding,layout_audit_protocol_errors,
                                    materialize_layout_audit_findings)
        audits=[{'category':'seating_group','status':'issue','view':'overview.png',
                 'evidence':'The armchairs turn away from the shared table and sofa.',
                 'component_ids':['armchair_a','coffee_table','sofa'],
                 'correction':'Reorient the chairs and table together toward the shared seating focus.',
                 'severity':'improvement'},
                {'category':'architectural_integration','status':'clear','view':'reverse.png',
                 'evidence':'The wall fixtures visibly meet the interior wall plane.'},
                {'category':'primary_use_composition','status':'clear','view':'overview.png',
                 'evidence':'The primary gathering area reads as one group.'},
                {'category':'access_and_sightlines','status':'unclear','view':'top.png',
                 'evidence':'The top view does not show seated eye-level sightlines.'}]
        decision=Decision(verdict='passed',summary='Layout issues recorded as improvements.',
            semantic_match=4,visual_quality=3,findings=[],layout_audits=audits)
        self.assertEqual(layout_audit_protocol_errors(decision,
            ['overview.png','reverse.png','top.png']),[])
        materialize_layout_audit_findings(decision)
        self.assertEqual(len(decision.findings),1)
        finding=decision.findings[0]
        self.assertEqual(finding.repair_target,'layout')
        self.assertEqual(finding.component_ids,['armchair_a','coffee_table','sofa'])
        self.assertIn('turn away',finding.evidence)
        duplicate=Decision(verdict='needs_revision',summary='Same seating issue already reported.',
            semantic_match=3,visual_quality=2,layout_audits=audits,
            findings=[Finding(severity='improvement',component_ids=['sofa','armchair_a','coffee_table'],
                view='overview.png',evidence='The seating group is aimed away from the shared focus.',
                correction='Turn the seats toward the shared focus.',repair_target='layout')])
        materialize_layout_audit_findings(duplicate)
        self.assertEqual(len(duplicate.findings),1)
        decision.layout_audits.pop()
        errors=layout_audit_protocol_errors(decision,['overview.png','reverse.png','top.png'])
        self.assertTrue(any('exactly one of each category' in error for error in errors))

    def test_primary_composition_audit_can_escalate_excess_inventory_to_design_revision(self):
        from general_vision import Decision,consolidate_layout_audits,materialize_layout_audit_findings
        decision=Decision(verdict='needs_revision',summary='Optional objects crowd the primary group.',
            semantic_match=2,visual_quality=2,findings=[],layout_audits=[
                {'category':'primary_use_composition','status':'issue','view':'overview.png',
                 'evidence':'Several optional objects crowd the requested seating group.',
                 'component_ids':['optional_plant','optional_lamp'],
                 'correction':'Remove or consolidate the optional objects and preserve the seating anchors.',
                 'repair_target':'decomposition','severity':'blocking',
                 'user_requirement_quote':'keep the primary activity readable'}])
        merged=consolidate_layout_audits([audit.model_dump() for audit in decision.layout_audits])
        self.assertEqual(merged[0]['repair_target'],'decomposition')
        materialize_layout_audit_findings(decision)
        self.assertEqual(decision.findings[0].repair_target,'decomposition')
        self.assertEqual(decision.findings[0].component_ids,['optional_plant','optional_lamp'])

    def test_layout_audit_issue_needs_an_image_component_and_actionable_correction(self):
        from general_vision import Decision,layout_audit_protocol_errors
        audits=[{'category':'seating_group','status':'issue','view':'missing.png',
                 'evidence':'Seats face different directions.','component_ids':[], 'correction':''},
                {'category':'architectural_integration','status':'not_applicable'},
                {'category':'primary_use_composition','status':'clear','view':'overview.png',
                 'evidence':'The activity reads clearly.'},
                {'category':'access_and_sightlines','status':'clear','view':'overview.png',
                 'evidence':'No major visible blockage.'}]
        decision=Decision(verdict='passed',summary='Review incomplete.',semantic_match=3,
                          visual_quality=3,findings=[],layout_audits=audits)
        errors=layout_audit_protocol_errors(decision,['overview.png','top.png'])
        self.assertTrue(any('exact attached image filename' in error for error in errors))
        self.assertTrue(any('exact component_ids' in error for error in errors))
        self.assertTrue(any('actionable correction' in error for error in errors))

    def test_visual_findings_batch_exact_scope_and_preserve_each_observation(self):
        from general_vision import consolidate_findings
        shared={'severity':'blocking','component_ids':['chair_a','table'],
            'part_ids':[],'requirement_ids':[],'repair_target':'layout',
            'user_requirement_quote':'leave accessible paths',
            'evidence':'The chair blocks access to the table and faces away from the seat.',
            'correction':'Move the chair clear of the table and turn it toward the seating group.'}
        first={**shared,'view':'overview.png'}
        distinct={**shared,'view':'reverse.png',
            'evidence':'The chair faces the entrance rather than the table.',
            'correction':'Rotate the chair toward the table to restore a usable conversation group.'}
        duplicate={**shared,'view':'overview.png'}
        result=consolidate_findings([first,duplicate,distinct])
        self.assertEqual(len(result),1)
        self.assertEqual(result[0]['severity'],'blocking')
        self.assertEqual(result[0]['issue_count'],2)
        self.assertEqual(result[0]['user_requirement_quotes'],['leave accessible paths'])
        self.assertIn('The chair blocks access',result[0]['evidence'])
        self.assertIn('The chair faces the entrance',result[0]['evidence'])
        self.assertIn('overview.png',result[0]['view'])
        self.assertIn('reverse.png',result[0]['view'])

    def test_unknown_visual_style_status_becomes_unclear_and_stays_fail_closed(self):
        from general_vision import parse_decision_json,decision_inconsistent
        raw=json.dumps({'verdict':'needs_revision','summary':'The intended style is only partly visible.',
            'semantic_match':3,'visual_quality':3,'findings':[{'severity':'blocking',
                'component_ids':['shelf_unit'],'part_ids':[],'requirement_ids':['refined_vintage_palette'],
                'view':'overview.png','evidence':'The palette reads as a generic placeholder.',
                'correction':'Apply the requested materials and verify them in the scene render.',
                'repair_target':'materials'}],
            'style_assessments':[{'requirement_id':'refined_vintage_palette',
                'status':'realistic_placeholder','evidence':'Only generic placeholder colors are visible.'}]})
        request={'style_requirements':[{'id':'refined_vintage_palette'}]}
        decision=parse_decision_json(raw)
        self.assertEqual(decision.style_assessments[0].status,'unclear')
        self.assertFalse(decision_inconsistent(decision,request))
        self.assertEqual(decision.verdict,'needs_revision')

    def test_visual_request_carries_blender_contact_measurements(self):
        from scene_execution import visual_request
        plan=plan_fixture()
        evidence={'components':{'shelf_unit':{
            'mount':'top','support_part':'top_rail','vertical_gap_m':0.0002,
            'contact_center_world_xy':[0.001,-0.002],
            'measured_footprint':[.2,.18]}}}
        request=visual_request(plan,'test',actual_geometry={'shelf_unit':[[0,0,0],[1,1,1]]},
                               actual_support=evidence)
        self.assertEqual(request['measured_geometry_evidence']['object_world_bounds_m'],
                         {'shelf_unit':[[0,0,0],[1,1,1]]})
        self.assertEqual(request['measured_geometry_evidence']['support_contacts'],
                         evidence['components'])

    def test_repair_batches_all_findings_and_supplies_measured_contacts(self):
        from general_repairs import _repair_contact_evidence
        plan=plan_fixture()
        orders=[
            {'stage':'geometry','component_ids':['shelf_unit'],'part_ids':['book_high'],
             'evidence':'Move supported book','correction':'Place it on the shelf.'},
            {'stage':'geometry','component_ids':['shelf_unit'],'part_ids':['book_low'],
             'evidence':'Move lower book','correction':'Keep it on the base.'}]
        active=__import__('general_repairs')._active_repair_orders(plan,orders)
        self.assertEqual(active,orders)
        contacts=_repair_contact_evidence(plan,active)
        self.assertEqual(len(contacts),4)
        self.assertEqual(contacts[0]['relation']['kind'],'supported_by')
        self.assertAlmostEqual(contacts[0]['measured_support_gap_m'],0)
        self.assertEqual(contacts[1]['relation']['kind'],'clearance')
        self.assertGreater(contacts[1]['measured_clearance_m'],0)

        async def complete(stage,instructions,payload):
            self.assertEqual(payload['active_work_order']['stage'],'geometry')
            self.assertEqual(payload['active_work_order']['component_ids'],['shelf_unit'])
            self.assertEqual(payload['active_work_order']['part_ids'],['book_high','book_low'])
            self.assertEqual(payload['deferred_work_order_count'],0)
            self.assertEqual(len(payload['active_work_orders']),1)
            self.assertEqual(payload['active_work_order']['finding_count'],2)
            self.assertEqual(len(payload['candidate_scope']['repair_parts'])>=2,True)
            self.assertAlmostEqual(payload['measured_frozen_contacts'][0]['measured_support_gap_m'],0)
            return {'reason':'Move both books while keeping their verified supports.',
                'replacements':[{'path':'/components/0/parts/6/center','value':[-.24,0,0]},
                                {'path':'/components/0/parts/5/center','value':[.3,0,0]}]}
        with tempfile.TemporaryDirectory() as directory:
            candidate,receipt=repair_candidate(plan,{'findings':[]},
                {'status':'needs_revision','decision':{'findings':[
                    {'severity':'blocking','component_ids':['shelf_unit'],'part_ids':['book_high'],
                     'repair_target':'geometry','evidence':'Move it','correction':'Move it left.'},
                    {'severity':'blocking','component_ids':['shelf_unit'],'part_ids':['book_low'],
                     'repair_target':'geometry','evidence':'Move it','correction':'Move it right.'}]}},
                'unused',directory,complete=complete,max_attempts=1)
        self.assertIsNotNone(candidate)
        self.assertEqual(len(receipt['orders']),2)
        self.assertEqual(len(receipt['deferred_orders']),0)

    def test_layout_order_group_shares_scene_context_and_keeps_every_finding(self):
        from general_repairs import proposal_order_groups
        orders=[
            {'stage':'layout','component_ids':['chair_a','table'],'part_ids':[],
             'allowed_fields':['position'],'revalidate':['chair_a','table'],
             'evidence':'The chair blocks the table approach.','correction':'Move the chair back.','view':'overview.png'},
            {'stage':'layout','component_ids':['chair_b','sofa'],'part_ids':[],
             'allowed_fields':['position'],'revalidate':['chair_b','sofa'],
             'evidence':'The second chair faces away from the sofa.','correction':'Rotate it toward the sofa.','view':'reverse.png'},
            {'stage':'layout','component_ids':['chair_a','ottoman'],'part_ids':[],
             'allowed_fields':['orientation_deg'],'revalidate':['chair_a','ottoman'],
             'evidence':'The ottoman is behind the chair.','correction':'Place it in front of the chair.','view':'top.png'}]
        grouped=proposal_order_groups(orders)
        self.assertEqual(len(grouped),1)
        self.assertEqual(grouped[0]['finding_count'],3)
        self.assertEqual(set(grouped[0]['component_ids']),{'chair_a','chair_b','table','sofa','ottoman'})
        self.assertIn('chair blocks the table approach',grouped[0]['evidence'])
        self.assertIn('ottoman is behind the chair',grouped[0]['evidence'])
        self.assertEqual(grouped[0]['view'],'overview.png; reverse.png; top.png')

    def test_geometry_candidate_repair_uses_measured_findings_when_vision_is_unavailable(self):
        plan=plan_fixture();calls=[]
        geometry={'status':'needs_revision','findings':[{'severity':'blocking',
            'component_ids':['shelf_unit'],'part_ids':['book_high'],'repair_target':'geometry',
            'evidence':'Blender measured the book outside its supported placement.',
            'correction':'Move the book within the shelf while preserving its support.'}]}
        visual={'status':'unavailable','accepted':False,'error_type':'TimeoutError'}
        async def complete(stage,instructions,payload):
            calls.append((stage,instructions,payload))
            return {'reason':'Use the measured geometry finding to reposition the book.',
                'replacements':[{'path':'/components/0/parts/6/center','value':[-.24,0,0]}]}
        with tempfile.TemporaryDirectory() as directory:
            candidate,receipt=repair_candidate(plan,geometry,visual,'unused',directory,
                complete=complete,max_attempts=1)
        self.assertIsNotNone(candidate)
        self.assertEqual(len(calls),1)
        self.assertIn('visual Judge is temporarily unavailable',calls[0][1])
        self.assertEqual(calls[0][2]['base_geometry_status'],'needs_revision')
        self.assertEqual([order['stage'] for order in calls[0][2]['active_work_orders']],['geometry'])
        self.assertTrue(any(order['stage']=='visual_service' for order in receipt['deferred_orders']))

    def test_owner_access_failure_supplies_actual_local_frame(self):
        from general_repairs import _repair_contact_evidence
        contacts=_repair_contact_evidence(plan_fixture(),[{'stage':'layout',
            'component_ids':['shelf_unit'],'part_ids':[]}])
        self.assertEqual(len(contacts),1)
        self.assertIn('measured_local_bounds_m',contacts[0])
        self.assertIn('declared_local_bounds_m',contacts[0])

    def test_scene_layout_repair_omits_irrelevant_part_contact_payload(self):
        captured=[]
        async def complete(stage,instructions,payload):
            captured.append(payload)
            return {'reason':'Reposition the object as requested.',
                'replacements':[{'path':'/components/0/position','value':[.01,0,0]}]}
        with tempfile.TemporaryDirectory() as directory:
            visual={'status':'needs_revision','decision':{'findings':[{
                'severity':'blocking','component_ids':['shelf_unit'],'part_ids':[],
                'repair_target':'layout','view':'overview.png','evidence':'Move the whole shelf.',
                'correction':'Shift the shelf slightly to improve its placement.'}]}}
            candidate,receipt=repair_candidate(plan_fixture(),{'findings':[]},
                visual,'unused',directory,complete=complete,max_attempts=1)
        self.assertIsNotNone(candidate,receipt)
        self.assertEqual(captured[0]['measured_frozen_contacts'],[])
        self.assertNotIn('work_orders',captured[0])
        self.assertIn('active_work_orders',captured[0])

    def test_failed_sweep_contact_returns_safe_numeric_center_correction(self):
        from general_planner import PartSpec
        from asset_physics import PartRelation
        from general_repairs import _repair_contact_evidence
        plan=plan_fixture();component=plan.components[0]
        component.parts.append(PartSpec.model_validate({'id':'test_rail','primitive':'sweep',
            'size':[.7,.1,.2],'center':[0,.15,.09],'path':[[-.3,0,0],[.3,0,0]],'radius':.05}))
        component.assembly.relations.append(PartRelation(kind='attached_to',part='test_rail',target='base',
            reason='Rail seats on the rear portion of the base.'))
        order={'stage':'geometry','component_ids':['shelf_unit'],'part_ids':['test_rail']}
        evidence=_repair_contact_evidence(plan,[order])
        contact=next(item for item in evidence if item['relation']['part']=='test_rail')
        self.assertAlmostEqual(contact['measured_surface_gap_m'],0,places=6)
        component.parts[-1].center=(0,.15,.1)
        evidence=_repair_contact_evidence(plan,[order])
        contact=next(item for item in evidence if item['relation']['part']=='test_rail')
        self.assertAlmostEqual(contact['measured_surface_gap_m'],.01,places=6)
        correction=next(item for item in contact['contact_adjustments'] if item['axis']=='z')
        self.assertAlmostEqual(correction['center_delta_m'],-.0095,places=4)
        self.assertTrue(correction['envelope_safe'])

    def test_exact_internal_locator_drives_local_layout_repair(self):
        from general_vision import Decision,normalize_locators
        from general_repairs import repair_work_orders,allowed_repair_paths
        from scene_execution import visual_request
        plan=plan_fixture()
        decision=Decision(verdict='needs_revision',summary='Move contents',semantic_match=4,visual_quality=3,findings=[
            {'severity':'blocking','component_ids':['book_high'],'part_ids':[],'view':'front.png','evidence':'wrong position',
             'correction':'move book','repair_target':'layout'}])
        normalized=normalize_locators(decision,visual_request(plan,'test'))
        self.assertEqual(normalized.findings[0].component_ids,['shelf_unit'])
        self.assertEqual(normalized.findings[0].part_ids,['book_high'])
        orders=repair_work_orders(plan,{'findings':[]},{'decision':normalized.model_dump()})
        paths=allowed_repair_paths(plan,orders['orders'])
        self.assertIn('/components/0/parts/6/center',paths)
        self.assertNotIn('/components/0/position',paths)

    def test_unlocalized_geometry_finding_cannot_authorize_component_wide_edits(self):
        from general_repairs import repair_work_orders,allowed_repair_paths,_active_repair_orders
        plan=plan_fixture()
        geometry={'status':'needs_revision','findings':[{
            'component_ids':['shelf_unit'],'part_ids':[],'repair_target':'geometry',
            'evidence':'Measured appearance suggests that the shelf assembly needs correction.'}]}
        orders=repair_work_orders(plan,geometry,{'status':'passed','decision':{'findings':[]}})
        self.assertTrue(orders['orders'][0]['needs_decomposition_review'])
        self.assertEqual(allowed_repair_paths(plan,orders['orders']),[])
        self.assertEqual(_active_repair_orders(plan,orders['orders']),[])
        visual_orders=repair_work_orders(plan,{'findings':[]},{'decision':{'findings':[{
            'severity':'blocking','component_ids':['shelf_unit'],'part_ids':[],
            'repair_target':'geometry','evidence':'The visual view does not localize a damaged part.'}]}})['orders']
        self.assertTrue(visual_orders[0]['needs_decomposition_review'])

    def test_geometry_repair_paths_stay_within_explicitly_located_parts(self):
        from general_repairs import allowed_repair_paths
        plan=plan_fixture()
        paths=allowed_repair_paths(plan,[{'stage':'geometry','component_ids':['shelf_unit'],
                                          'part_ids':['book_high']}])
        self.assertIn('/components/0/parts/6/center',paths)
        self.assertIn('/components/0/parts/6/rotation',paths)
        self.assertNotIn('/components/0/parts/5/center',paths)
        self.assertNotIn('/components/0/parts/0/size',paths)

    def test_support_gap_repair_moves_only_the_supported_part(self):
        from general_repairs import allowed_repair_paths,repair_work_orders
        plan=plan_fixture()
        orders=repair_work_orders(plan,{'status':'needs_revision','findings':[{
            'component_ids':['shelf_unit'],'part_ids':['book_high','shelf'],
            'code':'support_gap','repair_target':'geometry',
            'evidence':'Measured book/shelf gap is 0.03 m.'}]},
            {'status':'passed','decision':{'findings':[]}})['orders']
        paths=allowed_repair_paths(plan,orders)
        self.assertIn('/components/0/parts/6/center',paths)
        self.assertNotIn('/components/0/parts/6/size',paths)
        self.assertNotIn('/components/0/parts/3/center',paths)
        self.assertNotIn('/components/0/parts/3/size',paths)

    def test_small_scene_review_sees_opposing_views_together(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);images=[]
            for name in ('front','overview','reverse','top','detail_asset'):
                p=root/(name+'.png');p.write_bytes(b'image-fixture');images.append(str(p))
            def review(received,*args,**kwargs):
                self.assertEqual(set(received),set(images))
                return {'status':'passed','accepted':True}
            result=review_scene_images(images,{'components':[]},'unused',root/'review.json',reviewer=review)
        self.assertTrue(result['accepted']);self.assertEqual(result['batch_count'],1)

    def test_whole_scene_layout_audit_runs_once_with_exterior_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);images=[]
            for name in ('front','overview','reverse','top','exterior','entrance_entry','experience_arrival'):
                path=root/f'{name}.png';path.write_bytes(b'image-fixture');images.append(str(path))
            calls=[]
            def review(received,prompt,*args,**kwargs):
                calls.append((received,prompt,kwargs))
                return {'status':'passed','accepted':True,'decision':{'findings':[]}}
            result=review_scene_images(images,{},'unused',root/'review.json',reviewer=review,max_workers=1)
        scene_calls=[call for call in calls if call[2].get('scope')=='scene']
        audited=[call for call in scene_calls if call[1].get('layout_audit_required') is True]
        local=[call for call in scene_calls if call[1].get('layout_audit_required') is False]
        self.assertEqual(len(audited),1)
        self.assertIn('exterior.png',[Path(path).name for path in audited[0][0]])
        self.assertTrue(local)
        self.assertEqual(result['batch_count'],2)

    def test_detail_review_pairs_two_objects_with_bounded_context(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);images=[]
            for name in ('front','overview','reverse','top'):
                p=root/f'{name}.png';p.write_bytes(b'image-fixture');images.append(str(p))
            for component_id in ('sofa','chair_a','chair_b','table','lamp','plant'):
                for prefix in ('detail_','side_'):
                    p=root/f'{prefix}{component_id}.png';p.write_bytes(b'image-fixture');images.append(str(p))
            calls=[]
            def review(received,prompt,*args,**kwargs):
                calls.append((received,prompt,kwargs))
                return {'status':'passed','accepted':True,'decision':{'findings':[]}}
            result=review_scene_images(images,{'components':[{'id':component_id,'semantic':component_id}
                for component_id in ('sofa','chair_a','chair_b','table','lamp','plant')]},
                'unused',root/'review.json',reviewer=review,max_workers=1)
        component_calls=[item for item in calls if item[2].get('scope')=='component']
        self.assertEqual(result['batch_count'],4)  # one overview plus three paired detail batches
        self.assertEqual(len(component_calls),3)
        self.assertTrue(all(len(item[0])<=6 for item in component_calls))
        self.assertTrue(all(len(item[1]['components'])==2 for item in component_calls))

    def test_material_repair_has_real_surface_controls_and_no_geometry_rights(self):
        plan=plan_fixture()
        async def complete(stage,instructions,payload):
            self.assertIn('/components/0/parts/0/surface',payload['allowed_paths'])
            self.assertNotIn('/components/0/parts/0/size',payload['allowed_paths'])
            return {'reason':'Use a rough wood finish','replacements':[
                {'path':'/components/0/parts/0/finish','value':'wood'},
                {'path':'/components/0/parts/0/surface','value':{'roughness':.7,'grain_axis':'x'}}]}
        with tempfile.TemporaryDirectory() as d:
            candidate,receipt=repair_candidate(plan,{'findings':[]},defect('base','materials'),'unused',d,complete=complete)
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.components[0].parts[0].surface.roughness,.7)
        self.assertEqual(candidate.components[0].parts[0].size,plan.components[0].parts[0].size)

    def test_scoped_fix_preserves_contract_and_checks_edits(self):
        plan=plan_fixture();before=plan.model_dump()
        async def complete(stage,instructions,payload):
            self.assertNotIn('/components/0/assembly',payload['allowed_paths'])
            self.assertNotIn('/components/0/parts/6/size',payload['allowed_paths'])
            return {'reason':'reposition book','replacements':[{'path':'/components/0/parts/6/center','value':[-.24,0,0]}]}
        with tempfile.TemporaryDirectory() as d:
            candidate,receipt=repair_candidate(plan,{'findings':[]},defect(),'unused',d,complete=complete)
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.components[0].assembly,plan.components[0].assembly)
        self.assertEqual(plan.model_dump(),before)
        self.assertFalse(receipt['accepted'])

    def test_patch_proposer_continues_after_a_rejected_candidate(self):
        plan=plan_fixture();calls=[]
        original=list(plan.components[0].parts[6].center)
        async def complete(stage,instructions,payload):
            calls.append(stage)
            if len(calls)==1:
                return {'reason':'No actual change','replacements':[
                    {'path':'/components/0/parts/6/center','value':original}]}
            return {'reason':'Move the upper book while preserving its support.',
                'replacements':[{'path':'/components/0/parts/6/center','value':[-.24,0,0]}]}
        with tempfile.TemporaryDirectory() as directory:
            candidate,receipt=repair_candidate(plan,{'findings':[]},defect(),'unused',directory,
                                                complete=complete,max_attempts=0)
        self.assertIsNotNone(candidate)
        self.assertEqual(len(calls),2)
        self.assertEqual([item['status'] for item in receipt['attempts']],['rejected','validated'])

    def test_repair_cannot_delete_contract_or_resize_rigid_book(self):
        for path,value in [('/components/0/assembly/relations',[]),('/components/0/parts/6/size',[.09,.22,.1])]:
            async def complete(*args):return {'reason':'cheat','replacements':[{'path':path,'value':value}]}
            with tempfile.TemporaryDirectory() as d:
                candidate,receipt=repair_candidate(plan_fixture(),{'findings':[]},defect(),'unused',d,complete=complete,max_attempts=1)
            self.assertIsNone(candidate)
            self.assertIn('authorized fields',receipt['attempts'][0]['error'])

    def test_candidate_that_breaks_internal_contact_is_rejected(self):
        async def complete(*args):
            return {'reason':'float book','replacements':[{'path':'/components/0/parts/6/center','value':[.24,0,.1]}]}
        with tempfile.TemporaryDirectory() as d:
            candidate,receipt=repair_candidate(plan_fixture(),{'findings':[]},defect(),'unused',d,complete=complete,max_attempts=1)
        self.assertIsNone(candidate)
        self.assertIn('support_gap',receipt['attempts'][0]['error'])

    def test_new_visual_evidence_is_required_after_repair(self):
        calls=[];assets={}
        def evaluate(plan,received,directory,*args):
            self.assertIs(received,assets);calls.append(str(directory))
            visual=defect() if len(calls)==1 else {'status':'passed','accepted':True}
            return {'accepted':False,'geometry':{'status':'passed','findings':[]},'visual':visual,'directory':str(directory)}
        def repair(plan,*args):
            result=plan.model_copy(deep=True);result.components[0].parts[-1].center=(-.24,0.,0.)
            return result,{}
        with tempfile.TemporaryDirectory() as d:
            result=execute_with_repairs(plan_fixture(),assets,d,'shelf','unused','unused',evaluator=evaluate,repairer=repair)
        self.assertEqual(len(calls),2);self.assertTrue(result['accepted'])

    def test_spatial_recovery_keeps_localizable_layout_findings_in_local_repair(self):
        plan=plan_fixture();calls=[];repairs=[]
        def evaluate(candidate,assets,directory,*_):
            calls.append(directory)
            if len(calls)==1:
                return {'accepted':False,
                    'geometry':{'status':'needs_revision','findings':[{'component_ids':['shelf_unit'],
                            'repair_target':'layout','evidence':'The shelf faces away from its intended activity.'}],
                        'spatial':{'findings':[{'component_ids':['shelf_unit'],
                            'evidence':'The shelf faces away from its intended activity.'}]}},
                    'visual':{'status':'needs_revision','accepted':False,'decision':{'findings':[
                        {'severity':'blocking','component_ids':['shelf_unit'],'part_ids':[],
                         'repair_target':'layout','evidence':'The shelf faces away from the reading area.',
                         'correction':'Turn the shelf toward the room.'}]}},'directory':str(directory)}
            return {'accepted':False,'geometry':{'status':'passed','findings':[]},
                'visual':{'status':'passed','accepted':True,'decision':{'findings':[]}},
                'directory':str(directory)}
        def repair(current,*_):
            repairs.append(current)
            return current.model_copy(deep=True),{'status':'candidate_validated_pending_render'}
        with tempfile.TemporaryDirectory() as directory:
            result=execute_with_repairs(plan,{},directory,'brief','unused','unused',
                evaluator=evaluate,repairer=repair,coupled_layout_recovery=True)
        self.assertTrue(result['accepted'])
        self.assertEqual(len(calls),2)
        self.assertEqual(len(repairs),1)
        self.assertNotIn('replan_reason',result)

    def test_unlocalized_spatial_finding_escalates_to_coupled_recovery(self):
        plan=plan_fixture();calls=[]
        def evaluate(candidate,assets,directory,*_):
            calls.append(directory)
            return {'accepted':False,
                'geometry':{'status':'needs_revision','findings':[],
                    'spatial':{'findings':[{'component_ids':[],
                        'evidence':'The scene composition cannot be localized to an object.'}]}},
                'visual':{'status':'passed','accepted':True,'decision':{'findings':[]}},
                'directory':str(directory)}
        def forbidden(*_):raise AssertionError('Unlocalized scene issue must use coupled recovery')
        with tempfile.TemporaryDirectory() as directory:
            result=execute_with_repairs(plan,{},directory,'brief','unused','unused',
                evaluator=evaluate,repairer=forbidden,coupled_layout_recovery=True)
        self.assertEqual(len(calls),1)
        self.assertEqual(result['replan_reason'],'coupled_spatial_replan_from_reconstructed_evidence')

    def test_visual_repair_continues_past_legacy_zero_budget_for_new_findings(self):
        calls=[];repairs=[];plan=plan_fixture()
        def evaluate(candidate,assets,directory,*_):
            calls.append(str(directory))
            if len(calls)==1:visual=defect('book_high')
            elif len(calls)==2:visual=defect('book_low')
            else:visual={'status':'passed','accepted':True,'decision':{'findings':[]}}
            return {'accepted':False,'geometry':{'status':'passed','findings':[]},
                    'visual':visual,'directory':str(directory)}
        def repair(current,*_):
            repairs.append(current)
            return current.model_copy(deep=True),{'status':'candidate_validated_pending_render'}
        with tempfile.TemporaryDirectory() as directory:
            result=execute_with_repairs(plan,{},directory,'shelf','unused','unused',max_repairs=0,
                                        evaluator=evaluate,repairer=repair)
        self.assertTrue(result['accepted'])
        self.assertEqual(len(calls),3)
        self.assertEqual(len(repairs),2)

    def test_visual_outage_never_regenerates_geometry(self):
        calls=[]
        def evaluate(*args):
            calls.append(1)
            return {'accepted':True,'geometry':{'status':'passed','findings':[]},
                'visual':{'status':'unavailable','accepted':False},'directory':str(args[2])}
        def forbidden(*args):raise AssertionError('No repair on transport outage')
        with tempfile.TemporaryDirectory() as d:
            result=execute_with_repairs(plan_fixture(),{},d,'shelf','unused','unused',evaluator=evaluate,repairer=forbidden)
        self.assertFalse(result['accepted']);self.assertEqual(len(calls),1)

    def test_visual_outage_does_not_skip_actionable_blender_geometry_repair(self):
        calls=[];repairs=[];plan=plan_fixture()
        finding={'severity':'blocking','component_ids':['shelf_unit'],'part_ids':['book_high'],
                 'repair_target':'geometry','evidence':'Measured Blender geometry reports an invalid book placement.',
                 'correction':'Reposition the book while preserving its verified support.'}
        def evaluate(candidate,assets,directory,*_):
            calls.append(str(directory))
            geometry={'status':'needs_revision','findings':[finding]} if len(calls)==1 else {
                'status':'passed','findings':[]}
            return {'accepted':False,'geometry':geometry,
                    'visual':{'status':'unavailable','accepted':False},'directory':str(directory)}
        def repair(current,*_):
            repairs.append(current)
            return current.model_copy(deep=True),{'status':'candidate_validated_pending_render'}
        with tempfile.TemporaryDirectory() as directory:
            result=execute_with_repairs(plan,{},directory,'shelf','unused','unused',
                                        evaluator=evaluate,repairer=repair)
        self.assertEqual(len(calls),2)
        self.assertEqual(len(repairs),1)
        self.assertFalse(result['accepted'])
        self.assertEqual(result['visual']['status'],'unavailable')

    def test_passing_scene_images_cannot_accept_rejected_source(self):
        def evaluate(*args):
            return {'accepted':True,'geometry':{'status':'passed','findings':[]},
                'visual':{'status':'passed','accepted':True},
                'source_qa':{'status':'needs_revision','unaccepted_components':['external']},
                'directory':str(args[2])}
        with tempfile.TemporaryDirectory() as d:
            result=execute_with_repairs(plan_fixture(),{},d,'shelf','unused','unused',
                evaluator=evaluate,max_repairs=0)
        self.assertFalse(result['accepted'])

    def test_all_image_batches_are_required(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);images=[]
            for name in ('overview','reverse','top','front','detail_shelf_unit','detail_other','detail_third'):
                p=root/(name+'.png');p.write_bytes(b'image-fixture');images.append(str(p))
            calls=[]
            def review(images,*args,**kwargs):
                calls.append(images)
                return {'status':'passed' if len(calls)==1 else 'unavailable','accepted':len(calls)==1}
            result=review_scene_images(images,{'components':[{'id':'shelf_unit'}]},'unused',root/'review.json',reviewer=review,max_workers=1)
        self.assertFalse(result['accepted']);self.assertEqual(len(calls),5)
        self.assertTrue(result['unreviewed_images'])
        self.assertTrue(any('detail_third.png' in [Path(p).name for p in views] for views in calls))

    def test_architecture_and_side_views_reach_the_correct_judge_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);images=[];calls=[]
            for name in ('overview','reverse','top','front','exterior','entrance_entry',
                         'detail_shelf_unit','side_shelf_unit'):
                path=root/(name+'.png');path.write_bytes(b'image-fixture');images.append(str(path))
            def review(views,request,*args,**kwargs):
                calls.append((views,request,kwargs['scope']))
                return {'status':'passed','accepted':True}
            result=review_scene_images(images,{'components':[{'id':'shelf_unit'}]},'unused',
                                       root/'review.json',reviewer=review,max_workers=1)
        self.assertTrue(result['accepted'])
        self.assertEqual(result['unreviewed_images'],[])
        self.assertEqual(calls[1][2],'scene')
        self.assertIn('entrance_entry.png',[Path(p).name for p in calls[1][0]])
        self.assertEqual(calls[2][2],'component')
        self.assertEqual(calls[2][1]['components'],[{'id':'shelf_unit'}])
        self.assertIn('side_shelf_unit.png',[Path(p).name for p in calls[2][0]])

    def test_service_retry_is_automatic_and_reuses_identical_images(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);images=[];calls=[]
            for name in ('front','overview','reverse'):
                p=root/(name+'.png');p.write_bytes(b'image-fixture');images.append(str(p))
            def review(received,*args,**kwargs):
                calls.append(list(received))
                return {'status':'unavailable' if len(calls)==1 else 'passed','accepted':len(calls)>1}
            result=review_scene_images(images,{'components':[]},'unused',root/'review.json',reviewer=review)
        self.assertTrue(result['accepted']);self.assertEqual(calls,[images,images])
        self.assertEqual(len(result['batch_attempts'][0]),2)

    def test_camera_repair_preserves_geometry(self):
        from general_planner import to_general_plan
        plan=plan_fixture()
        async def complete(stage,instructions,payload):
            self.assertEqual(payload['allowed_paths'],['/intent/review_azimuth_deg'])
            return {'reason':'Show opposite side','replacements':[{'path':'/intent/review_azimuth_deg','value':180}]}
        with tempfile.TemporaryDirectory() as d:
            candidate,_=repair_candidate(plan,{'findings':[]},defect(target='camera'),'unused',d,complete=complete)
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.components,plan.components)
        self.assertEqual(to_general_plan(candidate)[0].review_azimuth_deg,180)


if __name__=='__main__':unittest.main(verbosity=2)
