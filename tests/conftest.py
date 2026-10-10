import os
import tempfile
from pathlib import Path

# Configure local temporary paths for test runs outside Docker containers
temp_dir = Path(tempfile.gettempdir()) / "grimmory_proxy_pytest_storage"
temp_dir.mkdir(parents=True, exist_ok=True)
db_path = temp_dir / "test_bridge.db"
thumb_dir = temp_dir / "test_thumbnails"
thumb_dir.mkdir(parents=True, exist_ok=True)

if db_path.exists():
    try:
        db_path.unlink()
    except Exception:
        pass

os.environ["DATABASE_PATH"] = str(db_path)
os.environ["THUMBNAILS_DIR"] = str(thumb_dir)
