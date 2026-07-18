"""Proof for agent/commission/sales-capture-fix — P1 (blank-contract_type activation rule engine) +
P0 (canonical store-key regrouping). Pure unit tests over the REAL router functions; NO live DB.

P1 uses the REAL luxelink July line shapes (owner block 1/2/3): blank contract_type on device lines
(dept BrandedHandset / cat KittedBranded|HandsetBranded), rate-plan lines (dept Rtr / cat Other Carr.
payments), SIM lines (cat SimMarketplace), accessory lines (Handset/Accessories), all trans_type 'Sale'.

Run:  cd backend && python3 scratchpad/sales_capture_classify_store_proof.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.modules.commcalc import router  # noqa: E402
from app.modules.commcalc.calculator import classify_contract_type as CCT  # noqa: E402

PASS = FAIL = 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1; print(f"  ok   {name}")
    else:
        FAIL += 1; print(f"  FAIL {name}")


class _Q:
    def __init__(s, c, t): s.c, s.t = c, t
    def select(s, *a, **k): return s
    def eq(s, *a, **k): return s
    def in_(s, *a, **k): return s
    def neq(s, *a, **k): return s
    def limit(s, *a, **k): return s
    def order(s, *a, **k): return s
    def execute(s):
        class R: pass
        r = R(); r.data = list(s.c.t.get(s.t, [])); r.count = len(r.data); return r


class FakeClient:
    def __init__(s, t): s.t = t
    def schema(s, _): return s
    def table(s, t): return _Q(s, t)


D = "2026-07-01"


def L(tid, dept="", cat="", pdesc="Item", ct="", ext=0.0, store="957 Pennsylvania Avenue",
      rep="Jane Rep", tt="Sale", voided=""):
    return {"trans_id": str(tid), "trans_date": D, "store": store, "salesperson": rep,
            "department": dept, "category": cat, "product_desc": pdesc, "contract_type": ct,
            "ext_price": ext, "gp": 0.0, "voided": voided, "trans_type": tt, "user_login": rep}


base_acfg = router._accessory_config(FakeClient({}), "o")  # empty → defaults (dept 'ondigo', empty ct-map/rules)

# The per-org SEED rules (mig 224 seed values, as CONFIG — order: byod before premium):
LUX_RULES = [
    {"bucket": "byod",
     "all_of": [{"field": "category", "contains_any": ["SimMarketplace"]},
                {"field": "department", "contains_any": ["Rtr"]}],
     "none_of": [{"field": "department", "contains_any": ["BrandedHandset"]}]},
    {"bucket": "premium",
     "all_of": [{"field": "department", "contains_any": ["BrandedHandset"]},
                {"field": "department", "contains_any": ["Rtr"]}]},
]


def acfg_with(rules=None, ct_map=None):
    a = dict(base_acfg)
    a["activation_rules"] = rules or []
    if ct_map is not None:
        a["contract_type_map"] = ct_map
    return a


# ══ (1) P1 — blank-ct PREMIUM: real branded-device + rate-plan transaction, all blank ct ═════════
print("(1) P1 blank-ct → PREMIUM: BrandedHandset device line + Rtr plan line, blank contract_type")
# tx 1624 shape: device promo line + plan line + wallet funding, ALL blank ct
tx1624 = [
    L("1624", dept="BrandedHandset", cat="KittedBranded", pdesc="Samsung Galaxy A36 TO - Promo $279.99", ext=0.0),
    L("1624", dept="Rtr", cat="Other Carr. payments", pdesc="Total MAX 5G Plan $55", ext=0.0),
    L("1624", dept="Rtr", cat="Other Carr. payments", pdesc="Wallet Funding", ext=55.0),
]
cells_off = router._sales_cell_agg(tx1624, acfg_with(rules=[]))   # NO rules → today's behavior
cells_on = router._sales_cell_agg(tx1624, acfg_with(rules=LUX_RULES))
key = ("957 Pennsylvania Avenue", "Jane Rep", D)
prem_off = len(cells_off[key]["_prem"])
prem_on = len(cells_on[key]["_prem"])
check("with NO rules the blank-ct activation is INVISIBLE (0) — today's bug", prem_off == 0)
check("with the config rules it counts as 1 premium activation", prem_on == 1)
check("byte-identical no-op when rules empty (Boost path): all buckets 0",
      prem_off == 0 and len(cells_off[key]["_byod"]) == 0 and len(cells_off[key]["_upg"]) == 0)

# ══ (2) P1 — blank-ct BYOD: SIM line + plan, NO branded device (none_of guard) ════════════════════
print("(2) P1 blank-ct → BYOD: SimMarketplace + Rtr plan, no BrandedHandset (none_of exclusion)")
txsim = [
    L("2001", dept="Handset", cat="SimMarketplace", pdesc="SIM Kit", ext=0.0),
    L("2001", dept="Rtr", cat="Other Carr. payments", pdesc="Total STARTER Plan $40", ext=0.0),
]
c2 = router._sales_cell_agg(txsim, acfg_with(rules=LUX_RULES))
k2 = ("957 Pennsylvania Avenue", "Jane Rep", D)
check("SIM + plan (no branded) → BYOD, not premium",
      len(c2[k2]["_byod"]) == 1 and len(c2[k2]["_prem"]) == 0)
# a SIM tx that ALSO has a branded device → the none_of blocks byod, premium rule then fires
txsim2 = txsim + [L("2001", dept="BrandedHandset", cat="HandsetBranded", pdesc="Moto G 5G TO - Promo", ext=0.0)]
c2b = router._sales_cell_agg(txsim2, acfg_with(rules=LUX_RULES))
check("SIM + plan + branded device → PREMIUM (byod none_of excludes it)",
      len(c2b[k2]["_prem"]) == 1 and len(c2b[k2]["_byod"]) == 0)

# ══ (3) P1 — rules NEVER override a ct-labeled transaction; accessory-only stays 0 ═══════════════
print("(3) P1 — rules only supplement blank-ct; a labeled tx and an accessory-only tx are unaffected")
tx_lab = [L("3001", dept="BrandedHandset", cat="KittedBranded", ct="BYOD Activation", pdesc="dev"),
          L("3001", dept="Rtr", cat="Other Carr. payments", pdesc="plan")]
c3 = router._sales_cell_agg(tx_lab, acfg_with(rules=LUX_RULES))
k3 = ("957 Pennsylvania Avenue", "Jane Rep", D)
check("labeled 'BYOD Activation' stays BYOD (rules do NOT re-bucket it to premium)",
      len(c3[k3]["_byod"]) == 1 and len(c3[k3]["_prem"]) == 0)
tx_acc = [L("3002", dept="Handset", cat="Accessories", pdesc="Case", ext=19.99)]
c3b = router._sales_cell_agg(tx_acc, acfg_with(rules=LUX_RULES))
check("accessory-only blank-ct tx → NOT an activation (no device+plan match)",
      len(c3b[k3]["_prem"]) == 0 and len(c3b[k3]["_byod"]) == 0)

# ══ (4) P1 surfacing — _classification_gaps counts the blank + unrecognized, writes the note ══════
print("(4) P1 surfacing — _classification_gaps: blank-ct count, rescued, unrecognized 'Port', note")
rows = tx1624 + txsim + [L("4001", ct="Port", dept="BrandedHandset", pdesc="dev"),   # 'Port' → unrecognized
                         L("4002", ct="Activation", dept="BrandedHandset", pdesc="dev")]  # labeled, fine
g_norules = router._classification_gaps(rows, acfg_with(rules=[]))
g_rules = router._classification_gaps(rows, acfg_with(rules=LUX_RULES))
check("no rules → blank-ct 1624 + 2001 both UNRECOVERED (2)", g_norules["blank_ct_unrecovered"] == 2)
check("with rules → 0 unrecovered (both rescued)", g_rules["blank_ct_unrecovered"] == 0)
check("with rules → rescued_by_rules == 2", g_rules["rescued_by_rules"] == 2)
check("'Port' surfaced as an unrecognized contract type",
      any(u["contract_type"] == "Port" for u in g_rules["unrecognized_contract_types"]))
check("note is present + names Classification settings when there is a gap",
      g_norules["note"] and "Classification" in g_norules["note"])
check("note is None for the house/Boost clean case (no blank, no unknown)",
      router._classification_gaps([L("9", ct="Activation", dept="x")], acfg_with(rules=[]))["note"] is None)

# ══ (5) P0 — canonical store key merges 'Ave'/'Avenue' into ONE cell; distinct stores stay split ══
print("(5) P0 — store_key regroups spelling variants of ONE store into a single row")
sales_ave = [L("5001", ct="Activation", dept="d", store="957 Pennsylvania Ave"),
             L("5002", ct="Activation", dept="d", store="957 Pennsylvania Avenue"),
             L("5003", ct="Activation", dept="d", store="957  PENNSYLVANIA  AVENUE ")]  # case/ws drift
# a store_key that resolves all three spellings of 957 to one canonical code (simulating the resolver)
def _skey(s):
    sl = " ".join(str(s).lower().split())
    return "957-pa" if sl.startswith("957 pennsylvania") else router._cell_store_key(s)
merged = router._sales_cell_agg(sales_ave, acfg_with(), store_key=_skey)
check("all three 957 spellings collapse to ONE (store,rep,day) cell", len(merged) == 1)
cell = list(merged.values())[0]
check("the merged cell counts all 3 activations", len(cell["_prem"]) == 3)
check("display is a real raw spelling (first-seen), not the lowercased key",
      cell["store"] == "957 Pennsylvania Ave")
# two GENUINELY different stores never merge
two = [L("6001", ct="Activation", dept="d", store="957 Pennsylvania Ave"),
       L("6002", ct="Activation", dept="d", store="12 Market St")]
m2 = router._sales_cell_agg(two, acfg_with(), store_key=_skey)
check("two different stores stay TWO cells", len(m2) == 2)
# default store_key=None → BYTE-IDENTICAL raw-string grouping (Boost safe)
m_off = router._sales_cell_agg(sales_ave, acfg_with())
check("store_key=None → raw grouping (3 distinct spellings → 3 cells) = byte-identical default",
      len(m_off) == 3)

# ══ (6) P0 — _canonical_store_key_fn never raises on an empty client (degrades to _cell_store_key) ═
print("(6) P0 — _canonical_store_key_fn degrades safely (no store tables) and folds case/whitespace")
fn = router._canonical_store_key_fn(FakeClient({}), "o")
check("empty resolver → case/whitespace fold only (never raises)",
      fn("957 Pennsylvania Ave") == fn("957  pennsylvania  ave "))
check("distinct strings stay distinct under the fallback",
      fn("957 Pennsylvania Ave") != fn("957 Pennsylvania Avenue"))

# ══ (7) labeled-vocabulary verification (owner block 3) — buckets per the SHIPPED classifier ══════
print("(7) labeled-vocabulary check — block-3 values hit the expected buckets (note the miss)")
expect = {"Activation": "premium", "Port with IDV": "premium", "Activation With IDV": "premium",
          "Upgrade": "upgrade", "Port with IDV AAL": "premium", "BYOD Activation": "byod",
          "Activation AAL": "premium", "Activation With IDV AAL": "premium", "BYOD Port": "byod",
          "BYOD Port AAL": "byod", "BYOD Upgrade": "byod", "BYOD Activation AAL": "byod",
          "Internal Port with IDV": "premium", "Port": None}
for lbl, exp in expect.items():
    check(f"classify {lbl!r} == {exp}", CCT(lbl) == exp)
check("DOCUMENTED MISS: bare 'Port' → None (needs a ct-map entry, not a code change)", CCT("Port") is None)

print(f"\n{PASS}/{PASS + FAIL} passed" + ("" if not FAIL else f"  ({FAIL} FAILED)"))
sys.exit(1 if FAIL else 0)
