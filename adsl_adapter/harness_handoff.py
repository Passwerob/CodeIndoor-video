"""Escalation to the harness model after repeated downstream failures.

Policy (see .claude/skills/codeindoor/SKILL.md, "Failure escalation"): when the same
object -- or the same planning stage -- has failed HANDOFF_ATTEMPTS times
(build, spec/geometry checks or the visual Judge), the pipeline stops retrying
and writes a handoff request. The harness model (Claude Code) then hand-writes
that object's program or recipe, marks it as harness-authored, and runs it
through the same checks; nothing is relaxed and the agent's failed attempts
stay on record.

Standard library only (also imported from Blender-side and FineScene code).
"""
from __future__ import annotations

import json
import time
from pathlib import Path

HANDOFF_ATTEMPTS = 5
SCHEMA = 'codeindoor.harness_handoff/1'


def write_handoff(directory: Path, *, pipeline: str, target: str, stage: str, attempts: int,
                  reason: str, evidence: dict[str, str] | None = None, last_findings=None,
                  repair_route: str = '') -> Path:
    """Write ``<directory>/harness_handoff.json`` (one entry per target, newest wins).

    ``evidence`` maps a label to a file/directory with the failed attempts;
    ``repair_route`` says how a hand-written result re-enters the pipeline.
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / 'harness_handoff.json'
    data = {'schema': SCHEMA, 'requests': {}}
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            pass
    data.setdefault('requests', {})[target] = {
        'pipeline': pipeline, 'target': target, 'stage': stage, 'attempts': int(attempts),
        'threshold': HANDOFF_ATTEMPTS, 'reason': reason,
        'evidence': {k: str(v) for k, v in (evidence or {}).items()},
        'last_findings': last_findings, 'repair_route': repair_route,
        'status': 'open', 'created': time.strftime('%Y-%m-%dT%H:%M:%S'),
        'rules': ('Hand-write only this target; keep the agent attempts as evidence; mark the result '
                  'harness-authored; run the same spec/geometry/visual checks without lowering thresholds; '
                  'report agent-generated and harness-written parts separately.'),
    }
    data['schema'] = SCHEMA
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    return path
