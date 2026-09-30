"""Shared runtime bounds and optional wake field for the standard meshers."""
from __future__ import annotations
import contextlib
import signal


@contextlib.contextmanager
def gmsh_deadline(seconds: int):
    if not hasattr(signal, "SIGALRM"):
        yield
        return
    def timed_out(*_):
        raise TimeoutError(f"Gmsh superó su límite de {seconds} s")
    previous = signal.signal(signal.SIGALRM, timed_out)
    signal.alarm(max(1, int(seconds)))
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


def add_wake_field(gmsh, background: int, config: dict, chord: float) -> int:
    wake = config.get("wake_refinement", {})
    if not wake.get("enabled", False):
        return background
    box = gmsh.model.mesh.field.add("Box")
    for key, value in {"VIn": float(wake["size_chord"])*chord, "VOut": 1e22,
                       "XMin": chord, "XMax": chord*(1+float(wake["length_chord"])),
                       "YMin": -chord*float(wake["half_width_chord"]), "YMax": chord*float(wake["half_width_chord"]),
                       "ZMin": -chord, "ZMax": chord, "Thickness": .1*chord}.items():
        gmsh.model.mesh.field.setNumber(box, key, value)
    combined = gmsh.model.mesh.field.add("Min")
    gmsh.model.mesh.field.setNumbers(combined, "FieldsList", [background, box])
    return combined
