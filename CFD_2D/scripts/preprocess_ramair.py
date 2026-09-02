#!/usr/bin/env python3
r"""
preprocess_ramair_profile_parametric_v18_EXPORT_FIX_SAFE_SHARPTE.py

Enhanced pre-processor for the CATIA V5 ram-air canopy CATScript.

What it does
------------
1) Reads a normalized 2D ram-air rib/airfoil CSV with columns x,y,z.
2) Splits the profile into UPPER and LOWER curves.
3) Writes CATIA-friendly CSV files for the macro:
      - ramair_profile_points_for_CATIA.csv
      - LS1_0417_profile_CATIA_points_mm.csv
      - ramair_global_inputs.csv
      - ramair_cell_distribution.csv
      - ramair_rib_stations.csv
      - ramair_cell_midsections.csv
      - LS1_0417_ramair_profile_2D_mm.dxf
      - ramair_generation_summary.txt

Main improvements over the first version
----------------------------------------
- All main inputs are accessible in the USER SETTINGS block below.
- Optional rectangular / elliptic / quasi-elliptic chord distributions.
- Optional uniform / elliptic / quasi-elliptic cell-span distributions.
- Optional span shrinkage.
- Optional loaded/non-loaded rib incidence, vertical displacement and aft shift.
- Optional mid-cell ballooning / thickness increase and TE rounding with automatic tangent-continuous cap in CATIA.
- Crossport cutting now uses a safer curve-first strategy and an adaptive cutter-wall length based on semi-cell span when a wall fallback is needed.
- Optional crossport guide generation for internal ribs (circle/ellipse, horizontal/vertical, per-port sizing), exported as normalized loops for CATIA.
- v7: crossport holes use an extruded cutting-wall strategy in CATIA by default, so the rib surface is split into a real opening rather than only receiving an imprinted curve.

The CATIA macro remains the final authority for geometric creation. This script only
prepares the station and parameter tables in a clear, editable way.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Literal, Any

import numpy as np
import pandas as pd

_THIS_DIR = Path(__file__).resolve().parent
_ROOT_SCRIPT_HELPER_DIR = _THIS_DIR / "CFD_2D" / "scripts"
if _ROOT_SCRIPT_HELPER_DIR.exists() and str(_ROOT_SCRIPT_HELPER_DIR) not in sys.path:
    sys.path.insert(0, str(_ROOT_SCRIPT_HELPER_DIR))

from ramair_profile_utils import read_and_canonicalize_profile_2d
from ramair_geometry_workspace import crossport_specs

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass


def np_trapezoid_compat(y: Any, x: Any) -> Any:
    """Use NumPy 2.x trapezoid when available, with NumPy 1.x trapz fallback."""
    trapezoid = getattr(np, "trapezoid", None)
    if trapezoid is not None:
        return trapezoid(y, x)
    return np.trapz(y, x)


def _default_project_root() -> Path:
    """Return the repository/project root for both new and legacy script layouts."""
    here = Path(__file__).resolve().parent
    if here.name.lower() == "scripts" and here.parent.name.lower() == "cfd_2d":
        return here.parent.parent
    return here.parent if here.name.lower() in {"cfd 2d", "cfd_2d"} else here


# =============================================================================
# USER SETTINGS - edit these variables for normal use
# =============================================================================

# Project paths
PROJECT_ROOT = _default_project_root()
ACTIVE_PROJECT_ROOT = PROJECT_ROOT
PROFILES_DIR = "Airfoil Profiles"
CATIA_INPUTS_DIR = "CATIA/Inputs"
CFD_2D_INPUTS_DIR = "CFD_2D/CFD_2D_inputs"

# Profile inputs
INPUT_CSV = "Airfoil Profiles/LS1-0417_Cut_Standard_Re3000000.csv"
PROFILE_INPUT_ORDER = "auto"

# CATIA input output folder
OUT_DIR = CATIA_INPUTS_DIR

# Base dimensions
CHORD_MM = 3016.0
SPAN_TOTAL_MM = 8119.0
NUM_CELLS = 9                    # Must be odd with the current symmetric generator
ARC_ANHEDRAL_DEG = 17.9

# Planform chord distribution
#   rectangular      -> same chord at all rib stations
#   elliptic         -> reduced chord toward tips using sqrt(1-eta^2)
#   quasi_elliptic   -> reduced chord toward tips with a softer superellipse-like law
ENABLE_VARIABLE_CHORD = True
CHORD_DISTRIBUTION_MODE: Literal["rectangular", "elliptic", "quasi_elliptic"] = "elliptic"
TIP_CHORD_FACTOR = 0.55          # chord_tip / chord_center if variable chord is enabled
# Chord anchoring when chord varies across the span:
#   leading_edge  -> LE remains at the same X, TE moves
#   trailing_edge -> TE remains at the same X, LE moves
#   mid_chord     -> chord reduction is split symmetrically around mid-chord
#   quarter_chord -> 25% chord line remains fixed
#   custom        -> uses CHORD_ANCHOR_FRACTION directly, 0=LE, 1=TE
CHORD_ANCHOR_MODE: Literal["leading_edge", "trailing_edge", "mid_chord", "quarter_chord", "custom"] = "quarter_chord"
CHORD_ANCHOR_FRACTION = 0.5      # Used only if CHORD_ANCHOR_MODE = "custom"
QUASI_ELLIPTIC_EXPONENT = 0.65   # <1 keeps more chord near the tips; 1 is closer to ellipse

# Cell span distribution
#   uniform          -> all full cells have the same span
#   elliptic         -> central cells wider, tip cells narrower
#   quasi_elliptic   -> same idea but softer
ENABLE_VARIABLE_CELL_SPAN = False
CELL_SPAN_DISTRIBUTION_MODE: Literal["uniform", "elliptic", "quasi_elliptic"] = "uniform"
TIP_CELL_WIDTH_FACTOR = 0.75     # cell_width_tip / cell_width_center when variable span is enabled

# Global cell span shrinkage caused by inflation / in-flight deformation
# Thesis values cited around 15-20%; default here is 15% when enabled.
ENABLE_SPAN_SHRINKAGE = False
SPAN_SHRINKAGE_FRACTION = 0.15   # 0.15 = 15% total span reduction

# Loaded / non-loaded rib distortions
# Based on the thesis description: non-loaded ribs may have higher incidence and upward shift.
ENABLE_RIB_INC_TRANSLATION = True
LOADED_RIB_INCIDENCE_DEG = 0.0
NONLOADED_RIB_INCIDENCE_DEG = 1.5
LOADED_RIB_VERTICAL_OFFSET_MM = 0.0
NONLOADED_RIB_VERTICAL_OFFSET_MM = 19.05   # 3/4 inch
LOADED_RIB_CHORDWISE_OFFSET_MM = 0.0
NONLOADED_RIB_CHORDWISE_OFFSET_MM = 0.0    # aft shift option; no fixed thesis value, user-controlled
TIP_EXTRA_INCIDENCE_DEG = 0.0              # optional extra incidence at tips, symmetric
TIP_EXTRA_VERTICAL_OFFSET_MM = 0.0         # optional extra vertical shift at tips, symmetric

# Mid-cell inflation/ballooning distortions
# These are not applied to structural rib curves. They create virtual mid-cell sections
# used by the CATIA macro to loft upper/lower/TE surfaces through a bulged intermediate section.
ENABLE_CELL_BALLOONING = True
MAX_THICKNESS_INCREASE_FRACTION = 0.30     # 0.30 = +30% section thickness at mid-cell
# Trailing-edge closure strategy for the 2D profile used in CATIA.
#   rounded           -> keep the original finite TE gap and let CATIA build a tangent-continuous rounded TE cap.
#   straight_gap      -> keep the original finite TE gap and use a straight TE closing line/panel.
#   sharp_extension   -> modify the exported/profile-used geometry so upper and lower TE meet at a single sharp point
#                        obtained from the intersection of the upper/lower TE tangents. This is usually the cleanest
#                        2D airfoil-like option for later 2D CFD/FEM profile studies.
TE_CLOSURE_MODE: Literal["rounded", "straight_gap", "sharp_extension"] = "rounded"
ENABLE_TE_ROUNDING = (TE_CLOSURE_MODE == "rounded")
# When rounded is enabled, CATIA closes the TE with a tangent-continuous rounded spline
# whose width comes from the current local upper/lower TE gap. No user radius is imposed.
TE_ROUNDING_NUM_POINTS = 17       # Number of points used by CATIA to approximate the rounded cap
SHARP_TE_INTERSECTION_MAX_X_C = 1.08  # safety limit: if tangent intersection is beyond this, use midpoint fallback
SHARP_TE_MIN_EXTENSION_X_C = 1.000    # ensure sharp TE point is at least this x/c when using fallback
# Tiny residual TE gap used only to avoid zero-length CATIA closing curves.
SHARP_TE_SAFE_GAP_CHORD = 1.0e-5
MIDCELL_AFT_SHIFT_MM = 0.0        # optional aft shift of virtual mid-cell section

# Optional canopy-level lateral bulging of the two external side panels.
# This belongs to canopy geometry, not to the suspension-line module.
# The v14 method builds the side surface with the same external-rib boundary
# as the flat rib: lower curve, upper curve, LE opening and TE closure remain
# coincident with the existing canopy. Only interior grid points are offset
# slightly outward along the local last-cell normal. This is still a geometric
# approximation, not an FSI prediction of the inflated side wall.
# Main optional modules. Keep these here so all user-editable high-level inputs
# are visible at the beginning of the script. Detailed suspension/payload/stabilizer
# inputs are still stored in ramair_suspension_config.json so they can be changed
# without editing Python.
ENABLE_CANOPY_STABILIZERS = False
ENABLE_SUSPENSION_LINES = False
SYSTEM_CONFIG_JSON = "ramair_suspension_config.json"

ENABLE_TIP_SIDE_BULGE = False
TIP_SIDE_BULGE_MAX_LATERAL_MM = 80.0
# v15 default: use fewer, smoother loft sections instead of hundreds/thousands of
# independent CATIA Fill panels. Increase only if needed; CATIA generation time
# grows quickly with the number of generated sections.
TIP_SIDE_BULGE_CHORDWISE_POINTS = 17
TIP_SIDE_BULGE_THICKNESS_LAYERS = 11
TIP_SIDE_BULGE_SURFACE_MODE: Literal["loft_sections", "panel_grid", "curves_only"] = "loft_sections"
TIP_SIDE_BULGE_MAX_GRID_NODES = 450



# CFD/FEM-oriented representation options
# -----------------------------------------------------------------------------
# Recommended strategy after testing CATIA stability:
#   - Canopy fabric: keep the CAD as mid-surfaces and export thickness as shell metadata for FEM.
#     True CAD thickening of many joined GSD patches is fragile and often creates non-watertight offset gaps.
#   - Suspension lines: keep CATIA curves for the layout and export diameter/material/angle data per segment.
#     For CFD, either use the line-drag model from the segment-analysis CSV or create cylinders later in the mesher.
#     Automatic CATIA cylinders are kept as an experimental option only.
ENABLE_FABRIC_THICKNESS_PROPERTIES = True
FABRIC_THICKNESS_MM = 0.30
FABRIC_DENSITY_KG_M3 = 1150.0
FABRIC_MATERIAL = "ripstop_nylon_placeholder"
FABRIC_THICKNESS_STRATEGY: Literal["shell_property", "post_mesh_extrusion", "catia_offset_experimental"] = "shell_property"
# Legacy/experimental CATIA offsets are disabled by default because they were unstable.
ENABLE_FABRIC_THICKNESS = False
FABRIC_THICKNESS_MODE: Literal["none", "single_offset", "symmetric_offsets"] = "none"
FABRIC_THICKNESS_OFFSET_SIDE: Literal["normal", "opposite"] = "normal"
FABRIC_THICKNESS_INCLUDE_RIBS = True
FABRIC_THICKNESS_INCLUDE_STABILIZERS = True
FABRIC_THICKNESS_INCLUDE_TIP_BULGE = True

# Suspension line CAD diameter. The robust default for CFD/FEM preprocessing is to export
# centreline curves + per-segment diameter/material/angle/tension metadata. CATIA tube
# creation remains experimental; use it only for visualization or very small line sets.
ENABLE_SUSPENSION_TUBE_GEOMETRY = False
DEFAULT_SUSPENSION_LINE_DIAMETER_MM = 1.20
SUSPENSION_LINE_CAD_STRATEGY: Literal["curve_with_properties", "catia_tubes_experimental", "mesh_cylinders_postprocess"] = "curve_with_properties"

# Export structure for downstream meshing workflows. CATIA exports go to CATIA/Exports.
# Component-level exports are best handled by showing/hiding HybridBodies according to
# ramair_cad_export_manifest.csv, while the full visible assembly is exported automatically.
EXPORT_FULL_ASSEMBLY_IGES = True
EXPORT_FULL_ASSEMBLY_STEP = False
EXPORT_COMPONENT_MANIFEST = True
EXPORT_SUBFOLDER_NAME = "..\\Exports"
EXPORT_CANOPY_FILENAME = "ramair_canopy_geometry.igs"
EXPORT_FULL_FILENAME = "ramair_full_assembly.igs"

# Crossports in internal ribs
# Crossports are pressure-equalization holes in rib sections. For robustness, the default
# CATIA creates the rib Fill surfaces first, then creates the crossport curves,
# and finally tries to split the rib surfaces one hole at a time. If CATIA rejects a
# split, the macro deletes the failed split feature and keeps the guide curve so the
# rest of the canopy still generates.
ENABLE_CROSSPORTS = True

# CATIA behaviour for the holes:
#   curves_only           -> create crossport guide curves only; no automatic holes.
#   post_split_surfaces   -> recommended: create rib Fill first, then split holes one-by-one.
#   fill_inner_boundaries -> legacy experimental: add crossports as inner Fill boundaries; often fails in CATIA V5.
CROSSPORT_CUT_MODE: Literal["curves_only", "post_split_surfaces", "fill_inner_boundaries"] = "post_split_surfaces"
# Split orientation used by CATIA when post_split_surfaces is selected. If the cut keeps
# the wrong side visually, switch between -1 and 1 and regenerate.
CROSSPORT_SPLIT_ORIENTATION = -1
# post_split_surfaces cutting strategy used by CATIA:
#   curve_split_first    -> recommended: split directly with the closed loop first. In the current CATIA workflow,
#                           this avoids many non-critical CutterWall update warnings; wall split is used only as fallback.
#   extruded_wall_split  -> force the previous wall-first method.
#   curve_split          -> direct curve split only; no cutter wall fallback.
CROSSPORT_CUT_STRATEGY: Literal["curve_split_first", "extruded_wall_split", "curve_split"] = "curve_split_first"

# Cutter-wall half-length. The cutter wall is only a fallback; if used, its extrusion length
# should scale with the canopy, not stay fixed at 200 mm.
#   auto_semicell_span -> half-length = factor * minimum semi-cell spacing, clamped by min/max below.
#   fixed              -> use CROSSPORT_CUTTER_EXTRUDE_MM directly.
CROSSPORT_CUTTER_EXTRUDE_MODE: Literal["auto_semicell_span", "fixed"] = "auto_semicell_span"
CROSSPORT_CUTTER_EXTRUDE_FACTOR_SEMICELL = 0.35
CROSSPORT_CUTTER_EXTRUDE_MIN_MM = 20.0
CROSSPORT_CUTTER_EXTRUDE_MAX_MM = 600.0
CROSSPORT_CUTTER_EXTRUDE_MM = 200.0  # used only when CROSSPORT_CUTTER_EXTRUDE_MODE = "fixed"

# Simple global defaults used when CROSSPORT_CUSTOM_SPECS is empty.
CROSSPORT_SHAPE: Literal["circle", "ellipse"] = "ellipse"
# For ellipse:
#   horizontal -> major axis along chord/X direction, typical and most robust.
#   vertical   -> major axis in thickness/Z direction.
#   auto       -> uses width/height values exactly as entered.
CROSSPORT_ELLIPSE_ORIENTATION: Literal["horizontal", "vertical", "auto"] = "horizontal"
CROSSPORT_COUNT = 3
# standard_3 = fixed robust default at 25%, 45%, 65% chord.
# equidistant = CROSSPORT_COUNT positions between X_START and X_END.
# custom = use CROSSPORT_X_POSITIONS_CHORD.
CROSSPORT_POSITION_MODE: Literal["standard_3", "equidistant", "custom"] = "standard_3"
CROSSPORT_X_POSITIONS_CHORD = [0.25, 0.45, 0.65]
CROSSPORT_X_START_CHORD = 0.25
CROSSPORT_X_END_CHORD = 0.70

# Size definitions, all non-dimensional.
# width_chord_fraction: total hole width divided by local chord.
# height_thickness_fraction: total hole height divided by local profile thickness at the hole x/c.
# Example: width=0.060 and height=0.35 gives an ellipse 6% chord wide and 35% of local thickness high.
CROSSPORT_WIDTH_FRACTION_CHORD = 0.080
CROSSPORT_HEIGHT_FRACTION_LOCAL_THICKNESS = 0.15
CROSSPORT_EDGE_CLEARANCE_FRACTION_LOCAL_THICKNESS = 0.22
CROSSPORT_POINTS_PER_LOOP = 32

# all_internal excludes only the two lateral closing/end ribs. This is the recommended default.
CROSSPORT_APPLY_TO: Literal["all_internal", "loaded_internal", "nonloaded_internal"] = "all_internal"
CROSSPORT_CENTERLINE_MODE: Literal["profile_midline"] = "profile_midline"

# Optional per-crossport customization. Leave empty [] to use the global mode above.
# Each dictionary may define:
#   x: normalized x/c position, e.g. 0.25
#   shape: "circle" or "ellipse"
#   orientation: "horizontal", "vertical", or "auto"
#   width_chord_frac: total chordwise width / chord
#   height_thickness_frac: total vertical height / local thickness
#   z_center_fraction: 0 = lower surface, 1 = upper surface, 0.5 = profile midline
# Example:
# CROSSPORT_CUSTOM_SPECS = [
#     {"x": 0.25, "shape": "ellipse", "orientation": "horizontal", "width_chord_frac": 0.055, "height_thickness_frac": 0.32},
#     {"x": 0.45, "shape": "circle",  "width_chord_frac": 0.045},
#     {"x": 0.65, "shape": "ellipse", "orientation": "vertical",   "width_chord_frac": 0.035, "height_thickness_frac": 0.50},
# ]
CROSSPORT_CUSTOM_SPECS: list[dict] = []

# CATIA output options written to ramair_global_inputs.csv
EXPORT_IGES = True
EXPORT_DXF = True
CREATE_RIB_FILLS = True
CREATE_LOFT_PANELS = True
CREATE_TE_CLOSURE_PANELS = True
USE_MIDCELL_DISTORTION_SECTIONS = ENABLE_CELL_BALLOONING or ENABLE_TE_ROUNDING

# Numerical cleanup
MIN_SPLINE_POINT_DISTANCE_MM = 0.01

# Anhedral/arc control used by CATIA
#   tip_tangent: ARC_ANHEDRAL_DEG is the local rib rotation at the tip.
#   center_to_tip_line: legacy behaviour; tip tangent is approximately 2*ARC_ANHEDRAL_DEG.
ANHEDRAL_ARC_MODE: Literal["tip_tangent", "center_to_tip_line"] = "center_to_tip_line"
# Pivot used to rotate each rib section around the canopy arc.
# profile_min_z keeps the lower envelope as the approximate radial reference.
ANHEDRAL_ROTATION_PIVOT_MODE: Literal["profile_min_z", "profile_zero", "te_center"] = "profile_min_z"
# focus_inward makes the lower side of a tip rib point inward/down toward the focus below the centreline.
# legacy_outward keeps the previous sign convention for comparison/debug only.
ANHEDRAL_SECTION_ORIENTATION: Literal["focus_inward", "legacy_outward"] = "focus_inward"

# Export control: robust default is to export inside BASE_FOLDER from CATIA.
FORCE_EXPORT_TO_BASE_FOLDER = True


# =============================================================================
# 2D CAE EXPORTS FOR CFD/FEM PROFILE ANALYSIS
# =============================================================================
# These outputs create a clean interface between the CATIA-oriented preprocessor
# and the future independent 2D CFD/FEM workflow. They do not create meshes,
# OpenFOAM cases, SU2 cases or solver inputs.
ENABLE_2D_CAE_EXPORTS = True
CAE_2D_DIR_NAME = CFD_2D_INPUTS_DIR
EXPORT_2D_OPEN_RAM_AIR_PROFILE = True
EXPORT_2D_CLOSED_REFERENCE_PROFILE = True
EXPORT_2D_PROFILE_PREVIEWS = True
EXPORT_2D_PROFILE_QUALITY_REPORT = True
EXPORT_2D_MESH_CONFIG_TEMPLATE = True
# Optional closed .dat/.csv reference. If empty or not found, the closed_reference
# variant is built from Profile_used by synthetically closing the LE opening.
CLOSED_REFERENCE_PROFILE_PATH = ""

# Ross / LS1-0417 validation reference exports for the downstream 2D CFD workflow.
ENABLE_REFERENCE_UNCUT_PROFILE_EXPORT = True
REFERENCE_UNCUT_PROFILE_PATH = "Airfoil Profiles/NASA LS1-0417.dat"
REFERENCE_UNCUT_PROFILE_NAME = "reference_uncut"

ENABLE_ROSS_VALIDATION_PROFILE_EXPORTS = True
ROSS_STANDARD_PROFILE_PATH = "Airfoil Profiles/ross_standard_8p4.csv"
ROSS_MINIMUM_PROFILE_PATH = "Airfoil Profiles/ross_minimum_4p0.csv"
ROSS_STANDARD_PROFILE_NAME = "ross_standard_8p4"
ROSS_MINIMUM_PROFILE_NAME = "ross_minimum_4p0"
ROSS_STANDARD_INLET_PERCENT_C = 8.4
ROSS_MINIMUM_INLET_PERCENT_C = 4.0

# CFD/FEM profile-contour settings.  The mesh module treats ram-air fabric as a
# thin solid by default because an open zero-thickness rib does not define a robust
# connected exterior+cavity fluid domain.  This only affects exported metadata and
# future 2D mesh generation; it does not change the CATIA canopy surfaces.
CFD2D_FABRIC_THICKNESS_CHORD = 1.0e-5
CFD2D_MODEL_ZERO_THICKNESS_AS_THIN_SOLID = True

CFD2D_DEFAULT_REYNOLDS = 3.0e6
CFD2D_DEFAULT_MACH = 0.10
CFD2D_DEFAULT_ALPHA_START_DEG = -5.0
CFD2D_DEFAULT_ALPHA_END_DEG = 15.0
CFD2D_DEFAULT_ALPHA_STEP_DEG = 1.0

CFD2D_LENGTH_UNIT = "m"
CFD2D_CHORD_REFERENCE = "local_profile_chord"
CFD2D_AXIS_CONVENTION = "x_chord_positive_TE_z_positive_up"


# =============================================================================
# Implementation
# =============================================================================


def mm_path_text(path: Path) -> str:
    """Return a CATIA-friendly path string."""
    return str(path.resolve())


@dataclass(frozen=True)
class Config:
    input_csv: Path
    profile_input_order: str
    out_dir: Path
    chord_mm: float
    span_total_mm: float
    cells: int
    arc_anhedral_deg: float
    enable_variable_chord: bool
    chord_distribution_mode: str
    tip_chord_factor: float
    chord_anchor_mode: str
    chord_anchor_fraction: float
    quasi_elliptic_exponent: float
    enable_variable_cell_span: bool
    cell_span_distribution_mode: str
    tip_cell_width_factor: float
    enable_span_shrinkage: bool
    span_shrinkage_fraction: float
    enable_rib_inc_translation: bool
    loaded_rib_incidence_deg: float
    nonloaded_rib_incidence_deg: float
    loaded_rib_vertical_offset_mm: float
    nonloaded_rib_vertical_offset_mm: float
    loaded_rib_chordwise_offset_mm: float
    nonloaded_rib_chordwise_offset_mm: float
    tip_extra_incidence_deg: float
    tip_extra_vertical_offset_mm: float
    enable_cell_ballooning: bool
    max_thickness_increase_fraction: float
    te_closure_mode: str
    enable_te_rounding: bool
    te_rounding_num_points: int
    sharp_te_intersection_max_x_c: float
    sharp_te_min_extension_x_c: float
    sharp_te_safe_gap_chord: float
    midcell_aft_shift_mm: float
    export_iges: bool
    export_dxf: bool
    create_rib_fills: bool
    create_loft_panels: bool
    create_te_closure_panels: bool
    use_midcell_distortion_sections: bool
    min_spline_point_distance_mm: float
    anhedral_arc_mode: str
    anhedral_rotation_pivot_mode: str
    force_export_to_base_folder: bool
    anhedral_section_orientation: str
    enable_crossports: bool
    crossport_cut_mode: str
    crossport_split_orientation: int
    crossport_cut_strategy: str
    crossport_cutter_extrude_mode: str
    crossport_cutter_extrude_factor_semicell: float
    crossport_cutter_extrude_min_mm: float
    crossport_cutter_extrude_max_mm: float
    crossport_cutter_extrude_mm: float
    crossport_shape: str
    crossport_ellipse_orientation: str
    crossport_count: int
    crossport_position_mode: str
    crossport_x_positions_chord: tuple[float, ...]
    crossport_x_start_chord: float
    crossport_x_end_chord: float
    crossport_width_fraction_chord: float
    crossport_height_fraction_local_thickness: float
    crossport_edge_clearance_fraction_local_thickness: float
    crossport_points_per_loop: int
    crossport_apply_to: str
    crossport_centerline_mode: str
    crossport_custom_specs: tuple[dict, ...]
    enable_fabric_thickness_properties: bool
    fabric_thickness_mm: float
    fabric_density_kg_m3: float
    fabric_material: str
    fabric_thickness_strategy: str
    enable_fabric_thickness: bool
    fabric_thickness_mode: str
    fabric_thickness_offset_side: str
    fabric_thickness_include_ribs: bool
    fabric_thickness_include_stabilizers: bool
    fabric_thickness_include_tip_bulge: bool
    enable_suspension_tube_geometry: bool
    default_suspension_line_diameter_mm: float
    suspension_line_cad_strategy: str
    export_full_assembly_iges: bool
    export_full_assembly_step: bool
    export_component_manifest: bool
    export_subfolder_name: str
    export_canopy_filename: str
    export_full_filename: str

    @property
    def span_effective_mm(self) -> float:
        if self.enable_span_shrinkage:
            return self.span_total_mm * (1.0 - self.span_shrinkage_fraction)
        return self.span_total_mm


def build_config_from_user_settings() -> Config:
    base = PROJECT_ROOT
    input_path = Path(INPUT_CSV)
    if not input_path.is_absolute():
        input_path = base / input_path
    out_path = Path(OUT_DIR)
    if not out_path.is_absolute():
        out_path = base / out_path

    return Config(
        input_csv=input_path,
        profile_input_order=PROFILE_INPUT_ORDER,
        out_dir=out_path,
        chord_mm=CHORD_MM,
        span_total_mm=SPAN_TOTAL_MM,
        cells=NUM_CELLS,
        arc_anhedral_deg=ARC_ANHEDRAL_DEG,
        enable_variable_chord=ENABLE_VARIABLE_CHORD,
        chord_distribution_mode=CHORD_DISTRIBUTION_MODE,
        tip_chord_factor=TIP_CHORD_FACTOR,
        chord_anchor_mode=CHORD_ANCHOR_MODE,
        chord_anchor_fraction=CHORD_ANCHOR_FRACTION,
        quasi_elliptic_exponent=QUASI_ELLIPTIC_EXPONENT,
        enable_variable_cell_span=ENABLE_VARIABLE_CELL_SPAN,
        cell_span_distribution_mode=CELL_SPAN_DISTRIBUTION_MODE,
        tip_cell_width_factor=TIP_CELL_WIDTH_FACTOR,
        enable_span_shrinkage=ENABLE_SPAN_SHRINKAGE,
        span_shrinkage_fraction=SPAN_SHRINKAGE_FRACTION,
        enable_rib_inc_translation=ENABLE_RIB_INC_TRANSLATION,
        loaded_rib_incidence_deg=LOADED_RIB_INCIDENCE_DEG,
        nonloaded_rib_incidence_deg=NONLOADED_RIB_INCIDENCE_DEG,
        loaded_rib_vertical_offset_mm=LOADED_RIB_VERTICAL_OFFSET_MM,
        nonloaded_rib_vertical_offset_mm=NONLOADED_RIB_VERTICAL_OFFSET_MM,
        loaded_rib_chordwise_offset_mm=LOADED_RIB_CHORDWISE_OFFSET_MM,
        nonloaded_rib_chordwise_offset_mm=NONLOADED_RIB_CHORDWISE_OFFSET_MM,
        tip_extra_incidence_deg=TIP_EXTRA_INCIDENCE_DEG,
        tip_extra_vertical_offset_mm=TIP_EXTRA_VERTICAL_OFFSET_MM,
        enable_cell_ballooning=ENABLE_CELL_BALLOONING,
        max_thickness_increase_fraction=MAX_THICKNESS_INCREASE_FRACTION,
        te_closure_mode=TE_CLOSURE_MODE,
        enable_te_rounding=ENABLE_TE_ROUNDING,
        te_rounding_num_points=TE_ROUNDING_NUM_POINTS,
        sharp_te_intersection_max_x_c=SHARP_TE_INTERSECTION_MAX_X_C,
        sharp_te_min_extension_x_c=SHARP_TE_MIN_EXTENSION_X_C,
        sharp_te_safe_gap_chord=SHARP_TE_SAFE_GAP_CHORD,
        midcell_aft_shift_mm=MIDCELL_AFT_SHIFT_MM,
        export_iges=EXPORT_IGES,
        export_dxf=EXPORT_DXF,
        create_rib_fills=CREATE_RIB_FILLS,
        create_loft_panels=CREATE_LOFT_PANELS,
        create_te_closure_panels=CREATE_TE_CLOSURE_PANELS,
        use_midcell_distortion_sections=(USE_MIDCELL_DISTORTION_SECTIONS or ENABLE_CELL_BALLOONING or ENABLE_TE_ROUNDING),
        min_spline_point_distance_mm=MIN_SPLINE_POINT_DISTANCE_MM,
        anhedral_arc_mode=ANHEDRAL_ARC_MODE,
        anhedral_rotation_pivot_mode=ANHEDRAL_ROTATION_PIVOT_MODE,
        force_export_to_base_folder=FORCE_EXPORT_TO_BASE_FOLDER,
        anhedral_section_orientation=ANHEDRAL_SECTION_ORIENTATION,
        enable_crossports=ENABLE_CROSSPORTS,
        crossport_cut_mode=CROSSPORT_CUT_MODE,
        crossport_split_orientation=CROSSPORT_SPLIT_ORIENTATION,
        crossport_cut_strategy=CROSSPORT_CUT_STRATEGY,
        crossport_cutter_extrude_mode=CROSSPORT_CUTTER_EXTRUDE_MODE,
        crossport_cutter_extrude_factor_semicell=CROSSPORT_CUTTER_EXTRUDE_FACTOR_SEMICELL,
        crossport_cutter_extrude_min_mm=CROSSPORT_CUTTER_EXTRUDE_MIN_MM,
        crossport_cutter_extrude_max_mm=CROSSPORT_CUTTER_EXTRUDE_MAX_MM,
        crossport_cutter_extrude_mm=CROSSPORT_CUTTER_EXTRUDE_MM,
        crossport_shape=CROSSPORT_SHAPE,
        crossport_ellipse_orientation=CROSSPORT_ELLIPSE_ORIENTATION,
        crossport_count=CROSSPORT_COUNT,
        crossport_position_mode=CROSSPORT_POSITION_MODE,
        crossport_x_positions_chord=tuple(CROSSPORT_X_POSITIONS_CHORD),
        crossport_x_start_chord=CROSSPORT_X_START_CHORD,
        crossport_x_end_chord=CROSSPORT_X_END_CHORD,
        crossport_width_fraction_chord=CROSSPORT_WIDTH_FRACTION_CHORD,
        crossport_height_fraction_local_thickness=CROSSPORT_HEIGHT_FRACTION_LOCAL_THICKNESS,
        crossport_edge_clearance_fraction_local_thickness=CROSSPORT_EDGE_CLEARANCE_FRACTION_LOCAL_THICKNESS,
        crossport_points_per_loop=CROSSPORT_POINTS_PER_LOOP,
        crossport_apply_to=CROSSPORT_APPLY_TO,
        crossport_centerline_mode=CROSSPORT_CENTERLINE_MODE,
        crossport_custom_specs=tuple(dict(x) for x in CROSSPORT_CUSTOM_SPECS),
        enable_fabric_thickness_properties=ENABLE_FABRIC_THICKNESS_PROPERTIES,
        fabric_thickness_mm=FABRIC_THICKNESS_MM,
        fabric_density_kg_m3=FABRIC_DENSITY_KG_M3,
        fabric_material=FABRIC_MATERIAL,
        fabric_thickness_strategy=FABRIC_THICKNESS_STRATEGY,
        enable_fabric_thickness=ENABLE_FABRIC_THICKNESS,
        fabric_thickness_mode=FABRIC_THICKNESS_MODE,
        fabric_thickness_offset_side=FABRIC_THICKNESS_OFFSET_SIDE,
        fabric_thickness_include_ribs=FABRIC_THICKNESS_INCLUDE_RIBS,
        fabric_thickness_include_stabilizers=FABRIC_THICKNESS_INCLUDE_STABILIZERS,
        fabric_thickness_include_tip_bulge=FABRIC_THICKNESS_INCLUDE_TIP_BULGE,
        enable_suspension_tube_geometry=ENABLE_SUSPENSION_TUBE_GEOMETRY,
        default_suspension_line_diameter_mm=DEFAULT_SUSPENSION_LINE_DIAMETER_MM,
        suspension_line_cad_strategy=SUSPENSION_LINE_CAD_STRATEGY,
        export_full_assembly_iges=EXPORT_FULL_ASSEMBLY_IGES,
        export_full_assembly_step=EXPORT_FULL_ASSEMBLY_STEP,
        export_component_manifest=EXPORT_COMPONENT_MANIFEST,
        export_subfolder_name=EXPORT_SUBFOLDER_NAME,
        export_canopy_filename=EXPORT_CANOPY_FILENAME,
        export_full_filename=EXPORT_FULL_FILENAME,
    )


def clamp01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def span_eta(y_mm: float, half_span_mm: float) -> float:
    if abs(half_span_mm) < 1e-12:
        return 0.0
    return clamp01(abs(y_mm) / half_span_mm)


def elliptic_shape(eta: float, exponent: float = 1.0) -> float:
    """Elliptic-like shape: 1 at center, 0 at tip."""
    eta = clamp01(eta)
    base = max(0.0, 1.0 - eta * eta)
    # exponent=1 -> true sqrt(1-eta^2). Lower values keep tips fuller.
    return base ** (0.5 * exponent)


def distribution_factor(mode: str, eta: float, tip_factor: float, quasi_exponent: float) -> float:
    mode = (mode or "rectangular").lower().strip()
    if mode in {"rectangular", "uniform", "constant"}:
        return 1.0
    if mode == "elliptic":
        shape = elliptic_shape(eta, 1.0)
    elif mode == "quasi_elliptic":
        shape = elliptic_shape(eta, quasi_exponent)
    else:
        raise ValueError(f"Unsupported distribution mode: {mode!r}")
    return float(tip_factor) + (1.0 - float(tip_factor)) * shape


def chord_at_y(y_mm: float, half_span_mm: float, cfg: Config) -> float:
    if not cfg.enable_variable_chord:
        return cfg.chord_mm
    eta = span_eta(y_mm, half_span_mm)
    f = distribution_factor(cfg.chord_distribution_mode, eta, cfg.tip_chord_factor, cfg.quasi_elliptic_exponent)
    return cfg.chord_mm * f


def chord_anchor_fraction(cfg: Config) -> float:
    mode = (cfg.chord_anchor_mode or "leading_edge").lower().strip()
    if mode in {"leading_edge", "le", "nose"}:
        return 0.0
    if mode in {"trailing_edge", "te"}:
        return 1.0
    if mode in {"mid_chord", "midchord", "symmetric", "both"}:
        return 0.5
    if mode in {"quarter_chord", "quarter", "c4", "25"}:
        return 0.25
    if mode == "custom":
        return clamp01(cfg.chord_anchor_fraction)
    raise ValueError(f"Unsupported CHORD_ANCHOR_MODE: {cfg.chord_anchor_mode!r}")


def planform_chordwise_offset_mm(local_chord_mm: float, cfg: Config) -> float:
    """Offset caused only by variable chord anchoring.

    If the local chord is smaller than the center chord:
      anchor=0.0 keeps LE fixed,
      anchor=1.0 keeps TE fixed,
      anchor=0.5 moves LE and TE symmetrically.
    """
    if not cfg.enable_variable_chord:
        return 0.0
    return chord_anchor_fraction(cfg) * (cfg.chord_mm - local_chord_mm)


def make_cell_widths(cfg: Config) -> np.ndarray:
    n = cfg.cells
    span_eff = cfg.span_effective_mm
    if not cfg.enable_variable_cell_span:
        return np.full(n, span_eff / n)

    centers = np.linspace(-1.0 + 1.0 / n, 1.0 - 1.0 / n, n)
    weights = []
    for c in centers:
        eta = abs(float(c))
        weights.append(distribution_factor(cfg.cell_span_distribution_mode, eta, cfg.tip_cell_width_factor, cfg.quasi_elliptic_exponent))
    weights = np.asarray(weights, dtype=float)
    return weights / weights.sum() * span_eff


def write_dxf_r12(path: Path, upper_pts: Iterable[tuple[float, float]], lower_pts: Iterable[tuple[float, float]]) -> None:
    upper_pts = list(upper_pts)
    lower_pts = list(lower_pts)

    def header() -> str:
        return "0\nSECTION\n2\nHEADER\n9\n$ACADVER\n1\nAC1009\n0\nENDSEC\n0\nSECTION\n2\nTABLES\n0\nTABLE\n2\nLAYER\n70\n4\n"

    def layer(name: str, color: int) -> str:
        return f"0\nLAYER\n2\n{name}\n70\n0\n62\n{color}\n6\nCONTINUOUS\n"

    def tables_end() -> str:
        return "0\nENDTAB\n0\nENDSEC\n0\nSECTION\n2\nENTITIES\n"

    def end() -> str:
        return "0\nENDSEC\n0\nEOF\n"

    def polyline(points: list[tuple[float, float]], layer_name: str, closed: bool = False) -> str:
        s = f"0\nPOLYLINE\n8\n{layer_name}\n66\n1\n70\n{1 if closed else 0}\n"
        for x, z in points:
            s += f"0\nVERTEX\n8\n{layer_name}\n10\n{x:.9f}\n20\n{z:.9f}\n30\n0.0\n"
        s += f"0\nSEQEND\n8\n{layer_name}\n"
        return s

    open_pts = [upper_pts[-1], lower_pts[0]]
    closed_pts = upper_pts + lower_pts + [upper_pts[0]]

    content = header()
    content += layer("UPPER", 1) + layer("LOWER", 3) + layer("OPENING", 5) + layer("PROFILE_CLOSED", 7)
    content += tables_end()
    content += polyline(upper_pts, "UPPER", False)
    content += polyline(lower_pts, "LOWER", False)
    content += polyline(open_pts, "OPENING", False)
    content += polyline(closed_pts, "PROFILE_CLOSED", False)
    content += end()
    path.write_text(content, encoding="ascii")


def split_profile(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    required = {"x", "y", "z"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Input CSV must contain columns x,y,z. Missing: {sorted(missing)}")

    df = df[["x", "y", "z"]].copy().astype(float)
    # Preserve original order. The LE opening is assumed around the minimum x value.
    le_idx = int(df["x"].idxmin())
    upper = df.iloc[: le_idx + 1].copy().reset_index(drop=True)
    lower = df.iloc[le_idx + 1 :].copy().reset_index(drop=True)
    lower = lower.loc[~lower[["x", "y", "z"]].duplicated()].reset_index(drop=True)

    if len(upper) < 2 or len(lower) < 2:
        raise ValueError("Could not split profile into usable UPPER/LOWER sections. Check point order and LE location.")
    return upper, lower


def read_profile_branches_for_canopy(cfg: Config) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Read and split the input profile using the shared robust 2D profile utilities.

    The canonical utility returns both branches LE->TE.  The legacy CATIA generator
    below expects UPPER as TE->LE and LOWER as LE->TE, so the upper branch is
    reversed here only at the compatibility boundary.
    """
    cp = read_and_canonicalize_profile_2d(
        cfg.input_csv,
        "open_ramair",
        input_order=cfg.profile_input_order,
        has_inlet="auto",
        te_closure_mode=cfg.te_closure_mode,
        chord_m=float(cfg.chord_mm) / 1000.0,
    )
    if cp.errors:
        details = "; ".join(cp.errors)
        raise ValueError(f"Could not canonicalize input profile {cfg.input_csv}: {details}")

    def branch_to_legacy(branch: pd.DataFrame, reverse: bool = False) -> pd.DataFrame:
        data = branch[["x_norm", "z_norm"]].copy()
        if reverse:
            data = data.iloc[::-1].reset_index(drop=True)
        return pd.DataFrame({
            "x": data["x_norm"].astype(float).to_numpy(),
            "y": data["z_norm"].astype(float).to_numpy(),
            "z": np.zeros(len(data), dtype=float),
        })

    upper = branch_to_legacy(cp.upper, reverse=True)
    lower = branch_to_legacy(cp.lower, reverse=False)
    return upper, lower, cp.report


def to_catia_points(section_df: pd.DataFrame, section: str, chord_mm: float) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "section": section,
            "order_in_section": np.arange(1, len(section_df) + 1),
            "x_chord_norm": section_df["x"].to_numpy(),
            "z_chord_norm": section_df["y"].to_numpy(),
            "X_mm": section_df["x"].to_numpy() * chord_mm,
            "Y_span_mm": np.zeros(len(section_df)),
            "Z_mm": section_df["y"].to_numpy() * chord_mm,
        }
    )


def make_cell_and_rib_tables(cfg: Config) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    if cfg.cells % 2 != 1:
        raise ValueError("NUM_CELLS must be odd for this symmetric canopy generator.")
    if cfg.cells < 1:
        raise ValueError("NUM_CELLS must be >= 1.")
    if not 0.0 <= cfg.span_shrinkage_fraction < 0.95:
        raise ValueError("SPAN_SHRINKAGE_FRACTION must be in [0, 0.95).")
    if cfg.tip_chord_factor <= 0.0:
        raise ValueError("TIP_CHORD_FACTOR must be > 0.")
    if cfg.tip_cell_width_factor <= 0.0:
        raise ValueError("TIP_CELL_WIDTH_FACTOR must be > 0.")
    _ = chord_anchor_fraction(cfg)  # validate mode/fraction early

    cell_widths = make_cell_widths(cfg)
    half_span = cfg.span_effective_mm / 2.0
    edges = [-half_span]
    for w in cell_widths:
        edges.append(edges[-1] + float(w))
    edges[-1] = half_span

    cell_rows = []
    for i in range(cfg.cells):
        y_left = edges[i]
        y_right = edges[i + 1]
        y_center = 0.5 * (y_left + y_right)
        rel = i + 1 - (cfg.cells + 1) / 2
        chord_center = chord_at_y(y_center, half_span, cfg)
        cell_rows.append(
            {
                "cell_id_left_to_right": i + 1,
                "relative_cell_index": int(rel),
                "Y_left_loaded_mm": y_left,
                "Y_center_nonloaded_mm": y_center,
                "Y_right_loaded_mm": y_right,
                "cell_width_mm": y_right - y_left,
                "cell_width_fraction_total_span": (y_right - y_left) / cfg.span_effective_mm,
                "chord_at_cell_center_mm": chord_center,
                "planform_chordwise_offset_mm": planform_chordwise_offset_mm(chord_center, cfg),
                "twist_deg": 0.0,
                "incidence_deg": cfg.nonloaded_rib_incidence_deg if cfg.enable_rib_inc_translation else 0.0,
                "notes": "Generated by parametric preprocessor",
            }
        )

    station_rows = []
    station_index = -cfg.cells
    rib_id = 1

    def add_station(y: float, rib_type: str, related_cell: int | str) -> None:
        nonlocal station_index, rib_id
        eta = span_eta(y, half_span)
        is_loaded = 1 if rib_type == "LOADED" else 0
        chord_local = chord_at_y(y, half_span, cfg)
        planform_xoff = planform_chordwise_offset_mm(chord_local, cfg)

        if cfg.enable_rib_inc_translation:
            inc = cfg.loaded_rib_incidence_deg if is_loaded else cfg.nonloaded_rib_incidence_deg
            voff = cfg.loaded_rib_vertical_offset_mm if is_loaded else cfg.nonloaded_rib_vertical_offset_mm
            manual_xoff = cfg.loaded_rib_chordwise_offset_mm if is_loaded else cfg.nonloaded_rib_chordwise_offset_mm
            inc += cfg.tip_extra_incidence_deg * eta
            voff += cfg.tip_extra_vertical_offset_mm * eta
        else:
            inc = 0.0
            voff = 0.0
            manual_xoff = 0.0

        rib_name = rib_type if not (not is_loaded and abs(y) < 1e-8) else "NON_LOADED_CENTER"
        station_rows.append(
            {
                "rib_id": rib_id,
                "station_index": station_index,
                "Y_flat_mm": y,
                "eta_abs_span": eta,
                "chord_mm": chord_local,
                "incidence_deg": inc,
                "vertical_offset_mm": voff,
                "manual_chordwise_offset_mm": manual_xoff,
                "planform_chordwise_offset_mm": planform_xoff,
                "chordwise_offset_mm": planform_xoff + manual_xoff,
                "thickness_scale": 1.0,
                "rib_type": rib_name,
                "is_loaded_rib": is_loaded,
                "related_cell": related_cell,
                "notes": "Loaded=edge rib; non-loaded=cell centre rib",
            }
        )
        rib_id += 1
        station_index += 1

    # Alternating sequence: left loaded edge, non-loaded cell centre, right loaded edge, etc.
    for i in range(cfg.cells):
        if i == 0:
            add_station(edges[i], "LOADED", "left_boundary")
        add_station(0.5 * (edges[i] + edges[i + 1]), "NON_LOADED", i + 1)
        add_station(edges[i + 1], "LOADED", i + 1)

    rib_df = pd.DataFrame(station_rows)

    # Virtual midsections between adjacent generated stations, used only as loft guides.
    mid_rows = []
    for i in range(len(station_rows) - 1):
        left = station_rows[i]
        right = station_rows[i + 1]
        y_mid = 0.5 * (left["Y_flat_mm"] + right["Y_flat_mm"])
        eta = span_eta(y_mid, half_span)
        chord_mid = chord_at_y(y_mid, half_span, cfg)
        planform_xoff_mid = planform_chordwise_offset_mm(chord_mid, cfg)
        manual_xoff_mid = 0.5 * (left["manual_chordwise_offset_mm"] + right["manual_chordwise_offset_mm"]) + cfg.midcell_aft_shift_mm
        thickness_scale = 1.0 + cfg.max_thickness_increase_fraction if cfg.enable_cell_ballooning else 1.0
        mid_rows.append(
            {
                "midsection_id": i + 1,
                "left_rib_id": left["rib_id"],
                "right_rib_id": right["rib_id"],
                "Y_flat_mm": y_mid,
                "eta_abs_span": eta,
                "chord_mm": chord_mid,
                "incidence_deg": 0.5 * (left["incidence_deg"] + right["incidence_deg"]),
                "vertical_offset_mm": 0.5 * (left["vertical_offset_mm"] + right["vertical_offset_mm"]),
                "manual_chordwise_offset_mm": manual_xoff_mid,
                "planform_chordwise_offset_mm": planform_xoff_mid,
                "chordwise_offset_mm": planform_xoff_mid + manual_xoff_mid,
                "thickness_scale": thickness_scale,
                "te_rounding_enabled": int(cfg.enable_te_rounding),
                "ballooning_factor": 1.0 if cfg.enable_cell_ballooning else 0.0,
                "notes": "Virtual guide section for CATIA lofts; not a structural rib",
            }
        )

    return pd.DataFrame(cell_rows), rib_df, pd.DataFrame(mid_rows)


def _interp_z_at_x(section_df: pd.DataFrame, x: float) -> float:
    """Interpolate profile z at a normalized chordwise x."""
    pts = section_df[["x", "y"]].copy().astype(float)
    pts = pts.groupby("x", as_index=False)["y"].mean().sort_values("x")
    xs = pts["x"].to_numpy()
    zs = pts["y"].to_numpy()
    x = float(np.clip(x, xs.min(), xs.max()))
    return float(np.interp(x, xs, zs))



def _crossport_x_positions(cfg: Config) -> list[float]:
    mode = (cfg.crossport_position_mode or "standard_3").lower().strip()
    if mode == "standard_3":
        # Simple robust default: three chordwise holes in a safe middle region.
        # The thesis states that ribs commonly have 2 to 4 crossports; 3 is a good starting point.
        n = max(1, int(cfg.crossport_count))
        if n == 3:
            return [0.25, 0.45, 0.65]
        if n == 1:
            return [0.45]
        return list(np.linspace(0.25, 0.70, n))
    if mode == "custom":
        return [float(x) for x in cfg.crossport_x_positions_chord]
    if mode == "equidistant":
        n = max(1, int(cfg.crossport_count))
        if n == 1:
            return [0.5 * (cfg.crossport_x_start_chord + cfg.crossport_x_end_chord)]
        return list(np.linspace(cfg.crossport_x_start_chord, cfg.crossport_x_end_chord, n))
    raise ValueError(f"Unsupported CROSSPORT_POSITION_MODE: {cfg.crossport_position_mode!r}")


def _base_crossport_specs(cfg: Config) -> list[dict]:
    """Return user-facing crossport specs with defaults expanded."""
    return crossport_specs({
        "custom_specs": cfg.crossport_custom_specs,
        "position_mode": cfg.crossport_position_mode,
        "count": cfg.crossport_count,
        "x_positions_chord": cfg.crossport_x_positions_chord,
        "x_start_chord": cfg.crossport_x_start_chord,
        "x_end_chord": cfg.crossport_x_end_chord,
        "shape": cfg.crossport_shape,
        "ellipse_orientation": cfg.crossport_ellipse_orientation,
        "width_fraction_chord": cfg.crossport_width_fraction_chord,
        "height_fraction_local_thickness": cfg.crossport_height_fraction_local_thickness,
        "points_per_loop": cfg.crossport_points_per_loop,
    })


def _size_for_crossport(spec: dict, cfg: Config, local_thickness: float) -> tuple[float, float, str, str]:
    shape = spec["shape"]
    orientation = spec["orientation"]
    if shape not in {"circle", "ellipse"}:
        raise ValueError(f"Crossport shape must be 'circle' or 'ellipse', got {shape!r}.")
    if orientation not in {"horizontal", "vertical", "auto"}:
        raise ValueError(f"Crossport orientation must be 'horizontal', 'vertical' or 'auto', got {orientation!r}.")

    radius = spec.get("radius_chord_frac")
    width = max(1e-6, 2.0 * float(radius) if radius is not None else float(spec["width_chord_frac"]))
    height = max(1e-6, float(spec["height_thickness_frac"]) * local_thickness)

    clearance = max(0.0, cfg.crossport_edge_clearance_fraction_local_thickness) * local_thickness
    admissible_height = max(1e-6, local_thickness - 2.0 * clearance)
    height = min(height, admissible_height)

    if shape == "circle":
        diameter = min(width, admissible_height)
        return diameter, diameter, shape, "circle"

    # For an ellipse, horizontal/vertical define which axis should be larger.
    # The entered size values are still respected as far as possible; if their order is
    # inconsistent with orientation we swap them to make the intent clear.
    if orientation == "horizontal" and height > width:
        width, height = height, width
    elif orientation == "vertical" and width > height:
        width, height = height, width

    # Re-clamp after a potential swap, since height is the constrained thickness direction.
    height = min(height, admissible_height)
    return width, height, shape, orientation


def make_crossport_table(cfg: Config, upper: pd.DataFrame, lower: pd.DataFrame) -> pd.DataFrame:
    """Create normalized closed crossport loops for CATIA.

    The loops are defined in the same normalized local rib coordinates as the base profile:
    x_chord_norm and z_chord_norm. CATIA scales and transforms them per rib exactly as it
    does for the external UPPER/LOWER profile points.

    Important robustness decision:
    - The CSV defines geometry of crossport guide curves.
    - Whether those curves are used as real Fill inner boundaries is controlled by
      crossport_cut_mode in ramair_global_inputs.csv. The recommended default is post_split_surfaces.
    """
    columns = [
        "crossport_id",
        "loop_id",
        "point_order",
        "shape",
        "orientation",
        "x_center_norm",
        "z_center_norm",
        "z_center_fraction",
        "width_chord_norm",
        "height_chord_norm",
        "x_chord_norm",
        "z_chord_norm",
        "apply_to",
        "cut_mode",
        "notes",
    ]

    if not cfg.enable_crossports:
        return pd.DataFrame(columns=columns)

    specs = _base_crossport_specs(cfg)
    rows = []
    loop_id = 1

    for cp_id, spec in enumerate(specs, start=1):
        x0 = float(spec["x"])
        if not 0.02 < x0 < 0.98:
            raise ValueError(f"Crossport x position {x0} is too close to LE/TE. Use normalized values in (0.02, 0.98).")

        z_u = _interp_z_at_x(upper, x0)
        z_l = _interp_z_at_x(lower, x0)
        z_high = max(z_u, z_l)
        z_low = min(z_u, z_l)
        local_thickness = z_high - z_low
        if local_thickness <= 1e-6:
            raise ValueError(f"Crossport at x={x0:.4f} has nearly zero local profile thickness.")

        requested_zfrac = spec.get("z_center_fraction")
        if requested_zfrac is not None:
            zfrac = max(0.0, min(1.0, float(requested_zfrac)))
            zc = z_low + zfrac * local_thickness
        elif str(cfg.crossport_centerline_mode).lower().strip() == "chordline":
            zc = min(z_high, max(z_low, 0.0))
            zfrac = (zc - z_low) / local_thickness
        else:
            zfrac = 0.5
            zc = z_low + zfrac * local_thickness

        width, height, shape, orientation = _size_for_crossport(spec, cfg, local_thickness)
        rx = 0.5 * width
        rz = 0.5 * height

        # Prevent chordwise coordinates from crossing LE/TE after sizing.
        if x0 - rx <= 0.01 or x0 + rx >= 0.99:
            raise ValueError(
                f"Crossport {cp_id} at x/c={x0:.3f} is too wide for its position. "
                "Reduce width_chord_frac or move it away from LE/TE."
            )

        # Prevent vertical coordinates from escaping the local profile bounds.
        if zc - rz < z_low or zc + rz > z_high:
            # Recenter around the midline and recheck; this protects custom z_center_fraction values.
            zc = 0.5 * (z_high + z_low)
            if zc - rz < z_low or zc + rz > z_high:
                rz = 0.5 * max(1e-6, local_thickness * (1.0 - 2.0 * cfg.crossport_edge_clearance_fraction_local_thickness))
                height = 2.0 * rz

        npts = max(12, int(spec.get("points_per_loop", cfg.crossport_points_per_loop)))
        for k in range(npts):
            a = 2.0 * math.pi * k / npts
            rows.append(
                {
                    "crossport_id": cp_id,
                    "loop_id": loop_id,
                    "point_order": k + 1,
                    "shape": shape,
                    "orientation": orientation,
                    "x_center_norm": x0,
                    "z_center_norm": zc,
                    "z_center_fraction": zfrac,
                    "width_chord_norm": width,
                    "height_chord_norm": height,
                    "x_chord_norm": x0 + rx * math.cos(a),
                    "z_chord_norm": zc + rz * math.sin(a),
                    "apply_to": cfg.crossport_apply_to,
                    "cut_mode": cfg.crossport_cut_mode,
                    "notes": "Closed crossport guide loop; CATIA uses cut_mode to keep as curves or split the rib Fill surface after creation.",
                }
            )

        loop_id += 1

    return pd.DataFrame(rows, columns=columns)

def compute_crossport_cutter_extrude_mm(cfg: Config, rib_df: pd.DataFrame) -> float:
    """Return the effective half-length for CATIA cutter-wall extrusion.

    The rib surfaces are zero-thickness, so the wall only needs to pass through the
    rib plane. A fixed 200 mm value can be too large on a small model and can create
    update failures in CATIA. The automatic value scales with the smallest semi-cell
    spacing, i.e. the smallest distance between two adjacent rib stations.
    """
    mode = (cfg.crossport_cutter_extrude_mode or "auto_semicell_span").lower().strip()
    if mode == "fixed":
        return max(1.0, float(cfg.crossport_cutter_extrude_mm))

    y = np.asarray(rib_df["Y_flat_mm"], dtype=float)
    y = np.sort(y)
    diffs = np.diff(y)
    diffs = diffs[diffs > 1.0e-9]
    if len(diffs) == 0:
        base = cfg.span_effective_mm / max(1, 2 * cfg.cells)
    else:
        base = float(np.min(diffs))

    value = float(cfg.crossport_cutter_extrude_factor_semicell) * base
    value = max(float(cfg.crossport_cutter_extrude_min_mm), value)
    value = min(float(cfg.crossport_cutter_extrude_max_mm), value)
    return value

def write_params_csv(cfg: Config, out_dir: Path, rib_df: pd.DataFrame) -> None:
    effective_cutter_extrude_mm = compute_crossport_cutter_extrude_mm(cfg, rib_df)
    te_mode = str(cfg.te_closure_mode).lower().strip()
    effective_te_rounding = bool(cfg.enable_te_rounding and te_mode == "rounded")
    effective_te_closure_panels = bool(cfg.create_te_closure_panels and te_mode != "sharp_extension")
    exports_dir = out_dir / cfg.export_subfolder_name
    exports_dir.mkdir(parents=True, exist_ok=True)
    rows = [
        ["parameter", "value", "unit", "description"],
        ["input_profile_path", str(cfg.input_csv), "file", "Source profile path used by the preprocessor."],
        ["profile_input_order", cfg.profile_input_order, "text", "Input order hint used by robust profile canonicalization."],
        ["chord_mm", cfg.chord_mm, "mm", "Center/base rib chord used to scale normalized profile points."],
        ["span_total_mm", cfg.span_total_mm, "mm", "Nominal flat span before optional shrinkage."],
        ["span_effective_mm", cfg.span_effective_mm, "mm", "Span used for generated rib stations after optional shrinkage."],
        ["num_cells", cfg.cells, "-", "Number of canopy cells; odd for this symmetric generator."],
        ["arc_anhedral_deg", cfg.arc_anhedral_deg, "deg", "Tip local rib rotation for tip_tangent arc mode."],
        ["anhedral_arc_mode", cfg.anhedral_arc_mode, "text", "tip_tangent or center_to_tip_line."],
        ["anhedral_rotation_pivot_mode", cfg.anhedral_rotation_pivot_mode, "text", "profile_min_z, profile_zero, or te_center."],
        ["enable_variable_chord", int(cfg.enable_variable_chord), "0/1", "Use per-rib chord distribution."],
        ["chord_distribution_mode", cfg.chord_distribution_mode, "text", "rectangular, elliptic, quasi_elliptic."],
        ["tip_chord_factor", cfg.tip_chord_factor, "-", "Tip chord divided by center chord."],
        ["chord_anchor_mode", cfg.chord_anchor_mode, "text", "leading_edge, trailing_edge, mid_chord, quarter_chord, custom."],
        ["chord_anchor_fraction", chord_anchor_fraction(cfg), "-", "0=LE fixed, 1=TE fixed, 0.5=symmetric."],
        ["enable_variable_cell_span", int(cfg.enable_variable_cell_span), "0/1", "Use variable full-cell widths."],
        ["cell_span_distribution_mode", cfg.cell_span_distribution_mode, "text", "uniform, elliptic, quasi_elliptic."],
        ["tip_cell_width_factor", cfg.tip_cell_width_factor, "-", "Tip cell width divided by center cell width."],
        ["enable_span_shrinkage", int(cfg.enable_span_shrinkage), "0/1", "Apply total span shrinkage before generating stations."],
        ["span_shrinkage_fraction", cfg.span_shrinkage_fraction, "-", "0.15 means 15 percent shrinkage."],
        ["enable_rib_inc_translation", int(cfg.enable_rib_inc_translation), "0/1", "Apply loaded/non-loaded incidence, vertical and chordwise offsets."],
        ["loaded_rib_incidence_deg", cfg.loaded_rib_incidence_deg, "deg", "Incidence for loaded ribs."],
        ["nonloaded_rib_incidence_deg", cfg.nonloaded_rib_incidence_deg, "deg", "Incidence for non-loaded ribs."],
        ["loaded_rib_vertical_offset_mm", cfg.loaded_rib_vertical_offset_mm, "mm", "Vertical offset for loaded ribs."],
        ["nonloaded_rib_vertical_offset_mm", cfg.nonloaded_rib_vertical_offset_mm, "mm", "Vertical offset for non-loaded ribs."],
        ["loaded_rib_chordwise_offset_mm", cfg.loaded_rib_chordwise_offset_mm, "mm", "Manual chordwise offset for loaded ribs."],
        ["nonloaded_rib_chordwise_offset_mm", cfg.nonloaded_rib_chordwise_offset_mm, "mm", "Manual chordwise offset for non-loaded ribs."],
        ["enable_cell_ballooning", int(cfg.enable_cell_ballooning), "0/1", "Create virtual mid-cell loft sections with increased thickness."],
        ["max_thickness_increase_fraction", cfg.max_thickness_increase_fraction, "-", "0.30 means +30 percent section thickness at virtual midsections."],
        ["te_closure_mode", cfg.te_closure_mode, "text", "rounded, straight_gap or sharp_extension. sharp_extension modifies the 2D profile used by CATIA and disables TE cap panels."],
        ["enable_te_rounding", int(effective_te_rounding), "0/1", "CATIA creates automatic tangent-continuous rounded TE caps from the local TE gap only when te_closure_mode=rounded."],
        ["te_rounding_num_points", cfg.te_rounding_num_points, "-", "Point count used to approximate each rounded TE cap curve."],
        ["sharp_te_safe_gap_chord", cfg.sharp_te_safe_gap_chord, "x/c", "Tiny CATIA-safe residual TE gap used only with sharp_extension to avoid degenerate closing lines."],
        ["profile_used_normalized_path", "Profile_used/ramair_profile_used_normalized.csv", "file", "Exact normalized 2D profile used by CATIA after optional sharp TE processing."],
        ["profile_used_catia_points_path", "Profile_used/ramair_profile_used_CATIA_points_mm.csv", "file", "Exact CATIA-scaled 2D profile used by CATIA after optional sharp TE processing."],
        ["profile_used_dxf_path", "Profile_used/ramair_profile_used_2D_mm.dxf", "file", "DXF of exact 2D profile used by CATIA after optional sharp TE processing."],
        ["use_midcell_distortion_sections", int(cfg.use_midcell_distortion_sections), "0/1", "CATIA uses ramair_cell_midsections.csv to loft through bulged sections."],
        ["min_spline_point_distance_mm", cfg.min_spline_point_distance_mm, "mm", "Near-duplicate filter used by CATIA macro."],
        ["create_rib_fills", int(cfg.create_rib_fills), "0/1", "Requested CATIA rib fills."],
        ["create_loft_panels", int(cfg.create_loft_panels), "0/1", "Requested CATIA upper/lower loft panels."],
        ["create_te_closure_panels", int(effective_te_closure_panels), "0/1", "Requested CATIA TE cap panels; automatically disabled for sharp_extension because the profile is already closed in 2D."],
        ["export_iges", int(cfg.export_iges), "0/1", "Requested CATIA IGES export."],
        ["export_full_assembly_iges", int(cfg.export_full_assembly_iges), "0/1", "Requested CATIA full visible assembly IGES export."],
        ["export_full_assembly_step", int(cfg.export_full_assembly_step), "0/1", "Requested CATIA full visible assembly STEP export."],
        ["exports_subfolder", cfg.export_subfolder_name, "folder", "Export folder relative to BASE_FOLDER; CATIA will create it if needed."],
        ["force_export_to_base_folder", int(cfg.force_export_to_base_folder), "0/1", "1 exports to BASE_FOLDER\\ramair_canopy_3D.igs regardless of CSV path."],
        ["anhedral_section_orientation", cfg.anhedral_section_orientation, "text", "focus_inward or legacy_outward."],
        ["enable_crossports", int(cfg.enable_crossports), "0/1", "Create crossport guide loops on selected internal ribs."],
        ["crossports_file", "ramair_crossports.csv", "file", "Normalized crossport loops used by CATIA."],
        ["crossport_cut_mode", cfg.crossport_cut_mode, "text", "curves_only, post_split_surfaces, or legacy fill_inner_boundaries."],
        ["crossport_split_orientation", int(cfg.crossport_split_orientation), "-1/1", "CATIA split kept-side orientation for post_split_surfaces; flip if holes keep the wrong side."],
        ["crossport_cut_strategy", cfg.crossport_cut_strategy, "text", "curve_split_first recommended; extruded_wall_split and curve_split available."],
        ["crossport_cutter_extrude_mode", cfg.crossport_cutter_extrude_mode, "text", "auto_semicell_span or fixed."],
        ["crossport_cutter_extrude_factor_semicell", cfg.crossport_cutter_extrude_factor_semicell, "-", "Automatic cutter wall half-length factor times smallest semi-cell span."],
        ["crossport_cutter_extrude_min_mm", cfg.crossport_cutter_extrude_min_mm, "mm", "Lower clamp for automatic cutter wall half-length."],
        ["crossport_cutter_extrude_max_mm", cfg.crossport_cutter_extrude_max_mm, "mm", "Upper clamp for automatic cutter wall half-length."],
        ["crossport_cutter_extrude_mm", effective_cutter_extrude_mm, "mm", "Effective half-length used by CATIA if a cutter-wall fallback is needed."],
        ["crossport_shape", cfg.crossport_shape, "text", "circle or ellipse."],
        ["crossport_ellipse_orientation", cfg.crossport_ellipse_orientation, "text", "horizontal, vertical, or auto."],
        ["crossport_count", cfg.crossport_count, "-", "Number of generated crossport loops when no custom specs are provided."],
        ["crossport_position_mode", cfg.crossport_position_mode, "text", "standard_3, equidistant, or custom."],
        ["crossport_apply_to", cfg.crossport_apply_to, "text", "all_internal, loaded_internal, or nonloaded_internal."],
        ["crossport_width_fraction_chord", cfg.crossport_width_fraction_chord, "-", "Total chordwise crossport width divided by local chord."],
        ["crossport_height_fraction_local_thickness", cfg.crossport_height_fraction_local_thickness, "-", "Total vertical crossport height divided by local thickness."],
        ["crossport_edge_clearance_fraction_local_thickness", cfg.crossport_edge_clearance_fraction_local_thickness, "-", "Clearance to upper/lower profile as fraction of local thickness."],
        ["crossport_points_per_loop", cfg.crossport_points_per_loop, "-", "Spline points per crossport loop."],
        ["opening_as_straight_line", 1, "0/1", "1 = straight LE opening between upper LE and lower LE."],
        ["export_dxf_path", "Canopy\\LS1_0417_ramair_profile_2D_mm.dxf", "path", "2D rib export path relative to BASE_FOLDER."],
        ["export_igs_path", cfg.export_subfolder_name + "\\" + cfg.export_canopy_filename, "path", "Canopy/main CATIA IGES export path relative to BASE_FOLDER."],
        ["export_full_assembly_igs_path", cfg.export_subfolder_name + "\\" + cfg.export_full_filename, "path", "Full visible assembly IGES export path relative to BASE_FOLDER."],
        ["export_step_path", cfg.export_subfolder_name + "\\" + cfg.export_full_filename.replace(".igs", ".stp"), "path", "Optional STEP export path relative to BASE_FOLDER."],
    ]
    with (out_dir / "ramair_global_inputs.csv").open("w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(rows)


def _line_intersection_2d(p1: np.ndarray, p2: np.ndarray, q1: np.ndarray, q2: np.ndarray) -> tuple[bool, np.ndarray]:
    """Return intersection of two infinite 2D lines p1-p2 and q1-q2 in x-z profile space."""
    p1 = np.asarray(p1, dtype=float); p2 = np.asarray(p2, dtype=float)
    q1 = np.asarray(q1, dtype=float); q2 = np.asarray(q2, dtype=float)
    r = p2 - p1
    s = q2 - q1
    den = r[0] * s[1] - r[1] * s[0]
    if abs(den) < 1.0e-12:
        return False, 0.5 * (p1 + q1)
    qp = q1 - p1
    t = (qp[0] * s[1] - qp[1] * s[0]) / den
    return True, p1 + t * r


def _linear_extrapolate_z_at_x(p_te: np.ndarray, p_next: np.ndarray, x_target: float) -> float:
    """Linear extrapolation of z(x) from two TE-side points."""
    dx = float(p_te[0] - p_next[0])
    if abs(dx) < 1.0e-12:
        return float(p_te[1])
    slope = float(p_te[1] - p_next[1]) / dx
    return float(p_te[1] + slope * (float(x_target) - float(p_te[0])))


def apply_te_closure_mode(upper: pd.DataFrame, lower: pd.DataFrame, cfg: Config) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Apply the selected trailing-edge closure strategy to the 2D point cloud before CATIA export.

    ``rounded`` and ``straight_gap`` keep the uploaded profile unchanged.

    ``sharp_extension`` is intentionally handled in 2D, before CATIA, instead of asking CATIA
    to extend/split surfaces afterwards.  The method is robust and conservative:
      1. It detects the existing upper TE and lower TE points from the ordered point cloud.
      2. It defines a common sharp TE x-location at least SHARP_TE_MIN_EXTENSION_X_C.
      3. It estimates the TE z-location from the TE-side tangents of the upper and lower branches.
      4. It writes a CATIA-safe nearly-closed TE: upper and lower end points are separated by a
         tiny gap SHARP_TE_SAFE_GAP_CHORD.  This avoids a zero-length TE boundary line in CATIA.

    The resulting profile is the one exported under Canopy/Profile_used and is also the profile
    used by the CATIA macro.  The tiny gap is negligible for 2D analysis but prevents unstable
    degenerate curves in CATIA V5.
    """
    mode = str(getattr(cfg, "te_closure_mode", "rounded")).lower().strip()
    up = upper.copy().reset_index(drop=True)
    lo = lower.copy().reset_index(drop=True)
    meta = {"te_closure_mode": mode, "te_modified": False, "te_method": "input_profile"}
    if mode != "sharp_extension":
        return up, lo, meta
    if len(up) < 2 or len(lo) < 2:
        meta["te_method"] = "fallback_insufficient_points"
        return up, lo, meta

    # Profile ordering convention:
    #   UPPER starts at TE and ends at LE.
    #   LOWER starts at LE and ends at TE.
    up_te = np.array([float(up.loc[0, "x"]), float(up.loc[0, "y"])], dtype=float)
    up_next = np.array([float(up.loc[1, "x"]), float(up.loc[1, "y"])], dtype=float)
    lo_prev = np.array([float(lo.loc[len(lo)-2, "x"]), float(lo.loc[len(lo)-2, "y"])], dtype=float)
    lo_te = np.array([float(lo.loc[len(lo)-1, "x"]), float(lo.loc[len(lo)-1, "y"])], dtype=float)

    x_te = max(float(cfg.sharp_te_min_extension_x_c), float(up_te[0]), float(lo_te[0]))
    x_te = min(x_te, float(cfg.sharp_te_intersection_max_x_c))

    # Tangent extrapolation in the 2D point cloud.  If this gives an extreme z, use midpoint fallback.
    z_up_ext = _linear_extrapolate_z_at_x(up_te, up_next, x_te)
    z_lo_ext = _linear_extrapolate_z_at_x(lo_te, lo_prev, x_te)
    z_mid_current = 0.5 * (float(up_te[1]) + float(lo_te[1]))
    z_common = 0.5 * (z_up_ext + z_lo_ext)
    if (not np.isfinite(z_common)) or abs(z_common - z_mid_current) > 0.10:
        z_common = z_mid_current
        method = "midpoint_fallback_pointcloud"
    else:
        method = "pointcloud_tangent_blend"

    # CATIA-safe almost-zero gap.  Exactly coincident TE end points can make AddNewLinePtPt
    # fail or make Fill boundaries ambiguous.  The gap is negligible at model scale.
    gap = max(0.0, float(getattr(cfg, "sharp_te_safe_gap_chord", 1.0e-5)))
    # Keep the residual gap below the CATIA boundary-join tolerance even if the model is scaled up.
    gap = min(gap, 0.02 / max(float(cfg.chord_mm), 1.0))
    up.loc[0, "x"] = x_te
    lo.loc[len(lo)-1, "x"] = x_te
    up.loc[0, "y"] = z_common + 0.5 * gap
    lo.loc[len(lo)-1, "y"] = z_common - 0.5 * gap

    # Safety: if the new TE is too close to the adjacent point, move it slightly aft in x.
    min_dx = 2.0e-5
    if abs(float(up.loc[0, "x"]) - float(up.loc[1, "x"])) < min_dx:
        up.loc[0, "x"] = float(up.loc[1, "x"]) + min_dx
        lo.loc[len(lo)-1, "x"] = max(float(lo.loc[len(lo)-2, "x"]) + min_dx, float(up.loc[0, "x"]))

    meta.update({
        "te_modified": True,
        "te_method": method,
        "sharp_te_x_c": float(0.5 * (up.loc[0, "x"] + lo.loc[len(lo)-1, "x"])),
        "sharp_te_z_c": float(z_common),
        "sharp_te_safe_gap_chord": gap,
        "sharp_te_safe_gap_mm": gap * float(cfg.chord_mm),
        "note": "Profile was closed in 2D before CATIA import. Tiny TE gap is deliberate to avoid zero-length CATIA boundary curves.",
    })
    return up, lo, meta

def write_profile_used_outputs(out_dir: Path, upper: pd.DataFrame, lower: pd.DataFrame, cfg: Config, meta: dict) -> None:
    """Write the exact 2D profile used by CATIA for later 2D CFD/FEM analyses."""
    prof_dir = Path(out_dir) / "Profile_used"
    prof_dir.mkdir(parents=True, exist_ok=True)
    profile_used = pd.concat([upper, lower], ignore_index=True)
    profile_used.to_csv(prof_dir / "ramair_profile_used_normalized.csv", index=False, float_format="%.9f")
    # Also export CATIA-scaled profile points and a DXF preview.
    cat_upper = to_catia_points(upper, "UPPER", cfg.chord_mm)
    cat_lower = to_catia_points(lower, "LOWER", cfg.chord_mm)
    macro_points = pd.concat([cat_upper, cat_lower], ignore_index=True)
    macro_points.insert(0, "point_id", np.arange(1, len(macro_points) + 1))
    macro_points.to_csv(prof_dir / "ramair_profile_used_CATIA_points_mm.csv", index=False, float_format="%.9f")
    upper_pts = [(float(r["x"]) * cfg.chord_mm, float(r["y"]) * cfg.chord_mm) for _, r in upper.iterrows()]
    lower_pts = [(float(r["x"]) * cfg.chord_mm, float(r["y"]) * cfg.chord_mm) for _, r in lower.iterrows()]
    write_dxf_r12(prof_dir / "ramair_profile_used_2D_mm.dxf", upper_pts, lower_pts)
    (prof_dir / "ramair_profile_used_info.txt").write_text("\n".join([f"{k}: {v}" for k, v in meta.items()]), encoding="utf-8")

def write_summary(cfg: Config, out_dir: Path, upper: pd.DataFrame, lower: pd.DataFrame, cell_df: pd.DataFrame, rib_df: pd.DataFrame, mid_df: pd.DataFrame, crossport_df: pd.DataFrame) -> None:
    effective_cutter_extrude_mm = compute_crossport_cutter_extrude_mm(cfg, rib_df)
    lines = []
    lines.append("Ram-air CATIA input generation summary")
    lines.append("=====================================")
    lines.append(f"Input CSV: {cfg.input_csv}")
    lines.append(f"Output folder: {out_dir.resolve()}")
    lines.append(f"Profile points: UPPER={len(upper)}, LOWER={len(lower)}")
    lines.append(f"Cells: {cfg.cells}")
    lines.append(f"Rib stations: {len(rib_df)}")
    lines.append(f"Virtual midsections: {len(mid_df)}")
    lines.append(f"Nominal span: {cfg.span_total_mm:.6f} mm")
    lines.append(f"Effective span: {cfg.span_effective_mm:.6f} mm")
    lines.append(f"Center chord: {cfg.chord_mm:.6f} mm")
    lines.append(f"Variable chord: {cfg.enable_variable_chord} ({cfg.chord_distribution_mode})")
    lines.append(f"Variable cell span: {cfg.enable_variable_cell_span} ({cfg.cell_span_distribution_mode})")
    lines.append(f"Span shrinkage: {cfg.enable_span_shrinkage}, fraction={cfg.span_shrinkage_fraction:.6f}")
    lines.append(f"Rib incidence/translation: {cfg.enable_rib_inc_translation}")
    lines.append(f"Cell ballooning: {cfg.enable_cell_ballooning}, thickness increase={cfg.max_thickness_increase_fraction:.6f}")
    lines.append(f"TE closure mode: {cfg.te_closure_mode}")
    lines.append(f"TE rounding: {cfg.enable_te_rounding}, tangent cap points={cfg.te_rounding_num_points}")
    lines.append(f"Fabric thickness strategy: {cfg.fabric_thickness_strategy}, thickness={cfg.fabric_thickness_mm:.6f} mm")
    lines.append(f"Suspension line CAD strategy: {cfg.suspension_line_cad_strategy}, default diameter={cfg.default_suspension_line_diameter_mm:.6f} mm")
    lines.append(f"Chord anchor: {cfg.chord_anchor_mode} (fraction={chord_anchor_fraction(cfg):.3f})")
    lines.append(f"Anhedral arc mode: {cfg.anhedral_arc_mode}, pivot={cfg.anhedral_rotation_pivot_mode}, orientation={cfg.anhedral_section_orientation}")
    lines.append(f"Crossports: {cfg.enable_crossports}, loops={0 if crossport_df.empty else crossport_df['loop_id'].nunique()}, shape={cfg.crossport_shape}, orientation={cfg.crossport_ellipse_orientation}, cut_mode={cfg.crossport_cut_mode}, split_orientation={cfg.crossport_split_orientation}, cut_strategy={cfg.crossport_cut_strategy}, cutter_half_length_mm={effective_cutter_extrude_mm:.6f}, apply_to={cfg.crossport_apply_to}")
    lines.append("")
    lines.append("Generated files:")
    for name in [
        "ramair_profile_points_for_CATIA.csv",
        "LS1_0417_profile_CATIA_points_mm.csv",
        "ramair_global_inputs.csv",
        "ramair_cell_distribution.csv",
        "ramair_rib_stations.csv",
        "ramair_cell_midsections.csv",
        "ramair_crossports.csv",
        "LS1_0417_ramair_profile_2D_mm.dxf",
        "Profile_used/ramair_profile_used_normalized.csv",
        "Profile_used/ramair_profile_used_2D_mm.dxf",
        "ramair_fabric_shell_properties.csv",
    ]:
        lines.append(f"- {name}")
    (out_dir / "ramair_generation_summary.txt").write_text("\n".join(lines), encoding="utf-8")


def preprocess_profile(cfg: Config) -> None:
    out_dir = cfg.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    upper, lower, split_report = read_profile_branches_for_canopy(cfg)
    upper, lower, te_meta = apply_te_closure_mode(upper, lower, cfg)
    te_meta["profile_input_order_requested"] = cfg.profile_input_order
    te_meta["profile_input_order_detected"] = split_report.get("input_order_detected")
    te_meta["profile_vertical_coordinate_source"] = split_report.get("coordinate_vertical_source")
    write_profile_used_outputs(out_dir, upper, lower, cfg, te_meta)

    cat_upper = to_catia_points(upper, "UPPER", cfg.chord_mm)
    cat_lower = to_catia_points(lower, "LOWER", cfg.chord_mm)

    opening_points = pd.DataFrame(
        [
            {
                "section": "OPENING",
                "order_in_section": 1,
                "x_chord_norm": upper.iloc[-1]["x"],
                "z_chord_norm": upper.iloc[-1]["y"],
                "X_mm": upper.iloc[-1]["x"] * cfg.chord_mm,
                "Y_span_mm": 0.0,
                "Z_mm": upper.iloc[-1]["y"] * cfg.chord_mm,
            },
            {
                "section": "OPENING",
                "order_in_section": 2,
                "x_chord_norm": lower.iloc[0]["x"],
                "z_chord_norm": lower.iloc[0]["y"],
                "X_mm": lower.iloc[0]["x"] * cfg.chord_mm,
                "Y_span_mm": 0.0,
                "Z_mm": lower.iloc[0]["y"] * cfg.chord_mm,
            },
        ]
    )

    all_points = pd.concat([cat_upper, cat_lower, opening_points], ignore_index=True)
    all_points.insert(0, "point_id", np.arange(1, len(all_points) + 1))
    all_points.to_csv(out_dir / "LS1_0417_profile_CATIA_points_mm.csv", index=False, float_format="%.9f")

    macro_points = pd.concat([cat_upper, cat_lower], ignore_index=True)
    macro_points.insert(0, "point_id", np.arange(1, len(macro_points) + 1))
    macro_points.to_csv(out_dir / "ramair_profile_points_for_CATIA.csv", index=False, float_format="%.9f")

    upper_pts = [(float(r["x"]) * cfg.chord_mm, float(r["y"]) * cfg.chord_mm) for _, r in upper.iterrows()]
    lower_pts = [(float(r["x"]) * cfg.chord_mm, float(r["y"]) * cfg.chord_mm) for _, r in lower.iterrows()]
    write_dxf_r12(out_dir / "LS1_0417_ramair_profile_2D_mm.dxf", upper_pts, lower_pts)

    cell_df, rib_df, mid_df = make_cell_and_rib_tables(cfg)
    crossport_df = make_crossport_table(cfg, upper, lower)
    cell_df.to_csv(out_dir / "ramair_cell_distribution.csv", index=False, float_format="%.9f")
    rib_df.to_csv(out_dir / "ramair_rib_stations.csv", index=False, float_format="%.9f")
    mid_df.to_csv(out_dir / "ramair_cell_midsections.csv", index=False, float_format="%.9f")
    crossport_df.to_csv(out_dir / "ramair_crossports.csv", index=False, float_format="%.9f")

    write_params_csv(cfg, out_dir, rib_df)
    write_summary(cfg, out_dir, upper, lower, cell_df, rib_df, mid_df, crossport_df)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate CATIA input CSVs for a parametric ram-air canopy model.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--config", type=Path, default=None, help="Optional project JSON config, e.g. Application Support/Configurations/default_case_config.json")
    parser.add_argument("input_csv", nargs="?", type=Path, default=None, help="Input normalized profile CSV/DAT with x,y,z or x,z columns")
    parser.add_argument("--input-csv", dest="input_csv_option", type=Path, default=None, help="Alternative explicit input profile path; useful for Ubuntu scripts/profiles folders")
    parser.add_argument("--profile-input-order", choices=[
        "auto",
        "upper_TE_to_LE__lower_LE_to_TE",
        "upper_LE_to_TE__lower_TE_to_LE",
        "upper_LE_to_TE__lower_LE_to_TE",
        "closed_airfoil_standard_dat",
        "section_column",
    ], default=None, help="Input profile ordering hint for robust upper/lower splitting.")
    parser.add_argument("--reference-uncut-profile", type=Path, default=None, help="Clean NASA LS1-0417 / GA(W)-1 reference profile (.dat or .csv)")
    parser.add_argument("--ross-standard-profile", type=Path, default=None, help="Ross LS1-0417 standard inlet profile, approx. 8.4%% chord (.dat or .csv)")
    parser.add_argument("--ross-minimum-profile", type=Path, default=None, help="Ross LS1-0417 minimum inlet profile, approx. 4.0%% chord (.dat or .csv)")
    parser.add_argument("--out", type=Path, default=None, help="Output folder")
    parser.add_argument("--chord-mm", type=float, default=None, help="Center/base chord in mm")
    parser.add_argument("--span-mm", type=float, default=None, help="Nominal total span in mm before optional shrinkage")
    parser.add_argument("--cells", type=int, default=None, help="Odd number of canopy cells")
    parser.add_argument("--anhedral-deg", type=float, default=None, help="Arc/anhedral angle in degrees")
    parser.add_argument("--variable-chord", action="store_true", help="Enable variable chord distribution")
    parser.add_argument("--chord-mode", choices=["rectangular", "elliptic", "quasi_elliptic"], default=None)
    parser.add_argument("--tip-chord-factor", type=float, default=None)
    parser.add_argument("--chord-anchor", choices=["leading_edge", "trailing_edge", "mid_chord", "quarter_chord", "custom"], default=None, help="Reference line kept fixed when local chord varies")
    parser.add_argument("--chord-anchor-fraction", type=float, default=None, help="Custom anchor fraction, 0=LE, 1=TE")
    parser.add_argument("--variable-cell-span", action="store_true", help="Enable variable cell-width distribution")
    parser.add_argument("--cell-span-mode", choices=["uniform", "elliptic", "quasi_elliptic"], default=None)
    parser.add_argument("--tip-cell-width-factor", type=float, default=None)
    parser.add_argument("--span-shrinkage", type=float, default=None, help="Apply span shrinkage fraction, e.g. 0.15")
    parser.add_argument("--rib-inc-translation", action="store_true", help="Enable loaded/non-loaded incidence and translation settings")
    parser.add_argument("--cell-ballooning", action="store_true", help="Enable mid-cell thickness increase sections")
    parser.add_argument("--te-closure-mode", choices=["rounded", "straight_gap", "sharp_extension"], default=None, help="Trailing-edge closure strategy. sharp_extension modifies/export the exact 2D profile used.")
    parser.add_argument("--te-rounding", nargs="?", const="on", default=None, help="Backward compatibility: enable/disable rounded TE mode.")
    parser.add_argument("--te-rounding-points", type=int, default=None, help="Number of points for the CATIA tangent-continuous TE cap")
    parser.add_argument("--anhedral-arc-mode", choices=["tip_tangent", "center_to_tip_line"], default=None)
    parser.add_argument("--anhedral-pivot", choices=["profile_min_z", "profile_zero", "te_center"], default=None)
    parser.add_argument("--anhedral-orientation", choices=["focus_inward", "legacy_outward"], default=None)
    parser.add_argument("--crossports", action="store_true", help="Enable crossport guide loops on internal ribs")
    parser.add_argument("--crossport-cut-mode", choices=["curves_only", "post_split_surfaces", "fill_inner_boundaries"], default=None, help="post_split_surfaces is recommended; curves_only leaves guide curves; fill_inner_boundaries is legacy/experimental")
    parser.add_argument("--crossport-split-orientation", type=int, choices=[-1, 1], default=None, help="CATIA split side for crossport holes; flip between -1 and 1 if CATIA keeps the wrong side")
    parser.add_argument("--crossport-cut-strategy", choices=["curve_split_first", "extruded_wall_split", "curve_split"], default=None, help="CATIA cutting strategy for post_split_surfaces. curve_split_first is recommended; wall split is kept as fallback.")
    parser.add_argument("--crossport-cutter-extrude-mode", choices=["auto_semicell_span", "fixed"], default=None, help="Scale cutter-wall fallback length from semi-cell span or use fixed mm value")
    parser.add_argument("--crossport-cutter-extrude-factor", type=float, default=None, help="Automatic cutter-wall half-length factor times smallest semi-cell span")
    parser.add_argument("--crossport-cutter-extrude-mm", type=float, default=None, help="Fixed half-length for the CATIA extruded cutting wall in mm; used only with --crossport-cutter-extrude-mode fixed")
    parser.add_argument("--crossport-shape", choices=["circle", "ellipse"], default=None)
    parser.add_argument("--crossport-orientation", choices=["horizontal", "vertical", "auto"], default=None, help="Ellipse orientation")
    parser.add_argument("--crossport-count", type=int, default=None)
    parser.add_argument("--crossport-position-mode", choices=["standard_3", "equidistant", "custom"], default=None)
    parser.add_argument("--crossport-x-positions", type=str, default=None, help="Comma-separated normalized x/c positions, e.g. 0.25,0.45,0.65")
    parser.add_argument("--crossport-apply-to", choices=["all_internal", "loaded_internal", "nonloaded_internal"], default=None)
    parser.add_argument("--crossport-width", type=float, default=None, help="Global crossport width/chord fraction")
    parser.add_argument("--crossport-height", type=float, default=None, help="Global crossport height/local-thickness fraction")
    return parser.parse_args()


def _resolve_project_path(raw: str | Path) -> Path:
    p = Path(raw)
    return p if p.is_absolute() else PROJECT_ROOT / p


def _apply_default_case_config(cfg: Config, args: argparse.Namespace) -> Config:
    """Apply the small public JSON config without changing hidden CATIA defaults."""
    config_path = getattr(args, "config", None)
    if config_path is None:
        return cfg
    config_path = _resolve_project_path(config_path)
    data = json.loads(config_path.read_text(encoding="utf-8"))
    values = cfg.__dict__.copy()
    config_parent = config_path.parent.name.lower()
    config_root = (
        config_path.parent.parent.parent
        if config_parent == "configurations" and config_path.parent.parent.name == "Application Support"
        else config_path.parent.parent if config_parent == "configs" else Path.cwd()
    )

    def resolve_config_path(raw: str | Path) -> Path:
        p = Path(raw)
        return p if p.is_absolute() else config_root / p

    project_paths = data.get("project_paths", {})
    if project_paths.get("catia_inputs_dir"):
        values["out_dir"] = resolve_config_path(project_paths["catia_inputs_dir"])

    profile_inputs = data.get("profile_inputs", {})
    if profile_inputs.get("main_profile"):
        values["input_csv"] = resolve_config_path(profile_inputs["main_profile"])
    if profile_inputs.get("profile_input_order"):
        values["profile_input_order"] = str(profile_inputs["profile_input_order"])

    canopy = data.get("canopy_geometry", {})
    if "chord_mm" in canopy:
        values["chord_mm"] = float(canopy["chord_mm"])
    if "span_mm" in canopy:
        values["span_total_mm"] = float(canopy["span_mm"])
    if "cells" in canopy:
        values["cells"] = int(canopy["cells"])
    if "anhedral_deg" in canopy:
        values["arc_anhedral_deg"] = float(canopy["anhedral_deg"])
    if canopy.get("chord_mode"):
        values["chord_distribution_mode"] = str(canopy["chord_mode"])
        values["enable_variable_chord"] = str(canopy["chord_mode"]) != "rectangular"
    if canopy.get("chord_anchor"):
        values["chord_anchor_mode"] = str(canopy["chord_anchor"])

    airfoil = data.get("airfoil_processing", {})
    if airfoil.get("te_closure_mode"):
        values["te_closure_mode"] = str(airfoil["te_closure_mode"])
        values["enable_te_rounding"] = values["te_closure_mode"] == "rounded"
    if "te_rounding_points" in airfoil:
        values["te_rounding_num_points"] = max(3, int(airfoil["te_rounding_points"]))

    global ACTIVE_PROJECT_ROOT, CAE_2D_DIR_NAME, REFERENCE_UNCUT_PROFILE_PATH, ROSS_STANDARD_PROFILE_PATH, ROSS_MINIMUM_PROFILE_PATH
    ACTIVE_PROJECT_ROOT = config_root.resolve()
    if project_paths.get("cfd_2d_inputs_dir"):
        CAE_2D_DIR_NAME = str(project_paths["cfd_2d_inputs_dir"]).replace("\\", "/")
    if profile_inputs.get("reference_uncut_profile"):
        REFERENCE_UNCUT_PROFILE_PATH = str(resolve_config_path(profile_inputs["reference_uncut_profile"]))
    if profile_inputs.get("ross_standard_profile"):
        ROSS_STANDARD_PROFILE_PATH = str(resolve_config_path(profile_inputs["ross_standard_profile"]))
    if profile_inputs.get("ross_minimum_profile"):
        ROSS_MINIMUM_PROFILE_PATH = str(resolve_config_path(profile_inputs["ross_minimum_profile"]))

    cfd = data.get("cfd_2d", {})
    global CFD2D_DEFAULT_REYNOLDS, CFD2D_DEFAULT_MACH, CFD2D_DEFAULT_ALPHA_START_DEG, CFD2D_DEFAULT_ALPHA_END_DEG, CFD2D_DEFAULT_ALPHA_STEP_DEG
    if "reynolds" in cfd:
        CFD2D_DEFAULT_REYNOLDS = float(cfd["reynolds"])
    if "mach" in cfd:
        CFD2D_DEFAULT_MACH = float(cfd["mach"])
    if "alpha_start_deg" in cfd:
        CFD2D_DEFAULT_ALPHA_START_DEG = float(cfd["alpha_start_deg"])
    if "alpha_end_deg" in cfd:
        CFD2D_DEFAULT_ALPHA_END_DEG = float(cfd["alpha_end_deg"])
    if "alpha_step_deg" in cfd:
        CFD2D_DEFAULT_ALPHA_STEP_DEG = float(cfd["alpha_step_deg"])

    values["use_midcell_distortion_sections"] = bool(values["enable_cell_ballooning"] or values["enable_te_rounding"])
    return Config(**values)


def config_with_cli_overrides(cfg: Config, args: argparse.Namespace) -> Config:
    values = cfg.__dict__.copy()
    if args.input_csv is not None:
        p = Path(args.input_csv)
        values["input_csv"] = p if p.is_absolute() else PROJECT_ROOT / p
    if getattr(args, "profile_input_order", None) is not None:
        values["profile_input_order"] = args.profile_input_order
    if args.out is not None:
        p = Path(args.out)
        values["out_dir"] = p if p.is_absolute() else PROJECT_ROOT / p
    if args.chord_mm is not None:
        values["chord_mm"] = args.chord_mm
    if args.span_mm is not None:
        values["span_total_mm"] = args.span_mm
    if args.cells is not None:
        values["cells"] = args.cells
    if args.anhedral_deg is not None:
        values["arc_anhedral_deg"] = args.anhedral_deg
    if args.variable_chord:
        values["enable_variable_chord"] = True
    if args.chord_mode is not None:
        values["chord_distribution_mode"] = args.chord_mode
        values["enable_variable_chord"] = args.chord_mode != "rectangular"
    if args.tip_chord_factor is not None:
        values["tip_chord_factor"] = args.tip_chord_factor
    if args.chord_anchor is not None:
        values["chord_anchor_mode"] = args.chord_anchor
    if args.chord_anchor_fraction is not None:
        values["chord_anchor_fraction"] = args.chord_anchor_fraction
        values["chord_anchor_mode"] = "custom"
    if args.variable_cell_span:
        values["enable_variable_cell_span"] = True
    if args.cell_span_mode is not None:
        values["cell_span_distribution_mode"] = args.cell_span_mode
        values["enable_variable_cell_span"] = args.cell_span_mode != "uniform"
    if args.tip_cell_width_factor is not None:
        values["tip_cell_width_factor"] = args.tip_cell_width_factor
    if args.span_shrinkage is not None:
        values["enable_span_shrinkage"] = args.span_shrinkage > 0.0
        values["span_shrinkage_fraction"] = args.span_shrinkage
    if args.rib_inc_translation:
        values["enable_rib_inc_translation"] = True
    if args.cell_ballooning:
        values["enable_cell_ballooning"] = True
    if getattr(args, "te_closure_mode", None) is not None:
        values["te_closure_mode"] = args.te_closure_mode
        values["enable_te_rounding"] = args.te_closure_mode == "rounded"
        if args.te_closure_mode == "sharp_extension":
            values["create_te_closure_panels"] = False
    if args.te_rounding is not None:
        val = str(args.te_rounding).strip().lower()
        rounded = val not in {"0", "false", "no", "off"}
        values["enable_te_rounding"] = rounded
        values["te_closure_mode"] = "rounded" if rounded else "straight_gap"
    if args.te_rounding_points is not None:
        values["te_rounding_num_points"] = max(3, int(args.te_rounding_points))
    if args.anhedral_arc_mode is not None:
        values["anhedral_arc_mode"] = args.anhedral_arc_mode
    if args.anhedral_pivot is not None:
        values["anhedral_rotation_pivot_mode"] = args.anhedral_pivot
    if args.anhedral_orientation is not None:
        values["anhedral_section_orientation"] = args.anhedral_orientation
    if args.crossports:
        values["enable_crossports"] = True
    if args.crossport_cut_mode is not None:
        values["crossport_cut_mode"] = args.crossport_cut_mode
    if args.crossport_split_orientation is not None:
        values["crossport_split_orientation"] = int(args.crossport_split_orientation)
    if args.crossport_cutter_extrude_mode is not None:
        values["crossport_cutter_extrude_mode"] = args.crossport_cutter_extrude_mode
    if args.crossport_cutter_extrude_factor is not None:
        values["crossport_cutter_extrude_factor_semicell"] = args.crossport_cutter_extrude_factor
    if args.crossport_cutter_extrude_mm is not None:
        values["crossport_cutter_extrude_mm"] = args.crossport_cutter_extrude_mm
        values["crossport_cutter_extrude_mode"] = "fixed"
    if args.crossport_shape is not None:
        values["crossport_shape"] = args.crossport_shape
    if args.crossport_orientation is not None:
        values["crossport_ellipse_orientation"] = args.crossport_orientation
    if args.crossport_count is not None:
        values["crossport_count"] = max(1, int(args.crossport_count))
    if args.crossport_position_mode is not None:
        values["crossport_position_mode"] = args.crossport_position_mode
    if args.crossport_x_positions is not None:
        values["crossport_x_positions_chord"] = tuple(float(x.strip()) for x in args.crossport_x_positions.split(",") if x.strip())
        values["crossport_position_mode"] = "custom"
    if args.crossport_apply_to is not None:
        values["crossport_apply_to"] = args.crossport_apply_to
    if args.crossport_width is not None:
        values["crossport_width_fraction_chord"] = args.crossport_width
    if args.crossport_height is not None:
        values["crossport_height_fraction_local_thickness"] = args.crossport_height

    values["use_midcell_distortion_sections"] = bool(values["enable_cell_ballooning"] or values["enable_te_rounding"])
    return Config(**values)


# =============================================================================
# OPTIONAL CANOPY STABILIZER + TIP-BULGE + SUSPENSION-LINE MODULE v15
# =============================================================================
# This is the only suspension/stabilizer section in the v11 file.  It replaces the
# duplicated v10 modules.  The canopy pre-processing above is unchanged: it still
# creates profile/rib/cell/crossport CSVs.  This section is executed afterwards and
# only when the switches below or the JSON configuration request it.
#
# IMPORTANT GEOMETRIC CONVENTION
# ------------------------------
# - CATIA coordinates keep the previous convention:
#       X = chordwise/aft direction, Y = spanwise, Z = vertical.
# - All rigging angles are interpreted in the central rib plane (X-Z), using
#       P_ref = central rib at x/c = p_ref_x_c, usually c/4.
# - theta is measured from the downward vertical payload direction.  Positive theta
#   moves the payload/slider aft in +X.
# - The relation alpha + mu = gamma + theta is only a rigging/trim geometry check.
#   Aerodynamic polars must still be evaluated at alpha_op_deg, not at alpha+mu.
# - Slider/risers/payload/AGU are placed along the payload line defined by theta.
# - R/b is solved by moving the slider+riser system along that payload line until
#   the mean structural path length equals R_target = R_over_b * b.
#
# HOW TO USE
# ----------
# 1) Leave both switches False for canopy-only generation.
# 2) Set ENABLE_CANOPY_STABILIZERS = True to generate stabilizer panels even if
#    suspension lines are disabled.
# 3) Set ENABLE_SUSPENSION_LINES = True to generate anchors, cascades, slider,
#    risers, brake actuators, payload/AGU and validation outputs.
# 4) The editable parameters are in ramair_suspension_config.json in the output
#    folder.  If it does not exist, it is created from default_system_config().

# The high-level switches ENABLE_CANOPY_STABILIZERS, ENABLE_TIP_SIDE_BULGE,
# ENABLE_SUSPENSION_LINES and SYSTEM_CONFIG_JSON are now defined in the initial
# USER SETTINGS block. They are intentionally not redefined here.


def default_system_config(cells: int = NUM_CELLS) -> dict:
    """Return the default editable JSON configuration for stabilizers and lines.

    The default values are conservative and intended for a first robust CAD build.
    Nothing in this dictionary is hardcoded into the algorithms: users can edit the
    JSON without touching Python.
    """
    return {
        "suspension": {
            "enabled": True,
            "derive_anhedral_from_R_over_b": True
        },
        "canopy_reference": {
            "p_ref_x_c": 0.25,
            "p_ref_z_mode": "chord_line",
            "description": "P_ref is defined in the central rib plane. Use chord_line to avoid contamination by profile thickness."
        },
        "banks": [
            {"name": "A", "enabled": True, "type": "structural", "x_c": 0.10, "riser_group": "front"},
            {"name": "B", "enabled": True, "type": "structural", "x_c": 0.30, "riser_group": "front"},
            {"name": "C", "enabled": True, "type": "structural", "x_c": 0.60, "riser_group": "rear"},
            {"name": "D", "enabled": True, "type": "structural", "x_c": 0.80, "riser_group": "rear"},
            {"name": "BRK", "enabled": True, "type": "brake", "x_c": 0.95, "riser_group": "brake"}
        ],
        "loaded_rib_selection": {
            "mode": "all_loaded",
            "rib_ids": [],
            "enforce_symmetric_pairs": True,
            "description": "all_loaded selects every loaded boundary for the current canopy. explicit_rib_ids preserves a manual subset."
        },
        "anchors": {
            "surface": "lower",
            "z_offset_mm": 0.0,
            "name_format": "{bank}_rib{rib_id:02d}_{side}",
            "tip_anchor_mode": "auto",
            "tip_attachment_banks": ["A", "B", "C", "D"],
            "description": "Tip anchors use the lower rib profile by default. If stabilizers.active and stabilizers.attach_tip_lines_to_stabilizer are true, external structural tip anchors move to the stabilizer edge. Set tip_anchor_mode='rib' to force rib anchors or 'stabilizer' to force stabilizer anchors."
        },
        "cascades": {
            "structural": [
                {"level": 1, "group_size": 2, "fraction_to_target": 0.25, "grouping_rule": "adjacent_spanwise", "bank_applicability": ["A", "B", "C", "D"]},
                {"level": 2, "group_size": 2, "fraction_to_target": 0.55, "grouping_rule": "adjacent_spanwise", "bank_applicability": ["A", "B", "C", "D"]}
            ],
            "brake": [
                {"level": 1, "group_size": 2, "fraction_to_target": 0.20, "grouping_rule": "adjacent_spanwise", "bank_applicability": ["BRK"]},
                {"level": 2, "group_size": 2, "fraction_to_target": 0.60, "grouping_rule": "adjacent_spanwise", "bank_applicability": ["BRK"]}
            ],
            "description": "P_cascade = centroid(children) + fraction_to_target*(target-centroid). Groups are created centre-to-tip on each side to enforce mirror symmetry."
        },
        "angles": {
            "alpha_op_deg": 4.2,
            "gamma_deg": 17.05,
            "mu_deg": 1.0,
            "theta_deg": 0.0,
            "angle_tolerance_deg": 0.25,
            "description": "If theta_deg is null, theta = alpha_op + mu - gamma. v16 deliberately does not export long rigging-measurement diagnostics; the suspension/payload system is positioned using this theta relation and the report only checks the angular closure error."
        },
        "constraints": {
            "R_over_b": 0.80,
            "R_tolerance_fraction": 0.01,
            "auto_solve_R": True,
            "R_definition": "straight_anchor_to_central_confluence",
            "R_target_point": "slider_center_on_symmetry_axis",
            "path_length_dispersion_warning_fraction": 0.08,
            "max_bifurcation_angle_deg": 55.0,
            "symmetry_tolerance_mm": 1.0,
            "segment_length_symmetry_tolerance_mm": 1.0,
            "description": "R/b is solved by moving slider/risers/payload along the payload line defined by theta."
        },
        "line_properties": {
            "line_diameter_mm": 1.20,
            "material": "aramid_or_Dyneema_placeholder",
            "line_density_kg_m": 0.0010,
            "elastic_modulus_pa": 5.0e10,
            "poisson_ratio": 0.35,
            "nominal_segment_tension_N": 50.0,
            "reference_velocity_m_s": 20.0,
            "air_density_kg_m3": 1.225,
            "dynamic_viscosity_pa_s": 1.81e-5,
            "strouhal_number": 0.22,
            "line_visualization_mode": "curve",
            "cad_strategy": SUSPENSION_LINE_CAD_STRATEGY,
            "Cd0_line": 1.2,
            "segmented_drag_active": True,
            "velocity_reference": "alpha_op",
            "incident_velocity_angle_deg": None,
            "include_lower_straps_in_drag": True,
            "include_brake_lines_in_drag": True,
            "description": "For CD_lines_segmented, the incident velocity is defined in the central X-Z plane. velocity_reference='alpha_op' uses the canopy operating angle of attack; 'gamma' uses the glide angle; 'custom' uses incident_velocity_angle_deg."
        },
        "risers": {
            "mode": "four_riser_slider_corners",
            "mapping": {"A": "front", "B": "front", "C": "rear", "D": "rear", "BRK": "brake"},
            "side_y_fraction_b_if_no_slider": 0.08,
            "front_x_c_if_no_slider": 0.22,
            "rear_x_c_if_no_slider": 0.70,
            "brake_x_c_if_no_slider": 0.95,
            "initial_slider_distance_mm": None,
            "brake_actuator_offset_depth_mm": 120.0,
            "brake_actuator_offset_span_mm": 80.0,
            "brake_actuator_offset_down_mm": 80.0,
            "description": "With active slider, front/rear risers are placed at slider corners. Brake actuators are offset from rear corners."
        },
        "slider": {
            "active": True,
            "slider_area_ratio": 0.02,
            "slider_width_mm": None,
            "slider_chord_or_depth_mm": None,
            "slider_aspect_ratio": 1.0,
            "description": "Slider plane is perpendicular to the payload line. width is spanwise, depth is chordwise in the local slider plane."
        },
        "payload": {
            "active": True,
            "payload_length_mm": 700.0,
            "payload_width_mm": 800.0,
            "payload_height_mm": 500.0,
            "payload_drop_below_slider_mm": 1200.0,
            "payload_CG_offset_local_mm": [0.0, 0.0, 250.0],
            "payload_attach_offset_local_mm": [0.0, 0.0, 0.0],
            "lower_straps_active": True,
            "description": "Local axes are depth, span, down-along-payload-line. Positive local z is downward from canopy to payload."
        },
        "agu": {
            "active": True,
            "agu_length_mm": 400.0,
            "agu_width_mm": 300.0,
            "agu_height_mm": 300.0,
            "agu_fraction_between_slider_and_payload": 0.45,
            "description": "AGU is an optional oriented box between slider and payload attach point."
        },
        "brakes": {
            "active": True,
            "separate_from_structural": True,
            "brake_anchor_x_c": 0.95,
            "brake_anchor_distribution": "loaded_ribs",
            "brake_target": "agu_if_active",
            "brake_deflection_mode": "none",
            "brake_deflection_mm": 0.0,
            "brake_line_shortening_mm": 0.0,
            "trailing_edge_displacement_mm": 0.0,
            "description": "Brake lines are independent from A/B/C/D. By default they terminate at AGU_CONTROL when the AGU is active, otherwise at the slider brake actuator."
        },
        "stabilizers": {
            "active": False,
            "shape": "triangular",
            "chord_start_x_c": 0.15,
            "chord_end_x_c": 0.95,
            "apex_x_c": 0.62,
            "height_mm": 450.0,
            "sweep_mm": 0.0,
            "lower_edge_scale": 0.85,
            "height_direction": "rib_down",
            "surface_reference": "lower",
            "triangular_anchor_edge_mode": "lower_perimeter_by_x",
            "attach_to_loaded_ribs": True,
            "attach_tip_lines_to_stabilizer": False,
            "description": "Independent side panel on the two external ribs. For line anchors, this is the single main switch: set this true to move external structural tip anchors to the stabilizer edge. Default triangular panel uses UP_LE and UP_TE on the external rib plus an apex controlled by apex_x_c and height_mm. Lines are distributed along UP_LE-apex-UP_TE according to bank x/c, not collapsed at the apex. height_direction: rib_down, global_down, span_outward."
        },
        "tip_side_bulge": {
            "active": False,
            "max_lateral_bulge_mm": TIP_SIDE_BULGE_MAX_LATERAL_MM,
            "chordwise_start_x_c": 0.00,
            "chordwise_end_x_c": 1.00,
            "chordwise_peak_x_c": 0.50,
            "chordwise_width_fraction": 0.45,
            "vertical_peak_fraction": 0.50,
            "vertical_width_fraction": 0.60,
            "shape_power": 1.0,
            "chordwise_points": TIP_SIDE_BULGE_CHORDWISE_POINTS,
            "vertical_layers": TIP_SIDE_BULGE_THICKNESS_LAYERS,
            "hide_original_flat_tip_ribs_in_CATIA": False,
            "description": "Canopy-level optional curved external side panel. The lateral bulge is zero on upper and lower boundaries and at chordwise_start/end so the generated surface closes with the external upper/lower canopy panels instead of floating apart."
        },
        "fabric_thickness": {
            "properties_enabled": ENABLE_FABRIC_THICKNESS_PROPERTIES,
            "thickness_mm": FABRIC_THICKNESS_MM,
            "density_kg_m3": FABRIC_DENSITY_KG_M3,
            "material": FABRIC_MATERIAL,
            "strategy": FABRIC_THICKNESS_STRATEGY,
            "catia_offsets_enabled": ENABLE_FABRIC_THICKNESS,
            "catia_offset_mode": FABRIC_THICKNESS_MODE,
            "description": "Recommended strategy: export mid-surfaces + shell thickness metadata for FEM; create physical thickness later in meshing/FEM software. CATIA offsets are experimental and disabled by default."
        },
        "outputs": {
            "write_visualizer_png": True,
            "visualizer_png": "ramair_suspension_network_preview.png",
            "write_json_used": True,
            "catia_create_points_and_lines": True,
            "catia_create_surfaces": True,
            "catia_create_tubes": False,
            "export_subfolder": EXPORT_SUBFOLDER_NAME,
            "labels": False
        }
    }


def _load_system_json(out_dir: Path, cells: int) -> dict:
    import json
    cfg_path = Path(out_dir) / SYSTEM_CONFIG_JSON
    if not cfg_path.exists():
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        cfg_path.write_text(json.dumps(default_system_config(cells), indent=2), encoding="utf-8")
    return json.loads(cfg_path.read_text(encoding="utf-8"))


def _write_system_template(path: Path, cells: int) -> None:
    import json
    Path(path).write_text(json.dumps(default_system_config(cells), indent=2), encoding="utf-8")


def _deep_get(d: dict, keys: list[str], default=None):
    cur = d
    for key in keys:
        if not isinstance(cur, dict) or key not in cur:
            return default
        cur = cur[key]
    return cur


def _as_vec(v, default=(0.0, 0.0, 0.0)) -> np.ndarray:
    if v is None:
        v = default
    return np.asarray(v, dtype=float)


def maybe_apply_suspension_beta_to_canopy_cfg(cfg: Config) -> Config:
    """Optionally set canopy anhedral from beta = b/(4R).

    Because R = R_over_b*b, beta_rad = 1/(4*R_over_b).  This is applied before
    the canopy CSVs are generated, so the canopy arc and suspension preprocessor
    are consistent.  It is only active when ENABLE_SUSPENSION_LINES is True and
    the JSON requests derive_anhedral_from_R_over_b.
    """
    if not ENABLE_SUSPENSION_LINES:
        return cfg
    try:
        line_cfg = _load_system_json(cfg.out_dir, cfg.cells)
    except Exception:
        return cfg
    if not _deep_get(line_cfg, ["suspension", "enabled"], True):
        return cfg
    if not _deep_get(line_cfg, ["suspension", "derive_anhedral_from_R_over_b"], False):
        return cfg
    r_over_b = float(_deep_get(line_cfg, ["constraints", "R_over_b"], 0.0))
    if r_over_b <= 0.0:
        return cfg
    beta_deg = math.degrees(1.0 / (4.0 * r_over_b))
    values = cfg.__dict__.copy()
    values["arc_anhedral_deg"] = beta_deg
    return Config(**values)


class CanopyGeometry:
    """Read the generated canopy CSVs and provide the same transforms used by CATIA."""
    def __init__(self, out_dir: Path):
        self.out_dir = Path(out_dir)
        self.params = self._read_params(self.out_dir / "ramair_global_inputs.csv")
        self.profile = pd.read_csv(self.out_dir / "ramair_profile_points_for_CATIA.csv")
        self.ribs = pd.read_csv(self.out_dir / "ramair_rib_stations.csv")
        self.cells = pd.read_csv(self.out_dir / "ramair_cell_distribution.csv")
        self.upper = self.profile[self.profile["section"].astype(str).str.upper() == "UPPER"].copy()
        self.lower = self.profile[self.profile["section"].astype(str).str.upper() == "LOWER"].copy()
        self.half_span = float(np.max(np.abs(self.ribs["Y_flat_mm"].to_numpy(dtype=float))))
        self.span_effective_mm = float(self.params.get("span_effective_mm", self.params.get("span_total_mm", 2.0 * self.half_span)))
        self.chord_center_mm = float(self.params.get("chord_mm", 1.0))
        self.arc_deg = float(self.params.get("arc_anhedral_deg", 0.0))
        self.anhedral_arc_mode = str(self.params.get("anhedral_arc_mode", "center_to_tip_line")).lower()
        self.anhedral_pivot_mode = str(self.params.get("anhedral_rotation_pivot_mode", "profile_min_z")).lower()
        self.anhedral_orientation = str(self.params.get("anhedral_section_orientation", "focus_inward")).lower()
        self.profile_min_z_norm = float(self.profile["z_chord_norm"].min())
        self.te_center_norm = self._te_center_norm()
        self.planform_area_mm2 = self._estimate_planform_area_mm2()

    @staticmethod
    def _read_params(path: Path) -> dict:
        data = {}
        if not path.exists():
            return data
        with path.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                k = str(row.get("parameter", "")).strip()
                v = str(row.get("value", "")).strip()
                if not k:
                    continue
                try:
                    data[k] = float(v)
                except Exception:
                    data[k] = v
        return data

    def _section_interp_z(self, section: pd.DataFrame, x_norm: float) -> float:
        pts = section[["x_chord_norm", "z_chord_norm"]].astype(float).groupby("x_chord_norm", as_index=False).mean().sort_values("x_chord_norm")
        xs = pts["x_chord_norm"].to_numpy()
        zs = pts["z_chord_norm"].to_numpy()
        x = float(np.clip(x_norm, xs.min(), xs.max()))
        return float(np.interp(x, xs, zs))

    def lower_z_norm(self, x_norm: float) -> float:
        return self._section_interp_z(self.lower, x_norm)

    def upper_z_norm(self, x_norm: float) -> float:
        return self._section_interp_z(self.upper, x_norm)

    def _te_center_norm(self) -> float:
        up = self.upper.sort_values("order_in_section").iloc[0]
        lo = self.lower.sort_values("order_in_section").iloc[-1]
        return 0.5 * (float(up["z_chord_norm"]) + float(lo["z_chord_norm"]))

    def _estimate_planform_area_mm2(self) -> float:
        y = self.ribs["Y_flat_mm"].to_numpy(dtype=float)
        c = self.ribs["chord_mm"].to_numpy(dtype=float)
        order = np.argsort(y)
        return float(np_trapezoid_compat(c[order], y[order])) if len(y) > 1 else float(self.span_effective_mm * self.chord_center_mm)

    def central_rib_row(self) -> pd.Series:
        idx = int(np.argmin(np.abs(self.ribs["Y_flat_mm"].to_numpy(dtype=float))))
        return self.ribs.iloc[idx]

    def central_reference_point(self, system_cfg: dict) -> np.ndarray:
        ref = system_cfg.get("canopy_reference", {})
        x_c = float(ref.get("p_ref_x_c", 0.25))
        z_mode = str(ref.get("p_ref_z_mode", "chord_line")).lower()
        z_norm = self.lower_z_norm(x_c) if z_mode == "lower_surface" else 0.0
        return self.transform_profile_point(x_c, z_norm, self.central_rib_row(), 0.0)

    def boundary_rib_rows(self) -> pd.DataFrame:
        return self.ribs[self.ribs["is_loaded_rib"].astype(int) == 1].copy().sort_values("Y_flat_mm").reset_index(drop=True)

    def _arc_radius(self, beta_rad: float) -> float:
        if abs(beta_rad) < 1e-12 or abs(self.half_span) < 1e-12:
            return 0.0
        denom = math.sin(beta_rad) if self.anhedral_arc_mode == "tip_tangent" else math.sin(2.0 * beta_rad)
        return 0.0 if abs(denom) < 1e-12 else self.half_span / denom

    def _arc_theta(self, y_flat: float, radius: float) -> float:
        if abs(radius) < 1e-12:
            return 0.0
        return math.asin(max(-1.0, min(1.0, y_flat / radius)))

    def _arc_pivot_z(self, chord: float, v_off: float, thickness_scale: float, inc_deg: float) -> float:
        if self.anhedral_pivot_mode == "profile_zero":
            return v_off
        if self.anhedral_pivot_mode == "te_center":
            return self.te_center_norm * thickness_scale * chord + v_off
        return self.profile_min_z_norm * thickness_scale * chord + v_off - chord * abs(math.sin(math.radians(inc_deg)))

    def transform_local(self, x_local: float, z_local: float, y_flat: float, chord: float, inc_deg: float, v_off: float, thickness_scale: float) -> np.ndarray:
        # 1) local incidence rotation in the rib plane.
        inc = math.radians(float(inc_deg))
        x_inc = x_local * math.cos(inc) + z_local * math.sin(inc)
        z_inc = -x_local * math.sin(inc) + z_local * math.cos(inc)
        # 2) arc/anhedral placement.  This mirrors the CATScript convention.
        beta = math.radians(self.arc_deg)
        if abs(self.half_span) < 1e-9 or abs(beta) < 1e-9:
            y_arc, z_arc, theta = y_flat, 0.0, 0.0
        else:
            radius = self._arc_radius(beta)
            theta = self._arc_theta(y_flat, radius)
            y_arc = y_flat
            z_arc = radius * (math.cos(theta) - 1.0)
        z_pivot = self._arc_pivot_z(chord, v_off, thickness_scale, inc_deg)
        z_rel = z_inc - z_pivot
        y_final = y_arc - z_rel * math.sin(theta) if self.anhedral_orientation == "legacy_outward" else y_arc + z_rel * math.sin(theta)
        z_final = z_arc + z_pivot + z_rel * math.cos(theta)
        return np.array([x_inc, y_final, z_final], dtype=float)

    def transform_profile_point(self, x_norm: float, z_norm: float, rib_row: pd.Series, extra_z_offset_mm: float = 0.0) -> np.ndarray:
        chord = float(rib_row["chord_mm"])
        y_flat = float(rib_row["Y_flat_mm"])
        inc = float(rib_row.get("incidence_deg", 0.0))
        v_off = float(rib_row.get("vertical_offset_mm", 0.0)) + float(extra_z_offset_mm)
        x_off = float(rib_row.get("chordwise_offset_mm", 0.0))
        t_scale = float(rib_row.get("thickness_scale", 1.0))
        return self.transform_local(float(x_norm) * chord + x_off, float(z_norm) * t_scale * chord + v_off, y_flat, chord, inc, v_off, t_scale)

    def lower_anchor_point(self, x_c: float, rib_row: pd.Series, z_offset_mm: float = 0.0) -> np.ndarray:
        return self.transform_profile_point(x_c, self.lower_z_norm(x_c), rib_row, z_offset_mm)


class Node:
    def __init__(self, node_id, node_type, x, y, z, bank="", side="", rib_id="", cascade_level=0, line_type="", mirror_key="", pair_index="", label=""):
        self.node_id = str(node_id)
        self.node_type = str(node_type)
        self.x, self.y, self.z = float(x), float(y), float(z)
        self.bank, self.side, self.rib_id = str(bank), str(side), str(rib_id)
        self.cascade_level = int(cascade_level)
        self.line_type, self.mirror_key, self.pair_index, self.label = str(line_type), str(mirror_key), str(pair_index), str(label)

    @property
    def p(self) -> np.ndarray:
        return np.array([self.x, self.y, self.z], dtype=float)

    def to_dict(self) -> dict:
        return {"node_id": self.node_id, "node_type": self.node_type, "x_mm": self.x, "y_mm": self.y, "z_mm": self.z, "bank": self.bank, "side": self.side, "rib_id": self.rib_id, "cascade_level": self.cascade_level, "line_type": self.line_type, "mirror_key": self.mirror_key, "pair_index": self.pair_index, "label": self.label}


class Segment:
    def __init__(self, segment_id, start_node, end_node, bank, side, cascade_level, diameter_mm, material, line_type="structural", mirror_key="", Cd0=1.2):
        self.segment_id = str(segment_id)
        self.start_node, self.end_node = str(start_node), str(end_node)
        self.bank, self.side = str(bank), str(side)
        self.cascade_level = int(cascade_level)
        self.diameter_mm = float(diameter_mm)
        self.material, self.line_type, self.mirror_key = str(material), str(line_type), str(mirror_key)
        self.Cd0 = float(Cd0)
        self.length_mm = 0.0
        self.theta_to_velocity_deg = 0.0
        self.Cd_local = 0.0
        self.Cl_local = 0.0
        self.CD_contribution = 0.0
        self.line_density_kg_m = 0.0
        self.elastic_modulus_pa = 0.0
        self.poisson_ratio = 0.0
        self.nominal_tension_N = 0.0
        self.estimated_fn1_hz = 0.0
        self.estimated_shedding_frequency_hz = 0.0
        self.segment_unit_x = 0.0
        self.segment_unit_y = 0.0
        self.segment_unit_z = 0.0

    def to_dict(self) -> dict:
        return {
            "segment_id": self.segment_id, "start_node": self.start_node, "end_node": self.end_node,
            "bank": self.bank, "side": self.side, "cascade_level": self.cascade_level,
            "line_type": self.line_type, "length_mm": self.length_mm, "diameter_mm": self.diameter_mm,
            "material": self.material, "mirror_key": self.mirror_key,
            "theta_to_velocity_deg": self.theta_to_velocity_deg,
            "Cd_local": self.Cd_local, "Cl_local": self.Cl_local, "CD_contribution": self.CD_contribution,
            "line_density_kg_m": self.line_density_kg_m, "elastic_modulus_pa": self.elastic_modulus_pa,
            "poisson_ratio": self.poisson_ratio, "nominal_tension_N": self.nominal_tension_N,
            "estimated_fn1_hz": self.estimated_fn1_hz, "estimated_shedding_frequency_hz": self.estimated_shedding_frequency_hz,
            "segment_unit_x": self.segment_unit_x, "segment_unit_y": self.segment_unit_y, "segment_unit_z": self.segment_unit_z,
        }


class StabilizerGeometry:
    """Independent canopy-side stabilizer surfaces at the two external ribs.

    The previous implementation displaced the lower edge mostly in global Z, which did
    not necessarily follow the orientation of the external rib when anhedral/incidence
    were present.  This version builds a local basis on each external rib:
      e_chord = direction between the two upper attachment points;
      e_down  = local rib-plane downward direction obtained by perturbing the profile
                coordinate normal to the lower surface and transforming through the
                same canopy mapping.
    With height_direction='rib_down' the stabilizer lies in the same geometric plane
    as the external rib.  This is the recommended default.
    """
    def __init__(self, canopy: CanopyGeometry, system_cfg: dict):
        self.canopy, self.cfg = canopy, system_cfg.get("stabilizers", {})
        self.nodes: dict[str, Node] = {}
        self.segments: list[Segment] = []
        self.surfaces: list[dict] = []
        self.warnings: list[str] = []

    def active(self) -> bool:
        return bool(self.cfg.get("active", False) or ENABLE_CANOPY_STABILIZERS)

    @staticmethod
    def _unit(v) -> np.ndarray:
        v = np.asarray(v, dtype=float)
        n = np.linalg.norm(v)
        return v / n if n > 1e-12 else v

    def generate(self):
        if not self.active():
            return self
        ribs = self.canopy.boundary_rib_rows()
        if len(ribs) < 2:
            self.warnings.append("Stabilizers requested but external loaded ribs could not be identified.")
            return self
        self._make_side("L", ribs.iloc[0])
        self._make_side("R", ribs.iloc[-1])
        return self

    def _surface_point(self, x_c: float, rib: pd.Series) -> np.ndarray:
        mode = str(self.cfg.get("surface_reference", "lower")).lower()
        if mode == "upper":
            return self.canopy.transform_profile_point(x_c, self.canopy.upper_z_norm(x_c), rib, 0.0)
        if mode == "midline":
            z = 0.5 * (self.canopy.upper_z_norm(x_c) + self.canopy.lower_z_norm(x_c))
            return self.canopy.transform_profile_point(x_c, z, rib, 0.0)
        return self.canopy.lower_anchor_point(x_c, rib, 0.0)

    def _local_basis(self, side: str, rib: pd.Series, x_start: float, x_end: float):
        p1 = self._surface_point(x_start, rib)
        p2 = self._surface_point(x_end, rib)
        e_chord = self._unit(p2 - p1)
        x_mid = 0.5 * (x_start + x_end)
        p_mid = self._surface_point(x_mid, rib)
        chord = float(rib.get("chord_mm", self.canopy.chord_center_mm))
        dz_norm = max(0.01, min(0.05, 40.0 / max(chord, 1.0)))
        # Perturb below the local lower surface in normalized coordinates, then transform.
        p_down_ref = self.canopy.transform_profile_point(x_mid, self.canopy.lower_z_norm(x_mid) - dz_norm, rib, 0.0)
        e_rib_down = self._unit(p_down_ref - p_mid)
        if e_rib_down[2] > 0.0:
            e_rib_down = -e_rib_down
        direction = str(self.cfg.get("height_direction", "rib_down")).lower()
        if direction == "global_down":
            e_down = np.array([0.0, 0.0, -1.0])
        elif direction == "span_outward":
            e_down = np.array([0.0, -1.0 if side == "L" else 1.0, 0.0])
        else:
            e_down = e_rib_down
        return p1, p2, e_chord, self._unit(e_down)

    def _make_side(self, side: str, rib: pd.Series):
        x1 = float(self.cfg.get("chord_start_x_c", 0.15))
        x2 = float(self.cfg.get("chord_end_x_c", 0.95))
        x1, x2 = min(x1, x2), max(x1, x2)
        apex_x = float(self.cfg.get("apex_x_c", 0.62))
        height = float(self.cfg.get("height_mm", 450.0))
        sweep = float(self.cfg.get("sweep_mm", 0.0))
        scale = float(self.cfg.get("lower_edge_scale", 0.85))
        shape = str(self.cfg.get("shape", "triangular")).lower()
        p_up_le, p_up_te, e_chord, e_down = self._local_basis(side, rib, x1, x2)

        if shape == "triangular":
            # Default recommended side-stabilizer: top edge attached to external rib,
            # one adjustable apex.  Apex x/c controls where the triangular point sits
            # along the chord; height controls its distance from the external rib.
            p_apex_base = self._surface_point(apex_x, rib)
            p_low_mid = p_apex_base + sweep * e_chord + height * e_down
            pts = [("UP_LE", p_up_le), ("UP_TE", p_up_te), ("LOW_MID", p_low_mid)]
        elif shape == "partial_te":
            p_apex_base = self._surface_point(apex_x, rib)
            p_low_mid = p_apex_base + sweep * e_chord + height * e_down
            pts = [("UP_MID", p_apex_base), ("UP_TE", p_up_te), ("LOW_MID", p_low_mid)]
        else:
            mid = 0.5 * (p_up_le + p_up_te)
            p_low_le = mid + scale * (p_up_le - mid) + sweep * e_chord + height * e_down
            p_low_te = mid + scale * (p_up_te - mid) + sweep * e_chord + height * e_down
            if shape == "rectangular":
                p_low_le = p_up_le + sweep * e_chord + height * e_down
                p_low_te = p_up_te + sweep * e_chord + height * e_down
            pts = [("UP_LE", p_up_le), ("UP_TE", p_up_te), ("LOW_TE", p_low_te), ("LOW_LE", p_low_le)]

        ids = []
        for name, p in pts:
            nid = f"STAB_{side}_{name}"
            self.nodes[nid] = Node(nid, "stabilizer_point", p[0], p[1], p[2], "STAB", side, int(rib["rib_id"]), 0, "stabilizer", f"stabilizer:{name}", side, nid)
            ids.append(nid)
        self.surfaces.append({"surface_id": f"STAB_{side}_SURFACE", "surface_type": "stabilizer", "bank": "STAB", "point_ids": ids})
        for i in range(len(ids)):
            self.segments.append(Segment(f"STAB_{side}_EDGE_{i+1:02d}", ids[i], ids[(i + 1) % len(ids)], "STAB", side, 0, 0.0, "fabric_edge", "stabilizer", f"stabilizer_edge:{i+1}"))

    def interpolate_tip_anchor(self, side: str, x_c: float) -> np.ndarray | None:
        """Return a tip-line anchor point on the stabilizer boundary.

        For rectangular/trapezoidal panels, the lower edge LOW_LE--LOW_TE is used.
        For triangular panels, the old implementation returned the unique apex for
        all banks, which collapsed A/B/C/D onto a single point.  The corrected method
        maps x/c along the triangular perimeter UP_LE -> LOW_MID -> UP_TE:
          - banks forward of apex_x_c lie on UP_LE--LOW_MID,
          - banks aft of apex_x_c lie on LOW_MID--UP_TE.
        This keeps each line at its own chordwise station while still attaching it
        to the stabilizer boundary.
        """
        x = float(x_c)
        # Quadrilateral lower edge.
        if f"STAB_{side}_LOW_LE" in self.nodes and f"STAB_{side}_LOW_TE" in self.nodes:
            p1 = self.nodes[f"STAB_{side}_LOW_LE"].p
            p2 = self.nodes[f"STAB_{side}_LOW_TE"].p
            x1 = float(self.cfg.get("chord_start_x_c", 0.15))
            x2 = float(self.cfg.get("chord_end_x_c", 0.95))
            t = 0.5 if abs(x2 - x1) < 1e-9 else (x - x1) / (x2 - x1)
            return p1 + max(0.0, min(1.0, t)) * (p2 - p1)

        # Triangular perimeter.  Default mode distributes anchors along the two
        # sloping edges instead of concentrating them at the apex.
        if f"STAB_{side}_UP_LE" in self.nodes and f"STAB_{side}_UP_TE" in self.nodes and f"STAB_{side}_LOW_MID" in self.nodes:
            mode = str(self.cfg.get("triangular_anchor_edge_mode", "lower_perimeter_by_x")).lower()
            if mode == "apex":
                return self.nodes[f"STAB_{side}_LOW_MID"].p
            x1 = float(self.cfg.get("chord_start_x_c", 0.15))
            x2 = float(self.cfg.get("chord_end_x_c", 0.95))
            xa = float(self.cfg.get("apex_x_c", 0.62))
            p_le = self.nodes[f"STAB_{side}_UP_LE"].p
            p_te = self.nodes[f"STAB_{side}_UP_TE"].p
            p_ap = self.nodes[f"STAB_{side}_LOW_MID"].p
            if x <= xa:
                den = max(abs(xa - x1), 1.0e-9)
                t = max(0.0, min(1.0, (x - x1) / den))
                return p_le + t * (p_ap - p_le)
            den = max(abs(x2 - xa), 1.0e-9)
            t = max(0.0, min(1.0, (x - xa) / den))
            return p_ap + t * (p_te - p_ap)
        return None



class TipSideBulgeGeometry:
    """Optional canopy-level curved external side-wall approximation.

    v15 strategy for speed and closure:
    - The outer boundary is always exactly the transformed external rib: lower curve,
      upper curve, LE and TE boundaries are not moved. This keeps the auxiliary side
      wall connected to the existing upper/lower canopy panels.
    - The lateral bulge is applied only to internal grid points with a zero-boundary
      shape function.
    - The default CATIA representation is `loft_sections`: one loft surface per side
      through vertical section splines. This avoids the very slow previous approach
      of creating hundreds/thousands of individual Fill panels.
    - `panel_grid` is still available for debugging, but it is deliberately not the
      default because CATIA V5 can become extremely slow with many small Fill panels.
    """
    def __init__(self, canopy: CanopyGeometry, system_cfg: dict):
        self.canopy = canopy
        self.cfg = system_cfg.get("tip_side_bulge", {})
        self.nodes: dict[str, Node] = {}
        self.segments: list[Segment] = []
        self.surfaces: list[dict] = []
        self.sections: list[dict] = []
        self.warnings: list[str] = []

    def active(self) -> bool:
        return bool(self.cfg.get("active", False) or ENABLE_TIP_SIDE_BULGE)

    @staticmethod
    def _unit(v) -> np.ndarray:
        v = np.asarray(v, dtype=float)
        n = np.linalg.norm(v)
        return v / n if n > 1e-12 else v

    def _shape(self, x: float, v: float) -> float:
        """Smooth non-dimensional bulge shape with exact zero on every boundary."""
        x_start = float(self.cfg.get("chordwise_start_x_c", 0.0))
        x_end = float(self.cfg.get("chordwise_end_x_c", 1.0))
        if x_end <= x_start:
            x_start, x_end = 0.0, 1.0
        t = max(0.0, min(1.0, (float(x) - x_start) / max(x_end - x_start, 1.0e-9)))
        v = max(0.0, min(1.0, float(v)))
        pwr = max(0.25, float(self.cfg.get("shape_power", 1.0)))
        edge_x = math.sin(math.pi * t) ** pwr
        edge_v = math.sin(math.pi * v) ** pwr
        x0 = float(self.cfg.get("chordwise_peak_x_c", 0.50))
        wx = max(1.0e-6, float(self.cfg.get("chordwise_width_fraction", 0.45)))
        v0 = float(self.cfg.get("vertical_peak_fraction", 0.50))
        wv = max(1.0e-6, float(self.cfg.get("vertical_width_fraction", 0.60)))
        peak = math.exp(-((float(x) - x0) / wx) ** 2 - ((v - v0) / wv) ** 2)
        return float(edge_x * edge_v * peak)

    def _x_grid(self) -> np.ndarray:
        """Chordwise grid capped for CATIA performance.

        v14 used profile_plus_uniform by default, which could include many original
        profile x-stations and produce too many CATIA features. v15 defaults to a
        controlled cosine/uniform grid. If more fidelity is needed, increase
        chordwise_points rather than using every profile point.
        """
        x_start = max(0.0, min(0.98, float(self.cfg.get("chordwise_start_x_c", 0.0))))
        x_end = max(0.02, min(1.0, float(self.cfg.get("chordwise_end_x_c", 1.0))))
        if x_end <= x_start:
            x_start, x_end = 0.0, 1.0
        nx = max(5, int(self.cfg.get("chordwise_points", TIP_SIDE_BULGE_CHORDWISE_POINTS)))
        # hard safety cap: a loft surface does not need a very dense set of sections
        nx = min(nx, int(self.cfg.get("max_chordwise_sections", 29)))
        return np.linspace(x_start, x_end, nx)

    def _v_grid(self) -> np.ndarray:
        nv = max(5, int(self.cfg.get("vertical_layers", TIP_SIDE_BULGE_THICKNESS_LAYERS)))
        nv = min(nv, int(self.cfg.get("max_vertical_layers", 17)))
        eta = np.linspace(0.0, math.pi, nv)
        return 0.5 * (1.0 - np.cos(eta))

    def _outward_dir(self, side: str, rib_ext: pd.Series, rib_in: pd.Series) -> np.ndarray:
        mode = str(self.cfg.get("spanwise_direction_mode", "last_cell_normal")).lower()
        if mode == "global_y":
            return np.array([0.0, -1.0 if side == "L" else 1.0, 0.0], dtype=float)
        x_mid = float(self.cfg.get("chordwise_peak_x_c", 0.50))
        z_mid = 0.5 * (self.canopy.upper_z_norm(x_mid) + self.canopy.lower_z_norm(x_mid))
        p_ext = self.canopy.transform_profile_point(x_mid, z_mid, rib_ext, 0.0)
        p_in = self.canopy.transform_profile_point(x_mid, z_mid, rib_in, 0.0)
        d = p_ext - p_in
        if np.linalg.norm(d) < 1.0e-9:
            d = np.array([0.0, -1.0 if side == "L" else 1.0, 0.0])
        if side == "L" and d[1] > 0.0:
            d = -d
        if side == "R" and d[1] < 0.0:
            d = -d
        return self._unit(d)

    def generate(self):
        if not self.active():
            return self
        ribs = self.canopy.ribs.sort_values("Y_flat_mm").reset_index(drop=True)
        loaded = self.canopy.boundary_rib_rows()
        if len(loaded) < 2 or len(ribs) < 4:
            self.warnings.append("Tip side bulge requested but external/inward ribs could not be identified.")
            return self
        left_ext = loaded.iloc[0]
        right_ext = loaded.iloc[-1]
        left_in = ribs.iloc[1]
        right_in = ribs.iloc[-2]
        self._make_side("L", left_ext, left_in)
        self._make_side("R", right_ext, right_in)
        return self

    def _make_side(self, side: str, rib_ext: pd.Series, rib_in: pd.Series):
        xs = self._x_grid()
        vs = self._v_grid()
        if len(xs) * len(vs) > int(self.cfg.get("max_grid_nodes", TIP_SIDE_BULGE_MAX_GRID_NODES)):
            # Reduce vertical layers first, then chordwise sections, instead of allowing
            # a CAD-generation blow-up.
            nmax = int(self.cfg.get("max_grid_nodes", TIP_SIDE_BULGE_MAX_GRID_NODES))
            nv = max(5, min(len(vs), nmax // max(len(xs), 1)))
            vs = np.linspace(0.0, 1.0, nv)
            if len(xs) * len(vs) > nmax:
                nx = max(5, nmax // max(len(vs), 1))
                xs = np.linspace(xs[0], xs[-1], nx)
            self.warnings.append(f"Tip-side bulge grid reduced for CATIA performance: side={side}, nx={len(xs)}, nv={len(vs)}.")

        mode = str(self.cfg.get("surface_mode", TIP_SIDE_BULGE_SURFACE_MODE)).lower()
        max_b = float(self.cfg.get("max_lateral_bulge_mm", TIP_SIDE_BULGE_MAX_LATERAL_MM))
        outward = self._outward_dir(side, rib_ext, rib_in)
        grid: list[list[str]] = []
        for ix, x in enumerate(xs):
            col = []
            zu = self.canopy.upper_z_norm(float(x))
            zl = self.canopy.lower_z_norm(float(x))
            section_id = f"TIPBULGE_{side}_SEC_{ix+1:03d}"
            for iv, v in enumerate(vs):
                z = zl + float(v) * (zu - zl)
                p = self.canopy.transform_profile_point(float(x), z, rib_ext, 0.0)
                bulge = max_b * self._shape(float(x), float(v))
                if ix == 0 or ix == len(xs)-1 or iv == 0 or iv == len(vs)-1:
                    bulge = 0.0
                p = p + bulge * outward
                nid = f"TIPBULGE_{side}_{ix+1:03d}_{iv+1:03d}"
                self.nodes[nid] = Node(nid, "tip_bulge_point", p[0], p[1], p[2], "TIPBULGE", side, int(rib_ext["rib_id"]), 0, "tip_bulge", f"tip_bulge:{side}:{ix}:{iv}", side, nid)
                self.sections.append({"section_id": section_id, "side": side, "x_order": ix+1, "point_order": iv+1, "node_id": nid, "bank": "TIPBULGE"})
                col.append(nid)
            grid.append(col)

        # Boundary edges for inspection and visual closure.
        boundary = []
        boundary.extend(grid[0])
        boundary.extend([grid[ix][-1] for ix in range(1, len(xs))])
        boundary.extend(reversed(grid[-1][:-1]))
        boundary.extend([grid[ix][0] for ix in range(len(xs)-2, 0, -1)])
        for i in range(len(boundary)):
            self.segments.append(Segment(f"TIPBULGE_{side}_EDGE_{i+1:04d}", boundary[i], boundary[(i+1) % len(boundary)], "TIPBULGE", side, 0, 0.0, "fabric_edge", "tip_bulge", f"tip_bulge_edge:{side}:{i+1}"))

        # Optional fallback/debug panel grid. Default loft_sections avoids many Fill operations.
        if mode == "panel_grid":
            face_count = 1
            for ix in range(len(xs)-1):
                for iv in range(len(vs)-1):
                    ids = [grid[ix][iv], grid[ix+1][iv], grid[ix+1][iv+1], grid[ix][iv+1]]
                    self.surfaces.append({"surface_id": f"TIPBULGE_{side}_PANEL_{face_count:04d}", "surface_type": "tip_side_bulge", "bank": "TIPBULGE", "point_ids": ids})
                    face_count += 1

def _nodes_to_df(nodes: dict[str, Node]) -> pd.DataFrame:
    return pd.DataFrame([n.to_dict() for n in nodes.values()])


def _segments_to_df(segments: list[Segment]) -> pd.DataFrame:
    return pd.DataFrame([s.to_dict() for s in segments])


def _surfaces_to_df(surfaces: list[dict]) -> pd.DataFrame:
    rows = []
    for surf in surfaces:
        for i, nid in enumerate(surf.get("point_ids", []), start=1):
            rows.append({"surface_id": surf.get("surface_id", ""), "surface_type": surf.get("surface_type", ""), "bank": surf.get("bank", ""), "point_order": i, "node_id": nid})
    return pd.DataFrame(rows, columns=["surface_id", "surface_type", "bank", "point_order", "node_id"])


def _sections_to_df(sections: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(sections, columns=["section_id", "side", "x_order", "point_order", "node_id", "bank"])


def append_global_params(out_dir: Path, rows: list[list]) -> None:
    with (Path(out_dir) / "ramair_global_inputs.csv").open("a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(rows)


def write_stabilizer_outputs(out_dir: Path, stabs: StabilizerGeometry) -> None:
    out_dir = Path(out_dir)
    _nodes_to_df(stabs.nodes).to_csv(out_dir / "ramair_stabilizer_nodes.csv", index=False, float_format="%.9f")
    _segments_to_df(stabs.segments).to_csv(out_dir / "ramair_stabilizer_segments.csv", index=False, float_format="%.9f")
    _surfaces_to_df(stabs.surfaces).to_csv(out_dir / "ramair_stabilizer_surfaces.csv", index=False)
    append_global_params(out_dir, [
        ["enable_stabilizers", int(stabs.active()), "0/1", "Generate independent canopy-side stabilizer panels in CATIA."],
        ["stabilizer_nodes_file", "ramair_stabilizer_nodes.csv", "file", "Independent stabilizer point table."],
        ["stabilizer_segments_file", "ramair_stabilizer_segments.csv", "file", "Independent stabilizer boundary segments."],
        ["stabilizer_surfaces_file", "ramair_stabilizer_surfaces.csv", "file", "Independent stabilizer surface table."],
    ])



def write_tip_side_bulge_outputs(out_dir: Path, tip: TipSideBulgeGeometry) -> None:
    out_dir = Path(out_dir)
    _nodes_to_df(tip.nodes).to_csv(out_dir / "ramair_tip_bulge_nodes.csv", index=False, float_format="%.9f")
    _segments_to_df(tip.segments).to_csv(out_dir / "ramair_tip_bulge_segments.csv", index=False, float_format="%.9f")
    _surfaces_to_df(tip.surfaces).to_csv(out_dir / "ramair_tip_bulge_surfaces.csv", index=False)
    _sections_to_df(getattr(tip, "sections", [])).to_csv(out_dir / "ramair_tip_bulge_sections.csv", index=False)
    append_global_params(out_dir, [
        ["enable_tip_side_bulge", int(tip.active()), "0/1", "Create optional laterally bulged external tip rib panels in CATIA."],
        ["tip_bulge_surface_mode", str(tip.cfg.get("surface_mode", TIP_SIDE_BULGE_SURFACE_MODE)), "text", "loft_sections is fast default; panel_grid creates many small Fill surfaces; curves_only writes guide curves only."],
        ["tip_bulge_nodes_file", "ramair_tip_bulge_nodes.csv", "file", "Tip-side bulge node table."],
        ["tip_bulge_segments_file", "ramair_tip_bulge_segments.csv", "file", "Tip-side bulge boundary table."],
        ["tip_bulge_sections_file", "ramair_tip_bulge_sections.csv", "file", "Tip-side bulge section table for fast CATIA loft generation."],
        ["tip_bulge_surfaces_file", "ramair_tip_bulge_surfaces.csv", "file", "Fallback/debug faceted surface table."],
        ["tip_bulge_hide_original_rib_fills", int(bool(tip.cfg.get("hide_original_flat_tip_ribs_in_CATIA", False))), "0/1", "Hide original flat external Rib_Fill_1 and Rib_Fill_N when displaying bulged tip panels."],
    ])


class AnchorGenerator:
    """Generate canopy anchor points, optionally moving tip anchors onto stabilizers."""
    def __init__(self, canopy: CanopyGeometry, system_cfg: dict, stabs: StabilizerGeometry | None = None):
        self.canopy, self.cfg, self.stabs = canopy, system_cfg, stabs
        self.warnings: list[str] = []

    def _enabled_banks(self) -> list[dict]:
        out = []
        brake_active = bool(_deep_get(self.cfg, ["brakes", "active"], True))
        for b in self.cfg.get("banks", []):
            if not b.get("enabled", True):
                continue
            if str(b.get("type", "structural")).lower() == "brake" and not brake_active:
                continue
            out.append(b)
        return out

    def selected_ribs(self) -> pd.DataFrame:
        sel = self.cfg.get("loaded_rib_selection", {})
        mode = str(sel.get("mode", "cell_boundaries")).lower()
        boundary = self.canopy.boundary_rib_rows()
        if mode == "all_loaded":
            idxs = list(range(len(boundary)))
        elif mode == "cell_boundaries":
            idxs = [int(i) for i in sel.get("indices", [])]
        elif mode == "rib_ids":
            ids = {int(i) for i in sel.get("rib_ids", [])}
            return self.canopy.ribs[self.canopy.ribs["rib_id"].astype(int).isin(ids)].copy().sort_values("Y_flat_mm").reset_index(drop=True)
        elif mode == "station_indices":
            ids = {int(i) for i in sel.get("station_indices", [])}
            return self.canopy.ribs[self.canopy.ribs["station_index"].astype(int).isin(ids)].copy().sort_values("Y_flat_mm").reset_index(drop=True)
        else:
            raise ValueError(f"Unsupported loaded_rib_selection mode: {mode!r}")
        valid = []
        n = len(boundary)
        for i in idxs:
            if 0 <= i < n:
                valid.append(i)
            else:
                self.warnings.append(f"Loaded boundary index {i} outside available range 0..{n-1}.")
        if sel.get("enforce_symmetric_pairs", True):
            missing = [(i, n - 1 - i) for i in valid if (n - 1 - i) not in valid]
            if missing:
                self.warnings.append(f"Loaded rib selection is not symmetric. Missing mirror boundary indices: {missing}.")
        return boundary.iloc[valid].copy().reset_index(drop=True)

    @staticmethod
    def _pair_index(rib: pd.Series, ribs: pd.DataFrame) -> str:
        vals = sorted(set(round(abs(float(v)), 6) for v in ribs["Y_flat_mm"].to_numpy(dtype=float)))
        val = round(abs(float(rib["Y_flat_mm"])), 6)
        return str(vals.index(val)) if val in vals else f"absY{val:.3f}"

    def generate(self) -> dict[str, Node]:
        nodes = {}
        ribs = self.selected_ribs()
        z_off = float(_deep_get(self.cfg, ["anchors", "z_offset_mm"], 0.0))
        fmt = _deep_get(self.cfg, ["anchors", "name_format"], "{bank}_rib{rib_id:02d}_{side}")
        if ribs.empty:
            self.warnings.append("No ribs selected for suspension anchors.")
            return nodes
        ymin, ymax = float(ribs["Y_flat_mm"].min()), float(ribs["Y_flat_mm"].max())
        # v15 simplification: only one user-facing switch is needed.
        # Recommended use: set stabilizers.active=True and stabilizers.attach_tip_lines_to_stabilizer=True.
        # anchors.tip_anchor_mode can override this only when needed:
        #   'auto'       -> use stabilizer switch if stabilizer geometry exists;
        #   'rib'        -> force anchors on the external rib profile;
        #   'stabilizer' -> force anchors on the stabilizer edge.
        tip_mode = str(_deep_get(self.cfg, ["anchors", "tip_anchor_mode"], "auto")).lower()
        stab_switch = bool(_deep_get(self.cfg, ["stabilizers", "attach_tip_lines_to_stabilizer"], False))
        stab_available = self.stabs is not None and self.stabs.active()
        if tip_mode == "rib":
            attach_tip = False
        elif tip_mode == "stabilizer":
            attach_tip = True
        else:
            attach_tip = bool(stab_switch and stab_available)
        tip_banks = {str(x).upper() for x in _deep_get(self.cfg, ["anchors", "tip_attachment_banks"], ["A", "B", "C", "D"])}
        for _, rib in ribs.iterrows():
            y = float(rib["Y_flat_mm"])
            side = "L" if y < 0.0 else "R"
            rib_id = int(rib["rib_id"])
            pair = self._pair_index(rib, ribs)
            is_tip = abs(y - ymin) < 1e-6 or abs(y - ymax) < 1e-6
            for bank in self._enabled_banks():
                bank_name = str(bank["name"]).upper()
                line_type = str(bank.get("type", "structural")).lower()
                x_c = float(bank.get("x_c", 0.5))
                if line_type == "brake":
                    x_c = float(_deep_get(self.cfg, ["brakes", "brake_anchor_x_c"], x_c))
                p = None
                if is_tip and attach_tip and bank_name in tip_banks and self.stabs is not None and self.stabs.active():
                    p = self.stabs.interpolate_tip_anchor(side, x_c)
                if p is None:
                    p = self.canopy.lower_anchor_point(x_c, rib, z_off)
                if line_type == "brake":
                    p = p + np.array([0.0, 0.0, -abs(float(_deep_get(self.cfg, ["brakes", "trailing_edge_displacement_mm"], 0.0)))])
                nid = fmt.format(bank=bank_name, rib_id=rib_id, side=side)
                nodes[nid] = Node(nid, "anchor", p[0], p[1], p[2], bank_name, side, rib_id, 0, line_type, f"anchor:{bank_name}:pair{pair}", pair, nid)
        return nodes


class SliderPayloadSystem:
    """Place slider, risers, brake actuators, payload and AGU from angles and R.

    Important interpretation:
    - P_ref is the central rib c/4 point.
    - theta is measured from the downward vertical in the central X-Z plane.
    - The solver moves the slider/riser plane along d_down until the mean structural
      path length from canopy anchors to slider/riser targets matches R_over_b*b.
    - Lower straps from slider/riser to payload/AGU are not included in R/b, but they
      are included in exposed-length drag if include_lower_straps_in_drag=True.
    """
    def __init__(self, canopy: CanopyGeometry, cfg: dict):
        self.canopy, self.cfg = canopy, cfg
        self.P_ref = canopy.central_reference_point(cfg)
        self.theta_deg, self.angle_warning = self._theta()
        self.d_down = self._unit([math.sin(math.radians(self.theta_deg)), 0.0, -math.cos(math.radians(self.theta_deg))])
        self.e_span = np.array([0.0, 1.0, 0.0])
        self.e_depth = self._unit([math.cos(math.radians(self.theta_deg)), 0.0, math.sin(math.radians(self.theta_deg))])
        self.last_slider_area = 0.0
        self.last_slider_width = 0.0
        self.last_slider_depth = 0.0

    @staticmethod
    def _unit(v) -> np.ndarray:
        arr = np.asarray(v, dtype=float)
        n = np.linalg.norm(arr)
        return arr / n if n > 1e-12 else arr

    def local_vec(self, depth: float, span: float, down: float) -> np.ndarray:
        return float(depth) * self.e_depth + float(span) * self.e_span + float(down) * self.d_down

    def _theta(self) -> tuple[float, str]:
        alpha = float(_deep_get(self.cfg, ["angles", "alpha_op_deg"], 0.0))
        gamma = float(_deep_get(self.cfg, ["angles", "gamma_deg"], 0.0))
        mu = float(_deep_get(self.cfg, ["angles", "mu_deg"], 0.0))
        theta = _deep_get(self.cfg, ["angles", "theta_deg"], None)
        if theta is None:
            return alpha + mu - gamma, ""
        theta = float(theta)
        err = abs((alpha + mu) - (gamma + theta))
        tol = float(_deep_get(self.cfg, ["angles", "angle_tolerance_deg"], 0.25))
        warn = "" if err <= tol else f"Angular relation warning: alpha+mu={alpha+mu:.3f} deg, gamma+theta={gamma+theta:.3f} deg, error={err:.3f} deg."
        return theta, warn

    def slider_dimensions(self) -> tuple[float, float, float]:
        s = self.cfg.get("slider", {})
        area = float(s.get("slider_area_ratio", 0.02)) * self.canopy.planform_area_mm2
        width = s.get("slider_width_mm", None)
        depth = s.get("slider_chord_or_depth_mm", None)
        aspect = float(s.get("slider_aspect_ratio", 1.0))
        if width is None and depth is None:
            width = math.sqrt(max(area, 1.0) * aspect)
            depth = max(area, 1.0) / max(width, 1.0)
        elif width is None:
            depth = float(depth)
            width = max(area, 1.0) / max(depth, 1.0)
        elif depth is None:
            width = float(width)
            depth = max(area, 1.0) / max(width, 1.0)
        self.last_slider_area, self.last_slider_width, self.last_slider_depth = float(area), float(width), float(depth)
        return float(width), float(depth), float(area)

    def _add_oriented_box(self, nodes: dict[str, Node], segments: list[Segment], surfaces: list[dict], prefix: str, center: np.ndarray, L: float, W: float, H: float, bank: str):
        coords = {
            "FLL": (-0.5*L, -0.5*W, -0.5*H), "FLU": (-0.5*L, -0.5*W,  0.5*H),
            "FRL": (-0.5*L,  0.5*W, -0.5*H), "FRU": (-0.5*L,  0.5*W,  0.5*H),
            "RLL": ( 0.5*L, -0.5*W, -0.5*H), "RLU": ( 0.5*L, -0.5*W,  0.5*H),
            "RRL": ( 0.5*L,  0.5*W, -0.5*H), "RRU": ( 0.5*L,  0.5*W,  0.5*H),
        }
        for key, (dx, dy, dz) in coords.items():
            p = center + self.local_vec(dx, dy, dz)
            nid = f"{prefix}_{key}"
            nodes[nid] = Node(nid, f"{prefix.lower()}_corner", p[0], p[1], p[2], bank, "", "", 0, prefix.lower(), f"{prefix}:box:{key}", key, nid)
        faces = [("BOTTOM", ["FLL", "FRL", "RRL", "RLL"]), ("TOP", ["FLU", "FRU", "RRU", "RLU"]), ("LEFT", ["FLL", "FLU", "RLU", "RLL"]), ("RIGHT", ["FRL", "FRU", "RRU", "RRL"]), ("FRONT", ["FLL", "FRL", "FRU", "FLU"]), ("REAR", ["RLL", "RRL", "RRU", "RLU"])]
        for face, ids in faces:
            surfaces.append({"surface_id": f"{prefix}_{face}", "surface_type": prefix.lower(), "bank": bank, "point_ids": [f"{prefix}_{i}" for i in ids]})
        edges = [("FLL","FRL"),("FRL","RRL"),("RRL","RLL"),("RLL","FLL"),("FLU","FRU"),("FRU","RRU"),("RRU","RLU"),("RLU","FLU"),("FLL","FLU"),("FRL","FRU"),("RRL","RRU"),("RLL","RLU")]
        for i, (a, b) in enumerate(edges, start=1):
            segments.append(Segment(f"{prefix}_EDGE_{i:02d}", f"{prefix}_{a}", f"{prefix}_{b}", bank, "", 0, 0.0, f"{prefix.lower()}_edge", prefix.lower(), f"{prefix}:edge:{i}"))

    def create_for_slider_distance(self, distance_mm: float):
        nodes: dict[str, Node] = {}
        segments: list[Segment] = []
        surfaces: list[dict] = []
        center = self.P_ref + float(distance_mm) * self.d_down
        width, depth, area = self.slider_dimensions()
        # Central crown-rigging reference/confluence point.  It always lies on
        # the canopy symmetry plane (Y=0) along the payload line.  R/b is checked
        # against straight distances from canopy anchors to this point, not against
        # the summed cascade path length.
        nodes["CONFLUENCE_CENTER"] = Node("CONFLUENCE_CENTER", "confluence_center", center[0], 0.0, center[2], "RIG", "C", "", 0, "reference", "confluence:center", "center", "CONFLUENCE_CENTER")
        nodes["SLIDER_CENTER"] = Node("SLIDER_CENTER", "slider_center", center[0], 0.0, center[2], "SLIDER", "C", "", 0, "slider", "slider:center", "center", "SLIDER_CENTER")
        slider_active = bool(_deep_get(self.cfg, ["slider", "active"], True))
        if slider_active:
            local = {"FL": (-0.5*depth, -0.5*width, 0.0), "FR": (-0.5*depth, 0.5*width, 0.0), "RR": (0.5*depth, 0.5*width, 0.0), "RL": (0.5*depth, -0.5*width, 0.0)}
            for key, xyz in local.items():
                p = center + self.local_vec(*xyz)
                side = "L" if key.endswith("L") else "R"
                nodes[f"SLIDER_{key}"] = Node(f"SLIDER_{key}", "slider_corner", p[0], p[1], p[2], "SLIDER", side, "", 0, "slider", f"slider:{key[0]}", key, f"SLIDER_{key}")
            surfaces.append({"surface_id": "SLIDER_SURFACE", "surface_type": "slider", "bank": "SLIDER", "point_ids": ["SLIDER_FL", "SLIDER_FR", "SLIDER_RR", "SLIDER_RL"]})
            for i, (a, b) in enumerate([("SLIDER_FL", "SLIDER_FR"), ("SLIDER_FR", "SLIDER_RR"), ("SLIDER_RR", "SLIDER_RL"), ("SLIDER_RL", "SLIDER_FL")], start=1):
                segments.append(Segment(f"SLIDER_EDGE_{i:02d}", a, b, "SLIDER", "", 0, 0.0, "slider_edge", "slider", f"slider_edge:{i}"))
            riser_p = {"L_front_riser": nodes["SLIDER_FL"].p, "R_front_riser": nodes["SLIDER_FR"].p, "L_rear_riser": nodes["SLIDER_RL"].p, "R_rear_riser": nodes["SLIDER_RR"].p}
        else:
            r = self.cfg.get("risers", {})
            b = self.canopy.span_effective_mm
            c = self.canopy.chord_center_mm
            yside = float(r.get("side_y_fraction_b_if_no_slider", 0.08)) * b
            front = float(r.get("front_x_c_if_no_slider", 0.22)) * c
            rear = float(r.get("rear_x_c_if_no_slider", 0.70)) * c
            riser_p = {"L_front_riser": center + self.local_vec(front, -yside, 0), "R_front_riser": center + self.local_vec(front, yside, 0), "L_rear_riser": center + self.local_vec(rear, -yside, 0), "R_rear_riser": center + self.local_vec(rear, yside, 0)}
        for nid, p in riser_p.items():
            side = "L" if nid.startswith("L_") else "R"
            group = "front" if "front" in nid else "rear"
            nodes[nid] = Node(nid, "riser", p[0], p[1], p[2], "", side, "", 0, "structural", f"riser:{group}", group, nid)
        r = self.cfg.get("risers", {})
        for side in ["L", "R"]:
            sign = -1.0 if side == "L" else 1.0
            rear = nodes[f"{side}_rear_riser"].p
            p = rear + self.local_vec(float(r.get("brake_actuator_offset_depth_mm", 120.0)), sign * float(r.get("brake_actuator_offset_span_mm", 80.0)), float(r.get("brake_actuator_offset_down_mm", 80.0)))
            nid = f"{side}_brake_actuator"
            nodes[nid] = Node(nid, "brake_actuator", p[0], p[1], p[2], "BRK", side, "", 0, "brake", "riser:brake", "brake", nid)

        payload_attach_id = "PAYLOAD_ATTACH"
        payload_cfg = self.cfg.get("payload", {})
        payload_attach = None
        if payload_cfg.get("active", False):
            drop = float(payload_cfg.get("payload_drop_below_slider_mm", 1200.0))
            payload_attach = center + drop * self.d_down + self.local_vec(*_as_vec(payload_cfg.get("payload_attach_offset_local_mm", [0, 0, 0])))
            nodes[payload_attach_id] = Node(payload_attach_id, "payload_attach", payload_attach[0], payload_attach[1], payload_attach[2], "PAYLOAD", "", "", 0, "payload", "payload:attach", "", payload_attach_id)
            cg = payload_attach + self.local_vec(*_as_vec(payload_cfg.get("payload_CG_offset_local_mm", [0, 0, 250])))
            nodes["PAYLOAD_CG"] = Node("PAYLOAD_CG", "payload_cg", cg[0], cg[1], cg[2], "PAYLOAD", "", "", 0, "payload", "payload:cg", "", "PAYLOAD_CG")
            self._add_oriented_box(nodes, segments, surfaces, "PAYLOAD", cg, float(payload_cfg.get("payload_length_mm", 700.0)), float(payload_cfg.get("payload_width_mm", 800.0)), float(payload_cfg.get("payload_height_mm", 500.0)), "PAYLOAD")

        agu_cfg = self.cfg.get("agu", {})
        agu_cg = None
        if agu_cfg.get("active", False):
            frac = float(agu_cfg.get("agu_fraction_between_slider_and_payload", 0.45))
            target = payload_attach if payload_attach is not None else center + 1000.0 * self.d_down
            agu_cg = center + frac * (target - center)
            nodes["AGU_CG"] = Node("AGU_CG", "agu_cg", agu_cg[0], agu_cg[1], agu_cg[2], "AGU", "", "", 0, "agu", "agu:cg", "", "AGU_CG")
            nodes["AGU_CONTROL"] = Node("AGU_CONTROL", "agu_control", agu_cg[0], agu_cg[1], agu_cg[2], "AGU", "", "", 0, "brake", "agu:control", "", "AGU_CONTROL")
            self._add_oriented_box(nodes, segments, surfaces, "AGU", agu_cg, float(agu_cfg.get("agu_length_mm", 400.0)), float(agu_cfg.get("agu_width_mm", 300.0)), float(agu_cfg.get("agu_height_mm", 300.0)), "AGU")

        # Lower straps are not part of R/b. They are only exposed segments for drag/visualization.
        if payload_cfg.get("active", False) and payload_cfg.get("lower_straps_active", True) and payload_attach_id in nodes:
            lp = self.cfg.get("line_properties", {})
            lower_targets = ["L_front_riser", "R_front_riser", "L_rear_riser", "R_rear_riser"]
            # Brake control connection goes to AGU_CONTROL if present, otherwise payload attach.
            for rid in lower_targets:
                side = "L" if rid.startswith("L_") else "R"
                strap_key = "front" if "front" in rid else "rear"
                segments.append(Segment(f"LOWER_STRAP_{rid}", rid, payload_attach_id, "LOWER", side, 100, float(lp.get("line_diameter_mm", 1.2)), "lower_strap", "lower_strap", f"lower_strap:{strap_key}"))
            if "AGU_CONTROL" not in nodes:
                for rid in ["L_brake_actuator", "R_brake_actuator"]:
                    if rid in nodes:
                        side = "L" if rid.startswith("L_") else "R"
                        segments.append(Segment(f"LOWER_BRAKE_STRAP_{rid}", rid, payload_attach_id, "BRK", side, 100, float(lp.get("line_diameter_mm", 1.2)), "brake_lower_strap", "brake", "lower_brake_strap:brake"))
        return nodes, segments, surfaces, center, area, width, depth

class LineNetwork:
    """Create the complete suspension line graph for a given slider distance."""
    def __init__(self, canopy: CanopyGeometry, cfg: dict, anchors: dict[str, Node], stabs: StabilizerGeometry | None = None):
        self.canopy, self.cfg, self.anchor_nodes, self.stabs = canopy, cfg, dict(anchors), stabs
        self.nodes: dict[str, Node] = dict(anchors)
        self.segments: list[Segment] = []
        self.surfaces: list[dict] = []
        self.parent: dict[str, str] = {}
        self.warnings: list[str] = []
        self._seg_counter = 1
        self._segment_keys = set()
        self.slider_system = SliderPayloadSystem(canopy, cfg)
        if self.slider_system.angle_warning:
            self.warnings.append(self.slider_system.angle_warning)

    def _line_props(self) -> dict:
        return self.cfg.get("line_properties", {})

    def _add_prebuilt_segment(self, seg: Segment):
        if seg.start_node in self.nodes and seg.end_node in self.nodes:
            seg.length_mm = float(np.linalg.norm(self.nodes[seg.end_node].p - self.nodes[seg.start_node].p))
        self.segments.append(seg)

    def add_segment(self, start: str, end: str, bank: str, side: str, level: int, line_type="structural", mirror_key=""):
        if start not in self.nodes or end not in self.nodes:
            self.warnings.append(f"Invalid segment skipped because a node was missing: {start}->{end}.")
            return
        key = tuple(sorted((start, end)))
        if key in self._segment_keys:
            self.warnings.append(f"Duplicated segment skipped: {start}--{end}.")
            return
        self._segment_keys.add(key)
        lp = self._line_props()
        seg = Segment(f"SEG_{self._seg_counter:05d}", start, end, bank, side, level, lp.get("line_diameter_mm", 1.2), lp.get("material", "line"), line_type, mirror_key, lp.get("Cd0_line", 1.2))
        seg.length_mm = float(np.linalg.norm(self.nodes[end].p - self.nodes[start].p))
        if seg.length_mm <= 1e-6:
            self.warnings.append(f"Near-zero segment skipped: {start}->{end}.")
            return
        self._seg_counter += 1
        self.segments.append(seg)
        if line_type in {"structural", "brake"}:
            self.parent[start] = end

    def _target_for(self, bank_name: str, side: str) -> str:
        group = str(_deep_get(self.cfg, ["risers", "mapping", bank_name], "brake" if bank_name == "BRK" else "front")).lower()
        if group == "brake":
            brake_target = str(_deep_get(self.cfg, ["brakes", "brake_target"], "agu_if_active")).lower()
            if brake_target in {"agu", "agu_if_active"} and "AGU_CONTROL" in self.nodes:
                return "AGU_CONTROL"
            return f"{side}_brake_actuator"
        return f"{side}_{'rear' if group == 'rear' else 'front'}_riser"

    def _cascade_levels_for_bank(self, bank: dict) -> list[dict]:
        btype = str(bank.get("type", "structural")).lower()
        key = "brake" if btype == "brake" else "structural"
        levels = self.cfg.get("cascades", {}).get(key, [])
        out = []
        for lvl in levels:
            app = [str(x).upper() for x in lvl.get("bank_applicability", [])]
            if not app or str(bank.get("name", "")).upper() in app:
                out.append(lvl)
        return out

    def build_for_distance(self, distance_mm: float):
        self.nodes = dict(self.anchor_nodes)
        self.segments, self.surfaces, self.parent = [], [], {}
        self._segment_keys, self._seg_counter = set(), 1
        if self.stabs is not None and self.stabs.active():
            for n in self.stabs.nodes.values():
                self.nodes[n.node_id] = n
            self.surfaces.extend(self.stabs.surfaces)
            for s in self.stabs.segments:
                self._add_prebuilt_segment(s)
        extra_nodes, extra_segments, extra_surfaces, _, _, _, _ = self.slider_system.create_for_slider_distance(distance_mm)
        for n in extra_nodes.values():
            self.nodes[n.node_id] = n
        self.surfaces.extend(extra_surfaces)
        for s in extra_segments:
            self._add_prebuilt_segment(s)
        banks = [b for b in self.cfg.get("banks", []) if b.get("enabled", True)]
        if not _deep_get(self.cfg, ["brakes", "active"], True):
            banks = [b for b in banks if str(b.get("type", "structural")).lower() != "brake"]
        for bank in banks:
            bank_name = str(bank.get("name", "")).upper()
            line_type = str(bank.get("type", "structural")).lower()
            for side in ["L", "R"]:
                current = [n.node_id for n in self.nodes.values() if n.node_type == "anchor" and n.bank == bank_name and n.side == side]
                # Mirror-safe grouping: both sides are ordered centre-to-tip by abs(Y), not by raw Y.
                current.sort(key=lambda nid: (abs(self.nodes[nid].y), self.nodes[nid].pair_index, self.nodes[nid].node_id))
                if not current:
                    continue
                target = self._target_for(bank_name, side)
                if target not in self.nodes:
                    self.warnings.append(f"Missing target {target} for bank {bank_name} side {side}.")
                    continue
                for lvl in self._cascade_levels_for_bank(bank):
                    gs = max(1, int(lvl.get("group_size", 2)))
                    fraction = float(lvl.get("fraction_to_target", 0.5))
                    level = int(lvl.get("level", 1))
                    new_current = []
                    for gi in range(0, len(current), gs):
                        group = current[gi:gi+gs]
                        if len(group) < gs:
                            # A final singleton is not an invalid cascade group. It is carried
                            # forward to the next level/target, keeping the network valid and
                            # avoiding artificial nodes. This is common when a side has an odd
                            # number of selected loaded ribs.
                            new_current.extend(group)
                            continue
                        centroid = np.array([self.nodes[c].p for c in group]).mean(axis=0)
                        target_p = self.nodes[target].p
                        p = centroid + fraction * (target_p - centroid)
                        group_index = gi // gs + 1
                        node_id = f"{bank_name}_cas_L{level}_{side}_{group_index:02d}"
                        pair_index = f"{level}:{group_index}"
                        mirror_key = f"cascade:{bank_name}:L{level}:G{group_index:02d}"
                        self.nodes[node_id] = Node(node_id, "cascade", p[0], p[1], p[2], bank_name, side, "", level, line_type, mirror_key, pair_index, node_id)
                        for child in group:
                            self.add_segment(child, node_id, bank_name, side, level, line_type, f"seg:{bank_name}:L{level}:G{group_index:02d}:child{self.nodes[child].pair_index}")
                        new_current.append(node_id)
                    current = new_current
                for child in current:
                    self.add_segment(child, target, bank_name, side, 99, line_type, f"seg:{bank_name}:to_target:{self.nodes[child].pair_index}")
        self._compute_segment_drag()
        return self

    def _anchor_nodes_for_R(self) -> list[Node]:
        """Structural canopy anchors used for the crown-rigging R/b constraint.

        In the crown-rigging interpretation requested for v16, R is not the sum of
        cascade segment lengths.  It is the straight geometric distance from each
        structural canopy attachment point to a central confluence reference point
        on the canopy symmetry plane.  Cascades remain geometry/drag/tension
        segments, but they do not redefine R.
        """
        return [n for n in self.nodes.values() if n.node_type == "anchor" and n.line_type == "structural"]

    def _mean_straight_R_to_confluence(self) -> tuple[float, list[float]]:
        if "CONFLUENCE_CENTER" not in self.nodes:
            return 0.0, []
        c = self.nodes["CONFLUENCE_CENTER"].p
        lengths = [float(np.linalg.norm(n.p - c)) for n in self._anchor_nodes_for_R()]
        return (float(np.mean(lengths)) if lengths else 0.0, lengths)

    def _mean_path_length(self, include_brake=False) -> tuple[float, list[float]]:
        lengths = []
        for n in self.nodes.values():
            if n.node_type != "anchor":
                continue
            if not include_brake and n.line_type == "brake":
                continue
            total = 0.0
            current = n.node_id
            seen = set()
            while current in self.parent and current not in seen:
                seen.add(current)
                nxt = self.parent[current]
                total += float(np.linalg.norm(self.nodes[nxt].p - self.nodes[current].p))
                current = nxt
            if total > 0.0:
                lengths.append(total)
        return (float(np.mean(lengths)) if lengths else 0.0, lengths)

    def solve_geometry_for_R_and_angles(self) -> float:
        """Solve slider/confluence distance for the requested crown-rigging R/b.

        v15 solved R/b using the mean summed path length through cascades.  For the
        crown-rigged layout used here, the controlling geometric condition is instead
        the straight distance from canopy attachment points to the central confluence
        reference on the symmetry axis.  The solver therefore moves the slider /
        confluence / payload-line system along theta until:

            mean(|anchor_i - CONFLUENCE_CENTER|) = R_over_b * b_effective

        The cascade node locations are then regenerated from their configured
        fractions toward their riser targets.  The resulting physical segment lengths
        are still exported for drag, tension and vibration analyses.
        """
        target = float(_deep_get(self.cfg, ["constraints", "R_over_b"], 0.8)) * self.canopy.span_effective_mm
        initial = _deep_get(self.cfg, ["risers", "initial_slider_distance_mm"], None)
        if initial is None:
            initial = target
        if not _deep_get(self.cfg, ["constraints", "auto_solve_R"], True):
            self.build_for_distance(float(initial))
            return float(initial)

        def mean_for(h):
            self.build_for_distance(h)
            return self._mean_straight_R_to_confluence()[0]

        lo, hi = 0.0, max(3.0 * target, 1000.0)
        mlo = mean_for(lo)
        if mlo > target:
            self.warnings.append(f"Crown R/b target not reachable: minimum straight anchor-to-confluence mean {mlo:.2f} mm > target {target:.2f} mm.")
            self.build_for_distance(lo)
            return lo
        mhi = mean_for(hi)
        while mhi < target and hi < max(20.0 * target, 100000.0):
            hi *= 1.8
            mhi = mean_for(hi)
        for _ in range(80):
            mid = 0.5 * (lo + hi)
            if mean_for(mid) < target:
                lo = mid
            else:
                hi = mid
        h = 0.5 * (lo + hi)
        self.build_for_distance(h)
        return h

    def compute_unique_exposed_length(self) -> float:
        # Drag uses unique exposed physical segments. Shared cascade segments are counted once.
        # R/b uses only structural canopy-to-slider paths; drag can include brake and lower straps.
        include_lower = bool(_deep_get(self.cfg, ["line_properties", "include_lower_straps_in_drag"], True))
        include_brake = bool(_deep_get(self.cfg, ["line_properties", "include_brake_lines_in_drag"], True))
        unique = {}
        for s in self.segments:
            if s.line_type in {"slider", "payload", "agu", "stabilizer", "tip_bulge"}:
                continue
            if (not include_lower) and s.line_type in {"lower_strap", "brake_lower_strap"}:
                continue
            if (not include_brake) and (s.bank == "BRK" or s.line_type == "brake"):
                continue
            unique[tuple(sorted((s.start_node, s.end_node)))] = s.length_mm
        return float(sum(unique.values()))

    def compute_sum_of_all_path_lengths(self) -> float:
        return float(sum(self._mean_path_length(include_brake=True)[1]))

    def _velocity_unit(self) -> np.ndarray:
        # Incident velocity used only for the segmented line-drag estimate.
        # alpha_op is the recommended reference because it is the angle between the
        # canopy chord and relative wind in the central rib plane. gamma can still be
        # selected for glide-path based sensitivity checks.
        ref = str(_deep_get(self.cfg, ["line_properties", "velocity_reference"], "alpha_op")).lower()
        if ref == "custom":
            ang = float(_deep_get(self.cfg, ["line_properties", "incident_velocity_angle_deg"], 0.0))
        elif ref == "gamma":
            ang = float(_deep_get(self.cfg, ["angles", "gamma_deg"], 0.0))
        else:
            ang = float(_deep_get(self.cfg, ["angles", "alpha_op_deg"], 0.0))
        a = math.radians(ang)
        return np.array([math.cos(a), 0.0, -math.sin(a)], dtype=float)

    def _compute_segment_drag(self):
        """Assign per-segment flow angle, drag/lift coefficients and vibration placeholders.

        The uploaded reference page states that the drag of a suspension-line
        segment depends on the angle theta between that segment and the oncoming
        stream, with Cd = Cd0*sin^3(theta) and Cl = Cd0*sin^2(theta)*cos(theta).
        Here theta is computed from the actual 3D CAD segment vector and a 2D
        incident-velocity vector in the central X-Z plane.  The segment table then
        carries the information needed for later drag, tension and vibration work.
        """
        S_m2 = max(self.canopy.planform_area_mm2 / 1.0e6, 1e-9)
        V = self._velocity_unit()
        lp = self._line_props()
        line_density = float(lp.get("line_density_kg_m", 0.0))
        E = float(lp.get("elastic_modulus_pa", 0.0))
        nu = float(lp.get("poisson_ratio", 0.0))
        T = float(lp.get("nominal_segment_tension_N", 0.0))
        Vref = float(lp.get("reference_velocity_m_s", 0.0))
        St = float(lp.get("strouhal_number", 0.22))
        for s in self.segments:
            if s.start_node not in self.nodes or s.end_node not in self.nodes:
                continue
            v = self.nodes[s.end_node].p - self.nodes[s.start_node].p
            n = np.linalg.norm(v)
            if n < 1e-12:
                continue
            u = v / n
            s.segment_unit_x, s.segment_unit_y, s.segment_unit_z = float(u[0]), float(u[1]), float(u[2])
            # theta is conventionally the smaller angle between a cylinder axis and flow.
            # theta=0: line aligned with flow; theta=90: line perpendicular to flow.
            theta = math.acos(max(-1.0, min(1.0, abs(float(np.dot(u, V))))))
            stheta = math.sin(theta)
            ctheta = math.cos(theta)
            s.theta_to_velocity_deg = math.degrees(theta)
            s.Cd_local = float(s.Cd0 * (stheta ** 3))
            s.Cl_local = float(s.Cd0 * (stheta ** 2) * ctheta)
            s.CD_contribution = float(s.Cd_local * (s.diameter_mm / 1000.0) * (s.length_mm / 1000.0) / S_m2)
            s.line_density_kg_m = line_density
            s.elastic_modulus_pa = E
            s.poisson_ratio = nu
            s.nominal_tension_N = T
            if line_density > 0 and T > 0 and s.length_mm > 0:
                Lm = s.length_mm / 1000.0
                s.estimated_fn1_hz = float((1.0 / (2.0 * Lm)) * math.sqrt(T / line_density))
            if Vref > 0 and s.diameter_mm > 0:
                s.estimated_shedding_frequency_hz = float(St * Vref / (s.diameter_mm / 1000.0))

    def CD_lines_simple(self) -> float:
        d_m = float(_deep_get(self.cfg, ["line_properties", "line_diameter_mm"], 1.2)) / 1000.0
        return d_m * (self.compute_unique_exposed_length() / 1000.0) / max(self.canopy.planform_area_mm2 / 1.0e6, 1e-9)

    def CD_lines_segmented(self) -> float:
        return float(sum(s.CD_contribution for s in self.segments if s.line_type not in {"slider", "payload", "agu", "stabilizer", "tip_bulge"}))

    def max_bifurcation_angle_deg(self) -> float:
        child_map: dict[str, list[str]] = {}
        for s in self.segments:
            if s.line_type not in {"structural", "brake"}:
                continue
            child_map.setdefault(s.end_node, []).append(s.start_node)
        max_ang = 0.0
        for node_id, children in child_map.items():
            if len(children) < 2:
                continue
            origin = self.nodes[node_id].p
            unit_vecs = []
            for child in children:
                v = self.nodes[child].p - origin
                n = np.linalg.norm(v)
                if n > 1e-12:
                    unit_vecs.append(v / n)
            for i in range(len(unit_vecs)):
                for j in range(i + 1, len(unit_vecs)):
                    max_ang = max(max_ang, math.degrees(math.acos(float(np.clip(np.dot(unit_vecs[i], unit_vecs[j]), -1.0, 1.0)))))
        return max_ang

    def check_left_right_symmetry(self) -> dict:
        out = {"max_node_mirror_error_mm": 0.0, "max_segment_length_diff_mm": 0.0, "warnings": []}
        node_groups: dict[str, dict[str, Node]] = {}
        for n in self.nodes.values():
            if n.mirror_key and n.side in {"L", "R"}:
                node_groups.setdefault(n.mirror_key, {})[n.side] = n
        for key, pair in node_groups.items():
            if "L" not in pair or "R" not in pair:
                out["warnings"].append(f"Missing mirror node for {key}: sides={list(pair)}")
                continue
            L, R = pair["L"].p, pair["R"].p
            err = float(np.linalg.norm(np.array([L[0] - R[0], L[1] + R[1], L[2] - R[2]])))
            out["max_node_mirror_error_mm"] = max(out["max_node_mirror_error_mm"], err)
        seg_groups: dict[str, dict[str, Segment]] = {}
        for s in self.segments:
            if s.mirror_key and s.side in {"L", "R"}:
                seg_groups.setdefault(s.mirror_key, {})[s.side] = s
        for key, pair in seg_groups.items():
            if "L" not in pair or "R" not in pair:
                out["warnings"].append(f"Missing mirror segment for {key}: sides={list(pair)}")
                continue
            diff = abs(pair["L"].length_mm - pair["R"].length_mm)
            out["max_segment_length_diff_mm"] = max(out["max_segment_length_diff_mm"], diff)
        return out

    def _bank_xc(self, bank_name: str) -> float:
        for b in self.cfg.get("banks", []):
            if str(b.get("name", "")).upper() == str(bank_name).upper():
                return float(b.get("x_c", 0.5))
        return 0.5

    def _trace_final_target(self, start_node: str) -> str:
        current = start_node
        seen = set()
        while current in self.parent and current not in seen:
            seen.add(current)
            current = self.parent[current]
        return current

    def _line_diagnostic_nearest_xc(self) -> dict:
        """Secondary diagnostic: angle of one central suspension line nearest x/c target."""
        target_xc = float(_deep_get(self.cfg, ["angles", "rigging_measure_x_c"], 0.25))
        structural = [b for b in self.cfg.get("banks", []) if b.get("enabled", True) and str(b.get("type", "structural")).lower() == "structural"]
        if not structural:
            return {"central_line_angle_from_vertical_deg": float("nan"), "central_line_bank": "", "central_line_anchor_nodes": "", "central_line_target_nodes": ""}
        bank = min(structural, key=lambda b: abs(float(b.get("x_c", 0.5)) - target_xc))
        bank_name = str(bank.get("name", "")).upper()
        anchors = [n for n in self.nodes.values() if n.node_type == "anchor" and n.bank == bank_name and n.line_type == "structural"]
        if not anchors:
            return {"central_line_angle_from_vertical_deg": float("nan"), "central_line_bank": bank_name, "central_line_anchor_nodes": "", "central_line_target_nodes": ""}
        min_abs_y = min(abs(n.y) for n in anchors)
        selected = [n for n in anchors if abs(abs(n.y) - min_abs_y) < 1.0e-6]
        vectors, targets = [], []
        for n in selected:
            final_id = self._trace_final_target(n.node_id)
            if final_id in self.nodes:
                targets.append(final_id)
                vectors.append(self.nodes[final_id].p - n.p)
        if not vectors:
            return {"central_line_angle_from_vertical_deg": float("nan"), "central_line_bank": bank_name, "central_line_anchor_nodes": ",".join(n.node_id for n in selected), "central_line_target_nodes": ""}
        v = np.array(vectors, dtype=float).mean(axis=0)
        angle = math.degrees(math.atan2(v[0], -v[2])) if abs(v[2]) > 1.0e-12 or abs(v[0]) > 1.0e-12 else float("nan")
        return {
            "central_line_angle_from_vertical_deg": angle,
            "central_line_slope_xz_deg": math.degrees(math.atan2(v[2], v[0])) if abs(v[0]) > 1.0e-12 or abs(v[2]) > 1.0e-12 else float("nan"),
            "central_line_bank": bank_name,
            "central_line_bank_x_c": float(bank.get("x_c", 0.5)),
            "central_line_anchor_nodes": ",".join(n.node_id for n in selected),
            "central_line_target_nodes": ",".join(targets),
        }

    def _theta_measured_from_system(self) -> tuple[float, str]:
        """Measure theta directly from P_ref to payload/AGU/slider system.

        This is the most consistent geometric measurement of the overall trim/rigging
        configuration because it uses the same angular relation as the design input:
            alpha + mu = gamma + theta.
        If a payload attach point exists, it is used.  Otherwise the code uses AGU_CONTROL,
        then the average slider corner, then the average riser point.
        """
        priority = _deep_get(self.cfg, ["angles", "rigging_measure_target_node_priority"], ["PAYLOAD_ATTACH", "AGU_CONTROL", "SLIDER_CENTER"])
        if not isinstance(priority, list):
            priority = ["PAYLOAD_ATTACH", "AGU_CONTROL", "SLIDER_CENTER"]
        target_id = ""
        target_p = None
        for cand in priority:
            cand = str(cand)
            if cand == "SLIDER_CENTER":
                pts = [n.p for n in self.nodes.values() if n.node_type == "slider_corner"]
                if pts:
                    target_id = "SLIDER_CENTER(mean corners)"
                    target_p = np.array(pts).mean(axis=0)
                    break
            elif cand in self.nodes:
                target_id = cand
                target_p = self.nodes[cand].p
                break
        if target_p is None:
            pts = [n.p for n in self.nodes.values() if n.node_type in {"riser", "brake_actuator"}]
            if pts:
                target_id = "RISER_CENTER(mean risers)"
                target_p = np.array(pts).mean(axis=0)
        if target_p is None:
            return float("nan"), "no valid payload/slider/riser target"
        v = target_p - self.slider_system.P_ref
        theta = math.degrees(math.atan2(v[0], -v[2])) if abs(v[2]) > 1.0e-12 or abs(v[0]) > 1.0e-12 else float("nan")
        return float(theta), target_id

    def rigging_reference_line_metrics(self) -> dict:
        """Primary rigging measurement of the whole assembly plus line diagnostic.

        The primary value is not the slope of a single suspension line. It is the
        effective system rigging angle inferred from the measured payload-line angle:
            mu_system = gamma + theta_measured - alpha.
        This follows the same definition used to position the assembly.  A secondary
        diagnostic still reports the central structural line nearest x/c=0.25 because
        it is useful for checking the line layout but should not be confused with the
        global trim/rigging angle.
        """
        alpha = float(_deep_get(self.cfg, ["angles", "alpha_op_deg"], 0.0))
        gamma = float(_deep_get(self.cfg, ["angles", "gamma_deg"], 0.0))
        theta_meas, target_id = self._theta_measured_from_system()
        mu_system = gamma + theta_meas - alpha if math.isfinite(theta_meas) else float("nan")
        diag = self._line_diagnostic_nearest_xc()
        out = {
            "angle_from_vertical_deg": mu_system,
            "mu_system_measured_deg": mu_system,
            "theta_measured_deg": theta_meas,
            "theta_target_node": target_id,
            "definition": "system rigging from measured payload/slider line: mu = gamma + theta_measured - alpha",
            "bank": diag.get("central_line_bank", ""),
            "bank_x_c": diag.get("central_line_bank_x_c", ""),
            "anchor_nodes": diag.get("central_line_anchor_nodes", ""),
            "target_nodes": diag.get("central_line_target_nodes", ""),
            "slope_xz_deg": diag.get("central_line_slope_xz_deg", ""),
            "central_line_angle_from_vertical_deg": diag.get("central_line_angle_from_vertical_deg", ""),
        }
        return out

    def rigging_mu_measured_deg(self) -> float:
        return float(self.rigging_reference_line_metrics().get("angle_from_vertical_deg", float("nan")))


class ValidationReport:
    def __init__(self, canopy: CanopyGeometry, cfg: dict, network: LineNetwork, solved_distance_mm: float, stabs: StabilizerGeometry):
        self.canopy, self.cfg, self.network, self.stabs = canopy, cfg, network, stabs
        self.solved_distance_mm = float(solved_distance_mm)
        self.sym = network.check_left_right_symmetry()
        self.warnings = list(dict.fromkeys(network.warnings + stabs.warnings + self.sym.get("warnings", [])))
        self._checks()

    def _checks(self):
        R_target = float(_deep_get(self.cfg, ["constraints", "R_over_b"], 0.8)) * self.canopy.span_effective_mm
        R_mean, lengths = self.network._mean_straight_R_to_confluence()
        if R_target > 0 and abs(R_mean - R_target) / R_target > float(_deep_get(self.cfg, ["constraints", "R_tolerance_fraction"], 0.01)):
            self.warnings.append(f"R/b not satisfied: R_mean={R_mean:.2f} mm, R_target={R_target:.2f} mm.")
        # v15: do not issue detailed rigging-angle measurement warnings here.
        # The geometry is positioned from the prescribed relation theta = alpha + mu - gamma.
        # The report only keeps the input angles and angular closure error to avoid confusing
        # local line slopes with the global rigging/trim convention.
        if lengths and R_mean > 1e-12:
            disp = float(np.std(lengths) / R_mean)
            if disp > float(_deep_get(self.cfg, ["constraints", "path_length_dispersion_warning_fraction"], 0.08)):
                self.warnings.append(f"Path length dispersion high: std/mean={disp:.4f}.")
        max_bif = self.network.max_bifurcation_angle_deg()
        if max_bif > float(_deep_get(self.cfg, ["constraints", "max_bifurcation_angle_deg"], 55.0)):
            self.warnings.append(f"Cascade bifurcation angle high: {max_bif:.2f} deg.")
        for s in self.network.segments:
            if s.bank == "BRK" and s.line_type != "brake":
                self.warnings.append(f"Brake/structural mixing detected in {s.segment_id}.")
            if s.bank != "BRK" and s.line_type == "brake":
                self.warnings.append(f"Brake line type assigned to non-brake bank in {s.segment_id}.")

    def rows(self) -> list[dict]:
        R_target = float(_deep_get(self.cfg, ["constraints", "R_over_b"], 0.8)) * self.canopy.span_effective_mm
        R_struct, struct_lengths = self.network._mean_straight_R_to_confluence()
        R_all, _ = self.network._mean_path_length(include_brake=True)
        L_exp = self.network.compute_unique_exposed_length()
        L_sum = self.network.compute_sum_of_all_path_lengths()
        alpha = float(_deep_get(self.cfg, ["angles", "alpha_op_deg"], 0.0))
        gamma = float(_deep_get(self.cfg, ["angles", "gamma_deg"], 0.0))
        mu = float(_deep_get(self.cfg, ["angles", "mu_deg"], 0.0))
        theta = self.network.slider_system.theta_deg
        r_over_b = float(_deep_get(self.cfg, ["constraints", "R_over_b"], 0.8))
        beta_rad = 1.0 / (4.0 * r_over_b) if r_over_b > 0 else float("nan")
        return [
            {"metric": "S_planform_mm2", "value": self.canopy.planform_area_mm2},
            {"metric": "b_effective_mm", "value": self.canopy.span_effective_mm},
            {"metric": "c_center_mm", "value": self.canopy.chord_center_mm},
            {"metric": "N_cells", "value": self.canopy.params.get("num_cells", "")},
            {"metric": "banks_active", "value": ",".join([str(b.get("name")) for b in self.cfg.get("banks", []) if b.get("enabled", True)])},
            {"metric": "stabilizers_active", "value": self.stabs.active()},
            {"metric": "number_of_anchors", "value": sum(1 for n in self.network.nodes.values() if n.node_type == "anchor")},
            {"metric": "number_of_cascade_nodes", "value": sum(1 for n in self.network.nodes.values() if n.node_type == "cascade")},
            {"metric": "number_of_unique_segments", "value": len({tuple(sorted((s.start_node, s.end_node))) for s in self.network.segments})},
            {"metric": "number_of_brake_segments", "value": sum(1 for s in self.network.segments if s.line_type == "brake")},
            {"metric": "R_target_mm", "value": R_target},
            {"metric": "R_mean_straight_anchor_to_confluence_mm", "value": R_struct},
            {"metric": "R_mean_path_anchor_to_riser_mm", "value": R_all},
            {"metric": "R_error_percent", "value": 100.0 * (R_struct - R_target) / max(R_target, 1e-9)},
            {"metric": "theta_deg", "value": theta},
            {"metric": "alpha_op_deg", "value": alpha},
            {"metric": "gamma_deg", "value": gamma},
            {"metric": "mu_target_deg", "value": mu},
            {"metric": "rigging_definition", "value": "System positioned from input angles using theta = alpha_op + mu - gamma; detailed post-CAD rigging angle diagnostics intentionally omitted in v16."},
            {"metric": "angular_relation_error_deg", "value": (alpha + mu) - (gamma + theta)},
            {"metric": "beta_est_rad_b_over_4R", "value": beta_rad},
            {"metric": "beta_est_deg_b_over_4R", "value": math.degrees(beta_rad) if math.isfinite(beta_rad) else float("nan")},
            {"metric": "slider_distance_solved_mm", "value": self.solved_distance_mm},
            {"metric": "R_path_definition", "value": "crown rigging: mean straight structural canopy-anchor -> CONFLUENCE_CENTER on symmetry axis; cascade path lengths are exported separately for drag/vibration"},
            {"metric": "R_straight_std_over_mean", "value": float(np.std(struct_lengths) / R_struct) if struct_lengths and R_struct > 0 else 0.0},
            {"metric": "slider_area_mm2", "value": self.network.slider_system.last_slider_area},
            {"metric": "slider_width_mm", "value": self.network.slider_system.last_slider_width},
            {"metric": "slider_chord_or_depth_mm", "value": self.network.slider_system.last_slider_depth},
            {"metric": "incident_velocity_reference", "value": _deep_get(self.cfg, ["line_properties", "velocity_reference"], "alpha_op")},
            {"metric": "incident_velocity_angle_deg", "value": (float(_deep_get(self.cfg, ["line_properties", "incident_velocity_angle_deg"], 0.0)) if str(_deep_get(self.cfg, ["line_properties", "velocity_reference"], "alpha_op")).lower() == "custom" else (float(_deep_get(self.cfg, ["angles", "gamma_deg"], 0.0)) if str(_deep_get(self.cfg, ["line_properties", "velocity_reference"], "alpha_op")).lower() == "gamma" else float(_deep_get(self.cfg, ["angles", "alpha_op_deg"], 0.0))))},
            {"metric": "include_lower_straps_in_drag", "value": bool(_deep_get(self.cfg, ["line_properties", "include_lower_straps_in_drag"], True))},
            {"metric": "include_brake_lines_in_drag", "value": bool(_deep_get(self.cfg, ["line_properties", "include_brake_lines_in_drag"], True))},
            {"metric": "slider_active", "value": bool(_deep_get(self.cfg, ["slider", "active"], False))},
            {"metric": "payload_active", "value": bool(_deep_get(self.cfg, ["payload", "active"], False))},
            {"metric": "agu_active", "value": bool(_deep_get(self.cfg, ["agu", "active"], False))},
            {"metric": "L_exposed_unique_mm", "value": L_exp},
            {"metric": "L_sum_paths_mm", "value": L_sum},
            {"metric": "L_sum_paths_over_L_exposed", "value": L_sum / max(L_exp, 1e-9)},
            {"metric": "line_diameter_mm", "value": float(_deep_get(self.cfg, ["line_properties", "line_diameter_mm"], 1.2))},
            {"metric": "CD_lines_simple", "value": self.network.CD_lines_simple()},
            {"metric": "CD_lines_segmented", "value": self.network.CD_lines_segmented()},
            {"metric": "max_node_symmetry_error_mm", "value": self.sym.get("max_node_mirror_error_mm", 0.0)},
            {"metric": "max_segment_symmetry_length_diff_mm", "value": self.sym.get("max_segment_length_diff_mm", 0.0)},
            {"metric": "max_bifurcation_angle_deg", "value": self.network.max_bifurcation_angle_deg()},
            {"metric": "R_straight_dispersion_structural_std_over_mean", "value": float(np.std(struct_lengths) / R_struct) if struct_lengths and R_struct > 0 else 0.0},
            {"metric": "warnings", "value": " | ".join(dict.fromkeys(self.warnings))},
        ]


class SuspensionVisualizer:
    @staticmethod
    def write_png(out_dir: Path, network: LineNetwork, cfg: dict):
        if not _deep_get(cfg, ["outputs", "write_visualizer_png"], True):
            return
        try:
            import matplotlib.pyplot as plt
        except Exception as exc:
            network.warnings.append(f"Matplotlib visualizer skipped: {exc}")
            return
        colors = {"A": "tab:red", "B": "tab:orange", "C": "tab:blue", "D": "tab:green", "BRK": "tab:purple", "SLIDER": "0.4", "LOWER": "0.2", "PAYLOAD": "0.2", "AGU": "0.5", "STAB": "tab:brown"}
        fig = plt.figure(figsize=(14, 10))
        ax3 = fig.add_subplot(2, 2, 1, projection="3d")
        axf = fig.add_subplot(2, 2, 2)
        axs = fig.add_subplot(2, 2, 3)
        axt = fig.add_subplot(2, 2, 4)
        for seg in network.segments:
            if seg.start_node not in network.nodes or seg.end_node not in network.nodes:
                continue
            p1, p2 = network.nodes[seg.start_node].p, network.nodes[seg.end_node].p
            col = colors.get(seg.bank, "k")
            ax3.plot([p1[0], p2[0]], [p1[1], p2[1]], [p1[2], p2[2]], color=col, linewidth=0.8)
            axf.plot([p1[1], p2[1]], [p1[2], p2[2]], color=col, linewidth=0.7)
            axs.plot([p1[0], p2[0]], [p1[2], p2[2]], color=col, linewidth=0.7)
            axt.plot([p1[0], p2[0]], [p1[1], p2[1]], color=col, linewidth=0.7)
        anchors = np.array([n.p for n in network.nodes.values() if n.node_type == "anchor"])
        if anchors.size:
            ax3.scatter(anchors[:, 0], anchors[:, 1], anchors[:, 2], s=10, c="k")
            axf.scatter(anchors[:, 1], anchors[:, 2], s=8, c="k")
            axs.scatter(anchors[:, 0], anchors[:, 2], s=8, c="k")
            axt.scatter(anchors[:, 0], anchors[:, 1], s=8, c="k")
        pts = np.array([n.p for n in network.nodes.values()]) if network.nodes else np.zeros((1, 3))
        P = network.slider_system.P_ref
        scale = 0.22 * max(np.ptp(pts[:, 0]), np.ptp(pts[:, 2]), 1.0)
        d = network.slider_system.d_down
        gamma = math.radians(float(_deep_get(cfg, ["angles", "gamma_deg"], 0.0)))
        axs.arrow(P[0], P[2], scale, 0.0, head_width=0.03*scale, color="k", length_includes_head=True)
        axs.text(P[0] + scale, P[2], "chord x_b")
        axs.arrow(P[0], P[2], d[0]*scale, d[2]*scale, head_width=0.03*scale, color="tab:red", length_includes_head=True)
        axs.text(P[0] + d[0]*scale, P[2] + d[2]*scale, "payload line θ")
        axs.arrow(P[0], P[2], math.cos(gamma)*scale, -math.sin(gamma)*scale, head_width=0.03*scale, color="tab:blue", length_includes_head=True)
        axs.text(P[0] + math.cos(gamma)*scale, P[2] - math.sin(gamma)*scale, "V / γ")
        ax3.set_title("3D suspension network"); ax3.set_xlabel("X [mm]"); ax3.set_ylabel("Y [mm]"); ax3.set_zlabel("Z [mm]")
        axf.set_title("Front Y-Z symmetry"); axf.set_xlabel("Y [mm]"); axf.set_ylabel("Z [mm]"); axf.axis("equal")
        axs.set_title("Side X-Z angles"); axs.set_xlabel("X [mm]"); axs.set_ylabel("Z [mm]"); axs.axis("equal")
        axt.set_title("Top X-Y distribution"); axt.set_xlabel("X [mm]"); axt.set_ylabel("Y [mm]"); axt.axis("equal")
        fig.tight_layout()
        fig.savefig(Path(out_dir) / _deep_get(cfg, ["outputs", "visualizer_png"], "ramair_suspension_network_preview.png"), dpi=200)
        plt.close(fig)


def write_suspension_outputs(out_dir: Path, network: LineNetwork, report: ValidationReport, cfg: dict) -> None:
    import json
    out_dir = Path(out_dir)
    _nodes_to_df(network.nodes).to_csv(out_dir / "ramair_suspension_nodes.csv", index=False, float_format="%.9f")
    seg_df = _segments_to_df(network.segments)
    seg_df.to_csv(out_dir / "ramair_suspension_segments.csv", index=False, float_format="%.9f")
    # Analysis-oriented table: one row per physical segment with length, diameter,
    # angle to incident flow and preliminary drag/vibration quantities.
    seg_df.to_csv(out_dir / "ramair_suspension_segment_analysis.csv", index=False, float_format="%.9f")
    analysis_lines = [
        "Ram-air suspension segment analysis",
        "===================================",
        "theta_to_velocity_deg is the CAD angle between each segment axis and the incident velocity vector.",
        "Cd_local = Cd0*sin(theta)^3; Cl_local = Cd0*sin(theta)^2*cos(theta).",
        "estimated_fn1_hz uses fn = 1/(2L)*sqrt(T/m) with the nominal tension and line density from JSON.",
        "estimated_shedding_frequency_hz uses fs = St*V/d.",
        "",
        f"Number of segment rows: {len(seg_df)}",
        f"CSV file: ramair_suspension_segment_analysis.csv",
    ]
    (out_dir / "ramair_suspension_segment_analysis.txt").write_text("\n".join(analysis_lines), encoding="utf-8")
    _surfaces_to_df(network.surfaces).to_csv(out_dir / "ramair_suspension_surfaces.csv", index=False)
    rep = pd.DataFrame(report.rows())
    rep.to_csv(out_dir / "ramair_suspension_validation_report.csv", index=False)
    (out_dir / "ramair_suspension_validation_report.txt").write_text("\n".join(["Ram-air suspension-line validation report", "========================================", ""] + [f"{r['metric']}: {r['value']}" for r in report.rows()]), encoding="utf-8")
    if _deep_get(cfg, ["outputs", "write_json_used"], True):
        (out_dir / "ramair_suspension_config_used.json").write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    append_global_params(out_dir, [
        ["enable_suspension_lines", 1, "0/1", "Generate suspension-line points and segments in CATIA."],
        ["suspension_nodes_file", "ramair_suspension_nodes.csv", "file", "Suspension-line node table."],
        ["suspension_segments_file", "ramair_suspension_segments.csv", "file", "Suspension-line segment table."],
        ["suspension_segment_analysis_file", "ramair_suspension_segment_analysis.csv", "file", "Per-segment length/diameter/theta/Cd/Cl/vibration-precheck table."],
        ["suspension_surfaces_file", "ramair_suspension_surfaces.csv", "file", "Slider/payload/AGU surface table."],
        ["suspension_validation_file", "ramair_suspension_validation_report.csv", "file", "Suspension-line validation report."],
        ["line_visualization_mode", _deep_get(cfg, ["line_properties", "line_visualization_mode"], "curve"), "text", "curve, tube or cylinder_surface."],
        ["enable_suspension_tube_geometry", int(str(_deep_get(cfg, ["line_properties", "line_visualization_mode"], "curve")).lower() in {"tube", "cylinder", "cylinder_surface"}), "0/1", "Request CATIA cylindrical surface creation for line segments."],
        ["suspension_line_diameter_mm", _deep_get(cfg, ["line_properties", "line_diameter_mm"], 1.2), "mm", "Diameter stored for each suspension segment."],
    ])



def write_fabric_property_outputs(out_dir: Path, cfg: Config, system_cfg: dict | None = None) -> None:
    """Export mesh/FEM-ready shell-thickness properties instead of forcing fragile CATIA offsets.

    For FEM, the recommended use is to import the canopy mid-surface and assign this
    thickness as shell/cloth property. For CFD, use the mid-surface unless the solver
    explicitly requires solid fabric volume; if so, create thickness later in the mesher.
    """
    out_dir = Path(out_dir)
    fab = (system_cfg or {}).get("fabric_thickness", {}) if isinstance(system_cfg, dict) else {}
    enabled = bool(fab.get("properties_enabled", cfg.enable_fabric_thickness_properties))
    t_mm = float(fab.get("thickness_mm", cfg.fabric_thickness_mm))
    rho = float(fab.get("density_kg_m3", cfg.fabric_density_kg_m3))
    material = str(fab.get("material", cfg.fabric_material))
    strategy = str(fab.get("strategy", cfg.fabric_thickness_strategy))
    rows = [
        {"component": "upper_skin", "property_type": "shell", "thickness_mm": t_mm, "density_kg_m3": rho, "material": material, "enabled": enabled, "strategy": strategy},
        {"component": "lower_skin", "property_type": "shell", "thickness_mm": t_mm, "density_kg_m3": rho, "material": material, "enabled": enabled, "strategy": strategy},
        {"component": "ribs", "property_type": "shell", "thickness_mm": t_mm, "density_kg_m3": rho, "material": material, "enabled": enabled, "strategy": strategy},
        {"component": "stabilizers", "property_type": "shell", "thickness_mm": t_mm, "density_kg_m3": rho, "material": material, "enabled": enabled, "strategy": strategy},
        {"component": "tip_side_bulge", "property_type": "shell", "thickness_mm": t_mm, "density_kg_m3": rho, "material": material, "enabled": enabled, "strategy": strategy},
    ]
    pd.DataFrame(rows).to_csv(out_dir / "ramair_fabric_shell_properties.csv", index=False)
    txt = [
        "Ram-air fabric thickness / shell-property recommendation",
        "========================================================",
        f"Recommended strategy: {strategy}",
        f"Thickness: {t_mm:.6f} mm",
        f"Density: {rho:.6f} kg/m3",
        f"Material label: {material}",
        "",
        "Recommended workflow:",
        "1) Export the canopy as CAD mid-surfaces.",
        "2) In FEM, assign the fabric thickness as shell/membrane property rather than thickening CAD.",
        "3) In CFD, keep the thin-surface geometry unless wall-thickness effects are explicitly needed.",
        "4) If a solid fabric thickness is required, create it after export in the mesher/CAD cleanup tool and verify watertightness.",
    ]
    (out_dir / "ramair_fabric_shell_properties.txt").write_text("\n".join(txt), encoding="utf-8")
    append_global_params(out_dir, [
        ["fabric_shell_properties_file", "Canopy/ramair_fabric_shell_properties.csv", "file", "FEM/CFD shell property table for fabric thickness."],
        ["fabric_shell_properties_notes", "Canopy/ramair_fabric_shell_properties.txt", "file", "Instructions for applying fabric thickness after export."],
    ])

def write_suspension_disabled_params(out_dir: Path) -> None:
    append_global_params(out_dir, [["enable_suspension_lines", 0, "0/1", "Suspension-line generation disabled in Python."]])


def _generate_stabilizers_after_canopy(cfg: Config, system_cfg: dict) -> StabilizerGeometry:
    canopy = CanopyGeometry(cfg.out_dir)
    stabs = StabilizerGeometry(canopy, system_cfg).generate()
    write_stabilizer_outputs(cfg.out_dir, stabs)
    return stabs


def _generate_tip_side_bulge_after_canopy(cfg: Config, system_cfg: dict) -> TipSideBulgeGeometry:
    canopy = CanopyGeometry(cfg.out_dir)
    tip = TipSideBulgeGeometry(canopy, system_cfg).generate()
    write_tip_side_bulge_outputs(cfg.out_dir, tip)
    return tip


def _format_bool(v) -> str:
    return "ON" if bool(v) else "OFF"


def write_full_design_summary(out_dir: Path, cfg: Config, system_cfg: dict, stabs: StabilizerGeometry | None = None, tip: TipSideBulgeGeometry | None = None, network: LineNetwork | None = None, report: ValidationReport | None = None) -> None:
    """Human-readable full summary of canopy, perturbations, suspension and validation."""
    out_dir = Path(out_dir)
    canopy = CanopyGeometry(out_dir)
    b = canopy.span_effective_mm
    b_nom = float(canopy.params.get("span_total_mm", cfg.span_total_mm))
    S = canopy.planform_area_mm2
    c_mac = S / max(b, 1e-9)
    AR = b*b / max(S, 1e-9)
    profile_name = str(cfg.input_csv.name)
    banks = [bb for bb in system_cfg.get("banks", []) if bb.get("enabled", True)]
    bank_txt = ", ".join([f"{bb.get('name')}@x/c={float(bb.get('x_c',0)):.3f}({bb.get('type','structural')})" for bb in banks])
    lines = []
    lines += ["RAM-AIR PARAMETRIC CAD DESIGN SUMMARY", "====================================", ""]
    lines += ["[CANOPY GEOMETRY]"]
    lines += [f"2D profile file: {profile_name}", f"Nominal span b_nominal: {b_nom:.3f} mm", f"Effective span b_effective: {b:.3f} mm", f"Center chord c: {canopy.chord_center_mm:.3f} mm", f"Estimated MAC c_mac=S/b: {c_mac:.3f} mm", f"Estimated planform area S: {S:.3f} mm^2 ({S/1e6:.6f} m^2)", f"Estimated aspect ratio AR=b^2/S: {AR:.4f}", f"Number of cells: {int(canopy.params.get('num_cells', cfg.cells))}", f"Planform chord mode: {canopy.params.get('chord_distribution_mode', cfg.chord_distribution_mode)}", f"Chord anchor mode: {canopy.params.get('chord_anchor_mode', cfg.chord_anchor_mode)}", f"Anhedral angle used by canopy: {float(canopy.params.get('arc_anhedral_deg', cfg.arc_anhedral_deg)):.4f} deg", ""]
    lines += ["[CANOPY PERTURBATIONS]"]
    for key in ["enable_span_shrinkage", "span_shrinkage_fraction", "enable_cell_ballooning", "max_thickness_increase_fraction", "enable_te_rounding", "enable_rib_inc_translation", "loaded_rib_incidence_deg", "nonloaded_rib_incidence_deg", "nonloaded_rib_vertical_offset_mm", "enable_crossports", "crossport_count", "crossport_shape", "crossport_cut_mode"]:
        if key in canopy.params:
            lines.append(f"{key}: {canopy.params[key]}")
    lines += [f"Stabilizers active: {_format_bool(stabs.active() if stabs else False)}", f"Tip side bulge active: {_format_bool(tip.active() if tip else False)}"]
    if system_cfg:
        lines += [
            f"Stabilizer shape / apex_x_c / height_mm: {_deep_get(system_cfg, ['stabilizers','shape'], 'n/a')} / {_deep_get(system_cfg, ['stabilizers','apex_x_c'], 'n/a')} / {_deep_get(system_cfg, ['stabilizers','height_mm'], 'n/a')}",
            f"Stabilizer anchor edge mode: {_deep_get(system_cfg, ['stabilizers','triangular_anchor_edge_mode'], 'n/a')}",
            f"Tip side bulge max lateral / chordwise_points / thickness_layers: {_deep_get(system_cfg, ['tip_side_bulge','max_lateral_bulge_mm'], 'n/a')} / {_deep_get(system_cfg, ['tip_side_bulge','chordwise_points'], 'n/a')} / {_deep_get(system_cfg, ['tip_side_bulge','vertical_layers'], 'n/a')}",
            f"Tip side bulge closure: zero lateral displacement on upper/lower and chordwise start/end boundaries",
        ]
    lines += [""]
    if system_cfg:
        lines += ["[SUSPENSION INPUTS]"]
        lines += [f"Banks active and x/c: {bank_txt}", f"Loaded rib selection: {system_cfg.get('loaded_rib_selection', {})}", f"R/b target: {_deep_get(system_cfg, ['constraints','R_over_b'], 'n/a')}", f"alpha_op_deg: {_deep_get(system_cfg, ['angles','alpha_op_deg'], 'n/a')}", f"gamma_deg: {_deep_get(system_cfg, ['angles','gamma_deg'], 'n/a')}", f"mu_deg: {_deep_get(system_cfg, ['angles','mu_deg'], 'n/a')}", f"theta_deg input/computed: {_deep_get(system_cfg, ['angles','theta_deg'], None)}", ""]
        lines += ["[SLIDER / PAYLOAD / AGU]"]
        lines += [f"Slider active: {_format_bool(_deep_get(system_cfg, ['slider','active'], False))}", f"Slider area ratio: {_deep_get(system_cfg, ['slider','slider_area_ratio'], 'n/a')}", f"Slider aspect ratio: {_deep_get(system_cfg, ['slider','slider_aspect_ratio'], 'n/a')}", f"Payload active: {_format_bool(_deep_get(system_cfg, ['payload','active'], False))}", f"Payload dimensions L/W/H [mm]: {_deep_get(system_cfg, ['payload','payload_length_mm'], 'n/a')} / {_deep_get(system_cfg, ['payload','payload_width_mm'], 'n/a')} / {_deep_get(system_cfg, ['payload','payload_height_mm'], 'n/a')}", f"AGU active: {_format_bool(_deep_get(system_cfg, ['agu','active'], False))}", f"AGU dimensions L/W/H [mm]: {_deep_get(system_cfg, ['agu','agu_length_mm'], 'n/a')} / {_deep_get(system_cfg, ['agu','agu_width_mm'], 'n/a')} / {_deep_get(system_cfg, ['agu','agu_height_mm'], 'n/a')}", ""]
    if network is not None and report is not None:
        report_map = {r['metric']: r['value'] for r in report.rows()}
        lines += ["[SUSPENSION RESULTS]"]
        for key in ["number_of_anchors", "number_of_cascade_nodes", "number_of_unique_segments", "number_of_brake_segments", "R_target_mm", "R_mean_straight_anchor_to_confluence_mm", "R_straight_std_over_mean", "R_error_percent", "L_exposed_unique_mm", "L_sum_paths_mm", "L_sum_paths_over_L_exposed", "line_diameter_mm", "CD_lines_simple", "CD_lines_segmented", "incident_velocity_reference", "incident_velocity_angle_deg", "angular_relation_error_deg", "max_node_symmetry_error_mm", "max_segment_symmetry_length_diff_mm", "max_bifurcation_angle_deg"]:
            if key in report_map:
                lines.append(f"{key}: {report_map[key]}")
        lines += ["", "[WARNINGS]", str(report_map.get("warnings", "")), ""]
    lines += ["[PHYSICAL NOTES]", "R/b is evaluated as the straight crown-rigging distance from canopy structural anchors to CONFLUENCE_CENTER on the symmetry axis. Lower straps from slider/risers to payload and brake/control runs to the AGU are included in exposed length/drag, not in R/b.", "The canopy itself is not globally rotated in CATIA by alpha or gamma; the suspension/payload system is positioned from P_ref using theta = alpha + mu - gamma. The aerodynamic polar should still be evaluated at alpha_op_deg. v17 keeps rigging reporting intentionally compact: it reports the input angles and angular closure error only.", "The tip-side bulge is now a canopy-level auxiliary surface with zero-boundary lateral displacement so it closes geometrically with the upper/lower external panels; it is still only a geometric approximation. Real inflated shape requires FSI or experimental reconstruction."]
    (out_dir / "ramair_design_summary_full.txt").write_text("\n".join(lines), encoding="utf-8")


def _generate_suspension_after_canopy(cfg: Config, system_cfg: dict, stabs: StabilizerGeometry | None = None):
    if not ENABLE_SUSPENSION_LINES or not _deep_get(system_cfg, ["suspension", "enabled"], True):
        write_suspension_disabled_params(cfg.out_dir)
        return None, None
    canopy = CanopyGeometry(cfg.out_dir)
    if stabs is None:
        stabs = StabilizerGeometry(canopy, system_cfg).generate()
    anchors = AnchorGenerator(canopy, system_cfg, stabs)
    anchor_nodes = anchors.generate()
    network = LineNetwork(canopy, system_cfg, anchor_nodes, stabs)
    network.warnings.extend(anchors.warnings)
    network.warnings.extend(stabs.warnings)
    solved = network.solve_geometry_for_R_and_angles()
    report = ValidationReport(canopy, system_cfg, network, solved, stabs)
    SuspensionVisualizer.write_png(cfg.out_dir, network, system_cfg)
    write_suspension_outputs(cfg.out_dir, network, report, system_cfg)
    print(f"Generated suspension-line inputs in: {Path(cfg.out_dir).resolve()}")
    return network, report



# =============================================================================
# OUTPUT ORGANIZATION v14
# =============================================================================
CANOPY_DIR_NAME = "Canopy"
SUSPENSION_DIR_NAME = "Suspension_Lines"
OTHER_MODS_DIR_NAME = "Other_Modifications"
TIP_BULGE_DIR_NAME = "Other_Modifications/Tip_Side_Bulge"
STABILIZER_DIR_NAME = "Canopy/Stabilizers"


def _move_if_exists(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.exists():
        if dst.exists():
            dst.unlink()
        shutil.move(str(src), str(dst))


def _rewrite_global_file_paths(out_dir: Path) -> None:
    """Update ramair_global_inputs.csv so CATIA reads files from the new folders."""
    out_dir = Path(out_dir)
    params = out_dir / "ramair_global_inputs.csv"
    if not params.exists():
        return
    mapping = {
        "export_dxf_path": f"{CANOPY_DIR_NAME}/LS1_0417_ramair_profile_2D_mm.dxf",
        "profile_used_normalized_path": f"{CANOPY_DIR_NAME}/Profile_used/ramair_profile_used_normalized.csv",
        "profile_used_catia_points_path": f"{CANOPY_DIR_NAME}/Profile_used/ramair_profile_used_CATIA_points_mm.csv",
        "profile_used_dxf_path": f"{CANOPY_DIR_NAME}/Profile_used/ramair_profile_used_2D_mm.dxf",
        "fabric_shell_properties_file": f"{CANOPY_DIR_NAME}/ramair_fabric_shell_properties.csv",
        "fabric_shell_properties_notes": f"{CANOPY_DIR_NAME}/ramair_fabric_shell_properties.txt",
        "stabilizer_nodes_file": f"{STABILIZER_DIR_NAME}/ramair_stabilizer_nodes.csv",
        "stabilizer_segments_file": f"{STABILIZER_DIR_NAME}/ramair_stabilizer_segments.csv",
        "stabilizer_surfaces_file": f"{STABILIZER_DIR_NAME}/ramair_stabilizer_surfaces.csv",
        "tip_bulge_nodes_file": f"{TIP_BULGE_DIR_NAME}/ramair_tip_bulge_nodes.csv",
        "tip_bulge_segments_file": f"{TIP_BULGE_DIR_NAME}/ramair_tip_bulge_segments.csv",
        "tip_bulge_sections_file": f"{TIP_BULGE_DIR_NAME}/ramair_tip_bulge_sections.csv",
        "tip_bulge_surfaces_file": f"{TIP_BULGE_DIR_NAME}/ramair_tip_bulge_surfaces.csv",
        "suspension_nodes_file": f"{SUSPENSION_DIR_NAME}/ramair_suspension_nodes.csv",
        "suspension_segments_file": f"{SUSPENSION_DIR_NAME}/ramair_suspension_segments.csv",
        "suspension_segment_analysis_file": f"{SUSPENSION_DIR_NAME}/ramair_suspension_segment_analysis.csv",
        "suspension_surfaces_file": f"{SUSPENSION_DIR_NAME}/ramair_suspension_surfaces.csv",
    }
    rows = []
    with params.open(newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        for row in reader:
            if row and row[0] in mapping and len(row) > 1:
                row[1] = mapping[row[0]].replace("/", "\\")
            rows.append(row)
    with params.open("w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(rows)


def write_chord_distribution_plot(out_dir: Path) -> None:
    """Create a root-level PNG showing local chord and rib station distribution."""
    out_dir = Path(out_dir)
    try:
        import matplotlib.pyplot as plt
        rib_path = out_dir / "ramair_rib_stations.csv"
        cell_path = out_dir / "ramair_cell_distribution.csv"
        if not rib_path.exists():
            return
        ribs = pd.read_csv(rib_path)
        fig = plt.figure(figsize=(9, 5))
        ax = fig.add_subplot(111)
        ax.plot(ribs["Y_flat_mm"], ribs["chord_mm"], marker="o", linewidth=1.2)
        if cell_path.exists():
            cells = pd.read_csv(cell_path)
            for _, r in cells.iterrows():
                ax.axvline(float(r["Y_left_loaded_mm"]), linewidth=0.4, alpha=0.3)
            ax.axvline(float(cells.iloc[-1]["Y_right_loaded_mm"]), linewidth=0.4, alpha=0.3)
        ax.set_xlabel("Y flat span station [mm]")
        ax.set_ylabel("Local chord [mm]")
        ax.set_title("Ram-air canopy chord distribution")
        ax.grid(True, linewidth=0.3)
        fig.tight_layout()
        fig.savefig(out_dir / "ramair_chord_distribution.png", dpi=200)
        plt.close(fig)
    except Exception:
        # Plotting is optional; never interrupt CAD preprocessing.
        return


def _replace_directory_from_generated(src: Path, dst: Path) -> None:
    """Move a freshly generated directory over an old one, tolerating Windows locks."""
    src = Path(src)
    dst = Path(dst)
    if not src.exists():
        return
    if dst.exists():
        try:
            shutil.rmtree(dst)
        except PermissionError:
            backup = dst.with_name(f"{dst.name}_locked_backup_{time.strftime('%Y%m%d_%H%M%S')}")
            try:
                shutil.move(str(dst), str(backup))
            except Exception:
                # Last-resort overlay for OneDrive/Explorer locks. This avoids
                # aborting preprocessing, but leaves any locked stale files in place.
                shutil.copytree(src, dst, dirs_exist_ok=True)
                shutil.rmtree(src, ignore_errors=True)
                return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))


def organize_output_folders(out_dir: Path) -> None:
    """Group generated CATIA inputs without breaking the updated CATScript reader.

    Root folder keeps only high-level control/report/preview files. Geometry tables are
    moved into Canopy, Suspension_Lines and Other_Modifications/Tip_Side_Bulge.
    CATIA exports are stored under Exports/.
    """
    out_dir = Path(out_dir)
    (out_dir / EXPORT_SUBFOLDER_NAME).mkdir(parents=True, exist_ok=True)
    # Exact 2D profile actually used by CATIA, preserved for later 2D profile analyses.
    if (out_dir / "Profile_used").exists():
        dst_prof = out_dir / CANOPY_DIR_NAME / "Profile_used"
        _replace_directory_from_generated(out_dir / "Profile_used", dst_prof)
    for name in ["ramair_fabric_shell_properties.csv", "ramair_fabric_shell_properties.txt"]:
        _move_if_exists(out_dir / name, out_dir / CANOPY_DIR_NAME / name)
    # Canopy core inputs.
    for name in [
        "ramair_profile_points_for_CATIA.csv",
        "LS1_0417_profile_CATIA_points_mm.csv",
        "ramair_cell_distribution.csv",
        "ramair_rib_stations.csv",
        "ramair_cell_midsections.csv",
        "ramair_crossports.csv",
        "LS1_0417_ramair_profile_2D_mm.dxf",
        "Profile_used/ramair_profile_used_normalized.csv",
        "Profile_used/ramair_profile_used_2D_mm.dxf",
        "ramair_fabric_shell_properties.csv",
    ]:
        _move_if_exists(out_dir / name, out_dir / CANOPY_DIR_NAME / name)
    # Stabilizers are canopy geometry, not suspension.
    for name in ["ramair_stabilizer_nodes.csv", "ramair_stabilizer_segments.csv", "ramair_stabilizer_surfaces.csv"]:
        _move_if_exists(out_dir / name, out_dir / STABILIZER_DIR_NAME / name)
    # Tip-side bulge is an optional modification.
    for name in ["ramair_tip_bulge_nodes.csv", "ramair_tip_bulge_segments.csv", "ramair_tip_bulge_sections.csv", "ramair_tip_bulge_surfaces.csv"]:
        _move_if_exists(out_dir / name, out_dir / TIP_BULGE_DIR_NAME / name)
    # Suspension-line geometry tables only. Reports stay in root.
    for name in ["ramair_suspension_nodes.csv", "ramair_suspension_segments.csv", "ramair_suspension_segment_analysis.csv", "ramair_suspension_segment_analysis.txt", "ramair_suspension_surfaces.csv"]:
        _move_if_exists(out_dir / name, out_dir / SUSPENSION_DIR_NAME / name)
    _rewrite_global_file_paths(out_dir)



def append_json_control_params(out_dir: Path, system_cfg: dict) -> None:
    """Append JSON-controlled options that CATIA reads from ramair_global_inputs.csv.

    The base canopy parameters are written before the JSON is loaded.  This small
    append step makes selected JSON edits effective in CATIA without needing to
    duplicate everything in the Python USER SETTINGS block.  Since the CATScript
    reads parameters into a dictionary, later rows with the same key override earlier
    rows.
    """
    fab = system_cfg.get("fabric_thickness", {}) if isinstance(system_cfg, dict) else {}
    lp = system_cfg.get("line_properties", {}) if isinstance(system_cfg, dict) else {}
    rows = []
    if fab:
        strategy = str(fab.get("strategy", FABRIC_THICKNESS_STRATEGY)).lower()
        catia_offsets = bool(fab.get("catia_offsets_enabled", False)) and strategy == "catia_offset_experimental"
        rows.extend([
            ["fabric_thickness_strategy", strategy, "text", "Recommended: shell_property or post_mesh_extrusion; catia_offset_experimental only for tests."],
            ["fabric_thickness_properties_enabled", int(bool(fab.get("properties_enabled", ENABLE_FABRIC_THICKNESS_PROPERTIES))), "0/1", "Export shell/fabric thickness metadata."],
            ["enable_fabric_thickness", int(catia_offsets), "0/1", "CATIA offset surfaces only if explicitly set to catia_offset_experimental."],
            ["fabric_thickness_mm", float(fab.get("thickness_mm", FABRIC_THICKNESS_MM)), "mm", "Finite fabric/shell thickness property."],
            ["fabric_density_kg_m3", float(fab.get("density_kg_m3", FABRIC_DENSITY_KG_M3)), "kg/m3", "Fabric density property."],
            ["fabric_material", str(fab.get("material", FABRIC_MATERIAL)), "text", "Fabric material label."],
            ["fabric_thickness_mode", str(fab.get("catia_offset_mode", FABRIC_THICKNESS_MODE)), "text", "CATIA offset mode if experimental offsets are enabled."],
        ])
    if lp:
        requested_mode = str(lp.get("line_visualization_mode", "curve")).lower()
        cad_strategy = str(lp.get("cad_strategy", SUSPENSION_LINE_CAD_STRATEGY)).lower()
        enable_tubes = int(requested_mode in {"tube", "cylinder", "cylinder_surface"} and cad_strategy == "catia_tubes_experimental")
        rows.extend([
            ["line_visualization_mode", "tube" if enable_tubes else "curve", "text", "CATIA line display mode. Robust default is curve; tubes require catia_tubes_experimental."],
            ["suspension_line_cad_strategy", cad_strategy, "text", "curve_with_properties, mesh_cylinders_postprocess, or catia_tubes_experimental."],
            ["enable_suspension_tube_geometry", enable_tubes, "0/1", "Request CATIA cylindrical surface creation only in experimental tube mode."],
            ["default_suspension_line_diameter_mm", float(lp.get("line_diameter_mm", DEFAULT_SUSPENSION_LINE_DIAMETER_MM)), "mm", "Fallback line diameter; per-segment values are exported too."],
        ])
    if rows:
        append_global_params(out_dir, rows)

def write_cad_export_manifest(out_dir: Path, cfg: Config) -> None:
    """Write a simple manifest describing which HybridBodies/files represent each export group.

    CATIA can always export the complete CATPart automatically.  Component-level
    exports are best handled by showing/hiding HybridBodies or by post-processing the
    IGES/STEP file.  This manifest makes the grouping explicit for CFD/FEM workflows.
    """
    if not cfg.export_component_manifest:
        return
    rows = [
        {"component": "canopy_only", "catia_hybrid_bodies": "01_Rib_Curves_From_CSV;02_Canopy_Surfaces;05_Fabric_Thickness_Offsets", "recommended_use": "CFD/FEM canopy shell or thickened canopy surfaces", "notes": "Excludes suspension lines, payload, AGU and slider."},
        {"component": "suspension_lines_only", "catia_hybrid_bodies": "04_Suspension_Lines", "recommended_use": "Line drag, cylindrical line surface meshing, vibration pre-processing", "notes": "Uses Suspension_Lines/ramair_suspension_segment_analysis.csv for per-segment metadata."},
        {"component": "canopy_plus_lines", "catia_hybrid_bodies": "01_Rib_Curves_From_CSV;02_Canopy_Surfaces;04_Suspension_Lines;05_Fabric_Thickness_Offsets", "recommended_use": "Combined aerodynamic interference studies", "notes": "Hide payload/AGU/slider bodies if not required."},
        {"component": "full_assembly", "catia_hybrid_bodies": "all visible HybridBodies", "recommended_use": "System-level visualization and complete IGES export", "notes": "This is the automatic CATIA ExportData target."},
        {"component": "other_modifications", "catia_hybrid_bodies": "03B_Canopy_Stabilizers;03C_Tip_Side_Bulge", "recommended_use": "Optional canopy geometry perturbations", "notes": "Can be included or excluded depending on simulation objective."},
    ]
    pd.DataFrame(rows).to_csv(Path(out_dir) / "ramair_cad_export_manifest.csv", index=False)




# =============================================================================

# 2D CAE EXPORTS FOR CFD/FEM PROFILE ANALYSIS
# =============================================================================


def _json_write_2d(path: Path, data: dict) -> None:
    import json
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _cfd2d_root_for_cfg(cfg: Config) -> Path:
    root = Path(CAE_2D_DIR_NAME)
    if root.is_absolute():
        return root
    return Path(ACTIVE_PROJECT_ROOT).resolve() / root


def _cfd2d_reference_root_for_inputs(root: Path) -> Path:
    root = Path(root)
    if root.name == "CFD_2D_inputs":
        return root.parent / "reference_data"
    return root / "reference_data"


def _csv_relpath(path: Path, base: Path) -> str:
    try:
        return os.path.relpath(Path(path).resolve(), Path(base).resolve()).replace("/", "\\")
    except Exception:
        return str(Path(path).resolve())


def _profile_used_source_path(out_dir: Path) -> Path:
    """Return current Profile_used path before/after output-folder organization."""
    out_dir = Path(out_dir)
    candidates = [
        out_dir / "Profile_used" / "ramair_profile_used_normalized.csv",
        out_dir / CANOPY_DIR_NAME / "Profile_used" / "ramair_profile_used_normalized.csv",
    ]
    for p in candidates:
        if p.exists():
            return p
    raise FileNotFoundError("Could not find Profile_used/ramair_profile_used_normalized.csv for 2D CAE export.")


def _read_profile_used_for_2d(out_dir: Path) -> pd.DataFrame:
    path = _profile_used_source_path(out_dir)
    df = pd.read_csv(path)
    if {"x", "y"}.issubset(df.columns):
        out = df[["x", "y"]].copy().rename(columns={"x": "x_norm", "y": "z_norm"})
    elif {"x_norm", "z_norm"}.issubset(df.columns):
        out = df[["x_norm", "z_norm"]].copy()
    elif {"x_chord_norm", "z_chord_norm"}.issubset(df.columns):
        out = df[["x_chord_norm", "z_chord_norm"]].copy().rename(columns={"x_chord_norm": "x_norm", "z_chord_norm": "z_norm"})
    else:
        raise ValueError(f"Profile_used file {path} has no recognized coordinate columns.")
    out = out.astype(float)
    out["source_path"] = str(path)
    return out.reset_index(drop=True)


def _split_profile_used_for_2d(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    clean = df[["x_norm", "z_norm"]].copy().astype(float).reset_index(drop=True)
    le_idx = int(clean["x_norm"].idxmin())
    upper = clean.iloc[: le_idx + 1].copy().reset_index(drop=True)
    lower = clean.iloc[le_idx + 1 :].copy().reset_index(drop=True)
    lower = lower.loc[~lower[["x_norm", "z_norm"]].duplicated()].reset_index(drop=True)
    if len(upper) < 2 or len(lower) < 2:
        raise ValueError("Could not split Profile_used into usable upper/lower branches for 2D CAE export.")
    return upper, lower


def _read_profile_dat_for_2d(path: Path) -> pd.DataFrame:
    """Read a simple DAT/CSV profile file into x_norm,z_norm columns.

    The parser accepts whitespace-separated DAT files with optional title/header
    lines and CSV files with x/y or x/z columns.  It is deliberately permissive so
    digitized Ross references can be dropped in later without touching code.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)
    if path.suffix.lower() == ".csv":
        df = pd.read_csv(path)
        if {"x_norm", "z_norm"}.issubset(df.columns):
            return df[["x_norm", "z_norm"]].astype(float).reset_index(drop=True)
        if {"x", "y"}.issubset(df.columns):
            return df[["x", "y"]].rename(columns={"x": "x_norm", "y": "z_norm"}).astype(float).reset_index(drop=True)
        if {"x", "z"}.issubset(df.columns):
            return df[["x", "z"]].rename(columns={"x": "x_norm", "z": "z_norm"}).astype(float).reset_index(drop=True)
    pts = []
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip().replace(",", " ")
        if not line or line.startswith("#") or line.startswith("//"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        try:
            pts.append((float(parts[0]), float(parts[1])))
        except Exception:
            continue
    if len(pts) < 3:
        raise ValueError(f"Could not read enough profile points from {path}")
    return pd.DataFrame(pts, columns=["x_norm", "z_norm"])


def _edge_length_2d(points: pd.DataFrame, a: int, b: int) -> float:
    pa = points.loc[points.point_id == a, ["x_norm", "z_norm"]].iloc[0].to_numpy(dtype=float)
    pb = points.loc[points.point_id == b, ["x_norm", "z_norm"]].iloc[0].to_numpy(dtype=float)
    return float(np.linalg.norm(pb - pa))


def _append_edge_2d(rows: list[dict], edge_id: int, start: int, end: int, patch_name: str, curve_group: str,
                    edge_type: str, is_wall: bool, is_inlet: bool, is_outlet: bool, is_synthetic: bool,
                    bc_of: str, bc_su2: str, notes: str,
                    is_physical_boundary: bool | None = None,
                    is_opening_between_exterior_and_cavity: bool = False) -> int:
    if is_physical_boundary is None:
        is_physical_boundary = bool(is_wall or is_outlet or (is_inlet and False))
    rows.append({
        "edge_id": edge_id,
        "start_point_id": int(start),
        "end_point_id": int(end),
        "patch_name": patch_name,
        "curve_group": curve_group,
        "edge_type": edge_type,
        "is_wall": bool(is_wall),
        "is_inlet": bool(is_inlet),
        "is_outlet": bool(is_outlet),
        "is_synthetic": bool(is_synthetic),
        "is_physical_boundary": bool(is_physical_boundary),
        "is_farfield": False,
        "is_opening_between_exterior_and_cavity": bool(is_opening_between_exterior_and_cavity),
        "recommended_bc_openfoam": bc_of,
        "recommended_bc_su2": bc_su2,
        "notes": notes,
    })
    return edge_id + 1


def _unit_vec_2d(vec: np.ndarray, fallback: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(vec))
    if n > 1.0e-14:
        return np.asarray(vec, dtype=float) / n
    fb = np.asarray(fallback, dtype=float)
    return fb / max(float(np.linalg.norm(fb)), 1.0e-14)


def _te_tangent_cap_internal_points(
    lower_te: np.ndarray,
    upper_te: np.ndarray,
    lower_prev: np.ndarray,
    upper_next: np.ndarray,
    total_points: int,
) -> np.ndarray:
    """Return internal points for a rounded TE cap with endpoint tangency.

    The cap is a cubic Bezier approximation of a rounded aft closure. Its control
    handles follow the local lower/upper profile tangents instead of forcing a
    perfect circular arc, which avoids the curvature reversal that can damage BL
    prisms near the TE.
    """
    p0 = np.asarray(lower_te, dtype=float)
    p3 = np.asarray(upper_te, dtype=float)
    gap = float(np.linalg.norm(p3 - p0))
    if gap <= 1.0e-12:
        return np.empty((0, 2), dtype=float)
    downstream = np.array([1.0, 0.0], dtype=float)
    d0 = _unit_vec_2d(p0 - np.asarray(lower_prev, dtype=float), downstream)
    d1 = _unit_vec_2d(np.asarray(upper_next, dtype=float) - p3, -downstream)
    if float(d0[0]) < 0.05:
        d0 = _unit_vec_2d(0.65 * d0 + 0.35 * downstream, downstream)
    if float(d1[0]) > -0.05:
        d1 = _unit_vec_2d(0.65 * d1 - 0.35 * downstream, -downstream)
    adj0 = float(np.linalg.norm(p0 - np.asarray(lower_prev, dtype=float)))
    adj1 = float(np.linalg.norm(np.asarray(upper_next, dtype=float) - p3))
    adjacent = np.mean([v for v in [adj0, adj1] if v > 1.0e-14]) if (adj0 > 1.0e-14 or adj1 > 1.0e-14) else gap
    handle = min(max(max(0.55 * gap, 0.70 * float(adjacent)), 0.35 * gap), 1.35 * gap)
    p1 = p0 + handle * d0
    p2 = p3 - handle * d1
    if float(p1[0]) <= float(p0[0]):
        p1 = p0 + handle * downstream
    if float(p2[0]) <= float(p3[0]):
        p2 = p3 + handle * downstream
    ts = np.linspace(0.0, 1.0, max(5, int(total_points)))[1:-1]
    pts = []
    for t in ts:
        omt = 1.0 - float(t)
        pts.append((omt ** 3) * p0 + 3.0 * (omt ** 2) * float(t) * p1 + 3.0 * omt * (float(t) ** 2) * p2 + (float(t) ** 3) * p3)
    return np.asarray(pts, dtype=float)


def _make_te_arc_points(upper: pd.DataFrame, lower: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    """Return synthetic normalized points for an explicit rounded TE contour.

    CATIA and 2D CFD now use a tangent-continuous Bezier-like cap between the
    lower and upper TE points, bulging aft in +x. It is only used for exported
    2D CFD contour metadata; it does not modify CATIA profile points.
    """
    up_te = np.array([float(upper.iloc[0].x_norm), float(upper.iloc[0].z_norm)])
    lo_te = np.array([float(lower.iloc[-1].x_norm), float(lower.iloc[-1].z_norm)])
    up_next = np.array([float(upper.iloc[min(1, len(upper) - 1)].x_norm), float(upper.iloc[min(1, len(upper) - 1)].z_norm)])
    lo_prev = np.array([float(lower.iloc[max(len(lower) - 2, 0)].x_norm), float(lower.iloc[max(len(lower) - 2, 0)].z_norm)])
    diameter = np.linalg.norm(up_te - lo_te)
    if diameter <= 1e-12:
        return pd.DataFrame(columns=["x_norm", "z_norm"])
    num = max(5, int(getattr(cfg, "te_rounding_num_points", 20)))
    pts_arr = _te_tangent_cap_internal_points(lo_te, up_te, lo_prev, up_next, num)
    pts = [(float(p[0]), float(p[1])) for p in pts_arr]
    return pd.DataFrame(pts, columns=["x_norm", "z_norm"])


def _write_profile_used_cfd_contour(out_dir: Path, cfg: Config, upper: pd.DataFrame, lower: pd.DataFrame) -> pd.DataFrame:
    """Write the exact 2D contour intended for future CFD profile studies."""
    prof_dir_candidates = [Path(out_dir) / "Profile_used", Path(out_dir) / CANOPY_DIR_NAME / "Profile_used"]
    prof_dir = next((p for p in prof_dir_candidates if p.exists()), prof_dir_candidates[0])
    prof_dir.mkdir(parents=True, exist_ok=True)
    pieces = [upper[["x_norm", "z_norm"]].copy(), lower[["x_norm", "z_norm"]].copy()]
    arc = pd.DataFrame(columns=["x_norm", "z_norm"])
    if str(cfg.te_closure_mode).lower() == "rounded":
        arc = _make_te_arc_points(upper, lower, cfg)
        if not arc.empty:
            pieces.append(arc)
    contour = pd.concat(pieces, ignore_index=True)
    contour.to_csv(prof_dir / "ramair_profile_used_cfd_contour_normalized.csv", index=False, float_format="%.10f")
    cmm = float(cfg.chord_mm)
    mm_df = contour.copy()
    mm_df["X_mm"] = mm_df["x_norm"] * cmm
    mm_df["Z_mm"] = mm_df["z_norm"] * cmm
    mm_df.to_csv(prof_dir / "ramair_profile_used_cfd_contour_points_mm.csv", index=False, float_format="%.10f")
    pts = [(float(x) * cmm, float(z) * cmm) for x, z in contour[["x_norm", "z_norm"]].to_numpy()]
    # use a single DXF polyline for the contour
    write_dxf_r12(prof_dir / "ramair_profile_used_cfd_contour_2D.dxf", pts[: max(2, len(pts)//2)], pts[max(2, len(pts)//2):])
    try:
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(8, 3.5))
        ax.plot(contour.x_norm, contour.z_norm, marker=".", linewidth=1.0)
        ax.set_aspect("equal", adjustable="datalim"); ax.grid(True, linewidth=0.3)
        ax.set_xlabel("x/c [-]"); ax.set_ylabel("z/c [-]"); ax.set_title(f"Profile CFD contour ({cfg.te_closure_mode})")
        fig.tight_layout(); fig.savefig(prof_dir / "ramair_profile_used_cfd_contour_preview.png", dpi=200); plt.close(fig)
        te_zone = contour[contour["x_norm"] >= float(contour["x_norm"].max()) - 0.08]
        if len(te_zone) >= 3:
            fig, ax = plt.subplots(figsize=(6, 4.5))
            ax.plot(contour.x_norm, contour.z_norm, color="0.75", linewidth=0.8)
            upper_zone = upper[upper["x_norm"] >= float(contour["x_norm"].max()) - 0.08]
            lower_zone = lower[lower["x_norm"] >= float(contour["x_norm"].max()) - 0.08]
            if len(upper_zone):
                ax.plot(upper_zone.x_norm, upper_zone.z_norm, marker=".", linewidth=1.0, color="#1f77b4")
            if len(lower_zone):
                ax.plot(lower_zone.x_norm, lower_zone.z_norm, marker=".", linewidth=1.0, color="#1f77b4")
            if not arc.empty:
                cap_chain = pd.concat([lower.tail(1)[["x_norm", "z_norm"]], arc, upper.head(1)[["x_norm", "z_norm"]]], ignore_index=True)
                ax.plot(cap_chain.x_norm, cap_chain.z_norm, marker=".", linewidth=1.4, color="#d62728")
            ax.set_aspect("equal", adjustable="box"); ax.grid(True, linewidth=0.3)
            ax.set_xlabel("x/c [-]"); ax.set_ylabel("z/c [-]"); ax.set_title("TE rounded closure zoom")
            zpad = max(0.01, 0.20 * float(te_zone.z_norm.max() - te_zone.z_norm.min()))
            ax.set_xlim(float(te_zone.x_norm.min()) - 0.01, float(te_zone.x_norm.max()) + 0.02)
            ax.set_ylim(float(te_zone.z_norm.min()) - zpad, float(te_zone.z_norm.max()) + zpad)
            fig.tight_layout(); fig.savefig(prof_dir / "ramair_profile_used_cfd_contour_te_zoom.png", dpi=220); plt.close(fig)
    except Exception:
        pass
    return contour


def _build_points_edges_from_branches(upper: pd.DataFrame, lower: pd.DataFrame, cfg: Config, variant: str,
                                      closed_le: bool, inlet_feature: bool,
                                      validation_manifest: dict | None = None) -> tuple[pd.DataFrame, pd.DataFrame, dict, dict]:
    chord_m = float(cfg.chord_mm) / 1000.0
    rows = []
    pid = 1
    for i, r in upper.iterrows():
        role = "trailing_edge" if i == 0 else ("inlet_upper_lip" if i == len(upper) - 1 else "upper_wall")
        rows.append({"point_id": pid, "x_norm": float(r.x_norm), "z_norm": float(r.z_norm), "x_m": float(r.x_norm) * chord_m, "z_m": float(r.z_norm) * chord_m, "source_section": "UPPER", "source_order": i + 1, "variant": variant, "boundary_role": role, "notes": "from 2D profile source"})
        pid += 1
    for i, r in lower.iterrows():
        role = "inlet_lower_lip" if i == 0 else ("trailing_edge" if i == len(lower) - 1 else "lower_wall")
        rows.append({"point_id": pid, "x_norm": float(r.x_norm), "z_norm": float(r.z_norm), "x_m": float(r.x_norm) * chord_m, "z_m": float(r.z_norm) * chord_m, "source_section": "LOWER", "source_order": i + 1, "variant": variant, "boundary_role": role, "notes": "from 2D profile source"})
        pid += 1
    points = pd.DataFrame(rows)
    n_upper = len(upper); n_lower = len(lower)
    upper_ids = list(range(1, n_upper + 1)); lower_ids = list(range(n_upper + 1, n_upper + n_lower + 1))
    upper_te, upper_le = upper_ids[0], upper_ids[-1]
    lower_le, lower_te = lower_ids[0], lower_ids[-1]
    edge_rows = []; eid = 1
    upper_patch = "outer_upper_wall" if inlet_feature else "airfoil_upper_wall"
    lower_patch = "outer_lower_wall" if inlet_feature else "airfoil_lower_wall"
    for a, b in zip(upper_ids[:-1], upper_ids[1:]):
        eid = _append_edge_2d(edge_rows, eid, a, b, upper_patch, "upper", "polyline_segment", True, False, False, False, "wall", "MARKER_HEATFLUX", "upper wall")
    if inlet_feature:
        eid = _append_edge_2d(edge_rows, eid, upper_le, lower_le, "inlet_opening_marker", "leading_edge_opening", "straight_segment", False, False, False, False, "metadata_only_not_openfoam_patch", "FEATURE_OPENING", "ram-air opening marker, not a physical OpenFOAM patch", False, True)
    elif closed_le:
        eid = _append_edge_2d(edge_rows, eid, upper_le, lower_le, "leading_edge_closure_wall", "leading_edge_closure", "synthetic_straight_segment", True, False, False, True, "wall", "MARKER_HEATFLUX", "synthetic LE closure")
    for a, b in zip(lower_ids[:-1], lower_ids[1:]):
        eid = _append_edge_2d(edge_rows, eid, a, b, lower_patch, "lower", "polyline_segment", True, False, False, False, "wall", "MARKER_HEATFLUX", "lower wall")
    te_gap = _edge_length_2d(points, lower_te, upper_te)
    if te_gap > 1e-12:
        patch = "trailing_edge_wall" if inlet_feature else "trailing_edge_closure_wall"
        eid = _append_edge_2d(edge_rows, eid, lower_te, upper_te, patch, "trailing_edge_closure", "straight_segment", True, False, False, closed_le, "wall", "MARKER_HEATFLUX", "TE closure")
    edges = pd.DataFrame(edge_rows)
    patches = {}
    if inlet_feature:
        # Open ram-air/Ross cut profile: the mesh module must build the thin-solid fabric
        # and leave this opening connected, not turn it into a solver boundary patch.
        for name in ["outer_upper_wall", "outer_lower_wall", "upper_lip_wall", "lower_lip_wall", "trailing_edge_wall"]:
            patches[name] = {"type": "wall", "recommended_bc_openfoam": "wall", "is_physical_boundary": True}
        patches["inlet_opening_marker"] = {"type": "feature/opening_marker", "is_physical_boundary": False, "is_wall": False, "is_farfield": False, "is_opening_between_exterior_and_cavity": True, "recommended_bc_openfoam": "none_metadata_only"}
    else:
        patches = {"airfoil_wall": {"type": "wall_group", "contains": ["airfoil_upper_wall", "airfoil_lower_wall", "leading_edge_closure_wall", "trailing_edge_closure_wall"]}, "airfoil_upper_wall": {"type": "wall", "recommended_bc_openfoam": "wall", "is_physical_boundary": True}, "airfoil_lower_wall": {"type": "wall", "recommended_bc_openfoam": "wall", "is_physical_boundary": True}, "leading_edge_closure_wall": {"type": "synthetic_wall", "recommended_bc_openfoam": "wall", "is_physical_boundary": True}, "trailing_edge_closure_wall": {"type": "wall", "recommended_bc_openfoam": "wall", "is_physical_boundary": True}}
    manifest = {
        "variant": variant,
        "source": "Profile_used/ramair_profile_used_normalized.csv" if validation_manifest is None else validation_manifest.get("source", "external_profile"),
        "axis_convention": CFD2D_AXIS_CONVENTION,
        "length_unit": CFD2D_LENGTH_UNIT,
        "chord_reference": CFD2D_CHORD_REFERENCE,
        "chord_m": chord_m,
        "te_closure_mode": getattr(cfg, "te_closure_mode", "unknown"),
        "fabric_thickness_chord": CFD2D_FABRIC_THICKNESS_CHORD,
        "model_zero_thickness_as_thin_solid": CFD2D_MODEL_ZERO_THICKNESS_AS_THIN_SOLID,
        "has_ram_air_opening_feature": bool(inlet_feature),
        "ram_air_inlet_is_physical_openfoam_patch": False,
        "forbidden_physical_patch_names": ["ram_air_inlet"],
        "number_of_points": int(len(points)),
        "number_of_edges": int(len(edges)),
    }
    if validation_manifest:
        manifest.update(validation_manifest)
    return points, edges, patches, manifest


def _add_te_arc_to_variant(points: pd.DataFrame, edges: pd.DataFrame, upper: pd.DataFrame, lower: pd.DataFrame, cfg: Config) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Replace a single straight TE closure edge by explicit rounded-TE cap segments.

    This makes the 2D CFD geometry independent of CATIA's visual TE cap.  The mesh
    builder can now read open_ramair/profile_points.csv and see the rounded TE
    directly instead of relying on Profile_used_cfd_contour as a separate file.
    """
    if str(getattr(cfg, "te_closure_mode", "")).lower() != "rounded":
        return points, edges
    arc = _make_te_arc_points(upper, lower, cfg)
    if arc.empty:
        return points, edges
    te_edges = edges[edges["curve_group"].astype(str).str.contains("trailing_edge_closure", na=False)].copy()
    if te_edges.empty:
        return points, edges
    te_edge = te_edges.iloc[-1]
    lower_te = int(te_edge.start_point_id)
    upper_te = int(te_edge.end_point_id)
    chord_m = float(cfg.chord_mm) / 1000.0
    new_points = points.copy()
    next_pid = int(new_points["point_id"].max()) + 1
    arc_ids = []
    for i, r in arc.iterrows():
        pid = next_pid + i
        arc_ids.append(pid)
        new_points.loc[len(new_points)] = {
            "point_id": pid,
            "x_norm": float(r.x_norm),
            "z_norm": float(r.z_norm),
            "x_m": float(r.x_norm) * chord_m,
            "z_m": float(r.z_norm) * chord_m,
            "source_section": "TE_ROUNDED_CAP",
            "source_order": i + 1,
            "variant": str(points["variant"].iloc[0]) if "variant" in points else "open_ramair",
            "boundary_role": "trailing_edge_closure",
            "notes": "explicit tangent-continuous rounded TE cap for 2D CFD/FEM geometry",
        }
    new_edges = edges[edges["edge_id"] != int(te_edge.edge_id)].copy()
    next_eid = int(new_edges["edge_id"].max()) + 1 if not new_edges.empty else 1
    chain = [lower_te] + arc_ids + [upper_te]
    rows = []
    for a, b in zip(chain[:-1], chain[1:]):
        rows.append({
            "edge_id": next_eid,
            "start_point_id": int(a),
            "end_point_id": int(b),
            "patch_name": str(te_edge.patch_name),
            "curve_group": "trailing_edge_closure",
            "edge_type": "rounded_te_cap_segment",
            "is_wall": True,
            "is_inlet": False,
            "is_outlet": False,
            "is_synthetic": False,
            "is_physical_boundary": True,
            "is_farfield": False,
            "is_opening_between_exterior_and_cavity": False,
            "recommended_bc_openfoam": "wall",
            "recommended_bc_su2": "MARKER_HEATFLUX",
            "notes": "explicit tangent-continuous rounded TE cap segment generated from finite TE gap",
        })
        next_eid += 1
    new_edges = pd.concat([new_edges, pd.DataFrame(rows)], ignore_index=True).sort_values("edge_id").reset_index(drop=True)
    return new_points, new_edges


def _build_2d_variant(out_dir: Path, cfg: Config, variant: str) -> tuple[pd.DataFrame, pd.DataFrame, dict, dict]:
    profile = _read_profile_used_for_2d(out_dir)
    upper, lower = _split_profile_used_for_2d(profile)
    points, edges, patches, manifest = _build_points_edges_from_branches(
        upper, lower, cfg, variant,
        closed_le=(variant == "closed_reference"),
        inlet_feature=(variant == "open_ramair"),
    )
    if str(cfg.te_closure_mode).lower() == "rounded":
        points, edges = _add_te_arc_to_variant(points, edges, upper, lower, cfg)
        manifest["explicit_tangent_rounded_te_in_2d_geometry"] = True
        if "trailing_edge_closure_wall" in patches:
            patches["trailing_edge_closure_wall"]["explicit_tangent_rounded_te_cap"] = True
    return points, edges, patches, manifest

def _build_external_profile_variant(path: Path, cfg: Config, variant: str, inlet_feature: bool, ross_meta: dict) -> tuple[pd.DataFrame, pd.DataFrame, dict, dict]:
    df = _read_profile_dat_for_2d(path)
    tmp = df.copy()
    tmp["source_path"] = str(path)
    upper, lower = _split_profile_used_for_2d(tmp)
    return _build_points_edges_from_branches(upper, lower, cfg, variant, closed_le=not inlet_feature, inlet_feature=inlet_feature, validation_manifest=ross_meta)


def _write_dat_2d(path: Path, points: pd.DataFrame, edges: pd.DataFrame, title: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lookup = points.set_index("point_id")
    ordered = []
    for _, e in edges.iterrows():
        if not bool(e.get("is_physical_boundary", True)) and str(e.patch_name) in {"inlet_opening_marker", "ram_air_inlet"}:
            # Keep the two lip endpoints in the DAT by not adding the opening marker edge as a wall segment.
            continue
        if e.start_point_id in lookup.index:
            p = lookup.loc[e.start_point_id]
            ordered.append((float(p.x_norm), float(p.z_norm)))
    if not ordered:
        ordered = list(points[["x_norm", "z_norm"]].itertuples(index=False, name=None))
    lines = [title]
    lines.extend(f"{x:.10f} {z:.10f}" for x, z in ordered)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_dxf_2d_edges(path: Path, points: pd.DataFrame, edges: pd.DataFrame, layer: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lookup = points.set_index("point_id")
    s = "0\nSECTION\n2\nENTITIES\n"
    for _, e in edges.iterrows():
        if e.start_point_id not in lookup.index or e.end_point_id not in lookup.index:
            continue
        p1 = lookup.loc[e.start_point_id]; p2 = lookup.loc[e.end_point_id]
        s += f"0\nLINE\n8\n{str(e.patch_name)[:30]}\n10\n{float(p1.x_norm):.10f}\n20\n{float(p1.z_norm):.10f}\n30\n0\n11\n{float(p2.x_norm):.10f}\n21\n{float(p2.z_norm):.10f}\n31\n0\n"
    s += "0\nENDSEC\n0\nEOF\n"
    path.write_text(s, encoding="ascii")


def _segments_intersect_basic(a, b, c, d, tol=1e-12) -> bool:
    def orient(p, q, r):
        return (q[0]-p[0])*(r[1]-p[1]) - (q[1]-p[1])*(r[0]-p[0])
    def on_seg(p, q, r):
        return min(p[0], r[0])-tol <= q[0] <= max(p[0], r[0])+tol and min(p[1], r[1])-tol <= q[1] <= max(p[1], r[1])+tol
    o1, o2, o3, o4 = orient(a,b,c), orient(a,b,d), orient(c,d,a), orient(c,d,b)
    if o1*o2 < -tol and o3*o4 < -tol:
        return True
    if abs(o1) <= tol and on_seg(a,c,b): return True
    if abs(o2) <= tol and on_seg(a,d,b): return True
    if abs(o3) <= tol and on_seg(c,a,d): return True
    if abs(o4) <= tol and on_seg(c,b,d): return True
    return False


def run_profile_quality_checks(out_dir: Path, cfg: Config, open_points: pd.DataFrame, open_edges: pd.DataFrame, closed_points: pd.DataFrame, closed_edges: pd.DataFrame) -> dict:
    profile = _read_profile_used_for_2d(out_dir)
    upper, lower = _split_profile_used_for_2d(profile)
    chord_m = float(cfg.chord_mm) / 1000.0
    coords = profile[["x_norm", "z_norm"]].to_numpy(dtype=float)
    has_nan = bool(np.isnan(coords).any())
    has_nonfinite = bool(~np.isfinite(coords).all())
    spacing = np.linalg.norm(np.diff(coords, axis=0), axis=1) if len(coords) > 1 else np.array([])
    # thickness sampled on common range
    ux = upper.sort_values("x_norm"); lx = lower.sort_values("x_norm")
    xs = np.linspace(max(ux.x_norm.min(), lx.x_norm.min()), min(ux.x_norm.max(), lx.x_norm.max()), 200)
    uz = np.interp(xs, ux.x_norm, ux.z_norm); lz = np.interp(xs, lx.x_norm, lx.z_norm)
    th = uz - lz
    # basic self-intersection on closed_reference edges
    lookup = closed_points.set_index("point_id")
    segs = []
    for _, e in closed_edges.iterrows():
        if e.start_point_id in lookup.index and e.end_point_id in lookup.index:
            p1 = lookup.loc[e.start_point_id][["x_norm", "z_norm"]].to_numpy(dtype=float)
            p2 = lookup.loc[e.end_point_id][["x_norm", "z_norm"]].to_numpy(dtype=float)
            segs.append((p1, p2))
    self_int = False
    for i in range(len(segs)):
        for j in range(i+1, len(segs)):
            if abs(i-j) <= 1 or (i == 0 and j == len(segs)-1):
                continue
            if _segments_intersect_basic(segs[i][0], segs[i][1], segs[j][0], segs[j][1]):
                self_int = True; break
        if self_int: break
    closed_ok = _edge_length_2d(closed_points, int(closed_edges.iloc[0].start_point_id), int(closed_edges.iloc[-1].end_point_id)) < 1e-6 if not closed_edges.empty else False
    open_inlet = bool(open_edges["patch_name"].isin(["inlet_opening_marker", "ram_air_inlet"]).any())
    fail = []
    if has_nan or has_nonfinite: fail.append("NaN or non-finite coordinate found")
    if self_int: fail.append("basic self-intersection detected")
    if not open_inlet: fail.append("open_ramair inlet marker missing")
    if not closed_ok: fail.append("closed_reference is not closed")
    status = "FAIL" if fail else "PASS"
    le_gap = float(np.linalg.norm(upper.iloc[-1][["x_norm", "z_norm"]].to_numpy(dtype=float) - lower.iloc[0][["x_norm", "z_norm"]].to_numpy(dtype=float)))
    te_gap = float(np.linalg.norm(upper.iloc[0][["x_norm", "z_norm"]].to_numpy(dtype=float) - lower.iloc[-1][["x_norm", "z_norm"]].to_numpy(dtype=float)))
    return {
        "number_of_upper_points": int(len(upper)), "number_of_lower_points": int(len(lower)), "number_of_total_points": int(len(profile)),
        "min_x_norm": float(profile.x_norm.min()), "max_x_norm": float(profile.x_norm.max()), "min_z_norm": float(profile.z_norm.min()), "max_z_norm": float(profile.z_norm.max()),
        "chord_norm": float(profile.x_norm.max() - profile.x_norm.min()), "chord_m": chord_m,
        "max_thickness_norm": float(np.nanmax(th)), "max_thickness_x_norm": float(xs[int(np.nanargmax(th))]), "min_positive_thickness_norm": float(np.nanmin(th[th > 0])) if np.any(th > 0) else 0.0,
        "leading_edge_gap_norm": le_gap, "leading_edge_gap_m": le_gap * chord_m, "trailing_edge_gap_norm": te_gap, "trailing_edge_gap_m": te_gap * chord_m,
        "te_closure_mode": str(cfg.te_closure_mode), "sharp_te_applied": str(cfg.te_closure_mode).lower() == "sharp_extension",
        "duplicate_point_count": int(pd.DataFrame(coords, columns=["x", "z"]).duplicated().sum()), "min_point_spacing_norm": float(np.min(spacing)) if len(spacing) else float("nan"), "min_point_spacing_m": float(np.min(spacing) * chord_m) if len(spacing) else float("nan"),
        "has_nan": has_nan, "has_nonfinite": has_nonfinite, "upper_monotonicity_warning": bool(np.any(np.diff(upper.x_norm.to_numpy(dtype=float)) > 1e-8)), "lower_monotonicity_warning": bool(np.any(np.diff(lower.x_norm.to_numpy(dtype=float)) < -1e-8)),
        "self_intersection_detected": self_int, "closed_reference_is_closed": closed_ok, "open_ramair_has_inlet_patch": open_inlet,
        "fabric_thickness_chord": CFD2D_FABRIC_THICKNESS_CHORD, "model_zero_thickness_as_thin_solid": CFD2D_MODEL_ZERO_THICKNESS_AS_THIN_SOLID,
        "pass_fail": status, "fail_reasons": fail, "warnings": [] if not fail else ["Fix profile before meshing"]
    }


def _plot_profile_2d(points: pd.DataFrame, edges: pd.DataFrame, path: Path, title: str) -> None:
    if not EXPORT_2D_PROFILE_PREVIEWS:
        return
    try:
        import matplotlib.pyplot as plt
        path.parent.mkdir(parents=True, exist_ok=True)
        fig, ax = plt.subplots(figsize=(8, 3.5))
        lookup = points.set_index("point_id")
        labels = set()
        for _, e in edges.iterrows():
            if e.start_point_id not in lookup.index or e.end_point_id not in lookup.index:
                continue
            p1 = lookup.loc[e.start_point_id]; p2 = lookup.loc[e.end_point_id]
            label = str(e.patch_name)
            ax.plot([p1.x_norm, p2.x_norm], [p1.z_norm, p2.z_norm], linewidth=1.8 if e.get("is_opening_between_exterior_and_cavity", False) or e.is_synthetic else 1.0, label=label if label not in labels else None)
            labels.add(label)
        ax.scatter(points.x_norm, points.z_norm, s=8)
        ax.set_aspect("equal", adjustable="datalim"); ax.grid(True, linewidth=0.3)
        ax.set_xlabel("x/c [-]"); ax.set_ylabel("z/c [-]"); ax.set_title(title); ax.legend(fontsize=7)
        fig.tight_layout(); fig.savefig(path, dpi=200); plt.close(fig)
    except Exception:
        return


def _plot_2d_comparison(open_points: pd.DataFrame, open_edges: pd.DataFrame, closed_points: pd.DataFrame, closed_edges: pd.DataFrame, path: Path) -> None:
    if not EXPORT_2D_PROFILE_PREVIEWS:
        return
    try:
        import matplotlib.pyplot as plt
        path.parent.mkdir(parents=True, exist_ok=True)
        fig, ax = plt.subplots(figsize=(8, 3.5))
        for points, edges, label in [(open_points, open_edges, "open_ramair"), (closed_points, closed_edges, "closed_reference")]:
            lookup = points.set_index("point_id"); first = True
            for _, e in edges.iterrows():
                if e.start_point_id in lookup.index and e.end_point_id in lookup.index:
                    p1 = lookup.loc[e.start_point_id]; p2 = lookup.loc[e.end_point_id]
                    ax.plot([p1.x_norm, p2.x_norm], [p1.z_norm, p2.z_norm], linewidth=1.0, label=label if first else None)
                    first = False
        ax.set_aspect("equal", adjustable="datalim"); ax.grid(True, linewidth=0.3); ax.legend(fontsize=8)
        ax.set_xlabel("x/c [-]"); ax.set_ylabel("z/c [-]"); ax.set_title("Open ram-air profile vs closed reference")
        fig.tight_layout(); fig.savefig(path, dpi=200); plt.close(fig)
    except Exception:
        return


def _write_2d_variant_outputs(root: Path, variant: str, points: pd.DataFrame, edges: pd.DataFrame, patches: dict, manifest: dict) -> None:
    out = root / "geometry" / variant
    out.mkdir(parents=True, exist_ok=True)
    points.to_csv(out / "profile_points.csv", index=False, float_format="%.10f")
    edges.to_csv(out / "profile_edges.csv", index=False)
    _json_write_2d(out / "profile_patches.json", patches)
    _json_write_2d(out / "profile_manifest.json", manifest)
    dat_name = f"profile_{variant}.dat"
    dxf_name = f"profile_{variant}.dxf"
    if variant == "open_ramair": dat_name = "profile_open_ramair.dat"; dxf_name = "profile_open_ramair.dxf"
    if variant == "closed_reference": dat_name = "profile_closed_reference.dat"; dxf_name = "profile_closed_reference.dxf"
    _write_dat_2d(out / dat_name, points, edges, variant)
    _write_dxf_2d_edges(out / dxf_name, points, edges, variant.upper())
    _plot_profile_2d(points, edges, out / "profile_preview.png", f"{variant} profile")



def _resolve_optional_profile_path(raw_path: str | Path, cfg: Config | None = None) -> Path:
    """Resolve profile paths robustly for Windows/WSL and scripts/profiles layouts.

    Search order for a relative path:
      1. current working directory,
      2. directory containing this preprocessor script,
      3. output case root,
      4. output case root / profiles,
      5. parent of output case root / profiles.
    The first existing path is returned; if none exists, return the cwd-relative path
    so the warning message is explicit.
    """
    p = Path(raw_path)
    if p.is_absolute():
        return p
    candidates = [
        Path.cwd() / p,
        PROJECT_ROOT / p,
        PROJECT_ROOT / PROFILES_DIR / p.name,
        Path(__file__).resolve().parent / p,
    ]
    if cfg is not None:
        out = Path(cfg.out_dir).resolve()
        candidates.extend([
            out / p,
            out / PROFILES_DIR / p.name,
            out.parent / PROFILES_DIR / p.name,
            out.parent.parent / PROFILES_DIR / p.name,
        ])
    for cand in candidates:
        if cand.exists():
            return cand
    return Path.cwd() / p


def _ross_manifest(case_type: str, inlet_percent: float, source: str) -> dict:
    return {
        "validation_family": "Ross_LS1_0417",
        "ross_case_type": case_type,
        "nominal_inlet_percent_chord": float(inlet_percent),
        "reference_reynolds": 4.0e6,
        "reference_alpha_cp_deg": 4.0,
        "reference_source": "Ross Computational Aerodynamics in the Design and Analysis of Ram-Air-Inflated Wings",
        "source": source,
        "digitized_reference_available": False,
    }


def _write_ross_reference_placeholders(root: Path) -> None:
    ref = _cfd2d_reference_root_for_inputs(root) / "Ross"
    ref.mkdir(parents=True, exist_ok=True)
    _json_write_2d(ref / "ross_reference_manifest.json", {
        "source": "Ross Computational Aerodynamics in the Design and Analysis of Ram-Air-Inflated Wings",
        "airfoil": "NASA LS1-0417",
        "standard_inlet_percent_c": ROSS_STANDARD_INLET_PERCENT_C,
        "minimum_inlet_percent_c": ROSS_MINIMUM_INLET_PERCENT_C,
        "navier_stokes_re": 4.0e6,
        "cp_alpha_deg": 4.0,
        "clean_validation_re": 6.0e6,
        "clean_validation_alpha_pressure_deg": 4.17,
        "digitized": False,
    })
    for name, cols in {
        "ross_figure8_lift_digitized.csv": "alpha_deg,Cl_reference,source_note\n",
        "ross_figure9_ld_digitized.csv": "alpha_deg,L_over_D_reference,source_note\n",
        "ross_figure10_cp_alpha4_digitized.csv": "x_c,Cp_standard_8p4,Cp_minimum_4p0,source_note\n",
    }.items():
        p = ref / name
        if not p.exists(): p.write_text(cols, encoding="utf-8")
    (ref / "README_digitization.md").write_text("# Ross digitization placeholders\n\nCSV files are intentionally empty until the Ross figures are digitized. The workflow must not calibrate results to these placeholders.\n", encoding="utf-8")


def write_2d_cae_exports(cfg: Config) -> None:
    """Export a stable 2D CFD/FEM interface from Profile_used without touching CATIA behavior."""
    if not ENABLE_2D_CAE_EXPORTS:
        append_global_params(cfg.out_dir, [["cfd2d_exports_enabled", 0, "0/1", "2D CFD/FEM exports disabled."]])
        return
    out_dir = Path(cfg.out_dir)
    root = _cfd2d_root_for_cfg(cfg)
    for sub in ["geometry/source", "geometry/open_ramair", "geometry/closed_reference", "config", "validation", "previews"]:
        (root / sub).mkdir(parents=True, exist_ok=True)
    (_cfd2d_reference_root_for_inputs(root) / "Ross").mkdir(parents=True, exist_ok=True)
    source = _read_profile_used_for_2d(out_dir)
    source.to_csv(root / "geometry" / "source" / "profile_used_source.csv", index=False, float_format="%.10f")
    upper, lower = _split_profile_used_for_2d(source)
    _write_profile_used_cfd_contour(out_dir, cfg, upper, lower)
    _json_write_2d(root / "geometry" / "source" / "profile_used_source_info.json", {
        "source_path": str(source.source_path.iloc[0]) if "source_path" in source else "",
        "axis_convention": CFD2D_AXIS_CONVENTION,
        "length_unit": CFD2D_LENGTH_UNIT,
        "chord_m": float(cfg.chord_mm) / 1000.0,
        "te_closure_mode": cfg.te_closure_mode,
        "fabric_thickness_chord": CFD2D_FABRIC_THICKNESS_CHORD,
        "model_zero_thickness_as_thin_solid": CFD2D_MODEL_ZERO_THICKNESS_AS_THIN_SOLID,
    })
    variants_written = []
    open_points = open_edges = closed_points = closed_edges = None
    if EXPORT_2D_OPEN_RAM_AIR_PROFILE:
        open_points, open_edges, open_patches, open_manifest = _build_2d_variant(out_dir, cfg, "open_ramair")
        _write_2d_variant_outputs(root, "open_ramair", open_points, open_edges, open_patches, open_manifest)
        _plot_profile_2d(open_points, open_edges, root / "previews" / "profile_open_ramair_preview.png", "Open ram-air 2D profile")
        variants_written.append("open_ramair")
    if EXPORT_2D_CLOSED_REFERENCE_PROFILE:
        closed_points, closed_edges, closed_patches, closed_manifest = _build_2d_variant(out_dir, cfg, "closed_reference")
        _write_2d_variant_outputs(root, "closed_reference", closed_points, closed_edges, closed_patches, closed_manifest)
        _plot_profile_2d(closed_points, closed_edges, root / "previews" / "profile_closed_reference_preview.png", "Closed reference 2D profile")
        variants_written.append("closed_reference")
    if open_points is not None and closed_points is not None:
        _plot_2d_comparison(open_points, open_edges, closed_points, closed_edges, root / "previews" / "profile_comparison_open_vs_closed.png")

    warnings = []
    # Ross/reference external variants are optional; missing files only create warnings.
    if ENABLE_REFERENCE_UNCUT_PROFILE_EXPORT:
        p = _resolve_optional_profile_path(REFERENCE_UNCUT_PROFILE_PATH, cfg)
        if p.exists():
            meta = _ross_manifest("reference_uncut", 0.0, str(p))
            pts, eds, patches, man = _build_external_profile_variant(p, cfg, REFERENCE_UNCUT_PROFILE_NAME, False, meta)
            _write_2d_variant_outputs(root, REFERENCE_UNCUT_PROFILE_NAME, pts, eds, patches, man)
            _plot_profile_2d(pts, eds, root / "previews" / f"profile_{REFERENCE_UNCUT_PROFILE_NAME}_preview.png", f"{REFERENCE_UNCUT_PROFILE_NAME} 2D profile")
            variants_written.append(REFERENCE_UNCUT_PROFILE_NAME)
        else:
            warnings.append(f"reference_uncut not exported: file not found: {p}")
    if ENABLE_ROSS_VALIDATION_PROFILE_EXPORTS:
        for name, raw_path, case_type, inlet_pct in [
            (ROSS_STANDARD_PROFILE_NAME, ROSS_STANDARD_PROFILE_PATH, "standard_8p4", ROSS_STANDARD_INLET_PERCENT_C),
            (ROSS_MINIMUM_PROFILE_NAME, ROSS_MINIMUM_PROFILE_PATH, "minimum_4p0", ROSS_MINIMUM_INLET_PERCENT_C),
        ]:
            if not str(raw_path).strip():
                warnings.append(f"{name} not exported: no profile path configured; placeholder reference data remains available.")
                continue
            p = _resolve_optional_profile_path(raw_path, cfg)
            if p.exists():
                meta = _ross_manifest(case_type, inlet_pct, str(p))
                pts, eds, patches, man = _build_external_profile_variant(p, cfg, name, True, meta)
                _write_2d_variant_outputs(root, name, pts, eds, patches, man)
                _plot_profile_2d(pts, eds, root / "previews" / f"profile_{name}_preview.png", f"{name} 2D profile")
                variants_written.append(name)
            else:
                warnings.append(f"{name} not exported: file not found: {p}")
    _write_ross_reference_placeholders(root)

    if EXPORT_2D_MESH_CONFIG_TEMPLATE:
        chord_m = float(cfg.chord_mm) / 1000.0
        velocity = CFD2D_DEFAULT_REYNOLDS * 1.81e-5 / (1.225 * chord_m)
        mesh_cfg = {
            "geometry_mode": "thin_solid_fabric",
            "fabric_thickness_chord": CFD2D_FABRIC_THICKNESS_CHORD,
            "target_y_plus": 0.5,
            "first_cell_height_chord_override": 5.0e-5,
            "boundary_layer_layers": 75,
            "boundary_layer_growth": 1.035,
            "boundary_layer_growth_max": 1.20,
            "boundary_layer_total_thickness_chord_override": None,
            "boundary_layer_exclude_te_cap_from_bl": False,
            "gmsh_mesh_algorithm_2d": 5,
            "gmsh_random_factor": 1.0e-7,
            "surface_size_general_chord": 0.0075,
            "surface_size_inlet_lips_chord": 0.00075,
            "surface_size_te_chord": 0.001,
            "surface_size_rounded_te_chord": 0.0012,
            "wake_refinement_length_chord": 4.0,
            "wake_refinement_height_chord": 0.7,
            "wake_size_chord": 0.025,
            "farfield_size_chord": 0.35,
            "cavity_mesh_mode": "coarse_validated",
            "cavity_size_chord": 0.01,
            "mesh_algorithm": "gmsh_delaunay_or_frontal",
            "request_boundary_layer": True,
            "recombine_boundary_layer": True,
            "extrude_to_3d_for_openfoam": True,
            "spanwise_thickness_chord": 0.01,
            "spanwise_layers": 1,
            "debug_simplify_profile": True,
            "debug_max_profile_points": 520,
            "debug_profile_preprocess": True,
            "debug_profile_min_spacing_chord": 0.00012,
            "debug_airfoil_curve_mode": "hybrid_te_spline",
            "debug_airfoil_transfinite": False,
            "debug_airfoil_transfinite_node_multiplier": 1.0,
            "debug_boundary_layer_fan_at_le": False,
            "debug_boundary_layer_fan_at_te": False,
            "debug_boundary_layer_te_fan_points": 64,
            "debug_te_rounding_enabled": True,
            "debug_te_rounding_points": 61,
            "debug_te_rounding_window_chord": 0.055,
            "debug_te_rounding_min_gap_chord": 0.0002,
            "debug_te_refinement_width_chord": 0.075,
            "debug_te_refinement_strength": 6.0,
            "debug_te_refinement_max_weight": 9.0,
            "debug_te_curve_line_window_chord": 0.008,
            "debug_te_cap_spline_segments": 5,
            "debug_te_transfinite_enabled": True,
            "debug_te_transfinite_min_nodes_per_curve": 5,
            "debug_domain_radius_chord": 7.0,
            "nearfield_refinement_enabled": True,
            "nearfield_dist_min_chord": 0.03,
            "nearfield_intermediate_dist_chord": 0.35,
            "nearfield_dist_max_chord": 3.0,
            "nearfield_intermediate_size_chord": 0.035,
            "nearfield_distance_sampling": 360,
            "wake_refinement_enabled": False,
            "open_connected_fluid_surface": False,
            "open_thin_solid_fluid_surface": True,
            "open_single_connected_surface_2d": False,
            "open_minimum_fabric_thickness_chord": 0.0004,
            "open_mesh_internal_cavity": True,
            "open_boundary_layer_split_curvature_sections": True,
            "open_te_transfinite_min_nodes": 28,
            "open_boundary_layer_include_inlet_bridge": True,
            "open_boundary_layer_single_loop_bspline": True,
            "open_boundary_layer_single_loop_curve_kind": "BSpline",
            "open_boundary_layer_single_loop_transfinite": True,
            "open_internal_cavity_curve_mode": "spline",
            "open_te_rounding_enabled": True,
            "open_te_rounding_points": 121,
            "open_te_refinement_width_chord": 0.12,
            "open_surface_size_general_chord": 0.008,
            "open_surface_size_le_chord": 0.003,
            "open_surface_size_lip_chord": 0.002,
            "open_surface_size_te_chord": 0.002,
            "open_farfield_size_chord": 0.4,
            "open_cavity_size_chord": 0.045,
            "open_first_cell_height_chord_override": 0.00002,
            "open_boundary_layer_layers": 30,
            "open_boundary_layer_growth": 1.035,
            "open_boundary_layer_aniso_max_deg": 170.0,
            "open_recombine_boundary_layer": True,
            "open_boundary_layer_total_thickness_chord_override": None,
            "open_diagnostic_boundary_layer_enabled": True,
            "open_boundary_layer_exclude_te_cap_from_bl": False,
            "open_boundary_layer_trim_end_segments": False,
            "open_boundary_layer_trim_ends_chord": 0.0,
            "open_boundary_layer_trim_end_points": 4,
            "open_boundary_layer_fan_at_lips": False,
            "open_boundary_layer_lip_fan_points": 8,
            "open_boundary_layer_include_inlet_marker": True,
            "open_inlet_marker_transfinite_enabled": True,
            "open_inlet_marker_transfinite_nodes": 120,
            "open_nearfield_refinement_enabled": True,
            "open_nearfield_dist_min_chord": 0.025,
            "open_nearfield_intermediate_dist_chord": 0.18,
            "open_nearfield_dist_max_chord": 2.50,
            "open_nearfield_intermediate_size_chord": 0.040,
            "open_nearfield_distance_sampling": 240,
            "open_internal_inlet_refinement_enabled": False,
            "open_internal_inlet_dist_min_chord": 0.02,
            "open_internal_inlet_dist_max_chord": 0.20,
            "open_internal_inlet_size_chord": 0.0015,
            "open_internal_inlet_distance_sampling": 140,
            "open_le_refinement_enabled": False,
            "open_le_refinement_width_chord": 0.18,
            "open_le_refinement_extent_chord": 0.28,
            "open_le_refinement_height_chord": 0.16,
            "open_le_refinement_transition_chord": 0.18,
            "open_lip_refinement_enabled": False,
            "open_lip_refinement_x_chord": 0.045,
            "open_lip_refinement_z_chord": 0.045,
            "open_lip_refinement_transition_chord": 0.08,
            "gmsh_boundary_layer_fallback_no_bl": False,
            "max_remesh_attempts": 4,
            "max_cells": 2000000,
            "max_internal_parse_mesh_size_mb": 80,
            "max_internal_parse_elements": 1000000,
            "min_cells_warning": 1000,
            "stop_if_negative_cells": True,
            "stop_if_geometry_invalid": True,
        }
        _json_write_2d(root / "config" / "cfd2d_mesh_config.json", mesh_cfg)
        _json_write_2d(
            root / "config" / "cfd2d_solver_config.json",
            {
                "config_schema_version": 14,
                "solver": "foamRun",
                "solver_module": "incompressibleFluid",
                "velocity_source": "reynolds",
                "transient": True,
                "time_step_mode": "adaptive_physics_limited",
                "deltaT_star": 0.005,
                "maxDeltaT_star": 0.02,
                "maxCo": 50.0,
                "n_outer_correctors": 15,
                "outer_corrector_residual_control": {
                    "enabled": True,
                    "U_tolerance": 1.0e-4,
                    "nuTilda_tolerance": 1.0e-4,
                    "relative_tolerance": 0.0,
                },
                "transport_correction_final": False,
                "endTime_star": 150,
                "field_write_interval_star": 0.25,
                "field_write_control": "adjustableRunTime",
                "field_write_interval_steps": 200,
                "average_from_fraction": 0.6,
                "temporal_accuracy": {
                    "profile_id": "closed_external_frequency_budget_v1",
                    "target_min_strouhal": 0.05,
                    "target_max_strouhal": 20.0,
                    "target_samples_per_cycle": 20,
                    "minimum_cycles_for_statistics": 10,
                    "smoke_end_time_star": 20.0,
                    "time_step_study_values_star": [0.01, 0.005, 0.0025],
                },
                "purgeWrite": 24,
                "turbulence_model": "SpalartAllmaras",
                "optional_secondary_model": "kOmegaSST",
                "validation_study": {
                    "enabled": False,
                    "study_id": "",
                    "alpha_deg": 8.0,
                    "time_policy": "fixed_staged",
                    "startup_scheme": "Euler",
                    "production_scheme": "backward",
                    "sensitivity_scheme": "CrankNicolson",
                    "crank_nicolson_psi": 0.9,
                    "dt_target_s": None,
                    "startup_factors": [0.25, 0.5, 1.0],
                    "startup_duration_tc": [1.0, 1.0, 2.0],
                    "settling_tc": None,
                    "sampling_tc": None,
                    "nOuterCorrectors": 3,
                    "nCorrectors": 2,
                    "nNonOrthogonalCorrectors": 0,
                    "courant_controls_dt": False,
                    "field_write_interval_tc": 1.0,
                    "retained_snapshots": 24,
                    "mpi_ranks": 8,
                    "timeout_hours": 24.0,
                    "steady_checkpoint_timeout_min": 120.0,
                },
            },
        )
        _json_write_2d(root / "config" / "cfd2d_case_config_template.json", {"variants": variants_written, "default_variant": "open_ramair", "length_unit": CFD2D_LENGTH_UNIT, "axis_convention": CFD2D_AXIS_CONVENTION, "chord_reference": CFD2D_CHORD_REFERENCE, "chord_m": chord_m, "alpha_start_deg": CFD2D_DEFAULT_ALPHA_START_DEG, "alpha_end_deg": CFD2D_DEFAULT_ALPHA_END_DEG, "alpha_step_deg": CFD2D_DEFAULT_ALPHA_STEP_DEG, "reynolds": CFD2D_DEFAULT_REYNOLDS, "mach": CFD2D_DEFAULT_MACH, "mesh_generation": "next_module_ramair_2d_mesh_builder"})
        _json_write_2d(root / "config" / "cfd2d_physical_defaults.json", {"reynolds": CFD2D_DEFAULT_REYNOLDS, "mach": CFD2D_DEFAULT_MACH, "rho_kg_m3": 1.225, "mu_pa_s": 1.81e-5, "pressure_ref_pa": 101325.0, "temperature_K": 288.15, "speed_of_sound_m_s": 340.294, "velocity_source": "reynolds", "velocity_m_s": velocity, "dynamic_pressure_pa": 0.5 * 1.225 * velocity * velocity})
        _json_write_2d(root / "config" / "cfd2d_boundary_condition_template.json", {"open_variants": {"inlet_opening_marker": "feature_opening_not_openfoam_boundary", "walls": "noSlip", "farfield": "freestream", "forbidden_physical_patch": "ram_air_inlet"}, "closed_reference": {"airfoil_wall": "noSlip", "farfield": "freestream"}})
    if EXPORT_2D_PROFILE_QUALITY_REPORT and open_points is not None and closed_points is not None:
        checks = run_profile_quality_checks(out_dir, cfg, open_points, open_edges, closed_points, closed_edges)
        checks["export_warnings"] = warnings
        pd.DataFrame([{"metric": k, "value": str(v)} for k, v in checks.items()]).to_csv(root / "validation" / "profile_quality_report.csv", index=False)
        _json_write_2d(root / "validation" / "profile_geometry_checks.json", checks)
        lines = ["Ram-air 2D profile quality report", "=================================", "", f"STATUS: {checks['pass_fail']}", ""]
        lines.extend([f"{k}: {v}" for k, v in checks.items()])
        if warnings:
            lines.extend(["", "Optional reference export warnings:"] + [f"- {w}" for w in warnings])
        (root / "validation" / "profile_quality_report.txt").write_text("\n".join(lines), encoding="utf-8")
    cfd_root_rel = _csv_relpath(root, out_dir)
    append_global_params(out_dir, [
        ["cfd2d_exports_enabled", 1, "0/1", "2D CFD/FEM profile interface generated."],
        ["cfd2d_root", cfd_root_rel, "folder", "Root folder for 2D CFD/FEM inputs, outside CATIA/Inputs."],
        ["cfd2d_geometry_root", f"{cfd_root_rel}\\geometry", "folder", "Root folder for 2D profile geometry variants."],
        ["cfd2d_open_profile_manifest", f"{cfd_root_rel}\\geometry\\open_ramair\\profile_manifest.json", "file", "Open ram-air 2D profile manifest."],
        ["cfd2d_closed_profile_manifest", f"{cfd_root_rel}\\geometry\\closed_reference\\profile_manifest.json", "file", "Closed reference 2D profile manifest."],
        ["cfd2d_case_config_template", f"{cfd_root_rel}\\config\\cfd2d_case_config_template.json", "file", "2D CFD case config template."],
        ["cfd2d_mesh_config", f"{cfd_root_rel}\\config\\cfd2d_mesh_config.json", "file", "2D mesh config for ramair_2d_mesh_builder."],
        ["cfd2d_profile_quality_report", f"{cfd_root_rel}\\validation\\profile_quality_report.txt", "file", "2D profile quality report."],
    ])



def _apply_2d_reference_cli_overrides(args: argparse.Namespace) -> None:
    """Override optional 2D reference/Ross profile paths from the CLI.

    These are intentionally stored as module-level settings because they affect only
    the optional 2D CFD/FEM export interface, not the CATIA canopy geometry Config.
    Paths may be absolute or relative to the current working directory. If a relative
    path does not exist there, write_2d_cae_exports also tries the script directory.
    """
    global REFERENCE_UNCUT_PROFILE_PATH, ROSS_STANDARD_PROFILE_PATH, ROSS_MINIMUM_PROFILE_PATH
    if getattr(args, "input_csv_option", None) is not None:
        args.input_csv = args.input_csv_option
    if getattr(args, "reference_uncut_profile", None) is not None:
        REFERENCE_UNCUT_PROFILE_PATH = str(args.reference_uncut_profile)
    if getattr(args, "ross_standard_profile", None) is not None:
        ROSS_STANDARD_PROFILE_PATH = str(args.ross_standard_profile)
    if getattr(args, "ross_minimum_profile", None) is not None:
        ROSS_MINIMUM_PROFILE_PATH = str(args.ross_minimum_profile)


def main() -> None:
    args = parse_args()
    cfg = _apply_default_case_config(build_config_from_user_settings(), args)
    _apply_2d_reference_cli_overrides(args)
    cfg = config_with_cli_overrides(cfg, args)
    cfg = maybe_apply_suspension_beta_to_canopy_cfg(cfg)
    preprocess_profile(cfg)
    write_2d_cae_exports(cfg)

    # Root-level plot requested for quick visual inspection before CATIA.
    write_chord_distribution_plot(cfg.out_dir)

    # Always create the editable JSON template if absent; generation is still controlled
    # by ENABLE_CANOPY_STABILIZERS, ENABLE_TIP_SIDE_BULGE and ENABLE_SUSPENSION_LINES.
    system_cfg = _load_system_json(cfg.out_dir, cfg.cells)
    append_json_control_params(cfg.out_dir, system_cfg)
    write_fabric_property_outputs(cfg.out_dir, cfg, system_cfg)

    stabs = None
    if ENABLE_CANOPY_STABILIZERS or bool(_deep_get(system_cfg, ["stabilizers", "active"], False)):
        stabs = _generate_stabilizers_after_canopy(cfg, system_cfg)
    else:
        stabs = StabilizerGeometry(CanopyGeometry(cfg.out_dir), system_cfg)
        write_stabilizer_outputs(cfg.out_dir, stabs)

    tip = None
    if ENABLE_TIP_SIDE_BULGE or bool(_deep_get(system_cfg, ["tip_side_bulge", "active"], False)):
        tip = _generate_tip_side_bulge_after_canopy(cfg, system_cfg)
    else:
        tip = TipSideBulgeGeometry(CanopyGeometry(cfg.out_dir), system_cfg)
        write_tip_side_bulge_outputs(cfg.out_dir, tip)

    network, report = _generate_suspension_after_canopy(cfg, system_cfg, stabs)
    write_full_design_summary(cfg.out_dir, cfg, system_cfg, stabs, tip, network, report)
    write_cad_export_manifest(cfg.out_dir, cfg)
    _write_system_template(Path(cfg.out_dir) / "ramair_suspension_config_template_v19.json", cfg.cells)

    # Final step: group input CSVs into folders and update ramair_global_inputs.csv.
    # The CATScript v19 reads these relative paths directly.
    organize_output_folders(cfg.out_dir)
    print(f"Generated CATIA input files in: {Path(cfg.out_dir).resolve()}")


if __name__ == "__main__":
    main()
