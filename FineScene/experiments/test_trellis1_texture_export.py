"""Regression checks for TRELLIS.1 appearance retention and textured GLB export."""
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import trimesh
import torch
from PIL import Image

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from run_trellis1_component import (downsample_sparse_mesh_features,
    inspect_textured_glb, vertex_colors_rgba)


class SparseStub:
    def __init__(self,feats,coords):
        self.feats=feats
        self.coords=coords


class SparseModuleStub:
    SparseTensor=SparseStub


class Trellis1TextureExportTests(unittest.TestCase):
    def test_vertex_rgb_is_preserved_as_gltf_color_and_alpha(self):
        attrs=np.array([[0.0,.25,1.0,.1,.2,.3],[1.0,.5,0.0,.2,.3,.4]],dtype=np.float32)
        rgba=vertex_colors_rgba(attrs,2,np)
        np.testing.assert_array_equal(rgba,[[0,64,255,255],[255,128,0,255]])

    def test_missing_or_invalid_appearance_attributes_are_rejected(self):
        with self.assertRaisesRegex(RuntimeError,'no appearance attributes'):
            vertex_colors_rgba(None,3,np)
        with self.assertRaisesRegex(RuntimeError,'outside \[0,1\]'):
            vertex_colors_rgba(np.array([[0,0,0],[1,2,0]],dtype=np.float32),2,np)
        with self.assertRaisesRegex(RuntimeError,'constant vertex color'):
            vertex_colors_rgba(np.ones((3,3),dtype=np.float32),3,np)

    def test_geometry_only_glb_cannot_pass_texture_acceptance(self):
        mesh=trimesh.creation.box()
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'white.glb'
            mesh.export(path)
            with self.assertRaisesRegex(RuntimeError,'no verifiable UV base-color texture'):
                inspect_textured_glb(path,trimesh)

    def test_uv_base_color_glb_passes_texture_acceptance(self):
        rgba=np.array([[220,30,25,255],[30,210,45,255],[35,40,220,255],[220,190,25,255]],dtype=np.uint8)
        texture=Image.fromarray(np.array([[[120,60,30],[210,110,60]],
                                          [[35,110,220],[220,190,45]]],dtype=np.uint8))
        mesh=trimesh.Trimesh(vertices=[[0,0,0],[1,0,0],[1,1,0],[0,1,0]],
            faces=[[0,1,2],[0,2,3]],
            visual=trimesh.visual.TextureVisuals(uv=[[0,0],[1,0],[1,1],[0,1]],
                material=trimesh.visual.material.PBRMaterial(
                    baseColorTexture=texture)))
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'textured.glb'
            mesh.export(path)
            evidence=inspect_textured_glb(path,trimesh)
        self.assertEqual(evidence['textured_meshes'],1)
        self.assertEqual(evidence['base_color_textures'],[[2,2]])
        self.assertEqual(evidence['uv_coordinates'],4)

    def test_sparse_mesh_features_pool_without_losing_appearance_channels(self):
        coords=torch.tensor([[0,0,0,0],[0,1,1,1],[0,3,3,3]],dtype=torch.int32)
        feats=torch.tensor([[0.,0.,0.,1.],[2.,4.,6.,3.],[8.,8.,8.,5.]],dtype=torch.float16)
        reduced=downsample_sparse_mesh_features(SparseStub(feats,coords),4,2,torch,SparseModuleStub)
        self.assertEqual(reduced.feats.dtype,torch.float16)
        self.assertEqual(reduced.coords.tolist(),[[0,0,0,0],[0,1,1,1]])
        torch.testing.assert_close(reduced.feats.float(),torch.tensor([[1.,2.,3.,2.],[8.,8.,8.,5.]]))

    def test_sparse_mesh_feature_pool_rejects_out_of_grid_coordinates(self):
        coords=torch.tensor([[0,0,0,4]],dtype=torch.int32)
        with self.assertRaisesRegex(RuntimeError,'outside the source decoder grid'):
            downsample_sparse_mesh_features(SparseStub(torch.zeros((1,3)),coords),4,2,torch,SparseModuleStub)


if __name__=='__main__':unittest.main()
