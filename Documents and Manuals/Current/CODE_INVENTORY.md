# Inventario de código · 2026.09.30

165 módulos. Inventario AST; no reemplaza tests ni identifica todos los procesos externos.

| Archivo | Líneas | Símbolos | Responsabilidad |
|---|---:|---:|---|
| CFD_2D/app/__init__.py | 2 | 0 | Graphical orchestration package for the ram-air CFD 2D workflow. |
| CFD_2D/app/cad_design_controls.py | 187 | 4 | Design-oriented CAD controls; backend geometry remains the source of previews. |
| CFD_2D/app/cad_preview.py | 70 | 2 | Cached preview from the same isolated preprocessor used by named CAD models. |
| CFD_2D/app/closed_experimental_mesh_page.py | 696 | 5 | Streamlit controls for the closed from-scratch experimental mesher. |
| CFD_2D/app/experimental_mesh_comparison.py | 260 | 10 | Reusable quality comparison panel for experimental Gmsh revisions. |
| CFD_2D/app/gmsh_python_runner.py | 120 | 3 | Isolated Gmsh Python API worker. |
| CFD_2D/app/ls1_validation_page.py | 1502 | 17 | Independent LS(1)-0417 closed-airfoil polar validation UI. |
| CFD_2D/app/mesh_workspace_controls.py | 103 | 6 | Common controls and exact checkMesh display for the standard meshers. |
| CFD_2D/app/open_closed_validation_page.py | 679 | 7 | Independent open-airfoil polar campaign compared with published closed points. |
| CFD_2D/app/open_experimental_mesh_page.py | 1079 | 7 | Streamlit panel for the isolated open-airfoil Gmsh experiment. |
| CFD_2D/app/parallel_campaign_ui.py | 138 | 5 | Reusable Streamlit controls and monitors for independent case campaigns. |
| CFD_2D/app/parameter_controls.py | 190 | 7 | Consistent typed controls and a searchable reference for current parameters. |
| CFD_2D/app/performance_report_page.py | 196 | 4 | Measured OpenFOAM performance report embedded in the validation lab. |
| CFD_2D/app/pyfoam_solver_runner.py | 774 | 27 | Run one OpenFOAM case through the PyFoam Python API. |
| CFD_2D/app/ramair_cfd2d_app.py | 184 | 5 | RamAir Studio shell: version contract, background jobs and modular pages. |
| CFD_2D/app/ramair_live_monitor.py | 502 | 17 | Reliable live plots for a PyFoam-managed OpenFOAM run. |
| CFD_2D/app/validation_convergence_page.py | 3690 | 27 | Streamlit page for the isolated Validation & Convergence Lab. |
| CFD_2D/app/validation_plotting.py | 245 | 5 | Static, compact plots for the Validation & Convergence Lab monitors. |
| CFD_2D/app/windows_gmsh_viewer.py | 52 | 3 | Open a Linux/WSL mesh with the Windows Gmsh Python API GUI. |
| CFD_2D/app/workflow_backend.py | 3435 | 105 | Backend shared by the Streamlit UI and command-line launchers. |
| CFD_2D/app/workspace_app.py | 828 | 19 | Modular desktop workflow. Profiles, CAD and CFD have independent ownership. |
| CFD_2D/app/workspace_execution_controls.py | 162 | 4 | Resource planning and live RANS/URANS monitors for independent solver setups. |
| CFD_2D/app/workspace_postprocess_controls.py | 46 | 1 | One visible source for analysis, sampling and visualization options. |
| CFD_2D/app/workspace_rans_convergence.py | 98 | 1 | General execution mesh-convergence UI; studies do not mutate the laboratory. |
| CFD_2D/scripts/boundary_layer_estimates.py | 225 | 8 | Lightweight boundary-layer estimates shared by the mesh UI and reports. |
| CFD_2D/scripts/check_environment.py | 225 | 10 | Environment checker for the ram-air CAD/CFD workflow. |
| CFD_2D/scripts/initialize_project_layout.py | 275 | 9 | Create, validate or migrate the RamAir DESIGN APP folder layout safely. |
| CFD_2D/scripts/mesh_configuration.py | 310 | 3 | Shared domain and mesh-level configuration for the CFD 2D workflow. |
| CFD_2D/scripts/openfoam_environment.py | 141 | 6 | Resolve the OpenFOAM shell environment without relying on login-shell state. |
| CFD_2D/scripts/openfoam_history.py | 171 | 7 | Readers for segmented OpenFOAM histories, including restarted cases. |
| CFD_2D/scripts/openfoam_wall_analysis.py | 1384 | 35 | Wall y+ and boundary-layer profile analysis for OpenFOAM 2D cases. |
| CFD_2D/scripts/paraview_case_viewer.py | 2252 | 13 | Scripted ParaView launcher for reconstructed OpenFOAM cases. |
| CFD_2D/scripts/preprocess_ramair.py | 4772 | 195 | preprocess_ramair_profile_parametric_v18_EXPORT_FIX_SAFE_SHARPTE.py |
| CFD_2D/scripts/project_layout.py | 80 | 3 | Canonical, user-facing RamAir DESIGN APP paths. |
| CFD_2D/scripts/ramair_2d_batch_postprocess.py | 143 | 5 | Postprocess selected alpha cases sequentially without hiding failures. |
| CFD_2D/scripts/ramair_2d_bump_matching.py | 499 | 11 | Four-segment Gmsh Bump matching for experimental airfoil meshes. |
| CFD_2D/scripts/ramair_2d_checkpoint_integrity.py | 141 | 7 | Content-based RANS-checkpoint and OpenFOAM field integrity checks. |
| CFD_2D/scripts/ramair_2d_closed_experimental_mesh.py | 1016 | 12 | Closed-airfoil companion to the from-scratch experimental Gmsh mesher. |
| CFD_2D/scripts/ramair_2d_closed_medium_slowdown_audit.py | 273 | 5 | Audit the archived closed-medium slowdown from real evidence. |
| CFD_2D/scripts/ramair_2d_closed_open_convergence_study.py | 549 | 11 | Build and package closed/open mesh-convergence baselines. |
| CFD_2D/scripts/ramair_2d_convergence_analysis.py | 452 | 16 | Numerical/statistical analysis for validation convergence studies. |
| CFD_2D/scripts/ramair_2d_convergence_decisions.py | 291 | 9 | Evidence-driven adaptive decisions for Validation Lab schema 11. |
| CFD_2D/scripts/ramair_2d_courant_diagnostics.py | 195 | 6 | Diagnose fixed or adaptive OpenFOAM time-step behavior from a real solver log. |
| CFD_2D/scripts/ramair_2d_execution_registry.py | 290 | 10 | Atomic execution registry for real RANS, canonical URANS and PIMPLE runs. |
| CFD_2D/scripts/ramair_2d_experimental_control_study.py | 138 | 4 | Run a bounded, reproducible study of opt-in experimental Gmsh controls. |
| CFD_2D/scripts/ramair_2d_experimental_mesh_urans_study.py | 557 | 5 | Run bounded closed/open experimental mesh trials for URANS selection. |
| CFD_2D/scripts/ramair_2d_final_mesh_selection_report.py | 119 | 5 | Persist the final closed/open URANS mesh selections and comparisons. |
| CFD_2D/scripts/ramair_2d_gmsh_experimental.py | 299 | 7 | Shared, opt-in Gmsh controls for the experimental 2D meshers. |
| CFD_2D/scripts/ramair_2d_hardware_audit.py | 144 | 5 | Publish a read-only CPU/MPI/GPU audit and fixed-numerics rank decision. |
| CFD_2D/scripts/ramair_2d_inlet_designer.py | 618 | 32 | Design a ram-air leading-edge opening from XFOIL stagnation points. |
| CFD_2D/scripts/ramair_2d_ls1_validation_study.py | 249 | 5 | Create/migrate the standalone LS(1)-0417 polar-validation workspace. |
| CFD_2D/scripts/ramair_2d_mesh_builder.py | 8699 | 140 | Robust Gmsh mesh builder for ram-air 2D profile case packages. |
| CFD_2D/scripts/ramair_2d_mesh_numerics.py | 83 | 4 | Derive OpenFOAM non-orthogonal controls from an actual checkMesh report. |
| CFD_2D/scripts/ramair_2d_mesh_optimizer.py | 289 | 8 | Run a bounded mesh-parameter study and retain only the best real mesh. |
| CFD_2D/scripts/ramair_2d_mesh_quality_controller.py | 651 | 8 | Strict, small mesh-quality controller for the ram-air 2D Gmsh workflow. |
| CFD_2D/scripts/ramair_2d_mesh_quality_distributions.py | 475 | 12 | Detailed, reproducible quality distributions for linear MSH2 CFD meshes. |
| CFD_2D/scripts/ramair_2d_mesh_refinement_analysis.py | 296 | 11 | Analyze the alpha=8 LS(1)-0417 mesh-refinement study using real outputs only. |
| CFD_2D/scripts/ramair_2d_mesh_refinement_study.py | 249 | 10 | Build protected coarse/fine alpha=8 meshes around the existing medium validation case. |
| CFD_2D/scripts/ramair_2d_mesh_science.py | 389 | 12 | Reproducible scientific checks for the RamAir Gmsh meshing policy. |
| CFD_2D/scripts/ramair_2d_open_closed_comparison.py | 491 | 19 | Publish open-airfoil validation points and compare them with the closed polar. |
| CFD_2D/scripts/ramair_2d_open_divergence.py | 85 | 2 | Classify fixed-geometry Open RANS diagnostics before URANS expansion. |
| CFD_2D/scripts/ramair_2d_open_experimental_mesh.py | 2789 | 42 | Independent from-scratch Gmsh mesh experiment for the open LS(1)-0417 airfoil. |
| CFD_2D/scripts/ramair_2d_open_light_candidate.py | 345 | 11 | Prepare and evaluate a non-destructive open-airfoil light-mesh sweep. |
| CFD_2D/scripts/ramair_2d_open_medium_cfl_verification.py | 182 | 4 | Run a disposable five-step open-medium CFL diagnostic and locate max Co. |
| CFD_2D/scripts/ramair_2d_open_mesh_quality_study.py | 162 | 4 | Run bounded open-mesh transition trials and retain the best URANS candidate. |
| CFD_2D/scripts/ramair_2d_open_mesh_refinement.py | 825 | 20 | Build, evaluate and atomically promote definitive open coarse/fine meshes. |
| CFD_2D/scripts/ramair_2d_open_validation_pilot.py | 247 | 6 | Prepare an open-airfoil validation pilot from an accepted convergence RANS base. |
| CFD_2D/scripts/ramair_2d_open_validation_workcase.py | 289 | 5 | Build the open ram-air comparison work case at LS(1)-0417 validation conditions. |
| CFD_2D/scripts/ramair_2d_openfoam_case_audit.py | 321 | 15 | Audit the generated 2-D SA airfoil case contract without changing a case. |
| CFD_2D/scripts/ramair_2d_openfoam_case_writer.py | 1848 | 44 | OpenFOAM case writer for approved ram-air 2D meshes. |
| CFD_2D/scripts/ramair_2d_openfoam_log_events.py | 158 | 6 | Structured classification of OpenFOAM process logs. |
| CFD_2D/scripts/ramair_2d_openfoam_runner.py | 1756 | 44 | Safe OpenFOAM runner for ram-air 2D cases. |
| CFD_2D/scripts/ramair_2d_openfoam_staged_runner.py | 1864 | 40 | Optional SIMPLE initialization followed by the transient OpenFOAM runner. |
| CFD_2D/scripts/ramair_2d_openfoam_sweep.py | 539 | 12 | Run existing OpenFOAM angle cases sequentially with bounded resources. |
| CFD_2D/scripts/ramair_2d_parallel.py | 435 | 16 | Single-source parallel execution policy for RamAir OpenFOAM cases. |
| CFD_2D/scripts/ramair_2d_parallel_campaign.py | 198 | 6 | Run independent RamAir case commands concurrently within one core budget. |
| CFD_2D/scripts/ramair_2d_parallel_throughput_benchmark.py | 160 | 6 | Compare one MPI case with two simultaneous cases at a fixed core budget. |
| CFD_2D/scripts/ramair_2d_parallel_tuner.py | 297 | 10 | Bounded, scratch-only OpenFOAM MPI pilot used by the pre-run optimiser. |
| CFD_2D/scripts/ramair_2d_pimple_outer_study.py | 1224 | 20 | Executable isolated nOuterCorrectors sensitivity study (2, 3 and 4). |
| CFD_2D/scripts/ramair_2d_postprocess.py | 1884 | 35 | Post-processing utilities for ram-air 2D OpenFOAM cases. |
| CFD_2D/scripts/ramair_2d_postprocess_registry.py | 239 | 7 | Portable authoritative postprocess inventory for general and lab runs. |
| CFD_2D/scripts/ramair_2d_profile_case_builder.py | 1240 | 61 | ramair_2d_profile_case_builder.py |
| CFD_2D/scripts/ramair_2d_rans_accounting.py | 358 | 10 | Absolute SIMPLE-iteration and timing accounting for Validation Lab. |
| CFD_2D/scripts/ramair_2d_rans_checkpoint_batch.py | 3231 | 62 | Manage the six mesh-specific RANS/SIMPLE bases for the validation lab. |
| CFD_2D/scripts/ramair_2d_rans_full_postprocess.py | 113 | 1 | Explicit full postprocess for one Validation Lab RANS checkpoint. |
| CFD_2D/scripts/ramair_2d_rans_paraview_final.py | 405 | 5 | Generate bounded final-state ParaView products for one RANS checkpoint. |
| CFD_2D/scripts/ramair_2d_rans_paraview_inventory.py | 49 | 1 | Resolve iteration-consistent ParaView artifact sets for six RANS bases. |
| CFD_2D/scripts/ramair_2d_rans_plateau_gate.py | 640 | 10 | Pure RANS/SIMPLE convergence and numerical-plateau evaluation. |
| CFD_2D/scripts/ramair_2d_rans_review.py | 1437 | 26 | Traceable review and scalar diagnostics for existing RANS/SIMPLE bases. |
| CFD_2D/scripts/ramair_2d_reference_extend_study.py | 231 | 12 | Generate immutable-reference Extend variants for the experimental meshers. |
| CFD_2D/scripts/ramair_2d_run_lease.py | 231 | 13 | Atomic single-flight leases for long validation-study executions. |
| CFD_2D/scripts/ramair_2d_scale_validation_geometry.py | 183 | 7 | Create a traceable one-metre CFD geometry from preprocessor exports. |
| CFD_2D/scripts/ramair_2d_solver_benchmark_report.py | 218 | 6 | Summarize bounded native/PyFoam, MPI-rank and PIMPLE benchmark scenarios. |
| CFD_2D/scripts/ramair_2d_solver_performance.py | 167 | 6 | Project transient OpenFOAM run time from a completed bounded benchmark. |
| CFD_2D/scripts/ramair_2d_space_time_convergence.py | 527 | 11 | Accepted-only spatial-temporal convergence assembly. |
| CFD_2D/scripts/ramair_2d_split_progression.py | 307 | 8 | Shared Gmsh Bump + split-Progression tangential matching. |
| CFD_2D/scripts/ramair_2d_storage_inventory.py | 255 | 8 | Storage inventory and bounded cleanup for the active validation workspace. |
| CFD_2D/scripts/ramair_2d_study_registry.py | 1463 | 30 | Isolated state and Results registry for the validation convergence lab. |
| CFD_2D/scripts/ramair_2d_temporal_budget.py | 202 | 6 | Physical and computational budgets for the validation convergence lab. |
| CFD_2D/scripts/ramair_2d_timestep_advisor.py | 500 | 11 | Build a physical and numerical time-step budget for RamAir CFD 2D. |
| CFD_2D/scripts/ramair_2d_urans_cases.py | 789 | 41 | Canonical URANS case identity, presence and lifecycle services. |
| CFD_2D/scripts/ramair_2d_urans_contract.py | 98 | 1 | Shared URANS execution vocabulary and start-mode rules. |
| CFD_2D/scripts/ramair_2d_urans_matrix_manager.py | 416 | 14 | Atomic sequential queue for canonical Validation Lab URANS cases. |
| CFD_2D/scripts/ramair_2d_urans_review.py | 808 | 20 | Oscillatory-signal review for one real Validation Lab URANS run. |
| CFD_2D/scripts/ramair_2d_urans_transition_verification.py | 263 | 6 | Run disposable bounded A->B and C->D OpenFOAM transition checks. |
| CFD_2D/scripts/ramair_2d_validation.py | 992 | 21 | Build LS(1)-0417 validation overlays from reference and real CFD results. |
| CFD_2D/scripts/ramair_2d_validation_campaigns.py | 553 | 12 | Extensible, metadata-only campaign engine for the current Validation Lab schema. |
| CFD_2D/scripts/ramair_2d_validation_lab_migration.py | 217 | 6 | Evidence-preserving migrations for the Validation & Convergence Lab. |
| CFD_2D/scripts/ramair_2d_validation_live_monitor.py | 561 | 13 | Incremental scalar-only monitor for validation RANS and URANS runs. |
| CFD_2D/scripts/ramair_2d_validation_publish.py | 94 | 3 | Publish explicitly selected, eligible CFD angles to the active validation work case. |
| CFD_2D/scripts/ramair_2d_validation_rans_recovery.py | 295 | 10 | Return an explicitly selected short validation URANS attempt to its last RANS state. |
| CFD_2D/scripts/ramair_2d_validation_report.py | 1361 | 21 | Per-run and study reports for the validation convergence laboratory. |
| CFD_2D/scripts/ramair_2d_validation_schema9_migration.py | 350 | 12 | Preview/apply the authorized Validation Lab schema-8 to schema-9 reset. |
| CFD_2D/scripts/ramair_2d_validation_smoke.py | 311 | 7 | Run a bounded closed-coarse SIMPLE-to-URANS software smoke test. |
| CFD_2D/scripts/ramair_2d_validation_staged_runner.py | 1599 | 23 | Execute one canonical Validation Lab URANS timeline. |
| CFD_2D/scripts/ramair_2d_validation_study.py | 1905 | 25 | CLI/backend for the isolated closed/open validation convergence lab. |
| CFD_2D/scripts/ramair_2d_validation_workcase.py | 240 | 5 | Create the real LS(1)-0417 validation work case under ``Results``. |
| CFD_2D/scripts/ramair_cad_diagnostics.py | 154 | 2 | Plots and numerical audits of the actual geometry exported to CATIA. |
| CFD_2D/scripts/ramair_case_library.py | 1231 | 36 | Save and restore reusable RamAir workflow stages under ``Results/``. |
| CFD_2D/scripts/ramair_cfd2d_workflow_tool.py | 773 | 21 | Interactive/configurable ram-air CFD 2D workflow helper. |
| CFD_2D/scripts/ramair_comparison_figures.py | 51 | 3 | Publication figures reused by workspace menus and exported reports. |
| CFD_2D/scripts/ramair_crossports.py | 51 | 1 | Explicit rib holes: one row per hole, shared by previews and CAD export. |
| CFD_2D/scripts/ramair_curve_curvature.py | 33 | 1 | Signed curvature of the actual CAD curves passed to Gmsh. |
| CFD_2D/scripts/ramair_execution_control.py | 571 | 19 | Durable process identity and restart evidence for RamAir OpenFOAM runs. |
| CFD_2D/scripts/ramair_frequency_analysis.py | 173 | 7 | Physical FFT/Welch estimates and five equivalent frequency coordinates. |
| CFD_2D/scripts/ramair_front_sizing.py | 81 | 3 | Spatially varying triangle sizes from the meshed boundary-layer columns. |
| CFD_2D/scripts/ramair_geometry_workspace.py | 305 | 10 | Shared, versioned geometry DTO and reusable airfoil catalogue. |
| CFD_2D/scripts/ramair_hardware.py | 65 | 4 | Live host inventory and campaign throughput with explicit measurement windows. |
| CFD_2D/scripts/ramair_manual_rigging.py | 71 | 2 | Validated explicit suspension graph, independent of automatic cascade layout. |
| CFD_2D/scripts/ramair_mesh_quality_atlas.py | 177 | 4 | Native OpenFOAM-14 quality maps for PASS and FAIL meshes, with exact IDs. |
| CFD_2D/scripts/ramair_mesh_runtime.py | 35 | 3 | Shared runtime bounds and optional wake field for the standard meshers. |
| CFD_2D/scripts/ramair_monitor_core.py | 236 | 11 | Shared, bounded parser for OpenFOAM scalar monitoring signals. |
| CFD_2D/scripts/ramair_polar_comparison.py | 141 | 7 | Curated polars and comparisons of runs with identical CFD conditions. |
| CFD_2D/scripts/ramair_production_capture.py | 20 | 1 | Shared, explicit production snapshot policy for all staged runners. |
| CFD_2D/scripts/ramair_profile_library.py | 210 | 9 | Shared unit-chord profile preparation, independent of CATIA and CFD cases. |
| CFD_2D/scripts/ramair_profile_utils.py | 781 | 24 | Robust 2D profile utilities for ram-air CFD preprocessing. |
| CFD_2D/scripts/ramair_rans_mesh_study.py | 216 | 8 | Persistent general-workspace RANS mesh studies. No extrapolation or solver mutation. |
| CFD_2D/scripts/ramair_remote_queue_runner.py | 425 | 12 | Run, stop, resume and post-process a frozen remote OpenFOAM queue. |
| CFD_2D/scripts/ramair_report_figures.py | 65 | 1 | Publication figures shared by the report export and the application. |
| CFD_2D/scripts/ramair_scientific_plot_style.py | 131 | 4 | Shared, deterministic scientific plotting and provenance helpers. |
| CFD_2D/scripts/ramair_simulation_inventory.py | 47 | 3 | Read-only phase-aware progress; completion of a process is not convergence. |
| CFD_2D/scripts/ramair_suspension_layout.py | 174 | 6 | Versioned suspension selection and automatic networks (millimetres). |
| CFD_2D/scripts/ramair_validation_gallery.py | 27 | 1 | Permanent selected figures, independent from deletable laboratory runs. |
| CFD_2D/scripts/ramair_validation_reference.py | 28 | 1 | User-curated LS reference curves, preserving original digitized coordinates. |
| CFD_2D/scripts/ramair_wall_topology.py | 70 | 2 | Separate coincident baffle faces using oriented polygons and real connectivity. |
| CFD_2D/scripts/ramair_workcase_schema.py | 600 | 23 | Versioned identities, revisions and dependencies for RamAir work cases. |
| CFD_2D/scripts/ramair_workspace.py | 335 | 28 | Persistent, independent CAD/CFD workspaces. No active-global input mutation. |
| CFD_2D/scripts/ramair_workspace_jobs.py | 193 | 6 | Bounded stage commands for the workspace UI; solvers require --run. |
| CFD_2D/scripts/ramair_workspace_lifecycle.py | 171 | 6 | Explicit lifecycle operations and scientific review adapters for workspaces. |
| CFD_2D/scripts/ramair_workspace_storage.py | 58 | 4 | Small Windows publications; OpenFOAM remains on the native Linux filesystem. |
| CFD_2D/scripts/wall_separation_analysis.py | 624 | 13 | Connectivity-based wall-shear and boundary-layer separation analysis. |
| Application Support/Tools/check_project_context.py | 51 | 2 | Check that Codex context and release metadata match the application API. |
| Application Support/Tools/check_repository_artifacts.py | 43 | 1 | Fail when Git would include generated or unexpectedly large artifacts. |
| Application Support/Tools/import_ramair_remote_results.py | 203 | 7 | Validate and import a RamAir remote-return archive into its canonical cases. |
| Application Support/Tools/launch_catia_macro.py | 237 | 10 | Detect CATIA V5 on Windows and optionally launch the RamAir CATScript. |
| Application Support/Tools/package_ramair_catia_windows.py | 280 | 8 | Build and verify a standalone Windows package for Python + CATIA V5. |
| Application Support/Tools/package_ramair_project.py | 202 | 5 | Create a portable RamAir source package without heavy generated outputs. |
| Application Support/Tools/package_ramair_remote_execution.py | 227 | 6 | Create a checked, restartable OpenFOAM execution package for a Linux/WSL server. |
| Application Support/Tools/project_git.py | 163 | 10 | Conservative Git maintenance commands used by the application UI. |
| Application Support/Tools/release_identity.py | 23 | 1 | Shared source identity for application, workspace and remote packages. |
| Application Support/Tools/transfer_studio_workspace.py | 92 | 5 | Export/import named Studio workspaces with verified files and relocatable metadata. |
| Application Support/Tools/verify_company_delivery.py | 112 | 1 | Repeatable, bounded company-delivery acceptance on native Linux/WSL. |
| Application Support/Tools/verify_studio_package.py | 46 | 1 | Extract a portable application and verify every file and Python source. |
| Application Support/Tools/verify_studio_ui.py | 39 | 1 | Exercise current pages without starting external CAD or solver processes. |
| CATIA/Utilities/VERIFY_CATIA_PACKAGE.py | 133 | 4 | Static and data-level verification for the standalone CATIA package. |
