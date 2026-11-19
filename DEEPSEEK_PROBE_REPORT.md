# DEEPSEEK_PROBE_REPORT.md — Official API measured report

> Measured at: 2026-11-08 21:4x (GMT+8)
> Scripts: `bridge/probe_deepseek.py` (capabilities), `bridge/probe_compat.py` (protocol boundaries), `bridge/test_gateway.py` (gateway smoke test)
> Raw sanitized results: `DEEPSEEK_PROBE_RESULT.json`, `DEEPSEEK_COMPAT_RESULT.json`
> **This report and all result files contain no real API key, prompt text or image bytes.**

## 1. `/models` measured

| Endpoint | HTTP | Models returned |
|---|---|---|
| `GET https://api.deepseek.com/models` | 200 | `deepseek-flash`, `deepseek-v4-pro` |
| `GET https://api.deepseek.com/v1/models` | 200 | Same as above |

The account has **only these two** models available. `SCENECODE_TEXT_MODEL=deepseek-flash` / `SCENECODE_VISION_MODEL=deepseek-flash` in the task config match reality. `deepseek-chat` / `deepseek-reasoner` are deprecated and do not appear in the list.

## 2. Eight capabilities measured (all 200)

| # | Capability | Result | Evidence |
|---|---|---|---|
| 1 | Plain-text chat | 200, returns `OK` | `message_keys=[content, role]`, 10 tokens |
| 2 | JSON output | 200, returns `{"probe":"ok"}` | `response_format:{"type":"json_object"}` takes effect |
| 3 | Tool calls | 200, returns `get_weather` | `message.tool_calls` present |
| 4 | Thinking mode | 200, returns `reasoning_content` + `content` | `thinking:{"type":"enabled"}` |
| 5 | Long-text output | 200, 303 completion tokens | `max_tokens=700` |
| 6 | **Image input** | **200, 1×1 PNG → `Pink`** | `image_url` + data URI content block accepted |
| 7 | `/models` | 200 | See §1 |
| 8 | `/v1/models` | 200 | See §1 |

**Conclusion**: `deepseek-flash` can handle the Planner / Designer / Critic text reasoning, JSON structured output, tool calls, and **visual review of rendered images**. This satisfies the task's premise that "DeepSeek Flash can receive rendered images for visual review". The task also states explicitly that "it cannot be treated as an image generation model" — measurement confirms the account has no image generation endpoint, so the gateway generates reference images locally.

## 3. Seven protocol boundaries measured (including one real fault)

The **first run** hit the local proxy tunnel error `Tunnel connection failed: 502`, and 6 of the 7 items never reached the server. This is **not API behavior**; following "find the real cause before fixing", I added backoff retry to the probe and reran, and all 7 items got real responses. The transport failure was not recorded as a protocol conclusion.

| # | Probe | HTTP | Conclusion |
|---|---|---|---|
| 1 | OpenAI-specific parameters `reasoning_effort` / `verbosity` / `service_tier` / `vision_detail` | **200** | **Ignored, no error** → the gateway need not force-strip them |
| 2 | `POST /responses` | **200** | Returns a standard OpenAI Responses object (`object:"response"`, `output`, `usage`…) → `openai-agents` **can pass through directly** |
| 3 | `tool_choice:"required"` + thinking enabled | **400** | `Thinking mode does not support this tool_choice` → **the only rule that must be rewritten** |
| 4 | `temperature` + thinking enabled | 200 | No error (merely ineffective), no need to strip |
| 5 | `max_completion_tokens` alias | 200 | Can pass through directly |
| 6 | Leftover Gemini-3 `extra_content.google.thought_signature` | 200 | Ignored, no forced cleanup needed |
| 7 | `response_format:json_object` + thinking enabled | 200, returns `{"a":1}` | Compatible |

## 4. Architecture assumptions overturned by measurement (already corrected in the architecture document)

| `SCENECODE_ADSL_ARCHITECTURE.md` v2 first draft | Measured | Correction |
|---|---|---|
| "Gateway strips `reasoning_effort` / `verbosity` / `service_tier` / `vision_detail`" | 200, server ignores | Made **optional** (stripped only with `STRICT_PARAMS=1`), pass through by default |
| "Clean up Gemini-3 `thought_signature` to avoid 400" | 200, server ignores | No forced cleanup needed |
| "`openai-agents` may use `/responses`; must verify and possibly translate to chat/completions" | `/responses` 200 and standard format | Changed to **direct pass-through**, greatly simplifying the gateway |
| "`temperature` and `tool_choice` need gateway rewriting" | `temperature` 200; only `tool_choice:"required"+thinking` is 400 | Only one rewrite rule kept |

The one mandatory rewrite retained: `tool_choice == "required"` with thinking enabled → change to `"auto"`.

## 5. Implemented gateway (`bridge/gateway.py`)

Pure Python standard library, **zero new dependencies** (the local Python 3.13 has no PIL/numpy/flask).

| Route | Behavior |
|---|---|
| `GET /v1/models` `/models` | Pass through to DeepSeek |
| `POST /v1/chat/completions` | Model-name mapping + one forced rewrite + pass-through |
| `POST /v1/responses` | Same as above, direct pass-through |
| `POST /v1/images/generations` `/images/edits` | **Local procedural PNG**, returns `{"created":…, "data":[{"b64_json":…}]}` |
| Others | 404 JSON error |

Key basis: SceneCode reads `response.data[0].b64_json` at `image_generation.py:1035`, so returning `b64_json` is **consumed as-is**, `generate_images()` at `router.py:1411` does not fail, and `fallback_backend: flux-klein` is not triggered. The PNG is encoded with the standard library `zlib`/`struct` (neutral background + furniture block outline, deterministically colored from the prompt hash), and **calls no external image/3D generation service**.

Model mapping: `gpt-5.4`, `gpt-5.2`, `gemini-3.1-pro-preview`, etc. → `deepseek-flash`; those already `deepseek-v4-pro` are kept.

The sanitized log `runtime/outputs/gateway_calls.jsonl` records only `ts / method / path / model / status / seconds / note`, never key, prompt or image. The key is read into process memory only from `runtime/.env` (gitignored).

## 6. Gateway smoke test (actually run, 7/7)

`bridge/test_gateway.py`:

| Test | Result |
|---|---|
| `GET /v1/models` | PASS, returns the two real models |
| chat with `gpt-5.4` + all OpenAI-specific parameters | PASS 200, mapped to `deepseek-flash`, returns `OK` |
| Forced rewrite `tool_choice:required` + thinking (with `gemini-3.1-pro-preview`) | PASS 200 (upstream originally 400) |
| `POST /v1/responses` (openai-agents path) | PASS 200, `object:"response"` |
| `POST /v1/images/generations` → local PNG `b64_json` | PASS, 1334 bytes, PNG magic check passes |
| `POST /v1/images/edits` → local PNG | PASS |
| Unsupported path returns 404 | PASS |

> Along the way I found that the assertion in test 7 that I had written was always `True` (a fake test); I changed it to actually request `/audio/transcriptions` to verify the 404 and reran. Per the task discipline of "do not fake success logs", no fake pass is left in.

## 7. To verify / risks (not written up as done)

- `[pending run verification]` **Effect of the procedural reference image on ObjectPlan quality**: without a real reference image, does the Planner still produce multi-part detailed furniture, or degrade into a few cubes. This is the most critical measured risk for PHASE 2.
- `[pending verification]` Whether `openai-agents` actually sends `/responses` or `/chat/completions`, and the shape of the `tool_choice` object in Responses format (the gateway currently handles only the string `"required"`). This can only be confirmed by actually running SceneCode.
- `[to implement]` Chunk-by-chunk SSE forwarding for `stream:true` (the gateway currently reads the whole upstream response before returning; fine for short responses, long streaming unverified).
- `[to implement]` Multipart upload parsing for `/v1/images/edits` (currently the uploaded file is ignored and a procedural image is returned directly; sufficient, but does not reproduce the edit intent).
- `[pending verification]` Token consumption, timeouts, and whether `reasoning_content` is passed back correctly across multiple turns under the real full SceneCode flow.
- The local proxy tunnel gives intermittent 502s, and the gateway currently has **no** retry for upstream transport failure; add it before any long run.

## 8. Reproduction commands

```bash
# Capability probes
python E:/3DUse/SceneCode_audit/bridge/probe_deepseek.py
python E:/3DUse/SceneCode_audit/bridge/probe_compat.py

# Start the gateway (SceneCode's OPENAI_API_BASE points to http://127.0.0.1:8765/v1)
python E:/3DUse/SceneCode_audit/bridge/gateway.py --port 8765

# Smoke test (in another terminal)
python E:/3DUse/SceneCode_audit/bridge/test_gateway.py
```
