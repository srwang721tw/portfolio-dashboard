"""
Google Sheets integration for pledge data.

Required env vars (same service account as Drive):
  GOOGLE_SERVICE_ACCOUNT_JSON  – same key used for Drive
  GOOGLE_PLEDGE_SHEET_ID       – ID of the Google Sheet for pledge data

Sheet structure ("質押明細" worksheet):
  說明 | 借款金額TWD | 年利率% | 借款日期 | 質押代號 | 質押股數 | 幣別

Multiple rows share the same 說明/借款金額/etc. when one loan has multiple stocks.

Setup:
  1. Enable Google Sheets API in Google Cloud Console (same project as Drive)
  2. Create a Google Sheet, rename the first sheet to "質押明細"
  3. Share the sheet with the service account email (Editor)
  4. Copy the Sheet ID from the URL: .../spreadsheets/d/<SHEET_ID>/edit
  5. Set GOOGLE_PLEDGE_SHEET_ID in Railway env vars
"""

import json
import os
from typing import List, Dict, Optional

import pandas as pd

_GSPREAD_OK = False
try:
    import gspread
    from google.oauth2 import service_account as _sa
    _GSPREAD_OK = True
except ImportError:
    pass

_HEADERS = ["說明", "借款金額TWD", "年利率%", "借款日期", "質押代號", "質押股數", "幣別"]
_SHEET_NAME = "質押明細"


def is_configured() -> bool:
    return (
        _GSPREAD_OK
        and bool(os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON"))
        and bool(os.environ.get("GOOGLE_PLEDGE_SHEET_ID"))
    )


def _gc():
    if not is_configured():
        return None
    try:
        info  = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
        creds = _sa.Credentials.from_service_account_info(
            info,
            scopes=[
                "https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive.file",
            ],
        )
        return gspread.authorize(creds)
    except Exception:
        return None


def _worksheet():
    gc = _gc()
    if not gc:
        return None
    sid = os.environ.get("GOOGLE_PLEDGE_SHEET_ID", "")
    try:
        sh = gc.open_by_key(sid)
        try:
            return sh.worksheet(_SHEET_NAME)
        except gspread.WorksheetNotFound:
            ws = sh.add_worksheet(_SHEET_NAME, rows=200, cols=7)
            ws.update([_HEADERS])
            return ws
    except Exception:
        return None


def load_pledge_from_sheet() -> List[Dict]:
    ws = _worksheet()
    if not ws:
        return []
    try:
        records = ws.get_all_records()
        if not records:
            return []
        df = pd.DataFrame(records)
        # Normalise column names
        df.columns = [c.strip() for c in df.columns]
        if "說明" not in df.columns:
            return []
        loans, loan_id = [], 1
        for desc, grp in df.groupby("說明", sort=False):
            row0 = grp.iloc[0]
            loans.append({
                "id":           loan_id,
                "description":  str(desc),
                "loan_amount_twd": float(str(row0.get("借款金額TWD", 0)).replace(",", "") or 0),
                "interest_rate":   float(str(row0.get("年利率%", 0)).replace("%", "") or 0),
                "date":            str(row0.get("借款日期", "")),
                "pledged_stocks": [
                    {
                        "symbol":   str(r.get("質押代號", "")).strip().upper(),
                        "shares":   int(float(str(r.get("質押股數", 0)).replace(",", "") or 0)),
                        "currency": str(r.get("幣別", "TWD")).strip().upper(),
                    }
                    for _, r in grp.iterrows()
                    if r.get("質押代號")
                ],
                "from_sheet": True,
            })
            loan_id += 1
        return loans
    except Exception:
        return []


def save_pledge_to_sheet(loans: List[Dict]) -> bool:
    ws = _worksheet()
    if not ws:
        return False
    try:
        rows = [_HEADERS]
        for loan in loans:
            for ps in loan.get("pledged_stocks", []):
                rows.append([
                    loan["description"],
                    loan["loan_amount_twd"],
                    loan["interest_rate"],
                    loan["date"],
                    ps["symbol"],
                    ps["shares"],
                    ps.get("currency", "TWD"),
                ])
        ws.clear()
        ws.update(rows)
        return True
    except Exception:
        return False


def sheet_url() -> Optional[str]:
    sid = os.environ.get("GOOGLE_PLEDGE_SHEET_ID", "")
    return f"https://docs.google.com/spreadsheets/d/{sid}" if sid else None
