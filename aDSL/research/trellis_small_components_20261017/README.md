# Code Structure + TRELLIS Fine Fixed Components

Date: 2026-10-17. Goal as clarified by the user: use the parametric and relation-preserving abilities of code 3D to implement interaction; use generators such as TRELLIS to provide local assets with fixed shape and refined visuals. There is no need to segment a whole generated piece of furniture before hybrid assembly can begin.

## Small Official Samples Found This Time

| Component | Official GLB | Local copy |
|---|---|---|
| Carved wooden chess piece | https://microsoft.github.io/TRELLIS/assets/txt2/glbs/chess.glb | `models/chess_queen.glb` |
| Ceramic mug | https://microsoft.github.io/TRELLIS/assets/txt2/glbs/mug.glb | `models/ceramic_mug.glb` |
| Bronze owl ornament | https://microsoft.github.io/TRELLIS/assets/txt2/glbs/owl.glb | `models/bronze_owl.glb` |

Source: the official TRELLIS project page and its generation-showcase script. The three files were downloaded, their GLB headers verified, and imported and rendered in Blender; each is a single mesh. Download records and SHA-256 are in `assets_manifest.json`; the Blender check is in `blender_audit.json`. These are complete small objects and can serve as rigid content items / decorative components; the official manifest checked this round contained no standalone handles, hinged lids, or carved plates, so it cannot be claimed that these mounting parts have been obtained.

![Preview](small_components_contact_sheet.jpg)

The preview is uniformly scaled and for display only; real dimensions cannot be inferred from the image. The original GLBs are unmodified. Code/weights licenses and showcase-asset permissions still need to be distinguished, following the scope of the source records from the previous round.

## Division of Responsibilities in Hybrid Modeling

| code 3D | Generated mesh |
|---|---|
| Precise dimensions, panels, motion joints, collision proxies, interaction logic, interfaces, dependency propagation | Carvings, handle appearance, decorative capitals, fixed-shape attachments, ornaments, and surface detail |

"Fixed" means local geometry and material stay unchanged during interaction; it does not mean the world pose is fixed. A handle can slide with a drawer; a carving can rotate about the hinge with a door; an ornament can move up with a shelf board. User dragging or replacement can also be code-controlled interaction.

Assembly units should respect functional boundaries: if a lantern's door is to open, the door and the shell must each be generated separately; a single whole mesh is only suitable for whole-body motion. The basis for division is the intended interaction, not the size of the object.

## Wrapping Required for External Mesh Components

The following is a proposed contract, not a currently supported aDSL API:

```text
Component instance
  Resource: local GLB + source + version/hash
  Initial normalization: axes, origin, explicit target dimensions or unit scale
  Local interface: mounting points/surfaces, normals, usable mounting range
  Instance pose: driven by code relations
  Interaction policy: keep local geometry; may translate/rotate as a whole; replaceable
  Proxy: simplified bounding volume or collider, using the same instance transform as the visual mesh
  Relations: attached to some mounting interface, or supported on some surface
```

On first import, a uniform scale can be set explicitly to match the design dimensions; later changes to the parent's dimensions should not implicitly stretch this decorative component. When a size change is needed, use an explicit spec change, replacement, or regeneration strategy.

For a continuous pattern after a cabinet is widened, code can lengthen the back panel, move the end carvings, and repeat the middle section at a fixed pitch; whether to change the count or spacing should be an explicitly chosen strategy. The fine mesh does not bear any whole segment of structure that must change.

The current `RelationAssembly`'s `supported`/`attached` accept aDSL `Asset`, which does not mean it can already read an arbitrary external GLB directly. The existing core primitives and the Blender exporter were checked and have no such mesh-resource branch yet. The minimal implementation path is to add a resource-instance record and a loading branch, together with a local interface, pose, and extent proxy, ensuring that boundary queries, rebuild, preview, export, and reload are consistent; merely attaching the mesh late in a rendering script does not count as system integration being complete.

## Suggested First Interaction Case

Use the existing code bookshelf to hold the chess piece, mug, or owl here: change the height of a tier, and the shelf boards and contents move according to the defined relations; the contents' local geometry is unchanged. Then use a purpose-generated handle or capital to verify mounting interfaces and drawer/door motion.

This round delivers only the small-component samples, previews, and the design notes; external mesh integration and the hybrid interaction case have not been implemented.
