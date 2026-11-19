"""Bounded, auditable scene planning with frozen semantics and local repairs.

Decomposition -> per-component recipe -> layout. A failed recipe cannot edit
the object inventory; a failed layout cannot shrink objects or change sourcing.
This orchestrates sequential model calls, not multiple autonomous agents.
"""
from __future__ import annotations

import ast
import asyncio
import copy
import hashlib
import inspect
import json
from pathlib import Path

import finescene_env as fenv
import re
import sys
import time
from typing import Literal

import numpy as np
from pydantic import Field, JsonValue, model_validator

from general_planner import (StrictModel, Vec3, PartSpec, ComponentPlan, SourceDecision,
                             SceneIntent, PlannerScenePlan, RelationPlan, EditPlan, to_general_plan)
from general_scene import GeneralPlan, SceneObject, Source, Part, scalar, compile_local_geometry
from asset_physics import AssemblyContract, PartRelation, PartForm
from style_contract import StyleRequirement,StyleBinding,validate_style_bindings,capabilities
from curve_presets import CURVE_PRIMITIVES, curve_points, lathe_profile
from scene_spatial import (SpatialContract, SceneUseRequirements, validate_spatial_contract,
    validate_zone_capacity, coarse_layout, write_spatial_preview,
    normalize_corner_based_spatial_frame,anchor_centered_architectural_shells,
    normalize_facing_relations)
from planning_diagnostics import (Diagnostics, PlanningValidationError, failure_report,
    repair_report, prompt_evidence, validate_together, diagnostic_families,
    resolved_diagnostic_families)
from scene_design import (SceneDesign, DESIGN_PROMPT, validate_design_coverage,
    validate_source_choices,validate_design_sources,scene_design_revision_required,
    normalize_facing_relationships)
from scene_experience import planning_experience_prompt, spatial_experience_prompt, prepend_experience
from composition_metrics import composition_archetype_prompt, composition_hook
from surface_mounting import (SurfaceContact, validate_wall_contact_axis,
                              validate_wall_child_part_face_axis,validate_wall_functional_face)
from scene_composition import validate_concept, validate_lowering, lower_typed_relationships


class EmptyAgentOutput(ValueError):
    """The model call completed without a usable final answer."""


class PlanningRepairLimit(RuntimeError):
    """Stop paid retries while preserving the complete latest rejection evidence."""
    def __init__(self,stage,limit,report):
        self.measurements=report
        super().__init__(f'{stage}: exhausted {limit} repair calls; candidate remains rejected. '
                         'Inspect the saved failure batch before resuming.')


class JSONSyntaxPatch(StrictModel):
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    replacement: str = Field(max_length=8)
    reason: str = Field(min_length=8,max_length=240)


def apply_json_syntax_patch(raw,patch,error):
    if patch.end<patch.start or patch.end-patch.start>8:
        raise ValueError('JSON syntax repair must replace at most 8 characters')
    if min(abs(patch.start-error.pos),abs(patch.end-error.pos))>8:
        raise ValueError('JSON syntax repair must stay within 8 characters of the parser error')
    if patch.start==patch.end and not patch.replacement:
        raise ValueError('JSON syntax repair cannot be empty')
    repaired=raw[:patch.start]+patch.replacement+raw[patch.end:]
    json.loads(repaired)
    return repaired


def _repair_guidance(stage_name,error):
    """Map contract failures to the specific representation that must change."""
    import re
    message=str(error)
    cross_cutting=[]
    if 'frozen_support' in message or 'missing_support' in message:
        cross_cutting.append('Frozen support repair [SUP-2]: the decomposition froze a specific support chain '
            'and the assembly contract contradicts it. Do NOT substitute attached_to for supported_by, and do '
            'not re-parent the part to a different member to satisfy the check: the frozen interfaces are the '
            'contract. Emit the supported_by relation the decomposition names, with the child bottom on the '
            'named support top. If the two cannot physically coexist, the artifact is a decomposition defect, '
            'not a recipe defect: say so in the repair reason naming both part IDs and the measured overhang, '
            'and leave the recipe otherwise unchanged rather than faking a support that does not exist.')
    if 'support_footprint' in message:
        cross_cutting.append('Support footprint repair [SUP-3]: read measured first and decide which case this '
            'is. If measured.unbalanced_overhang_m is present, the part is a straddling member whose '
            'supports together stop short at one end: add the missing support there (support accepts a '
            'list), widen a support, or reduce the span. Even overhang at both ends does not fail, so do '
            'not chase it. If measured.declared_support_count is 1 and '
            'measured.excess_m is large, this part needs a second support rather than a nudge: a wide top, '
            'shelf or rail on one narrow side panel cannot be fixed by moving it, and the added support '
            'belongs to the decomposition. Only when excess_m is a small centering error should you move the '
            'part inside the reported support_top_m envelope. Keep every frozen dimension unchanged; never '
            'widen a support the decomposition fixed.')
    if 'support_gap' in message:
        cross_cutting.append('Support gap repair [SUP-4]: measured.gap_m gives the signed vertical offset and '
            'measured.part_bottom_z / support_top_z give both surfaces. Remove the gap by changing the part '
            'height or its local Z only, preserving the frozen footprint. If measured.fixable_by_translation is '
            'false and measured.overhang_m is positive, the part is not merely floating: it is unsupported in '
            'span, and moving it will not help. Report that as a support-span conflict instead of iterating. '
            'When the part rests on several supports, all of their top faces must share one height: a gap that '
            'appears only against one of them is a decomposition defect, and tilting the part to average the '
            'gap breaks the other contact.')
    if 'disconnected' in message:
        cross_cutting.append('Disconnected part repair [SUP-5]: measured.declared_relations lists the relations '
            'this part actually has. The chain ends at a non-root group. Connect it to the root through a real '
            'support/attachment into a member that already reaches the root, or if the group is meant to be '
            'grounded, make its true base the root. Do not add an invented relation to a part that does not '
            'physically touch it.')
    if 'unsupported_surface' in message:
        cross_cutting.append('Unsupported surface repair [SUP-6]: the named support is a sphere, a sweep, a lathe, or a '
            'top that is not horizontal (measured.up_axis_dot is not 1). A part cannot rest upright on it. '
            'Either give the part a real flat horizontal carrier between them, or declare an actual fastening '
            'with attached_to instead of a rest.')
    if 'opening_swing_assembly' in message:
        cross_cutting.append('Door representation repair: the named swing component contains stationary jamb/header '
            'parts. This cannot be fixed by translating or rotating the whole assembly. Reopen decomposition, keep '
            'the fixed frame in the architectural shell or a stationary component, and make the moving leaf with its '
            'handle a separate scene component. Preserve the original doorway requirements and bind only the leaf '
            'in swing_leaf_ids; never delete the door or hide the fixed parts to pass validation.')
    if 'aperture_attachment_contract' in message:
        cross_cutting.append('Aperture binding repair: set the exact opening-bound fixture to the architectural shell, '
            'mount=origin, grounded=false, support_part=null and surface_contact=null. Its world placement still has '
            'to match the opening after applying the inverse parent transform. Do not attach the frame to a solid '
            'wall face or exempt nearby furniture.')
    if 'outside declared scene extent' in message or 'scene_extent' in message:
        cross_cutting.append('Extent repair [EXT-1]: read each component\'s measured.violations and expected world '
            'bounds in diagnostic_report. Fix only the reported axis/side, using the suggested parent-local '
            'translation when translation_feasible=true and checking affected routes and relations again. A null '
            'suggestion means the component is constrained by its support/contact or cannot fit by translation; do '
            'not guess another axis or move a mounted object away from its host. For an opening-bound leaf, account '
            'for the shell\'s measured world floor offset in shell-local Z. '
            'If measured.violations is ABSENT, EMPTY, or names no axis, do not guess: a bare "outside extent" with '
            'no axis, actual value, bound and overshoot is a diagnostic defect, not a geometry instruction. Leave the '
            'candidate unchanged and state the missing evidence in the repair reason instead of moving something.')
    if 'mounting_coverage' in message or 'mounting_surface' in message:
        cross_cutting.append('Mounting repair [MNT-1]: first decide whether the requested contact is POSSIBLE. '
            'If the contact face crosses an opening, has no continuous real surface, or is shorter than the child '
            'contact span, then NO translation or rotation can satisfy it: repeating tangential translations cannot '
            'create a surface. In that case change the REPRESENTATION, not the coordinates: set surface_contact=null '
            'and parent the fixture to the architectural shell at mount=origin for an aperture, or bind it to a real '
            'structural part that actually has the needed face. Only when the report shows a real surface with a '
            'measurable offset along one axis should you adjust position, and then move only that axis within the '
            'reported legal interval. Never slide a fitted window or door leaf away from its opening to find wall.')
    if 'route_extent' in message or 'entrance_approach' in message or 'route_blocked' in message:
        cross_cutting.append('Route repair [RTE-1]: route points are WORLD XY centerline points, not component-local '
            'or door-to-target guesses. Put the endpoint at the entrance opening centerline within the reported '
            'tangent interval and inward-distance interval, then route through points inside the reported legal '
            'centerline bounds after subtracting half the route width. Keep route IDs, endpoints, widths and access '
            'owners unchanged. In joint recovery edit /spatial/routes/<index>/points; in layout-only stages edit '
            '/route_points. Use the candidate schema to select the actual field. If the report names a blocking '
            'obstacle bounding box, the blocked segment must move around THAT box; adding points without clearing '
            'the named box will reproduce the same failure.')
    if 'Fitted window component' in message or '_window_opening' in message:
        cross_cutting.append('Window-opening repair [WIN-1]: use the measured opening_world_center, target wall plane, '
            'interior projection and recommended_local_position_m in diagnostic_report. Preserve the opening wall and '
            'sill Z. Check the local dimensions and yaw against the opening normal and tangent BEFORE applying any '
            'position recommendation: translation cannot fix wrong orientation or an oversized projected footprint. '
            'Use the full inverse parent transform. Keep the aperture contract instead of adding a wall contact. '
            'If no position recommendation is supplied because the projection cannot fit, do not invent one: the '
            'opening or the fixture dimensions are the problem and belong to an upstream repair.')
    if 'directional footprint gap' in message:
        cross_cutting.append('Directional relation repair [REL-1]: use expected_a_direction_in_b_local_xy and '
            'expected_a_direction_world_xy from the measured report. Rotate the related component or move it to the '
            'correct side while keeping non-overlapping envelopes; a zero minimum means no extra gap, not permission '
            'to overlap. Recheck all affected routes and zones in the same candidate.')
    if 'unrealized_group_link' in message or 'intent_coverage' in message:
        cross_cutting.append('Composition coverage repair [GRP-1]: realize each final design group/view/link exactly '
            'once. Remove only stale inferred open-space or link intents that are not requested, or add the missing '
            'truthful group relation using the exact final choice IDs. Do not duplicate the same intent under another '
            'name. Do not dissolve or merge functional zones to make a link realizable: if a group cannot be '
            'connected, report which anchor and which activity the connection serves.')
    if cross_cutting:
        return ' '.join(cross_cutting)+' Address every independent issue and keep unrelated validated fields unchanged.'
    if 'nonprogrammatic_open_space' in message or 'protected open_spaces cannot encode ordinary' in message:
        return ('Validation diagnosis: open_spaces is only for a distinct requested or functionally programmed '
                'spatial feature. Remove EVERY generic central/open floor, breathing-room, chair pull-back, '
                'window-frontage, entrance-path or furniture-separation item from the protected list in one '
                'replacement; do not rename the same ordinary area and leave a second one behind. Keep the '
                'human-use geometry in access_areas/routes and retain aesthetic openness in composition prose. '
                'Preserve only a genuinely programmed open-space activity explicitly required by the brief.')
    if 'door_facing_relation' in message or 'entrance door leaf is oriented by its jamb and swing' in message:
        return ('Validation diagnosis: a door leaf has no furniture-facing direction; its orientation comes from the '
                'wall opening, jamb and swing. Remove the exact invalid typed facing relationship using its authorized '
                'removable path, or replace it with a truthful physical relation to the architectural opening. Preserve '
                'the entrance opening, door leaf and all unrelated activity-group relationships. Express arrival '
                'sightlines in composition prose or a view intent, and keep every design group connected to its anchor.')
    if 'object has no accepted design purpose' in message:
        return ('Validation diagnosis: a plausible object is not automatically part of the accepted design. For each '
                'flagged component, either remove it using its exact removable_paths entry when it is not required by '
                'the original request or final design, or bind it to the one semantically matching ID from '
                'scene_design_choice_ids. Keep design_bindings keys equal to that exact final choice-ID set; do not '
                'restore preliminary-design/category aliases. Preserve all other correct mappings and do not delete '
                'an original user requirement.')
    if 'Editable parameter' in message and ('recipe' in message or 'expression' in message):
        return ('Validation diagnosis: this parametric component owns a requested editable parameter, but the '
                'recipe has no real expression that depends on it. Replace the exact affected size/center/path '
                'field with an arithmetic expression using the frozen parameter (for example, the middle shelf '
                'center from shelf_lower_z+shelf_gap), and propagate the resulting support chain through parent/mount '
                'or parameter-dependent placements. Do not use a numeric constant, parameter*0, cancellation, or '
                'layout-only reference. Preserve all IDs, dimensions of rigid contents, and assembly relations.')
    if 'Visual Judge rejected the parametric sweep implementation for style requirement' in message:
        return ('Validation diagnosis: this style feature already failed a fresh render/Judge review as a '
                'parametric tube sweep. Add a coherent rigid sourced child with acquisition.strategy="auto" '
                '(or retrieve/generate as justified), parent it to the editable root, and bind the failed '
                'style requirement to that child. Keep the editable carcass, shelves, supported books and '
                'edit behavior in the parametric root; do not repeat or rename the rejected sweep.')
    if 'shell_clearance_as_assembly' in message:
        return ('Validation diagnosis: remove the floor-to-ceiling internal_clearances entry from the room shell. '
                'Room clear height is determined by the shell envelope and the measured floor/ceiling geometry. '
                'Only retain a clear-height target when the user explicitly requested one; then express it in the '
                'shell features and provide enough total height for the slab and ceiling thicknesses.')
    if 'opening_intersection' in message:
        return ('Validation diagnosis: only solid wall/casing members may be reported for intersecting an accepted '
                'architectural aperture. Window frames, glazing, glazing bars, shutters and sills are intended to '
                'occupy their matching window opening and must stay fitted there. At an entrance, keep the frozen '
                'door leaf and hardware, but park/swing them beside the adjacent wall pier so the required clear '
                'passage remains usable. Use the exact size/center/rotation paths and measured overlap; preserve '
                'frozen opening dimensions and all unrelated geometry. Split wall or casing members around the '
                'clear passage.')
    if stage_name.startswith('recipe_'):
        match=re.search(r'Frozen internal clearance missing/weakened:\s*([A-Za-z][\w-]*)->([A-Za-z][\w-]*)\s*>=\s*([0-9.]+)m',message)
        if match:
            part,target,minimum=match.groups()
            return (f'Validation diagnosis: frozen clearance {part}->{target} >= {minimum}m is missing from assembly. '
                    f'Add an AssemblyContract relation {{"kind":"clearance","part":"{part}","target":"{target}",'
                    f'"minimum":{minimum},"reason":"preserve the frozen {minimum} m functional clearance"}} '
                    'to the existing assembly using the /assembly replacement. Preserve existing roots and relations. '
                    'Do not move, resize, or change the shelf_pitch formulas to simulate this declaration; the validator '
                    'requires this exact semantic clearance record.')
        match=re.search(r'Missing required internal part ([A-Za-z][\w-]*)',message)
        if match:
            return (f'Validation diagnosis: the frozen internal part {match.group(1)} is absent. Restore its exact ID '
                    'as a real recipe part; do not remove or rename the frozen intent.')
        if 'max_penetration' in message and 'less than or equal to 0.03' in message:
            return ('Validation diagnosis: one or more assembly relations set max_penetration above the '
                    'contract limit of 0.03 m. Do not increase the allowed penetration or use it to hide '
                    'a bad joint. Move/resize the named geometry to a real contact within the existing '
                    'allowance, or remove the invented relation if the parts are not actually fastened.')
        if 'Structural recipe must contain exactly the frozen structure/detail IDs' in message:
            return ('Validation diagnosis: the structural stage contains deferred or invented parts. '
                    'Replace /parts with exactly one recipe for every frozen non-content structure/detail ID; '
                    'do not include any books or contents, even under renamed IDs. Remove assembly relations '
                    'whose endpoints are removed, retain the frozen structure IDs and measured geometry, and '
                    'let the assigned content batches construct the books later.')
    return ''


def local_repair_stage_guidance(stage_name,brief=''):
    """Give each repair stage paths that match its actual candidate schema."""
    if stage_name=='access_faces':
        return ('The component IDs required by validation are the exact authoritative human-use root IDs in frozen_context. '
            'Remove every extra allowed_sides/reasons key using operation="remove" at its listed removable_path. '
            'For missing required keys, replace the complete affected mapping with exactly the required IDs. '
            'Do not include a table or work surface whose access_required flag is false. ')
    if stage_name=='decomposition':
        guidance=('The decomposition inventory is NOT frozen. You may replace /objects, /style_bindings, '
            '/zones and /requirements to split, add or regroup components and update references coherently. '
            'Preserve the ORIGINAL request_contract, contents, style requirements, quality and editable root. '
            'External descendants must be real objects with their own acquisition, not internal_parts '
            'with a source promise in semantic/reason text. Replace the whole parent object/list if a '
            'needed field does not yet exist as an allowed leaf pointer. ')
        return guidance
    if stage_name=='spatial_reconstruction_candidate':
        guidance=('This joint recovery candidate has nested JSON roots. Replace spatial fields only under '
            '/spatial: zones at /spatial/zones, access areas at /spatial/access_areas, routes at '
            '/spatial/routes and relations at /spatial/relations. Composition fields are under '
            '/composition, placements under /layout/placements, wall contacts under /mounting_contacts, '
            'and the explanation under /reason. Never use top-level /zones, /routes, /access_areas, '
            '/relations or /placements; those paths do not exist in this candidate. Preserve physical '
            'component IDs and ORIGINAL user requirements. You may revise inferred design assumptions '
            'coherently when physically justified; keep required human-use roots connected to real entrances. '
            'For an absent list element, replace its whole existing nested list; never replace a nonexistent index. ')
        return guidance+spatial_experience_prompt(brief)
    if stage_name.startswith('spatial'):
        return ('This spatial DESIGN is not frozen. Preserve physical component IDs and ORIGINAL user requirements. '
            'You may replace whole /zones,/access_areas,/routes,/relations lists to remove unnecessary inferred '
            'areas/routes and repair their graph together. An access_required=false component does not need '
            'an independent area. Do not rename that area to an opening ID or an invented owner. '
            'Keep all required human-use roots connected to real entrances. Change inferred design '
            'assumptions coherently when physically justified; do not preserve a known invalid grouping. '
            'For an absent list element, replace its whole existing list; never replace a nonexistent index. ')
    if stage_name.startswith('coarse_layout'):
        return ('Preserve all IDs and frozen requirements. For an absent optional recipe field, replace its whole '
            'existing /parts/{i} object and preserve all current values; do not replace a JSON pointer that is '
            'not present or invent schema fields. '+spatial_experience_prompt(brief))
    return ('Preserve all IDs and frozen requirements. For an absent optional recipe field, replace its whole '
        'existing /parts/{i} object and preserve all current values; do not replace a JSON pointer that is '
        'not present or invent schema fields. ')


def _lower_frozen_assembly_relations(obj, recipe):
    """Lower frozen part metadata, supports and clearances into the recipe contract.

    These relations are already frozen in the accepted decomposition. Asking the
    model to copy them into every local recipe creates avoidable repair turns and
    can make the recipe's assembly contract disagree with the request contract.
    The same applies to frozen rigidity and part-form metadata. Geometry is still
    measured and audited after lowering.
    """
    recipe=recipe.model_copy(deep=True)
    intents={part.id:part for part in obj.internal_parts}
    for part in recipe.parts:
        intent=intents.get(part.id)
        if intent is None:
            continue
        part.rigidity=intent.rigidity
        if intent.form is not None:
            part.form=intent.form.model_copy(deep=True)
        if intent.attached_to and intent.mount:
            part.parent=intent.attached_to
            part.mount=intent.mount
            part.center=(*part.center[:2],0)
    has_frozen_interfaces=any(part.support_targets() or part.attached_to for part in obj.internal_parts)
    if recipe.assembly is None and not has_frozen_interfaces and not obj.internal_clearances:
        return recipe
    relations=list(recipe.assembly.relations) if recipe.assembly is not None else []
    diagnostics=Diagnostics('frozen_assembly_relations')

    def same_pair(relation,kind,part,target):
        if relation.kind!=kind:return False
        if kind=='clearance':return {relation.part,relation.target}=={part,target}
        return relation.part==part and relation.target==target

    for intended in obj.internal_parts:
        targets=intended.support_targets()
        if targets:
            expected=[('supported_by',target) for target in targets]
        elif intended.attached_to:
            expected=[('attached_to',intended.attached_to)]
        else:
            continue
        expected_targets={target for _,target in expected}
        # A part carrying more than one support legitimately has several
        # supported_by relations; only an unplanned target or a wrong kind conflicts.
        pair_conflicts=[r for r in relations if r.part==intended.id and r.target not in expected_targets
                        and r.kind in {'supported_by','attached_to'}]
        kind_conflicts=[r for r in relations if r.part==intended.id and r.target in expected_targets
                        and r.kind in {'supported_by','attached_to'}
                        and (r.kind,r.target) not in expected]
        if pair_conflicts or kind_conflicts:
            found=sorted({f'{r.kind}->{r.target}' for r in (*pair_conflicts,*kind_conflicts)})
            kind=expected[0][0]
            code='frozen_support' if kind=='supported_by' else 'frozen_attachment'
            label='support' if kind=='supported_by' else 'attachment'
            wanted=', '.join(f'{k}->{t}' for k,t in expected)
            diagnostics.add(code,
                f'Frozen {label} conflict for {intended.id}: expected {wanted}, found {found}',
                paths=['/assembly'],component_ids=[obj.id])
        for kind,target in expected:
            if any(same_pair(r,kind,intended.id,target) for r in relations):
                continue
            if kind=='supported_by':
                reason=(f'Frozen internal support: {intended.id} rests on {target}.'
                        if len(targets)==1 else
                        f'Frozen internal support: {intended.id} rests on {target} '
                        f'(one of {len(targets)} frozen supports).')
            else:
                reason=f'Frozen internal attachment: {intended.id} is fastened to {target}.'
            relations.append(PartRelation(kind=kind,part=intended.id,target=target,reason=reason))

    for clearance in obj.internal_clearances:
        matches=[(i,r) for i,r in enumerate(relations)
                 if same_pair(r,'clearance',clearance.a,clearance.b)]
        conflicting=[r for r in relations if {r.part,r.target}=={clearance.a,clearance.b}
                     and r.kind!='clearance']
        if conflicting and clearance.minimum_m>.002:
            kinds=sorted({r.kind for r in conflicting})
            diagnostics.add('frozen_clearance_conflict',f'Frozen clearance conflicts with {kinds} relation for {clearance.a}->{clearance.b}',
                            paths=['/assembly'],component_ids=[obj.id])
        if not matches:
            relations.append(PartRelation(kind='clearance',part=clearance.a,target=clearance.b,
                minimum=clearance.minimum_m,reason=clearance.reason))
            continue
        for index,relation in matches:
            if relation.minimum<clearance.minimum_m:
                relations[index]=relation.model_copy(update={'minimum':clearance.minimum_m,
                                                               'reason':clearance.reason})
    children={part.id for part in recipe.parts if part.parent}
    children.update(part.id for part in obj.internal_parts if part.support_targets() or part.attached_to)
    if recipe.assembly is None:
        roots=sorted({part.id for part in recipe.parts}-children)
    else:
        roots=[part_id for part_id in recipe.assembly.roots if part_id not in children]
    if not roots:
        diagnostics.add('assembly_roots','No base part remains after applying frozen parent/support interfaces.',
                        paths=['/assembly'],component_ids=[obj.id])
    diagnostics.raise_if_any()
    recipe.assembly=AssemblyContract(roots=roots,relations=relations)
    return recipe


def component_recipe_context(outline, obj, brief, style_requirements=None):
    """Keep a recipe cache key independent of unrelated sibling inventory."""
    from material_catalog import capabilities
    style_requirements=style_requirements or []
    return {
        'component':obj.model_dump(mode='json'),
        'parameters':outline.parameters,
        'available_materials':capabilities(),
        'user_brief':brief,
        'component_requirements':[r.requirement for r in outline.requirements if obj.id in r.component_ids],
        'edit_checks':[c.model_dump() for c in outline.edit_checks],
        'editable_parameters':{c.parameter:outline.parameters[c.parameter] for c in outline.edit_checks},
        'style_bindings':[b.model_dump() for b in outline.style_bindings if obj.id in b.component_ids],
        'evaluated_size_m':[scalar(v,outline.parameters) for v in obj.dimensions],
        'externally_acquired_descendants_are_not_recipe_parts':True,
    }


def _fitted_window_feature(intent, low, high, opening, axis, plane, wall_thickness):
    if intent is None:return False
    label=f'{intent.id} {intent.semantic}'.casefold()
    if not any(token in label for token in ('window','sash','glaz','mullion','shutter')):return False
    other=1-axis;margin=.16
    fitted_laterally=(low[other]>=opening.offset-opening.width/2-margin and
                      high[other]<=opening.offset+opening.width/2+margin)
    fitted_vertically=(low[2]>=opening.sill_height-margin and
                       high[2]<=opening.sill_height+opening.height+margin)
    wall_band=(plane-wall_thickness-margin,plane+margin) if opening.wall.endswith('max') else (
        plane-margin,plane+wall_thickness+margin)
    fitted_to_reveal=low[axis]<=wall_band[1] and high[axis]>=wall_band[0]
    return fitted_laterally and fitted_vertically and fitted_to_reveal


def validate_architectural_openings(component, recipe, extent, parameters, openings, placement):
    """Report every shell part crossing an accepted opening with exact recipe paths."""
    import numpy as np
    from general_scene import _transform_local_bounds

    local=SceneObject(id=component.id,semantic=component.semantic,size=component.dimensions,
        parts=[Part.model_validate(part.model_dump()) for part in recipe.parts],assembly=recipe.assembly)
    plan=GeneralPlan(scene_id='shell_interfaces',description=component.semantic,
        extent=extent,parameters=parameters,objects=[local])
    evidence={}
    compile_local_geometry(plan,local,evidence=evidence)
    params={**parameters,**dict(zip(('w','d','h'),[scalar(v,parameters) for v in component.dimensions]))}
    origin=np.array([scalar(v,params) for v in placement.position])
    diagnostics=Diagnostics(f'architectural_openings_{component.id}')
    intents={part.id:part for part in getattr(component,'internal_parts',[])}
    for opening in openings:
        axis=0 if opening.wall.startswith('x') else 1
        other=1-axis
        sign=-1 if opening.wall.endswith('min') else 1
        plane=sign*extent[axis]/2
        for index,part in enumerate(recipe.parts):
            record=evidence['parts'][part.id]
            low,high=np.array(_transform_local_bounds(record['bounds'],placement.yaw_deg,origin))
            intent=intents.get(part.id)
            if getattr(opening,'kind',None)=='window' and intent:
                # Window construction occupies the aperture by design; only
                # exempt the frozen features that are actually fitted there.
                wall_thickness=scalar(parameters.get('wall_thickness',.2),params)
                if _fitted_window_feature(intent,low,high,opening,axis,plane,wall_thickness):
                    continue
            vertical=min(high[2],opening.sill_height+opening.height)-max(low[2],opening.sill_height)
            lateral=min(high[other],opening.offset+opening.width/2)-max(low[other],opening.offset-opening.width/2)
            reaches_wall=(high[axis]>plane-.4 if sign>0 else low[axis]<plane+.4)
            if vertical<=.015 or lateral<=.015 or not reaches_wall:
                continue
            diagnostics.add('opening_intersection',
                f'{opening.id}: {part.id} overlaps the clear aperture by {vertical:.4f} m vertically and '
                f'{lateral:.4f} m laterally; move or resize this solid part clear of the opening.',
                paths=[f'/parts/{index}/size',f'/parts/{index}/center',f'/parts/{index}/rotation'],
                component_ids=[component.id],
                measured={'world_bounds_m':[[float(v) for v in row] for row in (low,high)],
                          'vertical_overlap_m':float(vertical),'lateral_overlap_m':float(lateral),
                          'wall_plane_m':float(plane)})
    diagnostics.raise_if_any()


def spatial_outline_context(outline):
    """Placement uses envelopes and function, not repeated book/material recipes."""
    data=outline.model_dump(mode='json')
    fields=('id','semantic','category','dimensions','parent','mount','support_part','surface_contact','grounded',
            'access_required','support_footprint','collision_role','interface_notes')
    data['objects']=[{key:obj[key] for key in fields} for obj in data['objects']]
    data['preliminary_design_requirements']=data.pop('requirements',[])
    data['authority']='Inventory, dimensions and physical attachments are fixed. Preliminary design requirements and inferred aisle widths are model proposals. The separately supplied original user spatial requirements are authoritative.'
    data.pop('style_bindings',None);data.pop('design_bindings',None)
    return data


def preserve_circulation_program(spatial, circulation, contract):
    """Keep approved human approaches authoritative across spatial replans."""
    areas={item.owner_id:item for item in spatial.access_areas}
    if set(areas)!={item.owner_id for item in circulation.approaches} or len(areas)!=len(spatial.access_areas):
        raise ValueError('Spatial replanning must preserve every approved functional approach exactly once.')
    for approach in circulation.approaches:
        area=areas[approach.owner_id]
        if area.side!=approach.side or any(actual+1e-8<approved
                for actual,approved in zip(area.size,approach.size)):
            raise ValueError(f'{approach.owner_id}: spatial revision weakened the approved human approach side/size; '
                             'preserve or widen its owner-local area and change the surrounding layout.')
    user_width=contract.scene_use.minimum_route_width_m if contract.scene_use else None
    routes={route.id:route for route in spatial.routes}
    entrances={item.id for item in spatial.openings if item.kind=='entrance'}
    graph={node:set() for node in entrances|{area.id for area in spatial.access_areas}}
    for route in spatial.routes:
        if route.start in graph and route.end in graph:
            graph[route.start].add(route.end);graph[route.end].add(route.start)
    reachable=set(entrances);pending=list(entrances)
    while pending:
        for node in graph[pending.pop()]-reachable:
            reachable.add(node);pending.append(node)
    if set(graph)-reachable:
        raise ValueError('Spatial revision disconnected approved human approaches from every entrance.')
    anchor_id=getattr(circulation,'shared_aisle_owner_id',None)
    main=routes.get('main_aisle')
    if main is not None:
        endpoint=main.end if main.start in entrances else main.start if main.end in entrances else None
        main_area=next((area for area in spatial.access_areas if area.id==endpoint),None)
        if main_area is None or (anchor_id is not None and main_area.owner_id!=anchor_id):
            raise ValueError('Spatial revision changed the approved shared-aisle anchor.')
        anchor=next(item for item in circulation.approaches if item.owner_id==main_area.owner_id)
        expected=max(anchor.final_route_width,getattr(circulation,'shared_aisle_width',None) or 0.,user_width or 0.)
        if main.width+1e-8<expected:
            raise ValueError(f'main_aisle: spatial revision narrowed the approved {expected:.3f}m shared route '
                             f'to {main.width:.3f}m. Preserve the measured circulation width and rearrange obstacles.')
        for approach in circulation.approaches:
            if approach.owner_id==main_area.owner_id:continue
            route=routes.get('reach_'+approach.owner_id)
            expected=max(approach.final_route_width,user_width or 0.)
            if route is None or main_area.id not in {route.start,route.end}:
                raise ValueError(f'{approach.owner_id}: spatial revision removed its branch from the shared aisle.')
            if route.width+1e-8<expected:
                raise ValueError(f'reach_{approach.owner_id}: spatial revision narrowed the approved {expected:.3f}m '
                                 f'local approach to {route.width:.3f}m. Preserve its width and rearrange obstacles.')
    else:
        # Accept older checkpoints with one direct entrance-to-owner route per area.
        for approach in circulation.approaches:
            route=routes.get('reach_'+approach.owner_id)
            expected=max(approach.final_route_width,user_width or 0.)
            if route is None or not (route.start in entrances or route.end in entrances):
                raise ValueError(f'{approach.owner_id}: spatial revision removed the approved entrance route.')
            if route.width+1e-8<expected:
                raise ValueError(f'reach_{approach.owner_id}: spatial revision narrowed the approved {expected:.3f}m '
                                 f'human route to {route.width:.3f}m. Preserve the measured circulation width and rearrange obstacles.')
    return spatial


def apply_circulation_program(spatial, circulation, outline, contract, design=None):
    """Reattach frozen human-use geometry after an Agent revises the room design."""
    from scene_spatial import AccessArea,CirculationRoute,opening_polygon
    entrances=[opening for opening in spatial.openings if opening.kind=='entrance']
    if not entrances:raise ValueError('The spatial design must retain a real entrance for human circulation.')
    entry=entrances[0]
    entry_point=opening_polygon(entry,outline.extent).mean(axis=0)
    areas=[];routes=[]
    minimum=(contract.scene_use.minimum_route_width_m or 0.) if contract.scene_use else 0.
    approaches={item.owner_id:item for item in circulation.approaches}
    anchor_id=getattr(circulation,'shared_aisle_owner_id',None)
    if anchor_id is not None and anchor_id not in approaches:
        raise ValueError('The shared-aisle anchor must be one of the approved functional access owners.')
    if anchor_id is None and approaches:
        zone_by_owner={component_id:zone for zone in spatial.zones for component_id in zone.component_ids}
        def anchor_score(item):
            zone=zone_by_owner.get(item.owner_id)
            center=np.asarray(zone.center,dtype=float) if zone is not None else entry_point
            return (float(np.linalg.norm(center-entry_point)),item.owner_id)
        anchor_id=min(circulation.approaches,key=anchor_score).owner_id
    shared_width=getattr(circulation,'shared_aisle_width',None)
    if shared_width is None and circulation.approaches:
        shared_width=float(np.median([item.final_route_width for item in circulation.approaches]))
    shared_width=max(float(shared_width or 0.),minimum)
    for approach in circulation.approaches:
        area_id='use_'+approach.owner_id
        areas.append(AccessArea(id=area_id,owner_id=approach.owner_id,purpose=approach.reason,
            center=(0,0),size=approach.size,side=approach.side,along_face=approach.along_face))
    if anchor_id is not None:
        anchor=approaches[anchor_id]
        route_width=max(anchor.final_route_width,shared_width,minimum)
        route_start=entry_point.copy()
        axis=0 if entry.wall.startswith('x') else 1
        sign=-1 if entry.wall.endswith('min') else 1
        route_start[axis]=sign*(outline.extent[axis]/2-route_width/2-.01)
        routes.append(CirculationRoute(id='main_aisle',start=entry.id,end='use_'+anchor_id,
            points=[tuple(route_start),(0,0)],width=route_width,
            purpose='Shared room aisle from the entrance to the primary activity area.'))
        for approach in circulation.approaches:
            if approach.owner_id==anchor_id:continue
            branch_width=max(approach.final_route_width,minimum)
            routes.append(CirculationRoute(id='reach_'+approach.owner_id,start='use_'+anchor_id,
                end='use_'+approach.owner_id,points=[(0,0),(.01,0)],width=branch_width,
                purpose=approach.reason))
    spatial=spatial.model_copy(update={'access_areas':areas,'routes':routes})
    if design is not None:
        # Circulation replaces route IDs; rebuild design-link references against
        # the new graph before composition validation sees the contract.
        spatial=lower_typed_relationships(spatial,design,outline)
    return spatial


def recipe_repair_paths(data):
    return ['/parts']+(['/assembly'] if 'assembly' in data else [])+[
        f'/parts/{i}' for i in range(len(data.get('parts',[])))]+[
        f'/parts/{i}/{key}' for i,part in enumerate(data.get('parts',[])) if isinstance(part,dict)
        for key in ('size','center','rotation','color','finish','book','surface','parent','mount','path','radius') if key in part]


class Acquisition(StrictModel):
    strategy: Literal['parametric','retrieve','auto','generate']
    asset_id: str | None = None
    front_rotation_deg: float = 0
    reason: str = Field(min_length=12)
    retrieval_query: str = ''
    reference_query: str = ''
    reference_image: str | None = None
    generation_reason: str = ''
    custom_appearance: str = ''
    reference_requirements: list[str] = Field(default_factory=list)

    @model_validator(mode='after')
    def check_strategy_fields(self):
        if self.strategy in {'auto','generate'}:
            missing=[]
            if not(self.reference_query or self.reference_image):missing.append('reference_query or reference_image')
            if not self.reference_requirements:missing.append('reference_requirements')
            if missing:raise ValueError('Acquisition '+self.strategy+' requires: '+', '.join(missing))
        if self.strategy in {'auto','retrieve'} and not self.retrieval_query:
            raise ValueError('Acquisition requires retrieval_query')
        return self

    def executable(self, quality):
        return SourceDecision(mode=self.strategy, reason=self.reason,asset_id=self.asset_id,
            front_rotation_deg=self.front_rotation_deg,
            retrieval_query=self.retrieval_query, reference_query=self.reference_query,
            reference_image=self.reference_image,
            trellis_reason=(self.generation_reason or self.reason) if self.strategy in {'auto','generate'} else '',
            trellis_required=self.strategy=='generate', quality_target=quality,
            generation_trigger=('custom_appearance' if self.strategy=='generate' else
                                'retrieval_failure' if self.strategy=='auto' else 'not_applicable'),
            reference_requirements=self.reference_requirements)


class InternalPartIntent(StrictModel):
    id: str = Field(pattern=r'^[A-Za-z][A-Za-z0-9_-]{0,63}$')
    semantic: str
    rigidity: Literal['rigid','parametric']
    role: Literal['structure','content','detail']
    # A horizontal member straddling two uprights needs both, so support accepts
    # one ID or a list. A bare string stays valid for every existing plan.
    support: str | list[str] | None = None
    attached_to: str | None = None
    mount: Literal['top','bottom'] | None = None
    form: PartForm | None = None
    requirement_tags: list[str] = Field(default_factory=list)

    @model_validator(mode='after')
    def check_interface(self):
        supports=self.support_targets()
        if supports and self.attached_to:
            raise ValueError(f'{self.id}: choose support for resting on a top face or attached_to for a fastened joint')
        if self.mount and not self.attached_to:
            raise ValueError(f'{self.id}: mount requires a frozen attached_to target')
        if len(supports)!=len(set(supports)):
            raise ValueError(f'{self.id}: duplicate support targets {supports}')
        if self.id in supports:
            raise ValueError(f'{self.id}: a part cannot support itself')
        return self

    def support_targets(self) -> list[str]:
        """Normalize the single-or-multi support field to an ordered list."""
        if self.support is None:
            return []
        if isinstance(self.support,str):
            return [self.support] if self.support else []
        return [value for value in self.support if value]


def _part_form_semantic_conflict(part: InternalPartIntent) -> bool:
    """Reject obvious mismatches between one physical part and its form contract."""
    if part.form is None:
        return False
    if part.form.kind=='beam':
        return bool(re.search(
            r'\b(?:frame|assembly|set)\b.{0,80}\b(?:with|of|including|comprising)\b'
            r'.{0,120}\b(?:stiles?|rails?|uprights?|posts?|mullions?|members)\b',
            part.semantic, flags=re.IGNORECASE))
    if part.form.kind=='panel':
        return bool(re.search(r'\b(?:plinth|pedestal|column|pillar|solid block)\b',
                              part.semantic,flags=re.IGNORECASE))
    return False


def _is_shell_floor_ceiling_clearance(clearance, internal_parts) -> bool:
    """Room clear height belongs to shell dimensions and construction, not an assembly gap."""
    parts={part.id:part for part in internal_parts}
    a=parts.get(clearance.a);b=parts.get(clearance.b)
    if a is None or b is None:
        return False
    labels=[f'{part.id} {part.semantic}'.casefold() for part in (a,b)]
    floor=any(re.search(r'\b(?:floor|slab|deck)\b',label) for label in labels)
    ceiling=any(re.search(r'\b(?:ceiling|roof)\b',label) for label in labels)
    return floor and ceiling


class ObjectIntent(StrictModel):
    id: str = Field(pattern=r'^[A-Za-z][A-Za-z0-9_-]{0,63}$')
    semantic: str = Field(min_length=1,max_length=400)
    category: str
    dimensions: Vec3
    rigidity: Literal['rigid','parametric']
    decomposition_reason: str = Field(min_length=2)
    required_features: list[str] = Field(min_length=1)
    material_requirements: list[str] = Field(min_length=1)
    interface_notes: list[str] = Field(default_factory=list)
    internal_parts: list[InternalPartIntent] = Field(default_factory=list,max_length=160)
    internal_clearances: list['RequiredClearance'] = Field(default_factory=list,max_length=160)
    visual_importance: int = Field(default=3,ge=1,le=5)
    parent: str | None = None
    mount: Literal['origin','top','bottom'] = 'origin'
    support_part: str | None = None
    surface_contact: SurfaceContact | None = None
    grounded: bool = False
    access_required: bool = True
    support_footprint: tuple[float | str,float | str] | None = None
    collision_role: Literal['solid','shell','floor_covering'] = 'solid'
    acquisition: Acquisition
    requirement_tags: list[str] = Field(default_factory=list)


class ContentRequirement(StrictModel):
    tag: str
    description: str
    minimum_instances: int = Field(ge=1,le=160)
    maximum_instances: int | None = Field(default=None,ge=1,le=160)


class RequestContract(StrictModel):
    asset_boundary: Literal['single_asset','scene']
    quality_target: Literal['prototype','realistic']
    contents: list[ContentRequirement] = Field(default_factory=list)
    style_requirements: list[StyleRequirement] = Field(default_factory=list,max_length=8)
    scene_use: SceneUseRequirements | None = None


def validate_request_contract(outline,contract):
    diagnostics=Diagnostics('request_contract_coverage')
    if outline.quality_target!=contract.quality_target:
        diagnostics.add('quality_target','Cannot change requested quality target',paths=['/quality_target'])
    if contract.scene_use and contract.scene_use.extent is not None:
        if any(abs(a-b) > .001 for a,b in zip(outline.extent,contract.scene_use.extent)):
            diagnostics.add('extent','Scene extent must preserve frozen user dimensions',paths=['/extent'],
                            measured=list(outline.extent),expected=list(contract.scene_use.extent))
    if contract.scene_use and contract.scene_use.enclosure=='enclosed':
        shells=[o for o in outline.objects if o.collision_role=='shell' and o.parent is None]
        if not shells:
            diagnostics.add('missing_shell','The requested enclosed room needs an explicit architectural shell root with floor/wall/opening geometry; furniture and numeric extent alone are incomplete',paths=['/objects'])
        if len(shells)==1:
            shell=shells[0]
            for label,parameters in [('baseline',outline.parameters)]+[(check.parameter,
                    {**outline.parameters,check.parameter:outline.parameters[check.parameter]+check.delta})
                    for check in outline.edit_checks if check.parameter in outline.parameters]:
                size=[scalar(v,parameters) for v in shell.dimensions]
                if any(abs(a-b)>.002 for a,b in zip(size[:2],outline.extent[:2])):
                    diagnostics.add('shell_boundary','A single enclosing shell must span the scene XY boundary '
                        'where spatial openings will be placed. Do not enlarge the numeric extent beyond '
                        'the actual walls to make an edit fit. The current schema has a frozen room extent: '
                        'omit unrequested room-resize demonstration edits; preserve requested furniture edits.',
                        paths=['/extent','/objects','/edit_checks'],component_ids=[shell.id],scenario=label,
                        measured=size[:2],expected=list(outline.extent[:2]))
    if contract.asset_boundary=='single_asset':
        roots=[o for o in outline.objects if o.parent is None]
        if len(roots)!=1 or roots[0].acquisition.strategy!='parametric':
            diagnostics.add('editable_root','A single editable asset needs one parametric root; separately sourced rigid subassets must be its descendants, never a generated replacement for the entire asset.',paths=['/objects'])
        root=roots[0] if len(roots)==1 else None
        parametric_siblings=[o.id for o in outline.objects if root and o.id!=root.id and o.acquisition.strategy=='parametric']
        if parametric_siblings:
            diagnostics.add('parametric_siblings','Requested ONE editable asset: every aDSL-constructible structure and content must be an internal_part of the single parametric root. '
                'Separate scene objects are allowed only for rigid externally sourced subassets (retrieve/auto/generate) parented beneath that root. '
                'These parametric sibling objects are invalid: '+', '.join(parametric_siblings)+'. Move their intent and stable IDs into root.internal_parts, preserve supports and requirement_tags, and keep only genuine external geometry as child objects.',paths=['/objects'])
    try:
        validate_style_bindings(contract.style_requirements,outline.style_bindings,outline.objects,
                                source_mode=lambda o:o.acquisition.strategy)
    except ValueError as error:
        report=failure_report(error,'style_bindings')
        diagnostics.issues.extend(report['issues'])
        diagnostics.blocked_checks.extend(report.get('blocked_checks',[]))
    for requirement in contract.contents:
        matches=[o.id for o in outline.objects if requirement.tag in o.requirement_tags]+[
            o.id+'/'+p.id for o in outline.objects for p in o.internal_parts if requirement.tag in p.requirement_tags]
        count=len(matches)
        if count<requirement.minimum_instances or (requirement.maximum_instances is not None and count>requirement.maximum_instances):
            diagnostics.add('content_count',f'Requirement {requirement.tag}: {count} individually identified instances; expected {requirement.minimum_instances}..{requirement.maximum_instances}. Matched IDs: {matches}. Correct misclassified tags or inventory against the ORIGINAL requirement: {requirement.description}. Never count support hardware or a group as the contents themselves.',
                            paths=['/objects'],measured=count,expected=[requirement.minimum_instances,requirement.maximum_instances])
    diagnostics.raise_if_any()
    return outline


def validate_replan_style_feedback(outline, feedback):
    """Reject repeating a parametric style representation already failed by Judge."""
    if not isinstance(feedback, dict):
        return outline
    visual = feedback.get('visual') or {}
    if not isinstance(visual, dict):
        return outline
    decision = visual.get('decision') if isinstance(visual.get('decision'), dict) else visual
    failed = {a.get('requirement_id') for a in decision.get('style_assessments', [])
              if isinstance(a, dict) and a.get('status') in {'missing', 'unclear'}}
    for finding in decision.get('findings', []):
        if isinstance(finding, dict) and finding.get('severity') == 'blocking':
            failed.update(finding.get('requirement_ids', []))
    if not failed:
        return outline
    previous = feedback.get('previous_decomposition') or {}
    previous_objects = {item.get('id'): item for item in previous.get('objects', [])
                        if isinstance(item, dict)}
    current_objects = {item.id: item for item in outline.objects}
    current_bindings = {item.requirement_id: item for item in outline.style_bindings}
    for old in previous.get('style_bindings', []):
        if not isinstance(old, dict) or old.get('requirement_id') not in failed or not old.get('part_ids'):
            continue
        owners = {part.split('/', 1)[0] for part in old.get('part_ids', []) if '/' in part}
        if not any((previous_objects.get(owner, {}).get('acquisition') or {}).get('strategy') == 'parametric'
                   for owner in owners):
            continue
        binding = current_bindings.get(old['requirement_id'])
        if binding is None:
            continue
        if not any(current_objects[cid].acquisition.strategy in {'retrieve', 'auto', 'generate'}
                   for cid in binding.component_ids):
            raise ValueError(
                f"Visual Judge rejected the parametric sweep implementation for style requirement "
                f"{old['requirement_id']}; the replan must bind a real rigid retrieve/auto/generate child "
                "instead of repeating the same constant-radius sweep. Preserve the editable parametric root.")
    return outline


def restore_validated_parametric_specs(outline,feedback):
    """Freeze validated editable roots during an external-source-only replan."""
    if not isinstance(feedback,dict):return outline
    preserved=feedback.get('validated_parametric_specs') or []
    if not preserved:return outline
    data=outline.model_dump(mode='json')
    positions={item.get('id'):index for index,item in enumerate(data.get('objects',[]))
               if isinstance(item,dict)}
    missing=[item.get('id') for item in preserved
             if isinstance(item,dict) and item.get('id') not in positions]
    if missing:
        raise ValueError('External-source replan removed validated parametric component(s): '
                         +', '.join(str(item) for item in missing)
                         +'. Restore their exact IDs/specifications and revise only external descendants.')
    for item in preserved:
        if not isinstance(item,dict) or item.get('acquisition',{}).get('strategy')!='parametric':
            raise ValueError('Validated parametric replan context is malformed; cannot restore an editable root.')
        data['objects'][positions[item['id']]]=copy.deepcopy(item)
    if feedback.get('validated_parameters'):
        data['parameters']=copy.deepcopy(feedback['validated_parameters'])
    if feedback.get('validated_edit_checks'):
        data['edit_checks']=copy.deepcopy(feedback['validated_edit_checks'])
    return Decomposition.model_validate(data)


class FunctionalZone(StrictModel):
    name: str
    purpose: str
    component_ids: list[str] = Field(min_length=1)


class FunctionalBinding(StrictModel):
    id: str
    parent: str | None
    mount: Literal['origin','top','bottom']
    support_part: str | None
    surface_contact: SurfaceContact | None = None
    grounded: bool
    access_required: bool
    access_reason: str = Field(min_length=12)
    attachment_reason: str = Field(min_length=12)
    support_footprint: tuple[float,float] | None


class FunctionalBindings(StrictModel):
    bindings: list[FunctionalBinding] = Field(min_length=1)


def apply_functional_binding_result(outline,bindings):
    """Convert the validated binding-stage result back into the frozen outline."""
    if not isinstance(bindings,FunctionalBindings):
        raise TypeError(f'Functional binding stage returned {type(bindings).__name__}, expected FunctionalBindings.')
    return apply_functional_bindings(outline,bindings)


def _functional_binding_id_error(expected_ids, actual_ids):
    if (len(actual_ids)==len(expected_ids) and len(set(actual_ids))==len(actual_ids)
            and set(actual_ids)==set(expected_ids)):
        return None
    duplicates=list(dict.fromkeys(item for item in actual_ids if actual_ids.count(item)>1))
    expected_set=set(expected_ids)
    missing=[item for item in expected_ids if item not in actual_ids]
    unexpected=list(dict.fromkeys(item for item in actual_ids if item not in expected_set))
    first_order_mismatch=next((index for index,(expected,actual) in
        enumerate(zip(expected_ids,actual_ids)) if expected!=actual),None)
    return ('Functional binding IDs must equal the frozen top-level component IDs exactly once. '
        f'Expected {len(expected_ids)} IDs {json.dumps(expected_ids,ensure_ascii=True)}; '
        f'received {len(actual_ids)}. missing={json.dumps(missing,ensure_ascii=True)}; '
        f'unexpected={json.dumps(unexpected,ensure_ascii=True)}; '
        f'duplicates={json.dumps(duplicates,ensure_ascii=True)}; '
        f'first_order_mismatch={first_order_mismatch}. Only top-level components receive bindings; '
        'internal_parts are recipe members and must not be emitted as separate bindings.')


def _functional_binding_scope(component_ids):
    return (f'Return exactly {len(component_ids)} bindings, with IDs exactly equal to the supplied '
        f'component_ids list {json.dumps(component_ids,ensure_ascii=True)}. '
        'Bind only those top-level components. internal_parts are recipe members inside a component, '
        'not independent scene components, and must never appear as binding IDs.\n')


def _functional_bindings_response_schema(component_ids):
    """Constrain the model response to the frozen binding inventory."""
    schema=copy.deepcopy(FunctionalBindings.model_json_schema())
    item=schema.get('$defs',{}).get('FunctionalBinding',{})
    item.get('properties',{}).get('id',{})['enum']=list(component_ids)
    bindings=schema.get('properties',{}).get('bindings',{})
    bindings['minItems']=len(component_ids)
    bindings['maxItems']=len(component_ids)
    return schema


def _functional_bindings_prompt(component_ids):
    return ('Plan functional access and physical attachment for EVERY frozen scene component. Return bindings only.\n'+
        _functional_binding_scope(component_ids)+'''Each ID is an immutable key copied character-for-character from the supplied inventory, not a semantic label to rename.
Do not replace a component with a synonym, combine components, or omit optional-looking items. The response schema
restricts both IDs and binding count; decide every component in this pass.
Inventory, dimensions, source and design are frozen; choose semantic parent/mount/access decisions before
positions. Review all objects in one response. access_required is an explicit decision, not a default.
true means a dedicated person-sized approach/operation area: primary seats, work/storage/use surfaces.
false applies to passive finishes, lamps, art, display objects, contents and items accessed through a
supporting furniture's activity area, when no separate approach is needed. Give a concrete reason for each.
Do not invent independent circulation corridors to each teacup, lamp, book or picture. Avoid overconstraining
shared-use surfaces such as side tables reached from a seat; explain how the user reaches them instead.
For a desk/table explicitly paired with a chair facing it, the seated occupant reaches the surface from that chair:
set the surface access_required=false and let the chair own the single floor approach/pullback area, unless
the user explicitly requests separate standing or accessibility use. Do not reserve a desk-front approach
through the chair. The chair remains access_required=true; a task chair's entry is behind its back or from a
clear side, never from the front facing into the work surface.
Plan real physical support: objects resting on furniture should be parented to that actual support with
mount=top, rather than floating free roots with a guessed world height. Before choosing top support, compare
the child's real contact footprint (support_footprint when present, otherwise its dimensions) with the usable
support surface and every other child assigned there. Simultaneous contents must physically fit without overlap
and preserve any requested exposed surface. An optional cushion beside bedding is usually a separate grounded
floor object unless the mat has enough real exposed area for it; adjacency alone is not support. Never freeze
a known impossible support group for layout or a later repair to discover. For shelf contents name the exact
support_part ID from the parametric bookcase internal_parts; use an actual horizontal shelf. External
furniture may support a child at its envelope top, with support_footprint for a small base if overhanging.
grounded=true only for actual floor-contacting root objects. Supported contents are not grounded.
Architectural shells are passive boundaries: access_required=false. Entrance/window approach belongs
to spatial openings, not a person-sized operating area for the whole floor or all walls.
For wall/ceiling fixtures use parent=<actual architectural support>,mount=origin,support_part=null,
grounded=false and surface_contact={parent_part:<frozen wall/ceiling part ID>,parent_face:<x/y/z_min/max>,
child_part:<actual parametric backplate ID or null for a sourced mesh>,child_face:<x/y/z_min/max>}.
These are LOCAL part/enclosure faces, not room zones. Select opposing faces and the proper child yaw
later; for a vertical wall, a whole-object child face must use the thinner local horizontal envelope
(X or Y) so the broad visible face remains toward the room. Do not attach a wide picture/sconce face
edge-on just because its normal can oppose the wall. Compiler resolves normal position from real geometry
and verifies the patch misses openings.
Name the backplate for editable fixtures; sourced assets use their actual extreme mesh face. A bare
origin parent and prose about a wall do NOT establish contact. Treat explicit placement semantics such
as "north-wall fireplace", "wall-mounted", "fixed to the wall", or "flush-mounted" as a real
wall attachment intent, including chimney breasts and built-in wall fixtures. Bind these to the actual
architectural wall part and its room-facing surface; grounding the object on floor_slab or choosing a
nearby XY coordinate does not satisfy that intent. For parametric objects, the functional front is local
-Y and its rear is +Y. When the thin/back axis is Y, child_face MUST be y_max: y_min is the functional
front and must never contact the wall. The compiler derives world yaw from measured host/child normals.
For desks, chairs, low cabinets and other floor-standing furniture, "along the east wall", "against the
south wall" or similar compass placement describes location only; keep the item as a grounded root unless
the request explicitly says it is fixed, attached, flush-mounted, or built into the wall. A compass-wall
phrase alone implies attachment only for an obvious wall fixture such as a fireplace or sconce.
Nonmounted items use surface_contact=null. The word "built-in" alone does not establish wall attachment.
A fitted window assembly MUST be parented to the actual architectural shell at mount=origin, with no grounding,
so its placement can be expressed in the shell frame and audited against its named opening. A centered parent
origin is not a fitted position. Do not leave a window assembly as an unattached root at the room origin.
A floor-supported tatami platform, low bed, bench or storage base remains grounded even if it is described
as built-in or placed against a wall; use wall surface_contact only when it is explicitly fixed to or built
into the wall. Window frame/sash/glazing assemblies fit an opening and are audited by that opening, not by
the generic wall-contact rule. They must use the architectural shell as parent without a single-face
surface_contact; do not force a window sill or reveal into a wall-mount contact. A separate entry or
entrance door leaf is also a child fitted to the shell's entrance opening; keep it parented directly to the
shell at origin with no floor support, and let the spatial opening contract validate its swing_leaf association,
jamb-side open pose and clear passage. The fixed door frame is also a direct shell child; the door handle is a
child of the door leaf. Do not parent the leaf to the frame, and do not emit a scene-level attached_to relation
between these shell-sibling aperture fixtures. Do not force its bottom edge into a wall surface_contact. Both
aperture types may use the architectural shell as parent without a single-face surface_contact.
A fitted window's mounting member is the window sill or the reveal — a horizontal part whose thickness
normal axis (form.axis) is Z (horizontal slab). The validator rejects Z-normal child parts against a
vertical wall face: that is correct behaviour. Do NOT declare a Z-normal sill as the child_face against
a vertical wall; the sill is a structural anchor inside the window assembly, not its wall contact.
Instead, window assemblies have no surface_contact at all and rely on the spatial opening geometry for
positioning; the fitting face is derived from the opening, not from an explicit child_face against a wall.
If you need to express a window's contact with its wall opening, keep surface_contact=null; the spatial
solver places the assembly from the opening data.
Ceiling-light vocabulary describes appearance, not the mount enum: a hanging pendant still uses
parent=<ceiling support>,mount=origin,surface_contact=<canopy-to-ceiling faces>,grounded=false,support_part=null.
The bottom value means a child is supported beneath a parent's bottom face; it is not a synonym for a hanging light.
Preserve a floor slab at occupied world Z=0.
Door and window assemblies are children of the architectural shell. The shell's world Z origin may be
below the interior floor (e.g. a slab below occupiable space sets the shell origin at z=-0.05m). A door
or window whose local Z is 0 in the shell frame will therefore appear 0.05m below the interior floor.
When the shell has a known floor offset (visible in its interface_notes or slab geometry), offset the
door/window local Z by the shell slab thickness so its bottom aligns with the occupied floor level.
Concretely: if the shell's slab bottom is at world z=-0.05m and the interior floor is at world z=0, any
door leaf, frame, or window assembly that should sit on the interior floor must have local z=+0.05m in
the shell frame, not z=0. The compiler cannot guess this offset; the functional binding must supply it.
All parent graphs must be acyclic and physically meaningful. Do not attach a whole rug as the parent of
seating, nor a painting as a support for the wall. Keep floor coverings as low non-access objects; they
remain real geometry and cannot block the floor route merely because they have a large flat footprint.
For every binding supply parent (null allowed),mount,support_part,grounded,access_required,
access_reason,attachment_reason,support_footprint (null when not needed). Do not output placements.''')


_EXPLICIT_WALL_ATTACHMENT = re.compile(
    r'\bwall[- ]mounted\b|\bflush[- ]mounted\b|\bin[- ]wall\b|'
    r'\bbuilt[- ]in\s+(?:fireplace|chimney|bookcase|bookshelf|cabinet|wardrobe|shelf|alcove|niche)\b|'
    r'\bmounted (?:on|to|against) (?:the )?wall\b|'
    r'\b(?:attached|fixed|fastened|secured) (?:to|against) (?:the )?wall\b|'
    r'\b(?:north|south|east|west)[ -]wall(?:\s+\w+){0,2}\s+'
    r'(?:fireplace|chimney|mantel|sconce|wall[- ]lamp|wall[- ]light)\b', re.IGNORECASE)


_WINDOW_APERTURE_FIXTURE = re.compile(
    r'\b(?:north|south|east|west)[_ -]window\b|'
    r'\bwindow[_ -](?:assembly|unit|frame|sash|glazing|fixture|shutter)\b|'
    r'\bfitted(?:\s+\w+){0,4}\s+window\b',re.IGNORECASE)


def _fitted_window_component_ids(outline):
    """Identify aperture hardware that the spatial opening audit validates as a group."""
    result=set()
    for obj in outline.objects:
        if getattr(obj,'collision_role',None)=='shell':continue
        identity=f'{obj.id} {obj.semantic}'.replace('_',' ').replace('-',' ')
        primary_identity=f'{obj.id} {re.split(r"\bwith\b",obj.semantic,maxsplit=1,flags=re.I)[0]}'.replace('_',' ').replace('-',' ')
        # Furniture near a window does not acquire an aperture exemption.
        if re.search(r'\b(?:seat|bench|shelf|shelves|desk|table|cabinet|curtain)\b',primary_identity,re.I):continue
        architectural_window=(getattr(obj,'category','').casefold() in {'architecture','architectural','window'}
                              and re.search(r'\bwindow\b|窗',identity,re.I))
        if architectural_window or _WINDOW_APERTURE_FIXTURE.search(identity):result.add(obj.id)
    return result


_ENTRANCE_DOOR_LEAF = re.compile(
    r'\b(?:entry|entrance)[_ -]door\b|\bdoor[_ -](?:leaf|panel)\b',re.IGNORECASE)


def _entrance_door_leaf_ids(outline):
    """Identify shell-mounted door leaves validated against an entrance opening."""
    objects={obj.id:obj for obj in outline.objects}
    return {obj.id for obj in outline.objects
            if obj.parent in objects and objects[obj.parent].collision_role=='shell'
            and _ENTRANCE_DOOR_LEAF.search(f'{obj.id} {obj.semantic}')}


def _unattached_shell_children(outline):
    """Find shell children that lack a real support or an audited aperture role."""
    objects={obj.id:obj for obj in outline.objects}
    aperture_ids=_fitted_window_component_ids(outline)|_entrance_door_leaf_ids(outline)
    return [obj.id for obj in outline.objects if obj.parent in objects and
        objects[obj.parent].collision_role=='shell' and obj.mount=='origin' and not obj.surface_contact
        and obj.id not in aperture_ids]


def validate_wall_attachment_intents(outline, *, fitted_component_ids=(), room_facing_wall_faces=None):
    """Require wall fixtures to use measured, room-facing shell surfaces.

    Window fixtures and entrance door leaves fitted to shell apertures are
    checked by their opening geometry audit instead of a single-face wall
    contact contract.
    """
    objects={obj.id:obj for obj in outline.objects}
    fitted_component_ids=set(fitted_component_ids)
    room_facing_wall_faces=room_facing_wall_faces or {}
    diagnostics=Diagnostics('wall_attachment_intents')
    for index,obj in enumerate(outline.objects):
        if obj.id in fitted_component_ids or getattr(obj,'collision_role',None)=='shell':
            continue
        if not _EXPLICIT_WALL_ATTACHMENT.search(f'{obj.id} {obj.semantic}'):
            continue
        path=f'/objects/{index}'
        parent=objects.get(obj.parent)
        wall_parts={part.id for part in getattr(parent,'internal_parts',[])
                    if re.search(r'\bwall\b|\bpartition\b',f'{part.id} {part.semantic}',re.IGNORECASE)} if parent else set()
        contact=obj.surface_contact
        if parent is None or parent.collision_role!='shell':
            diagnostics.add('wall_attachment_parent',
                f'{obj.id}: explicit wall-placement intent requires the actual architectural shell as parent.',
                paths=[path+'/parent',path+'/surface_contact'],
                component_ids=[obj.id,parent.id] if parent else [obj.id])
        elif contact is None or contact.parent_part not in wall_parts:
            diagnostics.add('wall_attachment_contact',
                f'{obj.id}: explicit wall-placement intent must use surface_contact to a real wall/partition '
                f'part of {parent.id}; floor support and a nearby position do not attach it. '
                f'Available wall parts={sorted(wall_parts)}.',
                paths=[path+'/surface_contact'],component_ids=[obj.id,parent.id],
                expected={'parent_parts':sorted(wall_parts),'parent_face_axis':'x or y'})
        elif contact.parent_face.startswith('z_'):
            diagnostics.add('wall_attachment_face',
                f'{obj.id}: a wall-mounted object must contact a vertical wall face, not {contact.parent_face}.',
                paths=[path+'/surface_contact'],component_ids=[obj.id,parent.id])
        elif contact is not None and parent is not None:
            wall_context=room_facing_wall_faces.get(parent.id,{})
            measured=wall_context.get(contact.parent_part)
            if wall_context and measured is None:
                diagnostics.add('wall_attachment_geometry',
                    f'{obj.id}: no measured room-facing geometry is available for {parent.id}/{contact.parent_part}; '
                    'select a wall part with verified Blender bounds.',
                    paths=[path+'/surface_contact/parent_part'],component_ids=[obj.id,parent.id],
                    expected={'measured_wall_parts':sorted(wall_context)})
            elif measured and contact.parent_face not in measured.get('interior_faces',
                    [measured.get('interior_face')]):
                diagnostics.add('wall_attachment_exterior_face',
                    f'{obj.id}: measured bounds for {parent.id}/{contact.parent_part} put the room-facing '
                    f'surface(s) at {measured.get("interior_faces", [measured.get("interior_face")])}, '
                    f'but the candidate uses {contact.parent_face}. '
                    f'Part bounds={measured["bounds_m"]}; use measured geometry rather than the wall name.',
                    paths=[path+'/surface_contact/parent_face'],component_ids=[obj.id,parent.id],
                    measured=measured,expected={'parent_faces':measured.get('interior_faces',
                        [measured.get('interior_face')])})
            try:
                validate_wall_functional_face(contact)
            except ValueError as error:
                diagnostics.add('wall_functional_face',f'{obj.id}: {error}',
                    paths=[path+'/surface_contact/child_face'],component_ids=[obj.id,parent.id],
                    expected={'child_face':'the component rear, opposite local front -Y'})
    diagnostics.raise_if_any()
    return outline


def apply_functional_bindings(outline, bindings):
    """Apply bindings by stable ID and report every independent contract failure."""
    expected=[obj.id for obj in outline.objects]
    actual=[item.id for item in bindings.bindings]
    diagnostics=Diagnostics('functional_bindings')
    id_error=_functional_binding_id_error(expected,actual)
    if id_error:
        diagnostics.add('functional_binding_ids',id_error,paths=['/bindings'])

    binding_indices={}
    binding_by_id={}
    for index,item in enumerate(bindings.bindings):
        binding_indices.setdefault(item.id,index)
        binding_by_id.setdefault(item.id,item)
    revised=outline.model_copy(deep=True)
    for obj in revised.objects:
        binding=binding_by_id.get(obj.id)
        if binding is None:
            continue
        index=binding_indices[obj.id]
        if obj.collision_role=='shell' and binding.access_required:
            diagnostics.add('shell_access',
                'Architectural shells have no dedicated operator area; opening approach belongs to the opening contract.',
                paths=[f'/bindings/{index}/access_required'],component_ids=[obj.id])
        for key in ('parent','mount','support_part','surface_contact','grounded','access_required','support_footprint'):
            setattr(obj,key,getattr(binding,key))
        obj.interface_notes=[*obj.interface_notes,
            'Human access: '+binding.access_reason,'Physical attachment: '+binding.attachment_reason]

    fitted_window_ids=_fitted_window_component_ids(revised)
    fitted_aperture_ids=fitted_window_ids|_entrance_door_leaf_ids(revised)
    objects={obj.id:obj for obj in revised.objects}
    for obj in revised.objects:
        if obj.id not in fitted_aperture_ids:continue
        parent=objects.get(obj.parent)
        if (parent is None or parent.collision_role!='shell' or obj.mount!='origin'
                or obj.surface_contact is not None or obj.support_part is not None or obj.grounded):
            index=binding_indices.get(obj.id)
            root=f'/bindings/{index}' if index is not None else '/bindings'
            diagnostics.add('aperture_attachment_contract',
                f'{obj.id}: aperture hardware is fitted to its named opening, not to an unbroken wall face. '
                'Use the architectural shell as parent, mount=origin, grounded=false, support_part=null, '
                'surface_contact=null; opening geometry must still validate its position and clear passage.',
                paths=[root+'/'+field for field in ('parent','mount','grounded','support_part','surface_contact')],
                component_ids=[obj.id],measured={field:getattr(obj,field).model_dump() if field=='surface_contact'
                    and obj.surface_contact else getattr(obj,field)
                    for field in ('parent','mount','grounded','support_part','surface_contact')},
                expected={'parent_candidates':[o.id for o in revised.objects if o.collision_role=='shell'],
                    'mount':'origin','grounded':False,'support_part':None,'surface_contact':None,
                    'spatial_binding':'matching opening fitted_component_ids or entrance swing_leaf_ids'})
    for component_id in _unattached_shell_children(revised):
        index=binding_indices.get(component_id)
        path=f'/bindings/{index}/surface_contact' if index is not None else '/bindings'
        diagnostics.add('shell_child_attachment',
            f'{component_id}: a shell-child origin frame does not establish physical attachment. Use a real '
            'surface_contact or an actual top/bottom support; a fitted aperture fixture is validated against its opening.',
            paths=[path],component_ids=[component_id])

    def mapped_paths(issue):
        paths=[]
        for path in issue.get('paths',[]):
            match=re.match(r'^/objects/(\d+)(?=/|$)',path)
            if match:
                object_index=int(match.group(1))
                if object_index<len(revised.objects):
                    component_id=revised.objects[object_index].id
                    binding_index=binding_indices.get(component_id)
                    suffix=path[match.end():]
                    paths.append(f'/bindings/{binding_index}{suffix}' if binding_index is not None else '/bindings')
                    continue
            paths.append('/bindings' if path=='/objects' else path)
        return list(dict.fromkeys(paths or ['/bindings']))

    checks=(
        ('decomposition',lambda:revised.validate_decomposition(None)),
        ('wall_attachment',lambda:validate_wall_attachment_intents(
            revised,fitted_component_ids=fitted_aperture_ids)),
    )
    for name,check in checks:
        try:
            check()
        except (ValueError,KeyError,TypeError,IndexError,ZeroDivisionError) as failure:
            report=failure_report(failure,name)
            for issue in report.get('issues',[]):
                diagnostics.add(f'{name}.{issue["code"]}',
                    f'[{name}] {issue["evidence"]}',paths=mapped_paths(issue),
                    component_ids=issue.get('component_ids',()),scenario=issue.get('scenario','baseline'),
                    measured=issue.get('measured'),expected=issue.get('expected'))
            for blocked in report.get('blocked_checks',[]):
                diagnostics.block(blocked.get('check',name),f'[{name}] {blocked.get("reason","")}',
                    paths=mapped_paths(blocked),scenario=blocked.get('scenario','baseline'))
    diagnostics.raise_if_any()
    return revised


class AccessFaces(StrictModel):
    allowed_sides: dict[str,list[Literal['front','back','left','right']]]
    reasons: dict[str,str]


ACCESS_FACES_PROMPT = (
    'Decide physically usable access sides BEFORE room coordinates. Return allowed_sides and reasons. '
    'Their keys are a closed set exactly equal to expected_access_owner_ids in frozen_context; '
    'never add a table/surface marked access_required=false or omit any listed owner. '
    'front is always the object\'s local -Y, back=+Y,left=-X,right=+X. '
    'Use identity and actual use surfaces, not a guessed wall or world compass direction. '
    'A front-open storage unit is browsed from its open front, not its closed side/back. '
    'For a chair paired by a facing relation with a desk/table, its front faces the work surface and '
    'is NOT an approach side: a person enters/pulls the chair from local back (+Y) or a genuinely clear '
    'side. Never route through the chair front into its paired surface. The chair owns that station\'s '
    'single floor access area. For lounge seating, distinguish entering the seating position from '
    'reaching over a back; its front approach may be offset toward an open end. Tables may admit several sides. '
    'Reason about companion objects and the whole activity group from scene_design. A usable '
    'entry side depends on armrests, supports and nearby working surfaces; the visible front '
    'and the side a person uses to enter are distinct decisions. Do not infer a closed side '
    'frame unless actual required_features establishes one. '
    'List only physically usable sides for the stated activity; do not list all four to avoid a decision. '
    'Do not output placements, room directions, clearances, dimensions or meshes.'
)


def project_access_face_ids(value,expected_ids):
    """Drop invented keys while preserving the frozen set of human-use roots."""
    expected=set(expected_ids)
    return value.model_copy(update={
        'allowed_sides':{key:sides for key,sides in value.allowed_sides.items() if key in expected},
        'reasons':{key:reason for key,reason in value.reasons.items() if key in expected},
    })


def validate_access_faces(value,expected_ids,seated_chair_ids=()):
    expected=set(expected_ids)
    diagnostics=Diagnostics('access_faces')
    side_ids=set(value.allowed_sides);reason_ids=set(value.reasons)
    for id in sorted(side_ids-expected):
        diagnostics.add('extra_access_side',f'{id}: access_required is false or this is not a human-use root; remove its access-side entry.',
            paths=['/allowed_sides/'+id.replace('~','~0').replace('/','~1')],component_ids=[id])
    for id in sorted(reason_ids-expected):
        diagnostics.add('extra_access_reason',f'{id}: access_required is false or this is not a human-use root; remove its access reason.',
            paths=['/reasons/'+id.replace('~','~0').replace('/','~1')],component_ids=[id])
    missing_sides=expected-side_ids
    if missing_sides:
        diagnostics.add('missing_access_side','Add physically usable local access sides for exactly these roots: '+str(sorted(missing_sides)),
            paths=['/allowed_sides'],component_ids=sorted(missing_sides),expected=sorted(expected))
    missing_reasons=expected-reason_ids
    if missing_reasons:
        diagnostics.add('missing_access_reason','Add a physical-use reason for exactly these roots: '+str(sorted(missing_reasons)),
            paths=['/reasons'],component_ids=sorted(missing_reasons),expected=sorted(expected))
    for id,sides in value.allowed_sides.items():
        if id in expected and not sides:
            diagnostics.add('empty_access_sides',f'{id}: provide at least one physically usable local side.',
                paths=['/allowed_sides/'+id.replace('~','~0').replace('/','~1')],component_ids=[id])
    diagnostics.raise_if_any()
    return validate_seated_chair_faces(value,seated_chair_ids)


class ApproachDesign(StrictModel):
    owner_id: str
    side: Literal['front','back','left','right']
    along_face: float | str = 0
    size: tuple[float,float]
    final_route_width: float = Field(gt=0)
    reason: str = Field(min_length=20)


class CirculationProgram(StrictModel):
    approaches: list[ApproachDesign]
    shared_aisle_owner_id: str | None = None
    shared_aisle_width: float | None = Field(default=None,gt=0)


def validate_approach_owners(value,expected_ids):
    ids=[approach.owner_id for approach in value.approaches]
    if set(ids)!=set(expected_ids) or len(ids)!=len(set(ids)):
        missing=[id for id in expected_ids if id not in ids]
        unexpected=[id for id in ids if id not in expected_ids]
        duplicates=sorted({id for id in ids if ids.count(id)>1})
        raise ValueError(
            'Approaches must cover each functional access owner exactly once. '
            f'Expected IDs: {list(expected_ids)}; received IDs: {ids}; missing={missing}; '
            f'unexpected={unexpected}; duplicates={duplicates}. Preserve the frozen owners and fix only this mapping.')
    if value.shared_aisle_owner_id is not None and value.shared_aisle_owner_id not in set(expected_ids):
        raise ValueError('shared_aisle_owner_id must be one of the approved functional access owners.')
    return value


class HumanUseRevision(StrictModel):
    functional_faces: AccessFaces
    circulation: CirculationProgram
    reason: str = Field(min_length=20)


def seated_surface_pairs(design, outline):
    """Find seated work surfaces whose floor approach is owned by their paired chair."""
    if design is None or design.composition is None:
        return []
    choices={choice.id:' '.join((choice.id,choice.activity,choice.selection)).casefold()
             for choice in design.choices}
    seats=('chair','stool','seat','armchair','椅','座椅','凳')
    surfaces=('desk','table','work surface','worktop','writing surface','counter','书桌','课桌','餐桌','桌面')
    pairs=[]
    for group in design.composition.groups:
        for relation in group.relationships:
            if relation.kind!='facing':
                continue
            seat_identity=choices.get(relation.a_choice_id,'')
            surface_identity=choices.get(relation.b_choice_id,'')
            if not any(term in seat_identity for term in seats) or not any(term in surface_identity for term in surfaces):
                continue
            seat_ids=outline.design_bindings.get(relation.a_choice_id,[])
            surface_ids=outline.design_bindings.get(relation.b_choice_id,[])
            pairs.extend((seat_id,surface_id) for seat_id in seat_ids for surface_id in surface_ids
                         if seat_id!=surface_id)
    return pairs


def validate_seated_surface_access(outline, bindings, design, brief=''):
    """Prevent duplicate floor approaches through a chair that occupies the desk front."""
    pairs=seated_surface_pairs(design,outline)
    if not pairs:
        return bindings
    by_id={item.id:item for item in bindings.bindings}
    indices={item.id:index for index,item in enumerate(bindings.bindings)}
    explicit_separate=any(term in str(brief).casefold() for term in
        ('standing desk','standing work','standing access','wheelchair','accessible desk',
         '站立书桌','站立使用','无障碍','轮椅'))
    diagnostics=Diagnostics('seated_surface_access')
    roots={obj.id for obj in outline.objects if obj.parent is None and obj.collision_role!='shell'}
    for seat_id,surface_id in pairs:
        if seat_id not in roots or surface_id not in roots:
            continue
        seat=by_id.get(seat_id);surface=by_id.get(surface_id)
        if seat is not None and not seat.access_required:
            diagnostics.add('seated_chair_access',
                f'{seat_id}: the chair facing its work surface owns the single human floor approach and must remain access_required=true.',
                paths=[f'/bindings/{indices[seat_id]}/access_required'],component_ids=[seat_id,surface_id])
        if surface is not None and surface.access_required and not explicit_separate:
            diagnostics.add('duplicate_seated_surface_access',
                f'{surface_id}: its paired chair {seat_id} occupies the seated-use approach. Set this surface '
                'access_required=false so the chair owns the one approach/pullback area; the surface is reached '
                'from the seat. Preserve a separate area only when the original brief requests standing or accessibility use.',
                paths=[f'/bindings/{indices[surface_id]}/access_required'],component_ids=[surface_id,seat_id])
    diagnostics.raise_if_any()
    return bindings


def validate_seated_chair_faces(faces, owners):
    for owner_id in owners:
        if owner_id in faces.allowed_sides and 'front' in faces.allowed_sides[owner_id]:
            raise ValueError(f'{owner_id}: front faces its paired desk/table and cannot be a chair-entry side; use local back or a genuinely clear side.')
    return faces


def validate_human_use_revision(value, previous_faces, previous_program, contract, forbid_front_owners=()):
    """Review inferred access directions without shrinking people's clearances."""
    expected=set(previous_faces.allowed_sides)
    faces=value.functional_faces
    if set(faces.allowed_sides)!=expected or set(faces.reasons)!=expected or any(not v for v in faces.allowed_sides.values()):
        raise ValueError('Preserve all functional access owners with actual usable sides and reasons.')
    for owner_id in forbid_front_owners:
        if owner_id in faces.allowed_sides and 'front' in faces.allowed_sides[owner_id]:
            raise ValueError(owner_id+': its front faces the paired work surface and cannot be an entry/pullback side.')
    old={a.owner_id:a for a in previous_program.approaches}
    ids=[a.owner_id for a in value.circulation.approaches]
    if len(ids)!=len(set(ids)) or set(ids)!=set(old):
        raise ValueError('Preserve exactly the approved human approach owners.')
    for approach in value.circulation.approaches:
        if approach.size!=old[approach.owner_id].size or approach.final_route_width!=old[approach.owner_id].final_route_width:
            raise ValueError('Do not shrink or change approved human approach sizes or route widths.')
        if approach.side not in faces.allowed_sides[approach.owner_id]:
            raise ValueError(approach.owner_id+': choose an actual reviewed access side.')
        axis=1 if approach.side in {'front','back'} else 0
        if approach.size[axis]+.025<approach.final_route_width/2+.005:
            raise ValueError(approach.owner_id+': the full-width route must fit beside its owner.')
        minimum=contract.scene_use.minimum_route_width_m if contract.scene_use else None
        if minimum is not None and approach.final_route_width<minimum:
            raise ValueError('Preserve the original user route width.')
    return value


def human_access_failure(evidence, spatial):
    area_ids={a.id for a in spatial.access_areas}
    solver=evidence.get('numerical_solver',{})
    groups=solver.get('independent_conflict_groups',[])
    for group in groups:
        labels=[label.get('constraint','') if isinstance(label,dict) else label for label in group]
        access_labels=[label for label in labels if any(
            phrase+id in label for id in area_ids
            for phrase in ('clear approach ','usable approach boundary: '))]
        if access_labels and len(access_labels)==len(labels):return True
    return False


class RequiredClearance(StrictModel):
    a: str
    b: str
    minimum_m: float = Field(ge=0,le=100)
    reason: str


class RequirementCoverage(StrictModel):
    requirement: str = Field(min_length=1)
    component_ids: list[str] = Field(min_length=1)
    verification: str = Field(min_length=1)


class EditCheck(StrictModel):
    parameter: str
    delta: float
    intent: str


class Decomposition(StrictModel):
    scene_id: str = Field(pattern=r'^[A-Za-z][A-Za-z0-9_-]{0,63}$')
    description: str
    style: str
    quality_target: Literal['prototype','realistic']
    extent: tuple[float,float,float]
    parameters: dict[str,float] = Field(default_factory=dict)
    objects: list[ObjectIntent] = Field(min_length=1,max_length=80)
    zones: list[FunctionalZone] = Field(min_length=1)
    requirements: list[RequirementCoverage] = Field(min_length=1)
    clearances: list[RequiredClearance] = Field(default_factory=list)
    edit_checks: list[EditCheck] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    style_bindings: list[StyleBinding] = Field(default_factory=list,max_length=8)
    design_bindings: dict[str,list[str]] = Field(default_factory=dict)

    @model_validator(mode='after')
    def validate_decomposition(self,info):
        if info is not None and (info.context or {}).get('defer_decomposition_semantics'):
            return self
        diagnostics=Diagnostics('decomposition')
        def fail(code,message,*paths,scenario='baseline'):
            diagnostics.add(code,message,paths=paths,scenario=scenario)
        ids=[o.id for o in self.objects];known=set(ids)
        unique_ids=len(ids)==len(known)
        if not unique_ids:fail('component_ids','Duplicate component IDs','/objects')
        if any(not 0 < v <= 1000 for v in self.extent):fail('extent','Invalid metre extent','/extent')
        if any(not k.isidentifier() or k in {'w','d','h'} for k in self.parameters):
            fail('parameters','Global parameters must be identifiers, excluding local w,d,h','/parameters')
        valid_edits=[]
        for i,check in enumerate(self.edit_checks):
            if check.parameter not in self.parameters or abs(check.delta)<.001:
                fail('edit_parameter','Edit checks need a real parameter and meaningful nonzero delta',f'/edit_checks/{i}')
            else:valid_edits.append(check)
        for group,items in [('zones',self.zones),('requirements',self.requirements)]:
            for i,item in enumerate(items):
                if not set(item.component_ids)<=known:
                    fail('coverage_ids',f'Zone/requirement references unknown IDs {sorted(set(item.component_ids)-known)}. '
                        f'Valid SCENE OBJECT IDs are {sorted(known)}. Internal part IDs MUST NOT appear in zone/requirement component_ids; use their owning scene object ID.',f'/{group}/{i}/component_ids')
        covered={i for r in self.requirements for i in r.component_ids}
        if known-covered:fail('uncovered_objects','Every object must serve a stated requirement: '+str(known-covered),'/requirements')
        valid_clearances=[]
        for i,clearance in enumerate(self.clearances):
            if clearance.a not in known or clearance.b not in known or clearance.a==clearance.b:
                fail('clearance_ids','Clearance endpoints must be two real component IDs, never invented boundaries',f'/clearances/{i}')
            else:valid_clearances.append(clearance)
        sizes={}
        by_id={o.id:o for o in self.objects}
        graph_valid=unique_ids
        from general_scene import vec,scalar
        for i,obj in enumerate(self.objects):
            path=f'/objects/{i}'
            window_label=f'{obj.id} {obj.semantic}'.casefold()
            boundary_label=obj.semantic.casefold()
            window_feature=any(token in window_label for token in
                ('window','sash','glaz','mullion','shutter','blind'))
            describes_boundary=any(token in boundary_label for token in
                ('room shell','perimeter wall','partition','facade','façade','wall assembly'))
            try:
                world_size=[scalar(value,self.parameters) for value in obj.dimensions]
                spans_room=all(abs(world_size[axis]-self.extent[axis])<=.002 for axis in (0,1))
            except (ValueError,KeyError,TypeError,ZeroDivisionError,SyntaxError):
                spans_room=False
            if (obj.collision_role=='shell' and window_feature and not describes_boundary
                    and not spans_room):
                fail('window_shell_role',
                    f'{obj.id}: a standalone fitted window frame/sash/glazing/shutter/blind is a solid fixture, '
                    'not the room shell. Keep collision_role="solid" so the spatial plan can register this exact '
                    'object in its matching window opening fitted_component_ids; reserve shell for geometry that '
                    'forms the room boundary and proves its aperture.',
                    path+'/collision_role',scenario='baseline')
            if len({p.id for p in obj.internal_parts})!=len(obj.internal_parts):
                fail('internal_ids',f'{obj.id}: Duplicate internal part intent IDs',path+'/internal_parts')
            internal_ids={p.id for p in obj.internal_parts}
            if obj.collision_role=='shell':
                for j,clearance in enumerate(obj.internal_clearances):
                    if _is_shell_floor_ceiling_clearance(clearance,obj.internal_parts):
                        diagnostics.add('shell_clearance_as_assembly',
                            f'{obj.id}: floor-to-ceiling room height is encoded as an internal assembly clearance. '
                            'Represent room height through the shell envelope and the actual floor/ceiling geometry; '
                            'do not freeze an invented gap between those shell parts.',
                            paths=[path+f'/internal_clearances/{j}'],component_ids=[obj.id],
                            measured={'repair_target':'decomposition'})
            for j,part in enumerate(obj.internal_parts):
                targets=part.support_targets()
                bad=[t for t in targets if t not in internal_ids or t==part.id]
                if bad:
                    fail('internal_support',f'{obj.id}/{part.id}: Internal support must reference another internal part. '
                        f'Invalid targets {bad}',path+f'/internal_parts/{j}/support')
                if part.attached_to and (part.attached_to not in internal_ids or part.attached_to==part.id):
                    fail('internal_attachment',f'{obj.id}/{part.id}: Internal attachment must reference another internal part',path+f'/internal_parts/{j}/attached_to')
                if obj.collision_role=='shell' and targets:
                    target=next((item for item in obj.internal_parts if item.id in targets),None)
                    text=part.semantic.casefold()
                    if target and target.id.casefold() in {'floor_slab','floor','floor_root'} and any(
                            token in text for token in ('header','lintel','above opening','above window','above door','过梁','窗上','门上')):
                        fail('shell_header_support',
                            f'{obj.id}/{part.id}: an above-opening header does not rest on the floor. '
                            'Connect it to its adjacent structural piers with attached_to, or use its real structural '
                            'support; only wall cells whose lower face reaches the occupied floor may be supported_by floor_slab.',
                            path+f'/internal_parts/{j}/support')
                if obj.collision_role=='shell':
                    text=f'{part.id} {part.semantic}'.casefold()
                    is_header=any(token in text for token in
                        ('header','lintel','above opening','above window','above door','过梁','窗上','门上'))
                    if is_header:
                        target=next((item for item in obj.internal_parts
                                     if item.id==part.attached_to),None) if part.attached_to else None
                        valid=(part.form is not None and part.form.kind=='beam'
                               and part.form.axis in {'x','y'})
                        if valid and target and target.form and target.form.kind=='panel':
                            valid=target.form.axis in {'x','y'} and part.form.axis!=target.form.axis
                        if not valid:
                            fail('shell_header_form',
                                f'{obj.id}/{part.id}: an above-opening lintel must be one horizontal beam along the wall tangent, '
                                'not a wall panel or vertical member. When attached to a pier, its beam axis must differ '
                                'from the pier panel thickness-normal axis.',
                                path+f'/internal_parts/{j}/form')
                    is_door_head=(is_header and any(token in text for token in
                                     ('door','entrance','entry','门')))
                    if (is_door_head and part.form is not None and part.form.kind=='beam'
                            and part.form.minimum_ratio>1.100001):
                        fail('shell_door_head_ratio',
                            f'{obj.id}/{part.id}: a doorway head beam must use minimum_ratio=1.1; '
                            'the compiler measures its actual width and depth from the frozen opening, '
                            'then geometry validation checks the resulting beam proportion.',
                            path+f'/internal_parts/{j}/form')
                if _part_form_semantic_conflict(part):
                    fail('part_form_semantic',
                        f'{obj.id}/{part.id}: frozen form={part.form.kind} conflicts with the semantic shape. '
                        'A beam is one straight member; a panel is a thin board/plate, not a solid block, plinth, or pedestal. '
                        'Use form=null for a single compound or block-like part, or split a multi-member assembly '
                        'into stable physical members and give each an accurate form and joints.',
                        path+f'/internal_parts/{j}/form')
            for j,clearance in enumerate(obj.internal_clearances):
                if clearance.a==clearance.b or not {clearance.a,clearance.b}<=internal_ids:
                    fail('internal_clearance',f'{obj.id}: Internal clearance must reference two frozen internal part IDs',path+f'/internal_clearances/{j}')
            if obj.acquisition.strategy!='parametric' and obj.internal_parts:
                fail('external_internals',f'{obj.id}: internal_parts require acquisition.strategy="parametric". '
                    'auto means retrieve/generate an opaque mesh, NOT aDSL. Choose parametric for editable internal parts; '
                    'use external acquisition only for a separate rigid subasset without editable internal_parts.',path)
            if obj.parent and obj.parent not in known:
                fail('missing_parent',f'{obj.id}: missing parent',path+'/parent');graph_valid=False
            if obj.surface_contact:
                if not obj.parent or obj.mount!='origin' or obj.grounded or obj.support_part:
                    fail('mounting_contract',f'{obj.id}: surface_contact needs parent,origin mount,no grounding or support_part',path)
                elif obj.parent in by_id:
                    parent=by_id[obj.parent]
                    if parent.acquisition.strategy!='parametric' or obj.surface_contact.parent_part not in {p.id for p in parent.internal_parts}:
                        fail('mounting_parent_part',f'{obj.id}: name an actual frozen parametric parent surface',path+'/surface_contact')
                if obj.surface_contact.child_part and (obj.acquisition.strategy!='parametric' or obj.surface_contact.child_part not in internal_ids):
                    fail('mounting_child_part',f'{obj.id}: child_part must be its actual editable mounting member; external mesh uses null',path+'/surface_contact')
            if obj.mount in {'top','bottom'} and not obj.parent:
                fail('mount_parent',f'{obj.id}: Surface mounting requires a parent',path+'/parent');graph_valid=False
            if obj.support_part:
                if obj.mount not in {'top','bottom'} or not obj.parent:
                    fail('support_part_mount',f'{obj.id}: support_part requires a top/bottom mounted child and parent',path+'/support_part')
                parent=by_id.get(obj.parent)
                if parent:
                    if parent.acquisition.strategy!='parametric':
                        fail('support_part_source',f'{obj.id}: named support parts require a parametric parent',path+'/support_part')
                    if obj.support_part not in {part.id for part in parent.internal_parts}:
                        fail('support_part_id',f'{obj.id}: support_part {obj.support_part} is not a frozen internal part of {parent.id}',path+'/support_part')
            if obj.acquisition.strategy=='generate' and not obj.acquisition.custom_appearance.strip():
                fail('generation_reason',f'{obj.id}: Direct generation requires the custom appearance specified by this brief; otherwise auto',path+'/acquisition')
            try:obj.acquisition.executable(self.quality_target)
            except ValueError as failure:fail('source_contract',f'{obj.id}: {failure}',path+'/acquisition')
            if obj.acquisition.strategy!='parametric' and obj.rigidity!='rigid':
                fail('external_rigidity',f'{obj.id}: External geometry is rigid; independently editable structure must be separate',path+'/rigidity')
            seen={obj.id};parent=obj.parent
            while parent:
                if parent not in known:
                    graph_valid=False;break
                if parent in seen:
                    fail('parent_cycle',f'{obj.id}: Cyclic support/parent graph',path+'/parent');graph_valid=False;break
                seen.add(parent);parent=by_id[parent].parent
            # Reuse bounded expression evaluation without creating proxy geometry.
            try:sizes[obj.id]=vec(obj.dimensions,self.parameters,positive=True)
            except (ValueError,KeyError,TypeError,ZeroDivisionError,SyntaxError) as failure:
                fail('dimensions',f'{obj.id}: {failure}',path+'/dimensions')
            if obj.surface_contact and obj.id in sizes:
                try:validate_wall_contact_axis(obj.surface_contact,sizes[obj.id])
                except ValueError as failure:
                    diagnostics.add('mounting_face_axis',f'{obj.id}: {failure}',
                        paths=[path+'/surface_contact',f'/bindings/{i}/surface_contact'],
                        component_ids=[obj.id],
                        measured={'repair_target':'functional_bindings'})
            if obj.collision_role=='floor_covering':
                if not obj.grounded:
                    fail('floor_covering_contact',f'{obj.id}: floor coverings must rest on the occupied floor datum',path+'/grounded')
                if obj.parent is not None:
                    fail('floor_covering_root',f'{obj.id}: floor coverings must be independent floor-level scene objects',path+'/parent')
                if obj.id in sizes and sizes[obj.id][2]>.08:
                    fail('floor_covering_thickness',f'{obj.id}: floor_covering thickness must be at most 0.08m; use solid for a raised platform',path+'/dimensions')
            if obj.support_footprint:
                try:
                    contact=[scalar(v,self.parameters) for v in obj.support_footprint]
                    if any(v<=0 for v in contact):fail('contact_size',f'{obj.id}: Contact footprint must be positive',path+'/support_footprint')
                    if obj.id in sizes and any(v>sizes[obj.id][j]+.001 for j,v in enumerate(contact)):
                        fail('contact_envelope',f'{obj.id}: Contact footprint exceeds object envelope',path+'/support_footprint')
                except (ValueError,KeyError,TypeError,ZeroDivisionError,SyntaxError) as failure:
                    fail('contact_expression',f'{obj.id}: {failure}',path+'/support_footprint')
                if not obj.interface_notes:fail('contact_evidence',f'{obj.id}: An explicit footprint needs interface evidence requirements',path+'/interface_notes')
        chains_valid=graph_valid and len(sizes)==len(known)
        if not chains_valid:
            diagnostics.block('support_chains','Unique acyclic parent IDs and valid dimensions are required.',paths=['/objects'])
        for obj in self.objects if chains_valid else []:
            height=sizes[obj.id][2];cursor=obj;known_height=True
            while cursor.parent and cursor.mount=='top':
                if cursor.support_part:
                    known_height=False
                    break
                cursor=by_id[cursor.parent];height+=sizes[cursor.id][2]
            if known_height and height>self.extent[2]+.015 and not (obj.collision_role=='shell' and obj.parent is None):
                fail('support_height',f'{obj.id}: support-chain height {height:.3f} exceeds scene height {self.extent[2]}; revise decomposition extent before layout','/objects','/extent')
            if obj.mount=='top':
                try:footprint=[scalar(v,self.parameters) for v in obj.support_footprint] if obj.support_footprint else sizes[obj.id][:2]
                except (ValueError,KeyError,TypeError,ZeroDivisionError,SyntaxError):continue
                parent_xy=sizes[obj.parent][:2]
                if any(v>p+.015 for v,p in zip(sorted(footprint),sorted(parent_xy))):
                    fail('support_fit',f'{obj.id}: contact cannot fit {obj.parent}; specify actual base footprint, not foliage envelope, or revise component boundary','/objects')
        for clearance in valid_clearances if chains_valid else []:
            for child,ancestor in ((clearance.a,clearance.b),(clearance.b,clearance.a)):
                cursor=by_id[child];gap=0.
                while cursor.parent and cursor.mount=='top':
                    if cursor.parent==ancestor:
                        if clearance.minimum_m>gap+.015:
                            fail('support_clearance',f'clearance {child}->{ancestor} is impossible: top support chain fixes separation to {gap:.3f}m. '
                                'A usable area beside an object is not a 3D gap from its supporting surface. Express an appropriate sibling separation or layout placement requirement.','/clearances')
                        break
                    cursor=by_id[cursor.parent];gap+=sizes[cursor.id][2]
        for check in valid_edits:
            changed={**self.parameters,check.parameter:self.parameters[check.parameter]+check.delta}
            for obj in self.objects:
                if obj.rigidity=='rigid':
                    try:changed_size=vec(obj.dimensions,changed,positive=True)
                    except (ValueError,KeyError,TypeError,ZeroDivisionError,SyntaxError) as failure:
                        fail('edit_dimensions',f'Edit {check.parameter}: {obj.id}: {failure}','/objects',scenario=check.parameter);continue
                    if obj.id not in sizes:
                        diagnostics.block('rigid_edit','Baseline size is unavailable.',paths=['/objects'],scenario=check.parameter)
                    elif any(abs(a-b)>1e-8 for a,b in zip(changed_size,sizes[obj.id])):
                        fail('rigid_edit',f'Edit {check.parameter}: rigid {obj.id} would resize. '
                            'An editable structural member must declare rigidity=parametric; genuinely rigid contents must use invariant dimensions. Fix decomposition, not positions.','/objects',scenario=check.parameter)
        scenarios=[('baseline',self.parameters)]+[(f'edit {c.parameter} {c.delta:+}',
            {**self.parameters,c.parameter:self.parameters[c.parameter]+c.delta}) for c in valid_edits]
        for label,params in scenarios:
            dims={}
            for i,obj in enumerate(self.objects):
                try:dims[obj.id]=vec(obj.dimensions,params,positive=True)
                except (ValueError,KeyError,TypeError,ZeroDivisionError,SyntaxError) as failure:
                    fail('scenario_dimensions',f'{label}: {obj.id}: {failure}',f'/objects/{i}/dimensions',scenario=label)
                if obj.parent is None and obj.id in dims and dims[obj.id][0]*dims[obj.id][1]>self.extent[0]*self.extent[1]+1e-6:
                    fail('scene_footprint_capacity',f'{label}: {obj.id} footprint exceeds the entire frozen scene. '
                        'No placement/yaw can repair this. Correct dimensions/extent before layout. If an '
                        'UNREQUESTED room-resize edit caused it, omit that unsupported demonstration edit; '
                        'preserve all actual user-requested edits.',f'/objects/{i}/dimensions','/extent','/edit_checks',scenario=label)
            if not chains_valid or len(dims)!=len(known):
                diagnostics.block('scenario_support_chains','A valid parent graph and scenario dimensions are required.',scenario=label)
                continue
            def offset(obj):
                if not obj.parent or obj.mount=='origin':return obj.id,0.
                root,z=offset(by_id[obj.parent])
                # A named shelf/floor is not the enclosing object's envelope
                # top. Its elevation only exists once its recipe is compiled.
                # Keep this preflight unknown rather than inventing a height;
                # final assembly and measured geometry still check the surface.
                if z is None or obj.support_part:return root,None
                return root,z+(dims[obj.parent][2] if obj.mount=='top' else -dims[obj.id][2])
            offsets={o.id:offset(o) for o in self.objects}
            floor_origins={}
            for obj in self.objects:
                if obj.grounded:
                    root,z=offsets[obj.id]
                    if z is None:continue
                    required=-z
                    if root in floor_origins and abs(floor_origins[root]-required)>.015:
                        fail('grounded_heights',f'{label}: incompatible grounded support heights in {root}','/objects',scenario=label)
                    floor_origins[root]=required
            for obj in self.objects:
                root,z=offsets[obj.id]
                if z is None:continue
                top=floor_origins.get(root,0)+z+dims[obj.id][2]
                if top>self.extent[2]+.015 and not (obj.collision_role=='shell' and obj.parent is None):
                    fail('scenario_height',f'{label}: {obj.id} requires scene height at least {top:.3f}m from grounded support chain; '
                         f'extent Z={self.extent[2]}m cannot fit. Revise extent, not object positions or sizes.','/objects','/extent',scenario=label)
        diagnostics.raise_if_any()
        return self


class Recipe(StrictModel):
    parts: list[PartSpec] = Field(min_length=1,max_length=160)
    assembly: AssemblyContract | None = None


class Placement(StrictModel):
    id: str
    position: Vec3 = Field(description='Root: scene-centered XY and floor Z. Child: parent-local offset. '
        'For mount=top/bottom the compiler adds the selected support surface height: use Z=0 for contact, '
        'never repeat the table/shelf height. With support_part, XY is relative to that part center.')
    yaw_deg: float = Field(default=0,description='Relative to parent yaw for children; world yaw for roots. Local front is -Y.')


class Layout(StrictModel):
    placements: list[Placement] = Field(min_length=1,max_length=80)
    route_points: dict[str,list[tuple[float,float]]] = Field(default_factory=dict)
    access_centers: dict[str,tuple[float | str,float | str]] = Field(default_factory=dict)


class MountingInterfaceRevisionRequired(PlanningValidationError):
    """A proven interface capacity failure cannot be repaired through positions."""


class DetailedPlanningRevisionRequired(PlanningValidationError):
    """A measured obstruction needs component planning, not position retries."""


class MountingInterfaceRevision(StrictModel):
    contacts: dict[str,SurfaceContact] = Field(description='Map each failed COMPONENT ID to exactly ONE '
        'SurfaceContact. Keys equal failed_component_ids. Contact names and part IDs are not keys.')
    reason: str = Field(min_length=12)


def validate_mounting_interface_revision(revision,outline,owners):
    if set(revision.contacts)!=set(owners):
        diagnostics=Diagnostics('mounting_interface_revision')
        diagnostics.add('contact_owner_ids','Replace the /contacts object with exactly the failed COMPONENT '
            'IDs as keys, each mapped to ONE SurfaceContact. Do not use contact labels or part IDs as keys.',
            paths=['/contacts'],component_ids=sorted(owners),
            measured={'received_keys':sorted(revision.contacts)},
            expected={'required_keys':sorted(owners),'contacts_per_component':1})
        diagnostics.raise_if_any()
    revised=outline.model_copy(deep=True)
    diagnostics=Diagnostics('mounting_interface_revision')
    for obj in revised.objects:
        if obj.id in revision.contacts:
            contact=revision.contacts[obj.id]
            try:
                validate_wall_contact_axis(contact,[scalar(v,outline.parameters) for v in obj.dimensions])
                validate_wall_functional_face(contact)
                child_part=next((part for part in obj.internal_parts if part.id==contact.child_part),None)
                if child_part and child_part.form and child_part.form.kind=='panel':
                    validate_wall_child_part_face_axis(contact,child_part.id,
                        normal_axis=child_part.form.axis)
            except ValueError as error:
                diagnostics.add('mounting_face_axis',f'{obj.id}: {error}',
                    paths=[f'/contacts/{obj.id}/child_face'],component_ids=[obj.id],
                    measured={'repair_target':'mounting_interface_revision'})
            obj.surface_contact=contact
    diagnostics.raise_if_any()
    # Only contact choices changed; inventory, geometry, parent graph, access,
    # style, dimensions and original user contracts are preserved.
    return Decomposition.model_validate(revised.model_dump(mode='json'))


def mounting_interface_response_schema(owners):
    schema=MountingInterfaceRevision.model_json_schema()
    value=schema['properties']['contacts']['additionalProperties']
    schema['properties']['contacts']={**schema['properties']['contacts'],'type':'object',
        'properties':{id:copy.deepcopy(value) for id in sorted(owners)},
        'required':sorted(owners),'additionalProperties':False}
    return schema


def spatial_checkpoint_mismatches(candidate, outline, design):
    try:
        spatial=SpatialContract.model_validate(candidate)
    except (ValueError,TypeError,KeyError) as failure:
        return ['invalid spatial schema: '+str(failure)]
    object_ids={obj.id for obj in outline.objects}
    roots={obj.id for obj in outline.objects if not (obj.parent or getattr(obj,'support',None))}
    zone_members=[component_id for zone in spatial.zones for component_id in zone.component_ids]
    unknown=set(zone_members)-object_ids
    if unknown:return ['zone component IDs are absent from the current decomposition: '+str(sorted(unknown))]
    counts={component_id:zone_members.count(component_id) for component_id in roots}
    if any(count!=1 for count in counts.values()):
        return ['primary zone membership differs from the current decomposition roots: '+str(counts)]
    if any(relation.a not in object_ids or relation.b not in object_ids for relation in spatial.relations):
        return ['spatial relation endpoints differ from the current decomposition']
    if any(area.owner_id not in object_ids for area in spatial.access_areas):
        return ['access-area owners differ from the current decomposition']
    opening_ids={opening.id for opening in spatial.openings}
    access_ids={area.id for area in spatial.access_areas}
    nodes=opening_ids|access_ids
    if any(route.start not in nodes or route.end not in nodes for route in spatial.routes):
        return ['route endpoints differ from the current openings/access areas']

    composition=design.composition if design is not None else None
    expected_groups={item.id for item in composition.groups} if composition else set()
    expected_links={item.id for item in composition.links} if composition else set()
    expected_spaces={item.id for item in composition.open_spaces} if composition else set()
    expected_views={item.id for item in composition.views} if composition else set()
    group_bindings={item.group_id:item for item in spatial.composition_groups}
    if set(group_bindings)!=expected_groups or len(group_bindings)!=len(spatial.composition_groups):
        return ['composition groups differ from the current scene design']
    relation_ids={relation.id for relation in spatial.relations}
    for binding in group_bindings.values():
        if (not set(binding.component_ids)<=object_ids or binding.anchor_id not in binding.component_ids
                or not set(binding.relation_ids)<=relation_ids):
            return ['composition group bindings differ from the current decomposition/design']
    link_bindings={item.link_id:item for item in spatial.composition_links}
    if set(link_bindings)!=expected_links or len(link_bindings)!=len(spatial.composition_links):
        return ['composition links differ from the current scene design']
    if any(not set(item.relation_ids)<=relation_ids for item in link_bindings.values()):
        return ['composition link relations differ from the current spatial plan']
    if {item.intent_id for item in spatial.open_spaces}!=expected_spaces:
        return ['protected empty-space intents differ from the current scene design']
    if {item.intent_id for item in spatial.experience_views}!=expected_views:
        return ['experience-view intents differ from the current scene design']
    return []


def latest_failed_stage_candidate(runtime, stage, schema=None):
    """Return the newest Agent-authored candidate accepted by the current response schema."""
    runtime=Path(runtime)
    failures=sorted(runtime.glob(stage+'_failure_*.json'),
                    key=lambda path:int(path.stem.rsplit('_',1)[1]),reverse=True)
    for path in failures:
        try:
            saved=json.loads(path.read_text(encoding='utf-8'))
        except (OSError,ValueError):
            continue
        candidate=saved.get('candidate')
        if isinstance(candidate,dict):
            if schema is not None:
                try:schema.model_validate(candidate,context={'defer_decomposition_semantics':stage=='decomposition'})
                except ValueError:continue
            return {'candidate':candidate,'path':str(path),'request_hash':saved.get('request_hash'),
                    'error':saved.get('error')}
    return None


def layout_checkpoint_mismatches(candidate, outline):
    try:
        layout=Layout.model_validate(candidate)
    except (ValueError,TypeError,KeyError) as failure:
        return ['invalid layout schema: '+str(failure)]
    expected={obj.id for obj in outline.objects}
    actual=[placement.id for placement in layout.placements]
    if len(actual)!=len(expected) or set(actual)!=expected:
        return ['placement IDs differ from the current decomposition']
    return []


class SpatialChoicesInfeasible(PlanningValidationError):
    """Signal that the spatial contract must change before more layout repair."""


def spatial_conflict_families(solver: dict, outline: Decomposition):
    """Canonicalize solver cores by spatial group so content edits do not reset escalation."""
    objects=(outline.get('objects',[]) if isinstance(outline,dict) else outline.objects) if outline is not None else []
    edit_checks=(outline.get('edit_checks',[]) if isinstance(outline,dict) else outline.edit_checks) if outline is not None else []
    def get(item,key,default=None):
        return item.get(key,default) if isinstance(item,dict) else getattr(item,key,default)
    by_id={get(obj,'id'):obj for obj in objects}
    roots={get(obj,'id'):get(obj,'parent') or get(obj,'id') for obj in objects}
    for obj in objects:
        current=obj
        current_id=get(current,'id')
        seen=set()
        while get(current,'parent') and get(current,'parent') in roots and current_id not in seen:
            seen.add(current_id)
            current=by_id[get(current,'parent')]
            current_id=get(current,'id')
        roots[get(obj,'id')]=current_id
    scenarios={'baseline',*(get(check,'parameter') for check in edit_checks)}

    def family(label):
        for scenario in scenarios:
            prefix=scenario+' '
            if label.startswith(prefix):
                label=label[len(prefix):]
                break
        if label.startswith(('support footprint:', 'supported content separation:')):
            members=label.split(':',1)[1].strip().split('/')
            return 'support_group:'+','.join(sorted({roots.get(member,member) for member in members}))
        if label.startswith('zone '):
            return 'zone:'+label[5:].split(':',1)[0]
        if label.startswith('clear approach '):
            return 'clear_approach:'+label[len('clear approach '):].split(':',1)[0]
        if label.startswith('usable approach boundary: '):
            return 'approach_boundary:'+label.split(':',1)[1].strip()
        if label.startswith('scene bounds: '):
            return 'scene_bounds:'+roots.get(label.split(':',1)[1].strip(),label.split(':',1)[1].strip())
        if label.startswith('preserve mounting normal pending real surface: '):
            return 'mounting:'+label.rsplit(':',1)[1].strip()
        if label.startswith('furniture separation: '):
            members=label.split(':',1)[1].strip().split('/')
            return 'furniture_separation:'+','.join(sorted(roots.get(member,member) for member in members))
        if label.startswith('functional relation '):
            return 'functional_relation:'+label.split()[2]
        if label.startswith(('route ', 'joint corridor', 'joint endpoint', 'full corridor')):
            return label.split(':',1)[0]
        return label

    groups=solver.get('independent_conflict_groups') or []
    labels=[label for group in groups for label in group]
    if not labels:labels=solver.get('incompatible_constraints',[])
    return sorted({family(label) for label in labels})


def support_group_revision_issues(families, outline):
    """Turn repeated support-surface conflicts into component-design feedback."""
    objects=(outline.get('objects',[]) if isinstance(outline,dict) else outline.objects) if outline is not None else []
    def get(item,key,default=None):
        return item.get(key,default) if isinstance(item,dict) else getattr(item,key,default)
    by_id={get(obj,'id'):obj for obj in objects}
    roots={}
    for obj in objects:
        current=obj;current_id=get(current,'id');seen=set()
        while get(current,'parent') and get(current,'parent') in by_id and current_id not in seen:
            seen.add(current_id);current=by_id[get(current,'parent')];current_id=get(current,'id')
        roots[get(obj,'id')]=current_id
    result=[]
    for family in families:
        if not family.startswith('support_group:'):continue
        group_roots=set(family.split(':',1)[1].split(','))
        members=[obj for obj in objects if roots.get(get(obj,'id')) in group_roots]
        member_ids=[get(obj,'id') for obj in members]
        paths=[]
        for index,obj in enumerate(objects):
            if get(obj,'id') not in member_ids:continue
            paths.append(f'/objects/{index}/dimensions')
            if get(obj,'id') not in group_roots:paths.append(f'/objects/{index}/support_footprint')
        result.append({'code':'repeated_support_group_conflict','component_ids':member_ids,
            'constraint_id':family,'scenario':'baseline','repair_target':'decomposition',
            'evidence':'The same fixed support group remains infeasible after a changed layout repair. The fixed-yaw numerical solver is conservative; review the support surface capacity and supported-content grouping before asking layout to try more coordinates. Preserve every user-required content item.',
            'paths':paths,'measured':{'repair_target':'decomposition','support_group':family,
                'solver_limit':'fixed yaws and conservative envelopes; not proof all orientations are impossible'}})
    return result


def repeated_spatial_conflict(current, history, *, threshold=.8):
    current=set(current)
    if not current:return False
    for previous in history:
        previous=set(previous)
        union=current|previous
        if union and len(current&previous)/len(union)>=threshold:return True
    return False


def spatial_with_layout(spatial,layout,objects,extent,*,validate=True):
    if spatial is None:
        if layout.route_points or layout.access_centers: raise ValueError('Route/access placement requires a spatial contract')
        return None
    known={r.id for r in spatial.routes}
    if set(layout.route_points)-known:
        raise ValueError('Layout contains unknown route IDs')
    changed=spatial.model_copy(deep=True)
    if set(layout.access_centers)-{a.id for a in spatial.access_areas}:
        raise ValueError('Layout contains unknown access-area IDs')
    for area in changed.access_areas:
        if area.id in layout.access_centers:
            proposed=layout.access_centers[area.id]
            if area.side in {'front','back'}:area.center=(proposed[0],area.center[1])
            elif area.side in {'left','right'}:area.center=(area.center[0],proposed[1])
            else:area.center=proposed
    for route in changed.routes:
        if route.id in layout.route_points:
            route.points=layout.route_points[route.id]
    changed=SpatialContract.model_validate(changed.model_dump())
    return validate_spatial_contract(changed,objects,extent) if validate else changed


def coarse_preflight(outline,layout,spatial):
    """Reject impossible coarse placements and defer fit/usability to measured 3D."""
    diagnostics=Diagnostics('coarse_layout')
    routed=spatial_with_layout(spatial,layout,outline.objects,outline.extent,validate=False)
    try:validate_spatial_contract(routed,outline.objects,outline.extent)
    except ValueError as failure:
        nested=failure_report(failure,'spatial_contract')
        for item in nested['issues']:
            paths=[]
            for path in item.get('paths',[]):
                if re.match(r'/routes/\d+/points',path):paths.append('/route_points')
                elif re.match(r'/access_areas/\d+/center',path):paths.append('/access_centers')
            diagnostics.issues.append({**item,'paths':paths,'upstream_paths':item.get('paths',[])})
        diagnostics.blocked_checks.extend(nested.get('blocked_checks',[]))
    spatial_contract_issues=copy.deepcopy(diagnostics.issues)
    scenarios=[('baseline',outline,None)]+[(check.parameter,outline.model_copy(update={'parameters':{
        **outline.parameters,check.parameter:outline.parameters[check.parameter]+check.delta}}),check.delta)
        for check in outline.edit_checks]
    report={'bounds':{},'findings':[],'edit_scenarios':[],
            'deferred_findings':[],'blocking_findings':[]}

    def blocks_before_build(finding):
        evidence=finding.get('evidence','')
        constraint=finding.get('constraint_id')
        if constraint and constraint.startswith('entrance_leaf:'):
            return True
        if finding.get('constraint_id') in {'placement_expression','parent_graph','mounting_orientation'}:
            return True
        if evidence.startswith('Root envelope is outside extent or violates grounded floor contact'):
            return True
        measured=finding.get('measured') or {}
        # A component far outside its declared functional zone is a planning
        # error, while a small envelope edge miss remains an exported-geometry
        # concern. This keeps trim/fixture rounding from triggering a repair.
        if 'world_bounds_m' in measured and 'zone_bounds_xy_m' in measured:
            world=np.asarray(measured['world_bounds_m'],dtype=float)
            zone=np.asarray(measured['zone_bounds_xy_m'],dtype=float)
            if world.shape==(2,3) and zone.shape==(2,2):
                violation=max(float(zone[0,0]-world[0,0]),float(world[1,0]-zone[1,0]),
                              float(zone[0,1]-world[0,1]),float(world[1,1]-zone[1,1]),0.)
                if violation>.75:return True
        # Open-space intents are explicit negative space, so a solid blocker
        # means the planned composition cannot be realized as authored.
        if isinstance(constraint,str) and constraint.startswith('design_empty_') and ' is blocked by ' in evidence:
            return True
        # Explicit opening fit failures need correction before recipes/assets;
        # small depth or rounding differences remain measured after building.
        if 'does not fit opening' in evidence:
            projection=float(measured.get('measured_interior_projection_m',0.))
            aperture=measured.get('opening_aperture_m',{})
            allowed=float(aperture.get('allowed_interior_projection',float('inf')))
            width=float(aperture.get('width',0.)) if isinstance(aperture,dict) else 0.
            opening=next((o for o in routed.openings if o.id==constraint),None)
            along=1 if opening and opening.wall.startswith('x') else 0
            bounds=np.asarray(measured.get('world_bounds_m',[]),dtype=float)
            if projection>allowed+.25 or (bounds.shape==(2,3) and width and bounds[1,along]-bounds[0,along]>width+.25):
                return True
        if evidence != 'Coarse root envelopes overlap':
            relation=next((item for item in routed.relations if item.id==constraint),None)
            # Block only clear composition failures. Small clearance misses stay
            # for measured geometry so conservative envelopes do not cause a
            # needless model-repair turn.
            if relation:
                kind=relation.kind
                if kind in {'left_of','right_of','in_front_of','behind'} and measured.get('directional_gap_m',0)<-.30:
                    return True
                if kind in {'distance','facing'}:
                    distance=measured.get('footprint_distance_m')
                    minimum=measured.get('minimum_m',relation.minimum_m)
                    maximum=measured.get('maximum_m',relation.maximum_m)
                    gap_error=max(float(minimum)-float(distance),
                                  float(distance)-float(maximum) if maximum is not None else 0.) if distance is not None else 0.
                    angle_error=(float(measured.get('front_target_angle_deg',0.))-relation.tolerance_deg
                                 if kind=='facing' else 0.)
                    if gap_error>.25 or angle_error>20.:return True
            return False
        measured=finding.get('measured',{})
        a=np.asarray(measured.get('world_bounds_a_m',[]),dtype=float)
        b=np.asarray(measured.get('world_bounds_b_m',[]),dtype=float)
        depth=np.asarray(measured.get('overlap_depth_xyz_m',[]),dtype=float)
        if a.shape!=(2,3) or b.shape!=(2,3) or depth.shape!=(3,):
            return False
        # Envelopes are intentionally conservative. Only reject a near-total
        # intersection of both objects; partial intersections need real parts.
        dimensions_a=a[1]-a[0];dimensions_b=b[1]-b[0]
        penetration_a=depth/np.maximum(dimensions_a,1e-9)
        penetration_b=depth/np.maximum(dimensions_b,1e-9)
        return bool(np.all(depth>=.15) and np.all(penetration_a>=.45) and np.all(penetration_b>=.45))

    for label,candidate,delta in scenarios:
        try:current=coarse_layout(candidate,layout,routed)
        except (ValueError,KeyError,TypeError,ZeroDivisionError,SyntaxError) as failure:
            nested=failure_report(failure,'coarse_layout')
            diagnostics.issues.extend({**item,'scenario':label} for item in nested['issues'])
            diagnostics.block('spatial_occupancy','Envelope positions could not be fully evaluated.',scenario=label)
            continue
        if label=='baseline':
            report.update(current)
        else:
            report['edit_scenarios'].append({'parameter':label,'delta':delta,
                'status':current['status'],'findings':current['findings']})
            report['findings'].extend({**finding,'edit_scenario':label} for finding in current['findings'])
        diagnostics.blocked_checks.extend({**item,'scenario':label} for item in current.get('blocked_checks',[]))
        for finding in current['findings']:
            paths=[f'/placements/{i}/position' for i,p in enumerate(layout.placements)
                   if p.id in finding.get('component_ids',[])]
            paths.extend(f'/placements/{i}/yaw_deg' for i,p in enumerate(layout.placements)
                         if p.id in finding.get('component_ids',[]))
            constraint=finding.get('constraint_id')
            if any(r.id==constraint for r in routed.routes):paths.append('/route_points')
            if any(a.id==constraint for a in routed.access_areas):paths.append('/access_centers')
            item={**finding,'scenario':label}
            if blocks_before_build(finding):
                report['blocking_findings'].append(item)
                diagnostics.add(finding.get('code') or constraint or 'envelope',finding['evidence'],paths=paths,
                    component_ids=finding.get('component_ids',[]),scenario=label,
                    measured=finding.get('measured'),expected=finding.get('expected'))
            else:
                report['deferred_findings'].append(item)
    # A report can be ready for 3D even while recording unresolved usability
    # findings. Those findings remain visible to the Agent and are rechecked
    # against exported geometry before the scene can be accepted.
    report['status']='needs_revision' if diagnostics.issues else 'passed'
    report['readiness']='blocked' if diagnostics.issues else 'ready_for_3d_audit'
    report['audit_boundary']={
        'pre_build':['valid transforms and parent graph','scene extent and ground contact',
                     'catastrophic coarse envelope intersections','frozen spatial contract'],
        'blender':['zones','clearances','routes and access areas','opening approaches',
                   'measured support/contact','actual furniture intersections','visual usability']}
    report.update(diagnostics.report())
    report['coverage']='Pre-build hard checks only; deferred findings are unresolved until exported Blender geometry and visual review.'
    report['spatial_contract_issues']=spatial_contract_issues
    report['routed_contract']=routed.model_dump(mode='json')
    return report


class Replacement(StrictModel):
    path: str
    value: JsonValue = None
    operation: Literal['replace','remove'] = 'replace'


class LocalRepair(StrictModel):
    reason: str
    replacements: list[Replacement] = Field(min_length=1,max_length=256)


def normalize_local_repair_proposal(proposal, candidate: dict, allowed: list[str], *, removable_paths=None):
    """Project layout edits through authorized paths and split malformed sibling edits."""
    if not isinstance(proposal, dict):
        return proposal

    normalized=copy.deepcopy(proposal)
    if not isinstance(normalized.get('replacements'), list):
        def read(root, path):
            value=root
            for part in path.split('/')[1:]:
                part=part.replace('~1','/').replace('~0','~')
                if isinstance(value,list):
                    if not part.isdigit() or int(part)>=len(value):return None,False
                    value=value[int(part)]
                elif isinstance(value,dict) and part in value:
                    value=value[part]
                else:return None,False
            return value,True

        replacements=[]
        for path in allowed:
            before,before_exists=read(candidate,path)
            after,after_exists=read(normalized,path)
            if before_exists and after_exists and before!=after:
                replacements.append({'path':path,'value':copy.deepcopy(after)})
        if not replacements:
            return proposal
        normalized={'reason':'Projected full candidate changes onto the authorized repair paths.',
                    'replacements':replacements}

    replacements=normalized['replacements']
    known_fields={'path','value','operation'}
    existing_paths={item.get('path') for item in replacements if isinstance(item,dict)}
    removed_invalid_operations=0
    normalized_open_spaces=False

    def pointer_parts(path):
        return [part.replace('~1','/').replace('~0','~') for part in path.split('/')[1:]]

    def pointer(parts):
        return ''.join('/'+str(part).replace('~','~0').replace('/','~1') for part in parts)

    def read_parts(root, parts):
        value=root
        for part in parts:
            if isinstance(value,list):
                if not part.isdigit() or int(part)>=len(value):return None,False
                value=value[int(part)]
            elif isinstance(value,dict) and part in value:
                value=value[part]
            else:return None,False
        return value,True

    def read(root,path):
        return read_parts(root,pointer_parts(path))

    def authorized(path):
        return any(path==scope or path.startswith(scope.rstrip('/')+'/') for scope in allowed)

    def recover_misnested_root_path(path):
        if not isinstance(path,str) or not path.startswith('/'):
            return path
        parts=pointer_parts(path)
        matches=[]
        for scope in allowed:
            scope_parts=pointer_parts(scope)
            if len(scope_parts)!=1:
                continue
            for index in range(len(parts)):
                if parts[index:index+1]!=scope_parts:
                    continue
                rewritten=pointer(scope_parts+parts[index+1:])
                if authorized(rewritten):
                    _,exists=read(candidate,rewritten)
                    if exists:matches.append(rewritten)
        return max(matches,key=len) if len(matches)==1 else path

    projected=[]
    removable=set(removable_paths or ())
    for replacement in replacements:
        if not isinstance(replacement,dict) or not isinstance(replacement.get('path'),str):
            projected.append(replacement)
            continue
        replacement['path']=recover_misnested_root_path(replacement['path'])
        path=replacement['path']
        if path=='/spatial/open_spaces' and isinstance(replacement.get('value'),list):
            prior_spaces=(candidate.get('spatial') or {}).get('open_spaces',[])
            for index,item in enumerate(replacement['value']):
                if not isinstance(item,dict) or index>=len(prior_spaces):
                    continue
                prior_id=prior_spaces[index].get('intent_id') if isinstance(prior_spaces[index],dict) else None
                proposed_id=item.get('intent_id',item.get('id'))
                if prior_id is None or proposed_id!=prior_id:
                    continue
                if 'intent_id' not in item and item.get('id')==prior_id:
                    item['intent_id']=item.pop('id')
                    normalized_open_spaces=True
                if 'purpose' in item:
                    item.pop('purpose')
                    normalized_open_spaces=True
        if replacement.get('operation','replace')=='remove' and path not in removable and authorized(path):
            current,exists=read(candidate,path)
            if exists:
                removed_invalid_operations+=1
                continue
        if not authorized(path) and replacement.get('operation','replace')=='replace':
            descendants=[scope for scope in allowed if scope.startswith(path.rstrip('/')+'/')]
            replacement_parts=pointer_parts(path)
            for scope in descendants:
                current,current_exists=read(candidate,scope)
                proposed,proposed_exists=read_parts(replacement.get('value'),pointer_parts(scope)[len(replacement_parts):])
                if current_exists and proposed_exists and current!=proposed:
                    projected.append({'path':scope,'value':copy.deepcopy(proposed)})
            if descendants:
                continue
        projected.append(replacement)
    replacements=normalized['replacements']=projected
    if removed_invalid_operations:
        normalized['reason']=(str(normalized.get('reason',''))+
            f' Ignored {removed_invalid_operations} unauthorized remove operation(s); frozen fields remain unchanged.')
    if normalized_open_spaces:
        normalized['reason']=(str(normalized.get('reason',''))+
            ' Normalized spatial open-space identity fields against the existing composition intent IDs; '
            'their descriptive purpose remains in composition.open_spaces.')
    existing_paths={item.get('path') for item in replacements if isinstance(item,dict)}

    siblings=[]
    for replacement in replacements:
        if not isinstance(replacement,dict):
            continue
        extra_keys=[key for key in replacement if key not in known_fields]
        path=replacement.get('path')
        if (not extra_keys or not isinstance(path,str) or not path.startswith('/')
                or '/' in path[1:]):
            continue
        for key in extra_keys:
            sibling_path='/'+str(key).replace('~','~0').replace('/','~1')
            if key not in candidate or not authorized(sibling_path) or sibling_path in existing_paths:
                continue
            siblings.append({'path':sibling_path,'value':copy.deepcopy(replacement.pop(key))})
            existing_paths.add(sibling_path)
    replacements.extend(siblings)
    return normalized


def digest(data):
    return hashlib.sha256(json.dumps(data,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def replacement_paths(data,prefix=''):
    """Existing fields at every granularity; exact paths remain explicit to the model."""
    paths=[]
    entries=enumerate(data) if isinstance(data,list) else data.items() if isinstance(data,dict) else []
    for key,value in entries:
        path=prefix+'/'+str(key).replace('~','~0').replace('/','~1')
        paths.append(path)
        paths.extend(replacement_paths(value,path))
    return paths


def apply_local_repair(document: dict, repair: LocalRepair, allowed: list[str], *, removable_paths=None):
    """Apply edits inside authorized JSON-pointer subtrees; callers revalidate the full contract."""
    edited=copy.deepcopy(document)
    paths=[r.path for r in repair.replacements]
    if len(paths)!=len(set(paths)):raise ValueError('Duplicate repair paths')

    def segments(path):
        if not path.startswith('/'):
            raise ValueError('Repair path must be a JSON pointer: '+path)
        return [s.replace('~1','/').replace('~0','~') for s in path.split('/')[1:]]

    def locate(path):
        parts=segments(path)
        target=edited
        for segment in parts[:-1]:
            target=target[int(segment)] if isinstance(target,list) else target[segment]
        key=int(parts[-1]) if isinstance(target,list) else parts[-1]
        target[key]  # replacement must exist; no insertion/deletion
        return target,key

    def merge_partial(original, proposed, path):
        if isinstance(original,list) and isinstance(proposed,list):
            # Only indexed edits into THIS list require stable length/identity.
            # A partial mapping may freely replace an unrelated sibling list.
            if not any(pointer.startswith(path.rstrip('/')+'/') for pointer in paths):
                return copy.deepcopy(proposed)
            if len(original)!=len(proposed):
                raise ValueError(f'Parent list replacement at {path} and descendant paths require the same list length')
            merged=[]
            for index,(prior,value) in enumerate(zip(original,proposed)):
                if isinstance(prior,dict) and isinstance(value,dict):
                    prior_id=prior.get('intent_id',prior.get('id'))
                    value_id=value.get('intent_id',value.get('id'))
                    if prior_id is not None and value_id is not None and prior_id!=value_id:
                        raise ValueError(f'Parent list replacement at {path} changed item identity at index {index}; '
                                         'submit the full list without descendant paths')
                merged.append(merge_partial(prior,value,f'{path}/{index}'))
            return merged
        if isinstance(original,dict) and isinstance(proposed,dict):
            merged=copy.deepcopy(original)
            for key,value in proposed.items():
                child_path=path+'/'+str(key).replace('~','~0').replace('/','~1')
                if key in merged:
                    merged[key]=merge_partial(merged[key],value,child_path)
                else:
                    merged[key]=copy.deepcopy(value)
            return merged
        return copy.deepcopy(proposed)

    for replacement in repair.replacements:
        if not any(replacement.path==path or replacement.path.startswith(path.rstrip('/')+'/')
                   for path in allowed):
            raise ValueError('Repair outside authorized fields: '+replacement.path)

    # Resolve all original-index edits before deleting array entries. Otherwise a
    # removal near the start shifts later JSON-pointer indices in the same patch.
    replacements=sorted((item for item in repair.replacements if item.operation!='remove'),
                        key=lambda item:len(segments(item.path)))
    removals=[item for item in repair.replacements if item.operation=='remove']
    replacement_paths=[item.path for item in repair.replacements]
    for replacement in replacements:
        target,key=locate(replacement.path)
        value=copy.deepcopy(replacement.value)
        has_child=any(path.startswith(replacement.path.rstrip('/')+'/') for path in replacement_paths)
        partial_mapping=(isinstance(target[key],dict) and isinstance(value,dict) and
                         not set(target[key])<=set(value))
        partial_list=(isinstance(target[key],list) and isinstance(value,list) and
                      len(target[key])==len(value) and any(
                          isinstance(prior,dict) and isinstance(proposed,dict) and
                          not set(prior)<=set(proposed)
                          for prior,proposed in zip(target[key],value)))
        if (has_child or partial_mapping or partial_list) and isinstance(target[key],(dict,list)) and isinstance(value,type(target[key])):
            value=merge_partial(target[key],value,replacement.path)
        target[key]=value

    def removal_order(item):
        parts=segments(item.path)
        parent=edited
        for segment in parts[:-1]:
            parent=parent[int(segment)] if isinstance(parent,list) else parent[segment]
        index=int(parts[-1]) if isinstance(parent,list) else -1
        return (-len(parts),item.path.rsplit('/',1)[0],-index)

    for removal in sorted(removals,key=removal_order):
        if removal.path not in set(removable_paths or ()):
            raise ValueError('Repair may remove only a field explicitly reported as an unsupported extra')
        target,key=locate(removal.path)
        if isinstance(target,dict):del target[key]
        elif isinstance(target,list):target.pop(key)
        else:raise ValueError('Repair removal requires a dictionary field or list item')
    if edited==document:raise ValueError('Repair made no change; address the stated error instead of repeating values')
    return edited


class RecipeValidationError(ValueError):
    def __init__(self,message,measurements):
        super().__init__(message)
        self.measurements=measurements


def validate_recipe(outline: Decomposition, obj: ObjectIntent, recipe: Recipe, style_requirements=None):
    recipe=recipe.model_copy(deep=True)
    diagnostics=Diagnostics('recipe_'+obj.id)
    try:recipe=_lower_frozen_assembly_relations(obj,recipe)
    except ValueError as failure:
        nested=failure_report(failure,'frozen_assembly_relations')
        diagnostics.issues.extend(nested['issues'])
    actual={p.id:p for p in recipe.parts}
    # Enforce executable style geometry while validating the local recipe, not
    # after every component recipe has been built and laid out.
    requirements={r.id:r for r in (style_requirements or [])}
    full_obj=next((item for item in outline.objects if item.id==obj.id),obj)
    full_part_ids={p.id for p in full_obj.internal_parts}
    from material_catalog import material_assignment_issues
    for issue in material_assignment_issues(obj,recipe.parts):
        part_index=next((i for i,part in enumerate(recipe.parts) if part.id==issue['part_id']),0)
        diagnostics.add('material_semantic_conflict',
            f"{issue['component_id']}/{issue['part_id']} ({issue['texture_id']}): {issue['reason']} "+
            'Choose a compatible available surface or use surface=null for the specified procedural finish.',
            paths=[f'/parts/{part_index}/surface'],component_ids=[obj.id])
    # Scope comes from the assigned stage, not the returned inventory: a model
    # omitting a required part must not disable whole-component envelope checks.
    is_complete_recipe=full_part_ids<={p.id for p in obj.internal_parts}
    for binding in outline.style_bindings:
        requirement=requirements.get(binding.requirement_id)
        if requirement is None or requirement.geometry_need!='swept_path' or obj.id not in binding.component_ids:
            continue
        for qualified in binding.part_ids:
            owner,separator,part_id=qualified.partition('/')
            if not separator or owner!=obj.id:
                continue
            part=actual.get(part_id)
            if part is None:
                if is_complete_recipe:
                    diagnostics.add('style_part',f'Style {requirement.id}: recipe is missing bound swept_path part {qualified}',paths=['/parts'],component_ids=[obj.id])
                continue  # The bound part may be in a later semantic recipe batch.
            if part.primitive not in CURVE_PRIMITIVES:
                diagnostics.add('style_primitive',f'Style {requirement.id}: {qualified} must implement the required curve as a real sweep or lathe primitive',paths=[f'/parts/{recipe.parts.index(part)}'],component_ids=[obj.id])
    for intended in obj.internal_parts:
        if intended.id not in actual:
            diagnostics.add('missing_part',f'Missing required internal part {intended.id}: {intended.semantic}',paths=['/parts'],component_ids=[obj.id])
            continue
        if actual[intended.id].rigidity!=intended.rigidity:
            diagnostics.add('part_rigidity',f'{intended.id}: recipe must preserve frozen rigidity={intended.rigidity}',paths=[f'/parts/{recipe.parts.index(actual[intended.id])}'],component_ids=[obj.id])
        targets=intended.support_targets()
        if targets:
            missing=[target for target in targets if recipe.assembly is None or not any(
                r.kind=='supported_by' and r.part==intended.id and r.target==target
                for r in recipe.assembly.relations)]
            if missing:
                diagnostics.add('missing_support',
                    f'{intended.id}: missing frozen support {", ".join(missing)}'
                    +(f' (of {len(targets)} frozen supports)' if len(targets)>1 else ''),
                    paths=['/assembly'],component_ids=[obj.id])
        if intended.attached_to and (recipe.assembly is None or not any(
            r.kind=='attached_to' and r.part==intended.id and r.target==intended.attached_to
            for r in recipe.assembly.relations)):
            diagnostics.add('missing_attachment',f'{intended.id}: missing frozen attachment {intended.attached_to}',paths=['/assembly'],component_ids=[obj.id])
    for clearance in obj.internal_clearances:
        if recipe.assembly is None or not any(r.kind=='clearance' and
            {r.part,r.target}=={clearance.a,clearance.b} and r.minimum>=clearance.minimum_m
            for r in recipe.assembly.relations):
            diagnostics.add('frozen_clearance',f'Frozen internal clearance missing/weakened: {clearance.a}->{clearance.b} >= {clearance.minimum_m}m',paths=['/assembly'],component_ids=[obj.id])

    def mentions_parameter(value, parameter):
        """Check real arithmetic expressions, ignoring prose/semantic labels."""
        if isinstance(value, str):
            try:tree=ast.parse(value.strip(), mode='eval')
            except (SyntaxError,ValueError):return False
            return any(isinstance(node, ast.Name) and node.id==parameter for node in ast.walk(tree))
        if isinstance(value, dict):return any(mentions_parameter(v,parameter) for v in value.values())
        if isinstance(value, (list,tuple)):return any(mentions_parameter(v,parameter) for v in value)
        return False

    # Route edit ownership to the component recipe.  A global parameter is
    # considered owned here only when the frozen component description or one
    # of its internal part descriptions names it.  This keeps unrelated
    # parametric siblings independent while catching the common failure where
    # a shelf/rail is described as editable but emitted as a numeric constant.
    description_text=' '.join([obj.semantic,*[p.semantic for p in obj.internal_parts],
                               *obj.interface_notes,*[str(v) for v in obj.dimensions]])
    owned_edits=[check for check in outline.edit_checks
                 if re.search(r'(?<![A-Za-z0-9_])'+re.escape(check.parameter)+r'(?![A-Za-z0-9_])',description_text)]
    # Inspect geometry-bearing fields only.  Recipe semantics and assembly
    # reasons are explanatory text and must not satisfy edit ownership.
    recipe_data=[{
        key:part.get(key) for key in ('size','center','rotation','path','radius','fit_between')
    } for part in recipe.model_dump(mode='python').get('parts',[])]
    for check in owned_edits:
        # Object dimensions are compiled into the local ``w,d,h`` symbols,
        # so an explicit dimension expression is also a genuine connection.
        wired=mentions_parameter(recipe_data,check.parameter) or mentions_parameter(obj.dimensions,check.parameter)
        if not wired:
            diagnostics.add('edit_wiring',f'Editable parameter {check.parameter} must be expressed in the parametric recipe for {obj.id}; '
                            'a semantic label or layout-only/no-op reference is insufficient',paths=['/parts'],component_ids=[obj.id],scenario=check.parameter)
    from general_scene import vec
    scenarios=[('baseline',outline.parameters)]+[(check.parameter,
        {**outline.parameters,check.parameter:outline.parameters[check.parameter]+check.delta}) for check in outline.edit_checks]
    invalid_scenarios=set()
    for scenario,global_params in scenarios:
        try:
            size=vec(obj.dimensions,global_params,positive=True)
        except (ValueError,KeyError,TypeError,ZeroDivisionError,SyntaxError) as failure:
            diagnostics.add('component_dimensions',str(failure),component_ids=[obj.id],scenario=scenario)
            diagnostics.block('part_expressions','Owner dimensions cannot be evaluated.',scenario=scenario)
            invalid_scenarios.add(scenario)
            continue
        params={**global_params,**dict(zip(('w','d','h'),size))}
        for i,part in enumerate(recipe.parts):
            for field,value,positive in [('size',part.size,True),('center',part.center,False)]:
                try:vec(value,params,positive=positive)
                except (ValueError,KeyError,TypeError,ZeroDivisionError,SyntaxError) as failure:
                    diagnostics.add('part_expression',f'{obj.id}/{part.id} {field}: {failure}',
                        paths=[f'/parts/{i}/{field}'],component_ids=[obj.id],scenario=scenario)
                    invalid_scenarios.add(scenario)
            if part.primitive in CURVE_PRIMITIVES:
                # Evaluate the actual curve/profile, so a preset parameter out of
                # range or a path outside the envelope is reported here per field.
                evaluate=lambda value,params=params:scalar(value,params)
                try:
                    dims=vec(part.size,params,positive=True)
                    if part.primitive=='sweep':
                        radius=evaluate(part.radius)
                        if radius<=0:raise ValueError('Sweep radius must be positive')
                        end=radius if part.radius_end is None else evaluate(part.radius_end)
                        if end<0:raise ValueError('Sweep radius_end must be >= 0')
                        if part.curve is not None:curve_points(part.curve,dims,radius,end,evaluate,closed=part.closed)
                        for point in part.path:vec(point,params)
                    else:
                        lathe_profile(part.profile,dims,evaluate)
                except (ValueError,KeyError,TypeError,ZeroDivisionError,SyntaxError) as failure:
                    fields=(['path','curve','radius','radius_end'] if part.primitive=='sweep' else ['profile','size'])
                    diagnostics.add(f'{part.primitive}_expression',f'{obj.id}/{part.id}: {failure}',
                        paths=[f'/parts/{i}/{f}' for f in fields],component_ids=[obj.id],scenario=scenario)
                    invalid_scenarios.add(scenario)
    try:
        local=SceneObject(id=obj.id,semantic=obj.semantic,size=obj.dimensions,
            parts=[Part.model_validate(p.model_dump()) for p in recipe.parts],assembly=recipe.assembly,
            rigidity=obj.rigidity)
    except ValueError as failure:
        diagnostics.add('recipe_graph',str(failure),paths=['/parts','/assembly'],component_ids=[obj.id])
        diagnostics.block('baseline_and_edit_geometry','Invalid part dependency graph prevents compilation.',paths=['/parts'])
        diagnostics.raise_if_any()
    try:
        contract=GeneralPlan(scene_id='recipe_check',description=obj.semantic,extent=outline.extent,
                             parameters=outline.parameters,objects=[local])
    except ValueError as failure:
        diagnostics.add('recipe_contract',str(failure),paths=['/parts','/assembly'],component_ids=[obj.id])
        diagnostics.block('baseline_and_edit_geometry','Invalid component contract prevents compilation.')
        diagnostics.raise_if_any()
    evidence={}
    try:
        if 'baseline' in invalid_scenarios:raise ValueError('Invalid baseline expressions; see all part_expression issues.')
        compile_local_geometry(contract,local,evidence=evidence)
        baseline_passed=True
    except ValueError as failure:
        baseline_passed=False
        findings=evidence.get('assembly',{}).get('findings',[])
        for finding in findings:
            paths=[f'/parts/{i}' for i,p in enumerate(recipe.parts) if p.id in finding.get('part_ids',[])]
            diagnostics.add(finding['code'],finding['evidence'],paths=paths or ['/assembly'],component_ids=[obj.id])
        if not findings:
            diagnostics.add('local_geometry',str(failure),paths=['/parts'],component_ids=[obj.id])
            diagnostics.block('assembly_measurements','Local geometry could not be fully evaluated.',paths=['/parts'])

    def check_envelope(plan, records, scenario):
        import numpy as np
        if not is_complete_recipe:
            return  # A deferred content batch may still establish an envelope edge.
        if set(records)!=set(actual):
            diagnostics.block('component_envelope','Complete part measurements are unavailable.',paths=['/parts'],scenario=scenario)
            return
        measured=np.array([r['bounds'] for r in records.values()])
        low=measured[:,0].min(axis=0);high=measured[:,1].max(axis=0)
        expected=np.array([scalar(v,plan.parameters) for v in obj.dimensions])
        envelope_low=np.array([-expected[0]/2,-expected[1]/2,0])
        envelope_high=np.array([expected[0]/2,expected[1]/2,expected[2]])
        part_indices={part.id:index for index,part in enumerate(recipe.parts)}

        def controlling_paths(axis,side,actual_face):
            candidates=[part_id for part_id,record in records.items()
                if abs(record['bounds'][side][axis]-actual_face)<=1e-5]
            if not candidates:
                candidates=[min(records,key=lambda part_id:
                    abs(records[part_id]['bounds'][side][axis]-actual_face))]
            return list(dict.fromkeys(
                f'/parts/{part_indices[part_id]}/{field}'
                for part_id in candidates for field in ('size','center','rotation')))

        for axis,label in enumerate('xyz'):
            low_outside=abs(low[axis]-envelope_low[axis])>.001
            high_outside=abs(high[axis]-envelope_high[axis])>.001
            if low_outside or high_outside:
                paths=[]
                if low_outside:
                    paths.extend(controlling_paths(axis,0,low[axis]))
                if high_outside:
                    paths.extend(controlling_paths(axis,1,high[axis]))
                diagnostics.add('component_bounds',f'{obj.id}: geometry violates centered envelope/bottom on {label}.',
                    paths=list(dict.fromkeys(paths)),component_ids=[obj.id],scenario=scenario,
                    measured=[float(low[axis]),float(high[axis])],
                    expected=[float(envelope_low[axis]),float(envelope_high[axis])])
            if abs((high-low)[axis]-expected[axis])>.002:
                sides=[(0,low[axis])] if low_outside else []
                if high_outside:sides.append((1,high[axis]))
                if not sides:sides=[(0,low[axis]),(1,high[axis])]
                paths=list(dict.fromkeys(path for side,face in sides
                                         for path in controlling_paths(axis,side,face)))
                diagnostics.add('component_size',f'{obj.id}: measured parametric size on {label} differs from frozen envelope; repair the recipe geometry.',
                    paths=paths,component_ids=[obj.id],scenario=scenario,
                    measured=float((high-low)[axis]),expected=float(expected[axis]))
    check_envelope(contract,evidence.get('parts',{}),'baseline')
    # Lower verified semantic support to relative coordinates. This changes the
    # representation, not the baseline geometry, and is independent of category.
    # Never snap an invalid initial contact into place: the audit above must pass.
    anchors=[]
    for intended in obj.internal_parts if baseline_passed and not diagnostics.issues else []:
        targets=intended.support_targets()
        # Contents rest on one shelf. A straddling member keeps its authored
        # coordinates: anchoring it to one support would silently tilt the span.
        if intended.role!='content' or len(targets)!=1:continue
        primary=targets[0]
        part=actual[intended.id]
        if part.parent==primary and part.mount=='top':continue
        # Explicit expressions remain under model control and are checked below.
        if part.parent or any(isinstance(v,str) for v in part.center):continue
        record=evidence['parts'][part.id];support=evidence['parts'][primary]
        part.center=(record['center'][0]-support['center'][0],record['center'][1]-support['center'][1],
                     record['bounds'][0][2]-support['bounds'][1][2])
        part.parent=primary;part.mount='top';anchors.append(part.id)
    if anchors:
        import numpy as np
        local=SceneObject(id=obj.id,semantic=obj.semantic,size=obj.dimensions,
            parts=[Part.model_validate(p.model_dump()) for p in recipe.parts],assembly=recipe.assembly,rigidity=obj.rigidity)
        contract.objects=[local];anchored={}
        compile_local_geometry(contract,local,evidence=anchored)
        if any(not np.allclose(evidence['parts'][id]['bounds'],anchored['parts'][id]['bounds'],rtol=0,atol=1e-8) for id in evidence['parts']):
            raise ValueError('Support-coordinate lowering must preserve every baseline part bound')
        evidence=anchored
    import numpy as np
    for check in outline.edit_checks:
        edited=contract.model_copy(deep=True)
        edited.parameters[check.parameter]+=check.delta
        after={}
        try:
            if check.parameter in invalid_scenarios:raise ValueError('Invalid edit expressions; see all part_expression issues.')
            compile_local_geometry(edited,edited.objects[0],evidence=after)
            edit_passed=True
        except ValueError as failure:
            edit_passed=False
            findings=after.get('assembly',{}).get('findings',[])
            for finding in findings:
                diagnostics.add(finding['code'],f'EDIT {check.parameter} {check.delta:+}: '+finding['evidence'],
                    paths=[f'/parts/{i}' for i,p in enumerate(recipe.parts) if p.id in finding.get('part_ids',[])] or ['/assembly'],
                    component_ids=[obj.id],scenario=check.parameter)
            if not findings:
                diagnostics.add('edit_geometry',f'EDIT {check.parameter} {check.delta:+}: {failure}',paths=['/parts'],component_ids=[obj.id],scenario=check.parameter)
        check_envelope(edited,after.get('parts',{}),check.parameter)
        # Effect/rigidity comparisons require a valid baseline; scenario geometry
        # above is still checked independently when the baseline is defective.
        if not baseline_passed or not edit_passed:
            diagnostics.block('edit_invariants','A valid baseline is needed for effect and rigidity comparison.',scenario=check.parameter)
            continue
        effect=False
        for i,part in enumerate(local.parts):
            before_record=evidence['parts'][part.id];after_record=after['parts'][part.id]
            shape_keys=('local_path','radius') if part.primitive=='sweep' else ()
            def differs(keys):
                return any(not np.allclose(before_record[k],after_record[k],rtol=0,atol=1e-8) for k in keys)
            effect=effect or differs(('size','center','rotation')+shape_keys)
            keys=('size','center','rotation') if obj.rigidity=='rigid' else ('size','rotation')
            if (obj.rigidity=='rigid' or part.rigidity=='rigid') and differs(keys+shape_keys):
                diagnostics.add('rigid_part_edit',f'EDIT {check.parameter}: would deform rigid internal part {obj.id}/{part.id}',
                    paths=[f'/parts/{i}'],component_ids=[obj.id],scenario=check.parameter,
                    measured={'before':before_record,'after':after_record})
        if len(outline.objects)==1 and not effect:
            diagnostics.add('no_edit_effect',f'Edit {check.parameter} {check.delta:+} has no measured geometry effect. '
                'Connect the parameter to actual part sizes/positions.',paths=['/parts'],component_ids=[obj.id],scenario=check.parameter)
    if diagnostics.issues:
        report=diagnostics.report()
        report.update(evaluated_parameters=outline.parameters,parts=evidence.get('parts',{}),
                      assembly_findings=evidence.get('assembly',{}).get('findings',[]))
        raise RecipeValidationError('; '.join(i['evidence'] for i in diagnostics.issues),report)
    return recipe


def validate_structural_batch_ids(recipe: Recipe, structure, contents):
    """The structure stage may build every frozen non-content ID, and nothing else."""
    expected={p.id for p in structure}
    assigned_contents={p.id for p in contents}
    actual={p.id for p in recipe.parts}
    overlap=actual&assigned_contents
    missing=expected-actual
    extra=actual-expected
    if missing or extra or overlap:
        raise ValueError('Structural recipe must contain exactly the frozen structure/detail IDs. '
            f'missing={sorted(missing)}; unexpected={sorted(extra)}; '
            f'deferred_content_ids_used={sorted(overlap)}. Do not add duplicate contents under new IDs.')


def prune_unwired_proposals(outline,recipes,layout):
    """Remove inferred, unused geometry declarations, retaining requested edits.

    Inferred circulation quantities sometimes leak into the geometry parameter
    table even though the accepted spatial contract stores their actual values.
    Such a declaration is not an editable control. Preserve it in provenance,
    rather than invent a no-op geometric expression to satisfy parameter usage.
    Explicit edit checks still fail if their parameters are unwired.
    """
    referenced=set()
    values=[]
    for obj in outline.objects:values.extend(obj.dimensions)
    for placement in layout.placements:values.extend(placement.position)
    for recipe in recipes.values():
        for part in recipe.parts:
            values.extend((*part.size,*part.center,*[v for point in part.path for v in point]))
            if part.radius is not None:values.append(part.radius)
    for value in values:
        if not isinstance(value,str):continue
        try:tree=ast.parse(value.strip(),mode='eval')
        except SyntaxError:continue
        referenced.update(node.id for node in ast.walk(tree) if isinstance(node,ast.Name))
    requested={check.parameter for check in outline.edit_checks}
    removed={k:v for k,v in outline.parameters.items() if k not in referenced|requested}
    result=outline.model_copy(deep=True)
    result.parameters={k:v for k,v in outline.parameters.items() if k not in removed}
    if removed:
        result.assumptions.append('Inferred quantities without geometric wiring are provenance only, not editable geometry controls: '+json.dumps(removed,sort_keys=True)+'. Actual use clearances remain in the accepted spatial contract.')
    return result,removed


def assemble_plan(outline: Decomposition, recipes: dict[str,Recipe], layout: Layout, *, style_requirements=None, spatial=None,
                  reconstruction_only=False):
    diagnostics=Diagnostics('layout')
    spatial=spatial_with_layout(spatial,layout,outline.objects,outline.extent)
    ids=[o.id for o in outline.objects]
    if [p.id for p in layout.placements]!=ids:
        raise ValueError('Layout must contain exactly the frozen component IDs in the same order')
    components=[];relations=[]
    for obj,placement in zip(outline.objects,layout.placements):
        source=obj.acquisition.executable(outline.quality_target)
        components.append(ComponentPlan(id=obj.id,semantic=obj.semantic,category=obj.category,
            dimensions=obj.dimensions,position=placement.position,orientation_deg=(0,0,placement.yaw_deg),
            parent=obj.parent,support=obj.parent if obj.mount=='top' else None,
            attachment=obj.mount if obj.mount in {'top','bottom'} else 'free',collision_role=obj.collision_role,
            support_part=obj.support_part,
            surface_contact=obj.surface_contact,
            grounded=obj.grounded,
            access_required=obj.access_required,
            support_footprint=obj.support_footprint,
            rigidity=obj.rigidity,decomposition_reason=obj.decomposition_reason,
            required_features=obj.required_features,material_requirements=obj.material_requirements,
            interface_notes=obj.interface_notes,visual_importance=obj.visual_importance,source=source,
            parts=recipes[obj.id].parts if source.mode=='parametric' else [],
            assembly=recipes[obj.id].assembly if source.mode=='parametric' else None))
        if obj.parent:
            relations.append(RelationPlan(type='parent',from_id=obj.id,to_id=obj.parent))
            if obj.mount=='top':relations.append(RelationPlan(type='supported_by',from_id=obj.id,to_id=obj.parent))
    relations.extend(RelationPlan(type='clearance',from_id=c.a,to_id=c.b,clearance=c.minimum_m,note=c.reason)
                     for c in outline.clearances)
    edits=[]
    for parameter in outline.parameters:
        direct=set()
        for component in components:
            values=[*component.dimensions,*component.position]
            for part in component.parts:
                values.extend([*part.size,*part.center,*[v for point in part.path for v in point]])
                if part.radius is not None:values.append(part.radius)
            if any(isinstance(v,str) and parameter in {n.id for n in ast.walk(ast.parse(v.strip(),mode='eval')) if isinstance(n,ast.Name)}
                   for v in values):direct.add(component.id)
        if not direct:
            diagnostics.add('unused_parameter',f'Unused editable parameter: {parameter}',paths=[],expected='A real connection to geometry or placement.')
            continue
        affected=set(direct)
        while True:
            next_ids=affected|{c.id for c in components if c.parent in affected}
            if next_ids==affected:break
            affected=next_ids
        edits.append(EditPlan(parameter=parameter,affected_components=[i for i in ids if i in affected],
                              invariant_components=[i for i in ids if i not in affected],recomputation_scope='subtree'))
    diagnostics.raise_if_any()
    plan=PlannerScenePlan(intent=SceneIntent(scene_id=outline.scene_id,description=outline.description,
        style=outline.style,quality_target=outline.quality_target,extent=outline.extent),
        parameters=outline.parameters,components=components,relations=relations,edit_plans=edits,
        edit_checks=[c.model_dump() for c in outline.edit_checks],
        assumptions=outline.assumptions,style_requirements=style_requirements or [],style_bindings=outline.style_bindings,
        spatial=spatial)
    def collect(failure,scenario):
        report=failure_report(failure,'layout')
        for item in report['issues']:
            paths=[]
            for path in item.get('paths',[]):
                match=re.match(r'/components/(\d+)/(position|orientation_deg)',path)
                if match:
                    paths.append(f'/placements/{match.group(1)}/'+('position' if match.group(2)=='position' else 'yaw_deg'))
            diagnostics.issues.append({**item,'paths':paths,'scenario':scenario,
                'upstream_paths':item.get('paths',[])})
        diagnostics.blocked_checks.extend({**item,'scenario':scenario} for item in report.get('blocked_checks',[]))
    try:executable,_=to_general_plan(plan,check_layout=False,check_geometry=not reconstruction_only)
    except ValueError as failure:
        collect(failure,'baseline')
        executable=None
    from general_scene import edit_general
    for check in outline.edit_checks:
        changed=plan.model_copy(deep=True)
        changed.parameters[check.parameter]+=check.delta
        try:
            to_general_plan(changed,check_layout=False,check_geometry=not reconstruction_only)
            scenario_passed=True
        except ValueError as failure:
            collect(failure,check.parameter)
            scenario_passed=False
        if executable is None:
            diagnostics.block('edit_effect','Valid baseline required for effect comparison.',scenario=check.parameter)
        elif scenario_passed and not reconstruction_only:
            try:edit_general(executable,check.parameter,check.delta,check_layout=False)
            except ValueError as failure:collect(failure,check.parameter)
        else:
            diagnostics.block('edit_effect','The edited scenario must pass geometry/layout before invariant comparison.',scenario=check.parameter)
    diagnostics.raise_if_any()
    return plan


DECOMPOSE = '''Plan the semantic decomposition of the user's arbitrary 3D scene. Return JSON only.
Frozen style_requirements describe visible shape/material evidence, not optional names. Output style_bindings
covering each requirement exactly once with actual component IDs and an executable implementation.
For swept_path requirements also bind qualified component/part IDs in part_ids, referring ONLY to actual
visible internal_parts that the recipe must implement as sweep or lathe. Never list a mounting plate, substrate,
support board or other passive attachment surface as a curve part; if an external child supplies the feature,
part_ids may be empty or list only supplemental real curve parts. freeform_surface cannot be replaced by sweep.
Use available_capabilities as the authority. Editable sweeps (named curve presets: arcs, rings, helices,
spirals/volutes, C-scrolls, S-curves, cabriole legs, goosenecks, U-bends, waves, catenary swags, Bezier/smooth
paths; optional taper) and lathes (turned legs, balusters, vases, urns, knobs, finials, shades, pedestals)
are available; dense sculpted relief and arbitrary freeform mesh still require a separately
sourced rigid child (retrieve/auto/generate). Preserve aDSL for the editable structural root and regular contents.
One editable asset may contain these sourced descendants; it must not become one opaque generated mesh.
If previous_attempt_feedback contains a fresh visual rejection of a bound sweep for this same style feature,
treat that render as evidence that the current sweep representation is insufficient. Do not repeat the same
tube strategy, swap only its curve preset, or merely rename its part.Revisit the decomposition and route the missing
visible mass/profile to a coherent local rigid child with its own retrieval/auto/generate decision; keep the
editable frame and contents as the parametric root. A source promise in semantics or a style binding is not
an implementation: the child must enter the acquisition pipeline and pass reference, component, assembly
and scene visual gates. Use auto when the matching carved motif may already exist, then TRELLIS.1 only after
retrieval/reference QA shows a suitable source gap. The rigid child must be a real mounted subasset, not a
replacement for the whole editable bookcase.
Consolidate physically continuous decoration into coherent local subassets. Do not bind several
geometrically distinct shapes to one source target when reference search has rejected that composite:
replan only the failed external descendants into separately searchable, shape-coherent children, keeping
connected pieces together when one real object and one usable reference cover them. Do not fragment the
editable parametric root to work around an external-source failure.
When previous_attempt_feedback records an external retrieval/reference failure and lists
validated_parametric_components, preserve each listed component's ID, dimensions, parameters, internal-part
inventory, rigidity, edit behavior, and local requirements exactly. Change only the failed external subtree
and the scene-level relations needed for its new children. A source failure is not evidence that a validated
parametric recipe should be redesigned. Reuse its matching recipe checkpoint when its own specification is unchanged.
Do not silently relabel required carved/curved geometry as a box, nor substitute a style name for geometry.
Identify requirements, functional zones, and separately editable physical components. No fixed scene template.
If the user gives an exhaustive item list, says only/just, or explicitly excludes additions, carry that
closed inventory unchanged from request_contract and scene_design. Add only necessary room construction;
do not infer lifestyle props from age, room type or realism. Treat every unlisted freestanding object as
forbidden, even when common in that room type.
For a complete scene, reason from activities and occupancy before choosing the inventory: what happens,
how many people use it, which objects must work together, and where entry, operation and circulation fit.
Derive zones from ACTIVITIES, not from object categories [ZONE-2]. A functional zone answers one question:
which objects must be reachable together for one named human activity to happen. Two rules follow:
- Group by activity co-use, never by species. "All seating", "all lighting" and "all storage" are object
  categories, not activities; a reading lamp belongs with the reading seat if that is where it is used,
  even though another lamp in the same room serves a different activity.
- Every zone needs a stated activity it serves. A zone whose purpose is only a spatial label, a compass
  direction or an aesthetic grouping is not a functional zone and must not be emitted.
An object belongs in a zone because using it requires the others, not because it is the same kind of thing.
A single object may serve two activities only if it is genuinely shared; declare it once in the zone whose
activity dominates and note the secondary use in its interface_notes rather than duplicating membership.
These zones are a FROZEN OUTPUT: the spatial stage will spatialize this exact partition and is not free to
regroup it. Grouping the scene wrongly here cannot be repaired later by better coordinates, so choose the
partition on function and say why each zone exists.
Preserve user-stated room dimensions and openings; record any inferred boundary or entrance as an assumption.
Set access_required=true for roots needing a usable operating/approach area (seating, work, storage, fixtures).
Use false only when no dedicated human access is needed, with justification in interface_notes.
Use the common local frame consistently: front=-Y and rear/back=+Y before world yaw. Describe furniture
parts in that LOCAL frame; do not bake a proposed world-facing direction into the chair back geometry.
Only room-enclosing boundary geometry uses collision_role=shell. A separate fitted window assembly
(frame, sash, glazing, sill, shutter or blind) is collision_role=solid even though it sits in a wall opening;
the spatial stage must list its exact object ID in the matching window fitted_component_ids. Do not mark a
window fixture as shell to hide it from layout checks. Door frames/walls are collision_role=shell only when
their actual parts leave an opening. Solid furniture must remain solid. Use collision_role=floor_covering only
for a grounded, independent, low-profile rug or mat
with thickness <=0.08m; it is a walkable surface that may lie beneath furniture and must not block human routes.
Never use floor_covering for a raised platform. A shell label does not prove an opening; final measurements
inspect its wall/frame parts.
When request_contract.scene_use.enclosure=enclosed, include a real architectural shell root with floor,
walls and requested openings in internal_parts. Furniture-only output is incomplete even for prototype.
Use nonoverlapping structural panels around openings, preserving the declared scene extent and clear routes.
ENTRY ARCHETYPE IS FIXED FOR ENCLOSED ROOMS. Unless the user explicitly requests another type, every entrance
must be one standard single-leaf inward-swinging hinged door at 90 degrees: fixed jambs/frame and head remain
stationary in the architectural shell, one moving door panel is a separate transformable shell child, and one
separate handle is a child of that panel. Never model an entrance as only a bare opening, doorway aperture or
arch, and never omit the leaf just because the opening itself is passable. The entrance opening's
swing_leaf_ids MUST contain exactly one ID: the moving door panel. It MUST NOT contain the frame, jamb, head,
handle, hinge, architrave or any complete door assembly. Do not substitute a sliding, double, 180-degree, or
open-passage entrance unless that alternative is explicitly requested.
For an axis-aligned rectangular shell with at most one opening per wall, declare the wall IDs as full walls
or as the exact aperture cells: low-side, high-side, below-opening, and above-opening where each exists.
The structured shell compiler measures these cells from the frozen opening and binds them to the planned IDs;
it still creates wall cells automatically when a full-wall ID is used. Do not estimate wall-piece coordinates
or create arbitrary extra segments. Ground each wall base and any explicit door jambs on floor_slab with
support=floor_slab only for whole/low-side/high-side/below-opening wall cells that physically reach the floor.
An above-opening header does not rest on the floor: attach it to both adjacent full-height piers, or to the
nearest actual structural wall support. Never declare support=floor_slab for an elevated header. Choose the
beam minimum_ratio from its real opening width and section; the default may be lowered to 1.1 for a short,
deep doorway header when the actual geometry passes that proportion. A ceiling slab
attaches to a real top wall panel. Bind a door head to its left/right jambs, and bind the handle to the leaf
using attached_to. Classify jambs as vertical beams and the door head as a beam along the wall tangent. A scene
entrance with swing_leaf_ids needs a separately transformable leaf component: keep fixed jambs/head in the shell,
keep the moving panel as the only swing leaf, and parent the handle to that panel. Never freeze a complete
jamb-head-leaf assembly as the object that will be swung aside. Before returning, verify that every entrance has
exactly one swing leaf and that no hardware ID appears in swing_leaf_ids.
A separately fitted window object supplies its own frame; do not duplicate its frame in
the shell. This matches the structured compiler and preserves the real apertures. Do not replace the Agent's
plan with hardcoded geometry.
The occupied floor datum is WORLD Z=0. Use a 0.05m floor slab BELOW this datum with its TOP at zero,
not through the feet of grounded furniture. A shell root may extend below zero; its local bottom stays zero,
its declared height includes the slab thickness, and the shell placement is exactly Z=-0.05m. Room height
still limits its WORLD top. Walls start at the slab top and attach to it; openings start at the occupied floor.
An outdoor/open area needs no invented building. Do not replace the requested room with a studio ground.
Account for human circulation in extent and object dimensions. Individual scene instances need explicit IDs.
Keep route-width requirements as numeric spatial constraints, not unused global geometry parameters.
ROOM SIZE PREFERENCE [EXT-2]. When the user has NOT specified numeric room dimensions, choose a room extent
that comfortably accommodates the full furniture inventory plus all circulation, access and operating areas
with margin. Do NOT size the room to the minimum that fits the furniture; a tight room makes layout and
later repair fail repeatedly over spacing, route, and orientation conflicts. As a starting point:
- Estimate the total footprint of all furniture plus the planned access areas and main circulation routes.
- Add a comfortable margin around the composed group (typically 0.6-1.0 m of standing/walking space beyond
  the outermost furniture edges before the wall).
- For a typical living room with sofa + 2 chairs + coffee table + rug + media console, expect a room on
  the order of 6x5 m or larger; 5x4.6 m is already tight when two lounge chairs flank the coffee table.
- Only reduce below this if the user explicitly states a room dimension. When the user gives an extent,
  respect it exactly; instead of enlarging the room, revise non-essential Agent-inferred choices (extra
  chair, oversized rug, specific spacing) to fit, and clearly report any user requirement that cannot fit.
A spacious room is for usable margin and movement, not for adding more objects. Do not fill the extra
space with furniture the brief did not request.
Only introduce a global editable parameter when it controls actual geometry or a requested scene-layout edit.
Respect the user's requested asset boundary. A single requested composite asset has ONE parametric ROOT
object; if any geometry needs retrieval/generation, declare additional objects with parent=<root ID>,
rigidity=rigid, acquisition=retrieve/auto/generate and internal_parts=[]. These are local descendants of
the same editable asset. Put only aDSL-constructible boards, members and contents in root.internal_parts.
InternalPartIntent has NO acquisition field: rigidity=rigid alone does not retrieve/generate anything.
Bind freeform_surface style component_ids to those real external objects, never to root/internal part IDs.
Internal support and parameter edits are supported inside recipes; they do NOT require separate scene objects.
For a composite parametric asset, enumerate its required internal_parts: stable ID, semantic, rigidity,
and role=structure/content/detail. Include the user-requested contents (not just the frame). Parts that
resize under the requested edit are parametric; invariant contents are rigid. Recipe must retain these IDs.
Declare PART PHYSICAL SUPPORT explicitly and realistically [SUP-1]. These frozen supports become hard
constraints that no later stage may override, so an impossible structure declared here cannot be repaired
by better coordinates. support and attached_to are mutually exclusive; support itself accepts one ID or a
list of IDs:
- support="<id>" means "rests on this part's top surface". support=["<id_a>","<id_b>"] means a horizontal
  member that straddles two or more uprights and is carried by all of them. Use the list form whenever a
  member physically spans more than one support; do not pick one side and leave the other unsupported.
- attached_to= means "fastened to this part's side or face". A fastener is not a load-bearing rest and must
  not be used to stand a part in for a support.
- A wide top, shelf or rail resting on two side panels MUST list both side panels in support. Declaring a
  single side is a real structural error the validator measures and rejects, and the recipe stage cannot
  widen a support that this decomposition already fixed.
- Supported parts may overhang a little, but the combined top surfaces of the declared supports must carry
  the member along each axis. Listing two supports that do not actually reach under the member fails too.
- A part resting on a surface whose top is not horizontal, or on a sphere/sweep/lathe, has no valid rest: use
  attached_to for a real fastening or choose a flat structural member instead.
- Contents (books, dishes, small objects) rest on a real horizontal shelf, board or top by support=. They
  are not attached to the inside of a cabinet and they never straddle two supports.
- A lower shelf or under-table tray on a legged furniture piece rests on side rails or cross braces, NOT on
  the tops of the legs. The tops of the legs are occupied by the tabletop. A part with
  support=["leg_a","leg_b","leg_c","leg_d"] will be checked against the measured leg top height; if the
  tabletop already occupies that height the solver cannot produce a valid position. Instead, declare cross
  rails or lower stretcher members and use support=["rail_front","rail_back"] for a lower shelf. If no
  rails exist in the decomposition, add them; do not declare the shelf on the legs.
Every part except a root must have exactly one of support or attached_to, and every non-root part must
reach the assembly root through a chain of these relations. A part that only touches a non-root group is
disconnected and will be rejected.
Think like a furniture maker before choosing support targets [SUP-1b]. The load path must be structurally
coherent: weight travels from the top surface, through the members below it, to the floor. Common mistakes
that produce impossible assemblies:
- Table/desk: tabletop rests on leg tops (or on apron rail tops if an apron exists). Lower shelf rests on
  stretchers or lower cross-rails — not on leg tops which are already fully occupied by the tabletop.
  If no stretchers exist, add them before declaring the lower shelf.
- Cabinet/bookcase: horizontal shelves and the top panel rest on the two vertical side panels (list both).
  A back panel is attached_to the sides/top/base, never load-bearing. Drawers attach_to runners or sides.
- Chair: seat rests on the tops of the four legs. Back rail attaches_to the rear legs. A seat cushion rests
  on the seat, not attached to the back.
- Bed: mattress rests on the platform/slats, which rest on the side rails, which rest on the legs or are
  attached to the headboard/footboard frame.
- Sofa/armchair: cushions rest on the seat deck. Seat deck rests on the frame rails. Arms attached_to the
  frame, not resting on the seat cushion.
- Pendant light: canopy attached_to ceiling, cord/body hangs from canopy via attached_to. Never support=.
- Door: door leaf attached_to the frame at the hinge side. Never grounded or resting on floor slab.
  A door that grazes the floor by 2mm when closed is expected; that is not a support relationship.
Before declaring any support or attached_to, ask: does the named target have a real flat top/face at the
right height? If the answer requires guessing or hoping, the structure is wrong at decomposition and cannot
be fixed in recipe. Fix it here.
For regular panels and straight beams, freeze form={kind:panel|beam,axis:x|y|z,minimum_ratio:2}.
Panel axis is the THICKNESS NORMAL in the asset frame; beam axis is its LENGTH direction. This describes
functional orientation independently of later geometry: a vertical user-facing board has thickness normal Y,
a horizontal surface has normal Z. Do not swap thickness and visible face height/depth. Slender horizontal
rails are beams rather than panels when both cross-section dimensions are comparable. Apply these contracts
to the actual intended members, not to arbitrary details or curved/freeform geometry. All copies of the same
design must retain the same local functional form before placement yaw. Do not bake world yaw into form.
An internal_part form describes one physical panel or beam, never the envelope of a multi-member assembly.
For a frame made from rails and stiles, either give those members separate stable IDs and accurate local forms,
or describe one continuous sweepable shape with form=null. Never constrain a whole frame silhouette as one beam.
Use panel only for a genuinely thin board or plate; a flat face does not make a solid block, plinth, or pedestal a panel.
For an enclosed rectangular shell, an elevated lintel/header above a door/window aperture is one straight
horizontal beam along its wall tangent, never a full-height panel or a floor-supported part. A door-head beam
uses minimum_ratio=1.1 because the compiled band above a tall opening can be shorter than its cross-section;
the compiler and geometry checks verify its actual dimensions. A window header uses its measured ratio and may
retain 2 when its dimensions pass. The compiler binds
its beam form to the generated header cell. Only wall cells whose lower boundary is the occupied floor may use
support=floor_slab; elevated headers attach to their actual adjacent structural piers.
For contents resting on a surface specify support=<internal part ID>. It means vertical supported_by
the target's top face. For a part fastened to another internal part, specify attached_to=<internal part ID>;
set mount="bottom" when its TOP face fastens to the target's BOTTOM face. Side-face joints use attached_to
with mount omitted. Never encode a side/underside fastener as support. The compiler carries these frozen
interfaces into the recipe and supplies the matching parent/mount for declared top/bottom contacts.
Use request_contract.contents tags on each individually identified instance's requirement_tags.
Honor request_contract.asset_boundary and quantity limits before freezing the decomposition.
Plan visible richness at decomposition time: identify meaningful construction, finish, repeated-content
inventory, variation and negative space appropriate to THIS request. Describe each part's intended size,
appearance and group in its semantic text. Do not reduce a full scene or furnished asset to a test fixture.
Use the accepted scene design as a coherent inventory, not a checklist: several named design layers may
belong inside one editable root, and must not become duplicate scene objects just to satisfy relationship
coverage. Keep optional loose props sparse in compact rooms; include only the few that serve a stated
activity or a deliberate focal role, and express fine detail inside the parent assembly where possible.
Apply the brief-matched reusable construction experience appended for this request. Carry its relevant
visible construction, support and material intent into the exact owning component's required_features,
material_requirements and internal-part supports so the recipe builder can implement it on the first pass.
These priors do not add unrequested objects, numeric tolerances or extra layout constraints.
The reusable book recipe builds real covers/pages/spines. Use it for books, at the quantity justified by
the brief; it is available in any scene. Other complex geometry follows the source routing below.
For repeated contents enumerate stable individual IDs; choose enough instances to satisfy the brief.
Keep structural and detail members separate from contents so their interfaces can be verified first.
Record explicitly requested functional gaps in internal_clearances using the internal part IDs and
minimum_m. These requirements remain frozen and are copied into each recipe assembly by the compiler;
the recipe should focus on measured geometry and should not spend output tokens duplicating frozen
support/clearance relations. Geometry must still physically satisfy every copied relation.
Room clear height is NOT an internal assembly clearance between the shell floor and ceiling. Express
the room envelope in the shell dimensions and build the slab, walls and ceiling within that envelope;
floor/ceiling thickness reduces the clear interior height. Do not invent a numeric clear height or freeze
one as a shell clearance unless the user explicitly requested it. If the brief gives a clear-height target,
make the shell envelope tall enough for that target plus the actual slab and ceiling thicknesses, and state
the requested clear height in the shell's required_features/interface_notes.
Split an internal part into a scene object only when separately sourced geometry or explicitly separate
scene-level manipulation requires it. State internal parts and edit behavior in required_features/interface_notes.
Every scene object ID MUST appear in at least one requirements.component_ids entry. Requirements describe
both original user needs and accepted scene-design activities; a retained scene-design choice such as a
ceiling or task light belongs to its relevant lighting/activity requirement even when it was not named
verbatim in the user request. Keep design_bindings complete as a separate mapping. If an optional choice
has no useful requirement/activity, omit that choice and its object rather than leaving an orphan or
inventing a user requirement. Before returning, compare the union of requirement component_ids against the
full object ID list and include every missing ID. Every explicit requested object must also be covered.
Separate parts only when independent edits, motion, sourcing, support or assembly need it. Avoid a generated complete room, and
avoid duplicating a pot in both foliage and pot assets. External objects must be rigid; generated topology
is not internally parametric. Structural sizes can reference global numeric parameters using + - * /.
Each component's dimensions are the assembled outer bounds, including visible knobs, hinges, handles, trim,
feet, hardware, and other protrusions; do not size only the main body and leave details outside the envelope.
Regular structure may use parametric box/cylinder/sphere recipes. Use a parametric sweep part when a visible
curve or scroll can be represented by a centerline and a (possibly tapering) round section, and a lathe part
for any turned/round-profiled member; do not call crude boxes realistic.
Reserve external assets for dense relief, cavities, fabrics, or silhouettes that the available primitives cannot express.
Source strategy: parametric, retrieve, auto (retrieve then generate on failure), generate (custom appearance).
Direct generate requires a specific custom_appearance requested by this brief. Importance alone is not cause.
Both auto and generate MUST give reference_query or reference_image and reference_requirements.
The reason must explain why generation is required or why it is a conditional fallback; generation_reason
is optional extra detail, not a duplicate mandatory explanation. Auto/retrieve MUST supply retrieval_query.
References depict one complete isolated local object; state which shape/material/interface must be visible.
Write critical features, material requirements, interface notes and decomposition reasons for EVERY object.
When scene_design is supplied, implement the designed room, not a minimal furniture test. Preserve its
functional furniture types, requested material treatment, practical lighting and deliberate focal hierarchy.
Do not instantiate a separate object for every mentioned material, mood or furnishing category: wood and
wall texture belong on the architecture, and a requested lived-in quality may come from the existing books
and furniture. Use only the distinct objects needed for the activities and explicit visual requirements.
For an inferred extent, size the room around the primary group, human approaches and a comfortable clear
arrival path; do not pack the same inventory into a smaller generic room. If the user fixes the extent,
preserve the main-use anchors and omit optional accents that make circulation or the primary group feel tight.
design_bindings
maps EVERY final scene_design.choices ID to actual object IDs implementing it; internal details bind their owner.
Use the exact IDs in scene_design_choice_ids from context. Feasibility refinement may replace preliminary choice
IDs, so never use aliases from an earlier proposal. A plausible room feature is not automatically accepted: every
object must implement a final design choice or an original user requirement; otherwise remove it when allowed.
available_local_assets and matching_source_options are source metadata, not visual acceptance. Choose
an exact acquisition.asset_id when suitable (including polyhaven:<id> for remote candidates); do not
invent IDs. Preserve source proportions: use measured native_size_m as an aspect ratio, scaled uniformly
to human size, rather than planning an impossible envelope for a known mesh. front_rotation_deg corrects
the source front to local -Y before layout yaw; it is not world-facing orientation. No generated fallback
when brief says generation is unavailable. A complete sourced sofa/chair may stay rigid; the room and
structural furniture remain editable. Decorative textiles, pictures and wall trim need actual geometry
or materials rather than mentions in the description. Thin rugs and wall-mounted decor should use
appropriate shell/parent support semantics so valid layering is not misclassified as floor obstruction.
Use metres, centered scene X/Y, floor Z=0. dimensions is a local bottom-centered envelope. External meshes
fit uniformly, not stretch. mount=top puts a child bottom on the selected parent support top; no geometry or
positions in this stage. For a parametric parent with distinct structural surfaces, set support_part to the
exact frozen horizontal box-part ID (for example top_rail or tabletop), so placement does not use a decorative
highest point. Only named horizontal box faces are supported.
Repeated objects get distinct IDs. Top parents need a supporting surface matching the child base contact.
For a plant, lamp or overhanging object, support_footprint=[base_width,base_depth] specifies the bottom
contact, NOT canopy/shade extent. Include contact verification in interface_notes. Default null uses whole
envelope. Prefer a complete potted plant unless independent pot editing is needed; no duplicate pot.
Scene extent height must exceed every accumulated top-mounted support chain. Clearance minimum_m is numeric.
Use mount=bottom for a leg or bracket attached below its parent's bottom surface: compiler places child
TOP at parent BOTTOM. Set grounded=true for floor-contacting legs, bases or floor-supported objects.
Objects with mount=origin are free transform children; it does NOT imply any physical contact.
If the user requests edits, include edit_checks with parameter, realistic nonzero delta (e.g. 0.1m for
furniture height) and intent. Rigid object dimensions must stay unchanged. Include room for the edit.
Clearance endpoints are only real object IDs; scene boundaries are validated automatically by extent.
No coordinate_frame, orientation, relations, primitive parts or edit_plans fields: program creates those later.
Estimate adequate extent including all support heights. Keep parameters few and genuinely used. Entire plan
quality_target follows the brief; default realistic, never downgrade to avoid difficult features.
'''

RECIPE = '''Return JSON with parts for exactly the supplied parametric component. Do not change its intent.
Build only its frozen internal_parts. Separately retrieved or generated scene descendants never belong in
this local recipe; their IDs and shapes are not available as recipe parts.
Primitives: box/cylinder/sphere/sweep/lathe, plus reusable book subassemblies (covers, pages, spine, bands).
Each part needs id, primitive, size, center, rotation, color. Optional finish: matte/wood/metal.
For sweep give a positive radius and EITHER path (2..64 [x,y,z] OFFSETS FROM part.center) OR
curve={"preset":..., "plane":"xz"|"xy"|"yz", preset parameters} which is fitted to fill size minus radius
(see available_capabilities.sweep_curve_presets for presets and their parameters; the amplitude comes from
size; parameters may be
expressions). Optional radius_end tapers the tube linearly to that radius (0 = point); optional closed=true
joins the ends (ring and rounded_rect are already closed). All offsets plus radius must fit within +/-size/2.
size is an envelope, not a scaling operation. The compiler translates the path by center and rotates around
center. The result is overlapping capsule/frustum segments, not dense sculpted relief or a watertight mesh.
For lathe give profile={"preset":...} (see lathe_profile_presets; fitted to size) or profile={"points":
[[radius,z],...]} with z offsets from center, bottom to top; size x must equal size y; the axis is local +Z.
The curve must implement the requested shape. Do not use a box with a decorative name as a substitute.
Whole-object consistency: every repeated member of one design (legs, arms, stretchers, balusters, knobs,
brackets) is written ONCE and the others are copies: like=<that part id>, repeating its design fields
(primitive, size, rotation, color, finish, surface, form, path/curve/radius/radius_end/closed/profile) verbatim;
only id, center, semantic, parent and mount differ. For a left/right or front/back counterpart add
mirror="x" (mirror across the object's local YZ plane), "y" (XZ plane) or "xy": the compiler mirrors the
geometry and rotation, and the copy's center must be the source center with x and/or y negated. Mirrored
copies use no parent/fit_between. like always names the original, never another copy. When the object is
designed mirror symmetric, declare assembly.symmetry ("x", "y" or "xy"); every part then needs a mirror
partner in the compiled geometry. Share proportions through parameters so one edit changes all copies.
For book use book={"orientation":"upright" or "flat","palette_index":0..7,
"binding":"classic"/"cloth"/"paperback"/"leather"}; dimensions describe
its full rigid envelope. A flat book has its thickness along Z; an upright book along X.
Use one book part per volume instead of one colored box or dozens of detail primitives.
Book supports use its conservative rectangular envelope; rigid books must not resize on edits.
Materials may use surface={texture_id,roughness,grain_axis,texture_scale}; texture_id must come from
available_materials, never invent file paths. Null uses procedural finish. grain_axis is x/y/z.
Select a real surface map when available and appropriate to the brief. Vary bindings when appropriate.
Read each material's surface_family, appearance_tags and visual_description before selecting it; an ID is not a generic texture.
For a tatami/rush/straw mat face, use only a matching plant-fiber map. If the catalog has no suitable map,
leave surface null and keep the required straw color with a matte procedural finish; never substitute wood
or ordinary patterned cloth. When frozen materials call for muted/neutral/oatmeal tones, do not select a
red or high-contrast checkered/plaid map unless that pattern is explicitly requested.
When the frozen component description names an editable parameter from edit_checks, wire that parameter into
the actual affected size/center/path expression in this recipe. A semantic label is not a connection: do not
emit a numeric constant for an editable shelf/rail, do not use parameter*0 or parameter-parameter, and do not
leave the edit to the layout stage. Supported rigid contents must follow the moving parametric support through
parent/mount or a genuine parameter-dependent placement while retaining fixed dimensions.
Use w,d,h for the evaluated dimensions; global numeric parameters are also available. Use + - * / only.
Origin is bottom center: stay in [-w/2,w/2],[-d/2,d/2],[0,h], touch z=0. Local part rotations are degrees.
The shared coordinate frame is +Z up, -Y FUNCTIONAL FRONT, +Y BACK, +X right. Place rear members on
the back side and make access/opening features face the front. The book primitive's spine is on -Y.
Build actual specified visible structure, never a placeholder box for a complex object. Include legs/frames
and openings by positive solids where required. Sphere/cylinder size is full XYZ diameter/height envelope.
No sourcing, transforms of other objects or schema wrapper. Output {"parts":[...],"assembly":{...}}.
Build ONLY this named component. For a tabletop component 0.03m high, h=0.03, not the bench height.
Do not build its parent, siblings, or legs if they are separate objects. Local origin is ALWAYS at this
component's own bottom, never at its parent's or the world floor. Honor evaluated_size_m exactly.
For multi-part assets assembly is REQUIRED: roots are base parts at local Z=0; relations connect
EVERY part to those roots via supported_by or attached_to, with clearance for functional gaps.
Use relation fields kind, part, target, reason, minimum (clearance), max_penetration (attachment,
usually <=0.002m). These are measured constraints, not labels. Do not invent a joint across a gap.
Frozen internal_parts.support, attached_to and internal_clearances are compiled into assembly automatically.
Do not repeat or change their relation kinds/targets; the geometry must satisfy them. Specify other genuine
fastening joints only when no frozen interface already describes that contact.
Frozen internal_parts.form is also copied automatically and cannot be weakened by a recipe or repair.
Use the stated asset-frame panel thickness normal / beam length direction when choosing size and rotation.
A face contact does not prove functional orientation: distinguish a vertical broad face from a thin shelf.
Part semantic describes function, rigidity=rigid for contents/hardware that must not resize.
Part parent/mount=top or bottom drives placement from an internal support. For root parts center
Do not pass a functional check by degrading a part. A lower shelf, tray, drawer bottom, or stretcher that
must carry usable items has a real thickness (typically >=8mm for wood, >=3mm for metal sheet) and a real
position that preserves its function. Shrinking it to 1mm to satisfy a contact gap, raising it flush against
the top, or moving it so its clearance collapses, defeats the part's stated purpose. The geometry judge
measures the gap; a repair that thins or flattens a load-bearing part to pass that gap is not a fix. Choose
the declared support so the part rests where its function is real, and if no support at the authored height
exists, the decomposition must add a rail, stretcher, or bracket there — not a thinner shelf.
Human-scale dimensions are real constraints, not suggestions [DIM-1]. A shelf that holds books needs at
least 0.25m clear vertical space above it. A chair seat sits 0.42–0.48m above the floor. A dining tabletop
is 0.72–0.78m. A coffee table is 0.40–0.46m. A kitchen counter is 0.90–0.95m. A door is at least 2.0m tall.
A bookshelf interior bay is 0.30–0.40m tall. Drawers need at least 0.10m interior depth. A window sill
projects 0.05–0.20m beyond the wall. When a part's stated semantic implies one of these, its size and
position must preserve that usable dimension; a recipe that collapses the clearance to pass a contact
check has broken the part. The geometry judge does not measure usability headroom, so you must enforce
it here. If a frozen support forces a part below its functional minimum, the support is wrong, not the size.
is the geometry center; for mounted parts center is the offset from the parent surface, with z=0
for exact contact. Part offsets/rotations use ASSET axes (no inherited parent rotation).
top mounts require a matching supported_by relation. Frozen top/bottom interfaces set parent and mount
automatically; honor their local center.z=0 contact convention. All other parts still require verified joints.
Books on shelves follow their shelf, retain dimensions, and need clearance below the next shelf.
Build the contact/attachment graph first; a plausible outer silhouette does not verify interiors.
Prefer fit_between for unrotated parametric boards/beams bounded by other parts: each span has axis,
start part ID, start_face=min/max, end part ID, end_face=min/max and optional inset (normally 0).
The compiler measures those faces and SOLVES that dimension and center, overriding the nominal size/center
on that axis. Other axes still use size/center. No parent or rotations on fitted parts. Dependencies must
be acyclic. Example of the general rule: vertical members BETWEEN a bottom plate and top plate fit on Z
from bottom.max to top.min; an internal cross-member between sides fits X from left.max to right.min.
Use attached_to for a fastened side joint. supported_by means the ENTIRE child base rests on the target's
horizontal top; it does not describe a wide shelf fixed laterally to two narrow uprights.
An overhanging fixed moulding requires a verified attached_to face joint, not full-footprint supported_by.
Do not shrink a required overhang to satisfy the wrong joint type. Frozen content supports stay supported_by.
attached_to supports box face joints and a cylinder flat end cap fully inside a box face (including an
underside leg fastening). Curved lateral cylinder/sphere contact is not certified by envelope proximity.
Sweep-to-box, sweep-to-circular-cylinder, sweep-to-sweep and any lathe joint with a box, circular cylinder,
round sphere, sweep or lathe measure the real surfaces and reject gaps/penetration; a sweep or lathe cannot be
declared a flat supporting surface (use attached_to). Unsupported sweep-to-sphere and joints with
elliptical cylinders fail.
Do not add an overlapping full-width base through full-height sides: choose butt joints/face spans.
'''


def _component_recipe_instructions(brief, component_id):
    return prepend_experience(RECIPE,planning_experience_prompt(brief,component_id=component_id))

SPATIAL = '''Plan a frozen, measurable spatial-use contract for this complete scene. Return JSON only.
Use the decomposition's activities, requirements, functional zones, objects, dimensions and user brief.
This is category-independent spatial reasoning, not a furniture template. Do not change inventory, dimensions,
quality, sourcing, or requested room extent. Infer missing entrances/boundaries explicitly in assumptions.
HARD WORLD FRAME: the room floor center is XY=(0,0), +Z is up, and Z=0 is the occupied floor datum.
extent=(W,D,H) gives full lengths, so room XY bounds are [-W/2,+W/2] x [-D/2,+D/2]; it does NOT define
coordinates from (0,0) to (W,D). For extent=(4.2,4.2,2.8), all room XY bounds are [-2.1,+2.1].
All zone centers, opening offsets, open-space centers, route points, and experience-view positions/targets use
this same scene-centered world frame. Never add half the extent to a coordinate.
Opening wall/offset convention: x_min is x=-W/2 and its offset is the SIGNED world y coordinate;
x_max is x=+W/2 and offset is signed y; y_min is y=-D/2 and offset is signed x;
y_max is y=+D/2 and offset is signed x. Offset is not a distance from a wall corner. For width q,
the legal offset interval is [-T/2+q/2, +T/2-q/2], where T is the full tangent-axis extent.
Example: on a 4.2m-wide y_max wall, a 1.7m window at offset=+0.4 occupies x=[-0.45,+1.25]m;
offset=2.5 is outside the wall. Check each interval numerically before returning.
zones are axis-aligned, scene-centered XY rectangles in metres, with purpose and exact component_ids.
Every root belongs to exactly one primary zone. Zone bounds may overlap for shared activities. Include
space-only circulation zones with component_ids=[] if needed; never duplicate a root's primary membership.

FUNCTIONAL GROUPING VS SPATIAL GROUPING [ZONE-1]. decomposition.zones is the FUNCTIONAL partition: objects
that belong together because one human activity needs them together. Your task is to SPATIALIZE each
functional zone, not to regroup. Keep the partition identical:
- Each decomposition zone becomes exactly one spatial zone carrying the same purpose and the same members,
  plus any architecture needed to enclose it.
- Do not merge two functional zones into one rectangle and do not split one functional zone across two
  zones. Both break the activity the decomposition froze.
- A spatial zone with no functional counterpart is allowed ONLY as a circulation-only zone with
  component_ids=[].
- If a functional zone cannot be placed as a real rectangle inside the room, report the conflict naming the
  zone and the blocking anchor. Do not silently reshape the grouping to make packing easier: a zone
  rectangle that fits by regrouping is not a solution, it is a different scene.
- Density and adjacency inside a zone come from that zone's activity, not from what happens to pack well.
Whether two objects share a zone answers "do they serve one activity". Where that zone sits answers
"where the activity happens". These are two different questions and must not be collapsed.
Each spatial zone purpose states the ACTIVITY it serves, in the same terms the functional zone used; a
purpose that only labels a region ("left side", "near the window", "display area") does not explain why
those objects are together and is not acceptable. Before returning, self-check the partition: list every
decomposition zone, name its spatial counterpart, and confirm the member sets match. Any zone that has no
counterpart, or whose members changed, must be reported as a conflict rather than silently accepted.
Before using east/west/north/south, map each shell side to its world-boundary axis and sign from the shell's
interface notes/semantic and measured wall-part bounds. Apply that one mapping consistently to openings,
zone centers, placements and relations. Part IDs alone do not establish compass direction. If frozen prose
and geometry disagree, report the mismatch and follow measured shell geometry; never silently keep a
cardinal label while placing the group on the opposite side.
Reserve space for assigned objects AND use. All IDs are unique across zones, openings, access areas and routes.
openings locate entrances and relevant windows on extent boundaries (x_min/x_max/y_min/y_max); offset is
the coordinate along the other wall axis, width spans that axis. approach_depth reserves an interior rectangle.
Entrance sill_height=0. Windows reserve clearance only between sill_height and sill_height+height. For a window,
list the exact object ID of every separate fitted window assembly (frame, sash, glazing, sill, shutter or blind)
in that aperture as fitted_component_ids; those objects must be collision_role=solid, are exempt from that window's own
approach volume only, and must fit its width/height and stay within fixture_projection_m (default 0.3m) of the
room boundary. All other objects and routes still block it. Keep the object's frozen dimensions and align its
layout pose to the opening wall, offset and sill; never repair a world-placement mismatch by shifting its local
recipe parts out of the window opening. Fitted window components and swung door leaves are governed by that
opening's measured placement; they may stay semantically linked to an activity group, but do not constrain their
world bounds to the furniture zone rectangle. A room-center parent origin is not an opening position.
Every enclosed-room entrance is the fixed standard single-leaf hinged-door archetype: a passable clear opening
with fixed jambs/frame and head plus one separate moving leaf, opened inward to 90 degrees by default. Keep
that leaf in an actually open, swung-aside state beside a jamb, outside the aperture and its approach. A bare
opening, doorway aperture, arch or other entrance with no leaf is invalid unless the user explicitly requested
that alternative. Door front direction describes the leaf/swing, not a destination: do not add facing relations
from a door leaf to rugs or furnishings. Never list a door under fitted_component_ids. List exactly one ID, the
moving door panel, under that opening's swing_leaf_ids; never list its handle, frame, jamb, hinge or complete
assembly there. Do not add a scene-level attached_to relation from the shell-child door leaf to the shell-child
frame; the opening contract and parent tree express that hinge association. This typed
association is checked against the actual world-space wall plane and jamb interval, not merely its distance to
the room shell. Orient the leaf from its measured footprint: its long width must project along the wall normal,
with only its thin edge across the opening tangent. Account for the parent world yaw; yaw=0 is not inherently
edge-on. Preserve the door and frame inventory; resolve operation through their actual layout. swing_leaf_ids
may contain only moving leaf components, never a whole door assembly that includes stationary jambs/head.
If those share one component, request decomposition repair before positions or recipes are frozen.
An outdoor/open scene still needs an assumed entry boundary. Record this assumption instead of inventing walls.
access_areas reserve clear floor rectangles in the OWNER'S LOCAL XY frame (front=-Y before yaw). Their
center is relative to the owner's bottom center, size is full width/depth, and height is human headroom.
For every area declare side=front/back/left/right in the OWNER'S frame and along_face (normally 0).
The compiler computes its face-relative normal coordinate; center must not redefine that side.
front/back have width along local X and depth along local Y; left/right still use X/Y size order.
World east/west/north/south are NOT local front/left: layout yaw performs that conversion.
center accepts bounded arithmetic expressions using owner-local w,d,h and global parameters. Use face-relative
centers such as ["w/2+0.5",0] for a 1m-deep side access, so an editable object's operating area follows its edge.
Check requested edit_checks and reserve enough room in both baseline and edited states.
Provide an area for each access_required root. Include task-specific chair pullback, cabinet/fixture approach
and working space when needed. Do not place an access rectangle inside its owner's solid footprint.
An approach reserves space for a person performing the actual task; it does not automatically cover the
whole width and front of every piece of furniture. Choose a reachable approach side appropriate to use,
and allow shared activity areas. Check access depth against nearby functional distance limits: a full
front strip deeper than the maximum reachable gap to the facing object can make the design impossible.
Resolve that conflict in the approach location/composition, preserving plausible human body space and
all explicit user clearances. Passive fixtures with access_required=false do not require extra routes.
routes connect entrance IDs and access-area IDs with explicit world XY centerline points, width and headroom.
Every access area must be graph-connected to an entrance. Each endpoint point must lie inside its named area.
Width is the FULL corridor width, checked continuously, including bends; allow half the width to walls and
obstacles. Keep route centerlines inside the room, including at entrances. Do not route through furniture.
Distinguish main circulation from the short final approach to a seat or shared activity surface. Do not
automatically assign a main-aisle or wheelchair width to every gap inside a furniture group when the user
has not requested it. Justify inferred widths from actual human use. Numeric facts in
frozen_user_spatial_requirements are mandatory; additional widths in the decomposition are preliminary
design assumptions, which this spatial design may refine with a concrete ergonomic justification.
Before returning the contract, run a complete numeric top-view audit in the same centered frame: verify each
zone's four bounds, every opening's signed tangent interval, route centerline bounds including half-width,
opening approach endpoints, access-area owner-local coordinates, open-space bounds, and every experience-view
eye position against the equations above. Do not rely on later clipping or assume the compiler will translate
an invalid plan. Then run a complete occupancy thought experiment. Check every named
open-space volume against every root, every access rectangle against its owner and neighbours, every route
endpoint against its actual task area, and every directional relation against the selected yaw. Keep the main
entry-to-focal sightline and the brief's primary activity group readable; place secondary furnishings at
the edge of a group or in a secondary niche instead of letting them intrude into the principal conversation
ring. Use open_spaces sparingly for a distinct intentionally empty design feature. Routes already express
main circulation, and access_areas already express working clearance; do not duplicate these as overlapping
protected voids for each threshold, browsing lane, sightline or furniture gap. A visually open center is usually
the residual floor left by perimeter furniture placement, not a second large protected rectangle. A zone label
is not evidence of a good layout. Return all independent conflicts together so layout can solve one coherent arrangement.
Relations encode actual function: facing uses a's local front -Y toward b; distance is planar footprint-edge
distance with minimum_m and optional maximum_m. A zero minimum with maximum_m=null is valid and means no extra
pairwise gap; overlap and collision are checked separately. Every nonzero minimum and finite maximum becomes a
hard geometric constraint: add one only when the stated activity needs that exact clearance or reach. Do not turn
visual breathing room, a central open floor, zone separation or circulation into a positive distance between
furniture groups, or duplicate an access area/route as pairwise distance constraints. For chair-to-desk facing,
minimum_m is normally zero unless the user explicitly requires a gap; chair pull-back belongs in its access area,
not in a distance from the chair to unrelated furniture such as a tatami platform. Use null for a genuinely
unbounded maximum separation; do not invent an arbitrary upper bound for mere non-collision.
left_of/right_of/in_front_of/behind use b's yaw-local frame and minimum edge gap. At least one relation for multiple
roots. Give concrete reasons; names alone are not evidence.
Convert each directional vector through B's world yaw before planning coordinates: at yaw=90 degrees,
B-local left [-1,0] points along world [0,-1], not world [-1,0]. Compare rotated footprints along that world
vector and verify the signed gap; a relation label or zone membership does not prove A is on the requested side.
When scene_design includes typed group relationships, lower each one to a SpatialRelation with the exact
design_relation_id, matching kind, endpoint choices' actual component IDs and group relation binding. For
supported_by/attached_to, preserve the matching parent/interface relation. A distance relation never substitutes
for a directional relationship: a coffee table that belongs between seats and sofa must be explicitly on the
sofa's facing side, not merely near it. These are composition-level relations, not millimetre furniture-fit rules.
Route widths and functional clearances must match the brief and plausible use, never be reduced to evade a
blocked layout. State any accessibility assumptions. Preserve requested openings and numeric clearances.
This contract freezes before layout repairs. The layout stage will choose positions/yaws to satisfy it.
'''


LAYOUT = '''Return JSON {"placements":[{"id":...,"position":[x,y,z],"yaw_deg":...},...],
"route_points":{"route_id":[[x,y],...]},"access_centers":{"access_id":[local_x,local_y]}} only.
route_points and access_centers may be empty for single assets.
Use exactly the provided frozen IDs in order. Do not change dimensions, sources, parents or clearances.
Zones are a frozen FUNCTIONAL partition, not a packing device [ZONE-3]. Every root's zone membership and the
zone rectangle it is placed into come from the spatial contract and must both be satisfied. If a placement
would satisfy packing only by leaving its zone or by forcing another zone's contents out, the plan is
infeasible: report which zone and which activity it serves rather than silently trading one for the other.
A feasible packing is not a coherent composition; zones encode which objects must be usable together.
Root coordinates are scene-centered X/Y, floor z=0. Child positions are PARENT-LOCAL, not world positions.
An architectural shell whose local XY envelope matches the complete room extent is anchored at scene XY=(0,0);
never translate that root to (-W/2,-D/2). Keep its authored yaw and floor elevation. The compiler pins this
geometric datum before furniture solving; shell translation is not a furniture-placement choice.
Before writing a child placement, read its row in placement_frames. Never copy its desired world coordinates
into position: the compiler rotates the local XY offset by the parent's world yaw and adds the parent origin.
For a positioned window child, subtract the room-shell origin (including any slab-below-floor translation);
for a tabletop/bookshelf child, express the local offset from that parent and its declared support_part.
The returned position remains parent-local even when zones, openings, routes and relationships use world space.
For mount=top use local z=0 for contact: compiler adds the selected support top (support_part when set,
otherwise parent envelope top). Include rotated footprints.
For mount=bottom also use local z=0: compiler aligns the child top to the selected support bottom
(support_part when set, otherwise parent envelope bottom). Do not repeat
that offset. grounded=true components must land on world Z=0; derive root heights from parameters.
For surface_contact, select tangential coordinates in the PARENT frame and an actual opposing-face
yaw. The compiler owns the normal coordinate and resolves it from real parent and backplate geometry,
including any rotated parent. Do not try to attach to zone edges. At coarse stage the wall recipe
is unavailable: keep a plausible normal position; this is provisional, never contact evidence.
Keep inside extent, avoid solid intersections, respect all clearance distances and support footprints.
Only use a parameter in a placement when it genuinely moves the whole component. Internal editable dimensions
belong in the validated parametric component recipe; do not add a no-op expression such as parameter*0 or
parameter-parameter merely to satisfy an unused-parameter check. All objects are bottom-centered.
Use natural arrangement appropriate to the brief; preserve access and requested groupings.
When a frozen_spatial_contract is supplied, satisfy its zones, opening keepouts, owner-local access rectangles,
routes with full corridor width/headroom, and functional distance/facing/directional relations. Facing is
orientation only; do not infer a footprint gap from it. A separately requested gap is a distance relation. front is -Y
rotated by world yaw. Do not reduce widths or alter endpoint identity/access purposes to fit. Route centerline
points are layout variables: provide route_points to route around furniture, meeting the actual moved access
rectangles. A route graph may share its entrance aisle: preserve shared segments and branch only where each
activity needs access, rather than spreading parallel entrance-to-object paths across the room. Preserve route IDs,
widths, headroom, connectivity and all functional constraints. This first layout is
allowed to refine access_centers in owner-local coordinates, preserving owner, purpose, size and headroom.
Directional relations use B's yaw-local frame. Before placing A, rotate the relation vector by B world yaw
(e.g. B-local left [-1,0] at yaw 90 degrees becomes world [0,-1]); then compare the rotated object footprints
along that world vector. Never interpret local left/front as fixed world west/south, and do not accept a relation
from its label alone when the measured signed footprint gap puts A on the opposite side.
Treat this as a plausible first placement based on declared envelopes. The system records unresolved envelope
conflicts for Blender; measured exported geometry and the scene visual Judge decide whether access, scale,
support, grouping and the composition actually work. Do not claim a pass from this plan-stage estimate.
After placing all objects, inspect the top view as a composition: the entrance must open onto the principal
focal group, the primary activity group must remain readable from at least one experience view, and secondary
desks, lamps, plants, ottomans and side tables must stay in their named peripheral niches. Never fill a
protected open space merely because an object fits its zone; never let a large wall fixture, open door leaf,
or secondary activity become a foreground occluder for the main room. Resolve all strong directional,
open-space and route conflicts in the same candidate before returning it.
Use bounded arithmetic w,d,h expressions to follow an editable object's edge; position-only movement cannot
resolve an access area colliding with its own owner after a size edit. Check all frozen edit scenarios.
Place every entrance door leaf in an open position beside its jamb so its full footprint clears the aperture,
entry approach and connected route. Do not orient a door leaf toward a rug or furnishing; its open angle is
defined by the hinge at the jamb and the clear doorway, not by a furniture-facing relation.
an envelope-only feasibility gate before expensive recipe/acquisition work; it is not rendered geometry.
'''


class StagedScenePlanner:
    def __init__(self, model_profile, workspace, task_id, *, complete=None, max_repairs=None, resume_runtime=None,
                 replan_feedback=None,frozen_contract=None,allow_generation=True,replan_spatial=False):
        self.model_profile=Path(model_profile);self.workspace=Path(workspace);self.task_id=task_id
        self.complete=complete
        # Same budget as the harness handoff threshold: after this many repair
        # calls on one stage the run stops and the harness model takes over.
        self.max_repairs=5 if max_repairs is None else max(0,int(max_repairs))
        self.events=[]
        self.resume_runtime=Path(resume_runtime) if resume_runtime else None
        self.transport_calls=[]
        self.replan_feedback=replan_feedback
        self.frozen_contract=frozen_contract
        self.allow_generation=allow_generation
        self.replan_spatial=replan_spatial
        self.reuse_prior_stages=False
        if replan_spatial and not self.resume_runtime:
            raise ValueError('Spatial replanning requires a previous staged runtime')

    def preserved_stage(self,name):
        """Revalidate accepted same-brief work without resampling unchanged stages."""
        if not (self.replan_spatial or self.reuse_prior_stages):return None
        path=self.resume_runtime/(name+'_accepted.json')
        if name=='decomposition':
            path=self.resume_runtime/'frozen_decomposition.json'
        elif name=='scene_design':
            path=self.resume_runtime/'scene_design.json'
        else:
            path=self.resume_runtime/(name+'_accepted.json')
        if not path.is_file():return None
        if name=='decomposition':
            value=json.loads(path.read_text(encoding='utf-8'))['plan']
        else:
            value=json.loads(path.read_text(encoding='utf-8'))
        self.save('recovery_'+name+'_input.json',{'source':str(path),'candidate':value,
            'policy':'Preserved candidate must pass current validation; this is recovery, not a fresh prompt run.'})
        return value

    def preserved_recovery_request_contract(self,brief):
        """Reuse the frozen user contract when a visual rejection only reopens design."""
        if not self.replan_spatial or not self.resume_runtime:return None
        origin_path=self.resume_runtime.parent/'run_origin.json'
        origin=json.loads(origin_path.read_text(encoding='utf-8')) if origin_path.is_file() else {}
        if origin.get('brief')!=brief:
            raise ValueError('Spatial recovery cannot reuse a request contract from a different brief.')
        from spatial_recovery import load_recovery_request_contract
        contract=load_recovery_request_contract(self.resume_runtime)
        self.save('frozen_request_contract.json',contract.model_dump())
        self.events.append({'stage':'request_contract','status':'reused_and_revalidated',
            'source':str(self.resume_runtime/'frozen_request_contract.json'),
            'reuse_basis':'same_brief_frozen_user_contract'})
        self.save('stage_events.json',self.events)
        return contract

    def spatial_recovery_feedback(self):
        feedback=self.replan_feedback or {}
        return {key:feedback[key] for key in
            ('geometry','visual','error','observed_scene_feedback','previous_actual_geometry_directory',
             'detailed_planning_failure','replan_reason') if key in feedback}

    async def finalize_layout(self,outline,recipes,coarse,spatial,style_requirements,context,paths):
        if self.resume_runtime:
            try:
                origin=json.loads((self.resume_runtime.parent/'run_origin.json').read_text(encoding='utf-8'))
                original=Decomposition.model_validate(json.loads((self.resume_runtime/'frozen_decomposition.json').read_text(encoding='utf-8'))['plan'])
                revised=Decomposition.model_validate_json((self.resume_runtime/'current_mounting_outline.json').read_text(encoding='utf-8'))
                unchanged=copy.deepcopy(revised.model_dump(mode='json'))
                original_ids={o.id:o for o in original.objects}
                for item in unchanged['objects']:
                    item['surface_contact']=original_ids[item['id']].surface_contact.model_dump() if original_ids[item['id']].surface_contact else None
                if origin.get('brief')==context.get('brief') and unchanged==original.model_dump(mode='json') and original.model_dump(mode='json')==outline.model_dump(mode='json'):
                    outline=revised
                    failures=sorted(self.resume_runtime.glob('layout*_failure_*.json'),key=lambda p:p.stat().st_mtime,reverse=True)
                    seed=next((json.loads(p.read_text(encoding='utf-8')).get('candidate') for p in failures
                               if json.loads(p.read_text(encoding='utf-8')).get('candidate')),None)
                    if seed is not None and not layout_checkpoint_mismatches(seed,outline):coarse=Layout.model_validate(seed)
                    frozen=copy.deepcopy(context.get('frozen_decomposition',{}))
                    current={o.id:o for o in outline.objects}
                    for item in frozen.get('objects',[]):
                        item['surface_contact']=current[item['id']].surface_contact.model_dump() if current[item['id']].surface_contact else None
                    context={**context,'frozen_decomposition':frozen}
                    self.save('current_mounting_outline.json',outline.model_dump(mode='json'))
                    self.events.append({'stage':'detailed_layout_resume','status':'candidate_restored_pending_current_validation',
                        'source':str(self.resume_runtime),'rule':'Only Agent-selected contact changes restored; complete layout and edits still require validation.'})
            except (OSError,json.JSONDecodeError,ValueError,KeyError,TypeError):pass
        revision_index=0;seen=set()
        while True:
            stage='layout' if revision_index==0 else f'layout_interfaces_{revision_index}'
            try:
                plan=await self.stage(stage,Layout,prepend_experience(
                    LAYOUT,spatial_experience_prompt(context.get('brief'))),context,
                    lambda value:assemble_plan(outline,recipes,value,style_requirements=style_requirements,spatial=spatial),
                    paths,initial_candidate=coarse.model_dump(mode='json') if coarse else None,
                    prefer_matching_checkpoint=True)
                return plan,outline
            except MountingInterfaceRevisionRequired as failure:
                report=failure.measurements
                owners={i['component_ids'][0] for i in report['issues']
                        if i['code'] in {'mounting_interface_capacity','mounting_extent_conflict'} and i.get('component_ids')}
                state=digest({'contacts':{o.id:o.surface_contact.model_dump() for o in outline.objects
                                          if o.id in owners},'report':report})
                if state in seen:raise
                seen.add(state);revision_index+=1
                measured={}
                relevant=owners|{o.parent for o in outline.objects if o.id in owners}
                for obj in outline.objects:
                    if obj.id not in relevant or obj.id not in recipes:continue
                    recipe=recipes[obj.id]
                    local=SceneObject(id=obj.id,semantic=obj.semantic,size=obj.dimensions,
                        parts=[Part.model_validate(p.model_dump()) for p in recipe.parts],assembly=recipe.assembly)
                    evidence={}
                    compile_local_geometry(GeneralPlan(scene_id='interface_review',description=obj.semantic,
                        extent=outline.extent,parameters=outline.parameters,objects=[local]),local,evidence=evidence)
                    measured[obj.id]=evidence['parts']
                outline=await self.stage(f'mounting_interface_revision_{revision_index}',MountingInterfaceRevision,
                    'Revise inferred physical contact choices only for the supplied failed component IDs. '
                    'The selected face has a measured capacity or translation-domain conflict. '
                    'For a boundary conflict, moving inward loses support and outward crosses the scene boundary '
                    'at the recorded yaw. Choose an appropriate physical interface rather than repeating translations. '
                    'Choose actual existing child and parent parts/faces from measured geometry. '
                    'Use a genuine fastening or supporting interface, including a smaller existing mounting '
                    'member when a body overhangs. Keep original user requirements, all geometry, dimensions, '
                    'materials, parent objects, functional use and support coverage. Do not invent a contact '
                    'part, shrink geometry, weaken coverage or provide positions. Return contacts and reason.',
                    {'brief':context.get('brief'),'failed_component_ids':sorted(owners),
                     'response_schema':mounting_interface_response_schema(owners),
                     'scene_design':context.get('frozen_decomposition',{}).get('scene_design'),
                     'components':[o.model_dump(mode='json') for o in outline.objects if o.id in relevant],
                     'measured_interfaces':measured,'complete_failure_evidence':report},
                    lambda value:validate_mounting_interface_revision(value,outline,owners),replacement_paths)
                frozen=copy.deepcopy(context.get('frozen_decomposition',{}))
                by_id={o.id:o for o in outline.objects}
                for item in frozen.get('objects',[]):
                    if item['id'] in owners:item['surface_contact']=by_id[item['id']].surface_contact.model_dump()
                context={**context,'frozen_decomposition':frozen,
                         'previous_interface_failure_evidence':report}
                self.save('current_mounting_outline.json',outline.model_dump(mode='json'))

    async def component_recipe(self,outline,obj,brief,style_requirements=None,*,prior_candidate=None):
        """Build large assemblies in bounded semantic batches, never synthesize layout in code."""
        style_requirements=style_requirements or []
        reuse_candidate=prior_candidate
        def validate(recipe,component=obj):
            result=validate_recipe(outline,component,recipe,style_requirements)
            if component.collision_role=='shell' and getattr(self,'current_spatial',None):
                placement=next(p for p in self.current_coarse.placements if p.id==component.id)
                validate_architectural_openings(component,result,outline.extent,outline.parameters,
                    self.current_spatial.openings,placement)
            return result
        # Resume is explicit planning reuse. Prompt/capability prose can change
        # without changing the Agent's frozen target. Compare that target and
        # physical context, then run all current recipe/edit/opening validators.
        if self.resume_runtime and not self.replan_feedback:
            try:
                previous=Decomposition.model_validate(json.loads(
                    (self.resume_runtime/'frozen_decomposition.json').read_text(encoding='utf-8'))['plan'])
                origin=json.loads((self.resume_runtime.parent/'run_origin.json').read_text(encoding='utf-8'))
                previous_contract=RequestContract.model_validate_json(
                    (self.resume_runtime/'request_contract_accepted.json').read_text(encoding='utf-8'))
                identical=(previous.model_dump(mode='json')==outline.model_dump(mode='json')
                    and origin.get('brief')==brief
                    and [r.model_dump(mode='json') for r in previous_contract.style_requirements]
                        ==[r.model_dump(mode='json') for r in style_requirements])
                if identical and getattr(self,'current_spatial',None):
                    prior_spatial=json.loads((self.resume_runtime/'frozen_spatial_contract.json').read_text(encoding='utf-8'))['contract']
                    identical=SpatialContract.model_validate(prior_spatial).model_dump(mode='json')==self.current_spatial.model_dump(mode='json')
                if identical and getattr(self,'current_coarse',None):
                    prior_coarse=json.loads((self.resume_runtime/'coarse_layout_checkpoint.json').read_text(encoding='utf-8'))['candidate']
                    identical=Layout.model_validate(prior_coarse).model_dump(mode='json')==self.current_coarse.model_dump(mode='json')
                paths=[self.resume_runtime/f'recipe_{obj.id}_accepted.json']
                paths+=sorted(self.resume_runtime.glob(f'recipe_{obj.id}_contents_*_accepted.json'),reverse=True)
                saved_path=next((p for p in paths if p.is_file()),None)
                reused=validate(Recipe.model_validate_json(saved_path.read_text(encoding='utf-8'))) if identical and saved_path else None
            except (OSError,json.JSONDecodeError,ValueError,KeyError,TypeError):reused=None
            if reused is not None:
                candidate=reused.model_dump(mode='json')
                self.save('recipe_'+obj.id+'_accepted.json',candidate)
                self.save('recipe_'+obj.id+'_checkpoint.json',{'request_hash':None,'candidate':candidate,
                    'reuse_basis':'identical_frozen_target_current_validation','source':str(saved_path)})
                self.events.append({'stage':'recipe_'+obj.id,'status':'reused_and_revalidated',
                    'reuse_basis':'identical_frozen_target_current_validation','source':str(saved_path),
                    'hash':digest(candidate)})
                self.save('stage_events.json',self.events)
                print(f'[FineScene] recipe_{obj.id}: reused and revalidated',file=sys.stderr,flush=True)
                return reused
        # Source-only replans reuse a complete recipe that already passed
        # geometry, edit and assembly validation.  Re-running its semantic
        # content batches would both waste model calls and give the model an
        # opportunity to redesign a known-good editable root.
        preserved_ids={item.get('id') for item in
                       (self.replan_feedback or {}).get('validated_parametric_specs',[])
                       if isinstance(item,dict)}
        if self.resume_runtime and obj.id in preserved_ids:
            validated_plan=self.resume_runtime/'validated_plan.json'
            if validated_plan.is_file():
                try:
                    saved=json.loads(validated_plan.read_text(encoding='utf-8'))
                    saved_component=next(item for item in saved.get('components',[])
                                         if item.get('id')==obj.id)
                    reused=Recipe.model_validate({'parts':saved_component.get('parts',[]),
                                                  'assembly':saved_component.get('assembly')})
                    result=validate(reused)
                except (OSError,json.JSONDecodeError,StopIteration,ValueError,KeyError,TypeError):
                    if 'reused' in locals():reuse_candidate=reused.model_dump(mode='json')
                else:
                    record={'stage':'recipe_'+obj.id,'status':'reused_and_revalidated',
                            'reuse_basis':'validated_parametric_spec',
                            'source':str(validated_plan),'hash':digest(result.model_dump(mode='json'))}
                    self.events.append(record)
                    self.save('recipe_'+obj.id+'_reused.json',result.model_dump(mode='json'))
                    self.save('stage_events.json',self.events)
                    print(f'[FineScene] recipe_{obj.id}: reused and revalidated',file=sys.stderr,flush=True)
                    return result
        context=component_recipe_context(outline,obj,brief,style_requirements)
        recipe_instructions=_component_recipe_instructions(brief,obj.id)
        if obj.parent is None and getattr(self,'current_coarse',None):
            placement=next(p for p in self.current_coarse.placements if p.id==obj.id)
            dims=[scalar(v,outline.parameters) for v in obj.dimensions]
            params={**outline.parameters,**dict(zip(('w','d','h'),dims))}
            origin=[scalar(v,params) for v in placement.position]
            context['modeling_frame']={'space':'component_local_bottom_centered',
                'world_origin_m':origin,'world_yaw_deg':placement.yaw_deg,
                'local_envelope_bounds_m':[[-dims[0]/2,-dims[1]/2,0],[dims[0]/2,dims[1]/2,dims[2]]],
                'world_floor_z_in_component_frame_m':-origin[2],
                'instruction':'All recipe sizes/centers and assembly contacts are COMPONENT-LOCAL. '
                    'The world floor is the supplied local elevation; do not assume it is local Z=0. '
                    'The compiler applies this root transform only after local construction.'}
        rectangular_shell=(obj.collision_role=='shell' and getattr(self,'current_spatial',None)
            and len(outline.objects)>1
            and all(abs(scalar(obj.dimensions[i],outline.parameters)-outline.extent[i])<.001 for i in (0,1))
            and abs(next(p for p in self.current_coarse.placements if p.id==obj.id).yaw_deg)%360<1e-6)
        if rectangular_shell:
            from architectural_shell import shell_program_compatibility
            compatibility=shell_program_compatibility(obj,self.current_spatial,outline)
            self.save('architecture_backend_'+obj.id+'.json',compatibility)
            rectangular_shell=compatibility['supported']
        if rectangular_shell:
            from architectural_shell import ShellProgram,SHELL_PROMPT,compile_shell
            placement=next(p for p in self.current_coarse.placements if p.id==obj.id)
            shell_context={**context,'spatial_contract':self.current_spatial.model_dump(mode='json'),
                'shell_placement':placement.model_dump(mode='json'),
                'schema':ShellProgram.model_json_schema()}
            def validate_shell_program(program):
                validate(Recipe.model_validate(compile_shell(program,outline,obj,self.current_spatial,placement)))
                return program
            program=await self.stage('architecture_'+obj.id,ShellProgram,SHELL_PROMPT,shell_context,
                validate_shell_program,lambda data:['/'+key for key in data])
            result=validate(Recipe.model_validate(compile_shell(program,outline,obj,self.current_spatial,placement)))
            self.save('recipe_'+obj.id+'_accepted.json',result.model_dump(mode='json'))
            return result
        if obj.collision_role=='shell' and getattr(self,'current_spatial',None):
            context['architectural_spatial_contract']=self.current_spatial.model_dump(mode='json')
            context['architectural_layout']=self.current_coarse.model_dump(mode='json')
            context['architectural_rules']=(
                'The physical openings are mandatory voids. Use the structured shell backend when compatible: '
                'map each frozen wall ID to one whole wall or one actual aperture cell (low side, high side, '
                'below opening, above opening); the compiler computes its bounds. If the topology has multiple '
                'apertures on one wall, retain the semantic wall pieces in the general recipe path. '
                'Do not draw a frame over an unbroken solid wall. Do not use one sweep whose full AABB spans '
                'a window/door aperture: use separate box jambs and header. Preserve frozen frame ID on one member '
                'and add the other members with stable new IDs. Open entrance door leaves may be rotated or '
                'parked against an adjacent wall, never across the declared doorway or approach. '
                'North=+Y,south=-Y,east=+X,west=-X in the WORLD before local transformation. '
                'The shell placement translates its slab below world floor: convert all opening sill heights '
                'to this local frame. Wall-to-floor and ceiling joints must remain flush without overlap. '
                'Use GLOBAL dimension parameters, not numeric literals: unused declared parameters fail the final '
                'editable plan. A structural piece can add extra descendants, while all frozen intent IDs remain present.')
        paths=recipe_repair_paths
        if reuse_candidate is not None:
            result=await self.stage('recipe_'+obj.id,Recipe,recipe_instructions,context,
                lambda r:validate(r),paths,initial_candidate=reuse_candidate)
            self.save('recipe_'+obj.id+'_accepted.json',result.model_dump(mode='json'))
            return result
        contents=[p for p in obj.internal_parts if p.role=='content']
        structure=[p for p in obj.internal_parts if p.role!='content']
        if len(contents)<=8 or not structure:
            return await self.stage('recipe_'+obj.id,Recipe,recipe_instructions,context,
                                    lambda r:validate(r),paths)
        def scoped(ids):
            partial=obj.model_copy(deep=True)
            partial.internal_parts=[p for p in obj.internal_parts if p.id in ids]
            partial.internal_clearances=[c for c in obj.internal_clearances if {c.a,c.b}<=ids]
            return partial
        built_ids={p.id for p in structure}
        partial=scoped(built_ids)
        structural_context={**context,'component':partial.model_dump(mode='json'),
            'stage_contract':'Build structural/detail members only. Contents will be built in later dependency batches. Keep their intended volume available.',
            'deferred_content_intents':[p.model_dump() for p in contents]}
        def validate_structure(recipe):
            validate_structural_batch_ids(recipe,structure,contents)
            return validate(recipe,partial)
        recipe=await self.stage('recipe_'+obj.id+'_structure',Recipe,recipe_instructions,structural_context,
                                validate_structure,paths)
        pending=list(contents);index=0
        while pending:
            existing_ids={p.id for p in recipe.parts}
            batch=[];available=set(existing_ids)
            for part in pending:
                # A part with several supports becomes buildable only once every
                # one of them exists, otherwise its placement has no measured host.
                needed=part.support_targets()
                if all(target in available for target in needed):
                    batch.append(part);available.add(part.id)
                    if len(batch)==8:break
            if not batch:raise ValueError('Cyclic or unavailable internal content support dependency')
            batch_ids={p.id for p in batch};next_ids=built_ids|batch_ids
            partial=scoped(next_ids)
            local=SceneObject(id=obj.id,semantic=obj.semantic,size=obj.dimensions,
                parts=[Part.model_validate(p.model_dump()) for p in recipe.parts],assembly=recipe.assembly)
            evidence={}
            compile_local_geometry(GeneralPlan(scene_id='interfaces',description=obj.semantic,extent=outline.extent,
                parameters=outline.parameters,objects=[local]),local,evidence=evidence)
            def merge(chunk,previous=recipe,partial=partial,batch_ids=batch_ids):
                # Accepted checkpoints contain the validator's merged result.
                # Recover its new chunk only when every immutable prior part
                # and relation matches the freshly revalidated predecessor.
                if {p.id for p in chunk.parts}=={p.id for p in previous.parts}|batch_ids:
                    saved={p.id:p.model_dump(mode='json') for p in chunk.parts}
                    prefix=[r.model_dump(mode='json') for r in previous.assembly.relations]
                    if (chunk.assembly is None
                        or any(saved[p.id]!=p.model_dump(mode='json') for p in previous.parts)
                        or [r.model_dump(mode='json') for r in chunk.assembly.relations[:len(prefix)]]!=prefix):
                        raise ValueError('Merged batch checkpoint changed immutable previous geometry or relations')
                    chunk=Recipe(parts=[p for p in chunk.parts if p.id in batch_ids],
                        assembly=AssemblyContract(roots=chunk.assembly.roots,
                            relations=chunk.assembly.relations[len(prefix):]))
                if {p.id for p in chunk.parts}!=batch_ids:
                    raise ValueError('Chunk must contain exactly the assigned IDs; existing parts are immutable')
                if chunk.assembly is None:raise ValueError('Chunk must supply assembly relations')
                if set(chunk.assembly.roots)!=set(previous.assembly.roots):raise ValueError('Batch cannot change assembly roots')
                merged=Recipe(parts=previous.parts+chunk.parts,assembly=AssemblyContract(
                    roots=previous.assembly.roots,
                    relations=previous.assembly.relations+chunk.assembly.relations))
                return validate(merged,partial)
            recipe=await self.stage(f'recipe_{obj.id}_contents_{index:02d}',Recipe,
                recipe_instructions+'\nBATCH MODE: Output ONLY assigned_parts. Existing geometry is immutable. Use measured existing_interfaces for positions and clearances. '
                'assembly.roots repeats existing_roots; relations contains ONLY NEW relations involving assigned parts. '
                'Place complete support chains in batch order. Never shrink content to conceal a planning error.',
                {**context,'component':partial.model_dump(mode='json'),'assigned_parts':[p.model_dump() for p in batch],
                 'existing_interfaces':evidence['parts'],'existing_roots':recipe.assembly.roots},merge,paths)
            built_ids=next_ids;pending=[p for p in pending if p.id not in batch_ids];index+=1
        return validate(recipe)

    def save(self,name,data):
        self.workspace.mkdir(parents=True,exist_ok=True)
        (self.workspace/name).write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')

    def save_quality_metrics(self):
        validated=[event for event in self.events if event.get('status')=='validated']
        failed=[event for event in self.events if event.get('status')=='failed']
        first_candidates={event['stage']:event for event in self.events
                          if event.get('attempt')==0 and event.get('status') in {'validated','failed'}}
        first_passes=sum(event['status']=='validated' for event in first_candidates.values())
        self.save('planning_quality_metrics.json',{
            'validated_stages':len(validated),
            'first_candidates_checked':len(first_candidates),
            'first_candidate_passed_stages':first_passes,
            'first_candidate_pass_rate':first_passes/len(first_candidates) if first_candidates else None,
            'model_calls':len(self.transport_calls),
            'model_elapsed_seconds':round(sum(call['elapsed_seconds'] for call in self.transport_calls),3),
            'repair_calls':sum('_repair_' in call['stage'] for call in self.transport_calls),
            'diagnostic_batches':[{'stage':event['stage'],'attempt':event['attempt'],
                'issue_count':event.get('issue_count',0),'blocked_check_count':event.get('blocked_check_count',0)} for event in failed],
            'reused_stages':[event['stage'] for event in self.events if event.get('status')=='reused_and_revalidated'],
            'limits':'Per-stage candidate acceptance, not scene visual acceptance. Reused stages are excluded from first-candidate rates.'})

    async def ask(self,stage,instructions,payload):
        started=time.monotonic()
        self.save(stage+'_request.json',payload)
        (self.workspace/(stage+'_instructions.txt')).write_text(instructions,encoding='utf-8')
        generation_attempts=[]
        response_attempts=[]
        syntax_repair_attempts=[]
        reasoning_disabled_retry=False
        if not self.complete:
            from agents import Runner
            async def streamed(call_stage,call_instructions,call_payload,syntax_repair=False):
                settings=self.runtime.model_settings
                saved_extra=settings.extra_body;saved_cap=settings.max_tokens
                # Inventory and spatial lowering still require reasoning about
                # supports, proportions and dependencies. Keep low reasoning
                # with enough output room. Recipes also design physical joints
                # and coordinate frames; they are not mechanical transcription.
                structured_lowering=call_stage.startswith(('decomposition','spatial','coarse_layout','recipe_','layout','mounting_interface_')) and self.runtime.profile.model=='deepseek-flash'
                if call_stage.startswith(('coarse_layout','spatial_reconstruction_candidate')) and self.runtime.profile.model=='deepseek-flash':
                    settings.extra_body={'thinking':{'type':'disabled'}}
                    settings.max_tokens=max(saved_cap or 0,16384)
                elif structured_lowering:
                    settings.extra_body=({'thinking':{'type':'disabled'}} if reasoning_disabled_retry or syntax_repair else
                        {'thinking':{'type':'enabled'},'reasoning_effort':'low'})
                    if syntax_repair:settings.max_tokens=2048
                    elif call_stage.startswith('decomposition'):settings.max_tokens=max(saved_cap or 0,65536)
                    elif call_stage.startswith('spatial'):settings.max_tokens=max(saved_cap or 0,65536)
                    elif call_stage.startswith('coarse_layout'):settings.max_tokens=max(saved_cap or 0,16384)
                    elif call_stage.startswith('recipe_'):settings.max_tokens=max(saved_cap or 0,65536)
                    elif call_stage.startswith(('layout','mounting_interface_')):settings.max_tokens=max(saved_cap or 0,16384)
                try:
                    agent=self.runtime.agent(name='finescene-'+call_stage,instructions=call_instructions)
                    result=Runner.run_streamed(agent,json.dumps(call_payload,ensure_ascii=False),max_turns=1)
                    async for _ in result.stream_events():pass
                finally:
                    settings.extra_body=saved_extra;settings.max_tokens=saved_cap
                event=self.runtime.usage.record(stage=call_stage,agent=agent.name,result=result)
                raw=str(result.final_output or '')
                attempts=syntax_repair_attempts if syntax_repair else generation_attempts
                attempts.append({'attempt':len(attempts),'status':'empty' if not raw.strip() else 'received',
                    'response_chars':len(raw),'finish_reason':getattr(result,'finish_reason',None),
                    'output_tokens':event.output_tokens,'reasoning_tokens':event.reasoning_tokens})
                self.save(stage+('_syntax_repair_generation_attempts.json' if syntax_repair else '_generation_attempts.json'),attempts)
                return raw
        async def call_model(call_stage,call_instructions,call_payload,syntax_repair=False):
            if self.complete:return await self.complete(call_stage,call_instructions,call_payload)
            timeout=60 if syntax_repair else 300 if call_stage.startswith(('decomposition','spatial','recipe_')) else 180
            return await asyncio.wait_for(streamed(call_stage,call_instructions,call_payload,syntax_repair),timeout=timeout)
        async def call_once():return await call_model(stage,instructions,payload)
        async def invoke():
            nonlocal reasoning_disabled_retry
            response=await call_once()
            text=response if isinstance(response,str) else json.dumps(response,ensure_ascii=False)
            (self.workspace/(stage+f'_response_{len(response_attempts)}_raw.txt')).write_text(text,encoding='utf-8')
            def validate_json(raw):
                candidate=raw.strip()
                if candidate.startswith('```'):
                    candidate=candidate.split('\n',1)[1].rsplit('```',1)[0].strip()
                if not candidate:raise EmptyAgentOutput(f'{stage}: empty final output')
                return candidate,json.loads(candidate)
            try:
                validate_json(text)
            except (EmptyAgentOutput,json.JSONDecodeError) as first_failure:
                first_status='empty' if isinstance(first_failure,EmptyAgentOutput) else 'invalid_json'
                response_attempts.append({'attempt':len(response_attempts),'status':first_status,
                    'response_chars':len(text),'error':str(first_failure)[:300],'retry_reason':None})
            else:
                response_attempts.append({'attempt':len(response_attempts),'status':'valid_json',
                                          'response_chars':len(text),'retry_reason':None})
                return response
            live_runtime=getattr(self,'runtime',None)
            settings=getattr(live_runtime,'model_settings',None)
            profile=getattr(live_runtime,'profile',None)
            if settings is not None and getattr(profile,'model',None)=='deepseek-flash':
                old_extra_body=getattr(settings,'extra_body',None)
                old_reasoning_retry=reasoning_disabled_retry
                reasoning_disabled_retry=True
                settings.extra_body={'thinking':{'type':'disabled'}}
                response_attempts[-1]['retry_reason']='empty or invalid JSON; retry identical request with reasoning disabled and the original output cap'
                try:response=await call_once()
                finally:
                    settings.extra_body=old_extra_body
                    reasoning_disabled_retry=old_reasoning_retry
            else:
                response_attempts[-1]['retry_reason']='empty or invalid JSON; retry identical request once'
                response=await call_once()
            text=response if isinstance(response,str) else json.dumps(response,ensure_ascii=False)
            (self.workspace/(stage+f'_response_{len(response_attempts)}_raw.txt')).write_text(text,encoding='utf-8')
            try:
                validate_json(text)
            except (EmptyAgentOutput,json.JSONDecodeError) as second_failure:
                if isinstance(second_failure,json.JSONDecodeError):
                    candidate=text.strip()
                    if candidate.startswith('```'):
                        candidate=candidate.split('\n',1)[1].rsplit('```',1)[0].strip()
                    offset=second_failure.pos
                    excerpt=candidate[max(0,offset-120):min(len(candidate),offset+120)]
                    syntax_payload={'parser_error':str(second_failure),'offset':offset,
                        'candidate_length':len(candidate),'excerpt':excerpt}
                    syntax_instructions=('Repair JSON syntax only. Return one JSON object with integer start and end offsets, '
                        'a replacement string of at most 8 characters, and a short reason. Offsets are zero-based in the '
                        'provided candidate. Make the smallest punctuation/token correction within 8 characters of the '
                        'parser error; do not alter names, values, wording, IDs, or scene semantics. The full candidate is '
                        'not provided because only the shown local syntax fragment may change. Example shape: '
                        '{"start":12,"end":13,"replacement":",","reason":"Replace an invalid field separator."}')
                    self.save(stage+'_syntax_repair_request.json',{'instructions':syntax_instructions,'payload':syntax_payload})
                    (self.workspace/(stage+'_syntax_repair_instructions.txt')).write_text(syntax_instructions,encoding='utf-8')
                    started_patch=time.monotonic()
                    try:
                        patch_response=await call_model(stage+'_syntax_repair',syntax_instructions,syntax_payload,syntax_repair=True)
                        patch_text=patch_response if isinstance(patch_response,str) else json.dumps(patch_response,ensure_ascii=False)
                        (self.workspace/(stage+'_syntax_repair_response_raw.txt')).write_text(patch_text,encoding='utf-8')
                        patch_candidate=patch_text.strip()
                        if patch_candidate.startswith('```'):
                            patch_candidate=patch_candidate.split('\n',1)[1].rsplit('```',1)[0].strip()
                        patch=JSONSyntaxPatch.model_validate(json.loads(patch_candidate))
                        repaired=apply_json_syntax_patch(candidate,patch,second_failure)
                    except (ValueError,TypeError,KeyError,json.JSONDecodeError,EmptyAgentOutput) as patch_failure:
                        syntax_repair_attempts.append({'status':'failed','parser_error':str(second_failure),
                            'repair_error':str(patch_failure)[:300],'elapsed_seconds':round(time.monotonic()-started_patch,3)})
                        self.save(stage+'_syntax_repair_attempts.json',syntax_repair_attempts)
                        response_attempts.append({'attempt':len(response_attempts),'status':'invalid_json',
                            'response_chars':len(text),'error':str(second_failure)[:300],
                            'retry_reason':'parser-guided syntax repair failed'})
                        self.save(stage+'_response_attempts.json',response_attempts)
                        raise EmptyAgentOutput(f'{stage}: syntax-only JSON repair failed: {patch_failure}') from patch_failure
                    syntax_repair_attempts.append({'status':'repaired','start':patch.start,'end':patch.end,
                        'replacement':patch.replacement,'reason':patch.reason,
                        'elapsed_seconds':round(time.monotonic()-started_patch,3)})
                    self.save(stage+'_syntax_repair_attempts.json',syntax_repair_attempts)
                    (self.workspace/(stage+'_syntax_repaired_raw.txt')).write_text(repaired,encoding='utf-8')
                    response_attempts.append({'attempt':len(response_attempts),'status':'syntax_repaired',
                        'response_chars':len(repaired),'error':str(second_failure)[:300],
                        'retry_reason':'applied a parser-guided local syntax patch; full JSON revalidated'})
                    self.save(stage+'_response_attempts.json',response_attempts)
                    return repaired
                response_attempts.append({'attempt':len(response_attempts),
                    'status':'empty' if isinstance(second_failure,EmptyAgentOutput) else 'invalid_json',
                    'response_chars':len(text),'error':str(second_failure)[:300],'retry_reason':None})
                self.save(stage+'_response_attempts.json',response_attempts)
                raise EmptyAgentOutput(f'{stage}: model returned empty or invalid JSON twice: {second_failure}') from second_failure
            response_attempts.append({'attempt':len(response_attempts),'status':'valid_json',
                                      'response_chars':len(text),'retry_reason':None})
            return response
        from service_retry import call_with_service_retry
        service_attempts=[]
        def record(event):
            service_attempts.append(event)
            self.save(stage+'_service_attempts.json',service_attempts)
            print(f'[FineScene] {stage}: transient service failure after {event["elapsed_seconds"]}s '
                  f'({event["error_type"]}), same-request retry={event["same_request_retry"]}',file=sys.stderr,flush=True)
        try:
            raw=await call_with_service_retry(invoke,record)
        except Exception as failure:
            # Transport errors escape without naming the stage they came from;
            # the run report needs that name to offer a usable resume point.
            setattr(failure,'finescene_stage',stage)
            raise
        raw=raw if isinstance(raw,str) else json.dumps(raw,ensure_ascii=False)
        (self.workspace/(stage+'_raw.txt')).write_text(raw,encoding='utf-8')
        if generation_attempts:self.save(stage+'_generation_attempts.json',generation_attempts)
        self.save(stage+'_response_attempts.json',response_attempts)
        self.transport_calls.append({'stage':stage,'elapsed_seconds':round(time.monotonic()-started,3),
                                     'generation_attempts':len(generation_attempts) or 1,
                                     'syntax_repair_calls':len(syntax_repair_attempts),'response_chars':len(raw)})
        self.save('transport_calls.json',self.transport_calls)
        text=raw.strip()
        if text.startswith('```'):text=text.split('\n',1)[1].rsplit('```',1)[0].strip()
        return json.loads(text)

    async def stage(self,name,schema,instructions,context,validate,allowed=None,*,initial_candidate=None,
                    prefer_matching_checkpoint=False):
        try:
            inspect.signature(validate).bind(None)
        except (TypeError,ValueError) as failure:
            raise TypeError(f'{name}: validator must accept one parsed candidate') from failure
        started=time.monotonic()
        cache_key=digest({'schema':schema.model_json_schema(),'instructions':instructions,'context':context,
                          'profile':str(self.model_profile.resolve()),
                          'profile_hash':hashlib.sha256(self.model_profile.read_bytes()).hexdigest() if self.model_profile.is_file() else None})
        candidate=None;measurements={}
        if self.resume_runtime and (initial_candidate is None or prefer_matching_checkpoint):
            checkpoint=self.resume_runtime/(name+'_checkpoint.json')
            if not checkpoint.is_file() and initial_candidate is None:
                failures=sorted(self.resume_runtime.glob(name+'_failure_*.json'),key=lambda p:int(p.stem.rsplit('_',1)[1]),reverse=True)
                checkpoint=next((p for p in failures if json.loads(p.read_text(encoding='utf-8')).get('request_hash')==cache_key),checkpoint)
            if checkpoint.is_file():
                saved=json.loads(checkpoint.read_text(encoding='utf-8'))
                component=context.get('component',{})
                frozen_ids={item.get('id') for item in
                            (self.replan_feedback or {}).get('validated_parametric_specs',[])
                            if isinstance(item,dict)}
                safe_recipe_reuse=(name.startswith('recipe_') and
                                   component.get('id') in frozen_ids and
                                   component.get('acquisition',{}).get('strategy')=='parametric')
                if saved.get('request_hash')==cache_key or (safe_recipe_reuse and initial_candidate is None):
                    try:result=validate(schema.model_validate(saved['candidate'],context={'defer_decomposition_semantics':name=='decomposition'}))
                    except (ValueError,KeyError) as failure:
                        candidate=saved['candidate'];error=str(failure);measurements=failure_report(failure,name)
                        self.events.append({'stage':name,'status':'checkpoint_needs_repair','error':error})
                    else:
                        if isinstance(result,schema):saved['candidate']=result.model_dump(mode='json',by_alias=True)
                        self.save(name+'_checkpoint.json',saved)
                        self.save(name+'_accepted.json',saved['candidate'])
                        self.events.append({'stage':name,'status':'reused_and_revalidated','source':str(checkpoint),
                            'reuse_basis':'validated_parametric_spec' if safe_recipe_reuse and saved.get('request_hash')!=cache_key else 'matching_request',
                            'hash':digest(saved['candidate'])})
                        self.save('stage_events.json',self.events)
                        print(f'[FineScene] {name}: reused and revalidated',file=sys.stderr,flush=True)
                        return result
        candidate_report=measurements
        failed_states=set()
        diagnostic_signatures=set()
        spatial_conflict_history=[]
        stop_reason='repeated_failed_state'
        last_failure=None
        previous_issue_families=None
        attempt=0
        repair_calls=0
        while True:
            before=copy.deepcopy(candidate)
            phase='candidate_validation'
            repeated_failed_state=False
            try:
                if isinstance(candidate,dict) and allowed is not None:
                    if repair_calls>=self.max_repairs:
                        self.events.append({'stage':name,'status':'stopped','reason':'repair_call_limit',
                            'repair_calls':repair_calls,'accepted':False})
                        raise PlanningRepairLimit(name,self.max_repairs,measurements)
                    repair_calls+=1
                    phase='repair_application'
                    paths=allowed(candidate)
                    scalar_schema_codes={'string_too_long','string_too_short','string_pattern_mismatch',
                        'literal_error','string_type','int_type','float_type','greater_than','less_than',
                        'greater_than_equal','less_than_equal'}
                    issues=measurements.get('issues',[])
                    if issues and all(issue.get('code') in scalar_schema_codes for issue in issues):
                        exact={p for issue in issues for p in issue.get('paths',[])}
                        if exact and exact<=set(paths):paths=[p for p in paths if p in exact]
                    inventory_rule=local_repair_stage_guidance(name,context.get('brief',''))
                    diagnosis=' '.join(dict.fromkeys(filter(None,(
                        _repair_guidance(name,item.get('code','')+' '+item.get('evidence',''))
                        for item in measurements.get('issues',[]))))) or _repair_guidance(name,error)
                    removable_paths=[]
                    if name=='decomposition':
                        removable_paths=[path for issue in measurements.get('issues',[])
                                         if issue.get('code')=='unbound_design_object' for path in issue.get('paths',[]) if path in paths]
                    removable_paths.extend(path for issue in measurements.get('issues',[])
                        if issue.get('code')=='door_facing_relation' for path in issue.get('paths',[]) if path in paths)
                    if name=='access_faces':
                        removable_paths.extend(path for issue in measurements.get('issues',[])
                            if issue.get('code') in {'extra_access_side','extra_access_reason'}
                            for path in issue.get('paths',[]) if path in paths)
                    if hasattr(last_failure,'errors'):
                        for validation_error in last_failure.errors():
                            if validation_error.get('type')!='extra_forbidden':continue
                            pointer=''.join('/'+str(part).replace('~','~0').replace('/','~1')
                                            for part in validation_error.get('loc',()))
                            if pointer in paths:removable_paths.append(pointer)
                    model_diagnostics=prompt_evidence(measurements)
                    model_measurements={key:value for key,value in measurements.items()
                                        if key not in {'issues','blocked_checks'}}
                    repair_payload={'schema':LocalRepair.model_json_schema(),
                         'candidate_schema':context.get('response_schema',schema.model_json_schema()),
                         'candidate':candidate,'errors':error,'measurement_evidence':model_measurements,
                         'diagnostic_report':repair_report(model_diagnostics,paths),
                         'allowed_paths':paths,'removable_paths':removable_paths,
                         'frozen_context':context,'construction_rules':instructions}
                    if name=='spatial_reconstruction_candidate':
                        recovery_geometry=context.get('fixed_geometry',{})
                        recovery_context={
                            'brief':context.get('brief'),
                            'frozen_room':{key:recovery_geometry.get(key) for key in
                                ('extent','parameters','authority')},
                            'frozen_components':[{key:obj.get(key) for key in
                                ('id','semantic','category','dimensions','parent','mount','support_part',
                                 'grounded','collision_role','acquisition','requirement_tags')}
                                for obj in recovery_geometry.get('objects',[])],
                            'design_bindings':context.get('design_bindings'),
                            'existing_design':{
                                'room_identity':context.get('existing_design',{}).get('room_identity'),
                                'choices':context.get('existing_design',{}).get('choices')},
                            'frozen_openings':context.get('existing_apertures'),
                            'original_user_requirements':context.get('original_user_requirements'),
                            'placement_frames':context.get('placement_frames'),
                            'parametric_shell_mounting_hosts':context.get('parametric_shell_mounting_hosts'),
                            'component_part_ids':context.get('component_part_ids'),
                            'room_facing_wall_faces':context.get('room_facing_wall_faces'),
                            'previous_actual_feedback':context.get('measured_failure_evidence'),
                        }
                        repair_payload={'schema':LocalRepair.model_json_schema(),
                            'candidate_schema':schema.model_json_schema(),
                            'candidate':candidate,'errors':error,
                            'diagnostic_report':repair_report(model_diagnostics,paths),
                            'allowed_paths':paths,'removable_paths':removable_paths,
                            'recovery_context':recovery_context,
                            'repair_rules':('Patch only authorized paths. Preserve every component ID, frozen geometry, acquisition/source, '
                                'materials, requirements and aperture dimensions. Return one coordinated patch for all independent '
                                'diagnostics. Layout coordinates for children are parent-local; top/bottom children use local Z=0. '
                                'Window fitted fixtures are verified against the opening, not a single wall contact. For other wall '
                                'fixtures use measured room_facing_wall_faces and real parent part IDs; never infer wall side from its '
                                'name. Use numerical_solver.orientation_proposals and all independent_conflict_groups as measured '
                                'evidence; resolve the whole conflict set by revising poses and, when current relationships make it '
                                'infeasible, the inferred spatial design together. Do not merely move one component to the next '
                                'reported conflict. Keep the room coherent with valid entrance routes, activity-group facing, zones '
                                'and protected negative space. Route coordinates belong ONLY in /spatial/routes/<index>/points; '
                                'access centers belong in /spatial/access_areas/<index>/center. The layout contains placements only. '
                                'Intersect the legal centerline bounds with the entrance approach interval when placing endpoints. '
                                'For window-opening failures check orientation and dimensions before using a position suggestion; '
                                'a translation cannot correct an incompatible yaw or size. For directional failures, follow expected_a_direction_world_xy and '
                                'fix the relation orientation/pose, not by overlapping the objects. Return exact JSON-pointer replacements only.')}
                    proposal=await self.ask(f'{name}_repair_{attempt}',
                        'Repair only fields equal to or nested under the explicitly allowed JSON-pointer scopes; '
                        'never modify an ancestor of an allowed pointer. Return replacements, not the whole document. '
                        'When returning a mapping parent and one of its descendants, treat the parent value as a partial '
                        'update and preserve every omitted sibling key. '
                        'Each replacements item may contain only path, value, and optional operation. If changing two '
                        'root mappings such as /route_points and /access_centers, return separate replacement items. '
                        'Only exact removable_paths may use operation="remove" (unsupported fields or '
                        'explicitly unplanned components); do not replace them with null. Otherwise use operation="replace". '
                        'Preserve required content and frozen constraints. '
                        +inventory_rule+diagnosis+' Address ALL independent issues together using the full structured '
                        'diagnostic_report. Do not fix only the first error. Coordinate dependent geometry and expressions '
                        'in the same patch. Do not remove requirements to pass validation.',
                        repair_payload)
                    repair=LocalRepair.model_validate(
                        normalize_local_repair_proposal(proposal,candidate,paths,
                                                        removable_paths=removable_paths))
                    candidate=apply_local_repair(candidate,repair,paths,removable_paths=removable_paths)
                    self.events.append({'stage':name,'attempt':attempt,'paths':[r.path for r in repair.replacements],
                                        'before_hash':digest(before),'after_hash':digest(candidate),'reason':repair.reason})
                else:
                    if attempt == 0 and initial_candidate is not None:
                        candidate=copy.deepcopy(initial_candidate)
                    else:
                        candidate=await self.ask(f'{name}_{attempt}',instructions,
                            {'schema':context.get('response_schema',context.get('placement_schema',schema.model_json_schema())),'context':context,
                             'previous_errors':error if attempt else None,'previous_candidate':candidate})
                parsed=schema.model_validate(candidate,context={'defer_decomposition_semantics':name=='decomposition'})
                phase='candidate_validation'
                # Expose EVERY schema default as an exact repair pointer before
                # semantic checks, including omitted tags, forms and route maps.
                # Otherwise a missing tag forces replacement of a whole inventory.
                candidate=parsed.model_dump(mode='json',by_alias=True)
                result=validate(parsed)
                if isinstance(result,schema):candidate=result.model_dump(mode='json',by_alias=True)
                self.save(name+'_accepted.json',candidate)
                self.save(name+'_checkpoint.json',{'request_hash':cache_key,'candidate':candidate})
                self.events.append({'stage':name,'attempt':attempt,'status':'validated','hash':digest(candidate),
                                    'first_candidate_passed':attempt==0,
                                    'elapsed_seconds':round(time.monotonic()-started,3)})
                print(f'[FineScene] {name}: validated after {attempt} repair(s)',file=sys.stderr,flush=True)
                return result
            except (ValueError,KeyError,IndexError,ZeroDivisionError,SyntaxError) as failure:
                last_failure=failure
                error=str(failure)
                measurements=failure_report(failure,name)
                if phase=='repair_application' and candidate==before and candidate_report:
                    patch_issues=[{**item,'code':'repair_patch','evidence':'Repair patch rejected: '+item['evidence']}
                                  for item in measurements['issues']]
                    measurements={**candidate_report,'issues':candidate_report['issues']+patch_issues,
                                  'repair_patch_rejected':True}
                else:
                    candidate_report=measurements
                self.save(f'{name}_failure_{attempt}.json',{'request_hash':cache_key,'error':error,'candidate':candidate,'measurements':measurements})
                self.events.append({'stage':name,'attempt':attempt,'status':'failed','error':error,
                                    'issue_count':len(measurements['issues']),
                                    'blocked_check_count':len(measurements.get('blocked_checks',[])),
                                    'issue_ids':[i['id'] for i in measurements['issues']]})
                print(f'[FineScene] {name}: validation failed, attempt {attempt}',file=sys.stderr,flush=True)
                issue_families=diagnostic_families(measurements)
                if previous_issue_families is None:
                    self.events[-1].update({'repair_progress':'initial_failure_batch',
                                            'issue_families':sorted(issue_families)})
                elif measurements.get('repair_patch_rejected'):
                    self.events[-1].update({'repair_progress':'repair_patch_rejected',
                        'issue_families':sorted(issue_families)})
                else:
                    resolved=resolved_diagnostic_families(previous_issue_families,issue_families)
                    if resolved:
                        self.events[-1].update({'repair_progress':'resolved_issue_families',
                            'resolved_issue_families':sorted(resolved),
                            'remaining_issue_families':sorted(issue_families)})
                    else:
                        stop_reason='repeated failed state: same issue families without resolution'
                        repeated_failed_state=True
                        self.events[-1].update({'repair_progress':'no_issue_family_resolved',
                            'issue_families':sorted(issue_families),
                            'stop_reason':stop_reason})
                previous_issue_families=issue_families
                decomposition_issues=[item for item in measurements['issues']
                    if item.get('repair_target')=='decomposition' or
                    (isinstance(item.get('measured'),dict) and
                     item['measured'].get('repair_target')=='decomposition')]
                if name!='decomposition' and decomposition_issues:
                    self.events[-1].update({'escalation':'decomposition_revision_required',
                        'escalated_issue_ids':[item['id'] for item in decomposition_issues]})
                    self.save('stage_events.json',self.events)
                    self.save_quality_metrics()
                    raise DetailedPlanningRevisionRequired(measurements) from failure
                if isinstance(failure,SpatialChoicesInfeasible):
                    solver=measurements.get('numerical_solver') or {}
                    if (solver.get('status') in {'infeasible_current_choices','routing_requires_revision'}
                        and solver.get('orientation_proposals')):
                        conflict_families=spatial_conflict_families(solver,context.get('frozen_decomposition'))
                        self.events[-1].update({'escalation':'joint_spatial_revision_required',
                            'spatial_conflict_families':conflict_families,
                            'orientation_proposals':solver.get('orientation_proposals',{})})
                        self.save('stage_events.json',self.events)
                        self.save_quality_metrics()
                        raise SpatialChoicesInfeasible(measurements) from failure
                if name.startswith('layout') and any(i['code'] in {'mounting_interface_capacity','mounting_extent_conflict'}
                                                       for i in measurements['issues']):
                    raise MountingInterfaceRevisionRequired(measurements) from failure
                if name.startswith('layout') and any(
                        isinstance(i.get('measured'),dict) and i['measured'].get('repair_target')=='decomposition'
                        for i in measurements['issues']):
                    raise DetailedPlanningRevisionRequired(measurements) from failure
                if isinstance(failure,SpatialChoicesInfeasible):
                    # Let layout fix changed conflicts; escalate repeated spatial groups,
                    # even when a co-supported child or edit scenario changes in the core.
                    solver=measurements.get('numerical_solver') or {}
                    conflict_families=spatial_conflict_families(solver,context.get('frozen_decomposition'))
                    signature=digest({'status':solver.get('status'),'conflict_families':conflict_families})
                    measurements={**measurements,'numerical_solver':{
                        **solver,'conflict_families':conflict_families,'conflict_signature':signature}}
                    self.save(f'{name}_failure_{attempt}.json',{'request_hash':cache_key,'error':error,
                        'candidate':candidate,'measurements':measurements})
                    self.events[-1].update({'spatial_conflict_signature':signature,
                                            'spatial_conflict_families':conflict_families})
                    repeated=repeated_spatial_conflict(conflict_families,spatial_conflict_history)
                    spatial_conflict_history.append(conflict_families)
                    if repeated:
                        support_issues=support_group_revision_issues(conflict_families,
                            context.get('frozen_decomposition'))
                        if support_issues:
                            measurements={**measurements,'issues':[
                                *measurements.get('issues',[]),*support_issues]}
                            self.save(f'{name}_failure_{attempt}.json',{'request_hash':cache_key,'error':error,
                                'candidate':candidate,'measurements':measurements})
                            self.events[-1].update({'status':'decomposition_revision_required',
                                'support_conflicts':[issue['constraint_id'] for issue in support_issues]})
                            raise DetailedPlanningRevisionRequired(measurements) from failure
                        raise SpatialChoicesInfeasible(measurements) from failure
                issue_fields=('code','component_ids','constraint_id','paths','scenario',
                              'evidence','measured','expected')
                diagnostic_batch=[{key:item.get(key) for key in issue_fields if key in item}
                                   for item in measurements.get('issues',[])]
                diagnostic_batch.sort(key=lambda item:json.dumps(item,sort_keys=True,ensure_ascii=False))
                blocked_batch=[{key:item.get(key) for key in ('check','reason','paths','scenario') if key in item}
                               for item in measurements.get('blocked_checks',[])]
                blocked_batch.sort(key=lambda item:json.dumps(item,sort_keys=True,ensure_ascii=False))
                diagnostic_signature=digest({'issues':diagnostic_batch,'blocked_checks':blocked_batch})
                if diagnostic_signature in diagnostic_signatures:
                    stop_reason='repeated_diagnostic_batch_without_progress'
                    repeated_failed_state=True
                    self.events[-1].update({'diagnostic_signature':diagnostic_signature,
                                            'diagnostic_batch_progress':'unchanged_diagnostic_batch'})
                else:
                    diagnostic_signatures.add(diagnostic_signature)
                    self.events[-1].update({'diagnostic_signature':diagnostic_signature,
                                            'diagnostic_batch_progress':'new_or_changed_diagnostic_batch'})
                # Continue through new failures; an exact repeated state has no new repair evidence.
                failed_state=digest({'candidate':candidate,'error':error})
                if failed_state in failed_states:
                    if not repeated_failed_state:stop_reason='repeated_failed_state'
                    repeated_failed_state=True
                else:
                    failed_states.add(failed_state)
                # An invalid repair must not poison the next candidate.
                if before is not None and allowed is not None and candidate is None:candidate=before
            finally:
                self.save('stage_events.json',self.events)
                self.save_quality_metrics()
            if repeated_failed_state:break
            attempt+=1
        self.events.append({'stage':name,'status':'stopped','reason':stop_reason,
                            'attempts':attempt+1})
        self.save('stage_events.json',self.events)
        raise ValueError(f'{name} stopped after repeated failed state or diagnostic batch without progress: '
                         f'{stop_reason}; see {self.workspace}')

    async def execute(self,brief):
        revise_scene_design=scene_design_revision_required(self.replan_feedback)
        resume_brief_matches=False
        if self.resume_runtime:
            origin_path=self.resume_runtime.parent/'run_origin.json'
            if origin_path.is_file():
                try:resume_brief_matches=json.loads(origin_path.read_text(encoding='utf-8')).get('brief')==brief
                except (OSError,ValueError):pass
        if self.replan_spatial and not revise_scene_design and not resume_brief_matches:
            raise ValueError('Spatial recovery requires the exact original brief from its runtime.')
        self.reuse_prior_stages=(resume_brief_matches and not revise_scene_design and
                                 not bool((self.replan_feedback or {}).get('unresolved')))
        if self.replan_spatial and not revise_scene_design:
            from spatial_recovery import recover_spatial_scene
            return await recover_spatial_scene(self,brief)
        def decomposition_paths(data):
            # This stage is not frozen yet: regrouping is legitimate, subject to the original contract.
            return replacement_paths(data)
        if self.frozen_contract is not None:
            contract=RequestContract.model_validate(self.frozen_contract)
            self.save('frozen_request_contract.json',contract.model_dump())
        else:
            contract=(None if revise_scene_design else self.preserved_recovery_request_contract(brief))
        if self.frozen_contract is None and contract is None:
            contract=await self.stage('request_contract',RequestContract,
            'Extract the original user request, not a design. Return JSON. asset_boundary=single_asset when one editable composite asset is requested, otherwise scene. '
            'quality_target defaults realistic unless explicitly prototype. contents lists explicitly requested repeated contents and numeric quantity bounds; do not invent a count where none was requested. '
            'contents is for countable repeated content, not a catch-all checklist for floor materials, room identity, lighting or design layers. '
            'Each tag is a short stable identifier, description preserves semantics. Do not choose dimensions, geometry or layout. '
            'For an explicitly requested visual style, style_requirements lists 2-5 recognizable visible features needed to express that style, '
            'with stable IDs and concrete visible_evidence. No explicit style means an empty list. '
            'Preserve logical alternatives in the original brief. A or B means either may satisfy the requirement, '
            'not two mandatory requirements A AND B. For an alternative, choose an executable allowed realization '
            'using available_capabilities and state both the original alternative and the selected realization in feature. '
            'Do not turn typical style examples into extra mandatory features (e.g. do not require leaf relief when scrollwork is allowed instead). '
            'Classify geometry_need: regular_solids for simple primitives; swept_path only for clearly line-like constant-section rails or piping; freeform_surface for sculpted relief, leaf carving, layered historic scroll ornament, or variable-section organic surfaces; appearance for surface treatment. '
            'Choose the least complex representation that actually realizes the stated VISIBLE shape. A style name alone '
            'never establishes a freeform requirement. Regular raised panels, stepped moldings, straight trim and surrounds '
            'can use regular_solids; sculpted variable-section relief needs freeform_surface, and cannot be replaced by thin round tubes. '
            'For alternatives, freeform_surface is required only when EVERY permitted realization needs it. '
            'Negative mentions (not X, no need for X) are not positive style requests. Before returning, check each feature '
            'against the exact original wording and verify that geometry_need describes its shape, not a general style association. '
            'Do not pretend carved or sculpted style is only a color/material. Do not invent additional style requirements from generic quality words. '
            'For complete scenes, extract scene_use with ONLY user-specified numeric facts: extent [X width,Y depth,Z height] '
            'only if all three are given, minimum_route_width_m, and explicitly stated openings (kind, wall, offset, width). '
            'South=-Y=y_min, north=+Y=y_max, west=-X=x_min, east=+X=x_max; central offset=0. '
            'Use null for numeric facts not stated. scene_use.enclosure=enclosed for an indoor room/interior, '
            'open for an explicitly outdoor/open place, otherwise unspecified. This is a semantic requirement '
            'that an indoor scene must include actual architectural boundary geometry, not just furniture. '
            'Do not invent layout choices or relax dimensions/widths.',
            {'brief':brief,'available_capabilities':capabilities()},lambda c:c,
            initial_candidate=self.preserved_stage('request_contract'),
            prefer_matching_checkpoint=self.reuse_prior_stages)
        self.save('frozen_request_contract.json',contract.model_dump())
        decomposition_context={'brief':brief,'request_contract':contract.model_dump(),'available_capabilities':capabilities()}
        design_context={**decomposition_context}
        if revise_scene_design:
            design_context['previous_attempt_feedback']=self.replan_feedback
        design=None
        if contract.asset_boundary=='scene' and contract.quality_target=='realistic':
            from general_assets import local_catalog, planning_source_options
            from material_catalog import capabilities as material_capabilities
            available=local_catalog()
            source_context={'available_local_assets':available,'available_materials':material_capabilities(),
                            'runtime_source_capabilities':{'generation_available':self.allow_generation,'retrieval_available':True}}
            def validate_scene_design(value,stage_name='scene_design'):
                value,changes=normalize_facing_relationships(value)
                if changes:
                    self.save(stage_name+'_relationship_normalization.json',{
                        'changes':changes,
                        'raw_agent_response':'*_raw.txt',
                        'rule':'Facing is orientation only; requested metric spacing is represented by a separate distance relation.'})
                return validate_concept(value,brief)
            experience=planning_experience_prompt(brief)
            scene_design_prompt=prepend_experience(DESIGN_PROMPT,experience)
            if revise_scene_design:
                scene_design_prompt+='''
This is a design-level correction requested by the visual audit. Address the cited failure directly.
When the audit says optional inventory makes the room crowded, revise the choice list to omit or combine
those optional items while preserving every explicit user requirement and main-use anchor. The later
decomposition must follow this revised inventory; do not keep removed objects as unbound components.'''
            design=await self.stage('scene_design',SceneDesign,scene_design_prompt,
                {**design_context,**source_context},validate_scene_design,replacement_paths,
                initial_candidate=(None if revise_scene_design else self.preserved_stage('scene_design')),
                prefer_matching_checkpoint=self.reuse_prior_stages)
            self.save('scene_design.json',design.model_dump(mode='json'))
            queries=list(dict.fromkeys(q for c in design.choices for q in c.source_queries))
            options=None
            saved_options=self.resume_runtime/'planning_source_options.json' if self.resume_runtime else None
            if not revise_scene_design and saved_options and saved_options.is_file():
                try:options=json.loads(saved_options.read_text(encoding='utf-8'))
                except (OSError,json.JSONDecodeError):options=None
            if options is None:options=planning_source_options(queries)
            self.save('planning_source_options.json',options)
            feasibility_context={**decomposition_context,**source_context,
                'preliminary_design':design.model_dump(mode='json'),'matching_source_options':options}
            if revise_scene_design or (self.replan_feedback or {}).get('unresolved'):
                feasibility_context['previous_attempt_feedback']=self.replan_feedback
            design=await self.stage('design_feasibility',SceneDesign,DESIGN_PROMPT+experience+'''
The preliminary design is a proposal, not a frozen user specification. Refine it against source
availability BEFORE detailed decomposition. Preserve explicit user requirements and the main-use anchors,
while resolving every obvious source/representation mismatch in one response. When previous_attempt_feedback
identifies excess optional inventory or crowding, remove or consolidate the named optional choices in this
design and its later decomposition. Do not preserve an optional object merely because it appeared in an
earlier accepted plan. Never remove a user-requested object, activity anchor or required feature. Do not promise an exact sofa
upholstery, furniture profile, rug motif or sculpted fabric when available sources cannot support it.
For example, an available refined antique leather sofa can satisfy vintage comfort without inventing
an unavailable velvet rolled-arm variant. Prefer compatible real source families over speculative props.
Preserve dominant functional furniture TYPES selected in the preliminary design when matching remote
candidates exist. Uninspected remote candidates are not unavailable: they go through acquisition and QA.
Do not remove a sofa, bed, workbench or another main-use anchor just because only chairs/tables are
already cached locally. Change speculative upholstery/material specifics to suit plausible sources,
while retaining the primary furnishing and activity. Local convenience must not dictate room design.
Never call a source verified/accepted from catalog metadata. Existing files still need visual inspection.
For EVERY choice set realization=parametric or retrieve. For retrieve select exact selected_asset_ids
from supplied local/remote candidates. For parametric give a truthful constructible description using
available primitives and surfaces, not draped cloth or carved organic geometry. Keep primary seating
realistically upholstered using actual assets. Consolidate detail choices where they share construction.
When no actual source supports an optional decoration, replace it with a coherent source-supported
choice or justify omission before freeze. Do not promise fictional bundled tea/decanter/basket/throw
assets. Varied books can be genuine editable book primitives. The existing gilt picture frame includes
its image; the existing oil lamp is an oil lamp, not a cream-shaded table lamp. Choose honest semantics.
Keep a coherent composition, not an ever-expanding catalogue of individually mandatory knickknacks.
Consolidate related small details into meaningful design choices. This is not a count limit: retain every
explicitly requested layer, combine features when one choice can realize them together, and omit optional
details that add little beyond what the existing room already provides.
Regular construction and textured architectural surfaces can use aDSL. Draped cloth, open-book pages,
ornate upholstery and organic shapes need suitable actual sources; a source gap is not solved by calling
a flat box refined. Existing TRELLIS samples count as sourced geometry; do not schedule unavailable
inference. Candidate metadata alone does not establish orientation/geometry/visual acceptance.
Return the complete revised design, no dimensions or placements.''',
                feasibility_context,lambda d:validate_design_sources(
                    validate_scene_design(d,'design_feasibility'),available,options),replacement_paths,
                initial_candidate=(None if revise_scene_design else self.preserved_stage('design_feasibility')),
                prefer_matching_checkpoint=self.reuse_prior_stages)
            self.save('scene_design.json',design.model_dump(mode='json'))
            decomposition_context.update(source_context,scene_design=design.model_dump(mode='json'),
                scene_design_choice_ids=[choice.id for choice in design.choices],
                matching_source_options=options)
        if self.replan_feedback:decomposition_context['previous_attempt_feedback']=self.replan_feedback
        decomposition_seed=(latest_failed_stage_candidate(self.resume_runtime,'decomposition',Decomposition)
            if self.resume_runtime and resume_brief_matches and not revise_scene_design else None)
        if decomposition_seed:
            self.events.append({'stage':'decomposition',
                'status':'schema_valid_agent_candidate_restored_pending_full_validation',
                'source':decomposition_seed['path'],'prior_error':decomposition_seed.get('error'),
                'rule':'Exact same brief; current design, request, geometry and source validators still decide acceptance.'})
        def validate_decomposition(plan):
            plan=restore_validated_parametric_specs(plan,self.replan_feedback)
            diagnostics=Diagnostics('decomposition')
            checks=[lambda:plan.validate_decomposition(None),lambda:validate_request_contract(plan,contract),
                    lambda:validate_replan_style_feedback(plan,self.replan_feedback)]
            if design is not None:checks.extend([lambda:validate_design_coverage(plan,design),
                lambda:validate_source_choices(plan,available,options,allow_generation=self.allow_generation)])
            for check in checks:
                try:check()
                except (ValueError,KeyError,TypeError) as error:
                    report=failure_report(error,'decomposition')
                    diagnostics.issues.extend(report['issues']);diagnostics.blocked_checks.extend(report.get('blocked_checks',[]))
            diagnostics.raise_if_any()
            return plan
        outline=await self.stage('decomposition',Decomposition,prepend_experience(
            DECOMPOSE,planning_experience_prompt(brief)+'\n\n'+composition_archetype_prompt(brief)),
            decomposition_context,validate_decomposition,decomposition_paths,
            initial_candidate=(self.preserved_stage('decomposition') or
                (decomposition_seed['candidate'] if decomposition_seed else None)),
            prefer_matching_checkpoint=self.reuse_prior_stages)
        if design is not None:
            binding_ids=[o.id for o in outline.objects]
            binding_schema=_functional_bindings_response_schema(binding_ids)
            binding_seed=(latest_failed_stage_candidate(self.resume_runtime,'functional_bindings')
                          if self.resume_runtime and resume_brief_matches else None)
            if binding_seed:
                self.events.append({'stage':'functional_bindings',
                    'status':'unaccepted_agent_candidate_restored_pending_full_validation',
                    'source':binding_seed['path'],'prior_error':binding_seed.get('error'),
                    'rule':'Exact same brief; current schema, support and wall-attachment checks still decide acceptance.'})
            def validate_bindings(bindings):
                revised=apply_functional_bindings(outline,bindings)
                return validate_seated_surface_access(revised,bindings,design,brief)
            bindings=await self.stage('functional_bindings',FunctionalBindings,
                _functional_bindings_prompt(binding_ids),
                {'brief':brief,'scene_design':design.model_dump(mode='json'),
                 'component_ids':binding_ids,'response_schema':binding_schema,
                 'components':[o.model_dump(mode='json') for o in outline.objects]},
                validate_bindings,replacement_paths,
                initial_candidate=(self.preserved_stage('functional_bindings') or
                    (binding_seed['candidate'] if binding_seed else None)),
                prefer_matching_checkpoint=self.reuse_prior_stages)
            outline=apply_functional_binding_result(outline,bindings)
        access_faces=None
        if design is not None:
            users=[o for o in outline.objects if o.access_required and o.parent is None and o.collision_role!='shell']
            expected_access_ids=[o.id for o in users]
            seated_chair_ids={seat_id for seat_id,_ in seated_surface_pairs(design,outline)}
            def validate_faces(value):
                projected=project_access_face_ids(value,expected_access_ids)
                removed_sides=sorted(set(value.allowed_sides)-set(projected.allowed_sides))
                removed_reasons=sorted(set(value.reasons)-set(projected.reasons))
                self.save('access_faces_normalization.json',{
                    'basis':'frozen access_required root IDs',
                    'expected_owner_ids':expected_access_ids,
                    'removed_extra_allowed_side_ids':removed_sides,
                    'removed_extra_reason_ids':removed_reasons,
                    'raw_candidate_preserved_in':'access_faces_raw.txt',
                })
                return validate_access_faces(projected,expected_access_ids,seated_chair_ids)
            access_faces=await self.stage('access_faces',AccessFaces,
                ACCESS_FACES_PROMPT,
                {'brief':brief,'expected_access_owner_ids':expected_access_ids,
                 'scene_design':design.model_dump(mode='json'),
                 'components':[{'id':o.id,'semantic':o.semantic,'dimensions':o.dimensions,
                    'required_features':o.required_features,'interface_notes':o.interface_notes} for o in users]},
                validate_faces,replacement_paths,initial_candidate=self.preserved_stage('access_faces'),
                prefer_matching_checkpoint=self.reuse_prior_stages)
        frozen=outline.model_dump(mode='json')
        self.save('frozen_decomposition.json',{'hash':digest(frozen),'plan':frozen})
        def layout_paths(data):
            entries=data.get('placements',[])
            if len(entries)!=len(outline.objects) or any(not isinstance(e,dict) for e in entries):
                return ['/placements'] if 'placements' in data else []
            paths=[f'/placements/{i}/{key}' for i,_ in enumerate(entries)
                    for key in ('position','yaw_deg') if key in data['placements'][i]]
            paths.extend(f'/placements/{i}/id' for i,e in enumerate(entries)
                         if e.get('id')!=outline.objects[i].id and 'id' in e)
            if 'route_points' in data:paths.append('/route_points')
            if 'access_centers' in data:paths.append('/access_centers')
            return paths
        layout_schema=Layout.model_json_schema()
        # IDs are a closed enum at this stage; no virtual scene-boundary objects.
        layout_schema['$defs']['Placement']['properties']['id']['enum']=[o.id for o in outline.objects]
        spatial=None;coarse=None
        spatial_outline=spatial_outline_context(outline)
        if access_faces:spatial_outline['functional_access_faces']=access_faces.model_dump(mode='json')
        layout_context={'brief':brief,'frozen_decomposition':spatial_outline,'placement_schema':layout_schema,
            'placement_frames':[{'id':obj.id,'coordinate_frame':'scene_centered' if not obj.parent else 'parent_local',
                'parent_id':obj.parent,'mount':obj.mount,'support_part':obj.support_part,
                'dimensions':list(obj.dimensions),'grounded':obj.grounded}
                for obj in outline.objects]}
        if design is not None:
            spatial_outline['scene_design']=design.model_dump(mode='json')
            spatial_outline['design_bindings']=outline.design_bindings
            layout_context['composition_instruction']=('Preserve the whole-space design: bind every group and link. '
                'Only spatial links require a typed relation between their groups; circulation links require a real '
                'route; semantic links preserve daylight, lighting, view and negative-space intent without inventing '
                'object constraints. Protect intentional empty volumes and render experienced views. A feasible '
                'packing is not a coherent spatial composition.')
        if contract.asset_boundary == 'scene':
            if design is not None:
                def validate_circulation(value):
                    expected_ids=list(access_faces.allowed_sides)
                    validate_approach_owners(value,expected_ids)
                    for approach in value.approaches:
                        if approach.side not in access_faces.allowed_sides[approach.owner_id]:
                            raise ValueError(approach.owner_id+': use an actual functional face')
                        if min(approach.size)<=0:raise ValueError('Human approach sizes must be positive')
                        user_width=contract.scene_use.minimum_route_width_m if contract.scene_use else None
                        if user_width is not None and approach.final_route_width<user_width:
                            raise ValueError('Keep the original user route width')
                        axis=1 if approach.side in {'front','back'} else 0
                        if approach.size[axis]+.025 < approach.final_route_width/2+.005:
                            raise ValueError(approach.owner_id+': final corridor radius cannot fit beside the owner inside the chosen approach depth')
                    if value.shared_aisle_width is not None and user_width is not None and value.shared_aisle_width<user_width:
                        raise ValueError('Keep the original user minimum width on the shared aisle.')
                    return value
                circulation=await self.stage('circulation_program',CirculationProgram,
                    'Plan final HUMAN APPROACHES independently from world coordinates. Return one approach per supplied '
                    'functional owner, plus shared_aisle_owner_id and shared_aisle_width. Choose one real activity '
                    'approach near the primary shared room aisle as its anchor; the system connects the entrance to '
                    'that approach once, then connects the other activity approaches as short branches from that shared '
                    'node. This is a connected route graph, not a separate full-length entrance path to every object. '
                    'Choose an anchor whose approach area can host the aisle junction. shared_aisle_width is the width '
                    'of that common path; final_route_width is each owner\'s local approach width. Do not assume every '
                    'sofa/coffee-table gap is a 1m corridor. '
                    'Choose ergonomic dimensions for the actual adult use; typical short individual seat approaches '
                    'can use about 0.55-0.65m when wheelchair access is not requested. This is guidance, not a fixed '
                    'template. Storage/serving approaches need standing space; a bench end can offset along_face '
                    'to keep a shared low table reachable without filling the whole front strip. For a task/dining '
                    'chair facing a desk/table, never choose chair side=front: its local front points '
                    'into the work surface; choose back or a genuinely clear side for entry and pullback. The paired '
                    'seated surface has no duplicate floor approach unless the brief separately requires standing or '
                    'accessibility use. Preserve actual user minimum widths exactly. Use one shared main aisle and '
                    'only the local branches needed to reach each distinct activity. Explain each choice. '
                    'Side is owner-local, not world compass direction. '
                    'size is local X/Y in metres. A final corridor endpoint must fit beside the owner: approach normal '
                    'depth + .025 must exceed half the final_route_width. Do not output routes, zones or coordinates.',
                    {'brief':brief,'functional_faces':access_faces.model_dump(),
                     'scene_design':design.model_dump(mode='json'),
                     'components':[o.model_dump(mode='json') for o in outline.objects if o.id in access_faces.allowed_sides],
                     'original_user_requirements':contract.scene_use.model_dump() if contract.scene_use else None},
                    validate_circulation,replacement_paths,
                    initial_candidate=self.preserved_stage('circulation_program'),
                    prefer_matching_checkpoint=self.reuse_prior_stages)
            def validate_spatial(s, preliminary_errors=()):
                from scene_spatial import canonical_access_geometry,canonical_spatial_bounds
                s=s.model_copy(update={'requirements':contract.scene_use})
                before=s.model_dump(mode='json')
                s,normalization=normalize_corner_based_spatial_frame(s,outline.extent)
                if normalization:
                    normalized=s.model_dump(mode='json')
                    self.save('spatial_frame_normalization.json',{
                        **normalization,'before_hash':digest(before),'after_hash':digest(normalized),
                        'raw_agent_response':'spatial_plan_raw.txt'})
                before_facing=s.model_dump(mode='json')
                s,facing_changes=normalize_facing_relations(s)
                if facing_changes:
                    self.save('spatial_facing_normalization.json',{
                        'changes':facing_changes,
                        'before_hash':digest(before_facing),
                        'after_hash':digest(s.model_dump(mode='json')),
                        'raw_agent_response':'spatial_plan_raw.txt',
                        'rule':'Facing is orientation only; explicit spacing must be a separate distance relation.'})
                if design is not None:
                    s=apply_circulation_program(s,circulation,outline,contract,design)
                s=canonical_spatial_bounds(s,outline.extent)
                checks=[]
                def reject(error):
                    raise ValueError(str(error))
                for name,error in preliminary_errors:
                    checks.append((name,lambda error=error:reject(error),('/access_areas','/routes','/openings')))
                if design is not None:
                    errors=[]
                    for area in s.access_areas:
                        allowed=access_faces.allowed_sides.get(area.owner_id)
                        if allowed is None:
                            errors.append(f'{area.id}: {area.owner_id} has no separate human operating area; use its support activity/opening approach')
                        elif area.side not in allowed:
                            errors.append(f'{area.id}: choose local side from {allowed}; '+access_faces.reasons[area.owner_id])
                    if errors:
                        message='Functional access faces: '+'; '.join(errors)
                        checks.append(('functional_access_faces',lambda message=message:reject(message),('/access_areas',)))
                try:
                    s=canonical_access_geometry(s)
                except (ValueError,TypeError,KeyError,IndexError,ZeroDivisionError) as error:
                    checks.append(('access_area_geometry',lambda error=error:reject(error),('/access_areas',)))
                s=s.model_copy(update={'requirements':contract.scene_use})
                checks.extend([
                    ('composition_lowering',lambda:validate_lowering(s,design,outline)),
                    ('spatial_contract',lambda:validate_spatial_contract(s,outline.objects,outline.extent)),
                    ('zone_capacity',lambda:validate_zone_capacity(s,outline.objects,outline.parameters,outline.edit_checks)),
                ])
                validate_together('spatial_design',checks)
                return s
            resume_spatial=None;resume_layout=None;failed_spatial_seed=None
            if self.resume_runtime:
                contract_path=self.resume_runtime/'frozen_spatial_contract.json'
                accepted_path=self.resume_runtime/'spatial_plan_accepted.json'
                spatial_source=None
                if contract_path.is_file():
                    resume_spatial=json.loads(contract_path.read_text(encoding='utf-8'))['contract']
                    spatial_source=str(contract_path)
                elif accepted_path.is_file():
                    resume_spatial=json.loads(accepted_path.read_text(encoding='utf-8'))
                    spatial_source=str(accepted_path)
                elif not self.replan_spatial and resume_brief_matches:
                    failed_spatial_seed=latest_failed_stage_candidate(self.resume_runtime,'spatial_plan')
                    if failed_spatial_seed:
                        resume_spatial=failed_spatial_seed['candidate']
                        spatial_source=failed_spatial_seed['path']
                layouts=[*self.resume_runtime.glob('coarse_layout*failure_*.json'),
                         *self.resume_runtime.glob('coarse_layout*accepted.json')]
                numerical_seed=self.resume_runtime/'numerical_layout_seed.json'
                if numerical_seed.is_file():layouts.append(numerical_seed)
                layout_source=None
                if layouts:
                    selected=max(layouts,key=lambda p:p.stat().st_mtime_ns)
                    saved=json.loads(selected.read_text(encoding='utf-8'))
                    resume_layout=saved.get('candidate',saved)
                    layout_source=str(selected)
                rejected={}
                if resume_spatial is not None:
                    mismatches=spatial_checkpoint_mismatches(resume_spatial,outline,design)
                    if mismatches:
                        rejected['spatial']={'source':spatial_source,'reasons':mismatches}
                        if failed_spatial_seed is not None:
                            self.events.append({'stage':'spatial_plan',
                                'status':'failed_candidate_seed_rejected',
                                'source':spatial_source,
                                'reasons':mismatches,
                                'rule':'A failed spatial candidate can seed repair only while its component, design and intent bindings still match the current plan.'})
                        resume_spatial=None
                        failed_spatial_seed=None
                if failed_spatial_seed is not None:
                    self.events.append({'stage':'spatial_plan',
                        'status':'unaccepted_agent_candidate_restored_pending_full_validation',
                        'source':spatial_source,'prior_error':failed_spatial_seed.get('error'),
                        'rule':'Reuse only as a repair seed; current schema, composition, bounds, access and spatial checks must all pass.'})
                if resume_layout is not None:
                    mismatches=layout_checkpoint_mismatches(resume_layout,outline)
                    if mismatches:
                        rejected['layout']={'source':layout_source,'reasons':mismatches}
                        resume_layout=None
                if rejected:self.save('resume_checkpoint_rejections.json',rejected)
            spatial_plan_instructions=prepend_experience(
                SPATIAL,spatial_experience_prompt(brief)+'\n\n'+composition_archetype_prompt(brief))+'''
When scene_design.composition exists, lower it to composition_groups (actual component_ids, anchor_id
and relation_ids connecting members, including support chains), composition_links (one binding for every
design link, with cross-group relation_ids only for binding_mode="spatial" and route_ids for
binding_mode="circulation"; semantic links may have empty relation_ids and route_ids), open_spaces
(intent_id,center,size,bottom,height: actual protected empty volumes, not zones to fill), and experience_views
(intent_id,position,target,lens_mm: real human perspective locations). Preserve design_bindings. Every group,
link, empty-space and view intent needs a real binding. For every scene_design group relationship, emit a SpatialRelation carrying its
exact design_relation_id, kind, choice-bound endpoints, a functionally justified gap interval and facing tolerance, then
include that actual relation ID in the corresponding composition_groups.relation_ids. For supported_by and
attached_to, use the exact parent/interface relation instead. Preserve typed relationships that connect
distinct scene objects; relationships among layers bound to one editable root are internal recipe facts.
Distance alone does not encode which side. Express meaningful distances/facing/directions; don't add arbitrary
maxima between unrelated objects. Consider entire masses, density contrasts and views before details.
Every facing relation constrains orientation only: set minimum_m=0 and maximum_m=null. If the user explicitly
requires a metric reach or gap, encode it as a separate distance relation with a concrete use-based reason.
For chairs paired with desks or tables, allow the seat to tuck to the work edge; the chair's rear access area
owns entry and pullback. Never convert chair pullback into a positive facing gap.
Approved human approaches and routes are supplied separately in circulation_program and compiled by the
system after this stage as one shared entrance aisle with branches to functional approaches. Do not create,
rename or alter access_areas/routes. Keep every zone and route point
within room bounds; the compiler does not translate or clip an invalid spatial proposal into a different
composition. No aesthetic scores.'''
            spatial=await self.stage('spatial_plan',SpatialContract,spatial_plan_instructions,
                {'brief':brief,'frozen_decomposition':spatial_outline,'frozen_user_spatial_requirements':
                    contract.scene_use.model_dump() if contract.scene_use else None,
                 'circulation_program':circulation.model_dump(mode='json') if design is not None else None,
                 **({'spatial_recovery_evidence':self.spatial_recovery_feedback(),
                     'previous_spatial_contract':json.loads((self.resume_runtime/'frozen_spatial_contract.json').read_text(encoding='utf-8'))['contract'],
                     'instruction':'Replan all coupled zones, object relationships, negative space, paths and views together. '
                        'Keep the architectural apertures compatible with the existing shell recipe. '
                        'Furniture poses are free to change. Do not preserve the previous wrong side/facing/grouping. '
                        'The existing meshes and required features are fixed; preserve useful richness.'}
                    if self.replan_spatial else {})},
                validate_spatial,replacement_paths,
                initial_candidate=None if self.replan_spatial else resume_spatial)
            validate_composed_spatial=validate_spatial
            if design is not None:
                def validate_composed_spatial(value):
                    try:
                        value=value.model_copy(update={'requirements':contract.scene_use})
                        value,_=normalize_corner_based_spatial_frame(value,outline.extent)
                        value=apply_circulation_program(value,circulation,outline,contract,design)
                    except (ValueError,TypeError,KeyError,IndexError) as error:
                        return validate_spatial(value,[('approved_circulation',error)])
                    return validate_spatial(value)
            layout_context['frozen_spatial_contract']=spatial.model_dump(mode='json')
            self.save('frozen_spatial_contract.json',{'hash':digest(spatial.model_dump(mode='json')),
                                                     'contract':spatial.model_dump(mode='json')})
            def validate_coarse(layout):
                layout,anchor_normalization=anchor_centered_architectural_shells(outline,layout)
                if anchor_normalization:
                    self.save('architectural_anchor_normalization.json',anchor_normalization)
                known_routes={r.id for r in spatial.routes};known_access={a.id for a in spatial.access_areas}
                stale_routes=set(layout.route_points)-known_routes;stale_access=set(layout.access_centers)-known_access
                if stale_routes or stale_access:
                    self.save('ignored_stale_layout_overrides.json',{'route_ids':sorted(stale_routes),'access_ids':sorted(stale_access),
                        'reason':'Overrides for removed inferred design IDs do not define constraints. Every current contract constraint is still checked.'})
                    layout=layout.model_copy(update={'route_points':{k:v for k,v in layout.route_points.items() if k in known_routes},
                        'access_centers':{k:v for k,v in layout.access_centers.items() if k in known_access}})
                ids=[p.id for p in layout.placements];expected=[o.id for o in outline.objects]
                if len(ids)==len(expected) and set(ids)==set(expected):
                    lookup={p.id:p for p in layout.placements}
                    layout=layout.model_copy(update={'placements':[lookup[id] for id in expected]})
                report=coarse_preflight(outline,layout,spatial)
                self.save('numerical_layout_seed.json',layout.model_dump(mode='json'))
                receipt=None
                if report['status'] != 'passed':
                    from layout_solver import solve_layout
                    solved,receipt=solve_layout(outline,layout,spatial)
                    self.save('numerical_layout_solver.json',receipt)
                    if solved is not None:
                        layout=solved
                        report=coarse_preflight(outline,layout,spatial)
                        self.save('numerical_layout_solution.json',layout.model_dump(mode='json'))
                    elif receipt.get('candidate'):
                        layout=Layout.model_validate(receipt['candidate'])
                        report=coarse_preflight(outline,layout,spatial)
                        self.save('numerical_layout_seed.json',layout.model_dump(mode='json'))
                    report['numerical_solver']=receipt
                # Measurable composition gate: the prose in LAYOUT is now
                # backed by numbers, and each rejection names the layer that
                # actually owns the fix (decomposition / layout / spatial).
                comp=composition_hook(outline,layout,spatial,brief,report)
                self.save('composition_judge.json',comp)
                report['composition_archetype']=comp.get('archetype')
                report['composition_metrics']=comp.get('metrics',{})
                if comp.get('issues'):
                    known={i.get('id') for i in report.get('issues',[])}
                    fresh=[i for i in comp['issues'] if i.get('id') not in known]
                    report.setdefault('issues',[]).extend(fresh)
                    if fresh:report['status']='needs_revision'
                elif comp.get('status')=='blocked':
                    report.setdefault('blocked_checks',[]).append(
                        {'check':'composition_metrics',
                         'reason':comp.get('blocked_reason','adapter unavailable')})
                routed=SpatialContract.model_validate(report['routed_contract'])
                self.save('coarse_spatial_judge.json',report)
                write_spatial_preview(routed,report['bounds'],outline.extent,report,self.workspace/'spatial_preview.png')
                if report['status'] != 'passed':
                    if receipt and receipt['status'] in {'infeasible_current_choices','routing_requires_revision'}:
                        raise SpatialChoicesInfeasible(report)
                    raise PlanningValidationError(report)
                return layout
            spatial_failures=set();spatial_failure_history=[]
            reviewed_human_failures=set()
            revision=0
            while True:
                coarse_name='coarse_layout' if revision==0 else ('coarse_layout_revision' if revision==1 else f'coarse_layout_revision_{revision:02d}')
                coarse_seed=resume_layout if revision==0 and not self.replan_spatial else None
                if revision and previous_layout is not None:
                    # A spatial redesign may already make the existing Agent
                    # layout feasible. Recheck it before paying for another
                    # complete coordinate transcription. Failed seeds still
                    # reach the layout Agent with fresh, complete diagnostics.
                    try:coarse_seed=validate_coarse(Layout.model_validate(previous_layout)).model_dump(mode='json')
                    except (ValueError,SpatialChoicesInfeasible):
                        failed_seed=json.loads((self.workspace/'coarse_spatial_judge.json').read_text(encoding='utf-8'))
                        layout_context['previous_layout_failure_evidence']=prompt_evidence(
                            failed_seed,scenario_names=[c.parameter for c in outline.edit_checks])
                        self.events.append({'stage':coarse_name,'status':'previous_layout_needs_revision'})
                    else:
                        self.events.append({'stage':coarse_name,'status':'previous_layout_revalidated'})
                try:
                    coarse=await self.stage(coarse_name,Layout,prepend_experience(
                        LAYOUT,spatial_experience_prompt(brief)+'\n\n'+composition_archetype_prompt(brief)),layout_context,validate_coarse,layout_paths,
                        initial_candidate=coarse_seed)
                    break
                except DetailedPlanningRevisionRequired:
                    raise
                except (ValueError,SpatialChoicesInfeasible):
                    # Route/zone choices are design variables, unlike user facts.
                    # Keep this failure at its actual abstraction level.
                    failure_path=self.workspace/'coarse_spatial_judge.json'
                    if not failure_path.is_file():raise
                    failed=json.loads(failure_path.read_text(encoding='utf-8'))
                    if failed.get('status')=='passed':raise
                    failure_key=digest({'contract':spatial.model_dump(mode='json'),
                        'issues':failed.get('issues',[]),'solver':failed.get('numerical_solver',{}).get('incompatible_constraints',[])})
                    solver=failed.get('numerical_solver',{})
                    conflict_families=set(diagnostic_families(failed))
                    conflict_families.update(spatial_conflict_families(solver,outline))
                    if (failure_key in spatial_failures or
                            repeated_spatial_conflict(conflict_families,spatial_failure_history,threshold=.8)):
                        self.events.append({'stage':coarse_name,'status':'stopped_no_spatial_progress',
                            'spatial_conflict_families':sorted(conflict_families),
                            'reason':'The same measured spatial issue families remain after a complete spatial revision.'})
                        self.save('stage_events.json',self.events)
                        self.save_quality_metrics()
                        raise
                    spatial_failure_history.append(conflict_families)
                    spatial_failures.add(failure_key)
                revision_name='spatial_revision' if revision==0 else f'spatial_revision_{revision+1:02d}'
                seed_path=self.workspace/'numerical_layout_seed.json'
                previous_layout=json.loads(seed_path.read_text(encoding='utf-8')) if seed_path.is_file() else None
                # Keep every independent issue while avoiding duplicated bounds,
                # routed contracts and per-edit copies in the model's context.
                evidence=prompt_evidence(failed,scenario_names=[c.parameter for c in outline.edit_checks])
                if design is not None and human_access_failure(evidence,spatial):
                    review_key=digest({'faces':access_faces.model_dump(),'program':circulation.model_dump(),
                        'issues':evidence.get('issues',[]),
                        'constraints':sorted({label.get('constraint','') if isinstance(label,dict) else label
                            for label in evidence.get('numerical_solver',{}).get('incompatible_constraints',[])})})
                    if review_key not in reviewed_human_failures:
                        reviewed_human_failures.add(review_key)
                        review=await self.stage('human_use_revision_'+str(revision),HumanUseRevision,
                            'Review inferred HUMAN ACCESS decisions using the complete activity groups and measured '
                            'failure evidence. Return functional_faces, circulation and reason, not positions. '
                            'The ORIGINAL request is fixed. Preserve exactly all approach owners, their size tuples '
                            'and final_route_width values. You may correct an inferred entry side, allowed_sides '
                            'and along_face with a physically grounded reason. Consider how a person reaches and '
                            'enters a group of interacting objects, rather than classifying each visible front alone. '
                            'A working partner can obstruct one entry without making every other side unusable. '
                            'Do not invent closed arms/sides or weaken genuine functional access to satisfy packing. '
                            'If the prior choices are appropriate, return them unchanged and explain what grouping '
                            'needs redesign. No new components, coordinates, smaller clearances or aesthetic scores.',
                            {'brief':brief,'scene_design':design.model_dump(mode='json'),
                             'frozen_decomposition':spatial_outline,'previous_faces':access_faces.model_dump(),
                             'previous_program':circulation.model_dump(),'previous_spatial_contract':spatial.model_dump(),
                             'previous_layout':previous_layout,'coarse_failure_evidence':evidence,
                             'original_user_requirements':contract.scene_use.model_dump() if contract.scene_use else None},
                            lambda value:validate_human_use_revision(value,access_faces,circulation,contract,
                                forbid_front_owners=seated_chair_ids),replacement_paths)
                        access_faces=review.functional_faces;circulation=review.circulation
                        spatial_outline['functional_access_faces']=access_faces.model_dump(mode='json')
                        self.save('current_functional_access_faces.json',access_faces.model_dump(mode='json'))
                        self.save('current_circulation_program.json',circulation.model_dump(mode='json'))
                spatial=await self.stage(revision_name,SpatialContract,prepend_experience(
                    SPATIAL,spatial_experience_prompt(brief))+
                    '\nThe previous spatial/layout pair was infeasible. Replan zones, access areas and routes using '
                    'the full design program. Retain and UPDATE composition_groups, composition_links, open_spaces '
                    'and experience_views from previous_spatial_contract; every original design intent still needs '
                    'a real binding. When revising relations, update every affected relation_ids reference. '
                    'Shared design choices may assign different instances to their actual spatial groups; '
                    'do not invent cross-room proximity merely to put all lights into every group. '
                    'Resolve the measured failures; preserve the original user dimensions, openings and minimum widths. '
                    'Do not ask position-only repair to fix a route outside the room or an impossible grouping. '
                    'Use the numerical_solver incompatible_constraints as a conflict set: resolve ALL independent '
                    'zone/access/facing/distance conflicts together. Zones may overlap and should fit rotated '
                    'furniture plus use clearances and declared edits. Avoid imposing arbitrary maximum distances '
                    'between unrelated furniture. Preserve useful furniture, scene richness and purpose. '
                    'Do not weaken human circulation or remove genuine functional relationships. '
                    'The numerical solver orientation_proposals are measured candidate poses, not noise: use them '
                    'to reconsider the furniture side, facing axis and local front convention together. Preserve '
                    'the intended activity, but change inferred zones or relation direction when that is what makes '
                    'the authored arrangement physically realizable. The next layout Agent may change yaws; the '
                    'numerical solver then solves translations.',
                    {'brief':brief,'frozen_decomposition':spatial_outline,'previous_spatial_contract':spatial.model_dump(mode='json'),
                     'previous_layout':previous_layout,'coarse_failure_evidence':evidence,'frozen_user_spatial_requirements':
                         contract.scene_use.model_dump() if contract.scene_use else None},validate_composed_spatial,replacement_paths)
                layout_context['frozen_spatial_contract']=spatial.model_dump(mode='json')
                layout_context['previous_layout']=previous_layout
                layout_context['previous_layout_failure_evidence']=evidence
                self.save('frozen_spatial_contract.json',{'hash':digest(spatial.model_dump(mode='json')),
                                                         'contract':spatial.model_dump(mode='json')})
                revision+=1
        recipes={}
        self.current_spatial=spatial;self.current_coarse=coarse
        for obj in outline.objects:
            if obj.acquisition.strategy=='parametric':
                recipes[obj.id]=await self.component_recipe(outline,obj,brief,contract.style_requirements)
        outline,unwired=prune_unwired_proposals(outline,recipes,coarse) if coarse else (outline,{})
        if unwired:
            self.save('unwired_parameter_proposals.json',{'parameters':unwired,
                'reason':'Inferred declarations have no geometry references; retained as provenance. Requested edit controls and actual spatial constraints are preserved.'})
        plan,outline=await self.finalize_layout(outline,recipes,coarse,spatial,
            contract.style_requirements,layout_context,layout_paths)
        plan.design_program=design
        plan.design_bindings=outline.design_bindings
        if plan.spatial:
            self.save('frozen_spatial_contract.json',{'hash':digest(plan.spatial.model_dump(mode='json')),
                                                     'contract':plan.spatial.model_dump(mode='json')})
        self.save('validated_plan.json',plan.model_dump(mode='json',by_alias=True))
        self.save('planning_summary.json',{'status':'planned','accepted_scene':False,
            'backend':'staged_llm' if self.complete is None else 'injected_test_transport',
            'frozen_decomposition_hash':digest(frozen),'component_count':len(plan.components),
            'spatial_contract_hash':digest(plan.spatial.model_dump(mode='json')) if plan.spatial else None,
            'coarse_layout_before_recipes':coarse is not None,
            'quality_metrics':'planning_quality_metrics.json',
            'model_calls':len(self.transport_calls),
            'repairs':[e for e in self.events if 'paths' in e],
            'limitations':['A validated plan is not a visually accepted scene.','Recipe repairs are component-local; layout repairs preserve object sizes and routes.']})
        return plan

    def initialize_runtime(self):
        """Share the production transport with standalone diagnostic regressions."""
        if self.complete is None and not hasattr(self,'runtime'):
            sys.path.insert(0,str(fenv.ADSL_ROOT/'adsl-agents'))
            from utils.runner import AgentRuntime
            self.runtime=AgentRuntime(model_profile=self.model_profile,workspace=self.workspace,task_id=self.task_id)
            self.runtime.model_settings.max_tokens=max(self.runtime.model_settings.max_tokens or 8000,8000)
            if self.runtime.profile.model=='deepseek-flash':
                # Provider default high effort exhausted 16k without emitting JSON.
                # The documented low setting retains reasoning within staged budgets.
                self.runtime.model_settings.extra_body={'thinking':{'type':'enabled'},'reasoning_effort':'low'}

    def plan(self,brief):
        self.initialize_runtime()
        return asyncio.run(self.execute(brief))

