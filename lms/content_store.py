"""Content-addressed file storage for shared LLOVES course libraries."""

from __future__ import annotations

import hashlib
import mimetypes
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO


@dataclass(frozen=True)
class StoredBlob:
    """One immutable file in the shared blob store."""

    sha256: str
    bytes: int
    mime: str
    path: Path


def blob_path(data_dir: Path, sha256: str) -> Path:
    """Return the canonical path for a SHA-256 digest.

    Args:
        data_dir: LMS data volume (``/data`` on Fly).
        sha256: Lowercase SHA-256 digest.
    """
    digest = str(sha256).strip().lower()
    if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
        raise ValueError("sha256 must be a lowercase 64-character digest")
    return Path(data_dir) / "blobs" / digest[:2] / digest


def hash_stream(stream: BinaryIO, *, chunk_size: int = 1024 * 1024) -> tuple[str, int]:
    """Hash a binary stream from its current position.

    Args:
        stream: Readable binary file object.
        chunk_size: Bytes read per iteration.

    Returns:
        SHA-256 digest and byte count.
    """
    digest = hashlib.sha256()
    byte_count = 0
    while True:
        chunk = stream.read(chunk_size)
        if not chunk:
            break
        digest.update(chunk)
        byte_count += len(chunk)
    return digest.hexdigest(), byte_count


def hash_path(path: Path) -> tuple[str, int]:
    """Return a file's SHA-256 digest and byte count."""
    with Path(path).open("rb") as stream:
        return hash_stream(stream)


def _mime_for(filename: str | None, supplied: str | None) -> str:
    """Choose a stable MIME type for blob metadata."""
    if supplied:
        return str(supplied)
    guessed, _encoding = mimetypes.guess_type(filename or "")
    return guessed or "application/octet-stream"


class ContentBlobStore:
    """Deduplicated immutable files plus sqlite metadata."""

    def __init__(self, data_dir: Path, db: Any) -> None:
        """Create a blob store.

        Args:
            data_dir: LMS data volume.
            db: ``LovesDB``/``SchoolDB`` exposing ``register_blob``.
        """
        self.data_dir = Path(data_dir)
        self.db = db

    def put_bytes(
        self,
        payload: bytes,
        *,
        filename: str | None = None,
        mime: str | None = None,
    ) -> StoredBlob:
        """Store bytes once and register their metadata."""
        raw = bytes(payload)
        digest = hashlib.sha256(raw).hexdigest()
        target = blob_path(self.data_dir, digest)
        self._write_once(target, raw)
        media_type = _mime_for(filename, mime)
        row = self.db.register_blob(digest, len(raw), media_type)
        return StoredBlob(
            sha256=digest,
            bytes=int(row["bytes"]),
            mime=str(row["mime"]),
            path=target,
        )

    def put_path(
        self, source: Path, *, mime: str | None = None
    ) -> StoredBlob:
        """Store a file once without loading it wholly into memory."""
        source = Path(source)
        digest, byte_count = hash_path(source)
        target = blob_path(self.data_dir, digest)
        if not target.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            with source.open("rb") as src, tempfile.NamedTemporaryFile(
                dir=target.parent, delete=False
            ) as tmp:
                while True:
                    chunk = src.read(1024 * 1024)
                    if not chunk:
                        break
                    tmp.write(chunk)
                tmp_path = Path(tmp.name)
            try:
                os.replace(tmp_path, target)
            finally:
                if tmp_path.exists():
                    tmp_path.unlink()
        media_type = _mime_for(source.name, mime)
        row = self.db.register_blob(digest, byte_count, media_type)
        return StoredBlob(
            sha256=digest,
            bytes=int(row["bytes"]),
            mime=str(row["mime"]),
            path=target,
        )

    @staticmethod
    def _write_once(target: Path, payload: bytes) -> None:
        """Atomically write ``payload`` only when its digest path is absent."""
        if target.is_file():
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as tmp:
            tmp.write(payload)
            tmp_path = Path(tmp.name)
        try:
            os.replace(tmp_path, target)
        finally:
            if tmp_path.exists():
                tmp_path.unlink()
