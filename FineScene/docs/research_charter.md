# FineScene Research Charter

## Tentative Title

**FineScene: Constraint-Preserving Agentic Construction of Editable 3D Scenes with Local Generative Refinement**

Chinese working name: **面向可编辑三维场景的约束保持精细构造 Agent** (Constraint-Preserving Fine-Grained Construction Agent for Editable 3D Scenes).

## Core Claim

FineScene connects scene planning, a parametric whitebox, local refined assets and geometric verification into one shared scene contract. Structural parameters and dependencies are maintained by the procedural modeling system; high-frequency detail is supplied by asset retrieval or TRELLIS-class generators; edits propagate through the dependency graph and pass executable checks before assembly.

The paper's core should not be "multiple Agents are called", but rather: **how a shared scene contract makes locally generated results obey structural constraints, and lets parameter edits preserve the identity and geometric stability of unaffected components.**

## Role Boundaries

| Role | Input | Output | Modifiable scope |
|---|---|---|---|
| Planner | Description, reference images, edit instructions | Component graph, parameters, anchors, dependencies | Scene semantics and relationships |
| Builder | Planning result | aDSL whitebox, measurable bounds | Structural geometry |
| Refiner | Whitebox occupancy range, style, candidate assets | Refined mesh/material candidates | Local detail of designated components |
| Judge | Scene, candidates and constraints | Pass/reject, errors and repair suggestions | Select a candidate or trigger recomputation |
| Assembly | Passed components and transforms | Blender scene, GLB and report | Final placement, without changing contract semantics |

## Testable Hypotheses

### H1: Edit Stability

On the same structural edit tasks, a system with dependency propagation and component contracts changes unaffected components less than whole-scene regeneration, and keeps a higher contact/collision constraint pass rate.

### H2: Fineness-Editability Trade-off

At similar structural size error and constraint pass rate, locally generated or retrieved components have higher local visual quality than a pure primitive/aDSL whitebox.

### H3: Benefit of Domain Adaptation

After component-level adaptation on furniture, book and decor data, the local geometric quality and style consistency of TRELLIS candidates improve, without lowering the contract constraint pass rate.

H3 is a later experiment, not a prerequisite for the first-stage runnable closed loop.

## What the Dataset Should Record

Ordinary image-mesh pairs are insufficient for evaluating this project. Each scene sample should record at least:

- Description, reference images and scene-level goals;
- Component hierarchy, sizes, local coordinate frames, anchors and allowed occupancy ranges;
- Relationships such as attachment/contact/clearance;
- Whitebox, refined assets, transforms and source licenses;
- The contract before and after a parameter edit;
- The expected set of affected components;
- Results of size, contact, collision and semantic-integrity checks.

Edit trajectories are a key part of the dataset. For example, "increase the height of the second layer by 0.1 m" should annotate: which parameter changed, which shelf boards need recomputation, which books only need translation, and which components must be rejected or regenerated.

## First-Stage Experiment

First complete a deterministic closed loop for a single scene and a single edit, then extend to the dataset:

1. Read `examples/bookshelf_edit_case.json`;
2. Verify component IDs, relationship endpoints, bounds and provenance;
3. Apply `shelf_clearance += 0.1`;
4. Compute the transitive closure of affected components;
5. Output the edited contract and the verification report.

The first stage does not claim that TRELLIS has achieved high-quality generation, nor does it mix asset retrieval quality into structural edit verification.

## Required Baselines

- Pure aDSL/primitive: a baseline that is structurally correct but limited in detail;
- Whole-scene generation without a contract: a baseline that measures visual results but has poorer edit stability;
- Local refinement without a Judge: isolates the role of geometric constraint verification;
- Contract without calling the Refiner: isolates the role of local detail generation.

## Main Metrics

- Parameter and bounding-box error;
- attachment/contact/clearance pass rate;
- Collision and floating rate;
- Pose drift of unaffected components after an edit;
- Local geometric quality and material/style consistency;
- Number of affected components, recomputation time and peak VRAM.

## Practical Limits

The current TRELLIS.2 inference interface is mainly single-image-to-3D and cannot directly take text, aDSL meshes and size constraints as native multimodal input. The first version therefore treats TRELLIS as a component-level candidate generator rather than a structural controller for the whole scene. Full fine-tuning requires a separately prepared GPU budget; the local 5060 Ti is used first to verify the interface, asset import and lightweight experiments.
