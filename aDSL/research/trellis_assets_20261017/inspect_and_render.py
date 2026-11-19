"""Render downloaded official TRELLIS examples; original GLBs stay unchanged."""
import bpy
import json
import math
from pathlib import Path
from mathutils import Vector

ROOT = Path(__file__).resolve().parent
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
scene = bpy.context.scene
scene.render.engine = 'CYCLES'
scene.cycles.samples = 32
scene.cycles.use_denoising = True
scene.render.resolution_x = 700
scene.render.resolution_y = 700
scene.render.resolution_percentage = 100
scene.render.image_settings.file_format = 'PNG'
scene.world.color = (.3, .3, .3)
scene.view_settings.view_transform = 'AgX'
scene.render.film_transparent = False

bpy.ops.object.camera_add(location=(2.3, -3.5, 2.0))
camera = bpy.context.object
camera.data.type = 'ORTHO'
camera.data.ortho_scale = 2.05
camera.rotation_euler = (Vector((0, 0, .65)) - camera.location).to_track_quat('-Z', 'Y').to_euler()
scene.camera = camera
for position, energy, size in [((2, -3, 4), 450, 4), ((-3, -1, 2), 250, 3), ((1, 3, 3), 350, 3)]:
    bpy.ops.object.light_add(type='AREA', location=position)
    light = bpy.context.object
    light.data.energy = energy
    light.data.shape = 'DISK'
    light.data.size = size
    light.rotation_euler = (Vector((0, 0, .5)) - light.location).to_track_quat('-Z', 'Y').to_euler()

bpy.ops.mesh.primitive_plane_add(size=200)
ground = bpy.context.object
ground.name = 'Preview_ground'
mat = bpy.data.materials.new('Preview_ground_material')
mat.diffuse_color = (.24, .26, .29, 1)
ground.data.materials.append(mat)

names = ['bookshelf', 'rocking_chair', 'kitchen_rack']
assets = []
audit = []
for name in names:
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=str(ROOT / 'models' / (name + '.glb')))
    imported = list(set(bpy.data.objects) - before)
    meshes = [o for o in imported if o.type == 'MESH']
    points = [o.matrix_world @ Vector(corner) for o in meshes for corner in o.bound_box]
    low = Vector(tuple(min(v[i] for v in points) for i in range(3)))
    high = Vector(tuple(max(v[i] for v in points) for i in range(3)))
    audit.append(dict(name=name, mesh_objects=len(meshes),
                      vertices=sum(len(o.data.vertices) for o in meshes),
                      polygons=sum(len(o.data.polygons) for o in meshes),
                      imported_bounds=[list(low), list(high)],
                      modifiers=[m.type for o in meshes for m in o.modifiers],
                      object_names=[o.name for o in imported]))
    # Preview normalization is applied only to a new parent, not the original files.
    root = bpy.data.objects.new('PREVIEW_' + name, None)
    scene.collection.objects.link(root)
    for obj in imported:
        if obj.parent not in imported:
            obj.parent = root
    scale = 1.4 / max(high - low)
    root.scale = (scale,) * 3
    root.location = (-scale * (low.x + high.x) / 2,
                     -scale * (low.y + high.y) / 2, -scale * low.z)
    bpy.context.view_layer.update()
    for _, previous_objects in assets:
        for obj in previous_objects:
            obj.hide_render = True
    scene.render.filepath = str(ROOT / (name + '_preview.png'))
    bpy.ops.render.render(write_still=True)
    assets.append((root, imported))

for i, (root, objects) in enumerate(assets):
    root.location.x += (i - 1) * 2.0
    for obj in objects:
        obj.hide_render = False
camera.location = (3.6, -8, 3.6)
camera.rotation_euler = (Vector((0, 0, .6)) - camera.location).to_track_quat('-Z', 'Y').to_euler()
camera.data.ortho_scale = 7
bpy.ops.wm.save_as_mainfile(filepath=str(ROOT / 'official_samples_preview.blend'))
(ROOT / 'blender_audit.json').write_text(json.dumps(audit, indent=2), encoding='utf-8')
print('TRELLIS_SAMPLE_PREVIEWS_COMPLETE')
