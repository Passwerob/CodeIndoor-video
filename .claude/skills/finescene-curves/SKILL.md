---
name: finescene-curves
description: Use when modelling curved or turned forms in FineScene — sweep curve presets in recipes (arc, ring, helix, spiral, C-scroll, S-curve, cabriole, gooseneck, U-bend, wave, catenary, bezier/smooth), tapering and closed sweeps, lathe profile presets (baluster, vase, urn, bun foot, knob, lamp shades, turned leg, column base, finial, pedestal); deciding which mode and which layer a visual feature belongs to; whole-object consistency (like/mirror, assembly.symmetry); and every place to change, with tests, when adding a new curve mode. Triggers: sweep, lathe, curve, curve preset, profile, scroll, cabriole, style_requirement rejected by the visual Judge, available_capabilities.
---

# FineScene curve modes

- **Implementation:**
  - `FineScene/src/curve_presets.py`: presets, fitting and contact geometry;
  - `src/general_scene.py`: compilation;
  - `src/sweep_contacts.py`: contacts.

  On the aDSL side, a frustum is `Cylinder(radius, p0, p1, radius_end=...)`.
- **Regression tests:** `experiments/test_curve_presets.py`, `experiments/test_sweep_geometry.py`.

## 0. Three layers of curve capability (do not mix them)

| Layer | Where | Curve capability |
|---|---|---|
| **A. FineScene recipe** | Agent JSON recipe → `compile_general` | `sweep`: an explicit path or one of the **curve presets**, with a circular section; can **taper** (`radius_end`) and be **closed**. `lathe`: one of the **profile presets** or an explicit profile. Everything is parametrically editable. |
| **B. Forge program** | `build(B, mats, P)` in `adsl_adapter/forge_runner.py` | `curve_dsl` (sweep / bezier_tube / piping_loop / lathe / arc_tube, Blender curves baked to meshes) plus `baroque_lib` ornaments (volute, acanthus_leaf, fluted_column, corinthian_capital, dentil_row, …) |
| **C. Hand-written program** | `hand_objects/<id>/program.py` | `hand_lib`: loft, rounded_block, profile_extrude, lathe_mesh, tube, cut, bevel |

- **What layer A can do:** the **only authority** is `style_contract.capabilities()`. That is what the planning Agent actually sees, and its preset list is generated from `curve_presets.describe_presets()`, so it cannot drift from the implementation.
- **Still unavailable:** non-circular swept sections (mouldings, cornices), hollow shells, lofted soft bodies, dense relief, and arbitrary free-form meshes.

## 1. Visual feature → mode

| Visual feature | Layer A |
|---|---|
| Rails, lamp stems, handles, bent tubes | `sweep` + `arc` / `smooth` / `bezier`, or an explicit `path` |
| Tapered legs, horns, tapering lamp arms, plant stems | `sweep` + any preset + `radius_end < radius` (0 = pointed) |
| Cabriole legs | `curve: {preset: cabriole, knee}`. The knee projection is `size` along u. Use `radius` (knee) > `radius_end` (ankle). For a diagonal outward bow, give the source part `rotation: [0,0,45]`. |
| Cushion piping, table-edge banding, frame lines | `rounded_rect` (closed; `corner` sets the corner radius) |
| Rings, handle loops, barrel hoops, chandelier rings | `ring` (`plane` sets the orientation) |
| Springs, twisted ropes, spiral balusters | `helix` (`turns`; the normal of `plane` is the helix axis) |
| Volutes, scrolls | `spiral` (`turns`, `inner`); a C-scroll curled at both ends is `c_scroll` |
| S-brackets, S-scrolls | `s_curve` (the swing comes from `size`) |
| Faucets, desk-lamp arms | `gooseneck` (`spout` sets the length of the outlet section) |
| Loop handles, hairpin legs | `u_bend` (`corner`) |
| Wave trims | `wave` (`cycles`) |
| Swags, hanging cords, chains | `catenary` (depth from `size`; `tension` moves between a V shape and a flat bottom) |
| Turned feet, balusters, vases, urns, knobs, finials | `lathe` + `profile: {preset: ...}` |
| Lamp shades | `lathe` + `bell_shade` / `drum_shade` (solid, not a hollow shell) |
| Pedestal table bases, column bases | `lathe` + `pedestal` / `column_base` |
| Custom solids of revolution | `lathe` + `profile: {points: [[r, z], ...]}` (z bottom-up, non-decreasing) |
| Carving, acanthus relief | **Cannot** be faked with layer A. Use a separately sourced rigid sub-part (retrieve / auto / generate). |
| Mouldings, cornices (non-circular sections) | Not supported in layer A. Use layer B ornaments or a separately sourced sub-part. |

## 2. Syntax and rules

```json
{"id":"leg_fl","primitive":"sweep","size":[0.12,0.08,"leg_h"],"center":[0,0,"leg_h/2"],
 "radius":0.028,"radius_end":0.012,
 "curve":{"preset":"cabriole","plane":"xz","knee":"leg_knee"}}

{"id":"pedestal","primitive":"lathe","size":[0.5,0.5,0.7],"center":[0,0,0.35],
 "profile":{"preset":"pedestal"}}
```

**Fitting and parameters**
- **Presets are fitted to `size`.** The curve's bounding box, including the actual radius at each point, exactly fills the part envelope. So `size` is the editable outer dimension.
- **Preset parameters can be expressions.** `turns`, `knee` and the like may be parameter expressions: editing them changes the path while the envelope stays the same.
- **Parameters are strict.** Each preset accepts only its own parameters. An unused parameter, or a value out of range, raises an error naming the field.
- **Amplitude comes from `size`.** Since presets fill the envelope, parameters that would only scale along one direction are meaningless and have been removed. Every remaining parameter changes the shape itself, and `test_every_preset_parameter_changes_the_geometry` checks each one. Any new parameter must pass that test.

**Path options**
- `plane` is `xz` (front view, the default), `xy` (top view) or `yz` (side view). `flip` mirrors left to right. `samples` sets the sample count (4–96).
- Use either an explicit `path` or a `curve`, not both. `bezier` and `smooth` take control points in `curve.points`, as offsets from the centre; these are not fitted.
- `closed` joins an open curve end to start (`smooth` smooths across the seam).
  - `ring` and `rounded_rect` are already closed; do not add `closed`.
  - A closed tube has a constant radius: no `radius_end`.

**Lathe**
- The axis is the part's local +Z, and the `size` x must equal y.
- `rotation` can lay it on its side. A lathe part is solid, with no cavity.

**Grounding and contacts**
- A single part that is the assembly root must touch local ground (z = 0); otherwise it fails as `floating_root`. Presets ground themselves; with explicit control points you must ensure this yourself.
- **Contacts are measured to the real surfaces:**
  - sweep against box, cylinder, sweep and lathe;
  - lathe against box, cylinder, sphere, sweep and lathe.

  Tapered segments are split into conservative capsules. Lathes use surface samples against an SDF.
- **Sweep and lathe can never be supporting surfaces** (no `supported_by`); connect them with `attached_to`. Unsupported pairs fail explicitly.

**Style and Judge rejections**
- A `swept_path` style requirement must point its `part_ids` at sweep or lathe parts.
- After a visual rejection, **do not** resubmit the same tube scheme with only a different preset or a new name. Following the planner rules, move the missing mass or silhouette to a separately sourced rigid sub-part and keep the parametric root.

## 2.5 Whole-object consistency

Implementation: `src/part_consistency.py`. Regression: `experiments/test_part_consistency.py`.

**Copies (`like`)**
- **Write a repeated design once; every other instance is a copy:** `"like": "<source part id>"`.
- **Design fields must match the source verbatim**, compared after parameters are evaluated. These are primitive, size, rotation, color, finish, surface, form, and path / curve / radius / radius_end / closed / profile.
- **Only placement may differ:** id, center, semantic, parent and mount.
- **Mismatches are all reported at once**, with the source and copy values for each field.

**Mirrors (`mirror`)**
- **Symmetric parts add `"mirror": "x" | "y" | "xy"`.** The mirror plane is the object's local YZ plane, its XZ plane, or both.
- **The compiler mirrors the geometry**, including curve paths, and the rotation.
- **The copy's `center` must equal the mirrored source centre.** If it does not, the error gives the expected value.
- **Mirrored copies cannot use** parent or fit_between.

**Families and symmetry**
- `like` must point at an original design, not at another copy. Chains are not allowed.
- Put shared proportions in parameters, so one edit moves every copy. The repair route opens the same design fields across a whole copy family.
- **Whole-object symmetry:** declare `assembly.symmetry: "x" | "y" | "xy"`. After compilation, each part must have a mirror counterpart within the tolerance in `part_consistency.py`. Parts without one are reported as `asymmetric_part`, with a hint to use like/mirror. The default is `none`, which means no check.
- Stylistic consistency within one object (tube radii, fillets, curve vocabulary) is not yet checked automatically. Keep it consistent by sharing designs with `like` and proportions with parameters.

## 3. Checklist for adding a curve mode

**A new curve or profile preset** touches only `curve_presets.py`:
- the `CurvePresetName` / `ProfilePresetName` Literal;
- `CURVE_PARAMS`;
- `CURVE_HELP` / `PROFILE_HELP`;
- the generator (`_canonical_curve` or `_PROFILES`).

The tests iterate over every preset automatically (`test_every_*_preset_*`).

**A new primitive** (for example, a non-circular swept section) needs every item below. Miss one and the planner will think it can use something the compiler rejects.

1. **Fields and types:** `curve_presets.check_curve_fields`, plus the `primitive` Literal and fields in both `general_scene.Part` and `general_planner.PartSpec`.
2. **Compilation:** in `general_scene._compile_local_geometry`, add the compile branch, the records (keep the editable source quantities) and `SHAPE_KEYS`.
   - aDSL only has Sphere / Cube / Cylinder (including frustums) / ExternalMesh.
   - A new kernel primitive also needs `core/bounds.py` (`_aabb_of_primitive`, `_support_of_primitive`), `core/export/export_glb.py` and `export_urdf.py`.
3. **Capabilities:** in `style_contract.capabilities()`, move it from unavailable to available and state its limits. Add it to `curve_presets.CURVE_PRIMITIVES`.
4. **Planner prompts:** in `staged_planner.py`, update the primitive description and the contact description, and add `*_expression` diagnostics.
5. **Repairs:** in `general_repairs.py`, update the editable field list, the `moving` set and the part fields passed to the repair Agent.
6. **Contacts:** `sweep_contacts.sweep_gap` / `asset_physics` must either compute a real distance or return None so the connection fails. Falling back to an envelope approximation and passing is **not allowed**. Update the set of supporting surfaces too.
7. **Tests:** cover the primitive in `experiments/test_curve_presets.py`:
   - editing changes the geometry but keeps the envelope;
   - out-of-range and degenerate inputs are rejected;
   - contacts are measured;
   - style binding works.
8. **Docs:** this skill and `FineScene/README.md`.

## 4. Acceptance

```bash
cd FineScene
python -m pytest experiments/test_curve_presets.py experiments/test_sweep_geometry.py -q
python -m pytest experiments -q        # compare against the pre-change baseline; no new failures
cd .. && python -m pytest tests -q
```

- **Use this repository's aDSL.** The `.venv` built by `python env/setup_env.py` installs it in editable mode. If the system Python points at another aDSL copy, `Cylinder(radius_end=...)` does not exist, and tapered sweeps and lathes fail.
- **Known failures are a baseline.** `experiments` has known pre-existing failures unrelated to curves. A change passes only if the failure list is unchanged.
- **Keep the stages of evidence apart**, as `FineScene/AGENTS.md` requires. Unit tests passing ≠ planning complete ≠ correct Blender render ≠ accepted by the system visual Judge. If a new mode has no Blender render or Judge evidence yet, say so.
