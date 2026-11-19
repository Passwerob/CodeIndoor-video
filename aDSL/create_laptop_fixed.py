#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Fixed version: creates a correct laptop model (screen standing upright)
"""

import sys
import io
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "adsl-core"))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

from core.asset import Asset
from core.primitives import Cube, Sphere, Cylinder
from core.serialize import asset_to_dict
import numpy as np

print("=" * 80)
print(" " * 15 + "Create the fixed gaming laptop (screen standing upright)")
print("=" * 80)
print()

laptop = Asset(label="Gaming Laptop Fixed")

print("🎨 Creating laptop...")
print("-" * 80)

# Keyboard base (placed horizontally at Y=0)
laptop.attach_part("base", Cube(
    scale=(3.0, 0.1, 2.0),
    center=(0.0, 0.05, 0.0),  # Y=0.05 puts the base on the ground
    color=(0.15, 0.15, 0.17)
))
print("✓ Keyboard base")

# Screen hinge (connects the base and the screen)
laptop.attach_part("hinge", Cube(
    scale=(3.0, 0.1, 0.1),
    center=(0.0, 0.1, -1.0),  # at the rear of the base
    color=(0.1, 0.1, 0.12)
))
print("✓ Screen hinge")

# Screen shell (standing vertically)
laptop.attach_part("screen_frame", Cube(
    scale=(3.0, 2.0, 0.05),
    center=(0.0, 1.1, -1.05),  # up along Y, slightly back along Z
    color=(0.05, 0.05, 0.06)
))
print("✓ Screen shell")

# Display (slightly smaller, glowing blue)
laptop.attach_part("display", Cube(
    scale=(2.8, 1.8, 0.02),
    center=(0.0, 1.1, -1.0),  # slightly forward
    color=(0.1, 0.3, 0.5)
))
print("✓ Display")

# Touchpad
laptop.attach_part("touchpad", Cube(
    scale=(0.8, 0.01, 0.5),
    center=(0.0, 0.11, 0.5),
    color=(0.2, 0.2, 0.25)
))
print("✓ Touchpad")

# RGB keyboard keys (4 rows by 14 columns)
print("✓ Creating RGB keyboard keys...")
key_colors = [
    (1.0, 0.0, 0.0),   # red
    (1.0, 0.5, 0.0),   # orange
    (1.0, 1.0, 0.0),   # yellow
    (0.0, 1.0, 0.0),   # green
    (0.0, 1.0, 1.0),   # cyan
    (0.0, 0.0, 1.0),   # blue
    (0.5, 0.0, 1.0),   # purple
    (1.0, 0.0, 1.0),   # magenta
]

key_count = 0
for row in range(4):
    for col in range(14):
        color = key_colors[key_count % len(key_colors)]
        key_count += 1
        x = -1.3 + col * 0.19
        z = -0.7 + row * 0.25
        laptop.attach_part(f"key_{row}_{col}", Cube(
            scale=(0.16, 0.05, 0.2),
            center=(x, 0.13, z),
            color=color
        ))
print(f"  → Created {key_count} RGB keys")

# Power button (top right)
laptop.attach_part("power_button", Cylinder(
    radius=0.06,
    height=0.02,
    center=(1.3, 0.13, -0.8),
    color=(1.0, 0.0, 0.0)
))
print("✓ Power button")

# Speaker grilles (left and right sides)
print("✓ Speakers...")
for i in range(4):
    z = -0.4 + i * 0.3
    laptop.attach_part(f"speaker_l_{i}", Cylinder(
        radius=0.03,
        height=0.02,
        center=(-1.4, 0.12, z),
        color=(0.3, 0.3, 0.3)
    ))
    laptop.attach_part(f"speaker_r_{i}", Cylinder(
        radius=0.03,
        height=0.02,
        center=(1.4, 0.12, z),
        color=(0.3, 0.3, 0.3)
    ))

# Logo
laptop.attach_part("logo", Cube(
    scale=(0.2, 0.01, 0.08),
    center=(0.0, 0.11, 0.9),
    color=(0.7, 0.7, 0.7)
))
print("✓ Logo")

print()
print("✅ Laptop created!")
print()

# Statistics
print("📊 Model info:")
print(f"  Label: {laptop.label}")
print(f"  Total parts: {len(laptop._children)}")
print()

# Serialization
def numpy_to_python(obj):
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    elif isinstance(obj, (np.integer, np.floating)):
        return obj.item()
    return obj

laptop_dict = asset_to_dict(laptop)
laptop_json = json.loads(json.dumps(laptop_dict, default=numpy_to_python))

# Save
output_dir = Path(__file__).parent / "outputs/laptop_fixed"
output_dir.mkdir(parents=True, exist_ok=True)

json_file = output_dir / "laptop.json"
with open(json_file, 'w', encoding='utf-8') as f:
    json.dump(laptop_json, f, indent=2, ensure_ascii=False)

print(f"💾 JSON saved: {json_file}")
print(f"   Size: {json_file.stat().st_size / 1024:.1f} KB")
print()

# Generate the Blender script
blender_script = '''import bpy
import json
from pathlib import Path

# Clear the scene
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete()

# Load the JSON
json_path = Path(__file__).parent / "laptop.json"
with open(json_path, 'r', encoding='utf-8') as f:
    data = json.load(f)

def create_primitive(prim_type, params, color, alpha, name="Object"):
    """Create a basic primitive"""
    if prim_type == "cube":
        bpy.ops.mesh.primitive_cube_add()
        obj = bpy.context.active_object
        obj.scale = params.get("scale", [1, 1, 1])
        obj.location = params.get("center", [0, 0, 0])

    elif prim_type == "sphere":
        bpy.ops.mesh.primitive_uv_sphere_add()
        obj = bpy.context.active_object
        radius = params.get("radius", 1.0)
        obj.scale = [radius, radius, radius]
        obj.location = params.get("center", [0, 0, 0])

    elif prim_type == "cylinder":
        bpy.ops.mesh.primitive_cylinder_add()
        obj = bpy.context.active_object
        radius = params.get("radius", 1.0)
        height = params.get("height", 2.0)
        obj.scale = [radius, radius, height/2]
        obj.location = params.get("center", [0, 0, 0])

    obj.name = name

    # Create the material
    mat = bpy.data.materials.new(name=f"Material_{name}")
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (*color, 1.0)
    bsdf.inputs["Roughness"].default_value = 0.3
    bsdf.inputs["Metallic"].default_value = 0.3

    # RGB keyboard emission
    if "key_" in name:
        bsdf.inputs["Emission Strength"].default_value = 2.0
        bsdf.inputs["Emission Color"].default_value = (*color, 1.0)

    obj.data.materials.append(mat)
    return obj

def import_asset(asset_data, parent=None, offset=[0, 0, 0]):
    """Recursively import the asset"""
    bpy.ops.object.empty_add(location=offset)
    container = bpy.context.active_object
    container.name = asset_data.get("label", "Asset")

    if parent:
        container.parent = parent

    # Import the primitive
    for prim in asset_data.get("primitives", []):
        obj = create_primitive(
            prim["type"],
            prim["params"],
            prim.get("color", [0.8, 0.8, 0.8]),
            prim.get("alpha", 1.0),
            "Primitive"
        )
        obj.parent = container

    # Recursively import child objects
    for name, child in asset_data.get("children", {}).items():
        import_asset(child, container, offset)

    return container

# Import the model
print("Importing Gaming Laptop (Fixed)...")
laptop_obj = import_asset(data, None, [0, 0, 0])

# Set up the camera (a side view looks better)
bpy.ops.object.camera_add(location=(5, -4, 2))
camera = bpy.context.active_object
camera.rotation_euler = (1.3, 0, 0.8)
bpy.context.scene.camera = camera

# Key light
bpy.ops.object.light_add(type='SUN', location=(3, -3, 5))
sun = bpy.context.active_object
sun.data.energy = 2.0

# Fill light
bpy.ops.object.light_add(type='AREA', location=(0, -2, 2))
area = bpy.context.active_object
area.data.energy = 200

# Set up rendering
for area in bpy.context.screen.areas:
    if area.type == 'VIEW_3D':
        for space in area.spaces:
            if space.type == 'VIEW_3D':
                space.shading.type = 'MATERIAL'

print("✅ Laptop imported!")
print(f"Total objects: {len(list(laptop_obj.children_recursive))}")
'''

script_file = output_dir / "blender_import.py"
script_file.write_text(blender_script, encoding='utf-8')

print(f"🎬 Blender script: {script_file}")
print()

print("=" * 80)
print("🎉 Fixed laptop generated!")
print("=" * 80)
print()
print("📂 Output directory:", output_dir.absolute())
print()
