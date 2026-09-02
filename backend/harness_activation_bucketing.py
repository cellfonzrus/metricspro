"""HARNESS — config-driven Activation-Details bucketing (activation_bucketing.py, migration 313).

OWNER-APPROVED FIX (2026-09-01 recon report; applied 2026-09-02), proven DB-free:

  A. EDGE over-match fixed at the HOUSE DEFAULT: a Motorola Edge DEVICE on a Port / Activation /
     Upgrade contract no longer lands in the Edge column; a real Edge CONTRACT TYPE still does
     (whole word — 'Knowledge...' never matches). Trade-off is config, not code: an org that names
     its Edge program only in the plan NAME opts name-matching back in via `edge_name_tokens`.
  B. 'BYOD Upgrade' leaves the DISPLAYED Upgrade column (its own hidden 'BYOD Upgrade' bucket)
     while Total-Activation exclusion semantics stay IDENTICAL (both Upgrade families excluded).
  C. resolve_rules — defaults / garbage / partial config; org config only changes what it sets.
  D. Serial-dedup rank: the excluded families stay weakest; BYOD Upgrade never outranks a real
     activation line for the same device.
  E. The REAL shipped consumers (AST-extracted from commcalc/router.py, no FastAPI import):
     `_ad_cells_full` (the ONE AD source Exec MTD + Sales Report read) and the `activation_counts`
     totals loop — BYOD Upgrade rows land in `byod_upgrade`, NOT in `upgrade`/`new`, and
     total_activation excludes exactly the two Upgrade families.
  F. Router wiring — `_activation_details_bucket` DELEGATES to activation_bucketing (no re-hardcoded
     branch), and the resolver passes the per-org rules.
  G. ARMED negative control — the pre-313 classifier run through the SAME checks fails them,
     proving this harness distinguishes the bug it guards against.

Run: python3 harness_activation_bucketing.py     (stdlib-only; no DB, no FastAPI)
"""
import ast
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PASS, FAIL = [], []


def check(label, got, want=True):
    if got == want:
        PASS.append(label)
    else:
        FAIL.append(f"{label}\n      got:  {got!r}\n      want: {want!r}")


def section(t):
    print(f"\n── {t}")


# ── the pure module, imported directly by path (stdlib-only file; no package __init__ chain) ─────
_spec = importlib.util.spec_from_file_location(
    "activation_bucketing", os.path.join(HERE, "app", "modules", "commcalc", "activation_bucketing.py"))
ab = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ab)

DEFAULTS = ab.resolve_rules(None)


def bucket(ct, sp="", prod="", cat="", rules=None):
    return ab.activation_details_bucket(ct, sp, prod, cat, rules)


# ── A. Edge over-match fixed by default; contract-type Edge intact ───────────────────────────────
section("A. Edge: device-NAME match off by default, contract-type (word-boundary) on")
check("A1: Motorola Edge PORT device is a Port, not Edge",
      bucket("Port with IDV", "Total 5G+ Plan", "Motorola Edge 2025 TO - Promo $259.99", "KittedBranded"),
      "Port")
check("A2: Motorola Edge ACTIVATION device is a New Activation, not Edge",
      bucket("Activation With IDV", "", "Motorola Edge 2025", "KittedBranded"), "New Activation")
check("A3: Motorola Edge UPGRADE device joins the Upgrade family (excluded from TA), not Edge",
      bucket("Upgrade", "", "Motorola Edge 2025", "KittedBranded"), "Upgrade")
check("A4: a real Edge CONTRACT TYPE still classifies Edge",
      bucket("Edge Activation", "", "Some Phone", ""), "Edge")
check("A5: bare 'Edge' contract type classifies Edge", bucket("Edge"), "Edge")
check("A6: word-boundary — 'Knowledge Transfer' contract type is NOT Edge (falls to Other)",
      bucket("Knowledge Transfer"), "Other")
check("A7: 'edge' inside a larger token ('Edges') does not match", bucket("Edges"), "Other")
check("A8: precedence intact — Home Internet beats an edge contract token",
      bucket("Edge", "Total Wireless Home Internet", "", ""), "Home Internet")

section("A'. …and an org can opt name-matching back in (config, org-scoped)")
_name_cfg = ab.resolve_rules({"edge_name_tokens": ["edge"]})
check("A9: with edge_name_tokens configured, the device-name match returns for THAT org",
      bucket("Port with IDV", "", "Motorola Edge 2025", "", _name_cfg), "Edge")
check("A10: a narrower name token only matches what it says",
      bucket("Port", "Edge Program Plan", "", "", ab.resolve_rules({"edge_name_tokens": ["edge program"]})),
      "Edge")
check("A11: the same narrow token leaves Motorola Edge devices alone",
      bucket("Port", "", "Motorola Edge 2025", "", ab.resolve_rules({"edge_name_tokens": ["edge program"]})),
      "Port")

# ── B. BYOD Upgrade: out of the displayed Upgrade column, still an excluded family ───────────────
section("B. BYOD Upgrade family")
check("B1: 'BYOD Upgrade' classifies to its own hidden bucket", bucket("BYOD Upgrade"), "BYOD Upgrade")
check("B2: plain 'Upgrade' stays Upgrade", bucket("Upgrade"), "Upgrade")
check("B3: 'BYOD Port' stays BYOD (ordering: upgrade tested before byod, port after)",
      bucket("BYOD Port"), "BYOD")
check("B4: 'BYOD Activation' stays BYOD", bucket("BYOD Activation"), "BYOD")
check("B5: BOTH Upgrade families are the Total-Activation exclusion set",
      tuple(sorted(ab.TOTAL_ACTIVATION_EXCLUDED)), ("BYOD Upgrade", "Upgrade"))
check("B6: upgrade_hidden_contract_tokens: [] restores the single pre-313 Upgrade family",
      bucket("BYOD Upgrade", rules=ab.resolve_rules({"upgrade_hidden_contract_tokens": []})), "Upgrade")
check("B7: 'Port with IDV' is a Port — IDV never keys Port/insurance families",
      bucket("Port with IDV"), "Port")
check("B8: tablet by name still beats the phone families",
      bucket("Activation", "", "Samsung Galaxy Tab A11+", ""), "Tablet")

# ── C. resolve_rules robustness ──────────────────────────────────────────────────────────────────
section("C. resolve_rules")
check("C1: None -> house defaults",
      DEFAULTS, {"edge_contract_tokens": ["edge"], "edge_name_tokens": [],
                 "upgrade_hidden_contract_tokens": ["byod upgrade"]})
check("C2: {} -> house defaults", ab.resolve_rules({}), DEFAULTS)
check("C3: garbage value -> house defaults", ab.resolve_rules("not a dict"), DEFAULTS)
check("C4: non-list key values fall back per-key",
      ab.resolve_rules({"edge_contract_tokens": "edge"})["edge_contract_tokens"], ["edge"])
check("C5: tokens are trimmed + lowercased; empties dropped",
      ab.resolve_rules({"edge_name_tokens": ["  EdGe Plan ", "", None]})["edge_name_tokens"],
      ["edge plan"])
check("C6: a partial config only changes the keys it sets",
      ab.resolve_rules({"edge_name_tokens": ["edge"]})["upgrade_hidden_contract_tokens"],
      ["byod upgrade"])
check("C7: unknown keys are ignored", ab.resolve_rules({"bogus": ["x"]}), DEFAULTS)

# ── D. Serial-dedup rank ─────────────────────────────────────────────────────────────────────────
section("D. dedup rank")
check("D1: BYOD Upgrade ranks with Upgrade (weakest, 0)",
      (ab.BUCKET_RANK["BYOD Upgrade"], ab.BUCKET_RANK["Upgrade"]), (0, 0))


def _dedup_winner(buckets):
    """The router's serial-dedup rule: first line wins its slot; a later line replaces it only when
    STRICTLY stronger (router.py: rank(bucket) > rank(prev))."""
    win = buckets[0]
    for b in buckets[1:]:
        if ab.BUCKET_RANK.get(b, 1) > ab.BUCKET_RANK.get(win, 1):
            win = b
    return win


check("D2: a device with a BYOD Upgrade line AND a Port line counts as the Port",
      _dedup_winner(["BYOD Upgrade", "Port"]), "Port")
check("D3: …in either line order", _dedup_winner(["Port", "BYOD Upgrade"]), "Port")
check("D4: an Other line still beats the excluded families", _dedup_winner(["BYOD Upgrade", "Other"]), "Other")

# ── E. the REAL shipped consumers, AST-extracted from router.py ──────────────────────────────────
section("E. shipped consumers (_ad_cells_full / activation_counts totals loop)")
_router_path = os.path.join(HERE, "app", "modules", "commcalc", "router.py")
with open(_router_path, encoding="utf-8") as _fh:
    _router_src = _fh.read()
_tree = ast.parse(_router_src)


def _fn_src(name):
    for n in ast.walk(_tree):
        if isinstance(n, ast.FunctionDef) and n.name == name:
            return ast.get_source_segment(_router_src, n)
    return None


_AD_ROWS = [
    # (store, rep, date, bucket) — a Port, a plain Upgrade, a BYOD Upgrade, a New, an Edge-by-contract
    {"store": "S1", "salesperson": "rep", "trans_date": "2026-08-01", "bucket": "Port"},
    {"store": "S1", "salesperson": "rep", "trans_date": "2026-08-01", "bucket": "Upgrade"},
    {"store": "S1", "salesperson": "rep", "trans_date": "2026-08-01", "bucket": "BYOD Upgrade"},
    {"store": "S1", "salesperson": "rep", "trans_date": "2026-08-01", "bucket": "New Activation"},
    {"store": "S1", "salesperson": "rep", "trans_date": "2026-08-01", "bucket": "Edge"},
]

# _ad_cells_full — exec the real source with its ONE free dependency stubbed.
_cells_src = _fn_src("_ad_cells_full")
check("E0: _ad_cells_full found in router.py", _cells_src is not None)
_g = {"_cr_resolve_activation_details": (lambda client, org_id, period, ctx: list(_AD_ROWS))}
exec(_cells_src, _g)                                   # noqa: S102 — the shipped source, under test
_cells, _n = _g["_ad_cells_full"](None, "org", "August 2026")
_slot = _cells[("s1", "rep", "2026-08-01")]
check("E1: BYOD Upgrade lands in its own hidden byod_upgrade count", _slot["byod_upgrade"], 1)
check("E2: the DISPLAYED upgrade column counts ONLY plain Upgrade", _slot["upgrade"], 1)
check("E3: BYOD Upgrade is NOT folded into `new`", _slot["new"], 1)
check("E4: Total Activation (new+port+byod+tablet+hi+edge) excludes BOTH Upgrade families",
      _slot["new"] + _slot["port"] + _slot["byod"] + _slot["tablet"]
      + _slot["home_internet"] + _slot["edge"], 3)     # Port + New + Edge
check("E5: row count still reports every AD row (nothing silently dropped)", _n, 5)

# activation_counts' totals rule — exec the real endpoint source under stubs and read its grand row.
_ac_src = _fn_src("activation_counts")
check("E6: activation_counts found in router.py", _ac_src is not None)
_ACT_BUCKET_FIELD = {}
for n in ast.walk(_tree):
    if isinstance(n, ast.Assign) and any(getattr(t, "id", "") == "_ACT_BUCKET_FIELD" for t in n.targets):
        _ACT_BUCKET_FIELD = ast.literal_eval(n.value)
check("E7: _ACT_BUCKET_FIELD carries the hidden family", _ACT_BUCKET_FIELD.get("BYOD Upgrade"), "byod_upgrade")
_g2 = {
    "require_org": (lambda org: None),
    "sb": (lambda: None),
    "_market_for_fn": (lambda client, org_id: (lambda s: "")),
    "_cr_resolve_activation_details": (lambda client, org_id, period, ctx: list(_AD_ROWS)),
    "_norm_report_date": (lambda v: str(v or "")),
    "_ACT_BUCKET_FIELD": _ACT_BUCKET_FIELD,
    "_act_bucketing": ab,
    "ORG_ID": "org",
}
exec(_ac_src, _g2)                                     # noqa: S102 — the shipped source, under test
_res = _g2["activation_counts"]("August 2026", org_id="org")
check("E8: total_activation excludes BOTH Upgrade families (3 = Port+New+Edge)",
      _res["total"]["total_activation"], 3)
check("E9: total_with_upgrade still counts every device (5)", _res["total"]["total_with_upgrade"], 5)
check("E10: the response's upgrade column shows only the plain Upgrade", _res["total"]["upgrade"], 1)
check("E11: the hidden family is visible as its own additive field", _res["total"]["byod_upgrade"], 1)

# ── F. router wiring ─────────────────────────────────────────────────────────────────────────────
section("F. router wiring")
_adb_src = _fn_src("_activation_details_bucket") or ""
check("F1: _activation_details_bucket DELEGATES to activation_bucketing (no re-hardcoded branch)",
      "_act_bucketing.activation_details_bucket(" in _adb_src and '"edge" in nm' not in _adb_src)
_resolver_src = _fn_src("_cr_resolve_activation_details") or ""
check("F2: the resolver loads per-org rules once and passes them to every bucket call",
      "_activation_details_rules(client, org_id)" in _resolver_src
      and "_activation_details_bucket(ct, sp, prod, cat, _ad_rules)" in _resolver_src)
_rules_src = _fn_src("_activation_details_rules") or ""
check("F3: the rules loader is org-scoped and resolves through the house defaults",
      '.eq("org_id", org_id)' in _rules_src and "resolve_rules" in _rules_src)
_adex = {}
for n in ast.walk(_tree):
    if isinstance(n, ast.Assign) and any(getattr(t, "id", "") == "_AD_EXEC_KEY" for t in n.targets):
        _adex = ast.literal_eval(n.value)
check("F4: _AD_EXEC_KEY keeps BYOD Upgrade OUT of the folded 'activation' default and out of 'upgrade'",
      _adex.get("BYOD Upgrade"), "byod_upgrade")

# ── G. ARMED negative control — the pre-313 classifier fails these same checks ───────────────────
section("G. armed negative control")


def _old_bucket(ct, sp="", prod="", cat=""):
    """The pre-313 hardcoded classifier, verbatim semantics."""
    ctl = str(ct or "").lower()
    nm = f"{sp or ''} {prod or ''} {cat or ''}".lower()
    if "home internet" in nm or "fwa" in nm or "fixed wireless" in nm:
        return "Home Internet"
    if "edge" in nm or "edge" in ctl:
        return "Edge"
    if "tablet" in nm or "galaxy tab" in nm:
        return "Tablet"
    if "upgrade" in ctl:
        return "Upgrade"
    if "byod" in ctl or "customer phone" in nm:
        return "BYOD"
    if "port" in ctl:
        return "Port"
    if "activation" in ctl:
        return "New Activation"
    return "Other"


check("G1: control is ARMED — the old classifier DOES put a Motorola Edge Port device in Edge "
      "(so check A1 genuinely distinguishes the bug)",
      _old_bucket("Port with IDV", "", "Motorola Edge 2025", "KittedBranded"), "Edge")
check("G2: control is ARMED — the old classifier DOES put BYOD Upgrade in the displayed Upgrade "
      "family (so checks B1/E1-E2 genuinely distinguish the bug)",
      _old_bucket("BYOD Upgrade"), "Upgrade")
check("G3: fixed vs old classifiers actually DISAGREE on the two bug vectors",
      (bucket("Port with IDV", "", "Motorola Edge 2025", "KittedBranded")
       != _old_bucket("Port with IDV", "", "Motorola Edge 2025", "KittedBranded"))
      and (bucket("BYOD Upgrade") != _old_bucket("BYOD Upgrade")))

# ── report ───────────────────────────────────────────────────────────────────────────────────────
print(f"\n{'='*76}")
for p in PASS:
    print(f"  PASS  {p}")
if FAIL:
    print()
    for f in FAIL:
        print(f"  FAIL  {f}")
print(f"{'='*76}")
print(f"{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
