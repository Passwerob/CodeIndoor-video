# Mixed code 3D + TRELLIS component

This example keeps the shelf, frame, dimensions and support relation in code,
while using the official TRELLIS chess queen as a rigid visual component.

```powershell
& E:\3DUse\aDSL\venv\Scripts\python.exe E:\3DUse\aDSL\scripts\local_3d.py build `
  --source E:\3DUse\aDSL\examples\mixed_trellis_component\model.py `
  --output E:\3DUse\aDSL\outputs\mixed_trellis_component_default --views 2
```

To move the support surface, call `build(1.1)` in Python. The source can also
read `ADSL_DEMO_SHELF_TOP` before running the local build command. The queen's
local mesh and proxy dimensions remain unchanged; its world pose follows the
shelf's top surface. This is parameterized rebuild, not yet a live drag UI.
