import pandas as pd
import numpy as np
from io import BytesIO

MONEY_COLS = [
    "Owed to VIP", "On Inventory", "Reimbursement", "Commissions",
]

COL_MAP = {
    "ESN": "esn_imei",
    "Phone Number": "phone_number",
    "Contract Type": "contract_type",
    "Category": "category",
    "Status": "status",
    "Date Sold": "date_sold",
    "SFID": "sfid",
    "Owed to VIP": "owed_to_vip",
    "On Inventory": "on_inventory",
    "Reimbursement": "reimbursement",
    "Commissions": "commissions",
    "Notes": "notes",
    "Billing Address 1": "store",
    "item": "device_model",
}

# Date columns pulled from the file (parsed to YYYY-MM-DD below)
DATE_SRC = {
    "Date": "acquired_date",
    "Due Date": "due_date",
    "ESN Added Pay as You Go": "payg_date",
    "Reimbursement Date": "reimbursement_date",
}

def _is_anchor(cell) -> bool:
    """The ESN/device-key column marks the real header row."""
    return str(cell).strip().upper() in ("ESN", "ESN/IMEI", "IMEI", "ESN NUMBER")


def _read_asset_df(file_bytes: bytes):
    """Find the sheet + header row that actually holds the Asset_Lending data. Tolerant of a cover/summary
    sheet placed first and of title rows above the header (the common reasons a valid file 'fails to
    upload' when only the first sheet / row 1 is read). Raises a CLEAR error naming what was found."""
    try:
        book = pd.read_excel(BytesIO(file_bytes), sheet_name=None, header=None, dtype=str)
    except Exception as e:
        raise ValueError(f"Could not open the Asset_Lending file as a spreadsheet: {e}")
    tried = []
    for sheet, raw in (book or {}).items():
        if raw is None or raw.empty:
            tried.append(f"'{sheet}'(empty)")
            continue
        for hdr in range(min(15, len(raw))):   # scan leading rows for the header
            if any(_is_anchor(c) for c in raw.iloc[hdr].tolist()):
                df = pd.read_excel(BytesIO(file_bytes), sheet_name=sheet, header=hdr, dtype=str)
                df.columns = df.columns.str.strip()
                if len(df.dropna(how="all")) > 0:
                    return df
        tried.append(f"'{sheet}'({len(raw)} rows)")
    raise ValueError(
        "No device rows found in the Asset_Lending file — couldn't locate an 'ESN' column header in any "
        f"sheet (looked in: {', '.join(tried) or 'no sheets'}). Make sure the sheet with the device list "
        "(the one whose header row has 'ESN', 'Category', 'Status', 'Owed to VIP', …) is included.")


def parse_asset_ledger(file_bytes: bytes, org_id: str) -> list[dict]:
    df = _read_asset_df(file_bytes)

    # Clean money cols
    for col in MONEY_COLS:
        if col in df.columns:
            df[col] = (
                df[col]
                .str.replace(r"[$,]", "", regex=True)
                .str.strip()
                .replace({"": None, "nan": None, "NaN": None})
            )
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    # Date col
    if "Date Sold" in df.columns:
        df["Date Sold"] = pd.to_datetime(df["Date Sold"], errors="coerce").dt.strftime("%Y-%m-%d")
        df["Date Sold"] = df["Date Sold"].where(df["Date Sold"].notna(), None)
    # Parse the additional date columns (acquired / due / PAYG)
    for src_col in DATE_SRC:
        if src_col in df.columns:
            df[src_col] = pd.to_datetime(df[src_col], errors="coerce").dt.strftime("%Y-%m-%d")
            df[src_col] = df[src_col].where(df[src_col].notna(), None)

    rows = []
    for _, row in df.iterrows():
        r = {"org_id": org_id, "raw_row": {}}
        for src, dst in COL_MAP.items():
            val = row.get(src)
            if val is None or (isinstance(val, float) and np.isnan(val)):
                val = None
            elif isinstance(val, str) and val.strip() in ("", "nan", "NaT"):
                val = None
            r[dst] = val
        # Map the additional date columns
        for src_col, dst in DATE_SRC.items():
            v = row.get(src_col)
            if v is None or (isinstance(v, float) and np.isnan(v)):
                v = None
            elif isinstance(v, str) and v.strip() in ("", "nan", "NaT", "None"):
                v = None
            r[dst] = v

        # Derived billing fields (must match the SQL reconciliation rules):
        #   trigger = PAYG date if present, else due_date
        #   bill_path = 'billed' if PAYG present, else 'aging' (else None)
        #   billing_friday = first Friday ON or AFTER the trigger date
        payg = r.get("payg_date")
        due  = r.get("due_date")
        trig = payg or due
        r["trigger_date"] = trig
        r["bill_path"] = "billed" if payg else ("aging" if due else None)
        r["billing_friday"] = None
        if trig:
            try:
                td = pd.to_datetime(trig).date()
                # Mon=0 .. Fri=4
                offset = (4 - td.weekday() + 7) % 7
                from datetime import timedelta
                r["billing_friday"] = (td + timedelta(days=offset)).strftime("%Y-%m-%d")
            except Exception:
                r["billing_friday"] = None

        # store full raw row for debugging
        r["raw_row"] = {k: str(v) for k, v in row.items() if pd.notna(v)}
        rows.append(r)

    return rows
