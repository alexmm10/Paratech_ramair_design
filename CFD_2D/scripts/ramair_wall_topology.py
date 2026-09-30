"""Separate coincident baffle faces using oriented polygons and real connectivity."""
import numpy as np


def classify_connected_faces(data, chord):
    required={'edge_x0_m','edge_y0_m','edge_x1_m','edge_y1_m','normal_x','normal_y'}
    if not required.issubset(data):return data
    data=data.copy()
    tolerance=max(abs(chord)*1e-9,1e-12)
    def node(x,y):return (int(round(x/tolerance)),int(round(y/tolerance)))
    keys=[];edges={};adj={}
    for row in data.itertuples():
        a=node(row.edge_x0_m,row.edge_y0_m);b=node(row.edge_x1_m,row.edge_y1_m)
        key=tuple(sorted((a,b)));keys.append(key)
        if a==b or key in edges:continue
        edges[key]=(a,b)
        adj.setdefault(a,[]).append(key);adj.setdefault(b,[]).append(key)
    if any(len(v)>2 for v in adj.values()):return data
    remaining=set(edges);mapping={};component=0
    while remaining:
        seed=min(remaining);connected=set();stack=[seed]
        while stack:
            edge=stack.pop()
            if edge in connected:continue
            connected.add(edge)
            for n in edges[edge]:stack.extend(e for e in adj[n] if e not in connected)
        endpoints=[n for n in adj if len(adj[n])==1 and adj[n][0] in connected]
        current=min(endpoints) if endpoints else min(edges[seed]);nodes=[current];ordered=[]
        unused=set(connected)
        while unused:
            possible=[e for e in adj[current] if e in unused]
            if not possible:break
            e=min(possible);unused.remove(e);ordered.append(e)
            a,b=edges[e];current=b if current==a else a;nodes.append(current)
        remaining.difference_update(connected)
        if len(ordered)<3 or unused:continue
        xy=np.asarray(nodes,dtype=float)*tolerance
        area=float(np.sum(xy[:,0]*np.roll(xy[:,1],-1)-xy[:,1]*np.roll(xy[:,0],-1)))
        if abs(area)<tolerance**2:continue
        if not endpoints:
            # Start at LE so the two connected branches meet only at LE/TE.
            le=int(np.argmin(xy[:-1,0]));ordered=ordered[le:]+ordered[:le]
            nodes=nodes[le:-1]+nodes[:le]+[nodes[le]];xy=np.asarray(nodes)*tolerance
        te=int(np.argmax(xy[:,0]));first=ordered[:te];second=ordered[te:]
        means=[np.mean([.5*(edges[e][0][1]+edges[e][1][1]) for e in part]) if part else -np.inf for part in (first,second)]
        upper=0 if means[0]>=means[1] else 1
        for i,e in enumerate(ordered):
            delta=xy[i+1]-xy[i]
            # OpenFOAM boundary normals point out of fluid. Exterior fluid is
            # directed into a CCW enclosed profile (left of its directed edge).
            inward=np.sign(area)*np.array([-delta[1],delta[0]])
            part=0 if i<te else 1
            order=i if part==0 else len(ordered)-1-i
            mapping[e]=(inward,'upper' if part==upper else 'lower',order,component)
        component+=1
    counts={}
    for key in keys:counts[key]=counts.get(key,0)+1
    for position,key in enumerate(keys):
        if key not in mapping:continue
        expected,surface,order,component=mapping[key];idx=data.index[position]
        explicit=str(data.loc[idx,'wall_side'])
        if explicit=='combined':
            normal=data.loc[idx,['normal_x','normal_y']].to_numpy(float)
            side='external' if counts[key]==1 or np.dot(normal,expected)>0 else 'internal'
            data.loc[idx,'wall_side']=side
        data.loc[idx,'surface']=surface
        data.loc[idx,'topology_branch_order']=order
        data.loc[idx,'component_id']=component
    data['wall_classification']='oriented_face_connectivity'
    return data
