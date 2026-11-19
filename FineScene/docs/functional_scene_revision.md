# Functional members and complete scene evidence, 2026-10-23

The earlier exported discussion set was not an accepted room. Its north chair
declared back slats with XYZ dimensions 0.44 x 0.07 x 0.02 m while the south chair
used approximately 0.42 x 0.02 x 0.07 m. Both contacted their stiles, so contact
and outer-envelope checks passed even though the north chair presented horizontal
ledges instead of usable vertical backrest faces. The final system visual Judge
rejected that candidate, and actual architectural walls/entry were absent.

This revision changes the system, without editing that scene's geometry:

- Decomposition can freeze a `form` for each internal regular member: panel
  thickness normal or beam length axis, and a minimum cross-section ratio.
  The axes belong to the local asset frame. The compiler copies the frozen form
  into recipe parts; recipes cannot weaken it. Assembly auditing evaluates actual
  oriented dimensions, including rotations, before acquisition and after Blender
  export. Form checks complement contact checks and visual judgment; they do not
  infer function from part names or certify mechanical strength.
- The occupied floor is world Z=0. Architectural slabs may extend below it while
  their finished top meets zero. Shell grounding means reaching this datum;
  solid furniture still requires its bottom at zero. Coarse and measured checks
  continue to bound architectural XY and world top height. This avoids raising a
  structural floor through furniture or treating slab thickness as an obstruction.
- Recipe bounds must match their declared centered frame within 1 mm, rather
  than allowing a 5 mm frame drift to invalidate a previously feasible corridor.
  Measured parametric frame drift routes to geometry repair, with the actual local
  bounds supplied; translating the entire scene instance cannot correct it.
- Architectural parts obstructing an opening carry exact owner/part locators and
  a geometry work order. They must not trigger movement of an entire wall. Box
  repair evidence includes exact candidate face-contact center coordinates.
- Every parsed stage exposes schema defaults before semantic repair. Missing
  coverage tags can therefore be repaired at leaf pointers without rebuilding
  the entire inventory. Parse failures still retain the original raw output.
- Spatial validation checks each root's footprint area against its zone for the
  baseline and every requested edit. An object larger than its zone cannot fit
  at any yaw or position; this now returns a zone-size diagnosis to the spatial
  planner before position-only repair. Passing this necessary condition is not
  evidence that a detailed layout is feasible.
- Local visual repair receives all actionable findings and their measured
  neighbor evidence in one transaction; decomposition/source findings remain
  explicitly deferred. Final acceptance still requires new measurements/rendering.
- Rendering adds side views in each object's own facing frame. Enclosed scenes
  also get explicitly documented cutaways plus full-geometry exterior and entrance
  views. Only shell meshes are temporarily hidden; all are restored in the saved
  Blender file. The Judge receives the manifest and reviews architectural evidence
  at scene scope and side views with the correct component IDs.
- Constructible geometry candidates are exported and rendered even if their QA
  fails. This permits complete visual feedback and inspection of the actual
  `.blend`; geometry and visual failures still prevent acceptance. Repair patches
  now pass actual aDSL scene-part and edited-scene auditing before rendering.

Regression coverage: `test_functional_scene.py` checks swapped panel axes that the
old contact-only gate accepted, rotated members, frozen-form lowering and slab
grounding. Planning/visual regressions additionally check omitted default fields,
batch repair of multiple findings and architectural/side image routing.
The focused eight-module suite passes 140 tests with this source revision.

Live task: `experiments/brief_complete_discussion_room.txt` requests an actual
4 x 4 x 2.8 m room with a 1 m south entrance, a north window, table, two chairs,
wall bookshelf and at least six individual rigid books. The Agent owns inventory,
geometry, positions, yaw, routes and repairs. Run `_01` preserved an initial
diagnostic attempt. Run `complete_discussion_room_20261023_02` resumes its validated
request/decomposition/spatial stages under corrected slab grounding and default
repair handling. Its remaining bookcase-zone capacity failure motivated the
pre-layout capacity check; `_03` resumes the preserved Agent candidates and
repairs the spatial contract before layout. Final outcome is recorded in that
run's `run_report.json`. Execution replays use the same unedited Agent plan under
`complete_discussion_room_execution_20261023_01` through `_03`; the first two
preserve rejected local patch evidence, while `_03` also exports/render-reviews
constructible failed candidates. An
intermediate planning pass alone must not be reported as scene acceptance.
