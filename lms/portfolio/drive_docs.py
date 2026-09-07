"""Create/update the two-column assignment Doc in ``ALC/{year}/{term}/{CODE}/M{n}``."""

from __future__ import annotations

from typing import Any

from live_class_slides import GoogleSlidesClient, SlidesConnectRequired
from math_expectations_pdf import drive_upload_text
from slides_template import drive_year_semester


def ensure_portfolio_folder(
    client: GoogleSlidesClient,
    *,
    ontario_code: str,
    module_number: int,
    semester_label: str = "",
) -> str:
    """Create ``ALC / year / term / CODE / M{n}`` (not the slides ``Module n`` folder).

    Args:
        client: Authenticated Drive client.
        ontario_code: Course code folder name.
        module_number: 1-based module index.
        semester_label: Optional semester label override.

    Returns:
        Folder id.
    """
    from live_class_constants import ALC_DRIVE_FOLDER_ID, SETTING_ALC_DRIVE_FOLDER_ID

    alc = client.school.get_school_setting(
        SETTING_ALC_DRIVE_FOLDER_ID, ALC_DRIVE_FOLDER_ID
    ) or ALC_DRIVE_FOLDER_ID
    year, term = drive_year_semester(semester_label)
    year_id = client.find_or_create_folder(year, alc)
    term_id = client.find_or_create_folder(term, year_id)
    course_id = client.find_or_create_folder(str(ontario_code).upper(), term_id)
    return client.find_or_create_folder(f"M{int(module_number)}", course_id)


def find_named_file(client: GoogleSlidesClient, *, folder_id: str, name: str) -> str | None:
    """Return an existing Drive file id with this name in ``folder_id``.

    Args:
        client: Authenticated Drive client.
        folder_id: Parent folder.
        name: Exact file title.
    """
    query = (
        f"name = '{client._escape_drive_name(name)}' and "
        f"'{folder_id}' in parents and trashed = false"
    )
    listed = client.http.get(
        "https://www.googleapis.com/drive/v3/files",
        headers=client._headers(),
        params=client._drive_params(
            {"q": query, "fields": "files(id,name)", "pageSize": "5"}
        ),
        timeout=20,
    )
    listed.raise_for_status()
    files = (listed.json() or {}).get("files") or []
    if not files:
        return None
    return str(files[0].get("id") or "") or None


def list_folder_files(client: GoogleSlidesClient, folder_id: str) -> list[dict[str, str]]:
    """List Docs/PDF/HTML in a Drive folder.

    Args:
        client: Authenticated Drive client.
        folder_id: Parent folder.
    """
    listed = client.http.get(
        "https://www.googleapis.com/drive/v3/files",
        headers=client._headers(),
        params=client._drive_params(
            {
                "q": f"'{folder_id}' in parents and trashed = false",
                "fields": "files(id,name,mimeType,webViewLink)",
                "pageSize": "100",
            }
        ),
        timeout=20,
    )
    listed.raise_for_status()
    out: list[dict[str, str]] = []
    for row in (listed.json() or {}).get("files") or []:
        mime = str(row.get("mimeType") or "")
        name = str(row.get("name") or "")
        lower = name.lower()
        if (
            mime in {
                "application/vnd.google-apps.document",
                "application/pdf",
                "text/html",
            }
            or lower.endswith(".pdf")
            or lower.endswith(".html")
            or lower.endswith(".htm")
        ):
            out.append(
                {
                    "id": str(row.get("id") or ""),
                    "name": name,
                    "mimeType": mime,
                    "url": str(row.get("webViewLink") or ""),
                    "source": "drive",
                }
            )
    return out


def export_google_doc_text(client: GoogleSlidesClient, file_id: str) -> str:
    """Export a Google Doc as HTML then strip tags upstream.

    Args:
        client: Authenticated Drive client.
        file_id: Google file id.
    """
    resp = client.http.get(
        f"https://www.googleapis.com/drive/v3/files/{file_id}/export",
        headers=client._headers(),
        params={"mimeType": "text/plain"},
        timeout=30,
    )
    if getattr(resp, "ok", False):
        return str(resp.text or "")
    html = client.http.get(
        f"https://www.googleapis.com/drive/v3/files/{file_id}/export",
        headers=client._headers(),
        params={"mimeType": "text/html"},
        timeout=30,
    )
    html.raise_for_status()
    return str(html.text or "")


def create_or_update_assignment_doc(
    client: GoogleSlidesClient,
    *,
    ontario_code: str,
    module_number: int,
    title: str,
    doc_html: str,
    semester_label: str = "",
) -> dict[str, str]:
    """Upload two-column HTML as a Google Doc in the M{n} folder.

    Existing files with the same title are left in place and a new conversion
    is written (Drive HTML convert cannot patch in place reliably). Callers
    may pass an existing id to skip a duplicate when listing first.

    Args:
        client: Authenticated Drive client.
        ontario_code: Course code.
        module_number: Module index.
        title: Drive file name.
        doc_html: Two-column HTML.
        semester_label: Optional semester override.

    Returns:
        ``id`` and ``url``.

    Raises:
        SlidesConnectRequired: No Drive token.
    """
    folder_id = ensure_portfolio_folder(
        client,
        ontario_code=ontario_code,
        module_number=module_number,
        semester_label=semester_label,
    )
    existing = find_named_file(client, folder_id=folder_id, name=title)
    if existing:
        # Replace media by uploading a new converted Doc and trashing the old one.
        client.http.patch(
            f"https://www.googleapis.com/drive/v3/files/{existing}",
            headers=client._headers(),
            params=client._drive_params(),
            json={"trashed": True},
            timeout=15,
        )
    created = drive_upload_text(
        access_token=client._access_token(),
        parent_id=folder_id,
        title=title,
        text=doc_html,
        mime="text/html",
        drive_mime="application/vnd.google-apps.document",
        http=client.http,
    )
    file_id = str(created.get("id") or "")
    url = str(created.get("webViewLink") or f"https://docs.google.com/document/d/{file_id}/edit")
    return {"id": file_id, "url": url, "folder_id": folder_id}


def needs_drive_connect(exc: BaseException) -> bool:
    """True when the teacher still has to connect Slides/Drive OAuth."""
    return isinstance(exc, SlidesConnectRequired)
