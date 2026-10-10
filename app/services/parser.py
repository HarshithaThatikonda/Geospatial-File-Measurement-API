"""Read Shapefile (.zip) and KML uploads into plain Python structures."""
from __future__ import annotations

import json
import math
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pyogrio
from pyproj import CRS
from shapely import force_2d, to_geojson  # noqa: F401

from app import config


class FileProcessingError(Exception):
    """Raised for user-facing problems with the uploaded file (HTTP 422)."""


@dataclass
class ParsedFeature:
    index: int
    layer: str | None
    geometry_type: str | None
    geometry: object | None  # shapely geometry (file CRS) or None
    properties: dict


@dataclass
class ParsedFile:
    crs: CRS
    features: list[ParsedFeature] = field(default_factory=list)


def detect_type(filename: str) -> str:
    name = filename.lower()
    if name.endswith(".zip"):
        return "shapefile"
    if name.endswith(".kml"):
        return "kml"
    raise FileProcessingError("Unsupported file type. Upload a .zip (Shapefile) or .kml file.")


def safe_extract_zip(zip_path: Path, dest: Path) -> list[Path]:
    """Extract a zip while blocking zip-slip and zip-bomb style archives."""
    dest = dest.resolve()
    try:
        zf = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile as exc:
        raise FileProcessingError("File is not a valid zip archive.") from exc
    with zf:
        total = sum(i.file_size for i in zf.infolist())
        if total > config.MAX_UNZIPPED_BYTES:
            raise FileProcessingError("Zip archive is too large when extracted.")
        for info in zf.infolist():
            target = (dest / info.filename).resolve()
            if dest not in target.parents and target != dest:
                raise FileProcessingError("Zip archive contains an unsafe path.")
        zf.extractall(dest)
    return [p for p in dest.rglob("*") if p.is_file()]


def _clean_value(v):
    """Make a property value JSON-serialisable."""
    if v is None:
        return None
    if isinstance(v, float) and math.isinf(v):
        return None
    try:
        if pd.isna(v):  # NaN, NaT, pd.NA
            return None
    except (TypeError, ValueError):
        pass  # array-like values: fall through to str()
    if hasattr(v, "isoformat"):
        return v.isoformat()
    if hasattr(v, "item"):  # numpy scalar
        return _clean_value(v.item())
    if isinstance(v, (str, int, float, bool)):
        return v
    return str(v)


def _read_layer(path: Path, layer: str | None):
    try:
        return gpd.read_file(path, layer=layer, engine="pyogrio")
    except Exception as exc:
        raise FileProcessingError(f"Could not read geospatial data: {exc}") from exc


def parse(path: Path, file_type: str, workdir: Path) -> ParsedFile:
    if file_type == "shapefile":
        files = safe_extract_zip(path, workdir / "extracted")
        shps = [p for p in files if p.suffix.lower() == ".shp" and "__MACOSX" not in p.parts]
        if not shps:
            raise FileProcessingError("No .shp file found inside the zip archive.")
        sources = [(shp, None) for shp in shps]
    else:
        try:
            layers = [str(name) for name, _ in pyogrio.list_layers(path)]
        except Exception as exc:
            raise FileProcessingError(f"Could not read KML file: {exc}") from exc
        if not layers:
            raise FileProcessingError("KML file contains no layers.")
        sources = [(path, layer) for layer in layers]

    parsed: ParsedFile | None = None
    index = 0
    for src, layer in sources:
        gdf = _read_layer(src, layer)
        if gdf.crs is None:
            raise FileProcessingError(
                f"{src.name}: no CRS defined (missing .prj for shapefile?). "
                "Cannot compute reliable measurements without a CRS."
            )
        crs = CRS.from_user_input(gdf.crs)
        if parsed is None:
            parsed = ParsedFile(crs=crs)
        elif crs != parsed.crs:
            raise FileProcessingError("Archive contains shapefiles with different CRSs; upload them separately.")

        layer_name = layer or src.stem
        prop_cols = [c for c in gdf.columns if c != gdf.geometry.name]
        for _, row in gdf.iterrows():
            geom = row.geometry
            if geom is not None and geom.is_empty:
                geom = None
            parsed.features.append(
                ParsedFeature(
                    index=index,
                    layer=layer_name,
                    geometry_type=geom.geom_type if geom is not None else None,
                    geometry=geom,
                    properties={c: _clean_value(row[c]) for c in prop_cols},
                )
            )
            index += 1
    assert parsed is not None
    return parsed


def geometry_to_geojson(geom) -> dict | None:
    return None if geom is None else json.loads(to_geojson(geom))
