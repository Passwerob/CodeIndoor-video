# Debug Report — mounting bounds and occupied geometry contradicted layout repair

## Symptom and reproduction

Run 46 alternated between `mounting_coverage` and `outside declared scene extent`
for the same sash. The selected lower frame requires parent-local Y >= 3.927 m,
while the complete 0.18 m deep body must remain at Y <= 3.925 m. At the recorded
yaw there is no common translation. The same failure batch treated an open door
frame's complete envelope as a solid obstacle over its doorway.

Three generic regressions failed before the fix: an empty mounting translation
domain, a frame incorrectly blocking an aperture/route, and an independent object
incorrectly colliding with the empty interior of a frame. The tests use unrelated
generic geometry; the original Agent room candidates remain unchanged.

Read-only original-candidate revalidation is stored in
`evidence/mounting_extent_revalidation_20261025.json`. It uses Run 46's Agent-selected
mounting outline and saved failed layout. All five baseline/edit scenarios now
report the measured contact/boundary conflict instead of another translation
request. This reproduction is rejection with correct diagnostics, not acceptance
of the invalid room candidate.

## Root-cause fixes

`surface_mounting.translation_domain` intersects a conservative bound on the
support union with the child's complete world envelope and the frozen scene
extent. Two tangential coordinates are solved by half-plane intersections,
including rotated parents. An empty result proves that no translation is possible
at the supplied yaw; a nonempty result still requires exact union coverage.
`mounting_extent_conflict` returns to the normal mounting interface Agent, which
may select actual existing contact members/faces. Geometry, dimensions, parent
graph and original requirements remain frozen. The diagnostic does not claim
that all alternative yaws are impossible.

Spatial occupancy now uses available actual parametric members for solid
assemblies as well as architectural shells. Solid frames no longer occupy their
empty enclosing volume. A real blocking leaf still rejects the aperture and
route. Independent solid collisions use occupied member bounds. Conservative
member AABBs remain conservative for curved/rotated geometry; this does not claim
mesh-exact collision or physical certification. Detailed planning now audits
available compiled parametric geometry, retaining envelopes for unresolved rigid
assets; exported Blender measurements remain mandatory later.

Validated local geometry is cached by the complete local recipe and parameters.
Returned geometry and measurements are independent copies; parameter changes
invalidate reuse. Failed recipes and external assets are not cached.

## Resume consistency

Run 47 exposed a further cause of unnecessary regeneration: a capability prose
update changed the recipe request hash despite identical original requirements,
decomposition, spatial contract and coarse layout. Explicit resume now compares
those frozen Agent outputs and runs current recipe, edit and aperture validators
before reusing a complete recipe. Changed targets cannot use this path. The
previous run and all original recipes remain intact. Run 48 reused all 16 complete
parametric recipes under this stronger semantic check and proceeded to the
normal mounting interface stage without re-authoring them.

## Verification

All 284 available regression tests pass, including five added integrity/reuse
tests. Full evidence: `evidence/available_suite_mounting_extent_20261025.log`.
The optional TRELLIS texture suite is excluded because its `trimesh` dependency
is absent from this test interpreter; no local TRELLIS inference is enabled.
Two dispatch tests inject their historical fixture's geometry evaluator and
explicitly isolate that dispatch protocol from physical metrology. The stronger
real metrology gate is exercised separately and remains active in production.

## Current room status

Run 48 is an explicitly resumed Agent run with the unchanged original brief,
matching checkpoints and current validation. Whole-scene Blender export,
textured rendering and Agent visual acceptance are still pending at this report's
creation. Regression success and accurate rejection of the original candidate
are not an accepted realistic scene.
