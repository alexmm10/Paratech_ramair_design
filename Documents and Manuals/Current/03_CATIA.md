# Geometría CAD y suspensión

2026.09.30 · API 28 · suspensión schema 2. Este flujo prepara CATIA sin iniciarlo.

## Preparar un modelo

En Geometría CATIA elija perfil, nombre y carpeta de modelo. Configure dimensiones, costillas, arco, crossports, estabilizadores y componentes. El modelo posee model.json, model_config.json y system_config.json independientes. Previsualice, revise avisos y guarde antes de generar Inputs. La macro y tablas quedan con el modelo, no en un único directorio global.

## Bandas, anclajes y frenos

Defina bandas A/B/C/D por x/c y la selección estructural por costillas. Los frenos tienen selección independiente: porcentaje exterior de b/2 o IDs de celdas completos. Un modelo nuevo empieza con 33%; un modelo anterior conserva su selección. La simetría refleja los IDs del lado izquierdo; desactivarla permite ambos lados independientes.
Los IDs de celdas empiezan en 1 desde la punta izquierda. Se usan las posiciones desarrolladas Y_flat_mm reales, incluyendo anchuras no uniformes. Se incorporan celdas completas hasta cubrir el tramo; la cobertura efectiva puede ser superior a la solicitada. Para nueve celdas uniformes, el 33% puede cubrir efectivamente 44.44% de cada b/2. Las fijaciones son los límites de esas celdas, sin duplicar costillas compartidas ni rellenar huecos entre selecciones discontinuas.

## Agrupación y destinos

Las plantillas permiten líneas directas, vecinos en envergadura, A–B/C–D en la misma costilla, combinación longitudinal/transversal y niveles compatibles previos. A–B debe compartir destino delantero, C–D destino trasero. BRK tiene una sola confluencia por lado antes de su destino; no se añaden automáticamente cascadas antiguas y un tronco extra.
La fracción sitúa la confluencia entre el centro de sus entradas y el destino. El tamaño de grupo transversal define vecinos por banda y lado. Los elementos que no completan un grupo continúan directamente. El modo manual permite nodos y segmentos explícitos usando IDs de la vista previa.
Se comprueban destinos, ciclos, segmentos duplicados o nulos y anclajes/confluencias sin destino único. La red inválida bloquea la generación y explica el motivo. R/b sigue siendo la distancia recta media de anclajes estructurales al centro de confluencia; no la suma de los segmentos de cascada.

## Vistas y archivos

Las vistas de planta, frontal y lateral colorean bandas y niveles. El esquema de celdas resalta frenos. La vista previa muestra cobertura, IDs y número de fijaciones. Inputs contiene curvas, tablas ramair_*.csv, configuración efectiva, informe geométrico y validación de red. Las exportaciones de CATIA se guardan en Exports.
Las tablas existentes consumidas por la macro conservan su contrato. Antes de ejecutar manualmente la macro en CATIA V5, seleccionar la carpeta Inputs del modelo y verificar las opciones de exportación.

## Alcance

Los ángulos posicionan el conjunto mediante theta = alpha + mu − gamma. No son un cálculo automático de equilibrio aerodinámico ni una validación estructural. Longitudes/drag geométrico no prueban tensiones, resistencia o deformación del tejido. La entrega comprueba preparación Python y macro disponible; las operaciones reales CATIA y licencia se aceptan en el equipo receptor.
