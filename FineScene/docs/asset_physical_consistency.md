# Physical Consistency and Local Repair of a Single aDSL Asset (2026-10-20)

2026-10-25 update: what is recorded below is the historical implementation with at most two rounds initially. The current entry point has removed the fixed visual-repair count and now continues until the Judge and geometry checks pass, or the same candidate produces the same failure evidence again. When the vision service is unavailable, there is no legal local modification path, or the candidate is unchanged, it still stops and keeps the unresolved result; these cases are never counted as passing.

## Why the Inside of an Asset Must Be Checked

Previously the generic compiler checked the size, scene position and inter-component relations of the whole component. Even if a component internally has only a base plate and a floating shelf board, its outer bounding box may still be legal. This round actually reproduced the problem before the change, recorded in `runs/asset_physics_20261020_01/before_fix.json`.

Two levels of constraints are therefore adopted: scene-level object relations + asset-level part-assembly relations. aDSL stores the editable structure; local TRELLIS meshes are still connected through the component interface, and the overall bounding box of an external mesh must not pass for an internal assembly check.

## Implemented Mechanisms

1. **Planning inventory inside the asset.** The decomposition stage can use `internal_parts` to fix internal part IDs, functions, structure/content/detail roles and rigidity. The recipe stage may not omit declared parts or change their rigidity. `internal_clearances` stores declared functional clearances, which recipe repair cannot delete or reduce. The recipe also receives the original user brief, to avoid keeping only the frame description while losing the contents.
2. **Assembly graph.** A multi-part parametric asset must provide `assembly.roots` and `relations`. A root part must touch local Z=0; every part must connect to the root through a geometry-verified support/fixing relation. `supported_by` means truly resting on a horizontal top surface, `attached_to` means a bounded-penetration contact fixing, and `clearance` means a functional gap. Declaring a relation cannot replace actual measurement.
3. **Geometry solved by the interface.** Internal `parent + mount=top/bottom` makes the contents follow the actual supporting surface. `fit_between` solves the specified-axis size and center of a structural part from the measured surfaces of two parts, so that the LLM does not have to guess each board's length and position separately. For example, a side panel can be confined between the upper surface of the base plate and the lower surface of the top plate. At this stage, surface-to-surface solving supports unrotated parametric boxes with untransformed parent objects.
4. **Internal geometry and edit validation.** Boxes use OBB separating-axis checks for interpenetration; curved surfaces use conservative envelopes, and unsupported fixed contact on curved surfaces is rejected outright. An edit recompiles the internal parts and checks support, connection, clearance, collision and the size of rigid contents. A whole rigid asset also checks internal relative positions, to prevent internal deformation while the outer size is unchanged.
5. **Blender measurement.** After export, real vertices are collected by stable part path and transformed back into asset-local coordinates, outputting `actual_part_bounds.json`, which is compared with the compile record and the internal assembly re-checked, written into `actual_internal_judge.json`.
6. **Final repair execution in the initial implementation.** The final Judge can return `part_ids`. Layout, parametric part geometry and color issues triggered at most two rounds of candidate repair at the time; modifications were limited to exact fields. Object provenance, component size, part ID, rigidity, assembly relations, surface-to-surface constraints and clearance contracts stay fixed. Rigid book sizes are not on the repair whitelist. A candidate must pass internal/scene geometry and edit checks, then be exported, re-rendered and re-judged, before it can possibly be accepted.

This historical entry point is `src/run_general_scene.py`, where `--max-visual-repairs 0/1/2` then controlled the number of final repairs. Asset resolution is executed only once, and candidates reuse the accepted external assets; at present each round still recompiles and re-renders the scene, and rebuilding only part of the Blender mesh is not implemented.

Camera, provenance replacement, or problems that require changing the assembly topology still output a pending work order, and are not disguised as automatically repaired. When the service is unavailable the geometry repairer is not called, and the scene is not rebuilt to mask a service failure.

## Actual Verification Results

Regression command:

```powershell
& E:/3DUse/aDSL/venv/Scripts/python.exe -m unittest discover `
  -s E:/3DUse/FineScene/experiments -p 'test_*.py' -v
```

55 tests passed, and 9 compile checks involving source modules passed. The tests include the old bookshelf edit, generic scene, internal assembly, surface-to-surface solving, inventory/clearance preservation and final-repair rejection paths. Unit tests with injected model/vision results are mechanism verification, and do not represent the success rate of the real LLM or vision service.

The hand-written generic regression asset is in `examples/general_bookshelf_assembly.json`: one aDSL asset containing a base plate, left and right side panels, a middle shelf board, a top plate and two simplified rigid books. It is not a keyword template, and is not evidence of natural-language generation success.

| Check | Result and evidence |
|---|---|
| Reproduction of the original floating shelf board | The original check passed; it now rejects the missing internal assembly contract |
| Real connection check | Rejected when the shelf board is shortened so it no longer touches the side panels; writing attached_to still cannot pass |
| Layer clearance 0.32 → 0.42 m | The measured bottom of the upper-layer books moves up by about 0.10000002 m; the lower-layer books do not move |
| Rigid contents | The three-axis sizes of both books in Blender stay unchanged (1e-6 m tolerance) |
| Layer clearance reduced by 0.1 m | Insufficient clearance between the books and the upper board, rejected; the original plan is not modified |
| Prototype export | The actual GLB, Blender file and per-part geometry checks before and after the edit were all completed and passed |

Summary evidence: `runs/asset_physics_20261020_01/verification_summary.json`.

The pre-edit export, front view and multi-view are in `runs/asset_bookshelf_full_20261020_02/`; the post-edit ones are in `runs/asset_bookshelf_edited_20261020_01/`, containing `model_plan.json`, aDSL, Blender/GLB, measurements and images. The post-edit run is explicitly marked as a geometry/export experiment, with independent visual acceptance not requested.

## Real-Model Trial Runs That Did Not Pass

All real-model runs are kept:

- `asset_bookshelf_llm_20261020_01`: ignored the single-asset boundary and split into several scene components; support semantics failed; a description of the asset-internal relation capability was then added.
- `asset_bookshelf_llm_20261020_02`: accepted the single-asset decomposition, but the internal recipe showed board interpenetration and side fixing mis-written as top-surface support; the limited repair failed.
- `asset_bookshelf_llm_20261020_03`: still did not use the surface-to-surface constraint correctly, keeping a false support relation; it also exposed the problem of the recipe context losing the book requirement. The original brief, internal part inventory and fixed-clearance rules were added afterwards, but there is no new real generation success verification yet.
- `asset_bookshelf_llm_20261020_04`: after switching to another configured planning model, the service returned `service_unavailable_error` with the message "Our servers are currently overloaded. Please try again later."; no verifiable new plan was obtained.

These failures show that the checker can already intercept errors, but it cannot yet be claimed that the success rate of natural-language asset generation has been solved. Later work needs to verify the new internal planning contract, and evaluate whether the structural planning of complex assets should be further split into independent stages.

## Visual Evidence and Service Limits

The first full re-review timed out. A separate ordinary JSON vision diagnosis returned real feedback within 22.21 seconds: two oblique views hid the book tops, so the clearance could not be confirmed. Accordingly a near-horizontal front view was added, to avoid modifying correct geometry or shrinking the books merely because of occlusion.

Vision calls now use locally strictly validated JSON output and are batched at no more than 3 images, each batch must pass, and all required images must be covered. The structured transport option is kept for comparison. This adjustment is a compatibility/latency mitigation, not a root-cause fix of a located server-side problem; the full re-review after adding the front view still timed out after about 92 seconds, and `asset_bookshelf_full_20261020_02/run_report.json` stays at `accepted=false`.

The final repair loop has passed the simulated-feedback regression, but this round did not complete a whole-round service verification of "real visual rejection → automatic modification → independent visual acceptance"; manual inspection or a single successful call was not passed off as whole-scene acceptance.

## Capability Boundaries

This is a geometric assembly and edit consistency check, not a certification of material strength, center of gravity, friction, screw load capacity, tip-over resistance or rigid-body dynamics. Fastening relations still depend on declarations, and the curved-surface contact capability is limited. Declared internal inventory/clearances are enforced, but whether the planning stage fully understands the user's intent still needs semantic and visual review.

An old multi-part generic plan without an internal contract is now rejected and needs assembly relations added; it is not silently let through. The old study-room dedicated entry stays independent, and its regression tests pass. The physics rules live in FineScene's aDSL compile/planning layer, and the underlying aDSL library was not modified.
