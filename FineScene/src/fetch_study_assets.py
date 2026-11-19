"""Download source assets with checksums; never modify downloaded originals."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import finescene_env as fenv
import urllib.request

ROOT = fenv.STUDY_ASSETS
MODELS = ["WoodenTable_01", "WoodenChair_01", "decorative_book_set_01", "potted_plant_02",
          "vintage_oil_lamp", "side_table_tall_01", "fancy_picture_frame_01", "binder_notebook"]
TEXTURES = ["wood_floor", "wood_table_001", "plastered_wall_04", "fabric_pattern_07"]


def download(job):
    asset, relative, record = job
    path = (ROOT / asset / relative).resolve()
    if not path.is_relative_to((ROOT / asset).resolve()):
        raise ValueError("Asset include escapes its directory")
    path.parent.mkdir(parents=True, exist_ok=True)
    expected = record.get("md5")
    for attempt in range(3):
        try:
            if not path.is_file() or (expected and hashlib.md5(path.read_bytes()).hexdigest() != expected):
                request = urllib.request.Request(record["url"] + ("?finescene=2" if attempt else ""), headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(request, timeout=90) as response:
                    content = response.read()
                if expected and hashlib.md5(content).hexdigest() != expected:
                    raise ValueError(f"Download checksum mismatch: {asset}/{relative}, expected {expected}, got {hashlib.md5(content).hexdigest()}, bytes {len(content)}")
                path.write_bytes(content)
            return {"asset_id":asset,"path":str(path),"url":record["url"],"bytes":path.stat().st_size,
                    "sha256":hashlib.sha256(path.read_bytes()).hexdigest(),"checksum_verified":bool(expected)}
        except Exception:
            if attempt == 2:
                raise


def main():
    jobs, assets = [], []
    for name in MODELS + TEXTURES:
        data = json.loads((ROOT / f"{name}_files.json").read_text(encoding="utf-8"))
        form = "blend" if name == "decorative_book_set_01" else "gltf"
        record = data[form]["2k"][form]
        includes = record.get("include", {})
        if name in MODELS:
            jobs.append((name, record["url"].rsplit("/",1)[-1],record))
        for relative, item in includes.items():
            if name in MODELS or relative.startswith("textures/"):
                jobs.append((name,relative,item))
        assets.append({"asset_id":name,"provider":"Poly Haven","source_url":f"https://polyhaven.com/a/{name}",
                       "license":"CC0","license_url":"https://polyhaven.com/license","resolution":"2k"})
    data = json.loads((ROOT / "venice_sunset_files.json").read_text(encoding="utf-8"))
    hdr = data["hdri"]["2k"]["hdr"]
    jobs.append(("venice_sunset","venice_sunset_2k.hdr",hdr))
    assets.append({"asset_id":"venice_sunset","provider":"Poly Haven","license":"CC0","source_url":"https://polyhaven.com/a/venice_sunset"})
    statue_url = "https://microsoft.github.io/TRELLIS.2/assets/generation/52284bf45134c59a94be150a5b18b9cc3619ada4b30ded8d8d0288383b8c016f.glb"
    jobs.append(("trellis2_greek_statue","statue.glb",{"url":statue_url}))
    assets.append({"asset_id":"trellis2_greek_statue","provider":"Microsoft TRELLIS.2 official sample","source_url":statue_url,
                   "license":"Repository MIT; source sample recorded separately","license_url":"https://github.com/microsoft/TRELLIS.2/blob/main/LICENSE"})
    results = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        for index,result in enumerate(executor.map(download,jobs),1):
            results.append(result)
            if index % 20 == 0:
                print(f"Downloaded/verified {index}/{len(jobs)} files",flush=True)
    manifest = {"assets":assets,"files":results,"total_bytes":sum(r["bytes"] for r in results)}
    (ROOT / "download_manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"assets":len(assets),"files":len(results),"megabytes":manifest["total_bytes"]/1e6}),flush=True)


if __name__ == "__main__":
    main()
