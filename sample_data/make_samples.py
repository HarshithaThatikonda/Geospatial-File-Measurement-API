"""Generate small sample files: python sample_data/make_samples.py"""
import shutil
import tempfile
from pathlib import Path

import geopandas as gpd
from shapely.geometry import LineString, Point, Polygon

OUT = Path(__file__).parent

# ~1 km x 1 km square near Hyderabad (EPSG:4326). Expected area ~ 0.99 km^2 ... ~ 1e6 m^2 order.
square = Polygon([(78.4700, 17.4000), (78.4790, 17.4000), (78.4790, 17.4090), (78.4700, 17.4090)])
road = LineString([(78.47, 17.40), (78.48, 17.40), (78.48, 17.41)])
spot = Point(78.475, 17.405)

gdf = gpd.GeoDataFrame(
    {"name": ["plot-A", "road-1", "marker"], "kind": ["parcel", "road", "poi"]},
    geometry=[square, road, spot],
    crs="EPSG:4326",
)

# KML: one layer per geometry type is not required; KML driver handles mixed.
gdf.to_file(OUT / "survey.kml", driver="KML")

# Shapefile needs a single geometry type per file -> polygons only, zipped.
tmp = Path(tempfile.mkdtemp())
gdf[gdf.geometry.geom_type == "Polygon"].to_file(tmp / "parcels.shp")
shutil.make_archive(str(OUT / "parcels_shapefile"), "zip", tmp)
print("wrote", [p.name for p in OUT.iterdir()])
