# Repository Map — aDSL local adaptation

Date: 2026-10-17. Depth: 2; scope: core modeling, serialization, local Blender execution pipeline; Agent reasoning logic recorded only at the interface level.
Baseline commit: `0c10f36a459bf1033e89e2e5dfcf451bc30043b0`. The repository was not modified during the mapping phase; the existing uncommitted scripts and model configs were already present.

## Verified entry points and modules

| Location | Call/responsibility | Status |
|---|---|---|
| `test_adsl_direct.py` | `venv\Scripts\python.exe test_adsl_direct.py`; imports core directly, builds an Asset, composes, serializes | confirmed: ran and passed |
| `adsl-agents/cli.py` | `adsl-run create/edit/resume`; arguments are passed to service | confirmed: read, `--help` passes |
| `adsl-agents/service.py` | Orchestrates the generation flow; model, tools, execution and review | confirmed: interface read; not verified online |
| `adsl-agents/utils/execution.py` | Subprocess runs asset_executor; checks execution.json and export files | confirmed |
| `adsl-agents/utils/asset_executor.py` | Runs source via runpy, takes scene, GLB/URDF/render | confirmed |
| `adsl-core/core` | Asset, primitives, transforms, layout, booleans, joints | confirmed |
| `adsl-core/core/export/export_glb.py` | bpy creates geometry, applies transforms and joint initial states, exports the semantic hierarchy | confirmed: read; minimal material call passes |
| `adsl-core/tools/render.py` | Native rendering tool, depends on bpy and bpyrenderer | confirmed: imports and interface read; current venv lacks dependencies |
| `outputs/gpt6_final/blender_import.py` | Old JSON import script | confirmed: ran, reported 27 successful objects and printed many errors |
| `adsl-chat` / `asset` | Chat entry / sample assets | inferred: from metadata and README; outside the scope of this change |

## One complete execution path

1. `test_adsl_direct.py:25` puts adsl-core into sys.path and imports `core.asset` and `core.primitives`.
2. `core/primitives.py:72` Cube validates the three dimensions, `_build` → `primitive_record` → `Asset.add_primitive` (`core/asset.py:384`).
3. `Asset.attach_part` (`core/asset.py:63`) places the geometry in `_children` and sets the parent relation.
4. `core/serialize.py:6` `asset_to_dict` returns label, primitives, children, joints; child objects recurse via `Asset.to_dict` (`core/asset.py:519`).
5. The test confirms the hierarchy; the old demo `adsl_blender_complete.py` converts numpy to lists, then `json.dump`s, then generates a bpy script.

## Data and configuration

confirmed: the input is an aDSL Python program or a serialized Asset dict. Primitives contain `type/params/xform/color/alpha`; Cube.scale is the full size; Cylinder finally stores `p0/p1/radius`, not height. Geometric transforms are already baked into each primitive's xform (`core/transforms.py:24`), so ordinary children no longer stack the parent transform; joints additionally carry origin/initial.

confirmed: the official coordinate convention is +x right, +y inward, +z up; ordinary rotation angles are in degrees, revolute initial values are in radians (`core/docs/dsl_doc.md`). The existing laptop file uses Y as height, so the conversion should be made explicit at import.

confirmed: the official config `ModelProfile.load` (`adsl-agents/utils/config.py:44`) reads YAML; a CLI-specified path overrides the packaged default config. `api` flows to `agent_model` (`config.py:131`), which selects the Responses or ChatCompletions wrapper. Credentials can come from a file or an environment variable; the report does not record their values. The existing custom scripts additionally have a hard-coded URL and a relative key path.

## Dependencies, extension points and differences

- confirmed: venv Python 3.13.4, numpy 2.5.4, openai 2.54.0, openai-agents 0.20.0; pip check passes. Blender is at `E:\Blender\blender.exe`, version 5.2.2 LTS.
- confirmed: the README recommends Python 3.10 and bpy 4.0.0; the current venv has no bpy/bpyrenderer. An external Blender already exists and can run as a separate process, with no need to install Blender 4.0 into Python 3.13.
- confirmed: the official metadata declares mesh/render as optional dependencies; numpy is a core dependency. Online generation needs the corresponding model service and credentials.
- confirmed: relation layout (align_anchors, place_on_axis, grid_shapes, radial_shapes) and boolean operations are already implemented; the handoff document listing them as to-be-developed is inaccurate.
- confirmed: new relation operations can extend `core/layout.py`, using place_on_axis as a template; a new local execution backend can plug into `utils/execution.py` and return the existing execution.json contract; model adaptation should use the `api` branch of the existing ModelProfile.
- unknown: whether online structured output is compatible and whether the visual critic is usable. Needs verification by real requests and a full run; this round's offline checks cannot establish a complete reproduction of the paper.
- confirmed: tracked files are about 44 Python, 10 Markdown, 2 TOML; outputs, venv and egg-info are generated/environment content and are outside the source-mapping scope.

## Commands executed

`venv\Scripts\python.exe test_adsl_direct.py`: passes.
`venv\Scripts\adsl-run.exe --help`: passes.
`venv\Scripts\python.exe -m pip check`: no dependency conflicts.
`E:\Blender\blender.exe --background --factory-startup --python-exit-code 1 --python outputs\gpt6_final\blender_import.py`: the process returned 0, but internally printed cylinder NoneType and Emission slot errors. The exit code alone cannot prove everything succeeded.

## 2026-10-17: assembly relation layer extension

- confirmed: `core/relations.py` adds `RelationAssembly`, arithmetic expressions, support, spanning, anchor attachment and bounding-box fit checks; `core/__init__.py` exports the new API, and the old Asset interface is unchanged.
- confirmed: `examples/counterexamples/vintage_bookshelf_v1/` keeps a copy of the old bookshelf source; the old `height` parameter having no effect and local shelf-height changes not propagating are pinned as regression counterexamples.
- confirmed: `examples/physical_bookshelf/model.py` defines the relations, and `book_library.py` keeps the original book geometry; the default version's per-primitive world coordinates match the old version.
- confirmed: the 12 checks in `scripts/test_relations.py` pass, including four-shelf/single-shelf height increase, rigid books unchanged, wrong dependency, cycle, support and the does-not-fit failure path; the original `test_adsl_direct.py` also passes.
- confirmed: relations are re-evaluated at `compile` time; ordinary geometry export is still an evaluated snapshot. `relations.json` is an audit record, not a standalone replayable scene format. Live constraints during Blender editing and gravity/strength-of-materials solving are not implemented.
- confirmed: in the local venv the `adsl` root import is a namespace; direct scripts use `from adsl.core import ...`. The local Blender worker loads the root package explicitly, supporting Agents that use `from adsl import *`.
