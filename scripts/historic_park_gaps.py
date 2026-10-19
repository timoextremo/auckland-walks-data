#!/usr/bin/env python3
"""Gap report for seven historic LINZ lines near public council walks.

READ-ONLY. OpenStreetMap is volunteered geometry and cannot independently
establish statutory access, walking permission, present closures, or safety.
Unmatched portions are GAPS IN THIS ONE DATASET, not closed tracks.
"""
import argparse,json,pathlib,sys
from datetime import datetime,timezone
from pyproj import Transformer
from shapely.geometry import LineString
from shapely.ops import unary_union,transform
from osm_small_bbox_check import load_old,fetch_osm

TO_LL=Transformer.from_crs(2193,4326,always_xy=True).transform
def gap_parts(geometry):
    if geometry.is_empty:return []
    if geometry.geom_type=="LineString":return [geometry]
    if hasattr(geometry,"geoms"):
        result=[]
        for p in geometry.geoms:result+=gap_parts(p)
        return result
    return []
def access_class(tags):
    h=tags.get("highway")
    foot=tags.get("foot")
    access=tags.get("access")
    if foot in ("no","private") or access=="private" and foot not in ("yes","designated","permissive"):
        return "exclusion_tag"
    if h=="cycleway" and foot not in ("yes","designated","permissive","official"):
        return "cycleway_foot_unspecified"
    return "possible_pedestrian"
def metric(old,lines,m):
    if not lines:return 0.
    return round(100*old.intersection(unary_union(lines).buffer(m)).length/old.length,2)
def out(row):
    old,bbox=load_old(row)
    ways,meta=fetch_osm(bbox)
    tracks=[(id,t,g,access_class(t)) for id,t,g in ways if g.distance(old)<=40]
    allpaths=[g for _,_,g,_ in tracks]
    compat=[g for _,_,g,k in tracks if k=="possible_pedestrian"]
    excluded=[g for _,_,g,k in tracks if k=="exclusion_tag"]
    cycleunk=[g for _,_,g,k in tracks if k=="cycleway_foot_unspecified"]
    unmap=old.difference(unary_union(allpaths).buffer(10)) if allpaths else old
    gaps=sorted(gap_parts(unmap),key=lambda g:-g.length)
    formatted=[]
    for part in gaps[:20]:
        coords=list(part.coords)
        if len(coords)<2:continue
        mid=part.interpolate(.5,normalized=True)
        lon,lat=transform(TO_LL,mid).coords[0]
        # The map users can use coordinates to inspect specific holes.
        if part.length<12:continue
        formatted.append({"length_m":round(part.length,1),"mid_lon":round(lon,6),"mid_lat":round(lat,6)})
    nearTags=[]
    for id,t,g,k in tracks:
        if g.distance(old)>20:continue
        nearTags.append({"id":id,"class":k,"name":t.get("name"),"highway":t.get("highway"),
            "foot":t.get("foot"),"access":t.get("access"),
            "mapped_10m_overlap_m":round(old.intersection(g.buffer(10)).length,1)})
    nearTags.sort(key=lambda x:-x["mapped_10m_overlap_m"])
    result={"row":row,"historic_km":round(old.length/1000,3),"api_tiles":meta.get("tiles"),
        "osm_10m_all_pct":metric(old,allpaths,10),
        "osm_10m_possible_foot_pct":metric(old,compat,10),
        "osm_10m_cycle_foot_unspecified_pct":metric(old,cycleunk,10),
        "osm_10m_exclusion_tag_pct":metric(old,excluded,10),
        "unmatched_10m_gap_count_over_12m":len(formatted),
        "unmatched_10m_gap_total_m":round(sum(g.length for g in gaps),1),
        "largest_unmatched_geographic_gaps":formatted[:8],
        "top_nearby_tags":nearTags[:16],
        "run_utc":datetime.now(timezone.utc).isoformat(),
        "warning":"Only OSM lines; no official permission, closures or on-the-ground condition certified."}
    return result
def main():
    p=argparse.ArgumentParser();p.add_argument("--rows",required=True);p.add_argument("--name",default="pilot");a=p.parse_args()
    rows=[int(x) for x in a.rows.split(",") if x]
    if not (1<=len(rows)<=3):raise SystemExit("max 3 rows")
    results=[];errors=[]
    for row in rows:
        try:
            r=out(row);results.append(r);print("GAP_RESULT="+json.dumps(r,separators=(",",":")),flush=True)
        except Exception as exc:
            errors.append({"row":row,"error":str(exc)});print("GAP_ERROR="+json.dumps(errors[-1]),flush=True)
    pathlib.Path("park_gap_"+a.name+".json").write_text(json.dumps({"results":results,"errors":errors},indent=2))
    if errors:sys.exit(1)
if __name__=="__main__":main()
