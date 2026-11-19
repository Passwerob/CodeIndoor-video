"""Offline architectural-shell contract replay; never edits the recorded run.

This diagnostic reads run 11's frozen plan, applies the physically valid header
interface/form corrections to an in-memory copy, maps the already planned wall
IDs to aperture cells, then exercises the current recipe/geometry/opening gates.
It makes no model calls and writes no files.
"""
import json
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))

from architectural_shell import ShellProgram,compile_shell,shell_program_compatibility
from scene_spatial import SpatialContract
from staged_planner import (Decomposition,Recipe,validate_architectural_openings,
                            validate_recipe)


def replay(runtime=None):
    runtime=Path(runtime) if runtime else ROOT/'runs/high_school_tatami_bedroom_agent_20261028_11/agent_runtime'
    plan_data=json.loads((runtime/'decomposition_accepted.json').read_text(encoding='utf-8'))
    plan_data=plan_data.get('plan',plan_data)
    shell_data=next(item for item in plan_data['objects'] if item.get('collision_role')=='shell')
    # Correct two logged impossible floor supports and their wall-panel forms on
    # the in-memory copy only. A narrow above-opening cell is not a floor panel.
    for part in shell_data['internal_parts']:
        if part['id'] in {'wall_rear_header_above_window','wall_front_header_above_door'}:
            part['support']=None
            part['form']={'kind':'beam','axis':'x','minimum_ratio':1.1}
    outline=Decomposition.model_validate(plan_data)
    shell=next(obj for obj in outline.objects if obj.collision_role=='shell')
    frozen=json.loads((runtime/'frozen_spatial_contract.json').read_text(encoding='utf-8'))
    spatial=SpatialContract.model_validate(frozen.get('contract',frozen))
    compatibility=shell_program_compatibility(shell,spatial,outline)
    if not compatibility['supported']:
        raise ValueError(f"Frozen wall IDs do not map to aperture cells: {compatibility}")

    program=ShellProgram.model_validate({
        'floor_id':'floor_slab','ceiling_id':'ceiling_slab','wall_thickness':.12,
        'floor_surface':{},'wall_surface':{},'paint_color':[.9,.9,.9],
        'floor_color':[.5,.4,.3],
        'walls':[
            {'wall':'x_min','segments':[{'part_id':'wall_left_side','region':'whole'}]},
            {'wall':'x_max','segments':[{'part_id':'wall_right_side','region':'whole'}]},
            {'wall':'y_min','segments':[
                {'part_id':'wall_front_left','region':'low_side'},
                {'part_id':'wall_front_right','region':'high_side'},
                {'part_id':'wall_front_header_above_door','region':'above_opening'}]},
            {'wall':'y_max','segments':[
                {'part_id':'wall_rear_left','region':'low_side'},
                {'part_id':'wall_rear_right','region':'high_side'},
                {'part_id':'wall_rear_below_window','region':'below_opening'},
                {'part_id':'wall_rear_header_above_window','region':'above_opening'}]}],
        'openings':[
            {'opening_id':'opening_entrance_near','low_jamb_id':'door_jamb_left',
             'high_jamb_id':'door_jamb_right','head_id':'door_head_jamb',
             'leaf_id':'door_leaf','handle_id':'door_handle',
             'width':.9,'height':2.1,'sill_height':0},
            {'opening_id':'opening_window_far','width':1.1,'height':1.2,
             'sill_height':1.4}]})
    placement=SimpleNamespace(position=(0,0,-.05),yaw_deg=0)
    recipe=Recipe.model_validate(compile_shell(program,outline,shell,spatial,placement))
    validate_recipe(outline,shell,recipe)
    validate_architectural_openings(shell,recipe,outline.extent,outline.parameters,
                                    spatial.openings,placement)
    return {'status':'passed','compiled_parts':len(recipe.parts),
            'expected_wall_cells':compatibility['expected_panel_counts'],
            'historical_run_modified':False,'model_calls':0,'files_written':0}


if __name__=='__main__':
    print(json.dumps(replay(),ensure_ascii=False,indent=2))
