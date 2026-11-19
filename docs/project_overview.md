# SceneCode × aDSL Living Room Scene Generation · Project Overview

> **Positioning**: connect SceneCode's "scene organization + placement" capability with aDSL / FineScene's "single-object editable geometry generation" capability, and produce a deliverable, traceable, self-contained 3D living room scene.
> **Last updated**: 2026-11-09
> **Current status**: ✅ Layout loop passes (0 hard-constraint violations, all 15 objects in place); ✅ the scene has been built in Blender and rendered; 🔶 the automatic repair routing for the visual gate is written, but the last run was cut off by upstream rate limiting before one round of visual re-verification finished.

---

## 1. What the project is

**SceneCode** (wangpuyi/SceneCode, paper arXiv:2605.19587) is an "executable world program" system that can generate editable scenes for indoor environments containing articulated objects. It is itself two layers stacked together:

- **A. Room-level Agentic Backbone**: five stages `floor_plan → furniture → wall_mounted → ceiling_mounted → manipuland`, with a Planner orchestrating a Designer + Critic in iteration: text → layout → per-object placement → render → review → rollback.
- **B. Object-level Code_Object framework**: single object → ObjectPlan → per-part Blender Python program → execute/repair → GLB/SDF/URDF.

**Our goal** (the user's own words): *"What I want is SceneCode's scene organization and placement capability, combined with the single-object generation capability of our tuned aDSL."*

**Division-of-labor contract**:

| | SceneCode side | aDSL / FineScene side |
|---|---|---|
| Responsibility | What is in the room, how many stages, where things go, orientation, mutual relations, circulation paths, review feedback | What each item looks like, editable part hierarchy, materials and geometry |
| Output | `layout.json` | Editable aDSL objects + `.blend` |
| Forbidden | Producing any geometry / bpy code | Deciding spatial relations between objects |

The bridge layer does exactly one thing: it applies SceneCode's `placement{x,y,z,yaw_deg}` to each aDSL object through `apply_pose()`, with **no new geometry written at all**.

---

## 2. Why this round of rework (the core problem)

In the early scenes I delivered, the Critic gave only 3–4 points, and many problems (door opening offset, TV not on the wall, armchair facing the wrong way, wall-adjacency rules not enforced) were fixed by me **by hand-editing coordinates downstream**. The user pointed this out bluntly:

> "Isn't this still wrong? Essentially we still haven't achieved a closed loop, have we? Why did you have to write it? Shouldn't the whole agent do this? I told you before not to do it yourself — you may only change the agent's process. Is SceneCode's own placement capability not good enough?"

These four hard constraints set the direction of the rework:

1. **Do not hand-edit artifacts** (coordinates, poses, geometry).
2. **Only change the agent's process** (prompts, constraints, validation, feedback channels).
3. **It must be a real closed loop**: the agent decides and repairs itself, rather than me patching things downstream.
4. Answer "is SceneCode's placement capability not good enough?": locate where the capability gap actually is.

We located **4 places** where the loop was broken:

1. The Critic judged and stopped, with no re-verification;
2. The constraints hard-coded stale numbers (door opening coordinates, armchair angle inconsistent with downstream);
3. The `repair_target` produced by the scene gate (visual review) had no receiver; the review ended and stopped;
4. The inventory was hard-coded to 14 items, missing `wall_tv` and `entry_door`.

---

## 3. How it became a real closed loop (methodology)

The rework is organized around "let the agent decide; code only does deterministic derivation and validation". Six principles are written into the module docstring of `scene_layout.py`:

### 1. Single source of truth
Room dimensions, inner wall surfaces, and door/window openings are all **measured** from the assembled shell mesh and published as `room_measured.json`. The prompts, constraints, and assembly stages share this one data set instead of each hard-coding its own.
Measured results: interior clear space 6.0 × 5.0 × 2.9 m; door opening x 1.20~2.40 (net 1.28~2.32, height 2.10); window x −1.0~1.0, z 0.88~2.22.

### 2. Constraint as test
`expect()` returns **typed violations** with ids (`walls.*`, `bounds`, `overlap.*`, `clearance.*`, `faces.*`, `in_front_of.*`, `above.*`, `centered.*`, `opening.window`, ...). The same violation list serves as the Designer's feedback, the Critic's evidence, and the loop's postcondition. Constraints no longer hard-code numbers; they are derived from measurements.

### 3. Derive, don't decide
Any component that can be computed from a relation is never left to the model to guess:

- Pendant light `z = ceiling plane − local_z`; wall painting `yaw = inward wall normal`;
- Curtain `z` is solved back from its own local_z so the hem touches the floor; TV `z = cabinet top + 0.10 − local_z[0]`;
- Door leaf = opening + hinge offset.

The model controls only the `x / y / yaw` allowed by the SceneCode contract; everything else is derived by `resolve_pose()`.

### 4. Warm-start incremental repair
Poses that already satisfied the constraints in the previous round are **kept as is**, and only objects that violate constraints are rearranged. Earlier, a full re-sample every round meant "fix one, overturn eleven", getting messier with each repair; with warm start, each round only touches what needs to be touched.

### 5. Dependency ordering
Objects referenced by `in_front_of` / `above` / `faces` / `centered_with` must be placed before the objects that reference them, avoiding dangling cases such as "computing the TV's orientation before the sofa has been placed".

### 6. Gate → repair routing
`scene_gate_repair.py` routes the visual review's `findings` back to the responsible stage by `repair_target`: `layout` findings go back to the Designer (repaired one object at a time, and committed only if all hard constraints still pass after the round); the rest (`decomposition`/`geometry`/`source`/`materials`/`camera`) are reported as work orders to the corresponding stage, and never overstep by repairing on someone else's behalf.

---

## 4. Architecture and modules

```
room_measured.json ─┐
assets_measured.json ┘→ scene_layout (Designer+Critic, warm start, constraint as test)
                            ↓ layout.json  ──→ layout_finalize (derive door leaf / finalize)
                                                 ↓ layout_finalized.json
                                          asset_supply (supply plan)
                                                 ↓ supply_plan.json
                          living_room.blend ──→ build_asset_scene (Blender assembly)
                                                 ↓
                                     _run_scene_gate (visual gate)
                                                 ↓ scene_visual_qa.json
                                       scene_gate_repair (routed repair)
                                                 ↓
                                          pack_delivery (package delivery)
```

| Module | Responsibility |
|---|---|
| `room_spec.py` | Measures the room fact source from the shell mesh, producing `room_measured.json` |
| `measure_assets.py` | Measures each asset's bounding box in **root-node local coordinates** (corrected 4 hand-copied dimension errors), producing `assets_measured.json` |
| `scene_layout.py` | Closed-loop core: Designer places objects one by one + Critic + warm start + constraint as test + dependency ordering |
| `layout_finalize.py` | Derives the door leaf pose from the opening, checks that the door leaf matches the net opening (tolerance 60 mm), and finalizes |
| `asset_supply.py` | Translates the layout into an asset supply plan |
| `build_asset_scene.py` | Blender assembly: takes over poses, records drift, never moves any object on its own |
| `pack_delivery.py` | Removes orphans → merges same-source textures → packs everything embedded, producing a self-contained deliverable |
| `scene_gate_repair.py` | Routes visual gate findings back to the responsible stage by `repair_target` |

---

## 5. Key results (all verified)

- **Loop passes**: `validation.passed = true`, `issues = []`, all 15 objects in place (selected_round 1). The Critic score of 7 / `needs_revision` is an **advisory** aesthetic opinion and by design not a pass condition; the hard constraints are the gate.
- **Texture packing**: direct pack 758.1 MB → orphans removed 346.4 MB → same-source merged 167.5 MB (120 textures); the deliverable has **zero external dependencies**. The triple check (external links 0, linked libraries 0, embedded images `size≠0`) all passed; after merging, a pixel-by-pixel check shows max diff 4/255 and identical mean brightness (133.62).
- **Traceable**: the scene's 7 geometry programs were written by `deepseek-flash` through the local compatible gateway (49 gateway responses); scene organization and layout are done by deterministic code; visual acceptance is done by `deepseek-flash` through FineScene's `general_vision` reviewer. The code is derived from model responses, and it only counts as passed when all five hashes match.
- **Scene landed**: Blender assembly has 13 imported objects + 2 procedural (rug/curtain), 0 errors, 5 renders produced.

---

## 6. How to reproduce

Prerequisites: the local compatible gateway `bridge/gateway.py --port 8765` (maps SceneCode model names to `deepseek-flash` and guarantees that `images.generate` never fails); Blender 5.2.

```bash
# 1. Measure the room fact source + asset bounding boxes (produces room_measured.json / assets_measured.json)
python -m adsl_adapter.room_spec
python -m adsl_adapter.measure_assets

# 2. Closed-loop layout (Designer+Critic+warm start, produces layout.json)
python -m adsl_adapter.scene_layout

# 3. Finalize (derive door leaf, produces layout_finalized.json)
python -m adsl_adapter.layout_finalize

# 4. Supply plan
python -m adsl_adapter.asset_supply

# 5. Blender assembly (take over poses, no unilateral moves)
/e/Blender/blender.exe -b --python adsl_adapter/build_asset_scene.py

# 6. Visual gate
python -m adsl_adapter._run_scene_gate

# 7. Route review findings back to the responsible stage for repair
python -m adsl_adapter.scene_gate_repair

# 8. Package delivery (self-contained .blend)
python -m adsl_adapter.pack_delivery
```

> Note: `scene_layout` requires the gateway to be online; on upstream rate limiting (HTTP 429) the module records it and keeps the rounds that already succeeded, rather than discarding the whole result.

---

## 7. Known limitations and next steps

- **Automatic repair for the visual gate** is written (`scene_gate_repair.py`), but the last run could not finish a full round of visual re-verification because of upstream rate limiting (429); it needs to be re-run after the limit lifts, confirming that the `repair_target` routing closes the loop.
- **Critic score is low (7/10)**: all hard constraints pass, but there is still room to polish the aesthetics (proportions, whitespace, sight lines); this is agent tuning, not a broken loop.
- **The working .blend carries orphan data blocks**: this is a known limitation; the delivered version from `pack_delivery` has them removed; rendering and export are unaffected.
- **The shell door opening position** has now been handed to the agent's `floor_plan` stage (the user chose "fully closed loop"); the original hand-written eastward-shifted coordinates are no longer used.

---

## 8. Document index (deep reading)

| Document | Contents |
|---|---|
| `docs/living_room_scene.md` | Living room Blender project details (dimensions, collections, materials, textures, render parameters) |
| `SCENECODE_AUDIT.md` | Static audit of the repository (architecture, inheritance, authenticity notes) |
| `SCENECODE_ADSL_ARCHITECTURE.md` | Finalized architecture (direction and constraints for adapting aDSL to SceneCode) |
| `SCENECODE_LAYOUT_ADSL_REPORT.md` | Report on landing scene organization × single-object generation together |
| `ROOT_CAUSE_ANALYSIS.md` | Root-cause analysis of why early "agent generation" quality was poor |
| `SCENECODE_ADSL_MAPPING.md` | Field / contract mapping table |
