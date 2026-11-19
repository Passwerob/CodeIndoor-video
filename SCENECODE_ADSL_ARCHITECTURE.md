# SCENECODE_ADSL_ARCHITECTURE.md — Final Architecture (Direction-Corrected Version)

> Version: v2, 2026-11-08
> Basis for the correction: user instruction "adapt aDSL to the original SceneCode, not SceneCode to aDSL"
> Prerequisite documents: `SCENECODE_AUDIT.md`, `LOCAL_ENV_PROFILE.md`

---

## 1. Final direction

**aDSL is the only actively adapting party; SceneCode stays as-is and acts as an upstream black box.**

What this means concretely (written as verifiable clauses so that we do not slide back later):

| Constraint | Meaning |
|---|---|
| C1 | **Do not modify any SceneCode architecture/contract/business source**: `router.py`, `pipeline.py`, `part_constructor.py`, `image_generation.py`, `addon.py`, `base_stateful_agent.py` are all read-only |
| C2 | Do not require SceneCode to output structured parameters, local coordinates, stable IDs or CSG; it still produces `ObjectPlan.json`, `code/<part>.py`, `GLB/SDF`, `house_state.json` as usual |
| C3 | The aDSL extension itself **takes over the above artifacts as they are**: IDs reuse `PartPlan.name`, programs take over the part-wise `.py`, paths take over `GeneratedGeometry` |
| C4 | Differences in LLM / image generation / protocol are all absorbed in a **bridge layer outside SceneCode** |
| C5 | The only in-repo change allowed is leftover packaging metadata (`packages=["scenesmith"]` in `pyproject.toml`), with no change to any business semantics |

### Differences from the audit version's recommendations (explicitly withdrawn)

| Audit version (v1) recommendation | v2 handling |
|---|---|
| Modify `router.py:1411 _prepare_code_object_inputs` to remove reference-image generation | ❌ **Withdrawn**. Instead, the local compatibility gateway in §3 takes over `images.generate` |
| Modify `router.py:1511/1552 generate_from_image → generate_from_text` | ❌ **Withdrawn**. SceneCode keeps using image mode, and the gateway returns a procedural reference image |
| Modify the `fallback_backend` config + handle `image_generator is None` | ❌ **Withdrawn**. The gateway guarantees `images.generate` never fails, so the fallback is never triggered |
| "ObjectPlan → aDSL AST" implied that upstream output would be translated/taken over | ✅ Changed to "aDSL ingests existing upstream artifacts", see §4 |
| Add a Provider Adapter inside SceneCode to adapt two LLM stacks | ❌ **Withdrawn**. Protocol translation is unified in the gateway, see §3 |

---

## 2. Source of Truth conflict (must be resolved head-on)

The original task requires: **aDSL is the persistent, semantic, structured, executable Source of Truth**, and "the Scene State Registry must correspond to aDSL's stable IDs and component hierarchy; maintaining two mutually contradictory scene states is not allowed".

But C1/C2 require SceneCode to be unchanged, and it still maintains its own `house_state.json`, `AssetRegistry`, `GeneratedGeometry`. **If both sides stay alive, that is two states.**

### Solution: phase isolation + one-way projection, not two-way sync

```
┌─ Generation phase ─────────────────────────────────────┐
│ SceneCode runs as-is (upstream source of facts)         │
│ Brief → Planner/Designer/Critic → AssetItem → 5 strategies │
│      → ObjectPlan.json + code/<part>.py + GLB/SDF       │
│      → house_state.json                                 │
└──────────────────────┬─────────────────────────────────┘
                       │ one-way ingestion (read-only, no write-back)
                       ▼
┌─ Ingestion phase ──────────────────────────────────────┐
│ aDSL reads upstream artifacts → compiles to aDSL AST    │
│  · component_id = PartPlan.name / code file name        │
│  · ProgramComponent takes over part-wise Blender Python │
│  · house_state.json is frozen as upstream_snapshot.json │
│    (immutable, with sha256 + timestamp + lineage)       │
└──────────────────────┬─────────────────────────────────┘
                       │
                       ▼
┌─ Editing phase ────────────────────────────────────────┐
│ aDSL AST = the only active Source of Truth              │
│ Localized Edit → dirty marking → incremental execution → Render → Critic │
│ SceneCode-side state is no longer updated or read       │
└─────────────────────────────────────────────────────────┘
```

**Why this satisfies "no two contradictory states"**: SceneCode's state is demoted to a **read-only historical snapshot** (`upstream_snapshot.json`), not a parallel active state. At any moment there is only one writable SoT: SceneCode in the generation phase, aDSL in the editing phase. The two are distinguished explicitly by the `phase` field and `lineage_id`, not by real-time sync.

**Costs (must be accepted and written into the reproduction report)**:
- After entering the editing phase, SceneCode's native `resume_from_path`, checkpoint rollback and room-level critique are unavailable.
- The editing-phase `Render → Visual Critic → Repair` loop must be **re-wired on the aDSL side** (render the aDSL execution result → DeepSeek Flash visual review → generate aDSL localized edit instructions); SceneCode's Critic cannot be reused.
- "Regenerate the whole room" = explicitly return to the generation phase → re-ingest → invalidate the old AST and record the lineage. It is not a seamless switch.

> If what the user wants is "SceneCode and aDSL in real-time two-way sync, sharing one state", that directly conflicts with C1 and needs a fresh decision — **it cannot be covered up by adding an Adapter**.

---

## 3. Core mechanism: local compatibility gateway (keeping SceneCode at zero changes)

This is the key to "the aDSL/bridge layer adapts to SceneCode", and it solves three things at once: disabling image generation, protocol differences between the two LLM stacks, and API key isolation.

### 3.1 Topology

```
SceneCode (as-is, OPENAI_API_BASE=http://127.0.0.1:<port>/v1)
   │  · openai-agents (gpt-5.4)         → /responses or /chat/completions
   │  · AsyncOpenAI (gemini-3.1-pro)    → /chat/completions
   │  · image_generation.py             → /images/generations, /images/edits
   ▼
┌──────────────────────────────────────────────────────────┐
│ bridge/ local OpenAI-compatible gateway                   │
│  ├─ Text requests → parameter translation → official DeepSeek API │
│  │    · strip OpenAI-specific fields such as reasoning_effort / verbosity / service_tier │
│  │      / vision_detail                                   │
│  │    · map model name → deepseek-flash                   │
│  │    · keep tools / tool_calls / JSON output             │
│  │    · pass reasoning_content through in thinking mode   │
│  │    · strip the injected Gemini-3 thought_signature     │
│  │  · Image requests → ProceduralReferenceGenerator (local) │
│  │    · never call FLUX / SAM3D / TRELLIS / OpenAI images │
│  │    · generate procedural reference images with local Blender/PIL │
│  │  · the key lives only in the gateway process memory, never in logs/reports/repo │
│  │  · record sanitized call logs (model, tokens, latency, no key) │
└──────────────────────┬───────────────────────────────────┘
                       ▼
              https://api.deepseek.com
```

### 3.2 Why this disables image generation without changing SceneCode

The audit confirmed that `router.py:1394-1420` unconditionally calls `image_generator.generate_images(...)`, which ends up at `image_generation.py:190 client.images.generate(...)` (backend defaults to `openai`). The gateway intercepts `/images/generations` and returns a **locally procedurally generated reference image**; SceneCode's `image_path` remains valid, `generate_from_image()` runs as usual, and `fallback_backend: "flux-klein"` is never triggered.

Reference-image generation strategy (by priority, all local, no GPU model needed):
1. Use existing aDSL primitives + local Blender to render a **neutral line-drawing / block diagram** of the object (best fits the "code-driven" principle);
2. Degrade to a PIL-drawn solid-color/outline placeholder with the object name and dimensions annotated;
3. Either way, encode `AssetItem.description` / `dimensions` into the image metadata for the Planner to read.

`[to be verified by running]`: **whether the part richness of the ObjectPlan produced by the PlannerAgent drops without a real reference image** (whether it degenerates into a few cubes). This is the risk most in need of measurement in PHASE 2, and success cannot be assumed.

### 3.3 Protocol differences the gateway must handle (from audit §3)

> **Corrected against real measurements at 2026-11-08 21:4x**, see `DEEPSEEK_PROBE_REPORT.md`. Several "must strip/clean" assumptions in the first draft were overturned; the table below follows the server's real responses.

| Source | Parameter / behavior | Measured | Gateway handling |
|---|---|---|---|
| `base_furniture_agent.yaml:296-316` | `reasoning_effort`, `verbosity` | **200, ignored** | Pass through by default; strip only with `STRICT_PARAMS=1` |
| `configs/config.yaml:11`, `:289` | `service_tier` | **200, ignored** | Same as above |
| `base_furniture_agent.yaml:292` | `vision_detail: high` | **200, ignored** | Same as above |
| `llm_client.py:193-234` | Gemini-3 `thought_signature` injection | **200, ignored** | No forced cleanup needed |
| `llm_client.py:265-277` | `temperature` + thinking | **200** (merely ineffective) | No stripping needed |
| `llm_client.py:265-277` | `tool_choice:"required"` + thinking | **400** `Thinking mode does not support this tool_choice` | **The only forced rewrite**: downgrade to `"auto"` |
| `llm_client.py:265-277` | `max_completion_tokens` alias | **200** | Pass through |
| `openai-agents>=0.6.4` | Responses API `POST /responses` | **200**, standard `object:"response"` | **Pass through directly**, no need to translate to chat/completions |
| `OPENAI_TRACING_KEY` | OpenAI Agents tracing | — | Turn tracing off, or have the gateway return an empty success, sending no external requests |
| `image_generation.py:141-146` | Shares `OPENAI_API_KEY` with the text LLM | — | The gateway routes by path; the key applies only to text requests |
| `image_generation.py:1035` | Reads `response.data[0].b64_json` | — | `/images/*` returns `{"data":[{"b64_json":…}]}` with a locally procedural PNG |

**Conclusion**: the gateway is much simpler than the first draft imagined — the two text paths are essentially **pass-through + model name mapping**, and the only mandatory rewrite is the combination of `tool_choice:"required"` with thinking. It is implemented and passes the 7/7 smoke test.

### 3.4 Injection points outside the gateway (fallback)

If some `openai-agents` calls bypass `OPENAI_API_BASE` (for example hard-coded inside the SDK), use `sys.modules` injection or factory-function replacement in `bridge/entrypoint.py` as a backstop, **still without changing the SceneCode repo source**. All injection happens only in the standalone launch script.

---

## 4. aDSL-side modification route (the main body of "modifying aDSL")

### 4.1 Done (this round, with tests)

| Capability | Implementation | Verification |
|---|---|---|
| Stable Component ID | `Asset.component_id` (`kw_only`, appended at the end, breaking no existing positional-argument construction); `attach_part` automatically binds the **part key** as the ID; `copy()` preserves it | 10 new tests all green |
| ID lookup / dependency location | `component_ids()` / `find_component()` / `component_path()` / `effective_component_id`, duplicate IDs fail early | Same as above |
| Two-way serialization | `asset_to_dict` adds `component_id` (old readers can simply ignore it); the core library adds `asset_from_dict`; `Asset.from_dict()` | Same as above |
| Backward compatibility | Old `outputs/*.json` (without component_id) can be restored and get derived stable IDs | Same as above |
| Regression | `test_adsl_direct.py` passes; `scripts/test_relations.py` 14 items OK | Same as above |

**This step directly serves C3**: the ID is not a UUID invented by aDSL, but SceneCode's `PartPlan.name` / `code/<part>.py` file name. Upstream does not need to change a single word.

### 4.2 To be implemented (in order, each with tests)

| # | Capability | PHASE 5 acceptance item it addresses |
|---|---|---|
| P1 | `ProgramComponent`: a new aDSL component type that takes over SceneCode's part-wise Blender Python (including a `params` schema, local origin, output AABB, source file path) | Program Persistence |
| P2 | Dependency graph + dirty marking: record parameter/spatial dependencies based on `component_path()`, and compute the affected closure on edit | Localized Update |
| P3 | **Incremental executor**: change `export_glb` / `blender_worker` to support deleting and rebuilding a given subtree by `component_id` while leaving the other `bpy` objects' handles untouched (replacing `clear_scene=True`) | Localized Update / Semantic IDs |
| P4 | Local re-solving in the relation layer: `RelationAssembly.compile()` currently always returns a brand-new scene; it must support local re-solving by stable ID | Relation Preservation / Test B |
| P5 | Scene state layer: three-way consistency check `aDSL AST ↔ Registry ↔ Blender` (keyed by `component_id`) | State Consistency |
| P6 | Ingester: read `ObjectPlan.json` + `code/<part>.py` + `house_state.json` → aDSL AST + `upstream_snapshot.json` | Program Persistence / SoT |

> P3 is the hard part. `SCENECODE_AUDIT.md` §8.2 explained that the current Executor is `clear_scene=True` + `runpy.run_path`, a full rebuild. Without changing it, "non-target geometry is not regenerated" in Test A/B/C cannot pass. This is a **change to aDSL's core execution model**, which falls under the "needs modification to aDSL" the user confirmed this round, and does not conflict with C1.

### 4.3 Preserving articulated capability

aDSL has a separate `Joint` / `attach_joint` kinematic tree (`core/joints.py`), independent of the `_children` geometry tree. SceneCode's `is_movable` + `URDFPipeline` artifacts can be mapped onto `Joint`, so **introducing aDSL does not lose articulated-object capability**. This is one of the few pieces of good news in the audit; the ingester must handle the `URDF/SDF → Joint` mapping explicitly.

---

## 5. Target execution chain (corrected)

```
Natural Language Brief
   ↓  SceneCode Planner/Designer/Critic (as-is, via the gateway to DeepSeek)
AssetItem / AssetRequest
   ↓  AssetRouter 5 strategies (as-is, reference images generated locally by the gateway)
ObjectPlan.json + code/<part>.py + GLB/SDF + house_state.json
   ↓  ── one-way ingestion (aDSL adapts actively, no write-back) ──
aDSL AST / Component Tree  ★ the only SoT (editing phase)
   ↓  ProgramComponent compilation + aDSL incremental executor
Blender Python (execution artifact, not the only editable representation)
   ↓  Blender Scene (objects managed by component_id)
Render → DeepSeek Flash visual review
   ↓  Localized aDSL Edit (change only the target component's parameters/relations/program)
Incremental Re-execution (rebuild only dirty components)
   ↓
Updated .blend + aDSL AST + Registry (all three aligned by component_id)
```

---

## 6. Environment adaptation summary (see `LOCAL_ENV_PROFILE.md` for details)

| Constraint | Response |
|---|---|
| WSL2 disabled + `wsl.exe` blacklisted | Native Windows path; no bypassing of security policy |
| No Python 3.11 | Managed by `uv python install 3.11`, separate venv, **do not touch the existing 3.13 / aDSL venv** |
| Three-way conflict between Blender 5.2.2 / 4.4.3 / `bpy 4.5.4` | Isolate the three: aDSL uses 5.2.2; SceneCode uses a separate 4.4.3 Windows portable; `bpy` is only a Python module dependency |
| GPU 16 GB | Pure code geometry + EEVEE rendering; disable all generative/retrieval backends (SAM3D/FLUX/CLIP/HSSD) |
| C drive has 24 GB left | All venv/cache/Blender/output on the E drive, with `UV_CACHE_DIR` etc. set explicitly |
| pydrake on Windows | `[to be verified]`; if unavailable, explicitly cut SDF simulation export and record it as "not reproduced due to environment" |
| API key | Only in gateway process memory + `.env` (already gitignored), never written into any code/log/report |

---

## 7. Trade-off list (the user needs to know; not hidden in the implementation)

1. **aDSL is not a mirror kept in real-time sync with SceneCode**, but the only SoT in the editing phase; SceneCode state is frozen as a read-only upstream snapshot.
2. **After entering the editing phase, SceneCode's native rollback/Critic is given up**, and the visual review loop is re-wired on the aDSL side.
3. **Reference images are procedurally generated, not real images**; this may affect ObjectPlan part richness, `[to be verified by running]`, and equal quality to the paper cannot be assumed.
4. **SDF/Drake simulation export may be unavailable on Windows**; pure code geometry + `.blend` take priority.
5. **The P3 incremental executor changes aDSL's core execution model**, which is in tension with the task's constraint "no large-scale rewrite of existing code"; this round proceeds per the user-confirmed "needs modification to aDSL", but changes are kept localized + full regression tests.
6. Any item above that has not been measured will not be written as "done".

---

## 8. Current status (honestly labeled)

| Item | Status |
|---|---|
| PHASE 0 audit | ✅ Done (`SCENECODE_AUDIT.md`) |
| Local environment profile | ✅ Done (`LOCAL_ENV_PROFILE.md`) |
| Architecture direction finalized | ✅ This document |
| aDSL stable ID + two-way serialization + tests | ✅ Landed and running (10 new + 14 existing regression) |
| Mapping table | ✅ `SCENECODE_ADSL_MAPPING.md` |
| Local compatibility gateway | ✅ Implemented and run 9/9 (`bridge/gateway.py`, including SSE / retry / Responses form) |
| DeepSeek `/models` query and protocol measurement | ✅ Done (8 capabilities + 7 boundaries, see `DEEPSEEK_PROBE_REPORT.md`) |
| Whether procedural reference images degrade the ObjectPlan | ✅ Ruled out by measurement (two rounds of 15–17 parts, no degradation) |
| P3 incremental executor | ✅ Implemented; Test A/B/C all PASS in a real run on the local Blender 5.2.2, producing `.blend` + rendered images |
| P6 one-way ingestion (ObjectPlan → aDSL AST) | ✅ Structural skeleton implemented; 2 real ObjectPlans ingested successfully, IDs preserved in round trip |
| P1 ProgramComponent / single-object geometry | ⚠️ **Direction corrected**: the bare-`bpy` generation path (`agentic_coder.py` / `agentic_pipeline.py`) is marked DEPRECATED and kept only as run evidence. Root cause in `ROOT_CAUSE_ANALYSIS.md`: bypassing aDSL's parameterized factories + a non-blocking bystander Critic. Now changed to: **the LLM only outputs a structured plan → FineScene `FACTORIES` / existing aDSL assets compile it deterministically → per-object / whole-scene `review_images` hard gates** |
| **SceneCode scene organization × aDSL single-object generation** | ✅ **Wired up and run for real**: 14-object five-stage layout (validation 0 violations / Critic 2 rounds / best version kept) → poses stamped onto 13 existing aDSL objects + 1 `build_desk` factory part → 14 objects / 137 independent parts / 4-view interior renders / `.blend`. See `SCENECODE_LAYOUT_ADSL_REPORT.md` |
| Localized editing (aDSL as editing-phase SoT) | ✅ **Verified**: following the whole-scene QA's blocking finding, only the yaw of 2 armchairs was changed, and the AST SHA-256 of the other 12 components all stayed the same; after re-rendering, `needs_revision → passed / accepted=True`. FineScene's built-in `isolated_part_edit` / `grouped_object_edit` are also all green |
| P2 / P4 / P5 | 🟡 Partial: hash-level preservation of non-target parts verified; dirty-marking closure, automatic re-solving of relation constraints, and automatic three-way state reconciliation are still unimplemented |
| Separate environment (uv / Python 3.11 / Blender 4.4.3) | ⬜ Not installed (per the user-confirmed order, deferred until SceneCode itself needs to run) |
| PHASE 2 native reproduction | ⬜ Not executed (this round replicated the planning layer "per SceneCode's real contract", and did not run the native pipeline end to end) |
| Full living-room `.blend` / multi-view renders | ✅ Produced (`runtime/outputs/living_room/interior{,_edited}/`), but the geometry is FineScene's existing simplified parametric assets, not PBR-refined |
| PHASE 5 three edit tests on the full living room | 🟡 Component-level localized editing (class A) verified; relation-level / program-level not yet run separately on the full living room |

> See `STAGE1_RESULTS.md` for details. **Mechanism verification is not a full reproduction**; the two are not conflated.

---

## 9. Corrected implementation order (user-confirmed, 2026-11-08 21:31)

The user pointed out: **the separate environment should be installed only once the code is written and testing begins**. Adopted, and the order is corrected accordingly (replacing the approach in §8 of listing "install the environment or not" as a pending item).

### Stage 1 — Independent of the separate environment (can be done now, using the existing aDSL venv / Python 3.13.4)

1. **DeepSeek `/models` query + protocol measurement** (JSON output / tool calls / long text / image input) — this item must be **moved up**: the gateway must be written according to the models and endpoint shapes actually available to the account, not guessed from documentation.
2. The `bridge/` local compatibility gateway (pure Python, separate process).
3. The P1–P6 aDSL changes (including the **P3 incremental executor**) + unit tests.
4. P3's Blender execution verification **uses the Blender 5.2.2 already on this machine directly**, without waiting for 4.4.3 — the aDSL-side executor depends only on the basic `bpy` modeling API.

### Stage 2 — Install the separate environment (only at this moment, and only when SceneCode itself is to be run)

`uv` → managed Python 3.11 → separate `.venv-scenecode` → Blender 4.4.3 Windows portable → `uv sync`.

### Stage 3 — PHASE 2 reproduction

Run the SceneCode pipeline as-is, producing `.blend`, multi-view renders, sanitized logs, ObjectPlan, part programs and state files.

### Stage 4 — Ingestion + integration tests → PHASE 5 three edit tests

**Rationale**: the network on this machine is unstable (codeload throttled to ~25 KB/s, git 502), `uv sync` includes several extensions that need compiling, and the availability of `pydrake` on Windows is unverified. Installing too early would only leave a half-installed environment, and might stall the main line on a dependency-resolution failure.

**Distinguish two things**: the `runtime/` directory skeleton, `.env.template`, `env.sh` and `.gitignore` are a **configuration contract** (paths must be aligned while writing code), so generating them early is right; they are not "installing the environment" and do not conflict with Stage 2.

### SoT scheme explanation (user inquiry, 2026-11-08 21:31)

The difference between "phase isolation / one-way projection" and "real-time two-way sync", illustrated with Test A (chair back −5cm):

| | Phase isolation (recommended) | Real-time two-way sync |
|---|---|---|
| Writable states at any moment | **1** | 2, which must always agree |
| Whether edits are written back upstream | Not written back, the snapshot is read-only | Must change `ObjectPlan.json`, `code/backrest.py`, `house_state.json`, GLB/SDF etc. — 4+ places — in sync |
| Whether SceneCode must change | No | **Yes** (it has no component-level parameters/stable IDs/local coordinates, so the state structure must be extended + conflict handling added) |
| How it fails | Regenerating the whole room requires explicitly returning to the generation phase | If any place misses a sync, `resume_from_path` / critique rollback will read the old chair back and overwrite the edit |
| Against the "SceneCode zero changes" constraint | ✅ Compatible | ❌ Direct conflict |

An extra benefit of phase isolation: the frozen `upstream_snapshot.json` is exactly a ready-made baseline for PHASE 5 "before/after comparison / non-target component IDs unchanged".

**User confirmed (2026-11-08 21:35): adopt phase isolation / one-way projection.** C1 (zero changes to SceneCode business source) stays unchanged; the real-time two-way sync scheme is void and no longer an alternative. All later implementation follows "generation phase SceneCode source of facts → ingestion phase frozen snapshot → editing phase aDSL as the only SoT".
