# SCENECODE_AUDIT.md

> PHASE 0 — Repository Audit
> Audit target: `wangpuyi/SceneCode` (official repository, main branch, last pushed 2026-06-18, 64 stars)
> Paper: arXiv:2605.19587 "SceneCode: Executable World Programs for Editable Indoor Scenes with Articulated Objects", submitted 2026-05-19
> Audit date: 2026-11-08
> Local snapshot: `E:/3DUse/SceneCode_audit/sources/` (saved by repository-relative path)

---

## 0. Audit method and boundaries (please read this section first)

**This report is a static source-code audit, not a runtime verification.**

- Because this machine's proxy returns `CONNECT tunnel failed 502` for `git clone`, and rate-limits codeload source archives without supporting resumable downloads, this audit instead fetched **the real source on demand** through the GitHub `git/trees` + `git/blobs` APIs.
- **37** source/config/prompt files were read in full (list in the appendix), covering the room-level backbone, the Code_Object framework, strategy routing, data structures, the LLM client, image generation, the Blender add-on, and experiment orchestration.
- Repository file tree: 783 entries / 694 blobs / about 80.6 MB (`py` 394, `obj` 112, `yaml` 80, `bin` 27, `sdf` 13).
- **Not executed**: `uv sync`, `pytest`, `python main.py`, Blender, any LLM call, any rendering.
- Therefore every statement in this document about "whether it runs" or "whether it is usable" is a **code-level inference**, and anything involving runtime results is marked `[pending runtime verification]`. By task discipline, inference is not passed off as verification results.

**Authenticity note (avoid inferring code from the paper)**: every conclusion in the report is annotated with file and line number. Anything the paper mentions but that was not found in the code is explicitly stated as "not found".

---

## 1. Project architecture

SceneCode is a **two-layer stacked** system, not a single architecture:

| Layer | Directory | Responsibility | Origin |
|---|---|---|---|
| A. Room-level Agentic Backbone | `scenecode/floor_plan_agents/`, `furniture_agents/`, `wall_agents/`, `ceiling_agents/`, `manipuland_agents/`, `scenecode/agent_utils/`, `scenecode/experiments/` | Text → room layout → per-object requests → placement → rendering → Critique → rollback | **Inherited from SceneSmith** |
| B. Object-level Code_Object framework | `scenecode/code_object/agent_framework/` (49 files) | Single object → ObjectPlan → per-part Blender Python program → execute/repair → GLB/SDF/URDF | **SceneCode's core contribution** |

**Hard evidence of the inheritance** (not inferred from README acknowledgments):

- `README.md:215` explicitly acknowledges SceneSmith.
- `pyproject.toml:62` `packages = ["scenesmith"]`, `:76` `known_first_party = ['scenesmith']`: the packaging and isort configuration still point to `scenesmith`, a leftover not cleaned up after the fork, and **`uv sync` / `pip install -e .` may install the wrong package name**; this is a deployment risk.
- The 137 files of `scenecode/agent_utils/` (BlenderServer, AssetRegistry, image_generation, rendering, convex decomposition, HSSD/Objaverse retrieval) are isomorphic to SceneSmith's directory structure.
- The five stages `floor_plan → furniture → wall_mounted → ceiling_mounted → manipuland` in `configs/experiment/base_experiment.yaml` are identical to SceneSmith's.

**What SceneCode itself adds**: `code_object/agent_framework/` (Planner / PlannerChecker / PartConstructor / BlenderMCP), the `code_generated` / `code_articulated` strategies of `asset_router`, `code_articulated_pipeline.py`, `urdf_pipeline_v1.py`, `postprocess_pipeline_v1.py`.

### 1.1 Main call chain

```
python main.py +name=...                    (Hydra, configs/config.yaml)
  └─ experiments/indoor_scene_generation.py
       ├─ Stage floor_plan      → StatefulFloorPlanAgent
       ├─ Stage furniture       → StatefulFurnitureAgent
       │    └─ AssetRouter.route()                     [asset_router/router.py]
       │         └─ 5 strategies → _try_code_generated_strategy / ...
       │              └─ _prepare_code_object_inputs()  ★generates the reference image
       │              └─ CodeObjectRunner.generate_from_image()
       │                   └─ code_object/agent_framework/Pipeline.run()
       │                        ├─ PlannerAgent          → ObjectPlan.json
       │                        ├─ PlannerCheckerAgent   → plan_check_history.json
       │                        ├─ PartConstructorAgent  → code/<pkg>/<part>.py + <object>.py
       │                        │    └─ BlenderMCP.execute_code()  → execute / error repair
       │                        ├─ _export_part_mesh_assets()  → OBJ / GLTF
       │                        ├─ _render_full_object_and_save() → renders/full_object.png
       │                        └─ _export_full_object_glb()   → GLB
       ├─ Stage wall_mounted / ceiling_mounted / manipuland
       └─ save_scene_as_blend() → scene_states/<name>/scene.blend
          combined_house/ → house.blend / house.dmd.yaml / house_state.json
```

Key code locations:
- Five stages and resume: `configs/experiment/base_experiment.yaml:36-41`, `scenecode/experiments/indoor_scene_generation.py:1646-1648`
- `.blend` saving: `indoor_scene_generation.py:233-257` (`save_scene_as_blend`, output to `scene_states/<name>/scene.blend`)
- Scene state: `room.py:1593 to_state_dict()` / `room.py:1619 restore_from_state_dict()`, `indoor_scene_generation.py:1128`, `:1161`
- Final artifact list: `README.md:202-211`

**Two independent Blender backends** (easy to trip over):
1. Room level: the `scenecode/agent_utils/blender/server_manager.py` family, port range `8000–8350` (`base_furniture_agent.yaml:43`), used for scene rendering, multi-view analysis, and annotation.
2. Code_Object level: `code_object/agent_framework/blender/` (MCP addon + headless_server + mcp_client), port `9676` (`agent_framework/config.yaml:64-65`), port range `9900–9999` (`base_furniture_agent.yaml:127`), used to execute part Python code.

---

## 2. Conclusions for each of the ten PHASE 0 checkpoints

### ① Implementation of Planner / Designer / Critic

**There are two sets, with different names; do not confuse them**:

| Role | Room level (inherited from SceneSmith) | Object level (SceneCode contribution) |
|---|---|---|
| Planner / Designer | `BaseStatefulAgent` builds three `Agent`s with the OpenAI **Agents SDK**: `base_stateful_agent.py:241`, `:274`, `:306` (all `model=self.cfg.openai.model`) | `agents/planner.py:16 PlannerAgent` (generates ObjectPlan) |
| Critic | `max_critique_rounds: 3`, `early_finish_min_score: 9` (`base_furniture_agent.yaml:275-283`); `CheckpointState` dual-snapshot N-1/N rollback (`checkpoint_state.py:18-40`; `base_stateful_agent.py:470`, `:528`, `:871`) | `agents/planner_checker.py:54 PlannerCheckerAgent`, `check_and_fix()` (`pipeline.py:908`), `max_retry: 5` (`agent_framework/config.yaml:98-99`) |

**Important**: the object-level "Critic" is a **Plan Checker (checks the reasonableness of the ObjectPlan)**, not a closed loop of "rendered image → visual review → repair geometry". The room level has a `Render → Critique → Reset` loop (based on scoring and checkpoint rollback), but that is at the **placement layer**, not visual repair at the part-geometry layer. If the task's "Render → Critic → Repair closed loop" refers to object geometry quality, that implementation was **not found** in the code; `PartConstructorAgent` only has **execution-error repair**, not visual-quality repair.

### ② Complete data structures of AssetRequest and ObjectPlan

**The `AssetRequest` class was not found in the code.** A full-repository grep finds it only in `README.md:43` (paper abstract). Its counterparts in the code are:

- `scenecode/agent_utils/asset_router/dataclasses.py:10 class AssetItem`: `description`, `short_name`, `dimensions: list[float]`, `object_type`, `strategies: list[str]`, `thin_covering_type`, `code_object_profile: str | None`.
- `:97 GeneratedGeometry` in the same file: `geometry_path`, `item`, `asset_source`, `image_path`, `code_object_output_dir`, `object_plan_path`, `code_dir`, `pipeline_result_path`, `full_object_render_path`. **This is the actual carrier that links "request / program / render / geometry"**.
- `:137 CodeArticulatedGeometry` additionally contains `urdf_path`, `sdf_path`.
- Scene objects: `SceneObject` / `UniqueID` / `ObjectType` in `scenecode/agent_utils/room.py` (used by `AssetRegistry`).

`ObjectPlan`: `scenecode/code_object/agent_framework/schemas/object_plan.py:236`, full fields:

```
ObjectPlan: category, name, description, parts: List[PartPlan],
            total_dimensions: Dimensions, style, material_hints, metadata
PartPlan   (:168): name, part_type, shape: PartShape, position: Position(★world coordinates),
            rotation: Rotation, is_symmetric, symmetric_axis, is_movable,
            must_be_independent, description, priority, material: Dict,
            sub_parts: List[PartPlan] (★only one level), instances: List[Dict]
PartShape  (:131): base_shape: ShapeType(cube|cylinder|sphere|cone|torus|custom),
            dimensions, modifiers: List[str], description
PartRelation (:108) / RelativePosition (:33) / BlenderAction (:311) / ActionTrace (:337)
```

**Three places found in the code that are "declared but not wired up"**:
- `:221-222` of `PartPlan.from_dict()` has the `parent` and `relations` lines **commented out**: `PartRelation` / `RelativePosition` are defined but **not connected to ObjectPlan deserialization**. Spatial relations effectively do not enter the ObjectPlan.
- `sub_parts` is forcibly de-nested in `from_dict():212` with `sp_data.pop("sub_parts", None)`, so the hierarchy has only one level.
- The `position` comment explicitly says "position (world coordinates)" (`:173`), **not local coordinates / Local Transform**.

### ③ The five Object Code Generation Strategies

Configuration source `configs/furniture_agent/base_furniture_agent.yaml:90-120`, implementation in `asset_router/router.py`:

| # | Strategy | Default | Entry | Code-based? | Image generation dependency |
|---|---|---|---|---|---|
| 1 | `code_generated` | ✅ enabled | `router.py:1303 _try_code_generated_strategy` → `:1488 _generate_code_object_geometry` | ✅ | **Mandatory** (see §6) |
| 2 | `code_articulated` | ✅ enabled | `code_articulated_pipeline.py:62` (`Pipeline` + `URDFPipeline` wrapper) | ✅ | **Mandatory** (same as above) |
| 3 | `generated` | ❌ disabled | legacy geometry generation, `backend: sam3d` (`:76`) | ❌ | SAM3D |
| 4 | `articulated` | ❌ disabled | `router.py:579`, ArtVIP/PartNet-Mobility retrieval | ❌ | None (needs a 72 GB-class dataset) |
| 5 | `thin_covering` | ✅ enabled | `router.py:883`, thin surfaces such as rugs/yoga mats | ❌ | `generator.backend: "openai"` as a material fallback (`:114-120`) |

**Conclusion**: the paper's "five code-generation strategies" are, in the code, actually **5 asset acquisition strategies, of which only 2 are code-based**, and `code_articulated` is just a URDF wrapper over `code_generated` (`code_articulated_pipeline.py:74-86` directly does `self.pipeline = Pipeline(...)`). The path that actually carries furniture generation by default is the single `code_generated` path. This is the second hard proof of "do not infer code from the paper".

### ④ Part-wise Blender Python generation and execution mechanism

- Generation: `part_constructor.py:134 _generate_code_with_llm()` → `:106 _validate_generated_code_for_part()` → `:327 construct_part()` (single part)
- Execution: `:232 _execute_code()` → `_call_blender("execute_code", {"code": code})`; Blender side `blender/addon.py:509 execute_code`
- Error repair: `:247 _generate_code_fix_from_error()`: feeds the Blender error + the full code back to the LLM and **requires the complete corrected full code as output** (`:264-276` "Do not output only a patch snippet"), `max_retry: 5` (`config.yaml:102-103`)
- Code persistence: `pipeline.py:434-447` checks `code/<parts_pkg>/<part.name>.py`; `:453 _get_ordered_part_script_paths()`; `:501 _build_ordered_part_execution_code()` (injects `sys.path`, then imports and executes in order)
- Main file: `part_constructor.py:623 _generate_main_file()`, containing `clear_scene()` (`:681`)
- Artifacts: `pipeline.py:549 _export_part_mesh_assets()` (per-part OBJ/GLTF), `:726 _export_full_object_glb()`, `:758 _render_full_object_and_save()`, `renders/full_object.png`

**Note**: the part code is arbitrary `bpy` Python strings freely generated by the LLM; `PartShape` is passed in only as a **prompt constraint**, not as executable structured geometry nodes. There is no parameter table, no data types, no dependency graph.

### ⑤ Data structure and update mechanism of the Scene State Registry

**There are two states of different granularity; do not conflate them**:

1. **Object-level `AssetRegistry`** (`agent_utils/asset_registry.py:17`): `dict[UniqueID, SceneObject]`, `register/get/list_all/save_to_file/load_from_file` (`:149`, `:202`). Serialized fields (`:162-189`): `object_id`, `object_type`, `name`, `description`, `transform(translation + rotation_wxyz)`, `geometry_path`, `sdf_path`, `image_path`, `metadata`, `bbox_min/bbox_max`, `scale_factor`. Supports incremental flushing to disk via `auto_save_path` (`:54-57`). **The granularity is the whole object, with no parts and no program references** (the program path is in `GeneratedGeometry`, not among the registry's fields).
2. **Room-level `scene_state.json` / `house_state.json`**: `room.py:1593 to_state_dict()` → `indoor_scene_generation.py:1128`, restored with `restore_from_state_dict()` (`:1161`). It is a **scene snapshot dict**, used with staged checkpoints such as `scene_states/scene_after_furniture/` (`indoor_scene_generation.py:646`, `:718`, `:780`) and `resume_from_path` (`:1646-1648`) for stage-level resume; `base_stateful_agent.py:470/528/871` uses `copy.deepcopy` N-1/N snapshots for quality rollback.

**Conclusion**: the paper's "persistent scene-state registry links object requests, executable programs, rendered geometry, and simulation assets" is implemented in the code **by file-path fields (`GeneratedGeometry`) + two levels of JSON snapshots**, not as a semantic state tree with stable part-level IDs.

### ⑥ Blender building, rendering, saving, exporting

- Room level: `agent_utils/blender/server_manager.py` + `renderer.py` (123 KB) + `rendering.py` (62 KB); `configs/furniture_agent/base_furniture_agent.yaml:24-68` configures `top_plus_sides` multi-view, 1024² top view + 4×512² side views, EEVEE TAA 8 samples, set-of-mark annotation.
- Code_Object level: `agent_framework/blender/addon.py:704 render_scene()` (CYCLES / EEVEE / EEVEE_NEXT, `:726-739`), `headless_server.py`, `mcp_client.py`; `agent_framework/config.yaml:74-88` defaults to EEVEE / 32 samples / **336×336**.
- Saving `.blend`: `indoor_scene_generation.py:241-257` → `scene_states/<name>/scene.blend`; finally `combined_house/house.blend` (`README.md:208`).
- Export: GLB (`pipeline.py:726`), per-part OBJ/GLTF (`pipeline.py:549-620`), SDF / Drake `.dmd.yaml` (`README.md:209`), URDF (`code_articulated_pipeline.py:165`).

### ⑦ All LLM API call locations and model configuration

See the §4 model-call inventory.

### ⑧ Image generation, Flux, external models, asset retrieval dependencies

See §6 (the most critical section of this report).

### ⑨ Concrete implementation of object-local modification and scene restoration

**Exists**:
- `pipeline.py:420 _check_existing_plan()` / `:432 _check_existing_construction()`: reuse existing `ObjectPlan.json` and part `.py` files, skipping regeneration (`:940-977` "Using cached construction").
- `pipeline.py:721 _rebuild_scene_from_saved_code()`: rebuild the complete object from saved part code (called by `:736`, `:773`).
- `code_object/agent_framework/config.yaml:112` `enable_part_construct: false`: "part code already exists, only load it and generate the main file".
- Room-level `resume_from_path` stage resume + checkpoint rollback.

**Does not exist**:
- **Not found**: an API to "locate a part → change one dimension parameter → re-run only that part". Part code is a whole block of Python string, and error repair is also a whole-block rewrite.
- `pipeline.py:870-872` calls `_clear_blender_scene()` at the start of every `run()`, then replays **all** part scripts in order: the rebuild is a full object-level one.
- Room-level "local modification" is `restore_from_state_dict()` snapshot rollback + resuming from some stage, not an incremental edit that preserves the other objects.

**Conclusion**: what SceneCode provides is **part-level program persistence (reusable part program files) + stage-level scene restoration**, not **semantic component-level localized edit**. The "re-execute only the affected components, do not regenerate non-target geometry" required by task PHASE 5 **does not exist** in SceneCode's native capability and must be supplied on the aDSL side.

### ⑩ Can the official examples / configs / tests run directly

- `tests/` 296 files: `unit/` 101, `integration/` 25, `test_data/` 166. `pyproject.toml:78-80` has pytest configuration (`asyncio_mode = "auto"`).
- `tests/unit/test_default_asset_strategy_config.py` is a pure configuration smoke test (mocks the four `_try_*_strategy`), **needs no GPU/Blender/LLM**, and is the easiest item to get running first on this machine.
- But `tests/unit/test_flux-*.py`, `test_image_generation.py`, `test_blender*.py` (37 KB), etc. need external models or Blender.
- `examples/` has only 1 file (`examples/prompts.csv`, referenced at `README.md:198`), **no directly runnable demo scene**.
- `configs/` 14 yaml; the quick start at `README.md:157` is `python main.py +name=my_experiment`, but a full run needs Blender + Drake + a pile of checkpoints + LLM/image APIs.
- `[pending runtime verification]` All tests. Static judgment: **on Windows without a GPU and without an external image generation service, the full test suite cannot possibly pass directly**.

---

## 3. Model call inventory

**Key structural fact: SceneCode has two mutually independent LLM call stacks, so changing one place is not enough.**

| # | Call stack | SDK / endpoint | Default model | Config location | Purpose |
|---|---|---|---|---|---|
| A | Room-level backbone | **OpenAI Agents SDK** (`openai-agents>=0.6.4`), `base_stateful_agent.py:241/274/306` builds `Agent(model=cfg.openai.model)` | `gpt-5.4` | `configs/furniture_agent/base_furniture_agent.yaml:287` | Planner / Designer / Critic trio |
| A' | Room-level session summary | Same stack | `gpt-5.2` | `base_furniture_agent.yaml:13` | session memory summarization |
| A'' | Room-level VLM | `OpenAI/AsyncOpenAI` of `scenecode/utils/openai.py` (`:41-79`), `base_url = ${oc.env:OPENAI_API_BASE,https://api.openai.com/v1}` (`configs/config.yaml:5`) | Same `gpt-5.4` | `openai.py:15-16` | mesh physics analysis / asset router analysis / validation (`:302-304`) |
| B | Code_Object framework | `AsyncOpenAI` + **`chat.completions.create`** (`utils/llm_client.py:278`) | **`gemini-3.1-pro-preview`** | `code_object/agent_framework/config.yaml:13/21/30/38` | Planner, PartConstructor, PlannerChecker, Vision: all four positions use the same model |
| B' | Backup provider | `llm_client.py:313 AnthropicClient` (default `claude-3-opus-20240229`, `:327`) | — | `config.yaml:8` `provider: openai\|anthropic\|local` | Not enabled |

**Protocol-level coupling points (a DeepSeek adaptation must handle each one; swapping only the URL + model name is not enough)**:

1. `base_furniture_agent.yaml:296-316` passes `reasoning_effort` (`minimal/low/medium/high/xhigh`) and `verbosity` (`low/medium/high`); `:289` passes `service_tier`; `:292` passes `vision_detail: "high"`; `configs/config.yaml:11` is also `service_tier`. These are all OpenAI-specific parameters that the official DeepSeek API does not recognize.
2. `utils/openai.py:72-79` has `call_model_input_filter` and an `AsyncOpenAI` singleton cached by `base_url`; `image_generation.py` and the agent share `OPENAI_API_KEY`, so **pointing the key at DeepSeek makes `images.generate` hit DeepSeek and return 404**.
3. `llm_client.py:193` `need_thought_signature = "gemini-3" in model_lc`, `:205-234` injects `extra_content.google.thought_signature` into tool_calls: this is **Gemini-3-specific compatibility logic**; after changing the model name a different branch is taken, and it must be verified that nothing is collaterally broken.
4. `llm_client.py:265-277` always passes `temperature` and `max_tokens`, and forwards `tools` / `tool_choice`; when `tool_calls` are returned they are wrapped as `{"content", "tool_calls"}` (`:298-308`). In thinking mode, DeepSeek ignores `temperature`, returns 400 for `tool_choice:"required"`, and requires `reasoning_content` to be passed back verbatim, otherwise 400.
5. `llm_client.py:243-261` handles **multimodal input blocks** (`image_url`) where `content` is a list, and `:312-317` in `part_constructor.py` encodes the reference image as `data:image/...;base64` for input. DeepSeek Flash's vision capability needs separate measurement; `deepseek-v4-pro` does not support image input.
6. `OPENAI_TRACING_KEY` (`.env.example:3`) is for OpenAI Agents tracing; DeepSeek has no corresponding service, so tracing needs to be turned off rather than left blank.

**Current state of the official DeepSeek API (verified 2026-11-08)**: `deepseek-flash` (supports image input, tool calls, thinking/non-thinking, JSON output) and `deepseek-v4-pro` (text only). `base_url = https://api.deepseek.com`, compatible with OpenAI / Anthropic formats. `deepseek-chat` / `deepseek-reasoner` were deprecated on 2026-07-24. **There is no image generation interface**.

---

## 4. External dependency inventory

### Python dependencies (`pyproject.toml:6-48`, `requires-python >=3.11,<3.12`)

| Category | Packages | Risk |
|---|---|---|
| LLM | `openai>=2.8.0`, `openai-agents>=0.6.4`, `google-genai>=1.0.0` | Protocol differences between Agents SDK and DeepSeek (§3) |
| Blender / geometry | `bpy==4.5.4` (dedicated index `download.blender.org/pypi`), `mathutils`, `trimesh`, `manifold3d`, `pymeshlab==2023.12.post3`, `coacd`, `vhacdx`, `xatlas`, `python-fcl`, `shapely`, `rtree`, `pygltflib` | Several need compiled extensions; Windows wheel coverage is incomplete |
| Simulation | `drake>=1.47.0` | pydrake support on Windows is limited; officially designed for Linux |
| ML / GPU | `torch>=2.5,<2.6`, `torchvision`, `diffusers>=0.35.1`, `transformers>=4.55.4`, `open-clip-torch>=2.20.0`, `einops`, `opencv-python` | Without a GPU, CLIP retrieval / SAM3D / Flux are all unavailable |
| Config / other | `hydra-core`, `omegaconf`, `numpy<2.0`, `scipy`, `flask`, `pyvirtualdisplay` (Linux Xvfb), `wandb`, `ninja`, `pybind11` | `pyvirtualdisplay` is a Linux dependency |

### External services / checkpoints / environment variables (the full `.env.example`)

```
OPENAI_API_KEY          # shared by the room-level agent + image generation
OPENAI_API_BASE         # default https://api.openai.com/v1
OPENAI_TRACING_KEY      # tracing
BLENDER_PATH            # .env.example says blender; README uses PATH + SCENECODE_BLENDER_GLOBAL_LOCK
SCENECODE_OUTPUT_DIR / SCENECODE_MATERIALS_DIR / SCENECODE_MATERIAL_EMBEDDINGS_DIR
SAM3_CHECKPOINT / SAM3D_PIPELINE_CONFIG     # SAM3D backend + wall_art cropping
FLUX_PYTHON / FLUX_MODEL_PATH               # separate conda environment + FLUX.2-klein-9B checkpoint
GOOGLE_API_KEY                             # Gemini image generation
```

- The README additionally requires `SCENECODE_BLENDER_GLOBAL_LOCK` (`README.md:128`), a cross-process file lock; **this variable is not written in `.env.example`**, a documentation inconsistency.
- The `materials/` directory contains 7 files (1K-JPG textures such as Plaster001 / Wood094), so the repository does ship a few materials.
- Asset retrieval: `HSSD` (`data/hssd-models`), `Objaverse/ObjectThor` (`data/objathor-assets`), `ArtVIP` (`data/artvip_sdf`, default enabled), `PartNet-Mobility` (default disabled), CLIP embeddings of `AmbientCG` materials. These are all **tens-of-GB-scale** external data.

---

## 5. Image Generation dependency locations (★ key point)

The task specifically warned "do not assume that turning off Flux means all image generation dependencies are turned off"; the source confirms this warning is right. **At least 7 independent locations**:

| # | Location | Behavior | Difficulty to disable |
|---|---|---|---|
| 1 | `agent_utils/image_generation.py:190` `client.images.generate(...)` | **OpenAI image generation**, `backend: "openai"` is the **default** | High: default main path |
| 2 | `image_generation.py:351-376` `GeminiImageGenerator` (`gemini-3-pro-image-preview`), `:520` | Gemini image generation / editing | Medium: needs `GOOGLE_API_KEY` |
| 3 | `image_generation.py:588-815` `FluxKleinImageGenerator` + `flux_klein_worker.py` | Local **FLUX.2-klein-9B** subprocess | Medium: separate conda + checkpoint |
| 4 | `base_furniture_agent.yaml:189-190` `backend: "openai"` / **`fallback_backend: "flux-klein"`** | **Automatically falls back to Flux** when the main backend fails | ⚠️ Changing the main backend is not enough; the fallback must be changed too |
| 5 | `router.py:1394-1420 _prepare_code_object_inputs()` | The docstring says outright "Generate the reference image"; `:1411` **unconditionally** calls `image_generator.generate_images(...)`, and failure means `return None`, making the whole strategy fail | ⚠️ **Most fatal**: the `code_generated` / `code_articulated` main strategies are **image-conditioned** in the room pipeline, followed by `generate_from_image()` (`:1511`, `:1552`) |
| 6 | `router.py:2112-2121`, `:2151` | Another `image_generator.generate_images(...)` + `image_path` passing | Needs checking one by one |
| 7 | `base_furniture_agent.yaml:114-120` thin_covering `generator.backend: "openai"` | AI material generation fallback when material retrieval fails entirely | Medium |

**Two more categories of external generation/retrieval services that are easy to miss** (both in `blender/addon.py`, i.e. the BlenderMCP plugin; controlled by switches but the code does exist):

- `addon.py:242/1484-1763` **Hyper3D / Rodin** paid text/image-to-3D (`blendermcp_hyper3d_api_key`)
- `addon.py:251/2253-2510` **Hunyuan3D** (`blendermcp_hunyuan3d_secret_id/key`, `hunyuan3d_api_url`, `:2464`, `:2508` call `/generate`)
- `addon.py:232` **Poly Haven** materials, `addon.py:251/1820-2037` **Sketchfab** asset retrieval
- `router.py:1446-1461` **the wall_art profile uses the SAM3 checkpoint for picture-frame cropping**: even if no images are generated at all, the wall-art path still touches SAM3

**Two more "image input" dependencies (not generation, but they also need external models)**:
- `stateful_furniture_agent.py:249`, `base_furniture_agent.yaml:399-400 context_image_generation` (default `enabled: false`, safe to leave off)
- The **VLM multi-view grid validation** at `router.py:335-365`, `:754-793`, `:1178-1189` (Blender renders are sent to the LLM as base64): this falls under the permitted "DeepSeek visual review", but requires the model to accept image input.

### Conclusion and feasible alternative path

`Pipeline.run()` of `Code_Object` **supports a text-only mode**: `pipeline.py:369-378 _validate_generation_input()` requires **one of** `image_path` and `text_input`, `run():835-849` has an `input_mode == "text"` branch, `part_constructor.py:301-305` also splices `text_input` into the prompt, and `prompts/planner.md` / `constructor.md` have text-mode prompts.

So "disabling image generation" is technically **feasible**, but it requires code-level changes, not a configuration switch:

1. Add a `CodeObjectRunner.generate_from_text(text_input=...)` path (or have `_prepare_code_object_inputs` return `text_input` when image generation is disabled);
2. Modify the `generate_from_image(...)` calls at `router.py:1511` / `:1552`;
3. Change `fallback_backend` to a value that triggers no generation, and handle the `image_generator is None` branch;
4. Turn off thin_covering's `generator.enabled`;
5. Turn off the Hyper3D / Hunyuan3D / Sketchfab / PolyHaven switches in the addon;
6. Keep VLM validation, but route it through DeepSeek vision input.

`[pending runtime verification]` The quality of the ObjectPlan generated by `PlannerAgent` in text-only mode (part richness, whether it degenerates into stacked cubes): this is the core risk that PHASE 2 must measure.

---

## 6. Object-level code generation and editing mechanism (key judgments for integrating with aDSL)

**Mappable parts**:
- `ObjectPlan.parts` → a semantic part list; each part has a stable string `name`, an independent `.py` program file, `is_symmetric`/`symmetric_axis`, `is_movable`, `material`, `priority`.
- `GeneratedGeometry` already binds `object_plan_path` / `code_dir` / `pipeline_result_path` to the geometry artifacts: a ready-made anchor for registry ↔ program linking.
- `ActionTrace.final_code` (`object_plan.py:341`) saves each part's final code and iteration count.
- `_get_ordered_part_script_paths()` + `_build_ordered_part_execution_code()` provide a ready-made mechanism for **part-level ordered execution**, and `_check_existing_construction()` provides part-level cache reuse.

**Parts that cannot be mapped directly**:
- Part code is a **free-form `bpy` Python string**; `PartShape` is only prompt input, with no structured parameter table / types / dependency graph / local coordinate system.
- `PartPlan.position` is **world coordinates**, not a Local Transform; `PartRelation` is not wired into deserialization (§2②).
- No CSG concept (`ShapeType` has only 6 basic shapes).
- No stable Component ID (`name` is an LLM-generated string, not guaranteed consistent across runs).
- `_generate_code_fix_from_error()` rewrites the entire part code; `run()` clears the scene at the start and replays everything.

**Judgment**: wrapping part-wise Blender Python into "an aDSL Parametric Component with structured parameters, semantic parts, local coordinate systems, and dependency relations" **cannot be done by automatic conversion**. An explicit code contract is needed (each part program exposes `build(params) -> Asset`, a parameter schema, a local origin, and output AABB), and the LLM must be required to generate against that contract. This is the main workload of PHASE 3/4, not a shuffling of JSON fields.

---

## 7. Deployment risk summary

| Risk | Evidence | Impact |
|---|---|---|
| **Self-contradictory environment versions** | `README.md:95` requires Blender **4.4.3 Linux**; `pyproject.toml:10` pins `bpy==4.5.4` | Two Blender versions coexist; the API of the `bpy` wheel and the standalone Blender may be inconsistent |
| **Leftover packaging configuration** | `pyproject.toml:62` `packages=["scenesmith"]`, `:76` `known_first_party=['scenesmith']` | `uv sync` / editable install may fail or install the wrong package name |
| **Linux-only dependencies** | `pyvirtualdisplay`, `drake`, the README's `blender-4.4.3-linux-x64.tar.xz`, bubblewrap-style isolation, `/path/to`-style paths | Native Windows deployment is basically infeasible; WSL2 is a must |
| **GPU / data volume** | torch 2.5 + CLIP + SAM3D + FLUX.2-klein-9B + V-HACD + ArtVIP/HSSD (72 GB-class) | Conflicts with the resource constraint of "no renting GPU servers, only the local Windows machine"; the only way is pure code generation + disabling retrieval/generation backends |
| **Two LLM stacks** | §3 | The DeepSeek adaptation needs two Provider Adapters, not a single `.env` |
| **Image generation is a hard dependency of the main strategy** | §5 #5 | Without code changes `code_generated` fails outright and the whole furniture stage cannot run |
| **`OPENAI_API_KEY` semantic overloading** | `image_generation.py:141-146` shares the same variable with the agent | After pointing it at DeepSeek, `images.generate` is bound to 404; the text LLM and image generation must be completely separated |
| **Network** | During this audit git/codeload/raw/jsdelivr were all unstable | `uv sync` pulling 78 MB of dependencies, the Blender 4.4.3 download, and HuggingFace checkpoints would all be affected |
| **No ready-made demo / complete tests** | `examples/` has only 1 CSV | "Getting it running" requires building a minimal entry point yourself; the example cannot be copied |
| **API key management** | `.env.example` has plaintext placeholders; `README.md:123` directly uses `export` | Must use `.env` + `.gitignore`; writing into code/logs/reports is forbidden (this audit wrote no real key) |

---

## 8. Preliminary differences from the existing aDSL

### 8.1 The real interface of the local aDSL (checked file by file in `E:/3DUse/aDSL/`)

| Task description | Actual state of the code |
|---|---|
| Four-level classes `Scene → Asset/Object → Component → Primitive` | **The core library has only one class, `Asset`** (`adsl-core/core/asset.py:43`); Scene is a conventional variable name `scene`, Component is an `Asset` child node, Primitive is a **dict** (`_primitives: List[Dict]`, `asset.py:49`). Independent `Scene/Component/Primitive` classes were **not found** |
| `attach_part` | ✅ Exists: `asset.py:62 attach_part(name, shape) -> Asset`; paired with `detach_part(:78)` |
| Cube / Sphere / Cylinder | ✅ `primitives.py:66/40/95`, plus `RoundedCube(:187)`, `ExternalMesh(:151)` |
| CSG Union/Difference/Intersection | ✅ `boolean.py:27/47/37` (functions, generating a `{"type":"boolean"}` primitive, executed on the Blender side by `export_glb.py:193 _apply_boolean`) |
| Stable Component ID | ⚠️ **Not on the core `Asset`**. The identifier is just the string key of `_children`. `component_id` exists only in the planning layer `adsl-agents/models.py:20 ComponentContract` |
| Local Transform / Geometry Metadata / Collision AABB | ⚠️ **The core `Asset` has none of these fields**. Local transforms live in the primitive dict's `xform` (4×4) and `Joint.origin`; AABB is computed at runtime (`bounds.py:315 shape_aabb`), not a persistent field. The planning layer's `ComponentContract` has `local_bounds` |
| `align_center` | Actually **`align_centers`** (plural, `layout.py:326`). Also `align_anchors(:92)`, `shape_anchor(:81)`, `distribute_along_axis(:179)`, `stack_shapes/grid_shapes/radial_shapes/place_on_axis/offset_from` |
| Constraints between components | ✅ `relations.py:56 RelationAssembly` (`box/span_box/rigid/attached/supported/within/bound/parameter/datum/compile`), `Expr`(:23) expressions + a Z-up AABB constraint solver. `compile()` **always returns a brand-new scene** (comment at `:61`) |
| Parametric Code | ⚠️ **There is no `ParametricCode` class**. `parametric_code` is just a string enum value of `generation_mode` at `models.py:22`. The real "parametric geometry program" is the **aDSL Python source file `source.py`**, executed by `runpy.run_path` |
| `asset_to_dict` | ✅ `serialize.py:6 asset_to_dict(asset) -> dict`, returning `{label, primitives, children, joints}`; `Asset.to_dict()` (`asset.py:519`) calls it. There is also the more complete `export_manifest.py:44/93` |
| Deserialization | ⚠️ **None in the core library**. In the whole project only `scripts/blender_worker.py:39 asset_from_json` |
| ExternalMesh | ✅ Only an external asset reference + an explicit AABB proxy (`primitives.py:151-184`); it does not generate meshes itself, consistent with the requirement that it "must not become a primary geometry method" |
| Blender Executor | Not "compile to Blender Python text", but `export_glb.py:455 export_glb(shape, ...)` directly calls `bpy.ops.mesh.primitive_*_add` to build objects; entry chain `adsl-agents/utils/execution.py:25 → asset_executor.py:25 (runpy.run_path) → export_glb` |
| **Incremental / local re-execution** | ❌ **Not found**. `export_glb.py:459 clear_scene=True` + `:468 read_factory_settings(use_empty=True)`; `blender_worker.py:190/193` clears likewise; `execution.py:47 output.mkdir(exist_ok=False)`; `asset_executor.py:29` runs the entire `runpy.run_path` every round. `EditPlan.patch_scope` is only planning text, and the "minimal patch" in `service.py` changes the `source.py` text, while execution still re-runs everything |

### 8.2 Structural blockers (impact on PHASE 3–5)

> **This is the most important finding of this audit; a decision is recommended before entering PHASE 3.**

The acceptance table of task PHASE 5 requires: `Localized Update — non-target geometry is not regenerated`, `Semantic IDs — non-target component IDs remain unchanged`, `do not pass the tests merely by modifying JSON and regenerating the whole scene`.

**But the existing aDSL Executor's execution model is a full rebuild** (`clear_scene=True` + `runpy.run_path` of the entire source). On the SceneCode side, `run()` likewise calls `_clear_blender_scene()` at the start and replays all part scripts. **Neither side has incremental re-execution capability**.

Therefore `adsl_adapter/` alone, doing only "ObjectPlan → aDSL AST conversion", cannot pass Test A/B/C. The following minimal extensions must be solved first:

1. **Stable IDs**: add a persistent `component_id` to the core `Asset` (currently only `_children` key names), and guarantee that `asset_to_dict` / `asset_from_json` round-trip without loss.
2. **Dependency graph and dirty marking**: record parameter/spatial dependencies between Components, and compute the affected closure on edit.
3. **Incremental executor**: support in `export_glb` / `blender_worker` "delete and rebuild the specified subtree by ID while leaving the other objects' `bpy` handles untouched", replacing `clear_scene=True`.
4. **Bidirectional serialization**: promote `asset_from_json` from `scripts/` to the core library as the read-back entry of the Source of Truth.
5. **Connect the relation layer and the execution layer**: `RelationAssembly.compile()` currently returns a brand-new scene every time; it needs to support local re-solving based on stable IDs (corresponding to Test B).

These 5 items are a **rework of the aDSL core execution model**, not work for the "Adapter" layer, and not coverable under "do not rewrite existing code at scale". There is tension between the PHASE 4 constraints and the PHASE 5 acceptance criteria, and the trade-off needs to be confirmed with the user first.

### 8.3 Preliminary feasibility of the SceneCode → aDSL mapping

| SceneCode | Target aDSL | Feasibility | Gap |
|---|---|---|---|
| `AssetItem` (paper AssetRequest) | Scene/Object semantic request | 🟡 | No `AssetRequest` class; `AssetItem` has only description/dimensions/strategies |
| `ObjectPlan` | Asset hierarchy | 🟡 | World coordinates, no stable IDs, no CSG, `PartRelation` not wired |
| `PartPlan` | Asset child node / Component | 🟢 Structure mappable | Needs stable IDs and a parameter schema |
| `PartShape` | aDSL Primitive | 🟡 | The 6 shapes are mappable, but `modifiers`/`custom` have no corresponding semantics |
| `position/rotation` | `xform` / Local Transform | 🔴 | World → local coordinates needs explicit transform derivation |
| `is_symmetric` / `symmetric_axis` | `distribute` / `align_anchors` / `Expr` | 🟡 | Symmetry constraint semantics must be defined; the existing `relations.py` can carry them |
| `material` | Component Material | 🟡 | The fields of aDSL's `appearance` module need checking |
| `instances` | `obj.copy()`-style component instance | 🟡 | aDSL has no explicit instance concept |
| `is_movable` + URDF | `Joint` / `attach_joint` | 🟢 | aDSL has an independent joint tree (`joints.py`), so **articulated capability can be kept**; this is good news |
| part-wise `.py` | Parametric Code | 🔴 | Free-form strings ≠ structured parametric programs; needs contract-based rework (§6) |
| `GeneratedGeometry` path fields | Component ID + Scene State | 🟡 | Path association must be upgraded to ID association |
| Stage-level `resume_from_path` / cache reuse | Local editing | 🔴 | Different granularity; not an equivalent substitute |

---

## 9. Audit conclusions

**Facts confirmed at the code level**:
1. The repository and paper really exist; SceneCode is a fork of SceneSmith + the new `code_object/agent_framework/`.
2. There are **two independent LLM call stacks** (OpenAI Agents SDK `gpt-5.4` / AsyncOpenAI `gemini-3.1-pro-preview`); the DeepSeek adaptation must handle them separately, and there are multiple OpenAI/Gemini-specific protocol parameters.
3. **Image generation is not an optional plugin that can simply be turned off**: the main strategy `code_generated` forces reference-image generation at the router layer; the fallback also falls back to Flux; the addon additionally has Hyper3D / Hunyuan3D / Sketchfab / Poly Haven; the wall_art path also depends on SAM3.
4. But the `Code_Object Pipeline` itself supports a `text_input` text-only mode: **the pure-code geometry route is technically viable**, at the cost of changing the call chain of the router and runner.
5. `ObjectPlan` is not aDSL: world coordinates, no stable IDs, no CSG, `PartRelation` not wired in, `sub_parts` single-level.
6. SceneCode has part-level program persistence and stage-level scene restoration, but **no component-level parametric local editing**.
7. The local aDSL has 10 discrepancies from the task description, the most critical being that there is **no incremental re-execution**, which directly blocks the three tests of PHASE 5.

**Explicitly unverified, not to be treated as done**:
- Any installation, any test, any `main.py` run, any Blender execution, any rendering, `house.blend`, multi-view images, actual DeepSeek calls and `/models` queries.
- Furniture quality in text-only mode (whether there are backrests/armrests/cushions/bases, whether it degenerates into stacked cubes).
- The real runnability of examples / configs / tests.

**Suggested next-step order** (following "reproduce first, adapt later"):
1. First solve the PHASE 1 environment feasibility: WSL2 + Python 3.11 + `uv` + Blender 4.4.3 (note the conflict with `bpy==4.5.4`), and fix `packages=["scenesmith"]` in `pyproject.toml`.
2. Before PHASE 2, **first run `tests/unit/test_default_asset_strategy_config.py`**, this no-GPU/no-LLM smoke test, as the minimal evidence that the environment works.
3. Before implementing the aDSL Adapter, first make an architecture decision on the incremental executor rework of §8.2; otherwise PHASE 5 cannot pass per the acceptance table.
4. The DeepSeek Provider Adapter must cover both stacks, and measure JSON output, tool calls, long text, and image input separately (required by task PHASE 1.2).

---

## Appendix A: Source files actually read in this audit (37)

```
.env.example, pyproject.toml, README.md, main.py(downloaded),
configs/config.yaml, configs/experiment/base_experiment.yaml,
configs/furniture_agent/base_furniture_agent.yaml,
scenecode/code_object/agent_framework/config.yaml,
  schemas/object_plan.py,
  agents/base_agent.py, agents/planner.py, agents/part_constructor.py, agents/planner_checker.py,
  pipeline.py, code_articulated_pipeline.py,
  utils/llm_client.py,
  blender/backend_manager.py, blender/headless_server.py, blender/addon.py,
  prompts/planner.md, prompts/constructor.md, prompts/checker.md, prompts/urdf.md,
scenecode/utils/openai.py,
scenecode/agent_utils/image_generation.py, flux_klein_worker.py,
  asset_registry.py, base_stateful_agent.py, checkpoint_state.py,
  asset_router/router.py, asset_router/dataclasses.py, room.py,
scenecode/furniture_agents/stateful_furniture_agent.py,
scenecode/experiments/__init__.py, base_experiment.py, indoor_scene_generation.py,
tests/unit/test_default_asset_strategy_config.py
```

## Appendix B: Key files read while checking the local aDSL

```
E:/3DUse/aDSL/adsl-core/core/asset.py, primitives.py, boolean.py, layout.py,
  bounds.py, transforms.py, relations.py, serialize.py, joints.py,
  export/export_glb.py, export/export_urdf.py, export/export_manifest.py,
E:/3DUse/aDSL/adsl-agents/models.py, utils/execution.py, utils/asset_executor.py,
  service.py, prompt/planner.md,
E:/3DUse/aDSL/scripts/blender_worker.py, scripts/test_relations.py,
E:/3DUse/aDSL/test_adsl_direct.py, test_blender_integration.py,
E:/3DUse/aDSL/outputs/chair.json, examples/physical_bookshelf/model.py
```

---

> This report contains no real API key and no runtime results or logs. All "success/usable" judgments are static code inferences, marked `[pending runtime verification]`.
