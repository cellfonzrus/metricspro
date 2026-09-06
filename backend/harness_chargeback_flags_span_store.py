"""Offline proof harness: `GET /commcalc/chargebacks/{period}` filtered its manager span on
`store_code` / `store_address` — but `commcalc.chargeback_items` has NEITHER column. Its store
field is `store`. Both arguments were therefore None on every row, `in_keyset` returned False for
every scoped caller, and the page was a 100% blackout: SQL-verified 1,138 rows / $44,430 in the
house org, visible to super-admins (`ks is None` skips the filter) and to nobody else. Every DM and
store manager opened Chargebacks & Fraud and read it as "$0 in chargebacks". Both tenants.

Second hunk, same class: `GET /commcalc/flags/{period}` passed `f.get('store_code')` as a second
key, but `commcalc.flags` has no `store_code` column either — always None, so it was dead weight
rather than a fallback. Dropping it is behaviour-preserving; the harness pins that.

WHY THIS CLASS HIDES (the reason it survived in prod): `scope_keyset()` returns None for an
unrestricted caller and `in_keyset(None, ...)` short-circuits True — so an admin login sees the
full, correct page. A span bug is UNREPRODUCIBLE from an admin session; "works for me" proves
nothing. Every case below therefore asserts the SCOPED caller's result, not the admin's.

NOT A PAY CHANGE. SQL-verified at the time of the fix: 0 of 1,138 house chargeback rows carry
`deduct=true`, and this diff does not touch `deduct`, `decided_by`, `decided_at`, or any consumer
of them (hr/router.py:239, account/coa.py:599, the four commcalc totals). Visibility only.

Store strings below are the REAL top-frequency house values from
`select store, count(*) from commcalc.chargeback_items group by store` so the coverage arithmetic
this harness pins is the production arithmetic, not an invented one.

Run: `cd backend && python3 harness_chargeback_flags_span_store.py`
"""
import ast
import inspect
import os
import sys
from types import SimpleNamespace

# Anchored to THIS FILE's directory, not the shell's cwd, so the harness runs identically from
# `backend/` and from the repo root (same repair as commit 564c171f). A relative sys.path/open()
# makes the run die with an import/FileNotFoundError, which reads as "not run" rather than "failed".
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)


def _src(rel):
    return open(os.path.join(_HERE, rel), encoding="utf-8").read()

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS" if cond else "FAIL") + f"  {name}" + (f"  [{detail}]" if detail and not cond else ""))


HOUSE = "00000000-0000-0000-0000-000000000001"
PERIOD = "2026-07"


# ── fake supabase chain client (same convention as harness_closing_reports_span_scope.py) ─────────
class Q:
    def __init__(self, store, table):
        self.s, self.t = store, table
        self.filters = []

    def select(self, *a, **k): return self
    def eq(self, c, v): self.filters.append((c, "eq", v)); return self
    def in_(self, c, v): self.filters.append((c, "in", list(v))); return self
    def order(self, *a, **k): return self
    def limit(self, n, *a, **k): return self

    def _match(self, row):
        for c, kind, v in self.filters:
            rv = row.get(c)
            if kind == "eq" and rv != v: return False
            if kind == "in" and rv not in v: return False
        return True

    def execute(self):
        return SimpleNamespace(data=[dict(r) for r in self.s.setdefault(self.t, []) if self._match(r)])


class FakeClient:
    def __init__(self, store): self.store = store
    def schema(self, _n): return self
    def table(self, name): return Q(self.store, name)


import app.modules.commcalc.router as cc     # noqa: E402
import app.modules.storeops.router as SO     # noqa: E402

AUTH_ADMIN = ""
AUTH_DM = "Bearer dm-token"


def wire(store):
    fake = FakeClient(store)
    cc.sb = lambda: fake
    return fake


def scoped(codes):
    """A span keyset, exactly as scope_keyset builds it: UPPER store_code + UPPER address."""
    ks = {str(c).strip().upper() for c in codes}
    SO.scope_keyset = lambda authorization="", org_id=HOUSE: (ks if authorization == AUTH_DM else None)


def cb(store, amount=10.0, rep="Jane Rep"):
    return {"org_id": HOUSE, "period": PERIOD, "store": store, "amount": amount,
            "epay_salesperson": rep, "deduct": False, "decided_at": None, "source": "vip"}


def flag(store_address, sev="warning", status="open"):
    # `status` models migration 287: the real column is NOT NULL DEFAULT 'open', and `get_flags` now
    # serves the OPEN queue by default (a flag whose condition cleared is RETIRED, not deleted). A
    # fixture without it would not be a flag row this system can produce.
    return {"org_id": HOUSE, "period": PERIOD, "store_address": store_address,
            "severity": sev, "flag_type": "missing_1st_mrc", "amount": 5.0, "status": status}


async def _drain(aw):
    return await aw


def run(result):
    """Call a handler WITHOUT caring whether it is `async def` today.

    `get_chargebacks` used to be `async def` and is now a plain `def`; this harness hard-coded
    `run_until_complete()` and so blew up with `TypeError: An asyncio.Future, a coroutine or an
    awaitable is required` the moment the shape changed — every assertion below stopped running
    while the page itself was fine. The async/sync SHAPE of a handler is not part of its
    behavioural contract, so the behavioural assertions must not be coupled to it. The SHAPE is
    asserted separately and deliberately in section 7 below, where it IS the contract.
    """
    import asyncio
    if inspect.isawaitable(result):
        return asyncio.run(_drain(result))
    return result


# ══════════════════════════════════════════════════════════════════════════════════════════════
# 1. THE REPORTED BUG — a scoped DM saw nothing at all
# ══════════════════════════════════════════════════════════════════════════════════════════════
store = {"chargeback_items": [cb("117 E Burnside Ave"), cb("3565 Broadway"), cb("723 N Market St")]}
wire(store)
scoped(["B-117", "117 E BURNSIDE AVE", "B-3565", "3565 BROADWAY"])

got = run(cc.get_chargebacks(PERIOD, authorization=AUTH_DM, org_id=HOUSE))
check("DM in-span chargebacks are visible (was 0 — total blackout)", len(got) == 2, f"got {len(got)}")
check("DM sees exactly the two in-span stores",
      sorted(c["store"] for c in got) == ["117 E Burnside Ave", "3565 Broadway"],
      str(sorted(c.get("store") for c in got)))
check("out-of-span store is still excluded (no over-share)",
      all(c["store"] != "723 N Market St" for c in got))

admin = run(cc.get_chargebacks(PERIOD, authorization=AUTH_ADMIN, org_id=HOUSE))
check("super-admin still sees everything (ks is None short-circuits)", len(admin) == 3, f"got {len(admin)}")

# ══════════════════════════════════════════════════════════════════════════════════════════════
# 2. THE REGRESSION GUARD — the OLD predicate must be provably dead
# ══════════════════════════════════════════════════════════════════════════════════════════════
from app.modules.storeops.router import in_keyset   # noqa: E402

ks = {"B-117", "117 E BURNSIDE AVE"}
row = cb("117 E Burnside Ave")
check("OLD predicate (store_code, store_address) rejects a row it should keep — the bug",
      in_keyset(ks, row.get("store_code"), row.get("store_address")) is False)
check("NEW predicate (store) keeps it", in_keyset(ks, row.get("store")) is True)
check("chargeback_items genuinely has no store_code/store_address key",
      "store_code" not in row and "store_address" not in row)

# ══════════════════════════════════════════════════════════════════════════════════════════════
# 3. KEY-SHAPE CASES — code-only grant, address-only grant, case + whitespace
# ══════════════════════════════════════════════════════════════════════════════════════════════
store = {"chargeback_items": [cb("5135 BERGENLINE"), cb("2778 Ephraim Ave "), cb("6011 Bergenline Ave")]}
wire(store)

scoped(["5135 BERGENLINE"])
got = run(cc.get_chargebacks(PERIOD, authorization=AUTH_DM, org_id=HOUSE))
check("address-only grant matches an UPPERCASE POS spelling", len(got) == 1, f"got {len(got)}")

scoped(["6011 bergenline ave"])   # keyset built from a lowercase source
got = run(cc.get_chargebacks(PERIOD, authorization=AUTH_DM, org_id=HOUSE))
check("match is case-insensitive both ways", len(got) == 1, f"got {len(got)}")

scoped(["2778 EPHRAIM AVE"])
got = run(cc.get_chargebacks(PERIOD, authorization=AUTH_DM, org_id=HOUSE))
check("trailing whitespace in the stored value is stripped before compare (1 real house row)",
      len(got) == 1, f"got {len(got)}")

# ══════════════════════════════════════════════════════════════════════════════════════════════
# 4. THE ROWS THIS FIX DOES *NOT* RESCUE — pinned so the limit is a fact, not a surprise
#    SQL-verified house coverage: 989 of 1,138 match; 149 miss. Of the misses, 82 + 41 carry a
#    store_aliases row (rescued only when agent/platform-core/scope-alias-span ships), 23 have no
#    alias at all (B-2509 — needs the alias INSERT), and 3 have a blank store (nobody's span).
# ══════════════════════════════════════════════════════════════════════════════════════════════
store = {"chargeback_items": [cb("3 Palisade Ave Yonkers"), cb("2509 Bergenline Ave Ste A"), cb("")]}
wire(store)
scoped(["B-3PL", "3 PALISADE AVE", "B-2509", "2509 BERGENLINE AVE"])

got = run(cc.get_chargebacks(PERIOD, authorization=AUTH_DM, org_id=HOUSE))
check("KNOWN LIMIT: an aliased POS spelling still misses until scope-alias-span ships",
      len(got) == 0, f"got {len(got)} — if this now passes, the alias fix landed; update this pin")
check("KNOWN LIMIT: a blank store matches no keyset and stays admin-only",
      all(c.get("store") for c in got))

admin = run(cc.get_chargebacks(PERIOD, authorization=AUTH_ADMIN, org_id=HOUSE))
check("...but all three remain visible to a super-admin (nothing is lost, only unrouted)",
      len(admin) == 3, f"got {len(admin)}")

# ══════════════════════════════════════════════════════════════════════════════════════════════
# 5. FLAGS — dropping the dead store_code arg changes nothing
# ══════════════════════════════════════════════════════════════════════════════════════════════
store = {"flags": [flag("117 E Burnside Ave"), flag("723 N Market St"), flag("")]}
wire(store)
scoped(["B-117", "117 E BURNSIDE AVE"])

got = run(cc.get_flags(PERIOD, authorization=AUTH_DM, org_id=HOUSE))
check("flags: in-span flag visible", len(got) == 1 and got[0]["store_address"] == "117 E Burnside Ave",
      str([f.get("store_address") for f in got]))
check("flags: out-of-span flag excluded", all(f["store_address"] != "723 N Market St" for f in got))
check("flags: BLANK store_address still matches nothing — 27,428 of 31,037 house rows are in this "
      "state and stay super-admin-only until the DM-routing work lands",
      all(f.get("store_address") for f in got))

admin = run(cc.get_flags(PERIOD, authorization=AUTH_ADMIN, org_id=HOUSE))
check("flags: super-admin still sees all three", len(admin) == 3, f"got {len(admin)}")

# mig 287 — the retired flag must leave the active queue WITHOUT leaving the table
store = {"flags": [flag("117 E Burnside Ave"),
                   flag("117 E Burnside Ave", status="resolved"),
                   flag("117 E Burnside Ave", status="superseded")]}
wire(store)
scoped(["B-117", "117 E BURNSIDE AVE"])
got = run(cc.get_flags(PERIOD, authorization=AUTH_DM, org_id=HOUSE))
check("flags: a CLEARED/REPLACED flag is out of the active queue by default (mig 287)",
      len(got) == 1 and got[0]["status"] == "open", f"got {[f['status'] for f in got]}")
got = run(cc.get_flags(PERIOD, authorization=AUTH_DM, include_resolved=True, org_id=HOUSE))
check("flags: ...and is still THERE — retire is a status change, never a DELETE",
      len(got) == 3, f"got {len(got)}")

# ══════════════════════════════════════════════════════════════════════════════════════════════
# 6. MULTI-TENANT — org_id is still enforced ahead of the span filter
# ══════════════════════════════════════════════════════════════════════════════════════════════
other = "854f6d7b-6590-4e4d-88ab-646f560d4f4c"
store = {"chargeback_items": [cb("117 E Burnside Ave"),
                              dict(cb("117 E Burnside Ave"), org_id=other)]}
wire(store)
scoped(["117 E BURNSIDE AVE"])
got = run(cc.get_chargebacks(PERIOD, authorization=AUTH_DM, org_id=HOUSE))
check("the other tenant's identically-addressed row is NOT returned", len(got) == 1, f"got {len(got)}")

# ══════════════════════════════════════════════════════════════════════════════════════════════
# 7. ASYNC SHAPE — the SEV-1 2026-07-30 class (sync client called from an async endpoint)
# ══════════════════════════════════════════════════════════════════════════════════════════════
# WHY THIS IS HERE. Sections 1-6 were dead for months because this file hard-coded
# `run_until_complete()` and `get_chargebacks` became a plain `def`. Reviewing that drift raised the
# question the crash was hiding: which shape SHOULD these two handlers have?
#
# The answer is not cosmetic. `sb()` is the SYNCHRONOUS supabase client (app/core/database.py —
# `create_client`, blocking httpx). FastAPI runs a plain `def` handler in an anyio worker THREAD, so
# its blocking call costs one thread. It runs an `async def` handler ON THE EVENT LOOP, so the same
# blocking call stalls EVERY other in-flight request for its whole duration. That is verbatim the
# SEV-1 of 2026-07-30 (account/ai_limits.py: a sync client called from an async endpoint froze the
# backend). `async def` around blocking I/O buys nothing and costs availability.
#
# So the correct shape for a handler that touches sb() and awaits nothing is `def`. The rule pinned
# below: a route handler in this file's scope may be `async def` only if it actually awaits.
def _handler_shape(module_rel, name):
    """(is_async, awaits_something) for a top-level handler, read from source (no import needed)."""
    tree = ast.parse(_src(module_rel))
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name:
            awaits = any(isinstance(k, (ast.Await, ast.AsyncFor, ast.AsyncWith)) for k in ast.walk(n))
            return isinstance(n, ast.AsyncFunctionDef), awaits
    raise AssertionError(f"{name} not found in {module_rel} — the harness anchor moved")


_CC = "app/modules/commcalc/router.py"

# Non-vacuity: if either name is ever renamed, _handler_shape raises with a clear message rather
# than quietly asserting nothing.
_cb_async, _cb_awaits = _handler_shape(_CC, "get_chargebacks")
check("get_chargebacks does blocking sb() work, so it is a plain `def` (threadpool, not the loop)",
      not _cb_async, "it is `async def`")

_fl_async, _fl_awaits = _handler_shape(_CC, "get_flags")
check("get_flags likewise — it was `async def` with NO await over the blocking client (found by "
      "this section on 2026-09-06 and fixed: every Flags page load was stalling the event loop for "
      "a whole supabase round-trip). Re-introducing `async` here re-introduces the SEV-1",
      not (_fl_async and not _fl_awaits),
      "async def with no await, over a blocking sync client")

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILED: " + ", ".join(FAIL))
sys.exit(1 if FAIL else 0)
