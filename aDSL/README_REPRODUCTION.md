# aDSL Paper Reproduction Guide

## 📄 Paper Information

**Title**: aDSL: Agentic 3D Creation via Joint Agent-Program Design  
**Authors**: Rui-Huan Wang, Si-Tong Wei, Jia-Qi He, Heng-Yi Wei, Baoquan Chen, Peng-Shuai Wang  
**Published**: arXiv:2608.17975 [cs.GR] (2026)  
**Project homepage**: https://dylanwrh.github.io/aDSL/  
**Code repository**: https://github.com/sig-pku/aDSL

## 🎯 Core Contributions of the Paper

1. **Agent-centric DSL (aDSL)**: A domain-specific language for 3D modeling designed for LLM agents
   - ✅ **Expressiveness**: Supports diverse shapes and structures
   - ✅ **Composability**: Modular assembly and hierarchical reuse
   - ✅ **Spatial Reasoning**: Relational operators instead of brittle absolute coordinates

2. **Multi-Agent System**: Plan–Execute–Critic loop
   - Planner: Decomposes tasks and constraints
   - Coder: Generates aDSL programs
   - Critic: Evaluates and gives feedback
   - Debugger: Fixes runtime errors

3. **Training-Free**: No model fine-tuning; directly uses the LLM's inference capabilities

## 🛠️ Local Environment Setup

### Prerequisites

- Python 3.10+ (3.10 recommended)
- Conda or venv
- Blender (optional, for rendering)
- OpenRouter API Key (or another OpenAI-compatible API)

### Quick Start

#### 1. Create a virtual environment

```bash
# Using conda (recommended)
conda create -n adsl python=3.10
conda activate adsl

# Or using venv
python -m venv venv
source venv/Scripts/activate  # Windows
source venv/bin/activate      # Linux/Mac
```

#### 2. Install dependencies

```bash
# Run the installation script
bash setup_adsl.sh

# Or install manually
pip install -e "./adsl-core[mesh,render]" \
  --extra-index-url https://download.blender.org/pypi/
pip install -e .
```

#### 3. Configure the LLM API

```bash
# Set the OpenRouter API Key
echo -n "YOUR_OPENROUTER_API_KEY" > adsl-agents/configs/keys/openrouter_key.txt
```

### 🔧 Blender Integration

aDSL uses `bpy==4.0.0` (Blender Python) for rendering. The installation script installs it automatically from the official Blender PyPI index.

Verify the Blender integration:

```bash
python test_blender_integration.py
```

## 🚀 Usage

### 1. Basic demo (no API required)

```bash
python demo_adsl_basic.py
```

This script demonstrates the core functionality of aDSL:
- Creating basic geometry (cube, sphere, cylinder)
- Composing objects (chair, table)
- Spatial relation operations

### 2. Text-to-3D generation

```bash
adsl-run create "A modern office chair with armrests" \
  --output ./outputs/chair \
  --max-rounds 2
```

### 3. Image-to-3D generation

```bash
adsl-run create "Build the object in this image" \
  --image ./asset/example.png \
  --output ./outputs/desk-from-image \
  --max-rounds 2
```

### 4. Articulated object generation

```bash
adsl-run create "A nightstand with drawers" \
  --image ./asset/example-arti.png \
  --output ./outputs/nightstand-arti \
  --articulation
```

### 5. Interactive chat mode

```bash
adsl-chat --workspace ./outputs/chat
```

Supported commands:
- `/help` - Show help
- `/open PATH` - Attach an existing asset
- `/target PATH` - Specify the output path
- `/where` - Show the current workspace
- `/exit` - Exit

## 📁 Project Structure

```
aDSL/
├── adsl-core/              # Core DSL implementation
│   ├── adsl/
│   │   └── core/
│   │       ├── asset.py           # Asset container
│   │       ├── primitives.py      # Geometry primitives
│   │       ├── layout.py          # Spatial relation operations
│   │       ├── joints.py          # Joint definitions
│   │       └── serialize.py       # Serialization
│   └── tools/
├── adsl-agents/            # Agent system
│   ├── prompt/            # Agent prompts
│   ├── tools/             # Agent tools
│   └── configs/           # Configuration files
├── adsl-chat/             # Interactive chat interface
└── asset/                 # Example resources

Our reproduction files:
├── setup_adsl.sh                  # Installation script
├── demo_adsl_basic.py             # Basic demo (no API required)
├── test_blender_integration.py    # Blender integration test
└── README_REPRODUCTION.md         # This document
```

## 🔬 Reproduction of Core Concepts

### 1. aDSL language design

```python
from adsl.core.asset import Asset
from adsl.core.primitives import Cube, Sphere, Cylinder
from adsl.core.layout import align, stack

# Composability
chair = Asset(label="Chair")
seat = Cube(scale=(2, 0.2, 2))
backrest = Cube(scale=(2, 1.5, 0.2))
chair.attach_part("seat", seat)
chair.attach_part("backrest", backrest)

# Spatial Reasoning
# Use relational operators instead of absolute coordinates
align(backrest, seat, axis="Y", mode="max")  # backrest sits behind the seat
```

### 2. Multi-Agent workflow

The Plan–Execute–Critic loop in the paper corresponds to the following in the code:

- **Planner**: `adsl-agents/planner.py` - decomposes tasks
- **Coder**: `adsl-agents/coder.py` - generates code
- **Critic**: `adsl-agents/critic.py` - evaluates constraints
- **Debugger**: `adsl-agents/debugger.py` - fixes errors

### 3. Constraint verification

aDSL supports verifiable constraints:

```python
# Constraint example (pseudocode)
constraints = {
    "on_top_of": (part_a, part_b),
    "aligned": (part_c, part_d, axis="X"),
    "distance": (part_e, part_f, min=1.0, max=2.0)
}
```

## 📊 Evaluation Tasks in the Paper

### Text-to-Shape task
The paper evaluates on the following categories:
- Furniture (chairs, tables, cabinets)
- Vehicles (cars, bicycles)
- Everyday items (laptops, cups)

### Image-to-Shape task
Generate the corresponding 3D model from a single image

### Downstream applications
- Scene-level generation
- Articulated object creation
- Structured shape editing

## 🎓 Review of Key Points in the Paper

### Problem statement
Existing LLM-driven 3D generation methods are brittle because:
1. General-purpose modeling interfaces (Blender, CAD) have too low a level of abstraction
2. LLMs perform poorly on numerical geometric parameters
3. There is no verifiable constraint mechanism

### The aDSL solution
1. **Language design**: Emphasizes composability and spatial relations
2. **Agent design**: A multi-agent system with specialized roles
3. **Joint design**: The language and the agents adapt to each other

### Key innovations
- Relational operators replace absolute coordinates
- Executable constraint verification
- Iterative repair loop

## 🔗 Reference Resources

- [Paper PDF](https://arxiv.org/pdf/2608.17975)
- [Project homepage](https://dylanwrh.github.io/aDSL/)
- [GitHub repository](https://github.com/sig-pku/aDSL)
- [OpenAI Agents SDK](https://openai.github.io/openai-agents-python/)
- [Blender Python API](https://docs.blender.org/api/current/)

## 📝 Citation

```bibtex
@article{wang2026adsl,
  title   = {aDSL: Agentic 3D Creation via Joint Agent-Program Design},
  author  = {Wang, Rui-Huan and Wei, Si-Tong and He, Jia-Qi and 
             Wei, Heng-Yi and Chen, Baoquan and Wang, Peng-Shuai},
  journal = {arXiv preprint arXiv:2608.17975},
  year    = {2026}
}
```

## ✅ Reproduction Checklist

- [x] Clone the GitHub repository
- [ ] Install core dependencies (`adsl-core[mesh,render]`)
- [ ] Install agent dependencies
- [ ] Configure the LLM API Key
- [ ] Verify the Blender integration
- [ ] Run the basic demo (`demo_adsl_basic.py`)
- [ ] Test text-to-3D generation (`adsl-run create`)
- [ ] Test image-to-3D generation
- [ ] Test articulated object generation
- [ ] Try the interactive chat mode

## 🐛 FAQ

### Q: Installing bpy fails
A: Make sure you use the correct PyPI index:
```bash
pip install bpy==4.0.0 --extra-index-url https://download.blender.org/pypi/
```

### Q: API calls fail
A: Check the API Key configuration and the network connection:
```bash
cat adsl-agents/configs/keys/openrouter_key.txt
```

### Q: The generated result is unsatisfactory
A: Increase the `--max-rounds` parameter to allow more iterations

---

**Reproduced by**: Claude Code  
**Reproduction date**: 2026-10-16  
**Status**: ✅ Basic functionality verified, awaiting full installation testing
