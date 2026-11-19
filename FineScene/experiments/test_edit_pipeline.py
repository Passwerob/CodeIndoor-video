"""Regression checks for physical editing and rejection, without a render/GPU."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from agent_system import FineSceneAgentSystem, TemplateStudyPlanner
from scene_entities import expand_bookshelf_components, refresh_bookshelf_components
from constraint_solver import solve_spatial_constraints, judge_component_bounds
from edit_judge import judge_edit
from validate_scene_contract import apply_operations
from adsl_components import build_component, build_room_shell, build_desk_chair
from adsl.core import shape_aabb, rotate_shape, translate_shape


def contract():
    result = TemplateStudyPlanner().plan("study")
    result["room"] = {"width": 5, "depth": 4, "height": 2.8}
    expand_bookshelf_components(result)
    return result


def edited(base, delta):
    candidate = copy.deepcopy(base)
    candidate["edit_operations"] = [{"id": "edit", "op": "add", "parameter": "shelf_clearance", "delta": delta}]
    candidate = apply_operations(candidate)
    FineSceneAgentSystem._bind_builder_parameters(candidate)
    refresh_bookshelf_components(candidate)
    return candidate


class EditRegression(unittest.TestCase):
    def test_desk_pairing_is_idempotent_near_wall(self):
        scene = TemplateStudyPlanner().plan("study")
        scene["room"] = {"width":5,"depth":4,"height":2.8}
        desk = next(t for t in scene["furniture_tasks"] if t["kind"]=="desk")
        desk["placement"][1] = 1.9
        chair = next(t for t in scene["furniture_tasks"] if t["kind"]=="chair")
        chair["role"] = "desk"
        solve_spatial_constraints(scene)
        original = copy.deepcopy(scene["components"])
        solve_spatial_constraints(scene)
        self.assertEqual(original,scene["components"])

    def test_detailed_books_match_tilt_bounds_and_stack_supports(self):
        scene = contract()
        refresh_bookshelf_components(scene)
        bounds = {}
        books = [c for c in scene["components"] if c["kind"] == "book"]
        self.assertEqual({c["layout_variant"] for c in books}, {"upright", "leaning", "horizontal_stack"})
        supports = {r["from"]: r["to"] for r in scene["relations"] if r["type"] == "supported_by"}
        self.assertTrue(any(".book_" in target for target in supports.values()))
        for c in scene["components"]:
            if c["kind"] not in {"book", "bookend", "shelf"}:
                continue
            task = {**c,"component_id":c["id"],"size":c["bounds"]["size"]}
            if c["kind"] == "shelf":
                task["parameters"] = dict(zip(("width","depth","thickness"),c["bounds"]["size"]))
            built = build_component(task)
            if c["kind"] == "book":
                self.assertGreaterEqual(built.manifest()["primitive_count"], 9)
                low,high = shape_aabb(built.scene)
                self.assertAlmostEqual(float(low[2]),0)
                for actual,expected in zip(high-low,c["bounds"]["size"]):
                    self.assertAlmostEqual(float(actual),expected,places=5)
            moved = translate_shape(built.scene,c["transform"]["position"])
            bounds[c["id"]] = shape_aabb(moved)
        leaf_scene = {**scene,"furniture_tasks":[],"decor_tasks":[]}
        self.assertEqual(judge_component_bounds(leaf_scene,bounds)["status"],"passed")

    def test_missing_visual_evidence_never_passes(self):
        from visual_judge import judge_visual
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as tmp:
            out = Path(tmp)
            (out/"blender_export").mkdir()
            (out/"resolved_scene_contract.json").write_text("{}")
            (out/"blender_export/execution.json").write_text('{"render_paths": []}')
            result = judge_visual(out,Path("missing_profile.yaml"))
            self.assertFalse(result["accepted"])
            self.assertEqual(result["status"],"unavailable")

    def test_spacing_moves_upper_books_and_preserves_rigid_geometry(self):
        base = contract()
        after = edited(base, 0.1)
        self.assertEqual(judge_edit(base, after, after["edit_operations"][0])["status"], "passed")
        original = {c["id"]: c for c in base["components"]}
        for component in after["components"]:
            old = original[component["id"]]
            if component["kind"] == "book":
                self.assertEqual(old["bounds"], component["bounds"])
                index = int(component["parent"].rsplit("_", 1)[1]) - 1
                self.assertAlmostEqual(component["transform"]["position"][2] - old["transform"]["position"][2], index * .1)
                def mesh(c):
                    return build_component({"component_id": c["id"], "kind": "book", "size": c["bounds"]["size"]}).scene.to_dict()
                self.assertEqual(json.dumps(mesh(old), sort_keys=True, default=lambda v: v.tolist()), json.dumps(mesh(component), sort_keys=True, default=lambda v: v.tolist()))
            elif component["id"] in {"desk_01", "chair_01", "room", "bookshelf_01.shelf_1"}:
                self.assertEqual(old, component)
        restored = edited(after, -0.1)
        for before, back in zip(base["components"], restored["components"]):
            for a, b in zip(before["transform"]["position"], back["transform"]["position"]):
                self.assertAlmostEqual(a, b)

    def test_shrinking_rejects_instead_of_resizing_books(self):
        base = contract()
        saved = copy.deepcopy(base)
        with self.assertRaisesRegex(ValueError, "Rigid book"):
            edited(base, -.3)
        self.assertEqual(base, saved)

    def test_missing_or_resized_book_fails_edit_judge(self):
        base = contract()
        after = edited(base, .1)
        book = next(c for c in after["components"] if c["kind"] == "book" and "shelf_2" in c["id"])
        book["bounds"]["size"][2] *= .8
        self.assertEqual(judge_edit(base, after, after["edit_operations"][0])["status"], "failed")
        after["components"].remove(book)
        self.assertIn(book["id"], judge_edit(base, after, after["edit_operations"][0])["removed_component_ids"])

    def test_support_check_catches_floating_book(self):
        scene = {"room": {"width": 5, "depth": 5, "height": 3}, "components": [
            {"id": "shelf", "kind": "shelf", "parent": "room"},
            {"id": "book", "kind": "book", "parent": "shelf"}],
            "relations": [{"type": "supported_by", "from": "book", "to": "shelf"}]}
        bounds = {"shelf": ([-1, -.3, .9], [1, .3, 1]), "book": ([-.1, -.1, 1], [.1, .1, 1.2])}
        self.assertEqual(judge_component_bounds(scene, bounds)["status"], "passed")
        bounds["book"][0][2] += .02
        self.assertEqual(judge_component_bounds(scene, bounds)["status"], "failed")

    def test_rejected_edit_never_launches_blender(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as tmp:
            out = Path(tmp) / "candidate"
            with patch("agent_system.subprocess.run") as launch:
                report = FineSceneAgentSystem().run("study", out, {"id": "bad", "op": "add", "parameter": "shelf_clearance", "delta": -.3})
            self.assertEqual(report["status"], "rejected")
            launch.assert_not_called()
            self.assertTrue((out / "baseline_scene_contract.json").is_file())
            self.assertTrue((out / "run_report.json").is_file())

    def test_floor_surface_is_z_zero(self):
        low, high = shape_aabb(build_room_shell().scene)
        self.assertAlmostEqual(float(low[2]), -.08)


if __name__ == "__main__":
    unittest.main(verbosity=2)
