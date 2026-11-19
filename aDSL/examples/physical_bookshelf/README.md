# V2: Four-tier bookshelf with physical assembly relations

Copied from the frozen V1 and split into `model.py` (relational assembly) and `book_library.py`
(rigid geometry for 64 books). The core API is in `adsl-core/core/relations.py`.

```python
from model import build
baseline, books = build()
raised, books = build(extra_heights=(.1, .1, .1, .1))
scene = raised.scene
```

Relation chain: tier clear height → shelf board position → crown-moulding seat height → side panel/back panel span and top decoration;
shelf board → upright book or base book → horizontally stacked book above. Book geometry is not scaled.

- The first three tiers have an original clear height of .368 m; the top tier to the top datum is .400 m; board thickness is .032 m.
- Default overall height is 1.847 m; after adding .1 m to each of the four tiers it is 2.247 m.
- `build((0,.1,0,0))` increases only the clear height of the second tier: the books of the third and fourth tiers and the top move up.
- `relations.json` records parameters, expressions, dependencies and check results, and serves as the audit record.
  The entry point for a full rebuild is this Python source code; the ordinary `asset.json` is an evaluated geometry snapshot.
- The new presentation script assigns the spine text to the corresponding book and embeds the relation record in a `.blend` text block.
- The compiled product is an ordinary `Asset`, compatible with the existing GLB/Blender export, and does not change the old interface.

## Verification and export

Run from the project root:

```powershell
& .\venv\Scripts\python.exe -X utf8 scripts\test_relations.py
& .\venv\Scripts\python.exe -X utf8 examples\physical_bookshelf\prepare_demo.py
& .\venv\Scripts\python.exe scripts\local_3d.py build --source outputs\physical_bookshelf_20261017\raised_10cm\model.py --output outputs\physical_bookshelf_20261017\raised_10cm\base --no-render
& E:\Blender\blender.exe --background outputs\physical_bookshelf_20261017\raised_10cm\base\scene.blend --python-exit-code 1 --python outputs\physical_bookshelf_20261017\raised_10cm\present.py
```

The prepare script requires a new output directory, to avoid overwriting existing cases. The render script adjusts the camera to the height.

## Capability boundaries

This is evaluation and verification of source-level assembly relations: after changing parameters, re-run `compile` or `build`.
After exporting to Blender, dragging a part directly does not automatically run the Python graph solver.
The `supported` check tests Z-direction AABB contact and horizontal projection overlap; `within` checks that a bounding box fits inside another.
Gravity, friction stability of leaning books, material deformation, load bearing and full mesh collision are not solved for now.
Width, depth and the four-tier count follow the current design; there is no claim that all dimensions can be changed arbitrarily.
