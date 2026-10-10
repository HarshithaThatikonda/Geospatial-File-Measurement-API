"""Measurement logic.

CRS strategy
------------
Areas and lengths are never computed in degrees.

* Geographic CRS (e.g. EPSG:4326): each feature is reprojected to the UTM zone
  that contains its centroid (EPSG:326xx north / 327xx south). Near the poles
  (|lat| > 80) UTM is invalid, so we use UPS (EPSG:32661 / 32761).
* Projected CRS with metre units: used directly.
* Projected CRS with non-metre units (feet, etc.): reprojected to UTM like a
  geographic CRS, so the output is always in metres / square metres.

Per-feature UTM keeps distortion low even for files spanning several zones.
"""
from __future__ import annotations

import numpy as np
from pyproj import CRS, Transformer
import shapely
from shapely import force_2d
from shapely.geometry.base import BaseGeometry

POLYGONAL = {"Polygon", "MultiPolygon"}
LINEAR = {"LineString", "MultiLineString", "LinearRing"}
POINTLIKE = {"Point", "MultiPoint"}


def is_metric_projected(crs: CRS) -> bool:
    if not crs.is_projected:
        return False
    return all(a.unit_name in ("metre", "meter") for a in crs.axis_info)


def utm_crs_for(lon: float, lat: float) -> CRS:
    if lat > 80:
        return CRS.from_epsg(32661)  # UPS North
    if lat < -80:
        return CRS.from_epsg(32761)  # UPS South
    zone = int((lon + 180) // 6) + 1
    zone = min(max(zone, 1), 60)
    return CRS.from_epsg((32600 if lat >= 0 else 32700) + zone)


def _centroid_lonlat(geom: BaseGeometry, src: CRS) -> tuple[float, float]:
    c = geom.centroid
    if src.is_geographic:
        return c.x, c.y
    t = Transformer.from_crs(src, CRS.from_epsg(4326), always_xy=True)
    return t.transform(c.x, c.y)


def pick_projected_crs(geom: BaseGeometry, src: CRS) -> CRS:
    if is_metric_projected(src):
        return src
    lon, lat = _centroid_lonlat(geom, src)
    return utm_crs_for(lon, lat)


def measure(geom: BaseGeometry | None, src_crs: CRS) -> dict:
    """Return a measurement dict for one geometry. Never raises."""
    if geom is None or geom.is_empty:
        return {"status": "UNSUPPORTED", "reason": "Empty or missing geometry"}
    gtype = geom.geom_type
    try:
        if gtype in POINTLIKE:
            return {"status": "NOT_APPLICABLE", "reason": "No measurement defined for points"}
        if gtype not in POLYGONAL | LINEAR:
            return {"status": "UNSUPPORTED", "reason": f"Unsupported geometry type: {gtype}"}

        geom2d = force_2d(geom)
        target = pick_projected_crs(geom2d, src_crs)
        if target == src_crs:
            projected = geom2d
        else:
            tr = Transformer.from_crs(src_crs, target, always_xy=True)
            projected = shapely.transform(
                geom2d, lambda xy: np.column_stack(tr.transform(xy[:, 0], xy[:, 1]))
            )

        epsg = target.to_epsg()
        out = {"status": "OK", "projected_crs": f"EPSG:{epsg}" if epsg else target.to_string()}
        if gtype in POLYGONAL:
            area = float(projected.area)
            out.update(
                area_sq_m=round(area, 4),
                area_hectares=round(area / 10_000, 6),
                perimeter_m=round(float(projected.length), 4),
            )
            if not geom2d.is_valid:
                out["warning"] = "Input polygon is invalid (e.g. self-intersection); area may be unreliable"
        else:
            length = float(projected.length)
            out.update(length_m=round(length, 4), length_km=round(length / 1000, 6))
        return out
    except Exception as exc:  # one bad feature must not fail the whole file
        return {"status": "ERROR", "reason": f"{type(exc).__name__}: {exc}"}
