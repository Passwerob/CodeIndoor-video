#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Render the gaming laptop with the Blender Python API and take a screenshot
"""

import os
import shutil
import sys
import io
import subprocess
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

print("=" * 80)
print(" " * 20 + "Blender auto-render of the AI-generated laptop")
print("=" * 80)
print()

# Locate Blender
blender_paths = [
    os.environ.get("BLENDER_EXE"),
    shutil.which("blender"),
    r"C:\Program Files\Blender Foundation\Blender 4.2\blender.exe",
    r"C:\Program Files\Blender Foundation\Blender 4.1\blender.exe",
    r"C:\Program Files\Blender Foundation\Blender 4.0\blender.exe",
    r"C:\Program Files\Blender Foundation\Blender 3.6\blender.exe",
    r"C:\Program Files\Blender Foundation\Blender\blender.exe",
]

blender_exe = None
for path in blender_paths:
    if path and Path(path).exists():
        blender_exe = path
        break

if not blender_exe:
    print("❌ Blender installation not found")
    print()
    print("Please install Blender or run manually:")
    print("  1. Open Blender")
    print("  2. Scripting → Open → outputs/deepseek_gaming_laptop/render_screenshot.py")
    print("  3. Run the script")
    print()
    sys.exit(1)

print(f"✅ Found Blender: {blender_exe}")
print()

# Script path
script_path = Path(__file__).parent / "outputs/deepseek_gaming_laptop/render_screenshot.py"
output_path = Path(__file__).parent / "outputs/deepseek_gaming_laptop/gaming_laptop_render.png"

print(f"📜 Render script: {script_path}")
print(f"📸 Output image: {output_path}")
print()

print("🎬 Starting Blender render...")
print("-" * 80)

# Run Blender render in the background
cmd = [
    blender_exe,
    "--background",  # background mode
    "--python", str(script_path)  # run the Python script
]

try:
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='replace'
    )

    # Show output
    if result.stdout:
        for line in result.stdout.split('\n'):
            if any(keyword in line for keyword in ['Importing', 'Rendering', 'complete', 'Output', '✅']):
                print(line)

    if result.returncode == 0:
        print()
        print("=" * 80)
        print("🎉 Render complete!")
        print("=" * 80)
        print()

        if output_path.exists():
            size_mb = output_path.stat().st_size / (1024 * 1024)
            print(f"📸 Screenshot saved: {output_path}")
            print(f"   Size: {size_mb:.2f} MB")
            print()
            print("🖼️  View in the file manager:")
            print(f"   {output_path.parent}")
        else:
            print("⚠️  Output file not found, but the render command ran successfully")
    else:
        print()
        print("❌ Render failed")
        if result.stderr:
            print("Error message:")
            print(result.stderr[:500])

except Exception as e:
    print(f"❌ Execution error: {e}")
    print()
    print("To run manually:")
    print("  1. Open Blender")
    print("  2. Scripting → Open → outputs/deepseek_gaming_laptop/render_screenshot.py")
    print("  3. Run the script (▶️)")
