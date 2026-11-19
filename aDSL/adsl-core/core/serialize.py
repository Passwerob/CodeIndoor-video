from __future__ import annotations

from typing import Any


def asset_to_dict(asset: Any) -> dict[str, Any]:
    """Serialize an Asset tree to the JSON intermediate representation.

    ``component_id`` is additive: readers that predate it simply see one extra
    key, so existing manifests and snapshots stay loadable.
    """
    return {
        "label": asset.label,
        "component_id": getattr(asset, "component_id", None),
        "primitives": asset._primitives,
        "children": {key: child.to_dict() for key, child in asset._children.items()},
        "joints": {
            key: {
                "spec": asset._joints[key].to_dict(),
                "child": asset._joint_children[key].to_dict(),
            }
            for key in asset._joint_children.keys()
        },
    }


def asset_from_dict(data: dict[str, Any]) -> Any:
    """Restore an Asset tree from the JSON intermediate representation.

    This is the core-library counterpart of the old
    ``scripts/blender_worker.asset_from_json`` (which only worked inside the
    Blender worker). It preserves stable component ids and does **not** rebase
    joint geometry, because serialized geometry is already in link space.

    aDSL adapts to SceneCode here: when a child has no explicit id, its part
    key is used verbatim, matching SceneCode's ``PartPlan.name`` /
    ``code/<part>.py`` naming contract.
    """
    from .asset import Asset
    from .joints import Joint

    label = data.get("label", "Asset")
    asset = Asset(label=label)

    component_id = data.get("component_id")
    if component_id is None:
        # Legacy JSON: derive a deterministic id from the label so a round-trip
        # never produces a nameless component.
        component_id = label
    asset.component_id = component_id

    for primitive in data.get("primitives", []):
        record = dict(primitive)
        record.setdefault("color", (0.8, 0.8, 0.8))
        record.setdefault("alpha", None)
        asset.add_primitive(record)

    for name, child_data in data.get("children", {}).items():
        restored = asset_from_dict(child_data)
        if child_data.get("component_id") is None:
            restored.component_id = name
        asset.attach_part(name, restored)

    for name, entry in data.get("joints", {}).items():
        spec = dict(entry["spec"])
        spec["name"] = name
        joint = Joint(**spec)
        child = asset_from_dict(entry["child"])
        # attach_joint() would rebase this already-rebased geometry a second time.
        asset._joints[name] = joint
        asset._joint_children[name] = child
        object.__setattr__(child, "_joint_parent", asset)
        object.__setattr__(child, "_parent_joint", joint)
        object.__setattr__(asset, name, child)

    return asset
