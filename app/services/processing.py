"""Orchestrates: save upload -> parse -> measure -> persist."""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from sqlalchemy.orm import Session

from app import config
from app.models import Feature, UploadedFile
from app.services import measurements, parser


def crs_label(crs) -> str:
    epsg = crs.to_epsg()
    return f"EPSG:{epsg}" if epsg else (crs.name or crs.to_string())


def process_upload(db: Session, filename: str, data: bytes) -> UploadedFile:
    file_type = parser.detect_type(filename)  # raises FileProcessingError
    record = UploadedFile(filename=filename, file_type=file_type, status="PROCESSING")
    db.add(record)
    db.commit()

    suffix = ".zip" if file_type == "shapefile" else ".kml"
    config.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    workdir = Path(tempfile.mkdtemp(prefix=f"{record.id}_", dir=config.UPLOAD_DIR))
    try:
        stored = workdir / f"upload{suffix}"
        stored.write_bytes(data)

        parsed = parser.parse(stored, file_type, workdir)
        record.crs = crs_label(parsed.crs)

        rows = []
        for pf in parsed.features:
            rows.append(
                Feature(
                    file_id=record.id,
                    index=pf.index,
                    layer=pf.layer,
                    geometry_type=pf.geometry_type,
                    geometry=parser.geometry_to_geojson(pf.geometry),
                    properties=pf.properties,
                    measurement=measurements.measure(pf.geometry, parsed.crs),
                )
            )
        db.add_all(rows)
        record.feature_count = len(rows)
        record.status = "COMPLETED"
        db.commit()
    except parser.FileProcessingError as exc:
        db.rollback()
        record.status = "FAILED"
        record.error = str(exc)
        db.commit()
    except Exception as exc:  # unexpected: record it, don't leak a stack trace
        db.rollback()
        record.status = "FAILED"
        record.error = f"Unexpected error while processing file: {type(exc).__name__}"
        db.commit()
    finally:
        shutil.rmtree(workdir, ignore_errors=True)  # raw upload not retained; features live in DB
    return record
