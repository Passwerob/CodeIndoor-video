# FineScene

FineScene is a research prototype for "fine-grained scene construction". It puts an editable scene program, locally high-detail assets and geometry verification into a single executable representation:

**LLM Planner → Constraint Solver → aDSL Builder → Asset Assembly → Geometry Judge → Render → Vision Judge**

This directory is the standalone workspace for the new research part. The existing content remains in:

- `E:/3DUse/aDSL`
- `E:/3DUse/research/TRELLIS.2`
- `E:/3DUse/assets`

FineScene only references the existing code and assets in these directories; it does not copy large model weights or caches.

## Current research question

Given a text description, reference images and edit instructions, can the system:

1. decompose the scene into semantic components and relations;
2. generate an editable structural white-box model with aDSL;
3. retrieve or generate fine geometry and materials for designated components;
4. recompute only the affected parts after a parameter edit;
5. assemble the scene only after it passes dimension, contact, collision and provenance checks.

The first closed loop uses a bookshelf case where "each shelf level height increases by 0.1 m". It does not depend on a local TRELLIS run succeeding: ready-made assets can serve as Refiner candidates, and TRELLIS is an optional generation backend.

## Directory

- `docs/research_charter.md`: research claims, hypotheses, contribution boundary and experimental questions;
- `docs/system_architecture.md`: system architecture targeting a complete study as the scene, planning layers, component responsibilities and implementation order;
- `schemas/scene_contract.schema.json`: scene-level executable contract;
- `schemas/component_contract.schema.json`: component-level contract;
- `examples/bookshelf_edit_case.json`: a four-level vintage bookshelf and a parameter-edit example;
- `configs/mvp.json`: first-stage local run configuration;
- `src/validate_scene_contract.py`: structure validator with no third-party dependencies;
- `src/agent_system.py`: orchestration of LLM planning, editing, generation, actual-geometry and visual acceptance;
- `src/adsl_components.py`: standalone aDSL furniture component factories for bookshelf, desk and chair;
- `src/run_finescene.py`: FineScene command-line entry;
- `src/general_planner.py`: detailed planning schema for scenes of any category, relation graph and component source routing;
- `src/run_general_scene.py`: general scene entry; outputs the plan, aDSL/retrieval/TRELLIS decisions and acceptance evidence;
- `src/finalize_scene.py`: after Blender export, audit of actual component bounding boxes and rendering of the panorama, top view and a close-up of each bookshelf;
- `experiments/test_edit_pipeline.py`: regression for edit propagation, rigid contents, support relations and rejection paths;
- `dataset/`: reserved for later scenes, edit trajectories and verification results; model weights are not copied here.

### Curve primitives (2026-11-18)

Besides an explicit `path`, a `sweep` in a recipe can use `curve` to select one of 14 curve presets that adapt automatically to the part's `size`: arc, ring, rounded_rect, helix, spiral, c_scroll, s_curve, cabriole, gooseneck, u_bend, wave, catenary, bezier, smooth. `radius_end` makes the cross-section taper linearly along the arc length (0 gives a pointed tip), and `closed` closes the start and end. The new primitive `lathe` is a solid of revolution about the part's local +Z; its profile can use one of 12 presets (baluster, vase, urn, bun_foot, knob, bell_shade, drum_shade, turned_leg, column_base, finial, pedestal, tapered_foot), or explicit `[radius, z]` points can be given. Both compile to aDSL cylinders/truncated cones plus joint spheres and can be parametrically edited; contact is measured on the actual surface, and neither can serve as a supporting surface. Implementation: `src/curve_presets.py`; regression: `experiments/test_curve_presets.py`. Still unsupported: non-circular sweep cross-sections, hollow shells, lofted soft bodies, relief. Currently there is only unit regression; whole-scene verification with Blender rendering and the system visual Judge has not yet been done. For usage see `.claude/skills/finescene-curves/SKILL.md` in the repository root.

**Overall consistency**: repeated components are declared with `like=<source part>` as copies of the same design, and the design fields must be verbatim identical; `mirror=x|y|xy` has the compiler generate mirrored geometry, and the copy's center must be the mirrored position. `assembly.symmetry` declares that the whole object is mirror-symmetric, and it is verified part by part after compilation. Implementation: `src/part_consistency.py`; regression: `experiments/test_part_consistency.py`.

## Definition of the first stage

The first stage only requires one reproducible contract closed loop: read the scene JSON, validate components and relations, apply edit operations, and output the affected component set and a validation report. Blender/aDSL export, asset retrieval and the TRELLIS adapter come afterwards.

Run example:

```powershell
& E:/3DUse/aDSL/venv/Scripts/python.exe E:/3DUse/FineScene/src/validate_scene_contract.py `
  --scene E:/3DUse/FineScene/examples/bookshelf_edit_case.json
```

Run the LLM study-room Agent flow and simulate increasing the level height (calls the configured model service):

```powershell
& E:/3DUse/aDSL/venv/Scripts/python.exe E:/3DUse/FineScene/src/run_finescene.py `
  --model-profile E:/3DUse/aDSL/adsl-agents/configs/llm/gpt6astra.yaml `
  --vision-profile E:/3DUse/aDSL/adsl-agents/configs/llm/gpt6astra.yaml `
  --brief "一个复古风格、适合阅读和写作的完整书房，丰富错落的书本和桌面配件" `
  --out E:/3DUse/FineScene/runs/study_room_mvp_01 `
  --edit-parameter shelf_clearance --edit-delta 0.1
```

(The `--brief` value is the sample user input, kept in Chinese. Gloss: "a complete vintage-style study suited to reading and writing, with richly varied, staggered books and desktop accessories".)

The LLM Planner is used by default; `--planner offline` selects the fixed template. `--plan PATH` replays an existing LLM plan, which is not the same as re-understanding the brief. New plans or layout revisions are executed through the model; the component factories are responsible for turning the plan into aDSL geometry.

The visual Judge sends real PNGs to the vision model through `visual_judge.py`: the room panorama, the top view, and a close-up of each bookshelf. Responses calls use streaming. `visual_judge.json` records the model, the image SHA-256, scores, locatable image evidence, revision suggestions and a verdict of pass / needs revision / insufficient evidence; if the service is unavailable or the evidence is insufficient, acceptance is not granted. File-size and camera-ray checks do not bear visual acceptance; the old `render_audit.py` has been retired.

Bookshelf contents have independent stable IDs. A deterministic seed generates, per shelf and level, different book thickness, height, depth, front-back offset, gaps and colors; upright books have a cover, page block, spine and decorative bands, tilted books rotate about the local Y axis with the bottom face then corrected, and horizontal book stacks have a per-book `supported_by` relation. Horizontal stacks choose the left, right or centered zone according to each level's seed, and small groups get an independent L-shaped bookend at the end, which moves with its supporting level. Bookshelves gain a top crown and moldings; desktop book stacks and potted plants also have subdivided components. The primitives are not exact reconstructions of binding or plant surfaces.

Each candidate directory keeps `source_snapshot/`, the original plan, the before-and-after edit contracts, `model_plan.json`, aDSL, Blender/GLB and images. Acceptance must pass the edit, aDSL geometry, actual Blender geometry and visual checks together. Visual layout problems are fed back to the Planner, while other problems keep their repair targets for the Builder/material/camera revision.
Candidates from the LLM Planner go through deterministic layout solving, the contract Judge and Blender actual-geometry review; when layout fails, the candidate directory is kept and the full geometry evidence is fed back to the Planner, which keeps re-planning until it passes or the same failure state repeats. Books are independent rigid components: when the level height increases, upper books move with the supporting surface and keep their dimensions; if the clear height is insufficient the edit is rejected, and books are not scaled.

The chair task reuses the existing Oakwarden medieval armchair refined `.blend`: aDSL stores its local bounds, ground-contact interface and scene position, and Blender Assembly appends the original object tree and procedural materials. This way the furniture does not degrade into a white low-detail mesh by being exported as a GLB that contains only material slot names.

Geometry checks use conservative AABBs; contact on a support face does not prove the gravitational stability of a tilted book, and no friction/gravity simulation has been performed. The old study entry still reuses locally refined assets; the general component entry below uses TRELLIS.1 for local generation, and TRELLIS.2 is currently not wired into this execution path.

## General scene planning and component sources

`run_general_scene.py` uses staged LLM planning by default, with no bathroom/study keyword templates. Full-scene execution: requirement extraction → semantic decomposition → spatial-use contract → coarse layout check → parametric component recipes → detailed layout → asset resolution and assembly → measurement and visual review. A single asset keeps the local modeling chain. After decomposition passes, object IDs, sources, dimensions and interfaces are frozen; layout repair may adjust poses and route waypoints. `--planner single` keeps the old one-shot whole-scene planning as a baseline; `--planner replay --plan PATH` only replays an explicitly labeled existing plan. Plan fields include semantics, dimension expressions, counts and instance positions, parent/child and support, rigidity, key shapes, material requirements, split rationale, reference-image criteria and edit impact scope.

The spatial contract includes functional zones with extents, entrances and window-front reserved zones, operation zones in the object's local coordinates, route width and clear height, functional distances and orientation relations. Room dimensions, minimum route width and entrance information explicitly given by the user enter the frozen requirements and cannot be relaxed during repair. Every root object that needs to be operated must have an operation zone reachable from the entrance; the check covers the full passage width, route turns, object occupancy and zone ownership. Operation-zone positions support `w/d/h` and global-parameter expressions and can move with the object's edge after an edit; their purpose, ownership, size and clear height stay fixed during local repair. Declared edit scenarios are also rechecked at the coarse layout stage. If the coarse layout fails, the Agent first modifies the layout; once the budget is exhausted it may re-plan the spatial organization once with measurement evidence, and object recipes are generated only after it passes. The final Blender measurement and edit regression continue to use the same contract, and building shells are also checked for passage obstruction against actual internal parts. The final visual Judge receives the spatial contract and checks overall purpose, proportions, relations and entrance evidence; geometry passing does not equal visual passing.

When an indoor room is explicitly requested, `scene_use.enclosure=enclosed` in the requirements demands a real building shell in the decomposition, not just furniture and entrance coordinates. The current real meeting-room trial passed Blender geometry, space and three edit regressions, but the system visual Judge rejected it for lacking a real room and entrance; the newly added building-boundary check has passed regression, and a new whole-scene acceptance of an enclosed room has not yet been completed.

`agent_runtime/coarse_spatial_judge.json`, `frozen_spatial_contract.json` and `spatial_preview.png` keep early evidence; the run directory's `planned_spatial_judge.json` and the final `actual_spatial_judge.json` record the planned and actual geometry respectively. The plan view is generated by the system from the plan and only shows envelopes, operation zones and passages; it must not be treated as a finished render. The optional PNG preview depends on Pillow (`python -m pip install Pillow`); the lack of this library does not affect geometry checks. For the implementation and the boundary of real trials see [scene planning revision](docs/scene_spatial_planning.md).

Planning checks now aggregate independent problems by stage, and give the Agent the full type, JSON path, measurement, expected value and all edit states to fix together; checks that cannot run because of missing dependencies are listed separately. Dimension deviations in a complete recipe are intercepted before asset acquisition, and an illegal patch does not overwrite the original candidate's diagnostics. `planning_quality_metrics.json` records the first-candidate pass rate, call count, elapsed time and the number of issues per batch. A real north-chair replay reported, in one pass, 4 dimension deviations in the baseline and three edit states; the Agent passed the recheck with 1 repair call in about 14.6 seconds; the original coarse layout reported 9 conflicts in one pass. This is local planning evidence, not a whole-scene visual acceptance. For the implementation, the 130 regression tests and how to reproduce, see [planning diagnostic accuracy revision](docs/planning_diagnostic_accuracy.md).

Interfaces support `mount=top/bottom/origin`, `grounded` and `support_footprint`, expressing surface mounting, grounding and the actual bottom contact extent respectively. The decomposition stage checks support-chain height, scene extent, rigid-edit contradictions and impossible support spacing; the recipe stage compiles real local geometry, and the layout stage checks the declared `edit_checks`. These checks do not replace the visual Judge or a physical stability simulation.

For parametric parent objects with a clear supporting surface, a child component can declare `support_part` (for example a bookshelf `top_rail` or a pedestal `top_support`); layout and compilation position it by the measured surface of that internal part, not the highest bounding edge of the whole parent object. After Blender export, the actual support surface, contact height difference, contact center and bottom footprint are recorded and compared with the support offset and dimension envelope declared by the Agent; these measurements are handed to the visual Judge along with the images, to avoid misjudging shadows as real geometric gaps. The system still needs visual confirmation that the interface is clearly visible, and does not claim any center-of-gravity, friction or load simulation.

The local Agent probe of 2026-10-23 had the staged Planner autonomously plan an editable walnut pedestal of 0.60 × 0.45 × 0.80 m and a rigid urn of 0.18 × 0.18 × 0.34 m; it chose `top_support` as the support part and kept the urn centered. The Blender-measured contact height difference was about 0.00000002 m, the contact center offsets about 2.6 mm / 1.7 mm, and the contact footprint about 0.111 × 0.110 m; the geometry and support Judges passed. The executor then exported Blender separately for each of the two edit scenarios declared by the Agent: with pedestal height +0.10 m the urn moved up 0.10 m; with pedestal width +0.10 m the urn stayed centered; in both cases the rigid urn's bounding-box size was completely unchanged, and the support and geometry Judges both passed. The real outputs are in `runs/agent_mount_probe_edit_regression_20261023_02/`. The reused urn had previously been rejected by the visual Judge for shape and material; the geometry probe bypassed that visual gate only in memory, and does not represent generation quality or whole-scene acceptance.

Scenes with `edit_checks` additionally export each edited version and have Blender measure it, no longer relying only on the parameter-expression checks before compilation. `edit_scenarios.json` stores the geometry, support-surface and rigid-dimension verification for each edit; if the Blender measurement of an edited version fails, the scene geometry state cannot pass. For mounted components, the Judge compares the measured bounding-box center with the planned center and the bottom contact with the planned support offset; the measured size of an external rigid mesh must fall within the declared envelope and occupy at least 60% on each axis, to prevent an object from being significantly too small even though its contact is correct. This threshold is the current general plausibility gate for envelopes, not a physical theorem for arbitrary shape sizes; specific semantics and proportions are still judged by the visual Judge. `general_render.py --geometry-only` produces measurement evidence for edit regression without repeating the costly whole-scene render.

- `parametric`: explicit aDSL parts must be provided; automatic fallback to a box is not allowed.
- `retrieve`: retrieval only; candidates must pass component visual QA.
- `auto`: local candidates and network candidates are accepted in turn; TRELLIS.1 is attempted only if requirements are not met. The planning stage does not turn auto into retrieve.
- `generate`: an explicit custom appearance or an existing retrieval-failure basis; the generation rationale and reference-image strategy must be stated.

```powershell
& E:/3DUse/aDSL/venv/Scripts/python.exe E:/3DUse/FineScene/src/run_general_scene.py `
  --brief "一个户外植物与雕塑展区，台座尺寸可编辑，包含定制多孔石雕与蕨类植物" `
  --out E:/3DUse/FineScene/runs/botanical_plan_new --plan-only
```

(The `--brief` value is the sample user input, kept in Chinese. Gloss: "an outdoor plant and sculpture display area with editable pedestal dimensions, containing a custom porous stone sculpture and ferns".)

First check `planner_output.json`, `source_decisions.json`, `component_jobs.json` and `general_plan.json`. A component job records the generation conditions, features, reference-image and interface requirements, and the acceptance gates it must pass; the existence of a job does not mean the asset has been generated or has passed. Only without `--plan-only` are assets resolved, aDSL compiled, Blender exported, actual bounds checked and the general Visual Judge called. Missing images or a failing vision model will not yield acceptance. `--offline` only disables online search for assets/reference images; the model service still requires network access.

The optional `--reference-generator PATH` connects a local image-generator adapter. FineScene runs a `.py` adapter with the same Python interpreter, or runs an executable directly, passing `--request REQUEST.json --output IMAGE.png`; the request JSON contains the component semantics, shape requirements, reference-image criteria and the previous Judge feedback. The adapter must write a valid PNG/JPEG/WebP. The generated image is still an untrusted candidate and enters TRELLIS.1 only after passing the reference-image visual Judge; if the adapter fails or the Judge rejects it, TRELLIS is not called. FineScene provides the adapter protocol and execution entry, and does not include any particular image-generation model or service.

Using the same brief with `--resume-runtime OLD_RUN/agent_runtime --out NEW_RUN` reuses matching stages that pass re-validation; a change in context, schema, instructions or the model profile path invalidates the corresponding cache. Staged requests, raw outputs, repair paths and checkpoints are saved in `agent_runtime/`. This is a planning cache, and does not yet amount to incremental execution of asset building and rendering.

Transient transport failures of the model service (interrupted response body, dropped connection) are first retried on the same request with exponential backoff (3 times, on the order of 2/4 seconds plus jitter), without changing the design request or triggering re-planning. After retries are exhausted, the run report writes a `resume` field: the failed stage, the reusable `agent_runtime`, a suggested new output directory and one complete command; continuing with that command reuses the already-validated stages after re-validation and recomputes only the failed stage and the stages after it. Transport details do not enter the run evidence: `<stage>_service_attempts.json` records only the error type, truncated message, the elapsed time of that attempt and whether it was retried; troubleshooting information such as HTTP status, byte counts and request id is not stored long-term.

When the final QA does not pass, `repair_work_orders.json` is written. Layout, parametric part geometry and color problems are repaired, re-validated, re-rendered and visually reviewed continuously until they pass or the same candidate appears again; there is no longer a fixed number of repair rounds. The internal assembly contract, rigid dimensions and sources stay fixed. Source replacement, camera and topology restructuring still keep pending work orders; a vision service failure does not trigger geometry changes. Whole-scene planning retries also continue until pass, service unavailability, or repetition of the same failing plan. For the implementation and real service limits of this round, see [asset physical consistency revision](docs/asset_physical_consistency.md); for the earlier planning-layer revision, see [staged planning record](docs/staged_planner_revision.md).

Multi-part aDSL assets must now carry an internal `assembly` contract that checks part connection, support, interpenetration and clearance. Internal `parent/mount` makes contents move with the supporting surface; `fit_between` solves structural part sizes from the actual part surfaces. `internal_parts` and `internal_clearances` keep the needed contents and functional spacing at the decomposition stage. Edits also check internal rigid geometry; after export, Blender's `actual_part_bounds.json` is read and audited again. Old multi-part plans without an assembly contract are rejected. The general regression example is `examples/general_bookshelf_assembly.json`.

The offline regression sample is `examples/general_metrology_fixture.json` (a pedestal and two cylindrical specimens, prototype); it is used to check compilation, dimension editing, support, export and rendering, and is not proof of natural-language planning capability. Run `experiments/test_general_planner.py` to verify rejection paths and auto routing.

Current boundaries: parametric geometry is box/cylinder/sphere plus the curve primitives sweep and lathe (see "Curve primitives" above); object pose supports only yaw, and anything beyond capability is rejected; geometric collision is conservative AABB. Local generation is still a rigid mesh, and internal topology is not promised to be editable. TRELLIS.1 components keep the predicted mesh colors and, combined with the Planner's material requirements, bake a 1024² UV base-color texture; candidates without UV, a base-color map or valid colors do not enter the component visual Judge. To fit local 16 GB of VRAM, by default the native 256³ sparse grid features are average-pooled to 160³ before FlexiCubes decoding, so the model results suffer a corresponding loss of geometric detail; it can be adjusted explicitly with `--mesh-resolution`. The material profile provides auditable color/roughness and slight procedural variation; it is not learned material segmentation, does not generate learned roughness/metallic maps, and differs from the official Gaussian multi-view appearance baking. The complete texture must still be reviewed by the visual Judge; a material and mesh passing the texture gate does not mean the asset has been accepted. Camera/lighting currently use a general review configuration, and camera/lighting descriptions in the intent are not a complete executable photography plan. A detailed schema and routing do not equal a verified production success rate of photorealism for arbitrary scenes.


FineScene's `blender_worker_fast.py` only adapts, within this project, the export of large numbers of box meshes; the original aDSL exporter is left unchanged. The batch-mesh path verified equivalence of vertex positions/materials in three groups — original, tilted, and rotated-translated — (1e-6 m tolerance). Thicker parts get a small bevel, thin book covers keep exact support edges; each time the final Blender bounding box is still rechecked. Base geometry and visual style review are different acceptance stages.

Regression: `E:/3DUse/aDSL/venv/Scripts/python.exe E:/3DUse/FineScene/experiments/test_edit_pipeline.py`. It contains 9 items, including rigid book edit/restore, rejection for insufficient clear height, real tilted bounding boxes and per-book stacking support, stable repeated solving of desk and chair, and rejection for missing visual evidence.

For the provenance limits of the old heuristic visual report, see `docs/visual_audit_provenance.md`.
