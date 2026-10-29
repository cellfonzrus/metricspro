"""THE ONE HOME for "does what a rep DECLARED at closing agree with what our system CAPTURED".

Migration 1102. PURE — lists in, dicts out, no DB, no network.

OWNER ASK (Sanjot, 2026-10-29, verbatim): *"Add a field in as no of edge for total and no of
financicing in thr daily closing for all employees to declare along with the $amount of edge and
acima as they get paid for each transcoding they do, this would then be checked with our system if
our system is capturing the sales as declared by the sales rep"*.

THE HALF THAT ALREADY EXISTED, AND IS THEREFORE NOT BUILT AGAIN. A tenant has declared its own
closing categories since mig 1072: a category says how it is measured (`value_kind='both'` — a tally
AND its dollars) and who declares it (`declared_by='employee'`), `closing/metric_catalog.py` is the
one home for that vocabulary, and `ClosingSubmitForm` already renders a `#` box and a `$` box per
category per person off it. Edge and Acima are therefore CONFIG ROWS (RULE TWO), not fields, and no
code here spells either of them.

THE CLASS THIS FIXES. What had no home was the LINK — a declared category could not say what in the
sales feed answers the same question. So the only declared-vs-captured check in the platform was
`closing/router`'s activation/upgrade count recon, hard-wired to two feed figures: a new category
could not be checked at all, and the check could not be reused. The fix is a counterpart NAMED on the
category (`closing_count_field_def.feed_counterpart`) and ONE reconciler that every such category
flows through — the activation/upgrade recon included, so there is one path and not two.

WHAT THE COUNTERPART DEREFERENCES RATHER THAN RESTATES. `financing:<vendor_key>` points at the
tenant's own `commcalc.financing_vendor` row (mig 272), which already answers "is this sale line
financed, and by whom" — keyed on the TENDER, because Edge is how the customer paid and not a
Motorola model (the 2026-07-27 defect, `financing_registry`'s own docstring). The recon therefore
cannot disagree with what actually pays. `recon_class:<class>` points at the B2B activation/upgrade
buckets mig 501 already defines. No keyword list lives here.

AN ABSENCE NEEDS THE RIGHT REASON (the `recon_drilldown` pending doctrine, §CORE-17). Three
different things look like "nothing captured", and reporting them as one $0.00 variance would read
as *we checked and it agreed*:
  * the day's feed was never loaded          → `pending`, variance None, reason says so
  * the vendor's detection is not configured → `pending`, and the reason blames US, not the rep
  * the feed was read and holds nothing      → `not_captured`, a real accusation with a real zero

MONEY POSTURE: this measures, it never pays, and it never blocks a close. Every verdict is
`flagged` at worst — the same posture mig 501's count recon has always had (informational, never a
gate). A rep is paid per transaction, so a gap either way is worth a manager's eye, and neither
direction is evidence of anything on its own.

ONE PERSON IS ONE ROW. The roster spells a rep "Singh, Simarjyot" and the sales feed spells them
"Simarjyot Singh"; a join on strings reported two reps on B-1750 on 2026-10-10. The identity key is
DEREFERENCED from `core/person_identity.folded_token_key` (§CORE-08), never re-folded here.
"""
from app.modules.core import person_identity as _person

# ── Vocabulary. These constants ARE the counterpart vocabulary; nothing else may spell them. ──────

#: Which registry answers the same question as a declared category. A kind is added here and in the
#: resolver the endpoint hands `captured` from — never by a caller inventing a prefix.
COUNTERPART_KINDS = ("financing", "recon_class")

#: The B2B count buckets mig 501 already defines, reproduced from `count_config` semantics. Mirrors
#: `metric_catalog.RECON_CLASSES`; the lock harness asserts the two never drift.
RECON_CLASSES = ("activation", "upgrade")

#: The keys addressing ONE accusation. The metric is part of the address because a rep-day can agree
#: on Edge and disagree on Acima, and those are two findings about two different figures.
ROW_KEYS = ("date", "store_code", "rep", "metric_key")

#: Every verdict this module can return, worst first. `pending` is NOT a gap: it is an accusation
#: the report refuses to make because it has no counterpart to make it from.
VERDICTS = ("variance", "not_captured", "not_declared", "pending", "ok")

#: The verdicts that are a real finding about a rep's day.
GAP_VERDICTS = ("variance", "not_captured", "not_declared")


def _f(v, default=0.0):
    try:
        if v is None or (isinstance(v, str) and not str(v).strip()):
            return float(default)
        return float(v)
    except (TypeError, ValueError):
        return float(default)


def _i(v, default=0):
    try:
        if v is None or (isinstance(v, str) and not str(v).strip()):
            return int(default)
        return int(float(v))
    except (TypeError, ValueError):
        return int(default)


def _s(v):
    return str(v or "").strip()


# ── §A  the counterpart: what in the feed answers the same question ───────────────────────────────

def parse_counterpart(value):
    """`'financing:edge'` → `{'kind': 'financing', 'ref': 'edge'}`, else None.

    None is the ordinary case, not an error: most declared categories (mailbox rentals, printing)
    have no counterpart in a sales feed, and a category with none must be LEFT OUT rather than
    compared against zero. An unknown kind is refused for the same reason a mis-typed one is — a
    prefix this module does not serve would otherwise reconcile against an empty captured side and
    read as "the rep declared sales we never captured"."""
    raw = _s(value)
    if ":" not in raw:
        return None
    kind, _, ref = raw.partition(":")
    kind, ref = _s(kind).lower(), _s(ref)
    if kind not in COUNTERPART_KINDS or not ref:
        return None
    if kind == "recon_class" and ref.lower() not in RECON_CLASSES:
        return None
    return {"kind": kind, "ref": ref.lower() if kind == "recon_class" else ref}


def counterpart_key(parsed) -> str:
    """The canonical string a `captured` row is keyed on — the same spelling the category stores, so
    the resolver that builds the captured side and the category that names it cannot drift."""
    if not parsed:
        return ""
    return f"{parsed['kind']}:{parsed['ref']}"


def counterpart_of(metric):
    return parse_counterpart((metric or {}).get("feed_counterpart"))


def counterpart_options(*, financing_vendors=None, recon_classes=None):
    """[{value, label, kind, ref}] — what an admin may pick on the Count Fields editor.

    PICK-DON'T-TYPE (RULE THREE). The financing choices are the tenant's OWN vendor rows, with the
    label they typed, so mapping a category to Acima is a choice and never a spelling. A tenant with
    no financing vendor set up is offered none, which is the honest answer rather than an invented
    one."""
    out = []
    for v in (financing_vendors or []):
        vk = _s(v.get("vendor_key"))
        if not vk:
            continue
        out.append({"value": f"financing:{vk}", "kind": "financing", "ref": vk,
                    "label": f"{_s(v.get('label')) or vk} — financed sales from the sales feed"})
    for rc in (recon_classes if recon_classes is not None else RECON_CLASSES):
        rc = _s(rc).lower()
        if rc not in RECON_CLASSES:
            continue
        out.append({"value": f"recon_class:{rc}", "kind": "recon_class", "ref": rc,
                    "label": f"{rc.title()} count from the B2B sales feed"})
    return out


def reconciled_metrics(catalog):
    """The catalog entries that NAME a counterpart, in catalog order. The reconciler's whole subject
    — a surface that filters the catalog itself has reproduced the rule."""
    return [m for m in (catalog or []) if counterpart_of(m)]


def counterpart_keys(catalog):
    """The distinct counterparts this tenant actually reconciles — what the endpoint needs to go and
    fetch, so it reads the feed for the vendors in use and no others."""
    seen, out = set(), []
    for m in reconciled_metrics(catalog):
        k = counterpart_key(counterpart_of(m))
        if k and k not in seen:
            seen.add(k)
            out.append(k)
    return out


# ── §B  the comparison ────────────────────────────────────────────────────────────────────────────

def person_key(name) -> str:
    """ONE person, one key — DEREFERENCED from §CORE-08, never re-folded here. A second fold is how
    one surface would start seeing a rep the rest of the system does not."""
    return _person.folded_token_key(_s(name))


def _day(v):
    return _s(v)[:10]


def _addr(row, metric, metric_key):
    """The address of one accusation, AT THE GRAIN THE CATEGORY IS DECLARED AT.

    This is the load-bearing line. A store-declared category is ONE figure for the whole store-day,
    while the sales feed always knows a salesperson — so folding the feed per rep against a store
    declaration produced two rows, the store's "we captured nothing" and the rep's "nobody declared
    this", and accused a store of failing to declare a sale it had declared. The grain belongs to
    the CATEGORY, so it is applied once here rather than by each caller building its own rows.
    """
    rep = "" if (metric or {}).get("declared_by") != "employee" else person_key(row.get("rep"))
    return (_day(row.get("date")), _s(row.get("store_code")), rep, metric_key)


def reconcile(catalog, *, declared=None, captured=None, feed_days=None,
              counterpart_status=None, units_tolerance=0, amount_tolerance=0.0):
    """Declared vs captured, per day x store x rep x metric.

    `catalog`    — `metric_catalog.build` output; only entries naming a counterpart are reconciled.
    `declared`   — flat `{date, store_code, rep, metric_key, units, amount}` rows, as
                   `metric_catalog.by_employee` yields once addressed by day and store.
    `captured`   — flat `{date, store_code, rep, counterpart, units, amount}` rows, built by the
                   endpoint from each counterpart's OWN registry (never from a rule invented here).
    `feed_days`  — the days the sales feed was actually READ for. A day outside it has no
                   counterpart to compare against and is `pending`, not a zero.
    `counterpart_status` — `{counterpart: {status, note}}` from the registry. Anything but
                   `'ok'`/absent makes the row `pending` with the registry's own note as the reason,
                   so a detection WE have not configured is never reported as a rep's missing sale.
    Both tolerances are the tenant's, defaulting to exact.

    Flat rows in, flat rows out: a caller passing one day and a caller passing a month use the same
    shape, which is why there is no second reconciler for the ranged question.
    """
    cat = reconciled_metrics(catalog)
    by_counterpart = {}
    for m in cat:
        by_counterpart.setdefault(counterpart_key(counterpart_of(m)), []).append(m)
    status = {_s(k): (v or {}) for k, v in (counterpart_status or {}).items()}
    days = None if feed_days is None else {_day(d) for d in feed_days}

    # Both sides folded onto the SAME address. The declared side is keyed by metric and the captured
    # side by counterpart, so the fold happens through the catalog rather than by assuming the two
    # vocabularies are the same word.
    dec, cap, display = {}, {}, {}
    by_mkey = {m["key"]: m for m in cat}
    for r in (declared or []):
        m = by_mkey.get(_s(r.get("metric_key")))
        if m is None:
            continue
        a = _addr(r, m, m["key"])
        d = dec.setdefault(a, {"units": 0, "amount": 0.0})
        d["units"] += _i(r.get("units"))
        d["amount"] = round(d["amount"] + _f(r.get("amount")), 2)
        if a[2]:
            # The spelling a HUMAN typed into the closing wins the display, because that is the name
            # a manager recognises; the feed's spelling only fills a rep nobody declared.
            display[a[:3]] = _s(r.get("rep"))
    for r in (captured or []):
        ck = counterpart_key(parse_counterpart(r.get("counterpart")))
        for m in by_counterpart.get(ck, []):
            a = _addr(r, m, m["key"])
            c = cap.setdefault(a, {"units": 0, "amount": 0.0})
            c["units"] += _i(r.get("units"))
            c["amount"] = round(c["amount"] + _f(r.get("amount")), 2)
            if a[2]:
                display.setdefault(a[:3], _s(r.get("rep")))

    label_of = {m["key"]: (m.get("label") or m["key"]) for m in cat}
    counterpart_of_key = {m["key"]: counterpart_key(counterpart_of(m)) for m in cat}

    rows = []
    for a in sorted(set(dec) | set(cap)):
        day, store, _pk, mkey = a
        d = dec.get(a) or {"units": 0, "amount": 0.0}
        c = cap.get(a) or {"units": 0, "amount": 0.0}
        ck = counterpart_of_key.get(mkey, "")
        st = status.get(ck) or {}
        st_name = _s(st.get("status")).lower() or "ok"

        reason, verdict = None, None
        if days is not None and day not in days:
            verdict = "pending"
            reason = ("The sales feed has not been loaded for this day, so there is nothing to "
                      "compare the declaration against.")
        elif st_name not in ("", "ok"):
            verdict = "pending"
            note = _s(st.get("note"))
            reason = ("This category's counterpart is not ready, so the declaration cannot be "
                      "checked yet" + (f" — {note}" if note else "") + ".")
        if verdict == "pending":
            rows.append(_row(a, display.get(a[:3]), mkey, label_of.get(mkey), ck, d, c,
                             verdict, reason, None, None))
            continue

        uvar = _i(d["units"]) - _i(c["units"])
        avar = round(_f(d["amount"]) - _f(c["amount"]), 2)
        within = abs(uvar) <= _i(units_tolerance) and abs(avar) <= _f(amount_tolerance) + 1e-9
        if within:
            verdict = "ok"
            reason = "The declaration and the sales feed agree."
        elif _i(c["units"]) == 0 and _f(c["amount"]) == 0.0:
            verdict = "not_captured"
            reason = ("The rep declared this and the sales feed captured none of it. Either the sale "
                      "was not rung here, or it was rung in a way this category does not recognise.")
        elif _i(d["units"]) == 0 and _f(d["amount"]) == 0.0:
            verdict = "not_declared"
            reason = ("The sales feed captured this and nobody declared it at closing, so it is not "
                      "on anyone's day.")
        else:
            verdict = "variance"
            reason = "The declaration and the sales feed disagree."
        rows.append(_row(a, display.get(a[:3]), mkey, label_of.get(mkey), ck, d, c,
                         verdict, reason, uvar, avar))

    rows.sort(key=lambda r: (r["date"], r["store_code"], r["rep"] or "", r["metric_key"]))
    return {"rows": rows,
            "by_rep": _by_rep(rows),
            "totals": totals(rows),
            "metrics": [{"key": m["key"], "label": m.get("label") or m["key"],
                         "counterpart": counterpart_of_key.get(m["key"]),
                         "value_kind": m.get("value_kind")} for m in cat],
            # Named, not silently dropped: a manager asking "where is my mailbox category" gets an
            # answer instead of wondering whether the report lost it.
            "not_reconciled": [m["key"] for m in (catalog or []) if not counterpart_of(m)]}


def _row(addr, rep_display, mkey, label, ck, d, c, verdict, reason, uvar, avar):
    day, store, pkey, _m = addr
    return {"date": day, "store_code": store, "rep": rep_display or "", "rep_key": pkey,
            "metric_key": mkey, "label": label or mkey, "counterpart": ck,
            "units_declared": _i(d["units"]), "amount_declared": round(_f(d["amount"]), 2),
            "units_captured": _i(c["units"]), "amount_captured": round(_f(c["amount"]), 2),
            "units_variance": uvar, "amount_variance": avar,
            "verdict": verdict, "reason": reason,
            # NEVER 'blocked'. This report is informational, exactly as mig 501's count recon is.
            "severity": ("flagged" if verdict in GAP_VERDICTS
                         else ("pending" if verdict == "pending" else "ok"))}


def _by_rep(rows):
    """One line per person per store — the grain a manager coaches on, and the grain a per-
    transaction payout is questioned at."""
    out = {}
    for r in (rows or []):
        k = (r["store_code"], r["rep_key"])
        s = out.get(k)
        if s is None:
            s = out[k] = {"store_code": r["store_code"], "rep": r["rep"], "rep_key": r["rep_key"],
                          "units_declared": 0, "units_captured": 0,
                          "amount_declared": 0.0, "amount_captured": 0.0,
                          "ok": 0, "gaps": 0, "pending": 0, "days": set()}
        s["days"].add(r["date"])
        s["units_declared"] += r["units_declared"]
        s["units_captured"] += r["units_captured"]
        s["amount_declared"] = round(s["amount_declared"] + r["amount_declared"], 2)
        s["amount_captured"] = round(s["amount_captured"] + r["amount_captured"], 2)
        if r["verdict"] == "ok":
            s["ok"] += 1
        elif r["verdict"] == "pending":
            s["pending"] += 1
        else:
            s["gaps"] += 1
    for s in out.values():
        s["days"] = len(s["days"])
    return sorted(out.values(), key=lambda s: (-s["gaps"], s["store_code"], s["rep"] or ""))


def totals(rows):
    """Counts of findings, with `pending` kept OUT of `gaps` — an accusation the report refused to
    make is not a finding against anybody."""
    t = {"rows": len(rows or []), "ok": 0, "gaps": 0, "pending": 0,
         "units_declared": 0, "units_captured": 0, "amount_declared": 0.0, "amount_captured": 0.0}
    for r in (rows or []):
        t["ok" if r["verdict"] == "ok" else ("pending" if r["verdict"] == "pending" else "gaps")] += 1
        t["units_declared"] += r["units_declared"]
        t["units_captured"] += r["units_captured"]
        t["amount_declared"] = round(t["amount_declared"] + r["amount_declared"], 2)
        t["amount_captured"] = round(t["amount_captured"] + r["amount_captured"], 2)
    return t
