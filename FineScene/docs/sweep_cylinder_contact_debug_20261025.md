# Debug Report — curved members falsely intersecting a cylinder

## Symptom

Run 42's `recipe_floor_lamp_failure_2.json` reported `Undeclared/excessive part penetration: 0.0990m` between `bulb` and `drum_shade`, and `0.0530m` between `bulb` and `gooseneck`. The same findings repeated in all four parameter edit scenarios. These were the only remaining full-recipe failures. No scene values were modified to investigate them.

## Root cause and reproduction

`sweep_contacts.sweep_gap` measured capsule paths against boxes and other sweeps, but returned `None` for a cylinder. `asset_physics.separating_gap` then rejected the pair using rectangular oriented envelopes. The empty interior of a curved path therefore became a false solid obstacle. Unsupported cylinder attachments also failed without checking their real surfaces.

Two generic regressions failed before the fix: a U-shaped capsule path surrounding a circular cylinder returned `None` rather than its positive clearance, and a rotated cap contact was unsupported. The fixtures also test side contact, genuine penetration and attachment gaps, rather than only accepting the formerly rejected case.

## Fix

`src/sweep_contacts.py` now measures each capsule segment against the finite circular cylinder in cylinder-local coordinates. It uses the actual signed distance to the cap, curved side and circular edge, minimized along the segment. Golden-section minimization of the convex distance includes both endpoints and retains a bracket below 1e-14 of the segment length. Capsule radius is then subtracted. The same result serves attachment and collision checks.

Classification: root-cause fix at the surface-distance fallback. No changes to penetration allowances, frozen dimensions, assembly relationships, required appearance or visual acceptance. Elliptical cylinders and sphere combinations remain unsupported by this surface routine; no envelope-only attachment may pass for them.

## Verification

- Before: both new regressions failed; the original Agent recipe had two intersection findings across five scenarios.
- After: 23 sweep/assembly regressions pass, including genuine gaps and penetration, rotated contacts, unsupported elliptical surfaces, rigid path edits and box contacts.
- Original case: the exact saved candidate passes complete `validate_recipe`, including frozen part IDs, assembly, envelope and every requested edit. Actual analytic gaps are 0.0105 m and 0.0134264 m. Revalidation evidence is `docs/evidence/sweep_cylinder_revalidation_20261025.json`.
- Available full suite: 273 tests passed. The optional TRELLIS texture module was excluded because this environment has no `trimesh`. The first custom full-suite invocation omitted `experiments` from the import path and failed to import the existing book-subassembly fixture; correcting the invocation passed without changing that fixture.

## Runtime provenance and limits

Run 42 was interrupted only after the defect and unchanged-candidate success were reproduced. Run 43 uses the exact original brief and revalidates Run 42 checkpoints with the corrected surface checker. The unchanged conservative recipe instructions were retained at Run 43 startup to preserve matching checkpoints; the updated circular-cylinder capability description is in current source for subsequent runs. Each run's startup source snapshot remains unchanged.

This does not prove that the Agent's wire-like shade looks like fabric or that the room is attractive. Whole-scene Blender geometry, textured rendering and independent system visual Judge remain required. No final scene acceptance is claimed here.

## Additional planar cylinder joint defect

Run 43's picture-light recipe then reduced its failures to two circular-cylinder end-cap joints and the two resulting disconnected parts. `flat_cap_attachment` supported a cylinder against a box, but no pair of cylinders, even when their opposing planar caps matched. The generic circular-cap test failed before the fix.

The checker now verifies aligned cylinder axes, opposing outward cap normals, cap-plane distance and complete containment of the smaller circular face. Tangent curved sides, separated caps, insufficient overlap and unsupported elliptical faces still fail. Arbitrarily rotated and reversed-axis real cap contacts pass. The same unmodified `recipe_bookcase_picture_lights_failure_3.json` now passes full recipe and edit validation; evidence is `docs/evidence/circular_cap_revalidation_20261025.json`.

The active Run 43 had already found an alternative valid Agent recipe after four repairs, before loading this additional checker change. It was left running. The fix is current-source capability, not the mechanism responsible for that live stage passing. Both root-cause fixes together have 24 targeted regressions and 274 available full-suite tests passing; the optional TRELLIS module remains excluded. `docs/evidence/available_suite_20261025.log` stores the full-suite result.
