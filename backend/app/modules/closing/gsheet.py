"""Google Sheets read access for the daily-closing auto-import, via a service account.

The service-account JSON is read from the env var GOOGLE_SERVICE_ACCOUNT_JSON (never stored in the
DB or returned by the API). google-* imports are kept inside the function so the closing module
still imports fine if the libs aren't installed yet — only an actual sweep needs them.
"""
import os
import json

SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]


def sa_info() -> dict | None:
    """Parse the service-account JSON from the env (or None if unset/invalid)."""
    raw = (os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON") or "").strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def sa_email() -> str | None:
    info = sa_info()
    return (info or {}).get("client_email")


def fetch_values(sheet_id: str, tab: str | None = None) -> tuple[list[list], str]:
    """Return (rows, tab_used) for a spreadsheet tab. rows[0] is the header. Raises with a clear
    message if the SA key is missing or the sheet isn't shared with the service account."""
    info = sa_info()
    if not info:
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON is not set on the server.")
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    svc = build("sheets", "v4", credentials=creds, cache_discovery=False)
    if not (tab or "").strip():
        meta = svc.spreadsheets().get(spreadsheetId=sheet_id, fields="sheets.properties.title").execute()
        sheets = meta.get("sheets", [])
        tab = sheets[0]["properties"]["title"] if sheets else "Sheet1"
    res = svc.spreadsheets().values().get(spreadsheetId=sheet_id, range=f"'{tab}'").execute()
    return res.get("values", []), tab
