import io
import shutil
import tempfile
from pathlib import Path

import geopandas as gpd
import pytest
from fastapi.testclient import TestClient
from shapely.geometry import LineString, Point, Polygon
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from app import config

    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path / "uploads")
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def override():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override
    yield TestClient(app)
    app.dependency_overrides.clear()


SQUARE = Polygon([(78.4700, 17.4000), (78.4790, 17.4000), (78.4790, 17.4090), (78.4700, 17.4090)])
LINE = LineString([(78.47, 17.40), (78.48, 17.40), (78.48, 17.41)])
POINT = Point(78.475, 17.405)


def zip_shapefile(gdf: gpd.GeoDataFrame, name="data") -> bytes:
    tmp = Path(tempfile.mkdtemp())
    try:
        gdf.to_file(tmp / f"{name}.shp")
        archive = shutil.make_archive(str(tmp / "out"), "zip", tmp)
        return Path(archive).read_bytes()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def kml_bytes(gdf: gpd.GeoDataFrame) -> bytes:
    tmp = Path(tempfile.mkdtemp())
    try:
        p = tmp / "x.kml"
        gdf.to_file(p, driver="KML")
        return p.read_bytes()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture()
def mixed_gdf():
    return gpd.GeoDataFrame(
        {"name": ["plot", "road", "poi"]}, geometry=[SQUARE, LINE, POINT], crs="EPSG:4326"
    )
