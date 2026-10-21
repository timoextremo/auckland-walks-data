#!/usr/bin/env python3
"""L2 endpoint, exact-public-polygon and on-route OSM restriction evidence.
This DOES NOT establish legal certification, physical condition or live access."""
import argparse, json, pathlib, requests, xml.etree.ElementTree as ET
from shapely.geometry import Point, LineString
from shapely.ops import transform, unary_union
from full_way_topology_access import load_full_osm, grouped, OSM_URL
from land_access_primary_parcels import lines_for, poly_from_rings, WGS_TO_NZTM
from land_access_herenga_pilot import LAYERS, request_features

BAD={'private','no','agricultural','forestry'}
def restrictions(bbox,old):
    x1,y1,x2,y2=bbox
    bb=[x1-.00045,y1-.00045,x2+.00045,y2+.00045]
    resp=requests.get(OSM_URL,params={'bbox':','.join(f'{x:.7f}' for x in bb)},
        headers={'User-Agent':'AucklandWalks/1.0 (read-only exact restriction audit)'},timeout=70)
    resp.raise_for_status()
    if len(resp.content)>15000000:raise RuntimeError('Excessive bounded OSM payload')
    root=ET.fromstring(resp.content)
    nodes={}
    for n in root.findall('node'):
        try:nodes[n.attrib['id']]=(float(n.attrib['lon']),float(n.attrib['lat']))
        except (KeyError,ValueError):pass
    found=[]
    for w in root.findall('way'):
        tags={t.attrib['k']:t.attrib['v'] for t in w.findall('tag')}
        hw=tags.get('highway','')
        if hw not in {'footway','path','pedestrian','steps','track','bridleway','cycleway'}:continue
        access=tags.get('access','').lower();foot=tags.get('foot','').lower()
        cyc=hw=='cycleway' and foot not in {'yes','designated','official','permissive'}
        if not (access in BAD or foot in BAD or cyc):continue
        p=[nodes[n.attrib['ref']] for n in w.findall('nd') if n.attrib.get('ref') in nodes]
        if len(p)<2:continue
        g=transform(WGS_TO_NZTM,LineString(p))
        if g.distance(old)>20:continue
        found.append({'way':w.attrib['id'],'highway':hw,'access':access,'foot':foot,
            'restricted_cycleway_foot_not_confirmed':cyc,
            'distance_to_old_m':round(g.distance(old),1),
            'historic_within_5m_m':round(old.intersection(g.buffer(5)).length,1),
            'historic_within_10m_m':round(old.intersection(g.buffer(10)).length,1)})
    return sorted(found,key=lambda r:-r['historic_within_5m_m'])[:15]
def audit(row):
    raw=lines_for(row)
    if not raw:raise ValueError('Missing historic KML geometry')
    xy=[p for l in raw for p in l.coords]
    bbox=(min(p[0] for p in xy),min(p[1] for p in xy),max(p[0] for p in xy),max(p[1] for p in xy))
    old=unary_union([transform(WGS_TO_NZTM,l) for l in raw])
    ends=[Point(WGS_TO_NZTM(*l.coords[j])) for l in raw for j in (0,-1)]
    full,health=load_full_osm(bbox); groups,_=grouped(full,old)
    og=[]
    for g in groups:
        modern=g.pop('_modern');g.pop('_portion')
        ep=[round(p.distance(modern),1) for p in ends]
        unmatched=old.difference(modern.buffer(10))
        parts=list(unmatched.geoms) if hasattr(unmatched,'geoms') else ([] if unmatched.is_empty else [unmatched])
        longest=max((x.length for x in parts if x.geom_type in {'LineString','LinearRing'}),default=0)
        og.append({'ways':g['ways'],'connected_way_count':g['part_count'],
            'old_within_5m_pct':round(100*old.intersection(modern.buffer(5)).length/old.length,1),
            'old_within_10m_pct':g['old_within10m_pct'],
            'end_distances_m':ep,'both_ends_near_10m':all(d<=10 for d in ep),
            'largest_unmatched_10m_m':round(longest,1),
            'geometry_candidate':g['old_within10m_pct']>=98 and all(d<=10 for d in ep)})
    og.sort(key=lambda x:(-x['old_within_10m_pct'],-int(x['both_ends_near_10m'])))
    hits=[];maybe=[];public=[]
    for layer,label in LAYERS.items():
        for f in request_features(layer,bbox):
            a=f.get('attributes',{});p=poly_from_rings(f.get('geometry',{}).get('rings',[]))
            if p is None or p.is_empty or not p.intersects(old.buffer(15)):continue
            cut=old.intersection(p);length=cut.length if not cut.is_empty else 0
            item={'layer':label,'id':a.get('OBJECTID'),'kind':a.get('paa_type'),
                'name':a.get('common_name'),'public_access':a.get('public_access'),
                'manager':a.get('managed_by'),'old_line_m':round(length,1),
                'endpoint_inside':[p.covers(q) for q in ends],
                'near_m':round(p.distance(old),1)}
            if length>=.5:
                hits.append(item)
                if str(a.get('public_access','')).lower()=='yes':public.append(p)
            elif item['name']:maybe.append(item)
    union=unary_union(public) if public else None
    # Union POLYGONS, not clipped historical line fragments; line-on-line
    # intersections can produce false zeroes due to floating precision.
    cov=100*old.intersection(union).length/old.length if union is not None else 0
    return {'row':row,'historic_length_m':round(old.length,1),
        'old_wgs84_endpoints':[[list(l.coords[i]) for i in (0,-1)] for l in raw],
        'public_yes_exact_old_pct':round(min(100,cov),1),
        'endpoints_within_mapped_public_yes':([union is not None and union.buffer(.1).covers(p) for p in ends]),
        'on_line_access_polygons':sorted(hits,key=lambda x:-x['old_line_m'])[:15],
        'named_nearby_not_on_line':maybe[:8],
        'unformed_road_on_old_line':[q for q in hits if 'unformed' in str(q['kind']).lower()],
        'connected_osm_groups':og[:5],'restricted_osm_nearby':restrictions(bbox,old),
        'source':'WAMS Public Access Areas; OSM API; historical LINZ KML',
        'caution':'Proximity and WAMS line overlap alone DO NOT certify legal full-route public access, formation, closures or route completion.'}
def main():
    a=argparse.ArgumentParser();a.add_argument('--rows',required=True);a.add_argument('--name',default='pilot')
    o=a.parse_args();rows=[int(x) for x in o.rows.split(',') if x.strip()]
    if not 1<=len(rows)<=4:raise ValueError('Use 1–4 track IDs per job')
    result=[];err=[]
    for row in rows:
        try:
            r=audit(row);result.append(r)
            print('ENDPOINT_L2_JSON='+json.dumps(r,separators=(',',':')),flush=True)
        except Exception as e:
            q={'row':row,'error':type(e).__name__+': '+str(e)};err.append(q)
            print('ENDPOINT_L2_ERROR='+json.dumps(q),flush=True)
    pathlib.Path('endpoint_l2_'+o.name+'.json').write_text(json.dumps({'results':result,'errors':err},indent=2),encoding='utf-8')
    print('ENDPOINT_L2_SUMMARY='+json.dumps({'requested':len(rows),'completed':len(result),'errors':len(err)}),flush=True)
    if err:raise SystemExit(1)
if __name__=='__main__':main()
