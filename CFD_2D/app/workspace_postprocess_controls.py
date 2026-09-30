"""One visible source for analysis, sampling and visualization options."""
import streamlit as st
from parameter_controls import reference


def postprocess_controls():
    fields, figures, rans = st.columns(3)
    options = {
        "run_openfoam_postprocess": fields.toggle("Campos de pared y perfiles de velocidad", True, help="Calcula Cp, Cf, y+ y muestras normales a la pared desde los campos disponibles."),
        "automatic_paraview_products": figures.toggle("Figuras y campos de ParaView", True, help="Genera imágenes para los últimos estados guardados. No ejecuta el solver."),
        "include_rans_stage": rans.toggle("Incluir RANS guardado", True, help="Procesa también el archivo estacionario y permite compararlo con URANS."),
    }
    options["wall_profile_analysis"] = options["run_openfoam_postprocess"]
    with st.expander("Perfiles de velocidad · normales a la pared", expanded=True):
        text = st.text_input("Estaciones x/c", "0.05, 0.1, 0.3, 0.6, 0.9", help="Una normal por estación en extradós e intradós. Se muestran las posiciones efectivamente encontradas en la malla.")
        try:
            stations = sorted(set(float(s.strip()) for s in text.split(",") if s.strip()))
            if not stations or any(not 0 < s < 1 for s in stations): raise ValueError()
        except ValueError:
            st.error("Introduce estaciones numéricas entre 0 y 1, separadas por comas.")
            return None
        options["velocity_profile_stations"] = stations
        options["velocity_profile_sample_points"] = st.number_input("Puntos por normal", 10, 1000, 80, help="Muestreo progresivo con mayor densidad cerca de la pared. Más puntos interpolan mejor el perfil, pero no añaden resolución a la malla.")
        options["rans_average_tail_samples"] = st.number_input("Muestras finales para estadísticas RANS", 2, 100000, 500, help="Ventana de coeficientes estacionarios; no es un promedio temporal URANS.")
        absolute=st.toggle("Promediar RANS desde una iteración concreta",False)
        options["rans_average_start_iteration"]=st.number_input("Primera iteración del promedio RANS",min_value=0,value=3500) if absolute else None
    with st.expander("Imágenes y animaciones"):
        options["include_paraview_animations"] = st.toggle("Generar animaciones de campos", False, disabled=not options["automatic_paraview_products"], help="Requiere varios tiempos volumétricos guardados y aumenta el coste de postproceso.")
        options["paraview_maximum_frames"] = st.number_input("Máximo de fotogramas", 2, 1000, 24, help="Selecciona estados existentes; no inventa estados intermedios.")
        custom_range = st.toggle("Limitar el intervalo físico de las imágenes URANS", False, help="Filtra las imágenes y animaciones. No cambia la ventana estadística de la polar, que procede de las fases del caso.")
        options["paraview_time_range_s"] = None
        if custom_range:
            cols = st.columns(2)
            start = cols[0].number_input("Inicio [s]", min_value=0., value=0., format="%.8g")
            end = cols[1].number_input("Fin [s]", min_value=0., value=1., format="%.8g")
            if end <= start:
                st.error("El final del intervalo debe ser mayor que el inicio.")
                return None
            options["paraview_time_range_s"] = [start, end]
        options["field_scale_mode"] = st.selectbox("Escala de color", ["exact", "robust"], help="Exact usa extremos reales. Robust recorta visualmente valores extremos por percentiles; conserva los datos originales.")
    with st.expander("Exportaciones y límites de postproceso"):
        options["export_vtk"] = st.toggle("Exportar VTK", False, help="Copia campos a un formato independiente para otros visores; ParaView también puede leer el caso OpenFOAM directamente.")
        options["export_vtk_all_times"] = st.toggle("Exportar VTK de todos los tiempos guardados", False, disabled=not options["export_vtk"], help="Puede ocupar mucho espacio. Desactivado: se exporta el estado final.")
        options["openfoam_postprocess_timeout_s"] = st.number_input("Límite por operación de postproceso [s]", 30, 14400, 600, help="Interrumpe una operación atascada conservando los resultados ya producidos.")
    reference(options, "Referencia de variables de postproceso")
    return options
