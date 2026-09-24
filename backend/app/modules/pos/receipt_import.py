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
        from app.modules.billing import ai_meter as _ai_meter
        _ai_meter.record("pos_receipt_ocr", settings.ACCOUNT_ENGINE_MODEL, msg)  # usage metering only (mig 972/973) — no auth implication
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
# The columns the matcher reads. `notes` is OPTIONAL: mig 725 never created it, and selecting it made every
# lookup fail — read as "not found" — so every rebuilt receipt CREATED a customer (live, 2026-09-24). Each
# optional column is probed on its own (core.column_tolerant, the one reading rule) and cached per client × org.
_CUSTOMER_REQUIRED = ("id", "first_name", "last_name", "address_1", "address_2", "city", "state", "zip", "phone_primary")
_CUSTOMER_OPTIONAL = ("notes",)
_CUSTOMER_PRESENT: dict = {}
_IDENTITY_CFG: dict = {}


def _customer_present(client, org_id: str) -> frozenset:
    from app.core import column_tolerant as _ct
    k = (id(client), org_id)
    if k not in _CUSTOMER_PRESENT:
        _CUSTOMER_PRESENT[k] = _ct.present_columns(lambda: client.schema("pos").table("customers"),
                                                   lambda q: q.eq("org_id", org_id), _CUSTOMER_OPTIONAL)
    return _CUSTOMER_PRESENT[k]


def _customer_cols(client, org_id: str) -> str:
    from app.core import column_tolerant as _ct
    return _ct.select_list(_CUSTOMER_REQUIRED, _CUSTOMER_OPTIONAL, _customer_present(client, org_id))


def identity_config(client, org_id: str) -> dict:
    """The org's customer-identity config (placeholder bill-to words) — pos.pos_settings key
    customer_identity.CONFIG_KEY through customer_identity.resolve_config; cached per client × org."""
    from app.modules.pos import customer_identity as _cid
    k = (id(client), org_id)
    if k not in _IDENTITY_CFG:
        try:
            rows = (client.schema("pos").table("pos_settings").select("value").eq("org_id", org_id)
                    .eq("key", _cid.CONFIG_KEY).is_("store_code", "null").limit(1).execute().data) or []
        except Exception:
            rows = []
        _IDENTITY_CFG[k] = _cid.resolve_config(rows[0].get("value") if rows else None)
    return _IDENTITY_CFG[k]


def find_customer(client, org_id: str, parsed: dict) -> dict | None:
    """THE customer match every receipt import uses: by phone (the strongest key), else by the FULL
    name — first AND last, case-insensitive. It used to match the last name alone, so the first
    customer named 'Singh' took every later Singh's receipt (found while rebuilding sales from the
    reports, 2026-09-21; fixed here for the OCR path too). A one-word name matches a customer whose
    last name is that word and whose first name is empty. Returns the row or None; never raises."""
    from app.modules.pos import customer_identity as _cid
    phone = parsed.get("phone")
    name = (parsed.get("customer_name") or "").strip()
    if name and _cid.is_placeholder(name, identity_config(client, org_id)):
        name = ""                                   # a placeholder bill-to ('Walk In') names nobody — never matched
    tbl = lambda: client.schema("pos").table("customers")      # noqa: E731 — a builder is single-use
    cols = _customer_cols(client, org_id)
    try:
        if phone:
            rows = (tbl().select(cols).eq("org_id", org_id).eq("phone_primary", phone)
                    .limit(1).execute().data) or []
            if rows:
                return rows[0]
        if name:
            first, last = _cid.split_name(name)
            if not last:                            # a one-word name is stored as the LAST name (see the insert)
                first, last = "", first
            rows = (tbl().select(cols).eq("org_id", org_id).ilike("last_name", last)
                    .order("id").limit(200).execute().data) or []
            rows = [r for r in rows if _cid.same_name(r, first, last)]
            return rows[0] if rows else None
    except Exception:
        return None
    return None


def _match_or_create_customer(client, org_id: str, parsed: dict, note: str | None) -> str | None:
    """Find a customer (find_customer: phone, else the full name); create one when neither hits.
    Appends `note` to the customer's notes. Returns customer_id or None (never raises fatally)."""
    from app.modules.pos import customer_identity as _cid
    phone = parsed.get("phone")
    name = parsed.get("customer_name")
    if name and _cid.is_placeholder(name, identity_config(client, org_id)):
        name = None                                 # 'Walk In' / 'No Customer' never becomes a customer
    tbl = client.schema("pos").table("customers")
    has_notes = "notes" in _customer_present(client, org_id)
    try:
        found = find_customer(client, org_id, {**parsed, "customer_name": name})
        if found:
            if note and has_notes:
                merged = ((found.get("notes") or "") + f"\n[receipt import] {note}").strip()
                tbl.update({"notes": merged}).eq("id", found["id"]).eq("org_id", org_id).execute()
            return found["id"]
        if not (phone or name):
            return None
        first, last = _cid.split_name(name)
        if not last:                                # one word → the last name, so find_customer finds it next time
            first, last = "", first
        ins = {"org_id": org_id, "first_name": first or None, "last_name": last or None,
               "phone_primary": phone, "email": parsed.get("email"),
               "notes": (f"[receipt import] {note}" if note and has_notes else None)}
        r = tbl.insert({k: v for k, v in ins.items() if v is not None} | {"org_id": org_id}).execute()
        return (r.data or [{}])[0].get("id")
    except Exception:
        return None  # a customer-link failure must not sink the whole import


# ── Encryption at rest (import ledger) ─────────────────────────────────────────────────────────────
# By owner decision, customer NAME / PHONE / EMAIL / ADDRESS stay PLAINTEXT system-wide (they must be
# searched/matched directly, and the same values live plaintext in pos.customers, so encrypting them
# only here would add complexity without real protection). What we DO encrypt at rest here: the IMEI
# (a device identifier), the free-text note, and the parsed/raw_ocr blobs (audit payloads that can
# carry email / card last-4 / other sensitive fragments). Search over the encrypted IMEI is served by
# a keyed-HMAC BLIND-INDEX column (imei_bidx); name/phone/device search stays plain ILIKE.
# crypto is imported LAZILY so this module (and the pure normalize_receipt) stays importable without
# the app config/pydantic stack — see backend/harness_pos_receipt_parse.py.
_ENCRYPTED_IMPORT_COLUMNS = ("imei", "notes")


def _encrypt_import_row(imp: dict) -> dict:
    """Encrypt the IMEI + note + parsed/raw_ocr blobs and add the IMEI blind index. Name/phone/device
    are left plaintext. No-op on blank columns; with no key configured crypto passes through."""
    from app.core import crypto
    out = crypto.encrypt_map(imp, _ENCRYPTED_IMPORT_COLUMNS)
    out["parsed"] = crypto.encrypt_json(imp.get("parsed"))
    out["raw_ocr"] = crypto.encrypt_json(imp.get("raw_ocr"))
    out["imei_bidx"] = crypto.blind_index(imp.get("imei"), mode="digits")
    return out


def decrypt_receipt_row(row: dict | None) -> dict | None:
    """Reverse _encrypt_import_row for an authorized reader: decrypt the IMEI + note + parsed/raw_ocr,
    and drop the imei_bidx token (never returned to the client). A row written before encryption (plain
    values, no 'enc:v1:' prefix) passes through unchanged. A value that can't be decrypted → None."""
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
        out.pop(k, None)  # phone_bidx / search_bidx are now unused (kept nullable in the table)
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


# ── Structured (per-POS) import — the editable + reprintable Document (migration 866) ──────────────
# The v2 path: a PDF from a KNOWN POS format is parsed (app/modules/pos/receipt_formats) into ONE
# editable `document`, stored on the receipt_imports row alongside the usual denormalized search
# columns (derived from the document, so an edit stays searchable). A summary pos.sales row keeps it
# in the sales ledger; the full line/section/footer detail lives in `document` for the reprint.
def _derived(document: dict) -> dict:
    from app.modules.pos.receipt_formats import base as _b
    return _b.compute_derived(document or {})


def _sale_money(document: dict, der: dict) -> dict:
    """The summary sale's money FROM THE DOCUMENT'S OWN TOTALS: subtotal / tax (the format's 'sales_tax'
    or any key carrying 'tax') / total (the derived grand total). A document with no subtotal row keeps
    the old rule (subtotal = total, tax 0) so an older parse changes nothing."""
    from app.modules.pos.receipt_formats.base import money as _m
    totals = {str(t.get("key") or ""): t.get("amount") for t in (document or {}).get("totals") or []}
    total = _m(der.get("total")) or 0.0
    sub = _m(totals.get("subtotal"))
    tax = 0.0
    for k, v in totals.items():
        if "tax" in k and _m(v) is not None:
            tax += _m(v)
    return {"subtotal": total if sub is None else sub, "tax_total": round(tax, 2), "total": total}


def _sale_row(document: dict, der: dict, *, org_id, store_code, employee_id, uploaded_by, notes, pos_source) -> dict:
    """The pos.sales summary row a structured document becomes — one shape for create and replace.
    The sale is dated on the receipt's own date (derived sale_date) when it carries one."""
    money = _sale_money(document, der)
    prov = (document or {}).get("provenance") or None
    row = {
        "org_id": org_id, "store_code": store_code, "employee_id": employee_id or uploaded_by,
        "receipt_type": "sale", "status": "completed", "source": "receipt_import",
        "subtotal": money["subtotal"], "tax_total": money["tax_total"], "total": money["total"], "balance": 0, "notes": notes,
        "receipt": {"source": "receipt_import", "pos_source": pos_source,
                    "invoice_no": der.get("invoice_no"), "imeis": der.get("imeis"),
                    "sale_date": der.get("sale_date"), "salesperson": der.get("salesperson"),
                    "payments": (document or {}).get("payments") or [],
                    **({"provenance": {k: v for k, v in prov.items() if k != "report"}} if prov else {})},
    }
    if der.get("sale_date"):
        row["created_at"] = f"{der['sale_date']}T12:00:00Z"
    return row


def import_structured(client, *, org_id: str, pos_source: str, document: dict,
                      uploaded_by: str | None, store_code: str | None, notes: str | None,
                      image_path: str | None = None, employee_id: str | None = None) -> dict:
    """Store a parsed structured receipt: the customer (matched or created from the document's bill-to
    name), a summary sale carrying the document's own subtotal / tax / total, dated on the receipt's
    date, + the receipt_imports row carrying the editable `document`. Returns {import_id, sale_id,
    transaction_id, customer_id}. THE ONE way a structured document becomes a POS sale — the scanned-PDF
    path and the sales rebuilt from the landed reports both come through here."""
    der = (document or {}).get("derived") or _derived(document)
    total = der.get("total") or 0.0
    customer_id = _match_or_create_customer(client, org_id, {"customer_name": der.get("customer_name"), "phone": der.get("phone")}, notes)

    sale_row = _sale_row(document, der, org_id=org_id, store_code=store_code, employee_id=employee_id,
                         uploaded_by=uploaded_by, notes=notes, pos_source=pos_source)
    if customer_id:
        sale_row["customer_id"] = customer_id
    sr = client.schema("pos").table("sales").insert(sale_row).execute()
    sale = (sr.data or [{}])[0]
    sale_id, transaction_id = sale.get("id"), sale.get("transaction_id")

    imp = {
        "org_id": org_id, "store_code": store_code, "sale_id": sale_id, "customer_id": customer_id, "status": "imported",
        "image_path": image_path, "notes": notes, "pos_source": pos_source, "document": document,
        "invoice_no": der.get("invoice_no"), "salesperson": der.get("salesperson"),
        "imei": der.get("imei"), "phone": der.get("phone"), "customer_name": der.get("customer_name"),
        "device_name": der.get("device_name"), "total": total, "sale_date": der.get("sale_date"),
        "uploaded_by": uploaded_by,
    }
    row = _encrypt_import_row({k: v for k, v in imp.items() if not (k == "customer_id" and v is None)})
    ir = client.schema("pos").table("receipt_imports").insert(row).execute()
    return {"import_id": (ir.data or [{}])[0].get("id"), "sale_id": sale_id,
            "transaction_id": transaction_id, "customer_id": customer_id, "created": True}


def _sync_sale(client, org_id: str, sale_id: str | None, document: dict, der: dict, **kw) -> None:
    """Bring the summary pos.sales row in step with its (edited or rebuilt) document — money, date,
    payments, and the store / rep / customer when the caller passes them. Never raises."""
    if not sale_id:
        return
    try:
        money = _sale_money(document, der)
        patch = {"subtotal": money["subtotal"], "tax_total": money["tax_total"], "total": money["total"]}
        prov = (document or {}).get("provenance") or None
        patch["receipt"] = {"source": "receipt_import", "pos_source": kw.get("pos_source"),
                            "invoice_no": der.get("invoice_no"), "imeis": der.get("imeis"), "sale_date": der.get("sale_date"),
                            "salesperson": der.get("salesperson"), "payments": (document or {}).get("payments") or [],
                            **({"provenance": {k: v for k, v in prov.items() if k != "report"}} if prov else {})}
        for k in ("store_code", "employee_id", "customer_id"):
            if kw.get(k):
                patch[k] = kw[k]
        if der.get("sale_date"):
            patch["created_at"] = f"{der['sale_date']}T12:00:00Z"
        client.schema("pos").table("sales").update(patch).eq("org_id", org_id).eq("id", sale_id).execute()
    except Exception:
        pass


def update_structured_document(client, org_id: str, import_id: str, document: dict, sale_id: str | None = None, **sale_kw) -> dict:
    """Save edits to a structured receipt. Recomputes the derived search/summary fields from the
    edited document (so a changed description/qty/price/tax stays searchable and the summary total
    tracks the edit), refreshes the encrypted imei + its blind index, brings the summary sale's money in
    step (it did not before — an edited tax left pos.sales as first written), and returns the saved doc."""
    from app.core import crypto
    d = dict(document or {})
    d["derived"] = _derived(d)
    der = d["derived"]
    imei = der.get("imei")
    patch = {
        "document": d, "invoice_no": der.get("invoice_no"), "salesperson": der.get("salesperson"),
        "customer_name": der.get("customer_name"), "device_name": der.get("device_name"),
        "total": der.get("total"), "sale_date": der.get("sale_date"),
        "imei": crypto.encrypt(imei) if imei else None,
        "imei_bidx": crypto.blind_index(imei, mode="digits") if imei else None,
    }
    for k in ("store_code", "customer_id"):
        if sale_kw.get(k):
            patch[k] = sale_kw[k]
    client.schema("pos").table("receipt_imports").update(patch).eq("org_id", org_id).eq("id", import_id).execute()
    if sale_id is None:
        try:
            rows = (client.schema("pos").table("receipt_imports").select("sale_id,pos_source").eq("org_id", org_id)
                    .eq("id", import_id).limit(1).execute().data) or []
            sale_id = rows[0].get("sale_id") if rows else None
            sale_kw.setdefault("pos_source", rows[0].get("pos_source") if rows else None)
        except Exception:
            sale_id = None
    _sync_sale(client, org_id, sale_id, d, der, **sale_kw)
    return d


def find_structured(client, org_id: str, pos_source: str, invoice_no: str, provenance_kind: str | None = None) -> dict | None:
    """The receipt_imports row already holding this org × POS × invoice number — and, when asked, the
    one whose document carries that provenance kind (a sale rebuilt from the reports never replaces a
    scanned receipt of the same invoice, nor the other way round). Org-scoped; None when absent."""
    if not invoice_no:
        return None
    try:
        rows = (client.schema("pos").table("receipt_imports").select("id,sale_id,customer_id,document,status")
                .eq("org_id", org_id).eq("pos_source", pos_source).eq("invoice_no", invoice_no)
                .limit(50).execute().data) or []
    except Exception:
        return None
    for r in rows:
        if r.get("status") == "voided":
            continue
        kind = ((r.get("document") or {}).get("provenance") or {}).get("kind")
        if provenance_kind is None or kind == provenance_kind:
            return r
    return None


def upsert_structured(client, *, org_id: str, pos_source: str, document: dict, uploaded_by: str | None,
                      store_code: str | None, notes: str | None, employee_id: str | None = None) -> dict:
    """Import a structured document ONCE per org × POS × invoice number (× provenance kind): the first
    run creates the sale + import row through import_structured; every later run REPLACES that row's
    document and its sale's money / store / rep / customer — never a second sale for the same invoice.
    Returns import_structured's shape plus {"replaced": bool}."""
    der = (document or {}).get("derived") or _derived(document)
    prov_kind = ((document or {}).get("provenance") or {}).get("kind")
    existing = find_structured(client, org_id, pos_source, der.get("invoice_no"), prov_kind)
    if not existing:
        return import_structured(client, org_id=org_id, pos_source=pos_source, document=document, uploaded_by=uploaded_by,
                                 store_code=store_code, notes=notes, employee_id=employee_id)
    customer_id = existing.get("customer_id") or _match_or_create_customer(
        client, org_id, {"customer_name": der.get("customer_name"), "phone": der.get("phone")}, None)
    update_structured_document(client, org_id, existing["id"], document, sale_id=existing.get("sale_id"),
                               pos_source=pos_source, store_code=store_code, employee_id=employee_id, customer_id=customer_id)
    return {"import_id": existing["id"], "sale_id": existing.get("sale_id"), "transaction_id": None,
            "customer_id": customer_id, "created": False, "replaced": True}
