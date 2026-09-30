"""Spatially varying triangle sizes from the meshed boundary-layer columns.

Unlike a distance law measured from the wall, growth starts at the estimated
outer BL front. The tangential spacing comes from actual transfinite 1D nodes,
including curvature expansion at the TE/LE. This is an explicit optional
strategy; the final mesh and checkMesh remain the acceptance criteria.
"""
from __future__ import annotations
import numpy as np
from scipy.spatial import cKDTree


def front_samples(points: np.ndarray, thickness: float):
    p = np.asarray(points, dtype=float)
    if len(p) < 4 or thickness <= 0:
        raise ValueError("A closed wall contour and positive BL thickness are required")
    edges = np.roll(p, -1, axis=0)-p
    lengths = np.linalg.norm(edges, axis=1)
    if np.any(lengths <= 1e-12): raise ValueError("Zero-length wall element in front sizing")
    signed_area = .5*np.sum(p[:, 0]*np.roll(p[:, 1], -1)-p[:, 1]*np.roll(p[:, 0], -1))
    normals = np.column_stack((edges[:, 1], -edges[:, 0]))/lengths[:, None]
    if signed_area < 0: normals *= -1
    normals += np.roll(normals, 1, axis=0)
    normals /= np.linalg.norm(normals, axis=1)[:, None]
    front = p+thickness*normals
    front_edges = np.roll(front, -1, axis=0)-front
    return .5*(front+np.roll(front, -1, axis=0)), np.linalg.norm(front_edges, axis=1)


def install_front_size_callback(gmsh, curves: list[int], surface: int, layer: dict,
                                external: dict, chord: float, *, preserve_background: bool = False) -> dict:
    if external.get("interface_size_mode") != "sampled_front": return {"enabled": False}
    gmsh.model.mesh.generate(1)
    tags, xyz, _ = gmsh.model.mesh.getNodes()
    points = dict(zip(map(int, tags), np.asarray(xyz).reshape((-1, 3))[:, :2]))
    adjacency = {}
    for curve in curves:
        types, _, nodes = gmsh.model.mesh.getElements(1, int(curve))
        for kind, connectivity in zip(types, nodes):
            props = gmsh.model.mesh.getElementProperties(int(kind))
            for edge in np.asarray(connectivity).reshape((-1, props[3])):
                a, b = map(int, edge[:2])
                adjacency.setdefault(a, set()).add(b)
                adjacency.setdefault(b, set()).add(a)
    if any(len(neighbors) != 2 for neighbors in adjacency.values()):
        raise ValueError("The sampled BL front requires one closed conformal 1D boundary")
    start = next(iter(adjacency))
    order, previous, current = [start], None, start
    while True:
        nxt = next(n for n in adjacency[current] if n != previous)
        if nxt == start: break
        if nxt in order: raise ValueError("Disjoint or self-connected wall topology")
        order.append(nxt)
        previous, current = current, nxt
    if len(order) != len(adjacency): raise ValueError("More than one boundary loop in sampled front")
    thickness = float(layer["total_thickness_m"])
    samples, sizes = front_samples(np.array([points[n] for n in order]), thickness)
    factor = float(external.get("front_tangential_factor", .85))
    growth = float(external.get("radial_growth_rate", .11))
    far = chord*float(external["farfield_size_chord"])
    if min(factor, growth, far) <= 0: raise ValueError("Front sizing factors must be positive")
    sizes *= factor
    normal_factor = float(external.get("front_normal_size_cap_factor", 0.0))
    if normal_factor < 0: raise ValueError("Normal size cap factor must be nonnegative")
    if normal_factor:
        sizes = np.minimum(sizes, normal_factor*float(layer["last_layer_height_m"]))
    tree = cKDTree(samples)
    def callback(dim, tag, x, y, z, lc):
        if dim != 2 or tag != surface: return lc
        distance, index = tree.query([x, y], k=min(8, len(samples)))
        proposed = float(min(far, np.min(sizes[index]+growth*distance)))
        return min(float(lc), proposed) if preserve_background else proposed
    gmsh.model.mesh.setSizeCallback(callback)
    return {"enabled": True, "strategy": "sampled_front", "samples": len(samples),
            "front_thickness_chord": thickness/chord, "front_size_min_chord": float(sizes.min()/chord),
            "front_size_max_chord": float(sizes.max()/chord), "front_size_median_chord": float(np.median(sizes)/chord),
            "tangential_factor": factor, "radial_growth_rate": growth,
            "normal_size_cap_factor": normal_factor,
            "scope": "external_surface_only", "preserves_background_fields": preserve_background,
            "source": "actual 1D transfinite columns plus geometric normal offset",
            "limitation": "Estimated front before BL generation; does not split the shared BL edge or alter the internal surface."}
