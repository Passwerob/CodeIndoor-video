"""Galanz microwave, written by hand from the video (t = 128-143 s, 186-187 s).

Glossy black box. Front: a black-glass door over the left ~3/4 with a
see-through viewing window (the cavity and plate show dimly), white 'Galanz'
upper-left, a large C-bow handle on its right edge; a black-glass control strip
on the right with a red LED clock and printed key legends. Top: slot vents at the
back-left and the energy/warning label pair at the front-right corner. Left
side: slot vents near the front. The door hinges on its left edge.
Local frame: front = -y, origin bottom centre, metres.
"""
import math

import hand_lib as H


def build(B, mats, P):
    W, D, Ht = P["width"], P["depth"], P["height"]
    tex = P["tex_dir"] + "/"
    black = H.material("mw_black", "#0C0C0D", rough=0.07, coat=0.8)
    # the door is one black glass sheet: printed opaque round the window (the
    # black frame behind it shows), see-through over the window, where the
    # perforated screen makes the cavity read dim grey (t = 136 s)
    glass = H.material("mw_door_glass", "#B0B0B4", rough=0.02, transmission=1.0, ior=1.5, coat=0.6,
                       key="glass:#1A1A1A:0.04:alpha=0.9")
    screen = H.material("mw_window_screen", "#1C1C1E", rough=0.5, alpha=0.62, key="glass:#1A1A1A:0.5:alpha=0.6")
    yf, yb = -D / 2, D / 2
    foot_h = 0.01
    door_t = 0.024
    panel_w = 0.13
    # ---- feet -------------------------------------------------------------------
    for name, sx, sy in (("front_left_foot", -1, -1), ("front_right_foot", 1, -1),
                         ("rear_left_foot", -1, 1), ("rear_right_foot", 1, 1)):
        B.cylinder(name, 0.013, foot_h, (sx * (W / 2 - 0.05), sy * (D / 2 - 0.05), foot_h / 2),
                   "rubber:#0A0A0A:0.75", verts=16)
    # ---- body with the cooking cavity cut out of it -----------------------------
    body = H.rounded_block(B, "main_body", (W, D - door_t, Ht - foot_h), (0, door_t / 2, foot_h), black,
                           r_plan=0.012, r_edge=0.006, r_edge_bot=0.003)
    cx0, cx1 = -W / 2 + 0.03, W / 2 - panel_w - 0.03
    cz0, cz1 = foot_h + 0.03, Ht - 0.03
    cy0, cy1 = yf + door_t - 0.01, yb - 0.06
    H.cut(body, [H.cutter_box("mw_cavity_cut", (cx1 - cx0, cy1 - cy0, cz1 - cz0),
                              ((cx0 + cx1) / 2, (cy0 + cy1) / 2, (cz0 + cz1) / 2))])
    liner = "metal:#B9BAB8:0.45"
    e = 0.002                                     # the liner sits just inside the cut
    v = [(cx0 + e, cy1 - e, cz0 + e), (cx1 - e, cy1 - e, cz0 + e), (cx1 - e, cy1 - e, cz1 - e), (cx0 + e, cy1 - e, cz1 - e),
         (cx0 + e, yf + door_t + 0.001, cz0 + e), (cx1 - e, yf + door_t + 0.001, cz0 + e),
         (cx1 - e, yf + door_t + 0.001, cz1 - e), (cx0 + e, yf + door_t + 0.001, cz1 - e)]
    H.mesh_object(B, "cavity_recess", v, [(0, 1, 2, 3), (4, 5, 1, 0), (7, 3, 2, 6), (4, 0, 3, 7), (1, 5, 6, 2)],
                  liner, smooth_deg=0, closed=False)
    H.lathe_mesh(B, "turntable_plate", [(0.0, 0.0), (0.12, 0.0), (0.135, 0.008), (0.13, 0.01), (0.0, 0.006)],
                 "glass:#D8D8D8:0.14:alpha=0.9", center=((cx0 + cx1) / 2, (cy0 + cy1) / 2 - 0.01, cz0 + e + 0.012),
                 segments=48, cap=False)
    # ---- door: black frame with the window opening, glass sheet over it ---------
    dw = W - panel_w - 0.006
    dxc = -W / 2 + 0.003 + dw / 2
    dh = Ht - foot_h - 0.006
    dz = foot_h + 0.003 + dh / 2
    frame = B.box("door_frame", (dw, door_t - 0.004, dh), (dxc, yf + 0.004 + (door_t - 0.004) / 2, dz), None, bevel=0.003)
    frame.data.materials.append(black)
    wx0, wx1 = dxc - dw / 2 + 0.10 * dw, dxc - dw / 2 + 0.80 * dw
    wz0, wz1 = foot_h + 0.2 * dh, foot_h + 0.76 * dh
    H.cut(frame, [H.cutter_box("mw_window_cut", (wx1 - wx0, 0.1, wz1 - wz0), ((wx0 + wx1) / 2, yf + 0.01, (wz0 + wz1) / 2))])
    H.decal(B, "door_panel_screen", screen, wx1 - wx0, wz1 - wz0, ((wx0 + wx1) / 2, yf + 0.009, (wz0 + wz1) / 2), "-y")
    H.decal(B, "door_panel", glass, dw, dh, (dxc, yf + 0.0035, dz), "-y", thickness=0.0035, round_r=0.004)
    H.text(B, "door_panel_logo", "Galanz", 0.024, (dxc - dw / 2 + 0.17 * dw, yf - 0.0002, foot_h + 0.86 * dh),
           "paint:#F2F2F2:0.4", extrude=0.0002, bold_offset=0.0006)
    # ---- C-bow handle on the door's right edge ---------------------------------
    hx = dxc + dw / 2 - 0.03
    z0, z1 = foot_h + 0.05, Ht - 0.05
    pts = []
    for i in range(13):
        t = i / 12
        bulge = 0.010 + 0.026 * math.sin(math.pi * t) ** 0.7
        pts.append((hx, yf - bulge, z0 + (z1 - z0) * t))
    pts = [(hx, yf + 0.004, z0 - 0.004)] + pts + [(hx, yf + 0.004, z1 + 0.004)]
    hdl = H.tube(B, "door_handle", pts, 0.0075, black, samples=4)
    for vtx in hdl.data.vertices:                 # flat bar, ~3 cm wide, 1.5 cm deep
        vtx.co.x = hx + (vtx.co.x - hx) * 2.1
    # ---- control strip -----------------------------------------------------------
    pw = panel_w - 0.005
    pxc = W / 2 - 0.003 - pw / 2
    B.box("control_panel", (pw, 0.02, dh), (pxc, yf + 0.0065 + 0.01, dz), "plastic:#101010:0.14", bevel=0.002)
    H.decal(B, "control_panel_face", H.image_material("mw_panel", tex + "panel.png", rough=0.06, coat=0.8),
            pw - 0.004, dh - 0.004, (pxc, yf + 0.006, dz), "-y")
    ztop = dz + (dh - 0.004) / 2
    H.decal(B, "led_display", H.image_material("mw_led", tex + "led.png", rough=0.05, emit=4.0,
                                                key="screen:#200000:0.14:emit=#FF7CB2"),
            0.08, 0.027, (pxc, yf + 0.002, ztop - 0.137 * (dh - 0.004)), "-y")
    for k in range(12):                           # key legends, 2 columns x 6 rows
        col, row = k % 2, k // 2
        u = (130 if col == 0 else 360) / 500
        vv = (380 + row * 72) / 940
        kw, kh = 0.05, 0.012
        H.decal(B, f"touch_key_column_{k + 1}", H.image_material(f"mw_key_{k}", tex + "panel.png", rough=0.06,
                                                                  coat=0.8, key="plastic:#1C1C1C:0.38"),
                kw, kh, (pxc + (u - 0.5) * (pw - 0.004), yf + 0.0021, ztop - vv * (dh - 0.004)), "-y",
                uv_rect=(u - kw / (pw - 0.004) / 2, 1 - vv - kh / (dh - 0.004) / 2,
                         u + kw / (pw - 0.004) / 2, 1 - vv + kh / (dh - 0.004) / 2))
    # ---- vents, label -------------------------------------------------------------
    H.decal(B, "top_vent_grille", H.image_material("mw_top_vent", tex + "top_vent.png", rough=0.8, alpha=True),
            0.30, 0.28, (-0.11, yb - 0.17, Ht + 0.0004), "+z")
    H.decal(B, "side_vent_grille", H.image_material("mw_side_vent", tex + "side_vent.png", rough=0.8, alpha=True),
            0.13, 0.16, (-W / 2 - 0.0004, yf + door_t + 0.09, foot_h + 0.11), "-x")
    H.decal(B, "top_label_sticker", H.image_material("mw_label", tex + "top_label.png", rough=0.35,
                                                      key="plastic:#F1F1F1:0.38"),
            0.15, 0.065, (W / 2 - 0.095, yf + 0.065, Ht + 0.0006), "+z", thickness=0.0003)
    return B.objects_created()
