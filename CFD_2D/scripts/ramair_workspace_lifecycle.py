"""Explicit lifecycle operations and scientific review adapters for workspaces."""
from __future__ import annotations
import shutil
import time
from pathlib import Path
import pandas as pd
from ramair_workspace import read_json, write_json, alpha_name, prepare_solver_cases, comparable_setups


def reset_angles(root: Path, setup: Path, angles: list[float], phase: str) -> list[str]:
    if phase not in {"RANS", "URANS", "RANS y URANS"}:
        raise ValueError("Fase no reconocida")
    from ramair_execution_control import pid_is_alive
    cases = [setup / "Angles" / alpha_name(a) for a in angles]
    for case in cases:
        if case.resolve().parent != (setup / "Angles").resolve() or not (case / "case_config.json").is_file():
            raise ValueError("El caso no pertenece a esta configuración.")
        process = read_json(case / ".ramair_solver_process.json")
        if process.get("pid") and pid_is_alive(int(process["pid"])):
            raise ValueError("Detén la ejecución antes de reiniciar este ángulo.")
        if phase == "URANS" and not any((case / "steadyInitialization").glob("history/*/time_directories/*/U*")):
            raise ValueError("No existe un estado RANS conservado para reiniciar solo URANS.")
    backups = []
    for case in cases:
        backup = setup / "Archive" / f"{case.name}_{time.time_ns()}"
        backup.parent.mkdir(exist_ok=True)
        case.rename(backup)
        try:
            prepare_solver_cases(root, setup)
            if phase == "URANS" and (backup / "steadyInitialization").is_dir():
                shutil.copytree(backup / "steadyInitialization", case / "steadyInitialization")
                histories = sorted((case / "steadyInitialization").glob("history/*/time_directories"))
                times = [p for p in histories[-1].iterdir() if p.is_dir() and p.name.replace(".", "", 1).isdigit()]
                latest = max(times, key=lambda p: float(p.name))
                for field in latest.iterdir():
                    if field.is_file():
                        shutil.copy2(field, case / "0" / field.name)
            write_json(case / "reset_manifest.json", {"phase": phase, "archived_case": str(backup), "data_deleted": False,
                "rans_initialization_preserved": phase == "URANS",
                "note": "URANS evidence is retained in Archive when its upstream RANS is reset."})
        except Exception:
            # The previous case remains recoverable even if regeneration fails.
            raise RuntimeError(f"Reconstrucción fallida; datos conservados en {backup}")
        backups.append(str(backup))
    return backups


def review_angles(setup: Path, angles: list[float], phase: str) -> list[dict]:
    from openfoam_history import read_force_coefficient_history
    from ramair_2d_postprocess import parse_solver_log
    from ramair_2d_rans_plateau_gate import evaluate_rans_gate
    from ramair_2d_urans_review import review_urans_signals
    solver = read_json(setup / "solver_config.json")
    physical = read_json(setup / "solver_setup.json")["context"]["physical"]
    plan = read_json(setup / "phase_plan.json")
    results = []
    for alpha in angles:
        case = setup / "Angles" / alpha_name(alpha)
        target = case
        if phase == "RANS":
            candidates = sorted((case / "steadyInitialization").glob("**/postProcessing"))
            if candidates:
                target = candidates[-1].parent
        try:
            frame = read_force_coefficient_history(target)
            if isinstance(frame, tuple):
                frame = frame[0]
            frame = pd.DataFrame(frame)
            if frame.empty:
                raise ValueError("Sin historia de fuerzas real para revisar.")
            if phase == "RANS":
                residuals, _, _ = parse_solver_log(target)
                report = evaluate_rans_gate({"iterations_completed": float(frame.Time.max())}, frame, residuals,
                    rans_config={"minimum_simple_iterations_before_convergence_check": 0},
                    convergence_config=solver.get("validation_rans_convergence", {}))
            else:
                report = review_urans_signals(frame,
                    sampling_start_s=plan["production_start_time_star"]/physical["velocity_m_s"],
                    chord_m=1., velocity_m_s=physical["velocity_m_s"], reference_length_m=1.)
            report.update(alpha=alpha, phase=phase)
            def finite_values(value):
                import math
                if isinstance(value, float) and not math.isfinite(value):
                    return None
                if isinstance(value, dict):
                    return {key: finite_values(item) for key, item in value.items()}
                if isinstance(value, (list, tuple)):
                    return [finite_values(item) for item in value]
                return value
            report = finite_values(report)
            write_json(case / "Postprocess/Reviews" / f"{phase}.json", report)
        except (ValueError, KeyError, OSError) as exc:
            report = {"alpha": alpha, "phase": phase, "status": "REVIEW_UNAVAILABLE", "reason": str(exc)}
        results.append(report)
    return results


def wall_comparison(first: Path, second: Path, alpha: float, stage: str = "URANS") -> dict[str, pd.DataFrame]:
    if not comparable_setups(first, second)[0]:
        raise ValueError("Condiciones CFD incompatibles.")
    result = {}
    aliases = {"Cp": ("cp", "pressure_coefficient"), "Cf": ("cf", "cf_raw", "skin_friction_coefficient"), "y+": ("yplus", "y_plus", "y+")}
    for metric, columns in aliases.items():
        rows = []
        for tag, setup in (("A", first), ("B", second)):
            folder = setup / "Angles" / alpha_name(alpha) / "Postprocess"
            if (folder / stage).is_dir():
                folder = folder / stage
            elif stage == "RANS":
                continue
            names={"Cp":"wall_cp_vs_xc.csv","Cf":"skin_friction_coefficient_vs_xc.csv","y+":"wall_yplus_vs_xc.csv"}
            candidates=list(folder.glob("**/"+names[metric]))
            if metric=="Cf" and not candidates:candidates=list(folder.glob("**/wall_separation_data.csv"))
            for path in candidates:
                if stage == "URANS" and "RANS" in path.relative_to(folder).parts:
                    continue
                try:
                    frame = pd.read_csv(path)
                    lookup = {c.lower(): c for c in frame}
                    x = next((lookup[k] for k in ("x/c", "x_over_c", "x_norm", "x_chord", "x_m", "x") if k in lookup), None)
                    value = next((lookup[k] for k in columns if k in lookup), None)
                    if x and value:
                        if "surface" not in frame or "wall_side" not in frame:continue
                        patch=frame["wall_side"].astype(str)+" · "+frame["surface"].astype(str)
                        rows.append(pd.DataFrame({"x/c": frame[x], "value": frame[value], "series": tag+" · "+patch}))
                except (OSError, ValueError):
                    continue
        result[metric] = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
    return result


def releasable_paths(setup: Path) -> list[Path]:
    from ramair_execution_control import pid_is_alive
    for status in (setup / "Queues").glob("*.status.json"):
        if read_json(status).get("status") == "RUNNING":
            raise ValueError("Pausa la cola antes de liberar datos de cálculo.")
    paths = []
    for case in (setup / "Angles").iterdir():
        process = read_json(case / ".ramair_solver_process.json")
        if process.get("pid") and pid_is_alive(int(process["pid"])):
            raise ValueError("Hay un solver activo en esta configuración.")
        for path in case.iterdir():
            try:
                is_time = 0 < float(path.name) < float("inf")
            except ValueError:
                is_time = False
            is_processor = path.name.startswith("processor") and path.name[9:].isdigit()
            if path.is_dir() and (path.name in {"postProcessing", "Postprocess", "steadyInitialization", "VTK"} or is_processor or is_time):
                if not path.resolve().is_relative_to((setup / "Angles").resolve()) or path.is_symlink():
                    raise ValueError("La ruta de cálculo sale de este caso.")
                paths.append(path)
    return paths


def release_runtime_data(setup: Path, export_manifest: Path) -> dict:
    """Explicit user-selected release, only after a content-verified export."""
    from ramair_workspace import file_digest
    manifest = read_json(export_manifest)
    if manifest.get("source") != str(setup) or not manifest.get("files"):
        raise ValueError("Exporta y verifica primero los productos que deseas conservar.")
    for relative in manifest["files"]:
        source, target = setup / relative, export_manifest.parent / relative
        if not target.is_file() or not source.is_file() or file_digest(source) != file_digest(target):
            raise ValueError("La copia exportada ha cambiado o está incompleta.")
    paths = releasable_paths(setup)
    # The preview and this execution use the same bounded ownership rule.
    for path in paths:
        shutil.rmtree(path)
    result = {"source": str(setup), "verified_export": str(export_manifest), "removed_generated_directories": [str(p) for p in paths], "configuration_and_approved_mesh_preserved": True}
    write_json(setup / "data_release_manifest.json", result)
    return result
