# Auckland Walks map data

This is a deliberately minimal public geometry host for the **Auckland Walks** database.

## What is kept here

`map-data/auckland-walks/clusters/row-<ROW>.kml`

Each KML contains the public LINZ geometry for one retained anonymous foot-track row in the Auckland Walks spreadsheet. The row number is part of the live link contract: the spreadsheet builds its Topo map links from these filenames, so they should not be renamed or moved unless the sheet formulas are updated at the same time.

Source geometry: [LINZ — NZ Track Centrelines Topo 1:50k](https://data.linz.govt.nz/layer/50364-nz-track-centrelines-topo-150k/).

## What does not belong here

This repository must not contain personal GPS/workout archives, home or private start-location data, spreadsheet exports, audit scratch files, temporary source dumps, or other material that is not required by the live public geometry links.

The repository exists only to provide stable public raw-KML URLs for map viewing.