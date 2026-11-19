"""Material, bevel and render the actual aDSL bookcase; no external assets."""
import bpy
import json
import math
from pathlib import Path
from mathutils import Vector

HERE=Path(__file__).resolve().parent
books=json.loads((HERE/'books.json').read_text(encoding='utf-8'))

def material(name,base,rough=.5,metal=0):
    mat=bpy.data.materials.new(name);mat.use_nodes=True
    mat.node_tree.nodes.clear()
    bs=mat.node_tree.nodes.new('ShaderNodeBsdfPrincipled')
    out=mat.node_tree.nodes.new('ShaderNodeOutputMaterial');out.is_active_output=True
    mat.node_tree.links.new(bs.outputs['BSDF'],out.inputs['Surface'])
    bs.inputs['Base Color'].default_value=(*base,1)
    bs.inputs['Roughness'].default_value=rough;bs.inputs['Metallic'].default_value=metal
    return mat,bs

def noise_finish(mat,bs,scale,dark,light,stretch=None,bumpdist=.0003):
    nodes=mat.node_tree.nodes;links=mat.node_tree.links
    coord=nodes.new('ShaderNodeTexCoord')
    noise=nodes.new('ShaderNodeTexNoise');noise.inputs['Scale'].default_value=scale;noise.inputs['Detail'].default_value=3.5
    source=coord.outputs['Generated']
    if stretch:
        mapping=nodes.new('ShaderNodeVectorMath');mapping.operation='MULTIPLY';mapping.inputs[1].default_value=stretch
        links.new(source,mapping.inputs[0]);source=mapping.outputs[0]
    links.new(source,noise.inputs['Vector'])
    ramp=nodes.new('ShaderNodeValToRGB');ramp.color_ramp.elements[0].position=.18;ramp.color_ramp.elements[1].position=.83
    ramp.color_ramp.elements[0].color=(*dark,1);ramp.color_ramp.elements[1].color=(*light,1)
    links.new(noise.outputs['Fac'],ramp.inputs[0]);links.new(ramp.outputs['Color'],bs.inputs['Base Color'])
    bump=nodes.new('ShaderNodeBump');bump.inputs['Strength'].default_value=.20;bump.inputs['Distance'].default_value=bumpdist
    links.new(noise.outputs['Fac'],bump.inputs['Height']);links.new(bump.outputs['Normal'],bs.inputs['Normal'])

wood,bs=material('Walnut | warm waxed heartwood',(.13,.055,.023),.36)
noise_finish(wood,bs,4,(.038,.013,.0045),(.22,.105,.041),(8,8,.65),.0011)
gold,bs=material('Binding | aged gold leaf',(.47,.29,.09),.42,.72)
brass,bs=material('Hardware | antique brass',(.31,.19,.075),.45,.74)
pages,bs=material('Paper | warm aged edges',(.63,.53,.36),.86)
noise_finish(pages,bs,8,(.40,.31,.18),(.77,.69,.51),(1,1,45),.00015)
edge,_=material('Paper | quiet edge lines',(.37,.29,.17),.9)
label,_=material('Binding | inset tobacco labels',(.075,.043,.018),.6)
colors=[(.16,.025,.026),(.035,.10,.073),(.045,.085,.13),(.24,.12,.045),
        (.31,.23,.105),(.18,.125,.085),(.115,.06,.075),(.30,.28,.21)]
covers=[]
for i,color in enumerate(colors):
    mat,bs=material('Book cover | '+['oxblood','bottle green','faded indigo','tobacco','ochre','umber','plum','linen'][i],color,.57 if i!=7 else .83)
    noise_finish(mat,bs,88,tuple(c*.63 for c in color),tuple(c*1.15 for c in color),bumpdist=.00028)
    covers.append(mat)

def assign(obj,mat):
    obj.data.materials.clear();obj.data.materials.append(mat)

def bevel(obj,width):
    b=obj.modifiers.new('Soft worn edges','BEVEL');b.width=width;b.segments=3;b.limit_method='ANGLE'
    n=obj.modifiers.new('Weighted corner normals','WEIGHTED_NORMAL');n.keep_sharp=True

for obj in list(bpy.context.scene.objects):
    if obj.type in ('LIGHT','CAMERA'):
        bpy.data.objects.remove(obj,do_unlink=True);continue
    if obj.type!='MESH':continue
    path=obj.parent.get('adsl_path','') if obj.parent else ''
    leaf=path.rsplit('/',1)[-1]
    if leaf.startswith(('cover_','spine_','binding_band_')) and leaf!='spine_label':
        index=int(leaf.split('_')[2] if leaf.startswith('binding_band_') else leaf.split('_')[1])
        mat=covers[index];width=.0011
    elif 'aged_pages' in leaf:mat=pages;width=.0007
    elif 'page_edge_' in leaf:mat=edge;width=.0001
    elif 'gilt_' in leaf:mat=gold;width=.00015
    elif 'spine_label' in leaf:mat=label;width=.0006
    elif 'brass_' in leaf:mat=brass;width=.0005
    else:mat=wood;width=min(.004,min(obj.dimensions)*.18)
    assign(obj,mat)
    if '_cube' in obj.name or obj.name.endswith('cube'):bevel(obj,width)
    else:
        for poly in obj.data.polygons:poly.use_smooth=True

# Quiet period-style spine lettering, on selected unrotated bindings.
# This small native Blender refinement supplements the semantic aDSL geometry.
titles=['ATLAS','POEMS','HISTORY','BOTANY','TRAVELS','ESSAYS','LETTERS','MYTHS']
for b in books:
    if b['kind']!='upright' or b['lean_degrees'] or b['id']%4==0:continue
    lo,hi=b['bounds'];x=(lo[0]+hi[0])/2;z=lo[2]+(hi[2]-lo[2])*.56
    data=bpy.data.curves.new(f"Volume {b['id']} gilt title",'FONT')
    data.body=titles[b['id']%len(titles)];data.align_x='CENTER';data.align_y='CENTER';data.size=.0065;data.extrude=.000025
    obj=bpy.data.objects.new(data.name,data);bpy.context.scene.collection.objects.link(obj)
    obj.location=(x,lo[1]-.00025,z);obj.rotation_euler=(math.pi/2,0,0)
    assign(obj,gold);bpy.context.view_layer.update()
    if obj.dimensions.x>hi[0]-lo[0]-.012:
        obj.scale.x=(hi[0]-lo[0]-.012)/obj.dimensions.x
    bpy.ops.object.select_all(action='DESELECT');obj.select_set(True);bpy.context.view_layer.objects.active=obj
    bpy.ops.object.convert(target='MESH');obj=bpy.context.object
    root=next(o for o in bpy.context.scene.objects if o.parent is None and o.type=='EMPTY')
    obj.parent=root;obj['adsl_kind']='native_gilt_lettering'

asset_objects=[o for o in bpy.context.scene.objects if o.type in ('MESH','CURVE','EMPTY')]
bpy.ops.object.select_all(action='DESELECT')
for obj in asset_objects:obj.select_set(True)
bpy.ops.export_scene.gltf(filepath=str(HERE/'vintage_bookshelf.glb'),export_format='GLB',use_selection=True,export_apply=True,export_extras=True)

floor,bs=material('Stage | warm matte plaster',(.19,.17,.139),.88)
noise_finish(floor,bs,9,(.16,.145,.122),(.23,.211,.175),bumpdist=.003)
bpy.ops.mesh.primitive_plane_add(size=200,location=(0,0,-.001))
obj=bpy.context.object;obj.name='Presentation floor';assign(obj,floor)
sc=bpy.context.scene
sc.world=bpy.data.worlds.new('Soft reading room ambient');sc.world.use_nodes=True;sc.world.node_tree.nodes.clear()
bg=sc.world.node_tree.nodes.new('ShaderNodeBackground');out=sc.world.node_tree.nodes.new('ShaderNodeOutputWorld');out.is_active_output=True
sc.world.node_tree.links.new(bg.outputs[0],out.inputs[0]);bg.inputs[0].default_value=(.25,.27,.30,1);bg.inputs[1].default_value=.28
for name,loc,power,size,color in [
    ('Warm window',(-2.8,-3.2,4.0),650,2.8,(1,.86,.69)),
    ('Book spine fill',(2.3,-2.7,2.6),350,2.4,(.83,.89,1)),
    ('Walnut rim',(1.0,1.8,3.0),440,1.9,(1,.77,.51))]:
    data=bpy.data.lights.new(name,'AREA');data.energy=power;data.shape='DISK';data.size=size;data.color=color
    obj=bpy.data.objects.new(name,data);sc.collection.objects.link(obj);obj.location=loc
    obj.rotation_euler=(Vector((0,0,.95))-obj.location).to_track_quat('-Z','Y').to_euler()
data=bpy.data.cameras.new('Bookcase portrait');camera=bpy.data.objects.new('Bookcase portrait',data);sc.collection.objects.link(camera);sc.camera=camera
sc.render.engine='CYCLES';sc.cycles.device='CPU';sc.cycles.samples=64;sc.cycles.use_denoising=True
sc.render.resolution_x=1160;sc.render.resolution_y=1320;sc.render.resolution_percentage=100
sc.render.image_settings.file_format='PNG';sc.render.image_settings.color_mode='RGB'
sc.view_settings.view_transform='AgX';sc.view_settings.exposure=-.05
hero=(2.55,-4.4,2.5);target=(0,-.005,.91)

def view(name,loc,target,ortho=None):
    camera.location=loc;camera.rotation_euler=(Vector(target)-camera.location).to_track_quat('-Z','Y').to_euler()
    data.type='ORTHO' if ortho else 'PERSP';data.lens=66
    if ortho:data.ortho_scale=ortho
    sc.render.filepath=str(HERE/(name+'.png'));bpy.ops.render.render(write_still=True)

view('hero',hero,target)
view('front',(0,-4.8,1.12),(0,0,.92),2.15)
camera.location=hero;camera.rotation_euler=(Vector(target)-camera.location).to_track_quat('-Z','Y').to_euler();data.type='PERSP';data.lens=66
bpy.ops.object.select_all(action='DESELECT')
root=next(o for o in asset_objects if o.parent is None and o.type=='EMPTY');root.select_set(True);bpy.context.view_layer.objects.active=root
for screen in bpy.data.screens:
    for area in screen.areas:
        if area.type=='VIEW_3D':area.spaces.active.region_3d.view_perspective='CAMERA'
bpy.ops.wm.save_as_mainfile(filepath=str(HERE/'vintage_bookshelf.blend'))
report=dict(name='The Antiquarian',usable_levels=4,books=len(books),asset_meshes=sum(o.type=='MESH' for o in asset_objects),
            project='vintage_bookshelf.blend',export='vintage_bookshelf.glb',renders=['hero.png','front.png'],
            materials='Native procedural walnut, varied book bindings and aged paper; GLB uses base PBR colors.')
(HERE/'result.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print('BOOKCASE_RENDER_COMPLETE '+json.dumps(report))
