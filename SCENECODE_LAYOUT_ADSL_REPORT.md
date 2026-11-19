# SceneCode Scene Organization × aDSL Single-Object Generation — Combined Implementation Report

> Goal (user's own words): **"What I want is SceneCode's scene organization and placement ability, combined with the single-object generation ability of our tuned aDSL"**.
> This document records only real results and real limitations; anything not run is labeled as such, and getting the chain to run is not claimed as a paper reproduction.

---

## 1. Division-of-labor contract (the core of this round)

| | SceneCode side | aDSL / FineScene side |
|---|---|---|
| Responsibility | What is in the room, how many stages, where to put it, orientation, relations among objects, circulation paths, review feedback | What each object looks like, editable part hierarchy, material and geometry |
| Artifact | `layout.json` (`scenecode.layout.v1`) | `living_room.adsl.json` (aDSL AST) + `.blend` |
| Source of facts | **Generation-phase** SoT | **Editing-phase** SoT (phase isolation, confirmed) |
| Forbidden | Producing any geometry / bpy code | Deciding spatial relations between objects |

The bridge layer does only one thing: stamp SceneCode's `placement{x,y,z,yaw_deg}` onto each aDSL object's `xform` via FineScene's own
`apply_pose(asset, mat4(pos, yaw))`. **No geometry was newly written.**

### Real source of the SceneCode contract (read from the source prompts, not from the paper's description)

- `scenecode/prompts/data/floor_plan_agent/designer_agent.yaml` → `generate_room_specs()`: room dimensions, door/window openings
- `scenecode/prompts/data/furniture_agent/stateful_planner_agent.yaml` → Planner orchestrates Designer + Critic + iteration; **"Designer can only control x, y position and yaw rotation"**; `select_placement_style(natural|perfect)`
- `scenecode/prompts/data/asset_router/request_analysis_furniture.yaml` → `AssetItem(description, short_name, dimensions, object_type, strategies)`
- `scenecode/agent_utils/room.py` → `SceneObject` / `PlacementInfo(parent_surface_id, position_2d, rotation_2d)` / `RoomScene.add_object()` / `to_state_dict()`
- `scenecode/experiments/indoor_scene_generation.py` → five stages `floor_plan → furniture → wall_mounted → ceiling_mounted → manipuland`

### Real source of the aDSL geometry (all reused, not rewritten)

- `FineScene/deliveries/reception_lounge_adsl_20261104/build_reception_lounge.py`: 13 ready-made editable aDSL objects (room shell, sofa, coffee table, rug, two single chairs, side table, floor lamp, bookshelf, plant, wall painting, curtain, pendant lamp), each part a named primitive
- `build_blend.py` in the same directory: editable object hierarchy, materials, lights, cutaway/interior cameras, **and the built-in localized-edit verification tests**
- `FineScene/src/adsl_components.py::build_desk`: the TV cabinet is compiled by this parametric factory according to `AssetItem.dimensions` (the LLM gives only numbers)
- `FineScene/src/general_vision.py::review_images(scope='scene')`: whole-scene vision + layout audit hard gate
- The two `Asset` classes (`adsl.core.asset.Asset` and `core.asset.Asset`) are converted via aDSL's own `asset_to_dict → asset_from_dict` round trip, not moved by hand

---

## 2. Architecture correction: why it was changed to "place one by one"

The first implementation had the LLM spit out all coordinates of 14 objects at once, and geometry validation failed repeatedly (9 attempts, 2–7 violations).
The root cause was not a weak model, but that **I violated SceneCode's real mechanism**: its Designer places objects **one by one** through tools and runs a physical feasibility check immediately after each placement.

After changing to sequential placement it passed in one go:

```
request analysis + floor plan  →  inventory (14 AssetItems + relations + intents)
        ↓
Designer places one by one (14 tool-style calls)
        ↓  each time: deterministic validation (room bounds / XY overlap / doorway / sofa-coffee-table alignment / orientation / against wall)
        ↓  on failure, only the specific conflict is fed back to "this one current object", at most 4 times
        ↓
Critic reviews the whole → feedback → re-place → keep the best (validator PASS first, then compare scores)
        ↓
upstream_snapshot.json frozen (read-only)
```

Along the way, two **contract errors found only by reading the real code** were also fixed:

1. After rotation, the AABB of a wall-mounted item pierces the wall surface, so applying the interior bounds check was a false kill → replaced with a dedicated mounting check.
2. FineScene's pendant lamp / curtain parts are **already built locally at z=2.30–2.90 / 1.62–2.44**, so the overall z translation must be 0; my earlier requirement z≥1.9 would have pushed the pendant lamp to 4.5 m → corrected per the manifest.
3. The aDSL sofa faces -Y; the single chair `west` faces +X (yaw 90) and `east` faces -X (yaw −90) → orientation is written into the Designer constraints, to avoid the LLM making the sofa turn its back to the coffee table.

---

## 3. Real run results (Blender 5.2.2 / DeepSeek gateway / aDSL venv)

### Layout (`scene_layout.py`)

| Item | Result |
|---|---|
| Objects / stages | **14 objects**, covering floor_plan / furniture / wall_mounted / ceiling_mounted |
| Geometry validation | **PASS, 0 violations** (room bounds, XY overlap, doorway, sofa-coffee-table centering and spacing, orientation, against wall) |
| Placement | 22 tool-style calls (including immediate feedback repairs), generating all 14 at once |
| Critic | 2 iterations; round 1 scored 6, round 2 scored 3 and lost the TV cabinet → **the system picked round 1 per "keep the best"** |
| Final layout | Sofa (0,1.30) yaw 0 facing the coffee table (0,0.55) and the TV cabinet (0,−1.00); single chairs left-right symmetric; bookshelf against the west wall |

### Assembly and rendering (`assemble_scene.py`)

| Item | Result |
|---|---|
| Pose stamping | **14/14, missing=[]**; the TV cabinet is compiled by the `build_desk` factory at 1.8×0.45×0.55 |
| Editable hierarchy | **14 objects / 137 independent mesh parts** (imported by `build_blend.py`) |
| Localized-edit verification (FineScene built-in) | `isolated_part_edit` ✓ (change one cushion by 0.30 m, other parts move **0**); `grouped_object_edit` ✓ (move the sofa root by 0.40 m, other objects move **0**); `all_passed=True` |
| Rendering | 4 interior/cutaway views (Cutaway / inside the door / southwest overhead / north-side look back), 1100×700 Cycles CPU |
| Artifacts | `living_room.blend` (300 KB), `living_room.adsl.json` (175 KB), `SCENE_ASSEMBLY_REPORT.json` |
| Whole-scene hard gate | Initially **`needs_revision`**, 1 blocking item: the two armchairs did not symmetrically enclose the coffee table |

> The first render captured only a white box (the exterior camera was blocked by the ceiling/walls); this was changed to reuse FineScene's own
> cutaway/interior camera configuration; the issue of the wall painting floating in the cutaway view was also fixed (when the west wall is hidden, that wall's mounted items are hidden too).

### Localized edit (`local_edit.py`) — aDSL is the editing representation, not an export format

Trigger: a blocking finding from FineScene's scene-level QA, naming 4 components.

- The LLM outputs only **numeric** corrections: `armchair_west 90→−90`, `armchair_east −90→90` (no change needed to the sofa/coffee table verdict)
- Only the `xform` of these 2 components is rewritten; **the SHA-256 of the AST JSON of the other 12 components is entirely unchanged**
  (`non_target_preserved=True`, `non_target_violations=[]`)
- Re-render 4 views from the edited AST + `living_room_edited.blend`
- Re-review: **`needs_revision → passed`, `accepted=True`**
- Visual confirmation: the two chairs now face inward symmetrically around the coffee table, and the sofa/coffee table/rug/bookshelf/lamp/plant/window/pendant lamp/wall painting are all unchanged

Corresponding PHASE 5 acceptance metrics: Semantic IDs ✓, Localized Update ✓, Program Persistence ✓,
Relation Preservation ✓, Blender Output ✓, Visual Quality ✓, State Consistency ✓ (the aDSL AST as the single writable SoT).

---

## 4. Real limitations (no whitewashing)

1. **The geometry is FineScene's existing simplified parametric aDSL assets**, not a 9-point PBR-refined piece like `medieval_armchair`.
   The structure is right, parts are editable, and material layering is clear, but it is still block style; the surface-refinement layer of `present.py` has not yet been generalized.
2. **The Critic still gives 6 points, `needs_revision`** (12 comments, mainly about the sofa's relation to the rear-wall window, and the TV cabinet showing only its top from the inside-the-door view).
   The whole-scene QA's blocking item was fixed through a localized edit, but the layout-level comments were not all absorbed.
3. **The TV cabinet is a low cabinet from the `build_desk` factory**, not geometry written specifically for a TV cabinet; this is the result of reuse under FineScene's factory contract.
4. **SceneCode's native pipeline was not run**: this round is "replicate the scene-planning layer per SceneCode's real contract + take over the geometry with aDSL",
   with no `uv sync` / Python 3.11 / Blender 4.4.3 / Drake / pydrake. WSL2 is disabled by local policy on this machine, and by agreement the separate environment is installed after the code is written.
5. **Cost of phase isolation**: after entering the editing phase, SceneCode's `resume_from_path` / checkpoint rollback is no longer used,
   `upstream_snapshot.json` is frozen read-only, and the visual review loop is re-wired on the aDSL side.
6. **The manipuland stage is empty**: this round's 14 objects cover the first four stages, and small tabletop items were not generated.

## 5. Deliverable files

| File | Content |
|---|---|
| `adsl_adapter/scene_layout.py` | SceneCode five stages / one-by-one placement / deterministic validation / Critic iteration |
| `adsl_adapter/assemble_scene.py` | Pose stamping, reuse of FineScene's asset tree and interior rendering, whole-scene QA |
| `adsl_adapter/local_edit.py` | Critic-driven localized AST edit + non-target hash verification + re-render and re-review |
| `runtime/outputs/scene_layout/layout.json` | Final layout + contract sources + iteration history |
| `runtime/outputs/scene_layout/upstream_snapshot.json` | Frozen read-only upstream state |
| `runtime/outputs/living_room/living_room.adsl.json` | Initial aDSL SoT |
| `runtime/outputs/living_room/living_room_edited.adsl.json` | aDSL SoT after the localized edit |
| `runtime/outputs/living_room/interior/*.png` + `living_room.blend` | Initial 4 views and project |
| `runtime/outputs/living_room/interior_edited/*.png` + `living_room_edited.blend` | Edited 4 views and project |
| `runtime/outputs/living_room/LOCAL_EDIT_REPORT.json` | Localized-edit acceptance record |

## 6. Suggested next step

1. Generalize the refinement layer of `present.py` (`realistic_refiner.surface` + bevel + PBR nodes), so that **newly assembled furniture** can also reach the quality of `medieval_armchair`, instead of only being able to reproduce already-refined assets.
2. Feed the Critic's 12 layout comments back into individual objects in a structured way (currently they are only fed back in the intent text, with limited effect), and hook up the `manipuland` stage.
3. Only after that install the separate environment to run the PHASE 2 native reproduction.
