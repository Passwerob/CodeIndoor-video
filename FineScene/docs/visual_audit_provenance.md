# Visual Acceptance Provenance Correction

2026-10-19: The passed/accepted result of the early `visual_judge_offline_20261019_03` came from an old file-size and center-ray heuristic, not an image-semantic Judge, so it cannot serve as evidence of visual quality passing. The old verdicts of `visual_judge_llm_20261019_01` and `_02` do not carry that meaning either. Historical outputs are kept unchanged for traceability.

The only current source of image-semantic acceptance is the real model request in `src/visual_judge.py`. The report backend must be `vision_language_model` and must include the image SHA-256, model, decision and usage. A service failure must yield accepted=false; no heuristic is used as a substitute.
