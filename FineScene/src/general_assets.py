"""Retrieve or generate missing components, with visual gates before assembly."""
from __future__ import annotations
import hashlib
import copy
import json
from pathlib import Path

import finescene_env as fenv
import re
import subprocess
import urllib.parse
import urllib.request
import urllib.error
import asyncio
import sys
import time
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field

from general_scene import object_size
from general_vision import review_images

ROOT=fenv.REPO_ROOT
BLENDER=Path(fenv.BLENDER)
TRELLIS_PYTHON=fenv.TRELLIS_PYTHON


class ComponentResolutionError(RuntimeError):
    def __init__(self,message,evidence):
        super().__init__(message)
        self.evidence=evidence


def save(path, data):
    Path(path).parent.mkdir(parents=True,exist_ok=True)
    Path(path).write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')


def run_logged(command, log, timeout=900):
    with Path(log).open('w',encoding='utf-8') as f:
        result=subprocess.run([str(v) for v in command],stdout=f,stderr=subprocess.STDOUT,timeout=timeout)
    if result.returncode:raise RuntimeError(f'Process failed ({result.returncode}); inspect {log}')


def fetch(url, path=None, limit=80*1024**2):
    if urllib.parse.urlparse(url).scheme!='https':raise ValueError('Only HTTPS downloads supported')
    request=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0 FineScene/0.2'})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request,timeout=45) as response:
                body=response.read(limit+1)
            break
        except urllib.error.HTTPError as error:
            if error.code not in {429,500,502,503,504} or attempt==2:
                raise
            retry_after=error.headers.get('Retry-After','')
            try:delay=min(2.0,max(.2,float(retry_after)))
            except (TypeError,ValueError):delay=.35*(attempt+1)
            time.sleep(delay)
        except urllib.error.URLError:
            if attempt==2:raise
            time.sleep(.25*(attempt+1))
    if len(body)>limit:raise ValueError('Download exceeds component budget')
    if path:
        Path(path).parent.mkdir(parents=True,exist_ok=True);Path(path).write_bytes(body)
        return str(Path(path).resolve())
    return json.loads(body)


def local_catalog():
    """Discover available glTFs, not a closed list of object categories."""
    result=[]
    base=fenv.ASSETS_ROOT/'external'
    manifests={}
    audits={}
    for manifest in base.rglob('download_manifest.json'):
        try:
            manifests[manifest.parent]={a['asset_id']:a for a in json.loads(manifest.read_text(encoding='utf-8')).get('assets',[])}
        except (OSError,ValueError,KeyError):continue
    for audit in base.rglob('blender_asset_audit.json'):
        try:audits.update(json.loads(audit.read_text(encoding='utf-8')))
        except (OSError,ValueError):continue
    seen=set()
    for ext in ('*.glb','*.gltf'):
        for path in sorted(base.rglob(ext)):
            id=path.parent.name+'/'+path.stem
            if id in seen:id=path.relative_to(base).as_posix()
            seen.add(id)
            record={'asset_id':id,'path':str(path.resolve()),'label':path.parent.name.replace('_',' '),
                    'provenance':'Existing local asset; original provider manifest remains beside the asset'}
            for root,manifest in manifests.items():
                if path.is_relative_to(root) and path.parent.name in manifest:
                    record.update({k:v for k,v in manifest[path.parent.name].items() if k!='asset_id'})
            source=path.parent/'asset_source.json'
            if source.is_file():
                try:record.update({k:v for k,v in json.loads(source.read_text(encoding='utf-8')).items()
                                   if k in {'provider','license','source_url','name'}})
                except (OSError,ValueError):pass
            audit=audits.get(path.parent.name,{})
            objects=audit.get('objects',[])
            if objects and str(path.resolve()).casefold()==str(audit.get('source','')).casefold():
                record['native_size_m']=[max(o['high'][i] for o in objects)-min(o['low'][i] for o in objects) for i in range(3)]
                record['mesh_count']=len(objects)
                record['metadata_warning']='Multi-mesh source may contain alternative objects; inspect full candidate' if len(objects)>1 else 'Orientation still needs visual inspection'
            result.append(record)
    return result


def planning_source_options(queries):
    """Search cached provider metadata before freezing types/proportions. No downloads or acceptance."""
    catalogs=sorted((fenv.ASSETS_ROOT/'external').rglob('models_catalog.json'))
    catalog={}
    for path in catalogs:
        try:catalog.update(json.loads(path.read_text(encoding='utf-8')))
        except (OSError,ValueError):continue
    if not catalog:
        try:catalog=fetch('https://api.polyhaven.com/assets?t=models')
        except Exception:return {'queries':queries,'candidates':[],'status':'metadata_unavailable'}
    records=[{'asset_id':'polyhaven:'+key,'label':v.get('name',key)+' '+' '.join(v.get('tags',[])),
              'provider':'Poly Haven','license':'CC0','source_url':'https://polyhaven.com/a/'+key,
              **({'native_size_m':[float(d)/1000 for d in v['dimensions']],
                  'size_evidence':'Provider catalog dimensions in millimetres; Blender still measures the acquired mesh'}
                 if len(v.get('dimensions',[]))==3 and all(d>0 for d in v['dimensions']) else {})}
             for key,v in catalog.items()]
    selected={}
    for query in queries:
        for record in rank(query,records)[:5]:selected[record['asset_id']]=record
    return {'queries':queries,'candidates':list(selected.values()),'status':'metadata_only_pending_visual_qa'}


def rank(query, records):
    tokens=set(re.findall(r'[a-z0-9]+',query.lower()))
    scored=[]
    for r in records:
        text=(r.get('label','')+' '+r['asset_id']).lower().replace('_',' ')
        record_tokens=set(re.findall(r'[a-z0-9]+',text))
        score=sum(len(t) for t in tokens if len(t)>2 and t in record_tokens)
        if score:scored.append((score,r))
    return [r for _,r in sorted(scored,key=lambda x:(-x[0],x[1]['asset_id']))]


class RetrievalAssessment(BaseModel):
    model_config=ConfigDict(extra='forbid')
    asset_id: str
    action: Literal['inspect','reject']
    reason: str = Field(min_length=8)


class RetrievalSelection(BaseModel):
    model_config=ConfigDict(extra='forbid')
    assessments: list[RetrievalAssessment]


def metadata_candidates(obj,records,profile,folder,*,complete=None):
    """Reject clear category/boundary mismatches before download or Blender work.

    Uncertain candidates still require image QA; metadata never certifies them.
    A failed metadata service falls back to the old bounded visual inspection.
    """
    records=list(records)[:16]
    if not records:return []
    async def execute():
        def validate(selection):
            ids=[a.asset_id for a in selection.assessments]
            if len(ids)!=len(set(ids)) or set(ids)!={r['asset_id'] for r in records}:
                raise ValueError('Assess every supplied candidate exactly once; do not invent asset IDs.')
            return selection
        return await runtime.stage('retrieval_selection',RetrievalSelection,
            'Select candidates for subsequent VISUAL inspection using catalog metadata only. '
            'Assess each supplied asset ID exactly once. reject means a clear semantic identity or '
            'component-boundary mismatch: a whole cabinet/table is not a local carved ornament, '
            'and a complete assembly is not its isolated replacement part. Shared material, style '
            'or context words alone do not make the object suitable. inspect means plausible or '
            'uncertain; never claim geometry, style, contact or quality passed from metadata. '
            'Return only assessments with asset_id, action (inspect/reject), and a concrete reason.',
            {'semantic':obj.semantic,'required_features':obj.source.required_features,
             'query':obj.source.query,'candidates':[
                 {k:r[k] for k in ('asset_id','label','provider') if k in r} for r in records]},validate)
    try:
        runtime=_source_agent(profile,folder,obj.id+'_retrieval_selection',complete=complete)
        selection=asyncio.run(execute())
    except Exception as error:
        save(Path(folder)/'selection.json',{'status':'unavailable','error_type':type(error).__name__,
            'fallback':'bounded_visual_inspection','candidate_ids':[r['asset_id'] for r in records[:2]]})
        return records[:2]
    decisions={a.asset_id:a for a in selection.assessments}
    selected=[r for r in records if decisions[r['asset_id']].action=='inspect'][:2]
    save(Path(folder)/'selection.json',{'status':'screened_not_visually_accepted',
        **selection.model_dump(),'selected_ids':[r['asset_id'] for r in selected]})
    return selected


def verified_provider_file(record,path):
    """Commit a provider file only after integrity checks, retrying stale CDNs."""
    path=Path(path)
    def valid(candidate):
        if not candidate.is_file():return False
        if record.get('size') is not None and candidate.stat().st_size!=int(record['size']):return False
        return not record.get('md5') or hashlib.md5(candidate.read_bytes()).hexdigest()==record['md5']
    if valid(path):return
    temporary=path.with_name(path.name+'.download')
    try:
        for attempt in range(3):
            url=record['url']
            if attempt:
                parts=urllib.parse.urlsplit(url)
                query=urllib.parse.parse_qsl(parts.query,keep_blank_values=True)
                query.append(('finescene_integrity',str(record.get('md5',record.get('size','verify')))+'_'+str(attempt)))
                url=urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(query)))
            fetch(url,temporary)
            if valid(temporary):
                temporary.replace(path)
                return
        raise ValueError('Asset checksum/size mismatch after fresh downloads: '+path.name)
    finally:
        temporary.unlink(missing_ok=True)


def search_polyhaven(query, output, *, selector=None,asset_id=None):
    if asset_id:
        matches=[{'asset_id':asset_id,'label':asset_id.replace('_',' ')}]
    else:
        catalog=fetch('https://api.polyhaven.com/assets?t=models')
        records=[{'asset_id':key,'label':v.get('name',key)+' '+' '.join(v.get('tags',[]))} for key,v in catalog.items()]
        matches=rank(query,records)[:16]
        matches=selector(matches) if selector else matches[:2]
    downloaded=[]
    for item in matches:
        folder=output/item['asset_id'];folder.mkdir(parents=True,exist_ok=True)
        files=fetch('https://api.polyhaven.com/files/'+urllib.parse.quote(item['asset_id']))
        resolutions=files.get('gltf',{})
        resolution=next((v for v in ('1k','2k') if v in resolutions),None)
        if resolution is None:continue
        entry=resolutions[resolution]['gltf']
        main=folder/Path(urllib.parse.urlparse(entry['url']).path).name
        source_files={main.name:entry,**entry.get('include',{})}
        for relative,record in source_files.items():
            path=(folder/relative).resolve()
            if not path.is_relative_to(folder.resolve()):raise ValueError('Invalid asset include path')
            verified_provider_file(record,path)
        record={**item,'path':str(main.resolve()),'provider':'Poly Haven','license':'CC0',
                'source_url':'https://polyhaven.com/a/'+item['asset_id'],
                'source_files':{name:{k:v for k,v in row.items() if k in {'url','md5','size'}}
                                for name,row in source_files.items()}}
        save(folder/'provenance.json',record);downloaded.append(record)
    return downloaded


def _source_agent(profile,folder,task_id,*,complete=None):
    import sys
    from staged_planner import StagedScenePlanner
    runtime=StagedScenePlanner(profile,Path(folder),task_id,complete=complete)
    if complete is not None:return runtime
    sys.path.insert(0,str(fenv.ADSL_ROOT/'adsl-agents'))
    from utils.runner import AgentRuntime
    runtime.runtime=AgentRuntime(model_profile=Path(profile),workspace=runtime.workspace,task_id=runtime.task_id)
    runtime.runtime.model_settings.max_tokens=4096
    if runtime.runtime.profile.model=='deepseek-flash':
        runtime.runtime.model_settings.extra_body={'thinking':{'type':'disabled'}}
    return runtime


SearchQuery=Annotated[str,Field(min_length=3,max_length=120,pattern=r'^\S+(?:\s+\S+){1,3}$')]


class ReferenceQueries(BaseModel):
    model_config=ConfigDict(extra='forbid')
    queries:list[SearchQuery]=Field(min_length=1,max_length=3)


def _fallback_reference_queries(obj):
    """Create short catalog queries if the query-planning call is unusable."""
    import re
    source=getattr(obj,'source',None)
    text=' '.join(str(value) for value in (
        getattr(obj,'semantic',''), getattr(source,'reference_query',''),
        *getattr(source,'required_features',[])))
    stop={'isolated','single','complete','object','component','front','back','side','view',
          'plain','background','real','local','mounted','mounting','surface','required',
          'visible','with','and','the','for','from','into','over','under','its'}
    preferred={'baroque','rococo','carved','carving','wood','walnut','ornament','ornamental',
               'scroll','scrollwork','volute','volutes','acanthus','crown','crest','cresting',
               'pediment','cornice','apron','serpentine','ogee','rail','relief','molding','moulding'}
    tokens=[]
    for token in re.findall(r'[A-Za-z][A-Za-z0-9-]*',text.lower()):
        for piece in token.replace('-',' ').split():
            if piece not in stop and len(piece)>=3 and piece not in tokens:
                tokens.append(piece)
    ordered=[token for token in tokens if token in preferred]+[token for token in tokens if token not in preferred]
    if len(ordered)<2:ordered=['carved','ornament']
    queries=[]
    # Museum catalogues use these labels for the local part more often than
    # the full furniture name. Keep them as bounded fallbacks for crowns,
    # aprons and side rails; the reference Judge still rejects mismatches.
    if any(token in ordered for token in ('crown','crest','cresting','pediment','cornice')):
        queries.extend(['carved acanthus ornament','carved wood crest','furniture carved ornament',
                        'carved acanthus relief','wood carved ornament','baroque wood carving',
                        'acanthus furniture carving','wooden crest relief','carved wood applique',
                        'furniture crest ornament','acanthus scroll carving'])
    elif any(token in ordered for token in ('scroll','scrollwork','volute','ornament','relief')):
        queries.extend(['carved wood crest','furniture carved ornament','carved acanthus ornament',
                        'wood carved ornament','scroll relief','baroque wood carving',
                        'acanthus furniture carving','carved wood applique','ornament wood carving',
                        'furniture crest ornament','acanthus scroll carving'])
    for width in (4,3,2):
        candidate=' '.join(ordered[:width])
        if len(candidate.split())>=2 and candidate not in queries:queries.append(candidate)
    return list(dict.fromkeys(queries))


def refine_reference_queries(obj,profile,folder,*,complete=None,feedback=None,
                             search_feedback=None,attempt=0):
    """The agent converts an image specification into validated short searches."""
    search_folder=Path(folder)/('reference_search_agent' if attempt==0 else f'reference_search_retry_{attempt:02d}')
    runtime=_source_agent(profile,search_folder,obj.id+f'_reference_search_{attempt:02d}',complete=complete)
    async def execute():
        result=await runtime.stage('search_queries',ReferenceQueries,
            'Return JSON with queries: 1 to 3 concise Wikimedia Commons image catalog searches. '
            'Each query MUST be 2-4 English whitespace-separated terms, without generation-prompt words like isolated, render, single or complete. '
            'Describe the real local object and its defining style, try common museum/catalog synonyms. '
            'For a carved furniture ornament, prefer terms such as crest decoration, carved acanthus, volute relief, '
            'or wood ornament; never search for a whole chair, cabinet, book, catalog, scan, page, church, theater, portrait or painting. '
            'Start specific and then broaden while retaining the key shape. Do not use a whole piece of furniture '
            'when the requested input is a local decorative component. No URLs or invented results.',
            {'semantic':obj.semantic,'reference_query':obj.source.reference_query,
             'required_features':obj.source.required_features,'reference_requirements':obj.source.reference_requirements,
             'previous_reference_judge_rejections':feedback or [],
             'previous_search_attempts':((search_feedback or {}).get('attempts',[])
                                         if isinstance(search_feedback,dict)
                                         else (search_feedback or [])),
             'previous_search_queries':((search_feedback or {}).get('searched_queries',[])
                                        if isinstance(search_feedback,dict) else []),
             'instruction':(
                 ('When previous references were rejected, use their visual evidence to choose materially different catalog terms that are likely to retrieve an actual photographed isolated component, not pages or whole furniture. ' if feedback else '')+
                 ('The prior search returned no eligible images. Use its result counts, metadata-filter reasons and errors to choose materially different short catalog terms; do not repeat any searched query. Prefer common object/catalog synonyms over render, scan, or isolation wording.' if search_feedback else ''))},lambda r:r)
        excluded={str(item).strip().casefold() for item in
                  (search_feedback or {}).get('searched_queries',[])}
        return [query for query in result.queries
                if query.strip().casefold() not in excluded]
    try:
        return asyncio.run(execute())
    except Exception as error:
        excluded={str(item).strip().casefold() for item in
                  (search_feedback or {}).get('searched_queries',[])}
        fallback=[q for q in _fallback_reference_queries(obj)
                  if q.strip().casefold() not in excluded][:3]
        save(search_folder/'fallback_queries.json',{
            'status':'deterministic_fallback',
            'error':f'{type(error).__name__}: {error}'[:1200],
            'queries':fallback})
        return fallback


def is_supported_image(path):
    """Check common Wikimedia raster headers without adding a planner dependency."""
    with Path(path).open('rb') as stream:head=stream.read(16)
    return (head.startswith(b'\x89PNG\r\n\x1a\n') or head.startswith(b'\xff\xd8\xff') or
            (head[:4]==b'RIFF' and head[8:12]==b'WEBP'))


def normalize_image_extension(path):
    """Vision input MIME comes from suffix; downloaded JPEGs must not be called PNG."""
    path=Path(path)
    with path.open('rb') as stream:head=stream.read(16)
    extension='.png' if head.startswith(b'\x89PNG\r\n\x1a\n') else '.jpg' if head.startswith(b'\xff\xd8\xff') else '.webp'
    target=path.with_suffix(extension)
    if target!=path:path.replace(target)
    return str(target.resolve())


def _contains_whole_term(text, term):
    """Match a catalog word without confusing it with a longer object name.

    In particular, ``book`` must not match ``bookcase``.  Wikimedia metadata
    is noisy, so the filter is deliberately lexical and conservative; the
    visual Judge remains the acceptance gate.
    """
    return re.search(r'(?<![a-z0-9])'+re.escape(term)+r'(?![a-z0-9])',
                     str(text).lower()) is not None


def _reference_metadata_forbidden(title, metadata_text, brief_text, target_text=None):
    """Return whether a result is an obvious document/category mismatch."""
    haystack=str(title)+' '+str(metadata_text)
    # Exact terms avoid rejecting a bookcase merely because it contains the
    # substring ``book``.  Plurals and catalogue spelling are handled as
    # explicit aliases rather than broad substring tests.
    groups=(
        ('catalog','catalogue'),('scan','scanned','scanning'),('book','books'),
        ('page','pages'),('pdf',),('djvu',),
        ('chair','chairs','chaise','armchair','fauteuil'),
        ('sofa','sofas','couch','couches','settee','settees'),
        ('mirror','mirrors'),
        ('cabinet','cabinets','bookcase','bookcases','wardrobe','wardrobes',
         'dresser','dressers','commode','commodes','high chest','chest of drawers'),
        ('display case','display cases','vitrine','vitrines','showcase','showcases'),
        ('jewelry','jewellery','earring','earrings','necklace','bracelet','brooch',
         'accessory','accessories','costume'),
        ('candle stand','candle stands','torchere','torcheres','torchère','torchères'),
        ('lectern','lecterns'),('clock','clocks'),('table','tables'),
        ('settee','settees'),
        ('helmet','helmets','armor','armour','armored','armoured','weapon','weapons'),
        ('stone','stones','marble','granite','limestone','plaster','stucco'),
        ('building','buildings','facade','facades','façade','façades','wall','walls','street','strada','palace','palaces','house','houses',
         'castle','castles','exterior','portal','portals','monument','monuments'),
        ('drawing','drawings','design','designs','sketch','sketches','illustration','illustrations',
         'paper','papers','plate','plates','print','prints','diagram','diagrams','manuscript'),
        ('painting','paintings','painted','portrait','portraits','portraiture','oil','canvas',
         'person','people','woman','women','man','men','figure','figures','dress','dresses'),
        ('totem','totems','totemic','plaque','plaques','ivory','bone','animal','animals'),
        ('theater','theatre','theaters','theatres'),
        ('church','churches','chapel','chapels','cathedral','cathedrals'),
        ('building','buildings','facade','facades','façade','façades',
         'window','windows','doorway','doorways'),
    )
    brief=str(brief_text)
    # Use the requested object's leading noun phrase to decide whether a
    # furniture category is actually relevant.  The full semantic often
    # mentions its parent (e.g. a crest mounted on a bookcase), which must not
    # make a complete unrelated furniture photograph appear relevant.
    target=str(target_text if target_text is not None else brief)
    category_mismatch=any(any(_contains_whole_term(haystack, alias) for alias in group)
                          and not any(_contains_whole_term(target, alias) for alias in group)
                          for group in groups)
    wood_requested=any(_contains_whole_term(target, term) for term in
                       ('wood','wooden','walnut','oak','mahogany','carved wood'))
    nonwood_source=any(_contains_whole_term(haystack, term) for term in
                       ('stone','marble','granite','limestone','plaster','stucco',
                        'metal','bronze','iron','steel','helmet','armor','armour'))
    return category_mismatch or (wood_requested and nonwood_source)


def image_candidates(obj, folder, offline=False, *, profile=None, feedback=None,
                     search_feedback=None, attempt=0, exclude_sources=None,
                     exclude_queries=None, reference_generator=None):
    source=obj.source
    if source.reference_image:
        path=Path(source.reference_image).expanduser()
        if path.is_file():return [{'path':str(path.resolve()),'source':'provided_reference'}]
        if source.reference_image.startswith('https://'):
            if offline:return []
            return [{'path':fetch(source.reference_image,folder/'reference.png',15*1024**2),'source_url':source.reference_image}]
        raise FileNotFoundError(f'Missing reference: {source.reference_image}')
    if offline:
        if not reference_generator:return []
        try:
            generated=generate_reference_candidate(obj,Path(folder)/'configured_reference_generator',
                                                   reference_generator,attempt=attempt,feedback=feedback)
            save(Path(folder)/'reference_search.json',{'original_reference_query':source.reference_query,
                'attempts':[{'provider':'configured_reference_generator','result':'candidate_created'}],
                'searched_queries':[],'candidates':[generated]})
            return [generated]
        except Exception as error:
            save(Path(folder)/'reference_search.json',{'original_reference_query':source.reference_query,
                'attempts':[{'provider':'configured_reference_generator','error':str(error)[:500]}],
                'searched_queries':[],'candidates':[]})
            return []
    query=source.reference_query or source.query or obj.semantic
    original_query=query
    queries=[query];history=[];result=[];seen=set(exclude_sources or [])
    excluded_queries={str(item).strip().casefold() for item in (exclude_queries or [])}
    if search_feedback:
        excluded_queries.update(str(item).strip().casefold()
                                for item in search_feedback.get('searched_queries',[]))
    if profile:
        try:
            refined=refine_reference_queries(obj,profile,folder,feedback=feedback,
                                             search_feedback=search_feedback,attempt=attempt)
            # Keep the user/decomposition query first for provenance and
            # compatibility on the first pass; recovery searches must use
            # fresh terms informed by the recorded zero-result attempt.
            retry_context=bool(feedback or search_feedback)
            queries=([query] if not retry_context else [])+[item for item in refined if item!=query]
        except Exception as error:history.append({'stage':'query_refinement','error':str(error)[:400]})
    fallback_all=_fallback_reference_queries(obj)
    # Keep each pass bounded. The retry draws from the next deterministic
    # synonyms, while still allowing Agent-proposed fresh queries first.
    fallback_queries=fallback_all[:3] if attempt==0 else fallback_all[3:6]
    search_queries=[item for item in dict.fromkeys(queries)
                    if item.strip().casefold() not in excluded_queries][:4]
    attempted_queries=set()
    for query in search_queries:
        attempted_queries.add(query.strip().casefold())
        params={'action':'query','format':'json','generator':'search','gsrsearch':query,'gsrnamespace':6,'gsrlimit':3,
                'prop':'imageinfo','iiprop':'url|extmetadata','iiurlwidth':1024}
        try:
            data=fetch('https://commons.wikimedia.org/w/api.php?'+urllib.parse.urlencode(params))
            pages=list(data.get('query',{}).get('pages',{}).values())
            history.append({'query':query,'results':len(pages)})
            for page in pages:
                title=str(page.get('title','')).lower()
                info=page.get('imageinfo',[{}])[0];url=info.get('thumburl',info.get('url'))
                description_url=info.get('descriptionurl',url)
                if not url or any(value in seen for value in (url,title,description_url) if value):continue
                metadata_text=' '.join(str(info.get('extmetadata',{}).get(key,{}).get('value',''))
                                      for key in ('ObjectName','ImageDescription','Categories')).lower()
                brief_text=(str(getattr(obj,'semantic',''))+' '+str(getattr(source,'reference_query',''))).lower()
                target_text=str(getattr(obj,'semantic','')).split('.',1)[0].split(',',1)[0].lower()
                if _reference_metadata_forbidden(title,metadata_text,brief_text,target_text):
                    history.append({'query':query,'page_title':title,'source_url':url,
                                    'result':'metadata_context_mismatch'})
                    continue
                seen.add(url)
                try:
                    path=fetch(url,folder/f'reference_{len(result)}.png',15*1024**2)
                    if not is_supported_image(path):raise ValueError('Unsupported or invalid raster image header')
                    path=normalize_image_extension(path)
                except Exception as error:
                    history.append({'query':query,'source_url':url,'error':str(error)[:300]});continue
                result.append({'path':path,'query':query,'page_title':title,
                               'source_url':description_url,'image_url':url,
                               'provider':'Wikimedia Commons',
                               'license':info.get('extmetadata',{}).get('LicenseShortName',{}).get('value'),
                               'metadata':info.get('extmetadata',{})})
                if len(result)>=6:break
            if len(result)>=6:break
        except Exception as error:history.append({'query':query,'error':str(error)[:400]})
    # A valid but over-specific agent query can return only document scans. A
    # bounded lexical second pass keeps the source route alive without
    # weakening reference QA or accepting the first image blindly.
    if not result:
        for fallback in fallback_queries:
            normalized=fallback.strip().casefold()
            if normalized in attempted_queries or normalized in excluded_queries:continue
            attempted_queries.add(normalized)
            params={'action':'query','format':'json','generator':'search','gsrsearch':fallback,'gsrnamespace':6,'gsrlimit':3,
                    'prop':'imageinfo','iiprop':'url|extmetadata','iiurlwidth':1024}
            try:
                data=fetch('https://commons.wikimedia.org/w/api.php?'+urllib.parse.urlencode(params))
                pages=list(data.get('query',{}).get('pages',{}).values())
                history.append({'query':fallback,'results':len(pages),'fallback':True})
                for page in pages:
                    title=str(page.get('title','')).lower();info=page.get('imageinfo',[{}])[0]
                    url=info.get('thumburl',info.get('url'))
                    description_url=info.get('descriptionurl',url)
                    if not url or any(value in seen for value in (url,title,description_url) if value):continue
                    metadata_text=' '.join(str(info.get('extmetadata',{}).get(key,{}).get('value',''))
                                          for key in ('ObjectName','ImageDescription','Categories')).lower()
                    brief_text=(str(getattr(obj,'semantic',''))+' '+str(getattr(source,'reference_query',''))).lower()
                    target_text=str(getattr(obj,'semantic','')).split('.',1)[0].split(',',1)[0].lower()
                    if _reference_metadata_forbidden(title,metadata_text,brief_text,target_text):
                        history.append({'query':fallback,'page_title':title,'source_url':url,
                                        'result':'metadata_context_mismatch','fallback':True})
                        continue
                    try:
                        path=fetch(url,folder/f'reference_{len(result)}.png',15*1024**2)
                        if not is_supported_image(path):raise ValueError('Unsupported or invalid raster image header')
                        path=normalize_image_extension(path)
                    except Exception as error:
                        history.append({'query':fallback,'source_url':url,'error':str(error)[:300]});continue
                    seen.add(url);result.append({'path':path,'query':fallback,'page_title':title,
                        'source_url':description_url,'image_url':url,
                        'provider':'Wikimedia Commons',
                        'license':info.get('extmetadata',{}).get('LicenseShortName',{}).get('value'),
                        'metadata':info.get('extmetadata',{})})
                    if len(result)>=6:break
                # Keep collecting across fallback terms.  A first lexical hit
                # is often a museum context photograph; later narrow terms
                # can supply a better isolated carving for the visual gate.
            except Exception as error:history.append({'query':fallback,'error':str(error)[:400],'fallback':True})
    # The first batch stays on Commons unless that source has no candidates.
    # After concrete Judge rejections, search independent museum catalogs too;
    # a full Wikimedia page must not starve alternate providers on retry.
    if not result or attempt>0:
        commons_results=list(result)
        museum_results=[]
        museum_folder=Path(folder)/'cleveland_open_access'
        try:
            museum=search_cleveland_references(
                obj,museum_folder,attempt=attempt,exclude_sources=seen,
                exclude_queries=excluded_queries|attempted_queries)
            museum_results=museum[:6]
            museum_path=museum_folder/'cleveland_search.json'
            if museum_path.is_file():
                museum_record=json.loads(museum_path.read_text(encoding='utf-8'))
                for item in museum_record.get('attempts',[]):
                    history.append({'provider':'Cleveland Museum of Art Open Access',**item})
                attempted_queries.update(str(q).strip().casefold()
                                         for q in museum_record.get('searched_queries',[]))
        except Exception as error:
            history.append({'provider':'Cleveland Museum of Art Open Access',
                            'error':f'{type(error).__name__}: {error}'[:400]})
        artic_results=[]
        artic_folder=Path(folder)/'art_institute_open_access'
        try:
            artic=search_artic_references(obj,artic_folder,attempt=attempt,
                exclude_sources=seen,exclude_queries=excluded_queries|attempted_queries)
            artic_results=artic[:6]
            artic_path=artic_folder/'artic_search.json'
            if artic_path.is_file():
                artic_record=json.loads(artic_path.read_text(encoding='utf-8'))
                for item in artic_record.get('attempts',[]):
                    history.append({'provider':'Art Institute of Chicago Open Access',**item})
                attempted_queries.update(str(q).strip().casefold()
                                         for q in artic_record.get('searched_queries',[]))
        except Exception as error:
            history.append({'provider':'Art Institute of Chicago Open Access',
                            'error':f'{type(error).__name__}: {error}'[:400]})
        generated_results=[]
        if reference_generator and (attempt>0 or not commons_results):
            try:
                generated_results=[generate_reference_candidate(obj,
                    Path(folder)/'configured_reference_generator',reference_generator,
                    attempt=attempt,feedback=feedback) ]
                history.append({'provider':'configured_reference_generator',
                                'result':'candidate_created','attempt':attempt})
            except Exception as error:
                history.append({'provider':'configured_reference_generator',
                                'error':f'{type(error).__name__}: {error}'[:400],
                                'attempt':attempt})
        # Each round remains capped at six Judge calls. On retry, alternate
        # catalogs get first choice, followed by genuinely new Commons images.
        if attempt>0 and (feedback or search_feedback):
            result=_diverse_reference_candidates([generated_results,artic_results,museum_results,commons_results])
        else:
            result=_diverse_reference_candidates([commons_results,museum_results,artic_results,generated_results])
    save(folder/'reference_search.json',{'original_reference_query':original_query,'attempts':history,
        'fallback_queries':fallback_queries,'searched_queries':sorted(attempted_queries),
        'candidates':result})
    return result


def _reference_search_feedback(folder):
    """Condense an empty reference search into actionable, auditable retry input."""
    path=Path(folder)/'reference_search.json'
    if not path.is_file():return {'searched_queries':[],'attempts':[],'candidate_count':0}
    try:record=json.loads(path.read_text(encoding='utf-8'))
    except (OSError,json.JSONDecodeError):return {'searched_queries':[],'attempts':[],'candidate_count':0}
    attempts=[]
    for item in record.get('attempts',[])[:64]:
        attempts.append({key:item[key] for key in
                         ('query','results','result','page_title','source_url','catalog_id',
                          'provider','license','error','fallback') if key in item})
    searched=record.get('searched_queries') or list(dict.fromkeys(
        item.get('query') for item in record.get('attempts',[]) if item.get('query')))
    return {'original_reference_query':record.get('original_reference_query'),
            'searched_queries':searched[:32],'attempts':attempts,
            'candidate_count':len(record.get('candidates',[]))}


def _cleveland_reference_queries(obj, attempt=0):
    """Use open-access museum vocabulary when Commons is dominated by scans."""
    source=getattr(obj,'source',None)
    text=(' '.join(str(value) for value in (getattr(obj,'semantic',''),
                                             getattr(source,'reference_query','')))).lower()
    if any(token in text for token in ('urn','finial','vase')):
        return (['urn finial acanthus','carved urn ornament','urn furniture ornament'] if attempt==0 else
                ['acanthus urn carving','decorative urn finial','urn wood carving'])
    if any(token in text for token in ('acanthus','crest','ornament','volute','carving','relief')):
        # Keep the first pass diverse: a broad catalog query can otherwise
        # consume all six slots with near-duplicate pieces from one collection
        # before a more diagnostic acanthus/Grinling term is tried.
        return (['carving from an overmantel','Grinling Gibbons carving',
                 'acanthus wood carving','carved wood ornament'] if attempt==0 else
                ['acanthus leaf ornament','carved acanthus','baroque wood carving',
                 'decorative wood carving'])
    return ['carved wood furniture','wood carving'] if attempt==0 else ['decorative wood carving','furniture ornament']


def _artic_reference_queries(obj, attempt=0):
    """Use Art Institute catalog vocabulary after Commons misses or is rejected."""
    source=getattr(obj,'source',None)
    text=(' '.join(str(value) for value in (getattr(obj,'semantic',''),
        getattr(source,'reference_query',''),*getattr(source,'required_features',[])))).casefold()
    if any(token in text for token in ('urn','finial','vase')):
        return (['urn acanthus','furniture urn finial','urn ornament'] if attempt==0 else
                ['acanthus urn','decorative urn','urn with leaves'])
    if 'acanthus' in text or 'crest' in text:
        return (['acanthus furniture element','carved acanthus ornament','furniture scroll carving'] if attempt==0 else
                ['acanthus wood carving','carved furniture ornament','volute carving'])
    if any(token in text for token in ('volute','scroll','carving','relief')):
        return (['scroll furniture ornament','carved wood scroll','wood carving ornament'] if attempt==0 else
                ['ornamental scroll carving','carved wood ornament','furniture scrollwork'])
    return ['carved wood ornament','furniture carving','decorative wood carving']


def search_artic_references(obj, folder, *, attempt=0, exclude_sources=None,
                            exclude_queries=None):
    """Fetch public-domain Art Institute photographs as untrusted references.

    Catalog status establishes reuse provenance only. Every image still has to
    pass FineScene's visual reference Judge before TRELLIS.1 can consume it.
    """
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    excluded={str(item).casefold() for item in (exclude_sources or [])}
    excluded_queries={str(item).strip().casefold() for item in (exclude_queries or [])}
    queries=[q for q in _artic_reference_queries(obj,attempt)
             if q.strip().casefold() not in excluded_queries]
    candidates=[];history=[];searched=[]
    source=getattr(obj,'source',None)
    brief_text=(str(getattr(obj,'semantic',''))+' '+str(getattr(source,'reference_query',''))).lower()
    target_text=str(getattr(obj,'semantic','')).split('.',1)[0].split(',',1)[0].lower()
    fields=('id,title,artist_title,medium_display,date_display,department_title,'
            'artwork_type_title,classification_titles,is_public_domain,image_id,thumbnail,'
            'api_link,web_url,credit_line')
    for query in queries:
        searched.append(query)
        endpoint='https://api.artic.edu/api/v1/artworks/search?'+urllib.parse.urlencode(
            {'q':query,'limit':30,'fields':fields})
        try:
            response=fetch(endpoint);items=list(response.get('data',[]))
            history.append({'query':query,'results':len(items),
                            'catalog_total':(response.get('pagination') or {}).get('total')})
        except Exception as error:
            history.append({'query':query,'error':str(error)[:400]});continue
        query_candidates=0
        query_terms=set(re.findall(r'[a-z0-9]+',query.casefold()))
        for item in items:
            catalog_id=str(item.get('id') or '')
            image_id=str(item.get('image_id') or '')
            title=str(item.get('title') or '')
            object_url=item.get('web_url') or (f'https://www.artic.edu/artworks/{catalog_id}' if catalog_id else '')
            image_url=(f'https://www.artic.edu/iiif/2/{urllib.parse.quote(image_id,safe="")}/full/1024,/0/default.jpg'
                       if image_id else '')
            metadata_text=' '.join(str(item.get(key) or '') for key in
                ('title','artist_title','medium_display','date_display','department_title',
                 'artwork_type_title','classification_titles','description')).lower()
            identity_text=' '.join(str(item.get(key) or '') for key in
                ('title','medium_display','artwork_type_title','classification_titles')).casefold()
            if not catalog_id or not image_id or item.get('is_public_domain') is not True:
                history.append({'query':query,'catalog_id':catalog_id,'page_title':title,
                                'source_url':object_url,'result':'not_public_domain_or_no_image'})
                continue
            # Keep the catalog provider conservative: the search API is broad
            # and otherwise returns unrelated works for every phrase.
            if not query_terms.intersection(set(re.findall(r'[a-z0-9]+',metadata_text))):
                history.append({'query':query,'catalog_id':catalog_id,'page_title':title,
                                'source_url':object_url,'result':'metadata_query_mismatch'})
                continue
            shape_terms=query_terms & {'urn','finial','vase','acanthus','ornament','ornamental',
                                       'scroll','scrollwork','volute','carving','carved','relief',
                                       'applique','appliqué','furniture'}
            target_terms=set(re.findall(r'[a-z0-9]+',target_text))
            target_shape_terms=target_terms & {'urn','finial','vase','acanthus','ornament','ornamental',
                'scroll','scrollwork','volute','carving','carved','relief','applique','appliqué','corbel'}
            if ((shape_terms and not any(term in identity_text for term in shape_terms)) or
                (target_shape_terms and not target_shape_terms.intersection(
                    set(re.findall(r'[a-z0-9]+',identity_text))))):
                history.append({'query':query,'catalog_id':catalog_id,'page_title':title,
                                'source_url':object_url,'result':'metadata_shape_mismatch'})
                continue
            if _reference_metadata_forbidden(title,metadata_text,brief_text,target_text):
                history.append({'query':query,'catalog_id':catalog_id,'page_title':title,
                                'source_url':object_url,'result':'metadata_context_mismatch'})
                continue
            if any(value and str(value).casefold() in excluded
                   for value in (catalog_id,image_url,object_url)):
                continue
            path=folder/f'artic_{catalog_id}.jpg'
            try:
                path=fetch(image_url,path,15*1024**2)
                if not is_supported_image(path):raise ValueError('Unsupported or invalid raster image header')
                path=normalize_image_extension(path)
            except Exception as error:
                history.append({'query':query,'catalog_id':catalog_id,'source_url':object_url,
                                'error':str(error)[:300]});continue
            candidates.append({'path':path,'query':query,'page_title':title.lower(),
                'source_url':object_url,'image_url':image_url,
                'provider':'Art Institute of Chicago Open Access',
                'license':'CC0 1.0 (public-domain artwork)', 'catalog_id':catalog_id,
                'metadata':{key:item.get(key) for key in
                    ('title','artist_title','medium_display','date_display','department_title',
                     'artwork_type_title','classification_titles','api_link','credit_line')
                    if item.get(key) is not None}})
            excluded.update(str(value).casefold() for value in (catalog_id,image_url,object_url) if value)
            query_candidates+=1
            if query_candidates>=2 or len(candidates)>=6:break
        if len(candidates)>=6:break
    save(folder/'artic_search.json',{'queries':queries,'searched_queries':searched,
         'attempts':history,'candidates':candidates})
    return candidates


def _diverse_reference_candidates(groups, *, limit=6, per_provider=2):
    """Round-robin providers so one broad catalog cannot starve peers."""
    unique=[];seen=set()
    for group in groups:
        filtered=[]
        for candidate in group:
            key=(candidate.get('provider'),candidate.get('catalog_id') or candidate.get('source_url') or
                 candidate.get('image_url') or candidate.get('path'))
            if key in seen:continue
            seen.add(key);filtered.append(candidate)
        unique.append(filtered)
    selected=[]
    for index in range(per_provider):
        for group in unique:
            if index<len(group) and len(selected)<limit:selected.append(group[index])
    if len(selected)<limit:
        for group in unique:
            for candidate in group:
                if candidate in selected:continue
                selected.append(candidate)
                if len(selected)>=limit:break
            if len(selected)>=limit:break
    return selected


def generate_reference_candidate(obj, folder, generator, *, attempt=0, feedback=None):
    """Run an explicitly configured text-to-image adapter under an auditable contract.

    The adapter is intentionally opt-in: it receives a JSON request and must
    write one raster image to the requested output path. Its image is still an
    untrusted candidate and goes through the same reference Judge. No API key
    or model credential is accepted through the scene plan.
    """
    executable=Path(generator).expanduser()
    if not executable.is_file():
        raise FileNotFoundError(f'reference generator executable not found: {executable}')
    executable=executable.resolve()
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    request_path=folder/f'reference_generator_request_{attempt:02d}.json'
    output_path=folder/f'generated_reference_{attempt:02d}.png'
    request={
        'schema':'finescene.reference_generator_request.v1',
        'component_id':obj.id,
        'semantic':obj.semantic,
        'required_features':list(obj.source.required_features),
        'reference_requirements':list(obj.source.reference_requirements),
        'reference_query':obj.source.reference_query,
        'attempt':attempt,
        'previous_judge_feedback':(feedback or [])[-4:],
        'instruction':'Generate one complete isolated object on a neutral background for image-to-3D conditioning. Do not include the parent scene, props, text or watermark.'}
    save(request_path,request)
    # A Python adapter is the most portable local integration point on the
    # Windows research setup.  Native executables also work; .py files are
    # launched by the same interpreter running FineScene.
    command=([sys.executable,str(executable)] if executable.suffix.casefold()=='.py'
             else [str(executable)])
    command.extend(['--request',str(request_path),'--output',str(output_path)])
    output_path.unlink(missing_ok=True)
    try:
        completed=subprocess.run(command,
                                 stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                                 text=True,encoding='utf-8',timeout=900)
    except subprocess.TimeoutExpired as error:
        output=getattr(error,'stdout',None) or ''
        (folder/f'reference_generator_{attempt:02d}.log').write_text(
            str(output),encoding='utf-8')
        raise RuntimeError('reference generator timed out after 900 seconds') from error
    except OSError as error:
        (folder/f'reference_generator_{attempt:02d}.log').write_text(
            f'{type(error).__name__}: {error}',encoding='utf-8')
        raise
    (folder/f'reference_generator_{attempt:02d}.log').write_text(completed.stdout or '',encoding='utf-8')
    if completed.returncode:
        raise RuntimeError(f'reference generator failed ({completed.returncode})')
    if not output_path.is_file():raise RuntimeError('reference generator did not write its output image')
    if not is_supported_image(output_path):raise ValueError('reference generator output is not a supported raster')
    output_path=Path(normalize_image_extension(output_path))
    return {'path':str(output_path.resolve()),'query':'configured text-to-image reference',
            'page_title':obj.id+' generated reference','source_url':'',
            'image_url':'','provider':'configured_reference_generator',
            'license':'unknown; inspect generator contract','catalog_id':'',
            'generator':str(executable.resolve()),'metadata':{'request':str(request_path.resolve())}}


def search_cleveland_references(obj, folder, *, attempt=0, exclude_sources=None,
                                exclude_queries=None):
    """Fetch bounded CC0 museum photographs as reference candidates.

    This is still only source acquisition: every downloaded image enters the
    existing reference Visual Judge and no catalog metadata certifies a match.
    """
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    excluded={str(item).strip().casefold() for item in (exclude_sources or [])}
    excluded_queries={str(item).strip().casefold() for item in (exclude_queries or [])}
    candidates=[];history=[];searched=[]
    queries=[q for q in _cleveland_reference_queries(obj,attempt)
             if q.strip().casefold() not in excluded_queries]
    source=getattr(obj,'source',None)
    brief_text=(str(getattr(obj,'semantic',''))+' '+str(getattr(source,'reference_query',''))).lower()
    target_text=str(getattr(obj,'semantic','')).split('.',1)[0].split(',',1)[0].lower()
    per_query_cap=2
    for query in queries:
        searched.append(query)
        params={'q':query,'has_image':1,'limit':8}
        endpoint='https://openaccess-api.clevelandart.org/api/artworks/?'+urllib.parse.urlencode(params)
        try:
            data=fetch(endpoint);items=list(data.get('data',[]))
            history.append({'query':query,'results':len(items)})
        except Exception as error:
            history.append({'query':query,'error':str(error)[:400]});continue
        query_candidates=0
        for item in items:
            catalog_id=str(item.get('id',''))
            images=item.get('images') or {}
            web=images.get('print') or images.get('web') or {}
            url=web.get('url') if isinstance(web,dict) else None
            title=str(item.get('title',''))
            metadata_text=' '.join(str(item.get(key,'')) for key in
                                   ('title','technique','tombstone','description','culture','department')).lower()
            # Curatorial prose frequently mentions the original room (wall,
            # mantel, painting gallery, etc.) even for an isolated museum
            # fragment. Use object-identifying fields for hard category
            # rejection; keep the full description available to the visual
            # Judge as context rather than treating it as the depicted object.
            category_text=' '.join(str(item.get(key,'')) for key in
                                   ('title','technique','tombstone')).lower()
            source_url=item.get('url') or f'https://clevelandart.org/art/{catalog_id}'
            if not url or not catalog_id or any(value and str(value).casefold() in excluded
                                                for value in (url,source_url,catalog_id)):
                continue
            if _reference_metadata_forbidden(title,category_text,brief_text,target_text):
                history.append({'query':query,'catalog_id':catalog_id,'page_title':title,
                                'source_url':source_url,'result':'metadata_context_mismatch'})
                continue
            path=folder/f'cleveland_{catalog_id}.jpg'
            try:
                path=fetch(url,path,15*1024**2)
                if not is_supported_image(path):raise ValueError('Unsupported or invalid raster image header')
                path=normalize_image_extension(path)
            except Exception as error:
                history.append({'query':query,'catalog_id':catalog_id,'source_url':source_url,
                                'error':str(error)[:300]});continue
            candidates.append({'path':path,'query':query,'page_title':title.lower(),
                'source_url':source_url,'image_url':url,'provider':'Cleveland Museum of Art Open Access',
                'license':item.get('share_license_status') or 'Open Access',
                'catalog_id':catalog_id,'metadata':{
                    key:item.get(key) for key in ('title','technique','tombstone','description','url')
                    if item.get(key) is not None}})
            excluded.update({str(value).casefold() for value in (url,source_url,catalog_id) if value})
            query_candidates+=1
            if query_candidates>=per_query_cap or len(candidates)>=6:break
        if len(candidates)>=6:break
    save(folder/'cleveland_search.json',{'queries':queries,'searched_queries':searched,
         'attempts':history,'candidates':candidates})
    return candidates


def reference_geometry_request(obj):
    """Separate image-to-3D shape evidence from final material/interface QA."""
    import re
    interface_terms=('flat underside','underside mounting','mounting base','mounting face',
                     'mounting faces','flat back','attachment face')
    size_terms=('proportion','dimensions around','approximately','about ')
    material_words=('aged','dark','giltwood','gilt','gilded','golden','gold','walnut','wooden',
        'wood','timber','metallic','metal','bronze','brass','iron','steel','stone','marble',
        'granite','limestone','plaster','stucco','leather','cloth','vellum','paper','glass',
        'ceramic','porcelain','satin','matte','polished','finish','texture','grain','photoreal',
        'photorealistic','tool marks','highlights','high points','wear')
    def geometry_text(value):
        text=str(value).casefold()
        if any(term in text for term in interface_terms+size_terms):return ''
        if re.search(r'\b\d+(?:\.\d+)?\s*(?:m|cm|mm)\b',text):return ''
        for term in sorted(material_words,key=len,reverse=True):
            text=re.sub(r'(?<![a-z0-9])'+re.escape(term)+r'(?![a-z0-9])',' ',text)
        text=re.sub(r'\b(?:with|made|of|in|on|and|the|a|an)\b',' ',text)
        return ' '.join(text.split()).strip(' ,;:-')
    features=[cleaned for item in obj.source.required_features
              if (cleaned:=geometry_text(item))]
    reference=[cleaned for item in obj.source.reference_requirements
               if (cleaned:=geometry_text(item))]
    if not features:
        features=[str(obj.semantic).split('.',1)[0].split(',',1)[0]]
    return {'semantic':'Image-to-3D geometry reference: '+'; '.join(features),
            'required_features':features,'reference_requirements':reference,
            'generation_geometry_guidance':(
                'Judge only visible identity, silhouette, defining shape and whether one complete target object '
                'is isolated. The final generated asset is checked separately for its requested material, scale, '
                'mounting interface and full scene requirements; this source image need not prove hidden faces.')}


class SourceReconciliation(BaseModel):
    model_config=ConfigDict(extra='forbid')
    action: Literal['revise_inferred_specification','reject_candidate']
    semantic: str = Field(min_length=1,max_length=400)
    required_features: list[str] = Field(min_length=1)
    material_requirements: list[str] = Field(min_length=1)
    reason: str = Field(min_length=20)


def reconcile_source_specification(request,verdict,profile,folder):
    """The source planning Agent resolves its own speculative requirements.

    A proposal cannot accept a mesh: the independent visual Judge must review
    the unchanged images again against the original user request and new spec.
    """
    runtime=_source_agent(profile,folder,'source-reconciliation')
    def validate(proposal):
        if proposal.action=='revise_inferred_specification' and not proposal.required_features:
            raise ValueError('Preserve the actual object identity and required function')
        return proposal
    return asyncio.run(runtime.stage('source_reconciliation',SourceReconciliation,
        'You are the source planning Agent, auditing a rejected retrieval against the ORIGINAL user request. '
        'Return the supplied schema. You may revise an Agent-inferred material, decorative profile, wear, '
        'hardware or exact part subtype when the Judge describes an actual finished source that still '
        'satisfies the user\'s identity, comfort, realism and defining style. Ground the new description '
        'in the Judge\'s visible observations. Do not turn an incidental guessed preference into a user '
        'constraint. Never excuse real missing geometry, gross distortion, missing textures, incompatible '
        'functional type or a defining shape/style explicitly demanded by the user. Reject in those cases. '
        'Do not change source, scale, coordinates, attachment or mesh. A revised specification will undergo '
        'an independent visual review of the same images; your proposal cannot approve the asset.',
        {'request':request,'visual_decision':verdict.get('decision'),
         'schema':SourceReconciliation.model_json_schema()},validate,
        lambda data:['/'+key for key in data]))


def prepare_candidate(obj, plan, source, folder, vision_profile, quality, *, original_request=None):
    folder.mkdir(parents=True,exist_ok=True)
    save(folder/'source.json',source)
    command=[BLENDER,'-b','--python-exit-code','1','--python',Path(__file__).with_name('general_render.py'),'--',
        '--asset',source['path'],'--output',folder,'--size',*map(str,object_size(plan,obj)),
        '--rotation',str(obj.source.front_rotation_deg)]
    if source.get('requires_generated_texture'):
        command.append('--require-texture')
    cached=False
    if (folder/'asset.json').is_file() and (folder/'render_manifest.json').is_file():
        try:
            previous=json.loads((folder/'asset.json').read_text(encoding='utf-8'))
            manifest=json.loads((folder/'render_manifest.json').read_text(encoding='utf-8'))
            cached=(Path(previous['path']).is_file() and
                previous.get('source_sha256')==hashlib.sha256(Path(source['path']).read_bytes()).hexdigest() and
                manifest.get('renderer_source_sha256')==hashlib.sha256(Path(__file__).with_name('general_render.py').read_bytes()).hexdigest() and
                all(Path(p).is_file() for p in manifest['images']))
        except (OSError,ValueError,KeyError):pass
    if not cached:run_logged(command,folder/'prepare.log')
    images=json.loads((folder/'render_manifest.json').read_text(encoding='utf-8'))['images']
    request={'id':obj.id,'semantic':obj.semantic,'size':object_size(plan,obj).tolist(),
        'required_features':obj.source.required_features,'material_requirements':obj.source.material_requirements}
    if source.get('same_source_instances'):
        request['same_source_instances']=source['same_source_instances']
        request['source_identity_evidence']='These placements use this exact source asset at identical normalized size/orientation. Their matching model identity is established by the shared acquisition key; judge the real source appearance once.'
    if original_request:
        request['original_user_request']=original_request
        request['requirement_authority']='Original user request is authoritative. Component metadata is the Agent design proposal; its incidental material, wear, exact seams and cushion count are not additional user constraints. Reject genuine identity, quality, texture, geometry and explicit-style failures. Report acceptable material variants as design reconciliation improvements.'
    verdict=review_images(images,request,vision_profile,
        folder/'visual_qa.json',quality=quality,scope='component')
    if verdict.get('status')=='unavailable':
        verdict=review_images(images,request,vision_profile,
            folder/'visual_qa_service_retry.json',quality=quality,scope='component')
        verdict_path=folder/'visual_qa_service_retry.json'
    else:verdict_path=folder/'visual_qa.json'
    reconciled=None
    if original_request and verdict.get('status')=='needs_revision':
        try:
            proposal=reconcile_source_specification(request,verdict,vision_profile,folder/'source_planning')
            save(folder/'source_reconciliation.json',proposal.model_dump(mode='json'))
            if proposal.action=='revise_inferred_specification':
                adjusted={**request,'semantic':proposal.semantic,'required_features':proposal.required_features,
                    'material_requirements':proposal.material_requirements,
                    'source_planning_reason':proposal.reason}
                second=review_images(images,adjusted,vision_profile,folder/'reconciled_visual_qa.json',
                    quality=quality,scope='component')
                if second.get('accepted'):
                    reconciled=proposal.model_dump(mode='json');verdict=second
        except Exception as error:
            save(folder/'source_reconciliation_error.json',{'type':type(error).__name__,'error':str(error)[:700]})
    record=json.loads((folder/'asset.json').read_text(encoding='utf-8'))
    if source.get('requires_generated_texture') and not record.get('base_color_texture_verified'):
        raise RuntimeError('Generated textured component lost its UV/base-color material during Blender import/export')
    record.update(accepted=verdict['accepted'],provenance=source,visual_qa=str(verdict_path),
                  visual_status=verdict.get('status'),quality_target=quality)
    if reconciled:
        record['reconciled_specification']=reconciled
        record['visual_qa']=str(folder/'reconciled_visual_qa.json')
    save(folder/'asset.json',record)
    return record


def reuse_prepared_asset(obj,plan,previous,directory,vision_profile,quality,original_request,*,reviewer=None):
    """Reuse an unchanged normalized mesh; rejected sources get fresh Agent QA.

    The caller verifies the previous source/envelope contract. This function
    verifies file integrity and keeps previous evidence immutable.
    """
    record=copy.deepcopy(previous)
    path=Path(record['path'])
    if not path.is_file():raise ValueError('Prepared mesh is missing')
    sha=hashlib.sha256(path.read_bytes()).hexdigest()
    if record.get('sha256') and record['sha256']!=sha:
        raise ValueError('Prepared mesh content changed')
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    record['sha256']=sha
    record['reuse_evidence']={'source_record':previous,'verified_mesh_sha256':sha,
        'policy':'Unchanged prepared source/envelope; source rejection cannot be promoted without fresh Agent review.'}
    if record.get('accepted') is not True:
        manifest=json.loads((path.parent/'render_manifest.json').read_text(encoding='utf-8'))
        if manifest.get('renderer_source_sha256')!=hashlib.sha256(Path(__file__).with_name('general_render.py').read_bytes()).hexdigest():
            source=copy.deepcopy(record.get('provenance') or {})
            if not Path(source.get('path','')).is_file():
                raise ValueError('Prepared source needs fresh rendering but its original source is missing')
            refreshed=prepare_candidate(obj,plan,source,directory/'refreshed',vision_profile,quality,
                                        original_request=original_request)
            refreshed['sha256']=hashlib.sha256(Path(refreshed['path']).read_bytes()).hexdigest()
            refreshed['reuse_evidence']={**record['reuse_evidence'],
                'refresh_reason':'Renderer changed; source rendered and reviewed again by the Agent system.'}
            refreshed['inspection_only']=not refreshed['accepted']
            save(directory/'asset.json',refreshed)
            return refreshed
        request={'id':obj.id,'semantic':obj.semantic,'size':object_size(plan,obj).tolist(),
            'required_features':obj.source.required_features,'material_requirements':obj.source.material_requirements,
            'original_user_request':original_request,
            'source_material_measurements':{'base_color_texture_verified':record.get('base_color_texture_verified')},
            'requirement_authority':'Original user request is authoritative. Component metadata is an Agent proposal. '
                'Report incidental color, wear, exact profile or seams as improvements. Genuine identity, geometry, '
                'texture or explicit user-style failures remain blocking.'}
        qa_path=directory/'source_visual_qa.json'
        verdict=(reviewer or review_images)(manifest['images'],request,vision_profile,qa_path,quality=quality,scope='component')
        if verdict.get('status')=='unavailable':
            qa_path=directory/'source_visual_qa_service_retry.json'
            verdict=(reviewer or review_images)(manifest['images'],request,vision_profile,qa_path,quality=quality,scope='component')
        record.update(accepted=verdict.get('accepted') is True,visual_status=verdict.get('status'),visual_qa=str(qa_path))
        record['inspection_only']=not record['accepted']
    save(directory/'asset.json',record)
    return record


def source_visual_feedback(record):
    """Pass concrete Judge findings to source replanning, not only file paths."""
    path=Path(record.get('visual_qa',''))
    if not path.is_file():return {'status':record.get('visual_status','unavailable')}
    report=json.loads(path.read_text(encoding='utf-8'))
    decision=report.get('decision',{})
    return {'status':report.get('status','unavailable'),
            **{key:decision[key] for key in ('summary','findings','style_assessments') if key in decision},
            **{key:report[key] for key in ('error_type','error') if key in report}}


def resolve_component(obj, plan, cache, catalog, vision_profile, quality, offline=False,
                      reference_generator=None, original_request=None, source_instances=None):
    signature={'router_version':7,'object':obj.model_dump(),'size':object_size(plan,obj).tolist(),'quality':quality,'profile':str(vision_profile),
               'reference_generator':str(reference_generator) if reference_generator else None,
               'catalog':[{**a,'mtime':Path(a['path']).stat().st_mtime_ns} for a in catalog if Path(a['path']).is_file()]}
    if original_request:signature['original_user_request']=original_request
    if source_instances:signature['source_instances']=source_instances
    ref=obj.source.reference_image
    if ref and Path(ref).is_file():signature['image_sha256']=hashlib.sha256(Path(ref).read_bytes()).hexdigest()
    if obj.source.asset_id:
        selected=next((a for a in catalog if a['asset_id']==obj.source.asset_id),None)
        if selected and Path(selected['path']).is_file():signature['asset_sha256']=hashlib.sha256(Path(selected['path']).read_bytes()).hexdigest()
    key=hashlib.sha256(json.dumps(signature,sort_keys=True).encode()).hexdigest()[:20]
    folder=cache/f'{obj.id}_{key}';folder.mkdir(parents=True,exist_ok=True)
    result_path=folder/'resolution.json'
    if result_path.exists():
        record=json.loads(result_path.read_text(encoding='utf-8'))
        if record.get('accepted') and Path(record['path']).is_file() and hashlib.sha256(Path(record['path']).read_bytes()).hexdigest()==record.get('sha256'):
            return record
    errors=[];inspection_candidate=None
    def accept(record, actual_mode):
        record.update(actual_mode=actual_mode,requested_mode=obj.source.mode,
                      decision_reason=obj.source.decision_reason,generation_trigger=obj.source.generation_trigger,
                      prior_attempts=list(errors))
        record['sha256']=hashlib.sha256(Path(record['path']).read_bytes()).hexdigest()
        save(result_path,record)
        return record
    candidates=[]
    if obj.source.mode in ('retrieve','auto'):
        if obj.source.asset_id:
            candidates=[a for a in catalog if a['asset_id']==obj.source.asset_id]
        else:candidates=metadata_candidates(obj,rank(obj.source.query or obj.semantic,catalog),
                                            vision_profile,folder/'local_selection')
        def candidate_stream():
            yield from candidates
            if not offline:
                exact=(obj.source.asset_id or '').removeprefix('polyhaven:')
                if (obj.source.asset_id or '').startswith('polyhaven:'):
                    # Reuse a downloaded original across requirement-only source
                    # revisions; it still undergoes fresh normalization/visual QA.
                    for previous in sorted(cache.glob('*/downloads/'+exact)):
                        if previous==folder/'downloads'/exact:continue
                        meshes=sorted(previous.glob('*.gltf'))+sorted(previous.glob('*.glb'))
                        if meshes:
                            # An interrupted sibling download can leave a valid
                            # glTF beside missing or stale buffers/textures.
                            try:
                                yield from search_polyhaven(obj.source.query or obj.semantic,
                                    previous.parent,asset_id=exact)
                                return
                            except Exception as error:
                                errors.append({'stage':'cached_original_integrity','error':str(error)[:500]})
                try:yield from search_polyhaven(obj.source.query or obj.semantic,folder/'downloads',
                    asset_id=obj.source.asset_id.removeprefix('polyhaven:') if (obj.source.asset_id or '').startswith('polyhaven:') else None,
                    selector=lambda rows:metadata_candidates(obj,rows,vision_profile,folder/'remote_selection'))
                except Exception as e:errors.append({'stage':'model_search','error':str(e)[:500]})
        found=False
        for i,candidate in enumerate(candidate_stream()):
            found=True
            try:
                if source_instances:candidate={**candidate,'same_source_instances':source_instances}
                context={'original_request':original_request} if original_request else {}
                record=prepare_candidate(obj,plan,candidate,folder/f'retrieved_{i}',vision_profile,quality,**context)
                if record['accepted']:
                    return accept(record,'retrieve')
                if Path(record.get('path','')).is_file():
                    inspection_candidate={**record,'inspection_only':True,'actual_mode':'retrieve'}
                errors.append({'stage':'retrieval_qa','candidate':candidate,'result':'rejected',
                               'review':source_visual_feedback(record)})
            except Exception as e:errors.append({'stage':'retrieval','error':str(e)[:500]})
        if not found:errors.append({'stage':'retrieval','result':'no_candidates','offline':offline})
    if obj.source.mode in ('generate','auto'):
        try:
            # A rejected reference is evidence about the query, not a reason
            # to stop the whole component.  Give the source agent one bounded
            # retry with the Judge's concrete findings and exclude the URLs
            # already inspected.  Each attempt has its own evidence folder.
            reference_feedback=[]
            search_feedback=None
            searched_queries=set()
            excluded_sources=set()
            for reference_attempt in range(2):
                search_folder=folder if reference_attempt==0 else folder/f'reference_search_attempt_{reference_attempt:02d}'
                references=image_candidates(obj,search_folder,offline,profile=vision_profile,
                                             feedback=reference_feedback if reference_attempt else None,
                                             search_feedback=search_feedback if reference_attempt else None,
                                             attempt=reference_attempt,
                                             exclude_sources=excluded_sources,
                                             exclude_queries=searched_queries,
                                             reference_generator=reference_generator)
                if not references:
                    errors.append({'stage':'reference_search','attempt':reference_attempt,
                                   'result':'no_references','offline':offline})
                    search_evidence=_reference_search_feedback(search_folder)
                    errors[-1]['search_evidence']=search_evidence
                    # A zero-result catalog pass is recoverable. Feed its
                    # query/result/filter evidence into one fresh query pass;
                    # offline mode and a caller-provided URL have no useful
                    # alternate search to perform.
                    if reference_attempt==0 and not offline and not obj.source.reference_image:
                        search_feedback=search_evidence
                        searched_queries=set(search_feedback.get('searched_queries',[]))
                        continue
                    break
                rejected=[]
                judge_rejections=[]
                accepted_reference_count=0
                qa_folder=folder if reference_attempt==0 else folder/f'reference_retry_{reference_attempt:02d}'
                qa_folder.mkdir(parents=True,exist_ok=True)
                for i,reference in enumerate(references[:6]):
                    qa_path=qa_folder/f'reference_qa_{i}.json'
                    ref_review=review_images([reference['path']],reference_geometry_request(obj),vision_profile,
                        qa_path,quality=quality,scope='reference')
                    if ref_review.get('accepted'):
                        accepted_reference_count+=1
                        if accepted_reference_count>2:
                            # Keep the generation budget bounded while
                            # allowing one alternate accepted reference to
                            # recover from a bad TRELLIS seed/mesh QA result.
                            continue
                        for seed in (42,43):
                            generated=folder/f'generated_{reference_attempt}_{i}_{seed}'
                            contract=generated/'component_contract.json'
                            try:
                                if not contract.exists():
                                    if generated.exists():
                                        errors.append({'stage':'generation','error':f'incomplete previous attempt preserved: {generated}'});continue
                                    run_logged([TRELLIS_PYTHON,'-u',Path(__file__).with_name('run_trellis1_component.py'),
                                        '--image',reference['path'],'--output',generated,'--seed',str(seed),'--component-id',obj.id,
                                        *sum((['--material-hint',hint] for hint in obj.source.material_requirements),[])],
                                        folder/f'generate_{reference_attempt}_{i}_{seed}.log')
                                raw=json.loads(contract.read_text(encoding='utf-8'))
                                texture=raw.get('texture_evidence') or {}
                                texture_path=Path(raw.get('texture_map_path',''))
                                texture_hash=raw.get('texture_map_sha256')
                                if (raw.get('export_mode')!='uv_base_color_glb' or not raw.get('pbr') or
                                    'base_color' not in raw.get('pbr_maps',[]) or
                                    texture.get('textured_meshes',0)<1 or texture.get('uv_coordinates',0)<1 or
                                    not texture_path.is_file() or
                                    hashlib.sha256(texture_path.read_bytes()).hexdigest()!=texture_hash):
                                    raise RuntimeError('TRELLIS.1 candidate is missing a verified, nonblank UV base-color texture')
                                source={'path':raw['exchange_mesh_path'],'generator':'microsoft/TRELLIS-image-large',
                                    'reference':reference,'contract':str(contract),'texture_map':str(texture_path),
                                    'texture_evidence':texture,'requires_generated_texture':True}
                                record=prepare_candidate(obj,plan,source,folder/f'generated_qa_{reference_attempt}_{i}_{seed}',vision_profile,quality)
                                if record['accepted']:
                                    return accept(record,'generate')
                                errors.append({'stage':'generation_qa','attempt':reference_attempt,'reference_index':i,
                                               'seed':seed,'result':'rejected'})
                            except Exception as generation_error:
                                errors.append({'stage':'generation','attempt':reference_attempt,'reference_index':i,
                                               'seed':seed,'error':str(generation_error)[:700]})
                        # Continue to the next reference that independently
                        # passed reference QA, up to the two-reference cap.
                        # This reference was accepted by the reference Judge;
                        # generation/mesh failure must not be misclassified as
                        # a reference rejection or used as feedback that
                        # targets the search query.
                        continue
                    rejection={
                        'query':reference.get('query'),'source_url':reference.get('source_url'),
                        'image_url':reference.get('image_url'),'page_title':reference.get('page_title'),
                        'provider':reference.get('provider'),
                        'catalog_id':reference.get('catalog_id'),
                        'license':reference.get('license'),
                        'status':ref_review.get('status'),
                        'summary':(ref_review.get('decision') or {}).get('summary') or ref_review.get('error'),
                        'findings':[{
                            key:finding.get(key) for key in ('severity','view','evidence','correction','repair_target')
                            if finding.get(key) is not None
                        } for finding in (ref_review.get('decision') or {}).get('findings',[])[:4]]}
                    rejected.append(rejection)
                    if ref_review.get('status')!='unavailable':
                        judge_rejections.append(rejection)
                    errors.append({'stage':'reference_qa','attempt':reference_attempt,
                                   'reference':reference,'result':'rejected','review':rejection})
                    for source_key in ('source_url','image_url','page_title'):
                        if reference.get(source_key):excluded_sources.add(reference[source_key])
                if reference_attempt==1:
                    break
                # Search feedback is built only from concrete Judge
                # rejections. If another candidate's Judge call was
                # unavailable, keep that image out of the feedback and still
                # let clear rejections trigger the bounded alternate search.
                # If every verdict was unavailable, there is no source
                # evidence on which to change the query.
                if not judge_rejections:break
                if obj.source.reference_image:break
                reference_feedback=judge_rejections
        except Exception as e:errors.append({'stage':'generation','error':str(e)[:700]})
    save(result_path,{'accepted':False,'component':obj.id,'requested_mode':obj.source.mode,
                     'decision_reason':obj.source.decision_reason,'errors':errors,'status':'unresolved',
                     'inspection_candidate':inspection_candidate})
    raise ComponentResolutionError(f'Unresolved {obj.id} ({obj.semantic}); evidence: {result_path}',
                                   {'component':obj.id,'status':'unresolved','attempts':errors,
                                    'inspection_candidate':inspection_candidate})
