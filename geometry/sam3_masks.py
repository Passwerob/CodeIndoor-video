"""Text-prompted SAM3 masks for every keyframe (run in the vggrpo env).

Output: geometry/sam3_masks.npz  -- per detection: frame index, prompt, score,
box (frame px), mask packed at half frame resolution.
"""
import json, sys, time
from pathlib import Path
import numpy as np, torch
from PIL import Image
import local_paths  # noqa: E402  (SAM3_CHECKPOINT from env / runtime/.env)
from sam3.model_builder import build_sam3_image_model
from sam3.model.sam3_image_processor import Sam3Processor

run = Path(sys.argv[1])
prompts = json.loads((run / "geometry/prompts.json").read_text())
phrases = sorted({p for ps in prompts.values() for p in ps})
frames = json.loads((run / "frames/frames.json").read_text())["frames"]
grounding = json.loads((run / "grounding.json").read_text())["grounding"]["objects"]
model = build_sam3_image_model(checkpoint_path=local_paths.SAM3_CHECKPOINT,
                               load_from_HF=False)
proc = Sam3Processor(model, confidence_threshold=0.35)
torch.autocast("cuda", dtype=torch.bfloat16).__enter__()
rec = {"frame": [], "phrase": [], "score": [], "box": [], "mask": []}
t = time.time()
for fi, f in enumerate(frames):
    im = Image.open(run / "frames" / f["file"]).convert("RGB")
    W, H = im.size
    st = proc.set_image(im)
    for ph in phrases:
        proc.reset_all_prompts(st)
        o = proc.set_text_prompt(ph, st)
        for s, b, m in zip(o["scores"], o["boxes"], o["masks"]):
            mm = m.squeeze().float().cpu().numpy() > 0.5
            half = np.asarray(Image.fromarray(mm).resize((W // 2, H // 2), Image.NEAREST))
            rec["frame"].append(fi); rec["phrase"].append(ph); rec["score"].append(float(s))
            rec["box"].append(b.float().cpu().numpy()); rec["mask"].append(np.packbits(half))
    # box prompts from the VLM grounding: one instance mask per grounded (object, frame)
    for oid, entries in grounding.items():
        for g in entries:
            if g["frame"] != f["id"]:
                continue
            x0, y0, x1, y1 = [v / 1000 for v in g["bbox"]]
            proc.reset_all_prompts(st)
            o = proc.add_geometric_prompt([(x0 + x1) / 2, (y0 + y1) / 2, x1 - x0, y1 - y0], True, st)
            if len(o["scores"]) == 0:
                continue
            j = int(torch.argmax(o["scores"]))
            mm = o["masks"][j].squeeze().float().cpu().numpy() > 0.5
            half = np.asarray(Image.fromarray(mm).resize((W // 2, H // 2), Image.NEAREST))
            rec["frame"].append(fi); rec["phrase"].append("box:" + oid); rec["score"].append(float(o["scores"][j]))
            rec["box"].append(o["boxes"][j].float().cpu().numpy()); rec["mask"].append(np.packbits(half))
    if fi % 10 == 0:
        print(fi, f["id"], len(rec["frame"]), round(time.time() - t, 1), flush=True)
np.savez_compressed(run / "geometry/sam3_masks.npz", frame=np.array(rec["frame"]), phrase=np.array(rec["phrase"]),
                    score=np.array(rec["score"]), box=np.stack(rec["box"]), mask=np.stack(rec["mask"]),
                    half_hw=np.array([H // 2, W // 2]))
print("detections", len(rec["frame"]))
