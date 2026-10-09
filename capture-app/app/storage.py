"""Upload handling: size limit, type allow-list, server-generated names, SHA-256 while streaming."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Tuple

from fastapi import HTTPException, UploadFile

# Browsers disagree on what MediaRecorder produces: Chrome/Android -> audio/webm (opus),
# iOS Safari -> audio/mp4. Both are accepted by the transcription API.
AUDIO_EXT = {
    "audio/webm": ".webm",
    "video/webm": ".webm",
    "audio/mp4": ".m4a",
    "audio/x-m4a": ".m4a",
    "audio/m4a": ".m4a",
    "audio/aac": ".m4a",
    "video/mp4": ".mp4",
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
    "audio/ogg": ".ogg",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/wave": ".wav",
}
PHOTO_EXT = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/heic": ".heic",
    "image/heif": ".heic",
}
PROOF_EXT = dict(PHOTO_EXT, **{"application/pdf": ".pdf"})
_TABLES = {"audio": AUDIO_EXT, "photo": PHOTO_EXT, "proof": PROOF_EXT}
_FILENAME_EXTS = {
    "audio": {".webm", ".m4a", ".mp4", ".mp3", ".ogg", ".wav"},
    "photo": {".jpg", ".jpeg", ".png", ".webp", ".heic"},
    "proof": {".jpg", ".jpeg", ".png", ".webp", ".heic", ".pdf"},
}


def pick_ext(kind: str, content_type: str | None, filename: str | None) -> str:
    """Map an upload to a safe extension. The client's filename is never used for the path."""
    ct = (content_type or "").split(";")[0].strip().lower()  # drop ";codecs=opus"
    table = _TABLES[kind]
    if ct in table:
        return table[ct]
    ext = Path(filename or "").suffix.lower()
    if ext in _FILENAME_EXTS[kind]:  # generic content-type (octet-stream) -> trust a known extension
        return ".jpg" if ext == ".jpeg" else ext
    raise HTTPException(status_code=415, detail=f"Unsupported {kind} type: {ct or 'unknown'}")


def save_upload(upload: UploadFile, dest: Path, max_bytes: int) -> Tuple[int, str]:
    """Stream an upload to `dest`; returns (size_bytes, sha256_hex). Enforces the size limit."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    digest = hashlib.sha256()
    size = 0
    try:
        with open(tmp, "wb") as out:
            while True:
                chunk = upload.file.read(1 << 20)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise HTTPException(status_code=413, detail=f"File too large (limit {max_bytes // (1 << 20)} MB)")
                digest.update(chunk)
                out.write(chunk)
        if size == 0:
            raise HTTPException(status_code=400, detail="Empty upload")
        os.replace(tmp, dest)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return size, digest.hexdigest()


def media_url(rel_path: str | None) -> str | None:
    return f"/media/{rel_path}" if rel_path else None
