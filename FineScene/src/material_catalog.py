"""Verified local surface resources exposed as capabilities to every planner."""
from pathlib import Path
import json

import finescene_env as fenv

ROOT=Path(__file__).resolve().parents[1]/'assets/materials'
EXTERNAL=fenv.ASSETS_ROOT/'external'


def _surface_character(metadata, asset_id):
    """Expose concise, source-grounded material cues to planners and validators."""
    tags=metadata.get('tags',[])
    categories=metadata.get('categories',[])
    if isinstance(tags,str):tags=[tags]
    if isinstance(categories,str):categories=[categories]
    name=str(metadata.get('name') or asset_id)
    category=str(metadata.get('category') or '')
    description=' '.join(str(metadata.get('description') or '').split())
    source_text=' '.join([asset_id,name,category,*map(str,categories),*map(str,tags),description]).casefold()
    if any(word in source_text for word in ('straw','rush','reed','plant fiber','plant-fiber')):
        family='plant_fiber'
    elif any(word in source_text for word in ('fabric','textile','cotton','linen','wool','cloth','leather','upholstery')):
        family='textile'
    elif any(word in source_text for word in ('wood','timber','bamboo')):
        family='wood'
    elif any(word in source_text for word in ('metal','steel','iron','aluminum','brass','copper')):
        family='metal'
    elif any(word in source_text for word in ('glass','glazing')):
        family='glass'
    elif any(word in source_text for word in ('stone','concrete','plaster','brick','ceramic','tile')):
        family='mineral'
    else:family='unknown'
    appearance=[]
    if any(word in source_text for word in ('checkered','checker','plaid','gingham','grid')):
        appearance.append('checkered_or_plaid')
    if any(word in source_text for word in ('red','scarlet','crimson')):
        appearance.append('red')
    if any(word in source_text for word in ('striped','stripe')):
        appearance.append('striped')
    if any(word in source_text for word in ('bright','vivid','high contrast','high-contrast')):
        appearance.append('high_contrast')
    return {'surface_family':family,'appearance_tags':appearance,
            'visual_description':description[:220]}


def _value(item,key,default=None):
    return item.get(key,default) if isinstance(item,dict) else getattr(item,key,default)


def material_assignment_issues(component, parts, materials=None):
    """Find clear semantic mismatches between requested surfaces and selected maps."""
    materials=materials if materials is not None else catalog()
    parts=list(parts)
    component_id=str(_value(component,'id','component'))
    requirements=_value(component,'material_requirements',[]) or []
    component_semantic=str(_value(component,'semantic',''))
    issues=[]
    for index,part in enumerate(parts):
        surface=_value(part,'surface')
        texture_id=_value(surface,'texture_id') if surface is not None else None
        if not texture_id or texture_id not in materials:continue
        resource=materials[texture_id]
        family=resource.get('surface_family','unknown')
        appearance=set(resource.get('appearance_tags',[]))
        part_id=str(_value(part,'id',index))
        part_semantic=str(_value(part,'semantic',''))
        part_context=' '.join((part_id,part_semantic)).casefold()
        material_context=' '.join([component_semantic,*map(str,requirements),part_semantic]).casefold()
        conflicts=[]
        # A wooden deck can belong to a tatami assembly without being the
        # exposed woven face. Require both plant-fiber identity and face/weave
        # evidence before constraining a part to a plant-fiber map.
        tatami_surface=(
            any(word in part_context for word in ('tatami','rush','reed','straw','mat')) and
            any(word in part_context for word in ('rush','reed','straw','woven','weave')) and
            any(word in part_context for word in ('face','surface','woven','weave'))
        )
        if tatami_surface and family not in {'plant_fiber','unknown'}:
            conflicts.append('A tatami/rush/straw mat face needs a compatible plant-fiber surface; '
                             'wood or ordinary textile maps cannot supply that material identity.')
        quiet_palette=any(word in material_context for word in
                          ('muted','oatmeal','cool-grey','cool gray','neutral','pale straw',
                           'low-saturation','low saturation','no bright'))
        explicitly_patterned=any(word in material_context for word in
                                 ('checkered','checker','plaid','gingham','striped pattern'))
        if (quiet_palette and not explicitly_patterned and 'checkered_or_plaid' in appearance and
                ({'red','high_contrast'} & appearance)):
            conflicts.append('The frozen material intent calls for a muted/neutral surface, while this map is '
                             'red or high-contrast checkered/plaid.')
        for reason in conflicts:
            issues.append({'component_id':component_id,'part_id':part_id,'texture_id':texture_id,
                           'reason':reason})
    return issues


def catalog():
    items={}
    for manifest in ROOT.glob('*/provenance.json'):
        record=json.loads(manifest.read_text(encoding='utf-8'))
        maps={kind:next((p for p in manifest.parent.glob('*_'+suffix+'.jpg')),None)
              for kind,suffix in [('color','Color'),('roughness','Roughness'),('normal','NormalGL')]}
        if not maps['color'] or record.get('license')!='CC0 1.0':continue
        items[record['asset']]={'id':record['asset'],'source':record['source'],'license':record['license'],
                               'grain_uv_axis':record.get('grain_uv_axis','v'),
                               **_surface_character({},record['asset']),
                               'maps':{k:str(p.resolve()) for k,p in maps.items() if p}}
    # Provider manifests distinguish standalone PBR surfaces from textures of a model.
    for manifest in EXTERNAL.rglob('download_manifest.json'):
        try:
            data=json.loads(manifest.read_text(encoding='utf-8'))
            metadata_path=manifest.parent/'textures_catalog.json'
            metadata=json.loads(metadata_path.read_text(encoding='utf-8')) if metadata_path.is_file() else {}
        except (OSError,ValueError):continue
        for record in data.get('assets',[]):
            id=record['asset_id']
            if id not in metadata or record.get('license') not in {'CC0','CC0 1.0'}:continue
            files=list((manifest.parent/id/'textures').glob('*'))
            def find(*suffixes):
                return next((p for suffix in suffixes for p in files if suffix in p.stem and p.suffix.lower() in {'.jpg','.png','.exr'}),None)
            maps={'color':find('_diff_','_col_'),'roughness':find('_rough_'),
                  'normal':find('_nor_gl_'),'arm':find('_arm_')}
            if not maps['color']:continue
            dimensions=metadata[id].get('dimensions',[800,800])
            items.setdefault(id,{'id':id,'source':record['source_url'],'license':record['license'],
                'label':metadata[id].get('name',id),'grain_uv_axis':'v',
                **_surface_character(metadata[id],id),
                'tile_size_m':[v/1000 for v in dimensions[:2]],
                'maps':{k:str(p.resolve()) for k,p in maps.items() if p}})
    return items


def capabilities():
    return [{'id':key,'label':value.get('label',key),'source':value['source'],'maps':list(value['maps']),
             'tile_size_m':value.get('tile_size_m',[.8,.8]),
             'surface_family':value.get('surface_family','unknown'),
             'appearance_tags':value.get('appearance_tags',[]),
             'visual_description':value.get('visual_description','')}
            for key,value in catalog().items()]
