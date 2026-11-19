"""Whole-object consistency: part copies (like/mirror) and declared symmetry."""
import copy
import sys
from pathlib import Path
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from general_scene import GeneralPlan, compile_general, edit_general  # noqa: E402
from part_consistency import audit_symmetry, mirror_rotation  # noqa: E402
from asset_physics import rotation_matrix  # noqa: E402

LEG = {'primitive': 'sweep', 'size': [.1, .06, .71], 'radius': .025, 'radius_end': .012,
       'curve': {'preset': 'cabriole', 'plane': 'xz', 'knee': 'leg_knee'}, 'color': [.4, .25, .15], 'finish': 'wood'}


def table(symmetry='xy', overrides=None):
    legs = [
        {'id': 'leg_fl', **LEG, 'center': [-.33, -.25, .355]},
        {'id': 'leg_fr', **LEG, 'center': [.33, -.25, .355], 'like': 'leg_fl', 'mirror': 'x'},
        {'id': 'leg_bl', **LEG, 'center': [-.33, .25, .355], 'like': 'leg_fl', 'mirror': 'y'},
        {'id': 'leg_br', **LEG, 'center': [.33, .25, .355], 'like': 'leg_fl', 'mirror': 'xy'},
    ]
    for leg in legs:
        leg.update((overrides or {}).get(leg['id'], {}))
    ids = [leg['id'] for leg in legs]
    return GeneralPlan.model_validate({'scene_id': 'consistency', 'description': 'side table', 'extent': [3, 3, 3],
        'parameters': {'leg_knee': .75},
        'objects': [{'id': 'table', 'semantic': 'side table', 'size': [.8, .6, .75],
                     'parts': [*legs, {'id': 'top', 'primitive': 'box', 'size': [.8, .6, .04], 'center': [0, 0, .73]}],
                     'assembly': {'roots': ids, 'symmetry': symmetry, 'relations': [
                         {'kind': 'attached_to', 'part': 'top', 'target': leg, 'reason': 'top fixed on the leg'} for leg in ids]}}]})


def parts(plan):
    _, _, receipt = compile_general(plan)
    return receipt['table']['internal_geometry']['parts']


class CopyTests(unittest.TestCase):
    def test_mirrored_copies_are_exact_mirror_images(self):
        r = parts(table())
        fl = np.asarray(r['leg_fl']['path'])
        np.testing.assert_allclose(np.asarray(r['leg_fr']['path']), fl * [-1, 1, 1], atol=1e-9)
        np.testing.assert_allclose(np.asarray(r['leg_bl']['path']), fl * [1, -1, 1], atol=1e-9)
        np.testing.assert_allclose(np.asarray(r['leg_br']['path']), fl * [-1, -1, 1], atol=1e-9)
        self.assertEqual(r['leg_br']['like'], 'leg_fl')
        self.assertEqual(r['leg_br']['radii'], r['leg_fl']['radii'])

    def test_shared_parameter_edit_changes_every_copy_and_keeps_symmetry(self):
        p = table()
        before = parts(p)
        after = parts(edit_general(p, 'leg_knee', -.2))
        for leg in ('leg_fl', 'leg_fr', 'leg_bl', 'leg_br'):
            self.assertFalse(np.allclose(before[leg]['path'], after[leg]['path']))
        self.assertEqual(audit_symmetry(after, 'xy'), [])

    def test_copy_with_a_different_design_is_rejected_with_every_field(self):
        p = table(overrides={'leg_fr': {'radius': .03, 'curve': {'preset': 'cabriole', 'knee': .5}}})
        with self.assertRaises(ValueError) as caught:
            compile_general(p)
        message = str(caught.exception)
        self.assertIn('leg_fr.radius must repeat leg_fl.radius', message)
        self.assertIn('leg_fr.curve must repeat leg_fl.curve', message)

    def test_mirrored_copy_with_wrong_center_reports_the_expected_center(self):
        p = table(overrides={'leg_br': {'center': [.3, .25, .355]}})
        with self.assertRaisesRegex(ValueError, r'leg_br.center must be leg_fl.center mirrored \(xy\): expected \[0.33, 0.25, 0.355\]'):
            compile_general(p)

    def test_copy_rules(self):
        with self.assertRaisesRegex(ValueError, 'mirror requires like'):
            table(overrides={'leg_fr': {'like': None}})
        with self.assertRaisesRegex(ValueError, 'original design'):
            compile_general(table(overrides={'leg_br': {'like': 'leg_fr'}}))
        with self.assertRaisesRegex(ValueError, 'not a part'):
            compile_general(table(overrides={'leg_br': {'like': 'missing'}}))

    def test_mirror_rotation_is_the_conjugated_rotation(self):
        rot = (20, 35, -50)
        for mode, M in (('x', np.diag([-1, 1, 1])), ('y', np.diag([1, -1, 1])), ('xy', np.diag([-1, -1, 1]))):
            np.testing.assert_allclose(rotation_matrix(mirror_rotation(rot, mode)), M @ rotation_matrix(rot) @ M, atol=1e-12)


class SymmetryTests(unittest.TestCase):
    def test_declared_symmetry_catches_a_hand_written_odd_leg(self):
        # leg_br written by hand (not a copy) with the knee facing the wrong way.
        odd = {'like': None, 'mirror': 'none', 'curve': {'preset': 'cabriole', 'plane': 'xz', 'knee': 'leg_knee', 'flip': False}}
        p = table(overrides={'leg_br': odd})
        with self.assertRaisesRegex(ValueError, 'asymmetric_part'):
            compile_general(p)
        # The same object without a symmetry claim compiles: the check is opt-in.
        compile_general(table(symmetry='none', overrides={'leg_br': odd}))

    def test_hand_written_but_correct_mirror_passes(self):
        good = {'like': None, 'mirror': 'none', 'curve': {'preset': 'cabriole', 'plane': 'xz', 'knee': 'leg_knee', 'flip': True}}
        compile_general(table(overrides={'leg_br': good}))


class RepairTests(unittest.TestCase):
    def test_repair_paths_extend_to_the_whole_copy_family(self):
        from general_repairs import _extend_to_copy_families
        p = table()
        legs = p.objects[0].parts
        paths = {'/components/0/parts/1/radius', '/components/0/parts/0/center'}
        _extend_to_copy_families(paths, 0, legs)
        for j in range(4):
            self.assertIn(f'/components/0/parts/{j}/radius', paths)
        for j in (1, 2, 3):
            self.assertIn(f'/components/0/parts/{j}/center', paths)
        self.assertNotIn('/components/0/parts/4/radius', paths)


if __name__ == '__main__':
    unittest.main()
