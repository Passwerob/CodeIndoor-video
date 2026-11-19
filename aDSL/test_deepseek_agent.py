#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Test aDSL agent generation using the DeepSeek API
"""

import sys
import io
import os
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

print("=" * 80)
print(" " * 20 + "aDSL + DeepSeek AI generation test")
print("=" * 80)
print()

# Check dependencies
print("📋 Checking environment...")
print("-" * 80)

missing_deps = []

try:
    import openai
    print("✓ openai library installed")
except ImportError:
    missing_deps.append("openai")
    print("✗ openai library not installed")

try:
    from openai_agents import Agent
    print("✓ openai-agents library installed")
except ImportError:
    missing_deps.append("openai-agents")
    print("✗ openai-agents library not installed")

# Check aDSL core
sys.path.insert(0, str(Path(__file__).parent / "adsl-core"))
try:
    from core.asset import Asset
    from core.primitives import Cube, Sphere, Cylinder
    print("✓ aDSL core module available")
except ImportError as e:
    print(f"✗ aDSL core module import failed: {e}")
    missing_deps.append("adsl-core")

print()

if missing_deps:
    print("⚠️  Missing dependency packages")
    print("-" * 80)
    print()
    print("The following dependencies must be installed to use agent generation:")
    for dep in missing_deps:
        print(f"  • {dep}")
    print()
    print("Install command:")
    print("  bash setup_adsl.sh")
    print()
    print("Or install manually:")
    print("  pip install openai openai-agents==0.20.0")
    print("  pip install -e './adsl-core[mesh,render]' --extra-index-url https://download.blender.org/pypi/")
    print()
    sys.exit(1)

# Check API key
print("🔑 Checking DeepSeek API key...")
print("-" * 80)

key_path = Path(__file__).parent / "adsl-agents/configs/keys/deepseek_key.txt"
if key_path.exists():
    api_key = key_path.read_text().strip()
    masked_key = api_key[:8] + "..." + api_key[-4:]
    print(f"✓ API key configured: {masked_key}")
else:
    print("✗ API key not found")
    print(f"  Expected path: {key_path}")
    sys.exit(1)

print()
print("🚀 Preparing to generate 3D objects with DeepSeek...")
print("-" * 80)
print()
print("Example command:")
print("  adsl-run create \"A modern laptop\" \\")
print("    --model-config adsl-agents/configs/llm/deepseek-chat.yaml \\")
print("    --output ./outputs/ai_laptop \\")
print("    --max-rounds 3")
print()
print("This will use the DeepSeek API to automatically:")
print("  1. Plan - Analyze the request and plan the object structure")
print("  2. Code - Generate aDSL code")
print("  3. Execute - Run and render")
print("  4. Critic - Evaluate the result")
print("  5. Refine - Iterate and improve")
print()
print("=" * 80)
print("✅ Environment check complete! Ready to start AI generation")
print("=" * 80)
