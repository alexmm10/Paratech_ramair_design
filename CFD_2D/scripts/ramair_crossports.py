"""Explicit rib holes: one row per hole, shared by previews and CAD export.

Lengths are fractions of local chord; rotations are degrees in the rib plane.
Never resize or recenter a requested hole. Invalid geometry is reported.
"""
from __future__ import annotations
import math
import numpy as np

DEFAULT_HOLES = [dict(hole_id=f"CP{i+1}", shape="circle", x=x,
                     width_chord_frac=.025, height_chord_frac=.025,
                     reference_line="camber", z_offset_chord=0., angle_deg=0.)
                 for i, x in enumerate((.25, .45, .65))]


def explicit_hole_loop(hole: dict, upper: np.ndarray, lower: np.ndarray,
                       points: int = 48) -> tuple[np.ndarray, dict]:
    x = float(hole["x"])
    width = float(hole["width_chord_frac"])
    height = width if hole.get("shape") == "circle" else float(hole["height_chord_frac"])
    angle = math.radians(float(hole.get("angle_deg", 0.)))
    offset = float(hole.get("z_offset_chord", 0.))
    if hole.get("shape") not in {"circle", "ellipse"}:
        raise ValueError("La forma del crossport debe ser circle o ellipse.")
    if not all(math.isfinite(v) for v in (x, width, height, angle, offset)) or min(width, height) <= 0:
        raise ValueError("El crossport necesita dimensiones positivas y coordenadas finitas.")
    u = upper[np.argsort(upper[:, 0])]
    l = lower[np.argsort(lower[:, 0])]
    zu, zl = np.interp(x, u[:, 0], u[:, 1]), np.interp(x, l[:, 0], l[:, 1])
    reference = hole.get("reference_line", "camber")
    refs = {"camber": .5*(zu+zl), "chord": 0., "lower": zl, "upper": zu}
    if reference not in refs:
        raise ValueError(f"Línea de referencia desconocida: {reference}")
    z = float(refs[reference]+offset)
    t = np.linspace(0., 2.*np.pi, max(24, int(points)), endpoint=False)
    dx, dz = .5*width*np.cos(t), .5*height*np.sin(t)
    loop = np.column_stack((x+dx*np.cos(angle)-dz*np.sin(angle),
                            z+dx*np.sin(angle)+dz*np.cos(angle)))
    # Dense independent containment check catches rotated ellipses, LE/TE and
    # varying thickness; does not silently clamp to local midpoint thickness.
    check_t = np.linspace(0., 2.*np.pi, 720, endpoint=False)
    a, b = .5*width*np.cos(check_t), .5*height*np.sin(check_t)
    cx = x+a*np.cos(angle)-b*np.sin(angle)
    cz = z+a*np.sin(angle)+b*np.cos(angle)
    if (cx.min() <= max(u[:, 0].min(), l[:, 0].min()) or
        cx.max() >= min(u[:, 0].max(), l[:, 0].max()) or
        np.any(cz >= np.interp(cx, u[:, 0], u[:, 1])) or
        np.any(cz <= np.interp(cx, l[:, 0], l[:, 1]))):
        raise ValueError(f"Crossport {hole.get('hole_id', '')}: el contorno cruza el borde de la costilla; revisa su posición o dimensiones. No se han alterado.")
    return loop, dict(x=x, z=z, width=width, height=height,
                     z_fraction=(z-zl)/(zu-zl), angle_deg=math.degrees(angle))
