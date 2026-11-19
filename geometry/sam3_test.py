import sys, time, numpy as np, torch
from PIL import Image
import local_paths  # noqa: E402  (SAM3_CHECKPOINT from env / runtime/.env)
from sam3.model_builder import build_sam3_image_model
from sam3.model.sam3_image_processor import Sam3Processor
m = build_sam3_image_model(checkpoint_path=local_paths.SAM3_CHECKPOINT, load_from_HF=False)
proc = Sam3Processor(m, confidence_threshold=0.25)
torch.autocast("cuda", dtype=torch.bfloat16).__enter__()
run="runtime/video/pantry/frames/"
for f in ["F00","F46","F51","F09"]:
    im = Image.open(run+f+".jpg").convert("RGB")
    st = proc.set_image(im)
    for p in ["water dispenser","water cooler","water purifier","appliance","white cabinet","floor sign","yellow sticker","step warning strip"]:
        proc.reset_all_prompts(st); o = proc.set_text_prompt(p, st)
        print(f, p, [round(float(s),2) for s in o["scores"]], [tuple(int(x) for x in b) for b in o["boxes"].float().cpu().numpy()][:3])
    # box prompt from grounding
    proc.reset_all_prompts(st)
