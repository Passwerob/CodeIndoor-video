"""Fridge housing, written by hand from the video (t = 79-103 s, 193 s).

Two tall light-oak side panels either side of the fridge, from the floor to
the top of the wall cabinets, standing proud of the wall cabinet doors; the
wall cabinet run fills the top between them, the niche above the fridge is open
to the tiled wall. Local frame: front = -y, origin bottom centre (x = world -y).
"""


def build(B, mats, P):
    W, D, Ht = P["width"], P["depth"], P["height"]
    t = 0.025
    for name, sx in (("housing_panel_south", 1), ("housing_panel_north", -1)):
        B.box(name, (t, D, Ht), (sx * (W / 2 - t / 2), 0, Ht / 2), "wood:#C9A980:0.42", bevel=0.0015)
    return B.objects_created()
