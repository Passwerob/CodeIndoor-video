"""Scene-level visual acceptance for the legacy acme run directory.

This module used to carry its own visual schema, which drifted from the schema
the repair pipeline actually consumes: its findings had no part_ids and no
style_assessments, so a correct visual complaint could not be routed to a local
repair. It now delegates to general_vision, keeping only the acme-specific
evidence discovery and the report shape older run directories expect.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path

from general_vision import review_scene_images


def _scene_request(contract):
    """Translate the acme scene contract into the shared review request."""
    return {
        'original_user_request': contract.get('brief', ''),
        'quality_target': 'photorealistic' if contract.get('quality_target') == 'photorealistic' else 'prototype',
        'components': [{'id': item.get('component_id') or item.get('id'),
                        'semantic': item.get('semantic', ''),
                        'required_features': item.get('required_features', []),
                        'materials': item.get('material_requirements', [])}
                       for item in contract.get('furniture_tasks', []) + contract.get('decor_tasks', [])
                       if item.get('component_id') or item.get('id')],
        'style_requirements': contract.get('style_requirements', []),
        'layout_audit_required': True,
    }


def judge_visual(output_dir: Path, model_profile: Path) -> dict:
    output_dir = Path(output_dir)
    contract = json.loads((output_dir / 'resolved_scene_contract.json').read_text(encoding='utf-8'))
    execution = json.loads((output_dir / 'blender_export/execution.json').read_text(encoding='utf-8'))
    images = [Path(p) for p in execution.get('render_paths', [])]
    evidence = [{'path': str(p), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
                for p in images if p.is_file()]
    result = {'status': 'unavailable', 'accepted': False, 'backend': 'vision_language_model',
              'model_profile': str(model_profile), 'images': evidence}
    try:
        if len(images) < 3 or any(not p.is_file() for p in images):
            # A judge that silently scores two views accepts a scene it never
            # saw. The whole-scene layout audit needs the opposing views too.
            raise ValueError('Full room, top view and closeup are required')
        quality = 'photorealistic' if contract.get('quality_target') == 'photorealistic' else 'prototype'
        report = review_scene_images([str(p) for p in images], _scene_request(contract),
                                     model_profile, output_dir / 'visual_judge.json',
                                     quality=quality)
        decision = report.get('decision', {})
        blocking = any(f.get('severity') == 'blocking' for f in decision.get('findings', []))
        approved = bool(report.get('accepted')) and decision.get('verdict') == 'passed' and not blocking
        result.update(status='passed' if approved else
                      'unavailable' if report.get('status') == 'unavailable' else 'needs_revision',
                      accepted=approved, decision=decision,
                      images=report.get('images', evidence),
                      batch_count=report.get('batch_count'),
                      unreviewed_images=report.get('unreviewed_images', []))
    except Exception as error:
        result.update(error_type=type(error).__name__, error=str(error)[:1200])
    (output_dir / 'visual_judge.json').write_text(json.dumps(result, ensure_ascii=False, indent=2),
                                                  encoding='utf-8')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--model-profile', required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(judge_visual(args.output, args.model_profile), ensure_ascii=False, indent=2))
