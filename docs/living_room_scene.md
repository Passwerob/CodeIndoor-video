# Living Room Scene · Blender Project Notes

> In one sentence: this is a **6.3 × 5.3 m modern living room**. Of its 16 objects, 6 come from CC0 scanned assets, 6 are written as programs by a large model on the spot, 2 are procedurally generated (rug/curtain), 1 is a generated cabinet with procedurally filled books, and 1 is a borrowed shell. All of them hang under one metric coordinate system, rendered into 5 images with 4 lights + Cycles.

Last updated: 2026-11-09

---

## 1. Files and technical parameters

| Item | Value |
|---|---|
| Project file (working version) | `runtime/outputs/living_room/assets/living_room_assets.blend` |
| File size (working version) | 21.5 MB, textures externally linked for easy rebuilding |
| Deliverable file (self-contained) | `runtime/outputs/living_room/assets/living_room_assets_packed.blend` |
| File size (deliverable) | **167.5 MB**, all 120 textures embedded, zero external dependencies |
| Blender version | 5.2 |
| Render engine | Cycles, CPU |
| Sampling | 64 + OpenImageDenoise |
| Render resolution | 1280 × 800, PNG |
| Color management | AgX, exposure −0.30 |
| Units | Metric (METRIC), unit scale 1.0, length unit = metre |
| Coordinate system | Z up, −Y is the front-facing direction of objects |
| Scene bounding box | X −3.15 ~ 3.15, Y −2.65 ~ 2.66, Z −0.12 ~ 3.02 (6.30 × 5.31 × 3.14 m) |

**Object counts**

| Type | Count |
|---|---|
| Meshes (MESH) | 337 |
| Vertices | 216,615 |
| Triangles | 394,052 |
| Empties (the root node of each object) | 16 |
| Lights | 4 |
| Cameras | 6 |
| Materials | 167 (131 of which are book covers, see "Known limitations") |
| Textures | 495 data blocks (plus 2 render-result views), actually pointing to only **99 disk files**; the working version has 21 embedded and 474 externally linked data blocks; the deliverable has 120 embedded and 0 externally linked |

---

## 2. The room itself

The shell was not newly built; it is **borrowed from the original SceneCode scene's shell** (collection name `ADSL_ReceptionLounge` → `OBJ_room_shell`). The floor and wall materials were redone; the shell's geometry itself was changed only once: the south wall's door opening was shifted east as a whole (see section 9), and the wall, baseboard, and door casing were re-stitched accordingly. The door leaf is an independent object and is not included here.

| Item | Dimensions |
|---|---|
| Outer wall outline | 6.30 × 5.30 m, wall thickness 0.15 m |
| Interior clear dimensions | 6.00 × 5.00 m (inner surfaces x = ±3.00, y = ±2.50) |
| Clear height | 2.90 m (the ceiling adds a further 0.12 m slab thickness) |
| Window (north wall) | Opening 2.00 m wide × 1.30 m high, sill height 0.90 m, x −1.00 ~ 1.00; 6 parts in total: four frame sides + mullion + glass |
| Door (south wall) | Opening 1.20 m wide × 2.10 m high, **x 1.20 ~ 2.40 (east-offset)**; 3 parts: left and right door casing + lintel; the door leaf is an independent object `entry_door` and is not included here |
| Baseboard | Four walls, broken on both sides of the south wall's door opening, 5 parts in total, height 0.10 m |

The shell has **24 meshes** in total (floor 1, ceiling 1, walls 8, baseboards 5, door casing 3, window 6) and only 288 triangles; they are all regular cuboid slabs, so the geometry cost is negligible.

> The left and right posts of the door casing protrude 2 cm beyond the inner wall; this is the source of the "opening depth" between the door leaf (4 cm thick) and the wall opening. The lintel `door_lintel` is the 0.80 m of wall above the opening; its material follows the wall plaster, not the door casing's white paint.

---

## 3. Collection structure

One collection per object, with one Empty in the collection as the root node (`ROOT_<id>`), and all parts hang under the root node. This way each object can be moved/hidden/exported as a whole, which is also the precondition for "pulling it out on its own to feed TRELLIS".

```
Scene Collection
├─ Collection                     (2 items: auxiliary objects for rendering)
├─ ADSL_ReceptionLounge           (shell container)
│   └─ OBJ_room_shell             (25 objects = 24 meshes + 1 root)
├─ OBJ_sofa_3seater               (31 = 30 meshes + 1 root)
├─ OBJ_area_rug                   (1)
├─ OBJ_coffee_table               (1)
├─ OBJ_tv_cabinet                 (28 = 27 meshes + 1 root)
├─ OBJ_armchair_west              (1)
├─ OBJ_armchair_east              (1)
├─ OBJ_side_table                 (1)
├─ OBJ_floor_lamp                 (13 = 12 meshes + 1 root)
├─ OBJ_plant_northwest            (1)
├─ OBJ_bookshelf                  (22 = 21 meshes + 1 root)
├─ OBJ_wall_art_west              (1)
├─ OBJ_window_curtain             (1)
├─ OBJ_wall_tv                    (28 = 27 meshes + 1 root)
├─ OBJ_entry_door                 (34 = 33 meshes + 1 root)
└─ OBJ_ceiling_light              (20 = 19 meshes + 1 root)
```

> The bookshelf's 131 book meshes are appended in `populate_shelf()`, land directly under the Scene Collection and are bound to the bookshelf root node, without a collection of their own.

---

## 4. Inventory of the 16 objects

| Object | Source | Meshes | Triangles | Size W×D×H (m) | Position (x, y) | Orientation |
|---|---|---:|---:|---|---|---:|
| 3-seater sofa | generated | 30 | 52,352 | 2.20 × 0.90 × 0.88 | 0.00, 1.30 | 0° |
| Rug | procedural fabric | 1 | 188 | 3.60 × 2.60 × 0.03 | 0.00, 0.70 | — |
| Coffee table | retrieved `WoodenTable_01` | 1 | 952 | 1.20 × 0.60 × 0.42 | 0.00, 0.16 | 0° |
| Media cabinet | generated | 27 | 18,068 | 1.80 × 0.47 × 0.55 | 0.00, −2.255 | 180° |
| Wall-mounted TV | generated | 27 | 516 | 1.23 × 0.06 × 0.69 | 0.00, −2.44 (bottom edge z = 0.64) | 180° |
| Door leaf | generated | 33 | 1,972 | 1.02 × 0.12 × 2.07 | 1.80, −2.57 (bottom edge z = 0.01) | 180° |
| Armchair (west) | retrieved `ArmChair_01` | 1 | 5,626 | 0.86 × 0.86 × 0.82 | −1.55, 0.60 | 27° |
| Armchair (east) | retrieved `ArmChair_01` | 1 | 5,626 | 0.86 × 0.86 × 0.82 | 1.55, 0.60 | −27° |
| Side table | retrieved `side_table_tall_01` | 1 | 6,408 | 0.50 × 0.50 × 0.55 | −1.45, 1.36 | 0° |
| Floor lamp | generated | 12 | 26,956 | 0.41 × 0.41 × 1.57 | −1.71, 1.33 | 0° |
| Potted plant | retrieved `potted_plant_02` | 3 | 69,806 | 0.60 × 0.60 × 1.20 | 2.55, 1.95 | 0° |
| Bookshelf | generated cabinet + procedural books | 152 | 171,140 | 0.39 × 1.14 × 1.90 | **−2.773**, 1.55 | 90° |
| Wall painting | retrieved `fancy_picture_frame_01` | 2 | 938 | 1.06 × 0.05 × 0.70 | −2.975, −0.732 | 90° |
| Curtain | procedural cloth | 3 | 764 | 2.78 × 0.07 × 2.28 | 0.00, 2.40 | — |
| Pendant light | generated | 19 | 32,452 | 0.51 × 0.47 × 0.47 | 0.00, 0.35 (hanging point z = 2.90) | 0° |
| Room shell | borrowed | 24 | 288 | 6.30 × 5.30 × 2.90 | 0, 0 | — |
| **Total** | | **337** | **394,052** | | | |

The triangle distribution is very uneven: the bookshelf alone accounts for 43% (21 cabinet parts + 131 books), the potted plant for 18% (the scanned asset's pot soil has a high face count), while the whole room shell is only 0.07%; the two "slab hardware" items are both very light: the wall-mounted TV has 516 triangles (27 beveled cubes), and the door leaf 1,972 (33 parts, including double-sided handles and two barrel hinges).

> Of the bookshelf's 152 meshes only 21 are the cabinet; the remaining 131 are books; these 131 do not get a collection of their own and hang directly under the bookshelf root node.

---

## 5. Supply strategy: a mix of three sources

The scene inventory is first given by SceneCode's scene graph, then each object is judged for "where it comes from":

| Mode | Count | Description |
|---|---:|---|
| `borrowed_shell` | 1 | Room shell, reused directly from the original scene |
| `retrieved` | 6 | CC0 scanned items from the asset library; after import, textures are auto-repaired and sizes normalized to metres |
| `generated` | 6 | Not in the asset library → the large model writes the geometry program on the spot |
| `retrieved+generated` | 1 | Bookshelf: cabinet generated, books filled in procedurally |
| `procedural` | 2 | Rug, curtain: fabric generated by procedural profiles, no suitable scanned item |

The decision principle is straightforward: **if the asset library has something better, use the scan; only if it does not, let the model write it**. That is why the media cabinet, sofa, floor lamp, pendant light, wall-mounted TV, and door leaf are generated: the asset library has nothing that matches (the original "media cabinet" was actually a flattened wooden table).

### The five steps of the generation chain

1. **brief**: write the category, target dimensions, and required parts as natural language, then append a **real construction reference pack** retrieved online (size ranges, part anatomy, proportion rules, common mistakes).
2. **Write code**: DeepSeek outputs a piece of Python that calls our extended `Builder` DSL (`box` / `cylinder` / `tube` / `mesh` + five curve primitives `sweep` / `bezier_tube` / `piping_loop` / `lathe` / `arc_tube`).
3. **Execute**: Blender runs this program headless and bakes it into real meshes.
4. **Deterministic gate** (before the visual one): faces without material, bounding box exceeding the limit by 10%, part count < 12, curve-part count below the category quota (upholstery ≥ 4, lighting ≥ 3, slab 0), coplanarity detection. Coplanar faces are stitched automatically (pushed outward along the normal by 0.7 mm), so the model does not have to wrestle with floating-point error.
5. **Visual gate**: render three views (objects whose `mount` is `wall` / `door` get an extra back view) and hand them to the vision model for judgment, producing `evidence` + `correction`, fed back verbatim into the next round. At most 3 rounds.

### Where curves are used

The blocky look comes from "everything being boxes". The current curve-part counts:

| Object | Curve parts | Where used |
|---|---:|---|
| Sofa | 6 | Armrest roll edges, seat cushion piping, backrest piping, tapered wooden legs |
| Floor lamp | 11 | Turned lampshade (with rolled edge), brass main pole, turned base bevel, lampshade rim ring |
| Pendant light | 8 | Turned canopy, three bent arms, three tapered shades + ring shade rims |
| Bookshelf | 2 | Shelf front-edge bevels |
| Wall-mounted TV | 0 | Slab hardware; the quota does not require it in the first place |
| Door leaf | 0 | Same as above; its difficulty is that **both sides must hold up**, not curves |

### The material library was missing one category

The original palette had only six categories: "fabric / wood / brass / ceramic / marble / lampshade". The first version of the TV could therefore only use `linen_charcoal` (linen) for the screen and bezel, and the visual gate's verdict was very direct (model verdict, kept in Chinese):

> 三张视图均只呈现一块浅灰色布纹矩形面板……无法确认这是壁挂平板电视。 (All three views show only a light-gray fabric-textured rectangular panel ... cannot confirm this is a wall-mounted flat-panel TV.)

The weave of the linen was the giveaway. After adding three keys it passed on the first try:

| Key | Use | Parameters |
|---|---|---|
| `screen_black` | Screen glass | Near black (0.013, 0.015, 0.019), roughness 0.075 |
| `plastic_black` | Bezel / back shell | Near black (0.017), roughness 0.62, matte |
| `steel_brushed` | Wall-mount bracket | Metallic 0.94, roughness 0.27 |

The screen must be **near-black and smooth**, and the bezel **near-black and rough**: the difference is not in hue but in specular highlight. Viewed from the front, the screen mirror-reflects the dark background into a patch of deep color, while the bezel is diffuse, and only then can the two be told apart.

Another change was adding a fourth camera for wall-mounted objects. Originally all three camera positions were at the front, yet the gate demanded "the two steel bracket arms must be visible from the back": a requirement it raised that could never be satisfied by the three images. When `mount == "wall"`, a back camera position is added, and the verdict immediately became "two symmetric vertical steel bracket arms clearly visible", with the semantic score jumping from 1 to 5. The door leaf is the same: both faces are finished faces, with the handle on one side only, and `mount == "door"` also uses four camera positions.

### Door leaf: a wrong envelope fails three rounds in a row

The door was the only component missed when borrowing the shell: the casing, lintel, and baseboard were all there, just no door leaf, so the south wall had an opening leading into the void.

The first version wrote the envelope as `1.02 × 0.040 × 2.075`, i.e. "how thick the door is". All three rounds were rejected by the deterministic gate:

> D overshoots the envelope: 0.122 m > 0.040 m

**A door has a handle, so it cannot be only 40 mm deep.** A real door leaf's total footprint depth = 40 mm slab + about 40 mm of handle protrusion on each side. Changing it to `1.02 × 0.120 × 2.075` and stating in the brief "slab centered at y = 0, handles reach at most y = ±0.058" passed on the first try (33 parts, 1,972 triangles, semantic 5 / quality 4).

The second issue was width: the model delivered a 1.091 m door while the net opening width was only 1.040 m, so the door was 5 cm wider than the opening, pressing 2.5 cm into the casing on each side. The deterministic gate only looks at the "envelope axis order" and not at the opening, so it did not stop this. A measured correction was added at assembly time: `adsl_width_correction = 0.9349`, compressing only the X direction, leaving the slab thickness and handle thickness untouched.

---

## 6. Provenance: these programs really do come from the model API

A four-link evidence chain; if any link breaks it gets caught:

1. **Gateway log** `bridge/gateway_calls.jsonl`: records `prompt_sha`, `resp_sha`, `usage`, `upstream_model`.
2. **Raw response body** `forge/<id>_prov/llm_attempt<n>.json`: archived whole, containing `meta.resp_sha` and `raw_response`.
3. **Program file** `forge/<id>/program.py` + the `code_sha` in the log.
4. **The hardest link** `code_derives_from_body`: strip the code fence from `choices[0].message.content` in the archived response body and re-hash; it must equal `code_sha`. That is, the program must be a verbatim replica of the model output, and changing a single character exposes it.

Current result: **7/7 passed**:

| Object | code_sha | llm_resp_sha | upstream_model |
|---|---|---|---|
| bookshelf | `a6912b2233881412` | `82838fce579d0d3f` | deepseek-flash |
| ceiling_light | `3c2dc587b736e8ff` | `055b022357b4fb07` | deepseek-flash |
| entry_door | `2b8700b941073018` | `f7fd8c6a0f3e93ba` | deepseek-flash |
| floor_lamp | `dcebcc9ee6fe2e67` | `ddcd8ad47e301144` | deepseek-flash |
| sofa_3seater | `d45a2a33df164b77` | `8d4ebc90b0db5b65` | deepseek-flash |
| tv_cabinet | `9ede55fa6dae4d4c` | `8a78f1195b6dfab6` | deepseek-flash |
| wall_tv | `f9325f82ecb20464` | `1aac9ceae77a4d1e` | deepseek-flash |

Report: `runtime/outputs/forge/PROVENANCE.json` (the gateway has recorded 49 responses in total).

---

## 7. Lights and cameras

### Lights (4 + environment)

| Light | Type | Power | Size | Color temperature tendency | Position |
|---|---|---:|---|---|---|
| `Key_South` | Area (rectangle) | 195 W | 2.60 × 2.60 m | Warm (1.00, 0.93, 0.84) | (0, −0.25, 2.80) facing down |
| `Window_Fill` | Area (rectangle) | 145 W | 1.90 × 1.30 m | Cool (0.80, 0.88, 1.00) | (0, 3.05, 1.55) outside the window facing in |
| `Side_Fill` | Area (rectangle) | 70 W | 2.20 × 1.60 m | Warm (1.00, 0.95, 0.88) | (−2.55, 0.30, 2.20) |
| `Sun_Exterior` | Sun | 0.6 | — | White | Origin (direction only) |

Ambient light: World base color (0.82, 0.85, 0.90), strength 0.32. Overall it is a conventional indoor lighting setup of "one overhead key light + cool window-light fill + side fill + weak sun", not aiming at drama.

> These four lights are borrowed from the shell, but **their shapes were reset**: the original three area lights all had `shape` of `DISK` and `size` written as 3.0 / 2.2 / 2.6, while `size_y = 0.25` is only valid for rectangles. What was actually produced were three glowing discs 3 m in diameter, placed 0.4~0.7 m from the wall, and the disc edge is the hard edge running across the wall (see section 10 for details). All three are now rectangular area lights with a large aperture, placed far from the wall.

### Cameras (6)

| Camera | Focal length | Position | Purpose |
|---|---:|---|---|
| `Camera_Cutaway` | 30 mm | (5.9, −6.9, 4.5) | Southeast cutaway panorama, hiding the south/east/west walls |
| `Camera_WestCutaway` | 30 mm | (−5.9, −6.6, 4.3) | Southwest cutaway, to see the bookshelf and floor lamp |
| `Camera_Interior` | 18 mm | (0, −2.35, 1.68) | Interior eye-level view, human eye height |
| `Camera_Corner` | 22 mm | (2.15, −1.95, 1.66) | Southeast corner interior, currently the active camera |
| `Camera_Media` | 18 mm | (2.35, 2.25, 1.72) | Looking at the media wall from a corner of the seating area, sofa and screen in the same frame |
| `Camera_BackInterior` | 20 mm | (0.2, 2.15, 1.9) | Looking back from the north side (kept from the original scene) |

5 images are rendered: `v2_view_000` ~ `v2_view_004`. In the cutaway views 104 parts are hidden (three walls, the corresponding baseboards, door casing, and ceiling: 11 parts in total, plus the 2 wall paintings, 27 media cabinet parts, 27 TV parts, and 33 door leaf parts hanging on the cut walls); otherwise they would hang in mid-air. The door leaf has to be in this list: once the south wall is cut away, behind that door is the void.

`Camera_Media` was originally 24 mm, standing at (2.15, 1.85, 1.60), and could only capture the cabinet, not the sofa; the cutaway camera positions in turn hid the south wall together with the cabinet. The result was that **no single image could prove the orientation relation between the sofa and the TV cabinet**, so the gate ruled "the TV cabinet is on the wall behind the sofa". After changing to 18 mm and retreating to the northeast corner of the room, the sofa, cabinet, and screen enter the same frame, and this misjudgment disappeared.

---

## 8. Layout: not hand-placed, but computed

SceneCode gives a rough layout; a round of clearance solving was then run, and all 7 rules passed:

| Constraint pair | Required | Measured |
|---|---:|---:|
| Coffee table / sofa | 0.38 | 0.39 |
| Coffee table / west armchair | 0.30 | 0.52 |
| Coffee table / east armchair | 0.30 | 0.52 |
| Side table / west armchair | 0.06 | 0.08 |
| Floor lamp / bookshelf | 0.28 | 0.30 |
| Floor lamp / west armchair | 0.10 | 0.10 |
| Potted plant / bookshelf | 0.05 | 4.46 |

The solver changed the coordinates of three objects: the coffee table moved forward 0.39 m (in the original position, sitting down put the knees against the table edge), the side table 0.06 m, and the floor lamp 0.24 m (the lampshade radius is the real constraint, not the base).

The other items are **composition rules** rather than clearance rules:

- **Choosing the wall for the painting**: chosen automatically by "usable clear wall space", excluding the window wall (must not block the glass) and the media wall (must not compete with the screen). Among four candidate gaps, the one whose **width is closest to 2.2 m** is taken: a single painting hung in the middle of a 4 m empty wall looks like a stamp stuck on, and also easily floats out of every camera position. It ended up on the west wall at y = −0.73, at sight-line height 1.45 m, avoiding the bookshelf and floor lamp.
- **Media cabinet alignment**: moved from the middle of the walkway to the south wall directly facing the sofa, **coaxial with the sofa (x = 0)**, with 2 cm left behind it against the wall and the cabinet doors facing into the room. Viewing distance 3.0 m.
- **The TV follows the cabinet**: once the media cabinet is fixed, the screen is mounted directly above the cabinet at `x = 0`, with the bottom edge 0.09 m above the cabinet top, and the cabinet back 30 mm from the wall (bracket clearance). The calculation is "cabinet top height + 0.09", not a hard-coded absolute height: if the cabinet changes size, the screen moves with it.
- **Armchairs turned toward the screen**: originally the two chairs faced each other (90° / −90°), a pure conversation layout in which nobody could see the media wall. Each is now rotated 27° toward the screen; the sight line from the seat to x = 0 is oblique anyway, so this angle is both "watching the screen" and "enclosing the coffee table", with no second adjustment needed.
- **Bookshelf against the wall**: SceneCode's original x = −2.40 put the cabinet back at −2.595, while the west wall's inner surface is at −3.00, so the cabinet stood away from the wall and the wall was visible through the gap. The solver originally only checked "is it wide enough between two objects" and never checked "did the objects that should be against the wall actually touch it", so this gap went unattended. Now a separate `snap_to_wall` pass pushes all `WALL_BACKED` objects in front of the wall using the measured projected half-width, **separate from the clearance rules and executed last**; otherwise wall contact and avoidance would pull against each other. The 0.035 m gap is not arbitrary: the west wall's baseboard protrudes 30 mm, and pushing by 0.020 would insert 10 mm into the baseboard. After the change the cabinet back is at −2.967 and the baseboard's front surface at −2.970, leaving 3 mm of clearance.
- **Door leaf placement**: the shell provides a door opening, not a door. The door is sized by `place_entry_door` to the net opening width (1.04) inset by 1 cm, placed at the opening center x = 1.80, with the slab face set back 4 cm from the wall's inner surface, so that the 2 cm protrusion of the casing forms the opening depth.

---

## 9. Geometry corrections made at assembly time

None of these is "poorly generated"; they are math problems of assembly, and what was changed is the assembly, not the model's program:

1. **The parent transform was applied twice**: the parts were already positioned in world space, and setting `parent` again translated them once more by the root node. Assigning `matrix_world` directly does not work either: the `matrix_world` of a newly created Empty is the identity before dependency-graph evaluation, and Blender would use it to back-compute local coordinates. The correct approach: build the parent matrix `T·R` from the pose, then `matrix_basis = parent_world⁻¹ · world`. Before the fix the potted plant was at (5.03, 3.94), exactly double the target value.
2. **Automatic stitching of coplanar faces on all six sides**: originally only "coplanar top faces" were checked, later generalized to the six axis-aligned faces. The sofa armrest front face and the box front face were both at y = −0.44, overlapping by 0.19 × 0.135 m, and rendered as a solid black hole (Z-fighting smeared into a black patch by the denoiser), not a gap and not missing material. When detected, the face is pushed outward 0.7 mm along its normal; 5 places were stitched in total.
3. **The door opening shifted east as a whole**: the door is in the shell geometry, not in the layout table, and it sits right in the middle of the south wall: if the media cabinet is placed in the middle it stands in the doorway, and if placed to the side the sofa cannot see it (this was the state before the fix: the sofa axis x = 0 faced the door opening, and the media cabinet was squeezed to x = −1.65). So before assembly the opening was moved from x −0.60~0.60 to 1.20~2.40, and the left segment of the south wall was extended to 4.35 m accordingly. Rotating the seating group and turning the sofa sideways were both tried, but within the 6.00 m depth, after turning sideways the viewing distance is only 1.0~1.2 m, which does not work. After the change the media cabinet returns to x = 0, coaxial with the sofa, viewing distance 3.0 m.
4. **The wall's inner skin was treated as the outer skin**: `ROOM_HALF` in the solver was written as (3.15, 2.65), which is half of the **outer wall outline**; the inner surfaces are at ±3.00 / ±2.50. All wall-backed objects were therefore buried a further 15 cm into the wall: the media cabinet back was at −2.638 m while the wall surface is at −2.500, leaving 10 cm of the cabinet hidden in the wall (in the render this looked like "the cabinet is rather thin"). After changing to (3.00, 2.50) the cabinet back lands at −2.480, leaving a 2 cm construction joint.
5. **Book layout**: originally the whole row of books was handed to `fitted_copy` for one non-uniform scaling to the shelf width, cramming 30 books into 0.97 m, with adjacent covers biting into each other by 3~5 mm; the shelf height was computed by formula, and the top shelf hit the top board directly. It was changed to place books one by one with uniform scale: shelf height is measured from the actual shelf boards, books are laid out in sequence with 2 mm gaps, spines aligned 12 mm in front of the cabinet face, and books exceeding the shelf width are no longer placed (rather than left at their imported positions, which would scatter a few books on the floor in front of the sofa).
6. **Pose caliper**: after each object is assembled, its measured bounding box is compared against the target pose; if the deviation exceeds 2 cm it is translated and recorded in the report. The `moved` of all 16 objects is now false (except that the pendant light once had a −4.6 cm hanging-point fine adjustment).
7. **Wall-mounted objects placed by measured bounding box**: the TV's generated program centers the geometry at the origin, while the convention requires the origin at the bottom face. Applying the pose directly at assembly sank the panel below the cabinet top: measured z 0.29 ~ 0.98, 0.345 m lower than the target, looking "buried in the cabinet". Now for objects with `mount in ("wall", "door")`, the measured bounding box is taken and the **bottom edge** is aligned to the target height, with the correction recorded in `adsl_z_correction`. At the same time the forge deterministic report gained an `origin_at_bottom` field, so programs violating the convention are put on record rather than leaving downstream to guess.
8. **The door leaf is brought into the opening by measured width**: the model delivered a 1.091 m door with a 1.020 m envelope, pressing 2.5 cm into the casing on each side. At assembly the measured bounding box is taken, and only X is compressed to `1.020 / 1.091 = 0.9349` (`adsl_width_correction`), leaving slab and handle thickness untouched. After the fix the door occupies x 1.287 ~ 2.307, the opening clear space is 1.280 ~ 2.320, leaving 7 / 13 mm gaps on the two sides.
9. **Lintel material belongs to the wall**: `door_lintel` is the 0.80 m of wall above the opening, but the part name does not contain `wall`, and the matching condition of `rebuild_shell_surfaces()` missed it, so it kept the shell's original off-white placeholder color, rendering as a white board stuck on the plaster wall. Changed so that `"lintel" in part` also takes plaster.
10. **Re-lighting**: see section 10; this is the only correction that does not touch geometry.

Report: `assets/ASSET_SCENE_REPORT.json`.

---

## 10. QA status

### Per-object gate

All 7 generated objects `passed` (three views + an extra back view for wall-mounted/door leaf, semantic score ≥ 4). The newly added door leaf has 33 meshes, 1,972 triangles, 0 curve parts, and passed in one round (model verdict, kept in Chinese):

> 源资产为独立的橡木门扇，双面可见钢制执手、圆形底座、椭圆锁孔盖板和两个桶形合页，带 3 mm 浅凹面板与竖向阴影缝……未附加门框或壁挂件。语义分 5 / 质量分 4。 (The source asset is a standalone oak door leaf, with steel handles visible on both faces, round rosettes, oval keyhole cover plates and two barrel hinges, with 3 mm shallow recessed panels and vertical shadow gaps ... no door frame or wall-mount parts attached. Semantic score 5 / quality score 4.)

### Scene gate

**passed**, 0 blocking items, 2 improvement items:

| Improvement item | Current status |
|---|---|
| Wall-mounted TV "off to the left of the cabinet" (壁挂电视「偏柜体左侧」) | Perspective misjudgment: TV x −0.615 ~ 0.615, cabinet x −0.900 ~ 0.900, both centered at 0.000, measured coaxial |
| Floor lamp shade overlaps the shelf front edge from some viewpoints (落地灯灯罩与书架前缘在部分视角上重叠) | Measured clearance 0.30 m (required 0.28); this is a line-of-sight occlusion, not a collision |

### Three issues that were once blocking and have been fixed

| Verdict | State at the time | Fix |
|---|---|---|
| 「电视柜位于沙发背后的墙上……客厅观影核心功能无法成立」 ("The TV cabinet is on the wall behind the sofa ... the living room's core viewing function cannot hold") | The sofa axis faced the door opening and the cabinet was squeezed to x = −1.65; and no single image of the gate captured both the sofa and the cabinet | Door opening shifted east to x 1.20~2.40, cabinet back at x = 0; `Camera_Media` switched to an 18 mm wide angle retreated to the northeast corner |
| 「三张视图均只呈现一块浅灰色布纹矩形面板」 ("All three views show only a light-gray fabric-textured rectangular panel") | The material library had no screen/plastic/steel, so the TV could only be made with linen; all three camera positions were also at the front, so the back could never be captured | Added the three keys `screen_black` / `plastic_black` / `steel_brushed`; added a back-view camera position for wall-mounted objects |
| 「墙面有明显横向色带与水渍状斑驳」 ("The wall has an obvious horizontal color band and water-stain-like mottling") | The `shape` of the three area lights was `DISK` and `size` was used as the diameter, which amounts to three glowing discs 3 m in diameter placed 0.5 m from the wall | All three changed to large-aperture rectangles and moved into the middle of the room, see below |

### The horizontal band on the wall: two rounds of investigation, it was the lighting

In the render the wall has a horizontal light/dark boundary running across it, which the verdict read as "water-stain-like mottling". The first round ruled out textures and normals:

- The dynamic range of the three plaster channels (diffuse / ARM / normal) is only ±3%, with diffuse mean 140±4;
- The wall is a closed six-sided box, with normal orientation 50/50, normal;
- **With all lights turned off and rendering with uniform ambient light only, the band disappears.** ⇒ It is definitely light.

But "turning lights off one by one" could not locate it: turning off one at a time, the band remained. Because each of the three lights draws an edge on the same wall, only a few centimetres apart: turn off one, and the edges of the other two stay near the same place.

The real root cause is `DISK`:

```python
d.shape == "DISK";  d.size == 3.0;  d.size_y == 0.25   # size_y is ignored
```

`DISK` has only one dimension, `size` (the diameter), and `size_y` is an invalid parameter for it. So the "3.0 × 0.25 m strip light" is actually a **disc 3 m in diameter**, placed 0.4~0.7 m from the wall. The emitting surface itself casts its outline on the wall: the disc edge is that hard edge running across the whole wall.

The fix was to change all three to `RECTANGLE` with larger apertures (key light 2.6 × 2.6 m, window light 1.9 × 1.3 m, side light 2.2 × 1.6 m), move the positions into the middle of the room (the key light moved to directly below the ceiling light, (0, −0.25, 2.80)), and recompute power for the new apertures (195 / 145 / 70 W). With the same four lights, the wall becomes uniform plaster, and the gate no longer mentions the wall.

> One fix made along the way: in `realistic_refiner.surface` the diffuse uses object-coordinate BOX projection, while the normal map goes through UV FLAT projection. This is not visible on furniture, but on a 4.35 × 2.9 m wall, a 2K normal map is stretched across the whole wall, and the low-frequency tilt becomes a metre-scale light/dark gradient. The normals now also use BOX projection (`box_project_normals`, 0.4 m tile). This change is correct in itself, but it is **not** the cause of the band: the band persisted after changing it.

---

## 11. Single-object asset packs (for TRELLIS)

15 independent packs, 46.8 MB in total, in `runtime/outputs/library/<id>/`:

```
<id>/
├─ <id>.glb             Y-up, metric, can go straight into the reconstruction pipeline
├─ <id>.blend           Z-up, origin at the bottom-face center of the bounding box
├─ views/
│   ├─ front.png        640×640, neutral background + ground + three-point lighting
│   ├─ three_quarter.png
│   ├─ side.png
│   └─ back.png
└─ meta.json            units/orientation/dimensions/mesh count/materials/source hash
```

`meta.json` convention: `units: metre`, `up_axis: Z in .blend / Y in .glb`, `forward_axis: -Y`, `origin: bottom centre of the bounding box`. These four are the traps most easily stepped on when connecting to TRELLIS, so they are written into the metadata.

Size distribution: bookshelf 20.0 MB (cabinet + 131 books), potted plant 8.5 MB (scanned soil), side table 4.9 MB, the two armchairs 2.7 MB each, wall painting 2.6 MB, coffee table 1.7 MB; the six generated objects are all very light (sofa 1.15, pendant light 1.06, floor lamp 0.67, media cabinet 0.43, door leaf 0.21, wall-mounted TV 0.14 MB). Total 46.8 MB.

> `room_shell` also has a pack (for inspecting the shell on its own), but it is a borrowed resource and does not take part in TRELLIS reconstruction, so the count is 15 reusable objects.

---

## 12. Known limitations

1. **The working version carries orphan data blocks**: of 437 materials only 167 are actually attached to slots; the other 270 are zero-user copies left by successive assembly appends, and they also occupy 217 extra texture data blocks. The delivery step first purges orphans and then merges same-source textures, so the deliverable is clean (167 materials / 120 textures / 167.5 MB); the working version itself is left untouched for rebuild speed; if needed, add one `orphans_purge` line at the end of the assembly script.
2. **Material count is high**: of the 167 materials, 131 are book covers (each of the 131 books has its own cover texture), each an independent material instance. To reduce the material count the covers would have to be made into an atlas.
3. **Hanging point is a constant**: the pendant light hangs at the fixed value z = 2.90, not looked up from the actual ceiling surface.
4. **The bookshelf is the heaviest object in the scene**: 152 meshes, 171 thousand triangles, 43% of the whole scene. If a real-time preview is needed later, the books need instancing or LOD.
5. **The TV has no cables**: the back has a cable guide channel, but there is no conduit or outlet panel leading up from the cabinet. Harmless from a distance, but a close-up would miss a link.
6. **The door is sealed shut**: the door leaf has no opening animation and there is no space behind the door: outside the shell is the void, and opening the door would show the world background directly. To demonstrate "the door can open", a vestibule box would first have to be added to the shell.
7. **The sofa is a 3-seater**: SceneCode's scene graph gave three-seater, but the original text requirement described a two-seat living room. Three seats make more sense in this 2.2 m bay (two seats would make the seating area look empty), but it is inconsistent with the original requirement and is left pending confirmation.
8. **The baseboard behind the bookshelf**: 35 mm is left behind the cabinet to clear the baseboard, and viewed closely from the side the 3 cm gap behind the cabinet is visible. Real installation does the same; it is not interpenetration.

---

## 13. Reproduction

```bash
# 1. Scene layout + clearance solving + supply plan
python adsl_adapter/clearance_solver.py
python adsl_adapter/asset_supply.py

# 2. Generate the seven missing objects (goes through the model API, 40 seconds ~ 3 minutes each)
python adsl_adapter/furniture_forge.py --object sofa_3seater
python adsl_adapter/furniture_forge.py --object floor_lamp
python adsl_adapter/furniture_forge.py --object ceiling_light
python adsl_adapter/furniture_forge.py --object tv_cabinet
python adsl_adapter/furniture_forge.py --object wall_tv
python adsl_adapter/furniture_forge.py --object entry_door
python adsl_adapter/furniture_forge.py --object bookshelf

# 3. Assembly + rendering
blender -b --python adsl_adapter/build_asset_scene.py

# 4. Export the 15 independent asset packs
blender -b --python adsl_adapter/export_asset_library.py

# 5. Package delivery (purge orphans → embed all textures → write to disk)
blender -b --python adsl_adapter/pack_delivery.py
blender -b --python adsl_adapter/pack_delivery.py -- --verify runtime/outputs/living_room/assets/living_room_assets_packed.blend

# 6. Provenance verification
python adsl_adapter/verify_provenance.py
```

### How the deliverable was verified

Packing is not done by just "clicking Pack Resources"; file size alone cannot show whether the textures come from inside the package or from disk. What is done here is a triple check:

| Check | Method | Result |
|---|---|---|
| No external dependencies | Reopen the deliverable file, iterate `bpy.data.images`, assert `packed_file` is non-empty, external links 0, linked libraries 0 | 120 / 120 embedded, external links **0** |
| Textures can really be read | Check that all embedded images have `size[0] != 0` (a failed read gives 0) | No failures |
| The image is unchanged | Re-render with the same camera and same parameters (1280×800 / 64 samples / AgX −0.30) and compare pixel by pixel with the original | Max diff **4/255**, mean 0.00018, only 3 pixels exceeded 2 (denoiser randomness); purple-block pixels 0, mean brightness 133.62 exactly identical |

Two cleanup steps were done before packing, which are the whole reason the size dropped from 758 MB to 167 MB:

| Stage | Materials | Texture data blocks | After dedup | Source file size | After packing |
|---|---:|---:|---:|---:|---:|
| Direct pack | 437 | 495 | — | 783.0 MB | 758.1 MB |
| ① Purge orphans | 167 | 278 | — | 349.6 MB | 346.4 MB |
| ② Merge same-source textures | 167 | 278 | 120 | 148.0 MB | **167.5 MB** |

- **Orphans**: of 437 materials only 167 are actually attached to slots; the other 270 are zero-user copies left by successive assembly appends, but they still hold texture references, so they were packed into the file anyway.
- **Same-source duplicates**: after purging orphans, 278 texture data blocks remained, yet they pointed to only **99 disk files**: the same set of PBR textures was imported once by every object. After merging with `image.user_remap()`, 120 remain (158 redundant slots); merging happens only when `path + color space + alpha mode + format` are all equal, so normal maps (Non-Color) are never mixed with base-color maps (sRGB).

> Note: the generation stage must use the system `Python 3.13` (with pydantic and the visual-gate dependencies); WorkBuddy's bundled interpreter lacks dependencies.
