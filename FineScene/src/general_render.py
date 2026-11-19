"""Blender measurement and multi-view evidence for any asset or aDSL scene."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import sys

import bpy
import numpy as np
from mathutils import Matrix, Vector


def bounds(objects):
    bpy.context.view_layer.update()
    pts=[o.matrix_world@Vector(p) for o in objects for p in o.bound_box]
    if not pts: raise ValueError('No mesh geometry')
    return [Vector([f(p[i] for p in pts) for i in range(3)]) for f in (min,max)]


def asset_texture_evidence(objects):
    """Inspect imported UVs and base-color image links in Blender materials."""
    records=[]
    for obj in objects:
        base_color_images=[]
        for material in obj.data.materials:
            if not material or not material.use_nodes or not material.node_tree:
                continue
            nodes=material.node_tree.nodes
            links_by_input={}
            for link in material.node_tree.links:
                links_by_input.setdefault((link.to_node.as_pointer(),link.to_socket.identifier),[]).append(link)
            def upstream_images(node,socket,visited):
                key=(node.as_pointer(),socket.identifier)
                if key in visited:return []
                visited.add(key)
                found=[]
                for link in links_by_input.get(key,[]):
                    source=link.from_node
                    if source.type=='TEX_IMAGE' and source.image is not None:
                        found.append(source.image)
                    else:
                        for input_socket in source.inputs:
                            found.extend(upstream_images(source,input_socket,visited))
                return found
            for shader in (node for node in nodes if node.type=='BSDF_PRINCIPLED'):
                base_color=shader.inputs.get('Base Color')
                if base_color is None:continue
                for image in upstream_images(shader,base_color,set()):
                    base_color_images.append({'name':image.name,'size':[int(image.size[0]),int(image.size[1])],
                        'has_data':bool(image.has_data),'packed':bool(getattr(image,'packed_file',None) or
                                                                      getattr(image,'packed_files',None))})
        records.append({'object':obj.name,'uv_layers':len(obj.data.uv_layers),
            'base_color_images':base_color_images})
    return {'mesh_count':len(objects),'uv_mapped_meshes':sum(item['uv_layers']>0 for item in records),
        'meshes_with_base_color_image':sum(bool(item['base_color_images']) for item in records),
        'meshes':records}


def main():
    p=argparse.ArgumentParser()
    mode=p.add_mutually_exclusive_group(required=True)
    mode.add_argument('--asset',type=Path)
    mode.add_argument('--scene',type=Path)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--require-texture',action='store_true',help='Reject assets without UVs and linked base-color images')
    p.add_argument('--size',type=float,nargs=3)
    p.add_argument('--rotation',type=float,default=0)
    p.add_argument('--plan',type=Path)
    p.add_argument('--style-plan',type=Path,help='PlannerScenePlan providing style feature bindings')
    p.add_argument('--geometry-only',action='store_true',
                   help='Measure exported scene geometry and contacts without rendering images')
    p.add_argument('--view-phase',choices=('all','layout','details'),default='all',
                   help='Render whole-space evidence before generating expensive component close-ups')
    args=p.parse_args(sys.argv[sys.argv.index('--')+1:])
    if args.geometry_only and args.asset:
        p.error('--geometry-only requires --scene')
    out=args.output.resolve();out.mkdir(parents=True,exist_ok=True)
    if args.asset:
        if args.size is None: raise ValueError('Asset preparation requires a size envelope')
        bpy.ops.wm.read_factory_settings(use_empty=True)
        if args.asset.suffix.lower() not in ('.glb','.gltf'):raise ValueError('Asset must be glTF/GLB')
        bpy.ops.import_scene.gltf(filepath=str(args.asset.resolve()))
        meshes=[o for o in bpy.context.scene.objects if o.type=='MESH']
        if not meshes or not any(len(o.data.polygons) for o in meshes):
            raise ValueError('Asset has no polygonal geometry')
        texture_evidence=asset_texture_evidence(meshes)
        if args.require_texture and (texture_evidence['uv_mapped_meshes']!=len(meshes) or
                                     texture_evidence['meshes_with_base_color_image']!=len(meshes) or
                                     any(not image['has_data'] for item in texture_evidence['meshes']
                                         for image in item['base_color_images'])):
            raise ValueError(f'Generated asset failed UV/base-color texture gate: {texture_evidence}')
        if any(not math.isfinite(v) for o in meshes for vert in o.data.vertices for v in vert.co):
            raise ValueError('Asset contains nonfinite mesh coordinates')
        for o in meshes:
            world=o.matrix_world.copy();o.parent=None;o.matrix_world=world
        for o in list(bpy.context.scene.objects):
            if o.type!='MESH':bpy.data.objects.remove(o,do_unlink=True)
        rotation=Matrix.Rotation(math.radians(args.rotation),4,'Z')
        for o in meshes:o.matrix_world=rotation@o.matrix_world
        low,high=bounds(meshes);extent=high-low
        if any(not math.isfinite(v) or v<=0 for v in extent):raise ValueError('Invalid source bounds')
        factor=min(args.size[i]/extent[i] for i in range(3))
        translation=Vector((-(low.x+high.x)/2,-(low.y+high.y)/2,-low.z))
        transform=Matrix.Scale(factor,4)@Matrix.Translation(translation)
        for o in meshes:o.matrix_world=transform@o.matrix_world
        low,high=bounds(meshes)
        path=out/'normalized.glb'
        bpy.ops.export_scene.gltf(filepath=str(path),export_format='GLB',export_extras=True,export_apply=True)
        record={'path':str(path.resolve()),'bounds':[list(low),list(high)],'uniform_scale':factor,
                'source':str(args.asset.resolve()),'source_sha256':hashlib.sha256(args.asset.read_bytes()).hexdigest(),
                'faces':sum(len(o.data.polygons) for o in meshes),'accepted':False,
                'base_color_texture_verified':texture_evidence['uv_mapped_meshes']==len(meshes) and
                    texture_evidence['meshes_with_base_color_image']==len(meshes),
                'texture_evidence':texture_evidence}
        (out/'asset.json').write_text(json.dumps(record,indent=2))
    else:
        bpy.ops.wm.open_mainfile(filepath=str(args.scene.resolve()))
        part_bounds={}
        meshes=[o for o in bpy.context.scene.objects if o.type=='MESH']
        plan=json.loads(args.plan.read_text(encoding='utf-8'))
        known={o['id'] for o in plan['objects']}
        groups={k:[] for k in known}
        part_groups={k:{p['id']:[] for p in obj.get('parts',[])} for k,obj in
                     ((o['id'],o) for o in plan['objects'])}
        for o in meshes:
            cursor=o
            while cursor:
                parts=str(cursor.get('adsl_path','')).split('/')
                found=next((v for v in parts if v in known),None)
                if found:
                    groups[found].append(o)
                    tail=parts[parts.index(found)+1:]
                    part_id=next((v for v in tail if v in part_groups[found]),None)
                    if part_id:part_groups[found][part_id].append(o)
                    break
                cursor=cursor.parent
        actual={k:[list(v) for v in bounds(objs)] for k,objs in groups.items() if objs}
        (out/'actual_bounds.json').write_text(json.dumps(actual,indent=2))
        receipts_path=args.plan.with_name('build_receipts.json')
        receipts={}
        if receipts_path.is_file():
            receipts=json.loads(receipts_path.read_text(encoding='utf-8'));part_bounds={}
            graph=bpy.context.evaluated_depsgraph_get()
            for component,parts in part_groups.items():
                receipt=receipts[component]
                inverse=Matrix.Rotation(math.radians(-receipt['world_rotation_deg']),3,'Z')
                origin=Vector(receipt['world_origin']);part_bounds[component]={}
                for part_id,objects in parts.items():
                    points=[]
                    for obj in objects:
                        evaluated=obj.evaluated_get(graph)
                        points.extend(inverse@(evaluated.matrix_world@v.co-origin) for v in evaluated.data.vertices)
                    if points:part_bounds[component][part_id]=[[f(p[i] for p in points) for i in range(3)] for f in (min,max)]
            (out/'actual_part_bounds.json').write_text(json.dumps(part_bounds,indent=2))
        # For rigid imported assets, measure the actual bottom contact patch
        # from evaluated Blender vertices. A planner-declared footprint remains
        # useful during preflight, but must not stand in for the generated mesh
        # once geometry is available.
        support_contacts={}
        planned={item['id']:item for item in plan.get('objects',[])}
        for component,obj_plan in planned.items():
            mounting=obj_plan.get('surface_contact')
            if obj_plan.get('source',{}).get('mode')=='parametric' and not mounting:continue
            if obj_plan.get('mount') not in {'top','bottom'} and not obj_plan.get('grounded') and not mounting:continue
            receipt=receipts.get(component)
            objects=groups.get(component,[])
            if mounting and mounting.get('child_part'):
                objects=part_groups.get(component,{}).get(mounting['child_part'],[])
            if not receipt or not objects:continue
            yaw=math.radians(float(receipt['world_rotation_deg']))
            rotation=np.asarray(Matrix.Rotation(yaw,3,'Z'),dtype=float)
            inverse=np.asarray(Matrix.Rotation(-yaw,3,'Z'),dtype=float)
            origin=np.asarray(receipt['world_origin'],dtype=float)
            component_points=[]
            for obj in objects:
                evaluated=obj.evaluated_get(graph)
                flat=np.empty(len(evaluated.data.vertices)*3,dtype=np.float32)
                evaluated.data.vertices.foreach_get('co',flat)
                local_mesh=flat.reshape((-1,3)).astype(np.float64,copy=False)
                matrix=np.asarray(evaluated.matrix_world,dtype=np.float64)
                world_points=local_mesh@matrix[:3,:3].T+matrix[:3,3]
                component_points.append((world_points-origin)@inverse.T)
            if not component_points:continue
            points=np.concatenate(component_points,axis=0)
            local_low=points.min(axis=0)
            local_high=points.max(axis=0)
            height=float(receipt['parameters']['h'])
            tolerance=max(.001,min(.003,height*.005))
            contact_side='top' if obj_plan.get('mount')=='bottom' else 'bottom'
            contact_axis=2;maximum=contact_side=='top'
            if mounting:
                contact_side=mounting['child_face'];contact_axis='xyz'.index(contact_side[0]);maximum=contact_side.endswith('max')
            contact_plane=local_high[contact_axis] if maximum else local_low[contact_axis]
            if maximum:contact=points[points[:,contact_axis]>=contact_plane-tolerance]
            else:contact=points[points[:,contact_axis]<=contact_plane+tolerance]
            if not len(contact):continue
            local_bounds=np.stack((contact.min(axis=0),contact.max(axis=0)))
            world_contact=contact@rotation.T+origin
            world_bounds=np.stack((world_contact.min(axis=0),world_contact.max(axis=0)))
            measured=(local_bounds[1,:2]-local_bounds[0,:2]).tolist()
            support_contacts[component]={
                'method':'evaluated_blender_mesh_contact_vertices',
                'local_bounds':local_bounds.tolist(),'world_bounds':world_bounds.tolist(),
                'component_local_bounds':np.stack((local_low,local_high)).tolist(),
                'contact_side':contact_side,
                'contact_part':mounting.get('child_part') if mounting else None,
                'measured_footprint':measured,
                'declared_footprint':obj_plan.get('support_footprint'),
                'bottom_tolerance_m':tolerance,'contact_vertex_count':int(len(contact)),
                'total_vertex_count':int(len(points)),
            }
        (out/'actual_support_contacts.json').write_text(json.dumps(support_contacts,indent=2))
        if args.geometry_only:
            (out/'render_manifest.json').write_text(json.dumps({'images':[],'geometry_only':True},indent=2))
            return
        sys.path.insert(0,str(Path(__file__).resolve().parent))
        from general_materials import apply_finishes
        apply_finishes(plan,part_groups)
    low,high=bounds(meshes);center=(low+high)/2;extent=max(high-low)
    scene=bpy.context.scene
    for o in list(scene.objects):
        if o.type in ('CAMERA','LIGHT'):bpy.data.objects.remove(o,do_unlink=True)
    scene.render.engine='CYCLES';scene.cycles.samples=48;scene.cycles.use_denoising=True
    prefs=bpy.context.preferences.addons['cycles'].preferences;prefs.compute_device_type='OPTIX';prefs.get_devices()
    for d in prefs.devices:d.use=d.type!='CPU'
    if any(d.use for d in prefs.devices):scene.cycles.device='GPU'
    scene.render.resolution_x=scene.render.resolution_y=1400;scene.render.resolution_percentage=100
    scene.render.image_settings.file_format='PNG';scene.view_settings.view_transform='AgX'
    scene.world=bpy.data.worlds.new('GeneralWorld');scene.world.use_nodes=True
    scene.world.node_tree.nodes['Background'].inputs[0].default_value=(.15,.17,.20,1)
    scene.world.node_tree.nodes['Background'].inputs[1].default_value=.5
    if not args.asset and (len(plan['objects'])==1 or
                          (plan.get('spatial') and not any(o['collision_role']=='shell' for o in plan['objects']))):
        bpy.ops.mesh.primitive_plane_add(size=200,location=(0,0,low.z-.002))
        floor=bpy.context.object;floor.name='Presentation_ground_not_asset'
        mat=bpy.data.materials.new('Neutral studio ground');mat.diffuse_color=(.22,.21,.19,1)
        floor.data.materials.append(mat)
    for loc,power in [((1,-2,3),140),((-2,-1,2),80),((0,2,3),120)]:
        bpy.ops.object.light_add(type='AREA',location=center+Vector(loc)*extent)
        o=bpy.context.object;o.data.energy=power*extent**2;o.data.shape='DISK';o.data.size=extent*1.5
        o.rotation_euler=(center-o.location).to_track_quat('-Z','Y').to_euler()
    if not args.asset and any(o['collision_role']=='shell' for o in plan['objects']):
        # An enclosing ceiling blocks the outside inspection rig. Supply an
        # interior diffuse source and daylight at the actual planned windows.
        floor_area=(high.x-low.x)*(high.y-low.y)
        bpy.ops.object.light_add(type='AREA',location=(center.x,center.y,high.z-.12))
        lamp=bpy.context.object;lamp.name='Interior_diffuse_review_light'
        lamp.data.energy=floor_area*24;lamp.data.color=(1,.83,.65)
        lamp.data.shape='DISK';lamp.data.size=min(high.x-low.x,high.y-low.y)*.65
        for opening in (plan.get('spatial') or {}).get('openings',[]):
            if opening['kind']!='window':continue
            axis=0 if opening['wall'].startswith('x') else 1
            sign=-1 if opening['wall'].endswith('min') else 1
            position=Vector((center.x,center.y,min(high.z-.4,opening['height']+.4)))
            position[axis]=sign*(plan['extent'][axis]/2-.25);position[1-axis]=opening['offset']
            bpy.ops.object.light_add(type='AREA',location=position)
            lamp=bpy.context.object;lamp.name='Window_daylight_'+opening['id']
            lamp.data.energy=opening['width']*350;lamp.data.size=opening['width']
            lamp.data.color=(.81,.88,1)
            lamp.rotation_euler=(Vector((center.x,center.y,1))-position).to_track_quat('-Z','Y').to_euler()
    practical_lights=[]
    if not args.asset and args.style_plan and args.style_plan.is_file():
        appearance_plan=json.loads(args.style_plan.read_text(encoding='utf-8'))
        program=appearance_plan.get('design_program') or {}
        bindings=appearance_plan.get('design_bindings',{})
        lighting_ids={id for choice in program.get('choices',[]) if choice['layer']=='lighting'
                      for id in bindings.get(choice['id'],[])}
        for id in sorted(lighting_ids):
            if id not in groups or not groups[id]:continue
            a,b=bounds(groups[id]);position=(a+b)/2
            bpy.ops.object.light_add(type='POINT',location=position)
            lamp=bpy.context.object;lamp.name='Practical_light_'+id
            lamp.data.energy=75;lamp.data.color=(1,.74,.46);lamp.data.shadow_soft_size=.09
            practical_lights.append({'component_id':id,'position_m':list(position),'power_w':75,
                'placement_basis':'Approximate emitter at measured fixture center; source design lighting layer'})
    bpy.ops.object.camera_add();cam=bpy.context.object;cam.data.type='ORTHO';cam.data.clip_end=max(1000,extent*20);scene.camera=cam
    images=[];views=[];interior_view=None
    if args.view_phase=='details' and (out/'render_manifest.json').is_file():
        previous=json.loads((out/'render_manifest.json').read_text(encoding='utf-8'))
        images=previous.get('images',[]);views=previous.get('views',[])
    shell_surfaces=[]
    if not args.asset:
        for obj in plan['objects']:
            if obj['collision_role']!='shell':continue
            for mesh in groups[obj['id']]:
                a,b=bounds([mesh]);size=b-a;mid=(a+b)/2
                # The floor stays present. Only roof/vertical boundary surfaces
                # are eligible for documented camera cutaways; never furniture.
                if size.z<min(size.x,size.y)*.4:
                    if mid.z>center.z:shell_surfaces.append((mesh,'roof',mid))
                else:
                    axis=min(range(2),key=lambda i:size[i])
                    if size[axis]<max(high[axis]-low[axis],.1)*.2:
                        shell_surfaces.append((mesh,axis,mid))
    orbit=Matrix.Rotation(math.radians(plan.get('review_azimuth_deg',0) if not args.asset else 0),3,'Z')
    def render(name,target,direction,span,*,cutaway=False,wall_surface=None):
        cam.data.ortho_scale=span;cam.location=target+orbit@Vector(direction)*extent
        cam.rotation_euler=(target-cam.location).to_track_quat('-Z','Y').to_euler()
        hidden=[]
        if cutaway:
            for mesh,axis,mid in shell_surfaces:
                if axis=='roof' or ((mid[axis]-center[axis])*(cam.location[axis]-center[axis])>0):
                    hidden.append((mesh,mesh.hide_render));mesh.hide_render=True
        path=out/f'{name}.png';scene.render.filepath=str(path)
        try:bpy.ops.render.render(write_still=True)
        finally:
            for mesh,previous in hidden:mesh.hide_render=previous
        images.append(str(path.resolve()))
        views.append({'image':str(path.resolve()),'presentation':'cutaway' if cutaway else 'complete_geometry',
                      'camera_target_m':[float(v) for v in target],
                      'camera_direction_from_target_world':[float(v) for v in (orbit@Vector(direction)).normalized()],
                      'wall_surface':wall_surface,
                      'temporarily_hidden_shell_meshes':[mesh.name for mesh,_ in hidden]})
    span=(high-low).length*1.15
    for name,direction in ([('overview',(.9,-2.5,1.5)),('reverse',(-1,2,1.5)),('top',(0,0,4))]
                           if args.view_phase!='details' else []):
        render(name,center,direction,span,cutaway=bool(shell_surfaces))
    if not args.asset and args.view_phase!='details':
        render('front',center,(0,-3,.02),span)
        if shell_surfaces:
            render('exterior',center,(1,-2,1.4),span)
            for opening in (plan.get('spatial') or {}).get('openings',[]):
                if opening['kind']!='entrance':continue
                axis=0 if opening['wall'].startswith('x') else 1
                sign=-1 if opening['wall'].endswith('min') else 1
                target=Vector((0,0,opening['height']/2))
                target[axis]=sign*plan['extent'][axis]/2
                target[1-axis]=opening['offset']
                direction=Vector((0,0,.05));direction[axis]=sign*2
                render('entrance_'+opening['id'],target,direction,max(opening['width']*2,opening['height']*1.3))
                # Full-geometry human-height view, in addition to diagnostic cutaways.
                cam.data.type='PERSP';cam.data.lens=21
                eye=target.copy();eye.z=min(1.55,high.z-.3);eye[axis]-=sign*.45
                aim=Vector((center.x,center.y,min(1.25,high.z/2)))
                cam.location=eye;cam.rotation_euler=(aim-eye).to_track_quat('-Z','Y').to_euler()
                interior_view=(eye.copy(),aim.copy())
                scene.render.resolution_x=1680;scene.render.resolution_y=1050
                path=out/('interior_'+opening['id']+'.png');scene.render.filepath=str(path)
                bpy.ops.render.render(write_still=True)
                images.append(str(path.resolve()));views.append({'image':str(path.resolve()),
                    'presentation':'complete_geometry','temporarily_hidden_shell_meshes':[]})
                cam.data.type='ORTHO'
                scene.render.resolution_x=scene.render.resolution_y=1400
        for opening in (plan.get('spatial') or {}).get('openings',[]):
            if opening['kind']!='window':continue
            axis=0 if opening['wall'].startswith('x') else 1
            sign=-1 if opening['wall'].endswith('min') else 1
            target=Vector((0,0,opening['sill_height']+opening['height']/2))
            target[axis]=sign*plan['extent'][axis]/2
            target[1-axis]=opening['offset']
            direction=Vector((0,0,.05));direction[axis]=sign*2
            render('exterior_'+opening['id'],target,direction,
                   max(opening['width']*2.2,opening['height']*2,1.8),
                   wall_surface=opening['wall'])
    if not args.asset and args.view_phase!='details':
        for view in (plan.get('spatial') or {}).get('experience_views',[]):
            eye=Vector(view['position']);aim=Vector(view['target'])
            cam.data.type='PERSP';cam.data.lens=view['lens_mm'];cam.location=eye
            cam.rotation_euler=(aim-eye).to_track_quat('-Z','Y').to_euler()
            scene.render.resolution_x=1680;scene.render.resolution_y=1050
            name='experience_'+view['intent_id'];path=out/(name+'.png');scene.render.filepath=str(path)
            bpy.ops.render.render(write_still=True)
            images.append(str(path.resolve()));views.append({'image':str(path.resolve()),
                'presentation':'complete_geometry','design_view_intent':view['intent_id'],
                'eye_m':list(eye),'target_m':list(aim),'temporarily_hidden_shell_meshes':[]})
            interior_view=(eye.copy(),aim.copy())
            cam.data.type='ORTHO';scene.render.resolution_x=scene.render.resolution_y=1400
    if not args.asset and args.view_phase!='layout':
        for obj in plan['objects']:
            if obj['collision_role']=='shell' or obj['id'] not in actual:continue
            a,b=[Vector(v) for v in actual[obj['id']]]
            yaw=Matrix.Rotation(math.radians(obj.get('rotation_deg',0)),3,'Z')
            render('detail_'+obj['id'],(a+b)/2,yaw@Vector((-.6,-2,.5)),max((b-a).length*1.08,.25),cutaway=bool(shell_surfaces))
            # Opposing profile evidence distinguishes functional panel orientation
            # from plausible silhouettes in a single frontal view.
            render('side_'+obj['id'],(a+b)/2,yaw@Vector((2,.2,.35)),max((b-a).length*1.08,.25),cutaway=bool(shell_surfaces))
        style_path=getattr(args,'style_plan',None)
        receipts_path=args.plan.with_name('build_receipts.json') if args.plan else None
        if style_path and style_path.is_file() and receipts_path and receipts_path.is_file() and part_bounds:
            style_plan=json.loads(style_path.read_text(encoding='utf-8'))
            receipts=json.loads(receipts_path.read_text(encoding='utf-8'))
            for binding in style_plan.get('style_bindings',[]):
                points=[]
                for qualified in binding.get('part_ids',[]):
                    owner,separator,part_id=qualified.partition('/')
                    if not separator or owner not in part_bounds or part_id not in part_bounds[owner]:continue
                    low,high=[Vector(v) for v in part_bounds[owner][part_id]]
                    corners=[Vector((x,y,z)) for x in (low.x,high.x) for y in (low.y,high.y) for z in (low.z,high.z)]
                    receipt=receipts[owner]
                    yaw=Matrix.Rotation(math.radians(receipt['world_rotation_deg']),3,'Z')
                    origin=Vector(receipt['world_origin'])
                    points.extend(yaw@point+origin for point in corners)
                if not points:continue
                target=Vector(tuple(sum(point[i] for point in points)/len(points) for i in range(3)))
                focus_low=Vector(tuple(min(point[i] for point in points) for i in range(3)))
                focus_high=Vector(tuple(max(point[i] for point in points) for i in range(3)))
                render('detail_style_'+binding['requirement_id'],target,(0,-3,.42),
                       max((focus_high-focus_low).length*2.2,.18))
    (out/'render_manifest.json').write_text(json.dumps({'images':images,'views':views,
        'renderer_source_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'practical_lights':practical_lights,
        'cutaway_note':'Only named architectural surfaces were temporarily hidden for specified renders. All original shell meshes are restored in review.blend; full-geometry front/exterior/entrance views establish enclosure.'},indent=2))
    cam.data.ortho_scale=span;cam.location=center+orbit@Vector((1,-2,1.4))*extent
    cam.rotation_euler=(center-cam.location).to_track_quat('-Z','Y').to_euler()
    if interior_view is not None:
        eye,aim=interior_view;cam.data.type='PERSP';cam.data.lens=21
        cam.location=eye;cam.rotation_euler=(aim-eye).to_track_quat('-Z','Y').to_euler()
        scene.render.resolution_x=1680;scene.render.resolution_y=1050
        for screen in bpy.data.screens:
            for area in screen.areas:
                if area.type=='VIEW_3D':area.spaces.active.region_3d.view_perspective='CAMERA'
    bpy.ops.file.pack_all()
    bpy.ops.wm.save_as_mainfile(filepath=str(out/'review.blend'))


if __name__=='__main__':main()
