# CodeIndoor-Video

CodeIndoor-Video turns **indoor scenes** into Blender / aDSL scenes that you can **edit**, **articulate** and **verify**. Every object is a parametric program, not a frozen mesh. You can change its size, swap its material, or open its doors, and every change has to pass a geometry check and a visual Judge before it is accepted.

The repository contains three pipelines:

| Pipeline | Input | Output | Entry point |
|---|---|---|---|
| **Layout loop** | Measured room and requirements | A full room in which the Agent chooses furniture poses and repairs them itself | `adsl_adapter/layout_finalize.py`, `build_asset_scene.py` |
| **Video → scene** | One handheld phone video of a room | An articulated scene with measured sizes and poses, functional specs and joints, with lighting and materials calibrated against the video | `adsl_adapter/video_pipeline.py` (see [`README_VIDEO.md`](README_VIDEO.md)) |
| **FineScene** | A text brief, optionally with reference images | Parametric objects and scenes planned by a staged LLM planner, checked for assembly and reviewed by a visual Judge | `FineScene/src/run_general_scene.py` (see [`FineScene/README.md`](FineScene/README.md)) |

> All documentation is in English. `sources/` is an unmodified upstream snapshot; Chinese strings that remain in code are functional (keywords for recognising Chinese briefs, test inputs, or text printed on real objects).

---

## 1. Division of labour: the harness model and three reconstruction helpers

| Component | Kind | Does | Does not do |
|---|---|---|---|
| **FineScene** | Built in | **Builds single objects.** It decomposes an object into parts, writes a parametric recipe (box, cylinder, sphere, swept curves, lathe), checks assembly and contacts, and sends the result to the visual Judge. The video pipeline's *forge* uses the same Builder. | Decide which functional parts an object has; decide sizes or positions |
| **VLM** (DeepSeek-compatible API) | Built in | **Describes objects and their functions in detail.** It lists the objects; for each object it records the parts, their counts and positions, printed text and icons, how the object is used, the joint structure (what moves, where the axis is, which way it opens) and the material class. It also compares real and rendered frames from the same camera and lists the differences. | Produce numbers (sizes, coordinates, colour values) |
| **AHa-3D-style measured geometry** | External. The repo only contains scripts in the same style (`geometry/`); they depend on external Any4D and SAM3 installs. | Measures depth and cameras and builds point clouds; from these it derives each object's size, position and orientation, with per-frame refinement. | Semantics or shape |
| **Harness model** (e.g. Claude Code) | — | Orchestrates the three components above and cross-checks their outputs. When acceptance finds gaps, it instructs the downstream models to fill them. It also fixes shared code and hand-writes repairs after a failure escalation. | Invent results on behalf of any component |

Principle: **FineScene owns shape, the VLM owns semantics, measurement owns numbers.** None of them substitutes for another.

---

## 2. Capabilities

### 2.1 Video → articulated scene
- **Functional and structural spec.** The VLM writes a spec for each object: parts, counts, the face each part is on, size ratios, printed text and icons, and joints. The spec is both the brief for modelling and the test used for acceptance.
- **Measured geometry.** The pipeline measures depth and cameras, then fits walls and floor, then derives each object's box and pose. It refines the cameras frame by frame and adjusts positions from object silhouettes.
- **Lighting and materials.**
  - The VLM only makes judgements. Colours are measured from pixels, using a white wall in the same frame as the reference.
  - Lamps use physical colour temperature. The phone's auto white balance is modelled separately.
  - The renderer then compares same-camera renders with the video and calibrates light distribution, albedo and exposure.
- **Joints.** Every joint has one degree of freedom: hinge, swivel, slider or press. Each joint has a visible axis and can be exported to URDF. Clipping is checked with each joint 25 %, 50 %, 75 % and 100 % open.
- **Manual corrections.** These are versioned in `cases/<scene>/` with evidence for each entry. `cases/pantry/` is a worked example.

### 2.2 FineScene parametric modelling
- **Recipe primitives:** box, cylinder (including frustums via `radius_end`), sphere, book, **sweep** and **lathe**.
- **14 curve presets for sweep:**
  - arc, ring, rounded_rect, helix, spiral, c_scroll, s_curve, wave, bezier, smooth;
  - cabriole, gooseneck, u_bend, catenary.

  Each curve is fitted exactly to its declared envelope. Sweeps can taper and can be closed.
- **12 lathe profiles:** baluster, vase, urn, bun_foot, knob, bell_shade, drum_shade, turned_leg, column_base, finial, pedestal, tapered_foot.
- **Contact checks.**
  - Distances are measured to the real surfaces. Tapered segments are split into conservative capsules, and lathe parts are checked with an SDF plus surface sampling.
  - Sweeps and lathes cannot act as supporting surfaces.
  - Unsupported combinations fail explicitly.
- **Whole-object consistency.**
  - A repeated part is declared once as a copy with `like`, optionally with `mirror` (`x` / `y` / `xy`). The compiler mirrors both the geometry and the orientation.
  - A symmetric object declares `assembly.symmetry`. The compiler audits it and reports `asymmetric_part` when the parts do not match.
- **Preview tool:** `FineScene/experiments/preview_curve_presets.py`.

### 2.3 Textures and materials
- **No texture files by default.** A material is a parametric key, for example:
  - `tile:#F4F4F2:0.12:grout=#1E1E1E:size=0.15`
  - `stone:#ECECE8:0.15:veins=#9A9A9A`
  - `metal:#B9BBBD:0.32:brushed`
  - `screen:#0E0E12:0.06:emit=#3355FF`

  There are 15 classes. Patterns are built from native Blender procedural nodes at real-world scale, so no UVs are needed.
- **Images are used for only two kinds of surface.**
  - Large surfaces where the photographic look is the point use CC0 scanned PBR textures. They are downloaded from the source listed in each `provenance.json` and are not committed.
  - Flat printed graphics use decals. Legible graphics are cut out of a video frame with a perspective rectification (`hand_objects/texlib.py`). Illegible ones are redrawn from close-ups, without inventing text. Decals are applied with `adsl_adapter/hand_lib.py`.
- **Limits.**
  - Full surface textures are not reconstructed from video.
  - No learned materials are generated.
  - Downstream Agents do not extract decals on their own; the harness drives that during acceptance.

### 2.4 Failure escalation (hand repair by the harness)
- When the same object or the same stage has failed **5 times in total**, the harness model takes over and repairs it by hand.
  - Build errors, failed checks and blocking rejections by the visual Judge all count as failures.
  - The count carries over when the planner replans.
- The threshold is defined in one place, `adsl_adapter/harness_handoff.py: HANDOFF_ATTEMPTS = 5`. The forge and FineScene retry limits both use it.
- An escalation writes `harness_handoff.json`. It names the target, the evidence, the last findings and the repair route.
- Hand-written repairs go through the same checks and the same visual Judge, with **no relaxed thresholds**. In FineScene they are replayed with `--planner replay --plan <file> --harness-repair <notes.json>`, and their provenance is recorded in `run_origin.json`.

---

## 3. Repository layout

| Path | Contents |
|---|---|
| `adsl_adapter/` | Core code: layout planning, derivation and gates; Blender builds; the video pipeline; forge; joints; the material grammar (`material_spec.py`); the escalation handoff (`harness_handoff.py`); paths and secrets (`codeindoor_env.py`) |
| `aDSL/` | The aDSL modelling kernel (`adsl-core`: primitives, bounds, joints, glb/urdf export) and its agents, installed in editable mode |
| `FineScene/` | Fine-grained modelling: the staged planner, recipe compiler, curve presets (`src/curve_presets.py`), copies and symmetry (`src/part_consistency.py`), and geometry and visual review. Development rules are in `FineScene/AGENTS.md`. |
| `geometry/` | Video geometry measurement: Any4D, SAM3, camera and pose refinement, clipping check, sim2real audit |
| `hand_objects/` | Objects hand-written by the harness: `<id>/program.py`, with an optional `textures.py` |
| `cases/<scene>/` | Manual correction records and acceptance baselines |
| `bridge/` | Local LLM-compatible gateway and probes |
| `sources/` | Read-only snapshot of upstream SceneCode, run in its own environment |
| `tests/` | Root regression tests |
| `docs/` | Project overview and scene notes |
| `.claude/skills/` | Working guidelines for the harness model: `codeindoor` (overall), `scene-correction` (scene fixes), `finescene-curves` (curve modelling) |

The following are not version-controlled: `runtime/` (generated outputs and secrets), `.venv/`, `assets/`, `research/`, `.blend` files, images and videos.

---

## 4. Environment

**One venv.** The repository uses a single `.venv` at the repo root, with dependencies listed in `requirements.txt`. `aDSL/adsl-core` and `aDSL/` are installed in editable mode.

```bash
# Python 3.10+ (3.11–3.13 recommended)
python env/setup_env.py             # create .venv, install deps, generate runtime/.env, self-check
python env/setup_env.py --check     # self-check only: path resolution, Blender, adsl import
```

**One config file.** Settings live in `runtime/.env`, copied from `.env.example`. Lookup order: **shell environment → `runtime/.env` → in-repo defaults**. No machine-specific paths are hard-coded.

| Variable | Purpose |
|---|---|
| `DEEPSEEK_API_KEY` | LLM planning, VLM readings, visual Judge |
| `BLENDER_EXE` | Blender 5.x (looked up on `PATH`, then in standard install locations) |
| `ASSETS_ROOT` / `RESEARCH_ROOT` / `TRELLIS_PYTHON` | External asset packs; TRELLIS and its own GPU environment (optional) |
| `SOURCE_VIDEO`, `TEX_PY`, `FONT_*` | Decal extraction and texture generation from video |
| `SAM3_CHECKPOINT`, `ANY4D_ROOT` | External measured geometry |

See `.env.example` for the full list.

**Outside the main venv:**
- Blender scripts run under Blender's bundled Python (`blender -b --python ...`).
- TRELLIS, SAM3 and Any4D are heavy GPU dependencies, and each has its own environment.
- `sources/` (SceneCode) pins Python 3.11, torch and bpy 4.5. Set it up separately with `python env/prepare_local.py`.

**Assets to download separately:**
- Baroque material textures: run `python fetch_textures.py` inside `FineScene/deliveries/baroque_reception_pbr_20261104/` (Poly Haven, CC0).
- `FineScene/assets/materials/*/`: download URLs are in each folder's `provenance.json`.

---

## 5. Quick start

```bash
python env/setup_env.py                      # then set DEEPSEEK_API_KEY (and BLENDER_EXE if needed) in runtime/.env
source .venv/bin/activate                    # Windows: .venv\Scripts\activate

# Layout loop
python adsl_adapter/layout_finalize.py
blender -b --python adsl_adapter/build_asset_scene.py

# Video → scene
python adsl_adapter/video_pipeline.py --video <video.mp4> --run <scene>          # analysis
python adsl_adapter/video_pipeline.py --run <scene> --forge                      # generate object programs
python adsl_adapter/video_pipeline.py --run <scene> --build-scene --calibrate    # assemble and calibrate
python hand_objects/build.py <object_id>                                         # build a hand-written object

# FineScene
python FineScene/src/run_general_scene.py --brief "<brief>" --out <run_dir>
```

For all options of each stage, see `README_VIDEO.md`, `FineScene/README.md` and `docs/project_overview.md`.

---

## 6. Using it with a harness model (prompt guide)

The repository is designed to be driven by a coding agent, the **harness model** (for example Claude Code), that runs the pipelines, checks the results and repairs what the downstream models could not. The guidelines it follows live in `.claude/skills/`:

| Skill | Loaded when |
|---|---|
| `codeindoor` | Any work in this repo: division of labour, invariants, escalation, acceptance |
| `scene-correction` | Correcting a reconstructed scene: poses, missing objects, hand-written models, joints, clipping |
| `finescene-curves` | Curved or turned forms in FineScene, or extending the curve primitives |

Claude Code picks these up automatically when you work inside this repo. For another agent, paste the relevant `SKILL.md` into its system prompt or project instructions.

### 6.1 How to write a good prompt

A good prompt states five things:

1. **Goal:** what should exist at the end (a scene, an object, a fix).
2. **Inputs:** the video path, the scene name or run directory, the brief, and any reference images.
3. **Constraints:** what must not change (other objects, thresholds, frozen layouts), and which component owns what. Shape goes to FineScene, semantics to the VLM, numbers to measurement.
4. **Acceptance:** which checks must pass (tests, `clip_check`, `sim2real_audit`, visual Judge, printed-graphics check).
5. **Report:** what you want back. Usually you want the following, with Agent output and harness hand-work kept apart:
   - what was generated, what was hand-written, and what is still unverified;
   - the evidence paths.

Prompts in Chinese or English both work.

Avoid the following:
- **Vague goals** ("make it better").
- **Asking the harness to hand-edit outputs before the escalation threshold.** The downstream model gets 5 attempts first.
- **Accepting "tests pass" as "scene done".** Tests, rendering and visual Judge acceptance are separate claims.

### 6.2 Prompt templates

**Reconstruct a room from a video**
```text
Reconstruct the room in <path/to/video.mp4> as scene "<scene>" with the video pipeline.
Run analysis → forge → build-scene with calibration. Use measured geometry for all sizes and poses;
if Any4D/SAM3 are not available, say so instead of using VLM estimates.
Acceptance: clip_check and sim2real_audit at 0 findings, visual review against the video cameras,
and the printed-graphics check from the codeindoor skill.
Report per object: forged / hand-written / placeholder, its check results, and the remaining gaps.
```

**Build or edit one object with FineScene**
```text
With FineScene, build a <object> (<key dimensions>, <style>, <materials>).
Use curve presets / lathe profiles where the form is curved; declare repeated parts with like/mirror
and set assembly.symmetry if it is symmetric. Do not fake carving with primitives.
Run the full FineScene flow including the visual Judge. Report the recipe path, Judge verdict,
and anything that was rejected.
```

**Resolve a harness handoff**
```text
<run dir>/harness_handoff.json has an open request for <target>.
Read the evidence and last findings, keep the Agent's attempts, and hand-write only that target
following the codeindoor skill (FineScene: --planner replay --harness-repair; video: hand_objects/<id>).
Same checks and visual Judge, no relaxed thresholds. Mark the request resolved with the result paths.
```

**Correct a reconstructed scene**
```text
In scene "<scene>", <object> is <what is wrong> (evidence: <frame / time / point-cloud slice>).
Fix it following the scene-correction skill: change only the fields that are wrong, write the evidence
in _why, rerun apply_measured, clip_check and sim2real_audit, and confirm untouched objects are unchanged.
Sync the records to cases/<scene>/ and add a CHANGELOG entry.
```

**Complete printed graphics (labels, panels, icons)**
```text
Run the printed-graphics check on scene "<scene>": compare every label/icon in the VLM specs with the models.
For each gap, cut it from the clearest video frame (or redraw it if illegible, no invented text), then
instruct the forge model via --resume-code/--initial-feedback to add it as a decal. Verify with
same-camera renders and the visual Judge. Report each graphic: cut or redrawn, who applied it, still missing.
```

**Extend the system (for example, a new curve preset)**
```text
Add a "<name>" curve preset to FineScene for <use case>. Follow the checklist in the finescene-curves skill,
add tests (every parameter must change the geometry), keep the FineScene failure list identical
to the baseline, and update the docs.
```

---

## 7. Tests and acceptance

```bash
python -m pytest tests -q                                   # repo root (offline, no Blender)
cd FineScene && python -m pytest experiments -q             # FineScene regression
blender -b --factory-startup --python tests/blender_smoke_video.py   # Blender smoke test
```

- **Use this repository's aDSL.** Check that `python -c "import adsl.core;print(adsl.core.__file__)"` points to `aDSL/adsl-core`.
- **Known failures are a baseline.** `FineScene/experiments` has some failures that existed before any change. A change passes when the list of failures is unchanged; any new failure must be fixed.
- **Geometry changes must keep both audits at zero findings.** These are `geometry/clip_check.py` (clipping and joints) and `geometry/sim2real_audit.py`.
- **Check printed graphics before delivering a video scene.** Compare every text and icon in the VLM spec with the model. For anything missing, the harness supplies the image and instructs the downstream model to add it.

---

## 8. Known limitations

- **Rendering and Judge steps need Blender and an LLM API.** These are rendering, export, forge and the visual Judge. Offline tests cover only the pure-Python parts.
- **External measured geometry and TRELLIS need their own GPU environments.** Any4D, SAM3 and TRELLIS are not included in this repository.
- **Layout code generation (`agentic_coder`) has no automatic handoff.** It still retries 3 times. When a target has failed 5 times in total, apply the same escalation rule by hand.
- **Collision checks use conservative AABBs and surface distances.** There is no friction or gravity simulation.

## 9. Documentation

- [`README_VIDEO.md`](README_VIDEO.md): video pipeline, lighting and materials, joints, hand modelling
- [`FineScene/README.md`](FineScene/README.md) and [`FineScene/AGENTS.md`](FineScene/AGENTS.md): fine-grained modelling and development rules
- `docs/project_overview.md`: methodology, architecture and reproduction steps
- `.claude/skills/*/SKILL.md`: working guidelines for the harness model (English)
- `*_AUDIT.md`, `*_REPORT.md`, `*_ANALYSIS.md` at the repo root: audit and refactoring records

## License

The code in this repository is released under the MIT License; see [`LICENSE`](LICENSE). `sources/` (SceneCode) and `aDSL/` come from upstream projects and remain under their original licenses.
