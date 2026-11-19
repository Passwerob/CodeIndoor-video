# Writing physical assembly relations into the modeling system

The old bookshelf could produce a correct appearance, but `height` did not drive the geometry, and the side
panels and cornice did not depend on the shelf positions. It is now a frozen counterexample: `examples/counterexamples/vintage_bookshelf_v1/`.
The original work `outputs/vintage_bookshelf_20261017/` is kept.

The new version does not bulk-add numbers on top of the old coordinates; instead it stores
construction relations in `adsl.core.RelationAssembly`. On `compile`, values are evaluated along the dependencies, regenerating an ordinary aDSL `Asset`.

| Physical intent | Executable relation | When dimensions change |
|---|---|---|
| A vertical panel connects its top and bottom ends | `span_box(bottom, top)` | Only the vertical span is extended; thickness is kept |
| An object is supported by another object | `supported(shape, on=part)` | Moves rigidly to the supporting top surface |
| A decoration is fixed to a part | `attached(shape, to=part, ...)` | Follows the anchor, keeping its own shape |
| Books must fit | `within(part, lower, upper)` | Explicitly rejected when it does not fit |
| Shelf height determines the next shelf | `datum(name, expression)` | Propagates shelf by shelf, then updates the top surface |

```python
from adsl.core import Cube, RelationAssembly

a = RelationAssembly('Shelf')
top = a.parameter('shelf_top', .8)
a.box('shelf', (1, .3, .03), (0, 0, top-.015))
a.span_box('side', bottom=0, top=a.bound('shelf'),
           width=.04, depth=.3, x=-.52)
a.supported('book', Cube((.04, .2, .25)), on='shelf')
a.within('book', (-.5, -.15, top), (.5, .15, top+.35))
scene = a.compile({'shelf_top': .9}).scene
```

The real four-shelf example is in `examples/physical_bookshelf/model.py`:

```python
result, books = build(extra_heights=(.1, .1, .1, .1))
```

The height goes from 1.847 m to 2.247 m; the 64 books and the board thickness are unchanged. Shelf heights can be changed individually.
In the new presentation stage, the 36 extra book-spine text objects are grouped under their own book parents, completing the ownership for editing.
The Agent Coder prompt and the DSL documentation also gain a requirement to prefer expressing assembly relations.

## Verified and not covered

`venv/Scripts/python.exe scripts/test_relations.py`: 12 checks, including default per-primitive geometry
consistency, original source file hash consistency, changing each shelf height separately and all four together, rejection when it does not fit,
rejection of cyclic dependencies, rejection of a missing support target, and rejection of invalid span sizes.

The bookshelf's width, depth and four-shelf count follow the existing design; this round mainly brings vertical-dimension linkage into the system.
The relation layer is a Z-up geometric constraint. AABB support cannot prove stability when leaning, friction, load-bearing or mesh
collision. The source program is the complete basis for rebuilding; the relation audit record is stored separately as JSON and embedded in the new `.blend`
text block; ordinary exports such as GLB do not solve dependencies live on their own.

## Code structure + generated mesh components

`ExternalMesh(path, bounds)` can add a fine GLB from a generator such as TRELLIS to the relation assembly as a rigid visual
component. Code is responsible for dimensions, interfaces, motion and verification; the GLB is responsible for local appearance such as carving, handles and
ornaments. `bounds` is an explicit bounding proxy of the generated mesh in local coordinates, so relation solving does not
need to enter Blender first; Blender loads the original GLB only at export. The component can move with a shelf or drawer, but structural parameters do not implicitly stretch it. For an example see
`examples/mixed_trellis_component/model.py`, with output in
`outputs/mixed_trellis_component_20261017`.
