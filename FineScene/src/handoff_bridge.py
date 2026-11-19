"""FineScene access to the repository-wide harness handoff (adsl_adapter/harness_handoff.py).

FineScene normally sits inside the CodeIndoor repository; when it is used on
its own the same schema is written by a local fallback, so the escalation
contract never silently disappears.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

_ADAPTER = Path(__file__).resolve().parents[2] / 'adsl_adapter'
try:
    if str(_ADAPTER) not in sys.path:
        sys.path.insert(0, str(_ADAPTER))
    from harness_handoff import HANDOFF_ATTEMPTS, write_handoff  # noqa: F401
except ImportError:  # FineScene outside the CodeIndoor repository
    HANDOFF_ATTEMPTS = 5

    def write_handoff(directory, *, pipeline, target, stage, attempts, reason, evidence=None,
                      last_findings=None, repair_route=''):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / 'harness_handoff.json'
        data = json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}
        data['schema'] = 'codeindoor.harness_handoff/1'
        data.setdefault('requests', {})[target] = {
            'pipeline': pipeline, 'target': target, 'stage': stage, 'attempts': attempts,
            'threshold': HANDOFF_ATTEMPTS, 'reason': reason,
            'evidence': {k: str(v) for k, v in (evidence or {}).items()}, 'last_findings': last_findings,
            'repair_route': repair_route, 'status': 'open', 'created': time.strftime('%Y-%m-%dT%H:%M:%S')}
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
        return path


FINESCENE_REPAIR_ROUTE = (
    'hand-write the rejected component(s) in a complete planner_output.json (start from the latest '
    'candidate), then run run_general_scene.py --planner replay --plan <file> --harness-repair <notes.json> '
    'so run_origin.json records them as harness-authored; the same geometry checks and visual Judge apply')


def count_visual_rejections(visual: dict, counts: dict) -> list[str]:
    """Add one rejection per component named by a blocking Judge finding this round."""
    decision = (visual or {}).get('decision', visual or {})
    hit = sorted({cid for f in decision.get('findings', []) if f.get('severity') == 'blocking'
                  for cid in f.get('component_ids', [])})
    for cid in hit:
        counts[cid] = counts.get(cid, 0) + 1
    return hit
