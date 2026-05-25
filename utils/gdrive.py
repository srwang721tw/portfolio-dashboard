"""
Google Drive integration via Service Account.

Required env vars:
  GOOGLE_SERVICE_ACCOUNT_JSON  – full JSON content of the service account key file
  GOOGLE_DRIVE_FOLDER_ID       – ID of the Drive folder shared with the service account

Security note: NEVER commit the JSON key file to git.
Store it as the GOOGLE_SERVICE_ACCOUNT_JSON env var in Railway (or .env locally).
"""

import io
import json
import os
from pathlib import Path

_OK = False
try:
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload
    _OK = True
except ImportError:
    pass

_SCOPES = [
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/spreadsheets",
]


def is_configured() -> bool:
    return (
        _OK
        and bool(os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON"))
        and bool(os.environ.get("GOOGLE_DRIVE_FOLDER_ID"))
    )


def _service():
    if not is_configured():
        return None
    try:
        info  = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
        creds = service_account.Credentials.from_service_account_info(info, scopes=_SCOPES)
        return build("drive", "v3", credentials=creds, cache_discovery=False)
    except Exception:
        return None


def _folder() -> str:
    return os.environ.get("GOOGLE_DRIVE_FOLDER_ID", "")


def upload(local_path: Path) -> bool:
    svc = _service()
    if not svc or not local_path.exists():
        return False
    try:
        name = local_path.name
        fid  = _folder()
        existing = (
            svc.files()
            .list(q=f"name='{name}' and '{fid}' in parents and trashed=false",
                  fields="files(id)", spaces="drive")
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


def download(filename: str, local_path: Path, force: bool = False) -> bool:
    """Download file from Drive. If force=True, overwrite even if local exists."""
    if not force and local_path.exists():
        return True
    svc = _service()
    if not svc:
        return False
    try:
        fid   = _folder()
        files = (
            svc.files()
            .list(q=f"name='{filename}' and '{fid}' in parents and trashed=false",
                  fields="files(id)", spaces="drive")
            .execute()
            .get("files", [])
        )
        if not files:
            return False
        request = svc.files().get_media(fileId=files[0]["id"])
        buf  = io.BytesIO()
        dl   = MediaIoBaseDownload(buf, request)
        done = False
        while not done:
            _, done = dl.next_chunk()
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(buf.getvalue())
        return True
    except Exception:
        return False


def sync_down_all():
    """
    Pull all data files from Drive on session start.
    - users.json: always overwrite (so Railway redeployments don't lose accounts)
    - Everything else: only if not already local
    """
    if not is_configured():
        return
    from config.settings import (
        USERS_FILE, PLEDGE_FILE, HISTORY_FILE, TW_CSV_FILE, US_CSV_FILE
    )
    download("users.json",             USERS_FILE,   force=True)   # always pull
    download("pledge_config.json",     PLEDGE_FILE,  force=False)
    download("portfolio_history.json", HISTORY_FILE, force=False)
    download("tw_stocks.csv",          TW_CSV_FILE,  force=False)
    download("us_stocks.csv",          US_CSV_FILE,  force=False)
