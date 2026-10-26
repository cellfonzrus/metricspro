"""THE CLIENT FOLD'S FIXTURES ARE REAL SERVER PANELS — and cannot go stale (index §CORE-17.3d).

`frontend/prove_drill_narrow.mjs` pins the client-side `narrowPanel` to the server's own fold by
asserting the IDENTITY: with nothing filtered out, the narrowed figure, tie-out, wording and
provenance roll-up must equal what `core/report_drilldown.panel` produced. That proof is only worth
anything if its fixtures are what the server really emits, so this file REGENERATES them from the
real declarations and fails the build when the checked-in file differs.

So the two folds are locked to each other in both directions: change the server's panel shape and
this fails until the fixtures are regenerated; change the client's arithmetic and the JS proof fails
against them.

  python3 backend/harness_drill_narrow_fixtures.py            # check
  python3 backend/harness_drill_narrow_fixtures.py --write     # regenerate after a deliberate change
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.closing import cash_recon_drilldown as crd       # noqa: E402
from app.modules.closing import day_recon_drilldown as drd        # noqa: E402
from app.modules.closing import external_credit_drilldown as ecd  # noqa: E402
from app.modules.closing import external_credit_recon as ecr      # noqa: E402
from app.modules.commcalc import device_financing_drilldown as dfd  # noqa: E402
from app.modules.commcalc import tender_recon_drilldown as trd    # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "frontend", "prove_drill_narrow_fixtures.json")

# ── The cases the client fold has to survive, one per awkward shape. ────────────────────────────
#
# Every panel below comes out of a REAL declaration through the REAL shared fold, so the fixture
# carries whatever the server actually puts on a row — `row_key`, the reason on an unmeasured row, the
# origins of a leg whose feed did not resolve. A hand-written fixture would prove the client agrees
# with my idea of the server, which is not the thing in question.
TENDER_ROWS = [
    {"store": "1 Main St", "pos_cash": 400.0, "closing_cash": 380.0, "cash_variance": 20.0,
     "pos_card": 100.0, "closing_card": 100.0, "card_variance": 0.0, "match": False,
     "in_pos": True, "in_closing": True},
    {"store": "2 Oak Ave", "pos_cash": 120.0, "closing_cash": 120.0, "cash_variance": 0.0,
     "pos_card": 60.0, "closing_card": 60.0, "card_variance": 0.0, "match": True,
     "in_pos": True, "in_closing": True},
    # A store the X-report has and no closing sheet does — a real shape, and the one a naive fold
    # turns into a balanced $0.00.
    {"store": "3 Elm Rd", "pos_cash": 500.0, "closing_cash": 0.0, "cash_variance": 500.0,
     "pos_card": 0.0, "closing_card": 0.0, "card_variance": 0.0, "match": False,
     "in_pos": True, "in_closing": False},
]
TENDER_TOTALS = {"pos_cash": 1020.0, "closing_cash": 500.0, "cash_variance": 520.0,
                 "pos_card": 160.0, "closing_card": 160.0, "card_variance": 0.0,
                 "mismatches": 2}

EPAY_ROWS = [
    {"store_code": "B-1", "store_address": "1 Main St", "close_date": "2026-09-01", "flag": True,
     "declared": {"cash": 400.0}, "sales": {"cash": 380.0}, "bank_deposited": 250.0,
     "cash_variance": 150.0, "reps": [{"employee_name": "Abid"}]},
    {"store_code": "B-2", "store_address": "2 Oak Ave", "close_date": "2026-09-01", "flag": False,
     "declared": {"cash": 120.0}, "sales": {"cash": 120.0}, "bank_deposited": 120.0,
     "cash_variance": 0.0, "reps": []},
]
EPAY_TOTALS = {"declared_cash": 520.0, "bank_deposited": 370.0, "sales_cash": 500.0,
               "flagged": 1, "stores": 2}

# THE UNMEASURED CASE. A card-settlement tally whose processor feed never covered one store-day: the
# row is served with its reason and NO number, the figure counts it out, and the tie-out must come
# back None rather than False. That is the single most important thing for the client fold to get
# right, because None and False mean opposite things to a reader.
ECR_ROWS = [
    {"store_code": "B-1", "store_address": "1 Main St", "close_date": "2026-09-02", "market": "LI",
     "processor_role": "external_cc", "role_title": "External card", "declared_basis": "rep",
     "declared_amount": 500.0, "settled_amount": 470.0, "variance": -30.0, "status": "short"},
    {"store_code": "B-2", "store_address": "2 Oak Ave", "close_date": "2026-09-02", "market": "LI",
     "processor_role": "external_cc", "role_title": "External card", "declared_basis": "rep",
     "declared_amount": 200.0, "settled_amount": 200.0, "variance": 0.0, "status": "match"},
    {"store_code": "B-3", "store_address": "3 Elm Rd", "close_date": "2026-09-02", "market": "NJ",
     "processor_role": "external_cc", "role_title": "External card", "declared_basis": "rep",
     "declared_amount": 900.0, "settled_amount": None, "variance": None,
     "status": "no_processor_data"},
]

# THE UNRESOLVED-FEED CASE: the accessory recon with no sales table resolved, so every row carries an
# UNTRACEABLE origin. The client must keep counting those, not tidy them away.
ACC_ROWS = [
    {"store_code": "B-1", "store_address": "1 Main St", "close_date": "2026-09-01",
     "declared": 300.0, "actual": 240.0, "variance": 60.0, "flag": True, "reps": []},
    {"store_code": "B-2", "store_address": "2 Oak Ave", "close_date": "2026-09-01",
     "declared": 90.0, "actual": 90.0, "variance": 0.0, "flag": False, "reps": []},
]
ACC_TOTALS = {"declared": 390.0, "actual": 330.0, "variance": 60.0, "flagged": 1, "stores": 2}

# THE SUBSET-TILE CASE: cash recon's short/over dollar tiles count only part of their rows, so the
# panel carries rows it deliberately does not count.
CASH_ROWS = [
    {"store_code": "B-1", "store_name": "1 Main St", "day": "2026-09-01", "market": "LI",
     "reps": ["Abid"], "cash_declared": 400.0, "credit_declared": 100.0,
     "mgmt_counted": 380.0, "mgmt_variance": -20.0, "cash_picked_actual": 400.0,
     "cash_picked_variance": 0.0, "cash_picked_comparable": True},
    {"store_code": "B-2", "store_name": "2 Oak Ave", "day": "2026-09-01", "market": "LI",
     "reps": [], "cash_declared": 120.0, "credit_declared": 0.0,
     "mgmt_counted": 150.0, "mgmt_variance": 30.0, "cash_picked_actual": None,
     "cash_picked_variance": None, "cash_picked_comparable": None},
]
CASH_TOTALS = {"cash_declared": 520.0, "credit_declared": 100.0,
               "mgmt_short_amount": -20.0, "mgmt_over_amount": 30.0}

# THE TWO SHAPES NO OTHER FIXTURE CARRIED, and the one page batch 16 narrows (§CORE-17.3p): a panel
# whose unit is a COUNT rather than money, and a row addressed by a THREE-PART natural key. The
# client fold has to be the identity on both, and `prove_drill_narrow.mjs` asserts exactly that over
# every panel in this file — so without these two the count unit and the composite key were never
# tested on the client at all.
FIN_ROWS = [
    {"vendor_key": "plan-a", "store_code": "s1", "store": "1 Main St", "market": "LI",
     "rep": "Abid", "units": 2, "amount": 800.0, "transactions": 2,
     "first_date": "2026-09-02", "last_date": "2026-09-11"},
    {"vendor_key": "plan-a", "store_code": "s2", "store": "2 Oak Ave", "market": "NJ",
     "rep": "Sam", "units": 1, "amount": 300.0, "transactions": 1,
     "first_date": "2026-09-05", "last_date": "2026-09-05"},
    # The same store and rep under a SECOND vendor, which is why the key needs all three parts.
    {"vendor_key": "plan-b", "store_code": "s1", "store": "1 Main St", "market": "LI",
     "rep": "Abid", "units": 1, "amount": 450.0, "transactions": 1,
     "first_date": "2026-09-09", "last_date": "2026-09-09"},
]
FIN_RESULT = {"rows": FIN_ROWS, "totals": {"units": 4, "amount": 1550.0, "transactions": 4},
              "source": {"meta": {"primary": "daily_sales_feed", "primary_rows": 90,
                                  "other": "raw_sales", "other_rows": 12, "filled_rows": 3}}}


def build():
    panels = {}
    for k, v in trd.drilldowns(TENDER_ROWS, TENDER_TOTALS).items():
        panels["tender." + k] = v
    for k, v in drd.epay_drilldowns(EPAY_ROWS, EPAY_TOTALS,
                                    sales_tables=("daily_sales_feed",)).items():
        panels["epay." + k] = v
    for k, v in drd.accessory_drilldowns(ACC_ROWS, ACC_TOTALS, sales_tables=()).items():
        panels["accessory_no_feed." + k] = v
    for k, v in ecd.drilldowns(ECR_ROWS, ecr.totals(ECR_ROWS),
                               settlement_table="merchant_settlement_day").items():
        panels["card." + k] = v
    for k, v in crd.drilldowns(CASH_ROWS, CASH_TOTALS, 1.0).items():
        panels["cash." + k] = v
    for k, v in dfd.financing_drilldowns(FIN_RESULT).items():
        panels["financing." + k] = v
    return {
        "_note": ("GENERATED — do not hand-edit. Real panels from the real server declarations, "
                  "regenerate with `python3 backend/harness_drill_narrow_fixtures.py --write`. "
                  "Consumed by frontend/prove_drill_narrow.mjs (index §CORE-17.3d)."),
        "panels": panels,
    }


def main():
    want = json.dumps(build(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if "--write" in sys.argv:
        with open(OUT, "w", encoding="utf-8") as fh:
            fh.write(want)
        print("wrote %s (%d panels)" % (OUT, len(build()["panels"])))
        return 0
    have = open(OUT, encoding="utf-8").read() if os.path.exists(OUT) else ""
    if have == want:
        print("OK — the client fold's fixtures are what the server emits today (%d panels)."
              % len(build()["panels"]))
        return 0
    print("FAIL — frontend/prove_drill_narrow_fixtures.json is not what the server emits now.")
    print("       The server's panel shape changed. Regenerate and re-read the JS proof:")
    print("         python3 backend/harness_drill_narrow_fixtures.py --write")
    return 1


if __name__ == "__main__":
    sys.exit(main())
