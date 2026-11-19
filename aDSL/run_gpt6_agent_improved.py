#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Improved version: implements the full agent loop of the aDSL paper using GPT-6-Astra
Includes better error handling and validation
"""

import sys
import io
import json
from pathlib import Path
from openai import OpenAI

sys.path.insert(0, str(Path(__file__).parent / "adsl-core"))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

print("=" * 100)
print(" " * 25 + "aDSL Agent System with GPT-6-Astra (Improved)")
print(" " * 35 + "Plan -> Execute -> Critic Loop")
print("=" * 100)
print()

# API configuration
key_file = Path(__file__).parent / "adsl-agents/configs/keys/gpt6astra_key.txt"
api_key = key_file.read_text().strip()

client = OpenAI(
    api_key=api_key,
    base_url="https://esfaery.com/v1"
)

requirement = "a modern gaming laptop computer with RGB mechanical keyboard and screen standing upright"

print(f"Requirement: {requirement}")
print()

# ==================== Phase 1: Planning ====================
print("=" * 100)
print(" " * 40 + "Phase 1: PLANNING")
print("=" * 100)
print()

planner_prompt = f"""You are a 3D modeling expert using aDSL.

**Task:** Design a gaming laptop for: {requirement}

**aDSL Syntax (IMPORTANT):**
- Asset(label="name") creates container
- asset.attach_part("part_name", Primitive(...))
- Primitives: Cube(scale=(x,y,z), center=(x,y,z), color=(r,g,b))
             Cylinder(radius=r, height=h, center=(x,y,z), color=(r,g,b))

**Coordinate System:**
- Y-axis UP, base at Y=0
- Screen UPRIGHT in Y+ direction
- Screen BEHIND base (Z-)

**Requirements:**
1. Laptop base (keyboard area) - horizontal at Y≈0
2. Screen - vertical, upright in Y+, behind in Z-
3. RGB keyboard - 50+ keys, rainbow colors
4. Details: touchpad, power button, vents, speakers

Output a concise plan (200 words max) describing the structure."""

print("Calling GPT-6-Astra Planner...")
plan_response = client.chat.completions.create(
    model="gpt-6-astra",
    messages=[{"role": "user", "content": planner_prompt}],
    temperature=0.7,
    max_tokens=500
)

plan_text = plan_response.choices[0].message.content.strip()
print()
print("Plan:")
print(plan_text[:500])
print()

# ==================== Phase 2: Execution ====================
print("=" * 100)
print(" " * 40 + "Phase 2: EXECUTION")
print("=" * 100)
print()

coder_prompt = f"""Generate aDSL code for: {requirement}

**Plan:** {plan_text}

**EXACT aDSL API:**
```python
from core.asset import Asset
from core.primitives import Cube, Cylinder, Sphere

laptop = Asset(label="Gaming Laptop")

# CORRECT usage:
laptop.attach_part("base", Cube(
    scale=(3.0, 0.1, 2.0),
    center=(0, 0.05, 0),
    color=(0.15, 0.15, 0.17)
))

laptop.attach_part("screen", Cube(
    scale=(3.0, 2.0, 0.05),
    center=(0, 1.1, -1.0),  # Y+ upright, Z- behind
    color=(0.05, 0.05, 0.06)
))
```

**Requirements:**
1. Create `laptop = Asset(label="Gaming Laptop")`
2. Base at Y=0 (horizontal)
3. Screen upright in Y+, behind in Z-
4. RGB keyboard: 50+ keys, loop with rainbow colors
5. Touchpad, power button (Cylinder), vents, speakers

**Output ONLY valid Python code, no explanations, no markdown fences.**"""

print("Calling GPT-6-Astra Coder...")
code_response = client.chat.completions.create(
    model="gpt-6-astra",
    messages=[{"role": "user", "content": coder_prompt}],
    temperature=0.3,
    max_tokens=3000
)

code = code_response.choices[0].message.content.strip()

# Clean code
if "```python" in code:
    code = code.split("```python")[1].split("```")[0].strip()
elif "```" in code:
    code = code.split("```")[1].split("```")[0].strip()

print()
print("Generated code preview:")
print(code[:600] + "...")
print()

# Save code
output_dir = Path(__file__).parent / "outputs/gpt6_agent_laptop_v2"
output_dir.mkdir(parents=True, exist_ok=True)

code_file = output_dir / "generated_code.py"
code_file.write_text(code, encoding='utf-8')
print(f"Code saved: {code_file}")
print()

# Execute code
max_attempts = 2
attempt = 0
laptop = None

while attempt < max_attempts and laptop is None:
    attempt += 1
    print(f"Execution attempt {attempt}...")

    exec_globals = {}
    try:
        exec(code, exec_globals)

        # Find the laptop
        for name, obj in exec_globals.items():
            if hasattr(obj, 'label') and hasattr(obj, '_children'):
                laptop = obj
                break

        if laptop is None:
            raise ValueError("No laptop object found in generated code")

        print(f"SUCCESS: Created laptop with {len(laptop._children)} parts")
        print()
        break

    except Exception as e:
        print(f"ERROR: {e}")

        if attempt < max_attempts:
            print()
            print("=" * 100)
            print(" " * 40 + f"Phase 3: CRITIC (Attempt {attempt})")
            print("=" * 100)
            print()

            critic_prompt = f"""Debug this aDSL code.

**Error:** {str(e)}

**Code:**
```python
{code[:800]}
```

**aDSL API Rules:**
1. Cube/Cylinder/Sphere take ONLY: scale/radius/height, center, color
2. NO 'name' parameter in primitives
3. attach_part syntax: laptop.attach_part("name_string", Primitive(...))
4. Must create: laptop = Asset(label="...")

Provide COMPLETE fixed code (all parts), not just the bug fix."""

            print("Calling GPT-6-Astra Critic...")
            critic_response = client.chat.completions.create(
                model="gpt-6-astra",
                messages=[{"role": "user", "content": critic_prompt}],
                temperature=0.2,
                max_tokens=3000
            )

            fixed_code = critic_response.choices[0].message.content.strip()

            # Extract code
            if "```python" in fixed_code:
                fixed_code = fixed_code.split("```python")[1].split("```")[0].strip()
            elif "```" in fixed_code:
                fixed_code = fixed_code.split("```")[1].split("```")[0].strip()

            print("Fixed code preview:")
            print(fixed_code[:600] + "...")
            print()

            code = fixed_code
            code_file.write_text(code, encoding='utf-8')
        else:
            print(f"Failed after {max_attempts} attempts")
            sys.exit(1)

if laptop is None:
    print("ERROR: Could not generate valid laptop model")
    sys.exit(1)

# ==================== Phase 4: Serialization ====================
print()
print("=" * 100)
print(" " * 40 + "Phase 4: SERIALIZATION")
print("=" * 100)
print()

from core.serialize import asset_to_dict
import numpy as np

def numpy_to_python(obj):
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    elif isinstance(obj, (np.integer, np.floating)):
        return obj.item()
    return obj

laptop_dict = asset_to_dict(laptop)
laptop_json = json.loads(json.dumps(laptop_dict, default=numpy_to_python))

json_file = output_dir / "laptop.json"
with open(json_file, 'w', encoding='utf-8') as f:
    json.dump(laptop_json, f, indent=2, ensure_ascii=False)

size_kb = json_file.stat().st_size / 1024
print(f"Model saved: {json_file}")
print(f"Size: {size_kb:.1f} KB")
print(f"Parts: {len(laptop._children)}")
print()

# Generate the Blender script (same as before)
blender_script = '''import bpy
import json
from pathlib import Path
import math

bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete()

json_path = Path(__file__).parent / "laptop.json"
with open(json_path, 'r', encoding='utf-8') as f:
    data = json.load(f)

print("=" * 80)
print("Importing GPT-6-Astra Generated Laptop V2")
print("=" * 80)

def create_material(name, color, is_emissive=False):
    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    nodes.clear()
    output = nodes.new('ShaderNodeOutputMaterial')
    bsdf = nodes.new('ShaderNodeBsdfPrincipled')
    bsdf.inputs['Base Color'].default_value = (*color, 1.0)
    bsdf.inputs['Roughness'].default_value = 0.3
    bsdf.inputs['Metallic'].default_value = 0.4
    if is_emissive:
        bsdf.inputs[26].default_value = (*color, 1.0)
        bsdf.inputs[27].default_value = 3.0
    mat.node_tree.links.new(bsdf.outputs[0], output.inputs[0])
    return mat

def create_cube(scale, center, color, name, is_emissive=False):
    bpy.ops.mesh.primitive_cube_add(size=1, location=center)
    obj = bpy.context.active_object
    obj.name = name
    obj.scale = scale
    mat = create_material(f"Mat_{name}", color, is_emissive)
    obj.data.materials.append(mat)
    return obj

def create_cylinder(radius, height, center, color, name, is_emissive=False):
    bpy.ops.mesh.primitive_cylinder_add(radius=radius, depth=height, location=center)
    obj = bpy.context.active_object
    obj.name = name
    mat = create_material(f"Mat_{name}", color, is_emissive)
    obj.data.materials.append(mat)
    return obj

def create_sphere(radius, center, color, name, is_emissive=False):
    bpy.ops.mesh.primitive_uv_sphere_add(radius=radius, location=center)
    obj = bpy.context.active_object
    obj.name = name
    mat = create_material(f"Mat_{name}", color, is_emissive)
    obj.data.materials.append(mat)
    return obj

objects_created = 0
for part_name, part_data in data.get('children', {}).items():
    primitives = part_data.get('primitives', [])
    for prim in primitives:
        prim_type = prim['type']
        params = prim['params']
        color = prim.get('color', [0.8, 0.8, 0.8])
        is_key = part_name.startswith('key_')
        try:
            if prim_type == 'cube':
                create_cube(params.get('scale', [1, 1, 1]), params.get('center', [0, 0, 0]), color, part_name, is_key)
                objects_created += 1
            elif prim_type == 'cylinder':
                create_cylinder(params.get('radius', 1.0), params.get('height', 2.0), params.get('center', [0, 0, 0]), color, part_name, is_key)
                objects_created += 1
            elif prim_type == 'sphere':
                create_sphere(params.get('radius', 1.0), params.get('center', [0, 0, 0]), color, part_name, is_key)
                objects_created += 1
        except Exception as e:
            print(f"Error: {part_name}: {e}")

print(f"Created {objects_created} objects")

bpy.ops.object.camera_add(location=(6, -5, 3))
camera = bpy.context.active_object
camera.rotation_euler = (math.radians(70), 0, math.radians(50))
bpy.context.scene.camera = camera

bpy.ops.object.light_add(type='SUN', location=(4, -4, 6))
bpy.context.active_object.data.energy = 2.5

bpy.ops.object.light_add(type='AREA', location=(-2, -3, 3))
bpy.context.active_object.data.energy = 300

for area in bpy.context.screen.areas:
    if area.type == 'VIEW_3D':
        for space in area.spaces:
            if space.type == 'VIEW_3D':
                space.shading.type = 'MATERIAL'

print("=" * 80)
print("GPT-6-Astra Laptop V2 imported!")
'''

script_file = output_dir / "blender_import.py"
script_file.write_text(blender_script, encoding='utf-8')
print(f"Blender script: {script_file}")
print()

# Summary
print("=" * 100)
print(" " * 35 + "COMPLETION SUMMARY")
print("=" * 100)
print()
print("Paper Method (aDSL) Verified with GPT-6-Astra:")
print("  Phase 1: Planning     - Structural design")
print("  Phase 2: Execution    - aDSL code generation")
print("  Phase 3: Critic       - Automatic debugging")
print("  Phase 4: Serialization - JSON + Blender export")
print()
print(f"Output: {output_dir.absolute()}")
print(f"  - laptop.json ({size_kb:.1f} KB, {len(laptop._children)} parts)")
print(f"  - blender_import.py")
print(f"  - generated_code.py")
print()
print("To view in Blender:")
print(f'  blender --python "{script_file.absolute()}"')
print()
print("=" * 100)
