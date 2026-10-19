"""Regenerate the readable reference from the shipped effective configuration files."""
from pathlib import Path
import json,sys
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'CFD_2D/scripts'),str(ROOT/'CFD_2D/app')]
from ramair_studio_defaults import stage_defaults
from parameter_controls import info


def main():
    lines=['# Referencia de parámetros efectivos de estudio','',
      'Versión 2026.10.19. Generada desde los JSON entregados; las configuraciones guardadas de cada modelo/setup son la referencia efectiva después de editar.',
      '', 'Las tablas identifican clave, valor inicial, unidad y función. En el caso CFD Re/Mach/p/T son entradas; las propiedades resultantes se recalculan. En los otros bloques se muestran parámetros de configuración, incluidos controles avanzados. Las métricas de calidad son salidas originales y están en mesh_quality_reference.json; no deben editarse para aprobar otra malla.',
      '', 'Los sufijos *_star son magnitudes adimensionales basadas en c/U; los *_chord y x/c son fracciones de cuerda. Las fases URANS conservan segundos físicos, mientras RANS cuenta iteraciones. No convertir una de estas magnitudes en la otra al continuar.', '']
    stages=[('CAD','cad',None),('Sistema CAD','cad_system',None),('Condiciones CFD','case',None),
      ('Malla cerrada','mesh','closed'),('Malla abierta optimizada','mesh','open'),
      ('Solver cerrado','solver','closed'),('Solver abierto','solver','open'),
      ('Fases cerradas','phase_plan','closed'),('Fases abiertas','phase_plan','open'),('Postproceso','postprocess',None)]
    units={'rho':'kg/m³','rho_kg_m3':'kg/m³','mu':'Pa·s','mu_pa_s':'Pa·s','nu':'m²/s','velocity_m_s':'m/s','speed_of_sound_m_s':'m/s','pressure_ref_pa':'Pa','reynolds':'1','mach':'1','temperature_K':'K','gas_constant_J_kg_K':'J/(kg·K)'}
    for title,stage,top in stages:
        lines += ['## '+title,'','| Clave | Valor inicial | Unidad | Naturaleza | Función |','|---|---|---|---|---|']
        def walk(d,prefix=''):
            for key,value in d.items():
                full=prefix+'.'+key if prefix else key
                if isinstance(value,dict):walk(value,full);continue
                label,desc,unit=info(key,value)
                unit=units.get(key,unit)
                nature='Entrada/configuración'
                if stage=='case' and key not in {'reynolds','mach','pressure_ref_pa','temperature_K'}:nature='Derivada o referencia física'
                text=json.dumps(value,ensure_ascii=False) if not isinstance(value,str) else value
                def esc(v):return str(v).replace('|','/').replace('\n',' ')
                if len(text)>600:text=text[:597]+'... (ver JSON)'
                lines.append('| '+esc(full)+' | '+esc(text)+' | '+esc(unit)+' | '+nature+' | '+esc(desc)+' |')
        walk(stage_defaults(ROOT,stage,top,fallback={}))
        lines.append('')
    lines+=['## Malla abierta estándar alternativa','',
      'Se distribuye en Mesh Presets/open_medium_R50c_open_standard_265520.json. Tiene la misma función de parámetros que el bloque abierto; el archivo contiene sus ajustes exactos. Debe seleccionarse y volver a ligarse a la geometría/caso antes de generar. La malla optimizada es el default inicial, no una sustitución de la estándar.',
      '', '## Interpretación y fuentes','',
      'Los valores proceden de los archivos efectivos conservados en Study Presets/provenance.json. La calidad observada original está separada de los parámetros. La especificación física principal es CFD_2D_TECHNICAL_SPECIFICATIONS.txt; los manuales 04–09 describen referencias, modelos y limitaciones. No hay una corrección universal que convierta un preset aprobado en una validación científica de todas las geometrías.', '']
    path=ROOT/'Documents and Manuals/Current/17_STUDY_PARAMETER_REFERENCE.md'
    path.write_text('\n'.join(lines),encoding='utf-8')
    print(str(path))

if __name__=='__main__':main()
