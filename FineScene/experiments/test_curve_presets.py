"""Curve presets, tapered/closed sweeps and lathe parts, independent of category."""
import sys
from pathlib import Path
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from general_scene import GeneralPlan, compile_general, edit_general  # noqa: E402
from curve_presets import CURVE_HELP, PROFILE_HELP, EXPLICIT_PRESETS, capsules, lathe_sdf  # noqa: E402
from sweep_contacts import sweep_gap  # noqa: E402
from style_contract import StyleRequirement, StyleBinding, validate_style_bindings, capabilities  # noqa: E402

# Explicit points are offsets from the part center; the first one reaches the floor (z = -0.4 + radius).
EXPLICIT_POINTS = [[-.2, 0, -.38], [0, 0, .35], [.2, 0, -.1], [.25, 0, .3]]


def plan(part, size=(.6, .2, .8), parameters=None):
    return GeneralPlan.model_validate({'scene_id': 'curves', 'description': 'Curve presets',
        'extent': [3, 3, 3], 'parameters': parameters or {}, 'objects': [{
            'id': 'member', 'semantic': 'curved member', 'size': list(size),
            'parts': [{'id': 'curve', 'size': list(size), 'center': [0, 0, size[2] / 2], **part}]}]})


def record(p, part_id='curve'):
    _, _, receipt = compile_general(p)
    return receipt['member']['internal_geometry']['parts'][part_id]


def sweep_part(preset, **kw):
    curve = {'preset': preset, **kw}
    if preset in EXPLICIT_PRESETS:
        curve.setdefault('points', EXPLICIT_POINTS)
    if preset == 'helix':
        curve.setdefault('plane', 'xy')
    return {'primitive': 'sweep', 'radius': .02, 'curve': curve}


class CurvePresetTests(unittest.TestCase):
    def test_every_curve_preset_compiles_inside_its_envelope(self):
        for preset in CURVE_HELP:
            with self.subTest(preset=preset):
                size = (.3, .3, .8) if preset == 'helix' else (.6, .2, .8)
                r = record(plan(sweep_part(preset), size=size))
                path = np.asarray(r['path'])
                self.assertGreaterEqual(len(path), 4)
                low, high = np.asarray(r['bounds'])
                part_low = np.asarray([-size[0] / 2, -size[1] / 2, 0])
                self.assertTrue(np.all(low >= part_low - 1e-6) and np.all(high <= part_low + size + 1e-6))
                self.assertEqual(r['curve'], preset)
                if preset not in EXPLICIT_PRESETS:
                    # Fitted presets fill the envelope along their plane axes.
                    self.assertGreater((high - low)[2], .75)

    def test_every_preset_parameter_changes_the_geometry(self):
        # A parameter the fit normalizes away would be silently ignored; every
        # accepted parameter must move the compiled path.
        from curve_presets import CURVE_PARAMS
        values = {'turns': (1.2, 2.5), 'start_deg': (0, 40), 'end_deg': (120, 200), 'inner': (.2, .5),
                  'knee': (.6, .85), 'spout': (.15, .45), 'corner': (.2, .6), 'cycles': (1.5, 3), 'tension': (.4, 1.5)}
        for preset, accepted in CURVE_PARAMS.items():
            for name in sorted(accepted - {'points'}):
                with self.subTest(preset=preset, parameter=name):
                    size = (.3, .3, .8) if preset == 'helix' else (.6, .2, .8)
                    paths = [np.asarray(record(plan(sweep_part(preset, **{name: v}), size=size))['path']) for v in values[name]]
                    self.assertFalse(paths[0].shape == paths[1].shape and np.allclose(paths[0], paths[1], atol=1e-6),
                                     f'{preset}.{name} has no geometric effect')

    def test_closed_presets_return_to_their_start(self):
        for preset in ('ring', 'rounded_rect'):
            r = record(plan(sweep_part(preset)))
            self.assertTrue(r['closed'])
            np.testing.assert_allclose(r['path'][0], r['path'][-1], atol=1e-9)
        r = record(plan({**sweep_part('smooth'), 'closed': True}))
        np.testing.assert_allclose(r['path'][0], r['path'][-1], atol=1e-9)

    def test_preset_parameter_expression_is_editable_inside_fixed_envelope(self):
        p = plan(sweep_part('helix', turns='coil'), size=(.3, .3, .8), parameters={'coil': 3})
        before = record(p)
        edited = edit_general(p, 'coil', 1)
        after = record(edited)
        self.assertEqual(before['size'], after['size'])
        self.assertNotEqual(len(before['path']), len(after['path']))

    def test_taper_compiles_to_frustums_and_conservative_capsules(self):
        part = {**sweep_part('cabriole'), 'radius': .03, 'radius_end': .012}
        r = record(plan(part, size=(.2, .1, .7)))
        radii = np.asarray(r['radii'])
        self.assertAlmostEqual(radii[0], .03)
        self.assertAlmostEqual(radii[-1], .012)
        self.assertTrue(np.all(np.diff(radii) <= 1e-12))
        # The fit uses the actual tapered radii: the tube touches the floor
        # and the top of its envelope exactly (no floating thin end).
        low, high = np.asarray(r['bounds'])
        self.assertAlmostEqual(low[2], 0, places=4)
        self.assertAlmostEqual(high[2], .7, places=4)
        caps = capsules(r)
        self.assertGreater(len(caps), len(r['path']) - 1)
        self.assertTrue(all(c[2] <= .03 + 1e-12 for c in caps))
        _, _, receipt = compile_general(plan(part, size=(.2, .1, .7)))
        from general_scene import compile_local_geometry
        p = plan(part, size=(.2, .1, .7))
        shape = compile_local_geometry(p, p.objects[0])
        prims = list(shape.iter_primitives()) if hasattr(shape, 'iter_primitives') else []
        if prims:
            self.assertTrue(any('radius_end' in q['params'] for q in prims if q['type'] == 'cylinder'))

    def test_invalid_curve_specs_are_rejected_with_the_reason(self):
        cases = [
            ({**sweep_part('arc'), 'curve': {'preset': 'arc', 'turns': 2}}, 'does not use'),
            ({**sweep_part('ring'), 'closed': True}, 'already closed'),
            ({**sweep_part('ring'), 'radius_end': .01}, 'closed sweep'),
            ({**sweep_part('arc'), 'path': [[0, 0, 0], [.1, 0, 0]]}, 'exactly one of path'),
            ({**sweep_part('spiral', inner=2)}, 'inner'),
            ({'primitive': 'box', 'curve': {'preset': 'arc'}}, 'only valid'),
        ]
        for part, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(ValueError, message):
                    compile_general(plan(part))
        with self.assertRaisesRegex(ValueError, '2 x radius'):
            compile_general(plan({**sweep_part('arc'), 'radius': .2}))


class LatheTests(unittest.TestCase):
    def test_every_profile_preset_compiles_and_fills_its_envelope(self):
        for preset in PROFILE_HELP:
            with self.subTest(preset=preset):
                r = record(plan({'primitive': 'lathe', 'profile': {'preset': preset}}, size=(.3, .3, .6)))
                low, high = np.asarray(r['bounds'])
                np.testing.assert_allclose(high[2] - low[2], .6, atol=1e-6)
                self.assertLessEqual(high[0], .15 + 1e-6)
                self.assertGreater(high[0], .1)
                self.assertEqual(r['profile_preset'], preset)

    def test_explicit_profile_rotation_and_size_edit(self):
        # Turned axis laid along Y (rotated 90 deg about X), resting on the floor.
        part = {'primitive': 'lathe', 'profile': {'points': [[.1, '-hh'], [.05, 0], [.1, 'hh']]}, 'rotation': [90, 0, 0]}
        p = plan(part, size=(.2, .2, .4), parameters={'hh': .2})
        p.objects[0].size = (.2, .4, .2)
        p.objects[0].parts[0].center = (0, 0, .1)
        r = record(p)
        low, high = np.asarray(r['bounds'])
        # Rotated 90 deg about X: the turned axis now runs along Y.
        np.testing.assert_allclose(high[1] - low[1], .4, atol=1e-6)
        after = record(edit_general(p, 'hh', -.05, check_layout=False))
        self.assertNotEqual(r['profile'], after['profile'])

    def test_invalid_profiles_are_rejected(self):
        cases = [
            ({'primitive': 'lathe', 'profile': {'preset': 'vase'}}, (.3, .2, .6), 'x and y must be equal'),
            ({'primitive': 'lathe', 'profile': {'points': [[.3, -.1], [.1, .1]]}}, (.3, .3, .6), 'exceeds'),
            ({'primitive': 'lathe', 'profile': {'points': [[.1, .1], [.1, -.1]]}}, (.3, .3, .6), 'non-decreasing'),
            ({'primitive': 'lathe', 'profile': {'preset': 'vase'}, 'radius': .1}, (.3, .3, .6), 'only valid'),
            ({'primitive': 'lathe'}, (.3, .3, .6), 'requires profile'),
        ]
        for part, size, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(ValueError, message):
                    compile_general(plan(part, size=size))

    def test_lathe_signed_distance(self):
        profile = np.array([[.1, -.2], [.1, .2]])
        d = lathe_sdf(np.array([[0, 0, 0], [.15, 0, 0], [0, 0, .3], [.05, 0, 0]]), profile)
        np.testing.assert_allclose(d, [-.1, .05, .1, -.05], atol=1e-9)

    def test_pedestal_table_attachment_passes_and_gap_or_penetration_fails(self):
        from general_scene import Part
        from asset_physics import AssemblyContract
        p = plan({'primitive': 'lathe', 'profile': {'preset': 'pedestal'}}, size=(.5, .5, .7))
        obj = p.objects[0]
        obj.size = (.8, .8, .74)
        obj.parts.append(Part(id='top', primitive='box', size=(.8, .8, .04), center=(0, 0, .72)))
        obj.assembly = AssemblyContract.model_validate({'roots': ['curve'], 'relations': [
            {'kind': 'attached_to', 'part': 'top', 'target': 'curve', 'reason': 'top screwed to the pedestal capital'}]})
        compile_general(p)
        obj.parts[1].center = (0, 0, .73)
        with self.assertRaisesRegex(ValueError, 'attachment'):
            compile_general(p)
        obj.parts[1].center = (0, 0, .71)
        with self.assertRaisesRegex(ValueError, 'penetration|intersection|attachment'):
            compile_general(p)

    def test_lathe_cannot_be_a_rest_surface(self):
        from general_scene import Part
        from asset_physics import AssemblyContract
        p = plan({'primitive': 'lathe', 'profile': {'preset': 'pedestal'}}, size=(.5, .5, .7))
        obj = p.objects[0]
        obj.size = (.8, .8, .74)
        obj.parts.append(Part(id='top', primitive='box', size=(.8, .8, .04), center=(0, 0, .72)))
        obj.assembly = AssemblyContract.model_validate({'roots': ['curve'], 'relations': [
            {'kind': 'supported_by', 'part': 'top', 'target': 'curve', 'reason': 'rest'}]})
        with self.assertRaisesRegex(ValueError, 'unsupported_surface|horizontal flat'):
            compile_general(p)

    def test_sweep_to_lathe_contact_is_measured(self):
        I = np.eye(3)
        lathe = {'primitive': 'lathe', 'center': [0, 0, 0], 'rotation': I, 'size': [.2, .2, .4],
                 'profile': [[.1, -.2], [.1, .2]]}
        sweep = {'primitive': 'sweep', 'path': [[.13, 0, -.1], [.13, 0, .1]], 'radius': .03}
        self.assertAlmostEqual(sweep_gap(sweep, lathe), 0, places=6)
        sweep['path'] = [[.2, 0, -.1], [.2, 0, .1]]
        self.assertAlmostEqual(sweep_gap(lathe, sweep), .07, places=6)
        ellipsoid = {'primitive': 'sphere', 'center': [0, 0, .3], 'size': [.1, .2, .1], 'rotation': I}
        self.assertIsNone(sweep_gap(lathe, ellipsoid))


class ContractTests(unittest.TestCase):
    def test_capabilities_list_presets_and_lathe(self):
        c = capabilities()
        self.assertIn('lathe', c['parametric_geometry'])
        self.assertEqual(set(c['sweep_curve_presets']), set(CURVE_HELP))
        self.assertEqual(set(c['lathe_profile_presets']), set(PROFILE_HELP))
        self.assertNotIn('variable-radius sweep', c['unavailable_parametric_geometry'])

    def test_lathe_part_satisfies_curved_style_binding(self):
        requirement = StyleRequirement(id='turned', feature='Turned baluster legs', visible_evidence='Round turned profile with rings', geometry_need='swept_path')
        binding = StyleBinding(requirement_id='turned', component_ids=['member'], implementation='Lathe the legs from a baluster profile', part_ids=['member/curve'])
        p = plan({'primitive': 'lathe', 'profile': {'preset': 'baluster'}}, size=(.3, .3, .6))
        validate_style_bindings([requirement], [binding], p.objects, source_mode=lambda o: o.source.mode)


if __name__ == '__main__':
    unittest.main()
