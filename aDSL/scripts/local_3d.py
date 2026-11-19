"""Portable launcher for the installed desktop Blender and the aDSL agents."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def find_blender(explicit=None):
    candidates = [explicit, os.environ.get("ADSL_BLENDER_EXECUTABLE"),
                  os.environ.get("BLENDER_EXE"), shutil.which("blender")]
    foundation = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Blender Foundation"
    candidates.extend(sorted(foundation.glob("Blender */blender.exe"), reverse=True))
    for item in candidates:
        if item and Path(item).is_file():
            return str(Path(item).resolve())
    raise FileNotFoundError("Set BLENDER_EXE (or ADSL_BLENDER_EXECUTABLE) or pass --blender PATH")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--blender", help="Override Blender executable")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="Check local Python, package imports and Blender")
    build = sub.add_parser("build", help="Import JSON or run an aDSL program in Blender")
    source = build.add_mutually_exclusive_group(required=True)
    source.add_argument("--input", type=Path)
    source.add_argument("--source", type=Path)
    build.add_argument("--output", type=Path, required=True)
    build.add_argument("--up-axis", choices=("y", "z"), default="z")
    build.add_argument("--rgb-emission", type=float, default=0)
    build.add_argument("--no-render", action="store_true")
    build.add_argument("--views", type=int, default=1)
    build.add_argument("--resolution", type=int, default=640)
    build.add_argument("--urdf", action="store_true")
    build.add_argument("--open", action="store_true", help="Open the finished .blend in desktop Blender")
    agent = sub.add_parser("agent", help="Official agent CLI using external Blender")
    agent.add_argument("--model-config", type=Path)
    agent.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    blender = find_blender(args.blender)
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8",
               ADSL_BLENDER_EXECUTABLE=blender)
    if args.command == "doctor":
        import adsl.core
        import numpy
        version = subprocess.check_output([blender, "--version"], text=True, encoding="utf-8").splitlines()[0]
        print(json.dumps({"python": sys.executable, "python_version": sys.version.split()[0],
            "numpy": numpy.__version__, "adsl_core": adsl.core.__file__,
            "blender": blender, "blender_version": version}, indent=2))
        return subprocess.call([sys.executable, "-m", "pip", "check"], env=env)
    if args.command == "agent":
        profile = ["--model-config", str(args.model_config.resolve())] if args.model_config else []
        return subprocess.call([sys.executable, "-m", "adsl.agents.cli", *profile, *args.arguments], cwd=ROOT, env=env)
    source_path = (args.input or args.source).resolve()
    command = [blender, "--background", "--factory-startup", "--python-exit-code", "1",
        "--python", str(ROOT / "scripts" / "blender_worker.py"), "--",
        "--input" if args.input else "--source", str(source_path),
        "--output", str(args.output.resolve()), "--up-axis", args.up_axis,
        "--rgb-emission", str(args.rgb_emission), "--render-view-count", str(args.views),
        "--resolution", str(args.resolution)]
    if not args.no_render:
        command.append("--render")
    if args.urdf:
        command.append("--urdf")
    result = subprocess.call(command, cwd=ROOT, env=env)
    if result == 0 and args.open:
        subprocess.Popen([blender, str(args.output.resolve() / "scene.blend")], env=env)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
