"""BUILD LOCK + PROOF — one home for "how many will sell next", and for "is this line a device"
(index §COMMCALC-48).

WHY A LOCK AND NOT JUST A MODULE. CLAUDE.md: *"A design fix ships with a check that FAILS THE BUILD
if a caller stops dereferencing the shared fact, or if a second copy appears. Without that, the next
change quietly restores the patchwork."* This platform has written the velocity/horizon projection
twice and the device-line test twice, and the second copy of each was committed WITH A DOCSTRING
EXPLAINING THAT IT WAS A COPY. A rule written only in an index section is a rule that gets re-broken
by the next author who is in a hurry and reads that docstring as permission. So the rule is a check.

Run: `cd backend && python3 harness_demand_projection.py`
DB-FREE. No client, no network, no environment — drive it under `env -i`.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.commcalc import demand_projection as dp   # noqa: E402
from app.modules.core import module_graph as _mg               # noqa: E402
from app.modules.core.module_graph import home_under_app       # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS" if cond else "FAIL") + f"  {name}" + (f"  [{detail}]" if detail and not cond else ""))


APP = "app"
# THE RULING HAS ONE HOME AND THIS LOCK READS IT (index §19.18, enforced by
# harness_module_graph_guard §H2). Both the home and the list of callers are DEREFERENCED from
# `core/module_graph`, so a caller added there is checked here without this file being edited — and a
# literal kept here would be exactly the second copy the lock exists to refuse.
# `home_under_app` answers relative to `backend/app`; the walks here are relative to `backend`.
HOME = os.path.join(APP, home_under_app("demand_projection"))
CALLERS = tuple(sorted(_mg.FACTS["demand_projection"]["callers"]))


def src(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def py_files():
    for root, _dirs, files in os.walk(APP):
        for f in files:
            if f.endswith(".py"):
                yield os.path.join(root, f)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== A. the home exists and imports nothing ==")
# The import-cycle worry was the stated excuse for the SECOND copy of the device-line test. It is
# answered by this module importing nothing at all, so the excuse cannot be revived — and if an
# import is ever added here, the excuse comes back and this check fails first.
home = src(HOME)
check("A1 the one home is where §COMMCALC-48 says it is", os.path.exists(HOME))
_imports = [ln.strip() for ln in home.splitlines()
            if re.match(r"^\s*(import|from)\s", ln) and "__future__" not in ln]
check("A2 THE EXCUSE FOR THE SECOND COPY IS GONE — this module imports nothing, so no caller can "
      "claim a load-order cycle as a reason to keep its own copy",
      _imports == [], str(_imports))
check("A3 it is pure: no database client, no FastAPI, no HTTP",
      not re.search(r"get_supabase|APIRouter|HTTPException|requests|httpx", home))


print("\n== B. no second copy of the device-line test ==")
# The shape both copies had, in either order of the two halves.
_DEV_COPY = re.compile(r"""(?x)
    (cellphone)[^\n]{0,80}(in\s+_?DEV_DEPTS|in\s+\w*dev_depts\w*)
  | (in\s+_?DEV_DEPTS|in\s+\w*dev_depts\w*)[^\n]{0,80}(cellphone)
""", re.I)
_dev_copies = []
for f in py_files():
    if os.path.abspath(f) == os.path.abspath(HOME):
        continue
    body = src(f)
    if _DEV_COPY.search(body):
        _dev_copies.append(f)
check("B1 BUILD LOCK — the 'category is a handset OR the department is a device department' test "
      "exists in ONE place. Two tests of what a device is, is two answers to one question",
      _dev_copies == [], str(_dev_copies))
# And the vocabulary itself: a hardcoded copy of the department set is the same defect one level down.
#
# THE DEPARTMENT VOCABULARY HAS FOUR OLDER COPIES, AND THEY ARE REPORTED RATHER THAN QUIETLY
# EXCLUDED. `account/coa.DEVICE_DEPTS`, `commcalc/gp_report.DEVICE_DEPTS` and
# `commcalc/calculator.DEVICE_DEPTS` and `commcalc/router._BOX_DEPTS` each hold the same three department
# names, and all four sit on MONEY paths — the chart of accounts, the gross-profit report, the
# commission calculator and the box count every per-box payout rate divides by. Wiring
# them to this home would restate which lines those surfaces treat as a device, which is a change to
# booked figures and is not something this report was asked to make (CLAUDE.md: money-touching
# changes are surfaced for approval, never slipped in). So they are PINNED: a FOURTH copy fails the
# build, and when one is retired the pin comes down with it. The register may only shrink.
_KNOWN_VOCAB_COPIES = {
    "app/modules/account/coa.py",            # chart of accounts — P&L department routing
    "app/modules/commcalc/gp_report.py",     # gross profit — device vs accessory split
    "app/modules/commcalc/calculator.py",    # commission calculator — box classification
    "app/modules/commcalc/router.py",        # `_BOX_DEPTS` — the box count every payout rate divides by
}
_vocab_copies = {f.replace(os.sep, "/") for f in py_files()
                 if os.path.abspath(f) != os.path.abspath(HOME)
                 and re.search(r'["\']android - xp["\']', src(f), re.I)
                 and "HOUSE_DEVICE_DEPARTMENTS" not in src(f)}
check("B2 the ORDERING modules read the vocabulary rather than re-listing it — neither device "
      "forecast nor the PO recommendations keeps its own copy any more",
      not (_vocab_copies - _KNOWN_VOCAB_COPIES), str(sorted(_vocab_copies - _KNOWN_VOCAB_COPIES)))
check("B3 SHRINK-ONLY REGISTER — the four older copies on money paths are exactly the ones named, "
      "no more and no fewer. A fourth fails here; retiring one lowers the pin",
      _vocab_copies == _KNOWN_VOCAB_COPIES,
      f"extra={sorted(_vocab_copies - _KNOWN_VOCAB_COPIES)} "
      f"gone={sorted(_KNOWN_VOCAB_COPIES - _vocab_copies)}")


print("\n== C. no second copy of the velocity arithmetic ==")
# `units / lookback` followed by `round(rate * horizon)` is the shape both callers carried.
_VEL_COPY = re.compile(r"""(?x)
    \w+\s*=\s*[\w\[\]"'\.]+\s*/\s*lookback\b
""")
_vel = []
for f in py_files():
    if os.path.abspath(f) == os.path.abspath(HOME):
        continue
    body = src(f)
    if _VEL_COPY.search(body):
        _vel.append(f)
check("C1 BUILD LOCK — nothing divides a unit count by its own look-back window outside the one "
      "home. That division WAS the duplicated fact, in both copies",
      _vel == [], str(_vel))
_rec = [f for f in py_files()
        if os.path.abspath(f) != os.path.abspath(HOME)
        and re.search(r"max\(\s*0\s*,\s*\w*projecte?d?\w*\s*-\s*[\w\[\]\"'\.]*on_hand", src(f))]
check("C2 and nothing re-derives 'order the projection less the shelf' either",
      _rec == [], str(_rec))


print("\n== D. every caller really dereferences it ==")
# WRITING THE REGISTRY AND LEAVING THE CALLERS UNWIRED IS NOT A FIX AT ALL (CLAUDE.md §19.18, which
# records it happening three times here). So each caller is checked for the import AND for a real
# call, not just the import — the §CORE-17 batch-19 lesson about matching the use and not the name.
for c in CALLERS:
    body = src(c)
    has_import = re.search(r"from app\.modules\.commcalc import .*demand_projection", body)
    has_call = re.search(r"(demand_projection|_dp)\.(projection|projected_units|is_device_line|"
                         r"recommend_qty|HOUSE_DEVICE_DEPARTMENTS)", body)
    check(f"D[{os.path.basename(c)}] imports the one home AND actually calls it",
          bool(has_import and has_call),
          f"import={bool(has_import)} call={bool(has_call)}")


print("\n== E. the arithmetic is what the two callers were already running ==")
# BYTE-IDENTICAL OR IT IS NOT AN EXTRACTION. An extraction that quietly changes rounding is an
# unannounced change to live order quantities.
def _old_way(units, lookback, horizon, on_hand):
    rate = units / lookback
    return round(rate, 2), int(round(rate * horizon)), max(0, int(round(rate * horizon)) - on_hand)


_mismatch = []
for units in (0, 1, 2, 3, 5, 7, 14, 23, 99, 1000):
    for lookback in (1, 2, 3, 7, 14, 30, 90):
        for horizon in (1, 2, 7, 14, 30):
            for on_hand in (0, 1, 10, 500):
                want = _old_way(units, lookback, horizon, on_hand)
                p = dp.projection(units, lookback, horizon)
                got = (p["velocity"], p["projected"], dp.recommend_qty(p["projected"], on_hand))
                if want != got:
                    _mismatch.append((units, lookback, horizon, on_hand, want, got))
check("E1 1,400 combinations of units x window x horizon x on-hand agree with the four lines both "
      "callers used to carry, to the digit — the velocity, the projection AND the order",
      _mismatch == [], str(_mismatch[:3]))
# The one place the extraction deliberately DIFFERS, named rather than hidden: the old code raised
# ZeroDivisionError on a window of no days; this one reports that it could not measure.
check("E2 the ONE deliberate difference: a window of no days is reported as unmeasurable instead of "
      "raising. A 500 and a silent zero are both worse than saying so",
      dp.projection(9, 0, 7)["basis"] == "no_window"
      and dp.projection(9, 0, 7)["projected"] is None)


print("\n== F. the honesty rules ==")
check("F1 nothing sold over a real window is a MEASURED zero, not a blank",
      dp.projection(0, 30, 60) == {"units": 0, "window": 30, "horizon": 60,
                                   "velocity": 0.0, "projected": 0, "basis": "measured"})
check("F2 an unmeasurable projection cannot become an order quantity",
      dp.recommend_qty(None, 40) is None)
check("F3 an order is never negative", dp.recommend_qty(2, 900) == 0)
check("F4 a horizon of nothing is a real order of nothing",
      dp.projection(30, 30, 0)["projected"] == 0)
check("F5 every term of the arithmetic travels with the answer, so a quantity on an order screen "
      "can be argued with",
      set(dp.projection(1, 1, 1)) == {"units", "window", "horizon", "velocity", "projected", "basis"})
check("F6 the declared basis is one of the named ones",
      all(dp.projection(u, w, 7)["basis"] in dp.PROJECTION_BASES
          for u, w in ((1, 1), (0, 5), (1, 0))))


print("\n== G. the device vocabulary is config, never code (RULE TWO) ==")
check("G1 no carrier, tenant or brand name in the home",
      not re.search(r"(?i)boost|luxelink|cellfonz|verizon|t-?mobile|vzone|apple|samsung", home))
check("G2 a declared vocabulary replaces the house one",
      dp.is_device_line("Handsets", "", depts=["handsets"]) is True
      and dp.is_device_line("Android - XP", "", depts=["handsets"]) is False)
check("G3 an EXPLICITLY empty declaration is honoured — a tenant whose feed carries only one of the "
      "two columns can say so, and is not silently given the house list back",
      dp.is_device_line("Android - XP", "CellPhone", depts=[], categories=[]) is False)
check("G4 either column may answer, because the two live feed formats each carry only one",
      dp.is_device_line("", "CellPhone") and dp.is_device_line("Android - XP", None))


print("\n" + "=" * 70)
print(f"  {len(PASS)} passed, {len(FAIL)} failed")
print("=" * 70)
sys.exit(1 if FAIL else 0)
