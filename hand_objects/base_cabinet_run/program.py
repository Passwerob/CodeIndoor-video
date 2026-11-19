"""Base cabinet run + worktop, written by hand from the video (t = 23-66 s, 110-210 s).

Four flush, handle-less light-oak doors (3 mm gaps, push-to-open latches hidden
behind them), a black recessed toe kick, a white marble-look worktop ~38 mm
thick overhanging the doors by ~20 mm. The undermount sink is carved out of
the worktop by the scene builder. Local frame: front = -y, origin bottom centre.
"""
import hand_lib as H


def build(B, mats, P):
    W, D, Ht = P["width"], P["depth"], P["height"]
    yf, yb = -D / 2, D / 2
    wood = "wood:#C6A47B:0.42"
    t_top, over = 0.038, 0.02
    t_door, gap, z_kick = 0.018, 0.003, 0.035
    # ---- worktop ------------------------------------------------------------------
    marble = "stone:#EEEEEC:0.16:veins=#B9B9B9:scale=0.7"     # soft grey veins, mostly white (t = 120-160 s)
    top = B.box("countertop", (W, D, t_top), (0, 0, Ht - t_top / 2), marble, bevel=0.003)
    # (the low marble upstand along the walls is a room feature: it stands on
    #  the worktop, above this run's measured box)
    # ---- carcass: a hollow box open at the front (sides, bottom, back, a centre
    # divider between the door pairs, one shelf per bay), a top rail under the worktop
    yc0 = yf + over + t_door + 0.004               # carcass front: 4 mm behind the doors (push-latch travel)
    inner = "wood:#D6C3A0:0.5"
    tp, zc0, zc1 = 0.018, z_kick, Ht - t_top
    x0, x1 = -W / 2 + 0.002, W / 2 - 0.002
    B.box("cabinet_carcass", (x1 - x0, yb - yc0, tp), (0, (yc0 + yb) / 2, zc0 + tp / 2), inner)            # bottom
    B.box("cabinet_carcass_back", (x1 - x0, 0.008, zc1 - zc0 - 0.03), (0, yb - 0.004, (zc0 + 0.03 + zc1) / 2), inner)   # above the skirting
    for nm, xs in (("cabinet_carcass_side_l", x0 + tp / 2), ("cabinet_carcass_side_r", x1 - tp / 2), ("cabinet_carcass_divider", 0.0)):
        B.box(nm, (tp, yb - yc0 - 0.008, zc1 - zc0 - tp), (xs, (yc0 + yb - 0.008) / 2, zc0 + tp + (zc1 - zc0 - tp) / 2), inner)
    zs = zc0 + 0.42 * (zc1 - zc0)
    for nm, xa, xb in (("cabinet_carcass_shelf_l", x0 + tp, -tp / 2), ("cabinet_carcass_shelf_r", tp / 2, x1 - tp)):
        B.box(nm, (xb - xa, yb - yc0 - 0.06, tp), ((xa + xb) / 2, (yc0 + 0.04 + yb - 0.008) / 2, zs), inner)
    B.box("cabinet_carcass_top_rail", (x1 - x0, tp, 0.06), (0, yc0 + tp / 2, zc1 - 0.03), inner)
    B.box("toe_kick", (W - 0.004, yb - yc0 - 0.03, z_kick), (0, (yc0 + 0.03 + yb) / 2, z_kick / 2), "paint:#1E1E1E:0.6")
    for sx in (-1, 1):                               # end panels down to the floor
        B.box(f"cabinet_carcass_end_{'l' if sx < 0 else 'r'}", (0.018, yb - yc0, z_kick + 0.01),
              (sx * (W / 2 - 0.011), (yc0 + yb) / 2, (z_kick + 0.01) / 2), wood)
    # ---- four flush doors (alternating hinges per the spec), latches hidden behind -------
    dw = (W - 3 * gap - 0.004) / 4
    z0, z1 = z_kick + 0.004, Ht - t_top - 0.003
    for k in range(4):
        x = -W / 2 + 0.002 + dw / 2 + k * (dw + gap)
        B.box(f"door_{k + 1}", (dw, t_door, z1 - z0), (x, yf + over + t_door / 2, (z0 + z1) / 2), wood, bevel=0.0015)
        # push latch on the carcass behind the door's free edge (hidden when closed)
        free = x + (dw / 2 - 0.05) * (1 if k % 2 == 0 else -1)
        B.cylinder(f"carcass_push_latch_{k + 1}", 0.007, 0.0215, (free, yc0 - 0.0035 + 0.01075, zc1 - 0.03), "plastic:#202020:0.5",
                   verts=12, rot=(1.5708, 0, 0))                     # set in the top rail, its tip 0.5 mm off the door
    return B.objects_created()
