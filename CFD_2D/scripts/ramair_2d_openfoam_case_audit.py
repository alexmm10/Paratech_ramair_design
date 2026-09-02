#!/usr/bin/env python3
"""Audit the generated 2-D SA airfoil case contract without changing a case."""
from __future__ import annotations

import argparse
import csv
import gzip
import json
import math
import re
from pathlib import Path
from typing import Any

from ramair_2d_openfoam_case_writer import freestream_dirs, parse_boundary_patches, patch_role


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def text(path: Path) -> str:
    candidates = (path, Path(str(path) + ".gz"))
    for candidate in candidates:
        try:
            if candidate.suffix == ".gz":
                with gzip.open(candidate, "rt", encoding="utf-8", errors="ignore") as handle:
                    return handle.read()
            return candidate.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
    return ""


def initial_field(case: Path, name: str) -> str:
    """Prefer the immutable pre-SIMPLE field over a transferred transient field."""
    histories = sorted((case / "steadyInitialization/history").glob("run_*/initial_zero"))
    for directory in reversed(histories):
        source = text(directory / name)
        if source:
            return source
    return text(case / "0" / name)


def latest_checkmesh_log(case: Path) -> str:
    candidates = [case / "log.checkMesh", case / "log.checkMesh.preRun"]
    candidates.extend((case / "steadyInitialization/history").glob("run_*/log.checkMesh.preRun"))
    existing = [path for path in candidates if path.is_file()]
    if not existing:
        return ""
    return text(max(existing, key=lambda path: path.stat().st_mtime))


def scalar(source: str, key: str) -> float | None:
    match = re.search(rf"^\s*{re.escape(key)}\s+([-+0-9.eE]+)\s*;", source, re.MULTILINE)
    try:
        return float(match.group(1)) if match else None
    except ValueError:
        return None


def uniform_scalar(source: str, key: str) -> float | None:
    match = re.search(
        rf"\b{re.escape(key)}\s+uniform\s+([-+0-9.eE]+)\s*;",
        source,
    )
    try:
        return float(match.group(1)) if match else None
    except ValueError:
        return None


def vector(source: str, key: str) -> tuple[float, float, float] | None:
    match = re.search(
        rf"^\s*{re.escape(key)}\s+\(\s*([-+0-9.eE]+)\s+([-+0-9.eE]+)\s+([-+0-9.eE]+)\s*\)\s*;",
        source,
        re.MULTILINE,
    )
    try:
        return tuple(float(match.group(index)) for index in range(1, 4)) if match else None
    except ValueError:
        return None


def uniform_vector(source: str, key: str) -> tuple[float, float, float] | None:
    match = re.search(
        rf"^\s*{re.escape(key)}\s+uniform\s+\(\s*([-+0-9.eE]+)\s+([-+0-9.eE]+)\s+([-+0-9.eE]+)\s*\)\s*;",
        source,
        re.MULTILINE,
    )
    try:
        return tuple(float(match.group(index)) for index in range(1, 4)) if match else None
    except ValueError:
        return None


def vectors_close(left: tuple[float, float, float] | None, right: tuple[float, float, float]) -> bool:
    return left is not None and all(math.isclose(a, b, abs_tol=1.0e-6) for a, b in zip(left, right))


def patch_block(source: str, patch: str) -> str:
    match = re.search(rf"\b{re.escape(patch)}\s*\{{([^}}]*)\}}", source, re.DOTALL)
    return match.group(1) if match else ""


def second_order_final_schemes(source: str) -> bool:
    velocity = re.search(r"div\(phi,U\)\s+([^;]+);", source)
    turbulence = re.search(r"div\(phi,nuTilda\)\s+([^;]+);", source)
    if not velocity or not turbulence or "backward" not in source:
        return False
    velocity_scheme = velocity.group(1)
    turbulence_scheme = turbulence.group(1)
    velocity_ok = "linearUpwind" in velocity_scheme or "limitedLinearV" in velocity_scheme
    turbulence_ok = "linearUpwind" in turbulence_scheme or "limitedLinear" in turbulence_scheme
    return velocity_ok and turbulence_ok


def row(requirement: str, current: Any, expected: Any, ok: bool, change: str = "none") -> dict[str, Any]:
    return {
        "requirement": requirement,
        "current": current,
        "expected": expected,
        "status": "PASS" if ok else "ACTION_REQUIRED",
        "change_applied": change,
    }


def latest_yplus(project_root: Path, case: Path) -> float | None:
    variant = case.parent.name
    result = project_root / "CFD_2D/results" / variant / case.name / "wall_yplus_vs_xc.csv"
    if not result.is_file():
        return None
    values: list[float] = []
    with result.open(encoding="utf-8-sig", newline="") as handle:
        for item in csv.DictReader(handle):
            for key, value in item.items():
                if "yplus" in str(key).lower():
                    try:
                        values.append(float(value))
                    except (TypeError, ValueError):
                        pass
    return max(values) if values else None


def audit_case(project_root: Path, case: Path) -> dict[str, Any]:
    cfg = read_json(case / "case_config.json")
    rho = float(cfg.get("rho") or 0.0)
    mu = float(cfg.get("mu") or 0.0)
    chord = float(cfg.get("chord_m") or 0.0)
    velocity = float(cfg.get("velocity_m_s") or 0.0)
    temperature = float(cfg.get("temperature_K") or 0.0)
    reynolds = float(cfg.get("reynolds") or 0.0)
    mach = float(cfg.get("mach_input") or 0.0)
    nu = mu / rho if rho > 0.0 else 0.0
    computed_re = velocity * chord / nu if nu > 0.0 else 0.0
    speed_of_sound = math.sqrt(1.4 * 287.05 * temperature) if temperature > 0.0 else 0.0
    computed_mach = velocity / speed_of_sound if speed_of_sound > 0.0 else 0.0
    alpha = float(cfg.get("alpha_deg") or 0.0)
    expected_drag, expected_lift = freestream_dirs(alpha)
    fields = {name: initial_field(case, name) for name in ("U", "p", "nuTilda", "nut")}
    control = text(case / "system/controlDict")
    schemes = text(case / "system/transientTemplates/fvSchemes") or text(case / "system/fvSchemes")
    momentum = text(case / "constant/momentumTransport")
    patches = parse_boundary_patches(case / "constant/polyMesh")
    walls = [p["name"] for p in patches if patch_role(p["name"], p["type"]) == "wall"]
    farfields = [p["name"] for p in patches if patch_role(p["name"], p["type"]) == "farfield"]
    empties = [p["name"] for p in patches if patch_role(p["name"], p["type"]) == "empty"]
    patch_names = {p["name"].lower() for p in patches}
    opening_patches = sorted(name for name in patch_names if "opening" in name or "inlet_gap" in name)
    initial_nutilda = uniform_scalar(fields["nuTilda"], "internalField")
    check_log = latest_checkmesh_log(case)
    yplus_max = latest_yplus(project_root, case)
    expected_u = tuple(velocity * component for component in expected_drag)
    initial_u = uniform_vector(fields["U"], "internalField")
    initial_p = uniform_scalar(fields["p"], "internalField")
    bbox_match = re.search(
        r"Overall domain bounding box\s+\(([^)]+)\)\s+\(([^)]+)\)",
        check_log,
    )
    domain = None
    if bbox_match:
        try:
            lower = tuple(float(value) for value in bbox_match.group(1).split())
            upper = tuple(float(value) for value in bbox_match.group(2).split())
            domain = {
                "center_xy_c": [0.5 * (lower[0] + upper[0]) / chord, 0.5 * (lower[1] + upper[1]) / chord],
                "radius_x_c": 0.5 * (upper[0] - lower[0]) / chord,
                "radius_y_c": 0.5 * (upper[1] - lower[1]) / chord,
            }
        except (ValueError, IndexError, ZeroDivisionError):
            domain = None
    domain_ok = bool(domain) and all(
        math.isclose(float(domain[key]), expected, abs_tol=2.0e-3)
        for key, expected in (("radius_x_c", 50.0), ("radius_y_c", 50.0))
    ) and math.isclose(float(domain["center_xy_c"][0]), 0.5, abs_tol=2.0e-3) \
        and math.isclose(float(domain["center_xy_c"][1]), 0.0, abs_tol=2.0e-3)
    farfield_turbulence_ok = bool(farfields) and all(
        "type freestream" in patch_block(fields["nuTilda"], patch)
        and math.isclose(
            uniform_scalar(patch_block(fields["nuTilda"], patch), "freestreamValue") or -1.0,
            4.0 * nu,
            rel_tol=2.0e-6,
        )
        and (
            "type freestream" in patch_block(fields["nut"], patch)
            or "type calculated" in patch_block(fields["nut"], patch)
        )
        for patch in farfields
    )
    staged_runner = text(project_root / "CFD_2D/scripts/ramair_2d_openfoam_staged_runner.py")
    transfer_contract_ok = all(
        token in staged_runner
        for token in (
            "steady_transfer_plan", "missing_required", "field_continuity",
            "audit_steady_to_transient_continuity", "transfer_to_transient_zero=True",
        )
    )
    wall_bc_ok = bool(walls) and all(
        "type noSlip" in patch_block(fields["U"], wall)
        and "type zeroGradient" in patch_block(fields["p"], wall)
        and "type fixedValue" in patch_block(fields["nuTilda"], wall)
        and "value uniform 0" in patch_block(fields["nuTilda"], wall)
        and "type fixedValue" in patch_block(fields["nut"], wall)
        and "value uniform 0" in patch_block(fields["nut"], wall)
        for wall in walls
    )
    empty_bc_ok = bool(empties) and all(
        all("type empty" in patch_block(fields[field], patch) for field in fields)
        for patch in empties
    )
    actual_drag = vector(control, "dragDir")
    actual_lift = vector(control, "liftDir")
    rows = [
        row("chord", chord, 1.0, math.isclose(chord, 1.0, rel_tol=0.0, abs_tol=1e-12)),
        row("Re=U*c/nu", computed_re, reynolds, math.isclose(computed_re, reynolds, rel_tol=2e-3)),
        row("Mach=U/a(T)", computed_mach, mach, math.isclose(computed_mach, mach, rel_tol=2e-3)),
        row("validation thermophysical state", {"T_K": temperature, "rho": rho, "mu": mu, "U": velocity},
            {"T_K": 288.15, "rho": 0.66606662, "mu": 1.7894e-5, "U": 51.04384},
            all((math.isclose(temperature, 288.15, rel_tol=2e-4), math.isclose(rho, 0.66606662, rel_tol=2e-4),
                 math.isclose(mu, 1.7894e-5, rel_tol=2e-4), math.isclose(velocity, 51.04384, rel_tol=2e-4)))),
        row("circular domain radius", domain, {"center_xy_c": [0.5, 0.0], "radius_c": 50.0}, domain_ok),
        row("SA model", "SpalartAllmaras" if "SpalartAllmaras" in momentum else momentum.strip()[:80],
            "SpalartAllmaras", "SpalartAllmaras" in momentum),
        row("circular farfield U/p", farfields,
            "freestreamVelocity + freestreamPressure",
            bool(farfields) and "freestreamVelocity" in fields["U"] and "freestreamPressure" in fields["p"]),
        row("farfield SA turbulence", farfields, "nuTilda freestream=4nu; nut freestream/calculated",
            farfield_turbulence_ok),
        row("nuTilda freestream and initial value", initial_nutilda, 4.0 * nu,
            initial_nutilda is not None and math.isclose(initial_nutilda, 4.0 * nu, rel_tol=2e-6)
            and "type freestream;" in fields["nuTilda"],
            "base generator changed from 3nu to 4nu; regenerate legacy cases"),
        row("fixed geometry and velocity AoA", {"alpha_deg": alpha, "U_internal": initial_u},
            {"geometry_rotation_deg": 0.0, "U_internal": expected_u}, vectors_close(initial_u, expected_u)),
        row("whole-domain RANS initialization", {"U": initial_u, "p": initial_p, "nuTilda": initial_nutilda},
            {"U": expected_u, "p": 0.0, "nuTilda": 4.0 * nu},
            vectors_close(initial_u, expected_u)
            and math.isclose(initial_p or 0.0, 0.0, abs_tol=1.0e-14)
            and initial_nutilda is not None and math.isclose(initial_nutilda, 4.0 * nu, rel_tol=2e-6)),
        row("low-Re SA wall BC", walls, "U noSlip; p zeroGradient; nuTilda=0; nut=0; no wall functions",
            wall_bc_ok and "WallFunction" not in fields["nut"],
            "base generator now writes fixedValue nut=0; regenerate legacy cases"),
        row("strict 2-D patches", empties, "front/back empty in all fields", empty_bc_ok),
        row("open inlet continuity", opening_patches, "no physical opening patch", not opening_patches),
        row("force patches", re.search(r"patches\s*\(([^)]*)\)", control).group(1).strip()
            if re.search(r"patches\s*\(([^)]*)\)", control) else None, walls,
            all(wall in control for wall in walls)),
        row("force coefficient contributions", "OpenFOAM forceCoeffs on all physical walls",
            "pressure + viscous shear; Cl, Cd and Cm", "type            forceCoeffs" in control),
        row("force directions", {"drag": actual_drag, "lift": actual_lift},
            {"drag": expected_drag, "lift": expected_lift},
            vectors_close(actual_drag, expected_drag) and vectors_close(actual_lift, expected_lift)),
        row("force normalization", {"lRef": scalar(control, "lRef"), "Aref": scalar(control, "Aref")},
            {"lRef": chord, "Aref": chord * float(cfg.get("spanwise_thickness_chord") or 0.0) * chord},
            math.isclose(scalar(control, "lRef") or -1.0, chord, rel_tol=2e-6)
            and math.isclose(scalar(control, "Aref") or -1.0,
                             chord * float(cfg.get("spanwise_thickness_chord") or 0.0) * chord, rel_tol=2e-6)),
        row("second-order final temporal/spatial schemes", schemes.strip()[:120],
            "backward with limited second-order U and nuTilda convection",
            second_order_final_schemes(schemes)),
        row("RANS to URANS restart transfer", "inventory-driven state transfer with digest audit",
            "all required volume fields plus phi/nut when available", transfer_contract_ok),
        row("checkMesh", "Mesh OK" if "Mesh OK" in check_log else "not available/fail", "Mesh OK", "Mesh OK" in check_log),
        row("y+ integrated-to-wall", yplus_max, "measured max <= 1",
            yplus_max is not None and yplus_max <= 1.0,
            "runtime warning required when missing or measured max exceeds 1"),
    ]
    return {
        "case": str(case.resolve()),
        "variant": cfg.get("variant"),
        "topology": cfg.get("geometry_topology"),
        "rows": rows,
        "status": "PASS" if all(item["status"] == "PASS" for item in rows) else "ACTION_REQUIRED",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--case", type=Path, action="append", default=[])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    root = args.project_root.resolve()
    cases = [path.resolve() for path in args.case] or [
        root / "CFD_2D/openfoam_cases/reference_uncut_validation_1m/alpha_p12p000",
        root / "CFD_2D/openfoam_cases/open_ramair_validation_1m/alpha_p4p000",
    ]
    report = {"schema_version": 1, "cases": [audit_case(root, case) for case in cases]}
    report["status"] = "PASS" if all(item["status"] == "PASS" for item in report["cases"]) else "ACTION_REQUIRED"
    output = (args.output or root / "CFD_2D/app_state/openfoam_case_audit.json").resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
