# Search Log — Relational 3D Modeling

Date: 2026-10-17. Source: OpenAlex works API, followed by primary-source verification.

Counts below are index result counts, not numbers of papers read. Retrieved counts reflect only saved result batches. Duplicates and homonyms are present.

| File | Query / citation seed | Index count | Retrieved |
|---|---|---:|---:|
| openalex_search.json | parametric CAD | 134 | 12 |
| openalex_search.json | CAD editing | 33 | 12 |
| openalex_search.json | constraint CAD | 24 | 12 |
| openalex_search.json | procedural shape editing | 1 | 1 |
| openalex_search.json | design intent CAD | 6 | 6 |
| openalex_search.json | ShapeAssembly | 2 | 2 |
| openalex_search.json | ParSEL | 132 | 12 |
| openalex_search.json | GeoCode | 1348 | 12 |
| openalex_search.json | ShapeMOD | 2 | 2 |
| openalex_search.json | SketchGraphs | 2 | 2 |
| openalex_search.json | aDSL | 1871 | 12 |
| followup_search.json | AIDL | 14 | 8 |
| followup_search.json | constraint based 2D design language | 0 | 0 |
| followup_search.json | analytical edit propagation | 0 | 0 |
| followup_search.json | shape assembly constraint | 5 | 5 |
| followup_search.json | design intent constraint | 15 | 8 |
| precedents_search.json | solver aided hierarchical language | 2 | 2 |
| precedents_search.json | Parametric CAD modeling analysis strategies design reusability | 1 | 1 |
| precedents_search.json | component wise controllers structure preserving shape manipulation | 1 | 1 |
| precedents_search.json | Infinigen Indoors | 1 | 1 |
| forward_citations.json | W4404526118 | 7 | 7 |
| forward_citations.json | W7131420983 | 0 | 0 |
| forward_citations.json | W7212052628 | 0 | 0 |
| forward_citations.json | W7125139949 | 0 | 0 |
| forward_citations.json | W4416534954 | 4 | 4 |
| forward_additional.json | AIDL | 7 | 7 |
| forward_additional.json | ShapeAssembly | 28 | 10 |
| negative_search.json | parametric CAD editability failure | 3146 | 6 |
| negative_search.json | constraint inference design intent limitations | 32741 | 6 |
| negative_search.json | structure preserving editing contact rigidity | 9443 | 6 |

## Reproduction

- Initial recent-work title searches use `title.search:<query>,from_publication_date:2024-01-01,to_publication_date:2026-10-17`; historical named-work queries relax the lower date. The initial JSON includes exact request URLs.
- Named follow-up searches use OpenAlex title search. For the follow-up files that do not retain full URLs, use the saved query and records to reproduce discovery; exact filtering/ranking may not be reconstructable from those files alone.
- Citation searches use `cites:<OpenAlex work ID>` and the date window stated in the report. Published and arXiv versions can be separate records.
- Result totals and rankings can change as OpenAlex updates. Saved records are the evidence snapshot.

## Verified core works retained

| Work | Reason retained | Primary source |
|---|---|---|
| ParSEL | Analytical propagation of a seed edit | [Source](https://arxiv.org/abs/2405.20319) |
| AIDL | Hierarchical constraint DSL and solver | [Source](https://arxiv.org/abs/2502.09819) |
| Aligning Constraint Generation | Solver-feedback alignment for sketch constraints | [Source](https://openaccess.thecvf.com/content/ICCV2025/html/Casey_Aligning_Constraint_Generation_with_Design_Intent_in_Parametric_CAD_ICCV_2025_paper.html) |
| HistCAD | Intervention-based editability evaluation | [Source](https://arxiv.org/abs/2602.19171) |
| CIT-CAD | Explicit intent representation and deterministic verification | [Source](https://arxiv.org/abs/2609.07434) |
| ShapeLib | Learned programmatic abstractions with semantic edit controls | [Source](https://rkjones4.github.io/shapelib.html) |
| Proc3D | Procedural graph and Blender parametric editing | [Source](https://arxiv.org/abs/2601.12234) |
| ShapeAssembly | Historical 3D attachment DSL | [Source](https://rkjones4.github.io/shapeAssembly.html) |

## Rejected and limited discovery channels

- Google and DuckDuckGo were blocked. Bing responses were insufficiently relevant and are excluded as scientific evidence.
- Ambiguous acronym/name matches (Android AIDL, networking aDSL, geocoding GeoCode, cadastral ParSEL) are excluded.
- Negative-search index totals are broad. Actual failure/limitation evidence comes from full-text ablations and limitations in the retained works.
- Publisher access failures and generic help redirects are not evidence of model capabilities.
- Historical metadata-only candidates and incompletely followed citation trails are explicitly identified in LITERATURE_REPORT.md.
