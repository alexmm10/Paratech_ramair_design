#!/usr/bin/env python3
"""OpenFOAM case writer for approved ram-air 2D meshes.

Writes 0/, constant/ and system/ folders for an incompressible transient RANS case.
It does not run OpenFOAM.  By default it requires MESH_APPROVED.flag.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import sys
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any

from ramair_2d_timestep_advisor import (
    assessment_markdown,
    build_timestep_assessment,
)

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

CATIA_INPUTS_DIR_NAME = "CATIA/Inputs"
CFD_ROOT_DIR_NAME = "CFD_2D"
CFD_INPUTS_DIR_NAME = "CFD_2D_inputs"


def project_root_from_case_root(case_root: Path) -> Path:
    case_root = Path(case_root)
    if case_root.name == CATIA_INPUTS_DIR_NAME:
        return case_root.parent
    if case_root.name in {"openfoam_cases", "meshes", CFD_INPUTS_DIR_NAME} and case_root.parent.name == CFD_ROOT_DIR_NAME:
        return case_root.parent.parent
    if case_root.name == CFD_ROOT_DIR_NAME:
        return case_root.parent
    return case_root


def cfd_root(case_root: Path) -> Path:
    return project_root_from_case_root(case_root) / CFD_ROOT_DIR_NAME


def cfd_inputs_root(case_root: Path) -> Path:
    return cfd_root(case_root) / CFD_INPUTS_DIR_NAME


def cfd_meshes_root(case_root: Path) -> Path:
    return cfd_root(case_root) / "meshes"


def cfd_cases_root(case_root: Path) -> Path:
    return cfd_root(case_root) / "openfoam_cases"


def find_converted_polymesh(mesh_root: Path) -> Path | None:
    candidates = [
        mesh_root / "constant" / "polyMesh",
        mesh_root / "openfoam_mesh_check_case" / "constant" / "polyMesh",
    ]
    for p in candidates:
        if (p / "boundary").exists():
            return p
    return None


def reject_forbidden_boundary(poly_mesh: Path) -> None:
    boundary = poly_mesh / "boundary"
    text = boundary.read_text(encoding="utf-8", errors="ignore")
    if "ram_air_inlet" in text:
        raise RuntimeError(f"Forbidden OpenFOAM patch 'ram_air_inlet' found in {boundary}")


def parse_boundary_patches(poly_mesh: Path | None) -> list[dict[str, str]]:
    if poly_mesh is None or not (poly_mesh / "boundary").exists():
        return [
            {"name": "farfield", "type": "patch"},
            {"name": "airfoil_wall", "type": "wall"},
            {"name": "frontAndBack", "type": "empty"},
        ]
    text = (poly_mesh / "boundary").read_text(encoding="utf-8", errors="ignore")
    patches: list[dict[str, str]] = []
    for match in re.finditer(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*\{([^{}]*)\}", text, re.MULTILINE | re.DOTALL):
        if match.group(1) == "FoamFile":
            continue
        body = match.group(2)
        typ = "patch"
        type_match = re.search(r"\btype\s+([A-Za-z0-9_]+)\s*;", body)
        if type_match:
            typ = type_match.group(1)
        patches.append({"name": match.group(1), "type": typ})
    return patches or [
        {"name": "farfield", "type": "patch"},
        {"name": "airfoil_wall", "type": "wall"},
        {"name": "frontAndBack", "type": "empty"},
    ]


def patch_role(name: str, patch_type: str) -> str:
    lname = name.lower()
    ltype = patch_type.lower()
    if "frontandback" in lname or ltype == "empty":
        return "empty"
    if ltype == "wall" or "wall" in lname or "airfoil" in lname or "lip" in lname or "trailing_edge" in lname:
        return "wall"
    if lname in {"outlet", "downstream"}:
        return "outlet"
    if lname in {"inlet", "upstream"}:
        return "inlet"
    return "farfield"


def field_block(name: str, role: str, field: str, cfg: OpenFOAMCaseConfig, uvec: tuple[float, float, float]) -> str:
    Ux, Uy, Uz = uvec
    if role == "empty":
        return f"    {name} {{ type empty; }}"
    if field == "U":
        if role == "wall":
            return f"    {name} {{ type noSlip; }}"
        if role == "outlet":
            return f"    {name} {{ type inletOutlet; inletValue uniform ({Ux:.10g} {Uy:.10g} {Uz:.10g}); value uniform ({Ux:.10g} {Uy:.10g} {Uz:.10g}); }}"
        if role == "farfield" and cfg.farfield_boundary_condition == "freestream":
            return (
                f"    {name} {{ type freestreamVelocity; "
                f"freestreamValue uniform ({Ux:.10g} {Uy:.10g} {Uz:.10g}); "
                f"value uniform ({Ux:.10g} {Uy:.10g} {Uz:.10g}); }}"
            )
        return f"    {name} {{ type fixedValue; value uniform ({Ux:.10g} {Uy:.10g} {Uz:.10g}); }}"
    if field == "p":
        if role == "outlet":
            return f"    {name} {{ type fixedValue; value uniform 0; }}"
        if role == "farfield" and cfg.farfield_boundary_condition == "freestream":
            return f"    {name} {{ type freestreamPressure; freestreamValue uniform 0; value uniform 0; }}"
        return f"    {name} {{ type zeroGradient; }}"
    if field == "nuTilda":
        if role == "wall":
            return f"    {name} {{ type fixedValue; value uniform 0; }}"
        if role == "farfield" and cfg.farfield_boundary_condition == "freestream":
            return (
                f"    {name} {{ type freestream; freestreamValue uniform {3*cfg.nu:.10g}; "
                f"value uniform {3*cfg.nu:.10g}; }}"
            )
        return f"    {name} {{ type fixedValue; value uniform {3*cfg.nu:.10g}; }}"
    if field == "nut":
        if role == "wall":
            return f"    {name} {{ type nutUSpaldingWallFunction; value uniform 0; }}"
        return f"    {name} {{ type calculated; value uniform 0; }}"
    raise ValueError(field)


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def backup_existing_case_dir(case_root: Path, case_dir: Path, variant: str, safe_alpha: str) -> Path | None:
    """Move an existing OpenFOAM case aside before rewriting it."""
    if not case_dir.exists():
        return None
    project_root = project_root_from_case_root(case_root)
    backup_root = project_root / "Previous Versions" / "openfoam_case_backups"
    backup_root.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    target = backup_root / f"{variant}_{safe_alpha}_{stamp}"
    suffix = 1
    while target.exists():
        suffix += 1
        target = backup_root / f"{variant}_{safe_alpha}_{stamp}_{suffix:02d}"
    shutil.move(str(case_dir), str(target))
    write_json(target / "case_backup_manifest.json", {
        "reason": "clean_case_overwrite",
        "variant": variant,
        "alpha_dir": safe_alpha,
        "original_case_dir": str(case_dir),
        "backup_dir": str(target),
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    })
    return target


def prepare_existing_case_dir(
    case_root: Path,
    case_dir: Path,
    variant: str,
    safe_alpha: str,
    action: str,
) -> Path | None:
    """Apply the caller-selected overwrite policy to an existing active case."""
    if not case_dir.exists():
        return None
    if action == "archive":
        return backup_existing_case_dir(case_root, case_dir, variant, safe_alpha)
    if action == "keep":
        raise FileExistsError(
            f"OpenFOAM case already exists and existing-case action is keep: {case_dir}"
        )
    if action != "delete":
        raise ValueError(f"Unsupported existing-case action: {action}")
    expected_parent = (cfd_cases_root(case_root) / variant).resolve()
    target = case_dir.resolve()
    if target.parent != expected_parent:
        raise RuntimeError(f"Refusing to delete unexpected OpenFOAM case path: {target}")
    shutil.rmtree(target)
    print(f"Deleted previous active OpenFOAM case: {target}")
    return None


def foam_header(cls: str, obj: str) -> str:
    return f"""FoamFile
{{
    version     2.0;
    format      ascii;
    class       {cls};
    object      {obj};
}}
"""


def vec(v):
    return f"({v[0]:.10g} {v[1]:.10g} {v[2]:.10g})"


@dataclass
class OpenFOAMCaseConfig:
    config_schema_version: int = 15
    solver: str = "foamRun"
    solver_module: str = "incompressibleFluid"
    turbulence_model: str = "SpalartAllmaras"
    reynolds: float = 4e6
    alpha_deg: float = 4.0
    rho: float = 1.225
    mu: float = 1.81e-5
    chord_m: float = 1.0
    velocity_m_s: float = 1.0
    velocity_source: str = "reynolds"
    reynolds_from_velocity: float = 0.0
    mach_input: float | None = None
    temperature_K: float | None = None
    pressure_ref_pa: float = 101325.0
    speed_of_sound_m_s: float = 340.294
    mach_reynolds_consistency_warning: str | None = None
    velocity_from_reynolds_m_s: float | None = None
    velocity_from_mach_m_s: float | None = None
    required_mu_for_mach_and_re: float | None = None
    required_rho_for_mach_and_re: float | None = None
    spanwise_thickness_chord: float = 0.01
    geometry_topology: str = "closed_external_airfoil"
    numerics_profile: str = "closed_external_airfoil"
    ddt_scheme: str = "Euler"
    n_outer_correctors: int | None = 10
    n_correctors: int = 2
    n_non_orthogonal_correctors: int | None = 1
    outer_corrector_residual_control: dict[str, Any] = field(default_factory=dict)
    transport_correction_final: bool = False
    validation_fixed_subiterations: bool = False
    transient_velocity_divergence_scheme: str = "Gauss linearUpwind limited"
    transient_turbulence_divergence_scheme: str = "Gauss linearUpwind limited"
    time_step_mode: str = "adaptive_physics_limited"
    deltaT_star: float = 0.005
    maxDeltaT_star: float = 0.02
    endTime_star: float = 150.0
    field_write_interval_star: float = 0.25
    field_write_interval_s: float | None = None
    field_write_control: str = "adjustableRunTime"
    field_write_interval_steps: int = 200
    field_write_step_equivalent: int = 2000
    average_from_fraction: float = 0.6
    maxCo: float = 50.0
    purgeWrite: int = 24
    CofR_x_c: float = 0.25
    farfield_boundary_condition: str = "freestream"
    steady_initialization_enabled: bool = False
    steady_max_iterations: int = 20000
    steady_write_interval_iterations: int = 50
    steady_residual_p: float | None = 1.0e-5
    steady_residual_U: float | None = 1.0e-5
    steady_residual_nuTilda: float | None = 1.0e-5
    steady_n_non_orthogonal_correctors: int | None = 0
    steady_relaxation_p: float | None = 0.3
    steady_relaxation_U: float | None = 0.5
    steady_relaxation_nuTilda: float | None = 0.5
    steady_velocity_divergence_scheme: str = "bounded Gauss linearUpwind limited"
    steady_turbulence_divergence_scheme: str = "bounded Gauss upwind"
    temporal_accuracy: dict[str, Any] = field(default_factory=dict)

    @property
    def deltaT(self) -> float:
        requested = self.deltaT_star
        if self.time_step_mode != "fixed":
            requested = min(requested, self.maxDeltaT_star)
        return requested * self.chord_m / max(self.velocity_m_s, 1e-12)
    @property
    def maxDeltaT(self) -> float:
        return self.maxDeltaT_star * self.chord_m / max(self.velocity_m_s, 1e-12)
    @property
    def endTime(self) -> float:
        return self.endTime_star * self.chord_m / max(self.velocity_m_s, 1e-12)
    @property
    def field_write_interval(self) -> float:
        if self.field_write_step_equivalent > 0:
            step = self.deltaT if self.time_step_mode == "fixed" else self.maxDeltaT
            return step * self.field_write_step_equivalent
        if self.field_write_interval_s is not None:
            return self.field_write_interval_s
        return self.field_write_interval_star * self.chord_m / max(self.velocity_m_s, 1e-12)
    @property
    def nu(self) -> float:
        return self.mu / self.rho
    @property
    def dynamic_pressure_pa(self) -> float:
        return 0.5 * self.rho * self.velocity_m_s * self.velocity_m_s
    @property
    def mach_from_velocity(self) -> float:
        return self.velocity_m_s / max(self.speed_of_sound_m_s, 1e-12)
    @property
    def spanwise_thickness_m(self) -> float:
        return self.spanwise_thickness_chord * self.chord_m
    @property
    def reference_area_m2(self) -> float:
        return self.chord_m * self.spanwise_thickness_m


def topology_solver_config(
    raw_solver: dict[str, Any],
    variant_manifest: dict[str, Any],
    variant: str | None,
) -> tuple[dict[str, Any], str, str]:
    """Merge the numerical profile matching the actual fluid topology."""
    variant_name = str(variant or "").lower()
    has_open_inlet = bool(
        variant_manifest.get(
            "has_ram_air_opening_feature",
            variant_manifest.get("has_open_inlet", "open" in variant_name),
        )
    )
    topology = "open_internal_cavity" if has_open_inlet else "closed_external_airfoil"
    profiles = raw_solver.get("topology_profiles", {})
    if not isinstance(profiles, dict):
        raise ValueError("topology_profiles must be a JSON object")
    profile = profiles.get(topology, {})
    if not isinstance(profile, dict):
        raise ValueError(f"topology_profiles.{topology} must be a JSON object")

    effective = dict(raw_solver)
    for key, value in profile.items():
        if key in {
            "steady_numerics",
            "steady_residual_control",
            "temporal_accuracy",
        } and isinstance(value, dict):
            nested = dict(effective.get(key) or {})
            nested.update(value)
            effective[key] = nested
        else:
            effective[key] = value
    profile_id = str(profile.get("profile_id", topology))
    return effective, topology, profile_id


def optional_float(mapping: dict[str, Any], key: str, default: float) -> float | None:
    value = mapping[key] if key in mapping else default
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    return float(value)


def optional_int(
    mapping: dict[str, Any],
    key: str,
    default: int,
    *,
    minimum: int,
) -> int | None:
    value = mapping[key] if key in mapping else default
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    return max(minimum, int(value))


def foam_optional_entry(name: str, value: int | float | None, indent: int = 4) -> str:
    if value is None:
        return ""
    rendered = str(value) if isinstance(value, int) else f"{value:.10g}"
    return " " * indent + f"{name} {rendered};\n"


def format_optional(value: int | float | None, spec: str = ".6g") -> str:
    return "OpenFOAM default (entry omitted)" if value is None else format(value, spec)


def load_physical(
    case_root: Path,
    alpha: float,
    reynolds: float | None = None,
    variant: str | None = None,
) -> OpenFOAMCaseConfig:
    phys = read_json(cfd_inputs_root(case_root) / "case_package" / "physical_config.json", {})
    case_template = read_json(cfd_inputs_root(case_root) / "config" / "cfd2d_case_config_template.json", {})
    phys_defaults = read_json(cfd_inputs_root(case_root) / "config" / "cfd2d_physical_defaults.json", {})
    merged_phys = dict(case_template or {})
    merged_phys.update(phys_defaults or {})
    merged_phys.update(phys or {})
    raw_solver = read_json(cfd_inputs_root(case_root) / "config" / "cfd2d_solver_config.json", {})
    mesh_cfg = read_json(cfd_inputs_root(case_root) / "config" / "cfd2d_mesh_config.json", {})
    variant_manifest = read_json(
        cfd_inputs_root(case_root) / "case_package" / str(variant) / "manifest.json",
        {},
    ) if variant else {}
    solver, geometry_topology, numerics_profile = topology_solver_config(
        raw_solver,
        variant_manifest,
        variant,
    )
    chord = float(variant_manifest.get("chord_m", merged_phys.get("chord_m", 1.0)))
    rho = float(merged_phys.get("rho", merged_phys.get("rho_kg_m3", 1.225)))
    mu = float(merged_phys.get("mu", merged_phys.get("mu_pa_s", 1.81e-5)))
    Re = float(merged_phys.get("reynolds", 4e6))
    if reynolds is not None and not math.isclose(float(reynolds), Re, rel_tol=1.0e-12, abs_tol=0.0):
        raise ValueError(
            "--reynolds no longer overrides the CFD Case. Update case_package/physical_config.json "
            f"instead (CFD Case Re={Re:.12g}, requested override={float(reynolds):.12g})."
        )
    speed_of_sound = float(merged_phys.get("speed_of_sound_m_s", 340.294))
    mach_input = float(merged_phys["mach"]) if "mach" in merged_phys else None
    u_re = Re * mu / (rho * chord)
    u_mach = mach_input * speed_of_sound if mach_input is not None else None
    velocity_source = str(solver.get("velocity_source", merged_phys.get("velocity_source", "reynolds"))).lower()
    if velocity_source == "mach" and u_mach is not None:
        U = u_mach
    elif velocity_source == "velocity" and "velocity_m_s" in merged_phys:
        U = float(merged_phys["velocity_m_s"])
    else:
        velocity_source = "reynolds"
        U = u_re
    reynolds_from_velocity = rho * U * chord / max(mu, 1e-30)
    required_mu = rho * (u_mach or U) * chord / max(Re, 1e-30) if u_mach is not None else None
    required_rho = Re * mu / max((u_mach or U) * chord, 1e-30) if u_mach is not None else None
    consistency_warning = None
    if u_mach is not None:
        rel = abs(u_re - u_mach) / max(abs(u_re), abs(u_mach), 1e-30)
        if rel > 0.05:
            consistency_warning = (
                "Configured Mach and Reynolds are not simultaneously matched by the current rho/mu/chord. "
                f"Using velocity_source={velocity_source}; U_Re={u_re:.6g} m/s, U_Mach={u_mach:.6g} m/s."
            )
    field_write_control = str(solver.get("field_write_control", "adjustableRunTime"))
    if field_write_control not in {"timeStep", "runTime", "adjustableRunTime"}:
        raise ValueError("field_write_control must be timeStep, runTime or adjustableRunTime")
    steady_residual_control = solver.get("steady_residual_control", {})
    if not isinstance(steady_residual_control, dict):
        raise ValueError("steady_residual_control must be a JSON object with p, U and nuTilda tolerances")
    steady_numerics = solver.get("steady_numerics", {})
    if not isinstance(steady_numerics, dict):
        raise ValueError("steady_numerics must be a JSON object")
    steady_relaxation = {
        "p": optional_float(steady_numerics, "p_relaxation", 0.3),
        "U": optional_float(steady_numerics, "U_relaxation", 0.5),
        "nuTilda": optional_float(steady_numerics, "nuTilda_relaxation", 0.5),
    }
    invalid_relaxation = {
        name: value for name, value in steady_relaxation.items()
        if value is not None and not (0.0 < value <= 1.0)
    }
    if invalid_relaxation:
        raise ValueError(f"steady_numerics relaxation factors must be in (0, 1]: {invalid_relaxation}")
    farfield_boundary_condition = str(solver.get("farfield_boundary_condition", "freestream"))
    if farfield_boundary_condition not in {"freestream", "fixed_velocity_fallback"}:
        raise ValueError("farfield_boundary_condition must be freestream or fixed_velocity_fallback")
    ddt_scheme = str(solver.get("ddt_scheme", "Euler")).strip()
    if ddt_scheme not in {"Euler", "backward", "CrankNicolson 0.9"}:
        raise ValueError(
            "ddt_scheme must be Euler, backward or 'CrankNicolson 0.9'; "
            f"received {ddt_scheme!r}"
        )
    time_step_mode = str(solver.get("time_step_mode", "adaptive_physics_limited")).strip().lower()
    aliases = {
        "adaptive": "adaptive_courant",
        "adjustable": "adaptive_courant",
        "adaptive_physics_limited": "adaptive_physics_limited",
        "constant": "fixed",
    }
    time_step_mode = aliases.get(time_step_mode, time_step_mode)
    if time_step_mode not in {"adaptive_courant", "adaptive_physics_limited", "fixed"}:
        raise ValueError(
            "time_step_mode must be adaptive_courant, adaptive_physics_limited or fixed; "
            f"received {time_step_mode!r}"
        )
    outer_control_raw = solver.get("outer_corrector_residual_control", {})
    if not isinstance(outer_control_raw, dict):
        raise ValueError("outer_corrector_residual_control must be a JSON object")
    raw_fields = outer_control_raw.get("fields")
    fields: dict[str, dict[str, float]] = {}
    if isinstance(raw_fields, dict):
        for field_name, field_values in raw_fields.items():
            if field_name not in {"U", "p", "nuTilda"} or not isinstance(field_values, dict):
                raise ValueError(f"Unsupported outer residual field: {field_name!r}")
            fields[field_name] = {
                "tolerance": float(field_values.get("tolerance", 1.0e-4)),
                "relTol": float(field_values.get("relTol", 0.0)),
            }
    else:
        legacy_rel_tol = float(outer_control_raw.get("relative_tolerance", 0.0))
        legacy_fields = (
            ("U", "p")
            if geometry_topology == "open_internal_cavity"
            else ("U", "nuTilda")
        )
        for field_name in legacy_fields:
            fields[field_name] = {
                "tolerance": float(outer_control_raw.get(f"{field_name}_tolerance", 1.0e-4)),
                "relTol": legacy_rel_tol,
            }
    outer_control = {
        "enabled": bool(outer_control_raw.get("enabled", True)),
        "fields": fields,
    }
    if not fields or any(values["tolerance"] <= 0 for values in fields.values()):
        raise ValueError("Outer-corrector absolute tolerances must be positive")
    if any(values["relTol"] < 0 for values in fields.values()):
        raise ValueError("Outer-corrector relative tolerance cannot be negative")
    validation_fixed_subiterations = bool(
        (raw_solver.get("validation_study") or {}).get("enabled", False)
    )
    transient_velocity_scheme = str(
        solver.get("transient_velocity_divergence_scheme", "Gauss linearUpwind limited")
    ).strip()
    transient_turbulence_scheme = str(
        solver.get("transient_turbulence_divergence_scheme", "Gauss linearUpwind limited")
    ).strip()
    steady_velocity_scheme = str(
        steady_numerics.get("velocity_divergence_scheme", "bounded Gauss linearUpwind limited")
    ).strip()
    steady_turbulence_scheme = str(
        steady_numerics.get("turbulence_divergence_scheme", "bounded Gauss upwind")
    ).strip()
    for label, scheme in {
        "transient_velocity_divergence_scheme": transient_velocity_scheme,
        "transient_turbulence_divergence_scheme": transient_turbulence_scheme,
        "steady_numerics.velocity_divergence_scheme": steady_velocity_scheme,
        "steady_numerics.turbulence_divergence_scheme": steady_turbulence_scheme,
    }.items():
        if not scheme or ";" in scheme or "\n" in scheme:
            raise ValueError(f"{label} contains an invalid OpenFOAM scheme: {scheme!r}")
    turbulence_model = str(solver.get("turbulence_model", "SpalartAllmaras"))
    if turbulence_model != "SpalartAllmaras":
        raise NotImplementedError(
            "The current case writer has complete boundary fields and residual monitoring only for "
            "SpalartAllmaras. Select SpalartAllmaras until kOmegaSST field generation is implemented."
        )
    return OpenFOAMCaseConfig(
        solver=str(solver.get("solver", "foamRun")),
        solver_module=str(solver.get("solver_module", "incompressibleFluid")),
        turbulence_model=turbulence_model,
        reynolds=Re,
        alpha_deg=float(alpha),
        rho=rho,
        mu=mu,
        chord_m=chord,
        velocity_m_s=U,
        velocity_source=velocity_source,
        reynolds_from_velocity=reynolds_from_velocity,
        mach_input=mach_input,
        temperature_K=float(merged_phys["temperature_K"]) if "temperature_K" in merged_phys else None,
        pressure_ref_pa=float(merged_phys.get("pressure_ref_pa", 101325.0)),
        speed_of_sound_m_s=speed_of_sound,
        mach_reynolds_consistency_warning=consistency_warning,
        velocity_from_reynolds_m_s=u_re,
        velocity_from_mach_m_s=u_mach,
        required_mu_for_mach_and_re=required_mu,
        required_rho_for_mach_and_re=required_rho,
        spanwise_thickness_chord=float(mesh_cfg.get("spanwise_thickness_chord", 0.01)),
        geometry_topology=geometry_topology,
        numerics_profile=numerics_profile,
        ddt_scheme=ddt_scheme,
        n_outer_correctors=optional_int(solver, "n_outer_correctors", 10, minimum=1),
        n_correctors=max(1, int(solver.get("n_correctors", 2))),
        n_non_orthogonal_correctors=optional_int(
            solver, "n_non_orthogonal_correctors", 1, minimum=0
        ),
        outer_corrector_residual_control=outer_control,
        transport_correction_final=bool(solver.get("transport_correction_final", False)),
        validation_fixed_subiterations=validation_fixed_subiterations,
        transient_velocity_divergence_scheme=transient_velocity_scheme,
        transient_turbulence_divergence_scheme=transient_turbulence_scheme,
        time_step_mode=time_step_mode,
        deltaT_star=float(solver.get("deltaT_star", 0.005)),
        maxDeltaT_star=float(solver.get("maxDeltaT_star", 0.02)),
        endTime_star=float(solver.get("endTime_star", 150)),
        field_write_interval_star=float(
            solver.get("field_write_interval_star", solver.get("writeInterval_star", 0.25))
        ),
        field_write_interval_s=(
            float(solver["field_write_interval_s"])
            if solver.get("field_write_interval_s") is not None
            else None
        ),
        field_write_control=field_write_control,
        field_write_interval_steps=max(1, int(solver.get("field_write_interval_steps", 200))),
        field_write_step_equivalent=max(1, int(solver.get("field_write_step_equivalent", 2000))),
        average_from_fraction=float(solver.get("average_from_fraction", 0.6)),
        maxCo=float(
            solver.get(
                "maxCo",
                25.0 if geometry_topology == "open_internal_cavity" else 50.0,
            )
        ),
        purgeWrite=max(0, int(solver.get("purgeWrite", 24))),
        farfield_boundary_condition=farfield_boundary_condition,
        steady_initialization_enabled=bool(solver.get("steady_initialization_enabled", False)),
        steady_max_iterations=max(10, int(solver.get("steady_max_iterations", 20000))),
        steady_write_interval_iterations=max(1, int(solver.get("steady_write_interval_iterations", 50))),
        steady_residual_p=optional_float(steady_residual_control, "p", 1.0e-5),
        steady_residual_U=optional_float(steady_residual_control, "U", 1.0e-5),
        steady_residual_nuTilda=optional_float(steady_residual_control, "nuTilda", 1.0e-5),
        steady_n_non_orthogonal_correctors=optional_int(
            steady_numerics, "n_non_orthogonal_correctors", 0, minimum=0
        ),
        steady_relaxation_p=steady_relaxation["p"],
        steady_relaxation_U=steady_relaxation["U"],
        steady_relaxation_nuTilda=steady_relaxation["nuTilda"],
        steady_velocity_divergence_scheme=steady_velocity_scheme,
        steady_turbulence_divergence_scheme=steady_turbulence_scheme,
        temporal_accuracy=dict(solver.get("temporal_accuracy") or {}),
    )


def freestream_dirs(alpha_deg: float):
    a = math.radians(alpha_deg)
    drag = (math.cos(a), math.sin(a), 0.0)
    lift = (-math.sin(a), math.cos(a), 0.0)
    return drag, lift


def write_0(case_dir: Path, cfg: OpenFOAMCaseConfig, patches: list[dict[str, str]] | None = None) -> None:
    patches = patches or parse_boundary_patches(None)
    Ux = cfg.velocity_m_s * math.cos(math.radians(cfg.alpha_deg))
    Uy = cfg.velocity_m_s * math.sin(math.radians(cfg.alpha_deg))
    uvec = (Ux, Uy, 0.0)
    blocks = {
        field: "\n".join(field_block(p["name"], patch_role(p["name"], p["type"]), field, cfg, uvec) for p in patches)
        for field in ["U", "p", "nuTilda", "nut"]
    }
    (case_dir / "0").mkdir(parents=True, exist_ok=True)
    (case_dir / "0" / "U").write_text(foam_header("volVectorField", "U") + f"""
dimensions      [0 1 -1 0 0 0 0];
internalField   uniform ({Ux:.10g} {Uy:.10g} 0);
boundaryField
{{
{blocks["U"]}
}}
""", encoding="utf-8")
    (case_dir / "0" / "p").write_text(foam_header("volScalarField", "p") + f"""
dimensions      [0 2 -2 0 0 0 0];
internalField   uniform 0;
boundaryField
{{
{blocks["p"]}
}}
""", encoding="utf-8")
    (case_dir / "0" / "nuTilda").write_text(foam_header("volScalarField", "nuTilda") + f"""
dimensions      [0 2 -1 0 0 0 0];
internalField   uniform {3*cfg.nu:.10g};
boundaryField
{{
{blocks["nuTilda"]}
}}
""", encoding="utf-8")
    (case_dir / "0" / "nut").write_text(foam_header("volScalarField", "nut") + f"""
dimensions      [0 2 -1 0 0 0 0];
internalField   uniform 0;
boundaryField
{{
{blocks["nut"]}
}}
""", encoding="utf-8")


def write_constant(case_dir: Path, cfg: OpenFOAMCaseConfig) -> None:
    (case_dir / "constant").mkdir(parents=True, exist_ok=True)
    (case_dir / "constant" / "physicalProperties").write_text(foam_header("dictionary", "physicalProperties") + f"""
viscosityModel constant;
nu              {cfg.nu:.12g};
""", encoding="utf-8")
    (case_dir / "constant" / "transportProperties").write_text(foam_header("dictionary", "transportProperties") + f"""
transportModel  Newtonian;
nu              [0 2 -1 0 0 0 0] {cfg.nu:.12g};
""", encoding="utf-8")
    (case_dir / "constant" / "turbulenceProperties").write_text(foam_header("dictionary", "turbulenceProperties") + f"""
simulationType RAS;
RAS
{{
    RASModel        {cfg.turbulence_model};
    turbulence      on;
    printCoeffs     on;
}}
""", encoding="utf-8")
    (case_dir / "constant" / "momentumTransport").write_text(foam_header("dictionary", "momentumTransport") + f"""
simulationType  RAS;
RAS
{{
    model           {cfg.turbulence_model};
    turbulence      on;
    printCoeffs     on;
    viscosityModel  Newtonian;
}}
""", encoding="utf-8")


def write_steady_initialization_templates(case_dir: Path, cfg: OpenFOAMCaseConfig, force_patch_list: str) -> None:
    """Write an opt-in SIMPLE stage without changing the transient case files."""
    directory = case_dir / "system" / "steadyInitialization"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "controlDict").write_text(foam_header("dictionary", "controlDict") + f"""
application     {cfg.solver};
solver          {cfg.solver_module};
startFrom       startTime;
startTime       0;
stopAt          endTime;
endTime         {cfg.steady_max_iterations};
deltaT          1;
writeControl    timeStep;
writeInterval   {cfg.steady_write_interval_iterations};
purgeWrite      0;
writeFormat     ascii;
writePrecision  8;
writeCompression on;
timeFormat      general;
timePrecision   8;
runTimeModifiable true;
adjustTimeStep  no;
functions
{{
    forceCoeffs
    {{
        type            forceCoeffs;
        libs            ("libforces.so");
        patches         ({force_patch_list});
        rho             rhoInf;
        rhoInf          {cfg.rho:.10g};
        CofR            ({cfg.CofR_x_c*cfg.chord_m:.10g} 0 0);
        liftDir         {vec(freestream_dirs(cfg.alpha_deg)[1])};
        dragDir         {vec(freestream_dirs(cfg.alpha_deg)[0])};
        pitchAxis       (0 0 1);
        magUInf         {cfg.velocity_m_s:.10g};
        lRef            {cfg.chord_m:.10g};
        Aref            {cfg.reference_area_m2:.10g};
        writeControl    timeStep;
        writeInterval   1;
    }}
    residuals
    {{
        type            residuals;
        libs            ("libutilityFunctionObjects.so");
        fields          (U p nuTilda);
        writeControl    timeStep;
        writeInterval   1;
    }}
    pressureCoefficient
    {{
        type            pressure;
        libs            ("libfieldFunctionObjects.so");
        field           p;
        result          Cp;
        rho             rhoInf;
        rhoInf          {cfg.rho:.10g};
        calcTotal       no;
        calcCoeff       yes;
        pInf            0;
        UInf            ({cfg.velocity_m_s*math.cos(math.radians(cfg.alpha_deg)):.10g} {cfg.velocity_m_s*math.sin(math.radians(cfg.alpha_deg)):.10g} 0);
        executeControl  writeTime;
        writeControl    writeTime;
    }}
}}
""", encoding="utf-8")
    steady_schemes = """
ddtSchemes { default steadyState; }
gradSchemes
{
    default          Gauss linear;
    grad(U)          cellLimited Gauss linear 1;
    grad(nuTilda)    cellLimited Gauss linear 1;
}
divSchemes
{
    default none;
    div(phi,U) __STEADY_U_SCHEME__;
    div(phi,nuTilda) __STEADY_TURBULENCE_SCHEME__;
    div((nuEff*dev2(T(grad(U))))) Gauss linear;
}
laplacianSchemes { default Gauss linear corrected; }
interpolationSchemes { default linear; }
snGradSchemes { default corrected; }
wallDist { method meshWave; }
"""
    steady_schemes = (
        steady_schemes
        .replace("__STEADY_U_SCHEME__", cfg.steady_velocity_divergence_scheme)
        .replace("__STEADY_TURBULENCE_SCHEME__", cfg.steady_turbulence_divergence_scheme)
    )
    (directory / "fvSchemes").write_text(
        foam_header("dictionary", "fvSchemes") + steady_schemes,
        encoding="utf-8",
    )
    steady_residual_entries = "".join(
        foam_optional_entry(name, value, 8)
        for name, value in (
            ("p", cfg.steady_residual_p),
            ("U", cfg.steady_residual_U),
            ("nuTilda", cfg.steady_residual_nuTilda),
        )
    )
    steady_residual_block = (
        "    residualControl\n"
        "    {\n"
        f"{steady_residual_entries}"
        "    }\n"
        if steady_residual_entries else ""
    )
    steady_algorithm_controls = foam_optional_entry(
        "nNonOrthogonalCorrectors", cfg.steady_n_non_orthogonal_correctors, 4
    )
    steady_relaxation_fields = (
        f"    fields {{ p {cfg.steady_relaxation_p:.10g}; }}\n"
        if cfg.steady_relaxation_p is not None else ""
    )
    steady_equation_entries = " ".join(
        f"{name} {value:.10g};"
        for name, value in (
            ("U", cfg.steady_relaxation_U),
            ("nuTilda", cfg.steady_relaxation_nuTilda),
        )
        if value is not None
    )
    steady_relaxation_equations = (
        f"    equations {{ {steady_equation_entries} }}\n"
        if steady_equation_entries else ""
    )
    steady_relaxation_block = (
        "relaxationFactors\n"
        "{\n"
        f"{steady_relaxation_fields}"
        f"{steady_relaxation_equations}"
        "}\n"
        if steady_relaxation_fields or steady_relaxation_equations else ""
    )
    (directory / "fvSolution").write_text(foam_header("dictionary", "fvSolution") + f"""
solvers
{{
    Phi
    {{
        solver GAMG;
        smoother DIC;
        tolerance 1e-6;
        relTol 0.01;
    }}
    p {{ solver GAMG; smoother DIC; tolerance 1e-8; relTol 0.01; }}
    U {{ solver PBiCGStab; preconditioner DILU; tolerance 1e-10; relTol 0.05; }}
    nuTilda {{ solver PBiCGStab; preconditioner DILU; tolerance 1e-10; relTol 0.05; }}
}}
SIMPLE
{{
{steady_algorithm_controls}    pRefCell        0;
    pRefValue       0;
{steady_residual_block}}}
{steady_relaxation_block}
""", encoding="utf-8")
    write_json(directory / "stage_config.json", {
        "config_schema_version": 5,
        "numerics_profile": cfg.numerics_profile,
        "geometry_topology": cfg.geometry_topology,
        "enabled_by_default": cfg.steady_initialization_enabled,
        "algorithm": "SIMPLE",
        "ddt_scheme": "steadyState",
        "maximum_iterations": cfg.steady_max_iterations,
        "write_interval_iterations": cfg.steady_write_interval_iterations,
        "residual_control": {
            "p": cfg.steady_residual_p,
            "U": cfg.steady_residual_U,
            "nuTilda": cfg.steady_residual_nuTilda,
        },
        "numerics": {
            "potential_flow_solver": {
                "field": "Phi",
                "solver": "GAMG",
                "smoother": "DIC",
                "tolerance": 1.0e-6,
                "relative_tolerance": 0.01,
            },
            "grad_U": "cellLimited Gauss linear 1",
            "grad_nuTilda": "cellLimited Gauss linear 1",
            "div_phi_U": cfg.steady_velocity_divergence_scheme,
            "div_phi_nuTilda": cfg.steady_turbulence_divergence_scheme,
            "n_non_orthogonal_correctors": cfg.steady_n_non_orthogonal_correctors,
            "relaxation": {
                "p": cfg.steady_relaxation_p,
                "U": cfg.steady_relaxation_U,
                "nuTilda": cfg.steady_relaxation_nuTilda,
            },
        },
        "transition_note": (
            "The staged runner transfers reconstructed steady fields to transient 0/ only after "
            "the configured residual and force-history checks, or after an explicit diagnostic override."
        ),
    })


def write_system(case_dir: Path, cfg: OpenFOAMCaseConfig, patches: list[dict[str, str]] | None = None) -> None:
    patches = patches or parse_boundary_patches(None)
    wall_patches = [p["name"] for p in patches if patch_role(p["name"], p["type"]) == "wall"]
    force_patch_list = " ".join(wall_patches) if wall_patches else "airfoil_wall"
    (case_dir / "system").mkdir(parents=True, exist_ok=True)
    field_write_interval = (
        cfg.field_write_step_equivalent
        if cfg.field_write_control == "timeStep"
        else cfg.field_write_interval
    )
    time_step_controls = (
        "adjustTimeStep  yes;\n"
        f"maxCo           {cfg.maxCo:.10g};\n"
        f"maxDeltaT       {cfg.maxDeltaT:.10g};"
        if cfg.time_step_mode != "fixed"
        else "adjustTimeStep  no;"
    )
    (case_dir / "system" / "controlDict").write_text(foam_header("dictionary", "controlDict") + f"""
application     {cfg.solver};
solver          {cfg.solver_module};
startFrom       startTime;
startTime       0;
stopAt          endTime;
endTime         {cfg.endTime:.10g};
deltaT          {cfg.deltaT:.10g};
writeControl    {cfg.field_write_control};
writeInterval   {field_write_interval:.10g};
purgeWrite      {cfg.purgeWrite};
writeFormat     ascii;
writePrecision  8;
writeCompression on;
timeFormat      general;
timePrecision   6;
runTimeModifiable true;
{time_step_controls}
functions
{{
    forceCoeffs
    {{
        type            forceCoeffs;
        libs            ("libforces.so");
        patches         ({force_patch_list});
        rho             rhoInf;
        rhoInf          {cfg.rho:.10g};
        CofR            ({cfg.CofR_x_c*cfg.chord_m:.10g} 0 0);
        liftDir         {vec(freestream_dirs(cfg.alpha_deg)[1])};
        dragDir         {vec(freestream_dirs(cfg.alpha_deg)[0])};
        pitchAxis       (0 0 1);
        magUInf         {cfg.velocity_m_s:.10g};
        lRef            {cfg.chord_m:.10g};
        Aref            {cfg.reference_area_m2:.10g};
        writeControl    timeStep;
        writeInterval   1;
    }}
    residuals
    {{
        type            residuals;
        libs            ("libutilityFunctionObjects.so");
        fields          (U p nuTilda);
        writeControl    timeStep;
        writeInterval   1;
    }}
    pressureCoefficient
    {{
        type            pressure;
        libs            ("libfieldFunctionObjects.so");
        field           p;
        result          Cp;
        rho             rhoInf;
        rhoInf          {cfg.rho:.10g};
        calcTotal       no;
        calcCoeff       yes;
        pInf            0;
        UInf            ({cfg.velocity_m_s*math.cos(math.radians(cfg.alpha_deg)):.10g} {cfg.velocity_m_s*math.sin(math.radians(cfg.alpha_deg)):.10g} 0);
        executeControl  writeTime;
        writeControl    writeTime;
    }}
    courantNumber
    {{
        type            CourantNo;
        libs            ("libfieldFunctionObjects.so");
        result          Co;
        log             true;
        executeControl  writeTime;
        executeInterval 1;
        writeControl    writeTime;
        writeInterval   1;
    }}
    yPlus
    {{
        type            yPlus;
        libs            ("libfieldFunctionObjects.so");
        executeControl  writeTime;
        writeControl    writeTime;
    }}
    wallShearStress
    {{
        type            wallShearStress;
        libs            ("libfieldFunctionObjects.so");
        executeControl  writeTime;
        writeControl    writeTime;
    }}
    vorticity
    {{
        type            vorticity;
        libs            ("libfieldFunctionObjects.so");
        U               U;
        field           $U;
        executeControl  writeTime;
        writeControl    writeTime;
    }}
}}
""", encoding="utf-8")
    # OpenFOAM 13/14 read fvModels/fvConstraints as top-level dictionaries of
    # optional model entries. For an empty debug case, leave them empty after
    # the FoamFile header; a wrapper such as models{} would be parsed as a
    # model entry without a type and can stop foamRun at startup.
    (case_dir / "system" / "fvModels").write_text(foam_header("dictionary", "fvModels") + "\n", encoding="utf-8")
    (case_dir / "system" / "fvConstraints").write_text(foam_header("dictionary", "fvConstraints") + "\n", encoding="utf-8")
    (case_dir / "system" / "fvSchemes").write_text(
        foam_header("dictionary", "fvSchemes")
        + f"\nddtSchemes {{ default {cfg.ddt_scheme}; }}\n"
        + """
gradSchemes
{
    default          Gauss linear;
    grad(U)          cellLimited Gauss linear 1;
    grad(nuTilda)    cellLimited Gauss linear 1;
}
divSchemes
{
    default none;
    div(phi,U) __TRANSIENT_U_SCHEME__;
    div(phi,nuTilda) __TRANSIENT_TURBULENCE_SCHEME__;
    div((nuEff*dev2(T(grad(U))))) Gauss linear;
}
laplacianSchemes { default Gauss linear corrected; }
interpolationSchemes { default linear; }
snGradSchemes { default corrected; }
wallDist { method meshWave; }
"""
        .replace("__TRANSIENT_U_SCHEME__", cfg.transient_velocity_divergence_scheme)
        .replace("__TRANSIENT_TURBULENCE_SCHEME__", cfg.transient_turbulence_divergence_scheme),
        encoding="utf-8",
    )
    fv_solution = """
solvers
{
    p { solver GAMG; tolerance 1e-7; relTol 0.01; smoother DICGaussSeidel; }
    pFinal { $p; relTol 0; }
    U { solver smoothSolver; smoother symGaussSeidel; tolerance 1e-8; relTol 0.1; }
    UFinal { $U; relTol 0; }
    nuTilda { solver smoothSolver; smoother symGaussSeidel; tolerance 1e-8; relTol 0.1; }
    nuTildaFinal { $nuTilda; relTol 0; }
}
PIMPLE
{
__PIMPLE_CONTROLS__
    pRefCell 0;
    pRefValue 0;
__OUTER_RESIDUAL_CONTROL__
}
relaxationFactors
{
    equations { ".*" 1; }
}
"""
    pimple_controls = (
        foam_optional_entry("nOuterCorrectors", cfg.n_outer_correctors, 4)
        + foam_optional_entry("nCorrectors", cfg.n_correctors, 4)
        + foam_optional_entry(
            "nNonOrthogonalCorrectors", cfg.n_non_orthogonal_correctors, 4
        )
        + f"    transportCorrectionFinal {'true' if cfg.transport_correction_final else 'false'};\n"
    ).rstrip()
    outer_control = cfg.outer_corrector_residual_control
    outer_residual_control = ""
    if bool(outer_control.get("enabled")) and not cfg.validation_fixed_subiterations:
        field_blocks: list[str] = []
        for field_name, values in dict(outer_control.get("fields") or {}).items():
            field_blocks.extend([
                f"        {field_name}",
                "        {",
                f"            tolerance {float(values['tolerance']):.10g};",
                f"            relTol {float(values['relTol']):.10g};",
                "        }",
            ])
        outer_residual_control = "\n".join([
            "    outerCorrectorResidualControl",
            "    {",
            *field_blocks,
            "    }",
        ])
    fv_solution = (
        fv_solution
        .replace("__PIMPLE_CONTROLS__", pimple_controls)
        .replace("__OUTER_RESIDUAL_CONTROL__", outer_residual_control)
    )
    (case_dir / "system" / "fvSolution").write_text(
        foam_header("dictionary", "fvSolution") + fv_solution,
        encoding="utf-8",
    )
    (case_dir / "system" / "decomposeParDict").write_text(foam_header("dictionary", "decomposeParDict") + """
numberOfSubdomains 4;
method scotch;
""", encoding="utf-8")
    write_steady_initialization_templates(case_dir, cfg, force_patch_list)


def case_input_summary(cfg: OpenFOAMCaseConfig, mesh_root: Path, poly_src: Path | None, patches: list[dict[str, str]]) -> dict[str, Any]:
    Ux = cfg.velocity_m_s * math.cos(math.radians(cfg.alpha_deg))
    Uy = cfg.velocity_m_s * math.sin(math.radians(cfg.alpha_deg))
    quality = read_json(mesh_root / "mesh_quality_report.json", {}) or {}
    cell_count = int(
        quality.get("checkMesh_cell_count")
        or quality.get("cell_count")
        or quality.get("n_cells")
        or 0
    )
    # Planning estimate only. Compressed OpenFOAM text size depends on field
    # smoothness and precision; 130 bytes/cell is deliberately conservative for
    # U, p, turbulence plus the three derived fields used by this workflow.
    estimated_snapshot_mb = cell_count * 130.0 / 1048576.0 if cell_count else None
    retained_convective_time = (
        cfg.field_write_interval
        * cfg.velocity_m_s
        / max(cfg.chord_m, 1.0e-12)
        * cfg.purgeWrite
        if cfg.purgeWrite > 0 and cfg.field_write_control != "timeStep"
        else None
    )
    force_patches = [
        patch["name"]
        for patch in patches
        if patch_role(patch["name"], patch["type"]) == "wall"
    ]
    patch_types = {str(patch["name"]): str(patch["type"]) for patch in patches}
    open_topology = cfg.geometry_topology == "open_internal_cavity"
    internal_wall = patch_types.get("airfoil_wall_internal") == "wall"
    external_wall = patch_types.get("airfoil_wall_external") == "wall"
    inlet_is_patch = any(
        name in patch_types for name in ("ram_air_inlet", "airfoil_inlet", "inlet_bridge")
    )
    wall_topology_audit = {
        "applicable": open_topology,
        "status": (
            "PASS"
            if not open_topology or (internal_wall and external_wall and not inlet_is_patch)
            else "FAIL"
        ),
        "external_wall_patch": external_wall,
        "internal_wall_patch": internal_wall,
        "inlet_is_connected_fluid_not_patch": not inlet_is_patch,
        "create_baffles_required": False,
        "basis": (
            "Both fabric sides are already explicit polyMesh wall boundary patches; "
            "the inlet gap connects the internal and external fluid. createBaffles is "
            "only required when a solid sheet is still represented by internal faces/faceZones."
            if open_topology
            else "Closed external-fluid topology has no internal cavity wall."
        ),
    }
    time_step_assessment = build_timestep_assessment(
        chord_m=cfg.chord_m,
        velocity_m_s=cfg.velocity_m_s,
        topology=cfg.geometry_topology,
        time_step_mode=cfg.time_step_mode,
        delta_t_star=cfg.deltaT_star,
        max_delta_t_star=cfg.maxDeltaT_star,
        max_co=cfg.maxCo,
        end_time_star=cfg.endTime_star,
        average_from_fraction=cfg.average_from_fraction,
        temporal_config=cfg.temporal_accuracy,
    )
    return {
        "case_type": "2D extruded OpenFOAM case for ram-air/reference airfoil debugging",
        "solver": cfg.solver,
        "solver_module": cfg.solver_module,
        "flow_model": "incompressible transient RANS/PIMPLE",
        "time_integration": (
            f"transient PIMPLE with {cfg.ddt_scheme} ddtSchemes and "
            + (
                "adaptive time step limited by maxCo/maxDeltaT"
                if cfg.time_step_mode != "fixed"
                else "fixed physical time step"
            )
        ),
        "ddt_scheme": cfg.ddt_scheme,
        "time_step_mode": cfg.time_step_mode,
        "pimple_correctors": {
            "outer": cfg.n_outer_correctors,
            "pressure_velocity": cfg.n_correctors,
            "non_orthogonal": cfg.n_non_orthogonal_correctors,
        },
        "outer_corrector_residual_control": cfg.outer_corrector_residual_control,
        "transport_correction": {
            "transportCorrectionFinal": cfg.transport_correction_final,
            "effective_behavior": (
                "transport/turbulence corrected only on the final outer corrector"
                if cfg.transport_correction_final
                else "transport/turbulence corrected on every outer corrector"
            ),
            "openfoam14_source_keyword_verified": True,
        },
        "validation_fixed_subiterations": cfg.validation_fixed_subiterations,
        "open_airfoil_wall_topology_audit": wall_topology_audit,
        "optional_steady_initialization": {
            "enabled_by_default": cfg.steady_initialization_enabled,
            "algorithm": "SIMPLE",
            "ddt_scheme": "steadyState",
            "maximum_iterations": cfg.steady_max_iterations,
            "residual_control": {
                "p": cfg.steady_residual_p,
                "U": cfg.steady_residual_U,
                "nuTilda": cfg.steady_residual_nuTilda,
            },
            "numerics_profile": "balanced_sa_initialization_v3",
            "n_non_orthogonal_correctors": cfg.steady_n_non_orthogonal_correctors,
            "relaxation_factors": {
                "p": cfg.steady_relaxation_p,
                "U": cfg.steady_relaxation_U,
                "nuTilda": cfg.steady_relaxation_nuTilda,
            },
            "nuTilda_convection": "bounded Gauss upwind",
            "limited_gradients": ["U", "nuTilda"],
            "template_directory": "system/steadyInitialization",
            "physics_note": "Steady RANS is an initialization stage only; the aerodynamic result remains the transient PIMPLE stage.",
        },
        "turbulence_model": cfg.turbulence_model,
        "farfield_boundary_condition": cfg.farfield_boundary_condition,
        "reynolds": cfg.reynolds,
        "mach_input": cfg.mach_input,
        "mach_from_velocity": cfg.mach_from_velocity,
        "mach_reynolds_consistency_warning": cfg.mach_reynolds_consistency_warning,
        "velocity_source": cfg.velocity_source,
        "velocity_from_reynolds_m_s": cfg.velocity_from_reynolds_m_s,
        "velocity_from_mach_m_s": cfg.velocity_from_mach_m_s,
        "reynolds_from_velocity": cfg.reynolds_from_velocity,
        "required_mu_for_mach_and_re": cfg.required_mu_for_mach_and_re,
        "required_rho_for_mach_and_re": cfg.required_rho_for_mach_and_re,
        "rho_kg_m3": cfg.rho,
        "mu_Pa_s": cfg.mu,
        "nu_m2_s": cfg.nu,
        "pressure_ref_pa": cfg.pressure_ref_pa,
        "temperature_K": cfg.temperature_K,
        "temperature_note": "Temperature is not solved in this incompressible case; it is only metadata if provided.",
        "chord_m": cfg.chord_m,
        "alpha_deg": cfg.alpha_deg,
        "velocity_m_s": cfg.velocity_m_s,
        "velocity_components_m_s": {"Ux": Ux, "Uy": Uy, "Uz": 0.0},
        "dynamic_pressure_pa": cfg.dynamic_pressure_pa,
        "spanwise_thickness_chord": cfg.spanwise_thickness_chord,
        "spanwise_thickness_m": cfg.spanwise_thickness_m,
        "reference_area_m2": cfg.reference_area_m2,
        "pressure_field": "OpenFOAM incompressible p, kinematic pressure [m2/s2], initialized as gauge 0.",
        "deltaT_s": cfg.deltaT,
        "maxDeltaT_s": cfg.maxDeltaT,
        "endTime_s": cfg.endTime,
        "field_write_interval_s": cfg.field_write_interval if cfg.field_write_control != "timeStep" else None,
        "field_write_interval_source": (
            f"approximately_{cfg.field_write_step_equivalent}_requested_physical_steps"
            if cfg.field_write_step_equivalent > 0
            else (
                "explicit_physical_seconds"
                if cfg.field_write_interval_s is not None
                else "derived_from_convective_interval"
            )
        ),
        "field_write_control": cfg.field_write_control,
        "field_write_interval_steps": cfg.field_write_interval_steps,
        "effective_field_write_interval_steps": (
            cfg.field_write_step_equivalent if cfg.field_write_control == "timeStep" else None
        ),
        "field_write_step_equivalent": cfg.field_write_step_equivalent,
        "deltaT_star": cfg.deltaT_star,
        "maxDeltaT_star": cfg.maxDeltaT_star,
        "endTime_star": cfg.endTime_star,
        "field_write_interval_star": cfg.field_write_interval_star,
        "average_from_fraction": cfg.average_from_fraction,
        "maxCo": cfg.maxCo,
        "time_step_assessment": time_step_assessment,
        "purgeWrite": cfg.purgeWrite,
        "estimated_storage": {
            "mesh_cell_count": cell_count or None,
            "estimated_compressed_snapshot_MB": estimated_snapshot_mb,
            "estimated_max_retained_snapshot_MB": (
                estimated_snapshot_mb * cfg.purgeWrite
                if estimated_snapshot_mb is not None and cfg.purgeWrite > 0
                else None
            ),
            "retained_convective_time_star": retained_convective_time,
            "note": "Planning estimate using 130 bytes/cell; inspect actual time-directory sizes after the first writes.",
        },
        "derived_fields_at_each_volume_write": ["Cp", "Co", "yPlus", "wallShearStress", "vorticity"],
        "scalar_histories_each_iteration": ["forceCoeffs", "residuals", "Courant/deltaT in solver log"],
        "purge_write_scope": "volume fields only; function-object scalar histories are retained",
        "physical_input_ownership": {
            "reynolds_mach_fluid": "CFD_2D/CFD_2D_inputs/case_package/physical_config.json",
            "chord": "selected CFD Case variant manifest",
            "solver_override_allowed": False,
        },
        "mesh_root": str(mesh_root),
        "converted_polyMesh_source": str(poly_src) if poly_src else None,
        "patches": [{**p, "role": patch_role(p["name"], p["type"])} for p in patches],
        "fields": ["U", "p", "nuTilda", "nut"],
        "force_coefficients": {
            "enabled": True,
            "patches": force_patches,
            "patch_semantics": (
                "All wall patches are integrated as one rigid aerodynamic body. "
                "For the open airfoil this includes external skin, internal skin, lips and trailing edge; "
                "the fluid inlet bridge is not a wall patch."
            ),
            "rho": "rhoInf",
            "rhoInf": cfg.rho,
            "magUInf": cfg.velocity_m_s,
            "lRef": cfg.chord_m,
            "Aref": cfg.reference_area_m2,
            "CofR_x_c": cfg.CofR_x_c,
        },
    }


def time_integration_description(cfg: OpenFOAMCaseConfig) -> str:
    if cfg.time_step_mode == "fixed":
        return (
            f"- Time integration: transient PIMPLE, {cfg.ddt_scheme} ddt; fixed deltaT. "
            "OpenFOAM does not adjust the step from Courant, so the resulting Co must be monitored."
        )
    return (
        f"- Time integration: transient PIMPLE, {cfg.ddt_scheme} ddt; "
        "OpenFOAM may reduce/increase deltaT to satisfy maxCo but not above maxDeltaT."
    )


def write_case_input_summary(case_dir: Path, cfg: OpenFOAMCaseConfig, mesh_root: Path, poly_src: Path | None, patches: list[dict[str, str]]) -> None:
    data = case_input_summary(cfg, mesh_root, poly_src, patches)
    write_json(case_dir / "case_input_summary.json", data)
    time_step_assessment = data["time_step_assessment"]
    write_json(case_dir / "time_step_assessment.json", time_step_assessment)
    (case_dir / "time_step_assessment.md").write_text(
        assessment_markdown(time_step_assessment),
        encoding="utf-8",
    )
    lines = [
        "Ram-air CFD 2D OpenFOAM Case Input Summary",
        "==========================================",
        "",
        f"Case type: {data['case_type']}",
        f"Solver: {cfg.solver} / module: {cfg.solver_module}",
        f"Flow model: {data['flow_model']}",
        f"Turbulence model: {cfg.turbulence_model}",
        "",
        "Freestream and fluid:",
        f"- Reynolds: {cfg.reynolds:.6g}",
        f"- Alpha: {cfg.alpha_deg:.6g} deg",
        f"- Chord: {cfg.chord_m:.6g} m",
        f"- Velocity magnitude: {cfg.velocity_m_s:.6g} m/s",
        f"- Velocity source: {cfg.velocity_source}",
        (
            f"- Velocity from Reynolds: {cfg.velocity_from_reynolds_m_s:.6g} m/s"
            if cfg.velocity_from_reynolds_m_s is not None
            else "- Velocity from Reynolds: not available"
        ),
        f"- Velocity from Mach: {cfg.velocity_from_mach_m_s if cfg.velocity_from_mach_m_s is not None else 'not provided'} m/s",
        f"- Reynolds from selected velocity: {cfg.reynolds_from_velocity:.6g}",
        f"- Velocity components: Ux={data['velocity_components_m_s']['Ux']:.6g}, Uy={data['velocity_components_m_s']['Uy']:.6g}, Uz=0 m/s",
        f"- Density rho: {cfg.rho:.6g} kg/m3",
        f"- Dynamic viscosity mu: {cfg.mu:.6g} Pa.s",
        f"- Kinematic viscosity nu: {cfg.nu:.6g} m2/s",
        f"- Dynamic pressure q: {cfg.dynamic_pressure_pa:.6g} Pa",
        f"- Mach input: {cfg.mach_input if cfg.mach_input is not None else 'not provided'}",
        f"- Mach from velocity/a: {cfg.mach_from_velocity:.6g}",
        f"- Mach/Re consistency warning: {cfg.mach_reynolds_consistency_warning or 'none'}",
        f"- Required mu to satisfy configured Mach and Reynolds with current rho/chord: {cfg.required_mu_for_mach_and_re if cfg.required_mu_for_mach_and_re is not None else 'n/a'}",
        f"- Required rho to satisfy configured Mach and Reynolds with current mu/chord: {cfg.required_rho_for_mach_and_re if cfg.required_rho_for_mach_and_re is not None else 'n/a'}",
        f"- Reference pressure: {cfg.pressure_ref_pa:.6g} Pa",
        f"- Temperature: {cfg.temperature_K if cfg.temperature_K is not None else 'not solved / not provided'}",
        "",
        "OpenFOAM fields and pressure convention:",
        "- U: velocity [m/s]",
        "- p: incompressible kinematic pressure [m2/s2], initialized as gauge 0",
        "- nuTilda/nut: Spalart-Allmaras turbulence fields",
        "",
        "Time controls:",
        f"- time_step_mode: {cfg.time_step_mode}",
        f"- deltaT: {cfg.deltaT:.6g} s (deltaT*: {cfg.deltaT_star:.6g})",
        (
            f"- maxDeltaT: {cfg.maxDeltaT:.6g} s (maxDeltaT*: {cfg.maxDeltaT_star:.6g})"
            if cfg.time_step_mode != "fixed"
            else "- maxDeltaT/maxCo: not applied in fixed mode"
        ),
        f"- endTime: {cfg.endTime:.6g} s (endTime*: {cfg.endTime_star:.6g})",
        (
            f"- Volume-field writes: every {cfg.field_write_step_equivalent} time steps"
            if cfg.field_write_control == "timeStep"
            else f"- Volume-field writes: every {cfg.field_write_interval:.6g} s "
                 f"(approximately {cfg.field_write_step_equivalent} requested physical steps)"
        ),
        (
            f"- maxCo: {cfg.maxCo:.6g}"
            if cfg.time_step_mode != "fixed"
            else "- maxCo: monitored but not used to change deltaT"
        ),
        time_integration_description(cfg),
        "- The volume-field write interval is expressed in physical simulated seconds. adjustableRunTime aligns adaptive time steps to those physical output instants.",
        f"- purgeWrite: {cfg.purgeWrite}",
        (
            "- Fastest selected physical frequency: "
            f"St={time_step_assessment['frequency_resolution']['target_strouhal_range']['maximum']:.6g}; "
            f"engineering deltaT* ceiling="
            f"{time_step_assessment['frequency_resolution']['engineering_deltaT_star_ceiling']:.6g}"
        ),
        (
            "- Configured samples per fastest selected cycle: "
            f"{time_step_assessment['frequency_resolution']['configured_initial_samples_per_fastest_cycle']:.6g}"
        ),
        "- Full derivation and warnings: time_step_assessment.md/json",
        f"- Retained convective window t*: {data['estimated_storage']['retained_convective_time_star'] if data['estimated_storage']['retained_convective_time_star'] is not None else 'unbounded'}",
        f"- Estimated compressed snapshot: {data['estimated_storage']['estimated_compressed_snapshot_MB'] if data['estimated_storage']['estimated_compressed_snapshot_MB'] is not None else 'unknown'} MB",
        f"- Estimated maximum retained snapshots: {data['estimated_storage']['estimated_max_retained_snapshot_MB'] if data['estimated_storage']['estimated_max_retained_snapshot_MB'] is not None else 'unbounded/unknown'} MB",
        "",
        "Optional steady initialization:",
        f"- Enabled by default: {cfg.steady_initialization_enabled}",
        f"- SIMPLE maximum iterations: {cfg.steady_max_iterations}",
        (
            "- residualControl: "
            f"p={format_optional(cfg.steady_residual_p, '.3g')}, "
            f"U={format_optional(cfg.steady_residual_U, '.3g')}, "
            f"nuTilda={format_optional(cfg.steady_residual_nuTilda, '.3g')}"
        ),
        "- The staged runner archives this stage and transfers its reconstructed fields to transient 0/ only after explicit transition checks.",
        "",
        "Mesh:",
        f"- mesh_root: {mesh_root}",
        f"- converted_polyMesh_source: {poly_src if poly_src else 'not available'}",
        f"- spanwise_thickness_chord: {cfg.spanwise_thickness_chord:.6g}",
        f"- spanwise_thickness_m: {cfg.spanwise_thickness_m:.6g} m",
        f"- forceCoeffs Aref: {cfg.reference_area_m2:.6g} m2",
        f"- forceCoeffs integrated wall patches: {', '.join(data['force_coefficients']['patches']) or 'none'}",
        "",
        "Boundary patches:",
    ]
    for p in data["patches"]:
        lines.append(f"- {p['name']}: OpenFOAM type={p['type']}, role={p['role']}")
    lines.extend([
        "",
        "Post-processing intent:",
        "- forceCoeffs is enabled in controlDict for Cl/Cd/Cm histories.",
        "- residuals are written every iteration; yPlus, wallShearStress and vorticity are written with each volume snapshot.",
        "- Use ramair_2d_postprocess.py for residual plots, coefficient plots, VTK export and ParaView commands.",
    ])
    (case_dir / "case_input_summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_case(
    case_root: Path,
    variant: str,
    alpha: float,
    reynolds: float | None = None,
    require_approval: bool = True,
    overwrite: bool = False,
    require_converted_polymesh: bool = False,
    existing_case_action: str = "archive",
) -> Path:
    mesh_root = cfd_meshes_root(case_root) / variant
    if require_approval and not (mesh_root / "MESH_APPROVED.flag").exists():
        raise RuntimeError(f"Mesh approval required but missing: {mesh_root / 'MESH_APPROVED.flag'}")
    mesh_file = mesh_root / "mesh_final.msh"
    if require_approval and not mesh_file.exists():
        raise RuntimeError(f"Approved mesh file is missing: {mesh_file}")
    poly_src = find_converted_polymesh(mesh_root)
    if require_converted_polymesh and poly_src is None:
        raise RuntimeError(f"Converted OpenFOAM polyMesh required but not found under: {mesh_root}. Run mesh_builder with --check-mesh first.")
    if poly_src is not None:
        reject_forbidden_boundary(poly_src)
    boundary_patches = parse_boundary_patches(poly_src)
    cfg = load_physical(case_root, alpha, reynolds, variant)
    safe_alpha = f"alpha_{alpha:+.3f}".replace("+", "p").replace("-", "m").replace(".", "p")
    case_dir = cfd_cases_root(case_root) / variant / safe_alpha
    if case_dir.exists() and overwrite:
        backup = prepare_existing_case_dir(
            case_root,
            case_dir,
            variant,
            safe_alpha,
            existing_case_action,
        )
        if backup is not None:
            print(f"Previous OpenFOAM case moved to backup: {backup.resolve()}")
    case_dir.mkdir(parents=True, exist_ok=True)
    if mesh_file.exists():
        gmsh_dir = case_dir / "constant" / "gmsh"
        gmsh_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(mesh_file, gmsh_dir / "mesh_final.msh")
    mesh_status = "MESH_NOT_CONVERTED"
    if poly_src is not None:
        dst_poly = case_dir / "constant" / "polyMesh"
        if dst_poly.exists():
            shutil.rmtree(dst_poly)
        dst_poly.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(poly_src, dst_poly)
        mesh_status = "CONVERTED_POLYMESH_COPIED"
    write_0(case_dir, cfg, boundary_patches)
    write_constant(case_dir, cfg)
    write_system(case_dir, cfg, boundary_patches)
    applied_config = asdict(cfg) | {
        "variant": variant,
        "alpha_deg": alpha,
        "mesh_root": str(mesh_root),
        "mesh_status": mesh_status,
        "converted_polyMesh_source": str(poly_src) if poly_src else None,
        "boundary_patches": boundary_patches,
    }
    write_json(case_dir / "case_config.json", applied_config)
    write_json(case_dir / "applied_solver_configuration.json", {
        "schema_version": 1,
        "solver_config_schema_version": cfg.config_schema_version,
        "physical_source": "CFD Case (physical_config.json + selected variant manifest)",
        "physical_override_allowed": False,
        "effective_configuration": applied_config,
        "transportCorrectionFinal_semantics": (
            "final outer corrector only"
            if cfg.transport_correction_final
            else "every outer corrector"
        ),
        "field_write_equivalence_steps": cfg.field_write_step_equivalent,
        "scalar_histories_retained_independently_of_purgeWrite": True,
    })
    write_case_input_summary(case_dir, cfg, mesh_root, poly_src, boundary_patches)
    if poly_src is None:
        readme_mesh = "No `constant/polyMesh` folder was created because no converted mesh was found. Run `ramair_2d_mesh_builder.py --write-openfoam-mesh --check-mesh` on a system with OpenFOAM tools, then rewrite the case or pass `--require-converted-polymesh` to enforce this."
    else:
        readme_mesh = "`constant/polyMesh` was copied from the checked mesh output. Review the boundary file before running."
    (case_dir / "README_case.md").write_text(f"# OpenFOAM case\n\nThis case has not been executed. {readme_mesh}\n\nUse ramair_2d_openfoam_runner.py with --run to execute explicitly.\n", encoding="utf-8")
    return case_dir


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Write OpenFOAM case folders for approved ram-air 2D meshes; do not run solver.")
    p.add_argument(
        "--case-root",
        type=Path,
        required=True,
        help="Project root (recommended). CFD_2D/, CFD_2D/openfoam_cases/ and CFD_2D/meshes/ are also accepted.",
    )
    p.add_argument("--variant", required=True)
    p.add_argument("--alpha", type=float, default=None, help="Write one angle of attack.")
    p.add_argument("--alphas", type=float, nargs="+", default=None, help="Write several angle cases sequentially; no solver is executed.")
    p.add_argument(
        "--reynolds",
        type=float,
        default=None,
        help="Deprecated compatibility check only; CFD Case physical_config.json is authoritative.",
    )
    p.add_argument("--mesh-approved-required", action="store_true", default=True)
    p.add_argument("--no-mesh-approved-required", action="store_false", dest="mesh_approved_required")
    p.add_argument("--write-case", action="store_true")
    p.add_argument("--overwrite", action="store_true")
    p.add_argument(
        "--existing-case-action",
        choices=["archive", "delete", "keep"],
        default="archive",
        help="Policy applied by the writer when --overwrite finds an active case.",
    )
    p.add_argument("--require-converted-polymesh", action="store_true", help="Fail unless mesh_builder produced a real constant/polyMesh with gmshToFoam/checkMesh.")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    if not args.write_case:
        print("Dry planning mode: add --write-case to create 0/, constant/ and system/.")
        return
    alphas = list(args.alphas or ([] if args.alpha is None else [args.alpha]))
    if not alphas:
        raise ValueError("Provide --alpha or --alphas.")
    for alpha in alphas:
        path = write_case(
            args.case_root,
            args.variant,
            alpha,
            args.reynolds,
            args.mesh_approved_required,
            args.overwrite,
            args.require_converted_polymesh,
            args.existing_case_action,
        )
        print(f"Wrote OpenFOAM case: {path.resolve()}")


if __name__ == "__main__":
    main()
