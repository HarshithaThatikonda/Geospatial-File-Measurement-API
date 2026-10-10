import pytest
from pyproj import CRS, Geod
from shapely.geometry import GeometryCollection, LineString, Point, Polygon

from app.services.measurements import measure, utm_crs_for
from tests.conftest import LINE, SQUARE

WGS84 = CRS.from_epsg(4326)
GEOD = Geod(ellps="WGS84")


def test_polygon_area_matches_geodesic_within_tolerance():
    result = measure(SQUARE, WGS84)
    geodesic_area = abs(GEOD.geometry_area_perimeter(SQUARE)[0])
    assert result["status"] == "OK"
    assert result["projected_crs"] == "EPSG:32644"  # Hyderabad is UTM 44N
    assert result["area_sq_m"] == pytest.approx(geodesic_area, rel=1e-3)
    assert result["area_hectares"] == pytest.approx(result["area_sq_m"] / 1e4, rel=1e-6)


def test_line_length_matches_geodesic_within_tolerance():
    result = measure(LINE, WGS84)
    geodesic_len = GEOD.geometry_length(LINE)
    assert result["status"] == "OK"
    assert result["length_m"] == pytest.approx(geodesic_len, rel=1e-3)


def test_degrees_are_never_used_directly():
    # Naive degree-based area would be ~8.1e-5 -- anything near that means a bug.
    assert measure(SQUARE, WGS84)["area_sq_m"] > 1e5


def test_point_not_applicable():
    assert measure(Point(78.4, 17.4), WGS84)["status"] == "NOT_APPLICABLE"


def test_unsupported_and_empty_geometries_do_not_crash():
    gc = GeometryCollection([Point(0, 0), LineString([(0, 0), (1, 1)])])
    assert measure(gc, WGS84)["status"] == "UNSUPPORTED"
    assert measure(None, WGS84)["status"] == "UNSUPPORTED"
    assert measure(Polygon(), WGS84)["status"] == "UNSUPPORTED"


def test_projected_metric_crs_used_directly():
    crs = CRS.from_epsg(32644)
    poly = Polygon([(0, 0), (100, 0), (100, 50), (0, 50)])
    result = measure(poly, crs)
    assert result["projected_crs"] == "EPSG:32644"
    assert result["area_sq_m"] == pytest.approx(5000)


def test_non_metric_projected_crs_converted_to_metres():
    # EPSG:2272 = NAD83 / Pennsylvania South (US survey feet)
    crs = CRS.from_epsg(2272)
    poly = Polygon([(2_600_000, 220_000), (2_600_100, 220_000), (2_600_100, 220_100), (2_600_000, 220_100)])
    result = measure(poly, crs)
    # 100 ft x 100 ft ~= 929 m^2 (not 10,000)
    assert result["area_sq_m"] == pytest.approx(929.03, rel=0.01)


def test_utm_zone_selection_north_south_and_poles():
    assert utm_crs_for(78.4, 17.4).to_epsg() == 32644
    assert utm_crs_for(151.2, -33.9).to_epsg() == 32756  # Sydney
    assert utm_crs_for(0, 85).to_epsg() == 32661
    assert utm_crs_for(0, -85).to_epsg() == 32761


def test_z_coordinates_ignored():
    poly_z = Polygon([(78.47, 17.40, 500), (78.479, 17.40, 500), (78.479, 17.409, 500), (78.47, 17.409, 500)])
    assert measure(poly_z, WGS84)["status"] == "OK"


def test_polygon_with_hole_subtracts_hole():
    # Reference = outer - hole, each measured geodesically. (Geod on the holed polygon directly
    # is orientation-dependent and can ADD the hole, so it is not used as ground truth here.)
    outer = Polygon([(78.40, 17.40), (78.41, 17.40), (78.41, 17.41), (78.40, 17.41)])
    inner = Polygon([(78.403, 17.403), (78.407, 17.403), (78.407, 17.407), (78.403, 17.407)])
    holed = Polygon(outer.exterior.coords, [inner.exterior.coords])
    expected = abs(GEOD.geometry_area_perimeter(outer)[0]) - abs(GEOD.geometry_area_perimeter(inner)[0])
    assert measure(holed, WGS84)["area_sq_m"] == pytest.approx(expected, rel=2e-3)


def test_multipolygon_area_is_sum_of_parts():
    from shapely.geometry import MultiPolygon

    a = Polygon([(78.5, 17.5), (78.51, 17.5), (78.51, 17.51), (78.5, 17.51)])
    b = Polygon([(78.52, 17.5), (78.53, 17.5), (78.53, 17.51), (78.52, 17.51)])
    expected = abs(GEOD.geometry_area_perimeter(a)[0]) + abs(GEOD.geometry_area_perimeter(b)[0])
    assert measure(MultiPolygon([a, b]), WGS84)["area_sq_m"] == pytest.approx(expected, rel=2e-3)
