"""Plots and numerical audits of the actual geometry exported to CATIA."""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def _save(fig, folder, name):
    fig.tight_layout()
    fig.savefig(folder / (name+".png"), dpi=155)
    plt.close(fig)


def diagnostic_plots(out: Path, canopy, stabs, network, controls: dict | None = None):
    controls = controls or {}
    folder = Path(out).parent / "Outputs/Debug"
    folder.mkdir(parents=True, exist_ok=True)
    if network and controls.get("write_suspension_plot", True):
        colors = {"A": "#1f77b4", "B": "#17becf", "A_B": "#1f77b4", "C": "#2ca02c", "D": "#9467bd", "C_D": "#2ca02c", "BRK": "#d62728"}
        for ix, iy, name, title in ((1, 0, "07_suspension_plan", "Planta"), (1, 2, "08_suspension_front", "Frontal"), (0, 2, "09_suspension_side", "Lateral")):
            fig, ax = plt.subplots(figsize=(11, 7))
            labels = set()
            for segment in network.segments:
                a, b = network.nodes[segment.start_node], network.nodes[segment.end_node]
                bank = segment.bank
                ax.plot([a.p[ix], b.p[ix]], [a.p[iy], b.p[iy]], color=colors.get(bank, "#64748b"),
                    linewidth=1.1, alpha=.75, label=bank if bank not in labels else None)
                labels.add(bank)
            ax.set(xlabel="xyz"[ix]+" [mm]", ylabel="xyz"[iy]+" [mm]", title=title+" · red efectiva de suspensión")
            ax.set_aspect("equal", adjustable="datalim")
            ax.grid(alpha=.2)
            if labels: ax.legend(loc="best", ncol=3, fontsize=8)
            _save(fig, folder, name)
        selection = network.cfg.get("brake_selection_effective")
        if selection:
            (folder/"brake_selection_effective.json").write_text(json.dumps(selection, indent=2), encoding="utf-8")
            boundaries = canopy.boundary_rib_rows().sort_values("Y_flat_mm")
            fig, ax = plt.subplots(figsize=(11, 5))
            selected = {i for side in selection["sides"].values() for i in side["cells"]}
            records = list(boundaries.itertuples())
            for i, (left, right) in enumerate(zip(records, records[1:]), 1):
                y = [left.Y_flat_mm, right.Y_flat_mm, right.Y_flat_mm, left.Y_flat_mm]
                x = [left.chordwise_offset_mm, right.chordwise_offset_mm,
                     right.chordwise_offset_mm+right.chord_mm, left.chordwise_offset_mm+left.chord_mm]
                ax.fill(y, x, color="#ef4444" if i in selected else "#dbeafe", alpha=.5, edgecolor="#475569")
                ax.text((left.Y_flat_mm+right.Y_flat_mm)/2, .5*(x[0]+x[3]), str(i), ha="center", fontsize=9)
            ax.set(xlabel="y desarrollado [mm]", ylabel="x [mm]", title="Celdas completas frenadas (rojo) · planta desarrollada")
            ax.set_aspect("equal", adjustable="datalim"); ax.grid(alpha=.2)
            _save(fig, folder, "10_brake_cells")
    ribs = canopy.ribs.sort_values("Y_flat_mm")
    if controls.get("write_chord_distribution_plot", True):
        fig, axes = plt.subplots(2, 1, figsize=(10, 6))
        y = ribs.Y_flat_mm.to_numpy(float)
        x = ribs.chordwise_offset_mm.to_numpy(float)
        chord = ribs.chord_mm.to_numpy(float)
        axes[0].plot(y, x, label="Borde de ataque")
        axes[0].plot(y, x+chord, label="Borde de salida")
        axes[0].set(ylabel="x [mm]", title="Planta plana · geometría de costillas")
        axes[0].legend()
        axes[1].plot(y, chord, "o-")
        axes[1].set(xlabel="Envergadura plana y [mm]", ylabel="Cuerda [mm]")
        for ax in axes: ax.grid(alpha=.2)
        _save(fig, folder, "01_planform_chord")
    if controls.get("write_cell_span_plot", True):
        boundaries = canopy.boundary_rib_rows().sort_values("Y_flat_mm")
        widths = np.diff(boundaries.Y_flat_mm.to_numpy(float))
        centers = .5*(boundaries.Y_flat_mm.to_numpy(float)[1:]+boundaries.Y_flat_mm.to_numpy(float)[:-1])
        fig, ax = plt.subplots(figsize=(10, 3))
        ax.bar(centers, widths, width=widths*.8)
        ax.set(xlabel="Centro de celda y [mm]", ylabel="Ancho [mm]", title="Distribución de anchos de celdas")
        _save(fig, folder, "02_cell_widths")
    if controls.get("write_anhedral_plot", True):
        fig, ax = plt.subplots(figsize=(10, 4))
        for _, rib in ribs.iterrows():
            xyz = np.asarray([canopy.transform_profile_point(float(p.x_chord_norm), float(p.z_chord_norm), rib, 0.) for p in canopy.profile.itertuples()])
            ax.plot(xyz[:, 1], xyz[:, 2], alpha=.65)
        ax.set(xlabel="y global [mm]", ylabel="z global [mm]", title="Alzado real · anhedral y orientación de costillas")
        ax.axis("equal")
        ax.grid(alpha=.2)
        _save(fig, folder, "03_anhedral")
    if controls.get("write_crossports_plot", True):
        cp = Path(out)/"ramair_crossports.csv"
        fig, ax = plt.subplots(figsize=(11, 3.5))
        for branch in (canopy.upper, canopy.lower):
            ax.plot(branch.x_chord_norm, branch.z_chord_norm, color="#157a8b")
        if cp.is_file():
            frame = pd.read_csv(cp)
            for _, loop in frame.groupby("loop_id"):
                x = np.r_[loop.x_chord_norm, loop.x_chord_norm.iloc[0]]
                z = np.r_[loop.z_chord_norm, loop.z_chord_norm.iloc[0]]
                ax.plot(x, z, color="#bd7614")
        ax.set(xlabel="x/c", ylabel="z/c", title="Perfil y crossports exportados")
        ax.axis("equal")
        ax.grid(alpha=.2)
        _save(fig, folder, "04_crossports")
    if stabs and stabs.active() and controls.get("write_stabilizer_plot", True):
        rib = canopy.boundary_rib_rows().sort_values("Y_flat_mm").iloc[-1]
        origin = canopy.transform_profile_point(0., 0., rib, 0.)
        ex = canopy.transform_profile_point(1., 0., rib, 0.)-origin
        ez = canopy.transform_profile_point(0., 1., rib, 0.)-origin
        ex /= np.linalg.norm(ex)
        ez -= np.dot(ez, ex)*ex
        ez /= np.linalg.norm(ez)
        project = lambda xyz: np.column_stack(((np.asarray(xyz)-origin)@ex, (np.asarray(xyz)-origin)@ez))
        fig, ax = plt.subplots(figsize=(11, 5))
        for branch in (canopy.upper, canopy.lower):
            xyz = [canopy.transform_profile_point(float(p.x_chord_norm), float(p.z_chord_norm), rib, 0.) for p in branch.itertuples()]
            xy = project(xyz)
            ax.plot(*xy.T, color="#157a8b")
        for surface in stabs.surfaces:
            if not str(surface["surface_id"]).startswith("STAB_R"): continue
            points = project([stabs.nodes[n].p for n in surface["point_ids"]])
            ax.fill(points[:, 0], points[:, 1], color="#dca648", alpha=.35)
            for i, pt in enumerate(points):
                nxt = points[(i+1)%len(points)]
                ax.text(*(.5*(pt+nxt)), f"{np.linalg.norm(nxt-pt):.0f} mm", fontsize=8)
        if network:
            anchors = [n for n in network.anchor_nodes.values() if str(n.rib_id) == str(int(rib.rib_id))]
            for anchor in anchors:
                xy = project([anchor.p])[0]
                ax.plot(*xy, "o", color="#a62c52")
                ax.annotate(anchor.bank, xy, xytext=(4, 6), textcoords="offset points")
        ax.set(xlabel="Cuerda local [mm]", ylabel="Altura local [mm]", title="Proyección perpendicular al plano de costilla exterior · estabilizador y anclajes")
        ax.axis("equal")
        ax.grid(alpha=.2)
        _save(fig, folder, "05_stabilizer_true_projection")
    if network:
        from ramair_manual_rigging import line_path_audit
        paths = pd.DataFrame(line_path_audit(network))
        paths.to_csv(folder / "suspension_anchor_paths.csv", index=False)
        by_bank = []
        for bank, frame in paths.groupby("bank"):
            lengths = frame.path_length_mm.to_numpy(float)
            by_bank.append(dict(bank=bank, anchors=len(frame), mean_mm=float(lengths.mean()),
                minimum_mm=float(lengths.min()), maximum_mm=float(lengths.max()),
                spread_fraction=float(np.ptp(lengths)/max(lengths.mean(), 1e-12))))
        (folder / "crown_rigging_audit.json").write_text(json.dumps({"banks": by_bank,
            "interpretation": "Actual summed geometric lengths. Crown rigging requires approximately equal total paths within each bank; a mean R/b constraint alone does not guarantee equality.",
            "all_anchors_connected": bool(paths.connected.all()), "positioning_mode": network.cfg.get("suspension", {}).get("positioning_mode", "automatic")}, indent=2), encoding="utf-8")
        pd.DataFrame([dict(node_id=n.node_id, node_type=n.node_type, x=n.x, y=n.y, z=n.z, bank=n.bank, side=n.side) for n in network.nodes.values()]).to_csv(folder/"manual_node_reference.csv", index=False)
        pd.DataFrame([dict(start=s.start_node, end=s.end_node, bank=s.bank, side=s.side, line_type=s.line_type, length_mm=s.length_mm) for s in network.segments]).to_csv(folder/"manual_connection_reference.csv", index=False)
        if controls.get("write_suspension_plot", True):
            fig = plt.figure(figsize=(11, 8))
            ax = fig.add_subplot(111, projection="3d")
            for segment in network.segments:
                xyz = np.array([network.nodes[segment.start_node].p, network.nodes[segment.end_node].p])
                ax.plot(xyz[:, 0], xyz[:, 1], xyz[:, 2], alpha=.6, linewidth=.7)
            ax.set(xlabel="x [mm]", ylabel="y [mm]", zlabel="z [mm]", title="Red de suspensión · posiciones y conexiones reales")
            _save(fig, folder, "06_suspension_network")
    return folder
