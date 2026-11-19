"""Bounded model repair of invalid book thicknesses; no whole-plan rewrite."""
import asyncio
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'aDSL/adsl-agents'))
from utils.config import ModelProfile

async def main():
    out=ROOT/'FineScene/runs/flash_bookshelf_20261020_01'
    design=json.loads(json.loads((out/'design_correction_1.json').read_text(encoding='utf-8'))['content'])
    failures=[]
    for li,l in enumerate(design['levels']):
        for gi,g in enumerate(l['groups']):
            if g['kind']!='stack':continue
            for bi,b in enumerate(g['books']):
                if not .012<=b[2]<=.065:failures.append({'level':li,'group':gi,'book':bi,'size':b[:3]})
    prompt={'task':'Fix only the invalid horizontal book thicknesses. Units metres; these caused actual aDSL collisions with the shelf above. Width and depth stay unchanged. Return JSON patches [{level,group,book,height}] with height 0.020..0.045. The 72-book inventory is accepted; do not change count.','failures':failures}
    profile=ModelProfile.load(ROOT/'FineScene/configs/deepseek-flash.yaml')
    async with profile.client() as client:
        response=await client.chat.completions.create(model=profile.model,messages=[{'role':'user','content':json.dumps(prompt)}],
            response_format={'type':'json_object'},max_tokens=2000,extra_body={'thinking':{'type':'disabled'}})
    raw=response.choices[0].message.content
    (out/'size_repair_response.json').write_text(json.dumps({'request':prompt,'model':response.model,'usage':response.usage.model_dump(),'content':raw},indent=2),encoding='utf-8')
    patches=json.loads(raw)['patches']
    expected={(f['level'],f['group'],f['book']) for f in failures}
    if {(p['level'],p['group'],p['book']) for p in patches}!=expected or len(patches)!=len(expected):raise ValueError('Invalid patch scope')
    for p in patches:
        if not .020<=p['height']<=.045:raise ValueError('Invalid repaired thickness')
        design['levels'][p['level']]['groups'][p['group']]['books'][p['book']][2]=p['height']
    (out/'repaired_design.json').write_text(json.dumps(design,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(patches))

if __name__=='__main__':asyncio.run(main())
