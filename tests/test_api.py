import geopandas as gpd
import pytest
from pyproj import Geod
from shapely.geometry import Point

from tests.conftest import LINE, POINT, SQUARE, kml_bytes, zip_shapefile

GEOD = Geod(ellps="WGS84")


def upload(client, name, data, ctype="application/octet-stream"):
    return client.post("/api/files/", files={"file": (name, data, ctype)})


def test_upload_kml_and_get_info(client, mixed_gdf):
    r = upload(client, "survey.kml", kml_bytes(mixed_gdf))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "COMPLETED"
    assert body["feature_count"] == 3
    assert body["crs"] == "EPSG:4326"
    assert body["filename"] == "survey.kml"

    info = client.get(f"/api/files/{body['id']}/")
    assert info.status_code == 200
    assert info.json()["id"] == body["id"]


def test_kml_measurements(client, mixed_gdf):
    fid = upload(client, "survey.kml", kml_bytes(mixed_gdf)).json()["id"]
    r = client.get(f"/api/files/{fid}/measurements/")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 3
    by_type = {f["geometry_type"]: f for f in data["features"]}

    poly = by_type["Polygon"]
    assert poly["crs"] == "EPSG:4326"
    assert poly["properties"]["Name"] == "plot"
    assert poly["geometry"]["type"] == "Polygon"
    assert poly["measurement"]["area_sq_m"] == pytest.approx(abs(GEOD.geometry_area_perimeter(SQUARE)[0]), rel=1e-3)

    line = by_type["LineString"]
    assert line["measurement"]["length_m"] == pytest.approx(GEOD.geometry_length(LINE), rel=1e-3)

    assert by_type["Point"]["measurement"]["status"] == "NOT_APPLICABLE"
    assert data["summary"]["by_status"] == {"OK": 2, "NOT_APPLICABLE": 1}


def test_shapefile_zip_upload(client):
    gdf = gpd.GeoDataFrame({"id": [1]}, geometry=[SQUARE], crs="EPSG:4326")
    r = upload(client, "parcels.zip", zip_shapefile(gdf))
    assert r.status_code == 201, r.text
    fid = r.json()["id"]
    feats = client.get(f"/api/files/{fid}/measurements/").json()["features"]
    assert len(feats) == 1 and feats[0]["measurement"]["status"] == "OK"
    assert feats[0]["index"] == 0


def test_shapefile_in_projected_crs(client):
    gdf = gpd.GeoDataFrame({"id": [1]}, geometry=[SQUARE], crs="EPSG:4326").to_crs("EPSG:32644")
    fid = upload(client, "utm.zip", zip_shapefile(gdf)).json()["id"]
    f = client.get(f"/api/files/{fid}/measurements/").json()["features"][0]
    assert f["crs"] == "EPSG:32644"
    assert f["measurement"]["projected_crs"] == "EPSG:32644"
    assert f["measurement"]["area_sq_m"] == pytest.approx(abs(GEOD.geometry_area_perimeter(SQUARE)[0]), rel=1e-3)


def test_pagination(client):
    gdf = gpd.GeoDataFrame({"n": range(5)}, geometry=[Point(78 + i * 0.01, 17) for i in range(5)], crs="EPSG:4326")
    fid = upload(client, "pts.zip", zip_shapefile(gdf)).json()["id"]
    r = client.get(f"/api/files/{fid}/measurements/", params={"limit": 2, "offset": 3}).json()
    assert r["total"] == 5
    assert [f["index"] for f in r["features"]] == [3, 4]


def test_unsupported_extension(client):
    assert upload(client, "notes.txt", b"hello").status_code == 415


def test_empty_file(client):
    assert upload(client, "x.kml", b"").status_code == 400


def test_corrupt_zip_returns_422_and_is_recorded(client):
    r = upload(client, "bad.zip", b"this is not a zip")
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["status"] == "FAILED"
    info = client.get(f"/api/files/{detail['id']}/").json()
    assert info["status"] == "FAILED" and info["error"]
    assert client.get(f"/api/files/{detail['id']}/measurements/").status_code == 409


def test_zip_without_shp(client):
    import io, zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("readme.txt", "nothing here")
    r = upload(client, "empty.zip", buf.getvalue())
    assert r.status_code == 422
    assert "No .shp" in r.json()["detail"]["error"]


def test_zip_slip_rejected(client):
    import io, zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("../../evil.shp", "x")
    r = upload(client, "evil.zip", buf.getvalue())
    assert r.status_code == 422
    assert "unsafe" in r.json()["detail"]["error"]


def test_garbage_kml(client):
    r = upload(client, "bad.kml", b"<not really kml")
    assert r.status_code == 422


def test_unknown_id_404(client):
    assert client.get("/api/files/nope/").status_code == 404
    assert client.get("/api/files/nope/measurements/").status_code == 404


def test_null_properties_are_json_null_not_strings(client, mixed_gdf):
    # Regression: pandas NaT used to be serialised as the string "NaT".
    fid = upload(client, "survey.kml", kml_bytes(mixed_gdf)).json()["id"]
    props = client.get(f"/api/files/{fid}/measurements/").json()["features"][0]["properties"]
    assert props["timestamp"] is None
    assert "NaT" not in props.values()


GOOGLE_EARTH_STYLE_KML = b"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2"><Document>
<Folder><name>Plots</name><Placemark><name>Field</name>
<Polygon><outerBoundaryIs><LinearRing><coordinates>
78.40,17.40,520 78.41,17.40,520 78.41,17.41,520 78.40,17.41,520 78.40,17.40,520
</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark></Folder>
<Folder><name>Other</name><Placemark><name>Track</name><LineString><coordinates>
78.4,17.4,500 78.45,17.42,505 78.5,17.45,510</coordinates></LineString></Placemark></Folder>
</Document></kml>"""


def test_kml_with_folders_and_z_values(client):
    r = upload(client, "earth.kml", GOOGLE_EARTH_STYLE_KML)
    assert r.status_code == 201, r.text
    feats = client.get(f"/api/files/{r.json()['id']}/measurements/").json()["features"]
    assert [f["geometry_type"] for f in feats] == ["Polygon", "LineString"]
    assert [f["layer"] for f in feats] == ["Plots", "Other"]  # one layer per KML folder
    assert all(f["measurement"]["status"] == "OK" for f in feats)


def test_shapefile_without_prj_is_rejected_not_guessed(client, tmp_path):
    import shutil
    from pathlib import Path

    gdf = gpd.GeoDataFrame({"a": [1]}, geometry=[SQUARE], crs="EPSG:4326")
    gdf.to_file(tmp_path / "x.shp")
    for prj in tmp_path.glob("*.prj"):
        prj.unlink()
    data = Path(shutil.make_archive(str(tmp_path / "out"), "zip", tmp_path)).read_bytes()
    r = upload(client, "noprj.zip", data)
    assert r.status_code == 422
    assert "no CRS" in r.json()["detail"]["error"]
