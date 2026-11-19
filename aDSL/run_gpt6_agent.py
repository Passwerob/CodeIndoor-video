#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Implements the full agent loop of the aDSL paper using GPT-6-Astra
Plan → Execute → Critic → Refine
"""

import sys
import io
import json
from pathlib import Path
from openai import OpenAI

sys.path.insert(0, str(Path(__file__).parent / "adsl-core"))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

print("=" * 100)
print(" " * 30 + "aDSL Agent System with GPT-6-Astra")
print(" " * 35 + "Plan → Execute → Critic Loop")
print("=" * 100)
print()

# Read the API key
key_file = Path(__file__).parent / "adsl-agents/configs/keys/gpt6astra_key.txt"
api_key = key_file.read_text().strip()

import os
os.environ['OPENAI_API_KEY'] = api_key

client = OpenAI(
    api_key=api_key,
    base_url="https://esfaery.com/v1"  # append the /v1 suffix (OpenAI-compatible format)
)

requirement = "a modern gaming laptop computer with RGB mechanical keyboard and screen standing upright"

print(f"Requirement: {requirement}")
print()

# ==================== Phase 1: Planning ====================
print("=" * 100)
print(" " * 40 + "Phase 1: PLANNING")
print("=" * 100)
print()

planner_prompt = f"""You are a 3D modeling expert using aDSL (a Python DSL for 3D creation).

**User Request:** {requirement}

**Your Task:** Create a detailed plan for building this 3D model.

**aDSL Components:**
- Asset(label="name") - container
- Cube(scale=(x,y,z), center=(x,y,z), color=(r,g,b))
- Cylinder(radius=r, height=h, center=(x,y,z), color=(r,g,b))
- Sphere(radius=r, center=(x,y,z), color=(r,g,b))
- asset.attach_part("part_name", primitive)

**Coordinate System:**
- Y-axis is UP (vertical)
- Base at Y=0
- Screen should stand UPRIGHT in Y+ direction
- Screen should be BEHIND base (Z- direction)

**Output a JSON plan:**
```json
{{
  "components": [
    {{"name": "base", "description": "...", "primitives": [...]}},
    {{"name": "screen", "description": "...", "primitives": [...]}},
    ...
  ],
  "assembly_strategy": "...",
  "coordinate_system": "Y-up, screen in Y+ and Z-"
}}
```

Only output valid JSON, no other text."""

print("Calling GPT-6-Astra Planner...")
plan_response = client.chat.completions.create(
    model="gpt-6-astra",
    messages=[{"role": "user", "content": planner_prompt}],
    temperature=0.7,
    max_tokens=2000
)

plan_text = plan_response.choices[0].message.content.strip()

# Extract JSON
if "```json" in plan_text:
    plan_text = plan_text.split("```json")[1].split("```")[0].strip()
elif "```" in plan_text:
    plan_text = plan_text.split("```")[1].split("```")[0].strip()

try:
    plan = json.loads(plan_text)
    print()
    print("Plan received:")
    print(json.dumps(plan, indent=2, ensure_ascii=False)[:500] + "...")
    print()
except json.JSONDecodeError as e:
    print(f"Failed to parse plan JSON: {e}")
    print(f"Raw response: {plan_text[:500]}")
    sys.exit(1)

# ==================== Phase 2: Execution ====================
print("=" * 100)
print(" " * 40 + "Phase 2: EXECUTION")
print("=" * 100)
print()

coder_prompt = f"""You are an aDSL code generator.

**Plan:**
{json.dumps(plan, indent=2, ensure_ascii=False)}

**User Requirement:** {requirement}

**Generate executable Python code using aDSL:**

1. Import from core modules:
```python
from core.asset import Asset
from core.primitives import Cube, Cylinder, Sphere
```

2. Create the laptop model according to the plan
3. Use correct coordinate system (Y-up, screen upright)
4. Include RGB keyboard (at least 50 keys, rainbow colors)
5. Add details: touchpad, power button, vents, speakers

**Output ONLY Python code, starting with imports. No explanations.**"""

print("Calling GPT-6-Astra Coder...")
code_response = client.chat.completions.create(
    model="gpt-6-astra",
    messages=[{"role": "user", "content": coder_prompt}],
    temperature=0.5,
    max_tokens=3000
)

code = code_response.choices[0].message.content.strip()

# Extract code
if "```python" in code:
    code = code.split("```python")[1].split("```")[0].strip()
elif "```" in code:
    code = code.split("```")[1].split("```")[0].strip()

print()
print("Generated code preview:")
print(code[:800] + "...")
print()

# Save code
output_dir = Path(__file__).parent / "outputs/gpt6_agent_laptop"
output_dir.mkdir(parents=True, exist_ok=True)

code_file = output_dir / "generated_code.py"
code_file.write_text(code, encoding='utf-8')
print(f"Code saved: {code_file}")
print()

# Execute code
print("Executing generated code...")
exec_globals = {}
try:
    exec(code, exec_globals)

    # Find the laptop object
    laptop = None
    for name, obj in exec_globals.items():
        if hasattr(obj, 'label') and hasattr(obj, '_children'):
            laptop = obj
            break

    if laptop is None:
        print("ERROR: No laptop object found in generated code")
        sys.exit(1)

    print(f"SUCCESS: Created laptop with {len(laptop._children)} parts")
    print()

except Exception as e:
    print(f"EXECUTION ERROR: {e}")
    import traceback
    traceback.print_exc()

    # ==================== Phase 3: Critic & Debug ====================
    print()
    print("=" * 100)
    print(" " * 40 + "Phase 3: CRITIC & DEBUG")
    print("=" * 100)
    print()

    critic_prompt = f"""You are a debugging expert for aDSL code.

**Error:**
{str(e)}

**Generated Code:**
{code[:1000]}

**Identify the bug and provide a FIX:**
1. What went wrong?
2. Corrected code snippet

Output as:
BUG: ...
FIX:
```python
<corrected code>
```"""

    print("Calling GPT-6-Astra Critic...")
    critic_response = client.chat.completions.create(
        model="gpt-6-astra",
        messages=[{"role": "user", "content": critic_prompt}],
        temperature=0.3,
        max_tokens=2000
    )

    fix_text = critic_response.choices[0].message.content.strip()
    print()
    print("Critic feedback:")
    print(fix_text[:500])
    print()

    # Extract the fixed code
    if "```python" in fix_text:
        fixed_code = fix_text.split("```python")[1].split("```")[0].strip()
    elif "```" in fix_text:
        fixed_code = fix_text.split("```")[1].split("```")[0].strip()
    else:
        print("Could not extract fixed code, manual intervention needed")
        sys.exit(1)

    # Re-run the fixed code
    print("Re-executing fixed code...")
    exec_globals = {}
    try:
        exec(fixed_code, exec_globals)
        laptop = None
        for name, obj in exec_globals.items():
            if hasattr(obj, 'label') and hasattr(obj, '_children'):
                laptop = obj
                break

        if laptop is None:
            print("ERROR: Still no laptop object after fix")
            sys.exit(1)

        print(f"SUCCESS after fix: {len(laptop._children)} parts")
        code = fixed_code  # use the fixed code

    except Exception as e2:
        print(f"STILL FAILED: {e2}")
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

# Generate the Blender script
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
print("Importing GPT-6-Astra Generated Laptop")
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
                scale = params.get('scale', [1, 1, 1])
                center = params.get('center', [0, 0, 0])
                create_cube(scale, center, color, part_name, is_key)
                objects_created += 1

            elif prim_type == 'cylinder':
                radius = params.get('radius', 1.0)
                height = params.get('height', 2.0)
                center = params.get('center', [0, 0, 0])
                create_cylinder(radius, height, center, color, part_name, is_key)
                objects_created += 1

            elif prim_type == 'sphere':
                radius = params.get('radius', 1.0)
                center = params.get('center', [0, 0, 0])
                create_sphere(radius, center, color, part_name, is_key)
                objects_created += 1
        except Exception as e:
            print(f"Error creating {part_name}: {e}")

print(f"Created {objects_created} objects")

bpy.ops.object.camera_add(location=(6, -5, 3))
camera = bpy.context.active_object
camera.rotation_euler = (math.radians(70), 0, math.radians(50))
bpy.context.scene.camera = camera

bpy.ops.object.light_add(type='SUN', location=(4, -4, 6))
sun = bpy.context.active_object
sun.data.energy = 2.5

bpy.ops.object.light_add(type='AREA', location=(-2, -3, 3))
area = bpy.context.active_object
area.data.energy = 300

for area in bpy.context.screen.areas:
    if area.type == 'VIEW_3D':
        for space in area.spaces:
            if space.type == 'VIEW_3D':
                space.shading.type = 'MATERIAL'

print("=" * 80)
print("GPT-6-Astra Laptop imported!")
print("=" * 80)
'''

script_file = output_dir / "blender_import.py"
script_file.write_text(blender_script, encoding='utf-8')

print(f"Blender script: {script_file}")
print()

# ==================== Summary ====================
print("=" * 100)
print(" " * 35 + "COMPLETION SUMMARY")
print("=" * 100)
print()
print("Paper Method Verification:")
print("  Phase 1: Planning     - GPT-6-Astra generated structural plan")
print("  Phase 2: Execution    - GPT-6-Astra generated aDSL code")
print("  Phase 3: Critic       - GPT-6-Astra debugged errors (if any)")
print("  Phase 4: Serialization - JSON export + Blender script")
print()
print(f"Output: {output_dir.absolute()}")
print(f"  - laptop.json ({size_kb:.1f} KB, {len(laptop._children)} parts)")
print(f"  - blender_import.py")
print(f"  - generated_code.py")
print()
print("=" * 100)
