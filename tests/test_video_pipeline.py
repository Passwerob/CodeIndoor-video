"""Offline tests for the video -> scene pipeline (no network, no Blender).

Every VLM call is replaced by a stub, so these pin the deterministic parts:
material grammar, joint derivation, the spec-as-test, the dry run, colour
measurement, keyframes, layout derivation, normalisation of model answers.
"""
import json
import math
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "adsl_adapter"))

import appearance as ap  # noqa: E402
import articulation as ar  # noqa: E402
import dryrun_builder as dr  # noqa: E402
import functional_spec as fs  # noqa: E402
import material_spec as ms  # noqa: E402
import spec_check as sc  # noqa: E402
import video_frames as vf  # noqa: E402
import video_layout as vl  # noqa: E402
import video_survey as vs  # noqa: E402
import vlm  # noqa: E402

cv2 = pytest.importorskip("cv2")


# --------------------------------------------------------------------------
# materials
# --------------------------------------------------------------------------
def test_material_grammar_is_backward_compatible_and_takes_modifiers():
    old = ms.parse("fabric:#8FA08A:0.9")
    assert old["kind"] == "fabric" and old["roughness"] == 0.9 and old["mods"] == {}
    assert old["name"] == "Fabric | #8FA08A r0.9"          # material names in old .blend files
    tile = ms.parse("tile:#F4F4F2:0.12:grout=#1E1E1E:size=0.15:gap=0.004")
    assert tile["mods"] == {"grout": "#1E1E1E", "size": 0.15, "gap": 0.004}
    scr = ms.parse("screen:#0E0E12:0.06:emit=#3355FF:strength=3")
    assert scr["emit_strength"] == 3.0 and ms.is_emissive(scr["key"])
    assert ms.parse("light:#FFFFFF")["emit_strength"] > 0
    for bad in ("paint:#F3F1EC:1.5", "wood:#B58A5A:x", "tile:#FFF:0.1", "metal:#AAAAAA:0.3:size=9",
                "stone:#AAAAAA:0.3:veins=grey"):
        assert ms.parse(bad) is None, bad


def test_material_recipe_patterns():
    r = ms.recipe(ms.parse("tile:#F4F4F2:0.12:grout=#1E1E1E:size=0.10"))
    assert r["pattern"]["type"] == "tile" and r["pattern"]["size"] == 0.10
    assert ms.recipe(ms.parse("stone:#ECECE8:0.15:veins=#9A9A9A"))["pattern"]["type"] == "veins"
    assert ms.recipe(ms.parse("stone:#D9D9D6:0.45:speckle=#5A5A5A"))["pattern"]["type"] == "speckle"
    m = ms.recipe(ms.parse("metal:#B9BBBD:0.32:brushed"))
    assert m["metallic"] > 0.9 and m["anisotropic"] > 0 and m["pattern"]["type"] == "brushed"
    e = ms.recipe(ms.parse("screen:#0E0E12:0.06:emit=#3355FF"))
    assert e["emission_strength"] > 0 and max(e["emission_rgb"]) == e["emission_rgb"][2]


@pytest.mark.parametrize("args", [
    ("stainless steel", "#b8b8b8 (brushed)", "Satin"),
    ("ceramic", "#F5F5F2", "glossy", {"type": "tile_grid", "secondary_hex": "#202020", "scale_m": 0.15}),
    ("marble", "#EDEDE8", "polished", {"type": "marble_veins", "secondary_hex": "#9a9a9a"}),
    ("???", None, None),
    ("plastic", "not a colour", "weird"),
])
def test_from_observation_always_parses(args):
    cls, hexa, finish = args[:3]
    pattern = args[3] if len(args) > 3 else None
    key = ms.from_observation(cls, hexa, finish, pattern=pattern, emissive=cls == "plastic",
                              emit_hex="#3355FF")
    assert ms.parse(key) is not None, key


# --------------------------------------------------------------------------
# structure
# --------------------------------------------------------------------------
BODY = {"name": "body", "min": [-0.2, -0.18, 0.0], "max": [0.2, 0.2, 1.0]}
DOOR = {"name": "door", "min": [-0.192, -0.2, 0.05], "max": [0.192, -0.18, 0.55]}
HANDLE = {"name": "door_handle", "min": [0.15, -0.22, 0.25], "max": [0.17, -0.2, 0.45]}


@pytest.mark.parametrize("edge,toward,sign,collides", [
    ("left", "front", -1, False), ("right", "front", 1, False),
    ("left", "back", 1, True), ("right", "back", -1, True)])
def test_hinge_sign_follows_opening_direction_and_swing_is_tested(edge, toward, sign, collides):
    j = ar.derive_joint({"type": "revolute", "moving_parts": ["door"], "hinge_edge": edge,
                         "opens_toward": toward, "range": 110},
                        [BODY, DOOR, HANDLE], {"door": {"id": "door", "face": "front"}})
    assert j["parts"] == ["door", "door_handle"]            # the handle rides with the door
    assert j["axis_index"] == 2 and math.copysign(1, j["open_value"]) == sign
    assert j["origin"][0] == pytest.approx(-0.192 if edge == "left" else 0.192)
    assert bool(j["issues"]) == collides


def test_lid_drawer_and_button_joints():
    box = {"name": "box", "min": [-0.2, -0.2, 0.0], "max": [0.2, 0.2, 0.4]}
    lid = {"name": "lid", "min": [-0.2, -0.2, 0.4], "max": [0.2, 0.2, 0.42]}
    j = ar.derive_joint({"type": "revolute", "moving_parts": ["lid"], "face": "top",
                         "hinge_edge": "back", "opens_toward": "up", "range": 100}, [box, lid])
    assert j["axis_index"] == 0 and j["origin"][1:] == [0.2, 0.42] and j["open_value"] < 0
    d = ar.derive_joint({"type": "prismatic", "moving_parts": ["lid"], "opens_toward": "front"},
                        [box, lid], envelope=(0.4, 0.4, 0.42))
    assert d["axis_index"] == 1 and d["open_value"] == pytest.approx(-0.26)
    b = ar.derive_joint({"type": "press", "moving_parts": ["lid"], "face": "front"}, [box, lid])
    assert b["axis_index"] == 1 and 0 < b["open_value"] <= 0.02          # into the face


def test_counted_moving_parts_get_one_joint_each():
    parts = [BODY] + [{"name": f"drawer_{k}", "min": [-0.2, -0.2, 0.1 * k], "max": [0.2, -0.18, 0.1 * k + 0.08]}
                      for k in (1, 2, 3)]
    spec = {"parts": [{"id": "drawer", "kind": "drawer", "count": 3, "face": "front"}],
            "joints": [{"id": "slide", "type": "prismatic", "moving_parts": ["drawer"],
                        "opens_toward": "front", "range": 0.3}]}
    joints = ar.derive_all(spec, parts)
    assert [j["parts"] for j in joints] == [["drawer_1"], ["drawer_2"], ["drawer_3"]]


def test_range_magnitude_reads_lists_and_strings():
    assert ar.range_magnitude([0, 110], 1) == 110
    assert ar.range_magnitude("0-95 deg", 1) == 95
    assert ar.range_magnitude(None, 7) == 7


# --------------------------------------------------------------------------
# spec as a test
# --------------------------------------------------------------------------
SPEC = {"parts": [
    {"id": "body", "kind": "housing", "count": 1, "face": "front", "u": 0.5, "v": 0.5},
    {"id": "door", "kind": "door", "count": 1, "face": "front", "u": 0.5, "v": 0.3},
    {"id": "button", "kind": "button", "count": 4, "face": "front", "u": 0.5, "v": 0.85},
    {"id": "display", "kind": "display", "count": 1, "face": "front", "u": 0.5, "v": 0.93,
     "emissive": True},
    {"id": "logo", "kind": "logo", "count": 1, "face": "front", "u": 0.1, "v": 0.9}]}
P = {"width": 0.4, "depth": 0.4, "height": 1.0}


def _part(name, lo, hi, mat="plastic:#202020"):
    return {"name": name, "min": list(lo), "max": list(hi), "material": mat}


def _good_parts():
    parts = [_part("body", (-0.2, -0.19, 0), (0.2, 0.2, 1.0)),
             _part("door", (-0.19, -0.2, 0.05), (0.19, -0.19, 0.55)),
             _part("display", (-0.05, -0.2, 0.9), (0.05, -0.19, 0.96), "screen:#0E0E12:0.06:emit=#3355FF")]
    for k in range(4):
        x = -0.12 + 0.08 * k
        parts.append(_part(f"button_{k + 1}", (x - 0.01, -0.2, 0.84), (x + 0.01, -0.19, 0.86)))
    return parts


def test_spec_check_passes_a_matching_part_list():
    rep = sc.check(_good_parts(), SPEC, P)
    assert rep["hard"] == [] and rep["coverage"] == {"required": 4, "found": 4}


def test_spec_check_reports_missing_count_and_glow():
    parts = [p for p in _good_parts() if p["name"] not in ("door", "button_4")]
    parts = [dict(p, material="plastic:#101010") if p["name"] == "display" else p for p in parts]
    ids = {v["id"] for v in sc.check(parts, SPEC, P)["hard"]}
    assert {"spec.missing:door", "spec.count:button", "spec.emission:display"} <= ids
    assert "spec.missing:logo" not in ids                    # labels/logos are optional


def test_envelope_violation_names_the_culprit_and_keeps_presence_checks():
    """Regression: the envelope branch used to overwrite the part-name list,
    so every part read as missing whenever the envelope failed."""
    parts = _good_parts() + [_part("side_vent", (-0.35, -0.0, 0.3), (-0.2, 0.01, 0.4))]
    rep = sc.check(parts, SPEC, P)
    env = next(v for v in rep["hard"] if v["id"] == "envelope")
    assert "side_vent" in env["message"]
    assert rep["coverage"]["found"] == 4
    assert not any(v["id"].startswith("spec.missing") for v in rep["hard"])


def test_buried_and_filled_functional_parts_are_hard_failures():
    spec = {"parts": [
        {"id": "body", "kind": "housing", "count": 1, "face": "front", "u": 0.5, "v": 0.5},
        {"id": "niche", "kind": "other", "count": 1, "face": "inside", "u": 0.5, "v": 0.6},
        {"id": "tray", "kind": "tray", "count": 1, "face": "inside", "u": 0.5, "v": 0.55,
         "parent": "niche"}]}
    solid = [_part("body", (-0.2, -0.2, 0), (0.2, 0.2, 1.0)),
             _part("niche", (-0.15, -0.2, 0.55), (0.15, 0.0, 0.75)),
             _part("tray", (-0.14, -0.19, 0.55), (0.14, -0.01, 0.57))]
    ids = {v["id"] for v in sc.check(solid, spec, P)["hard"]}
    assert "spec.filled:niche" in ids and "spec.buried:tray" in ids
    hollow = [_part("body_lower", (-0.2, -0.2, 0), (0.2, 0.2, 0.55)),
              _part("body_upper", (-0.2, -0.2, 0.75), (0.2, 0.2, 1.0)),
              _part("body_back", (-0.2, 0.0, 0.55), (0.2, 0.2, 0.75)),
              _part("body_left", (-0.2, -0.2, 0.55), (-0.15, 0.0, 0.75)),
              _part("body_right", (0.15, -0.2, 0.55), (0.2, 0.0, 0.75)),
              _part("niche", (-0.15, -0.2, 0.55), (0.15, 0.0, 0.75)),
              _part("tray", (-0.14, -0.19, 0.55), (0.14, -0.01, 0.57))]
    assert sc.check(hollow, spec, P)["hard"] == []


# --------------------------------------------------------------------------
# dry run
# --------------------------------------------------------------------------
PROGRAM = '''
def build(B, mats, P):
    W, D, H = P["width"], P["depth"], P["height"]
    B.box("body", (W, D, H), (0, 0, H / 2), "plastic:#F2F0EC")
    for k in range(2):
        o = B.cylinder(f"knob_{k + 1}", 0.02, 0.01, (-0.05 + 0.1 * k, -D / 2 - 0.005, 0.8),
                       "metal:#B0B0B0", rot=(math.pi / 2, 0, 0))
        o.location.z += 0.01
    lathe(B, "foot", [(0.03, 0), (0.02, 0.02)], (0, 0, 0), mats["steel_brushed"])
    return B.objects_created()
'''


def test_dry_run_records_names_boxes_and_materials():
    res = dr.run_program(PROGRAM, P)
    assert res["ok"], res
    by = {p["name"]: p for p in res["parts"]}
    assert set(by) == {"body", "knob_1", "knob_2", "foot"}
    assert by["knob_1"]["min"][2] == pytest.approx(0.79, abs=1e-3)      # moved by .location.z
    assert by["knob_1"]["max"][1] - by["knob_1"]["min"][1] == pytest.approx(0.01, abs=1e-3)
    assert by["foot"]["material"] == "steel_brushed"


def test_dry_run_separates_program_faults_from_stand_in_gaps():
    bad_key = dr.run_program("def build(B, mats, P):\n    B.box('a', (1, 1, 1), (0, 0, 0), 'paint:#F00')\n", P)
    assert not bad_key["ok"] and bad_key["program_fault"]
    bug = dr.run_program("def build(B, mats, P):\n    x = P['nope']\n", P)
    assert not bug["ok"] and bug["program_fault"] and "line" in bug["error"]
    odd = dr.run_program("def build(B, mats, P):\n    o = B.box('a', (1, 1, 1), (0, 0, .5), 'paint:#FF0000')\n"
                         "    o.modifiers.new('bevel', 'BEVEL').width = 0.01\n", P)
    assert odd["ok"]                                         # Blender-side attribute: tolerated


# --------------------------------------------------------------------------
# colour measurement
# --------------------------------------------------------------------------
def _srgb8(lin):
    return int(round(float(ap.lin_to_srgb(lin)) * 255))


def _scene_image():
    """Left half: white wall (linear 0.40); right: grey material (linear 0.20)."""
    img = np.zeros((200, 400, 3), np.uint8)
    img[:, :200] = _srgb8(0.40)
    img[:, 200:] = _srgb8(0.20)
    return img


def test_white_anchored_albedo_and_neutral_snap():
    img = _scene_image()
    m = ap.measure(img, [550, 100, 950, 900], {"bbox": [50, 100, 450, 900]}, "stone")
    assert m["anchored"] and m["ratio_to_white"] == pytest.approx(0.5, abs=0.02)
    assert ms.hex_to_linear(m["albedo_hex"])[1] == pytest.approx(0.425, abs=0.02)   # 0.5 * 0.85
    assert len(set(m["albedo_hex"][1:][i:i + 2] for i in (0, 2, 4))) == 1          # neutral


def test_shadowed_white_box_is_replaced_by_the_frame_white_level():
    img = _scene_image()
    img[:40, :200] = _srgb8(0.10)                            # the "white" box sits in shadow
    a = ap.white_anchor(img, {"bbox": [0, 0, 500, 200]})
    assert a["note"] and ap._lum(a["lin"]) == pytest.approx(0.40, abs=0.03)


def test_consistency_gate_rejects_a_region_on_something_else():
    ok, _ = ap.consistent_with_vlm("#C9A678", ms.hex_to_linear("#C2A070"))
    assert ok
    grey, why = ap.consistent_with_vlm("#C9A678", ms.hex_to_linear("#B1AFA4"))
    assert not grey and "colour" in why
    bright, why = ap.consistent_with_vlm("#222224", ms.hex_to_linear("#C7C8C7"))
    assert not bright and "brightness" in why


def test_colour_temperature_and_camera_white_balance():
    warm, cool = ap.cct_to_rgb(2700), ap.cct_to_rgb(6500)
    assert warm[2] < 0.2 and min(cool) > 0.9
    assert ap.camera_white_balance(4000, [1, 1, 1]) == pytest.approx(4000, abs=60)
    assert ap.camera_white_balance(4000, [1.0, 0.95, 0.85]) > 4000


def test_light_plan_places_pendants_below_the_ceiling():
    lighting = {"fixtures": [{"id": "pend", "type": "pendant", "count": 2, "beam": "medium",
                              "cct_k": 4000, "drop_m": 0.9,
                              "housing": {"diameter_m": 0.1, "height_m": 0.16}}]}
    plan = ap.light_plan(lighting, [{"fixture": "pend", "x": 0, "y": 1}, {"fixture": "pend", "x": 0, "y": -1}], 2.8)
    assert len(plan) == 2 and plan[0]["type"] == "SPOT"
    assert plan[0]["location"][2] == pytest.approx(2.8 - 0.9 - 0.16 - 0.005, abs=1e-3)
    assert plan[0]["temperature_k"] == 4000


# --------------------------------------------------------------------------
# reading the model's answers
# --------------------------------------------------------------------------
def test_spec_normalisation_unwraps_maps_kinds_and_infers_joints():
    raw = {"object": {"category": "cabinet", "parts": [
        {"id": "Carcass", "kind": "cabinet body", "face": "front"},
        {"id": "door L", "kind": "cabinet door", "face": "front", "u": 0.3,
         "material": {"class": "laminate", "hex": "#C9A678", "finish": "satin"}},
        {"id": "pull", "kind": "door handle", "face": "front", "u": 0.45, "parent": "door L"},
        {"id": "fan", "kind": "vent grille", "face": "left"}],
        "joints": [{"type": "revolute", "moving_parts": ["door L"], "hinge_edge": "right",
                    "opens_toward": "front", "range": "0-100", "confidence": "low"}]}}
    spec = fs.normalise(fs.unwrap(raw))
    kinds = {p["id"]: p["kind"] for p in spec["parts"]}
    assert kinds == {"carcass": "housing", "door_l": "door", "pull": "handle", "fan": "grille"}
    door = next(p for p in spec["parts"] if p["id"] == "door_l")
    assert door["material_key"].startswith("wood:#C9A678")
    j = spec["joints"][0]
    assert j["moving_parts"] == ["door_l"] and j["range"] == 100
    assert j["hinge_edge"] == "left"          # handle on the right of the door -> hinge left
    assert any("hinge moved" in c for c in spec["checks"])


def test_doors_without_a_joint_get_an_inferred_one():
    spec = fs.normalise({"parts": [{"id": "lid", "kind": "lid", "face": "top"},
                                   {"id": "drawer", "kind": "drawer", "face": "front"}]})
    by = {j["moving_parts"][0]: j for j in spec["joints"]}
    assert by["lid"]["type"] == "revolute" and by["lid"]["hinge_edge"] == "back"
    assert by["drawer"]["type"] == "prismatic" and by["drawer"]["source"] == "inferred"


def test_control_reconciliation_respects_readability():
    def spec():
        return fs.normalise({"parts": [
            {"id": "head", "kind": "housing", "face": "front"},
            {"id": "key", "kind": "touch_key", "count": 4, "parent": "head", "confidence": "medium"}]})
    s = spec()
    fs.reconcile(s, "head", {"readability": "partial", "controls": [{"type": "touch key"}] * 2})
    assert next(p for p in s["parts"] if p["id"] == "key")["count"] == 4       # kept, flagged
    s = spec()
    out = fs.reconcile(s, "head", {"readability": "clear", "controls": [
        {"type": "touch key", "label": f"k{i}"} for i in range(3)] + [{"type": "indicator", "label": "lock"}]})
    key = next(p for p in s["parts"] if p["id"] == "key")
    assert key["count"] == 3 and key["labels"] == ["k0", "k1", "k2"]
    assert any(p["kind"] == "indicator" for p in s["parts"]) and out["changes"]


def test_spec_block_gives_box_extents_in_dsl_order():
    spec = fs.normalise({"parts": [{"id": "vent", "kind": "grille", "face": "left",
                                    "size_frac": [0.02, 0.5, 0.2]}]})
    spec.update({"dimensions_m": {"width": 0.4, "depth": 0.4, "height": 1.0}, "object": {}})
    assert "box x=0.02*W y=0.50*D z=0.20*H" in fs.spec_block(spec)


def test_inventory_normalisation_and_view_selection():
    raw = {"objects": [{"id": "Trash Bin", "frames": ["F1", "f03", "F99"], "best_frame": "F03",
                        "mount": "floor", "on": "nothing"},
                       {"id": "trash bin", "frames": ["F02"], "mount": "sideways"}],
           "room": {"openings": [{"kind": "window", "frames": ["F01"]}]}}
    inv = vs.normalise_inventory(raw, {"F01", "F02", "F03"})
    a, b = inv["objects"]
    assert (a["id"], b["id"]) == ("trash_bin", "trash_bin_2")
    assert a["frames"] == ["F01", "F03"] and a["best_frame"] == "F03" and a["on"] is None
    assert b["mount"] == "floor"
    views = [{"t": t, "area": ar_, "complete": True, "visible_fraction": 1.0, "sharpness_rel": 1.0}
             for t, ar_ in ((10, 0.30), (11, 0.29), (40, 0.10), (80, 0.20))]
    picked = vs.select_views(views, k=3, min_gap_s=4)
    assert [v["t"] for v in picked] == [10, 80, 40]


def test_vlm_rejects_answers_that_never_saw_the_images(monkeypatch, tmp_path):
    vlm._VISION_OK["fake"] = True
    monkeypatch.setattr(vlm, "_send", lambda payload: ({"usage": {"prompt_tokens": 300},
                                                        "choices": [{"message": {"content": "{}"}}]},
                                                       b"{}", "stub"))
    img = np.zeros((32, 32, 3), np.uint8)
    with pytest.raises(vlm.VisionError):
        vlm.ask_json("json please", [img, img, img], stage="t", model="fake")
    assert vlm._parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert vlm._parse_json('noise {"b": 2} trailing') == {"b": 2}


# --------------------------------------------------------------------------
# frames + layout
# --------------------------------------------------------------------------
def test_keyframes_contact_sheets_and_crops(tmp_path):
    path = tmp_path / "pan.avi"
    w, h, fps = 160, 96, 10
    out = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), fps, (w, h))
    rng = np.random.default_rng(0)
    texture = (rng.random((h, 4 * w)) * 255).astype(np.uint8)
    for i in range(120):                                        # 12 s pan
        frame = cv2.cvtColor(texture[:, i * 2: i * 2 + w].copy(), cv2.COLOR_GRAY2BGR)
        out.write(frame)
    out.release()
    frames = vf.select_keyframes(path, tmp_path / "frames", target=6, sample_fps=5)
    assert 3 <= len(frames) <= 6 and frames[0]["id"] == "F00"
    assert all((tmp_path / "frames" / f["file"]).is_file() for f in frames)
    sheets = vf.contact_sheets(frames, tmp_path / "frames", tmp_path / "sheets", per_sheet=4)
    assert len(sheets) == (len(frames) + 3) // 4
    img = vf.imread(tmp_path / "frames" / frames[0]["file"])
    cut, box = vf.crop(img, [250, 250, 750, 750], margin=0.0)
    assert cut.shape[:2] == (box[3] - box[1], box[2] - box[0])
    assert vf.bbox_pixels([0, 0, 1, 1], w, h) is None


def test_layout_derivation_walls_supports_and_separation():
    objects = [
        {"id": "counter", "mount": "floor", "size_guess_m": [2.0, 0.6, 0.9]},
        {"id": "fridge", "mount": "floor", "size_guess_m": [0.9, 0.7, 1.9]},
        {"id": "microwave", "mount": "on_surface", "on": "counter", "size_guess_m": [0.5, 0.4, 0.3]},
        {"id": "sink", "mount": "built_in", "on": "counter", "size_guess_m": [0.6, 0.45, 0.2]},
        {"id": "upper", "mount": "wall", "size_guess_m": [2.0, 0.35, 0.7]}]
    raw = {"room": {"width_m": 3.0, "depth_m": 4.0, "height_m": 2.7},
           "objects": [{"id": "counter", "wall": "east", "offset_m": 2.0},
                       {"id": "fridge", "wall": "east", "offset_m": 3.0},
                       {"id": "microwave", "wall": "east", "offset_m": 1.5, "on": "counter"},
                       {"id": "sink", "wall": "east", "offset_m": 2.4, "on": "counter"},
                       {"id": "upper", "wall": "east", "offset_m": 2.0, "bottom_m": 1.5}],
           "openings": [{"id": "w", "kind": "window", "wall": "west", "offset_m": 2, "width_m": 1,
                         "height_m": 1.2, "sill_m": 0.9}],
           "fixtures": [{"fixture": "pend", "positions_m": [[0, 1], [0, -1]]}]}
    lay = vl.derive(raw, objects, {}, [{"id": "pend", "count": 2}])
    o = lay["objects"]
    assert o["counter"]["yaw_deg"] == -90 and o["counter"]["x"] == pytest.approx(1.5 - 0.3)
    assert o["fridge"]["y"] == pytest.approx(2.0 - 3.0)           # east wall: left end is north
    assert o["microwave"]["z"] == pytest.approx(0.9) and o["upper"]["z"] == pytest.approx(1.5)
    assert o["sink"]["z"] == pytest.approx(0.9 - 0.2)             # built-in: recessed
    # the counter (offset 2.0 -> y 0) overlapped the fridge and was pushed along the wall
    assert o["counter"]["y"] == pytest.approx(-1.0 + 0.45 + 1.0)
    assert any(i["id"].startswith("layout.overlap") for i in lay["issues"])
    assert len(lay["lights"]) == 2
    seg = vl.wall_segments("west", 3.0, 4.0, 2.7, lay["openings"])
    assert len(seg["holes"]) == 1 and len(seg["solids"]) == 4     # left, below, above, right
    sb = vl.splashbacks({"objects": {"base_counter": dict(o["counter"], id="base_counter"),
                                     "upper": o["upper"]}},
                        {"t": {"class": "tile", "key": "tile:#F4F4F2:0.12:grout=#1E1E1E"}})
    assert sb and sb[0]["z0"] == pytest.approx(0.9) and sb[0]["z1"] == pytest.approx(1.5)


def test_a_panel_inside_a_full_height_body_is_buried():
    """Regression: a charcoal control head built inside a full-height white
    body box (coplanar front face) rendered white and passed every check."""
    spec = {"parts": [
        {"id": "body", "kind": "housing", "count": 1, "face": "front", "u": 0.5, "v": 0.5},
        {"id": "head", "kind": "panel", "count": 1, "face": "top", "u": 0.5, "v": 0.5,
         "material_key": "plastic:#33363F:0.2"}]}
    parts = [_part("body", (-0.2, -0.2, 0), (0.2, 0.2, 1.0), "plastic:#F2F1EC"),
             _part("head", (-0.19, -0.2, 0.84), (0.19, 0.02, 1.0), "plastic:#33363F:0.2")]
    assert "spec.buried:head" in {v["id"] for v in sc.check(parts, spec, P)["hard"]}
    parts[0] = _part("body", (-0.2, -0.2, 0), (0.2, 0.2, 0.84), "plastic:#F2F1EC")
    assert sc.check(parts, spec, P)["hard"] == []
    parts[1] = dict(parts[1], material="plastic:#F2F1EC")     # right place, wrong tone
    assert "spec.colour:head" in {v["id"] for v in sc.check(parts, spec, P)["hard"]}


def test_an_opening_covered_by_an_intact_slab_is_filled():
    """Regression: a sink cut-out drawn as a thin plate on a whole countertop."""
    spec = {"parts": [
        {"id": "top", "kind": "panel", "count": 1, "face": "top", "u": 0.5, "v": 0.5},
        {"id": "cutout", "kind": "recess", "count": 1, "face": "top", "u": 0.5, "v": 0.5}]}
    slab = [_part("top", (-0.2, -0.2, 0.96), (0.2, 0.2, 1.0)),
            _part("cutout", (-0.1, -0.1, 1.0), (0.1, 0.1, 1.002), "plastic:#111111")]
    assert "spec.filled:cutout" in {v["id"] for v in sc.check(slab, spec, P)["hard"]}
    ring = [_part("top_front", (-0.2, -0.2, 0.96), (0.2, -0.1, 1.0)),
            _part("top_back", (-0.2, 0.1, 0.96), (0.2, 0.2, 1.0)),
            _part("top_left", (-0.2, -0.1, 0.96), (-0.1, 0.1, 1.0)),
            _part("top_right", (0.1, -0.1, 0.96), (0.2, 0.1, 1.0)),
            _part("cutout", (-0.1, -0.1, 0.8), (0.1, 0.1, 1.0), "metal:#B4B6B8")]
    assert not any(v["id"].startswith("spec.filled") for v in sc.check(ring, spec, P)["hard"])


def test_a_swept_tube_does_not_bury_its_own_tip():
    spec = {"parts": [{"id": "spout", "kind": "spout", "count": 1, "face": "top", "u": 0.5, "v": 0.5},
                      {"id": "outlet", "kind": "outlet", "count": 1, "face": "front", "u": 0.5, "v": 0.6}]}
    arc = dict(_part("spout", (-0.02, -0.15, 0.3), (0.02, 0.05, 0.6)), type="sweep")
    tip = dict(_part("outlet", (-0.01, -0.14, 0.45), (0.01, -0.12, 0.47)), type="cylinder")
    assert not any(v["id"].startswith("spec.buried") for v in sc.check([arc, tip], spec, P)["hard"])
    solid = dict(arc, type="box")
    assert any(v["id"] == "spec.buried:outlet" for v in sc.check([solid, tip], spec, P)["hard"])


def test_dry_run_catches_swapped_arguments():
    """Regression: B.sphere(name, loc, radius, ...) failed in Blender five times."""
    bad = dr.run_program("def build(B, mats, P):\n    B.sphere('s', (0, 0, 0.5), 0.1, 'paint:#00AA00')\n", P)
    assert not bad["ok"] and bad["program_fault"] and "wrong order" in bad["error"]
    tube = dr.run_program("def build(B, mats, P):\n    B.tube('t', [(0, 0, 0), (0, 0, 1)], 0.01, 'rubber:#111111')\n", P)
    assert not tube["ok"] and tube["program_fault"]


def test_parts_inside_glass_are_not_buried():
    spec = {"parts": [{"id": "vase", "kind": "housing", "count": 1, "face": "front", "u": 0.5, "v": 0.3},
                      {"id": "roots", "kind": "other", "count": 1, "face": "inside", "u": 0.5, "v": 0.2},
                      {"id": "stem", "kind": "leg", "count": 1, "face": "inside", "u": 0.5, "v": 0.3}]}
    parts = [_part("vase", (-0.05, -0.05, 0), (0.05, 0.05, 0.2), "glass:#E8F0F0"),
             _part("roots", (-0.03, -0.03, 0.02), (0.03, 0.03, 0.08), "wood:#7A6040"),
             _part("stem", (-0.005, -0.005, 0.05), (0.005, 0.005, 0.15), "plastic:#3A7A30")]
    assert sc.check(parts, spec, P)["hard"] == []


# --- sim2real: grasp features (a 13 mm microwave handle passed every visual check) ---
def _grasp_case(door_top=0.835, rail_recess=0.0, handle=None, extra=None, spec_handle=None):
    import spec_check
    spec = {"parts": [{"id": "carcass", "kind": "housing", "face": "front"},
                      {"id": "door", "kind": "door", "face": "front"},
                      {"id": "worktop", "kind": "panel", "face": "top"}]
            + ([{"id": "door_handle", "kind": "handle", "face": "front", "parent": "door"}]
               if (handle and spec_handle is not False) or spec_handle else []),
            "joints": [{"id": "j", "type": "revolute", "moving_parts": ["door"], "hinge_edge": "left",
                        "opens_toward": "front", "range": 100}]}
    parts = [{"name": "carcass", "min": [-0.3, -0.28, 0], "max": [0.3, 0.3, 0.80]},
             {"name": "carcass_rail", "min": [-0.3, -0.28 + rail_recess, 0.80], "max": [0.3, 0.3, 0.86]},
             {"name": "worktop", "min": [-0.3, -0.31, 0.86], "max": [0.3, 0.3, 0.9]},
             {"name": "door", "min": [-0.299, -0.298, 0.1], "max": [0.299, -0.28, door_top]}]
    if handle:
        parts.append(handle)
    parts.extend(extra or [])
    rep = spec_check.check(parts, spec, {"width": 0.6, "depth": 0.62, "height": 0.9})
    return [v["id"] for v in rep["hard"] if v["id"].startswith("sim2real")]


def test_flush_handleless_door_is_not_graspable():
    assert _grasp_case(door_top=0.858)


def test_gap_without_room_behind_is_not_graspable():
    assert _grasp_case(door_top=0.835, rail_recess=0.0)


def test_j_pull_with_recessed_rail_is_graspable():
    assert not _grasp_case(door_top=0.835, rail_recess=0.03)


def test_flat_handle_is_not_graspable_but_proud_bar_is():
    flat = {"name": "door_handle", "min": [0.2, -0.305, 0.6], "max": [0.215, -0.298, 0.8]}
    bar = {"name": "door_handle", "min": [0.2, -0.33, 0.6], "max": [0.215, -0.298, 0.8]}
    assert _grasp_case(door_top=0.858, handle=flat)
    assert not _grasp_case(door_top=0.858, handle=bar)


def test_handle_may_leave_the_envelope_by_the_allowance():
    import spec_check
    spec = {"parts": [{"id": "body", "kind": "housing", "face": "front"}], "joints": []}
    P = {"width": 0.5, "depth": 0.4, "height": 0.3}
    parts = [{"name": "body", "min": [-0.25, -0.2, 0], "max": [0.25, 0.2, 0.3]},
             {"name": "door_handle", "min": [0.1, -0.235, 0.1], "max": [0.12, -0.2, 0.25]}]
    ids = [v["id"] for v in spec_check.check(parts, spec, P)["hard"]]
    assert not any(i.startswith("envelope") for i in ids)
    parts[1]["min"][1] = -0.30
    ids = [v["id"] for v in spec_check.check(parts, spec, P)["hard"]]
    assert any(i.startswith("envelope.hardware") for i in ids)


def test_handle_the_video_does_not_show_is_rejected():
    bar = {"name": "door_handle", "min": [0.2, -0.33, 0.6], "max": [0.215, -0.298, 0.8]}
    ids = _grasp_case(door_top=0.858, handle=bar, spec_handle=False)
    assert any(i.startswith("sim2real.extra_hardware") for i in ids)


def test_hidden_grip_channel_is_graspable():
    # the door panel is split: a 30 mm channel along its free edge, recessed 18 mm
    door_parts = [{"name": "door", "min": [-0.299, -0.298, 0.1], "max": [0.26, -0.28, 0.858]},
                  {"name": "door_grip_channel", "min": [0.26, -0.28, 0.4], "max": [0.29, -0.275, 0.8]},
                  {"name": "door_edge_lip", "min": [0.29, -0.298, 0.1], "max": [0.299, -0.28, 0.858]}]
    import spec_check
    spec = {"parts": [{"id": "carcass", "kind": "housing", "face": "front"},
                      {"id": "door", "kind": "door", "face": "front"},
                      {"id": "door_grip_channel", "kind": "recess", "face": "front", "parent": "door"}],
            "joints": [{"id": "j", "type": "revolute", "moving_parts": ["door", "door_grip_channel"],
                        "hinge_edge": "left", "opens_toward": "front", "range": 100}]}
    parts = [{"name": "carcass", "min": [-0.3, -0.27, 0], "max": [0.3, 0.3, 0.86]}] + door_parts
    ids = [v["id"] for v in spec_check.check(parts, spec, {"width": 0.6, "depth": 0.62, "height": 0.9})["hard"]]
    assert not any(i.startswith("sim2real.grasp") for i in ids), ids
