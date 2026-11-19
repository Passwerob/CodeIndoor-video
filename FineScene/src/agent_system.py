"""First runnable FineScene agent orchestrator (offline deterministic backend)."""
from __future__ import annotations

import copy
import json
import subprocess
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

import finescene_env as fenv
from typing import Any, Protocol

from adsl.core import Asset, rotate_shape, shape_aabb, translate_shape
from adsl_components import build_component
from scene_planner_agent import LLMScenePlanner, compile_scene_plan
from constraint_solver import judge_component_bounds, solve_spatial_constraints
from edit_judge import judge_edit
from asset_registry import resolve_local_assets
from scene_entities import expand_bookshelf_components, refresh_bookshelf_components
from validate_scene_contract import apply_operations, validate_scene
from visual_judge import judge_visual


class PlannerBackend(Protocol):
    def plan(self, brief: str) -> dict[str, Any]: ...


def _component(
    component_id: str,
    kind: str,
    parent: str | None,
    size: tuple[float, float, float],
    position: tuple[float, float, float],
    source_mode: str = "adsl",
    editable: tuple[str, ...] = (),
) -> dict[str, Any]:
    return {
        "id": component_id,
        "kind": kind,
        "parent": parent,
        "bounds": {"size": list(size), "unit": "m"},
        "transform": {
            "position": list(position),
            "rotation_deg": [0, 0, 0],
            "scale": [1, 1, 1],
        },
        "anchors": ["center", "bottom_center", "top"],
        "editable": list(editable),
        "style": "vintage_wood_study",
        "source": {
            "mode": source_mode,
            "path": None,
            "license": None,
            "generator": "FineScene deterministic planner",
        },
    }


class TemplateStudyPlanner:
    """Offline planner used until a multimodal LLM backend is configured."""

    def plan(self, brief: str) -> dict[str, Any]:
        brief = brief.strip()
        if not brief:
            raise ValueError("A scene brief is required")
        components = [
            _component("room", "structure", None, (5.0, 4.0, 2.8), (0, 0, 1.4), editable=("room_width", "room_depth", "room_height")),
            _component("floor", "structure", "room", (5.0, 4.0, 0.08), (0, 0, 0), editable=("room_width", "room_depth")),
            _component("back_wall", "structure", "room", (5.0, 0.12, 2.8), (0, 1.94, 1.4), editable=("room_width", "room_height")),
            _component("left_wall", "structure", "room", (0.12, 4.0, 2.8), (-2.44, 0, 1.4), editable=("room_depth", "room_height")),
            _component("right_wall", "structure", "room", (0.12, 4.0, 2.8), (2.44, 0, 1.4), editable=("room_depth", "room_height")),
            _component("bookshelf_01", "structure", "room", (1.25, 0.42, 1.9), (-1.7, 1.55, 0.95), editable=("shelf_clearance", "shelf_thickness")),
            _component("desk_01", "structure", "room", (1.5, 0.72, 0.76), (0.15, 1.0, 0.38), editable=("desk_width", "desk_height")),
            _component("chair_01", "prop", "room", (1.06, 0.925, 1.68), (0.15, 0.05, 0.84), "retrieved"),
            _component("desk_lamp_01", "prop", "desk_01", (0.22, 0.22, 0.42), (0.72, 0.95, 0.97)),
            _component("rug_01", "detail", "room", (1.8, 1.2, 0.025), (0.15, 0.15, 0.013)),
        ]
        relations: list[dict[str, Any]] = []
        for component in components:
            if component["parent"]:
                relations.append({"type": "parent", "from": component["id"], "to": component["parent"]})
        for item in ("floor", "back_wall", "left_wall", "right_wall", "bookshelf_01", "desk_01", "chair_01", "rug_01"):
            relations.append({"type": "depends_on", "from": item, "to": "room"})
        relations.extend([
            {"type": "contact", "from": "bookshelf_01", "to": "floor", "anchor_from": "bottom_center", "anchor_to": "top", "axis": "+Z", "value": 0},
            {"type": "contact", "from": "desk_01", "to": "floor", "anchor_from": "bottom_center", "anchor_to": "top", "axis": "+Z", "value": 0},
            {"type": "contact", "from": "desk_lamp_01", "to": "desk_01", "anchor_from": "bottom_center", "anchor_to": "top", "axis": "+Z", "value": 0},
            {"type": "clearance", "from": "chair_01", "to": "desk_01", "axis": "+Y", "value": 0.15},
        ])
        return {
            "schema": "finescene.scene.v1",
            "scene_id": "vintage_study_room_001",
            "brief": brief,
            "units": "m",
            "coordinate_frame": {"up": "+Z", "front": "-Y", "right": "+X"},
            "parameters": {
                "room_width": 5.0,
                "room_depth": 4.0,
                "room_height": 2.8,
                "shelf_clearance": 0.5,
                "shelf_thickness": 0.035,
                "desk_width": 1.5,
                "desk_height": 0.76,
            },
            "zones": ["storage", "writing", "reading", "circulation"],
            "furniture_tasks": [
                {"component_id": "bookshelf_01", "kind": "bookshelf", "zone": "storage", "placement": [-1.7, 1.55, 0.0], "parameters": {"shelf_clearance": 0.5}},
                {"component_id": "desk_01", "kind": "desk", "zone": "writing", "placement": [0.15, 1.0, 0.0], "parameters": {"width": 1.5, "depth": 0.72, "height": 0.76}},
                {"component_id": "chair_01", "kind": "chair", "role": "reading", "zone": "reading", "placement": [0.15, 0.05, 0.0], "parameters": {}, "refined_blend": str(fenv.OAKWARDEN_DIR / "review_02" / "medieval_armchair.blend")},
            ],
            "components": components,
            "relations": relations,
            "edit_operations": [],
            "parameter_dependencies": {},
            "parameter_propagation": {},
            "planning_backend": "template_offline",
            "planning_assumptions": [
                "Room dimensions and furniture placement are defaults inferred by the offline template planner.",
                "Retrieved component paths are unresolved until an asset catalog is connected.",
                "The current planner does not infer doors or windows from a reference image.",
            ],
        }


@dataclass
class StageResult:
    name: str
    status: str
    artifact: str | None = None
    details: dict[str, Any] | None = None


class FineSceneAgentSystem:
    """Orchestrates planning, validation, dependency analysis and artifact routing."""

    def __init__(self, planner: PlannerBackend | None = None, vision_profile: Path | None = None) -> None:
        self.planner = planner or TemplateStudyPlanner()
        self.vision_profile = vision_profile or fenv.LLM_CONFIGS / "gpt6astra.yaml"

    def run(self, brief: str, output_dir: Path, edit: dict[str, Any] | None = None) -> dict[str, Any]:
        root = output_dir
        attempts = []
        for attempt in range(3 if isinstance(self.planner, LLMScenePlanner) else 1):
            candidate_dir = root if attempt == 0 else root / f"repair_{attempt:02d}"
            if isinstance(self.planner, LLMScenePlanner):
                self.planner.workspace = candidate_dir / "agent_runtime"
            report = self._run_candidate(brief, candidate_dir, edit)
            attempts.append({"directory": str(candidate_dir), "status": report["status"]})
            report["output_directory"] = str(candidate_dir)
            if report["status"] != "rejected" or not isinstance(self.planner, LLMScenePlanner):
                break
            geometry = next((s for s in report["stages"] if s["name"] == "geometry_judge" and s["status"] != "passed"), None)
            visual = next((s for s in report["stages"] if s["name"] == "visual_judge" and s["status"] == "needs_revision"), None)
            visual_findings = (visual or {}).get("details", {}).get("decision", {}).get("findings", [])
            layout_findings = [f for f in visual_findings if f["severity"] == "blocking" and f["repair_target"] == "layout"]
            plan_path = candidate_dir / "planner_output.json"
            if not (geometry or layout_findings) or not plan_path.is_file():
                break
            self.planner.repair_feedback = {
                "instruction": "Revise the previous plan to fix ALL listed layout failures and collision candidates. Keep the requested objects, sizes and stable IDs; move objects instead of shrinking them. Room X/Y are CENTERED. Use actual rotated footprints, leave wall clearance, separate furniture. Do not pre-apply the requested edit; the system applies it. Return a full corrected plan.",
                "previous_plan": json.loads(plan_path.read_text(encoding="utf-8")),
                "geometry_judge": geometry["details"] if geometry else None,
                "visual_judge": layout_findings,
            }
        self._write_json(root / "pipeline_summary.json", {"attempts": attempts, "status": report["status"], "output_directory": report["output_directory"]})
        return report

    def _run_candidate(self, brief: str, output_dir: Path, edit: dict[str, Any] | None = None) -> dict[str, Any]:
        output_dir.mkdir(parents=True, exist_ok=False)
        snapshot = output_dir / "source_snapshot"
        snapshot.mkdir()
        for source in Path(__file__).parent.glob("*.py"):
            shutil.copy2(source, snapshot / source.name)
        self._stages: list[StageResult] = []
        try:
            return self._run(brief, output_dir, edit)
        except (ValueError, RuntimeError, subprocess.CalledProcessError) as error:
            report = {
                "schema": "finescene.run_report.v1",
                "status": "rejected",
                "error": str(error),
                "stages": [stage.__dict__ for stage in self._stages],
                "artifacts": [path.name for path in output_dir.iterdir()],
                "accepted": False,
            }
            self._write_json(output_dir / "run_report.json", report)
            return report

    def _run(self, brief: str, output_dir: Path, edit: dict[str, Any] | None = None) -> dict[str, Any]:
        stages = self._stages
        if isinstance(self.planner, LLMScenePlanner):
            raw_plan = self.planner.plan(brief)
            self._write_json(output_dir / "planner_output.json", raw_plan)
            contract = compile_scene_plan(raw_plan, brief)
        else:
            contract = self.planner.plan(brief)
            room = contract.get("room") or {
                "width": contract["parameters"].get("room_width", 5.0),
                "depth": contract["parameters"].get("room_depth", 4.0),
                "height": contract["parameters"].get("room_height", 2.8),
                "window_wall": "back", "door_wall": "front",
                "window_width": 1.4, "window_height": 1.1, "window_sill": 0.9,
                "door_width": 0.9, "door_height": 2.1,
            }
            contract["room"] = room
            contract["architecture_tasks"] = [{"component_id": "room_shell", "kind": "room_shell", "parameters": room}]
            contract["decor_tasks"] = contract.get("decor_tasks", [])
        if edit:
            contract["edit_operations"] = [edit]
        resolved_assets = resolve_local_assets(contract)
        # Resolve canonical widths/origins before generating the stable child
        # IDs; otherwise a rotated footprint produces the wrong book count.
        self._bind_builder_parameters(contract)
        solve_spatial_constraints(contract)
        # Expand repeated physical contents once, before dependency closure is
        # computed. IDs remain stable across edits, while geometry is emitted by
        # the ordinary aDSL assembly path below.
        expand_bookshelf_components(contract)
        plan_path = output_dir / "scene_contract.json"
        self._write_json(plan_path, contract)
        stages.append(StageResult("scene_planner", "completed", plan_path.name, {"backend": contract["planning_backend"], "components": len(contract["components"]), "relations": len(contract["relations"])}))
        stages.append(StageResult("asset_resolver", "completed" if resolved_assets else "no_local_match", details={"resolved_assets": resolved_assets}))

        validate_scene(contract)
        stages.append(StageResult("contract_validator", "passed", details={"schema": contract["schema"], "component_ids_unique": True, "relation_endpoints_valid": True}))
        baseline = copy.deepcopy(contract)
        baseline["edit_operations"] = []
        refresh_bookshelf_components(baseline)
        self._write_json(output_dir / "baseline_scene_contract.json", baseline)
        resolved = apply_operations(contract)
        self._apply_parameter_propagation(resolved)
        self._bind_builder_parameters(resolved)
        constraint_result = solve_spatial_constraints(resolved)
        refresh_bookshelf_components(resolved)
        edit_results = [judge_edit(baseline, resolved, operation) for operation in resolved.get("edit_operations", [])]
        resolved_path = output_dir / "resolved_scene_contract.json"
        self._write_json(resolved_path, resolved)
        self._write_json(output_dir / "model_plan.json", {
            "coordinate_frame": resolved["coordinate_frame"], "units": "m", "room": resolved["room"],
            "parameters": resolved["parameters"], "dependencies": resolved.get("parameter_dependencies", {}),
            "parts": resolved["components"], "relations": resolved["relations"],
            "build_order": ["room_shell", "furniture", "shelves", "rigid_books", "decor", "refined_asset_assembly"],
            "checks": ["unique IDs", "rigid edit invariance", "AABB support and bounds", "actual Blender bounds", "image-based visual Judge"],
            "views": ["room", "top", "each bookshelf closeup"],
            "assumptions": ["Book content and bindings are procedural approximations.", "Leaning books have geometric shelf contact; mechanical equilibrium is not simulated."]})
        operations = resolved.get("edit_operations", [])
        stages.append(StageResult("edit_dependency_planner", "completed", resolved_path.name, {"operations": len(operations), "affected": {op["id"]: op["resolved_affected"] for op in operations}}))
        stages.append(StageResult("constraint_solver", constraint_result["status"], details=constraint_result))
        stages.append(StageResult("edit_judge", "passed" if all(item["status"] == "passed" for item in edit_results) else "failed", details={"results": edit_results}))
        if any(item["status"] != "passed" for item in edit_results):
            raise ValueError("Edit Judge rejected the candidate; baseline_scene_contract.json is preserved")

        component_dir = output_dir / "components"
        component_dir.mkdir()
        assembled = Asset("FineScene_Study_Room")
        build_receipts = []
        world_bounds: dict[str, tuple[list[float], list[float]]] = {}
        build_tasks = []
        for task in resolved.get("architecture_tasks", []):
            build_tasks.append({**task, "placement": [0, 0, 0], "zone": "architecture"})
        build_tasks.extend(resolved["furniture_tasks"])
        build_tasks.extend(resolved.get("decor_tasks", []))
        built_ids = {task["component_id"] for task in build_tasks}
        for component in resolved.get("components", []):
            if component["id"] in built_ids or component["kind"] not in {"book", "bookend", "shelf", "prop", "detail"}:
                continue
            detail_task = {
                "component_id": component["id"],
                "kind": component["kind"],
                "size": component["bounds"]["size"],
                "placement": component["transform"]["position"],
                "rotation_deg": component.get("transform", {}).get("rotation_deg", [0, 0, 0])[2],
                "zone": "component_detail",
                "palette_index": component.get("palette_index", 0),
                "geometry_size": component.get("geometry_size", component["bounds"]["size"]),
                "layout_variant": component.get("layout_variant", "upright"),
                "layout_lean_deg": component.get("layout_lean_deg", 0.0),
            }
            if component["kind"] == "shelf":
                detail_task["parameters"] = {
                    "width": component["bounds"]["size"][0],
                    "depth": component["bounds"]["size"][1],
                    "thickness": component["bounds"]["size"][2],
                }
            build_tasks.append(detail_task)
        for task in build_tasks:
            if task["kind"] == "bookshelf":
                # Contents are separate entities with their own stable IDs.
                task = {**task, "parameters": {**task.get("parameters", {}), "include_shelves": False, "include_books": False}}
            built = build_component(task)
            placement = task.get("placement", [0, 0, 0])
            positioned = built.scene
            rotation = float(task.get("rotation_deg", 0.0))
            if rotation:
                positioned = rotate_shape(positioned, axis="+z", angle=rotation, center=(0, 0, 0))
            positioned = translate_shape(positioned, placement)
            assembled.attach_part(task["component_id"], positioned)
            receipt = built.manifest()
            receipt["placement"] = placement
            receipt["zone"] = task.get("zone", "architecture")
            world_low, world_high = shape_aabb(positioned)
            world_bounds[task["component_id"]] = (
                [float(value) for value in world_low],
                [float(value) for value in world_high],
            )
            receipt_path = component_dir / f"{task['component_id']}.json"
            self._write_json(receipt_path, receipt)
            build_receipts.append(receipt)
        self._write_json(output_dir / "adsl_scene.json", assembled.to_dict())
        self._write_json(output_dir / "world_bounds.json", world_bounds)
        geometry_judge = judge_component_bounds(resolved, world_bounds)
        stages.append(StageResult("geometry_judge", geometry_judge["status"], details=geometry_judge))
        if geometry_judge["status"] in {"failed", "passed_with_warnings"}:
            raise ValueError("Geometry Judge rejected the candidate; see run_report.json for component evidence")
        blender_output = output_dir / "blender_export"
        worker = Path(__file__).with_name("blender_worker_fast.py")
        command = [
            fenv.BLENDER, "--background", "--factory-startup", "--python-exit-code", "1", "--python", str(worker), "--",
            "--input",
            str(output_dir / "adsl_scene.json"),
            "--output",
            str(blender_output),
            "--render-view-count",
            "1",
        ]
        blender_run = subprocess.run(command, check=True, capture_output=True, text=True)
        execution_path = blender_output / "execution.json"
        execution = json.loads(execution_path.read_text(encoding="utf-8"))
        refined_tasks = [task for task in resolved["furniture_tasks"] if task.get("refined_blend")]
        if refined_tasks:
            blender_executable = Path(fenv.BLENDER)
            refine_script = Path(__file__).with_name("assemble_refined_asset.py")
            for refined_task in refined_tasks:
                refine_command = [
                    str(blender_executable), "--background", "--python", str(refine_script), "--",
                    "--source", refined_task["refined_blend"], "--output", str(blender_output),
                    "--component-id", refined_task["component_id"],
                    "--x", str(refined_task["placement"][0]), "--y", str(refined_task["placement"][1]), "--z", str(refined_task["placement"][2]),
                    "--rotation-z", str(refined_task.get("rotation_deg", 0.0)),
                ]
                subprocess.run(refine_command, check=True, capture_output=True, text=True)
                execution = json.loads(execution_path.read_text(encoding="utf-8"))
        subprocess.run([fenv.BLENDER, "--background", "--python", str(Path(__file__).with_name("finalize_scene.py")), "--", "--output", str(output_dir)], check=True, capture_output=True, text=True)
        actual_bounds = json.loads((output_dir / "blender_world_bounds.json").read_text(encoding="utf-8"))
        actual_judge = judge_component_bounds(resolved, actual_bounds)
        stages.append(StageResult("blender_geometry_judge", actual_judge["status"], "blender_world_bounds.json", actual_judge))
        if actual_judge["status"] != "passed":
            raise ValueError("Final Blender geometry did not pass; inspect blender_geometry_judge")
        judge_visual(output_dir, self.vision_profile)
        visual_report_path = output_dir / "visual_judge.json"
        if not visual_report_path.is_file():
            raise ValueError("Visual Judge did not produce visual_judge.json")
        visual_report = json.loads(visual_report_path.read_text(encoding="utf-8"))
        stages.append(StageResult("visual_judge", visual_report["status"], "visual_judge.json", visual_report))
        if visual_report["status"] != "passed":
            raise ValueError("Visual Judge rejected the candidate; inspect visual_judge.json")
        execution = json.loads(execution_path.read_text(encoding="utf-8"))
        stages.extend([
            StageResult("architecture_builder", "completed", "components/room_shell.json", {"features": ["floor", "left/right/back walls", "cutaway front and ceiling beams", "door frame and leaf", "window and frame", "baseboards"]}),
            StageResult("furniture_builders", "completed", "components/", {"built_components": [r["component_id"] for r in build_receipts], "primitive_counts": {r["component_id"]: r["primitive_count"] for r in build_receipts}}),
            StageResult("adsl_builder", "completed", "adsl_scene.json", {"components": len(build_receipts), "geometry": "parametric aDSL Asset objects"}),
            StageResult("asset_refiner", "reused_local_assets" if refined_tasks else "not_configured", "components/" if refined_tasks else None, {"sources": [{"component_id": task["component_id"], "asset_id": task.get("asset_id"), "source": task["refined_blend"]} for task in refined_tasks], "method": "local asset registry and native Blender material-preserving assembly" if refined_tasks else "TRELLIS.2 adapter remains optional and disabled."}),
            StageResult("refined_asset_assembly", "completed" if refined_tasks else "not_run", "blender_export/scene.blend" if refined_tasks else None, {"components": [task["component_id"] for task in refined_tasks]} if refined_tasks else {}),
            StageResult("assembly", "completed", "blender_export/scene.blend", {"assembled_components": len(build_receipts), "room_shell": "built as aDSL geometry", "glb": "blender_export/render/scene.glb"}),
            StageResult("render_evidence", "completed", "blender_export/render/view_000.png", {"blender": execution.get("blender_version"), "render_paths": execution.get("render_paths", [])}),
        ])
        report = {
            "schema": "finescene.run_report.v1",
            "scene_id": contract["scene_id"],
            "status": "validated_rendered_scene",
            "accepted": True,
            "stages": [stage.__dict__ for stage in stages],
            "artifacts": [plan_path.name, resolved_path.name, "adsl_scene.json", "components/", "blender_export/scene.blend", "blender_export/render/scene.glb", "blender_export/render/view_000.png"],
            "limitations": ["Collision/support checks use AABBs; no gravity or friction simulation.", "Visual Judge reads rendered views; occluded surfaces remain unverified.", "TRELLIS.2 refinement remains optional; local assets are recorded in the run report."],
        }
        self._write_json(output_dir / "run_report.json", report)
        return report

    @staticmethod
    def _apply_parameter_propagation(scene: dict[str, Any]) -> None:
        components = {component["id"]: component for component in scene.get("components", [])}
        for operation in scene.get("edit_operations", []):
            propagation = scene.get("parameter_propagation", {}).get(operation["parameter"], {})
            delta = float(operation.get("delta", 0.0)) if operation.get("op") == "add" else 0.0
            for component_id in propagation.get("translate_z", []):
                component = components.get(component_id)
                if component is not None:
                    component["transform"]["position"][2] += delta

    @staticmethod
    def _bind_builder_parameters(scene: dict[str, Any]) -> None:
        bindings: dict[str, dict[str, str]] = {}
        for task in scene["furniture_tasks"]:
            if task["kind"] == "bookshelf" and "shelf_clearance" in scene["parameters"]:
                task.setdefault("parameters", {})["shelf_clearance"] = scene["parameters"]["shelf_clearance"]
                bindings.setdefault(task["component_id"], {})["shelf_clearance"] = "shelf_clearance"
            if task["kind"] == "desk":
                for local_name, scene_name in (("width", "desk_width"), ("height", "desk_height")):
                    if scene_name in scene["parameters"]:
                        task.setdefault("parameters", {})[local_name] = scene["parameters"][scene_name]
                        bindings.setdefault(task["component_id"], {})[local_name] = scene_name
        scene["builder_parameter_bindings"] = bindings

    @staticmethod
    def _write_json(path: Path, value: dict[str, Any]) -> None:
        def default(value: Any) -> Any:
            if hasattr(value, "tolist"):
                return value.tolist()
            raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")

        path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=default) + "\n", encoding="utf-8")


__all__ = ["FineSceneAgentSystem", "PlannerBackend", "TemplateStudyPlanner"]
