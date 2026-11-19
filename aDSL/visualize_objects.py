#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
aDSL object visualization preview
Generates ASCII art and structure diagrams to show the created 3D objects
"""

import sys
import io
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

print("=" * 80)
print(" " * 25 + "aDSL 3D object visualization preview")
print("=" * 80)
print()

# ASCII art of the chair
print("🪑 Chair")
print("━" * 80)
print("""
                    Backrest
                    ┌────────────┐
                    │            │
                    │            │
                    │            │
                    │            │
                    │            │
                    └────┬───┬───┘
                         │   │
    Seat                 │   │
    ┌────────────────────┴───┴────┐
    │                              │
    └─┬──────────────────────────┬─┘
      │                          │
   Leg│                          │Leg
      │                          │
    ┌─┴─┐                      ┌─┴─┐
    │   │                      │   │
    │   │                      │   │
    └───┘                      └───┘

Structure:
  Chair (container)
  ├── seat (seat) - Cube(2.0 × 0.2 × 2.0) - brown
  ├── backrest (backrest) - Cube(2.0 × 1.5 × 0.2) - brown
  ├── leg_0 (front left leg) - Cube(0.2 × 1.0 × 0.2) - dark brown
  ├── leg_1 (front right leg) - Cube(0.2 × 1.0 × 0.2) - dark brown
  ├── leg_2 (rear left leg) - Cube(0.2 × 1.0 × 0.2) - dark brown
  └── leg_3 (rear right leg) - Cube(0.2 × 1.0 × 0.2) - dark brown

Total parts: 6
Color: brown tones (RGB: 0.6, 0.4, 0.2)
""")

# ASCII art of the table
print()
print("🪑 Table")
print("━" * 80)
print("""
    Tabletop
    ┌──────────────────────────────────────┐
    │                                      │
    │                                      │
    └──┬─────────────────────────────────┬─┘
       │                                 │
    Leg│                                 │Leg
       │                                 │
     ┌─┴─┐                             ┌─┴─┐
     │   │                             │   │
     │   │                             │   │
     │   │                             │   │
     └───┘                             └───┘

Structure:
  Table (container)
  ├── top (tabletop) - Cube(4.0 × 0.2 × 2.5) - light brown
  ├── leg_0 (leg 1) - Cube(0.2 × 1.5 × 0.2) - brown
  ├── leg_1 (leg 2) - Cube(0.2 × 1.5 × 0.2) - brown
  ├── leg_2 (leg 3) - Cube(0.2 × 1.5 × 0.2) - brown
  └── leg_3 (leg 4) - Cube(0.2 × 1.5 × 0.2) - brown

Total parts: 5
Tabletop size: 4.0 × 0.2 × 2.5 (width × height × depth)
Color: light brown (RGB: 0.7, 0.5, 0.3)
""")

# ASCII art of the lamp
print()
print("💡 Lamp")
print("━" * 80)
print("""
        Shade
         ___
       /     \\
      |   ○   |  <- spherical shade
       \\_____/
          │
          │  Pole
          │
          │
          │
          │
       ┌──┴──┐
       │     │  <- Base
       └─────┘

Structure:
  Lamp (container)
  ├── base (base) - Cylinder(radius=0.3, height=0.1) - gray
  ├── pole (pole) - Cylinder(radius=0.05, height=2.0) - gray
  └── shade (shade) - Sphere(radius=0.4) - beige

Total parts: 3
Height: ~2.5 units
Color: base/pole gray, shade beige (RGB: 0.9, 0.9, 0.7)
""")

# Scene layout
print()
print("🏠 Full scene layout (arrangement in Blender)")
print("━" * 80)
print("""
                    Top View

    ┌─────────────┬─────────────┬─────────────┐
    │             │             │             │
    │    Chair    │    Table    │    Lamp     │
    │             │             │             │
    │   🪑        │    🪑       │     💡      │
    │             │             │             │
    │  (0,0,0)    │  (6,0,0)    │  (12,0,0)   │
    │             │             │             │
    └─────────────┴─────────────┴─────────────┘

    X axis →
    (horizontal spacing: 6 units)

Side View

    💡
     │
     │ shade
     │
     │ pole
     │
    ═╧═ base

    🪑 Chair        🪑 Table
    │ │
    ├─┤ backrest       ═══ tabletop
    ├─┤ seat       │ │
    │ │           │ │ leg
    │ │ leg        │ │

""")

# Size comparison
print()
print("📏 Size comparison")
print("━" * 80)
print("""
Object        Width(X)  Height(Y)  Depth(Z)  Volume
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Chair seat      2.0       0.2        2.0     ▓▓▓░░
Chair back      2.0       1.5        0.2     ▓▓▓░░
Tabletop        4.0       0.2        2.5     ▓▓▓▓░
Lamp base       0.6Ø      0.1        0.6Ø    ▓░░░░
Lamp pole       0.1Ø      2.0        0.1Ø    ░░░░░
Lamp shade      0.8Ø      0.8Ø       0.8Ø    ▓░░░░

Ø = diameter
▓ = relative size indicator
""")

# Color scheme
print()
print("🎨 Color scheme")
print("━" * 80)
print("""
Object             RGB value           Color preview   Material
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Chair seat/back  (0.6, 0.4, 0.2)     ███  Brown      Wood
Chair legs      (0.5, 0.3, 0.1)     ███  Dark brown Wood
Tabletop        (0.7, 0.5, 0.3)     ███  Light brown Wood
Table legs      (0.6, 0.4, 0.2)     ███  Brown      Wood
Lamp base/pole  (0.3, 0.3, 0.3)     ███  Dark gray  Metal
Lamp shade      (0.9, 0.9, 0.7)     ███  Beige      Glass/plastic
""")

# Effect in Blender
print()
print("🎬 Expected effect in Blender")
print("━" * 80)
print("""
1. Material rendering:
   • All objects have colored materials
   • Principled BSDF shader
   • Roughness 0.5 (semi-gloss)

2. Lighting:
   • Sun Light (Sun Light)
   • Energy: 2.0
   • Location: (5, 5, 10)

3. Camera:
   • Location: (10, -10, 8)
   • Angle: tilted down 45°
   • Perspective projection

4. Layout:
   • Three objects arranged horizontally
   • Spacing: 6 units
   • The camera can see all three objects
""")

# Interaction tips
print()
print("🎮 Blender usage tips")
print("━" * 80)
print("""
View controls:
  • Middle mouse drag → orbit view
  • Shift+middle mouse → pan view
  • Scroll wheel → zoom

Quick views:
  • Numpad 0 → camera view (render preview)
  • Numpad 1 → front view
  • Numpad 3 → right view
  • Numpad 7 → top view

Render modes:
  • Z key → open the shading mode menu
    - Solid (solid)
    - Material Preview (material preview) ← recommended
    - Rendered (full render)
  • F12 → render the final image

Selecting objects:
  • Left-click an object
  • Press G to move
  • Press R to rotate
  • Press S to scale
""")

print()
print("=" * 80)
print("✅ Now run outputs/blender_import.py in Blender to see the real 3D models!")
print("=" * 80)
print()
print("💡 Hint: these ASCII diagrams are only illustrative; the real 3D models in Blender will have:")
print("   • A full 3D look")
print("   • Smooth surfaces and shadows")
print("   • Realistic materials and colors")
print("   • Can be viewed from any angle")
print()
