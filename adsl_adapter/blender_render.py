"""Cycles device selection (Blender side).

CYCLES_DEVICE=CUDA | OPTIX | HIP | METAL | ONEAPI renders on that GPU backend;
unset or CPU keeps the CPU (the Windows default the forge was tuned on). Any
failure to enable the GPU falls back to the CPU with a note, never an error:
on the Linux H20 box OptiX failed to compile its kernel with driver 535 while
CUDA rendered a 720x1280 frame at 64 samples in 1.6 s.
"""
from __future__ import annotations

import os

import bpy


def configure(scene) -> str:
    want = (os.environ.get("CYCLES_DEVICE") or "CPU").upper()
    scene.render.engine = "CYCLES"
    if want == "CPU":
        scene.cycles.device = "CPU"
        return "CPU"
    try:
        prefs = bpy.context.preferences.addons["cycles"].preferences
        prefs.compute_device_type = want
        prefs.get_devices()
        devices = [d for d in prefs.devices if d.type == want]
        if not devices:
            raise RuntimeError(f"no {want} device")
        for d in prefs.devices:
            d.use = d.type == want
        scene.cycles.device = "GPU"
        return f"{want} x{len(devices)}"
    except Exception as exc:  # noqa: BLE001
        print(f"[render] {want} unavailable ({exc}); rendering on CPU", flush=True)
        scene.cycles.device = "CPU"
        return "CPU"
