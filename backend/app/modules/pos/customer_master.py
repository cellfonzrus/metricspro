"""THE CUSTOMER MASTER — the I/O around `pos/customer_identity` (owner 2026-09-24; index §30.16).

OWNER, verbatim: *"now these customers which are uploaded should be available in the pos to make the sales in
future and if any data is later uploaded for the same customer the system to check for existing fields to match
the data and update their record rather than creating a new record … if in the last 2 years data a customer
came in twice to get phones on different names they should be combined together and shows in the customer pages
as separate line items with sales from different dates which can be opened, edited or notes added, option to add
notes on every single line of sales data per phone number, each customer will bear separate lines with individual
phone numbers in line with their imei and plan shown on the customer page to give more details for that
customer, with another tab showing how much they paid for that invoice"*.

WHAT LIVES WHERE (one fact, one home — dereferenced, never copied):
  · WHO the customer is — `customer_identity.decide` (pure), driven by THE matcher `receipt_import.find_customer`
    / `match_or_create` (the only writer that creates a customer from a sale). This module never decides.
  · A customer's PHONE LINES — `pos.activations` (mig 726): one row per sale × phone number, written by
    `sync_invoice_lines` from the invoice's landed lines through `customer_identity.invoice_lines` (the pairing
    rule `inventory_sold_recon.line_pairings`, the plan words `plan_sources`, both dereferenced). Notes per
    dated line are the EXISTING `pos.activation_notes` (`/pos/activations/{id}/notes`); edits the EXISTING
    `PATCH /pos/activations/{id}`.
  · The names a customer also went by — `pos.customer_aliases` (mig 1017); a merge — `pos.customers.merged_into`
    + `merge_record` (what moved, so it can be undone). Migration 1017 may not be applied yet: every reader and
    writer PROBES it (`core.column_tolerant.present_columns`) and, when it is absent, skips the alias / merge write
    and SAYS "apply migration 1017" — never crashes.
  · The customer page's payloads — `lines_payload` (one entry per phone number, its dated sales) and
    `invoices_payload` (per invoice: what the CUSTOMER paid = Σ the receipt's payment lines, which the rebuild
    filled through `closing.router.is_customer_payment` — the one answer; a register sale's `pos.sale_payments`).

Every query is org-scoped. RULE TWO: no carrier, POS vendor, tenant or product name here.
"""
from __future__ import annotations

import datetime as _dt
import re
import time as _time

from app.core import column_tolerant as _ct
from app.modules.pos import customer_identity as _cid

MIGRATION = "1017_pos_customer_identity.sql"
MIGRATION_WORDS = ("apply migration 1017 (database/migrations/1017_pos_customer_identity.sql) — until it is applied the "
                   "other names a customer went by are not recorded and customers cannot be merged or un-merged")
# every pos table that points at a customer — what a merge repoints (and an un-merge puts back). ONE list.
CUSTOMER_LINKS = ("sales", "receipt_imports", "activations", "trade_ins", "special_orders", "customer_notes", "customer_aliases")
SCHEMA_TTL_SECONDS = 300          # a probe result is re-checked after this long, so applying 1017 needs no restart
_SCHEMA: dict = {}
_PAGE = 1000


def _s(v):
    return "" if v is None else str(v).strip()


def _now():
    return _dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _pos(client, table):
    return client.schema("pos").table(table)


def ttl_cached(cache, key, fn, ttl=SCHEMA_TTL_SECONDS):
    """A probe cached per client × org for `ttl` seconds (a hand-applied migration is seen without a restart)."""
    hit = cache.get(key)
    now = _time.monotonic()
    if hit and now - hit[0] < ttl:
        return hit[1]
    val = fn()
    cache[key] = (now, val)
    return val


def identity_schema(client, org_id):
    """What migration 1017 has put in place, probed per column (the one reading rule):
    {"aliases": bool, "merged_into": bool, "merge_record": bool, "ready": bool, "missing": [...], "words": str|None}."""
    def probe():
        al = _ct.present_columns(lambda: _pos(client, "customer_aliases"), lambda q: q.eq("org_id", org_id), ("alias_norm",))
        cu = _ct.present_columns(lambda: _pos(client, "customers"), lambda q: q.eq("org_id", org_id), ("merged_into", "merge_record"))
        out = {"aliases": "alias_norm" in al, "merged_into": "merged_into" in cu, "merge_record": "merge_record" in cu}
        out["missing"] = [k for k in ("customer_aliases", "merged_into", "merge_record")
                          if not out["aliases" if k == "customer_aliases" else k]]
        out["ready"] = not out["missing"]
        out["words"] = None if out["ready"] else MIGRATION_WORDS
        return out
    return ttl_cached(_SCHEMA, (id(client), org_id), probe)


def reset_caches():
    _SCHEMA.clear()


def _page_all(q_fn):
    out, start = [], 0
    while True:
        rows = (q_fn().range(start, start + _PAGE - 1).execute().data) or []
        out.extend(rows)
        if len(rows) < _PAGE:
            return out
        start += _PAGE


def in_chunks(ids, n=200):
    ids = [i for i in ids if i]
    for i in range(0, len(ids), n):
        yield ids[i:i + n]


# ── aliases ──────────────────────────────────────────────────────────────────────────────────────────
def add_alias(client, org_id, customer_id, name, source, seen=None):
    """Record another name this customer went by (mig 1017). Returns the alias row id, None when there is
    nothing to record, or False when the table is not there yet (the caller says `MIGRATION_WORDS`)."""
    nn = _cid.norm_name(name)
    if not customer_id or not nn or _cid.is_placeholder(name):
        return None
    if not identity_schema(client, org_id)["aliases"]:
        return False
    seen = _s(seen)[:10] or None
    rows = (_pos(client, "customer_aliases").select("id,first_seen,last_seen").eq("org_id", org_id)
            .eq("customer_id", customer_id).eq("alias_norm", nn).limit(1).execute().data) or []
    if rows:
        r = rows[0]
        patch = {}
        if seen and (not r.get("first_seen") or seen < _s(r.get("first_seen"))):
            patch["first_seen"] = seen
        if seen and (not r.get("last_seen") or seen > _s(r.get("last_seen"))):
            patch["last_seen"] = seen
        if patch:
            _pos(client, "customer_aliases").update(patch).eq("org_id", org_id).eq("id", r["id"]).execute()
        return r["id"]
    ins = (_pos(client, "customer_aliases").insert({"org_id": org_id, "customer_id": customer_id, "alias_name": _s(name),
                                                    "alias_norm": nn, "source": source, "first_seen": seen, "last_seen": seen})
           .execute().data) or [{}]
    return ins[0].get("id")


def aliases_of(client, org_id, ids):
    """{customer_id: [alias rows]} for these customers; {} before mig 1017."""
    out = {}
    if not ids or not identity_schema(client, org_id)["aliases"]:
        return out
    for chunk in in_chunks(sorted(set(ids))):
        for r in (_pos(client, "customer_aliases").select("id,customer_id,alias_name,alias_norm,source,first_seen,last_seen,created_at")
                  .eq("org_id", org_id).in_("customer_id", chunk).limit(5000).execute().data) or []:
            out.setdefault(r["customer_id"], []).append(r)
    return out


def alias_owners(client, org_id, name):
    """The customers carrying this name as an alias (mig 1017); [] before it."""
    nn = _cid.norm_name(name)
    if not nn or not identity_schema(client, org_id)["aliases"]:
        return []
    rows = (_pos(client, "customer_aliases").select("customer_id").eq("org_id", org_id).eq("alias_norm", nn)
            .limit(200).execute().data) or []
    return sorted({r["customer_id"] for r in rows if r.get("customer_id")})


# ── the lines: one pos.activations row per sale × phone number ───────────────────────────────────────
LINE_FILL_FIELDS = ("phone_serial", "phone_model", "plan_description", "contract_type", "activation_date", "store_code")


def plan_line_predicate(client, org_id):
    """The org's plan-line words (`plan_sources` source `sales_lines`, read through onboarding.load_plan_sources —
    the one reader) as a predicate over a landed line; None when the org has confirmed no words (no plan is
    ever guessed: the line's plan stays empty)."""
    from app.modules.core import plan_sources as _ps
    from app.modules.core import onboarding as _onb
    src = _ps.source_of(_onb.load_plan_sources(client, org_id), "sales_lines")
    if not src or not src.get("include"):
        return None
    return lambda row: _ps.line_matches(row, src)


def sync_invoice_lines(client, org_id, *, sale_id, customer_id, rows, sale_date=None, store_code=None, employee_id=None,
                       invoice_no=None, plan_line=None):
    """ONE invoice's phone lines onto its customer: one `pos.activations` row per distinct phone number (keyed
    org × sale × cell_number — select, then update or insert; a re-run never duplicates). A NEW row carries the
    number, the paired device (IMEI) and its name, the plan, the invoice date, the contract type, the store and
    rep, status 'active'. An EXISTING row is kept: its customer follows the sale's customer, and only its EMPTY
    fields are filled — an edit made on the customer page is never overwritten by a re-run. A number that left
    the invoice keeps its row (it may carry notes) and is SAID. No customer → nothing written (said).
    Returns {"written", "updated", "unchanged", "kept", "lines": [...], "words": [...]}."""
    from app.modules.commcalc import inventory_sold_recon as _isr
    from app.modules.commcalc import device_cost_recon as _dcr
    res = {"written": 0, "updated": 0, "unchanged": 0, "kept": 0, "lines": [], "words": []}
    lines = _cid.invoice_lines(rows, _isr.line_pairings, _dcr.device_key, plan_line)
    res["lines"] = lines
    if not lines:
        return res
    if not (sale_id and customer_id):
        res["words"].append(f"{len(lines)} phone line(s) on this invoice are not recorded on a customer — the sale has no customer")
        return res
    existing = {}
    for r in (_pos(client, "activations").select("id,customer_id,cell_number," + ",".join(LINE_FILL_FIELDS))
              .eq("org_id", org_id).eq("sale_id", sale_id).limit(500).execute().data) or []:
        existing.setdefault(_cid.norm_phone(r.get("cell_number")) or _s(r.get("cell_number")), r)
    date = _s(sale_date)[:10] or None
    for ln in lines:
        want = {"phone_serial": ln.get("imei"), "phone_model": ln.get("device_name"), "plan_description": ln.get("plan") or None,
                "contract_type": ln.get("contract_type"), "activation_date": date, "store_code": store_code}
        cur = existing.pop(ln["mdn"], None)
        if cur:
            patch = {k: v for k, v in want.items() if v not in (None, "") and cur.get(k) in (None, "")}
            if cur.get("customer_id") != customer_id:
                patch["customer_id"] = customer_id
            if patch:
                patch["updated_at"] = _now()
                _pos(client, "activations").update(patch).eq("org_id", org_id).eq("id", cur["id"]).execute()
                res["updated"] += 1
            else:
                res["unchanged"] += 1
            continue
        ins = {"org_id": org_id, "customer_id": customer_id, "sale_id": sale_id, "cell_number": ln["mdn"], "mobile_phone": ln["mdn"],
               "employee_id": employee_id, "status": "active",
               "memo": f"from the sales reports — invoice {invoice_no}" if invoice_no else "from the sales reports",
               **{k: v for k, v in want.items() if v not in (None, "")}}
        _pos(client, "activations").insert(ins).execute()
        res["written"] += 1
    res["kept"] = len(existing)
    unpaired = [ln["mdn"] for ln in lines if not ln.get("imei")]
    if unpaired:
        res["words"].append(f"{len(unpaired)} phone line(s) carry no device: " + "; ".join(
            f"{ln['mdn']} — {ln.get('reason') or 'no device on the invoice'}" for ln in lines if not ln.get("imei")))
    if res["kept"]:
        res["words"].append(f"{res['kept']} phone line(s) recorded on this sale are no longer on the invoice — kept (they may carry notes)")
    return res


# ── merge / un-merge ───────────────────────────────────────────────────────────────────────────────
def _customer(client, org_id, cid, extra=()):
    cols = "id,first_name,last_name,company_name,cust_number,is_active,address_1,address_2,city,state,zip,created_at"
    cols += "".join("," + c for c in extra)
    rows = (_pos(client, "customers").select(cols).eq("org_id", org_id).eq("id", cid).limit(1).execute().data) or []
    return rows[0] if rows else None


def _ids_linked(client, org_id, table, cid):
    return [r["id"] for r in (_pos(client, table).select("id").eq("org_id", org_id).eq("customer_id", cid)
                              .limit(100000).execute().data) or [] if r.get("id")]


def _repoint(client, org_id, table, ids, to):
    for chunk in in_chunks(ids):
        _pos(client, table).update({"customer_id": to}).eq("org_id", org_id).in_("id", chunk).execute()


def _note(client, org_id, cid, text, who):
    try:
        _pos(client, "customer_notes").insert({"org_id": org_id, "customer_id": cid, "note": text, "severity": "important",
                                               "employee_id": who or None}).execute()
    except Exception:
        pass


def _move_links(client, org_id, from_id, to_id, schema):
    moved, skipped = {}, []
    for table in CUSTOMER_LINKS:
        if table == "customer_aliases" and not schema["aliases"]:
            continue
        try:
            ids = _ids_linked(client, org_id, table, from_id)
            if table == "customer_aliases" and ids:       # UNIQUE (org, customer, alias_norm): a name `into` already has stays put
                have = {r["alias_norm"] for r in aliases_of(client, org_id, [to_id]).get(to_id, [])}
                mine = aliases_of(client, org_id, [from_id]).get(from_id, [])
                ids = [r["id"] for r in mine if r.get("alias_norm") not in have]
            _repoint(client, org_id, table, ids, to_id)
            if ids:
                moved[table] = ids
        except Exception as e:           # a table this tenant does not have (e.g. an unapplied module) is skipped and SAID
            skipped.append(f"{table}: {str(e)[:120]}")
    return moved, skipped


def merge_customers(client, org_id, from_id, into_id, who=None, reason=None):
    """Combine two customers: everything that points at `from` (CUSTOMER_LINKS) is repointed to `into`, the
    from-customer's name becomes an alias of `into`, `from` is marked merged_into = into and inactive, the move
    list is kept on `from.merge_record` (so `unmerge` can put exactly those rows back), and a note on `into`
    records it. Needs migration 1017 — without it nothing is written and the answer says so."""
    schema = identity_schema(client, org_id)
    out = {"ok": False, "from": from_id, "into": into_id, "moved": {}, "words": []}
    if not schema["ready"]:
        out["words"].append(MIGRATION_WORDS)
        return out
    if not from_id or not into_id or from_id == into_id:
        out["words"].append("choose two different customers")
        return out
    src = _customer(client, org_id, from_id, ("merged_into",))
    dst = _customer(client, org_id, into_id, ("merged_into",))
    if not src or not dst:
        out["words"].append("customer not found in this company")
        return out
    if src.get("merged_into"):
        out["words"].append("this customer is already merged into another — un-merge it first")
        return out
    if dst.get("merged_into"):
        out["words"].append("the customer you chose was itself merged into another — merge into that one instead")
        return out
    moved, skipped = _move_links(client, org_id, from_id, into_id, schema)
    alias_id = None
    src_norm = _cid.norm_name(_cid.display_name(src))
    had = {r["alias_norm"] for r in aliases_of(client, org_id, [into_id]).get(into_id, [])}
    if src_norm and src_norm != _cid.norm_name(_cid.display_name(dst)) and src_norm not in had:   # the same / a known name needs no alias
        alias_id = add_alias(client, org_id, into_id, _cid.display_name(src), "merge", _s(src.get("created_at"))[:10] or None)
    rec = {"into": into_id, "at": _now(), "by": who, "reason": reason, "moved": moved, "was_active": bool(src.get("is_active", True)),
           "alias_added": alias_id or None, "skipped": skipped}
    _pos(client, "customers").update({"merged_into": into_id, "is_active": False, "merge_record": rec, "updated_at": _now()}) \
        .eq("org_id", org_id).eq("id", from_id).execute()
    n_sales = len(moved.get("sales", []))
    n_lines = len(moved.get("activations", []))
    _note(client, org_id, into_id, f"Merged customer #{src.get('cust_number')} '{_cid.display_name(src)}' into this record — "
          f"{n_sales} sale(s), {n_lines} phone line row(s) moved" + (f" — {reason}" if reason else "") + (f" (by {who})" if who else ""), who)
    out.update(ok=True, moved={k: len(v) for k, v in moved.items()}, alias_added=bool(alias_id),
               words=[f"'{_cid.display_name(src)}' merged into '{_cid.display_name(dst)}': {n_sales} sale(s), {n_lines} phone line row(s) moved; "
                      + (f"'{_cid.display_name(src)}' is kept as an also-known-as name" if alias_id else "the same name — no alias needed")]
               + [f"skipped: {x}" for x in skipped])
    return out


def detach_placeholder(client, org_id, cid, who=None, reason=None):
    """A placeholder customer ('Walk In' — `is_placeholder`) names nobody: its sales / receipts / lines are
    detached (customer cleared), it is marked inactive, and the detached ids are kept on `merge_record` so
    `unmerge` restores them. Needs migration 1017."""
    schema = identity_schema(client, org_id)
    if not schema["ready"]:
        return {"ok": False, "words": [MIGRATION_WORDS]}
    row = _customer(client, org_id, cid, ("merged_into",))
    if not row:
        return {"ok": False, "words": ["customer not found in this company"]}
    detached = {}
    for table in ("sales", "receipt_imports", "activations"):
        ids = _ids_linked(client, org_id, table, cid)
        _repoint(client, org_id, table, ids, None)
        if ids:
            detached[table] = ids
    rec = {"detached": detached, "at": _now(), "by": who, "reason": reason, "was_active": bool(row.get("is_active", True)), "moved": {}}
    _pos(client, "customers").update({"is_active": False, "merge_record": rec, "updated_at": _now()}).eq("org_id", org_id).eq("id", cid).execute()
    return {"ok": True, "detached": {k: len(v) for k, v in detached.items()},
            "words": [f"'{_cid.display_name(row)}' is a placeholder: {len(detached.get('sales', []))} sale(s) detached, the record deactivated"]}


def unmerge(client, org_id, cid, who=None, reason=None):
    """Undo a merge (or a placeholder detach) from the record kept on `merge_record`: exactly the rows that moved
    are pointed back at this customer (wherever they sit now), the alias the merge added is removed, the customer
    is active again as it was, merged_into cleared; a note on both says so. Needs migration 1017."""
    schema = identity_schema(client, org_id)
    if not schema["ready"]:
        return {"ok": False, "words": [MIGRATION_WORDS]}
    row = _customer(client, org_id, cid, ("merged_into", "merge_record"))
    rec = (row or {}).get("merge_record") or {}
    if not row or not (row.get("merged_into") or rec.get("detached")):
        return {"ok": False, "words": ["this customer is not merged into another — nothing to undo"]}
    back = {}
    for table, ids in (rec.get("moved") or {}).items():
        if table in CUSTOMER_LINKS:
            _repoint(client, org_id, table, ids, cid)
            back[table] = len(ids)
    for table, ids in (rec.get("detached") or {}).items():
        if table in CUSTOMER_LINKS:
            _repoint(client, org_id, table, ids, cid)
            back[table] = back.get(table, 0) + len(ids)
    if rec.get("alias_added") and schema["aliases"]:
        _pos(client, "customer_aliases").delete().eq("org_id", org_id).eq("id", rec["alias_added"]).execute()
    into = row.get("merged_into")
    _pos(client, "customers").update({"merged_into": None, "is_active": bool(rec.get("was_active", True)),
                                      "merge_record": {"undone": rec, "at": _now(), "by": who, "reason": reason}, "updated_at": _now()}) \
        .eq("org_id", org_id).eq("id", cid).execute()
    text = f"Un-merged customer #{row.get('cust_number')} '{_cid.display_name(row)}'" + (f" — {reason}" if reason else "") + (f" (by {who})" if who else "")
    _note(client, org_id, cid, text, who)
    if into:
        _note(client, org_id, into, text, who)
    return {"ok": True, "restored": back, "words": [f"'{_cid.display_name(row)}' is its own customer again: "
                                                   + (", ".join(f"{n} {t}" for t, n in back.items()) or "nothing had moved") + " put back"]}


# ── the one-time cleanup of existing duplicates (dry run first, the house counted-confirm) ───────────
def dedupe_plan(client, org_id):
    """Plan, write nothing: every EXACT normalised-name duplicate group → merge into the first created (unless two
    of them carry different addresses — maybe two people: listed, not planned); every placeholder customer
    ('Walk In') → detached. `count` is what the caller must confirm."""
    schema = identity_schema(client, org_id)
    cols = "id,first_name,last_name,company_name,cust_number,is_active,address_1,address_2,city,state,zip,created_at"
    if schema["merged_into"]:
        cols += ",merged_into"
    if schema["merge_record"]:
        cols += ",merge_record"
    rows = _page_all(lambda: _pos(client, "customers").select(cols).eq("org_id", org_id).order("id"))
    live = [r for r in rows if not r.get("merged_into")]
    placeholder_cfg = _identity_config(client, org_id)
    detach, groups, skipped = [], {}, []
    for r in live:
        name = _cid.display_name(r)
        if _cid.is_placeholder(name, placeholder_cfg):
            if not (r.get("merge_record") or {}).get("detached"):
                detach.append({"id": r["id"], "name": name, "cust_number": r.get("cust_number")})
            continue
        groups.setdefault(_cid.norm_name(name), []).append(r)
    merges = []
    for nn, grp in sorted(groups.items()):
        if len(grp) < 2:
            continue
        grp = sorted(grp, key=lambda r: (_s(r.get("created_at")), _s(r.get("cust_number")), _s(r.get("id"))))
        addrs = {_cid.norm_address(r) for r in grp} - {""}
        if len(addrs) > 1:
            skipped.append({"name": _cid.display_name(grp[0]), "customers": len(grp),
                            "why": "they carry different addresses — perhaps different people; merge them by hand if they are one"})
            continue
        for r in grp[1:]:
            merges.append({"from": r["id"], "from_number": r.get("cust_number"), "into": grp[0]["id"], "into_number": grp[0].get("cust_number"),
                           "name": _cid.display_name(r)})
    count = len(merges) + len(detach)
    words = [f"{len(merges)} duplicate customer record(s) to merge into {len({m['into'] for m in merges})} customer(s) with the same name",
             f"{len(detach)} placeholder customer record(s) (a bill-to that names nobody) to detach from their sales"]
    if skipped:
        words.append(f"{len(skipped)} same-name group(s) NOT planned — different addresses: " + "; ".join(f"{s['name']} ×{s['customers']}" for s in skipped[:10]))
    if not schema["ready"]:
        words.append(MIGRATION_WORDS)
    words.append(f"to apply, send this again with confirm_count = {count}" if count else "nothing to clean up")
    return {"merges": merges, "detach": detach, "skipped": skipped, "count": count, "ready": schema["ready"], "words": words}


def dedupe_apply(client, org_id, confirm_count, who=None, reason=None):
    """Re-plan, then apply ONLY when `confirm_count` equals the planned count (the plan can change between the dry
    run and the confirm — a different count is refused and the fresh plan returned)."""
    plan = dedupe_plan(client, org_id)
    if not plan["ready"]:
        return {**plan, "applied": False}
    if confirm_count is None or int(confirm_count) != plan["count"]:
        return {**plan, "applied": False, "words": [f"not applied: the plan now holds {plan['count']} change(s) and you confirmed {confirm_count} — "
                                                     "review the plan and confirm its count"] + plan["words"]}
    done, failed = [], []
    why = reason or "duplicate customer cleanup"
    for m in plan["merges"]:
        r = merge_customers(client, org_id, m["from"], m["into"], who=who, reason=why)
        (done if r["ok"] else failed).append({**m, "words": r["words"]})
    for d in plan["detach"]:
        r = detach_placeholder(client, org_id, d["id"], who=who, reason=why)
        (done if r["ok"] else failed).append({**d, "words": r["words"]})
    return {**plan, "applied": True, "done": len(done), "failed": failed,
            "words": [f"applied: {len(done)} of {plan['count']} change(s)" + (f", {len(failed)} failed" if failed else "")
                      + " — each can be undone with Un-merge on the customer"]}


def _identity_config(client, org_id):
    from app.modules.pos import receipt_import as _ri
    return _ri.identity_config(client, org_id)


# ── the customer page's payloads ─────────────────────────────────────────────────────────────────────
def _money(v):
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return None


def _doc_line_amount(document, keys):
    """Σ the printed totals of the receipt document's items whose tracking # is one of `keys` (the phone number
    or its device) — what the receipt shows for that line; None when the document names neither."""
    from app.modules.pos.receipt_formats import base as _b
    if not document:
        return None
    cols = document.get("columns") or []
    serial = [c["key"] for c in cols if c.get("kind") == _b.KIND_SERIAL]
    total = [c["key"] for c in cols if c.get("kind") == _b.KIND_TOTAL]
    hit, amt = False, 0.0
    for it in document.get("items") or []:
        cells = it.get("cells") or {}
        if any(_b.digits(cells.get(k)) in keys for k in serial if _b.digits(cells.get(k))):
            hit = True
            for k in total:
                amt += _b.money(cells.get(k)) or 0.0
    return round(amt, 2) if hit else None


def lines_payload(client, org_id, customer_id):
    """ONE ENTRY PER PHONE NUMBER the customer carries: the number, its device (latest IMEI + every IMEI seen), its
    plan, first / last date, and the dated sales on that line — each with its activation id (edit it; its notes),
    the invoice #, the receipt import id (print it), the sale total and what the receipt prints for that line."""
    acts = (_pos(client, "activations").select("id,sale_id,cell_number,phone_serial,phone_model,plan_description,activation_date,"
                                               "contract_type,status,store_code,memo,created_at")
            .eq("org_id", org_id).eq("customer_id", customer_id).limit(5000).execute().data) or []
    sale_ids = sorted({a["sale_id"] for a in acts if a.get("sale_id")})
    ri_by_sale, sale_by_id = {}, {}
    for chunk in in_chunks(sale_ids):
        for r in (_pos(client, "receipt_imports").select("id,sale_id,invoice_no,sale_date,document,status").eq("org_id", org_id)
                  .in_("sale_id", chunk).execute().data) or []:
            if r.get("status") != "voided":
                ri_by_sale.setdefault(r["sale_id"], r)
        for s in (_pos(client, "sales").select("id,transaction_id,total,status,created_at,receipt").eq("org_id", org_id)
                  .in_("id", chunk).execute().data) or []:
            sale_by_id[s["id"]] = s
    note_count = {}
    for chunk in in_chunks([a["id"] for a in acts]):
        for n in (_pos(client, "activation_notes").select("activation_id").eq("org_id", org_id).in_("activation_id", chunk)
                  .limit(10000).execute().data) or []:
            note_count[n["activation_id"]] = note_count.get(n["activation_id"], 0) + 1
    by_mdn = {}
    for a in acts:
        mdn = _cid.norm_phone(a.get("cell_number")) or _s(a.get("cell_number"))
        ri = ri_by_sale.get(a.get("sale_id")) or {}
        sale = sale_by_id.get(a.get("sale_id")) or {}
        date = _s(a.get("activation_date"))[:10] or _s(ri.get("sale_date"))[:10] or _s(sale.get("created_at"))[:10] or None
        keys = {mdn} | ({_s(a.get("phone_serial"))} if _s(a.get("phone_serial")) else set())
        by_mdn.setdefault(mdn, []).append({
            "activation_id": a["id"], "sale_id": a.get("sale_id"), "date": date,
            "invoice_no": ri.get("invoice_no") or ((sale.get("receipt") or {}).get("invoice_no")) or sale.get("transaction_id"),
            "receipt_import_id": ri.get("id"), "sale_total": _money(sale.get("total")),
            "line_amount": _doc_line_amount(ri.get("document"), keys), "imei": a.get("phone_serial"), "device": a.get("phone_model"),
            "plan": a.get("plan_description"), "contract_type": a.get("contract_type"), "status": a.get("status"),
            "store_code": a.get("store_code"), "memo": a.get("memo"), "notes": note_count.get(a["id"], 0),
            "voided": sale.get("status") == "voided"})
    out = []
    for mdn, sales in by_mdn.items():
        sales.sort(key=lambda s: (_s(s["date"]), _s(s["invoice_no"])), reverse=True)
        imeis = []
        for s in sales:
            if _s(s["imei"]) and s["imei"] not in imeis:
                imeis.append(s["imei"])
        plans = []
        for s in sales:
            if _s(s["plan"]) and s["plan"] not in plans:
                plans.append(s["plan"])
        dates = sorted(_s(s["date"]) for s in sales if _s(s["date"]))
        out.append({"mdn": mdn, "imei": imeis[0] if imeis else None, "imeis": imeis, "plan": plans[0] if plans else None, "plans": plans,
                    "device": next((s["device"] for s in sales if _s(s["device"])), None),
                    "status": sales[0]["status"], "first_date": dates[0] if dates else None, "last_date": dates[-1] if dates else None,
                    "sales": sales, "notes": sum(s["notes"] for s in sales)})
    out.sort(key=lambda ln: (_s(ln["last_date"]), ln["mdn"]), reverse=True)
    n_sales = sum(len(ln["sales"]) for ln in out)
    words = [f"{len(out)} phone line(s), {n_sales} dated sale(s) on them"] if out else \
        ["no phone lines yet — a line appears when a sale carrying a phone number is rebuilt from the sales reports, or is added under Activations"]
    return {"customer_id": customer_id, "lines": out, "words": words}


def invoices_payload(client, org_id, customer_id):
    """PER INVOICE what the customer paid: date, invoice #, the payment lines and their sum (a rebuilt receipt's
    payment lines are the tenders `closing.router.is_customer_payment` calls the customer's — a vendor rebate is
    not one; a register sale's are its `pos.sale_payments`), the total, the balance, the receipt to print."""
    sales = (_pos(client, "sales").select("id,transaction_id,store_code,total,subtotal,tax_total,status,created_at,receipt,source")
             .eq("org_id", org_id).eq("customer_id", customer_id).order("created_at", desc=True).limit(2000).execute().data) or []
    ids = [s["id"] for s in sales]
    ri_by_sale, pay_by_sale = {}, {}
    for chunk in in_chunks(ids):
        for r in (_pos(client, "receipt_imports").select("id,sale_id,invoice_no,sale_date,status").eq("org_id", org_id)
                  .in_("sale_id", chunk).execute().data) or []:
            if r.get("status") != "voided":
                ri_by_sale.setdefault(r["sale_id"], r)
        try:
            for p in (_pos(client, "sale_payments").select("sale_id,payment_method,amount").eq("org_id", org_id)
                      .in_("sale_id", chunk).execute().data) or []:
                pay_by_sale.setdefault(p["sale_id"], []).append({"label": p.get("payment_method"), "amount": _money(p.get("amount"))})
        except Exception:                # a tenant without the register's payments table: the receipt's own lines only
            pass
    out = []
    for s in sales:
        rec = s.get("receipt") or {}
        ri = ri_by_sale.get(s["id"]) or {}
        pays = rec.get("payments") if isinstance(rec.get("payments"), list) else pay_by_sale.get(s["id"], [])
        pays = [{"label": _s(p.get("label")), "amount": _money(p.get("amount")) or 0.0} for p in pays or []]
        paid = round(sum(p["amount"] for p in pays), 2)
        total = _money(s.get("total")) or 0.0
        out.append({"sale_id": s["id"], "date": _s(rec.get("sale_date"))[:10] or _s(ri.get("sale_date"))[:10] or _s(s.get("created_at"))[:10] or None,
                    "invoice_no": ri.get("invoice_no") or rec.get("invoice_no") or s.get("transaction_id"), "transaction_id": s.get("transaction_id"),
                    "receipt_import_id": ri.get("id"), "store_code": s.get("store_code"), "total": total, "paid": paid,
                    "balance": round(total - paid, 2), "payments": pays, "status": s.get("status"), "source": s.get("source")})
    out.sort(key=lambda r: (_s(r["date"]), _s(r["invoice_no"])), reverse=True)
    live = [r for r in out if r["status"] != "voided"]
    words = [f"{len(live)} invoice(s); the customer paid {sum(r['paid'] for r in live):,.2f} of {sum(r['total'] for r in live):,.2f}"] if live else ["no invoices yet"]
    return {"customer_id": customer_id, "invoices": out, "words": words}


def aliases_payload(client, org_id, customer_id):
    schema = identity_schema(client, org_id)
    rows = aliases_of(client, org_id, [customer_id]).get(customer_id, [])
    merged_from = []
    if schema["merged_into"]:
        merged_from = (_pos(client, "customers").select("id,first_name,last_name,company_name,cust_number")
                       .eq("org_id", org_id).eq("merged_into", customer_id).limit(500).execute().data) or []
    row = _customer(client, org_id, customer_id, ("merged_into",) if schema["merged_into"] else ())
    into = None
    if row and row.get("merged_into"):
        into = _customer(client, org_id, row["merged_into"])
    return {"customer_id": customer_id, "ready": schema["ready"], "words": [schema["words"]] if schema["words"] else [],
            "aliases": sorted(rows, key=lambda r: (_s(r.get("last_seen")), _s(r.get("alias_name"))), reverse=True),
            "merged_from": [{"id": m["id"], "name": _cid.display_name(m), "cust_number": m.get("cust_number")} for m in merged_from],
            "merged_into": ({"id": into["id"], "name": _cid.display_name(into), "cust_number": into.get("cust_number")} if into else None)}


# ── the search the POS sale screen and the customer page use ──────────────────────────────────────────
SEARCH_COLUMNS = ("first_name", "last_name", "company_name", "phone_primary", "phone_secondary", "email", "primary_account_no")


def search_ids(client, org_id, text, limit=300):
    """The customer ids a search box's text finds: any one column containing the text (as before); a FULL name
    ('Jane Doe' → first contains 'Jane' AND last contains 'Doe', either order); a phone number on a customer's
    LINES (`pos.activations.cell_number`); an also-known-as name (mig 1017); the customer number. Each query is
    one column (no filter-string composition), org-scoped."""
    s = _s(text).replace("%", "").replace(",", " ")
    if not s:
        return None
    ids = []

    def add(rows, key="id"):
        for r in rows or []:
            v = r.get(key)
            if v and v not in ids:
                ids.append(v)

    tbl = lambda: _pos(client, "customers")      # noqa: E731 — a builder is single-use
    for col in SEARCH_COLUMNS:
        add(tbl().select("id").eq("org_id", org_id).ilike(col, f"%{s}%").limit(limit).execute().data)
    words = s.split()
    if len(words) > 1:
        first, rest = words[0], " ".join(words[1:])
        for a, b in ((first, rest), (rest, first), (" ".join(words[:-1]), words[-1])):
            add(tbl().select("id").eq("org_id", org_id).ilike("first_name", f"%{a}%").ilike("last_name", f"%{b}%").limit(limit).execute().data)
    digits = re.sub(r"\D", "", s)
    if len(digits) >= 4 and not re.search(r"[A-Za-z]", s):                  # a phone number (or part of one) on a LINE
        q = _cid.norm_phone(digits) or digits
        add(_pos(client, "activations").select("customer_id").eq("org_id", org_id).ilike("cell_number", f"%{q}%")
            .limit(limit).execute().data, "customer_id")
    if s.isdigit():
        add(tbl().select("id").eq("org_id", org_id).eq("cust_number", int(s)).limit(5).execute().data)
    nn = _cid.norm_name(s)
    if nn and identity_schema(client, org_id)["aliases"]:
        add(_pos(client, "customer_aliases").select("customer_id").eq("org_id", org_id).ilike("alias_norm", f"%{nn}%")
            .limit(limit).execute().data, "customer_id")
    return ids
