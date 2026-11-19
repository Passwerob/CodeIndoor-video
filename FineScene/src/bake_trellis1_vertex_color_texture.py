"""Bake TRELLIS.1 mesh vertex RGB attributes into a UV base-color map in Blender."""
import argparse
import json
import math
from pathlib import Path
import sys

import bpy


def material_profile(hints):
    """Map planner material language to a baked, auditable appearance profile."""
    text=' '.join(str(h) for h in (hints or [])).casefold()
    has_gilding=any(keyword in text for keyword in ('gilt','gilded','gold','brass'))
    profiles=[
        (('walnut','oak','mahogany','wood','wooden','giltwood'),
         {'name':'dark_wood','dark':(0.035,0.008,0.002,1),'light':(0.34,0.075,0.018,1),
          'roughness':0.62,'metallic':0.08,'noise_scale':7.0,'gilding':has_gilding,
          'accent':(0.72,0.39,0.075,1)}),
        (('gold','gilded','gilt','brass','bronze'),
         {'name':'aged_gold','dark':(0.10,0.025,0.004,1),'light':(0.78,0.38,0.045,1),
          'roughness':0.38,'metallic':0.38,'noise_scale':9.0}),
        (('stone','marble','limestone','plaster','stucco','ceramic','porcelain'),
         {'name':'aged_stone','dark':(0.16,0.13,0.095,1),'light':(0.62,0.49,0.32,1),
          'roughness':0.78,'metallic':0.0,'noise_scale':11.0}),
        (('leather',),
         {'name':'leather','dark':(0.045,0.008,0.002,1),'light':(0.28,0.045,0.012,1),
          'roughness':0.72,'metallic':0.0,'noise_scale':16.0}),
        (('metal','iron','steel'),
         {'name':'aged_metal','dark':(0.04,0.045,0.05,1),'light':(0.38,0.42,0.46,1),
          'roughness':0.42,'metallic':0.62,'noise_scale':13.0}),
    ]
    for keywords,profile in profiles:
        if any(keyword in text for keyword in keywords):
            return profile
    return {'name':'reference_preserving','dark':(0.10,0.085,0.065,1),'light':(0.62,0.54,0.42,1),
            'roughness':0.76,'metallic':0.0,'noise_scale':8.0}


def set_ramp(ramp, low, high):
    ramp.elements[0].position=0.0
    ramp.elements[0].color=(*low[:3],1.0)
    ramp.elements[1].position=1.0
    ramp.elements[1].color=(*high[:3],1.0)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--texture',type=Path,required=True)
    parser.add_argument('--size',type=int,default=1024)
    parser.add_argument('--material-hint',action='append',default=[],
                        help='Planner material requirement; baked before GLB export')
    args=parser.parse_args(sys.argv[sys.argv.index('--')+1:])
    if args.size<256 or args.size>4096:
        raise ValueError('Texture size must be between 256 and 4096 pixels')
    for path in (args.input,args.output,args.texture):
        path.parent.mkdir(parents=True,exist_ok=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=str(args.input.resolve()))
    objects=[obj for obj in bpy.context.scene.objects if obj.type=='MESH' and len(obj.data.polygons)]
    if len(objects)!=1:
        raise RuntimeError(f'Expected one TRELLIS.1 component mesh, found {len(objects)}')
    obj=objects[0]
    color_attrs=[attr for attr in obj.data.color_attributes
                 if attr.domain in {'POINT','CORNER'} and attr.data_type in {'FLOAT_COLOR','BYTE_COLOR'}]
    if not color_attrs:
        raise RuntimeError('Imported TRELLIS.1 GLB has no vertex color attribute; refusing to create a blank texture')
    color_attr=color_attrs[0]
    # Blender's glTF importer can expose a malformed unnamed COLOR_0 label
    # from Trimesh exports. Normalize it before using the attribute in nodes.
    color_attr.name='TRELLIS1_Predicted_RGB'
    if not obj.data.uv_layers:
        bpy.ops.object.select_all(action='DESELECT')
        obj.select_set(True)
        bpy.context.view_layer.objects.active=obj
        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.mesh.select_all(action='SELECT')
        bpy.ops.uv.smart_project(island_margin=0.008)
        bpy.ops.object.mode_set(mode='OBJECT')
    if not obj.data.uv_layers:
        raise RuntimeError('Blender did not create UV coordinates for the TRELLIS.1 mesh')

    scene=bpy.context.scene
    scene.render.engine='CYCLES'
    scene.cycles.device='CPU'
    scene.cycles.samples=1
    scene.render.bake.margin=8
    image=bpy.data.images.new('TRELLIS1_BaseColor',width=args.size,height=args.size,alpha=True)
    image.generated_color=(0.0,0.0,0.0,1.0)
    image.colorspace_settings.name='sRGB'

    profile=material_profile(args.material_hint)
    material=bpy.data.materials.new('TRELLIS1_MaterialProfile_BakeSource')
    material.use_nodes=True
    material_nodes=material.node_tree.nodes
    material_nodes.clear()
    texcoord=material_nodes.new('ShaderNodeTexCoord')
    noise=material_nodes.new('ShaderNodeTexNoise')
    noise.inputs['Scale'].default_value=profile['noise_scale']
    noise.inputs['Detail'].default_value=3.0
    noise.inputs['Roughness'].default_value=.72
    noise_ramp=material_nodes.new('ShaderNodeValToRGB')
    noise_ramp.color_ramp.elements[0].position=.28
    noise_ramp.color_ramp.elements[0].color=(.72,.72,.72,1)
    noise_ramp.color_ramp.elements[1].position=.74
    noise_ramp.color_ramp.elements[1].color=(1.12,1.12,1.12,1)
    color_node=material_nodes.new('ShaderNodeVertexColor')
    color_node.layer_name='TRELLIS1_Predicted_RGB'
    luminance=material_nodes.new('ShaderNodeRGBToBW')
    remap=material_nodes.new('ShaderNodeMapRange')
    remap.clamp=True
    # Stretch the model's own luminance range before mapping it to the requested
    # material. This preserves relief shading while avoiding a nearly-white tint.
    import numpy as np
    color_values=np.empty((len(color_attr.data),4),dtype=np.float32)
    color_attr.data.foreach_get('color',color_values.reshape(-1))
    luminance_values=color_values[:,:3]@np.array([.2126,.7152,.0722],dtype=np.float32)
    low=float(np.percentile(luminance_values,2))
    high=float(np.percentile(luminance_values,98))
    if not math.isfinite(low) or not math.isfinite(high) or high-low<1e-4:
        raise RuntimeError('TRELLIS.1 vertex colors have insufficient luminance variation for material baking')
    remap.inputs['From Min'].default_value=low
    remap.inputs['From Max'].default_value=high
    remap.inputs['To Min'].default_value=0.0
    remap.inputs['To Max'].default_value=1.0
    palette=material_nodes.new('ShaderNodeValToRGB')
    set_ramp(palette.color_ramp,profile['dark'],profile['light'])
    if profile.get('gilding'):
        palette.color_ramp.elements[1].position=.62
        accent=palette.color_ramp.elements.new(.80)
        accent.color=profile['accent']
        upper=palette.color_ramp.elements.new(.96)
        upper.color=profile['accent']
    mix=material_nodes.new('ShaderNodeMixRGB')
    mix.blend_type='MULTIPLY'
    mix.inputs[0].default_value=.32
    emission=material_nodes.new('ShaderNodeEmission')
    output=material_nodes.new('ShaderNodeOutputMaterial')
    material.node_tree.links.new(texcoord.outputs['Generated'],noise.inputs['Vector'])
    material.node_tree.links.new(noise.outputs['Fac'],noise_ramp.inputs['Fac'])
    material.node_tree.links.new(color_node.outputs['Color'],luminance.inputs['Color'])
    material.node_tree.links.new(luminance.outputs['Val'],remap.inputs['Value'])
    material.node_tree.links.new(remap.outputs['Result'],palette.inputs['Fac'])
    material.node_tree.links.new(palette.outputs['Color'],mix.inputs[1])
    material.node_tree.links.new(noise_ramp.outputs['Color'],mix.inputs[2])
    material.node_tree.links.new(mix.outputs['Color'],emission.inputs['Color'])
    material.node_tree.links.new(emission.outputs['Emission'],output.inputs['Surface'])
    image_node=material_nodes.new('ShaderNodeTexImage')
    image_node.image=image
    for node in material_nodes:node.select=False
    image_node.select=True
    material_nodes.active=image_node
    obj.data.materials.clear()
    obj.data.materials.append(material)

    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    bpy.context.view_layer.objects.active=obj
    bpy.ops.object.bake(type='EMIT',use_clear=True,margin=8)
    image.filepath_raw=str(args.texture.resolve())
    image.file_format='PNG'
    image.save()
    if not args.texture.is_file() or args.texture.stat().st_size<128:
        raise RuntimeError('Blender did not write a valid base-color texture image')
    if not all(math.isfinite(value) for value in image.pixels[:]):
        raise RuntimeError('Baked base-color texture contains non-finite pixel values')
    image.pack()

    final_material=bpy.data.materials.new('TRELLIS1_Baked_BaseColor')
    final_material.use_nodes=True
    nodes=final_material.node_tree.nodes
    nodes.clear()
    texture_node=nodes.new('ShaderNodeTexImage')
    texture_node.image=image
    principled=nodes.new('ShaderNodeBsdfPrincipled')
    principled.inputs['Roughness'].default_value=profile['roughness']
    if principled.inputs.get('Metallic') is not None:
        principled.inputs['Metallic'].default_value=profile['metallic']
    output=nodes.new('ShaderNodeOutputMaterial')
    final_material.node_tree.links.new(texture_node.outputs['Color'],principled.inputs['Base Color'])
    final_material.node_tree.links.new(principled.outputs['BSDF'],output.inputs['Surface'])
    obj.data.materials.clear()
    obj.data.materials.append(final_material)
    bpy.ops.export_scene.gltf(filepath=str(args.output.resolve()),export_format='GLB',
        export_image_format='AUTO',export_materials='EXPORT',export_cameras=False,export_lights=False)
    report={'status':'baked','source':str(args.input.resolve()),'output':str(args.output.resolve()),
        'texture':str(args.texture.resolve()),'texture_size':[args.size,args.size],
        'color_attribute':'TRELLIS1_Predicted_RGB','color_attribute_domain':color_attr.domain,
        'uv_layer':obj.data.uv_layers.active.name,'bake':'EMIT from TRELLIS.1 predicted mesh vertex RGB',
        'material_maps':['base_color'],'roughness_factor':profile['roughness'],'metallic_factor':profile['metallic'],
        'material_profile':profile['name'],'material_hints':args.material_hint,
        'source_luminance_percentiles':[low,high],
        'note':'Base color is baked from TRELLIS.1 predicted vertex RGB through a planner-selected color profile with subtle procedural variation; it is not TRELLIS.1 Gaussian multi-view optimization or learned material segmentation.'}
    report_path=args.output.with_suffix('.texture_bake.json')
    report_path.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2),flush=True)


if __name__=='__main__':main()
