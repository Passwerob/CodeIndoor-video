# FineScene System Design

## 1. System Goals

FineScene's goal is to construct, from a text description, reference images and edit instructions, a **complete 3D scene that is editable, verifiable and locally refinable**.

The first target scene category is a study room, not a single bookshelf. The bookshelf, desk, chair, desk lamp, rug, walls and decorations together make up the study scene; each object is in turn made of components that can be generated and edited independently.

The system must satisfy three conditions at once:

1. Scene-level spatial relations are correct, e.g. furniture lies inside the room, the desk is against the wall, the chair is in front of the desk;
2. Component-level parameters are editable, e.g. after changing the bookshelf layer height, the upper boards and books update along the dependency relations;
3. Local components can use retrieved or TRELLIS.2-generated high-detail assets without breaking sizes, interfaces and assembly relations.

The core of the system is not "how many Agents are called", but that all planning, generation, editing and verification share the same **scene contract**.

## 2. Overall Architecture

```text
User description / reference images / edit instructions
              |
              v
      Scene Planner
              |
              v
    Scene Contract
   /          |             \
  v           v              v
Layout     Object        Edit-dependency
Planner    Planner       Planner
  |           |              |
  |           v              |
  |   Component contract     |
  |   graph                  |
  |           |              |
  +-----------+--------------+
              v
      Contract Validator
              |
       +------+------+
       |             |
       v             v
  aDSL Builder    Asset Refiner
                  (retrieval / TRELLIS.2)
       |             |
       +------+------+
              v
      Assembly and evaluation
              |
              v
   Geometry Judge / Appearance Judge / Edit Judge
              |
       Pass, repair or reject
              |
              v
       Blender / GLB / report
```

The scene Planner does not directly generate all furniture geometry. It creates independent `furniture_tasks` for the bookshelf, desk, chair and so on, each task declaring the furniture type, placement position and local parameters. The furniture Builder routes by type to the corresponding aDSL component factory, which returns the local Asset, bounds, parameters and named interfaces; scene Assembly then combines these Assets at the positions given by the Planner. This way the internal structure of an object is the responsibility of the furniture factory, and cross-object layout is the responsibility of the scene contract.

Planner, Builder, Refiner, Judge and Assembly here are responsibility boundaries. The first version does not require each responsibility to correspond to an independent language-model Agent; they can be stages and tools within one orchestrator. This lets us first make state, contracts and failure reports stable, and then decide which stages are worth turning into independent Agents.

## 3. Three-Level Planning

### 3.1 Scene-Level Planning

The scene planner first turns "a vintage-style, readable study" into a spatial and functional structure:

- Room boundary, floor, walls, doors/windows and main orientation;
- Functional zones: reading zone, storage zone, writing zone, decoration zone;
- Object list: bookshelf, desk, chair, desk lamp, rug, books and decorations;
- Coarse size, position, orientation and style of the objects;
- Relations between objects: against the wall, facing, located in, keeping a passage, supporting and containing.

This level only decides "what is in the scene, where, and why it is placed so", and does not directly generate each mesh.

### 3.2 Object-Level Planning

The object planner splits each scene object into editable components. For example, a bookshelf can contain a base, side panels, a back panel, shelf boards, top decoration, books and decor items; a desk can contain a tabletop, legs, drawers and desktop props; a chair can contain a backrest, seat cushion, armrests and legs.

Each component must declare:

- A stable component ID and parent;
- Local size, coordinate frame and named interfaces;
- Variable parameters and parameter ranges;
- Rigid geometry or rebuildable geometry;
- Component source: aDSL, retrieval, TRELLIS.2 or a manual asset;
- Inputs, outputs and the check conditions that must be preserved.

### 3.3 Edit-Level Planning

The edit planner answers "after a change, who must be recomputed". For example:

```text
Bookshelf shelf_2_clearance + 0.10 m
  -> poses of shelf_2, shelf_3, shelf_4 updated
  -> books above translate along the support chain
  -> side-panel and back-panel spans rebuilt
  -> book sizes and local details preserved
  -> unrelated desk, chair and lamp keep identity and pose
```

It outputs the closure of affected components, the components that need regeneration, the components that only need repositioning, and the rejection reason when a conflict occurs.

## 4. Scene Contract

The scene contract is the single source of truth between modules, and contains at least:

- `scene_id`, units and coordinate system;
- Scene parameters, e.g. room width, bookshelf spacing, desktop height and passage width;
- Component tree;
- Relation graph;
- Parameter dependency graph;
- Component provenance and licenses;
- Edit operations and the expected affected set;
- Check items, check results and unsupported capabilities.

Three different graphs need to be kept:

1. **Ownership tree**: answers who manages a component;
2. **Relation graph**: answers what spatial or functional relation two components satisfy;
3. **Evaluation dependency graph**: answers in what order parameters and poses are computed.

The three cannot be merged into one tree. For example, a book belongs to the bookshelf in the semantic hierarchy, but its height may be determined by the support interface of the shelf board; the room and the bookshelf have a containment relation, but that does not mean the internal geometry of the books must be rebuilt when a room parameter changes.

## 5. Responsibilities of aDSL

aDSL serves as the deterministic structural Builder and evaluation backend, and is responsible for:

- Parameters, derived quantities and dependency propagation;
- Parametric geometry reconstruction of structural parts;
- Support, mounting, span and containment relations;
- Component hierarchy and deterministic layout;
- Bounding proxies and rigid positioning of external meshes;
- Outputting measurable Assets, GLB and relation reports.

For example, the clear height of a bookshelf is driven by inter-layer parameters, shelf boards and side panels are rebuilt by `span_box`, books are placed by `supported`, decor items between books use `attached`, and books and shelf cells are checked with `within`. aDSL is not responsible for inferring scene intent, and should not change the size of unauthorized components on its own.

The current aDSL relation solving is Z-up, AABB-level deterministic evaluation. It cannot automatically prove gravitational stability, friction, load capacity, or collision-freeness of arbitrarily rotated meshes. Relations beyond this scope must be marked `unsupported` in the contract or handed to a later physics checker.

## 6. Responsibilities of the Refiner

The Refiner only handles the local detail of designated components, and does not decide a component's position in the scene or its structural size. The target system takes TRELLIS.2 local object generation as the main path; component-level fine-tuning is a later extension. Existing component libraries and online assets are used as assembly baselines, comparison experiments or explicitly chosen fallback paths; tuning downloaded assets should not be the main implementation of the target generation system.

The target closed loop is: component contract → component reference image → TRELLIS.2 candidate → local multi-view and geometry acceptance → rigid assembly → scene acceptance. The current image-to-3D interface cannot natively enforce metric size, support interfaces or overall style constraints; these are still maintained by condition preparation, candidate acceptance and assembly.

Local revision should be executed according to the failure source: a generated object's missing parts or wrong shape are fed back to component generation; position, orientation and contact errors are fed back to assembly; global lighting, exposure and camera problems are fed back to rendering. Keep the components that have already passed and their stable IDs, to avoid a single object's failure triggering regeneration of the whole room. Different sources use the same acceptance criteria, and the failures of the existing low-VRAM bridge must not be taken as the quality ceiling of TRELLIS.

Implementation status (2026-10-19): the main orchestration is still mainly local asset assembly, and TRELLIS generation has not yet been wired into the main orchestration's automatic local retry. `src/preview_asset_candidates.py` already supports three views of any GLB/glTF, source hashes, real bounds and proportional-normalization verification; it is an evidence generator, not an automatic acceptor. The independent component Judge, constrained generation requests, caching and local retry are yet to be wired in.

The Refiner's inputs include component semantics, style, target bounding range, interface positions, allowed rotations and provenance constraints. The output must include the mesh, local coordinate frame, bounds, interface declarations and generation provenance. A generated mesh must not be stretched non-uniformly at will for size fitting; if a candidate does not satisfy the contract, it should be rejected or sent back to the generation stage.

For components that have already been through Blender refinement, it is recommended to keep two deliverables at once: the local bounding proxy at the aDSL/contract level, and the source `.blend` asset with the full Blender material nodes. GLB is suitable for exchange and preview, but it cannot be assumed to preserve all procedural material nodes. Assembly should use the aDSL proxy to determine the position in the scene, then append the object tree and materials of the source `.blend`, and finally output the scene `.blend` and a GLB snapshot.

Therefore TRELLIS.2 is a component-level high-detail candidate generator, not the layout planner of the whole study, and not a structural constraint solver.

## 7. Judge and Failure Handling

The Judge is divided into three mutually independent check surfaces:

### Geometry Judge

Checks sizes, bounds, support, mounting, gaps, collisions and room containment relations. Check results must cite specific components, relation IDs, expected values and actual values.

### Edit Judge

Runs an intervention test for each parameter edit:

- Whether all expected affected components were updated;
- Whether unaffected components keep their ID, geometry and pose;
- Whether rigid components were accidentally scaled;
- Whether restoring the original parameter returns to the original result;
- When unsatisfiable, whether the previous valid version is kept and the edit is explicitly rejected.

### Appearance Judge

Checks overall composition, style consistency and local detail quality. Passing appearance cannot replace passing geometry and edits; the three kinds of results must be recorded separately.

Failure repair should prefer modifying the contract, relations or component generators, rather than patching the final mesh directly with world coordinates. A candidate result does not overwrite the previous valid result.

## 8. Minimal Complete Instance of a Study

The first-version study can use the following components:

```text
study_room
├── room_shell
│   ├── floor
│   ├── walls
│   ├── window
│   └── door
├── bookshelf_zone
│   ├── bookshelf_01
│   │   ├── carcass
│   │   ├── shelves
│   │   ├── books
│   │   └── local_props
│   └── bookshelf_02
├── reading_zone
│   ├── desk
│   ├── chair
│   └── desk_lamp
├── circulation_zone
│   └── floor_clearance
└── style_layer
    ├── materials
    └── decoration_policy
```

The minimal runnable closed loop does not need to generate all refined assets from the start. The room, desk, chair and bookshelf can first use aDSL whiteboxes, and then a retrieval/TRELLIS.2 component can be connected for one book, one desk lamp or one chess piece, to verify that local high detail does not break overall relations.

## 9. Recommended Implementation Order

1. Extend the existing scene contract to study-level component and relation representations;
2. Add structured relation types, gradually replacing `ObjectPlan.relations: list[str]`;
3. Implement the study coarse layout and room containment/passage checks;
4. Wrap the bookshelf, desk and chair as independent aDSL component factories, called by the furniture-task router;
5. Connect component-level asset candidates and bounds fitting;
6. Add edit propagation and stability tests for unaffected components;
7. Only at the end connect TRELLIS.2 and more independent Agents to the Refiner and Judge.

The first end-to-end task suggested is: generate a vintage study with a bookshelf, desk and chair, then execute "increase the clear height of the bookshelf's second layer by 0.1 m". The system should be able to output the updated bookshelf, affected books and top structure, and prove that the desk, chair, room and unaffected decorations were not regenerated or drifted.

## 10. Statement of Research Contributions

FineScene's contributions can be stated as:

1. A shared scene contract for complete indoor scenes, connecting semantic planning, parametric modeling, local generation and assembly verification;
2. A relation-preserving local editing flow, so that structural parameter changes propagate only to the dependency closure, and the identity and geometric stability of unaffected components are kept;
3. An implementation combining the aDSL structural Builder with component-level generators, balancing editable layout structure and locally refined geometry;
4. A scene dataset and evaluation protocol containing component interfaces, assembly relations and edit trajectories.

The paper should emphasize verifiable edit stability and structure-detail synergy, rather than the number of Agents or single-render quality.
