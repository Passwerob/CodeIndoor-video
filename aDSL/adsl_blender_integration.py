# -*- coding: utf-8 -*-
"""
aDSL-Blender integration script
Converts objects created with aDSL into a format Blender can import
"""

import sys
import io
from pathlib import Path
import json

# Set UTF-8 output
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# Add adsl-core to the path
adsl_core_path = Path(__file__).parent / "adsl-core"
sys.path.insert(0, str(adsl_core_path))

from core.asset import Asset
from core.primitives import Cube, Sphere, Cylinder
from core.serialize import asset_to_dict

print("=" * 70)
print(" " * 15 + "aDSL → Blender integration demo")
print("=" * 70 + "\n")

# Create the output directory
output_dir = Path("outputs")
output_dir.mkdir(exist_ok=True)

def create_sample_objects():
    """Create sample objects"""

    # 1. Chair
    chair = Asset(label="Chair")
    chair.attach_part("seat", Cube(scale=(2.0, 0.2, 2.0), color=(0.6, 0.4, 0.2)))
    chair.attach_part("backrest", Cube(scale=(2.0, 1.5, 0.2), color=(0.6, 0.4, 0.2)))
    for i, pos in enumerate([(-0.9, -0.6, -0.9), (0.9, -0.6, -0.9),
                              (-0.9, -0.6, 0.9), (0.9, -0.6, 0.9)]):
        chair.attach_part(f"leg_{i}", Cube(scale=(0.2, 1.0, 0.2), color=(0.5, 0.3, 0.1)))

    # 2. Table
    table = Asset(label="Table")
    table.attach_part("top", Cube(scale=(4.0, 0.2, 2.5), color=(0.7, 0.5, 0.3)))
    for i in range(4):
        table.attach_part(f"leg_{i}", Cube(scale=(0.2, 1.5, 0.2), color=(0.6, 0.4, 0.2)))

    # 3. Lamp
    lamp = Asset(label="Lamp")
    lamp.attach_part("base", Cylinder(radius=0.3, height=0.1, color=(0.3, 0.3, 0.3)))
    lamp.attach_part("pole", Cylinder(radius=0.05, height=2.0, color=(0.4, 0.4, 0.4)))
    lamp.attach_part("shade", Sphere(radius=0.4, color=(0.9, 0.9, 0.7)))

    return {"chair": chair, "table": table, "lamp": lamp}

def export_to_json(asset, filepath):
    """Export assets as JSON"""
    data = asset_to_dict(asset)
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    return data

def generate_blender_python_script(objects_dict, output_script="blender_import.py"):
    """Generate a Python script that can run inside Blender"""

    script = '''# -*- coding: utf-8 -*-
"""
Run this script in Blender to import aDSL objects
Usage:
1. Open Blender
2. Switch to the Scripting workspace
3. Open this script
4. Click Run or press Alt+P
"""

import bpy
import json
from pathlib import Path

# Clear the scene
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete()

def create_primitive(prim_data):
    """Create a Blender object from primitive data"""
    ptype = prim_data.get("type")
    params = prim_data.get("params", {})
    color = prim_data.get("color", [1.0, 1.0, 1.0])

    if ptype == "cube":
        scale = params.get("scale", [1, 1, 1])
        center = params.get("center", [0, 0, 0])
        bpy.ops.mesh.primitive_cube_add(size=1, location=center)
        obj = bpy.context.active_object
        obj.scale = scale

    elif ptype == "sphere":
        radius = params.get("radius", 1.0)
        center = params.get("center", [0, 0, 0])
        bpy.ops.mesh.primitive_uv_sphere_add(radius=radius, location=center)
        obj = bpy.context.active_object

    elif ptype == "cylinder":
        radius = params.get("radius", 1.0)
        height = params.get("height", 2.0)
        center = params.get("center", [0, 0, 0])
        bpy.ops.mesh.primitive_cylinder_add(radius=radius, depth=height, location=center)
        obj = bpy.context.active_object

    else:
        return None

    # Set the material color
    mat = bpy.data.materials.new(name="Material")
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (*color, 1.0)
    obj.data.materials.append(mat)

    return obj

def import_asset(asset_data, parent=None):
    """Recursively import an asset and its child parts"""
    label = asset_data.get("label", "Asset")

    # Create an empty object as a container
    bpy.ops.object.empty_add(location=(0, 0, 0))
    container = bpy.context.active_object
    container.name = label

    if parent:
        container.parent = parent

    # Create the primitive
    for prim_data in asset_data.get("primitives", []):
        obj = create_primitive(prim_data)
        if obj:
            obj.parent = container

    # Recursively process child parts
    for child_name, child_data in asset_data.get("children", {}).items():
        import_asset(child_data, parent=container)

    return container

# Import all objects
'''

    # Add the import code for each object
    for name, asset in objects_dict.items():
        data = asset_to_dict(asset)
        script += f'\nprint("Importing {name}...")\n'
        script += f'{name}_data = {json.dumps(data, indent=2)}\n'
        script += f'import_asset({name}_data)\n'

    script += '''
print("Import complete!")
print("Hint: press Z to open the shading mode menu, then choose 'Material Preview' or 'Rendered' to see colors")
'''

    output_path = output_dir / output_script
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(script)

    return output_path

# Main flow
print("Step 1: Create sample objects")
print("-" * 70)
objects = create_sample_objects()
for name, obj in objects.items():
    print(f"  ✓ {name}: {len(obj._children)} parts")

print("\nStep 2: Export as JSON")
print("-" * 70)
for name, obj in objects.items():
    json_path = output_dir / f"{name}.json"
    export_to_json(obj, json_path)
    print(f"  ✓ {name} → {json_path}")

print("\nStep 3: Generate the Blender import script")
print("-" * 70)
script_path = generate_blender_python_script(objects)
print(f"  ✓ Blender script → {script_path}")

print("\n" + "=" * 70)
print("✅ aDSL → Blender integration complete!")
print("=" * 70)

print("\n📁 Generated files:")
print(f"  • {output_dir}/chair.json")
print(f"  • {output_dir}/table.json")
print(f"  • {output_dir}/lamp.json")
print(f"  • {output_dir}/blender_import.py")

print("\n🎯 Usage in Blender:")
print("  1. Open Blender")
print("  2. Switch to the Scripting workspace (top menu)")
print("  3. Click 'Open' and open blender_import.py")
print("  4. Click 'Run Script' or press Alt+P")
print("  5. The objects will be imported into the scene automatically")

print("\n💡 Tips:")
print("  • In Blender, press Z to choose a shading mode to see colors")
print("  • Use the middle mouse button to orbit the view")
print("  • Press numpad 7 for the top view")
print("  • Press numpad 1 for the front view")

print("\n🔗 aDSL → Blender workflow:")
print("  aDSL (high-level semantics)")
print("    ↓ asset_to_dict()")
print("  JSON (intermediate format)")
print("    ↓ Blender Python API")
print("  Blender Scene (3D rendering)")

print("\n📝 Paper reproduction summary:")
print("  ✓ aDSL core language - composability, expressiveness, hierarchy")
print("  ✓ Basic primitives - Cube, Sphere, Cylinder")
print("  ✓ Object composition - attach_part mechanism")
print("  ✓ Serialization - asset_to_dict")
print("  ✓ Blender integration - import via the Python API")
