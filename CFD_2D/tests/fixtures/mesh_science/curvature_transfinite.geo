// Fixed TAREA-05 fixture: curvature sizing versus an explicit transfinite count.
Mesh.MshFileVersion = 2.2;
// Delaunay isolates the 1-D sizing interaction from Frontal-Delaunay timing.
Mesh.Algorithm = 5;
Mesh.MeshSizeFromPoints = 0;
Mesh.MeshSizeFromCurvature = 80;
Mesh.MeshSizeExtendFromBoundary = 0;
Point(1) = {0, 0, 0, 1};
Point(2) = {1, 0, 0, 1};
Point(3) = {0, 1, 0, 1};
Point(4) = {-1, 0, 0, 1};
Point(5) = {0, -1, 0, 1};
Circle(1) = {2, 1, 3};
Circle(2) = {3, 1, 4};
Circle(3) = {4, 1, 5};
Circle(4) = {5, 1, 2};
__TRANSFINITE_DIRECTIVE__
Curve Loop(1) = {1, 2, 3, 4};
Plane Surface(1) = {1};
Physical Curve("curved_wall") = {1, 2, 3, 4};
Physical Surface("fluid") = {1};
