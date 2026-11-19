"""Impossible contact capacity returns to interface planning, preserving geometry."""
import asyncio
import copy
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from staged_planner import (Decomposition,Recipe,Layout,StagedScenePlanner,MountingInterfaceRevision,
                           validate_mounting_interface_revision,replacement_paths)
from planning_diagnostics import PlanningValidationError
from surface_mounting import SurfaceContact,SurfaceMountingError,resolve


def fixture():
    objects=[]
    for id,size,parts in [('host',[1,1,.2],['top']),('fixture',[1.2,1.2,.1],['pad','panel'])]:
        objects.append(dict(id=id,semantic='Generic supported assembly',category='assembly',dimensions=size,
            rigidity='parametric',grounded=id=='host',parent=None if id=='host' else 'host',
            decomposition_reason='Independent editable structural assembly',required_features=['real support'],
            material_requirements=['matte'],visual_importance=3,
            internal_parts=[dict(id=p,semantic=p,rigidity='parametric',role='structure') for p in parts],
            acquisition=dict(strategy='parametric',reason='Regular editable geometric structure')))
    objects[1]['surface_contact']=dict(parent_part='top',parent_face='z_max',child_part='panel',child_face='z_min')
    outline=Decomposition.model_validate(dict(scene_id='generic_mount',description='A supported display panel',
        style='functional',quality_target='prototype',extent=[5,5,3],parameters={},objects=objects,
        zones=[dict(name='display',purpose='display assembly',component_ids=['host','fixture'])],
        requirements=[dict(requirement='supported structure',component_ids=['host','fixture'],verification='geometry')]))
    recipes={'host':Recipe.model_validate({'parts':[dict(id='top',primitive='box',size=[1,1,.2],center=[0,0,.1])],
        'assembly':{'roots':['top'],'relations':[]}}),
        'fixture':Recipe.model_validate({'parts':[
            dict(id='pad',primitive='box',size=[.2,.2,.06],center=[0,0,.03]),
            dict(id='panel',primitive='box',size=[1.2,1.2,.04],center=[0,0,.08])],
            'assembly':{'roots':['pad'],'relations':[dict(kind='attached_to',part='panel',target='pad',reason='fixed pad connection')]}})}
    coarse=Layout.model_validate({'placements':[{'id':o.id,'position':[0,0,0]} for o in outline.objects]})
    return outline,recipes,coarse


class MountingRevisionTests(unittest.TestCase):
    def test_wall_contact_uses_the_component_thickness_axis(self):
        outline,_,_=fixture()
        data=outline.model_dump(mode='json')
        data['objects'][1]['dimensions']=[.86,.03,.66]
        outline=Decomposition.model_validate(data)
        broad_face=MountingInterfaceRevision(contacts={'fixture':dict(parent_part='top',
            parent_face='x_max',child_face='x_min')},reason='Attach the component to the host side')
        with self.assertRaisesRegex(ValueError,'thinner horizontal envelope'):
            validate_mounting_interface_revision(broad_face,outline,{'fixture'})
        front_face=MountingInterfaceRevision(contacts={'fixture':dict(parent_part='top',
            parent_face='x_max',child_face='y_min')},reason='Use the thin face against the host side')
        with self.assertRaisesRegex(ValueError,'functional front'):
            validate_mounting_interface_revision(front_face,outline,{'fixture'})
        thin_face=MountingInterfaceRevision(contacts={'fixture':dict(parent_part='top',
            parent_face='x_max',child_face='y_max')},reason='Use the rear face against the host side')
        revised=validate_mounting_interface_revision(thin_face,outline,{'fixture'})
        self.assertEqual(revised.objects[1].surface_contact.child_face,'y_max')

    def test_named_back_panel_contact_must_use_its_declared_plane_axis(self):
        outline,_,_=fixture()
        data=outline.model_dump(mode='json')
        data['objects'][1]['internal_parts'][1]['form']={
            'kind':'panel','axis':'y','minimum_ratio':2.0}
        outline=Decomposition.model_validate(data)
        wrong=MountingInterfaceRevision(contacts={'fixture':dict(parent_part='top',
            parent_face='x_max',child_part='panel',child_face='x_max')},
            reason='Mount the back panel against the wall')
        with self.assertRaises(PlanningValidationError) as caught:
            validate_mounting_interface_revision(wrong,outline,{'fixture'})
        self.assertEqual(caught.exception.measurements['issues'][0]['code'],'mounting_face_axis')
        self.assertIn('panel normal axis is Y',caught.exception.measurements['issues'][0]['evidence'])
        right=MountingInterfaceRevision(contacts={'fixture':dict(parent_part='top',
            parent_face='x_max',child_part='panel',child_face='y_max')},
            reason='Use the panel face on the wall')
        revised=validate_mounting_interface_revision(right,outline,{'fixture'})
        self.assertEqual(revised.objects[1].surface_contact.child_face,'y_max')

    def test_spatial_recovery_rejects_child_long_edge_before_compiler_moves_asset(self):
        from spatial_recovery import revise_recovery_mounting_contacts
        from planning_diagnostics import PlanningValidationError
        outline,_,_=fixture()
        data=outline.model_dump(mode='json')
        host,bookcase=data['objects']
        host['collision_role']='shell'
        host['internal_parts'].append({'id':'wall_east','semantic':'East wall','rigidity':'parametric',
            'role':'structure','support':None,'form':None,'requirement_tags':[]})
        bookcase.update(parent=None,mount='origin',grounded=True,support_part=None,surface_contact=None,
            dimensions=[2.8,.38,2.9])
        outline=Decomposition.model_validate(data)
        recipes={
            'host':Recipe.model_validate({'parts':[
                {'id':'wall_east','primitive':'box','size':[.1,1,2],'center':[0,0,1]}],
                'assembly':{'roots':['wall_east'],'relations':[]}}),
            'fixture':Recipe.model_validate({'parts':[
                {'id':'bookcase_back','primitive':'box','size':[2.74,.02,2.66],
                 'center':[0,.17,1.45]}],
                'assembly':{'roots':['bookcase_back'],'relations':[]}})}
        wrong=SurfaceContact(parent_part='wall_east',parent_face='x_max',
            child_part='bookcase_back',child_face='x_max')
        with self.assertRaises(PlanningValidationError) as caught:
            revise_recovery_mounting_contacts(outline,recipes,{'fixture':wrong})
        issue=caught.exception.measurements['issues'][0]
        self.assertEqual(issue['code'],'mounting_child_face_axis')
        self.assertEqual(issue['measured']['part_size_m'],[2.74,.02,2.66])
        right=SurfaceContact(parent_part='wall_east',parent_face='x_max',
            child_part='bookcase_back',child_face='y_max')
        revised,new_parents=revise_recovery_mounting_contacts(outline,recipes,{'fixture':right})
        self.assertEqual(new_parents,{'fixture':'host'})
        self.assertEqual(revised.objects[1].surface_contact.child_face,'y_max')

    def test_actual_area_capacity_is_distinguished_from_a_position_failure(self):
        contact=SurfaceContact(parent_part='top',parent_face='z_max',child_face='z_min')
        with self.assertRaises(SurfaceMountingError) as caught:
            resolve(contact,{'top':[[-.5,-.5,0],[.5,.5,.2]]},[[-.6,-.6,0],[.6,.6,.1]],{},[0,0,0],0)
        self.assertEqual(caught.exception.code,'mounting_interface_capacity')
        self.assertGreater(caught.exception.measurements['contact_area_m2'],
                           caught.exception.measurements['support_area_upper_bound_m2'])

    def test_normal_loop_revises_only_interface_and_keeps_validated_geometry(self):
        outline,recipes,coarse=fixture();before=copy.deepcopy(outline.model_dump());calls=[]
        async def complete(stage,instructions,payload):
            calls.append(stage)
            self.assertEqual(stage,'mounting_interface_revision_1_0')
            self.assertEqual(payload['context']['failed_component_ids'],['fixture'])
            self.assertEqual(payload['schema']['properties']['contacts']['required'],['fixture'])
            self.assertFalse(payload['schema']['properties']['contacts']['additionalProperties'])
            self.assertIn('pad',payload['context']['measured_interfaces']['fixture'])
            return {'contacts':{'fixture':dict(parent_part='top',parent_face='z_max',child_part='pad',child_face='z_min')},
                    'reason':'The body overhangs; its existing pad is the actual supporting interface.'}
        with tempfile.TemporaryDirectory() as tmp:
            planner=StagedScenePlanner('unused',tmp,'test',complete=complete)
            plan,revised=asyncio.run(planner.finalize_layout(outline,recipes,coarse,None,[],
                {'brief':'Create the supported assembly','frozen_decomposition':outline.model_dump()},replacement_paths))
        self.assertEqual(calls,['mounting_interface_revision_1_0'])
        self.assertEqual(outline.model_dump(),before)
        expected=copy.deepcopy(before);expected['objects'][1]['surface_contact']['child_part']='pad'
        self.assertEqual(revised.model_dump(),expected)
        self.assertEqual(plan.components[1].parts,recipes['fixture'].parts)
        self.assertEqual(plan.components[1].surface_contact.child_part,'pad')

    def test_interface_revision_cannot_invent_members_or_change_other_owners(self):
        outline,_,_=fixture()
        revision=MountingInterfaceRevision(contacts={'fixture':SurfaceContact(parent_part='top',
            parent_face='z_max',child_part='invented',child_face='z_min')},reason='Select another actual member')
        with self.assertRaises(ValueError):validate_mounting_interface_revision(revision,outline,{'fixture'})
        with self.assertRaises(ValueError):validate_mounting_interface_revision(revision,outline,{'host'})


if __name__=='__main__':unittest.main()
