"""Floor safety label, written by hand from the video (t = 63 s, 193 s).

One long sticker stuck flat on the floor beside the aluminium step nosing:
yellow half '小心台阶 WATCH YOUR STEP' (stair pictogram), white half '小心地滑
CAUTION WET FLOOR' (slipping figure). The image is the label itself, cut out of
the footage. Local frame: long axis = x, the edge facing -y lies along the nosing.
"""
import hand_lib as H


def build(B, mats, P):
    W, D, Ht = P["width"], P["depth"], P["height"]
    m = H.image_material("floor_label", P["tex_dir"] + "/label.png", rough=0.3, coat=0.4,
                         key="plastic:#E8B021:0.3")
    t = max(Ht, 0.0008)
    split = 0.5                                    # the yellow half ends where the white begins
    H.decal(B, "yellow_step_panel", m, W * split, D, (-W / 2 + W * split / 2, 0, t), "+z", thickness=t,
            uv_rect=(0.0, 0.0, split, 1.0))
    H.decal(B, "white_wet_floor_panel", m, W * (1 - split), D, (W * split / 2, 0, t), "+z", thickness=t,
            uv_rect=(split, 0.0, 1.0, 1.0))
    return B.objects_created()
