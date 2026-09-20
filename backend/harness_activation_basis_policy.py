"""DB-FREE PROOF — the activation BASIS is a stated choice, and the flip cannot silently re-price.

OWNER RULING, 2026-09-20: "tablets pay in ny same as the phones for the month of july august and sept,
but it should be configurable in settings not hard coded".

THE MECHANISM THE RULING IS AIMED AT. Whether `tablet` / `home_internet` / `edge` exist as their own
paid categories, or stay FOLDED inside `activation`, depended on whether an Activation-Details file
happened to have rows for the period — `router._apply_activation_basis` degraded to the sales
aggregation when `ad_rows == 0`, silently. Live (org 854f6d7b) `ad_rows` ran 0 / 0 / 0 / 1,078 / 813
for May–Sep 2026, so THE SAME TABLET ACTIVATION PAID $10 FOLDED IN JULY AND $0 SPLIT IN AUGUST. §A is
that sentence as a test.

THE EXPOSURE IS NOT "THE BASIS CHANGED". It is "a split-only category is priced differently from the
category it folds into". Where every split category carries the fold target's rate the flip moves $0
(§B) — which is the state a tenant should be steered to, and exactly what the owner's ruling produces.

SIBLING HARNESS, DIFFERENT MECHANISM: `harness_activation_cross_bucket.py` covers one SALE counted in
two buckets. This one covers one CATEGORY priced two ways. Neither subsumes the other.

Run:  cd backend && python3 harness_activation_basis_policy.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import app.modules.commcalc.activation_bucketing as AB

FAILS = []
N = [0]


def ok(label, cond, detail=""):
    N[0] += 1
    print(("  PASS  " if cond else "  FAIL  ") + label + ("" if cond else f"   {detail}"))
    if not cond:
        FAILS.append(label)


def eq(label, got, want):
    ok(label, got == want, f"got={got!r} want={want!r}")


# The live NY plan's stored rates, verbatim.
RATES = {"activation": 10, "port": 10, "byod": 10, "tablet": 0, "home_internet": 10, "edge": 10,
         "upgrade": 0, "accessory_pct": 0.1}
# The live August NY counts on the Activation-Details basis.
AUG = {"activation": 48, "port": 83, "byod": 77, "tablet": 55, "home_internet": 13, "edge": 0,
       "upgrade": 12}

# ══ §A  THE REGRESSION — one sale, two prices ════════════════════════════════════════════════════
print("\n§A  the same tablet activation pays $10 folded and $0 split")
one = {"activation": 0, "port": 0, "byod": 0, "tablet": 1, "home_internet": 0, "edge": 0, "upgrade": 0}
e = AB.basis_flip_exposure(one, RATES)
eq("A1 split basis pays the tablet rate", e["split_pay"], 0.0)
eq("A2 folded basis pays the activation rate", e["folded_pay"], 10.0)
eq("A3 the flip is worth −$10 on ONE sale", e["delta"], -10.0)
eq("A4 and it names the category responsible", sorted(e["by_category"]), ["tablet"])
eq("A5 with the two rates that disagree",
   (e["by_category"]["tablet"]["split_rate"], e["by_category"]["tablet"]["fold_rate"]), (0.0, 10.0))
# the live month
ea = AB.basis_flip_exposure(AUG, RATES)
eq("A6 live August NY: 55 tablets = −$550", ea["by_category"]["tablet"]["delta"], -550.0)
eq("A7 total August exposure is −$550", ea["delta"], -550.0)
eq("A8 home_internet contributes $0 — its rate ALREADY equals the activation rate",
   ea["by_category"]["home_internet"]["delta"], 0.0)
ok("A9 edge contributes nothing here because it has no units",
   "edge" not in ea["by_category"])

# ══ §B  THE STATE TO STEER TO ════════════════════════════════════════════════════════════════════
print("\n§B  when every split category carries the fold rate, the flip is harmless")
safe = {**RATES, "tablet": 10}
eq("B1 the owner's ruling makes the exposure exactly $0",
   AB.basis_flip_exposure(AUG, safe)["delta"], 0.0)
eq("B2 ...and split == folded to the cent",
   AB.basis_flip_exposure(AUG, safe)["split_pay"],
   AB.basis_flip_exposure(AUG, safe)["folded_pay"])
eq("B3 the exposure returns the moment a sibling rate is moved away",
   AB.basis_flip_exposure(AUG, {**safe, "home_internet": 0})["delta"], -130.0)
eq("B4 ...and names the sibling, not the tablet",
   sorted(k for k, v in AB.basis_flip_exposure(AUG, {**safe, "home_internet": 0})["by_category"].items()
          if v["delta"]), ["home_internet"])

# ══ §C  THE POLICY IS INERT UNTIL SOMEBODY STATES IT ═════════════════════════════════════════════
print("\n§C  resolve_basis_policy — every existing plan is byte-identical")
eq("C1 no key -> auto", AB.resolve_basis_policy(RATES), "auto")
eq("C2 None -> auto", AB.resolve_basis_policy(None), "auto")
eq("C3 garbage -> auto", AB.resolve_basis_policy({"activation_basis": "nonsense"}), "auto")
eq("C4 not a dict -> auto", AB.resolve_basis_policy("folded"), "auto")
eq("C5 case/space tolerant", AB.resolve_basis_policy({"activation_basis": "  FOLDED "}), "folded")
eq("C6 the three stated policies", sorted(AB.BASIS_POLICIES), ["auto", "folded", "require_split"])

# ══ §D  THE DECISION IS NAMED, INCLUDING THE DEGRADED ONE ════════════════════════════════════════
print("\n§D  basis_decision — a silent fold becomes a stated, reasoned one")
d = AB.basis_decision("auto", 1078, "activation_details")
eq("D1 rows present -> the stated basis, not degraded", (d["basis"], d["degraded"]),
   ("activation_details", False))
d0 = AB.basis_decision("auto", 0, "activation_details")
eq("D2 NO rows -> folded, and DEGRADED (this was the silent case)",
   (d0["basis"], d0["degraded"]), ("sales_agg", True))
ok("D3 the reason names every category that folds",
   all(c in d0["reason"] for c in AB.SPLIT_ONLY_CATEGORIES), d0["reason"])
ok("D4 ...and names the category they fold INTO", AB.FOLD_TARGET in d0["reason"])
ok("D5 ...and tells the operator both ways out",
   "upload" in d0["reason"].lower() and "folded" in d0["reason"].lower())
df = AB.basis_decision("folded", 0, "activation_details")
eq("D6 a DELIBERATE fold is never reported as degraded", df["degraded"], False)
eq("D7 the stated source is carried through for the operator", d0["stated_source"], "activation_details")
eq("D8 ad_rows survives as evidence", d["ad_rows"], 1078)

# ══ §E  FOLDING LOSES NO UNITS ═══════════════════════════════════════════════════════════════════
print("\n§E  fold_counts")
f = AB.fold_counts(AUG)
eq("E1 the three split counts are emptied", (f["tablet"], f["home_internet"], f["edge"]), (0, 0, 0))
eq("E2 and land in the fold target", f["activation"], 48 + 55 + 13 + 0)
eq("E3 no other category is touched",
   {k: f[k] for k in ("port", "byod", "upgrade")}, {"port": 83, "byod": 77, "upgrade": 12})
eq("E4 total units are conserved", sum(f[k] for k in AUG), sum(AUG.values()))
_in = dict(AUG)
AB.fold_counts(_in)
eq("E5 the input is not mutated", _in, AUG)
eq("E6 None survives", AB.fold_counts(None), {AB.FOLD_TARGET: 0, "tablet": 0, "home_internet": 0,
                                              "edge": 0})

# ══ §F  THE TWO ROUTES TO THE RULING MUST AGREE ══════════════════════════════════════════════════
print("\n§F  'set tablet to the activation rate' and 'fold the split categories' are equivalent")
rate_route = AB.basis_flip_exposure(AUG, safe)["split_pay"]
fold_route = AB.basis_flip_exposure(AUG, RATES)["folded_pay"]
eq("F1 they produce the SAME pay", rate_route, fold_route)
ok("F2 and both exceed the as-is split figure by the tablet units x the activation rate",
   round(fold_route - AB.basis_flip_exposure(AUG, RATES)["split_pay"], 2) == 550.0)

# ══ §G  PURITY + RULE TWO ════════════════════════════════════════════════════════════════════════
print("\n§G  pure, and free of tenant vocabulary")
_src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "app", "modules", "commcalc",
                         "activation_bucketing.py")).read()
_seg = _src[_src.index("BASIS_POLICIES = "):]
_body = "\n".join(l for l in _seg.splitlines() if not l.strip().startswith("#")).lower()
eq("G1 no tenant / carrier / market name",
   [w for w in ("luxelink", "boost", "cricket", "total wireless", "chicago", '"ny"', "'ny'")
    if w in _body], [])
ok("G2 no I/O in the module",
   not any(t in _src for t in ("import requests", "supabase", "client.schema", "psycopg")))
eq("G3 the split-only set is DATA, not a branch per category",
   AB.SPLIT_ONLY_CATEGORIES, ("tablet", "home_internet", "edge"))

print("\n" + "=" * 78)
print(f"{N[0]} checks, {len(FAILS)} failed" + ("" if not FAILS else f"  -> {FAILS}"))
print("=" * 78)
sys.exit(1 if FAILS else 0)
