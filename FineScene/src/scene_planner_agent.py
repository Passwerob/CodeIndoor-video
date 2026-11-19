"""LLM-backed scene planning, kept separate from deterministic geometry builders."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import finescene_env as fenv
from typing import Any, Literal

from pydantic import BaseModel, Field


class RoomPlan(BaseModel):
    width: float = Field(gt=3.0, le=12.0)
    depth: float = Field(gt=3.0, le=12.0)
    height: float = Field(gt=2.2, le=5.0)
    window_wall: Literal["back"] = "back"
    door_wall: Literal["front"] = "front"
    window_width: float = Field(default=1.4, gt=0.5, le=3.0)
    window_height: float = Field(default=1.1, gt=0.5, le=2.0)
    window_sill: float = Field(default=0.9, ge=0.3, le=1.5)
    door_width: float = Field(default=0.9, gt=0.6, le=1.4)
    door_height: float = Field(default=2.1, gt=1.8, le=2.5)


class FurnitureTask(BaseModel):
    component_id: str
    kind: Literal["bookshelf", "desk", "chair"]
    zone: str
    role: Literal["desk", "reading"] | None = None
    placement: tuple[float, float, float]
    rotation_deg: float = 0.0
    wall_attachment: Literal["left", "right", "back", "front"] | None = None
    parameters: dict[str, float] = Field(default_factory=dict)
    refined_blend: str | None = None


class DecorTask(BaseModel):
    component_id: str
    kind: Literal["rug", "lamp", "book_stack", "plant", "side_table"]
    zone: str
    placement: tuple[float, float, float]
    size: tuple[float, float, float]
    color: tuple[float, float, float] = (0.22, 0.12, 0.06)
    parent_id: str | None = None


class ScenePlan(BaseModel):
    scene_id: str
    style: str
    room: RoomPlan
    zones: list[str]
    furniture_tasks: list[FurnitureTask]
    decor_tasks: list[DecorTask] = Field(default_factory=list)
    planning_assumptions: list[str] = Field(default_factory=list)


_INSTRUCTIONS = """You are the Scene Planner Agent in FineScene, a code-first 3D scene system.
Plan a complete, plausible study room from the user brief. You plan semantics and spatial layout; deterministic aDSL builders create geometry.
Return a full room shell plan (dimensions, a window and a door), functional zones, at least one bookshelf, one desk, and a desk chair; add a separate reading chair only when the room allows it. Set chair role to desk for writing/work seating and reading for an armchair. Keep those roles distinct: the desk chair must be a compact, upright task chair, while a reading chair can use a lounge or refined asset. Respect circulation: keep a clear path from the door, place storage along a wall, put the desk near useful light, and place its chair directly in front of it facing the work surface. Room coordinates are centered in X and Y, floor at Z=0, front is -Y, back is +Y, up is +Z. Furniture placement is its local origin at floor level; do not put furniture outside room bounds. Furniture kinds are bookshelf, desk, chair. For bookshelves, width is the long front-facing span and depth is the back-to-front dimension, regardless of which room wall they touch. Decor kinds are rug, lamp, book_stack, plant, side_table. Put tabletop decor on its supporting surface and avoid stacking multiple objects at the exact same location. Use realistic meter dimensions and room-specific positions. For objects against a wall, set wall_attachment to the corresponding side; assembly aligns the rotated component bounds and points the object's front into the room. The preview is a cutaway with the front wall and ceiling panel omitted so interior contents remain visible; the door frame, side walls, back wall, floor, ceiling beams, and window remain. Only set a refined chair path when a specific refined chair asset is intended; otherwise null. Avoid arbitrary tiny clutter and ensure all choices serve the requested room."""


class LLMScenePlanner:
    def __init__(self, model_profile: str | Path, workspace: Path, task_id: str) -> None:
        self.model_profile = Path(model_profile)
        self.workspace = workspace
        self.task_id = task_id
        self.repair_feedback: dict[str, Any] | None = None
        self.initial_plan: Path | None = None

    def plan(self, brief: str) -> dict[str, Any]:
        if self.initial_plan is not None and self.repair_feedback is None:
            return ScenePlan.model_validate_json(self.initial_plan.read_text(encoding="utf-8")).model_dump(mode="json")
        agents_root = fenv.ADSL_ROOT / "adsl-agents"
        sys.path.insert(0, str(agents_root))
        from utils.runner import AgentRuntime
        from agents import AgentOutputSchema

        runtime = AgentRuntime(
            model_profile=self.model_profile,
            workspace=self.workspace,
            task_id=self.task_id,
        )
        agent = runtime.agent(
            name="finescene-scene-planner",
            instructions=_INSTRUCTIONS,
            output_type=AgentOutputSchema(ScenePlan, strict_json_schema=False),
        )
        prompt = json.dumps({"brief": brief, "required_output": ScenePlan.model_json_schema(),
                             "repair_feedback": self.repair_feedback}, ensure_ascii=False)
        async def streamed_plan():
            from agents import Runner
            result = Runner.run_streamed(agent, prompt, max_turns=4, session=runtime.sessions.for_role("planner"))
            async for event in result.stream_events():
                pass
            runtime.usage.record(stage="scene_plan", agent=agent.name, result=result)
            return result
        try:
            result = asyncio.run(streamed_plan())
        except Exception as error:
            raise RuntimeError(f"Scene Planner unavailable ({type(error).__name__}); no candidate was accepted") from None
        plan = result.final_output
        if not isinstance(plan, ScenePlan):
            plan = ScenePlan.model_validate(plan)
        return plan.model_dump(mode="json")


def compile_scene_plan(plan: dict[str, Any], brief: str) -> dict[str, Any]:
    """Turn the agent's semantic plan into a validated build contract."""
    from agent_system import _component

    for task in plan["furniture_tasks"]:
        params = task.setdefault("parameters", {})
        if task["kind"] == "bookshelf" and "levels" not in params:
            params["levels"] = params.get("shelves", params.get("shelf_count", 4))
    room = plan["room"]
    width, depth, height = room["width"], room["depth"], room["height"]
    palette_wall = (0.075, 0.16, 0.12) if "green" in plan["style"].lower() else (0.42, 0.30, 0.20)
    components = [
        _component("room", "structure", None, (width, depth, height), (0, 0, height / 2), editable=("room_width", "room_depth", "room_height")),
        _component("room_shell", "structure", "room", (width, depth, height), (0, 0, height / 2)),
    ]
    for task in plan["furniture_tasks"]:
        kind = task["kind"]
        defaults = {"bookshelf": (1.25, 0.42, 1.9), "desk": (1.5, 0.72, 0.76), "chair": (0.58, 0.58, 0.98)}
        parameters = task.get("parameters", {})
        size = (
            float(parameters.get("width", defaults[kind][0])),
            float(parameters.get("depth", defaults[kind][1])),
            float(parameters.get("height", defaults[kind][2])),
        )
        attachment = task.get("wall_attachment")
        editable = ("shelf_clearance",) if kind == "bookshelf" else ()
        item = _component(task["component_id"], "structure", "room", size, tuple(task["placement"]), "retrieved" if task.get("refined_blend") else "adsl", editable=editable)
        item["transform"]["rotation_deg"][2] = float(task.get("rotation_deg", 0))
        item["wall_attachment"] = attachment
        if kind == "chair":
            role = task.get("role")
            if role is None:
                identity = f"{task.get('zone', '')} {task['component_id']}".lower()
                role = "reading" if any(token in identity for token in ("reading", "lounge", "armchair")) else "desk"
            item["role"] = role
            task["role"] = role
        components.append(item)
    for task in plan.get("decor_tasks", []):
        components.append(_component(task["component_id"], "detail", task.get("parent_id") or "room", tuple(task["size"]), tuple(task["placement"])))
    relations = [{"type": "parent", "from": c["id"], "to": c["parent"]} for c in components if c["parent"]]
    relations.extend({"type": "adjacent_to", "from": task["component_id"], "to": "room_shell", "wall": task["wall_attachment"]} for task in plan["furniture_tasks"] if task.get("wall_attachment"))
    relations.extend({"type": "depends_on", "from": c["id"], "to": "room"} for c in components if c["id"] not in {"room", "room_shell"})
    parameters = {"room_width": width, "room_depth": depth, "room_height": height}
    bookshelves = [task for task in plan["furniture_tasks"] if task["kind"] == "bookshelf"]
    if bookshelves:
        first = bookshelves[0].get("parameters", {})
        levels = int(first.get("levels", first.get("shelves", 4)))
        thickness = float(first.get("shelf_thickness", 0.035))
        if first.get("shelf_clearance") is not None:
            clearance = float(first["shelf_clearance"])
        elif first.get("height") is not None:
            clearance = (float(first["height"]) - 0.18 - (levels - 1) * thickness) / levels
        else:
            clearance = 0.5
        parameters["shelf_clearance"] = clearance
        # Shelf spacing is a scene-level editable parameter. Normalize the
        # initial contract so each bookcase shares one structural rule; a user
        # edit then propagates through every frame, board, and book.
        for task in bookshelves:
            task.setdefault("parameters", {})["shelf_clearance"] = clearance
    return {
        "schema": "finescene.scene.v1",
        "scene_id": plan["scene_id"],
        "brief": brief,
        "units": "m",
        "coordinate_frame": {"up": "+Z", "front": "-Y", "right": "+X"},
        "parameters": parameters,
        "room": room,
        "style": plan["style"],
        "zones": plan["zones"],
        "architecture_tasks": [{"component_id": "room_shell", "kind": "room_shell", "parameters": {**room, "wall_color": list(palette_wall)}}],
        "furniture_tasks": plan["furniture_tasks"],
        "decor_tasks": plan.get("decor_tasks", []),
        "components": components,
        "relations": relations,
        "edit_operations": [],
        "planning_backend": "llm_agent",
        "planning_assumptions": plan.get("planning_assumptions", []),
    }


__all__ = ["LLMScenePlanner", "ScenePlan", "compile_scene_plan"]
