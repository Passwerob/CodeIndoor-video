# Executable spatial design and physical mounting

This revision responds to the supplied `general_scene_aesthetic_direction.txt`
and the floating wall-light screenshot. Only general Agent/planner/compiler,
renderer and validator code was changed. No candidate scene was manually placed,
edited or accepted.

## Root causes

The previous design described composition in prose but lowered mainly inventory,
zones and circulation. The solver optimized feasible translations near those
initial coordinates; it was never a scene designer. Shell parenting with
`mount=origin` provided a coordinate frame, not a mounting interface. A heuristic
even pinned thin fixtures to a functional-zone boundary, which need not coincide
with a physical wall. Feedback entered decomposition after high-level design,
so repeated generation could retain the rejected overall concept.

## Implemented behavior

`scene_composition.py` represents a whole-space concept, spatial groups, group
relationships, density/rhythm, intentional empty volumes, experienced views and
editing priorities. The scene design must provide this concept in production.
Backward parsing of existing saved designs remains possible.

The spatial Agent binds group members and anchors to the real design inventory,
and binds group relationships to real spatial relations. Members must have an
executable relationship/support path to their anchor. This is a reference for
relationships, not a compulsory single visual focal point. Empty volumes and
view intents must also survive lowering. The existing solver and independent
spatial audit protect the empty volumes. Actual full-geometry perspective
cameras render the Agent's chosen observation positions, and geometric checks
reject a camera inside an object. Opposing scene context views remain required.

Design feedback now reaches the whole-space design and source-feasibility
stages. Image Judge and repair instructions call for designer critique with
concrete visual evidence, intentional movement/replacement/removal, and whole
scene re-review. No aesthetic scoring formula or symmetry/room template was
introduced. High-level source/inventory changes still go through replanning;
local patches cannot silently change frozen topology or source decisions.

`surface_mounting.py` replaces the zone-border heuristic. The Agent names the
actual parent member and local face, and the child backplate/mesh face. The
compiler requires opposing normals, resolves only the normal placement axis
against compiled geometry, and checks tangential patch coverage against actual
coplanar wall members. Split wall cells preserve their semantic member ID;
the union-coverage check rejects mounting across a window/door hole. Parent yaw
is applied consistently. Blender measures evaluated contact vertices, including
parametric backplates, and the final support audit checks the measured mounting
gap and surface coverage. Missing measurements fail closed.

Coarse planning still uses provisional fixture positions before architectural
recipes exist. These positions cannot establish mounting acceptance. Surface
supports currently require real unrotated rectangular box faces in the parent
frame; arbitrary curved or tilted support surfaces are rejected. This is an
explicit domain limit, not an invisible nearest-wall fallback.

A new live run exposed an additional planning defect: request extraction
classified straight paneling/baseboards as necessarily freeform and placed
materials in a repeated-content counting contract. The extraction prompt now
distinguishes visible shape from a style association, preserves alternatives and
negative mentions, and keeps countable contents separate from design layers.
Decomposition/spatial/coarse-layout stages retain low reasoning with sufficient
output space. They are planning decisions, not merely JSON transcription.
Scalar schema errors now permit exact field repairs without rewriting an
unrelated inventory. All geometric, style, source and visual gates remain.

## Evidence and current limits

Regression log: `runs/composition_regressions_20261024.log` (245 tests).
Real Blender exporter/measurement smoke test:
`runs/mounting_blender_regression_20261024_02/mount_audit.json`.
The rotated-wall diagnostic measured normal contact error about 0.000000304 m.
This is a synthetic compiler test, not a newly generated reception room.

Run `refined_reading_lounge_20261024_30` is a diagnostic failure. Its initial
request/design/source-feasibility stages passed, but decomposition repeatedly
failed. It was interrupted during high-level replan after the wrong frozen
geometry classification became clear. Original outputs are preserved. It has
no new accepted Blender scene. The corrected extraction and final prompt
changes require a fresh run; no old output is relabeled as a success.

Fresh run `refined_reading_lounge_20261024_31` uses the original brief and the
shared verified asset cache, with no manually authored inventory or placements.
Its new request extraction passed on the first response: only books are counted
as repeated contents, period wall joinery uses regular solids, upholstery uses
freeform sources and textile appearance remains an appearance requirement.
The run is still in progress; this is planning evidence, not scene acceptance.

Executable design bindings establish traceability and enforce selected spatial
decisions. They do not prove beauty, ergonomic completeness, or autonomous
success on arbitrary scenes. Those claims require actual Agent image review and
more varied scene experiments. A new complete scene remains subject to all
source, geometry, image and requested-edit gates.

## Named support preflight correction and resumed run

Run 31 exposed a false height failure: functional bindings correctly named
`floor_slab`, `mantel_shelf` and individual bookcase shelves, but decomposition
preflight added the entire parent envelope height. Named interfaces now remain
unknown at this early stage until actual recipe geometry establishes their
elevation. Ordinary whole-envelope support chains still receive early height
checks; final assembly and measured geometry checks remain mandatory. A new
regression verifies a low shelf inside a tall frame fits and that an actually
over-height support still fails the production geometry gate.

`runs/support_preflight_regressions_20261024.log`: 246 tests passed.
Run 31 raw outputs remain preserved with a diagnostic pause record. Run 32
explicitly resumes its matching checkpoints and revalidates them under the
corrected code. It is a resumed execution, not a fresh-prompt success. The
request, whole-space design, source feasibility, decomposition and functional
bindings were reused and revalidated; spatial planning is in progress.
No new final image or accepted Blender scene exists at this checkpoint.

Further live transport evidence showed the spatial response exhausted its 32K
output cap (27,245 reasoning tokens) and truncated the actual JSON. Its fallback
requested disabled thinking, but stage-specific settings re-enabled it. The
fallback flag now survives those overrides, spatial responses have 64K output
headroom and a 300-second timeout, and every completed raw response is preserved
before JSON parsing. A transport regression exercises the real settings path
and verifies enabled-to-disabled retry plus restoration of configured settings.

Shared design choices can now distribute their actual instances among spatial
groups. Previously a lighting choice referenced by two groups forced *all*
lights into *both* groups, creating artificial cross-room relationships. Each
group must still realize every intended choice, retain its anchor and exclusive
members, establish executable relationships, and all realized components must
belong to a group. A regression rejects dropping a shared instance entirely.

`runs/composition_support_transport_regressions_20261024.log`: 248 tests passed.
Run 32 is preserved as an interrupted diagnostic execution. Run 33 explicitly
resumes and revalidates its checkpoints; it has passed spatial planning and
circulation planning. The first numerical layout was infeasible, and the Agent
is revising spatial choices from the full solver feedback. No new scene has
passed Blender geometry or visual review at this checkpoint.
