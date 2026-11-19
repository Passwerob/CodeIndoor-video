"""Water purifier / dispenser (GE 通用净水, "GE water purification"), written by hand from the video.

What the footage shows (t = 0-14 s from the door, 224-259 s up close):
  * a wide white LOWER CABINET (~0.93 m) with rounded vertical edges, a small
    grey square badge top-left on its front and a dark stadium-shaped DRIP
    TRAY grille on the front of its top;
  * a narrower white UPPER COLUMN standing on its back part: two side cheeks
    whose top-front corners are rounded with a large radius, a recessed
    dispensing niche between them (red CAUTION sticker on its back wall);
  * a dark charcoal CONTROL HEAD nested between the cheeks and overhanging
    the niche: GE roundel, 通用净水 (brand text) / 400-788-7171, a round blue display with
    two touch keys either side, a small spout under it;
  * stickers on the east (right-hand) cheek; a black power cord leaving the
    head's back-right corner and hanging to the floor.
Local frame: front = -y, origin at the bottom centre, metres.
"""
import math

import hand_lib as H


def build(B, mats, P):
    W, D, Ht = P["width"], P["depth"], P["height"]
    tex = P["tex_dir"] + "/"
    white = H.material("disp_white", "#EDEDEB", rough=0.28, coat=0.25)
    charcoal = H.material("disp_charcoal", "#303236", rough=0.32, coat=0.35)
    dark = "plastic:#1E1F21:0.45"
    grey = "plastic:#8E9092:0.4"

    yf, yb = -D / 2, D / 2
    # proportions from F01 (camera at head height): head 0.30 x 0.235, niche
    # ~0.26, lower cabinet the rest; the column is ~12 % narrower than the cabinet
    z_head = Ht - 0.235                           # head underside
    z_cab = z_head - 0.31                         # lower cabinet top (F01 overlay: tray band)
    Wc = 0.955 * W                                # cheeks nearly flush with the cabinet sides (F46)
    # ---- lower cabinet ------------------------------------------------------
    H.rounded_block(B, "base_foot", (W - 0.03, D - 0.03, 0.014), (0, 0, 0), "plastic:#3C3D3F:0.6",
                    r_plan=0.03, r_edge=0.0, r_edge_bot=0.0)
    H.rounded_block(B, "front_lower_panel", (W, D, z_cab - 0.012), (0, 0, 0.012), white,
                    r_plan=0.036, r_edge=0.012, r_edge_bot=0.006)
    H.decal(B, "lower_badge", H.image_material("disp_badge", tex + "badge.png", rough=0.4),
            0.026, 0.026, (-0.115 * W / 0.34, yf - 0.0006, z_cab - 0.075), "-y", round_r=0.003)
    # ---- drip tray on the front of the cabinet top ----------------------------
    tray_d = 0.078
    tray_y = yf + 0.008 + tray_d / 2
    H.rounded_block(B, "niche_floor_tray", (Wc - 0.04, tray_d, 0.014), (0, tray_y, z_cab - 0.004), dark,
                    r_plan=tray_d / 2 - 0.001, r_edge=0.003, r_edge_bot=0.0)
    H.decal(B, "niche_floor_tray_grille", H.image_material("disp_tray", tex + "tray.png", rough=0.5),
            Wc - 0.06, tray_d - 0.016, (0, tray_y, z_cab + 0.0105), "+z", round_r=(tray_d - 0.016) / 2 - 0.001)
    # ---- upper column: two C-shaped cheeks (F46, t = 230 s) -------------------
    # side view: the foot flares forward beside the tray, the edge is recessed
    # along the niche, comes forward again beside the head, and the top-front
    # corner is a large radius; the back is square with a small round.
    cheek_t = 0.018
    col_front = tray_y + tray_d / 2 + 0.004       # niche cheeks behind the tray
    col_back = yb - 0.006
    head_front = yf + 0.012
    Rtop = 0.13
    def qbez(a, c, b, n=8):
        return [((1 - t) ** 2 * a[0] + 2 * (1 - t) * t * c[0] + t * t * b[0],
                 (1 - t) ** 2 * a[1] + 2 * (1 - t) * t * c[1] + t * t * b[1]) for t in [i / n for i in range(n + 1)]]
    foot_front = yf + 0.014
    edge = qbez((foot_front, z_cab), (col_front, z_cab), (col_front, z_cab + 0.09))          # foot flare
    edge += qbez((col_front, z_head - 0.07), (col_front, z_head), (head_front + 0.045, z_head + 0.03))[1:]
    edge += [(head_front + 0.045, Ht - 0.002 - Rtop)]
    edge += H.arc_pts(head_front + 0.045 + Rtop, Ht - 0.002 - Rtop, Rtop, 180, 90, 10)[1:]
    edge += H.arc_pts(col_back - 0.025, Ht - 0.002 - 0.025, 0.025, 90, 0, 5)
    edge += [(col_back, z_cab)]
    outline = edge
    xo = Wc / 2
    for side, sx in (("left", -1), ("right", 1)):
        ob = H.profile_extrude(B, "main_body" if sx < 0 else "main_body_cheek_right", outline, cheek_t, white,
                               plane="yz", offset=sx * (xo - cheek_t / 2))
        H.bevel(ob, 0.007, segments=3, angle_deg=50)
    inner = xo - cheek_t                           # inner faces of the cheeks
    niche_back = col_front + 0.05
    H.rounded_block(B, "dispensing_niche", (2 * inner + 0.002, col_back - niche_back, z_head - z_cab + 0.01),
                    (0, (niche_back + col_back) / 2, z_cab), white, r_plan=0.004, r_edge=0.0, r_edge_bot=0.0)
    H.rounded_block(B, "main_body_back", (2 * inner + 0.002, 0.03, Ht - z_cab - 0.04),
                    (0, col_back - 0.015, z_cab), white, r_plan=0.004, r_edge=0.0, r_edge_bot=0.0)
    H.decal(B, "caution_label", H.image_material("disp_caution", tex + "caution.png", rough=0.35),
            0.10, 0.064, (0, niche_back - 0.0006, z_cab + 0.43 * (z_head - z_cab)), "-y", thickness=0.0004)
    # ---- control head ---------------------------------------------------------
    head_back = col_back - 0.04
    hw = 2 * inner - 0.002
    H.rounded_block(B, "control_head", (hw, head_back - head_front, Ht - z_head),
                    (0, (head_front + head_back) / 2, z_head), charcoal,
                    r_plan=0.03, r_edge=0.016, r_edge_bot=0.008, steps=5)
    zc = z_head + 0.008 + (Ht - z_head - 0.024) / 2
    dw = hw - 0.05                                # width of the head graphic (flat part)
    gh = Ht - z_head - 0.03                       # its height
    H.decal(B, "control_head_graphics", H.image_material("disp_head_front", tex + "head_front.png",
                                                          rough=0.32, coat=0.35),
            dw, gh, (0, head_front - 0.0006, zc), "-y")
    H.decal(B, "display_screen", H.image_material("disp_display", tex + "display.png", rough=0.1,
                                                   emit=6.0, coat=0.5, key="screen:#06060A:0.1:emit=#5C50FF"),
            0.05, 0.05, (0.025 * dw, head_front - 0.0012, zc - 0.015 * gh), "-y", round_r=0.0249)
    # four touch keys sit under the graphic (named for the functional spec;
    # flush capacitive keys: a 0.4 mm proud pad each)
    # each key is its printed icon + label (white on the dark head), cut from the
    # same graphic so it lines up, 0.4 mm proud of the head
    for name, u in (("touch_key_left_1", 0.15), ("touch_key_left_2", 0.29),
                    ("touch_key_right_1", 0.745), ("touch_key_right_2", 0.875)):
        kw, kh, kv = 0.026, 0.032, 0.555                 # size (m), centre (fraction from the top)
        uw, vh = kw / dw, kh / gh
        H.decal(B, name, H.image_material("disp_" + name, tex + "head_front.png", rough=0.25, coat=0.4,
                                          key="paint:#E6E6E6:0.3"),
                kw, kh, ((u - 0.5) * dw, head_front - 0.0010, zc + (0.5 - kv) * gh), "-y", round_r=0.005,
                uv_rect=(u - uw / 2, 1 - kv - vh / 2, u + uw / 2, 1 - kv + vh / 2))
    # spout under the head
    B.box("spout_bracket", (0.045, 0.03, 0.01), (0, head_front + 0.04, z_head - 0.004), grey, bevel=0.002)
    B.cylinder("spout", 0.0065, 0.022, (0, head_front + 0.045, z_head - 0.019), grey, verts=20)
    # ---- stickers on the east cheek (cut out of the video, t = 230 s) ---------
    H.decal(B, "side_labels", H.image_material("disp_side_labels", tex + "side_labels.png", rough=0.4),
            0.19, 0.27, (xo + 0.0006, col_front + 0.135, Ht - 0.20), "+x")
    # ---- vent on the lower east side, near the floor at the back (F46) --------
    H.decal(B, "side_vent_grille", H.image_material("disp_vent", tex + "vent.png", rough=0.4),
            0.14, 0.09, (W / 2 + 0.0006, yb - 0.11, 0.075), "+x")
    # ---- east service panel of the lower cabinet: seam + recessed grip (F46) --
    seam = "plastic:#9C9D9E:0.5"
    zs = z_cab - 0.075
    H.decal(B, "side_panel_seam_top", seam, D - 0.07, 0.0015, (W / 2 + 0.0005, 0.015, zs), "+x")
    H.decal(B, "side_panel_seam_front", seam, 0.0015, zs - 0.03, (W / 2 + 0.0005, yf + 0.05, 0.03 + (zs - 0.03) / 2), "+x")
    H.decal(B, "side_panel_grip_recess", H.image_material("disp_grip", tex + "side_grip.png", rough=0.45),
            0.075, 0.02, (W / 2 + 0.0007, yf + 0.20, zs - 0.022), "+x")
    # ---- drain hose: white tube from the back into the bucket beside it (F46) --
    bx, by = P.get("bucket_local", (0.29, 0.533))
    H.tube(B, "drain_hose", [(W / 2 - 0.06, yb - 0.002, 0.30), (W / 2 - 0.02, yb + 0.06, 0.24),
                             (bx - 0.10, by - 0.13, 0.32), (bx - 0.03, by - 0.03, 0.35), (bx + 0.01, by, 0.22)],
           0.005, "plastic:#F2F2F0:0.35", samples=6)
    # ---- power cord: from the wall outlet on the pier behind-right (F46), a
    # loose loop down to the back of the cabinet top -------------------------
    ox, oy, oz = P.get("outlet_local", (0.141, 0.563, 1.26))
    B.box("power_cord_plug", (0.03, 0.036, 0.042), (ox + 0.010 + 0.015, oy, oz), "plastic:#161616:0.5", bevel=0.004)
    pts = [(ox + 0.04, oy, oz - 0.005), (ox + 0.055, oy - 0.01, oz - 0.06), (ox + 0.06, oy - 0.04, oz - 0.2),
           (ox + 0.05, oy - 0.10, z_cab + 0.05), (ox + 0.03, oy - 0.2, z_cab - 0.02),
           (W / 2 - 0.05, yb + 0.03, z_cab - 0.06), (W / 2 - 0.07, yb + 0.004, z_cab - 0.08)]
    H.tube(B, "power_cord", pts, 0.0032, "rubber:#141414:0.6", samples=6)
    return B.objects_created()
