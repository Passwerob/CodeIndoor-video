"""Asset-driven refinement of a FineScene contract, preserving semantic identities.

Run in Blender. Source scenes/downloads are never overwritten.
"""
import argparse, json, math, hashlib, random, sys
from pathlib import Path
import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent))
import finescene_env as fenv
import bpy
from mathutils import Vector, Matrix

ASSETS=fenv.STUDY_ASSETS
POLY=fenv.POLYHAVEN_ASSETS


def bounds(objects):
    points=[o.matrix_world@Vector(c) for o in objects if o.type=='MESH' for c in o.bound_box]
    return Vector(tuple(min(p[a] for p in points) for a in range(3))),Vector(tuple(max(p[a] for p in points) for a in range(3)))


def identity(obj,known):
    node=obj
    while node:
        if node.get('finescene_component_id'):return node['finescene_component_id']
        for part in str(node.get('adsl_path',node.name)).split('/'):
            if part in known:return part
        node=node.parent


def load_asset(path):
    before=set(bpy.data.objects)
    if path.suffix=='.blend':
        with bpy.data.libraries.load(str(path),link=False) as (a,b):b.objects=a.objects
        for o in b.objects:
            if o:bpy.context.scene.collection.objects.link(o)
    else:bpy.ops.import_scene.gltf(filepath=str(path))
    objects=[o for o in bpy.data.objects if o not in before and o.type=='MESH']
    bpy.context.view_layer.update()
    for o in objects:
        matrix=o.matrix_world.copy();o.parent=None;o.matrix_world=matrix
    for image in bpy.data.images:
        if image.packed_file or image.source!='FILE':continue
        candidate=path.parent/'textures'/Path(image.filepath.replace('\\','/')).name
        if candidate.is_file():image.filepath=str(candidate);image.reload()
    return objects


def fitted_copy(template,component_id,size,position,rotation=0.,lean=0.,flat=False):
    copies=[]
    for obj in template:
        new=obj.copy();new.data=obj.data.copy();bpy.context.scene.collection.objects.link(new)
        new.parent=None;new.matrix_world=obj.matrix_world.copy()
        new.name=component_id+'/'+obj.name;new['finescene_component_id']=component_id
        new['finescene_asset_source']=obj.get('finescene_asset_source','')
        copies.append(new)
    if flat:
        rot=Matrix.Rotation(-math.pi/2,4,'Y')
        for obj in copies:obj.matrix_world=rot@obj.matrix_world
    bpy.context.view_layer.update();low,high=bounds(copies)
    dims=high-low
    norm=Matrix.Diagonal(Vector((size[0]/dims.x,size[1]/dims.y,size[2]/dims.z,1.))) @ Matrix.Translation(Vector((-(low.x+high.x)/2,-(low.y+high.y)/2,-low.z)))
    for obj in copies:obj.matrix_world=norm@obj.matrix_world
    if lean:
        for obj in copies:obj.matrix_world=Matrix.Rotation(math.radians(lean),4,'Y')@obj.matrix_world
        bpy.context.view_layer.update();lo,hi=bounds(copies)
        offset=Matrix.Translation(Vector((-(lo.x+hi.x)/2,0,-lo.z)))
        for obj in copies:obj.matrix_world=offset@obj.matrix_world
    place=Matrix.Translation(Vector(position))@Matrix.Rotation(math.radians(rotation),4,'Z')
    for obj in copies:obj.matrix_world=place@obj.matrix_world
    return copies


def surface(name,asset,tint=None,scale=1):
    material=bpy.data.materials.new(name);material.use_nodes=True
    nodes=material.node_tree.nodes;links=material.node_tree.links
    bsdf=next((n for n in nodes if n.type=='BSDF_PRINCIPLED'),None)
    if bsdf is None:
        bsdf=nodes.new('ShaderNodeBsdfPrincipled');output=nodes.new('ShaderNodeOutputMaterial');links.new(bsdf.outputs[0],output.inputs['Surface'])
    coord=nodes.new('ShaderNodeTexCoord');mapping=nodes.new('ShaderNodeVectorMath');mapping.operation='SCALE';mapping.inputs[3].default_value=scale
    links.new(coord.outputs['Object'],mapping.inputs[0])
    textures=list((ASSETS/asset/'textures').glob('*'))
    def tex(tokens,color=False):
        path=next((p for p in textures if any(t in p.name for t in tokens)),None)
        if path is None:return None
        n=nodes.new('ShaderNodeTexImage');n.image=bpy.data.images.load(str(path),check_existing=True)
        if not color:n.image.colorspace_settings.name='Non-Color'
        n.projection='BOX';n.projection_blend=.15;links.new(mapping.outputs[0],n.inputs['Vector']);return n
    diffuse=tex(['_diff_','_col_1_'],True)
    if diffuse:
        if tint:
            mix=nodes.new('ShaderNodeMixRGB');mix.blend_type='MULTIPLY';mix.inputs[0].default_value=1;mix.inputs[2].default_value=(*tint,1)
            links.new(diffuse.outputs['Color'],mix.inputs[1]);links.new(mix.outputs[0],bsdf.inputs['Base Color'])
        else:links.new(diffuse.outputs['Color'],bsdf.inputs['Base Color'])
    rough=tex(['_rough_']);arm=tex(['_arm_'])
    if rough:
        remap=nodes.new('ShaderNodeMapRange');remap.inputs['From Min'].default_value=.05;remap.inputs['From Max'].default_value=.9
        remap.inputs['To Min'].default_value=.48;remap.inputs['To Max'].default_value=.82
        links.new(rough.outputs['Color'],remap.inputs['Value']);links.new(remap.outputs['Result'],bsdf.inputs['Roughness'])
    elif arm:
        separate=nodes.new('ShaderNodeSeparateColor');links.new(arm.outputs['Color'],separate.inputs[0]);links.new(separate.outputs['Green'],bsdf.inputs['Roughness'])
    bsdf.inputs['Metallic'].default_value=0
    normal_path=next((p for p in textures if '_nor_gl_' in p.name),None)
    if normal_path:
        normal_tex=nodes.new('ShaderNodeTexImage');normal_tex.image=bpy.data.images.load(str(normal_path),check_existing=True)
        normal_tex.image.colorspace_settings.name='Non-Color';normal_tex.projection='FLAT'
        links.new(coord.outputs['UV'],normal_tex.inputs['Vector'])
        normal_map=nodes.new('ShaderNodeNormalMap');normal_map.inputs['Strength'].default_value=.28
        links.new(normal_tex.outputs['Color'],normal_map.inputs['Color']);links.new(normal_map.outputs['Normal'],bsdf.inputs['Normal'])
    return material


def area(name,location,target,power,color,size):
    data=bpy.data.lights.new(name,'AREA');data.energy=power;data.color=color;data.shape='DISK';data.size=size
    obj=bpy.data.objects.new(name,data);bpy.context.scene.collection.objects.link(obj);obj.location=location
    obj.rotation_euler=(Vector(target)-obj.location).to_track_quat('-Z','Y').to_euler();return obj


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--source',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args(sys.argv[sys.argv.index('--')+1:]);out=args.output
    contract=json.loads((out/'resolved_scene_contract.json').read_text(encoding='utf-8'))
    bpy.ops.wm.open_mainfile(filepath=str(args.source/'blender_export/scene.blend'))
    scene=bpy.context.scene;known={c['id'] for c in contract['components']};by_id={c['id']:c for c in contract['components']}
    originals={}
    for o in list(scene.objects):
        cid=identity(o,known)
        if cid and o.type=='MESH':originals.setdefault(cid,[]).append(o);o['finescene_component_id']=cid
    chair_id='oakwarden_reading_chair'
    chair_parts=originals.get(chair_id,[])
    for obj in list(chair_parts):
        path=str(obj.get('adsl_path',obj.name)).lower()
        if any(part in path for part in ('wood_apex_finial','wood_finial_','brass_finial_collar')):
            bpy.data.objects.remove(obj,do_unlink=True);chair_parts.remove(obj);continue
        if '/back/' in path:
            pivot=Matrix.Translation(Vector((0,0,-.45)))
            obj.matrix_world=pivot.inverted() @ Matrix.Diagonal(Vector((1,1,.94,1))) @ pivot @ obj.matrix_world
        for slot_index,source_material in enumerate(list(obj.data.materials)):
            if not source_material or not source_material.use_nodes:continue
            material=source_material.copy();obj.data.materials[slot_index]=material
            leather='leather' in path or 'upholstery' in path
            for node in material.node_tree.nodes:
                if node.type!='BSDF_PRINCIPLED':continue
                node.inputs['Roughness'].default_value=.7 if leather else max(.52,node.inputs['Roughness'].default_value)
                if 'Coat Weight' in node.inputs:node.inputs['Coat Weight'].default_value=0
                if leather:node.inputs['Base Color'].default_value=(.16,.035,.035,1)
    # Load all reusable templates once; clones share image data, retain their own meshes/IDs.
    libraries={};sources={}
    for name in ['WoodenTable_01','painted_wooden_chair_02','side_table_tall_01','vintage_oil_lamp','potted_plant_02','binder_notebook','fancy_picture_frame_01','desk_lamp_arm_01']:
        path=ASSETS/name/(name+'_2k.gltf')
        if path.is_file():libraries[name]=load_asset(path);sources[name]=str(path)
    for name,path in [('books',ASSETS/'decorative_book_set_01/decorative_book_set_01_2k.blend'),('encyclopedia',POLY/'book_encyclopedia_set_01_2k/book_encyclopedia_set_01_2k.gltf'),('trellis2_statue',ASSETS/'trellis2_greek_statue/statue.glb')]:
        libraries[name]=load_asset(path);sources[name]=str(path)
    for name,objects in libraries.items():
        for obj in objects:obj['finescene_asset_source']=sources[name]
    receipts=[]
    def replace(cid,key,size,position,rotation=0,templates=None,lean=0,flat=False):
        for o in originals.get(cid,[]):bpy.data.objects.remove(o,do_unlink=True)
        created=fitted_copy(templates or libraries[key],cid,size,position,rotation,lean,flat)
        bpy.context.view_layer.update();lo,hi=bounds(created)
        receipts.append({'component_id':cid,'asset_id':key,'source':sources[key],'source_objects':[o.name for o in templates or libraries[key]],'bounds':[list(lo),list(hi)],'fit_dimensions':list(size),'fit_policy':'explicit dimension fit; preserved thereafter during edits'})
        if cid in by_id:by_id[cid]['source'].update(mode='retrieved',path=sources[key],generator=key)
        return created
    books=sorted(libraries['books'],key=lambda o:o.name)
    encyclopedia=sorted(libraries['encyclopedia'],key=lambda o:o.name)
    for index,c in enumerate(c for c in contract['components'] if c['kind']=='book'):
        rng=random.Random(c['id']);key='encyclopedia' if index%10==0 else 'books'
        source=rng.choice(encyclopedia if key=='encyclopedia' else books)
        replace(c['id'],key,c['geometry_size'],c['transform']['position'],c['transform']['rotation_deg'][2],templates=[source],lean=c['layout_lean_deg'],flat=c['layout_variant']=='horizontal_stack')
    # Match the existing functional envelopes, preserving floor and tabletop datums.
    for task in contract['furniture_tasks']:
        cid=task['component_id'];kind=task['kind']
        key='WoodenTable_01' if kind=='desk' else 'painted_wooden_chair_02' if kind=='chair' and task.get('role')=='desk' else None
        if key:
            size=tuple(task['parameters'].get(k,d) for k,d in zip(('width','depth','height'),(.52,.55,.88)))
            replace(cid,key,size,task['placement'],task.get('rotation_deg',0))
    for task in contract['decor_tasks']:
        cid=task['component_id'];kind=task['kind']
        key={'side_table':'side_table_tall_01','plant':'potted_plant_02'}.get(kind)
        if kind=='lamp' and task.get('role')=='desk':key='desk_lamp_arm_01'
        elif kind=='lamp':key='vintage_oil_lamp'
        if key:replace(cid,key,task['size'],task['placement'])
        elif kind=='book_stack':
            for o in originals.get(cid,[]):bpy.data.objects.remove(o,do_unlink=True)
            for j in range(3):
                pos=list(task['placement']);pos[2]+=task['size'][2]*j/3
                dims=[task['size'][0]-j*.012,task['size'][1]-j*.01,task['size'][2]/3]
                fitted_copy([books[10+j*8]],cid,dims,pos,flat=True)
            receipts.append({'component_id':cid,'asset_id':'books','source':sources['books'],'mode':'three independent textured volumes'})
    # New accents are derived from support anchors and recorded as semantic components.
    def accent(cid,key,size,position,parent='room',rotation=0,templates=None,support=None):
        replace(cid,key,size,position,rotation,templates)
        contract['components'].append({'id':cid,'kind':'prop','parent':parent,'bounds':{'size':list(size),'unit':'m'},'transform':{'position':list(position),'rotation_deg':[0,0,rotation],'scale':[1,1,1]},'anchors':['bottom_center','top'],'editable':[],'source':{'mode':'retrieved','path':sources[key],'license':'CC0' if key!='trellis2_statue' else 'See download manifest','generator':key}})
        contract['relations'].append({'type':'parent','from':cid,'to':parent})
        if support:contract['relations'].append({'type':'supported_by','from':cid,'to':support})
    desk=next(t for t in contract['furniture_tasks'] if t['kind']=='desk');dx,dy,dz=desk['placement'];dz+=desk['parameters']['height']
    notebook=next(o for o in libraries['binder_notebook'] if o.name=='binder_notebook')
    accent('writing_notebook','binder_notebook',(.38,.23,.02),(dx-.12,dy-.12,dz),desk['component_id'],templates=[notebook],support=desk['component_id'])
    accent('trellis2_sculpture','trellis2_statue',(.17,.13,.30),(dx+.15,dy+.21,dz),desk['component_id'],rotation=180,support=desk['component_id'])
    room=contract['room'];w,d,h=(room[k] for k in ('width','depth','height'))
    accent('reading_wall_art','fancy_picture_frame_01',(.85,.025,.65),(-w/2+.15,.0,1.65),'room',rotation=90)
    for objects in libraries.values():
        for o in objects:bpy.data.objects.remove(o,do_unlink=True)
    # Physically textured architectural shell and retained editable bookcase frames.
    wood=surface('Walnut_PBR','wood_table_001',(.48,.35,.24),.72)
    floor=surface('Wood_floor_PBR','wood_floor',None,.34)
    wall=surface('Sage_plaster_PBR','plastered_wall_04',(.17,.28,.21),.7)
    fabric=surface('Woven_rug_PBR','fabric_pattern_07',(.5,.25,.19),1.1)
    for obj in list(scene.objects):
        if obj.type!='MESH':continue
        cid=identity(obj,known);parts=[];node=obj
        while node:parts.append(str(node.get('adsl_path',node.name)));node=node.parent
        path='/'.join(parts)
        mat=None
        if cid and ('.shelf_' in cid and '.book' not in cid or cid.startswith('bookcase_') and '.book' not in cid):mat=wood
        if cid=='room_shell':
            if 'window_glass' in path:
                obj.hide_render=True;continue
            if 'floor' in path:mat=floor
            elif 'wall' in path or 'back_lower' in path or 'back_upper' in path or 'back_left' in path or 'back_right' in path:mat=wall
            else:mat=wood
        if cid=='reading_rug':mat=fabric
        if mat:obj.data.materials.clear();obj.data.materials.append(mat)
    # Real enclosed room during interior rendering, with a genuine window opening.
    def box(name,size,center,mat):
        bpy.ops.mesh.primitive_cube_add(size=1,location=center);o=bpy.context.object;o.name=name;o.dimensions=size
        bpy.ops.object.transform_apply(location=False,rotation=False,scale=True);o.data.materials.append(mat);o['finescene_component_id']='room_shell';return o
    ceiling=box('realistic_ceiling',(w,d,.08),(0,0,h+.04),wall)
    front=box('realistic_front_wall',(w,.12,h),(0,-d/2,h/2),wall)
    # Add window mullions and sill joinery in the existing opening.
    sill=room['window_sill'];ww=room['window_width'];wh=room['window_height']
    box('window_center_mullion',(.045,.11,wh),(0,d/2-.08,sill+wh/2),wood)
    box('window_crossbar',(ww,.11,.04),(0,d/2-.08,sill+wh*.5),wood)
    glass=bpy.data.materials.new('Window_Glass');glass.use_nodes=True
    glass_bsdf=next(n for n in glass.node_tree.nodes if n.type=='BSDF_PRINCIPLED')
    glass_bsdf.inputs['Roughness'].default_value=.12;glass_bsdf.inputs['Transmission Weight'].default_value=.94;glass_bsdf.inputs['IOR'].default_value=1.45
    box('window_glazing',(ww-.12,.012,wh-.12),(0,d/2-.12,sill+wh/2),glass)
    for obj in list(scene.objects):
        if obj.type=='LIGHT':bpy.data.objects.remove(obj,do_unlink=True)
    world=scene.world;world.use_nodes=True;nodes=world.node_tree.nodes;links=world.node_tree.links
    nodes.clear();env=nodes.new('ShaderNodeTexEnvironment');env.image=bpy.data.images.load(str(ASSETS/'venice_sunset/venice_sunset_2k.hdr'))
    background=nodes.new('ShaderNodeBackground');background.inputs['Strength'].default_value=.22;output=nodes.new('ShaderNodeOutputWorld');links.new(env.outputs[0],background.inputs[0]);links.new(background.outputs[0],output.inputs[0])
    area('Window_daylight',(0,d/2+.2,2.0),(0,-1,.6),260,(.78,.86,1),3.0)
    area('Soft_interior_bounce',(-1,-1,2.65),(1,.5,.8),65,(1,.92,.82),2.5)
    for task in contract['decor_tasks']:
        if task['kind']=='lamp':
            x,y,z=task['placement'];data=bpy.data.lights.new(task['component_id']+'_warm','POINT');data.energy=12;data.color=(1,.55,.22);data.shadow_soft_size=.035
            obj=bpy.data.objects.new(data.name,data);scene.collection.objects.link(obj);obj.location=(x,y,z+task['size'][2]*.6)
    scene.render.engine='CYCLES';scene.cycles.samples=96;scene.cycles.use_denoising=True
    try:
        prefs=bpy.context.preferences.addons['cycles'].preferences;prefs.compute_device_type='OPTIX';prefs.get_devices()
        for device in prefs.devices:device.use=device.type!='CPU'
        if any(x.use for x in prefs.devices):scene.cycles.device='GPU'
    except Exception:pass
    scene.view_settings.view_transform='AgX';scene.view_settings.exposure=0.0
    scene.render.resolution_x=1400;scene.render.resolution_y=1000;scene.render.resolution_percentage=100
    render=out/'blender_export/render';render.mkdir(parents=True,exist_ok=True)
    camera=scene.camera;camera.data.type='PERSP';camera.data.clip_start=.02
    views=[('interior',(-2.30,-1.95,1.65),(.5,.8,1.2),23),('reading_corner',(1.3,-1.9,1.5),(-2.05,.3,1.05),35),('book_detail',(.95,-1.3,1.65),(2.56,-.95,1.55),48),('desk_detail',(-.5,.45,1.5),(0,1.85,1.03),40)]
    images=[]
    for name,loc,target,lens in views:
        camera.location=loc;camera.rotation_euler=(Vector(target)-camera.location).to_track_quat('-Z','Y').to_euler();camera.data.lens=lens
        scene.render.filepath=str(render/(name+'.png'));bpy.ops.render.render(write_still=True);images.append(scene.render.filepath)
    ceiling.hide_render=True;front.hide_render=True
    camera.data.type='ORTHO';camera.data.ortho_scale=max(w,d)*1.1;camera.location=(0,0,h*4);camera.rotation_euler=(0,0,0)
    scene.render.filepath=str(render/'top.png');bpy.ops.render.render(write_still=True);images.append(scene.render.filepath)
    ceiling.hide_render=False;front.hide_render=False
    camera.data.type='PERSP';camera.data.lens=views[0][3];camera.location=views[0][1];camera.rotation_euler=(Vector(views[0][2])-camera.location).to_track_quat('-Z','Y').to_euler()
    bpy.context.view_layer.update();actual={}
    for obj in scene.objects:
        cid=obj.get('finescene_component_id')
        if cid and obj.type=='MESH':actual.setdefault(cid,[]).append(obj)
    actual={k:[list(x) for x in bounds(v)] for k,v in actual.items()}
    (out/'blender_world_bounds.json').write_text(json.dumps(actual,indent=2),encoding='utf-8')
    (out/'asset_refinement_receipts.json').write_text(json.dumps(receipts,indent=2),encoding='utf-8')
    (out/'resolved_scene_contract.json').write_text(json.dumps(contract,ensure_ascii=False,indent=2),encoding='utf-8')
    (out/'blender_export/execution.json').write_text(json.dumps({'render_paths':images,'blender_version':bpy.app.version_string,'quality_target':'photorealistic','render_device':scene.cycles.device,'samples':scene.cycles.samples},indent=2),encoding='utf-8')
    missing=[im.filepath for im in bpy.data.images if im.source=='FILE' and not im.packed_file and not Path(bpy.path.abspath(im.filepath)).is_file()]
    (out/'texture_audit.json').write_text(json.dumps({'missing_files':missing,'images':len(bpy.data.images)},indent=2),encoding='utf-8')
    bpy.ops.file.pack_all();bpy.ops.wm.save_as_mainfile(filepath=str(out/'blender_export/scene.blend'))
    bpy.ops.object.select_all(action='DESELECT')
    for obj in scene.objects:
        if obj.type=='MESH':obj.select_set(True)
    bpy.ops.export_scene.gltf(filepath=str(render/'scene.glb'),export_format='GLB',use_selection=True,export_extras=True,export_apply=True)
    print('REALISTIC_REFINEMENT_COMPLETE',flush=True)


if __name__=='__main__':main()
