# PBR materials (textures not committed)

`src/material_catalog.py` loads textures from `assets/materials/<Name>/`.
The image files are not in git. Each folder has a `provenance.json` that gives
the source, licence and a direct `download` URL. To restore one, download the
zip and unpack its files into that folder. For example, Wood051 (CC0, ambientCG):

```bash
curl -L -o Wood051.zip "https://ambientcg.com/get?file=Wood051_1K-JPG.zip"
unzip Wood051.zip -d FineScene/assets/materials/Wood051
```
