# Installing a TRELLIS component onto a book

This example does not place the chess piece on a shelf board. Instead it installs it as an independent rigid component
onto `book_19` at the top of the second-tier book stack, offset 6 cm toward the front edge of the book. This book was chosen to leave headroom above the piece:

```text
Tier 2 shelf board -> book_19 -> chess_queen
```

Therefore, when the height of tier 1 is changed, the book and the chess piece move together; the mesh size, material and local
shape of the piece stay unchanged. When other tiers are changed, this piece should not move.

```powershell
& E:\3DUse\aDSL\venv\Scripts\python.exe E:\3DUse\aDSL\scripts\local_3d.py build `
  --source E:\3DUse\aDSL\examples\physical_bookshelf_trellis\model.py `
  --output E:\3DUse\aDSL\outputs\physical_bookshelf_trellis_default --views 2
```

The default instance scales the official TRELLIS chess piece once to a height of about 0.15 m. This is an
explicit installation spec of the component instance, not an implicit scaling when the tier height is changed.
