#!/usr/bin/env python3
"""Summarise the exact historical KML geometry without interpreting access."""
import argparse, json, pathlib, xml.etree.ElementTree as ET
from pyproj import Transformer
from shapely.geometry import LineString
from shapely.ops import transform,unary_union
TX=Transformer.from_crs("EPSG:4326","EPSG:2193",always_xy=True).transform
def inspect(row):
  root=ET.parse(pathlib.Path(f"map-data/auckland-walks/clusters/row-{row}.kml")).getroot()
  lines=[];pts=[]
  for e in root.iter():
    if e.tag.split("}")[-1]!="LineString":continue
    coords=next((c for c in e.iter() if c.tag.split("}")[-1]=="coordinates"),None)
    if coords is None or not coords.text:continue
    xy=[tuple(map(float,p.split(",")[:2])) for p in coords.text.strip().split()]
    if len(xy)<2:continue
    lines.append(transform(TX,LineString(xy)));pts.extend(xy)
  if not lines:raise ValueError(f"missing line geometry {row}")
  lo=min(x for x,y in pts);hi=max(x for x,y in pts)
  south=min(y for x,y in pts);north=max(y for x,y in pts)
  return {"source_row":row,"bbox_wgs84":[round(v,6) for v in (lo,south,hi,north)],
    "line_parts":len(lines),"length_m":round(unary_union(lines).length,2),
    "center_wgs84":[round((lo+hi)/2,6),round((south+north)/2,6)]}
def main():
  p=argparse.ArgumentParser();p.add_argument("--rows",required=True);a=p.parse_args()
  for raw in a.rows.split(","):
    obj=inspect(int(raw))
    print("HISTORICAL_KML_GEOMETRY="+json.dumps(obj,separators=(",",":")),flush=True)
if __name__=="__main__":main()
