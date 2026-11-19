"""Design intent lowered to relationships, protected space and real viewpoints.

This checks that the Agent's design can affect execution, not whether a design
is beautiful. Image-grounded designer critique remains responsible for that.
"""
from pydantic import BaseModel, ConfigDict, Field, model_validator
from typing import Literal
import hashlib
from planning_diagnostics import Diagnostics


class Model(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)


class DesignRelation(Model):
    """A typed, reviewable spatial relationship between two design choices."""
    id: str = Field(pattern=r'^[A-Za-z][A-Za-z0-9_-]{0,63}$')
    a_choice_id: str
    b_choice_id: str
    kind: Literal['facing','distance','left_of','right_of','in_front_of','behind',
                  'supported_by','attached_to']
    minimum_m: float = Field(default=0, ge=0, le=100,
        description='Footprint separation in metres; must be zero for supported_by/attached_to.')
    maximum_m: float | None = Field(default=None, ge=0, le=100,
        description='Only distance and facing may set an upper bound. All directional and contact kinds must use null.')
    tolerance_deg: float = Field(default=30, gt=0, le=90)
    reason: str = Field(min_length=2)

    @model_validator(mode='after')
    def valid_relation(self):
        if self.a_choice_id == self.b_choice_id:
            raise ValueError('A design relationship needs two different choices')
        if self.maximum_m is not None and self.maximum_m < self.minimum_m:
            raise ValueError('Design relationship maximum is smaller than its minimum')
        if self.kind == 'distance' and self.maximum_m is None and self.minimum_m <= 0:
            raise ValueError('A distance relationship needs a positive minimum or an explicit maximum')
        if self.kind in {'left_of','right_of','in_front_of','behind','supported_by','attached_to'} and self.maximum_m is not None:
            raise ValueError(f'{self.kind} uses a minimum separation and cannot declare maximum_m')
        if self.kind in {'supported_by','attached_to'} and self.minimum_m > 0:
            raise ValueError(f'{self.kind} is a contact relationship and cannot declare a positive gap')
        return self


class DesignGroup(Model):
    id: str = Field(pattern=r'^[A-Za-z][A-Za-z0-9_-]{0,63}$')
    role: str = Field(min_length=2)
    choice_ids: list[str] = Field(min_length=1)
    anchor_choice_id: str
    organization: str = Field(min_length=2)
    density_and_rhythm: str = Field(min_length=2)
    relationships: list[DesignRelation] = Field(default_factory=list, max_length=32)


class DesignLink(Model):
    id: str = Field(pattern=r'^[A-Za-z][A-Za-z0-9_-]{0,63}$')
    a: str
    b: str
    intent: str = Field(min_length=2)
    binding_mode: Literal['semantic','spatial','circulation'] = 'semantic'


class SpaceIntent(Model):
    id: str = Field(pattern=r'^[A-Za-z][A-Za-z0-9_-]{0,63}$')
    purpose: str = Field(min_length=2)


class ViewIntent(Model):
    id: str = Field(pattern=r'^[A-Za-z][A-Za-z0-9_-]{0,63}$')
    experience: str = Field(min_length=2)
    group_ids: list[str] = Field(min_length=1)


class CompositionIntent(Model):
    whole_space: str = Field(min_length=2)
    groups: list[DesignGroup] = Field(min_length=1,max_length=16)
    links: list[DesignLink] = Field(default_factory=list,max_length=32)
    open_spaces: list[SpaceIntent] = Field(default_factory=list,max_length=12)
    views: list[ViewIntent] = Field(min_length=1,max_length=6)
    editing_priorities: str = Field(min_length=2)


class GroupBinding(Model):
    group_id: str
    component_ids: list[str] = Field(min_length=1)
    anchor_id: str
    relation_ids: list[str] = Field(default_factory=list)


class LinkBinding(Model):
    link_id: str
    relation_ids: list[str] = Field(default_factory=list)
    route_ids: list[str] = Field(default_factory=list)


class OpenSpace(Model):
    intent_id: str = Field(pattern=r'^[A-Za-z][A-Za-z0-9_-]{0,63}$')
    center: tuple[float,float]
    size: tuple[float,float]
    bottom: float = Field(default=.03,ge=0)
    height: float = Field(default=1.9,gt=0)


class ExperienceView(Model):
    intent_id: str = Field(pattern=r'^[A-Za-z][A-Za-z0-9_-]{0,63}$')
    position: tuple[float,float,float]
    target: tuple[float,float,float]
    lens_mm: float = Field(default=28,ge=18,le=85)


def _choice_identity(choice):
    return ' '.join((choice.id.replace('_',' '),choice.activity,choice.selection)).casefold()


def _is_architectural_opening(choice):
    if choice.layer!='architecture':return False
    identity=_choice_identity(choice)
    return any(term in identity for term in (
        'window','window frame','window opening','door','doorway','door leaf','aperture',
        'glazing','sash','skylight','clerestory','fenestration','opening fixture',
        '窗','门','采光口','天窗','窗洞','门洞'))


def _is_opening_treatment(choice):
    identity=_choice_identity(choice)
    return any(term in identity for term in (
        'curtain','drape','blind','window shade','valance','window treatment',
        '窗帘','百叶帘','遮阳帘'))


def _is_envelope_fixture(choice):
    if choice.layer!='lighting':return False
    identity=_choice_identity(choice)
    return any(term in identity for term in (
        'ceiling','ambient','chandelier','pendant','flush light','main light',
        '吸顶灯','吊灯','顶灯','天花板','主灯'))


def validate_concept(design, original_request=''):
    concept=design.composition
    if concept is None:raise ValueError('Design whole space before parts: supply composition with groups, density/emptiness, relationships and experienced views')
    d=Diagnostics('composition_intent');choices={c.id for c in design.choices};groups={g.id for g in concept.groups}
    choice_by_id={choice.id:choice for choice in design.choices}
    request=' '.join(str(original_request or '').split()).casefold()
    explicit_open_space=any(term in request for term in (
        'open floor','open area','clear floor','dance floor','dance studio','courtyard',
        'stage','yoga','exercise area','stretching area','play area','空地','留空','中央地面',
        '舞蹈','舞台','庭院','瑜伽','健身','运动区域','活动区域'))
    ordinary_void_terms=(
        'central open floor','central floor','breathing space','breathing room',
        'visual separation','chair pullout','chair pull-out','chair pull back','chair pullback',
        'circulation','entry path','arrival path','window approach','sightline','walking path',
        'aisle','room center','center of the room','middle of the room','floor at the room center',
        'floor in the center','continuous floor','sense of spaciousness','room to move',
        'window frontage','floor directly in front of the window','in front of the window',
        'daylight frontage','glazing approach','clear view out',
        '通行动线','入口动线','窗前净空','视觉留白','呼吸空间')
    for collection in (concept.groups,concept.links,concept.open_spaces,concept.views):
        if len({x.id for x in collection})!=len(collection):d.add('intent_ids','Duplicate composition intent IDs',paths=['/composition'])
    for index,space in enumerate(concept.open_spaces):
        purpose=space.purpose.casefold().replace('-', ' ')
        matched=next((term for term in ordinary_void_terms if term in purpose),None)
        if matched and not explicit_open_space:
            d.add('nonprogrammatic_open_space',
                f'{space.id}: protected open_spaces cannot encode ordinary {matched}. Describe breathing room as composition; '
                'use access_areas/routes for human use. Keep a protected volume only when the original request asks for '
                'a distinct open-floor activity or feature.',
                paths=[f'/composition/open_spaces/{index}'])
    for group_index,g in enumerate(concept.groups):
        if not set(g.choice_ids)<=choices or g.anchor_choice_id not in g.choice_ids:
            d.add('group_choices',f'{g.id}: group members and anchor must use actual choice IDs',paths=['/composition/groups'])
        if len(set(g.choice_ids))!=len(g.choice_ids):
            d.add('group_choices',f'{g.id}: choice IDs must be unique within a group',paths=['/composition/groups'])
        opening_members=[choice_id for choice_id in g.choice_ids
                         if choice_id in choice_by_id and _is_architectural_opening(choice_by_id[choice_id])]
        activity_members=[choice_id for choice_id in g.choice_ids
                          if choice_id in choice_by_id and choice_by_id[choice_id].layer!='architecture'
                          and not _is_opening_treatment(choice_by_id[choice_id])
                          and not _is_envelope_fixture(choice_by_id[choice_id])]
        if opening_members and activity_members:
            d.add('opening_activity_group',
                f'{g.id}: fitted doors/windows/skylights belong to an architectural-envelope group, not a furniture/activity group. '
                f'Separate opening choices {opening_members} from activity choices {activity_members}; express daylight or arrival '
                'intent with a group link or composition prose, and let the opening contract locate the fixture.',
                paths=[f'/composition/groups/{group_index}'],component_ids=[*opening_members,*activity_members])
        relation_ids=[r.id for r in g.relationships]
        if len(set(relation_ids))!=len(relation_ids):
            d.add('relation_ids',f'{g.id}: relationship IDs must be unique within the group',paths=['/composition/groups'])
        graph={choice:set() for choice in g.choice_ids}
        for relation_index,relation in enumerate(g.relationships):
            if not {relation.a_choice_id,relation.b_choice_id}<=set(g.choice_ids):
                d.add('group_relationship_choices',f'{g.id}/{relation.id}: relationship endpoints must be choices in this group',paths=['/composition/groups'])
                continue
            door_choices=[]
            for choice_id in (relation.a_choice_id,relation.b_choice_id):
                choice=choice_by_id.get(choice_id)
                if choice is None:continue
                identity=_choice_identity(choice)
                words=set(identity.replace('-', ' ').replace('/', ' ').split())
                if choice.layer=='architecture' and (
                        'door' in words or 'doorway' in words or any(term in identity for term in ('门','入户','门洞'))):
                    door_choices.append(choice_id)
            if relation.kind=='facing' and door_choices:
                d.add('door_facing_relation',
                    f'{g.id}/{relation.id}: an entrance door leaf is oriented by its jamb and swing, not by facing '
                    f'another choice. Remove or replace the typed facing relation for door choice(s) {door_choices}; '
                    'describe arrival sightlines in composition prose or a view intent.',
                    paths=[f'/composition/groups/{group_index}/relationships/{relation_index}'])
            opening_endpoints=[choice_id for choice_id in (relation.a_choice_id,relation.b_choice_id)
                               if choice_id in choice_by_id and _is_architectural_opening(choice_by_id[choice_id])]
            other_endpoints=[choice_id for choice_id in (relation.a_choice_id,relation.b_choice_id)
                             if choice_id not in opening_endpoints]
            if opening_endpoints and any(
                    choice_by_id.get(choice_id) is not None and
                    choice_by_id[choice_id].layer!='architecture' and
                    not (_is_opening_treatment(choice_by_id[choice_id]) and
                         relation.kind in {'supported_by','attached_to'})
                    for choice_id in other_endpoints):
                related=[choice_id for choice_id in other_endpoints
                         if choice_by_id.get(choice_id) is not None and
                         choice_by_id[choice_id].layer!='architecture' and
                         not (_is_opening_treatment(choice_by_id[choice_id]) and
                              relation.kind in {'supported_by','attached_to'})]
                d.add('opening_furnishing_relation',
                    f'{g.id}/{relation.id}: an architectural opening is positioned by its wall/opening contract, not by a typed '
                    f'{relation.kind} relation to furnishing/activity choice(s) {related}. Remove this relation; express useful '
                    'daylight or arrival intent in composition prose or a group link. A window treatment may use attached_to '
                    'within the opening assembly.',
                    paths=[f'/composition/groups/{group_index}/relationships/{relation_index}'],
                    component_ids=[*opening_endpoints,*related])
            graph[relation.a_choice_id].add(relation.b_choice_id)
            graph[relation.b_choice_id].add(relation.a_choice_id)
        reached={g.anchor_choice_id};pending=list(reached)
        while pending:
            for choice in graph.get(pending.pop(),set())-reached:
                reached.add(choice);pending.append(choice)
        if set(g.choice_ids)-reached:
            d.add('group_relationship_coverage',f'{g.id}: connect every choice to anchor {g.anchor_choice_id} with typed relationships; disconnected={sorted(set(g.choice_ids)-reached)}',paths=['/composition/groups'])
    all_relationship_ids=[relation.id for group in concept.groups for relation in group.relationships]
    if len(all_relationship_ids)!=len(set(all_relationship_ids)):
        d.add('relation_ids','Typed design relationship IDs must be unique across the whole scene design',paths=['/composition/groups'])
    for link in concept.links:
        if not {link.a,link.b}<=groups or link.a==link.b:
            d.add('group_link',f'{link.id}: link two actual design groups',paths=['/composition/links'])
    for v in concept.views:
        if not set(v.group_ids)<=groups:d.add('view_groups',f'{v.id}: unknown viewed groups',paths=['/composition/views'])
    d.raise_if_any();return design


def validate_bound_relationships(design,outline):
    """Catch impossible contact intents before freezing spatial lowering."""
    d=Diagnostics('bound_design_relationships')
    objects={o.id:o for o in outline.objects}
    for group in design.composition.groups:
        for relation in group.relationships:
            a=set(outline.design_bindings.get(relation.a_choice_id,[]))
            b=set(outline.design_bindings.get(relation.b_choice_id,[]))
            if relation.kind in {'supported_by','attached_to'}:
                realizable=bool(a&b) or any(objects[id].parent in b for id in a if id in objects)
                if not realizable:
                    d.add('unrealizable_design_contact',
                        f'{relation.id}: {relation.kind} requires A to be a child of B, or two design layers '
                        f'within the same editable component. A={sorted(a)}, B={sorted(b)}, '
                        f'actual parents={ {id:objects[id].parent for id in a if id in objects} }. '
                        'Correct the contact direction or use a truthful spatial relationship; do not reparent frozen geometry.',
                        paths=['/composition/groups'])
            # Multiple design choices can describe layers of one editable
            # component. Their internal geometry belongs to the component
            # recipe, not to the scene-level relationship graph.
    d.raise_if_any();return design


def _group_route_nodes(spatial, members):
    """Associate real entry apertures through their declared door/frame members.

    Opening IDs are route nodes, not access-area owners. Only entrance fixtures
    explicitly bound to this group establish membership; windows and arbitrary
    shell labels cannot stand in for a traversable entrance.
    """
    nodes={area.id for area in spatial.access_areas if area.owner_id in members}
    nodes.update(opening.id for opening in spatial.openings
                 if opening.kind=='entrance' and members.intersection(
                     opening.fitted_component_ids+opening.swing_leaf_ids))
    return nodes


def lower_typed_relationships(spatial,design,outline):
    """Compile Agent choice relationships once instead of asking for duplicates.

    The Agent still chooses groups, relationship kinds, endpoints and gaps.
    Binding IDs and copying their exact parameters is deterministic lowering.
    """
    from scene_spatial import SpatialRelation
    result=spatial.model_copy(deep=True)
    objects={o.id:o for o in outline.objects}
    bindings={b.group_id:b for b in result.composition_groups}
    uses={choice:sum(choice in g.choice_ids for g in design.composition.groups)
          for choice in outline.design_bindings}
    typed_ids={r.id for g in design.composition.groups for r in g.relationships}
    typed_signatures=set()
    for group in design.composition.groups:
        for intended in group.relationships:
            a=outline.design_bindings.get(intended.a_choice_id,[])
            b=outline.design_bindings.get(intended.b_choice_id,[])
            pairs=([(x,y) for x in a for y in b] if min(len(a),len(b))==1 else list(zip(a,b)))
            typed_signatures.update((intended.kind,x,y) for x,y in pairs if x!=y)
    result.relations=[r for r in result.relations if
        r.a!=r.b and r.id not in typed_ids and r.design_relation_id not in typed_ids and
        (r.kind,r.a,r.b) not in typed_signatures]
    known={r.id for r in result.relations}
    groups=[]
    for group in design.composition.groups:
        eligible={id for choice in group.choice_ids for id in outline.design_bindings.get(choice,[])}
        required={id for choice in group.choice_ids if uses[choice]==1
                  for id in outline.design_bindings.get(choice,[])}
        prior=bindings.get(group.id)
        members=(set(prior.component_ids)&eligible if prior else set())|required
        for choice in group.choice_ids:
            candidates=outline.design_bindings.get(choice,[])
            if not members.intersection(candidates):members.update(candidates)
        anchors=[id for id in outline.design_bindings[group.anchor_choice_id] if id in members]
        anchor=prior.anchor_id if prior and prior.anchor_id in anchors else anchors[0]
        relation_ids=[rid for rid in (prior.relation_ids if prior else []) if rid in known]
        for intended in group.relationships:
            if intended.kind in {'supported_by','attached_to'}:continue
            a=[id for id in outline.design_bindings[intended.a_choice_id] if id in members]
            b=[id for id in outline.design_bindings[intended.b_choice_id] if id in members]
            # Single-to-many relations apply to each instance. For two plural
            # choices preserve Agent-specified pairings, otherwise pair by the
            # existing inventory order rather than invent all cross-products.
            pairs=([(x,y) for x in a for y in b] if min(len(a),len(b))==1 else list(zip(a,b)))
            for x,y in pairs:
                if x==y:continue
                identity=intended.id+'__'+x+'__'+y
                rid=identity if len(identity)<=64 else intended.id[:44]+'_'+hashlib.sha256(identity.encode()).hexdigest()[:18]
                result.relations.append(SpatialRelation(id=rid,a=x,b=y,kind=intended.kind,
                    minimum_m=intended.minimum_m,maximum_m=intended.maximum_m,
                    tolerance_deg=intended.tolerance_deg,design_relation_id=intended.id,reason=intended.reason))
                relation_ids.append(rid);known.add(rid)
        groups.append(GroupBinding(group_id=group.id,component_ids=[id for id in objects if id in members],
            anchor_id=anchor,relation_ids=list(dict.fromkeys(relation_ids))))
    result.composition_groups=groups
    # Existing cross-group spatial relations remain Agent-authored. Resolve
    # stale link references only when actual endpoint membership proves a link.
    group_map={g.group_id:set(g.component_ids) for g in groups}
    prior_links={link.link_id:link for link in result.composition_links}
    links=[]
    for link in design.composition.links:
        a=group_map[link.a];b=group_map[link.b]
        candidates=[r.id for r in result.relations if (r.a in a and r.b in b) or (r.b in a and r.a in b)]
        prior=prior_links.get(link.id)
        selected=[rid for rid in (prior.relation_ids if prior else []) if rid in candidates]
        route_ids=[]
        if link.binding_mode=='circulation' or (not selected and not candidates):
            starts=_group_route_nodes(result,a)
            goals=_group_route_nodes(result,b)
            pending=[(node,[]) for node in sorted(starts)];seen=set(starts)
            while pending:
                node,path=pending.pop(0)
                if node in goals:
                    route_ids=path;break
                for route in result.routes:
                    other=route.end if route.start==node else route.start if route.end==node else None
                    if other is not None and other not in seen:
                        seen.add(other);pending.append((other,path+[route.id]))
        links.append(LinkBinding(link_id=link.id,relation_ids=selected or candidates,route_ids=route_ids))
    result.composition_links=links
    return result


def validate_lowering(spatial,design,outline):
    if design is None or design.composition is None:return spatial
    c=design.composition;d=Diagnostics('composition_lowering')
    def fail(code,message,path):d.add(code,message,paths=[path])
    groups={g.id:g for g in c.groups};bindings={b.group_id:b for b in spatial.composition_groups}
    if set(bindings)!=set(groups) or len(bindings)!=len(spatial.composition_groups):
        fail('group_coverage','Bind each design group exactly once','/composition_groups')
    relations={r.id:r for r in spatial.relations};objects={o.id:o for o in outline.objects}
    choice_uses={choice:sum(choice in g.choice_ids for g in groups.values())
                 for g in groups.values() for choice in g.choice_ids}
    all_members=set()
    for id,g in groups.items():
        b=bindings.get(id)
        if b is None:continue
        eligible={cid for choice in g.choice_ids for cid in outline.design_bindings.get(choice,[])}
        required={cid for choice in g.choice_ids if choice_uses[choice]==1
                  for cid in outline.design_bindings.get(choice,[])}
        expected=set(b.component_ids)
        all_members.update(expected)
        anchors=set(outline.design_bindings.get(g.anchor_choice_id,[]))
        # One design choice can supply distinct objects to several spatial
        # groups (e.g. local lights along a path). Do not force every instance
        # into every group and invent relationships between distant objects.
        missing_choices=[choice for choice in g.choice_ids
                         if not expected.intersection(outline.design_bindings.get(choice,[]))]
        if (not required<=expected or not expected<=eligible or not expected<=objects.keys()
                or len(expected)!=len(b.component_ids) or b.anchor_id not in anchors.intersection(expected)
                or missing_choices):
            fail('group_members',f'{id}: members must realize each group choice, include exclusive members '
                 f'{sorted(required)}, use only eligible IDs {sorted(eligible)}, and retain the anchor. '
                 f'Shared choices may partition their instances across groups; missing choices={missing_choices}.','/composition_groups')
        graph={cid:set() for cid in expected}
        for cid in expected:
            parent=objects[cid].parent if cid in objects else None
            if parent in graph:graph[cid].add(parent);graph[parent].add(cid)
        for rid in b.relation_ids:
            r=relations.get(rid)
            if r is None:fail('group_relation',f'{id}: unknown relation {rid}','/composition_groups');continue
            if r.a in graph and r.b in graph:graph[r.a].add(r.b);graph[r.b].add(r.a)
        for intended in g.relationships:
            a_ids=set(outline.design_bindings.get(intended.a_choice_id,[]))&expected
            b_ids=set(outline.design_bindings.get(intended.b_choice_id,[]))&expected
            if intended.kind in {'supported_by','attached_to'}:
                # Distinct design layers can lower into one aDSL object. Its
                # internal contact graph is validated by the recipe gate.
                realized=bool(a_ids&b_ids) or any(objects[a].parent==b for a in a_ids for b in b_ids)
                if not realized:
                    fail('design_relationship_missing',f'{id}/{intended.id}: expected {intended.kind} relation between bound objects {sorted(a_ids)} and {sorted(b_ids)} is not present in the parent graph.','/composition_groups')
                    continue
                for a in a_ids:
                    for b_id in b_ids:
                        if objects[a].parent==b_id:
                            graph[a].add(b_id);graph[b_id].add(a)
                continue
            a=[id for id in outline.design_bindings.get(intended.a_choice_id,[]) if id in expected]
            b_ids_ordered=[id for id in outline.design_bindings.get(intended.b_choice_id,[]) if id in expected]
            pairs=([(x,y) for x in a for y in b_ids_ordered]
                   if min(len(a),len(b_ids_ordered))==1 else list(zip(a,b_ids_ordered)))
            if not any(x!=y for x,y in pairs):
                # The recipe validates relationships among layers of one root.
                continue
            matches=[r for r in spatial.relations if r.design_relation_id==intended.id and
                     r.kind==intended.kind and r.a in a_ids and r.b in b_ids]
            if not matches:
                fail('design_relationship_missing',f'{id}/{intended.id}: lower the typed {intended.kind} relationship from {intended.a_choice_id} to {intended.b_choice_id} into SpatialRelation with matching design_relation_id and endpoints.','/relations')
                continue
            for relation in matches:
                if relation.id not in b.relation_ids:
                    fail('group_relation',f'{id}/{intended.id}: include lowered relation {relation.id} in the group binding.','/composition_groups')
                if (abs(relation.minimum_m-intended.minimum_m)>.001 or
                    relation.maximum_m!=intended.maximum_m or
                    (intended.kind=='facing' and abs(relation.tolerance_deg-intended.tolerance_deg)>.1)):
                    fail('design_relationship_weakened',f'{id}/{intended.id}: spatial relation must preserve the designed minimum/maximum/tolerance.','/relations')
                graph[relation.a].add(relation.b);graph[relation.b].add(relation.a)
        reached={b.anchor_id};pending=list(reached)
        while pending:
            for cid in graph.get(pending.pop(),set())-reached:reached.add(cid);pending.append(cid)
        if expected-reached:
            fail('unrelated_members',f'{id}: {sorted(expected-reached)} have no executable relationship/support path to group anchor {b.anchor_id}. Choose meaningful facing/distance/directional relations; labels and zone membership alone are insufficient.','/relations')
    designed_members={cid for choice in choice_uses for cid in outline.design_bindings.get(choice,[])}
    if designed_members-all_members:
        fail('ungrouped_members','All realized design components need a meaningful spatial group; missing: '+
             str(sorted(designed_members-all_members)),'/composition_groups')
    links={b.link_id:b for b in spatial.composition_links}
    if set(links)!={l.id for l in c.links} or len(links)!=len(spatial.composition_links):
        fail('link_coverage','Lower every designed group relationship exactly once','/composition_links')
    for l in c.links:
        if l.id not in links or l.a not in bindings or l.b not in bindings:continue
        a=set(bindings[l.a].component_ids);b=set(bindings[l.b].component_ids)
        route_map={r.id:r for r in spatial.routes}
        reached=_group_route_nodes(spatial,a)
        goals=_group_route_nodes(spatial,b)
        selected_routes=[route_map[rid] for rid in links[l.id].route_ids if rid in route_map]
        while True:
            expanded=reached|{endpoint for route in selected_routes if route.start in reached or route.end in reached
                              for endpoint in (route.start,route.end)}
            if expanded==reached:break
            reached=expanded
        routed=bool(selected_routes) and bool(reached.intersection(goals))
        linked=any((r:=relations.get(rid)) and ((r.a in a and r.b in b) or (r.b in a and r.a in b))
                   for rid in links[l.id].relation_ids)
        if l.binding_mode=='spatial' and not linked:
            fail('unrealized_group_link',f'{l.id}: spatial link needs a relation connecting the two intended groups','/composition_links')
        if l.binding_mode=='circulation' and not routed:
            fail('unrealized_group_link',f'{l.id}: circulation link needs a route connecting the two intended groups','/composition_links')
        if any(rid not in relations for rid in links[l.id].relation_ids):fail('unknown_link_relation',l.id+': unknown relation','/composition_links')
        if any(rid not in route_map for rid in links[l.id].route_ids):fail('unknown_link_route',l.id+': unknown route','/composition_links')
    for actual,expected,path in ((spatial.open_spaces,c.open_spaces,'/open_spaces'),(spatial.experience_views,c.views,'/experience_views')):
        if {x.intent_id for x in actual}!={x.id for x in expected} or len(actual)!=len(expected):
            fail('intent_coverage','Realize each designed space/view intent exactly once',path)
    d.raise_if_any();return spatial
