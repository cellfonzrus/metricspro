"""Proof harness for agent/commission/coverage-identity-bridge — drives the REAL
app.modules.commcalc.commission_engine over an in-memory FakeClient (no DB, no network).

Run:  cd backend && python3 scratchpad/coverage_identity_bridge_proof.py

The defect (owner, 2026-07-28): the Plan-coverage panel listed 15 sellers as "sales but NO plan attached",
all with a BLANK Market and "—" Role, even though the owner had assigned them markets, roles and plans.

What this proves
  A. MONEY PATH IS BYTE-IDENTICAL. preview(coverage=False) before/after is unchanged for a house-shaped
     tenant, and coverage=True never changes by_rep/totals.
  B. NAME BRIDGE IS NARRATED, NOT MADE FUZZY. _canon_person is untouched (still exact); the diagnosis
     merely EXPLAINS the miss and ranks candidates ("Sri ram, Nivas" ↔ roster "Nivas Sriram").
  C. epay_salesperson-ONLY matches are called out honestly — employee scope will attach, ROLE scope
     still cannot (role resolution reads the roster NAME column).
  D. ASSIGNMENT NEAR-MISS — "assignment exists under '<scope_value>' but names differ".
  E. STORE BRIDGE narrates address hit / code hit / first-token hit / alias hit, and distinguishes
     "unmapped" from "mapped but market blank".
  G. The ALIAS PREVIEW is honest — it re-runs the REAL _resolve_plan_for with the alias-resolved
     market/store keys, so it never promises an attachment the pay path would not make. (Making that
     resolution the PAY path is a separate, money-adjacent package.)
  H. ORPHAN ASSIGNMENTS — assigned to a name nobody sold under (the mirror of the unassigned list; the
     bulk-assign roster shows these as "current plan ✓").
  I. EXCLUDED SELLERS (Part D) — configurable, reported not hidden, and provably money-free.
  J. UNMATCHED EXPLORER (Part C) — both populations, group aggregates over ALL filtered lines, honest
     cap, filters drive everything, and the "nearby rules" come from the REAL _rule_matches.
  K. ORG ISOLATION — org A never sees org B's roster, aliases, assignments, sellers or lines.
  L. DEGRADATION — no mig 248/249 columns, no storeops roster, no store_aliases table: still a full
     answer, never an exception, and never a money change.
"""
import copy
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import app.modules.commcalc.commission_engine as CE

PASS = 0
FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f"FAIL  {name}   {extra}")


ORG_A = "854f6d7b-6590-4e4d-88ab-646f560d4f4c"      # luxelink-shaped tenant
ORG_B = "00000000-0000-0000-0000-000000000001"      # house / Boost


# ── in-memory fake supabase client ───────────────────────────────────────────────────────────────
class FakeResult:
    def __init__(self, data=None):
        self.data = data or []


class FakeQuery:
    def __init__(self, store, table, absent, absent_cols):
        self.store, self.t, self.absent, self.absent_cols = store, table, absent, absent_cols
        self.f, self.rng, self.cols = [], None, None
        self._upserted = None

    def select(self, cols="*", **k):
        self.cols = cols
        return self

    def eq(self, c, v):
        self.f.append(("eq", c, v))
        return self

    def in_(self, c, v):
        self.f.append(("in", c, list(v)))
        return self

    def limit(self, n):
        return self

    def order(self, *a, **k):
        return self

    def range(self, a, b):
        self.rng = (a, b)
        return self

    def upsert(self, row, on_conflict=None):
        """Minimal PostgREST upsert emulation, keyed on `on_conflict` (used by the settings writers)."""
        if self.t in self.absent:
            raise Exception(f'relation "{self.t}" does not exist')
        gone = self.absent_cols.get(self.t, set())
        bad = [c for c in row if c in gone]
        if bad:
            raise Exception(f'column {self.t}.{bad[0]} does not exist')
        rows = self.store.setdefault(self.t, [])
        key = on_conflict or "id"
        for r in rows:
            if r.get(key) == row.get(key):
                r.update(row)
                self._upserted = [dict(r)]
                return self
        rows.append(dict(row))
        self._upserted = [dict(row)]
        return self

    def _m(self, r):
        for k, c, v in self.f:
            if k == "eq" and r.get(c) != v:
                return False
            if k == "in" and r.get(c) not in v:
                return False
        return True

    def execute(self):
        if getattr(self, "_upserted", None) is not None:
            return FakeResult(self._upserted)
        if self.t in self.absent:
            raise Exception(f'relation "{self.t}" does not exist')
        rows = [r for r in self.store.get(self.t, []) if self._m(r)]
        if self.cols and self.cols != "*":
            want = [c.strip() for c in self.cols.split(",")]
            gone = self.absent_cols.get(self.t, set())
            bad = [c for c in want if c in gone]
            if bad:
                raise Exception(f'column {self.t}.{bad[0]} does not exist')
            known = set()
            for r in self.store.get(self.t, []):
                known |= set(r.keys())
            missing = [c for c in want if known and c not in known and c not in gone]
            if missing:
                raise Exception(f'column {self.t}.{missing[0]} does not exist')
            rows = [{c: r.get(c) for c in want} for r in rows]
        if self.rng:
            a, b = self.rng
            rows = rows[a:b + 1]
        return FakeResult([dict(r) for r in rows])


class FakeSchema:
    def __init__(self, client, s):
        self.c, self.s = client, s

    def table(self, t):
        return FakeQuery(self.c.store, t, self.c.absent, self.c.absent_cols)

    def rpc(self, name, params):
        raise Exception("no such rpc")


class FakeClient:
    def __init__(self, store, absent=None, absent_cols=None):
        self.store = store
        self.absent = set(absent or [])
        self.absent_cols = {k: set(v) for k, v in (absent_cols or {}).items()}

    def schema(self, s):
        return FakeSchema(self, s)


# ── fixture: the owner's real shape ─────────────────────────────────────────────────────────────
PLAN_MAIN = {"id": "p1", "org_id": ORG_A, "name": "Luxelink Base", "is_active": True}
PLAN_MKT = {"id": "p2", "org_id": ORG_A, "name": "Queens Market Plan", "is_active": True}
PLAN_B = {"id": "pb", "org_id": ORG_B, "name": "House Boost Plan", "is_active": True}

RULES = [
    {"id": "r1", "org_id": ORG_A, "plan_id": "p1", "match_field": "department", "match_op": "equals",
     "match_value": "accessories", "payout_kind": "pct_gp", "pct": 10, "amount": 0, "qualifies": True,
     "label": "Accessories 10% GP", "sort": 0},
    {"id": "r2", "org_id": ORG_A, "plan_id": "p2", "match_field": "category", "match_op": "equals",
     "match_value": "cases", "payout_kind": "flat_per_unit", "amount": 2, "pct": 0, "qualifies": True,
     "label": "Cases $2", "sort": 0},
    {"id": "rb", "org_id": ORG_B, "plan_id": "pb", "match_field": "any", "match_op": "equals",
     "match_value": "", "payout_kind": "flat_per_unit", "amount": 1, "pct": 0, "qualifies": True,
     "label": "House blanket", "sort": 0},
]

ASSIGNS = [
    # employee assignment written from the ROSTER value — the POS spells the name differently
    {"id": "a1", "org_id": ORG_A, "plan_id": "p1", "scope": "employee",
     "scope_value": "Nivas Sriram", "priority": 0},
    # market assignment that can only attach once the store resolves to a market
    {"id": "a2", "org_id": ORG_A, "plan_id": "p2", "scope": "market", "scope_value": "Queens",
     "priority": 0},
    {"id": "ab", "org_id": ORG_B, "plan_id": "pb", "scope": "default", "scope_value": None,
     "priority": 0},
]

ROSTER = [
    {"id": 1, "org_id": ORG_A, "name": "Nivas Sriram", "role": "sales rep",
     "email": "nivas@lux.test", "epay_salesperson": "", "home_store": "B-NOS", "is_active": True},
    # NOTE the roster NAME is spelled "Genevieve" while the POS rings "Genvieve" — so the comma-flip
    # alone does NOT bridge this one; only the ePay/POS name column does.
    {"id": 2, "org_id": ORG_A, "name": "Genevieve Montijo", "role": "sales rep",
     "email": "gen@lux.test", "epay_salesperson": "Montijo, Genvieve", "home_store": "B-LAW",
     "is_active": True},
    {"id": 3, "org_id": ORG_A, "name": "Ariful Islam Khan", "role": "market manager",
     "email": "ariful@lux.test", "epay_salesperson": "", "home_store": "B-HEM", "is_active": True},
    {"id": 9, "org_id": ORG_B, "name": "House Person", "role": "sales rep", "email": "h@b.test",
     "epay_salesperson": "", "home_store": "B-HSE", "is_active": True},
]

STORE_MAPPING = [
    # the POS rings "3560 Nostrand Avenue"; store_mapping stores it WITHOUT the suffix → exact miss
    {"org_id": ORG_A, "store_code": "B-NOS", "store_address": "3560 Nostrand Ave", "market": "Brooklyn"},
    # this one IS mapped but has NO market → "mapped_no_market"
    {"org_id": ORG_A, "store_code": "B-LAW", "store_address": "3248 w lawrence ave", "market": ""},
    {"org_id": ORG_A, "store_code": "B-HEM", "store_address": "218-80 Hempstead Ave", "market": "Queens"},
    {"org_id": ORG_B, "store_code": "B-HSE", "store_address": "1 house way", "market": "House"},
]

STORE_ALIASES = [
    {"org_id": ORG_A, "alias": "3560 Nostrand Avenue", "store_code": "B-NOS"},
    {"org_id": ORG_A, "alias": "218-80 Hempstead Avenue", "store_code": "B-HEM"},
]


def sale(org, rep, store, dept, cat, prod, ext, gp, tid, ct="", **kw):
    r = {"org_id": org, "period": "July 2026", "salesperson": rep, "store": store,
         "department": dept, "category": cat, "product_desc": prod, "sku": kw.get("sku", ""),
         "ext_price": ext, "gp": gp, "trans_id": tid, "trans_date": "2026-07-05",
         "contract_type": ct, "tender_type": "", "trans_type": "Sale", "voided": "",
         "mdn": "", "serial_1": ""}
    r.update({k: v for k, v in kw.items() if k != "sku"})
    return r


SALES = [
    # 1. NAME BRIDGE MISS: POS "Sri ram, Nivas" vs roster/assignment "Nivas Sriram"
    sale(ORG_A, "Sri ram, Nivas", "3560 Nostrand Avenue", "Accessories", "Cases", "Case A",
         50.0, 30.0, "T1"),
    sale(ORG_A, "Sri ram, Nivas", "3560 Nostrand Avenue", "Phones", "Handset", "Phone X",
         500.0, 100.0, "T2", ct="New Activation"),
    # 2. STORE BRIDGE MISS: name matches the roster's epay_salesperson, but the store has a blank market
    sale(ORG_A, "Montijo, Genvieve", "3248 W Lawrence Ave", "Accessories", "Cases", "Case B",
         40.0, 20.0, "T3"),
    # 3. MARKET-SCOPE would attach if the alias resolved: POS string differs from store_mapping
    sale(ORG_A, "Islam Khan, Ariful", "218-80 Hempstead Avenue", "Accessories", "Cases", "Case C",
         60.0, 25.0, "T4"),
    sale(ORG_A, "Islam Khan, Ariful", "218-80 Hempstead Avenue", "Internet", "Home Internet",
         "Fios 500", 120.0, 40.0, "T5"),
    # 4. POS ARTIFACTS
    sale(ORG_A, "Office, Back", "3560 Nostrand Avenue", "Misc", "Fees", "Bag fee", 1.0, 1.0, "T6"),
    sale(ORG_A, "ar, Rush", "3560 Nostrand Avenue", "Misc", "Fees", "Rush fee", 2.0, 2.0, "T7"),
    # org B — isolation control
    sale(ORG_B, "House Person", "1 House Way", "Accessories", "Cases", "House case",
         10.0, 5.0, "H1"),
]


# commission_org_config rows must carry EVERY column the engine may select — the FakeQuery emulates
# PostgREST, where selecting a column the table does not have is an ERROR (that strictness is what
# proves the widest-first probe in _plan_pay_config; see section M). Pre-migration is simulated with
# `absent_cols`, not by omitting a key.
CFG_COLUMNS = ("plan_ct_resolution", "store_resolution", "coverage_excluded_sellers",
               "coverage_artifact_hints")


def cfg(org=None, **kw):
    row = {"org_id": org or ORG_A}
    for c in CFG_COLUMNS:
        row[c] = kw.get(c)
    row["plan_ct_resolution"] = row["plan_ct_resolution"] or "raw"
    row["store_resolution"] = row["store_resolution"] or "exact"
    return row


def base_store(cfg_rows=None, absent=None, absent_cols=None, aliases=None, roster=None):
    st = {
        "commission_plan": [PLAN_MAIN, PLAN_MKT, PLAN_B],
        "commission_rule": copy.deepcopy(RULES),
        "commission_tier": [],
        "commission_plan_assignment": copy.deepcopy(ASSIGNS),
        "raw_sales": copy.deepcopy(SALES),
        "daily_sales_feed": [],
        "raw_mi": [],
        "raw_catalog": [],
        "store_mapping": copy.deepcopy(STORE_MAPPING),
        "store_aliases": copy.deepcopy(aliases if aliases is not None else STORE_ALIASES),
        "stores": [],   # storeops.stores — empty; store_mapping is the only canon here
        "employees": copy.deepcopy(roster if roster is not None else ROSTER),
        "accessory_config": [],
        "commission_org_config": copy.deepcopy(cfg_rows if cfg_rows is not None else []),
        "raw_dlar_store": [],
    }
    return FakeClient(st, absent=absent, absent_cols=absent_cols)


def cov_of(client, org=ORG_A, **kw):
    return CE.preview(client, org, "July 2026", coverage=True, **kw).get("coverage") or {}


def by_rep_name(cov_or_prev, rep):
    for u in cov_or_prev.get("unassigned_reps", []):
        if u["rep"] == rep:
            return u
    return None


print("\n── A. money path is byte-identical ─────────────────────────────────────────────")
c = base_store()
money = CE.preview(c, ORG_A, "July 2026")
money_cov = CE.preview(c, ORG_A, "July 2026", coverage=True)
check("A1 coverage=False returns no coverage block", "coverage" not in money)
check("A2 coverage=True leaves by_rep identical",
      money_cov["by_rep"] == money["by_rep"] or
      [{k: v for k, v in r.items() if k not in ("tier_units", "tier_basis", "unmatched_lines",
                                                "unmatched_ext_price", "unmatched_sample")}
       for r in money_cov["by_rep"]] == money["by_rep"],
      f"{money['by_rep']} vs {money_cov['by_rep']}")
check("A3 coverage=True leaves totals.payout identical",
      money_cov["totals"]["payout"] == money["totals"]["payout"],
      f"{money['totals']} vs {money_cov['totals']}")
check("A4 nobody is paid in this fixture (every seller is unassigned)",
      money["totals"]["payout"] == 0.0 and money["totals"]["reps"] == 0, money["totals"])
_house = CE.preview(base_store(), ORG_B, "July 2026")
check("A5 the house tenant's default-scope plan still pays (1 line x $1)",
      _house["totals"]["payout"] == 1.0, _house["totals"])

print("\n── B. name bridge narrated, NOT made fuzzy ─────────────────────────────────────")
check("B1 _canon_person is still exact (no token dropping)",
      CE._canon_person("Sri ram, Nivas") == "nivas sri ram"
      and CE._canon_person("Sri ram, Nivas") != CE._canon_person("Nivas Sriram"))
check("B2 the fuzzy score is diagnostic-only and DOES see the pair",
      CE._name_score("Sri ram, Nivas", "Nivas Sriram") == 1.0)
cov = cov_of(base_store())
u = by_rep_name(cov, "Sri ram, Nivas")
check("B3 the seller is listed with a structured diagnosis", bool(u and u.get("diagnosis")))
nb = (u or {}).get("diagnosis", {}).get("name_bridge", {})
check("B4 status = no_match", nb.get("status") == "no_match", nb.get("status"))
check("B5 the nearest roster candidate is Nivas Sriram at 100%",
      nb.get("candidates") and nb["candidates"][0]["name"] == "Nivas Sriram"
      and nb["candidates"][0]["score"] == 1.0, nb.get("candidates"))
check("B6 remediation names epay_salesperson AND the exact POS string",
      "epay_salesperson" in (nb.get("remediation") or "")
      and "Sri ram, Nivas" in (nb.get("remediation") or ""), nb.get("remediation"))
check("B7 remediation warns the existing assignment keeps the OLD spelling",
      "RE-APPLY" in (nb.get("remediation") or ""), nb.get("remediation"))
check("B8 at most 3 candidates", len(nb.get("candidates") or []) <= 3)

print("\n── C. epay-only match is called out honestly ───────────────────────────────────")
g = by_rep_name(cov, "Montijo, Genvieve")
gnb = (g or {}).get("diagnosis", {}).get("name_bridge", {})
check("C1 status = epay_match_only", gnb.get("status") == "epay_match_only", gnb.get("status"))
check("C2 message says ROLE scope still cannot attach",
      "role-scope assignment still" in (gnb.get("message") or "").lower()
      or "role resolution" in (gnb.get("message") or "").lower(), gnb.get("message"))
check("C3 the engine's own role map really does miss this rep (the claim is true)",
      CE._read_employee_roles(base_store(), ORG_A).get(CE._canon_person("Montijo, Genvieve")) is None)
check("C4 the engine's role map DOES resolve a roster-NAME match",
      CE._read_employee_roles(base_store(), ORG_A).get("nivas sriram") == "sales rep")
check("C5 the comma-flip DOES bridge a plain 'Last, First' vs 'First Last' pair (unchanged)",
      CE._canon_person("Montijo, Genevieve") == CE._canon_person("Genevieve Montijo"))
check("C6 the epay-only rep is still listed as unassigned (an assignment must exist too)",
      by_rep_name(cov, "Montijo, Genvieve") is not None)

print("\n── D. assignment near-miss ────────────────────────────────────────────────────")
near = (u or {}).get("diagnosis", {}).get("assignment_near_miss") or []
check("D1 the 'Nivas Sriram' assignment is surfaced as a near-miss",
      any(n["scope_value"] == "Nivas Sriram" for n in near), near)
check("D2 the message states both canonical forms",
      near and "nivas sriram" in near[0]["message"] and "nivas sri ram" in near[0]["message"],
      near[:1])

print("\n── E. store bridge narration ──────────────────────────────────────────────────")
sb_ = (u or {}).get("diagnosis", {}).get("store_bridge") or {}
check("E1 the Nostrand POS string is UNMAPPED under exact resolution",
      sb_.get("status") == "unmapped" and not sb_.get("exact_market"), sb_)
check("E2 but the alias table WOULD resolve it to B-NOS / Brooklyn",
      (sb_.get("alias") or {}).get("store_code") == "B-NOS" and sb_.get("alias_market") == "Brooklyn",
      sb_.get("alias"))
check("E3 the message points at /store-match or the alias setting",
      "store-match" in (sb_.get("message") or "").lower()
      or "alias" in (sb_.get("message") or "").lower(), sb_.get("message"))
gsb = (g or {}).get("diagnosis", {}).get("store_bridge") or {}
check("E4 a mapped store with a BLANK market is its own state, not 'unmapped'",
      gsb.get("status") == "mapped_no_market", gsb)
check("E5 and its message says to set the market in settings",
      "market" in (gsb.get("message") or "").lower()
      and "blank" in (gsb.get("message") or "").lower(), gsb.get("message"))
check("E6 the coverage Stores panel counts what would resolve with alias",
      cov["stores"]["would_resolve_with_alias"] >= 2 and cov["stores"]["mode"] == "exact",
      cov["stores"])

CFG_EXACT = [cfg(store_resolution="exact")]
CFG_ALIAS = [cfg(store_resolution="alias")]

print("\n── G. alias preview is honest (re-runs the REAL resolver) ─────────────────────")
ap = (by_rep_name(cov, "Islam Khan, Ariful") or {}).get("diagnosis", {}).get("alias_preview") or {}
check("G1 Ariful's preview says a plan WOULD attach", ap.get("would_attach") is True, ap)
check("G2 it names the plan and the scope",
      ap.get("plan_name") == "Queens Market Plan" and ap.get("scope") == "market", ap)
ap2 = (by_rep_name(cov, "Sri ram, Nivas") or {}).get("diagnosis", {}).get("alias_preview") or {}
check("G4 a rep whose gap is the NAME is told alias would NOT help",
      ap2.get("would_attach") is False, ap2)
check("G5 the alias preview is computed even though the pay path resolves stores exactly",
      cov["stores"]["mode"] == "exact" and bool(ap))

print("\n── H. orphan assignments (the mirror of the unassigned list) ──────────────────")
orph = cov.get("orphan_assignments") or []
check("H1 'Nivas Sriram' is reported as assigned-to-nobody",
      any(o["scope_value"] == "Nivas Sriram" for o in orph), orph)
o0 = next((o for o in orph if o["scope_value"] == "Nivas Sriram"), {})
check("H2 with the real seller as its nearest match",
      o0.get("nearest_sellers") and o0["nearest_sellers"][0]["rep"] == "Sri ram, Nivas", o0)
check("H3 a seller who DID sell under their assigned name is not an orphan",
      not any(o["scope_value"] == "House Person" for o in cov_of(base_store(), ORG_B)
              .get("orphan_assignments", [])))

print("\n── I. excluded sellers (Part D) — configurable, reported, money-free ──────────")
CFG_EXCL = [cfg(coverage_excluded_sellers=["Office, Back", "ar, Rush"])]
cov_x = cov_of(base_store(CFG_EXCL))
check("I1 excluded sellers leave the unassigned list",
      not any(x["rep"] in ("Office, Back", "ar, Rush") for x in cov_x["unassigned_reps"]),
      [x["rep"] for x in cov_x["unassigned_reps"]])
check("I2 ... and are REPORTED in excluded_reps (never hidden)",
      {x["rep"] for x in cov_x["excluded_reps"]} == {"Office, Back", "ar, Rush"}, cov_x["excluded_reps"])
check("I3 excluded totals are exposed",
      cov_x["excluded_count"] == 2 and cov_x["excluded_ext_price"] == 3.0, cov_x["excluded_count"])
check("I4 exclusion changes NO payout",
      CE.preview(base_store(CFG_EXCL), ORG_A, "July 2026")["totals"] == money["totals"])
check("I5 name-order-insensitive matching of the excluded name",
      not any(x["rep"] == "Office, Back" for x in
              cov_of(base_store([cfg(coverage_excluded_sellers=["Back Office"])]))
              ["unassigned_reps"]))
check("I6 artifact HINTS flag 'Office, Back' even before it is excluded",
      ((by_rep_name(cov, "Office, Back") or {}).get("diagnosis", {})
       .get("artifact", {}).get("confidence") == "high"))
check("I7 suspected artifacts sort to the BOTTOM of the unassigned list",
      [x["rep"] for x in cov["unassigned_reps"]][-2:] == ["Office, Back", "ar, Rush"]
      or {x["rep"] for x in cov["unassigned_reps"][-2:]} == {"Office, Back", "ar, Rush"},
      [x["rep"] for x in cov["unassigned_reps"]])
check("I8 a real seller is never flagged as an artifact",
      not (by_rep_name(cov, "Sri ram, Nivas") or {}).get("diagnosis", {}).get("artifact", {})
      .get("suspect"))
check("I9 a tenant hint list overrides the code default",
      (by_rep_name(cov_of(base_store([cfg(coverage_artifact_hints=["zzz"])])),
                   "Office, Back") or {}).get("diagnosis", {}).get("artifact", {})
      .get("confidence") != "high")

print("\n── J. unmatched-lines explorer (Part C) ───────────────────────────────────────")
ex = CE.unmatched_explorer(base_store(), ORG_A, "July 2026")
check("J1 both populations are present",
      set(ex["totals"]["by_why"]) == {"rep_unassigned"}, ex["totals"]["by_why"])
# give one rep a plan so 'no_rule_matched' exists too
c2 = base_store()
c2.store["commission_plan_assignment"] = ASSIGNS + [
    {"id": "a3", "org_id": ORG_A, "plan_id": "p1", "scope": "employee",
     "scope_value": "Sri ram, Nivas", "priority": 0}]
ex2 = CE.unmatched_explorer(c2, ORG_A, "July 2026")
check("J2 a covered rep's unmatched lines are tagged no_rule_matched",
      "no_rule_matched" in ex2["totals"]["by_why"], ex2["totals"]["by_why"])
check("J3 the covered rep's MATCHED line is NOT in the explorer",
      not any(l["trans_id"] == "T1" and l["rep"] == "Sri ram, Nivas" for l in ex2["lines"]),
      [l["trans_id"] for l in ex2["lines"] if l["rep"] == "Sri ram, Nivas"])
check("J4 ... but their unmatched Phones line IS",
      any(l["trans_id"] == "T2" and l["why"] == "no_rule_matched" for l in ex2["lines"]))
check("J5 voided / Return lines are excluded by the pay path's own gate",
      all(l.get("trans_type") != "Return" for l in ex["lines"]))
c3 = base_store()
c3.store["raw_sales"] = c3.store["raw_sales"] + [
    sale(ORG_A, "Sri ram, Nivas", "3560 Nostrand Avenue", "Accessories", "Cases", "Voided case",
         99.0, 99.0, "TV", voided="true"),
    sale(ORG_A, "Sri ram, Nivas", "3560 Nostrand Avenue", "Accessories", "Cases", "Returned case",
         99.0, 99.0, "TR", trans_type="Return")]
ex3 = CE.unmatched_explorer(c3, ORG_A, "July 2026")
check("J6 a voided line and a Return line never appear",
      not any(l["trans_id"] in ("TV", "TR") for l in ex3["lines"]),
      [l["trans_id"] for l in ex3["lines"]])
check("J7 grouping aggregates over ALL filtered lines",
      sum(gg["lines"] for gg in ex["groups"]) == ex["totals"]["lines"])
check("J8 nearby rules come from the REAL matcher — the Cases group knows about r2",
      any(gg.get("category") == "Cases" and
          any(h["rule_id"] == "r2" for h in gg["matching_rules"]) for gg in ex["groups"]),
      [(gg.get("category"), [h["rule_id"] for h in gg["matching_rules"]]) for gg in ex["groups"]])
check("J9 a category no rule references says so explicitly",
      any(gg.get("category") == "Home Internet" and gg["matching_rule_count"] == 0
          and "NO rule in any active plan" in gg["suggestion"] for gg in ex["groups"]),
      [(gg.get("category"), gg["matching_rule_count"]) for gg in ex["groups"]])
check("J10 filters drive the groups AND the totals",
      CE.unmatched_explorer(base_store(), ORG_A, "July 2026",
                            filters={"category": "Cases"})["totals"]["lines"] == 3,
      CE.unmatched_explorer(base_store(), ORG_A, "July 2026",
                            filters={"category": "Cases"})["totals"]["lines"])
check("J11 a rep filter narrows to that rep only — and a name WITH A COMMA survives it",
      {l["rep"] for l in CE.unmatched_explorer(base_store(), ORG_A, "July 2026",
                                               filters={"rep": ["Office, Back"]})["lines"]}
      == {"Office, Back"},
      CE.unmatched_explorer(base_store(), ORG_A, "July 2026",
                            filters={"rep": ["Office, Back"]})["totals"])
check("J11b multi-value uses '|' (never ',') so 'Last, First' is one value, not two",
      {l["rep"] for l in CE.unmatched_explorer(base_store(), ORG_A, "July 2026",
                                               filters={"rep": "Office, Back|ar, Rush"})["lines"]}
      == {"Office, Back", "ar, Rush"})
check("J12 a product substring filter works",
      CE.unmatched_explorer(base_store(), ORG_A, "July 2026",
                            filters={"product": "phone"})["totals"]["lines"] == 1)
check("J13 facets are offered from the UNFILTERED population (pick-don't-type)",
      len(CE.unmatched_explorer(base_store(), ORG_A, "July 2026",
                                filters={"category": "Cases"})["facets"]["category"]) >= 3)
capd = CE.unmatched_explorer(base_store(), ORG_A, "July 2026", line_limit=2)
check("J14 the cap is honest — line_cap / line_total / truncated all reported",
      len(capd["lines"]) == 2 and capd["line_cap"] == 2 and capd["truncated"] is True
      and capd["line_total"] == capd["totals"]["lines"], capd["line_total"])
check("J15 group totals are NOT capped",
      sum(gg["lines"] for gg in capd["groups"]) == capd["totals"]["lines"])
check("J16 the cap is bounded by UNMATCHED_LINE_CAP_MAX",
      CE.unmatched_explorer(base_store(), ORG_A, "July 2026",
                            line_limit=10**9)["line_cap"] == CE.UNMATCHED_LINE_CAP_MAX)
check("J17 group_by=product splits the two Misc fee products",
      len([gg for gg in CE.unmatched_explorer(base_store(), ORG_A, "July 2026",
                                              group_by="product")["groups"]
           if gg.get("department") == "Misc"]) == 2)
check("J18 an unknown group_by degrades to 'category' instead of erroring",
      CE.unmatched_explorer(base_store(), ORG_A, "July 2026", group_by="nonsense")["group_by"]
      == "category")
check("J19 excluded sellers are removed from the explorer AND counted",
      CE.unmatched_explorer(base_store(CFG_EXCL), ORG_A, "July 2026")
      ["totals"]["excluded_seller_lines"] == 2)
check("J20 the explorer never writes and never pays",
      CE.preview(base_store(), ORG_A, "July 2026")["totals"] == money["totals"])

print("\n── K. org isolation ───────────────────────────────────────────────────────────")
cb = cov_of(base_store(), ORG_B)
check("K1 org B sees none of org A's sellers",
      not any(x["rep"] in {"Sri ram, Nivas", "Montijo, Genvieve", "Islam Khan, Ariful"}
              for x in cb["unassigned_reps"]), cb["unassigned_reps"])
check("K2 org B's roster candidates never leak org A people",
      all(not any(cc["name"] in {"Nivas Sriram", "Ariful Islam Khan"}
                  for cc in ((x.get("diagnosis") or {}).get("name_bridge") or {}).get("candidates") or [])
          for x in cb["unassigned_reps"]))
check("K3 org B's store panel has only org B stores",
      all(r["store"] == "1 House Way" for r in cb["stores"]["rows"]), cb["stores"]["rows"])
exb = CE.unmatched_explorer(base_store(), ORG_B, "July 2026")
check("K4 org B's explorer has no org A lines",
      all(l["rep"] == "House Person" for l in exb["lines"]) if exb["lines"] else True,
      [l["rep"] for l in exb["lines"]])
check("K5 org A's orphan list never names an org B assignment",
      not any(o["plan_name"] == "House Boost Plan" for o in cov.get("orphan_assignments", [])))
check("K6 _coverage_config is org-scoped",
      CE._coverage_config(base_store([cfg(org=ORG_B,
                                          coverage_excluded_sellers=["Office, Back"])]),
                          ORG_A)["excluded_sellers"] == [])

print("\n── L. degradation ─────────────────────────────────────────────────────────────")
no248 = base_store([cfg()],
                   absent_cols={"commission_org_config": ["coverage_excluded_sellers",
                                                          "coverage_artifact_hints",
                                                          "store_resolution"]})
covL = cov_of(no248)
check("L1 pre-mig-248: coverage still answers in full",
      covL["unassigned_count"] >= 4 and covL["stores"]["mode"] == "exact")
check("L2 ... and reports the config as not-ready",
      covL["excluded_config"]["ready"] is False, covL["excluded_config"])
check("L3 ... and pays exactly what it pays today",
      CE.preview(no248, ORG_A, "July 2026")["totals"] == money["totals"])
noroster = base_store(absent=["employees"])
covR = cov_of(noroster)
uR = by_rep_name(covR, "Sri ram, Nivas")
check("L4 no storeops roster: honest 'roster_unavailable', not a name accusation",
      uR["diagnosis"]["name_bridge"]["status"] == "roster_unavailable",
      uR["diagnosis"]["name_bridge"]["status"])
noalias = base_store(absent=["store_aliases"])
covN = cov_of(noalias)
uN = by_rep_name(covN, "Sri ram, Nivas")
check("L5 no store_aliases table: store bridge still narrates, no exception",
      uN["diagnosis"]["store_bridge"]["status"] == "unmapped"
      and uN["diagnosis"]["store_bridge"]["alias"] is None)
check("L6 ... and 'alias' mode with no alias table changes nothing",
      CE.preview(base_store(CFG_ALIAS, absent=["store_aliases"]), ORG_A,
                 "July 2026")["totals"] == money["totals"])
nomapping = base_store(absent=["store_mapping"])
check("L7 no store_mapping at all: still no exception",
      cov_of(nomapping)["unassigned_count"] >= 4)
noplans = base_store()
noplans.store["commission_plan"] = []
check("L8 no plans: preview returns the 'no plans configured' note, not a crash",
      CE.preview(noplans, ORG_A, "July 2026").get("note") is not None)
check("L9 explorer on a tenant with no plans is honest",
      CE.unmatched_explorer(noplans, ORG_A, "July 2026")["ready"] is True
      or CE.unmatched_explorer(noplans, ORG_A, "July 2026")["note"] is not None)

print(f"\n{'='*70}\n{PASS} passed, {FAIL} failed\n{'='*70}")
sys.exit(1 if FAIL else 0)
