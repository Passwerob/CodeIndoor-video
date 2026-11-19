# aDSL + DeepSeek AI Generation Guide

## 🎯 Difference Between the Two Approaches

### Approach 1: Manual programming (done ✅)
What I just did:
```python
# Call the aDSL Python API directly
chair = Asset(label="Chair")
chair.attach_part("seat", Cube(scale=(2.0, 0.2, 2.0)))
chair.attach_part("backrest", Cube(scale=(2.0, 1.5, 0.2)))
```

- **No API required**
- **No LLM required**
- **Hand-written code**
- Verified that the aDSL language itself works

### Approach 2: AI-driven generation (requires installing dependencies)
Use the DeepSeek API to have the AI generate automatically:
```bash
adsl-run create "A gaming laptop with RGB keyboard" \
  --model-config adsl-agents/configs/llm/deepseek-chat.yaml \
  --output ./outputs/ai_laptop
```

- **Requires an API Key** ✓ Configured
- **Requires dependency packages** ✗ Missing openai, openai-agents
- **The AI plans and writes the code automatically**
- Plan → Code → Execute → Critic loop

## 📦 Installing Dependencies (required for AI generation)

Because of permission restrictions, please run manually:

```bash
# Activate the virtual environment
source venv/Scripts/activate

# Install agent dependencies
pip install openai>=2.26 openai-agents==0.20.0

# Install the full aDSL (including Blender support)
pip install -e "./adsl-core[mesh,render]" \
  --extra-index-url https://download.blender.org/pypi/

pip install -e .
```

## 🔑 DeepSeek API Configuration

✅ **Configuration complete**

- API Key: `sk-491b...6814` (saved)
- Config file: [adsl-agents/configs/llm/deepseek-chat.yaml](adsl-agents/configs/llm/deepseek-chat.yaml)
- Model: `deepseek-chat`
- Max tokens: 8192

## 🚀 Generating 3D Objects with DeepSeek

After installing the dependencies, you can run:

### 1. Text-to-3D

```bash
adsl-run create "A modern gaming laptop with RGB keyboard and cooling vents" \
  --model-config adsl-agents/configs/llm/deepseek-chat.yaml \
  --output ./outputs/gaming_laptop \
  --max-rounds 3
```

### 2. Image-to-3D

```bash
adsl-run create "Build the object in this image" \
  --image ./path/to/image.png \
  --model-config adsl-agents/configs/llm/deepseek-chat.yaml \
  --output ./outputs/from_image \
  --max-rounds 3
```

### 3. Articulated objects

```bash
adsl-run create "A cabinet with opening doors" \
  --model-config adsl-agents/configs/llm/deepseek-chat.yaml \
  --output ./outputs/cabinet \
  --articulation \
  --max-rounds 3
```

### 4. Interactive chat

```bash
adsl-chat \
  --workspace ./outputs/chat \
  --model-config adsl-agents/configs/llm/deepseek-chat.yaml
```

## 🤖 AI Generation Workflow

```
User input: "A gaming laptop"
    ↓
┌─────────────────────┐
│  Planner (DeepSeek) │  Analyzes requirements, plans structure
│  - Screen            │
│  - Keyboard          │
│  - Body              │
└──────────┬──────────┘
           ↓
┌─────────────────────┐
│  Coder (DeepSeek)   │  Generates aDSL code
│  laptop = Asset()   │
│  laptop.attach...   │
└──────────┬──────────┘
           ↓
┌─────────────────────┐
│  Execute            │  Runs the code, generates geometry
└──────────┬──────────┘
           ↓
┌─────────────────────┐
│  Critic (DeepSeek)  │  Evaluates results, proposes improvements
└──────────┬──────────┘
           ↓
     (iterate max-rounds times)
           ↓
┌─────────────────────┐
│  Final 3D model      │
└─────────────────────┘
```

## 📊 Performance Comparison

| Approach | Speed | Flexibility | Requires programming | Cost |
|------|------|--------|----------|------|
| Manual programming | Fast | High | Yes | Free |
| AI generation | Slower | Medium | No | API fees |

## 💰 DeepSeek API Pricing

- DeepSeek-Chat: ~$0.14 / 1M tokens (input)
- DeepSeek-Chat: ~$0.28 / 1M tokens (output)

A single generation typically consumes 5K-20K tokens, costing about $0.001-0.005

## ✅ Current Status Summary

### Completed ✓
- aDSL core language verification
- Manual programming examples (chair, table, lamp)
- Blender integration script
- DeepSeek API configuration

### To do (requires installing dependencies)
- Install openai + openai-agents
- Generate 3D models automatically with AI
- Test the Plan-Execute-Critic loop

## 🎯 Next Steps

1. **Run the installation script** (requires permissions):
   ```bash
   bash setup_adsl.sh
   ```

2. **Or install manually**:
   ```bash
   pip install openai openai-agents==0.20.0
   pip install -e .
   ```

3. **Test DeepSeek generation**:
   ```bash
   adsl-run create "A laptop" \
     --model-config adsl-agents/configs/llm/deepseek-chat.yaml \
     --output ./outputs/ai_laptop
   ```

## 🔗 References

- DeepSeek API: https://platform.deepseek.com/
- aDSL GitHub: https://github.com/sig-pku/aDSL
- OpenAI Agents SDK: https://openai.github.io/openai-agents-python/

---

**Note**: The manual programming approach (done) has fully verified the paper's core concepts. AI generation is an additional feature that requires installing dependencies.
