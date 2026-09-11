"""PROOF: Capitalised chargeback — a distributor chargeback carried as a balance-sheet ASSET and
expensed as it amortises.

OWNER DIRECTIVE 2026-09-11, verbatim: *"9663.75 is actually being mortised towards the chargeback -
ttoal chargeback is 159106.76 +9663.75 x6 should be in the balance sheet as chargeback"*.

WHAT THIS FILE HOLDS

  §A  THE OWNER'S THREE NUMBERS, reproduced from the real line shapes: gross $217,089.26,
      amortised to date $57,982.50, carrying value $159,106.76.
  §B  DERIVED, NEVER FROZEN. No count of instalments appears anywhere; a seventh, an eighth and a
      gap month all land correctly, and the "× 6" that would have gone stale next month is absent.
  §C  THE TRAP. Matching on line NAME alone sweeps in $571.05 of other people's chargebacks that
      share a date and a word with this event. The scope is location AND name, and §C proves the
      naive rule is wrong by measuring it.
  §D  THREE STATES, NEVER A SILENT ZERO — not configured / measured / genuinely zero.
  §E  RULE TWO. The house default is EMPTY, so no org derives or books anything until an owner
      configures it; no location, vendor or line name appears in the module's code.
  §F  THE CONSEQUENCE THE OWNER SHOULD SEE BEFORE IT SHIPS. Under their stated basis the carrying
      value NEVER declines — $159,106.76 at every as-of date, for ever. Both bases are computed and
      published so the choice is made on numbers.
  §G  AS-OF. The position is answerable at any past date, because a balance sheet is.
  §H  PURITY. No client, no I/O, no writes — provable with no database at all.
  §I  THE P&L LEG. Period amortisation is returned per raw location and re-derives no attribution.

THE LIVE FIGURES ARE A DATED SNAPSHOT, house org 00000000-…-0001, `commcalc.vip_invoice_lines`,
measured 2026-09-11. Every one of them was verified against the live feed before being pinned here.

DB-FREE BY CONSTRUCTION: `_harness_dbfree.install()` patches the client chokepoint and tripwires the
real constructor. This module is PURE, so nothing below ever needs a client — §H proves it.
stdlib only.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import _harness_dbfree                                                          # noqa: E402

P = F = 0


def check(label, ok, detail=""):
    global P, F
    if ok:
        P += 1
        print("  PASS  %s" % label)
    else:
        F += 1
        print("  FAIL  %s   %s" % (label, detail))


def section(t):
    print("\n" + t)
    print("-" * len(t))


class _Dead:
    """Any attribute access is a failure: this module must never reach for a client."""

    def __getattr__(self, name):
        raise AssertionError("chargeback_asset touched a database client (.%s)" % name)


_harness_dbfree.install(_Dead())

from app.modules.account import chargeback_asset as ca                          # noqa: E402

# ── THE FIXTURE: the real line shapes, with the account spelled as the feed spells it ─────────────
ACCOUNT = "228 N Wood Ave"          # the distributor's master/dealer ACCOUNT — a head office
STORE_A = "559 Broadway"            # …and three RETAIL stores, which are not it
STORE_B = "1598 Mt Ephraim Ave"
STORE_C = "5619 N Broad St"

EVENT = "Return Item Chargeback"
NSF = "NSF Fee"
INSTALMENT = "Dealer Chargeback"
CHURN = "Early Life Churn Chargeback"
RETURNS = "Handset Returns Commission Chargeback"

CFG = {"locations": [ACCOUNT], "event_names": [EVENT, NSF], "amortisation_names": [INSTALMENT]}

GROSS = 217089.26
AMORTISED = 57982.50
CARRYING = 159106.76
EVENT_TOTAL = 159106.76
ALT_CARRYING = 101124.26
INSTALMENT_AMT = 9663.75
# the instalment dates exactly as the feed carries them — note FEBRUARY to APRIL: there is no March,
# which is why §B refuses to assume a monthly cadence
INSTALMENT_DATES = ["2026-02-23", "2026-04-03", "2026-05-02",
                    "2026-06-02", "2026-07-02", "2026-08-02"]


def line(name, total, location, date, inv="INV-X", status="Paid In Full"):
    return {"invoice_number": inv, "location": location, "status": status, "name": name,
            "quantity": 1, "total": total, "created_on": date + "T00:00:00+00:00",
            "period_year": int(date[:4]), "period_month": int(date[5:7])}


LINES = [
    # the capitalised EVENT — one invoice, two lines
    line(EVENT, 159056.76, ACCOUNT, "2025-12-29", "1604147"),
    line(NSF, 50.00, ACCOUNT, "2025-12-29", "1604147"),
]
for i, d in enumerate(INSTALMENT_DATES):                      # the amortisation instalments
    LINES.append(line(INSTALMENT, INSTALMENT_AMT, ACCOUNT, d, "16%05d" % i))

# ── THE NOISE: chargeback-NAMED money that is NOT this event. Same word, same day, other places. ──
NOISE = [
    line(EVENT, 30.00, STORE_A, "2025-12-29", "1604144"),
    line(NSF, 50.00, STORE_A, "2025-12-29", "1604144"),
    line(EVENT, 30.00, STORE_B, "2025-12-29", "1604145"),
    line(NSF, 50.00, STORE_B, "2025-12-29", "1604145"),
    line(EVENT, 30.00, STORE_C, "2025-12-29", "1604146"),
    line(NSF, 50.00, STORE_C, "2025-12-29", "1604146"),
    line(RETURNS, 25.00, "6011 Bergenline Ave", "2023-04-18", "1308568"),
]
NOISE += [line(CHURN, 15.94, "2509 Bergenline Ave", "2023-08-17", "1350942") for _ in range(11)]
NOISE += [line(CHURN, 9.56, "2509 Bergenline Ave", "2023-08-17", "1350942")]
NOISE += [line(CHURN, 19.13, "6011 Bergenline Ave", "2023-08-17", "1350970") for _ in range(5)]
NOISE += [line(CHURN, 12.75, "6011 Bergenline Ave", "2023-08-17", "1350970") for _ in range(2)]

ALL = LINES + NOISE
NOISE_TOTAL = round(sum(x["total"] for x in NOISE), 2)

print("=" * 78)
print("CAPITALISED CHARGEBACK — asset on the balance sheet, expensed as it amortises")
print("=" * 78)

R = ca.derive(ALL, CFG)

# ══ §A — THE OWNER'S THREE NUMBERS ═══════════════════════════════════════════════════════════════
section("§A  THE OWNER'S THREE NUMBERS, REPRODUCED — and all three are published, never just the net")

check("A1 gross $217,089.26 = the original chargeback + its NSF fee + every instalment billed",
      R["gross"] == GROSS, R["gross"])
check("A2 amortised to date $57,982.50 — the six instalments the feed actually carries",
      R["amortised_to_date"] == AMORTISED, R["amortised_to_date"])
check("A3 carrying value $159,106.76 = gross less amortisation",
      R["carrying_value"] == CARRYING, R["carrying_value"])
check("A4 all three are in the payload — a reader is never handed the net alone, so the "
      "amortisation is visible rather than implied",
      all(R[k] is not None for k in ("gross", "amortised_to_date", "carrying_value")))
check("A5 …and so are the lines behind them, each with its date, invoice and amount, so every "
      "figure is traceable to the invoice it came from",
      len(R["event_lines"]) == 2 and len(R["amortisation_lines"]) == 6
      and all(set(x) >= {"date", "invoice_number", "name", "amount"} for x in R["event_lines"]))
check("A6 the event leg is $159,106.76 — the December chargeback plus the NSF fee on the SAME "
      "invoice, which is part of the same event and is not dropped for being named differently",
      R["event_total"] == EVENT_TOTAL, R["event_total"])

# ══ §B — DERIVED, NEVER FROZEN ═══════════════════════════════════════════════════════════════════
section("§B  DERIVED FROM THE FEED — no instalment count, so a seventh lands by itself")

src = open(os.path.join(HERE, "app/modules/account/chargeback_asset.py"), encoding="utf-8").read()
import ast                                                                      # noqa: E402


def _strip_docstrings(tree):
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            b = node.body
            if (b and isinstance(b[0], ast.Expr) and isinstance(b[0].value, ast.Constant)
                    and isinstance(b[0].value.value, str)):
                node.body = b[1:] or [ast.Pass()]
    return tree


body = ast.unparse(_strip_docstrings(ast.parse(src)))
check("B1 neither the instalment COUNT nor the instalment AMOUNT is written in the code — the "
      "'× 6' that would have been wrong next month is nowhere in it",
      "9663" not in body and "217089" not in body and "159106" not in body and "57982" not in body,
      [w for w in ("9663", "217089", "159106", "57982") if w in body])

SEVENTH = ALL + [line(INSTALMENT, INSTALMENT_AMT, ACCOUNT, "2026-09-02", "1675000")]
R7 = ca.derive(SEVENTH, CFG)
check("B2 a SEVENTH instalment is picked up with no edit: amortisation rises by exactly one "
      "instalment and the count follows",
      R7["amortised_to_date"] == round(AMORTISED + INSTALMENT_AMT, 2) and R7["instalments"] == 7,
      (R7["amortised_to_date"], R7["instalments"]))
check("B3 the cadence is NOT assumed monthly — the real schedule skips March (Feb 23 → Apr 3) and "
      "the derivation neither interpolates the gap nor counts it as a missed payment",
      R["instalments"] == 6
      and [x["date"] for x in R["amortisation_lines"]] == INSTALMENT_DATES,
      [x["date"] for x in R["amortisation_lines"]])
check("B4 an instalment of a DIFFERENT amount is taken at its own value, not at an assumed one",
      ca.derive(ALL + [line(INSTALMENT, 1234.56, ACCOUNT, "2026-09-02", "1675001")], CFG
                )["amortised_to_date"] == round(AMORTISED + 1234.56, 2))
check("B5 the latest instalment date is published, so a schedule that has silently STOPPED is "
      "visible rather than looking like a settled balance",
      R["latest_instalment"] == INSTALMENT_DATES[-1], R["latest_instalment"])

# ══ §C — THE TRAP ════════════════════════════════════════════════════════════════════════════════
section("§C  THE TRAP: matching on the line NAME alone captures other people's money")

check("C1 the noise is real money and it is NOT nothing — $571.05 of chargeback-named lines that "
      "have nothing to do with this event",
      NOISE_TOTAL == 571.05, NOISE_TOTAL)
LOOSE = {"locations": sorted({x["location"] for x in ALL}),
         "event_names": [EVENT, NSF, CHURN, RETURNS], "amortisation_names": [INSTALMENT]}
check("C2 …and a name-only rule would sweep every cent of it into the asset, overstating the gross "
      "by exactly that amount",
      ca.derive(ALL, LOOSE)["gross"] == round(GROSS + NOISE_TOTAL, 2),
      ca.derive(ALL, LOOSE)["gross"])
check("C3 the real rule is scoped on the ACCOUNT as well as the name, so three retail stores' "
      "$30.00 chargebacks — same word, same day — stay out",
      R["gross"] == GROSS
      and not any(x["location"] != ACCOUNT for x in R["event_lines"] + R["amortisation_lines"]))
check("C4 a line at the account with a name nobody configured is IGNORED rather than guessed at — "
      "the vocabulary is a whitelist, never a pattern",
      ca.derive(ALL + [line("Some Other Fee", 500.0, ACCOUNT, "2026-03-01")], CFG)["gross"] == GROSS)
check("C5 …and a configured name at a location nobody configured is ignored too (the other half of "
      "the same claim)",
      ca.derive(ALL + [line(INSTALMENT, 500.0, STORE_A, "2026-03-01")], CFG)["gross"] == GROSS)
check("C6 matching is case- and whitespace-insensitive, so feed spelling drift cannot silently "
      "drop an instalment and understate the expense",
      ca.derive([line("  dealer CHARGEBACK ", INSTALMENT_AMT, "  228 n wood ave  ", "2026-02-23")],
                CFG)["amortised_to_date"] == INSTALMENT_AMT)

# ══ §D — THREE STATES ════════════════════════════════════════════════════════════════════════════
section("§D  THREE STATES, NEVER A SILENT ZERO")

U = ca.derive(ALL, None)
check("D1 an org with no vocabulary configured is 'not_configured' — not $0.00",
      U["state"] == "not_configured" and U["gross"] is None and U["carrying_value"] is None, U)
check("D2 …with a reason a reader can act on, and no lines emitted",
      isinstance(U["reason"], str) and U["event_lines"] == [] and U["amortisation_lines"] == [])
Z = ca.derive([], CFG)
check("D3 a CONFIGURED org whose feed carries no such line yet is a real, measured $0.00 — the "
      "third state, kept distinct from the other two",
      Z["state"] == "measured" and Z["gross"] == 0.0 and Z["carrying_value"] == 0.0, Z)
check("D4 a HALF-configured org (an account but nothing to look for) measures nothing rather than "
      "matching everything billed to that account",
      ca.derive(ALL, {"locations": [ACCOUNT]})["state"] == "not_configured")
check("D5 …and names with no account scope measure nothing either — that combination IS the §C "
      "trap, so it is refused rather than served",
      ca.derive(ALL, {"event_names": [EVENT]})["state"] == "not_configured")
check("D6 a malformed config degrades to 'not_configured' instead of raising — a config typo must "
      "never take the Balance Sheet down",
      ca.derive(ALL, {"locations": "not-a-list", "event_names": None,
                      "basis": "nonsense"})["state"] == "not_configured")
_S = ca.derive(ALL, {"locations": ACCOUNT, "event_names": EVENT,
                     "amortisation_names": INSTALMENT})
check("D7 …and a single string where a list belongs is accepted as that one value, because a "
      "config that ALMOST works should work rather than silently book nothing. Here only the "
      "chargeback name is configured, so the event leg is $159,056.76 — the NSF fee is correctly "
      "ABSENT, which is the coercion working rather than a wildcard",
      _S["state"] == "measured" and _S["event_total"] == 159056.76
      and _S["gross"] == round(159056.76 + AMORTISED, 2), (_S["state"], _S["event_total"]))

# ══ §E — RULE TWO ════════════════════════════════════════════════════════════════════════════════
section("§E  RULE TWO: empty house default, and no vocabulary in the code")

check("E1 the house default is EMPTY on every vocabulary, so NO org — the house org included — "
      "derives or books anything until an owner configures it",
      ca.DEFAULT_CONFIG["locations"] == [] and ca.DEFAULT_CONFIG["event_names"] == []
      and ca.DEFAULT_CONFIG["amortisation_names"] == [], ca.DEFAULT_CONFIG)
check("E2 …so the default config measures nothing at all against the live line shapes",
      ca.derive(ALL, dict(ca.DEFAULT_CONFIG))["state"] == "not_configured")
check("E3 no location, vendor, tenant or line name appears in the module's CODE — the docstring may "
      "name what was MEASURED; the code may not BRANCH on it",
      not any(w in body.lower() for w in
              ("wood ave", "dealer chargeback", "return item", "nsf", "boost", "vip", "cellular",
               "churn", "syosset")),
      [w for w in ("wood ave", "dealer chargeback", "return item", "nsf", "cellular")
       if w in body.lower()])
check("E4 the module BOOKS nothing and WRITES nothing — no insert, update, upsert or delete",
      not any(w in body for w in (".insert(", ".update(", ".upsert(", ".delete(", ".rpc(")))

# ══ §F — THE CONSEQUENCE ═════════════════════════════════════════════════════════════════════════
section("§F  THE CONSEQUENCE OF THE OWNER'S BASIS, COMPUTED RATHER THAN DISCOVERED LATER")

check("F1 under the owner's stated basis each instalment raises the gross AND the amortisation by "
      "the same amount, so the CARRYING VALUE NEVER DECLINES — it is $159,106.76 today and "
      "$159,106.76 after the seventh",
      R7["carrying_value"] == CARRYING and R["carrying_value"] == CARRYING,
      (R["carrying_value"], R7["carrying_value"]))
check("F2 …and the payload SAYS SO, so nobody has to notice it from the numbers a year from now",
      R["carrying_value_moves"] is False)
check("F3 the other reading — instalments paying DOWN the original — is computed on every run and "
      "published beside it: carrying $101,124.26 today",
      R["alternative"]["basis"] == ca.BASIS_EVENT_ONLY
      and R["alternative"]["carrying_value"] == ALT_CARRYING, R["alternative"])
check("F4 …and under THAT basis the carrying value does decline, which is the distinction the "
      "owner is really choosing between",
      R["alternative"]["carrying_value_moves"] is True
      and ca.derive(SEVENTH, CFG)["alternative"]["carrying_value"]
      == round(ALT_CARRYING - INSTALMENT_AMT, 2))
check("F5 the basis is CONFIG — switching it is a config edit, never a code change",
      ca.derive(ALL, dict(CFG, basis=ca.BASIS_EVENT_ONLY))["carrying_value"] == ALT_CARRYING)
check("F6 an unrecognised basis falls back to the owner's stated one rather than inventing a third",
      ca.derive(ALL, dict(CFG, basis="something-else"))["basis"]
      == ca.BASIS_EVENT_PLUS_INSTALMENTS)

# ══ §G — AS-OF ═══════════════════════════════════════════════════════════════════════════════════
section("§G  THE POSITION IS ANSWERABLE AT ANY PAST DATE, BECAUSE A BALANCE SHEET IS")

Y = ca.derive(ALL, CFG, as_of="2025-12-31")
check("G1 at 2025-12-31 the chargeback had been billed and NOTHING had amortised yet — gross "
      "$159,106.76, amortisation $0.00, carrying $159,106.76",
      Y["gross"] == EVENT_TOTAL and Y["amortised_to_date"] == 0.0
      and Y["carrying_value"] == EVENT_TOTAL, Y)
check("G2 …and that $0.00 is a MEASURED zero, not a missing figure",
      Y["state"] == "measured" and Y["instalments"] == 0)
check("G3 at 2026-04-30 exactly two instalments have landed (Feb and Apr — there is no March)",
      ca.derive(ALL, CFG, as_of="2026-04-30")["amortised_to_date"] == round(INSTALMENT_AMT * 2, 2))
check("G4 the as-of boundary is INCLUSIVE, as a closing date is",
      ca.derive(ALL, CFG, as_of="2026-02-23")["instalments"] == 1
      and ca.derive(ALL, CFG, as_of="2026-02-22")["instalments"] == 0)
check("G5 an omitted as-of means 'everything the feed has', which is what a live balance sheet "
      "wants",
      ca.derive(ALL, CFG, as_of="")["gross"] == GROSS)

# ══ §H — PURITY ══════════════════════════════════════════════════════════════════════════════════
section("§H  PURE: no client, no I/O, no second attribution rule")

check("H1 every figure above was produced with a DEAD client installed — the module never reached "
      "for a database, and could not have",
      R["gross"] == GROSS)
check("H2 the module imports no client, no router and no engine",
      "supabase" not in body and "create_client" not in body and "requests" not in body)
check("H3 it defines no store or company resolver — attribution stays with the platform's shared "
      "ones, and the P&L leg hands back the RAW location for the caller to resolve",
      "def store_resolver" not in src and "def build_company_matcher" not in src
      and "store_resolver" not in body)

# ══ §I — THE P&L LEG ═════════════════════════════════════════════════════════════════════════════
section("§I  THE P&L LEG: period amortisation, per raw location, re-deriving nothing")

feb = ca.amortisation_in_period(ALL, CFG, lambda r: r.get("period_year") == 2026
                                and r.get("period_month") == 2)
check("I1 February 2026 books exactly one instalment, $9,663.75, at the account",
      feb == {ACCOUNT: INSTALMENT_AMT}, feb)
check("I2 MARCH 2026 books nothing — a real, measured nil month, because there was no March "
      "instalment. It is not smoothed and not carried forward",
      ca.amortisation_in_period(ALL, CFG, lambda r: r.get("period_year") == 2026
                                and r.get("period_month") == 3) == {})
check("I3 the EVENT itself never books to the P&L — it is capitalised, and only amortisation is an "
      "expense. December 2025 books $0.00 of expense",
      ca.amortisation_in_period(ALL, CFG, lambda r: r.get("period_year") == 2025
                                and r.get("period_month") == 12) == {})
check("I4 the noise never reaches the P&L leg either",
      ca.amortisation_in_period(NOISE, CFG, lambda _r: True) == {})
check("I5 an unconfigured org books nothing, so the P&L is byte-identical until an owner opts in",
      ca.amortisation_in_period(ALL, None, lambda _r: True) == {})
check("I6 the period predicate is the CALLER's — this module owns no second period rule",
      "period_pred" in ca.amortisation_in_period.__code__.co_varnames)
check("I7 every instalment in the feed is booked in exactly one period, so the year's expense "
      "sums to the amortisation to date and no instalment is booked twice or lost",
      round(sum(ca.amortisation_in_period(
          ALL, CFG, lambda r, m=m: r.get("period_year") == 2026
          and r.get("period_month") == m).get(ACCOUNT, 0.0) for m in range(1, 13)), 2) == AMORTISED)

print()
print("=" * 78)
print("RESULT: %d passed, %d failed" % (P, F))
print("=" * 78)
sys.exit(1 if F else 0)
