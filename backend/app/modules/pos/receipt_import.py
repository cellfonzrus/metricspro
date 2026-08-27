"""POS receipt import — photograph a receipt from a PRIMARY POS, OCR it, and record it as a
first-class sale in MetricsPro (the SECONDARY POS) in the tenant's own receipt series.

Three layers, split so the risky/expensive part (the vision model) is isolated and the mapping
logic is a PURE function that can be unit-tested without a network or a DB:

  • ocr_receipt(raw, ext)      — Claude vision → raw JSON (mirrors closing._ocr_deposit_amount:
                                 capped timeout/retries, graceful no-op when ANTHROPIC_API_KEY unset).
  • normalize_receipt(raw)     — PURE: coerce the model's loose JSON into clean, typed fields
                                 (numbers without $/commas, phone → digits, primary IMEI/device,
                                 total fallback). Tested in backend/harness_pos_receipt_parse.py.
  • import_receipt(client, …)  — match/create the customer, create the pos.sales header
                                 (source='receipt_import') with the line detail in receipt JSONB,
                                 write the pos.receipt_imports audit row, copy the note to the
                                 customer. Returns the created ids + the parsed preview.

Search (by IMEI / phone / customer) is served from pos.receipt_imports' denormalized, indexed
columns; see the router's /pos/receipt-imports endpoints.
"""
from __future__ import annotations

import base64
import json as _json
import re
from typing import Any

# NOTE: `settings` (app.core.config) is imported LAZILY inside ocr_receipt so this module — and its
# PURE normalize_receipt() — can be imported without the FastAPI/pydantic stack (keeps the parser
# unit-testable in isolation; see backend/harness_pos_receipt_parse.py).


# ── Layer 1: OCR (Claude vision) ─────────────────────────────────────────────────────────────────
_OCR_PROMPT = (
    "This is a photo of a retail/cell-phone store sales receipt. Extract the sale into COMPACT JSON "
    "with EXACTLY these keys (use null when a field is not present, never guess):\n"
    '{"customer_name": <string|null>, "phone": <string|null>, "email": <string|null>, '
    '"items": [{"description": <string>, "imei": <string|null>, "qty": <number>, "unit_price": <number>}], '
    '"subtotal": <number|null>, "tax": <number|null>, "total": <number|null>, '
    '"sale_date": "<YYYY-MM-DD|null>", "payment_method": <string|null>}\n'
    "IMEI/serial numbers are 14-15 digit device identifiers — include them per line when shown. "
    "Return ONLY the JSON, no prose, no code fences."
)


def ocr_receipt(raw: bytes, ext: str) -> tuple[dict, dict]:
    """Return (normalized_fields, raw_model_json). Graceful no-op ({}, {skipped}) when the vision
    key is unset — the caller can still record the receipt for MANUAL entry."""
    from app.core.config import settings  # lazy — keeps normalize_receipt importable without config
    if not settings.ANTHROPIC_API_KEY or not raw:
        return {}, {"skipped": "ANTHROPIC_API_KEY not set — enter the receipt manually"}
    try:
        from anthropic import Anthropic
        from app.modules.closing.ai_limits import CLOSING_OCR_TIMEOUT_S, CLOSING_OCR_MAX_RETRIES

        cli = Anthropic(api_key=settings.ANTHROPIC_API_KEY,
                        timeout=CLOSING_OCR_TIMEOUT_S, max_retries=CLOSING_OCR_MAX_RETRIES)
        media = "image/png" if str(ext).lower() == "png" else "image/jpeg"
        b64 = base64.b64encode(raw).decode("ascii")
        msg = cli.messages.create(
            model=settings.ACCOUNT_ENGINE_MODEL, max_tokens=1200,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media, "data": b64}},
                {"type": "text", "text": _OCR_PROMPT}]}])
        text = "".join(getattr(b, "text", "") for b in msg.content) if msg.content else ""
        text = text.strip()
        if text.startswith("```"):
            text = text.strip("`").split("\n", 1)[-1]
        raw_json = _json.loads(text[text.find("{"): text.rfind("}") + 1])
        return normalize_receipt(raw_json), raw_json
    except Exception as e:  # never surface a stack trace / provider string to the client
        return {}, {"error": str(e)[:300]}


# ── Layer 2: normalize (PURE) ────────────────────────────────────────────────────────────────────
def _money(v: Any) -> float | None:
    """'$1,299.00' / '1299' / 1299.0 → 1299.0 ; None/'' /unparseable → None."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return round(float(v), 2)
    s = re.sub(r"[^0-9.\-]", "", str(v))
    if s in ("", "-", ".", "-."):
        return None
    try:
        return round(float(s), 2)
    except ValueError:
        return None


def _digits(v: Any) -> str | None:
    if v is None:
        return None
    d = re.sub(r"\D", "", str(v))
    return d or None


def _int(v: Any, default: int = 1) -> int:
    try:
        n = int(float(str(v)))
        return n if n > 0 else default
    except (ValueError, TypeError):
        return default


def _clean_imei(v: Any) -> str | None:
    """Keep a 14-15 digit device id; reject anything else (a stray SKU/price is not an IMEI)."""
    d = _digits(v)
    return d if d and 14 <= len(d) <= 16 else None


def normalize_receipt(raw: dict | None) -> dict:
    """Coerce the vision model's loose JSON into clean, typed fields. Pure & deterministic."""
    raw = raw or {}
    items_out: list[dict] = []
    imeis: list[str] = []
    for it in (raw.get("items") or []):
        if not isinstance(it, dict):
            continue
        desc = (str(it.get("description") or "").strip()) or None
        imei = _clean_imei(it.get("imei"))
        qty = _int(it.get("qty"), 1)
        unit = _money(it.get("unit_price")) or 0.0
        if imei:
            imeis.append(imei)
        if desc or imei or unit:
            items_out.append({
                "description": desc, "imei": imei, "qty": qty,
                "unit_price": unit, "extended": round(unit * qty, 2),
            })

    subtotal = _money(raw.get("subtotal"))
    tax = _money(raw.get("tax"))
    total = _money(raw.get("total"))
    if total is None:  # derive when the receipt total wasn't read
        line_sum = round(sum(i["extended"] for i in items_out), 2) if items_out else None
        if line_sum is not None:
            total = round(line_sum + (tax or 0.0), 2)
        elif subtotal is not None:
            total = round(subtotal + (tax or 0.0), 2)

    name = (str(raw.get("customer_name") or "").strip()) or None
    date = str(raw.get("sale_date") or "").strip() or None
    if date and not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
        date = None  # only accept an ISO date; a bad parse must not corrupt the record

    primary = items_out[0] if items_out else {}
    return {
        "customer_name": name,
        "phone": _digits(raw.get("phone")),
        "email": (str(raw.get("email") or "").strip() or None),
        "items": items_out,
        "subtotal": subtotal,
        "tax": tax,
        "total": total,
        "sale_date": date,
        "payment_method": (str(raw.get("payment_method") or "").strip() or None),
        # denormalized search keys
        "imei": imeis[0] if imeis else None,
        "imeis": imeis,
        "device_name": primary.get("description"),
    }


# ── Layer 3: import (customer match/create + sale + audit row) ────────────────────────────────────
def _split_name(full: str | None) -> tuple[str, str]:
    parts = (full or "").strip().split()
    if not parts:
        return ("", "")
    if len(parts) == 1:
        return (parts[0], "")
    return (parts[0], " ".join(parts[1:]))


def _match_or_create_customer(client, org_id: str, parsed: dict, note: str | None) -> str | None:
    """Find a customer by phone (strongest key), else by exact name; create one when neither hits.
    Appends `note` to the customer's notes. Returns customer_id or None (never raises fatally)."""
    phone = parsed.get("phone")
    name = parsed.get("customer_name")
    tbl = client.schema("pos").table("customers")
    try:
        found = None
        if phone:
            rows = (tbl.select("id,notes").eq("org_id", org_id).eq("phone_primary", phone)
                    .limit(1).execute().data) or []
            found = rows[0] if rows else None
        if not found and name:
            rows = (tbl.select("id,notes").eq("org_id", org_id).ilike("last_name", _split_name(name)[1] or name)
                    .limit(1).execute().data) or []
            found = rows[0] if rows else None
        if found:
            if note:
                merged = ((found.get("notes") or "") + f"\n[receipt import] {note}").strip()
                tbl.update({"notes": merged}).eq("id", found["id"]).execute()
            return found["id"]
        if not (phone or name):
            return None
        first, last = _split_name(name)
        ins = {"org_id": org_id, "first_name": first or None, "last_name": last or None,
               "phone_primary": phone, "email": parsed.get("email"),
               "notes": (f"[receipt import] {note}" if note else None)}
        r = tbl.insert({k: v for k, v in ins.items() if v is not None} | {"org_id": org_id}).execute()
        return (r.data or [{}])[0].get("id")
    except Exception:
        return None  # a customer-link failure must not sink the whole import


# ── Encryption at rest (import ledger) ─────────────────────────────────────────────────────────────
# The receipt PII lives in pos.receipt_imports (denormalized columns + the parsed/raw_ocr blobs). We
# store those ENCRYPTED (app.core.crypto, 'enc:v1:' envelope) so a raw DB export is useless, and keep
# search working via keyed-HMAC BLIND-INDEX columns (phone_bidx/imei_bidx exact, search_bidx word).
# crypto is imported LAZILY so this module (and the pure normalize_receipt) stays importable without
# the app config/pydantic stack — see backend/harness_pos_receipt_parse.py.
_ENCRYPTED_IMPORT_COLUMNS = ("imei", "phone", "customer_name", "device_name", "notes")


def _encrypt_import_row(imp: dict) -> dict:
    """Encrypt the PII columns + parsed/raw_ocr blobs and add the blind-index columns. No-op columns
    (blank) are left as-is; with no key configured crypto passes through (graceful)."""
    from app.core import crypto
    out = crypto.encrypt_map(imp, _ENCRYPTED_IMPORT_COLUMNS)
    out["parsed"] = crypto.encrypt_json(imp.get("parsed"))
    out["raw_ocr"] = crypto.encrypt_json(imp.get("raw_ocr"))
    out["phone_bidx"] = crypto.blind_index(imp.get("phone"), mode="digits")
    out["imei_bidx"] = crypto.blind_index(imp.get("imei"), mode="digits")
    out["search_bidx"] = crypto.blind_index_words(imp.get("customer_name"), imp.get("device_name"))
    return out


def decrypt_receipt_row(row: dict | None) -> dict | None:
    """Reverse _encrypt_import_row for an authorized reader: decrypt the PII columns + parsed/raw_ocr,
    and drop the *_bidx tokens (never returned to the client). A row written before encryption (plain
    values, no 'enc:v1:' prefix) passes through unchanged. Value that can't be decrypted → None."""
    if not row:
        return row
    from app.core import crypto
    out = dict(row)
    for col in _ENCRYPTED_IMPORT_COLUMNS:
        if col in out:
            out[col] = crypto.decrypt(out[col])
    if "parsed" in out:
        out["parsed"] = crypto.decrypt_json(out["parsed"])
    if "raw_ocr" in out:
        out["raw_ocr"] = crypto.decrypt_json(out["raw_ocr"])
    for k in ("phone_bidx", "imei_bidx", "search_bidx"):
        out.pop(k, None)
    return out


def import_receipt(client, *, org_id: str, store_code: str | None, uploaded_by: str | None,
                   parsed: dict, raw_ocr: dict, notes: str | None,
                   image_path: str | None = None) -> dict:
    """Create the customer link, the pos.sales header (source='receipt_import') with line detail in
    the receipt JSONB, and the pos.receipt_imports audit row (PII encrypted at rest). Returns
    {import_id, sale_id, customer_id, transaction_id}."""
    customer_id = _match_or_create_customer(client, org_id, parsed, notes)

    total = parsed.get("total") or 0.0
    subtotal = parsed.get("subtotal")
    if subtotal is None:
        subtotal = round(total - (parsed.get("tax") or 0.0), 2)

    sale_row = {
        "org_id": org_id, "store_code": store_code, "customer_id": customer_id,
        "employee_id": uploaded_by, "receipt_type": "sale", "status": "completed",
        "source": "receipt_import",
        "subtotal": subtotal, "tax_total": parsed.get("tax") or 0.0, "total": total, "balance": 0,
        "notes": notes,
        "receipt": {"source": "receipt_import", "sale_date": parsed.get("sale_date"),
                    "payment_method": parsed.get("payment_method"), "items": parsed.get("items"),
                    "imeis": parsed.get("imeis")},
    }
    sr = client.schema("pos").table("sales").insert(sale_row).execute()
    sale = (sr.data or [{}])[0]
    sale_id = sale.get("id")
    transaction_id = sale.get("transaction_id")

    imp = {
        "org_id": org_id, "store_code": store_code, "sale_id": sale_id, "customer_id": customer_id,
        "status": "imported", "image_path": image_path, "raw_ocr": raw_ocr, "parsed": parsed,
        "notes": notes, "imei": parsed.get("imei"), "phone": parsed.get("phone"),
        "customer_name": parsed.get("customer_name"), "device_name": parsed.get("device_name"),
        "total": total, "sale_date": parsed.get("sale_date"), "uploaded_by": uploaded_by,
    }
    ir = client.schema("pos").table("receipt_imports").insert(_encrypt_import_row(imp)).execute()
    import_id = (ir.data or [{}])[0].get("id")

    return {"import_id": import_id, "sale_id": sale_id, "customer_id": customer_id,
            "transaction_id": transaction_id}
