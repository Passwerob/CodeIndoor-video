"""Re-evaluated assembly relations, compiled to ordinary aDSL Assets.

Z-up geometric constraints: this is not a rigid-body or material-stress solver.
Recipes retain dependencies; compile(overrides=...) rebuilds without scaling
rigid contents. Existing Asset serialization/export remains unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from numbers import Real

from .asset import Asset
from .bounds import shape_aabb
from .primitives import Cube
from .transforms import translate_shape


class RelationError(ValueError):
    """An unresolved, cyclic, or unsatisfied assembly relation."""


@dataclass(frozen=True)
class Expr:
    op: str
    args: tuple

    @staticmethod
    def value(value):
        return value if isinstance(value, Expr) else Expr('constant', (float(value),))

    @staticmethod
    def ref(name):
        return Expr('ref', (name,))

    def __add__(self, other): return Expr('add', (self, Expr.value(other)))
    def __radd__(self, other): return Expr.value(other) + self
    def __sub__(self, other): return Expr('sub', (self, Expr.value(other)))
    def __rsub__(self, other): return Expr.value(other) - self
    def __mul__(self, other): return Expr('mul', (self, Expr.value(other)))
    def __rmul__(self, other): return Expr.value(other) * self
    def __truediv__(self, other): return Expr('div', (self, Expr.value(other)))

    def to_dict(self):
        return {'op': self.op, 'args': [a.to_dict() if isinstance(a, Expr) else a for a in self.args]}


@dataclass
class AssemblyResult:
    scene: Asset
    parts: dict[str, Asset]
    values: dict[str, float]
    report: dict


class RelationAssembly:
    """A reusable dependency graph with span, attachment, support and fit rules.

    Part ids may contain '/' to create semantic Asset subassemblies. Parameters
    and datums are expressions; part bounds can also be expression inputs.
    Compiling always returns a fresh scene and leaves prior results untouched.
    """

    def __init__(self, label='Relational assembly'):
        self.label = label
        self.parameters = {}
        self.datums = {}
        self.recipes = {}
        self.rules = []

    def parameter(self, name, value):
        if name in self.parameters or name in self.datums:
            raise RelationError(f'Duplicate scalar: {name}')
        self.parameters[name] = float(value)
        return Expr.ref(name)

    def datum(self, name, expression):
        if name in self.parameters or name in self.datums:
            raise RelationError(f'Duplicate scalar: {name}')
        self.datums[name] = Expr.value(expression)
        return Expr.ref(name)

    def bound(self, part, axis='z', side='max'):
        if axis not in ('x', 'y', 'z') or side not in ('min', 'max'):
            raise RelationError('Bounds require axis x/y/z and side min/max')
        return Expr('bound', (part, axis, side))

    def _add(self, name, recipe):
        if not name or any(not token for token in name.split('/')):
            raise RelationError(f'Invalid part id: {name!r}')
        if name in self.recipes:
            raise RelationError(f'Duplicate part: {name}')
        if any(p.startswith(name+'/') or name.startswith(p+'/') for p in self.recipes):
            raise RelationError(f'Part/group id conflict: {name}')
        self.recipes[name] = recipe
        return name

    def box(self, name, size, center=(0,0,0), color=(.5,.5,.5)):
        if len(size)!=3 or len(center)!=3:
            raise RelationError('Box size and center require three coordinates')
        return self._add(name, dict(kind='box', size=tuple(size), center=tuple(center), color=color))

    def span_box(self, name, *, bottom, top, width, depth, x=0, y=0, color=(.5,.5,.5)):
        bottom, top = Expr.value(bottom), Expr.value(top)
        result = self.box(name, (width,depth,top-bottom), (x,y,(bottom+top)/2), color)
        self.recipes[name]['relation'] = 'span_between'
        return result

    def rigid(self, name, shape, translation=(0,0,0)):
        if len(translation)!=3:
            raise RelationError('Translation requires three coordinates')
        return self._add(name, dict(kind='rigid', shape=shape.copy(), translation=tuple(translation)))

    def attached(self, name, shape, *, to, anchor='center', target_anchor='center', offset=(0,0,0)):
        if len(offset)!=3:
            raise RelationError('Attachment offset requires three coordinates')
        return self._add(name, dict(kind='attached', shape=shape.copy(), target=to,
                                   anchor=anchor, target_anchor=target_anchor, offset=tuple(offset)))

    def supported(self, name, shape, *, on, gap=0):
        """Seat the AABB bottom on target top, retaining source X/Y and size.

        Positive gap represents an explicitly modeled spacer clearance.
        AABB footprint overlap is checked; stability/lean contact is not solved.
        """
        return self._add(name, dict(kind='supported', shape=shape.copy(), target=on, gap=gap))

    def within(self, part, lower, upper):
        if len(lower)!=3 or len(upper)!=3:
            raise RelationError('Containment requires three lower and upper coordinates')
        self.rules.append(dict(kind='within', part=part, lower=tuple(lower), upper=tuple(upper)))

    def compile(self, overrides=None, *, tolerance=1e-7):
        from .layout import align_anchors
        if not math.isfinite(tolerance) or tolerance<=0:
            raise RelationError('Tolerance must be positive and finite')
        params = dict(self.parameters)
        for name, value in (overrides or {}).items():
            if name not in params:
                raise RelationError(f'Unknown parameter override: {name}')
            params[name] = float(value)
        values, parts, visiting, checks = {}, {}, set(), []

        def enter(key):
            if key in visiting:
                raise RelationError(f'Cyclic dependency at {key}')
            visiting.add(key)

        def scalar(name):
            if name in values: return values[name]
            key='scalar:'+name; enter(key)
            if name in params: result=params[name]
            elif name in self.datums: result=evaluate(self.datums[name])
            else: raise RelationError(f'Unknown scalar: {name}')
            if not math.isfinite(result): raise RelationError(f'Non-finite scalar: {name}')
            values[name]=float(result);visiting.remove(key)
            return values[name]

        def evaluate(expr):
            if isinstance(expr, Real): result=float(expr)
            elif isinstance(expr, Expr):
                op,args=expr.op,expr.args
                if op=='constant': result=args[0]
                elif op=='ref': result=scalar(args[0])
                elif op=='bound':
                    low,high=shape_aabb(part(args[0]));result=(low if args[2]=='min' else high)['xyz'.index(args[1])]
                else:
                    a,b=map(evaluate,args)
                    if op=='add': result=a+b
                    elif op=='sub': result=a-b
                    elif op=='mul': result=a*b
                    elif op=='div':
                        if b==0: raise RelationError('Division by zero in relation')
                        result=a/b
                    else: raise RelationError(f'Unknown expression operation: {op}')
            else: raise RelationError(f'Invalid expression: {expr!r}')
            if not math.isfinite(result): raise RelationError('Non-finite relation value')
            return float(result)

        def vector(items): return tuple(evaluate(v) for v in items)

        def part(name):
            if name in parts: return parts[name]
            if name not in self.recipes: raise RelationError(f'Unknown part: {name}')
            key='part:'+name;enter(key);r=self.recipes[name];kind=r['kind']
            if kind=='box':
                size=vector(r['size'])
                if min(size)<=0: raise RelationError(f'Non-positive span or dimension: {name}: {size}')
                shape=Cube(size, center=vector(r['center']), color=r['color'])
            elif kind=='rigid': shape=translate_shape(r['shape'],vector(r['translation']))
            elif kind=='attached':
                shape=align_anchors(r['shape'],part(r['target']),anchor=r['anchor'],
                                    target_anchor=r['target_anchor'],offset=vector(r['offset']))
                checks.append(dict(kind='attached',part=name,target=r['target']))
            elif kind=='supported':
                target=part(r['target']);low,high=shape_aabb(target);src_low,_=shape_aabb(r['shape'])
                gap=evaluate(r['gap'])
                if gap<0: raise RelationError(f'Negative support gap: {name}')
                shape=translate_shape(r['shape'],(0,0,float(high[2])-float(src_low[2])+gap))
                a,b=shape_aabb(shape)
                if any(min(float(b[i]),float(high[i]))-max(float(a[i]),float(low[i]))<=tolerance for i in (0,1)):
                    raise RelationError(f'Support footprint misses target: {name} -> {r["target"]}')
                if abs(float(a[2])-float(high[2])-gap)>tolerance:
                    raise RelationError(f'Support contact failed: {name}')
                checks.append(dict(kind='supported',part=name,target=r['target'],gap=gap))
            else: raise RelationError(f'Unknown recipe: {kind}')
            low,high=shape_aabb(shape)
            if not all(math.isfinite(float(v)) for v in (*low,*high)):
                raise RelationError(f'Non-finite geometry: {name}')
            parts[name]=shape;visiting.remove(key)
            return shape

        for name in params: scalar(name)
        for name in self.datums: scalar(name)
        for name in self.recipes: part(name)
        for rule in self.rules:
            name=rule['part'];low,high=shape_aabb(part(name));lower=vector(rule['lower']);upper=vector(rule['upper'])
            if any(lower[i]>upper[i] or low[i]<lower[i]-tolerance or high[i]>upper[i]+tolerance for i in range(3)):
                raise RelationError(f'Containment failed (part does not fit): {name}')
            checks.append(dict(kind='within',part=name))
        root=Asset(self.label);groups={'':root}
        for name in self.recipes:
            tokens=name.split('/');parent=''
            for token in tokens[:-1]:
                key=parent+'/'+token if parent else token
                if key not in groups: groups[key]=groups[parent].attach_part(token,Asset(token))
                parent=key
            groups[parent].attach_part(tokens[-1],parts[name])
        def encode(value):
            if isinstance(value,Expr): return value.to_dict()
            if isinstance(value,Asset):
                primitives = list(value.iter_primitives())
                return {
                    'label': value.label,
                    'primitive_count': len(primitives),
                    'primitive_types': [p.get('type') for p in primitives],
                    'external_meshes': [
                        p.get('params', {}).get('path')
                        for p in primitives if p.get('type') == 'external_mesh'
                    ],
                }
            if isinstance(value,(tuple,list)): return [encode(v) for v in value]
            if isinstance(value,dict): return {k:encode(v) for k,v in value.items()}
            return value
        report=dict(schema='adsl.relations.v1',parameters=params,datums=values,
                    recipes=encode(self.recipes),rules=encode(self.rules),checks=checks,
                    evaluation='rebuild on compile; no automatic Blender edit-time solving',
                    scope='Z-up AABB support, explicit attachment, span and containment; no gravity/stress solver')
        return AssemblyResult(root,parts,values,report)
