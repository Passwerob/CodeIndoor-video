"""Machine-specific paths and secrets, resolved in one place.

Every setting is looked up in this order:

  1. the process environment (``export BLENDER_EXE=...``),
  2. ``runtime/.env`` (gitignored; copy ``.env.example`` there),
  3. a default relative to this repository.

The defaults assume the layout of the repository itself: ``aDSL/`` and
``FineScene/`` sit next to ``adsl_adapter/``. Large external asset packs go in
``assets/`` (gitignored) unless ``ASSETS_ROOT`` points somewhere else.

When this module is imported it copies the entries of ``runtime/.env`` into
``os.environ``. It never overwrites a variable that is already set. Child
processes (Blender, FineScene workers, the aDSL model-profile loader) then see
the same configuration without reading the file themselves.

This module uses only the standard library, so Blender's bundled Python can
import it.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

# REPO: where this code lives; the toolkit defaults (aDSL/, FineScene/, assets/) hang off it.
# ROOT: where runtime/ (outputs, .env) lives. CODEINDOOR_ROOT moves only that, e.g.
# tests point it at a temp dir, so it must not drag the toolkit paths along.
REPO = Path(__file__).resolve().parents[1]
ROOT = Path(os.environ.get("CODEINDOOR_ROOT") or REPO)
ENV_FILE = Path(os.environ.get("CODEINDOOR_ENV_FILE") or ROOT / "runtime" / ".env")


def read_env_file(path: Path) -> dict[str, str]:
    """Parse a simple KEY=VALUE file. Blank values are skipped."""
    values: dict[str, str] = {}
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return values
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and value:
            values[key] = value
    return values


def load_env_file(path: Path = ENV_FILE) -> None:
    """Copy ``path`` into ``os.environ`` without overriding existing values."""
    for key, value in read_env_file(path).items():
        if not os.environ.get(key, "").strip():
            os.environ[key] = value


load_env_file()


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
    return "blender"   # let the subprocess fail with a clear "not found"


RUNTIME = ROOT / "runtime"
BLENDER = find_blender()

ADSL_ROOT = path_setting("ADSL_ROOT", REPO / "aDSL")
ADSL_CORE = ADSL_ROOT / "adsl-core"
ADSL_AGENTS = ADSL_ROOT / "adsl-agents"

FINESCENE_ROOT = path_setting("FINESCENE_ROOT", REPO / "FineScene")
FINESCENE_SRC = FINESCENE_ROOT / "src"
# FineScene's vision/LLM reviewer profile (key comes from DEEPSEEK_API_KEY).
VISION_PROFILE = path_setting("FINESCENE_VISION_PROFILE",
                              FINESCENE_ROOT / "configs" / "deepseek-flash.yaml")

# External asset packs (Poly Haven / ambientCG downloads etc.), not in git.
ASSETS_ROOT = path_setting("ASSETS_ROOT", REPO / "assets")
STUDY_ASSETS = path_setting("STUDY_ASSETS_DIR",
                            ASSETS_ROOT / "external" / "realistic_study_20261019")
POLYHAVEN_ASSETS = path_setting("POLYHAVEN_ASSETS_DIR",
                                ASSETS_ROOT / "external" / "polyhaven")


def add_to_sys_path(*paths: Path) -> None:
    for path in paths:
        entry = str(path)
        if entry not in sys.path:
            sys.path.insert(0, entry)


def describe() -> dict[str, str]:
    """Resolved settings, for `python -m adsl_adapter.codeindoor_env` / doctor output."""
    return {
        "CODEINDOOR_ROOT": str(ROOT),
        "env file": f"{ENV_FILE} ({'found' if ENV_FILE.is_file() else 'missing'})",
        "BLENDER_EXE": BLENDER,
        "ADSL_ROOT": str(ADSL_ROOT),
        "FINESCENE_ROOT": str(FINESCENE_ROOT),
        "FINESCENE_VISION_PROFILE": str(VISION_PROFILE),
        "ASSETS_ROOT": str(ASSETS_ROOT),
        "STUDY_ASSETS_DIR": str(STUDY_ASSETS),
        "POLYHAVEN_ASSETS_DIR": str(POLYHAVEN_ASSETS),
        "DEEPSEEK_API_KEY": "set" if setting("DEEPSEEK_API_KEY") else "missing",
    }


if __name__ == "__main__":
    for key, value in describe().items():
        print(f"{key:26s} {value}")
