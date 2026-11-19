# aDSL local workbench

Adaptation date: 2026-10-17. Project: `E:\3DUse\aDSL`.

This round got the local modeling, import, execution, export and preview pipeline working. Online generation, the visual Critic and the paper's metrics have not been verified, so this cannot be taken as a complete reproduction of the paper.

## Local environment

| Item | Verified configuration |
|---|---|
| Python | `E:\3DUse\aDSL\venv\Scripts\python.exe`, 3.13.4 |
| aDSL | core 0.2.0 / agents 0.3.0, editable install |
| Blender | `E:\Blender\blender.exe`, 5.2.2 LTS |
| SDK | openai 2.54.0 / openai-agents 0.20.0 |
| Dependencies | pip check passes; direct core tests pass |

It uses the existing virtual environment and Blender's bundled Python; there is no need to install the old bpy 4.0.0 into Python 3.13, and it does not depend on Blender MCP. Scripts prefer the command-line `--blender`, then the environment variable `ADSL_BLENDER_EXECUTABLE`, then PATH, known local locations, and a Program Files probe. All path arguments are passed as argument arrays.

## Everyday commands

Run in PowerShell; no need to activate the virtual environment:

```powershell
Set-Location 'E:\3DUse\aDSL'
& .\venv\Scripts\python.exe scripts\local_3d.py doctor

# The existing laptop JSON is Y-up. The output directory must be new or empty.
& .\venv\Scripts\python.exe scripts\local_3d.py build --input outputs\gpt6_final\laptop.json --output outputs\my_laptop_preview --up-axis y --rgb-emission 4

# New aDSL programs use native Z-up and assign `scene`.
& .\venv\Scripts\python.exe scripts\local_3d.py build --source .\model.py --output outputs\my_model --views 3

# Run the native source and JSON round-trip regression checks, without calling any model service.
& .\venv\Scripts\python.exe scripts\verify_local_3d.py
```

By default `build` outputs a preview with CPU Cycles, producing `execution.json`, the serialized `asset.json`, `scene.blend`, `render/scene.glb` and `render/view_*.png`. `--no-render` only models and exports; `--urdf` adds URDF (native Z-up only); `--open` opens the finished project; `--resolution` adjusts the preview size. When emission is needed, set `--rgb-emission` explicitly; it applies only to keycaps/LEDs with the agreed semantic names and does not change the source JSON.

When the scene contains an `ExternalMesh`, `execution.json` also writes `external_components`, including the semantic node path, original GLB path, vertex/face counts and the instance world bounding box. It is used for relation auditing and Critic checks, and does not replace the local bounds and mounting relations in the source code.

`--up-axis y` is only a coordinate conversion at import time; it does not correct wrong part positions or orientations in the source model.

## Official Agent using local Blender

When `ADSL_BLENDER_EXECUTABLE` is set, `adsl-agents/utils/execution.py` calls a new external Blender worker and keeps the original execution-result contract; when unset it still uses the original Python/bpy backend. The entry below selects the local Blender automatically:

```powershell
& .\venv\Scripts\python.exe scripts\local_3d.py agent --model-config adsl-agents\configs\llm\deepseek-local.yaml create 'A simple chair' --output outputs\new_agent_case --max-rounds 1
```

This command calls the model service; it was not run this round. The new `deepseek-local.yaml` uses the existing credential file and the `chat_completions` adapter; config loading and adapter construction were verified, but online responses were not. The original model configs are left as they were. Visual review also requires the model to support image input; being able to call text generation does not mean visual review is available.

## Actual verification completed

| Case | Result | Output |
|---|---|---|
| Core direct test | Pass | `test_adsl_direct.py` |
| GPT laptop JSON | 101/101 meshes, 71 semantic emissive meshes | `outputs/local_gpt6_20261017/` |
| DeepSeek laptop JSON | 254/254 meshes, 161 semantic emissive meshes | `outputs/local_deepseek_20261017/` |
| Native execution regression | Pass: 4 geometries, 2 PNGs, GLB, URDF, JSON round trip | `outputs/local_verification_*/` |
| Geometry check | Coordinates after nested transforms, slanted-cylinder endpoint projection, joint pivots and initial poses are correct | .blend checks inside the regression script |
| Failure check | A source that raises gives a non-zero exit, the main process receives AssetExecutionError, and no fake success manifest is produced | Regression script |

Two model previews were inspected: the body and keyboard are visible; the old models still have problems such as a protruding touchpad and unreasonable geometry for some ports/hinges. These are source-model quality issues and were not modified in the import adaptation. Manufacturing tolerances, all collisions, closed manifolds and the paper's metrics were not verified.

## Installed skills

Skills are installed in `%USERPROFILE%\.codex\skills` and are available in the next conversation; if the list does not refresh, restart the app.

| Skill | Purpose | Source |
|---|---|---|
| `adsl-model-planning` | Semantic part decomposition, dimension dependencies, connection relations, build order, aDSL implementation and local checks | Customized this round, verified against the real API and the local pipeline |
| `hard-surface` | Mechanical/hard-surface structure, panel, boolean and bevel planning | arjun988/blender-skills |
| `prop-artist` | Primary/secondary forms and size planning for furniture, electronics and everyday props | Same as above |
| `procedural-modeling` | Parametric, repeated-structure and procedural modeling | Same as above |

Third-party source: <https://github.com/arjun988/blender-skills>, pinned commit `8f778d2405a214b508d4c7d80742be8e43acdd52`. Each skill's `origin.json` and the MIT LICENSE are kept; only background-bpy execution notes for when there is no local MCP were added, and the shared reference-doc path was fixed for hard-surface. All four skills pass `quick_validate.py`; on Windows the validator needs `python -X utf8`.

Example request: `用 $adsl-model-planning 把这个对象分解为部件，先给出尺寸和连接关系，再建立参数化模型并渲染检查。` (Use $adsl-model-planning to decompose this object into parts, first give the dimensions and connection relations, then build a parametric model and render it for checking.)

## Adaptation implementation and boundaries

- `scripts/local_3d.py`: environment probing, JSON/source modeling, launching the official Agent.
- `scripts/blender_worker.py`: uses the repository's official geometry builders, supports endpoint cylinders, primitive xforms, hierarchy and joints; handles emissive slots by name; outputs a new project and manifest.
- `scripts/verify_local_3d.py`: offline integration regression and real .blend geometry assertions.
- `REPOSITORY_MAP_LOCAL.md`: pre-change record of code paths, configuration and dependency verification.
- `LOCAL_DEBUG_REPORT.md`: old-script faults and evidence from validating the new path.

The old `outputs/*/blender_import.py` and the old import template embedded in the original generation scripts are kept as historical results; use the unified entry going forward. They may keep swallowing errors and cannot serve as a local success baseline. The new path reuses the official exporter's private build functions; rerun the regression after upgrading the repository. Although boolean functions already exist, this round did not cover complex booleans or full URDF simulation compatibility.
