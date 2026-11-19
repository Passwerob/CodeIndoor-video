"""Machine-specific paths and secrets for FineScene, resolved in one place.

Every setting is looked up in this order:

  1. the process environment,
  2. the shared ``runtime/.env`` of the enclosing CodeIndoor repository
     (or the file named by ``CODEINDOOR_ENV_FILE``),
  3. a default relative to this checkout (``aDSL/`` next to ``FineScene/``,
     asset packs under ``assets/``, TRELLIS checkouts under ``research/``).

On import, the entries of the env file are copied into ``os.environ``. A
variable that is already set is never overwritten. Blender workers and the aDSL
model-profile loader then see the same values, including ``DEEPSEEK_API_KEY``.

This module uses only the standard library, so Blender's bundled Python can
import it.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

FINESCENE_ROOT = Path(__file__).resolve().parents[1]
# The checkout that contains FineScene/ and aDSL/ (code location, not CODEINDOOR_ROOT:
# that variable only relocates runtime/ outputs and the .env file).
REPO_ROOT = FINESCENE_ROOT.parent
_RUNTIME_ROOT = Path(os.environ.get("CODEINDOOR_ROOT") or REPO_ROOT)
ENV_FILE = Path(os.environ.get("CODEINDOOR_ENV_FILE") or _RUNTIME_ROOT / "runtime" / ".env")


def _load_env_file(path: Path) -> None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and value and not os.environ.get(key, "").strip():
            os.environ[key] = value


_load_env_file(ENV_FILE)


def setting(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name, "").strip()
    return value or default


def path_setting(name: str, default: Path) -> Path:
    return Path(setting(name) or default).expanduser()


def find_blender() -> str:
    """Use BLENDER_EXE if set, then ``blender`` on PATH, then the usual install folders."""
    explicit = setting("BLENDER_EXE")
    if explicit:
        return explicit
    on_path = shutil.which("blender")
    if on_path:
        return on_path
    candidates: list[Path] = []
    if sys.platform == "win32":
        base = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Blender Foundation"
        candidates += sorted(base.glob("Blender*/blender.exe"), reverse=True)
    elif sys.platform == "darwin":
        candidates.append(Path("/Applications/Blender.app/Contents/MacOS/Blender"))
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return "blender"


BLENDER = find_blender()

ADSL_ROOT = path_setting("ADSL_ROOT", REPO_ROOT / "aDSL")
LLM_CONFIGS = ADSL_ROOT / "adsl-agents" / "configs" / "llm"
# Previously refined "Oakwarden" armchair (an aDSL output, not in git).
OAKWARDEN_DIR = path_setting("OAKWARDEN_ASSET_DIR",
                             ADSL_ROOT / "outputs" / "medieval_armchair_20261017")

ASSETS_ROOT = path_setting("ASSETS_ROOT", REPO_ROOT / "assets")
STUDY_ASSETS = path_setting("STUDY_ASSETS_DIR",
                            ASSETS_ROOT / "external" / "realistic_study_20261019")
POLYHAVEN_ASSETS = path_setting("POLYHAVEN_ASSETS_DIR",
                                ASSETS_ROOT / "external" / "polyhaven")

# Optional TRELLIS / TRELLIS.2 checkouts and their separate GPU venv.
RESEARCH_ROOT = path_setting("RESEARCH_ROOT", REPO_ROOT / "research")
_venv_python = ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
TRELLIS_PYTHON = path_setting("TRELLIS_PYTHON",
                              RESEARCH_ROOT / "trellis_windows_venv" / _venv_python)

RUNS = FINESCENE_ROOT / "runs"
