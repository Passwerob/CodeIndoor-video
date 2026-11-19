# General Scene Planning Revision Record — 2026-10-20

For the later internal asset revisions see [asset_physical_consistency.md](asset_physical_consistency.md): internal assembly/edit checks, face-to-face dimension solving and bounded final visual-repair execution have been added. The text below keeps the state and evidence at the time of the first revision; for statements that something is "not yet executed automatically", the newer record prevails.

## Conclusions of this round

This round changed the general entry to staged planning, and actually verified local repair, planning-checkpoint reuse, aDSL compilation, parameter editing and Blender geometry audit. It resolves the planning and execution interface problems of the general scene system; it cannot be taken as a claim that any scene can be stably generated as a photorealistic finished product.

aDSL holds the semantic components, parameters, dependencies, poses and assembly interfaces; retrieval and TRELLIS provide the local geometry that is needed. Components are split according to the needs of independent editing, motion, source and assembly, rather than generating the whole scene into an uneditable mesh, and without requiring every object to be simplified to primitives.

## Problems, causes and revisions

| Observed problem | Cause located | Revision implemented |
|---|---|---|
| Whole-scene planning produced a nonexistent `scene_front_boundary`, unsupported poses, or external assets with placeholder geometry | One output mixed semantics, coordinates, sources, relations and build details, and the whole was rewritten after a failure | Decomposition → per-component recipe → layout; the program generates coordinate conventions and relations; IDs are frozen |
| The workbench geometry check passed, but the table legs grew upward from the tabletop | `parent + origin` could not express "mounted at the bottom of the tabletop", and the old geometry check did not check grounding | Added the bottom interface and grounded; the actual table-leg top meets the parent's bottom, and the bottom must be on the ground |
| When the table height was changed, the rigid legs changed size, or specimens went beyond the scene extent | Decomposition mislabeled a stretchable structure as rigid, and the extent accommodated only the initial state | The decomposition stage checks rigid dimensions, the support chain and the height range after editing; the layout stage executes edit_checks |
| A fern's crown spread could not fit into the flowerpot's support range | The overall outer bounding box was used in place of the bottom contact interface | Declare support_footprint; also require later visual evidence to verify contact, with no claim of stability certification |
| A local recipe's dimensions looked right, but the actual geometry deviated from the component origin | The old check compared only length, width and height, not the range of the bottom-center coordinates | Compile the local aDSL geometry before asset acquisition, and verify the XY-centered envelope and the Z=0 bottom face |
| One layout error caused unrelated components to be regenerated | No stage boundaries or verifiable reuse | Exact JSON pointer repair, at most two repairs per stage, reuse after re-validation of matching checkpoints |
| Visual request timeout | Latency of the external model service/request; the server-side root cause has not been located | Shortened request metadata and output budget, 180-second limit; on failure the rejection is kept and existing images are reused for re-review. This is a mitigation |

Main files: `src/staged_planner.py`, `src/general_planner.py`, `src/general_scene.py`, `src/run_general_scene.py`, `src/general_vision.py`, `src/general_repairs.py`.

## What planning must now answer

1. Which functional zones, objects and acceptance evidence correspond to the user's requirement; what is the reason each object exists.
2. Which parts must be independently editable and which geometry stays rigid; what are the dimension parameters, supports and mounting interfaces.
3. Whether each component goes through aDSL, retrieval only, generation after retrieval fails, or direct generation because a custom appearance is specified; and the rationale.
4. Which shape and material features an external component must keep; which contour, bottom or interface the reference image must show.
5. How the components are assembled once completed, whether the edits the user requested still hold, and which real images are used for the final acceptance.

For a surface-mounted external child component, the decomposition stage must also answer "which real part of the parent object supports it". `support_part` can only point to a frozen horizontal box-like part in the parametric parent object; for example a pedestal's `top_support` or a bookshelf's `top_rail`. The layout stage places the child's bottom on that part's top face, and neither the highest point of the decoration nor the parent object's overall AABB may be used instead. After Blender export, `actual_support_contacts.json` and `actual_support_judge.json` record the real vertex contacts, support surface, vertical gap, center offset and contact footprint; the geometry Judge uses this data to verify position and size, and the visual Judge also uses this evidence to avoid treating render shadows as a basis for moving things.

Once planning passes, component building cannot change the object list, dimensions or sources; layout repair cannot eliminate collisions by shrinking objects, changing sources or deleting requirements. Requirement fidelity in the decomposition stage currently still relies on the prompt and later review, with no formal proof of semantic equivalence.

Source rules:

| Path | Applicable condition | Acceptance requirement |
|---|---|---|
| parametric | Structures that can be expressed with existing primitives and need parameter control | Real local geometry, relation checks, scene vision |
| retrieve | An existing ordinary asset fits the requirement | Candidate geometry and vision; on failure, explicitly stop this route |
| auto | An ordinary asset may fit, but generation is allowed as a fallback | Retrieve candidates and QA first; only after failure check the reference image and call TRELLIS |
| generate | A custom appearance requested by the user has a concrete description | Reference-image QA, generated-mesh QA, assembly geometry, scene vision |

TRELLIS in general planning currently means TRELLIS.1. Without access to the restricted DINOv3 weights, TRELLIS.2 cannot be written up as available. Local generation is still a rigid mesh, and being editable at the scene level does not mean the internal topology of the generated mesh is editable.

## Measured evidence

### Workbench: real LLM planning, real compilation and editing

The final plan is in `runs/staged_workstation_20261020_06/`, with 8 independent components: tabletop, four legs, inspection board and two specimens. This development went through many failures and revisions; it was not a one-shot success, nor is it a success rate on an independent evaluation set.

`edit_verification.json` records:

| Edit | Actual check result |
|---|---|
| bench_height +0.1 m | Both specimens' Z increased by 0.1 m; dimensions unchanged; all four legs' bottoms at Z=0; geometry passed |
| bench_width +0.2 m | Specimen dimensions and Z unchanged; the four legs still grounded; geometry passed |

The last decomposition repair changed only `/extent`, and all 8 component recipes were reused. An earlier repair adjusted the rigidity of the four legs, and the other four recipes were reused. Checkpoint events are saved in each run's `agent_runtime/stage_events.json`.

The original render with the wrong table-leg direction is kept at `runs/staged_workstation_full_20261020_01/visual_review/overview.png`; the corrected Blender result is in `runs/staged_workstation_full_20261020_02/`, where the exported geometry and the actual Blender bounds both passed, and 11 review PNGs were generated. This example explicitly requires prototype, only verifies the assembly mechanism, and is not a photorealistic scene result.

The first full visual request timed out after 181.81 seconds, and `run_report.json` keeps `accepted=false`. An independent re-review was then run with the same set of images and a slimmed-down semantic request, and it also timed out after 181.81 seconds; the result is recorded in `final_visual_qa_retry.json` and does not overwrite the first failure record. The corresponding work order is in `repair_work_orders_retry.json`, requiring only a retry of the image review and forbidding geometry changes on that basis. The final visual acceptance is still not complete, and this round does not claim that the scene has been accepted by an independent Judge.

### Plant and sculpture display area: source-planning evidence

The real LLM plan is in `runs/staged_botanical_20261020_04/`: two editable pedestals go through aDSL; the custom asymmetric porous stone sculpture goes through generate; the flowerpot and fern go through retrieve.

The plan was replayed with the current CLI to export jobs, and the result is in `runs/staged_botanical_jobs_20261020_02/component_jobs.json`. The sculpture job contains an asymmetric contour, holes, a flat mounting base, an isolated reference image and component/scene QA. These are only jobs and source decisions; this sculpture has not yet been generated nor the display area assembled.

The retained plan also has a semantic risk: apart from the standalone flowerpot, the fern retrieval query still contains "potted", which may bring in a duplicate flowerpot; schema passing cannot be taken as the semantics being fully correct. The latest decomposition prompt already requires avoiding duplication, but there is no cross-scene evaluation proving it can be stably eliminated.

### Regression verification

Executed with `E:/3DUse/aDSL/venv/Scripts/python.exe`:

| Command | Result this round |
|---|---|
| FineScene/experiments/test_staged_planner.py | 16/16 passed |
| FineScene/experiments/test_general_planner.py | 12/12 passed |
| FineScene/experiments/test_edit_pipeline.py | 9/9 passed |
| py_compile of the six modified source modules | passed |

The tests cover really compiled bottom/grounded and editing, post-edit scene extent, bottom contact and crown spread, local repair isolation, cache reuse, service failures not changing the scene, and the scope of dependency re-checks. Unit tests that inject a mock model do not count as real LLM success cases.

## Usage

```powershell
& E:/3DUse/aDSL/venv/Scripts/python.exe E:/3DUse/FineScene/src/run_general_scene.py `
  --brief "一个户外植物与雕塑展区，台座宽度可编辑，有定制多孔石雕与蕨类植物" `
  --out E:/3DUse/FineScene/runs/botanical_new --plan-only
```

(The `--brief` value is the sample user input, kept in Chinese. Gloss: "an outdoor plant and sculpture display area with an editable pedestal width, a custom porous stone sculpture and ferns".)

After checking the plan, the full build can be replayed into a new directory through `--planner replay --plan .../planner_output.json`. To continue the same staged planning, keep the original brief and specify `--resume-runtime .../agent_runtime` and a new `--out`. `--planner single` is the whole-scene planning baseline, not the default flow.

Each round's output keeps the raw requests/outputs, errors, repair paths, checkpoints and image hashes; a failure is not silently replaced by a placeholder box or by an acceptance pass. Cache matching includes the model profile path but not yet a version fingerprint of that file's content; after modifying the model configuration under the same path, a new planning directory should be used and the old cache not resumed.

## Capabilities not yet closed and follow-up priorities

1. **Final visual-repair execution.** Work orders can already be output that localize to layout, geometry, source, material, camera or decomposition, together with the dependency scope; they are currently not executed automatically. A transactional loop of bounded modification, re-compilation/rendering and re-Judging needs to be implemented, keeping before-and-after evidence.
2. **A general end-to-end scene containing really generated components.** This round did not re-run TRELLIS, and the plant display area only completed planning. The next round should complete one example of "parametric structure + retrieved part + generated part", to verify how component dimension, material and contact failures are handled locally.
3. **Cross-category evaluation.** With fixed inputs and budget, compare whole-scene planning against staged planning across categories such as workspace, indoor, outdoor and exhibition display, and count acceptance rate, call count, elapsed time, proportion of rebuilt components and edit retention. The current development trajectory is insufficient to support a claim of generality.

The existing builder still supports only box/cylinder/sphere recipes and object yaw; collision is conservative AABB; materials, photography and enclosed-space cameras are not yet mature. TRELLIS.1 component export keeps the predicted mesh colors and passes the Planner's material requirements into the Blender baking profile, forming a UV-mapped 1024² base-color texture; candidates missing colors, UV or a non-empty base-color map are rejected before entering the component visual Judge. By default on local 16 GB VRAM the native 256³ sparse features are average-pooled to 160³ before FlexiCubes mesh decoding; this is a configurable VRAM trade-off made to complete local inference, and `component_contract.json` records the native/actual resolution. The profile provides only auditable base color, roughness, metallic and slight procedural variation; it is not learned material segmentation, does not generate learned roughness/metallic maps, and is not TRELLIS.1's official Gaussian multi-view appearance baking. Passing the texture gate does not mean visual semantics, geometric completeness or style have passed the Judge. Multi-root support and contact-interface checks also do not stand in for center-of-gravity, friction and mechanical stability simulation.

The existing revisions establish clear boundaries for the closed loops above; verification of "stable photorealistic production for arbitrary scenes" has not yet been completed.

## 2026-10-23: Agent-named support surface and local asset measurement

The real staged Planner's pedestal probe, without any decomposition repair, gave two objects: the editable pedestal `walnut_pedestal` (0.60 × 0.45 × 0.80 m) and the independent rigid generated child component `carved_urn_finial` (declared envelope 0.18 × 0.18 × 0.34 m). For the child component the Agent chose the parent object's internal `top_support`, and declared `mount=top`, bottom-centered contact, rigid and not stretched during assembly. The corresponding plan is saved in `runs/agent_mount_probe_20261023_01/planner_output.json`.

After handing this Agent plan to the full candidate executor and doing an assembly diagnostic with the existing local urn mesh, the Blender output gave a pedestal Z top face of 0.800000012 m and an urn bottom face of 0.800000020 m, a difference of about 0.000000008 m. The measured contact center was X=0.00257 m, Y=-0.00170 m; the contact footprint was 0.1106 × 0.1095 m, falling within the 0.18 × 0.18 m contact range declared by the Agent. The mesh bounding box was 0.1800 × 0.1790 × 0.3071 m, and the occupancy relative to the declared envelope on the three axes was about 100%, 99% and 90%; it did not exceed the envelope. `actual_support_judge.json` and `actual_geometry_judge.json` passed, and the latest measurements and images are in `runs/agent_mount_probe_final_20261023_01/`.

This urn is a local TRELLIS.1 diagnostic output previously rejected by the visual Judge; here its component visual acceptance status was bypassed only to verify size, position and contact. The scene visual Judge still rejects the urn's terracotta color, broken details and off-target contour, so the whole scene stays `accepted=false`. The visual review had also misread a cast shadow as a contact gap; the executor now passes the Blender-measured surface positions, contact gaps and center offsets into the Judge, and requires them to take precedence over shadow-based inference of geometry, while the image is still used to judge whether the contact is visually clear. After adding this measurement evidence, the latest Judge no longer gives a blocking layout defect and still rejects on appearance; one `execute_with_repairs` also kept geometry passing, but the external rigid mesh was not pretended to be fixed or scaled to pass. The visual report is in `runs/agent_mount_probe_measured_vision_20261023_01/`, and the repair-loop record is in `runs/agent_mount_probe_repair_loop_20261023_01/`.

The more complex Baroque bookshelf real probe also had the decomposition Agent correctly pick `top_rail`, keep the exact urn size and the rigid generation path, but the full bookshelf recipe did not pass edit verification: multiple candidates failed on missing internal parts, support relations and width/height parameter expressions. This example shows that the named-support protocol itself works, but does not yet prove that the Agent can generate the physical recipe of complex furniture in one pass. The corresponding failure evidence is kept in `runs/agent_placement_probe_20261023_02/` and `runs/agent_placement_probe_20261023_03/`.

Planning repair now supports a delete operation, but it can only delete existing fields that Pydantic explicitly reports as `extra_forbidden`; deleting arbitrary fields is still rejected, and the full schema, assembly and edit validation is still re-run afterward. This revision fixes a real failure in which the model set `requirements_extra` to null but could not clear an illegal field.

The updated targeted regressions are: 22 for general planning, 37 for staged planning, 14 for visual repair, 11 for asset physics, 10 for sweep geometry, and 6 for TRELLIS.1 texture export, 100 passed in total. The texture tests run in `research/trellis_windows_venv`, and the rest use `aDSL/venv`. This is still mechanism and single-scene probing, not an evaluation of production rate of photorealism for arbitrary scenes.
