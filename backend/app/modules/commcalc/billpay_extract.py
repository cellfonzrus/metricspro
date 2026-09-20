"""BILL PAYMENTS EXTRACTED FROM THE SALES EXPORT — the derived Bill Payments report (owner 2026-09-20).

Owner, verbatim: *"other reports land in their respective categories if such a report is present for
that carrier, otherwise bill payments should be extracted from their sales reports and then assigned a
separate report for themselves."*

DUPLICATE CHECK (build gate, CLAUDE.md). The predicate that says "this sale line IS a bill payment"
already exists and is the ONE definition every surface rides: `exec_metric_defs.line_match` on the
org's resolved `bill_payment` bucket (tenant row > house carrier preset > built-in, mig 962), consumed
by `router._sales_cell_agg` for the Exec-MTD Bill Payment Qty / $ columns, `/metric-recon`'s secondary
basis and Leg B of the mig-944 3-way recon. This module does NOT define a second predicate. It calls
the same `line_match` (injected, so the module stays pure and the harness proves the real one) over the
same rows with the same canonical skip rules `_sales_cell_agg` applies (voided / Return / no rep), and
its only additions are (a) the LINES, not just the cell totals, (b) WHICH token matched each line —
the provenance the owner needs to adjust the tokens through the existing Exec-MTD metric definitions
(PUT /exec-metric-config) — and (c) the comparison with the carrier's own bill-pay report when one is
present for the same days. The known over-match of `_BILLPAY_DEFAULT_TOKENS` (index §19) is the
Daily-Targets conversion vocabulary, NOT this predicate; nothing here widens either.

THIS BOOKS NOTHING. Read-only, stdlib-pure. Proof: backend/harness_onboarding_intake_c.py §E.
"""

# The canonical skip rules of router._sales_cell_agg (the "was three slightly different predicates"
# note there): a voided line, a Return, a line with no rep or the admin login. Mirrored, not widened.
VOID_TOKENS = ("true", "yes", "1", "voided", "void")
MATCH_KINDS = ("category", "department", "product_desc_contains")


def _s(v):
    return "" if v is None else str(v).strip()


def _f(v):
    try:
        return float(str(v).replace(",", "").replace("$", "") or 0)
    except (TypeError, ValueError):
        return 0.0


def money(x):
    return round(float(x or 0.0), 2)


def is_countable(row, void_tokens=VOID_TOKENS):
    """Would `_sales_cell_agg` count this line at all? (the three canonical skip rules)."""
    r = row or {}
    if _s(r.get("voided")).lower() in void_tokens:
        return False
    if _s(r.get("trans_type")) == "Return":
        return False
    rep = _s(r.get("salesperson"))
    if not rep or rep.lower() == "admin":
        return False
    return True


def match_reason(rule, dept, cat, pdesc):
    """WHY a line matches the bill_payment rule — the SAME precedence as exec_metric_defs.line_match
    (exclusions first; then category membership, department membership, product substring), so the
    answer is always `line_match`'s answer plus the token that made it. Returns
    {"by": <kind>, "token": <the configured token>} or None. Inputs already lowercased."""
    rule = rule or {}
    if rule.get("exclude_department") and dept in rule["exclude_department"]:
        return None
    if rule.get("exclude_category") and cat in rule["exclude_category"]:
        return None
    for t in rule.get("exclude_product_desc_contains") or []:
        if t in pdesc:
            return None
    if rule.get("category") and cat in rule["category"]:
        return {"by": "category", "token": cat}
    if rule.get("department") and dept in rule["department"]:
        return {"by": "department", "token": dept}
    for t in rule.get("product_desc_contains") or []:
        if t in pdesc:
            return {"by": "product_desc_contains", "token": t}
    return None


def extract_lines(rows, rule, line_match=None, store_key=None, void_tokens=VOID_TOKENS):
    """The bill-pay LINES of a sales slice, each with its match provenance.

    `line_match(rule, dept, cat, pdesc) -> bool` is exec_metric_defs.line_match, injected; when given,
    every line's verdict is TAKEN FROM IT and `match_reason` only explains it — the two can never
    disagree (asserted in the harness). Without it, match_reason decides (same rule, pure)."""
    lines, scanned, skipped, unmatched = [], 0, 0, 0
    for r in rows or []:
        r = r or {}
        if not is_countable(r, void_tokens):
            skipped += 1
            continue
        scanned += 1
        d, c, p = _s(r.get("department")).lower(), _s(r.get("category")).lower(), _s(r.get("product_desc")).lower()
        hit = bool(line_match(rule, d, c, p)) if line_match else (match_reason(rule, d, c, p) is not None)
        if not hit:
            unmatched += 1
            continue
        why = match_reason(rule, d, c, p) or {"by": "line_match", "token": None}
        store = _s(r.get("store"))
        lines.append({
            "trans_id": _s(r.get("trans_id")), "trans_date": _s(r.get("trans_date"))[:10],
            "store": store, "store_key": (store_key(store) if store_key else store) or store,
            "salesperson": _s(r.get("salesperson")), "department": _s(r.get("department")),
            "category": _s(r.get("category")), "product_desc": _s(r.get("product_desc")),
            "tender_type": _s(r.get("tender_type")) or None,
            "amount": money(_f(r.get("ext_price"))), "matched_by": why["by"], "matched_token": why["token"],
        })
    return {"lines": lines, "rows_scanned": scanned, "rows_skipped": skipped, "rows_unmatched": unmatched}


def rollup(lines):
    """Σ and count per store-day, per store, per matched token — from the extracted lines."""
    per_sd, per_store, per_tok = {}, {}, {}
    for l in lines or []:
        k = (l["store_key"], l["trans_date"])
        s = per_sd.setdefault(k, {"store": l["store_key"], "store_name": l["store"], "date": l["trans_date"],
                                  "amount": 0.0, "count": 0})
        s["amount"] = money(s["amount"] + l["amount"])
        s["count"] += 1
        st = per_store.setdefault(l["store_key"], {"store": l["store_key"], "amount": 0.0, "count": 0})
        st["amount"] = money(st["amount"] + l["amount"])
        st["count"] += 1
        tk = (l["matched_by"], l["matched_token"])
        t = per_tok.setdefault(tk, {"by": tk[0], "token": tk[1], "amount": 0.0, "count": 0})
        t["amount"] = money(t["amount"] + l["amount"])
        t["count"] += 1
    sd = sorted(per_sd.values(), key=lambda s: (s["date"], s["store"]))
    return {"count": len(lines or []), "sum": money(sum(l["amount"] for l in lines or [])),
            "per_store_day": sd,
            "per_store": sorted(per_store.values(), key=lambda s: -s["amount"]),
            "per_token": sorted(per_tok.values(), key=lambda t: -t["count"]),
            "dates": sorted({s["date"] for s in sd}), "stores": sorted({s["store"] for s in sd})}


def token_coverage(rule, lines):
    """Which configured tokens matched NOTHING in this slice (the silent-zero signal — a token that
    describes nobody's data — without widening anything) and which matched, with counts."""
    hit = {}
    for l in lines or []:
        hit[(l["matched_by"], l["matched_token"])] = hit.get((l["matched_by"], l["matched_token"]), 0) + 1
    out = {"matched": [], "unmatched_tokens": []}
    for kind in MATCH_KINDS:
        for tok in (rule or {}).get(kind) or []:
            n = hit.get((kind, str(tok).lower()), 0)
            (out["matched"] if n else out["unmatched_tokens"]).append({"by": kind, "token": tok, "count": n})
    return out


def compare_with_feed(per_store_day, feed_days):
    """The extracted lines beside the CARRIER's own bill-pay report for the same (store, day)s —
    `feed_days` = {(store_key, day): amount} from the mig-939 processor reader
    (router._billpay_processor_by_store_day). A day the feed does not carry is stated as such, never
    as a $0 difference; a feed day the sales export does not carry is listed too."""
    ours = {(s["store"], s["date"]): s for s in per_store_day or []}
    feed = {(k[0], k[1]): money(v if not isinstance(v, dict) else v.get("amount")) for k, v in (feed_days or {}).items()}
    rows = []
    for k in sorted(set(ours) | set(feed)):
        o, f = ours.get(k), feed.get(k)
        rows.append({"store": k[0], "date": k[1],
                     "sales_amount": o["amount"] if o else None, "sales_count": o["count"] if o else None,
                     "feed_amount": f,
                     "difference": (money(o["amount"] - f) if (o and f is not None) else None),
                     "status": ("match" if (o and f is not None and abs(money(o["amount"] - f)) < 0.005) else
                                "differs" if (o and f is not None) else
                                "sales_only" if o else "feed_only")})
    present = bool(feed)
    both = [r for r in rows if r["difference"] is not None]
    return {"feed_present": present, "rows": rows,
            "sum_sales": money(sum(r["sales_amount"] or 0 for r in rows)),
            "sum_feed": money(sum(r["feed_amount"] or 0 for r in rows)) if present else None,
            "difference": money(sum(r["difference"] for r in both)) if both else None,
            "days_compared": len(both), "days_sales_only": sum(1 for r in rows if r["status"] == "sales_only"),
            "days_feed_only": sum(1 for r in rows if r["status"] == "feed_only")}
