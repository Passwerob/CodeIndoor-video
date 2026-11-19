"""Single-lever gooseneck mixer tap, written by hand from the video (t = 115-122 s, 150-158 s).

Polished chrome: round escutcheon on the worktop, a short column with the
lever on its left side, a tall gooseneck rising from the column top, arcing
over and dropping to an outlet ~0.19 m in front of it over the bowl.
Local frame: front = -y (towards the bowl), origin bottom centre.
"""
import math

import hand_lib as H


def build(B, mats, P):
    chrome = H.material("tap_chrome", "#D8DADC", rough=0.06, metallic=1.0, key="metal:#C6CACE:0.04")
    H.lathe_mesh(B, "base_escutcheon", [(0.0, 0.0), (0.029, 0.0), (0.029, 0.004), (0.024, 0.009), (0.0, 0.01)], chrome,
                 segments=40)
    H.lathe_mesh(B, "body_column", [(0.0, 0.009), (0.0215, 0.009), (0.0205, 0.10), (0.019, 0.125), (0.0, 0.126)], chrome,
                 segments=40)
    H.lathe_mesh(B, "collar_ring", [(0.0, 0.124), (0.0205, 0.124), (0.021, 0.132), (0.0, 0.133)], chrome, segments=40)
    # gooseneck: up from the collar, a tight arc over, down to the outlet
    pts = [(0, 0.0, 0.13), (0, 0.0, 0.22), (0, -0.005, 0.27), (0, -0.03, 0.305), (0, -0.07, 0.322), (0, -0.115, 0.315),
           (0, -0.15, 0.29), (0, -0.175, 0.255), (0, -0.188, 0.215)]
    H.tube(B, "gooseneck_spout", pts, 0.0115, chrome, samples=8)
    H.lathe_mesh(B, "spout_outlet", [(0.0, 0.0), (0.012, 0.0), (0.0125, 0.025), (0.0, 0.026)], chrome,
                 center=(0, -0.19, 0.192), segments=24)
    # lever on the column's left side, pointing forward and slightly down
    H.tube(B, "lever_stem", [(-0.018, 0.0, 0.085), (-0.032, 0.0, 0.088)], 0.008, chrome)
    H.tube(B, "lever_handle", [(-0.03, 0.0, 0.088), (-0.036, -0.025, 0.08), (-0.038, -0.06, 0.068),
                               (-0.039, -0.085, 0.06)], 0.0065, chrome, samples=4)
    return B.objects_created()
