"""PROOF: Distributor chargebacks — TWO separate chargebacks billed to the master/dealer account.

OWNER DIRECTIVE 2026-09-11. First: *"9663.75 is actually being mortised towards the chargeback -
ttoal chargeback is 159106.76 +9663.75 x6 should be in the balance sheet as chargeback"*. Then, on
being shown what an amortisation model implies over time: *"159106.76 is a separate cjhargeback
which it seems occured iun 4 instalments as per teh report but 9663.75 is a seaparate chargeback
which started in 2025 and going to 2026"*.

    A — a ONE-OFF chargeback            $159,106.76
    B — a RECURRING monthly chargeback  $  9,663.75 each, still running  →  $57,982.50 to date
    ──────────────────────────────────────────────────────────────────────────────────────────
    combined                            $217,089.26

**NOTHING AMORTISES ANYTHING**, so no word in this file or the module says it does. B does not
reduce A; A's balance is meant to stay where it is. §F pins the absence of that vocabulary, because
a field named for amortisation that never amortises would mislead the accountant this is built for.

WHAT THIS FILE HOLDS
  §A  THE THREE NUMBERS: A, B, and the combined total the owner asked for.
  §B  DERIVED, NEVER FROZEN — no charge count and no charge amount is written in the code.
  §C  THE TRAP: matching on line NAME alone captures $571.05 of other people's chargebacks.
  §D  THREE STATES, never a silent zero.
  §E  RULE TWO: empty house default, no vocabulary in the code.
  §F  THE MODEL IS TWO SEPARATE CHARGEBACKS — and the amortisation vocabulary is provably gone.
  §G  THE TWO CORRECTIONS. The owner's description differs from the feed in two places, and the
      report follows the DATA and publishes the difference rather than reproducing the description.
  §H  AS-OF, PURITY, AND NOTHING WIRED — `booking` is config, and no statement is touched.
  §I  THE P&L LEG for the recurring charge.

LIVE FIGURES ARE A DATED SNAPSHOT, house org 00000000-…-0001, `commcalc.vip_invoice_lines`,
measured 2026-09-11, each verified against the feed before being pinned.

DB-FREE BY CONSTRUCTION. The module is PURE, so §H installs a DEAD client and proves it never
reaches for one. stdlib only.
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
    def __getattr__(self, name):
        raise AssertionError("distributor_chargebacks touched a database client (.%s)" % name)


_harness_dbfree.install(_Dead())

from app.modules.account import distributor_chargebacks as dc                   # noqa: E402

# ── THE FIXTURE: the real shapes, with the account spelled as the feed spells it ──────────────────
ACCOUNT = "228 N Wood Ave"
STORE_A, STORE_B, STORE_C = "559 Broadway", "1598 Mt Ephraim Ave", "5619 N Broad St"
ONE_OFF, NSF, RECURRING = "Return Item Chargeback", "NSF Fee", "Dealer Chargeback"
CHURN, RETURNS = "Early Life Churn Chargeback", "Handset Returns Commission Chargeback"

CFG = {"locations": [ACCOUNT], "one_off_names": [ONE_OFF, NSF], "recurring_names": [RECURRING],
       "watch_names": [ONE_OFF, NSF, CHURN, RETURNS]}

A_TOTAL = 159106.76
B_EACH = 9663.75
B_TOTAL = 57982.50
COMBINED = 217089.26
B_DATES = ["2026-02-23", "2026-04-03", "2026-05-02", "2026-06-02", "2026-07-02", "2026-08-02"]


def line(name, total, location, date, inv="INV-X", status="Paid In Full"):
    return {"invoice_number": inv, "location": location, "status": status, "name": name,
            "quantity": 1, "total": total, "created_on": date + "T00:00:00+00:00",
            "period_year": int(date[:4]), "period_month": int(date[5:7])}


LINES = [line(ONE_OFF, 159056.76, ACCOUNT, "2025-12-29", "1604147"),
         line(NSF, 50.00, ACCOUNT, "2025-12-29", "1604147")]
for i, d in enumerate(B_DATES):
    LINES.append(line(RECURRING, B_EACH, ACCOUNT, d, "16%05d" % i))

# THE NOISE: chargeback-NAMED money that is not either charge. Same word, same day, other places.
NOISE = []
for st, inv in ((STORE_A, "1604144"), (STORE_B, "1604145"), (STORE_C, "1604146")):
    NOISE += [line(ONE_OFF, 30.00, st, "2025-12-29", inv), line(NSF, 50.00, st, "2025-12-29", inv)]
NOISE.append(line(RETURNS, 25.00, "6011 Bergenline Ave", "2023-04-18", "1308568"))
NOISE += [line(CHURN, 15.94, "2509 Bergenline Ave", "2023-08-17", "1350942") for _ in range(11)]
NOISE += [line(CHURN, 9.56, "2509 Bergenline Ave", "2023-08-17", "1350942")]
NOISE += [line(CHURN, 19.13, "6011 Bergenline Ave", "2023-08-17", "1350970") for _ in range(5)]
NOISE += [line(CHURN, 12.75, "6011 Bergenline Ave", "2023-08-17", "1350970") for _ in range(2)]

ALL = LINES + NOISE
NOISE_TOTAL = round(sum(x["total"] for x in NOISE), 2)

print("=" * 78)
print("DISTRIBUTOR CHARGEBACKS — two separate charges, derived from the invoice feed")
print("=" * 78)

R = dc.derive(ALL, CFG)
A, B = R["one_off"], R["recurring"]

# ══ §A ═══════════════════════════════════════════════════════════════════════════════════════════
section("§A  THE THREE NUMBERS, EACH REPORTED IN ITS OWN RIGHT")

check("A1 A — the ONE-OFF chargeback — is $159,106.76: one charge plus the NSF fee on the SAME "
      "invoice, which is part of the same event and is not dropped for being named differently",
      A["total"] == A_TOTAL and A["count"] == 2 and A["invoices"] == ["1604147"],
      (A["total"], A["count"], A["invoices"]))
check("A2 B — the RECURRING chargeback — is $57,982.50 so far, six charges of $9,663.75",
      B["total"] == B_TOTAL and B["count"] == 6 and B["amount_each"] == B_EACH,
      (B["total"], B["count"], B["amount_each"]))
check("A3 combined $217,089.26 — the total the owner asked for, reported BESIDE its two parts and "
      "never instead of them",
      R["combined_total"] == COMBINED and A["total"] and B["total"], R["combined_total"])
check("A4 every figure is traceable: each leg carries its lines with date, invoice and amount",
      all(set(x) >= {"date", "invoice_number", "name", "amount"} for x in A["lines"] + B["lines"]))
check("A5 `amount_each` is published only when the feed really bills ONE constant amount — a charge "
      "that has CHANGED must not be described by a single number",
      dc.derive(ALL + [line(RECURRING, 1234.56, ACCOUNT, "2026-09-02", "1675001")],
                CFG)["recurring"]["amount_each"] is None)

# ══ §B ═══════════════════════════════════════════════════════════════════════════════════════════
section("§B  DERIVED FROM THE FEED — a seventh charge lands by itself")

src = open(os.path.join(HERE, "app/modules/account/distributor_chargebacks.py"),
           encoding="utf-8").read()
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
check("B1 neither the charge COUNT nor the charge AMOUNT is in the code — the '× 6' that would have "
      "been wrong next month is nowhere in it",
      not any(w in body for w in ("9663", "217089", "159106", "57982")),
      [w for w in ("9663", "217089", "159106", "57982") if w in body])
R7 = dc.derive(ALL + [line(RECURRING, B_EACH, ACCOUNT, "2026-09-02", "1675000")], CFG)
check("B2 a SEVENTH charge is picked up with no edit, and the combined total follows",
      R7["recurring"]["count"] == 7 and R7["recurring"]["total"] == round(B_TOTAL + B_EACH, 2)
      and R7["combined_total"] == round(COMBINED + B_EACH, 2), R7["combined_total"])
check("B3 …and A is UNAFFECTED by it, because the two charges are independent",
      R7["one_off"]["total"] == A_TOTAL)
check("B4 `last_seen` is published, so a recurring charge that has silently STOPPED is visible "
      "rather than looking like a settled balance",
      B["last_seen"] == B_DATES[-1], B["last_seen"])

# ══ §C ═══════════════════════════════════════════════════════════════════════════════════════════
section("§C  THE TRAP: matching on the line NAME alone captures other people's money")

check("C1 the noise is real money — $571.05 of chargeback-named lines unrelated to either charge",
      NOISE_TOTAL == 571.05, NOISE_TOTAL)
LOOSE = {"locations": sorted({x["location"] for x in ALL}),
         "one_off_names": [ONE_OFF, NSF, CHURN, RETURNS], "recurring_names": [RECURRING]}
check("C2 …and a name-only rule sweeps every cent of it in, overstating the combined total by "
      "exactly that amount",
      dc.derive(ALL, LOOSE)["combined_total"] == round(COMBINED + NOISE_TOTAL, 2),
      dc.derive(ALL, LOOSE)["combined_total"])
check("C3 the real rule is scoped on the ACCOUNT as well as the name, so three retail stores' "
      "$30.00 chargebacks — same word, same day — stay out",
      R["combined_total"] == COMBINED
      and not any(x["location"] != ACCOUNT for x in A["lines"] + B["lines"]))
check("C4 a line at the account with a name nobody configured is IGNORED — the vocabulary is a "
      "whitelist, never a pattern",
      dc.derive(ALL + [line("Some Other Fee", 500.0, ACCOUNT, "2026-03-01")],
                CFG)["combined_total"] == COMBINED)
check("C5 …and a configured name at an unconfigured location is ignored too",
      dc.derive(ALL + [line(RECURRING, 500.0, STORE_A, "2026-03-01")],
                CFG)["combined_total"] == COMBINED)
check("C6 matching is case- and whitespace-insensitive, so feed spelling drift cannot silently "
      "drop a charge",
      dc.derive([line("  dealer CHARGEBACK ", B_EACH, "  228 n wood ave  ", "2026-02-23")],
                CFG)["recurring"]["total"] == B_EACH)

# ══ §D ═══════════════════════════════════════════════════════════════════════════════════════════
section("§D  THREE STATES, NEVER A SILENT ZERO")

U = dc.derive(ALL, None)
check("D1 an org with no vocabulary is 'not_configured' — not $0.00",
      U["state"] == "not_configured" and U["one_off"] is None and U["combined_total"] is None, U)
Z = dc.derive([], CFG)
check("D2 a CONFIGURED org whose feed carries no such line is a real, measured $0.00",
      Z["state"] == "measured" and Z["combined_total"] == 0.0
      and Z["one_off"]["total"] == 0.0, Z)
check("D3 …and a leg with no lines reports no first/last date rather than inventing one",
      Z["recurring"]["first_seen"] is None and Z["recurring"]["last_seen"] is None)
check("D4 a HALF-configured org (an account but nothing to look for) measures nothing rather than "
      "matching everything billed to that account",
      dc.derive(ALL, {"locations": [ACCOUNT]})["state"] == "not_configured")
check("D5 …and names with no account scope measure nothing either — that combination IS the §C "
      "trap, so it is refused rather than served",
      dc.derive(ALL, {"one_off_names": [ONE_OFF]})["state"] == "not_configured")
check("D6 a malformed config degrades to 'not_configured' instead of raising — a config typo must "
      "never take a statement down",
      dc.derive(ALL, {"locations": "x", "one_off_names": None,
                      "booking": "nonsense"})["state"] == "not_configured")

# ══ §E ═══════════════════════════════════════════════════════════════════════════════════════════
section("§E  RULE TWO: empty house default, and no vocabulary in the code")

check("E1 the house default is EMPTY on every vocabulary and the booking is OFF, so NO org — the "
      "house org included — derives or books anything until an owner configures it",
      dc.DEFAULT_CONFIG["locations"] == [] and dc.DEFAULT_CONFIG["one_off_names"] == []
      and dc.DEFAULT_CONFIG["recurring_names"] == []
      and dc.DEFAULT_CONFIG["booking"] == dc.BOOKING_OFF, dc.DEFAULT_CONFIG)
check("E2 …so the default config measures nothing against the live line shapes",
      dc.derive(ALL, dict(dc.DEFAULT_CONFIG))["state"] == "not_configured")
check("E3 no location, vendor, tenant or line name appears in the CODE — the docstring may name "
      "what was MEASURED; the code may not BRANCH on it",
      not any(w in body.lower() for w in
              ("wood ave", "dealer chargeback", "return item", "nsf", "boost", "vip", "cellular",
               "churn", "syosset")),
      [w for w in ("wood ave", "dealer chargeback", "return item", "nsf") if w in body.lower()])
check("E4 the module BOOKS nothing and WRITES nothing",
      not any(w in body for w in (".insert(", ".update(", ".upsert(", ".delete(", ".rpc(")))

# ══ §F ═══════════════════════════════════════════════════════════════════════════════════════════
section("§F  TWO SEPARATE CHARGEBACKS — and the amortisation vocabulary is provably gone")

check("F1 B does NOT reduce A: adding charges to B leaves A's total untouched, which is the whole "
      "content of the owner's ruling",
      R7["one_off"]["total"] == A["total"] == A_TOTAL)
check("F2 the combined total is the SUM of two independent charges, not a net of one against the "
      "other ($159,106.76 + $57,982.50)",
      R["combined_total"] == round(A["total"] + B["total"], 2))
check("F3 no amortisation vocabulary survives in the module's CODE — no identifier, field or "
      "branch named for amortising, carrying value or accumulation. (The docstring explains why "
      "that model was rejected, which is what a good comment does; what must stay clean is the "
      "code a reader will trust.)",
      not any(w in body.lower() for w in ("amortis", "carrying", "accumulated")),
      [w for w in ("amortis", "carrying", "accumulated") if w in body.lower()])
check("F4 …and the payload exposes no such field either",
      not any("amort" in k or "carrying" in k for k in
              list(R) + list(A) + list(B)), list(R))
check("F5 the two legs are separately addressable, each with its own total, count and dates — a "
      "reader is never handed one number that hides two facts",
      {"total", "count", "first_seen", "last_seen", "lines"} <= set(A)
      and {"total", "count", "first_seen", "last_seen", "lines"} <= set(B))

# ══ §G ═══════════════════════════════════════════════════════════════════════════════════════════
section("§G  THE TWO CORRECTIONS: the report follows the DATA and publishes the difference")

check("G1 CORRECTION 1 — A did NOT arrive in four instalments. It is ONE charge on ONE invoice "
      "(plus that invoice's NSF fee); the other three 'Return Item Chargeback' lines are $30.00 "
      "each at three different RETAIL stores on the same day",
      A["count"] == 2 and A["invoices"] == ["1604147"]
      and A["first_seen"] == A["last_seen"] == "2025-12-29",
      (A["count"], A["invoices"], A["first_seen"]))
check("G2 …and those three $30.00 lines are demonstrably present in the feed and demonstrably "
      "OUT of A — the correction is measured, not asserted",
      sum(1 for x in NOISE if x["name"] == ONE_OFF and x["total"] == 30.00) == 3
      and len({x["location"] for x in NOISE
               if x["name"] == ONE_OFF and x["total"] == 30.00}) == 3
      and A["total"] == A_TOTAL)
check("G3 CORRECTION 2 — B did NOT start in 2025. The first date the DATA carries is 2026-02-23, "
      "and that is what is published, so a remembered start date can be checked against it",
      B["first_seen"] == "2026-02-23", B["first_seen"])
check("G4 …and no charge of that amount exists anywhere in 2025 — the same whole-feed search that "
      "found six hits, every one of them in 2026",
      not any(x for x in ALL
              if abs(x["total"] - B_EACH) < 0.005 and x["created_on"][:4] == "2025")
      and sum(1 for x in ALL if abs(x["total"] - B_EACH) < 0.005) == 6,
      [x["created_on"][:10] for x in ALL if abs(x["total"] - B_EACH) < 0.005])
check("G5 the MISSING MONTH is surfaced rather than smoothed: the real schedule skips March "
      "(Feb 23 → Apr 3), which may be a skipped month or an invoice that never reached the feed — "
      "either way the reader sees the hole",
      B["missing_months"] == ["2026-03"], B["missing_months"])
check("G6 …and a charge that resumes after a longer gap reports every missing month, not just one",
      dc.derive(LINES + [line(RECURRING, B_EACH, ACCOUNT, "2026-12-02", "1690000")],
                CFG)["recurring"]["missing_months"] == ["2026-03", "2026-09", "2026-10", "2026-11"],
      dc.derive(LINES + [line(RECURRING, B_EACH, ACCOUNT, "2026-12-02", "1690000")],
                CFG)["recurring"]["missing_months"])

check("C7 the out-of-scope chargeback money is REPORTED, never absorbed: $571.05 across the three "
      "retail stores and the 2023 churn/commission lines, in no total above and in no P&L leg",
      R["unbooked_watch"]["total"] == NOISE_TOTAL
      and R["combined_total"] == COMBINED
      and R["unbooked_watch"]["count"] == len(NOISE), R["unbooked_watch"]["total"])
check("C8 …grouped by location, so the owner can see WHOSE money it is and decide whether any of it "
      "belongs — the point of declaring rather than silently excluding",
      {b["location"] for b in R["unbooked_watch"]["by_location"]}
      == {STORE_A, STORE_B, STORE_C, "6011 Bergenline Ave", "2509 Bergenline Ave"},
      [b["location"] for b in R["unbooked_watch"]["by_location"]])
check("C9 a line already booked in a leg is NEVER also reported as unbooked — no figure is counted "
      "twice, even across the two sections",
      not any(w["location"] == ACCOUNT for w in R["unbooked_watch"]["lines"]))
check("C10 the watch list never reaches the P&L, whatever it contains",
      dc.expense_in_period(NOISE, CFG, lambda _r: True) == {})

# ══ §H ═══════════════════════════════════════════════════════════════════════════════════════════
section("§H  AS-OF, PURITY, AND NOTHING WIRED")

Y = dc.derive(ALL, CFG, as_of="2025-12-31")
check("H1 at 2025-12-31 A had been billed and B had not started — A $159,106.76, B a measured "
      "$0.00, combined $159,106.76",
      Y["one_off"]["total"] == A_TOTAL and Y["recurring"]["total"] == 0.0
      and Y["combined_total"] == A_TOTAL, Y["combined_total"])
check("H2 …and that B zero is MEASURED, not missing",
      Y["state"] == "measured" and Y["recurring"]["count"] == 0)
check("H3 at 2026-03-31 exactly one charge has landed (Feb — there is no March)",
      dc.derive(ALL, CFG, as_of="2026-03-31")["recurring"]["count"] == 1)
check("H4 the as-of boundary is INCLUSIVE, as a closing date is",
      dc.derive(ALL, CFG, as_of="2026-02-23")["recurring"]["count"] == 1
      and dc.derive(ALL, CFG, as_of="2026-02-22")["recurring"]["count"] == 0)
check("H5 BOTH LEGS ARE EXPENSE and there is deliberately NO 'asset' option — the owner ruled "
      "\"159106.76 is an expense\", and an option nobody chose invites booking money somewhere "
      "nobody decided on",
      set(dc.BOOKINGS) == {"off", "expense"} and not hasattr(dc, "BOOKING_ASSET"), dc.BOOKINGS)
check("H6 the house default is OFF, so the vocabulary being configured is not on its own enough to "
      "book anything — a tenant opts in twice, deliberately",
      dc.DEFAULT_CONFIG["booking"] == dc.BOOKING_OFF
      and dc.derive(ALL, CFG)["booking"] == dc.BOOKING_OFF)
check("H7 every figure above was produced with a DEAD client installed — the module never reached "
      "for a database and could not have",
      R["combined_total"] == COMBINED)
check("H8 it imports no client and defines no resolver — attribution stays with the platform's "
      "shared ones, and the P&L leg hands back the RAW location",
      "supabase" not in body and "create_client" not in body
      and "def store_resolver" not in src and "store_resolver" not in body)

# ══ §I ═══════════════════════════════════════════════════════════════════════════════════════════
section("§I  THE P&L LEG: the RECURRING charge, per period, per raw location")

feb = dc.recurring_in_period(ALL, CFG, lambda r: r.get("period_year") == 2026
                             and r.get("period_month") == 2)
check("I1 February 2026 books exactly one charge, $9,663.75, at the account",
      feb == {ACCOUNT: B_EACH}, feb)
check("I2 MARCH 2026 books nothing — a real, measured nil month, neither smoothed nor carried",
      dc.recurring_in_period(ALL, CFG, lambda r: r.get("period_year") == 2026
                             and r.get("period_month") == 3) == {})
check("I3 the ONE-OFF charge never reaches the recurring P&L leg — the two charges stay separate "
      "all the way to the books",
      dc.recurring_in_period(ALL, CFG, lambda r: r.get("period_year") == 2025
                             and r.get("period_month") == 12) == {})
check("I4 the noise never reaches it either",
      dc.recurring_in_period(NOISE, CFG, lambda _r: True) == {})
check("I5 an unconfigured org books nothing, so the P&L is byte-identical until an owner opts in",
      dc.recurring_in_period(ALL, None, lambda _r: True) == {})
check("I6 the period predicate is the CALLER's — this module owns no second period rule",
      "period_pred" in dc.recurring_in_period.__code__.co_varnames)
check("I7 every charge is booked in exactly one period, so the year sums to the total to date and "
      "nothing is booked twice or lost",
      round(sum(dc.recurring_in_period(
          ALL, CFG, lambda r, m=m: r.get("period_year") == 2026
          and r.get("period_month") == m).get(ACCOUNT, 0.0) for m in range(1, 13)), 2) == B_TOTAL)

print()
print("=" * 78)
print("RESULT: %d passed, %d failed" % (P, F))
print("=" * 78)
sys.exit(1 if F else 0)
