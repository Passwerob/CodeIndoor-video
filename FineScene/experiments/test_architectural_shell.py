import sys
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))

from architectural_shell import ShellProgram,compile_shell,shell_program_compatibility
from general_scene import GeneralPlan,SceneObject,Part,compile_local_geometry
from asset_physics import PartForm
from scene_spatial import SpatialContract


class ArchitecturalShellTests(unittest.TestCase):
    def test_extra_frozen_wall_fragments_use_general_recipe_capacity(self):
        obj=NS(internal_parts=[NS(id='segment_'+str(i),role='structure',
            form=PartForm(kind='panel',axis='y',minimum_ratio=2)) for i in range(3)])
        receipt=shell_program_compatibility(obj)
        self.assertFalse(receipt['supported'])
        self.assertEqual(receipt['independent_structural_panels']['y'],['segment_0','segment_1','segment_2'])

    def test_split_wall_ids_match_window_and_door_cells_and_ignore_attached_leaf(self):
        parts=[NS(id=axis+str(i),role='structure',form=PartForm(kind='panel',axis=axis,minimum_ratio=2),
                  attached_to=None) for axis,n in (('x',2),('y',7),('z',2)) for i in range(n)]
        parts.append(NS(id='door_leaf',role='structure',form=PartForm(kind='panel',axis='y',minimum_ratio=2),
                        attached_to='door_frame'))
        obj=NS(internal_parts=parts,dimensions=(4,4,3))
        spatial=NS(openings=[NS(wall='y_min',sill_height=0,height=2.1),
                              NS(wall='y_max',sill_height=.8,height=1.2)])
        outline=NS(parameters={})
        receipt=shell_program_compatibility(obj,spatial,outline)
        self.assertTrue(receipt['supported'])
        self.assertEqual(receipt['expected_panel_counts'],{'x':2,'y':7,'z':2})
        spatial.openings.append(NS(wall='y_max',sill_height=.5,height=1.0))
        self.assertFalse(shell_program_compatibility(obj,spatial,outline)['supported'])

    def test_legacy_successful_bedroom_wall_topology_matches_compiler(self):
        parts=[NS(id=axis+str(i),role='structure',form=PartForm(kind='panel',axis=axis,minimum_ratio=2),
                  attached_to=None) for axis,n in (('x',3),('y',4),('z',2)) for i in range(n)]
        parts.extend([
            NS(id='door_lintel',semantic='Horizontal door header above opening',role='structure',
               form=PartForm(kind='beam',axis='y',minimum_ratio=1.1),attached_to=None),
            NS(id='window_lintel',semantic='Horizontal window header above opening',role='structure',
               form=PartForm(kind='beam',axis='x',minimum_ratio=1.1),attached_to=None)])
        parts.append(NS(id='entry_door_child',role='structure',form=PartForm(kind='panel',axis='y',minimum_ratio=2),
                        attached_to='door_jamb'))
        obj=NS(internal_parts=parts,dimensions=(4.2,4.2,2.8))
        spatial=NS(openings=[NS(wall='x_min',sill_height=0,height=2.12),
                             NS(wall='y_max',sill_height=.85,height=1.4)])
        receipt=shell_program_compatibility(obj,spatial,NS(parameters={}))
        self.assertTrue(receipt['supported'])
        self.assertEqual(receipt['expected_panel_counts'],{'x':4,'y':5,'z':2})
        self.assertEqual({axis:len(ids) for axis,ids in receipt['independent_wall_members'].items()},
                         {'x':4,'y':5,'z':2})

    def test_attached_lintels_still_count_as_aperture_wall_cells(self):
        parts=[NS(id='x'+str(i),role='structure',semantic='side wall',
            form=PartForm(kind='panel',axis='x',minimum_ratio=2),attached_to=None) for i in range(2)]
        parts.extend(NS(id='y'+str(i),role='structure',semantic='wall pier or sill',
            form=PartForm(kind='panel',axis='y',minimum_ratio=2),attached_to=None) for i in range(5))
        parts.extend([
            NS(id='floor',role='structure',semantic='floor slab',
               form=PartForm(kind='panel',axis='z',minimum_ratio=2),attached_to=None),
            NS(id='ceiling',role='structure',semantic='ceiling slab',
               form=PartForm(kind='panel',axis='z',minimum_ratio=2),attached_to=None),
            NS(id='door_header',role='structure',semantic='header above opening',
               form=PartForm(kind='beam',axis='x',minimum_ratio=1.1),attached_to='y0'),
            NS(id='window_header',role='structure',semantic='lintel over opening',
               form=PartForm(kind='beam',axis='x',minimum_ratio=1.1),attached_to='y1'),
            NS(id='door_leaf',role='structure',semantic='door leaf',
               form=PartForm(kind='panel',axis='y',minimum_ratio=2),attached_to='door_jamb')])
        obj=NS(internal_parts=parts,dimensions=(4,4,3))
        spatial=NS(openings=[NS(wall='y_min',sill_height=0,height=2.1),
                              NS(wall='y_max',sill_height=.8,height=1.2)])
        receipt=shell_program_compatibility(obj,spatial,NS(parameters={}))
        self.assertTrue(receipt['supported'])
        self.assertEqual(len(receipt['independent_wall_members']['y']),7)

    def test_independent_perimeter_panels_fit_structured_capacity(self):
        obj=NS(internal_parts=[NS(id=axis+str(i),role='structure',
            form=PartForm(kind='panel',axis=axis,minimum_ratio=2)) for axis in 'xyz' for i in range(2)])
        self.assertTrue(shell_program_compatibility(obj)['supported'])

    def fixture(self):
        walls=[];intents=[]
        def intent(id):
            intents.append(NS(id=id,semantic=id,rigidity='parametric',form=None,support=None,attached_to=None));return id
        for wall in ['x_min','x_max','y_min','y_max']:
            wall_regions={'x_min':['whole'],'x_max':['low_side','high_side','below_opening','above_opening'],
                          'y_min':['low_side','high_side','above_opening'],
                          'y_max':['whole']}[wall]
            walls.append(dict(wall=wall,segments=[dict(part_id=intent('wall_'+wall+'_'+region),region=region)
                for region in wall_regions],
                skirting_id=intent('base_'+wall),cornice_id=intent('crown_'+wall),
                panel_trim_id=intent('panel_'+wall)))
        for item in intents:
            if item.id.startswith(('base_','crown_')):
                item.form=PartForm(kind='beam',axis='y' if 'x_' in item.id else 'x',minimum_ratio=20)
        program=ShellProgram.model_validate(dict(floor_id=intent('floor'),ceiling_id=intent('ceiling'),
            slab_thickness=.05,wall_thickness='thickness',skirting_height=.1,floor_surface={},wall_surface={},
            paint_color=[.8,.7,.6],floor_color=[.4,.3,.2],walls=walls,
            openings=[dict(opening_id='entrance',frame_id=intent('door_frame'),leaf_id=intent('door'),
                handle_id=intent('door_handle'),
                width='door_width',height='door_height',sill_height=0),
                dict(opening_id='window',frame_id=intent('window_frame'),sill_id=intent('sill'),
                    width=1.2,height=1.7,sill_height=.8)]))
        outline=NS(parameters=dict(width=6,depth=5,height=3.1,slab=.05,thickness=.18,
            door_width=1,door_height=2.1),extent=(6.36,5.36,3.5))
        obj=NS(id='room',dimensions=('width+2*thickness','depth+2*thickness','height+slab'),internal_parts=intents)
        spatial=NS(openings=[NS(id='entrance',wall='y_min',offset=0,width=1,height=2.1,sill_height=0),
            NS(id='window',wall='x_max',offset=-.5,width=1.2,height=1.7,sill_height=.8)])
        return program,outline,obj,spatial,NS(position=(0,0,'-slab'),yaw_deg=0)

    def compile(self,fixture):
        program,outline,obj,spatial,placement=fixture
        data=compile_shell(*fixture)
        local=SceneObject(id=obj.id,semantic='editable architecture',size=obj.dimensions,
            parts=[Part.model_validate(p) for p in data['parts']],assembly=data['assembly'])
        plan=GeneralPlan(scene_id='test',description='rectangular shell',extent=outline.extent,
            parameters=outline.parameters,objects=[local])
        evidence={};compile_local_geometry(plan,local,evidence=evidence)
        return data,evidence

    def test_actual_voids_exact_contacts_and_open_door(self):
        data,evidence=self.compile(self.fixture())
        self.assertEqual(evidence['assembly']['status'],'passed')
        self.assertIn('door_handle',evidence['parts'])
        # Independently inspect every generated solid in world space.
        for opening in self.fixture()[3].openings:
            axis=0 if opening.wall.startswith('x') else 1;along=1-axis
            plane=(3.18 if axis==0 else -2.68)
            for p in evidence['parts'].values():
                lo,hi=[list(v) for v in p['bounds']];lo[2]-=.05;hi[2]-=.05
                near=lo[axis]<plane+.4 if plane<0 else hi[axis]>plane-.4
                overlap=min(hi[along],opening.offset+opening.width/2)-max(lo[along],opening.offset-opening.width/2)
                vertical=min(hi[2],opening.sill_height+opening.height)-max(lo[2],opening.sill_height)
                self.assertFalse(near and overlap>.015 and vertical>.015)

    def test_room_width_edit_recomputes_physical_members(self):
        f=self.fixture();before=self.compile(f)[1]
        f[1].parameters['width']+=.4
        after=self.compile(f)[1]
        self.assertEqual(after['assembly']['status'],'passed')
        width=lambda e:e['parts']['floor']['size'][0]
        self.assertAlmostEqual(width(after)-width(before),.4)

    def test_missing_and_wrong_opening_bindings_rejected(self):
        f=self.fixture();f[0].openings[1].width=1.5
        with self.assertRaisesRegex(ValueError,'frozen spatial aperture'):compile_shell(*f)
        f=self.fixture();f[0].walls[0].segments[0].part_id='unrequested'
        with self.assertRaisesRegex(ValueError,'cover frozen IDs'):compile_shell(*f)


if __name__=='__main__':unittest.main()
