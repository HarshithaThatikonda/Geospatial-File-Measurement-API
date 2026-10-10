# Geospatial File Measurement API

A FastAPI service that accepts a **Shapefile (.zip)** or **KML**, extracts every feature, and returns
**area** (polygons) and **length** (lines) computed in a proper projected CRS — never in raw degrees.

## Setup

Requires Python 3.10+ (developed on 3.12).

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Open http://localhost:8000/docs for interactive Swagger UI.

Run tests: `python -m pytest -q` (26 tests: measurement maths, CRS selection, upload/error handling, pagination)

Optional env vars: `DATABASE_URL` (default SQLite `geo.db`), `UPLOAD_DIR`, `MAX_UPLOAD_BYTES` (default 50 MB),
`MAX_UNZIPPED_BYTES` (default 500 MB). Sample files can be regenerated with `python sample_data/make_samples.py`.

## API

### `POST /api/files/` — upload and process
Multipart form field `file`: a `.kml`, or a `.zip` containing a shapefile (`.shp/.shx/.dbf/.prj`).

```bash
curl -F "file=@sample_data/survey.kml" http://localhost:8000/api/files/
```
```json
{"id": "b419891c...", "filename": "survey.kml", "file_type": "kml", "status": "COMPLETED",
 "crs": "EPSG:4326", "feature_count": 3, "error": null, "created_at": "2026-10-10T11:07:39Z"}
```

| Code | Meaning |
|------|---------|
| 201 | Processed successfully |
| 400 | Empty file |
| 413 | Over size limit |
| 415 | Not a `.zip` / `.kml` |
| 422 | Readable upload but unprocessable (corrupt zip, no `.shp`, missing CRS, unsafe zip paths...). Body contains the record `id` and `error`; the failed record stays queryable. |

### `GET /api/files/{id}/` — file information
Returns the same object as above (`status` is `COMPLETED` or `FAILED`).

### `GET /api/files/{id}/measurements/?limit=100&offset=0` — per-feature measurements
Paginated (`limit` 1-1000). `summary` covers the **whole file**, not just the page. Returns `409` if the file `FAILED`.

```json
{
  "file_id": "b419891c...", "crs": "EPSG:4326", "total": 3, "limit": 100, "offset": 0,
  "summary": {"by_status": {"OK": 2, "NOT_APPLICABLE": 1},
              "by_geometry_type": {"Polygon": 1, "LineString": 1, "Point": 1},
              "total_area_sq_m": 953462.2337, "total_length_m": 2170.3694},
  "features": [
    {"index": 0, "layer": "survey", "geometry_type": "Polygon", "crs": "EPSG:4326",
     "geometry": {"type": "Polygon", "coordinates": [[[78.47, 17.4], "..."]]},
     "properties": {"Name": "plot-A", "kind": "parcel"},
     "measurement": {"status": "OK", "projected_crs": "EPSG:32644",
                     "area_sq_m": 953462.2337, "area_hectares": 95.346223, "perimeter_m": 3906.6263}},
    {"index": 1, "geometry_type": "LineString", "...": "...",
     "measurement": {"status": "OK", "projected_crs": "EPSG:32644", "length_m": 2170.3694, "length_km": 2.170369}},
    {"index": 2, "geometry_type": "Point", "...": "...",
     "measurement": {"status": "NOT_APPLICABLE", "reason": "No measurement defined for points"}}
  ]
}
```

**Measurement `status` values:** `OK`, `NOT_APPLICABLE` (points), `UNSUPPORTED` (empty/null geometry,
GeometryCollection...), `ERROR` (unexpected failure on that one feature). A bad feature never fails the file.

## Architecture

```
app/
  main.py              FastAPI app, table creation on startup
  api/files.py         HTTP layer only: validation, status codes, pagination
  services/
    processing.py      orchestration: upload -> parse -> measure -> persist
    parser.py          shapefile/KML reading, safe zip extraction, JSON-safe properties
    measurements.py    CRS selection + area/length maths (pure functions, no I/O)
  models.py            SQLAlchemy: UploadedFile 1-to-many Feature
  schemas.py           Pydantic response models
tests/                 unit tests (maths) + API tests (upload, errors, pagination)
```

**File-processing flow:** validate extension -> read bytes (size-capped) -> save to a temp dir -> (zip: safely extract,
find `.shp`) -> read each layer with GeoPandas/pyogrio -> require a CRS -> build features -> measure each -> store in DB
-> delete the temp dir.

**Measurement flow:** per feature, pick a projected CRS -> reproject (2D) -> read `.area` / `.length` in metres.
Multi-geometries are supported (areas/lengths summed). Z values are dropped before measuring.

**CRS handling:** per-feature choice, in this order:
1. Projected CRS with metre units -> used as-is (no needless reprojection).
2. Geographic CRS (e.g. EPSG:4326) or projected non-metre CRS (feet) -> the **UTM zone of the feature's centroid**
   (EPSG:326xx north / 327xx south).
3. Beyond 80 degrees latitude, where UTM is invalid -> UPS (EPSG:32661 / 32761).

The chosen CRS is reported in each measurement as `projected_crs`, so results are auditable.

## Design Decisions

- **Synchronous processing.** Parsing + measuring happens inside the request, and the result is returned immediately.
  Simple, and fine for typical survey files. The `status` field already exists so moving to background jobs later
  doesn't change the API contract (see Future scope).
- **Per-feature UTM instead of one CRS per file.** A single zone distorts badly for files spanning many zones;
  per-feature keeps error small. Cost: a feature straddling a zone boundary is measured in one zone (small error).
  Alternatives considered: equal-area projections (exact area but worse for length), and geodesic maths
  (`pyproj.Geod`, most accurate but the brief asked for projection). Tests use `Geod` as an **independent check**
  (within 0.1%).
- **Store features in the DB, delete the raw upload.** Makes `GET` endpoints fast and stateless. Geometry is stored as
  GeoJSON in the file's *original* CRS.
- **SQLite + SQLAlchemy.** Zero setup; switch via `DATABASE_URL`. Features use plain JSON columns rather than PostGIS to
  keep local setup trivial.
- **Failed uploads are recorded**, return 422, and stay queryable, which helps debugging.
- **Missing CRS is rejected**, not guessed. Silently assuming WGS84 could yield wrong areas that look plausible.
- **Security:** zip-slip protection, extracted-size cap (zip bombs), upload size cap, no stack traces in errors.
- **A shapefile `.zip` may hold several `.shp`** as long as they share a CRS; KML files expose every layer.

## Known limitations

- A shapefile holds one geometry type, so mixed-type test data uses KML.
- KML property names come from GDAL's KML driver (e.g. the placemark name appears as `Name`).
- Invalid polygons (self-intersections) are measured but flagged with a `warning`.
- `created_at` loses its timezone suffix on GET with SQLite.

## Learnings and Future scope

**Learnings**
- **Degrees are not metres.** Computing area directly on EPSG:4326 coordinates gives ~1e-4 instead of ~1e6 m^2 for a
  ~1 km square near Hyderabad. A test asserts the result is far from the naive value.
- **Validate against an independent method, and validate the validator.** I compared results with `pyproj.Geod`
  (geodesic maths). For polygons with holes, `Geod` gave a value 27% off, because it depends on ring orientation and can
  *add* the hole. Computing outer-minus-hole separately showed the API was right (within 0.1%) and the reference was
  wrong. The tests now use outer-minus-hole for that case.
- **GDAL's KML driver changes your data.** A placemark name written as `name` comes back as `Name`, and each KML
  folder becomes its own layer. The parser reads every layer.
- **pandas nulls are not JSON nulls.** `NaT` timestamps were serialised as the string `"NaT"` until a test caught it;
  null handling now goes through `pd.isna`.
- **Missing CRS is a data problem, not a default.** A shapefile without `.prj` is rejected with a clear message
  instead of assuming WGS84.

**Measured behaviour:** a 5,000-polygon shapefile processes in about 2.6 s on a laptop-class machine, and a
1,000-feature measurements page returns in under 0.1 s. This is why synchronous processing is acceptable for now, and
also where it would stop being acceptable (files in the hundreds of thousands of features).

**Future scope**
- Background processing (Celery/RQ) with status polling for very large files, plus streaming reads
- PostGIS for spatial queries and indexes
- More formats: GeoJSON, GeoPackage, DXF; `.kmz` upload
- Optional geodesic mode (`?method=geodesic`) for comparison with the projected result
- Return geometry reprojected to WGS84 on request; GeoJSON FeatureCollection export
- Auth, rate limiting, Docker + CI, Alembic migrations
