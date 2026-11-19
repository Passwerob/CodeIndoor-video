"""Whole-object consistency of parametric recipes.

Two generic contracts, both deterministic and category independent:

``like`` / ``mirror`` (per part)
    A part declared ``like=<source>`` is another copy of the same design: its
    design fields must repeat the source's verbatim (checked after evaluating
    expressions), so four legs, two arms or a row of balusters cannot drift
    apart, and an edit of the shared parameters changes every copy together.
    ``mirror`` ('x', 'y' or 'xy') makes the copy the mirror image of the source
    across the object's local YZ / XZ / both planes: the compiler mirrors the
    geometry (curve paths included) and the rotation, and the copy's center
    must be the mirrored source center (the exact value is reported).

``assembly.symmetry`` (per object)
    Declares that the whole compiled object is mirror symmetric. Every part
    must have a mirror partner (itself or another part) with the same
    primitive and matching mirrored geometry; unmatched parts are reported.
"""
from __future__ import annotations

from typing import Callable, Literal

import numpy as np

MirrorMode = Literal['none', 'x', 'y', 'xy']
MIRRORS = {'x': np.diag([-1.0, 1.0, 1.0]), 'y': np.diag([1.0, -1.0, 1.0]), 'xy': np.diag([-1.0, -1.0, 1.0])}
# Fields that define the design of a part (everything except where it is and
# what it is attached to). A copy must repeat them; rotation is mirrored.
DESIGN_FIELDS = ('primitive', 'size', 'rotation', 'color', 'finish', 'book', 'surface', 'form',
                 'path', 'radius', 'radius_end', 'closed', 'curve', 'profile')
SYMMETRY_TOLERANCE_M = 0.003


def check_copy_fields(part) -> None:
    """Schema rule shared by the planner PartSpec and the executable Part."""
    if part.mirror != 'none' and part.like is None:
        raise ValueError(f'{part.id}: mirror requires like=<source part id>')
    if part.like is not None and part.like == part.id:
        raise ValueError(f'{part.id}: a part cannot be a copy of itself')


def mirror_rotation(rotation, mode: str):
    """Euler angles of M R M for the axis reflection M (valid for any XYZ order)."""
    rx, ry, rz = rotation
    flip = {'x': (1, -1, -1), 'y': (-1, 1, -1), 'xy': (-1, -1, 1)}[mode]
    return tuple(_neg(a) if s < 0 else a for a, s in zip((rx, ry, rz), flip))


def _neg(value):
    if isinstance(value, str):
        return f'-({value})'
    return -value if value else value


def _dump(value):
    return value.model_dump() if hasattr(value, 'model_dump') else value


def _evaluated(field, value, scalar: Callable):
    """Comparable form of a design field: expressions evaluated where numeric."""
    if value is None or isinstance(value, bool):
        return value
    if field in ('size', 'rotation', 'color'):
        return tuple(round(float(scalar(v)), 9) for v in value)
    if field in ('radius', 'radius_end'):
        return round(float(scalar(value)), 9)
    if field == 'path':
        return tuple(tuple(round(float(scalar(c)), 9) for c in p) for p in value)
    return _dump(value)


def resolve_copies(obj, scalar: Callable):
    """Effective parts for ``like`` copies.

    Returns {part_id: (effective_part, mirror_matrix_or_None)} for every copy.
    Raises ValueError listing every inconsistency at once (field, source value,
    copy value, expected center), so one repair can fix all of them.
    """
    parts = {p.id: p for p in obj.parts}
    problems, resolved = [], {}
    for part in obj.parts:
        if part.like is None:
            continue
        source = parts.get(part.like)
        if source is None:
            problems.append(f'{part.id}: like={part.like} is not a part of {obj.id}')
            continue
        if source.like is not None:
            problems.append(f'{part.id}: like must name the original design ({source.like}), not another copy ({source.id})')
            continue
        for field in DESIGN_FIELDS:
            if field == 'rotation':
                continue
            try:
                a, b = _evaluated(field, getattr(source, field), scalar), _evaluated(field, getattr(part, field), scalar)
            except (ValueError, KeyError, TypeError, ZeroDivisionError, SyntaxError) as failure:
                problems.append(f'{part.id}.{field}: cannot evaluate ({failure})')
                continue
            if a != b:
                problems.append(f'{part.id}.{field} must repeat {source.id}.{field} verbatim '
                                f'(copy of the same design): source={_short(getattr(source, field))}, '
                                f'copy={_short(getattr(part, field))}')
        if _evaluated('rotation', part.rotation, scalar) != _evaluated('rotation', source.rotation, scalar):
            problems.append(f'{part.id}.rotation must repeat {source.id}.rotation {list(source.rotation)} verbatim; '
                            'mirror is applied to it by the compiler')
        mirror = None
        if part.mirror != 'none':
            mirror = MIRRORS[part.mirror]
            if part.parent or source.parent or part.fit_between or source.fit_between:
                problems.append(f'{part.id}: mirrored copies are placed in the object frame; neither the copy nor '
                                f'{source.id} may use parent or fit_between (use a plain copy, mirror="none")')
            try:
                expected = mirror @ np.array([float(scalar(v)) for v in source.center])
                actual = np.array([float(scalar(v)) for v in part.center])
                if np.max(np.abs(expected - actual)) > 1e-4:
                    problems.append(f'{part.id}.center must be {source.id}.center mirrored ({part.mirror}): '
                                    f'expected {np.round(expected, 6).tolist()}, got {np.round(actual, 6).tolist()}')
            except (ValueError, KeyError, TypeError, ZeroDivisionError, SyntaxError) as failure:
                problems.append(f'{part.id}.center: cannot evaluate ({failure})')
        rotation = mirror_rotation(source.rotation, part.mirror) if mirror is not None else source.rotation
        effective = source.model_copy(update={
            'id': part.id, 'center': part.center, 'rotation': rotation, 'parent': part.parent,
            'mount': part.mount, 'fit_between': part.fit_between, 'semantic': part.semantic or source.semantic,
            'rigidity': part.rigidity, 'like': part.like, 'mirror': part.mirror})
        resolved[part.id] = (effective, mirror)
    if problems:
        raise ValueError(f'{obj.id}: inconsistent part copies: ' + '; '.join(problems))
    return resolved


def _short(value):
    text = repr(_dump(value))
    return text if len(text) <= 160 else text[:157] + '...'


def _mirrored_signature(record, M):
    """Geometry of a compiled part record after reflection M, for matching."""
    sig = {'primitive': record['primitive'],
           'bounds': np.sort(np.stack([np.asarray(record['bounds'][0]) @ M, np.asarray(record['bounds'][1]) @ M]), axis=0)}
    if record.get('path') is not None:
        sig['path'] = np.asarray(record['path'], dtype=float) @ M
        sig['radii'] = np.asarray(record.get('radii') or [record['radius']] * len(record['path']), dtype=float)
    return sig


def _signature(record):
    return _mirrored_signature(record, np.eye(3))


def _path_distance(a, b):
    """Symmetric Hausdorff distance between two sampled paths (direction-free)."""
    d = np.linalg.norm(a[:, None] - b[None], axis=2)
    return max(d.min(axis=1).max(), d.min(axis=0).max())


def _matches(mirrored, other, tol):
    if mirrored['primitive'] != other['primitive']:
        return False
    if np.max(np.abs(mirrored['bounds'] - other['bounds'])) > tol:
        return False
    if 'path' in mirrored or 'path' in other:
        if 'path' not in mirrored or 'path' not in other:
            return False
        if _path_distance(mirrored['path'], other['path']) > tol:
            return False
        if abs(mirrored['radii'].max() - other['radii'].max()) > tol or abs(mirrored['radii'].min() - other['radii'].min()) > tol:
            return False
    return True


def audit_symmetry(records: dict, mode: str, tol: float = SYMMETRY_TOLERANCE_M):
    """Parts without a mirror partner under ``mode``; [] when the object is symmetric.

    Matching uses the compiled geometry (bounds, and sweep paths/radii), so it
    holds for copies made by ``like``/``mirror`` and for hand-written parts.
    Lathes, boxes, cylinders and spheres are compared by their bounds.
    """
    if mode in (None, 'none'):
        return []
    planes = ['x', 'y'] if mode == 'xy' else [mode]
    unmatched = []
    for plane in planes:
        M = MIRRORS[plane]
        signatures = {pid: _signature(r) for pid, r in records.items()}
        for pid, record in records.items():
            mirrored = _mirrored_signature(record, M)
            if not any(_matches(mirrored, sig, tol) for sig in signatures.values()):
                unmatched.append({'part': pid, 'plane': plane,
                                  'mirrored_bounds': np.round(mirrored['bounds'], 4).tolist()})
    return unmatched
