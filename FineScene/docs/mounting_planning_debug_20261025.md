# Debug Report — mounting repairs lacked owners and used the wrong repair scope

## Symptom and cause

Run 43 completed every recipe, but detailed layout repeatedly received only `Mounting contact crosses an opening or lies outside actual parent surface; revise tangential placement`. The report had no component ID or measured patch. The first exception also hid independent mounting failures.

The unmodified saved layout actually failed for both `entrance_door` and `sash_window`. `_planned_envelopes_and_poses` allowed an unannotated surface exception to terminate placement; `to_general_plan` replaced it with a generic issue. This lost the identities and evidence required for precise repair.

`surface_mounting.resolve` now reports selected faces, local patch, support rectangles, plane and offsets. Placement collects independent mounting failures with exact component paths; descendants of failed poses are recorded as blocked rather than assigned guessed positions. Detailed preflight preserves this structured report. The original layout revalidation is recorded in `docs/evidence/mounting_diagnostics_revalidation_20261025.json`. Two generic regressions failed before the fix and pass afterwards.

Run 44's first Agent repair then fixed the door. The remaining window sill contact measured 1.75 x 0.18 m, against a 1.6 x 0.12 m support. Full coverage is impossible through translation or yaw because contact area exceeds even the support's bounding rectangle area. Continuing position-only repair could not solve an inferred interface choice.

## Capacity routing

The surface checker now distinguishes this proven `mounting_interface_capacity` condition from ordinary coverage/pose errors. It compares contact area to a conservative upper bound for all named coplanar support members, including the existing tolerance. It does not relax coverage or infer a replacement face.

The normal staged final-layout loop immediately returns this condition to a mounting interface Agent. The Agent receives actual geometry for the affected component and its existing parent, the full failure batch and original brief. It can choose existing parent/child contact parts and faces for exactly the failed owners. Inventory, geometry, dimensions, parent graph, access, materials and style remain fixed. The complete layout and all requested edit scenarios are then revalidated. A repeated unchanged interface failure stops or escalates normally; there is no arbitrary repair count.

Generic integration coverage exercises an overhanging panel with an existing mounting pad. The system calls the interface Agent once, accepts the Agent-selected pad after full layout validation, and preserves the exact geometry. Invented members and changes to other owners fail. This fixture is protocol evidence, not evidence of room appearance.

## Checkpoint root cause

The batch validator returns a merged recipe. Stage checkpoints therefore contained the merged result, while restoration attempted to validate it as a new chunk. This incorrectly rejected already accepted content batches and called the model again.

Merged restoration now compares every predecessor part and relation to freshly validated predecessor geometry before extracting the assigned chunk. Changed predecessor geometry or relations remain rejected. Two independent fixture categories reproduce zero-model-call restoration and identical final plans; corrupted predecessor checkpoints cannot pass.

## Verification and runtime provenance

The relevant suites passed 105 tests before capacity routing; the final available suite passes 279 tests after routing. The optional TRELLIS texture module is excluded because `trimesh` is absent. Complete suite evidence is in `docs/evidence/available_suite_20261025.log`.

Runs 43 and 44 were interrupted only to load verified general fixes; original candidates and failures remain intact with `developer_interruption.json`. Run 45 revalidates all matching recipes, including all three content batches, without regenerating them. It enters the new mounting interface stage through `run_general_scene.py` with the exact original brief. The startup conservative recipe instructions were preserved for matching checkpoints; current source describes the additional cylinder-contact capability for future runs.

All fixes are general compiler, validator or Agent changes. No run coordinates, component inventory, models or materials were hand-edited. A completed plan remains separate from actual Blender export, textured rendering and whole-scene visual acceptance; these final stages are still pending at this report's creation.

## Interface response protocol

Run 45 reached the interface Agent, but it returned three descriptive contact labels as dictionary keys instead of the one failed component ID. The geometry proposal was preserved and rejected. The response schema now requires exactly the actual failed component keys with one contact value per owner, and forbids extra keys. Owner mismatch diagnostics point to `/contacts` and include both actual and required keys. The existing generic integration test verifies this emitted schema. The related 64 tests pass after this protocol refinement. Run 46 revalidates the same recipes and uses this explicit response schema; Run 45's interruption and raw proposals remain available.
