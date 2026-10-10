from collections import Counter

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import config
from app.database import get_db
from app.models import Feature, UploadedFile
from app.schemas import FeatureOut, FileOut, MeasurementsResponse
from app.services import parser, processing

router = APIRouter(prefix="/api/files", tags=["files"])


def _get_file_or_404(db: Session, file_id: str) -> UploadedFile:
    record = db.get(UploadedFile, file_id)
    if record is None:
        raise HTTPException(404, "File not found")
    return record


@router.post("/", response_model=FileOut, status_code=201)
async def upload_file(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Upload a .zip (Shapefile) or .kml, process it, and store features + measurements."""
    filename = file.filename or "upload"
    try:
        parser.detect_type(filename)
    except parser.FileProcessingError as exc:
        raise HTTPException(415, str(exc))

    data = await file.read(config.MAX_UPLOAD_BYTES + 1)
    if len(data) > config.MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"File exceeds {config.MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit")
    if not data:
        raise HTTPException(400, "Uploaded file is empty")

    record = processing.process_upload(db, filename, data)
    if record.status == "FAILED":
        # Record is kept (so it can be inspected via GET) but the client gets a 422.
        raise HTTPException(
            422, {"id": record.id, "status": record.status, "error": record.error}
        )
    return record


@router.get("/{file_id}/", response_model=FileOut)
def get_file(file_id: str, db: Session = Depends(get_db)):
    return _get_file_or_404(db, file_id)


@router.get("/{file_id}/measurements/", response_model=MeasurementsResponse)
def get_measurements(
    file_id: str,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    record = _get_file_or_404(db, file_id)
    if record.status != "COMPLETED":
        raise HTTPException(409, f"File status is {record.status}; measurements unavailable")

    total = db.scalar(select(func.count()).select_from(Feature).where(Feature.file_id == file_id)) or 0
    page = db.scalars(
        select(Feature).where(Feature.file_id == file_id).order_by(Feature.index).limit(limit).offset(offset)
    ).all()

    # Summary over the whole file (not just the page).
    all_rows = db.execute(
        select(Feature.geometry_type, Feature.measurement).where(Feature.file_id == file_id)
    ).all()
    status_counts = Counter(m.get("status") for _, m in all_rows)
    summary = {
        "by_status": dict(status_counts),
        "by_geometry_type": dict(Counter(g for g, _ in all_rows)),
        "total_area_sq_m": round(sum(m.get("area_sq_m", 0) for _, m in all_rows), 4),
        "total_length_m": round(sum(m.get("length_m", 0) for _, m in all_rows), 4),
    }

    features = [
        FeatureOut(
            index=f.index,
            layer=f.layer,
            geometry_type=f.geometry_type,
            crs=record.crs,
            geometry=f.geometry,
            properties=f.properties,
            measurement=f.measurement,
        )
        for f in page
    ]
    return MeasurementsResponse(
        file_id=file_id,
        crs=record.crs,
        total=total,
        limit=limit,
        offset=offset,
        summary=summary,
        features=features,
    )
