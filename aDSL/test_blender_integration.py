#!/usr/bin/env python
"""Test the integration of aDSL with Blender"""

import sys
from pathlib import Path

def test_imports():
    """Test core module imports"""
    print("Testing module imports...")

    try:
        import adsl
        print("✓ adsl module imported successfully")
    except ImportError as e:
        print(f"✗ adsl module import failed: {e}")
        return False

    try:
        from adsl.core.asset import Asset
        from adsl.core.primitives import cube, sphere, cylinder
        print("✓ aDSL core components imported successfully")
    except ImportError as e:
        print(f"✗ aDSL core components import failed: {e}")
        return False

    try:
        import bpy
        print(f"✓ Blender Python (bpy) imported successfully")
        print(f"  Blender version: {bpy.app.version_string}")
    except ImportError as e:
        print(f"✗ Blender Python import failed: {e}")
        print("  Hint: bpy==4.0.0 must be installed")
        return False

    return True

def test_basic_creation():
    """Test basic 3D object creation"""
    print("\nTesting basic object creation...")

    try:
        from adsl.core.asset import Asset
        from adsl.core.primitives import cube

        # Create a simple cube
        test_asset = Asset(label="TestCube")
        test_asset.attach_part("box", cube(size=(1, 1, 1)))

        print("✓ Test cube created successfully")
        print(f"  Object: {test_asset.label}")
        print(f"  Part count: {len(test_asset._children)}")
        return True

    except Exception as e:
        print(f"✗ Object creation failed: {e}")
        return False

def main():
    print("=" * 60)
    print("aDSL + Blender integration test")
    print("=" * 60)

    # Test imports
    if not test_imports():
        print("\n❌ Import test failed, please run the install script first")
        sys.exit(1)

    # Test basic creation
    if not test_basic_creation():
        print("\n❌ Object creation test failed")
        sys.exit(1)

    print("\n" + "=" * 60)
    print("✅ All tests passed! aDSL is now integrated with Blender")
    print("=" * 60)
    print("\nNext steps:")
    print("1. Generate 3D from text: adsl-run create \"A chair\" --output ./outputs/chair")
    print("2. Generate 3D from an image: adsl-run create \"Build this\" --image <path> --output ./outputs/obj")
    print("3. Start interactive chat: adsl-chat --workspace ./outputs/chat")

if __name__ == "__main__":
    main()
