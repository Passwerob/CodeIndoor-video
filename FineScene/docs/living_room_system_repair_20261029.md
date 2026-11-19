# Living-Room Planning System Repair and Verification Record

## Conclusion

What has been confirmed and modified are the generic planning prompts, the coordinate and attachment diagnostics, the repair-patch protocol and the retry control. These failures cannot simply be attributed to model capability: the system had allowed incorrect door-component boundaries into the frozen plan, demanded solid-wall contact for an open window, let one attachment exception mask an independent out-of-bounds, and supplied incomplete repair context.

The current full regression is **510 passed, 23 subtests passed**; there are 8 existing Pydantic serialization warnings. In the real Agent replay, the window attachment passed with 1 repair, and the detailed door and window position out-of-bounds passed with another 1 repair. But the full spatial review still rejects this living room, and **there is no Blender file, render or visual acceptance pass**. The scene list, coordinates, models and materials were not hand-modified.

All new files and test temporary directories are under `E:/3DUse`. The repository has no Git metadata; the original run and the replay each keep a source snapshot and input hashes.

## Failure Mechanisms and Fixes

| Confirmed problem | System change | Verification evidence |
| --- | --- | --- |
| Planning expanded from the room category into a decor list, and the primary activity relations had not converged first | The prompt first generates a minimal usable object set from the user's activities, then plans furniture groups, orientations, operation areas and entrance circulation; living-room rest, TV and guest reception share the main seating group; it is made explicit that optional ornaments must not be added merely because they are "common" | The shared-room and living-room condition priors in `scene_experience.py`, the existing bedroom-trimming prior and the corresponding tests |
| `side_window` carries an integrated curtain and was missed by identity recognition; the window frame of a fitted opening was required to touch a solid wall | Extended the building-window semantic recognition, excluding window-side seats and shelves; the functional-binding stage makes the opening attachment contract explicit, keeping the real opening geometry check | The original binding was located at `/bindings/2/surface_contact`; the real Agent passed with a single repair |
| The door bottom is at world Z=-0.05, yet only "out of bounds" was fed back; the old bedroom prompt even required local Z=0 | State that the occupied ground is world Z=0, accounting for the parent floor-slab origin offset; report the axis, lower/upper bounds, actual value, threshold, excess, parent pose and the legal translation interval | The original door-bottom report is z_min=-0.05, lower bound 0, excess 0.05; when the condition holds, a parent-local translation `[0,0,0.05]` is given |
| An attachment exception exited early, masking another object's out-of-bounds | Collect the partially valid poses and continue checking independent objects; checks that depend on invalid poses are explicitly marked blocked | The same batch of feedback contains the window attachment failure and the door out-of-bounds; dependent children are not given forged valid poses |
| A wrongly oriented window cannot fit the opening by translation, yet a position was still recommended | Check the normal, tangent and height projection sizes; when translation is infeasible, the position suggestion is null and points to checking orientation/upstream size | The wrong-orientation regression test passes; neither the opening size nor the acceptance threshold was relaxed |
| The door frame, lintel and leaf were merged into one component but registered as a swing leaf rotating as a whole component | The decomposition and spatial prompts state the component boundary of fixed frame and moving leaf; an `opening_swing_assembly` check was added before freezing the spatial contract, pointing to decomposition repair | The new minimal test fails before the fix and passes after; the original living-room replay accurately lists the two jambs and the door_head, and refuses to keep fixing only positions |
| Collision and route feedback were a single sentence of description | Collision reports the part pair, world bounding boxes and per-axis penetration; route reports the blocked segment, obstacle bounding box, clearance shortfall, coordinate system and route modification path; doors and windows keep position and orientation paths | Numeric regression tests pass; the original collision and route thresholds are unchanged |
| Joint recovery lacked the candidate schema, and the route field description did not match the real structure | Repair requests pass the full candidate schema; joint recovery states `/spatial/routes/<index>/points` explicitly, while the layout stage uses its own route_points; error guidance is merged item by item | The repair requests and source snapshot are preserved; a single uniform error field name no longer directs different stages |
| Patching a parent mapping and descendant fields simultaneously, with errors constraining unrelated list lengths | Index stability is kept only for the list that has an explicit descendant patch; unrelated sibling lists may be replaced wholesale or have elements deleted | An unrelated open_spaces list deletion and a groups sub-field patch pass in the same batch; the original parent-child patch semantics are preserved |
| max_repairs did not actually limit repair calls, and changing diagnostics let retries continue | The local repair call limit defaults to 6 and is explicitly configurable; when the budget is exhausted a stop signal carrying the full diagnostics is raised, and the outer layer must not re-plan to reset the budget | Two repair budgets still stop under continuously changing errors; the outer call happens only once, and the diagnostics are preserved |

The generic room priors are placed at the shared prompt entry and selected by task, to avoid duplicating the whole set of room rules into every single-object modeling call. The analysis of existing research and of the locally successful shell record is in `bedroom_scene_planning_failure_analysis_20261028.md`; this change did not copy others' object-count targets directly, and added no manual layout specific to a particular living room.

## Actual Replay

Original input: `一个普通家庭客厅，舒适整洁，可供家人日常休息、看电视和会客；请根据这些活动自行规划房间、家具和布局。` (An ordinary family living room, comfortable and tidy, where the family can rest, watch TV and receive guests; plan the room, furniture and layout yourself based on these activities.)

Original run: `runs/living_room_probe_20261028_02/replan_02/agent_runtime`.

Real-model local replay: `runs/living_room_system_repair_evidence_01`. This is a bounded recovery reusing the old candidate, not a first full generation from a new prompt. The model used the existing `configs/deepseek-flash.yaml`, with each of the two stages limited to 2 repairs. Each actually used 1, with stage times of about 4.9 seconds and 14.2 seconds. The first removed the wrong window-wall contact; the second corrected the door-bottom and window-top out-of-bounds according to the system measurement. The specific requests, responses and patches are in that directory's `agent_runtime`.

This replay was completed before the "door frame cannot serve as a moving leaf" check was added, and its source snapshot is preserved. The subsequent full review still found: the door intersects the sofa, the door-leaf projection orientation is wrong, the window is not aligned with the opening's tangent/height, and the entrance route is blocked by the south wall. Therefore passing the detailed-position pre-check cannot be counted as spatial passing, let alone as generation success.

Final no-model-call replay: `runs/living_room_system_repair_final_check_20261029`. The current code identifies, at the earlier spatial-contract stage, that the original door component contains a fixed door frame, and explicitly reports `repair_target=decomposition`. Continuing to let this frozen component fix only coordinates has no correct physical meaning, so the expensive full re-plan was not started again. The input-hash re-check of the original JSON shows all unchanged.

## Verification Commands and Results

```powershell
$env:TEMP='E:\3DUse\FineScene\runs\test_tmp_living_room'
$env:TMP=$env:TEMP
python -m pytest experiments -q --basetemp runs/test_tmp_system_repair_release_01

& 'E:\3DUse\aDSL\venv\Scripts\python.exe' experiments/replay_scene_repair_evidence.py `
  --source-runtime runs/living_room_probe_20261028_02/replan_02/agent_runtime `
  --out runs/living_room_system_repair_final_check_20261029
```

Full test log: `runs/system_repair_tests_release_01.txt`, 510 passed, 23 subtests passed, 20.31 seconds. After the retry limit was enabled, an old test that had set max_repairs=1 but expected 2 calls was changed to verify stopping after exactly 1 call, and still checks that all collision evidence over 6000 characters is not truncated.

Newly added protection covers: window identity vs. furniture distinction, attachment failure reporting independent out-of-bounds at the same time, parent-rotation coordinate transform, oversize non-translatability, wrong-orientation window non-translatability, door-frame/door-leaf boundary, collision numerics, route blocked segment, list patches, local budget and the outer layer not resetting the budget. No temporary debug prints remain.

## Not Yet Proven

The prompt strengthening is a preventive measure, and the end-to-end first-round success rate cannot be inferred from a single local repair. The current evidence only proves that the above system defects were located and fixed, and that two specific local problems could be corrected by the Agent in a single pass. A new full living room still needs the Agent to redo the door-component partition, re-plan, and pass the actual Blender geometry and system visual review. The existing failed run was not changed to accepted, and no substitute render was produced to pass for acceptance.
