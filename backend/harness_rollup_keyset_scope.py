"""Offline proof harness for retail-ops-23 (OWNER BUG REPORT 2026-08-03, PACKAGE A): `/closing/rollup`'s
manager-span keyset used to be applied ONLY to `by_store`/`by_rep` AFTER `grand` (the tiles) had already
been accumulated over every kept row — a span-restricted viewer (e.g. a DM) saw ORG-WIDE money in the top
tiles while the table beneath showed only their stores. Fixed by resolving the keyset BEFORE the
accumulation loop and gating row admission on it (same place market_set/store_set/rep_set already gate),
so `grand`/`by_store`/`by_rep`/`verified_keys`/`submitted_keys` are all computed over the identical
visible row set.

Same convention as harness_dmverify_parity.py / harness_team_snapshot_perf.py: runs the REAL
`closing_rollup` function against a stateful fake Supabase-chain client, monkeypatching
`app.modules.storeops.router.scope_keyset` directly (closing/router.py's `from ... import scope_keyset,
in_keyset` is a LOCAL import re-executed on every call, so patching the storeops module attribute is
picked up live — same trick harness_team_snapshot_perf.py already uses).

Run: `cd backend && python3 harness_rollup_keyset_scope.py`

Proves:
  A. Unscoped caller (scope_keyset -> None) is BYTE-IDENTICAL to before this fix: totals == sum over
     every kept row, by_store/by_rep unrestricted.
  B. Scoped caller (keyset limited to store S1's code+address) — TILES == TABLE FOOTER: `totals` equals
     the sum of the VISIBLE `by_store` rows only, not the org-wide total. This is the exact bug: before
     the fix `totals` stayed org-wide while `by_store` was already scoped.
  C. `verified_keys`/`submitted_keys` are ALSO computed over the same scoped set (an out-of-span store's
     verification doesn't count toward a scoped viewer's coverage numbers).
  D. A row with NO store identity (`store_code` None, no resolvable address) is excluded from a SCOPED
     viewer's totals/by_store/by_rep (deliberate: an identity-less row is not provably inside a DM's
     span) but still included for an UNSCOPED viewer (unchanged pre-existing behavior).
  E. Keyset scoping composes correctly with an ACTIVE market/store/rep filter (both gates apply; result
     is the intersection, not either gate alone).
  F. date_from/date_to RANGE mode gets the identical fix (not just period= mode).
  G. by_rep is scoped the same way as by_store, and its sum also equals `totals`.
  H. Multi-tenant isolation is unaffected by the keyset fix (org_id filtering still happens first via
     the query's own `.eq("org_id", org_id)`, independent of the keyset).
  I. Scoping by ADDRESS (not just store_code) still matches — `in_keyset` checks both.
"""
import sys
from types import SimpleNamespace

sys.path.insert(0, ".")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS" if cond else "FAIL") + f"  {name}" + (f"  [{detail}]" if detail and not cond else ""))


HOUSE = "00000000-0000-0000-0000-000000000001"
OTHER = "00000000-0000-0000-0000-000000000099"
_ID = {"n": 0}


def nid(pfx="id"):
    _ID["n"] += 1
    return f"{pfx}-{_ID['n']}"


# ── stateful fake supabase client (copied convention from harness_dmverify_parity.py) ──────────────
class Q:
    def __init__(self, store, table):
        self.s, self.t = store, table
        self.op, self.payload = "select", None
        self.filters = []
        self._limit = None

    def select(self, *a, **k): self.op = "select"; return self
    def insert(self, rows, **k): self.op = "insert"; self.payload = rows; return self
    def update(self, patch, **k): self.op = "update"; self.payload = patch; return self
    def delete(self, **k): self.op = "delete"; return self
    def eq(self, c, v): self.filters.append((c, "eq", v)); return self
    def in_(self, c, v): self.filters.append((c, "in", list(v))); return self
    def gte(self, c, v): self.filters.append((c, "gte", v)); return self
    def lte(self, c, v): self.filters.append((c, "lte", v)); return self
    def order(self, *a, **k): return self
    def limit(self, n, *a, **k): self._limit = n; return self

    def _match(self, row):
        for c, kind, v in self.filters:
            rv = row.get(c)
            if kind == "eq" and rv != v: return False
            if kind == "in" and rv not in v: return False
            if kind == "gte" and not (rv is not None and str(rv) >= str(v)): return False
            if kind == "lte" and not (rv is not None and str(rv) <= str(v)): return False
        return True

    def execute(self):
        rows = self.s.setdefault(self.t, [])
        if self.op == "select":
            matched = [dict(r) for r in rows if self._match(r)]
            if self._limit is not None:
                matched = matched[: self._limit]
            return SimpleNamespace(data=matched)
        if self.op == "insert":
            payload = self.payload if isinstance(self.payload, list) else [self.payload]
            out = []
            for r in payload:
                r = dict(r); r.setdefault("id", nid(self.t))
                rows.append(r); out.append(dict(r))
            return SimpleNamespace(data=out)
        return SimpleNamespace(data=[])


class FakeClient:
    def __init__(self, store): self.store = store
    def schema(self, _n): return self
    def table(self, name): return Q(self.store, name)


def fresh_store():
    return {"daily_closing": [], "daily_closing_verification": [], "stores": []}


import app.modules.closing.router as cr             # noqa: E402
import app.modules.storeops.router as SO             # noqa: E402

AUTH_NONE = ""
AUTH_SCOPED = "Bearer dm-token"


def wire(store):
    fake = FakeClient(store)
    cr.sb = lambda: fake
    cr.get_supabase = lambda: fake
    SO.scope_keyset = lambda authorization="", org_id=HOUSE: None
    return fake


def dc_row(**kw):
    r = {"org_id": HOUSE, "close_date": "2026-07-15", "period": "2026-07",
         "store_code": "S1", "store_address": "1 Main St", "store_name": "1 Main St",
         "employee_name": "Jane Rep", "source": "manual",
         "store_cash": 100.0, "store_cc": 50.0, "epay_cash": 0.0, "epay_cc": 0.0,
         "acc_sale": 25.0, "other_account": 0.0,
         "upgrade_count": 1, "new_line_count": 2, "postpaid_count": 0}
    r.update(kw)
    return r


def sum_money(rows, key):
    return round(sum(r.get(key, 0.0) or 0.0 for r in rows), 2)


# ═══════════════════════ A. Unscoped caller — byte-identical to before the fix ═══════════════════════
st = fresh_store(); wire(st)
st["stores"] = [{"org_id": HOUSE, "store_code": "S1", "address": "1 Main St", "market": "Texas"},
                {"org_id": HOUSE, "store_code": "S2", "address": "2 Oak Ave", "market": "Ohio"}]
st["daily_closing"] = [
    dc_row(id="s1a", store_code="S1", employee_name="Jane Rep", store_cash=100.0, store_cc=50.0),
    dc_row(id="s1b", store_code="S1", employee_name="John Rep", store_cash=40.0, store_cc=10.0),
    dc_row(id="s2a", store_code="S2", employee_name="Mo Rep", store_cash=200.0, store_cc=75.0),
]
st["daily_closing_verification"] = [
    {"org_id": HOUSE, "store_code": "S1", "close_date": "2026-07-15", "verified": True},
    {"org_id": HOUSE, "store_code": "S2", "close_date": "2026-07-15", "verified": True},
]
roll_unscoped = cr.closing_rollup(period="2026-07", authorization=AUTH_NONE, org_id=HOUSE)
check("A1. unscoped: totals.store_cash sums ALL 3 rows (100+40+200=340)",
      roll_unscoped["totals"]["store_cash"] == 340.0, str(roll_unscoped["totals"]["store_cash"]))
check("A2. unscoped: by_store has both stores", len(roll_unscoped["by_store"]) == 2)
check("A3. unscoped: submitted_keys/verified_keys unaffected (2/2)",
      roll_unscoped["submitted_keys"] == 2 and roll_unscoped["verified_keys"] == 2,
      f"{roll_unscoped['submitted_keys']}/{roll_unscoped['verified_keys']}")

# ═══════════════════════ B. Scoped caller — TILES == TABLE FOOTER (the actual bug) ═══════════════════
st2 = fresh_store(); wire(st2)
st2["stores"] = [{"org_id": HOUSE, "store_code": "S1", "address": "1 Main St", "market": "Texas"},
                 {"org_id": HOUSE, "store_code": "S2", "address": "2 Oak Ave", "market": "Ohio"}]
st2["daily_closing"] = [
    dc_row(id="s1a", store_code="S1", employee_name="Jane Rep", store_cash=100.0, store_cc=50.0),
    dc_row(id="s1b", store_code="S1", employee_name="John Rep", store_cash=40.0, store_cc=10.0),
    dc_row(id="s2a", store_code="S2", employee_name="Mo Rep", store_cash=200.0, store_cc=75.0),
]
# DM scoped to S1 only.
SO.scope_keyset = lambda authorization="", org_id=HOUSE: ({"S1", "1 MAIN ST"} if authorization == AUTH_SCOPED else None)
roll_scoped = cr.closing_rollup(period="2026-07", authorization=AUTH_SCOPED, org_id=HOUSE)
check("B1. scoped: by_store shows ONLY S1 (the pre-existing, already-correct half of the bug)",
      [s["store_code"] for s in roll_scoped["by_store"]] == ["S1"], str(roll_scoped["by_store"]))
check("B2. scoped: totals.store_cash == S1-only sum (140), NOT the org-wide 340 — THE BUG this fixes",
      roll_scoped["totals"]["store_cash"] == 140.0, str(roll_scoped["totals"]["store_cash"]))
check("B3. scoped: totals.store_cc == S1-only sum (60), NOT org-wide 135",
      roll_scoped["totals"]["store_cc"] == 60.0, str(roll_scoped["totals"]["store_cc"]))
check("B4. TILES == TABLE FOOTER: totals.store_cash equals sum of the VISIBLE by_store rows",
      roll_scoped["totals"]["store_cash"] == sum_money(roll_scoped["by_store"], "store_cash"),
      f"totals={roll_scoped['totals']['store_cash']} vs by_store-sum={sum_money(roll_scoped['by_store'], 'store_cash')}")
check("B5. TILES == TABLE FOOTER: totals.store_cc equals sum of the VISIBLE by_store rows",
      roll_scoped["totals"]["store_cc"] == sum_money(roll_scoped["by_store"], "store_cc"),
      f"totals={roll_scoped['totals']['store_cc']} vs by_store-sum={sum_money(roll_scoped['by_store'], 'store_cc')}")
check("B6. scoped: totals.rows == 2 (only S1's 2 submitted rows counted, not S2's)",
      roll_scoped["totals"]["rows"] == 2, str(roll_scoped["totals"]["rows"]))
SO.scope_keyset = lambda authorization="", org_id=HOUSE: None  # reset for next section

# ═══════════════════════ C. verified_keys/submitted_keys ALSO scoped ═════════════════════════════════
st3 = fresh_store(); wire(st3)
st3["stores"] = [{"org_id": HOUSE, "store_code": "S1", "address": "1 Main St", "market": "Texas"},
                 {"org_id": HOUSE, "store_code": "S2", "address": "2 Oak Ave", "market": "Ohio"}]
st3["daily_closing"] = [
    dc_row(id="s1a", store_code="S1", close_date="2026-07-15"),
    dc_row(id="s2a", store_code="S2", close_date="2026-07-15"),
]
st3["daily_closing_verification"] = [
    {"org_id": HOUSE, "store_code": "S1", "close_date": "2026-07-15", "verified": True},
    {"org_id": HOUSE, "store_code": "S2", "close_date": "2026-07-15", "verified": True},
]
SO.scope_keyset = lambda authorization="", org_id=HOUSE: ({"S1"} if authorization == AUTH_SCOPED else None)
roll_c_unscoped = cr.closing_rollup(period="2026-07", authorization=AUTH_NONE, org_id=HOUSE)
roll_c_scoped = cr.closing_rollup(period="2026-07", authorization=AUTH_SCOPED, org_id=HOUSE)
check("C1. unscoped: submitted_keys/verified_keys count BOTH stores (2/2)",
      roll_c_unscoped["submitted_keys"] == 2 and roll_c_unscoped["verified_keys"] == 2)
check("C2. scoped: submitted_keys/verified_keys count ONLY S1 (1/1) — was leaking S2's coverage before",
      roll_c_scoped["submitted_keys"] == 1 and roll_c_scoped["verified_keys"] == 1,
      f"{roll_c_scoped['submitted_keys']}/{roll_c_scoped['verified_keys']}")
SO.scope_keyset = lambda authorization="", org_id=HOUSE: None

# ═══════════════════════ D. Identity-less row: excluded when scoped, kept when unscoped ═════════════
st4 = fresh_store(); wire(st4)
st4["daily_closing"] = [
    dc_row(id="s1a", store_code="S1", store_cash=100.0),
    dc_row(id="ghost", store_code=None, store_name="Unresolved Store", store_address=None, store_cash=999.0),
]
roll_d_unscoped = cr.closing_rollup(period="2026-07", authorization=AUTH_NONE, org_id=HOUSE)
check("D1. unscoped: identity-less row IS counted (matches pre-existing by_store/by_rep behavior)",
      roll_d_unscoped["totals"]["store_cash"] == 1099.0 and len(roll_d_unscoped["by_store"]) == 2,
      str(roll_d_unscoped["totals"]["store_cash"]))

SO.scope_keyset = lambda authorization="", org_id=HOUSE: ({"S1"} if authorization == AUTH_SCOPED else None)
roll_d_scoped = cr.closing_rollup(period="2026-07", authorization=AUTH_SCOPED, org_id=HOUSE)
check("D2. scoped: identity-less row EXCLUDED (can't be proven inside the DM's span) — totals stay "
      "100, not 1099; privacy boundary wins over completeness for a scoped viewer",
      roll_d_scoped["totals"]["store_cash"] == 100.0 and len(roll_d_scoped["by_store"]) == 1,
      str((roll_d_scoped["totals"]["store_cash"], len(roll_d_scoped["by_store"]))))
SO.scope_keyset = lambda authorization="", org_id=HOUSE: None

# ═══════════════════════ E. Keyset composes correctly with an active market/store/rep filter ═════════
st5 = fresh_store(); wire(st5)
st5["stores"] = [{"org_id": HOUSE, "store_code": "S1", "address": "1 Main St", "market": "Texas"},
                 {"org_id": HOUSE, "store_code": "S2", "address": "2 Oak Ave", "market": "Texas"},
                 {"org_id": HOUSE, "store_code": "S3", "address": "3 Elm St", "market": "Ohio"}]
st5["daily_closing"] = [
    dc_row(id="s1", store_code="S1", store_cash=10.0),
    dc_row(id="s2", store_code="S2", store_cash=20.0),
    dc_row(id="s3", store_code="S3", store_cash=30.0),
]
# DM spans S1+S2 (both Texas); caller ALSO filters markets=Texas -> should still be just S1+S2 (keyset
# doesn't add anything beyond what the market filter already limits to, proving the two gates AND
# together rather than a scoped viewer's market filter somehow re-widening past their span).
SO.scope_keyset = lambda authorization="", org_id=HOUSE: ({"S1", "S2"} if authorization == AUTH_SCOPED else None)
roll_e = cr.closing_rollup(period="2026-07", markets="Texas", authorization=AUTH_SCOPED, org_id=HOUSE)
check("E1. keyset ∩ market filter: only S1+S2 (both in span AND in Texas)",
      sorted(s["store_code"] for s in roll_e["by_store"]) == ["S1", "S2"], str(roll_e["by_store"]))
check("E2. totals match the intersection sum (10+20=30), not all 3 stores' 60",
      roll_e["totals"]["store_cash"] == 30.0, str(roll_e["totals"]["store_cash"]))

# Now narrow the span to S1 ONLY, market filter stays Texas -> span wins (S2 excluded even though it's
# also Texas) — proves the keyset gate is a hard boundary, not just influencing the market bucket.
SO.scope_keyset = lambda authorization="", org_id=HOUSE: ({"S1"} if authorization == AUTH_SCOPED else None)
roll_e2 = cr.closing_rollup(period="2026-07", markets="Texas", authorization=AUTH_SCOPED, org_id=HOUSE)
check("E3. narrower span (S1 only) + markets=Texas -> ONLY S1, even though S2 is also Texas",
      [s["store_code"] for s in roll_e2["by_store"]] == ["S1"], str(roll_e2["by_store"]))
check("E4. totals == S1 only (10), not the Texas-market total (30)",
      roll_e2["totals"]["store_cash"] == 10.0, str(roll_e2["totals"]["store_cash"]))
SO.scope_keyset = lambda authorization="", org_id=HOUSE: None

# ═══════════════════════ F. date_from/date_to RANGE mode gets the identical fix ═══════════════════════
st6 = fresh_store(); wire(st6)
st6["daily_closing"] = [
    dc_row(id="r1", store_code="S1", close_date="2026-07-05", store_cash=10.0),
    dc_row(id="r2", store_code="S2", close_date="2026-07-06", store_cash=20.0),
]
SO.scope_keyset = lambda authorization="", org_id=HOUSE: ({"S1"} if authorization == AUTH_SCOPED else None)
roll_f_unscoped = cr.closing_rollup(date_from="2026-07-01", date_to="2026-07-31", authorization=AUTH_NONE, org_id=HOUSE)
roll_f_scoped = cr.closing_rollup(date_from="2026-07-01", date_to="2026-07-31", authorization=AUTH_SCOPED, org_id=HOUSE)
check("F1. range mode, unscoped: totals sums both rows (30)", roll_f_unscoped["totals"]["store_cash"] == 30.0)
check("F2. range mode, scoped: totals == S1-only (10), not org-wide (30) — same fix applies in range mode",
      roll_f_scoped["totals"]["store_cash"] == 10.0, str(roll_f_scoped["totals"]["store_cash"]))
check("F3. range mode, scoped: tiles == table footer",
      roll_f_scoped["totals"]["store_cash"] == sum_money(roll_f_scoped["by_store"], "store_cash"))
SO.scope_keyset = lambda authorization="", org_id=HOUSE: None

# ═══════════════════════ G. by_rep is scoped the same way; its sum also equals totals ═══════════════
st7 = fresh_store(); wire(st7)
st7["daily_closing"] = [
    dc_row(id="s1_jane", store_code="S1", employee_name="Jane Rep", store_cash=100.0),
    dc_row(id="s2_mo", store_code="S2", employee_name="Mo Rep", store_cash=200.0),
]
SO.scope_keyset = lambda authorization="", org_id=HOUSE: ({"S1"} if authorization == AUTH_SCOPED else None)
roll_g = cr.closing_rollup(period="2026-07", authorization=AUTH_SCOPED, org_id=HOUSE)
check("G1. by_rep shows ONLY Jane (S1's rep), Mo (S2) excluded",
      [r["employee_name"] for r in roll_g["by_rep"]] == ["Jane Rep"], str(roll_g["by_rep"]))
check("G2. by_rep sum also equals totals (100)",
      sum_money(roll_g["by_rep"], "store_cash") == roll_g["totals"]["store_cash"] == 100.0,
      str((sum_money(roll_g["by_rep"], "store_cash"), roll_g["totals"]["store_cash"])))
SO.scope_keyset = lambda authorization="", org_id=HOUSE: None

# ═══════════════════════ H. Multi-tenant isolation unaffected by the keyset fix ═══════════════════════
st8 = fresh_store(); wire(st8)
st8["daily_closing"] = [
    dc_row(id="house_row", org_id=HOUSE, store_code="S1", store_cash=50.0),
    dc_row(id="other_row", org_id=OTHER, store_code="S1", store_cash=999.0, employee_name="Intruder"),
]
roll_house = cr.closing_rollup(period="2026-07", authorization=AUTH_NONE, org_id=HOUSE)
check("H1. HOUSE call never sees OTHER org's row regardless of keyset fix",
      roll_house["totals"]["store_cash"] == 50.0, str(roll_house["totals"]["store_cash"]))

# ═══════════════════════ I. Scoping by ADDRESS (not just store_code) still matches ═════════════════
st9 = fresh_store(); wire(st9)
st9["stores"] = [{"org_id": HOUSE, "store_code": "S1", "address": "1 Main St", "market": "Texas"}]
st9["daily_closing"] = [dc_row(id="s1a", store_code="S1", store_address="1 Main St", store_cash=77.0)]
SO.scope_keyset = lambda authorization="", org_id=HOUSE: ({"1 MAIN ST"} if authorization == AUTH_SCOPED else None)
roll_i = cr.closing_rollup(period="2026-07", authorization=AUTH_SCOPED, org_id=HOUSE)
check("I1. keyset containing ONLY the address (not the code) still matches via meta.address",
      roll_i["totals"]["store_cash"] == 77.0 and len(roll_i["by_store"]) == 1, str(roll_i["totals"]))
SO.scope_keyset = lambda authorization="", org_id=HOUSE: None

# ── Summary ──────────────────────────────────────────────────────────────────────────────────────
print(f"\n{len(PASS)}/{len(PASS) + len(FAIL)} checks passed")
if FAIL:
    print("FAILED:")
    for f in FAIL:
        print("  -", f)
    sys.exit(1)
