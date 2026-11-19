"""Upright dustpan + brush set, written by hand from the video (t = 198-212 s).

A translucent teal scoop (flat floor, side walls rising to the back, a hood at
the back with a black comb), a stainless shaft rising from the back to a teal
grip with a hook; the brush stands clipped beside it: stainless shaft, teal grip
with a flag-shaped hook, and a long teal head with black bristles lying
diagonally in the scoop. Local frame: front = -y (the open lip), origin bottom centre.
"""
import math

import hand_lib as H


def build(B, mats, P):
    W, D, Ht = P["width"], P["depth"], P["height"]
    teal = H.material("dp_teal", "#5E9E96", rough=0.22, transmission=0.35, coat=0.3, key="plastic:#4E8F86:0.14")
    teal_solid = H.material("dp_teal_solid", "#4E9A90", rough=0.3, key="plastic:#4E8F86:0.75")
    steel = "metal:#B8BCC0:0.25:brushed"
    yf, yb = -D / 2, D / 2
    back = yb - 0.035                                  # the pan's back wall / hood line
    # ---- scoop ------------------------------------------------------------------------
    fw, bw = W - 0.004, W - 0.03
    H.mesh_object(B, "pan_body", [(-fw / 2, yf + 0.004, 0.0), (fw / 2, yf + 0.004, 0.0), (bw / 2, back, 0.006),
                                  (-bw / 2, back, 0.006), (-fw / 2, yf + 0.004, 0.004), (fw / 2, yf + 0.004, 0.004),
                                  (bw / 2, back, 0.010), (-bw / 2, back, 0.010)],
                  [(0, 1, 2, 3), (4, 7, 6, 5), (0, 4, 5, 1), (1, 5, 6, 2), (2, 6, 7, 3), (3, 7, 4, 0)], teal, smooth_deg=0)
    for sx in (-1, 1):                                 # side walls rising to the back
        prof = [(yf + 0.03, 0.004), (back, 0.006), (back, 0.11), (yf + 0.03, 0.012)]
        x = sx * ((fw + bw) / 4 - 0.002)
        H.profile_extrude(B, f"pan_body_side_{'l' if sx < 0 else 'r'}", prof, 0.004, teal, plane="yz", offset=x,
                          smooth_deg=0)
    H.profile_extrude(B, "pan_body_hood", [(back - 0.01, 0.006), (back + 0.02, 0.006), (back + 0.02, 0.13),
                                           (back - 0.07, 0.12), (back - 0.06, 0.105)], bw, teal, plane="yz",
                      smooth_deg=0)
    B.box("pan_front_lip", (fw, 0.012, 0.003), (0, yf + 0.006, 0.0015), "plastic:#3A877C:0.2")
    for k in range(16):                                # comb along the hood's front edge
        x = -bw / 2 + 0.02 + k * (bw - 0.04) / 15
        B.box(f"comb_teeth_{k}", (0.004, 0.006, 0.03), (x, back - 0.066, 0.095), "plastic:#16201E:0.6")
    # ---- dustpan shaft + grip -----------------------------------------------------------
    sx0 = -0.03
    H.tube(B, "pan_handle_shaft", [(sx0, back + 0.005, 0.12), (sx0, yb - 0.012, Ht - 0.10)], 0.0105, steel)
    H.tube(B, "pan_grip", [(sx0, yb - 0.012, Ht - 0.12), (sx0, yb - 0.010, Ht - 0.003)], 0.0135, teal_solid)
    H.tube(B, "pan_grip_hook", [(sx0, yb - 0.012, Ht - 0.015), (sx0, yb - 0.012 - 0.022, Ht - 0.03)], 0.004, teal_solid)
    B.box("brush_rest_clip", (0.06, 0.03, 0.025), (sx0 + 0.03, yb - 0.02, 0.30), None, bevel=0.004
          ).data.materials.append(teal_solid)
    # ---- brush: shaft beside the pan's, head lying diagonally in the scoop ----------------
    bx = sx0 + 0.06
    top_b = Ht - 0.17
    sock = (bx, back - 0.10, 0.07)                   # the head's shaft socket, in front of the hood
    H.tube(B, "brush_handle_shaft", [sock, (bx, yb - 0.02, top_b)], 0.0095, steel)
    H.tube(B, "brush_grip", [(bx, yb - 0.02, top_b - 0.09), (bx, yb - 0.019, top_b)], 0.012, teal_solid)
    H.profile_extrude(B, "brush_grip_flag", [(yb - 0.02, top_b - 0.01), (yb - 0.075, top_b + 0.03),
                                             (yb - 0.085, top_b + 0.015), (yb - 0.03, top_b - 0.03)],
                      0.012, teal_solid, plane="yz", offset=bx + 0.004, smooth_deg=0)
    # head: 0.15 m long, lying flat on the scoop floor, diagonal from the socket
    # forward-left (under the comb, which starts 80 mm up)
    ang = math.radians(50)
    hl = 0.15
    hc = (sock[0] - hl / 2 * math.sin(ang), sock[1] + 0.012 - hl / 2 * math.cos(ang))
    head = B.box("brush_head", (0.034, hl, 0.03), (hc[0], hc[1], 0.058), None, bevel=0.008)
    head.data.materials.append(teal_solid)
    head.rotation_euler = (0.0, 0.0, -ang)
    br = B.box("brush_bristles", (0.028, hl - 0.012, 0.034), (hc[0], hc[1], 0.027), "fabric:#141414:0.85")
    br.rotation_euler = (0.0, 0.0, -ang)
    return B.objects_created()
