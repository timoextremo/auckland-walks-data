#!/usr/bin/env python3
"""Modern OSM footway vs mapped WAMS access-corridor audit.

Designed to catch cases where an old LINZ centreline misses the actual public
footpath, WITHOUT inferring a legal right to walk from OSM/community tags.
Retains both geometries, compares separate percent coverages and flags cases
for authoritative corridor/closure verification. No Google Sheets writes.
"""
import argparse
import json
import pathlib
import sys
from shapely.ops import unary_union, transform
from land_access_herenga_pilot import LAYERS, request_features
from land_access_primary_parcels import lines_for, poly_from_rings, WGS_TO_NZTM
from osm_small_bbox_check import fetch_osm

ELIGIBLE = {"footway", "path", "pedestrian", "steps", "track", "bridleway", "cycleway"}
NOT_FOOT = {"no", "private", "agricultural", "forestry"}
# A cycleway without a foot tag might be bicycles-only; do not treat it as a footway.

def run_row(row):
    old_lines=lines_for(row)
    if not old_lines: raise ValueError(f"No source geometry row={row}")
    old=unary_union([transform(WGS_TO_NZTM, line) for line in old_lines])
    coords=[p for seg in old_lines for p in seg.coords]
    bbox=(min(x for x,y in coords),min(y for x,y in coords),
          max(x for x,y in coords),max(y for x,y in coords))
    ways, osm_meta=fetch_osm(bbox)
    corridor=old.buffer(15)
    usable=[];ignored=[]
    for way_id,tags,geom in ways:
        highway=tags.get("highway")
        if highway not in ELIGIBLE or old.distance(geom)>15: continue
        foot=tags.get("foot","").lower()
        access=tags.get("access","").lower()
        # Excluded footways are retained in ignored clues, never counted as
        # corroborating a legitimate pedestrian passage.
        excluded=(foot in NOT_FOOT or access in {"private","no"} or
                  (highway=="cycleway" and foot not in {"yes","designated","permissive","official"}))
        seg=geom.intersection(corridor)
        if seg.is_empty or seg.length<1: continue
        item={"way":str(way_id),"highway":highway,"name":tags.get("name"),
              "foot":tags.get("foot"),"access":tags.get("access"),
              "surface":tags.get("surface"),"operator":tags.get("operator"),
              "metres_near_old":round(seg.length,1)}
        if excluded:
            ignored.append(item)
        else:
            usable.append((seg,item))
    modern=unary_union([x for x,_ in usable]) if usable else None
    # Load polygons near BOTH the old centreline and the modern centreline.
    # A polygon can be 10m from historical line but overlap a modern footway.
    yes_polys=[];others=[];layers={}
    for index,label in LAYERS.items():
        fs=request_features(index,bbox)
        layer_items=[]
        for f in fs:
            poly=poly_from_rings(f.get("geometry",{}).get("rings",[]))
            if poly is None or poly.is_empty or not poly.intersects(corridor.buffer(10)):continue
            prop=f.get("attributes",{})
            flag=str(prop.get("public_access") or "missing").lower()
            d={"name":prop.get("common_name"),"kind":prop.get("paa_type"),
               "access":flag,"objectid":prop.get("OBJECTID")}
            if flag=="yes":yes_polys.append(poly)
            else:others.append(d)
            layer_items.append(d)
        if layer_items:layers[label]=layer_items[:5]
    area=unary_union(yes_polys) if yes_polys else None
    old_covered=old.intersection(area).length if area else 0.0
    modern_all=modern.length if modern is not None else 0.0
    modern_covered=modern.intersection(area).length if modern is not None and area else 0.0
    nearby_old=old.intersection(modern.buffer(10)).length if modern is not None else 0.0
    historical_yes=round(100*old_covered/old.length,1)
    modern_yes=round(100*modern_covered/modern_all,1) if modern_all>0 else None
    overlap=round(100*nearby_old/old.length,1)
    shift=round(modern_yes-historical_yes,1) if modern_yes is not None else None
    # CORRIDOR_CANDIDATE is a geometric clue, not a legality classification.
    candidate=bool(modern_all>5 and overlap>=85 and modern_yes is not None and
                   modern_yes>=90 and (modern_yes-historical_yes)>=20)
    return {"row":row,"old_length_m":round(old.length,1),
      "old_wams_yes_pct":historical_yes,
      "modern_path_around_old_m":round(modern_all,1),
      "old_overlap_modern_10m_pct":overlap,
      "modern_path_wams_yes_pct":modern_yes,
      "difference_pct_points":shift,
      "potential_cadastral_centreline_offset":candidate,
      "modern_way_examples":[r for g,r in usable][:12],
      "excluded_way_examples":ignored[:8],
      "wams_nearby_by_layer":layers,
      "osm_source":osm_meta.get("api"),
      "wams_source":"https://services2.arcgis.com/b5ADKIcWivL5vNaV/ArcGIS/rest/services/Public_Access_Areas/FeatureServer",
      "interpretation":"A GIS-only lead. Neither OSM presence nor WAMS overlap certifies legal public pedestrian rights, physical continuity, restrictions, opening, or track completion."}

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--rows",required=True)
    ap.add_argument("--name",default="pilot")
    args=ap.parse_args()
    rows=[int(q) for q in args.rows.split(",") if q]
    if len(rows)>5:raise ValueError("At most five rows per job")
    results=[];errors=[]
    for row in rows:
        try:
            r=run_row(row)
            results.append(r)
            print("CORRIDOR_JSON="+json.dumps(r,separators=(",",":")),flush=True)
        except Exception as e:
            x={"row":row,"error":type(e).__name__+": "+str(e)}
            errors.append(x);print("CORRIDOR_ERROR="+json.dumps(x),flush=True)
    pathlib.Path("corridor_"+args.name+".json").write_text(json.dumps({"results":results,"errors":errors},indent=2),encoding="utf-8")
    print("CORRIDOR_SUMMARY="+json.dumps({"requested":len(rows),"completed":len(results),"failed":len(errors)}),flush=True)
    if errors:sys.exit(1)

if __name__=="__main__":main()
