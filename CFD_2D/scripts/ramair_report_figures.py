"""Publication figures shared by the report export and the application."""
from pathlib import Path
import numpy as np
import pandas as pd
from ramair_scientific_plot_style import apply_scientific_style, save_scientific_figure


def rans_grid_figures(rows, output):
    import matplotlib.pyplot as plt
    apply_scientific_style()
    data = pd.DataFrame(rows)
    if data.empty:
        return []
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    products = []
    for (topology, alpha), group in data.groupby(["topology", "alpha_deg"]):
        group = group[group.included_in_rans_mesh_convergence.astype(str).str.lower() == "true"]
        group = group.sort_values("cell_count")
        if group.empty:
            continue
        prefix = f"{topology}_alpha_{float(alpha):g}"
        metrics = (("mean_CL", r"$C_L$"), ("mean_CD", r"$C_D$"), ("mean_CM", r"$C_M$"))
        fig, axes = plt.subplots(1, 3, figsize=(10.5, 3.3), constrained_layout=True)
        for axis, (key, label) in zip(axes, metrics):
            axis.plot(group.cell_count, group[key], "o-", lw=1.2)
            axis.set(xlabel=r"Number of cells, $N$", ylabel=label)
            axis.ticklabel_format(axis="x", style="sci", scilimits=(0, 0))
            for _, row in group.iterrows():
                axis.annotate(str(row.mesh_level).title(), (row.cell_count, row[key]),
                              xytext=(0, 8), textcoords="offset points", ha="center", fontsize=8)
            axis.margins(y=.2)
        fig.suptitle(f"RANS grid comparison: {topology}, "+r"$\alpha$"+f" = {alpha:g} deg")
        target = output / (prefix+"_coefficients_vs_cells.png")
        fig.savefig(target.with_suffix('.pdf'))
        save_scientific_figure(fig, target, data=group, metadata={"source":"Explicitly reviewed RANS checkpoint summaries", "grouping":"topology, alpha; original acceptance flags retained"})
        products.append(str(target))
        medium = group[group.mesh_level == "medium"]
        if len(medium) != 1:
            continue
        fig, ax = plt.subplots(figsize=(7, 3.8), constrained_layout=True)
        changes = []
        for offset, level, color in ((-.19, "coarse", "#0072B2"), (.19, "fine", "#D55E00")):
            candidate = group[group.mesh_level == level]
            if len(candidate) != 1:
                continue
            heights = []
            for key, label in metrics:
                reference = float(medium.iloc[0][key])
                value = float(candidate.iloc[0][key])
                delta = 100*(value-reference)/abs(reference) if abs(reference) > 1e-10 else np.nan
                heights.append(delta)
                changes.append(dict(topology=topology, alpha_deg=alpha, mesh_level=level, metric=key, relative_difference_percent=delta))
            ax.bar(np.arange(3)+offset, heights, width=.38, label=level.title(), color=color)
        ax.axhline(3, ls="--", lw=.8, color="#555555", label=r"$\pm3\%$ reference")
        ax.axhline(-3, ls="--", lw=.8, color="#555555")
        ax.axhline(0, lw=.6, color="black")
        ax.set(xticks=np.arange(3), xticklabels=[label for _, label in metrics],
               ylabel="Relative difference [%]", title="Relative difference with respect to Medium grid")
        ax.legend(ncols=3, loc="best", fontsize=9)
        target = output / (prefix+"_relative_to_medium.png")
        fig.savefig(target.with_suffix('.pdf'))
        save_scientific_figure(fig, target, data=changes, metadata={"source":"Explicitly reviewed RANS checkpoint summaries", "transformation":"100*(value-medium)/abs(medium); undefined near zero; 3% is a reference, not GCI"})
        products.append(str(target))
    return products
