# Counterexample V1: correct appearance, but incomplete structural dependencies

This is a source-code snapshot of the original four-tier bookshelf. It is kept for comparison and is not corrected here.
The original render and model are still in `outputs/vintage_bookshelf_20261017/`.

## Reproducible problems

1. `P['height']` does not take part in the structural computation: changing it from 1.84 to 2.24 leaves the actual geometry unchanged.
2. `shelf_tops` controls the heights of the shelf boards and books, but the dimensions of the side panels, back panel and crown moulding do not follow.
3. The `Asset` parent-child hierarchy expresses ownership; it does not itself define dependencies such as "supported" or "spans both ends".
4. Some spine text is attached to the overall root node at the Blender presentation stage, so it does not move with a book when that single book is moved.
5. AABB placement cannot prove the static stability of a leaning book, and it must not be equated with a full physics simulation.

## Acceptance conditions for the new version

With default parameters, the per-primitive world-space geometry matches this snapshot; changing only the four tier clear-height parameters,
the vertical members and the top move together while book and board thickness stay unchanged; a single-tier change affects only the parts that depend on it; if something
does not fit, a conflict should be reported. See `examples/physical_bookshelf/` and `scripts/test_relations.py`.

The old numbers in this snapshot are not themselves wrong; the point of the counterexample is the lack of dependency relations that can be re-evaluated.
