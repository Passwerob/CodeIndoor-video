# Component preview and generation workflow correction

## Symptom and reproduction

The original candidate preview showed chairs without seats/legs and a desk lamp without its lower arm. The prior conclusion that these were incomplete source assets is withdrawn.

Environment: Blender 5.2.2 LTS on Windows; workspace is not a Git repository. Known comparison source is the preview script copied in `runs/realistic_assets_20261019_04/source_snapshot/`.

Minimal reproduction: import `ArmChair_01_2k.gltf`, apply the old center translation and object scale, then measure world bounds. Observed `OLD_PREVIEW_MIN_Z = -0.5325579643249512` while `FLOOR_Z = 0`. The opaque plane concealed the lower half. This explains the misleading preview without requiring missing geometry.

## Root cause and hypotheses

The old script used the center of the bounding box as the origin and placed the floor at zero. It also scaled object geometry separately from the group translation. Hypothesis 1 (incomplete source mesh) is refuted for the inspected armchair: its intact seat and four legs appear under corrected placement. Hypothesis 2 (floor occlusion caused by normalization) is confirmed by the negative minimum Z and corrected render.

Classification: root-cause fix in `src/preview_asset_candidates.py`. Freeze world transforms before detaching parents, then apply one uniform scale and bottom-center translation to every mesh. Measured minimum Z must be zero; all three dimension ratios must equal the scale. No source mesh or source texture is edited. The script now accepts repeated `--asset` arguments and a new `--output` directory, produces three views per asset, and records source/render hashes and bounds. Evidence-ready is explicitly not accepted.

## Verification

`runs/component_evidence_20261019_01/preview_manifest.json` records the armchair, articulated desk lamp, and existing local TRELLIS GLB. All three have minimum Z = 0 and pass aspect-ratio preservation checks. The corrected front views show the complete armchair and a lamp with a table-edge clamp. The lamp requires an attachment interface, rather than ordinary base-on-table placement.

The existing TRELLIS sample still appears fragmented under the corrected preview. Its source contract declares geometry-only export and no PBR. This is evidence about that local sample and compatibility path, not about TRELLIS's general quality ceiling; inference versus decoding/export failure is not localized. Neither a complex mesh nor disconnected islands alone prove semantic completeness; UV seams and legitimate separate pieces also affect topology statistics. Automatic deletion of small islands is not a verified repair.

Regression: all 9 existing `experiments/test_edit_pipeline.py` cases passed. The full original five-asset batch is rerun in `runs/component_preview_regression_20261019_01/` with ground and uniform-scale assertions enabled. Checks are permanent protection; there is no temporary instrumentation in production code.

## Target workflow and remaining work

The user clarified that the target is TRELLIS local-object generation. Downloaded assets serve as controls for assembly/rendering and optional alternatives. The main work is conditioning each component, evaluating it before insertion, preserving IDs/support constraints during rigid assembly, and retrying only failed components. These are architecture requirements, not a claim that the full loop has been implemented.

The local preview evidence stage is implemented and verified. Component semantic Judge integration, generative retry orchestration, and repair of the existing TRELLIS inference/export path remain open. Scene-wide lighting errors belong to the renderer; they should not trigger regeneration of valid objects.

The experimental full scenes `realistic_assets_20261019_03` and `_04` remain unaccepted. Run 03 failed to supply image paths to Judge. Absolute paths fixed that in run 04; its independent Judge now rejects lighting/exposure. The material/lighting experiments and attempted chair edits did not produce an accepted scene and should not be promoted to the primary pipeline. Prior claims that the pointed chair ornaments were successfully removed were not supported by the rendered result.
