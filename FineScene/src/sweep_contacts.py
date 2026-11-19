"""Geometric distance for curved parts: circular sweeps (unions of capsules,
tapered ones split conservatively) and lathe solids of revolution.

These checks concern contact geometry only, not fastening strength or stability.
No envelope-only contact may certify a curved attachment. Lathe distances are
measured on sampled surfaces against exact signed-distance functions; the
sampling (<= 3 mm along the profile, 48 azimuths) bounds the error well below
the 2 mm contact tolerance.
"""
import math

import numpy as np
from math import hypot

from curve_presets import (box_sdf, capsules, cylinder_sdf, lathe_sdf,
                           lathe_surface_samples)


def segment_box_distance(start, end, half):
    """Minimum signed point-to-AABB distance along a segment in box coordinates."""
    start,end,half=map(lambda x:np.asarray(x,dtype=float),(start,end,half))
    direction=end-start
    cuts=[0.,1.]
    for axis in range(3):
        if abs(direction[axis])>1e-12:
            cuts.extend(t for face in (-half[axis],half[axis])
                        if 0<(t:=(face-start[axis])/direction[axis])<1)
    cuts=sorted(set(cuts));candidates=list(cuts)
    for lo,hi in zip(cuts,cuts[1:]):
        middle=start+direction*(lo+hi)/2
        outside=np.abs(middle)>half
        if np.any(outside):
            # Distance squared is quadratic between crossings of the box faces.
            a=direction[outside]
            b=start[outside]-np.sign(middle[outside])*half[outside]
            if np.dot(a,a)>1e-20:
                candidates.append(float(np.clip(-np.dot(a,b)/np.dot(a,a),lo,hi)))
        else:
            # Inside distance is the maximum of six affine signed face distances.
            slopes=np.concatenate([direction,-direction])
            intercepts=np.concatenate([start-half,-start-half])
            for i in range(6):
                for j in range(i):
                    if abs(slopes[i]-slopes[j])>1e-12:
                        t=(intercepts[j]-intercepts[i])/(slopes[i]-slopes[j])
                        if lo<t<hi:candidates.append(float(t))
    def distance(t):
        q=np.abs(start+direction*t)-half
        return float(np.linalg.norm(np.maximum(q,0))+min(float(q.max()),0))
    return min(map(distance,candidates))


def segment_distance(a,b,c,d):
    """Closest distance between two finite nondegenerate line segments."""
    a,b,c,d=map(lambda x:np.asarray(x,dtype=float),(a,b,c,d))
    u,v,w=b-a,d-c,a-c
    uu,vv,uv,uw,vw=map(float,(u@u,v@v,u@v,u@w,v@w))
    candidates=[]
    for s in (0.,1.):
        candidates.append((s,float(np.clip((vw+s*uv)/vv,0,1))))
    for t in (0.,1.):
        candidates.append((float(np.clip((t*uv-uw)/uu,0,1)),t))
    determinant=uu*vv-uv*uv
    if determinant>1e-12*uu*vv:
        s=(uv*vw-vv*uw)/determinant;t=(uu*vw-uv*uw)/determinant
        if 0<=s<=1 and 0<=t<=1:candidates.append((s,t))
    return min(float(np.linalg.norm(w+s*u-t*v)) for s,t in candidates)


def segment_cylinder_distance(start,end,radius,half_height):
    """Minimum signed distance to a finite circular cylinder in its local frame.

    The cylinder's signed distance is convex along the segment. Golden-section
    minimization includes both endpoints and retains a bracket after 72 steps,
    whose spatial length is below 1e-14 of the segment length. Caps, curved sides
    and their circular edges all use the actual surface, not a rectangular proxy.
    """
    start,end=np.asarray(start,dtype=float),np.asarray(end,dtype=float)
    x,y,z=map(float,start);dx,dy,dz=map(float,end-start)
    def distance(t):
        radial=hypot(x+t*dx,y+t*dy)-radius
        axial=abs(z+t*dz)-half_height
        return hypot(max(radial,0.),max(axial,0.))+min(max(radial,axial),0.)
    lo,hi=0.,1.;ratio=(np.sqrt(5.)-1.)/2
    left,right=hi-ratio*(hi-lo),lo+ratio*(hi-lo)
    fl,fr=distance(left),distance(right)
    for _ in range(72):
        if fl<=fr:
            hi,right,fr=right,left,fl
            left=hi-ratio*(hi-lo);fl=distance(left)
        else:
            lo,left,fl=left,right,fr
            right=lo+ratio*(hi-lo);fr=distance(right)
    return min(distance(0.),distance(1.),fl,fr)


def _circular_cylinder(record):
    return record.get('primitive')=='cylinder' and np.isclose(record['size'][0],record['size'][1],rtol=1e-9,atol=1e-12)


def _round_sphere(record):
    return record.get('primitive')=='sphere' and np.allclose(record['size'],record['size'][0],rtol=1e-9,atol=1e-12)


def _to_local(points,record):
    return (np.asarray(points,dtype=float)-np.asarray(record['center']))@np.asarray(record['rotation'])


def _to_world(points,record):
    return np.asarray(points,dtype=float)@np.asarray(record['rotation']).T+np.asarray(record['center'])


def _capsule_pair_gap(left,right):
    """Exact minimum over capsule pairs, pruned by a bounding-sphere lower bound."""
    mids_l=np.array([(a+b)/2 for a,b,_ in left]);half_l=np.array([np.linalg.norm(b-a)/2+r for a,b,r in left])
    mids_r=np.array([(a+b)/2 for a,b,_ in right]);half_r=np.array([np.linalg.norm(b-a)/2+r for a,b,r in right])
    bound=np.linalg.norm(mids_l[:,None]-mids_r[None],axis=2)-half_l[:,None]-half_r[None]
    order=np.argsort(bound,axis=None);best=math.inf
    for flat in order:
        i,j=np.unravel_index(flat,bound.shape)
        if bound[i,j]>=best:break
        a,b,ra=left[i];c,d,rb=right[j]
        best=min(best,segment_distance(a,b,c,d)-ra-rb)
    return best


def _capsule_samples(caps,step=.002):
    pts,rad=[],[]
    for a,b,r in caps:
        n=max(2,int(math.ceil(np.linalg.norm(b-a)/step))+1)
        t=np.linspace(0,1,n)[:,None]
        pts.append(a+(b-a)*t);rad.append(np.full(n,r))
    return np.concatenate(pts),np.concatenate(rad)


def _edge_samples(half,step=.004):
    half=np.asarray(half,dtype=float);out=[]
    for axis in range(3):
        o=[i for i in range(3) if i!=axis]
        n=max(2,int(math.ceil(2*half[axis]/step))+1)
        for su in (-1,1):
            for sv in (-1,1):
                pts=np.zeros((n,3));pts[:,axis]=np.linspace(-half[axis],half[axis],n)
                pts[:,o[0]]=su*half[o[0]];pts[:,o[1]]=sv*half[o[1]];out.append(pts)
    return np.concatenate(out)


def _cylinder_rim_samples(radius,half_height,azimuth=96):
    a=np.linspace(0,2*np.pi,azimuth,endpoint=False)
    rings=[np.stack([radius*np.cos(a),radius*np.sin(a),np.full(azimuth,z)],axis=1) for z in (-half_height,half_height)]
    return np.concatenate(rings)


def _lathe_gap(a,b):
    """a is a lathe record; b any record. None when the surface pair is unsupported."""
    profile=np.asarray(a['profile'],dtype=float)
    a_sdf=lambda world:lathe_sdf(_to_local(world,a),profile)
    a_surface=lambda:_to_world(lathe_surface_samples(profile),a)
    kind=b.get('primitive')
    if kind=='sweep':
        pts,rad=_capsule_samples(capsules(b))
        return float(np.min(a_sdf(pts)-rad))
    if _round_sphere(b):
        return float(a_sdf(np.asarray(b['center'],dtype=float)[None])[0]-b['size'][0]/2)
    if kind in {'box','book'}:
        half=np.asarray(b['size'],dtype=float)/2
        # Any overlap puts a lathe surface point inside the box or a box edge
        # inside the lathe, so both directions together detect penetration.
        return float(min(box_sdf(_to_local(a_surface(),b),half).min(),
                         a_sdf(_to_world(_edge_samples(half),b)).min()))
    if _circular_cylinder(b):
        radius,half_height=b['size'][0]/2,b['size'][2]/2
        return float(min(cylinder_sdf(_to_local(a_surface(),b),radius,half_height).min(),
                         a_sdf(_to_world(_cylinder_rim_samples(radius,half_height),b)).min()))
    if kind=='lathe':
        b_profile=np.asarray(b['profile'],dtype=float)
        b_surface=_to_world(lathe_surface_samples(b_profile),b)
        return float(min(lathe_sdf(_to_local(a_surface(),b),b_profile).min(),a_sdf(b_surface).min()))
    return None


def sweep_gap(a,b):
    """Signed gap of curved parts (sweeps, lathes) against supported surfaces.

    Sweeps: against boxes, circular cylinders, other sweeps and lathes.
    Lathes: against boxes, circular cylinders, round spheres, sweeps and lathes.
    Returns None for unsupported surface combinations; callers must fail closed
    on attachments and may retain conservative envelopes for collision rejection.
    """
    if a.get('primitive')!='lathe' and b.get('primitive')=='lathe':a,b=b,a
    if a.get('primitive')=='lathe':return _lathe_gap(a,b)
    if a.get('primitive')!='sweep':a,b=b,a
    if a.get('primitive')!='sweep':return None
    caps=capsules(a)
    if b.get('primitive') in {'box','book'}:
        half=np.asarray(b['size'])/2
        return min(segment_box_distance(x,y,half)-r
                   for x,y,r in ((*_to_local([p,q],b),r) for p,q,r in caps))
    if _circular_cylinder(b):
        return min(segment_cylinder_distance(x,y,b['size'][0]/2,b['size'][2]/2)-r
                   for x,y,r in ((*_to_local([p,q],b),r) for p,q,r in caps))
    if b.get('primitive')=='sweep':
        return _capsule_pair_gap(caps,capsules(b))
    return None
