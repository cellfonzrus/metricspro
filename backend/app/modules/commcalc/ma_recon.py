"""B2B ↔ MA Commission / MA TX activation reconciliation → the discrepancy report (Phase C).

OWNER SPEC (2026-09-01): "MA TX can also be used to verify the sales ingested via the B2B for
verification and reconciliation purposes. If an activation has been rung out in B2B but not paid in
MA Commission / MA TX then it should fall into the discrepancy report for that month, and full
analysis to be attributed to why it did not get paid as per the business rules — if business rules
are not present then they should be uploaded; if the activation is still not paid but no business
rules exist it should still appear in the report without a reason."

HOW IT WORKS. Sold side vs paid side, presence-based (did the MA ever pay this activation?), with the
two-hop join the MA TX formula already uses — every join/month primitive is REUSED from
sale_installment_engine / commission_ledger, never re-implemented:

  SOLD (B2B)            raw_sales ∪ daily_sales_feed (data_lineage_registry.SALES_DISPLAY_SOURCES)
     │  serial_1, digit-normalized (_norm_imei)
  PAID hop 1            raw_ma_commission.imei|sim  →  activation_order   (build_ma_link_index)
  PAID hop 2            raw_ma_commission.activation_order ↔ raw_ma_daily_tx.order_number
                        ('MONTH n' wording via commission_ledger.parse_payment_month inside
                         build_ma_tx_index; the activation-order row itself is month-1 evidence)

  Paid test = _gate_met_ma_tx(sale, spiff_index, tx_indexes, month=1, cfg): the UNION of the
  ma_commission spiff/rebate month-1 evidence and the MA TX month-1 evidence, direction-aware
  (ma_payout_sign / ma_min_amount from installment_gate_source_config — mig 223/308 ladder).

SOLD-SIDE BASIS (the ONE consistent definition, documented here and in the harness):
  An ACTIVATION line is a SALES_DISPLAY_SOURCES row (daily_sales_feed ∪ raw_sales, deduped by
  digit-normalized serial_1) with a NON-BLANK contract_type whose label does not contain 'swap'
  (mirrors discrepancy_engine.ACTIVATION_TYPE_MAP, where 'swap'/'byod swap' are the only 'excluded'
  entries) and that is not VOIDED (gp_report.is_voided — the shared void-token source). Rows with no
  normalizable serial cannot join the MA feeds and are counted in the summary as
  `sold_without_serial`, never silently dropped. This is the discrepancy_engine basis (contract_type
  semantics over the sales rows), NOT the Activation-Details custom-report basis metric_recon uses —
  chosen because this recon attributes INDIVIDUAL sold lines (a row-level join needs the sales row's
  own columns for rule matching), and so the two discrepancy writers share one sold universe.

STATUSES (evidence-first, never guessed):
  'ok'     paid in MA Commission or MA TX (evidence says which). Not persisted.
  'open'   sold, unpaid, NO business rule matched — notes are LITERALLY 'no business rule configured'
           (the mig-254 'unmapped' idiom: absence of a rule is itself reported, not papered over).
  'info'   sold, unpaid, a rule matched (expected_outcome 'not_paid' or 'partial') — rule_key +
           description attached.
  'lagged' sold, unpaid, a rule matched with expected_outcome 'paid_late' (the report's existing
           "awaiting a later statement" tab).

Everything above the LOADERS section is PURE (rows/config in, rows out) — no DB, no framework — so
harness_ma_recon.py proves the business rules without a database. Loaders take a `client` param
(sale_installment_engine style); nothing here imports the DB at module import time.
"""
import calendar
import re

from app.modules.commcalc.calculator import safe_float
from app.modules.commcalc.gp_report import is_voided as _is_voided
from app.modules.commcalc.installment_engine import _pvariants, _shift_period
# The join/evidence primitives — REUSED from the mig-308 MA TX engine, never re-implemented:
from app.modules.commcalc.sale_installment_engine import (
    _norm_imei, _ma_gate_index, _gate_met_ma_tx, build_ma_link_index, build_ma_tx_index,
    _read_ma_commission, _read_ma_tx, _load_gate_source_rows, _resolve_gate_cfg,
)
from app.modules.commcalc.data_lineage_registry import SALES_DISPLAY_SOURCES

HOUSE_ORG = "00000000-0000-0000-0000-000000000001"

# The literal no-rule reason (owner spec: "…it should still appear in the report without a reason").
NO_RULE_REASON = "no business rule configured"
# comp_type marker on every row this engine writes — doubles as the delete-scope fallback when the
# mig-312 `source` column is absent, so the Boost engine's comp types are NEVER touched.
MA_COMP_TYPE = "MA_ACTIVATION"
SOURCE_MA = "ma"

# Sold-line columns a rule may test (mirrors the mig-312 match_field CHECK).
RULE_MATCH_FIELDS = ("product_desc", "department", "category", "contract_type", "sku", "plan")
RULE_MATCH_OPS = ("contains", "equals", "prefix", "regex")
# expected_outcome → report status for an unpaid-but-explained activation.
OUTCOME_STATUS = {"not_paid": "info", "paid_late": "lagged", "partial": "info"}

# The mig-312 attribution columns, in the order the ADAPTIVE persist drops them when the migration
# has not run (narrow insert = the pre-312 column set, exactly what the Boost engine writes).
ATTRIBUTION_COLUMNS = ("rule_id", "rule_key", "rule_reason", "evidence", "source", "order_number")


# ── PURE: period canonicalization ──────────────────────────────────────────────────────────────────
def canonical_period(period):
    """'2026-08' or 'august 2026' → the canonical month-name label 'August 2026'.

    STRICT — run_ma_discrepancy normalizes its period through this BEFORE touching the shared
    period helpers, because installment_engine's _pvariants/_shift_period lean on parse_period,
    which leniently maps an unrecognized spelling to JANUARY ('2026-08' → 'January 2026' variants —
    the exact trap router._pvariants documents). POST /discrepancy/run sends 'YYYY-MM', so without
    this the recon would read January's MA statements for an August run. Unparseable input passes
    through unchanged (never guesses a month). PURE."""
    p = str(period or "").strip()
    if len(p) >= 7 and p[:4].isdigit() and p[4] == "-" and p[5:7].isdigit():
        y, m = int(p[:4]), int(p[5:7])
        if 1 <= m <= 12:
            return f"{calendar.month_name[m]} {y}"
        return p
    parts = p.split()
    names = {n.lower(): i for i, n in enumerate(calendar.month_name) if n}
    if len(parts) == 2 and parts[0].lower() in names and parts[1].isdigit():
        return f"{calendar.month_name[names[parts[0].lower()]]} {parts[1]}"
    return p


# ── PURE: sold side ────────────────────────────────────────────────────────────────────────────────
def _is_activation_line(row):
    """The sold-side basis test (see module docstring): non-blank contract_type, not a swap, not
    voided. PURE."""
    ct = str((row or {}).get("contract_type") or "").strip()
    if not ct:
        return False
    if "swap" in ct.lower():
        return False
    if _is_voided((row or {}).get("voided")):
        return False
    return True


def _sort_key(row):
    """Deterministic representative-row order: earliest trans_date, then trans_id (as text)."""
    return (str(row.get("trans_date") or "9999-99-99"), str(row.get("trans_id") or ""))


def build_sold_index(sales_rows):
    """{normalized serial → representative activation sale row} over the SALES_DISPLAY_SOURCES rows.

    Applies the documented basis (_is_activation_line), digit-normalizes serial_1 (_norm_imei — the
    SAME normalization the MA join uses, so both sides key identically), and dedupes to ONE row per
    device (earliest trans_date/trans_id wins — a re-ring never doubles the expectation). Returns
    (index, sold_without_serial) where the second element counts activation lines with no
    normalizable serial — they cannot join the MA feeds and are surfaced in the summary, never
    silently dropped. Never raises. PURE."""
    idx, no_serial = {}, 0
    for r in (sales_rows or []):
        if not _is_activation_line(r):
            continue
        k = _norm_imei(r.get("serial_1"))
        if not k:
            no_serial += 1
            continue
        cur = idx.get(k)
        if cur is None or _sort_key(r) < _sort_key(cur):
            idx[k] = r
    return idx, no_serial


# ── PURE: paid side ────────────────────────────────────────────────────────────────────────────────
def build_paid_index(ma_rows, tx_rows, cfg):
    """All three paid-side indexes from one pass over the MA feeds, entirely via the mig-308
    primitives: {'spiff': _ma_gate_index (ma_commission netted month columns), 'link':
    build_ma_link_index (hop 1, serial→activation_order), 'tx': build_ma_tx_index (hop 2,
    order_number → activation row + MONTH-n nets, month wording via commission_ledger.
    parse_payment_month inside)}. PURE; never raises."""
    return {"spiff": _ma_gate_index(ma_rows or []),
            "link": build_ma_link_index(ma_rows or []),
            "tx": build_ma_tx_index(tx_rows or [], cfg or {})}


# ── PURE: business-rule matching ───────────────────────────────────────────────────────────────────
def _rule_in_window(rule, trans_date):
    """effective_from/to window test against the sale's trans_date (YYYY-MM-DD prefix compare — ISO
    strings order correctly). Missing bounds are open; an unparseable trans_date only fails a rule
    that actually sets a bound (honest: a windowed rule needs a date to prove it applies)."""
    frm = str(rule.get("effective_from") or "").strip()[:10]
    to = str(rule.get("effective_to") or "").strip()[:10]
    if not frm and not to:
        return True
    d = str(trans_date or "").strip()[:10]
    if len(d) != 10:
        return False
    if frm and d < frm:
        return False
    if to and d > to:
        return False
    return True


def _rule_matches_value(op, pattern, value):
    """One case/trim-insensitive match test; a bad regex degrades to NO match (never raises)."""
    v = str(value or "").strip().lower()
    p = str(pattern or "").strip().lower()
    if not p:
        return False
    if op == "equals":
        return v == p
    if op == "prefix":
        return v.startswith(p)
    if op == "regex":
        try:
            return re.search(str(pattern or "").strip(), str(value or "").strip(), re.I) is not None
        except re.error:
            return False        # guarded: a broken rule is skipped, the recon never crashes
    return p in v               # 'contains' (default, and any unknown op degrades to it)


def match_rules(sale_row, rules):
    """FIRST business rule that explains this sold line, or None.

    Rules are walked in ascending (priority, rule_key) order — first match wins (the mig-207
    report_pull_map / mig-071 category-map posture). Only is_active rules inside their
    effective_from/to window are considered; matching is case/trim-insensitive on the rule's
    match_field over the SALE row's own column. A regex rule with a broken pattern is skipped
    (guarded in _rule_matches_value), never crashes. PURE."""
    sale_row = sale_row or {}
    ordered = sorted((r for r in (rules or []) if r),
                     key=lambda r: (int(r.get("priority") if r.get("priority") is not None else 100),
                                    str(r.get("rule_key") or "")))
    for rule in ordered:
        if rule.get("is_active") is False:
            continue
        field = str(rule.get("match_field") or "").strip()
        if field not in RULE_MATCH_FIELDS:
            continue
        if not _rule_in_window(rule, sale_row.get("trans_date")):
            continue
        op = str(rule.get("match_op") or "contains").strip().lower()
        if _rule_matches_value(op, rule.get("match_value"), sale_row.get(field)):
            return rule
    return None


# ── PURE: the reconciliation itself ────────────────────────────────────────────────────────────────
def _evidence_for(sale, serial, paid_idx, gate_ev):
    """EVIDENCE dict naming exactly which source had / lacked this activation — never a guessed
    reason. b2b: the sold transaction; ma_commission: hop-1 linkage + the netted month-1 columns;
    ma_tx: hop-2 order + month-1 net / activation-order sighting. PURE."""
    ev = gate_ev or {}
    inner = ev.get("evidence") or {}
    tx_ev = inner.get("ma_tx") or {}
    orders = (paid_idx or {}).get("link", {}).get(serial, [])
    return {
        "b2b": {"trans_id": str(sale.get("trans_id") or "") or None,
                "trans_date": str(sale.get("trans_date") or "")[:10] or None,
                "source_table": sale.get("_source_table") or None},
        "ma_commission": {"matched": serial in (paid_idx or {}).get("spiff", {}),
                          "imei": serial,
                          "activation_orders": orders[:8],
                          "month1_columns": inner.get("ma_commission")},
        "ma_tx": {"matched": bool(orders and any(o in (paid_idx or {}).get("tx", {}) for o in orders)),
                  "order_number": ev.get("order_number"),
                  "month_net": tx_ev.get("month_net") if isinstance(tx_ev, dict) else None,
                  "activation_order_seen": tx_ev.get("activation_order_seen")
                  if isinstance(tx_ev, dict) else None},
        "gate_reason": ev.get("reason"),
    }


def reconcile_ma_activations(sold_idx, paid_idx, rules, period, cfg=None):
    """The Phase-C reconciliation: every sold activation (sold_idx from build_sold_index) is tested
    against the MA paid evidence (paid_idx from build_paid_index) with the mig-308 union gate at
    month 1; unpaid ones are attributed through the business rules (match_rules).

    Returns (rows, summary). Each row is discrepancy_results-shaped (minus org_id — the persist
    stamps it) with the mig-312 attribution columns; statuses per the module docstring. 'ok' rows are
    returned (so a caller can display full coverage) but persist_results drops them, mirroring the
    Boost engine. Deterministic: devices walked in sorted serial order. PURE."""
    cfg = cfg or {}
    rows = []
    n_ok = n_open = n_info = n_lagged = 0
    tx_indexes = {"link": (paid_idx or {}).get("link", {}), "tx": (paid_idx or {}).get("tx", {})}
    spiff_idx = (paid_idx or {}).get("spiff", {})
    for serial in sorted(sold_idx or {}):
        sale = sold_idx[serial] or {}
        met, gate_ev = _gate_met_ma_tx(sale, spiff_idx, tx_indexes, 1, cfg)
        evidence = _evidence_for(sale, serial, paid_idx, gate_ev)
        rule = None
        if met:
            status, notes = "ok", "paid"
            n_ok += 1
        else:
            rule = match_rules(sale, rules)
            if rule is None:
                status, notes = "open", NO_RULE_REASON
                n_open += 1
            else:
                outcome = str(rule.get("expected_outcome") or "not_paid").strip().lower()
                status = OUTCOME_STATUS.get(outcome, "info")
                notes = str(rule.get("description") or "").strip() or str(rule.get("rule_key") or "")
                if status == "lagged":
                    n_lagged += 1
                else:
                    n_info += 1
        # received: the month-1 MA TX net in the payout direction, when the evidence carries one.
        tx_net = safe_float((evidence.get("ma_tx") or {}).get("month_net"))
        rows.append({
            "period": period,
            "imei": serial,
            "mdn": str(sale.get("mdn") or "").strip(),
            "store": str(sale.get("store") or "").strip() or "Unknown",
            "rep_username": str(sale.get("salesperson") or sale.get("user_login") or "").strip(),
            "activation_date": (str(sale.get("trans_date") or "")[:10] or None),
            "activation_type": str(sale.get("contract_type") or "").strip(),
            "device_model": str(sale.get("product_desc") or "")[:200],
            "customer_plan": str(sale.get("plan") or sale.get("product_desc") or "")[:200],
            "commissionable_mrc": 0.0,
            "bounty_month": 1,
            "comp_type": MA_COMP_TYPE,
            "expected_amount": 0.0,
            "received_amount": round(abs(tx_net), 2) if met else 0.0,
            "gap": 0.0,
            "status": status,
            "notes": notes,
            # mig-312 attribution (adaptive persist drops these when the migration hasn't run):
            "rule_id": (rule or {}).get("id"),
            "rule_key": (rule or {}).get("rule_key"),
            "rule_reason": (str((rule or {}).get("description") or "").strip() or None) if rule else None,
            "evidence": evidence,
            "source": SOURCE_MA,
            "order_number": (gate_ev or {}).get("order_number"),
        })
    summary = {"sold_activations": len(sold_idx or {}), "paid_ok": n_ok, "open_no_rule": n_open,
               "explained_info": n_info, "explained_lagged": n_lagged}
    return rows, summary


def persist_payload(rows, org_id, wide=True):
    """The insert dicts for discrepancy_results: NON-'ok' rows only (mirrors the Boost engine —
    the report holds discrepancies, not the paid population), each stamped with org_id. wide=True
    includes the mig-312 attribution columns; wide=False is the ADAPTIVE fallback (the pre-312
    column set) for a database where migration 312 has not run. PURE."""
    out = []
    for r in (rows or []):
        if r.get("status") == "ok":
            continue
        row = dict(r)
        row["org_id"] = org_id
        if not wide:
            for c in ATTRIBUTION_COLUMNS:
                row.pop(c, None)
        out.append(row)
    return out


# ── LOADERS (DB; org-scoped; adaptive) ─────────────────────────────────────────────────────────────
def load_rules(client, org_id):
    """Active ma_payment_rule rows: the org's own plus (for a tenant) the house-org defaults — the
    mig-223 inherit posture. Precedence is the rules' own `priority` (match_rules sorts on it), the
    ladder's contract: a tenant overrides a house default by giving its rule a lower priority
    number. Missing table (mig 312 unrun) ⇒ [] — the recon still runs and every unpaid row reports
    'no business rule configured'."""
    def _read(oid):
        try:
            return (client.schema("commcalc").table("ma_payment_rule").select("*")
                    .eq("org_id", oid).eq("is_active", True).execute().data) or []
        except Exception:
            return []
    rules = _read(org_id)
    if str(org_id) != HOUSE_ORG:
        rules += _read(HOUSE_ORG)
    return rules


def load_sold_sales(client, org_id, period):
    """The sold-side rows for the period from data_lineage_registry.SALES_DISPLAY_SOURCES
    (daily_sales_feed ∪ raw_sales — the registry's display union, NEVER a hardcoded table name).
    Paginated; period matched on both spellings (_pvariants); each row tagged '_source_table' for
    the evidence dict. A missing table degrades to []. Basis filtering happens in build_sold_index
    (pure), not here, so the harness proves the basis without a database."""
    out = []
    for table in SALES_DISPLAY_SOURCES:
        start, page = 0, 1000
        while True:
            try:
                rows = (client.schema("commcalc").table(table).select("*")
                        .eq("org_id", org_id).in_("period", _pvariants(period))
                        .range(start, start + page - 1).execute().data) or []
            except Exception:
                break
            for r in rows:
                r["_source_table"] = table
            out.extend(rows)
            if len(rows) < page:
                break
            start += page
    return out


def load_ma_paid_rows(client, org_id, period):
    """(ma_commission_rows, ma_tx_rows) for the period PLUS the following month (+1 LOOKAHEAD: MA
    statements routinely post an activation's payout in the next month's file; feeding both months
    into ONE index keeps the mig-308 base+adjustment netting intact, and a genuinely-unpaid
    activation still reports because the lookahead only ADDS evidence, never removes it). Reuses the
    engine's paginated, absent-table-safe readers."""
    ma_rows = list(_read_ma_commission(client, org_id, period))
    tx_rows = list(_read_ma_tx(client, org_id, period))
    nxt = _shift_period(period, 1)
    if nxt:
        ma_rows += _read_ma_commission(client, org_id, nxt)
        tx_rows += _read_ma_tx(client, org_id, nxt)
    return ma_rows, tx_rows


def load_gate_cfg(client, org_id):
    """The plan-mode gate config through the mig-223 ladder (org-carrier → org-default → house →
    code), UNCHANGED — the same knobs (ma_min_amount, ma_payout_sign, ma_month_field_prefix,
    ma_month1_extra_fields, ma_tx_activation_order_type) the installment gate resolves, so the
    discrepancy report and the payout gate can never disagree about what 'paid' means."""
    org_rows, house_rows = _load_gate_source_rows(client, org_id)
    return _resolve_gate_cfg(org_rows, house_rows, None, "plan")


def persist_results(client, org_id, period, rows):
    """Delete-then-insert THIS ENGINE'S slice of discrepancy_results: scoped to (org_id, period
    spellings, source='ma') so the Boost engine's rows are never touched. ADAPTIVE both ways:
      • delete: if the mig-312 `source` column is absent the delete falls back to scoping by
        comp_type='MA_ACTIVATION' — a value only this engine writes, so Boost rows stay untouched.
      • insert: tries the wide (attribution) payload first; on failure retries the narrow pre-312
        column set (persist_payload(wide=False)), exactly the mig-308 adaptive-write posture.
    Returns {'saved': n, 'wide': bool}."""
    variants = _pvariants(period)

    def _delete_slice():
        try:
            (client.schema("commcalc").table("discrepancy_results").delete()
             .eq("org_id", org_id).in_("period", variants).eq("source", SOURCE_MA).execute())
        except Exception:
            (client.schema("commcalc").table("discrepancy_results").delete()
             .eq("org_id", org_id).in_("period", variants).eq("comp_type", MA_COMP_TYPE).execute())

    _delete_slice()
    wide_rows = persist_payload(rows, org_id, wide=True)
    BATCH = 500
    try:
        for i in range(0, len(wide_rows), BATCH):
            (client.schema("commcalc").table("discrepancy_results")
             .insert(wide_rows[i:i + BATCH]).execute())
        return {"saved": len(wide_rows), "wide": True}
    except Exception:
        # A wide insert can fail mid-batch on an unmigrated database: re-clear THIS engine's slice
        # so the narrow retry never duplicates the batches that did land.
        _delete_slice()
    narrow_rows = persist_payload(rows, org_id, wide=False)
    for i in range(0, len(narrow_rows), BATCH):
        (client.schema("commcalc").table("discrepancy_results")
         .insert(narrow_rows[i:i + BATCH]).execute())
    return {"saved": len(narrow_rows), "wide": False}


def run_ma_discrepancy(client, period, org_id):
    """Orchestrator: load → pure reconcile → persist. Returns the summary the /discrepancy/run
    response carries. Any single loader degrading (missing table) yields an honest partial result
    rather than a 500 — e.g. no MA data at all reports every sold activation as unpaid, which is the
    truthful reading of the evidence."""
    period = canonical_period(period)   # STRICT: 'YYYY-MM' → 'Month YYYY' (see canonical_period)
    cfg = load_gate_cfg(client, org_id)
    sold_rows = load_sold_sales(client, org_id, period)
    ma_rows, tx_rows = load_ma_paid_rows(client, org_id, period)
    rules = load_rules(client, org_id)
    sold_idx, no_serial = build_sold_index(sold_rows)
    paid_idx = build_paid_index(ma_rows, tx_rows, cfg)
    rows, summary = reconcile_ma_activations(sold_idx, paid_idx, rules, period, cfg)
    saved = persist_results(client, org_id, period, rows)
    summary.update({
        "period": period,
        "source": SOURCE_MA,
        "sold_without_serial": no_serial,
        "ma_commission_rows": len(ma_rows),
        "ma_tx_rows": len(tx_rows),
        "rules_loaded": len(rules),
        "rows_saved": saved.get("saved", 0),
        "attribution_columns_written": saved.get("wide", False),
    })
    return summary
