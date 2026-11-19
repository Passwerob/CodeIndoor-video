"""Numerical placement/routing for an Agent-authored spatial design.

Fixed yaws and conservative boxes make linear solving practical. Unsatisfiable
means this particular representation/choice set needs review, not that all room
designs are impossible. Every returned candidate passes the production preflight.
"""
from __future__ import annotations
import heapq
import math
import time


JOINT_ROUTE_SEGMENTS=8
import numpy as np
from general_scene import scalar
from scene_spatial import (_rotation,_rectangle,_segment_polygon_distance,opening_polygon,
                           coarse_layout,distributed_architecture_envelope_ids)


def _frames(outline,layout):
    bounds=coarse_layout(outline,layout,None)['bounds']
    by={o.id:o for o in outline.objects};placements={p.id:p for p in layout.placements};yaws={}
    def yaw(id):
        if id not in yaws:yaws[id]=placements[id].yaw_deg+(yaw(by[id].parent) if by[id].parent else 0)
        return yaws[id]
    return bounds,{id:(yaw(id),np.r_[(np.array(v[0][:2])+v[1][:2])/2,v[0][2]]) for id,v in bounds.items()}


def _compact_route(points):
    """Export a solved polyline without zero-length or redundant straight turns."""
    result=[]
    for index,point in enumerate(points):
        point=tuple(point)
        if result and np.linalg.norm(np.asarray(point)-result[-1])<.001:
            # Retain the actual endpoint when a symbolic bend collapses onto it.
            if index==len(points)-1 and len(result)>1:result[-1]=point
            continue
        while len(result)>1:
            first=np.asarray(result[-1])-result[-2]
            second=np.asarray(point)-result[-1]
            if abs(first[0]*second[1]-first[1]*second[0])>1e-9 or np.dot(first,second)<0:break
            result.pop()
        result.append(point)
    return result


def _envelope_polygons(outline,poses):
    return {obj.id:_rectangle(poses[obj.id][1][:2],
            [scalar(v,outline.parameters) for v in obj.dimensions[:2]],poses[obj.id][0])
            for obj in outline.objects}


def _route(outline,layout,spatial):
    """Route full-width corridors, validating every straight segment against boxes."""
    bounds,poses=_frames(outline,layout)
    envelope_exempt=distributed_architecture_envelope_ids(outline.objects,outline.extent,outline.parameters)
    half=np.array(outline.extent[:2])/2;areas={}
    footprints=_envelope_polygons(outline,poses)
    for area in spatial.access_areas:
        owner=next(o for o in outline.objects if o.id==area.owner_id)
        dims=[scalar(v,outline.parameters) for v in owner.dimensions]
        params={**outline.parameters,**dict(zip(('w','d','h'),dims))}
        center=layout.access_centers.get(area.id,area.center)
        offset=np.array([scalar(v,params) for v in center])
        yaw,origin=poses[area.owner_id]
        areas[area.id]=_rectangle(origin[:2]+_rotation(yaw)@offset,area.size,yaw)
    for opening in spatial.openings:areas[opening.id]=opening_polygon(opening,outline.extent)
    paths={}
    for route in spatial.routes:
        radius=route.width/2
        active=[o for o in outline.objects if o.collision_role not in {'shell','floor_covering'} and
                o.id not in envelope_exempt and
                min(bounds[o.id][1][2],route.height)-max(bounds[o.id][0][2],0)>.015]
        angled=[footprints[o.id] for o in active if abs(math.sin(math.radians(2*poses[o.id][0])))>1e-7]
        boxes=np.array([bounds[o.id] for o in active if abs(math.sin(math.radians(2*poses[o.id][0])))<=1e-7])
        lows=boxes[:,0,:2] if boxes.size else np.empty((0,2))
        highs=boxes[:,1,:2] if boxes.size else np.empty((0,2))
        corners=np.stack((lows,np.column_stack((lows[:,0],highs[:,1])),
                          highs,np.column_stack((highs[:,0],lows[:,1]))),axis=1)
        def safe(a,b):
            if np.any(np.minimum(a,b)-radius < -half) or np.any(np.maximum(a,b)+radius > half):return False
            if any(_segment_polygon_distance(a,b,polygon)<radius-1e-6 for polygon in angled):return False
            if not boxes.size:return True
            # Exact Euclidean capsule vs axis-aligned boxes, including rounded
            # turns. Inflated square obstacles falsely disconnect tight corners.
            delta=np.asarray(b)-a;parallel=np.abs(delta)<1e-10
            t1=(lows-a)/np.where(parallel,1.,delta);t2=(highs-a)/np.where(parallel,1.,delta)
            near=np.minimum(t1,t2);far=np.maximum(t1,t2)
            near[:,parallel]=-np.inf;far[:,parallel]=np.inf
            outside=np.any(parallel & ((a<lows)|(a>highs)),axis=1)
            hit=(np.maximum(near.max(axis=1),0)<=np.minimum(far.min(axis=1),1)) & ~outside
            if np.any(hit):return False
            endpoint_distance=np.minimum(np.sum(np.maximum(np.maximum(lows-a,a-highs),0)**2,axis=1),
                                         np.sum(np.maximum(np.maximum(lows-b,b-highs),0)**2,axis=1))
            length=float(np.dot(delta,delta))
            if length>1e-16:
                t=np.clip(np.sum((corners-a)*delta,axis=2)/length,0,1)
                corner_distance=np.min(np.sum((corners-a-t[:,:,None]*delta)**2,axis=2),axis=1)
                endpoint_distance=np.minimum(endpoint_distance,corner_distance)
            return bool(np.all(endpoint_distance>=(radius-1e-6)**2))
        def endpoint(polygon):
            center=polygon.mean(axis=0)
            edge1=polygon[1]-polygon[0];edge2=polygon[3]-polygon[0]
            points=[center]+[center+u*edge1+v*edge2 for u in (-.4,-.2,0,.2,.4) for v in (-.4,-.2,0,.2,.4)]
            valid=[p for p in points if safe(p,p)]
            return min(valid,key=lambda p:np.linalg.norm(p-center)) if valid else None
        start=endpoint(areas[route.start]);goal=endpoint(areas[route.end])
        if start is None or goal is None:
            return None,{'route':route.id,'reason':'No endpoint inside the named area has full required corridor clearance.'}
        if not safe(start,start) or not safe(goal,goal):
            return None,{'route':route.id,'reason':'An endpoint lacks the full required corridor radius.',
                         'start':start.tolist(),'goal':goal.tolist()}
        if safe(start,goal):paths[route.id]=[tuple(start),tuple(goal)];continue
        step=.12;nx,ny=[int(2*v/step)+1 for v in half]
        def position(node):return -half+np.array(node)*step
        def nearest(point):return tuple(np.rint((point+half)/step).astype(int))
        anchor=nearest(start)
        choices=[(anchor[0]+dx,anchor[1]+dy) for dx in range(-2,3) for dy in range(-2,3)]
        choices=[n for n in choices if 0<=n[0]<nx and 0<=n[1]<ny and safe(start,position(n))]
        if not choices:return None,{'route':route.id,'reason':'No unobstructed start-to-grid connection.'}
        first=min(choices,key=lambda n:np.linalg.norm(position(n)-start))
        queue=[(0,first)];cost={first:0};previous={};done=None
        while queue:
            _,node=heapq.heappop(queue);point=position(node)
            if safe(point,goal) and np.linalg.norm(point-goal)<step*2:done=node;break
            for dx,dy in ((1,0),(-1,0),(0,1),(0,-1),(1,1),(-1,-1),(1,-1),(-1,1)):
                nxt=(node[0]+dx,node[1]+dy)
                if not (0<=nxt[0]<nx and 0<=nxt[1]<ny):continue
                other=position(nxt)
                if not safe(point,other):continue
                value=cost[node]+np.linalg.norm(other-point)
                if value<cost.get(nxt,float('inf')):
                    cost[nxt]=value;previous[nxt]=node
                    heapq.heappush(queue,(value+np.linalg.norm(other-goal),nxt))
        if done is None:return None,{'route':route.id,'reason':'No full-width path in the current conservative obstacle field.'}
        points=[goal];cursor=done
        while cursor!=first:points.append(position(cursor));cursor=previous[cursor]
        points.extend([position(first),start]);points.reverse()
        # The short connection from the actual entrance/access center also matters.
        if not all(safe(a,b) for a,b in zip(points,points[1:])):
            return None,{'route':route.id,'reason':'Grid endpoint connection is obstructed.'}
        simplified=[points[0]];i=0
        while i<len(points)-1:
            j=len(points)-1
            while j>i+1 and not safe(points[i],points[j]):j-=1
            simplified.append(points[j]);i=j
        if len(simplified)>32:return None,{'route':route.id,'reason':'Path needs more than 32 validated segments.'}
        paths[route.id]=[tuple(p) for p in simplified]
    return paths,None


def _solve_fixed_layout(outline,layout,spatial,*,timeout_ms=12000,reserve_routes=False,joint_routes=False):
    started=time.monotonic()
    try:import z3
    except ImportError:return None,{'status':'unavailable','reason':'Install z3-solver from requirements-layout.txt'}
    from staged_planner import coarse_preflight,spatial_with_layout
    trial=layout.model_copy(deep=True)
    # Semantic access sides own their normal coordinate. Match the exact
    # contract that production preflight uses before building constraints.
    effective=spatial_with_layout(spatial,trial,outline.objects,outline.extent,validate=False)
    for area in effective.access_areas:
        if area.id in trial.access_centers:
            trial.access_centers[area.id]=area.center
    envelope_report=coarse_layout(outline,trial,None)
    unresolved=sorted({obj.id for obj in outline.objects}-set(envelope_report['bounds']))
    if unresolved:
        findings=[finding for finding in envelope_report['findings']
                  if set(finding.get('component_ids',())) & set(unresolved)]
        return None,{'backend':'z3-linear-fixed-yaw-and-grid-routing',
            'status':'candidate_needs_revision',
            'reason':'The candidate has unresolved world poses; repair its placement expressions before numerical solving.',
            'unresolved_components':unresolved,'findings':findings,
            'blocked_checks':envelope_report.get('blocked_checks',[]),
            'candidate':trial.model_dump(mode='json')}
    base_bounds,base_poses=_frames(outline,trial)
    objects={o.id:o for o in outline.objects};placements={p.id:p for p in trial.placements}
    envelope_exempt=distributed_architecture_envelope_ids(outline.objects,outline.extent,outline.parameters)
    # A support relation fixes height/contact, not the tangential coordinates
    # of a lamp, book or other independently placed child on that surface.
    body={id:id for id in objects}
    bodies=sorted(set(body.values()))
    variables={id:(z3.Real('dx_'+id),z3.Real('dy_'+id)) for id in bodies}
    solver=z3.Solver();solver.set(timeout=timeout_ms)
    all_constraints=[];tracked_constraints=[];labels={};counter=0
    def q(v):return z3.RealVal(str(round(float(v),7)))
    def add(label,*conditions):
        nonlocal counter
        expression=z3.And(*conditions);name='constraint_'+str(counter);counter+=1
        solver.assert_and_track(expression,z3.Bool(name));all_constraints.append(expression)
        tracked_constraints.append((name,expression));labels[name]=label
    def absolute(v):return z3.If(v>=0,v,-v)
    def maximum(*values):
        result=values[0]
        for v in values[1:]:result=z3.If(result>=v,result,v)
        return result
    route_variables={}
    if joint_routes:
        for route in spatial.routes:
            values={'nodes':[(z3.Real(route.id+'_x_0'),z3.Real(route.id+'_y_0'))]}
            values['nodes'].extend((z3.Real(route.id+f'_x_{index}'),
                                    z3.Real(route.id+f'_y_{index}'))
                                   for index in range(1,JOINT_ROUTE_SEGMENTS))
            values['nodes'].append((z3.Real(route.id+'_x_end'),z3.Real(route.id+'_y_end')))
            values['horizontal']=[z3.Bool(route.id+f'_horizontal_{index}')
                                  for index in range(JOINT_ROUTE_SEGMENTS)]
            route_variables[route.id]=values
    for id,(dx,dy) in variables.items():
        obj=objects[id]
        if getattr(obj,'surface_contact',None):
            # Real face position is resolved after recipes. Preserve the Agent's
            # coarse normal coordinate; shell-role fixtures may still slide
            # tangentially to satisfy their zone and scene layout.
            face=obj.surface_contact.parent_face
            if face[0] in 'xy':
                normal=_rotation(base_poses[obj.parent][0])@np.eye(2)['xy'.index(face[0])]
                parent_delta=variables[body[obj.parent]]
                add('preserve mounting normal pending real surface: '+id,
                    q(normal[0])*(dx-parent_delta[0])+q(normal[1])*(dy-parent_delta[1])==0)
        elif obj.collision_role=='shell':
            add('architectural shell stays fixed: '+id,dx==0,dy==0)
    # Repair owner-local approach offsets symbolically so subsequent size edits follow the edge.
    for area in spatial.access_areas:
        if area.side is not None:continue
        obj=objects[area.owner_id];yaw=base_poses[obj.id][0]
        dims=[scalar(v,outline.parameters) for v in obj.dimensions]
        params={**outline.parameters,**dict(zip(('w','d','h'),dims))}
        original=trial.access_centers.get(area.id,area.center)
        offset=[scalar(v,params) for v in original]
        axis=0 if abs(offset[0])>abs(offset[1]) else 1
        sign=-1 if offset[axis]<=0 else 1
        c,s=abs(math.cos(math.radians(yaw))),abs(math.sin(math.radians(yaw)))
        # Projection of the conservative world box onto this owner-local approach normal.
        coeff=(c*c+s*s,2*c*s) if axis==0 else (2*c*s,c*c+s*s)
        minimum=(coeff[0]*dims[0]+coeff[1]*dims[1])/2+area.size[axis]/2+.025
        if abs(offset[axis])<minimum:
            value=f'{sign}*({coeff[0]:.7f}*w/2+{coeff[1]:.7f}*d/2+{area.size[axis]/2+.025:.6f})'
            changed=list(original);changed[axis]=value;trial.access_centers[area.id]=tuple(changed)
    scenarios=[('baseline',outline)]+[(c.parameter,outline.model_copy(update={'parameters':{
        **outline.parameters,c.parameter:outline.parameters[c.parameter]+c.delta}})) for c in outline.edit_checks]
    for scenario,current in scenarios:
        bounds,poses=_frames(current,trial)
        footprints=_envelope_polygons(current,poses)
        def rect(id):
            a,b=bounds[id];dx,dy=variables[body[id]]
            return (q(a[0])+dx,q(a[1])+dy,q(b[0])+dx,q(b[1])+dy)
        def separated(a,b,margin=.002):
            m=q(margin)
            return z3.Or(a[2]+m<=b[0],b[2]+m<=a[0],a[3]+m<=b[1],b[3]+m<=a[1])
        def clear_polygon(polygon,shift,box):
            # Keep rotated approach polygons oriented; replacing these with
            # AABBs invents owner self-collisions even when SAT says clear.
            choices=[]
            edges=np.roll(polygon,-1,axis=0)-polygon
            axes=[np.array([1.,0]),np.array([0.,1])]
            axes.extend(np.array([-edge[1],edge[0]])/np.linalg.norm(edge) for edge in edges)
            for axis in axes:
                values=polygon@axis
                offset=q(axis[0])*shift[0]+q(axis[1])*shift[1]
                low=sum(q(axis[i])*box[i if axis[i]>=0 else i+2] for i in range(2))
                high=sum(q(axis[i])*box[i+2 if axis[i]>=0 else i] for i in range(2))
                choices.extend([q(values.max())+offset+q(.002)<=low,
                                high+q(.002)<=q(values.min())+offset])
            return z3.Or(*choices)
        def clear_envelopes(first,first_shift,second,second_shift):
            choices=[]
            for polygon in (first,second):
                for edge in np.roll(polygon,-1,axis=0)-polygon:
                    axis=np.array([-edge[1],edge[0]])/np.linalg.norm(edge)
                    a=first@axis;b=second@axis
                    shift=sum(q(axis[i])*(first_shift[i]-second_shift[i]) for i in range(2))
                    choices.extend([q(a.max())+shift+q(.002)<=q(b.min()),
                                    q(b.max())+q(.002)<=q(a.min())+shift])
            return z3.Or(*choices)
        def inside(a,low,high):return [a[i]>=q(low[i]) for i in range(2)]+[a[i+2]<=q(high[i]) for i in range(2)]
        half=np.array(current.extent[:2])/2
        for id in objects:
            add(f'{scenario} scene bounds: {id}',*inside(rect(id),-half,half))
        for opening in spatial.openings:
            if opening.kind!='entrance' or not opening.swing_leaf_ids:continue
            axis=0 if opening.wall.startswith('x') else 1
            tangent=1-axis;sign=-1 if opening.wall.endswith('min') else 1
            wall_plane=sign*half[axis]
            aperture_low=opening.offset-opening.width/2
            aperture_high=opening.offset+opening.width/2
            for leaf_id in opening.swing_leaf_ids:
                polygon=footprints[leaf_id]
                tangent_values=polygon[:,tangent]
                normal_values=polygon[:,axis]
                tangent_low=float(tangent_values.min());tangent_high=float(tangent_values.max())
                if tangent_high-tangent_low>.45:
                    add(f'{scenario} entrance swing leaf edge-on: {opening.id}/{leaf_id}',z3.BoolVal(False))
                    continue
                delta=variables[body[leaf_id]]
                tangent_delta=delta[tangent]
                normal_near=float(normal_values.max() if sign>0 else normal_values.min())
                wall_gap=(q(wall_plane-normal_near)-delta[axis] if sign>0 else
                          q(normal_near-wall_plane)+delta[axis])
                left_gap=q(aperture_low-tangent_high)-tangent_delta
                right_gap=q(tangent_low-aperture_high)+tangent_delta
                dimensions=[scalar(v,current.parameters) for v in objects[leaf_id].dimensions]
                long_axis=0 if dimensions[0]>=dimensions[1] else 1
                jambs=(aperture_low,aperture_high)
                hinge_coordinate=min(jambs,key=lambda value:abs(value-poses[leaf_id][1][tangent]))
                tangent_out=-1 if hinge_coordinate==aperture_low else 1
                inward=np.zeros(2);inward[axis]=-sign
                hinge=np.zeros(2);hinge[axis]=wall_plane
                hinge[tangent]=hinge_coordinate+tangent_out*(dimensions[1-long_axis]/2+.01)
                target_center=hinge+inward*(dimensions[long_axis]/2)
                origin=poses[leaf_id][1][:2]
                add(f'{scenario} entrance hinge stays at selected jamb: {opening.id}/{leaf_id}',
                    delta[0]==q(target_center[0]-origin[0]),
                    delta[1]==q(target_center[1]-origin[1]))
                add(f'{scenario} entrance swing leaf jamb and wall alignment: {opening.id}/{leaf_id}',
                    wall_gap>=q(-.05),wall_gap<=q(.20),
                    z3.Or(z3.And(left_gap>=q(-.10),left_gap<=q(.35)),
                          z3.And(right_gap>=q(-.10),right_gap<=q(.35))))
        for obj in current.objects:
            if not obj.parent or obj.mount not in {'top','bottom'}:continue
            parent=objects[obj.parent]
            parent_size=np.array([scalar(v,current.parameters) for v in parent.dimensions])
            size=np.array([scalar(v,current.parameters) for v in obj.dimensions])
            params={**current.parameters,**dict(zip(('w','d','h'),size))}
            footprint=np.array([scalar(v,params) for v in obj.support_footprint]) if obj.support_footprint else size[:2]
            parent_yaw,parent_origin=poses[parent.id];yaw,origin=poses[obj.id]
            transform=_rotation(-parent_yaw)
            local=transform@(origin[:2]-parent_origin[:2])
            contact_half=np.abs(_rotation(yaw-parent_yaw))@footprint/2
            delta=[variables[body[obj.id]][i]-variables[body[parent.id]][i] for i in range(2)]
            center=[q(local[i])+sum(q(transform[i,j])*delta[j] for j in range(2)) for i in range(2)]
            add(f'{scenario} support footprint: {obj.id}/{parent.id}',
                *[absolute(center[i])+q(contact_half[i])<=q(parent_size[i]/2+.001) for i in range(2)])
        for zone in spatial.zones:
            low=np.array(zone.center)-np.array(zone.size)/2;high=low+zone.size
            for id in zone.component_ids:add(f'{scenario} zone {zone.id}: {id}',*inside(rect(id),low,high))
        roots=[o for o in current.objects if o.parent is None and o.collision_role not in {'shell','floor_covering'}]
        for i,a in enumerate(roots):
            for b in roots[i+1:]:
                if min(bounds[a.id][1][2],bounds[b.id][1][2])-max(bounds[a.id][0][2],bounds[b.id][0][2])>.015:
                    add(f'{scenario} furniture separation: {a.id}/{b.id}',separated(rect(a.id),rect(b.id)))
        supported=[o for o in current.objects if o.parent and o.mount in {'top','bottom'}]
        for i,a in enumerate(supported):
            for b in supported[i+1:]:
                if a.parent==b.parent and min(bounds[a.id][1][2],bounds[b.id][1][2])-max(bounds[a.id][0][2],bounds[b.id][0][2])>.015:
                    add(f'{scenario} supported content separation: {a.id}/{b.id}',separated(rect(a.id),rect(b.id)))
        reserves=[]
        for area in spatial.access_areas:
            obj=objects[area.owner_id];dims=[scalar(v,current.parameters) for v in obj.dimensions]
            params={**current.parameters,**dict(zip(('w','d','h'),dims))}
            offset=np.array([scalar(v,params) for v in trial.access_centers.get(area.id,area.center)])
            yaw,origin=poses[obj.id];center=origin[:2]+_rotation(yaw)@offset
            polygon=_rectangle(center,area.size,yaw)
            dx,dy=variables[body[obj.id]]
            a,b=polygon.min(axis=0),polygon.max(axis=0)
            reserve=(q(a[0])+dx,q(a[1])+dy,q(b[0])+dx,q(b[1])+dy)
            reserves.append((area.id,reserve,0,area.height,polygon,(dx,dy),set()))
        for opening in spatial.openings:
            polygon=opening_polygon(opening,current.extent);a,b=polygon.min(axis=0),polygon.max(axis=0)
            reserves.append((opening.id,tuple(q(v) for v in (*a,*b)),opening.sill_height,opening.sill_height+opening.height,polygon,(q(0),q(0)),set(opening.fitted_component_ids)))
        for space in spatial.open_spaces:
            polygon=_rectangle(space.center,space.size);a,b=polygon.min(axis=0),polygon.max(axis=0)
            reserves.append(('design_empty_'+space.intent_id,tuple(q(v) for v in (*a,*b)),space.bottom,
                space.bottom+space.height,polygon,(q(0),q(0)),set()))
        for name,reserve,zlow,zhigh,polygon,shift,ignored in reserves:
            add(f'{scenario} usable approach boundary: {name}',*inside(reserve,-half,half))
            for obj in current.objects:
                if obj.collision_role in {'shell','floor_covering'} or obj.id in envelope_exempt or obj.id in ignored:continue
                if min(bounds[obj.id][1][2],zhigh)-max(bounds[obj.id][0][2],zlow)>.015:
                    add(f'{scenario} clear approach {name}: {obj.id}',
                        clear_envelopes(polygon,shift,footprints[obj.id],variables[body[obj.id]]))
        if joint_routes:
            reserve_map={name:(poly,shift) for name,_,_,_,poly,shift,_ in reserves}
            for route in spatial.routes:
                nodes=route_variables[route.id]['nodes'];radius=q(route.width/2+.001)
                add(f'{scenario} joint route connects distinct endpoints: {route.id}',
                    absolute(nodes[0][0]-nodes[-1][0])+absolute(nodes[0][1]-nodes[-1][1])>=q(.003))
                for endpoint,point in ((route.start,nodes[0]),(route.end,nodes[-1])):
                    polygon,shift=reserve_map[endpoint]
                    for a,b in zip(polygon,np.roll(polygon,-1,axis=0)):
                        edge=b-a
                        add(f'{scenario} joint endpoint {route.id}/{endpoint}',
                            q(edge[0])*(point[1]-q(a[1])-shift[1])-q(edge[1])*(point[0]-q(a[0])-shift[0])>=0)
                for index,(a,b) in enumerate(zip(nodes,nodes[1:])):
                    horizontal=route_variables[route.id]['horizontal'][index]
                    add(f'{scenario} joint route axis {route.id}/{index}',
                        z3.Implies(horizontal,a[1]==b[1]),
                        z3.Implies(z3.Not(horizontal),a[0]==b[0]))
                    corridor=(-maximum(-a[0],-b[0])-radius,-maximum(-a[1],-b[1])-radius,
                              maximum(a[0],b[0])+radius,maximum(a[1],b[1])+radius)
                    add(f'{scenario} joint corridor bounds {route.id}/{index}',*inside(corridor,-half,half))
                    for obj in current.objects:
                        if obj.collision_role not in {'shell','floor_covering'} and obj.id not in envelope_exempt and min(bounds[obj.id][1][2],route.height)-max(bounds[obj.id][0][2],0)>.015:
                            add(f'{scenario} joint full corridor {route.id}/{index}: {obj.id}',
                                clear_polygon(footprints[obj.id],variables[body[obj.id]],corridor))
        for relation in spatial.relations:
            a,b=rect(relation.a),rect(relation.b)
            ca=((a[0]+a[2])/2,(a[1]+a[3])/2);cb=((b[0]+b[2])/2,(b[1]+b[3])/2)
            gapx=maximum(q(0),a[0]-b[2],b[0]-a[2]);gapy=maximum(q(0),a[1]-b[3],b[1]-a[3])
            constraints=[]
            if relation.kind=='facing':
                front=_rotation(poses[relation.a][0])@np.array([0,-1.])
                vx,vy=cb[0]-ca[0],cb[1]-ca[1]
                dot=q(front[0])*vx+q(front[1])*vy
                cross=q(front[0])*vy-q(front[1])*vx
                constraints.append(dot>=q(.001))
                if relation.tolerance_deg<89.9:
                    constraints.append(absolute(cross)<=q(math.tan(math.radians(relation.tolerance_deg)))*dot)
            elif relation.kind!='distance':
                axis=_rotation(poses[relation.b][0])@np.array({'left_of':[-1,0],'right_of':[1,0],'in_front_of':[0,-1],'behind':[0,1]}[relation.kind])
                ap=footprints[relation.a]@axis;bp=footprints[relation.b]@axis
                projected=q(axis[0])*(ca[0]-cb[0])+q(axis[1])*(ca[1]-cb[1])
                half_a=(ap.max()-ap.min())/2;half_b=(bp.max()-bp.min())/2
                constraints.append(projected>=q(half_a+half_b+relation.minimum_m))
            if relation.kind in {'facing','distance'}:
                if relation.minimum_m:constraints.append(z3.Or(gapx>=q(relation.minimum_m),gapy>=q(relation.minimum_m)))
                if relation.maximum_m is not None:constraints.append(gapx+gapy<=q(relation.maximum_m))
            add(f'{scenario} functional relation {relation.id}',*constraints)
        if reserve_routes:
            routed=spatial_with_layout(spatial,trial,current.objects,current.extent,validate=False)
            reserve_map={name:(poly,shift) for name,_,_,_,poly,shift,_ in reserves}
            def hull(points):
                points=sorted(set(map(tuple,points)))
                def cross(o,a,b):return (a[0]-o[0])*(b[1]-o[1])-(a[1]-o[1])*(b[0]-o[0])
                low=[];high=[]
                for point in points:
                    while len(low)>1 and cross(low[-2],low[-1],point)<=0:low.pop()
                    low.append(point)
                for point in reversed(points):
                    while len(high)>1 and cross(high[-2],high[-1],point)<=0:high.pop()
                    high.append(point)
                return np.array(low[:-1]+high[:-1])
            for route in routed.routes:
                for endpoint,point in ((route.start,route.points[0]),(route.end,route.points[-1])):
                    poly,shift=reserve_map[endpoint]
                    for a,b in zip(poly,np.roll(poly,-1,axis=0)):
                        edge=b-a
                        add(f'{scenario} route endpoint {route.id}/{endpoint}',
                            q(edge[0])*(q(point[1]-a[1])-shift[1])-q(edge[1])*(q(point[0]-a[0])-shift[0])>=0)
                for index,(a,b) in enumerate(zip(route.points,route.points[1:])):
                    r=route.width/2+.003
                    poly=hull([np.array(point)+[x*r,y*r] for point in (a,b) for x in (-1,1) for y in (-1,1)])
                    for obj in current.objects:
                        if obj.collision_role not in {'shell','floor_covering'} and obj.id not in envelope_exempt and min(bounds[obj.id][1][2],route.height)-max(bounds[obj.id][0][2],0)>.015:
                            add(f'{scenario} full corridor {route.id}/{index}: {obj.id}',
                                clear_polygon(poly,(q(0),q(0)),rect(obj.id)))
    status=solver.check()
    receipt={'backend':'z3-linear-fixed-yaw-and-grid-routing','elapsed_seconds':round(time.monotonic()-started,3),
        'constraints':len(all_constraints),'limits':'Fixed Agent yaws, conservative AABBs and L1 distance bounds. An unsat result is not proof that all designs/yaws are impossible.'}
    if status!=z3.sat:
        conflicts=[]
        if status==z3.unsat:
            ignored=set();probe=solver
            deadline=time.monotonic()+timeout_ms/1000
            while True:
                core=[str(v) for v in probe.unsat_core()]
                if not core:break
                conflicts.append([labels[name] for name in core]);ignored.update(core)
                remaining=int((deadline-time.monotonic())*1000)
                if remaining<=0:break
                probe=z3.Solver();probe.set(timeout=remaining)
                for name,expression in tracked_constraints:
                    if name not in ignored:probe.assert_and_track(expression,z3.Bool(name))
                if probe.check()!=z3.unsat:break
        receipt.update(status='infeasible_current_choices' if status==z3.unsat else 'unknown',
            incompatible_constraints=list(dict.fromkeys(label for group in conflicts for label in group)),
            independent_conflict_groups=conflicts,
            reason=solver.reason_unknown() if status==z3.unknown else 'Revise the named spatial design choices or object yaws, not repeated position-only patches.')
        receipt['elapsed_seconds']=round(time.monotonic()-started,3)
        return None,receipt
    model=solver.model()
    optimizer=z3.Optimize();optimizer.set(timeout=min(timeout_ms,8000));optimizer.add(*all_constraints)
    optimizer.minimize(z3.Sum([absolute(v) for pair in variables.values() for v in pair]))
    if joint_routes:
        optimizer.minimize(z3.Sum([
            absolute(b[0]-a[0])+absolute(b[1]-a[1])
            for values in route_variables.values()
            for a,b in zip(values['nodes'],values['nodes'][1:])]))
    if optimizer.check()==z3.sat:model=optimizer.model()
    def number(v):
        value=model.eval(v,model_completion=True)
        return value.numerator_as_long()/value.denominator_as_long()
    deltas={id:np.array([number(v) for v in pair]) for id,pair in variables.items()}
    for obj in current.objects:
        placement=placements[obj.id];parent_delta=deltas[body[obj.parent]] if obj.parent else np.zeros(2)
        parent_yaw=base_poses[obj.parent][0] if obj.parent else 0
        change=_rotation(-parent_yaw)@(deltas[body[obj.id]]-parent_delta)
        value=list(placement.position)
        for i in range(2):
            if abs(change[i])<1e-8:continue
            value[i]=f'({value[i]})+({change[i]:.8f})' if isinstance(value[i],str) else float(value[i])+float(change[i])
        placement.position=tuple(value)
    routed=spatial_with_layout(spatial,trial,outline.objects,outline.extent,validate=False)
    if joint_routes:
        paths={id:_compact_route([tuple(number(v) for v in point) for point in values['nodes']])
               for id,values in route_variables.items()}
        error=None
    else:paths,error=_route(outline,trial,routed)
    if paths is None:
        receipt.update(status='routing_requires_revision',routing_failure=error,
                       candidate=trial.model_dump(mode='json'))
        return None,receipt
    trial.route_points=paths
    final=coarse_preflight(outline,trial,spatial)
    receipt.update(status='passed' if final['status']=='passed' else 'validation_failed',
                   elapsed_seconds=round(time.monotonic()-started,3),findings=final['findings'],issues=final.get('issues',[]))
    if final['status']!='passed':receipt['candidate']=trial.model_dump(mode='json')
    return (trial if final['status']=='passed' else None),receipt


def solve_layout(outline,layout,spatial,*,timeout_ms=12000):
    """Keep proposed yaws when feasible; derive an alternative from actual use.

    Facing relationships and wall approaches, rather than category templates,
    define the alternate orientation proposal. The full solver and independent
    preflight must still accept every pose and edit.
    """
    solved,receipt=_solve_fixed_layout(outline,layout,spatial,timeout_ms=timeout_ms)
    if solved is not None or receipt['status'] in {'unavailable','candidate_needs_revision'}:return solved,receipt
    if receipt['status']=='routing_requires_revision':
        routed,routing_receipt=_solve_fixed_layout(outline,layout,spatial,timeout_ms=max(timeout_ms,60000),joint_routes=True)
        if routed is not None:
            routing_receipt['routing_strategy']=f'Jointly solved movable full-width routes with up to {JOINT_ROUTE_SEGMENTS} orthogonal segments and furniture positions across all edits'
            return routed,routing_receipt
        receipt['joint_corridor_failure']={k:v for k,v in routing_receipt.items() if k!='candidate'}
    _,poses=_frames(outline,layout)
    trial=layout.model_copy(deep=True);placements={p.id:p for p in trial.placements}
    objects={o.id:o for o in outline.objects};changes={}
    facing={r.a:r for r in spatial.relations if r.kind=='facing'}
    access={a.owner_id:a for a in spatial.access_areas}
    swing_openings={leaf:opening for opening in spatial.openings
                    if opening.kind=='entrance'
                    for leaf in opening.swing_leaf_ids}
    for obj in outline.objects:
        if obj.collision_role=='shell' and not obj.surface_contact:continue
        old_yaw,origin=poses[obj.id]
        target=None;reason=None
        if obj.id in swing_openings:
            opening=swing_openings[obj.id]
            dimensions=[scalar(v,outline.parameters) for v in obj.dimensions]
            long_axis=0 if dimensions[0]>=dimensions[1] else 1
            local_axis_deg=(0. if long_axis==0 else 90.)+obj.acquisition.front_rotation_deg
            wall_axis=0 if opening.wall.startswith('x') else 1
            tangent_axis=1-wall_axis
            wall_sign=-1 if opening.wall.endswith('min') else 1
            inward=np.zeros(2);inward[wall_axis]=-wall_sign
            target=math.degrees(math.atan2(inward[1],inward[0]))-local_axis_deg
            half=np.asarray(outline.extent[:2],dtype=float)/2
            wall_plane=wall_sign*half[wall_axis]
            jambs=(opening.offset-opening.width/2,opening.offset+opening.width/2)
            hinge_coordinate=min(jambs,key=lambda value:abs(value-origin[tangent_axis]))
            hinge=np.zeros(2);hinge[wall_axis]=wall_plane;hinge[tangent_axis]=hinge_coordinate
            center=hinge+inward*(dimensions[long_axis]/2)
            parent_yaw,parent_origin=poses.get(obj.parent,(0.,np.zeros(3)))
            local_center=_rotation(-parent_yaw)@(center-parent_origin[:2])
            current_local=_rotation(-parent_yaw)@(origin[:2]-parent_origin[:2])
            local_yaw=(target-parent_yaw+180.)%360.-180.
            local_height=origin[2]-parent_origin[2]
            if (abs((target-old_yaw+180.)%360.-180.)>.01 or
                    np.linalg.norm(local_center-current_local)>.01 or
                    abs(local_height)>.01):
                placements[obj.id].yaw_deg=local_yaw
                placements[obj.id].position=tuple([*local_center,0.])
                changes[obj.id]={'from_world_deg':old_yaw,'to_world_deg':target,
                    'basis':'Swing the measured long leaf axis inward from its declared opening hinge.',
                    'hinge_world_xy_m':hinge.tolist()}
            continue
        if obj.surface_contact:
            from surface_mounting import face_info
            parent_normal=face_info(obj.surface_contact.parent_face)[2]
            child_normal=face_info(obj.surface_contact.child_face)[2]
            if abs(parent_normal[2])<.5 and abs(child_normal[2])<.5:
                relative=math.degrees(math.atan2(-parent_normal[1],-parent_normal[0])-
                                      math.atan2(child_normal[1],child_normal[0]))
                target=poses[obj.parent][0]+relative
                reason='Oppose the selected physical mounting faces on '+obj.parent
        elif obj.id in facing:
            relation=facing[obj.id];vector=poses[relation.b][1][:2]-origin[:2]
            if np.linalg.norm(vector)>.01:
                target=math.degrees(math.atan2(vector[1],vector[0]))+90
                cardinal=round(target/90)*90
                if abs((target-cardinal+180)%360-180)<relation.tolerance_deg*.8:target=cardinal
                reason='Face the actual related object '+relation.b
        elif obj.parent is None or objects[obj.parent].collision_role=='shell':
            dims=[scalar(v,outline.parameters) for v in obj.dimensions]
            ratios=np.abs(origin[:2])/(np.array(outline.extent[:2])/2)
            axis=int(np.argmax(ratios))
            if ratios[axis]>.62:
                # A thin wall fixture's normal is local -Y; an operating
                # area supplies its actual local approach direction otherwise.
                area=access.get(obj.id)
                if area or (obj.parent and dims[1]<min(dims[0],dims[2])*.45):local=np.array([0.,-1.])
                else:continue
                inward=np.zeros(2);inward[axis]=-1 if origin[axis]>0 else 1
                target=math.degrees(math.atan2(inward[1],inward[0])-math.atan2(local[1],local[0]))
                reason='Keep the declared approach/fixture normal inside the nearest room boundary'
        if target is not None and abs((target-old_yaw+180)%360-180)>.01:
            parent_yaw=poses[obj.parent][0] if obj.parent else 0
            placements[obj.id].yaw_deg=(target-parent_yaw+180)%360-180
            changes[obj.id]={'from_world_deg':old_yaw,'to_world_deg':target,'basis':reason}
    # An owner-local access center is a layout variable too. Reflect an
    # outward-facing approach into the room while retaining its dimensions.
    _,new_poses=_frames(outline,trial)
    access_changes={}
    for area in spatial.access_areas:
        if area.side is not None:continue
        obj=objects[area.owner_id];dims=[scalar(v,outline.parameters) for v in obj.dimensions]
        params={**outline.parameters,**dict(zip(('w','d','h'),dims))}
        original=trial.access_centers.get(area.id,area.center)
        offset=np.array([scalar(v,params) for v in original]);yaw,origin=new_poses[obj.id]
        polygon=_rectangle(origin[:2]+_rotation(yaw)@offset,area.size,yaw)
        half=np.array(outline.extent[:2])/2
        if np.any(polygon < -half) or np.any(polygon > half):
            reversed_polygon=_rectangle(origin[:2]-_rotation(yaw)@offset,area.size,yaw)
            def excess(p):return np.maximum(np.abs(p)-half,0).sum()
            if excess(reversed_polygon)+.001<excess(polygon):
                trial.access_centers[area.id]=tuple(f'-({v})' if isinstance(v,str) else -v for v in original)
                access_changes[area.id]='Opposite owner-local approach keeps the same human area inside the room'
    if not changes and not access_changes:return None,receipt
    alternate,second=_solve_fixed_layout(outline,trial,spatial,timeout_ms=timeout_ms)
    if alternate is None and second['status']=='routing_requires_revision':
        joint,joint_receipt=_solve_fixed_layout(outline,trial,spatial,timeout_ms=max(timeout_ms,60000),joint_routes=True)
        if joint is not None:alternate,second=joint,joint_receipt
        else:second['joint_corridor_failure']={k:v for k,v in joint_receipt.items() if k!='candidate'}
    second['orientation_proposals']=changes
    second['access_side_proposals']=access_changes
    second['previous_fixed_yaw_status']=receipt['status']
    if alternate is None:
        # Keep deterministic physical-face corrections in the next Agent seed
        # even when unrelated spatial constraints are still infeasible.
        second.setdefault('candidate',trial.model_dump(mode='json'))
    return alternate,second
