"""INVENTORY INTEGRITY — the flag state machine, the scan planner, the adjustment rules and the import report.
PURE (stdlib + the pure engine `commcalc.inventory_sold_recon`). No DB, no FastAPI.

OWNER (2026-09-24), verbatim: *"it is not possible that 383 imei have not ben sold but appering in the invnetory
so they have to be crashed agains the sales report by invoice and the commission received reports to, it is
possoble that the receipt was not made and it sold, 2 things it shoudl apprear as a flag and also highlight which
customer it was sold to from commisison report and have the ability to assign it tot hte customer with one click
if the imei was sold and commission revceived to move it out of the inventory, the flag still stays there till
verified by the management that the imei was actually sold, need to do this for all tenants as platform wide"*.
Earlier: *"duplicate entires of imei have to checked before they are received and should give a pop up, if they
are duplicated by the way of import that report should be generated and checked against the imei received and
already sold, if they have are duplicated and already sold then it should be highligheted and iption to adjust
put with one click"* and *"we need to make a mechanish of manually adjusting the inventory in or out of the
system"*.

WHAT LIVES HERE (and what does not). The FINDINGS — which unit is sold-still-on-hand, sold-with-no-receipt, a
duplicate, … — are the ONE engine's (`inventory_sold_recon.integrity` / `receive_check` / `classify_unit`); this
module never re-derives them. It owns what happens AFTER a finding: the flag's life (open → assigned → verified,
or dismissed with a note), when a re-scan may re-open a closed flag (only when the evidence changed), what a
one-click resolution moves, which manual adjustments exist and what status each lands a unit in, and the shape
of the adjustment-ledger row every status change writes.

THE CLASS THIS CLOSES — *an inventory status change without a ledger row.* Before this, `PATCH
/pos/inventory/serial/{id}` could set any status with no permission and no record. Every status change the
application makes now goes through ONE writer (`inventory_integrity_router.apply_status_change`), which writes
the ledger row this module shapes (`ledger_row`); the lock fails the build on a second writer.

RULE TWO: no carrier, tenant, POS or distributor word. The statuses are the POS table's own vocabulary (mig 725
+ mig 1018's `adjusted_out`); the reasons are generic stock-keeping words.
"""
from __future__ import annotations

import hashlib
import json

from app.modules.commcalc import inventory_sold_recon as _isr

# ── the unit statuses (pos.inventory_serial, mig 725 CHECK + mig 1018 adds `adjusted_out`) ─────────────────
LIVE_STATUSES = ("in_stock", "in_transit")          # the statuses the checkout trigger sells from = on hand
UNIT_STATUSES = ("in_stock", "in_transit", "sold", "returned", "transferred", "rma", "lost", "stolen", "adjusted_out")
UNIT_COLUMNS = ("id", "org_id", "product_id", "store_code", "serial_number", "imei", "sim_card", "color", "storage",
                "condition", "status", "cost", "date_received", "po_number", "sold_at", "sold_in_sale_id",
                "created_at", "updated_at")          # mig 725 exactly — the reader selects these, never '*'


def is_live(status):
    """Is a unit with this status on hand (sellable)? The checkout trigger's own set."""
    return str(status or "").strip() in LIVE_STATUSES


# ── the flag ─────────────────────────────────────────────────────────────────────────────────────────────
OPEN, ASSIGNED, VERIFIED, DISMISSED = "open", "assigned", "verified", "dismissed"
FLAG_STATUSES = (OPEN, ASSIGNED, VERIFIED, DISMISSED)
ACTIVE = (OPEN, ASSIGNED)                              # "one live flag per (org, imei_key, kind)" — mig 1018 index
TERMINAL = (VERIFIED, DISMISSED)
# action → {from status: to status}. `reject` is the manager saying "not sold after all": the unit is moved back
# IN through the ledger and the flag re-opens. A verified or dismissed flag is never moved by an action.
TRANSITIONS = {
    "assign": {OPEN: ASSIGNED},
    "verify": {ASSIGNED: VERIFIED},
    "reject": {ASSIGNED: OPEN},
    "dismiss": {OPEN: DISMISSED},                     # an ASSIGNED flag moved a unit: verify it or reject it
}
NOTE_REQUIRED = ("dismiss", "reject")
KIND_LABELS = {
    _isr.SOLD_NO_RECEIPT: "Sold per commission — no sales line / receipt",
    _isr.SOLD_STILL_ON_HAND: "Sold (sales report) — still on hand",
    _isr.RETURNED_COMMISSION_KEPT: "Returned on the sales report — commission kept (review)",
    _isr.RECEIVED_AFTER_SOLD: "Received after it was already sold",
    _isr.DUPLICATE_ON_HAND: "Same IMEI on more than one unit in stock",
}
assert set(KIND_LABELS) == set(_isr.INTEGRITY_KINDS), "every engine kind has a label"


class IntegrityError(ValueError):
    """A refused action — the message is the sentence the page shows."""


def transition(status, action):
    """The flag's next status for `action`, or IntegrityError naming why not."""
    nxt = (TRANSITIONS.get(action) or {}).get(status)
    if not nxt:
        allowed = sorted(TRANSITIONS.get(action) or {})
        raise IntegrityError(f"'{action}' is not possible on a flag that is '{status}'"
                             + (f" — only on one that is {' / '.join(allowed)}" if allowed else ""))
    return nxt


def _stable(v):
    """The part of a finding that DECIDES it — so a re-scan can tell "the same facts" from "new facts"."""
    return json.dumps(v, sort_keys=True, default=str, separators=(",", ":"))


def evidence_hash(finding):
    """A stable fingerprint of the facts behind a finding: kind, device, the units, the sale net and its
    invoices, the commission net / kept / invoices. A verified or dismissed flag re-opens ONLY when this changes."""
    f = finding or {}
    sale, com = f.get("sale") or {}, f.get("commission") or {}
    facts = {
        "kind": f.get("kind"), "key": f.get("device_key"), "units": sorted(str(u) for u in (f.get("unit_ids") or []) if u),
        "sale_net": sale.get("net"), "sale_invoices": sorted(str(x.get("invoice")) for x in (sale.get("lines") or [])),
        "com_net": com.get("net_earned"), "com_kept": com.get("kept"), "com_invoices": sorted(com.get("invoices") or []),
    }
    return hashlib.sha1(_stable(facts).encode()).hexdigest()


def compact_evidence(finding):
    """What a flag STORES about its finding (jsonb) — enough to render the row and to assign with one click after
    the unit has left the on-hand set (an assigned flag stays visible until verified)."""
    f = finding or {}
    keep = ("kind", "device_key", "unit_id", "unit_ids", "serial_number", "imei", "store_code", "status", "product_id",
            "product_name", "cost", "received_on", "sold_on", "customer", "invoice_no", "evidence")
    out = {k: f.get(k) for k in keep}
    com = f.get("commission")
    if com:
        out["commission"] = {k: com.get(k) for k in ("net_earned", "earned_gross", "charged_back", "kept", "reversed",
                                                     "invoices", "sold_on", "customer_name", "customer_ref", "mobile",
                                                     "device_name", "device_sku", "rate_plan", "rows")}
    sale = f.get("sale")
    if sale:
        out["sale"] = {k: sale.get(k) for k in ("net", "lines", "last_sold_on", "customer", "mobile", "invoice")}
    return out


def flag_row(org_id, finding, now, note=None):
    """The pos.inventory_flags row for a NEW open flag (mig 1018 columns)."""
    f = finding or {}
    cust = f.get("customer") or {}
    return {"org_id": org_id, "unit_id": f.get("unit_id"), "imei_key": f.get("device_key"), "kind": f.get("kind"),
            "evidence": compact_evidence(f), "evidence_hash": evidence_hash(f),
            "customer_name": cust.get("name"), "invoice_no": f.get("invoice_no"), "sold_on": f.get("sold_on"),
            "status": OPEN, "note": note, "last_seen_at": now, "created_at": now, "updated_at": now}


def plan_scan(org_id, findings, flags, now):
    """PERSIST OR REFRESH, IDEMPOTENTLY. `findings` = the engine's rows; `flags` = the org's existing flag rows.
    Returns {"insert": [row…], "update": [(flag_id, patch)…], "unchanged": n, "reopened": n, "refreshed": n}.

      · an ACTIVE flag (open / assigned) for (key, kind) → refreshed in place (evidence, unit, last_seen_at) —
        never a second row, never a status change;
      · else the LATEST terminal flag (verified / dismissed) with the SAME evidence → left alone: a scan never
        re-opens what management closed;
      · else (none, or the evidence CHANGED since it was closed) → a NEW open flag, the old row kept as history,
        the note saying why it came back.
    Running it twice on the same data changes nothing the second time."""
    by = {}
    for fl in flags or []:
        by.setdefault((fl.get("imei_key"), fl.get("kind")), []).append(fl)
    out = {"insert": [], "update": [], "unchanged": 0, "reopened": 0, "refreshed": 0}
    for f in findings or []:
        k = (f.get("device_key"), f.get("kind"))
        mine = by.get(k) or []
        h = evidence_hash(f)
        active = [x for x in mine if x.get("status") in ACTIVE]
        if active:
            fl = active[0]
            patch = {"evidence": compact_evidence(f), "evidence_hash": h, "last_seen_at": now, "updated_at": now,
                     "customer_name": (f.get("customer") or {}).get("name"), "invoice_no": f.get("invoice_no"),
                     "sold_on": f.get("sold_on")}
            if fl.get("status") == OPEN:
                patch["unit_id"] = f.get("unit_id")      # an assigned flag keeps the unit it moved
            out["update"].append((fl.get("id"), patch))
            out["refreshed"] += 1
            continue
        closed = sorted((x for x in mine if x.get("status") in TERMINAL),
                        key=lambda x: str(x.get("updated_at") or x.get("created_at") or ""))
        if closed and closed[-1].get("evidence_hash") == h:
            out["unchanged"] += 1
            continue
        note = None
        if closed:
            last = closed[-1]
            note = (f"re-opened by the scan: the evidence changed since this was {last.get('status')} "
                    f"on {str(last.get('updated_at') or '')[:10] or 'an earlier date'}")
            out["reopened"] += 1
        out["insert"].append(flag_row(org_id, f, now, note))
    return out


def merge_view(findings, flags, include_closed=False):
    """What GET shows: the engine's findings merged with the persisted flags.
      · a finding with an ACTIVE flag → the flag's status + id; `still_found` true;
      · a finding whose latest flag is CLOSED with the same evidence → shown only with `include_closed`;
      · a finding with no flag yet → status 'new' (the Scan button persists it);
      · an ACTIVE flag the engine no longer finds (typically: assigned, so the unit left the shelf) → STILL shown,
        from its stored evidence, `still_found` false — "the flag still stays there till verified";
      · closed flags the engine no longer finds → only with `include_closed`."""
    by = {}
    for fl in flags or []:
        by.setdefault((fl.get("imei_key"), fl.get("kind")), []).append(fl)
    rows, used = [], set()
    for f in findings or []:
        k = (f.get("device_key"), f.get("kind"))
        mine = sorted(by.get(k) or [], key=lambda x: str(x.get("updated_at") or x.get("created_at") or ""))
        active = [x for x in mine if x.get("status") in ACTIVE]
        h = evidence_hash(f)
        fl = active[-1] if active else (mine[-1] if mine and mine[-1].get("evidence_hash") == h else None)
        if fl:
            used.add(fl.get("id"))
            if fl.get("status") in TERMINAL and not include_closed:
                continue
        rows.append({**compact_evidence(f), "flag_id": (fl or {}).get("id"), "flag_status": (fl or {}).get("status") or "new",
                     "still_found": True, **_flag_meta(fl)})
    for fl in flags or []:
        if fl.get("id") in used:
            continue
        if fl.get("status") in TERMINAL and not include_closed:
            continue
        ev = dict(fl.get("evidence") or {})
        ev.setdefault("kind", fl.get("kind"))
        ev.setdefault("device_key", fl.get("imei_key"))
        ev.setdefault("unit_id", fl.get("unit_id"))
        rows.append({**ev, "flag_id": fl.get("id"), "flag_status": fl.get("status"), "still_found": False, **_flag_meta(fl)})
    order = {k: i for i, k in enumerate(_isr.INTEGRITY_KINDS)}
    st = {"new": 0, OPEN: 1, ASSIGNED: 2, VERIFIED: 3, DISMISSED: 4}
    rows.sort(key=lambda r: (st.get(r.get("flag_status"), 9), order.get(r.get("kind"), 99), -float(r.get("cost") or 0),
                             str(r.get("device_key"))))
    return rows


def _flag_meta(fl):
    fl = fl or {}
    return {k: fl.get(k) for k in ("assigned_customer_id", "assigned_by", "assigned_at", "verified_by", "verified_at",
                                   "dismissed_by", "dismissed_at", "note", "last_seen_at")}


def summary(view_rows):
    """Counts + cost at the top of the tab: per kind (active flags + not-yet-scanned findings), per status."""
    by_kind = {k: {"count": 0, "cost": 0.0} for k in _isr.INTEGRITY_KINDS}
    by_status = {s: 0 for s in ("new",) + FLAG_STATUSES}
    for r in view_rows or []:
        s = r.get("flag_status") or "new"
        by_status[s] = by_status.get(s, 0) + 1
        if s in ("new",) + ACTIVE and r.get("kind") in by_kind:
            by_kind[r["kind"]]["count"] += 1
            by_kind[r["kind"]]["cost"] = round(by_kind[r["kind"]]["cost"] + float(r.get("cost") or 0), 2)
    open_rows = [r for r in view_rows or [] if (r.get("flag_status") or "new") in ("new",) + ACTIVE]
    return {"by_kind": by_kind, "by_status": by_status, "open": len(open_rows),
            "open_cost": round(sum(float(r.get("cost") or 0) for r in open_rows), 2),
            "labels": dict(KIND_LABELS)}


# ── manual adjustments: the reasons and what status each lands a unit in ─────────────────────────────────
IN, OUT, STATUS = "in", "out", "status"        # STATUS = a change that neither enters nor leaves stock
ADJUST_REASONS = {
    OUT: {"sold": "sold", "lost": "lost", "stolen": "stolen", "rma": "rma",
          "duplicate_record": "adjusted_out", "damaged_write_off": "adjusted_out", "count_correction": "adjusted_out"},
    IN: {"found": "in_stock", "count_correction": "in_stock", "customer_return": "in_stock",
         "rma_returned": "in_stock", "reversal": "in_stock"},
}
# the system's own reasons (written by the one writer, never offered in the modal) → the status each lands in;
# None = the caller names it (the edit form's status select).
SYSTEM_REASONS = {"assign_sold": "sold", "assign_duplicate": "adjusted_out", "reject_assign": None, "manual_edit": None}
LANDING_REASONS = ("received", "received_duplicate_confirmed", "import")   # a unit created — from nothing to in stock


def direction_of(from_status, to_status):
    """in / out / status for a change (live → not live is OUT, not live → live is IN, anything else STATUS)."""
    a, b = is_live(from_status), is_live(to_status)
    return OUT if a and not b else IN if b and not a else STATUS


def plan_adjustment(unit, direction, reason, to_status=None):
    """Validate an adjustment and say where the unit lands. Returns (from_status, to_status, direction).
      · a MANUAL reason (the modal): `direction` must be in/out, the reason one of ADJUST_REASONS[direction];
        OUT needs a unit in stock, IN a unit that is not;
      · a SYSTEM reason: the status is the reason's own (assign) or the caller's `to_status` (edit / reject), and
        the direction is DERIVED from the two statuses — never trusted from the caller."""
    frm = str((unit or {}).get("status") or "").strip() or None
    if reason in SYSTEM_REASONS:
        dest = SYSTEM_REASONS[reason] or to_status
        direction = direction_of(frm, dest)
    else:
        if direction not in (IN, OUT):
            raise IntegrityError("direction must be 'in' or 'out'")
        if reason not in ADJUST_REASONS[direction]:
            raise IntegrityError(f"'{reason}' is not an adjust-{direction} reason "
                                 f"(one of: {', '.join(sorted(ADJUST_REASONS[direction]))})")
        dest = ADJUST_REASONS[direction][reason]
        if direction == OUT and not is_live(frm):
            raise IntegrityError(f"only a unit that is in stock can be adjusted out (this one is '{frm}')")
        if direction == IN and is_live(frm):
            raise IntegrityError(f"this unit is already in stock ('{frm}')")
    if dest not in UNIT_STATUSES:
        raise IntegrityError(f"'{dest}' is not a unit status")
    if dest == frm:
        raise IntegrityError(f"the unit is already '{frm}'")
    if reason in ("assign_sold", "assign_duplicate") and not is_live(frm):
        raise IntegrityError(f"the unit is no longer in stock ('{frm}') — nothing to move out")
    return frm, dest, direction


def ledger_row(org_id, unit, direction, reason, from_status, to_status, by, imei_key=None, flag_id=None, note=None,
               customer_id=None):
    """The pos.inventory_adjustments row (mig 1018) — one per status change, written by the one writer."""
    u = unit or {}
    return {"org_id": org_id, "unit_id": u.get("id"), "imei_key": imei_key, "store_code": u.get("store_code"),
            "direction": direction, "reason": reason, "from_status": from_status, "to_status": to_status,
            "flag_id": flag_id, "customer_id": customer_id, "note": (str(note).strip() or None) if note else None,
            "created_by": by or None}


def assign_plan(flag, units_by_id):
    """What the ONE-CLICK does for a flag. Returns {unit, reason, to_status, sold_at, customer {name, phone} | None}.
      · a sold kind (sold_no_receipt / sold_still_on_hand / received_after_sold) and returned_commission_kept →
        the unit goes OUT as SOLD, dated the commission's sold-on (else the sale's), to the customer the
        commission report names (else the sale line's);
      · duplicate_on_hand → the NEWEST live unit of the key goes OUT as `adjusted_out` (the second record of one
        device), no customer."""
    ev = (flag or {}).get("evidence") or {}
    kind = (flag or {}).get("kind")
    if kind == _isr.DUPLICATE_ON_HAND:
        live = [units_by_id[i] for i in (ev.get("unit_ids") or []) if i in units_by_id and is_live(units_by_id[i].get("status"))]
        if len(live) < 2:
            raise IntegrityError("this IMEI is no longer on more than one unit in stock — nothing to remove")
        live.sort(key=lambda u: (str(u.get("created_at") or ""), str(u.get("id") or "")))
        return {"unit": live[-1], "reason": "assign_duplicate", "to_status": "adjusted_out", "sold_at": None, "customer": None}
    unit = units_by_id.get((flag or {}).get("unit_id") or ev.get("unit_id"))
    if not unit:
        raise IntegrityError("the unit this flag names is no longer in the inventory")
    com, sale = ev.get("commission") or {}, ev.get("sale") or {}
    sold_at = com.get("sold_on") or ev.get("sold_on") or sale.get("last_sold_on")
    cust = ev.get("customer") or {}
    customer = {"name": cust.get("name"), "phone": cust.get("mobile")} if (cust.get("name") or cust.get("mobile")) else None
    return {"unit": unit, "reason": "assign_sold", "to_status": "sold", "sold_at": sold_at, "customer": customer}


# ── the landing report (import) ──────────────────────────────────────────────────────────────────────────
def import_report(incoming, existing_units, key_of, sales_idx, comm_idx):
    """THE LANDING GUARD over a BATCH (the onboarding bring-over; any bulk path). Every incoming unit is asked the
    SAME `receive_check` the single receive asks, against the org's existing units AND the batch so far.
    Returns {"land": [unit…], "skip": [{unit, reason}], "report": {duplicates[], already_sold[], within_file[],
    no_key[]}} — a duplicate of an existing record or of an earlier line in the file is SKIPPED and reported; an
    already-sold device is LANDED (it is on the shelf per the export) and REPORTED, so the flags scan picks it up
    and it can be adjusted out with one click. Nothing is dropped silently."""
    land, skip = [], []
    rep = {"duplicates": [], "already_sold": [], "within_file": [], "no_key": []}
    batch = []
    for u in incoming or []:
        keys = _isr.unit_keys(u, key_of)
        if not keys:
            rep["no_key"].append(_brief(u))
            skip.append({"unit": u, "reason": "no usable serial / IMEI"})
            continue
        sale = next((sales_idx[k] for k in keys if k in sales_idx), None)
        com = next((comm_idx[k] for k in keys if k in comm_idx), None)
        chk = _isr.receive_check(u, existing_units, key_of, is_live, sale, com)
        if chk["existing"]:
            rep["duplicates"].append({**_brief(u), "existing": [_brief(x) for x in chk["existing"]],
                                      "already_sold": chk["already_sold"], "customer": chk["customer"]})
            skip.append({"unit": u, "reason": "duplicate of an existing unit"})
            continue
        if set(keys) & set(k for b in batch for k in b):
            rep["within_file"].append(_brief(u))
            skip.append({"unit": u, "reason": "the same IMEI earlier in this file"})
            continue
        batch.append(keys)
        if chk["already_sold"]:
            rep["already_sold"].append({**_brief(u), "sold_on": chk["sold_on"], "customer": chk["customer"],
                                        "commission_kept": bool(com and com.get("kept")),
                                        "sale_net": (sale or {}).get("net")})
        land.append(u)
    return {"land": land, "skip": skip, "report": rep}


def _brief(u):
    u = u or {}
    return {k: u.get(k) for k in ("id", "serial_number", "imei", "store_code", "status", "date_received")}
