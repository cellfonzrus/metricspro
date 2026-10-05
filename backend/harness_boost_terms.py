"""HARNESS — boost_terms.py + payout_structure.build_boost_doc (the Boost employee handout).

Two risks, and every section below targets one of them.

RISK ONE — THE REFACTOR MOVED MONEY. `calc_rep_commissions` built its `G` dict and its `KPI` target
map inline; both moved into `boost_terms` so the employee document could read the same numbers the
engine pays. A refactor of a pay path is only safe if it is BYTE-IDENTICAL, so §A replays the retired
inline expressions — pasted here verbatim, as the thing being compared against, not re-derived — over
a grid of config shapes chosen to hit every fallback branch, including the two `or 0.10` quirks that
are deliberately preserved. §B does the same end-to-end through the real engine.

RISK TWO — THE DOCUMENT PRINTS A RATE NOBODY IS PAID. An employee reads this PDF as policy. §C–§F
assert that what the document says matches what `calc_rep_commissions` would actually pay for the
same config, that a zeroed term is LABELLED rather than dropped, that a metric with no target is
left out rather than printed as a bar of 0, and that the tier ladder matches the engine's own
comparison. §G renders. §H is the armed negative control.

The fixtures in §A/§B include CELLFONZ R US'S REAL October 2026 row (measured live 2026-10-05) rather
than only invented ones, so a passing harness means the real handout is right.

Run: python3 harness_boost_terms.py     (no DB, no network — pure fixtures)
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

from app.modules.commcalc import boost_terms as bt            # noqa: E402
from app.modules.commcalc import calculator as calc           # noqa: E402
from app.modules.commcalc import kpi_failing as kf            # noqa: E402
from app.modules.commcalc import payout_structure as ps       # noqa: E402

PASS, FAIL, SKIPPED = [], [], []


def check(name, got, want):
    if got == want:
        PASS.append(name)
    else:
        FAIL.append(f"{name}: got {got!r}, want {want!r}")


def ok(name, cond):
    (PASS.append(name) if cond else FAIL.append(name))


def section(t):
    print(f"\n── {t} " + "─" * max(0, 76 - len(t)))


def _pdf_backend():
    """('run'|'skip'|'defect', message) — the same gate harness_payout_structure.py uses: a MISSING
    but DECLARED reportlab is an environment gap and skips loudly; a missing and UNDECLARED one is a
    production defect and fails."""
    import importlib.util
    installed = importlib.util.find_spec("reportlab") is not None
    req = os.path.join(_HERE, "requirements.txt")
    declared = False
    try:
        with open(req) as fh:
            declared = any(ln.strip().lower().startswith("reportlab")
                           and not ln.strip().startswith("#") for ln in fh)
    except OSError:
        pass
    if installed:
        return "run", "reportlab installed"
    if declared:
        return "skip", "reportlab declared in requirements.txt but not installed in this container"
    return "defect", "reportlab is imported by shipped code but NOT declared in requirements.txt"


PDF_MODE, PDF_WHY = _pdf_backend()


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# THE RETIRED EXPRESSIONS — pasted verbatim from calculator.calc_rep_commissions as they stood before
# 2026-10-05. These are the ORACLE. They must never be "tidied": the whole point is that they are the
# old code, not a fresh reading of it.
# ══════════════════════════════════════════════════════════════════════════════════════════════════
def _retired_G(cfg):
    return {
        'upgrade_flat':     cfg.get('upgrade_flat') if cfg.get('upgrade_flat') is not None else 20,
        'premium_flat':     cfg.get('premium_flat') if cfg.get('premium_flat') is not None else 5,
        'byod_flat':        cfg.get('byod_flat') if cfg.get('byod_flat') is not None else 3,
        'byod_extra':       cfg.get('byod_extra_spiff') or 0,
        'trade_in_spiff':   cfg.get('trade_in_spiff') if cfg.get('trade_in_spiff') is not None else 20,
        'acima_spiff':      cfg.get('acima_spiff') if cfg.get('acima_spiff') is not None else 25,
        'acc_rate':         cfg.get('acc_rate') or 0.10,
        'setup_rate':       cfg.get('setup_fee_rate') or 0.10,
        'acc_target_on':    bool(cfg.get('acc_target_enabled', False)),
        'acc_target_pct':   cfg.get('acc_target_pct') or 0.10,
        'custom_spiffs':    cfg.get('custom_spiffs') or [],
        'straight':         bool(cfg.get('straight_line', False)),
        't100':             int(cfg.get('tier_100_min_kpis') or 7),
        't75':              int(cfg.get('tier_75_min_kpis') or 5),
        't75pct':           float(cfg.get('tier_75_pct') or 0.75),
        't50pct':           float(cfg.get('tier_50_pct') or 0.50),
    }


def _retired_KPI(cfg, defs):
    KPI = {}
    for (_k, _label, _col, _dflt) in defs:
        _t = cfg.get(_col)
        if not _t:
            _t = _dflt
        try:
            _t = float(_t) if _t is not None else None
        except (TypeError, ValueError):
            _t = None
        if _t:
            KPI[_k] = _t
    return KPI


# Cellfonz R Us, October 2026 — the live row, measured 2026-10-05.
LIVE_OCT = {
    "period": "October 2026", "upgrade_flat": 0, "premium_flat": 5, "byod_flat": 3,
    "byod_extra_spiff": 0, "trade_in_spiff": 5, "acima_spiff": 25, "acc_rate": 0.1,
    "setup_fee_rate": 0.1, "kpi_atu_target": 55, "kpi_protect_target": 80,
    "kpi_boostapp_target": 65, "kpi_familyplan_target": 45, "kpi_byod_target": 35,
    "kpi_tmr3_target": 70, "kpi_aal_target": 5, "tier_100_min_kpis": 7, "tier_75_min_kpis": 5,
    "tier_75_pct": 0.75, "tier_50_pct": 0.5, "straight_line": False, "acc_target_enabled": False,
    "acc_target_pct": 0.1, "custom_spiffs": [],
}
# Cellfonz R Us, May 2026 — the live row where tier_100_min_kpis was 5, not 7.
LIVE_MAY = dict(LIVE_OCT, period="May 2026", upgrade_flat=5, byod_flat=5, trade_in_spiff=2,
                tier_100_min_kpis=5)

CONFIG_GRID = [
    ("live October 2026", LIVE_OCT),
    ("live May 2026", LIVE_MAY),
    ("empty — every default", {}),
    ("None — no row at all", None),
    # Every key explicitly ZERO. This is the shape that separates the two fallback styles: the flats
    # honour a stored 0, the two rates fall back to 10% (the preserved quirk).
    ("all zeros", {"upgrade_flat": 0, "premium_flat": 0, "byod_flat": 0, "byod_extra_spiff": 0,
                   "trade_in_spiff": 0, "acima_spiff": 0, "acc_rate": 0, "setup_fee_rate": 0,
                   "acc_target_pct": 0, "tier_100_min_kpis": 0, "tier_75_min_kpis": 0,
                   "tier_75_pct": 0, "tier_50_pct": 0, "custom_spiffs": []}),
    # Every key explicitly None — the `is not None` branch must take the default, and `or` likewise.
    ("all None", {"upgrade_flat": None, "premium_flat": None, "byod_flat": None,
                  "byod_extra_spiff": None, "trade_in_spiff": None, "acima_spiff": None,
                  "acc_rate": None, "setup_fee_rate": None, "acc_target_pct": None,
                  "tier_100_min_kpis": None, "tier_75_min_kpis": None, "tier_75_pct": None,
                  "tier_50_pct": None, "custom_spiffs": None, "straight_line": None}),
    ("straight line + accessory target", dict(LIVE_OCT, straight_line=True,
                                              acc_target_enabled=True, acc_target_pct=0.12)),
    ("custom spiffs + byod extra", dict(LIVE_OCT, byod_extra_spiff=2,
                                        custom_spiffs=[{"name": "Tablet", "rate": 7},
                                                       {"name": "", "rate": 0}])),
    ("floats everywhere", {"upgrade_flat": 1.5, "premium_flat": 2.25, "byod_flat": 0.5,
                           "acc_rate": 0.075, "setup_fee_rate": 0.125, "tier_75_pct": 0.8,
                           "tier_50_pct": 0.4, "tier_100_min_kpis": 6, "tier_75_min_kpis": 3}),
]

DEFS = kf.resolve_defs(None)
# A tenant registry whose labels differ from the built-ins AND which carries a metric with no target
# at all — the case the document must leave OUT rather than print as "0 or better".
TENANT_DEFS = (
    ("atu", "ATU", "kpi_atu_target", 55),
    ("protect", "Protection %", "kpi_protect_target", 80),
    ("boostapp", "App Attach %", "kpi_boostapp_target", 65),
    ("doorcount", "Door count", "kpi_doorcount_target", None),
)


section("A. BYTE-IDENTICAL — resolve_terms / resolve_kpi_targets vs the retired inline code")
for label, cfg in CONFIG_GRID:
    check(f"A-terms  {label}", bt.resolve_terms(cfg), _retired_G(cfg or {}))
    check(f"A-targets {label}", bt.resolve_kpi_targets(cfg, DEFS), _retired_KPI(cfg or {}, DEFS))
    check(f"A-targets(tenant defs) {label}",
          bt.resolve_kpi_targets(cfg, TENANT_DEFS), _retired_KPI(cfg or {}, TENANT_DEFS))

# The two preserved quirks, asserted BY NAME so nobody "fixes" one without this harness saying so.
check("A-quirk acc_rate 0 falls back to 10%", bt.resolve_terms({"acc_rate": 0})["acc_rate"], 0.10)
check("A-quirk setup_fee_rate 0 falls back to 10%",
      bt.resolve_terms({"setup_fee_rate": 0})["setup_rate"], 0.10)
check("A-not-a-quirk premium_flat 0 stays 0", bt.resolve_terms({"premium_flat": 0})["premium_flat"], 0)
check("A-not-a-quirk upgrade_flat 0 stays 0", bt.resolve_terms({"upgrade_flat": 0})["upgrade_flat"], 0)
check("A-falsy target is NO target, not 0",
      "doorcount" in bt.resolve_kpi_targets({"kpi_doorcount_target": 0}, TENANT_DEFS), False)
check("A-stored target wins over the definition default",
      bt.resolve_kpi_targets({"kpi_atu_target": 61}, TENANT_DEFS)["atu"], 61.0)
check("A-unparseable target drops out",
      "atu" in bt.resolve_kpi_targets({"kpi_atu_target": "sixty"}, TENANT_DEFS), False)
ok("A-keys unchanged", set(bt.resolve_terms({})) == set(_retired_G({})))
ok("A-DEFAULTS agree with an empty resolve", all(
    bt.resolve_terms({})[k] == v for k, v in bt.DEFAULTS.items()))


section("B. END TO END — the real engine pays the same before and after the move")


def _line(tid, ct, **kw):
    r = {"trans_id": tid, "salesperson": "RE P", "user_login": "rep", "store": "1234 Main St",
         "contract_type": ct, "voided": "NO", "trans_type": "Sale", "department": "",
         "category": "", "product_desc": "", "gp": 0, "ext_price": 0, "tender_type": ""}
    r.update(kw)
    return r


SALES = ([_line(f"T{i}", "New Activation") for i in range(10)]
         + [_line(f"B{i}", "BYOD Activation") for i in range(5)]
         + [_line(f"U{i}", "Upgrade") for i in range(4)]
         + [_line("A1", "", department="Ondigo", ext_price=2000, gp=800),
            _line("S1", "", product_desc="Device Setup Charge", ext_price=600, gp=600),
            _line("AC1", "New Activation", tender_type="ACIMA Lease")])
DLAR_REP_HIGH = [{"rep_name": "RE P", "store": "1234 Main St", "atu_pct": 60,
                  "device_insurance_pct": 85, "boost_app_pct": 70, "byod_pct": 40}]
DLAR_STORE_HIGH = [{"address": "1234 Main St", "family_plan_pct": 50, "tmr3": 72,
                    "aal_conversion": 6}]
DLAR_REP_LOW = [dict(DLAR_REP_HIGH[0], atu_pct=40, device_insurance_pct=60, boost_app_pct=50,
                     byod_pct=20)]
DLAR_STORE_LOW = [dict(DLAR_STORE_HIGH[0], aal_conversion=2)]


def _pay(cfg, dr, ds):
    out = calc.calc_rep_commissions(SALES, [], dr, ds, [], [], cfg or {}, [], [], [], [],
                                    "October 2026", [])
    return out["commissions"][0]


r_hi = _pay(LIVE_OCT, DLAR_REP_HIGH, DLAR_STORE_HIGH)
r_lo = _pay(LIVE_OCT, DLAR_REP_LOW, DLAR_STORE_LOW)
check("B1 subtotal on the live October row", r_hi["subtotal"], 355.0)
check("B2 all seven met -> tier 1.0", (r_hi["kpis_met"], r_hi["total_kpis"], r_hi["tier"]), (7, 7, 1.0))
check("B3 full tier pays the whole subtotal", r_hi["total_payout"], 355.0)
check("B4 two met -> tier 0.5", (r_lo["kpis_met"], r_lo["tier"]), (2, 0.5))
check("B5 half tier pays half", r_lo["total_payout"], 177.5)
check("B6 upgrade at $0 earns nothing", r_hi["upgrade_comm"], 0)
check("B7 accessories pay on the SALE, not the profit", r_hi["acc_comm"], 200.0)
# The identity the whole engine rests on, over the whole grid.
for label, cfg in CONFIG_GRID:
    row = _pay(cfg, DLAR_REP_HIGH, DLAR_STORE_HIGH)
    ok(f"B-identity subtotal x tier == payout ({label})",
       abs(row["subtotal"] * row["tier"] - row["total_payout"]) < 1e-9)
    ok(f"B-engine reads the one home ({label})",
       row["premium_comm"] == 11 * bt.resolve_terms(cfg)["premium_flat"])


section("C. THE DOCUMENT SAYS WHAT THE ENGINE PAYS")
doc = ps.build_boost_doc(LIVE_OCT, DEFS, tenant_name="Cellfonz R Us", period="October 2026")
plan = doc["plans"][0]
rates = {i["what"]: i["rate"] for i in plan["pay_items"]}
check("C1 premium rate printed", rates.get("Premium activation"), "$5.00")
check("C2 BYOD rate printed", rates.get("BYOD activation"), "$3.00")
check("C3 accessory rate printed", rates.get("Accessories"), "10% of the sale")
check("C4 set-up fee rate printed", rates.get("Device set-up fee"), "10% of the fee")
check("C5 trade-in spiff printed", rates.get("Trade-in"), "$5.00")
check("C6 acima spiff printed", rates.get("Acima lease"), "$25.00")
# The document's premium rate IS the engine's, for every config in the grid — not a parallel reading.
for label, cfg in CONFIG_GRID:
    d = ps.build_boost_doc(cfg, DEFS, period="October 2026")
    shown = {i["what"]: i["rate"] for i in d["plans"][0]["pay_items"]}
    g = bt.resolve_terms(cfg)
    want = ps.money(g["premium_flat"]) if float(g["premium_flat"] or 0) else None
    ok(f"C-premium agrees with the engine ({label})", shown.get("Premium activation") == want)
check("C7 BYOD rate is base PLUS the spiff",
      {i["what"]: i["rate"] for i in
       ps.build_boost_doc(dict(LIVE_OCT, byod_extra_spiff=2), DEFS)["plans"][0]["pay_items"]
       }.get("BYOD activation"), "$5.00")
check("C8 a named custom spiff is printed",
      any(i["what"] == "Spiff: Tablet" for i in ps.build_boost_doc(
          dict(LIVE_OCT, custom_spiffs=[{"name": "Tablet", "rate": 7}]),
          DEFS)["plans"][0]["pay_items"]), True)
check("C9 an UNNAMED custom spiff is not printed",
      [i["what"] for i in ps.build_boost_doc(
          dict(LIVE_OCT, custom_spiffs=[{"name": "", "rate": 9}]),
          DEFS)["plans"][0]["pay_items"] if i["what"].startswith("Spiff")], [])


section("D. A ZEROED TERM IS LABELLED, NEVER DROPPED")
zeroed = {i["what"] for i in plan["no_pay_items"]}
check("D1 upgrade at $0 appears as not-paid", "Upgrade" in zeroed, True)
check("D2 ...and NOT in the pay table", "Upgrade" in rates, False)
allz = ps.build_boost_doc({"upgrade_flat": 0, "premium_flat": 0, "byod_flat": 0,
                           "trade_in_spiff": 0, "acima_spiff": 0}, DEFS)["plans"][0]
check("D3 every term zero -> nothing pays", allz["pay_items"] == [], False)  # acc/setup still 10%
check("D4 the four flats are all labelled not-paid",
      {"Premium activation", "BYOD activation", "Upgrade", "Trade-in", "Acima lease"}
      <= {i["what"] for i in allz["no_pay_items"]}, True)
check("D5 a zeroed term carries a reason a reader can act on",
      all(i["why"] for i in allz["no_pay_items"]), True)


section("E. THE MEASURE TABLE — the bar, and no fabricated bar")
kpis = {r["label"]: r for r in plan["kpis"]["rows"]}
check("E1 all seven built-ins are listed", len(kpis), 7)
check("E2 ATU target", kpis["ATU"]["target"], "55 or better")
check("E3 AAL target is not printed as a percentage", kpis["AAL"]["target"], "5 or better")
check("E4 a rep-grain measure says so", kpis["ATU"]["grain"], "your own numbers")
check("E5 a store-grain measure says so", kpis["Family Plan"]["grain"], "your store")
tdoc = ps.build_boost_doc(LIVE_OCT, TENANT_DEFS, period="October 2026")["plans"][0]
tlabels = [r["label"] for r in tdoc["kpis"]["rows"]]
check("E6 the tenant's OWN labels are used", "Protection %" in tlabels, True)
check("E7 a metric with no target anywhere is LEFT OUT, not printed as 0",
      "Door count" in tlabels, False)
check("E8 the footnote counts the measures actually printed",
      f"fewer than {len(tlabels)}" in " ".join(tdoc and ps.build_boost_doc(
          LIVE_OCT, TENANT_DEFS, period="October 2026")["footnotes"]), True)


section("F. THE TIER LADDER MATCHES THE ENGINE'S OWN COMPARISON")
G = bt.resolve_terms(LIVE_OCT)
tiers = plan["tiers"]
check("F1 full-rate threshold is the engine's t100", tiers["rows"][0]["min_count"], G["t100"])
check("F2 reduced threshold is the engine's t75", tiers["rows"][1]["min_count"], G["t75"])
check("F3 reduced multiplier is the engine's t75pct", tiers["rows"][1]["multiplier"], "75%")
check("F4 below the ladder is the engine's t50pct", tiers["below"], "50%")
# Replay the engine's own `if kpis_met >= t100 ... elif >= t75 ... else` against the printed ladder.
for met in range(0, 9):
    engine = 1.0 if met >= G["t100"] else (G["t75pct"] if met >= G["t75"] else G["t50pct"])
    if met >= tiers["rows"][0]["min_count"]:
        shown = 1.0
    elif met >= tiers["rows"][1]["min_count"]:
        shown = float(tiers["rows"][1]["multiplier"].rstrip("%")) / 100.0
    else:
        shown = float(tiers["below"].rstrip("%")) / 100.0
    ok(f"F-ladder {met} met reads as the engine pays", abs(engine - shown) < 1e-9)
sdoc = ps.build_boost_doc(dict(LIVE_OCT, straight_line=True), DEFS)["plans"][0]
check("F5 straight-line prints NO ladder", sdoc["tiers"], None)
check("F6 ...and says why", any("straight-line" in w for w in sdoc["warnings"]), True)
udoc = ps.build_boost_doc(LIVE_OCT, TENANT_DEFS, period="October 2026")["plans"][0]
check("F7 an unreachable full rate is stated, not hidden",
      any("cannot be reached" in w for w in udoc["warnings"]), True)
adoc = ps.build_boost_doc(dict(LIVE_OCT, acc_target_enabled=True), DEFS)
check("F8 the accessory-target cap is disclosed",
      any("accessory target" in f for f in adoc["footnotes"]), True)


section("G. IT RENDERS, AND HOSTILE TEXT CANNOT BREAK IT")
check("G1 the model is JSON-serialisable", isinstance(__import__("json").dumps(doc), str), True)
check("G2 the download name is stable",
      ps.filename_for(doc), "cellfonz-r-us-payout-structure-" +
      doc["generated_at"].lower().replace(" ", "-").replace(",", "-").replace("--", "-") + ".pdf")
check("G3 the period is on the model so the masthead can print it", doc["period"], "October 2026")
hostile = ps.build_boost_doc(
    dict(LIVE_OCT, custom_spiffs=[{"name": "<b>A & B</b> <script>", "rate": 3}]),
    DEFS, tenant_name="Tenant & <Co>", period="October 2026")
if PDF_MODE == "run":
    for nm, d in (("G4 live document", doc), ("G5 hostile text", hostile),
                  ("G6 empty config", ps.build_boost_doc({}, DEFS)),
                  ("G7 straight line", ps.build_boost_doc(dict(LIVE_OCT, straight_line=True), DEFS))):
        try:
            b = ps.render_pdf(d)
            ok(f"{nm} renders a PDF", b[:4] == b"%PDF" and len(b) > 1200)
        except Exception as e:                                        # noqa: BLE001
            FAIL.append(f"{nm} raised {type(e).__name__}: {e}")
    # The plan engine's own document must still render — this change touched its renderer.
    try:
        pd = ps.build_doc([{"id": "p1", "name": "Plan", "is_active": True, "rules": [
            {"label": "acc", "match_field": "department", "match_op": "in",
             "match_value": "Accessories", "rate_kind": "pct_price", "pct": 0.1, "amount": 10.0}]}],
            tenant_name="T")
        b = ps.render_pdf(pd)
        ok("G8 the PLAN document still renders (no regression)", b[:4] == b"%PDF")
        ok("G9 a plan document carries no `kpis` key", pd["plans"][0].get("kpis") is None)
    except Exception as e:                                            # noqa: BLE001
        FAIL.append(f"G8 plan document raised {type(e).__name__}: {e}")
elif PDF_MODE == "defect":
    FAIL.append(f"G PRODUCTION DEFECT — {PDF_WHY}")
else:
    SKIPPED.append("G4-G9 (PDF render)")


section("H. ARMED negative control — these MUST fail if the checks above are real")
_f_before = len(FAIL)
check("H-armed premium", rates.get("Premium activation"), "$20.00")            # the WRONG rate
check("H-armed ladder", tiers["rows"][0]["min_count"], 99)                     # the WRONG threshold
check("H-armed quirk", bt.resolve_terms({"acc_rate": 0})["acc_rate"], 0)       # the WRONG quirk
_fired = len(FAIL) - _f_before
if _fired == 3:
    FAIL[:] = FAIL[:_f_before]
    PASS.append("H1 negative control fired on all three wrong expectations (checks are live)")
else:
    FAIL.append(f"H1 NEGATIVE CONTROL DID NOT FIRE — {_fired}/3 wrong answers were accepted. "
                f"The assertions above prove nothing.")

print(f"\n{'=' * 78}")
for f in FAIL:
    print(f"  ✗ {f}")
print(f"  PASS {len(PASS)} / {len(PASS) + len(FAIL)}")
if SKIPPED:
    print(f"  SKIPPED {len(SKIPPED)} (NOT passed): {', '.join(SKIPPED)}")
    print(f"  reason: {PDF_WHY}")
print(f"{'=' * 78}")
sys.exit(1 if FAIL else 0)
