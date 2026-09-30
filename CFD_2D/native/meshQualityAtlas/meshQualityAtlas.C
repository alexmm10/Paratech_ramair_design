// RamAir diagnostic exporter. Metrics are evaluated by OpenFOAM's own libmeshCheck.
#include "argList.H"
#include "Time.H"
#include "polyMesh.H"
#include "meshCheck.H"
#include "syncTools.H"
#include <fstream>
#include <iomanip>
#include <cmath>
using namespace Foam;

int main(int argc, char *argv[])
{
    argList::noParallel();
    argList::addOption("output", "directory", "Directory for immutable mesh-quality evidence");
    #include "setRootCase.H"
    #include "createTime.H"
    polyMesh mesh(IOobject(polyMesh::defaultRegion, runTime.name(), runTime, IOobject::MUST_READ));
    fileName output(args.optionRead<fileName>("output"));
    mkDir(output);
    const auto& cc=mesh.cellCentres(); const auto& fc=mesh.faceCentres();
    const auto& sf=mesh.faceAreas(); const auto& vol=mesh.cellVolumes();
    scalarField ortho(meshCheck::faceOrthogonality(mesh,sf,cc));
    scalarField skew(meshCheck::faceSkewness(mesh,mesh.points(),fc,sf,cc));
    scalarField weight(meshCheck::faceWeights(mesh,fc,sf,cc));
    scalarField ratio(meshCheck::volRatio(mesh,vol));
    scalarField det(meshCheck::cellDeterminant(mesh,mesh.geometricD(),sf,syncTools::getInternalOrCoupledFaces(mesh)));
    scalarField openness,aspect;
    meshCheck::cellClosedness(mesh,mesh.geometricD(),sf,vol,openness,aspect);
    scalarField cw(mesh.nCells(),1), cr(mesh.nCells(),1), cn(mesh.nCells(),0), cs(mesh.nCells(),0);
    labelList wf(mesh.nCells(),-1), rf(mesh.nCells(),-1);
    std::ofstream faces((output/"faces.csv").c_str()); faces<<std::setprecision(17);
    faces<<"face_id,owner,neighbour,x,y,z,weight,volume_ratio,nonorth_deg,skewness,patch\n";
    forAll(mesh.faces(), fi)
    {
        const label own=mesh.faceOwner()[fi];
        const label nei=fi<mesh.nInternalFaces()?mesh.faceNeighbour()[fi]:-1;
        word patch("internal");
        if(nei<0) { const auto& p=mesh.boundary()[mesh.boundary().whichPatch(fi)];
            if(p.type()=="empty") continue;
            patch=p.name(); }
        const scalar angle=std::acos(std::max(-1.0,std::min(1.0,ortho[fi])))*180.0/3.141592653589793;
        for(const label celli : {own,nei}) if(celli>=0) {
            if(weight[fi]<cw[celli]) {cw[celli]=weight[fi];wf[celli]=fi;}
            if(ratio[fi]<cr[celli]) {cr[celli]=ratio[fi];rf[celli]=fi;}
            cn[celli]=max(cn[celli],angle);cs[celli]=max(cs[celli],skew[fi]);
        }
        faces<<fi<<','<<own<<','<<nei<<','<<fc[fi].x()<<','<<fc[fi].y()<<','<<fc[fi].z()<<','
             <<weight[fi]<<','<<ratio[fi]<<','<<angle<<','<<skew[fi]<<','<<patch<<'\n';
    }
    std::ofstream cells((output/"cells.csv").c_str());cells<<std::setprecision(17);
    cells<<"cell_id,x,y,z,volume,determinant,weight,volume_ratio,nonorth_deg,skewness,aspect_ratio,weight_face_id,ratio_face_id,polygon\n";
    forAll(mesh.cells(), ci) {
        cells<<ci<<','<<cc[ci].x()<<','<<cc[ci].y()<<','<<cc[ci].z()<<','<<vol[ci]<<','<<det[ci]<<','
             <<cw[ci]<<','<<cr[ci]<<','<<cn[ci]<<','<<cs[ci]<<','<<aspect[ci]<<','<<wf[ci]<<','<<rf[ci]<<',';
        label front=-1;scalar area=-1;
        for(const label fi:mesh.cells()[ci]) if(mag(sf[fi].z())>area){front=fi;area=mag(sf[fi].z());}
        const auto& f=mesh.faces()[front];
        forAll(f,vi){if(vi)cells<<'|';const auto& p=mesh.points()[f[vi]];cells<<p.x()<<':'<<p.y();}
        cells<<'\n';
    }
    Info<<"Native quality exported for "<<mesh.nCells()<<" cells and "<<mesh.nFaces()<<" faces to "<<output<<endl;
    return 0;
}
