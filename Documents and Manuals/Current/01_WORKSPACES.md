# Casos de trabajo y almacenamiento

API 28 · Manual vigente · 2026-09-30.

## Responsabilidad

Una carpeta workspace.json agrupa modelos CATIA y casos CFD independientes. Cambiarla no restaura configuraciones globales ni altera otras carpetas. Las bibliotecas de perfiles y presets se comparten entre todos los casos.

## Funciones y entradas

workspace_app.render_workspaces llama a create_workspace, register_workspace y workspaces, en ramair_workspace.py. Crear recibe un nombre y carpeta contenedora. Registrar exige workspace.json. Los nombres se normalizan y las identidades UUID distinguen carpetas homónimas.

## Archivos

Workspaces/<nombre>/workspace.json, CATIA/, CFD_2D/; Workspaces/index.json registra ubicaciones adicionales. Cada CFD_2D/<caso>/cfd_case.json posee condiciones; Geometries/<perfil>/geometry.json posee una instantánea de puntos; Meshes/<malla> conserva su propia configuración, calidad y aprobación.

## Windows y WSL

El código editable es el checkout Windows. El cálculo se hace bajo el runtime Linux configurado (por defecto ~/ramair_cfd/DESIGN_APP/Workspaces). ramair_workspace_storage publica runtime.location.json, README y acceso .url en Windows. Selected Results contiene únicamente productos exportados. El enlace no es una copia de seguridad de los campos.

## Recuperación

Para trasladar una simulación copie los datos completos o use el paquete de ejecución remota. Copiar index.json no traslada resultados. Los antiguos Results y registros del laboratorio permanecen separados y se conservan en sus rutas documentadas.
