# Scene design before object decomposition (2026-10-23)

The previous complete-room trial was a circulation/geometry prototype. Its brief
explicitly requested minimal furniture, and the planner had no explicit design
stage. Passing that trial cannot demonstrate a finished, realistic room. The
user's correction is about the planner's decisions, not a missing decorative mesh.

The realistic-scene path now runs:

`request contract -> scene design -> provider metadata search -> design feasibility
-> decomposition and design coverage -> functional attachment/access decisions
-> semantic access faces -> spatial plan -> circulation program -> numerical layout -> recipes
-> source acquisition/QA -> whole scene Blender measurement and visual QA/repair`.

`scene_design.py` records room identity, users/activities, composition, focal
hierarchy, material palette, lighting and visible design choices. These choices
cover architecture, furnishings, lighting, decoration and activity contents as
appropriate to the request; they are not a fixed room template or primitive-count
quota. Design feasibility reconciles speculative upholstery/profile details with
available sources before sizes freeze. It must retain a primary functional
furniture type when plausible remote candidates exist, rather than reducing the
design to whichever simple furniture happens to be cached locally.

`Decomposition.design_bindings` maps every design choice to actual components.
Coverage rejects omitted layers before layout. The final plan persists both the
design and bindings, and the scene visual Judge receives them. Binding metadata
does not prove that a component visually realizes its intended purpose or quality.

The local catalog now includes source manifests and measured source proportions
where existing Blender audits are available. Cached provider model metadata is
searched before decomposition. Exact local or `polyhaven:<id>` source choices and
source-front corrections survive all planning/lowering boundaries. The resolver
downloads an exact remote selection and still subjects it to normalization and
independent component visual QA. New preflight checks reject invented IDs and
known impossible source/envelope aspect ratios before acquisition.

The feasibility output allocates each design choice to parametric construction or
actual catalog IDs. `--disable-generation` reaches both the planning capabilities
and the execution boundary; speculative auto/generate jobs are rejected before
layout/acquisition. Existing generated sample meshes remain valid retrieval
ingredients. Source-front orientation and exact provider IDs survive lowering.

An explicit functional-binding call now decides access and support before spatial
planning. It distinguishes dedicated human approach areas from contents reached
through their supporting furniture. Small props do not each acquire independent
circulation corridors by schema default. Surface-resting objects are attached to
their actual support and shelf contents name the intended structural shelf.
Wall/ceiling fixtures use the shell's coordinate frame rather than a false floor
contact. Inventory, source and dimensions remain frozen in this call.

Functional access faces are decided independently of room compass coordinates.
For new realistic scenes, every approach names an allowed owner-local side and
an optional along-face offset. The compiler derives the editable normal offset;
the model cannot use world east as a substitute for a closed bookcase side.
A compact circulation program chooses task-specific approach sizes and final
route widths, preserving explicit user minima. The compiler builds its connected
entrance-to-activity graph; the numerical backend must find actual full-width
paths. Inferred decomposition aisle assumptions are marked as design proposals,
not promoted to user constraints. Required room objects are preserved.

Decomposition semantic checks now aggregate independent relationship, design,
source/backend and room-capacity errors into one repair request after field parsing.
Pydantic's first model-level semantic error no longer hides independent checks.
Whole-room footprint capacity is checked under every requested edit. A single
enclosing shell must actually span the scene XY boundary where openings are
declared; numeric empty margins cannot hide an impossible room-resize test.
User spatial requirements are copied from the frozen original contract by the
compiler, then checked against the generated spatial plan, rather than asking the
model to reproduce and redefine them in a second JSON structure.

The shared material catalog discovers standalone provider PBR surfaces from their
manifests/catalogs, including plaster, wood floor and woven fabric. Provider tile
dimensions govern physical UV scale. Packed ARM maps use G for roughness and B for
metallic; they are not mistaken for normal maps. Imported asset materials remain
in the packed review Blender file.

Scene review now includes an actual entrance-height perspective image with the
full room geometry, in addition to the documented cutaway/measurement views. An
interior diffuse inspection light and light at actual planned windows illuminate
enclosed rooms whose ceilings would otherwise block the external review rig.
These are inspection lighting, not evidence of simulated luminaire photometry.

Each visual batch receives only its attached render-manifest entries and relevant
measured component findings. A response citing an unattached image is retried as
invalid evidence rather than becoming a geometry work order. Acceptance remains
fail-closed and still requires actual geometric and visual checks.

The new live brief is `experiments/brief_refined_reading_lounge.txt`. It requests
a refined, inhabited reading/reception room and lets the Agent choose inventory,
scale, layout and construction. The assistant has not authored its mesh geometry
or placement coordinates. Existing generated samples and downloaded assets are
allowed; the live brief explicitly disables new local TRELLIS inference.

The coarse gate now calls `layout_solver.solve_layout` when the Agent's proposed
coordinates fail. The Agent still chooses inventory, use relationships, zones
and yaws. Z3 solves translations under conservative world AABBs for baseline and
all declared edits, then full-width grid routing and the original production
preflight independently verify the result. Incompatible fixed-yaw choices return
named conflict constraints directly to spatial revision rather than consuming
the coordinate-patch loop. Disjoint conflict cores are collected together, with
all original independent preflight issues. Rotated human approach rectangles use
separating axes rather than inflated axis-aligned reservation boxes. Route
endpoints are chosen inside their actual access areas and every full-width
segment is verified. Spatial-only failures stay in spatial revision while the
accepted furniture/design inventory remains intact. Explicit resumed candidates
take precedence over older matching checkpoints and are revalidated.
Unsatisfiability in this conservative representation
does not prove that every yaw or design is impossible. Corrected same-schema
validator outputs are saved in accepted/checkpoint files, including on resume.

Rectangular architecture now has a semantic construction path. The Agent emits
`ShellProgram` material/profile choices and binds all frozen floor, wall, trim,
door and window intents to the accepted spatial opening IDs. The compiler in
`architectural_shell.py` computes symbolic nonoverlapping wall cells, separate
frame members, raised panel moldings, skirting/cornice and a parked open door.
It preserves genuine empty apertures and derives physical assembly joints from
the generated members. Existing recipe, envelope, edit and Blender gates still
apply. Representative frozen panel/beam proportions bind to the most suitable
actual member of a split assembly. This path handles centered, axis-aligned
rectangular enclosures; it does not claim an arbitrary building topology kernel.

Unreferenced inferred quantities are retained as provenance instead of fake
editable geometry controls. Explicit requested edit checks are never pruned;
unwired requested controls still fail the production gate. Actual circulation
dimensions remain in the frozen spatial contract.

Retrieved-source reviews now receive the original user brief. Guessed upholstery,
wear and decorative subtypes are proposals rather than extra user constraints.
The Source Planning Agent may reconcile these descriptions with the observed
source, but cannot change the source, dimensions, placement or mesh. An independent
visual Judge must review the unchanged images again before acceptance. Identical
sources at identical normalized dimensions/orientation share a union of feature
requirements; their scene placements remain independently checked.

Independent acquisition failures are collected together. A rejected but valid real
mesh may be compiled only in explicit inspection mode so whole-scene geometry and
visual evidence can be gathered. It retains `accepted=false`, and scene acceptance
requires source QA as well as geometry and visual QA. No placeholder substitutes
for an unavailable model, and a favorable scene verdict cannot override source
rejection. Component feature descriptions do not create frozen style IDs; declared
style contracts and blocking-finding/verdict consistency remain enforced.

Provider files are checked for size and MD5 before atomic commit. A stale CDN
response is retried with a cache-busting query. Downloaded originals reused across
sibling component caches undergo the same dependency checks; an existing glTF
alone does not establish complete buffers and textures. `--asset-cache` allows
reusing these originals and normalized candidates across resumed Agent runs.

Live artifacts and final validation status will be recorded after execution.

Regression evidence: 245 scene/planner/geometry tests pass in the aDSL Python
environment. The six TRELLIS texture-export tests pass separately in their
existing TRELLIS environment (the scene Python intentionally lacks torch/trimesh).
Two older diagnostic fixtures were updated for the earlier removal of the 6000
character error truncation and millimetre envelope checking; no gate was relaxed.

The 2026-10-24 revision adds executable composition bindings, protected empty
volumes, Agent-authored perspective views, high-level feedback propagation and
measured named-face mounting. See `composition_and_mounting_revision_20261024.md`
for implementation, real Blender evidence, live-run failures and domain limits.
