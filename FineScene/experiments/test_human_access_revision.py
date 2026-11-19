"""Human-use inference can be reviewed without weakening physical clearances."""
import copy
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from staged_planner import (AccessFaces,CirculationProgram,HumanUseRevision,RequestContract,
                            seated_surface_pairs,validate_human_use_revision,
                            validate_seated_chair_faces,validate_seated_surface_access,
                            human_access_failure)


class HumanAccessRevisionTests(unittest.TestCase):
    def setUp(self):
        self.faces=AccessFaces(allowed_sides={'seat':['front']},reasons={'seat':'Initial entry assumption.'})
        self.program=CirculationProgram(approaches=[{'owner_id':'seat','side':'front','size':[.6,.55],
            'final_route_width':.5,'reason':'Room for an adult to enter the seating activity.'}])
        self.contract=RequestContract(asset_boundary='scene',quality_target='realistic')
        self.revised=HumanUseRevision(functional_faces=AccessFaces(
            allowed_sides={'seat':['left']},reasons={'seat':'The open left side admits entry beside the working partner.'}),
            circulation=CirculationProgram(approaches=[{'owner_id':'seat','side':'left','along_face':.1,
                'size':[.6,.55],'final_route_width':.5,
                'reason':'Use the verified open side while keeping the approved adult clearance.'}]),
            reason='Review the approach of interacting objects without changing their geometry.')

    def test_review_changes_inferred_side_and_preserves_all_clearance_dimensions(self):
        result=validate_human_use_revision(self.revised,self.faces,self.program,self.contract)
        self.assertEqual(result.circulation.approaches[0].side,'left')
        self.assertEqual(result.circulation.approaches[0].size,self.program.approaches[0].size)

    def test_review_rejects_smaller_person_area_or_route(self):
        for key,value in [('size',(.4,.55)),('final_route_width',.4)]:
            bad=self.revised.model_copy(deep=True);setattr(bad.circulation.approaches[0],key,value)
            with self.assertRaisesRegex(ValueError,'sizes or route widths'):
                validate_human_use_revision(bad,self.faces,self.program,self.contract)

    def test_paired_task_chair_rejects_front_entry(self):
        with self.assertRaisesRegex(ValueError,'front faces its paired desk/table'):
            validate_seated_chair_faces(self.faces,{'seat'})
        bad=self.revised.model_copy(deep=True)
        bad.functional_faces.allowed_sides['seat']=['front']
        bad.functional_faces.reasons['seat']='Front entry was assumed to be the chair approach.'
        bad.circulation.approaches[0].side='front'
        with self.assertRaisesRegex(ValueError,'cannot be an entry/pullback side'):
            validate_human_use_revision(bad,self.faces,self.program,self.contract,
                                        forbid_front_owners={'seat'})

    def test_seated_surface_uses_its_chair_approach_once(self):
        from types import SimpleNamespace
        design=SimpleNamespace(
            choices=[SimpleNamespace(id='study_chair',activity='study',selection='task chair'),
                     SimpleNamespace(id='study_desk',activity='writing',selection='writing desk')],
            composition=SimpleNamespace(groups=[SimpleNamespace(relationships=[SimpleNamespace(
                kind='facing',a_choice_id='study_chair',b_choice_id='study_desk')])]))
        outline=SimpleNamespace(
            design_bindings={'study_chair':['chair'],'study_desk':['desk']},
            objects=[SimpleNamespace(id='chair',parent=None,collision_role='solid'),
                     SimpleNamespace(id='desk',parent=None,collision_role='solid')])
        self.assertEqual(seated_surface_pairs(design,outline),[('chair','desk')])
        bindings=SimpleNamespace(bindings=[SimpleNamespace(id='chair',access_required=True),
                                           SimpleNamespace(id='desk',access_required=True)])
        with self.assertRaisesRegex(ValueError,'paired chair chair occupies the seated-use approach'):
            validate_seated_surface_access(outline,bindings,design,'High school bedroom with a desk and chair')
        bindings.bindings[1].access_required=False
        self.assertIs(validate_seated_surface_access(outline,bindings,design,
            'High school bedroom with a desk and chair'),bindings)
        bindings.bindings[1].access_required=True
        self.assertIs(validate_seated_surface_access(outline,bindings,design,
            'High school bedroom with a standing desk and chair'),bindings)

    def test_review_cannot_remove_a_functional_owner(self):
        bad=self.revised.model_copy(deep=True);bad.circulation.approaches=[]
        with self.assertRaisesRegex(ValueError,'approach owners'):
            validate_human_use_revision(bad,self.faces,self.program,self.contract)

    def test_named_solver_access_conflict_is_reviewed_without_mutating_evidence(self):
        spatial=SimpleNamespace(access_areas=[SimpleNamespace(id='use_seat')])
        evidence={'numerical_solver':{'incompatible_constraints':[
            'baseline clear approach use_seat: partner'],
            'independent_conflict_groups':[['baseline clear approach use_seat: partner']],
            'joint_corridor_failure':{'incompatible_constraints':['baseline scene bounds: other']}}}
        before=copy.deepcopy(evidence)
        self.assertTrue(human_access_failure(evidence,spatial))
        self.assertEqual(evidence,before)
        compact={'numerical_solver':{'incompatible_constraints':[
            {'constraint':'clear approach use_seat: partner','scenarios':['baseline']}],
            'independent_conflict_groups':[['clear approach use_seat: partner']]}}
        self.assertTrue(human_access_failure(compact,spatial))
        mixed={'issues':[{'code':'use_seat'}],'numerical_solver':{'independent_conflict_groups':[
            ['clear approach use_seat: partner','scene bounds: other']]}}
        self.assertFalse(human_access_failure(mixed,spatial))
        self.assertFalse(human_access_failure({'issues':[{'code':'unrelated_zone'}]},spatial))


if __name__=='__main__':unittest.main()
