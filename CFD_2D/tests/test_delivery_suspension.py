import copy
import json
from types import SimpleNamespace
import numpy as np
import pytest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]/"CFD_2D/scripts"))
from ramair_suspension_layout import migrate, select_brake_cells, build_network


def ribs(ys):
    return [{"rib_id": i+1, "Y_flat_mm": y} for i, y in enumerate(ys)]


@pytest.mark.parametrize("ys", [[-5,-3,-1,1,3,5], [-5,-3,0,3,5], [-5,-4.8,-1,0,1,4.8,5]])
def test_real_cells_cover_requested_span_and_unique_boundaries(ys):
    config = migrate({}, new=True)
    result = select_brake_cells(ribs(ys), config["brake_selection"])
    for side in result["sides"].values():
        assert .33 <= side["effective_fraction"] <= 1
        assert side["attachments"] == len(set(side["rib_ids"]))
    assert select_brake_cells(ribs(ys), json.loads(json.dumps(config))["brake_selection"]) == result


def test_discontinuous_asymmetric_selection_does_not_fill_gap():
    result = select_brake_cells(ribs([-4,-3,-2,-1,0,1,2,3,4]),
        {"mode":"cells", "symmetric":False,"cells_L":[1,3],"cells_R":[8]})
    assert result["sides"]["L"]["cells"] == [1,3]
    assert result["sides"]["L"]["rib_ids"] == [1,2,3,4]
    assert result["sides"]["R"]["rib_ids"] == [8,9]


def test_legacy_preserved_and_invalid_selection_rejected():
    original = {"loaded_rib_selection":{"mode":"rib_ids","rib_ids":[2,4]}}
    migrated = migrate(original)
    assert migrated["loaded_rib_selection"] == original["loaded_rib_selection"]
    assert migrated["brake_selection"]["mode"] == migrated["cascade_layout"]["template"] == "legacy"
    for selection in ({"mode":"outer_fraction","fraction_L":1.1}, {"mode":"cells","cells_L":[20]}, {"mode":"cells","cells_L":[4]}):
        with pytest.raises(ValueError): select_brake_cells(ribs([-2,-1,0,1,2]), selection)


def test_chordwise_one_brake_confluence_and_singletons():
    def node(nid, kind, x,y,z, bank="", side="L", rib_id="", level=0, line_type="structural", *args):
        return SimpleNamespace(node_id=nid,node_type=kind,p=np.array([x,y,z]),x=x,y=y,z=z,bank=bank,side=side,rib_id=rib_id,line_type=line_type)
    nodes = {"front":node("front","riser",0,-1,-10),"rear":node("rear","riser",1,-1,-10),"brake":node("brake","riser",2,-1,-10)}
    for station in (1,2,3):
        for bank,x in (("A",0),("B",.3),("C",.6),("D",.8),("BRK",1)):
            nid=f"{bank}{station}"
            nodes[nid]=node(nid,"anchor",x,-station,0,bank,"L",station,0,"brake" if bank=="BRK" else "structural")
    del nodes["B3"]
    edges=[]
    network=SimpleNamespace(cfg={"cascade_layout":{"template":"chordwise"},"banks":[{"name":b} for b in ("A","B","C","D","BRK")]},nodes=nodes,
        _target_for=lambda bank,side: "front" if bank in ("A","B") else "rear" if bank in ("C","D") else "brake",
        add_segment=lambda a,b,*args:edges.append((a,b)))
    build_network(network,node)
    cascades=[n for n in nodes.values() if n.node_type=="cascade"]
    assert len([n for n in cascades if n.line_type=="brake"]) == 1
    assert ("A3","front") in edges
    assert len(edges)==len(set(edges))
    assert all(np.linalg.norm(nodes[a].p-nodes[b].p)>0 for a,b in edges)
    assert all(sum(a==n.node_id for a,b in edges)==1 for n in nodes.values() if n.node_type in ("anchor","cascade"))
