"""Haier French-door fridge, written by hand from the video (t = 63-88 s, 186-194 s, 266 s).

Grey glass fronts (two upper doors, two drawers), very glossy; dark grey
body; black hinge covers on the top front. Hidden grips: a vertical channel
along each door's inner edge and a horizontal channel along each drawer's top
edge (the fronts are split around them so the recess is real, >= 20 mm deep).
Right door: silver 'Haier' and the blue energy label below it; left door: a
printed notice in a clear sleeve. Doors hinge on their outer edges, drawers
slide out. Local frame: front = -y, origin bottom centre, metres.
"""
import hand_lib as H


def build(B, mats, P):
    W, D, Ht = P["width"], P["depth"], P["height"]
    tex = P["tex_dir"] + "/"
    glass = H.material("fr_glass", "#474A4F", rough=0.035, coat=1.0, key="glass:#2B2E33:0.04")
    body = H.material("fr_body", "#3C3F44", rough=0.3, coat=0.3, key="paint:#2A2C30:0.2")
    recess = "plastic:#34373C:0.5"            # reads as a shadowed groove, not a black band
    yf, yb = -D / 2, D / 2
    t = 0.05                                       # front thickness (doors, drawer fronts)
    gap = 0.004
    # ---- body: a shell (sides, top, bottom, back) with a white liner, two
    # dividers making the door compartment and the two drawer compartments,
    # glass shelves behind the doors
    white = H.material("fr_liner", "#ECECEA", rough=0.35, key="plastic:#ECECEA:0.35")
    shelf_glass = H.material("fr_shelf", "#DDE4E4", rough=0.05, transmission=0.9, key="glass:#DDE4E4:0.05")
    y0, y1 = yf + t + 0.003, yb
    ts = 0.025
    def panel(name, size, loc, m):
        B.box(name, size, loc, None, bevel=0.002).data.materials.append(m)
    for e, sx in (("l", -1), ("r", 1)):
        panel(f"main_body_side_{e}", (ts, y1 - y0, Ht - 0.02), (sx * (W / 2 - 0.003 - ts / 2), (y0 + y1) / 2, 0.02 + (Ht - 0.02) / 2), body)
    panel("main_body", (W - 0.006, y1 - y0, 0.03), (0, (y0 + y1) / 2, Ht - 0.015), body)                       # top
    panel("main_body_bottom", (W - 0.006, y1 - y0, 0.03), (0, (y0 + y1) / 2, 0.02 + 0.015), body)
    panel("main_body_back", (W - 0.006, 0.05, Ht - 0.02), (0, y1 - 0.025, 0.02 + (Ht - 0.02) / 2), body)
    xi = W / 2 - 0.003 - ts                          # inner half width
    for name, zc, th in (("main_body_divider_doors", 0.5375, 0.025), ("main_body_divider_drawers", 0.2725, 0.012)):
        panel(name, (2 * xi, y1 - y0 - 0.05, th), (0, (y0 + y1 - 0.05) / 2, zc), white)
    for k, zc in enumerate((0.82, 1.10)):
        B.box(f"main_body_shelf_{k + 1}", (2 * xi - 0.004, y1 - y0 - 0.09, 0.006), (0, (y0 + 0.04 + y1 - 0.05) / 2, zc), None
              ).data.materials.append(shelf_glass)
    # white liner on the inner faces of the door compartment (the shell stays grey outside)
    panel("main_body_liner_back", (2 * xi, 0.004, Ht - 0.6), (0, y1 - 0.052, 0.55 + (Ht - 0.6) / 2), white)
    for e, sx in (("l", -1), ("r", 1)):
        panel(f"main_body_liner_{e}", (0.004, y1 - y0 - 0.06, Ht - 0.6), (sx * (xi - 0.002), (y0 + y1 - 0.05) / 2,
                                                                          0.55 + (Ht - 0.6) / 2), white)
    B.box("main_body_plinth", (W - 0.04, D - 0.08, 0.02), (0, 0.02, 0.01), "plastic:#16171A:0.6")
    # ---- doors: split around the vertical grip channel on the inner edge ------------
    z0, z1 = 0.53, Ht - 0.03
    dh = z1 - z0
    dw = (W - gap) / 2
    cw, cdep, c0, c1 = 0.0155, 0.021, z0 + 0.22 * dh, z0 + 0.80 * dh   # channel width/depth/extent
    for name, s in (("upper_left_door", -1), ("upper_right_door", 1)):
        x_in = s * gap / 2                         # inner edge (next to the other door)
        x_out = s * (gap / 2 + dw)
        xm = x_in + s * cw                         # where the channel ends towards the outer edge
        main_w = abs(x_out - xm)
        B.box(name, (main_w, t, dh), ((x_out + xm) / 2, yf + t / 2, z0 + dh / 2), None, bevel=0.003
              ).data.materials.append(glass)
        for part, za, zb in (("lower", z0, c0), ("upper", c1, z1)):
            B.box(f"{name}_edge_{part}", (cw, t, zb - za), ((x_in + xm) / 2, yf + t / 2, (za + zb) / 2), None,
                  bevel=0.002).data.materials.append(glass)
        B.box(f"{name}_grip_channel", (cw, t - cdep, c1 - c0), ((x_in + xm) / 2, yf + cdep + (t - cdep) / 2, (c0 + c1) / 2),
              recess, bevel=0.001)
    # white inner liners on the doors' backs (move with the doors)
    for name, sgn in (("upper_left_door", -1), ("upper_right_door", 1)):
        xc = sgn * (gap / 2 + dw / 2)
        B.box(f"{name}_liner", (dw - 0.06, 0.012, dh - 0.07), (xc, yf + t + 0.006, z0 + dh / 2), None, bevel=0.003
              ).data.materials.append(white)
    # ---- drawers: split around the horizontal channel along the top edge ---------
    for name, za, zb in (("lower_drawer_1", 0.275, 0.525), ("lower_drawer_2", 0.025, 0.27)):
        hh = 0.018                                 # channel height
        cwid = 0.62 * W
        B.box(name, (W - 0.004, t, zb - za - hh), (0, yf + t / 2, za + (zb - za - hh) / 2), None, bevel=0.003
              ).data.materials.append(glass)
        side = (W - 0.004 - cwid) / 2
        for sx in (-1, 1):
            B.box(f"{name}_top_{'left' if sx < 0 else 'right'}", (side, t, hh),
                  (sx * (cwid / 2 + side / 2), yf + t / 2, zb - hh / 2), None, bevel=0.002).data.materials.append(glass)
        B.box(f"{name}_grip_channel", (cwid, t - cdep, hh), (0, yf + cdep + (t - cdep) / 2, zb - hh / 2), recess,
              bevel=0.001)
        # the bin behind the front: open top, slides out with it
        bz0, bz1 = max(za + 0.02, 0.056), zb - 0.03       # clear of the shell bottom / divider
        bl = (y1 - 0.06) - (yf + t)
        byc = yf + t + bl / 2
        bw = 2 * xi - 0.02
        B.box(f"{name}_bin", (bw, bl, 0.006), (0, byc, bz0 + 0.003), None).data.materials.append(white)
        for e, sx in (("l", -1), ("r", 1)):
            B.box(f"{name}_bin_side_{e}", (0.006, bl, bz1 - bz0), (sx * (bw / 2 - 0.003), byc, (bz0 + bz1) / 2), None
                  ).data.materials.append(white)
        B.box(f"{name}_bin_back", (bw, 0.006, bz1 - bz0), (0, yf + t + bl - 0.003, (bz0 + bz1) / 2), None
              ).data.materials.append(white)
    # ---- top: hinge covers -----------------------------------------------------------
    for k, x in enumerate((-W / 2 + 0.06, 0.0, W / 2 - 0.06)):
        B.box("top_hinge_cover" if k == 1 else f"top_hinge_cover_{k}", (0.045 if k != 1 else 0.035, 0.05, 0.008),
              (x, yf + 0.032, Ht - 0.004), "plastic:#0E0E10:0.25", bevel=0.002)
    # ---- graphics ---------------------------------------------------------------------
    xr = W / 2 - 0.11
    H.text(B, "brand_logo", "Haier", 0.034, (xr, yf - 0.0012, z1 - 0.075), "metal:#C9CBCE:0.3", extrude=0.0003,
           bold_offset=0.0003)
    H.decal(B, "energy_label", H.image_material("fr_energy", tex + "energy_label.png", rough=0.4,
                                                 key="plastic:#1E7FC2:0.75"),
            0.074, 0.197, (xr + 0.005, yf - 0.0012, z1 - 0.09 - 0.0985), "-y", thickness=0.0008)
    H.decal(B, "upper_left_door_notice", H.image_material("fr_notice", tex + "notice.png", rough=0.25, coat=0.5),
            0.215, 0.30, (-W / 4 - 0.01, yf - 0.0015, z1 - 0.20 - 0.15), "-y", thickness=0.0012, round_r=0.004)
    return B.objects_created()
