# Living-Room Entrance Circulation Misjudgment Debug Record

## Symptom

`spatial_design preflight failed: [baseline][unrealized_group_link] [composition_lowering] link_arrival_route: circulation link needs a route connecting the two intended groups`

Original run: `runs/living_room_probe_20261028_01`, natural-language living-room request, generic staged Agent, `configs/deepseek-flash.yaml`, new TRELLIS inference disabled. The initial candidate and two replans all stopped at spatial design, before assembly or rendering. The working directory has no Git metadata; the pre-fix source is saved in `system_source/` of the original run, and the original candidate was not rewritten.

## Localization and Evidence

The originally rejected candidate actually contained a route `entry_opening → family_sofa_access`, and the entrance's `swing_leaf_ids` explicitly referenced `entry_door` in the architecture group. However, the route lookup and the inter-group link validation in `scene_composition.py` built the group node set only from `access_areas`, so the architecture group had no starting point. Another branch skipped the circulation route search whenever a cross-group spatial relation existed, but a spatial relation cannot replace a circulation route.

Two hypotheses:

1. The Agent did not generate a route: refuted by the real route and door-leaf binding in the original `spatial_plan_failure_1.json`.
2. The compile/validate step dropped the entrance node: the minimal test failed in all four direction/binding variants, and the original living-room candidate also consistently reported the same error through `lower_typed_relationships → validate_lowering`, supporting this mechanism.

## Generic Fix

`src/scene_composition.py` now extracts a group's route nodes uniformly: operation areas are attributed by owner; entrances are attributed by the real intersection of `fitted_component_ids` / `swing_leaf_ids` with the group's members. Only entrances are admitted; a window or an opening with only a text label is not treated as a circulation entrance. Circulation links always search for a real route, even when a cross-group spatial relation already exists.

This is a root-cause fix of the inter-group circulation topology judgment; it does not change scene objects, sizes, positions, materials or any doorway/route geometry. It does not prove the passage is collision-free; subsequent layout, Blender measurement and visual review must still be performed.

## Verification

- The new regression tests are in `experiments/test_scene_composition.py`: door-leaf and door-frame entrance attribution, forward and reverse group links, circulation binding when a spatial relation already exists, and the rejection paths for windows / unattributed entrances / missing routes.
- New tests before the fix: 5 failures (including subtests); after the fix, the two related test files: 57 passed, 7 subtests passed.
- The original living-room candidate re-ran inter-group relationship compilation and validation: passed; its frozen circulation program, recompiled, used `main_aisle`: passed. Checked in memory only; no run JSON was edited.
- Full regression: 498 passed, 23 subtests passed, 8 existing Pydantic serialization warnings. Recorded in `runs/living_room_probe_20261028_01/regression_after_fix.txt`.
- Checkpoint resume of the full pipeline: `runs/living_room_probe_20261028_02`. The original entrance misjudgment is gone and all parametric component recipes passed their own checks, but detailed assembly still failed on a window-frame wall-contact coverage error and a door component going out of bounds; after two replans the system stopped with `repeated_replanning_failure`. There was no Blender/GLB or rendering, and no visual acceptance. This fix only verifies that the inter-group entrance route defect is gone; it cannot be claimed that the original full generation task passed.

All temporary tests and outputs were directed into `E:/3DUse`. No runtime debug prints were added.
