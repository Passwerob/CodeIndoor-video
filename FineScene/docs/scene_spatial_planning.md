# Scene Spatial Planning Revision, 2026-10-23

Subsequent accuracy revision: [planning_diagnostic_accuracy.md](planning_diagnostic_accuracy.md).
Independent diagnostics now accumulate across baseline and edit scenarios, retain
precise pointers and measurements, and reach one repair request without text truncation.
The subsequent focused suite has 130 passing tests; the 118-test count below describes
the earlier spatial implementation. Repair-count defaults were not changed.

## Problem and execution path

The previous staged planner recorded functional-zone names but discarded them when
lowering to the executable plan. Detailed recipes preceded placement validation.
Object contact and nonintersection could pass while the room remained unusable.

The default complete-scene path is now:

1. Extract the original request, including explicitly specified extent, entrance
   facts, minimum route width and enclosed/open scene intent. Unstated numeric
   facts remain unknown. An enclosed scene requires an explicit architectural
   shell in decomposition; furniture plus opening metadata is incomplete.
2. Decompose activity-driven inventory, dimensions, source decisions and local
   edit/assembly contracts.
3. Ask the Agent for spatial zones, openings, access rectangles, connected routes
   and functional facing/distance/directional relationships.
4. Ask the Agent for a coarse layout. Check envelopes before recipes, retrieval or
   TRELLIS. Repair positions, yaws and route points; preserve widths, headroom,
   topology, inventory and original user requirements.
5. On bounded coarse-layout failure, revisit spatial organization once with the
   failure evidence. Do not run an impossible route through position-only repair.
6. Build detailed recipes, reuse the feasible coarse layout, then check actual
   assembly and requested edits before acquisition and Blender execution.
7. Audit the exported geometry against the same spatial contract. Pass the
   contract to the scene visual Judge and the local repairer. Fresh evidence is
   necessary for acceptance.

Single-asset requests retain their local recipe/layout flow. Legacy/replayed plans
without a spatial contract remain readable, but do not constitute evidence that
the new scene-use checks passed.

## Contracts and checks

`scene_spatial.py` owns the typed contracts and backend-independent checks.
`PlannerScenePlan.spatial` survives conversion into `GeneralPlan.spatial`.
`judge_general_geometry` runs the checks for planned, compiled and measured bounds,
and `edit_general` rechecks them after parameter changes.

Root objects have exactly one primary spatial zone. An `access_required` root
needs an operating area reachable from an entrance. Access areas are owner-local
rectangles; their pose follows the owner. Center coordinates support bounded
arithmetic in local `w,d,h` and global parameters, so clearance follows an edited
edge. Repairs may update center placement while preserving area size, owner,
headroom and purpose. Requested edits also pass the coarse envelope gate before
recipes. Routes are continuous polyline corridors
with full width and headroom, including at bends. Endpoint identity/connectivity
is fixed; path points are layout variables. Entrance approach rectangles and
window clearances at their sill height are reserved against geometry. Facing uses
local front -Y, while directional relationships use the target object's yaw frame.

Solid envelopes use conservative AABBs. Shells are excluded from coarse occupancy,
but compiled/measured parametric shell parts participate in final spatial checks;
an opaque shell label does not prove a door exists. Visual review must establish
architectural openings and semantic function. Neither stage certifies building
codes, wheelchair accessibility, load capacity or dynamic stability. Numeric
request extraction is itself model-based and must be inspected for fidelity.

## Evidence

- `scene_planning_gallery_20261023_01`: rejected early; routes ran at the room
  boundary and an entrance frame was treated as a solid blocker. No recipes or
  neural generation were started after coarse failure.
- `scene_planning_gallery_20261023_02`: rejected at spatial validation. Preserved
  route errors motivated reporting all boundary violations together.
- `scene_planning_gallery_20261023_03`: real DeepSeek Agent plan passed. It selected
  two display supports, three exhibits and one two-person bench in a 6 x 5 x 3 m
  scene with a centered 1.2 m south entrance and 0.9 m routes. The ceramic vessel
  uses conditional retrieval/generation; the other five components use aDSL.
  Source acquisition and final visual acceptance were not run for this plan-only
  trial. `accepted=false` is preserved.
- `scene_planning_meeting_20261023_03`: real Agent coarse layout failed, then
  `spatial_revision` and `coarse_layout_revision` passed before recipes. Table and
  chair recipes passed after repairs, but a potted-plant internal assembly failed.
  This is scene-planning evidence and a separate asset-construction failure,
  not an accepted scene.
- `scene_planning_discussion_20261023_04`: real Agent generated one table and two
  chairs. Coarse repair fixed a blocked north-chair approach and an access-area
  conflict after table-width editing, before any recipe calls. All three recipes
  and detailed layout passed. Compiled execution then exposed a 0.50 m versus
  0.55 m chair-depth mismatch. The former size-mismatch finding incorrectly
  requested layout repair; it now requests local geometry repair.
- `scene_planning_discussion_execution_20261023_01`: explicitly replayed that real
  Agent plan and ran the corrected system repairer. The Agent repaired chair
  geometry, preserving assembly and scene contracts. `repair_01` passed Blender
  geometry, actual spatial checks and all three Blender-backed edits: table
  height +0.05 m, table width +0.20 m and chair seat height +0.05 m. Seven rendered
  images reached the system's DeepSeek visual Judge. It rejected the whole scene
  because the room shell and actual entrance were absent, while recognizing the
  prototype furniture and facing relationship. Final `accepted=false` remains.
  This finding motivated the new enclosed-scene decomposition guard. That guard
  is regression-tested but has not yet been validated by a new fully enclosed
  end-to-end live run. The earlier gallery plan also lacks such proof and remains
  planning-only evidence.

All layouts and spatial revisions above came from model calls. No scene-specific
constructor or hand-edited coordinates were used.

The focused regression command is:

```powershell
& E:/3DUse/aDSL/venv/Scripts/python.exe -m unittest experiments.test_scene_spatial experiments.test_staged_planner experiments.test_general_planner experiments.test_visual_repair experiments.test_asset_physics experiments.test_sweep_geometry
```

118 tests passed at this revision. Tests include route-width obstructions without
object collision, access self-blocking, disconnected access, orientation, zone
containment, sill-height clearance, preserved user dimensions/openings, shell-part
obstructions, path-only routing changes, pre-recipe spatial escalation and contract
delivery to visual/repair requests. The optional plan PNG uses Pillow.

The main remaining workflow gap is a live generated room with real openings,
measured unobstructed circulation and final scene visual acceptance. Merely
switching the local geometry backend to TRELLIS.2 will not establish that result.
