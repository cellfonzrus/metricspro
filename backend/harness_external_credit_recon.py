"""PROOF — external-credit-machine field + card-settlement TALLY (owner directive 2026-09-04,
migs 960/961). DB-free, framework-free, stdlib only:

    python3 backend/harness_external_credit_recon.py

Sections
  A  config resolution: tender → processor ROLE (house default < per-org closing_tender_def rows)
  B  the DECLARED leg (store-day grain, custom-tender JSONB, store-code normalization)
  C  the mig-961 DM SPLIT — the money invariant: the corrected CARD TOTAL never moves
  D  the settlement ADAPTER (report_pull_map column_map, both directions; merchant→store; unmapped)
  E  the TALLY truth table — and that its verdict IS envelope_report.count_fields, not a copy
  F  honest gaps: no_processor_data / no_declared_data / dm_merged never fabricate a variance
  G  totals + status filter
  H  RULE TWO: no tenant/carrier/processor BRAND anywhere in the module or the migrations
"""
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "app", "modules"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "app"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MOD_DIR = os.path.join(ROOT, "backend", "app", "modules", "closing")


def _load(name):
    """Import a closing module WITHOUT importing the package (whose __init__/router pull FastAPI)."""
    import importlib.util
    import types
    pkg = "closing_pure"
    if pkg not in sys.modules:
        p = types.ModuleType(pkg)
        p.__path__ = [MOD_DIR]
        sys.modules[pkg] = p
    spec = importlib.util.spec_from_file_location(f"{pkg}.{name}", os.path.join(MOD_DIR, name + ".py"))
    m = importlib.util.module_from_spec(spec)
    sys.modules[f"{pkg}.{name}"] = m
    spec.loader.exec_module(m)
    return m


envelope_report = _load("envelope_report")
verified_overlay = _load("verified_overlay")
verification_audit = _load("verification_audit")
ecr = _load("external_credit_recon")

PASS, FAIL = [], []


def check(label, cond, detail=""):
    (PASS if cond else FAIL).append(label)
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f"  — {detail}" if detail and not cond else ""))


def head(t):
    print(f"\n{t}\n" + "-" * len(t))


EXT, POS = ecr.EXTERNAL_CC, ecr.POS_MERCHANT

# ══ A. tender → processor ROLE config ════════════════════════════════════════════════════════════
head("A. config: which declared tender feeds which processor role")

check("house default: ext_cc → external_cc, credit → pos_merchant",
      ecr.tender_processor_map() == {"ext_cc": EXT, "credit": POS})
check("no tender_def rows ⇒ house default columns (t_ext_cc / t_credit)",
      ecr.role_columns([]) == {EXT: ["t_ext_cc"], POS: ["t_credit"]},
      ecr.role_columns([]))
check("per-org row re-points a tender to another role",
      ecr.role_columns([{"tender_key": "zelle", "processor_key": POS, "is_active": True}])[POS]
      == ["t_credit", "t_zelle"])
check("a CUSTOM tender (mig 111, no t_* column) maps to its JSONB key",
      ecr.role_columns([{"tender_key": "white_terminal", "processor_key": EXT, "is_active": True}])[EXT]
      == ["t_ext_cc", "white_terminal"])
check("inactive tender_def row is ignored",
      ecr.role_columns([{"tender_key": "zelle", "processor_key": POS, "is_active": False}])[POS]
      == ["t_credit"])
check("unknown role slug OPTS THE TENDER OUT (never invents a phantom leg)",
      ecr.tender_processor_map([{"tender_key": "ext_cc", "processor_key": "whatever"}]) == {"credit": POS})
check("blank processor_key leaves the house default alone",
      ecr.tender_processor_map([{"tender_key": "ext_cc", "processor_key": ""}])["ext_cc"] == EXT)

# ══ B. the DECLARED leg ══════════════════════════════════════════════════════════════════════════
head("B. declared leg — the employees' closing rows at store-day grain")


def crow(store, day, credit=0.0, ext=0.0, tenders=None):
    return {"store_code": store, "close_date": day, "t_credit": credit, "t_ext_cc": ext,
            "tenders": tenders}


rows = [crow("S1", "2026-09-01", 100.0, 40.0),
        crow("S1", "2026-09-01", 50.0, 10.0),      # second rep, same store-day
        crow("s1", "2026-09-02", 0.0, 25.0),       # lower-case store code, same store
        crow("S2", "2026-09-01", 70.0, 0.0)]
dec = ecr.declared_cells(rows)
check("two reps on one store-day SUM into one cell",
      dec[("S1", "2026-09-01")][EXT]["amount"] == 50.0 and
      dec[("S1", "2026-09-01")][POS]["amount"] == 150.0, dec[("S1", "2026-09-01")])
check("store code normalizes ('s1' and 'S1' are one store)",
      ("S1", "2026-09-02") in dec and ("s1", "2026-09-02") not in dec)
check("a store with no external tender declares 0.00, not None",
      dec[("S2", "2026-09-01")][EXT]["amount"] == 0.0)
check("every declared leg starts on the REP basis", all(
    leg["basis"] == "rep" for slot in dec.values() for leg in slot.values()))
cust = ecr.declared_cells([crow("S3", "2026-09-01", 0.0, 0.0, {"white_terminal": 33.0})],
                          ecr.role_columns([{"tender_key": "white_terminal",
                                             "processor_key": EXT, "is_active": True}]))
check("custom-tender JSONB money reaches the external leg",
      cust[("S3", "2026-09-01")][EXT]["amount"] == 33.0)
check("a row with no close_date is dropped, never keyed on ''",
      ecr.declared_cells([crow("S1", None, 5.0, 5.0)]) == {})

# ══ C. the mig-961 DM SPLIT — THE MONEY INVARIANT ════════════════════════════════════════════════
head("C. mig 961 DM split — the corrected CARD TOTAL never moves")

# C1 — verified_overlay (what the rest of the platform books off)
base = {"store_cc": 999.0, "t_credit": 999.0, "t_ext_cc": 999.0, "epay_cc": 7.0}
legacy = verified_overlay.apply_overlay(dict(base), {"dm_store_cc": 120.0})
check("C1a pre-961 branch unchanged: dm_store_cc alone → t_credit=total, t_ext_cc zeroed",
      legacy == {"store_cc": 120.0, "t_credit": 120.0, "t_ext_cc": 0.0, "epay_cc": 0.0}, legacy)
split = verified_overlay.apply_overlay(dict(base), {"dm_store_cc": 120.0, "dm_ext_cc": 45.0})
check("C1b dm_ext_cc set → t_ext_cc = dm_ext_cc, t_credit = total − ext",
      split["t_ext_cc"] == 45.0 and split["t_credit"] == 75.0, split)
check("C1c INVARIANT: t_credit + t_ext_cc == dm_store_cc in BOTH branches",
      round(legacy["t_credit"] + legacy["t_ext_cc"], 2) == 120.0 ==
      round(split["t_credit"] + split["t_ext_cc"], 2))
check("C1d store_cc (the legacy family's card total) is the FULL corrected total either way",
      legacy["store_cc"] == split["store_cc"] == 120.0)
zero_split = verified_overlay.apply_overlay(dict(base), {"dm_store_cc": 120.0, "dm_ext_cc": 0.0})
check("C1e dm_ext_cc = 0.00 is a STATED zero, not 'unset' (t_credit keeps the whole total)",
      zero_split["t_ext_cc"] == 0.0 and zero_split["t_credit"] == 120.0)
check("C1f a cash-only correction still never touches the card columns",
      verified_overlay.apply_overlay(dict(base), {"dm_store_cash": 10.0}) == base)
check("C1g has_correction sees a dm_ext_cc-only correction",
      verified_overlay.has_correction({"dm_ext_cc": 5.0}) is True)

# C2 — the tally's own overlay states the SAME rule
cells = ecr.declared_cells([crow("S1", "2026-09-01", 100.0, 40.0)])
ecr.apply_dm_split(cells, {("S1", "2026-09-01"): {"dm_store_cc": 120.0, "dm_ext_cc": 45.0}})
leg = cells[("S1", "2026-09-01")]
check("C2a the tally's split matches verified_overlay to the cent",
      leg[EXT]["amount"] == 45.0 and leg[POS]["amount"] == 75.0 and
      round(leg[EXT]["amount"] + leg[POS]["amount"], 2) == 120.0, leg)
check("C2b both legs are stamped basis 'dm'",
      leg[EXT]["basis"] == "dm" and leg[POS]["basis"] == "dm")

merged = ecr.declared_cells([crow("S1", "2026-09-01", 100.0, 40.0)])
ecr.apply_dm_split(merged, {("S1", "2026-09-01"): {"dm_store_cc": 120.0, "dm_ext_cc": None}})
check("C2c DM corrected the card total WITHOUT a split ⇒ basis 'dm_merged', amount not invented",
      merged[("S1", "2026-09-01")][EXT]["basis"] == "dm_merged" and
      merged[("S1", "2026-09-01")][EXT]["amount"] == 40.0)
untouched = ecr.declared_cells([crow("S1", "2026-09-01", 100.0, 40.0)])
ecr.apply_dm_split(untouched, {("S1", "2026-09-01"): {"dm_store_cash": 500.0}})
check("C2d a cash-only DM correction leaves the card legs on the REP basis",
      untouched[("S1", "2026-09-01")][EXT]["basis"] == "rep")

# C3 — the mig-935 audit trail carries the new field with no new logic
arow = verification_audit.build_audit_row(
    "org", {"close_date": "2026-09-01", "store_code": "S1", "verified": True, "dm_ext_cc": 45.0},
    {"verified": True, "dm_ext_cc": 10.0})
check("C3a a dm_ext_cc change alone writes a revision row",
      arow is not None and "dm_ext_cc" in (arow or {}).get("changed_fields", []))
check("C3b prior value preserved", arow["prior_dm_ext_cc"] == 10.0 and arow["dm_ext_cc"] == 45.0)
check("C3c edited_after_verify flags it as a money change on an already-verified day",
      arow["edited_after_verify"] is True)
check("C3d submissions export surfaces dm_ext_cc beside the other DM figures",
      "dm_ext_cc" in verification_audit.submission_dm_fields({"verified": True, "dm_ext_cc": 45.0}))
check("C3e an idle re-save still writes nothing",
      verification_audit.build_audit_row("org", {"dm_ext_cc": 45.0}, {"dm_ext_cc": 45.0}) is None)

# ══ D. the settlement ADAPTER ════════════════════════════════════════════════════════════════════
head("D. settlement adapter — config-named columns, merchant→store, unmapped surfaced")

raw_default = [{"terminal_id": "M-1", "settlement_date": "2026-09-01", "amount": "140.00"}]
n = ecr.normalize_settlement_rows(raw_default, None, role=EXT)
check("D1 default spellings (terminal_id/settlement_date/amount) normalize",
      n[0] == {"store_code": None, "merchant_id": "M-1", "day": "2026-09-01",
               "amount": 140.0, "role": EXT}, n)

raw_cfg = [{"Terminal": "M-1", "Batch Date": "2026-09-01T00:00:00", "Net": 140.0}]
fwd = ecr.normalize_settlement_rows(raw_cfg, {"merchant_id": "Terminal", "day": "Batch Date",
                                              "amount": "Net"}, role=EXT)
check("D2 report_pull_map column_map, {canonical: source_header} direction",
      fwd[0]["merchant_id"] == "M-1" and fwd[0]["day"] == "2026-09-01" and fwd[0]["amount"] == 140.0,
      fwd)
rev = ecr.normalize_settlement_rows(raw_cfg, {"Terminal": {"col": "merchant_id"},
                                              "Batch Date": {"col": "day"},
                                              "Net": {"col": "amount"}}, role=EXT)
check("D3 the ingest-facing {source_header: {col: canonical}} direction resolves identically",
      rev == fwd)

cells, unmapped = ecr.settlement_cells(fwd, {"M-1": "S1"}, role=EXT)
check("D4 merchant id resolves to a store through the mig-902 map",
      cells == {("S1", "2026-09-01"): {EXT: 140.0}}, cells)
check("D5 same store+day+role rows SUM", ecr.settlement_cells(
    fwd + fwd, {"M-1": "S1"}, role=EXT)[0][("S1", "2026-09-01")][EXT] == 280.0)
_, unm = ecr.settlement_cells(
    ecr.normalize_settlement_rows([{"terminal_id": "M-9", "settlement_date": "2026-09-01",
                                    "amount": 10.0}], None, role=EXT), {"M-1": "S1"}, role=EXT)
check("D6 an UNMAPPED terminal is surfaced, never silently dropped", len(unm) == 1)
pre, _ = ecr.settlement_cells(ecr.normalize_settlement_rows(
    [{"store_code": "s1", "settlement_date": "2026-09-01", "amount": 12.0}], None, role=EXT), {}, role=EXT)
check("D7 a feed that already resolved a store needs no merchant map (and normalizes the code)",
      pre == {("S1", "2026-09-01"): {EXT: 12.0}}, pre)

# ══ E. the TALLY truth table ═════════════════════════════════════════════════════════════════════
head("E. tally truth table — the verdict IS envelope_report.count_fields")

TT = [
    # declared, settled, tol, expected status, expected variance
    (100.0, 100.0, 0.0, "match", 0.0),
    (100.0, 90.0, 0.0, "short", -10.0),      # processor settled LESS than the store declared
    (100.0, 110.0, 0.0, "over", 10.0),
    (100.0, 99.5, 1.0, "match", -0.5),       # inside tolerance
    (100.0, 98.0, 1.0, "short", -2.0),       # outside tolerance
    (0.0, 25.0, 0.0, "over", 25.0),          # nothing declared, processor settled money
    (25.0, 0.0, 0.0, "short", -25.0),        # declared money, processor silent (feed covers the day)
]
for d, s_, tol, want_st, want_var in TT:
    r = ecr.recon_row("S1", "2026-09-01", EXT, {"amount": d, "basis": "rep"}, s_, tol)
    check(f"E declared {d} vs settled {s_} (tol {tol}) ⇒ {want_st} / {want_var}",
          r["status"] == want_st and r["variance"] == want_var, r)

for d, s_, tol, _st, _v in TT:
    cf = envelope_report.count_fields(d, s_, tol)
    r = ecr.recon_row("S1", "2026-09-01", EXT, {"amount": d, "basis": "rep"}, s_, tol)
    check(f"E-reuse count_fields({d},{s_},{tol}) is byte-identical to the tally verdict",
          (cf["status"], cf["variance"]) == (r["status"], r["variance"]))
check("E-reuse the module imports the mig-936 classifier rather than defining one",
      "def count_fields" not in open(os.path.join(MOD_DIR, "external_credit_recon.py")).read())

# ══ F. HONEST GAPS ═══════════════════════════════════════════════════════════════════════════════
head("F. honest gaps — an absent feed is never a zero, a merged DM total never a variance")

g1 = ecr.recon_row("S1", "2026-09-01", EXT, {"amount": 40.0, "basis": "rep"}, None)
check("F1 feed has not landed ⇒ no_processor_data with variance None",
      g1["status"] == "no_processor_data" and g1["variance"] is None)
g2 = ecr.recon_row("S1", "2026-09-01", EXT, None, 40.0)
check("F2 no closing row ⇒ no_declared_data with variance None",
      g2["status"] == "no_declared_data" and g2["variance"] is None)
g3 = ecr.recon_row("S1", "2026-09-01", EXT, {"amount": 40.0, "basis": "dm_merged"}, 5.0)
check("F3 THE DEFECT CLASS: a DM-merged card total NEVER reports a $35 fake short",
      g3["status"] == "dm_merged" and g3["variance"] is None, g3)

decl = ecr.declared_cells([crow("S1", "2026-09-01", 100.0, 40.0),
                           crow("S2", "2026-09-01", 10.0, 20.0)])
sett = {("S1", "2026-09-01"): {EXT: 40.0, POS: 100.0}}
covered = ecr.assemble_rows(decl, sett, roles=(EXT,), feed_days={EXT: {"2026-09-01"}})
s2 = [r for r in covered if r["store_code"] == "S2"][0]
check("F4 feed COVERS the day but is silent for this store ⇒ honest 0.00 settled, a real short",
      s2["settled_amount"] == 0.0 and s2["status"] == "short" and s2["variance"] == -20.0, s2)
absent = ecr.assemble_rows(decl, sett, roles=(EXT,), feed_days=None)
s2a = [r for r in absent if r["store_code"] == "S2"][0]
check("F5 feed has NOT landed for the day ⇒ no_processor_data, not a fabricated short",
      s2a["status"] == "no_processor_data" and s2a["variance"] is None)
check("F6 a settled-only store-day still produces a line (money we never declared is visible)",
      any(r["store_code"] == "S9" for r in ecr.assemble_rows(
          {}, {("S9", "2026-09-01"): {EXT: 5.0}}, roles=(EXT,))))

# ══ G. totals + status filter ════════════════════════════════════════════════════════════════════
head("G. totals + status filter")

mixed = [
    ecr.recon_row("S1", "2026-09-01", EXT, {"amount": 100.0, "basis": "rep"}, 90.0),   # short 10
    ecr.recon_row("S2", "2026-09-01", EXT, {"amount": 50.0, "basis": "rep"}, 55.0),    # over 5
    ecr.recon_row("S3", "2026-09-01", EXT, {"amount": 20.0, "basis": "rep"}, 20.0),    # match
    ecr.recon_row("S4", "2026-09-01", EXT, {"amount": 77.0, "basis": "rep"}, None),    # gap
    ecr.recon_row("S5", "2026-09-01", POS, {"amount": 10.0, "basis": "dm_merged"}, 9.0),  # gap
]
t = ecr.totals(mixed)
check("G1 counts", (t["short"], t["over"], t["match"]) == (1, 1, 1), t)
check("G2 gap rows are COUNTED but contribute no dollars",
      t["no_processor_data"] == 1 and t["dm_merged"] == 1 and
      t["declared_total"] == 170.0 and t["settled_total"] == 165.0, t)
check("G3 variance_total nets short against over", t["variance_total"] == -5.0)
check("G4 short_total is the POSITIVE missing money", t["short_total"] == 10.0)
check("G5 per-role rollup separates the two processors",
      t["by_role"][EXT]["cells"] == 4 and t["by_role"][POS]["cells"] == 1, t["by_role"])
check("G6 status='variance' = short ∪ over", len(ecr.status_filter(mixed, "variance")) == 2)
check("G7 status='gap' = every honest-gap status", len(ecr.status_filter(mixed, "gap")) == 2)
check("G8 blank status filters nothing", len(ecr.status_filter(mixed, "")) == 5)
check("G9 an exact status still works", len(ecr.status_filter(mixed, "short")) == 1)

# ══ H. RULE TWO — no brand names in code or migrations ═══════════════════════════════════════════
head("H. RULE TWO — the module and its migrations name no tenant, carrier or processor brand")

BRANDS = re.compile(r"payanywhere|payments\s*hub|paymentshub|translink|transfirst|businesstrack"
                    r"|white\s+machine|boost|total\s+wireless|luxelink", re.I)
mod_src = open(os.path.join(MOD_DIR, "external_credit_recon.py")).read()
check("H1 the pure module contains no processor/tenant BRAND at all",
      not BRANDS.search(mod_src), (BRANDS.search(mod_src) or [""])[0])
ep_src = open(os.path.join(ROOT, "backend", "app", "modules", "closing", "router.py")).read()
ep = ep_src[ep_src.index("def external_credit_recon("):]
ep = ep[:ep.index("\n# \"Need a training walkthru")] if "\n# \"Need a training walkthru" in ep else ep
check("H2 the endpoint body contains no processor/tenant BRAND either",
      not BRANDS.search(ep), (BRANDS.search(ep) or [""])[0])
check("H3 roles are neutral slugs", set(ecr.ROLES) == {"external_cc", "pos_merchant"})

mig_dir = os.path.join(ROOT, "database", "migrations")
m960 = open(os.path.join(mig_dir, "960_external_credit_machine_label_and_processor_map.sql")).read()
m961 = open(os.path.join(mig_dir, "961_dm_external_credit_split.sql")).read()
for name, src in (("960", m960), ("961", m961)):
    check(f"H4 mig {name} is idempotent + carries a REVERT note",
          "-- REVERT" in src and ("IF NOT EXISTS" in src or "ON CONFLICT" in src))
_m960_sql = [ln for ln in m960.splitlines() if not ln.strip().startswith("--")]
check("H5 every brand-ish string in mig 960's EXECUTABLE SQL is label DATA on ui_label_override",
      all(("ui_label_override" in "\n".join(_m960_sql)) and
          ("White machine" not in ln or "'report_col:" in ln) for ln in _m960_sql),
      [ln for ln in _m960_sql if "White machine" in ln])
check("H5b the presets are seeded for both carrier scopes, once each",
      sum("White machine" in ln for ln in _m960_sql) == 2 and
      "'report_col:boost'" in m960 and "'report_col:total'" in m960)
check("H6 mig 960 creates NO new table (the field and the label store already exist)",
      "CREATE TABLE" not in m960.upper())
check("H7 mig 961 creates NO new table either (dm_ext_cc joins the mig-029/935 rows)",
      "CREATE TABLE" not in m961.upper())
check("H8 no money seed ships uncommented in either migration",
      "UPDATE commcalc" not in m960 and "UPDATE commcalc" not in m961)

# label registry
sys.path.insert(0, os.path.join(ROOT, "backend", "app", "modules", "commcalc"))
import importlib.util as _ilu
_spec = _ilu.spec_from_file_location(
    "report_labels_pure", os.path.join(ROOT, "backend", "app", "modules", "commcalc", "report_labels.py"))
_rl = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_rl)
check("H9 the closing field is registered in the EXISTING label registry (mig 945/953 machinery)",
      dict(_rl.LABELABLE_COLUMNS).get("closing_t_ext_cc") == "External Credit Card")
check("H10 an org with no carrier/preset renders the built-in wording (byte-identical to today)",
      _rl.resolve_columns({}, {})["closing_t_ext_cc"] == "External Credit Card")
check("H11 a carrier preset renames it; a tenant override still wins over the preset",
      _rl.resolve_columns({}, {"closing_t_ext_cc": "W"})["closing_t_ext_cc"] == "W" and
      _rl.resolve_columns({"closing_t_ext_cc": "T"}, {"closing_t_ext_cc": "W"})["closing_t_ext_cc"] == "T")
check("H12 _TCOL is re-pointed at the shared map, not copied",
      "_TCOL = _external_credit_recon.TENDER_COLUMN" in ep_src)

# ══ I. THE CROSS-AGENT CONTRACT with the portal-scrape side (mig 955) ════════════════════════════
head("I. contract with the scrape side — mig 955 registers the feed this tally resolves")

m955_path = os.path.join(mig_dir, "955_merchant_portal_settlement.sql")
if not os.path.exists(m955_path):
    check("I0 mig 955 present (the scrape side's storage + registry seed)", False,
          "955_merchant_portal_settlement.sql not found")
else:
    m955 = open(m955_path).read()
    check("I1 the scrape side seeds the report_pull_map key this tally looks up",
          f"'{ecr.SETTLEMENT_REPORT_KEY}'" in m955 and "report_pull_map" in m955)
    check("I2 both role slugs are the SAME two words on both sides",
          "'external_cc'" in m955 and "'pos_merchant'" in m955)
    check("I3 the registered column_map is in the {canonical: source_header} direction this "
          "module's normalizer accepts",
          '"day":"business_date"' in m955.replace(" ", "") or
          '"day": "business_date"' in m955)
    # The registry row as mig 955 actually seeds it, applied to a row shaped like the table it names.
    live_map = {"day": "business_date", "amount": "net_amount", "role": "settlement_role",
                "store_code": "store_code", "merchant_id": "merchant_id"}
    feed_row = {"org_id": "o", "business_date": "2026-09-01", "merchant_id": "MID-7",
                "store_code": None, "settlement_role": "external_cc", "card_brand": "visa",
                "gross_amount": 152.0, "net_amount": 148.5, "fee_amount": 3.5, "txn_count": 4}
    got = ecr.normalize_settlement_rows([feed_row], live_map)
    check("I4 a real merchant_settlement_day row normalizes end to end through that map",
          got[0] == {"store_code": None, "merchant_id": "MID-7", "day": "2026-09-01",
                     "amount": 148.5, "role": "external_cc"}, got)
    cells_i, unm_i = ecr.settlement_cells(got, {"MID-7": "S1"})
    check("I5 an ingest-unresolved store_code still resolves at read time via the mig-902 map",
          cells_i == {("S1", "2026-09-01"): {EXT: 148.5}}, cells_i)
    _, unm_j = ecr.settlement_cells(got, {})
    check("I6 a still-unmapped merchant id is SURFACED, never counted as $0 for a store",
          len(unm_j) == 1 and not _)
    brands = ecr.normalize_settlement_rows(
        [dict(feed_row, card_brand="visa", net_amount=100.0),
         dict(feed_row, card_brand="amex", net_amount=48.5)], live_map)
    cells_b, _ = ecr.settlement_cells(brands, {"MID-7": "S1"})
    check("I7 the table's per-card-brand grain SUMS to one store-day figure",
          cells_b[("S1", "2026-09-01")][EXT] == 148.5, cells_b)
    check("I8 the FUNDING table is a different grain and is NOT read by this tally",
          "merchant_settlement_batch" not in mod_src and
          "merchant_settlement_batch" not in ep)

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILED: " + "; ".join(FAIL))
    sys.exit(1)
print("✅ harness_external_credit_recon: ALL PASS")
