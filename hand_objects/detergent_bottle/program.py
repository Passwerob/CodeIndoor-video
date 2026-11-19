"""白猫 ("White Cat" brand) dish-soap jug (~2 kg), written by hand from the video (t = 115-160 s).

Yellow rectangular jug with rounded edges and shoulders, a carry handle at
the back of the top, a white collar and an orange-red pump; the front label
(cat logo, 白猫 brand name, lemons, a plate) is cut out of the footage.
Local frame: front = -y, origin bottom centre. Built at its real size.
"""
import hand_lib as H


def build(B, mats, P):
    yellow = H.material("jug_yellow", "#F2D42A", rough=0.3, coat=0.3, key="plastic:#F2D42A:0.3")
    w, d, h = 0.13, 0.095, 0.245
    H.loft(B, "bottle_body", [H.sec(0.0, w - 0.01, d - 0.01, 0.018), H.sec(0.006, w, d, 0.022),
                               H.sec(h * 0.82, w, d, 0.022), H.sec(h * 0.93, w - 0.02, d - 0.012, 0.02),
                               H.sec(h, w - 0.05, d - 0.04, 0.016)], yellow)
    # carry handle: an arch from the shoulder to the back of the top
    H.tube(B, "carry_handle", [(0.02, 0.02, h - 0.01), (0.035, 0.03, h + 0.035), (0.05, 0.03, h + 0.04),
                                (0.06, 0.02, h * 0.9)], 0.009, yellow, samples=6)
    neck_y = -0.012
    # a ring the pump stem slides down into when pressed
    H.lathe_mesh(B, "pump_collar", [(0.0085, h - 0.003), (0.017, h - 0.003), (0.017, h + 0.027), (0.0085, h + 0.027),
                                    (0.0085, h - 0.003)], "plastic:#E4492C:0.35", center=(-0.02, neck_y, 0.0),
                 segments=32, cap=False)
    B.cylinder("pump_stem", 0.0065, 0.028, (-0.02, neck_y, h + 0.04), "plastic:#F4F4F2:0.3", verts=16)
    B.box("pump_head", (0.024, 0.06, 0.016), (-0.02, neck_y - 0.016, h + 0.06), "plastic:#E4492C:0.35", bevel=0.004)
    B.cylinder("pump_head_spout", 0.004, 0.03, (-0.02, neck_y - 0.05, h + 0.055), "plastic:#E4492C:0.35", verts=12,
               rot=(1.5708, 0, 0))
    H.decal(B, "front_label", H.image_material("jug_label", P["tex_dir"] + "/label.png", rough=0.35, coat=0.3,
                                                key="plastic:#F2D42A:0.35"),
            w - 0.012, 0.15, (0, -d / 2 - 0.0005, 0.04 + 0.075), "-y")
    return B.objects_created()
