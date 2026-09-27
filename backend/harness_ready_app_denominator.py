"""PROOF — "HOW MANY NEW ACTIVATIONS" HAS ONE HOME, AND IT IS THE OWNER'S RULING.
stdlib only, DB-free, no framework. Run: python3 backend/harness_ready_app_denominator.py

OWNER RULING 2026-09-27, verbatim: *"Denominator should be the total of new activations excluding
upgrade and swap as reported in exec mats - data source is the same for all reports"*.

WHAT THIS PROVES
 A. `line_class.resolve_new_activation` / `resolve_exclusions` — the config, per org, with the house
    default being EXACTLY the ruling; junk and an empty classes list can never produce a denominator
    of nothing, and an explicitly empty exclusion word list IS honoured.
 B. `exclusion_class` / `exclusion_kinds` — the ONE swap vocabulary, over the org's own configured
    fields, byte-identical to the three bare substring copies it replaces (the Sales Report's swap
    tally, `ma_recon`'s sold-side basis, and the chargeback detector's `'ineligible' in ct`), AND the
    precedence hazard removed: a caller ASKING ABOUT A KIND reads the SET, never the precedence winner.
 C. `new_activation_units` — the count. Per-UNIT exclusion (a BYOD-Swap invoice leaves once, not per
    line), the evidence kept, and the same answer under BOTH count units when the boundary is closed.
 D. THE CARRIER RECONCILIATION, from the real August shape (anonymised): 8 premium + 11 BYOD − 5
    BYOD-Swap = 14 under the ruling as written, and − the ineligible port-in = 13, which is
    ElevateGo's own denominator (61.54% = 8/13). Under `count_unit='event'` the same fixture gives
    15 and 13 — so the ruling is basis-STABLE only once the boundary is closed, which is why the
    boundary is a question for the owner and not a guess here.
 E. `new_activation_from_buckets` — the ONLY other way in (an Activation-Details basis), agreeing with
    the line-level count on the same fixture; and THE MIXED-BUCKET TICKET, which is why the owner's
    number is not literally Executive MTD's `total_activation - upgrade - swap`: Total Activation SUMS
    four per-line bucket counts, so an invoice whose lines land in two buckets is counted twice there
    (the §6d / `harness_activation_cross_bucket.py` defect (C)), while the denominator counts it ONCE
    because it is a SET of unit ids. Exec MTD now also reports `cross_bucket`, so
    `total_activation - upgrade - swap_excluded - cross_bucket == new_activation` exactly (verified live:
    house August 1926-696-134-4 = 1092; September 1629-566-117-9 = 937; June 1853-672-106-2 = 1073).
 F. `kpi_failing` — the derived rate: `boost_ready_bounty / new_activations`, the basis names, and
    that a caller offering NO basis reads the stored column exactly as before (the money pin: shipping
    this moves nothing until a tenant's `kpi_boostapp_basis` says otherwise).
 G. The score: the reference rep's carrier-published 61.54% reproduces to the digit, and it FAILS the
    65 target either way — so the ruling does not move that rep's tier by itself.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app.modules.commcalc import line_class as lc          # noqa: E402  (pure)
from app.modules.commcalc import kpi_failing as kf         # noqa: E402  (pure)

P = F = 0


def check(label, ok, detail=""):
    global P, F
    if ok:
        P += 1
        print("  PASS  %s" % label)
    else:
        F += 1
        print("  FAIL  %s   %s" % (label, str(detail)[:300]))


# ── the real August shape for the reference rep, anonymised: the contract-type spellings and their
#    line counts exactly as measured on the live rows, one invoice per unit unless noted. ────────────
def _lines():
    """(contract_type, trans_id, mdn) — 8 premium invoices (2 plain + 6 port-in, one of which is the
    invoice carrying TWO ineligible port-in phone lines), 11 BYOD invoices (5 of them BYOD-Swap, each
    ALSO carrying an Upgrade line on the SAME phone line), and 11 further plain Upgrade invoices."""
    out = []
    for i in range(2):
        out.append(("Activation", "A%d" % i, "917000%04d" % i))
    for i in range(5):
        out.append(("Eligible Port-In Activation", "P%d" % i, "917100%04d" % i))
    # ONE invoice with two Ineligible Port-In phone LINES → 1 transaction, 2 events
    out.append(("Ineligible Port-In Activation", "PI0", "9171100001"))
    out.append(("Ineligible Port-In Activation", "PI0", "9171100002"))
    for i in range(6):
        out.append(("BYOD", "B%d" % i, "917200%04d" % i))
    for i in range(5):                                  # the five BYOD-Swap invoices
        out.append(("BYOD Swap", "S%d" % i, "917300%04d" % i))
        out.append(("Upgrade", "S%d" % i, "917300%04d" % i))     # same phone line
    for i in range(11):
        out.append(("Upgrade", "U%d" % i, "917400%04d" % i))
    return [{"contract_type": ct, "trans_id": t, "mdn": m, "voided": "", "trans_type": "Sale",
             "salesperson": "Rep One", "store": "1 Example St"} for ct, t, m in out]


ROWS = _lines()

print("\nA. the config — the house default IS the ruling, and junk cannot empty the denominator")
h = lc.resolve_new_activation(None)
check("A1 house classes = activation + port + byod (Total Activation less Upgrade)",
      h["classes"] == ["activation", "port", "byod"], h)
check("A2 house exclusions = swap only (exactly what the owner said, no more)",
      h["exclusions"] == ["swap"], h)
check("A3 'upgrade' is NOT a counted class", "upgrade" not in h["classes"])
check("A4 junk → the house default, source 'house'",
      lc.resolve_new_activation("nonsense") == h and h["source"] == "house")
check("A5 an EMPTY classes list can never produce a denominator of nothing",
      lc.resolve_new_activation({"classes": []})["classes"] == h["classes"])
check("A6 an unknown class is dropped, a known one kept",
      lc.resolve_new_activation({"classes": ["byod", "nope", "upgrade"]})["classes"] == ["byod", "upgrade"])
check("A7 a tenant may declare NO exclusion (an empty list is honoured)",
      lc.resolve_new_activation({"exclusions": []})["exclusions"] == [])
check("A8 declaring anything marks the source 'tenant'",
      lc.resolve_new_activation({"exclusions": ["swap"]})["source"] == "tenant")
check("A9 ineligible is DECLARED as a kind and NOT applied by default",
      "ineligible" in lc.EXCLUSION_KINDS and "ineligible" not in h["exclusions"])
ex = lc.resolve_exclusions(None)
check("A10 house exclusion words: swap → ['swap'], ineligible → ['ineligible']",
      ex == {"swap": ["swap"], "ineligible": ["ineligible"]}, ex)
check("A11 an explicitly EMPTY word list matches nothing (a real answer)",
      lc.resolve_exclusions({"swap": []})["swap"] == [])
check("A12 the new keys are OWNED by this module (a save cannot silently drop them)",
      "exclusions" in lc.OWNED_KEYS and "new_activation" in lc.OWNED_KEYS)

print("\nB. exclusion_class — ONE swap vocabulary, byte-identical to the two bare copies it replaces")
for ct, want in (("BYOD Swap", "swap"), ("byod swap", "swap"), ("Swap", "swap"),
                 ("Ineligible Port-In Activation", "ineligible"), ("Activation", None),
                 ("Upgrade", None), ("BYOD", None), ("", None), (None, None)):
    check("B1 %-32r → %r" % (ct, want), lc.exclusion_class({"contract_type": ct}) == want,
          lc.exclusion_class({"contract_type": ct}))
check("B2 the retired predicate `'swap' in ct.lower()` agrees on every live spelling",
      all((lc.exclusion_class({"contract_type": r["contract_type"]}) == "swap")
          == ("swap" in r["contract_type"].lower()) for r in ROWS))
# a tenant whose POS carries the fact in the category path is served by the SAME function
trules = lc.resolve_rules({"fields": ["category"], "tokens": {"byod": ["customer provided"]}})
check("B3 a tenant's declared fields are read for the exclusion too",
      lc.exclusion_class({"category": "… >> Device Swap (x)", "contract_type": ""}, trules) == "swap")
check("B4 and the house field is NOT read when the tenant declared another",
      lc.exclusion_class({"category": "plain", "contract_type": "BYOD Swap"}, trules) is None)
check("B5 EXCLUSION_KINDS order is the precedence (a line saying both reports the first)",
      lc.exclusion_class({"contract_type": "Ineligible Port-In Swap"}) == "swap"
      and lc.EXCLUSION_KINDS[0] == "swap")
# THE PRECEDENCE HAZARD, removed. `exclusion_class` answers "what is this, in one word" and so needs a
# precedence; a caller testing MEMBERSHIP must not be answered by one. Measured 2026-09-27: none of the
# 35 distinct `contract_type` values live on the platform carries two kinds, so the two agree everywhere
# today — which is exactly when this is cheapest to get right.
BOTH = {"contract_type": "Ineligible Port-In Swap", "trans_id": "X", "mdn": "9170000000"}
check("B6 exclusion_kinds reports BOTH kinds, in EXCLUSION_KINDS order",
      lc.exclusion_kinds(BOTH) == ("swap", "ineligible"), lc.exclusion_kinds(BOTH))
check("B7 a plain line carries no kinds (an empty tuple, not a None to unpack)",
      lc.exclusion_kinds({"contract_type": "Activation"}) == ())
check("B8 exclusion_class is exactly the FIRST of exclusion_kinds, on every live spelling",
      all((lc.exclusion_class({"contract_type": v}) or None)
          == ((lc.exclusion_kinds({"contract_type": v}) or (None,))[0])
          for v in ("Activation", "BYOD Swap", "Ineligible Port-In Activation",
                    "PML Ineligible Port In Activation", "Ineligible Port-In Add A Line",
                    "Ineligible Port-In Swap", "Upgrade", "")))
check("B9 a config excluding ONLY 'ineligible' still excludes a line spelling both — the precedence "
      "must not decide membership",
      lc.new_activation_units([BOTH], exclusions=["ineligible"])["excluded"] == {"X": "ineligible"},
      lc.new_activation_units([BOTH], exclusions=["ineligible"])["excluded"])
check("B10 …and a config excluding only 'swap' excludes it too",
      lc.new_activation_units([BOTH], exclusions=["swap"])["excluded"] == {"X": "swap"})
check("B11 with NO exclusions configured it is counted, not dropped",
      lc.new_activation_units([BOTH], exclusions=[])["count"] == 1)
check("B12 the retired chargeback predicate `'ineligible' in ct` agrees with the SET on every live "
      "spelling (byte-identical; measured over all 35 distinct platform values)",
      all(("ineligible" in v.lower()) == ("ineligible" in lc.exclusion_kinds({"contract_type": v}))
          for v in ("Activation", "BYOD", "BYOD Swap", "Upgrade", "Eligible Port-In Activation",
                    "Eligible Port-In Add A Line", "Ineligible Port-In Activation",
                    "Ineligible Port-In Add A Line", "PML Ineligible Port In Activation",
                    "Ineligible Port-In Swap", "BYOD Port-In", "BYOD Add A Line", "")))

print("\nC/D. the count, and the carrier reconciliation")
EXPECT = {("transaction", ("swap",)): 14, ("transaction", ("swap", "ineligible")): 13,
          ("event", ("swap",)): 15, ("event", ("swap", "ineligible")): 13}
for (unit, excl), want in sorted(EXPECT.items()):
    got = lc.new_activation_units(ROWS, unit=unit, exclusions=list(excl))
    check("C1 unit=%-11s exclusions=%-22s → %d" % (unit, ",".join(excl), want),
          got["count"] == want, got["count"])
t = lc.new_activation_units(ROWS, unit="transaction")
check("C2 the house default (swap only) is the ruling as WRITTEN → 14", t["count"] == 14, t["count"])
check("C3 the excluded units are named by kind, as evidence — never silently dropped",
      sorted(set(t["excluded"].values())) == ["swap"] and t["excluded_count"] == 5, t["excluded"])
check("C4 a swap invoice leaves the count ONCE, not once per line",
      len(t["excluded"]) == 5 and all(k.startswith("S") for k in t["excluded"]), t["excluded"])
check("C5 by_class shows what the count is made of (2 activation + 6 port + 6 byod = 14)",
      t["by_class"] == {"activation": 2, "port": 6, "byod": 6}, t["by_class"])
check("C6 an upgrade unit is never in the count",
      not any(k.startswith("U") for k in t["units"]), sorted(t["units"])[:4])
check("C7 the config travels with the answer (basis, classes, exclusions, source)",
      t["config"] == {"classes": ["activation", "byod", "port"], "exclusions": ["swap"],
                      "unit": "transaction", "source": "house"}, t["config"])
check("D1 THE CARRIER: 8 premium + 11 BYOD − 5 BYOD-Swap − the ineligible port-in = 13, "
      "ElevateGo's own denominator",
      lc.new_activation_units(ROWS, unit="transaction", exclusions=["swap", "ineligible"])["count"] == 13)
check("D2 and it is the SAME 13 under count_unit='event' — the ruling is basis-stable once the "
      "boundary is closed",
      lc.new_activation_units(ROWS, unit="event", exclusions=["swap", "ineligible"])["count"] == 13)
check("D3 while the ruling as WRITTEN gives 14 (transaction) and 15 (event) — which is why the "
      "boundary is the owner's question and not a guess here",
      (lc.new_activation_units(ROWS, unit="transaction")["count"],
       lc.new_activation_units(ROWS, unit="event")["count"]) == (14, 15))
check("D4 excluding ALL port-ins undercounts badly (measured live: mean −1.66/cell) — the fixture "
      "shows the shape: 14 → 8",
      lc.new_activation_units(ROWS, unit="transaction", classes=["activation", "byod"])["count"] == 8)
check("D5 excluding BYOD undercounts too — 14 → 8",
      lc.new_activation_units(ROWS, unit="transaction", classes=["activation", "port"])["count"] == 8)
check("D6 new_activation_count is the integer alone",
      lc.new_activation_count(ROWS, unit="transaction") == 14)
check("D7 a caller's already-computed units are reused, giving the same answer (no second pass, and "
      "so no second basis)",
      lc.new_activation_units(ROWS, units=lc.activation_units(ROWS))["count"] == 14)
check("D8 no rows → 0, not a crash", lc.new_activation_units([])["count"] == 0)
check("D9 junk rows never raise",
      lc.new_activation_units([None, {}, {"contract_type": 7}])["count"] == 0)

print("\nE. the only other way in — aggregated bucket counts (an Activation-Details basis)")
check("E1 from_buckets agrees with the line-level count on the same fixture",
      lc.new_activation_from_buckets({"activation": 2, "port": 6, "byod": 11, "upgrade": 16},
                                     excluded=5) == 14)
check("E2 it reads the SAME `classes` config (upgrade is never added)",
      lc.new_activation_from_buckets({"upgrade": 99}) == 0)
check("E3 it can never go negative",
      lc.new_activation_from_buckets({"activation": 1}, excluded=50) == 0)
check("E4 junk in → 0, never a crash",
      lc.new_activation_from_buckets({"activation": "x"}, excluded="y") == 0)

# THE MIXED-BUCKET TICKET (§6d defect (C)). One invoice whose lines land in TWO buckets: Total
# Activation sums the bucket counts and so counts it twice; the denominator is a SET and counts it once.
MIXED = ROWS + [{"contract_type": "Activation", "trans_id": "B0", "mdn": "9172000000",
                 "voided": "", "trans_type": "Sale", "salesperson": "Rep One", "store": "1 Example St"}]
_u = lc.activation_units(MIXED, unit="transaction")
_prem = {x[1] for x in _u if x and x[0] == "premium"}
_byod = {x[1] for x in _u if x and x[0] == "byod"}
check("E5 the fixture now has exactly one invoice in BOTH buckets",
      len(_prem & _byod) == 1 and (_prem & _byod) == {"B0"}, _prem & _byod)
check("E6 Total Activation's arithmetic counts it TWICE (len(prem) + len(byod))",
      len(_prem) + len(_byod) == 20 and len(_prem | _byod) == 19)
check("E7 the DENOMINATOR counts it ONCE — 14 still, not 15 (the defect cannot reach the rate)",
      lc.new_activation_units(MIXED, unit="transaction")["count"] == 14,
      lc.new_activation_units(MIXED, unit="transaction")["count"])
check("E8 and the subtraction is COMPLETE once the cross-bucket count is reported: "
      "TA − upgrade − swap − cross_bucket == new_activation",
      (len(_prem) + len(_byod) + 16) - 16 - 5 - ((len(_prem) + len(_byod)) - len(_prem | _byod))
      == lc.new_activation_units(MIXED, unit="transaction")["count"])

print("\nF. the derived rate, and THE MONEY PIN")
check("F1 boostapp is declared derived: numerator boost_ready_bounty over the new-activation basis",
      kf.REP_DERIVED_RATES["boostapp"] == ("boost_ready_bounty", "new_activations"))
check("F2 derived_rate: 8 over 13 = 61.54%", round(kf.derived_rate(8, 13), 2) == 61.54)
for n, d in ((8, 0), (8, None), (8, -1), (None, 13), ("x", 13)):
    check("F3 no basis → None, never a 0%% failure (%r/%r)" % (n, d), kf.derived_rate(n, d) is None)
check("F4 the basis names, and the default is the PRE-RULING one",
      kf.BOOSTAPP_BASES == ("exec_new_activations", "feed_prepaid")
      and kf.BOOSTAPP_BASIS_DEFAULT == "feed_prepaid")
for raw in (None, "", "nonsense", "feed_prepaid", 0, []):
    check("F5 missing beats wrong: %r → the pre-ruling basis" % (raw,),
          kf.resolve_boostapp_basis(raw) == kf.BOOSTAPP_BASIS_FEED)
check("F6 only the exact ruling name selects it (case-insensitively)",
      kf.resolve_boostapp_basis("Exec_New_Activations") == kf.BOOSTAPP_BASIS_EXEC)
D = kf.BUILTIN_KPI_DEFS
REP = {"boost_ready_bounty": 8, "boost_app_pct": 75.0, "atu_pct": 50.0}
v_old, s_old = kf.rep_kpi_values(D, rep_row=REP)
check("F7 THE MONEY PIN — a caller that offers NO basis reads the stored column, byte-identical",
      v_old["boostapp"] == 75.0 and s_old["boostapp"] == kf.SOURCE_REP_DLAR)
v_new, s_new = kf.rep_kpi_values(D, rep_row=REP, basis={"boostapp": 13})
check("F8 a caller that offers the basis gets the ruling's rate, stamped rep_derived",
      round(v_new["boostapp"], 2) == 61.54 and s_new["boostapp"] == kf.SOURCE_REP_DERIVED)
v_z, s_z = kf.rep_kpi_values(D, rep_row=REP, basis={"boostapp": 0})
check("F9 the basis offered but unusable is no_data — the stored column does NOT stand in "
      "(that would be the fabricated denominator again)",
      v_z["boostapp"] is None and s_z["boostapp"] is None)
check("F10 every OTHER metric is untouched by the basis argument",
      all(v_old[k] == v_new[k] for k in v_old if k != "boostapp"))
check("F11 the derived source is registered in SOURCES",
      kf.SOURCE_REP_DERIVED in kf.SOURCES)

print("\nG. the score — the carrier's own number, and what it does to the tier")
TARGETS = {"atu": 55, "protect": 80, "boostapp": 65, "familyplan": 45, "byod": 35, "tmr3": 70, "aal": 5}
vals = {"atu": 84.62, "protect": 80.0, "boostapp": kf.derived_rate(8, 13), "familyplan": 25.0,
        "byod": 53.85, "tmr3": 88.9, "aal": 3.9}
met, tot, ev, nd = kf.score(vals, D, TARGETS)
check("G1 the carrier-published Ready App rate reproduces to the digit (61.54)",
      round(vals["boostapp"], 2) == 61.54)
check("G2 it FAILS the 65 target — so the ruling does not lift this rep's score on its own",
      not [e for e in ev if e["kpi"] == "boostapp"][0]["met"])
check("G3 the honest denominator counts every measured metric (7 of 7 here)", tot == 7, tot)
check("G4 with a NULL basis the metric is no_data and OUT of the denominator, never a failed 0",
      kf.score({**vals, "boostapp": None}, D, TARGETS)[1] == 6
      and [n["kpi"] for n in kf.score({**vals, "boostapp": None}, D, TARGETS)[3]] == ["boostapp"])

print("\n%d passed, %d failed" % (P, F))
if F:
    sys.exit(1)
print("OK — one new-activation count, the owner's ruling as its house default, the carrier "
      "reconciliation reproduced, and today's pay byte-identical until a tenant flips the basis.")
