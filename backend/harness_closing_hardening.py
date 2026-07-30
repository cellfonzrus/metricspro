"""Offline proof harness for the closing-hardening package (2026-07-30 auto-fix pipeline dispatch,
triaged from the production failure_log backlog): two small backend-only guards, no gate/recon
formula change. No live DB/network — same stateful-fake-Supabase-client convention as
harness_dmverify_parity.py / harness_closing_submissions.py: runs the REAL `closing_rows` /
`closing_recon` functions, monkeypatching only `_b2b_day` (already independently proven elsewhere)
so this harness stays focused on THIS package's actual change surface.

Run: `cd backend && python3 harness_closing_hardening.py`

Proves:
  A. GET /closing/days (`closing_rows`) — a garbage `date`/`date_from`/`date_to` now raises a clean
     HTTPException(400) instead of an unhandled 500 from inside the Supabase-client date comparison;
     a well-formed date/date_from/date_to/store_code combination (any of the 4 params, alone or
     combined) is BYTE-IDENTICAL to the pre-fix filtering behavior; omitting every date param is
     unaffected (no validation branch fires at all).
  B. GET /closing/recon (`closing_recon`) fan-out cap — an IN-CAP period (<= _RECON_MAX_DATES
     distinct close_dates, i.e. every real calendar month) is BYTE-IDENTICAL to the pre-fix
     unbounded-per-date-_b2b_day algorithm (re-verified against an independent, un-refactored
     reimplementation of the OLD algorithm, not just re-running the new code against itself) and
     calls _b2b_day EXACTLY once per distinct date (same call volume as before — the cap doesn't
     change in-cap behavior, only bounds the ceiling). An OVER-CAP period (more distinct close_dates
     than _RECON_MAX_DATES) calls _b2b_day AT MOST _RECON_MAX_DATES times (never once per date), the
     capped-out dates' rows are NOT silently dropped from `errors` (guard response, not silent
     truncation — they land with `status="not_computed"`, distinct from the pre-existing
     `"recon_pending"` no-data case), and the response carries an explicit `recon_capped`/
     `dates_computed`/`dates_total` trio so a caller can tell the difference from a true empty month.
  C. Zero-write proof on both endpoints — a write-poisoned fake client (insert/update/delete all
     raise) never trips across a full run of every case above; both endpoints are pure reads.
  D. Money-safety: `_money_issues`/`_rep_b2b` are the REAL, unmocked functions throughout — block/
     flag detection for in-cap dates is unchanged; capped-out dates never reach `_money_issues` at
     all (no B2B fetched for them), so no block/flag can ever be fabricated for a date this request
     didn't examine.
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


# ── stateful fake supabase client (copied convention from harness_dmverify_parity.py) ──────────────
class Q:
    def __init__(self, store, table, poison_writes=False):
        self.s, self.t = store, table
        self.op, self.payload = "select", None
        self.filters = []
        self._limit = None
        self._poison = poison_writes

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
        if self._poison and self.op in ("insert", "update", "delete"):
            raise AssertionError(f"UNEXPECTED WRITE ({self.op}) on {self.t} — these endpoints are read-only")
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


def fresh_store():
    return {"daily_closing": [], "stores": []}


import app.modules.core.router as core            # noqa: E402
import app.modules.closing.router as cr            # noqa: E402

AUTH_NONE = ""


def wire(store, poison_writes=True):
    """poison_writes=True by default (item C, zero-write proof) — both endpoints under test are
    GET-only reads; a write attempted anywhere during this harness is a real bug, not a fixture gap."""
    fake = FakeClient(store, poison_writes=poison_writes)
    cr.sb = lambda: fake
    cr.get_supabase = lambda: fake
    core.get_supabase = lambda: fake
    return fake


def dc_row(**kw):
    # upgrade_count=1/new_line_count=1/postpaid_count=0 -> matches day_row()'s default
    # activations=1/upgrades=1 exactly (see count_config.STANDARD_DEFS: new_line+postpaid=
    # "activation", upgrade="upgrade") so section B's fixtures isolate the MONEY recon path under
    # test without also tripping the pre-existing, unrelated store-level count-mismatch recon.
    r = {"org_id": HOUSE, "close_date": "2026-07-15", "period": "2026-07",
         "store_code": "S1", "store_address": "1 Main St", "store_name": "1 Main St",
         "employee_name": "Jane Rep", "source": "manual",
         "t_cash": 100.0, "t_credit": 50.0, "t_ext_cc": 0.0, "t_gift": 0.0, "t_store_acct": 0.0,
         "t_zelle": 0.0, "t_acima": 0.0, "store_cash": 100.0, "store_cc": 50.0, "epay_cash": 0.0,
         "epay_cc": 0.0, "acc_sale": 25.0, "other_account": 0.0,
         "upgrade_count": 1, "new_line_count": 1, "postpaid_count": 0,
         "expense_amount": 0.0, "expense_description": None, "expense_approved": False,
         "envelope_picture": None, "remarks": "", "tenders": None, "counts": None}
    r.update(kw)
    return r


def day_row(cash=100.0, card=50.0, store="S1", rep="Jane Rep"):
    return {"has_data": True,
            "by_store": {store: {"cash": cash, "card": card, "other": 0.0, "acc_gross": 0.0, "total": cash + card,
                                  "tenders_available": True}},
            "by_rep": {(store, rep.lower()): {"cash": cash, "card": card, "other": 0.0, "acc_gross": 0.0,
                                               "total": cash + card, "salesperson": rep, "tenders_available": True}},
            "counts": {store: {"activations": 1, "upgrades": 1}}}


NO_DATA_DAY = {"has_data": False, "by_store": {}, "by_rep": {}, "counts": {}}


# ═══════════════════════════ A. GET /closing/days — date-validation guard ═══════════════════════════
st = fresh_store()
wire(st)
st["daily_closing"] = [
    dc_row(id="r1", store_code="S1", close_date="2026-07-15"),
    dc_row(id="r2", store_code="S2", close_date="2026-07-16", employee_name="Bob Rep"),
    dc_row(id="r3", store_code="S1", close_date="2026-07-20"),
]

for bad in ("not-a-date", "2026-13-99", "'; DROP TABLE daily_closing; --"):
    try:
        cr.closing_rows(date=bad, org_id=HOUSE)
        ok = False
    except Exception as e:
        ok = e.__class__.__name__ == "HTTPException" and getattr(e, "status_code", None) == 400
    check(f"A1. closing_rows(date={bad!r}) -> clean 400, not an unhandled exception", ok)

# An empty string is FALSY -> `if date:` never fires -> treated exactly like omitting the param
# entirely (no validation, no filtering) — not a 400, and not a crash either. Explicit regression.
try:
    out = cr.closing_rows(date="", org_id=HOUSE)
    ok = isinstance(out, list) and len(out) == 3
except Exception:
    ok = False
check("A1b. closing_rows(date='') -> falsy, same as omitted (no 400, all 3 rows) — not treated as garbage", ok)

for field in ("date_from", "date_to"):
    try:
        cr.closing_rows(org_id=HOUSE, **{field: "garbage-date"})
        ok = False
    except Exception as e:
        ok = e.__class__.__name__ == "HTTPException" and getattr(e, "status_code", None) == 400
    check(f"A2. closing_rows({field}='garbage-date') -> clean 400", ok)

# Well-formed single date — byte-identical filtering to the pre-fix (unvalidated) behavior.
out = cr.closing_rows(date="2026-07-15", org_id=HOUSE)
check("A3. good date= -> exactly the one matching row", [r["id"] for r in out] == ["r1"], str(out))

# Well-formed range. (Set-compared, not order-sensitive — the fake client's .order() is a no-op by
# convention across this harness family; ordering is an unrelated, untouched concern.)
out = cr.closing_rows(date_from="2026-07-16", date_to="2026-07-20", org_id=HOUSE)
check("A4. good date_from/date_to range -> exactly the 2 matching rows",
      {r["id"] for r in out} == {"r2", "r3"}, str([r["id"] for r in out]))

# store_code combined with a date.
out = cr.closing_rows(date="2026-07-15", store_code="S1", org_id=HOUSE)
check("A5. good date + store_code -> narrows correctly", [r["id"] for r in out] == ["r1"])

out = cr.closing_rows(date="2026-07-15", store_code="S9-nomatch", org_id=HOUSE)
check("A6. good date + non-matching store_code -> empty, no error", out == [])

# No date params at all — the validation branches never fire; unaffected (regression).
out = cr.closing_rows(org_id=HOUSE)
check("A7. no date params at all -> all 3 rows, unaffected by the new guard", len(out) == 3)

# A slightly loose-but-parseable date (dateparser is lenient) still normalizes and matches.
out = cr.closing_rows(date="July 15 2026", org_id=HOUSE)
check("A8. a loosely-formatted but real date still parses + matches (dateparser, same as the sibling endpoints)",
      [r["id"] for r in out] == ["r1"], str(out))


# ═══════════════════ B. GET /closing/recon — fan-out cap + day-cache (in-cap byte-identity) ═══════════
def old_algorithm_recon(closing_rows_list, store_meta, day_by_date, market=None, tolerance=1.0):
    """Independent reimplementation of the PRE-FIX algorithm (unbounded, calls day_by_date[date] for
    literally every distinct date, no cap) — used as an oracle to prove the NEW code's in-cap output
    is byte-identical, not just self-consistent with its own refactor."""
    by_date = {}
    for r in closing_rows_list:
        by_date.setdefault(r.get("close_date"), []).append(r)
    errors = []
    blocks = flags = pending = 0
    for date in sorted((d for d in by_date if d), reverse=True):
        day = day_by_date[date]
        store_groups = {}
        for r in by_date[date]:
            store_groups.setdefault(r.get("store_code") or f"name:{r.get('store_name') or '—'}", []).append(r)
        for key, reps in store_groups.items():
            code = None if str(key).startswith("name:") else key
            meta = store_meta.get(code, {}) if code else {}
            if market and (meta.get("market") or "") != market:
                continue
            addr = meta.get("address")
            for r in reps:
                emp = (r.get("employee_name") or "").strip()
                dcash = cr._f(r.get("store_cash")) + cr._f(r.get("epay_cash"))
                dcred = cr._f(r.get("store_cc")) + cr._f(r.get("epay_cc"))
                repb = cr._rep_b2b(day, code, emp) if (code and day["has_data"]) else None
                if repb is None:
                    pending += 1
                    errors.append({"date": date, "store_code": code, "rep": emp or "—", "status": "recon_pending"})
                    continue
                for it in cr._money_issues(dcash, dcred, repb["cash"], repb["card"], tolerance):
                    blocks += it["severity"] == "block"; flags += it["severity"] == "flag"
                    errors.append({"date": date, "store_code": code, "rep": emp or "—", "status": it["severity"]})
    return {"errors": errors, "summary": {"blocks": blocks, "flags": flags, "pending": pending, "total": len(errors)}}


# ── B1: IN-CAP period (a normal month — 10 distinct dates, well under _RECON_MAX_DATES) ────────────
st = fresh_store(); wire(st)
st["stores"] = [{"org_id": HOUSE, "store_code": "S1", "address": "1 Main St", "market": "Texas"}]
in_cap_dates = [f"2026-07-{d:02d}" for d in range(1, 11)]  # 10 dates
rows = []
day_by_date = {}
for i, d in enumerate(in_cap_dates):
    rows.append(dc_row(id=f"r{i}", close_date=d, store_cash=100.0 + i, store_cc=50.0))
    # alternate: some days short cash (block), some clean (ok) — real mixed-severity data
    day_by_date[d] = day_row(cash=100.0 + i if i % 3 else 40.0, card=50.0)
st["daily_closing"] = rows
call_log = []
_real_b2b_day = day_by_date
cr._b2b_day = lambda client, org_id, date: (call_log.append(date), _real_b2b_day[date])[1]

resp = cr.closing_recon(period="2026-07", org_id=HOUSE)
check("B1a. in-cap: recon_capped is False", resp["recon_capped"] is False)
check("B1b. in-cap: dates_computed == dates_total == 10", resp["dates_computed"] == 10 and resp["dates_total"] == 10,
      str((resp["dates_computed"], resp["dates_total"])))
check("B1c. in-cap: _b2b_day called EXACTLY once per distinct date (10), not more",
      len(call_log) == 10, str(len(call_log)))
check("B1d. in-cap: no 'not_computed' status anywhere (nothing was capped out)",
      not any(e.get("status") == "not_computed" for e in resp["errors"]))

oracle = old_algorithm_recon(rows, {"S1": {"address": "1 Main St", "market": "Texas"}}, day_by_date)
new_shape = [{"date": e["date"], "store_code": e["store_code"], "rep": e["rep"], "status": e["status"]}
             for e in resp["errors"]]
check("B1e. in-cap output is BYTE-IDENTICAL to the independent old-algorithm oracle (summary)",
      resp["summary"] == oracle["summary"], str((resp["summary"], oracle["summary"])))
check("B1f. in-cap output is BYTE-IDENTICAL to the oracle (per-row date/store/rep/status, order incl.)",
      new_shape == oracle["errors"], str((new_shape, oracle["errors"])))

# ── B2: OVER-CAP period (60 distinct dates > _RECON_MAX_DATES) ─────────────────────────────────────
st = fresh_store(); wire(st)
st["stores"] = [{"org_id": HOUSE, "store_code": "S1", "address": "1 Main St", "market": "Texas"}]
N = cr._RECON_MAX_DATES + 15
over_dates = sorted({f"2026-{(1 + i // 28):02d}-{1 + (i % 28):02d}" for i in range(N)})
assert len(over_dates) == N, "date-generation collision — fixture bug, not the code under test"
rows = [dc_row(id=f"o{i}", close_date=d, store_cash=200.0, store_cc=50.0) for i, d in enumerate(over_dates)]
st["daily_closing"] = rows
call_log = []
day_by_date = {d: day_row(cash=999.0, card=999.0) for d in over_dates}   # if queried, would ALWAYS be a cash-block
cr._b2b_day = lambda client, org_id, date: (call_log.append(date), day_by_date[date])[1]

resp = cr.closing_recon(period="2026-07", org_id=HOUSE)
check("B2a. over-cap: recon_capped is True", resp["recon_capped"] is True)
check("B2b. over-cap: dates_computed == _RECON_MAX_DATES", resp["dates_computed"] == cr._RECON_MAX_DATES,
      str(resp["dates_computed"]))
check("B2c. over-cap: dates_total == the real distinct-date count (N)", resp["dates_total"] == N, str(resp["dates_total"]))
check("B2d. over-cap: _b2b_day called AT MOST _RECON_MAX_DATES times, never once-per-date",
      len(call_log) == cr._RECON_MAX_DATES, str(len(call_log)))

most_recent = sorted(over_dates, reverse=True)[:cr._RECON_MAX_DATES]
oldest_capped = sorted(over_dates, reverse=True)[cr._RECON_MAX_DATES:]
check("B2e. over-cap: the MOST RECENT dates are the ones actually queried (prioritize-recent, same as summary/submissions)",
      set(call_log) == set(most_recent), str(set(call_log) ^ set(most_recent)))

by_date_status = {}
for e in resp["errors"]:
    by_date_status.setdefault(e["date"], set()).add(e.get("status"))
check("B2f. GUARD RESPONSE not silent truncation: every capped-out date still has a row in `errors`",
      all(d in by_date_status for d in oldest_capped), str([d for d in oldest_capped if d not in by_date_status]))
check("B2g. every capped-out date's row is explicitly status='not_computed' (never 'ok'/'block'/'flag' — those "
      "dates were NEVER examined, so no gate outcome can be fabricated for them)",
      all(by_date_status[d] == {"not_computed"} for d in oldest_capped),
      str({d: by_date_status[d] for d in oldest_capped if by_date_status[d] != {"not_computed"}}))
check("B2h. a computed (in-cap) date still gets REAL block detection ($999 B2B cash vs $200 declared -> cash short -> block)",
      any(e["date"] in most_recent and e.get("severity") == "block" for e in resp["errors"]))
check("B2i. summary.blocks only counts the dates that were ACTUALLY computed (each forces 1 cash-short block)",
      resp["summary"]["blocks"] == len(most_recent), str(resp["summary"]["blocks"]))
check("B2j. NOTHING from the never-queried dates leaked into blocks/flags — exactly 2 real money issues "
      "per COMPUTED date (1 block + 1 credit-under flag), no fabricated gate outcome for the capped-out dates",
      resp["summary"]["blocks"] + resp["summary"]["flags"] == 2 * len(most_recent),
      str((resp["summary"]["blocks"], resp["summary"]["flags"], len(most_recent))))


# ═══════════════════ C. Zero-write proof (both endpoints, across everything above) ═══════════════════
# Implicit across every case above (wire() poisons inserts/update/delete by default; any accidental
# write anywhere in A/B would already have raised and failed this harness's run). Explicit re-check:
st = fresh_store(); wire(st, poison_writes=True)
st["daily_closing"] = [dc_row(id="rw1")]
st["stores"] = [{"org_id": HOUSE, "store_code": "S1", "address": "1 Main St", "market": "Texas"}]
cr._b2b_day = lambda client, org_id, date: NO_DATA_DAY
try:
    cr.closing_rows(date="2026-07-15", org_id=HOUSE)
    cr.closing_recon(period="2026-07", org_id=HOUSE)
    zero_write_ok = True
except AssertionError:
    zero_write_ok = False
check("C1. both endpoints complete a full call with the write-poisoned fake client raising nothing "
      "(closing_rows + closing_recon are pure reads)", zero_write_ok)


print(f"\n{len(PASS)}/{len(PASS) + len(FAIL)} checks passed")
if FAIL:
    print("FAILED:", FAIL)
    sys.exit(1)
