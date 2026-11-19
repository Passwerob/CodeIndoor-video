# CodeIndoor-Video: Video -> Editable, Openable Blender Scene

This is the video-input version of CodeIndoor. Given an indoor video shot on a phone, it outputs:

- **Scene representation** (all JSON, traceable to video frames and the model's raw answers): the object inventory, each object's **function + structure spec**, **lights** and **materials** (colors measured from pixels), and the room layout;
- **Blender files**: one `.blend` per object (parts carry function annotations, doors/drawers/lids carry hinges, and you can drag `open` to open them directly), plus the assembled whole-room `scene.blend`;
- A single-page **report.html** that puts all of the above and the evidence images together for manual review.

## Why a "functional representation"

Given only an image, the model knows "this is a water dispenser" and reproduces the appearance: a white box with a black top. It does not know how many touch keys the control head has, which one dispenses hot water, where the spout is, whether the drip tray can be pulled out, or whether the lower panel is a door.
So for each object we first read a **spec** off the video crop (a detailed image2text representation), and then use it to supervise code generation:

| Spec content | Example (the water dispenser in the video) |
|---|---|
| Parts: kind, count, face they sit on, position on the face (u,v), size ratio, material, function, interaction, printed text/icons, confidence | 4 touch keys `touch_key_left`...`touch_key_right` (cup / water drop / thermometer / lock icons), `display_screen` self-illuminated blue, `drip_tray`, `dispensing_niche` (recess) |
| **Structure (joints)**: revolute / prismatic / continuous / press, movable part, hinge edge, opening direction, range | The lower panel is hinged along the bottom edge and flips forward 90°; the drip tray pulls out forward |
| Usage, and what is not visible / uncertain | "Put the cup on the drip tray -> touch a key to pick cold or hot -> water comes out of the spout" / "Whether the lower panel opens: hinge not visible" |

The spec supervises generation in three ways:

1. **Basis for writing code**: parts are named by spec id, with counts, positions and material keys listed one by one (`functional_spec.spec_block`);
2. **Deterministic tests before rendering**: the program first runs in a pure-Python "dry-run" Builder (`dryrun_builder.py`); the resulting part table is checked by `spec_check.py` for missing parts, wrong counts, emissive parts that do not emit, parts outside the envelope (naming the protruding parts), **functional parts buried inside the body (buried)**, **recesses filled by solids (filled)**, and joint rotations that would hit the body (sweep test). Hard violations are sent back for a rewrite without launching Blender;
3. **Exact re-check inside Blender + visual gate checklist**: forge_runner checks again with the real mesh and converts the spec into a gate checklist (counts, emission, whether openable parts are independently visible).

## Lights and materials (key part)

Principle: **the VLM only makes judgments; anything that can be measured is measured**.

- **Lights**: dedicated readings from full-resolution ceiling/window frames: day/night (look outside the window, not at room brightness), fixture type/count/drop length/shade size/beam/color temperature/whether lit; two non-overlapping groups of frames are each judged once, the larger count is taken and the disagreement is recorded (one reading missed a third lamp hidden behind a pipe). In Blender each light uses a **physical color temperature** (`light.temperature`), plus a shade, a hanging cord, a ceiling canopy and an emissive diffuser.
- **Camera**: the phone's auto white balance is modeled separately: the residual color cast is solved from the brightest white wall patch to get the camera white point (for this video: lamps 4000K, camera ≈4300K), and Blender reproduces it with `view_settings.white_balance_temperature`.
- **Materials**: the VLM gives category, gloss and pattern (tile grout lines / marble veins / terrazzo speckle / wood grain / brushed), and points out a clean sampling box; the color is **measured from pixels**:
  - The phone auto-exposes every frame, so colors are compared only against the **white wall in the same frame** (brightness is anchored to the same-frame white wall, chroma uses the camera residual across the whole video);
  - If the white wall patch is in shadow (clearly darker than the brightest neutral in this frame) -> use this frame's white point level instead;
  - If the measured color contradicts what the model saw (brightness differs by > 2.5x or hue mismatch) -> the sampling box is judged wrong and discarded;
  - Near-neutral surfaces are stored as neutral (the warm reflection of the wooden cabinet by the wall is produced by the renderer itself and must not be baked into the material); metals take a category prior (what is in the pixels is reflection); for grout lines the more extreme color is taken (in a 544 px video a 2-pixel line is entirely mixed pixels).
- **Render-compare loop** (`--calibrate`): render from a viewpoint close to the first frame + render a surface ID map, and calibrate in order:
  1. **Light distribution**: the render's "floor/wall" brightness ratio is far larger than in the video (the "medium beam" spotlight judged by the model makes the floor 6–8 times brighter than the wall) -> widen the beam step by step, switching to a point light if necessary;
  2. **Albedo**: under the same viewpoint, each surface's linear brightness ratio to the white wall is aligned to the video (amplitude limited to ±30% per round);
  3. **Exposure**: aligned on the white wall (the whole-image median would be skewed by the floor).

The material grammar has been extended backward-compatibly with modifiers: `tile:#F4F4F2:0.12:grout=#1E1E1E:size=0.10`, `stone:#DFDFDF:0.08:veins=#9E9E9E`, `stone:#D7D7D7:0.38:speckle=#8A8A86`, `metal:#B4B6B8:0.38:brushed`, `screen:#101020:0.14:emit=#3A6BFF`, with new categories `tile / rubber / screen / light`. All are native Blender procedural nodes, with no texture files.

## How to use structure (joints) in Blender

Joint positions are **not given as coordinates by the model**; they are derived from "which edge the hinge is on, which way it opens" + the bounding box of the part the program actually built (the sign guarantees the free edge swings toward the opening direction, and the sweep test guarantees it does not pass through the body). Each joint generates:

```
ROOT_<object>
  JOINT_<joint>      <- empty object at the derived pivot point; custom property open ∈ [0,1]
    door_left, door_left_handle ...   <- moving parts (re-parented keeping world position)
```

Select `JOINT_*` and drag `open` in the N panel under "Item -> Custom Properties" to open/close (driven by a simple-expression driver; "Auto Run Python Scripts" does not need to be enabled). A part with several degrees of freedom (a faucet handle lifting + swinging left/right) is automatically chained into a joint chain. The joint description (type/axis/range/origin/parts) is stored as JSON on `JOINT_*["ci_joint"]` and `ROOT_*["ci_joints"]`, consistent with the SceneCode URDF convention (+X right, −Y front, +Z up, right-hand rule), and can be exported directly to URDF. Parts additionally carry `ci_part / ci_kind / ci_function / ci_label / ci_instance`.

## Running

Dependencies: a unified venv, run `python env/setup_env.py` (installs `requirements.txt`, which includes `requirements-video.txt` and aDSL); Blender 5.2. forge needs FineScene (already in the repo) and `FineScene/deliveries/baroque_reception_pbr_20261104/textures/` (run `python fetch_textures.py` in that directory to download).
Keys: fill in `DEEPSEEK_API_KEY` in `runtime/.env` (see `.env.example`). Start the gateway first with `python bridge/gateway.py --port 8765` (if it is not running, DeepSeek is called directly and a notice is shown).

```bash
# 1) Analysis: keyframes -> inventory -> grounding crops -> lights/materials -> functional spec per object -> layout -> scene_plan.json -> report.html
python adsl_adapter/video_pipeline.py --video D:/videos/pantry.mp4 --run pantry

# 2) Generate object programs from the spec (forge); you can do just a few
python adsl_adapter/video_pipeline.py --run pantry --forge --only water_dispenser --only fridge

# 3) Assemble the whole room (objects not yet forged use placeholder boxes with measured materials), and calibrate lighting/materials/exposure
python adsl_adapter/video_pipeline.py --run pantry --build-scene --calibrate
```

Every stage is cached in `runtime/video/<run>/`; `--refresh <stage>` reruns it (frames / survey / ground / appearance / spec / layout / plan); `--all-objects` also produces specs for low-priority small items. All VLM answers are stored verbatim in `vlm_calls/` (including request/response hashes).

Environment variables (can be written into `runtime/.env`; see `.env.example` for the full list): `CODEINDOOR_GATEWAY` (gateway address), `VIDEO_VISION_MODEL` (default `deepseek-flash`), `BLENDER_EXE`, `ADSL_ROOT`, `FINESCENE_ROOT`, `ASSETS_ROOT` (root of the asset pack; the study assets are under `external/realistic_study_20261019` in it, or you can point to them directly with `STUDY_ASSETS_DIR`), and `CODEINDOOR_ROOT` (only affects the location of the `runtime/` output and `.env`).

Tests: `python -m pytest tests/test_video_pipeline.py` (offline, no network, no Blender); `blender -b --factory-startup --python tests/blender_smoke_video.py` (verifies material/joint/room building in your Blender).

## Running on a Linux GPU machine (verified on 2×H20)

```bash
# aDSL and FineScene are both in the repo, no extra placement needed
python3.11 env/setup_env.py                       # create .venv and install requirements.txt
(cd FineScene/deliveries/baroque_reception_pbr_20261104 && ../../../.venv/bin/python fetch_textures.py)

# Write into runtime/.env or export directly:
export DEEPSEEK_API_KEY=...
export BLENDER_EXE=/path/to/blender-5.2.4-linux-x64/blender
export CYCLES_DEVICE=CUDA                         # OptiX kernel compilation fails on H20; CUDA works
python bridge/gateway.py --port 8765 &            # or set CODEINDOOR_GATEWAY to point to another port

.venv/bin/python adsl_adapter/video_pipeline.py --video pantry.mp4 --run pantry --all-objects
.venv/bin/python adsl_adapter/video_pipeline.py --run pantry --all-objects --forge --attempts 5
.venv/bin/python adsl_adapter/video_pipeline.py --run pantry --build-scene --calibrate --open-renders --samples 128 --percentage 150
```

`--forge` skips objects that have already passed (`--reforge` forces a redo); `--open-renders` additionally opens all doors/drawers/lids and renders once more.

## Output files

| File | Content |
|---|---|
| `frames/F*.jpg`, `frames.json`, `sheets/` | Keyframes (the sharpest per segment, deduplicated) and contact sheets |
| `survey.json` | Object inventory (including dedup/merge records), lamps, surfaces, openings |
| `grounding.json`, `objects/<id>/view_*.jpg` | Per-frame grounding boxes, 3 evidence crops per object from different moments, same-frame white wall anchors |
| `appearance.json` | Lighting (two readings and their disagreement), camera white balance/exposure anchors, materials (what the model saw vs. measured from pixels vs. final material key, and the reasoning for the decision) |
| `objects/<id>/spec.json`, `audit_*.jpg` | Functional + structure spec, the enlarged images used for control review |
| `layout.json` | Room size, openings, each object's pose, lamp positions, issue list |
| `scene_plan.json` -> `scene/scene.blend`, `scene/render_*.png`, `scene_build.json` | Blender assembly and calibration records |
| `report.html` | All of the above, viewable on one page |

## Is DeepSeek enough (measured)

- `deepseek-flash` can see images: multiple images at once, localization boxes in 0..1000 (accurate boxes, directly croppable), and it reads icons and Chinese printed text. The whole analysis takes about 60–80 calls.
- **`deepseek-v4-pro` does not see images, and does not raise an error**: the same contact sheet was billed only 427 prompt tokens, and it answered about a nonexistent "客厅" (living room). So vision calls are fixed to flash, verified at startup with red/blue test cards, and an error is raised directly if the answer's token count is too small to contain the image.
- The API does not accept video: frames are decoded locally.
- Weaknesses and countermeasures: when looking at the whole at once it judges a night scene as day and a white pendant lamp as black -> split into dedicated full-resolution readings; counting small buttons is unstable -> go back to the original video, sample frames densely around the moments when the object appears large, pick the most readable frame and zoom in to count one by one; output occasionally does not follow the schema (stuffs all fields into `object`) -> automatically expanded and re-asked; the same prompt may yield different readings twice -> lighting uses two groups of frames merged for self-consistency.

## Measured geometry: measure placement and size from the video (AHa-3D approach)

The VLM can only estimate "roughly which wall, how far from the corner", so placement and size do not match the video. The scripts under `geometry/` replace this step with measurement:

| Step | Script (conda env `vggrpo`) | What it does |
|---|---|---|
| 1 | `any4d_reconstruct.py` (cwd = Any4D repo) | One inference over 53 keyframes: metric depth, camera poses and intrinsics, point cloud |
| 2 | `sam3_masks.py` | SAM3.1 text prompts (`geometry/prompts.json`) + VLM grounding box prompts, per-frame masks |
| 3 | `measure_scene.py` | Floor RANSAC fixes gravity, wall normals fix the Manhattan axes; wall rules: no floor outside a wall, all cameras inside the room (walls not captured are completed from the floor/camera extent); countertop height 0.86 m fixes the scale; masks are lifted to 3D to fit each object's bounding box (front-face normal voting decides orientation; cabinets with a flat front have their back against the wall); window and pendant lamp positions |
| 4 | `refine_boxes.py` | Project the bounding boxes back to each view, run coordinate descent on IoU against SAM3 masks, and take the midpoint with the point-cloud box (removes mask spill-over) |
| 5 | `measure_surfaces.py` | Spatial period of tile grout lines (the fundamental needs a p/2 harmonic) -> tile size; grout color taken from the darkest 10%; floor speckle diameter and coverage |
| 6 | `apply_measured.py` (any python) | Writes `measured/scene_plan.json`: measured room, poses, sizes, lamps, materials; 53 video cameras |
| 7 | `video_scene_builder.py ... --calibrate --no-render` | Generates `measured/scene/scene.blend`. Forged parts are scaled to the measured size; embedded parts (the sink) are boolean-cut into their supporting body; each video camera carries that frame's original image as background (packed) |
| 8 | `compare_views.py` | Render ID masks from the video cameras (1 sample, for checking only) and compare against SAM3 masks, per-object IoU |
| 9 | `adsl_adapter/video_review.py --select / --review` | Under the same camera, the "real crop / model crop" pair is annotated separately and sent to deepseek-flash, which lists the differences and modification instructions |
| 10 | `video_pipeline.py --forge --from-review --only <id>` | Starting from the original program, re-forge with the measured size + review comments (comments concerning other independent objects or room surfaces are filtered out) |

To check in Blender: in the outliner pick any `CAM_src_Fxx` under `VIDEO_CAMERAS`, press Ctrl+Numpad0 to make it the active camera, and Numpad0 to look through it; the model is overlaid on that frame's original video.

## Hand modeling (v5, Claude writes programs directly against the video, without LLM forging)

`hand_objects/<id>/program.py` is an ordinary forge program (`build(B, mats, P)`), hand-written by Claude one object at a time against close-ups in the video; `textures.py` (optional) generates that object's textures. Tools and workflow:

| File | Purpose |
|---|---|
| `adsl_adapter/hand_lib.py` | Rounded lofted bodies, profile extrusion, tubes, lathe, decals (image materials, packed into the .blend), text to mesh, boolean cutting |
| `hand_objects/texlib.py` | Perspective-corrected cutouts from video frames by four corner points (labels, stickers, panels), or hand-drawn clear patterns from close-ups (button icons, small text) |
| `hand_objects/build.py <id> ...` | Build with forge_runner (spec check + joint binding as before), outputs to `runtime/outputs/hand/<id>/` |
| `hand_objects/preview.py` | Swap the hand-written object into the assembled scene and render from the video camera for comparison (for checking, not delivered) |
| `geometry/refine_cameras.py` | Any4D per-frame depth drift (the same wall differs by up to 10 cm across frames): for each frame, estimate scale + translation from the floor/countertop/wall and correct that frame's camera position |
| `geometry/refine_poses.py` | Fine-tune objects' planar positions with the corrected cameras against SAM3 silhouettes (adopted only if IoU improves by > 0.02) |
| `geometry/tile_phase.py` | Tried to measure the tile grout phase; per-frame results were inconsistent (camera accuracy insufficient), not adopted |
| `measured/pose_overrides.json` | Manually verified pose/size corrections, each stating its evidence (point cloud slice, video timestamp) |
| `measured/extra_objects.json` | Objects missed by the automatic stage (side panels of the tall cabinets on both sides of the fridge) |
| `adsl_adapter/blender_articulation.py` | Joints are 1-DOF: a hinge/turntable can only rotate about its own axis (other rotation and translation channels are locked), a slider/press can only move along its own axis; each joint carries an `AXIS_<id>` empty drawing the axis line (along the door's hinge edge, along the drawer travel); the `ci_joint` record holds kind / dof / origin / axis and can be exported to URDF directly |
| `geometry/clip_check.py` | Checks in Blender: object origin at the bottom center; each joint's axis position/direction and opening direction; static interpenetration (ray parity test for points inside a closed mesh, depth > 4 mm); interpenetration of the moving part with everything else when each joint is opened to 25/50/75/100 %. Writes `measured/clip_check.json` |
| `measured/room_features.json` | Architecture that wall fitting cannot express: the northwest corner column (with a socket), the protruding section of the northeast wall (tiled), the black wall band above 2.56 m, the aluminum step nosing on the floor, the splash-proof socket by the faucet, the countertop upstand, the terrazzo floor |

`measured/blend_overrides.json` points objects in the scene to `runtime/outputs/hand/` (the sink uses a forge program verified part by part, rebuilt to the measured opening size).

The version-controlled copy of the above `measured/*.json` manual correction records is in `cases/pantry/` (`runtime/` is ignored); for the granularity of changes and acceptance rules see `.claude/skills/scene-correction/SKILL.md`.

## Known limitations / next steps

- **Layout is a monocular estimate**: room size is inferred from objects of known size, and two runs can differ by 0.5–1 m; wall orientation is defined by the first frame and occasionally contradicts (e.g. which wall the window is on). A next step is to plug in MASt3R/VGGT on a GPU machine for metric reconstruction, replacing the numerical source of the layout stage.
- **Small controls are limited by video resolution**: in the 544×960 video the control panel was never captured clearly head-on, and the water dispenser's button icons were read inconsistently across two runs; the spec honestly marks them "partially legible, to be verified".
- **Light emitted from the top of the pendant lamp**: calibration widens the spotlight to a point light, so the ceiling is too bright (real fixtures do not emit light from the top); this can be changed to geometric occlusion of "point light inside the shade + opening facing down".
- This machine has no FineScene: on the Blender side, materials, joints, spec re-check, scene assembly and calibration were verified in Blender 5.2.4 with a **stub test Builder**; FineScene's visual gates and the real Builder (bevels, decorative parts) need to be run once on Windows to confirm.
- The TRELLIS appearance stage is still an interface (`--use_trellis`), not wired in.
