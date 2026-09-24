"""INVENTORY INTEGRITY — the endpoints, the readers, THE landing guard and THE one status writer (index §11b).

Owner 2026-09-24: inventory "crashed against the sales report by invoice and the commission received reports",
a FLAG per suspect unit with the customer it was sold to (from the commission report), a ONE-CLICK assign that
moves it out of inventory, the flag kept until MANAGEMENT verifies; a duplicate-IMEI pop-up on receive and a
duplicate / already-sold report on import; a manual adjust in / out. Platform-wide — nothing here names a
tenant, carrier, POS or distributor (RULE TWO); the commission feed is the lineage registry's
COMMISSION_PER_DEVICE_FEED, dereferenced; the device key is `device_cost_recon.device_key`, injected.

A SEPARATE ROUTER MODULE on purpose: `pos/router.py` keeps only two one-line delegations (the receive and the
edit of a serial unit), so concurrent work on the POS router merges cleanly. Registered in app/main.py beside
the POS router, under the same router-wide member gate (`_require_pos_access`).

  GET  /pos/inventory/integrity                 the engine's findings merged with the persisted flags (+ counts, cost)
  POST /pos/inventory/integrity/scan            persist / refresh flags, idempotently (never re-opens a closed flag
                                                unless its evidence changed)
  POST /pos/inventory/flags/{id}/assign         THE ONE CLICK: customer found-or-created through the ONE matcher
                                                (receipt_import.match_or_create), the unit OUT as sold
                                                on the commission's sold-on date, a ledger row, flag → assigned
  POST /pos/inventory/flags/{id}/verify         manager (pos_inventory_verify): assigned → verified; approve:false
                                                REJECTS — the unit back IN through the ledger, flag → open
  POST /pos/inventory/flags/{id}/dismiss        manager, a note required: open → dismissed
  POST /pos/inventory/adjust                    manual in / out with a reason (pos_inventory_adjust)
  (pos/router.py) POST  /pos/inventory/serial   → receive_unit: THE landing guard, 409 unless confirm_duplicate
  (pos/router.py) PATCH /pos/inventory/serial/{id} → edit_unit: status through the ledger, serial/IMEI through
                                                the guard, integrity fields permission-gated

Money: none. No P&L, GP, payout or tax path reads a unit's status; nothing here writes a sale. Every query is
org-scoped (harness_org_scope_guard.py scans this file).
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException

from app.modules.commcalc import data_lineage_registry as _dlr
from app.modules.commcalc import device_cost_recon as _dcr
from app.modules.commcalc import inventory_sold_recon as _isr
from app.modules.pos import inventory_integrity as _ii
from app.modules.pos import router as _pr

ORG_ID = _pr.ORG_ID
MIGRATION = "1018_pos_inventory_integrity.sql"
ADJUST_PERM = "pos_inventory_adjust"        # existing key (transfers ship/cancel): assign, adjust, integrity edits
VERIFY_PERM = "pos_inventory_verify"        # NEW manager key, same mechanism (_pos_grant: explicit grant, else a
                                            # full-scope admin): verify / reject / dismiss
INTEGRITY_FIELDS = ("status", "serial_number", "imei", "store_code", "cost")   # an edit of these is an adjustment
READ_CAP = 200000

router = APIRouter(prefix="/pos", tags=["pos"], dependencies=[Depends(_pr._require_pos_access)])


def _now():
    return datetime.now(timezone.utc).isoformat()


def _need_1018(e=None):
    return HTTPException(503, f"inventory integrity is not set up on this database yet — apply migration {MIGRATION}"
                              + (f" ({str(e)[:160]})" if e else ""))


def _refused(e):
    return HTTPException(409, str(e))


def _actor(authorization, org_id):
    """Who is acting — the caller's employee_id, else their login id (never a body field)."""
    emp = _pr._caller_employee(authorization, org_id)
    if emp:
        return emp
    try:
        from app.modules.core.router import _uid_from_token
        return _uid_from_token(authorization) or None
    except Exception:
        return None


def _span_check(authorization, org_id, unit):
    """A store-scoped caller may act only on a unit of a store inside their span."""
    if not _pr._span_filter([unit or {}], _pr._caller_store_keyset(authorization, org_id)):
        raise HTTPException(403, "this unit's store is outside your store scope")


# ══ READERS — every one org-scoped ═══════════════════════════════════════════════════════════════════════
def _all_units(client, org_id):
    """(units, ok) — every serialised unit of the org, any status, the mig-725 columns (never '*')."""
    cols, out, start = ",".join(_ii.UNIT_COLUMNS), [], 0
    try:
        while start < READ_CAP:
            chunk = (client.schema("pos").table("inventory_serial").select(cols).eq("org_id", org_id)
                     .order("id").range(start, start + 999).execute().data) or []
            out.extend(chunk)
            if len(chunk) < 1000:
                break
            start += 1000
    except Exception as e:
        print(f"WARN inventory integrity: unit read failed: {e}")
        return out, False
    return out, True


def _units_by_ids(client, org_id, ids):
    ids = sorted({i for i in ids if i})
    out = {}
    for i in range(0, len(ids), 100):
        for u in (client.schema("pos").table("inventory_serial").select(",".join(_ii.UNIT_COLUMNS))
                  .eq("org_id", org_id).in_("id", ids[i:i + 100]).execute().data) or []:
            out[u["id"]] = u
    return out


def _variants(keys, raws=()):
    """The spellings a stored value of these device keys may carry (the key, lower case, the raw input)."""
    out = set()
    for k in keys:
        out.update((k, k.lower()))
    out.update(str(r).strip() for r in raws if str(r or "").strip())
    return sorted(out)


def _units_by_values(client, org_id, values):
    cols, seen = ",".join(_ii.UNIT_COLUMNS), {}
    for field in ("serial_number", "imei"):
        for u in (client.schema("pos").table("inventory_serial").select(cols).eq("org_id", org_id)
                  .in_(field, values).execute().data) or []:
            seen[u["id"]] = u
    return list(seen.values())


def _sales_cols(client, org_id):
    """The sales-line columns, the optional ones probed one by one (core.column_tolerant — the one reading rule)."""
    from app.core import column_tolerant as _ct
    present = _ct.present_columns(lambda: client.schema("commcalc").table(_dlr.MONTHLY_SALES),
                                  lambda q: q.eq("org_id", org_id), ("customer", "mdn"))
    return _ct.select_list(("serial_1", "quantity", "trans_id", "trans_date"), ("customer", "mdn"), present)


def read_sources(client, org_id):
    """Everything the engine reads, org-scoped, through the existing paged reader (`commcalc._dcr_paged` — a
    failed or capped read degrades and is STATED in `basis`, never a 500):
      units       pos.inventory_serial (the POS's own units)
      sales       commcalc.<registry MONTHLY_SALES> — the sale LINES (serial_1, quantity, invoice, date, customer)
      invoices    commcalc.raw_sales_invoice — the invoice header's customer (sales by invoice, mig 1012)
      commission  commcalc.<registry COMMISSION_PER_DEVICE_FEED> — per IMEI, signed earned, customer, invoice"""
    from app.modules.commcalc import router as _cr

    def org(q):
        return q.eq("org_id", org_id)
    units, u_ok = _all_units(client, org_id)
    sales, s_ok, s_cut = _cr._dcr_paged(client, _dlr.MONTHLY_SALES, _sales_cols(client, org_id), org, READ_CAP, "integrity sales")
    com, c_ok, c_cut = _cr._inventory_commission_rows(client, org_id)     # THE commission reader (Inventory vs Sold's too)
    inv, i_ok, _i_cut = _cr._dcr_paged(client, "raw_sales_invoice", "trans_id,customer", org, READ_CAP, "integrity invoices")
    inv_cust = {str(r.get("trans_id") or "").strip(): str(r.get("customer") or "").strip()
                for r in inv if str(r.get("trans_id") or "").strip() and str(r.get("customer") or "").strip()}
    basis = {
        "units": {"table": "pos.inventory_serial", "read_ok": u_ok, "rows": len(units)},
        "sales": {"table": f"commcalc.{_dlr.MONTHLY_SALES}", "read_ok": s_ok, "truncated": s_cut, "rows": len(sales),
                  "pairs_on": "serial_1 (device key); net quantity — a refund nets to zero"},
        "invoices": {"table": "commcalc.raw_sales_invoice", "read_ok": i_ok, "rows": len(inv),
                     "used_for": "the invoice's customer when the sale line carries none"},
        "commission": {"table": f"commcalc.{_dlr.COMMISSION_PER_DEVICE_FEED}", "read_ok": c_ok, "truncated": c_cut,
                       "rows": len(com), "pairs_on": f"{_dlr.COMMISSION_PER_DEVICE_COLUMNS['device']} (device key); "
                                                     "net earned after chargebacks — kept when > 0"},
        "complete": u_ok and s_ok and c_ok and not s_cut and not c_cut,
    }
    return {"units": units, "sales": sales, "commission": com, "invoice_customers": inv_cust, "basis": basis}


def run_engine(client, org_id):
    """THE ONE ENGINE over the org's sources: `inventory_sold_recon.integrity`."""
    src = read_sources(client, org_id)
    rep = _isr.integrity(src["units"], src["sales"], src["commission"], _dcr.device_key, _ii.is_live,
                         invoice_customers=src["invoice_customers"])
    return rep, src


def _flags(client, org_id):
    """(flags, ok) — the org's persisted flags. `select("*")` (the any-subset rule); a missing table → ok False."""
    out, start = [], 0
    try:
        while True:
            chunk = (client.schema("pos").table("inventory_flags").select("*").eq("org_id", org_id)
                     .order("id").range(start, start + 999).execute().data) or []
            out.extend(chunk)
            if len(chunk) < 1000:
                break
            start += 1000
    except Exception as e:
        print(f"WARN inventory integrity: flag read failed: {e}")
        return out, False
    return out, True


def _flag(client, org_id, flag_id):
    try:
        rows = (client.schema("pos").table("inventory_flags").select("*").eq("org_id", org_id).eq("id", flag_id)
                .limit(1).execute().data) or []
    except Exception as e:
        raise _need_1018(e)
    if not rows:
        raise HTTPException(404, "flag not found")
    return rows[0]


def _unit(client, org_id, unit_id):
    rows = (client.schema("pos").table("inventory_serial").select(",".join(_ii.UNIT_COLUMNS))
            .eq("org_id", org_id).eq("id", unit_id).limit(1).execute().data) or []
    if not rows:
        raise HTTPException(404, "unit not found")
    return rows[0]


# ══ THE LANDING GUARD — every path that writes a serialised unit asks it first ═════════════════════════════
def guard_landing(client, org_id, candidate, exclude_unit_id=None):
    """Before ONE unit lands (receive) or changes its serial / IMEI (edit): the engine's `receive_check` over
    the org's units that answer to the same device key, and the sale / commission evidence for that key —
    targeted reads (only this device), org-scoped. Returns the check (blocking, reasons, existing, …)."""
    keys = _isr.unit_keys(candidate, _dcr.device_key)
    if not keys:
        return _isr.receive_check(candidate, [], _dcr.device_key, _ii.is_live)
    vals = _variants(keys, (candidate.get("serial_number"), candidate.get("imei")))
    units = [u for u in _units_by_values(client, org_id, vals) if u.get("id") != exclude_unit_id]
    sale = com = None
    try:
        rows = (client.schema("commcalc").table(_dlr.MONTHLY_SALES).select(_sales_cols(client, org_id))
                .eq("org_id", org_id).in_("serial_1", vals).execute().data) or []
        idx = _isr.sales_detail_index(rows, _dcr.device_key)
        sale = next((idx[k] for k in keys if k in idx), None)
    except Exception as e:
        print(f"WARN inventory guard: sales read failed: {e}")
    try:
        rows = (client.schema("commcalc").table(_dlr.COMMISSION_PER_DEVICE_FEED).select(_dlr.commission_per_device_select())
                .eq("org_id", org_id).in_(_dlr.COMMISSION_PER_DEVICE_COLUMNS["device"], vals).execute().data) or []
        idx = _isr.commission_index(rows, _dcr.device_key)
        com = next((idx[k] for k in keys if k in idx), None)
    except Exception as e:
        print(f"WARN inventory guard: commission read failed: {e}")
    return _isr.receive_check(candidate, units, _dcr.device_key, _ii.is_live, sale, com)


def guard_import(client, org_id, incoming):
    """Before a BATCH lands (the onboarding bring-over, any bulk path): the same check, per unit, against the
    org's units and the batch so far — `inventory_integrity.import_report` over one full read of the sources.
    Returns {land, skip, report}; the report is returned to the caller, never swallowed."""
    src = read_sources(client, org_id)
    sales_idx = _isr.sales_detail_index(src["sales"], _dcr.device_key, src["invoice_customers"])
    comm_idx = _isr.commission_index(src["commission"], _dcr.device_key)
    out = _ii.import_report(incoming, src["units"], _dcr.device_key, sales_idx, comm_idx)
    out["basis"] = src["basis"]
    return out


def _public_check(chk):
    """The 409 / report payload: what already exists, its status, and the sale / commission evidence."""
    com, sale = chk.get("commission") or {}, chk.get("sale") or {}
    return {
        "reasons": chk.get("reasons") or [], "keys": chk.get("keys") or [], "already_sold": chk.get("already_sold"),
        "sold_on": chk.get("sold_on"), "customer": chk.get("customer"),
        "existing": [{k: u.get(k) for k in ("id", "serial_number", "imei", "store_code", "status", "date_received", "sold_at")}
                     for u in chk.get("existing") or []],
        "sale": ({"net": sale.get("net"), "lines": sale.get("lines"), "last_sold_on": sale.get("last_sold_on")} if sale else None),
        "commission": ({k: com.get(k) for k in ("net_earned", "charged_back", "kept", "invoices", "sold_on", "customer_name")}
                       if com else None),
    }


def _duplicate_message(chk):
    parts = []
    ex = chk.get("existing") or []
    if ex:
        parts.append("this IMEI / serial is already in the inventory: " +
                     "; ".join(f"{u.get('serial_number')} — {u.get('status')} at {u.get('store_code') or 'no store'}" for u in ex[:5]))
    if chk.get("already_sold"):
        who = (chk.get("customer") or {}).get("name")
        parts.append("it was already sold" + (f" on {chk.get('sold_on')}" if chk.get("sold_on") else "")
                     + (f" to {who}" if who else ""))
    return "Duplicate IMEI — " + "; ".join(parts) + ". Receive anyway only if this is a different physical unit."


# ══ THE ONE STATUS WRITER — every status change the application makes, with its ledger row ═══════════════
def apply_status_change(client, org_id, unit, reason, by, direction=None, to_status=None, flag_id=None, note=None,
                        customer_id=None, sold_at=None, imei_key=None):
    """Move ONE unit's status and write its adjustment-ledger row. The only Python writer of
    `inventory_serial.status` (the lock fails the build on another). The update is CONDITIONAL on the status
    the plan read (a concurrent sale / transfer wins, and the caller is told); if the ledger row cannot be
    written the unit is put back exactly as it was — a status change without its ledger row cannot persist.
    Returns {"unit", "adjustment"}. Raises HTTPException (409 refused / changed, 503 no ledger)."""
    try:
        frm, dest, direction = _ii.plan_adjustment(unit, direction, reason, to_status)
    except _ii.IntegrityError as e:
        raise _refused(e)
    patch = {"status": dest, "updated_at": _now()}
    if dest == "sold":
        patch["sold_at"] = sold_at or _now()
    elif _ii.is_live(dest):
        patch["sold_at"] = None
        patch["sold_in_sale_id"] = None
    r = (client.schema("pos").table("inventory_serial").update(patch)
         .eq("org_id", org_id).eq("id", unit["id"]).eq("status", frm).execute())
    if not r.data:
        raise HTTPException(409, "the unit changed while you were working (it is no longer "
                                 f"'{frm}') — reload and try again")
    key = imei_key or next(iter(_isr.unit_keys(unit, _dcr.device_key)), None)
    row = _ii.ledger_row(org_id, unit, direction, reason, frm, dest, by, key, flag_id, note, customer_id)
    try:
        led = (client.schema("pos").table("inventory_adjustments").insert(row).execute().data) or [row]
    except Exception as e:
        back = {"status": frm, "sold_at": unit.get("sold_at"), "sold_in_sale_id": unit.get("sold_in_sale_id"),
                "updated_at": _now()}
        (client.schema("pos").table("inventory_serial").update(back)
         .eq("org_id", org_id).eq("id", unit["id"]).execute())
        raise _need_1018(e)
    return {"unit": r.data[0], "adjustment": led[0]}


def record_landing(client, org_id, units, reason, by, note=None):
    """The ledger 'in' rows for units that were just CREATED (from nothing to their status). Best-effort by
    design: a landing is not a status change, and receiving must keep working on a database where migration
    1018 is not applied yet — the failure is RETURNED (`error`), never hidden."""
    rows = [_ii.ledger_row(org_id, u, _ii.IN, reason, None, u.get("status") or _ii.LIVE_STATUSES[0], by,
                           next(iter(_isr.unit_keys(u, _dcr.device_key)), None), None, note)
            for u in units or [] if u.get("id")]
    try:
        for i in range(0, len(rows), 200):
            client.schema("pos").table("inventory_adjustments").insert(rows[i:i + 200]).execute()
    except Exception as e:
        return {"written": 0, "error": f"the adjustment ledger was not written — apply migration {MIGRATION} ({str(e)[:120]})"}
    return {"written": len(rows), "error": None}


def landed_units(client, org_id, serials):
    """The rows just inserted, by serial number (a bulk insert that returned no ids), org-scoped."""
    serials = sorted({str(s).strip() for s in serials if str(s or "").strip()})
    out = []
    for i in range(0, len(serials), 100):
        out.extend((client.schema("pos").table("inventory_serial").select(",".join(_ii.UNIT_COLUMNS))
                    .eq("org_id", org_id).in_("serial_number", serials[i:i + 100]).execute().data) or [])
    return out


# ══ the two POS-router endpoints, delegated here (pos/router.py keeps a one-line call) ═══════════════════
def receive_unit(body, authorization, org_id):
    """POST /pos/inventory/serial — receive ONE unit, through THE landing guard. A duplicate of an existing record
    (any status) or a device that already nets sold / kept its commission answers 409 with the evidence, unless
    the body carries `confirm_duplicate: true` ("Receive anyway") — then it lands, and the ledger row says so."""
    ins = {k: body[k] for k in _pr.SERIAL_FIELDS if k in body}
    for k in ("imei", "sim_card", "color", "storage", "cost", "date_received", "po_number", "store_code"):
        if k in ins and ins[k] == "":
            ins[k] = None
    if not (ins.get("product_id") and (ins.get("serial_number") or "").strip()):
        raise HTTPException(400, "product_id and serial_number required")
    ins["serial_number"] = ins["serial_number"].strip()
    ins["org_id"] = org_id
    client = _pr.sb()
    chk = guard_landing(client, org_id, ins)
    confirmed = body.get("confirm_duplicate") is True
    if chk["blocking"] and not confirmed:
        raise HTTPException(409, detail={"code": "inventory_duplicate", "message": _duplicate_message(chk),
                                         "check": _public_check(chk)})
    r = client.schema("pos").table("inventory_serial").insert(ins).execute()
    unit = (r.data or [{}])[0]
    led = record_landing(client, org_id, [unit], "received_duplicate_confirmed" if chk["blocking"] else "received",
                         _actor(authorization, org_id),
                         note=("received anyway: " + ", ".join(chk["reasons"])) if chk["blocking"] else None)
    return {"unit": unit, "landing": {"guard": _public_check(chk) if chk["blocking"] else None, "ledger": led}}


def _same(a, b):
    if a is None or b is None or a == "" or b == "":
        return (a in (None, "")) and (b in (None, ""))
    try:
        return float(a) == float(b)
    except (TypeError, ValueError):
        return str(a).strip() == str(b).strip()


def _update_fields(client, org_id, unit_id, fields):
    """Write NON-status fields of one unit. Refuses a status — a status moves only through
    `apply_status_change` (the ledger)."""
    if "status" in fields:
        raise ValueError("a unit's status moves only through apply_status_change (the adjustment ledger)")
    r = (client.schema("pos").table("inventory_serial").update({**fields, "updated_at": _now()})
         .eq("org_id", org_id).eq("id", unit_id).execute())
    if not r.data:
        raise HTTPException(404, "not found")
    return r.data[0]


def edit_unit(unit_id, body, authorization, org_id):
    """PATCH /pos/inventory/serial/{id}. Only CHANGED fields count. A change to an integrity field (status,
    serial, IMEI, store, cost) needs `pos_inventory_adjust`; a new serial / IMEI asks THE landing guard (409
    unless confirmed); a status change is an ADJUSTMENT — through `apply_status_change`, with its ledger row
    (reason `manual_edit`, the note from `adjust_note`). Other fields (colour, storage, …) write as before."""
    upd = {k: body[k] for k in _pr.SERIAL_FIELDS if k in body}
    if not upd:
        raise HTTPException(400, "nothing to update")
    for k in ("imei", "sim_card", "color", "storage", "cost", "date_received", "po_number", "store_code"):
        if k in upd and upd[k] == "":
            upd[k] = None
    client = _pr.sb()
    unit = _unit(client, org_id, unit_id)
    changed = {k: v for k, v in upd.items() if not _same(unit.get(k), v)}
    if not changed:
        return {"unit": unit}
    if set(changed) & set(INTEGRITY_FIELDS):
        _pr._require_pos_perm(authorization, org_id, ADJUST_PERM)
        _span_check(authorization, org_id, unit)
    new_status = changed.pop("status", None)
    if "serial_number" in changed or "imei" in changed:
        cand = {"serial_number": changed.get("serial_number", unit.get("serial_number")),
                "imei": changed.get("imei", unit.get("imei"))}
        chk = guard_landing(client, org_id, cand, exclude_unit_id=unit_id)
        if chk["blocking"] and body.get("confirm_duplicate") is not True:
            raise HTTPException(409, detail={"code": "inventory_duplicate", "message": _duplicate_message(chk),
                                             "check": _public_check(chk)})
    out = unit
    if changed:
        out = _update_fields(client, org_id, unit_id, changed)
    if new_status:
        res = apply_status_change(client, org_id, {**unit, **out, "status": unit.get("status")}, "manual_edit",
                                  _actor(authorization, org_id), to_status=new_status, note=body.get("adjust_note"))
        out = res["unit"]
    return {"unit": out}


# ══ ENDPOINTS ═════════════════════════════════════════════════════════════════════════════════════════════
@router.get("/inventory/integrity")
def integrity_report(include_closed: bool = False, store_code: str = "", authorization: str = Header(default=""),
                     org_id: str = ORG_ID):
    """The engine's findings merged with the persisted flags. Store-scoped callers see their stores' rows only
    (and no org-wide totals). Every row names the customer the commission report says it was sold to."""
    client = _pr.sb()
    rep, src = run_engine(client, org_id)
    flags, f_ok = _flags(client, org_id)
    view = _ii.merge_view(rep["rows"], flags, include_closed)
    keyset = _pr._caller_store_keyset(authorization, org_id)
    view = _pr._span_filter(view, keyset)
    if store_code:
        view = [r for r in view if r.get("store_code") == store_code]
    names = _pr._product_names(org_id, [r.get("product_id") for r in view])
    for r in view:
        r["product_name"] = r.get("product_name") or (names.get(r.get("product_id")) or {}).get("short_name")
        r["kind_label"] = _ii.KIND_LABELS.get(r.get("kind"))
    return {
        "rows": view, "summary": _ii.summary(view),
        "totals": rep["totals"] if keyset is None else None,
        "basis": src["basis"], "flags_ready": f_ok,
        "setup_note": None if f_ok else f"Flags cannot be saved until migration {MIGRATION} is applied — the findings "
                                        "below are live, but Scan / Assign / Verify need it.",
        "can": {"adjust": _pr._has_pos_perm(authorization, org_id, ADJUST_PERM),
                "verify": _pr._has_pos_perm(authorization, org_id, VERIFY_PERM)},
        "reasons": {k: sorted(v) for k, v in _ii.ADJUST_REASONS.items()},
    }


def scan(client, org_id):
    """Persist / refresh the flags, idempotently (`inventory_integrity.plan_scan`)."""
    rep, src = run_engine(client, org_id)
    flags, ok = _flags(client, org_id)
    if not ok:
        raise _need_1018()
    plan = _ii.plan_scan(org_id, rep["rows"], flags, _now())
    inserted, raced = 0, 0
    for row in plan["insert"]:
        try:
            client.schema("pos").table("inventory_flags").insert(row).execute()
            inserted += 1
        except Exception:
            raced += 1          # a concurrent scan wrote the same live flag first (the partial unique index)
    for fid, patch in plan["update"]:
        client.schema("pos").table("inventory_flags").update(patch).eq("org_id", org_id).eq("id", fid).execute()
    return {"inserted": inserted, "refreshed": plan["refreshed"], "unchanged": plan["unchanged"],
            "reopened": plan["reopened"], "raced": raced, "totals": rep["totals"], "basis": src["basis"]}


def scan_after_landing(client, org_id):
    """The flags scan a bulk landing runs so its already-sold / duplicate units are flagged at once. Never raises
    into the landing; what happened is returned."""
    try:
        return scan(client, org_id)
    except HTTPException as e:
        return {"error": e.detail}
    except Exception as e:
        return {"error": str(e)[:200]}


@router.post("/inventory/integrity/scan")
def scan_endpoint(authorization: str = Header(default=""), org_id: str = ORG_ID):
    _pr._require_any_pos_perm(authorization, org_id, (ADJUST_PERM, VERIFY_PERM))
    return scan(_pr.sb(), org_id)


@router.post("/inventory/flags/{flag_id}/assign")
def assign_flag(flag_id: str, body: dict = None, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """THE ONE CLICK. A sold kind: the customer the commission report names (else the sale line's) is found or
    created through THE matcher (`receipt_import.match_or_create` — phone, else the full name; a
    placeholder name never becomes a customer), the unit goes OUT as sold on the commission's sold-on date, the
    ledger row carries the flag and the customer, and the flag becomes `assigned` — it STAYS visible until a
    manager verifies. A duplicate: the newest unit of the IMEI goes out as `adjusted_out`."""
    _pr._require_pos_perm(authorization, org_id, ADJUST_PERM)
    client = _pr.sb()
    flag = _flag(client, org_id, flag_id)
    try:
        _ii.transition(flag.get("status"), "assign")
        ev = flag.get("evidence") or {}
        units = _units_by_ids(client, org_id, list(ev.get("unit_ids") or []) + [flag.get("unit_id")])
        plan = _ii.assign_plan(flag, units)
    except _ii.IntegrityError as e:
        raise _refused(e)
    _span_check(authorization, org_id, plan["unit"])
    by = _actor(authorization, org_id)
    customer_id, matched = None, None
    if plan["customer"]:
        from app.modules.pos import receipt_import as _ri
        matched = _ri.match_or_create(client, org_id, {"customer_name": plan["customer"]["name"],
                                                       "phone": plan["customer"]["phone"], "sale_date": plan["sold_at"]}, None)
        customer_id = matched.get("customer_id")
    moved = apply_status_change(client, org_id, plan["unit"], plan["reason"], by, flag_id=flag_id,
                                note=(body or {}).get("note"), customer_id=customer_id, sold_at=plan["sold_at"],
                                imei_key=flag.get("imei_key"))
    now = _now()
    patch = {"status": _ii.ASSIGNED, "assigned_customer_id": customer_id, "assigned_by": by, "assigned_at": now,
             "unit_id": plan["unit"]["id"], "updated_at": now}
    r = client.schema("pos").table("inventory_flags").update(patch).eq("org_id", org_id).eq("id", flag_id).execute()
    return {"flag": (r.data or [{**flag, **patch}])[0], "unit": moved["unit"], "adjustment": moved["adjustment"],
            "customer_id": customer_id, "customer_created": bool((matched or {}).get("created")),
            "customer_decision": ((matched or {}).get("decision") or {}).get("reason"),
            "customer_note": None if customer_id or not plan["customer"] else
            "no customer could be matched or created from the report's name / phone — the unit was moved out without one"}


@router.post("/inventory/flags/{flag_id}/verify")
def verify_flag(flag_id: str, body: dict = None, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """MANAGEMENT (pos_inventory_verify). approve (default): assigned → verified — the flag closes. approve:false
    (a note required): REJECT — the unit comes back IN through the ledger to the status it left, the flag re-opens."""
    _pr._require_pos_perm(authorization, org_id, VERIFY_PERM)
    body = body or {}
    client = _pr.sb()
    flag = _flag(client, org_id, flag_id)
    note = str(body.get("note") or "").strip() or None
    by, now = _actor(authorization, org_id), _now()
    approve = body.get("approve", True) is not False
    try:
        _ii.transition(flag.get("status"), "verify" if approve else "reject")
    except _ii.IntegrityError as e:
        raise _refused(e)
    if approve:
        patch = {"status": _ii.VERIFIED, "verified_by": by, "verified_at": now, "updated_at": now}
        if note:
            patch["note"] = note
        r = client.schema("pos").table("inventory_flags").update(patch).eq("org_id", org_id).eq("id", flag_id).execute()
        return {"flag": (r.data or [{**flag, **patch}])[0]}
    if not note:
        raise HTTPException(400, "say why the assignment is rejected (a note is required)")
    unit = _unit(client, org_id, flag.get("unit_id"))
    _span_check(authorization, org_id, unit)
    prior = (client.schema("pos").table("inventory_adjustments").select("from_status,created_at")
             .eq("org_id", org_id).eq("flag_id", flag_id).order("created_at", desc=True).limit(1).execute().data) or []
    back_to = (prior[0].get("from_status") if prior else None) or _ii.LIVE_STATUSES[0]
    moved = apply_status_change(client, org_id, unit, "reject_assign", by, to_status=back_to, flag_id=flag_id, note=note,
                                imei_key=flag.get("imei_key"))
    patch = {"status": _ii.OPEN, "assigned_customer_id": None, "assigned_by": None, "assigned_at": None,
             "note": f"assignment rejected: {note}", "updated_at": now}
    r = client.schema("pos").table("inventory_flags").update(patch).eq("org_id", org_id).eq("id", flag_id).execute()
    return {"flag": (r.data or [{**flag, **patch}])[0], "unit": moved["unit"], "adjustment": moved["adjustment"]}


@router.post("/inventory/flags/{flag_id}/dismiss")
def dismiss_flag(flag_id: str, body: dict = None, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """MANAGEMENT, a note required: open → dismissed (the finding is not a problem — e.g. a real return). The
    unit is not touched. A later scan re-opens it only if the evidence changes."""
    _pr._require_pos_perm(authorization, org_id, VERIFY_PERM)
    note = str((body or {}).get("note") or "").strip()
    if not note:
        raise HTTPException(400, "a note is required to dismiss a flag")
    client = _pr.sb()
    flag = _flag(client, org_id, flag_id)
    try:
        _ii.transition(flag.get("status"), "dismiss")
    except _ii.IntegrityError as e:
        raise _refused(e)
    now = _now()
    patch = {"status": _ii.DISMISSED, "dismissed_by": _actor(authorization, org_id), "dismissed_at": now,
             "note": note, "updated_at": now}
    r = client.schema("pos").table("inventory_flags").update(patch).eq("org_id", org_id).eq("id", flag_id).execute()
    return {"flag": (r.data or [{**flag, **patch}])[0]}


@router.post("/inventory/adjust")
def adjust_unit(body: dict, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """MANUAL ADJUST IN / OUT (pos_inventory_adjust): {unit_id, direction: in|out, reason, note?, flag_id?}. The
    reason decides the status (ADJUST_REASONS); the ledger row records who, why and from → to. With an OPEN
    `flag_id` the flag becomes `assigned` — resolved, awaiting a manager's verification."""
    _pr._require_pos_perm(authorization, org_id, ADJUST_PERM)
    body = body or {}
    if not body.get("unit_id"):
        raise HTTPException(400, "unit_id required")
    client = _pr.sb()
    unit = _unit(client, org_id, body["unit_id"])
    _span_check(authorization, org_id, unit)
    flag = _flag(client, org_id, body["flag_id"]) if body.get("flag_id") else None
    by = _actor(authorization, org_id)
    moved = apply_status_change(client, org_id, unit, str(body.get("reason") or "").strip(), by,
                                direction=str(body.get("direction") or "").strip().lower(),
                                flag_id=(flag or {}).get("id"), note=body.get("note"),
                                imei_key=(flag or {}).get("imei_key"))
    out = {"unit": moved["unit"], "adjustment": moved["adjustment"]}
    if flag and flag.get("status") == _ii.OPEN:
        now = _now()
        patch = {"status": _ii.ASSIGNED, "assigned_by": by, "assigned_at": now, "unit_id": unit["id"], "updated_at": now}
        r = client.schema("pos").table("inventory_flags").update(patch).eq("org_id", org_id).eq("id", flag["id"]).execute()
        out["flag"] = (r.data or [{**flag, **patch}])[0]
    return out


@router.get("/inventory/adjustments")
def list_adjustments(unit_id: str = "", limit: int = 200, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """The adjustment ledger (newest first), optionally for one unit — store-scoped like the units — and the manual
    reasons the adjust modal offers (THE list, inventory_integrity.ADJUST_REASONS — the page keeps no copy)."""
    reasons = {k: sorted(v) for k, v in _ii.ADJUST_REASONS.items()}
    q = (_pr.sb().schema("pos").table("inventory_adjustments").select("*").eq("org_id", org_id))
    if unit_id:
        q = q.eq("unit_id", unit_id)
    try:
        rows = q.order("created_at", desc=True).limit(max(1, min(int(limit or 200), 2000))).execute().data or []
    except Exception as e:
        return {"rows": [], "reasons": reasons, "setup_note": f"apply migration {MIGRATION} ({str(e)[:120]})"}
    return {"rows": _pr._span_filter(rows, _pr._caller_store_keyset(authorization, org_id)), "reasons": reasons}
