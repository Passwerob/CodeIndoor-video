#!/usr/bin/env python
"""
aDSL basic demo script
Demonstrates how to create simple 3D objects with aDSL
"""

import sys
from pathlib import Path

# Add adsl-core to the path
sys.path.insert(0, str(Path(__file__).parent / "adsl-core"))

try:
    from adsl.core.asset import Asset
    from adsl.core.primitives import Sphere, Cube, Cylinder
    from adsl.core.layout import align, stack
    print("✓ aDSL core module imported successfully\n")
except ImportError as e:
    print(f"✗ Import failed: {e}")
    print("\nPlease run first: bash setup_adsl.sh")
    sys.exit(1)


def demo_simple_chair():
    """Demo: create a simple chair"""
    print("=" * 60)
    print("Demo 1: Create a simple chair")
    print("=" * 60)

    # Create the chair asset
    chair = Asset(label="SimpleChair")

    # Seat (cube)
    seat = Cube(scale=(2.0, 0.2, 2.0), color=(0.6, 0.4, 0.2))
    chair.attach_part("seat", seat)

    # Backrest
    backrest = Cube(scale=(2.0, 1.5, 0.2), color=(0.6, 0.4, 0.2))
    chair.attach_part("backrest", backrest)

    # Four legs
    leg_size = (0.2, 1.0, 0.2)
    for i, name in enumerate(["leg_fl", "leg_fr", "leg_bl", "leg_br"]):
        leg = Cube(scale=leg_size, color=(0.5, 0.3, 0.1))
        chair.attach_part(name, leg)

    print(f"✓ Created chair asset: {chair.label}")
    print(f"  - Part count: {len(chair._children)}")
    print(f"  - Part list: {list(chair._children.keys())}")

    return chair


def demo_stacked_objects():
    """Demo: stack objects using spatial relations"""
    print("\n" + "=" * 60)
    print("Demo 2: Using spatial relation operations (stacking)")
    print("=" * 60)

    # Create the scene
    scene = Asset(label="StackedScene")

    # Base
    base = Cube(scale=(3.0, 0.5, 3.0), color=(0.3, 0.3, 0.3))
    scene.attach_part("base", base)

    # Middle layer
    middle = Cylinder(radius=1.0, height=2.0, color=(0.8, 0.2, 0.2))
    scene.attach_part("middle", middle)

    # Top sphere
    top = Sphere(radius=0.8, color=(0.2, 0.8, 0.2))
    scene.attach_part("top", top)

    print(f"✓ Created stacked scene: {scene.label}")
    print(f"  - Parts: {list(scene._children.keys())}")
    print(f"  - Note: aDSL spatial relation operators enable precise relative positioning")

    return scene


def demo_composite_object():
    """Demo: create a composite object (table)"""
    print("\n" + "=" * 60)
    print("Demo 3: Create a composite object (table)")
    print("=" * 60)

    # Table
    table = Asset(label="Table")

    # Tabletop
    tabletop = Cube(scale=(4.0, 0.2, 2.5), color=(0.7, 0.5, 0.3))
    table.attach_part("tabletop", tabletop)

    # Four table legs
    leg_params = (0.2, 1.5, 0.2)
    positions = [
        ("leg_1", (-1.8, -0.85, -1.0)),
        ("leg_2", (1.8, -0.85, -1.0)),
        ("leg_3", (-1.8, -0.85, 1.0)),
        ("leg_4", (1.8, -0.85, 1.0)),
    ]

    for name, pos in positions:
        leg = Cube(scale=leg_params, color=(0.6, 0.4, 0.2))
        table.attach_part(name, leg)

    print(f"✓ Created table: {table.label}")
    print(f"  - Part count: {len(table._children)}")
    print(f"  - Tabletop size: 4.0 × 0.2 × 2.5")
    print(f"  - Leg count: 4")

    return table


def export_to_dict(asset):
    """Export an asset as a dict"""
    print("\n" + "-" * 60)
    print(f"Exporting structure of {asset.label}:")
    print("-" * 60)

    try:
        from adsl.core.serialize import asset_to_dict
        asset_dict = asset_to_dict(asset)

        print(f"  Label: {asset_dict.get('label')}")
        print(f"  Children: {len(asset_dict.get('children', {}))}")
        if 'primitives' in asset_dict:
            print(f"  Primitives: {len(asset_dict['primitives'])}")

        return asset_dict
    except Exception as e:
        print(f"  ✗ Export failed: {e}")
        return None


def main():
    """Main function"""
    print("\n" + "=" * 70)
    print(" " * 15 + "aDSL 3D modeling demo")
    print(" " * 10 + "Reproducing paper: Agentic 3D Creation")
    print("=" * 70 + "\n")

    # Demo 1: simple chair
    chair = demo_simple_chair()
    export_to_dict(chair)

    # Demo 2: stacked objects
    stacked = demo_stacked_objects()
    export_to_dict(stacked)

    # Demo 3: composite object
    table = demo_composite_object()
    export_to_dict(table)

    # Summary
    print("\n" + "=" * 70)
    print("✅ Demo complete!")
    print("=" * 70)
    print("\naDSL core features:")
    print("  1. ✓ Composability(composability) - modular assembly")
    print("  2. ✓ Expressiveness(expressiveness) - rich set of primitives")
    print("  3. ✓ Spatial Reasoning(spatial reasoning) - relation operators")
    print("\nNext steps:")
    print("  • Install full dependencies: bash setup_adsl.sh")
    print("  • Generate with the agent: adsl-run create \"A laptop\" --output ./outputs/laptop")
    print("  • Start interactive chat: adsl-chat --workspace ./outputs/chat")
    print("  • Test Blender integration: python test_blender_integration.py")


if __name__ == "__main__":
    main()
