"""Image-conditioned design brief; sends only the supplied renders to DeepSeek."""
import asyncio
import base64
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'aDSL/adsl-agents'))
from utils.config import ModelProfile

def validate(design):
    errors=[]
    count=sum(len(g['books']) for l in design['levels'] for g in l['groups'])
    if not 50<=count<=65:errors.append(f'Actual book count is {count}; must be 50..65.')
    for i,level in enumerate(design['levels']):
        occupied=0
        for group in level['groups']:
            books=group['books']
            occupied+=(sum(b[0]+.001 for b in books) if group['kind']=='upright' else max(b[0] for b in books))+group['gap_after']
            if any(not 0<=b[3]<=7 for b in books):errors.append(f'Level {i}: invalid palette index; 0..7 only.')
            if group['kind']=='stack' and any(b[0]>a[0] or b[1]>a[1] for a,b in zip(books,books[1:])):errors.append(f'Level {i}: stack sizes must descend.')
            if group['kind']=='upright' and any(b[2]>design['clearances'][i]-.025 for b in books):errors.append(f'Level {i}: book too tall.')
        if occupied>design['width']-.14:errors.append(f'Level {i}: used width {occupied:.3f} exceeds {design["width"]-.14:.3f}.')
    return errors

async def main():
    out=ROOT/'FineScene/runs/flash_bookshelf_20261020_01'
    out.mkdir(parents=True,exist_ok=True)
    reference=ROOT/'FineScene/runs/rich_books_vision_20261019_08/blender_export/render/detail_bookcase_right_front.png'
    prompt='''Inspect this previous bookshelf render. The user rejected a subsequent two-shelf blockout with two colored boxes. We need a mature realistic editable full-height walnut bookcase. Give an honest Chinese critique of this reference, then design a better bookcase, not a room and not a minimal fixture.
Return JSON with critique (list of strings), design_reasoning (Chinese string), width (1.2 to 1.5 metres), depth (0.34 to 0.40), clearances (5 numbers, bottom to top, each 0.32 to 0.40), wood_color (3 linear RGB numbers, dark warm walnut), levels (5 entries).
Each level: groups (2 or 3 groups left to right). Each group: kind ('upright' or 'stack'), gap_after (0.015 to 0.060), books (list of [width,depth,height,palette_index]). palette_index 0..7 maps burgundy, forest, brown, navy, ochre, rust, teal, tan. Upright widths .020 to .055, depths .16 to .245, heights .19 to .31 (must fit clearance minus .025). Stack book widths .21 to .29, depths .17 to .24, heights .020 to .045; stacks 2 to 4 books descending widths/depths so supported. Use 50 to 65 individual books overall. Upright groups 4 to 11 books. Total width used per level (sum upright widths + .001 per book + max stack width + group gaps) must stay below width-.14. Vary heights non-monotonically, color clusters, paperback versus hardback proportions and negative space. At least 3 levels mix horizontal stacks and upright groups; avoid mechanically identical level compositions. No fake props or placeholders. Geometry compiler will attach shelves, sides, crown, backing and plinth and generate detailed covers/pages/spines. Do not emit coordinates, code or extra keys.'''
    (out/'design_request.txt').write_text(prompt,encoding='utf-8')
    profile=ModelProfile.load(ROOT/'FineScene/configs/deepseek-flash.yaml')
    async with profile.client() as client:
        response=await client.chat.completions.create(model=profile.model,
            messages=[{'role':'user','content':[{'type':'text','text':prompt},
                {'type':'image_url','image_url':{'url':'data:image/png;base64,'+base64.b64encode(reference.read_bytes()).decode(),'detail':'high'}}]}],
            response_format={'type':'json_object'},max_tokens=12000,
            extra_body={'thinking':{'type':'disabled'}})
    raw=response.choices[0].message.content
    (out/'design_response.json').write_text(json.dumps({'model':response.model,'usage':response.usage.model_dump(),'finish_reason':response.choices[0].finish_reason,'content':raw},ensure_ascii=False,indent=2),encoding='utf-8')
    design=json.loads(raw)
    for attempt in range(2):
        errors=validate(design)
        if not errors:break
        (out/f'design_validation_{attempt}.json').write_text(json.dumps(errors),encoding='utf-8')
        async with profile.client() as client:
            response=await client.chat.completions.create(model=profile.model,
                messages=[{'role':'user','content':prompt},{'role':'assistant','content':json.dumps(design)},
                    {'role':'user','content':'Correct the complete JSON. Measured validation failures: '+json.dumps(errors)+'. Also the image was a crop, so do not infer the whole cabinet has only two levels.'}],
                response_format={'type':'json_object'},max_tokens=12000,extra_body={'thinking':{'type':'disabled'}})
        raw=response.choices[0].message.content
        (out/f'design_correction_{attempt}.json').write_text(json.dumps({'model':response.model,'usage':response.usage.model_dump(),'content':raw},ensure_ascii=False,indent=2),encoding='utf-8')
        design=json.loads(raw)
    if validate(design):raise ValueError(validate(design))
    (out/'flash_design.json').write_text(json.dumps(design,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'model':response.model,'books':sum(len(g['books']) for l in design['levels'] for g in l['groups']),'critique':design['critique']},ensure_ascii=True))

if __name__=='__main__':asyncio.run(main())
