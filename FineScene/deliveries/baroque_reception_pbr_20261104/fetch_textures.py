"""Fetch real CC0 PBR texture sets from Poly Haven for the Baroque reception room.

No TRELLIS, no generated meshes. Every map below is downloaded from Poly Haven's
public CDN and recorded in texture_manifest.json with its exact source URL.

Usage:  <aDSL venv python> fetch_textures.py
"""
import json
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
RESEARCH = os.path.join(ROOT, "_research")
TEXDIR = os.path.join(ROOT, "textures")
os.makedirs(TEXDIR, exist_ok=True)

CURL = shutil.which("curl") or r"C:\Windows\System32\curl.exe"

# logical material -> Poly Haven asset id
MATERIALS = {
    "floor_parquet":      "herringbone_parquet",
    "boiserie_dark":      "dark_wood",
    "boiserie_walnut":    "black_walnut_veneer_02",
    "furniture_rosewood": "rosewood_veneer1",
    "marble_cream":       "marble_01",
    "velvet_crimson":     "velour_velvet",
    "damask_crimson":     "quatrefoil_jacquard_fabric",
    "stucco_white":       "white_stucco",
    "gilded_bronze":      "metal_plate",
    "relief_plaster":     "patterned_clay_plaster",
}

# channel in Poly Haven files API -> short suffix used in the .blend material
CHANNELS = [
    ("Diffuse",      "diff"),
    ("nor_gl",       "nor"),
    ("Rough",        "rough"),
    ("Displacement", "disp"),
    ("AO",           "ao"),
]

RES_PREFERENCE = ("2k", "1k")


def pick(files, channel):
    node = files.get(channel)
    if not isinstance(node, dict):
        return None
    for res in RES_PREFERENCE:
        bucket = node.get(res)
        if isinstance(bucket, dict) and isinstance(bucket.get("jpg"), dict):
            entry = bucket["jpg"]
            if entry.get("url"):
                return res, entry["url"]
    return None


def download(url, dest):
    if os.path.exists(dest) and os.path.getsize(dest) > 4096:
        return "cached"
    tmp = dest + ".part"
    cmd = [CURL, "-sS", "--ssl-no-revoke", "-L", "-m", "180", "-o", tmp, url]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 or not os.path.exists(tmp) or os.path.getsize(tmp) < 4096:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise RuntimeError("curl failed for %s: %s" % (url, r.stderr[:200]))
    os.replace(tmp, dest)
    return "fetched"


def main():
    manifest = {
        "source": "Poly Haven (https://polyhaven.com)",
        "license": "CC0 1.0 Universal",
        "license_url": "https://polyhaven.com/license",
        "trellis_used": False,
        "note": ("Real scanned/photographed PBR texture sets. gilded_bronze uses "
                 "metal_plate's real normal/roughness/AO maps with a gold metallic "
                 "tint in the shader; no generated geometry and no TRELLIS."),
        "materials": {},
    }
    total = 0
    for logical, asset in MATERIALS.items():
        meta_path = os.path.join(RESEARCH, "files_%s.json" % asset)
        if not os.path.exists(meta_path):
            raise SystemExit("missing files manifest: " + meta_path)
        with open(meta_path, encoding="utf-8") as fh:
            files = json.load(fh)

        entry = {
            "asset_id": asset,
            "source_url": "https://polyhaven.com/a/" + asset,
            "resolution": None,
            "channels": {},
            "source_urls": {},
        }
        for channel, suffix in CHANNELS:
            got = pick(files, channel)
            if not got:
                print("  %-20s %-14s -- unavailable" % (logical, channel))
                continue
            res, url = got
            name = "%s_%s.jpg" % (logical, suffix)
            rel = "textures/" + name
            dest = os.path.join(TEXDIR, name)
            state = download(url, dest)
            size_kb = round(os.path.getsize(dest) / 1024, 1)
            print("  %-20s %-14s %s %6.1f KB  %s" % (logical, channel, res, size_kb, state))
            entry["channels"][suffix] = rel
            entry["source_urls"][suffix] = url
            entry["resolution"] = res
            total += 1
        manifest["materials"][logical] = entry

    out = os.path.join(ROOT, "texture_manifest.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, ensure_ascii=False)
    print("\nMAPS: %d  MATERIALS: %d  MANIFEST: %s" % (total, len(MATERIALS), out))


if __name__ == "__main__":
    main()
