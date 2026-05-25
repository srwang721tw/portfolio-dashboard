"""
Google Drive integration via Service Account.

Env vars required:
  GOOGLE_SERVICE_ACCOUNT_JSON  - full JSON content of service account key
  GOOGLE_DRIVE_FOLDER_ID       - ID of the shared Drive folder
"""

import io
import json
import os
from pathlib import Path
from typing import Optional

_GDRIVE_OK = False
try:
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload
    _GDRIVE_OK = True
except ImportError:
    pass


def is_configured() -> bool:
    return (
        _GDRIVE_OK
        and bool(os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON"))
        and bool(os.environ.get("GOOGLE_DRIVE_FOLDER_ID"))
    )


def _service():
    if not is_configured():
        return None
    try:
        info = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
        creds = service_account.Credentials.from_service_account_info(
            info,
            scopes=["https://www.googleapis.com/auth/drive.file"],
        )
        return build("drive", "v3", credentials=creds, cache_discovery=False)
    except Exception:
        return None


def _folder() -> str:
    return os.environ.get("GOOGLE_DRIVE_FOLDER_ID", "")


def upload(local_path: Path) -> bool:
    """Upload or update a file in the Drive folder."""
    svc = _service()
    if not svc or not local_path.exists():
        return False
    try:
        name = local_path.name
        fid = _folder()
        existing = (
            svc.files()
            .list(
                q=f"name='{name}' and '{fid}' in parents and trashed=false",
                fields="files(id)",
                spaces="drive",
            )
            .execute()
            .get("files", [])
        )
        media = MediaFileUpload(str(local_path), resumable=False)
        if existing:
            svc.files().update(fileId=existing[0]["id"], media_body=media).execute()
        else:
            svc.files().create(
                body={"name": name, "parents": [fid]}, media_body=media
            ).execute()
        return True
    except Exception:
        return False


def download(filename: str, local_path: Path) -> bool:
    """Download a named file from Drive folder to local path."""
    svc = _service()
    if not svc:
        return False
    try:
        fid = _folder()
        files = (
            svc.files()
            .list(
                q=f"name='{filename}' and '{fid}' in parents and trashed=false",
                fields="files(id)",
                spaces="drive",
            )
            .execute()
            .get("files", [])
        )
        if not files:
            return False

        request = svc.files().get_media(fileId=files[0]["id"])
        buf = io.BytesIO()
        dl = MediaIoBaseDownload(buf, request)
        done = False
        while not done:
            _, done = dl.next_chunk()

        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(buf.getvalue())
        return True
    except Exception:
        return False


def sync_down_all():
    """Pull all data files from Drive if not already local. Call once per session."""
    if not is_configured():
        return
    from config.settings import (
        USERS_FILE, PLEDGE_FILE, HISTORY_FILE, TW_CSV_FILE, US_CSV_FILE
    )
    for fname, path in [
        ("users.json",              USERS_FILE),
        ("pledge_config.json",      PLEDGE_FILE),
        ("portfolio_history.json",  HISTORY_FILE),
        ("tw_stocks.csv",           TW_CSV_FILE),
        ("us_stocks.csv",           US_CSV_FILE),
    ]:
        if not path.exists():
            download(fname, path)
