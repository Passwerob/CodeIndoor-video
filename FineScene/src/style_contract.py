"""Visible style requirements, independent of category and source backend."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from planning_diagnostics import Diagnostics
from curve_presets import CURVE_PRIMITIVES, describe_presets


class StyleRequirement(BaseModel):
    model_config=ConfigDict(extra='forbid')
    id: str = Field(pattern=r'^[A-Za-z][A-Za-z0-9_-]{0,63}$')
    feature: str = Field(min_length=8)
    visible_evidence: str = Field(min_length=12)
    geometry_need: Literal['regular_solids','swept_path','freeform_surface','appearance']


class StyleBinding(BaseModel):
    model_config=ConfigDict(extra='forbid')
    requirement_id: str
    component_ids: list[str] = Field(min_length=1)
    implementation: str = Field(min_length=12)
    part_ids: list[str] = Field(default_factory=list,description='For swept_path: qualified component/part IDs implementing the path')


def capabilities():
    return {'parametric_geometry':['box','cylinder','sphere','book','sweep','lathe'],
            'parametric_materials':['matte','wood','metal','verified PBR surface maps'],
            'sweep_limits':'Circular section swept along an explicit path or a named curve preset fitted to size; radius may taper linearly to radius_end (0 = cone tip); closed joins the ends. Compiles to overlapping cylinders/frustums and spheres: no carved relief, no non-circular section, no watertight union. Local path offsets are translated by center, rotated by rotation. size bounds the path including radius.',
            'lathe_limits':'Solid of revolution about the part local +Z from a named profile preset or explicit [radius, z] points (z non-decreasing, radius <= size_x/2, size_x == size_y). Solid, not hollow; no undercut loops, no carving.',
            **describe_presets(),
            'part_consistency':'Repeated members are copies of one design: like=<source part id> (+ mirror="x"|"y"|"xy" for mirrored counterparts, center = mirrored source center); design fields must match the source verbatim. assembly.symmetry declares whole-object mirror symmetry and is verified on the compiled geometry.',
            'unavailable_parametric_geometry':['dense sculpted relief','arbitrary freeform mesh','non-circular profile sweep (mouldings, cornices)','hollow shells','lofted soft forms'],
            'external_geometry':['retrieved rigid local mesh','TRELLIS.1 image-conditioned local mesh'],
            'policy':'A descriptive semantic name does not implement a shape. Curved rails and scrollwork may use the editable sweep primitive; dense sculpted relief must be a separately sourced rigid child. Reference and generated mesh must pass visual QA.'}


def validate_style_bindings(requirements,bindings,components,*,source_mode):
    diagnostics=Diagnostics('style_bindings')
    required={r.id:r for r in requirements}
    if len(required)!=len(requirements):diagnostics.add('style_ids','Duplicate style requirement IDs',paths=['/style_requirements'])
    actual={b.requirement_id:b for b in bindings}
    if len(actual)!=len(bindings) or set(actual)!=set(required):
        diagnostics.add('style_coverage','Style bindings must cover exactly the frozen style requirement IDs: '+str(sorted(required)),paths=['/style_bindings'])
    objects={o.id:o for o in components}
    for i,binding in enumerate(bindings):
        id=binding.requirement_id;path=f'/style_bindings/{i}'
        if not set(binding.component_ids)<=set(objects):
            diagnostics.add('style_components',f'Style {id}: binding references unknown scene component IDs',paths=[path+'/component_ids'])
            diagnostics.block('style_implementation','Unknown component IDs prevent checking this binding.',paths=[path])
            continue
        if id not in required:
            diagnostics.block('style_implementation','No frozen requirement matches this binding.',paths=[path])
            continue
        if required[id].geometry_need=='swept_path':
            external=any(source_mode(objects[key]) in {'retrieve','auto','generate'} for key in binding.component_ids)
            if not external and not binding.part_ids:
                diagnostics.add('sweep_binding',f'Style {id}: swept_path requires qualified component/part IDs; a textual sweep promise is insufficient',paths=[path+'/part_ids'])
            for qualified in binding.part_ids:
                owner,separator,part_id=qualified.partition('/')
                if not separator or owner not in binding.component_ids:
                    diagnostics.add('sweep_part_id',f'Style {id}: invalid qualified path part {qualified}',paths=[path+'/part_ids'])
                    continue
                obj=objects[owner]
                parts=getattr(obj,'parts',getattr(obj,'internal_parts',[]))
                part=next((p for p in parts if p.id==part_id),None)
                if part is None or (hasattr(part,'primitive') and part.primitive not in CURVE_PRIMITIVES):
                    diagnostics.add('sweep_part',f'Style {id}: {qualified} must exist and implement real sweep or lathe geometry',paths=[path+'/part_ids'])
        if required[id].geometry_need=='freeform_surface' and not any(
            source_mode(objects[key]) in {'retrieve','auto','generate'} for key in binding.component_ids):
            diagnostics.add('freeform_source',f'Style {id} requires freeform geometry: {required[id].feature}. '
                'Split sculpted local style geometry into a rigid retrieve/auto/generate child. A constant-radius sweep does not implement sculpted relief. '
                'Create an actual scene object with parent=<editable root ID>, internal_parts=[], and its own acquisition strategy; '
                'then bind component_ids to that object. A rigid internal part or text about sourcing does not invoke an asset backend. '
                'During decomposition replace /objects and affected bindings as a whole if necessary. Do not change the root acquisition, downgrade or delete the requirement.',paths=[path+'/component_ids','/objects'])
    diagnostics.raise_if_any()


def _blocking_style_ids(visual):
    return {requirement_id for finding in visual.get('decision',{}).get('findings',[])
            if finding.get('severity')=='blocking'
            for requirement_id in finding.get('requirement_ids',[])}


def replan_reason(plan,visual,previous_visual=None):
    if visual.get('status')!='needs_revision':return None
    if previous_visual is not None:
        repeated=_blocking_style_ids(previous_visual)&_blocking_style_ids(visual)
        if repeated:
            return 'repeated_style_requirement_failure_after_local_render:'+','.join(sorted(repeated))
    for finding in visual.get('decision',{}).get('findings',[]):
        if finding.get('severity')=='blocking' and finding.get('repair_target') in {'decomposition','source'}:
            return 'visual_requires_new_geometry_or_source'
    return None
