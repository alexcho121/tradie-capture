#!/usr/bin/env python3
"""Regenerate docs/openapi.json (the API contract B builds against). Run after changing any endpoint or response model."""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402

app = create_app(Settings(data_dir=Path(tempfile.mkdtemp(prefix="openapi-"))))
out = ROOT / "docs" / "openapi.json"
out.write_text(json.dumps(app.openapi(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
print(f"wrote {out} ({len(app.openapi()['paths'])} paths)")
