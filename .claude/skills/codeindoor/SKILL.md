---
name: codeindoor
description: CodeIndoor guidelines for indoor scene reconstruction (layout loop, video → articulated Blender scene, FineScene fine-grained modelling). Read before changing code, running a pipeline, or adding objects or scenes in this repository. Covers the division of labour between the harness model and the three reconstruction helpers (built-in FineScene for modelling, built-in VLM for descriptions, external AHa-3D-style measured geometry), the limits of textures and materials, project invariants, the escalation rule (5 failures of the same target → hand repair by the harness), acceptance requirements (including the harness-driven printed-graphics check), and which sub-skill to switch to.
---

# CodeIndoor guidelines

This file covers principles and limits only. For how to use each module, see `README.md`, `README_VIDEO.md`, `FineScene/README.md`, `FineScene/AGENTS.md` and `docs/project_overview.md`.

## 1. Division of labour

| Component | Kind | Owns | Does not own |
|---|---|---|---|
| **FineScene** | Built in | Single-object modelling. It decomposes an object into parts, writes an editable parametric recipe and checks the assembly, and the result passes through the visual Judge. The video pipeline's forge uses it too. | Which functional parts an object has; sizes and positions |
| **VLM** | Built in | Detailed descriptions of objects and their functions: the object inventory; parts, counts and positions; printed text and icons; interaction; joint structure; appearance and material class. It also compares real and rendered frames from the same camera and lists the differences. | Numbers (sizes, coordinates, colour values) |
| **AHa-3D-style measured geometry** | External. The repo only contains scripts in that style (`geometry/`); they depend on external Any4D and SAM3. | Measuring sizes, positions, orientations and cameras | Semantics and shape |
| **You (the harness model)** | — | You orchestrate the three components and check that their outputs agree. When acceptance finds a gap (for example, missing printed graphics), you instruct the downstream model to fill it. You also fix shared code and do the hand repair after an escalation (§4). | Inventing results on behalf of any component |

- **FineScene owns shape, the VLM owns semantics, measurement owns numbers.** None of them substitutes for another.
- Every output from these components is evidence; cite its source. If the external part is unavailable, say "measured geometry unavailable". Never pass an estimate off as a measurement.

## 2. Textures and materials

**Principles**
- **No texture files by default.** A material is a key made of a class, a measured colour and procedural parameters. Patterns are generated at real-world scale and need no UVs.
- **Only two kinds of surface use images:**
  - Large surfaces whose photographic look matters use scanned PBR with recorded provenance, kept out of version control.
  - Flat printed graphics use decals.
- **Measure what can be measured.** The VLM judges the class and where to sample. Colours are measured from pixels and calibrated with same-camera renders.
- **Printed graphics:** if a graphic is legible, cut it from the video with a perspective rectification. If it is illegible, redraw it without inventing text. Record the source of every image.
- **Do not count lighting twice.** A cut-out image already contains its lighting. Reflections and shadows produced by the renderer are never baked into a material.

**Limits**
- Downstream Agents (forge, FineScene) **do not extract decals from video on their own**. The harness completes printed graphics during acceptance: it finds what is missing, prepares the images and instructs the downstream model (§5, printed-graphics check). Do not claim printed graphics are reproduced in a scene that has not had this check.
- Full surface textures are not reconstructed from video, and no learned materials are generated. Patterns on curved surfaces can only be approximated.
- Scanned PBR, TRELLIS and the source video are external resources. When one is missing, fall back to procedural materials and state this in the report.

## 3. Project invariants

- **Derive, don't decide.** Anything that follows from geometry (for example, which way a wall-backed cabinet faces) is derived by code, not chosen by a model.
- **Single source of truth.** Once a layout or measurement is finalised, downstream stages may reference it but must not change it.
- **Shared conventions.** An object's origin is at the centre of its bottom face. Every joint has one degree of freedom and an explicit axis.
- **Whole-object consistency.** Declare repeated and symmetric parts as copies or mirrors instead of writing each one by hand.
- **A name is not a shape.** Capabilities are whatever the code declares. Do not name a box a curve or a carving to pass it off as one.
- **Fail explicitly.** Missing inputs and unsupported cases fail; they never degrade silently.
- **Report truthfully.** State only verified conclusions and mark what is unverified. Keep Agent output and harness hand-work separate.
- **Environment:**
  - one venv and one `runtime/.env`;
  - no machine paths in code — read them through `codeindoor_env`;
  - never commit secrets;
  - human-made inputs that cannot be regenerated go in `cases/<scene>/`;
  - external code is copied into the repo with its source noted.

## 4. Failure escalation

- **Rule.** After **5 failures in total** on the same object or stage, the harness model repairs the target by hand.
  - Build errors, failed checks and blocking visual-Judge rejections all count.
  - The count carries over across replans.
  - The threshold is defined only in `adsl_adapter/harness_handoff.py`, and every pipeline's retry limit matches it.
  - This is the only exception to the rule that the assistant does not hand-edit outputs.
- **Trigger.** The pipeline writes `harness_handoff.json` with the target, the evidence, the last findings and the repair route. Pipelines without automatic handoff follow the same rule once a target reaches 5 failures.
- **Repair.**
  - Read the evidence first, keep the Agent's failed attempts, and hand-write only the target named in the handoff.
  - The hand-written result goes through all the original checks and the visual Judge, with **no relaxed thresholds**.
  - When done, mark the request resolved and record the outputs and check results.
- If the root cause is in shared code (prompts, validators, the compiler), fix that separately. It does not hold up this hand-written delivery.

## 5. Acceptance

- **Tests.** Run the tests for whatever you changed, then the root regressions (`tests/`, `FineScene/experiments/`). If scene geometry changed, the clipping and sim2real checks must stay at zero findings.
- **aDSL copy.** Tests must use this repository's aDSL. Results from any other copy do not count.
- **Baseline failures.** Known failures are a baseline: a change passes only if the list of failures is unchanged. New failures are never waved through.
- **Kernel primitives.** When you change one, update its bounds, support and exporters together.
- **New capabilities.** A new capability means code, its capability declaration (or prompt), tests and docs, delivered together. Record version changes in the relevant changelog.

**Printed-graphics check (required before delivering a video scene; driven by the harness)**
1. **Reconcile.** Check every printed text and icon in the VLM spec against the model. Anything missing, rendered as a flat colour, or wrong is a gap.
2. **Prepare images.** For each gap, pick the clearest frame and decide whether to cut or redraw.
   - Flat graphics with clear corners: cut them out with perspective rectification (`hand_objects/texlib.py`).
   - Curved or illegible graphics: redraw them, without inventing text.
   - Record each image together with its source (video time or close-up frame).
3. **Instruct.** Give the downstream model an explicit edit instruction: add the decal to the existing program, not rewrite the object.
   - For the forge: `furniture_forge.py --object <id> --resume-code <previous program> --initial-feedback <instructions>`.
   - In the program, the decal is applied with `hand_lib.image_material` and `hand_lib.decal`.
   - The instruction states which image goes on which part and face, at what position and size, and facing which way.
4. **Verify.** After the rebuild, compare same-camera real and rendered frames, and pass the visual Judge. If the downstream model still gets it wrong, each attempt counts as 1 failure. At 5, the harness hand-writes the decal (§4).
5. **Report.** For each graphic, state whether it was cut or redrawn and who applied it, and list anything still missing.

## 6. Sub-skills

| Task | Sub-skill |
|---|---|
| Manual corrections to a reconstructed scene: poses, missing objects, hand-written models, joints and clipping | `scene-correction` |
| Curved and turned forms in FineScene, and extending the curve primitives | `finescene-curves` |
