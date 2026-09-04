#!/usr/bin/env python3
"""Scripted ParaView launcher for reconstructed OpenFOAM cases.

The launcher deliberately avoids ``paraFoam`` session state and ``--data``.
Instead, a ParaView Python startup script opens the resolved ``.foam`` marker,
selects ``internalMesh``, advances to the latest written time, frames the data
and saves a readiness screenshot. Interactive state saving is intentionally
disabled because ParaView can open a blocking file-location/copy-mode dialog.
"""
from __future__ import annotations

import json
import argparse
import os
import signal
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from openfoam_environment import activate_openfoam_environment


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _reap_interactive_process(process: subprocess.Popen[Any], label: str) -> None:
    """Prevent closed ParaView windows from remaining as defunct children."""
    threading.Thread(
        target=process.wait,
        name=f"{label}-{process.pid}",
        daemon=True,
    ).start()


def case_chord_m(case_dir: Path) -> float | None:
    for name in ("case_config.json", "case_input_summary.json"):
        data = read_json(case_dir / name)
        for key in ("chord_m", "reference_length_m"):
            try:
                value = float(data[key])
            except (KeyError, TypeError, ValueError):
                continue
            if value > 0.0:
                return value
    return None


def case_physical_time_ceiling_s(case_dir: Path) -> float | None:
    """Return the configured URANS end time, excluding archived RANS indices."""
    values: dict[str, Any] = {}
    for name in ("case_config.json", "case_input_summary.json"):
        values.update(read_json(case_dir / name))
    try:
        velocity = float(values["velocity_m_s"])
        chord = float(values.get("chord_m") or values["reference_length_m"])
        end_star = float(values["endTime_star"])
    except (KeyError, TypeError, ValueError):
        return None
    if velocity <= 0.0 or chord <= 0.0 or end_star <= 0.0:
        return None
    return end_star * chord / velocity


def write_paraview_case_script(
    script_path: Path,
    foam_marker: Path,
    *,
    screenshot_path: Path | None = None,
    state_path: Path | None = None,
    ready_path: Path | None = None,
    focus_chord_m: float | None = None,
    maximum_physical_time_s: float | None = None,
) -> Path:
    """Write a deterministic startup script that emits a portable state."""
    script_path = script_path.resolve()
    foam_marker = foam_marker.resolve()
    screenshot_path = (screenshot_path or script_path.with_suffix(".png")).resolve()
    state_path = (state_path or script_path.with_suffix(".pvsm")).resolve()
    ready_path = (ready_path or script_path.with_suffix(".ready.json")).resolve()
    script = f'''from paraview.simple import *
import json
import os
from pathlib import Path

try:
    from paraview.simple import _DisableFirstRenderCameraReset
    _DisableFirstRenderCameraReset()
except (ImportError, AttributeError):
    pass

foam_path = {json.dumps(str(foam_marker))}
screenshot_path = {json.dumps(str(screenshot_path))}
state_path = {json.dumps(str(state_path))}
ready_path = {json.dumps(str(ready_path))}
focus_chord_m = {json.dumps(float(focus_chord_m) if focus_chord_m else None)}
maximum_physical_time_s = {json.dumps(float(maximum_physical_time_s) if maximum_physical_time_s else None)}

view = GetActiveViewOrCreate("RenderView")
view.CameraParallelProjection = 1
view.ViewSize = [1600, 1000]
try:
    view.OrientationAxesVisibility = 0
except Exception:
    pass
source = OpenFOAMReader(FileName=foam_path)
try:
    source.CaseType = "Reconstructed Case"
except Exception:
    pass
try:
    source.MeshRegions = ["internalMesh"]
except Exception:
    pass
try:
    source.SkipZeroTime = 0
except Exception:
    pass
source.UpdatePipeline()

scene = GetAnimationScene()
scene.UpdateAnimationUsingDataTimeSteps()
try:
    available_times = [float(value) for value in source.TimestepValues]
except Exception:
    available_times = []
if available_times:
    physical_times = [
        value for value in available_times
        if maximum_physical_time_s is None
        or value <= maximum_physical_time_s * (1.0 + 1.0e-8)
    ]
    scene.AnimationTime = (physical_times or available_times)[-1]
source.UpdatePipeline(time=scene.AnimationTime)
display = Show(source, view)
display.Representation = "Surface"

colored_by = "solid"
for association in ("POINTS", "CELLS"):
    try:
        information = source.GetPointDataInformation() if association == "POINTS" else source.GetCellDataInformation()
        if information.GetArray("Cp") is not None:
            ColorBy(display, (association, "Cp"))
            display.RescaleTransferFunctionToDataRange(True, False)
            display.SetScalarBarVisibility(view, True)
            colored_by = association + ":Cp"
            break
        if information.GetArray("U") is not None:
            ColorBy(display, (association, "U", "Magnitude"))
            display.RescaleTransferFunctionToDataRange(True, False)
            display.SetScalarBarVisibility(view, True)
            colored_by = association + ":U:Magnitude"
            break
        if information.GetArray("p") is not None:
            ColorBy(display, (association, "p"))
            display.RescaleTransferFunctionToDataRange(True, False)
            display.SetScalarBarVisibility(view, True)
            colored_by = association + ":p"
            break
    except Exception:
        continue

try:
    bounds = source.GetDataInformation().GetBounds()
    cx = 0.5 * (bounds[0] + bounds[1])
    cy = 0.5 * (bounds[2] + bounds[3])
    cz = 0.5 * (bounds[4] + bounds[5])
    extent = max(bounds[1] - bounds[0], bounds[3] - bounds[2], 1.0)
    view.CameraFocalPoint = [cx, cy, cz]
    view.CameraPosition = [cx, cy, cz + 2.0 * extent]
    view.CameraViewUp = [0.0, 1.0, 0.0]
except Exception:
    pass
ResetCamera(view)
if focus_chord_m and focus_chord_m > 0:
    # The CFD geometry is chordwise x=0..c. Keep the full solver domain loaded,
    # but frame the screenshot around the airfoil rather than the farfield.
    try:
        z_mid = 0.5 * (bounds[4] + bounds[5])
        view.CameraFocalPoint = [0.5 * focus_chord_m, 0.0, z_mid]
        view.CameraPosition = [0.5 * focus_chord_m, 0.0, z_mid + 2.0 * focus_chord_m]
        view.CameraViewUp = [0.0, 1.0, 0.0]
        view.CameraParallelScale = 0.45 * focus_chord_m
    except Exception:
        pass
Render(view)
SaveScreenshot(screenshot_path, view, ImageResolution=[1600, 1000])
# SaveState opens ParaView's file-location/copy-mode dialog in an interactive
# WSL session and blocks the loaded case behind that modal window. Interactive
# opening therefore renders the case and writes readiness evidence only.
state_path = None
portable_loader_path = None
relative_foam_path = foam_path
Path(ready_path).write_text(json.dumps({{
    "schema_version": 2,
    "path_base": "absolute_case_reference",
    "status": "READY",
    "foam_marker": foam_path,
    "animation_time": float(scene.AnimationTime),
    "available_times": available_times,
    "positive_time_count": len([value for value in available_times if value > 0.0]),
    "temporal_animation_ready": len([value for value in available_times if value > 0.0]) >= 2,
    "camera_focus": "airfoil_chord" if focus_chord_m else "full_domain",
    "focus_chord_m": focus_chord_m,
    "colored_by": colored_by,
    "screenshot": screenshot_path,
    "state": state_path,
    "portable_loader": portable_loader_path,
    "case_reference": relative_foam_path,
}}, indent=2) + "\\n", encoding="utf-8")
'''
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text(script, encoding="utf-8")
    return script_path


def paraview_environment() -> dict[str, str]:
    environment = os.environ.copy()
    if environment.get("WSL_DISTRO_NAME"):
        environment.setdefault("DISPLAY", ":0")
        environment.setdefault("WAYLAND_DISPLAY", "wayland-0")
        environment.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
        environment.setdefault("PULSE_SERVER", "unix:/mnt/wslg/PulseServer")
        dist_packages = "/usr/lib/python3/dist-packages"
        entries = [entry for entry in environment.get("PYTHONPATH", "").split(os.pathsep) if entry]
        if dist_packages not in entries:
            environment["PYTHONPATH"] = os.pathsep.join([dist_packages, *entries])
    return environment


def prepare_paraview_case(case_dir: Path) -> dict[str, Any]:
    """Prepare the marker/startup/state paths without opening a GUI."""
    case_dir = case_dir.resolve()
    if not (case_dir / "system" / "controlDict").is_file():
        raise FileNotFoundError(f"Not an OpenFOAM case: {case_dir}")
    marker = case_dir / f"{case_dir.name}.foam"
    marker.touch(exist_ok=True)
    support = case_dir / "postProcessing" / "ParaView"
    support.mkdir(parents=True, exist_ok=True)
    script = write_paraview_case_script(
        support / "open_case.py",
        marker,
        screenshot_path=support / "case_latest.png",
        state_path=support / "case_latest.pvsm",
        ready_path=support / "case_latest.ready.json",
        focus_chord_m=case_chord_m(case_dir),
        maximum_physical_time_s=case_physical_time_ceiling_s(case_dir),
    )
    ready = support / "case_latest.ready.json"
    ready.unlink(missing_ok=True)
    return {
        "status": "PREPARED_FOR_PARAVIEW",
        "render_verified": False,
        "case_dir": str(case_dir),
        "foam_marker": str(marker),
        "startup_script": str(script),
        "ready_file": str(ready),
        "screenshot": str(support / "case_latest.png"),
        "state": str(support / "case_latest.pvsm"),
        "log": str(support / "log.paraview_launch"),
    }


def write_automatic_products_script(
    script_path: Path,
    foam_marker: Path,
    output_dir: Path,
    *,
    chord_m: float,
    velocity_m_s: float,
    alpha_deg: float,
    maximum_courant: float,
    maximum_frames: int,
    time_semantics: str,
    stage_label: str,
    field_scale_mode: str = "exact",
    robust_percentiles: tuple[float, float] = (1.0, 99.0),
    manual_scales: dict[str, tuple[float, float]] | None = None,
    time_range_s: tuple[float, float] | None = None,
    include_animations: bool = True,
) -> Path:
    """Write a bounded, off-screen ParaView render script.

    The script reads the OpenFOAM case directly. It does not call foamToVTK and
    therefore does not duplicate the transient field database.
    """
    script_path = script_path.resolve()
    foam_marker = foam_marker.resolve()
    output_dir = output_dir.resolve()
    normalized_scale_mode = str(field_scale_mode).strip().lower()
    if normalized_scale_mode not in {"exact", "robust", "manual"}:
        raise ValueError(
            f"Unsupported ParaView field scale mode: {field_scale_mode}"
        )
    low_percentile, high_percentile = robust_percentiles
    if not 0.0 <= low_percentile < high_percentile <= 100.0:
        raise ValueError("Invalid robust percentile interval")
    normalized_manual_scales = {
        str(name): [float(bounds[0]), float(bounds[1])]
        for name, bounds in (manual_scales or {}).items()
        if len(bounds) == 2 and float(bounds[0]) < float(bounds[1])
    }
    normalized_time_range = (
        [float(time_range_s[0]), float(time_range_s[1])]
        if time_range_s is not None and float(time_range_s[0]) <= float(time_range_s[1])
        else None
    )
    script = f'''from paraview.simple import *
import json
import math
import os
from pathlib import Path

try:
    from paraview.simple import _DisableFirstRenderCameraReset
    _DisableFirstRenderCameraReset()
except (ImportError, AttributeError):
    pass

foam_path = {json.dumps(str(foam_marker))}
output_dir = Path({json.dumps(str(output_dir))})
chord_m = {float(chord_m)!r}
velocity_m_s = {float(velocity_m_s)!r}
alpha_deg = {float(alpha_deg)!r}
maximum_courant = {max(float(maximum_courant), 1.0e-6)!r}
maximum_frames = {max(2, int(maximum_frames))}
time_semantics = {json.dumps(str(time_semantics))}
stage_label = {json.dumps(str(stage_label))}
field_scale_mode = {json.dumps(normalized_scale_mode)}
robust_percentiles = {json.dumps([float(low_percentile), float(high_percentile)])}
manual_scales = {json.dumps(normalized_manual_scales)}
requested_time_range_s = {normalized_time_range!r}
include_animations = {bool(include_animations)!r}
scale_evidence = {{}}
stage_slug = "".join(character for character in stage_label if character.isalnum() or character in ("_", "-")) or "CFD"
is_iteration_stage = (
    stage_label.strip().upper() == "RANS"
    or (
        stage_label.strip().upper() != "URANS"
        and "iteration" in time_semantics.lower()
    )
)
frame_axis_label = "iteration" if is_iteration_stage else "t [s]"
output_dir.mkdir(parents=True, exist_ok=True)
velocity_dir = output_dir / "velocity_frames"
pressure_dir = output_dir / "pressure_frames"
pressure_contour_dir = output_dir / "pressure_contour_frames"
vorticity_contour_dir = output_dir / "vorticity_contour_frames"
velocity_dir.mkdir(exist_ok=True)
pressure_dir.mkdir(exist_ok=True)
pressure_contour_dir.mkdir(exist_ok=True)
vorticity_contour_dir.mkdir(exist_ok=True)

source = OpenFOAMReader(FileName=foam_path)
try:
    source.CaseType = "Reconstructed Case"
except Exception:
    pass
try:
    source.MeshRegions = ["internalMesh"]
except Exception:
    pass
try:
    source.SkipZeroTime = 0
except Exception:
    pass
try:
    source.UpdatePipelineInformation()
    requested_arrays = [
        "U", "p", "Cp", "Co", "nuTilda", "nut", "yPlus",
        "wallShearStress", "vorticity", "Q", "UMean", "pMean",
        "CpMean", "vorticityMean",
        "UPrime2Mean", "pPrime2Mean", "nuTildaMean",
    ]
    available_cell_arrays = list(source.CellArrays.Available)
    source.CellArrays = [
        name for name in requested_arrays if name in available_cell_arrays
    ]
except Exception:
    pass
source.UpdatePipeline()
visual_input = source
try:
    source_cell_names = [
        source.GetCellDataInformation().GetArray(index).GetName()
        for index in range(source.GetCellDataInformation().GetNumberOfArrays())
    ]
except Exception:
    source_cell_names = []
if not is_iteration_stage and "CpMean" not in source_cell_names and "pMean" in source_cell_names:
    mean_cp = Calculator(registrationName="DerivedCpMean", Input=visual_input)
    mean_cp.ResultArrayName = "CpMean"
    mean_cp.Function = "2*pMean/%.16g" % max(velocity_m_s * velocity_m_s, 1.0e-30)
    mean_cp.UpdatePipeline()
    visual_input = mean_cp
if not is_iteration_stage and "vorticityMean" not in source_cell_names and "UMean" in source_cell_names:
    try:
        mean_point_fields = CellDatatoPointData(
            registrationName="MeanPointFields", Input=visual_input
        )
        mean_point_fields.PassCellData = 1
        mean_velocity_gradient = Gradient(
            registrationName="MeanVelocityGradient", Input=mean_point_fields
        )
        mean_velocity_gradient.ScalarArray = ["POINTS", "UMean"]
        mean_velocity_gradient.ComputeVorticity = 1
        mean_velocity_gradient.VorticityArrayName = "vorticityMean"
        mean_velocity_gradient.UpdatePipeline()
        visual_input = mean_velocity_gradient
    except Exception:
        pass

scene = GetAnimationScene()
scene.UpdateAnimationUsingDataTimeSteps()
try:
    available_times = [float(value) for value in source.TimestepValues]
except Exception:
    available_times = []
positive_times = [value for value in available_times if value > 0.0]
if not positive_times and available_times:
    positive_times = available_times
if requested_time_range_s is not None and not is_iteration_stage:
    positive_times = [
        value for value in positive_times
        if requested_time_range_s[0] <= value <= requested_time_range_s[1]
    ]
if len(positive_times) > maximum_frames:
    selected_indices = sorted(set(
        int(round(index * (len(positive_times) - 1) / (maximum_frames - 1)))
        for index in range(maximum_frames)
    ))
    selected_times = [positive_times[index] for index in selected_indices]
else:
    selected_times = positive_times
range_times = selected_times if include_animations else selected_times[-1:]

view = GetActiveViewOrCreate("RenderView")
view.CameraParallelProjection = 1
view.ViewSize = [1280, 720]
try:
    view.UseColorPaletteForBackground = 0
except Exception:
    pass
view.Background = [0.91, 0.92, 0.93]
try:
    view.Background2 = [0.91, 0.92, 0.93]
except Exception:
    pass
try:
    view.OrientationAxesVisibility = 0
except Exception:
    pass
visual_source = Transform(registrationName="AngleOfAttackFrame", Input=visual_input)
visual_source.Transform.Rotate = [0.0, 0.0, -alpha_deg]
try:
    visual_source.TransformAllInputVectors = 1
except Exception:
    pass
visual_source.UpdatePipeline()
display = Show(visual_source, view)
display.Representation = "Surface"
title_source = Text(registrationName="StageTitle")
title_display = Show(title_source, view)
try:
    title_display.WindowLocation = "Upper Center"
    title_display.FontSize = 22
    title_display.Color = [0.05, 0.05, 0.05]
except Exception:
    pass

def set_title(field_label, value):
    title_source.Text = "%s final | %s | %s = %.6g" % (
        stage_label,
        field_label,
        frame_axis_label,
        value,
    )

def available_array(name):
    for association, information in (
        ("CELLS", visual_source.GetCellDataInformation()),
        ("POINTS", visual_source.GetPointDataInformation()),
    ):
        try:
            if information.GetArray(name) is not None:
                return association
        except Exception:
            pass
    return None

def leaf_datasets(dataset):
    if dataset is None:
        return
    if hasattr(dataset, "NewIterator"):
        iterator = dataset.NewIterator()
        iterator.SkipEmptyNodesOn()
        iterator.InitTraversal()
        while not iterator.IsDoneWithTraversal():
            child = iterator.GetCurrentDataObject()
            if child is not None:
                yield child
            iterator.GoToNextItem()
    else:
        yield dataset

def exact_range(name, association, vector_magnitude=False):
    lower = math.inf
    upper = -math.inf
    for value in range_times:
        visual_source.UpdatePipeline(time=value)
        information = (
            visual_source.GetCellDataInformation()
            if association == "CELLS"
            else visual_source.GetPointDataInformation()
        )
        array = information.GetArray(name)
        if array is None:
            continue
        component = -1 if vector_magnitude else 0
        try:
            current = array.GetComponentRange(component)
        except Exception:
            current = array.GetRange(component)
        if len(current) == 2:
            lower = min(lower, float(current[0]))
            upper = max(upper, float(current[1]))
    return (lower, upper) if lower < upper else None

def robust_range(name, association, vector_magnitude=False):
    try:
        import numpy as np
        from paraview import servermanager
        from vtk.util.numpy_support import vtk_to_numpy
    except Exception:
        return None
    samples = []
    maximum_samples_per_block = 200000
    for value in range_times:
        visual_source.UpdatePipeline(time=value)
        fetched = servermanager.Fetch(visual_source)
        for dataset in leaf_datasets(fetched):
            attributes = (
                dataset.GetCellData()
                if association == "CELLS"
                else dataset.GetPointData()
            )
            vtk_array = attributes.GetArray(name) if attributes else None
            if vtk_array is None:
                continue
            array = vtk_to_numpy(vtk_array)
            if vector_magnitude and getattr(array, "ndim", 1) > 1:
                array = np.linalg.norm(array, axis=1)
            else:
                array = np.asarray(array).reshape(-1)
            array = array[np.isfinite(array)]
            if not array.size:
                continue
            stride = max(1, int(math.ceil(array.size / maximum_samples_per_block)))
            samples.append(array[::stride])
    if not samples:
        return None
    merged = np.concatenate(samples)
    lower, upper = np.percentile(merged, robust_percentiles)
    return float(lower), float(upper)

def field_range(name, association, vector_magnitude=False):
    cached = scale_evidence.get(name)
    if cached:
        return float(cached["minimum"]), float(cached["maximum"])
    requested_mode = field_scale_mode
    used_mode = requested_mode
    bounds = None
    if requested_mode == "manual":
        configured = manual_scales.get(name)
        if configured and len(configured) == 2:
            bounds = float(configured[0]), float(configured[1])
        else:
            used_mode = "exact_fallback_missing_manual_range"
    elif requested_mode == "robust":
        bounds = robust_range(name, association, vector_magnitude)
        if bounds is None:
            used_mode = "exact_fallback_robust_unavailable"
    if bounds is None:
        bounds = exact_range(name, association, vector_magnitude)
    if bounds is None:
        return None
    lower, upper = bounds
    if not math.isfinite(lower) or not math.isfinite(upper):
        return None
    if lower >= upper:
        pad = max(abs(lower), abs(upper), 1.0) * 1.0e-9
        lower, upper = lower - pad, upper + pad
    scale_evidence[name] = {{
        "requested_mode": requested_mode,
        "used_mode": used_mode,
        "minimum": lower,
        "maximum": upper,
        "robust_percentiles": (
            robust_percentiles if requested_mode == "robust" else None
        ),
        "global_over_selected_frames": len(range_times) > 1,
    }}
    return lower, upper

def apply_field_range(lut, name, association, vector_magnitude=False):
    bounds = field_range(name, association, vector_magnitude)
    if bounds is None:
        return False
    lut.RescaleTransferFunction(bounds[0], bounds[1])
    return True

def style_scalar_bar(lut, title):
    try:
        bar = GetScalarBar(lut, view)
        bar.Title = title
        bar.ComponentTitle = ""
        bar.TitleColor = [0.05, 0.05, 0.05]
        bar.LabelColor = [0.05, 0.05, 0.05]
        bar.TitleFontSize = 12
        bar.LabelFontSize = 10
        bar.ScalarBarLength = 0.30
    except Exception:
        pass

def set_camera(kind):
    if kind == "airfoil":
        focal_x = 0.5 * chord_m
        scale = 0.42 * chord_m
    elif kind == "nearfield":
        # At 1600x1000 this frames approximately -0.5c..2c: the complete
        # profile, 0.5c upstream and 1c downstream of the trailing edge.
        focal_x = 0.75 * chord_m
        scale = 0.78 * chord_m
    else:
        focal_x = 1.5 * chord_m
        scale = 1.35 * chord_m
    try:
        bounds = visual_source.GetDataInformation().GetBounds()
        z_mid = 0.5 * (bounds[4] + bounds[5])
    except Exception:
        z_mid = 0.0
    view.CameraFocalPoint = [focal_x, 0.0, z_mid]
    view.CameraPosition = [focal_x, 0.0, z_mid + 5.0 * max(chord_m, 1.0e-6)]
    view.CameraViewUp = [0.0, 1.0, 0.0]
    view.CameraParallelScale = max(scale, 1.0e-6)

def color_cp(instantaneous=False):
    try:
        display.SetScalarBarVisibility(view, False)
    except Exception:
        pass
    preferred = (
        "CpMean" if not is_iteration_stage and not instantaneous and available_array("CpMean")
        else "pMean" if not is_iteration_stage and not instantaneous and available_array("pMean")
        else "Cp" if available_array("Cp")
        else "p" if available_array("p")
        else None
    )
    association = available_array(preferred) if preferred else None
    if association and preferred:
        ColorBy(display, (association, preferred))
        lut = GetColorTransferFunction(preferred)
        if not apply_field_range(lut, preferred, association):
            display.RescaleTransferFunctionToDataRange(True, False)
        display.SetScalarBarVisibility(view, True)
        style_scalar_bar(lut, "Cp [-]" if preferred.startswith("Cp") else preferred)
        return preferred
    return None

def color_velocity(instantaneous=False):
    try:
        display.SetScalarBarVisibility(view, False)
    except Exception:
        pass
    name = (
        "UMean"
        if not is_iteration_stage and not instantaneous and available_array("UMean")
        else "U"
    )
    association = available_array(name)
    if not association:
        return None
    ColorBy(display, (association, name, "Magnitude"))
    lut = GetColorTransferFunction(name)
    if not apply_field_range(lut, name, association, True):
        display.RescaleTransferFunctionToDataRange(True, False)
    display.SetScalarBarVisibility(view, True)
    style_scalar_bar(lut, "|%s| [m/s]" % name)
    return "%s magnitude" % name

def color_courant():
    try:
        display.SetScalarBarVisibility(view, False)
    except Exception:
        pass
    association = available_array("Co")
    if not association:
        return None
    ColorBy(display, (association, "Co"))
    lut = GetColorTransferFunction("Co")
    # A fixed 0..10 display range makes Co >= 10 visibly saturated red while
    # preserving the true instantaneous maximum as a separate annotation.
    lut.RescaleTransferFunction(0.0, 10.0)
    display.SetScalarBarVisibility(view, True)
    style_scalar_bar(lut, "Co [-]")
    return "Co"

def color_scalar_field(name, vector_magnitude=False):
    try:
        display.SetScalarBarVisibility(view, False)
    except Exception:
        pass
    association = available_array(name)
    if not association:
        return None
    if vector_magnitude:
        ColorBy(display, (association, name, "Magnitude"))
    else:
        ColorBy(display, (association, name))
    lut = GetColorTransferFunction(name)
    if not apply_field_range(lut, name, association, vector_magnitude):
        display.RescaleTransferFunctionToDataRange(True, False)
    display.SetScalarBarVisibility(view, True)
    style_scalar_bar(lut, name)
    return {{"array": name, "association": association, "vector_magnitude": vector_magnitude}}

wall_overlay = {{
    "reader": None,
    "transform": None,
    "feature_edges": None,
    "display": None,
    "regions": [],
}}

def show_airfoil_overlay(latest):
    if wall_overlay["display"] is not None:
        wall_overlay["display"].Visibility = 1
        return wall_overlay["display"]
    try:
        reader = OpenFOAMReader(FileName=foam_path)
        reader.CaseType = "Reconstructed Case"
        reader.SkipZeroTime = 0
        reader.UpdatePipelineInformation()
        available = list(reader.MeshRegions.Available)
        regions = [
            name for name in available
            if "airfoil" in name.lower()
            or ("wall" in name.lower() and "frontandback" not in name.lower())
        ]
        if regions:
            reader.MeshRegions = regions
            reader.UpdatePipeline(time=latest)
            transformed = Transform(registrationName="RotatedAirfoilOutline", Input=reader)
            transformed.Transform.Rotate = [0.0, 0.0, -alpha_deg]
            transformed.UpdatePipeline(time=latest)
            # The wall patch is an extruded side surface and can have zero
            # apparent area in the 2-D camera. Its explicit edges remain
            # visible and give the airfoil an unambiguous dark outline.
            edges = ExtractEdges(registrationName="AirfoilPatchEdges", Input=transformed)
            edges.UpdatePipeline(time=latest)
            overlay_source = edges
        else:
            Delete(reader)
            reader = None
            transformed = None
            # Some OpenFOAMReader builds expose only internalMesh. FeatureEdges
            # then provides a deterministic outline of the airfoil and farfield;
            # the latter remains outside the airfoil/wake cameras.
            edges = FeatureEdges(registrationName="DomainBoundaryOutline", Input=visual_source)
            edges.BoundaryEdges = 1
            edges.FeatureEdges = 1
            edges.NonManifoldEdges = 0
            edges.ManifoldEdges = 0
            edges.FeatureAngle = 80.0
            edges.UpdatePipeline(time=latest)
            overlay_source = edges
        overlay = Show(overlay_source, view)
        ColorBy(overlay, None)
        overlay.DiffuseColor = [0.08, 0.08, 0.08]
        overlay.AmbientColor = [0.08, 0.08, 0.08]
        overlay.LineWidth = 2.8
        wall_overlay.update({{
            "reader": reader, "transform": transformed,
            "feature_edges": overlay_source,
            "display": overlay, "regions": regions,
        }})
        return overlay
    except Exception:
        return None

def save_courant_hotspots(latest):
    association = available_array("Co")
    if association != "CELLS":
        return None
    threshold_value = 0.70 * maximum_courant
    hotspot = Threshold(registrationName="CourantHotspots", Input=source)
    hotspot.Scalars = ["CELLS", "Co"]
    try:
        hotspot.ThresholdMethod = "Between"
        hotspot.LowerThreshold = threshold_value
        hotspot.UpperThreshold = max(10.0 * maximum_courant, threshold_value + 1.0)
    except Exception:
        try:
            hotspot.ThresholdRange = [
                threshold_value,
                max(10.0 * maximum_courant, threshold_value + 1.0),
            ]
        except Exception:
            Delete(hotspot)
            return None
    hotspot.UpdatePipeline(time=latest)
    hotspot_display = Show(hotspot, view)
    hotspot_display.Representation = "Surface"
    ColorBy(hotspot_display, ("CELLS", "Co"))
    lut = GetColorTransferFunction("Co")
    lut.RescaleTransferFunction(threshold_value, max(1.2 * maximum_courant, threshold_value * 1.01))
    hotspot_display.SetScalarBarVisibility(view, True)
    Hide(source, view)
    try:
        bounds = hotspot.GetDataInformation().GetBounds()
        focal_x = 0.5 * (bounds[0] + bounds[1])
        focal_y = 0.5 * (bounds[2] + bounds[3])
        focal_z = 0.5 * (bounds[4] + bounds[5])
        span = max(bounds[1] - bounds[0], bounds[3] - bounds[2], 0.025 * chord_m)
        view.CameraFocalPoint = [focal_x, focal_y, focal_z]
        view.CameraPosition = [focal_x, focal_y, focal_z + 5.0 * max(chord_m, 1.0e-6)]
        view.CameraViewUp = [0.0, 1.0, 0.0]
        view.CameraParallelScale = max(0.15 * span, 0.005 * chord_m)
    except Exception:
        set_camera("airfoil")
    set_title("Co hotspots >= %.2g" % threshold_value, latest)
    Render(view)
    path = output_dir / ("Courant_hotspots_%s_final.png" % stage_slug)
    SaveScreenshot(str(path), view, ImageResolution=[1600, 1000])
    Hide(hotspot, view)
    Delete(hotspot)
    Show(source, view)
    return str(path.relative_to(output_dir))

products = {{
    "schema_version": 1,
    "path_base": "manifest_directory",
    "time_semantics": time_semantics,
    "frame_coordinate_kind": "SIMPLE_iteration" if is_iteration_stage else "physical_time_seconds",
    "selected_times": selected_times,
    "available_times": available_times,
    "requested_time_range_s": requested_time_range_s,
    "scale_policy": {{
        "mode": field_scale_mode,
        "robust_percentiles": robust_percentiles,
        "manual_scales": manual_scales,
        "animation_policy": "one global range over all selected frames",
    }},
}}
if selected_times:
    latest = selected_times[-1]
    scene.AnimationTime = latest
    source.UpdatePipeline(time=latest)
    streamline_source = CellDatatoPointData(
        registrationName="VisualPointFields",
        Input=visual_source,
    )
    try:
        streamline_source.PassCellData = 1
    except Exception:
        pass
    streamline_source.UpdatePipeline(time=latest)
    # OpenFOAM stores primary volume fields cell-centred. CleanToGrid removes
    # duplicate coincident points before interpolation; CellDatatoPointData is
    # still required because streamline integration needs a continuous point
    # vector field. Keep the original source as a compatibility fallback.
    try:
        native_streamline_grid = CleanToGrid(
            registrationName="NativeCleanGrid",
            Input=source,
        )
        native_streamline_grid.UpdatePipeline(time=latest)
    except Exception:
        native_streamline_grid = source
    native_streamline_source = CellDatatoPointData(
        registrationName="NativeStreamlinePointFields",
        Input=native_streamline_grid,
    )
    try:
        native_streamline_source.PassCellData = 1
    except Exception:
        pass
    native_streamline_source.UpdatePipeline(time=latest)
    streamlines = StreamTracer(
        registrationName="FreestreamSeededStreamlines",
        Input=native_streamline_source,
        SeedType="Line",
    )
    # Integrate in the physical OpenFOAM frame. StreamTracer can return an
    # empty output when its vector field is supplied through Transform; rotate
    # the finished trajectories only for display.
    alpha_rad = math.radians(alpha_deg)
    flow_x = math.cos(alpha_rad)
    flow_y = math.sin(alpha_rad)
    normal_x = -flow_y
    normal_y = flow_x
    seed_center_x = -1.0 * chord_m * flow_x
    seed_center_y = -1.0 * chord_m * flow_y
    seed_lower_length = 0.60 * chord_m
    seed_upper_length = 0.45 * chord_m
    try:
        bounds = visual_source.GetDataInformation().GetBounds()
        seed_z = 0.5 * (bounds[4] + bounds[5])
    except Exception:
        seed_z = 0.0
    # Isolines must be computed on the physical 2-D mid-plane. Contouring the
    # one-cell-thick extruded volume produces isosurfaces that are edge-on in
    # the aerodynamic camera and therefore appear blank once the filled field
    # is hidden.
    contour_plane = Slice(
        registrationName="AerodynamicMidPlane", Input=streamline_source
    )
    contour_plane.SliceType = "Plane"
    contour_plane.SliceType.Origin = [0.0, 0.0, seed_z]
    contour_plane.SliceType.Normal = [0.0, 0.0, 1.0]
    contour_plane.UpdatePipeline(time=latest)
    streamlines.SeedType.Point1 = [
        seed_center_x - seed_lower_length * normal_x,
        seed_center_y - seed_lower_length * normal_y,
        seed_z,
    ]
    streamlines.SeedType.Point2 = [
        seed_center_x + seed_upper_length * normal_x,
        seed_center_y + seed_upper_length * normal_y,
        seed_z,
    ]
    streamlines.SeedType.Resolution = 100
    streamlines.Vectors = ["POINTS", "U"]
    try:
        # The OpenFOAM case is a one-cell-thick volume, not a surface mesh.
        # SurfaceStreamlines can lock trajectories to an internal block face
        # that visually resembles the outer boundary-layer envelope.
        streamlines.SurfaceStreamlines = 0
    except Exception:
        pass
    try:
        streamlines.IntegrationDirection = "BOTH"
    except Exception:
        pass
    try:
        streamlines.MaximumStreamlineLength = 8.0 * chord_m
    except Exception:
        pass
    streamlines.UpdatePipeline(time=latest)
    streamline_information = streamlines.GetDataInformation()
    streamline_point_count = int(streamline_information.GetNumberOfPoints())
    streamline_cell_count = int(streamline_information.GetNumberOfCells())
    display_streamlines = Transform(
        registrationName="RotatedFreestreamStreamlines",
        Input=streamlines,
    )
    display_streamlines.Transform.Rotate = [0.0, 0.0, -alpha_deg]
    # Lift the displayed trajectories above the front face of the single-cell
    # extrusion. Their physical integration remains in the native case frame.
    display_streamlines.Transform.Translate = [
        0.0, 0.0, (bounds[5] - seed_z) + 0.001 * chord_m,
    ]
    try:
        display_streamlines.TransformAllInputVectors = 1
    except Exception:
        pass
    display_streamlines.UpdatePipeline(time=latest)
    # The z-offset above removes coplanar depth occlusion. Render the line
    # cells directly: this is thinner than tubes and AppendDatasets preserves
    # them reliably on ParaView 5.10.
    streamline_tube = display_streamlines
    streamline_display = Show(streamline_tube, view)
    try:
        streamline_display.Representation = "Wireframe"
        streamline_display.LineWidth = 1.15
        ColorBy(streamline_display, ("POINTS", "U", "Magnitude"))
        streamline_display.SetScalarBarVisibility(view, False)
        streamline_display.Opacity = 0.9
    except Exception:
        pass
    def set_streamline_visibility(visible):
        # Reusing Show(proxy) after Hide(proxy) is unreliable with some
        # ParaView 5.10 OpenGL backends. Drive the representation explicitly.
        streamline_display.Visibility = 1 if visible else 0
    products["streamlines"] = {{
        "enabled": True,
        "vector": "U",
        "seed_geometry": "line_perpendicular_to_freestream",
        "seed_center_chord": [-1.0, 0.0],
        "seed_lower_extent_chord": 0.60,
        "seed_upper_extent_chord": 0.45,
        "seed_resolution": 100,
        "near_body_seed": None,
        "rendering": "volume-integrated point-data line cells, front-offset for visibility",
        "alpha_deg": alpha_deg,
        "maximum_length_chord": 8.0,
        "output_points": streamline_point_count,
        "output_cells": streamline_cell_count,
    }}
    products["display_frame"] = {{
        "coordinate_rotation_deg_about_z": -alpha_deg,
        "vectors_rotated_with_geometry": True,
        "interpretation": "freestream parallel to display x; airfoil shown at incidence",
    }}
    set_streamline_visibility(False)
    show_airfoil_overlay(latest)
    products["cp_field"] = color_cp()
    set_camera("airfoil")
    set_title(str(products["cp_field"] or "pressure"), latest)
    Render(view)
    cp_final = output_dir / ("Cp_airfoil_%s_final.png" % stage_slug)
    SaveScreenshot(str(cp_final), view, ImageResolution=[1600, 1000])
    products["cp_final_png"] = str(cp_final.relative_to(output_dir))
    products["cp_streamlines_final_png"] = None

    products["velocity_field"] = color_velocity()
    set_camera("wake")
    scene.AnimationTime = latest
    source.UpdatePipeline(time=latest)
    set_title("|U|", latest)
    Render(view)
    velocity_final = output_dir / ("Velocity_%s_final.png" % stage_slug)
    SaveScreenshot(str(velocity_final), view, ImageResolution=[1600, 1000])
    products["velocity_final_png"] = str(velocity_final.relative_to(output_dir))
    products["velocity_streamlines_final_png"] = None
    set_streamline_visibility(True)
    Hide(visual_source, view)
    streamline_lut = GetColorTransferFunction("U")
    velocity_bounds = field_range("U", available_array("U"), True)
    if velocity_bounds is not None:
        streamline_lut.RescaleTransferFunction(*velocity_bounds)
    streamline_display.SetScalarBarVisibility(view, True)
    style_scalar_bar(streamline_lut, "|U| [m/s]")
    show_airfoil_overlay(latest)
    view.CameraFocalPoint = [0.5 * chord_m, 0.0, seed_z]
    view.CameraPosition = [0.5 * chord_m, 0.0, seed_z + 5.0 * max(chord_m, 1.0e-6)]
    view.CameraViewUp = [0.0, 1.0, 0.0]
    view.CameraParallelScale = max(0.58 * chord_m, 1.0e-6)
    set_title("velocity streamlines", latest)
    Render(view)
    streamline_final = output_dir / ("Velocity_streamlines_%s_final.png" % stage_slug)
    SaveScreenshot(str(streamline_final), view, ImageResolution=[1600, 1000])
    products["streamlines_final_png"] = str(streamline_final.relative_to(output_dir))
    streamline_display.SetScalarBarVisibility(view, False)
    set_streamline_visibility(False)
    Show(visual_source, view)

    # Boundary-layer containment audit at the aft airfoil.  Showing mesh edges
    # on top of |U| makes the resolved shear layer and the prism-to-triangle
    # interface directly inspectable without loading the whole case manually.
    try:
        display.Representation = "Surface With Edges"
        aft_x = 0.90 * chord_m * math.cos(math.radians(alpha_deg))
        aft_y = -0.90 * chord_m * math.sin(math.radians(alpha_deg))
        view.CameraFocalPoint = [aft_x, aft_y, seed_z]
        view.CameraPosition = [aft_x, aft_y, seed_z + 5.0 * max(chord_m, 1.0e-6)]
        view.CameraViewUp = [0.0, 1.0, 0.0]
        velocity_field = color_velocity(display, instantaneous=True)
        velocity_lut = GetColorTransferFunction(str(velocity_field["name"]))
        velocity_lut.RescaleTransferFunction(0.0, max(1.05 * velocity_m_s, 1.0e-6))
        view.CameraParallelScale = max(0.11 * chord_m, 1.0e-6)
        set_title("|U| and mesh: aft boundary layer", latest)
        Render(view)
        boundary_layer_final = output_dir / ("Velocity_BL_TE_mesh_%s_final.png" % stage_slug)
        SaveScreenshot(str(boundary_layer_final), view, ImageResolution=[1600, 1000])
        products["velocity_boundary_layer_te_mesh_png"] = str(
            boundary_layer_final.relative_to(output_dir)
        )
    except Exception as exc:
        products["velocity_boundary_layer_te_mesh"] = {{
            "status": "UNAVAILABLE", "reason": str(exc)
        }}
    finally:
        try:
            display.Representation = "Surface"
        except Exception:
            pass

    products["velocity_contours"] = {{"status": "UNAVAILABLE"}}
    velocity_contours = None
    velocity_contour_name = "U"
    if available_array(velocity_contour_name):
        try:
            velocity_magnitude = Calculator(
                registrationName="VelocityMagnitude",
                Input=contour_plane,
            )
            velocity_magnitude.ResultArrayName = "UMagnitude"
            velocity_magnitude.Function = "mag(%s)" % velocity_contour_name
            velocity_magnitude.UpdatePipeline(time=latest)
            velocity_contours = Contour(
                registrationName="VelocityMagnitudeContours",
                Input=velocity_magnitude,
            )
            velocity_contours.ContourBy = ["POINTS", "UMagnitude"]
            velocity_range = robust_range(
                velocity_contour_name,
                available_array(velocity_contour_name),
                True,
            )
            if velocity_range is None or velocity_range[1] <= velocity_range[0]:
                velocity_range = (0.0, max(1.15 * velocity_m_s, 1.0e-6))
            velocity_contours.Isosurfaces = [
                velocity_range[0]
                + (velocity_range[1] - velocity_range[0]) * i / 21.0
                for i in range(1, 21)
            ]
            velocity_contours.UpdatePipeline(time=latest)
            velocity_contour_front = Transform(
                registrationName="VelocityContoursFront", Input=velocity_contours
            )
            velocity_contour_front.Transform.Translate = [
                0.0, 0.0, (bounds[5] - seed_z) + 0.002 * chord_m,
            ]
            velocity_contour_front.UpdatePipeline(time=latest)
            Hide(visual_source, view)
            contour_display = Show(velocity_contour_front, view)
            ColorBy(contour_display, ("POINTS", "UMagnitude"))
            velocity_contour_lut = GetColorTransferFunction("UMagnitude")
            velocity_contour_lut.RescaleTransferFunction(*velocity_range)
            contour_display.LineWidth = 2.0
            contour_display.Opacity = 0.95
            contour_display.SetScalarBarVisibility(view, True)
            style_scalar_bar(velocity_contour_lut, "|U| [m/s]")
            set_camera("nearfield")
            set_title("|U| contours: near-airfoil", latest)
            show_airfoil_overlay(latest)
            Render(view)
            nearfield_contour_final = output_dir / (
                "Velocity_contours_%s_final.png" % stage_slug
            )
            SaveScreenshot(str(nearfield_contour_final), view, ImageResolution=[1600, 1000])
            products["velocity_contours"]["nearfield_image"] = str(
                nearfield_contour_final.relative_to(output_dir)
            )
            products["velocity_contours"].update({{
                "array": velocity_contour_name,
                "association": "POINTS",
                "isovalues_m_s": list(velocity_contours.Isosurfaces),
                "image": str(nearfield_contour_final.relative_to(output_dir)),
                "view_x_over_c": [-0.5, 2.0],
            }})
            contour_display.SetScalarBarVisibility(view, False)
            Hide(velocity_contour_front, view)
            Show(visual_source, view)
            set_streamline_visibility(False)
            Delete(velocity_contour_front)
            Delete(velocity_contours)
            Delete(velocity_magnitude)
        except Exception as exc:
            products["velocity_contours"] = {{"status": "UNAVAILABLE", "reason": str(exc)}}

    pressure_name = (
        "Cp" if available_array("Cp")
        else "p" if available_array("p")
        else None
    )
    products["pressure_contours"] = {{"status": "UNAVAILABLE"}}
    pressure_contours = None
    pressure_contour_front = None
    pressure_display = None
    if pressure_name:
        try:
            pressure_range = robust_range(
                pressure_name, available_array(pressure_name), False
            )
            if pressure_range is not None and pressure_range[1] > pressure_range[0]:
                pressure_contours = Contour(
                    registrationName="PressureContours",
                    Input=contour_plane,
                )
                pressure_contours.ContourBy = ["POINTS", pressure_name]
                pressure_contours.Isosurfaces = [
                    pressure_range[0] + (pressure_range[1] - pressure_range[0]) * i / 21.0
                    for i in range(1, 21)
                ]
                pressure_contours.UpdatePipeline(time=latest)
                pressure_contour_front = Transform(
                    registrationName="PressureContoursFront", Input=pressure_contours
                )
                pressure_contour_front.Transform.Translate = [
                    0.0, 0.0, (bounds[5] - seed_z) + 0.002 * chord_m,
                ]
                pressure_contour_front.UpdatePipeline(time=latest)
                Hide(visual_source, view)
                pressure_display = Show(pressure_contour_front, view)
                ColorBy(pressure_display, ("POINTS", pressure_name))
                pressure_lut = GetColorTransferFunction(pressure_name)
                pressure_lut.RescaleTransferFunction(*pressure_range)
                pressure_display.LineWidth = 2.0
                pressure_display.SetScalarBarVisibility(view, True)
                style_scalar_bar(pressure_lut, "%s contours" % pressure_name)
                show_airfoil_overlay(latest)
                set_camera("nearfield")
                set_title("%s contours: near-airfoil" % pressure_name, latest)
                Render(view)
                pressure_path = output_dir / (
                    "Pressure_contours_%s_final.png" % stage_slug
                )
                SaveScreenshot(str(pressure_path), view, ImageResolution=[1600, 1000])
                products["pressure_contours"] = {{
                    "array": pressure_name,
                    "association": "POINTS",
                    "image": str(pressure_path.relative_to(output_dir)),
                    "view_x_over_c": [-0.5, 2.0],
                }}
                pressure_display.SetScalarBarVisibility(view, False)
                Hide(pressure_contour_front, view)
                Show(visual_source, view)
        except Exception as exc:
            products["pressure_contours"] = {{"status": "UNAVAILABLE", "reason": str(exc)}}

    vorticity_contours = None
    vorticity_contour_front = None
    vorticity_contour_display = None
    vorticity_name = "vorticity"
    vorticity_field = color_scalar_field(vorticity_name, vector_magnitude=True)
    if vorticity_field:
        set_streamline_visibility(False)
        visual_vorticity_range = robust_range(
            vorticity_name, vorticity_field["association"], True
        )
        if visual_vorticity_range is not None:
            visual_vorticity_upper = max(
                1.0e-12, 0.07 * visual_vorticity_range[1]
            )
            GetColorTransferFunction("vorticity").RescaleTransferFunction(
                0.0, visual_vorticity_upper
            )
            products["vorticity_visual_upper"] = visual_vorticity_upper
        set_camera("wake")
        set_title("|vorticity|", latest)
        Render(view)
        vorticity_final = output_dir / ("Vorticity_%s_final.png" % stage_slug)
        SaveScreenshot(str(vorticity_final), view, ImageResolution=[1600, 1000])
        products["vorticity_field"] = vorticity_field
        products["vorticity_final_png"] = str(vorticity_final.relative_to(output_dir))
        try:
            vorticity_calculator = Calculator(
                registrationName="VorticityMagnitude",
                Input=contour_plane,
            )
            vorticity_calculator.ResultArrayName = "vorticityMagnitude"
            vorticity_calculator.Function = "mag(%s)" % vorticity_name
            vorticity_calculator.UpdatePipeline(time=latest)
            vorticity_range = field_range(
                vorticity_name, vorticity_field["association"], True
            )
            if vorticity_range is not None:
                robust_vorticity = robust_range(
                    vorticity_name, vorticity_field["association"], True
                ) or vorticity_range
                display_upper = max(1.0e-12, 0.07 * robust_vorticity[1])
                if display_upper > max(0.0, robust_vorticity[0]):
                    vorticity_contours = Contour(
                        registrationName="VorticityMagnitudeContours",
                        Input=vorticity_calculator,
                    )
                    vorticity_contours.ContourBy = ["POINTS", "vorticityMagnitude"]
                    vorticity_contours.Isosurfaces = [
                        display_upper * (i + 1) / 20.0 for i in range(20)
                    ]
                    vorticity_contours.UpdatePipeline(time=latest)
                    vorticity_contour_front = Transform(
                        registrationName="VorticityContoursFront",
                        Input=vorticity_contours,
                    )
                    vorticity_contour_front.Transform.Translate = [
                        0.0, 0.0, (bounds[5] - seed_z) + 0.002 * chord_m,
                    ]
                    vorticity_contour_front.UpdatePipeline(time=latest)
                    Hide(visual_source, view)
                    set_streamline_visibility(False)
                    vorticity_contour_display = Show(
                        vorticity_contour_front, view
                    )
                    ColorBy(
                        vorticity_contour_display,
                        ("POINTS", "vorticityMagnitude"),
                    )
                    vorticity_contour_lut = GetColorTransferFunction(
                        "vorticityMagnitude"
                    )
                    vorticity_contour_lut.RescaleTransferFunction(
                        0.0, display_upper
                    )
                    vorticity_contour_display.LineWidth = 2.0
                    vorticity_contour_display.SetScalarBarVisibility(view, True)
                    style_scalar_bar(
                        vorticity_contour_lut, "|vorticity| [1/s]"
                    )
                    show_airfoil_overlay(latest)
                    set_camera("nearfield")
                    set_title("|vorticity| contours: near-airfoil", latest)
                    Render(view)
                    vorticity_contour_path = output_dir / (
                        "Vorticity_contours_%s_final.png" % stage_slug
                    )
                    SaveScreenshot(
                        str(vorticity_contour_path), view,
                        ImageResolution=[1600, 1000],
                    )
                    products["vorticity_contours"] = {{
                        "array": "vorticityMagnitude",
                        "association": "POINTS",
                        "image": str(vorticity_contour_path.relative_to(output_dir)),
                        "view_x_over_c": [-0.5, 2.0],
                    }}
                    vorticity_contour_display.SetScalarBarVisibility(view, False)
                    Hide(vorticity_contour_front, view)
                    Show(visual_source, view)
                lower = max(0.0, robust_vorticity[0], 0.01 * display_upper)
                high_vorticity = Threshold(
                    registrationName="HighVorticity",
                    Input=vorticity_calculator,
                )
                high_vorticity.Scalars = ["POINTS", "vorticityMagnitude"]
                try:
                    high_vorticity.LowerThreshold = lower
                    high_vorticity.UpperThreshold = vorticity_range[1]
                except Exception:
                    high_vorticity.ThresholdRange = [lower, vorticity_range[1]]
                high_vorticity.UpdatePipeline(time=latest)
                Hide(visual_source, view)
                set_streamline_visibility(False)
                high_display = Show(high_vorticity, view)
                ColorBy(high_display, ("POINTS", "vorticityMagnitude"))
                GetColorTransferFunction("vorticityMagnitude").RescaleTransferFunction(
                    lower, display_upper
                )
                high_display.SetScalarBarVisibility(view, True)
                vorticity_bar = GetScalarBar(
                    GetColorTransferFunction("vorticityMagnitude"), view
                )
                vorticity_bar.Title = "|vorticity| [1/s]"
                vorticity_bar.ComponentTitle = ""
                vorticity_bar.WindowLocation = "Lower Right Corner"
                vorticity_bar.TitleFontSize = 12
                vorticity_bar.LabelFontSize = 10
                vorticity_bar.ScalarBarLength = 0.30
                try:
                    vorticity_bar.TitleColor = [0.0, 0.0, 0.0]
                    vorticity_bar.LabelColor = [0.0, 0.0, 0.0]
                except Exception:
                    pass
                set_camera("wake")
                show_airfoil_overlay(latest)
                set_streamline_visibility(False)
                if vorticity_contour_display is not None:
                    vorticity_contour_display.Visibility = 1
                set_title("High-vorticity regions with vorticity contours", latest)
                Render(view)
                threshold_png = output_dir / ("Vorticity_threshold_%s_final.png" % stage_slug)
                SaveScreenshot(str(threshold_png), view, ImageResolution=[1600, 1000])
                products["vorticity_threshold_png"] = str(threshold_png.relative_to(output_dir))
                products["vorticity_threshold_minimum"] = lower
                if vorticity_contour_display is not None:
                    vorticity_contour_display.Visibility = 0
                Hide(high_vorticity, view)
                Show(visual_source, view)
                set_streamline_visibility(False)
                Delete(high_vorticity)
            Delete(vorticity_calculator)
        except Exception as exc:
            try:
                Show(visual_source, view)
                set_streamline_visibility(False)
            except Exception:
                pass
            products["vorticity_threshold"] = {{"status": "UNAVAILABLE", "reason": str(exc)}}

    q_field = color_scalar_field("Q")
    if q_field:
        products["q_field"] = q_field
        try:
            q_bounds = field_range("Q", q_field["association"])
            if q_bounds is not None and q_bounds[1] > 0.0:
                # A geometric Contour of point-interpolated Q is empty for some
                # one-cell-thick OpenFOAM meshes. Thresholding cell-centred Q>0
                # preserves the actual positive regions and is the robust 2-D
                # equivalent of displaying Q-positive vortex cores.
                q_positive = Threshold(registrationName="PositiveQRegions", Input=visual_source)
                q_positive.Scalars = [q_field["association"], "Q"]
                try:
                    q_positive.ThresholdMethod = "Between"
                    q_positive.LowerThreshold = 0.0
                    q_positive.UpperThreshold = q_bounds[1]
                except Exception:
                    q_positive.ThresholdRange = [0.0, q_bounds[1]]
                q_positive.UpdatePipeline(time=latest)
                Hide(visual_source, view)
                q_display = Show(q_positive, view)
                ColorBy(q_display, (q_field["association"], "Q"))
                robust_q = robust_range("Q", q_field["association"]) or q_bounds
                q_upper = max(
                    1.0e-12,
                    0.35 * robust_q[1] if robust_q[1] > 0.0 else q_bounds[1],
                )
                q_display_minimum = 0.01 * q_upper
                try:
                    q_positive.LowerThreshold = q_display_minimum
                except Exception:
                    q_positive.ThresholdRange = [q_display_minimum, q_bounds[1]]
                q_positive.UpdatePipeline(time=latest)
                GetColorTransferFunction("Q").RescaleTransferFunction(0.0, q_upper)
                q_display.SetScalarBarVisibility(view, True)
                style_scalar_bar(GetColorTransferFunction("Q"), "Q [1/s^2]")
                set_camera("wake")
                view.CameraFocalPoint = [1.15 * chord_m, 0.0, seed_z]
                view.CameraPosition = [
                    1.15 * chord_m, 0.0,
                    seed_z + 5.0 * max(chord_m, 1.0e-6),
                ]
                view.CameraParallelScale = max(0.90 * chord_m, 1.0e-6)
                show_airfoil_overlay(latest)
                set_streamline_visibility(False)
                if pressure_display is not None:
                    pressure_display.Visibility = 1
                    pressure_display.SetScalarBarVisibility(view, False)
                    set_title("Q-positive regions with pressure contours", latest)
                    Render(view)
                    q_pressure_png = output_dir / (
                        "Q_pressure_contours_%s_final.png" % stage_slug
                    )
                    SaveScreenshot(
                        str(q_pressure_png), view, ImageResolution=[1600, 1000]
                    )
                    products["q_pressure_contours_png"] = str(
                        q_pressure_png.relative_to(output_dir)
                    )
                    pressure_display.Visibility = 0
                if vorticity_contour_display is not None:
                    vorticity_contour_display.Visibility = 1
                    vorticity_contour_display.SetScalarBarVisibility(view, False)
                    set_title("Q-positive regions with vorticity contours", latest)
                    Render(view)
                    q_vorticity_png = output_dir / (
                        "Q_vorticity_contours_%s_final.png" % stage_slug
                    )
                    SaveScreenshot(
                        str(q_vorticity_png), view, ImageResolution=[1600, 1000]
                    )
                    products["q_vorticity_contours_png"] = str(
                        q_vorticity_png.relative_to(output_dir)
                    )
                    vorticity_contour_display.Visibility = 0
                products["q_positive_filter"] = "Threshold(Q >= 0) for one-cell-thick 2-D mesh"
                products["q_positive_threshold"] = q_display_minimum
                products["q_positive_policy"] = (
                    "Q>0 with a 1% robust-colour-range display floor to suppress numerical speckle"
                )
                products["q_positive_colour_upper"] = q_upper
                Hide(q_positive, view)
                Show(visual_source, view)
                set_streamline_visibility(False)
                Delete(q_positive)
        except Exception as exc:
            try:
                Show(visual_source, view)
                set_streamline_visibility(False)
            except Exception:
                pass
            products["q_positive_contour"] = {{"status": "UNAVAILABLE", "reason": str(exc)}}

    products["courant_policy"] = "NOT_APPLICABLE_TO_RANS" if is_iteration_stage else "URANS_ONLY"
    if not is_iteration_stage:
        products["courant_field"] = color_courant()
        if products["courant_field"]:
            actual_courant_max = None
            try:
                scene.AnimationTime = latest
                visual_source.UpdatePipeline(time=latest)
                courant_association = available_array("Co")
                courant_information = (
                    visual_source.GetCellDataInformation()
                    if courant_association == "CELLS"
                    else visual_source.GetPointDataInformation()
                )
                courant_array = courant_information.GetArray("Co")
                actual_courant_max = float(courant_array.GetComponentRange(0)[1])
            except Exception:
                actual_courant_max = None
            try:
                display.Representation = "Surface With Edges"
                display.EdgeColor = [0.15, 0.15, 0.15]
                display.LineWidth = 0.6
            except Exception:
                pass
            set_camera("airfoil")
            scene.AnimationTime = latest
            source.UpdatePipeline(time=latest)
            courant_title = "cell Courant number Co"
            if actual_courant_max is not None and math.isfinite(actual_courant_max):
                courant_title += " | max Co = %.1f" % actual_courant_max
            set_title(courant_title, latest)
            Render(view)
            courant_final = output_dir / ("Courant_%s_final.png" % stage_slug)
            SaveScreenshot(str(courant_final), view, ImageResolution=[1600, 1000])
            products["courant_final_png"] = str(courant_final.relative_to(output_dir))
            products["courant_colour_range"] = [0.0, 10.0]
            products["courant_instantaneous_max"] = actual_courant_max
            products["courant_hotspots_png"] = None
            products["courant_hotspots_policy"] = "not_generated; final Courant field only"
            try:
                display.Representation = "Surface"
            except Exception:
                pass

    set_streamline_visibility(False)
    color_velocity(instantaneous=True)
    set_camera("wake")
    for index, value in enumerate(selected_times if include_animations else []):
        scene.AnimationTime = value
        source.UpdatePipeline(time=value)
        title_source.Text = "%s | |U| | %s = %.6g" % (
            stage_label,
            frame_axis_label,
            value,
        )
        Render(view)
        SaveScreenshot(
            str(velocity_dir / ("velocity_%04d.png" % index)),
            view,
            ImageResolution=[1280, 720],
        )

    products["pressure_field"] = color_cp(instantaneous=True)
    set_camera("wake")
    for index, value in enumerate(selected_times if include_animations else []):
        scene.AnimationTime = value
        source.UpdatePipeline(time=value)
        title_source.Text = "%s | Cp | %s = %.6g" % (
            stage_label,
            frame_axis_label,
            value,
        )
        Render(view)
        SaveScreenshot(
            str(pressure_dir / ("pressure_Cp_%04d.png" % index)),
            view,
            ImageResolution=[1280, 720],
        )
    if pressure_display is not None and pressure_contour_front is not None:
        Hide(visual_source, view)
        pressure_display.Visibility = 1
        pressure_display.SetScalarBarVisibility(view, True)
        set_camera("nearfield")
        show_airfoil_overlay(latest)
        for index, value in enumerate(selected_times if include_animations else []):
            scene.AnimationTime = value
            source.UpdatePipeline(time=value)
            pressure_contour_front.UpdatePipeline(time=value)
            title_source.Text = "%s | pressure contours | %s = %.6g" % (
                stage_label, frame_axis_label, value,
            )
            Render(view)
            SaveScreenshot(
                str(pressure_contour_dir / ("pressure_contours_%04d.png" % index)),
                view, ImageResolution=[1280, 720],
            )
        pressure_display.SetScalarBarVisibility(view, False)
        pressure_display.Visibility = 0
        Show(visual_source, view)

    if vorticity_contour_display is not None and vorticity_contour_front is not None:
        Hide(visual_source, view)
        vorticity_contour_display.Visibility = 1
        vorticity_contour_display.SetScalarBarVisibility(view, True)
        set_camera("nearfield")
        show_airfoil_overlay(latest)
        for index, value in enumerate(selected_times if include_animations else []):
            scene.AnimationTime = value
            source.UpdatePipeline(time=value)
            vorticity_contour_front.UpdatePipeline(time=value)
            title_source.Text = "%s | vorticity contours | %s = %.6g" % (
                stage_label, frame_axis_label, value,
            )
            Render(view)
            SaveScreenshot(
                str(vorticity_contour_dir / ("vorticity_contours_%04d.png" % index)),
                view, ImageResolution=[1280, 720],
            )
        vorticity_contour_display.SetScalarBarVisibility(view, False)
        vorticity_contour_display.Visibility = 0
        Show(visual_source, view)

products["status"] = "RENDERED" if selected_times else "NO_WRITTEN_TIMES"
products["frame_count"] = len(selected_times) if include_animations else 0
products["animation_generation"] = "included" if include_animations else "deferred"
products["applied_scales"] = scale_evidence
scales_path = output_dir / "visualization_scales.json"
scales_path.write_text(
    json.dumps({{
        "schema_version": 1,
        "path_base": "manifest_directory",
        "selected_times": selected_times,
        "range_times": range_times,
        "policy": products["scale_policy"],
        "fields": scale_evidence,
        "shared_by": (
            ["final_images", "animation_frames"]
            if include_animations else ["final_images"]
        ),
    }}, indent=2) + "\\n",
    encoding="utf-8",
)
products["visualization_scales"] = scales_path.name
relative_foam_path = os.path.relpath(foam_path, output_dir).replace("\\\\", "/")
products["case_reference"] = relative_foam_path
# SaveState can block for many minutes with OpenFOAM readers under WSL. The
# automatic path therefore writes images/animations only. A portable .foam
# marker remains the authoritative interactive entry point; state export is
# an explicit opt-in for users who accept its additional cost.
if os.environ.get("RAMAIR_PVBATCH_SAVE_STATE", "0") == "1":
    state_path = output_dir / ("final_%s.pvsm" % stage_slug)
    SaveState(str(state_path))
    try:
        state_text = state_path.read_text(encoding="utf-8")
        state_text = state_text.replace(foam_path, relative_foam_path)
        state_text = state_text.replace(foam_path.replace("/", "\\\\"), relative_foam_path)
        state_path.write_text(state_text, encoding="utf-8")
    except Exception:
        pass
    products["state"] = state_path.name
else:
    products["state"] = None
    products["state_policy"] = "disabled_for_automatic_batch_to_avoid_WSL_SaveState_stall"
(output_dir / "paraview_products.json").write_text(
    json.dumps(products, indent=2) + "\\n",
    encoding="utf-8",
)
'''
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text(script, encoding="utf-8")
    return script_path


def _encode_frame_sequence(
    frame_pattern: Path,
    output_path: Path,
    *,
    frame_rate: int = 2,
) -> dict[str, Any]:
    executable = shutil.which("ffmpeg")
    if not executable:
        try:
            from PIL import Image

            frame_glob = frame_pattern.name.replace("%04d", "*")
            frames = sorted(frame_pattern.parent.glob(frame_glob))
            images = [Image.open(path).convert("RGB") for path in frames]
            gif_path = output_path.with_suffix(".gif")
            if images:
                images[0].save(
                    gif_path,
                    save_all=True,
                    append_images=images[1:],
                    duration=max(40, int(round(1000 / max(1, frame_rate)))),
                    loop=0,
                    optimize=True,
                )
            for image in images:
                image.close()
            return {
                "status": "ENCODED_GIF" if gif_path.is_file() else "FRAMES_ONLY",
                "reason": "ffmpeg_not_found_pillow_fallback",
                "frame_pattern": str(frame_pattern),
                "output": str(gif_path) if gif_path.is_file() else None,
            }
        except (ImportError, OSError) as exc:
            return {
                "status": "FRAMES_ONLY",
                "reason": f"ffmpeg_and_gif_encoder_unavailable: {type(exc).__name__}: {exc}",
                "frame_pattern": str(frame_pattern),
            }
    command = [
        executable,
        "-y",
        "-loglevel",
        "error",
        "-framerate",
        str(max(1, int(frame_rate))),
        "-i",
        str(frame_pattern),
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        str(output_path),
    ]
    completed = subprocess.run(
        command,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=180,
        check=False,
    )
    return {
        "status": "ENCODED" if completed.returncode == 0 and output_path.is_file() else "ENCODE_FAILED",
        "command": command,
        "returncode": int(completed.returncode),
        "output": str(output_path) if output_path.is_file() else None,
        "log_tail": (completed.stdout or "")[-3000:],
    }


def generate_automatic_paraview_products(
    case_dir: Path,
    output_dir: Path,
    *,
    maximum_frames: int = 24,
    timeout_s: int = 600,
    time_semantics: str = "physical seconds",
    stage_label: str = "URANS",
    field_scale_mode: str = "exact",
    robust_percentiles: tuple[float, float] = (1.0, 99.0),
    manual_scales: dict[str, tuple[float, float]] | None = None,
    time_range_s: tuple[float, float] | None = None,
    include_animations: bool = True,
    alpha_deg_override: float | None = None,
) -> dict[str, Any]:
    """Render the final diagnostic set and, optionally, bounded animations."""
    activate_openfoam_environment()
    case_dir = case_dir.resolve()
    output_dir = output_dir.resolve()
    executable = os.environ.get("RAMAIR_PVBATCH_EXECUTABLE") or shutil.which("pvbatch")
    if not executable:
        return {"status": "SKIPPED", "reason": "pvbatch_not_found", "case_dir": str(case_dir)}
    if not (case_dir / "system" / "controlDict").is_file():
        return {"status": "SKIPPED", "reason": "openfoam_case_missing", "case_dir": str(case_dir)}
    marker = case_dir / f"{case_dir.name}.foam"
    marker.touch(exist_ok=True)
    inputs = read_json(case_dir / "case_input_summary.json")
    inputs = {**read_json(case_dir / "case_config.json"), **inputs}
    chord = case_chord_m(case_dir) or 1.0
    try:
        velocity = float(inputs.get("velocity_m_s", 1.0))
    except (TypeError, ValueError):
        velocity = 1.0
    try:
        alpha_deg = float(
            alpha_deg_override
            if alpha_deg_override is not None
            else inputs.get("alpha_deg", 0.0)
        )
    except (TypeError, ValueError):
        alpha_deg = 0.0
    try:
        maximum_courant = float(inputs.get("maxCo", 1.0))
    except (TypeError, ValueError):
        maximum_courant = 1.0
    effective_time_range = time_range_s
    if effective_time_range is None and str(stage_label).strip().upper() == "URANS":
        ceiling = case_physical_time_ceiling_s(case_dir)
        if ceiling is not None:
            effective_time_range = (0.0, ceiling * (1.0 + 1.0e-8))
    # This directory is a reproducible generated product package. Remove only
    # old visual assets so obsolete views cannot reappear in the application
    # after a new post-process run.
    output_dir.mkdir(parents=True, exist_ok=True)
    if include_animations:
        for asset in output_dir.rglob("*"):
            if asset.is_file() and asset.suffix.lower() in {".png", ".mp4", ".webm", ".gif"}:
                asset.unlink(missing_ok=True)
        for frame_directory in (
            "velocity_frames", "pressure_frames",
            "pressure_contour_frames", "vorticity_contour_frames",
        ):
            shutil.rmtree(output_dir / frame_directory, ignore_errors=True)
    else:
        for asset in output_dir.glob("*"):
            if asset.is_file() and asset.suffix.lower() == ".png":
                asset.unlink(missing_ok=True)
    script = write_automatic_products_script(
        output_dir / "render_automatic_products.py",
        marker,
        output_dir,
        chord_m=chord,
        velocity_m_s=max(velocity, 1.0e-9),
        alpha_deg=alpha_deg,
        maximum_courant=max(maximum_courant, 1.0e-6),
        maximum_frames=maximum_frames,
        time_semantics=time_semantics,
        stage_label=stage_label,
        field_scale_mode=field_scale_mode,
        robust_percentiles=robust_percentiles,
        manual_scales=manual_scales,
        time_range_s=effective_time_range,
        include_animations=include_animations,
    )
    command = [str(executable), "--force-offscreen-rendering", str(script)]
    started = time.monotonic()
    process = subprocess.Popen(
            command,
            cwd=str(case_dir),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=paraview_environment(),
            start_new_session=True,
        )
    try:
        stdout, _ = process.communicate(timeout=max(30, int(timeout_s)))
        completed_returncode = process.returncode
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        try:
            os.killpg(process.pid, signal.SIGTERM)
            stdout, _ = process.communicate(timeout=10)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            stdout, _ = process.communicate()
        completed_returncode = None
        log_text = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
        if stdout:
            log_text += stdout
    else:
        log_text = stdout or ""
    output_dir.mkdir(parents=True, exist_ok=True)
    log_path = output_dir / "log.pvbatch_automatic_products"
    log_path.write_text(log_text, encoding="utf-8", errors="replace")
    manifest = read_json(output_dir / "paraview_products.json")
    velocity_video = _encode_frame_sequence(
        output_dir / "velocity_frames" / "velocity_%04d.png",
        output_dir / "velocity_airfoil_wake.mp4",
    ) if include_animations and manifest.get("frame_count", 0) >= 2 else {"status": "SKIPPED", "reason": "animations_deferred" if not include_animations else "fewer_than_two_frames"}
    pressure_video = _encode_frame_sequence(
        output_dir / "pressure_frames" / "pressure_Cp_%04d.png",
        output_dir / "pressure_Cp_airfoil_wake.mp4",
    ) if include_animations and manifest.get("frame_count", 0) >= 2 else {"status": "SKIPPED", "reason": "animations_deferred" if not include_animations else "fewer_than_two_frames"}
    pressure_contour_video = _encode_frame_sequence(
        output_dir / "pressure_contour_frames" / "pressure_contours_%04d.png",
        output_dir / "pressure_contours.mp4",
    ) if include_animations and manifest.get("frame_count", 0) >= 2 else {"status": "SKIPPED", "reason": "animations_deferred" if not include_animations else "fewer_than_two_frames"}
    vorticity_contour_video = _encode_frame_sequence(
        output_dir / "vorticity_contour_frames" / "vorticity_contours_%04d.png",
        output_dir / "vorticity_contours.mp4",
    ) if include_animations and manifest.get("frame_count", 0) >= 2 else {"status": "SKIPPED", "reason": "animations_deferred" if not include_animations else "fewer_than_two_frames"}
    report = {
        "status": (
            "TIMEOUT_PARTIAL"
            if timed_out
            else "OK"
            if completed_returncode == 0 and manifest.get("status") == "RENDERED"
            else "FAILED"
        ),
        "command": command,
        "returncode": completed_returncode,
        "elapsed_s": time.monotonic() - started,
        "log": str(log_path),
        "products": manifest,
        "velocity_animation": velocity_video,
        "pressure_animation": pressure_video,
        "pressure_contour_animation": pressure_contour_video,
        "vorticity_contour_animation": vorticity_contour_video,
        "animation_frame_rate_fps": 2,
        "include_animations": bool(include_animations),
        "storage_strategy": "Direct OpenFOAM reader; no duplicated VTK volume database.",
    }
    report_name = (
        "automatic_products_report.json"
        if include_animations else "automatic_static_products_report.json"
    )
    (output_dir / report_name).write_text(
        json.dumps(report, indent=2) + "\n",
        encoding="utf-8",
    )
    return report


def launch_paraview_case(case_dir: Path) -> dict[str, Any]:
    """Launch ParaView with a real reader, latest time and deterministic view."""
    activate_openfoam_environment()
    case_dir = case_dir.resolve()
    if sys.platform.startswith("linux") and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return {"status": "OPEN_SKIPPED", "reason": "no WSLg/X11 display available", "case_dir": str(case_dir)}
    executable = os.environ.get("RAMAIR_PARAVIEW_EXECUTABLE") or shutil.which("paraview")
    if not executable:
        return {"status": "OPEN_SKIPPED", "reason": "paraview not found on PATH", "case_dir": str(case_dir)}

    prepared = prepare_paraview_case(case_dir)
    marker = Path(prepared["foam_marker"])
    script = Path(prepared["startup_script"])
    ready = Path(prepared["ready_file"])
    log_path = Path(prepared["log"])
    command = [str(executable), "--disable-registry", f"--script={script}"]
    handle = log_path.open("w", encoding="utf-8")
    process = subprocess.Popen(
        command,
        cwd=str(case_dir),
        stdout=handle,
        stderr=subprocess.STDOUT,
        env=paraview_environment(),
        start_new_session=True,
    )
    handle.close()
    time.sleep(0.8)
    returncode = process.poll()
    if returncode not in {None, 0}:
        details = log_path.read_text(encoding="utf-8", errors="replace")[-4000:]
        return {
            "status": "OPEN_FAILED",
            "returncode": int(returncode),
            "command": command,
            "log": str(log_path),
            "details": details,
        }
    if returncode is None:
        _reap_interactive_process(process, "paraview-case")
    return {
        "status": "OPEN_REQUESTED",
        "method": "scripted-paraview-openfoam-reader",
        "command": command,
        "case_dir": str(case_dir),
        "foam_marker": str(marker),
        "startup_script": str(script),
        "ready_file": str(ready),
        "screenshot": prepared["screenshot"],
        "state": prepared["state"],
        "log": str(log_path),
        "pid": process.pid,
    }


def launch_paraview_vtk_set(
    vtk_paths: list[Path],
    *,
    support_dir: Path,
    selected_time: float,
) -> dict[str, Any]:
    """Launch one exact, iteration-consistent legacy-VTK artifact set."""
    paths = [Path(path).resolve() for path in vtk_paths if Path(path).is_file()]
    if not paths:
        return {"status": "OPEN_SKIPPED", "reason": "no resolved VTK artifacts"}
    executable = os.environ.get("RAMAIR_PARAVIEW_EXECUTABLE") or shutil.which("paraview")
    if not executable:
        return {"status": "OPEN_SKIPPED", "reason": "paraview not found on PATH"}
    support = Path(support_dir).resolve()
    support.mkdir(parents=True, exist_ok=True)
    script_path = support / "open_resolved_vtk_set.py"
    screenshot = support / "resolved_vtk_set.png"
    ready_path = support / "resolved_vtk_set.ready.json"
    script = f'''from pathlib import Path
import json
from paraview.simple import *
try:
    from paraview.simple import _DisableFirstRenderCameraReset
    _DisableFirstRenderCameraReset()
except Exception:
    pass
paths = {json.dumps([str(path) for path in paths])}
view = GetActiveViewOrCreate("RenderView")
focus_bounds = []
for index, path in enumerate(paths):
    reader = LegacyVTKReader(FileNames=[path])
    reader.UpdatePipeline()
    display = Show(reader, view)
    if "/airfoil_wall/" in path.replace("\\\\", "/"):
        bounds = reader.GetDataInformation().GetBounds()
        if bounds and (bounds[1] - bounds[0]) > 0 and (bounds[3] - bounds[2]) > 0:
            focus_bounds.append(bounds)
    if index == 0:
        try:
            arrays = list(reader.PointData.keys()) + list(reader.CellData.keys())
            if "U" in arrays:
                ColorBy(display, ("POINTS", "U", "Magnitude"))
                display.RescaleTransferFunctionToDataRange(True, False)
        except Exception:
            pass
ResetCamera(view)
if focus_bounds:
    xmin = min(item[0] for item in focus_bounds); xmax = max(item[1] for item in focus_bounds)
    ymin = min(item[2] for item in focus_bounds); ymax = max(item[3] for item in focus_bounds)
    zmid = 0.5 * (min(item[4] for item in focus_bounds) + max(item[5] for item in focus_bounds))
    chord = max(xmax - xmin, 1.0e-6)
    view.CameraParallelProjection = 1
    view.CameraFocalPoint = [0.5 * (xmin + xmax), 0.5 * (ymin + ymax), zmid]
    view.CameraPosition = [0.5 * (xmin + xmax), 0.5 * (ymin + ymax), zmid + 5.0 * chord]
    view.CameraViewUp = [0.0, 1.0, 0.0]
    view.CameraParallelScale = max(0.35 * chord, 0.65 * (ymax - ymin), 1.0e-6)
Render(view)
SaveScreenshot({json.dumps(str(screenshot))}, view, ImageResolution=[1600, 1000])
Path({json.dumps(str(ready_path))}).write_text(json.dumps({{
    "status": "READY",
    "selected_time": {float(selected_time)!r},
    "reader_paths": paths,
    "screenshot": {json.dumps(str(screenshot))},
    "state_policy": "disabled_to_avoid_interactive_copy_mode_dialog",
}}, indent=2) + "\\n", encoding="utf-8")
'''
    script_path.write_text(script, encoding="utf-8")
    log_path = support / "log.paraview_vtk_launch"
    log_stream = log_path.open("a", encoding="utf-8")
    command = [str(executable), "--disable-registry", "--script", str(script_path)]
    process = subprocess.Popen(
        command,
        cwd=str(support),
        stdout=log_stream,
        stderr=subprocess.STDOUT,
        env=paraview_environment(),
        start_new_session=True,
    )
    _reap_interactive_process(process, "paraview-vtk")
    return {
        "status": "OPEN_REQUESTED",
        "pid": int(process.pid),
        "command": command,
        "method": "resolved-legacy-vtk-set",
        "selected_time": float(selected_time),
        "reader_paths": [str(path) for path in paths],
        "script": str(script_path),
        "state": None,
        "ready_file": str(ready_path),
        "state_policy": "disabled_to_avoid_interactive_copy_mode_dialog",
        "screenshot": str(screenshot),
        "log": str(log_path),
    }


def generate_vtk_final_screenshots(
    vtk_paths: list[Path],
    output_dir: Path,
    *,
    timeout_s: int = 900,
) -> dict[str, Any]:
    """Render velocity and pressure from the same resolved VTK volume."""
    paths = [Path(path).resolve() for path in vtk_paths if Path(path).is_file()]
    if not paths:
        return {"status": "NOT_GENERATED", "reason": "no VTK files"}
    executable = shutil.which("pvbatch") or shutil.which("pvpython")
    if not executable:
        return {"status": "NOT_GENERATED", "reason": "pvbatch/pvpython not found"}
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    script_path = output / "render_resolved_vtk.py"
    velocity = output / "velocity_final_vtk.png"
    pressure = output / "pressure_final_vtk.png"
    script = f'''from paraview.simple import *
paths = {json.dumps([str(path) for path in paths])}
reader = LegacyVTKReader(FileNames=[paths[0]])
view = GetActiveViewOrCreate("RenderView")
display = Show(reader, view)
ResetCamera(view)
focus_bounds = []
for path in paths[1:]:
    if "/farfield/" in path.replace("\\\\", "/"):
        continue
    focus_reader = LegacyVTKReader(FileNames=[path])
    focus_reader.UpdatePipeline()
    bounds = focus_reader.GetDataInformation().GetBounds()
    if bounds and (bounds[1] - bounds[0]) > 0 and (bounds[3] - bounds[2]) > 0:
        focus_bounds.append(bounds)
if focus_bounds:
    xmin = min(item[0] for item in focus_bounds); xmax = max(item[1] for item in focus_bounds)
    ymin = min(item[2] for item in focus_bounds); ymax = max(item[3] for item in focus_bounds)
    zmin = min(item[4] for item in focus_bounds); zmax = max(item[5] for item in focus_bounds)
    focal_x = 0.5 * (xmin + xmax); focal_y = 0.5 * (ymin + ymax); focal_z = 0.5 * (zmin + zmax)
    chord = max(xmax - xmin, 1.0e-6)
    view.CameraParallelProjection = 1
    view.CameraFocalPoint = [focal_x, focal_y, focal_z]
    view.CameraPosition = [focal_x, focal_y, focal_z + 5.0 * chord]
    view.CameraViewUp = [0.0, 1.0, 0.0]
    view.CameraParallelScale = max(0.35 * chord, 0.65 * (ymax - ymin), 1.0e-6)
arrays_point = list(reader.PointData.keys())
arrays_cell = list(reader.CellData.keys())
def render_field(name, component, target):
    association = "POINTS" if name in arrays_point else "CELLS"
    if name not in arrays_point and name not in arrays_cell:
        return False
    # Hide the legend associated with the previously rendered array before
    # changing ColorBy.  ParaView otherwise keeps both LUT legends visible and
    # overlays U and p titles/ticks in the second screenshot.
    display.SetScalarBarVisibility(view, False)
    if component:
        ColorBy(display, (association, name, component))
    else:
        ColorBy(display, (association, name))
    display.RescaleTransferFunctionToDataRange(True, False)
    display.SetScalarBarVisibility(view, True)
    lut = GetColorTransferFunction(name)
    scalar_bar = GetScalarBar(lut, view)
    scalar_bar.Title = "|U| [m/s]" if name == "U" else "p [m2/s2]"
    scalar_bar.ComponentTitle = ""
    scalar_bar.TitleFontSize = 12
    scalar_bar.LabelFontSize = 10
    scalar_bar.ScalarBarLength = 0.28
    scalar_bar.ScalarBarThickness = 14
    scalar_bar.Orientation = "Horizontal"
    scalar_bar.WindowLocation = "Upper Right Corner"
    Render(view)
    SaveScreenshot(target, view, ImageResolution=[1600, 1000])
    scalar_bar.Visibility = 0
    return True
ok_u = render_field("U", "Magnitude", {json.dumps(str(velocity))})
ok_p = render_field("p", None, {json.dumps(str(pressure))})
'''
    script_path.write_text(script, encoding="utf-8")
    completed = subprocess.run(
        [str(executable), "--force-offscreen-rendering", str(script_path)],
        cwd=str(output),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=int(timeout_s),
        check=False,
        env=paraview_environment(),
    )
    products = {
        name: str(path)
        for name, path in (("velocity_final_vtk_png", velocity), ("pressure_final_vtk_png", pressure))
        if path.is_file()
    }
    return {
        "status": "READY" if completed.returncode == 0 and products else "NOT_GENERATED",
        "returncode": int(completed.returncode),
        "reader_paths": [str(path) for path in paths],
        "products": products,
        "script": str(script_path),
        "camera_policy": "wall-artifact bounds with 35 percent chord half-height",
        "log_tail": (completed.stdout or "")[-5000:],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Prepare or open a ParaView OpenFOAM case view.")
    parser.add_argument("case", type=Path)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--automatic-products", action="store_true")
    parser.add_argument(
        "--include-animations",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Render frame sequences and encode videos in addition to final images.",
    )
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--maximum-frames", type=int, default=24)
    parser.add_argument("--timeout-s", type=int, default=600)
    parser.add_argument("--stage-label", default="URANS")
    parser.add_argument(
        "--field-scale-mode",
        choices=("exact", "robust", "manual"),
        default="exact",
    )
    parser.add_argument(
        "--robust-percentiles",
        type=float,
        nargs=2,
        default=(1.0, 99.0),
        metavar=("LOW", "HIGH"),
    )
    parser.add_argument("--manual-cp-range", type=float, nargs=2)
    parser.add_argument("--manual-u-range", type=float, nargs=2)
    arguments = parser.parse_args()
    if arguments.automatic_products:
        result = generate_automatic_paraview_products(
            arguments.case,
            arguments.output_dir or arguments.case / "postProcessing" / "ParaViewAutomatic",
            maximum_frames=max(2, arguments.maximum_frames),
            timeout_s=max(30, arguments.timeout_s),
            stage_label=arguments.stage_label,
            field_scale_mode=arguments.field_scale_mode,
            robust_percentiles=tuple(arguments.robust_percentiles),
            manual_scales={
                name: tuple(bounds)
                for name, bounds in {
                    "Cp": arguments.manual_cp_range,
                    "U": arguments.manual_u_range,
                }.items()
                if bounds is not None
            },
            include_animations=bool(arguments.include_animations),
        )
    elif arguments.prepare_only:
        result = prepare_paraview_case(arguments.case)
    else:
        result = launch_paraview_case(arguments.case)
    print(json.dumps(result, indent=2))
