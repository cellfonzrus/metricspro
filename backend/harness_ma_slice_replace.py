"""HARNESS — DATE_KEYED per-day replace must be ACCOUNT-SLICE scoped (incident 2026-09-02).

THE INCIDENT. One org (854f6d7b-…) feeds commcalc.raw_ma_commission from TWO master-agent portals:
LuxeLink=VidaPay (Chicago) and Novawave/Total-Access (NY/NJ). The manual /upload/{file_type} path
did delete-then-insert PER (org, day) for the DATE_KEYED feeds, so uploading the Novawave August
MA Commission file WIPED the VidaPay bridge rows for every day the file contained:
raw_ma_commission August 824 → 364 rows, and 750+ devices went un-paid in the reconciliation.

THE FIX (pure, app/modules/commcalc/ingest_slice.py — day_replace_filters/apply_filters, used by
router.py's DATE_KEYED branch): the replace narrows to (org, day-set, account-set) when every
incoming row proves its account (merchant_account_id / account_id / tspid); a file that cannot
prove its slice falls back to the legacy (org, day-set) whole-day replace — a REAL delete either
way, never a silent no-delete that would double-count on re-upload.

Pure stdlib: imports ONLY ingest_slice (no pandas / fastapi / supabase / DB). The fake query
builder applies eq/in_ for real, so a filter that does not narrow FAILS the test instead of
silently passing. §2 is an ARMED negative control: it runs the PRE-FIX (org, day) delete and
asserts the wipe HAPPENS — proving this harness detects the bug the fix removes.
"""
import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
from app.modules.commcalc import ingest_slice  # noqa: E402  (stdlib-only module)

PASS, FAIL = [], []


def ok(cond, what):
    (PASS if cond else FAIL).append(what)
    print(("  PASS " if cond else "  FAIL ") + what)


# ── fake supabase-style store: eq/in_ filters applied for real ─────────────────────────────────────
class _Q:
    def __init__(self, rows_ref, op):
        self.rows_ref, self.op, self.f = rows_ref, op, []

    def eq(self, c, v):
        self.f.append(("eq", c, v)); return self

    def in_(self, c, v):
        self.f.append(("in", c, [str(x) for x in v])); return self

    def _match(self, r):
        for kind, c, v in self.f:
            if kind == "eq" and r.get(c) != v:
                return False
            if kind == "in" and str(r.get(c)) not in v:
                return False
        return True

    def execute(self):
        rows = self.rows_ref["rows"]
        if self.op == "delete":
            keep = [r for r in rows if not self._match(r)]
            self.rows_ref["deleted"] = self.rows_ref.get("deleted", 0) + (len(rows) - len(keep))
            self.rows_ref["rows"] = keep
            return types.SimpleNamespace(data=[], count=None)
        hit = [r for r in rows if self._match(r)]
        return types.SimpleNamespace(data=[dict(r) for r in hit], count=len(hit))


class Table:
    """One raw table. upload() runs the production DATE_KEYED sequence: derive filters from the
    file, delete (org ∩ filters), insert — the delete-then-insert order collapses safe_replace's
    insert-first choreography, which is orthogonal to WHAT the scope covers (that module has its
    own proof); the SCOPE is exactly what the router hands safe_replace."""

    def __init__(self, name, day_col):
        self.name, self.day_col, self.store = name, day_col, {"rows": [], "deleted": 0}

    def seed(self, rows):
        self.store["rows"].extend(dict(r) for r in rows)

    def count(self, **where):
        return sum(1 for r in self.store["rows"] if all(r.get(k) == v for k, v in where.items()))

    def upload(self, org_id, mapped):
        feed_dates = sorted({m.get(self.day_col) for m in mapped if m.get(self.day_col)})
        filters, slice_ = ingest_slice.day_replace_filters(self.name, mapped, self.day_col, feed_dates)
        d = _Q(self.store, "delete").eq("org_id", org_id)
        ingest_slice.apply_filters(d, filters).execute()
        self.store["rows"].extend(dict(m) for m in mapped)
        return slice_

    def upload_prefix_org_day(self, org_id, mapped):
        """THE PRE-FIX BEHAVIOUR, verbatim scope: delete (org, day-set) with no account slice."""
        feed_dates = sorted({m.get(self.day_col) for m in mapped if m.get(self.day_col)})
        _Q(self.store, "delete").eq("org_id", org_id).in_(self.day_col, feed_dates).execute()
        self.store["rows"].extend(dict(m) for m in mapped)


ORG = "854f6d7b-6590-4e4d-88ab-646f560d4f4c"
OTHER_ORG = "00000000-0000-0000-0000-000000000001"
VIDA, NOVA = "170084", "168874"          # VidaPay (Chicago) vs Novawave (NY/NJ) merchant accounts


def comm(acct, day, org=ORG, amt="25.0"):
    return {"org_id": org, "merchant_account_id": acct, "tx_date": day,
            "period": "August 2026", "device_margin": amt}


def aug(day):
    return "2026-08-%02d" % day


def vida_month(rows_per_day=2):
    return [comm(VIDA, aug(d)) for d in range(1, 29) for _ in range(rows_per_day)]


print("\n§1 · THE INCIDENT, FIXED — Novawave's August file must not delete VidaPay's August rows")
t = Table("raw_ma_commission", "tx_date")
t.seed(vida_month())                       # VidaPay bridge rows already ingested
before_vida = t.count(merchant_account_id=VIDA)
nova_file = [comm(NOVA, aug(d)) for d in range(1, 29)]
slice_ = t.upload(ORG, nova_file)
ok(slice_ is not None and slice_["partition_col"] == "merchant_account_id",
   "the file proves its slice on merchant_account_id (the real raw_ma_commission column)")
ok(t.count(merchant_account_id=VIDA) == before_vida,
   f"VidaPay's {before_vida} August rows SURVIVE the Novawave upload "
   f"(got {t.count(merchant_account_id=VIDA)})")
ok(t.count(merchant_account_id=NOVA) == 28, "Novawave's own slice landed (28 rows)")

print("\n§2 · ARMED NEGATIVE CONTROL — the PRE-FIX (org, day) delete DOES wipe the other portal")
t2 = Table("raw_ma_commission", "tx_date")
t2.seed(vida_month())                      # 56 rows ≈ the Aug 824 that shrank to 364
before = len(t2.store["rows"])
t2.upload_prefix_org_day(ORG, [comm(NOVA, aug(d)) for d in range(1, 29)])
ok(t2.count(merchant_account_id=VIDA) == 0 and before > 0,
   "pre-fix behaviour reproduces the wipe (VidaPay 56 → 0) — this harness would catch a regression")

print("\n§3 · RE-UPLOAD replaces ONLY its own slice, idempotently (guard 3)")
t3 = Table("raw_ma_commission", "tx_date")
t3.upload(ORG, [comm(VIDA, aug(d)) for d in (1, 2, 3)])
t3.upload(ORG, [comm(NOVA, aug(d)) for d in (1, 2, 3)])
n1 = len(t3.store["rows"])
t3.upload(ORG, [comm(NOVA, aug(d)) for d in (1, 2, 3)])       # identical Nova file again
n2 = len(t3.store["rows"])
ok(n1 == 6 and n2 == 6, f"re-uploading the identical Nova file ⇒ still 6 rows, no duplicates "
                        f"(got {n1} then {n2})")
ok(t3.count(merchant_account_id=VIDA) == 3, "VidaPay untouched by the Nova re-upload")
# corrected re-upload at (account, day) grain: a one-day Nova correction replaces ONLY that
# (account, day) cell — its other days AND the other portal stay exactly as loaded.
t3.upload(ORG, [comm(NOVA, aug(2)), comm(NOVA, aug(2))])       # Aug 2 corrected: 1 row → 2 rows
ok(t3.count(merchant_account_id=NOVA, tx_date=aug(2)) == 2
   and t3.count(merchant_account_id=NOVA) == 4 and t3.count(merchant_account_id=VIDA) == 3,
   "a one-day Nova correction replaces only (Nova, Aug 2); Nova Aug 1/3 and all VidaPay survive")

print("\n§4 · SAME ACCOUNT IN TWO FILES never double-counts (guard 3, second half)")
t4 = Table("raw_ma_commission", "tx_date")
t4.upload(ORG, [comm(VIDA, aug(5)), comm(NOVA, aug(5))])       # combined export
t4.upload(ORG, [comm(VIDA, aug(5)), comm(VIDA, aug(5))])       # VidaPay-only re-pull, 2 rows
ok(t4.count(merchant_account_id=VIDA) == 2 and t4.count(merchant_account_id=NOVA) == 1,
   "VidaPay's Aug-5 slice replaced (2 rows, not 3) while Novawave's Aug-5 row survives")

print("\n§5 · NO ACCOUNT VALUES ⇒ fall back to the whole-day replace — never a silent no-delete")
t5 = Table("raw_ma_commission", "tx_date")
t5.seed([comm(VIDA, aug(1)), comm(NOVA, aug(1))])
legacy_file = [dict(comm("x", aug(1)), merchant_account_id=None) for _ in range(3)]
slice5 = t5.upload(ORG, legacy_file)
ok(slice5 is None, "a file with no account values proves no slice (scope None)")
ok(len(t5.store["rows"]) == 3 and t5.store["deleted"] == 2,
   f"fallback DELETED the whole day (2 prior rows) before inserting 3 — re-upload cannot "
   f"double-count (rows now {len(t5.store['rows'])})")
t5b = Table("raw_ma_commission", "tx_date")
t5b.seed([comm(VIDA, aug(1))])
mixed = [comm(VIDA, aug(1)), dict(comm(VIDA, aug(1)), merchant_account_id="")]
ok(t5b.upload(ORG, mixed) is None,
   "ONE blank account value ⇒ whole-day fallback too (a partial slice would strand the blank rows)")

print("\n§6 · TENANT ISOLATION — another org's same-day rows are never touched")
t6 = Table("raw_ma_commission", "tx_date")
t6.seed([comm(NOVA, aug(1), org=OTHER_ORG)])
t6.upload(ORG, [comm(NOVA, aug(1))])
ok(t6.count(org_id=OTHER_ORG) == 1, "the other org's row survives an identical-account upload")

print("\n§7 · ma_daily_tx (account_id) and ma_fulfillment (tspid) carry the same protection")
f7, s7 = ingest_slice.day_replace_filters(
    "raw_ma_daily_tx",
    [{"org_id": ORG, "account_id": VIDA, "tx_date": aug(9)}], "tx_date", [aug(9)])
ok(s7 is not None and s7["partition_col"] == "account_id", "raw_ma_daily_tx slices on account_id")
f7b, s7b = ingest_slice.day_replace_filters(
    "raw_ma_fulfillment",
    [{"org_id": ORG, "tspid": "88123", "date_ordered": aug(9)}], "date_ordered", [aug(9)])
ok(s7b is not None and s7b["partition_col"] == "tspid", "raw_ma_fulfillment slices on tspid")

print("\n§8 · daily_sales stays UNTOUCHED — single-source feed keeps (org, day) byte-identical")
f8, s8 = ingest_slice.day_replace_filters(
    "daily_sales_feed",
    [{"org_id": ORG, "store": "957 Pennsylvania Avenue", "trans_date": aug(9)}],
    "trans_date", [aug(9)])
ok(s8 is None and f8 == [("in", "trans_date", [aug(9)])],
   "daily_sales_feed derives NO slice — the delete filter is exactly the legacy day-set")

print("\n§9 · the delete filter NARROWS for real (fake builder applies every filter)")
t9 = Table("raw_ma_commission", "tx_date")
t9.seed([comm(VIDA, aug(1)), comm(VIDA, aug(2)), comm(NOVA, aug(1))])
t9.upload(ORG, [comm(VIDA, aug(1))])
ok(t9.count(merchant_account_id=VIDA, tx_date=aug(2)) == 1,
   "the SAME account's row on a day OUTSIDE the file also survives (day-set is part of the scope)")

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
for f_ in FAIL:
    print("  ✗ " + f_)
sys.exit(1 if FAIL else 0)
