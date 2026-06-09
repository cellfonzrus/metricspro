import pandas as pd
from io import BytesIO
from datetime import date

COLUMN_MAP = {
    "model": "device_model",
    "device": "device_model",
    "device model": "device_model",
    "srp": "srp",
    "suggested retail price": "srp",
    "promo port": "promo_port_in",
    "promo port-in": "promo_port_in",
    "port promo": "promo_port_in",
    "promo non-port": "promo_non_port",
    "non-port promo": "promo_non_port",
    "promo non port": "promo_non_port",
    "promo upgrade": "promo_upgrade",
    "upgrade promo": "promo_upgrade",
    "promo aal": "promo_aal",
    "aal promo": "promo_aal",
    "min plan port": "min_plan_port",
    "min plan non-port": "min_plan_non_port",
    "min plan upgrade": "min_plan_upgrade",
    "min plan aal": "min_plan_aal",
    "boost protect": "boost_protect_fee",
    "boost protect fee": "boost_protect_fee",
    "notes": "notes",
}

def _clean_money(val):
    if val is None:
        return None
    s = str(val).replace("$", "").replace(",", "").strip()
    if s in ("", "-", "N/A", "n/a", "FREE", "free"):
        return 0.0 if s in ("FREE", "free") else None
    try:
        return float(s)
    except Exception:
        return None

def _clean_plan(val):
    if val is None:
        return None
    s = str(val).replace("$", "").strip()
    try:
        return int(float(s))
    except Exception:
        return None

def parse_hotsheet(file_bytes: bytes, effective_date: date, org_id: str) -> list[dict]:
    """
    Parse a hotsheet PDF or CSV/Excel upload into rows for commcalc.hotsheet.
    Returns list of dicts ready for Supabase upsert.
    """
    # Try CSV first, then Excel
    try:
        df = pd.read_csv(BytesIO(file_bytes))
    except Exception:
        try:
            df = pd.read_excel(BytesIO(file_bytes))
        except Exception as e:
            raise ValueError(f"Could not parse hotsheet file: {e}")

    # Normalize column names
    df.columns = [str(c).lower().strip() for c in df.columns]
    rename = {}
    for col in df.columns:
        if col in COLUMN_MAP:
            rename[col] = COLUMN_MAP[col]
    df = df.rename(columns=rename)

    if "device_model" not in df.columns:
        raise ValueError("Could not find device model column in hotsheet. Expected column named 'Model' or 'Device'.")

    rows = []
    for _, row in df.iterrows():
        model = str(row.get("device_model", "")).strip()
        if not model or model.lower() in ("nan", "", "device", "model"):
            continue
        rows.append({
            "org_id": org_id,
            "effective_date": effective_date.isoformat(),
            "device_model": model,
            "srp": _clean_money(row.get("srp")),
            "promo_port_in": _clean_money(row.get("promo_port_in")),
            "promo_non_port": _clean_money(row.get("promo_non_port")),
            "promo_upgrade": _clean_money(row.get("promo_upgrade")),
            "promo_aal": _clean_money(row.get("promo_aal")),
            "min_plan_port": _clean_plan(row.get("min_plan_port")),
            "min_plan_non_port": _clean_plan(row.get("min_plan_non_port")),
            "min_plan_upgrade": _clean_plan(row.get("min_plan_upgrade")),
            "min_plan_aal": _clean_plan(row.get("min_plan_aal")),
            "boost_protect_fee": _clean_money(row.get("boost_protect_fee")),
            "notes": str(row.get("notes", "")) if pd.notna(row.get("notes")) else None,
        })
    return rows
