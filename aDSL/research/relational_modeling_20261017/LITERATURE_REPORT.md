# Structure-Relation-Driven 3D Modeling and Editing: Literature Survey

Search date: 2026-10-17. Main competing-work window: 2024-2026; backward tracing has no lower year bound. This report is compiled from paper bodies, official paper pages, and code documentation; no third-party system was run, so the capability comparisons do not represent reproduction experiments.

## Conclusions and Research Questions

**This has been done before, and there are direct precedents.** "After changing one dimension, the other parts still obey the original relations" is usually called **design intent**, **constraint-preserving editing**, **structure-preserving shape manipulation**, or **edit propagation**. It has a long history in traditional parametric CAD as well as recent LLM-oriented research.

Our specific problem: given a bookshelf composed of independent parts, increase the clear height of each of the four tiers by 0.1 m. The system should automatically lengthen the side panels and back panel, move the shelf boards and the top, make the books move with their supporting surfaces while keeping the books' own dimensions, and detect conflicts such as items that no longer fit. The candidate approach is to organize parameters, datum planes, parts, and relations into an executable model.

The current implementation is a system in which relations are defined by hand, evaluated by dependency, and geometry is rebuilt. `span_box`, `supported`, `attached`, and `within` handle spanning members, support, attachment, and containment respectively. It is not a general constraint solver, and it has no simulation of gravity, friction, load bearing, or structural mechanics; one cannot claim from it that complete physical modeling has been implemented.

Candidate novelty statements for testing (derived from the project goals; they do not represent paper claims we have made):

- **N1: No method automatically propagates changes to other parts from a single local edit while maintaining relations.** Directly covered by work such as ParSEL.
- **N2: No LLM-oriented hierarchical DSL hands relational constraints to a solver to improve model editability.** Directly covered by AIDL; ShapeAssembly provides an even earlier 3D assembly-language precedent.
- **N3: No evaluation distinguishes "initial shape is correct" from "design intent is preserved after a dimension change."** Directly covered by HistCAD.
- **N4: Inferring functional relations from ordinary LLM modeling code or parts, distinguishing rigid items from stretchable structures, and verifying multi-step dimension interventions.** There are several partial precedents, especially ParSEL, ShapeLib, AIDL, and HistCAD; this round is not enough to show that the full combination is a blank space.

## Terminology and Method Taxonomy

| Our phrasing | Searchable terms | Concepts to distinguish |
|---|---|---|
| Change one dimension, other parts follow | design intent; edit propagation; parametric dependencies | Merely exposing parameters as sliders does not necessarily handle dependencies |
| Panels lengthen, books keep their size | structure-preserving editing; part-aware editing; rigidity constraints | Mesh deformation differs from rigid-body pose change |
| Book rests on a shelf board | attachment; contact; support relation; assembly constraints | Geometric contact is not the same as stable support or mechanical feasibility |
| Relations enter the modeling language | constraint-based CAD; solver-aided DSL; hierarchical assembly language | A one-way expression graph differs from simultaneous constraint solving |
| Still correct after the edit | constraint preservation; editability evaluation; dimensional intervention | Static IoU and successful compilation cannot answer this alone |
| Learning such relations automatically | constraint inference; programmatic shape abstraction; inverse procedural modeling | Reusing functions does not automatically guarantee relation correctness |

Using "representation, how relations are obtained, execution mechanism, evaluation target" as classification dimensions, each core work is assigned to one primary class:

| Primary class | Characteristics | Core works |
|---|---|---|
| Assembly / hierarchical languages | Express part relations explicitly in the program | ShapeAssembly, AIDL |
| Edit propagation | Expand an initial edit into a complete edit program | ParSEL |
| Constraint learning and editability evaluation | Predict constraints and check editing behavior | Aligning Constraint Generation, HistCAD |
| Construction-intent verification | Generate code from intent and extract relations for checking | CIT-CAD |
| Reusable program abstractions and parameter graphs | Functions or graphs expose semantically meaningful edit controls | ShapeLib, Proc3D |

## Core Works and Verification Records

### ParSEL: Parameterized Shape Editing with Language

- **Publication and sources:** SIGGRAPH Asia / ACM TOG 2024; [paper](https://arxiv.org/abs/2405.20319), [project](https://bardofcodes.github.io/parsel/), [code](https://github.com/BardOfCodes/parsel). The body's method, experiments, limitations, and the code README were checked.
- **Problem and input:** A segmented 3D mesh and a natural-language edit request; the goal is an edit program whose magnitude can be adjusted continuously.
- **Method and representation:** An LLM proposes a seed edit; Analytical Edit Propagation (AEP) adds analytical edits on related parts, maintaining connection and symmetry relations. Parts are proxied by hexahedral control cages initialized from bounding boxes, and deformation is propagated to the mesh.
- **Supervision and evaluation:** Combines a pretrained LLM, structural relations, and analytical search; evaluates geometric/structural differences of edit programs and human intent matching, and includes a propagation-component ablation.
- **Limitations:** Edits are restricted to the family of affine transformations of the proxy geometry; complex searches can exceed 500 seconds. When no edit satisfies all constraints, the result that breaks the fewest constraints may be chosen; this cannot be described as a universal correctness guarantee.
- **Relation to the bookshelf:** It most directly corresponds to "after editing a tier height, derive how the other parts must change." It also derives secondary edits automatically, which exceeds our current hand-written dependencies; but its connection/symmetry preservation should not be interpreted as complete support mechanics.

### A Solver-Aided Hierarchical Language for LLM-Driven CAD Design (AIDL)

- **Publication and sources:** Computer Graphics Forum 2025, 44(7), e70250; [paper](https://arxiv.org/abs/2502.09819), [publisher DOI](https://doi.org/10.1111/cgf.70250), [code](https://github.com/deGravity/aidl). The body and README were checked; publisher full-text access was blocked, and publication metadata was verified via OpenAlex.
- **Problem and input:** Have an LLM generate more easily editable CAD programs from text, reducing explicit coordinate reasoning.
- **Method and representation:** Hierarchical Structures with semantic names, geometric references, and constraints, with a recursive constraint-solving backend; the code uses SolveSpace-related solving facilities.
- **Supervision and evaluation:** Few-shot prompting, mainly **2D text-to-CAD**; compares against OpenSCAD, ablates hierarchy and constraints, and uses CLIP, perceptual studies, and editing examples.
- **Limitations:** The paper's empirical scope is mainly 2D; feedback-based error correction may obtain executable results by deleting complex design content. One cannot claim from it that general 3D furniture support has been verified.
- **Relation to the bookshelf:** The closest system design to "writing object relations into a DSL." Our forward expression graph is simpler than its simultaneous solving; renaming the API does not constitute a substantive difference.

### Aligning Constraint Generation with Design Intent in Parametric CAD

- **Publication and sources:** ICCV 2025, 8613-8622; [official paper page](https://openaccess.thecvf.com/content/ICCV2025/html/Casey_Aligning_Constraint_Generation_with_Design_Intent_in_Parametric_CAD_ICCV_2025_paper.html), [full text](https://arxiv.org/abs/2504.13178). The official abstract and paper body were checked.
- **Problem and method:** Targets constraint generation for 2D engineering sketches, aligning the model with real sketch-solver feedback; compares several alignment training schemes.
- **Data and evaluation:** A sketch constraint-generation task, focusing on solver outcomes such as fully constrained; the body reports the fully constrained ratio rising from 34% for naive SFT to 93%. These are results under that paper's setting.
- **Limitations:** Being fully constrained does not automatically mean matching the designer's intent; nor does it directly cover book-and-board semantics in assemblies.
- **Relation to the bookshelf:** It explicitly ties "design intent" to part relations propagating with modifications, showing that this is a mature problem formulation; it provides a precedent for learning relations from execution feedback.

### HistCAD: Constraint-Aware Parametric CAD Histories for Evaluating Editability

- **Status and sources:** Currently an arXiv preprint; the version read this time is v4, revised 2026-10-08; [paper](https://arxiv.org/abs/2602.19171). The abstract, task, evaluation, and results were checked. The page's first-submission date and the month of the identifier are inconsistent; this report does not infer the first-submission date from the identifier.
- **Problem and representation:** The same parametric construction history, under different sketch constraints, has identical initial geometry but may behave differently after a dimension change; it provides 180,495 executable histories, including entity-reference constraints and subsequent feature operations.
- **Protocol:** The predictor sees a history with explicit constraints removed and, without knowing the edit request, predicts the constraints of all sketches; the same dimension intervention is then applied to the same history.
- **Success criteria:** Initial geometry matches, the target dimension is reached, the specified relations and unedited dimensions are preserved, and the whole history rebuilds successfully.
- **Results and limitations:** Under the paper's specified joint training and description conditions, the overall edit success rate on Academic / Industrial is 52.4% / 29.0%; the scope is sketch-feature histories and cannot be used directly as a furniture-assembly benchmark.
- **Relation to the bookshelf:** The closest to our experimental idea of keeping counterexamples: V1/V2 have identical initial appearance, but their editability must be distinguished through actual modification. It also states explicitly that "can rebuild" is not enough to show the modification is correct.

### CIT-CAD: Constraint Intent Tree-based CAD Code Generation and Verification

- **Status and sources:** arXiv preprint from 2026-09, v2 read this time; [paper](https://arxiv.org/abs/2609.07434). The body's method, verifier scope, experiments, and limitations were checked.
- **Input and representation:** Text is converted into a Constraint Intent Tree that records entities, hierarchy, attributes, Boolean roles, and relations, and then CadQuery code is generated.
- **Method and evaluation:** Deterministically extracts geometric/construction evidence and checks it against the intent, and locally repairs violations; under a filtered multi-entity Text2CAD setting, checks validity, geometry, and constraint satisfaction rate. The repair acceptance rule requires keeping already-satisfied items and reducing violated items.
- **Limitations:** The verification vocabulary covers sketch fields, Boolean roles, intersection, contact types, coplanarity, shared axes, and so on; the paper explicitly does not cover arbitrary parameter dependencies, all exact symmetries, tolerances, or manufacturing constraints. Errors in the intent tree also cause the wrong target to be checked.
- **Relation to the bookshelf:** Well suited for borrowing "relation declaration - code - verification - repair," but **a static construction-intent check cannot replace a dependency-preservation check after a dimension intervention**.

### ShapeLib: Designing a library of programmatic 3D shape abstractions with Large Language Models

- **Publication and sources:** ACM TOG 2026; [paper/project](https://rkjones4.github.io/shapelib.html), [PDF](https://rkjones4.github.io/pdf/shapelib.pdf), [DOI](https://doi.org/10.1145/3840293), [code](https://github.com/rkjones4/ShapeLib). The body, project page, and code README were checked.
- **Problem and method:** The user supplies function design intent and a few seed shapes; the LLM proposes function interfaces, applications, and implementations, and usable abstractions are selected through geometric verification, forming a programmatic 3D part library; supports reconstruction, generation, and semantically meaningful editing.
- **Representation and evaluation:** Reusable shape functions with parameters, plus a model that recognizes geometry-to-program; the paper includes targeted edits and human plausibility / request-matching evaluation.
- **Limitations:** Geometric seeds can filter some runtime and semantic errors, but this does not amount to a constraint proof for all subsequent parameter modifications; consistency between the function design intent and the seeds affects results.
- **Relation to the bookshelf:** If studying "automatically inducing reusable, editable relation functions," it is a neighbor that must be compared; one cannot claim in general that previously there were only hard-coded relations.

### Proc3D: Procedural 3D Generation and Parametric Editing of 3D Shapes with Large Language Models

- **Status and sources:** arXiv preprint from 2026-01; [paper](https://arxiv.org/abs/2601.12234). The body was checked; no official code release could be verified, and no guessed repository link is provided.
- **Problem and method:** An LLM generates a Procedural Compact Graph, which is interpreted into Blender Geometry Nodes, exposing sliders/switches to support parametric editing.
- **Data and evaluation:** Uses hierarchically segmented shapes and procedural representations; evaluates generation quality, graph compactness, and interactive editing efficiency.
- **Limitations:** The paper's implementation is mainly based on representations such as cuboid parts; a parameter graph and editing efficiency alone cannot prove that support, rigidity, and edit invariants are preserved.
- **Relation to the bookshelf:** Close to the aDSL/Blender workflow; a neighbor in procedurally controllable modeling, but "a parameter graph" alone does not establish that all of our relational semantics are solved.

### ShapeAssembly: Learning to Generate Programs for 3D Shape Structure Synthesis

- **Publication and sources:** SIGGRAPH Asia / ACM TOG 2020; [project](https://rkjones4.github.io/shapeAssembly.html), [PDF](https://rkjones4.github.io/pdf/shapeAssembly.pdf), [code](https://github.com/rkjones4/shapeAssembly). The body and README were checked; note the capital A in the project URL.
- **Method and representation:** Cuboid proxies, hierarchical structure, and assembly commands such as `attach`, `squeeze`, reflection, and translation; program execution constructs part relations, and generation of such programs is learned.
- **Data and evaluation:** Extracts assembly programs from segmented shapes and evaluates the geometry and structure produced by the programs; its filtered representation is limited by part count and proxy complexity.
- **Limitations:** A proxy assembly language is not the same as full furniture geometry or physical simulation.
- **Relation to the bookshelf:** Relations such as `squeeze` are an early explicit precedent for "a member spanning two boundaries." Putting relations into a 3D DSL is not itself new.

## Lineage, Backward and Forward Tracing

The conceptual evolution can be summarized as: design intent and structure preservation in parametric CAD -> relational 3D assembly programs -> LLM initial edits with analytical propagation, and LLM-oriented constraint languages -> design-intent learning, construction verification, and evaluation of actual editing success. The arrows here denote conceptual evolution, not automatically a direct citation between two papers.

| Seed | Backward check | Forward tracing and results |
|---|---|---|
| ParSEL | Body compares existing structure-preserving / natural-language editing, emphasizing complete edit programs and analytical propagation | OpenAlex returned 7 for W4404526118; ShapeLib was found and checked in full text. The Proc3D body also discusses ParSEL |
| AIDL | Body's CAD DSL taxonomy and related work on dependencies, hierarchy, and solving | The publication record returned 7; the HistCAD body references also include AIDL. The remaining hits are only candidates, not all checked against full text |
| Aligning Constraint Generation | Body traces sketch constraint generation, the definition of design intent, and solver feedback | OpenAlex returned 4; HistCAD explicitly discusses the difference between constraint prediction and edit preservation |
| HistCAD | Read its related work on sketch generation, design intent, and history representation; includes the learned-constraint direction above | OpenAlex returned 0 this time; the new version is recent, which cannot be read as no follow-up work |
| CIT-CAD | Read the body's generate-verify-repair positioning and verifier scope | OpenAlex returned 0 this time; the preprint was just released, so coverage is insufficient |
| Proc3D | Body's comparison with procedural editing work such as ParSEL | Returned 0 for the corresponding record; research impact cannot be inferred from this |
| ShapeLib | Read its positioning on program abstraction, LLMs, and shape-relation reasoning | Added through ParSEL forward tracing, with its citations and method checked; not independently exhaustively enumerated for all citing literature |
| ShapeAssembly | Read related work and methods on assembly programs and part structure | 28 returned since 2025, with metadata of the first 10 read; not all verified in full text |

Candidate historical extensions also include the 2011 Component-wise Controllers and the 2016 analysis of CAD design reusability; their verification depth is insufficient, so they are placed at the end and not used to support any specific algorithmic conclusion.

## Competing Work Closest to the Current Code

Four dimensions are compared uniformly: input/representation, source of relations, execution mechanism, and evaluation target. The differences below have functional consequences; simple renaming is not a difference.

| Work | Four-dimension differences from the current implementation | Nature and implication |
|---|---|---|
| ParSEL | Segmented mesh rather than an aDSL generator; automatically expands secondary edits rather than hand-written dependencies; analytical search/deformation rather than forward rebuild; edit programs and structural evaluation | Substantive difference, but its automatic propagation is stronger, so it cannot be treated as an advantage we already have |
| AIDL | Hierarchical constraint programs; LLM declares relations; recursive simultaneous solving rather than directed expression evaluation; mainly 2D generation/editing experiments | Substantive difference. Our 3D engineering instance has an application difference, but this does not automatically constitute a methodological innovation |
| HistCAD | Sketch-feature histories; learned constraint completion; via CAD solving/rebuild; strict dimension-intervention evaluation | Substantive difference in setting; "counterexamples + post-edit verification" clearly overlaps in evaluation principle |
| CIT-CAD | Text intent tree and CadQuery; extracts/declares construction relations; deterministic verification and repair; mainly static construction-intent evaluation | Substantive difference; its verification mechanism can be borrowed, but dependency and intervention semantics must be added |
| ShapeLib | Seed shapes and function intent; LLM induces abstractions; executes parametric functions; reconstruction/generation/targeted-edit evaluation | Substantive difference; if we learn relations automatically, it becomes a closer competing method |

### Novelty Collision Assessment

| Statement | Status | Evidence |
|---|---|---|
| N1 automatic edit propagation | collision | ParSEL directly studies seed edit -> complete, relation-preserving parametric edit programs |
| N2 hierarchical constraint DSL | collision | AIDL is explicitly designed for LLMs around dependencies, constraints, semantics, and hierarchy; ShapeAssembly is an earlier 3D assembly precedent |
| N3 separating initially correct from edit-correct | collision | HistCAD specifically adds preserving relations and unchanged dimensions to its edit-success criteria |
| N4 automatic functional relations + rigid/stretchable distinction + multi-step intervention verification | partial precedent | ParSEL, ShapeLib, AIDL, and HistCAD each cover important parts; there is not enough evidence to assert that no one has studied the full combination |

## Negative Evidence and Failure Modes

| Work | Scheme examined | Phenomenon reported by the paper | Implication for us |
|---|---|---|---|
| ParSEL | Directly asking the LLM for a complete edit program; propagation-component ablation | The LLM can find the initial edit but tends to miss secondary changes; propagation search also has representation and time limits | Single-shot code generation should not replace relation propagation and edit checking |
| AIDL | Removing constraints or removing hierarchy | Without constraints, detached parts can appear; without hierarchy, local edits can break the structure | Relations and module boundaries need to be explicit; merely adding common parameters is not enough |
| HistCAD | Substitute constraint settings such as retaining only some connection constraints | Can reach the target and rebuild, yet break relations or dimensions that should be preserved | Successful compilation, target dimension, and initial geometry are each insufficient criteria |
| CIT-CAD | Implemented relation detectors | Explicitly does not cover design intent such as arbitrary parameter dependencies | Verification conclusions must state their coverage |
| ShapeLib | LLM candidate functions and seed verification | Candidates have runtime or semantic errors and need filtering; passing the seeds is not a universal guarantee | Should hold out unseen shapes and parameter-modification tests |

These are limitations or ablation evidence under stated conditions; they do not mean the corresponding systems fail in all scenarios.

## Recommendations for Our Project

**Engineering-wise, keeping the relation layer is reasonable; research-wise, "has parameters and moves together" cannot be the novelty point on its own.** A more worthwhile candidate problem to test: automatically recover a functional relation graph from raw programs/parts, decide which members stretch with boundaries and which items only move as rigid bodies, and maintain these relations under unseen multi-step modifications; when a conflict occurs, give a locatable cause.

| Candidate problem | Current evidence and openness |
|---|---|
| Automatic relation recovery that distinguishes structure from contents | May have research value, but ParSEL/ShapeLib already provide strong partial precedents; inputs and relation types need further checking before a narrowly scoped claim can be made |
| Extending from static verification to multi-step dimension intervention | HistCAD already has a direct principle precedent; a concrete protocol for furniture assembly may have room, but the evaluation idea cannot be claimed as original |
| Explanation of unsatisfiable modifications, minimal conflicting relation sets | This round did not systematically search the constraint diagnosis/explanation area, so it cannot be treated as a confirmed gap |
| Physical feasibility such as load bearing, friction, center of gravity | The current code and the main opponent comparisons this round are insufficient to answer it; adding it would fall within a new round of physical simulation / inverse design search |

### Executable Comparison and Evaluation Plan (proposed, not yet executed)

Baselines first use the original static version V1, a manually centralized-parameter version, and the current relation graph V2, separating the roles of "shared numbers" and "explicit relations." Choose adaptations or public cases of ParSEL and AIDL according to input comparability; scores of systems with different input conditions must not be ranked directly against each other.

Borrowing from HistCAD, take initial geometry match as the precondition, then apply the same dimension intervention. The bookshelf should at least check: target clear height, side-panel/back-panel span, shelf-board/crown position, each book's size and support contact, containment and collision, and full rebuild. The current `supported` AABB contact/footprint rule is not a general stability or collision check, and the implementation scope must be declared separately.

Report each preservation rate and the overall all-pass rate, conflict detection rate / false alarm rate, and rebuild time. Beyond the initial parameters, hold out modification magnitudes, combinations, and orderings; automatic inference methods should also hold out object and relation templates. The LLM generation stage should be repeated several times, with deterministic rebuilding verified separately. This round did not establish a cross-paper common seed count, data split, or unified metric; these are proposed protocols and must not be passed off as domain standards.

Recommended deep-reading order: **ParSEL -> AIDL -> HistCAD**; then use ShapeLib to examine the automatic-abstraction direction, and CIT-CAD to borrow the verification chain.

## Search Record and Coverage Limits

The complete queries, hit counts, actual retrieved counts, and related files are in [SEARCH_LOG.md](SEARCH_LOG.md). Machine-readable results are saved in the six `*search.json` / `forward_*.json` files in the same directory; the extracted body text and two PDFs are also saved in this directory.

1. This round mainly relied on the OpenAlex index to discover works, then opened arXiv, CVF, and the authors' projects and code. Google/DuckDuckGo access was blocked; Bing results had abnormal relevance and were not used as paper evidence. The ACM, IEEE, CAD/CAE engineering databases and Chinese databases were not searched systematically.
2. Competing work from 2024-2026 was prioritized, with backward tracing into history; this is not a systematic survey of the whole field. The total returned by OpenAlex does not equal the number actually read, and preprint/published versions may be duplicated; zero citing results do not mean there is no follow-up work.
3. Many 2026 results are still preprints, which this report marks explicitly; conclusions may change with versions. ShapeLib's month differs across records, so only 2026 is recorded. HistCAD uses an explicit v4 revision date.
4. A total of 8 core works had their body methods checked; for code, only the public documentation was checked, with no installation, running, or reproduction. Research recommendations in the text that are our own inferences have been separated from paper facts.
5. Graphics editing, parametric CAD, and functional physical constraints are not entirely equivalent; this round concentrated on geometric/structural dependencies and did not adequately cover mechanics, robot-scene support, or all constraint-diagnosis research.

### Insufficiently Verified and Excluded Items

- [Component-wise Controllers for Structure-Preserving Shape Manipulation (2011)](https://doi.org/10.1111/j.1467-8659.2011.01880.x): metadata/abstract checked, full text not read; no specific algorithmic guarantee is asserted from it.
- [Parametric CAD modeling: An analysis of strategies for design reusability (2016)](https://doi.org/10.1016/j.cad.2016.01.003): found through historical search and reference discovery, full text not read.
- SketchGraphs, ShapeMOD, Infinigen Indoors: candidates from historical or adjacent directions; body-method verification was not completed this round, so they are not listed as core comparisons.
- CAD-Editor, CADMorph, AgenticCADedit, ConCAD: adjacent editing/constraint-generation bodies were checked, and nothing was seen that would change the core positioning above, so they are not expanded into full work cards for now; this does not mean they were judged irrelevant.
- Single-word searches for `aDSL`, `AIDL`, `GeoCode`, and `ParSEL` hit same-named items such as network protocols, Android, geocoding, or cadastral records; the irrelevant hits were excluded. Broad negative keywords hit large numbers of non-target fields, and counts cannot be used to prove a gap.
- `CADtraditional.txt` is a generic help-page redirect and `AIDL_DOI.txt` is an access-failure response; neither is used as technical evidence. The unrelated arXiv ID 2009.03692 that was tried does not belong to ShapeAssembly and was excluded; ShapeAssembly uses the official PDF verified above.
