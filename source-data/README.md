# Source data and provenance

This repository supports the Auckland Walks database and its map/geometry audit.

## Privacy

Historical workout GPS files are **not stored in this public repository**. GPS matching is performed privately against route geometry, and only derived route-coverage percentages / completion evidence are written to the private working spreadsheet.

## LINZ Topo50 track centrelines

Source: Land Information New Zealand (LINZ), **NZ Track Centrelines (Topo, 1:50k)**.

Official layer: https://data.linz.govt.nz/layer/50364-nz-track-centrelines-topo-150k/

The KML files under `map-data/auckland-walks/` are derived from the LINZ `t50_fid` geometries retained by the audit. They are used as exact map overlays for the anonymous/unmatched LINZ foot-track rows.

## OpenStreetMap walking-route geometry

OpenStreetMap is used only as a **secondary gap-filling geometry source** where an official Auckland Council, DOC or LINZ route geometry is unavailable or does not cleanly describe the promoted route.

Snapshot query used for the September 2026 audit:

```overpass
[out:json][timeout:180];
relation["route"~"^(hiking|walking|foot)$"](-37.4,174.0,-35.8,176.0);
(._;>;);
out body geom;
```

Data © OpenStreetMap contributors, licensed under ODbL: https://www.openstreetmap.org/copyright

OSM route identity is checked against the spreadsheet name/location and against higher-authority sources before it can establish a completion.

## Auckland Council

Auckland Council public park/track pages and public GIS services are used for current route names, status and line geometry where appropriate. Council data may have source-specific terms; raw Council datasets are not intended to be republished here merely because they were used during the audit.

The physical `TrackTrailCycleway` GIS layer is particularly useful for confirming track components, but a GIS line named like a route is **not automatically treated as the complete promoted walk**. Component-vs-route scale is checked before assigning completion or GPS coverage.

AKL Paths application data has its own Auckland Council terms and should not be treated as an unrestricted public dataset.

## Department of Conservation (DOC)

DOC Walking and Tramping Experiences geometry is used for relevant conservation routes, with the current DOC route page retained as the preferred public reference where possible.

## Pukekohe Five Summits Trail

The September 2026 audit used the published updated Five Summits GPX (including Te Ara o Puuriri) from Wilderness Magazine as a measurement reference. The third-party GPX itself is not retained here as a permanent redistributed dataset; the database records the source and derived coverage result.

## GPS coverage method

- Primary corridor: **25 m** around a recorded foot workout, allowing for consumer-GPS drift.
- Stricter cross-check: **15 m** where useful.
- `GPS best outing %`: proportion of the canonical route geometry covered by the single best available foot workout.
- `GPS cumulative %`: proportion covered by the union of all available foot workouts.
- A blank percentage means canonical route geometry is not yet resolved well enough to measure.
- `0%` means the route geometry was actually measured and no archived foot-workout GPS covered it.
- GPS coverage is evidence; human completion ticks remain the authoritative completion record.
