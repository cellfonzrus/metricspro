"""P&L ← COMMISSION LEDGER (owner 2026-09-21, mig 1013): the per-org P&L commission SOURCE, the
bucket → line booking, the DERIVED suppression set, the double-booking guard and the divergence
read-out. Every deciding function is PURE; the two `load_*` functions are the only I/O.

OWNER, verbatim: "p&l is not showing the commission received, it shows in the commission ledger but
not populating the p&l - check platform wide not bandaid".

THE CLASS THIS CLOSES. The P&L's commission lines were derived from PER-FEED tables
(raw_ma_commission, raw_ma_daily_tx, raw_comp_report, activation_rebate_ledger, raw_mi — each a
feed-specific shape with its own booking loop in coa.build_inputs). The canonical, bucketed,
sign-conventioned commission ledger (commcalc.commission_ledger, mig 071 / 1006 / 1009) — where EVERY
carrier onboarded through the intake lands, whatever its file looks like — was not a P&L source. A
tenant whose statements land only in the ledger showed $0 commission on the P&L, silently (org
f4f1c16e…: 973 ledger lines for July 2026, net 86,970.34, nothing on the P&L).

THE DESIGN (config, one home, dereferenced, byte-identical by default)
  • WHICH SOURCE books the P&L commission lines is per-org CONFIG:
    commission_org_config.pl_commission_source ∈ ma_store_pnl.COMMISSION_SOURCES, read by
    ma_store_pnl.load_config (the reader of every other P&L switch) and resolved by ONE function
    here, `resolve_source` — every consumer of the switch calls it (harness_pl_commission_source_lock).
  • THE BOOKING dereferences the registry. `ledger_bookings` groups the period's ledger rows by store
    and hands each group to `commission_ledger.summarize` — the ledger's OWN summarizer, the finance
    boundary in CLAUDE.md: the AMOUNTS are the commission module's; this module never sums a
    `payout_total` itself — and books each bucket's total to the P&L line its registry row names
    (`categories[bucket]['pl_line_key']`, mig 1009). No bucket → line map exists here.
  • THE SIGN on the line is the chart's, not a second convention: a revenue line takes the bucket's
    signed total as-is (earned +, a deduction a tenant nets into revenue −); a COGS / opex line takes
    the NEGATED total, so a deduction bucket (−7,396.27 of chargebacks) lands as +7,396.27 of expense
    and an earned rebate bucket lands as contra-COGS (ruling K1) — through `ma_store_pnl.rebate_route`
    for the rebate family, so the org's rebate presentation (mig 934) is honoured by this source too.
  • THE SUPPRESSION SET IS DERIVED: `covered_lines(buckets)` = the pl_line_keys of the org's ACTIVE
    buckets. Under 'ledger', coa's guarded adder drops a commission-FEED booking whose line is in that
    set (recorded, never silent) — so a line is never booked from both. Bookings from non-commission
    sources (rent on store_opex, chargeback_items on chargebacks, distributor fees on vip_fees) go
    through the plain adder and are untouched.
  • THE GUARD: `double_booked` names any line booked from both; coa raises on it (an honest failure
    beats a doubled statement) and the harness asserts it goes RED when the guarded adder is bypassed.
  • THE DIVERGENCE: `divergence` puts Σ feeds vs Σ ledger per line, with the difference in words, on
    the line's `commission_source` (engine passthrough) — so a tenant migrating from the feed tables
    to the ledger sees the gap on the P&L BEFORE flipping the switch, and after.
  • UNBOOKED MONEY IS REPORTED, never dropped: an unmapped payout ('other'), a line under a bucket key
    the registry no longer lists ('unlisted'), a bucket with no P&L line chosen — each with its amount
    and a reason (the mig-312 posture), on the same passthrough.

RULE TWO: no carrier, tenant, feed or product name appears here. Proof: backend/harness_pl_commission_source.py
(the booking, the compatibility pin, the guard, the divergence, the shows-in); lock:
backend/harness_pl_commission_source_lock.py (CI).
"""
from app.modules.commcalc.calculator import safe_float
from app.modules.commcalc import commission_ledger as _cl
from app.modules.account import ma_store_pnl as _msp
from app.core import column_tolerant as _ct      # the ONE reading rule for a wide table (§4b.1)

CONFIG_COLUMN = "pl_commission_source"
MIGRATION = _msp.PL_CONFIG_MIGRATION[CONFIG_COLUMN]      # one home: ma_store_pnl.PL_CONFIG_COLUMNS

SOURCE_FEEDS = _msp.COMMISSION_SOURCE_FEEDS
SOURCE_LEDGER = _msp.COMMISSION_SOURCE_LEDGER
SOURCE_LEDGER_ELSE_FEEDS = _msp.COMMISSION_SOURCE_LEDGER_ELSE_FEEDS
SOURCES = _msp.COMMISSION_SOURCES
SOURCE_LABELS = {
    SOURCE_FEEDS: "The carrier / master-agent feed tables (how the P&L books today)",
    SOURCE_LEDGER: "The Commission Ledger (every statement taken in through the intake, by bucket)",
    SOURCE_LEDGER_ELSE_FEEDS: "The Commission Ledger for a month it holds lines for, otherwise the feed tables",
}
SOURCE_BLURBS = {
    SOURCE_FEEDS: ("Commission books from the raw feed tables the platform pulls or you upload "
                   "(commission sheet, daily transactions, compensation report, activation report). "
                   "Statements taken in through the intake are recorded in the Commission Ledger but do "
                   "not reach the P&L."),
    SOURCE_LEDGER: ("Commission books from the Commission Ledger: each bucket's total for the month "
                    "lands on the P&L line its bucket is linked to (Category → Bucket Map), per store "
                    "where the statement names one. The feed-table bookings for those same lines are "
                    "switched off so nothing is counted twice; everything else on the P&L is unchanged."),
    SOURCE_LEDGER_ELSE_FEEDS: ("For a month the Commission Ledger holds lines for, book from the ledger; "
                               "for a month it does not, book from the feed tables as today."),
}
# the feed tables coa.build_inputs books commission from — the evidence the settings panel shows
FEED_TABLES = ("raw_mi", "raw_ma_commission", "raw_ma_daily_tx", "raw_comp_report", "activation_rebate_ledger")
DETAIL_SUFFIX = " (commission ledger)"
UNBOOKED_NO_LINE = "no P&L line chosen for this bucket (Category → Bucket Map → Buckets panel)"
UNBOOKED_UNMAPPED = "unmapped payout ('other') — assign these labels a bucket on the Category → Bucket Map"
UNBOOKED_UNLISTED = "filed under a bucket key the registry no longer lists"
UNBOOKED_UNKNOWN_LINE = "the bucket names a P&L line the chart does not have"


def _money(x):
    return round(safe_float(x), 2)


# ── 1. THE ONE RESOLVER ──────────────────────────────────────────────────────────────────────────
def resolve_source(configured, ledger_has_lines):
    """PURE. The source that books THIS period's commission lines: SOURCE_FEEDS or SOURCE_LEDGER.
    `configured` is the org's pl_commission_source (None / unknown → the house default, feeds);
    `ledger_has_lines` says whether the ledger holds any line for the period, which is the only fact
    'ledger_else_feeds' turns on. Every consumer of the switch calls this — never re-derives it."""
    c = str(configured or "").strip().lower()
    if c == SOURCE_LEDGER:
        return SOURCE_LEDGER
    if c == SOURCE_LEDGER_ELSE_FEEDS:
        return SOURCE_LEDGER if ledger_has_lines else SOURCE_FEEDS
    return SOURCE_FEEDS


# ── 2. THE DERIVED SUPPRESSION SET ───────────────────────────────────────────────────────────────
def covered_lines(buckets, sections=None, cfg=None):
    """PURE. The P&L line keys the org's ACTIVE buckets book to — each registry `pl_line_key` AND the
    line it ROUTES to under the org's config (`route_line`: the rebate family follows the ONE rebate
    route, so under pl_rebate_presentation='income' a bucket linked to `device_rebate` books, and
    therefore covers, `rebate_income` — exactly where the feed sources put the same dollars). With
    `sections` (the chart's {line_key: section}) a key the chart does not have is left out — it can
    book nothing, so it can suppress nothing. This is the ONLY definition of 'which lines the ledger
    covers'; the feed-booking suppression is derived from it, never listed."""
    out = set()
    for b in _cl.active_buckets(buckets):
        k = str(b.get("pl_line_key") or "").strip()
        if not k:
            continue
        if sections is not None and k not in sections:
            continue
        out.add(k)
        out.add(route_line(k, sections, cfg)[0])
    return sorted(out)


# ── 3. THE BOOKING — through the ledger's own summarizer, to the registry's line ────────────────
def line_sign(section):
    """PURE. +1 on a revenue line (the bucket's signed total as-is), −1 on a COGS / opex line (a
    deduction bucket's negative total becomes positive expense; an earned rebate becomes contra-COGS)."""
    return 1 if str(section or "").strip().lower() == "revenue" else -1


def route_line(line_key, sections, cfg=None):
    """PURE. (line_key, sign) a bucket's total books with. The rebate family goes through THE resolved
    rebate route (ma_store_pnl.rebate_route — contra-COGS by default, 'income' flips line and sign),
    so this source can never present a rebate differently from the feed sources. Everything else
    takes the chart's own sign for its section."""
    if line_key in _msp.REBATE_LINES:
        return _msp.rebate_route(cfg)
    return line_key, line_sign((sections or {}).get(line_key))


def _store_of(r):
    s = str((r or {}).get("store") or "").strip()
    return s or None


def ledger_bookings(rows, buckets, sections, cfg=None):
    """PURE. The period's ledger rows + the org's merged buckets + the chart's {line: section} →
      {"bookings": [(line, store, amount, detail_label, bucket_key), …] — what coa adds, per store,
       "by_line": {line: Σ amount} (whole period, every store),
       "by_bucket": {bucket: {"total", "line", "label", "kind"}},
       "by_source_report": {source_report: Σ payout_total} (overlap between statements stays visible),
       "unbooked": [{"what", "amount", "reason", "lines"}, …] — money that reached no line, and why,
       "line_count": n}
    The amounts are `commission_ledger.summarize`'s — this function never sums a payout_total."""
    rows = list(rows or [])
    groups, order = {}, []
    for r in rows:
        st = _store_of(r)
        if st not in groups:
            groups[st] = []
            order.append(st)
        groups[st].append(r)
    bookings, by_line, by_bucket, unbooked = [], {}, {}, {}
    by_src = {}
    for st in order:
        s = _cl.summarize(groups[st], buckets=buckets)
        for cat, c in s["categories"].items():
            total = _money(c.get("total"))
            if not total:
                continue
            line = str(c.get("pl_line_key") or "").strip()
            meta = by_bucket.setdefault(cat, {"total": 0.0, "line": line or None, "label": c.get("label"),
                                              "kind": c.get("kind")})
            meta["total"] = _money(meta["total"] + total)
            if not line:
                u = unbooked.setdefault(("bucket", cat), {"what": c.get("label") or cat, "amount": 0.0,
                                                          "reason": UNBOOKED_NO_LINE, "lines": 0})
                u["amount"], u["lines"] = _money(u["amount"] + total), u["lines"] + int(c.get("count") or 0)
                continue
            if line not in (sections or {}):
                u = unbooked.setdefault(("bucket", cat), {"what": c.get("label") or cat, "amount": 0.0,
                                                          "reason": f"{UNBOOKED_UNKNOWN_LINE} ('{line}')",
                                                          "lines": 0})
                u["amount"], u["lines"] = _money(u["amount"] + total), u["lines"] + int(c.get("count") or 0)
                continue
            ln, sign = route_line(line, sections, cfg)
            amt = _money(sign * total)
            if not amt:
                continue
            bookings.append((ln, st, amt, f"{c.get('label') or cat}{DETAIL_SUFFIX}", cat))
            by_line[ln] = _money(by_line.get(ln, 0.0) + amt)
        if s.get("other_total"):
            u = unbooked.setdefault(("other",), {"what": "Other payout (unmapped)", "amount": 0.0,
                                                 "reason": UNBOOKED_UNMAPPED, "lines": 0})
            u["amount"] = _money(u["amount"] + _money(s["other_total"]))
            u["lines"] += int(s.get("other_count") or 0)
        if s.get("unlisted_total"):
            u = unbooked.setdefault(("unlisted",), {"what": "Bucket no longer listed", "amount": 0.0,
                                                    "reason": UNBOOKED_UNLISTED, "lines": 0})
            u["amount"] = _money(u["amount"] + _money(s["unlisted_total"]))
            u["lines"] += int(s.get("unlisted_count") or 0)
    # per-statement roll-up: the ledger's own net per source_report (summarize's payout_total), so a
    # period holding the same money under two templates / origins is VISIBLE on the P&L drill-down
    by_report = {}
    for r in rows:
        by_report.setdefault(str(r.get("source_report") or ""), []).append(r)
    for k, rs in by_report.items():
        by_src[k] = _cl.summarize(rs, buckets=buckets)["payout_total"]
    return {"bookings": bookings, "by_line": by_line, "by_bucket": by_bucket,
            "by_source_report": by_src, "unbooked": list(unbooked.values()), "line_count": len(rows)}


# ── 4. THE GUARD ─────────────────────────────────────────────────────────────────────────────────
def double_booked(feed_booked_lines, ledger_booked_lines):
    """PURE. The lines that received BOTH a commission-FEED booking and a ledger booking — must be
    empty under 'ledger' (the guarded adder drops every feed booking on a covered line, and the
    ledger books only covered lines, so the intersection is empty by construction). coa raises on a
    non-empty answer; the harness proves it goes RED when a ledger booking is forced onto a line the
    feeds also booked."""
    return sorted(set(feed_booked_lines or ()) & set(ledger_booked_lines or ()))


# ── 5. THE DIVERGENCE — in numbers and in words, per line ───────────────────────────────────────
def _fmt(x):
    x = _money(x)
    return f"-${abs(x):,.2f}" if x < 0 else f"${x:,.2f}"


def divergence(feed_by_line, ledger_by_line, source, covered, configured=None, unbooked=None,
               by_source_report=None, ledger_line_count=0, config_columns_missing=None):
    """PURE. {line: commission_source-meta} for every line either source names. Under 'ledger' a
    covered line says what the ledger booked and what the feeds WOULD have booked (suppressed); under
    'feeds' a line the ledger also holds says what the feeds booked and what the ledger holds —
    the gap a migrating tenant reads before flipping the switch. A line only the feeds book, in an
    org whose ledger is empty, gets NO meta (the payload stays byte-identical).
    `config_columns_missing` (the reader's report, 2026-09-22): when the switch column itself is
    absent the words SAY so and name the migration — the read side says what the save side refuses —
    and `switch_ready` is False; otherwise the meta is byte-identical to before."""
    feed_by_line = {k: _money(v) for k, v in (feed_by_line or {}).items()}
    ledger_by_line = {k: _money(v) for k, v in (ledger_by_line or {}).items()}
    covered = set(covered or ())
    switch_ready = CONFIG_COLUMN not in set(config_columns_missing or ())
    out = {}
    lines = set(ledger_by_line) | (covered if source == SOURCE_LEDGER else set())
    if ledger_by_line:
        lines |= set(feed_by_line)
    for line in sorted(lines):
        f, l = feed_by_line.get(line, 0.0), ledger_by_line.get(line, 0.0)
        booked_from_ledger = source == SOURCE_LEDGER and line in covered
        diff = _money(l - f)
        if booked_from_ledger:
            if f:
                words = (f"Booked from the Commission Ledger ({_fmt(l)}). The feed tables would have booked "
                         f"{_fmt(f)} this month — a difference of {_fmt(diff)}; the feed booking is switched off "
                         "on this line so nothing is counted twice.")
            else:
                words = (f"Booked from the Commission Ledger ({_fmt(l)}). The feed tables hold nothing for "
                         "this line this month.")
        elif source == SOURCE_LEDGER:
            words = (f"Booked from the feed tables ({_fmt(f)}): no bucket in the registry is linked to this "
                     "line, so the ledger does not cover it.")
        else:
            if l:
                words = (f"Booked from the feed tables ({_fmt(f)}). The Commission Ledger holds {_fmt(l)} for "
                         f"this line this month — a difference of {_fmt(diff)}. Switch the P&L commission "
                         "source to the ledger to book it from there.")
            else:
                words = f"Booked from the feed tables ({_fmt(f)}). The Commission Ledger holds nothing for this line this month."
        if not switch_ready:
            words += (f" The P&L commission-source switch column ({CONFIG_COLUMN}) is not applied on this "
                      f"database yet — apply migration {MIGRATION}; until then the feed tables book this line.")
        out[line] = {"source": SOURCE_LEDGER if booked_from_ledger else SOURCE_FEEDS,
                     "configured": str(configured or SOURCE_FEEDS),
                     "switch_ready": switch_ready,
                     "feeds": f, "ledger": l, "difference": diff,
                     "suppressed": _money(f) if booked_from_ledger else 0.0,
                     "words": words,
                     "ledger_lines": int(ledger_line_count or 0),
                     "by_source_report": dict(by_source_report or {}),
                     "unbooked": list(unbooked or []) if source == SOURCE_LEDGER else []}
    return out


# ── 6. WHERE IT SHOWS UP — the ledger's P&L lines for "this statement will show in" ─────────────
def pl_lines_for(buckets, spec_labels, line_labels=None, sections=None, cfg=None):
    """PURE. [{key, label, buckets:[labels]}] — the P&L lines the org's active buckets book to (the
    registry line, ROUTED through `route_line` so the rebate family shows where the org presents
    rebates), in registry order, each once, labelled by the chart (`coa.PL_LABEL`) with the org's own
    `pl_line_labels` override. What ShowsIn renders after 'P&L Statement →'."""
    order, by_key = [], {}
    for b in _cl.active_buckets(buckets):
        k = str(b.get("pl_line_key") or "").strip()
        if not k or (sections is not None and k not in sections):
            continue
        k = route_line(k, sections, cfg)[0]
        if k not in by_key:
            lbl = (line_labels or {}).get(k) or (spec_labels or {}).get(k) or k
            by_key[k] = {"key": k, "label": lbl, "buckets": []}
            order.append(k)
        by_key[k]["buckets"].append(b.get("label") or b.get("key"))
    return [by_key[k] for k in order]


def pl_link(buckets, spec_labels, line_labels=None, sections=None, configured=None, cfg=None):
    """PURE. The decoration `landing_identity.shows_in` attaches to the ledger's P&L consumer:
    the lines, the configured source and whether the ledger books the P&L today."""
    c = str(configured or SOURCE_FEEDS)
    return {"lines": pl_lines_for(buckets, spec_labels, line_labels, sections, cfg),
            "source": c, "active": c in (SOURCE_LEDGER, SOURCE_LEDGER_ELSE_FEEDS),
            "source_label": SOURCE_LABELS.get(c, c)}


# ── 7. THE SUGGESTION (offered, never applied) ──────────────────────────────────────────────────
def suggest_source(configured, feed_tables_with_rows, ledger_line_count):
    """PURE. What the settings panel proposes: 'ledger' when the org has ledger lines and NO feed table
    populated (the obvious answer — confirmed by a person, never switched silently); nothing when both
    exist (compare on the P&L drill-down first) or when the ledger is empty."""
    feeds = sorted(feed_tables_with_rows or [])
    n = int(ledger_line_count or 0)
    c = str(configured or SOURCE_FEEDS)
    if n and not feeds:
        if c == SOURCE_LEDGER:
            return {"value": None, "why": "Already booking from the Commission Ledger, and no feed table holds rows."}
        return {"value": SOURCE_LEDGER,
                "why": (f"The Commission Ledger holds {n:,} line(s) and none of the feed tables holds a row, "
                        "so the ledger is the only place your commission exists — the P&L reads nothing "
                        "until the source is the ledger.")}
    if n and feeds:
        return {"value": None,
                "why": (f"Both exist: the Commission Ledger holds {n:,} line(s) and {', '.join(feeds)} hold rows. "
                        "Open the P&L — each commission line shows what the feeds booked and what the ledger "
                        "holds, with the difference — before switching.")}
    return {"value": None, "why": "The Commission Ledger holds no lines yet; the feed tables book the P&L."}


# ── 8. I/O — the only two readers ───────────────────────────────────────────────────────────────
# The ledger is a wide, hot table, so it keeps an explicit column list; the OPTIONAL column (`origin`,
# mig 251) is probed on its own before the one real select (core.column_tolerant, §4b.1) — never a
# block whose failure on one column drops another.
_LEDGER_REQUIRED = ("category", "payout_total", "store", "source_report", "is_payout", "raw_amount",
                    "payment_month", "product_name", "period")
_LEDGER_OPTIONAL = ("origin",)


def load_ledger_rows(client, org_id, period_keys, page=1000, cap=200000):
    """I/O, org-scoped, paginated: the ledger's lines for the period under EVERY spelling of it
    (`_period.period_keys`). `origin` (mig 251) is read when the column exists. NEVER raises — a
    missing table (mig 071 not applied) is an empty ledger."""
    keys = list(period_keys or [])
    if not keys:
        return []
    table = lambda: client.schema("commcalc").table(_cl.LEDGER_TABLE)      # noqa: E731
    scope = lambda q: q.eq("org_id", org_id)                                # noqa: E731
    out, start = [], 0
    try:
        cols = _ct.select_list(_LEDGER_REQUIRED, _LEDGER_OPTIONAL,
                               _ct.present_columns(table, scope, _LEDGER_OPTIONAL))
        while start < cap:
            chunk = (scope(table().select(cols)).in_("period", keys)
                     .range(start, start + page - 1).execute().data) or []
            out.extend(chunk)
            if len(chunk) < page:
                break
            start += page
        return out
    except Exception:
        return []


def load_source_meta(client, org_id):
    """I/O, org-scoped: {configured, ready, migration, config_columns_missing,
    config_migrations_missing} — the settings panel's read-back. ONE reader: this dereferences
    `ma_store_pnl.load_config` (which reads the row whole and reports what is absent), so the panel
    and the P&L can never disagree about what the org configured. `ready` is False exactly when the
    mig-1013 column is absent. The RESOLVED value for a period still comes from `resolve_source`.
    NEVER raises."""
    cfg = _msp.load_config(client, org_id)
    missing = list(cfg.get("config_columns_missing") or [])
    return {"configured": cfg.get("commission_source") or SOURCE_FEEDS,
            "ready": CONFIG_COLUMN not in missing, "migration": MIGRATION,
            "config_columns_missing": missing,
            "config_migrations_missing": list(cfg.get("config_migrations_missing") or [])}


def load_source_evidence(client, org_id, ledger_probe=5000):
    """I/O, org-scoped: which feed tables hold ANY row for the org, and how many ledger lines it has
    (probed up to `ledger_probe`) over which periods — the evidence behind the suggestion. NEVER raises."""
    feeds = {}
    for t in FEED_TABLES:
        try:
            rows = (client.schema("commcalc").table(t).select("org_id").eq("org_id", org_id)
                    .limit(1).execute().data) or []
            feeds[t] = bool(rows)
        except Exception:
            feeds[t] = False
    periods, n = {}, 0
    try:
        rows = (client.schema("commcalc").table(_cl.LEDGER_TABLE).select("period")
                .eq("org_id", org_id).limit(ledger_probe).execute().data) or []
        n = len(rows)
        for r in rows:
            p = str(r.get("period") or "(no period)")
            periods[p] = periods.get(p, 0) + 1
    except Exception:
        pass
    return {"feeds": feeds, "feed_tables_with_rows": sorted(t for t, ok in feeds.items() if ok),
            "ledger_lines": n, "ledger_lines_truncated": n >= ledger_probe,
            "ledger_periods": [{"period": p, "lines": c} for p, c in sorted(periods.items())]}
