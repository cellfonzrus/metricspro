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
# lookup fail — read as "not found" — so every rebuilt receipt CREATED a customer (live, 2026-09-24). `merged_into`
# (mig 1017) is optional the same way. Each optional column is probed on its own (core.column_tolerant, the one
# reading rule), cached per client × org for customer_master.SCHEMA_TTL_SECONDS (a migration applied by hand is
# seen without a restart).
_CUSTOMER_REQUIRED = ("id", "first_name", "last_name", "company_name", "address_1", "address_2", "city", "state", "zip",
                      "phone_primary", "phone_secondary", "email", "is_active", "created_at", "updated_at")
_CUSTOMER_OPTIONAL = ("notes", "merged_into")
_CUSTOMER_PRESENT: dict = {}
_IDENTITY_CFG: dict = {}


def _customer_present(client, org_id: str) -> frozenset:
    from app.core import column_tolerant as _ct
    from app.modules.pos import customer_master as _cm
    return _cm.ttl_cached(_CUSTOMER_PRESENT, (id(client), org_id),
                          lambda: _ct.present_columns(lambda: client.schema("pos").table("customers"),
                                                      lambda q: q.eq("org_id", org_id), _CUSTOMER_OPTIONAL))


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


def _incoming(parsed: dict) -> dict:
    """A sale's identity as the pure decision reads it: the bill-to name, every phone it carries (the receipt's
    phone + the invoice's line numbers), the sale date, the address and e-mail when the source has them."""
    phones = [parsed.get("phone")] + list(parsed.get("phones") or [])
    return {"name": (parsed.get("customer_name") or "").strip(), "phones": [p for p in phones if p],
            "date": parsed.get("sale_date"), "address": parsed.get("address"), "email": parsed.get("email")}


def _candidates(client, org_id: str, inc: dict) -> tuple[list, dict]:
    """EVERY customer the decision must weigh — org-scoped reads, nothing decided here: the customers whose LINES
    (`pos.activations.cell_number`) or phone fields carry one of the sale's numbers, the customers carrying the
    name (full name compared normalised) or an also-known-as name (mig 1017). A merged-away customer is followed
    to the customer it was merged into. Returns (candidate dicts for `customer_identity.decide`, rows by id)."""
    from app.modules.pos import customer_identity as _cid
    from app.modules.pos import customer_master as _cm
    tbl = lambda: client.schema("pos").table("customers")      # noqa: E731 — a builder is single-use
    cols = _customer_cols(client, org_id)
    phones = sorted({p for p in (_cid.norm_phone(x) for x in inc["phones"]) if p})
    nn = _cid.norm_name(inc["name"])
    line_dates: dict = {}                                       # customer id → {mdn: [dates]}
    rows: dict = {}
    if phones:
        for a in (client.schema("pos").table("activations").select("customer_id,cell_number,activation_date")
                  .eq("org_id", org_id).in_("cell_number", phones).limit(5000).execute().data) or []:
            if a.get("customer_id"):
                line_dates.setdefault(a["customer_id"], {}).setdefault(_cid.norm_phone(a.get("cell_number")), []).append(a.get("activation_date"))
        for col in ("phone_primary", "phone_secondary"):
            for r in (tbl().select(cols).eq("org_id", org_id).in_(col, phones).limit(200).execute().data) or []:
                rows[r["id"]] = r
    if nn:
        last = nn.split()[-1]
        for r in (tbl().select(cols).eq("org_id", org_id).ilike("last_name", f"%{last}%").order("id").limit(500).execute().data) or []:
            if _cid.norm_name(_cid.display_name(r)) == nn:
                rows[r["id"]] = r
    want = (set(line_dates) | set(_cm.alias_owners(client, org_id, inc["name"]))) - set(rows)
    for chunk in _cm.in_chunks(sorted(want)):
        for r in (tbl().select(cols).eq("org_id", org_id).in_("id", chunk).execute().data) or []:
            rows[r["id"]] = r
    for _hop in range(5):                                       # a merged-away customer → the survivor (its lines moved there)
        away = {r["id"]: r.get("merged_into") for r in rows.values() if r.get("merged_into")}
        if not away:
            break
        for rid, into in away.items():
            rows.pop(rid, None)
            line_dates.setdefault(into, {}).update(line_dates.pop(rid, {}))
            if into not in rows:
                got = (tbl().select(cols).eq("org_id", org_id).eq("id", into).limit(1).execute().data) or []
                if got:
                    rows[into] = got[0]
    aliases = _cm.aliases_of(client, org_id, list(rows))
    cands = []
    for rid, r in rows.items():
        ph = {m: max((str(d)[:10] for d in ds if d), default=None) for m, ds in line_dates.get(rid, {}).items() if m}
        for col in ("phone_primary", "phone_secondary"):
            m = _cid.norm_phone(r.get(col))
            if m and m in phones and not ph.get(m):             # a number typed on the record, no line yet: dated by the record
                ph[m] = str(r.get("updated_at") or r.get("created_at") or "")[:10] or None
        cands.append({"id": rid, "name": _cid.display_name(r), "aliases": [a.get("alias_name") for a in aliases.get(rid, [])],
                      "address": _cid.norm_address(r), "merged_into": None, "created_at": r.get("created_at"), "phones": ph})
    return cands, rows


def match_customer(client, org_id: str, parsed: dict) -> dict:
    """THE MATCHER's read half: the sale's identity → `customer_identity.decide` over `_candidates`. Returns
    {"decision": {...}, "row": the matched row | None, "incoming": {...}}. Never raises (a failed read → a
    decision of `none` that says so — it never reads as "not found → create")."""
    from app.modules.pos import customer_identity as _cid
    inc = _incoming(parsed)
    cfg = identity_config(client, org_id)
    if _cid.is_placeholder(inc["name"], cfg):                   # 'Walk In' names nobody — decided before any read
        return {"decision": _cid.decide(inc, [], config=cfg), "row": None, "incoming": inc}
    try:
        cands, rows = _candidates(client, org_id, inc)
    except Exception as e:
        return {"decision": {"action": "none", "customer_id": None, "rule": "unreadable", "alias_to_add": None, "reassigned": [],
                             "reason": f"the customer records could not be read ({str(e)[:120]}) — no customer attached, none created"},
                "row": None, "incoming": inc}
    d = _cid.decide(inc, cands, config=cfg)
    return {"decision": d, "row": rows.get(d.get("customer_id")) if d["action"] == "match" else None, "incoming": inc}


def find_customer(client, org_id: str, parsed: dict) -> dict | None:
    """THE customer match every import uses (the OCR path, the structured PDF, the sales rebuilt from the reports):
    `match_customer` → the matched row or None. Phone number and name first (a line the customer already carries,
    under the same name — or, within two years, under another name: combined), else the FULL name (first AND last,
    normalised; the last-name-only match that handed every 'Singh' the first Singh's receipt is gone), never a
    placeholder bill-to. Never raises."""
    return match_customer(client, org_id, parsed)["row"]


def match_or_create(client, org_id: str, parsed: dict, note: str | None = None) -> dict:
    """THE MATCHER's write half — the one path that creates a customer from a sale. match → fill the customer's
    EMPTY fields only (`customer_identity.fill_patch`; a filled field is never overwritten), record a different name
    as an also-known-as name (mig 1017 — when it is not applied the answer says so), append the note; create →
    one new customer (the first phone number as the primary phone); a placeholder → no customer.
    Returns {"customer_id", "decision", "created", "filled", "alias", "words"}; never raises fatally."""
    from app.modules.pos import customer_identity as _cid
    from app.modules.pos import customer_master as _cm
    out = {"customer_id": None, "decision": None, "created": False, "filled": {}, "alias": None, "words": []}
    m = match_customer(client, org_id, parsed)
    d, inc = m["decision"], m["incoming"]
    out["decision"] = d
    tbl = client.schema("pos").table("customers")
    has_notes = "notes" in _customer_present(client, org_id)
    try:
        if d["action"] == "match" and m["row"]:
            row = m["row"]
            patch = _cid.fill_patch(row, inc)
            if note and has_notes:
                patch["notes"] = ((row.get("notes") or "") + f"\n[receipt import] {note}").strip()
            if patch:
                tbl.update(patch).eq("id", row["id"]).eq("org_id", org_id).execute()
                out["filled"] = {k: v for k, v in patch.items() if k != "notes"}
            if d.get("alias_to_add"):
                a = _cm.add_alias(client, org_id, row["id"], d["alias_to_add"], "upload", inc.get("date"))
                out["alias"] = a if a else None
                if a is False:
                    out["words"].append(f"'{d['alias_to_add']}' was combined into this customer but not recorded as an also-known-as name — "
                                        + _cm.MIGRATION_WORDS)
            out["customer_id"] = row["id"]
            return out
        if d["action"] != "create" or _cid.is_placeholder(inc["name"], identity_config(client, org_id)):
            return out                                          # 'Walk In' / 'No Customer' never becomes a customer
        first, last = _cid.split_name(inc["name"])
        if not last:                                            # one word → the last name, so the matcher finds it next time
            first, last = "", first
        phones = [p for p in (_cid.norm_phone(x) for x in inc["phones"]) if p]
        phones = list(dict.fromkeys(phones))
        ins = {"org_id": org_id, "first_name": first or None, "last_name": last or None,
               "phone_primary": phones[0] if phones else None, "phone_secondary": phones[1] if len(phones) > 1 else None,
               "email": parsed.get("email"), "is_active": True,
               "notes": (f"[receipt import] {note}" if note and has_notes else None)}
        r = tbl.insert({k: v for k, v in ins.items() if v is not None} | {"org_id": org_id}).execute()
        out["customer_id"] = (r.data or [{}])[0].get("id")
        out["created"] = bool(out["customer_id"])
        return out
    except Exception as e:
        out["words"].append(f"the customer link failed ({str(e)[:120]}) — the sale is kept without a customer")
        return out                                              # a customer-link failure must not sink the whole import


def _match_or_create_customer(client, org_id: str, parsed: dict, note: str | None) -> str | None:
    """The customer id `match_or_create` settles on (or None) — the shape every older caller reads."""
    return match_or_create(client, org_id, parsed, note)["customer_id"]


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
                      image_path: str | None = None, employee_id: str | None = None, identity: dict | None = None) -> dict:
    """Store a parsed structured receipt: the customer (matched or created from the document's bill-to
    name), a summary sale carrying the document's own subtotal / tax / total, dated on the receipt's
    date, + the receipt_imports row carrying the editable `document`. Returns {import_id, sale_id,
    transaction_id, customer_id}. THE ONE way a structured document becomes a POS sale — the scanned-PDF
    path and the sales rebuilt from the landed reports both come through here."""
    der = (document or {}).get("derived") or _derived(document)
    total = der.get("total") or 0.0
    cm = match_or_create(client, org_id, _identity_of(der, identity), notes)
    customer_id = cm["customer_id"]

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
            "transaction_id": transaction_id, "customer_id": customer_id, "created": True, "customer_match": cm}


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


def _identity_of(der: dict, identity: dict | None) -> dict:
    """The sale identity the matcher reads: the document's bill-to name + phone, and what the caller knows beyond
    the document (the invoice's phone-line numbers and date from the sales reports: `identity`)."""
    return {"customer_name": der.get("customer_name"), "phone": der.get("phone"), "sale_date": der.get("sale_date"), **(identity or {})}


def upsert_structured(client, *, org_id: str, pos_source: str, document: dict, uploaded_by: str | None,
                      store_code: str | None, notes: str | None, employee_id: str | None = None,
                      identity: dict | None = None) -> dict:
    """Import a structured document ONCE per org × POS × invoice number (× provenance kind): the first
    run creates the sale + import row through import_structured; every later run REPLACES that row's
    document and its sale's money / store / rep / customer — never a second sale for the same invoice.
    Returns import_structured's shape plus {"replaced": bool}."""
    der = (document or {}).get("derived") or _derived(document)
    prov_kind = ((document or {}).get("provenance") or {}).get("kind")
    existing = find_structured(client, org_id, pos_source, der.get("invoice_no"), prov_kind)
    if not existing:
        return import_structured(client, org_id=org_id, pos_source=pos_source, document=document, uploaded_by=uploaded_by,
                                 store_code=store_code, notes=notes, employee_id=employee_id, identity=identity)
    cm = None
    customer_id = existing.get("customer_id")
    if not customer_id:                     # a re-run KEEPS the customer the sale already has (a merge may have moved it)
        cm = match_or_create(client, org_id, _identity_of(der, identity), None)
        customer_id = cm["customer_id"]
    update_structured_document(client, org_id, existing["id"], document, sale_id=existing.get("sale_id"),
                               pos_source=pos_source, store_code=store_code, employee_id=employee_id, customer_id=customer_id)
    return {"import_id": existing["id"], "sale_id": existing.get("sale_id"), "transaction_id": None,
            "customer_id": customer_id, "created": False, "replaced": True, "customer_match": cm}
