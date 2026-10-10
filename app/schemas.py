from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, model_serializer


class FileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    filename: str
    file_type: str
    status: str
    crs: str | None
    feature_count: int
    error: str | None = None
    created_at: datetime


class MeasurementOut(BaseModel):
    status: str
    projected_crs: str | None = None
    area_sq_m: float | None = None
    area_hectares: float | None = None
    perimeter_m: float | None = None
    length_m: float | None = None
    length_km: float | None = None
    reason: str | None = None
    warning: str | None = None

    @model_serializer(mode="wrap")
    def _drop_none(self, handler):
        # Only emit fields that apply (a polygon has no length_m, etc.).
        return {k: v for k, v in handler(self).items() if v is not None}


class FeatureOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    index: int
    layer: str | None
    geometry_type: str | None
    crs: str | None = None
    geometry: dict[str, Any] | None
    properties: dict[str, Any]
    measurement: MeasurementOut


class MeasurementsResponse(BaseModel):
    file_id: str
    crs: str | None
    total: int
    limit: int
    offset: int
    summary: dict[str, Any]
    features: list[FeatureOut]
