"""Exercise current pages without starting external CAD or solver processes."""
from pathlib import Path
import argparse
import json
import time
from streamlit.testing.v1 import AppTest


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace',type=Path)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[2]
    app=AppTest.from_file(str(root/'CFD_2D/app/ramair_cfd2d_app.py')).run(timeout=120)
    if args.workspace:
        app.sidebar.selectbox[0].select(str(args.workspace.resolve())).run(timeout=120)
    rows=[]
    for page in ('Inicio','Casos de trabajo','Perfiles 2D','Geometría CATIA','Caso CFD','Mallado','Caso OpenFOAM','Ejecución','Postproceso','Validation & Convergence Lab','Archivos y logs'):
        start=time.monotonic()
        app.sidebar.radio[0].set_value(page).run(timeout=120)
        if page=='Ejecución' and args.workspace:
            study=next((s for s in app.selectbox if s.label=='Estudio guardado'),None)
            if study and any('Final_Delivery' in str(o) for o in study.options):
                folder=next(args.workspace.glob('CFD_2D/*/Geometries/*/Convergence/RANS/Final_Delivery_2x2'))
                study.select(folder.resolve()).run(timeout=120)
        rows.append({'page':page,'exceptions':[e.message for e in app.exception],
            'errors':[e.value for e in app.error],'warnings':[e.value for e in app.warning],
            'duration_s':round(time.monotonic()-start,3)})
        print(json.dumps(rows[-1],ensure_ascii=False),flush=True)
    report={'status':'PASS' if not any(r['exceptions'] or r['errors'] for r in rows) else 'FAIL','pages':rows,'solver_launched':False,'CATIA_launched':False}
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    return 0 if report['status']=='PASS' else 1


if __name__=='__main__':
    raise SystemExit(main())
