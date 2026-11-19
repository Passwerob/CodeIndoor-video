# Hybrid ADSL + TRELLIS Related Work

Date: 2026-10-17

## Research question

Can an agent generate a complete editable 3D assembly as executable ADSL/Blender
code, delegate selected high-detail leaf components to a model such as TRELLIS,
and then register those meshes back through explicit interfaces so that structural
edits propagate without stretching the generated geometry?

## Short answer

Adjacent capabilities now exist, but the complete combination is not represented
by the works checked here. The closest precedents are assembly specifications with
ports and mates, code-generation harnesses with measurement and visual feedback,
physics-grounded part generation, and mesh-anchored procedural proxies. TRELLIS
itself supplies high-quality mesh generation and local editing, but does not define
an assembly contract or a parametric parent-child relation system.

## Terminology and search log

Queries covered: `part-aware 3D generation`, `3D assembly generation`,
`text-to-CAD assembly`, `editable 3D code agent`, `mesh-anchored procedural
proxy`, `physics-grounded 3D parts`, `TRELLIS structured 3D`.

Sources checked on 2026-10-17: arXiv API abstracts and method summaries, official
GitHub repositories/READMEs for AssemblyWorld and 3DCodeVerse, the Microsoft
TRELLIS repository, and the local ADSL implementation.

## Closest works

| Work | What it provides | Difference from our target |
|---|---|---|
| [ASSEMCAD](https://arxiv.org/abs/2607.05123) | An assembly specification with typed parts, geometry-backed ports, executable mates, engineering axioms, and deterministic validation. It also supports reusable parametric component factories. | Closest representation precedent. It targets CAD assembly generation and does not use a TRELLIS-style external neural mesh generator as a leaf-component provider, nor an ADSL/Blender code agent loop. |
| [CIT-CAD](https://arxiv.org/abs/2609.07434) | A Constraint Intent Tree guides CAD code generation and compares expected versus actual construction constraints for repair. | Strong precedent for construction-aware planning and verification, but the generated result is CAD code; it does not establish a mixed procedural/neural asset contract. |
| [ProxyBuild](https://arxiv.org/abs/2609.23386) | Mesh-Anchored Procedural Proxies decouple structured building generation into proxy prediction and asset instantiation, with hard spatial constraints and post-editable output. | Very close hybrid idea at the representation level, but focused on buildings and retrieval/instantiation of proxies, not arbitrary TRELLIS-generated local components inside a relational ADSL scene. |
| [3DCodeVerse](https://arxiv.org/abs/2610.22341) and [repository](https://github.com/gaoypeng/3dcodeverse) | A multi-agent DAG harness plans, writes, executes, measures, renders, and repairs executable 3D code. Measurements include dimensions, contacts, collisions, kinematics, and placement. | Closest agent-system precedent. It emphasizes executable code and verification, while our target adds a deliberate generation-mode decision and external mesh/component registry. |
| [SNAP3D](https://arxiv.org/abs/2609.13146) | Physics-grounded part generation, contact-graph recovery, parameterized connectors, simulation feedback, and stability evaluation. | Closest physical-validity precedent. It generates/refines parts from an image rather than combining a code-authored structure with a separately generated detail mesh. |
| [PhySGA](https://arxiv.org/abs/2610.20560) | Assembly Agent plus Critic Agent, action routers, physical simulation, and a self-evolving skill library for interactive composition. | Strong precedent for closed-loop physical assembly, but it is simulator-action oriented and does not expose a persistent editable program/mesh-component contract like ADSL. |
| [AssemblyWorld](https://arxiv.org/abs/2609.40353) and [repository](https://github.com/AssemblyWorld/assembly-world-bench) | Interactive benchmark for agents assembling supplied rigid parts from visual observations, with part accuracy and complete-assembly success metrics. | Benchmark for placement/assembly reasoning, not a generative pipeline and not a parametric code representation. |
| [LLM2R](https://arxiv.org/abs/2610.18604) | A language-model architecture that predicts SE(3) poses for many unassembled parts in one pass. | Solves pose prediction for an existing part set; it does not generate the parts, their interfaces, or editable structural code. |
| [TRELLIS](https://arxiv.org/abs/2412.01506), [repository](https://github.com/microsoft/TRELLIS) | Structured latent 3D generation with mesh output, variants, and local editing. | Supplies the high-detail mesh generator, not the assembly semantics. Its README does not define ports, mates, parent constraints, fit checks, or parameter-propagating edits. |

## Taxonomy

1. **Executable procedural code:** ADSL and 3DCodeVerse. The artifact is a
   program that can be rerun and checked.
2. **Assembly specification and constraints:** ASSEMCAD and CIT-CAD. The key
   artifact is an explicit graph of parts, interfaces, mates, and intent.
3. **Generated part sets and pose solving:** LLM2R, SNAP3D, and AssemblyWorld.
   The parts are supplied or generated separately and then arranged or validated.
4. **Hybrid structured asset instantiation:** ProxyBuild. A structured proxy
   mediates between a shell and detailed assets.
5. **Closed-loop physical composition:** PhySGA. Simulation feedback drives
   placement and repair.
6. **High-detail mesh generation:** TRELLIS. The output is a local mesh/latent
   asset rather than an assembly-aware program.

## Novelty collision test

| Candidate claim | Status | Evidence |
|---|---|---|
| An agent generates editable executable 3D code and verifies it with execution, measurement, and visual feedback. | Collision/strong precedent | 3DCodeVerse explicitly describes this harness. ADSL already has the same broad loop. |
| Assembly relations should be represented by typed interfaces, ports, mates, and constraints. | Collision/strong precedent | ASSEMCAD and CIT-CAD. |
| Generated parts should be checked for contact, penetration, and physical stability. | Partial precedent | SNAP3D and PhySGA; their physical/simulation settings differ from ADSL's local compile-time relation checks. |
| A high-detail neural mesh can be used as a rigid leaf component inside a code-authored, parameterized assembly, with its local proxy and interface preserved under edits. | No direct precedent found in the checked set | TRELLIS provides local meshes; ASSEMCAD/ProxyBuild provide structured assembly ideas; no checked work combines both in this persistent code-level contract. This is the most promising claim, but requires a wider search of concurrent repositories and papers before making a priority claim. |
| The agent can choose generation mode per component: ADSL primitive/code versus TRELLIS/external mesh. | No direct precedent found in the checked set | Adjacent work supports code generation or mesh instantiation separately; no verified work exposes this as a first-class planner decision. |

## Practical positioning for this project

The defensible contribution is not “we use TRELLIS” or “we use an LLM to make
3D.” Those are established. The distinctive system boundary is:

```text
component plan
  -> generation_mode = parametric_code | external_mesh
  -> stable component id + local frame + installation interface
  -> external generator produces mesh
  -> explicit local AABB/collision proxy is registered
  -> ADSL relations place and move the component
  -> code/measurement/visual/physical checks decide repair or regeneration
```

The strongest early demonstration should be a code-authored bookshelf or chair
with one ornamental, non-load-bearing TRELLIS component. Structural parameters
should move the component through its parent relation while preserving mesh size;
changing its style should regenerate only the leaf mesh. Report interface error,
penetration, fit/clearance, rigid-size preservation, and edit propagation rather
than only final-image quality.

## Coverage limits

This is a targeted first-pass search, not a complete citation survey. It used
arXiv abstracts/method summaries and public repository READMEs; full PDFs,
closed-source systems, patents, and non-English venues were not exhaustively
searched. Several 2026 concurrent papers are very recent, so repository/code
availability and claims may change. The “no direct precedent” statements above
mean none was found in this checked set, not proof that no such system exists.
