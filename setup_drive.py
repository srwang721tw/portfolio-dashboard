"""
One-time setup: create placeholder files in Google Drive so the service account
can later update them (service accounts can update but not create on personal Drive).

Run once from your local machine BEFORE the first Railway deploy:
  python setup_drive.py

Requires GOOGLE_SERVICE_ACCOUNT_JSON and GOOGLE_DRIVE_FOLDER_ID to be set
(either in .env or environment).
"""

import json
import os
import sys
import tempfile
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

try:
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
except ImportError:
    print("Missing dependencies. Run: pip install google-api-python-client google-auth")
    sys.exit(1)


SA_JSON_STR = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
FOLDER_ID   = os.environ.get("GOOGLE_DRIVE_FOLDER_ID", "")

if not SA_JSON_STR or not FOLDER_ID:
    print("ERROR: Set GOOGLE_SERVICE_ACCOUNT_JSON and GOOGLE_DRIVE_FOLDER_ID in .env first.")
    sys.exit(1)

info  = json.loads(SA_JSON_STR)
creds = service_account.Credentials.from_service_account_info(
    info,
    scopes=["https://www.googleapis.com/auth/drive"],
)
svc = build("drive", "v3", credentials=creds, cache_discovery=False)

# Placeholder content for each file.
# CSVs only need a header row so the service account can later overwrite them
# via the Upload tab.  Real data is uploaded through the dashboard.
PLACEHOLDERS = {
    "users.json":             "{}",
    "pledge_config.json":     '{"loans": []}',
    "portfolio_history.json": "[]",
    "us_cost_config.json":    '{"us_twd_cost": 0}',
    "tw_stocks.csv":          "symbol,shares,cost_per_share,currency\n",
    "us_stocks.csv":          "symbol,shares,cost_per_share,currency\n",
}


def _file_exists(name: str) -> str | None:
    files = (
        svc.files()
        .list(
            q=f"name='{name}' and '{FOLDER_ID}' in parents and trashed=false",
            fields="files(id)",
        )
        .execute()
        .get("files", [])
    )
    return files[0]["id"] if files else None


def _create_or_update(name: str, content: str):
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json",
                                     delete=False, encoding="utf-8") as f:
        f.write(content)
        tmp = f.name
    try:
        media   = MediaFileUpload(tmp, mimetype="application/json", resumable=False)
        file_id = _file_exists(name)
        if file_id:
            svc.files().update(fileId=file_id, media_body=media).execute()
            print(f"  Updated  : {name}")
        else:
            # Create owned by the service account — works only if the folder
            # grants the service account write permission (Editor role).
            try:
                svc.files().create(
                    body={"name": name, "parents": [FOLDER_ID]},
                    media_body=media,
                ).execute()
                print(f"  Created  : {name}")
            except Exception as e:
                print(f"  SKIP     : {name} — cannot create ({e})")
                print(f"             → Please create it manually in Drive: {name}")
    finally:
        os.unlink(tmp)


print("Setting up Google Drive placeholder files...")
print(f"Folder ID: {FOLDER_ID}\n")
for filename, placeholder in PLACEHOLDERS.items():
    _create_or_update(filename, placeholder)

print("\nDone. Re-run after each manual account or data reset if needed.")
