# FineScene general-scene change map (2026-10-20)

Finished-room planning revision: `scene_design_revision.md` records the new
realistic-scene design and source-feasibility stages, coverage into actual
components, provider asset/material capabilities, source ID/orientation lowering,
and image-batch evidence checks. The earlier minimal discussion room is explicitly
a prototype and is not the target for the refined reading/reception-room run.

Current functional/whole-scene revision: `functional_scene_revision.md` records
frozen panel/beam axes, occupied-floor slab handling, default-field leaf repairs,
all-actionable visual repair batches, side views and documented room cutaways.
`scene_spatial.validate_zone_capacity` also rejects pose-independent zone overflow
under every requested edit before coarse-layout repair. The complete room live
trial keeps Agent-authored geometry and all intermediate failure evidence.

Latest planning accuracy revision: `planning_diagnostic_accuracy.md` documents
`planning_diagnostics.py`, complete per-stage diagnostic reports, pre-acquisition
recipe-size checks, all-edit preflight, production repair-path reuse and metrics.
130 focused tests pass. A real original Agent chair recipe now fails early with
four scenario findings and passes after one Agent repair call (14.555 seconds).
That is local planning evidence, not final scene visual acceptance.

Latest scene-use revision (2026-10-23): see `scene_spatial_planning.md`. Complete
scenes now pass an Agent-generated spatial contract and coarse layout gate before
recipes/acquisition. Zones, operating areas, entrances, connected corridor widths
and functional relationships persist through executable plans, Blender auditing,
edits and scene visual/repair requests. Single assets retain the local pipeline.

Current revision: see `staged_planner_revision.md` for the implemented staged planner, support/edit regression evidence and remaining final-visual execution gap. The entries below preserve earlier mapping and experiments chronologically.

Latest asset revision: `asset_physical_consistency.md`. `asset_physics.py` adds internal assembly checks and `general_scene.py` solves face spans and checks rigid internal parts on edits. `scene_execution.py` now runs bounded final repairs for layout/part geometry/color, freezes contracts, reuses resolved external assets, and requires new evidence. `general_render.py` measures Blender part bounds; `general_vision.py` batches all required images and fails closed. 55 regression tests passed. Live LLM trials and final vision still failed; no end-to-end accepted arbitrary scene is claimed.

Scope: level 2, CLI → planner → aDSL construction → asset resolution → Blender → visual acceptance. Existing study editing stays as a compatibility backend.

Confirmed from code: `run_finescene.py:12` reads the brief/profiles and calls `FineSceneAgentSystem.run`; `scene_planner_agent.py:26` restricts furniture to three categories and its prompt requires a study. `compile_scene_plan` creates a study-specific contract; `scene_entities.py` expands bookcases; `constraint_solver.py` contains desk/chair and shelf policies; `adsl_components.py` dispatches fixed factories. `asset_registry.py` resolves one local reading-chair asset, not open search. `agent_system.py` serializes aDSL and runs `blender_worker_fast.py`, which adapts the original `aDSL/scripts/blender_worker.py`. That worker restores the Asset tree and uses `adsl-core/core/export/export_glb.py` to import external GLBs and preserve semantic paths. `finalize_scene.py` audits actual Blender bounds and renders study views. `visual_judge.py` calls the configured vision service but its schema/prompt measures book variety. Failures do not become acceptance.

Confirmed prerequisites: aDSL Python at `aDSL/venv/Scripts/python.exe`, Blender at `E:/Blender/blender.exe`, model profiles in `aDSL/adsl-agents/configs/llm`, separate TRELLIS.1 Python at `research/trellis_windows_venv/Scripts/python.exe`. `run_trellis1_component.py` has already produced the toilet; it is not called by the scene orchestrator. TRELLIS outputs require independent visual acceptance.

Configuration: CLI profiles override defaults and become `AgentRuntime`/`ModelProfile` inputs. Credentials are loaded by those helpers and must not be copied into output artifacts. Geometry uses metres, Z up; glTF import in Blender converts Y up to Z up. Existing external-mesh bounds are metadata, not automatic normalization, so a generic resolver must measure imported bounds and normalize uniformly before assembly.

Verified command: `aDSL/venv/Scripts/python.exe FineScene/src/run_finescene.py --help` succeeds. Previous full study and TRELLIS.1 artifacts exist; no new full run was performed during mapping. Unknown until new runs: category-independent planning performance, service availability, retrieval completeness and visual success on novel scenes.

Extension: add a general typed object/recipe contract, dependency-based placement and parameter edits, source resolver with measured asset contracts, and category-independent visual judge. Route through the existing CLI with an explicit legacy-study option. Reuse the aDSL Asset/export boundary, not the study-only contract/compiler/solver. A declared arbitrary category is insufficient: unresolved objects must produce explicit failures rather than fallback boxes.


## Implementation update, 2026-10-20

Confirmed new execution path: run_general_scene.py -> GeneralLLMPlanner or explicit replay -> PlannerScenePlan validation -> to_general_plan (expressions and envelope preflight) -> general_assets.resolve_component -> compile_general -> aDSL Blender worker -> general_render -> measured geometry Judge and general_vision. Old run_finescene.py remains study-specific.

Confirmed: auto acquisition remains conditional through compilation, local QA failure continues to remote candidates before generation. Missing/failed components stop assembly. Component required features/material/reference criteria reach the visual Judge. TRELLIS.1 outputs use component IDs, not a hardcoded toilet filename. Generic shapes remain box/cylinder/sphere and object rotation remains yaw-only; unsupported plans are rejected explicitly.

Verified regression: 12 general tests passed (schema, support, expression rejection, rigid placement, parameter change, conditional routing, rejected/unresolved external assets, accepted mesh bounds and envelope preflight). Authored metrology fixture exported actual GLB/Blender with six review images under runs/general_metrology_verified_20261020_01. This is a prototype pipeline check, not evidence of photorealistic arbitrary-scene generation.

Real LLM planning attempts: botanical exhibit trials 01-03 were rejected. Preserved raw/repair validation evidence identifies fabricated relation endpoints, unsupported orientation and external components carrying placeholder primitive parts. These results do not establish reliable open-scene planning. The general planner now keeps raw output and makes one bounded repair attempt. No bathroom keyword template remains in the planner.

Limitations: aDSL internal editing of generated topology is not implemented; external geometry is rigid. Camera/lighting expectations remain metadata, generic render rigs may be occluded in enclosed scenes. No automatic final-visual repair loop or incremental cached edit execution yet. Final acceptance requires measured bounds and visual evidence; unavailable vision does not pass. Local catalog provenance is descriptive and does not establish licensing; network Poly Haven search is CC0 but availability is not guaranteed.

Final metrology review: actual Blender geometry passed, six images exist and were inspected locally; configured visual model timed out at 120 seconds. run_report.json remains rejected with accepted=false. No accepted scene or new TRELLIS generation is claimed in this change.

## Staged implementation update

Default path is now `run_general_scene.py -> StagedScenePlanner (decomposition -> component recipes -> layout) -> PlannerScenePlan -> to_general_plan -> asset resolution -> compile_general -> Blender -> actual bounds and visual Judge`. `--planner single` retains GeneralLLMPlanner; replay is explicit. `--resume-runtime` revalidates matching checkpoints; it is planning reuse, not incremental rendering.

New contracts include bottom mounting, floor contact, explicit contact footprints and requested edit scenarios. Recipe geometry is compiled before acquisition. `component_jobs.json` exposes conditional TRELLIS tasks. `general_repairs.py` emits scoped work orders but does not automatically execute final visual corrections. Workstation height/width edits passed real compilation; final scene review evidence remains separate. Regression suite: 16 staged + 12 general + 9 legacy tests passed.

## Implementation update, 2026-10-23

Surface-mounted rigid children may name a frozen horizontal parametric support part through `support_part` (for example `top_support` or `top_rail`). Planned and exported placement uses that part's face rather than the parent's overall AABB. Blender export writes `actual_support_contacts.json`; `actual_support_judge.json` records the measured patch, support plane, vertical gap, contact/support centers and envelope utilization. Those measurements are included in the visual Judge request so a shadow cannot override measured flush contact; images still decide appearance, identity, style and visual legibility.

The execution path now performs Blender-backed edit regressions for every `edit_check`: each edited plan is exported and measured with `general_render.py --geometry-only`, and `edit_scenarios.json` records support contact, planned offset error and rigid-size preservation. External rigid meshes are also checked against their declared envelope (minimum 60% utilization per axis); this catches a correctly placed but implausibly undersized generated component before visual review.

The live-agent pedestal probe in `runs/agent_mount_probe_20261023_01/` planned a 0.60 × 0.45 × 0.80 m editable root and a rigid 0.18 × 0.18 × 0.34 m generated urn with `support_part=top_support`. The full executor measured a near-zero top contact gap and passed geometry/support checks. The reused local TRELLIS.1 diagnostic urn remained visually rejected for its material and shape, so this is placement evidence rather than an accepted generated asset or arbitrary-scene success claim.
