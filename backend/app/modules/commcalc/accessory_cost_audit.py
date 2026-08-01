"""ACCESSORY %-OF-GP COST AUDIT — read-only, zero-write, no recompute.

OWNER REPORT (luxelink, July 2026): "accessory % GP payouts are inconsistent" — a $24.99 screen
protector paid $0 while a $14.99 pair of headphones paid a number that made no sense.

THIS MODULE DOES NOT CHANGE WHAT ANYONE IS PAID. It answers three questions with the tenant's own
live data and the tenant's own live plan rules:

  Q1  WHICH lines does a %-of-basis rule actually pay on, and what are their money columns?
  Q2  WHICH of those lines have an UNBELIEVABLE cost, and what are the items? (`raw_sales` has no
      cost column — cost is implied: cost = ext_price - gp. See pay_data_quality.)
  Q3  What WOULD the period have paid under each of the owner's options, per rep, versus today?

SINGLE SOURCE OF TRUTH. `current` is not recomputed here — it is read out of
`commission_engine.preview(detail=True)`, the very function `_apply_new_engines` pays from, so the
"today" column can never drift from the money. The option columns are LINEAR re-bases of the same
per-line numbers, so they need no second engine either.

  Option A — fix the POS catalog cost.       An OWNER ACTION in the POS: this module cannot invent the
                                             corrected cost, so it reports the exact item list, the
                                             CURRENT implied cost, and — where `raw_catalog` carries a
                                             cost for the item — what GP and pay WOULD be with it.
                                             Items with no catalog cost are reported as UNKNOWN, never
                                             guessed.
  Option B — pay % of PRICE instead of GP.   basis = ext_price. Fully computable.
  Option C — guarded fallback (config).      Healthy lines keep %-of-GP untouched; only lines whose
                                             cost is suspect fall back to a basis the owner picks
                                             (% of price, or % of an assumed GP margin).
  Option R — the rate's UNIT.                `commission_rule.pct` is a FRACTION (0.10 = 10%) and the
                                             save path stores it verbatim. A rate typed as a whole
                                             percent pays 100x. Because every %-kind payout is linear
                                             in `pct`, "the same rules with the rate divided by 100"
                                             is exactly today's pay / 100 — no re-derivation.

ACCESSORY-DEFINITION ANNOTATION (mig 257, added 2026-08-01). Every matched line and every item also
carries the verdict of the tenant's own ACCESSORY DEFINITION (accessory_definition.py) — is this line
something the owner actually calls an accessory, and under which class. It is PURE ANNOTATION: no
option amount, no total, no delta and no existing key is derived from it, and the audit's arithmetic
is byte-identical with the annotation removed. It exists because this is the surface the owner already
opens to ask "why did that accessory pay that", and the first thing to check is whether the line is an
accessory at all.

MULTI-TENANT: org_id is the caller's on EVERY read. Nothing here is branched on a carrier or tenant
name; every threshold comes from `pay_data_quality` config (migration 255, degrading to defaults).
"""
from app.modules.commcalc import pay_data_quality as pdq
from app.modules.commcalc.pay_data_quality import _f

# The owner-facing option keys, in presentation order.
OPTION_KEYS = ("current", "option_a", "option_b", "option_c", "option_r")
OPTION_LABELS = {
    "current": "Today (as the engine pays now)",
    "option_a": "A — POS catalog cost corrected (only where a catalog cost exists)",
    "option_b": "B — pay % of PRICE instead of % of GP",
    "option_c": "C — guarded fallback (suspect lines only)",
    "option_r": "R — same rules, rate read as a percent (rate ÷ 100)",
}

# Option C's fallback basis. 'price' = pay the same rate on ext_price; 'assumed_gp' = pay the rate on
# (ext_price x assumed margin). The margin is the OPERATOR'S number — never a silent default.
C_BASES = ("price", "assumed_gp")


def _blank_totals():
    return {k: 0.0 for k in OPTION_KEYS}


def _line_options(ext_price, gp, amount, pct, kind, suspect, c_basis, assume_gp_pct,
                  catalog_cost=None):
    """The five option amounts for ONE matched line. PURE.

    `amount` is the LIVE per-line payout the engine produced (never recomputed here). Every option is
    a re-based version of the same rate, so each is `pct x <basis>` — except `option_r`, which is
    exactly `amount / 100` because every %-kind payout is linear in the rate.
    """
    ext, g, amt, p = _f(ext_price), _f(gp), _f(amount), _f(pct)
    out = {"current": round(amt, 2)}

    # A — the corrected-cost basis, ONLY when a real catalog cost exists for the item.
    # HONEST LIMIT: `raw_sales` carries no quantity column, so this treats the line as qty 1
    # (ext_price - catalog cost). For a multi-quantity accessory line the true corrected GP is
    # larger; the item table reports the implied cost range so a qty>1 line is visible.
    # `pct_price_over_cost` already pays off the catalog cost, so for that kind A == today by
    # construction — which is itself the answer: fixing the catalog is what moves it.
    if catalog_cost is None:
        out["option_a"] = None                      # unknown until the owner sets a cost — never guessed
    else:
        out["option_a"] = round(p * max(0.0, ext - _f(catalog_cost)), 2)

    # B — % of price.
    out["option_b"] = round(p * ext, 2)

    # C — healthy lines untouched; suspect lines re-based onto the operator's chosen fallback.
    if not suspect:
        out["option_c"] = round(amt, 2)
    elif c_basis == "assumed_gp":
        out["option_c"] = round(p * ext * _f(assume_gp_pct), 2)
    else:
        out["option_c"] = round(p * ext, 2)

    # R — the rate's unit. Linear in pct, so this is exact.
    out["option_r"] = round(amt / 100.0, 2)
    return out


def _catalog_index(client, org_id):
    """{normalized product key -> cost} from commcalc.raw_catalog for THIS org. Keyed by SKU and by
    normalized product description so a POS line can be looked up either way. Missing table -> {}."""
    by_sku, by_desc, by_pid = {}, {}, {}
    try:
        rows = (client.schema("commcalc").table("raw_catalog")
                .select("product_id,product_desc,cost,sku").eq("org_id", org_id)
                .limit(100000).execute().data) or []
    except Exception:
        return {"sku": {}, "desc": {}, "product_id": {}, "rows": 0}
    for r in rows:
        cost = r.get("cost")
        if cost is None:
            continue
        s = str(r.get("sku") or "").strip().upper()
        if s and s not in by_sku:
            by_sku[s] = _f(cost)
        d = " ".join(str(r.get("product_desc") or "").strip().upper().split())
        if d and d not in by_desc:
            by_desc[d] = _f(cost)
        pid = r.get("product_id")
        if pid is not None:
            try:
                by_pid.setdefault(float(pid), _f(cost))
            except (TypeError, ValueError):
                pass
    return {"sku": by_sku, "desc": by_desc, "product_id": by_pid, "rows": len(rows)}


def _catalog_cost_for(idx, sku, desc, product_id=None):
    """Catalog cost for one line, SKU first then product_id then normalized description. None when the
    catalog says nothing — the honest answer, which Option A reports as UNKNOWN."""
    s = str(sku or "").strip().upper()
    if s and s in idx["sku"]:
        return idx["sku"][s]
    if product_id is not None:
        try:
            pid = float(product_id)
            if pid in idx["product_id"]:
                return idx["product_id"][pid]
        except (TypeError, ValueError):
            pass
    d = " ".join(str(desc or "").strip().upper().split())
    if d and d in idx["desc"]:
        return idx["desc"][d]
    return None


def _sale_key_index(client, org_id, period):
    """{(trans_id, NORMALIZED product_desc) -> {sku, product_id}} for the period, so a preview detail
    line (which carries neither) can be cross-referenced to the POS catalog. Read-only, org-scoped.

    A collision (the same product twice on one transaction) is harmless: the two rows carry the same
    SKU. Missing table / unreadable -> {} and Option A simply reports UNKNOWN."""
    from app.modules.commcalc.commission_engine import _read_sales
    out = {}
    try:
        rows = _read_sales(client, org_id, period)
    except Exception:
        return out
    for r in rows:
        t = str(r.get("trans_id") or "").strip()
        d = " ".join(str(r.get("product_desc") or "").strip().upper().split())
        if not d:
            continue
        out.setdefault((t, d), {"sku": r.get("sku"), "product_id": r.get("product_id"),
                                "department": r.get("department"), "category": r.get("category")})
    return out


def _definition_ctx(client, org_id):
    """(index, rule, setup_keywords, ready) for the tenant's ACCESSORY DEFINITION (mig 257). Read-only
    and org-scoped; every failure degrades to "no definition configured" rather than raising, so this
    annotation can never take the audit down."""
    from app.modules.commcalc import accessory_definition as adef
    idx, rule, kws, ready = {f: {} for f in adef.MATCH_FIELDS}, dict(adef.DEFAULT_FIELD_RULE), set(), True
    try:
        rows = (client.schema("commcalc").table(adef.MAP_TABLE).select("*")
                .eq("org_id", org_id).limit(100000).execute().data) or []
        idx = adef.build_index(rows)
    except Exception:
        ready = False
    try:
        cfg = (client.schema("commcalc").table("accessory_config")
               .select("definition_field_rule").eq("org_id", org_id).limit(1).execute().data) or []
        rule, _refused = adef.normalize_field_rule(cfg[0].get("definition_field_rule") if cfg else None)
    except Exception:
        rule, _refused = adef.normalize_field_rule(None)
    try:
        srows = (client.schema("commcalc").table("accessory_config")
                 .select("setup_fee_keywords").eq("org_id", org_id).limit(1).execute().data) or []
        kws = {str(k).strip().lower() for k in ((srows[0].get("setup_fee_keywords") if srows else None) or []) if str(k).strip()}
    except Exception:
        kws = set()
    if not kws:
        kws = {"device setup charge"}
    return idx, rule, kws, ready


def audit(client, org_id, period, c_basis="price", assume_gp_pct=None, rep=None, max_lines=4000):
    """The full read-only audit for ONE period. Writes nothing; recomputes nothing.

    c_basis / assume_gp_pct parameterise Option C ONLY. `assume_gp_pct` has no default on purpose —
    an assumed margin is the operator's number, and with none supplied Option C falls back to
    % of price and says so.
    """
    from app.modules.commcalc import commission_engine as ce

    cfg = pdq.load_cost_config(client, org_id)
    c_basis = c_basis if c_basis in C_BASES else "price"
    if c_basis == "assumed_gp" and assume_gp_pct is None:
        c_basis = "price"                       # never invent a margin

    out = {
        "period": period, "org_id": org_id, "ready": False,
        "config": {k: v for k, v in cfg.items() if k != "_stored"},
        "config_stored": bool(cfg.get("_stored")),
        "option_labels": dict(OPTION_LABELS),
        "option_c": {"basis": c_basis, "assume_gp_pct": (None if assume_gp_pct is None
                                                         else round(_f(assume_gp_pct), 4))},
        "rules": [], "by_rep": [], "items": [], "lines": [],
        "totals": _blank_totals(), "flag_summary": [],
        "counts": {"reps": 0, "rules": 0, "matched_lines": 0, "suspect_lines": 0},
        "note": None,
    }

    try:
        pv = ce.preview(client, org_id, period, detail=True, only_rep=(rep or None))
    except Exception as e:
        out["note"] = f"plan preview unavailable: {type(e).__name__}: {e}"
        return out
    if not pv.get("ready"):
        out["note"] = pv.get("note") or "No Commission Plans configured for this tenant."
        return out
    out["ready"] = True

    cat_idx = _catalog_index(client, org_id)
    out["catalog_rows"] = cat_idx.get("rows", 0)
    key_idx = _sale_key_index(client, org_id, period)
    # mig 257 — PURE ANNOTATION (see the header). Nothing below reads it into a dollar.
    from app.modules.commcalc import accessory_definition as _adef
    _d_idx, _d_rule, _d_kws, _d_ready = _definition_ctx(client, org_id)
    _d_counts = {"lines": 0, "is_accessory": 0, "not_accessory": 0, "setup_fee": 0,
                 "ext_is_accessory": 0.0, "ext_not_accessory": 0.0, "by_class": {}}

    flagged, item_agg, rule_agg = [], {}, {}
    reps_out = []
    total_matched = total_suspect = 0

    for rrow in (pv.get("by_rep") or []):
        rep_name = rrow.get("rep")
        rep_tot = _blank_totals()
        rep_lines = rep_suspect = 0
        for rb in (rrow.get("rules") or []):
            kind = str(rb.get("payout_kind") or "").strip().lower()
            if kind not in pdq.COST_BASED_KINDS:
                continue
            pct = _f(rb.get("pct"))
            rid = rb.get("rule_id")
            ra = rule_agg.setdefault(rid, {
                "rule_id": rid, "label": rb.get("label"), "payout_kind": kind, "pct": pct,
                "match_field": rb.get("match_field"), "match_op": rb.get("match_op"),
                "match_value": rb.get("match_value"),
                "rate_flags": pdq.rate_flags(kind, pct, cfg),
                "rate_flag_labels": [pdq.RATE_FLAG_LABELS[c] for c in pdq.rate_flags(kind, pct, cfg)],
                "matched_lines": 0, "suspect_lines": 0, "paid": 0.0})
            for ldet in (rb.get("lines") or []):
                if not ldet.get("qualifies", True):
                    continue
                ext, gp = _f(ldet.get("ext_price")), _f(ldet.get("gp"))
                amt = _f(ldet.get("amount"))
                flags = pdq.line_flags(ext, gp, cfg)
                suspect = bool(flags)
                desc = ldet.get("product")
                dkey = " ".join(str(desc or "").strip().upper().split())
                meta = key_idx.get((str(ldet.get("trans_id") or "").strip(), dkey), {})
                ccost = _catalog_cost_for(cat_idx, meta.get("sku"), desc, meta.get("product_id"))
                opts = _line_options(ext, gp, amt, pct, kind, suspect, c_basis, assume_gp_pct, ccost)
                # ANNOTATION ONLY — computed AFTER every option amount above, and never fed back in.
                _dv = _adef.classify({"product_desc": desc, "sku": meta.get("sku"),
                                      "department": meta.get("department"),
                                      "category": meta.get("category")},
                                     _d_idx, _d_rule, _d_kws, mode="proposed")
                _d_counts["lines"] += 1
                if _dv.get("matched_by") == "setup_fee":
                    _d_counts["setup_fee"] += 1
                if _dv.get("is_accessory"):
                    _d_counts["is_accessory"] += 1
                    _d_counts["ext_is_accessory"] = round(_d_counts["ext_is_accessory"] + ext, 2)
                    _ck = _dv.get("accessory_class") or "(no class)"
                    _d_counts["by_class"][_ck] = int(_d_counts["by_class"].get(_ck, 0)) + 1
                else:
                    _d_counts["not_accessory"] += 1
                    _d_counts["ext_not_accessory"] = round(_d_counts["ext_not_accessory"] + ext, 2)

                total_matched += 1
                rep_lines += 1
                ra["matched_lines"] += 1
                ra["paid"] = round(ra["paid"] + amt, 2)
                for k in OPTION_KEYS:
                    v = opts.get(k)
                    rep_tot[k] = round(rep_tot[k] + (amt if v is None else v), 2)
                    out["totals"][k] = round(out["totals"][k] + (amt if v is None else v), 2)
                if suspect:
                    total_suspect += 1
                    rep_suspect += 1
                    ra["suspect_lines"] += 1

                ik = (dkey, str(meta.get("sku") or "").strip().upper())
                ia = item_agg.setdefault(ik, {
                    "product": desc, "sku": meta.get("sku"),
                    "department": meta.get("department"), "category": meta.get("category"),
                    "lines": 0, "ext_price": 0.0, "gp": 0.0, "paid": 0.0,
                    "implied_cost_min": None, "implied_cost_max": None,
                    "catalog_cost": ccost, "flags": set(),
                    "acc_def": bool(_dv.get("is_accessory")),
                    "acc_def_class": _dv.get("accessory_class"),
                    "acc_def_by": _dv.get("matched_by")})
                ia["lines"] += 1
                ia["ext_price"] = round(ia["ext_price"] + ext, 2)
                ia["gp"] = round(ia["gp"] + gp, 2)
                ia["paid"] = round(ia["paid"] + amt, 2)
                ic = pdq.derived_cost(ext, gp)
                ia["implied_cost_min"] = ic if ia["implied_cost_min"] is None else min(ia["implied_cost_min"], ic)
                ia["implied_cost_max"] = ic if ia["implied_cost_max"] is None else max(ia["implied_cost_max"], ic)
                ia["flags"].update(flags)

                if suspect and len(flagged) < max_lines:
                    flagged.append({
                        "rep": rep_name, "rule": rb.get("label"), "payout_kind": kind, "pct": pct,
                        "date": ldet.get("date"), "trans_id": ldet.get("trans_id"),
                        "product": desc, "sku": meta.get("sku"),
                        "ext_price": ext, "gp": gp, "implied_cost": ic,
                        "catalog_cost": ccost, "amount": amt, "flags": flags,
                        "flag_labels": [pdq.FLAG_LABELS.get(c, c) for c in flags],
                        "acc_def": bool(_dv.get("is_accessory")),
                        "acc_def_class": _dv.get("accessory_class"),
                        "acc_def_by": _dv.get("matched_by"),
                        **{k: opts.get(k) for k in OPTION_KEYS}})
        if rep_lines:
            reps_out.append({"rep": rep_name, "store": rrow.get("store"), "market": rrow.get("market"),
                             "plan_name": rrow.get("plan_name"),
                             "matched_lines": rep_lines, "suspect_lines": rep_suspect,
                             **{k: round(rep_tot[k], 2) for k in OPTION_KEYS},
                             "delta_b": round(rep_tot["option_b"] - rep_tot["current"], 2),
                             "delta_c": round(rep_tot["option_c"] - rep_tot["current"], 2),
                             "delta_r": round(rep_tot["option_r"] - rep_tot["current"], 2)})

    reps_out.sort(key=lambda x: -_f(x.get("current")))
    items = []
    for v in item_agg.values():
        v = dict(v)
        v["flags"] = sorted(v["flags"])
        v["flag_labels"] = [pdq.FLAG_LABELS.get(c, c) for c in v["flags"]]
        # The POS catalog's own cost next to the cost the sale lines IMPLY. When they disagree the
        # sale export and the catalog disagree — which is the fact the owner needs for Option A.
        v["catalog_cost_matches_implied"] = (
            None if v["catalog_cost"] is None
            else bool(v["implied_cost_min"] == v["implied_cost_max"]
                      and abs(_f(v["implied_cost_min"]) - _f(v["catalog_cost"])) <= 0.005))
        items.append(v)
    items.sort(key=lambda x: (0 if x["flags"] else 1, -_f(x.get("ext_price"))))

    out["by_rep"] = reps_out
    out["items"] = items
    out["lines"] = flagged
    out["rules"] = sorted(rule_agg.values(), key=lambda x: -_f(x.get("paid")))
    out["flag_summary"] = pdq.summarize(flagged)
    out["counts"] = {"reps": len(reps_out), "rules": len(rule_agg),
                     "matched_lines": total_matched, "suspect_lines": total_suspect,
                     "flagged_shown": len(flagged)}
    # mig 257 — the annotation's roll-up. Additive: no existing key is derived from it.
    out["accessory_definition"] = {
        **_d_counts, "ready": _d_ready,
        "note": ("Of the lines a %-of-basis rule actually paid on, this is how many your own accessory "
                 "definition counts as accessories. It changes no number on this page — it says whether "
                 "the lines being paid an accessory rate are things you call accessories."),
        "migration": None if _d_ready else "257_commission_accessory_definition.sql"}
    out["deltas"] = {
        "option_b": round(out["totals"]["option_b"] - out["totals"]["current"], 2),
        "option_c": round(out["totals"]["option_c"] - out["totals"]["current"], 2),
        "option_r": round(out["totals"]["option_r"] - out["totals"]["current"], 2),
        "option_a": ("partial" if any(l.get("option_a") is not None for l in flagged) else "unknown"),
    }
    if not total_matched:
        out["note"] = ("No %-of-GP / %-of-(price-cost) plan rule matched any line in this period. "
                       "Nothing here is broken — this tenant pays accessories some other way, or the "
                       "reps who sold them have no plan assigned.")
    return out
