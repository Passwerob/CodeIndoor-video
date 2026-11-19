#!/usr/bin/env python3
"""Set up the single project venv and check the local toolchain.

    python env/setup_env.py            # create .venv, install requirements.txt, run checks
    python env/setup_env.py --check    # checks only (no venv / install)

Run it with the interpreter the venv should use, e.g. ``py -3.13 env/setup_env.py``
on Windows or ``python3.11 env/setup_env.py`` on Linux.

What it does:
  1. creates ``.venv`` at the repository root (skipped if it already exists),
  2. ``pip install -r requirements.txt``, which includes aDSL as editable packages,
  3. copies ``.env.example`` to ``runtime/.env`` if there is no env file yet,
  4. prints the resolved paths (codeindoor_env) and checks that Blender starts
     and that the venv can import adsl.core / adsl.agents.

It never writes an API key. Fill in ``runtime/.env`` yourself.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
VENV = REPO / ".venv"
VENV_PY = VENV / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    print("$", " ".join(str(c) for c in cmd), flush=True)
    return subprocess.run([str(c) for c in cmd], **kw)


def create_venv() -> None:
    if sys.version_info < (3, 10):
        raise SystemExit(f"Python 3.10+ required, this is {sys.version.split()[0]}")
    if VENV_PY.exists():
        print(f"venv exists: {VENV}")
    else:
        run([sys.executable, "-m", "venv", VENV], check=True)
    run([VENV_PY, "-m", "pip", "install", "--upgrade", "pip"], check=True)
    run([VENV_PY, "-m", "pip", "install", "-r", REPO / "requirements.txt"], check=True, cwd=REPO)


def ensure_env_file() -> None:
    target = REPO / "runtime" / ".env"
    if target.exists():
        print(f"env file exists: {target}")
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(REPO / ".env.example", target)
    print(f"created {target} from .env.example -> fill in DEEPSEEK_API_KEY etc.")


def check() -> int:
    sys.path.insert(0, str(REPO / "adsl_adapter"))
    import codeindoor_env as env

    problems = 0
    print("\n=== resolved settings (env > runtime/.env > repo default) ===")
    for key, value in env.describe().items():
        print(f"  {key:26s} {value}")

    print("\n=== checks ===")
    try:
        out = subprocess.run([env.BLENDER, "--version"], capture_output=True, text=True, timeout=120)
        first = (out.stdout or "").strip().splitlines()[:1]
        print(f"  blender    OK  {first[0] if first else '(no version output)'}")
    except (OSError, subprocess.TimeoutExpired) as exc:
        problems += 1
        print(f"  blender    MISSING  {env.BLENDER!r}: {exc}  -> set BLENDER_EXE in runtime/.env")

    for name, path in (("aDSL", env.ADSL_CORE), ("FineScene", env.FINESCENE_SRC)):
        ok = path.is_dir()
        problems += not ok
        print(f"  {name:10s} {'OK ' if ok else 'MISSING'} {path}")

    if VENV_PY.exists():
        probe = "import adsl.core, adsl.agents, cv2, numpy, pydantic; print('imports ok')"
        out = subprocess.run([str(VENV_PY), "-c", probe], capture_output=True, text=True)
        ok = out.returncode == 0
        problems += not ok
        detail = out.stdout.strip() if ok else (out.stderr.strip().splitlines() or ["?"])[-1]
        print(f"  venv       {'OK ' if ok else 'BROKEN'} {VENV_PY}: {detail}")
    else:
        problems += 1
        print(f"  venv       MISSING {VENV}  -> run: python env/setup_env.py")

    if not env.setting("DEEPSEEK_API_KEY") or env.setting("DEEPSEEK_API_KEY") == "your-key-here":
        print("  key        DEEPSEEK_API_KEY not set (runtime/.env) - LLM / vision steps will fail")
    if not env.STUDY_ASSETS.is_dir():
        print(f"  assets     (optional) {env.STUDY_ASSETS} not found - asset-library steps need it")

    print(f"\n{'all good' if not problems else f'{problems} problem(s)'}")
    return 1 if problems else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="only report, do not create/install")
    args = ap.parse_args()
    if not args.check:
        create_venv()
        ensure_env_file()
    return check()


if __name__ == "__main__":
    raise SystemExit(main())
