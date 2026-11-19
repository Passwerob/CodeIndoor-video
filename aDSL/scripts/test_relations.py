"""Regression checks for geometric dependency propagation and v1 preservation."""
import hashlib
import importlib.util
import itertools
from pathlib import Path
import sys
import unittest
import numpy as np
from adsl.core import Cube, Expr, ExternalMesh, RelationAssembly, RelationError, shape_aabb

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'examples/physical_bookshelf'))
import model as bookshelf

def bounds(shape):return np.array(shape_aabb(shape))

def frozen():
    spec=importlib.util.spec_from_file_location('v1_frozen',ROOT/'examples/counterexamples/vintage_bookshelf_v1/model.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module

def leaves(asset,prefix=''):
    result={}
    for name,child in asset._children.items():
        key=f'{prefix}/{name}' if prefix else name
        if child._primitives:result[key]=child
        result.update(leaves(child,key))
    return result

class RelationTests(unittest.TestCase):
    def test_external_mesh_is_a_rigid_supported_component(self):
        source = ROOT/'research/trellis_small_components_20261017/models/chess_queen.glb'
        mesh = ExternalMesh(source, ((-.23256,-.23328,-.50007),(.23195,.23225,.50009)))
        assembly = RelationAssembly('Mesh assembly')
        assembly.box('shelf',(1,1,.04),(0,0,.02))
        assembly.supported('queen',mesh,on='shelf')
        result = assembly.compile()
        low, high = bounds(result.parts['queen'])
        np.testing.assert_allclose(high-low,[.46451,.46553,1.00016],atol=1e-8)
        self.assertAlmostEqual(low[2],.04,places=8)
        recipe = result.report['recipes']['queen']
        self.assertEqual(recipe['kind'],'supported')
        self.assertEqual(recipe['shape']['primitive_types'],['external_mesh'])
        self.assertEqual(len(recipe['shape']['external_meshes']),1)

    def test_external_mesh_requires_existing_file_and_valid_bounds(self):
        with self.assertRaises(FileNotFoundError):
            ExternalMesh(ROOT/'missing.glb',((0,0,0),(1,1,1)))
        source = ROOT/'research/trellis_small_components_20261017/models/chess_queen.glb'
        with self.assertRaises(ValueError):
            ExternalMesh(source,((0,0,0),(0,1,1)))

    def test_support_and_span_propagate_without_scaling(self):
        a=RelationAssembly();h=a.parameter('height',1)
        a.box('shelf',(1,1,.1),(0,0,h-.05))
        a.span_box('side',bottom=0,top=a.bound('shelf'),width=.1,depth=1)
        a.supported('book',Cube((.1,.2,.3)),on='shelf')
        low=a.compile();high=a.compile({'height':1.7})
        np.testing.assert_allclose(bounds(high.parts['book'])-bounds(low.parts['book']),[[0,0,.7],[0,0,.7]])
        self.assertAlmostEqual(bounds(high.parts['side'])[1,2],1.7)
        self.assertAlmostEqual(bounds(low.parts['side'])[1,2],1)

    def test_support_dependency_order_and_stack(self):
        a=RelationAssembly()
        a.supported('top_book',Cube((.1,.2,.3)),on='bottom_book')
        a.supported('bottom_book',Cube((.1,.2,.05)),on='shelf')
        a.box('shelf',(1,1,.1),(0,0,.95))
        r=a.compile();self.assertAlmostEqual(bounds(r.parts['top_book'])[0,2],1.05)

    def test_attachment_follows_target(self):
        a=RelationAssembly();z=a.parameter('z',1)
        a.box('case',(1,1,z),(0,0,z/2))
        a.attached('badge',Cube((.1,.01,.04)),to='case',anchor='top',target_anchor='front_top')
        b=a.compile({'z':2});self.assertAlmostEqual(bounds(b.parts['badge'])[1,2],2)

    def test_rejects_invalid_graphs(self):
        a=RelationAssembly();a.datum('a',Expr.ref('b'));a.datum('b',Expr.ref('a'))
        with self.assertRaisesRegex(RelationError,'Cyclic'):a.compile()
        a=RelationAssembly();a.supported('book',Cube((1,1,1)),on='missing')
        with self.assertRaisesRegex(RelationError,'Unknown part'):a.compile()
        a=RelationAssembly();a.box('p',(1,1,a.bound('p')))
        with self.assertRaisesRegex(RelationError,'Cyclic'):a.compile()

    def test_invalid_values_and_nonpositive_span(self):
        a=RelationAssembly();h=a.parameter('h',1);a.span_box('panel',bottom=0,top=h,width=1,depth=.1)
        for value in (0,-1,float('nan'),float('inf')):
            with self.assertRaises(RelationError):a.compile({'h':value})
        with self.assertRaisesRegex(RelationError,'Unknown parameter'):a.compile({'typo':2})

    def test_rejects_unsupported_and_oversized_parts(self):
        a=RelationAssembly();a.box('shelf',(1,1,.1))
        a.supported('book',Cube((.1,.2,.3),center=(3,0,0)),on='shelf')
        with self.assertRaisesRegex(RelationError,'footprint'):a.compile()
        a=RelationAssembly();a.box('book',(.2,.2,.3));a.within('book',(-1,-1,-.1),(1,1,.1))
        with self.assertRaisesRegex(RelationError,'does not fit'):a.compile()

class BookshelfTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.old=frozen();cls.base,cls.books=bookshelf.build()

    def test_original_is_byte_identical_to_snapshot(self):
        old=ROOT/'outputs/vintage_bookshelf_20261017/model.py'
        snap=ROOT/'examples/counterexamples/vintage_bookshelf_v1/model.py'
        self.assertEqual(hashlib.sha256(old.read_bytes()).digest(),hashlib.sha256(snap.read_bytes()).digest())

    def test_default_recreates_every_original_primitive(self):
        old=leaves(self.old.scene);new=leaves(self.base.scene)
        self.assertEqual(set(old),set(new))
        for name in old:
            for a,b in zip(old[name]._primitives,new[name]._primitives):
                self.assertEqual(a['type'],b['type'],name)
                def points(p):
                    q=p['params']
                    if p['type']=='cube':
                        local=np.array(q['center'])+np.array(list(itertools.product((-.5,.5),repeat=3)))*q['scale']
                    else:
                        self.assertAlmostEqual(a['params']['radius'],b['params']['radius'])
                        local=np.array([q['p0'],q['p1']])
                    return (np.asarray(p['xform'])@np.column_stack((local,np.ones(len(local)))).T).T
                np.testing.assert_allclose(points(a),points(b),atol=1e-12,err_msg=name)

    def test_all_bays_plus_point_one(self):
        raised,books=bookshelf.build((.1,)*4)
        self.assertAlmostEqual(raised.values['overall_top'],2.247)
        np.testing.assert_allclose(bounds(raised.scene)[1]-bounds(self.base.scene)[1],[0,0,.4],atol=1e-12)
        for before,after in zip(self.books,books):
            np.testing.assert_allclose(np.array(after['bounds'])-before['bounds'],[[0,0,.1*(before['level']-1)]]*2,atol=1e-12)
            self.assertEqual(before['palette'],after['palette'])

    def test_each_bay_changes_only_its_dependents(self):
        for changed_bay in range(4):
            changes=[0.]*4;changes[changed_bay]=.1
            result,books=bookshelf.build(changes)
            self.assertAlmostEqual(result.values['overall_top'],1.947)
            for before,after in zip(self.books,books):
                dz=.1 if before['level']-1>changed_bay else 0
                np.testing.assert_allclose(np.array(after['bounds'])-before['bounds'],[[0,0,dz]]*2,atol=1e-12)

    def test_books_that_do_not_fit_fail(self):
        with self.assertRaisesRegex(RelationError,'does not fit'):bookshelf.build((-.2,0,0,0))

    def test_snapshot_exposes_old_height_parameter_failure(self):
        baseline=bounds(self.old.scene)
        source=(ROOT/'examples/counterexamples/vintage_bookshelf_v1/model.py').read_text(encoding='utf-8')
        changed=source.replace('height=1.84,','height=2.24,')
        namespace={'__name__':'counterexample','__file__':str(ROOT/'examples/counterexamples/vintage_bookshelf_v1/model.py')}
        exec(compile(changed,'v1_height_counterexample','exec'),namespace)
        self.assertEqual(namespace['P']['height'],2.24)
        np.testing.assert_array_equal(bounds(namespace['scene']),baseline)

if __name__=='__main__':unittest.main(verbosity=2)
