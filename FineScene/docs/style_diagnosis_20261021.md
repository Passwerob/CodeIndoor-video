# Style Failure Attribution and Generic Curve Capability Revision

This change is to the system contract, compilation capability, physical validation and repair protocol. No Baroque scene was hand-written, and no developer looking at images replaced the system visual Judge.

## Existing Evidence and Attribution Boundary

- The prompt protocol contained a locatable contradiction: decomposition repair could replace `/objects`, yet the prompt required keeping all IDs; the single-asset description once led the model to think there could be only one scene object, whereas a complex local mesh needs independent child objects. In the real run `_01` the model rewrote back and forth between the root's parametric/auto labels, did not correctly produce an executable external child asset, and only after a whole-round fallback did it split correctly.
- The original request was "carved or scrolling vine" (雕花或卷草). The old requirement extraction froze curve, scrolling vine and leaf-ornament relief all as mandatory, introducing unnecessary asset dependencies. The new extraction rule preserves the logical alternative relation; the original model output of `_02` explicitly chose the allowed scrolling-vine realization.
- The backend expressiveness limit is real. Previously there were only box/cylinder/sphere/book, and curved paths could not be executed. Adding more emphasis in text does not produce a missing primitive.
- Model execution reliability is still insufficient: the curve structure in `_01/replan_01` passed after two repairs, but the third content batch exhausted its repairs on top-book clearance; it also tried to change a frozen support relation, which the validator rejected. This shows the model makes planning/repair errors under the current contract and budget; it cannot be inferred that the model does not understand Baroque at all, or that swapping the model would certainly fix it.
- There is no same-protocol, same-tool, same-requirement multi-model comparison yet, so no causal percentage such as "prompt accounts for X, model accounts for Y" can be given.

## Generic Changes Landed

1. `sweep` recipe: 2–64 path points, constant circular cross-section radius, center, rotation, color and material. Path points are offsets relative to the center, and the size is a constraint bounding box containing the path and radius; the path is not silently scaled. It compiles to a combination of native aDSL cylinders and spheres, keeping editable expressions.
2. Path contact: sweep-to-box uses the actual segment-to-box distance, and sweep-to-sweep uses the segment-to-segment distance; attachment contact cannot rely on the outer bounding box alone. A curve cannot be a certified horizontal bearing surface; unsupported curved-surface joints continue to be rejected.
3. Edit invariance: real changes in path and radius are checked; a rigid part cannot be silently deformed within a fixed bounding box, and valid parameter edits that change only the curve cannot be missed.
4. Style contract: `swept_path` must be bound to a real `component/part`, and the recipe and final plan verify the actual sweep primitives. `freeform_surface` still requires an external child asset; a round-tube curve cannot pass for leaf carving or relief.
5. Decomposition repair allows regrouping the component inventory that is not yet frozen, while the recipe and layout stages still freeze IDs. Root structure and external rigid descendants are clearly distinguished; internal `rigidity=rigid` does not mean a mesh was retrieved/generated.
6. Requirement extraction preserves the semantics of "or", and does not turn style examples into extra required objects. Quality requirements and the system's visual acceptance are not relaxed.

## Verification

`python -m unittest discover -s FineScene/experiments -p 'test_*.py'`: 93 passed.

Includes regressions for curve-to-box/curve distances, measurement after rotation, real contact and gap/penetration rejection, rigid path edits, text promises being unable to bypass the style gate, decomposition being able to add external descendants with the original requirement frozen, and so on.

`experiments/verify_sweep_export.py` → `runs/sweep_export_check_20261021_01/`: passed the production Blender export and the actual part-geometry audit. It is an explicitly labeled developer geometry-regression fixture, visually unaccepted, and **cannot be used as evidence of successful agent generation**.

Real system runs use `src/run_general_scene.py` and the original Baroque prompt, each saving a source snapshot, model outputs and failure evidence in a new directory:

- `runs/agent_baroque_sweep_20261021_01/`: rejected after two planning rounds; the first-round decomposition failed, after fallback the structure passed and content clearance failed. It did not enter whole-scene rendering/vision or TRELLIS.
- `runs/agent_baroque_sweep_20261021_02/`: adopted the corrected decomposition and logical-alternative protocol. The final status is as given in that directory's `run_report.json`.

## Still Unresolved

The current sweep is a polyline-path approximation made of overlapping cylinders/spheres; it is **not** a smooth-surface modeler, variable-section carving, profile sweep or watertight solid. The system visual Judge must check whether it is merely tubular decoration, whether the curves are smooth enough, and whether the overall style holds.

The automatic image-reference generation backend was not implemented this round; reference retrieval may still fail to find a qualifying image. TRELLIS cannot be counted as executed without a qualifying input. Tasks that need genuine relief are still affected by this capability gap.

To later separate model influence, fix the requirement, capability catalog, repair budget and Judge, and compare different planning models on requirement fidelity, executable geometry, number of repairs, time and per-item style acceptance; no conclusion may be drawn from a single run that happens to succeed after swapping the model.
