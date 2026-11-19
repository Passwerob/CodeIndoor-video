"""Scene design decisions made before dimensions and construction are frozen."""
from pydantic import BaseModel, ConfigDict, Field, field_validator
from typing import Literal
import re
from planning_diagnostics import Diagnostics
from scene_composition import CompositionIntent


class DesignChoice(BaseModel):
    model_config=ConfigDict(extra='forbid')
    id: str = Field(pattern=r'^[A-Za-z][A-Za-z0-9_-]{0,63}$')
    layer: Literal['architecture','furniture','lighting','decoration','contents']
    activity: str
    selection: str = Field(min_length=2)
    quality_reason: str = Field(min_length=2)
    source_queries: list[str] = Field(default_factory=list,max_length=3)
    realization: Literal['parametric','retrieve'] = 'parametric'
    selected_asset_ids: list[str] = Field(default_factory=list,max_length=5)


class SceneDesign(BaseModel):
    model_config=ConfigDict(extra='forbid')
    room_identity: str = Field(min_length=2)
    users_and_activities: str = Field(min_length=2)
    composition: CompositionIntent | None = None
    spatial_composition: str = Field(min_length=2)
    focal_hierarchy: str = Field(min_length=2)
    material_palette: list[str] = Field(min_length=1)
    lighting_atmosphere: str = Field(min_length=2)
    choices: list[DesignChoice] = Field(min_length=1,max_length=32)
    deliberate_omissions: list[str] = Field(default_factory=list)

    @field_validator('deliberate_omissions',mode='before')
    @classmethod
    def normalize_deliberate_omissions(cls,value):
        if isinstance(value,str):
            return [value] if value.strip() else []
        return value


DESIGN_PROMPT='''Design the requested place before listing dimensions or making geometry. Return JSON.
First conceive the whole space, THEN organize groups, THEN select choices implementing it. Supply
composition: whole_space, groups, links, open_spaces, views and editing_priorities. Each group names
choice_ids and anchor_choice_id (design choices, not frozen objects), its role, organization and
density_and_rhythm. Every relationship inside a group's relationships MUST have both endpoint choice IDs
listed in that same group's choice_ids. Never put a cross-group relationship in a group's relationships.
Represent a relation between groups with links {id,a:<group_id>,b:<group_id>,intent,binding_mode}. Set
binding_mode="spatial" only when the intent specifies a physical side, adjacency or distance that must
constrain object poses; use "circulation" only when a traversable route between the groups is itself
required. Use "semantic" for daylight, shared lighting, visual hierarchy, material contrast, sightlines,
and qualitative negative-space intent. Semantic links remain design commitments, but must not invent
typed furniture constraints. If no meaningful within-group relationship exists, leave that group's
relationships empty. Add typed relationships between
choices inside their shared group: id, a_choice_id, b_choice_id, kind
(facing/distance/left_of/right_of/in_front_of/behind/supported_by/attached_to), justified minimum/maximum
distances, tolerance_deg, and reason (required explanatory text). There is no tolerance_m field.
All directions are A relative to B, in B's local frame: B front=-Y, back=+Y, left=-X, right=+X. For a chair used at a desk/table, the chair front faces the work surface, while entry and pullback are behind the chair or on a genuinely clear side; never route through the chair front into the desk. Treat a paired seated work surface as reached from the chair, not as a second person-sized floor approach. Convert directional relations through B's world yaw before choosing room coordinates: at yaw=90 degrees, B-local left (-X) points to world -Y, not world -X.
Only distance/facing may set maximum_m; every directional/contact relationship uses maximum_m=null.
Contact relationships use minimum_m=0. Set a separate distance relationship when an upper bound matters.
Thus a footrest in front of a seat is A=footrest, B=seat, kind=in_front_of; do not reverse these endpoints.
facing is orientation only: always set minimum_m=0 and maximum_m=null. If the user explicitly requires
a gap or reach interval, add a separate distance relationship for that requirement. Do not infer a positive
static footprint gap between a chair and the work surface it serves; a chair may tuck toward or partly under
the worktop, while its pullback remains behind the chair. supported_by/attached_to mean A is the physical child of B;
do not encode mere proximity or floor-covering contact as a new parent. Choices realized as layers of
one component may use integrated attached_to, with its recipe proving the internal construction.
Connect every choice to its group anchor through meaningful local chains, not an arbitrary star of distances.
Distance alone means spacing; it does not
say which side an object occupies. When the organization says between, in front of, behind, left/right of, or
facing, encode that direction explicitly. Support/attachment is lowered to the parent/interface graph.
Door leaves are oriented by their wall opening, jamb, hinge and swing. Never create a typed facing relationship
from an entrance door to a window or furnishing. Describe arrival sightlines in group prose or a view intent;
do not encode them as door-facing constraints or invented door-to-object distance relations.
Explain relative orientation, scale, spacing, related variation and why these elements belong together.
Place the room shell and fitted doors/windows/skylights in architectural-envelope groups, never inside a
furniture/activity group. Do not connect an opening to furniture with any typed pairwise relation
(facing, distance, left/right, in-front/behind, support or attachment): the wall-opening contract locates
it. Express daylight or arrival intent in composition prose or a link between the architecture and activity
groups. A window treatment may stay with its opening assembly in the architectural group. Door leaves are oriented
by the opening, jamb, hinge and swing, never by another object. Use SPATIAL groups of related masses/activities,
not material or catalog layers dispersed throughout the room. Assign a local light/textile/accent to the
activity or mass it serves instead of putting all lights or all textiles in their own group. Choices may
serve more than one group; not every global finish needs a group. Review whether the selected sources can
actually realize this spatial concept.
ENTRY ARCHETYPE IS FIXED. For every enclosed indoor room, every planned entrance must be realized as the
same standard single-leaf hinged door: a fixed door frame/jambs and head, one independently transformable
moving door panel, and one separate handle mounted on that panel. The handle is hardware, not part of the
moving leaf. Do not choose or emit an entrance that is only an empty wall opening,
doorway aperture, arch, or "opening" component with no door leaf. The leaf must be represented separately
so it can be listed in swing_leaf_ids and opened inward to 90 degrees by default; never use a complete
frame-head-leaf assembly as the swing component. Use another entrance type (arch, open passage, sliding door,
double door, or a different swing angle) only when the user explicitly requests it. This is an architectural
entry requirement, not an optional furnishing or a reason to add an extra entrance. The door frame and moving
leaf are both fitted children of the architectural shell; the handle is a child of the leaf. Do not emit a
scene-level attached_to relation between shell-sibling aperture fixtures (door leaf to frame, curtain to window).
Their physical association is expressed by the opening contract, parent tree and interface notes. Only emit an
attached_to relation when the decomposition parent graph can realize it.
Describe purposeful open spaces with stable IDs; these will become protected real volumes, so do not name an
occupied focal area as empty space. Views describe entering, pausing or exploring from representative
human positions, with group_ids; they become real perspective cameras. A group anchor is a reference
for relationships, not a compulsory visual focal point. Multiple centers and intentional irregularity
are valid. No symmetry/minimalism template, random scattering, uniform filling or decorative clutter
to conceal weak massing. State what to move/replace/remove first if composition does not work.
The composition must survive lowering to actual relationships, negative space and views, not just prose.
This is the scene-planning agent's design responsibility. Establish a specific room/place identity,
activities and users, coherent functional groups, focal hierarchy, finishes, lighting and lived-in detail.
Choose furniture TYPE and quality appropriate to the purpose, comfort and requested level of finish.
Explain choices: for example a refined reception/lounge activity warrants comfortable upholstered
seating or sofas rather than automatically reusing plain dining chairs. This is an example of functional
reasoning, NOT a mandatory room template. Consider meaningful alternatives before selecting a family.
Richness comes from choices that serve the brief, not from filling a checklist of furnishing categories.
Completeness means that the requested activities work and the room has a coherent focal hierarchy,
materials and lighting; it does not mean that every room needs separate art, textiles, plants, lamps and
small props. Add an object only when it contributes a requested function, a distinct style cue or useful
scale/lived-in detail that the existing architecture and furnishings do not already provide. Prefer a
surface finish or an integrated architectural detail over another freestanding object when both achieve
the same goal. Keep a generous, visually continuous area of open floor around the primary group and a
clear arrival view. If an inferred room is tight, enlarge it before compressing circulation; when the
user fixes the extent, keep required functions and omit lower-priority optional accents. Do not translate
adjectives such as rich, layered or realistic into one new object apiece. Avoid generic test fixtures.
When the user gives a complete item list, uses "only"/"just", or explicitly rejects extra items,
treat the requested inventory as closed: add no unlisted freestanding furniture, decor, plants, baskets,
textiles, lighting or lifestyle props. Room identity (such as "high-school bedroom") is context, not
permission to invent age-based belongings. Keep construction members and small details integrated into
the named objects; put tempting but unrequested additions in deliberate_omissions. If the brief is
genuinely open-ended, make a lean choice set and add only items required for the stated activities.
For a compact single-occupant room, establish only the primary 1-3 furnishing masses first, then add
objects needed for the stated activities. Keep optional freestanding decorative accents to at most two
unless the user asks for more; carry visual detail through material, silhouette, integrated construction
and a small number of deliberate focal objects instead of filling every surface.
Do not invent user constraints or extra historic carvings. Do not name ordinary routes, thresholds and
working clearances as separate protected empty-space features; those belong to circulation and access.
Do not turn an ordinary central floor, generic clear floor, chair pull-back, window frontage, entrance path, or visual separation between furniture groups into a protected open_spaces volume. These are outcomes of perimeter composition and the access/route contracts; put human-use areas in access_areas/routes and describe aesthetic breathing room in group or whole-space prose. Reserve open_spaces only for a distinct program feature explicitly requested or functionally central to the brief, such as a courtyard, stage floor, or dance area. Aesthetic breathing room does not create a pairwise furniture distance or another protected rectangle. Before returning, inspect every open_spaces item: it must name its own user activity or requested spatial feature, not a location where ordinary furniture should simply remain absent.
Before returning, perform a whole-space composition pass: identify important approach-to-activity sightlines,
organize secondary activities in relation to the primary activities, and preserve readable spatial depth.
Do not let incidental furnishings occupy a main threshold, an activity's required use space, or another
named open-space intent. Derive these priorities from the actual scene brief. State which
negative spaces separate the groups and which view proves that the room is ordered rather than merely full.
Choices are executable design commitments with stable IDs and one layer. Each selection describes
the visible intended result; quality_reason links it to use, comfort, materials, style or composition.
Apply the brief-matched reusable construction experience appended for this request. These are conditional
priors, not extra object-count requirements or a room template. Preserve the user's requested asset boundary.
source_queries are short English catalog terms for complicated shapes, not fabricated file paths.
Use local asset/material capabilities when appropriate and seek suitable external assets for upholstery,
organic forms and sculpture. A catalog label is only availability evidence, never visual acceptance.
If generation is unavailable in the brief, use existing generated samples or retrieval, not a generation
job. Retain editable aDSL architecture/regular structural furniture and contents. Do not generate a whole
opaque room. Do not specify positions, dimensions, frozen component IDs or recipes at this stage.
Check the whole design against the user's explicit requirements and actual activities before returning.
Add only a genuinely missing required function or requested visual feature; do not add optional categories
merely because they are common in furnished rooms.'''


def normalize_facing_relationships(design):
    """Keep orientation semantics from silently becoming invented gap constraints."""
    result=design.model_copy(deep=True)
    changes=[]
    if not result.composition:
        return result,changes
    for group in result.composition.groups:
        for relation in group.relationships:
            if relation.kind!='facing' or (relation.minimum_m==0 and relation.maximum_m is None):
                continue
            reason=relation.reason.casefold()
            explicit_distance=(
                re.search(r'\d+(?:\.\d+)?\s*(?:mm|cm|m|millimet(?:er|re)s?|centimet(?:er|re)s?|毫米|厘米|米)\b',reason)
                or any(token in reason for token in
                    ('user explicitly requires','explicit user distance','per user request',
                     '用户明确要求','用户指定间距','按用户要求')))
            if explicit_distance:
                continue
            changes.append({'group_id':group.id,'relation_id':relation.id,
                'before':{'minimum_m':relation.minimum_m,'maximum_m':relation.maximum_m},
                'after':{'minimum_m':0,'maximum_m':None},
                'reason':'Facing encodes orientation only; explicit spacing belongs in a separate distance relation.'})
            relation.minimum_m=0
            relation.maximum_m=None
    return result,changes


def scene_design_revision_required(feedback):
    """Only scene-level visual rejection should reopen the accepted design."""
    if not isinstance(feedback,dict):return False
    explicit=feedback.get('scene_design_revision_required')
    if explicit is not None:return bool(explicit)
    reason=str(feedback.get('replan_reason') or '')
    if reason.startswith('repeated_style_requirement_failure_after_local_render:'):
        return True
    visual=feedback.get('visual') or {}
    decision=visual.get('decision',visual) if isinstance(visual,dict) else {}
    return any(finding.get('severity','blocking')=='blocking' and
               finding.get('repair_target','decomposition')=='decomposition'
               for finding in decision.get('findings',[]))


def validate_design_coverage(outline,design):
    """A designed layer cannot silently disappear when lowering to objects."""
    diagnostics=Diagnostics('scene_design_coverage')
    expected={choice.id for choice in design.choices}
    bindings=outline.design_bindings
    if len(expected)!=len(design.choices):
        diagnostics.add('design_choice_ids','Design choices must have unique IDs',paths=['/design_bindings'])
    if set(bindings)!=expected:
        diagnostics.add('design_coverage','Bind every final accepted scene design choice exactly once using its exact ID. '
            f'Accepted choice IDs={sorted(expected)}; missing={sorted(expected-set(bindings))}; '
            f'unknown={sorted(set(bindings)-expected)}. Do not reuse preliminary-design aliases.',
            paths=['/design_bindings','/objects'])
    known={o.id for o in outline.objects}
    bound={id for ids in bindings.values() for id in ids}
    for i,obj in enumerate(outline.objects):
        if obj.id not in bound:
            diagnostics.add('unbound_design_object',f'{obj.id}: object has no accepted design purpose. '
                'Bind it under one exact accepted design choice ID or remove it if it is not user-requested '
                'and not selected in the design. Accepted choice IDs: '+
                f'{sorted(expected)}. Do not invent aliases or reuse preliminary-design IDs.',
                paths=[f'/objects/{i}'],component_ids=[obj.id])
    for key,ids in bindings.items():
        if not ids or not set(ids)<=known:
            diagnostics.add('design_components',f'Design choice {key} must bind actual component IDs',paths=['/design_bindings','/objects'])
    diagnostics.raise_if_any()
    return outline


def validate_design_sources(design,local,options):
    known={r['asset_id'] for r in [*local,*options.get('candidates',[])]}
    diagnostics=Diagnostics('design_source_allocation')
    for i,choice in enumerate(design.choices):
        if choice.realization=='retrieve' and not choice.selected_asset_ids:
            diagnostics.add('missing_source_choice',f'{choice.id}: retrieved design needs actual candidate IDs',
                            paths=[f'/choices/{i}'])
        unknown=set(choice.selected_asset_ids)-known
        if unknown:diagnostics.add('unknown_design_source',f'{choice.id}: unknown IDs {sorted(unknown)}',paths=[f'/choices/{i}'])
    diagnostics.raise_if_any()
    return design


def validate_source_choices(outline,local,options,*,allow_generation=True):
    from general_scene import scalar
    records={r['asset_id']:r for r in [*local,*options.get('candidates',[])]}
    diagnostics=Diagnostics('design_source_feasibility')
    for index,obj in enumerate(outline.objects):
        source=obj.acquisition
        if obj.parent is None:
            size=[scalar(v,outline.parameters) for v in obj.dimensions]
            if size[0]*size[1]>outline.extent[0]*outline.extent[1]+1e-6:
                diagnostics.add('room_capacity',f'{obj.id}: root footprint area exceeds the entire room; '
                    'no layout translation/yaw can fix it. Correct extent or dimensions before spatial planning.',
                    paths=['/extent',f'/objects/{index}/dimensions'],component_ids=[obj.id],
                    measured=size[:2],expected=list(outline.extent[:2]))
        if not allow_generation and source.strategy in {'auto','generate'}:
            diagnostics.add('generation_unavailable',f'{obj.id}: inference is disabled. Choose a real retrieved '
                'candidate, an appropriate editable recipe, or revise the design before layout. Do not '
                'route an unsupported optional ornament to an unavailable backend.',paths=[f'/objects/{index}'],component_ids=[obj.id])
        if not source.asset_id:continue
        paths=[f'/objects/{index}/acquisition',f'/objects/{index}/dimensions']
        record=records.get(source.asset_id)
        if record is None:
            diagnostics.add('unknown_asset','Unknown catalog asset ID '+source.asset_id,
                            paths=paths,component_ids=[obj.id])
            continue
        native=record.get('native_size_m')
        if native and all(v>0 for v in native) and source.front_rotation_deg%180==0:
            envelope=[scalar(v,outline.parameters) for v in obj.dimensions]
            factor=min(a/b for a,b in zip(envelope,native))
            utilization=[factor*b/a for a,b in zip(envelope,native)]
            if min(utilization)<.6:
                diagnostics.add('source_aspect_ratio',f'{obj.id}: known source cannot uniformly fit '
                    'the planned envelope with >=60% use on each axis. Revise the dimensions or source '
                    'before layout; external meshes must not stretch.',paths=paths,component_ids=[obj.id],
                    measured=utilization,expected={'native_size_m':native,'envelope_m':envelope})
    diagnostics.raise_if_any()
    return outline
