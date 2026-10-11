"""HARNESS — the clock-report VARIANCE, the Monday ask, the Tuesday gate, the Wednesday run gate.

OWNER ASK 2026-10-10: *"match with what we have in metrics pro and asses the difference and ask the
DM on every monday and require the correction to be done before the payroll is run, the dm should be
notified as an alert as soon as they log in to fix it and block them from logging in unless they fix
it by tuesday so the payroll can be run on Wednesday."*

TWO THINGS THE OWNER'S WORDS AND THE HOUSE RULES DISAGREE ABOUT, settled here and pinned:

  1. *"block them from logging in"* is implemented as a FULL-SCREEN CORRECTION-ONLY SESSION, not a
     refused sign-in. ★ NO LOCKOUT ★ (index §22) is a standing property of this platform for the
     simplest possible reason: a person who cannot sign in cannot fix what blocks them. §E proves the
     sign-in always succeeds and the session is confined instead.
  2. *Monday / Tuesday / Wednesday* are CONFIG (`ask_weekday` / `gate_weekday` / `run_weekday`), with
     his days as the house defaults. A tenant that runs payroll on Thursday changes three numbers in
     a row and no code. §F proves the window moves with the rows.

THE SAFE DIRECTION IS THE OPPOSITE OF THE HOURS BASIS, deliberately, and that is §A's point. In
`payroll_hours_basis` an unruled basis must NOT pay. Here an unruled variance kind must BLOCK: the
question is "is it safe to run payroll" and an unrecognised disagreement is not an answer.

SECTIONS
  A. The kind vocabulary, and why an UNKNOWN kind blocks.
  B. The comparison itself, against the measured 2026-10-09 rows.
  C. An unbindable login is a first-class variance — never a silent attribution.
  D. A day whose file never arrived, and a store with nothing wrong.
  E. The ask, and the recipient ladder the measured DM coverage demands.
  F. The gate: an alert on any day, confinement from the gate day, NEVER a refused sign-in.
  G. The payroll run gate, and the thirteen employees with no pay rate.
  H. Everything ships OFF.
  Z. ARMED negative control.

Run: cd backend && python3 harness_clock_variance.py   (DB-free, stdlib only)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.storeops import clock_variance as V  # noqa: E402
from app.modules.storeops import payroll_hours_basis as B  # noqa: E402
from app.modules.storeops import payroll_identity as PI  # noqa: E402

PASS, FAIL = [], []


def check(label, got, want):
    if got == want:
        PASS.append(label)
    else:
        FAIL.append(f"{label}\n      got:  {got!r}\n      want: {want!r}")


def section(t):
    print(f"\n── {t}")


DAY = "2026-10-09"
OFF = V.DEFAULT_CONFIG
ON = V.resolve_config({"clock_feed_enabled": True, "variance_ask_enabled": True,
                       "login_gate_enabled": True, "payroll_run_gate_enabled": True})
BASIS = B.resolve_config(None)

# THE MEASURED ROWS. Four of the real 2026-10-09 pairs, chosen because each is a different outcome:
#   Khan  — the two clocks agree inside tolerance, so there is NO variance at all
#   shaik — a real disagreement of 0.28h
#   Garapati — a real disagreement of 1.96h, the row the owner's ruling is about
#   Ramos — our kiosk AUTO-CLOSED the punch and the report has no row: a missing punch, not 9 hours
EMPS = [
    {"employee_id": "E1", "name": "Amir Khan", "epay_login": "Amir",
     "is_active": True, "pay_basis": "hourly", "pay_rate": 17.00},
    {"employee_id": "E2", "name": "Junaid shaik", "epay_login": "Junaid",
     "is_active": True, "pay_basis": "hourly", "pay_rate": 17.00},
    {"employee_id": "E3", "name": "Rohit Garapati", "epay_login": "Rohit",
     "is_active": True, "pay_basis": "hourly", "pay_rate": 15.00},
    {"employee_id": "E4", "name": "Angie Ramos", "epay_login": "Angie",
     "is_active": True, "pay_basis": "hourly", "pay_rate": 17.00},
]
MATCH = PI.build_employee_matcher(EMPS)

FEED = [
    {"employee_raw": "Khan, Amir", "feed_login": "Amir", "store_code": "B-117",
     "business_date": DAY, "hours": 8.82},
    {"employee_raw": "shaik, Junaid", "feed_login": "Junaid", "store_code": "B-4712",
     "business_date": DAY, "hours": 8.90},
    {"employee_raw": "Garapati, Rohit", "feed_login": "Rohit", "store_code": "B-151",
     "business_date": DAY, "hours": 9.17},
]
STORE_OF = {("E1", DAY): "B-117", ("E2", DAY): "B-4712",
            ("E3", DAY): "B-151", ("E4", DAY): "B-652"}


def ours(extra_punches=()):
    return B.build_evidence(
        shifts=[{"employee_id": e, "shift_date": DAY, "scheduled_hours": 9.0, "actual_hours": 0}
                for e in ("E1", "E2", "E3", "E4")],
        punches=[
            {"employee_id": "E1", "work_date": DAY, "hours": 8.81,
             "clock_out": f"{DAY}T19:17:00Z"},
            {"employee_id": "E2", "work_date": DAY, "hours": 9.18,
             "clock_out": f"{DAY}T19:17:00Z"},
            {"employee_id": "E3", "work_date": DAY, "hours": 11.13,
             "clock_out": f"{DAY}T20:52:00Z"},
            # Angie: the kiosk closed this punch by itself. Not evidence of anything.
            {"employee_id": "E4", "work_date": DAY, "hours": 9.0,
             "clock_out": f"{DAY}T23:59:00Z", "auto_clocked_out": True},
        ] + list(extra_punches))


FH, UNMATCHED = V.feed_day_hours(FEED, MATCH)
OUT = V.compare(FH, ours(), BASIS, ON, employee_rows=EMPS, store_of=STORE_OF,
                extra_rows=UNMATCHED)
BY_KIND = OUT["totals"]["by_kind"]


# ════════════════════════════════════════════════════════════════════════════════════════════════
section("A. the kind vocabulary — and why an UNKNOWN kind BLOCKS")
check("A1 every kind has an explaining sentence of its own",
      sorted(V.KINDS) == sorted(V.KIND_REASONS), True)
# THE DIRECTION THE NEXT MISTAKE FALLS IN, and it is the OPPOSITE of the hours basis on purpose.
# "Is it safe to run payroll" cannot be answered by an unrecognised disagreement.
check("A2 an UNRULED variance kind BLOCKS — the safe direction for THIS question",
      V.kind_blocks("something_nobody_has_seen_yet"), True)
check("A3 …whereas an unruled PAY BASIS does not pay — the safe direction for THAT one",
      B.basis_pays_hours("something_nobody_has_seen_yet"), False)
check("A4 and both facts live in their own home, so neither can be read off the other",
      (V.__name__ != B.__name__), True)
check("A5 every ruled kind blocks until a human settles it",
      sorted(k for k in V.KINDS if not V.kind_blocks(k)), [])
# A FILE THAT NEVER ARRIVED IS NOT AGREEMENT. Under the owner's ruling the feed decides pay, so a
# missing file is not a missing check — it is a store-day that cannot be priced.
check("A6 'no_clock_feed' says plainly that the day cannot be priced, not merely 'unchecked'",
      "pay" in V.KIND_REASONS[V.NO_CLOCK_FEED].lower(), True)


# ════════════════════════════════════════════════════════════════════════════════════════════════
section("B. the comparison, against the measured rows of 2026-10-09")
check("B1 the four logins bind to the four employees", sorted(FH), sorted([
    ("E1", "B-117", DAY), ("E2", "B-4712", DAY), ("E3", "B-151", DAY)]))
# Khan: ours 8.81, the report 8.82 — a difference of 0.01h. That is not a variance, it is two clocks.
check("B2 Khan's 0.01h difference is INSIDE the tolerance, so it raises nothing",
      [r for r in OUT["rows"] if r.get("employee_id") == "E1"], [])
check("B3 shaik's 0.28h difference IS a variance",
      [(r["kind"], r["delta_hours"]) for r in OUT["rows"] if r.get("employee_id") == "E2"],
      [(V.HOURS_DIFFER, -0.28)])
check("B4 Garapati's 1.96h difference is the big one, and it is signed OUR-WAY-HIGH",
      [(r["kind"], r["delta_hours"]) for r in OUT["rows"] if r.get("employee_id") == "E3"],
      [(V.HOURS_DIFFER, -1.96)])
check("B5 the tolerance is a CONFIG value, not a literal", ON["variance_tolerance_hours"], 0.25)
check("B6 every variance row carries OUR basis, so the reader sees WHERE our number came from",
      sorted({r["our_basis"] for r in OUT["rows"] if r.get("our_hours") is not None}),
      ["clocked"])
check("B7 …and the sentence explaining that basis, not just its slug",
      all(r.get("our_basis_reason") for r in OUT["rows"] if r.get("our_basis")), True)
# ANGIE RAMOS — the regression fixture. The kiosk auto-closed her punch at 23:59 and the report has
# no row at all. Nine hours must NOT appear: an auto-closed punch is not evidence of anything.
_angie = [r for r in OUT["rows"] if r.get("employee_id") == "E4"]
# TODAY'S DEFAULT PAYS AUTO-CLOSED PUNCHES — 290 live ones — so under the shipped config her nine
# hours ARE our figure. What matters is that the report has NO row for her, so the day is raised,
# and that the auto-closure is carried ON the row rather than being invisible on exactly the orgs
# where it is being paid.
check("B8 Angie's day is raised: the report has nothing where we hold nine hours",
      [(r["kind"], r["our_hours"]) for r in _angie], [(V.EXTRA_PUNCH, 9.0)])
check("B9 …and the row SAYS the kiosk closed the punch itself, so no DM trusts a sweep's clock",
      [r["auto_closed_punches"] for r in _angie], [1])
# UNDER CLOCKED-ONLY the same day refuses to pay at all, rather than paying a sweep's timestamp.
_strict = V.compare(FH, ours(), B.resolve_config({"hours_basis_mode": B.MODE_CLOCKED_ONLY,
                                                  "pay_from_schedule_default": False}),
                    ON, employee_rows=EMPS, store_of=STORE_OF, extra_rows=UNMATCHED)
check("B9b under clocked-only her hours are 0.0 on an UNUSABLE punch, not nine",
      [(r["our_hours"], r["our_basis"]) for r in _strict["rows"] if r.get("employee_id") == "E4"],
      [(0.0, B.PUNCH_UNUSABLE)])
check("B9c …and an unusable punch does not pay", B.basis_pays_hours(B.PUNCH_UNUSABLE), False)
check("B10 the totals name every kind seen, so a board can be read without the rows",
      sorted(BY_KIND), sorted([V.EXTRA_PUNCH, V.HOURS_DIFFER]))
# STORE MISMATCH — the other kind that is not a disagreement about a number. The hours can tie to
# the minute and the labour cost still land on the wrong store's P&L.
_mis = V.compare({("E1", "B-999", DAY): 8.81}, ours(), BASIS, ON, employee_rows=EMPS,
                 store_of={("E1", DAY): "B-117"})
check("B10b one person placed at two stores on one day is its OWN kind",
      [(r["kind"], r["feed_stores"], r["our_stores"])
       for r in _mis["rows"] if r.get("employee_id") == "E1"],
      [(V.STORE_MISMATCH, ["B-999"], ["B-117"])])
check("B10c …raised although the hours agree exactly",
      [r["delta_hours"] for r in _mis["rows"] if r.get("employee_id") == "E1"], [0.0])
# A HUMAN'S DECISION STICKS. A row somebody has already settled is reported as resolved, not raised
# again every morning until they stop reading the alert.
_cleared = V.compare(FH, ours(), BASIS, ON, employee_rows=EMPS, store_of=STORE_OF,
                     resolved={("E2", "B-4712", DAY)}, extra_rows=UNMATCHED)
check("B11 a settled row is reported as RESOLVED and stops blocking",
      [(r["resolved"], r["blocks"]) for r in _cleared["rows"] if r.get("employee_id") == "E2"],
      [(True, False)])
check("B12 …and it still APPEARS, so 'somebody looked' is visible rather than an absence",
      len(_cleared["rows"]), len(OUT["rows"]))


# ════════════════════════════════════════════════════════════════════════════════════════════════
section("C. an unbindable login is a FIRST-CLASS variance, never a silent attribution")
_stranger = FEED + [{"employee_raw": "Martínez, LESLIEE", "feed_login": "Leslie",
                     "store_code": "B-1750", "business_date": DAY, "hours": 8.77}]
_fh2, _un2 = V.feed_day_hours(_stranger, MATCH)
check("C1 a login the roster does not carry attributes NOTHING",
      len(_fh2), len(FH))
check("C2 …and is reported as its own kind, with its hours",
      [(r["kind"], r["feed_hours"]) for r in _un2], [(V.LOGIN_UNLINKED, 8.77)])
# `feed_day_hours` reports the row; whether it BLOCKS is the kind's own fact, read from the one
# vocabulary rather than stamped on by whichever function happened to build the row.
check("C3 …and it BLOCKS, because 8.77 hours belong to somebody",
      all(V.kind_blocks(r["kind"]) for r in _un2), True)
# A NAME IS A PROPOSAL. The measured case: the clock report spells her `Martínez, LESLIEE` and the
# roster has `Leslie Martinez`. The accent fold now makes the NAMES match — and a name match is
# still only a proposal for a human to approve.
_m2 = PI.build_employee_matcher(EMPS + [{"employee_id": "E9", "name": "Leslie Martinez",
                                         "is_active": True, "pay_basis": "hourly"}])
_eid, _basis, _d = _m2("Leslie", "Martínez, LESLIEE")
# THE MEASURED CASE, AND IT IS HONESTLY UNMATCHED. The fold fix means `Martínez` and `Martinez` are
# now the same token — but the report spells her forename `LESLIEE` and the roster has `Leslie`, so
# only the SURNAME overlaps. One shared surname is not a person, and this platform does not guess a
# person from a name. So she stays a variance the DM is asked about, which is the right outcome:
# 8.77 unattributed hours are VISIBLE rather than quietly assigned to somebody who may be right.
check("C4 one shared surname is NOT enough to propose a person",
      (_eid, _d.get("proposed_employee_id")), (None, None))
check("C5 …and the basis it reports binds nothing", PI.match_is_binding(_basis), False)
_fh3, _un3 = V.feed_day_hours(_stranger, _m2)
check("C6 so the hours are STILL not attributed", len(_fh3), len(FH))
check("C7 …and she stays on the board as unlinked hours a human must place",
      [(r["kind"], r["feed_hours"]) for r in _un3], [(V.LOGIN_UNLINKED, 8.77)])
# WHERE THE FORENAME AGREES TOO, the accent fold is what makes the match reachable at all — that IS
# the defect this fixed, pinned on a spelling that differs ONLY by the accent.
_m3 = PI.build_employee_matcher(EMPS + [{"employee_id": "E9", "name": "Leslie Martinez",
                                         "is_active": True}])
# The matcher returns the employee id ONLY for a binding basis, and a name is never binding, so the
# person it reached arrives as `proposed_employee_id` and the id itself stays None. That shape is
# the guarantee: a caller that reads the id cannot accidentally attribute hours to a proposal.
_pm = _m3("whoever", "Martínez, Leslie")
check("C7b 'Martínez, Leslie' reaches the right person once the accent is folded",
      _pm[2].get("proposed_employee_id"), "E9")
check("C7c …and the plain-ASCII spelling reaches exactly the same person",
      _m3("whoever", "Martinez, Leslie")[2].get("proposed_employee_id"), "E9")
check("C7d …as a PROPOSAL only: the employee id itself is withheld, so nothing can attribute",
      (_pm[0], PI.match_is_binding(_pm[1])), (None, False))
# AN APPROVED LINK BINDS. That is the whole point of the approval step.
_m4 = PI.build_employee_matcher(
    EMPS + [{"employee_id": "E9", "name": "Leslie Martinez", "is_active": True}],
    link_rows=[{"feed_login": "Leslie", "employee_id": "E9", "status": "approved"}])
_fh4, _un4 = V.feed_day_hours(_stranger, _m4)
check("C8 once APPROVED, the hours attribute and the variance is gone",
      (len(_fh4), len(_un4)), (len(FH) + 1, 0))
# A REJECTED LINK IS A HUMAN SAYING "NOT THIS PERSON", and must never be re-proposed.
_m5 = PI.build_employee_matcher(
    EMPS + [{"employee_id": "E9", "name": "Leslie Martinez", "is_active": True}],
    link_rows=[{"feed_login": "Leslie", "employee_id": "E9", "status": "rejected"}])
check("C9 a REJECTED link is not re-proposed as if nobody had looked",
      PI.match_is_binding(_m5("Leslie", "Martínez, LESLIEE")[1]), False)


# ════════════════════════════════════════════════════════════════════════════════════════════════
section("D. a day whose file never arrived — and a store with nothing wrong")
_expected = {"B-117": {DAY, "2026-10-08"}, "B-4712": {DAY}, "B-151": {DAY}}
_arrived = {"B-117": {DAY}, "B-4712": {DAY}, "B-151": {DAY}}
_gaps = V.feed_day_coverage(_arrived, _expected, ON)
check("D1 a store-day with no file is its OWN variance kind",
      [(r["store_code"], r["date"], r["kind"]) for r in _gaps],
      [("B-117", "2026-10-08", V.NO_CLOCK_FEED)])
check("D2 …and it blocks, so a silent morning cannot read as agreement",
      all(r["blocks"] for r in _gaps), True)
check("D3 a store whose file never arrived at all raises every expected day",
      len(V.feed_day_coverage({}, _expected, ON)), 4)
# ABSENCE IS NEVER ZERO. A board that lists only problems cannot tell "clean" from "not looked at".
_clear = V.store_all_clear(OUT["by_store"], ["B-117", "B-4712", "B-151", "B-999"])
check("D4 EVERY store appears on the board, including the ones with nothing wrong",
      sorted(_clear), ["B-117", "B-151", "B-4712", "B-999"])
check("D5 a store with no variance row says ALL CLEAR, rather than being absent",
      (_clear["B-999"], _clear["B-117"]), (True, True))
check("D6 …and a store with one does not", _clear["B-151"], False)


# ════════════════════════════════════════════════════════════════════════════════════════════════
section("E. the ask, and the ladder the MEASURED DM coverage demands")
# MEASURED 2026-10-10: 6 of 29 Cellfonz stores and 0 of 20 Luxelink stores resolve a DM through
# `storeops/org_chain`. A DM-keyed ask alone would reach eight stores of fifty-one and look as
# though it had asked everybody — the silent-failure shape this ladder exists to prevent.
_dm = {"employee_id": "D1", "name": "The DM", "email": "dm@example.com"}
_mm = {"employee_id": "M1", "name": "The Market Manager", "email": "mm@example.com"}
# The chain is `storeops/org_chain.chain_for`'s own answer, verbatim: {"district", "dm", "above"},
# nearest-first. This module reads that shape and walks no tree of its own.
check("E1 a store WITH a DM asks the DM",
      V.recipient_ladder({"district": "D-PA", "dm": [_dm], "above": [_mm]}), ([_dm], "dm"))
check("E2 a store with NO DM falls to the NEAREST manager above the district",
      V.recipient_ladder({"district": "D-PA", "dm": [], "above": [_mm]}), ([_mm], "above_district"))
check("E3 a store nobody owns is REPORTED as unowned, never dropped and never guessed",
      (V.recipient_ladder({}), V.recipient_ladder({"district": None, "dm": [], "above": []})),
      (([], "unowned"), ([], "unowned")))
# B-4712 (shaik's 0.28h) has a DM; B-151 (Garapati's 1.96h) has only a manager above the district;
# B-117 is CLEAN, because Khan's 0.01h is inside the tolerance; B-652 (Angie) is owned by nobody.
_plan = V.ask_plan(OUT["by_store"],
                   {"B-4712": {"dm": [_dm], "above": [_mm]},
                    "B-151": {"dm": [], "above": [_mm]},
                    "B-117": {"dm": [_dm], "above": [_mm]},
                    "B-652": {"dm": [], "above": []}},
                   store_codes=["B-117", "B-4712", "B-151", "B-652"])
check("E4 the ask reaches the DM for their store and the manager above for the orphaned one",
      sorted(_plan["recipients"]), ["D1", "M1"])
check("E5 the store nobody owns is NAMED with its count, so it cannot be silently unasked",
      [(u["store_code"], u["blocking"]) for u in _plan["unowned_stores"]], [("B-652", 1)])
check("E6 …and each recipient is told WHY they are being asked, by level",
      {k: v["level"] for k, v in _plan["recipients"].items()},
      {"D1": "dm", "M1": "above_district"})
check("E7 each recipient's count is their OWN stores', not the org's",
      {k: v["blocking"] for k, v in _plan["recipients"].items()}, {"D1": 1, "M1": 1})
# A STORE WITH NOTHING WRONG IS NOT ASKED ABOUT. The weekly ask must not become background noise,
# or it stops being read and the gate is the only thing left.
check("E8 the CLEAN store is in nobody's ask, though its DM is asked about another",
      _plan["recipients"]["D1"]["stores"], ["B-4712"])
check("E9 the ask reference key is stable, so the same ask is not sent twice",
      V.ask_ref_key("org-1", "2026-10-01", "2026-10-15", "D1"),
      V.ask_ref_key("org-1", "2026-10-01", "2026-10-15", "D1"))
check("E10 …and differs per period, so next period's ask is a new one",
      V.ask_ref_key("org-1", "2026-10-01", "2026-10-15", "D1")
      != V.ask_ref_key("org-1", "2026-10-16", "2026-10-31", "D1"), True)
check("E11 …and differs per STORE, so a store that turns blocking mid-week is asked about",
      V.ask_ref_key("org-1", "2026-10-01", "2026-10-15", "D1", "B-151")
      != V.ask_ref_key("org-1", "2026-10-01", "2026-10-15", "D1", "B-4712"), True)
check("E12 a key with no store is the key the earlier callers already wrote (additive)",
      V.ask_ref_key("org-1", "2026-10-01", "2026-10-15", "D1"), "org-1:2026-10-01:2026-10-15:D1")


# ════════════════════════════════════════════════════════════════════════════════════════════════
section("E2. the ask RENDERED — the fan-out the delivery ladder eats, and what it says")
# This is where the ask meets `notify/digest_delivery.deliver_digests`. The shape is not ours to
# choose: a digest is {to, to_name, addresses, items}, and each item carries its own `ref_key` so
# `storeops/alert_log` can dedup it PER CHANNEL. Nothing here sends anything.
_fan = V.ask_digests(_plan, org_id="org-1", period_start="2026-10-01", period_end="2026-10-15")
check("E2a one digest per recipient, in a stable order",
      [d["employee_id"] for d in _fan], ["D1", "M1"])
check("E2b one ITEM per store, each with its own dedup key",
      [[(i["store_code"], i["ref_key"]) for i in d["items"]] for d in _fan],
      [[("B-4712", "org-1:2026-10-01:2026-10-15:D1:B-4712")],
       [("B-151", "org-1:2026-10-01:2026-10-15:M1:B-151")]])
check("E2c the addresses dict is what the ladder reads — email present, WhatsApp absent",
      [d["addresses"] for d in _fan],
      [{"email": "dm@example.com"}, {"email": "mm@example.com"}])
# A recipient the org tree names but whose record carries no address at all must still appear. The
# ladder marks that row `no_channel` and records nothing, so it retries once an address exists —
# dropping them here would make an unreachable manager look like a satisfied one.
_noaddr = V.ask_digests({"recipients": {"X1": {"stores": ["B-1"], "blocking": 1, "variances": 1,
                                               "level": "dm", "name": "No Address"}}},
                        org_id="o", period_start="a", period_end="b")
check("E2d a recipient with NO address is still returned, with no addresses",
      [(d["employee_id"], d["addresses"], d["to"]) for d in _noaddr], [("X1", {}, "")])
check("E2e a recipient with no blocking store produces no digest at all",
      V.ask_digests({"recipients": {"X1": {"stores": [], "blocking": 0}}},
                    org_id="o", period_start="a", period_end="b"), [])

_built = V.build_ask_digest("The DM", _fan[0]["items"], period_start="2026-10-01",
                            period_end="2026-10-15", gate_weekday=1, run_weekday=2,
                            link="/storeops/payroll/clock-variances")
check("E2f the three parts the delivery ladder requires are all present",
      sorted(_built), ["html", "subject", "text"])
check("E2g the subject counts the BLOCKING rows and the stores, not the org's total",
      _built["subject"],
      "Clock-in corrections needed before payroll: 1 row across 1 store")
# THE OWNER RULING TRAVELS WITH THE ASK. *"mention on the report why you are doing so"* — an email
# that asks a manager to correct a row without saying why the earlier Elevate clock-out is being
# paid is the report the ruling was about, missing its reason.
for _part in ("html", "text"):
    check(f"E2h the WHY is in the {_part} body, in the owner's own terms",
          "time in the store, not time worked" in _built[_part], True)
    check(f"E2i the {_part} body names the store being asked about",
          "B-4712" in _built[_part], True)
    check(f"E2j the {_part} body says when the correction is due and when payroll runs",
          ("weekday 1" in _built[_part] and "weekday 2" in _built[_part]), True)
# THE ASK GOES TO DISTRICT MANAGERS, and money is market-manager-and-above
# (`storeops/pay_visibility.py`, mig 434). A digest that priced the hours would walk straight past
# that gate on its way to an inbox, where no RBAC check can reach it.
for _part in ("html", "text"):
    check(f"E2k NO money figure appears in the {_part} body",
          ("$" in _built[_part] or "pay_rate" in _built[_part] or "gross" in _built[_part]), False)
check("E2l a store spelling with an & in it cannot break the email",
      "&amp;" in V.build_ask_digest("x", [{"store_code": "A & B", "blocking": 1}])["html"], True)


# ════════════════════════════════════════════════════════════════════════════════════════════════
section("F. THE GATE — ★ NO LOCKOUT ★ : confined, never refused")
_g = V.resolve_config({"login_gate_enabled": True})
# THE OWNER'S WORDS WERE "block them from logging in". THIS IS THE ONE PLACE THIS PACKAGE DOES NOT
# DO WHAT HE LITERALLY SAID, and the reason is in §22: a person who cannot sign in cannot fix what
# blocks them, so a refused sign-in would make the Wednesday payroll IMPOSSIBLE, not protected.
_mon = V.gate_decision(weekday=0, role="dm", login="x", blocking_count=3, cfg=_g)
check("F1 on the ASK day the DM is ALERTED but not confined", (_mon["alert"], _mon["gated"]),
      (True, False))
_tue = V.gate_decision(weekday=1, role="dm", login="x", blocking_count=3, cfg=_g)
check("F2 from the GATE day they are confined", (_tue["alert"], _tue["gated"]), (True, True))
check("F3 …to the correction board, which is in the allowed paths",
      V.CORRECTION_PATH in _tue["allowed_paths"], True)
check("F4 ★ and SIGN-IN ITSELF IS ALWAYS ALLOWED — this is not a lockout",
      all(any(p.startswith(x) for x in _tue["allowed_paths"]) for p in ("/login", "/logout")), True)
check("F5 …and the decision NAMES what to fix and where, not merely 'blocked'",
      ("correction" in _tue["reason"] and "sign in" in _tue["reason"]), True)
check("F6 with nothing blocking there is no alert and no gate",
      V.gate_decision(weekday=1, role="dm", login="x", blocking_count=0, cfg=_g),
      dict(_tue, alert=False, gated=False, blocking_count=0, reason=V.gate_decision(
          weekday=1, role="dm", login="x", blocking_count=0, cfg=_g)["reason"]))
check("F7 an ungated role is alerted but never confined",
      [V.gate_decision(weekday=1, role=r, login="x", blocking_count=3, cfg=_g)["gated"]
       for r in ("rep", "sales associate", "store manager")], [False, False, False])
check("F8 the gated roles are CONFIG rows, folded case/space-insensitively",
      V.gate_decision(weekday=1, role=" District Manager ", login="x", blocking_count=3,
                      cfg=_g)["gated"], True)
check("F9 the escape hatch is a config ROW, so no login is named in code",
      V.gate_decision(weekday=1, role="dm", login="owner", blocking_count=3,
                      cfg=V.resolve_config({"login_gate_enabled": True,
                                            "gate_exempt_logins": ("owner",)}))["gated"], False)
# THE WEEKDAYS ARE DATA. The owner's Mon/Tue/Wed are defaults, not a calendar in the code.
check("F10 the house weekday plan is the owner's Monday / Tuesday / Wednesday",
      V.weekday_plan(OFF), {"ask": 0, "gate": 1, "run": 2})
_thu = V.resolve_config({"login_gate_enabled": True, "ask_weekday": 2, "gate_weekday": 3,
                         "run_weekday": 4})
check("F11 an org that runs payroll on Friday moves the whole window with three rows",
      [V.gate_decision(weekday=d, role="dm", login="x", blocking_count=3, cfg=_thu)["gated"]
       for d in range(5)], [False, False, False, True, True])
# The OFF switch is part of the QUESTION, not a separate check a caller might forget to make.
check("F12 while the ask is switched off, no day is an ask day",
      [V.is_ask_day(d, OFF) for d in range(7)], [False] * 7)
check("F13 switched on, it reads the org's OWN ask weekday",
      (V.is_ask_day(0, ON), V.is_ask_day(2, ON),
       V.is_ask_day(2, V.resolve_config({"variance_ask_enabled": True, "ask_weekday": 2}))),
      (True, False, True))


# ════════════════════════════════════════════════════════════════════════════════════════════════
section("G. the payroll RUN gate — and the thirteen employees with no pay rate")
_blk = [r for r in OUT["rows"] if r["blocks"]]
check("G1 the run is refused while variances are uncorrected",
      V.payroll_run_gate(blocking=_blk, cfg=ON, employee_rows=EMPS)["refused"], True)
check("G2 …and the refusal NAMES what blocks it — a refusal without rows is a wall, not a gate",
      all(x in V.payroll_run_gate(blocking=_blk, cfg=ON, employee_rows=EMPS)["reason"]
          for x in ["variance", str(len(_blk))]), True)
check("G3 with nothing blocking the run proceeds",
      V.payroll_run_gate(blocking=[], cfg=ON, employee_rows=EMPS)["refused"], False)
check("G4 an org with the gate OFF is not blocked by variances — today's behaviour exactly",
      V.payroll_run_gate(blocking=_blk, cfg=OFF, employee_rows=EMPS)["refused"], False)
# THE SEPARATE BLOCKER, REPORTED RATHER THAN FIXED. 13 of 46 active hourly house employees carry no
# `pay_rate`, so a run today prices them at $0.00 — which looks like a finished payroll and pays
# nobody. That is a MISSING-DATA defect, and it is reported whether or not the clock feed is on.
_norate = EMPS + [{"employee_id": e, "name": e, "is_active": True, "pay_basis": "hourly"}
                  for e in ("E252", "E257", "E258", "E259", "E260", "E261", "E262",
                            "E263", "E264", "E265", "E266", "E267", "E282")]
check("G5 the thirteen rate-less employees are found, by id",
      [e["employee_id"] for e in V.employees_without_rate(_norate)],
      ["E252", "E257", "E258", "E259", "E260", "E261", "E262", "E263", "E264", "E265",
       "E266", "E267", "E282"])
check("G5b …and each is NAMED, so the refusal is a list of people and not a number",
      all(e.get("name") for e in V.employees_without_rate(_norate)), True)
check("G6 an inactive employee with no rate is NOT reported — they are not being paid",
      V.employees_without_rate([{"employee_id": "X", "is_active": False, "pay_basis": "hourly"}]),
      [])
check("G7 a SALARIED employee with no hourly rate is not a defect either",
      V.employees_without_rate([{"employee_id": "S", "is_active": True, "pay_basis": "salary",
                                 "salary": 60000}]), [])
check("G8 the run is refused for the missing rates EVEN WITH THE VARIANCE GATE OFF",
      V.payroll_run_gate(blocking=[], cfg=OFF, employee_rows=_norate)["refused"], True)
check("G9 …and the refusal says they would be priced at nothing, not that a figure is odd",
      all(x in V.payroll_run_gate(blocking=[], cfg=OFF, employee_rows=_norate)["reason"]
          for x in ["$0.00", "E252", "13"]), True)
check("G10 scoped to the people this RUN would pay, so an unrelated gap does not block a store",
      V.payroll_run_gate(blocking=[], cfg=OFF, employee_rows=_norate,
                         paid_employee_ids={"E1", "E2"})["refused"], False)


# ════════════════════════════════════════════════════════════════════════════════════════════════
section("H. everything ships OFF")
for _k in ("clock_feed_enabled", "variance_ask_enabled", "login_gate_enabled",
           "payroll_run_gate_enabled"):
    check(f"H1 {_k} is FALSE with no config row", V.resolve_config(None)[_k], False)
check("H2 with the gate off, a DM with fifty blocking variances is neither alerted nor confined",
      V.gate_decision(weekday=1, role="dm", login="x", blocking_count=50, cfg=OFF)["gated"], False)
check("H3 the config is read from the SAME per-org row as the hours basis — one row, one feature",
      V.CONFIG_TABLE, B.CONFIG_TABLE)
check("H4 an unknown config key cannot turn anything on",
      V.resolve_config({"enable_everything": True})["login_gate_enabled"], False)


# ════════════════════════════════════════════════════════════════════════════════════════════════
section("Z. ARMED negative control")
_f0 = len(FAIL)
check("Z-armed an unknown variance kind does not block", V.kind_blocks("zzz"), False)
check("Z-armed the DM is confined on the ASK day too, not merely alerted",
      V.gate_decision(weekday=0, role="dm", login="x", blocking_count=3, cfg=_g)["gated"], True)
check("Z-armed Khan's 0.01h difference is a variance",
      [r["kind"] for r in OUT["rows"] if r.get("employee_id") == "E1"], [V.HOURS_DIFFER])
_fired = len(FAIL) - _f0
if _fired == 3:
    FAIL[:] = FAIL[:_f0]
    PASS.append("Z1 negative control fired on all three wrong expectations (the checks are live)")
else:
    FAIL.append(f"Z1 NEGATIVE CONTROL DID NOT FIRE — {_fired}/3 wrong answers accepted.")

print(f"\n{'=' * 78}")
for f in FAIL:
    print(f"  ✗ {f}")
print(f"  PASS {len(PASS)} / {len(PASS) + len(FAIL)}")
print(f"{'=' * 78}")
sys.exit(1 if FAIL else 0)
