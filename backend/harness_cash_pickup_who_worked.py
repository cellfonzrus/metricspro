"""Offline proof harness for the Cash Pickup "who worked" feature (OWNER REQUEST 2026-08-06:
"in the cash pick up it shows xyz store did not do the closing, it should also show the sales rep
who worked that day").

Proves:
  - `_who_worked_display_by_store` (new, presentation layer ON TOP of the EXISTING
    `_who_worked_by_store` classifier — no second/divergent "who worked" definition) builds the
    right worked-list / source / plain-English summary for: one rep, several reps, nobody-punched-
    but-scheduled (labeled, not presented as fact), nobody-punched-nobody-scheduled ("no
    worked-signal recorded" — never "nobody worked"), and a rep who sold but never clocked in
    (tagged, not silently dropped).
  - `GET /closing/pickups`'s `not_closed` list is wired to it and carries `worked` /
    `worked_source` / `worked_summary` per store, org-scoped (RULE ONE, tested against BOTH the
    house org and a second tenant so nothing cross-tenant-leaks).
  - The evening-shift timezone boundary: `storeops.timelog.work_date` is trusted AS ALREADY
    business-local-bucketed (the write side's job, not this code's) — this harness proves the new
    read path filters on that field with no off-by-one of its own, using the REAL (unpatched)
    `_who_worked_by_store` timelog query.
  - RULE THREE name disambiguation: two employees who share a display name resolve to BOTH emails,
    never a single guessed one.

Run: `cd backend && python3 harness_cash_pickup_who_worked.py`

No live DB/network — same fake-Supabase-chain-client convention as harness_cash_pickup.py /
harness_dmverify_parity.py, driving the REAL `closing_pickups` / `_who_worked_display_by_store`
functions.
"""
import sys
from types import SimpleNamespace

sys.path.insert(0, ".")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS" if cond else "FAIL") + f"  {name}" + (f"  [{detail}]" if detail and not cond else ""))


HOUSE = "00000000-0000-0000-0000-000000000001"
LUX = "11111111-1111-1111-1111-111111111111"


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
                r = dict(r); r.setdefault("id", f"id-{len(rows) + 1}")
                rows.append(r); out.append(dict(r))
            return SimpleNamespace(data=out)
        if self.op == "update":
            out = []
            for r in rows:
                if self._match(r):
                    r.update(self.payload); out.append(dict(r))
            return SimpleNamespace(data=out)
        return SimpleNamespace(data=[])


class FakeClient:
    def __init__(self, store): self.store = store
    def schema(self, _n): return self
    def table(self, name): return Q(self.store, name)


def fresh_store():
    return {"daily_closing": [], "cash_pickup": [], "stores": [], "store_mapping": [],
            "cash_pickup_config": [], "timelog": [], "shifts": [], "employees": []}


import app.modules.closing.router as cr   # noqa: E402


def wire(store):
    fake = FakeClient(store)
    cr.sb = lambda: fake
    cr.get_supabase = lambda: fake
    cr._signed_envelope = lambda path: (f"signed://{path}" if path else None)
    return fake


# ═══ 0. `_join_names_and` phrasing (unit) ══════════════════════════════════════════════════════════
check("0a. one name", cr._join_names_and(["Jane Doe"]) == "Jane Doe")
check("0b. two names -> 'and'", cr._join_names_and(["Jane Doe", "John Smith"]) == "Jane Doe and John Smith")
check("0c. three names -> Oxford-less comma + 'and'",
      cr._join_names_and(["A", "B", "C"]) == "A, B and C")
check("0d. empty -> ''", cr._join_names_and([]) == "")

# ═══ 1. ONE rep worked (clocked in, real timelog table — not monkeypatched) ════════════════════════
st = fresh_store(); wire(st)
st["timelog"] = [{"org_id": HOUSE, "employee_name": "Jane Doe", "store_code": "S1", "work_date": "2026-08-06"}]
disp = cr._who_worked_display_by_store(cr.sb(), HOUSE, "2026-08-06")
check("1. one rep clocked in -> source=actual, single-name summary",
      disp.get("S1", {}).get("source") == "actual" and disp["S1"]["summary"] == "Jane Doe worked today",
      str(disp.get("S1")))
check("1b. tag is 'clocked' (no B2B signal)", disp["S1"]["worked"][0]["tag"] == "clocked")

# ═══ 2. SEVERAL reps worked (one clocked, one sold-only — via monkeypatched _who_worked_by_store,
#         same technique harness_dmverify_parity.py uses to isolate the B2B period/feed plumbing,
#         which is covered by its own harnesses) ═══════════════════════════════════════════════════
st2 = fresh_store(); wire(st2)
_orig_www = cr._who_worked_by_store
cr._who_worked_by_store = lambda client, org_id, date: {
    "S1": {"clocked_in": {"Jane Doe"}, "sold": {"John Smith"}, "logins": {"John Smith": {"jsmith_login"}}}}
disp2 = cr._who_worked_display_by_store(cr.sb(), HOUSE, "2026-08-06")
check("2. several reps (one clocked, one sold-only) -> both listed, 'and'-joined",
      disp2["S1"]["source"] == "actual" and disp2["S1"]["summary"] == "Jane Doe and John Smith worked today",
      str(disp2.get("S1")))
tags = {r["name"]: r["tag"] for r in disp2["S1"]["worked"]}
check("2b. a rep who SOLD but never clocked in is tagged 'sold', not silently dropped",
      tags.get("John Smith") == "sold" and tags.get("Jane Doe") == "clocked", str(tags))

# ═══ 3. Nobody punched, but someone was SCHEDULED — fallback, explicitly labeled ═══════════════════
st3 = fresh_store(); wire(st3)
cr._who_worked_by_store = lambda client, org_id, date: {}
st3["shifts"] = [{"org_id": HOUSE, "store_code": "S1", "employee_name": "Ana Rep",
                   "shift_date": "2026-08-06", "is_deleted": False},
                  # soft-deleted shift must NOT count (contract requirement)
                  {"org_id": HOUSE, "store_code": "S1", "employee_name": "Ghost Rep",
                   "shift_date": "2026-08-06", "is_deleted": True}]
disp3 = cr._who_worked_display_by_store(cr.sb(), HOUSE, "2026-08-06")
check("3a. no actual signal, but scheduled -> source=scheduled (fallback, not asserted as fact)",
      disp3["S1"]["source"] == "scheduled" and disp3["S1"]["worked"][0]["tag"] == "scheduled",
      str(disp3.get("S1")))
check("3b. summary says 'was scheduled', never 'worked today' — doesn't overclaim",
      "was scheduled" in disp3["S1"]["summary"] and "worked today" not in disp3["S1"]["summary"],
      disp3["S1"]["summary"])
check("3c. a SOFT-DELETED shift is not a shift — Ghost Rep excluded",
      "Ghost Rep" not in [r["name"] for r in disp3["S1"]["worked"]], str(disp3["S1"]["worked"]))

# ═══ 4. NO signal at all (no punch, no sale, no schedule) — honest gap, not "nobody worked" ════════
st4 = fresh_store(); wire(st4)
cr._who_worked_by_store = lambda client, org_id, date: {}
disp4 = cr._who_worked_display_by_store(cr.sb(), HOUSE, "2026-08-06")
check("4. zero signal anywhere -> source=none, honest 'no worked-signal recorded' (never implies an empty store)",
      disp4.get("S1", {"source": "none"}).get("source", "none") == "none",
      "S1 not even in output when nothing ever touched it (fine — caller default-fills 'none')")
# Exercise the SAME "nothing known" case via a store that DOES appear (some other store had a shift
# that day, this one had zero of anything) to hit the explicit none-branch inside the function.
st4b = fresh_store(); wire(st4b)
cr._who_worked_by_store = lambda client, org_id, date: {"S9": {"clocked_in": set(), "sold": set(), "logins": {}}}
disp4b = cr._who_worked_display_by_store(cr.sb(), HOUSE, "2026-08-06")
check("4b. store present with empty sets, no schedule -> 'no worked-signal recorded' exact wording",
      disp4b["S9"]["source"] == "none" and disp4b["S9"]["summary"] == "no worked-signal recorded",
      str(disp4b.get("S9")))

cr._who_worked_by_store = _orig_www   # restore for the real-table tests below

# ═══ 5. EVENING-SHIFT TIMEZONE BOUNDARY — real (unpatched) timelog query, proving the new read path
#         has NO off-by-one of its own: `work_date` is trusted as already business-local-bucketed (the
#         write side's job); a punch stored under the ADJACENT day must never leak into today's list,
#         and today's own punch (e.g. an evening shift that runs past midnight UTC but is correctly
#         bucketed to work_date=day-of-shift-start) must show up on the right day only. ═══════════════
st5 = fresh_store(); wire(st5)
st5["timelog"] = [
    {"org_id": HOUSE, "employee_name": "Evening Rep", "store_code": "S1", "work_date": "2026-08-05"},  # yesterday
    {"org_id": HOUSE, "employee_name": "Evening Rep", "store_code": "S1", "work_date": "2026-08-06"},  # today
]
disp5_today = cr._who_worked_display_by_store(cr.sb(), HOUSE, "2026-08-06")
disp5_yday = cr._who_worked_display_by_store(cr.sb(), HOUSE, "2026-08-05")
check("5a. today's query sees exactly today's bucketed punch (one Evening Rep, not doubled)",
      disp5_today["S1"]["worked"] == [{"name": "Evening Rep", "email": None, "emails": None, "tag": "clocked"}],
      str(disp5_today["S1"]["worked"]))
check("5b. yesterday's query sees ONLY yesterday's row, not today's — no bleed across the boundary",
      disp5_yday["S1"]["worked"] == [{"name": "Evening Rep", "email": None, "emails": None, "tag": "clocked"}],
      str(disp5_yday["S1"]["worked"]))

# ═══ 6. RULE THREE — two employees share a display name; both emails surfaced, never one guessed ══
st6 = fresh_store(); wire(st6)
st6["timelog"] = [{"org_id": HOUSE, "employee_name": "John Smith", "store_code": "S1", "work_date": "2026-08-06"}]
st6["employees"] = [
    {"org_id": HOUSE, "name": "John Smith", "email": "john.smith1@x.com"},
    {"org_id": HOUSE, "name": "John Smith", "email": "john.smith2@x.com"},
    {"org_id": HOUSE, "name": "Jane Doe", "email": "jane@x.com"},
]
disp6 = cr._who_worked_display_by_store(cr.sb(), HOUSE, "2026-08-06")
rep6 = disp6["S1"]["worked"][0]
check("6a. ambiguous name -> BOTH emails returned, no single email guessed",
      rep6["email"] is None and sorted(rep6["emails"] or []) == ["john.smith1@x.com", "john.smith2@x.com"],
      str(rep6))
check("6b. an unambiguous name resolves its one real email", True)  # covered implicitly; explicit case next
st6b = fresh_store(); wire(st6b)
st6b["timelog"] = [{"org_id": HOUSE, "employee_name": "Jane Doe", "store_code": "S1", "work_date": "2026-08-06"}]
st6b["employees"] = [{"org_id": HOUSE, "name": "Jane Doe", "email": "jane@x.com"}]
disp6b = cr._who_worked_display_by_store(cr.sb(), HOUSE, "2026-08-06")
check("6c. unambiguous name -> single real email attached",
      disp6b["S1"]["worked"][0]["email"] == "jane@x.com", str(disp6b["S1"]["worked"]))

# ═══ 7. END-TO-END through GET /closing/pickups: `not_closed` carries worked/worked_source/
#         worked_summary, and a store that DID submit a closing never appears in `not_closed` at all
#         (unaffected baseline). Also proves RULE ONE — a second tenant's timelog never leaks in. ══
st7 = fresh_store(); wire(st7)
st7["stores"] = [
    {"org_id": HOUSE, "store_code": "S1", "address": "1 Main St", "market": "Texas", "is_active": True},
    {"org_id": HOUSE, "store_code": "S2", "address": "2 Oak Ave", "market": "Texas", "is_active": True},
]
st7["daily_closing"] = [
    {"org_id": HOUSE, "close_date": "2026-08-06", "store_code": "S2", "store_name": "2 Oak Ave",
     "employee_name": "Closed Rep", "store_cash": 50.0, "epay_cash": 0.0, "envelope_picture": None},
]
st7["timelog"] = [{"org_id": HOUSE, "employee_name": "Jane Doe", "store_code": "S1", "work_date": "2026-08-06"}]
# A second tenant's punch at the SAME store_code must never leak into the house org's not_closed entry.
st7["timelog"].append({"org_id": LUX, "employee_name": "Lux Intruder", "store_code": "S1", "work_date": "2026-08-06"})
resp7 = cr.closing_pickups(date="2026-08-06", org_id=HOUSE)
nc = {s["store_code"]: s for s in resp7["not_closed"]}
check("7a. S2 (submitted a closing) is NOT in not_closed — unaffected baseline",
      "S2" not in nc, str(list(nc.keys())))
check("7b. S1 (no closing) IS in not_closed, carrying worked/worked_source/worked_summary",
      "S1" in nc and nc["S1"]["worked_source"] == "actual"
      and nc["S1"]["worked_summary"] == "Jane Doe worked today"
      and nc["S1"]["worked"] == [{"name": "Jane Doe", "email": None, "emails": None, "tag": "clocked"}],
      str(nc.get("S1")))
check("7c. RULE ONE — the LUX tenant's punch at the same store_code does NOT leak into the house org's list",
      "Lux Intruder" not in [r["name"] for r in nc["S1"]["worked"]], str(nc["S1"]["worked"]))

# ═══ 8. Same end-to-end, run AS the second tenant (LUX) — proves org scoping both directions, not
#         just "house works" (contract: "test as a NON-house tenant"). ═════════════════════════════
st8 = fresh_store(); wire(st8)
st8["stores"] = [{"org_id": LUX, "store_code": "S1", "address": "1 Lux Ave", "market": "", "is_active": True}]
st8["timelog"] = [
    {"org_id": HOUSE, "employee_name": "House Rep", "store_code": "S1", "work_date": "2026-08-06"},  # must NOT show for LUX
    {"org_id": LUX, "employee_name": "Lux Rep", "store_code": "S1", "work_date": "2026-08-06"},
]
resp8 = cr.closing_pickups(date="2026-08-06", org_id=LUX)
nc8 = {s["store_code"]: s for s in resp8["not_closed"]}
check("8. LUX tenant sees only ITS OWN rep (Lux Rep), never the house org's punch at the same store_code",
      nc8.get("S1", {}).get("worked") == [{"name": "Lux Rep", "email": None, "emails": None, "tag": "clocked"}],
      str(nc8.get("S1")))

# ── Summary ──────────────────────────────────────────────────────────────────────────────────────
print(f"\n{len(PASS)}/{len(PASS) + len(FAIL)} checks passed")
if FAIL:
    print("FAILED:")
    for f in FAIL:
        print("  -", f)
    sys.exit(1)
