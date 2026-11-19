# LOCAL_ENV_PROFILE.md — Local environment profile and adaptation decisions

> Measured at: 2026-11-08 21:1x (GMT+8)
> Method: read-only commands; no environment was installed or modified
> Purpose: factual basis for PHASE 1, "adapt to the local environment"

## 1. Measured baseline

| Item | Measured value | Evidence |
|---|---|---|
| OS | Windows 11 Pro, Version 10.0.26200, Build 26200 | `Win32_OperatingSystem` |
| WSL feature | `Microsoft-Windows-Subsystem-Linux = Disabled`; `VirtualMachinePlatform = Enabled` | `Get-WindowsOptionalFeature` |
| WSL executable | `wsl.exe` **access denied**, blocked by the Security Center "program blacklist" | Command returned `PROGRAM BLOCKED BY SECURITY POLICY` |
| System Python | **3.13.14** (WorkBuddy 3.13.12), 3.13.12, 3.10; **no 3.11** | `python --version` / `py -0p` |
| aDSL venv | `E:/3DUse/aDSL/venv/Scripts/python.exe` = **Python 3.13.4** | Run directly |
| uv | **Not installed** | `uv --version` → command not found |
| conda | Not on PATH; only a `E:/3DUse/conda_pkgs` cache directory | `where conda` |
| Blender | `E:/Blender/blender.exe` = **Blender 5.2.2 LTS** (2026-09-15), **not on PATH** | `blender.exe --version` |
| GPU | NVIDIA GeForce RTX 5060 Ti, **16311 MiB ≈ 16 GB**, driver 610.88 | `nvidia-smi` |
| RAM | 31.8 GB | `Win32_ComputerSystem` |
| Disk | **C: 24 GB free / 93% used**; **E: 295 GB free** | `df -h` |

## 2. Hard blockers and their direct consequences

### 2.1 WSL2 is unavailable (double block)

The feature itself is `Disabled`, and `wsl.exe` is blocked by the security-policy blacklist. **The block is explicitly not to be bypassed**; switching shell or retrying with a script is not allowed.

Per the task text, "if WSL2 is unavailable, evaluate a native Windows deployment" → **the native Windows branch must be taken**.

> If WSL2 is really needed later, the only compliant path is for the user to remove `wsl.exe` under **Security Center → Command Security → Program Blacklist** and enable the WSL feature (requires admin + reboot). That is a user-side action, and I will not bypass it on their behalf.

### 2.2 Real feasibility of native Windows

| Dependency | Native Windows risk | Verdict |
|---|---|---|
| `drake>=1.47.0` (pydrake) | Drake officially targets Ubuntu/macOS; Windows wheel coverage is very limited | 🔴 **Highest risk**. Need to test whether `uv` can resolve a wheel; if it fails, the Drake dependency path must be trimmed (keep only code geometry + Blender export, do not run `dmd.yaml` simulation) |
| `bpy==4.5.4` | Needs the dedicated `download.blender.org/pypi` index | 🟡 Installable, but coexists with local 5.2.2, giving two Python APIs |
| `torch 2.5 / open-clip-torch / diffusers` | Windows CUDA wheels exist, but 16 GB VRAM is not enough to run generative backends | 🟡 Installable, but **not enabled** |
| `coacd / vhacdx / python-fcl / pymeshlab / manifold3d / xatlas` | Mostly C extensions that need compiling; Windows wheel coverage is incomplete | 🟡 Verify one by one; trim as needed |
| `pyvirtualdisplay` | Depends on Linux Xvfb | 🟡 Installable but useless; headless Blender runs directly on Windows |
| `rtree / shapely / opencv` | Windows wheels exist | 🟢 |
| Blender launch | `blender.exe -b --python ...` works on Windows | 🟢 |

**Conclusion**: native Windows **can run the "pure code geometry → Blender execution → render" main line**; the `drake/SDF simulation export` branch most likely will not run, must be explicitly disabled in the config, and must be recorded in the reproduction report as "not reproduced due to environment limits", never glossed over.

### 2.3 Three-way Blender version mismatch

| Source | Blender version |
|---|---|
| Already on this machine (used by aDSL) | **5.2.2 LTS** |
| SceneCode README requirement | **4.4.3 Linux** (`blender-4.4.3-linux-x64`) |
| SceneCode `pyproject.toml` | **`bpy==4.5.4`** |

Handling principle: **none of the three overrides another**.
- aDSL keeps using the local 5.2.2, untouched.
- Put a **Blender 4.4.3 Windows portable zip** (the official `download.blender.org/release/Blender4.4/` has a Windows build) in the standalone SceneCode directory, and point to it via `PATH` + `SCENECODE_BLENDER_GLOBAL_LOCK`.
- `bpy==4.5.4` serves only as the Python-side `mathutils/bpy` module dependency and is not used to start the Blender process; if it conflicts with 4.4.3's Python 3.11, use a `uv` override or make it an optional dependency.

### 2.4 VRAM and disk constraints

- **16 GB VRAM** → the paper pipeline recommends 45 GB, SAM3D needs 32 GB, Hunyuan3D needs 24 GB; **none are met**. This is in fact consistent with the requirement "use no image/3D generative models": only **Blender Python procedural geometry + EEVEE rendering + DeepSeek visual review** may be used, and the `generated` / `articulated` retrieval backends, SAM3D, FLUX and CLIP embedding retrieval are disabled.
- **Only 24 GB left on C:** → all venvs, uv cache, pip cache, Blender portable, models/data and outputs **go on E: without exception**. `UV_CACHE_DIR`, `UV_PYTHON_INSTALL_DIR`, `PIP_CACHE_DIR`, `HF_HOME` and `TMP`/`TEMP` must be set explicitly, otherwise the defaults write to C: and fill it up.

## 3. Adaptation plan (isolated, no change to the existing environment)

### 3.1 Directory convention (all on E:)

```
E:/3DUse/SceneCode_audit/            # standalone working directory (already exists)
  ├─ sources/                       # SceneCode source snapshot (for audit)
  ├─ tests/                         # regression tests for the aDSL changes (already exists)
  ├─ env/                           # environment scripts and config
  ├─ bridge/                        # local compatibility gateway + aDSL adapter layer (to be implemented)
  └─ runtime/                       # standalone venv / Blender portable / outputs
       ├─ .venv-scenecode/          # Python 3.11, created by uv
       ├─ blender-4.4.3-windows/    # standalone Blender
       ├─ cache/                    # uv / pip / HF caches
       └─ outputs/                  # run artifacts
```

### 3.2 How to obtain Python 3.11

**Do not install a system Python, and do not touch 3.13 / 3.10 / the aDSL venv.** Use `uv`'s managed Python:

```bash
uv python install 3.11            # installs into UV_PYTHON_INSTALL_DIR (on E:)
uv venv --python 3.11 runtime/.venv-scenecode
```

`uv` itself also does not modify the system: download the standalone binary into `E:/3DUse/SceneCode_audit/runtime/uv/` and call it only by absolute path.

### 3.3 Two repo-level problems that must be fixed (in the standalone copy, without changing upstream semantics)

1. `pyproject.toml:62` `packages = ["scenesmith"]` → `["scenecode"]` (otherwise `uv sync` installs the wrong package name).
2. `pyproject.toml:76` `known_first_party = ['scenesmith']` (isort config, affects only format checks).

These two are **leftover packaging metadata**, not changes to SceneCode's architecture/contracts, and do not conflict with "aDSL adapts to SceneCode, SceneCode is not changed".

### 3.4 Environment variables (all via `.env`, added to `.gitignore`, no real key)

```
OPENAI_API_KEY=<injected from .env; must not be written into code/logs/reports>
OPENAI_API_BASE=http://127.0.0.1:<bridge_port>/v1      # points to the local compatibility gateway, not directly to DeepSeek
SCENECODE_TEXT_MODEL=deepseek-flash
SCENECODE_VISION_MODEL=deepseek-flash
PATH=<E:/.../blender-4.4.3-windows>;%PATH%
SCENECODE_BLENDER_GLOBAL_LOCK=E:/.../runtime/blender.lock
UV_CACHE_DIR / UV_PYTHON_INSTALL_DIR / PIP_CACHE_DIR / HF_HOME = E:/.../runtime/cache/*
SCENECODE_OUTPUT_DIR / SCENECODE_MATERIALS_DIR = E:/.../runtime/outputs/*
SAM3_CHECKPOINT / SAM3D_PIPELINE_CONFIG / FLUX_PYTHON / FLUX_MODEL_PATH / GOOGLE_API_KEY = empty (all disabled)
```

**Key point**: `OPENAI_API_BASE` points to the local gateway rather than `https://api.deepseek.com`. The reason is in `SCENECODE_ADSL_ARCHITECTURE.md` §3 — this way SceneCode's two LLM stacks, image-generation calls and `images.generate` can all be taken over by the adapter layer without changing a line of source.

### 3.5 Operations not yet performed (to avoid harming the environment)

This round **only did read-only probing plus the aDSL core code changes and tests**; none of the following has been run, and they wait for confirmation:

- Install uv / download Python 3.11 / create `.venv-scenecode`
- Download Blender 4.4.3 Windows portable
- `uv sync` (78 MB of dependencies + several extensions needing compilation; the network is unstable and it may fail for a long time)
- Any `pytest` / `main.py` / Blender / DeepSeek call

`[pending run verification]`: whether pydrake has a usable wheel on Windows + Python 3.11; this is the branch point deciding "whether the SDF simulation export can be kept".

## 4. Baseline record (aDSL before vs. after the changes)

| Test | Before changes | After changes |
|---|---|---|
| `test_adsl_direct.py` | Pass | Pass |
| `scripts/test_relations.py` | 14 tests OK | 14 tests OK |
| `SceneCode_audit/tests/test_adsl_stable_ids.py` | Does not exist | **10 tests OK** |

Changed files (all in `E:/3DUse/aDSL/adsl-core/core/`):
- `asset.py`: adds `component_id` (`kw_only`, appended at the end), `attach_part` binds the part key, `copy()` preserves the ID, `component_ids/find_component/component_path/effective_component_id/from_dict`
- `serialize.py`: `asset_to_dict` adds `component_id` (additional field, ignored by old readers); adds `asset_from_dict`
- `__init__.py`: exports `asset_to_dict` / `asset_from_dict`
