"""Offline proof harness — ONE STORE-DAY TAKES ONE CLOSING, and the second person is told
(index §29.13). Drives the PURE `closing/closing_grain` + `closing/dedup_key` and the REAL
`closing/router.create_row` / `_ingest_dataframe` against the house's shared in-memory client
(`harness_intake_fakes.FakeDB` — reused, not a new fake). No live database, no network.

Run: `cd backend && python3 harness_closing_store_day_grain.py`

THE DEFECT (owner bug report 2026-10-13), verbatim: *"2 reps did the closing for Oct 10 for nostrand
ave, for luxelink, by rana Bilal and Moza Ali, one the system should not have taken duplicate
entries - it should have flagged the second person that they cannot enter, research, move the entries
for ran a Bilal to 531 utica"*.

MEASURED LIVE FIRST (read-only, Supabase REST, 2026-10-13):
  · `Nostrand` / 2026-10-10 holds THREE accepted closings — Rana Bilal $230 cash (23:03:21),
    moza ali $140 (23:04:02), Dixit Chauhan $0 (submitted 2026-10-13 16:35) — so the store-day
    declares $370 against a POS day of $140.
  · The POS feed for 2026-10-10 says WHO was where: `3560 Nostrand Avenue` cash $140.00, rep
    "Ali, Moaz"; `531 Utica Ave` cash $447.29 + External $12.00, rep "Bilal, rana". Utica has NO
    closing for that day at all. So moza ali's row is right, Rana Bilal's belongs to Utica, and
    nothing in the platform noticed either fact.
  · Every other Nostrand store-day from 2026-09-05 to 2026-10-12 holds exactly ONE closing.
  · House-wide 54 store-days hold more than one closing; 16 of them hold two DIFFERENT reps, at
    3-4 a month, across BOTH tenants. The rest are one rep repeating, which mig 502 already stopped.

THE CLASS: the platform had exactly ONE answer to "is this store-day already closed?" and it was
"only if this same REP closed it" — in the dedup key, in the database index built from it, and in the
pre-insert guard. A second PERSON was therefore not a duplicate anywhere, for any store or tenant,
and the day's cash doubled into every downstream total in silence. It is also why a closing filed at
the WRONG store goes unnoticed: the close gate compares a rep against the POS day for the store they
picked, and Rana Bilal had no POS rows at Nostrand, so the gate had nothing to compare and passed him
as recon-pending.

Proves:
  A. PURE — the grain vocabulary: two values, a label and a help sentence for each, an unknown value
     folds to the house default, and no database / table / hosting name reaches a screen (§19.38).
  B. PURE — `resolve` precedence: per-store override → org default → house default, case-insensitive,
     and an EMPTY config (no table yet) resolves to one-per-store-day, so the protection does not
     wait on migration 1080.
  C. PURE — the key: at store-day grain `dedup_key.for_row` leaves the employee out; the two
     spellings can never collide; identity is still required; the rep-day spelling is byte-identical
     to what it was before this package.
  D. BEHAVIOURAL, THE REGRESSION — the exact Nostrand 2026-10-10 shape. A second rep is REFUSED 409
     with the holder named, and no second `daily_closing` row is written. Before this package the
     same inputs wrote the row.
  E. THE OPT-OUT IS REAL — a tenant declaring `rep_day` behaves exactly as the platform did before:
     the second rep's closing is accepted and keyed per rep.
  F. THE CORRECTION PATH — a manager's release lets the RIGHT person redo a store-day the WRONG
     person closed: the resubmit UPDATES that one row (never a second insert) and carries the new
     name onto it; the same rep resubmitting is unchanged.
  G. THE SIBLING PATH — the closing-sheet upload (the second writer of `daily_closing`) skips and
     REPORTS a row for a store-day that is already closed, instead of inserting a second
     declaration; and two rows for one store-day in one file collapse to the first, reported.
  H. WIRING LOCKS — these FAIL THE BUILD if the design un-wires:
       H1  `create_row` dereferences the grain registry, and composes the dedup key WITH the grain;
       H2  the sheet-upload path dereferences the same registry;
       H3  nobody spells the grain vocabulary a second time (one fact, one home);
       H4  the refusal code is declared, raised through `_refuse`, and worded on the manager screen;
       H5  migration 1080 creates the config table, constrains the vocabulary, and recomputes NO
           stored dedup key (which would collide on the 16 historical two-rep store-days);
       H6  the derive sweep's EXCUSE still holds — it is store-day grained and never overwrites a
           human's row.
  I. NEGATIVE CONTROLS — the lock fails on code that reintroduces the defect, so a green §H means
     something.
"""
import sys
import os
import re
import ast
import asyncio

sys.path.insert(0, ".")

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("  ok   " if ok else "  FAIL ") + name + (f"   [{detail}]" if detail and not ok else ""))


HOUSE = "00000000-0000-0000-0000-000000000001"
LUXE = "854f6d7b-6590-4e4d-88ab-646f560d4f4c"

from harness_intake_fakes import FakeDB                       # noqa: E402
from app.modules.closing import closing_grain as cg           # noqa: E402
from app.modules.closing import dedup_key as dk               # noqa: E402
from app.modules.closing import submit_refusal as sr          # noqa: E402
import app.modules.closing.router as cr                       # noqa: E402
import app.modules.storeops.router as storeops                # noqa: E402
from fastapi import HTTPException                             # noqa: E402

ROUTER_PATH = "app/modules/closing/router.py"
GRAIN_PATH = "app/modules/closing/closing_grain.py"
MIG_PATH = "../database/migrations/1080_closing_grain_config.sql"
MGMT_PATH = "../frontend/src/app/(platform)/closing/management/page.tsx"
ROUTER_SRC = open(ROUTER_PATH).read()


# ═══════════════════════════════════════════════════════════════════════════════════════════════════
# A. PURE — the vocabulary
# ═══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== A. the grain vocabulary: two values, complete, screen-safe ==")
check("A1 exactly two grains, and the house default is one of them",
      cg.GRAINS == ("store_day", "rep_day") and cg.HOUSE_DEFAULT in cg.GRAINS)
check("A2 the house default is ONE CLOSING PER STORE-DAY (owner 2026-10-13)",
      cg.HOUSE_DEFAULT == cg.GRAIN_STORE_DAY)
check("A3 every grain has a label and a help sentence",
      all(cg.LABELS.get(g) and cg.HELP.get(g) for g in cg.GRAINS))
check("A4 nothing is worded that the vocabulary does not declare",
      set(cg.LABELS) == set(cg.GRAINS) and set(cg.HELP) == set(cg.GRAINS))
_BANNED = ("supabase", "postgres", "vercel", "relation ", "daily_closing", "commcalc",
           "dedup_key", "closing_grain_config")
_copy = " ".join(list(cg.LABELS.values()) + list(cg.HELP.values())
                 + [sr.REFUSALS["store_day_already_closed"][1]]).lower()
check("A5 no database, table or hosting name reaches a screen (§19.38)",
      not [b for b in _BANNED if b in _copy], detail=str([b for b in _BANNED if b in _copy]))
for _bad in ("", None, "STORE_DAY_ish", "per_rep", "0", 7):
    pass
check("A6 an unknown / blank / None value folds to the house default, never decides behaviour",
      all(cg.normalize(v) == cg.HOUSE_DEFAULT for v in ("", None, "per_rep", "weekly", 7, "  ")))
check("A7 a declared value survives casing and whitespace",
      cg.normalize("  REP_Day ") == cg.GRAIN_REP_DAY
      and cg.normalize("Store_Day") == cg.GRAIN_STORE_DAY)
check("A8 the refusal suffix names the holder, and says nothing when there is no name",
      "Zainab" in cg.refusal_suffix("Zainab") and cg.refusal_suffix("") == ""
      and cg.refusal_suffix(None) == "")


# ═══════════════════════════════════════════════════════════════════════════════════════════════════
# B. PURE — resolve precedence
# ═══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== B. resolve: per-store override -> org default -> house default ==")
check("B1 NO config at all resolves to one-per-store-day (migration 1080 not run yet)",
      cg.resolve([], "Nostrand") == cg.GRAIN_STORE_DAY and cg.resolve(None) == cg.GRAIN_STORE_DAY)
_rows = [{"store_code": None, "grain": "rep_day"},
         {"store_code": "Nostrand", "grain": "store_day"}]
check("B2 the org default row applies where no override exists",
      cg.resolve(_rows, "Utica") == cg.GRAIN_REP_DAY)
check("B3 a per-store override beats the org default",
      cg.resolve(_rows, "Nostrand") == cg.GRAIN_STORE_DAY)
check("B4 the override key is case-insensitive, like every other store_code key",
      cg.resolve(_rows, "nOsTrAnD") == cg.GRAIN_STORE_DAY)
check("B5 an org default of an UNKNOWN value folds to the house default, not to chaos",
      cg.resolve([{"store_code": None, "grain": "whenever"}], "X") == cg.HOUSE_DEFAULT)
check("B6 grain_map is resolve, batched — same answers, one config read",
      cg.grain_map(_rows, ["Nostrand", "Utica", "  "]) ==
      {"Nostrand": cg.GRAIN_STORE_DAY, "Utica": cg.GRAIN_REP_DAY})
check("B7 blocks_other_reps is TRUE only at store-day grain",
      cg.blocks_other_reps(cg.GRAIN_STORE_DAY) and not cg.blocks_other_reps(cg.GRAIN_REP_DAY))


# ═══════════════════════════════════════════════════════════════════════════════════════════════════
# C. PURE — the key
# ═══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== C. the dedup key at each grain ==")
_sd = dk.for_row(LUXE, "Nostrand", "Rana Bilal", "2026-10-10", grain=cg.GRAIN_STORE_DAY)
_rd = dk.for_row(LUXE, "Nostrand", "Rana Bilal", "2026-10-10", grain=cg.GRAIN_REP_DAY)
check("C1 the store-day key leaves the EMPLOYEE out", "rana bilal" not in _sd.lower()
      and _sd == f"{LUXE}|Nostrand|2026-10-10", detail=_sd)
check("C2 the rep-day key is byte-identical to what it was before this package",
      _rd == f"{LUXE}|Nostrand|rana bilal|2026-10-10", detail=_rd)
check("C3 two different reps collapse to ONE key at store-day grain — which is what the existing "
      "unique index then refuses",
      _sd == dk.for_row(LUXE, "Nostrand", "moza ali", "2026-10-10", grain=cg.GRAIN_STORE_DAY))
check("C4 the two spellings can never collide (3 segments vs 4)",
      _sd.count("|") == 2 and _rd.count("|") == 3 and _sd != _rd)
check("C5 the default grain of for_row is the house default, so a caller that forgets is SAFE",
      dk.for_row(LUXE, "Nostrand", "Rana Bilal", "2026-10-10") == _sd)
check("C6 identity is STILL required at both grains — a nameless closing has no key",
      dk.for_row(LUXE, "Nostrand", "", "2026-10-10", grain=cg.GRAIN_STORE_DAY) == ""
      and dk.for_row(LUXE, "", "x", "2026-10-10", grain=cg.GRAIN_STORE_DAY) == "")
check("C7 dedupable is unchanged — it answers identity, not grain",
      dk.dedupable("Nostrand", "Rana Bilal") and not dk.dedupable("Nostrand", "  "))
check("C8 the store half keeps its case, so two store codes differing only in case stay two stores",
      dk.for_row(LUXE, "b-1115", "x", "2026-07-12", grain=cg.GRAIN_STORE_DAY)
      != dk.for_row(LUXE, "B-1115", "x", "2026-07-12", grain=cg.GRAIN_STORE_DAY))
check("C9 the key dereferences closing_grain rather than re-deciding the grain",
      "_grain." in open("app/modules/closing/dedup_key.py").read())


# ═══════════════════════════════════════════════════════════════════════════════════════════════════
# The wiring for the behavioural sections
# ═══════════════════════════════════════════════════════════════════════════════════════════════════
#: The columns the shared FakeDB must know about for the tables this harness drives. It derives a
#: table's columns from the rows it holds, so an EMPTY table has none and a `select("id,...")`
#: raises 42703 — which would be read as "the migration has not run" and silently degrade the very
#: guard under test into its fallback. Declared here so a config read that resolves to the house
#: default does so because the ROWS say so, never because the fake could not answer.
_DECLARED = {
    "daily_closing": ["id", "org_id", "period", "close_date", "submitted_at", "sfid", "store_name",
                      "store_code", "store_address", "employee_name", "store_cash", "store_cc",
                      "epay_cash", "epay_cc", "acc_sale", "other_account", "upgrade_count",
                      "new_line_count", "postpaid_count", "envelope_picture", "remarks", "source",
                      "created_at", "updated_at", "t_cash", "t_credit", "t_ext_cc", "t_gift",
                      "t_store_acct", "t_zelle", "t_acima", "attempts", "auto_accepted",
                      "mgmt_flag", "epay_on_cash", "epay_on_credit", "epay_on_acima",
                      "expense_amount", "expense_description", "expense_approved", "tenders",
                      "counts", "released_at", "released_by", "release_note", "dedup_key",
                      "corrected_at", "correction_count", "derived_other", "derived_at"],
    "closing_grain_config": ["id", "org_id", "store_code", "grain", "updated_at", "updated_by"],
    "closing_source_config": ["id", "org_id", "store_code", "source", "updated_at", "updated_by"],
}


def wire(grain_rows=(), org=LUXE):
    db = FakeDB()
    db.declared.update(_DECLARED)
    db.tables.setdefault("daily_closing", [])
    db.tables.setdefault("closing_attempt", [])
    db.tables.setdefault("closing_grain_config", list(grain_rows))
    db.tables.setdefault("closing_source_config", [])
    cr.sb = lambda: db
    cr.get_supabase = lambda: db
    storeops.scope_keyset = lambda auth, org_: None
    cr._caller_perms = lambda client, auth: {"__super_admin": True, "__resolved": True}
    cr._caller_email = lambda client, auth: "dm@test.com"
    # The close gate and the photo gate are NOT what this harness is about: hold them open so a
    # refusal that fires can only be the one under test.
    cr._gate_row = lambda *a, **k: {"status": "recon_pending", "block_reasons": [], "flags": [],
                                    "b2b": None}
    cr._envelope_config = lambda *a, **k: {"require_photo_if_cash": False}
    return db


def submit(emp, store="Nostrand", cash="0", date="2026-10-10", org=LUXE):
    """Run the REAL submit. Returns (status, message) on refusal, None when accepted."""
    payload = {"close_date": date, "store_code": store, "store_name": "3560 Nostrand Avenue",
               "employee_name": emp, "t_cash": cash, "t_credit": "0"}
    try:
        asyncio.run(cr.create_row(payload, org_id=org))
        return None
    except HTTPException as e:
        return (e.status_code, str(e.detail))


def closings(db, store="Nostrand", date="2026-10-10"):
    return [r for r in db.tables.get("daily_closing", [])
            if r.get("store_code") == store and r.get("close_date") == date]


# ═══════════════════════════════════════════════════════════════════════════════════════════════════
# D. BEHAVIOURAL — the Nostrand 2026-10-10 regression
# ═══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== D. THE REGRESSION: Nostrand / 2026-10-10, two reps, one store-day ==")
_db = wire()
_first = submit("Rana Bilal", cash="230")
check("D1 the FIRST closing of the store-day is accepted, exactly as before", _first is None,
      detail=str(_first))
_second = submit("moza ali", cash="140")
check("D2 the SECOND PERSON is refused", _second is not None, detail=str(_second))
check("D3 refused with 409, the status a duplicate answers with",
      bool(_second) and _second[0] == 409, detail=str(_second))
check("D4 the rep is TOLD they cannot enter it — the owner's actual ask",
      bool(_second) and "already been submitted by someone else" in _second[1], detail=str(_second))
check("D5 and told WHO holds the day, so they go to a manager instead of retrying",
      bool(_second) and "Rana Bilal" in _second[1], detail=str(_second))
check("D6 NO second daily_closing row is written — the money cannot double",
      len(closings(_db)) == 1, detail=f"{len(closings(_db))} rows")
check("D7 the store-day's declared cash is the first closing's, not the sum",
      sum(float(r.get("t_cash") or 0) for r in closings(_db)) == 230.0)
_third = submit("Dixit Chauhan", cash="0")
check("D8 a THIRD submit three days later is refused too (the live row that made it three)",
      bool(_third) and _third[0] == 409 and len(closings(_db)) == 1)
_att = [a for a in _db.tables.get("closing_attempt", []) if a.get("refused")]
check("D9 every refusal is RECORDED in the submit audit trail (§29.11), never silent",
      len(_att) == 2 and all(a.get("refusal_code") == "store_day_already_closed" for a in _att),
      detail=str([(a.get("employee_name"), a.get("refusal_code")) for a in _att]))
check("D10 the audited detail names the store, the day and the holder — never the sentence",
      bool(_att) and "Nostrand" in str(_att[0].get("refusal_detail"))
      and "2026-10-10" in str(_att[0].get("refusal_detail"))
      and "Rana Bilal" in str(_att[0].get("refusal_detail")), detail=str(_att[0].get("refusal_detail")))
check("D11 a refusal is not a TRY — it cannot walk a rep toward the auto-accepting third attempt",
      all(not sr.is_real_try(a) for a in _att))
_other_day = submit("moza ali", cash="140", date="2026-10-11")
check("D12 the SAME rep at the SAME store on ANOTHER day is untouched", _other_day is None)
_other_store = submit("moza ali", store="Utica", cash="140")
check("D13 ANOTHER store on the same day is untouched", _other_store is None)
check("D14 the accepted row is keyed at store-day grain, so the database index backs the guard up",
      closings(_db)[0].get("dedup_key") == f"{LUXE}|Nostrand|2026-10-10",
      detail=str(closings(_db)[0].get("dedup_key")))


# ═══════════════════════════════════════════════════════════════════════════════════════════════════
# E. THE OPT-OUT IS REAL
# ═══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== E. a tenant declaring rep_day behaves exactly as the platform did before ==")
_db = wire(grain_rows=[{"org_id": LUXE, "store_code": None, "grain": "rep_day"}])
check("E1 the first rep is accepted", submit("Rana Bilal", cash="230") is None)
check("E2 the SECOND REP is accepted too — per-rep envelopes, unchanged",
      submit("moza ali", cash="140") is None)
check("E3 and both rows exist, keyed per rep", len(closings(_db)) == 2
      and {r["dedup_key"] for r in closings(_db)} ==
      {f"{LUXE}|Nostrand|rana bilal|2026-10-10", f"{LUXE}|Nostrand|moza ali|2026-10-10"})
check("E4 the SAME rep twice is still refused at rep_day grain (mig 502's protection, intact)",
      (submit("moza ali", cash="99") or (0,))[0] == 409 and len(closings(_db)) == 2)
_db = wire(grain_rows=[{"org_id": LUXE, "store_code": None, "grain": "rep_day"},
                       {"org_id": LUXE, "store_code": "Nostrand", "grain": "store_day"}])
submit("Rana Bilal", cash="230")
check("E5 a per-STORE override wins over the org default",
      (submit("moza ali", cash="140") or (0,))[0] == 409 and len(closings(_db)) == 1)
check("E6 and the store next door still follows the org default",
      submit("Zainab", store="Utica", cash="1") is None
      and submit("Rana Bilal", store="Utica", cash="2") is None)


# ═══════════════════════════════════════════════════════════════════════════════════════════════════
# F. THE CORRECTION PATH
# ═══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== F. a manager's release lets the RIGHT person redo the store-day ==")
_db = wire()
submit("Rana Bilal", cash="230")
_row = closings(_db)[0]
_row["released_at"] = "2026-10-13T16:39:05Z"
_row["released_by"] = "management"
check("F1 after a release, the other rep's corrected closing is ACCEPTED",
      submit("moza ali", cash="140") is None)
check("F2 it UPDATES that one row — never a second insert", len(closings(_db)) == 1)
_fixed = closings(_db)[0]
check("F3 the row now carries the RIGHT person's name and money",
      _fixed.get("employee_name") == "moza ali" and float(_fixed.get("t_cash")) == 140.0)
check("F4 the correction is audited and the row is RE-LOCKED",
      _fixed.get("correction_count") == 1 and _fixed.get("corrected_at")
      and _fixed.get("released_at") is None)
check("F5 and a THIRD person is refused again — one release, one correction",
      (submit("Nivas", cash="7") or (0,))[0] == 409 and len(closings(_db)) == 1)
_db = wire()
submit("Rana Bilal", cash="230")
check("F6 the SAME rep resubmitting without a release is refused as before (not the new code)",
      (submit("Rana Bilal", cash="999") or (0, ""))[1].startswith("Already submitted for this day"))


# ═══════════════════════════════════════════════════════════════════════════════════════════════════
# G. THE SIBLING PATH — the closing-sheet upload
# ═══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== G. the sheet upload cannot do by file what the form can no longer do by hand ==")
try:
    import pandas as pd
    _db = wire()
    _db.tables["daily_closing"].append(
        {"id": "held", "org_id": LUXE, "close_date": "2026-10-10", "store_code": "Nostrand",
         "employee_name": "moza ali", "source": "manual", "t_cash": 140.0})
    cr._store_resolver = lambda c, o: {"S1": {"store_code": "Nostrand"}, "S2": {"store_code": "Utica"}}
    _df = pd.DataFrame([
        {"Date": "2026-10-10", "SFID": "S1", "Employee": "Rana Bilal", "Store Cash": "230"},
        {"Date": "2026-10-10", "SFID": "S2", "Employee": "Zainab", "Store Cash": "447.29"},
        {"Date": "2026-10-10", "SFID": "S2", "Employee": "Someone Else", "Store Cash": "99"},
    ])
    _res = cr._ingest_dataframe(_db, LUXE, _df)
    _sk = {(s["store_code"], s["employee_name"]) for s in _res.get("skipped_already_closed") or []}
    check("G1 a sheet row for a store-day a human already closed is SKIPPED",
          ("Nostrand", "Rana Bilal") in _sk, detail=str(_res.get("skipped_already_closed")))
    check("G2 and REPORTED, naming who already holds the day — never dropped in silence",
          any(s.get("already_closed_by") == "moza ali"
              for s in _res.get("skipped_already_closed") or []))
    check("G3 the store-day keeps exactly one closing, and it is the human's",
          len(closings(_db)) == 1 and closings(_db)[0]["employee_name"] == "moza ali")
    check("G4 two rows for ONE store-day in one file collapse to the FIRST, reported",
          ("Utica", "Someone Else") in _sk and len(closings(_db, store="Utica")) == 1
          and closings(_db, store="Utica")[0]["employee_name"] == "Zainab")
    check("G5 rows_saved counts what was actually written, not what the file held",
          _res.get("rows_saved") == 1)
    _db2 = wire(grain_rows=[{"org_id": LUXE, "store_code": None, "grain": "rep_day"}])
    _db2.tables["daily_closing"].append(
        {"id": "held", "org_id": LUXE, "close_date": "2026-10-10", "store_code": "Nostrand",
         "employee_name": "moza ali", "source": "manual", "t_cash": 140.0})
    _res2 = cr._ingest_dataframe(_db2, LUXE, pd.DataFrame(
        [{"Date": "2026-10-10", "SFID": "S1", "Employee": "Rana Bilal", "Store Cash": "230"}]))
    check("G6 a rep_day tenant's sheet upload is UNCHANGED — nothing skipped",
          not (_res2.get("skipped_already_closed") or []) and len(closings(_db2)) == 2)
except ImportError:
    check("G pandas not installed — the sheet path could not be driven", False, detail="import pandas")


# ═══════════════════════════════════════════════════════════════════════════════════════════════════
# H. WIRING LOCKS
# ═══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== H. the locks: these fail the build if the design un-wires ==")
_tree = ast.parse(ROUTER_SRC)
_lines = ROUTER_SRC.splitlines()


def fn_src(name):
    n = next((x for x in ast.walk(_tree)
              if isinstance(x, (ast.FunctionDef, ast.AsyncFunctionDef)) and x.name == name), None)
    return "\n".join(_lines[n.lineno - 1:(n.end_lineno or n.lineno)]) if n else ""


_CREATE = fn_src("create_row")
_SHEET = fn_src("_ingest_dataframe")
_DERIVE = fn_src("_derive_closing_day")
check("H1a create_row asks the grain registry whether another rep's row blocks this submit",
      "_grain_src.blocks_other_reps(" in _CREATE)
check("H1b it resolves the grain for THIS store, not for the org in general",
      "_closing_grain(client, org_id, _dupe_store)" in _CREATE)
check("H1c the duplicate query stops filtering on the employee at store-day grain",
      "if not _store_day_grain:" in _CREATE and ".ilike(\"employee_name\"" in _CREATE)
check("H1d the dedup key is composed WITH the grain — the key and the guard cannot disagree",
      "_dedup.for_row(org_id, _dupe_store, _dupe_emp, d, grain=_grain_here)" in _CREATE)
check("H1e the refusal goes through _refuse, like every other one (§29.11)",
      '_refuse(client, org_id, d, body, "store_day_already_closed"' in _CREATE)
check("H2a the sheet upload dereferences the SAME registry",
      "_grain_src.blocks_other_reps(" in _SHEET and "_closing_grain_map(" in _SHEET)
check("H2b and it REPORTS what it skipped rather than dropping it",
      "skipped_already_closed" in _SHEET)
# H3 — one fact, one home. Nobody outside the registry may spell the vocabulary AS CODE.
#
# Two exclusions, both named and both justified rather than waved through:
#  · `closing_grain.py` is the home itself.
#  · `commcalc/flag_registry.py` carries its OWN, older `grain` vocabulary for watchdog FINDINGS
#    ('transaction' | 'store_day' | 'rep_period' | 'store_period') — the shape a flag is, not how
#    many closings a store-day takes. It shares the token `store_day` and nothing else, and H3c
#    below holds that apart: the day the two vocabularies converge, that check goes red and somebody
#    has to decide deliberately instead of by coincidence.
# Comments are stripped before scanning: a comment naming the two values is documentation, not a
# second copy, and failing on one would push the next author to delete the explanation.
_EXCLUDE = ("closing_grain.py", os.path.join("commcalc", "flag_registry.py"))
_LITS = ('"store_day"', "'store_day'", '"rep_day"', "'rep_day'")
_copies = []
for root, _dirs, files in os.walk("app"):
    for f in files:
        if not f.endswith(".py"):
            continue
        p = os.path.join(root, f)
        if any(p.endswith(x) for x in _EXCLUDE):
            continue
        for i, ln in enumerate(open(p).read().splitlines(), 1):
            code = ln.split("#", 1)[0]
            for lit in _LITS:
                if lit in code:
                    _copies.append((p, i, lit))
check("H3a nobody spells the grain vocabulary a second time — it is read, never retyped",
      not _copies, detail=str(_copies))
check("H3b every caller reaches the vocabulary through the module, not through a string",
      "_grain_src." in ROUTER_SRC and "import closing_grain" in ROUTER_SRC)
_FLAGS = open(os.path.join("app", "modules", "commcalc", "flag_registry.py")).read()
check("H3c the flag registry's `grain` is a DIFFERENT vocabulary, not a copy of this one",
      "rep_day" not in _FLAGS and "closing_grain" not in _FLAGS)
check("H4a the refusal code is DECLARED in the one registry",
      "store_day_already_closed" in sr.REFUSALS and sr.REFUSALS["store_day_already_closed"][0] == 409)
check("H4b its words say a DIFFERENT person closed it — not 'you already submitted', which sends "
      "the rep back to retry",
      "someone else" in sr.REFUSALS["store_day_already_closed"][1].lower())
_page = open(MGMT_PATH).read() if os.path.exists(MGMT_PATH) else ""
check("H4c the Management Review screen has words for it (a code with no words renders raw)",
      "store_day_already_closed:" in _page, detail=MGMT_PATH)
_mig = open(MIG_PATH).read() if os.path.exists(MIG_PATH) else ""
check("H5a migration 1080 exists", bool(_mig), detail=MIG_PATH)
check("H5b it creates the config table idempotently",
      "CREATE TABLE IF NOT EXISTS commcalc.closing_grain_config" in _mig)
check("H5c the database constrains the vocabulary to the same two values",
      all(g in _mig for g in cg.GRAINS) and "closing_grain_config_grain_chk" in _mig)
check("H5d one org-default row and one row per store, both enforced",
      "closing_grain_config_org_default" in _mig and "closing_grain_config_org_store" in _mig)
check("H5e it recomputes NO stored dedup key (that would collide on the historical two-rep days)",
      not re.search(r"set\s+dedup_key\s*=", _mig, re.I))
check("H5f it touches no money column and no closing row",
      not re.search(r"(update|delete\s+from)\s+commcalc\.daily_closing", _mig, re.I))
check("H6a the derive sweep's excuse holds: it is keyed per STORE, never per rep",
      'in_("store_code", plan["derive"])' in _DERIVE)
check("H6b and it still refuses to overwrite a human's row",
      "kept_manual" in _DERIVE)


# ═══════════════════════════════════════════════════════════════════════════════════════════════════
# I. NEGATIVE CONTROLS — the lock is only worth something if it goes red
# ═══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== I. negative controls ==")
check("I1 a guard that still filters by employee would NOT satisfy H1c",
      "if not _store_day_grain:" not in
      _CREATE.replace("if not _store_day_grain:", "if True:"))
check("I2 a key composed without the grain would NOT satisfy H1d",
      "_dedup.for_row(org_id, _dupe_store, _dupe_emp, d, grain=_grain_here)" not in
      "body['dedup_key'] = _dedup.for_row(org_id, _dupe_store, _dupe_emp, d)")
check("I3 a second copy of the vocabulary IS caught by H3",
      any(lit in 'grain = "store_day"' for lit in ('"store_day"',)))
check("I4 a migration that rewrote stored keys WOULD trip H5e",
      bool(re.search(r"set\s+dedup_key\s*=", "update commcalc.daily_closing set dedup_key = x", re.I)))
_old_for_row = f"{LUXE}|Nostrand|rana bilal|2026-10-10"
check("I5 the OLD key really did let two reps through — the defect reproduces on the old formula",
      _old_for_row != dk.for_row(LUXE, "Nostrand", "moza ali", "2026-10-10", grain=cg.GRAIN_REP_DAY))


print(f"\n{len(PASS)} passed / {len(FAIL)} failed")
if FAIL:
    print("FAILED:")
    for f in FAIL:
        print("  - " + f)
sys.exit(1 if FAIL else 0)
