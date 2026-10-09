"""Safe handling of uploaded files: size limit, real file type (not just the extension), safe name."""
import os
import re
from typing import Dict, Tuple

from fastapi import HTTPException, UploadFile, status

MB = 1024 * 1024

# extension -> (allowed leading bytes of a real file of that type)
SIGNATURES: Dict[str, Tuple[bytes, ...]] = {
    ".pdf": (b"%PDF-",),
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".docx": (b"PK\x03\x04",),
    ".xlsx": (b"PK\x03\x04",),
    ".doc": (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",),
    ".xls": (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",),
}
DOCUMENT_TYPES = (".pdf", ".doc", ".docx", ".jpg", ".jpeg", ".png")
SPREADSHEET_TYPES = (".xlsx", ".xls", ".csv")

MAX_DOCUMENT_BYTES = 15 * MB
MAX_SPREADSHEET_BYTES = 25 * MB


def safe_filename(name: str, max_len: int = 80) -> str:
    """Base name only, with anything that is not a letter, digit, dot, dash or underscore replaced."""
    base = os.path.basename((name or "").replace("\\", "/"))
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("._") or "file"
    stem, ext = os.path.splitext(base)
    return (stem[: max_len - len(ext)].rstrip("._-") or "file") + ext.lower()


def check_content(ext: str, head: bytes) -> bool:
    """True when the first bytes match the claimed type (CSV: plain text without NUL bytes)."""
    if ext == ".csv":
        return b"\x00" not in head
    return any(head.startswith(sig) for sig in SIGNATURES.get(ext, ()))


async def read_upload(file: UploadFile, allowed: Tuple[str, ...], max_bytes: int) -> Tuple[bytes, str]:
    """Read an upload fully with a size limit and verify extension + real content type.
    Returns (content, extension). Raises HTTP 400 / 413 with a clear message."""
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in allowed:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail=f"File type '{ext or 'unknown'}' is not allowed. Allowed: {', '.join(allowed)}.")
    chunks, size = [], 0
    while True:
        chunk = await file.read(1 * MB)
        if not chunk:
            break
        size += len(chunk)
        if size > max_bytes:
            raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                                detail=f"File is larger than the {max_bytes // MB} MB limit.")
        chunks.append(chunk)
    content = b"".join(chunks)
    if not content:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="The uploaded file is empty.")
    if not check_content(ext, content[:16]):
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail=f"The file content does not match its '{ext}' extension.")
    return content, ext
