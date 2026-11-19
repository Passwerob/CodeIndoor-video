"""One VLM client for every video stage: DeepSeek, through the local gateway.

Facts this module encodes (measured on the official API, 2026-11-13)
-------------------------------------------------------------------
* `deepseek-flash` reads images: image_url parts with data URIs, several per
  request (a 3-view object spec and a 6-frame lighting pass both worked), and
  returns usable grounding boxes in 0..1000 coordinates.
* `deepseek-v4-pro` silently DROPS images. Sent the same 2040x1440 contact
  sheet it billed 427 prompt tokens (flash: 1415) and described a living room
  with a sofa and a TV that were not in the picture. Nothing errors. So every
  call that carries images is pinned to a model that passed `ensure_vision`,
  and a response whose prompt-token count is too small to contain the images
  is rejected instead of trusted.
* The API takes no video: video_frames.py decodes frames locally.
* json_object mode needs the lowercase word "json" in the prompt (HTTP 400
  otherwise); every prompt here contains it.

Calls go to the local gateway (bridge/gateway.py, which logs a hash of every
response for provenance). If the gateway is not running, the call goes
straight to DeepSeek with DEEPSEEK_API_KEY from the environment or
runtime/.env, and says so once. Every answer is cached under the run
directory with the request and response hashes, so a stage re-run costs
nothing and the representation can be traced to the exact model output.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np

try:
    import cv2
except ImportError:  # pragma: no cover - reported by video_frames on use
    cv2 = None

ROOT = Path(os.environ.get("CODEINDOOR_ROOT") or Path(__file__).resolve().parents[1])
ENV_FILE = ROOT / "runtime/.env"
GATEWAY = os.environ.get("CODEINDOOR_GATEWAY", "http://127.0.0.1:8765/v1/chat/completions")
TOKENS_PER_IMAGE_FLOOR = 120      # flash bills ~250-1000 per image; v4-pro billed 0
_VISION_OK: dict[str, bool] = {}
_DIRECT_NOTED = False


class VisionError(RuntimeError):
    """The model answered without seeing the images."""


def setting(name: str, default: str | None = None) -> str | None:
    """Environment first, then runtime/.env (the one secrets file)."""
    value = os.environ.get(name)
    if value and value.strip():
        return value.strip()
    try:
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith(name + "="):
                return line.split("=", 1)[1].strip().strip('"').strip("'") or default
    except OSError:
        pass
    return default


def vision_model() -> str:
    return setting("VIDEO_VISION_MODEL", "deepseek-flash") or "deepseek-flash"


# --------------------------------------------------------------------------
# images
# --------------------------------------------------------------------------
def _load(image) -> np.ndarray:
    if isinstance(image, np.ndarray):
        return image
    if cv2 is None:
        raise RuntimeError("opencv-python is required for the video pipeline "
                           "(pip install opencv-python)")
    data = np.fromfile(str(image), dtype=np.uint8)      # unicode-safe on Windows
    img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f"cannot read image {image}")
    return img


def encode(image, max_side: int = 1600, min_side: int = 0,
           quality: int = 90) -> tuple[dict, str]:
    """(image_url content part, sha256 of the encoded bytes)."""
    img = _load(image)
    h, w = img.shape[:2]
    scale = 1.0
    if max(h, w) > max_side:
        scale = max_side / max(h, w)
    elif min_side and min(h, w) < min_side:
        # Small crops (a key panel 60 px tall) read better enlarged: the model
        # resizes internally and a tiny input loses the icons entirely.
        scale = min(min_side / min(h, w), max_side / max(h, w))
    if abs(scale - 1.0) > 1e-3:
        interp = cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC
        img = cv2.resize(img, (max(1, int(w * scale)), max(1, int(h * scale))),
                         interpolation=interp)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise ValueError("JPEG encoding failed")
    data = buf.tobytes()
    part = {"type": "image_url",
            "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(data).decode("ascii")}}
    return part, hashlib.sha256(data).hexdigest()


# --------------------------------------------------------------------------
# transport
# --------------------------------------------------------------------------
def _post(url: str, payload: dict, headers: dict, timeout: int = 300) -> tuple[dict, bytes]:
    req = urllib.request.Request(url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                                 method="POST", headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        blob = resp.read()
    return json.loads(blob.decode("utf-8")), blob


def _send(payload: dict) -> tuple[dict, bytes, str]:
    """Gateway first; direct DeepSeek if the gateway is not running."""
    global _DIRECT_NOTED
    try:
        body, blob = _post(GATEWAY, payload, {"Content-Type": "application/json"})
        return body, blob, "gateway"
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", None)
        if isinstance(exc, urllib.error.HTTPError) or not isinstance(
                reason, (ConnectionRefusedError, OSError)):
            raise
    key = setting("DEEPSEEK_API_KEY")
    if not key:
        raise RuntimeError(f"gateway {GATEWAY} is not running and DEEPSEEK_API_KEY "
                           f"is not set (env or runtime/.env)")
    base = (setting("DEEPSEEK_BASE_URL", "https://api.deepseek.com") or "").rstrip("/")
    if not _DIRECT_NOTED:
        print(f"[vlm] gateway not reachable; calling {base} directly", flush=True)
        _DIRECT_NOTED = True
    body, blob = _post(base + "/chat/completions", payload,
                       {"Content-Type": "application/json", "Authorization": "Bearer " + key})
    return body, blob, "direct"


def _parse_json(text: str) -> dict | None:
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("{"):]
    try:
        out = json.loads(text)
        return out if isinstance(out, dict) else {"items": out}
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if 0 <= start < end:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                return None
    return None


# --------------------------------------------------------------------------
# public
# --------------------------------------------------------------------------
def ensure_vision(model: str) -> None:
    """Prove `model` sees images before trusting it with the video.

    A 2-colour test card: red on the left, blue on the right. A model that
    drops images cannot answer this, whatever it says about a pantry.
    """
    if _VISION_OK.get(model):
        return
    card = np.zeros((96, 192, 3), dtype=np.uint8)
    card[:, :96] = (0, 0, 230)        # BGR red
    card[:, 96:] = (230, 40, 0)       # BGR blue
    part, _ = encode(card)
    payload = {"model": model, "temperature": 0.0, "max_tokens": 60,
               "response_format": {"type": "json_object"}, "thinking": {"type": "disabled"},
               "messages": [{"role": "user", "content": [
                   {"type": "text", "text": 'Return json {"left": "<colour of the left half>", '
                                            '"right": "<colour of the right half>"}'}, part]}]}
    body, _, _ = _send(payload)
    text = ((body.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    ans = (_parse_json(text) or {})
    ok = "red" in str(ans.get("left", "")).lower() and "blue" in str(ans.get("right", "")).lower()
    if not ok:
        raise VisionError(f"model '{model}' failed the image test card ({text[:120]!r}); "
                          f"it does not see images. Use deepseek-flash "
                          f"(VIDEO_VISION_MODEL).")
    _VISION_OK[model] = True


def ask_json(prompt: str, images: list | None = None, *, stage: str,
             cache_dir: Path | None = None, refresh: bool = False,
             model: str | None = None, max_tokens: int = 4000, max_side: int = 1600,
             min_side: int = 0, temperature: float = 0.0) -> dict:
    """One JSON answer about `images` (list of (label, image) or images).

    Returns {"data": parsed json, "meta": {...}}. Raises on failure: every
    caller in the video pipeline needs the answer to continue, and a stage
    that silently returns nothing produces a scene that silently lacks it.
    """
    model = model or vision_model()
    items = []
    for i, item in enumerate(images or []):
        label, img = item if isinstance(item, tuple) else (f"image_{i}", item)
        part, digest = encode(img, max_side=max_side, min_side=min_side)
        items.append((label, part, digest, str(img) if not isinstance(img, np.ndarray) else None))
    if "json" not in prompt:
        prompt += "\nAnswer with a json object."
    key = hashlib.sha256(json.dumps([model, prompt, max_side, min_side,
                                     [d for _, _, d, _ in items]]).encode()).hexdigest()[:20]
    cache = (cache_dir / f"{stage}_{key}.json") if cache_dir else None
    if cache and cache.is_file() and not refresh:
        try:
            return json.loads(cache.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    if items:
        ensure_vision(model)
    content: list = [{"type": "text", "text": prompt}]
    for label, part, _, _ in items:
        if len(items) > 1:
            content.append({"type": "text", "text": f"{label}:"})
        content.append(part)
    messages = [{"role": "user", "content": content if items else prompt}]
    last_error = None
    for attempt in range(3):
        payload = {"model": model, "messages": messages, "max_tokens": max_tokens,
                   "temperature": temperature, "response_format": {"type": "json_object"},
                   "thinking": {"type": "disabled"}}
        started = time.time()
        try:
            body, blob, route = _send(payload)
        except Exception as exc:  # noqa: BLE001
            last_error = f"{type(exc).__name__}: {exc}"
            time.sleep(2 + 3 * attempt)
            continue
        usage = body.get("usage") or {}
        if items and int(usage.get("prompt_tokens") or 0) < TOKENS_PER_IMAGE_FLOOR * len(items):
            raise VisionError(f"{stage}: {usage.get('prompt_tokens')} prompt tokens for "
                              f"{len(items)} image(s) -- the images were dropped by "
                              f"'{model}'")
        text = ((body.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        data = _parse_json(text)
        if data is None:
            last_error = "answer was not valid json"
            messages = messages[:1] + [{"role": "assistant", "content": text[:4000]},
                                       {"role": "user", "content":
                                        "That was not valid json. Return ONLY the json object."}]
            continue
        out = {"data": data, "meta": {
            "stage": stage, "model": model, "upstream_model": body.get("model"),
            "route": route, "usage": usage, "seconds": round(time.time() - started, 2),
            "resp_sha": hashlib.sha256(blob).hexdigest()[:16],
            "prompt_sha": hashlib.sha256(prompt.encode()).hexdigest()[:16],
            "images": [{"label": l, "sha256": d, "source": s} for l, _, d, s in items],
            "finish_reason": (body.get("choices") or [{}])[0].get("finish_reason"),
            "ts": time.strftime("%Y-%m-%d %H:%M:%S")}}
        if cache:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        return out
    raise RuntimeError(f"{stage}: VLM call failed after 3 attempts: {last_error}")
