"""Proof harness: merchant-portal adapters — report payload in, normalized settlement rows out.

WHY THIS EXISTS. The three merchant portals (PayAnywhere/Payments Hub, TransFirst TransLink,
ClientLine/BusinessTrack) sit behind a 2FA'd login nobody can reach from CI. So the part of the
integration that CAN be proven without the portal is proven here, exhaustively: given the report payload
the portal exports, do we produce the right rows? That is where a money bug would live — a totals row
counted as a day, a per-terminal line overwriting a store's total, '(12.34)' read as +12.34, an
MM/DD/YY date read as a YY-MM-DD one.

Fixtures are REPRESENTATIVE export shapes (header wording, banner rows, totals rows, parenthesised
credits, per-terminal splits), not captured tenant data — no credential, no real merchant id, no real
money is in this file.

No DB, no network, no browser. Run:  cd backend && python3 harness_merchant_portals.py
Exit 0 = all green.
"""
import sys

sys.path.insert(0, ".")

from app.modules.commcalc import merchant_portals as mp                # noqa: E402
from app.modules.commcalc import merchant_portal_sweep as sweep        # noqa: E402

PASS = FAIL = 0


def ok(cond, msg):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ✓ %s" % msg)
    else:
        FAIL += 1
        print("  ✗ %s" % msg)


def eq(got, want, msg):
    ok(got == want, "%s (got %r, want %r)" % (msg, got, want) if got != want else msg)


# ── FIXTURES ─────────────────────────────────────────────────────────────────────────────────────
# PayAnywhere / Payments Hub — "transactions by card type", one line per brand per day, a $ format,
# a parenthesised refund column, and the trailing TOTAL row every portal export carries.
PAYANYWHERE_CARD_CSV = """Payments Hub - Transactions by Card Type
Generated 09/03/2026

Business Date,Merchant ID,DBA,Card Type,Sales Amount,Refunds,Net Amount,Fees,Transaction Count
09/01/2026,MID10001,STORE ONE,Visa,"$1,240.50",(40.00),"$1,200.50",$32.15,18
09/01/2026,MID10001,STORE ONE,MasterCard,$860.25,0.00,$860.25,$22.40,11
09/01/2026,MID10001,STORE ONE,Amex,$310.00,0.00,$310.00,$12.10,3
09/02/2026,MID10001,STORE ONE,Visa,$980.00,(15.50),$964.50,$25.00,14
09/01/2026,MID10002,STORE TWO,Visa,$540.00,0.00,$540.00,$14.00,7
TOTAL,,,,"$3,930.75",(55.50),"$3,875.25",$105.65,53
"""

# TransFirst TransLink — batch summary, per TERMINAL, so one (merchant, day, brand) arrives as several
# lines that MUST be summed, not overwritten. Different header wording, MM-DD-YYYY dates.
TRANSFIRST_BATCH_CSV = """Merchant Number,Terminal ID,Batch Date,Card Brand,Gross Sales,Returns,Fees,Trans Count,Batch Number
7001234,T01,09-01-2026,VI,500.00,0.00,12.00,6,B900
7001234,T02,09-01-2026,VI,300.00,25.00,7.50,4,B901
7001234,T01,09-01-2026,MC,220.00,0.00,5.50,3,B900
7001234,T01,09-02-2026,VI,410.00,0.00,10.25,5,B910
"""

# ClientLine / BusinessTrack — funding report (a DIFFERENT grain: money to the bank), semicolon
# delimited with a trailing-minus negative, which is a real export convention.
BUSINESSTRACK_FUNDING_CSV = """Outlet ID;DBA Name;Funding Date;Batch Reference;Amount Funded;Total Fees;Items
417700;STORE ONE;2026-09-02;F55010;1875.25;105.65;53
417700;STORE ONE;2026-09-03;F55011;964.50;25.00;14
417701;STORE TWO;2026-09-02;F55012;540.00-;14.00;7
"""

PA_SPEC = {"key": "card_summary", "label": "Transactions by card type", "grain": "store_day_brand"}
TF_SPEC = {"key": "batch_summary", "label": "Batch summary", "grain": "store_day_brand"}
BT_SPEC = {"key": "funding", "label": "Funding / deposits", "grain": "batch"}


def main():
    print("\n1. Portal registry + config resolution (RULE TWO — role is config, not a code branch)")
    eq(sorted(mp.PORTAL_KEYS), ["businesstrack", "payanywhere", "transfirst"], "three portals registered")
    eq(mp.settlement_role("payanywhere"), mp.ROLE_EXTERNAL,
       "PayAnywhere defaults to external_cc (the standalone terminal / 'white machine')")
    eq(mp.settlement_role("transfirst"), mp.ROLE_POS, "TransFirst defaults to pos_merchant")
    eq(mp.settlement_role("businesstrack"), mp.ROLE_POS, "ClientLine defaults to pos_merchant")
    eq(mp.settlement_role("transfirst", "external_cc"), mp.ROLE_EXTERNAL,
       "a per-source override wins over the portal default")
    eq(mp.settlement_role("transfirst", "nonsense"), mp.ROLE_POS,
       "an illegal override falls back to the house default rather than inventing a role")
    cat = mp.public_catalog()
    # `login_fields` lists which boxes the login form needs (field NAMES like "password"), which is
    # exactly what the settings UI must render. What must never appear is a key that HOLDS a secret.
    ok(all(not ({"password", "totp_secret", "session_state", "username_value"} & set(e))
           for e in cat),
       "the public catalog exposes no secret-bearing key (only descriptors + field names)")
    ok(all(set(e["login_fields"]) <= {"username", "password", "account_id"} for e in cat),
       "login_fields names only data_source columns that already exist — no new credential store")
    eq([r["key"] for r in mp.report_specs("payanywhere", ["deposits"])], ["deposits"],
       "per-source enabled-report filter selects a subset")
    eq(len(mp.report_specs("payanywhere")), 2, "no filter ⇒ every report for the portal")

    print("\n2. Pure value readers (the money-bug surface)")
    eq(mp.money("$1,240.50"), 1240.50, "currency + thousands separator")
    eq(mp.money("(40.00)"), -40.0, "parenthesised credit is NEGATIVE")
    eq(mp.money("540.00-"), -540.0, "trailing-minus is NEGATIVE")
    eq(mp.money(""), 0.0, "blank ⇒ 0")
    eq(mp.money("N/A"), 0.0, "unparseable ⇒ 0, never a crash")
    eq(mp.iso_date("09/01/2026"), "2026-09-01", "MM/DD/YYYY")
    eq(mp.iso_date("09-01-26"), "2026-09-01", "MM-DD-YY")
    eq(mp.iso_date("2026-09-01"), "2026-09-01", "ISO")
    eq(mp.iso_date("Sep 1, 2026"), "2026-09-01", "month-name")
    eq(mp.iso_date("TOTAL"), None, "a totals label is NOT a date")
    eq(mp.card_brand("VI"), "visa", "portal brand code VI")
    eq(mp.card_brand("MC"), "mastercard", "portal brand code MC")
    eq(mp.card_brand("American Express"), "amex", "long brand name")
    eq(mp.card_brand("Frobnicator"), "unknown",
       "an unknown brand is kept as 'unknown', never dropped from the day's total")

    print("\n3. PayAnywhere card-type report → settlement rows")
    res = sweep.parse_report("payanywhere", PA_SPEC, PAYANYWHERE_CARD_CSV,
                             src_row={"id": "src-pa", "org_id": "org-1"})
    rows = res["rows"]
    eq(len(rows), 5, "5 data rows parsed (banner + header + TOTAL row excluded)")
    ok(any(s["reason"] == "totals row" for s in res["skipped"]),
       "the TOTAL row is skipped with a reason, not counted as a business day")
    r0 = rows[0]
    eq(r0["business_date"], "2026-09-01", "date normalized")
    eq(r0["merchant_id"], "MID10001", "the portal's OWN merchant id is kept")
    eq(r0["card_brand"], "visa", "brand canonicalized")
    eq(r0["gross_amount"], 1240.50, "gross parsed")
    eq(r0["refund_amount"], 40.0, "refund stored as a positive magnitude")
    eq(r0["net_amount"], 1200.50, "the portal's own net is trusted when published")
    eq(r0["fee_amount"], 32.15, "fees parsed")
    eq(r0["txn_count"], 18, "count parsed")
    eq(r0["settlement_role"], mp.ROLE_EXTERNAL, "rows carry the external_cc role the closing recon filters on")
    ok(r0["raw"].get("Card Type") == "Visa", "the export row is kept verbatim in raw for traceability")
    ok(r0["store_code"] is None, "store_code is left for the canonical resolver, never guessed here")

    print("\n4. Net is DERIVED only when the portal publishes none")
    derived = sweep.parse_report("transfirst", TF_SPEC, TRANSFIRST_BATCH_CSV,
                                 src_row={"id": "src-tf", "org_id": "org-1"})["rows"]
    t02 = [r for r in derived if r["terminal_id"] == "T02"][0]
    eq(t02["gross_amount"], 300.0, "TransFirst gross")
    eq(t02["refund_amount"], 25.0, "TransFirst returns")
    eq(t02["net_amount"], 275.0, "no net column ⇒ net = gross − |returns|")

    print("\n5. Per-terminal lines SUM into the day (the overwrite bug this guards)")
    deduped = mp.dedupe_settlement(derived)
    sep1_visa = [r for r in deduped if r["business_date"] == "2026-09-01" and r["card_brand"] == "visa"]
    eq(len(sep1_visa), 1, "T01 + T02 Visa on 09-01 collapse to ONE row (the table's grain is the day)")
    eq(sep1_visa[0]["gross_amount"], 800.0, "gross SUMMED (500 + 300), not overwritten by the last line")
    eq(sep1_visa[0]["net_amount"], 775.0, "net summed (500 + 275)")
    eq(sep1_visa[0]["txn_count"], 10, "counts summed")
    eq(sorted(sep1_visa[0]["merged_from"]), ["T01", "T02"],
       "both terminals' identifiers survive the merge")
    eq(len(deduped), 3, "09-01 visa, 09-01 mc, 09-02 visa")

    print("\n6. ClientLine funding report → BATCH rows (a different grain, kept apart)")
    bres = sweep.parse_report("businesstrack", BT_SPEC, BUSINESSTRACK_FUNDING_CSV,
                              src_row={"id": "src-bt", "org_id": "org-1"})
    brows = bres["rows"]
    eq(len(brows), 3, "semicolon-delimited export parsed")
    eq(brows[0]["deposit_date"], "2026-09-02", "funding date normalized")
    eq(brows[0]["batch_ref"], "F55010", "the portal's own batch reference is kept")
    eq(brows[0]["deposit_amount"], 1875.25, "funded amount parsed")
    eq(brows[2]["deposit_amount"], -540.0, "a trailing-minus funding is negative (a debit, not a credit)")
    ok("deposit_amount" in brows[0] and "card_brand" not in brows[0],
       "batch rows carry no card brand — they are not settlement rows and cannot be summed into them")

    print("\n7. Header mapping is specific-wins, and calibratable per source")
    fields = mp.map_headers(["Business Date", "Sales Amount", "Net Amount", "Transaction Count"])
    ok(fields["gross_amount"] != fields["net_amount"],
       "'Sales Amount' and 'Net Amount' map to DIFFERENT fields (specific synonym wins over 'amount')")
    eq(fields["business_date"], 0, "date column found")
    custom = mp.map_headers(["Fecha", "Importe Bruto"],
                            {"business_date": ["fecha"], "gross_amount": ["importe bruto"]})
    eq(custom.get("business_date"), 0, "a per-source column synonym resolves an unseen header")
    eq(custom.get("gross_amount"), 1, "…for every field, config not code")

    print("\n8. Unparseable / defensive input never crashes and never invents a day")
    empty = mp.normalize_settlement("payanywhere", [])
    eq(empty["rows"], [], "empty payload ⇒ no rows")
    ok(empty["warnings"], "…and a warning saying so")
    nodate = mp.normalize_settlement("payanywhere",
                                     [["Merchant ID", "Sales Amount"], ["MID1", "$10.00"]])
    eq(nodate["rows"], [], "a report with no date column yields NO rows")
    ok(any("date column" in w for w in nodate["warnings"]),
       "…and says the source needs column calibration, rather than guessing today")

    print("\n9. Store totals — the shape the closing recon reads")
    for r in rows:
        r["store_code"] = {"MID10001": "S1", "MID10002": "S2"}.get(r["merchant_id"])
    tot = mp.totals_by_store_day(rows)
    eq(tot["by_store_day"][("S1", "2026-09-01")]["net"], 2370.75,
       "store S1's 09-01 card total = visa 1200.50 + mc 860.25 + amex 310.00")
    eq(tot["by_store_day"][("S1", "2026-09-01")]["brands"]["amex"], 310.0, "per-brand split preserved")
    eq(tot["by_store_day"][("S2", "2026-09-01")]["net"], 540.0, "store S2 kept separate")
    unmapped = [dict(r, store_code=None) for r in rows]
    tot2 = mp.totals_by_store_day(unmapped)
    eq(tot2["by_store_day"], {}, "rows with no resolved store are EXCLUDED from store totals…")
    eq(len(tot2["unresolved"]), 5, "…and surfaced, so an unmapped MID can never read as $0 for a store")

    print("\n10. Pull window — daily, closed days only, bounded")
    from datetime import date as _d
    d_from, d_to = sweep.date_range({}, "payanywhere", today=_d(2026, 9, 4))
    eq(d_to, "2026-09-03", "the window ends YESTERDAY — today's business day is not final yet")
    eq(d_from, "2026-08-28", "…and re-fetches the portal's restatement window (7 days)")
    eq(sweep.window_days({"portal_window_days": 3}, "payanywhere"), 3, "per-source window override")
    eq(sweep.window_days({"portal_window_days": 9999}, "payanywhere"), sweep.MAX_WINDOW_DAYS,
       "an absurd window is clamped — re-fetching months is how a portal starts rate-limiting us")

    print("\n11. read_table tolerates what portals actually emit")
    eq(sweep.read_table(b"A,B\n1,2\n")[0], ["A", "B"], "bytes payload decoded")
    eq(sweep.read_table("﻿A,B\n1,2\n")[0], ["A", "B"], "UTF-8 BOM stripped")
    eq(len(sweep.read_table(PAYANYWHERE_CARD_CSV)), 7, "banner rows above the header are dropped")
    eq(sweep.read_table(None), [], "None payload ⇒ empty, never a crash")

    print("\n%d passed, %d failed" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
