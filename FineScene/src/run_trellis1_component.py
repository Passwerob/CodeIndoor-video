"""Run the verified local TRELLIS.1 environment and export a raw mesh candidate."""
import argparse
import gc
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
import sys
import time

import finescene_env as fenv

ROOT = fenv.REPO_ROOT
RESEARCH = fenv.RESEARCH_ROOT
os.environ['HF_HOME'] = str(RESEARCH / 'hf_cache')
os.environ['HF_HUB_OFFLINE'] = '1'
os.environ['SPARSE_BACKEND'] = 'spconv'
os.environ['ATTN_BACKEND'] = 'xformers'
os.environ['SPARSE_ATTN_BACKEND'] = 'xformers'
os.environ['SPCONV_ALGO'] = 'native'
os.environ.setdefault('PYTORCH_CUDA_ALLOC_CONF','expandable_segments:True')
sys.path.insert(0, str(RESEARCH / 'TRELLIS'))
sys.path.insert(0, str(RESEARCH / 'trellis_compat'))


def face_edge_component_summary(mesh, graph, np, *, face_limit=500_000):
    """Bound optional topology diagnostics for dense TRELLIS outputs.

    TRELLIS meshes can exceed two million faces. Trimesh's face-adjacency
    construction can then allocate substantially more memory than the mesh
    itself, even after both GLBs have already been exported. Component count
    is diagnostic only, so large meshes skip it while preserving the model
    output and recording why it was unavailable.
    """
    face_count=len(mesh.faces)
    if face_count>face_limit:
        return None,None,'skipped_face_count_limit'
    try:
        components=graph.connected_components(mesh.face_adjacency,
            nodes=np.arange(face_count),min_len=1)
    except MemoryError:
        return None,None,'skipped_memory_limit'
    return len(components),max(map(len,components))/face_count,'measured'


def vertex_colors_rgba(vertex_attrs, vertex_count, np):
    """Convert TRELLIS mesh RGB attributes to glTF-ready vertex colors."""
    if vertex_attrs is None:
        raise RuntimeError('TRELLIS.1 mesh decoder returned no appearance attributes; refusing to export a white mesh')
    attrs=np.asarray(vertex_attrs)
    if attrs.ndim!=2 or attrs.shape[0]!=vertex_count or attrs.shape[1]<3:
        raise RuntimeError(f'Invalid TRELLIS.1 mesh appearance attributes: {attrs.shape}, expected ({vertex_count}, >=3)')
    rgb=attrs[:,:3].astype(np.float32,copy=False)
    if not np.isfinite(rgb).all():
        raise RuntimeError('TRELLIS.1 mesh appearance attributes contain non-finite RGB values')
    if rgb.min() < -1e-3 or rgb.max() > 1.001:
        raise RuntimeError(f'TRELLIS.1 mesh RGB attributes are outside [0,1]: min={rgb.min()}, max={rgb.max()}')
    rgba=np.empty((vertex_count,4),dtype=np.uint8)
    rgba[:,:3]=np.rint(np.clip(rgb,0,1)*255).astype(np.uint8)
    rgba[:,3]=255
    if np.unique(rgba[:,:3],axis=0).shape[0]<2:
        raise RuntimeError('TRELLIS.1 predicted a constant vertex color; refusing to bake a misleading texture')
    return rgba


def inspect_textured_glb(path, trimesh):
    """Require an embedded base-color image and UVs in the exported GLB."""
    scene=trimesh.load(path,force='scene')
    textured=[]
    for geometry in scene.geometry.values():
        visual=getattr(geometry,'visual',None)
        uv=getattr(visual,'uv',None)
        material=getattr(visual,'material',None)
        image=getattr(material,'baseColorTexture',None)
        if uv is None or image is None:
            continue
        size=getattr(image,'size',None)
        if not size or min(size)<2:
            continue
        extrema=image.convert('RGB').getextrema()
        rgb_spans=[int(bounds[1])-int(bounds[0]) for bounds in extrema]
        if max(rgb_spans,default=0)<8:
            continue
        textured.append({'uv_coordinates':int(len(uv)),'texture_size':[int(size[0]),int(size[1])],
                         'rgb_channel_spans':rgb_spans})
    if not textured:
        raise RuntimeError(f'Exported GLB has no verifiable UV base-color texture: {path}')
    return {'mesh_objects':len(scene.geometry),'textured_meshes':len(textured),
            'base_color_textures':[entry['texture_size'] for entry in textured],
            'uv_coordinates':sum(entry['uv_coordinates'] for entry in textured),
            'rgb_channel_spans':[entry['rgb_channel_spans'] for entry in textured]}


def downsample_sparse_mesh_features(sparse, source_resolution, target_resolution, torch, sparse_module):
    """Average-pool sparse cube features before the dense FlexiCubes extraction."""
    if target_resolution == source_resolution:
        return sparse
    if not 1 <= target_resolution < source_resolution:
        raise ValueError(f'Invalid mesh resolution {target_resolution}; expected 1..{source_resolution}')
    coords=sparse.coords
    features=sparse.feats
    if coords.ndim!=2 or coords.shape[1]!=4:
        raise RuntimeError(f'Expected [batch,x,y,z] sparse coordinates, got {tuple(coords.shape)}')
    if coords.numel() and (coords[:,1:].min()<0 or coords[:,1:].max()>=source_resolution):
        raise RuntimeError('TRELLIS mesh feature coordinates fall outside the source decoder grid')
    reduced=coords.clone()
    reduced[:,1:]=torch.div(coords[:,1:].to(torch.float32)*target_resolution,
                             source_resolution,rounding_mode='floor').to(coords.dtype)
    unique,inverse=torch.unique(reduced,dim=0,sorted=True,return_inverse=True)
    pooled=torch.zeros((len(unique),features.shape[1]),device=features.device,dtype=torch.float32)
    pooled.index_add_(0,inverse,features.float())
    counts=torch.bincount(inverse,minlength=len(unique)).to(device=features.device,dtype=torch.float32).unsqueeze(1)
    pooled.div_(counts.clamp_min_(1))
    return sparse_module.SparseTensor(feats=pooled.to(features.dtype),coords=unique)


def configure_mesh_resolution(decoder, target_resolution, torch):
    """Rebuild TRELLIS.1 dense FlexiCubes buffers at the requested resolution."""
    extractor=decoder.mesh_extractor
    source_resolution=extractor.res
    if target_resolution==source_resolution:
        return source_resolution
    if not 64<=target_resolution<source_resolution:
        raise ValueError(f'Mesh resolution must be in [64,{source_resolution}], got {target_resolution}')
    del extractor.reg_c
    del extractor.reg_v
    gc.collect()
    torch.cuda.empty_cache()
    from trellis.representations.mesh.utils_cube import construct_dense_grid
    reg_v,reg_c=construct_dense_grid(target_resolution,device='cuda')
    extractor.res=target_resolution
    extractor.sdf_bias=-1.0/target_resolution
    extractor.reg_v=reg_v
    extractor.reg_c=reg_c
    return target_resolution


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--image', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--seed', type=int, default=42)
    p.add_argument('--component-id', default='component')
    p.add_argument('--steps', type=int, default=25)
    p.add_argument('--texture-size',type=int,default=1024)
    p.add_argument('--mesh-resolution',type=int,default=160,
                   help='FlexiCubes grid resolution; sparse features are pooled from TRELLIS.1 256^3 output')
    p.add_argument('--material-hint',action='append',default=[],
                   help='Planner material requirement forwarded to the base-color baker')
    p.add_argument('--blender',type=Path,default=Path(fenv.BLENDER))
    args = p.parse_args()
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,63}',args.component_id):
        p.error('--component-id must be a stable alphanumeric identifier')
    if args.texture_size<256 or args.texture_size>4096:
        p.error('--texture-size must be between 256 and 4096')
    if args.mesh_resolution<64 or args.mesh_resolution>256:
        p.error('--mesh-resolution must be between 64 and 256')
    args.blender=args.blender.resolve()
    if not args.blender.is_file():
        p.error(f'Blender executable not found: {args.blender}')
    args.output.mkdir(parents=True, exist_ok=False)
    import numpy as np
    import torch
    import trimesh
    from PIL import Image
    from trellis.pipelines import TrellisImageTo3DPipeline

    start = time.perf_counter()
    torch.cuda.reset_peak_memory_stats()
    print('Loading local TRELLIS.1 weights', flush=True)
    pipeline = TrellisImageTo3DPipeline.from_pretrained('microsoft/TRELLIS-image-large')
    # The texture bake in this local route uses predicted mesh RGB attributes.
    # Gaussian and radiance-field decoders are therefore not needed.
    for unused_decoder in ('slat_decoder_gs','slat_decoder_rf'):
        pipeline.models.pop(unused_decoder,None)
    pipeline.cuda()
    im = Image.open(args.image).convert('RGBA')
    im=pipeline.preprocess_image(im)
    im.save(args.output / 'preprocessed_input.png')
    print('Generating with TRELLIS.1 structure sampler and appearance-aware mesh decoder', flush=True)
    # Run sampling explicitly so sampler/conditioning weights can be moved off
    # GPU before the high-resolution mesh decoder allocates its working set.
    condition=pipeline.get_cond([im])
    torch.manual_seed(args.seed)
    coords=pipeline.sample_sparse_structure(condition,1,{'steps':args.steps})
    slat=pipeline.sample_slat(condition,coords,{'steps':args.steps})
    del condition,coords
    for unused_name in list(pipeline.models):
        if unused_name=='slat_decoder_mesh':
            continue
        unused_model=pipeline.models.pop(unused_name)
        unused_model.cpu()
        del unused_model
    gc.collect()
    torch.cuda.empty_cache()
    mesh_decoder=pipeline.models['slat_decoder_mesh']
    source_mesh_resolution=mesh_decoder.mesh_extractor.res
    actual_mesh_resolution=configure_mesh_resolution(mesh_decoder,args.mesh_resolution,torch)
    if actual_mesh_resolution!=source_mesh_resolution:
        original_to_representation=mesh_decoder.to_representation
        sparse_module=__import__('trellis.modules.sparse',fromlist=['SparseTensor'])
        mesh_decoder.to_representation=lambda value: original_to_representation(
            downsample_sparse_mesh_features(value,source_mesh_resolution,actual_mesh_resolution,
                                            torch,sparse_module))
    print(f'Decoding mesh on {actual_mesh_resolution}^3 grid (TRELLIS.1 native grid: {source_mesh_resolution}^3)',flush=True)
    outputs=pipeline.decode_slat(slat,formats=['mesh'])
    del slat
    mesh = outputs['mesh'][0]
    vertices=mesh.vertices.detach().cpu().numpy()
    faces=mesh.faces.detach().cpu().numpy()
    attrs=None if mesh.vertex_attrs is None else mesh.vertex_attrs.detach().float().cpu().numpy()
    colors=vertex_colors_rgba(attrs,len(vertices),np)
    raw = trimesh.Trimesh(vertices=vertices,faces=faces,
                          visual=trimesh.visual.ColorVisuals(vertex_colors=colors),process=False)
    if not np.isfinite(raw.vertices).all() or len(raw.faces) == 0:
        raise RuntimeError('Non-finite or empty generated mesh')
    path = args.output / f'{args.component_id}_trellis1_raw.glb'
    raw.export(str(path))
    # TRELLIS mesh coordinates use Z up; glTF requires Y up.
    exchange = raw.copy()
    exchange.apply_transform(trimesh.transformations.rotation_matrix(-np.pi / 2, [1, 0, 0]))
    exchange_path = args.output / f'{args.component_id}_trellis1.glb'
    color_input_path=args.output/f'{args.component_id}_trellis1_vertex_color_input.glb'
    exchange.export(str(color_input_path))
    texture_path=args.output/f'{args.component_id}_base_color.png'
    bake_script=Path(__file__).with_name('bake_trellis1_vertex_color_texture.py')
    subprocess.run([str(args.blender),'-b','--python-exit-code','1','--python',str(bake_script),'--',
        '--input',str(color_input_path),'--output',str(exchange_path),
        '--texture',str(texture_path),'--size',str(args.texture_size),
        *sum((['--material-hint',hint] for hint in args.material_hint),[])],check=True)
    texture_evidence=inspect_textured_glb(exchange_path,trimesh)
    bake_report_path=exchange_path.with_suffix('.texture_bake.json')
    bake_report=json.loads(bake_report_path.read_text(encoding='utf-8')) if bake_report_path.is_file() else {}
    component_count,largest_component_fraction,component_analysis=face_edge_component_summary(raw,trimesh.graph,np)
    report = {
        'component_id': args.component_id,
        'generator': 'microsoft/TRELLIS-image-large', 'version': 'TRELLIS.1',
        'source_image': str(args.image.resolve()),
        'source_sha256': hashlib.sha256(args.image.read_bytes()).hexdigest(),
        'mesh_path': str(path.resolve()), 'seed': args.seed, 'steps': args.steps,
        'mesh_decode_resolution':actual_mesh_resolution,
        'mesh_decode_native_resolution':source_mesh_resolution,
        'mesh_decode_method':'sparse cube features average-pooled before FlexiCubes extraction' if actual_mesh_resolution!=source_mesh_resolution else 'native TRELLIS.1 resolution',
        'raw_coordinate_system': 'Z up',
        'exchange_mesh_path': str(exchange_path.resolve()),
        'exchange_coordinate_system': 'glTF Y up',
        'vertex_color_input_path':str(color_input_path.resolve()),
        'texture_map_path':str(texture_path.resolve()),
        'texture_map_sha256':hashlib.sha256(texture_path.read_bytes()).hexdigest(),
        'texture_bake_report_path':str(bake_report_path.resolve()),
        'texture_evidence':texture_evidence,
        'appearance_export':{'method':'TRELLIS.1 predicted mesh vertex colors baked to a UV base-color map in Blender Cycles',
            'source':'mesh.vertex_attrs RGB','pbr_maps':['base_color'],
            'material_hints':args.material_hint,
            'material_profile':bake_report.get('material_profile'),'roughness_factor':bake_report.get('roughness_factor'),
            'metallic_factor':bake_report.get('metallic_factor'),
            'limitations':'This is not the official Gaussian multi-view texture optimization; material profile variation is baked into base_color and no learned metallic/roughness maps are generated.'},
        'exchange_transform': 'Rigid rotation -90 degrees around X; no geometry cleanup, scaling or manual remodeling; UV color bake only',
        'image_cond_model': pipeline._pretrained_args['image_cond_model'],
        'vertices': len(raw.vertices), 'faces': len(raw.faces),
        'bounds': raw.bounds.tolist(), 'face_edge_components': component_count,
        'face_edge_component_analysis':component_analysis,
        'largest_component_face_fraction':largest_component_fraction,
        'unreferenced_vertices': len(raw.vertices) - len(np.unique(raw.faces)),
        'watertight': bool(raw.is_watertight),
        'export_mode': 'uv_base_color_glb', 'pbr': True, 'pbr_maps':['base_color'],
        'vertex_color_rgb_min':float(attrs[:,:3].min()),
        'vertex_color_rgb_max':float(attrs[:,:3].max()),
        'accepted': False, 'status': 'pending_visual_qa',
        'elapsed_seconds': round(time.perf_counter() - start, 2),
        'peak_gpu_memory_gib': round(torch.cuda.max_memory_allocated() / 1024**3, 2),
        'gpu': torch.cuda.get_device_name(0),
    }
    (args.output / 'component_contract.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2), flush=True)


if __name__ == '__main__':
    main()
