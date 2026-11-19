"""One local HTML page with everything the pipeline read from the video.

The representation is only useful if a person can check it: which keyframes
were used, what each object was read as (parts, counts, labels, joints and
the evidence crops they came from), which colours were measured and which
were guessed, where the lights are and how the room was laid out. Written to
runtime/video/<run>/report.html; images are referenced relatively, so the
folder can be copied as a whole.
"""
from __future__ import annotations

import html
import json
import math
from pathlib import Path

import material_spec

CSS = """
:root{--fg:#1d1d1f;--mut:#6b6b70;--bd:#e3e3e6;--bg:#fbfbfa;--card:#fff;--acc:#2b6cb0}
*{box-sizing:border-box}body{margin:0;font:14px/1.5 -apple-system,Segoe UI,Roboto,"Noto Sans SC",sans-serif;color:var(--fg);background:var(--bg)}
main{max-width:1180px;margin:0 auto;padding:24px 16px 64px}h1{font-size:22px;margin:0 0 4px}
h2{font-size:17px;margin:32px 0 10px;border-bottom:1px solid var(--bd);padding-bottom:6px}
h3{font-size:15px;margin:18px 0 6px}.mut{color:var(--mut)}.card{background:var(--card);border:1px solid var(--bd);border-radius:10px;padding:14px 16px;margin:12px 0}
table{border-collapse:collapse;width:100%;font-size:12.5px}th,td{border-bottom:1px solid var(--bd);padding:5px 6px;text-align:left;vertical-align:top}
th{font-weight:600;color:var(--mut)}.sw{display:inline-block;width:14px;height:14px;border-radius:3px;border:1px solid #0002;vertical-align:-2px;margin-right:4px}
.crops img{height:180px;margin:4px 6px 4px 0;border-radius:6px;border:1px solid var(--bd)}.tag{display:inline-block;font-size:11px;padding:1px 6px;border-radius:9px;background:#eef2f7;margin:1px 3px 1px 0}
.low{background:#fdeceb}.med{background:#fff6e0}.high{background:#e8f6ec}code{font-size:12px;background:#f3f3f5;padding:1px 4px;border-radius:4px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:10px}img.sheet{max-width:100%;border-radius:8px;border:1px solid var(--bd)}
"""


def _e(x) -> str:
    return html.escape(str(x if x is not None else ""))


def _sw(hexa) -> str:
    h = material_spec._clean_hex(hexa, "")
    return f'<span class="sw" style="background:{h}"></span>' if h else ""


def _key_hex(key) -> str | None:
    spec = material_spec.parse(key) if key else None
    return spec["hex"] if spec else None


def _load(p: Path):
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None


def lighting_html(app: dict) -> str:
    L, cam = app["lighting"], app["camera"]
    rows = "".join(
        f"<tr><td>{_e(f.get('id'))}</td><td>{_e(f.get('type'))}</td><td>{_e(f.get('count'))}</td>"
        f"<td>{_e(f.get('mount'))}</td><td>{_e(json.dumps(f.get('housing'), ensure_ascii=False))}</td>"
        f"<td>{_e(f.get('drop_m'))}</td><td>{_e(f.get('beam'))}</td><td>{_e(f.get('cct_k'))} K</td>"
        f"<td>{'on' if f.get('on', True) else 'off'}</td><td>{_e(f.get('arrangement'))}</td></tr>"
        for f in L.get("fixtures") or [])
    prac = ", ".join(f"{_sw(p.get('colour_hex'))}{_e(p.get('object'))} / {_e(p.get('part'))}"
                     for p in L.get("practicals") or []) or "none"
    return (f'<div class="card"><b>{_e(L["time_of_day"]).upper()}</b> — {_e(L.get("time_evidence"))}'
            f'<br><span class="mut">shadows: {_e(L.get("shadows"))}; {_e(L.get("distribution"))}</span>'
            f'<table><tr><th>id</th><th>type</th><th>n</th><th>mount</th><th>housing</th><th>drop m</th>'
            f'<th>beam</th><th>CCT</th><th>state</th><th>arrangement</th></tr>{rows}</table>'
            f'<p>Small emitters: {prac}</p>'
            f'<p>Camera: white balance <b>{cam.get("white_balance_k")} K</b> (lamps {cam.get("dominant_light_k")} K, '
            f'residual cast {cam.get("residual_rgb")}), white wall at sRGB {cam.get("exposure_anchor_srgb")} '
            f'over {cam.get("anchors")} anchor patches.</p></div>')


def materials_html(app: dict) -> str:
    rows = []
    for m in app["materials"].values():
        me = m.get("measured") or {}
        rows.append(
            f"<tr><td>{_e(m['id'])}</td><td>{_e(m['class'])} / {_e(m.get('finish'))}</td>"
            f"<td>{_sw(m.get('vlm_hex'))}{_e(m.get('vlm_hex'))}</td>"
            f"<td>{_sw(me.get('display_hex'))}{_e(me.get('display_hex'))}</td>"
            f"<td>{_sw(_key_hex(m['key']))}<code>{_e(m['key'])}</code></td>"
            f"<td>{_e(me.get('ratio_to_white'))}</td>"
            f"<td class='mut'>{_e('; '.join((m.get('key_notes') or []) + (me.get('notes') or [])))}</td></tr>")
    return ('<div class="card"><table><tr><th>material</th><th>class / finish</th><th>model saw</th>'
            '<th>pixels</th><th>key used</th><th>vs white</th><th>how</th></tr>'
            + "".join(rows) + "</table></div>")


def object_html(run: Path, oid: str, spec: dict, forge: dict | None) -> str:
    obj = spec.get("object") or {}
    d = spec["dimensions_m"]
    crops = "".join(f'<img src="objects/{_e(oid)}/{_e(v["file"])}" title="{_e(v["frame"])} t={v["t"]}s">'
                    for v in spec.get("evidence") or [])
    for a in spec.get("audits") or []:
        crops += f'<img src="objects/{_e(oid)}/{_e(a["image"])}" title="control close-up {_e(a.get("frame"))}">'
    prow = []
    for p in spec["parts"]:
        conf = {"low": "low", "medium": "med", "high": "high"}.get(p["confidence"], "med")
        lab = ", ".join(p.get("labels") or [])
        if p.get("labels_audit") and p.get("labels_audit") != p.get("labels"):
            lab += f' <span class="mut">(close-up: {_e(", ".join(p["labels_audit"]))})</span>'
        prow.append(
            f"<tr><td><code>{_e(p['id'])}</code></td><td>{_e(p['kind'])}</td><td>{p['count']}</td>"
            f"<td>{_e(p['face'])} u{p['u']:.2f} v{p['v']:.2f}</td>"
            f"<td>{_sw(_key_hex(p['material_key']))}<code>{_e(p['material_key'])}</code>"
            f"{' ✦' if p.get('emissive') else ''}</td><td>{_e(p['function'])}</td><td>{lab}</td>"
            f"<td><span class='tag {conf}'>{_e(p['confidence'])}</span></td></tr>")
    jrow = "".join(
        f"<tr><td><code>{_e(j['id'])}</code></td><td>{_e(j['type'])}</td><td>{_e(', '.join(j['moving_parts']))}</td>"
        f"<td>{_e(j.get('hinge_edge') or '')}</td><td>{_e(j.get('axis') or '')}</td><td>{_e(j.get('opens_toward') or '')}</td>"
        f"<td>{_e(j['range'])}</td><td><span class='tag'>{_e(j.get('source'))}</span>"
        f"<span class='tag'>{_e(j.get('confidence'))}</span></td><td class='mut'>{_e(j.get('evidence'))}</td></tr>"
        for j in spec.get("joints") or [])
    changes = [c for a in spec.get("audits") or [] for c in a.get("changes") or []]
    forge_txt = ""
    if forge:
        forge_txt = (f"<p><b>forge:</b> ok={forge.get('ok')} attempts={forge.get('attempts')} "
                     f"spec coverage={_e(forge.get('spec'))} joints={forge.get('joints')}</p>")
        views = Path(forge.get("blend") or "").parent
        imgs = "".join(f'<img src="{_e(views / f"view_{v}.png")}">' for v in ("hero", "front")
                       if (views / f"view_{v}.png").is_file())
        if imgs:
            forge_txt += f'<div class="crops">{imgs}</div>'
    return (f'<div class="card"><h3>{_e(oid)} <span class="mut">— {_e(obj.get("category"))} / '
            f'{_e(obj.get("subtype"))}, {d["width"]:.2f} × {d["depth"]:.2f} × {d["height"]:.2f} m</span></h3>'
            f'<p>{_e(obj.get("summary"))}</p><div class="crops">{crops}</div>'
            f'<table><tr><th>part</th><th>kind</th><th>n</th><th>where</th><th>material</th><th>function</th>'
            f'<th>labels / icons</th><th>conf.</th></tr>{"".join(prow)}</table>'
            + (f'<h3>structure</h3><table><tr><th>joint</th><th>type</th><th>moves</th><th>hinge edge</th>'
               f'<th>axis</th><th>opens</th><th>range</th><th>source</th><th>evidence</th></tr>{jrow}</table>'
               if jrow else '<p class="mut">no moving parts</p>')
            + (f'<p><b>use:</b> {_e(" → ".join(spec.get("how_it_is_used") or []))}</p>')
            + (f'<p class="mut"><b>not verifiable from the video:</b> {_e("; ".join(spec.get("uncertain") or []))}</p>')
            + (f'<p class="mut"><b>checks:</b> {_e("; ".join((spec.get("checks") or []) + changes))}</p>'
               if (spec.get("checks") or changes) else "")
            + forge_txt + "</div>")


def plan_svg(lay: dict) -> str:
    r = lay["room"]
    W, D = r["width"], r["depth"]
    s = 520.0 / max(W, D)
    pad = 30

    def X(x):
        return pad + (x + W / 2) * s

    def Y(y):
        return pad + (D / 2 - y) * s

    out = [f'<svg viewBox="0 0 {W * s + 2 * pad:.0f} {D * s + 2 * pad:.0f}" width="100%" '
           f'style="max-width:{W * s + 2 * pad:.0f}px;background:#fff;border:1px solid #e3e3e6;border-radius:8px">',
           f'<rect x="{X(-W / 2):.1f}" y="{Y(D / 2):.1f}" width="{W * s:.1f}" height="{D * s:.1f}" '
           f'fill="#f6f6f4" stroke="#333" stroke-width="3"/>',
           f'<text x="{X(0):.0f}" y="{Y(D / 2) - 8:.0f}" font-size="12" text-anchor="middle">north</text>']
    for op in lay.get("openings") or []:
        wall, c = op["wall"], op["offset_m"]
        import video_layout
        axis, coord = video_layout.along(wall, c, W, D)
        half = op["width_m"] / 2
        col = "#2b6cb0" if op["kind"] == "window" else "#b7791f"
        if axis == "x":
            y = D / 2 if wall == "north" else -D / 2
            out.append(f'<line x1="{X(coord - half):.1f}" y1="{Y(y):.1f}" x2="{X(coord + half):.1f}" '
                       f'y2="{Y(y):.1f}" stroke="{col}" stroke-width="7"/>')
        else:
            x = W / 2 if wall == "east" else -W / 2
            out.append(f'<line x1="{X(x):.1f}" y1="{Y(coord - half):.1f}" x2="{X(x):.1f}" '
                       f'y2="{Y(coord + half):.1f}" stroke="{col}" stroke-width="7"/>')
    for o in lay["objects"].values():
        w, d = o["dims"][0], o["dims"][1]
        a = math.radians(o["yaw_deg"])
        pts = []
        for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
            lx, ly = sx * w / 2, sy * d / 2
            pts.append((o["x"] + lx * math.cos(a) - ly * math.sin(a),
                        o["y"] + lx * math.sin(a) + ly * math.cos(a)))
        fill = "#cfe0f5" if o.get("on") else ("#e8dcc8" if o.get("mount") == "wall" else "#d9d9d9")
        out.append('<polygon points="' + " ".join(f"{X(x):.1f},{Y(y):.1f}" for x, y in pts)
                   + f'" fill="{fill}" fill-opacity="0.75" stroke="#555"/>')
        out.append(f'<text x="{X(o["x"]):.0f}" y="{Y(o["y"]):.0f}" font-size="10" '
                   f'text-anchor="middle">{_e(o["id"])}</text>')
    for li in lay.get("lights") or []:
        out.append(f'<circle cx="{X(li["x"]):.1f}" cy="{Y(li["y"]):.1f}" r="7" fill="#f6e05e" stroke="#975a16"/>')
    out.append("</svg>")
    return "".join(out)


def write(run: Path) -> Path:
    frames = _load(run / "frames" / "frames.json") or {}
    sheets = _load(run / "sheets" / "sheets.json") or []
    inv = _load(run / "survey.json") or {"objects": []}
    app = _load(run / "appearance.json")
    lay = _load(run / "layout.json")
    forge = _load(run / "forge_results.json") or {}
    build = _load(run / "scene" / "scene_build.json")
    info = frames.get("info") or {}
    parts = [f"<!doctype html><html lang='zh'><head><meta charset='utf-8'>"
             f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
             f"<title>Video reading — {_e(run.name)}</title><style>{CSS}</style></head><body><main>",
             f"<h1>Video → scene reading: {_e(run.name)}</h1>",
             f"<p class='mut'>{_e(Path(frames.get('video', '')).name)} · {info.get('width')}×{info.get('height')} · "
             f"{info.get('duration', 0):.0f} s · {len(frames.get('frames') or [])} keyframes · "
             f"{len(inv['objects'])} objects</p>"]
    if build:
        renders = "".join(f'<img class="sheet" src="scene/{Path(p).name}" style="max-height:420px">'
                          for p in build.get("renders") or [])
        parts.append(f"<h2>Assembled scene</h2><div class='card'>{renders}<p class='mut'>"
                     f"{_e(json.dumps({k: build.get(k) for k in ('room', 'lights', 'exposure', 'calibration')}, default=str))}</p></div>")
    if app:
        parts += ["<h2>Lighting</h2>", lighting_html(app), "<h2>Materials</h2>", materials_html(app)]
    if lay:
        issues = "".join(f"<li>{_e(i['id'])}: {_e(i['message'])}</li>" for i in lay.get("issues") or [])
        r = lay["room"]
        parts.append(f"<h2>Layout</h2><div class='card'><p>Room {r['width']:.2f} × {r['depth']:.2f} × "
                     f"{r['height']:.2f} m <span class='mut'>({_e(r.get('basis'))})</span></p>"
                     f"{plan_svg(lay)}<ul class='mut'>{issues}</ul></div>")
    parts.append("<h2>Objects</h2>")
    for o in inv["objects"]:
        spec = _load(run / "objects" / o["id"] / "spec.json")
        if spec:
            parts.append(object_html(run, o["id"], spec, forge.get(o["id"])))
        else:
            parts.append(f"<div class='card'><b>{_e(o['id'])}</b> <span class='mut'>{_e(o['category'])} — "
                         f"{_e(o['brief'])} (priority {o['priority']}, no spec)</span></div>")
    parts.append("<h2>Keyframes</h2>")
    parts += [f"<img class='sheet' src='sheets/{_e(s['file'])}'>" for s in sheets]
    parts.append("</main></body></html>")
    out = run / "report.html"
    out.write_text("\n".join(parts), encoding="utf-8")
    return out
