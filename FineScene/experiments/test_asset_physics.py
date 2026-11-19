"""Physical consistency inside one aDSL asset, including bookshelf edits."""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from general_scene import GeneralPlan, compile_general, edit_general
from asset_physics import FitSpan


def shelf_plan():
    def part(id,size,center,**kw):
        return dict(id=id,semantic=id,primitive='box',size=size,center=center,**kw)
    parts=[
        part('base',['w','d','t'],[0,0,'t/2'],color=[.3,.16,.08]),
        part('left',['t','d','h-2*t'],['-w/2+t/2',0,'h/2'],color=[.3,.16,.08]),
        part('right',['t','d','h-2*t'],['w/2-t/2',0,'h/2'],color=[.3,.16,.08]),
        part('shelf',['w-2*t','d','t'],[0,0,'t+gap+t/2'],color=[.3,.16,.08]),
        part('top',['w','d','t'],[0,0,'h-t/2'],color=[.3,.16,.08]),
        part('book_low',[.07,.2,.26],[-.3,0,0],parent='base',mount='top',rigidity='rigid',color=[.15,.25,.55]),
        part('book_high',[.09,.22,.25],[.24,0,0],parent='shelf',mount='top',rigidity='rigid',color=[.45,.12,.1]),
    ]
    joints=[dict(kind='attached_to',part=a,target=b,reason='fastened board joint') for a,b in
            [('left','base'),('right','base'),('shelf','left'),('shelf','right'),('top','left'),('top','right')]]
    supports=[dict(kind='supported_by',part=a,target=b,reason='book rests on board') for a,b in
              [('book_low','base'),('book_high','shelf')]]
    gaps=[dict(kind='clearance',part=a,target=b,minimum=.02,reason='headroom above rigid book') for a,b in
          [('book_low','shelf'),('book_high','top')]]
    return GeneralPlan.model_validate(dict(scene_id='editable_shelf',description='One asset with shelves and rigid books',
        extent=[3,3,3],parameters={'gap':.32,'t':.04},objects=[dict(id='shelf_unit',semantic='two-bay bookshelf',
        size=[1.2,.4,'3*t+2*gap'],parts=parts,assembly={'roots':['base'],'relations':joints+supports+gaps})]))


class AssetPhysicsTests(unittest.TestCase):
    def test_circular_cylinder_caps_need_opposing_contained_faces(self):
        import copy,numpy as np
        from asset_physics import flat_cap_attachment,rotation_matrix
        base={'primitive':'cylinder','size':[.2,.2,.2],'center':[0,0,0],
              'rotation':np.eye(3).tolist()}
        member={'primitive':'cylinder','size':[.05,.05,.2],'center':[0,0,.2],
                'rotation':np.eye(3).tolist()}
        self.assertTrue(flat_cap_attachment(member,base))
        member['center']=[0,0,.21]
        self.assertFalse(flat_cap_attachment(member,base))
        member['center']=[.09,0,.2]
        self.assertFalse(flat_cap_attachment(member,base))
        member['center']=[.125,0,0]  # Tangent curved sides are not a flat joint.
        self.assertFalse(flat_cap_attachment(member,base))
        member['center']=[0,0,.2]
        rotation=rotation_matrix([35,60,-20]);offset=np.array([.2,-.3,.7])
        rotated=copy.deepcopy([member,base])
        for record in rotated:
            record['center']=(rotation@record['center']+offset).tolist()
            record['rotation']=rotation.tolist()
        self.assertTrue(flat_cap_attachment(*rotated))
        member['rotation']=rotation_matrix([0,180,0]).tolist()
        self.assertTrue(flat_cap_attachment(member,base))
        member['size']=[.05,.06,.2]
        self.assertFalse(flat_cap_attachment(member,base))

    def test_cylinder_fastening_needs_real_flat_cap_contact(self):
        import numpy as np
        from asset_physics import flat_cap_attachment
        box={'primitive':'box','size':[1,1,.1],'center':[0,0,.55],'rotation':np.eye(3).tolist()}
        cylinder={'primitive':'cylinder','size':[.1,.1,.5],'center':[0,0,.25],'rotation':np.eye(3).tolist()}
        self.assertTrue(flat_cap_attachment(cylinder,box))
        cylinder['center']=[0,0,.22]
        self.assertFalse(flat_cap_attachment(cylinder,box))
        cylinder['center']=[.51,0,.25]
        self.assertFalse(flat_cap_attachment(cylinder,box))
        cylinder['center']=[0,0,.25];cylinder['rotation']=[[0,0,1],[0,1,0],[-1,0,0]]
        self.assertFalse(flat_cap_attachment(cylinder,box))

    def test_face_spans_solve_members_and_survive_edits(self):
        plan=shelf_plan()
        for index in (1,2):
            part=plan.objects[0].parts[index]
            part.size=('t','d','h')
            part.fit_between=[FitSpan(axis='z',start='base',end='top')]
        plan.objects[0].parts[3].fit_between=[FitSpan(axis='x',start='left',end='right')]
        for candidate in (plan,edit_general(plan,'gap',.1)):
            _,_,receipts=compile_general(candidate)
            records=receipts['shelf_unit']['internal_geometry']['parts']
            self.assertAlmostEqual(records['left']['bounds'][0][2],records['base']['bounds'][1][2])
            self.assertAlmostEqual(records['left']['bounds'][1][2],records['top']['bounds'][0][2])

    def test_cyclic_face_fit_and_rigid_fitting_are_rejected(self):
        plan=shelf_plan();plan.objects[0].parts[1].fit_between=[FitSpan(axis='z',start='base',end='top')]
        plan.objects[0].parts[4].fit_between=[FitSpan(axis='x',start='left',end='right')]
        with self.assertRaisesRegex(ValueError,'cyclic'):GeneralPlan.model_validate(plan.model_dump())
        plan=shelf_plan();plan.objects[0].parts[-1].fit_between=[FitSpan(axis='z',start='base',end='top')]
        with self.assertRaisesRegex(ValueError,'parametric boxes'):GeneralPlan.model_validate(plan.model_dump())

    def test_outer_envelope_cannot_hide_unconstrained_floating_shelf(self):
        plan=shelf_plan();plan.objects[0].assembly=None
        with self.assertRaisesRegex(ValueError,'missing_contract'):compile_general(plan)

    def test_measured_connections_not_labels(self):
        plan=shelf_plan()
        plan.objects[0].parts[3].size=(.8,.4,.04)  # no longer touches either side panel
        with self.assertRaisesRegex(ValueError,'attachment'):compile_general(plan)

    def test_books_move_with_shelves_without_deformation_and_restore(self):
        plan=shelf_plan();_,_,before=compile_general(plan)
        changed=edit_general(plan,'gap',.1);_,_,after=compile_general(changed)
        a,b=before['shelf_unit']['internal_geometry'],after['shelf_unit']['internal_geometry']
        self.assertEqual(a['assembly']['status'],'passed')
        self.assertEqual(b['assembly']['status'],'passed')
        self.assertAlmostEqual(b['parts']['book_high']['center'][2]-a['parts']['book_high']['center'][2],.1)
        self.assertEqual(a['parts']['book_low'],b['parts']['book_low'])
        for name in ('book_low','book_high'):
            self.assertEqual(a['parts'][name]['size'],b['parts'][name]['size'])
        _,_,restored=compile_general(edit_general(changed,'gap',-.1))
        import numpy as np
        for name in a['parts']:
            np.testing.assert_allclose(a['parts'][name]['bounds'],restored['shelf_unit']['internal_geometry']['parts'][name]['bounds'])

    def test_shrinking_rejects_before_render_without_mutation(self):
        plan=shelf_plan();original=plan.model_dump()
        with self.assertRaisesRegex(ValueError,'clearance|intersection'):edit_general(plan,'gap',-.1)
        self.assertEqual(plan.model_dump(),original)

    def test_rigid_part_cannot_resize_with_constant_outer_envelope(self):
        plan=shelf_plan();plan.objects[0].parts[-1].size=(.09,.22,'gap-.1')
        with self.assertRaisesRegex(ValueError,'rigid internal part'):edit_general(plan,'gap',.03)

    def test_floating_book_and_fake_root_rejected(self):
        plan=shelf_plan();plan.objects[0].parts[-1].center=(.24,0,.02)
        with self.assertRaisesRegex(ValueError,'support_gap'):compile_general(plan)
        # An elevated root is valid when its measured connected subassembly
        # reaches the floor through attached structural members.
        plan=shelf_plan();plan.objects[0].assembly.roots.append('shelf')
        _,_,receipts=compile_general(plan)
        self.assertEqual(receipts['shelf_unit']['internal_geometry']['assembly']['status'],'passed')

    def test_disconnected_elevated_root_is_rejected(self):
        import numpy as np
        from general_scene import Part,SceneObject
        from asset_physics import audit_assembly,PartRelation
        obj=SceneObject(id='desk',semantic='desk',size=(1,1,1),parts=[
            Part(id='floor_leg',primitive='box',size=(.1,.1,.1),center=(0,0,.05)),
            Part(id='loose_panel',primitive='box',size=(.1,.1,.1),center=(0,0,.55))],
            assembly={'roots':['floor_leg','loose_panel'],'relations':[]})
        def record(part,bounds):
            low,high=np.asarray(bounds,dtype=float)
            return {'primitive':'box','size':(high-low).tolist(),'center':((high+low)/2).tolist(),
                    'rotation':np.eye(3).tolist(),'bounds':bounds}
        records={'floor_leg':record(obj.parts[0],[[-.05,-.05,0],[.05,.05,.1]]),
                 'loose_panel':record(obj.parts[1],[[-.05,-.05,.5],[.05,.05,.6]])}
        report=audit_assembly(obj,records)
        self.assertIn('floating_root',{item['code'] for item in report['findings']})
        obj.assembly.relations=[PartRelation(kind='attached_to',part='loose_panel',target='floor_leg',
                                             reason='Verified shared face')]
        records['loose_panel']=record(obj.parts[1],[[-.05,-.05,.1],[.05,.05,.2]])
        report=audit_assembly(obj,records)
        self.assertNotIn('floating_root',{item['code'] for item in report['findings']})

    def test_clearance_relation_cannot_establish_load_path(self):
        plan=shelf_plan()
        plan.objects[0].assembly.relations=[r for r in plan.objects[0].assembly.relations if r.part!='book_low']
        with self.assertRaisesRegex(ValueError,'disconnected'):compile_general(plan)

    def test_rotated_boxes_not_false_collision_from_aabb(self):
        from asset_physics import separating_gap,rotation_matrix
        import numpy as np
        r=rotation_matrix((0,0,45))
        a={'center':[0,0,0],'size':[1,.1,.1],'rotation':r}
        b={**a,'center':(r@np.array([0,.2,0])).tolist()}
        self.assertAlmostEqual(separating_gap(a,b),.1)

    def test_straddling_member_settles_on_union_of_supports(self):
        """A top spanning two uprights is valid on their combined surfaces.

        Declaring only one upright leaves the far end carrying nothing, and that
        must still fail: the union rule widens what counts as contact, it does
        not accept a member hovering past its one declared support.
        """
        import types,numpy as np
        from asset_physics import PartRelation,AssemblyContract,audit_assembly
        def box(id,size,center):
            lo=np.array(center)-np.array(size)/2;hi=np.array(center)+np.array(size)/2
            return {'primitive':'box','size':list(size),'center':list(center),
                    'rotation':np.eye(3).tolist(),'bounds':[lo.tolist(),hi.tolist()]}
        records={
            'floor':box('floor',[2.0,1.0,.02],[0,0,-.01]),
            'side_l':box('side_l',[.05,.45,.50],[-.475,0,.25]),
            'side_r':box('side_r',[.05,.45,.50],[.475,0,.25]),
            'top':box('top',[1.0,.5,.03],[0,0,.515]),
        }
        parts=[types.SimpleNamespace(id=i,form=None,parent=None,mount='origin') for i in records]
        def contract(targets):
            return AssemblyContract(roots=['floor'],relations=[
                PartRelation(kind='attached_to',part='side_l',target='floor',reason='test joint'),
                PartRelation(kind='attached_to',part='side_r',target='floor',reason='test joint'),
                *[PartRelation(kind='supported_by',part='top',target=t,reason='test support')
                  for t in targets]])
        obj=types.SimpleNamespace(id='cabinet',parts=parts)
        obj.assembly=contract(['side_l','side_r'])
        result=audit_assembly(obj,records)
        self.assertEqual(result['findings'],[])
        obj.assembly=contract(['side_l'])
        result=audit_assembly(obj,records)
        codes=[f['code'] for f in result['findings']]
        self.assertIn('support_footprint',codes)
        measured=next(f for f in result['findings'] if f['code']=='support_footprint')['measured']
        self.assertEqual(measured['declared_support_count'],1)
        self.assertTrue(measured['child_needs_multi_support'])

    def test_support_targets_normalizes_single_and_multi(self):
        import types
        from asset_physics import support_targets
        part=lambda s:types.SimpleNamespace(support=s)
        self.assertEqual(support_targets(part(None)),[])
        self.assertEqual(support_targets(part('a')),['a'])
        self.assertEqual(support_targets(part(['a','b'])),['a','b'])
        self.assertEqual(support_targets(part(('a',''))),['a'])


if __name__=='__main__':unittest.main(verbosity=2)
