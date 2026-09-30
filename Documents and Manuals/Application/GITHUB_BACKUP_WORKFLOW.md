# Guardado y actualización en GitHub

Git guarda fuentes, macro, defaults, presets, referencias pequeñas y manuales. Las campañas se exportan aparte; .gitignore y check_repository_artifacts impiden incluir campos/workspaces/archivos grandes.
Desde el checkout editable, revise cambios, ejecute pytest y auditorías, actualice contexto/cambios y cree el snapshot. La página de entorno usa project_git.py: configure nombre/correo y origin, cree snapshot, revise preview-push y publique. Se rechaza publicar si HEAD/rama/origin cambió desde esa revisión. Pull requiere árbol limpio y avance rápido; no reescribe historial.
Las distribuciones extraídas sin .git son instalaciones, no repositorios: para desarrollar/actualizar mediante Git, clone el repositorio. El tag studio-delivery-20260930 identifica la entrega. El hash de commit completo está en cada manifiesto; no deducir versión a partir del nombre de una carpeta.
Ver [mantenimiento](../Current/10_INSTALL_MAINTENANCE.md) y [entrega](../Current/14_DELIVERY.md).
