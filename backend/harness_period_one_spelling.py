#!/usr/bin/env python3
"""ONE MONTH, ONE STORED SPELLING — proof + un-wiring lock (owner report 2026-10-06, index §19.47).

Owner, comparing two surfaces: *"first check the gross profit report vs the account dashboard the
data on those dont match so clearly there are differnt sources of data and likely they are bing
doubled also"*. He was right about the doubling, in a place neither report shows.

THE DEFECT, measured live on the house org 2026-10-06. Every period-keyed cache keys on
`(org_id, period)` where `period` is whatever STRING the caller happened to hold, and the platform
stores a month under two spellings ('September 2026' and '2026-09'). So one month reached a cache
twice and the two copies disagreed:

    commcalc.gp_snapshot    'September 2026'  net profit $201,289.22   computed 2026-10-06
                            '2026-09'         net profit $194,724.22   computed 2026-10-04
                            'October 2026'    net profit  $45,589.14
                            '2026-10'         net profit  $44,799.87
    commcalc.account_statements   'May 2026' + '2026-05',  'June 2026' + '2026-06'

`_tperiods` de-duped the raw STRING, so September and October each took TWO slots of the trend's
six-month window — the same month, twice, with two different answers, and two real months pushed
off the end. The statements purge was `.eq("period", period)`, so computing '2026-09' left the
'September 2026' snapshot of the same month standing and the dashboard held two vintages at once.

THE CLASS, not the instance. The general fact that was wrong is not "the trend shows September
twice"; it is **"a month was stored under whatever spelling the caller held"**. That question already
had ONE home — `account/_period.canonical_period`, which `is_canonical_period` documents as exactly
this class ("a stored spelling that is NOT canonical is an ORPHAN no period_keys() reader finds") —
and the writers did not read it. `commcalc.router._canon_period` was a SECOND COPY of the rule.

DB-FREE and stdlib: the real `_tperiods` / `_gp_snapshot_period` are extracted from the router by
AST and exercised directly; nothing imports FastAPI, nothing touches a database.
"""
import ast
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from app.modules.core.module_graph import home_under_app  # noqa: E402

ROUTER = "app/modules/commcalc/router.py"
ENGINE = "app/modules/account/engine.py"
STMT = "app/modules/account/statement_engine.py"
# The graph is the one home for the ruling — never a literal path of our own (index §19.18).
HOME = "app/" + home_under_app("period_stored_spelling")

ok = fail = 0


def check(label, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  ok   {label}")
    else:
        fail += 1
        print(f"  FAIL {label}" + (f" — {detail}" if detail else ""))


def src(rel):
    with open(os.path.join(ROOT, rel)) as f:
        return f.read()


def func_src(rel, name):
    """The real source of one top-level function, by AST — never a copy of it."""
    text = src(rel)
    for node in ast.parse(text).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(text, node)
    return None


# Stand-ins used ONLY when a function this lock requires has been deleted — they reproduce the
# PRE-FIX behaviour, so the checks below then fail by name instead of the harness crashing. A lock
# must say what is missing.
_MISSING_STANDIN = {
    "_gp_snapshot_period": "def _gp_snapshot_period(period):\n    return str(period or '').strip()\n",
    "_tperiods": ("def _tperiods(periods_present, months):\n"
                  "    kept = sorted({(p or '').strip() for p in periods_present if p},\n"
                  "                  key=lambda p: (parse_period(p)['year'], parse_period(p)['month']))\n"
                  "    return kept[-months:] if months and months > 0 else kept\n"),
}


def load_real(names):
    """Exec the REAL functions in a sandbox carrying only what they legitimately need."""
    from app.modules.commcalc.calculator import parse_period
    g = {"parse_period": parse_period}
    for n in names:
        s = func_src(ROUTER, n)
        check(f"C0 {n} exists in {ROUTER}", bool(s), "the lock's subject has been deleted")
        exec(s or _MISSING_STANDIN[n], g)
    return g


# ═══ A. THE REGRESSION — the live duplicate, reproduced and then fixed ════════════════════════
print("\nA. the live duplicate: one month under two spellings")
G = load_real(["_gp_snapshot_period", "_tperiods"])
_tperiods, _canon = G["_tperiods"], G["_gp_snapshot_period"]

# exactly what the house org's gp_snapshot held
LIVE = [
    {"period": "2026-09", "computed_at": "2026-10-04T12:42:31Z", "net_profit": 194724.22},
    {"period": "September 2026", "computed_at": "2026-10-06T00:52:45Z", "net_profit": 201289.22},
    {"period": "October 2026", "computed_at": "2026-10-05T21:13:16Z", "net_profit": 45589.14},
    {"period": "2026-10", "computed_at": "2026-10-04T12:42:28Z", "net_profit": 44799.87},
    {"period": "August 2026", "computed_at": "2026-10-06T01:08:38Z", "net_profit": 266148.46},
    {"period": "July 2026", "computed_at": "2026-10-03T20:35:56Z", "net_profit": 310000.00},
]
present = [r["period"] for r in LIVE]

# the OLD rule, stated here only to prove it was wrong (a de-dupe on the raw string)
old_kept = sorted({p.strip() for p in present},
                  key=lambda p: (parse_period := None) or 0)  # order irrelevant to the count
check("A1 old rule: 6 raw spellings for 4 months — two slots wasted",
      len({p.strip() for p in present}) == 6, f"{len(set(present))}")
check("A2 old rule: September present twice",
      sum(1 for p in present if _canon(p) == "September 2026") == 2)

kept = _tperiods(present, 6)
check("A3 fixed: six raw spellings collapse to FOUR months", len(kept) == 4, f"{kept}")
check("A4 fixed: every kept entry is the canonical spelling",
      all(_canon(p) == p for p in kept), f"{kept}")
check("A5 fixed: September appears exactly once",
      sum(1 for p in kept if _canon(p) == "September 2026") == 1, f"{kept}")
check("A6 fixed: October appears exactly once",
      sum(1 for p in kept if _canon(p) == "October 2026") == 1, f"{kept}")
check("A7 chronological, oldest first",
      kept == ["July 2026", "August 2026", "September 2026", "October 2026"], f"{kept}")

# the reader's fold: newest row wins per canonical month (the stale copy is what disagreed)
folded, at = {}, {}
for r in LIVE:
    k = _canon(r["period"])
    a = str(r.get("computed_at") or "")
    if k in folded and a <= at.get(k, ""):
        continue
    folded[k], at[k] = r, a
check("A8 fold keeps ONE row per month", len(folded) == 4, f"{sorted(folded)}")
check("A9 the NEWER September wins ($201,289.22, not $194,724.22)",
      folded["September 2026"]["net_profit"] == 201289.22)
check("A10 the NEWER October wins ($45,589.14, not $44,799.87)",
      folded["October 2026"]["net_profit"] == 45589.14)

# A window of 2 months must now show the two NEWEST months, not one month twice
kept2 = _tperiods(present, 2)
check("A11 a 2-month window shows two DIFFERENT months",
      kept2 == ["September 2026", "October 2026"], f"{kept2}")


# ═══ B. THE ONE HOME — dereferenced, never re-derived ════════════════════════════════════════
print("\nB. the one home is dereferenced")
from app.modules.account._period import canonical_period, is_canonical_period, period_keys

SPELLINGS = ["September 2026", "2026-09", "sept 2026", "Sep 2026", "2026-9", "SEPTEMBER 2026"]
check("B1 every spelling of one month canonicalises identically",
      len({canonical_period(s) for s in SPELLINGS}) == 1,
      f"{ {s: canonical_period(s) for s in SPELLINGS} }")
check("B2 the canonical spelling is the month-name form",
      canonical_period("2026-09") == "September 2026")
check("B3 the router's helper IS the one home, for every spelling",
      all(_canon(s) == canonical_period(s) for s in SPELLINGS))
check("B4 a non-period passes through unchanged, never invented",
      _canon("not a month") == "not a month" and _canon("") == "")
check("B5 the canonical spelling is stable under a second pass",
      all(_canon(_canon(s)) == _canon(s) for s in SPELLINGS))
check("B6 is_canonical_period agrees about the orphan spellings",
      is_canonical_period("September 2026") and not is_canonical_period("2026-09"))

# the OLD private rule was blind to abbreviations — the gap that made it a divergent copy
import calendar as _cal


def old_canon(period):
    """The deleted body of router._canon_period, kept ONLY to prove the collapse was a widening."""
    s = (period or "").strip()
    if len(s) >= 7 and s[:4].isdigit() and s[4] == "-":
        try:
            mo, yr = int(s[5:7]), int(s[:4])
        except Exception:
            mo, yr = 0, 0
    else:
        from app.modules.commcalc.calculator import parse_period as pp
        pm = pp(s)
        mo, yr = pm.get("month", 0), pm.get("year", 0)
    return f"{_cal.month_name[mo]} {yr}" if (1 <= mo <= 12 and yr) else s


check("B7 the collapse changed no answer the old copy got right",
      all(_canon(s) == old_canon(s) for s in
          ["September 2026", "2026-09", "July 2026", "2026-07", "December 2026"]))
check("B8 and it FIXED the abbreviations the old copy orphaned",
      _canon("Sept 2026") == "September 2026" and old_canon("Sept 2026") != "September 2026",
      f"old={old_canon('Sept 2026')!r} new={_canon('Sept 2026')!r}")


# ═══ C. THE UN-WIRING LOCK — no writer may key on a raw period again ═════════════════════════
print("\nC. the lock: a period-keyed write cannot go back to the raw string")
rt = src(ROUTER)
tree = ast.parse(rt)


def assigned_dict_keys_raw(text, fn_name):
    """True when `fn_name` builds a dict literal keyed on a bare `r['period']` (the old reader)."""
    for node in ast.parse(text).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == fn_name:
            for sub in ast.walk(node):
                if isinstance(sub, ast.DictComp):
                    k = sub.key
                    if (isinstance(k, ast.Subscript) and isinstance(k.slice, ast.Constant)
                            and k.slice.value == "period"):
                        return True
    return False


check("C1 gp_trend no longer keys its cache dict on the raw r['period']",
      not assigned_dict_keys_raw(rt, "gp_trend"))
check("C2 gp_trend folds through the canonical helper",
      "_gp_snapshot_period(r['period'])" in func_src(ROUTER, "gp_trend"))
check("C3 gp_trend compares computed_at so the NEWER copy wins",
      "computed_at" in func_src(ROUTER, "gp_trend"))
check("C4 the snapshot WRITER stores the canonical spelling",
      "_gp_snapshot_period(period)" in func_src(ROUTER, "_write_gp_snapshot"))
check("C5 _tperiods de-dupes on the canonical spelling, not the raw string",
      "_gp_snapshot_period(p)" in func_src(ROUTER, "_tperiods"))
check("C6 _canon_period no longer re-derives the month name itself",
      "month_name" not in (func_src(ROUTER, "_canon_period") or ""),
      "a second copy of the rule is back")
check("C7 _canon_period dereferences the one home",
      "canonical_period" in (func_src(ROUTER, "_canon_period") or ""))
check("C8 payout_config saves under the canonical spelling",
      "_canon_period(period)" in (func_src(ROUTER, "save_config") or ""))

eng, stm = src(ENGINE), src(STMT)
check("C9 the statements purge covers EVERY spelling (engine)",
      'in_("period", list(_period.period_keys(period)))' in eng,
      "a .eq purge leaves the other spelling's snapshot standing")
check("C10 the statements purge covers EVERY spelling (statement_engine)",
      'in_("period", list(_period.period_keys(period)))' in stm)
check("C11 neither engine purges or reads with a bare .eq on period",
      '.eq("period", period).execute()' not in eng and '.eq("period", period).execute()' not in stm,
      "a one-spelling filter is the whole defect")
check("C12 _persist stores the canonical spelling",
      "_period.canonical_period(period)" in (func_src(ENGINE, "_persist") or ""))


# ═══ D. OMISSION DOES NOT COMPILE — a planted regression must be caught ══════════════════════
print("\nD. negative controls (each planted defect is detected)")
check("D1 a raw-string de-dupe would keep six entries for four months",
      len({p.strip() for p in present}) != len(_tperiods(present, 6)))
broken = [r for r in LIVE if r["period"] != "September 2026"]  # only the STALE September survives
f2, a2 = {}, {}
for r in broken:
    k = _canon(r["period"])
    a = str(r["computed_at"])
    if k in f2 and a <= a2.get(k, ""):
        continue
    f2[k], a2[k] = r, a
check("D2 with the fresh copy gone the fold reports the stale figure — so it is really reading it",
      (f2.get("September 2026") or {}).get("net_profit") == 194724.22,
      f"{sorted(f2)}")
check("D3 a cache written under two spellings yields ONE row after the fix",
      len({_canon(r["period"]) for r in LIVE}) == 4)


# ═══ E. PURE AND RULE TWO ════════════════════════════════════════════════════════════════════
print("\nE. purity and RULE TWO")
home = src(HOME).lower()
BRANDS = ("boost", "luxelink", "cellfonz", "vzone", "nova wave", "total wireless", "epay portal")
check("E1 the one home names no carrier, tenant or product",
      not any(b in home for b in BRANDS),
      f"{[b for b in BRANDS if b in home]}")
check("E2 the one home imports nothing but stdlib",
      "import " not in src(HOME).split('"""')[2] if src(HOME).count('"""') >= 2 else True)
check("E3 _gp_snapshot_period reads no clock and no client",
      not any(t in (func_src(ROUTER, "_gp_snapshot_period") or "")
              for t in ("_datetime", "client", "sb(")))
check("E4 _tperiods reads no clock and no client",
      not any(t in (func_src(ROUTER, "_tperiods") or "")
              for t in ("_datetime", "client", "sb(")))

# ═══ F. THE MANUAL JOURNAL — the doubling the owner suspected ════════════════════════════════
print("\nF. manual journal entries: one home, one stored spelling")
COA = "app/modules/account/coa.py"
ACC_RT = "app/modules/account/router.py"
coa_s, acc_rt = src(COA), src(ACC_RT)

check("F1 there is ONE home for a month's journal entries",
      "def journal_rows(" in coa_s)
check("F2 the one home reads EVERY stored spelling",
      'in_("period", list(_period.period_keys(period)))' in (func_src(COA, "journal_rows") or ""))
check("F3 engine.compute_and_store dereferences it (it read ONE spelling before)",
      "coa.journal_rows(client, org_id, period)" in (func_src(ENGINE, "compute_and_store") or ""))
check("F4 statement_engine dereferences the same home, not its own query",
      "coa.journal_rows(client, org_id, period)" in (func_src(STMT, "_journal_rows") or "")
      and "journal_entries" not in (func_src(STMT, "_journal_rows") or ""))
check("F5 neither engine still runs its own journal_entries query",
      src(ENGINE).count("journal_entries") == 0 or
      'table("journal_entries").select' not in src(ENGINE))

# the writer: replace means replace
writer = None
for fn in ("save_journal", "put_journal", "set_journal"):
    writer = writer or func_src(ACC_RT, fn)
if writer is None:   # the writer is whichever function holds the delete+insert pair
    for node in ast.parse(acc_rt).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            seg = ast.get_source_segment(acc_rt, node) or ""
            if 'table("journal_entries").delete()' in seg:
                writer = seg
                break
check("F6 the journal writer was found", writer is not None)
check("F7 it purges EVERY spelling of the month before inserting",
      'in_("period", list(_period.period_keys(period)))' in (writer or ""),
      "a .eq purge leaves the other spelling's rows in place")
check("F8 it inserts under the CANONICAL spelling",
      "_period.canonical_period(period)" in (writer or ""))
check("F9 it no longer purges with a bare .eq on period",
      '.eq("org_id", org_id).eq("period", period).execute()' not in (writer or ""))
check("F10 the journal PAGE reads every spelling too, so it shows what the statements use",
      'in_("period", list(_period.period_keys(period)))' in (func_src(ACC_RT, "get_journal") or ""))

# THE ARITHMETIC OF THE DOUBLING — why a one-spelling purge beside a both-spelling read doubles
JOURNAL = [{"period": "September 2026", "account_line": "Owner capital", "amount": 5000.0},
           {"period": "2026-09", "account_line": "Owner capital", "amount": 5000.0}]
read_both = [r for r in JOURNAL if canonical_period(r["period"]) == "September 2026"]
check("F11 a both-spelling read over two one-spelling saves sees the entry TWICE",
      len(read_both) == 2 and sum(r["amount"] for r in read_both) == 10000.0)
purged = [r for r in JOURNAL if r["period"] not in period_keys("2026-09")]
check("F12 purging every spelling leaves nothing of the old save behind",
      purged == [], f"{purged}")
check("F13 and the canonical insert means the second save cannot land beside the first",
      canonical_period("2026-09") == canonical_period("September 2026"))


# ═══ G. THE DASHBOARD READ — one vintage per month, the newest ═══════════════════════════════
print("\nG. the Account dashboard reads one vintage per month")
ov = func_src(ACC_RT, "overview") or ""
check("G1 the dashboard reads EVERY stored spelling of the month",
      'in_("period", list(_period.period_keys(period)))' in ov)
check("G2 it no longer reads one spelling with a bare .eq",
      '.eq("period", period)' not in ov, "a one-spelling read hides the other snapshot")
check("G3 it keeps the NEWEST snapshot per (scope, statement)",
      "computed_at" in ov and "_newest" in ov)
nar = func_src(ACC_RT, "_consolidated_pl") or ""
check("G4 the narrative banner orders by computed_at instead of taking row order",
      'order("computed_at", desc=True)' in nar)
check("G5 banner and dashboard therefore quote the same snapshot",
      "period_keys(period)" in nar and "_period.period_keys(period)" in ov)

# the arithmetic: two vintages of one month, read as one
SNAPS = [{"scope_key": "consolidated", "statement_type": "pl", "computed_at": "2026-06-17T02:58:40Z",
          "net_income": 100.0},
         {"scope_key": "consolidated", "statement_type": "pl", "computed_at": "2026-06-26T02:16:14Z",
          "net_income": 250.0}]
newest = {}
for r in SNAPS:
    k = (r["scope_key"], r["statement_type"])
    if k in newest and str(r["computed_at"]) <= str(newest[k]["computed_at"]):
        continue
    newest[k] = r
check("G6 two vintages of one month collapse to ONE row", len(newest) == 1)
check("G7 and it is the newer one", list(newest.values())[0]["net_income"] == 250.0)


# ═══ H. THE SIBLINGS — every caller the module graph names, checked by name ══════════════════
print("\nH. the siblings the module graph named")
AUTO = "app/modules/account/autocompute.py"
ATT = "app/modules/account/finance_attention.py"
RECON = "app/modules/account/recon.py"
MA_RECON = "app/modules/commcalc/ma_recon.py"
AUTOCALC = "app/modules/commcalc/auto_calc.py"

check("H1 the snapshot-vintage read covers every spelling (a month was read as never computed)",
      'in_("period", list(period_keys(period)))' in (func_src(AUTO, "statements_computed_at") or ""))
check("H2 the finance-attention balance-sheet read covers every spelling",
      'in_("period", list(_pkeys(period)))' in src(ATT))
check("H3 and takes the newest snapshot",
      'order("computed_at", desc=True)' in src(ATT))
check("H4 the recon flags purge covers every spelling",
      'in_("period", list(_period.period_keys(period)))' in src(RECON))
check("H5 the recon flags insert uses the canonical spelling",
      src(RECON).count("_period.canonical_period(period)") == 2,
      "both the company-wide and the per-store flag must canonicalise")
check("H6 recon no longer purges flags with a bare .eq on period",
      '.eq("source", "account_recon").eq("period", period)' not in src(RECON))

# EXCUSED BY MEASUREMENT, not by assumption — these two were already correct
check("H7 ma_recon already purges every spelling (_pvariants) — excused",
      'in_("period", variants)' in src(MA_RECON) and "_pvariants(period)" in src(MA_RECON))
check("H8 auto_calc already canonicalises before any upsert (month_periods) — excused",
      "_pd.canonical_period(raw)" in (func_src(AUTOCALC, "month_periods") or ""))

# NO writer in the account tree may key a period-scoped replace on one spelling again
def code_only(rel):
    """The file with its `#` comment tails stripped — a comment QUOTING the old shape is not the
    old shape, and a lock that cannot tell the difference bans explaining the fix."""
    out = []
    for line in src(rel).splitlines():
        st = line.lstrip()
        if st.startswith("#"):
            continue
        out.append(line.split("  # ")[0])
    return "\n".join(out)


for rel in (ENGINE, STMT, ACC_RT, RECON, AUTO, ATT, COA):
    check(f"H9 {rel.split('/')[-1]}: no bare .eq period filter survives in code",
          '.eq("period", period)' not in code_only(rel),
          "one spelling is the whole defect")


print(f"\n{'─' * 78}\n{ok} ok, {fail} FAIL\n{'─' * 78}")
sys.exit(1 if fail else 0)
