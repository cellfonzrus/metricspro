"""Offline proof harness for the envelope-photo-required gate (mig 510, owner-reported bug
2026-08-07). Same stateful-fake-Supabase-client convention as harness_eep_retail_ops.py /
harness_closing_hardening.py — runs the REAL `cr.create_row` / `cr.put_envelope_config` /
`cr.get_envelope_config` / `cr._envelope_config`, no live DB/network.

Run: `cd backend && python3 harness_envelope_photo_required_gate.py`

Proves:
  A. Un-configured tenant (no envelope_payout_config row at all) -> require_photo_if_cash reads the
     coded default False -> a cash>0 closing with NO envelope photo still SUCCEEDS, byte-identical to
     today's unconditional accept (the whole point of the "empty config = today's behaviour" doctrine).
  B. Org default opted IN (require_photo_if_cash=True) -> a cash>0 closing with NO photo is BLOCKED
     with a clean HTTPException(400), and NOTHING is written to daily_closing (no partial row).
  C. Same org config -> a cash>0 closing WITH a photo path succeeds normally.
  D. Same org config -> a $0-cash closing with NO photo is NEVER blocked (the gate is cash-scoped).
  E. A per-store override can turn the gate back OFF for one store even though the org default is ON
     (store override wins over org default, matching every other envelope-config field).
  F. PUT /closing/envelope-config round-trips require_photo_if_cash through GET's `effective` view,
     without disturbing any of the pre-existing EEP fields (take_commission/cadence/order_preference).
  G. Degrade pre-migration: envelope_payout_config table missing entirely (select raises) ->
     _envelope_config returns the coded default (False) -> the gate is a total no-op, never crashes.
"""
import sys
from types import SimpleNamespace

sys.path.insert(0, ".")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS" if cond else "FAIL") + f"  {name}" + (f"  [{detail}]" if detail and not cond else ""))


HOUSE = "00000000-0000-0000-0000-000000000001"
_ID = {"n": 0}


def nid(pfx="id"):
    _ID["n"] += 1
    return f"{pfx}-{_ID['n']}"


# ── stateful fake supabase client (copied convention from harness_eep_retail_ops.py) ────────────────
class Q:
    def __init__(self, store, table, poison_writes=False):
        self.s, self.t = store, table
        self.op, self.payload = "select", None
        self.filters = []
        self._limit = None
        self._order = None
        self._poison = poison_writes

    def select(self, *a, **k): self.op = "select"; return self
    def insert(self, rows, **k): self.op = "insert"; self.payload = rows; return self
    def update(self, patch, **k): self.op = "update"; self.payload = patch; return self
    def upsert(self, rows, **k): self.op = "upsert"; self.payload = rows; return self
    def delete(self, **k): self.op = "delete"; return self
    def eq(self, c, v): self.filters.append((c, "eq", v)); return self
    def in_(self, c, v): self.filters.append((c, "in", list(v))); return self
    def gte(self, c, v): self.filters.append((c, "gte", v)); return self
    def lte(self, c, v): self.filters.append((c, "lte", v)); return self
    def is_(self, c, v): self.filters.append((c, "is", v)); return self
    def ilike(self, c, v): self.filters.append((c, "ilike", v)); return self
    def order(self, col, desc=False, **k): self._order = (col, desc); return self
    def limit(self, n, *a, **k): self._limit = n; return self

    def _match(self, row):
        for c, kind, v in self.filters:
            rv = row.get(c)
            if kind == "eq" and rv != v: return False
            if kind == "in" and rv not in v: return False
            if kind == "gte" and not (rv is not None and str(rv) >= str(v)): return False
            if kind == "lte" and not (rv is not None and str(rv) <= str(v)): return False
            if kind == "is" and v == "null" and rv is not None: return False
            if kind == "ilike" and str(rv or "").lower() != str(v or "").lower(): return False
        return True

    def execute(self):
        if self._poison and self.op in ("insert", "update", "delete", "upsert"):
            raise AssertionError(f"UNEXPECTED WRITE ({self.op}) on {self.t}")
        rows = self.s.setdefault(self.t, [])
        if self.op == "select":
            matched = [dict(r) for r in rows if self._match(r)]
            if self._order:
                col, desc = self._order
                matched.sort(key=lambda r: str(r.get(col) or ""), reverse=desc)
            if self._limit is not None:
                matched = matched[: self._limit]
            return SimpleNamespace(data=matched)
        if self.op in ("insert", "upsert"):
            payload = self.payload if isinstance(self.payload, list) else [self.payload]
            out = []
            for r in payload:
                r = dict(r)
                if self.op == "upsert" and r.get("id"):
                    existing = next((x for x in rows if x.get("id") == r["id"]), None)
                    if existing:
                        existing.update(r); out.append(dict(existing)); continue
                r.setdefault("id", nid(self.t))
                rows.append(r); out.append(dict(r))
            return SimpleNamespace(data=out)
        if self.op == "update":
            out = []
            for r in rows:
                if self._match(r):
                    r.update(self.payload); out.append(dict(r))
            return SimpleNamespace(data=out)
        if self.op == "delete":
            keep = [r for r in rows if not self._match(r)]
            deleted = [r for r in rows if self._match(r)]
            self.s[self.t] = keep
            return SimpleNamespace(data=deleted)
        return SimpleNamespace(data=[])


class FakeClient:
    def __init__(self, store, poison_writes=False):
        self.store = store
        self.poison_writes = poison_writes

    def schema(self, _n): return self
    def table(self, name): return Q(self.store, name, poison_writes=self.poison_writes)


class PoisonTableClient(FakeClient):
    """envelope_payout_config's own select raises (table doesn't exist yet, pre-mig-507) — every
    other table behaves normally."""
    def table(self, name):
        if name == "envelope_payout_config":
            class Boom:
                def select(self, *a, **k): raise Exception('relation "commcalc.envelope_payout_config" does not exist')
            return Boom()
        return super().table(name)


def fresh_store():
    return {"daily_closing": [], "stores": [], "envelope_payout_config": [], "closing_expense": [],
            "envelope_withdrawal": [], "tenants": [], "closing_attempt": []}


import app.modules.core.router as core                # noqa: E402
import app.modules.storeops.router as storeops         # noqa: E402
import app.modules.closing.router as cr                # noqa: E402


def wire(store, unrestricted_span=True, manager=True):
    fake = FakeClient(store)
    cr.sb = lambda: fake
    cr.get_supabase = lambda: fake
    core.get_supabase = lambda: fake
    if unrestricted_span:
        storeops.scope_keyset = lambda auth, org: None
    if manager:
        cr._caller_perms = lambda client, auth: {"__super_admin": True, "__resolved": True}
        cr._caller_email = lambda client, auth: "dm@test.com"
    return fake


import asyncio  # noqa: E402


async def _submit(payload, org_id=HOUSE):
    return await cr.create_row(payload, org_id=org_id)


def base_payload(employee_name, store_code="S1", t_cash="0", envelope_picture=""):
    return {"close_date": "2026-08-07", "store_code": store_code, "store_name": "1 Main St",
            "employee_name": employee_name, "t_cash": t_cash, "t_credit": "0",
            "envelope_picture": envelope_picture}


# ═══════════════════════════════════════════════════════════════════════════════════════════════════
# A. Un-configured tenant -> coded default False -> cash>0 + no photo still SUCCEEDS
# ═══════════════════════════════════════════════════════════════════════════════════════════════════
print("== A. un-configured tenant: gate is a no-op (byte-identical to today) ==")
store = fresh_store()
fake = wire(store)
cfg = cr._envelope_config(fake, HOUSE, "S1")
check("coded default require_photo_if_cash is False", cfg.get("require_photo_if_cash") is False)

resp = asyncio.run(_submit(base_payload("Syed 117", t_cash="500", envelope_picture="")))
check("cash>0, no photo, no config -> submit ACCEPTED", resp.get("accepted") is True)
check("row written with envelope_picture NULL (today's exact symptom, unblocked)",
      len(store["daily_closing"]) == 1 and store["daily_closing"][0].get("envelope_picture") is None)


# ═══════════════════════════════════════════════════════════════════════════════════════════════════
# B. Org default opted IN -> cash>0 + no photo is BLOCKED, nothing written
# ═══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== B. org default require_photo_if_cash=True -> cash>0 + no photo BLOCKS ==")
store = fresh_store()
fake = wire(store)
cr.put_envelope_config({"require_photo_if_cash": True}, org_id=HOUSE, authorization="")
cfg = cr._envelope_config(fake, HOUSE, "S1")
check("org default now reads True", cfg.get("require_photo_if_cash") is True)

try:
    asyncio.run(_submit(base_payload("Suanny Hidalgo", t_cash="200", envelope_picture="")))
    check("cash>0, no photo, gate ON -> blocked", False)
except Exception as e:
    check("cash>0, no photo, gate ON -> blocked with HTTPException(400)",
          getattr(e, "status_code", None) == 400, str(e))
check("nothing written to daily_closing on a blocked submit", len(store["daily_closing"]) == 0)


# ═══════════════════════════════════════════════════════════════════════════════════════════════════
# C. Same config -> cash>0 WITH a photo path succeeds
# ═══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== C. gate ON, photo attached -> succeeds ==")
resp = asyncio.run(_submit(base_payload("Suanny Hidalgo", t_cash="200",
                                         envelope_picture=f"{HOUSE}/20260807120000000000.jpg")))
check("cash>0 WITH photo path -> accepted", resp.get("accepted") is True)
check("envelope_picture persisted", store["daily_closing"][0].get("envelope_picture") is not None)


# ═══════════════════════════════════════════════════════════════════════════════════════════════════
# D. Same config -> a $0-cash closing with no photo is NEVER blocked
# ═══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== D. gate ON, $0 cash -> never blocked (cash-scoped, not a blanket photo requirement) ==")
resp = asyncio.run(_submit(base_payload("Rohit", t_cash="0", envelope_picture="")))
check("$0 cash, no photo, gate ON -> still accepted", resp.get("accepted") is True)


# ═══════════════════════════════════════════════════════════════════════════════════════════════════
# E. Per-store override turns the gate back OFF for one store
# ═══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== E. store override wins over org default ==")
cr.put_envelope_config({"store_code": "S2", "require_photo_if_cash": False}, org_id=HOUSE, authorization="")
cfg_s1 = cr._envelope_config(fake, HOUSE, "S1")
cfg_s2 = cr._envelope_config(fake, HOUSE, "S2")
check("S1 (no override) still True (org default)", cfg_s1.get("require_photo_if_cash") is True)
check("S2 (explicit override) reads False", cfg_s2.get("require_photo_if_cash") is False)
resp = asyncio.run(_submit(base_payload("Simarjyot Singh", store_code="S2", t_cash="150", envelope_picture="")))
check("S2 cash>0, no photo, store override OFF -> accepted despite org default ON",
      resp.get("accepted") is True)


# ═══════════════════════════════════════════════════════════════════════════════════════════════════
# F. PUT round-trips require_photo_if_cash through GET without disturbing pre-existing EEP fields
# ═══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== F. PUT/GET round-trip + sibling fields untouched ==")
store2 = fresh_store()
fake2 = wire(store2)
cr.put_envelope_config({"take_commission": True, "take_salary": False, "commission_cadence": "weekly",
                        "order_preference": "newest_first", "require_photo_if_cash": True},
                       org_id=HOUSE, authorization="")
got = cr.get_envelope_config(org_id=HOUSE)
eff = got["effective"]
check("effective.require_photo_if_cash == True", eff.get("require_photo_if_cash") is True)
check("sibling field take_salary unaffected (False)", eff.get("take_salary") is False)
check("sibling field order_preference unaffected", eff.get("order_preference") == "newest_first")

# Explicit False is honored too (not just "unset").
cr.put_envelope_config({"require_photo_if_cash": False}, org_id=HOUSE, authorization="")
got2 = cr.get_envelope_config(org_id=HOUSE)
check("explicit False round-trips as False", got2["effective"].get("require_photo_if_cash") is False)


# ═══════════════════════════════════════════════════════════════════════════════════════════════════
# G. Degrade pre-migration: envelope_payout_config table missing entirely
# ═══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== G. pre-migration degrade: table missing -> coded default, never crashes ==")
poison_fake = PoisonTableClient(fresh_store())
cfg = cr._envelope_config(poison_fake, HOUSE, "S1")
check("pre-migration degrade -> require_photo_if_cash False", cfg.get("require_photo_if_cash") is False)

cr.sb = lambda: poison_fake
cr.get_supabase = lambda: poison_fake
resp = asyncio.run(_submit(base_payload("Kashif", t_cash="900", envelope_picture="")))
check("pre-migration + cash>0 + no photo -> still accepted (gate can't fire on a missing table)",
      resp.get("accepted") is True)


print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILED:", FAIL)
    sys.exit(1)
