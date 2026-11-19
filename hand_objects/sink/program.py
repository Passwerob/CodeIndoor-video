"""Undermount stainless sink: the part-checked forge program (2026-11-14, 4 readings), rebuilt
at the bowl opening measured from the point cloud (0.60 x 0.345 m) so it is not scaled."""
def build(B, mats, P):
    W = P["width"]; D = P["depth"]; H = P["height"]

    # Brushed stainless: medium grey, directional brushed, real metal
    steel       = "metal:#B4B6B8:0.38:brushed"
    steel_inner = "metal:#B5B8BB:0.32:brushed"
    steel_dark  = "metal:#6E7378:0.75"
    steel_basket= "metal:#A9AEB3:0.08"
    steel_drain = "metal:#8A8F94:0.38"

    objs = []

    # =========================================================
    # sink_body : outer housing, undermount, thin straight top edge
    # No rolled rim, no inner ledge. Walls run straight up to top.
    # =========================================================
    wall = 0.006
    bw = W * 0.99
    bd = D * 0.99
    bh = H * 0.99

    # bottom
    objs.append(B.box("sink_body_bottom", (bw, bd, wall),
                      (0.0, 0.0, wall * 0.5), steel, bevel=0.002))
    # four side walls -- thin straight edges, no top return
    objs.append(B.box("sink_body_wall_front", (bw, wall, bh),
                      (0.0, -bd * 0.5 + wall * 0.5, bh * 0.5), steel, bevel=0.001))
    objs.append(B.box("sink_body_wall_back", (bw, wall, bh),
                      (0.0,  bd * 0.5 - wall * 0.5, bh * 0.5), steel, bevel=0.001))
    objs.append(B.box("sink_body_wall_left", (wall, bd - 2 * wall, bh),
                      (-bw * 0.5 + wall * 0.5, 0.0, bh * 0.5), steel, bevel=0.001))
    objs.append(B.box("sink_body_wall_right", (wall, bd - 2 * wall, bh),
                      ( bw * 0.5 - wall * 0.5, 0.0, bh * 0.5), steel, bevel=0.001))

    # =========================================================
    # bowl_interior : inner liner, clean flat four walls, rounded corners
    # =========================================================
    iw = W * 0.92
    id_ = D * 0.90
    ih = H * 0.90
    iwall = 0.004

    objs.append(B.box("bowl_interior_bottom", (iw, id_, iwall),
                      (0.0, 0.0, wall + iwall * 0.5), steel_inner, bevel=0.003))
    objs.append(B.box("bowl_interior_front", (iw, iwall, ih),
                      (0.0, -id_ * 0.5 + iwall * 0.5, ih * 0.5), steel_inner, bevel=0.003))
    objs.append(B.box("bowl_interior_back", (iw, iwall, ih),
                      (0.0,  id_ * 0.5 - iwall * 0.5, ih * 0.5), steel_inner, bevel=0.003))
    objs.append(B.box("bowl_interior_left", (iwall, id_ - 2 * iwall, ih),
                      (-iw * 0.5 + iwall * 0.5, 0.0, ih * 0.5), steel_inner, bevel=0.003))
    objs.append(B.box("bowl_interior_right", (iwall, id_ - 2 * iwall, ih),
                      ( iw * 0.5 - iwall * 0.5, 0.0, ih * 0.5), steel_inner, bevel=0.003))

    # vertical corner fillets (small, tucked into the corners)
    cr = 0.010
    for sx in (-1, 1):
        for sy in (-1, 1):
            objs.append(B.cylinder("bowl_interior_corner_%d_%d" % (sx, sy),
                                   cr, ih,
                                   (sx * (iw * 0.5 - cr),
                                    sy * (id_ * 0.5 - cr),
                                    ih * 0.5),
                                   steel_inner, verts=16))

    # =========================================================
    # overflow_vent : small slotted grille on back wall, upper right
    # (no vertical pipe -- just a flush slot in the wall)
    # =========================================================
    ov_w = W * 0.12
    ov_h = H * 0.05
    ov_x = (0.88 - 0.5) * W
    ov_z = 0.82 * H
    ov_y = id_ * 0.5 - iwall - 0.0005

    objs.append(B.box("overflow_vent", (ov_w, 0.003, ov_h),
                      (ov_x, ov_y, ov_z), steel_dark, bevel=0.0008))
    for i in range(3):
        sz = ov_z + (i - 1) * ov_h * 0.30
        objs.append(B.box("overflow_vent_bar_%d" % (i + 1),
                          (ov_w * 0.85, 0.002, ov_h * 0.16),
                          (ov_x, ov_y - 0.0018, sz), steel_inner, bevel=0.0006))

    # =========================================================
    # drain_hole : round outlet at bowl floor CENTRE, flush
    # =========================================================
    drain_x = 0.0
    drain_y = 0.0
    floor_z = wall + iwall          # top of bowl floor

    # outer flange ring (flush with floor)
    objs.append(B.cylinder("drain_hole", W * 0.115, 0.004,
                           (drain_x, drain_y, floor_z + 0.002),
                           steel, verts=48))
    # recessed cup ring
    objs.append(B.cylinder("drain_hole_ring", W * 0.10, 0.006,
                           (drain_x, drain_y, floor_z + 0.003),
                           steel_drain, verts=48))
    # dark inner cavity
    objs.append(B.cylinder("drain_hole_cavity", W * 0.075, 0.004,
                           (drain_x, drain_y, floor_z + 0.004),
                           steel_dark, verts=32))

    # =========================================================
    # strainer_basket : perforated basket, sits in drain, clear gap
    # =========================================================
    sb_r = W * 0.085
    sb_h = H * 0.03
    sb_z = floor_z + 0.006 + sb_h * 0.5

    # rim ring (visible gap to drain ring)
    objs.append(B.cylinder("strainer_basket_rim", sb_r, 0.003,
                           (drain_x, drain_y, floor_z + 0.0075),
                           steel_basket, verts=48))
    # basket body (slightly smaller than rim so gap reads)
    objs.append(B.cylinder("strainer_basket_body", sb_r * 0.85, sb_h,
                           (drain_x, drain_y, sb_z),
                           steel_basket, verts=48))
    # perforations
    for i in range(8):
        ang = i * (2 * math.pi / 8)
        px = drain_x + math.cos(ang) * sb_r * 0.5
        py = drain_y + math.sin(ang) * sb_r * 0.5
        objs.append(B.cylinder("strainer_basket_hole_%d" % (i + 1),
                               0.0025, 0.002,
                               (px, py, sb_z + sb_h * 0.5 + 0.0005),
                               steel_dark, verts=8))
    objs.append(B.cylinder("strainer_basket_hole_center", 0.003, 0.002,
                           (drain_x, drain_y, sb_z + sb_h * 0.5 + 0.0005),
                           steel_dark, verts=8))

    # =========================================================
    # strainer_lift_joint : lift tab on basket rim
    # =========================================================
    objs.append(B.box("strainer_lift_joint", (0.022, 0.008, 0.005),
                      (drain_x + sb_r * 0.55, drain_y, floor_z + 0.011),
                      steel_basket, bevel=0.001))

    return objs