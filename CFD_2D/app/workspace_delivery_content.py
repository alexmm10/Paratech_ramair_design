"""Read-only entry point for the defaults, manuals and preserved study figures."""
from pathlib import Path
import streamlit as st
from ramair_studio_defaults import catalog, stage_defaults
from ramair_workspace import read_json


def retained_gallery(root: Path, key: str):
    collections = sorted(p.parent for p in (root / 'Validation Gallery').glob('*/gallery.json'))
    if not collections:
        st.caption('No hay figuras conservadas en esta instalación.'); return
    preferred = next((i for i,p in enumerate(collections) if '20261019' in p.name), 0)
    selected = st.selectbox('Colección de resultados', collections, index=preferred,
        format_func=lambda p: read_json(p / 'gallery.json').get('name', p.name), key=key+'/collection')
    info = read_json(selected / 'gallery.json')
    st.caption(info.get('scope', 'Figuras conservadas del estudio de origen; consultar su procedencia y validez.'))
    entries = [e for e in info.get('figures', []) if (selected/e['file']).is_file()]
    choice = st.multiselect('Figuras conservadas', entries, default=entries[:2],
        format_func=lambda e:e.get('title',e['file']),key=key+'/figures')
    columns = st.columns(2)
    for i,entry in enumerate(choice):
        columns[i%2].image(str(selected/entry['file']),caption=entry.get('title',entry['file']),width='stretch')


def delivery_content(root: Path):
    defaults = catalog(root)
    if not defaults:return
    with st.expander('Contenido incluido · presets, ejemplo y figuras'):
        st.markdown('**'+defaults['name']+'**')
        st.caption('Valores iniciales editables para diseños nuevos. Los casos y borradores existentes conservan sus ajustes.')
        quality = stage_defaults(root, 'quality_reference', fallback={})
        st.dataframe([{'Preset':m['name'],'Celdas de origen':m['quality'].get('checkMesh_cell_count'),
          'checkMesh de origen':m['quality'].get('checkMesh_status'),
          'No ortogonalidad máx. [°]':m['quality'].get('checkMesh_max_non_orthogonality_deg'),
          'Skewness máx.':m['quality'].get('checkMesh_max_skewness')} for m in quality.get('meshes',[])], hide_index=True,width='stretch')
        st.info('El instalador completo añade Example_Open_Polar. Selecciónelo en Caso de trabajo y visite Postproceso → Polar y resultados. Conserva resultados RANS y campos iniciales; no contiene checkpoints volumétricos para continuar la ejecución de origen.')
        st.caption('La galería se consulta aunque el usuario no haya importado ninguna simulación.')
        retained_gallery(root,'home/delivery-gallery')
