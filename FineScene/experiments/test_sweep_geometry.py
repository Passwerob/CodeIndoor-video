"""Sweep transforms, contact, edit invariants and style bindings, independent of category."""
import copy
import sys
from pathlib import Path
from types import SimpleNamespace
import unittest
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from general_scene import GeneralPlan,compile_general,edit_general
from sweep_contacts import segment_box_distance,segment_distance,sweep_gap
from style_contract import StyleRequirement,StyleBinding,validate_style_bindings


def plan():
    return GeneralPlan.model_validate({'scene_id':'curved_member','description':'Editable curved member',
        'extent':[3,3,3],'parameters':{'bend':.08},'objects':[{
        'id':'member','semantic':'generic curved member','size':[1,.4,1],
        'parts':[{'id':'path','primitive':'sweep','size':[1,.3,1],'center':[0,0,.5],
            'path':[[-.4,0,-.47],[0,'bend',-.1],[.4,0,.2]],'radius':.03}]}]})


class SweepTests(unittest.TestCase):
    def test_edit_changes_path_even_when_envelope_stays_fixed(self):
        original=plan();edited=edit_general(original,'bend',.02)
        _,_,a=compile_general(original);_,_,b=compile_general(edited)
        ra=a['member']['internal_geometry']['parts']['path'];rb=b['member']['internal_geometry']['parts']['path']
        self.assertNotEqual(ra['path'],rb['path'])
        self.assertEqual(ra['size'],rb['size'])
        np.testing.assert_allclose(ra['path'][0],[-.4,0,.03])

    def test_rigid_path_cannot_deform_inside_fixed_envelope(self):
        p=plan();p.objects[0].parts[0].rigidity='rigid'
        with self.assertRaisesRegex(ValueError,'rigid internal part'):edit_general(p,'bend',.02)

    def test_rotation_keeps_measured_path_and_sphere_bounds_consistent(self):
        p=plan();p.objects[0].size=(2,2,1)
        p.objects[0].parts[0].rotation=(0,0,45)
        _,_,receipt=compile_general(p)
        record=receipt['member']['internal_geometry']['parts']['path']
        points=np.array(record['path'])
        np.testing.assert_allclose(record['bounds'],[points.min(axis=0)-.03,points.max(axis=0)+.03],atol=1e-8)

    def test_real_attached_sweep_passes_and_gap_or_penetration_fails(self):
        p=plan();obj=p.objects[0]
        from general_scene import Part
        from asset_physics import AssemblyContract
        curve=obj.parts[0];curve.center=(0,0,.54)
        obj.parts.insert(0,Part(id='base',primitive='box',size=(1,.4,.04),center=(0,0,.02)))
        obj.assembly=AssemblyContract.model_validate({'roots':['base'],'relations':[
            {'kind':'attached_to','part':'path','target':'base','reason':'capsule endpoint contact'}]})
        compile_general(p)
        curve.center=(0,0,.56)
        with self.assertRaisesRegex(ValueError,'attachment'):compile_general(p)
        curve.center=(0,0,.52)
        with self.assertRaisesRegex(ValueError,'penetration|intersection'):compile_general(p)

    def test_path_outside_envelope_and_zero_segments_rejected(self):
        p=plan();p.objects[0].parts[0].path[1]=(0,1,0)
        with self.assertRaisesRegex(ValueError,'exceeds declared'):compile_general(p)
        p=plan();p.objects[0].parts[0].path[1]=p.objects[0].parts[0].path[0]
        with self.assertRaisesRegex(ValueError,'zero-length'):compile_general(p)

    def test_box_distance_interior_endpoint_and_edge_minima(self):
        self.assertAlmostEqual(segment_box_distance([-2,0,0],[2,0,0],[1,1,1]),-1)
        self.assertAlmostEqual(segment_box_distance([2,-2,0],[2,2,0],[1,1,1]),1)
        self.assertAlmostEqual(segment_box_distance([2,2,0],[3,3,0],[1,1,1]),np.sqrt(2))
        self.assertAlmostEqual(segment_box_distance([1,1.5,0],[1.5,1,0],[1,1,1]),np.sqrt(.125))

    def test_segment_distance_crossing_parallel_and_endpoints(self):
        self.assertAlmostEqual(segment_distance([-1,0,0],[1,0,0],[0,-1,0],[0,1,0]),0)
        self.assertAlmostEqual(segment_distance([-1,0,0],[1,0,0],[-1,.2,0],[1,.2,0]),.2)
        self.assertAlmostEqual(segment_distance([0,0,0],[1,0,0],[2,1,0],[3,1,0]),np.sqrt(2))

    def test_sweep_box_uses_real_path_not_overlapping_envelope(self):
        box={'primitive':'box','center':[0,0,0],'size':[.2,.2,.2],'rotation':np.eye(3)}
        sweep={'primitive':'sweep','path':[[-.5,0,0],[-.5,0,.5],[.5,0,.5],[.5,0,0]],'radius':.02}
        self.assertAlmostEqual(sweep_gap(sweep,box),.38)
        sweep['path']=[[-.5,0,.12],[.5,0,.12]]
        self.assertAlmostEqual(sweep_gap(sweep,box),0)
        sweep['path']=[[-.5,0,0],[.5,0,0]]
        self.assertLess(sweep_gap(sweep,box),-.1)

    def test_rotated_box_contact_and_unsupported_surface(self):
        from asset_physics import rotation_matrix
        rotation=rotation_matrix([0,45,30])
        box={'primitive':'box','center':[.1,.2,.3],'size':[.2,.2,.2],'rotation':rotation}
        points=np.array([[-.3,0,.12],[.3,0,.12]])@rotation.T+box['center']
        sweep={'primitive':'sweep','path':points,'radius':.02}
        self.assertAlmostEqual(sweep_gap(sweep,box),0)
        box['primitive']='sphere'
        self.assertIsNone(sweep_gap(sweep,box))

    def test_sweep_cylinder_cavity_is_not_an_envelope_collision(self):
        from asset_physics import separating_gap
        cylinder={'primitive':'cylinder','center':[0,0,0],
                  'size':[.2,.2,.2],'rotation':np.eye(3)}
        sweep={'primitive':'sweep','path':[[-.5,0,0],[-.5,0,.5],[.5,0,.5],[.5,0,0]],
               'radius':.02,'center':[0,0,.25],'size':[1.04,.04,.54],
               'rotation':np.eye(3)}
        self.assertIsNotNone(sweep_gap(sweep,cylinder))
        self.assertAlmostEqual(separating_gap(sweep,cylinder),.38)
        # Both the flat cap and curved side must retain real contact checks.
        sweep['path']=[[-.3,0,.12],[.3,0,.12]]
        self.assertAlmostEqual(sweep_gap(sweep,cylinder),0)
        sweep['path']=[[-.3,.12,0],[.3,.12,0]]
        self.assertAlmostEqual(sweep_gap(sweep,cylinder),0)
        sweep['path']=[[-.3,0,0],[.3,0,0]]
        self.assertAlmostEqual(sweep_gap(sweep,cylinder),-.12)

    def test_sweep_cylinder_contact_survives_rotation_and_checks_real_joints(self):
        from asset_physics import rotation_matrix,AssemblyContract,audit_assembly
        rotation=rotation_matrix([40,25,70]);center=np.array([.4,-.7,1.2])
        cylinder={'primitive':'cylinder','center':center,'size':[.2,.2,.2],
                  'rotation':rotation}
        sweep={'primitive':'sweep','path':np.array([[-.3,0,.12],[.3,0,.12]])@rotation.T+center,
               'radius':.02}
        self.assertAlmostEqual(sweep_gap(cylinder,sweep),0)
        cylinder['size']=[.2,.3,.2]
        self.assertIsNone(sweep_gap(sweep,cylinder))

        cylinder={'primitive':'cylinder','center':[0,0,.1],'size':[.2,.2,.2],
                  'rotation':np.eye(3),'bounds':[[-.1,-.1,0],[.1,.1,.2]]}
        sweep={'primitive':'sweep','path':[[0,0,.22],[0,0,.52]],'radius':.02,
               'center':[0,0,.37],'size':[.04,.04,.34],'rotation':np.eye(3),
               'bounds':[[-.02,-.02,.2],[.02,.02,.54]]}
        obj=SimpleNamespace(id='joint',parts=[SimpleNamespace(id=id,form=None,parent=None)
                                             for id in ('base','curve')],
            assembly=AssemblyContract.model_validate({'roots':['base'],'relations':[
                {'kind':'attached_to','part':'curve','target':'base','reason':'real cap contact'}]}))
        records={'base':cylinder,'curve':sweep}
        self.assertEqual(audit_assembly(obj,records)['status'],'passed')
        for shift in (-.03,.03):
            moved=copy.deepcopy(records)
            moved['curve']['path']=(np.array(sweep['path'])+[0,0,shift]).tolist()
            moved['curve']['bounds']=(np.array(sweep['bounds'])+[0,0,shift]).tolist()
            result=audit_assembly(obj,moved)
            self.assertEqual(result['status'],'needs_revision')
            self.assertTrue(any(f['code']=='attachment' for f in result['findings']))

    def test_textual_sweep_does_not_bypass_style_contract(self):
        requirement=StyleRequirement(id='curve',feature='Curved perimeter rails',visible_evidence='Visible flowing path with round cross section',geometry_need='swept_path')
        binding=StyleBinding(requirement_id='curve',component_ids=['member'],implementation='Use sweep to construct the perimeter')
        p=plan();objects=p.objects
        with self.assertRaisesRegex(ValueError,'qualified'):validate_style_bindings([requirement],[binding],objects,source_mode=lambda o:o.source.mode)
        binding.part_ids=['member/path']
        validate_style_bindings([requirement],[binding],objects,source_mode=lambda o:o.source.mode)
        objects[0].parts[0].primitive='box'
        with self.assertRaisesRegex(ValueError,'real sweep'):validate_style_bindings([requirement],[binding],objects,source_mode=lambda o:o.source.mode)
        requirement.geometry_need='freeform_surface'
        with self.assertRaisesRegex(ValueError,'requires freeform'):validate_style_bindings([requirement],[binding],objects,source_mode=lambda o:o.source.mode)


if __name__=='__main__':unittest.main()
