# BIM Point Cloud Analyzer — IFC extraction pilot

This is the first reusable module for extracting floor surfaces from IFC files. It is designed to accept different IFC projects; the pilot selection defaults to B4 only because that is the current test floor.

## Pilot IFC findings

The supplied `N4(1).ifc` is IFC2X3 and was exported by Autodesk Revit 2023. The B4 storey is named `4TH BASEMENT FLOOR PLAN`, has IFC entity id `#104`, and an IFC elevation of approximately `35000` project units. Its spatial containment relationship lists 458 contained references, including 75 `IfcSlab` elements; all 75 are typed `.FLOOR.`. These counts are structural IFC findings, not yet verified extracted surface areas.

## Setup

Use Python 3.10 or 3.11 in a fresh virtual environment:

```bash
py -3.11 -m venv .venv
.venv\\Scripts\\activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

If pip cannot find a compatible IfcOpenShell wheel, install IfcOpenShell from its official installation instructions or use a conda-forge environment. Do not use a random third-party wheel.

## Run B4 extraction

Place the IFC somewhere accessible, then run from this folder:

```bash
python modules/ifc_floor_extractor.py "C:\\path\\to\\N4(1).ifc" --storey "4TH BASEMENT FLOOR PLAN" --out output/B4
```

Or use the shorter match `--storey B4` only if the storey name itself contains B4. In the supplied IFC, use the exact name shown above because its IFC name is `4TH BASEMENT FLOOR PLAN`.

Outputs:

- `b4_floor_surfaces.geojson` — upward-facing slab surfaces projected to XY in metres.
- `b4_slab_areas.csv` — per-slab projected areas.
- `extraction_summary.json` — processing counts, failures, and area sum.

## Important limitations before using results

- The IFC model's world coordinates are preserved. Do not shift the geometry to match E57 until alignment is measured.
- The exported geometry is the union of upward-facing mesh triangles per slab. It is a prototype extraction, not yet an approved GFA calculation.
- The sum of individual slab areas can double-count overlaps. Unique floor plan area must be calculated from the union of polygons and reviewed for planter slabs, roads, ramps, openings, and non-occupiable areas.
- The 35,000 elevation is an IFC project-coordinate value. The E57 may use a different origin, axis orientation, or unit; verify before filtering points.
- No E57 data has been processed yet. This stage only prepares the BIM reference geometry.
