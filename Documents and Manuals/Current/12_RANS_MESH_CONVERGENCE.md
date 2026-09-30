# Convergencia de malla RANS · Ejecución general

2026.09.30 · API 28. Es independiente del laboratorio y conserva ejecuciones anteriores.

## Preparación

Abra Ejecución → Convergencia de malla · RANS. Seleccione el caso/geométría, al menos dos mallas aprobadas y una configuración RANS existente como referencia. Las demás mallas pueden no tener solver. Introduzca un ángulo, varios o la lista completa de la polar; nombre y guarde el estudio.
Se comparan geometría y revisión, condiciones físicas, referencias de fuerzas/momentos, turbulencia, contornos y ajustes numéricos. Las firmas incluyen los ajustes efectivos; solamente se excluyen controles operativos y la marca de prueba. Las diferencias se explican antes de reutilizar datos. Las mallas incompatibles/no aprobadas bloquean creación. Las configuraciones necesarias reciben nombres nuevos y copias de ajustes; no se reescriben setups anteriores.

## Estados y ejecución

La matriz malla–ángulo distingue pendiente, disponible, ejecutándose, pausado, fallido, postprocesado, revisado y obsoleto. Cada punto registra fuente, SHA-256 y contexto. Los hashes de historia de fuerzas permiten detectar continuación posterior al postproceso.
Prepare/compruebe sin solver o habilite RANS real. La cola reutiliza asignación de recursos, monitores y pausa guardando campos. Solo se despachan puntos pendientes/continuables; el control adicional permite extender puntos ya postprocesados. Tras extenderlos se repite postproceso y análisis. URANS queda deshabilitado también en el paquete remoto.
El estudio se guarda en Geometries/<nombre>/Convergence/RANS/<estudio>, separado de Meshes y del laboratorio.

## Comparación y exportación

Se presentan número de celdas, versión/calidad, Cl/Cd/Cm y eficiencia frente a N, polares superpuestas y diferencias absolutas/relativas por ángulo. La referencia inicial es la malla con mayor N que disponga de resultados. Puede elegir otra. Hay tablas CSV y doce imágenes PNG para descargar.
Los análisis múltiples utilizan solo ángulos comunes y enumeran ausentes; no extrapolan. Cerca de un denominador cero, el porcentaje queda sin definir y se informa; no aparece una mejora infinita. Cambiar resultados invalida el análisis guardado hasta recalcularlo.

## GCI y límites

Con tres mallas se usa h proporcional a N^(−1/2), apropiado para esta extrusión 2D, y el cálculo existente de orden observado/GCI. Requiere refinamiento admisible, convergencia iterativa y puntos revisados compatibles. Oscilación, denominadores nulos, refinamiento insuficiente y pruebas diagnósticas impiden atribuir independencia de malla. La prueba de entrega 2×2 solo verifica software y produce resultados parciales; no cumple ese criterio.
