# Design Replanning Scope — 2026-10-25

## Root cause

The reading-lounge run `runs/refined_reading_lounge_20261025_49/` failed before
Blender export. Its detailed layout preflight returned 72 independent findings
across the baseline and requested edit states. The outer runner escalated to a
decomposition/source replan.

That replan regenerated both `scene_design` and `design_feasibility` even though
the evidence was geometric. The new design omitted the previously accepted
`console_table` and `arched_mirror`, while source-replan validation required
their validated parametric specifications to remain. Decomposition repair then
oscillated between restoring these objects and deleting them as unbound design
objects. The original run stopped after 14 repairs on a repeated failed state;
it never reached Blender or scene Visual Judge.

## Change

`scene_design_revision_required` now distinguishes scene-level visual/style
rejection from layout and geometry failure. Geometry-only retries keep the
accepted design context stable, reuse the matching source-option snapshot, and
allow decomposition to receive the full failure batch. Explicit scene-level
visual rejection still reopens design; unresolved source acquisition can still
revise source feasibility.

Regression coverage executes the planner twice: the first execution writes
accepted design checkpoints, and the second resumes them with a full layout
failure while making no new design-model or source-search calls.

The outer retry loop now includes stage and issue diagnostics in its repeated
failure signature. Candidate artifact hashes remain in each saved
`replan_failure_state.json` for provenance but do not turn an identical failure
into a new state. This preserves unlimited correction attempts for genuinely
changed diagnostics while stopping when the same complete failure returns.

## Verification

- `python -m unittest experiments.test_staged_planner experiments.test_scene_composition experiments.test_scene_design experiments.test_style_contract`: 95 tests passed.
- `python -m py_compile src/scene_design.py src/staged_planner.py src/run_general_scene.py experiments/test_scene_design.py experiments/test_style_contract.py`: passed.
- `python src/run_general_scene.py --help`: passed.

## Run status and continuation

At 17:59 local time, the original process for run 49 had recorded two rejected
rounds and created `replan_02`. Its source snapshot predates this fix, so its
output remains diagnostic evidence and must not be treated as a result of the
new replanning policy. Preserve its raw proposals and stage events.

After that process exits, continue from the original accepted runtime and pass
the complete rejected attempt report as initial feedback. Use a new output
directory. The corrected policy should reuse the accepted scene design and
source options, then revise the failed decomposition/layout. Acceptance still
requires an actual Blender file, measured geometry, rendered images and a
passing system Visual Judge; a repaired plan alone is not scene acceptance.
