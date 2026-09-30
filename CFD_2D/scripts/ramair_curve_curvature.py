"""Signed curvature of the actual CAD curves passed to Gmsh."""
from pathlib import Path
import json
import numpy as np
import pandas as pd

def audit_wall_curvature(gmsh, curves, thickness_m, output: Path):
    rows=[]
    for tag in curves:
        bounds=gmsh.model.getParametrizationBounds(1,abs(int(tag)))
        lo,hi=float(bounds[0][0]),float(bounds[1][0])
        t=np.linspace(lo+1e-8*(hi-lo),hi-1e-8*(hi-lo),801)
        xyz=np.asarray(gmsh.model.getValue(1,abs(int(tag)),t)).reshape(-1,3)
        d1=np.asarray(gmsh.model.getDerivative(1,abs(int(tag)),t)).reshape(-1,3)
        d2=np.asarray(gmsh.model.getSecondDerivative(1,abs(int(tag)),t)).reshape(-1,3)
        speed=np.linalg.norm(d1[:,:2],axis=1)
        k=np.divide(d1[:,0]*d2[:,1]-d1[:,1]*d2[:,0],speed**3,out=np.zeros(len(t)),where=speed>1e-14)
        if tag<0:xyz=xyz[::-1];k=-k[::-1]
        rows.extend({'curve':int(tag),'x_m':float(p[0]),'y_m':float(p[1]),'curvature_per_m':float(v)} for p,v in zip(xyz,k))
    frame=pd.DataFrame(rows)
    xy=frame[['x_m','y_m']].to_numpy()
    area=np.sum(xy[:,0]*np.roll(xy[:,1],-1)-xy[:,1]*np.roll(xy[:,0],-1))/2
    frame['outward_curvature_per_m']=frame.curvature_per_m*np.sign(area)
    frame['offset_jacobian_at_BL_height']=1+frame.outward_curvature_per_m*float(thickness_m)
    output.mkdir(parents=True,exist_ok=True)
    frame.to_csv(output/'wall_curvature.csv',index=False)
    worst=frame.nsmallest(20,'offset_jacobian_at_BL_height').to_dict(orient='records')
    report={'source':'Gmsh getDerivative/getSecondDerivative on actual CAD curves, 801 samples per curve',
            'total_BL_height_m':thickness_m,'min_offset_jacobian':float(frame.offset_jacobian_at_BL_height.min()),
            'predicted_local_offset_folding':bool((frame.offset_jacobian_at_BL_height<=0).any()),'worst_locations':worst,
            'limitation':'Local normal-offset test, not proof of generated BL collision. Convex positive curvature expands outward layers; negative curvature compresses them. Global intersections and discretization require actual mesh checks.'}
    (output/'wall_curvature.json').write_text(json.dumps(report,indent=2))
    return report
