"""
Google Drive integration via Service Account.

Required env vars:
  GOOGLE_SERVICE_ACCOUNT_JSON  – full JSON content of the service account key file
  GOOGLE_DRIVE_FOLDER_ID       – ID of the Drive folder shared with the service account

Note: Service accounts cannot CREATE new files on personal Google Drive (no storage quota).
      They can only UPDATE files that already exist in the folder.
      Run `python setup_drive.py` once to create placeholder files before first deploy.
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

# drive scope (not drive.file) so we can read files owned by the user
_SCOPES = [
    "https://www.googleapis.com/auth/drive",
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
    """
    Upload a local file to Drive.
    Only UPDATES existing files (service accounts cannot create on personal Drive).
    """
    svc = _service()
    if not svc or not local_path.exists():
        return False
    try:
        name = local_path.name
        fid  = _folder()
        existing = (
            svc.files()
            .list(q=f"name='{name}' and '{fid}' in parents and trashed=false",
                  fields="files(id)")
            .execute()
            .get("files", [])
        )
        if not existing:
            return False  # File must be pre-created by folder owner (see setup_drive.py)
        media = MediaFileUpload(str(local_path), resumable=False)
        svc.files().update(fileId=existing[0]["id"], media_body=media).execute()
        return True
    except Exception:
        return False


def download(filename: str, local_path: Path, force: bool = False) -> bool:
    """Download a named file from the Drive folder. force=True overwrites local copy."""
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
                  fields="files(id)", orderBy="modifiedTime desc")
            .execute()
            .get("files", [])
        )
        if not files:
            return False
        return _download_by_id(svc, files[0]["id"], local_path)
    except Exception:
        return False


def _download_by_id(svc, file_id: str, local_path: Path) -> bool:
    try:
        request = svc.files().get_media(fileId=file_id)
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


def _merge_csv_files(svc, name_contains: str, local_path: Path) -> bool:
    """Download all matching CSV files from Drive and merge + dedup into one file."""
    try:
        import tempfile
        import pandas as pd

        fid   = _folder()
        files = (
            svc.files()
            .list(
                q=f"name contains '{name_contains}' and '{fid}' in parents and trashed=false",
                fields="files(id,name)",
            )
            .execute()
            .get("files", [])
        )
        if not files:
            return False

        dfs = []
        for f in files:
            tmp = Path(tempfile.mktemp(suffix=".csv"))
            if _download_by_id(svc, f["id"], tmp):
                try:
                    for enc in ["utf-8", "utf-8-sig", "big5", "cp950"]:
                        try:
                            dfs.append(pd.read_csv(tmp, encoding=enc))
                            break
                        except Exception:
                            continue
                except Exception:
                    pass
                tmp.unlink(missing_ok=True)

        if not dfs:
            return False

        merged = pd.concat(dfs).drop_duplicates().reset_index(drop=True)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        merged.to_csv(local_path, index=False, encoding="utf-8")
        return True
    except Exception:
        return False


def sync_down_all():
    """
    Pull all data files from Drive on session start.

    tw_stocks.csv / us_stocks.csv:
      Prefer exact-name match; fall back to searching for 對帳單 / 複委託庫存 files
      (the raw exports the user puts in the folder).
    users.json / pledge / history:
      Always force-overwrite users.json so accounts survive Railway redeploys.
    """
    if not is_configured():
        return
    svc = _service()
    if not svc:
        return

    from config.settings import (
        USERS_FILE, PLEDGE_FILE, HISTORY_FILE, TW_CSV_FILE, US_CSV_FILE,
        US_COST_CONFIG_FILE,
    )

    # ── User / config files ────────────────────────────────────────────────
    download("users.json",             USERS_FILE,           force=True)   # always sync accounts
    download("pledge_config.json",     PLEDGE_FILE,          force=True)   # always sync pledges
    download("us_cost_config.json",    US_COST_CONFIG_FILE,  force=True)   # always sync US cost
    download("portfolio_history.json", HISTORY_FILE,         force=False)

    # ── TW stock CSV ───────────────────────────────────────────────────────
    if not download("tw_stocks.csv", TW_CSV_FILE, force=False):
        # Fall back: merge all 對帳單 files in folder
        _merge_csv_files(svc, "對帳單", TW_CSV_FILE)

    # ── US stock CSV ───────────────────────────────────────────────────────
    if not download("us_stocks.csv", US_CSV_FILE, force=False):
        # Fall back: use most recent 複委託庫存 file (no merge needed, latest is complete)
        _merge_csv_files(svc, "複委託庫存", US_CSV_FILE)
