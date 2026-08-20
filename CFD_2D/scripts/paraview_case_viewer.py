#!/usr/bin/env python3
"""Scripted ParaView launcher for reconstructed OpenFOAM cases.

The launcher deliberately avoids ``paraFoam`` session state and ``--data``.
Instead, a ParaView Python startup script opens the absolute ``.foam`` marker,
selects ``internalMesh``, advances to the latest written time, frames the data
and saves both a screenshot and a reusable ``.pvsm`` state.
"""
from __future__ import annotations

import json
import argparse
import os
import shutil
import subprocess
import sys
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


def write_paraview_case_script(
    script_path: Path,
    foam_marker: Path,
    *,
    screenshot_path: Path | None = None,
    state_path: Path | None = None,
    ready_path: Path | None = None,
    focus_chord_m: float | None = None,
) -> Path:
    """Write a deterministic ParaView startup script using absolute paths."""
    script_path = script_path.resolve()
    foam_marker = foam_marker.resolve()
    screenshot_path = (screenshot_path or script_path.with_suffix(".png")).resolve()
    state_path = (state_path or script_path.with_suffix(".pvsm")).resolve()
    ready_path = (ready_path or script_path.with_suffix(".ready.json")).resolve()
    script = f'''from paraview.simple import *
import json
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
    scene.AnimationTime = available_times[-1]
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
try:
    SaveState(state_path)
except Exception:
    state_path = None
Path(ready_path).write_text(json.dumps({{
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
    script = f'''from paraview.simple import *
import json
import math
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
velocity_dir.mkdir(exist_ok=True)
pressure_dir.mkdir(exist_ok=True)

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
    source.CellArrays = list(source.CellArrays.Available)
except Exception:
    pass
source.UpdatePipeline()

scene = GetAnimationScene()
scene.UpdateAnimationUsingDataTimeSteps()
try:
    available_times = [float(value) for value in source.TimestepValues]
except Exception:
    available_times = []
positive_times = [value for value in available_times if value > 0.0]
if not positive_times and available_times:
    positive_times = available_times
if len(positive_times) > maximum_frames:
    selected_indices = sorted(set(
        int(round(index * (len(positive_times) - 1) / (maximum_frames - 1)))
        for index in range(maximum_frames)
    ))
    selected_times = [positive_times[index] for index in selected_indices]
else:
    selected_times = positive_times

view = GetActiveViewOrCreate("RenderView")
view.CameraParallelProjection = 1
view.ViewSize = [1280, 720]
try:
    view.OrientationAxesVisibility = 0
except Exception:
    pass
display = Show(source, view)
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
        ("CELLS", source.GetCellDataInformation()),
        ("POINTS", source.GetPointDataInformation()),
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
    for value in selected_times:
        source.UpdatePipeline(time=value)
        information = (
            source.GetCellDataInformation()
            if association == "CELLS"
            else source.GetPointDataInformation()
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
    for value in selected_times:
        source.UpdatePipeline(time=value)
        fetched = servermanager.Fetch(source)
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
        "global_over_selected_frames": len(selected_times) > 1,
    }}
    return lower, upper

def apply_field_range(lut, name, association, vector_magnitude=False):
    bounds = field_range(name, association, vector_magnitude)
    if bounds is None:
        return False
    lut.RescaleTransferFunction(bounds[0], bounds[1])
    return True

def set_camera(kind):
    if kind == "airfoil":
        focal_x = 0.5 * chord_m
        scale = 0.42 * chord_m
    else:
        focal_x = 1.5 * chord_m
        scale = 1.35 * chord_m
    try:
        bounds = source.GetDataInformation().GetBounds()
        z_mid = 0.5 * (bounds[4] + bounds[5])
    except Exception:
        z_mid = 0.0
    view.CameraFocalPoint = [focal_x, 0.0, z_mid]
    view.CameraPosition = [focal_x, 0.0, z_mid + 5.0 * max(chord_m, 1.0e-6)]
    view.CameraViewUp = [0.0, 1.0, 0.0]
    view.CameraParallelScale = max(scale, 1.0e-6)

def color_cp():
    try:
        display.SetScalarBarVisibility(view, False)
    except Exception:
        pass
    association = available_array("Cp")
    if association:
        ColorBy(display, (association, "Cp"))
        lut = GetColorTransferFunction("Cp")
        if not apply_field_range(lut, "Cp", association):
            display.RescaleTransferFunctionToDataRange(True, False)
        display.SetScalarBarVisibility(view, True)
        return "Cp"
    association = available_array("p")
    if association:
        ColorBy(display, (association, "p"))
        lut = GetColorTransferFunction("p")
        if not apply_field_range(lut, "p", association):
            display.RescaleTransferFunctionToDataRange(True, False)
        display.SetScalarBarVisibility(view, True)
        return "p"
    return None

def color_velocity():
    try:
        display.SetScalarBarVisibility(view, False)
    except Exception:
        pass
    association = available_array("U")
    if not association:
        return None
    ColorBy(display, (association, "U", "Magnitude"))
    lut = GetColorTransferFunction("U")
    if not apply_field_range(lut, "U", association, True):
        display.RescaleTransferFunctionToDataRange(True, False)
    display.SetScalarBarVisibility(view, True)
    return "U magnitude"

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
    lut.RescaleTransferFunction(0.0, max(1.2 * maximum_courant, 1.0e-6))
    display.SetScalarBarVisibility(view, True)
    return "Co"

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
    return str(path)

products = {{
    "time_semantics": time_semantics,
    "frame_coordinate_kind": "SIMPLE_iteration" if is_iteration_stage else "physical_time_seconds",
    "selected_times": selected_times,
    "available_times": available_times,
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
        registrationName="StreamlinePointFields",
        Input=source,
    )
    try:
        streamline_source.PassCellData = 1
    except Exception:
        pass
    streamline_source.UpdatePipeline(time=latest)
    streamlines = StreamTracer(
        registrationName="FreestreamSeededStreamlines",
        Input=streamline_source,
        SeedType="Line",
    )
    flow_x = math.cos(math.radians(alpha_deg))
    flow_y = math.sin(math.radians(alpha_deg))
    normal_x = -flow_y
    normal_y = flow_x
    seed_center_x = -0.25 * chord_m
    seed_center_y = 0.0
    seed_half_length = 0.75 * chord_m
    try:
        bounds = source.GetDataInformation().GetBounds()
        seed_z = 0.5 * (bounds[4] + bounds[5])
    except Exception:
        seed_z = 0.0
    streamlines.SeedType.Point1 = [
        seed_center_x - seed_half_length * normal_x,
        seed_center_y - seed_half_length * normal_y,
        seed_z,
    ]
    streamlines.SeedType.Point2 = [
        seed_center_x + seed_half_length * normal_x,
        seed_center_y + seed_half_length * normal_y,
        seed_z,
    ]
    streamlines.SeedType.Resolution = 120
    streamlines.Vectors = ["POINTS", "U"]
    try:
        streamlines.MaximumStreamlineLength = 8.0 * chord_m
    except Exception:
        pass
    streamlines.UpdatePipeline(time=latest)
    streamline_display = Show(streamlines, view)
    try:
        ColorBy(streamline_display, None)
        streamline_display.DiffuseColor = [0.08, 0.08, 0.08]
        streamline_display.LineWidth = 1.2
        streamline_display.Opacity = 0.82
    except Exception:
        pass
    products["streamlines"] = {{
        "enabled": True,
        "vector": "U",
        "seed_geometry": "line_perpendicular_to_freestream",
        "seed_center_chord": [-0.25, 0.0],
        "seed_length_chord": 1.5,
        "seed_resolution": 120,
        "alpha_deg": alpha_deg,
        "maximum_length_chord": 8.0,
    }}
    products["cp_field"] = color_cp()
    set_camera("airfoil")
    set_title("Cp", latest)
    Render(view)
    cp_final = output_dir / ("Cp_airfoil_%s_final.png" % stage_slug)
    SaveScreenshot(str(cp_final), view, ImageResolution=[1600, 1000])
    products["cp_final_png"] = str(cp_final)
    products["cp_streamlines_final_png"] = str(cp_final)

    products["velocity_field"] = color_velocity()
    set_camera("wake")
    scene.AnimationTime = latest
    source.UpdatePipeline(time=latest)
    set_title("|U|", latest)
    Render(view)
    velocity_final = output_dir / ("Velocity_%s_final.png" % stage_slug)
    SaveScreenshot(str(velocity_final), view, ImageResolution=[1600, 1000])
    products["velocity_final_png"] = str(velocity_final)
    products["velocity_streamlines_final_png"] = str(velocity_final)

    products["courant_policy"] = "NOT_APPLICABLE_TO_RANS" if is_iteration_stage else "URANS_ONLY"
    if not is_iteration_stage:
        products["courant_field"] = color_courant()
        if products["courant_field"]:
            set_camera("airfoil")
            scene.AnimationTime = latest
            source.UpdatePipeline(time=latest)
            set_title("cell Courant number Co", latest)
            Render(view)
            courant_final = output_dir / ("Courant_%s_final.png" % stage_slug)
            SaveScreenshot(str(courant_final), view, ImageResolution=[1600, 1000])
            products["courant_final_png"] = str(courant_final)
            products["courant_hotspots_png"] = save_courant_hotspots(latest)

    color_velocity()
    set_camera("wake")
    for index, value in enumerate(selected_times):
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

    products["pressure_field"] = color_cp()
    set_camera("wake")
    for index, value in enumerate(selected_times):
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

products["status"] = "RENDERED" if selected_times else "NO_WRITTEN_TIMES"
products["frame_count"] = len(selected_times)
products["applied_scales"] = scale_evidence
state_path = output_dir / ("final_%s.pvsm" % stage_slug)
SaveState(str(state_path))
products["state"] = str(state_path)
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
    frame_rate: int = 8,
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
) -> dict[str, Any]:
    """Render a Cp close-up and bounded U/Cp animations with ``pvbatch``."""
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
    chord = case_chord_m(case_dir) or 1.0
    try:
        velocity = float(inputs.get("velocity_m_s", 1.0))
    except (TypeError, ValueError):
        velocity = 1.0
    try:
        alpha_deg = float(inputs.get("alpha_deg", 0.0))
    except (TypeError, ValueError):
        alpha_deg = 0.0
    try:
        maximum_courant = float(inputs.get("maxCo", 1.0))
    except (TypeError, ValueError):
        maximum_courant = 1.0
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
    )
    command = [str(executable), "--force-offscreen-rendering", str(script)]
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            cwd=str(case_dir),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=paraview_environment(),
            timeout=max(30, int(timeout_s)),
            check=False,
        )
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        completed = None
        timed_out = True
        log_text = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
    else:
        log_text = completed.stdout or ""
    output_dir.mkdir(parents=True, exist_ok=True)
    log_path = output_dir / "log.pvbatch_automatic_products"
    log_path.write_text(log_text, encoding="utf-8", errors="replace")
    manifest = read_json(output_dir / "paraview_products.json")
    velocity_video = _encode_frame_sequence(
        output_dir / "velocity_frames" / "velocity_%04d.png",
        output_dir / "velocity_airfoil_wake.mp4",
    ) if manifest.get("frame_count", 0) >= 2 else {"status": "SKIPPED", "reason": "fewer_than_two_frames"}
    pressure_video = _encode_frame_sequence(
        output_dir / "pressure_frames" / "pressure_Cp_%04d.png",
        output_dir / "pressure_Cp_airfoil_wake.mp4",
    ) if manifest.get("frame_count", 0) >= 2 else {"status": "SKIPPED", "reason": "fewer_than_two_frames"}
    report = {
        "status": (
            "TIMEOUT_PARTIAL"
            if timed_out
            else "OK"
            if completed is not None and completed.returncode == 0 and manifest.get("status") == "RENDERED"
            else "FAILED"
        ),
        "command": command,
        "returncode": None if completed is None else int(completed.returncode),
        "elapsed_s": time.monotonic() - started,
        "log": str(log_path),
        "products": manifest,
        "velocity_animation": velocity_video,
        "pressure_animation": pressure_video,
        "storage_strategy": "Direct OpenFOAM reader; no duplicated VTK volume database.",
    }
    (output_dir / "automatic_products_report.json").write_text(
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
    state_path = support / "resolved_vtk_set.pvsm"
    screenshot = support / "resolved_vtk_set.png"
    script = f'''from paraview.simple import *
try:
    from paraview.simple import _DisableFirstRenderCameraReset
    _DisableFirstRenderCameraReset()
except Exception:
    pass
paths = {json.dumps([str(path) for path in paths])}
view = GetActiveViewOrCreate("RenderView")
for index, path in enumerate(paths):
    reader = LegacyVTKReader(FileNames=[path])
    display = Show(reader, view)
    if index == 0:
        try:
            arrays = list(reader.PointData.keys()) + list(reader.CellData.keys())
            if "U" in arrays:
                ColorBy(display, ("POINTS", "U", "Magnitude"))
                display.RescaleTransferFunctionToDataRange(True, False)
        except Exception:
            pass
ResetCamera(view)
focus_bounds = []
for source in GetSources().values():
    try:
        bounds = source.GetDataInformation().GetBounds()
        if bounds and (bounds[1] - bounds[0]) > 0 and (bounds[3] - bounds[2]) > 0:
            if (bounds[1] - bounds[0]) < 0.75 * max(1.0e-12, view.CameraParallelScale * 2.0):
                focus_bounds.append(bounds)
    except Exception:
        pass
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
SaveState({json.dumps(str(state_path))})
'''
    script_path.write_text(script, encoding="utf-8")
    log_path = support / "log.paraview_vtk_launch"
    log_stream = log_path.open("a", encoding="utf-8")
    command = [str(executable), "--script", str(script_path)]
    process = subprocess.Popen(
        command,
        cwd=str(support),
        stdout=log_stream,
        stderr=subprocess.STDOUT,
        env=paraview_environment(),
        start_new_session=True,
    )
    return {
        "status": "OPEN_REQUESTED",
        "pid": int(process.pid),
        "command": command,
        "method": "resolved-legacy-vtk-set",
        "selected_time": float(selected_time),
        "reader_paths": [str(path) for path in paths],
        "script": str(script_path),
        "state": str(state_path),
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
        )
    elif arguments.prepare_only:
        result = prepare_paraview_case(arguments.case)
    else:
        result = launch_paraview_case(arguments.case)
    print(json.dumps(result, indent=2))
