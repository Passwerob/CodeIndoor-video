# STAGE1_RESULTS.md — Stage 1 Measured Results Summary

> 2026-11-08 22:0x (GMT+8)
> Instruction basis: user said "Just test it, fill everything in, as originally envisioned"
> Principle: all conclusions come from **real runs**; failures are recorded honestly and traced to a fix; inferences are not written up as successes.

## 1. Overview of this round's measurements

| # | Item | Result | Evidence |
|---|---|---|---|
| 1 | Smoke test after completing the gateway | **9/9 PASS** | `bridge/test_gateway.py` |
| 2 | ObjectPlan quality risk (whether procedural reference images cause degradation) | **No degradation**, 15–17 parts in each of two rounds | `OBJECTPLAN_PROBE_RESULT.json` |
| 3 | P3 incremental executor + real Blender run | **Test A/B/C all PASS** | `runtime/outputs/incremental_test/report.json` |
| 4 | Blender artifacts | `.blend` 96 KB + rendered image 168 KB | See §4 |
| 5 | P6 one-way ingestion (real ObjectPlan → aDSL AST) | **2/2 succeeded**, IDs preserved in round trip | `runtime/outputs/ingested/` |
| 6 | aDSL regression | **10 + 14 all green** | `test_adsl_stable_ids.py` / `scripts/test_relations.py` |
| 7 | Key-leak re-check | **Clean**, only `runtime/.env` (already gitignored) | grep over the whole directory |

## 2. Gateway completion (9/9)

On top of v1, added: upstream 502 backoff retry (3 times), `stream:true` SSE chunk-by-chunk pass-through, handling of the object form of Responses `tool_choice`, and an `/images/edits` fallback.

| Test | Result |
|---|---|
| `GET /v1/models` | PASS, returns `deepseek-flash` / `deepseek-v4-pro` |
| chat with `gpt-5.4` + all OpenAI-specific parameters | PASS 200, mapped to `deepseek-flash` |
| `tool_choice:required` + thinking forced rewrite | PASS 200 (upstream originally returned 400) |
| `POST /v1/responses` | PASS 200, `object:"response"` |
| `/v1/images/generations` → local PNG `b64_json` | PASS, PNG magic number checked |
| `/v1/images/edits` → local PNG | PASS |
| 404 guard for unsupported paths | PASS |
| **`stream:true` SSE pass-through** | PASS, `text/event-stream`, 4 chunks |
| **Responses `tool_choice` object form** | PASS 200 |

## 3. Real failures and fixes along the way (not hidden)

| Failure | Real cause | Fix |
|---|---|---|
| Protocol probe: 6 of 7 items `-1 transport_error` | The local proxy tunnel returned `502 Bad Gateway`; requests never reached the server | Added backoff retry to the probe; transport failures are not recorded as API conclusions |
| Gateway test `responses tool_choice` returned 422 | DeepSeek `/responses` has `tool_choice.type` enum `function`/`web_search`, and **does not accept OpenAI's `required`** | Delete the OpenAI-style variant per path and let the server use its default; 9/9 pass |
| ObjectPlan probe JSON parse failed (twice) | `max_tokens=3000` truncated the 8–13K-character output | Raised to 6000; both runs `finish_reason=stop` and strict parsing succeeded. Also added regex fallback counting, to prevent malformed JSON being counted as success |
| Gateway smoke item 7 assertion was always `True` | A fake test I wrote myself | Changed to a real request to `/audio/transcriptions` verifying 404 |
| Ingester `ModuleNotFoundError: numpy` | Ran with system Python, but aDSL core depends on numpy | Switched to `E:/3DUse/aDSL/venv/Scripts/python.exe` (which confirms the need for environment isolation) |

## 4. ObjectPlan quality probe conclusion (answering "just test it")

Using the output contract of SceneCode `prompts/planner.md`, calling `deepseek-flash` through the gateway, comparing two kinds of input. **Two independent runs** (model output is random):

| Run | Text only | Procedural reference image + text |
|---|---|---|
| First | 16 parts / 7 part_types / cube 11 + cylinder 4 + custom 1 / 6 sub_parts / 26 modifiers | 17 parts / 6 types / cube 10 + cylinder 7 / 2 sub_parts / 23 modifiers |
| Second | 15 parts / cone 4 + cube 10 + torus 1 / 22 modifiers | 15 parts (including piping_seam) |
| **Degradation verdict** | **False** | **False** |

**Conclusion**: the procedural reference image did **not** make the ObjectPlan degenerate into a few cubes; part count and shape diversity are on par with text only. The risk is ruled out by measurement.

**Qualification**: this is a capability probe, not a reproduction of the full SceneCode pipeline — the real `PlannerAgent` also has a 20 KB full prompt and the PlannerChecker's multi-round `check_and_fix` repair. It cannot be used to claim that PHASE 2 reproduction is complete.

## 5. P3 incremental executor (real run on local Blender 5.2.2)

`adsl_adapter/incremental_executor.py` reuses the aDSL core's `asset_from_dict` and `core.export.export_glb._build_shape` (via `importlib.import_module`, because `import ... as` would be overridden by package attributes). Each component is stamped with a **UUID build token** when built; the token of geometry that was not rebuilt stays unchanged, which proves "non-target geometry was not regenerated" more strictly than comparing object names.

`adsl_adapter/test_incremental_blender.py` results (17 objects, count unchanged before/after editing, component_ids preserved):

| Test | Behavior | Verification |
|---|---|---|
| A component-level edit (lower the chair back by 5cm) | Delete/rebuild only the 2 objects of `backrest` | Target token changed, non-target violations `[]` → PASS |
| B relation-level edit | Move only the 2 objects of `seat`, no geometry rebuild | Target token **kept**, matrix changed, non-target violations `[]` → PASS |
| C program-level symmetric edit (widen the armrests) | Rebuild only `armrest_left` + `armrest_right` | Both tokens changed, non-target violations `[]` → PASS |

Artifacts: `chair_incremental.blend` (96 KB), `chair_after_edits.png` (168 KB, visually confirmed that the backrest/armrests/seat/four legs are visible), `report.json`.

**Qualification**: this is a **mechanism verification on a single chair**, not PHASE 5's full living-room scene test; Test B is currently "move the component" and is not yet connected to `RelationAssembly`'s constraint re-solving (P4).

## 6. P6 one-way ingestion (aDSL actively adapts to SceneCode)

`adsl_adapter/ingest_objectplan.py` converts two **really generated** ObjectPlans into an aDSL AST:

- `component_id` is taken directly from `PartPlan.name` (such as `back_cushion_1`, `leg_front_left`), with no UUIDs made up;
- 15 parts each → 16 stable IDs (including the root), and all IDs are preserved in the `asset_to_dict → asset_from_dict` round trip;
- Outputs `scene_a2.json` / `scene_b2.json` + `.upstream.json` (upstream manifest: dimensions, material, symmetry, movability, original pose) + `.ingest_report.json`;
- `cone` / `torus` are honestly recorded as `unmapped_shapes`, **not silently downgraded to Cube** — the real geometry should be taken over by P1's `ProgramComponent` using the part-wise `code/<part>.py`; the ingester only builds the structural skeleton.

## 7. Artifact inventory

```
SceneCode_audit/
├─ STAGE1_RESULTS.md                    (this document)
├─ DEEPSEEK_PROBE_REPORT.md             (capability + protocol measurements)
├─ OBJECTPLAN_PROBE_RESULT.json         (sanitized statistics)
├─ OBJECTPLAN_SAMPLE_A.json / _B.json   (real ObjectPlans)
├─ DEEPSEEK_PROBE_RESULT.json / DEEPSEEK_COMPAT_RESULT.json
├─ bridge/
│   ├─ gateway.py                      (local compatibility gateway)
│   ├─ probe_deepseek.py / probe_compat.py / probe_objectplan.py
│   └─ test_gateway.py                 (9/9)
├─ adsl_adapter/
│   ├─ incremental_executor.py         (P3)
│   ├─ test_incremental_blender.py     (A/B/C)
│   └─ ingest_objectplan.py            (P6)
├─ tests/test_adsl_stable_ids.py       (10/10)
└─ runtime/outputs/
    ├─ incremental_test/  chair_incremental.blend, chair_after_edits.png, report.json
    ├─ ingested/          scene_a2.json, scene_b2.json, *.upstream.json, *.ingest_report.json
    └─ gateway_calls.jsonl             (sanitized)
```

## 8. Still incomplete (honestly labeled, not claimed as filled in)

| Item | Status | Reason |
|---|---|---|
| Separate environment (uv / Python 3.11 / Blender 4.4.3 / `uv sync`) | ⬜ Not installed | Per the user-confirmed order, it should be installed only once the code is written and SceneCode itself is to be run; the network on this machine is unstable and `pydrake` on Windows is unverified |
| **PHASE 2 native reproduction** (full living room, five stages, `.blend`, multi-view renders, sanitized logs) | ⬜ Not executed | Depends on the separate environment |
| P1 `ProgramComponent` (parameter schema / local origin / AABB / dependencies taking over the part-wise `.py`) | ⬜ Not implemented | The ingester currently only builds the structural skeleton |
| P2 dependency graph + dirty marking (closure based on `component_path()`) | ⬜ Not implemented | P3 currently has the caller specify the dirty components explicitly |
| P4 `RelationAssembly` local re-solving | ⬜ Not implemented | Test B currently only does pose moves |
| P5 aDSL ↔ Registry ↔ Blender three-way consistency check | ⬜ Not implemented | — |
| PHASE 5 three edit tests on the full living room + before/after multi-view comparison | ⬜ Not executed | Depends on P1–P5 + the PHASE 2 scene |
| `SCENECODE_REPRO_REPORT.md` | ⬜ Not written | PHASE 2 has not been run, so no reproduction conclusion can be written |

**"Fill everything in" has so far covered all the verifiable parts of Stage 1** (gateway, protocol measurement, risk measurement, P3 mechanism, P6 ingestion skeleton), not the completion of all of PHASE 1–6. Per the task discipline, mechanism verification is not passed off as a full reproduction.

## 9. Next-step priorities

1. **P1 ProgramComponent** — have the ingester attach real part-wise programs, so that the AST gets geometry.
2. **P2 + P4** — dirty-marking closure + local relation re-solving, upgrading Test B from "manual move" to "automatic solving after changing a constraint".
3. Install the separate environment → PHASE 2 native reproduction → run PHASE 5's three edit tests with the real living room.

---

## 10. Addendum (22:5x): turning "a lump" into truly fine furniture

The user pointed out that `chair_after_edits.png` is just a few boxes put together, which conflicts with the task's "do not simplify fine furniture into a few cubes". **Acknowledged**: that was placeholder geometry for the P3 mechanism verification, used only to prove incremental execution, and does not represent final quality.

### The fix: P1 ProgramComponent, not adding bevels to boxes

| New file | Purpose |
|---|---|
| `adsl_adapter/program_component.py` | Structured component: `component_id` / `program` (**a program reference name, not a code string**) / typed and ranged `ParamSpec` schema / `values` / `local_origin` / `depends_on` / `upstream_refs` / `revisions`. Pure Python, no bpy, can be unit-tested outside Blender |
| `adsl_adapter/part_programs.py` | 4 parameterized Blender builders: `rounded_panel` (bevel + weighted normals), `upholstered_cushion` (subdiv + normal puffing + curved seam stitching + procedural fabric micro-texture), `tapered_leg` (tapered wooden leg + metal foot cap), `piping_seam` |
| `adsl_adapter/run_fine_sofa.py` | Read the 15 parts of the real `scene_a2.upstream.json` → generate ProgramComponents → attach to the P3 executor → lighting/floor/camera → 3 views each before and after rendered → localized edit → save `.blend` + `program_ast.json` |

`IncrementalBlenderExecutor` gained a `build_fn` callback and `build_initial_from_specs()`, so it **reuses P3's incremental mechanism** with the geometry source swapped for parameterized programs, and the original `test_incremental_blender.py` is unaffected.

### Visual tuning process (all really run, all images inspected)

1. First version: the sofa structure was recognizable but **overexposed**, the fabric washed out to white and the stitching invisible → lowered world light / three-light energy, darkened fabric and frame colors, `Standard` + medium-high contrast, thickened stitching.
2. Second version: layers were clear but the fabric was too light → pressed exposure down another notch, gave the fabric a procedural Noise→Bump micro-texture, darkened the floor.
3. Final: blue-gray fabric / dark gray frame / wood-colored base / four tapered legs / three back cushions / three seat cushions / rounded armrests / stitching are all distinguishable.

### Final result (Blender 5.2.2, EEVEE)

```
component_count = 15      object_count_initial = 25      ids_preserved = True
test_a (backrest -5cm)   PASS  violations=0  target_rebuilt=True   (only deleted/rebuilt 2 objects)
test_b (relation edit)     PASS  violations=0  token_kept=True matrix_changed=True
test_c (armrest +3cm)    PASS  violations=0  targets_rebuilt=True  (rebuilt only the left and right armrests)
renders = 6 (before/after × front_45 / side / top_3q)
```

Artifacts: `runtime/outputs/fine_sofa/fine_sofa.blend`, 6 PNGs, `program_ast.json` (structured AST, **containing no code strings**), `report.json`. Before/after renders visually confirm that the middle backrest got lower and the armrests wider, while the seat cushions/wooden base/legs are unchanged.

### Honest qualifications (the parts that must be said)

- These part programs are a **parameterized reference implementation I wrote by hand**, used to prove the chain "an aDSL component can carry a structured parameterized program + incremental editing + fine geometry".
- They are **not part-wise Blender Python generated by DeepSeek**. The task's core Agentic Coding (LLM generates program → execute → feed errors back to repair) **is still not implemented**; this is the real remaining work of P1, requiring wiring in the gateway + `execution-guided repair`.
- There is still room for visual improvement: no real fabric texture/normal map, the cushion surface is rather smooth, and the floor is rather bright; this is a procedural reference implementation, not paper-grade asset quality.
- It is still verified on **two-seat or single pieces of furniture**, not a full living-room scene; PHASE 2 reproduction, P2/P4/P5 and the separate environment are still not done (see §8).

> **§10 voided (evening of 2026-11-08)**: the user pointed out "geometry must be generated by the Agent, not written by yourself". The hand-written geometry module `part_programs.py` and the assembly script `run_fine_sofa.py` have been physically deleted, replaced by the Agent loop of `agentic_coder.py` / `agentic_pipeline.py` / `part_runner.py` / `run_agent_scene.py` / `critic_repair.py`. The good-looking renders from then are kept only as a historical comparison and **do not represent the system path**. For current results see `AGENTIC_SYSTEM_REPORT.md`.
