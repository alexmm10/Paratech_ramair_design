"""Persistent general-workspace RANS mesh studies. No extrapolation or solver mutation."""
from __future__ import annotations
import copy
import math
import os
import time
from pathlib import Path
import numpy as np
import pandas as pd
from ramair_workspace import (read_json, write_json, digest, file_digest, slug, alpha_name,
    geometry_context, compatible_mesh, mesh_evidence, create_solver_setup, prepare_solver_cases)


def solver_signature(config):
    # Operational limits may differ; every physical/numerical input is retained.
    return digest({k: v for k, v in config.items() if k not in {"execution_controls", "workflow_test"}})


def compatible_setup(setup, geometry, solver):
    manifest = read_json(setup/"solver_setup.json")
    reasons = []
    if manifest.get("context") != geometry_context(geometry): reasons.append("geometría/revisión/condiciones/referencias")
    if solver_signature(read_json(setup/"solver_config.json")) != solver_signature(solver): reasons.append("ajustes físicos, turbulentos o numéricos")
    mesh = setup.parent.parent
    ok, reason = compatible_mesh(mesh, geometry)
    if not ok: reasons.append(reason)
    return not reasons, "; ".join(reasons) or "Compatible"


def create_study(root, geometry, meshes, reference_setup, angles, name):
    from ramair_workspace import parse_angles
    angles = parse_angles("list", " ".join(map(str, angles)))
    meshes = [Path(m).resolve() for m in meshes]
    geometry = Path(geometry).resolve()
    if len(set(meshes)) < 2: raise ValueError("Selecciona al menos dos mallas diferentes.")
    solver = read_json(reference_setup/"solver_config.json")
    if not solver: raise ValueError("Falta la configuración de referencia.")
    ok, reason = compatible_setup(reference_setup, geometry, solver)
    if not ok: raise ValueError("Referencia incompatible: "+reason)
    for mesh in meshes:
        if mesh.parent.parent != geometry: raise ValueError("Todas las mallas deben pertenecer a la misma geometría.")
        ok, reason = compatible_mesh(mesh, geometry)
        if not ok: raise ValueError(mesh.name+": "+reason)
    folder = geometry/"Convergence/RANS"/slug(name)
    folder.mkdir(parents=True, exist_ok=False)
    write_json(folder/"solver_reference.json", solver)
    write_json(folder/"phase_plan_reference.json", read_json(reference_setup/"phase_plan.json"))
    entries = []
    for mesh in meshes:
        candidates = sorted(p.parent for p in (mesh/"OpenFOAM").glob("*/solver_setup.json"))
        matching = [p for p in candidates if compatible_setup(p, geometry, solver)[0]
                    and bool(read_json(p/"solver_config.json").get("workflow_test")) == bool(solver.get("workflow_test"))]
        missing = [a for a in angles if not any(a in read_json(p/"solver_setup.json").get("angles", []) for p in matching)]
        if missing:
            copied = copy.deepcopy(solver)
            copied.setdefault("execution_controls", {}).update(phase_action="rans_only", renumber_before_decompose=False)
            new = create_solver_setup(root, mesh, "RANS_"+folder.name+"_"+str(time.time_ns()), copied,
                read_json(reference_setup/"phase_plan.json"), missing)
            matching.append(new)
        for a in angles:
            options = [p for p in matching if a in read_json(p/"solver_setup.json").get("angles", [])]
            # Prefer a source with existing results; never copy its fields into another mesh.
            options.sort(key=lambda p: (not (p/"Angles"/alpha_name(a)/"Postprocess/RANS").exists(), str(p)))
            setup = options[0]
            entries.append({"mesh": mesh.name, "alpha": a, "setup": str(setup.relative_to(geometry)),
                "mesh_evidence": mesh_evidence(mesh), "solver_signature": solver_signature(solver)})
    write_json(folder/"study.json", {"schema_version": 1, "name": name, "context": geometry_context(geometry),
        "angles": angles, "entries": entries, "reference_mesh": None, "execution_mode": "rans_only"})
    return folder


def locations(folder, entry):
    geometry = folder.parents[2]
    setup = geometry/entry["setup"]
    return geometry, setup, setup/"Angles"/alpha_name(entry["alpha"])


def entry_state(folder, entry):
    from ramair_simulation_inventory import case_progress
    from ramair_execution_control import load_solver_process, pid_is_alive
    geometry, setup, case = locations(folder, entry)
    mesh = setup.parent.parent
    report = read_json(mesh/"mesh_report.json")
    row = {"mesh": entry["mesh"], "alpha": entry["alpha"], "state": "pendiente", "reason": "",
        "cells": report.get("openfoam_cells") or report.get("cell_count") or report.get("n_cells") or report.get("total_2d_cells") or report.get("boundary_layer_mesh_audit", {}).get("cell_counts_2d", {}).get("total"),
        "quality": report.get("checkMesh_status"), "setup": str(setup), "case": str(case)}
    try:
        ok, reason = compatible_setup(setup, geometry, read_json(folder/"solver_reference.json"))
        if not ok or mesh_evidence(mesh) != entry["mesh_evidence"]:
            return dict(row, state="obsoleto", reason=reason if not ok else "Cambió el contenido de la malla")
    except (OSError, ValueError) as exc:
        return dict(row, state="incompatible", reason=str(exc))
    process = load_solver_process(case)
    if process and pid_is_alive(process.get("pid"), process.get("pid_start_token")):
        return dict(row, state="ejecutándose")
    if (case/"case_config.json").is_file(): row["state"] = "disponible"
    if (case/"steadyInitialization/pending_stage.json").is_file(): row["state"] = "pausado"
    run_status = str(read_json(case/"staged_run_status.json").get("status", ""))
    if any(flag in run_status.upper() for flag in ("DIVERGED", "FAILED", "ERROR")):
        return dict(row, state="fallido", reason=run_status)
    for path in (case/"Postprocess/RANS/case_summary.json", case/"Postprocess/RANS/stage_summary.json", case/"Postprocess/case_summary.json"):
        summary = read_json(path)
        candidate = summary if path.parent.name == "RANS" else summary.get("rans_stage", {})
        values = candidate.get("force_summary", candidate)
        means = {k.lower(): v for k, v in values.get("mean", {}).items()}
        if not all(k in means for k in ("cl", "cd", "cm")): continue
        if not all(math.isfinite(float(means[k])) for k in ("cl", "cd", "cm")): continue
        status = str(summary.get("status", "")) + str(values.get("status", ""))
        if any(s in status.upper() for s in ("FAILED", "DIVERGED", "NO_RESULTS", "ERROR")):
            return dict(row, state="fallido", reason=status)
        row.update(state="postprocesado", Cl=float(means["cl"]), Cd=float(means["cd"]), Cm=float(means["cm"]),
            source=str(path), source_sha256=file_digest(path), scientific_accepted=False)
        row["executed_iteration"] = values.get("averaging_end_iteration")
        provenance = read_json(case/"Postprocess/input_evidence.json")
        if provenance and any(not (case/relative).is_file() or file_digest(case/relative) != checksum
                              for relative, checksum in provenance.get("rans_force_files", {}).items()):
            return dict(row, state="obsoleto", reason="El solver cambió la historia RANS; vuelve a postprocesar.")
        row["Cl/Cd"] = row["Cl"]/row["Cd"] if abs(row["Cd"]) > 1e-10 else None
        accepted = read_json(setup/"Polar/accepted_points.json", {"points": []}).get("points", [])
        curated = any(p.get("alpha") == entry["alpha"] and p.get("stage") == "RANS" and p.get("source_sha256") == row["source_sha256"] for p in accepted)
        # Manual curation alone is insufficient evidence for mesh independence.
        iterative = case_progress(case).get("rans_stable") is True
        diagnostic = bool(read_json(setup/"solver_config.json").get("workflow_test")) or "PARTIAL" in status.upper()
        row.update(state="revisado" if curated and not diagnostic else "postprocesado", diagnostic=diagnostic,
                   scientific_accepted=bool(curated and iterative and not diagnostic))
        break
    progress = case_progress(case)
    row["iteration"] = progress.get("rans_iteration", 0)
    return row


def matrix(folder):
    return pd.DataFrame([entry_state(folder, entry) for entry in read_json(folder/"study.json")["entries"]])


def prepare_queue(root, folder, cores=1, concurrent=1, run=False, options=None, *, selected=None, continue_processed=False):
    from ramair_workspace_jobs import execution_command
    budget = min(8, os.cpu_count() or 1)
    if not 1 <= cores <= budget or not 1 <= concurrent <= 8: raise ValueError("Recursos fuera de los límites del gestor.")
    controls = {**read_json(folder/"solver_reference.json").get("execution_controls", {}), **(options or {}),
                "phase_action": "rans_only", "renumber_before_decompose": False}
    cases = []
    for entry in read_json(folder/"study.json")["entries"]:
        if selected is not None and (entry["mesh"], entry["alpha"]) not in selected: continue
        row = entry_state(folder, entry)
        if row["state"] in {"obsoleto", "incompatible", "ejecutándose"}: raise ValueError(row["state"]+": "+row["mesh"]+" "+row["reason"])
        if row["state"] in {"revisado", "postprocesado"} and not continue_processed: continue
        _, setup, case = locations(folder, entry)
        prepare_solver_cases(root, setup)
        cases.append({"case_id": entry["mesh"]+"_"+case.name, "case_path": str(case), "n_cores": cores,
            "alpha_deg": entry["alpha"], "command": execution_command(root, setup, case, cores, run, options=controls)})
    if not cases: raise ValueError("Todos los puntos seleccionados tienen resultados. Crea otro estudio/configuración para recalcularlos.")
    path = folder/"Queues"/f"queue_{time.time_ns()}.json"
    return write_json(path, {"schema_version": 1, "campaign_id": path.stem, "project_root": str(root),
        "total_core_budget": budget, "max_concurrent_cases": concurrent, "execution_controls": controls, "cases": cases})


def analyze(folder, reference=None):
    from ramair_2d_convergence_analysis import generalized_gci
    frame = matrix(folder)
    output = folder/"Analysis"
    output.mkdir(exist_ok=True)
    frame.to_csv(output/"matrix.csv", index=False)
    if "Cl" not in frame: return frame, pd.DataFrame(), {}
    data = frame[frame.state.isin(["postprocesado", "revisado"])].dropna(subset=["Cl", "Cd", "Cm"])
    if data.empty: return frame, pd.DataFrame(), {}
    counts = data.groupby("mesh").cells.first().dropna()
    reference = reference or (counts.astype(float).idxmax() if not counts.empty else data.mesh.iloc[-1])
    ref = data[data.mesh == reference]
    if ref.empty: raise ValueError("La malla de referencia no tiene resultados disponibles.")
    joined = data.merge(ref[["alpha", "Cl", "Cd", "Cm", "Cl/Cd"]], on="alpha", suffixes=("", "_reference"))
    aggregates = []
    for coefficient in ("Cl", "Cd", "Cm", "Cl/Cd"):
        joined[coefficient+"_delta"] = joined[coefficient]-joined[coefficient+"_reference"]
        denominator = joined[coefficient+"_reference"].abs()
        joined[coefficient+"_percent"] = 100*joined[coefficient+"_delta"]/denominator.where(denominator > 1e-10)
        for mesh, group in joined.groupby("mesh"):
            aggregates.append({"mesh": mesh, "coefficient": coefficient, "common_angles": len(group),
                "mean_absolute_error": group[coefficient+"_delta"].abs().mean(),
                "max_absolute_error": group[coefficient+"_delta"].abs().max()})
    joined.to_csv(output/"differences.csv", index=False)
    pd.DataFrame(aggregates).to_csv(output/"aggregates.csv", index=False)
    selected_angles = set(read_json(folder/"study.json").get("angles", frame.alpha.unique()))
    missing = [{"mesh": mesh, "alpha": float(alpha)} for mesh in frame.mesh.unique() for alpha in sorted(selected_angles)
               if not ((data.mesh == mesh) & (data.alpha == alpha)).any()]
    gci = {}
    for alpha, group in data.groupby("alpha"):
        group = group.dropna(subset=["cells"]).sort_values("cells")
        if len(group) != 3 or group.cells.nunique() != 3 or not group.scientific_accepted.all():
            gci[str(alpha)] = {"eligible": False, "reason": "Requiere tres mallas distintas, convergencia iterativa demostrada y revisión; excluye pruebas breves."}
            continue
        gci[str(alpha)] = {k: generalized_gci(coarse_value=group[k].iloc[0], medium_value=group[k].iloc[1], fine_value=group[k].iloc[2],
            coarse_cells=int(group.cells.iloc[0]), medium_cells=int(group.cells.iloc[1]), fine_cells=int(group.cells.iloc[2])) for k in ("Cl", "Cd", "Cm")}
    write_json(output/"analysis.json", {"reference_mesh": reference, "gci": gci,
        "sources": data[["mesh", "alpha", "source_sha256"]].to_dict("records"), "missing_points": missing,
        "note": "Solo ángulos comunes; denominadores casi nulos sin porcentaje. GCI no demuestra por sí solo independencia."})
    import matplotlib.pyplot as plt
    for x, coefficients, suffix in (("alpha", ("Cl", "Cd", "Cm", "Cl/Cd"), "polar"), ("cells", ("Cl", "Cd", "Cm", "Cl/Cd"), "cells")):
        for coefficient in coefficients:
            fig, ax = plt.subplots(figsize=(8, 5))
            for label, group in data.groupby("mesh" if x == "alpha" else "alpha"):
                ordered = group.sort_values(x)
                ax.plot(ordered[x], ordered[coefficient], "o-", label=str(label))
            ax.set(xlabel="α [°]" if x == "alpha" else "N celdas", ylabel=coefficient, title="Comparación RANS · verificar convergencia iterativa")
            ax.legend(); ax.grid(alpha=.2); fig.tight_layout()
            fig.savefig(output/(coefficient.replace("/", "_")+"_"+suffix+".png"), dpi=160)
            plt.close(fig)
    for coefficient in ("Cl", "Cd", "Cm", "Cl/Cd"):
        fig, ax = plt.subplots(figsize=(8, 5))
        for mesh, group in joined.groupby("mesh"):
            group = group.sort_values("alpha")
            ax.plot(group.alpha, group[coefficient+"_percent"], "o-", label=mesh)
        ax.set(xlabel="α [°]", ylabel="Diferencia [%]", title=coefficient+" · referencia "+reference)
        ax.grid(alpha=.2); ax.legend(); fig.tight_layout()
        fig.savefig(output/(coefficient.replace("/", "_")+"_errors.png"), dpi=160); plt.close(fig)
    return frame, joined, gci
