"""A PERIOD IS NEVER EMPTIED BY A COLUMN THE SCHEMA DOES NOT HAVE YET — the lock (index §4b.1/§19.19).

WHAT THIS REPRODUCES, exactly. On 2026-11-10 the store-DLAR sweep was taught to land 20 columns the
carrier portal had been sending for weeks (§COMMCALC-60). The migration that CREATES those columns is
applied by hand and had not been applied. At 12:00 UTC the sweep wrote November's 28 store rows; at
17:16 the change deployed; at 18:30 the sweep

    deleted the period's rows  ->  then tried to insert  ->  PGRST204: Could not find the
                                                             'activations_total' column of
                                                             'raw_dlar_store' in the schema cache

and November's store grain was gone. The read side of this platform has had a rule for exactly this
class since 2026-09-22 (`core.column_tolerant`, "a reader must tolerate ANY subset of its columns and
REPORT what is missing"). The WRITE side had no rule, and `safe_replace` — written in August 2026
for the delete-then-insert half of this same failure, after the April comp upload zeroed a month —
had never been wired to this sweep. The registry existed; the caller did not read it. §19.18.

TWO INVARIANTS, both locked here, because either alone still loses data:

  A. COLUMN. A declared column the table does not have is DROPPED from the row and NAMED in the
     run's evidence, decided BEFORE anything is written. The columns that DO exist still land.
  B. DELETE. No existing row is removed until the replacement has landed. A failed insert is a
     no-op, and the sweep says the period was left as it was.

PURE enough to run anywhere: the in-memory `harness_fakedb` double, which models PostgREST's two
real refusals — 42703 on a select naming an unknown column, PGRST204 on an insert naming one.
"""
import re
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from harness_fakedb import FakeClient, FakeTable, PgError          # noqa: E402
from app.core import column_tolerant as _ct                        # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  {extra}" if extra and not cond else ""))


ORG = "00000000-0000-0000-0000-000000000001"
SRC = pathlib.Path(__file__).resolve().parent / "app" / "modules" / "commcalc" / "dlar_sweep.py"
SWEEP = SRC.read_text()


def _table(cols, rows=()):
    """A fake commcalc table with `cols` and the rows already stored."""
    c = FakeClient()
    t = FakeTable(dict(cols))
    t.name = "raw_dlar_store"
    c.tables["raw_dlar_store"] = t
    # The prior load's `created_at` PREDATES the double's clock, which is how it is in production:
    # safe_replace tells the old load from the new by the database's own timestamp, and a fixture
    # that stamped the old rows LATER would make the swap look broken when it is the fixture that is.
    for r in rows:
        t.rows.append(dict(r, id=f"id{len(t.rows)}", created_at="2026-07-01T00:00:00+00:00"))
    return c, t


BASE_COLS = {"org_id": "text", "period": "text", "store_code": "text", "total_acts": "int"}
PRIOR = [{"org_id": ORG, "period": "November 2026", "store_code": f"B-{i}", "total_acts": i}
         for i in range(1, 29)]


# ── A. THE COLUMN RULE ──────────────────────────────────────────────────────────────────────────
print("== A. a column the table does not have is dropped and named, before any write ==")

client, tbl = _table(BASE_COLS, PRIOR)
scope = lambda q: q.eq("org_id", ORG)                                           # noqa: E731
table = lambda: client.schema("commcalc").table("raw_dlar_store")               # noqa: E731

declared = ["org_id", "period", "store_code", "total_acts", "activations_total", "byod_total"]
present = _ct.writable_columns(table, scope, declared)
check("A1 a declared column the table HAS is writable", "total_acts" in present)
check("A2 a declared column the table LACKS is not", "activations_total" not in present)
check("A3 and neither is the second absent one (one absence never hides another)",
      "byod_total" not in present)

rows = [{"org_id": ORG, "period": "November 2026", "store_code": "B-1",
         "total_acts": 7, "activations_total": 9, "byod_total": 3}]
out, absent = _ct.drop_absent(rows, present)
check("A4 the absent keys are stripped from the row", set(out[0]) == {
    "org_id", "period", "store_code", "total_acts"})
check("A5 the figures that DO exist still land", out[0]["total_acts"] == 7)
check("A6 and the absent columns are RETURNED, by name", absent == ["activations_total", "byod_total"])
check("A7 a dropped key is never written as None (absent != the source sent nothing)",
      "activations_total" not in out[0])

out2, absent2 = _ct.drop_absent(rows, frozenset(rows[0].keys()))
check("A8 byte-identical when nothing is absent — the SAME row objects, not copies",
      absent2 == [] and out2[0] is rows[0])

# an empty table still answers, because a tenant with no rows is the common case for a new feed
client_e, _ = _table(BASE_COLS)
present_e = _ct.writable_columns(lambda: client_e.schema("commcalc").table("raw_dlar_store"),
                                 scope, declared)
check("A9 an org with NO rows still gets a real answer (per-column probe)",
      "total_acts" in present_e and "activations_total" not in present_e)

check("A10 an unreadable table has no columns, so a caller drops everything rather than guessing",
      _ct.writable_columns(lambda: (_ for _ in ()).throw(PgError("gone")), scope, declared) == frozenset())


# ── B. THE DELETE RULE, on the real failure ─────────────────────────────────────────────────────
print("\n== B. the 2026-11-10 wipe, reproduced — and refused ==")

from app.modules.commcalc.safe_replace import safe_replace, ReplaceFailed   # noqa: E402

client2, tbl2 = _table(BASE_COLS, PRIOR)
period_scope = lambda q: q.eq("org_id", ORG).eq("period", "November 2026")   # noqa: E731

# THE DEFECT AS IT SHIPPED: send the unknown column and the whole insert is refused.
bad = [{"org_id": ORG, "period": "November 2026", "store_code": "B-1",
        "total_acts": 7, "activations_total": 9}]
try:
    safe_replace(client2, "raw_dlar_store", bad, period_scope, schema="commcalc")
    refused = False
except ReplaceFailed as e:
    refused, restored = True, e.restored
check("B1 an insert naming a column the table lacks IS refused (PGRST204 modelled)", refused)
check("B2 and the 28 rows that were there are STILL there — the wipe cannot happen",
      len([r for r in tbl2.rows if r["period"] == "November 2026"]) == 28)
check("B3 the exception says the previous data was preserved", refused and restored is True)

# THE FIX: probe first, drop the absent column, and the replace lands.
client3, tbl3 = _table(BASE_COLS, PRIOR)
good, dropped = _ct.drop_absent(
    [{"org_id": ORG, "period": "November 2026", "store_code": f"B-{i}", "total_acts": i * 2,
      "activations_total": i} for i in range(1, 29)],
    _ct.writable_columns(lambda: client3.schema("commcalc").table("raw_dlar_store"),
                         scope, ["org_id", "period", "store_code", "total_acts",
                                 "activations_total"]))
res = safe_replace(client3, "raw_dlar_store", good, period_scope, schema="commcalc")
nov = [r for r in tbl3.rows if r["period"] == "November 2026"]
check("B4 with the absent column dropped, the replace LANDS", res["saved"] == 28)
check("B5 the period holds exactly the new load, not both", len(nov) == 28)
check("B6 and the new figures are the new ones", sorted(r["total_acts"] for r in nov)[0] == 2)
check("B7 the dropped column is still reported, so nobody thinks it landed",
      dropped == ["activations_total"])

# a mid-insert failure for ANY other reason is also a no-op
client4, tbl4 = _table(BASE_COLS, PRIOR)
client4.fail_on_batch = 1
try:
    safe_replace(client4, "raw_dlar_store", [dict(r) for r in PRIOR], period_scope, schema="commcalc")
    anyfail = False
except ReplaceFailed:
    anyfail = True
check("B8 a mid-insert failure of any kind leaves the period untouched too", anyfail and len(
    [r for r in tbl4.rows if r["period"] == "November 2026"]) == 28)


# ── C. THE SWEEP DEREFERENCES BOTH — the un-wiring lock ─────────────────────────────────────────
print("\n== C. the sweep reads both homes, and cannot stop reading them ==")

check("C1 dlar_sweep imports safe_replace", "from app.modules.commcalc.safe_replace import" in SWEEP)
check("C2 dlar_sweep imports column_tolerant", re.search(r"from app\.core import column_tolerant", SWEEP))
check("C3 the write path calls safe_replace", "_safe_replace(" in SWEEP)
check("C4 the write path probes writable columns", "_ct.writable_columns(" in SWEEP)
check("C5 and drops what is absent", "_ct.drop_absent(" in SWEEP)

# THE ARMED NEGATIVE CONTROL: the shipped defect was a bare delete-then-insert of the period, and it
# must not be able to come back. The one remaining `.delete()` in this file belongs to nothing here.
_write_block = SWEEP[SWEEP.index("skipped, written, dropped_columns"):] if \
    "skipped, written, dropped_columns" in SWEEP else SWEEP
_block = _write_block[:_write_block.index("# DATA LANDED")] if "# DATA LANDED" in _write_block else _write_block
check("C6 the period write block no longer deletes the period itself (the shipped defect)",
      ".delete()" not in _block, extra=_block[:200])
check("C7 nor inserts directly (safe_replace owns the ordering)", ".insert(" not in _block)

# the probe must come BEFORE the write, or it has learned nothing in time
check("C8 the probe is ordered BEFORE the replace, not after",
      _block.index("_ct.writable_columns(") < _block.index("_safe_replace("))

# and the evidence must carry what was dropped, or a person is never told to apply the migration
check("C9 the run's evidence names the columns the table did not have",
      '"columns_not_yet_in_table"' in SWEEP)
check("C10 a run that could not land a declared column is PARTIAL, never ok",
      'res.get("columns_not_yet_in_table") or res.get("replace_warnings")' in SWEEP)
check("C11 and the status line says to apply the pending migration",
      "FIGURES ARRIVED THAT THIS DATABASE HAS NO COLUMN FOR" in SWEEP)


# ── D. the fake models the real refusals, or none of the above proves anything ──────────────────
# ── E. THE SIBLING THAT KEPT A PRIVATE COPY ─────────────────────────────────────────────────────
print("\n== E. the POS feed: the private column probe is gone, the ordering is fixed ==")

POS = (pathlib.Path(__file__).resolve().parent / "app" / "modules" / "pos"
       / "commcalc_feed.py").read_text()

check("E1 the POS feed reads the one home for which columns exist", "_ct.writable_columns(" in POS)
check("E2 and the one home for dropping what is absent", "_ct.drop_absent(" in POS)
check("E3 its own per-column probe is GONE (the §19.18 copy)",
      'table(table).select(col).limit(1)' not in POS)
check("E4 and so is the hard-coded one column it knew about", 'columns=("tax",)' not in POS)
check("E5 _replace_period no longer deletes before it inserts",
      re.search(r"def _replace_period\(.*?\n(?:.*?\n){0,24}", POS, re.S)
      and ".delete()" not in POS[POS.index("def _replace_period("):
                                  POS.index("def _ensure_registrations(")])
check("E6 it dereferences safe_replace instead", "_safe_replace(" in POS)

# WHAT IS NOT FIXED HERE, pinned so it is not forgotten rather than quietly carried. These three
# feeds still replace a scope delete-first; each is the same class and each is named in the PR with
# its line. This check FAILS THE BUILD if one of them silently grows a safe_replace call without the
# lock being extended to prove it — a half-wired sibling is how §19.18 recurs.
_KNOWN_UNWIRED = {
    "app/modules/commcalc/vip_sweep.py": "deletes invoice-id chunks, then inserts two tables",
    "app/modules/commcalc/b2b_sweep.py": "deletes (as_of_date, source), then inserts",
    "app/modules/commcalc/report_pull.py": "deletes a date window, then inserts",
}
_root = pathlib.Path(__file__).resolve().parent
for _rel, _why in sorted(_KNOWN_UNWIRED.items()):
    _txt = (_root / _rel).read_text()
    check(f"E7 {_rel.rsplit('/', 1)[-1]} is still on the old ordering, as the PR states ({_why})",
          "safe_replace" not in _txt,
          extra="it now calls safe_replace — extend this lock to prove the new path")


print("\n== D. the double's own fidelity (an armed check on the check) ==")

cd, td = _table(BASE_COLS)
try:
    cd.schema("commcalc").table("raw_dlar_store").insert(
        [{"org_id": ORG, "nope": 1}]).execute()
    modelled = False
except PgError as e:
    modelled = "PGRST204" in str(e)
check("D1 the double refuses an insert naming an unknown column, as PostgREST does", modelled)
try:
    cd.schema("commcalc").table("raw_dlar_store").select("nope").limit(1).execute()
    sel = False
except PgError as e:
    sel = "42703" in str(e)
check("D2 and refuses a select naming one (what the probe relies on)", sel)


print("\n" + "-" * 96)
if FAIL:
    print(f"RED — {len(FAIL)} check(s) failed:")
    for f in FAIL:
        print("  ·", f)
    sys.exit(1)
print(f"{len(PASS)} passed, 0 failed")
print("OK — a column the schema does not have yet costs a dropped figure and a named migration, "
      "never a period.")
