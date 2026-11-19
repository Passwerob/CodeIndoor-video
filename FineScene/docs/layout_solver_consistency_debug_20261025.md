# Debug Report — coarse layout solver and preflight disagreed

## Symptom

Run 39's joint solver returned `validation_failed` with `degenerate_route`, `use_desk_chair` and `mounting_orientation` findings. Its earlier seed used a positive owner-local approach override even though the accepted access contract specified `front` (local negative Y). Preflight ignored that illegal normal override, whereas the solver constrained furniture against it. A rotated canonical approach also collided with its owner's axis-aligned envelope corners.

Environment: Windows, Python from `E:/3DUse/aDSL/venv`, no Git repository. Evidence is preserved in `runs/refined_reading_lounge_20261025_39/agent_runtime/`. No run coordinates, inventory, recipes or materials were edited.

## Reproduction and root cause

1. `test_semantic_access_normal_is_identical_in_solver_and_preflight` failed before the fix with a route endpoint outside the accepted access area. The solver used an overridden normal that `spatial_with_layout` correctly rejected. Solver constraints now use that same effective contract. Classification: root-cause fix.
2. `test_mounting_orientation_comes_from_the_selected_contact_faces` failed before the fix with `mounting_orientation`. The orientation proposal considered proximity and nearest room walls but omitted the explicitly selected mounting faces. The proposal now derives the relative yaw from opposing parent/child normals. Classification: root-cause fix. Real mounting coverage remains a later measured-geometry gate.
3. `test_rotated_semantic_approach_uses_the_actual_oriented_envelope` failed before the fix with an owner blocking its valid face-relative approach. Coarse envelopes, constraints and routing now use rotated envelope polygons for occupancy. Declared geometry does not replace measured export evidence. Classification: root-cause fix.
4. Run 39's joint receipt contained five `degenerate_route` findings. Symbolic three-segment routes may collapse bends; `_compact_route` removes repeated and redundant collinear vertices while retaining endpoints and genuine reversals. Production preflight remains mandatory. Classification: root-cause fix at route export. The small straight-route test already passed before this change; it alone is not before/after proof for the recorded room failure.

## Avoiding an unnecessary model round trip

After a spatial contract revision, the existing Agent layout is revalidated and solved against the new constraints before requesting another full coordinate response. A feasible seed proceeds without that model call. An infeasible seed sends fresh complete evidence to the layout Agent. Two staged regressions cover both branches; neither bypasses validation or changes the original user contract.

## Verification

Minimal commands: `python -m unittest experiments.test_layout_solver -v` (13 passed), and the related solver/staged/spatial/diagnostic suites (94 passed). The available full suite passed 262 tests before the final staged seed-reuse edit; the additional staged branch test is included in the later targeted run. The optional TRELLIS texture module is excluded because `trimesh` is unavailable.

The actual room was rechecked with the changed solver and initially still had writing-zone and desk/chair constraints. Run 40 subsequently passed full coarse layout with the accepted revised contract and updated circulation. This is a layout-level reproduction success, not complete-scene acceptance. Run 42 is proceeding through normal component construction: room-shell geometry passed after two repairs, fireplace geometry after one. No final Blender scene or whole-scene acceptance is claimed at this point.

The final available full suite now passes 271 tests. Additional regressions cover structured-backend binding capacity, precise repair pointers, retained rejected-patch/assembly evidence and the component-local elevation of the world floor. Actual Run 41 exposed an unsupported segmented-wall binding and incorrect local/world floor reasoning. The capability gate chooses the general recipe backend; recipe calls retain low reasoning and an explicit transform context. Complete compact repair evidence reduces repeated pointer expansion without dropping issue IDs or part measurements.

## Residual risk

Fixed yaws, conservative furniture separation and restricted joint route topology can still require redesign. Actual support, wall-hole coverage, materials, source suitability and the whole-scene visual Judge remain independent gates. No debug instrumentation remains in production; diagnostic receipt fields are normal runtime evidence.
