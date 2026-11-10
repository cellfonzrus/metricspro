"""LOCK — WHICH STORE-DLAR COLUMN IS WHICH METRIC HAS ONE HOME, AND EVERY CALLER DEREFERENCES IT.
stdlib only, DB-free, no framework. Run: python3 backend/harness_dlar_metric_catalog.py

OWNER ASK 2026-11-10, verbatim: *"Use those other 21 columns and make them a part of the kpi
reporting also filtered by the kpi and out regular filters and send option"*.

THE DEFECT BEHIND THE ASK — measured, not inferred. The 2026-11-10 12:00 UTC DLAR sweep over 28
stores recorded, in `core.job_run.detail.evidence.fields.dlar.unconsumed`, 21 source fields arriving
that the store mapping did not read; among them `non_selling_days`, the carrier's new spelling of the
`zero_selling_days` column we already had and which had been landing empty since the rename. The
§COMMCALC-16 diagnostic had been printing all of this correctly for weeks. Nothing read it, because
reading a new column meant editing FOUR lists:

    dlar_sweep.STORE_FIELDS · kpi_failing.STORE_KPI_COLUMNS · two `select(...)` literals in
    commcalc/router.py · a hand-typed `KPIS` array in frontend .../commcalc/kpi/page.tsx

Four homes for one fact, and the LAST of them decided what a person could actually see — so a column
could be declared, landed, and invisible. That is the shape CLAUDE.md's "one fact, one home,
dereferenced — never copied" rule exists to stop, and a registry without this lock has been written
here three times already and quietly un-wired each time (index §19.18).

WHAT THIS FAILS THE BUILD ON
 A. a measurable column the sweep declares that the catalog cannot name (the next column landing in
    the table and never reaching a surface);
 B. a second copy of the column list — the `select(...)` literal returning to either router reader;
 C. either router reader no longer dereferencing `dlar_metric_catalog`;
 D. the frontend KPI page carrying its own store-column map again;
 E. the scored set being restated here instead of read from `kpi_failing` (the §19.28 defect: the
    report's columns and the PAID score naming a different set);
 F. the 21 measured-arriving fields going undeclared again, by name;
 G. `non_selling_days` becoming a SECOND column beside `zero_selling_days` (one fact, two homes);
 H. `activations_total` being quietly folded into `gross_adds` while the owner decision is open
    (§19.26: missing beats wrong on a figure reps are scored against);
 I. RULE TWO — a carrier, tenant or product name appearing in the catalog module;
 J. an optional column being read without the per-column probe (§4b.1), which would 400 the whole
    read on an unapplied migration and show a person nothing at all.
"""
import os
import pathlib
import re
import sys
import types

# stdlib-only import of `dlar_sweep`: its portal I/O needs requests/bs4, its rules need nothing. The
# same stub `harness_portal_cell.py` installs, for the same reason — this proof is DB-free AND
# wheel-free, so it runs in the no-install lock job beside every other lock in it.
for _n, _attrs in (("requests", ("Session",)), ("bs4", ("BeautifulSoup",))):
    if _n not in sys.modules:
        _m = types.ModuleType(_n)
        for _a in _attrs:
            setattr(_m, _a, lambda *a, **k: None)
        sys.modules[_n] = _m

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))

from app.modules.commcalc import dlar_metric_catalog as cat   # noqa: E402
from app.modules.commcalc import dlar_sweep as ds             # noqa: E402
from app.modules.commcalc import kpi_failing as kf            # noqa: E402

P = F = 0


def check(label, ok, got=None):
    global P, F
    if ok:
        P += 1
        print("  PASS  " + label)
    else:
        F += 1
        print("  FAIL  " + label + ("" if got is None else "  got=%r" % (got,)))


_ROOT = pathlib.Path(__file__).resolve().parent
_ROUTER = (_ROOT / "app" / "modules" / "commcalc" / "router.py")
_CAT_SRC = (_ROOT / "app" / "modules" / "commcalc" / "dlar_metric_catalog.py").read_text("utf-8")
_RSRC = _ROUTER.read_text("utf-8") if _ROUTER.exists() else ""
_PAGE = (_ROOT.parent / "frontend" / "src" / "app" / "(platform)" / "commcalc" / "kpi" / "page.tsx")
_PSRC = _PAGE.read_text("utf-8") if _PAGE.exists() else ""

# The 21 fields the live sweep reported arriving-but-unread on 2026-11-10. EVIDENCE: pinned from that
# run, not paraphrased, because it is the reason this module exists.
ARRIVING_2026_11_10 = (
    "activations_total", "auxiliary_device_acts", "avg_platinum_points", "byod_total",
    "byod_unlocked_activations", "mom_percent_change", "non_selling_days", "offer_trade_ins",
    "projection", "protection_dop", "protection_upgrades", "rpl_count", "starlink_activations",
    "starlink_sales", "swaps_total", "total_health_checks", "total_trade_ins",
    "trade_in_unlocked_activations", "upgrade_value_trade_ins", "value_trade_ins",
    "yoy_percent_change",
)

print("── A. every measurable declared column can be named ────────────────────────────────────────")
_measurable = {r[0] for r in ds.STORE_FIELDS if r[1] in (ds.COUNT, ds.RATE)}
_keys = {m["key"] for m in cat.catalog()}
check("A1 no declared figure is missing from the catalog",
      _measurable <= _keys, sorted(_measurable - _keys))
check("A2 every catalogued figure has a label", all(m["label"].strip() for m in cat.catalog()),
      [m["key"] for m in cat.catalog() if not m["label"].strip()])
check("A3 every catalogued figure has a declared display group",
      all(m["group"] in cat.GROUPS for m in cat.catalog()),
      [(m["key"], m["group"]) for m in cat.catalog() if m["group"] not in cat.GROUPS])
check("A4 labels are unique — two columns must not read as one metric",
      len({m["label"] for m in cat.catalog()}) == len(cat.catalog()))
check("A5 the catalog carries the derived total and says it is derived",
      any(m["key"] == "total_acts" and m["derived"] and m["source"] == () for m in cat.catalog()))

print("── B/C. ONE spelling of the column list, and both readers ask for it ───────────────────────")
# The literal, as it was written in BOTH readers. Kept as an armed negative control: its return is
# the duplicate defect itself, so the lock recognises the exact shape rather than a paraphrase.
_OLD_LITERAL = "location,address,store_code,atu,protect_pct,byod_pct,family_plan_pct,tmr3"
check("B1 the hand-written select literal is gone from the router", _OLD_LITERAL not in _RSRC)
check("B2 no backend file spells a raw_dlar_store column list by hand",
      not any(_OLD_LITERAL in p.read_text("utf-8", errors="ignore")
              for p in (_ROOT / "app").rglob("*.py")))
check("C1 the KPI store reader dereferences the catalog",
      _RSRC.count("dlar_metric_catalog") >= 3, _RSRC.count("dlar_metric_catalog"))
for _fn in ("def get_dlar_store_kpis", "def _cr_resolve_kpi_metrics"):
    _body = _RSRC.split(_fn, 1)[-1][:2600] if _fn in _RSRC else ""
    check("C2 %s reads the catalog's column list" % _fn.replace("def ", ""),
          "_cat.REQUIRED_COLUMNS" in _body and "_cat.OPTIONAL_COLUMNS" in _body)
check("C3 the catalog endpoint exists for a picker to read",
      '@router.get("/dlar-store-catalog")' in _RSRC)

print("── D. the frontend does not keep its own copy ──────────────────────────────────────────────")
check("D1 the KPI page exists where this lock looks for it", bool(_PSRC), str(_PAGE))
# The map's SHAPE, not the word: the deleted copy was a `storeKey: '<column>'` property on each KPI
# row, and the comment that records why it is gone must stay readable. An armed negative control.
check("D2 the page carries no hand-typed store-column map (the fourth home)",
      not re.search(r"storeKey\s*:", _PSRC))
check("D3 the page reads the catalog endpoint instead",
      "dlar-store-catalog" in _PSRC)

print("── E. SCORED-ness is the pay engine's fact, read not restated ──────────────────────────────")
check("E1 the catalog's scored set IS kpi_failing's map",
      {m["key"]: m["scored"] for m in cat.catalog() if m["scored"]}
      == {c: k for k, c in kf.STORE_KPI_COLUMNS.items()})
check("E2 and it is DEREFERENCED, not written out again",
      "STORE_KPI_COLUMNS" in _CAT_SRC
      and not re.search(r'"scored":\s*\{', _CAT_SRC))

print("── F/G/H. the measured arrivals, declared — and not guessed at ─────────────────────────────")
_spellings = {k for r in ds.STORE_FIELDS for k in r[2:]}
check("F1 all 21 fields the portal was sending are read",
      all(f in _spellings for f in ARRIVING_2026_11_10),
      [f for f in ARRIVING_2026_11_10 if f not in _spellings])
_zsd = [r for r in ds.STORE_FIELDS if r[0] == "zero_selling_days"]
check("G1 the renamed field is a SPELLING of the column we had, not a second column",
      len(_zsd) == 1 and "non_selling_days" in _zsd[0][2:]
      and not any(r[0] == "non_selling_days" for r in ds.STORE_FIELDS))
_gross = [r for r in ds.STORE_FIELDS if r[0] == "gross_adds"]
check("H1 `activations_total` is its own column while the owner decision is open",
      any(r[0] == "activations_total" for r in ds.STORE_FIELDS))
check("H2 and it is NOT folded into gross_adds on a guess (§19.26)",
      bool(_gross) and "activations_total" not in _gross[0][2:])

print("── I. RULE TWO ─────────────────────────────────────────────────────────────────────────────")
for _w in ("boost", "elevate", "cellfonz", "luxelink", "vzone", "t-mobile", "verizon", "metro"):
    check("I:%s does not appear in the catalog module" % _w,
          not re.search(_w, _CAT_SRC, re.I))

print("── J. an optional column is PROBED, never assumed (§4b.1) ──────────────────────────────────")
check("J1 optional and required are disjoint and together are the whole read",
      set(cat.OPTIONAL_COLUMNS).isdisjoint(cat.REQUIRED_COLUMNS)
      and set(cat.OPTIONAL_COLUMNS) | set(cat.REQUIRED_COLUMNS) == set(cat.select_columns()))
check("J2 the read list has no duplicate (a duplicated column makes PostgREST 400)",
      len(cat.select_columns()) == len(set(cat.select_columns())))
check("J3 every brand-new column is optional",
      all(cat.by_key()[c]["optional"] for c in
          ("activations_total", "total_trade_ins", "starlink_activations", "yoy_percent_change")))
check("J4 nothing migration 002 already created became optional",
      not any(cat.by_key()[c]["optional"] for c in
              ("atu", "protect_pct", "total_acts", "zero_selling_days")))
check("J5 both readers probe per column rather than assuming the migration ran",
      _RSRC.count("present_columns") >= 3, _RSRC.count("present_columns"))
_unapplied = cat.present_catalog(frozenset())
check("J6 an unapplied column is reported unavailable WITH a reason, never dropped",
      len(_unapplied) == len(cat.catalog())
      and all((m["available"] is False and m[cat.UNAVAILABLE_REASON_KEY])
              for m in _unapplied if m["optional"]))
# §19.36 — WHY THE KEY IS PINNED AND NOT THE WORDING. The sentence names migration 1118, which is a
# setup internal: a tenant gets the one neutral house sentence and the platform super admin the
# detail, and `SetupNoticeMiddleware` performs that swap ONLY on `core.setup_notice.MESSAGE_KEYS`.
# The first push of this change shipped it under `unavailable_reason`, which the boundary does not
# read — so the internals would have reached the customer. CI caught it; this keeps it caught.
import importlib as _il
_sn = _il.import_module("app.core.setup_notice")
check("J8 the unavailable sentence leaves under a key the tenant boundary reads (§19.36)",
      _sn.is_message_key(cat.UNAVAILABLE_REASON_KEY), cat.UNAVAILABLE_REASON_KEY)
check("J9 and NOT under the key the shipped defect used (armed negative control)",
      not _sn.is_message_key("unavailable_reason")
      and all("unavailable_reason" not in m for m in _unapplied))
check("J7 and a pre-existing column is never reported unavailable",
      all(m["available"] for m in _unapplied if not m["optional"]))

print("\n%d passed, %d failed" % (P, F))
if F:
    sys.exit(1)
print("OK — one home for which store-DLAR column is which metric; every reader dereferences it, an "
      "unapplied column says so, and a renamed field is a spelling rather than a second column.")
