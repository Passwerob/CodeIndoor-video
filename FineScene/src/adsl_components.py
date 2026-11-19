"""Furniture component factories backed by the local aDSL RelationAssembly."""
from __future__ import annotations

from dataclasses import dataclass
import inspect
from typing import Any, Callable

import finescene_env as fenv

from adsl.core import Asset, Cube, Cylinder, ExternalMesh, RelationAssembly, shape_aabb, rotate_shape, translate_shape, Sphere


WOOD = (0.19, 0.08, 0.035)
UPHOLSTERY = (0.16, 0.18, 0.17)
METAL = (0.12, 0.12, 0.11)
WALL = (0.42, 0.30, 0.20)
FLOOR = (0.26, 0.12, 0.045)
TRIM = (0.12, 0.055, 0.02)


@dataclass
class ComponentBuild:
    component_id: str
    factory: str
    scene: Asset
    values: dict[str, float]
    report: dict[str, Any]
    interfaces: dict[str, dict[str, Any]]

    def manifest(self) -> dict[str, Any]:
        low, high = shape_aabb(self.scene)
        primitive_count = len(list(self.scene.iter_primitives()))
        return {
            "component_id": self.component_id,
            "factory": self.factory,
            "bounds": {"min": [float(x) for x in low], "max": [float(x) for x in high]},
            "primitive_count": primitive_count,
            "parameters": self.values,
            "interfaces": self.interfaces,
            "relation_report": self.report,
            "generation": "adsl_parametric",
        }


def build_room_shell(component_id: str = "room_shell", **room: Any) -> ComponentBuild:
    """Build the architectural envelope from the scene planner's room contract."""
    width = float(room.get("width", 5.0))
    depth = float(room.get("depth", 4.0))
    height = float(room.get("height", 2.8))
    wall_color = tuple(float(value) for value in room.get("wall_color", WALL))
    wall = 0.12
    assembly = RelationAssembly("FineScene_RoomShell")
    assembly.box("architecture/floor", (width, depth, 0.08), (0, 0, -0.04), FLOOR)
    assembly.box("architecture/ceiling_back_beam", (width, wall, 0.08), (0, depth / 2 - wall / 2, height - 0.04), wall_color)
    assembly.box("architecture/ceiling_left_beam", (wall, depth, 0.08), (-width / 2 + wall / 2, 0, height - 0.04), wall_color)
    assembly.box("architecture/ceiling_right_beam", (wall, depth, 0.08), (width / 2 - wall / 2, 0, height - 0.04), wall_color)
    assembly.box("architecture/left_wall", (wall, depth, height), (-width / 2 + wall / 2, 0, height / 2), wall_color)
    assembly.box("architecture/right_wall", (wall, depth, height), (width / 2 - wall / 2, 0, height / 2), wall_color)

    # Keep a real opening in the front wall by building three lintel/jamb pieces.
    door_width = min(float(room.get("door_width", 0.9)), width - 0.4)
    door_height = min(float(room.get("door_height", 2.1)), height - 0.2)
    front_y = -depth / 2 + wall / 2
    assembly.box("openings/door_jamb_left", (0.07, wall, door_height), (-door_width / 2, front_y, door_height / 2), TRIM)
    assembly.box("openings/door_jamb_right", (0.07, wall, door_height), (door_width / 2, front_y, door_height / 2), TRIM)
    assembly.box("openings/door_header", (door_width, wall, 0.07), (0, front_y, door_height), TRIM)
    assembly.box("openings/door_leaf", (door_width - 0.06, 0.045, door_height - 0.06), (0.0, front_y + 0.045, (door_height - 0.06) / 2), TRIM)

    window_wall = str(room.get("window_wall", "back"))
    window_width = min(float(room.get("window_width", 1.4)), width - 0.4)
    window_height = min(float(room.get("window_height", 1.1)), height - 0.4)
    sill = min(float(room.get("window_sill", 0.9)), height - window_height - 0.1)
    frame = 0.07
    if window_wall == "back":
        x, y = 0, depth / 2 - wall / 2
        side_span = max((width - window_width) / 2, wall)
        assembly.box("architecture/back_left", (side_span, wall, height), (-width / 2 + side_span / 2, y, height / 2), wall_color)
        assembly.box("architecture/back_right", (side_span, wall, height), (width / 2 - side_span / 2, y, height / 2), wall_color)
        assembly.box("architecture/back_lower", (window_width, wall, sill), (0, y, sill / 2), wall_color)
        assembly.box("architecture/back_upper", (window_width, wall, height - sill - window_height), (0, y, sill + window_height + (height - sill - window_height) / 2), wall_color)
        assembly.box("openings/window_sill", (window_width + 0.16, 0.16, frame), (x, y, sill), TRIM)
        assembly.box("openings/window_header", (window_width + 0.16, 0.16, frame), (x, y, sill + window_height), TRIM)
        assembly.box("openings/window_left", (frame, 0.16, window_height), (x - window_width / 2, y, sill + window_height / 2), TRIM)
        assembly.box("openings/window_right", (frame, 0.16, window_height), (x + window_width / 2, y, sill + window_height / 2), TRIM)
        assembly.box("openings/window_glass", (window_width - 0.1, 0.025, window_height - 0.1), (x, y - 0.03, sill + window_height / 2), (0.15, 0.28, 0.38))
    else:
        x = -width / 2 - 0.005 if window_wall == "left" else width / 2 + 0.005
        assembly.box("openings/window_sill", (0.16, window_width + 0.16, frame), (x, 0, sill), TRIM)
        assembly.box("openings/window_header", (0.16, window_width + 0.16, frame), (x, 0, sill + window_height), TRIM)
        assembly.box("openings/window_front", (0.16, frame, window_height), (x, -window_width / 2, sill + window_height / 2), TRIM)
        assembly.box("openings/window_back", (0.16, frame, window_height), (x, window_width / 2, sill + window_height / 2), TRIM)
        assembly.box("openings/window_glass", (0.025, window_width - 0.1, window_height - 0.1), (x + (0.03 if x < 0 else -0.03), 0, sill + window_height / 2), (0.15, 0.28, 0.38))
    assembly.box("trim/baseboard_back", (width, 0.04, 0.10), (0, depth / 2 - wall - 0.02, 0.13), TRIM)
    assembly.box("trim/baseboard_left", (0.04, depth, 0.10), (-width / 2 + wall + 0.02, 0, 0.13), TRIM)
    assembly.box("trim/baseboard_right", (0.04, depth, 0.10), (width / 2 - wall - 0.02, 0, 0.13), TRIM)
    result = assembly.compile()
    interfaces = {
        "floor_surface": {"kind": "support", "anchor": "top", "normal": "+Z"},
        "door_opening": {"kind": "opening", "wall": "front cutaway", "width": door_width, "height": door_height},
        "window_opening": {"kind": "opening", "wall": window_wall, "width": window_width, "height": window_height},
    }
    return ComponentBuild(component_id, "build_room_shell", result.scene, result.values, result.report, interfaces)


def build_decor(component: dict[str, Any]) -> ComponentBuild:
    size = tuple(float(x) for x in component["size"])
    color = tuple(float(x) for x in component.get("color", (0.22, 0.12, 0.06)))
    assembly = RelationAssembly(f"FineScene_Decor_{component['kind']}")
    if component["kind"] == "lamp":
        assembly.rigid("detail/base", Cylinder(min(size[:2]) * .4, height=size[2] * .06, center=(0, 0, size[2] * .03), color=METAL))
        assembly.rigid("detail/stand", Cylinder(min(size[:2]) * .055, height=size[2] * .64, center=(0, 0, size[2] * .38), color=METAL))
        # A tapered shade with a brass rim, assembled from short circular bands.
        for j in range(12):
            radius = min(size[:2]) * (.5 - .21*j/11)
            assembly.rigid(f"detail/shade_band_{j}", Cylinder(radius, height=size[2]*.025,
                center=(0,0,size[2]*(.70+.025*(j+.5))), color=(.055,.16,.075)))
        assembly.rigid("detail/shade_rim", Cylinder(min(size[:2])*.5,height=.005,center=(0,0,size[2]*.70+.0025),color=(.38,.25,.07)))
    elif component["kind"] == "book_stack":
        for index in range(3):
            dims = (size[0]-index*.012,size[1]-index*.01,size[2]/3)
            book = build_simple_detail({"component_id":f"book_{index+1}","kind":"book","size":dims,"layout_variant":"horizontal_stack","palette_index":index+2})
            assembly.rigid(f"detail/book_{index+1}",translate_shape(book.scene,(0,0,index*size[2]/3)))
    elif component["kind"] == "plant":
        radius = min(size[0],size[1])*.24
        assembly.rigid("pot/body",Cylinder(radius,height=size[2]*.30,center=(0,0,size[2]*.15),color=(.27,.10,.045)))
        assembly.rigid("pot/rim",Cylinder(radius*1.1,height=size[2]*.045,center=(0,0,size[2]*.285),color=(.38,.17,.08)))
        assembly.rigid("pot/soil",Cylinder(radius*.92,height=.008,center=(0,0,size[2]*.31),color=(.04,.025,.012)))
        for j in range(7):
            import math
            angle=j*2.4
            end=(math.cos(angle)*size[0]*.28,math.sin(angle)*size[1]*.28,size[2]*(.65+.035*j))
            assembly.rigid(f"stems/{j}",Cylinder(.006,p0=(0,0,size[2]*.3),p1=end,color=(.06,.11,.025)))
            leaf = Cube((size[0]*.26,size[1]*.10,.012),center=end,color=(.035+.012*j,.14+.008*j,.045))
            leaf = rotate_shape(leaf,axis="+y",angle=25 if j%2 else -25)
            leaf = rotate_shape(leaf,axis="+z",angle=math.degrees(angle))
            assembly.rigid(f"leaves/{j}",leaf)
    elif component["kind"] == "side_table":
        assembly.box("detail/top", (size[0], size[1], .04), (0, 0, size[2] - .02), color)
        for x in (-1, 1):
            for y in (-1, 1):
                assembly.box(f"detail/leg_{x}_{y}", (.04, .04, size[2] - .04), (x * (size[0] / 2 - .04), y * (size[1] / 2 - .04), (size[2] - .04) / 2), color)
    else:
        assembly.box("detail/body", size, (0, 0, size[2] / 2), color)
    result = assembly.compile()
    return ComponentBuild(component["component_id"], "build_decor", result.scene, result.values, result.report, {"floor_contact": {"kind": "support", "normal": "+Z"}})


BOOK_PALETTE = ((.19,.035,.025),(.045,.11,.075),(.16,.09,.035),(.08,.10,.18),(.42,.24,.08),(.27,.08,.06),(.09,.17,.18),(.32,.26,.16))


def build_simple_detail(component: dict[str, Any]) -> ComponentBuild:
    size = tuple(float(v) for v in component.get("geometry_size",component.get("size",(.045,.22,.29))))
    assembly = RelationAssembly("FineScene_Book")
    w,d,h = size
    color = tuple(component.get('color',BOOK_PALETTE[int(component.get("palette_index",0)) % len(BOOK_PALETTE)]))
    if component.get("kind") == "bookend":
        assembly.box("bookend/foot",(w,d,.006),(0,0,.003),(.16,.10,.035))
        assembly.box("bookend/upright",(.006,d,h-.006),(w/2-.003,0,(h+.006)/2),(.16,.10,.035))
        assembly.box("bookend/front_trim",(.010,.012,h*.7),(w/2-.005,-d/2+.006,h*.5),(.38,.26,.07))
    elif component.get("kind") != "book":
        assembly.box("detail/body",size,(0,0,h/2),color)
    else:
        flat = component.get("layout_variant") == "horizontal_stack"
        style_name=component.get('binding_style','classic')
        cover = min(.0012 if style_name=='paperback' else .004, (h if flat else w)*.14)
        paper = (.72,.65,.48)
        gold = (.43,.29,.095)
        if flat:
            for z,name in ((cover/2,"bottom"),(h-cover/2,"top")):
                assembly.box(f"cover/{name}",(w,d,cover),(0,0,z),color)
            assembly.box("pages/block",(w-.014,d-.020,h-2*cover),(0,0,h/2),paper)
            assembly.box("binding/spine",(w,.004,h-2*cover),(0,-d/2+.004,h/2),color)
            for x in ((-w*.34,w*.34) if style_name in {'classic','leather'} else ()):
                assembly.box(f"binding/band_{x}",(.006,.001,h*.68),(x,-d/2+.0005,h/2),gold)
            for k in range(1,5):
                z = cover+(h-2*cover)*k/5
                assembly.box(f"pages/edge_{k}",(w-.016,.001,.0007),(0,d/2-.0095,z),(.43,.35,.22))
                assembly.box(f"pages/side_edge_{k}",(.001,d-.022,.0007),(w/2-.0065,0,z),(.43,.35,.22))
        else:
            for x,name in ((-w/2+cover/2,"left"),(w/2-cover/2,"right")):
                assembly.box(f"cover/{name}",(cover,d,h),(x,0,h/2),color)
            assembly.box("pages/block",(w-2*cover,d-.020,h-.016),(0,0,h/2),paper)
            assembly.box("binding/spine",(w-2*cover,.004,h),(0,-d/2+.004,h/2),color)
            for k,z in enumerate((h*.13,h*.22,h*.78,h*.87) if style_name in {'classic','leather'} else ()):
                assembly.box(f"binding/raised_band_{k}",(w*.9,.0015,.003),(0,-d/2+.00075,z),gold)
            style = int(component.get("palette_index",0)) % 3
            if style != 1 and style_name in {'classic','leather'}:
                assembly.box("binding/title_panel",(w*.72,.001,.052 if style==0 else .035),(0,-d/2+.001,h*.60),(.16,.105,.055))
            for k in range(3):
                assembly.box(f"binding/title_rule_{k}",(w*(.45 if k==1 else .30),.001,.0018),(0,-d/2+.0005,h*.60+(k-1)*.008),gold)
            for k in range(1,5):
                x = -(w-2*cover)/2+(w-2*cover)*k/5
                assembly.box(f"pages/top_edge_{k}",(.0006,d-.022,.001),(x,0,h-.0075),(.43,.35,.22))
    result = assembly.compile()
    scene = result.scene
    lean = float(component.get("layout_lean_deg",0))
    if lean:
        scene = rotate_shape(scene,axis="+y",angle=lean,center=(0,0,0))
        low,high = shape_aabb(scene)
        scene = translate_shape(scene,(-(low[0]+high[0])/2,0,-low[2]))
    return ComponentBuild(component["component_id"],"build_bound_book",scene,result.values,result.report,{"support":{"kind":"contact","normal":"+Z"}})


def build_bookshelf(
    component_id: str = "bookshelf_01",
    *,
    width: float = 1.25,
    depth: float = 0.42,
    height: float | None = None,
    shelf_clearance: float | None = None,
    shelf_thickness: float = 0.035,
    levels: int = 4,
    include_shelves: bool = True,
    include_books: bool = True,
) -> ComponentBuild:
    levels = int(levels)
    if not 2 <= levels <= 8:
        raise ValueError("Bookshelf levels must be between 2 and 8")
    if shelf_clearance is None and height is not None:
        shelf_clearance = (float(height) - 0.18 - (levels - 1) * shelf_thickness) / levels
    if shelf_clearance is None:
        shelf_clearance = 0.5
    if shelf_clearance <= 0:
        raise ValueError("Bookshelf height and levels produce a non-positive shelf clearance")
    width_value = float(width)
    depth_value = float(depth)
    assembly = RelationAssembly("FineScene_Bookshelf")
    width = assembly.parameter("width", width)
    depth = assembly.parameter("depth", depth)
    thickness = assembly.parameter("shelf_thickness", shelf_thickness)
    clearance = assembly.parameter("shelf_clearance", shelf_clearance)
    shelf_top = [assembly.datum("shelf_1_top", 0.18)]
    for index in range(1, levels):
        shelf_top.append(assembly.datum(f"shelf_{index + 1}_top", shelf_top[-1] + clearance + thickness))
    crown = assembly.datum("crown_top", shelf_top[-1] + clearance)
    assembly.span_box("carcass/left_side", bottom=0, top=crown - .04, width=0.06, depth=depth, x=width * -0.5 + 0.03, color=WOOD)
    assembly.span_box("carcass/right_side", bottom=0, top=crown - .04, width=0.06, depth=depth, x=width * 0.5 - 0.03, color=WOOD)
    assembly.span_box("carcass/back", bottom=0, top=crown - .04, width=width - 0.12, depth=0.035, y=depth / 2 - 0.0175, color=WOOD)
    if include_shelves:
        for index, top in enumerate(shelf_top, 1):
            assembly.box(f"shelves/level_{index}", (width - 0.12, depth - 0.035, thickness), (0, 0, top - thickness / 2), WOOD)
            if not include_books:
                continue
            book_count = max(4, min(22, int(width_value / 0.16)))
            book_pitch = (width_value - 0.18) / book_count
            book_width = book_pitch * 0.76
            clearance_value = float(shelf_clearance)
            book_height = clearance_value * 0.72
            for book_index in range(book_count):
                x = -width_value / 2 + 0.09 + book_pitch * (book_index + 0.5)
                color = ((0.19, 0.035, 0.025), (0.045, 0.11, 0.075), (0.16, 0.09, 0.035), (0.08, 0.10, 0.18), (0.42, 0.24, 0.08))[book_index % 5]
                assembly.box(
                    f"contents/shelf_{index}/book_{book_index + 1:02d}",
                    (book_width, depth_value * 0.62, book_height),
                    (x, -depth_value * 0.12, top + book_height / 2),
                    color,
                )
    assembly.box("carcass/crown", (width + .06, depth + .02, .04), (0, 0, crown - .02), WOOD)
    assembly.box("trim/crown_front", (width + .08, .035, .055), (0, depth * -.5, crown - .0425), TRIM)
    assembly.box("base/plinth", (width + 0.08, depth + 0.04, 0.12), (0, 0, 0.06), WOOD)
    result = assembly.compile()
    interfaces = {
        "floor_contact": {"kind": "support", "anchor": "bottom", "normal": "+Z"},
        "front_frame": {"kind": "mount", "anchor": "front", "normal": "-Y"},
        "shelf_support_surface": {"kind": "support", "count": levels, "normal": "+Z"},
    }
    return ComponentBuild(component_id, "build_bookshelf", result.scene, result.values, result.report, interfaces)


def build_shelf(component_id: str = "bookshelf_01.shelf_1", *, width: float = 1.13, depth: float = 0.385, thickness: float = 0.035) -> ComponentBuild:
    """Build one shelf board as an independently addressable support surface."""
    assembly = RelationAssembly("FineScene_ShelfBoard")
    assembly.box("shelf/board", (float(width), float(depth), float(thickness)), (0, 0, float(thickness) / 2), WOOD)
    result = assembly.compile()
    return ComponentBuild(
        component_id,
        "build_shelf",
        result.scene,
        result.values,
        result.report,
        {"support_surface": {"kind": "support", "anchor": "top", "normal": "+Z"}},
    )


def build_desk(component_id: str = "desk_01", *, width: float = 1.5, depth: float = 0.72, height: float = 0.76) -> ComponentBuild:
    assembly = RelationAssembly("FineScene_Desk")
    desk_width = assembly.parameter("width", width)
    desk_depth = assembly.parameter("depth", depth)
    desk_height = assembly.parameter("height", height)
    top_thickness = assembly.parameter("top_thickness", 0.06)
    assembly.box("top", (desk_width, desk_depth, top_thickness), (0, 0, desk_height - top_thickness / 2), WOOD)
    leg_height = desk_height - top_thickness
    for x in (-1, 1):
        for y in (-1, 1):
            assembly.span_box(f"legs/{'left' if x < 0 else 'right'}_{'front' if y < 0 else 'back'}", bottom=0, top=leg_height, width=0.08, depth=0.08, x=x * (desk_width / 2 - 0.10), y=y * (desk_depth / 2 - 0.10), color=WOOD)
    assembly.box("drawer", (desk_width * 0.42, desk_depth * 0.45, 0.10), (0, desk_depth * 0.12, desk_height - 0.15), WOOD)
    result = assembly.compile()
    interfaces = {
        "floor_contact": {"kind": "support", "anchor": "bottom", "normal": "+Z"},
        "work_surface": {"kind": "support", "anchor": "top", "normal": "+Z"},
        "front_clearance": {"kind": "clearance", "anchor": "front", "normal": "-Y"},
    }
    return ComponentBuild(component_id, "build_desk", result.scene, result.values, result.report, interfaces)


def build_desk_chair(
    component_id: str = "chair_01",
    *,
    width: float = 0.58,
    depth: float = 0.58,
    height: float = 0.98,
    seat_height: float = 0.46,
) -> ComponentBuild:
    """Parametric task chair used when a refined reading chair is not requested."""
    assembly = RelationAssembly("FineScene_DeskChair")
    seat = assembly.box("seat", (width, depth, 0.10), (0, 0, seat_height), WOOD)
    assembly.box("back", (width, 0.10, max(height - seat_height, 0.3)), (0, depth * 0.38, seat_height + (height - seat_height) / 2), WOOD)
    leg_height = max(seat_height - 0.05, 0.1)
    for x in (-1, 1):
        for y in (-1, 1):
            assembly.span_box(f"legs/{x}_{y}", bottom=0, top=leg_height, width=0.055, depth=0.055, x=x * (width / 2 - 0.08), y=y * (depth / 2 - 0.08), color=WOOD)
    result = assembly.compile()
    interfaces = {
        "floor_contact": {"kind": "support", "anchor": "bottom", "normal": "+Z"},
        "seat_surface": {"kind": "support", "height": seat_height, "normal": "+Z"},
        "desk_clearance": {"kind": "clearance", "normal": "-Y"},
    }
    return ComponentBuild(component_id, "build_desk_chair", result.scene, result.values, result.report, interfaces)


def build_parametric_reading_chair(
    component_id: str = "reading_chair_01",
    *,
    width: float = 0.78,
    depth: float = 0.82,
    height: float = 1.02,
    seat_height: float = 0.44,
) -> ComponentBuild:
    """Coarse but coherent lounge-chair fallback for an unrefined reading seat."""
    frame = RelationAssembly("FineScene_ReadingChair")
    seat_width = float(width)
    seat_depth = float(depth)
    body_color = (0.23, 0.075, 0.045)
    cushion_color = (0.32, 0.13, 0.085)
    leg_height = max(float(seat_height) - 0.08, 0.18)
    frame.box("frame/seat_rail", (seat_width * 0.88, seat_depth * 0.84, 0.12), (0, 0, leg_height + 0.04), WOOD)
    frame.box("upholstery/seat_cushion", (seat_width * 0.84, seat_depth * 0.78, 0.15), (0, 0, seat_height), cushion_color)
    back_height = max(float(height) - float(seat_height), 0.42)
    frame.box("upholstery/back", (seat_width * 0.78, 0.14, back_height), (0, seat_depth * 0.35, seat_height + back_height / 2 - 0.04), cushion_color)
    arm_z = seat_height + 0.18
    for side in (-1, 1):
        x = side * seat_width * 0.43
        frame.span_box(f"frame/legs_{side}", bottom=0, top=leg_height, width=0.07, depth=0.07, x=x, y=0, color=WOOD)
        frame.box(f"frame/arm_support_{side}", (0.075, seat_depth * 0.62, 0.20), (x, 0.02, arm_z), body_color)
        frame.box(f"upholstery/arm_pad_{side}", (0.12, seat_depth * 0.66, 0.10), (x, 0.02, arm_z + 0.14), cushion_color)
    result = frame.compile()
    interfaces = {
        "floor_contact": {"kind": "support", "anchor": "bottom", "normal": "+Z"},
        "seat_surface": {"kind": "support", "height": seat_height, "normal": "+Z"},
        "front_clearance": {"kind": "clearance", "anchor": "front", "normal": "-Y"},
    }
    return ComponentBuild(component_id, "build_parametric_reading_chair", result.scene, result.values, result.report, interfaces)


def build_armchair(component_id: str = "chair_01") -> ComponentBuild:
    """Mount the previously refined Oakwarden armchair as a rigid aDSL component."""
    mesh_path = fenv.OAKWARDEN_DIR / "review_02" / "medieval_armchair.glb"
    if not mesh_path.is_file():
        mesh_path = fenv.OAKWARDEN_DIR / "medieval_armchair.glb"
    if not mesh_path.is_file():
        raise FileNotFoundError(f"The refined armchair asset is missing: {mesh_path}")
    bounds = ((-0.53, -0.4625, 0.0), (0.53, 0.4625, 1.68))
    assembly = RelationAssembly("FineScene_Oakwarden_Armchair")
    armchair = ExternalMesh(mesh_path, bounds, label="Oakwarden_Medieval_Armchair")
    assembly.rigid("oakwarden_refined_mesh", armchair)
    result = assembly.compile()
    interfaces = {
        "floor_contact": {"kind": "support", "anchor": "bottom", "normal": "+Z"},
        "seat_surface": {"kind": "support", "anchor": "top", "normal": "+Z", "height": 0.58},
        "front_clearance": {"kind": "clearance", "anchor": "front", "normal": "-Y"},
        "back_direction": {"kind": "orientation", "axis": "+Y"},
    }
    result.report["external_asset"] = str(mesh_path)
    result.report["refinement_lineage"] = "aDSL native structure + Blender surface refinement (Oakwarden)"
    return ComponentBuild(component_id, "build_armchair", result.scene, result.values, result.report, interfaces)


FACTORIES: dict[str, Callable[..., ComponentBuild]] = {
    "bookshelf": build_bookshelf,
    "shelf": build_shelf,
    "desk": build_desk,
    "chair": build_armchair,
}


def build_component(component: dict[str, Any]) -> ComponentBuild:
    component_id = component["component_id"]
    kind = component["kind"]
    if kind == "room_shell":
        return build_room_shell(component_id, **component.get("parameters", {}))
    if kind in {"rug", "lamp", "book_stack", "plant", "side_table"}:
        return build_decor(component)
    if kind in {"book", "bookend", "prop", "detail"}:
        return build_simple_detail(component)
    factory = FACTORIES.get(kind)
    if factory is None:
        raise ValueError(f"No aDSL factory registered for component kind: {kind}")
    if kind == "chair" and not component.get("refined_blend"):
        role = str(component.get("role", "")).lower()
        factory = build_parametric_reading_chair if role == "reading" else build_desk_chair
    parameters = component.get("parameters", {})
    if kind == "bookshelf" and "shelves" in parameters and "levels" not in parameters:
        parameters = {**parameters, "levels": parameters["shelves"]}
    accepted = inspect.signature(factory).parameters
    filtered = {key: value for key, value in parameters.items() if key in accepted}
    built = factory(component_id, **filtered)
    ignored = sorted(set(parameters) - set(filtered))
    if ignored:
        built.report["ignored_planner_parameters"] = ignored
    return built


__all__ = ["ComponentBuild", "FACTORIES", "build_component", "build_bookshelf", "build_shelf", "build_desk", "build_desk_chair", "build_parametric_reading_chair", "build_armchair", "build_room_shell", "build_decor"]
