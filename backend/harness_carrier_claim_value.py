"""A CLAIM WITH NO AMOUNT CANNOT BE FILED — the §COMMCALC-22 lock.

WHAT THIS HOLDS IN PLACE. Two writers fill `commcalc.discrepancy_results`. The rate-card engine
(`discrepancy_engine`) sizes each claim; the carrier-statement recon (`ma_recon`) hardcoded `0.0` on
every row, so the Commission Discrepancy hub's open-$ card summed to $0.00 for a whole tenant and no
claim could be filed. One home now answers "is this non-payment a claim at all, and what is it
worth" for BOTH, and this harness fails the build if either stops asking it or starts writing a
zero again.

THE TWO DIRECTIONS THAT MATTER (§66 posture, twice):
  • `is_claimable` is an ALLOWLIST of states — an unruled state is NOT filed.
  • `value_is_measured` is an ALLOWLIST of bases — an unruled basis carries NO figure.
A caller that forgets to resolve one therefore files nothing and shows a blank, never a wrong dollar
amount in a letter to a carrier.

AND THE ONE THAT NEARLY GOT AWAY (§D): an EMPTY day-coverage map means the statement's coverage is
unknown, not that a day is missing. Asserting a feed gap from absent metadata is the same
"absence read as a finding" defect §19.28 / §66 / §COMMCALC-09 / §COMMCALC-21 each already outlawed,
and it would have mislabelled every unpaid row in a statement whose rows carry no dates.

NOT DONE, deliberately: no priced figure moves. The rate-card engine rounds a sized claim exactly as
it always did (§F proves it), and nothing here decides whether the carrier paid — that stays the
mig-308 gate's job.

DB-FREE: the module is pure, and the wiring checks read source text.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from app.modules.commcalc import carrier_claim_value as ccv            # noqa: E402
from app.modules.core.module_graph import home_under_app               # noqa: E402

APP = os.path.join(HERE, "app")
RECON = os.path.join(APP, "modules", "commcalc", "ma_recon.py")
BOOST = os.path.join(APP, "modules", "commcalc", "discrepancy_engine.py")
ROUTER = os.path.join(APP, "modules", "commcalc", "router.py")
# The home is DEREFERENCED from module_graph, never spelled here (§19.18).
HOME = os.path.join(APP, home_under_app("carrier_claim_value"))

P = F = 0


def check(label, ok, detail=None):
    global P, F
    if ok:
        P += 1
        print("  ok  " + label)
    else:
        F += 1
        print("  XX  " + label + (("  -> " + repr(detail)) if detail is not None else ""))


def read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


HOMESRC = read(HOME)
RECONSRC = read(RECON)
BOOSTSRC = read(BOOST)
ROUTERSRC = read(ROUTER)


def code_only(src):
    """Source with docstrings and comments stripped, so a RULE TWO check cannot be tripped by prose
    that merely DISCUSSES a carrier or a tenant."""
    out = re.sub(r'"""(?:.|\n)*?"""', "", src)
    out = re.sub(r"'''(?:.|\n)*?'''", "", out)
    return re.sub(r"(?m)#.*$", "", out)


print("── A. the four shapes of a non-payment, and only one is a claim ──")
check("the four states are distinct strings",
      len({ccv.STATE_OPEN_CLAIM, ccv.STATE_NOT_YET_STATEMENTED,
           ccv.STATE_STATEMENT_DAY_MISSING, ccv.STATE_EXPLAINED_BY_RULE}) == 4)
check("NON_PAYMENT_STATES lists exactly those four",
      set(ccv.NON_PAYMENT_STATES) == {ccv.STATE_OPEN_CLAIM, ccv.STATE_NOT_YET_STATEMENTED,
                                      ccv.STATE_STATEMENT_DAY_MISSING, ccv.STATE_EXPLAINED_BY_RULE}
      and len(ccv.NON_PAYMENT_STATES) == 4)
check("ONLY the open claim is filable", ccv.is_claimable(ccv.STATE_OPEN_CLAIM)
      and not ccv.is_claimable(ccv.STATE_NOT_YET_STATEMENTED)
      and not ccv.is_claimable(ccv.STATE_STATEMENT_DAY_MISSING)
      and not ccv.is_claimable(ccv.STATE_EXPLAINED_BY_RULE))
check("a not-yet-due sale is never filed — the carrier has not reported on it",
      not ccv.is_claimable(ccv.STATE_NOT_YET_STATEMENTED))
check("a missing statement day is never filed — it is OUR feed gap, not their refusal",
      not ccv.is_claimable(ccv.STATE_STATEMENT_DAY_MISSING))

print("── B. the allowlist DIRECTION: an unruled state is not a claim ──")
for junk in ("", None, "open", "OPEN_CLAIM", "claim", "unknown_state", "withheld", 0, [], {}):
    check("is_claimable(%r) is False — never filed on a state nobody ruled on" % (junk,),
          ccv.is_claimable(junk) is False)
check("is_claimable always returns a real bool, never a truthy string",
      all(isinstance(ccv.is_claimable(x), bool)
          for x in (ccv.STATE_OPEN_CLAIM, "nonsense", None)))

print("── C. the classifier: order of evidence strength ──")
DAYS = {"2026-08-01": 4, "2026-08-02": 7, "2026-08-04": 2}
LAST = "2026-08-04"
RULE = {"rule_key": "byod-upgrade-no-payout", "description": "BYOD Upgrade carries no payout"}
check("sold past the statement's last day → not yet statemented, whatever else is true",
      ccv.classify_non_payment("2026-08-09", LAST, DAYS, RULE) == ccv.STATE_NOT_YET_STATEMENTED)
check("a day the statement demonstrably skipped → statement day missing, BEFORE any rule speaks",
      ccv.classify_non_payment("2026-08-03", LAST, DAYS, RULE) == ccv.STATE_STATEMENT_DAY_MISSING)
check("a covered day with a matched rule → explained by the rule",
      ccv.classify_non_payment("2026-08-02", LAST, DAYS, RULE) == ccv.STATE_EXPLAINED_BY_RULE)
check("a covered day with NO rule → the open claim",
      ccv.classify_non_payment("2026-08-02", LAST, DAYS, None) == ccv.STATE_OPEN_CLAIM)
check("a day present in the map but with ZERO rows is still a missing day",
      ccv.classify_non_payment("2026-08-05", "2026-08-06", {"2026-08-05": 0, "2026-08-06": 3}, None)
      == ccv.STATE_STATEMENT_DAY_MISSING)
check("the last day itself is covered, not 'past the end'",
      ccv.classify_non_payment("2026-08-04", LAST, DAYS, None) == ccv.STATE_OPEN_CLAIM)
check("an unusable sale date asserts NEITHER feed fact — falls through to the rule",
      ccv.classify_non_payment("", LAST, DAYS, RULE) == ccv.STATE_EXPLAINED_BY_RULE)
check("an unusable sale date with no rule is an open claim, not an invented gap",
      ccv.classify_non_payment(None, LAST, DAYS, None) == ccv.STATE_OPEN_CLAIM)
check("a timestamp is truncated to its day, not rejected",
      ccv.classify_non_payment("2026-08-03T11:04:00Z", LAST, DAYS, None)
      == ccv.STATE_STATEMENT_DAY_MISSING)
check("classify_non_payment never raises on junk",
      ccv.classify_non_payment({}, [], "nope", object()) in ccv.NON_PAYMENT_STATES)

print("── D. ABSENCE OF COVERAGE IS NOT A GAP (the defect that nearly shipped) ──")
check("empty day map + no rule → OPEN CLAIM, never a fabricated missing day",
      ccv.classify_non_payment("2026-08-02", "", {}, None) == ccv.STATE_OPEN_CLAIM)
check("empty day map + a rule → the rule explains it; still no fabricated gap",
      ccv.classify_non_payment("2026-08-02", "", {}, RULE) == ccv.STATE_EXPLAINED_BY_RULE)
check("None day map behaves exactly as an empty one",
      ccv.classify_non_payment("2026-08-02", "", None, None) == ccv.STATE_OPEN_CLAIM)
check("a known last day still works with no day map (the two tests are independent)",
      ccv.classify_non_payment("2026-08-09", LAST, {}, None) == ccv.STATE_NOT_YET_STATEMENTED)
check("coverage known for OTHER days does assert a gap (the test is not disabled wholesale)",
      ccv.classify_non_payment("2026-08-03", LAST, DAYS, None) == ccv.STATE_STATEMENT_DAY_MISSING)

print("── E. one sentence per state, in one place ──")
check("every state has its own reason sentence",
      all(s in ccv.STATE_REASON for s in ccv.NON_PAYMENT_STATES))
check("the sentences are distinct", len({ccv.STATE_REASON[s] for s in ccv.NON_PAYMENT_STATES}) == 4)
check("an unruled state yields 'could not be established', never a guess",
      ccv.non_payment_reason("something_new") == ccv.STATE_REASON_UNKNOWN
      and ccv.non_payment_reason(None) == ccv.STATE_REASON_UNKNOWN)
check("the tenant's OWN rule description wins for the rule-explained state",
      ccv.non_payment_reason(ccv.STATE_EXPLAINED_BY_RULE, RULE) == "BYOD Upgrade carries no payout")
check("a rule with no description falls back to its key, then to the registry line",
      ccv.non_payment_reason(ccv.STATE_EXPLAINED_BY_RULE, {"rule_key": "k"}) == "k"
      and ccv.non_payment_reason(ccv.STATE_EXPLAINED_BY_RULE, {})
      == ccv.STATE_REASON[ccv.STATE_EXPLAINED_BY_RULE])
check("the missing-day sentence says it is OUR feed, not the carrier's refusal",
      "feed" in ccv.STATE_REASON[ccv.STATE_STATEMENT_DAY_MISSING])
check("every state has a short label for a screen",
      all(s in ccv.STATE_LABELS for s in ccv.NON_PAYMENT_STATES))

print("── F. the claim VALUE: a comparable with its sample, or nothing at all ──")
ROWS = [{"p": "August 2026", "t": "Activation", "a": 100.0},
        {"p": "August 2026", "t": "activation", "a": 200.0},
        {"p": "August 2026", "t": " Activation ", "a": 300.0},
        {"p": "August 2026", "t": "Port with IDV", "a": 500.0},
        {"p": "August 2026", "t": "Port with IDV", "a": 520.0},
        {"p": "July 2026", "t": "Activation", "a": 10.0},
        {"p": "August 2026", "t": "Activation", "a": 0.0},       # no money moved — contributes nothing
        {"p": "August 2026", "t": "Activation", "a": None},      # unusable — contributes nothing
        {"p": "August 2026", "t": "Activation", "a": "junk"}]    # unusable — contributes nothing
IDX = ccv.build_comparable_index(ROWS, lambda r: r["p"], lambda r: r["t"], lambda r: r["a"])
amt, basis, n = ccv.claim_value(IDX, "August 2026", "Activation")
check("three observations give a median and the measured basis", (amt, basis, n) == (200.0, ccv.BASIS_COMPARABLE_MEDIAN, 3), (amt, basis, n))
check("the contract family is case- and space-insensitive (one family, not three)", n == 3)
check("a zero / None / junk amount contributes nothing to the comparable", n == 3)
amt2, basis2, n2 = ccv.claim_value(IDX, "August 2026", "Port with IDV")
check("a TWO-observation comparable is withheld — an anecdote is not a comparable",
      amt2 is None and basis2 == ccv.BASIS_NOT_MEASURED and n2 == 2, (amt2, basis2, n2))
amt3, basis3, n3 = ccv.claim_value(IDX, "August 2026", "Never Seen")
check("an absent comparable is withheld with sample 0 — NEVER 0.00",
      amt3 is None and basis3 == ccv.BASIS_NOT_MEASURED and n3 == 0, (amt3, basis3, n3))
check("the comparable is scoped to the PERIOD — July's rate never prices August",
      ccv.claim_value(IDX, "July 2026", "Activation")[0] is None)
check("MIN_COMPARABLE_SAMPLE is 3 — the smallest sample whose median is not an endpoint",
      ccv.MIN_COMPARABLE_SAMPLE == 3)
check("a caller may demand a thicker sample, never a thinner one than 1",
      ccv.claim_value(IDX, "August 2026", "Activation", min_sample=4)[0] is None
      and ccv.claim_value(IDX, "August 2026", "Port with IDV", min_sample=0)[0] == 510.0)
check("magnitude is used, so a payout signed negative still prices a claim",
      ccv.build_comparable_index([{"p": "P", "t": "T", "a": -5.0}, {"p": "P", "t": "T", "a": -7.0},
                                  {"p": "P", "t": "T", "a": -9.0}],
                                 lambda r: r["p"], lambda r: r["t"], lambda r: r["a"])
      == {ccv.comparable_key("P", "T"): (7.0, 3)})
check("an even sample averages the two middles", ccv.build_comparable_index(
      [{"p": "P", "t": "T", "a": v} for v in (1.0, 2.0, 3.0, 10.0)],
      lambda r: r["p"], lambda r: r["t"], lambda r: r["a"])[ccv.comparable_key("P", "T")] == (2.5, 4))
check("build_comparable_index never raises on junk",
      ccv.build_comparable_index(None, lambda r: "", lambda r: "", lambda r: 0) == {})
check("claim_value never raises on junk",
      ccv.claim_value(None, None, None) == (None, ccv.BASIS_NOT_MEASURED, 0))

print("── G. the basis allowlist, and a gap that cannot be invented ──")
check("only the comparable basis is measured", ccv.value_is_measured(ccv.BASIS_COMPARABLE_MEDIAN)
      and not ccv.value_is_measured(ccv.BASIS_NOT_MEASURED))
for junk in ("", None, "median", "COMPARABLE_MEDIAN", "estimate", "rate_card", 0, [], {}):
    check("value_is_measured(%r) is False — an unruled basis carries no figure" % (junk,),
          ccv.value_is_measured(junk) is False)
check("a gap exists only where the expected side was measured",
      ccv.claim_gap(None, 0.0) is None and ccv.claim_gap(None, 25.0) is None)
check("a measured expected gives the arithmetic, to the cent",
      ccv.claim_gap(100.0, 25.0) == 75.0 and ccv.claim_gap(19.999, 0) == 20.0
      and ccv.claim_gap("100.50", "0.25") == 100.25)
check("a received figure ALONE never manufactures a gap", ccv.claim_gap(None, 999.99) is None)
check("claim_gap never raises on junk", ccv.claim_gap("x", "y") is None)

print("── H. the summary is honest about what it could not price ──")
S = ccv.summarize([
    {"state": ccv.STATE_OPEN_CLAIM, "expected_amount": 100.0, "claim_basis": ccv.BASIS_COMPARABLE_MEDIAN},
    {"state": ccv.STATE_OPEN_CLAIM, "expected_amount": 50.0, "claim_basis": ccv.BASIS_COMPARABLE_MEDIAN},
    {"state": ccv.STATE_OPEN_CLAIM, "expected_amount": None, "claim_basis": ccv.BASIS_NOT_MEASURED},
    {"state": ccv.STATE_NOT_YET_STATEMENTED, "expected_amount": 900.0, "claim_basis": ccv.BASIS_COMPARABLE_MEDIAN},
    {"state": ccv.STATE_STATEMENT_DAY_MISSING, "expected_amount": 900.0, "claim_basis": ccv.BASIS_COMPARABLE_MEDIAN},
    {"state": ccv.STATE_EXPLAINED_BY_RULE, "expected_amount": 900.0, "claim_basis": ccv.BASIS_COMPARABLE_MEDIAN},
    {"state": "invented_later", "expected_amount": 900.0, "claim_basis": ccv.BASIS_COMPARABLE_MEDIAN},
])
check("only claimable states are counted as claims", S["claimable"] == 3, S)
check("only MEASURED claim values are summed — $150, not $1,050", S["claimable_amount"] == 150.0, S)
check("the unpriced claim is COUNTED, so no total pretends to be complete",
      S["claimable_not_measured"] == 1)
check("a not-yet-due / missing-day / ruled row contributes NOTHING to the claim total",
      S["claimable_amount"] == 150.0 and S["by_state"][ccv.STATE_NOT_YET_STATEMENTED] == 1
      and S["by_state"][ccv.STATE_STATEMENT_DAY_MISSING] == 1
      and S["by_state"][ccv.STATE_EXPLAINED_BY_RULE] == 1)
check("a state nobody ruled on is counted separately and never filed",
      S["states_not_ruled_on"] == 1 and S["claimable"] == 3)
check("the summary carries the caveat, so no screen can overstate the figure",
      S["value_caveat"] == ccv.CLAIM_VALUE_CAVEAT and "not an invoice" in ccv.CLAIM_VALUE_CAVEAT)
check("summarize never raises on junk",
      ccv.summarize(None)["claimable"] == 0 and ccv.summarize([None, {}])["claimable"] == 0)

print("── I. BOTH writers dereference the one home (the §19.18 un-wire test) ──")
check("the carrier-statement recon imports the one home",
      re.search(r"(?m)^from app\.modules\.commcalc import carrier_claim_value as ccv\s*$", RECONSRC))
check("the rate-card engine imports the one home too — the sibling is fixed, not excused",
      "carrier_claim_value as ccv" in BOOSTSRC)
check("the recon's claim context is REQUIRED, with no default — it cannot silently un-wire",
      re.search(r"def reconcile_ma_activations\(sold_idx, paid_idx, rules, period, cfg, claim_ctx\)",
                RECONSRC))
check("the recon asks the one home which SHAPE the non-payment is",
      "ccv.classify_non_payment(" in RECONSRC)
check("the recon asks the one home what it is WORTH",
      "ccv.claim_value(" in RECONSRC)
check("the recon takes its reason sentence from the one home, composing none",
      "ccv.non_payment_reason(" in RECONSRC)
check("the recon's gap is the one home's subtraction", "ccv.claim_gap(" in RECONSRC)
check("the rate-card engine's gap is the one home's subtraction too",
      "ccv.claim_gap(" in BOOSTSRC)
check("the recon's claim totals come from the one home", "ccv.summarize(" in RECONSRC)
check("the router hands the context in rather than letting it default",
      "ma_recon.build_claim_context(" in ROUTERSRC)
check("the context builder reuses the statement rows already loaded — no second read",
      re.search(r"def build_claim_context\(ma_rows, period, cfg=None\)", RECONSRC))

print("── J. the hardcoded zero is GONE from both writers ──")
check("the recon no longer writes a literal 0.0 expected amount",
      '"expected_amount": 0.0' not in RECONSRC)
check("the recon no longer writes a literal 0.0 gap", '"gap": 0.0,' not in RECONSRC)
check("the recon no longer asserts a 0.0 commissionable MRC it cannot know",
      '"commissionable_mrc": 0.0' not in RECONSRC)
check("the recon's expected amount IS the one home's figure",
      '"expected_amount": expected,' in RECONSRC)
check("the rate-card engine no longer files a 0.0 unsizeable claim",
      'add("SOLD_NOT_IN_MI", 0.0' not in BOOSTSRC
      and 'add("SOLD_NOT_IN_MI", None' in BOOSTSRC)
check("the rate-card engine writes NULL, not 0.00, for a claim it could not size",
      'round(expected, 2) if sized else None' in BOOSTSRC)
check("a NULL gap can no longer be compared with > in the dedupe (TypeError guard)",
      "_gap_rank(" in BOOSTSRC and 'r["gap"] > seen[key]["gap"]' not in BOOSTSRC)
check("NO second copy of the state or basis vocabulary outside the one home",
      all(lit not in code_only(RECONSRC) + code_only(BOOSTSRC) + code_only(ROUTERSRC)
          for lit in ('"open_claim"', '"not_yet_statemented"', '"statement_day_missing"',
                      '"explained_by_rule"', '"comparable_median"', '"not_measured"')))

print("── K. the comparable is built from the CONFIGURED money columns (RULE TWO) ──")
check("the recon derives the value columns from config, not a literal set",
      re.search(r"def _month1_value_columns\(cfg\)", RECONSRC)
      and "ma_month_field_prefix" in RECONSRC and "ma_month1_extra_fields" in RECONSRC
      and "ma_payout_sign" in RECONSRC)
check("the one home names no carrier, tenant, market or product",
      not re.search(r"(?i)\b(boost|total|luxelink|cellfonz|vzone|verizon|t-?mobile|at&t|"
                    r"vidapay|iphone|samsung|motorola)\b", code_only(HOMESRC)))
check("the one home takes ACCESSORS, so it knows nothing about a statement row's shape",
      re.search(r"def build_comparable_index\(paid_rows, period_of, type_of, amount_of\)", HOMESRC))
check("the one home does no I/O and reaches for no app module",
      "from app" not in HOMESRC and "import app" not in HOMESRC
      and ".table(" not in HOMESRC and "client" not in code_only(HOMESRC))

print("── Z. armed controls — each would FAIL if this harness had gone blind ──")
check("Z1 a deliberately wrong claimable verdict is caught",
      not ccv.is_claimable("open_claim_x"))
check("Z2 a deliberately wrong basis verdict is caught",
      not ccv.value_is_measured("comparable_median_x"))
check("Z3 the sources really were read", len(HOMESRC) > 2000 and len(RECONSRC) > 2000
      and len(BOOSTSRC) > 2000 and len(ROUTERSRC) > 2000)
check("Z4 code_only really strips prose (a carrier named in a docstring is not a RULE TWO breach)",
      "Boost" not in code_only('"""Boost pays this."""\nX = 1\n')
      and "X = 1" in code_only('"""Boost pays this."""\nX = 1\n'))
check("Z5 the module_graph home really resolves to this module",
      HOME.endswith(os.path.join("commcalc", "carrier_claim_value.py")) and os.path.exists(HOME))

print()
if F:
    print("FAILURES: %d passed, %d failed" % (P, F))
    sys.exit(1)
print("ALL PASS: %d passed, 0 failed" % P)
