"""Shared unit-chord profile preparation, independent of CATIA and CFD cases."""
from __future__ import annotations

import copy
import shutil
from pathlib import Path

import numpy as np
import pandas as pd

from ramair_workspace import digest, file_digest, new_manifest, read_json, slug, write_json


def _branches(source: Path, topology: str | None = None) -> tuple[np.ndarray, np.ndarray, bool]:
    from ramair_profile_utils import read_and_canonicalize_profile_2d, read_profile_table
    if source.suffix.lower() == ".csv" and "source_section" in source.open(encoding="utf-8-sig").readline() and "x_m" in source.open(encoding="utf-8-sig").readline():
        frame = pd.read_csv(source)
        upper = frame[frame.source_section == "UPPER"].sort_values("source_order")[["x_m", "z_m"]].to_numpy(float)
        lower = frame[frame.source_section == "LOWER"].sort_values("source_order")[["x_m", "z_m"]].to_numpy(float)
        upper, lower = upper[np.argsort(upper[:, 0])], lower[np.argsort(lower[:, 0])]
        metadata = read_json(source.parent / "profile_manifest.json") or read_json(source.parent / "profile.json")
        opened = metadata.get("has_ram_air_opening_feature", metadata.get("topology") == "open" if metadata.get("topology") else np.linalg.norm(upper[0]-lower[0]) > .005)
        return upper, lower, bool(opened)
    declared = topology or read_json(source.parent / "profile.json").get("topology")
    if declared == "closed" and source.suffix.lower() == ".dat":
        from ramair_profile_utils import normalize_profile
        raw = normalize_profile(read_profile_table(source))
        xy = raw[["x_norm", "z_norm"]].to_numpy(float)
        leading = int(np.argmin(xy[:, 0]))
        if xy[0, 0] > .9 and xy[-1, 0] > .9 and 3 <= leading <= len(xy)-4:
            # A continuous XFOIL contour owns one actual nose point. Include it
            # in both branches rather than averaging neighbours from a half split.
            a, b = xy[:leading+1][::-1], xy[leading:]
            if np.interp(.4, a[:, 0], a[:, 1]) < np.interp(.4, b[:, 0], b[:, 1]):
                a, b = b, a
            return a, b, False
    cp = read_and_canonicalize_profile_2d(source, "library", has_inlet=(declared == "open") if declared else "auto", te_closure_mode="straight")
    if cp.errors:
        raise ValueError("; ".join(cp.errors))
    upper = cp.upper[["x_norm", "z_norm"]].to_numpy(float)
    lower = cp.lower[["x_norm", "z_norm"]].to_numpy(float)
    raw = read_profile_table(source)
    xcol = "x_norm" if "x_norm" in raw else "x"
    if cp.has_inlet and xcol in raw:
        xmin, xmax = float(raw[xcol].min()), float(raw[xcol].max())
        if 0 < xmin < .3 and .9 < xmax <= 1.000001:
            # Cut profiles retain the uncut profile's chord and LE origin.
            for branch in (upper, lower):
                branch[:, 0] = xmin + branch[:, 0]*(xmax-xmin)
                branch[:, 1] *= xmax-xmin
    return upper, lower, bool(cp.has_inlet)


def bounded_te(upper: np.ndarray, lower: np.ndarray, mode: str = "rounded", samples: int = 35) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict]:
    """Trim aft endpoints and apply a C1 cubic cap whose maximum x is exactly 1.

    Returns LE->TE upper/lower branches and a lower->upper cap. The upstream
    coordinates remain unchanged. Convex Bezier control hulls bound x <= 1.
    """
    upper, lower = np.array(upper, float, copy=True), np.array(lower, float, copy=True)
    if mode not in {"rounded", "straight", "sharp"}:
        raise ValueError("Modo TE desconocido")
    for branch in (upper, lower):
        if len(branch) < 8 or not np.isfinite(branch).all():
            raise ValueError("Se necesitan al menos ocho puntos finitos por superficie.")
    xmax = max(upper[:, 0].max(), lower[:, 0].max())
    if abs(xmax - 1) > 1e-6:
        for branch in (upper, lower):
            branch /= xmax
    if mode == "straight":
        upper[-1, 0] = lower[-1, 0] = 1.
        return upper, lower, np.vstack([lower[-1], upper[-1]]), {"mode": mode, "maximum_x_c": 1.}
    gap = abs(upper[-1, 1]-lower[-1, 1])
    trim = max(.001, min(.02, gap*.65))
    # Fixed point places anchors roughly one half local thickness upstream.
    for _ in range(20):
        local_gap = np.interp(1-trim, upper[:, 0], upper[:, 1])-np.interp(1-trim, lower[:, 0], lower[:, 1])
        trim = max(.001, min(.03, .5*(trim + .60*abs(local_gap))))
    cut = 1-trim

    def truncate(branch):
        z = np.interp(cut, branch[:, 0], branch[:, 1])
        values = branch[branch[:, 0] < cut-1e-10]
        return np.vstack([values, [cut, z]])

    upper, lower = truncate(upper), truncate(lower)
    tip = np.array([1., .5*(upper[-1, 1]+lower[-1, 1])])
    if mode == "sharp":
        return np.vstack([upper, tip]), np.vstack([lower, tip]), tip[None, :], {"mode": mode, "maximum_x_c": 1.}
    n = max(9, int(samples)//2*2+1)

    def bezier(p0, p1, p2, p3):
        t = np.linspace(0, 1, n//2+1)[:, None]
        return (1-t)**3*p0 + 3*(1-t)**2*t*p1 + 3*(1-t)*t*t*p2 + t**3*p3

    gap = upper[-1, 1]-lower[-1, 1]
    if gap <= 0:
        raise ValueError("Las superficies se cruzan en el borde de salida.")
    upper_slope = (upper[-1, 1]-upper[-2, 1])/(upper[-1, 0]-upper[-2, 0])
    lower_slope = (lower[-1, 1]-lower[-2, 1])/(lower[-1, 0]-lower[-2, 0])
    handle = min(trim*.65, gap*.3/max(abs(upper_slope), abs(lower_slope), 1e-9))
    a = bezier(lower[-1], lower[-1]+[handle, handle*lower_slope], tip-[0, gap*.28], tip)
    b = bezier(tip, tip+[0, gap*.28], upper[-1]+[handle, handle*upper_slope], upper[-1])
    cap = np.vstack([a, b[1:]])
    if cap[:, 0].max() > 1+1e-12 or np.any(np.linalg.norm(np.diff(cap, axis=0), axis=1) < 1e-12):
        raise ValueError("Cierre TE degenerado o fuera de la cuerda.")
    return upper, lower, cap, {"mode": "rounded", "method": "trimmed_C1_cubic_cap", "anchor_x_c": cut,
                              "maximum_x_c": 1., "samples": len(cap), "upstream_coordinates_preserved": True}


def profile_frame(upper: np.ndarray, lower: np.ndarray, cap: np.ndarray) -> pd.DataFrame:
    records = []
    for section, points in (("UPPER", upper[::-1]), ("LOWER", lower), ("TE_ROUNDED_CAP", cap[1:-1])):
        for index, (x, z) in enumerate(points):
            records.append({"point_id": len(records)+1, "x_norm": x, "z_norm": z, "x_m": x, "z_m": z,
                            "source_section": section, "source_order": index+1})
    return pd.DataFrame(records)


def process_profile(root: Path, source: Path, name: str, mode: str, base_profile: Path | None = None,
                    reference: bool = False, topology: str | None = None) -> Path:
    if topology not in {None, "open", "closed"}:
        raise ValueError("Topología desconocida")
    upper, lower, opened = _branches(source, topology)
    if not opened:
        xmin = min(upper[:, 0].min(), lower[:, 0].min())
        xmax = max(upper[:, 0].max(), lower[:, 0].max())
        for branch in (upper, lower):
            branch[:, 0] = (branch[:, 0]-xmin)/(xmax-xmin)
            branch[:, 1] /= xmax-xmin
        # Explicit common LE eliminates duplicate/zero-length connecting edges.
        leading = .5*(upper[0]+lower[0])
        leading[0] = 0.
        upper[0] = lower[0] = leading
    shared_cap = None
    if opened and base_profile:
        from ramair_2d_open_experimental_mesh import _recover_open_profile_base_coordinates
        base_file = base_profile / "profile_points.csv" if base_profile.is_dir() else base_profile
        base_upper, base_lower, _ = _branches(base_file)
        original = next(base_profile.glob("original.*"), base_file) if base_profile.is_dir() else base_file
        raw_upper, raw_lower, _ = _branches(original)
        scale, alignment = _recover_open_profile_base_coordinates(upper[::-1], lower, raw_upper[::-1], raw_lower, 1., 1e-6)
        xmin = min(raw_upper[:, 0].min(), raw_lower[:, 0].min())
        extent = max(raw_upper[:, 0].max(), raw_lower[:, 0].max())-xmin
        def retained(base, cut_branch):
            x = (1.+scale*(cut_branch[0, 0]-1.)-xmin)/extent
            z = np.interp(x, base[:, 0], base[:, 1])
            return np.vstack([[x, z], base[base[:, 0] > x+1e-12]])
        upper, lower = retained(base_upper, upper), retained(base_lower, lower)
        base_info = read_json(base_profile / "profile.json") if base_profile.is_dir() else {}
        if base_info.get("te", {}).get("mode") == mode:
            base_frame = pd.read_csv(base_file)
            cap_points = base_frame[base_frame.source_section == "TE_ROUNDED_CAP"].sort_values("source_order")[["x_m", "z_m"]].to_numpy(float)
            shared_cap = np.vstack([lower[-1], cap_points, upper[-1]])
    if shared_cap is None:
        upper, lower, cap, report = bounded_te(upper, lower, mode)
    else:
        cap, report = shared_cap, dict(base_info["te"], shared_with_base=True, alignment=alignment)
    suffix = {"rounded": "roundedTE", "straight": "straightTE", "sharp": "sharp"}[mode]
    target = root / "Airfoil Profiles" / ("Reference" if reference else "Processed") / f"{slug(name)}_{suffix}"
    target.mkdir(parents=True, exist_ok=False)
    shutil.copy2(source, target / ("original"+source.suffix))
    frame = profile_frame(upper, lower, cap)
    frame.to_csv(target / "profile_points.csv", index=False, float_format="%.14g")
    # XFOIL order: max-x TE along upper towards LE then lower back to TE.
    mid = int(np.argmax(cap[:, 0]))
    contour = np.vstack([cap[mid:], upper[-2::-1], lower[1:], cap[1:mid+1]]) if mode == "rounded" else np.vstack([upper[::-1], lower[1:]])
    np.savetxt(target / f"{target.name}.dat", contour, header=target.name, comments="", fmt="%.14g")
    manifest = new_manifest("profile", target.name, topology="open" if opened else "closed", chord_m=1.,
                            source=str(source), source_sha256=file_digest(source), te=report,
                            base_profile=str(base_profile.resolve().relative_to(root.resolve())) if base_profile and base_profile.resolve().is_relative_to(root.resolve()) else (str(base_profile.resolve()) if base_profile else None),
                            coordinates="upper_TE_to_LE__lower_LE_to_TE__cap_lower_to_upper")
    manifest["revision"] = file_digest(target / "profile_points.csv")
    write_json(target / "profile.json", manifest)
    from ramair_workspace_storage import publish_shared
    publish_shared(root, target)
    return target


def catalogue(root: Path) -> list[dict]:
    items = []
    for path in sorted((root / "Airfoil Profiles").glob("**/profile.json")):
        info = read_json(path)
        items.append(dict(info, path=str(path.parent), file=str(path.parent / "profile_points.csv")))
    return items


def import_geometry(cfd_case: Path, profile_dir: Path, name: str, base_dir: Path | None = None) -> Path:
    profile = read_json(profile_dir / "profile.json")
    if not profile:
        raise ValueError("Selecciona un perfil procesado de la biblioteca.")
    if profile["topology"] == "open":
        base_dir = base_dir or (Path(profile["base_profile"]) if profile.get("base_profile") else None)
        if base_dir and not base_dir.is_absolute():
            library = next(p for p in profile_dir.parents if p.name == "Airfoil Profiles")
            base_dir = library.parent / base_dir
        if not base_dir or read_json(base_dir / "profile.json").get("topology") != "closed":
            raise ValueError("El perfil abierto necesita su perfil base sin cortar para la interfase.")
    target = cfd_case / "Geometries" / slug(name)
    target.mkdir(parents=True, exist_ok=False)
    (target / "Inputs/profile").mkdir(parents=True)
    shutil.copy2(profile_dir / "profile_points.csv", target / "Inputs/profile/profile_points.csv")
    if base_dir:
        (target / "Inputs/base").mkdir()
        shutil.copy2(base_dir / "profile_points.csv", target / "Inputs/base/profile_points.csv")
    revision = digest({"profile": profile["revision"], "base": read_json(base_dir / "profile.json").get("revision") if base_dir else None})
    write_json(target / "geometry.json", new_manifest("geometry", name, topology=profile["topology"],
               profile_id=profile["id"], revision=revision, source=str(profile_dir), chord_m=1., te=profile["te"]))
    (target / "Meshes").mkdir()
    return target
