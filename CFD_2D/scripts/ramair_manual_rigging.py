"""Validated explicit suspension graph, independent of automatic cascade layout."""
from __future__ import annotations
import math


def apply_manual_network(network, node_type):
    manual = network.cfg.get("manual_network", {})
    rows, edges = manual.get("nodes", []), manual.get("segments", [])
    if not edges:
        raise ValueError("El modo manual necesita una tabla de conexiones. Usa los ID de anclajes de la vista previa.")
    identifiers = set()
    for row in rows:
        name = str(row.get("node_id", "")).strip()
        if not name or name in identifiers or name in network.anchor_nodes:
            raise ValueError(f"ID manual vacío, repetido o reservado a un anclaje: {name}")
        identifiers.add(name)
        xyz = [float(row[k]) for k in ("x", "y", "z")]
        if not all(math.isfinite(x) for x in xyz):
            raise ValueError(f"El nodo {name} necesita coordenadas finitas.")
        network.nodes[name] = node_type(name, str(row.get("node_type", "cascade")), *xyz,
            str(row.get("bank", "")), str(row.get("side", "")), line_type="structural", label=name)
    # Component corners can be overridden by node ID. Refresh their true lengths.
    for segment in network.segments:
        import numpy as np
        segment.length_mm = float(np.linalg.norm(network.nodes[segment.end_node].p-network.nodes[segment.start_node].p))
    parents = {}
    for row in edges:
        start, end = str(row.get("start", "")), str(row.get("end", ""))
        if start not in network.nodes or end not in network.nodes or start == end:
            raise ValueError(f"Conexión manual inválida: {start} → {end}")
        if start in parents:
            raise ValueError(f"{start} tiene más de un destino; orienta las conexiones del canopy hacia la carga.")
        parents[start] = end
    for start in parents:
        visited, node = set(), start
        while node in parents:
            if node in visited: raise ValueError(f"La red manual contiene un ciclo desde {start}.")
            visited.add(node)
            node = parents[node]
        if node in network.anchor_nodes:
            raise ValueError(f"La conexión desde {start} termina en otro anclaje del canopy.")
    missing = set(network.anchor_nodes)-set(parents)
    if missing:
        raise ValueError("Anclajes sin conexión: "+", ".join(sorted(missing)))
    for row in edges:
        start, end = str(row["start"]), str(row["end"])
        source = network.nodes[start]
        kind = str(row.get("line_type") or source.line_type or "structural")
        if kind not in {"structural", "brake"}:
            raise ValueError("Las conexiones manuales deben ser structural o brake.")
        before = len(network.segments)
        network.add_segment(start, end, str(row.get("bank") or source.bank), str(row.get("side") or source.side), 1, kind)
        if len(network.segments) == before:
            raise ValueError(f"Conexión repetida o de longitud nula: {start} → {end}")


def line_path_audit(network) -> list[dict]:
    """Sum actual segment lengths to the terminal target for every loaded anchor."""
    rows = []
    for name, anchor in network.anchor_nodes.items():
        node, length, seen = name, 0., set()
        while node in network.parent:
            if node in seen: raise ValueError(f"Ciclo en red de líneas: {name}")
            seen.add(node)
            nxt = network.parent[node]
            import numpy as np
            length += float(np.linalg.norm(network.nodes[nxt].p-network.nodes[node].p))
            node = nxt
        rows.append(dict(anchor=name, bank=anchor.bank, side=anchor.side, terminal=node,
                         path_length_mm=length, connected=bool(seen)))
    return rows
