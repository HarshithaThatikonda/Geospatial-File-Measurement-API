import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR / 'geo.db'}")
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", BASE_DIR / "uploads"))
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", 50 * 1024 * 1024))  # 50 MB
MAX_UNZIPPED_BYTES = int(os.getenv("MAX_UNZIPPED_BYTES", 500 * 1024 * 1024))
