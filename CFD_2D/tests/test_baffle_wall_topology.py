from pathlib import Path
import sys
import numpy as np
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from openfoam_wall_analysis import _classify_wall_samples
from wall_separation_analysis import split_connected_wall_branches


def test_coincident_faces_keep_four_distinct_connected_wall_branches():
    points=np.array([[.05,-.02],[.3,-.06],[.7,-.03],[1,0],[.7,.07],[.3,.12],[.08,.09]])
    rows=[]
    for a,b in zip(points[:-1],points[1:]):
        normal=np.array([-(b-a)[1],(b-a)[0]])
        for sign in (1,-1):
            rows.append(dict(x_m=(a[0]+b[0])/2,y_m=(a[1]+b[1])/2,patch='airfoil_wall',
                edge_x0_m=a[0],edge_y0_m=a[1],edge_x1_m=b[0],edge_y1_m=b[1],
                normal_x=sign*normal[0],normal_y=sign*normal[1],Cp=-1 if sign==1 else .9))
    data=_classify_wall_samples(pd.DataFrame(rows),1)
    assert set(data.wall_side)=={'internal','external'}
    assert set(data[data.wall_side=='external'].Cp)=={-1}
    assert set(data[data.wall_side=='internal'].Cp)=={.9}
    branches,audit=split_connected_wall_branches(data,1)
    assert len(branches)==12
    assert set(branches.branch_id)=={'upper_external','lower_external','upper_internal','lower_internal'}
    assert branches.groupby('branch_id').size().eq(3).all()
    assert audit['method']=='oriented_baffle_face_connectivity'


def test_no_normals_preserves_legacy_classification():
    data=pd.DataFrame(dict(x_m=[0,.5,1],y_m=[0,.1,0],patch=['upper_external']*3))
    result=_classify_wall_samples(data,1)
    assert (result.surface=='upper').all() and (result.wall_side=='external').all()
