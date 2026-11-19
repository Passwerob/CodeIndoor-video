# TRELLIS on Windows with RTX 5060 Ti

Adaptation date: 2026-10-17. The goal is to have TRELLIS generate only high-detail local meshes, while aDSL/Blender is responsible for the main structure, interfaces, and relation propagation.

## Verified Environment

| Item | Configuration |
|---|---|
| GPU | NVIDIA GeForce RTX 5060 Ti, about 16 GB VRAM, Compute Capability 12.0 |
| Driver | 591.86, driver CUDA 13.1 |
| Python | `E:\3DUse\research\trellis_windows_venv` |
| PyTorch | 2.7.1 + cu128 |
| Blender | `E:\Blender\blender.exe`, 5.2.2 LTS |
| TRELLIS | Official repository, including the FlexiCubes submodule |

## How to Run

The TRELLIS environment is separate from aDSL's Python 3.13 environment. Before inference, set:

```powershell
$env:PYTHONPATH='E:\3DUse\research\trellis_compat;E:\3DUse\research\TRELLIS'
$env:SPARSE_BACKEND='spconv'
$env:ATTN_BACKEND='xformers'
$env:SPARSE_ATTN_BACKEND='xformers'
$env:SPCONV_ALGO='native'
& E:\3DUse\research\trellis_windows_venv\Scripts\python.exe <inference-script.py>
```

The official `microsoft/TRELLIS-image-large` has been loaded on this machine and real inference completed. One test used 8 structure sampling steps and 8 SLAT sampling steps, yielding `344,942` vertices and `690,484` triangles.

## Current Output

Raw mesh:

`E:\3DUse\research\trellis_generated_windows_20261017\trellis_component_raw.glb`

Local AABB measured after Blender import:

```text
min = (-0.21128884, -0.49775735, -0.44165894)
max = ( 0.21295437,  0.49955997,  0.47023201)
```

Audit and preview script:

`E:\3DUse\research\trellis_generated_windows_20261017\render_generated.py`

Outputs:

- `trellis_component_preview.png`
- `trellis_component_preview.blend`
- `blender_audit.json`

## ADSL Hybrid Verification

Actual integration example:

`E:\3DUse\aDSL\examples\physical_bookshelf_trellis_generated\model.py`

It generates the bookshelf, shelf boards, and books in code, registers the TRELLIS GLB above with `ExternalMesh`, and then attaches it to `book_19` with an `attached` relation. Test results:

- The default build completed `139` relation checks;
- When the height of tier 1 is increased by `0.1 m`, the external component's overall Z displacement is `0.1 m`;
- The external component's X/Y dimensions and its own height are unchanged;
- Blender export succeeded, producing `1083` meshes, a GLB, a BLEND, and two PNGs.

Output directory:

`E:\3DUse\aDSL\outputs\physical_bookshelf_trellis_generated_default`

## Limitations

The texture-baking path of the official `postprocessing_utils.to_glb` depends on `nvdiffrast`, which is not installed in the current Windows environment. This verification therefore exported a GLB directly from the raw mesh `outputs["mesh"][0]`; this is sufficient for verifying structural assembly and rigid relations, but material/texture baking still needs separate adaptation.

This path proves that the 5060 Ti can run TRELLIS locally. A production system should keep: stable component IDs, local coordinate systems, explicit bounds, mounting interfaces, and the generation mode `parametric_code | external_mesh`, and should forbid structural parameters from implicitly stretching external meshes.
