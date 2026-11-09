"""ONE read for "what money is expected to COME IN, and on which day" — the inbound mirror of
`payables/owed_read.py`.

Index §COMMCALC-56. Lock: `backend/harness_cash_calendar.py`.

WHY THIS EXISTS, and it is not tidiness. The outbound half of the owner's question already has one
home: `owed_read.dated_owed` is read by the Daily Owed surface and by the weekly roll-up alike, so the
week cannot total something the day disagrees with (§COMMCALC-34). The inbound half had no home at
all, and the obvious build gives the new calendar its own read of whichever commission feed the house
org happens to use. That is two defects in one commit:

  · a SECOND derivation of "what has the carrier not paid us yet", free to drift from the commission
    surfaces that already answer it — the duplicate defect CLAUDE.md makes a build gate; and
  · ONE TENANT'S FEED standing in for every tenant's. Measured 2026-11-09: `raw_payment_detail` holds
    rows for the house org only, and `raw_ma_commission` for the processor-fed tenant only. A reader
    that named either table would have told the other tenant it is expecting nothing — the exact
    shape of the 1,034-handsets-read-as-zero defect behind CLAUDE.md's "Every feature is for EVERY
    tenant" rule.

So the source is DECLARED per tenant, on `commcalc.receipt_source_map`, and this module reads the
declaration rather than knowing any feed's name.

WHAT IS DECLARED, AND WHAT IS DEREFERENCED
──────────────────────────────────────────
DECLARED by the tenant (one row per inbound money source):
  · `source_table`   — which relation states money the carrier/processor will pay us;
  · `amount_field`   — which column on it is the money;
  · the payout rhythm — read by `receipt_schedule.resolve_schedule`, which rules on it.

DEREFERENCED, never declared twice:
  · **which column dates a row** — `data_lineage_registry.DATA_DATE_COLUMN_BY_TABLE`, which has been
    the one home for that fact since the feed watchdog shipped. A second copy here would be the
    §19.18 divergence, and that registry's `None` entries are DECLARATIONS: a feed the registry says
    carries no single dating column cannot be a receipt source, and is refused rather than guessed
    around. A table the registry has never heard of is refused for the same reason.
  · **the store identity and the market** — the CALLER injects its resolvers, exactly as `owed_read`
    takes them, so §64 and RULE FIVE keep their own homes instead of gaining copies here.

A DECLARED SOURCE THAT CANNOT BE READ IS REPORTED (§COMMCALC-29). The two outcomes "this tenant
declared nothing" and "this tenant's declaration names a column that does not exist" are the same
empty table on screen and must never be the same answer in the payload. `declared` and `error` are
separate fields for exactly that reason, and an unreadable declaration is `declared: True` with the
failure in words.

AN EMPTY RESULT IS NEVER A ZERO. `measured` is False where nothing could be placed on a day, and
`note` says which half of the setup is missing. A $0.00 total here would read as "the carrier owes us
nothing", which is a sentence about the carrier, while the truth is a sentence about the setup.

NO CARRIER, TENANT OR PRODUCT NAME (RULE TWO), and no feed name of its own.
"""
from app.modules.commcalc import data_lineage_registry as _lineage
from app.modules.commcalc import date_window
from app.modules.payables import receipt_schedule as _rs

#: The tenant's declaration of its own inbound money sources.
SOURCE_MAP = "receipt_source_map"

GRAIN_STATEMENT_LINE = "statement_line"

PAGE = 1000
#: A hard stop so a runaway page loop cannot hang a request. Reached means INCOMPLETE, and the caller
#: is told rather than handed a short total that looks whole.
MAX_PAGES = 60

#: The one place each setup state is put into words.
NOT_DECLARED_NOTE = ("No inbound money source is declared for this tenant, so there is nothing to "
                     "expect on a day. Declare which feed states what the carrier will pay us, "
                     "which column is the money, and the payout cycle.")
NO_TABLE_NOTE = ("The table that holds these declarations does not exist yet, so nothing can have "
                 "been declared. Apply migration 1115_receipt_source_map.sql, then declare each "
                 "tenant's inbound money source and payout cycle.")
NO_DATE_COLUMN_NOTE = ("The declared source has no column that names the day a row is about, so its "
                       "money cannot be placed on a calendar. Declare a day-grain source instead.")
UNREADABLE_NOTE = ("An inbound money source IS declared for this tenant but could not be read. This "
                   "is a setup fault, not an empty month — the figures below are incomplete.")
INCOMPLETE_NOTE = ("More rows exist than this read will page through, so the total below is a floor "
                   "rather than the whole amount.")


def earning_date_field(source_table):
    """Which column on `source_table` names the day its row is about — DEREFERENCED from the lineage
    registry, which is that fact's one home.

    Returns None for a relation the registry has never heard of AND for one it declares has no single
    dating column. Both are refusals rather than guesses: the registry's `None` is a stated fact, and
    an unregistered table is one nobody has stated anything about. A reader that fell back to a
    literal column name would be the second copy this function exists to prevent.
    """
    tbl = str(source_table or "").strip()
    if not tbl:
        return None
    return _lineage.DATA_DATE_COLUMN_BY_TABLE.get(tbl) or None


def declared_sources(client, org_id):
    """The tenant's ACTIVE inbound money declarations, and whether the declaration table is reachable.

    Returns {rows, error, table_missing}. Nothing raises: a platform where the migration has not been
    applied must REPORT that, not 500 on a report.

    THE TABLE BEING ABSENT IS NOT A BAD DECLARATION, and the two must not collapse into one answer.
    If the declaration table does not exist, nobody has been able to declare anything — so that is
    `declared: False` with the migration named, the same answer as a tenant who simply has not set up
    yet. A declaration that EXISTS and cannot be read is the §COMMCALC-29 case and stays
    `declared: True`.
    """
    try:
        rows = (client.schema("commcalc").table(SOURCE_MAP)
                .select("label,source_table,amount_field,payout_cadence,payout_weekday,"
                        "payout_days,payout_day_of_month,payout_net_days,is_active")
                .eq("org_id", org_id).limit(200).execute().data or [])
    except Exception as exc:                      # noqa: BLE001 — a setup fault is reported, not raised
        return {"rows": [], "table_missing": True,
                "error": "%s.%s is not readable: %s" % ("commcalc", SOURCE_MAP, exc)}
    # An INACTIVE declaration is not a declaration: a tenant can switch one off without deleting the
    # row that records what it was.
    return {"rows": [r for r in rows if r.get("is_active") is not False],
            "error": None, "table_missing": False}


def _source_rows(client, org_id, src, win, date_field):
    """Every row of ONE declared source inside the window, paged. Raises on a bad declaration, which
    the caller turns into a reported setup fault."""
    table = str(src.get("source_table") or "").strip()
    amount_field = str(src.get("amount_field") or "").strip()
    cols = ",".join(sorted({date_field, amount_field, "business_address"}))
    out, page, truncated = [], 0, False
    while True:
        q = (client.schema("commcalc").table(table).select(cols).eq("org_id", org_id))
        if win:
            q = date_window.apply_window(q, date_field, win)
        got = q.order(date_field).range(page * PAGE, (page + 1) * PAGE - 1).execute().data or []
        out.extend(got)
        if len(got) < PAGE:
            break
        page += 1
        if page >= MAX_PAGES:
            truncated = True
            break
    return out, truncated


def dated_receipts(client, org_id, win, *, want_store=None, want_markets=None, market_of=None,
                   store_field="business_address"):
    """The money expected to come IN, grouped onto the DAYS the declared payout cycle says it lands.

    Returns {days, total, source, sources, grain, declared, measured, error, note, unplaced_rows,
    unplaced_amount, bases}.

    `want_store` / `market_of` are the CALLER'S resolvers — store identity (§64) and the market home
    (RULE FIVE) keep their own homes rather than gaining copies here, the same contract
    `owed_read.ledger_rows` has.

    ORG-SCOPED ON EVERY READ, including the declaration itself. The cross-tenant leak this guards
    against is not hypothetical: two tenants' rows sit in the same physical tables, so an unscoped
    read of a declared source would hand one tenant another's money.
    """
    dec = declared_sources(client, org_id)
    blank = {"days": [], "total": None, "source": None, "sources": [], "grain": None,
             "measured": False, "unplaced_rows": 0, "unplaced_amount": 0.0, "bases": {},
             "table_missing": bool(dec.get("table_missing"))}
    if dec.get("table_missing"):
        return dict(blank, declared=False, error=dec["error"], note=NO_TABLE_NOTE)
    if dec["error"]:
        return dict(blank, declared=True, error=dec["error"], note=UNREADABLE_NOTE)
    if not dec["rows"]:
        return dict(blank, declared=False, error=None, note=NOT_DECLARED_NOTE)

    by_day, bases, errors, notes = {}, {}, [], []
    answered, unplaced_n, unplaced_amt, truncated = [], 0, 0.0, False

    for src in dec["rows"]:
        table = str(src.get("source_table") or "").strip()
        amount_field = str(src.get("amount_field") or "").strip()
        date_field = earning_date_field(table)
        if not table or not amount_field:
            errors.append("a declaration is missing its source table or amount column")
            continue
        if not date_field:
            errors.append("%s: %s" % (table, NO_DATE_COLUMN_NOTE))
            continue
        try:
            rows, trunc = _source_rows(client, org_id, src, win, date_field)
        except Exception as exc:                  # noqa: BLE001 — §COMMCALC-29: reported, never swallowed
            errors.append("%s.%s could not be read: %s" % (table, amount_field, exc))
            continue
        truncated = truncated or trunc

        kept = []
        for r in rows:
            st = (r.get(store_field) or "").strip()
            if want_store and not want_store(st):
                continue
            if want_markets is not None:
                mkt = market_of(st) if market_of else None
                if str(mkt or "").lower() not in want_markets:
                    continue
            kept.append(r)

        schedule = _rs.resolve_schedule(src)
        roll = _rs.roll_up(kept, schedule, date_field=date_field,
                           amount_of=lambda r, _f=amount_field: r.get(_f))
        answered.append({"label": str(src.get("label") or table), "source_table": table,
                         "cadence": roll["cadence"], "cadence_declared": roll["cadence_declared"],
                         "total": roll["total"], "measured": roll["measured"],
                         "unplaced_amount": roll["unplaced_amount"], "note": roll["note"]})
        if roll["note"]:
            notes.append(roll["note"])
        unplaced_n += roll["unplaced_rows"]
        unplaced_amt += roll["unplaced_amount"]
        for b, n in roll["bases"].items():
            bases[b] = bases.get(b, 0) + n
        for d in roll["days"]:
            e = by_day.setdefault(d["date"], {"date": d["date"], "count": 0, "amount": 0.0,
                                              "basis": d["basis"], "label": d["label"]})
            e["count"] += d["count"]
            e["amount"] += d["amount"]
            # Two sources landing on one day is one day with two rhythms behind it. The day names the
            # bases it really has rather than keeping whichever arrived first.
            if e["basis"] != d["basis"]:
                e["basis"] = "mixed"
                e["label"] = "more than one payout cycle lands today"

    days = [dict(v, amount=round(v["amount"], 2)) for v in by_day.values()]
    days.sort(key=lambda x: x["date"])
    if truncated:
        notes.append(INCOMPLETE_NOTE)
    note = next((n for n in notes if n), None)
    if errors and not note:
        note = UNREADABLE_NOTE
    measured_any = any(s["measured"] for s in answered)
    return {"days": days,
            # A total is a figure only where something could be measured. Under a complete
            # declaration an empty read really is $0.00; otherwise it is None, never a zero.
            "total": (round(sum(d["amount"] for d in days), 2) if measured_any else None),
            # The single `source` names the relation that answered when exactly one did, so the
            # existing "the response names its source" contract holds; `sources` carries them all.
            "source": (answered[0]["source_table"] if len(answered) == 1 and answered[0]["measured"]
                       else (answered[0]["source_table"] if len(answered) == 1 and days else None)),
            "sources": answered, "grain": (GRAIN_STATEMENT_LINE if days else None),
            # MEASURED means a declaration could date a dollar, NOT that one happened to be there.
            # A store filter that matches nothing is a real $0.00 under a complete declaration; an
            # undeclared (or incompletely declared) carrier is an unknown. Those are different
            # answers and the surface prints them differently.
            "declared": True, "measured": any(s["measured"] for s in answered),
            "table_missing": False,
            "error": ("; ".join(errors) if errors else None), "note": note,
            "unplaced_rows": unplaced_n, "unplaced_amount": round(unplaced_amt, 2), "bases": bases}
