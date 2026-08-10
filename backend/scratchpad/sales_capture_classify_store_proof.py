"""Proof for agent/commission/sales-capture-fix — P1 (blank-contract_type activation rule engine) +
P0 (canonical store-key regrouping). Pure unit tests over the REAL router functions; NO live DB.

P1 uses the REAL luxelink July line shapes (owner block 1/2/3): blank contract_type on device lines
(dept BrandedHandset / cat KittedBranded|HandsetBranded), rate-plan lines (dept Rtr / cat Other Carr.
payments), SIM lines (cat SimMarketplace), accessory lines (Handset/Accessories), all trans_type 'Sale'.

Gate-1 REWORK (2026-07-18) proven here with a REAL (range-capable, store-mapping-backed) fake client:
  M1 token-overlap guard on leading-number merges (§5) — partially-mapped same-number different-street stays
     SPLIT; Ave/Avenue same-street merges; fully-mapped unchanged; unmapped folds only.
  M2 drill-down + Exec-MTD filter canonicalized (§8) — a merged store's detail + filter return the FULL
     multi-spelling set, not the label's spelling only.
  m1 STRING contains_any can't per-char match (§9); m2 voided lines aren't rule evidence (§10).

Run:  cd backend && python3 scratchpad/sales_capture_classify_store_proof.py
"""
import sys, os, asyncio
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import date as _date  # noqa: E402
from app.modules.commcalc import router  # noqa: E402
from app.modules.commcalc.calculator import classify_contract_type as CCT  # noqa: E402

PASS = FAIL = 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1; print(f"  ok   {name}")
    else:
        FAIL += 1; print(f"  FAIL {name}")


# ── range-capable, schema-agnostic in-memory fake client (mirrors exec_targets_one_source_proof) ──
class FakeResult:
    def __init__(self, data=None, count=None):
        self.data = data or []; self.count = count


class FakeQuery:
    def __init__(self, store, table):
        self.store = store; self.t = table; self.f = []; self.cnt = False; self.rng = None

    def select(self, *a, **k):
        if k.get('count') == 'exact':
            self.cnt = True
        return self

    def eq(self, c, v): self.f.append(('eq', c, v)); return self
    def neq(self, c, v): self.f.append(('neq', c, v)); return self
    def in_(self, c, v): self.f.append(('in', c, list(v))); return self
    def gte(self, c, v): self.f.append(('gte', c, v)); return self
    def lt(self, c, v): self.f.append(('lt', c, v)); return self
    def limit(self, n): return self
    def range(self, a, b): self.rng = (a, b); return self
    def order(self, *a, **k): return self

    def _m(self, r):
        for k, c, v in self.f:
            rv = r.get(c)
            if k == 'eq' and rv != v: return False
            if k == 'neq' and rv == v: return False
            if k == 'in' and rv not in v: return False
            if k == 'gte' and not (str(rv) >= str(v)): return False
            if k == 'lt' and not (str(rv) < str(v)): return False
        return True

    def execute(self):
        rows = self.store.setdefault(self.t, [])
        m = [r for r in rows if self._m(r)]
        if self.rng:
            a, b = self.rng; m = m[a:b + 1]
        if self.cnt:
            return FakeResult(data=m, count=len(m))
        return FakeResult(data=[dict(r) for r in m])


class FakeSchema:
    def __init__(self, store): self.store = store
    def table(self, t): return FakeQuery(self.store, t)
    def rpc(self, *a, **k): raise Exception('no rpc')


class FakeClient:
    def __init__(self, store): self.store = store
    def schema(self, s): return FakeSchema(self.store)


ORG = 'o'
_T = _date.today()
OPEN = f"{_T.year}-{_T.month:02d}"
D = f"{OPEN}-01"
base_acfg = router._accessory_config(FakeClient({}), ORG)

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


def L(tid, dept="", cat="", pdesc="Item", ct="", ext=0.0, store="957 Pennsylvania Avenue",
      rep="Jane Rep", tt="Sale", voided="", period=OPEN, day=D):
    return {"trans_id": str(tid), "trans_date": day, "store": store, "salesperson": rep,
            "department": dept, "category": cat, "product_desc": pdesc, "contract_type": ct,
            "ext_price": ext, "gp": 0.0, "voided": voided, "trans_type": tt, "user_login": rep,
            "org_id": ORG, "period": period}


# ══ (1) P1 blank-ct → PREMIUM ═════════════════════════════════════════════════════════════════════
print("(1) P1 blank-ct → PREMIUM: BrandedHandset device + Rtr plan, blank contract_type")
tx1624 = [L("1624", dept="BrandedHandset", cat="KittedBranded", pdesc="Samsung Galaxy A36 TO - Promo"),
          L("1624", dept="Rtr", cat="Other Carr. payments", pdesc="Total MAX 5G Plan $55"),
          L("1624", dept="Rtr", cat="Other Carr. payments", pdesc="Wallet Funding", ext=55.0)]
key = ("957 Pennsylvania Avenue", "Jane Rep", D)
off = router._sales_cell_agg(tx1624, acfg_with(rules=[]))
on = router._sales_cell_agg(tx1624, acfg_with(rules=LUX_RULES))
check("NO rules → blank-ct activation invisible (0)", len(off[key]["_prem"]) == 0)
check("with rules → 1 premium activation", len(on[key]["_prem"]) == 1)

# ══ (2) P1 blank-ct → BYOD (SIM + plan, none_of branded) ═════════════════════════════════════════
print("(2) P1 blank-ct → BYOD: SimMarketplace + Rtr plan, no branded (none_of)")
txsim = [L("2001", dept="Handset", cat="SimMarketplace", pdesc="SIM Kit"),
         L("2001", dept="Rtr", cat="Other Carr. payments", pdesc="Total STARTER Plan $40")]
c2 = router._sales_cell_agg(txsim, acfg_with(rules=LUX_RULES))
check("SIM + plan (no branded) → BYOD not premium",
      len(c2[key]["_byod"]) == 1 and len(c2[key]["_prem"]) == 0)
c2b = router._sales_cell_agg(txsim + [L("2001", dept="BrandedHandset", cat="HandsetBranded", pdesc="Moto")],
                             acfg_with(rules=LUX_RULES))
check("SIM + plan + branded → PREMIUM (byod none_of excludes)",
      len(c2b[key]["_prem"]) == 1 and len(c2b[key]["_byod"]) == 0)

# ══ (3) rules supplement-only; accessory-only stays 0 ════════════════════════════════════════════
print("(3) P1 — rules never override a labeled tx; accessory-only stays 0")
c3 = router._sales_cell_agg([L("3001", dept="BrandedHandset", ct="BYOD Activation"),
                             L("3001", dept="Rtr", pdesc="plan")], acfg_with(rules=LUX_RULES))
check("labeled 'BYOD Activation' stays byod (not re-bucketed)",
      len(c3[key]["_byod"]) == 1 and len(c3[key]["_prem"]) == 0)
c3b = router._sales_cell_agg([L("3002", dept="Handset", cat="Accessories", pdesc="Case", ext=19.99)],
                             acfg_with(rules=LUX_RULES))
check("accessory-only blank-ct → not an activation", len(c3b[key]["_prem"]) == 0)

# ══ (4) surfacing — _classification_gaps ═════════════════════════════════════════════════════════
print("(4) P1 surfacing — _classification_gaps counts + note")
rows = tx1624 + txsim + [L("4001", ct="Port", dept="BrandedHandset"), L("4002", ct="Activation", dept="BrandedHandset")]
g0 = router._classification_gaps(rows, acfg_with(rules=[]))
g1 = router._classification_gaps(rows, acfg_with(rules=LUX_RULES))
check("no rules → 2 blank-ct unrecovered", g0["blank_ct_unrecovered"] == 2)
check("with rules → 0 unrecovered, rescued 2", g1["blank_ct_unrecovered"] == 0 and g1["rescued_by_rules"] == 2)
check("'Port' surfaced unrecognized", any(u["contract_type"] == "Port" for u in g1["unrecognized_contract_types"]))
check("note present + names Classification", g0["note"] and "Classification" in g0["note"])
check("note None for clean house case",
      router._classification_gaps([L("9", ct="Activation", dept="x")], acfg_with(rules=[]))["note"] is None)

# ══ (5) M1 — token-overlap guard on leading-number merges (REAL resolver, store-mapping backed) ══
print("(5) M1 — leading-number merge guard (real _canonical_store_key_fn over store_mapping)")
# partially mapped: only "100 Main St" (B-100M) exists
part = FakeClient({"store_mapping": [{"org_id": ORG, "store_code": "B-100M",
                                      "store_address": "100 Main St", "market": "M"}]})
kp = router._canonical_store_key_fn(part, ORG)
check("M1: unmapped '100 Oak Ave' does NOT merge into '100 Main St' (different street)",
      kp("100 Oak Ave") != kp("100 Main St"))
check("M1: '100 Main St' resolves to its mapped code key", kp("100 Main St") == router._cell_store_key("B-100M"))
check("M1: '100  MAIN  ST' (double space) still hits the mapping (n1 whitespace-insensitive)",
      kp("100  MAIN  ST") == kp("100 Main St"))
# same street, Ave/Avenue drift, only 'Avenue' mapped → the 'Ave' variant MERGES via number + shared token
avem = FakeClient({"store_mapping": [{"org_id": ORG, "store_code": "B-957",
                                      "store_address": "957 Pennsylvania Avenue", "market": "NY"}]})
ka = router._canonical_store_key_fn(avem, ORG)
check("M1: '957 Pennsylvania Ave' MERGES with mapped '957 Pennsylvania Avenue' (shared 'pennsylvania')",
      ka("957 Pennsylvania Ave") == ka("957 Pennsylvania Avenue") == router._cell_store_key("B-957"))
# a same-number store on a DIFFERENT street sharing only the suffix must NOT merge
check("M1: '957 Madison Ave' does NOT merge into '957 Pennsylvania Avenue' (only 'ave' shared → dropped)",
      ka("957 Madison Ave") != ka("957 Pennsylvania Avenue"))
# fully mapped → both explicit, distinct
full = FakeClient({"store_mapping": [
    {"org_id": ORG, "store_code": "B-100M", "store_address": "100 Main St", "market": "M"},
    {"org_id": ORG, "store_code": "B-100O", "store_address": "100 Oak Ave", "market": "M"}]})
kf = router._canonical_store_key_fn(full, ORG)
check("M1: fully-mapped same-number stores stay distinct",
      kf("100 Main St") != kf("100 Oak Ave") and kf("100 Oak Ave") == router._cell_store_key("B-100O"))
# explicit alias with NO token overlap is trusted (owner nickname)
alia = FakeClient({"store_mapping": [{"org_id": ORG, "store_code": "B-100M", "store_address": "100 Main St", "market": "M"}],
                   "store_aliases": [{"org_id": ORG, "alias": "Downtown Flagship", "store_code": "B-100M"}]})
kal = router._canonical_store_key_fn(alia, ORG)
check("M1: explicit alias 'Downtown Flagship' → mapped code (trusted, no token guard)",
      kal("Downtown Flagship") == router._cell_store_key("B-100M"))
# unmapped org → fold only, Ave/Avenue stay separate (owner must alias)
ku = router._canonical_store_key_fn(FakeClient({}), ORG)
check("M1: unmapped org → case/ws fold only (Ave/Avenue stay split until aliased)",
      ku("957 Pennsylvania Ave") == ku("957  PENNSYLVANIA  AVE ") and
      ku("957 Pennsylvania Ave") != ku("957 Pennsylvania Avenue"))

# ══ (6) safe degrade ═════════════════════════════════════════════════════════════════════════════
print("(6) _canonical_store_key_fn never raises on empty client")
fn = router._canonical_store_key_fn(FakeClient({}), ORG)
check("empty resolver folds case/whitespace (never raises)", fn("A B") == fn("a  b"))

# ══ (7) labeled-vocabulary check ═════════════════════════════════════════════════════════════════
print("(7) labeled-vocabulary — block-3 values (documented 'Port' miss)")
expect = {"Activation": "premium", "Port with IDV": "premium", "Upgrade": "upgrade",
          "BYOD Activation": "byod", "Internal Port with IDV": "premium", "Port": None}
for lbl, exp in expect.items():
    check(f"classify {lbl!r} == {exp}", CCT(lbl) == exp)

# ══ (8) M2 — drill-down + Exec-MTD filter are canonical-safe (endpoint-level) ═════════════════════
print("(8) M2 — merged Ave/Avenue store: drill-down + exec filter return the FULL multi-spelling set")
# Both spellings live in the FEED; store_mapping maps 'Avenue' → B-957 so 'Ave' merges via M1.
feed = [L("8001", ct="Activation", dept="d", store="957 Pennsylvania Ave"),
        L("8002", ct="Activation", dept="d", store="957 Pennsylvania Ave"),
        L("8003", ct="Activation", dept="d", store="957 Pennsylvania Ave"),
        L("8004", ct="Activation", dept="d", store="957 Pennsylvania Avenue"),
        L("8005", ct="Activation", dept="d", store="957 Pennsylvania Avenue")]
store = {"daily_sales_feed": feed, "raw_sales": [],
         "store_mapping": [{"org_id": ORG, "store_code": "B-957", "store_address": "957 Pennsylvania Avenue", "market": "NY"}]}
cli = FakeClient(store)
_orig_sb = router.sb
router.sb = lambda: cli
try:
    sr = asyncio.run(router.sales_report(period=OPEN, authorization="", org_id=ORG))
    loc_rows = [r for r in sr["rows"]]
    check("M2: Sales Report shows ONE merged 957 row (both spellings collapsed)",
          len([r for r in loc_rows if "957" in (r["store"] or "")]) == 1)
    merged_label = [r for r in loc_rows if "957" in (r["store"] or "")][0]["store"]
    check("M2: merged row counts all 5 activations", loc_rows[0]["activations"] == 5)
    # drill into the merged store by its label → must return ALL 5 (both spellings)
    det = asyncio.run(router.sales_report_detail(period=OPEN, store=merged_label,
                                                 salesperson="Jane Rep", date=D, org_id=ORG))
    n_txn = len(det.get("transactions", det.get("rows", [])) or [])
    check("M2: drill-down of the merged store returns ALL 5 transactions (both spellings)", n_txn == 5)
    # Exec MTD filtered by the merged label → full set
    ex = router._exec_mtd(cli, ORG, OPEN, stores=[merged_label], today=_date(_T.year, _T.month, 28))
    exrows = ex["by_location"]["rows"]
    check("M2: Exec MTD filter by the merged label keeps the FULL store (5 activations)",
          len(exrows) == 1 and (exrows[0]["activation"] + exrows[0]["port"]) == 5)
finally:
    router.sb = _orig_sb

# ══ (9) m1 — a STRING contains_any must NOT per-character match ═══════════════════════════════════
print("(9) m1 — STRING contains_any (SQL-seeded) can't per-char match an accessory-only txn")
bad_rule = [{"bucket": "premium", "all_of": [{"field": "department", "contains_any": "Rtr"}]}]  # STRING, not list
acc_txn = [L("9001", dept="Accessories", cat="Accessories", pdesc="Case", ext=9.99)]  # has 'r' chars, NO 'Rtr'
c9 = router._sales_cell_agg(acc_txn, acfg_with(rules=bad_rule))
k9 = ("957 Pennsylvania Avenue", "Jane Rep", D)
check("m1: string 'Rtr' is treated as one token, not chars → accessory-only NOT premium",
      len(c9.get(k9, {"_prem": []})["_prem"]) == 0)
# and the coerced-string form still WORKS as a whole-token match on a real Rtr line
c9b = router._sales_cell_agg([L("9002", dept="BrandedHandset"), L("9002", dept="Rtr")],
                             acfg_with(rules=[{"bucket": "premium",
                                               "all_of": [{"field": "department", "contains_any": "BrandedHandset"},
                                                          {"field": "department", "contains_any": "Rtr"}]}]))
check("m1: coerced string patterns still match as whole tokens (device+plan → premium)",
      len(c9b[("957 Pennsylvania Avenue", "Jane Rep", D)]["_prem"]) == 1)

# ══ (10) m2 — voided / Return lines are not rule evidence ═════════════════════════════════════════
print("(10) m2 — voided/Return lines excluded from rule evidence")
vtx = [L("10001", dept="BrandedHandset", pdesc="dev", voided="Yes"),   # voided device
       L("10001", dept="Rtr", pdesc="plan")]                            # live plan
c10 = router._sales_cell_agg(vtx, acfg_with(rules=LUX_RULES))
check("m2: voided device + live plan → NOT premium (voided isn't evidence)",
      len(c10.get(("957 Pennsylvania Avenue", "Jane Rep", D), {"_prem": []})["_prem"]) == 0)
# a txn whose ONLY ct-labeled line is VOIDED must still be rescuable by the blank-ct rules
vtx2 = [L("10002", dept="BrandedHandset", ct="Activation", voided="Yes"),  # voided labeled line
        L("10002", dept="BrandedHandset", pdesc="dev"),                    # live device
        L("10002", dept="Rtr", pdesc="plan")]                              # live plan
c10b = router._sales_cell_agg(vtx2, acfg_with(rules=LUX_RULES))
check("m2: a voided-only ct label doesn't mark tid 'classed' → still rescued to premium",
      len(c10b[("957 Pennsylvania Avenue", "Jane Rep", D)]["_prem"]) == 1)

# ══ (11) gap-note ALARM QUALITY — a bill-payment / accessory-only receipt is NOT a mapping gap ════
# 2026-08-09: the note counted EVERY blank-contract-type transaction, so the Total Wireless tenant was
# told 1009 of its 1303 August transactions needed mapping "so they count as activations" — when 930
# were RTR bill payments (which the owner ruled never pay) and 78 were accessory-only receipts. Acting
# on it would have created a rule that swept every bill payment into the activation count.
print("(11) gap note only alarms on transactions that could plausibly BE activations")

_RTR = [{"code": "rtr", "match_field": "product_desc", "match_op": "word",
         "match_value": "RTR", "enabled": True, "status": "confirmed"}]
from app.modules.commcalc.plan_pay_gate import exclusion_hit as _EXH  # noqa: E402
_is_exc = lambda r: _EXH(r, _RTR) is not None                          # noqa: E731

# a pure bill-payment receipt: every line is an RTR refill, nothing was activated
billpay_only = [L("2001", dept="Rtr", cat="Other Carr. payments", pdesc="Total MAX 5G Plan $55 RTR."),
                L("2001", dept="Rtr", cat="Other Carr. payments", pdesc="Total Wireless Protect+ RTR.")]
# an accessory-only receipt (base_acfg's accessory departments/categories)
acc_only = [L("2002", dept="Ondigo", cat="Accessory", pdesc="Case")]
# a REAL gap: a branded handset sold with no contract type at all
real_gap = [L("2003", dept="BrandedHandset", cat="KittedBranded", pdesc="Motorola Moto G 5G 2026 TO")]

g_bp = router._classification_gaps(billpay_only, acfg_with(rules=[]), is_excluded=_is_exc)
check("bill-payment-only receipt is not reported as an unclassified activation",
      g_bp["blank_ct_unrecovered"] == 0 and g_bp["note"] is None)
check("...but it is still COUNTED and returned, never silently dropped",
      g_bp["blank_ct_transactions"] == 1 and g_bp["blank_ct_non_activation"] == 1)

g_ac = router._classification_gaps(acc_only, acfg_with(rules=[]), is_excluded=_is_exc)
check("accessory-only receipt is not reported as an unclassified activation",
      g_ac["blank_ct_unrecovered"] == 0 and g_ac["note"] is None)

g_rg = router._classification_gaps(real_gap, acfg_with(rules=[]), is_excluded=_is_exc)
check("a device sold with NO contract type IS still reported (the alarm still works)",
      g_rg["blank_ct_unrecovered"] == 1 and g_rg["note"] is not None)

g_mix = router._classification_gaps(billpay_only + acc_only + real_gap,
                                    acfg_with(rules=[]), is_excluded=_is_exc)
check("mixed period: only the device receipt is flagged, the other two are explained",
      g_mix["blank_ct_transactions"] == 3 and g_mix["blank_ct_non_activation"] == 2
      and g_mix["blank_ct_unrecovered"] == 1)

# DEGRADATION: a tenant with no exclusion map configured keeps the OLD, louder behaviour rather than
# silently suppressing anything — the filter can only ever be as good as the tenant's own config.
g_noexc = router._classification_gaps(billpay_only, acfg_with(rules=[]))
check("no exclusion config → bill-payment receipt still flagged (no silent suppression)",
      g_noexc["blank_ct_unrecovered"] == 1)

# A transaction the activation_rules DO rescue is still rescued, not suppressed as non-activation.
g_resc = router._classification_gaps(
    [L("2004", dept="BrandedHandset", cat="KittedBranded", pdesc="dev"),
     L("2004", dept="Rtr", cat="Other Carr. payments", pdesc="plan")],
    acfg_with(rules=LUX_RULES), is_excluded=_is_exc)
check("the blank-ct activation rules still rescue what they always rescued",
      g_resc["rescued_by_rules"] == 1 and g_resc["blank_ct_unrecovered"] == 0)

print(f"\n{PASS}/{PASS + FAIL} passed" + ("" if not FAIL else f"  ({FAIL} FAILED)"))
sys.exit(1 if FAIL else 0)
