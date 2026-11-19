"""Blender surface finishes selected by explicit part metadata, not categories."""
import bpy
from pathlib import Path
from material_catalog import catalog


def apply_pbr(obj,bsdf,nodes,links,surface):
    resource=catalog()[surface['texture_id']]
    # Explicit per-face UVs: grain follows the declared physical asset axis.
    axis='xyz'.index(surface.get('grain_axis','z'));scale=surface.get('texture_scale',1)
    uv=obj.data.uv_layers.active or obj.data.uv_layers.new(name='SurfaceUV')
    for polygon in obj.data.polygons:
        normal_axis=max(range(3),key=lambda i:abs(polygon.normal[i]))
        along=axis if axis!=normal_axis else next(i for i in range(3) if i!=normal_axis)
        across=next(i for i in range(3) if i not in (normal_axis,along))
        for li in polygon.loop_indices:
            co=obj.data.vertices[obj.data.loops[li].vertex_index].co
            pair=(co[along],co[across]) if resource.get('grain_uv_axis')=='u' else (co[across],co[along])
            tile=resource.get('tile_size_m',[.8,.8])
            uv.data[li].uv=(pair[0]*scale/tile[0],pair[1]*scale/tile[1])
    for kind,path in resource['maps'].items():
        image=nodes.new('ShaderNodeTexImage');image.image=bpy.data.images.load(path,check_existing=True)
        if kind!='color':image.image.colorspace_settings.name='Non-Color'
        if kind=='color':links.new(image.outputs['Color'],bsdf.inputs['Base Color'])
        elif kind in {'roughness','arm'}:
            mix=nodes.new('ShaderNodeMath');mix.operation='MULTIPLY';mix.inputs[1].default_value=surface.get('roughness',.5)*2
            if kind=='arm':
                separate=nodes.new('ShaderNodeSeparateColor');links.new(image.outputs['Color'],separate.inputs['Color'])
                links.new(separate.outputs['Green'],mix.inputs[0]);links.new(separate.outputs['Blue'],bsdf.inputs['Metallic'])
            else:links.new(image.outputs['Color'],mix.inputs[0])
            links.new(mix.outputs[0],bsdf.inputs['Roughness'])
        elif kind=='normal':
            normal=nodes.new('ShaderNodeNormalMap');normal.inputs['Strength'].default_value=.35
            links.new(image.outputs['Color'],normal.inputs['Color']);links.new(normal.outputs[0],bsdf.inputs['Normal'])


def apply_finishes(plan,part_groups):
    for component in plan['objects']:
        for part in component.get('parts',[]):
            objects=part_groups[component['id']].get(part['id'],[])
            for obj in objects:
                if not obj.data.materials:continue
                finish=part.get('finish','matte')
                surface=part.get('surface') or {}
                if finish=='matte' and part['primitive']!='book' and not surface:continue
                material=obj.data.materials[0].copy()
                obj.data.materials[0]=material
                material.use_nodes=True
                nodes=material.node_tree.nodes;links=material.node_tree.links
                bsdf=next(n for n in nodes if n.type=='BSDF_PRINCIPLED')
                if surface.get('texture_id'):
                    apply_pbr(obj,bsdf,nodes,links,surface)
                    continue
                color=list(bsdf.inputs['Base Color'].default_value)
                tex=nodes.new('ShaderNodeTexCoord')
                scale=nodes.new('ShaderNodeVectorMath');scale.operation='MULTIPLY'
                links.new(tex.outputs['Generated'],scale.inputs[0])
                noise=nodes.new('ShaderNodeTexNoise');noise.inputs['Scale'].default_value=5
                noise.inputs['Detail'].default_value=3;noise.inputs['Roughness'].default_value=.7
                links.new(scale.outputs[0],noise.inputs['Vector'])
                ramp=nodes.new('ShaderNodeValToRGB')
                if finish=='wood':
                    # Generated coordinates follow the dominant member dimension.
                    axis=max(range(3),key=lambda i:obj.dimensions[i])
                    stretch=[9.,9.,9.];stretch[axis]=.45
                    scale.inputs[1].default_value=stretch
                    dark,light=.38,1.12
                    bsdf.inputs['Roughness'].default_value=.35
                    bsdf.inputs['Coat Weight'].default_value=.18
                    bsdf.inputs['Coat Roughness'].default_value=.3
                    strength,distance=.13,.0007
                elif finish=='metal':
                    scale.inputs[1].default_value=(4,4,4)
                    dark,light=.8,1.05;strength,distance=.06,.0001
                    bsdf.inputs['Metallic'].default_value=.8
                    bsdf.inputs['Roughness'].default_value=.32
                else:
                    scale.inputs[1].default_value=(35,35,35)
                    dark,light=.8,1.02;strength,distance=.13,.00015
                    bsdf.inputs['Roughness'].default_value=.72
                ramp.color_ramp.elements[0].position=.15
                ramp.color_ramp.elements[0].color=tuple(c*dark for c in color[:3])+(1,)
                ramp.color_ramp.elements[1].position=.85
                ramp.color_ramp.elements[1].color=tuple(min(c*light,1) for c in color[:3])+(1,)
                links.new(noise.outputs['Fac'],ramp.inputs[0]);links.new(ramp.outputs[0],bsdf.inputs['Base Color'])
                bump=nodes.new('ShaderNodeBump');bump.inputs['Strength'].default_value=strength
                bump.inputs['Distance'].default_value=distance
                links.new(noise.outputs['Fac'],bump.inputs['Height']);links.new(bump.outputs[0],bsdf.inputs['Normal'])
                if surface:bsdf.inputs['Roughness'].default_value=surface.get('roughness',.5)
