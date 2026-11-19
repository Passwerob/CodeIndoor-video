# Local Blender import debugging record

2026-10-17; baseline commit `0c10f36a459bf1033e89e2e5dfcf451bc30043b0`; existing user changes were not reset.

## Symptoms and reproduction

Running `E:\Blender\blender.exe --background --factory-startup --python-exit-code 1 --python outputs\gpt6_final\blender_import.py`:

```text
Error: hinge: ... primitive_cylinder_add.depth expected a float type, not NoneType
Error: key_0_0: ... NodeSocketFloatFactor.default_value expected a float type, not tuple
Created 27 objects
```

The process returned 0 because the old script catches and prints errors. Keycap objects had material errors after creation, so the "success count 27" also cannot be taken as the actual number of meshes in the scene.

Minimal cylinder reproduction: `Cylinder(radius=1,height=2).to_dict()['primitives'][0]['params'].get('height')` returns None, and `float(None)` raises TypeError.

## Root cause and evidence

1. `core/primitives.py` converts height/center/axis uniformly into p0/p1/radius; `core/serialize.py` saves them as is. Serialization did not lose height information; the old import template read the wrong field.
2. The old script wrote a color tuple into the BSDF's numeric index 26, which is a scalar input on this machine. Setting Emission Color/Emission Strength by name works normally.
3. The old script caught exceptions piece by piece, so partial failures returned success. The new worker lets errors propagate and writes execution.json only after everything succeeds.

Investigation order: environment runs → actual JSON fields → import parameters → material inputs. Core tests and pip check both pass; therefore the environment was not reinstalled and height was not given an arbitrary default.

## Fix classification

The new unified entry is a root-cause fix that replaces the old import path: it reuses the official exporter's endpoint-cylinder, matrix and hierarchy handling; emission uses named slots. The old historical scripts were not fixed one by one, and should be imported through the unified entry going forward.

The official Agent's execution layer gains an optional desktop Blender backend, resolving the local run boundary that host Python has no bpy/bpyrenderer. This is an execution-backend adaptation, not equivalent to fixing the online model's JSON or image capability.

Semantic changes: the old laptop's explicit `--up-axis y` is converted to Blender's Z-up; `--rgb-emission` is an explicit preview option; source model coordinates are not modified and generated content is not rewritten.

## Verification

| Case | Before | After |
|---|---|---|
| p0/p1 cylinder | height is None, error | Built with the official endpoints, slanted endpoint projection assertion passes |
| Original GPT JSON | 27 reported successful, multiple errors | 101/101 meshes, 71 emissive meshes, GLB/.blend/PNG |
| Original DeepSeek JSON | Static check shows the same cylinder/material-template risk; old path not rerun | 254/254 meshes, 161 emissive meshes, GLB/.blend/PNG |
| Core direct test | Pass | Pass |
| Native source / JSON round trip | No unified local entry | Nested transforms, sphere, slanted cylinder, joint poses all pass |
| Error reporting | Old script swallowed errors | Explicit source errors are caught by the main process as AssetExecutionError |

Regression protection: `scripts/verify_local_3d.py` uses a real Blender export and then reopens the .blend to verify geometry coordinates and endpoints, rather than merely comparing implementation text. Test inputs, logs and outputs are kept in `outputs/local_verification_*/`. No temporary debug instrumentation is left in the production path.

Unresolved: detail errors in the source models, the old import template, online service response format, the visual Critic, complex booleans, simulation compatibility and the paper's evaluation. The source code having relation-layout and boolean APIs does not mean every feature was run-verified this round.
