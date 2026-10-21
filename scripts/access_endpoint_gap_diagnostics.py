#!/usr/bin/env python3
"""Read-only fine-grained geometry diagnostics for historically mapped path gaps.

Reports minimum geometric separation of independently node-connected OSM groups.
Geometric proximity/crossings, OSM connectivity and publicly walkable legal access
are distinct facts. Never classify full-route legal access based on this output.
"""
import argparse
import json
import pathlib

from pyproj import Transformer
from shapely.geometry import Point
from shapely.ops import transform, unary_union, nearest_points
from full_way_topology_access import load_full_osm, grouped
from land_access_primary_parcels import WGS_TO_NZTM, lines_for, poly_from_rings
from land_access_herenga_pilot import LAYERS,request_features

TO_WGS = Transformer.from_crs("EPSG:2193","EPSG:4326",always_xy=True).transform

def evaluate(row):
    raw=lines_for(row)
    if len(raw)!=1:
        raise ValueError("Diagnostic deliberately limited to single historical path geometry")
    old=transform(WGS_TO_NZTM,raw[0])
    p=list(raw[0].coords)
    bbox=(min(x for x,y in p),min(y for x,y in p),max(x for x,y in p),max(y for x,y in p))
    full,metadata=load_full_osm(bbox)
    groups,nearby=grouped(full,old)
    if len(groups)<2:
        return {"row":row,"old_m":round(old.length,2),"groups":len(groups),
                "interpretation":"Fewer than two individually node-connected groups near historical line"}
    indexed={w["way"]:w for w in full}
    def summary(g):
        ways=[indexed[w] for w in g["ways"]]
        return {"ways":g["ways"],"old_within_10m_pct":g["old_within10m_pct"],
                "group_has_truncated_way_nodes":sum(w["truncated_nodes"] for w in ways),
                "way_tags":[{"way":w["way"],"highway":w["tags"].get("highway"),
                   "name":w["tags"].get("name"),"surface":w["tags"].get("surface"),
                   "access":w["tags"].get("access"),"foot":w["tags"].get("foot"),
                   "bridge":w["tags"].get("bridge"),"tunnel":w["tags"].get("tunnel"),
                   "layer":w["tags"].get("layer")}
                  for w in ways[:8]]}
    main=groups[0];second=groups[1]
    a=main["_modern"];b=second["_modern"]
    q1,q2=nearest_points(a,b)
    joining=round(q1.distance(q2),2)

    # Test whether the short gap between disconnected OSM ways and WAMS's
    # historical public-access map gap are actually co-located. This prevents
    # conflating two similar-sized gaps at different places.
    mid=Point((q1.x+q2.x)/2,(q1.y+q2.y)/2)
    yespolys=[]
    for layer in LAYERS:
        for feat in request_features(layer,bbox):
            attrs=feat.get("attributes",{})
            if str(attrs.get("public_access") or "").lower()!="yes":continue
            poly=poly_from_rings(feat.get("geometry",{}).get("rings",[]))
            if poly is not None and not poly.is_empty and poly.intersects(old.buffer(3)):
                yespolys.append(poly)
    public=unary_union(yespolys) if yespolys else None
    missing=old.difference(public) if public is not None else old
    pieces=list(missing.geoms) if hasattr(missing,"geoms") else ([] if missing.is_empty else [missing])
    missing_parts=sorted([{"length_m":round(g.length,2),
       "midpoint_wgs84":list(transform(TO_WGS,g.interpolate(.5,normalized=True)).coords)[0],
       "distance_from_osm_gap_m":round(g.distance(mid),2)}
       for g in pieces if g.geom_type=="LineString" and g.length>.25],
       key=lambda x:-x["length_m"])
    gap_public=[public is not None and public.buffer(.1).covers(pt) for pt in (q1,q2,mid)]
    x1=transform(TO_WGS,q1);x2=transform(TO_WGS,q2)
    u=unary_union([a.buffer(10),b.buffer(10)])
    uncovered=old.difference(u)
    bits=list(uncovered.geoms) if hasattr(uncovered,"geoms") else ([] if uncovered.is_empty else [uncovered])
    sections=sorted([{"length_m":round(g.length,2),
                      "midpoint_wgs84":list(transform(TO_WGS,g.interpolate(.5,normalized=True)).coords)[0]}
                    for g in bits if g.geom_type=="LineString"],key=lambda z:-z["length_m"])
    def endpoint_distance(g):
        return [round(transform(WGS_TO_NZTM,raw[0]).boundary.geoms[i].distance(g),2) for i in range(2)]
    return {"row":row,"historical_length_m":round(old.length,2),
      "group_count":len(groups),"best_groups":[summary(main),summary(second)],
      "best_group_ends_m":endpoint_distance(a),"other_group_ends_m":endpoint_distance(b),
      "minimum_between_groups_m":joining,
      "osm_gap_points_and_midpoint_in_wams_public_yes":gap_public,
      "wams_public_yes_pct_old":round(100*old.intersection(public).length/old.length,1) if public is not None else 0,
      "wams_unmapped_historic_segments":missing_parts[:10],
      "closest_group_points_wgs84":[list(x1.coords)[0],list(x2.coords)[0]],
      "distance_of_join_points_from_old_m":[round(q1.distance(old),2),round(q2.distance(old),2)],
      "combined_two_group_within10m_pct":round(100*old.intersection(u).length/old.length,1),
      "unmatched_historic_segments_after_two_groups":sections[:8],
      "warning":"No shared OSM node between groups. A small geometric gap or even crossing is NOT proof of an at-grade, formed, legally usable connection."}

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--rows",required=True)
    args=parser.parse_args()
    rows=[int(x) for x in args.rows.split(",") if x.strip()]
    if not 1<=len(rows)<=3:raise ValueError("Maximum 3 bounded historical lines per job")
    done=[];errors=[]
    for row in rows:
        try:
            z=evaluate(row);done.append(z)
            print("GAP_TOPOLOGY_JSON="+json.dumps(z,separators=(",",":")),flush=True)
        except Exception as exc:
            e={"row":row,"error":type(exc).__name__+": "+str(exc)}
            errors.append(e);print("GAP_TOPOLOGY_ERROR="+json.dumps(e),flush=True)
    pathlib.Path("endpoint_gap_diagnostic.json").write_text(json.dumps({"results":done,"errors":errors},indent=2),encoding="utf-8")
    print("GAP_TOPOLOGY_SUMMARY="+json.dumps({"requested":len(rows),"completed":len(done),"errors":len(errors)}),flush=True)
    if errors:raise SystemExit(1)
if __name__=="__main__":main()
