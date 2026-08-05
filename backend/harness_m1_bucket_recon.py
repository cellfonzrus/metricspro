"""harness_m1_bucket_recon.py — the 1st-Month (M1) commission bucket vs the VidaPay portal.

OWNER-REPORTED 2026-08-05: the Gross Profit report's "1st Month" commission showed ~$124k where the
VidaPay/master-agent portal states M1 ~$28k for the same period — 4.4x.

ROOT CAUSE proven here: `commission_legs.DEFAULT_CFG['ma_m1_fields']` (and the two rows migration 274
seeds) forced the SIX activation-order MARGIN columns of `raw_ma_commission` into the M1 bucket on top
of `spiff_m1`. Those columns are not commission legs — the owner settled that on 2026-08-04 ("these are
not margins but paid commission based on MRC"), the canonical Commission Ledger already maps them with
`payment_month: None`, and the VidaPay portal states Rebates Paid / Fees Margin Paid as their OWN
figures, so counting them as M1 double-counts them against the very number the owner cross-checks.

WHAT THIS PROVES (no DB, no network — pure + fake client):
  A. the arithmetic bridge 124k -> 28k on a luxelink-shaped component vector
  B. the SUM IDENTITY survives (m1 + m2_12 + unsplit == the unchanged commission total)
  C. through the REAL GP engine (`gp_report.calc_gp_report`): every pre-existing money column is
     byte-identical and only the leg companions move
  D. through the REAL `/ma-commission/summary` leg block (`router._ma_summary_legs`)
  E. CROSS-SURFACE AGREEMENT — the GP M1 now equals the /ma-overview-recon "Commissions Paid (M1)"
     tile's own definition (`spiff_m1`), which is what the portal states
  F. THE INVARIANT THAT WOULD HAVE CAUGHT THIS — the leg classifier and the canonical Commission
     Ledger's component map must agree on all twelve columns
  G. the ePay/Boost path is untouched (it splits on the payment-type label, not on these columns)
  H. it is still CONFIG — an org can put the margins back in M1 without a code change
  I. migration 277 clears exactly the seed the code default now matches
"""
import io
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.commcalc import commission_legs as CL          # noqa: E402
from app.modules.commcalc import gp_report as GP                # noqa: E402
from app.modules.commcalc import ledger_ma_sync as LMS          # noqa: E402
from app.modules.commcalc import ma_overview as MAO             # noqa: E402
from app.modules.account.residual_subs import _MA_COMPONENTS    # noqa: E402

_P = _F = 0
_FAILED = []


def section(t):
    print("\n── %s %s" % (t, "─" * max(0, 92 - len(t))))


def check(name, ok, detail=None):
    global _P, _F
    if ok:
        _P += 1
        print("  PASS  %s" % name)
    else:
        _F += 1
        _FAILED.append(name)
        print("  FAIL  %s   %s" % (name, "" if detail is None else detail))


def eq2(a, b):
    return abs(round(float(a), 2) - round(float(b), 2)) < 0.01


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# The luxelink-shaped component vector. Signs follow the real export: NEGATIVE = paid TO the dealer,
# so the roll-up flips them. spiff_m1 is scaled to the owner's reported portal figure ($28,000) and
# the margin block to the gap he reported ($96,000) so the harness reproduces HIS numbers, not toys.
# ══════════════════════════════════════════════════════════════════════════════════════════════════
MARGINS = ["rebate", "device_margin", "consumer_margin",
           "consumer_financing", "wallet_funding", "fees_margin"]
SPIFFS = ["spiff_m%d" % i for i in range(1, 7)]

SUMS = {
    "rebate":             -61_000.00,
    "device_margin":      -18_500.00,
    "consumer_margin":         -0.00,
    "consumer_financing":  -9_250.00,
    "wallet_funding":      -5_100.00,
    "fees_margin":         -2_150.00,
    "spiff_m1":           -28_000.00,
    "spiff_m2":            -6_400.00,
    "spiff_m3":            -3_100.00,
    "spiff_m4":            -1_050.00,
    "spiff_m5":              -700.00,
    "spiff_m6":              -420.00,
}
TOTAL = round(-sum(SUMS[c] for c in _MA_COMPONENTS), 2)        # exactly how _compute_gp builds `comm`
PORTAL_M1 = 28_000.00                                          # what VidaPay states
MARGIN_BLOCK = round(-sum(SUMS[c] for c in MARGINS), 2)        # 96,000

OLD_CFG = dict(CL.DEFAULT_CFG, ma_m1_fields=list(MARGINS))     # the shipped-and-wrong config
NEW_CFG = dict(CL.DEFAULT_CFG)                                 # the corrected default

old = CL.LegClassifier(OLD_CFG).ma(SUMS, _MA_COMPONENTS)
new = CL.LegClassifier(NEW_CFG).ma(SUMS, _MA_COMPONENTS)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("A. the 124k -> 28k bridge")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
check("the shipped config reproduces the owner's over-stated M1 ($124,000)",
      eq2(old["buckets"]["m1"], 124_000.00), old["buckets"]["m1"])
check("the corrected default lands on the portal's M1 ($28,000 = spiff_m1 alone)",
      eq2(new["buckets"]["m1"], PORTAL_M1), new["buckets"]["m1"])
check("the ENTIRE difference is the activation-order margin block ($96,000)",
      eq2(old["buckets"]["m1"] - new["buckets"]["m1"], MARGIN_BLOCK),
      (old["buckets"]["m1"] - new["buckets"]["m1"], MARGIN_BLOCK))
check("the over-statement factor is the 4.4x the owner reported",
      abs(old["buckets"]["m1"] / new["buckets"]["m1"] - 4.43) < 0.05,
      old["buckets"]["m1"] / new["buckets"]["m1"])
check("M2-M12 is UNCHANGED by the correction (it was never margin money)",
      eq2(old["buckets"]["trailing"], new["buckets"]["trailing"])
      and eq2(new["buckets"]["trailing"], 6_400 + 3_100 + 1_050 + 700 + 420),
      (old["buckets"]["trailing"], new["buckets"]["trailing"]))
check("the margin block moved to UNSPLIT — it is reclassified, never deleted",
      eq2(new["buckets"]["unsplit"] - old["buckets"]["unsplit"], MARGIN_BLOCK),
      (old["buckets"]["unsplit"], new["buckets"]["unsplit"]))
check("unsplit NAMES the six columns, so the page can explain the pile",
      sorted(new["unsplit_fields"]) == sorted(MARGINS), new["unsplit_fields"])


# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("B. SUM IDENTITY — the commission TOTAL does not move (misclassification, not duplication)")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
check("before: m1 + m2_12 + unsplit == the commission total", eq2(sum(old["buckets"].values()), TOTAL))
check("after:  m1 + m2_12 + unsplit == the SAME commission total", eq2(sum(new["buckets"].values()), TOTAL))
check("the total is byte-identical across the fix", eq2(old["total"], new["total"]))
check("every one of the 12 components is assigned to exactly one bucket, none dropped",
      eq2(sum(f["amount"] for f in new["fields"].values()), TOTAL)
      and len(new["fields"]) == len(_MA_COMPONENTS))
check("the leg LADDER re-sums to the total too (the chart cannot out-total its column)",
      eq2(sum(new["leg_ladder"].values()), TOTAL), new["leg_ladder"])
check("a clawback (positive raw value) stays in its own leg rather than reclassifying",
      eq2(CL.LegClassifier(NEW_CFG).ma(dict(SUMS, spiff_m2=+6_400.0), _MA_COMPONENTS)
          ["buckets"]["trailing"], (3_100 + 1_050 + 700 + 420) - 6_400))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("C. through the REAL GP engine — nothing but the leg companions moves")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
SALES = [
    {"store": "1200 Main St", "department": "ACCESSORIES", "category": "Case", "gp": 30.0,
     "ext_price": 80.0, "salesperson": "Rep One", "product_id": "P1", "sku": "P1",
     "product_desc": "Case", "voided": None, "trans_type": "Sale"},
    {"store": "1200 Main St", "department": "IPHONE", "category": "Phone", "gp": 0.0,
     "ext_price": 800.0, "salesperson": "Rep One", "product_id": "P2", "sku": "P2",
     "product_desc": "iPhone", "voided": None, "trans_type": "Sale"},
]
SMAP = [{"store_address": "1200 Main St", "salesforce_id": "SF1", "market": "NY",
         "store_code": "S1", "is_active": True}]
MA_INCOME = {"comm": TOTAL, "atu": 1_234.56,
             "components": dict(SUMS), "component_list": list(_MA_COMPONENTS)}


def gp_with(cfg):
    return GP.calc_gp_report(
        sales=[dict(r) for r in SALES], pay_detail=[], mi_rows=[], rep_commissions=[], expenses=[],
        catalog=[], store_mapping=[dict(r) for r in SMAP], period="June 2026", comp_rows=[],
        ma_income=dict(MA_INCOME, components=dict(SUMS)),
        leg_classify=CL.LegClassifier(cfg))


gp_old, gp_new = gp_with(OLD_CFG), gp_with(NEW_CFG)
t_old, t_new = gp_old["totals"], gp_new["totals"]

_LEG_KEYS = set()
for _p in ("comm", "comp_comm", "mi", "atu"):
    _LEG_KEYS |= set(CL.public_keys(_p))
_MONEY_KEYS = [k for k in t_new
               if isinstance(t_new.get(k), (int, float)) and k not in _LEG_KEYS]

_moved = [k for k in _MONEY_KEYS if not eq2(t_old.get(k, 0), t_new.get(k, 0))]
check("EVERY pre-existing GP money total is byte-identical (%d columns checked)" % len(_MONEY_KEYS),
      not _moved, _moved)
check("...including Commission received itself", eq2(t_new["comm"], TOTAL), t_new["comm"])
check("...and Net Profit", eq2(t_old["net_profit"], t_new["net_profit"]))
check("GP '1st Month Commission' tile falls 124,000 -> 28,000",
      eq2(t_old["comm_m1"], 124_000.00) and eq2(t_new["comm_m1"], PORTAL_M1),
      (t_old["comm_m1"], t_new["comm_m1"]))
check("GP 'M2-M12 Commission' tile is unchanged", eq2(t_old["comm_m2_12"], t_new["comm_m2_12"]))
check("GP Unsplit absorbs the margin block, so the three still add to Commission",
      eq2(t_new["comm_m1"] + t_new["comm_m2_12"] + t_new["comm_unsplit"], t_new["comm"]),
      (t_new["comm_m1"], t_new["comm_m2_12"], t_new["comm_unsplit"], t_new["comm"]))
check("the engine's own identity flag stays true", gp_new["commission_legs"]["identity_ok"])

_hl = gp_new["commission_legs"]["headline"]
check("the leg card names the REAL source (VidaPay/MA, not 'ePay Payment Detail')",
      "VidaPay" in _hl["label"], _hl["label"])
check("...and says the split is on the leg COLUMN", "column" in _hl["splits_on"].lower(), _hl["splits_on"])
check("...and NAMES the six unsplit columns with a plain-English why",
      sorted(_hl.get("unsplit_fields") or []) == sorted(MARGINS)
      and "not commission legs" in (_hl.get("unsplit_why") or ""),
      (_hl.get("unsplit_fields"), _hl.get("unsplit_why")))
check("an ePay (house/Boost) org's leg card still says ePay Payment Detail",
      GP.calc_gp_report(sales=[dict(r) for r in SALES], pay_detail=[
          {"business_address": "1200 Main St", "amount": 90.0,
           "payment_type": "New Activation Bounty - Month 1", "category": "Commission"}],
          mi_rows=[], rep_commissions=[], expenses=[], catalog=[],
          store_mapping=[dict(r) for r in SMAP], period="June 2026", comp_rows=[],
          leg_classify=CL.LegClassifier(NEW_CFG))["commission_legs"]["headline"]["label"]
      == "Commission received (ePay Payment Detail)")
check("a caller that passes NO components still reports the money honestly as unsplit (never guessed)",
      eq2(GP.calc_gp_report(sales=[], pay_detail=[], mi_rows=[], rep_commissions=[], expenses=[],
                            catalog=[], store_mapping=[dict(r) for r in SMAP], period="June 2026",
                            comp_rows=[], ma_income={"comm": TOTAL, "atu": 0.0},
                            leg_classify=CL.LegClassifier(NEW_CFG))["totals"]["comm_unsplit"], TOTAL))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("D. through the REAL /ma-commission/summary leg block")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test")
import app.modules.commcalc.router as R                                    # noqa: E402


class _NoConfigClient:
    """A tenant whose migration-274 config table is absent -> the CODE default decides. That is the
    posture this fix must be correct in, because the code default is what every un-migrated org uses."""

    def schema(self, _s):
        return self

    def table(self, _t):
        raise RuntimeError("relation does not exist")


legs = R._ma_summary_legs(_NoConfigClient(), "org-luxelink", dict(SUMS))
check("the Total-Processor card's M1 is the portal's M1", eq2(legs["m1"], PORTAL_M1), legs["m1"])
check("...its identity still holds against total_payable", legs["identity_ok"] and eq2(legs["total"], TOTAL))
check("...and its basis text no longer claims the margins are 1st month",
      "margins" in legs["basis"] and "NOT" in legs["basis"] and "1st Month = spiff_m1" in legs["basis"],
      legs["basis"])
check("...and it names the unsplit columns", sorted(legs["unsplit_fields"]) == sorted(MARGINS))
check("a config-table failure degrades to the corrected default, never a 500 and never the old rule",
      eq2(legs["m1"], PORTAL_M1))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("E. CROSS-SURFACE AGREEMENT — GP M1 == the /ma-overview-recon portal tile")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
tile = next(t for t in MAO.DEFAULT_TILES if t["tile_key"] == "commissions_paid")
check("the recon tile that cross-checks the portal is defined as spiff_m1 alone",
      tile["value_fields"] == "spiff_m1" and tile["sign"] == "negate", tile["value_fields"])
tile_value = round(-sum(SUMS[c] for c in tile["value_fields"].split(",")), 2)
check("GP '1st Month' now EQUALS that tile (the two surfaces agreed on nothing before)",
      eq2(t_new["comm_m1"], tile_value), (t_new["comm_m1"], tile_value))
check("...and the SHIPPED code disagreed with it by the whole margin block",
      eq2(t_old["comm_m1"] - tile_value, MARGIN_BLOCK))
check("the owner's settled definition is recorded in the module the tile lives in",
      "these are not margins but paid commission based on MRC"
      in " ".join((MAO.__doc__ or "").split()))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("F. THE INVARIANT — leg classifier vs the canonical Commission Ledger component map")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
# ledger_ma_sync.DEFAULT_COMPONENTS is the OTHER map of these same twelve columns and it was already
# right. Two maps of one export are how this defect happened; from here they are asserted equal.
_ledger = {c["col"]: c["payment_month"] for c in LMS.DEFAULT_COMPONENTS["ma_commission"]}
check("the ledger map and the leg classifier cover the same twelve columns",
      sorted(_ledger) == sorted(_MA_COMPONENTS), sorted(set(_ledger) ^ set(_MA_COMPONENTS)))
_disagree = []
for col, pm in _ledger.items():
    bucket, leg = CL.ma_field_leg(col, NEW_CFG)
    want_bucket = CL.UNSPLIT if pm is None else (CL.M1 if pm == 1 else CL.TRAILING)
    if bucket != want_bucket or (pm is not None and leg != pm):
        _disagree.append((col, pm, bucket, leg))
check("EVERY column's leg agrees with its ledger payment_month (this check fails on the old default)",
      not _disagree, _disagree)
_old_disagree = [c["col"] for c in LMS.DEFAULT_COMPONENTS["ma_commission"]
                 if c["payment_month"] is None and CL.ma_field_leg(c["col"], OLD_CFG)[0] == CL.M1]
check("...and it WOULD have caught the shipped default (6 columns disagreed)",
      sorted(_old_disagree) == sorted(MARGINS), _old_disagree)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("G. the ePay / Boost path is untouched")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
for lbl, want in (("New Activation Bounty - Month 1", CL.M1),
                  ("Boost Ready Bounty - Month 3", CL.TRAILING),
                  ("(In-Store) Device Financing Bounty - Month 6", CL.TRAILING),
                  ("UNL Premium - 2 Month Promo", CL.UNSPLIT),      # the trap label
                  ("Boost Auto Top-Up", CL.UNSPLIT)):
    check("label %-45r -> %s (unchanged)" % (lbl, want),
          CL.classify_label(lbl, NEW_CFG)[0] == want == CL.classify_label(lbl, OLD_CFG)[0])
check("MI/ATU residual still splits on the activation date, unchanged",
      CL.classify_activation("June 2026", "2026-06-04", NEW_CFG)[0] == CL.M1
      and CL.classify_activation("June 2026", "2026-02-04", NEW_CFG)[0] == CL.TRAILING
      and CL.classify_activation("June 2026", None, NEW_CFG)[0] == CL.UNSPLIT)
_CL_SRC = io.open(CL.__file__.replace(".pyc", ".py"), encoding="utf-8").read()
_CL_TREE = __import__("ast").parse(_CL_SRC)
_app_imports = [
    n.module if isinstance(n, __import__("ast").ImportFrom) else ",".join(a.name for a in n.names)
    for n in __import__("ast").walk(_CL_TREE)
    if isinstance(n, (__import__("ast").Import, __import__("ast").ImportFrom))]
check("still a PURE LEAF — zero app imports, so no payout/rate/tier/plan symbol is reachable",
      not [m for m in _app_imports if str(m).startswith("app")], _app_imports)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("H. still CONFIG, not code (RULE TWO)")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
check("an org that wants the margins in M1 gets them back with one config row, no code change",
      eq2(CL.LegClassifier(dict(CL.DEFAULT_CFG, ma_m1_fields=list(MARGINS)))
          .ma(SUMS, _MA_COMPONENTS)["buckets"]["m1"], 124_000.00))
check("one column at a time works too (it is a list, not a boolean)",
      eq2(CL.LegClassifier(dict(CL.DEFAULT_CFG, ma_m1_fields=["rebate"]))
          .ma(SUMS, _MA_COMPONENTS)["buckets"]["m1"], PORTAL_M1 + 61_000.0))
check("describe() exposes ma_m1_fields so the config surface can SHOW what is forced into M1",
      CL.LegClassifier(NEW_CFG).describe()["ma_m1_fields"] == [])
check("describe()'s MA line no longer tells the reader the margins are M1",
      "NOT commission" in next(s["splits_on"] for s in CL.LegClassifier(NEW_CFG).describe()["sources"]
                               if "raw_ma_commission" in s["source"]))
# RULE TWO: the classification FUNCTIONS must not branch on a carrier/tenant proper noun. Prose that
# names the source report ("VidaPay / master agent") is documentation, not a branch, so this checks the
# executable bodies of the three pure classifiers only.
import inspect as _inspect                                                   # noqa: E402
_bodies = "".join(_inspect.getsource(f).split('"""')[0] + _inspect.getsource(f).split('"""')[-1]
                  for f in (CL.ma_field_leg, CL.split_ma_components, CL.classify_label,
                            CL.bucket_for_leg, CL.classify_activation))
check("no carrier or tenant proper noun in the executable classification path",
      not re.search(r"luxelink|vidapay|total\s*wireless|cellfonz|boost", _bodies, re.I), _bodies[:200])
# (Tenant names DO appear in this module's prose — the docstring records which tenant's live figures
# proved the rule. What must stay clean is the executable path, checked above, and the DEFAULTS.)
check("the code DEFAULT config holds no tenant/carrier-specific value",
      not re.search(r"luxelink|cellfonz|vidapay|total\s*wireless", repr(CL.DEFAULT_CFG), re.I),
      CL.DEFAULT_CFG)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("I. migration 277 matches the code default")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
_mig = io.open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "database", "migrations",
                            "277_commission_leg_ma_margin_not_m1.sql"), encoding="utf-8").read()
check("277 sets the column default to the empty array", "SET DEFAULT '{}'::text[]" in _mig)
check("277 clears EXACTLY the six-column seed migration 274 wrote (order-insensitive, exact set)",
      "consumer_financing','consumer_margin','device_margin','fees_margin','rebate','wallet_funding"
      in _mig and "array_agg(x ORDER BY x)" in _mig)
check("277 is a no-op when 274 has not been run", "to_regclass('commcalc.commission_leg_config') IS NULL" in _mig)
check("277 grants nothing to anon/authenticated (contract §5)",
      not re.search(r"grant\b.*\b(anon|authenticated)\b", _mig, re.I))
check("277 creates no table and backfills no data table",
      "CREATE TABLE" not in _mig.upper() and "raw_ma_commission" not in _mig.split("--")[0])
check("277 says in plain words that no payout moves", "MOVES NO PAYOUT" in _mig)



# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("J. the GP page reads exactly the keys the GP engine emits")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
_PAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "frontend", "src", "app",
                     "(platform)", "commcalc", "gp", "page.tsx")
_page = io.open(_PAGE, encoding="utf-8").read()
_hl_keys = set(gp_new["commission_legs"]["headline"])
check("the page reads `unsplit_fields`, and the engine emits it on the MA Commission row",
      "unsplit_fields" in _page and "unsplit_fields" in _hl_keys)
check("the page reads `unsplit_why`, and the engine emits it",
      "unsplit_why" in _page and "unsplit_why" in _hl_keys)
check("the page still has the ORIGINAL label-mapping banner for ePay orgs (no MA fields -> no change)",
      "Assign them on" in _page
      and "unsplit_fields" not in (GP.calc_gp_report(
          sales=[dict(r) for r in SALES],
          pay_detail=[{"business_address": "1200 Main St", "amount": 90.0,
                       "payment_type": "Boost Auto Top-Up", "category": "Commission"}],
          mi_rows=[], rep_commissions=[], expenses=[], catalog=[],
          store_mapping=[dict(r) for r in SMAP], period="June 2026", comp_rows=[],
          leg_classify=CL.LegClassifier(NEW_CFG))["commission_legs"]["headline"]))
check("the page states that 1st Month equals what the portal states",
      "Commissions Paid" in _page)

print("\n" + "=" * 96)
print("  %d passed, %d failed" % (_P, _F))
if _FAILED:
    for n in _FAILED:
        print("   - " + n)
print("=" * 96)
sys.exit(1 if _F else 0)
