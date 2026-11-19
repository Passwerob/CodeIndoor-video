"""Make a clean, isolated 3D reference image for a TRELLIS.2 toilet test."""
import bpy, math
from mathutils import Vector
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
import finescene_env as fenv

OUT = fenv.RUNS / 'trellis_toilet_20261020_01'
OUT.mkdir(parents=True, exist_ok=True)  # The first aborted attempt left this run directory empty.
bpy.ops.wm.read_factory_settings(use_empty=True)


def mat(name, color, roughness=.28, metallic=0):
    m=bpy.data.materials.new(name);m.diffuse_color=(*color,1);m.use_nodes=True
    p=next((n for n in m.node_tree.nodes if n.type=='BSDF_PRINCIPLED'),None)
    if p is None:p=m.node_tree.nodes.new('ShaderNodeBsdfPrincipled')
    p.inputs['Base Color'].default_value=(*color,1);p.inputs['Roughness'].default_value=roughness;p.inputs['Metallic'].default_value=metallic
    return m

porcelain=mat('warm white vitreous porcelain',(.86,.84,.79),.19)
seatmat=mat('porcelain seat',(.93,.91,.86),.22)
interior=mat('shadow inside bowl',(.24,.27,.28),.34)
chrome=mat('brushed chrome',(.35,.38,.40),.24,.78)

def mesh_obj(name, verts, faces, material, smooth=True):
    me=bpy.data.meshes.new(name);me.from_pydata(verts,[],faces);me.materials.append(material);me.update()
    ob=bpy.data.objects.new(name,me);bpy.context.collection.objects.link(ob)
    if smooth:
        for p in me.polygons:p.use_smooth=True
    return ob

def oval_rings(name, profile, center_y, material, segments=96, close_bottom=True):
    verts=[];faces=[]
    for z,rx,ry in profile:
        for i in range(segments):
            a=2*math.pi*i/segments
            verts.append((rx*math.cos(a),center_y+ry*math.sin(a),z))
    for r in range(len(profile)-1):
        for i in range(segments):
            a=r*segments+i;b=r*segments+(i+1)%segments;c=(r+1)*segments+(i+1)%segments;d=(r+1)*segments+i
            faces.append((a,b,c,d))
    if close_bottom:
        faces.append(tuple(reversed(range(segments))))
    return mesh_obj(name,verts,faces,material)

def oval_ring(name, center, outer, inner, zbottom, ztop, material, segments=128):
    cx,cy=center;ox,oy=outer;ix,iy=inner
    verts=[]
    # outer bottom, outer top, inner top, inner bottom
    for z,rx,ry in [(zbottom,ox,oy),(ztop,ox,oy),(ztop,ix,iy),(zbottom,ix,iy)]:
        for i in range(segments):
            a=2*math.pi*i/segments;verts.append((cx+rx*math.cos(a),cy+ry*math.sin(a),z))
    faces=[]
    for k in range(4):
        kn=(k+1)%4
        for i in range(segments):
            ni=(i+1)%segments
            faces.append((k*segments+i,k*segments+ni,kn*segments+ni,kn*segments+i))
    return mesh_obj(name,verts,faces,material)

def bevel_cube(name, dims, loc, material, bevel=.025):
    bpy.ops.mesh.primitive_cube_add(size=1, location=loc);o=bpy.context.object;o.name=name;o.dimensions=dims
    bpy.ops.object.transform_apply(location=False,rotation=False,scale=True);o.data.materials.append(material)
    mod=o.modifiers.new('soft ceramic edges','BEVEL');mod.width=bevel;mod.segments=5
    o.modifiers.new('weighted corner normals','WEIGHTED_NORMAL');return o

def cylinder(name, radius, depth, loc, material, direction='Z'):
    bpy.ops.mesh.primitive_cylinder_add(vertices=48, radius=radius, depth=depth, location=loc);o=bpy.context.object;o.name=name;o.data.materials.append(material)
    if direction=='Y':o.rotation_euler[0]=math.pi/2
    bevel=o.modifiers.new('edge chamfer','BEVEL');bevel.width=min(radius*.18,depth*.2);bevel.segments=3
    o.modifiers.new('weighted normals','WEIGHTED_NORMAL');return o

# Smooth one-piece pedestal and open bowl, facing toward -Y.
oval_rings('toilet pedestal and bowl',[(.025,.135,.145),(.055,.145,.155),(.12,.15,.17),(.20,.17,.19),(.29,.195,.22),(.37,.215,.245),(.43,.22,.25),(.47,.215,.245)],-.105,porcelain)
# Inner ceramic bowl descends to the drain; a dark water surface sits below its lip.
oval_rings('visible inner bowl',[(.462,.174,.205),(.43,.15,.18),(.39,.118,.145),(.35,.075,.095),(.335,.045,.055)],-.105,porcelain,close_bottom=False)
bpy.ops.mesh.primitive_uv_sphere_add(segments=48, ring_count=24, location=(0,-.105,.338));water=bpy.context.object;water.name='water in bowl';water.scale=(.048,.06,.008);water.data.materials.append(interior)
for p in water.data.polygons:p.use_smooth=True
# Raised seat is a true ring, with the bowl opening visible through it.
oval_ring('toilet seat ring',(0,-.115),(.226,.267),(.132,.17),.464,.495,seatmat)
# Hinges and rear rectangular cistern with rounded edges and separate lid.
for x in (-.115,.115):
    cylinder('seat hinge',.018,.055,(x,.145,.492),chrome,'Y')
bevel_cube('toilet cistern',(.405,.205,.355),(0,.205,.625),porcelain,.035)
bevel_cube('cistern lid',(.425,.22,.035),(0,.205,.8175),seatmat,.015)
cylinder('flush button',.026,.009,(0,.205,.84),chrome)
# Small flush plate-like button detail and subtle base contact.

bpy.ops.object.camera_add(location=(1.8,-2.8,1.65));cam=bpy.context.object;cam.name='Reference Camera';target=Vector((0,-.02,.43));cam.rotation_euler=(target-cam.location).to_track_quat('-Z','Y').to_euler();cam.data.type='ORTHO';cam.data.ortho_scale=1.22;cam.data.lens=50;bpy.context.scene.camera=cam
for name,loc,power,size in [('large softbox',(1,-2,3),480,2.3),('fill',(-2,-1,1.8),260,2),('rim',(0,2,2.2),360,1.4)]:
    data=bpy.data.lights.new(name,'AREA');data.energy=power;data.shape='DISK';data.size=size;ob=bpy.data.objects.new(name,data);bpy.context.collection.objects.link(ob);ob.location=loc;ob.rotation_euler=(Vector((0,0,.43))-ob.location).to_track_quat('-Z','Y').to_euler()
scene=bpy.context.scene;scene.render.engine='CYCLES';scene.cycles.samples=64;scene.cycles.use_denoising=True
try:
    prefs=bpy.context.preferences.addons['cycles'].preferences;prefs.compute_device_type='OPTIX';prefs.get_devices()
    for device in prefs.devices:device.use=device.type!='CPU'
    if any(d.use for d in prefs.devices):scene.cycles.device='GPU'
except Exception:pass
scene.render.resolution_x=1024;scene.render.resolution_y=1024;scene.render.resolution_percentage=100;scene.render.image_settings.file_format='PNG';scene.render.film_transparent=True
scene.world=bpy.data.worlds.new('studio white');scene.world.use_nodes=True
bg=scene.world.node_tree.nodes.new('ShaderNodeBackground');out=scene.world.node_tree.nodes.new('ShaderNodeOutputWorld');scene.world.node_tree.links.new(bg.outputs[0],out.inputs[0]);bg.inputs[0].default_value=(.72,.72,.72,1);bg.inputs[1].default_value=.45
scene.view_settings.view_transform='AgX';scene.view_settings.exposure=0
scene.render.filepath=str(OUT/'toilet_reference.png');bpy.ops.render.render(write_still=True)
bpy.ops.wm.save_as_mainfile(filepath=str(OUT/'reference_scene.blend'))
print('REFERENCE_SAVED',scene.render.filepath,flush=True)

