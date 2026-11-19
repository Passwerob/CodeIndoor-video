"""Detailed book bounds and support/edit contracts, rather than primitive counts."""
from pathlib import Path
import sys
import unittest
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from general_scene import compile_general,edit_general
from part_details import BookDetail
from test_asset_physics import shelf_plan


class BookSubassemblyTests(unittest.TestCase):
    def test_cover_envelope_and_rigid_edit(self):
        plan=shelf_plan()
        for p in plan.objects[0].parts[-2:]:
            p.primitive='book';p.book=BookDetail()
        scene,_,before=compile_general(plan)
        _,_,after=compile_general(edit_general(plan,'gap',.1))
        for name in ('book_low','book_high'):
            a=before['shelf_unit']['internal_geometry']['parts'][name]
            b=after['shelf_unit']['internal_geometry']['parts'][name]
            np.testing.assert_allclose(np.subtract(a['bounds'][1],a['bounds'][0]),a['size'])
            self.assertEqual(a['size'],b['size'])
        self.assertAlmostEqual(after['shelf_unit']['internal_geometry']['parts']['book_high']['bounds'][0][2]-before['shelf_unit']['internal_geometry']['parts']['book_high']['bounds'][0][2],.1)

    def test_horizontal_stack_support_uses_book_top(self):
        plan=shelf_plan();obj=plan.objects[0]
        lower=obj.parts[-2];lower.primitive='book';lower.book=BookDetail(orientation='flat');lower.size=(.28,.23,.04)
        upper=lower.model_copy(deep=True);upper.id='stack_top';upper.size=(.24,.20,.035);upper.parent=lower.id;upper.center=(0,0,0)
        obj.parts.append(upper)
        from asset_physics import PartRelation
        obj.assembly.relations.append(PartRelation(kind='supported_by',part=upper.id,target=lower.id,reason='Stack contact'))
        _,_,receipts=compile_general(plan)
        records=receipts[obj.id]['internal_geometry']['parts']
        self.assertAlmostEqual(records[upper.id]['bounds'][0][2],records[lower.id]['bounds'][1][2])
        upper.size=(.32,.20,.035)
        with self.assertRaisesRegex(ValueError,'support_footprint'):compile_general(plan)


if __name__=='__main__':unittest.main()
