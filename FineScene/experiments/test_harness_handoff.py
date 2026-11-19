"""Escalation to the harness model after HANDOFF_ATTEMPTS failures of one object/stage."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'FineScene' / 'src'))
sys.path.insert(0, str(REPO / 'adsl_adapter'))
from harness_handoff import HANDOFF_ATTEMPTS, write_handoff  # noqa: E402


class HandoffTests(unittest.TestCase):
    def test_threshold_and_budgets_agree(self):
        self.assertEqual(HANDOFF_ATTEMPTS, 5)
        from staged_planner import StagedScenePlanner
        with tempfile.TemporaryDirectory() as folder:
            planner = StagedScenePlanner('unused', Path(folder), 'budget', complete=lambda *a, **k: None)
            self.assertEqual(planner.max_repairs, HANDOFF_ATTEMPTS)
        forge_src = (REPO / 'adsl_adapter' / 'furniture_forge.py').read_text(encoding='utf-8')
        self.assertIn('def forge(entry: dict, max_attempts: int = HANDOFF_ATTEMPTS,', forge_src)
        self.assertIn('default=HANDOFF_ATTEMPTS', forge_src)
        self.assertIn('"--attempts", type=int, default=5', (REPO / 'adsl_adapter' / 'video_pipeline.py').read_text(encoding='utf-8'))

    def test_write_handoff_keeps_one_open_request_per_target(self):
        with tempfile.TemporaryDirectory() as folder:
            d = Path(folder)
            write_handoff(d, pipeline='forge', target='fridge', stage='forge', attempts=5, reason='r1')
            write_handoff(d, pipeline='forge', target='sink', stage='forge', attempts=6, reason='r2',
                          evidence={'result': d / 'sink_result.json'}, repair_route='hand_objects/sink')
            data = json.loads((d / 'harness_handoff.json').read_text(encoding='utf-8'))
            self.assertEqual(set(data['requests']), {'fridge', 'sink'})
            sink = data['requests']['sink']
            self.assertEqual((sink['status'], sink['attempts'], sink['threshold']), ('open', 6, 5))
            self.assertIn('without lowering thresholds', sink['rules'])

    def test_planning_repair_limit_writes_a_handoff_request(self):
        from run_general_scene import run
        from staged_planner import PlanningRepairLimit
        from planning_diagnostics import Diagnostics
        with tempfile.TemporaryDirectory() as folder:
            args = SimpleNamespace(out=Path(folder) / 'run', planner='llm', plan_only=False, resume_runtime=None,
                                   prepared_assets_from=None, initial_feedback=None, replan_spatial=False)

            def attempt(value):
                runtime = value.out / 'agent_runtime'
                runtime.mkdir(parents=True)
                (runtime / 'frozen_request_contract.json').write_text('{}', encoding='utf-8')
                report = Diagnostics('recipe_table')
                report.add('sweep_expression', 'leg path outside envelope', component_ids=['table'])
                raise PlanningRepairLimit('recipe_table', 5, report.report())

            result = run(args, attempt_runner=attempt)
            self.assertEqual(result['stop_reason'], 'planning_repair_call_limit')
            handoff = json.loads(Path(result['harness_handoff']).read_text(encoding='utf-8'))
            request = handoff['requests']['recipe_table']
            self.assertEqual((request['pipeline'], request['attempts'], request['status']), ('finescene', 5, 'open'))
            self.assertIn('--harness-repair', request['repair_route'])

    def _rejecting_run(self, prior=None):
        sys.path.insert(0, str(REPO / 'FineScene' / 'experiments'))
        from test_visual_repair import plan_fixture, defect
        from scene_execution import execute_with_repairs
        calls = []

        def evaluate(candidate, assets, directory, *_):
            calls.append(directory)
            visual = defect()
            # A fresh (different) rejection each round, so only the handoff count stops the loop.
            visual['decision']['findings'][0]['evidence'] = f'Book placement still wrong (round {len(calls)}).'
            return {'accepted': False, 'geometry': {'status': 'passed', 'findings': []},
                    'visual': visual, 'directory': str(directory)}

        def repair(current, *_):
            return current.model_copy(deep=True), {'status': 'candidate_validated_pending_render'}

        folder = tempfile.mkdtemp()
        result = execute_with_repairs(plan_fixture(), {}, folder, 'brief', 'unused', 'unused', evaluator=evaluate,
                                      repairer=repair, prior_visual_rejections=prior)
        return result, calls, Path(folder)

    def test_visual_judge_rejections_count_toward_the_handoff(self):
        result, calls, folder = self._rejecting_run()
        self.assertEqual(len(calls), HANDOFF_ATTEMPTS)
        self.assertEqual(result['replan_reason'], 'visual_rejection_handoff:shelf_unit')
        self.assertEqual(result['visual_rejections'], {'shelf_unit': HANDOFF_ATTEMPTS})
        request = json.loads((folder / 'harness_handoff.json').read_text(encoding='utf-8'))['requests']['shelf_unit']
        self.assertEqual((request['stage'], request['attempts']), ('visual_judge', 5))
        self.assertTrue(request['last_findings'])

    def test_visual_rejections_carry_over_from_earlier_replans(self):
        result, calls, _ = self._rejecting_run(prior={'shelf_unit': 3})
        self.assertEqual(len(calls), 2)
        self.assertTrue(result['replan_reason'].startswith('visual_rejection_handoff'))


if __name__ == '__main__':
    unittest.main()
