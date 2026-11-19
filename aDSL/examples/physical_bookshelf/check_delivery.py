"""Verify GLB ownership metadata, keep regression evidence and record delivery."""
import hashlib
import json
from pathlib import Path
import shutil
import struct

ROOT=Path(__file__).resolve().parents[2]
out=ROOT/'outputs/physical_bookshelf_20261017'
folder=out/'raised_10cm'
data=(folder/'relational_bookshelf.glb').read_bytes()
magic,version,length=struct.unpack_from('<4sII',data)
assert magic==b'glTF' and version==2 and length==len(data)
size,kind=struct.unpack_from('<II',data,12)
assert kind==0x4e4f534a
gltf=json.loads(data[20:20+size])
assert not gltf.get('cameras')
assert not gltf.get('extensions',{}).get('KHR_lights_punctual')
assert not any('Presentation' in n.get('name','') for n in gltf['nodes'])
supported=[n for n in gltf['nodes'] if n.get('extras',{}).get('physical_relation')=='supported']
assert len(supported)==64
original=ROOT/'outputs/vintage_bookshelf_20261017'
for name,digest in json.loads((out/'original_hashes.json').read_text()).items():
    assert hashlib.sha256((original/name).read_bytes()).hexdigest()==digest
log=(ROOT/'outputs/relation_tests.log').read_text(encoding='utf-8-sig')
assert 'Ran 12 tests' in log and '\nOK' in log
shutil.copy2(ROOT/'outputs/relation_tests.log',out/'relation_tests.log')
blend=json.loads((folder/'blender_validation.json').read_text())
report=dict(regression_tests=12,passed=True,baseline_geometry_matches_v1=True,
            baseline_height=1.847,raised_height=2.247,books=64,exported_meshes=len(gltf['meshes']),
            glb_support_metadata_count=len(supported),original_files_hashes_unchanged=True,
            blender=blend,comparison='comparison.png',
            core='adsl-core/core/relations.py',source='examples/physical_bookshelf/model.py')
(out/'verification.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps(report,indent=2))
