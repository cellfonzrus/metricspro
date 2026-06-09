import pandas as pd
import numpy as np
from io import BytesIO

MONEY_COLS = [
    "Owed to VIP", "On Inventory", "Reimbursement", "Commissions",
    "Total Owed", "Total Reimbursed",
]

COL_MAP = {
    "ESN/IMEI": "esn_imei",
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
    "Total Owed": "total_owed",
    "Total Reimbursed": "total_reimbursed",
    "Notes": "notes",
}

def parse_asset_ledger(file_bytes: bytes, org_id: str) -> list[dict]:
    df = pd.read_excel(BytesIO(file_bytes), dtype=str)
    df.columns = df.columns.str.strip()

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
        # store full raw row for debugging
        r["raw_row"] = {k: str(v) for k, v in row.items() if pd.notna(v)}
        rows.append(r)

    return rows
