"""PROOF: a leading address token is not a store identity (index §64).

Owner directive 2026-10-09, verbatim: *"chase trhew street number matching"*.

THE DEFECT, measured live before this harness was written (house org, read-only):
`gp_report.calc_gp_report` joined EVERY money source to a store on `street_num(addr)` — the first
space-separated token of the address. Two real stores lost money to that token every month:

  • the carrier's payment detail writes `116-36 Springfield Blvd Cambria Heights, NY 11411` while
    the roster writes `11636 Springfield Blvd`. Token `116-36` matched no store row, so that
    store's whole carrier income was bucketed and then DISCARDED — $17,287.01 (Jul) / $16,729.12
    (Aug) / $16,277.66 (Sep) / $7,767.39 (Oct-to-date) of payment detail, and the same again on the
    comp report;
  • a relocated store's feed spelling `2778 Mt Ephraim Ave Camden, NJ 08104` against the roster's
    `1598 Mount Ephraim Ave`: aliases existed for three other spellings but not that one, so the
    money went nowhere the month the POS feed stopped using the old spelling;
  • and because `{token: row}` was LAST-WINS, three house addresses claimed by TWO `store_mapping`
    rows each resolved to whichever row the loop saw last — in each pair exactly one row carries
    the `salesforce_id` the residual feed joins on, so that store's residual could not be found:
    $8,974.73 (Aug) + $11,832.01 (Sep) of MI/ATU.

THE FIX, and what this file proves: identity comes from ONE home — `account.store_identity`
(`build_store_resolver` / `store_key` / `store_identity_index`), the pure twin behind
`account.coa.store_resolver` — every money source is keyed by it, ambiguity resolves to NOTHING
rather than to a winner, and money nothing can place appears as an explicit row with its reason
instead of vanishing. §E is the NEGATIVE CONTROL: the same fixtures run through the old
leading-token join, proving these are real regressions and not an invented test.

DB-FREE and dependency-free (fixtures only — no credentials, no live read).
Run:  cd backend && python3 harness_gp_store_identity.py
"""
import sys

from app.modules.account import store_identity as sid
from app.modules.commcalc.gp_report import calc_gp_report

P = F = 0


def check(name, cond, detail=""):
    global P, F
    if cond:
        P += 1
        print(f"  ✓ {name}")
    else:
        F += 1
        print(f"  ✗ {name}" + (f"\n      {detail}" if detail else ""))


def head(title):
    print()
    print("=" * 78)
    print(title)
    print("=" * 78)


# ── the org's DECLARED vocabulary (the shape of the live config rows, as fixtures) ───────────────
MAPPING = [
    {"store_code": "S-11634", "store_address": "11636 Springfield Blvd",
     "market": "M1", "salesforce_id": "SF-11634", "is_active": True},
    # a RELOCATION the org never merged: two codes, ONE address, and the carrier's door
    # (salesforce_id) sits on only one of them — the live last-wins trap.
    {"store_code": "S-1598", "store_address": "1598 Mount Ephraim Ave",
     "market": "M2", "salesforce_id": "SF-1598", "is_active": True},
    {"store_code": "S-2778", "store_address": "1598 Mount Ephraim Ave",
     "market": "M2", "salesforce_id": None, "is_active": True},
    {"store_code": "S-9", "store_address": "9 Elm St", "market": "M3",
     "salesforce_id": "SF-9", "is_active": True},
    {"store_code": "S-CLOSED", "store_address": "77 Closed Rd", "market": "M3",
     "salesforce_id": None, "is_active": False},
]
ALIASES = [
    {"alias": "2778 Mount Ephraim Ave", "store_code": "S-1598"},
    {"alias": "2778 Ephraim Ave", "store_code": "S-1598"},
]
RESOLVE = sid.build_store_resolver(MAPPING, ALIASES)

head("A. THE CHAIN — every declared spelling, and nothing else")
check("A1 exact address", RESOLVE("11636 Springfield Blvd") == "11636 Springfield Blvd")
check("A2 exact alias → the code's canonical address",
      RESOLVE("2778 Ephraim Ave") == "1598 Mount Ephraim Ave")
check("A3 the raw string IS a store_code", RESOLVE("s-9") == "9 Elm St")
check("A4 squashed spelling (case / punctuation / spacing drift)",
      RESOLVE("11636  springfield blvd.") == "11636 Springfield Blvd")
check("A5 THE B-11634 REGRESSION — the carrier's hyphenated number with a city tail resolves "
      "to the roster's unhyphenated spelling",
      RESOLVE("116-36 Springfield Blvd Cambria Heights, NY 11411") == "11636 Springfield Blvd")
check("A6 THE RELOCATION REGRESSION — an abbreviated spelling of an ALIASED number, with a city "
      "tail, resolves through the alias's street number",
      RESOLVE("2778 Mt Ephraim Ave Camden, NJ 08104") == "1598 Mount Ephraim Ave")
check("A7 an unknown store resolves to ITSELF — never onto a plausible neighbour",
      RESOLVE("400 Nowhere Rd Somewhere, NY 00000") == "400 Nowhere Rd Somewhere, NY 00000")
_amb = sid.build_store_resolver(
    [{"store_code": "A", "store_address": "3 Palisade Ave", "market": "M", "salesforce_id": "x"},
     {"store_code": "B", "store_address": "3 Broadway", "market": "M", "salesforce_id": "y"}], [])
check("A8 AMBIGUITY IS REFUSED — a street number two stores claim resolves to NOTHING, never a "
      "coin-flip (this is what last-wins used to do)",
      _amb("3 Main St Elsewhere, NY") == "3 Main St Elsewhere, NY")
check("A9 a non-numeric lead is never matched by number (a code only ever matches exactly)",
      RESOLVE("B-99 something") == "B-99 something")
check("A10 blank in, None out", RESOLVE("  ") is None and sid.store_key(RESOLVE, None) == "")
check("A11 no resolver → the raw string, so a caller without one still groups a store with itself",
      sid.store_key(None, " 9 Elm St ") == "9 Elm St")

head("B. THE IDENTITY INDEX — one physical store, one key, and the door survives")
IDX = sid.store_identity_index(MAPPING, RESOLVE)
check("B1 one key per physical store (two codes on one address do NOT make two rows)",
      sorted(IDX) == ["11636 Springfield Blvd", "1598 Mount Ephraim Ave", "77 Closed Rd",
                      "9 Elm St"], sorted(IDX))
check("B2 the carrier's door is KEPT — the row with the salesforce_id is folded first, so the "
      "residual join still finds the store (last-wins used to keep the NULL)",
      IDX["1598 Mount Ephraim Ave"]["salesforce_id"] == "SF-1598")
check("B3 …and so is its code and market",
      IDX["1598 Mount Ephraim Ave"]["store_code"] == "S-1598"
      and IDX["1598 Mount Ephraim Ave"]["market"] == "M2")
check("B4 the collision is REPORTED, not silently resolved",
      IDX["1598 Mount Ephraim Ave"]["ambiguous"] is True
      and sorted(IDX["1598 Mount Ephraim Ave"]["codes"]) == ["S-1598", "S-2778"])
check("B5 ambiguous_identities names it for the page",
      [r["store"] for r in sid.ambiguous_identities(IDX)] == ["1598 Mount Ephraim Ave"])
# ── THE REGRESSION (owner 2026-11-16: *"902 is closed for good, it set to inactive"*) ────────────
# This index USED TO SKIP a closed row, and that is how a closed store's money left the report: with
# no identity, LuxeLink's `902 Ave U` could not be keyed at all and $6,352.09 of its October labour
# was neither booked nor reported — not hidden, GONE. Identity is not visibility (§ACCOUNT-03.6).
check("B6 a CLOSED store still HAS an identity — the money spelled with its code can be keyed",
      "77 Closed Rd" in IDX and IDX["77 Closed Rd"]["store_code"] == "S-CLOSED")
check("B7 …and the index SAYS it is closed, so a caller can make it a ROW rule without re-deriving "
      "the predicate", IDX["77 Closed Rd"]["is_active"] is False
      and IDX["9 Elm St"]["is_active"] is True)
_closed_only = sid.store_identity_index(MAPPING, RESOLVE, include_inactive=False)
check("B8 a caller that wants a LIST, not a lookup, narrows it — and only then",
      "77 Closed Rd" not in _closed_only and "9 Elm St" in _closed_only)
_null_active = sid.store_identity_index(
    [{"store_code": "S-N", "store_address": "5 Null Way", "is_active": None}], RESOLVE,
    include_inactive=False)
check("B9 NULL means ACTIVE, so a narrowed list still keeps the store (the 2026-08-06 null-safe "
      "rule, now read from the §CORE-45 home)",
      "5 Null Way" in _null_active and _null_active["5 Null Way"]["is_active"] is True)
_str_false = sid.store_identity_index(
    [{"store_code": "S-F", "store_address": "6 Str Way", "is_active": "false"}], RESOLVE,
    include_inactive=False)
check("B10 …and the STRING 'false' is not active either, though it is truthy in Python",
      "6 Str Way" not in _str_false)
_one_live = sid.store_identity_index(
    [{"store_code": "S-D", "store_address": "9 Elm St", "is_active": False},
     {"store_code": "S-9", "store_address": "9 Elm St", "is_active": True}], RESOLVE)
check("B11 a store with ONE live row anywhere is live, whichever order the rows arrive in",
      _one_live["9 Elm St"]["is_active"] is True
      and sid.store_identity_index(list(reversed([
          {"store_code": "S-D", "store_address": "9 Elm St", "is_active": False},
          {"store_code": "S-9", "store_address": "9 Elm St", "is_active": True}]), ),
          RESOLVE)["9 Elm St"]["is_active"] is True)
check("B12 …and the LIVE row is folded first, so the identity a reader gets is the live store's",
      _one_live["9 Elm St"]["store_code"] == "S-9")
check("B13 the fold is deterministic, not insertion order",
      sid.store_identity_index(list(reversed(MAPPING)), RESOLVE) == IDX)
check("B14 the WITHHOLDING relation still narrows to live rows — a closed old address is the "
      "relocation shape it looks for, so broadening it is a money-visible change measured on its "
      "own, not carried in here",
      not any("77 Closed Rd" in p for p in sid.shared_identity_pairs(MAPPING, RESOLVE)))

head("C. THE GP REPORT — the money lands on the store it was paid for")
PERIOD = "July 2026"
SALES = [
    # the POS spells this store WITHOUT the hyphen; the carrier spells it WITH one
    {"store": "11636 Springfield Blvd", "department": "Ondigo", "gp": 10.0, "ext_price": 10.0,
     "product_desc": "case", "salesperson": "rep1", "category": "", "product_id": "", "sku": ""},
    # …and this one by the spelling it had before it moved
    {"store": "2778 Ephraim Ave", "department": "Ondigo", "gp": 20.0, "ext_price": 20.0,
     "product_desc": "case", "salesperson": "rep2", "category": "", "product_id": "", "sku": ""},
]
PAY = [
    {"business_address": "116-36 Springfield Blvd Cambria Heights, NY 11411",
     "amount": 17287.01, "payment_type": "New Activation Bounty - Month 1", "category": "Commission"},
    {"business_address": "2778 Mt Ephraim Ave Camden, NJ 08104",
     "amount": 8892.08, "payment_type": "New Activation Bounty - Month 3", "category": "Commission"},
    {"business_address": "400 Nowhere Rd Somewhere, NY 00000",
     "amount": 123.45, "payment_type": "New Activation Bounty - Month 1", "category": "Commission"},
]
COMP = [{"business_address": "116-36 Springfield Blvd Cambria Heights, NY 11411",
         "compensation_type": "Activation", "payment_amount": 500.0}]
MI = [{"salesforce_id": "SF-1598", "actual_mi_payout": 8974.73, "actual_atu_payout": 100.0,
       "mi_activation_date": "2026-07-01"}]
REP = [{"store": "2778 Mt Ephraim Ave Camden, NJ 08104", "total_payout": 178.75,
        "epay_salesperson": "rep2", "storeops_name": "Rep Two"}]

R = calc_gp_report(SALES, PAY, MI, REP, [], [], MAPPING, PERIOD,
                   comp_rows=COMP, resolve_store_canonical=RESOLVE)
rows = {r["store"]: r for r in R["store_rows"]}
check("C1 every mapped store has exactly ONE row (the POS spelling did not make a second)",
      len([k for k in rows if not rows[k].get("store_unplaced")]) == 3, sorted(rows))
_spr = rows.get("11636 Springfield Blvd", {})
check("C2 THE B-11634 REGRESSION: the carrier's hyphenated payment lands on the store",
      round(_spr.get("comm", 0) + _spr.get("unmapped", 0), 2) == 17287.01, str(_spr)[:200])
check("C3 …and so does its comp-report money",
      round(sum(_spr.get(k, 0) for k in ("comp_comm", "comp_reimb", "comp_mdf", "comp_chb",
                                         "comp_unmapped")), 2) == 500.0)
check("C4 …stamped with the store's own code and market, not a blank row",
      _spr.get("store_code") == "S-11634" and _spr.get("market") == "M1")
_eph = rows.get("1598 Mount Ephraim Ave", {})
check("C5 THE RELOCATION REGRESSION: the abbreviated feed spelling lands on the store",
      round(_eph.get("comm", 0) + _eph.get("unmapped", 0), 2) == 8892.08, str(_eph)[:200])
check("C6 the POS sale spelled the OLD way is on the SAME row (one store, one line)",
      round(_eph.get("acc_gp", 0), 2) == 20.0)
check("C7 THE DOOR: the residual joins through the salesforce_id the fold kept, so MI/ATU land on "
      "the store instead of on the code with the NULL door",
      round(_eph.get("mi", 0), 2) == 8974.73 and round(_eph.get("atu", 0), 2) == 100.0)
check("C8 rep pay keyed by identity too — the carrier spelling of the store pays its rep",
      round(_eph.get("rep_pay", 0), 2) == 178.75)

head("D. MONEY NOTHING CAN PLACE IS STATED, NEVER DROPPED")
_un = [r for r in R["store_rows"] if r.get("store_unplaced")]
check("D1 the unplaceable address gets a row of its own, under the spelling the feed sent",
      [r["store"] for r in _un] == ["400 Nowhere Rd Somewhere, NY 00000"], str(_un)[:200])
check("D2 …carrying its money", round(sum(_un[0].get(k, 0) for k in ("comm", "unmapped")), 2) == 123.45
      if _un else False)
check("D3 …and the REASON, so the fix is a config row and not a code change",
      bool(_un and "Store-Matching" in str(_un[0].get("store_unplaced_why"))))
check("D4 the report block names it, with the resolver it used",
      R["store_identity"]["resolver_present"] is True
      and [u["store"] for u in R["store_identity"]["unplaced"]] == ["400 Nowhere Rd Somewhere, NY 00000"])
check("D5 the §13d two-codes-one-store collision rides along as evidence",
      [r["store"] for r in R["store_identity"]["ambiguous_identities"]] == ["1598 Mount Ephraim Ave"])
_feed = round(sum(r["amount"] for r in PAY), 2)
_booked = round(sum(sum(r.get(k, 0) for k in ("comm", "reimb", "mdf", "chargeback", "unmapped"))
                    for r in R["store_rows"]), 2)
check("D6 THE IDENTITY THE OLD JOIN BROKE: every payment-detail dollar is in the report — "
      f"feed {_feed} == booked {_booked}", _feed == _booked)
check("D7 …and the header totals carry it (this is why the report sat below the P&L)",
      round(sum(R["totals"][k] for k in ("comm", "reimb", "mdf", "chargeback", "unmapped")), 2)
      == _feed, str(R["totals"])[:160])
check("D8 nothing is double-counted: the unplaced rows and the store rows are disjoint",
      len(R["store_rows"]) == len(set(r["store"] for r in R["store_rows"])))

head("E. NEGATIVE CONTROL — the same fixtures through the OLD leading-token join")


def _old_street_num(addr):
    """`gp_report.street_num` as it shipped until 2026-10-09. Kept HERE, in a harness, so the
    defect can be reproduced forever without the join existing in the product."""
    return str(addr or "").strip().split(" ")[0]


_old_store_by_num = {}
for _s in MAPPING:
    _n = _old_street_num(_s.get("store_address", ""))
    if _n:
        _old_store_by_num[_n] = _s      # LAST WINS — the bug
check("E1 the old join could not place the carrier's hyphenated address at all",
      _old_street_num(PAY[0]["business_address"]) not in _old_store_by_num)
check("E2 the old join could not place the relocated store's feed spelling either",
      _old_street_num(PAY[1]["business_address"]) not in _old_store_by_num)
check("E3 the old last-wins map kept the row with the NULL door, so the residual had nothing to "
      "join on", _old_store_by_num["1598"].get("salesforce_id") is None)
_lost = round(PAY[0]["amount"] + PAY[1]["amount"], 2)
check(f"E4 so the old engine dropped ${_lost:,.2f} of these fixtures' payment detail, and the new "
      "one drops $0.00", _lost == 26179.09 and _feed == _booked)

head("F. NO RESOLVER AT ALL — degrade honestly, never back to token matching")
R0 = calc_gp_report(SALES, PAY, MI, REP, [], [], MAPPING, PERIOD, comp_rows=COMP,
                    resolve_store_canonical=None)
_booked0 = round(sum(sum(r.get(k, 0) for k in ("comm", "reimb", "mdf", "chargeback", "unmapped"))
                     for r in R0["store_rows"]), 2)
check("F1 with no resolver every dollar is still in the report (on raw-spelling rows)",
      _booked0 == _feed, str(_booked0))
check("F2 …and the report SAYS it had no resolver instead of implying a placement",
      R0["store_identity"]["resolver_present"] is False)
check("F3 …with the unplaced rows naming each spelling it could not join",
      len(R0["store_identity"]["unplaced"]) >= 2)

head("H. THE FOLD MUST NOT LOSE THE OTHER CODE'S EXPENSES")

# REGRESSION, found by re-measuring live before merging (2026-10-09). Folding two store_mapping
# codes onto one identity is right, but the expense column looked expenses up under a SINGLE code,
# so whichever code the fold did not pick had its expenses zeroed. Live house org: `1 S 60th street`
# is claimed by `B-1` (no expense rows) and `B-60TH` ($12,285.35 in August), `1598 Mount Ephraim
# Ave` by `B-1598` (none) and `B-2778` (the same) — $24,570.70 of August expenses stopped booking,
# which is this PR's own defect wearing the expense column's hat. The identity carries every
# claiming code, so the sum is over all of them, once each.
EXPENSES = [
    # filed under the SECONDARY code of the relocation pair — the one the fold does not pick
    {"store_code": "S-2778", "expense_name": "Rent / Lease", "amount": 4500.0},
    # and under the PRIMARY code of a single-code store, which must be unaffected
    {"store_code": "S-9", "expense_name": "Rent / Lease", "amount": 1000.0},
]
RX = calc_gp_report(SALES, PAY, MI, REP, EXPENSES, [], MAPPING, PERIOD,
                    comp_rows=COMP, resolve_store_canonical=RESOLVE)
_rx = {r["store"]: r for r in RX["store_rows"]}
check("H1 the folded store books the expenses filed under its OTHER code",
      round(_rx.get("1598 Mount Ephraim Ave", {}).get("exp_total", 0), 2) == 4500.0,
      str(_rx.get("1598 Mount Ephraim Ave", {}).get("exp_total")))
check("H2 a single-code store is untouched",
      round(_rx.get("9 Elm St", {}).get("exp_total", 0), 2) == 1000.0,
      str(_rx.get("9 Elm St", {}).get("exp_total")))
check("H3 nothing is counted twice: the report's expense total equals the fixtures' sum",
      round(RX["totals"]["exp_total"], 2) == 5500.0, str(RX["totals"]["exp_total"]))
check("H4 the fold did not raise a second row to carry them",
      len([k for k in _rx if not _rx[k].get("store_unplaced")]) == 3, sorted(_rx))

head("G. MIGRATION 1066 IS SAFE IN EITHER ORDER — a BARE TOKEN still resolves")

# The PR claims mig 1066 may be merged before it is applied. That rests on exactly one fact: the
# mig-274 rollup, UNAPPLIED, hands the commission-leg endpoints a bare leading token as `store_num`
# (`'11636'`), and the chain must place that token on the canonical address like any other
# spelling. Proven here rather than asserted in the PR body.
_resolve = sid.build_store_resolver(MAPPING, ALIASES)
check("G1 pre-1066 (SQL still splits): the bare token resolves to the canonical address",
      sid.store_key(_resolve, "11636") == "11636 Springfield Blvd",
      sid.store_key(_resolve, "11636"))
check("G2 post-1066 (SQL returns the raw address): the carrier's own spelling resolves to the "
      "SAME key, so the two orders agree",
      sid.store_key(_resolve, "116-36 Springfield Blvd Cambria Heights, NY 11411")
      == sid.store_key(_resolve, "11636"))
check("G3 an AMBIGUOUS bare token resolves to nothing rather than to a winner",
      sid.store_key(_resolve, "1598") == "1598 Mount Ephraim Ave"
      and sid.store_key(_resolve, "99999") == "99999",
      (sid.store_key(_resolve, "1598"), sid.store_key(_resolve, "99999")))
check("G4 a bare token is never resolved by the ALIAS number ahead of a declared ADDRESS number "
      "(step 6 keeps precedence over step 7)",
      sid.store_key(_resolve, "2778") == "1598 Mount Ephraim Ave",
      sid.store_key(_resolve, "2778"))

head("I. A CLOSED STORE: ITS MONEY IS NEVER LOST, ITS EMPTY ROW IS NEVER SHOWN")
# THE DEFECT, live (owner 2026-11-16: *"902 is closed for good, it set to inactive"*). LuxeLink
# closed `902 Ave U`. Because the identity index skipped closed rows, the store had NO identity, so
# the Gross Profit report could not key the $6,352.09 of October labour filed under its code: the
# payload reported it as `labour_measured_fill.unkeyed` and the column showed nothing. The owner's
# own two rules both hold — a closed store does not clutter the report, AND a report never lies
# about a month that happened — so the rule is a ROW rule on the row's own money.
_CLOSED_EXP = [{"store_code": "S-CLOSED", "amount": 6352.09, "category": "Employee Salaries"}]
R_CL = calc_gp_report([], [], [], [], _CLOSED_EXP, [], MAPPING, PERIOD,
                      resolve_store_canonical=RESOLVE)
_cl = {r["store"]: r for r in R_CL["store_rows"]}
check("I1 THE REGRESSION: the closed store's expenses are keyed and BOOKED, not dropped",
      round(_cl.get("77 Closed Rd", {}).get("exp_total", 0.0), 2) == 6352.09,
      str(_cl.get("77 Closed Rd", {}))[:160])
check("I2 …and it is listed, because it HAS data this period",
      "77 Closed Rd" in _cl)
check("I3 …and its loss is in the report's own total, not stranded off it",
      round(_cl.get("77 Closed Rd", {}).get("net_profit", 0.0), 2) == -6352.09)
check("I4 with NOTHING this period the closed store is NOT a line of the report (the owner's "
      "hide-inactive directive, §CORE-45)",
      "77 Closed Rd" not in {r["store"] for r in R["store_rows"]})
check("I5 …while a LIVE store with nothing is still listed, so the drop is about closure and not "
      "about emptiness", "9 Elm St" in {r["store"] for r in R["store_rows"]})

print()
print("=" * 78)
print(f"RESULT: {P} passed, {F} failed")
print("=" * 78)
sys.exit(1 if F else 0)
