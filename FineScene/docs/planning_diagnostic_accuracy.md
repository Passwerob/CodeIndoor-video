# Planning Diagnostic Accuracy Revision (2026-10-23)

## Goal

Reduce the back-and-forth of "expose only one error → call the model once → expose the next error".
This change did not adjust the repair-attempt limit, and no layout solver was added. The changes target check coverage,
error localization, complete feedback and re-checking; the first-attempt success rate for arbitrary scenes cannot be inferred from a single trial run.

## Implemented

`planning_diagnostics.py` provides unified diagnostics: issue code, stable in-candidate identifier, related objects,
JSON path, edit state, and, where applicable, the measured and expected values. Dependent checks that cannot be executed are listed separately in
`blocked_checks` and must not be counted as passed. Semantic errors wrapped by Pydantic still keep structured issues,
and field errors are also listed in full.

- Decomposition: aggregates errors in requirement coverage, provenance, internal decomposition, parent-child relationships, support height and rigid edits.
  A parent-child graph error blocks the support-chain computation, but does not block the independent size and rigid-edit checks.
- Style binding: aggregates errors in each style realization and its provenance; an unknown object blocks only the realization check of the corresponding binding.
- Spatial contract: aggregates errors in zoning, operation areas, entrances, widths, clear heights, routes and connectivity.
- Coarse layout: after a contract check fails, the computable occupancy checks are still executed; the baseline and all declared edits are checked in full.
  An expression error in one root component does not block computing other root components; child components that depend on it are marked blocked.
- Component recipe: aggregates missing parts, rigidity, support/clearance declarations, expressions, assembly, full envelope and edit issues.
  Real sizes are checked at the recipe stage, to prevent an undersized part from being discovered only after asset acquisition or Blender.
  Batched building requires the full envelope only at the full-component stage; a part missing from the model's output is not mistaken for a legitimate partial stage.
  Each edit state is checked separately, and deformations of rigid parts are reported one by one.
- Detailed plan: expression/assembly errors of different objects and scene-relationship errors are collected as far as possible, and the scene baseline and
  all edit states are re-checked together. A world bounding box that cannot be computed explicitly blocks the relationship checks that depend on it.

Repair requests carry the full `diagnostic_report`, mapping issue paths to the existing modifiable fields.
Error text is no longer truncated to 6000 characters; the Agent is required to handle all independent issues in one coordinated patch.
The patch's modification permissions keep the original freeze rules. After an illegal-path or no-change patch is rejected, the original candidate's geometric
diagnostics are still sent into the next request. Passing must come from a full re-check; the model's claim that it has fixed things does not count as passing.

`planning_quality_metrics.json` records the number of first-candidate passing stages, number of checked stages, number of calls, number of repair calls,
model time and the number of issues/blocked items per batch. Failed stages count in the denominator, and reused stages are listed separately. This metric is a stage-level check,
not a success rate or visual-acceptance rate for arbitrary scenes.

## Regression and Real Agent Evidence

130 related tests passed, including 12 new diagnostic tests: multi-category errors reported together, multiple expressions, multiple edits,
independent root components continuing to be checked, a complete long report, an illegal patch keeping the original diagnostics, and one patch fixing several sizes at once.
The old test case where "internal parameters are editable" itself had an envelope mismatch, and was corrected to a valid case with real size changes within the envelope.

The real input comes from `runs/scene_planning_discussion_20261023_04/agent_runtime`; the original
layout or chair coordinates were not hand-edited. Replay evidence is in `runs/planning_diagnostics_probe_20261023_03/`:

| Check | Result |
| --- | --- |
| Original recipes of the table and the south chair | Both old and new checks pass |
| Original recipe of the north chair | Old check passes; the new check finds early a depth of 0.50 m, expected 0.55 m |
| North chair baseline and three edit states | The same batch report returns 4 size deviations |
| Original coarse layout | The same batch report returns 9 conflicts, including edit states |
| DeepSeek Agent repairs the north chair | 1 real repair call, 14.555 s; baseline and all edits pass the re-check |

`diagnostics.json` stores the comparison results, `agent_runtime/` stores the full requests, model patches and re-check records,
and `repaired_recipe.json` is the Agent's own output. `current_system_revalidation.json` records the re-validation of the same repaired recipe under later
supplementary checks, together with the fingerprints of the current source files. The old-validator comparison uses archived source files and the
current geometry backend; it is not a full reconstruction of the old environment. The dynamic module-loading issue in the earlier `_01` and the inconsistent experiment
path list in `_02` are both kept in their original directories; the official `_03` reuses the production system's `recipe_repair_paths`.

Reproduction example (the output directory must be new):

```powershell
& E:/3DUse/aDSL/venv/Scripts/python.exe experiments/run_planning_diagnostics_probe.py --source-runtime runs/scene_planning_discussion_20261023_04/agent_runtime --out runs/NEW_PROBE --repair-component chair_north
```

Regression command:

```powershell
& E:/3DUse/aDSL/venv/Scripts/python.exe -m unittest experiments.test_planning_diagnostics experiments.test_scene_spatial experiments.test_staged_planner experiments.test_general_planner experiments.test_visual_repair experiments.test_asset_physics experiments.test_sweep_geometry
```

## Scope

"Complete" means the independently computable checks of the same stage in the current candidate; it does not include meshes not yet generated or visual
evidence not yet rendered. Field or dependency errors may block downstream checks, so it cannot be promised that all potential issues are exhausted the first time.
This change did not re-run Blender or the full-scene visual acceptance, and `accepted_scene=false`.
The visual Judge, measured geometry and edit regression remain required steps of final acceptance.
