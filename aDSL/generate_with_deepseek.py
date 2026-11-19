#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Generate aDSL code directly with the DeepSeek API and create a 3D model
"""

import sys
import io
import json
from pathlib import Path
from openai import OpenAI

sys.path.insert(0, str(Path(__file__).parent / "adsl-core"))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# Read the API key
api_key_path = Path(__file__).parent / "adsl-agents/configs/keys/deepseek_key.txt"
api_key = __import__("os").environ.get("DEEPSEEK_API_KEY", "").strip()
if not api_key:
    api_key = api_key_path.read_text().strip()  # legacy key file

# Initialize the DeepSeek client
client = OpenAI(
    api_key=api_key,
    base_url="https://api.deepseek.com/v1"
)

print("=" * 80)
print(" " * 20 + "Generate an aDSL 3D Model with the DeepSeek API")
print("=" * 80)
print()

# Prompt
user_prompt = """请使用 aDSL 语言编写 Python 代码，创建一个现代游戏笔记本电脑的 3D 模型。

要求：
1. 使用 aDSL 的 Asset 类作为容器
2. 使用 Cube, Sphere, Cylinder 等基本图元
3. 包含以下部分：
   - 屏幕 (薄的立方体，较大)
   - 键盘底座 (扁平的立方体)
   - 键盘按键 (多个小立方体，RGB 效果用不同颜色)
   - 触控板 (小的扁平立方体)
   - 散热口 (可以用小圆柱体表示)

4. 使用 attach_part() 方法组装
5. 设置合理的尺寸、位置和颜色

请只返回 Python 代码，从 import 开始，到创建 Asset 对象并命名为 laptop。
不要包含任何解释文字，只要代码。

示例格式：
```python
from core.asset import Asset
from core.primitives import Cube

laptop = Asset(label="Gaming Laptop")
laptop.attach_part("screen", Cube(...))
# 更多部件...
```
"""

print("🤖 Calling the DeepSeek API to generate code...")
print("-" * 80)

try:
    # Call the DeepSeek API
    response = client.chat.completions.create(
        model="deepseek-chat",
        messages=[
            {
                "role": "system",
                "content": "你是一个 3D 建模专家，擅长使用 aDSL 领域特定语言创建 3D 模型。"
            },
            {
                "role": "user",
                "content": user_prompt
            }
        ],
        temperature=0.7,
        max_tokens=2000
    )

    generated_code = response.choices[0].message.content

    print("✅ DeepSeek API call succeeded!")
    print()
    print("📝 Generated code:")
    print("-" * 80)
    print(generated_code)
    print("-" * 80)
    print()

    # Extract the code block
    if "```python" in generated_code:
        code = generated_code.split("```python")[1].split("```")[0].strip()
    elif "```" in generated_code:
        code = generated_code.split("```")[1].split("```")[0].strip()
    else:
        code = generated_code.strip()

    # Save the generated code
    output_dir = Path(__file__).parent / "outputs/deepseek_generated"
    output_dir.mkdir(parents=True, exist_ok=True)

    code_file = output_dir / "laptop_code.py"
    code_file.write_text(code, encoding='utf-8')
    print(f"✅ Code saved: {code_file}")
    print()

    # Run the code to generate the model
    print("🎨 Running the generated code...")
    print("-" * 80)

    exec_globals = {}
    exec(code, exec_globals)

    laptop = exec_globals.get('laptop')

    if laptop:
        print("✅ Successfully created the laptop object!")
        print()
        print("📊 Model info:")
        print(f"  Label: {laptop.label}")
        print(f"  Parts: {len(laptop._children)}")
        print(f"  Part list: {list(laptop._children.keys())}")
        print()

        # Serialize to JSON
        from core.serialize import asset_to_dict

        def numpy_to_python(obj):
            import numpy as np
            if isinstance(obj, np.ndarray):
                return obj.tolist()
            elif isinstance(obj, (np.integer, np.floating)):
                return obj.item()
            return obj

        laptop_dict = asset_to_dict(laptop)

        # Deep-convert numpy types
        laptop_json = json.loads(
            json.dumps(laptop_dict, default=numpy_to_python)
        )

        # Save JSON
        json_file = output_dir / "laptop.json"
        with open(json_file, 'w', encoding='utf-8') as f:
            json.dump(laptop_json, f, indent=2, ensure_ascii=False)

        print(f"✅ JSON saved: {json_file}")
        print(f"   Size: {json_file.stat().st_size} bytes")
        print()

        # Generate the Blender import script
        blender_script = '''import bpy
import json
from pathlib import Path

# Clear the scene
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete()

# Load JSON
json_path = Path(__file__).parent / "laptop.json"
with open(json_path, 'r', encoding='utf-8') as f:
    data = json.load(f)

def create_primitive(prim_type, params, color, alpha):
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

    # Set the material
    mat = bpy.data.materials.new(name="Material")
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (*color, 1.0)
    bsdf.inputs["Roughness"].default_value = 0.5
    obj.data.materials.append(mat)

    return obj

def import_asset(asset_data, parent=None, offset=[0, 0, 0]):
    """Recursively import an asset"""
    # Create an empty object as a container
    bpy.ops.object.empty_add(location=offset)
    container = bpy.context.active_object
    container.name = asset_data.get("label", "Asset")

    if parent:
        container.parent = parent

    # Import basic primitives
    for prim in asset_data.get("primitives", []):
        obj = create_primitive(
            prim["type"],
            prim["params"],
            prim.get("color", [0.8, 0.8, 0.8]),
            prim.get("alpha", 1.0)
        )
        obj.parent = container

    # Recursively import child objects
    for name, child in asset_data.get("children", {}).items():
        import_asset(child, container, offset)

    return container

# Import the model
print("Importing AI-generated laptop model...")
import_asset(data, None, [0, 0, 0])

# Set up the camera
bpy.ops.object.camera_add(location=(5, -5, 4))
camera = bpy.context.active_object
camera.rotation_euler = (1.1, 0, 0.785)
bpy.context.scene.camera = camera

# Add lights
bpy.ops.object.light_add(type='SUN', location=(5, 5, 10))
sun = bpy.context.active_object
sun.data.energy = 2.0

# Set the shading mode
for area in bpy.context.screen.areas:
    if area.type == 'VIEW_3D':
        for space in area.spaces:
            if space.type == 'VIEW_3D':
                space.shading.type = 'MATERIAL'

print("Import completed!")
'''

        script_file = output_dir / "blender_import.py"
        script_file.write_text(blender_script, encoding='utf-8')

        print(f"✅ Blender import script saved: {script_file}")
        print()

        # Generate the usage instructions
        readme = f"""# DeepSeek AI-Generated Gaming Laptop 3D Model

## 🤖 AI Generation Info

- **Model**: DeepSeek Chat
- **Generated at**: {Path(__file__).stat().st_mtime}
- **Object**: Gaming laptop
- **Parts**: {len(laptop._children)}

## 📁 File List

- `laptop_code.py` - aDSL code generated by DeepSeek
- `laptop.json` - Serialized 3D model data
- `blender_import.py` - Blender import script
- `README.md` - This file

## 🎨 Viewing in Blender

1. Open Blender
2. Switch to the **Scripting** workspace
3. Open the file: `blender_import.py`
4. Click ▶️ **Run Script**
5. View the AI-generated 3D model!

## 🔍 Model Structure

```
{laptop.label}
"""
        for part_name in laptop._children.keys():
            readme += f"├── {part_name}\n"

        readme += """```

## 💡 Tips

- Use the middle mouse button to rotate the view
- Press Numpad 0 to switch to the camera view
- Press Z to choose a shading mode (Material Preview recommended)
- Press F12 to render the final image

## 🎯 Technical Details

This model was generated through the following pipeline:

1. **AI planning**: DeepSeek analyzes the requirements and plans the model structure
2. **Code generation**: The AI writes aDSL Python code
3. **Execution**: Python runs the code to create the Asset object
4. **Serialization**: Convert to JSON format
5. **Blender import**: Rebuild it in Blender via a script

---

✨ **Automatically generated by DeepSeek AI** - aDSL paper reproduction project
"""

        readme_file = output_dir / "README.md"
        readme_file.write_text(readme, encoding='utf-8')

        print(f"✅ README saved: {readme_file}")
        print()

        print("=" * 80)
        print("🎉 DeepSeek AI generation complete!")
        print("=" * 80)
        print()
        print("📂 Output directory:", output_dir.absolute())
        print()
        print("🎬 Next steps:")
        print("  1. Open Blender")
        print(f"  2. Run the script: {script_file.name}")
        print("  3. View the AI-generated gaming laptop!")
        print()

        # Usage statistics
        print("📊 API usage statistics:")
        if hasattr(response, 'usage'):
            print(f"  • Prompt tokens: {response.usage.prompt_tokens}")
            print(f"  • Completion tokens: {response.usage.completion_tokens}")
            print(f"  • Total tokens: {response.usage.total_tokens}")
        print()

    else:
        print("❌ Could not get the laptop object from the generated code")

except Exception as e:
    print(f"❌ Error: {e}")
    import traceback
    traceback.print_exc()
