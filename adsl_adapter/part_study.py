"""Part study: read controls, openings and basins off the sharpest video close-ups,
cross-check the reading, and align the functional spec's text with it.

Why: the dispenser's spec said three touch keys. The whole panel, seen face-on
from the door (2-9 s) and up close (226 s), has FOUR keys around a round
display. The spec came from small keyframe crops and one angled close-up whose
left half was hidden; nobody counted again.

    python adsl_adapter/part_study.py --run pantry --only water_dispenser sink ...   # study + proposed patch
    python adsl_adapter/part_study.py --run pantry --only ... --apply                 # write the patch into spec.json

Inputs: runtime/video/<run>/closeups/<oid>[__<part>]/crop_*.jpg (geometry/closeup_frames.py).
Outputs: closeups/<oid>/study.json (three independent readings + consensus) and
closeups/<oid>/spec_patch.json (parts/joints to replace or add, with reasons).
"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

import vlm

ROOT = Path(__file__).resolve().parents[1]
CONTROL_KINDS = {"touch_key", "button", "knob", "switch", "dial", "display", "indicator", "lever"}
OPENING_KINDS = {"door", "drawer", "lid", "flap", "handle"}
BASIN_KINDS = {"basin", "recess", "outlet", "tap", "spout", "drain", "nozzle"}

STUDY_PROMPT = """These {n} images are crops of ONE object -- {category} -- taken from a phone video at different
moments (some far and face-on, some close and angled). Read it carefully, like an inspector writing a
specification that a modeller will follow exactly. Use every crop: the face-on ones show the whole layout,
the close ones show details; a part hidden in one crop may be visible in another.

{questions}

Rules: count items ONE BY ONE and say in which crop(s) (0-based index) you counted them. Transcribe printed
text exactly (Chinese and English). If you cannot read something, write "unreadable" -- never guess.
Positions are as seen standing in FRONT of the object (left/right are the object's own).
Answer with a json object:
{{"controls": {{"total_controls": int or null, "display": "shape/size/what it shows" or null,
               "items": [{{"kind": "touch_key|button|knob|switch|dial|indicator", "icon": "...", "label": "...",
                          "position": "e.g. left of the display, upper row", "u": 0-1, "v": 0-1, "seen_in": [..]}}],
               "other_text": ["..."], "logo": "..."}},
  "openings": [{{"part": "door|drawer|lid|flap", "count": int, "how_opened": "bar handle|D handle|curved handle|recessed grip|hidden groove|finger gap at top edge|push-to-open|none visible",
                "handle_shape": "...", "handle_position": "...", "hinge_side_evidence": "...", "seen_in": [..]}}],
  "basin": {{"bowls": int or null, "shape": "...", "mount": "undermount|top-mount (visible rim)|integrated|n/a",
            "drain": "type and position" or null, "overflow": "..." , "drainer_grooves_on_counter": true/false/null,
            "tap": "type and position" or null, "other": ["..."]}},
  "finish": {{"main_colour": "...", "finish": "matte|satin|glossy|mirror|brushed", "notes": "..."}},
  "uncertain": ["what you could not verify"]}}"""

ALIGN_PROMPT = """A modeller will build "{oid}" ({category}) from the specification below. A careful reading of
sharp video close-ups (three independent readings and their consensus) is given after it. Align the
specification with the reading: correct counts, labels, positions, shapes and the way things open; remove
parts the reading shows do not exist; add parts it shows that are missing. Only change what the reading
supports; keep everything else exactly as it is. Keep the spec's conventions: face front|top|left|right|back|
bottom|inside; u 0 = left .. 1 = right seen from outside that face; v 0 = bottom .. 1 = top of the whole
object; size_frac = [x, y, z] fractions of width, depth, height; numbered copies via "count".

CURRENT SPEC PARTS (relevant ones):
{parts}

CURRENT JOINTS:
{joints}

READING:
{reading}

Answer with a json object:
{{"remove": [{{"id": "...", "reason": "..."}}],
  "replace": [{{"id": "...", "part": {{full part object}}, "reason": "..."}}],
  "add": [{{"part": {{full part object with id, kind, count, parent, face, u, v, size_frac, shape, material_class,
             labels, function, interaction}}, "reason": "..."}}],
  "joints_replace": [{{"id": "...", "joint": {{full joint object}}, "reason": "..."}}],
  "joints_add": [{{"joint": {{...}}, "reason": "..."}}],
  "joints_remove": [{{"id": "...", "reason": "..."}}],
  "summary": "one paragraph: what changed and why"}}"""


def questions_for(spec: dict, category: str) -> str:
    kinds = {p.get("kind") for p in spec.get("parts") or []}
    q = []
    if kinds & CONTROL_KINDS:
        q.append("CONTROLS: how many controls (buttons, touch keys, knobs, switches) are there IN TOTAL? List each "
                 "one: its icon, its printed label, and where it sits relative to the display (left/right, row). "
                 "What shape is the display and what does it show? Transcribe the logo and every printed text.")
    if kinds & OPENING_KINDS or "door" in category or "cabinet" in category or "fridge" in category \
            or "refrigerator" in category or "microwave" in category:
        q.append("OPENINGS: count the doors / drawers / lids. For EACH, how is it opened by a hand: a protruding "
                 "handle (shape, length, where), a recessed grip or hidden groove (where), a finger gap along an "
                 "edge, push-to-open, or nothing visible? Which side are the hinges on, and what shows it?")
    if kinds & BASIN_KINDS or "sink" in category or "basin" in category or "tap" in category or "faucet" in category:
        q.append("BASIN / TAP: how many bowls; bowl shape and depth; mounted under the counter (no rim on top) or on "
                 "top (visible metal rim); drain type and position in the bowl; overflow; are there drainer grooves "
                 "cut into the counter; the tap's type (gooseneck, pull-out, single lever...) and where it stands.")
    q.append("FINISH: main colour and finish of the body.")
    return "\n".join(q)


def crops_for(run: Path, oid: str) -> tuple[list[Path], list[Path]]:
    whole = sorted((run / "closeups" / oid).glob("crop_*.jpg"))
    parts = sorted(p for d in (run / "closeups").glob(f"{oid}__*") for p in d.glob("crop_*.jpg"))
    return whole, parts


def study(run: Path, oid: str, refresh: bool = False) -> dict:
    spec = json.loads((run / "objects" / oid / "spec.json").read_text())
    category = ((spec.get("object") or {}).get("category") or oid).lower()
    whole, parts = crops_for(run, oid)
    sets = [parts[:5] or whole[:5], whole[:5], (parts[5:8] + whole[5:8]) or whole[:5]]
    readings = []
    for k, crops in enumerate(sets):
        crops = [c for c in crops if c.is_file()][:6]
        if not crops:
            continue
        prompt = STUDY_PROMPT.format(n=len(crops), category=category, questions=questions_for(spec, category))
        ans = vlm.ask_json(prompt, [(f"crop_{i}", c) for i, c in enumerate(crops)], stage=f"partstudy_{oid}_{k}",
                           cache_dir=run / "vlm_calls", refresh=refresh, max_tokens=3500, max_side=1100)
        readings.append({"set": k, "crops": [c.name for c in crops], "reading": ans["data"]})
    # consensus on the numbers that matter: total controls, openings counts / how opened, bowls, mount
    def vals(f):
        return [f(r["reading"]) for r in readings if r.get("reading")]
    def vote(xs):
        xs = [x for x in xs if x not in (None, "", [])]
        if not xs:
            return None, []
        best = max(set(map(json.dumps, xs)), key=lambda v: sum(json.dumps(x) == v for x in xs))
        return json.loads(best), xs
    tot, tot_all = vote(vals(lambda r: (r.get("controls") or {}).get("total_controls")))
    bowls, _ = vote(vals(lambda r: (r.get("basin") or {}).get("bowls")))
    mount, _ = vote(vals(lambda r: (r.get("basin") or {}).get("mount")))
    grooves, _ = vote(vals(lambda r: (r.get("basin") or {}).get("drainer_grooves_on_counter")))
    opened, _ = vote(vals(lambda r: sorted({str(x.get("how_opened")) for x in (r.get("openings") or [])})))
    consensus = {"total_controls": tot, "total_controls_readings": tot_all, "bowls": bowls, "mount": mount,
                 "drainer_grooves_on_counter": grooves, "how_opened": opened,
                 "agree_on_controls": len(set(map(str, tot_all))) <= 1 if tot_all else None}
    out = {"object": oid, "readings": readings, "consensus": consensus}
    (run / "closeups" / oid).mkdir(parents=True, exist_ok=True)
    (run / "closeups" / oid / "study.json").write_text(json.dumps(out, indent=1, ensure_ascii=False))
    return out


def align(run: Path, oid: str, st: dict, refresh: bool = False) -> dict:
    spec = json.loads((run / "objects" / oid / "spec.json").read_text())
    category = ((spec.get("object") or {}).get("category") or oid)
    keep = ("id", "kind", "count", "parent", "face", "u", "v", "size_frac", "shape", "material_class", "labels",
            "function", "interaction")
    parts = [{k: p.get(k) for k in keep} for p in spec.get("parts") or []]
    joints = [{k: j.get(k) for k in ("id", "type", "moving_parts", "hinge_edge", "opens_toward", "axis", "range")}
              for j in spec.get("joints") or []]
    reading = {"consensus": st["consensus"], "readings": [r["reading"] for r in st["readings"]]}
    prompt = ALIGN_PROMPT.format(oid=oid, category=category, parts=json.dumps(parts, ensure_ascii=False),
                                 joints=json.dumps(joints, ensure_ascii=False),
                                 reading=json.dumps(reading, ensure_ascii=False)[:14000])
    ans = vlm.ask_json(prompt, [], stage=f"partalign_{oid}", cache_dir=run / "vlm_calls", refresh=refresh,
                       max_tokens=6000)
    patch = ans["data"] or {}
    (run / "closeups" / oid / "spec_patch.json").write_text(json.dumps(patch, indent=1, ensure_ascii=False))
    return patch


def fill_material_keys(spec: dict) -> None:
    """Parts added by the study carry a class and maybe a colour, not a key; the
    forge prompt and the report need a key on every part. Derive it from the
    part's own observation, else inherit its parent's."""
    import material_spec
    by_id = {p["id"]: p for p in spec.get("parts") or []}
    for p in spec.get("parts") or []:
        if p.get("material_key"):
            continue
        mv = p.get("material_vlm") or {}
        parent = by_id.get(p.get("parent") or "")
        if mv.get("hex") or p.get("hex"):
            p["material_key"] = material_spec.from_observation(
                mv.get("class") or p.get("material_class"), mv.get("hex") or p.get("hex"), mv.get("finish"),
                emissive=bool(p.get("emissive")), emit_hex=mv.get("emit_hex"))
        elif parent and parent.get("material_key"):
            p["material_key"] = parent["material_key"]
        else:
            p["material_key"] = material_spec.from_observation(p.get("material_class") or "plastic", "#808080")


def apply_patch(run: Path, oid: str) -> dict:
    sp = run / "objects" / oid / "spec.json"
    spec = json.loads(sp.read_text())
    patch = json.loads((run / "closeups" / oid / "spec_patch.json").read_text())
    backup = sp.with_name("spec_before_part_study.json")
    if not backup.exists():
        backup.write_text(json.dumps(spec, indent=1, ensure_ascii=False))
    parts = {p["id"]: p for p in spec.get("parts") or []}
    order = [p["id"] for p in spec.get("parts") or []]
    for r in patch.get("remove") or []:
        parts.pop(r.get("id"), None)
    for r in patch.get("replace") or []:
        new = r.get("part") or {}
        if r.get("id") in parts and new:
            merged = copy.deepcopy(parts[r["id"]])
            merged.update({k: v for k, v in new.items() if v is not None})
            merged["id"] = new.get("id") or r["id"]
            if merged["id"] != r["id"]:
                parts.pop(r["id"])
                order.append(merged["id"])
            parts[merged["id"]] = merged
    for r in patch.get("add") or []:
        new = r.get("part") or {}
        if new.get("id") and new["id"] not in parts:
            new.setdefault("count", 1)
            new.setdefault("confidence", "high")
            new["source"] = "part_study"
            parts[new["id"]] = new
            order.append(new["id"])
    spec["parts"] = [parts[i] for i in dict.fromkeys(order) if i in parts]
    fill_material_keys(spec)
    joints = {j["id"]: j for j in spec.get("joints") or []}
    for r in patch.get("joints_remove") or []:
        joints.pop(r.get("id"), None)
    for r in patch.get("joints_replace") or []:
        if r.get("id") in joints and r.get("joint"):
            joints[r["id"]].update({k: v for k, v in r["joint"].items() if v is not None})
    for r in patch.get("joints_add") or []:
        j = r.get("joint") or {}
        if j.get("id") and j["id"] not in joints:
            joints[j["id"]] = j
    # moving parts that no longer exist leave the joint
    for j in joints.values():
        j["moving_parts"] = [m for m in j.get("moving_parts") or [] if m in parts]
    spec["joints"] = [j for j in joints.values() if j.get("moving_parts")]
    spec.setdefault("part_study", {})["applied"] = patch.get("summary")
    sp.write_text(json.dumps(spec, indent=2, ensure_ascii=False))
    return spec


TEXT_PROMPT = """The parts list below is the corrected, verified description of "{oid}" ({category}). The text fields
that describe the object were written earlier and may still mention parts that turned out not to exist or
describe them wrongly. Rewrite the text fields so they agree with the parts list exactly: never mention a part
or feature that is not in the list; keep the useful wording otherwise.
PARTS (verified): {parts}
REMOVED (verified NOT to exist -- must not be mentioned): {removed}
CURRENT TEXT FIELDS: {text}
Answer json: {{"subtype": "...", "summary": "two sentences", "how_it_is_used": ["..."], "uncertain": ["..."]}}"""


def align_text(run: Path, oid: str, refresh: bool = False) -> dict:
    """Bring the object's text (subtype, summary, usage, uncertainties) in line
    with the verified parts: the sink summary still promised 'a drainer-groove
    area', and the visual gate kept asking for it."""
    sp = run / "objects" / oid / "spec.json"
    spec = json.loads(sp.read_text())
    obj = spec.get("object") or {}
    parts = [{k: p.get(k) for k in ("id", "kind", "count", "face", "shape", "labels")} for p in spec.get("parts") or []]
    text = {"subtype": obj.get("subtype"), "summary": obj.get("summary"),
            "how_it_is_used": spec.get("how_it_is_used"), "uncertain": spec.get("uncertain")}
    ans = vlm.ask_json(TEXT_PROMPT.format(oid=oid, category=obj.get("category") or oid,
                                          parts=json.dumps(parts, ensure_ascii=False),
                                          removed=json.dumps(spec.get("removed_parts") or [], ensure_ascii=False),
                                          text=json.dumps(text, ensure_ascii=False)),
                       [], stage=f"textalign_{oid}", cache_dir=run / "vlm_calls", refresh=refresh, max_tokens=1500)
    new = ans["data"] or {}
    if new.get("summary"):
        obj["subtype"] = new.get("subtype") or obj.get("subtype")
        obj["summary"] = new["summary"]
        spec["object"] = obj
        if new.get("how_it_is_used") is not None:
            spec["how_it_is_used"] = new["how_it_is_used"]
        if new.get("uncertain") is not None:
            spec["uncertain"] = new["uncertain"]
        spec.setdefault("part_study", {})["text_aligned"] = True
        sp.write_text(json.dumps(spec, indent=2, ensure_ascii=False))
    return new


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--only", nargs="+", required=True)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--align-text", action="store_true", help="rewrite the object's text to agree with its parts")
    ap.add_argument("--refresh", action="store_true")
    a = ap.parse_args()
    run = Path(a.run) if Path(a.run).is_dir() else ROOT / "runtime/video" / a.run
    if not a.apply and not a.align_text:
        vlm.ensure_vision(vlm.vision_model())
    for oid in a.only:
        if a.apply:
            spec = apply_patch(run, oid)
            print(f"{oid}: applied -> {len(spec['parts'])} parts, {len(spec['joints'])} joints")
            continue
        if a.align_text:
            t = align_text(run, oid, a.refresh)
            print(f"{oid}: {t.get('subtype')} | {t.get('summary')}")
            continue
        st = study(run, oid, a.refresh)
        c = st["consensus"]
        print(f"== {oid}: controls {c['total_controls']} (readings {c['total_controls_readings']}), bowls {c['bowls']}, "
              f"mount {c['mount']}, grooves {c['drainer_grooves_on_counter']}, opened {c['how_opened']}")
        patch = align(run, oid, st, a.refresh)
        print("   patch:", str(patch.get("summary"))[:600])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
