#!/usr/bin/env python3
"""Compare each historical LINZ section against INDIVIDUAL official route features.

Read-only. Never infer walkability, lawful entry, closures, or route completion
from spatial correspondence alone. Council Akl Paths geometry is not fetched.
"""
import argparse,json,pathlib
from shapely.ops import unary_union
from shapely.geometry import Point
import oac_tracks_match as oac
import doc_open_track_match as doc

def as_metric_feature(geom, old):
    if geom is None or geom.is_empty: return None
    n=old.length
    coverage={str(t):round(100*old.intersection(geom.buffer(t)).length/n,1) for t in [5,10,20,30]}
    # Exact old KML endpoints by constituent line, not a topology-chosen arbitrary path.
    lines=list(old.geoms) if hasattr(old,"geoms") else [old]
    endpoints=[Point(line.coords[j]) for line in lines for j in [0,-1]]
    ends={str(t):sum(p.distance(geom)<=t for p in endpoints) for t in [10,20]}
    unmatched=old.difference(geom.buffer(10))
    uns=list(unmatched.geoms) if hasattr(unmatched,"geoms") else ([unmatched] if not unmatched.is_empty else [])
    longest=max([p.length for p in uns if p.geom_type in ("LineString","LinearRing")]+[0])
    return {"coverage_pct":coverage,"endpoints_near":ends,"historic_endpoints_tested":len(endpoints),
            "largest_unmatched_10m_component_m":round(longest,1),"near_m":round(old.distance(geom),1),
            "old_route_m":round(old.length,1)}

def audit(row):
    old=oac.route(row)
    service,meta,disc=oac.resolve()
    by_feature=[]
    for layer,label in oac.LAYERS.items():
        feats=oac.request_features(service,layer,old)
        for f in feats:
            pieces=oac.lines_from_feature(f)
            if not pieces:continue
            geom=unary_union(pieces)
            result=as_metric_feature(geom,old)
            if result["coverage_pct"]["30"]<=0:continue
            a=f.get("attributes",{})
            by_feature.append({"dataset":label,"source_id":a.get("OBJECTID"),
              "name":a.get("Name") or a.get("name") or "(unnamed)","status":a.get("Status"),
              "source":a.get("Source"),"match":result})
    docresults=[]
    for f in doc.fetch_doc_tracks(old):
        geom=unary_union(doc.feature_lines(f)) if doc.feature_lines(f) else None
        metric=as_metric_feature(geom,old)
        if not metric or metric["coverage_pct"]["30"]<=0:continue
        a=f.get("attributes",{})
        docresults.append({"dataset":"DOC published walking experiences","source_id":a.get("OBJECTID"),
              "name":a.get("name"),"alerts_flag":a.get("hasAlerts"),"webpage":a.get("walkingAndTrampingWebPage"),
              "match":metric})
    return {"row":row,"historical_length_m":round(old.length,1),"region_service":service,
        "regional":sorted(by_feature,key=lambda q:-q["match"]["coverage_pct"]["10"])[:14],
        "doc":sorted(docresults,key=lambda q:-q["match"]["coverage_pct"]["10"])[:10],
        "interpretation":"Per-feature source geometry only. Do not combine fragments from different named routes into a single named full route. DOC/council conditions/closures must be checked independently."}

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--rows",required=True);p.add_argument("--name",default="pilot")
    a=p.parse_args()
    rows=[int(x) for x in a.rows.split(",") if x.strip()]
    if len(rows)>5:raise ValueError("Max five rows")
    results=[];errors=[]
    for row in rows:
        try:
            x=audit(row);results.append(x);print("NAMED_ROUTE_JSON="+json.dumps(x,separators=(",",":")),flush=True)
        except Exception as exc:
            d={"row":row,"error":type(exc).__name__+": "+str(exc)}
            errors.append(d);print("NAMED_ROUTE_ERROR="+json.dumps(d),flush=True)
    pathlib.Path("named_route_alignment_"+a.name+".json").write_text(json.dumps({"results":results,"errors":errors},indent=2),encoding="utf-8")
    print("NAMED_ROUTE_SUMMARY="+json.dumps({"completed":len(results),"errors":len(errors)}),flush=True)
    if errors:raise SystemExit(1)

if __name__=="__main__":main()
