# Referencia de parámetros efectivos de estudio

Versión 2026.10.19. Generada desde los JSON entregados; las configuraciones guardadas de cada modelo/setup son la referencia efectiva después de editar.

Las tablas identifican clave, valor inicial, unidad y función. En el caso CFD Re/Mach/p/T son entradas; las propiedades resultantes se recalculan. En los otros bloques se muestran parámetros de configuración, incluidos controles avanzados. Las métricas de calidad son salidas originales y están en mesh_quality_reference.json; no deben editarse para aprobar otra malla.

Los sufijos *_star son magnitudes adimensionales basadas en c/U; los *_chord y x/c son fracciones de cuerda. Las fases URANS conservan segundos físicos, mientras RANS cuenta iteraciones. No convertir una de estas magnitudes en la otra al continuar.

## CAD

| Clave | Valor inicial | Unidad | Naturaleza | Función |
|---|---|---|---|---|
| project_paths.profiles_dir | Airfoil Profiles | — | Entrada/configuración | Ajusta profiles dir. |
| project_paths.catia_inputs_dir | CATIA/Inputs | — | Entrada/configuración | Ajusta catia inputs dir. |
| project_paths.catia_exports_dir | CATIA/Exports | — | Entrada/configuración | Ajusta catia exports dir. |
| project_paths.cfd_2d_inputs_dir | CFD_2D/CFD_2D_inputs | — | Entrada/configuración | Ajusta cfd 2d inputs dir. |
| project_paths.reports_dir | Application Support/Reports | — | Entrada/configuración | Ajusta reports dir. |
| project_paths.logs_dir | Application Support/Logs | — | Entrada/configuración | Ajusta logs dir. |
| profile_inputs.main_profile | Airfoil Profiles/Reference/Company_Delivery_20260924_LS1_roundedTE/Company_Delivery_20260924_LS1_roundedTE.dat | — | Entrada/configuración | Ajusta main profile. |
| profile_inputs.profile_input_order | auto | — | Entrada/configuración | Ajusta profile input order. |
| canopy_geometry.chord_mm | 3016.0 | mm | Entrada/configuración | Longitud de referencia de la sección central del canopy. |
| canopy_geometry.span_mm | 8119.0 | mm | Entrada/configuración | Anchura total del canopy antes de contracción y anhedral. |
| canopy_geometry.cells | 9 | — | Entrada/configuración | Celdas completas. Las costillas loaded delimitan las celdas y reciben líneas. |
| canopy_geometry.anhedral_deg | 17.9 | ° | Entrada/configuración | Ángulo de arco según la convención elegida. Puede derivarse de R/b en Suspensión. |
| canopy_geometry.enable_variable_chord | true | — | Entrada/configuración | Permite disminuir la cuerda hacia las puntas con la distribución elegida. |
| canopy_geometry.chord_mode | elliptic | — | Entrada/configuración | Rectangular: constante; elliptic: elíptica; quasi_elliptic: elíptica modificada por el exponente. |
| canopy_geometry.tip_chord_factor | 0.55 | — | Entrada/configuración | Escala final de cuerda hacia ambas puntas. |
| canopy_geometry.chord_anchor | quarter_chord | — | Entrada/configuración | Ajusta chord anchor. |
| canopy_geometry.chord_anchor_fraction | 0.5 | — | Entrada/configuración | Estación que permanece alineada: 0 borde de ataque, 0.5 media cuerda, 1 borde de salida. |
| canopy_geometry.quasi_elliptic_exponent | 0.65 | — | Entrada/configuración | Modifica la distribución quasi_elliptic; 1 recupera la forma elíptica. |
| canopy_geometry.enable_variable_cell_span | false | — | Entrada/configuración | Distribuye el ancho entre celdas conservando la envergadura total. |
| canopy_geometry.cell_span_mode | uniform | — | Entrada/configuración | Uniforme, elíptica o cuasi elíptica. El ancho de cada celda se normaliza a la envergadura. |
| canopy_geometry.tip_cell_width_factor | 0.75 | — | Entrada/configuración | Proporción usada para reducir las celdas exteriores. |
| canopy_geometry.enable_span_shrinkage | false | — | Entrada/configuración | Reduce la envergadura plana con la fracción especificada. |
| canopy_geometry.span_shrinkage_fraction | 0.15 | — | Entrada/configuración | 0.15 representa un acortamiento de envergadura del 15 %. |
| canopy_geometry.anhedral_arc_mode | center_to_tip_line | — | Entrada/configuración | center_to_tip_line mide la recta centro-punta; tip_tangent mide la tangente en la punta. |
| canopy_geometry.anhedral_rotation_pivot_mode | profile_min_z | — | Entrada/configuración | Punto de referencia vertical para orientar las costillas sobre el arco. |
| canopy_geometry.anhedral_section_orientation | focus_inward | — | Entrada/configuración | focus_inward orienta las secciones hacia el foco del arco; conserva la convención actual de CATIA. |
| rib_and_cell_geometry.enable_rib_inc_translation | false | — | Entrada/configuración | Aplica incidencias y desplazamientos diferenciados a costillas loaded y nonloaded. |
| rib_and_cell_geometry.loaded_rib_incidence_deg | 0.0 | ° | Entrada/configuración | Ajusta loaded rib incidence deg. |
| rib_and_cell_geometry.nonloaded_rib_incidence_deg | 1.5 | ° | Entrada/configuración | Ajusta nonloaded rib incidence deg. |
| rib_and_cell_geometry.loaded_rib_vertical_offset_mm | 0.0 | mm | Entrada/configuración | Ajusta loaded rib vertical offset mm. |
| rib_and_cell_geometry.nonloaded_rib_vertical_offset_mm | 19.05 | mm | Entrada/configuración | Ajusta nonloaded rib vertical offset mm. |
| rib_and_cell_geometry.loaded_rib_chordwise_offset_mm | 0.0 | mm | Entrada/configuración | Ajusta loaded rib chordwise offset mm. |
| rib_and_cell_geometry.nonloaded_rib_chordwise_offset_mm | 0.0 | mm | Entrada/configuración | Ajusta nonloaded rib chordwise offset mm. |
| rib_and_cell_geometry.tip_extra_incidence_deg | 0.0 | ° | Entrada/configuración | Ajusta tip extra incidence deg. |
| rib_and_cell_geometry.tip_extra_vertical_offset_mm | 0.0 | mm | Entrada/configuración | Ajusta tip extra vertical offset mm. |
| rib_and_cell_geometry.enable_cell_ballooning | false | — | Entrada/configuración | Genera secciones intermedias infladas para los lofts entre costillas. |
| rib_and_cell_geometry.max_thickness_increase_fraction | 0.3 | — | Entrada/configuración | 0.30 aumenta el espesor un 30 % en el centro de cada celda. |
| rib_and_cell_geometry.midcell_aft_shift_mm | 0.0 | mm | Entrada/configuración | Ajusta midcell aft shift mm. |
| airfoil_processing.te_closure_mode | rounded | — | Entrada/configuración | Ajusta te closure mode. |
| airfoil_processing.te_rounding_points | 30 | — | Entrada/configuración | Ajusta te rounding points. |
| airfoil_processing.sharp_te_intersection_max_x_c | 1.08 | — | Entrada/configuración | Ajusta sharp te intersection max x c. |
| airfoil_processing.sharp_te_min_extension_x_c | 1.0 | — | Entrada/configuración | Ajusta sharp te min extension x c. |
| airfoil_processing.sharp_te_safe_gap_chord | 1e-05 | — | Entrada/configuración | Ajusta sharp te safe gap chord. |
| airfoil_processing.min_spline_point_distance_mm | 0.01 | mm | Entrada/configuración | Ajusta min spline point distance mm. |
| airfoil_processing.model_zero_thickness_as_thin_solid | true | — | Entrada/configuración | Activa o desactiva model zero thickness as thin solid. |
| airfoil_processing.fabric_thickness_chord | 1e-05 | — | Entrada/configuración | Ajusta fabric thickness chord. |
| crossports.enable_crossports | true | — | Entrada/configuración | Activa o desactiva enable crossports. |
| crossports.cut_mode | post_split_surfaces | — | Entrada/configuración | Ajusta cut mode. |
| crossports.split_orientation | -1 | — | Entrada/configuración | Ajusta split orientation. |
| crossports.cut_strategy | curve_split_first | — | Entrada/configuración | Ajusta cut strategy. |
| crossports.cutter_extrude_mode | auto_semicell_span | — | Entrada/configuración | Ajusta cutter extrude mode. |
| crossports.cutter_extrude_factor_semicell | 0.35 | — | Entrada/configuración | Ajusta cutter extrude factor semicell. |
| crossports.cutter_extrude_min_mm | 20.0 | mm | Entrada/configuración | Ajusta cutter extrude min mm. |
| crossports.cutter_extrude_max_mm | 600.0 | mm | Entrada/configuración | Ajusta cutter extrude max mm. |
| crossports.cutter_extrude_mm | 200.0 | mm | Entrada/configuración | Ajusta cutter extrude mm. |
| crossports.shape | ellipse | — | Entrada/configuración | Geometría elegida para esta pieza; solo se muestran sus parámetros aplicables. |
| crossports.ellipse_orientation | horizontal | — | Entrada/configuración | Ajusta ellipse orientation. |
| crossports.count | 3 | — | Entrada/configuración | Ajusta count. |
| crossports.position_mode | standard_3 | — | Entrada/configuración | Ajusta position mode. |
| crossports.x_positions_chord | [0.25, 0.45, 0.65] | — | Entrada/configuración | Ajusta x positions chord. |
| crossports.x_start_chord | 0.25 | — | Entrada/configuración | Ajusta x start chord. |
| crossports.x_end_chord | 0.7 | — | Entrada/configuración | Ajusta x end chord. |
| crossports.width_fraction_chord | 0.08 | — | Entrada/configuración | Ajusta width fraction chord. |
| crossports.height_fraction_local_thickness | 0.15 | — | Entrada/configuración | Ajusta height fraction local thickness. |
| crossports.edge_clearance_fraction_local_thickness | 0.22 | — | Entrada/configuración | Ajusta edge clearance fraction local thickness. |
| crossports.points_per_loop | 32 | — | Entrada/configuración | Ajusta points per loop. |
| crossports.apply_to | all_internal | — | Entrada/configuración | Ajusta apply to. |
| crossports.centerline_mode | profile_midline | — | Entrada/configuración | Ajusta centerline mode. |
| crossports.custom_specs | [{"hole_id": "crossport-1", "x": 0.25, "shape": "ellipse", "orientation": "horizontal", "width_chord_frac": 0.08, "height_thickness_frac": 0.15, "points_per_loop": 32}, {"hole_id": "crossport-2", "x": 0.45, "shape": "ellipse", "orientation": "horizontal", "width_chord_frac": 0.08, "height_thickness_frac": 0.15, "points_per_loop": 32}, {"hole_id": "crossport-3", "x": 0.65, "shape": "ellipse", "orientation": "horizontal", "width_chord_frac": 0.08, "height_thickness_frac": 0.15, "points_per_loop": 32}] | — | Entrada/configuración | Ajusta custom specs. |
| fabric_and_lines.enable_fabric_thickness_properties | true | — | Entrada/configuración | Activa o desactiva enable fabric thickness properties. |
| fabric_and_lines.fabric_thickness_mm | 0.3 | mm | Entrada/configuración | Ajusta fabric thickness mm. |
| fabric_and_lines.fabric_density_kg_m3 | 1150.0 | — | Entrada/configuración | Ajusta fabric density kg m3. |
| fabric_and_lines.fabric_material | ripstop_nylon_placeholder | — | Entrada/configuración | Ajusta fabric material. |
| fabric_and_lines.fabric_thickness_strategy | shell_property | — | Entrada/configuración | Ajusta fabric thickness strategy. |
| fabric_and_lines.enable_fabric_thickness | false | — | Entrada/configuración | Activa o desactiva enable fabric thickness. |
| fabric_and_lines.fabric_thickness_mode | none | — | Entrada/configuración | Ajusta fabric thickness mode. |
| fabric_and_lines.fabric_thickness_offset_side | normal | — | Entrada/configuración | Ajusta fabric thickness offset side. |
| fabric_and_lines.fabric_thickness_include_ribs | true | — | Entrada/configuración | Activa o desactiva fabric thickness include ribs. |
| fabric_and_lines.fabric_thickness_include_stabilizers | true | — | Entrada/configuración | Activa o desactiva fabric thickness include stabilizers. |
| fabric_and_lines.fabric_thickness_include_tip_bulge | true | — | Entrada/configuración | Activa o desactiva fabric thickness include tip bulge. |
| fabric_and_lines.enable_suspension_tube_geometry | false | — | Entrada/configuración | Activa o desactiva enable suspension tube geometry. |
| fabric_and_lines.default_suspension_line_diameter_mm | 1.2 | mm | Entrada/configuración | Ajusta default suspension line diameter mm. |
| fabric_and_lines.suspension_line_cad_strategy | curve_with_properties | — | Entrada/configuración | Ajusta suspension line cad strategy. |
| catia_generation.create_rib_fills | true | — | Entrada/configuración | Activa o desactiva create rib fills. |
| catia_generation.create_loft_panels | true | — | Entrada/configuración | Activa o desactiva create loft panels. |
| catia_generation.create_te_closure_panels | true | — | Entrada/configuración | Activa o desactiva create te closure panels. |
| catia_generation.force_export_to_base_folder | true | — | Entrada/configuración | Activa o desactiva force export to base folder. |
| catia_exports.export_iges | true | — | Entrada/configuración | Activa o desactiva export iges. |
| catia_exports.export_dxf | true | — | Entrada/configuración | Activa o desactiva export dxf. |
| catia_exports.export_full_assembly_iges | true | — | Entrada/configuración | Activa o desactiva export full assembly iges. |
| catia_exports.export_full_assembly_step | false | — | Entrada/configuración | Activa o desactiva export full assembly step. |
| catia_exports.export_component_manifest | true | — | Entrada/configuración | Activa o desactiva export component manifest. |
| catia_exports.exports_subfolder | ../CATIA_exports | — | Entrada/configuración | Ajusta exports subfolder. |
| catia_exports.export_canopy_filename | ramair_canopy_geometry.igs | — | Entrada/configuración | Ajusta export canopy filename. |
| catia_exports.export_full_filename | ramair_full_assembly.igs | — | Entrada/configuración | Ajusta export full filename. |
| optional_modules.enable_canopy_stabilizers | false | — | Entrada/configuración | Activa o desactiva enable canopy stabilizers. |
| optional_modules.enable_suspension_lines | true | — | Entrada/configuración | Activa o desactiva enable suspension lines. |
| optional_modules.enable_tip_side_bulge | false | — | Entrada/configuración | Activa o desactiva enable tip side bulge. |
| optional_modules.system_config_json | Workspaces/Company_Delivery_20260924/CATIA/Prepared_model/system_config.json | — | Entrada/configuración | Ajusta system config json. |
| cfd_2d_exports.enable_2d_cae_exports | false | — | Entrada/configuración | Activa o desactiva enable 2d cae exports. |
| debug_plots.write_chord_distribution_plot | true | — | Entrada/configuración | Activa o desactiva write chord distribution plot. |
| debug_plots.write_cell_span_plot | true | — | Entrada/configuración | Activa o desactiva write cell span plot. |
| debug_plots.write_anhedral_plot | true | — | Entrada/configuración | Activa o desactiva write anhedral plot. |
| debug_plots.write_crossports_plot | true | — | Entrada/configuración | Activa o desactiva write crossports plot. |
| debug_plots.write_stabilizer_plot | true | — | Entrada/configuración | Activa o desactiva write stabilizer plot. |
| debug_plots.write_suspension_plot | true | — | Entrada/configuración | Activa o desactiva write suspension plot. |

## Sistema CAD

| Clave | Valor inicial | Unidad | Naturaleza | Función |
|---|---|---|---|---|
| suspension.enabled | true | — | Entrada/configuración | Activa o desactiva enabled. |
| suspension.derive_anhedral_from_R_over_b | true | — | Entrada/configuración | Usa beta = 1/(4 R/b) en radianes para la geometría de partida. |
| canopy_reference.p_ref_x_c | 0.25 | — | Entrada/configuración | Ajusta p ref x c. |
| canopy_reference.p_ref_z_mode | chord_line | — | Entrada/configuración | Ajusta p ref z mode. |
| canopy_reference.description | P_ref is defined in the central rib plane. Use chord_line to avoid contamination by profile thickness. | — | Entrada/configuración | Ajusta description. |
| banks | [{"name": "A", "enabled": true, "type": "structural", "x_c": 0.1, "riser_group": "front"}, {"name": "B", "enabled": true, "type": "structural", "x_c": 0.3, "riser_group": "front"}, {"name": "C", "enabled": true, "type": "structural", "x_c": 0.6, "riser_group": "rear"}, {"name": "D", "enabled": true, "type": "structural", "x_c": 0.8, "riser_group": "rear"}, {"name": "BRK", "enabled": true, "type": "brake", "x_c": 0.95, "riser_group": "brake"}] | — | Entrada/configuración | Ajusta banks. |
| loaded_rib_selection.mode | cell_boundaries | — | Entrada/configuración | Ajusta mode. |
| loaded_rib_selection.indices | [0, 2, 4, 5, 7, 9] | — | Entrada/configuración | Ajusta indices. |
| loaded_rib_selection.enforce_symmetric_pairs | true | — | Entrada/configuración | Activa o desactiva enforce symmetric pairs. |
| loaded_rib_selection.description | cell_boundaries are the loaded boundary ribs from left to right, indexed 0..num_cells. For 9 cells, [0,2,4,5,7,9] gives 6 loaded stations. | — | Entrada/configuración | Ajusta description. |
| anchors.surface | lower | — | Entrada/configuración | Ajusta surface. |
| anchors.z_offset_mm | 0.0 | mm | Entrada/configuración | Ajusta z offset mm. |
| anchors.name_format | {bank}_rib{rib_id:02d}_{side} | — | Entrada/configuración | Ajusta name format. |
| anchors.tip_anchor_mode | auto | — | Entrada/configuración | Ajusta tip anchor mode. |
| anchors.tip_attachment_banks | ["A", "B", "C", "D"] | — | Entrada/configuración | Ajusta tip attachment banks. |
| anchors.description | Tip anchors use the lower rib profile by default. If stabilizers.active and stabilizers.attach_tip_lines_to_stabilizer are true, external structural tip anchors move to the stabilizer edge. Set tip_anchor_mode='rib' to force rib anchors or 'stabilizer' to force stabilizer anchors. | — | Entrada/configuración | Ajusta description. |
| cascades.structural | [{"level": 1, "group_size": 2, "fraction_to_target": 0.25, "grouping_rule": "adjacent_spanwise", "bank_applicability": ["A", "B", "C", "D"]}, {"level": 2, "group_size": 2, "fraction_to_target": 0.55, "grouping_rule": "adjacent_spanwise", "bank_applicability": ["A", "B", "C", "D"]}] | — | Entrada/configuración | Ajusta structural. |
| cascades.brake | [{"level": 1, "group_size": 2, "fraction_to_target": 0.2, "grouping_rule": "adjacent_spanwise", "bank_applicability": ["BRK"]}, {"level": 2, "group_size": 2, "fraction_to_target": 0.6, "grouping_rule": "adjacent_spanwise", "bank_applicability": ["BRK"]}] | — | Entrada/configuración | Ajusta brake. |
| cascades.description | P_cascade = centroid(children) + fraction_to_target*(target-centroid). Groups are created centre-to-tip on each side to enforce mirror symmetry. | — | Entrada/configuración | Ajusta description. |
| angles.alpha_op_deg | 4.2 | ° | Entrada/configuración | Ángulo aerodinámico de la cuerda respecto al flujo; usado en la relación angular del esquema CAD. |
| angles.gamma_deg | 17.05 | ° | Entrada/configuración | Inclinación de la trayectoria según la convención alpha + mu = gamma + theta. |
| angles.mu_deg | 1.0 | ° | Entrada/configuración | Desfase angular entre referencias de suspensión y canopy dentro de la relación de cierre. |
| angles.theta_deg | null | ° | Entrada/configuración | Giro respecto a la vertical descendente en el plano X–Z; 0 muestra el sistema vertical. Vacío usa alpha + mu − gamma. |
| angles.angle_tolerance_deg | 0.25 | ° | Entrada/configuración | Ajusta angle tolerance deg. |
| angles.description | If theta_deg is null, theta = alpha_op + mu - gamma. v16 deliberately does not export long rigging-measurement diagnostics; the suspension/payload system is positioned using this theta relation and the report only checks the angular closure error. | — | Entrada/configuración | Ajusta description. |
| constraints.R_over_b | 0.8 | — | Entrada/configuración | Radio de referencia de suspensión dividido por envergadura. Las longitudes reales se calculan sumando segmentos y se auditan por banda. |
| constraints.R_tolerance_fraction | 0.01 | — | Entrada/configuración | Ajusta r tolerance fraction. |
| constraints.auto_solve_R | true | — | Entrada/configuración | Activa o desactiva auto solve r. |
| constraints.R_definition | straight_anchor_to_central_confluence | — | Entrada/configuración | Ajusta r definition. |
| constraints.R_target_point | slider_center_on_symmetry_axis | — | Entrada/configuración | Ajusta r target point. |
| constraints.path_length_dispersion_warning_fraction | 0.08 | — | Entrada/configuración | Ajusta path length dispersion warning fraction. |
| constraints.max_bifurcation_angle_deg | 55.0 | ° | Entrada/configuración | Ajusta max bifurcation angle deg. |
| constraints.symmetry_tolerance_mm | 1.0 | mm | Entrada/configuración | Ajusta symmetry tolerance mm. |
| constraints.segment_length_symmetry_tolerance_mm | 1.0 | mm | Entrada/configuración | Ajusta segment length symmetry tolerance mm. |
| constraints.description | R/b is solved by moving slider/risers/payload along the payload line defined by theta. | — | Entrada/configuración | Ajusta description. |
| line_properties.line_diameter_mm | 1.2 | mm | Entrada/configuración | Ajusta line diameter mm. |
| line_properties.material | aramid_or_Dyneema_placeholder | — | Entrada/configuración | Ajusta material. |
| line_properties.line_density_kg_m | 0.001 | m | Entrada/configuración | Ajusta line density kg m. |
| line_properties.elastic_modulus_pa | 50000000000.0 | Pa | Entrada/configuración | Ajusta elastic modulus pa. |
| line_properties.poisson_ratio | 0.35 | — | Entrada/configuración | Ajusta poisson ratio. |
| line_properties.nominal_segment_tension_N | 50.0 | — | Entrada/configuración | Ajusta nominal segment tension n. |
| line_properties.reference_velocity_m_s | 20.0 | s | Entrada/configuración | Ajusta reference velocity m s. |
| line_properties.air_density_kg_m3 | 1.225 | — | Entrada/configuración | Ajusta air density kg m3. |
| line_properties.dynamic_viscosity_pa_s | 1.81e-05 | s | Entrada/configuración | Ajusta dynamic viscosity pa s. |
| line_properties.strouhal_number | 0.22 | — | Entrada/configuración | Ajusta strouhal number. |
| line_properties.line_visualization_mode | curve | — | Entrada/configuración | Ajusta line visualization mode. |
| line_properties.cad_strategy | curve_with_properties | — | Entrada/configuración | Ajusta cad strategy. |
| line_properties.Cd0_line | 1.2 | — | Entrada/configuración | Ajusta cd0 line. |
| line_properties.segmented_drag_active | true | — | Entrada/configuración | Activa o desactiva segmented drag active. |
| line_properties.velocity_reference | alpha_op | — | Entrada/configuración | Ajusta velocity reference. |
| line_properties.incident_velocity_angle_deg | null | ° | Entrada/configuración | Ajusta incident velocity angle deg. |
| line_properties.include_lower_straps_in_drag | true | — | Entrada/configuración | Activa o desactiva include lower straps in drag. |
| line_properties.include_brake_lines_in_drag | true | — | Entrada/configuración | Activa o desactiva include brake lines in drag. |
| line_properties.description | For CD_lines_segmented, the incident velocity is defined in the central X-Z plane. velocity_reference='alpha_op' uses the canopy operating angle of attack; 'gamma' uses the glide angle; 'custom' uses incident_velocity_angle_deg. | — | Entrada/configuración | Ajusta description. |
| risers.mode | four_riser_slider_corners | — | Entrada/configuración | Ajusta mode. |
| risers.mapping.A | front | — | Entrada/configuración | Ajusta a. |
| risers.mapping.B | front | — | Entrada/configuración | Ajusta b. |
| risers.mapping.C | rear | — | Entrada/configuración | Ajusta c. |
| risers.mapping.D | rear | — | Entrada/configuración | Ajusta d. |
| risers.mapping.BRK | brake | — | Entrada/configuración | Ajusta brk. |
| risers.side_y_fraction_b_if_no_slider | 0.08 | — | Entrada/configuración | Ajusta side y fraction b if no slider. |
| risers.front_x_c_if_no_slider | 0.22 | — | Entrada/configuración | Ajusta front x c if no slider. |
| risers.rear_x_c_if_no_slider | 0.7 | — | Entrada/configuración | Ajusta rear x c if no slider. |
| risers.brake_x_c_if_no_slider | 0.95 | — | Entrada/configuración | Ajusta brake x c if no slider. |
| risers.initial_slider_distance_mm | null | mm | Entrada/configuración | Ajusta initial slider distance mm. |
| risers.brake_actuator_offset_depth_mm | 120.0 | mm | Entrada/configuración | Ajusta brake actuator offset depth mm. |
| risers.brake_actuator_offset_span_mm | 80.0 | mm | Entrada/configuración | Ajusta brake actuator offset span mm. |
| risers.brake_actuator_offset_down_mm | 80.0 | mm | Entrada/configuración | Ajusta brake actuator offset down mm. |
| risers.description | With active slider, front/rear risers are placed at slider corners. Brake actuators are offset from rear corners. | — | Entrada/configuración | Ajusta description. |
| slider.active | true | — | Entrada/configuración | Activa o desactiva active. |
| slider.slider_area_ratio | 0.02 | — | Entrada/configuración | Ajusta slider area ratio. |
| slider.slider_width_mm | null | mm | Entrada/configuración | Ajusta slider width mm. |
| slider.slider_chord_or_depth_mm | null | mm | Entrada/configuración | Ajusta slider chord or depth mm. |
| slider.slider_aspect_ratio | 1.0 | — | Entrada/configuración | Ajusta slider aspect ratio. |
| slider.description | Slider plane is perpendicular to the payload line. width is spanwise, depth is chordwise in the local slider plane. | — | Entrada/configuración | Ajusta description. |
| payload.active | true | — | Entrada/configuración | Activa o desactiva active. |
| payload.payload_length_mm | 700.0 | mm | Entrada/configuración | Ajusta payload length mm. |
| payload.payload_width_mm | 800.0 | mm | Entrada/configuración | Ajusta payload width mm. |
| payload.payload_height_mm | 500.0 | mm | Entrada/configuración | Ajusta payload height mm. |
| payload.payload_drop_below_slider_mm | 1200.0 | mm | Entrada/configuración | Ajusta payload drop below slider mm. |
| payload.payload_CG_offset_local_mm | [0.0, 0.0, 250.0] | mm | Entrada/configuración | Ajusta payload cg offset local mm. |
| payload.payload_attach_offset_local_mm | [0.0, 0.0, 0.0] | mm | Entrada/configuración | Ajusta payload attach offset local mm. |
| payload.lower_straps_active | true | — | Entrada/configuración | Activa o desactiva lower straps active. |
| payload.description | Local axes are depth, span, down-along-payload-line. Positive local z is downward from canopy to payload. | — | Entrada/configuración | Ajusta description. |
| agu.active | true | — | Entrada/configuración | Activa o desactiva active. |
| agu.agu_length_mm | 400.0 | mm | Entrada/configuración | Ajusta agu length mm. |
| agu.agu_width_mm | 300.0 | mm | Entrada/configuración | Ajusta agu width mm. |
| agu.agu_height_mm | 300.0 | mm | Entrada/configuración | Ajusta agu height mm. |
| agu.agu_fraction_between_slider_and_payload | 0.45 | — | Entrada/configuración | Ajusta agu fraction between slider and payload. |
| agu.description | AGU is an optional oriented box between slider and payload attach point. | — | Entrada/configuración | Ajusta description. |
| brakes.active | true | — | Entrada/configuración | Activa o desactiva active. |
| brakes.separate_from_structural | true | — | Entrada/configuración | Activa o desactiva separate from structural. |
| brakes.brake_anchor_x_c | 0.95 | — | Entrada/configuración | Ajusta brake anchor x c. |
| brakes.brake_anchor_distribution | loaded_ribs | — | Entrada/configuración | Ajusta brake anchor distribution. |
| brakes.brake_target | agu_if_active | — | Entrada/configuración | Ajusta brake target. |
| brakes.brake_deflection_mode | null | — | Entrada/configuración | Ajusta brake deflection mode. |
| brakes.brake_deflection_mm | 0.0 | mm | Entrada/configuración | Ajusta brake deflection mm. |
| brakes.brake_line_shortening_mm | 0.0 | mm | Entrada/configuración | Ajusta brake line shortening mm. |
| brakes.trailing_edge_displacement_mm | 0.0 | mm | Entrada/configuración | Ajusta trailing edge displacement mm. |
| brakes.description | Brake lines are independent from A/B/C/D. By default they terminate at AGU_CONTROL when the AGU is active, otherwise at the slider brake actuator. | — | Entrada/configuración | Ajusta description. |
| stabilizers.active | false | — | Entrada/configuración | Activa o desactiva active. |
| stabilizers.shape | triangular | — | Entrada/configuración | Geometría elegida para esta pieza; solo se muestran sus parámetros aplicables. |
| stabilizers.chord_start_x_c | 0.15 | — | Entrada/configuración | Extremo delantero de unión a la costilla exterior. |
| stabilizers.chord_end_x_c | 0.95 | — | Entrada/configuración | Extremo posterior de unión a la costilla exterior. |
| stabilizers.apex_x_c | 0.62 | — | Entrada/configuración | Estación del vértice inferior de un panel triangular. |
| stabilizers.height_mm | 450.0 | mm | Entrada/configuración | Distancia desde el borde de unión en la dirección de altura seleccionada. |
| stabilizers.sweep_mm | 0.0 | mm | Entrada/configuración | Desplaza el borde inferior a lo largo de la dirección de cuerda. |
| stabilizers.lower_edge_scale | 0.85 | — | Entrada/configuración | Longitud del borde inferior respecto al superior para el trapecio. |
| stabilizers.height_direction | rib_down | — | Entrada/configuración | rib_down sigue el plano real de costilla; global_down es vertical global; span_outward apunta hacia fuera. |
| stabilizers.surface_reference | lower | — | Entrada/configuración | Ajusta surface reference. |
| stabilizers.triangular_anchor_edge_mode | lower_perimeter_by_x | — | Entrada/configuración | Ajusta triangular anchor edge mode. |
| stabilizers.attach_to_loaded_ribs | true | — | Entrada/configuración | Activa o desactiva attach to loaded ribs. |
| stabilizers.attach_tip_lines_to_stabilizer | false | — | Entrada/configuración | Mueve los anclajes exteriores de las bandas estructurales al borde inferior del panel. |
| stabilizers.description | Independent side panel on the two external ribs. For line anchors, this is the single main switch: set this true to move external structural tip anchors to the stabilizer edge. Default triangular panel uses UP_LE and UP_TE on the external rib plus an apex controlled by apex_x_c and height_mm. Lines are distributed along UP_LE-apex-UP_TE according to bank x/c, not collapsed at the apex. height_direction: rib_down, global_down, span_outward. | — | Entrada/configuración | Ajusta description. |
| tip_side_bulge.active | false | — | Entrada/configuración | Activa o desactiva active. |
| tip_side_bulge.max_lateral_bulge_mm | 80.0 | mm | Entrada/configuración | Ajusta max lateral bulge mm. |
| tip_side_bulge.chordwise_start_x_c | 0.0 | — | Entrada/configuración | Ajusta chordwise start x c. |
| tip_side_bulge.chordwise_end_x_c | 1.0 | — | Entrada/configuración | Ajusta chordwise end x c. |
| tip_side_bulge.chordwise_peak_x_c | 0.5 | — | Entrada/configuración | Ajusta chordwise peak x c. |
| tip_side_bulge.chordwise_width_fraction | 0.45 | — | Entrada/configuración | Ajusta chordwise width fraction. |
| tip_side_bulge.vertical_peak_fraction | 0.5 | — | Entrada/configuración | Ajusta vertical peak fraction. |
| tip_side_bulge.vertical_width_fraction | 0.6 | — | Entrada/configuración | Ajusta vertical width fraction. |
| tip_side_bulge.shape_power | 1.0 | — | Entrada/configuración | Ajusta shape power. |
| tip_side_bulge.chordwise_points | 17 | — | Entrada/configuración | Ajusta chordwise points. |
| tip_side_bulge.vertical_layers | 11 | — | Entrada/configuración | Ajusta vertical layers. |
| tip_side_bulge.hide_original_flat_tip_ribs_in_CATIA | false | — | Entrada/configuración | Activa o desactiva hide original flat tip ribs in catia. |
| tip_side_bulge.description | Canopy-level optional curved external side panel. The lateral bulge is zero on upper and lower boundaries and at chordwise_start/end so the generated surface closes with the external upper/lower canopy panels instead of floating apart. | — | Entrada/configuración | Ajusta description. |
| fabric_thickness.properties_enabled | true | — | Entrada/configuración | Activa o desactiva properties enabled. |
| fabric_thickness.thickness_mm | 0.3 | mm | Entrada/configuración | Ajusta thickness mm. |
| fabric_thickness.density_kg_m3 | 1150.0 | — | Entrada/configuración | Ajusta density kg m3. |
| fabric_thickness.material | ripstop_nylon_placeholder | — | Entrada/configuración | Ajusta material. |
| fabric_thickness.strategy | shell_property | — | Entrada/configuración | Ajusta strategy. |
| fabric_thickness.catia_offsets_enabled | false | — | Entrada/configuración | Activa o desactiva catia offsets enabled. |
| fabric_thickness.catia_offset_mode | null | — | Entrada/configuración | Ajusta catia offset mode. |
| fabric_thickness.description | Recommended strategy: export mid-surfaces + shell thickness metadata for FEM; create physical thickness later in meshing/FEM software. CATIA offsets are experimental and disabled by default. | — | Entrada/configuración | Ajusta description. |
| outputs.write_visualizer_png | true | — | Entrada/configuración | Activa o desactiva write visualizer png. |
| outputs.visualizer_png | ramair_suspension_network_preview.png | — | Entrada/configuración | Ajusta visualizer png. |
| outputs.write_json_used | true | — | Entrada/configuración | Activa o desactiva write json used. |
| outputs.catia_create_points_and_lines | true | — | Entrada/configuración | Activa o desactiva catia create points and lines. |
| outputs.catia_create_surfaces | true | — | Entrada/configuración | Activa o desactiva catia create surfaces. |
| outputs.catia_create_tubes | false | — | Entrada/configuración | Activa o desactiva catia create tubes. |
| outputs.export_subfolder | ../CATIA_exports | — | Entrada/configuración | Ajusta export subfolder. |
| outputs.labels | false | — | Entrada/configuración | Activa o desactiva labels. |
| suspension_schema_version | 2 | — | Entrada/configuración | Ajusta suspension schema version. |
| brake_selection.mode | outer_fraction | — | Entrada/configuración | Ajusta mode. |
| brake_selection.symmetric | true | — | Entrada/configuración | Activa o desactiva symmetric. |
| brake_selection.fraction_L | 0.33 | — | Entrada/configuración | Ajusta fraction l. |
| brake_selection.fraction_R | 0.33 | — | Entrada/configuración | Ajusta fraction r. |
| brake_selection.cells_L | [] | — | Entrada/configuración | Ajusta cells l. |
| brake_selection.cells_R | [] | — | Entrada/configuración | Ajusta cells r. |
| cascade_layout.template | chordwise | — | Entrada/configuración | Ajusta template. |
| cascade_layout.fraction | 0.45 | — | Entrada/configuración | Ajusta fraction. |
| cascade_layout.span_group_size | 2 | — | Entrada/configuración | Ajusta span group size. |
| cascade_layout.span_fraction | 0.7 | — | Entrada/configuración | Ajusta span fraction. |

## Condiciones CFD

| Clave | Valor inicial | Unidad | Naturaleza | Función |
|---|---|---|---|---|
| reynolds | 1900000.0 | 1 | Entrada/configuración | Relación entre inercia y viscosidad basada en la cuerda. |
| mach | 0.15 | 1 | Entrada/configuración | Velocidad dividida por la velocidad del sonido. |
| pressure_ref_pa | 101325.0 | Pa | Entrada/configuración | Ajusta pressure ref pa. |
| temperature_K | 288.15 | K | Entrada/configuración | Ajusta temperature k. |
| chord_m | 1.0 | m | Derivada o referencia física | Ajusta chord m. |
| rho | 1.2250122659906946 | kg/m³ | Derivada o referencia física | Ajusta rho. |
| mu | 3.291017569199909e-05 | Pa·s | Derivada o referencia física | Ajusta mu. |
| nu | 2.6865180541995553e-05 | m²/s | Derivada o referencia física | Ajusta nu. |
| speed_of_sound_m_s | 340.29228686527705 | m/s | Derivada o referencia física | Ajusta speed of sound m s. |
| velocity_m_s | 51.04384302979155 | m/s | Derivada o referencia física | Ajusta velocity m s. |
| velocity_source | mach | — | Derivada o referencia física | Ajusta velocity source. |
| viscosity_policy | match_Re_at_Mach_with_unit_chord | — | Derivada o referencia física | Ajusta viscosity policy. |
| gas_constant_J_kg_K | 287.05 | J/(kg·K) | Derivada o referencia física | Ajusta gas constant j kg k. |
| gamma | 1.4 | — | Derivada o referencia física | Ajusta gamma. |

## Malla cerrada

| Clave | Valor inicial | Unidad | Naturaleza | Función |
|---|---|---|---|---|
| schema_version | 3 | — | Entrada/configuración | Ajusta schema version. |
| name | validation_medium_01 | — | Entrada/configuración | Ajusta name. |
| topology | closed | — | Entrada/configuración | Ajusta topology. |
| geometry.spanwise_thickness_chord | 0.01 | — | Entrada/configuración | Ajusta spanwise thickness chord. |
| geometry.spanwise_layers | 1 | — | Entrada/configuración | Ajusta spanwise layers. |
| boundary_layer.layers | 75 | — | Entrada/configuración | Ajusta layers. |
| boundary_layer.growth_rate | 1.1 | — | Entrada/configuración | Ajusta growth rate. |
| boundary_layer.distribution_mode | beta_law | — | Entrada/configuración | Ajusta distribution mode. |
| boundary_layer.thickness_safety_factor | 1.2 | — | Entrada/configuración | Ajusta thickness safety factor. |
| boundary_layer.target_y_plus | 0.6666666666666666 | — | Entrada/configuración | Ajusta target y plus. |
| boundary_layer.wall_nodes_target | 1200 | — | Entrada/configuración | Ajusta wall nodes target. |
| boundary_layer.minimum_tangential_size_chord | 0.0003 | — | Entrada/configuración | Ajusta minimum tangential size chord. |
| boundary_layer.maximum_tangential_size_chord | 0.005 | — | Entrada/configuración | Ajusta maximum tangential size chord. |
| boundary_layer.wall_bump_coefficient | 0.45 | — | Entrada/configuración | Ajusta wall bump coefficient. |
| boundary_layer.leading_edge_nodes | 70 | — | Entrada/configuración | Ajusta leading edge nodes. |
| boundary_layer.te_cap_nodes | 35 | — | Entrada/configuración | Ajusta te cap nodes. |
| boundary_layer.minimum_profile_point_spacing_chord | 1e-06 | — | Entrada/configuración | Ajusta minimum profile point spacing chord. |
| boundary_layer.automatic_bump_matching | false | — | Entrada/configuración | Activa o desactiva automatic bump matching. |
| boundary_layer.tangential_distribution_method | four_bumps | — | Entrada/configuración | Ajusta tangential distribution method. |
| boundary_layer.split_progression_midpoint_x_chord | 0.5 | — | Entrada/configuración | Ajusta split progression midpoint x chord. |
| boundary_layer.segment_divisions.te | 90 | — | Entrada/configuración | Ajusta te. |
| boundary_layer.segment_divisions.upper | 810 | — | Entrada/configuración | Ajusta upper. |
| boundary_layer.segment_divisions.leading_or_inlet | 150 | — | Entrada/configuración | Ajusta leading or inlet. |
| boundary_layer.segment_divisions.lower | 750 | — | Entrada/configuración | Ajusta lower. |
| boundary_layer.manual_bump_coefficients.te | 1.0 | — | Entrada/configuración | Ajusta te. |
| boundary_layer.manual_bump_coefficients.upper | 0.55 | — | Entrada/configuración | Ajusta upper. |
| boundary_layer.manual_bump_coefficients.leading_or_inlet | 1.00007 | — | Entrada/configuración | Ajusta leading or inlet. |
| boundary_layer.manual_bump_coefficients.lower | 0.5 | — | Entrada/configuración | Ajusta lower. |
| boundary_layer.manual_split_progression.split_divisions.upper_leading_or_inlet | 110 | — | Entrada/configuración | Ajusta upper leading or inlet. |
| boundary_layer.manual_split_progression.split_divisions.upper_te | 110 | — | Entrada/configuración | Ajusta upper te. |
| boundary_layer.manual_split_progression.split_divisions.lower_leading_or_inlet | 110 | — | Entrada/configuración | Ajusta lower leading or inlet. |
| boundary_layer.manual_split_progression.split_divisions.lower_te | 110 | — | Entrada/configuración | Ajusta lower te. |
| boundary_layer.manual_split_progression.progression_coefficients.upper_leading_or_inlet | 1.02 | — | Entrada/configuración | Ajusta upper leading or inlet. |
| boundary_layer.manual_split_progression.progression_coefficients.upper_te | 1.02 | — | Entrada/configuración | Ajusta upper te. |
| boundary_layer.manual_split_progression.progression_coefficients.lower_leading_or_inlet | 1.02 | — | Entrada/configuración | Ajusta lower leading or inlet. |
| boundary_layer.manual_split_progression.progression_coefficients.lower_te | 1.02 | — | Entrada/configuración | Ajusta lower te. |
| boundary_layer.bump_maximum_growth_ratio | 1.2 | — | Entrada/configuración | Ajusta bump maximum growth ratio. |
| boundary_layer.bump_maximum_size_percent_chord | 1.0 | — | Entrada/configuración | Ajusta bump maximum size percent chord. |
| boundary_layer.leading_edge_curvature_fraction | 0.2 | — | Entrada/configuración | Ajusta leading edge curvature fraction. |
| boundary_layer.te_transition_extension_chord | 0.0 | — | Entrada/configuración | Ajusta te transition extension chord. |
| boundary_layer.te_segment_early_start_enabled | false | — | Entrada/configuración | Activa o desactiva te segment early start enabled. |
| boundary_layer.te_segment_start_x_over_c | 0.98 | — | Entrada/configuración | Ajusta te segment start x over c. |
| boundary_layer.leading_segment_extension_enabled | false | — | Entrada/configuración | Activa o desactiva leading segment extension enabled. |
| boundary_layer.leading_segment_end_x_over_c | 0.05 | — | Entrada/configuración | Ajusta leading segment end x over c. |
| boundary_layer.te_geometry_points | 35 | — | Entrada/configuración | Ajusta te geometry points. |
| external_volume.domain_radius_chord | 50.0 | — | Entrada/configuración | Ajusta domain radius chord. |
| external_volume.interface_size_mode | tangential_match | — | Entrada/configuración | Ajusta interface size mode. |
| external_volume.interface_size_chord | 0.00035 | — | Entrada/configuración | Ajusta interface size chord. |
| external_volume.interface_tangential_factor | 0.55 | — | Entrada/configuración | Ajusta interface tangential factor. |
| external_volume.farfield_size_chord | 5.0 | — | Entrada/configuración | Ajusta farfield size chord. |
| external_volume.radial_growth_rate | 0.12 | — | Entrada/configuración | Ajusta radial growth rate. |
| external_volume.mesh_algorithm | 6 | — | Entrada/configuración | Ajusta mesh algorithm. |
| external_volume.automatic_extend_enabled | true | — | Entrada/configuración | Activa o desactiva automatic extend enabled. |
| external_volume.extend_distance_max_chord | 35.0 | — | Entrada/configuración | Ajusta extend distance max chord. |
| external_volume.extend_power | 0.35 | — | Entrada/configuración | Ajusta extend power. |
| external_volume.extend_size_max_chord | 5.0 | — | Entrada/configuración | Ajusta extend size max chord. |
| external_volume.extend_interface_guard_enabled | true | — | Entrada/configuración | Activa o desactiva extend interface guard enabled. |
| external_volume.extend_interface_transition_chord | 0.1 | — | Entrada/configuración | Ajusta extend interface transition chord. |
| execution.gmsh_threads | 12 | — | Entrada/configuración | Ajusta gmsh threads. |
| execution.openfoam_timeout_s | 300 | s | Entrada/configuración | Ajusta openfoam timeout s. |
| execution.post_generation_optimization | off | — | Entrada/configuración | Ajusta post generation optimization. |
| execution.post_generation_optimization_iterations | 5 | — | Entrada/configuración | Ajusta post generation optimization iterations. |
| execution.mesh_smoothing | 3 | — | Entrada/configuración | Ajusta mesh smoothing. |
| execution.analyse_mesh_quality | false | — | Entrada/configuración | Activa o desactiva analyse mesh quality. |
| execution.max_cells | 2000000 | — | Entrada/configuración | Ajusta max cells. |
| execution.gmsh_timeout_s | 300 | s | Entrada/configuración | Ajusta gmsh timeout s. |
| execution.minimum_cells_warning | 10000 | — | Entrada/configuración | Ajusta minimum cells warning. |
| source_evidence.revision | closed_urans_final_20260831_tangent1800_interface055 | — | Entrada/configuración | Ajusta revision. |
| source_evidence.path | /home/alejm/ramair_cfd/DESIGN_APP/CFD_2D/experimental_meshes/closed_reference_from_scratch/revisions/closed_urans_final_20260831_tangent1800_interface055 | — | Entrada/configuración | Ajusta path. |
| source_evidence.checkMesh_status | OK | — | Entrada/configuración | Ajusta checkmesh status. |
| source_evidence.qualification | historical_geometry_only_requires_regeneration | — | Entrada/configuración | Ajusta qualification. |
| wake_refinement.enabled | false | — | Entrada/configuración | Activa o desactiva enabled. |
| wake_refinement.size_chord | 0.01 | — | Entrada/configuración | Ajusta size chord. |
| wake_refinement.length_chord | 5.0 | — | Entrada/configuración | Ajusta length chord. |
| wake_refinement.half_width_chord | 0.15 | — | Entrada/configuración | Ajusta half width chord. |

## Malla abierta optimizada

| Clave | Valor inicial | Unidad | Naturaleza | Función |
|---|---|---|---|---|
| schema_version | 3 | — | Entrada/configuración | Ajusta schema version. |
| name | Polar_RANS_medium_20260913 | — | Entrada/configuración | Ajusta name. |
| geometry.spanwise_thickness_chord | 0.01 | — | Entrada/configuración | Ajusta spanwise thickness chord. |
| geometry.spanwise_layers | 1 | — | Entrada/configuración | Ajusta spanwise layers. |
| boundary_layer.layers | 75 | — | Entrada/configuración | Ajusta layers. |
| boundary_layer.growth_rate | 1.1 | — | Entrada/configuración | Ajusta growth rate. |
| boundary_layer.distribution_mode | beta_law | — | Entrada/configuración | Ajusta distribution mode. |
| boundary_layer.thickness_safety_factor | 1.2 | — | Entrada/configuración | Ajusta thickness safety factor. |
| boundary_layer.target_y_plus | 0.6666666666666666 | — | Entrada/configuración | Ajusta target y plus. |
| boundary_layer.wall_nodes_target | 1000 | — | Entrada/configuración | Ajusta wall nodes target. |
| boundary_layer.minimum_tangential_size_chord | 0.0003 | — | Entrada/configuración | Ajusta minimum tangential size chord. |
| boundary_layer.maximum_tangential_size_chord | 0.005 | — | Entrada/configuración | Ajusta maximum tangential size chord. |
| boundary_layer.wall_bump_coefficient | 0.45 | — | Entrada/configuración | Ajusta wall bump coefficient. |
| boundary_layer.te_cap_nodes | 40 | — | Entrada/configuración | Ajusta te cap nodes. |
| boundary_layer.inlet_nodes | 140 | — | Entrada/configuración | Ajusta inlet nodes. |
| boundary_layer.inlet_bump_coefficient | 1.0 | — | Entrada/configuración | Ajusta inlet bump coefficient. |
| boundary_layer.lip_fan_elements | 0 | — | Entrada/configuración | Ajusta lip fan elements. |
| boundary_layer.minimum_profile_point_spacing_chord | 1e-06 | — | Entrada/configuración | Ajusta minimum profile point spacing chord. |
| boundary_layer.continue_over_base_inlet | true | — | Entrada/configuración | Activa o desactiva continue over base inlet. |
| boundary_layer.unstructured_lip_length_chord | 0.01 | — | Entrada/configuración | Ajusta unstructured lip length chord. |
| boundary_layer.base_inlet_blend_length_chord | 0.035 | — | Entrada/configuración | Ajusta base inlet blend length chord. |
| boundary_layer.base_inlet_tangent_scale | 1.0 | — | Entrada/configuración | Ajusta base inlet tangent scale. |
| boundary_layer.leading_edge_curvature_fraction | 0.5 | — | Entrada/configuración | Ajusta leading edge curvature fraction. |
| boundary_layer.prefer_exact_base_inlet | true | — | Entrada/configuración | Activa o desactiva prefer exact base inlet. |
| boundary_layer.automatic_bump_matching | true | — | Entrada/configuración | Activa o desactiva automatic bump matching. |
| boundary_layer.manual_four_segment_bump_enabled | false | — | Entrada/configuración | Activa o desactiva manual four segment bump enabled. |
| boundary_layer.tangential_distribution_method | four_bumps | — | Entrada/configuración | Ajusta tangential distribution method. |
| boundary_layer.split_progression_midpoint_x_chord | 0.5 | — | Entrada/configuración | Ajusta split progression midpoint x chord. |
| boundary_layer.segment_divisions.te | 70 | — | Entrada/configuración | Ajusta te. |
| boundary_layer.segment_divisions.upper | 660 | — | Entrada/configuración | Ajusta upper. |
| boundary_layer.segment_divisions.leading_or_inlet | 440 | — | Entrada/configuración | Ajusta leading or inlet. |
| boundary_layer.segment_divisions.lower | 650 | — | Entrada/configuración | Ajusta lower. |
| boundary_layer.manual_bump_coefficients.te | 1.0000363396203997 | — | Entrada/configuración | Ajusta te. |
| boundary_layer.manual_bump_coefficients.upper | 0.7223508027959347 | — | Entrada/configuración | Ajusta upper. |
| boundary_layer.manual_bump_coefficients.leading_or_inlet | 17.80041270068183 | — | Entrada/configuración | Ajusta leading or inlet. |
| boundary_layer.manual_bump_coefficients.lower | 0.7967048927592753 | — | Entrada/configuración | Ajusta lower. |
| boundary_layer.manual_split_progression.split_divisions.upper_leading_or_inlet | 160 | — | Entrada/configuración | Ajusta upper leading or inlet. |
| boundary_layer.manual_split_progression.split_divisions.upper_te | 160 | — | Entrada/configuración | Ajusta upper te. |
| boundary_layer.manual_split_progression.split_divisions.lower_leading_or_inlet | 160 | — | Entrada/configuración | Ajusta lower leading or inlet. |
| boundary_layer.manual_split_progression.split_divisions.lower_te | 160 | — | Entrada/configuración | Ajusta lower te. |
| boundary_layer.manual_split_progression.progression_coefficients.upper_leading_or_inlet | 1.02 | — | Entrada/configuración | Ajusta upper leading or inlet. |
| boundary_layer.manual_split_progression.progression_coefficients.upper_te | 1.02 | — | Entrada/configuración | Ajusta upper te. |
| boundary_layer.manual_split_progression.progression_coefficients.lower_leading_or_inlet | 1.02 | — | Entrada/configuración | Ajusta lower leading or inlet. |
| boundary_layer.manual_split_progression.progression_coefficients.lower_te | 1.02 | — | Entrada/configuración | Ajusta lower te. |
| boundary_layer.bump_maximum_growth_ratio | 1.1 | — | Entrada/configuración | Ajusta bump maximum growth ratio. |
| boundary_layer.bump_maximum_size_percent_chord | 0.3 | — | Entrada/configuración | Ajusta bump maximum size percent chord. |
| boundary_layer.te_segment_early_start_enabled | true | — | Entrada/configuración | Activa o desactiva te segment early start enabled. |
| boundary_layer.te_segment_start_x_over_c | 0.96 | — | Entrada/configuración | Ajusta te segment start x over c. |
| boundary_layer.leading_segment_extension_enabled | true | — | Entrada/configuración | Activa o desactiva leading segment extension enabled. |
| boundary_layer.leading_segment_end_x_over_c | 0.12 | — | Entrada/configuración | Ajusta leading segment end x over c. |
| boundary_layer.te_geometry_points | 35 | — | Entrada/configuración | Ajusta te geometry points. |
| external_volume.domain_radius_chord | 50.0 | — | Entrada/configuración | Ajusta domain radius chord. |
| external_volume.interface_size_mode | sampled_front | — | Entrada/configuración | Ajusta interface size mode. |
| external_volume.interface_size_chord | 1e-05 | — | Entrada/configuración | Ajusta interface size chord. |
| external_volume.interface_tangential_factor | 0.02 | — | Entrada/configuración | Ajusta interface tangential factor. |
| external_volume.farfield_size_chord | 5.0 | — | Entrada/configuración | Ajusta farfield size chord. |
| external_volume.radial_growth_rate | 0.11 | — | Entrada/configuración | Ajusta radial growth rate. |
| external_volume.mesh_algorithm | 6 | — | Entrada/configuración | Ajusta mesh algorithm. |
| external_volume.automatic_extend_enabled | false | — | Entrada/configuración | Activa o desactiva automatic extend enabled. |
| external_volume.extend_distance_max_chord | 50.0 | — | Entrada/configuración | Ajusta extend distance max chord. |
| external_volume.extend_power | 2.0 | — | Entrada/configuración | Ajusta extend power. |
| external_volume.extend_size_max_chord | 5.0 | — | Entrada/configuración | Ajusta extend size max chord. |
| external_volume.extend_interface_guard_enabled | false | — | Entrada/configuración | Activa o desactiva extend interface guard enabled. |
| external_volume.extend_interface_transition_chord | 0.25 | — | Entrada/configuración | Ajusta extend interface transition chord. |
| external_volume.front_tangential_factor | 0.85 | — | Entrada/configuración | Ajusta front tangential factor. |
| external_volume.front_normal_size_cap_factor | 0.0 | — | Entrada/configuración | Ajusta front normal size cap factor. |
| internal_volume.inlet_size_factor | 0.45 | — | Entrada/configuración | Ajusta inlet size factor. |
| internal_volume.core_size_chord | 0.01 | — | Entrada/configuración | Ajusta core size chord. |
| internal_volume.inlet_fine_distance_chord | 0.0005 | — | Entrada/configuración | Ajusta inlet fine distance chord. |
| internal_volume.transition_distance_chord | 0.065 | — | Entrada/configuración | Ajusta transition distance chord. |
| internal_volume.inner_wall_size_chord | 0.00175 | — | Entrada/configuración | Ajusta inner wall size chord. |
| internal_volume.inner_wall_transition_distance_chord | 0.05 | — | Entrada/configuración | Ajusta inner wall transition distance chord. |
| internal_volume.te_internal_size_chord | 0.00065 | — | Entrada/configuración | Ajusta te internal size chord. |
| internal_volume.te_internal_transition_distance_chord | 0.0075 | — | Entrada/configuración | Ajusta te internal transition distance chord. |
| internal_volume.automatic_extend_enabled | false | — | Entrada/configuración | Activa o desactiva automatic extend enabled. |
| internal_volume.extend_distance_max_chord | 0.1 | — | Entrada/configuración | Ajusta extend distance max chord. |
| internal_volume.extend_power | 2.5 | — | Entrada/configuración | Ajusta extend power. |
| internal_volume.extend_size_max_chord | 0.015 | — | Entrada/configuración | Ajusta extend size max chord. |
| execution.gmsh_threads | 4 | — | Entrada/configuración | Ajusta gmsh threads. |
| execution.openfoam_timeout_s | 300 | s | Entrada/configuración | Ajusta openfoam timeout s. |
| execution.post_generation_optimization | laplace2d_then_relocate2d | — | Entrada/configuración | Ajusta post generation optimization. |
| execution.post_generation_optimization_iterations | 5 | — | Entrada/configuración | Ajusta post generation optimization iterations. |
| execution.mesh_smoothing | 10 | — | Entrada/configuración | Ajusta mesh smoothing. |
| execution.analyse_mesh_quality | false | — | Entrada/configuración | Activa o desactiva analyse mesh quality. |
| execution.run_check_mesh | true | — | Entrada/configuración | Activa o desactiva run check mesh. |
| execution.max_cells | 2000000 | — | Entrada/configuración | Ajusta max cells. |
| execution.gmsh_timeout_s | 600 | s | Entrada/configuración | Ajusta gmsh timeout s. |
| execution.minimum_cells_warning | 10000 | — | Entrada/configuración | Ajusta minimum cells warning. |
| topology | open | — | Entrada/configuración | Ajusta topology. |
| wake_refinement.enabled | false | — | Entrada/configuración | Activa o desactiva enabled. |
| wake_refinement.size_chord | 0.01 | — | Entrada/configuración | Ajusta size chord. |
| wake_refinement.length_chord | 5.0 | — | Entrada/configuración | Ajusta length chord. |
| wake_refinement.half_width_chord | 0.15 | — | Entrada/configuración | Ajusta half width chord. |

## Solver cerrado

| Clave | Valor inicial | Unidad | Naturaleza | Función |
|---|---|---|---|---|
| config_schema_version | 16 | — | Entrada/configuración | Ajusta config schema version. |
| preset_id | LS1_0417_validation_RANS_ABCDE_URANS_v1 | — | Entrada/configuración | Ajusta preset id. |
| solver | foamRun | — | Entrada/configuración | OpenFOAM Foundation 14 usa foamRun con el módulo incompressibleFluid. SIMPLE/PIMPLE son algoritmos controlados por los diccionarios. |
| solver_module | incompressibleFluid | — | Entrada/configuración | incompressibleFluid resuelve flujo incompresible en foamRun; no es una instalación separada. |
| velocity_source | mach | — | Entrada/configuración | Ajusta velocity source. |
| transient | true | — | Entrada/configuración | Activa o desactiva transient. |
| time_step_mode | adaptive_physics_limited | — | Entrada/configuración | Ajusta time step mode. |
| ddt_scheme | backward | — | Entrada/configuración | Ajusta ddt scheme. |
| n_outer_correctors | 5 | — | Entrada/configuración | Máximo de iteraciones PIMPLE por paso; los residuales pueden terminar el bucle antes. |
| n_correctors | 2 | — | Entrada/configuración | Correcciones presión-velocidad dentro de cada iteración externa. |
| transient_velocity_divergence_scheme | Gauss linearUpwind limited | — | Entrada/configuración | Ajusta transient velocity divergence scheme. |
| transient_turbulence_divergence_scheme | Gauss linearUpwind limited | — | Entrada/configuración | Ajusta transient turbulence divergence scheme. |
| deltaT_star | 0.0025 | — | Entrada/configuración | Paso adimensional: Δt* = Δt U/c. El control de Courant puede reducirlo. |
| maxDeltaT_star | 0.0025 | — | Entrada/configuración | Límite superior de paso físico adimensional. |
| maxCo | 50.0 | — | Entrada/configuración | Limita el paso por advección local. Los labios abiertos requieren especial atención. |
| outer_corrector_residual_control.enabled | true | — | Entrada/configuración | Activa o desactiva enabled. |
| outer_corrector_residual_control.fields.p.tolerance | 0.0001 | — | Entrada/configuración | Ajusta tolerance. |
| outer_corrector_residual_control.fields.p.relTol | 0.0 | — | Entrada/configuración | Ajusta reltol. |
| outer_corrector_residual_control.fields.U.tolerance | 0.0001 | — | Entrada/configuración | Ajusta tolerance. |
| outer_corrector_residual_control.fields.U.relTol | 0.0 | — | Entrada/configuración | Ajusta reltol. |
| outer_corrector_residual_control.fields.nuTilda.tolerance | 0.0001 | — | Entrada/configuración | Ajusta tolerance. |
| outer_corrector_residual_control.fields.nuTilda.relTol | 0.0 | — | Entrada/configuración | Ajusta reltol. |
| transport_correction_final | false | — | Entrada/configuración | Activa o desactiva transport correction final. |
| endTime_star | 64.0 | — | Entrada/configuración | Ajusta endtime star. |
| field_write_interval_star | 0.1 | — | Entrada/configuración | Ajusta field write interval star. |
| field_write_control | adjustableRunTime | — | Entrada/configuración | Ajusta field write control. |
| average_from_fraction | 0.21875 | — | Entrada/configuración | Ajusta average from fraction. |
| temporal_accuracy.profile_id | closed_external_frequency_budget_v1 | — | Entrada/configuración | Ajusta profile id. |
| temporal_accuracy.target_min_strouhal | 0.05 | — | Entrada/configuración | Ajusta target min strouhal. |
| temporal_accuracy.target_max_strouhal | 20.0 | — | Entrada/configuración | Ajusta target max strouhal. |
| temporal_accuracy.target_samples_per_cycle | 20 | — | Entrada/configuración | Ajusta target samples per cycle. |
| temporal_accuracy.minimum_cycles_for_statistics | 10 | — | Entrada/configuración | Ajusta minimum cycles for statistics. |
| temporal_accuracy.smoke_end_time_star | 20.0 | — | Entrada/configuración | Ajusta smoke end time star. |
| temporal_accuracy.reference_deltaT_star | 0.01276096076 | — | Entrada/configuración | Ajusta reference deltat star. |
| temporal_accuracy.reference_end_time_star | 319.0240189 | — | Entrada/configuración | Ajusta reference end time star. |
| temporal_accuracy.reference_average_time_star | 127.60960756 | — | Entrada/configuración | Ajusta reference average time star. |
| temporal_accuracy.time_step_study_values_star | [0.01276096076, 0.00638048038, 0.00319024019, 0.001595120095, 0.0007975600475, 0.00039878002375] | — | Entrada/configuración | Ajusta time step study values star. |
| temporal_accuracy.strouhal_scope | Conservative screening envelope for coherent wake, separated-flow and shear-layer content. St_max=20 comes from the Cummings et al. high-frequency shear-layer example and must be replaced by measured spectra when available. | — | Entrada/configuración | Ajusta strouhal scope. |
| temporal_accuracy.scope_note | The 20-samples-per-cycle ceiling is a project engineering target, not a quoted universal rule. The Ghoreyshi validation deltaT is retained as a reproduction reference and must be followed by sequential halving. | — | Entrada/configuración | Ajusta scope note. |
| purgeWrite | 24 | — | Entrada/configuración | 0 conserva todos; valores positivos limitan carpetas temporales. Preserva historial suficiente para reiniciar backward. |
| farfield_boundary_condition | freestream | — | Entrada/configuración | Ajusta farfield boundary condition. |
| steady_initialization_enabled | true | — | Entrada/configuración | Activa o desactiva steady initialization enabled. |
| steady_max_iterations | 15000 | — | Entrada/configuración | Límite absoluto de iteración del arranque o continuación estacionaria. |
| steady_write_interval_iterations | 1000 | — | Entrada/configuración | Ajusta steady write interval iterations. |
| steady_residual_control.p | 1e-06 | — | Entrada/configuración | Ajusta p. |
| steady_residual_control.U | 1e-06 | — | Entrada/configuración | Ajusta u. |
| steady_residual_control.nuTilda | 1e-06 | — | Entrada/configuración | Ajusta nutilda. |
| steady_numerics.p_relaxation | 0.3 | — | Entrada/configuración | Ajusta p relaxation. |
| steady_numerics.U_relaxation | 0.7 | — | Entrada/configuración | Ajusta u relaxation. |
| steady_numerics.nuTilda_relaxation | 0.7 | — | Entrada/configuración | Ajusta nutilda relaxation. |
| steady_numerics.velocity_divergence_scheme | bounded Gauss linearUpwind limited | — | Entrada/configuración | Ajusta velocity divergence scheme. |
| steady_numerics.turbulence_divergence_scheme | bounded Gauss upwind | — | Entrada/configuración | Ajusta turbulence divergence scheme. |
| velocity_profile_stations_xc | [0.1, 0.3, 0.6, 0.9] | — | Entrada/configuración | Ajusta velocity profile stations xc. |
| velocity_profile_sample_points | 40 | — | Entrada/configuración | Ajusta velocity profile sample points. |
| turbulence_model | SpalartAllmaras | — | Entrada/configuración | Ajusta turbulence model. |
| optional_secondary_model | kOmegaSST | — | Entrada/configuración | Ajusta optional secondary model. |
| validation_study.enabled | false | — | Entrada/configuración | Activa o desactiva enabled. |
| validation_study.study_id |  | — | Entrada/configuración | Ajusta study id. |
| validation_study.alpha_deg | 8.0 | ° | Entrada/configuración | Ajusta alpha deg. |
| validation_study.time_policy | fixed_staged | — | Entrada/configuración | Ajusta time policy. |
| validation_study.startup_scheme | Euler | — | Entrada/configuración | Ajusta startup scheme. |
| validation_study.production_scheme | backward | — | Entrada/configuración | Ajusta production scheme. |
| validation_study.sensitivity_scheme | CrankNicolson | — | Entrada/configuración | Ajusta sensitivity scheme. |
| validation_study.crank_nicolson_psi | 0.9 | — | Entrada/configuración | Ajusta crank nicolson psi. |
| validation_study.dt_target_s | null | s | Entrada/configuración | Ajusta dt target s. |
| validation_study.startup_factors | [0.25, 0.5, 1.0] | — | Entrada/configuración | Ajusta startup factors. |
| validation_study.startup_duration_tc | [1.0, 1.0, 2.0] | — | Entrada/configuración | Ajusta startup duration tc. |
| validation_study.settling_tc | null | — | Entrada/configuración | Ajusta settling tc. |
| validation_study.sampling_tc | null | — | Entrada/configuración | Ajusta sampling tc. |
| validation_study.nOuterCorrectors | 3 | — | Entrada/configuración | Ajusta noutercorrectors. |
| validation_study.nCorrectors | 2 | — | Entrada/configuración | Ajusta ncorrectors. |
| validation_study.nNonOrthogonalCorrectors | 0 | — | Entrada/configuración | Ajusta nnonorthogonalcorrectors. |
| validation_study.courant_controls_dt | false | — | Entrada/configuración | Activa o desactiva courant controls dt. |
| validation_study.field_write_interval_tc | 1.0 | — | Entrada/configuración | Ajusta field write interval tc. |
| validation_study.retained_snapshots | 24 | — | Entrada/configuración | Ajusta retained snapshots. |
| validation_study.mpi_ranks | 8 | — | Entrada/configuración | Ajusta mpi ranks. |
| validation_study.timeout_hours | 24.0 | — | Entrada/configuración | Ajusta timeout hours. |
| validation_study.steady_checkpoint_timeout_min | 120.0 | — | Entrada/configuración | Ajusta steady checkpoint timeout min. |
| screening_note | Topology-aware laptop preset. SIMPLE uses nNonOrthogonalCorrectors=0 for both checked meshes, matching the OpenFOAM 14 steady-state recommendation. Closed external airfoils use one PIMPLE outer loop, maxCo=1 and second-order backward/limited spatial transport. Open cavities use two outer loops, maxCo=0.7, a smaller adaptive deltaT ceiling and more bounded transport. Both allow up to 10000 SIMPLE iterations but can transition earlier on residual and force-history criteria. | — | Entrada/configuración | Ajusta screening note. |
| single_solver_contract | true | — | Entrada/configuración | Activa o desactiva single solver contract. |
| steady_native_residual_control_enabled | false | — | Entrada/configuración | Activa o desactiva steady native residual control enabled. |
| transient_relaxation.p | 0.3 | — | Entrada/configuración | Ajusta p. |
| transient_relaxation.U | 0.9 | — | Entrada/configuración | Ajusta u. |
| transient_relaxation.nuTilda | 0.7 | — | Entrada/configuración | Ajusta nutilda. |
| validation_rans_convergence.window_samples | 1000 | — | Entrada/configuración | Ajusta window samples. |
| validation_rans_convergence.minimum_samples | 500 | — | Entrada/configuración | Ajusta minimum samples. |
| validation_rans_convergence.residual_tolerance | 1e-06 | — | Entrada/configuración | Ajusta residual tolerance. |
| validation_rans_convergence.mean_change_tolerance_percent | 0.5 | — | Entrada/configuración | Ajusta mean change tolerance percent. |
| validation_rans_convergence.fluctuation_tolerance_percent | 1.0 | — | Entrada/configuración | Ajusta fluctuation tolerance percent. |
| validation_rans_convergence.required_consecutive_windows | 3 | — | Entrada/configuración | Ajusta required consecutive windows. |
| validation_write_strategy.rans_full_field_interval_iterations | 1000 | — | Entrada/configuración | Ajusta rans full field interval iterations. |
| validation_write_strategy.urans_control | adjustableRunTime | — | Entrada/configuración | Ajusta urans control. |
| validation_write_strategy.urans_interval_time_star | 0.1 | — | Entrada/configuración | Ajusta urans interval time star. |
| validation_write_strategy.purge_write | 24 | — | Entrada/configuración | Ajusta purge write. |
| validation_write_strategy.authoritative | true | — | Entrada/configuración | Activa o desactiva authoritative. |
| mesh_quality_numerics_mode | automatic | — | Entrada/configuración | Ajusta mesh quality numerics mode. |
| validation_phase_d_steady_equivalence.enabled | false | — | Entrada/configuración | Activa o desactiva enabled. |
| validation_phase_d_steady_equivalence.window_time_star | 2.5 | — | Entrada/configuración | Ajusta window time star. |
| validation_phase_d_steady_equivalence.minimum_samples | 200 | — | Entrada/configuración | Ajusta minimum samples. |
| validation_phase_d_steady_equivalence.mean_difference_tolerance_percent | 0.3 | — | Entrada/configuración | Ajusta mean difference tolerance percent. |
| validation_phase_d_steady_equivalence.fluctuation_tolerance_percent | 0.5 | — | Entrada/configuración | Ajusta fluctuation tolerance percent. |
| validation_phase_d_steady_equivalence.coefficient_floors.Cl | 0.05 | — | Entrada/configuración | Ajusta cl. |
| validation_phase_d_steady_equivalence.coefficient_floors.Cd | 0.005 | — | Entrada/configuración | Ajusta cd. |
| validation_phase_d_steady_equivalence.coefficient_floors.Cm | 0.005 | — | Entrada/configuración | Ajusta cm. |
| validation_phase_d_steady_equivalence.coefficient_floors.Cl_over_Cd | 1.0 | — | Entrada/configuración | Ajusta cl over cd. |
| validation_phase_d_steady_equivalence.policy | skip_E_and_use_final_D_window_only_when_all_coefficients_pass | — | Entrada/configuración | Ajusta policy. |
| workspace_solver_contract | true | — | Entrada/configuración | Activa o desactiva workspace solver contract. |
| execution_controls.phase_action | rans_only | — | Entrada/configuración | Ajusta phase action. |
| execution_controls.renumber_before_decompose | false | — | Entrada/configuración | Activa o desactiva renumber before decompose. |

## Solver abierto

| Clave | Valor inicial | Unidad | Naturaleza | Función |
|---|---|---|---|---|
| config_schema_version | 16 | — | Entrada/configuración | Ajusta config schema version. |
| preset_id | LS1_0417_validation_RANS_ABCDE_URANS_v1 | — | Entrada/configuración | Ajusta preset id. |
| solver | foamRun | — | Entrada/configuración | OpenFOAM Foundation 14 usa foamRun con el módulo incompressibleFluid. SIMPLE/PIMPLE son algoritmos controlados por los diccionarios. |
| solver_module | incompressibleFluid | — | Entrada/configuración | incompressibleFluid resuelve flujo incompresible en foamRun; no es una instalación separada. |
| velocity_source | mach | — | Entrada/configuración | Ajusta velocity source. |
| transient | true | — | Entrada/configuración | Activa o desactiva transient. |
| time_step_mode | adaptive_physics_limited | — | Entrada/configuración | Ajusta time step mode. |
| ddt_scheme | backward | — | Entrada/configuración | Ajusta ddt scheme. |
| n_outer_correctors | 5 | — | Entrada/configuración | Máximo de iteraciones PIMPLE por paso; los residuales pueden terminar el bucle antes. |
| n_correctors | 2 | — | Entrada/configuración | Correcciones presión-velocidad dentro de cada iteración externa. |
| transient_velocity_divergence_scheme | Gauss linearUpwind limited | — | Entrada/configuración | Ajusta transient velocity divergence scheme. |
| transient_turbulence_divergence_scheme | Gauss linearUpwind limited | — | Entrada/configuración | Ajusta transient turbulence divergence scheme. |
| deltaT_star | 0.0025 | — | Entrada/configuración | Paso adimensional: Δt* = Δt U/c. El control de Courant puede reducirlo. |
| maxDeltaT_star | 0.0025 | — | Entrada/configuración | Límite superior de paso físico adimensional. |
| maxCo | 50.0 | — | Entrada/configuración | Limita el paso por advección local. Los labios abiertos requieren especial atención. |
| outer_corrector_residual_control.enabled | true | — | Entrada/configuración | Activa o desactiva enabled. |
| outer_corrector_residual_control.fields.p.tolerance | 0.0001 | — | Entrada/configuración | Ajusta tolerance. |
| outer_corrector_residual_control.fields.p.relTol | 0.0 | — | Entrada/configuración | Ajusta reltol. |
| outer_corrector_residual_control.fields.U.tolerance | 0.0001 | — | Entrada/configuración | Ajusta tolerance. |
| outer_corrector_residual_control.fields.U.relTol | 0.0 | — | Entrada/configuración | Ajusta reltol. |
| outer_corrector_residual_control.fields.nuTilda.tolerance | 0.0001 | — | Entrada/configuración | Ajusta tolerance. |
| outer_corrector_residual_control.fields.nuTilda.relTol | 0.0 | — | Entrada/configuración | Ajusta reltol. |
| transport_correction_final | false | — | Entrada/configuración | Activa o desactiva transport correction final. |
| endTime_star | 64.0 | — | Entrada/configuración | Ajusta endtime star. |
| field_write_interval_star | 0.1 | — | Entrada/configuración | Ajusta field write interval star. |
| field_write_control | adjustableRunTime | — | Entrada/configuración | Ajusta field write control. |
| average_from_fraction | 0.21875 | — | Entrada/configuración | Ajusta average from fraction. |
| temporal_accuracy.profile_id | closed_external_frequency_budget_v1 | — | Entrada/configuración | Ajusta profile id. |
| temporal_accuracy.target_min_strouhal | 0.05 | — | Entrada/configuración | Ajusta target min strouhal. |
| temporal_accuracy.target_max_strouhal | 20.0 | — | Entrada/configuración | Ajusta target max strouhal. |
| temporal_accuracy.target_samples_per_cycle | 20 | — | Entrada/configuración | Ajusta target samples per cycle. |
| temporal_accuracy.minimum_cycles_for_statistics | 10 | — | Entrada/configuración | Ajusta minimum cycles for statistics. |
| temporal_accuracy.smoke_end_time_star | 20.0 | — | Entrada/configuración | Ajusta smoke end time star. |
| temporal_accuracy.reference_deltaT_star | 0.01276096076 | — | Entrada/configuración | Ajusta reference deltat star. |
| temporal_accuracy.reference_end_time_star | 319.0240189 | — | Entrada/configuración | Ajusta reference end time star. |
| temporal_accuracy.reference_average_time_star | 127.60960756 | — | Entrada/configuración | Ajusta reference average time star. |
| temporal_accuracy.time_step_study_values_star | [0.01276096076, 0.00638048038, 0.00319024019, 0.001595120095, 0.0007975600475, 0.00039878002375] | — | Entrada/configuración | Ajusta time step study values star. |
| temporal_accuracy.strouhal_scope | Conservative screening envelope for coherent wake, separated-flow and shear-layer content. St_max=20 comes from the Cummings et al. high-frequency shear-layer example and must be replaced by measured spectra when available. | — | Entrada/configuración | Ajusta strouhal scope. |
| temporal_accuracy.scope_note | The 20-samples-per-cycle ceiling is a project engineering target, not a quoted universal rule. The Ghoreyshi validation deltaT is retained as a reproduction reference and must be followed by sequential halving. | — | Entrada/configuración | Ajusta scope note. |
| purgeWrite | 24 | — | Entrada/configuración | 0 conserva todos; valores positivos limitan carpetas temporales. Preserva historial suficiente para reiniciar backward. |
| farfield_boundary_condition | freestream | — | Entrada/configuración | Ajusta farfield boundary condition. |
| steady_initialization_enabled | true | — | Entrada/configuración | Activa o desactiva steady initialization enabled. |
| steady_max_iterations | 20000 | — | Entrada/configuración | Límite absoluto de iteración del arranque o continuación estacionaria. |
| steady_write_interval_iterations | 1000 | — | Entrada/configuración | Ajusta steady write interval iterations. |
| steady_residual_control.p | 1e-06 | — | Entrada/configuración | Ajusta p. |
| steady_residual_control.U | 1e-06 | — | Entrada/configuración | Ajusta u. |
| steady_residual_control.nuTilda | 1e-06 | — | Entrada/configuración | Ajusta nutilda. |
| steady_numerics.p_relaxation | 0.3 | — | Entrada/configuración | Ajusta p relaxation. |
| steady_numerics.U_relaxation | 0.7 | — | Entrada/configuración | Ajusta u relaxation. |
| steady_numerics.nuTilda_relaxation | 0.7 | — | Entrada/configuración | Ajusta nutilda relaxation. |
| steady_numerics.velocity_divergence_scheme | bounded Gauss linearUpwind limited | — | Entrada/configuración | Ajusta velocity divergence scheme. |
| steady_numerics.turbulence_divergence_scheme | bounded Gauss upwind | — | Entrada/configuración | Ajusta turbulence divergence scheme. |
| velocity_profile_stations_xc | [0.1, 0.3, 0.6, 0.9] | — | Entrada/configuración | Ajusta velocity profile stations xc. |
| velocity_profile_sample_points | 40 | — | Entrada/configuración | Ajusta velocity profile sample points. |
| turbulence_model | SpalartAllmaras | — | Entrada/configuración | Ajusta turbulence model. |
| optional_secondary_model | kOmegaSST | — | Entrada/configuración | Ajusta optional secondary model. |
| validation_study.enabled | false | — | Entrada/configuración | Activa o desactiva enabled. |
| validation_study.study_id |  | — | Entrada/configuración | Ajusta study id. |
| validation_study.alpha_deg | 8.0 | ° | Entrada/configuración | Ajusta alpha deg. |
| validation_study.time_policy | fixed_staged | — | Entrada/configuración | Ajusta time policy. |
| validation_study.startup_scheme | Euler | — | Entrada/configuración | Ajusta startup scheme. |
| validation_study.production_scheme | backward | — | Entrada/configuración | Ajusta production scheme. |
| validation_study.sensitivity_scheme | CrankNicolson | — | Entrada/configuración | Ajusta sensitivity scheme. |
| validation_study.crank_nicolson_psi | 0.9 | — | Entrada/configuración | Ajusta crank nicolson psi. |
| validation_study.dt_target_s | null | s | Entrada/configuración | Ajusta dt target s. |
| validation_study.startup_factors | [0.25, 0.5, 1.0] | — | Entrada/configuración | Ajusta startup factors. |
| validation_study.startup_duration_tc | [1.0, 1.0, 2.0] | — | Entrada/configuración | Ajusta startup duration tc. |
| validation_study.settling_tc | null | — | Entrada/configuración | Ajusta settling tc. |
| validation_study.sampling_tc | null | — | Entrada/configuración | Ajusta sampling tc. |
| validation_study.nOuterCorrectors | 3 | — | Entrada/configuración | Ajusta noutercorrectors. |
| validation_study.nCorrectors | 2 | — | Entrada/configuración | Ajusta ncorrectors. |
| validation_study.nNonOrthogonalCorrectors | 0 | — | Entrada/configuración | Ajusta nnonorthogonalcorrectors. |
| validation_study.courant_controls_dt | false | — | Entrada/configuración | Activa o desactiva courant controls dt. |
| validation_study.field_write_interval_tc | 1.0 | — | Entrada/configuración | Ajusta field write interval tc. |
| validation_study.retained_snapshots | 24 | — | Entrada/configuración | Ajusta retained snapshots. |
| validation_study.mpi_ranks | 8 | — | Entrada/configuración | Ajusta mpi ranks. |
| validation_study.timeout_hours | 24.0 | — | Entrada/configuración | Ajusta timeout hours. |
| validation_study.steady_checkpoint_timeout_min | 120.0 | — | Entrada/configuración | Ajusta steady checkpoint timeout min. |
| screening_note | Topology-aware laptop preset. SIMPLE uses nNonOrthogonalCorrectors=0 for both checked meshes, matching the OpenFOAM 14 steady-state recommendation. Closed external airfoils use one PIMPLE outer loop, maxCo=1 and second-order backward/limited spatial transport. Open cavities use two outer loops, maxCo=0.7, a smaller adaptive deltaT ceiling and more bounded transport. Both allow up to 10000 SIMPLE iterations but can transition earlier on residual and force-history criteria. | — | Entrada/configuración | Ajusta screening note. |
| single_solver_contract | true | — | Entrada/configuración | Activa o desactiva single solver contract. |
| steady_native_residual_control_enabled | false | — | Entrada/configuración | Activa o desactiva steady native residual control enabled. |
| transient_relaxation.p | 0.3 | — | Entrada/configuración | Ajusta p. |
| transient_relaxation.U | 0.9 | — | Entrada/configuración | Ajusta u. |
| transient_relaxation.nuTilda | 0.7 | — | Entrada/configuración | Ajusta nutilda. |
| validation_rans_convergence.window_samples | 1000 | — | Entrada/configuración | Ajusta window samples. |
| validation_rans_convergence.minimum_samples | 500 | — | Entrada/configuración | Ajusta minimum samples. |
| validation_rans_convergence.residual_tolerance | 1e-06 | — | Entrada/configuración | Ajusta residual tolerance. |
| validation_rans_convergence.mean_change_tolerance_percent | 0.5 | — | Entrada/configuración | Ajusta mean change tolerance percent. |
| validation_rans_convergence.fluctuation_tolerance_percent | 1.0 | — | Entrada/configuración | Ajusta fluctuation tolerance percent. |
| validation_rans_convergence.required_consecutive_windows | 3 | — | Entrada/configuración | Ajusta required consecutive windows. |
| validation_write_strategy.rans_full_field_interval_iterations | 1000 | — | Entrada/configuración | Ajusta rans full field interval iterations. |
| validation_write_strategy.urans_control | adjustableRunTime | — | Entrada/configuración | Ajusta urans control. |
| validation_write_strategy.urans_interval_time_star | 0.1 | — | Entrada/configuración | Ajusta urans interval time star. |
| validation_write_strategy.purge_write | 24 | — | Entrada/configuración | Ajusta purge write. |
| validation_write_strategy.authoritative | true | — | Entrada/configuración | Activa o desactiva authoritative. |
| mesh_quality_numerics_mode | automatic | — | Entrada/configuración | Ajusta mesh quality numerics mode. |
| validation_phase_d_steady_equivalence.enabled | false | — | Entrada/configuración | Activa o desactiva enabled. |
| validation_phase_d_steady_equivalence.window_time_star | 2.5 | — | Entrada/configuración | Ajusta window time star. |
| validation_phase_d_steady_equivalence.minimum_samples | 200 | — | Entrada/configuración | Ajusta minimum samples. |
| validation_phase_d_steady_equivalence.mean_difference_tolerance_percent | 0.3 | — | Entrada/configuración | Ajusta mean difference tolerance percent. |
| validation_phase_d_steady_equivalence.fluctuation_tolerance_percent | 0.5 | — | Entrada/configuración | Ajusta fluctuation tolerance percent. |
| validation_phase_d_steady_equivalence.coefficient_floors.Cl | 0.05 | — | Entrada/configuración | Ajusta cl. |
| validation_phase_d_steady_equivalence.coefficient_floors.Cd | 0.005 | — | Entrada/configuración | Ajusta cd. |
| validation_phase_d_steady_equivalence.coefficient_floors.Cm | 0.005 | — | Entrada/configuración | Ajusta cm. |
| validation_phase_d_steady_equivalence.coefficient_floors.Cl_over_Cd | 1.0 | — | Entrada/configuración | Ajusta cl over cd. |
| validation_phase_d_steady_equivalence.policy | skip_E_and_use_final_D_window_only_when_all_coefficients_pass | — | Entrada/configuración | Ajusta policy. |
| workspace_solver_contract | true | — | Entrada/configuración | Activa o desactiva workspace solver contract. |
| execution_controls.phase_action | rans_only | — | Entrada/configuración | Ajusta phase action. |
| execution_controls.renumber_before_decompose | false | — | Entrada/configuración | Activa o desactiva renumber before decompose. |
| execution_controls.steady_additional_iterations | 1000 | — | Entrada/configuración | Ajusta steady additional iterations. |
| execution_controls.steady_timeout_min | 1440.0 | — | Entrada/configuración | Ajusta steady timeout min. |
| execution_controls.timeout_min | 1440.0 | — | Entrada/configuración | Ajusta timeout min. |

## Fases cerradas

| Clave | Valor inicial | Unidad | Naturaleza | Función |
|---|---|---|---|---|
| schema_version | 1 | — | Entrada/configuración | Ajusta schema version. |
| target_deltaT_star | 0.0025 | — | Entrada/configuración | Ajusta target deltat star. |
| adjust_time_step | true | — | Entrada/configuración | Activa o desactiva adjust time step. |
| maxCo | 50.0 | — | Entrada/configuración | Limita el paso por advección local. Los labios abiertos requieren especial atención. |
| max_outer_correctors | 5 | — | Entrada/configuración | Ajusta max outer correctors. |
| outer_residual_control_enabled | true | — | Entrada/configuración | Activa o desactiva outer residual control enabled. |
| stages | [{"stage": "A", "scheme": "Euler", "dt_factor": 0.25, "duration_time_star": 1.0, "sampling": false}, {"stage": "B", "scheme": "Euler", "dt_factor": 0.5, "duration_time_star": 1.0, "sampling": false}, {"stage": "C", "scheme": "Euler", "dt_factor": 1.0, "duration_time_star": 2.0, "sampling": false}, {"stage": "D", "scheme": "backward", "dt_factor": 1.0, "duration_time_star": 10.0, "sampling": false}, {"stage": "E", "scheme": "backward", "dt_factor": 1.0, "duration_time_star": 50.0, "sampling": true}] | — | Entrada/configuración | Ajusta stages. |
| production_stage | E | — | Entrada/configuración | Ajusta production stage. |
| production_start_time_star | 14.0 | — | Entrada/configuración | Ajusta production start time star. |
| total_time_star | 64.0 | — | Entrada/configuración | Ajusta total time star. |
| average_from_fraction | 0.21875 | — | Entrada/configuración | Ajusta average from fraction. |
| phase_d_steady_equivalence.enabled | false | — | Entrada/configuración | Activa o desactiva enabled. |
| phase_d_steady_equivalence.window_time_star | 2.5 | — | Entrada/configuración | Ajusta window time star. |
| phase_d_steady_equivalence.minimum_samples | 200 | — | Entrada/configuración | Ajusta minimum samples. |
| phase_d_steady_equivalence.mean_difference_tolerance_percent | 0.3 | — | Entrada/configuración | Ajusta mean difference tolerance percent. |
| phase_d_steady_equivalence.fluctuation_tolerance_percent | 0.5 | — | Entrada/configuración | Ajusta fluctuation tolerance percent. |

## Fases abiertas

| Clave | Valor inicial | Unidad | Naturaleza | Función |
|---|---|---|---|---|
| schema_version | 1 | — | Entrada/configuración | Ajusta schema version. |
| target_deltaT_star | 0.0025 | — | Entrada/configuración | Ajusta target deltat star. |
| adjust_time_step | true | — | Entrada/configuración | Activa o desactiva adjust time step. |
| maxCo | 50.0 | — | Entrada/configuración | Limita el paso por advección local. Los labios abiertos requieren especial atención. |
| max_outer_correctors | 5 | — | Entrada/configuración | Ajusta max outer correctors. |
| outer_residual_control_enabled | true | — | Entrada/configuración | Activa o desactiva outer residual control enabled. |
| stages | [{"stage": "A", "scheme": "Euler", "dt_factor": 0.25, "duration_time_star": 1.0, "sampling": false}, {"stage": "B", "scheme": "Euler", "dt_factor": 0.5, "duration_time_star": 1.0, "sampling": false}, {"stage": "C", "scheme": "Euler", "dt_factor": 1.0, "duration_time_star": 2.0, "sampling": false}, {"stage": "D", "scheme": "backward", "dt_factor": 1.0, "duration_time_star": 10.0, "sampling": false}, {"stage": "E", "scheme": "backward", "dt_factor": 1.0, "duration_time_star": 50.0, "sampling": true}] | — | Entrada/configuración | Ajusta stages. |
| production_stage | E | — | Entrada/configuración | Ajusta production stage. |
| production_start_time_star | 14.0 | — | Entrada/configuración | Ajusta production start time star. |
| total_time_star | 64.0 | — | Entrada/configuración | Ajusta total time star. |
| average_from_fraction | 0.21875 | — | Entrada/configuración | Ajusta average from fraction. |
| phase_d_steady_equivalence.enabled | false | — | Entrada/configuración | Activa o desactiva enabled. |
| phase_d_steady_equivalence.window_time_star | 2.5 | — | Entrada/configuración | Ajusta window time star. |
| phase_d_steady_equivalence.minimum_samples | 200 | — | Entrada/configuración | Ajusta minimum samples. |
| phase_d_steady_equivalence.mean_difference_tolerance_percent | 0.3 | — | Entrada/configuración | Ajusta mean difference tolerance percent. |
| phase_d_steady_equivalence.fluctuation_tolerance_percent | 0.5 | — | Entrada/configuración | Ajusta fluctuation tolerance percent. |

## Postproceso

| Clave | Valor inicial | Unidad | Naturaleza | Función |
|---|---|---|---|---|
| run_openfoam_postprocess | true | — | Entrada/configuración | Activa o desactiva run openfoam postprocess. |
| automatic_paraview_products | true | — | Entrada/configuración | Activa o desactiva automatic paraview products. |
| include_rans_stage | true | — | Entrada/configuración | Activa o desactiva include rans stage. |
| wall_profile_analysis | true | — | Entrada/configuración | Activa o desactiva wall profile analysis. |
| velocity_profile_stations | [0.05, 0.1, 0.3, 0.6, 0.9] | — | Entrada/configuración | Ajusta velocity profile stations. |
| velocity_profile_sample_points | 80 | — | Entrada/configuración | Ajusta velocity profile sample points. |
| rans_average_tail_samples | 500 | — | Entrada/configuración | Ajusta rans average tail samples. |
| rans_average_start_iteration | null | — | Entrada/configuración | Ajusta rans average start iteration. |
| include_paraview_animations | false | — | Entrada/configuración | Activa o desactiva include paraview animations. |
| paraview_maximum_frames | 24 | — | Entrada/configuración | Ajusta paraview maximum frames. |
| paraview_time_range_s | null | s | Entrada/configuración | Ajusta paraview time range s. |
| field_scale_mode | exact | — | Entrada/configuración | Ajusta field scale mode. |
| export_vtk | false | — | Entrada/configuración | Activa o desactiva export vtk. |
| export_vtk_all_times | false | — | Entrada/configuración | Activa o desactiva export vtk all times. |
| openfoam_postprocess_timeout_s | 600 | s | Entrada/configuración | Ajusta openfoam postprocess timeout s. |

## Malla abierta estándar alternativa

Se distribuye en Mesh Presets/open_medium_R50c_open_standard_265520.json. Tiene la misma función de parámetros que el bloque abierto; el archivo contiene sus ajustes exactos. Debe seleccionarse y volver a ligarse a la geometría/caso antes de generar. La malla optimizada es el default inicial, no una sustitución de la estándar.

## Interpretación y fuentes

Los valores proceden de los archivos efectivos conservados en Study Presets/provenance.json. La calidad observada original está separada de los parámetros. La especificación física principal es CFD_2D_TECHNICAL_SPECIFICATIONS.txt; los manuales 04–09 describen referencias, modelos y limitaciones. No hay una corrección universal que convierta un preset aprobado en una validación científica de todas las geometrías.
