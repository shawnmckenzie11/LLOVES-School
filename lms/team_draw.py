"""MCK-242: Team Draw group whiteboard icebreaker (no scoring, session images)."""

from __future__ import annotations

import base64
import json
import re
import struct
import zlib
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from school_db import SchoolDB

TEAM_DRAW_KIND = "team_draw"
TEAM_DRAW_RESPONSE_MODE = "team_draw"
MAX_TITLE_LEN = 80
MAX_IMAGE_BYTES = 750_000
# Whiteboard cards are about 720×360. Real drawings stay under 6 megapixels.
# Refuse a side over 4096, or more than 8 million pixels, before inflate.
MAX_PNG_SIDE = 4096
MAX_PNG_PIXELS = 8_000_000
# Largest in-cap RGBA image: one filter byte per row plus four bytes a pixel.
MAX_DECOMPRESSED_BYTES = MAX_PNG_PIXELS * 4 + MAX_PNG_SIDE
DRAWING_TOO_LARGE_ERROR = "Image is too large."
BLANK_DRAWING_ERROR = "Draw on the board before submitting."

BUILTIN_PROMPTS: tuple[dict[str, str], ...] = (
    {
        "id": "team_logo",
        "label": "Team logo",
        "text": "Draw your team logo. Bonus if it has an animal in it.",
    },
    {
        "id": "mystery_rule",
        "label": "Mystery rule",
        "text": "Mystery rule: write the rule on the board with the Text tool after you draw step 4.",
    },
    {
        "id": "finish_pattern",
        "label": "Finish the pattern",
        "text": "Finish the pattern.",
    },
    {
        "id": "team_common",
        "label": "Something in common",
        "text": "Draw something every person on your team has in common.",
    },
)

DEFAULT_MYSTERY_STEPS: tuple[str, ...] = (
    "Everyone must use at least two colours.",
    "Include one curved line and one straight line.",
    "Leave a blank corner for the next step.",
)


def is_draw_catchup_question(question: dict[str, Any] | None) -> bool:
    """True when a catch-up row is an individual draw question.

    Individual draw questions count in participation, so catch-up has to
    take a drawing. A typed answer does not qualify.

    Args:
        question: Catch-up row with ``prompt_kind`` and ``content``.
    """
    if not isinstance(question, dict):
        return False
    kind = str(question.get("prompt_kind") or "").strip().lower()
    content = question.get("content") if isinstance(question.get("content"), dict) else {}
    token = str(content.get("type") or content.get("kind") or "").strip().lower()
    return kind == TEAM_DRAW_KIND or token == TEAM_DRAW_KIND


def is_team_draw_item(item: dict[str, Any] | None) -> bool:
    """True when a lifecycle row or catalogue payload is Team Draw."""
    if not isinstance(item, dict):
        return False
    if str(item.get("response_mode") or "") == TEAM_DRAW_RESPONSE_MODE:
        return True
    question = item.get("item") if isinstance(item.get("item"), dict) else item
    if not isinstance(question, dict):
        return False
    token = str(question.get("type") or question.get("kind") or "").strip().lower()
    return token == TEAM_DRAW_KIND


def prompt_catalog() -> list[dict[str, str]]:
    """Built-in prompts for the teacher picker."""
    return [dict(row) for row in BUILTIN_PROMPTS]


def resolved_prompt_text(item: dict[str, Any]) -> str:
    """Return the stem students and staff should see for one item."""
    question = item.get("item") if isinstance(item.get("item"), dict) else {}
    custom = str(question.get("team_draw_custom_text") or "").strip()
    if custom:
        return custom
    prompt_id = str(question.get("team_draw_prompt_id") or "").strip()
    for row in BUILTIN_PROMPTS:
        if row["id"] == prompt_id:
            return row["text"]
    return str(question.get("text") or question.get("prompt") or "Team Draw").strip()


def is_mystery_rule_item(item: dict[str, Any] | None) -> bool:
    """True when the Team Draw item uses the mystery-rule built-in prompt."""
    if not isinstance(item, dict):
        return False
    question = item.get("item") if isinstance(item.get("item"), dict) else item
    if not isinstance(question, dict):
        return False
    return str(question.get("team_draw_prompt_id") or "").strip() == "mystery_rule"


def mystery_steps_for_item(item: dict[str, Any]) -> list[str]:
    """Return three locked mystery-rule steps (teacher-authored or defaults)."""
    question = item.get("item") if isinstance(item.get("item"), dict) else {}
    raw = question.get("team_draw_mystery_steps")
    steps: list[str] = []
    if isinstance(raw, list):
        for entry in raw[:3]:
            text = str(entry or "").strip()
            if text:
                steps.append(text[:200])
    while len(steps) < 3:
        steps.append(DEFAULT_MYSTERY_STEPS[len(steps)])
    return steps[:3]


def decode_png_payload(image_b64: str | None) -> bytes:
    """Decode a data-URL or raw base64 PNG, or raise ValueError."""
    raw = str(image_b64 or "").strip()
    if not raw:
        raise ValueError("Drawing image is required.")
    if raw.startswith("data:"):
        comma = raw.find(",")
        if comma < 0:
            raise ValueError("Invalid image data.")
        raw = raw[comma + 1 :]
    raw = re.sub(r"\s+", "", raw)
    try:
        blob = base64.b64decode(raw, validate=True)
    except (TypeError, ValueError) as exc:
        raise ValueError("Invalid image data.") from exc
    if len(blob) > MAX_IMAGE_BYTES:
        raise ValueError(DRAWING_TOO_LARGE_ERROR)
    if not blob.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("Only PNG drawings are supported.")
    return blob


def _png_chunks(blob: bytes):
    """Yield ``(type, data)`` for each chunk after the PNG signature.

    Args:
        blob: Raw PNG bytes.

    Raises:
        ValueError: The bytes are not a readable PNG.
    """
    if len(blob) < 8 or not blob.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("Invalid image data.")
    pos = 8
    while pos + 12 <= len(blob):
        length = int.from_bytes(blob[pos : pos + 4], "big")
        if pos + 12 + length > len(blob):
            raise ValueError("Invalid image data.")
        kind = blob[pos + 4 : pos + 8]
        data = blob[pos + 8 : pos + 8 + length]
        pos += 12 + length
        yield kind, data
        if kind == b"IEND":
            return
    raise ValueError("Invalid image data.")


def _paeth(left: int, up: int, up_left: int) -> int:
    """PNG Paeth predictor for one reconstructed byte.

    Args:
        left: Byte ``bpp`` positions to the left on this row.
        up: Byte directly above.
        up_left: Byte ``bpp`` positions left of the byte above.
    """
    estimate = left + up - up_left
    dist_left = abs(estimate - left)
    dist_up = abs(estimate - up)
    dist_up_left = abs(estimate - up_left)
    if dist_left <= dist_up and dist_left <= dist_up_left:
        return left
    if dist_up <= dist_up_left:
        return up
    return up_left


def _remember_pixel(
    seen: set[tuple[int, int, int, int] | None],
    red: int,
    green: int,
    blue: int,
    alpha: int,
) -> bool:
    """Record one pixel. Transparent pixels share a single background key.

    Args:
        seen: Colours found so far. ``None`` is the clear background.
        red: Red channel, 0–255.
        green: Green channel, 0–255.
        blue: Blue channel, 0–255.
        alpha: Alpha channel, 0–255. Zero is background.

    Returns:
        True once a second colour appears, which means the canvas has ink.
    """
    if alpha == 0:
        seen.add(None)
    else:
        seen.add((red & 255, green & 255, blue & 255, alpha & 255))
    return len(seen) >= 2


def _reject_oversized_png(width: int, height: int) -> None:
    """Refuse a canvas that would expand far past a whiteboard.

    Non-positive sizes are left for the caller. A side over
    ``MAX_PNG_SIDE`` or a pixel count over ``MAX_PNG_PIXELS`` raises
    before any inflate.

    Args:
        width: IHDR width.
        height: IHDR height.

    Raises:
        ValueError: ``DRAWING_TOO_LARGE_ERROR`` when the canvas is over the cap.
    """
    if width <= 0 or height <= 0:
        return
    if width > MAX_PNG_SIDE or height > MAX_PNG_SIDE or width * height > MAX_PNG_PIXELS:
        raise ValueError(DRAWING_TOO_LARGE_ERROR)


def _reconstruct_row(kind: int, row: bytes, prior: bytes, bpp: int) -> bytes | bytearray:
    """Undo one PNG filter and return the reconstructed row.

    Filter None is already reconstructed, so those rows are returned as-is.
    The other filters rebuild each byte from the row above and the byte
    to the left.

    Args:
        kind: Filter type 0–4.
        row: Filtered row bytes, without the filter byte.
        prior: Previous reconstructed row. Empty bytes stand in for the first row.
        bpp: Bytes per pixel.

    Raises:
        ValueError: ``kind`` is not a PNG filter.
    """
    if kind == 0:
        return row
    if kind not in (1, 2, 3, 4):
        raise ValueError("Invalid image data.")
    recon = bytearray(len(row))
    for index, value in enumerate(row):
        left = recon[index - bpp] if index >= bpp else 0
        above = prior[index] if prior else 0
        above_left = prior[index - bpp] if prior and index >= bpp else 0
        if kind == 1:
            recon[index] = (value + left) & 255
        elif kind == 2:
            recon[index] = (value + above) & 255
        elif kind == 3:
            recon[index] = (value + ((left + above) // 2)) & 255
        else:
            recon[index] = (value + _paeth(left, above, above_left)) & 255
    return recon


def _iter_unfiltered_rows(idat: bytes, height: int, stride: int, bpp: int):
    """Yield each unfiltered row, inflating at most the declared image.

    The caller can stop iterating to stop decompression. Output past
    ``height * (stride + 1)`` bytes raises ``DRAWING_TOO_LARGE_ERROR``.
    A short or corrupt stream raises ``ValueError``.

    Args:
        idat: Concatenated IDAT bytes (zlib wrapper).
        height: Declared pixel height.
        stride: Unfiltered bytes per row.
        bpp: Bytes per pixel.
    """
    if height <= 0 or stride <= 0 or bpp <= 0:
        raise ValueError("Invalid image data.")
    row_in = stride + 1
    limit = height * row_in
    if limit > MAX_DECOMPRESSED_BYTES:
        raise ValueError(DRAWING_TOO_LARGE_ERROR)
    decomp = zlib.decompressobj()
    pending = bytearray()
    produced = 0
    prior = b""
    src = memoryview(idat)
    pos = 0

    def pull() -> None:
        """Fill ``pending`` until one filtered row is buffered, or the stream ends."""
        nonlocal pos, produced
        while len(pending) < row_in:
            room = min(row_in - len(pending), limit - produced)
            if room <= 0:
                return
            if decomp.unconsumed_tail:
                data = decomp.unconsumed_tail
            elif pos < len(src):
                take = min(65536, len(src) - pos)
                data = bytes(src[pos : pos + take])
                pos += take
            else:
                try:
                    piece = b"" if decomp.eof else decomp.flush()
                except zlib.error as exc:
                    raise ValueError("Invalid image data.") from exc
                if not piece:
                    return
                if produced + len(piece) > limit:
                    raise ValueError(DRAWING_TOO_LARGE_ERROR)
                produced += len(piece)
                pending.extend(piece)
                continue
            try:
                piece = decomp.decompress(data, room)
            except zlib.error as exc:
                raise ValueError("Invalid image data.") from exc
            if not piece:
                if decomp.unconsumed_tail or pos < len(src):
                    if decomp.unconsumed_tail:
                        raise ValueError("Invalid image data.")
                    continue
                try:
                    piece = b"" if decomp.eof else decomp.flush()
                except zlib.error as exc:
                    raise ValueError("Invalid image data.") from exc
                if not piece:
                    return
            if produced + len(piece) > limit:
                raise ValueError(DRAWING_TOO_LARGE_ERROR)
            produced += len(piece)
            pending.extend(piece)

    for _ in range(height):
        pull()
        if len(pending) < row_in:
            raise ValueError("Invalid image data.")
        kind = pending[0]
        row = bytes(pending[1:row_in])
        del pending[:row_in]
        recon = _reconstruct_row(kind, row, prior, bpp)
        prior = recon if isinstance(recon, bytes) else bytes(recon)
        yield recon
    if pending:
        raise ValueError(DRAWING_TOO_LARGE_ERROR)
    try:
        extra = b""
        if decomp.unconsumed_tail:
            extra = decomp.decompress(decomp.unconsumed_tail, 1)
        elif pos < len(src):
            extra = decomp.decompress(bytes(src[pos:]), 1)
        flushed = b"" if decomp.eof or extra else decomp.flush()
    except zlib.error as exc:
        raise ValueError("Invalid image data.") from exc
    if extra or flushed:
        raise ValueError(DRAWING_TOO_LARGE_ERROR)


def _scan_row(
    seen: set[tuple[int, int, int, int] | None],
    recon: bytes,
    color_type: int,
    trns: bytes,
    colours: list[tuple[int, int, int]],
    alphas: list[int],
) -> bool:
    """Return True when this row introduces a second colour.

    Args:
        seen: Colours already found. Updated in place.
        recon: Unfiltered row bytes.
        color_type: PNG colour type 0, 2, 3, 4, or 6.
        trns: Raw tRNS chunk, possibly empty.
        colours: Palette RGB tuples. Used for colour type 3.
        alphas: Palette alpha bytes. Used for colour type 3.

    Raises:
        ValueError: The row does not match the colour type.
    """
    if color_type == 6:
        if len(recon) % 4:
            raise ValueError("Invalid image data.")
        for index in range(0, len(recon), 4):
            if _remember_pixel(
                seen, recon[index], recon[index + 1], recon[index + 2], recon[index + 3]
            ):
                return True
        return False
    if color_type == 2:
        if len(recon) % 3:
            raise ValueError("Invalid image data.")
        transparent = None
        if len(trns) >= 6:
            transparent = (trns[1], trns[3], trns[5])
        for index in range(0, len(recon), 3):
            pixel = (recon[index], recon[index + 1], recon[index + 2])
            alpha = 0 if transparent is not None and pixel == transparent else 255
            if _remember_pixel(seen, pixel[0], pixel[1], pixel[2], alpha):
                return True
        return False
    if color_type == 0:
        transparent_gray = trns[1] if len(trns) >= 2 else None
        for gray in recon:
            alpha = 0 if transparent_gray is not None and gray == transparent_gray else 255
            if _remember_pixel(seen, gray, gray, gray, alpha):
                return True
        return False
    if color_type == 4:
        if len(recon) % 2:
            raise ValueError("Invalid image data.")
        for index in range(0, len(recon), 2):
            gray, alpha = recon[index], recon[index + 1]
            if _remember_pixel(seen, gray, gray, gray, alpha):
                return True
        return False
    for index in recon:
        if index >= len(colours):
            raise ValueError("Invalid image data.")
        red, green, blue = colours[index]
        if _remember_pixel(seen, red, green, blue, alphas[index]):
            return True
    return False


def _row_changes_colours(
    seen: set[tuple[int, int, int, int] | None],
    recon: bytes | bytearray,
    previous: bytes | None,
    color_type: int,
    trns: bytes,
    colours: list[tuple[int, int, int]],
    alphas: list[int],
    channels: int,
) -> tuple[bool, bytes]:
    """Apply one reconstructed row to the colours seen so far.

    An all-zero row is a single pixel of that colour. A row whose bytes
    match the previous row cannot add a colour, so it is not scanned.

    Args:
        seen: Colours already found. Updated in place.
        recon: Unfiltered row bytes.
        previous: Previous reconstructed row, or None on the first row.
        color_type: PNG colour type 0, 2, 3, 4, or 6.
        trns: Raw tRNS chunk, possibly empty.
        colours: Palette RGB tuples. Used for colour type 3.
        alphas: Palette alpha bytes. Used for colour type 3.
        channels: Bytes per pixel.

    Returns:
        ``(ink, snapshot)``. ``ink`` is True when a second colour appears.
        ``snapshot`` is the row the next call should treat as previous.

    Raises:
        ValueError: The row does not match the colour type.
    """
    row = recon if isinstance(recon, bytes) else bytes(recon)
    if previous is not None and row == previous:
        return False, previous
    if (
        channels > 0
        and len(row) >= channels
        and len(row) % channels == 0
        and not any(row)
    ):
        return (
            _scan_row(seen, row[:channels], color_type, trns, colours, alphas),
            row,
        )
    return _scan_row(seen, row, color_type, trns, colours, alphas), row


def png_has_ink(blob: bytes) -> bool:
    """True when the PNG shows more than a blank canvas.

    Width and height are read from the header and refused before inflate
    when a side is over ``MAX_PNG_SIDE`` or the pixel count is over
    ``MAX_PNG_PIXELS`` (8 million). Inflation stops at the declared size.
    All-zero rows and rows that repeat the previous row are not scanned
    pixel by pixel. Any other row stops at the first pixel of a second colour.

    Fully transparent pixels count as one background, even when their
    colour channels differ. A flat fill is a single colour. Either case
    has no ink. Any second colour is a stroke.

    Args:
        blob: Raw PNG bytes.

    Raises:
        ValueError: The PNG cannot be read, or it is over the canvas cap
            (``DRAWING_TOO_LARGE_ERROR``).
    """
    width = height = bit_depth = color_type = interlace = None
    palette = b""
    trns = b""
    idat = bytearray()
    for kind, data in _png_chunks(blob):
        if kind == b"IHDR":
            if len(data) < 13:
                raise ValueError("Invalid image data.")
            (
                width,
                height,
                bit_depth,
                color_type,
                _compression,
                _png_filter,
                interlace,
            ) = struct.unpack(">IIBBBBB", data[:13])
            if _compression != 0 or _png_filter != 0:
                raise ValueError("Invalid image data.")
            _reject_oversized_png(int(width), int(height))
        elif kind == b"PLTE":
            palette = data
        elif kind == b"tRNS":
            trns = data
        elif kind == b"IDAT":
            if width is None:
                raise ValueError("Invalid image data.")
            idat.extend(data)
    if width is None or bit_depth != 8 or interlace not in (0, None):
        raise ValueError("Invalid image data.")
    if int(width) <= 0 or int(height) <= 0:
        return False
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(int(color_type))
    if channels is None:
        raise ValueError("Invalid image data.")
    colours: list[tuple[int, int, int]] = []
    alphas: list[int] = []
    if int(color_type) == 3:
        if len(palette) < 3 or len(palette) % 3:
            raise ValueError("Invalid image data.")
        colours = [
            (palette[index], palette[index + 1], palette[index + 2])
            for index in range(0, len(palette), 3)
        ]
        alphas = [255] * len(colours)
        for index, alpha in enumerate(trns[: len(colours)]):
            alphas[index] = alpha
    seen: set[tuple[int, int, int, int] | None] = set()
    previous: bytes | None = None
    for recon in _iter_unfiltered_rows(bytes(idat), int(height), int(width) * channels, channels):
        ink, previous = _row_changes_colours(
            seen,
            recon,
            previous,
            int(color_type),
            trns,
            colours,
            alphas,
            channels,
        )
        if ink:
            return True
    return False


def load_inked_png(image_b64: str | None) -> bytes:
    """Decode a PNG drawing and refuse a blank or oversized canvas.

    Args:
        image_b64: Data URL or raw base64 PNG.

    Returns:
        Raw PNG bytes that contain ink.

    Raises:
        ValueError: The image is missing, not a PNG, unreadable, blank,
            or larger than the canvas cap. A blank canvas raises
            ``BLANK_DRAWING_ERROR``. An oversized canvas raises
            ``DRAWING_TOO_LARGE_ERROR``.
    """
    blob = decode_png_payload(image_b64)
    try:
        inked = png_has_ink(blob)
    except ValueError as exc:
        if str(exc) == DRAWING_TOO_LARGE_ERROR:
            raise
        raise ValueError("Invalid image data.") from exc
    if not inked:
        raise ValueError(BLANK_DRAWING_ERROR)
    return blob


def team_draw_answer_json(title: str = "") -> dict[str, Any]:
    """Persisted draw answer. The drawing itself is not stored here.

    Args:
        title: Optional leftover label. New submits send none.
    """
    clean = str(title or "").strip()[:MAX_TITLE_LEN]
    return {"kind": TEAM_DRAW_KIND, "title": clean, "value": clean}


def status_label(submitted: bool) -> str:
    """Teacher grid status copy."""
    return "✓ submitted" if submitted else "drawing…"


def gallery_row_title(row: dict[str, Any]) -> str:
    """Optional title line for one draw tile. Empty when the submit had none."""
    final = row.get("final_answer") if isinstance(row.get("final_answer"), dict) else {}
    return str(final.get("title") or "").strip()


def should_snapshot_team_draw_run(
    school: SchoolDB,
    class_id: int,
    run_key: str,
    *,
    live_run: bool = False,
) -> bool:
    """Skip persisting Team Draw rows into live_result_snapshots on test runs."""
    from participation_exclusion import participation_run_counts_in_tab

    kind = school.participation_run_kind(class_id, run_key)
    if kind is None:
        # End Live writes the snapshot before ``participation_runs`` exists.
        return bool(live_run)
    return participation_run_counts_in_tab(kind)


def strip_image_from_snapshot_answer(answer: dict[str, Any]) -> dict[str, Any]:
    """Remove any ephemeral image field before writing snapshots."""
    clean = dict(answer)
    clean.pop("image", None)
    clean.pop("image_png", None)
    return clean
