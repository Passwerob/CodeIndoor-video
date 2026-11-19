"""Durable, brief-matched construction priors shared by scene agents and judges."""


def _has(text, terms):
    return any(term in text for term in terms)


def _living_room(text):
    return _has(text, ('living room','lounge','family room','客厅','起居室','会客厅'))


def _shared_room_planning_priors():
    """Mechanism-level priors that apply across room types without fixing layouts."""
    return [
        "Plan from stated activities and occupant count to the smallest coherent set of objects that makes those activities work. Give every freestanding item a traceable activity or accepted design purpose; room type, age and the word realistic do not authorize stock clutter. Compare each optional object against an existing surface, finish or integrated detail that could serve the same purpose, and omit redundant objects. Keep user-specified inventory and exclusions authoritative.",
        "Keep the architectural shell, wall openings and fitted aperture fixtures distinct from furniture/activity groups. A fitted window or door leaf is located by its matching opening contract, never by a generic wall-face contact. A shell parent establishes a local frame, not a world position: transform the measured world opening target by the inverse parent translation and yaw, including the difference between shell base and occupied floor Z=0. Validate the whole fixture against the opening after placement. For doors, keep fixed jamb/head geometry fixed and associate only the moving leaf with the swing; place its hinge at the jamb before deriving the rotated leaf center.",
        "Resolve room-scale composition as functional groups before object coordinates: identify each group's anchor, facing/working edge, human approach and route to the entrance; then check all groups together for fit, collision, support and circulation. A readable empty floor is normally the residual of a sound arrangement, not a reason to add object-spacing constraints or arbitrary protected rectangles. Do not freeze relationships that merely restate visual openness.",
        "A repair must use every independent issue in the structured report. For each one, read component IDs, measured values, expected values, coordinate frame, controlling interface and allowed repair paths. Correct parent-local coordinates in the named axis; do not guess an axis from the prose summary. If an interface or opening is invalid, repair that contract before translating its child. Return one coherent change set and recheck support, opening fit, extent, collisions, access and routes together."
    ]


def _living_room_planning_priors():
    return [
        "Living-room activity mapping: the brief's daily relaxation, television viewing and hosting guests share one primary seating/conversation group. Select seating capacity for the described household/use, orient seats coherently to the requested television focal plane while keeping a face-to-face conversation arrangement, and let one media surface/console serve the screen. Add a coffee or side table only when it is reachable from seats and does not break the circulation route. Do not duplicate seating, screens, tables or lighting to fill a room checklist; make any extra seat or accent earn its place through a stated activity or deliberate design choice.",
        "For a television room, treat screen, media support and primary seating as one viewing relationship; calculate screen-facing direction and occupied footprint together. Treat hosting as shared seating, not a second unrelated furniture zone. Preserve a direct entrance path, access to every retained seat, and a continuous path around the table. Use materials, wall/floor finish, upholstery, and light distribution to make the room feel complete before adding optional rugs, lamps, plants, baskets or display props."
    ]

def prepend_experience(instructions, experience):
    """Put directly relevant construction priors before the general rule set."""
    experience = str(experience or '').strip()
    instructions = str(instructions or '').lstrip()
    return f'{experience}\n\n{instructions}' if experience else instructions


def planning_experience_prompt(brief, component_id=None):
    text = ' '.join(str(brief or '').split()).casefold()
    component = str(component_id or '').casefold()
    bedroom = _has(text, ('bedroom', '卧室', '寝室'))
    study = _has(text, ('study', 'homework', 'desk', '书桌', '学习', '高中生', '学生'))
    desk_requested = study
    tatami = _has(text, ('tatami', '榻榻米', '畳'))
    window = _has(text, ('window', '窗户', '窗'))
    lighting = _has(text, ('lamp', 'lighting', 'light fixture', 'ceiling light', '台灯', '吊灯', '吸顶灯', '灯具', '灯光'))
    activity_lighting = lighting or (bedroom and study)

    if component:
        if tatami:
            tatami = _has(component, ('tatami', 'futon', 'bedding', 'sleep_surface'))
        if window:
            window = _has(component, ('window', 'sash', 'glaz', 'shutter', 'blind', 'shade', 'curtain', 'roller', 'frame'))
        if activity_lighting:
            lighting = _has(component, ('lamp', 'light', 'fixture', 'diffuser', 'sconce'))
        else:
            lighting = False
        bedroom = False
        study = False
    else:
        lighting = activity_lighting

    notes = []
    if not component and (bedroom or study or tatami or window or lighting or _living_room(text)):
        notes = _shared_room_planning_priors()
        if _living_room(text):notes.extend(_living_room_planning_priors())
    if window:
        notes.append(
            'Window assembly: freeze the real frame/sash/shutter as aperture hardware with dimensions suited to its '
            'named opening. Its shell parent establishes a frame, not a wall position; the placement stage must also '
            'resolve the opening wall plane in that parent-local frame. Keep the fitted window in an architectural-envelope '
            'group, outside furniture activity groups. The opening contract alone positions it; daylight belongs in prose '
            'or a group link, never a typed desk/window relation.'
        )
    if tatami:
        notes.append(
            'Tatami sleep/rest assembly: use a low, stable platform with distinct rectangular woven mats '
            'and readable, staggered bound seams; avoid a four-corner seam intersection when using a traditional '
            'modular layout. The exposed mat top must read as a muted rush/straw weave with separate module faces '
            'and visible binding; never reuse the bedding fabric or its plaid/check pattern as the tatami surface. '
            'Choose a surface map by its catalog surface_family, not by color: use plant_fiber only for exposed mat '
            'faces, and use surface=null with a muted straw procedural finish when no suitable plant-fiber map exists. '
            'Keep the wooden platform/deck structurally distinct from the exposed mats. Seat mat faces and binding '
            'strips flush at clean seams; do not invent visible gaps or separate the bands from the mat edges. '
            'Keep the futon/bedding as one coherent, properly supported sleep layer that is smaller than the mat '
            'field, leaving exposed woven surface and a readable mat edge in room views. Give bedding its own '
            'quiet linen/cotton material and keep the platform finish distinct from both textiles. '
            'Do not bury the tatami under an oversized mattress or duplicate the sleep function with a conventional '
            'bed unless the user explicitly requests both. For a mature or restrained room, use quiet, compatible '
            'bedding colors and avoid loud juvenile prints unless requested. Keep shared platform/mat/bedding '
            'details in one editable assembly when they share the requested asset boundary. Before freezing the '
            'inventory, check every supported layer against the real support footprint together: a mattress and a '
            'side cushion must fit without overlap while leaving the described exposed mat. Add a cushion to the '
            'platform only when that usable mat area exists; otherwise place an optional cushion on clear floor or '
            'omit it when it was not requested. Do not send a known support-capacity conflict to layout. At trim '
            'corners, use butt or miter joints so edge rails meet without crossing; check the full platform, mats, '
            'bedding and cushions together against the frozen envelope.'
        )
    if desk_requested and (not component or _has(component, ('desk', 'workstation', 'writing_table'))):
        notes.append(
            'Desk drawer construction: internal_parts.support means a child resting on the target horizontal top. '
            'Freeze an under-desktop drawer as attached_to=desktop with mount=bottom and support=null; the recipe '
            'compiler supplies that parent/mount and the matching attached_to relation. Its local center.z must be '
            '0 so its top meets the desktop bottom. Put the pull on the drawer front and '
            'keep the drawer inside the desk envelope without blocking the knee opening. Never encode an underside '
            'fastener as supported_by.'
        )
    if window and component and _has(component, ('shade', 'blind', 'curtain', 'roller')):
        notes.append(
            'Roller shade construction: keep the headrail/roller at the top of the opening and the fabric panel '
            'hanging below it. Join the panel to the headrail with a real face attachment; do not overlap the tube '
            'and panel or declare the hanging panel supported_by. Check every part against the frozen component '
            'envelope and keep the complete assembly connected to one root.'
        )
    if lighting:
        notes.append(
            'Lighting fixtures: choose fixture types for the stated activities rather than treating lamps as '
            'a room checklist. A ceiling fixture must have a real ceiling attachment/contact and plausible '
            'suspension for its type; a pendant has a visible stem or cord and hangs below the ceiling, while a '
            'flush fixture sits against it. Keep the room usable beneath it. A desk/task lamp belongs on the work '
            'surface it serves, with its base supported inside that surface and its shade aimed at the task. Keep '
            'the base, arm and shade in believable proportions, verify the selected asset has a usable contact '
            'footprint and orientation, and avoid redundant fixtures that compete for space. Hanging is visual '
            'language, not the mount enum: a ceiling pendant uses the ceiling as parent, mount=origin and an actual '
            'canopy-to-ceiling surface_contact; mount=bottom means support beneath the host bottom face. Carry each '
            'retained fixture into an actual design choice and physical support relationship, not only room prose. '
            'A passive ceiling fixture does not need a floor zone, approach route, or a duplicate decorative light.'
        )
    if bedroom and study:
        notes.append(
            'Student bedroom composition: make sleep and study distinct, legible activity groups sized for '
            'one occupant. Pair the chair with the desk facing the work surface. Keep the fitted window in the '
            'architectural-envelope group, outside furniture activity groups; express its daylight contribution as '
            'prose or a group link, never as a typed relation to furniture. Preserve a direct entry path '
            'and a visibly open shared floor area. Use only storage '
            'and small working objects that serve those activities; keep the task lamp and study materials within '
            'the desk group rather than giving them separate room zones. Do not fill the center to signal realism.'
        )
        notes.append(
            'Student-room scope and finish: build a believable, finished interior, not a furniture checklist. '
            'Describe the wall, floor, ceiling, window joinery and ambient/task lighting as one compatible material '
            'scheme. Include only storage or personal objects that have a clear use in the accepted design; do not '
            'add a laundry basket, plant, extra chair or decorative set solely because bedrooms often contain one. '
            'Keep the room visually lived-in through a few intentional, well-supported details and material contrast, '
            'with open floor left by the actual furniture arrangement.'
        )
        notes.append(
            'Seated study station: the task chair front faces the desk/work surface, but a person enters and pulls '
            'the chair out from its rear (owner-local back=+Y) or a genuinely clear side. The chair front is occupied '
            'by the desk and is never its own access route. Treat the chair and desk as one seated-use station: the '
            'chair owns the floor approach and pullback; do not add a second desk-front approach through the chair. '
            'A typed chair-to-work-surface facing relation encodes orientation only: use minimum_m=0 and '
            'maximum_m=null. Do not invent a positive footprint gap for a chair that serves a desk; add a separate '
            'distance relation only when the user requests a measurable gap or reach interval. '
            'Set a seated desk/work surface access_required=false unless the user explicitly requests a separate '
            'standing or accessibility approach.'
        )
    if bedroom:
        notes.append(
            'Door and opening semantics: define an entrance through its wall opening, jamb/hinge and clear swing; '
            'never create a facing relation from a door leaf to a window or furnishing. Express an arrival sightline '
            'in composition prose or a view intent, not as a door-facing constraint.'
        )
    if not notes:
        return ''
    return '\n\nRelevant reusable scene experience for this request (apply only matching parts; the original user brief remains authoritative):\n- ' + '\n- '.join(notes)


def spatial_experience_prompt(brief):
    """Reusable placement priors for the room-level spatial planning stages."""
    text = ' '.join(str(brief or '').split()).casefold()
    bedroom = _has(text, ('bedroom', '卧室', '寝室', '高中生', '学生'))
    student_room = bedroom and _has(text, ('高中生', '学生'))
    study = _has(text, ('study', 'homework', 'desk', '书桌', '学习')) or student_room
    tatami = _has(text, ('tatami', '榻榻米', '畳'))
    window = _has(text, ('window', '窗户', '窗'))
    lighting = _has(text, ('lamp', 'lighting', 'light fixture', 'ceiling light', '台灯', '吊灯', '吸顶灯', '灯具', '灯光'))
    lighting = lighting or (bedroom and study)

    notes = []
    if not (bedroom or study or tatami or window or lighting or _living_room(text)):
        return ''
    notes.extend([
        'Universal placement checks: use the occupied floor as world Z=0 and retain the shell world pose as the '
        'coordinate datum. For each opening-bound child, convert the world wall plane, tangent offset and sill height '
        'through the inverse shell world pose into the child parent-local frame; check normal, tangent and vertical '
        'coordinates, opening fit and wall approach separately. A horizontal adjustment cannot fix a Z-floor error. '
        'A supported or wall-contacting object must be corrected through its real interface if an independent '
        'translation would break attachment.',
        'Before accepting any candidate, check the exact occupied bounds of every component against each extent axis, '
        'then check apertures, door swing, overlaps, access areas, routes and primary group relationships. Feedback '
        'must identify component, axis, side, measured bound, limit, excess, parent/world pose and parent-local '
        'translation interval; use a numeric correction only when the whole feasible interval exists.'
    ])
    if _living_room(text):notes.extend(_living_room_planning_priors())
    if window:
        notes.append(
            'Window-opening placement: a fitted frame, sash, glazing or shutter belongs at its named architectural '
            'opening, not at the room origin and not wherever its parent origin happens to be. Use the opening wall, '
            'tangent offset, sill and measured fixture depth together; transform the world opening target into the '
            'component parent-local frame, accounting for the parent world origin and yaw. Keep aperture hardware '
            'in the opening contract rather than an activity-zone footprint. Keep the desk in its own study footprint '
            'and preserve a useful approach to the window; do not encode the window and desk as a typed spatial relation. '
            'Describe daylight use in composition prose or a group link.'
        )
    if tatami:
        notes.append(
            'Tatami placement: keep the low sleep platform in one coherent rest zone with an approachable edge '
            'facing shared floor. Preserve the mat surface and seams in the main room view; keep bedding supported '
            'on the platform, and do not block an entrance swing or the window approach. Do not impose a numeric '
            'coverage ratio or a fixed compass-side placement. Do not create desk-to-tatami or chair-to-tatami '
            'minimum distances to represent visual separation or chair pull-back. The shared floor is residual '
            'space between activity zones; chair pull-back belongs in the chair access area on its seated side. '
            'For distance relations, minimum_m is normally zero unless the user explicitly requires a gap or the '
            'activity has a concrete functional reason for one. Check all simultaneous children against the mat '
            'support footprint before accepting a side-by-side cushion or bedding arrangement; do not preserve an '
            'impossible support group for the layout solver to discover later.'
        )
        if bedroom:
            notes.append(
                'Compact tatami-bedroom composition: reserve the daylight wall or window approach for the sleep '
                'surface when it fits, and put the study group on another usable wall or adjacent perimeter. Treat '
                'central open floor as a qualitative result of perimeter placement, not a prescribed empty rectangle. '
                'Do not add a hard desk-to-tatami or chair-to-tatami distance just to create visual separation; '
                'distinct zones, non-overlapping occupied footprints, the real task approaches and the connected '
                'entry route already express that separation.'
            )
            notes.append(
                'Tatami bedroom appearance: make the platform read as a deliberate built-in or low raised sleep area '
                'through its timber edge, visible woven mat modules and carefully placed bedding. Keep the room shell '
                'finished with a calm wall surface and a distinct floor material; realism comes from these coherent '
                'architectural surfaces and usable furniture groups, not from filling every empty patch with props.'
            )
    if lighting:
        notes.append(
            'Lighting placement: keep a task lamp inside the desk group and footprint; it is a supported tabletop '
            'object, not a separate floor zone or circulation destination. A ceiling fixture belongs to the ceiling '
            'or room-envelope group and must attach to the ceiling with a physically plausible mount; a pendant '
            'hangs from that mount. Its parent contact still uses mount=origin with the actual canopy face; the enum '
            'mount=bottom describes support below a parent bottom face. Passive ceiling fixtures need no floor access area or route. Select only fixtures '
            'serving the stated activities.'
        )
    if window:
        notes.append(
            'Opening fixture layout: place each frame/shutter in the exact named opening using its measured target '
            'and parent-local transform. A parent at the room center does not put its child on a wall. Include the '
            'wall-normal coordinate as well as tangent and sill alignment; use recommended_local_position_m when '
            'opening-fit diagnostics provide it. Do not require a fitted window to lie inside the furniture activity '
            'zone; do not relate a desk to a window with any typed footprint constraint. Keep daylight '
            'intent in the design prose; relate the desk to its own work and chair group.'
        )
    if bedroom and study:
        notes.append(
            'Student-room layout: solve the desk, chair and task lamp as one study group. Give the desk and chair '
            'separate, non-overlapping footprints; put the chair in front of the working edge and orient its front '
            'toward the desk. A person approaches the chair from behind it or a clear side: use owner-local back '
            '(+Y) for the usual pullback/entry area, never the chair front that faces into the desk. The chair pullback '
            'area is the floor approach for the whole seated station; do not reserve a second desk-front strip that '
            'the chair occupies. The chair-to-desk facing relation means orientation only and must use minimum_m=0 '
            'and maximum_m=null; a chair may tuck close to the work surface. Express a user-requested measurable '
            'reach interval as a separate distance relation. Pullback belongs behind the chair and must not become '
            'an extra gap between the chair and desk. '
            'For rotated rectangular envelopes, project each half-width onto the center-to-center direction '
            'and compute edge gap = center distance minus both projected half-widths; choose the center distance from '
            'that sum plus the required gap. A facing angle can be correct while the footprints still overlap, so '
            'check facing and edge gap separately. Do not place both centers at the same coordinates. Before returning '
            'the layout, check the rotated footprints and declared relations together, then move the pair and refresh '
            'affected routes, access areas and open-space reservations in the same candidate. Keep a useful approach '
            'to the window, an open entry-to-room route, and a continuous shared floor area between study and rest zones.'
        )
        notes.append(
            'Student-room circulation: use one connected shared entrance aisle with short branches to distinct '
            'activities. Do not reserve a separate full-length door-to-furniture corridor for the tatami, chair, '
            'wardrobe, basket and other items in parallel. Keep the tatami approach at its usable exposed edge, '
            'the chair pullback behind the chair, and storage access at its operating face; nearby areas may meet '
            'where a person can naturally pass through.'
        )
    if bedroom:
        notes.append(
            'Door placement is governed by its actual jamb, hinge and open swing. From the opening wall and tangent '
            'interval, choose the jamb pivot first; rotate the leaf so its measured long axis swings into clear room '
            'space, then recompute its CENTER from that fixed pivot and the rotated local center-to-hinge offset. '
            'For a centered leaf with a local long axis, this offset is half its measured long dimension along that '
            'axis. Put a shell-fitted, floor-reaching door leaf at WORLD bottom Z=0; its parent-local Z equals '
            'zero minus the shell world-origin Z (for example +0.05m when the shell is at -0.05m). Do not use half its '
            'height. Apply the inverse parent transform to obtain a parent-local position. Rotating the leaf around its '
            'own center and leaving its position unchanged moves the hinge and is not a valid swing. Confirm the full '
            'rotated bounds clear the aperture and route. Never encode a door leaf as facing a window or furniture; '
            'use a camera/view intent for an arrival sightline. Do not add an invented distance relation merely to '
            'describe that sightline.'
        )
    if not notes:
        return ''
    return '\n\nStable conditional system priors for room layout (not extra objects or hard numeric thresholds):\n- ' + '\n- '.join(notes)


def visual_experience_prompt(brief):
    text = ' '.join(str(brief or '').split()).casefold()
    notes = []
    if _has(text, ('tatami', '榻榻米', '畳')):
        notes.append(
            'For an explicitly requested tatami sleep/rest area, check that the render actually shows '
            'a rush/straw-like woven mat surface, distinct bound rectangular modules and exposed mat beside '
            'separate bedding. Reject a shared loud plaid/check fabric that makes the tatami read as ordinary '
            'cloth. If the view is occluded or too small to tell, use unclear; do not estimate a coverage ratio '
            'or invent dimensional limits.'
        )
    if _has(text, ('window', '窗户', '窗')):
        notes.append(
            'For an explicitly requested window, check that the frame and shutter visibly occupy the wall opening '
            'and meet the reveal. An obvious window floating at room center or detached from its wall is a blocking '
            'composition error; if the view cannot establish the contact, use unclear and defer to measured opening '
            'geometry rather than guessing from perspective.'
        )
    bedroom = _has(text, ('bedroom', '卧室', '寝室', '高中生', '学生'))
    study = _has(text, ('study', 'homework', 'desk', '书桌', '学习')) or (
        bedroom and _has(text, ('高中生', '学生')))
    lighting = _has(text, ('lamp', 'lighting', 'light fixture', 'ceiling light', '台灯', '吊灯', '吸顶灯', '灯具', '灯光'))
    if lighting or (bedroom and study):
        notes.append(
            'For explicitly requested lighting, check that the visible fixture reads as the intended '
            'ceiling/task/wall type, appears physically connected to its support, and is not conspicuously '
            'floating, inverted or implausibly proportioned. A desk-lamp base should visibly rest on its worktop; '
            'a ceiling fixture should visibly meet its mount or suspension. Images do not prove physical attachment; '
            'measured support checks own that decision. Do not require every fixture type in every room.'
        )
    if bedroom and study:
        notes.append(
            'Review the whole room for a clear sleep area, usable study station, fitted window, finished walls and '
            'a coherent light/material hierarchy. Do not reject a scene over an ordinary small clearance that cannot '
            'be established from the image; use measured geometry for scale, contact and route claims.'
        )
    if not notes:
        return ''
    return '\n\nRelevant scene-experience review cues (only for matching user-requested features):\n- ' + '\n- '.join(notes)


