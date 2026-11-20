#!/usr/bin/env python3
"""Extract BIM floor slab top surfaces from an IFC file.

Pilot-tested against the structure of the N4.1 IFC2X3 export. Geometry
extraction requires IfcOpenShell and Shapely. Coordinates are exported in
metres in the IFC model's world/project coordinate system.
"""
from __future__ import annotations
import argparse, csv, json, re, sys
from pathlib import Path
from collections import defaultdict

try:
    import ifcopenshell
    import ifcopenshell.geom
    import ifcopenshell.util.unit
    from shapely.geometry import Polygon, MultiPolygon, mapping
    from shapely.ops import unary_union
except ImportError as exc:
    raise SystemExit(
        "Missing dependency. Install with:\n"
        "  python -m pip install ifcopenshell shapely\n"
        f"Original import error: {exc}"
    )


def safe_name(value: str) -> str:
    return re.sub(r'[^A-Za-z0-9_-]+', '_', value).strip('_') or 'unnamed'


def get_storey_elements(model):
    """Return {storey: [products]} using IFC spatial containment relationships."""
    mapping_by_storey = defaultdict(list)
    for rel in model.by_type('IfcRelContainedInSpatialStructure'):
        storey = getattr(rel, 'RelatingStructure', None)
        if storey and storey.is_a('IfcBuildingStorey'):
            mapping_by_storey[storey].extend(list(rel.RelatedElements or []))
    return mapping_by_storey


def polygonal(geom):
    if geom.is_empty:
        return None
    if geom.geom_type == 'Polygon':
        return geom
    if geom.geom_type == 'MultiPolygon':
        return geom
    parts = [g for g in getattr(geom, 'geoms', []) if g.geom_type == 'Polygon' and not g.is_empty]
    return unary_union(parts) if parts else None


def extract_top_surface(product, settings, upward_normal_min=0.90):
    """Return union of upward-facing mesh triangles projected to XY (metres)."""
    shape = ifcopenshell.geom.create_shape(settings, product)
    verts = shape.geometry.verts
    faces = shape.geometry.faces
    if not verts or not faces:
        return None
    points = [(float(verts[i]), float(verts[i+1]), float(verts[i+2]))
              for i in range(0, len(verts), 3)]
    triangles = []
    for i in range(0, len(faces), 3):
        ia, ib, ic = int(faces[i]), int(faces[i+1]), int(faces[i+2])
        a, b, c = points[ia], points[ib], points[ic]
        ux, uy, uz = b[0]-a[0], b[1]-a[1], b[2]-a[2]
        vx, vy, vz = c[0]-a[0], c[1]-a[1], c[2]-a[2]
        nx, ny, nz = uy*vz-uz*vy, uz*vx-ux*vz, ux*vy-uy*vx
        norm = (nx*nx + ny*ny + nz*nz) ** 0.5
        if norm == 0 or nz / norm < upward_normal_min:
            continue
        tri = Polygon([(a[0], a[1]), (b[0], b[1]), (c[0], c[1])])
        if tri.is_valid and tri.area > 1e-9:
            triangles.append(tri)
    if not triangles:
        return None
    return polygonal(unary_union(triangles).buffer(0))


def main():
    ap = argparse.ArgumentParser(description='Extract IFC floor slab top surfaces by storey.')
    ap.add_argument('ifc', help='Path to IFC/IFCZIP file')
    ap.add_argument('--storey', default='B4', help='Storey name or substring (default: B4)')
    ap.add_argument('--out', default='output/ifc_extraction', help='Output directory')
    ap.add_argument('--include-non-floor-slabs', action='store_true',
                    help='Include slabs whose PredefinedType is not FLOOR/USERDEFINED/NOTDEFINED')
    ap.add_argument('--normal-z', type=float, default=0.90,
                    help='Minimum upward face-normal Z component (default 0.90)')
    args = ap.parse_args()

    source = Path(args.ifc).expanduser().resolve()
    outdir = Path(args.out).expanduser().resolve()
    outdir.mkdir(parents=True, exist_ok=True)
    print(f'Opening IFC: {source}')
    model = ifcopenshell.open(str(source))
    unit_scale = ifcopenshell.util.unit.calculate_unit_scale(model)
    print(f'IFC schema: {model.schema}; project-unit-to-metre scale: {unit_scale}')

    storey_products = get_storey_elements(model)
    storeys = list(storey_products)
    wanted = args.storey.casefold().strip()
    aliases = {'b4': ('4th basement', 'basement 4', 'b4'),
               'basement 4': ('4th basement', 'basement 4', 'b4')}
    terms = aliases.get(wanted, (wanted,))
    matches = [s for s in storeys if any(term in str(getattr(s, 'Name', '') or '').casefold()
               or term in str(getattr(s, 'LongName', '') or '').casefold() for term in terms)]
    if not matches:
        print('Available storeys:')
        for s in storeys:
            print(f"  #{s.id()} {getattr(s,'Name',None)!r} elevation={getattr(s,'Elevation',None)}")
        raise SystemExit(f'No storey matched {args.storey!r}.')
    if len(matches) > 1:
        print('Multiple storeys matched; refine --storey:')
        for s in matches:
            print(f"  #{s.id()} {getattr(s,'Name',None)!r}")
        raise SystemExit(2)
    storey = matches[0]
    products = storey_products[storey]
    slabs = [p for p in products if p.is_a('IfcSlab')]
    accepted = {'.FLOOR.', '.USERDEFINED.', '.NOTDEFINED.', 'FLOOR', 'USERDEFINED', 'NOTDEFINED', None}
    if not args.include_non_floor_slabs:
        slabs = [p for p in slabs if str(getattr(p, 'PredefinedType', None)).upper() in accepted]

    print(f"Selected storey: {getattr(storey,'Name',None)} (#{storey.id()})")
    print(f"Storey elevation (IFC project units): {getattr(storey,'Elevation',None)}")
    print(f'Contained products: {len(products)}; candidate IfcSlab elements: {len(slabs)}')

    settings = ifcopenshell.geom.settings()
    # World coordinates ensure placements are applied before exporting XY geometry.
    settings.set(settings.USE_WORLD_COORDS, True)
    features, rows, failed, all_geometries = [], [], [], []
    total_area = 0.0
    for idx, slab in enumerate(slabs, 1):
        guid = getattr(slab, 'GlobalId', '') or f'entity_{slab.id()}'
        name = getattr(slab, 'Name', '') or ''
        ptype = str(getattr(slab, 'PredefinedType', None))
        try:
            geom = extract_top_surface(slab, settings, args.normal_z)
            if geom is None or geom.is_empty:
                failed.append({'ifc_id': slab.id(), 'guid': guid, 'name': name,
                               'reason': 'No upward-facing surface triangles generated'})
                continue
            # IfcOpenShell geometry coordinates are SI metres by default.
            area = float(geom.area)
            total_area += area
            all_geometries.append(geom)
            features.append({'type': 'Feature', 'properties': {
                'ifc_id': slab.id(), 'global_id': guid, 'name': name,
                'predefined_type': ptype, 'area_m2': area,
            }, 'geometry': mapping(geom)})
            rows.append({'ifc_id': slab.id(), 'global_id': guid, 'name': name,
                         'predefined_type': ptype, 'area_m2': f'{area:.4f}'})
        except Exception as exc:
            failed.append({'ifc_id': slab.id(), 'guid': guid, 'name': name,
                           'reason': f'{type(exc).__name__}: {exc}'})
        if idx % 10 == 0 or idx == len(slabs):
            print(f'Processed {idx}/{len(slabs)} slabs; extracted {len(features)}')

    geojson = {'type': 'FeatureCollection', 'name': str(getattr(storey, 'Name', 'storey')),
               'features': features}
    (outdir / 'b4_floor_surfaces.geojson').write_text(json.dumps(geojson), encoding='utf-8')
    unique_union_area = None
    if all_geometries:
        merged = polygonal(unary_union(all_geometries).buffer(0))
        unique_union_area = float(merged.area)
        union_fc = {'type': 'FeatureCollection', 'name': str(getattr(storey, 'Name', 'storey')) + '_union',
                    'features': [{'type': 'Feature', 'properties': {'storey': getattr(storey, 'Name', None),
                                  'unique_union_plan_area_m2': unique_union_area}, 'geometry': mapping(merged)}]}
        (outdir / 'b4_floor_union.geojson').write_text(json.dumps(union_fc), encoding='utf-8')
    with (outdir / 'b4_slab_areas.csv').open('w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=['ifc_id','global_id','name','predefined_type','area_m2'])
        writer.writeheader(); writer.writerows(rows)
    summary = {
        'source_ifc': source.name, 'schema': model.schema,
        'storey_name': getattr(storey, 'Name', None), 'storey_global_id': getattr(storey, 'GlobalId', None),
        'storey_elevation_ifc_units': getattr(storey, 'Elevation', None),
        'ifc_project_unit_to_metre': unit_scale,
        'contained_product_count': len(products), 'candidate_slab_count': len(slabs),
        'extracted_slab_count': len(features), 'failed_slab_count': len(failed),
        'sum_of_individual_top_surface_areas_m2': total_area,
        'union_of_extracted_slab_plan_area_m2': unique_union_area,
        'note': 'Union area removes geometric overlaps between extracted slab polygons, but is not automatically formal GFA. Review planter slabs, roads, ramps, voids, exclusions, and IFC geometry quality before accepting it.',
        'failed_elements': failed,
    }
    (outdir / 'extraction_summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    print('\nExtraction complete.')
    print(json.dumps({k:v for k,v in summary.items() if k != 'failed_elements'}, indent=2))
    print(f'Outputs: {outdir}')

if __name__ == '__main__':
    main()
