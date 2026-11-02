#!/usr/bin/env python3
"""Export the FastAPI OpenAPI schema to docs/openapi.json."""

import json
import sys
from pathlib import Path

# Add project root to sys.path
root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from app.main import app
from app.config import settings

def export_openapi() -> None:
    schema = app.openapi()
    schema["info"]["title"] = "grimmory-proxy API"
    schema["info"]["description"] = (
        "Complete Komga v1.12.0 and Admin WebUI API specification for grimmory-proxy."
    )
    schema["info"]["version"] = settings.app_version

    out_file = root_dir / "docs" / "openapi.json"
    out_file.write_text(json.dumps(schema, indent=2), encoding="utf-8")
    print(f"Exported OpenAPI specification ({len(schema.get('paths', {}))} endpoints) to {out_file}")

if __name__ == "__main__":
    export_openapi()
