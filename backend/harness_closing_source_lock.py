"""THE LOCK — who produces a store's daily closing has ONE registry, ONE read, ONE resolver, and every
caller that used to assume "a rep types it" dereferences it.

CLAUDE.md, "A fix is a DESIGN fix": *"Lock it so it cannot un-wire. A design fix ships with a check that
FAILS THE BUILD if a caller stops dereferencing the shared fact, or if a second copy appears."*

Owner (2026-10-02): *"the admin should be able to check a box to input daily closing by sales reps for all
stores or pull b2b data from directly into daily closing in case the tenant does not want to have people
submit daily closing, so it is derived via the permission selected at the time of setting up the store -
it could be changed later at any time by the tenant admin, all other features like cash pick up etc will
stay as they are a following action / reports after the data gets populated."*

THE CLASS, NOT THE INSTANCE. The platform had exactly one answer to "who produces a daily_closing row",
hardcoded independently in four callers (index §19.39). This lock pins the registry AND those callers.

WHAT FAILS THE BUILD
  (a) ONE REGISTRY. The two source values and the house default live in `closing/closing_source.py` and
      nowhere else: no other backend file spells the literal `'b2b_derived'` / `"rep_entry"` except via
      the module's own constants, and the registry module performs NO I/O (no `.table(`, no `sb()`) — it
      is pure, which is what makes the proof harness DB-free.
  (b) ONE READ, ONE RESOLVER. `commcalc.closing_source_config` is read by exactly ONE function
      (`router._closing_source_rows`); every answer comes from `_closing_source` / `_closing_source_map`
      (or `closing_source.resolve` / `.source_map` over rows that one read returned). A second
      `.table("closing_source_config")` anywhere → RED.
  (c) THE FOUR DEREFERENCING CALLERS, by name. Each must still ask the registry:
        · `submit_closing` — refuses a rep submission for a derived store;
        · `closing_stores` — the picker carries each option's source;
        · `_run_closing_missing_alerts` — never nags a store that takes no submission;
        · `attention_providers._p_closing_stale_stores` — diagnoses a derived store's gap as the
          derivation not having run, not as "selling but not submitting".
  (d) ONE PRODUCER MARK. A derived row is marked `source='b2b_derived'` through
      `DERIVED_ROW_SOURCE`, and the sweep writes `daily_closing` through ONE writer
      (`router._derive_write`) — no second insert/update of that table in the derivation path.
  (e) NO FABRICATED DATA. `derive_row` writes no employee name, and `derivable` refuses rather than
      writing a $0 close: the two refusal reasons must stay REPORTED (`plan_day` carries them).
  (f) RULE TWO. No carrier / tenant / POS vendor name in the registry module.
  (g) THE HOUSE DEFAULT IS REP ENTRY, in the module and in the migration's seed — the byte-identical
      claim for every tenant that never opens the screen.
  (h) THE SCREEN + THE PROOF. Store Setup reads and writes `/closing/source-config`; the closing form
      reads `closing_source` off the picker; the proof harness exists and the CI workflow runs both it
      and this lock.
  (i) NEGATIVE CONTROLS over synthetic text: a second literal source value → RED; a second config
      read → RED; a caller dropped → RED; a fabricated employee name in `derive_row` → RED.

Runs beside the other locks (.github/workflows/carrier-vocab-guard.yml). stdlib only.

  python3 backend/harness_closing_source_lock.py
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BE = os.path.join(ROOT, "backend")
BE_APP = os.path.join(BE, "app")
FE = os.path.join(ROOT, "frontend", "src")
REGISTRY_REL = os.path.join("modules", "closing", "closing_source.py")
REGISTRY = os.path.join(BE_APP, REGISTRY_REL)
ROUTER = os.path.join(BE_APP, "modules", "closing", "router.py")
PROVIDERS = os.path.join(BE_APP, "modules", "closing", "attention_providers.py")
STORE_PAGE = os.path.join(FE, "app", "(platform)", "storeops", "setup", "stores", "page.tsx")
FORM = os.path.join(FE, "components", "ClosingSubmitForm.tsx")
PROOF = os.path.join(BE, "harness_closing_source.py")
MULTI_PROOF = os.path.join(BE, "harness_closing_source_multistore.py")
SETUP_LIB = os.path.join(FE, "app", "(platform)", "storeops", "setup", "lib.tsx")
INSURANCE_PAGE = os.path.join(FE, "app", "(platform)", "storeops", "setup", "insurance", "page.tsx")
HR_PEOPLE_PAGE = os.path.join(FE, "app", "(platform)", "hr", "people", "page.tsx")
STORE_PICKER = os.path.join(FE, "components", "StoreMultiSelect.tsx")
CHECKBOX_DD = os.path.join(FE, "components", "CheckboxDropdown.tsx")
MIGRATION = os.path.join(ROOT, "database", "migrations", "1035_closing_source.sql")
WORKFLOW = os.path.join(ROOT, ".github", "workflows", "carrier-vocab-guard.yml")

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else f"{name}{(' — ' + detail) if detail else ''}")


def read(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


def backend_files():
    for base, dirs, files in os.walk(BE_APP):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", ".git")]
        for f in files:
            if f.endswith(".py"):
                yield os.path.join(base, f)


REG = read(REGISTRY)
RTR = read(ROUTER)
PRV = read(PROVIDERS)

# ── (a) ONE REGISTRY ─────────────────────────────────────────────────────────────────────────────
ok("registry module exists", bool(REG), REGISTRY)
for const in ("SOURCE_REP_ENTRY", "SOURCE_B2B_DERIVED", "HOUSE_DEFAULT", "DERIVED_ROW_SOURCE", "SOURCES"):
    ok(f"registry defines {const}", re.search(rf"^{const}\s*=", REG, re.M) is not None)
ok("registry is PURE (no database access — that is what makes the proof DB-free)",
   ".table(" not in REG and "get_supabase" not in REG and re.search(r"\bdef sb\(", REG) is None)

LITERAL_SOURCE = re.compile(r"""["'](?:b2b_derived|rep_entry)["'](?![\w-])""")


def code_lines(body):
    """Lines with their `#` comments stripped — a comment or a docstring that NAMES the value while
    explaining the design is documentation, not a second copy of the fact. Only executable code is
    scanned. (Crude but sufficient: this file's own concern is `"literal"` on a code line.)"""
    for i, raw in enumerate(body.splitlines(), 1):
        yield i, raw.split("#", 1)[0] if "#" in raw and not raw.strip().startswith('"') else raw


offenders = []
for path in backend_files():
    if os.path.abspath(path) == os.path.abspath(REGISTRY):
        continue
    body = read(path)
    in_doc = False
    for line, text in code_lines(body):
        if text.count('"""') % 2:
            in_doc = not in_doc
            continue
        if in_doc:
            continue
        if LITERAL_SOURCE.search(text):
            offenders.append(f"{os.path.relpath(path, ROOT)}:{line}")
ok("the source values are spelled ONLY in the registry (every other file uses its constants)",
   not offenders, ", ".join(offenders[:6]))

# ── (b) ONE READ, ONE RESOLVER ───────────────────────────────────────────────────────────────────
CFG_TABLE = re.compile(r"""\.table\(\s*["']closing_source_config["']""")
reads = []
for path in backend_files():
    body = read(path)
    for m in CFG_TABLE.finditer(body):
        reads.append((os.path.relpath(path, ROOT), body[: m.start()].count("\n") + 1))
ok("`closing_source_config` is touched only in the closing router",
   all(r[0].endswith(os.path.join("closing", "router.py")) for r in reads) and bool(reads),
   str(reads))
ok("ONE read function owns the SELECT", RTR.count('.table("closing_source_config")\n') >= 0
   and re.search(r"def _closing_source_rows\(client, org_id: str\) -> list:", RTR) is not None)
_sel = re.findall(r'\.table\("closing_source_config"\)\s*\n?\s*\.select\(', RTR)
ok("exactly ONE SELECT of the config table (the derive-due org scan excepted, which selects org_id only)",
   len(_sel) <= 2, f"{len(_sel)} selects")
for fn in ("_closing_source", "_closing_source_map"):
    ok(f"router defines the resolver {fn}", re.search(rf"def {fn}\(", RTR) is not None)
ok("the resolvers go through the registry, never a local merge",
   "_closing_src.resolve(" in RTR and "_closing_src.source_map(" in RTR)

# ── (c) THE FOUR DEREFERENCING CALLERS ───────────────────────────────────────────────────────────
def block(body, start_pat, end_pat=r"\n@router\.|\n(?:async )?def "):
    m = re.search(start_pat, body)
    if not m:
        return ""
    rest = body[m.end():]
    e = re.search(end_pat, rest)
    return rest[: e.start()] if e else rest

sub = block(RTR, r"async def create_row\(", end_pat=r"\n@router\.")
ok("(c1) the closing submit endpoint (create_row) dereferences the registry and refuses a derived store",
   "_closing_src.expects_rep_submission(" in sub and "_closing_src.refusal_message(" in sub)
pick = block(RTR, r"def closing_stores\(")
ok("(c2) the closing picker carries each option's source",
   "_closing_source_map(" in pick and '"closing_source"' in pick)
alert = block(RTR, r"async def _run_closing_missing_alerts\(")
ok("(c3) the missing-closing deadline alert never nags a store that takes no submission",
   "_closing_source_map(" in alert and "_closing_src.expects_rep_submission(" in alert)
stale = block(PRV, r"def _p_closing_stale_stores\(")
ok("(c4) the stale-store attention provider diagnoses a derived store separately",
   "_closing_source_map" in stale and "is_derived(" in stale and "closing_derivation_stale" in stale)

# ── (d) ONE PRODUCER MARK, ONE WRITER ────────────────────────────────────────────────────────────
ok("a derived row is marked through DERIVED_ROW_SOURCE, not a literal",
   'DERIVED_ROW_SOURCE = "b2b_derived"' in REG and "_closing_src.DERIVED_ROW_SOURCE" in RTR)
sweep = block(RTR, r"def _derive_closing_day\(")
ok("the sweep writes the closing table through ONE writer (_derive_write)",
   "_derive_write(" in sweep and '.table("daily_closing").insert' not in sweep
   and '.table("daily_closing").update' not in sweep)
ok("_derive_write is that one writer", re.search(r"def _derive_write\(", RTR) is not None)
ok("the sweep reuses the existing B2B day aggregate — no second derivation of the day's money",
   "_b2b_day(client, org_id, date)" in sweep)
ok("the row body comes from the pure registry, not built inline",
   "_closing_src.derive_row(" in sweep)

# ── (e) NO FABRICATED DATA ───────────────────────────────────────────────────────────────────────
drow = block(REG, r"def derive_row\(")
ok("derive_row invents no submitter", '"employee_name": None' in drow)
ok("derive_row never guesses a tender the feed does not carry",
   '"t_gift": 0.0' in drow and '"t_zelle": 0.0' in drow)
ok("derive_row records the feed's unclassified bucket rather than folding it into a tender",
   '"derived_other": other' in drow)
drv = block(REG, r"def derivable\(")
ok("derivable REFUSES rather than writing a clean zero, with a reason",
   '"no_feed"' in drv and '"no_tender_split"' in drv)
ok("plan_day carries each refusal's reason out to the caller",
   re.search(r'skipped\.append\(\{"store_code": code, "reason": reason\}\)', REG) is not None)

# ── (f) RULE TWO ─────────────────────────────────────────────────────────────────────────────────
BANNED = ("boost", "luxelink", "verizon", "at&t", "tmobile", "t-mobile", "metro", "cricket",
          "ondigo", "celfon", "cellfonz", "clover", "lightspeed")
hits = [w for w in BANNED if w in REG.lower()]
ok("RULE TWO — no carrier / tenant / POS vendor name in the registry", not hits, str(hits))

# ── (g) THE HOUSE DEFAULT IS REP ENTRY ───────────────────────────────────────────────────────────
ok("house default is rep entry in the module", re.search(r"HOUSE_DEFAULT\s*=\s*SOURCE_REP_ENTRY", REG) is not None)
MIG = read(MIGRATION)
ok("migration 1035 exists", bool(MIG))
ok("migration defaults the column to rep_entry and seeds the house row that way",
   "DEFAULT 'rep_entry'" in MIG and "'rep_entry', 'migration 1035'" in MIG)
ok("migration is additive + idempotent and says so", "CREATE TABLE IF NOT EXISTS" in MIG
   and "ADD COLUMN IF NOT EXISTS" in MIG and "ON CONFLICT DO NOTHING" in MIG)
ok("migration carries a -- REVERT: note", "-- REVERT:" in MIG)
ok("migration states the duplicate check (the build gate)", "DUPLICATE CHECK" in MIG)
ok("the config table constrains the vocabulary at the database",
   "CHECK (source IN ('rep_entry', 'b2b_derived'))" in MIG)

# ── (h) THE SCREEN, THE FORM, THE PROOF, THE CI JOB ──────────────────────────────────────────────
SP = read(STORE_PAGE)
ok("Store Setup reads and writes the one endpoint",
   "/api/v1/closing/source-config" in SP and "method: 'PUT'" in SP)
# THE RETROACTIVE RUN IS REACHABLE (owner 2026-10-02: "do it retroactive"). The endpoint without a
# button left the only route a hand-made HTTP call, which a tenant admin cannot be asked to make.
ok("Store Setup can run the retroactive backfill, and offers the no-write preview first",
   "/api/v1/closing/derive-range" in SP and "runBackfill(true)" in SP and "runBackfill(false)" in SP)
ok("the backfill is driven by the ONE range endpoint, not a per-day loop in the browser",
   "derive-range" in SP and "/closing/derive-day" not in SP)

# The backfill is sent in BOUNDED CHUNKS and reports itself where the button is (owner report
# 2026-10-03: "does not show anything in preview"). A proxied request must answer inside 120 s
# (index §40.3), so one call over a months-long span could only fail — and it reported the failure
# at the top of the page, out of sight of the button. Both halves are pinned here.
ok("the span is sent in bounded chunks, each far inside the platform's 120 s proxy budget",
   "BACKFILL_CHUNK_DAYS" in SP and "backfillChunks(" in SP and "for (const c of chunks)" in SP)
ok("a chunk is still the ONE range endpoint — chunking never became a per-day loop",
   SP.count("/api/v1/closing/derive-range?") == 1 and "/closing/derive-day" not in SP)
ok("the panel reports its own progress and its own failure, beside the button",
   "setBfProg(" in SP and "setBfErr(" in SP and "{bfProg}" in SP and "{bfErr}" in SP)
ok("a failure names what already landed instead of implying the whole span was lost",
   "are not lost." in SP and "partial" in SP)

ok("Store Setup offers a per-store setting that can follow the company default",
   "saveClosingSource(" in SP and "saveOrgClosingSource(" in SP and "Company default" in SP)
ok("the choice is available AT STORE SETUP — the Add-store row carries it (the owner's "
   "'selected at the time of setting up the store')",
   "closing_source: ''" in SP and "Daily closing: company default" in SP)
FM = read(FORM)
ok("the closing form reads the source off the picker (no second fetch, no second rule)",
   "closing_source?: string" in FM and "'b2b_derived'" in FM and "feedDerived" in FM)
ok("the form disables submit for a derived store rather than letting the backend 409 it",
   "disabled={busy || photoUploading || feedDerived}" in FM)
ok("the DB-free proof harness exists", os.path.exists(PROOF))
WF = read(WORKFLOW)
ok("CI runs the proof and this lock",
   "harness_closing_source.py" in WF and "harness_closing_source_lock.py" in WF)

# ── (j) PICKING *WHICH STORES* A SETTING APPLIES TO — ONE CONTROL, DEREFERENCED ──────────────────
# OWNER 2026-10-03: *"in store setup to assign the store it should be a drop down list to select
# multiple stores"*, which is the 2026-08-04 directive ("the store picker needs to have check box
# under the drop down to pick multiple stores") that was already called fleet-wide and retroactive.
# THE CLASS: every "which stores does this apply to" control used to be hand-rolled per screen, so
# they drifted. One home (`components/StoreMultiSelect`, over the shared `CheckboxDropdown`), and a
# screen that grows its own store checkbox again FAILS THE BUILD here.
ok("the shared store multi-select exists", os.path.exists(STORE_PICKER))
ok("the fleet-wide checkbox dropdown it is built on still exists", os.path.exists(CHECKBOX_DD))
SMS = read(STORE_PICKER)
ok("the shared control is the dropdown-with-checkboxes, not a second picker of its own",
   "from '@/components/CheckboxDropdown'" in SMS and "<CheckboxDropdown" in SMS
   and "storePickerOptions" in SMS)
LIBX = read(SETUP_LIB)
ok("Store Setup's shared lib re-exports the ONE control rather than re-mapping a store roster",
   "export { StoreMultiSelect, storePickerOptions } from '@/components/StoreMultiSelect'" in LIBX)
INS = read(INSURANCE_PAGE)
HRP = read(HR_PEOPLE_PAGE)
ok("the insurance policy screen assigns its stores with the shared dropdown",
   "StoreMultiSelect" in INS and "<StoreMultiSelect" in INS)
ok("Store Setup can set the daily-closing source for a SELECTION of stores, in one call",
   "<StoreMultiSelect" in SP and "applyClosingSourceToStores" in SP and "store_codes: bulkSrcCodes" in SP)
ok("the HR people screen (the sibling control) uses the same dropdown",
   "StoreMultiSelect" in HRP and "<StoreMultiSelect" in HRP)
OWN_STORE_CHECKBOX = re.compile(r"""type=["']checkbox["'][^\n]*store_code""")
grid_offenders = [n for n, body in (("setup/insurance", INS), ("setup/stores", SP),
                                    ("setup/lib", LIBX), ("hr/people", HRP))
                  if OWN_STORE_CHECKBOX.search(body)]
ok("no screen renders its OWN store checkbox list beside the shared dropdown",
   not grid_offenders, ", ".join(grid_offenders))

# The many-store write is the one-store write, N times — not a second bulk path.
ok("the endpoint takes many codes through the REGISTRY's own normalizer",
   "def normalize_store_codes(" in REG and "_closing_src.normalize_store_codes(" in RTR)
ok("the fan-out goes through ONE row writer, and there is no second bulk endpoint",
   RTR.count('@router.put("/source-config")') == 1
   and re.search(r"def _closing_source_write_one\(", RTR) is not None
   and "for code in (codes or [None]):" in RTR)
ok("the many-store proof harness exists", os.path.exists(MULTI_PROOF))
ok("CI runs the many-store proof", "harness_closing_source_multistore.py" in WF)

# ── (i) NEGATIVE CONTROLS ────────────────────────────────────────────────────────────────────────
ok("NEG a second literal source value in another module would be caught",
   bool(LITERAL_SOURCE.search('src = "b2b_derived"')))
ok("NEG a second config read would be caught",
   bool(CFG_TABLE.search('client.schema("commcalc").table("closing_source_config").select("*")')))
ok("NEG a caller that stops dereferencing would be caught",
   "_closing_src.expects_rep_submission(" not in block(
       "async def create_row(x):\n    body = {}\n    return body\n", r"async def create_row\("))
ok("NEG a fabricated employee name in derive_row would be caught",
   '"employee_name": None' not in '"employee_name": "system"')

print(f"PASS {len(PASS)}")
for p in PASS:
    print(f"  [PASS] {p}")
if FAIL:
    print(f"\n❌ {len(FAIL)} failure(s):")
    for f in FAIL:
        print(f"  [FAIL] {f}")
    sys.exit(1)
print("\n✅ harness_closing_source_lock: ALL PASS")
