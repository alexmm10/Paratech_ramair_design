"""Rebuild the module and callable inventory without executing application code."""
import ast,csv
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
def main():
    paths=sorted({*ROOT.glob('*.py'),*(ROOT/'CFD_2D/app').glob('*.py'),*(ROOT/'CFD_2D/scripts').glob('*.py'),*(ROOT/'Application Support/Tools').glob('*.py')})
    docs=ROOT/'Documents and Manuals/Current'
    rows=[];summary=[]
    for path in paths:
        source=path.read_text(encoding='utf-8-sig');tree=ast.parse(source)
        symbols=[]
        def visit(node,prefix=''):
            for child in ast.iter_child_nodes(node):
                if isinstance(child,(ast.ClassDef,ast.FunctionDef,ast.AsyncFunctionDef)):
                    symbol=prefix+child.name;symbols.append((child.lineno,symbol,type(child).__name__))
                    visit(child,symbol+'.')
                else:visit(child,prefix)
        visit(tree)
        relative=path.relative_to(ROOT).as_posix()
        rows.extend((relative,*s) for s in sorted(symbols))
        description=(ast.get_docstring(tree) or 'Módulo de soporte; consultar sus funciones y pruebas.').split('\n')[0].replace('|','/')
        summary.append((relative,len(source.splitlines()),len(symbols),description))
    with (docs/'FUNCTION_INVENTORY.csv').open('w',encoding='utf-8',newline='') as out:
        writer=csv.writer(out);writer.writerow(['module','line','symbol','kind']);writer.writerows(rows)
    text=['# Inventario de código · 2026.10.19','',f'{len(paths)} módulos y {len(rows)} símbolos. Análisis AST; no ejecuta código ni sustituye pruebas de software/CAE.','',
      '| Archivo | Líneas | Símbolos | Responsabilidad |','|---|---:|---:|---|']
    text+=['| '+' | '.join(map(str,row))+' |' for row in summary]
    (docs/'CODE_INVENTORY.md').write_text('\n'.join(text)+'\n',encoding='utf-8')
    print(f'{len(paths)} modules / {len(rows)} symbols')
if __name__=='__main__':main()
