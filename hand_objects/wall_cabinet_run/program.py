"""Wall cabinet run, written by hand from the video (t = 44-108 s).

Six tall flush light-oak doors (3 mm gaps): two over the fridge niche, four over
the counter, hinged in pairs; their bottom edges overhang the carcass by ~20 mm
as a finger pull (no handles). Local frame: front = -y, origin bottom centre;
local +x runs towards the fridge end (world -y).
"""
import hand_lib as H


def build(B, mats, P):
    W, D, Ht = P["width"], P["depth"], P["height"]
    yf, yb = -D / 2, D / 2
    wood = "wood:#D2B38B:0.42"
    t_door, gap, lip = 0.018, 0.003, 0.02
    split = P.get("split_x", 0.677)                 # where the fridge-niche section starts (world y = 0)
    nb = P.get("niche_bottom", 0.0)                 # its underside above the run's (open niche over the fridge)
    yc = yf + t_door + 0.002 + (D - t_door - 0.002) / 2
    g0 = P.get("split_gap", 0.0125)
    inner, tp = "wood:#D6C3A0:0.5", 0.018
    yc0 = yf + t_door + 0.002
    for name, xa, xb, z0 in (("cabinet_carcass", -W / 2, split - g0, 0.0), ("cabinet_carcass_fridge", split + g0, W / 2, nb)):
        za, zb = z0 + lip, Ht
        B.box(name, (xb - xa, yb - yc0, tp), ((xa + xb) / 2, (yc0 + yb) / 2, za + tp / 2), inner)                 # bottom
        B.box(name + "_top", (xb - xa, yb - yc0, tp), ((xa + xb) / 2, (yc0 + yb) / 2, zb - tp / 2), inner)
        B.box(name + "_back", (xb - xa, 0.008, zb - za), ((xa + xb) / 2, yb - 0.004, (za + zb) / 2), inner)
        for e, xs in (("l", xa + tp / 2), ("r", xb - tp / 2)):
            B.box(f"{name}_side_{e}", (tp, yb - yc0 - 0.008, zb - za - 2 * tp), (xs, (yc0 + yb - 0.008) / 2, (za + zb) / 2), inner)
        B.box(name + "_shelf", (xb - xa - 2 * tp, yb - yc0 - 0.05, tp), ((xa + xb) / 2, (yc0 + 0.03 + yb - 0.008) / 2,
                                                                       (za + zb) / 2), inner)
    B.box("bottom_rail", (split - g0 + W / 2 - 0.01, 0.02, 0.018), ((split - g0 - W / 2) / 2, yf + t_door + 0.012, lip + 0.009),
          "wood:#C8AA82:0.5")
    g = P.get("split_gap", 0.0125)                  # half the gap the housing panel passes through
    sections = [(-W / 2, split - g, 4), (split + g, W / 2, 2)]
    k = 0
    order = []
    for x0, x1, n in sections:
        dw = (x1 - x0 - (n - 1) * gap) / n
        for i in range(n):
            order.append(x0 + dw / 2 + i * (dw + gap))
    # number the doors from the fridge end (spec order), widths per section
    for k, x in enumerate(sorted(order, reverse=True)):
        n = 2 if x > split else 4
        x0, x1 = (split + g, W / 2) if x > split else (-W / 2, split - g)
        dw = (x1 - x0 - (n - 1) * gap) / n
        z0 = nb if x > split else 0.0             # the over-fridge cabinet hangs higher
        B.box(f"door_{k + 1}", (dw, t_door, Ht - 0.002 - z0), (x, yf + t_door / 2, z0 + (Ht - 0.002 - z0) / 2), wood,
              bevel=0.0015)
    return B.objects_created()
