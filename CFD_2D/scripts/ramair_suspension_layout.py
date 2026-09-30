"""Versioned suspension selection and automatic networks (millimetres).

Cell IDs are one-based, ordered from left tip to right tip. Selection uses
developed coordinates, never projected distances or an assumed uniform grid.
Missing layout fields mean legacy behaviour; migration must not change it.
"""
from __future__ import annotations
import copy
import math
import numpy as np

SCHEMA_VERSION = 2
TEMPLATES = ("legacy", "none", "spanwise", "chordwise", "combined")


def validate_network(network):
    """Check the effective directed line graph, including manual connections."""
    errors, outgoing, keys = [], {}, set()
    for segment in network.segments:
        # Closed outlines of slider/payload/AGU are solid geometry, not directed
        # suspension chains; their intentional loops are not line-network cycles.
        if getattr(segment, "line_type", "structural") not in {"structural", "brake", "lower_strap"}:
            continue
        a, b = segment.start_node, segment.end_node
        if a not in network.nodes or b not in network.nodes:
            errors.append(f"Nodo inexistente: {a} → {b}")
            continue
        key = tuple(sorted((a, b)))
        if key in keys:
            errors.append(f"Segmento duplicado: {a} → {b}")
        keys.add(key)
        if np.linalg.norm(network.nodes[a].p - network.nodes[b].p) <= 1e-6:
            errors.append(f"Segmento de longitud nula: {a} → {b}")
        outgoing.setdefault(a, []).append(b)
    for node in network.nodes.values():
        if node.node_type in {"anchor", "cascade"} and len(outgoing.get(node.node_id, [])) != 1:
            errors.append(f"Anclaje/confluencia sin un destino único: {node.node_id}")
    active, done = set(), set()
    def visit(node):
        if node in active:
            errors.append(f"Ciclo en la red: {node}")
            return
        if node in done:
            return
        active.add(node)
        for child in outgoing.get(node, []):
            visit(child)
        active.remove(node)
        done.add(node)
    for node in network.nodes:
        visit(node)
    for warning in getattr(network, "warnings", []):
        if str(warning).startswith(("Invalid segment", "Duplicated segment", "Near-zero segment")):
            errors.append(str(warning))
    return {"schema_version": SCHEMA_VERSION, "valid": not errors,
            "errors": list(dict.fromkeys(errors)), "nodes": len(network.nodes),
            "segments": len(network.segments)}


def migrate(config, *, new=False):
    result = copy.deepcopy(config)
    result.setdefault("suspension_schema_version", SCHEMA_VERSION)
    result.setdefault("brake_selection", {"mode": "outer_fraction" if new else "legacy",
        "symmetric": True, "fraction_L": .33, "fraction_R": .33, "cells_L": [], "cells_R": []})
    result.setdefault("cascade_layout", {"template": "chordwise" if new else "legacy",
        "fraction": .45, "span_group_size": 2, "span_fraction": .7})
    return result


def select_brake_cells(ribs, selection):
    rows = sorted(ribs, key=lambda r: float(r["Y_flat_mm"]))
    ys = [float(r["Y_flat_mm"]) for r in rows]
    if len(rows) < 2 or any(not math.isfinite(v) for v in ys) or any(b <= a for a, b in zip(ys, ys[1:])):
        raise ValueError("Se necesitan límites de celdas distintos y ordenados.")
    if not ys[0] < 0 < ys[-1]:
        raise ValueError("La envergadura debe incluir ambos lados del plano de simetría.")
    mode = selection.get("mode", "legacy")
    if mode not in {"legacy", "outer_fraction", "cells"}:
        raise ValueError("Modo de selección de frenos desconocido.")
    reports = {}
    selected = set()
    for side in ("L", "R"):
        sign = -1 if side == "L" else 1
        semispan = -ys[0] if side == "L" else ys[-1]
        source_side = "L" if selection.get("symmetric", True) else side
        fraction = float(selection.get("fraction_" + source_side, .33))
        if not math.isfinite(fraction) or not 0 <= fraction <= 1:
            raise ValueError("La cobertura de frenos debe estar entre 0 y 100 % de b/2.")
        explicit = set(int(i) for i in selection.get("cells_" + source_side, []))
        if side == "R" and selection.get("symmetric", True):
            explicit = {len(rows) - i for i in explicit}
        if any(i < 1 or i >= len(rows) for i in explicit):
            raise ValueError("Identificador de celda fuera de la envergadura.")
        cells, rib_ids, covered = [], set(), 0.
        for i, (a, b) in enumerate(zip(ys, ys[1:]), 1):
            # A centre-straddling cell belongs to both sides, with clipped coverage.
            inner, outer = sorted((sign * a, sign * b))
            if outer <= 0: continue
            chosen = mode == "legacy" or (mode == "cells" and i in explicit) or (
                mode == "outer_fraction" and fraction > 0 and outer > semispan * (1 - fraction) + 1e-9)
            if chosen:
                cells.append(i)
                rib_ids.update((int(rows[i-1]["rib_id"]), int(rows[i]["rib_id"])))
                covered += outer - max(0., inner)
        if mode == "cells" and explicit - set(cells):
            raise ValueError("Hay celdas seleccionadas en el lado incorrecto.")
        selected.update(rib_ids)
        reports[side] = {"cells": cells, "rib_ids": sorted(rib_ids), "attachments": len(rib_ids),
            "requested_fraction": fraction if mode == "outer_fraction" else None,
            "effective_fraction": covered / semispan}
    return {"schema_version": SCHEMA_VERSION, "mode": mode, "sides": reports, "rib_ids": sorted(selected)}


def build_network(network, node_class):
    """Build explicit chordwise/spanwise stages, preserving the existing R/b solver."""
    cfg = network.cfg
    layout = cfg.get("cascade_layout", {})
    template = layout.get("template", "legacy")
    if template not in TEMPLATES or template == "legacy":
        raise ValueError("Plantilla automática no válida.")
    fraction = float(layout.get("fraction", .45))
    span_fraction = float(layout.get("span_fraction", .7))
    group_size = int(layout.get("span_group_size", 2))
    if not 0 < fraction < 1 or not 0 < span_fraction < 1 or group_size < 2:
        raise ValueError("Fracciones de cascada entre 0 y 1; agrupación de al menos dos líneas.")
    banks = {str(b["name"]).upper(): b for b in cfg["banks"] if b.get("enabled", True)}
    for side in ("L", "R"):
        anchors = [n for n in network.nodes.values() if n.node_type == "anchor" and n.side == side]
        destinations = {}
        for anchor in anchors:
            target = network._target_for(anchor.bank, side)
            if target not in network.nodes: raise ValueError(f"Destino inexistente: {target}")
            destinations.setdefault((target, anchor.line_type), []).append(anchor.node_id)
        serial = 0
        def join(children, target, level, frac, bank, kind):
            nonlocal serial
            if len(children) < 2: return children
            serial += 1
            nid = f"LAYOUT_{side}_{level}_{serial}"
            centroid = np.mean([network.nodes[c].p for c in children], axis=0)
            point = centroid + frac * (network.nodes[target].p-centroid)
            network.nodes[nid] = node_class(nid, "cascade", *point, bank, side, "", level, kind,
                f"layout:{level}:{serial}", str(serial), nid)
            for child in children:
                network.add_segment(child, nid, bank, side, level, kind, f"layout:{child}:{nid}")
            return [nid]
        for (target, kind), current in destinations.items():
            current.sort(key=lambda n: (abs(network.nodes[n].y), network.nodes[n].bank, n))
            if kind == "brake":
                if template != "none":
                    current = join(current, target, 1, fraction, "BRK", kind)
            elif template in {"chordwise", "combined"}:
                remaining = set(current)
                next_nodes = []
                for pair in (("A", "B"), ("C", "D")):
                    if all(b in banks for b in pair) and network._target_for(pair[0], side) != network._target_for(pair[1], side):
                        raise ValueError(f"Destinos incompatibles para {pair[0]}–{pair[1]}.")
                    stations = sorted({str(network.nodes[n].rib_id) for n in current})
                    for station in stations:
                        group = [n for n in current if network.nodes[n].bank in pair and str(network.nodes[n].rib_id) == station]
                        if len(group) == 2:
                            next_nodes += join(group, target, 1, fraction, "_".join(pair), kind)
                            remaining.difference_update(group)
                current = next_nodes + sorted(remaining)
            if kind != "brake" and template in {"spanwise", "combined"}:
                next_nodes = []
                for bank in sorted({network.nodes[n].bank for n in current}):
                    group = sorted([n for n in current if network.nodes[n].bank == bank], key=lambda n: abs(network.nodes[n].y))
                    for i in range(0, len(group), group_size):
                        chunk = group[i:i+group_size]
                        next_nodes += join(chunk, target, 2, span_fraction, bank, kind) if len(chunk) == group_size else chunk
                current = next_nodes
            for child in current:
                network.add_segment(child, target, network.nodes[child].bank, side, 99, kind, f"layout:{child}:target")
