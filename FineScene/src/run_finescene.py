"""Run the FineScene offline agent pipeline."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import finescene_env as fenv

from agent_system import FineSceneAgentSystem, TemplateStudyPlanner
from scene_planner_agent import LLMScenePlanner


def main() -> None:
    parser = argparse.ArgumentParser(description="Plan and validate a FineScene room scene")
    parser.add_argument("--brief", required=True, help="Scene request in natural language")
    parser.add_argument("--out", type=Path, required=True, help="New output directory")
    parser.add_argument("--planner", choices=("llm", "offline"), default="llm", help="Use the actual Scene Planner Agent, or explicitly select the deterministic offline fallback")
    parser.add_argument("--model-profile", type=Path, default=fenv.LLM_CONFIGS / "deepseek-chat.yaml")
    parser.add_argument("--edit-parameter")
    parser.add_argument("--vision-profile", type=Path, default=fenv.LLM_CONFIGS / "gpt6astra.yaml")
    parser.add_argument("--plan", type=Path, help="Replay a saved LLM plan first; rejected layouts are revised by the LLM")
    parser.add_argument("--edit-delta", type=float)
    args = parser.parse_args()
    if bool(args.edit_parameter) != (args.edit_delta is not None):
        parser.error("--edit-parameter and --edit-delta must be provided together")
    edit = None
    if args.edit_parameter:
        edit = {
            "id": "user_edit_001",
            "op": "add",
            "parameter": args.edit_parameter,
            "delta": args.edit_delta,
            "expected_affected": [],
        }
    if args.planner == "llm":
        planner = LLMScenePlanner(args.model_profile, args.out / "agent_runtime", args.out.name)
        planner.initial_plan = args.plan
    else:
        if args.plan:
            parser.error("--plan requires --planner llm")
        planner = TemplateStudyPlanner()
    report = FineSceneAgentSystem(planner, args.vision_profile).run(args.brief, args.out, edit)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["status"] == "rejected":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
