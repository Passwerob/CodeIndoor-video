"""Swing-lid trash bin, written by hand from the video (t = 0-1 s, 198-212 s).

Dark grey tapered tub with rounded corners, a black liner bag folded over its
rim; a lid made of a skirt band and a hood whose top rounds over from the back,
the sloped front of the hood carrying the PUSH swing flap (white 'PUSH' and an
open arrow); a white recycling roundel on the body front. The lid hinges at the
back, the flap at its top edge. Local frame: front = -y, origin bottom centre.
"""
import hand_lib as H


def build(B, mats, P):
    W, D, Ht = P["width"], P["depth"], P["height"]
    tex = P["tex_dir"] + "/"
    grey = H.material("bin_grey", "#2C2F33", rough=0.38, coat=0.15, key="plastic:#696D77:0.38")
    lid = H.material("bin_lid", "#2E3135", rough=0.32, coat=0.2, key="plastic:#4E5660:0.38")
    zt = 0.70 * Ht                                    # top of the tub
    # ---- tub: open shell (outer + inner wall), tapered -------------------------------
    B.box("base_foot", (W - 0.11, D - 0.11, 0.008), (0, 0, 0.004), "plastic:#1C1C1E:0.75")
    secs = [H.sec(0.006, W - 0.085, D - 0.085, 0.03), H.sec(0.02, W - 0.075, D - 0.075, 0.034),
            H.sec(zt, W - 0.038, D - 0.038, 0.040)]       # the lid skirt overhangs it ~17 mm (photos)
    H.loft(B, "body_shell", secs, grey, cap_top=False)
    inner = [H.sec(0.03, W - 0.09, D - 0.09, 0.028), H.sec(zt - 0.002, W - 0.046, D - 0.046, 0.036)]
    H.loft(B, "body_shell_inner", inner, "plastic:#2E3135:0.5", cap_top=False)
    # liner bag folded over the rim (black, a little slack)
    H.loft(B, "body_shell_liner", [H.sec(zt - 0.06, W - 0.034, D - 0.034, 0.041),
                                   H.sec(zt - 0.012, W - 0.033, D - 0.033, 0.042),
                                   H.sec(zt + 0.004, W - 0.036, D - 0.036, 0.040)],
           "plastic:#0E0E10:0.35", cap_top=False, cap_bottom=False)
    H.rounded_block(B, "body_rim", (W - 0.040, D - 0.040, 0.008), (0, 0, zt - 0.004), grey, r_plan=0.040,
                    r_edge=0.003)
    # recycling roundel on the (slightly forward-leaning) front
    zc = 0.40 * Ht
    yfront = -(D - 0.075 + (0.037) * (zc - 0.02) / (zt - 0.02)) / 2
    H.decal(B, "recycle_icon", H.image_material("bin_recycle", tex + "recycle.png", rough=0.4, alpha=True,
                                                 key="paint:#D8D8D8:0.75"),
            0.13, 0.13, (0, yfront - 0.0008, zc), "-y", tilt_deg=1.6)
    # ---- lid: skirt band + hood (hinged at the back) --------------------------------
    zs = zt + 0.004
    H.rounded_block(B, "lid_dome_skirt", (W, D, 0.075 * Ht), (0, 0, zs), lid, r_plan=0.045, r_edge=0.006,
                    r_edge_bot=0.004)
    zh = zs + 0.075 * Ht                              # hood base
    yb, yfh = D / 2 - 0.022, -D / 2 + 0.012
    def qb(a, c, b, n=8):
        return [((1 - t) ** 2 * a[0] + 2 * (1 - t) * t * c[0] + t * t * b[0],
                 (1 - t) ** 2 * a[1] + 2 * (1 - t) * t * c[1] + t * t * b[1]) for t in [i / n for i in range(n + 1)]]
    top = Ht - 0.001
    yslope_top, zslope_top = yfh + 0.04, top - 0.022
    prof = [(yfh, zh), (yb, zh), (yb, zh + 0.45 * (top - zh))]
    prof += qb((yb, zh + 0.45 * (top - zh)), (yb, top), (yb - 0.08, top))[1:]
    prof += qb((yfh + 0.10, top), (yslope_top - 0.005, top), (yslope_top, zslope_top))
    prof += [(yfh, zh + 0.012)]
    hood = H.profile_extrude(B, "lid_dome", prof, W - 0.045, lid, plane="yz")
    H.bevel(hood, 0.03, segments=5, angle_deg=30)
    # ---- swing flap on the sloped front --------------------------------------------
    import math
    dy, dz = yslope_top - yfh, zslope_top - (zh + 0.012)
    tilt = -math.degrees(math.atan2(dy, dz))
    L = math.hypot(dy, dz)
    nrm = (-dz / L, dy / L)                            # outward normal of the slope (y, z)
    cy, cz = (yfh + yslope_top) / 2 + nrm[0] * 0.0025, (zh + 0.012 + zslope_top) / 2 + nrm[1] * 0.0025
    # the flap swings INTO the bin: open the hood behind it (a pocket as deep as the flap is long)
    cut = H.cutter_box("bin_flap_opening", (0.62 * W - 0.006, 0.8 * L + 0.05, 0.8 * L - 0.006),
                       (0, cy - nrm[0] * (0.4 * L + 0.023), cz - nrm[1] * (0.4 * L + 0.023)))
    cut.rotation_euler[0] = math.radians(tilt)        # same lean as the flap
    H.cut(hood, [cut])
    H.decal(B, "push_flap", H.material("bin_flap", "#303337", rough=0.22, coat=0.4, key="plastic:#4E545E:0.14"),
            0.62 * W, 0.80 * L, (0, cy, cz), "-y", tilt_deg=tilt, thickness=0.004, round_r=0.012)
    H.decal(B, "push_label", H.image_material("bin_push", tex + "push.png", rough=0.4, alpha=True,
                                              key="paint:#E8E8E8:0.75"),
            0.58 * W, 0.62 * L, (0, cy + nrm[0] * 0.0012, cz + nrm[1] * 0.0012 + 0.004), "-y", tilt_deg=tilt)
    return B.objects_created()
