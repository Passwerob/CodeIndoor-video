"""Metric multi-view reconstruction of the keyframes with Any4D (run in the
`vggrpo` env, cwd = the Any4D repo). Writes one npz with, per view: world
points (frame of view 0), camera-to-world pose, intrinsics, depth, valid mask.

    cd $ANY4D_ROOT && CUDA_VISIBLE_DEVICES=0 $TEX_PY <repo>/geometry/any4d_reconstruct.py RUN_DIR
"""
import json
import os
import sys
import time
from pathlib import Path

import numpy as np
import torch

sys.path.insert(0, os.getcwd())
sys.path.insert(0, os.path.join(os.getcwd(), "scripts"))
from demo_inference import init_inference_model, sample_inference  # noqa: E402
from any4d.utils.image import load_images  # noqa: E402
from any4d.utils.geometry import quaternion_to_rotation_matrix, recover_pinhole_intrinsics_from_ray_directions  # noqa: E402

run = Path(sys.argv[1])
frames = json.loads((run / "frames/frames.json").read_text())["frames"]
paths = [str(run / "frames" / f["file"]) for f in frames]
cfg = {"path": "configs/train.yaml",
       "config_overrides": ["machine=local", "model=any4d", "model.encoder.uses_torch_hub=false",
                            "model/task=images_only"],
       "checkpoint_path": "checkpoints/any4d_4v_combined.pth", "trained_with_amp": True,
       "data_norm_type": "dinov2"}
dev = torch.device("cuda")
t = time.time()
model = init_inference_model(cfg, cfg["checkpoint_path"], dev)
views = load_images(paths, norm_type="dinov2", patch_size=14, verbose=False)
print("views", len(views), tuple(views[0]["img"].shape), "load", round(time.time() - t, 1), flush=True)
t = time.time()
out = sample_inference(model, views, dev, use_amp=True)
print("inference", round(time.time() - t, 1), "s; mem GB", round(torch.cuda.max_memory_allocated() / 1e9, 1), flush=True)
rec = {"ids": np.array([f["id"] for f in frames])}
pts, poses, Ks, depths, masks, imgs = [], [], [], [], [], []
for i in range(len(views)):
    p = out[f"pred{i + 1}"]
    pts.append(p["pts3d"][0].float().cpu().numpy())
    R = quaternion_to_rotation_matrix(p["cam_quats"][0].float().cpu())
    T = np.eye(4, dtype=np.float32)
    T[:3, :3] = R.numpy() if hasattr(R, "numpy") else R
    T[:3, 3] = p["cam_trans"][0].float().cpu().numpy()
    poses.append(T)
    rd = p["ray_directions"][0].float().cpu()
    Ks.append(recover_pinhole_intrinsics_from_ray_directions(rd).numpy())
    depths.append((rd * p["depth_along_ray"][0].float().cpu())[..., 2].numpy())
    masks.append(views[i]["non_ambiguous_mask"].cpu().numpy() if "non_ambiguous_mask" in views[i] else
                 np.ones(depths[-1].shape, bool))
    img = views[i]["img"][0].permute(1, 2, 0).cpu().numpy()
    imgs.append(img)
np.savez_compressed(run / "geometry/any4d.npz", ids=rec["ids"], pts=np.stack(pts), poses=np.stack(poses),
                    K=np.stack(Ks), depth=np.stack(depths), mask=np.stack(masks), img=np.stack(imgs))
print("saved", run / "geometry/any4d.npz", np.stack(pts).shape)
