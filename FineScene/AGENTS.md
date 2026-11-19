# FineScene Development and Verification Boundaries

When improving or verifying scene-generation capability, you must follow the
user's requirement: after a prompt is entered, the FineScene Agent system
itself autonomously completes scene planning, component selection, asset
acquisition, aDSL construction, placement, geometry checks, the visual Judge
and repair.

- The assistant may only modify the general Agent, compiler, validators and
  run infrastructure. It must not hand-edit the scene coordinates, models,
  component list or materials of a particular run to improve the presented
  result.
  **The only exception is failure escalation**: when the same object or the
  same planning stage accumulates 5 failures (build, geometry/spec check or
  visual Judge not passing; FineScene's per-stage revision limit and forging
  attempt limit are both set to 5; each blocking-level rejection by the
  visual Judge in a round counts 1 for each named object; re-planning does
  not reset the count), the pipeline stops and writes
  `harness_handoff.json`. At that point the model in the harness hand-writes
  the repair for that one target directly:
  - Hand-write only the objects/stages listed in the handoff request; keep
    everything else as the Agent produced it;
  - The hand-written result must state its provenance: FineScene is run with
    `--planner replay --plan <file> --harness-repair <description.json>`,
    and `run_origin.json` records it; the video pipeline writes it under
    `hand_objects/<id>/` and notes in the `_note` of
    `blend_overrides.json` that it was hand-written by the harness;
  - The Agent's failed attempts are kept as evidence and must not be deleted
    or rewritten;
  - The hand-written result goes through the same geometry checks, spec
    checks and visual Judge as usual, with no lowering of the bar; if the
    Judge rejects it, keep repairing; it must not be marked as passed;
  - When delivering and reporting, describe the Agent-generated parts and the
    harness hand-written parts separately; a hand-written repair must not be
    presented as the Agent succeeding in one generation.
- The Agent's raw outputs and run evidence must remain traceable. Resumed
  execution may reuse checkpoints and caches that have been re-validated; an
  explicit replay must be labeled as such and must not pass as a complete
  generation from a fresh prompt.
- The general compiler and solver may compute geometry and constraint
  solutions from the Agent's semantic plan; they must not embed a
  hand-made arrangement of a specific room to stand in for planning.
- Visual acceptance and repair decisions must be made inside the Agent
  system. The assistant's own judgment from looking at images cannot replace
  the system Judge, and must not mark a rejected asset or scene as passed.
- The original user requirement takes precedence over narrative details the
  Agent itself conjectured. When repairing review-protocol errors, keep the
  real geometry, material, semantic and style requirements; the bar must not
  be lowered to make an example pass.
- Return all independent problems in a single round of feedback where
  possible; prioritize planning precision and repair targeting; keep work
  that has been verified and is unrelated to the failure.
- New TRELLIS inference is currently disabled on this machine; existing
  finished assets may be reused and suitable assets may be fetched online.
  Future server-side inference capability will be enabled through backend
  configuration; the local limitation must not be written up as a permanent
  limitation of the method.
- On delivery, give the actual Blender file, renders and the system review
  status. Tests passing, planning completed, a diagnostic candidate render
  and final acceptance must be distinguished truthfully.
