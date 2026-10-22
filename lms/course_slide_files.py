"""Turn an uploaded PowerPoint into page images on the media volume.

The upload and the rendered pages stay files under the class image
folder. SQLite stores a path, never the pptx or the PNG bytes. Conversion
runs LibreOffice headless, then the same PDF-to-PNG path as a Google
Slides export.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

# The sample deck is 21.7 MB. 64 MiB leaves room above that without
# accepting an unbounded body. Flask's global limit is the IMSCC cap,
# so the upload handler has to enforce this itself.
COURSE_SLIDE_UPLOAD_MAX_BYTES = 64 * 1024 * 1024
_CONVERT_TIMEOUT_S = 90
_READ_CHUNK = 65536


def read_capped_upload(uploaded: object, limit: int) -> bytes:
    """Read an upload and stop once it passes ``limit``.

    Args:
        uploaded: File-like object with ``read``.
        limit: Maximum accepted size in bytes.

    Returns:
        The full body when it fits.

    Raises:
        ValueError: The body is empty or larger than ``limit``.
    """
    read = getattr(uploaded, "read", None)
    if not callable(read):
        raise ValueError("Choose a PowerPoint file.")
    chunks: list[bytes] = []
    total = 0
    cap = max(0, int(limit))
    while True:
        block = read(_READ_CHUNK)
        if not block:
            break
        if not isinstance(block, (bytes, bytearray)):
            raise ValueError("Choose a PowerPoint file.")
        total += len(block)
        if total > cap:
            raise ValueError("That file is too large to attach.")
        chunks.append(bytes(block))
    if total < 1:
        raise ValueError("Choose a PowerPoint file.")
    return b"".join(chunks)


def office_suffix(filename: str) -> str:
    """Return ``.ppt`` or ``.pptx`` for an upload name.

    Args:
        filename: Original client filename. It is not passed to a shell.

    Raises:
        ValueError: The name is not a PowerPoint file.
    """
    suffix = Path(str(filename or "")).suffix.lower()
    if suffix not in {".ppt", ".pptx"}:
        raise ValueError("Upload a .ppt or .pptx file.")
    return suffix


def office_bytes_ok(raw: bytes, suffix: str) -> None:
    """Reject a body that is not a PowerPoint package.

    Args:
        raw: Upload bytes.
        suffix: ``.ppt`` or ``.pptx``.

    Raises:
        ValueError: The bytes do not match that package.
    """
    if suffix == ".pptx":
        if not raw.startswith(b"PK"):
            raise ValueError("That file is not a PowerPoint deck.")
        return
    if not raw.startswith(b"\xd0\xcf\x11\xe0"):
        raise ValueError("That file is not a PowerPoint deck.")


def slide_deck_title(raw: str, *, fallback: str) -> str:
    """One short deck name for the Set Class summary.

    Args:
        raw: Filename stem or other label.
        fallback: Used when ``raw`` is blank.

    Returns:
        A single line, at most 120 characters.
    """
    text = " ".join(str(raw or "").replace("\\", " ").replace("/", " ").split())
    if not text:
        text = fallback
    return text[:120]


def soffice_bin() -> str:
    """Path to headless LibreOffice, or empty when it is not installed."""
    for name in ("soffice", "libreoffice"):
        found = shutil.which(name)
        if found:
            return found
    return ""


def pages_from_office(raw: bytes, suffix: str) -> tuple[list[bytes], bool]:
    """Render a PowerPoint upload to one PNG per page.

    Args:
        raw: ppt or pptx bytes. Already size-checked.
        suffix: ``.ppt`` or ``.pptx``.

    Returns:
        ``(png_pages, truncated)``. ``truncated`` is true past the page cap.

    Raises:
        ValueError: LibreOffice is missing, the file is not a deck, or
            conversion did not finish.
    """
    office_bytes_ok(raw, suffix)
    binary = soffice_bin()
    if not binary:
        raise ValueError("PowerPoint import is not available on this server.")
    from google_slides_import import render_pdf_pages

    pdf = _office_to_pdf(raw, suffix, binary)
    return render_pdf_pages(pdf)


def _office_to_pdf(raw: bytes, suffix: str, binary: str) -> bytes:
    """Convert one deck to PDF in a private LibreOffice profile.

    The upload name is never placed on the command line. The process
    gets a fixed ``deck.ppt`` or ``deck.pptx`` inside a temp directory.

    Args:
        raw: Deck bytes.
        suffix: ``.ppt`` or ``.pptx``.
        binary: Absolute ``soffice`` path.

    Returns:
        PDF bytes.

    Raises:
        ValueError: Conversion failed or timed out.
    """
    with tempfile.TemporaryDirectory(prefix="lloves-slides-") as tmp:
        root = Path(tmp)
        source = root / f"deck{suffix}"
        outdir = root / "out"
        profile = root / "profile"
        outdir.mkdir()
        source.write_bytes(raw)
        command = [
            binary,
            "--headless",
            "--norestore",
            "--nolockcheck",
            "--nologo",
            f"-env:UserInstallation=file://{profile}",
            "--convert-to",
            "pdf",
            "--outdir",
            str(outdir),
            str(source),
        ]
        env = os.environ.copy()
        env["HOME"] = str(root)
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                timeout=_CONVERT_TIMEOUT_S,
                check=False,
                env=env,
            )
        except subprocess.TimeoutExpired as exc:
            raise ValueError("That deck took too long to attach.") from exc
        pdfs = sorted(outdir.glob("*.pdf"))
        if completed.returncode != 0 or not pdfs:
            raise ValueError("That PowerPoint could not be read.")
        data = pdfs[0].read_bytes()
    if not data.startswith(b"%PDF"):
        raise ValueError("That PowerPoint could not be read.")
    return data
