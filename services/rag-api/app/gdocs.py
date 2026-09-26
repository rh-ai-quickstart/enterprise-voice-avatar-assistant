"""Google Docs for transcript archival, through the Drive API with a service account.

No OAuth consent screen and no sign-in: the service account (GOOGLE_SERVICE_ACCOUNT_JSON)
creates the document in a Drive folder shared with it (GOOGLE_DOCS_FOLDER_ID). The transcript is
uploaded as plain text and Drive converts it into a Google Doc. Only when GOOGLE_DOCS_ENABLED is
true (integrations.googleDocs.enabled in the chart); archival works without it.
"""

from __future__ import annotations

import json
import logging

from .config import settings

log = logging.getLogger("rag.gdocs")

DRIVE_SCOPE = "https://www.googleapis.com/auth/drive"
UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3/files"
BOUNDARY = "assistant-transcript-boundary"


def keys_present() -> bool:
    return bool(settings.google_service_account_json and settings.google_docs_folder_id)


def configured() -> bool:
    """Archival creates Google Docs: the deployment turned the integration on and the keys are there."""
    return settings.google_docs_enabled and keys_present()


def service_account_email() -> str | None:
    """The address the Drive folder must be shared with."""
    try:
        return json.loads(settings.google_service_account_json or "{}").get("client_email")
    except json.JSONDecodeError:
        return None


def _session():
    from google.auth.transport.requests import AuthorizedSession
    from google.oauth2 import service_account

    info = json.loads(settings.google_service_account_json or "{}")
    credentials = service_account.Credentials.from_service_account_info(info, scopes=[DRIVE_SCOPE])
    return AuthorizedSession(credentials)


def multipart_body(metadata: dict, text: str) -> bytes:
    """A multipart/related upload body: the file metadata, then the plain-text content."""
    head = (
        f"--{BOUNDARY}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n{json.dumps(metadata)}\r\n"
        f"--{BOUNDARY}\r\nContent-Type: text/plain; charset=UTF-8\r\n\r\n"
    )
    return head.encode("utf-8") + text.encode("utf-8") + f"\r\n--{BOUNDARY}--".encode()


def create_document(title: str, text: str) -> str | None:
    """Create a Google Doc with the text in the configured folder; the link, or None when not
    configured or when Drive refuses (logged, never raised: archival must not depend on it)."""
    if not configured():
        return None
    metadata = {
        "name": title,
        "mimeType": "application/vnd.google-apps.document",
        "parents": [settings.google_docs_folder_id],
    }
    try:
        response = _session().post(
            UPLOAD_URL,
            params={"uploadType": "multipart", "supportsAllDrives": "true", "fields": "id,webViewLink"},
            data=multipart_body(metadata, text),
            headers={"Content-Type": f"multipart/related; boundary={BOUNDARY}"},
            timeout=60,
        )
        if response.status_code >= 400:
            log.warning(
                "Google Drive refused the document (%s): %s", response.status_code, response.text[:300]
            )
            return None
        body = response.json()
        link = body.get("webViewLink") or f"https://docs.google.com/document/d/{body['id']}/edit"
        log.info("Google Doc created: %s", link)
        return link
    except Exception as exc:  # noqa: BLE001 - network, auth or JSON problems are all "no document"
        log.warning("Google Doc not created: %s", exc)
        return None
