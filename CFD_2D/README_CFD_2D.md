# CFD 2D · RamAir Studio 2026.09.30

API 28, solver schema 15, registro de laboratorio schema 10. El flujo general usa workspaces independientes; el laboratorio conserva su propia estructura y recuperación.

Consulte [manuales vigentes](../Documents%20and%20Manuals/Current/README.md), [ejecución](../Documents%20and%20Manuals/Current/07_EXECUTION.md), [convergencia RANS](../Documents%20and%20Manuals/Current/12_RANS_MESH_CONVERGENCE.md) y [postproceso](../Documents%20and%20Manuals/Current/08_POSTPROCESS.md).

La especificación física principal permanece en Documents and Manuals/CFD 2D/CFD_2D_TECHNICAL_SPECIFICATIONS.txt. La interfaz no extrapola polares ni considera un checkMesh PASS como validación científica. RANS usa iteraciones, URANS segundos; se exige producción estable suficiente para análisis frecuencial, convergencia temporal y resultados aceptados.

Mantenga configuraciones, geometría, malla, campos e historial juntos al trasladar un caso. El paquete de código excluye datos del usuario.
