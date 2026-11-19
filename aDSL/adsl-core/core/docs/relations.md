# Re-evaluated assembly relations

`RelationAssembly` adds persistent construction recipes above ordinary `Asset`
geometry. Unlike a one-time `align_anchors` placement, it retains dependencies
and rebuilds them whenever `compile(overrides)` runs.

```python
from adsl.core import *

model = RelationAssembly('Shelf and book')
height = model.parameter('shelf_top', .8)
model.box('shelf', (1, .3, .03), (0, 0, height-.015))
model.span_box('side', bottom=0, top=model.bound('shelf'),
               width=.04, depth=.3, x=-.52)
model.supported('book', Cube((.04, .2, .25)), on='shelf')
model.within('book', (-.5, -.15, height), (.5, .15, height+.35))
result = model.compile({'shelf_top': .9})
scene = result.scene
```

- `parameter(name, value)` returns a named scalar expression. Overrides accept
  only registered parameters; unknown names fail instead of being ignored.
- `datum(name, expression)` retains formulas made with `+`, `-`, `*`, `/`.
- `bound(part, axis='z', side='max')` queries a solved part; forward references
  work. Cyclic scalar or geometry dependencies raise `RelationError`.
- `box` evaluates size and center expressions. `span_box` computes a positive
  Z span and its center from endpoints, preserving X/Y dimensions.
- `rigid` translates a copied subassembly without scaling it.
- `attached` aligns source and target AABB anchors plus an explicit offset.
- `supported` seats the source AABB bottom on target top, retains X/Y and size,
  and verifies positive overlap of both horizontal projection intervals.
- `within` rejects objects whose AABB cannot fit the stated lower/upper bounds.
- `/` in part ids creates semantic parent groups in the resulting Asset.
- `ExternalMesh(path, bounds)` adds a rigid visual component backed by a local
  GLB/GLTF resource. `bounds` is an explicit local AABB proxy used for support
  and fit checks; Blender imports the source mesh only during export. The
  resource follows `supported`/`attached` transforms but is never implicitly
  resized by a structural parameter.
- `result.parts`, `result.values`, and `result.report` expose solved geometry,
  datums, expression recipes and validation evidence. The report intentionally
  summarizes literal shape geometry; it is not a standalone replay format.

The graph lives at construction time. Save source along with the report and
the ordinary geometry export. An `asset.json` or GLB alone is a baked snapshot,
and direct Blender edits do not automatically rerun the graph. The bookshelf
example embeds the report as a Blender text block and adds relation metadata.

All relations here use Z-up geometric construction and AABB contact/fit. They
do not establish static equilibrium, leaning friction, load limits or material
elasticity. Unsupported projections and fit conflicts are explicit errors;
do not silently stretch contents, clamp dimensions or omit failing parts.

External meshes are visual resources, not editable parametric solids. Keep the
source path, proxy bounds and hash/version with the component definition. A
mesh can move with a parent assembly while retaining its local geometry; a
change to its shape requires replacing or regenerating the resource explicitly.
