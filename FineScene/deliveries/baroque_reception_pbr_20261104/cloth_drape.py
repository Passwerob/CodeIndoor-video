"""Real Blender Cloth simulation for drapery, baked to an editable aDSL part.

Earlier I refused cloth simulation because "baking destroys editability".
That was the wrong conclusion. The right design is:

  * simulate with Blender's native CLOTH solver (top edge pinned, gravity,
    structural/shear/bending stiffness, air damping);
  * freeze the solved shape into an INDEPENDENT mesh object with its own
    datablock, material and aDSL custom properties (adsl_params records every
    simulation constant and the source object name);
  * keep the pinned source mesh hidden as a re-runnable control object.

So the drape is still one selectable, independently movable part. Editing
granularity changes from "drag a vertex" to "change a parameter and re-run
the deterministic simulation", which is exactly the aDSL contract.

Blender 5.2 note: ClothSettings.vertex_group_mass is the PIN vertex group
(its RNA description is "Vertex Group for pinning of vertices"), despite the
misleading identifier. pin_stiffness controls how strongly pinned vertices
hold their target.
"""
from __future__ import annotations

import json

import bpy


DEFAULT_SIM = {
    "engine": "BLENDER_CLOTH",
    "pin_vertex_group": "Pinned",
    "pin_stiffness": 1.0,
    "mass": 0.18,
    "gravity": [0.0, 0.0, -9.81],
    "air_damping": 2.2,
    "quality": 5,
    "tension_stiffness": 24.0,
    "compression_stiffness": 12.0,
    "shear_stiffness": 12.0,
    "bending_stiffness": 1.2,
    "tension_damping": 3.0,
    "shear_damping": 2.0,
    "bending_damping": 1.0,
    "use_self_collision": False,
    "use_object_collision": False,
    "solidify_thickness_m": 0.012,
}


class DrapeSimulator:
    """Collect single-layer cloth sources, then simulate and bake them all."""

    def __init__(self, builder, scene, materials, sim_config=None):
        self.builder = builder
        self.scene = scene
        self.materials = materials
        self.sim = dict(DEFAULT_SIM)
        self.sim.update(sim_config or {})
        self.sources = []

    def add(self, name, verts, faces, material_key, params, pinned_indices):
        """Create one cloth source. Kept OUTSIDE the OBJ_ collections so the
        part exporter and the integrity checker only see the baked result."""
        me = bpy.data.meshes.new(name + "_cloth_source_mesh")
        me.from_pydata(verts, [], faces)
        me.update()
        ob = bpy.data.objects.new(name + "_cloth_source", me)
        self.scene.collection.objects.link(ob)

        vg = ob.vertex_groups.new(name=self.sim["pin_vertex_group"])
        vg.add(list(pinned_indices), 1.0, "REPLACE")

        cloth = ob.modifiers.new("ClothSimulation", "CLOTH")
        s = cloth.settings
        s.vertex_group_mass = self.sim["pin_vertex_group"]
        s.pin_stiffness = self.sim["pin_stiffness"]
        s.mass = self.sim["mass"]
        s.gravity = tuple(self.sim["gravity"])
        s.air_damping = self.sim["air_damping"]
        s.quality = self.sim["quality"]
        s.tension_stiffness = self.sim["tension_stiffness"]
        s.compression_stiffness = self.sim["compression_stiffness"]
        s.shear_stiffness = self.sim["shear_stiffness"]
        s.bending_stiffness = self.sim["bending_stiffness"]
        s.tension_damping = self.sim["tension_damping"]
        s.shear_damping = self.sim["shear_damping"]
        s.bending_damping = self.sim["bending_damping"]
        cloth.collision_settings.use_self_collision = \
            self.sim["use_self_collision"]
        cloth.collision_settings.use_collision = \
            self.sim["use_object_collision"]

        solid = ob.modifiers.new("ClothThickness", "SOLIDIFY")
        solid.thickness = self.sim["solidify_thickness_m"]
        solid.offset = 0.0

        # Snapshot the owning object triad: B.begin() may be called again
        # before bake(), so we cannot rely on the Builder's current state.
        self.sources.append({
            "name": name, "source": ob, "material": material_key,
            "params": dict(params), "coll": self.builder.coll,
            "root": self.builder.root, "cur": self.builder.cur,
            "source_vertices": len(verts), "source_faces": len(faces),
            "pinned": len(pinned_indices),
        })
        return ob

    def bake(self, frames=30):
        """Advance the solver, then freeze each solved mesh into an aDSL part."""
        scene = self.scene
        scene.frame_start = 1
        scene.frame_end = max(2, int(frames))
        scene.frame_set(1)
        for f in range(2, int(frames) + 1):
            scene.frame_set(f)
        depsgraph = bpy.context.evaluated_depsgraph_get()

        baked = []
        for item in self.sources:
            ob = item["source"]
            ev = ob.evaluated_get(depsgraph)
            mesh = bpy.data.meshes.new_from_object(ev, depsgraph=depsgraph)
            mesh.name = item["name"] + "_cloth_baked_mesh"
            result = bpy.data.objects.new(item["name"], mesh)
            item["coll"].objects.link(result)
            result.parent = item["root"]
            if item["material"] in self.materials:
                mesh.materials.append(self.materials[item["material"]])

            record = dict(item["params"])
            record.update({
                "primitive": "cloth_simulated_drape",
                "simulation": {**self.sim, "frames": int(frames)},
                "source_object": ob.name,
                "source_vertices": item["source_vertices"],
                "source_faces": item["source_faces"],
                "pinned_vertices": item["pinned"],
                "editable": "re-run recipe with changed sim constants; "
                            "the baked part itself stays one independent object",
            })
            result["adsl_part"] = item["name"]
            result["adsl_type"] = "cloth_simulated_drape"
            result["adsl_params"] = json.dumps(record, ensure_ascii=False)
            result["adsl_material"] = item["material"]
            item["cur"]["parts"].append({
                "name": item["name"], "type": "cloth_simulated_drape",
                "params": record, "position": [0, 0, 0],
                "material": item["material"]})
            baked.append(item["name"])

            # Keep the source hidden but present: it is the re-runnable
            # control surface for this drape, not a rendered part.
            ob.hide_render = True
            ob.hide_viewport = True

        scene.frame_set(1)
        return baked


def simulation_summary(simulator, frames):
    return {
        "engine": "BLENDER_CLOTH",
        "drapes": len(simulator.sources),
        "frames": frames,
        "settings": simulator.sim,
        "note": "Baked into independent aDSL parts; hidden pinned sources "
                "remain for deterministic re-simulation.",
    }
