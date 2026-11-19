# Root cause and fix for single-asset quality

Diagnosis and fix record for "the individual assets are too poor, FineScene is much better than this".

## 1. Root cause: not a material bug, the wrong geometry source was used

Three layers, each verified:

**Layer 1 — Borrowed from the wrong source.**
`assemble_scene.py` took its geometry from
`FineScene/deliveries/reception_lounge_adsl_20261104/build_reception_lounge.py`.
That script exists to **verify aDSL AST editability**: each part is one named
primitive, so that `attach_part` and local-edit tests have something to bite on.
Its forms were deliberately reduced to the simplest, and quality was never the goal.
Assembling the living room from it treats aDSL's editing ability as if it were modeling ability.

**Layer 2 — aDSL's real single-asset generation ability was not used.**
`aDSL/outputs/medieval_armchair_20261017` (The Oakwarden) is 200 primitives → 156 meshes
+ 10 curves, with turned wooden legs, diamond-tufted upholstery buttons, copper rivets, and procedural wood grain and leather PBR. This pipeline was
never called once in the living-room assembly.

**Layer 3 — Materials degraded to a single color.**
`build_blend.py::material_for()` gives only flat colors. FineScene, however, has
`realistic_refiner.py::surface()`
(the full set of diff/rough/arm/normal 2k PBR + BOX projection) and 19 CC0 scanned assets in `assets/external/`, none of which were used.

## 2. Single-asset supply after the fix (three tiers, in descending fidelity)

| Tier | Source | Amount in this scene |
|---|---|---|
| retrieved | Poly Haven CC0 scanned assets + native 2k PBR | 7 items (coffee table, TV cabinet, armchair ×2, side table, potted plant, wall painting) |
| generated | Agent-authored program → FineScene Builder DSL → dual gate | 4 items (sofa, floor lamp, pendant light, bookshelf) |
| procedural | Profile + procedural PBR (soft furnishings with no real volume) | 2 items (rug, curtain) |

`asset_supply.py` is the resolver; every mapping carries a reason, so it is auditable.
`furniture_forge.py` + `forge_runner.py` are the generation pipeline.

## 3. Key finding: FineScene's furniture is not made by four factories

`adsl_components.FACTORIES` has only bookshelf / shelf / desk / chair,
and each compiles to a few boxes — this is the direct source of the "building-block look".

FineScene's own good furniture comes from **`baroque_lib.Builder` + ornament generators**:
`volute`, `cabriole_leg`, `acanthus_leaf`,
`torus`, `fluted_column`, `corinthian_capital`, `dentil_row`, layered on real 2k PBR.

So `forge_runner.py` hands this DSL directly to the Agent, and the Agent writes the part table itself:

```
def build(B, mats, P):
    B.box("plinth", (w, d, 0.09), (0, 0, 0.15), "oak_joinery", bevel=0.010)
    cabriole_leg(B, "leg_w_front", (-0.9, -0.35, 0), 0.14, "oak_joinery")
    ...
```

`forge_runner.py` contains no furniture geometry.

## 4. Gates: two, plus one added deterministic check

**Component level (FineScene's stock `general_vision.review_images`, scope='component')**
4/4 passed: sofa 2 rounds, floor lamp 2 rounds, pendant light 1 round, bookshelf 1 round.

**Deterministic check (new, because the visual gate let one serious error through)**
An all-white, zero-material sofa was judged `passed` by the visual gate. Cause: the Agent passed
`mats["oak_joinery"]` (a material object) directly to `B.box`, while `Builder._register` only accepts
string keys and silently skipped it → all meshes had no material and rendered paper-white.

Now `forge_runner` reports `unshaded_count`, and `furniture_forge` rejects **before** the visual gate:
parts with no material, exceeding the envelope by >10%, or fewer than 12 parts.

**Scene level**: `needs_revision` (not passed). Specific items:
- The orientation relationship between the TV cabinet and the sofa is unclear (spatial misreading caused by the cutaway view)
- The wall painting is invisible from most views (on the west wall, blocked by the bookshelf)
- The relationship between the potted plant and the wall is questionable from some views

These are at the placement/framing layer, not the asset-quality layer. Recorded honestly, not marked as passed.

## 5. The other side of the gate: the clearance solver

`validate_layout` only rejects XY **overlap** (3 cm tolerance). As a result
the rear edge of `coffee_table` sits exactly on the front edge of the sofa:
it passes the overlap test but is wrong as a room
— a person cannot stand up.

`clearance_solver.py` adds functional clearances (38 cm in front of seating, 6 cm from chair to side table, 12 cm from floor lamp to bookshelf), using local search rather than axis-by-axis pushing (axis-by-axis pushing oscillates back and forth on pinned objects).
It is a solver, not a number tweak: the rules are semantic and apply to any layout.

Solver result: 3 adjustments, all 7 rules pass.

## 6. Measured artifacts

| Artifact | Path |
|---|---|
| Scene blend (editable hierarchy + camera + lights) | `runtime/outputs/living_room/assets/living_room_assets.blend` |
| Final 4 views | `runtime/outputs/living_room/assets/v2_view_00{0..3}.png` |
| Agent program (readable, rerunnable) | `runtime/outputs/forge/<id>/program.py` |
| Single-asset blend | `runtime/outputs/forge/<id>/<id>.blend` |
| Assembly report | `runtime/outputs/living_room/assets/ASSET_SCENE_REPORT.json` |
| Supply plan | `runtime/outputs/scene_layout/supply_plan.json` |
| Clearance report | `runtime/outputs/scene_layout/clearance_report.json` |

Assembly: 11 items (7 retrieved + 4 forged) + 2 procedural, 0 errors.

## 7. Not yet resolved

- The `ceiling_light` anchor at z=2.90 is set by a constant, not measured from the shell ceiling height.
- The sofa back is on the thin side, and the seat cushion thickness was pulled up by the visual gate, not guaranteed by a geometric rule.
- `tv_cabinet` reuses a flattened `WoodenTable_01`, which barely reads as a low cabinet; the asset library lacks a real media cabinet.
- Wall painting visibility is not factored into framing decisions.
- The scene-level gate is still not passed.
