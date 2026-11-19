# Teaching "Placement and Layout" to the Agent, and Turning Anti-Interpenetration into Executable Rules

Date: 2026-11-04
Nature: **engineering change notes + offline verification record**. This is not a FineScene Agent run; there is no `runs/`, no `run_report.json`, no `accepted=true`. All Agent-side data in this change come from the historical frozen run `runs/complete_discussion_room_20261023_01`.

---

## 1. Trigger

For the same sentence "generate a reception room" (生成一个会客厅), the directly constructed delivery was clearly more readable than the Agent's spatial planning; but the three Blender viewport screenshots given by the user also pointed out that the direct construction had interpenetrations, and that the curtains were only "for show" (样子货). Requirements:

- Hand the direct construction's **placement and layout** capability over to the Agent;
- At the same time keep the direct construction's **anti-interpenetration design**;
- Each side has its strengths; do not pick just one.

## 2. First Correct a Judgment

The previous diagnosis said "the contract has no enclosure concept at all", which is inaccurate. `src/scene_composition.py` already has `DesignGroup` / `CompositionIntent` / `density_and_rhythm` / `ViewIntent`, and lines 3399–3404 of the `LAYOUT` prompt in `src/staged_planner.py` already say:

> "the entrance must open onto the principal focal group ... secondary desks, lamps, plants, ottomans and side tables must stay in their named peripheral niches ... never let a large wall fixture ... become a foreground occluder"

**The language exists, the ruler does not.** `scene_spatial.validate_spatial_contract()` only validates schema, bounds and member uniqueness; `layout_solver.py` only does envelope-level SAT + clearance; `general_vision.py` reports `geometry`-class blocking, yet the local repairer can only change poses. So the model writes a composition essay, the validator only measures clearance, and everything else depends on probability.

What is really missing is: **turning composition sentences into deterministic metrics that are computable, reject, and return numeric values**, and **upgrading "anti-interpenetration" from hand-made experience to a part-level checker**.

## 3. Quantitative Evidence from the Historical Run

Data come from `runs/complete_discussion_room_20261023_01/agent_runtime/`:

| Fact | Value | Source |
|---|---|---|
| Frozen furniture inventory | 5 of 9 objects are shell; only 4 pieces of furniture | `decomposition_accepted.json` |
| Furniture footprint ratio | 10.415 % (1.666 m² of 16 m² clear area) | `coarse_layout_0_raw.txt` + dimensions |
| Enclosure group | 2 chairs, no common focal hierarchy | same as above |
| Activity-zone overlap | **2.318 m²** (including circulation zone × access area) | `coarse_spatial_judge.json / access_polygons_m` |
| Circulation shared-trunk ratio | 0.037 (5 routes each radiating from the doorway) | `spatial_plan_accepted.json` |
| Clearance margin | planned 0.400 / required 0.400 (margin 0); measured after compilation **0.395** | `coarse_spatial_judge.json` / `final_visual_qa.json` |
| Frozen contract expressions | numbers mixed with symbolic strings `'w/2+0.35'`, `'d/2+0.4'` | `spatial_plan_accepted.json` |
| Repair no-op | `coarse_layout_repair_2_raw.txt` wrote the bookshelf coordinates back unchanged | same directory |

The symbolic expressions in the frozen contract show again that the model guesses coordinates, formulas and circulation waypoints all within one stage, while downstream can only rely on another model to guess how to parse them.

## 4. Change List

### 4.1 New `src/composition_metrics.py` (pure standard library)

- `ARCHETYPES`: four measurable priors, `baroque_salon` / `reception_lounge` / `study` / `generic`: focal point, enclosure group, central axis, footprint-ratio range, focal-distance CV, orientation tolerance, enclosure arc, symmetry error, activity-zone exclusivity, circulation shared trunk, main-sightline occlusion.
- `infer_archetype(brief)`: Chinese/English keyword matching, with style words taking priority over room-type words.
- `evaluate(scene)` → metrics + rejection items. Each rejection item carries `measured` / `expected` / `repair_target`, **precise down to the layer that can fix it**: sparse inventory → `decomposition`, pose/orientation/symmetry → `layout`, zone/area/route → `spatial`. This is exactly to avoid the deadlock diagnosed last round of "the Judge wants to change parts, the repairer can only shift positions".
- `composition_archetype_prompt(brief)`: writes the thresholds into the prompt, instead of adjectives like "be gorgeous, be enclosed".
- `from_finescene(...)` / `composition_hook(...)`: convert live pydantic objects into a unified input; **the whole hook carries its own try/except guard**, so an adapter error only writes `blocked_checks` and never interrupts the existing planning run.

### 4.2 New `src/part_integrity.py` (pure standard library)

The SAT in `layout_solver.py` only proves two **wholes** do not collide, and cannot prove "the wall-lamp candle floats 18 cm above the scroll". This module performs deterministic checks on **part AABBs**:

| Check | Criterion | repair_target |
|---|---|---|
| `joint_gap` | The measured gap between two parts that declare the connection > 0.02 m | `geometry` |
| `floating_part` | Not grounded and more than 0.02 m from any part | `geometry` |
| `undeclared_intersection` | Cross-object solid overlap without a declared joint | `geometry` |
| `coplanar_z_fighting` | Elevated decorative strips overlapping coplanarly (normal grounded/shell contact excluded) | `geometry` |
| `clearance_zero_margin` | Measured clearance < required + 0.05 m safety margin | `spatial` |

`DESIGN_RULES` makes three of the direct construction's hand-made rules of thumb explicit: borders of the same thickness **abut rather than overlap** (preventing coplanar black seams), the chandelier **does not block the main-view sightline**, and clearance **never sits at the theoretical limit** (0.395 vs 0.400 is exactly how it failed).

### 4.3 Four Small Changes to `src/staged_planner.py`

1. Import the two new modules at the top;
2. `prepend_experience(DECOMPOSE, ...)` of the `decomposition` stage appends the archetype prior;
3. `spatial_plan_instructions` of the `spatial` stage appends it;
4. `LAYOUT` of the `coarse` stage appends it; and a guarded `composition_hook` is inserted in `validate_coarse` before `coarse_spatial_judge.json` is written, writing `composition_archetype` / `composition_metrics` / issues into the report and saving a separate `composition_judge.json`.

The integration strategy is **minimal intrusion**: the logic of the 358 KB planner body is untouched, and a hook failure degrades automatically.

### 4.4 New Files on the Delivery Side (directory `deliveries/baroque_reception_pbr_20261104/`)

- `export_blend_parts.py`: exports part world AABBs from the .blend, **including the evaluated mesh of CURVEs** (exporting only MESH would miss 142 scrolls/curved legs/chandelier arms, because their bevel is not in the control-point bound_box); derives the declared connection pairs from `owner + name pattern`, to avoid mistaking the sofa armrest `arm_1` for a chandelier arm.
- `verify_teaching.py`: runs positive and negative samples + hook smoke test with the same ruler, outputting `teaching_report.json`.
- `parts.json` / `joint_pairs.json` / `scene_normalized.json`: geometric facts, not manually filled in.

## 5. Offline Verification Results

`python verify_teaching.py` (`E:/3DUse/aDSL/venv/Scripts/python.exe`):

| Metric | Agent historical run | This direct construction |
|---|---|---|
| Footprint ratio | 10.415 % | 21.296 % |
| Enclosure group members | 2 | 4 |
| Focal-distance CV | 0.0000 | 0.0157 |
| Enclosure arc | 180.0° | 185.7° |
| Activity-zone overlap | **2.318 m²** | 0 |
| Circulation shared trunk | 0.0374 | not measured (see boundaries) |

- By their respectively inferred archetypes: the Agent is `needs_revision / 4 issues`, this delivery is `passed / 0 issues`.
- Re-running **uniformly with the same `reception_lounge` thresholds**: the conclusion is unchanged (4 / 0), ruling out the possibility of "picking lenient thresholds".
- The 4 rejection items are `sparse_inventory`, `missing_enclosure_group` (→ decomposition), `use_areas_are_not_exclusive`, `radiating_routes` (→ spatial). Each carries measured and expected values, and can be fed directly to `prompt_evidence`.
- The `composition_hook` smoke test was fixed from `blocked` to `needs_revision / 3 issues` (a real bug was found where the variable `cor` is undefined under an empty loop, and has been fixed).
- `part_integrity` reports `clearance_zero_margin → spatial` for both clearance numbers recorded by the Agent (0.400 / 0.395).

## 6. Using the Checker to Fix Our Own Delivery in Reverse: 41 → 0

The physical causes behind the user's three screenshots were all **part-level**, not envelope-level:

| Screenshot / location | Root cause | Fix |
|---|---|---|
| Wall-lamp scroll disconnected from the candle | Arm end z=2.02, candle bottom z=2.20, difference 0.18 m | Raise the arm to 2.20 + add a `bobeche` tray |
| Sofa and side table interpenetrating | West side-table top x∈[-3.5,-2.9] intersects sofa back x∈[-2.96,-2.82] by 1.13e-3 m³ | Move the side table to the ±3.52 niche |
| Chandelier crystal strand floating | Sphere spacing 0.09 m > sphere height 0.065 m, a 0.025 m gap per bead | Change to 0.065 m spacing + one continuous `crystal_chain` |
| Chandelier ceiling canopy passing through the ceiling | The Empty root had an extra +0.30 m double offset | Zero the root Z, attach the canopy at H-0.035, recompute the chain end |
| Mirror frame inserted into the fireplace cornice | Frame bottom 1.28 m, cornice 1.335–1.528 m; mirror top 3.92 m also passed through the 3.6 m ceiling | Rescale the mirror to 1.58–3.58 m |
| festoon floating | Start point r=0.30 lies outside the bowl r=0.17 | Pull the start point inside the bowl at r=0.16 |

Before and after the fix (`part_integrity` on the real AABBs of the same .blend):

| | Before | After |
|---|---|---|
| floating | 7 | **0** |
| joint_gap | 34 | **0** |
| undeclared_intersection | 33 | **0** |
| coplanar | 0 (grounded/shell false positives filtered) | 0 |
| blocking findings | 41 | **0** |

In addition, the 5 box-shaped "curtains" were replaced with **variable-section parametric pleated cloth panels**: 6 sinusoidal pleats, waist `gather=0.30` gathering, 1.5 cm off the floor, top attached to the cornice board, 14/18 segments along y/z respectively, with the control constants written into `adsl_params`. Texturing uses `TexCoord.Object` + real Poly Haven velvet PBR, with no UV needed.

**Why cloth simulation was not done at the time**: the judgment then was that after baking, the Cloth modifier is a frozen mesh, which would break aDSL's "single object, each part independently editable", so a static parametric cloth panel was delivered first.

> **2026-11-04 follow-up update (additional user requirement)**: this judgment was overturned. The correct solution is not to skip simulation, but to make the baked result an **independent aDSL part** (with its own mesh datablock / material / `adsl_params` recording all simulation constants), and to keep a **hidden pinned source mesh** for deterministic re-runs; the editing granularity changes from "dragging vertices" to "changing parameters and re-running", so editability still holds. See `E:/3DUse/DirectAgent/src/cloth_drape.py` for the implementation, which has also been wired back into this delivery's `build_baroque.py` (`cloth_simulation.json` records the engine and frame count). This section keeps the original judgment to reflect truthfully the decision chain at the time.

## 7. Honest Boundaries

1. **No end-to-end Agent run was performed**. The new hook is integrated and passed the offline smoke test, but its convergence behavior under a real model service (whether it turns into another kind of whack-a-mole) has not been verified. In keeping with the traceability requirement of `FineScene/AGENTS.md`, no run evidence is fabricated here.
2. **Composition metrics are necessary but not sufficient conditions**. They can block "4 isolated pieces of furniture + radiating circulation + large areas of overlap", but cannot prove "looks good". The render visual Judge still owns that.
3. **The current integration point is in coarse**. A sparse inventory is rejected at coarse and stops early, but the real remedy is to add an inventory lower bound at the decomposition layer; `composition_metrics.thresholds()` already provides the values, but is not yet wired into `validate_decomposition`.
4. **This version has no circulation contract**. In `scene_normalized.json`, `access_areas=[] / routes=[]`, and circulation-class metrics are marked `not_measured` and do not take part in the pass decision; no trunk circulation was invented out of thin air to inflate the score.
5. **AABB is a conservative approximation**. Part-level AABBs can catch disconnection and obvious interpenetration, but not subtle self-intersections inside curved surfaces; that needs mesh-level detection or looking at images.
6. After the chandelier was put back in place, the central-axis view is somewhat occluding the mirror (the oblique view is complete). This is the physically correct hanging position, and "raise it until it passes through the ceiling" is no longer used to buy composition.

## 8. Suggested Next Steps (by priority)

1. Wire `composition_metrics.thresholds()` into `validate_decomposition`, so that a sparse inventory is rejected at the **very top upstream**, instead of idling at coarse.
2. Move checks such as the 0.8 m passage / pull-out zone / opening clearance from the **generation loop** to **delivery acceptance**: place it to look good first, then measure with the ruler, and adjust only if it fails. This way the freedom of direct construction and the rigor of the Agent can both be kept.
3. Escalate `repair_target=geometry` findings directly to decomposition/part-level reconstruction, and stop letting the layout layer idle.
4. Reuse validated recipes across runs (the kind of run-10 wall shell → run-11 same room).
5. To verify the real benefit of this integration, run one comparison run with a model service, and compare the issue count and convergence rounds of `composition_judge.json`.
