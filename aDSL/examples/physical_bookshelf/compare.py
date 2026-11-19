"""Create an annotated before/after plate at one common physical scale.

Run with the bundled image/document Python (Pillow); no project dependency.
"""
import hashlib
import json
from pathlib import Path
from PIL import Image,ImageDraw,ImageFont

ROOT=Path(__file__).resolve().parents[2]
out=ROOT/'outputs/physical_bookshelf_20261017'
original=ROOT/'outputs/vintage_bookshelf_20261017'
new=out/'raised_10cm'
left=Image.open(original/'front.png').convert('RGB')
right=Image.open(new/'front.png').convert('RGB')
# Front views use slightly different baseline orthographic scales; compensate
# from actual camera scales instead of simply resizing by the object heights.
ratio=2.15/(2.247*1.164)
small=left.resize((round(left.width*ratio),round(left.height*ratio)),Image.Resampling.LANCZOS)
plate=Image.new('RGB',(2320,1470),(34,33,31))
plate.paste(small,((1160-small.width)//2,136+round(1230*(1-ratio))))
plate.paste(right,(1160,136))
draw=ImageDraw.Draw(plate)
font=ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',35)
sub=ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',22)
draw.text((80,30),'V1 original bookshelf · 1.847 m',font=font,fill=(237,225,204))
draw.text((1240,30),'V2 +0.1 m per tier · 2.247 m',font=font,fill=(237,225,204))
draw.text((80,84),'Same physical scale; original preserved',font=sub,fill=(191,181,160))
draw.text((1240,84),'Relation-driven rebuild; size and placement of the 64 books preserved',font=sub,fill=(191,181,160))
plate.save(out/'comparison.png')
files=['model.py','model_plan.json','hero.png','front.png','vintage_bookshelf.blend','vintage_bookshelf.glb']
manifest={name:hashlib.sha256((original/name).read_bytes()).hexdigest() for name in files}
(out/'original_hashes.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
print(out/'comparison.png')
