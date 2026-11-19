"""Category-independent planning and component source routing.

The old planner is intentionally study-room specific.  This module is the
generic contract boundary: an LLM (or an explicitly replayed plan)
describes the scene in semantic terms, then a deterministic router decides
which objects remain editable aDSL geometry and which require an existing
asset or TRELLIS generation.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import finescene_env as fenv
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from asset_physics import AssemblyContract, FitSpan, PartForm
from curve_presets import CurveSpec, ProfileSpec, check_curve_fields
from part_consistency import check_copy_fields
from part_details import BookDetail, Finish, SurfaceSpec
from scene_spatial import SpatialContract, validate_spatial_contract
from scene_design import SceneDesign
from scene_experience import planning_experience_prompt, prepend_experience
from surface_mounting import SurfaceContact


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, validate_default=True)


Scalar = float | str
Vec3 = tuple[Scalar, Scalar, Scalar]


class SourceDecision(StrictModel):
    mode: Literal["parametric", "retrieve", "generate", "auto"] = "auto"
    asset_id: str | None = None
    front_rotation_deg: float = 0
    reason: str = Field(min_length=12, max_length=800)
    retrieval_query: str = ""
    reference_query: str = ""
    reference_image: str | None = None
    trellis_required: bool = False
    trellis_reason: str = ""
    quality_target: Literal["prototype", "realistic"] = "realistic"
    generation_trigger: Literal["not_applicable", "custom_appearance", "retrieval_exhausted", "retrieval_failure"] = "not_applicable"
    reference_requirements: list[str] = Field(default_factory=list)
    retrieval_evidence: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_generation_evidence(self):
        if self.trellis_required != (self.mode == "generate"):
            raise ValueError("trellis_required means unconditional generate; auto is conditional")
        if self.mode in {"generate", "auto"}:
            if not (self.reference_image or self.reference_query) or len(self.trellis_reason.strip()) < 12:
                raise ValueError("generation paths require a reference strategy and trellis_reason")
            if not self.reference_requirements:
                raise ValueError("generation paths require reference image acceptance criteria")
        if self.mode == "generate" and self.generation_trigger not in {"custom_appearance", "retrieval_exhausted"}:
            raise ValueError("direct generation requires customization or recorded retrieval failure")
        if self.mode == "auto" and self.generation_trigger != "retrieval_failure":
            raise ValueError("auto requires the conditional retrieval_failure trigger")
        if self.generation_trigger == 'retrieval_exhausted' and not self.retrieval_evidence:
            raise ValueError('retrieval_exhausted requires prior search/QA evidence paths')
        if self.mode in {"retrieve", "auto"} and not self.retrieval_query.strip():
            raise ValueError("retrieval requires an explicit search query")
        return self


class PartSpec(StrictModel):
    """Planner-level part declaration; converted to general_scene.Part later."""

    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
    primitive: Literal["box", "cylinder", "sphere", "book", "sweep", "lathe"]
    size: Vec3
    center: Vec3
    rotation: tuple[float, float, float] = (0, 0, 0)
    color: tuple[float, float, float] = (.5, .5, .5)
    finish: Finish = 'matte'
    book: BookDetail | None = None
    surface: SurfaceSpec | None = None
    semantic: str = ''
    form: PartForm | None = None
    rigidity: Literal['rigid','parametric'] = 'parametric'
    parent: str | None = None
    mount: Literal['origin','top','bottom'] = 'origin'
    fit_between: list[FitSpan] = Field(default_factory=list,max_length=3)
    path: list[Vec3] = Field(default_factory=list, max_length=64)
    radius: Scalar | None = None
    radius_end: Scalar | None = None
    closed: bool = False
    curve: CurveSpec | None = None
    profile: ProfileSpec | None = None
    like: str | None = None
    mirror: Literal['none','x','y','xy'] = 'none'

    @model_validator(mode='after')
    def check_sweep(self):
        check_curve_fields(self)
        check_copy_fields(self)
        return self


class ComponentPlan(StrictModel):
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
    semantic: str = Field(min_length=1, max_length=400)
    category: str = Field(min_length=1, max_length=120)
    quantity: int = Field(default=1, ge=1, le=128)
    dimensions: Vec3
    position: Vec3 = (0, 0, 0)
    instance_positions: list[Vec3] = Field(default_factory=list, max_length=128)
    orientation_deg: tuple[Literal[0], Literal[0], float] = (0, 0, 0)
    parent: str | None = None
    support: str | None = None
    attachment: Literal["free", "floor", "wall", "ceiling", "top", "surface", "bottom"] = "free"
    support_part: str | None = None
    surface_contact: SurfaceContact | None = None
    grounded: bool = False
    access_required: bool = True
    support_footprint: tuple[Scalar,Scalar] | None = None
    collision_role: Literal["solid", "shell", "floor_covering"] = "solid"
    editable_parameters: dict[str, float] = Field(default_factory=dict)
    rigidity: Literal["rigid", "parametric"] = "rigid"
    decomposition_reason: str = Field(min_length=12)
    required_features: list[str] = Field(min_length=1)
    material_requirements: list[str] = Field(min_length=1)
    interface_notes: list[str] = Field(default_factory=list)
    parts: list[PartSpec] = Field(default_factory=list, max_length=160)
    assembly: AssemblyContract | None = None
    visual_importance: int = Field(default=3, ge=1, le=5)
    source: SourceDecision

    @model_validator(mode="after")
    def check_component(self):
        if any(isinstance(v, (int, float)) and v <= 0 for v in self.dimensions):
            raise ValueError(f"{self.id}: dimensions must be positive metres")
        if (self.quantity > 1 or self.instance_positions) and len(self.instance_positions) != self.quantity:
            raise ValueError(f"{self.id}: quantity > 1 requires exactly quantity instance_positions")
        if self.source.mode == "parametric" and not self.parts:
            raise ValueError(f"{self.id}: parametric components require explicit parts")
        if self.source.mode != "parametric" and self.parts:
            raise ValueError(f"{self.id}: external components cannot carry parametric parts")
        if len({p.id for p in self.parts}) != len(self.parts):
            raise ValueError(f"{self.id}: duplicate part IDs")
        if self.orientation_deg[:2] != (0, 0):
            raise ValueError("Current compiler supports object yaw only; pitch/roll must be explicit part rotations")
        if self.rigidity == "parametric" and self.source.mode != "parametric":
            raise ValueError("External meshes are rigid; split editable structure into separate components")
        if self.attachment in {"top", "surface", "bottom"} and not (self.support or self.parent):
            raise ValueError("Surface attachment requires an explicit support")
        if self.support_part and (not (self.support or self.parent) or
                                  self.attachment not in {'top','surface','bottom'}):
            raise ValueError('support_part requires a surface-mounted child and explicit parent/support')
        return self


class RelationPlan(StrictModel):
    type: Literal["parent", "supported_by", "attached_to", "adjacent_to", "paired_with", "clearance"]
    from_id: str = Field(alias="from")
    to_id: str = Field(alias="to")
    clearance: float | None = Field(default=None, ge=0, le=100)
    note: str = ""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class EditPlan(StrictModel):
    parameter: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_.-]{0,79}$")
    affected_components: list[str] = Field(default_factory=list)
    invariant_components: list[str] = Field(default_factory=list)
    recomputation_scope: Literal["component", "subtree", "scene"] = "component"


class EditScenario(StrictModel):
    parameter: str
    delta: float
    intent: str


class CoordinateFrame(StrictModel):
    up: Literal['+Z'] = '+Z'
    front: Literal['-Y'] = '-Y'
    right: Literal['+X'] = '+X'


class SceneIntent(StrictModel):
    scene_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
    description: str = Field(min_length=1, max_length=2000)
    style: str = Field(min_length=1, max_length=300)
    quality_target: Literal["prototype", "realistic"] = "realistic"
    extent: tuple[float, float, float]
    coordinate_frame: CoordinateFrame = Field(default_factory=CoordinateFrame)
    lighting_expectations: str = "physically plausible key, fill and practical lights"
    camera_expectations: str = "overview, reverse, top and component close-ups"
    review_azimuth_deg: float = Field(default=0,ge=-360,le=360)

    @model_validator(mode="after")
    def check_extent(self):
        if any(v <= 0 or v > 1000 for v in self.extent):
            raise ValueError("scene extent must be positive metres and <= 1000")
        return self


from style_contract import StyleRequirement,StyleBinding,validate_style_bindings


class PlannerScenePlan(StrictModel):
    schema_version: Literal["finescene.general_plan.v1"] = Field("finescene.general_plan.v1", alias="schema")
    intent: SceneIntent
    parameters: dict[str, float] = Field(default_factory=dict)
    components: list[ComponentPlan] = Field(min_length=1, max_length=160)
    relations: list[RelationPlan] = Field(default_factory=list, max_length=640)
    edit_plans: list[EditPlan] = Field(default_factory=list, max_length=160)
    edit_checks: list[EditScenario] = Field(default_factory=list, max_length=32)
    assumptions: list[str] = Field(default_factory=list, max_length=80)
    style_requirements: list[StyleRequirement] = Field(default_factory=list,max_length=8)
    style_bindings: list[StyleBinding] = Field(default_factory=list,max_length=8)
    spatial: SpatialContract | None = None
    design_program: SceneDesign | None = None
    design_bindings: dict[str,list[str]] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, populate_by_name=True)

    @model_validator(mode="after")
    def check_graph(self):
        validate_style_bindings(self.style_requirements,self.style_bindings,self.components,
                                source_mode=lambda c:c.source.mode)
        from material_catalog import material_assignment_issues
        material_issues=[issue for component in self.components
                         for issue in material_assignment_issues(component,component.parts)]
        if material_issues:
            detail='; '.join(f"{item['component_id']}/{item['part_id']} ({item['texture_id']}): {item['reason']}"
                             for item in material_issues)
            raise ValueError('Incompatible planned surface materials: '+detail)
        ids = [c.id for c in self.components]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate component IDs")
        known = set(ids)
        for check in self.edit_checks:
            if check.parameter not in self.parameters or abs(check.delta)<.001:
                raise ValueError('Edit scenario needs a declared parameter and nonzero delta')
        repeated = {c.id for c in self.components if c.quantity > 1}
        expanded = [c.id if c.quantity == 1 else f"{c.id}_{i:02d}"
                    for c in self.components for i in range(1, c.quantity + 1)]
        if len(expanded) != len(set(expanded)) or len(expanded) > 80:
            raise ValueError("Expanded component IDs collide or exceed 80 objects")
        for component in self.components:
            for endpoint in (component.parent, component.support):
                if endpoint and endpoint not in known:
                    raise ValueError(f"{component.id}: unknown relation endpoint {endpoint}")
                if endpoint in repeated:
                    raise ValueError("Ambiguous repeated parent/support: declare explicit instance IDs instead")
            if component.support and component.parent and component.support != component.parent:
                raise ValueError("Current compiler requires support and transform parent to agree")
            if component.support_part:
                parent=next(c for c in self.components if c.id==(component.parent or component.support))
                if parent.source.mode!='parametric':
                    raise ValueError(f'{component.id}: support_part requires a parametric parent')
                part=next((p for p in parent.parts if p.id==component.support_part),None)
                if part is None:
                    raise ValueError(f'{component.id}: support_part {component.support_part} is not declared on {parent.id}')
                if part.primitive!='box' or any(abs(a)>1e-8 for a in part.rotation[:2]):
                    raise ValueError(f'{component.id}: support_part must be a horizontal box face')
            if component.source.quality_target != self.intent.quality_target:
                raise ValueError("Component quality cannot silently differ from the scene target")
        for relation in self.relations:
            if relation.from_id not in known or relation.to_id not in known:
                raise ValueError(f"unknown relation endpoint: {relation.from_id}->{relation.to_id}")
        for edit in self.edit_plans:
            if not set(edit.affected_components + edit.invariant_components) <= known:
                raise ValueError(f"edit plan {edit.parameter} references unknown component")
            if set(edit.affected_components) & set(edit.invariant_components):
                raise ValueError("Edit cannot declare a component affected and invariant")
            if edit.parameter not in self.parameters:
                raise ValueError("Edit plans currently target global parameters; local parameters remain component-scoped")
        # Parent/support edges must be acyclic so a local edit has a finite scope.
        for component in self.components:
            seen = {component.id}
            cursor = component.parent or component.support
            while cursor:
                if cursor in seen:
                    raise ValueError("cyclic parent dependency")
                seen.add(cursor)
                cursor = next((c.parent or c.support for c in self.components if c.id == cursor), None)
        if self.spatial is not None:
            validate_spatial_contract(self.spatial, self.components, self.intent.extent)
        return self


def route_component(component: ComponentPlan) -> tuple[str, str]:
    """Preserve the acquisition policy; only observed QA resolves auto."""
    source = component.source
    if source.mode == "parametric":
        return "parametric", source.reason
    if source.mode == "auto":
        return "auto", "retrieve_then_generate_if_candidates_fail: " + source.reason
    return source.mode, source.reason


def _parts_to_general(parts: list[PartSpec]):
    from general_scene import Part
    return [Part.model_validate(p.model_dump()) for p in parts]


def to_general_plan(plan: PlannerScenePlan, *, check_layout: bool = True, check_geometry: bool = True):
    """Convert the detailed planner schema into the executable aDSL schema."""
    from general_scene import GeneralPlan, SceneObject, Source
    objects = []
    decisions = []
    for component in plan.components:
        mode, route_reason = route_component(component)
        source = component.source.model_copy(update={"mode": mode})
        positions = component.instance_positions or [component.position]
        for index, position in enumerate(positions, 1):
            object_id = component.id if component.quantity == 1 else f"{component.id}_{index:02d}"
            parent = component.parent or component.support
            objects.append(SceneObject(
                id=object_id,
                semantic=component.semantic,
                source=Source(mode=mode, asset_id=source.asset_id,front_rotation_deg=source.front_rotation_deg,
                              query=source.retrieval_query,
                              reference_image=source.reference_image,
                              reference_query=source.reference_query,
                              required_features=component.required_features,
                              material_requirements=component.material_requirements,
                              reference_requirements=source.reference_requirements,
                              decision_reason=source.reason, generation_trigger=source.generation_trigger),
                size=component.dimensions,
                position=position,
                rotation_deg=component.orientation_deg[2],
                parent=parent,
                mount=('top' if component.attachment in {'top','surface'} else 'bottom' if component.attachment=='bottom' else 'origin'),
                support_part=component.support_part,
                surface_contact=component.surface_contact,
                grounded=component.grounded,
                access_required=component.access_required,
                support_footprint=component.support_footprint,
                collision_role=component.collision_role,
                parameters=component.editable_parameters,
                rigidity=component.rigidity,
                parts=_parts_to_general(component.parts) if mode == "parametric" else [],
                assembly=component.assembly,
            ))
            decisions.append({"component_id": object_id, "semantic": component.semantic,
                              "requested_mode": component.source.mode, "resolved_mode": mode,
                              "route_reason": source.reason if component.source.mode != "auto" else route_reason,
                              "trellis_required": source.trellis_required,
                              "trellis_reason": source.trellis_reason,
                              "retrieval_query": source.retrieval_query,
                              "reference_query": source.reference_query,
                              "generation_trigger": source.generation_trigger,
                              "retrieval_evidence": source.retrieval_evidence,
                              "required_features": component.required_features,
                              "material_requirements": component.material_requirements,
                              "reference_requirements": source.reference_requirements,
                              "decomposition_reason": component.decomposition_reason,
                              "interface_notes": component.interface_notes,
                              "support_part": component.support_part,
                              "support_footprint": component.support_footprint,
                              "grounded": component.grounded,
                              "visual_importance": component.visual_importance,
                              "quantity_group": component.id})
    executable = GeneralPlan(scene_id=plan.intent.scene_id, description=plan.intent.description,
                             extent=plan.intent.extent,
                             review_azimuth_deg=plan.intent.review_azimuth_deg,
                             parameters=plan.parameters,
                             spatial=plan.spatial,
                             objects=objects,
                             relations=[{**r.model_dump(by_alias=True), "from": a, "to": b}
                                for r in plan.relations
                                for a in [d['component_id'] for d in decisions if d['quantity_group'] == r.from_id]
                                for b in [d['component_id'] for d in decisions if d['quantity_group'] == r.to_id]],
                             edit_plans=[{**e.model_dump(mode="json"),
                                 'affected_components':[d['component_id'] for d in decisions if d['quantity_group'] in e.affected_components],
                                 'invariant_components':[d['component_id'] for d in decisions if d['quantity_group'] in e.invariant_components]}
                                 for e in plan.edit_plans],
                             assumptions=plan.assumptions)
    executable.edit_checks=[c.model_dump() for c in plan.edit_checks]
    from general_scene import object_size, vec, compile_local_geometry
    from planning_diagnostics import Diagnostics
    diagnostics=Diagnostics('detailed_plan')
    for i,obj in enumerate(executable.objects):
        path=f'/components/{i}'
        try:size=object_size(executable,obj)
        except (ValueError,KeyError,TypeError,ZeroDivisionError,SyntaxError) as failure:
            diagnostics.add('component_dimensions',f'{obj.id}: {failure}',paths=[path+'/dimensions'],component_ids=[obj.id])
            diagnostics.block('local_geometry','Invalid owner dimensions.',paths=[path],scenario=obj.id)
            continue
        params={**executable.parameters,**obj.parameters,**dict(zip(('w','d','h'),size))}
        try:vec(obj.position,params)
        except (ValueError,KeyError,TypeError,ZeroDivisionError,SyntaxError) as failure:
            diagnostics.add('position_expression',f'{obj.id}: {failure}',paths=[path+'/position'],component_ids=[obj.id])
        part_errors=False
        for j,part in enumerate(obj.parts):
            for field,value,positive in [('size',part.size,True),('center',part.center,False)]:
                try:vec(value,params,positive=positive)
                except (ValueError,KeyError,TypeError,ZeroDivisionError,SyntaxError) as failure:
                    diagnostics.add('part_expression',f'{obj.id}/{part.id} {field}: {failure}',paths=[path+f'/parts/{j}/{field}'],component_ids=[obj.id])
                    part_errors=True
        if check_geometry and obj.source.mode == 'parametric':
            if part_errors:
                diagnostics.block('local_geometry','Invalid part expressions.',paths=[path])
                continue
            evidence={}
            try:compile_local_geometry(executable,obj,evidence=evidence)
            except ValueError as failure:
                findings=evidence.get('assembly',{}).get('findings',[])
                for finding in findings:
                    affected=set(finding.get('part_ids',[]))
                    diagnostics.add(finding['code'],finding['evidence'],
                        paths=[path+f'/parts/{j}' for j,p in enumerate(obj.parts) if p.id in affected]+[path+'/assembly'],
                        component_ids=[obj.id],measured={id:r for id,r in evidence.get('parts',{}).items() if id in affected})
                if not findings:diagnostics.add('local_geometry',str(failure),paths=[path+'/parts'],component_ids=[obj.id])
    from general_scene import planned_envelope_bounds, judge_general_geometry, audit_parametric_scene
    if check_layout or check_geometry:
        try:preflight=(audit_parametric_scene(executable,check_spatial=check_layout) if check_geometry else
                       judge_general_geometry(executable,planned_envelope_bounds(executable),check_spatial=check_layout))
        except (ValueError,KeyError,TypeError,ZeroDivisionError,SyntaxError) as failure:
            report=getattr(failure,'measurements',{})
            if isinstance(report,dict) and 'issues' in report:
                diagnostics.issues.extend(report['issues'])
                diagnostics.blocked_checks.extend(report.get('blocked_checks',[]))
            else:diagnostics.add('layout_evaluation',str(failure),paths=['/components'])
            diagnostics.block('layout_relations','World bounds could not be fully evaluated.')
        else:
            diagnostics.blocked_checks.extend(preflight.get('blocked_checks',[]))
            for finding in preflight['findings']:
                diagnostics.add(finding.get('code','layout_relation'),finding['evidence'],paths=finding.get('paths') or [f'/components/{i}/position'
                    for i,obj in enumerate(executable.objects) if obj.id in finding.get('component_ids',[])],
                    component_ids=finding.get('component_ids',[]),
                    expected=finding.get('expected'),
                    measured={**finding.get('measured',{}),
                        **{k:finding[k] for k in ('repair_target','part_ids','constraint_id') if k in finding}})
    diagnostics.raise_if_any()
    return executable, decisions


_INSTRUCTIONS = """You are FineScene's general scene planner. Plan ANY scene described by the user.
Return only the detailed JSON schema. First list all visible objects and necessary structural objects.
For every component specify semantic identity, category, count, metres dimensions, pose, parent/support,
attachment, collision role, editable parameters, and an explicit part decomposition when aDSL can express it.
Use collision_role="floor_covering" only for grounded independent rugs or mats no thicker than 0.08m;
these are walkable floor finishes, may lie beneath furniture, and do not block route clearance. Raised platforms
and other solid objects remain collision_role="solid".
For an enclosed room, reserve collision_role="shell" for geometry that forms the room boundary and proves its
openings. A separate fitted window assembly (frame, sash, glazing, sill, shutter or blind) is a solid object;
register its exact ID in the matching window opening's fitted_component_ids and place it at that opening.
For a component mounted on a specific surface of a parametric parent, set support_part to that parent's
exact flat box-part ID (for example top_rail or tabletop); do not anchor to a higher decorative extremity.
Choose source mode: parametric for regular editable structure; retrieve for an existing asset likely to match;
generate for a custom object or evidenced retrieval gap; auto when retrieval should be tried before
TRELLIS. Both auto and generate require trellis_reason and a reference query or provided reference image.
Explain uncertainty in assumptions. Keep relations
and edit propagation explicit. Never silently replace a complex component with a box. Parametric parts
support box/cylinder/sphere/book plus an editable sweep path for curved rails and scrollwork; use that
path when it can express the requested silhouette.
For sweep, path has 2..64 three-vector offsets from part.center, radius is a positive constant-section
radius (or bounded parameter expression), and size encloses every path offset plus radius. The compiler
translates by center and rotates around it; size does not stretch the path. This is a capsule-chain
approximation, not a carved relief, profile sweep or watertight union. Swept style bindings must name
qualified component/part IDs in part_ids; sculpted relief remains an external rigid child.

Plan at three levels: functional zones/scene composition; independently editable physical objects;
then structural parts versus isolated generated surfaces. Do not generate an entire room as one mesh.
Decompose where independent edits, motion, assembly or support require it, not at every surface detail.
Example: cabinet frame and shelf spacing stay parametric; an ornate rigid ornament may be generated.
Generated geometry cannot promise internal parameter edits. Its envelope, pose and replacement are editable.
Do not choose TRELLIS just because an object is important or absent from a short local catalog.
Direct generate requires custom_appearance or recorded retrieval_exhausted evidence. Otherwise use auto
with generation_trigger=retrieval_failure, retrieval query AND reference strategy; trellis_required=false
until retrieval actually fails. Parametric/retrieve use generation_trigger=not_applicable.
Explicit generate uses trellis_required=true. Explain each decision and structural decomposition.
Specify required visible features, materials, mounting interfaces, rigidity and reference acceptance
criteria. Reference inputs should isolate one full object, show important surfaces, avoid occlusion,
strong perspective and ambiguous background. List unverifiable details in assumptions.

Style-discipline rule: a style adjective in the brief (for example Baroque, Rococo or Art Deco)
does not authorize inventing a specific historical motif, object category or named icon. Preserve
the style as a visible style requirement, but use a generic semantic such as "carved Baroque crown
ornament" when the user did not request an urn, eagle, shield, floral species or other exact motif.
Do not turn a failed reference for one motif into a different invented motif. After concrete source
rejections, simplify the target to the requested visible silhouette and style, or keep it in an
editable sweep/parametric part when that representation is sufficient. A reference query must use
catalog vocabulary for that generic component, and reference requirements must focus on visible
identity and silhouette; hidden mounting faces, exact material, scale and finish are checked after
generation and must not make the reference impossible to satisfy.

For each window opening, put only actual separate sash/frame/shutter component IDs fitted in that aperture in
fitted_component_ids; those fitted components alone may occupy that window's approach volume and are checked
against its aperture and interior fixture_projection_m. Room contents and routes still need to clear it.
Use collision_role=floor_covering only for a grounded independent rug/mat no thicker than 0.08m; it may
underlie furniture and is excluded from human route blockers. Platforms and raised solids remain solid.

Use metres, Z up, front -Y. Scene extent (W,D,H) is centered in X/Y with floor Z=0: bounds are
[-W/2,+W/2] x [-D/2,+D/2], never [0,W] x [0,D]. Wall opening offsets are signed world coordinates
from the room center along the wall tangent, not distances from a corner; a width-q opening must satisfy
offset in [-T/2+q/2,+T/2-q/2] for tangent extent T. Compute and verify the interval before returning.
Each component's local
origin is its bottom center, parts must stay in [-w/2,w/2],[-d/2,d/2],[0,h]. size is an envelope,
not permission to stretch an external mesh. Position is parent-local; top attachment adds support top
to local Z, so touching objects use z=0. Use explicit stable IDs for repeated parents. Quantity>1
requires individual positions; all instances share yaw. Compiler supports object yaw only.
Connect editable dimensions and positions to parameter expressions, using only + - * / and names.
Do not merely list editable parameters while hardcoding their geometry. w,d,h are evaluated local size.
For every proposed edit list affected/invariant IDs and recomputation scope. Include clearance/support
relations for functional usability. Do not infer a study, bathroom or room shell unless the brief asks.
Plan camera visibility including occluding ceilings, full object and detail views; quality is never
downgraded silently. Maximum expanded object count is 80. All component quality targets match intent.
"""


def _planner_instructions(brief):
    return prepend_experience(_INSTRUCTIONS, planning_experience_prompt(brief))


class GeneralLLMPlanner:
    def __init__(self, model_profile: str | Path, workspace: Path, task_id: str):
        self.model_profile = Path(model_profile)
        self.workspace = workspace
        self.task_id = task_id

    def plan(self, brief: str) -> PlannerScenePlan:
        agents_root = fenv.ADSL_ROOT / "adsl-agents"
        sys.path.insert(0, str(agents_root))
        from utils.runner import AgentRuntime
        from agents import Runner
        runtime = AgentRuntime(model_profile=self.model_profile, workspace=self.workspace, task_id=self.task_id)
        # Preserve the raw answer before Pydantic validation. SDK-level parsing
        # previously discarded malformed plans, leaving no repair evidence.
        runtime.model_settings.max_tokens = max(runtime.model_settings.max_tokens or 0, 16000)
        agent = runtime.agent(name="finescene-general-planner", instructions=_planner_instructions(brief))
        prompt = json.dumps({"brief": brief, "required_output": PlannerScenePlan.model_json_schema()}, ensure_ascii=False)

        async def call():
            self.workspace.mkdir(parents=True,exist_ok=True)
            message=prompt
            for attempt in range(2):
                result = Runner.run_streamed(agent, message, max_turns=2)
                async for _ in result.stream_events():
                    pass
                runtime.usage.record(stage="general_plan", agent=agent.name, result=result)
                answer=str(result.final_output).strip()
                (self.workspace/f'planner_raw_{attempt}.txt').write_text(answer,encoding='utf-8')
                if answer.startswith('```'):
                    answer=answer.split('\n',1)[1].rsplit('```',1)[0].strip()
                try:
                    parsed=PlannerScenePlan.model_validate_json(answer)
                    to_general_plan(parsed)
                    return parsed
                except ValueError as error:
                    evidence=str(error)[:5000]
                    (self.workspace/f'validation_{attempt}.txt').write_text(evidence,encoding='utf-8')
                    message=json.dumps({'request':brief,'schema':PlannerScenePlan.model_json_schema(),
                        'previous_plan':answer,'repair_errors':evidence,
                        'instruction':'Return a complete corrected JSON plan, preserve intent and stable IDs.'},ensure_ascii=False)
            raise ValueError('Planner failed validation after one repair; inspect agent_runtime/validation_*.txt')

        try:
            async def bounded():return await asyncio.wait_for(call(),timeout=300)
            result = asyncio.run(bounded())
            return result if isinstance(result, PlannerScenePlan) else PlannerScenePlan.model_validate(result)
        except Exception as error:
            raise RuntimeError(f"General Planner failed ({type(error).__name__}); inspect {self.workspace}; no candidate was accepted") from None


__all__ = ["PlannerScenePlan", "ComponentPlan", "SourceDecision", "RelationPlan", "EditPlan",
           "GeneralLLMPlanner", "route_component", "to_general_plan"]
