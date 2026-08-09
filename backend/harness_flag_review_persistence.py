"""HARNESS — flag REVIEW PERSISTENCE: recalculation must ADD, never wipe (migration 287).

OWNER DIRECTIVE 2026-08-08, verbatim:
    "DM review should not be erased and teh new data should only add the missing data if any"

THE DEFECT
──────────
`_run_calculation` DELETED every `commcalc.flags` row for the period and re-inserted the whole set, and
`_do_dlar_sweep` recalculates Boost DAILY — so a district manager's review was erased within 24 hours.
Migration 285/286 routed flags to the right DM; that is worth nothing if the decision does not survive
the night.

WHAT THIS PROVES
────────────────
  A. IDENTITY is deterministic, spelling-proof, and independent of the display payload — the same flag
     produces the same `flag_key` on every run, and a changed AMOUNT does not create a second row.
  B. Identity CHANGES exactly when the accusation changes (different rep, different flag type), and
     adding a store alias cannot silently re-key a flag that already has a row-level identifier.
  C. The producers now carry a stable identifier: an MI row with NO MDN and NO IMEI — the 17,662-row
     class — is keyed on its `subscriber_id`, and the ones that genuinely cannot be keyed are counted,
     not hidden.
  D. The WIRING: the primary write path calls the additive merge, the wholesale DELETE survives only
     as the pre-migration fallback, the retire step is scoped to this module's own sources, and the
     DAILY DLAR sweep goes through exactly that path.
  E. The Python identity and the SQL identity (`commcalc.flag_key_material`) agree KEY FOR KEY over
     the REAL rows in both tenants. (live, READ-ONLY)
  F. THE INVARIANT, end to end against the real RPCs: mark a flag reviewed → run the merge with a
     CHANGED amount → the review SURVIVES and the amount refreshes; a genuinely-new flag still appears;
     a flag whose condition cleared is RETIRED (status, not DELETE) with its review intact; a returning
     condition reopens the same row; another module's flags and another tenant's flags are untouched.
     (live, inside a transaction that ROLLS BACK — nothing is left behind)
  G. THE DAILY SWEEP: the exact double-run `_do_dlar_sweep` performs is idempotent — the second run
     inserts nothing, deletes nothing, and the review is still there. (live, ROLLED BACK)
  H. A REAL recalculation of the flag half: the actual `calc_portout_flags` output built from the
     org's REAL `raw_mi` rows, merged through the real RPC twice, with a review in between.
     (live, ROLLED BACK)

Sections A–D are OFFLINE (pure, no database). E–H need `tools/sbsql.py` + a Supabase PAT; E is
read-only and F–H run inside `BEGIN … ROLLBACK` so the live table is never modified.

    python3 backend/harness_flag_review_persistence.py
"""

import io
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))

from app.modules.commcalc import flag_persist as FP            # noqa: E402
from app.modules.commcalc.portout_flags import calc_portout_flags  # noqa: E402
from app.modules.commcalc.flags import calc_flags              # noqa: E402

PASS, FAIL = [], []
HOUSE = "00000000-0000-0000-0000-000000000001"
SENTINEL_PERIOD = "HARNESS287 2999"      # never used by real data; every write is rolled back anyway


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"   [{detail}]" if detail else ""))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("A. identity is deterministic, spelling-proof, and payload-independent")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
BASE = {"period": "June 2026", "period_month": 6, "period_year": 2026,
        "flag_type": "PORT_OUT_30DAY", "source": "mi_report", "severity": "CRITICAL",
        "store_address": "", "store_code": "B-3PL", "epay_salesperson": "jdoe",
        "mdn": "9175551212", "imei": "", "subscriber_id": "SUB-1", "amount": 45.0,
        "description": "Ported out after 12 days", "coaching_note": "n"}


def k(**over):
    r = dict(BASE, **over)
    FP.assign_keys([r])
    return r["flag_key"]


check("A1 the same flag hashes to the same key twice", k() == k())
check("A2 'June 2026' and '2026-06' produce the SAME key (the period-spelling bug class)",
      k(period="June 2026") == k(period="2026-06"),
      "canon " + FP.period_canon(BASE))
check("A3 a CHANGED AMOUNT keeps the key — it refreshes the row, it does not fork it",
      k(amount=45.0) == k(amount=99.99))
check("A4 a changed description/severity/coaching_note keeps the key",
      k() == k(description="totally different", severity="LOW", coaching_note="x"))
check("A5 a changed days_active / phone_model / plan keeps the key",
      k() == k(days_active=99, phone_model="Moto G", customer_plan="Unl 5G"))
check("A6 the key is 32 hex chars (md5, matching Postgres md5())",
      bool(re.fullmatch(r"[0-9a-f]{32}", k())))
check("A7 identity is case/whitespace insensitive on every component",
      k() == k(flag_type=" port_out_30day ", source="MI_Report",
               epay_salesperson=" JDoe ", mdn=" 9175551212 "))

_rows = [dict(BASE), dict(BASE, amount=1), dict(BASE, amount=2)]
FP.assign_keys(_rows)
check("A8 three genuinely-interchangeable flags get three DISTINCT ordinal keys",
      len({r["flag_key"] for r in _rows}) == 3)
_again = [dict(BASE, amount=2), dict(BASE, amount=1), dict(BASE)]   # same multiset, different order
FP.assign_keys(_again)
check("A9 …and the ordinals are assigned the SAME way regardless of input ORDER",
      {r["flag_key"] for r in _rows} == {r["flag_key"] for r in _again})

# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\nB. identity changes exactly when the ACCUSATION changes")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
check("B1 a different REP is a different flag (a DM's ruling on rep A is not a ruling on rep B)",
      k() != k(epay_salesperson="asmith"))
check("B2 a different FLAG TYPE is a different flag", k() != k(flag_type="PORT_OUT_60DAY"))
check("B3 a different PERIOD is a different flag",
      k() != k(period="July 2026", period_month=7))
check("B4 a different SOURCE is a different flag", k() != k(source="sales"))
check("B5 a different SUBSCRIBER is a different flag, when that is the identity",
      k(mdn="", imei="") != k(mdn="", imei="", subscriber_id="SUB-2"))
check("B6 resolving the STORE later (adding an alias) does NOT re-key a flag that has an identifier",
      k(store_code=None, store_address="") == k(store_code="B-9999", store_address=""))
check("B7 …but the store IS the identity when the row has no identifier at all "
      "(HIGH_PORT_OUT_RATE / MISSING_STORE_*)",
      k(mdn="", imei="", subscriber_id="", epay_salesperson="", store_address="12 Main St")
      != k(mdn="", imei="", subscriber_id="", epay_salesperson="", store_address="99 Other Rd"))
check("B8 imei outranks mdn outranks subscriber_id outranks source_ref (the ladder is ordered)",
      FP.ident_of(dict(BASE, imei="IM1", mdn="M", subscriber_id="S", source_ref="R")) == "IM1"
      and FP.ident_of(dict(BASE, imei="", mdn="M", subscriber_id="S", source_ref="R")) == "M"
      and FP.ident_of(dict(BASE, imei="", mdn="", subscriber_id="S", source_ref="R")) == "S"
      and FP.ident_of(dict(BASE, imei="", mdn="", subscriber_id="", source_ref="R")) == "R")
check("B9 the merge payload NEVER carries reviewed_by / reviewed_at / action_taken",
      not ({"reviewed_by", "reviewed_at", "action_taken"} & set(FP._ROW_FIELDS)),
      "fields=" + str(len(FP._ROW_FIELDS)))

# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\nC. the producers now carry a stable identifier — including the 17,662-row class")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
MI = [
    # the identifier-less class: no phone_number, no device_serial, blank plan, $0 MRC — but it DOES
    # carry a subscriber_id (true of 100% of them: 53,978 raw_mi rows checked, all five 2026 periods)
    {"subscriber_id": "SUB-NOID-1", "subscriber_status": "PORTED-OUT", "phone_number": "",
     "device_serial": "", "customer_plan": "", "base_mrc": 0,
     "mi_activation_date": "2026-06-01", "residual_transfer_out_date": "2026-06-10",
     "salesforce_id": "0018000000caWUpAAM"},
    {"subscriber_id": "SUB-NOID-2", "subscriber_status": "INVOLUNTARY-SUSPENDED", "phone_number": "",
     "device_serial": "", "customer_plan": "", "base_mrc": 0,
     "mi_activation_date": "2026-06-01", "mi_deactivation_date": "2026-06-20",
     "salesforce_id": "0018000000caWUpAAM"},
    # a normal one, sold in-period, with both identifiers
    {"subscriber_id": "SUB-3", "subscriber_status": "PORTED-OUT", "phone_number": "9175551212",
     "device_serial": "350000000000001", "customer_plan": "Unl", "base_mrc": 25,
     "mi_activation_date": "2026-06-01", "residual_transfer_out_date": "2026-06-05",
     "salesforce_id": "0018000000caWUpAAM"},
]
SALES = [{"mdn": "9175551212", "salesperson": "jdoe", "store": "3 Palisade Ave Yonkers",
          "serial_1": "350000000000001", "product_desc": "Moto G"}]
MAPPING = [{"store_code": "B-3PL", "store_address": "3 Palisade Ave",
            "salesforce_id": "0018000000caWUpAAM"}]

po = calc_portout_flags(MI, SALES, MAPPING, "June 2026", 6, 2026)
hist = FP.assign_keys(po)
check("C1 calc_portout_flags emits a flag per MI condition", len(po) == 3, f"{len(po)}")
check("C2 the no-MDN/no-IMEI rows are keyed on SUBSCRIBER_ID, not on nothing",
      sorted(f["key_basis"] for f in po) == ["imei", "subscriber", "subscriber"], str(hist))
check("C3 …and every one of them has a key", all(f.get("flag_key") for f in po))
check("C4 subscriber_id is PERSISTED on the flag (it is what makes the key reproducible)",
      all(f.get("subscriber_id") for f in po))
check("C5 two identifier-less flags on the same door do NOT collide",
      len({f["flag_key"] for f in po}) == 3)

# re-run the identical inputs — this is exactly what the daily sweep does
po2 = calc_portout_flags(MI, SALES, MAPPING, "2026-06", 6, 2026)     # note: the OTHER spelling
FP.assign_keys(po2)
check("C6 re-running the producer on the same MI data reproduces the SAME keys "
      "(even under the other period spelling)",
      {f["flag_key"] for f in po} == {f["flag_key"] for f in po2})

# an MI row whose MRC moved — the amount must refresh on the SAME flag
MI_CHANGED = [dict(MI[0], base_mrc=31.5), MI[1], MI[2]]
po3 = calc_portout_flags(MI_CHANGED, SALES, MAPPING, "June 2026", 6, 2026)
FP.assign_keys(po3)
check("C7 a CHANGED MRC refreshes the same flag rather than forking it",
      {f["flag_key"] for f in po} == {f["flag_key"] for f in po3}
      and any(f["amount"] == 31.5 for f in po3))

PAY = [{"category": "Chargeback", "amount": -50, "imei": "", "mdn": "", "payment_type": "REBATE CB",
        "payment_date": "2026-06-09", "business_address": "3 Palisade Ave", "rep_username": "jdoe"},
       {"category": "", "payment_type": "MYSTERY FEE", "amount": 12,
        "business_address": "3 Palisade Ave"}]
cf = calc_flags(sales=SALES, pay_detail=PAY, mi_rows=[], dlar_store=[], store_mapping=MAPPING,
                period="June 2026", period_month=6, period_year=2026)
FP.assign_keys(cf)
_ump = [f for f in cf if f["flag_type"] == "UNMAPPED_PAYMENT_TYPE"]
check("C8 UNMAPPED_PAYMENT_TYPE carries the payment type as its identity "
      "(its description embeds a row count and a $ total, so it can never be the key)",
      len(_ump) == 1 and _ump[0].get("source_ref") == "MYSTERY FEE"
      and _ump[0]["key_basis"] == "ref")
_cb = [f for f in cf if f["flag_type"] == "CHARGEBACK"]
check("C9 a chargeback with neither IMEI nor MDN still gets an identity from its payment type + date",
      len(_cb) == 1 and _cb[0]["key_basis"] == "ref" and "REBATE CB" in (_cb[0].get("source_ref") or ""))
check("C10 key_basis 'none' is reported honestly rather than pretended stable",
      FP.key_basis({"flag_type": "X"}) == "none")

# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\nD. the WIRING — the wipe is gone from the primary path, and the daily sweep goes through it")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
HERE = os.path.dirname(os.path.abspath(__file__))
RT = io.open(os.path.join(HERE, "app/modules/commcalc/router.py"), encoding="utf-8").read()
SR = io.open(os.path.join(HERE, "app/modules/commcalc/sales_recon.py"), encoding="utf-8").read()

check("D1 the main flag pass calls the ADDITIVE merge",
      "flag_persist.sync(" in RT and RT.count("flag_persist.sync(") >= 2)
_del = "table('flags').delete()"
_fallback_guarded = all(
    "FlagPersistUnavailable" in RT[max(0, m.start() - 1400):m.start()]
    for m in re.finditer(re.escape(_del), RT))
check("D2 every remaining flag DELETE in router.py sits inside the "
      "pre-migration FlagPersistUnavailable fallback",
      _fallback_guarded, f"{RT.count(_del)} delete call(s)")
check("D3 the retire step is scoped to THIS module's sources, so a commcalc recalculation can no "
      "longer wipe the asset / payables / closing / account flags that share the table",
      "_CALC_FLAG_SOURCES = ('payment_detail', 'sales', 'dlar_store', 'mi_report')" in RT
      and "sources=_MAIN_FLAG_SOURCES" in RT and "sources=_INSTALLMENT_FLAG_SOURCES" in RT)
_emitted = set(re.findall(r"'source':\s*'([a-z_]+)'",
                          io.open(os.path.join(HERE, "app/modules/commcalc/flags.py"),
                                  encoding="utf-8").read()))
_emitted |= set(re.findall(r"'source':\s*'([a-z_]+)'",
                           io.open(os.path.join(HERE, "app/modules/commcalc/portout_flags.py"),
                                   encoding="utf-8").read()))
check("D4 the static source registry covers EVERY source the main pass can emit — including one that "
      "produced zero flags this run, which is exactly when its flags must be retired",
      _emitted <= {"payment_detail", "sales", "dlar_store", "mi_report"},
      "emits " + ",".join(sorted(_emitted)))
check("D5 the DAILY DLAR sweep reaches this path (_do_dlar_sweep -> _run_calculation)",
      "_cres = _run_calculation(res['period'], org_id)" in RT)
check("D6 sales_recon's per-sweep delete-by-source is additive too",
      "flag_persist.sync(" in SR)
check("D7 the active queue defaults to OPEN, with an explicit include_resolved escape hatch",
      "include_resolved: bool = False" in RT and "flag_persist.STATUS_OPEN" in RT)
check("D8 there is a DM review WRITE path at all (nothing has ever written reviewed_by)",
      '@router.post("/flags/{flag_id}/review")' in RT)
check("D9 the review endpoint is SPAN-enforced on the same two keys get_flags filters on",
      "in_keyset(ks, f.get('store_code'), f.get('store_address'))" in RT)
check("D10 the reviewer identity comes from the TOKEN, not from the request body",
      "_flag_reviewer_name(authorization" in RT)
check("D11 org_id is a QUERY PARAM on both new endpoints (contract §2)",
      RT.count("async def review_flag(flag_id: str, payload: dict = {}, authorization: str = Header(default=\"\"),\n                      org_id: str = ORG_ID)") == 1
      and "async def flags_key_health(period: str, authorization: str = Header(default=\"\"), org_id: str = ORG_ID)" in RT)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
SBSQL = "/workspaces/commcalc/tools/sbsql.py"


_TMP = os.path.join(os.environ.get("TMPDIR", "/tmp"), "harness_flag_review_persistence.sql")


def sql(q):
    """Run SQL through tools/sbsql.py.

    Always via a FILE: a real 400-flag payload is far past ARG_MAX as a command-line argument.
    NOTE: the Supabase management API returns only the LAST result set of a multi-statement batch,
    so every transactional section below collects its assertions into a temp table and selects that
    table once, at the end, immediately before ROLLBACK."""
    io.open(_TMP, "w", encoding="utf-8").write(q)
    r = subprocess.run([sys.executable, SBSQL, "-f", _TMP], capture_output=True, text=True,
                       timeout=300)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[-600:])
    return json.loads(r.stdout)


def lit(v):
    if v is None:
        return "null"
    return "'" + str(v).replace("'", "''") + "'"


LIVE = os.path.exists(SBSQL)

print("\nE. the Python identity and commcalc.flag_key_material agree, over the REAL rows (READ-ONLY)")
if not LIVE:
    print("  SKIP  tools/sbsql.py not present — offline run")
else:
    try:
        rows = sql("""
          select id::text, org_id::text, period, period_month, period_year, flag_type, source,
                 imei, mdn, subscriber_id, source_ref, epay_salesperson, store_address, store_code,
                 commcalc.flag_key_material(period, period_month, period_year, flag_type, source,
                     imei, mdn, subscriber_id, source_ref, epay_salesperson, store_address,
                     store_code) as mat,
                 commcalc.flag_key_basis(imei, mdn, subscriber_id, source_ref, epay_salesperson,
                     store_address, store_code) as basis,
                 key_basis
            from commcalc.flags
           order by md5(id::text)
           limit 4000""")
        bad_mat = [r for r in rows if FP._material(r) != r["mat"]]
        bad_bas = [r for r in rows if FP.key_basis(r) != r["basis"]]
        check("E1 material agrees byte-for-byte on every sampled real row",
              not bad_mat, f"{len(rows) - len(bad_mat)}/{len(rows)} agree")
        check("E2 key_basis agrees on every sampled real row",
              not bad_bas, f"{len(rows) - len(bad_bas)}/{len(rows)} agree")
        check("E3 the migration's backfill wrote the SAME basis the code computes",
              all(r["key_basis"] == r["basis"] for r in rows))
        orgs = {r["org_id"] for r in rows}
        check("E4 the sample spans BOTH tenants (a house-only proof proves nothing, contract §2)",
              len(orgs) >= 2, f"{len(orgs)} org(s)")
        nulls = sql("select count(*) c from commcalc.flags where flag_key is null")
        check("E5 no row was left without a key by the backfill", int(nulls[0]["c"]) == 0)
        dupes = sql("""select count(*) c from (
                         select org_id, flag_key from commcalc.flags
                          group by 1,2 having count(*) > 1) d""")
        check("E6 the backfilled keys are unique within a tenant (the ordinal de-collides them)",
              int(dupes[0]["c"]) == 0, f"{dupes[0]['c']} duplicate key(s)")
    except Exception as e:
        check("E  live identity parity", False, str(e)[:200])

# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\nF. THE INVARIANT — against the real RPCs, inside a transaction that ROLLS BACK")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
OTHER_ORG_Q = "(select org_id from commcalc.flags where org_id <> '%s'::uuid limit 1)" % HOUSE

# The management API hands back only the LAST result set, so each transactional section funnels its
# assertions into a temp table and selects it once, immediately before ROLLBACK.
F_SQL = """
BEGIN;
create temporary table _h(t text, v text, a text, b text, c text) on commit drop;

-- ── seed: one REVIEWED flag, one that will disappear, one foreign-module flag, one other-tenant row
insert into commcalc.flags
  (org_id, period, period_month, period_year, flag_type, source, severity, epay_salesperson,
   mdn, subscriber_id, amount, description, flag_key, key_basis, status, last_run_id,
   reviewed_by, reviewed_at, action_taken)
values
  -- reviewed, and the condition is STILL present (with a restated MRC)
  ('__HOUSE__'::uuid, '__P__', 1, 2999, 'PORT_OUT_30DAY', 'mi_report', 'CRITICAL', 'jdoe',
   '9175550001', 'SUB-A', 45.00, 'Ported out after 12 days', 'hk_reviewed', 'mdn', 'open',
   '00000000-0000-0000-0000-0000000000aa'::uuid,
   'dm@cellfonzrus.com', now(), 'Coached the rep; not chargeable'),
  -- reviewed, produced by a PREVIOUS additive run, condition has since CLEARED  -> 'resolved'
  ('__HOUSE__'::uuid, '__P__', 1, 2999, 'PORT_OUT_30DAY', 'mi_report', 'CRITICAL', 'jdoe',
   '9175550002', 'SUB-B', 30.00, 'Ported out after 3 days', 'hk_gone', 'mdn', 'open',
   '00000000-0000-0000-0000-0000000000aa'::uuid,
   'dm@cellfonzrus.com', now(), 'Chargeback approved'),
  -- a row that PREDATES the additive era (last_run_id NULL) -> bookkeeping, 'superseded'
  ('__HOUSE__'::uuid, '__P__', 1, 2999, 'PORT_OUT_30DAY', 'mi_report', 'CRITICAL', 'jdoe',
   '9175550004', 'SUB-D', 21.00, 'legacy row, no run id', 'hk_legacy', 'mdn', 'open', null,
   'dm@cellfonzrus.com', now(), 'Legacy review'),
  -- a flag written by ANOTHER MODULE, which this recalculation must not touch
  ('__HOUSE__'::uuid, '__P__', 1, 2999, 'ASSET_APPEAL_DENIED', 'asset_appeal', 'CRITICAL', 'jdoe',
   '9175550003', null, 99.00, 'a flag this module does not own', 'hk_foreign', 'mdn', 'open', null,
   'dm@cellfonzrus.com', now(), 'Appeal reviewed');

insert into commcalc.flags
  (org_id, period, period_month, period_year, flag_type, source, severity, amount, description,
   flag_key, key_basis, status, reviewed_by, action_taken)
select __OTHER__, '__P__', 1, 2999, 'PORT_OUT_30DAY', 'mi_report', 'CRITICAL', 7.00,
       'another tenant, SAME key', 'hk_reviewed', 'mdn', 'open', 'other-dm@x.com', 'other tenant note'
 where __OTHER__ is not null;

-- ── the recalculation: hk_reviewed comes back with a CHANGED amount, hk_new is brand new,
--    hk_gone is NOT produced (its condition cleared). One run id, exactly like _run_calculation.
insert into _h(t, v) select 'F0sync', (commcalc.flags_sync_batch(
  '__HOUSE__'::uuid, '11111111-1111-1111-1111-111111111111'::uuid,
  '[{"flag_key":"hk_reviewed","key_basis":"mdn","period":"__P__","period_month":1,
     "period_year":2999,"flag_type":"PORT_OUT_30DAY","source":"mi_report","severity":"HIGH",
     "mdn":"9175550001","subscriber_id":"SUB-A","amount":77.77,
     "description":"Ported out after 12 days - MRC restated"},
    {"flag_key":"hk_new","key_basis":"mdn","period":"__P__","period_month":1,"period_year":2999,
     "flag_type":"PORT_OUT_60DAY","source":"mi_report","severity":"HIGH","mdn":"9175550009",
     "subscriber_id":"SUB-C","amount":12.00,"description":"a genuinely new flag"}]'::jsonb))::text;

insert into _h(t, v) select 'F0res', (commcalc.flags_resolve_stale(
  '__HOUSE__'::uuid, '11111111-1111-1111-1111-111111111111'::uuid,
  array['__P__'], array['payment_detail','sales','dlar_store','mi_report'],
  'the condition was not present in the latest recalculation'))::text;

-- ── assertions
insert into _h(t, v, a, b)
select 'F1', (reviewed_by = 'dm@cellfonzrus.com'
          and action_taken = 'Coached the rep; not chargeable'
          and reviewed_at is not null)::text, amount::text, status
  from commcalc.flags where org_id='__HOUSE__'::uuid and flag_key='hk_reviewed';

insert into _h(t, v) select 'F2', count(*)::text from commcalc.flags
 where org_id='__HOUSE__'::uuid and flag_key='hk_new' and status='open';

insert into _h(t, v, a, b, c)
select 'F3', status, (reviewed_by='dm@cellfonzrus.com'
                  and action_taken='Chargeback approved')::text,
       (resolved_at is not null)::text, resolved_reason
  from commcalc.flags where org_id='__HOUSE__'::uuid and flag_key='hk_gone';

insert into _h(t, v, a)
select 'F4', status, (reviewed_by='dm@cellfonzrus.com')::text
  from commcalc.flags where org_id='__HOUSE__'::uuid and flag_key='hk_foreign';

insert into _h(t, v, a, b)
select 'F5', status, reviewed_by, amount::text
  from commcalc.flags where flag_key='hk_reviewed' and org_id <> '__HOUSE__'::uuid;

insert into _h(t, v, a, b)
select 'F8', status, (reviewed_by='dm@cellfonzrus.com' and action_taken='Legacy review')::text,
       (resolved_at is not null)::text
  from commcalc.flags where org_id='__HOUSE__'::uuid and flag_key='hk_legacy';

insert into _h(t, v, a) select 'F6', count(*)::text,
       count(*) filter (where status='open')::text from commcalc.flags
 where org_id='__HOUSE__'::uuid and period='__P__';

select * from _h order by t;
ROLLBACK;
""".replace("__HOUSE__", HOUSE).replace("__P__", SENTINEL_PERIOD).replace("__OTHER__", OTHER_ORG_Q)

if not LIVE:
    print("  SKIP  offline run")
else:
    try:
        by = {r["t"]: r for r in (sql(F_SQL) or []) if isinstance(r, dict) and r.get("t")}
        f1 = by.get("F1", {})
        check("F1 🔑 the DM's review SURVIVES the recalculation, and the AMOUNT refreshes on the "
              "same row (45.00 -> 77.77)",
              f1.get("v") == "true" and str(f1.get("a", "")).startswith("77.77")
              and f1.get("b") == "open", json.dumps(f1))
        check("F2 a genuinely-new flag still appears", by.get("F2", {}).get("v") == "1",
              json.dumps(by.get("F0sync")))
        f3 = by.get("F3", {})
        check("F3 a flag whose condition cleared is RETIRED, not deleted — status 'resolved', a "
              "timestamp, a reason, and its review still on the row",
              f3.get("v") == "resolved" and f3.get("a") == "true" and f3.get("b") == "true"
              and bool(f3.get("c")), json.dumps(f3))
        f4 = by.get("F4", {})
        check("F4 another MODULE's flag (source 'asset_appeal') is untouched — the old wholesale "
              "per-period DELETE wiped these too",
              f4.get("v") == "open" and f4.get("a") == "true", json.dumps(f4))
        f5 = by.get("F5", {})
        check("F5 another TENANT's row with the SAME flag_key is untouched (contract §2)",
              f5.get("v") == "open" and str(f5.get("b", "")).startswith("7")
              and f5.get("a") == "other-dm@x.com", json.dumps(f5))
        f8 = by.get("F8", {})
        check("F8 a row that PREDATES the additive era is retired as 'superseded' (bookkeeping), "
              "not accused of having cleared — and keeps its review too",
              f8.get("v") == "superseded" and f8.get("a") == "true" and f8.get("b") == "true",
              json.dumps(f8))
        check("F6 🔑 NOTHING was deleted — 4 seeded + 1 inserted = 5 rows still present, 3 of them "
              "still open (today's code would have left 2 rows and zero reviews)",
              by.get("F6", {}).get("v") == "5" and by.get("F6", {}).get("a") == "3",
              json.dumps(by.get("F6")))
        left = sql(f"select count(*) c from commcalc.flags where period = {lit(SENTINEL_PERIOD)}")
        check("F7 the transaction ROLLED BACK — zero harness rows left in the live table",
              int(left[0]["c"]) == 0, f"{left[0]['c']} left")
    except Exception as e:
        check("F  live invariant", False, str(e)[:300])

# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\nG. THE DAILY SWEEP — _do_dlar_sweep runs this every night; the second run must be a no-op")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
G_ROWS = ('[{"flag_key":"gk_1","key_basis":"mdn","period":"__P__","period_month":1,'
          '"period_year":2999,"flag_type":"PORT_OUT_30DAY","source":"mi_report",'
          '"severity":"CRITICAL","mdn":"9175550001","subscriber_id":"SUB-A","amount":45.00,'
          '"description":"d"}]')

G_SQL = """
BEGIN;
create temporary table _h(t text, v text, a text, b text, c text) on commit drop;

insert into _h(t, v) select 'G0', (commcalc.flags_sync_batch('__HOUSE__'::uuid,
   '22222222-2222-2222-2222-222222222222'::uuid, '__ROWS__'::jsonb))::text;
insert into _h(t, v) select 'G0r', (commcalc.flags_resolve_stale('__HOUSE__'::uuid,
   '22222222-2222-2222-2222-222222222222'::uuid, array['__P__'], array['mi_report'], 'x'))::text;

update commcalc.flags set reviewed_by='dm@cellfonzrus.com', reviewed_at=now(),
       action_taken='Reviewed on day 1 - no action'
 where org_id='__HOUSE__'::uuid and flag_key='gk_1';

-- night 2: the sweep fires again with the SAME computed set under a NEW run id
insert into _h(t, v) select 'G1', (commcalc.flags_sync_batch('__HOUSE__'::uuid,
   '33333333-3333-3333-3333-333333333333'::uuid, '__ROWS__'::jsonb))::text;
insert into _h(t, v) select 'G1r', (commcalc.flags_resolve_stale('__HOUSE__'::uuid,
   '33333333-3333-3333-3333-333333333333'::uuid, array['__P__'], array['mi_report'], 'x'))::text;

-- night 3
insert into _h(t, v) select 'G2', (commcalc.flags_sync_batch('__HOUSE__'::uuid,
   '44444444-4444-4444-4444-444444444444'::uuid, '__ROWS__'::jsonb))::text;
insert into _h(t, v) select 'G2r', (commcalc.flags_resolve_stale('__HOUSE__'::uuid,
   '44444444-4444-4444-4444-444444444444'::uuid, array['__P__'], array['mi_report'], 'x'))::text;

insert into _h(t, v, a, b, c)
select 'G9', count(*)::text, min(reviewed_by), min(action_taken), min(status)
  from commcalc.flags where org_id='__HOUSE__'::uuid and period='__P__';

select * from _h order by t;
ROLLBACK;
""".replace("__HOUSE__", HOUSE).replace("__P__", SENTINEL_PERIOD).replace("__ROWS__",
                                                  G_ROWS.replace("__P__", SENTINEL_PERIOD))

if not LIVE:
    print("  SKIP  offline run")
else:
    try:
        by = {r["t"]: r for r in (sql(G_SQL) or []) if isinstance(r, dict) and r.get("t")}
        j = lambda t: json.loads(by.get(t, {}).get("v") or "{}")
        g0, g1, g2 = j("G0"), j("G1"), j("G2")
        r1, r2 = j("G1r"), j("G2r")
        check("G1 night 1 INSERTS the flag",
              g0.get("inserted") == 1 and g0.get("updated") == 0, json.dumps(g0))
        check("G2 🔑 night 2 (after the DM reviewed it) inserts NOTHING and retires NOTHING",
              g1.get("inserted") == 0 and g1.get("updated") == 1
              and r1.get("resolved") == 0 and r1.get("superseded") == 0,
              f"sync={g1} resolve={r1}")
        check("G3 night 3 likewise — the queue does not grow and nothing is re-created",
              g2.get("inserted") == 0 and r2.get("resolved") == 0, f"sync={g2} resolve={r2}")
        g9 = by.get("G9", {})
        check("G4 🔑 after THREE nightly sweeps there is still exactly ONE row and the review is "
              "still on it — today it would have been erased on night 2",
              g9.get("v") == "1" and g9.get("a") == "dm@cellfonzrus.com"
              and g9.get("b") == "Reviewed on day 1 - no action" and g9.get("c") == "open",
              json.dumps(g9))
        left = sql(f"select count(*) c from commcalc.flags where period = {lit(SENTINEL_PERIOD)}")
        check("G5 rolled back — nothing left behind", int(left[0]["c"]) == 0)
    except Exception as e:
        check("G  daily sweep idempotence", False, str(e)[:300])

# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\nH. a REAL recalculation of the flag half — real raw_mi rows through the real producer + RPC")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
if not LIVE:
    print("  SKIP  offline run")
else:
    try:
        mi = sql("""
          select subscriber_id, subscriber_status, phone_number, device_serial, customer_plan,
                 base_mrc, mi_activation_date, mi_deactivation_date, residual_transfer_out_date,
                 salesforce_id
            from commcalc.raw_mi
           where org_id = '%s'::uuid and period = 'June 2026'
             and (upper(btrim(coalesce(subscriber_status,''))) in
                    ('PORTED-OUT','INVOLUNTARY-SUSPENDED')
              or (upper(btrim(coalesce(subscriber_status,''))) = 'ACTIVE'
                  and residual_transfer_out_date is not null))
           order by md5(coalesce(subscriber_id,'')) limit 400""" % HOUSE)
        mapping = sql("select store_code, store_address, salesforce_id from commcalc.store_mapping "
                      f"where org_id = '{HOUSE}'::uuid")
        # the REAL producer, on REAL data, relabelled to a sentinel period (and rolled back anyway)
        real = calc_portout_flags(mi, [], mapping, SENTINEL_PERIOD, 1, 2999)
        histo = FP.assign_keys(real)
        check("H1 the real MI rows produce flags", len(real) > 100, f"{len(real)} flags")
        check("H2 🔑 EVERY one of them has a stable, identifier-backed key — none fall into 'none'",
              histo.get("none", 0) == 0 and sum(histo.values()) == len(real), json.dumps(histo))
        check("H3 the keys are unique across the real set (no silent collapse)",
              len({f["flag_key"] for f in real}) == len(real),
              f"{len({f['flag_key'] for f in real})}/{len(real)}")

        payload = json.dumps([FP._clean(r) for r in real]).replace("'", "''")
        target = real[0]["flag_key"]
        bumped = [FP._clean(r) for r in real[1:]]           # row 0 dropped => must be RETIRED
        for r in bumped:
            r["amount"] = (r.get("amount") or 0) + 1.11
        payload2 = json.dumps(bumped).replace("'", "''")
        H_SQL = f"""
BEGIN;
create temporary table _h(t text, v text, a text, b text) on commit drop;
insert into _h(t, v) select 'H0', (commcalc.flags_sync_batch('{HOUSE}'::uuid,
   '55555555-5555-5555-5555-555555555555'::uuid, '{payload}'::jsonb))::text;
update commcalc.flags set reviewed_by='dm@cellfonzrus.com', reviewed_at=now(),
       action_taken='Real-data review'
 where org_id='{HOUSE}'::uuid and flag_key='{target}';
insert into _h(t, v) select 'H1', (commcalc.flags_sync_batch('{HOUSE}'::uuid,
   '66666666-6666-6666-6666-666666666666'::uuid, '{payload2}'::jsonb))::text;
insert into _h(t, v) select 'H2', (commcalc.flags_resolve_stale('{HOUSE}'::uuid,
   '66666666-6666-6666-6666-666666666666'::uuid, array['{SENTINEL_PERIOD}'],
   array['mi_report'], 'condition cleared'))::text;
insert into _h(t, v, a, b) select 'H3', status, reviewed_by, action_taken
  from commcalc.flags where org_id='{HOUSE}'::uuid and flag_key='{target}';
insert into _h(t, v, a) select 'H4', count(*)::text,
       count(*) filter (where status='open')::text
  from commcalc.flags where org_id='{HOUSE}'::uuid and period='{SENTINEL_PERIOD}';
select * from _h order by t;
ROLLBACK;
"""
        by = {r["t"]: r for r in (sql(H_SQL) or []) if isinstance(r, dict) and r.get("t")}
        h0 = json.loads(by.get("H0", {}).get("v") or "{}")
        h1 = json.loads(by.get("H1", {}).get("v") or "{}")
        h2 = json.loads(by.get("H2", {}).get("v") or "{}")
        h3 = by.get("H3", {})
        h4 = by.get("H4", {})
        check("H4 run 1 inserts the whole real set",
              h0.get("inserted") == len(real) and h0.get("updated") == 0, json.dumps(h0))
        check("H5 run 2 inserts NOTHING and refreshes the rest",
              h1.get("inserted") == 0 and h1.get("updated") == len(real) - 1, json.dumps(h1))
        check("H6 🔑 the reviewed flag whose condition cleared is RETIRED with its review intact — "
              "not deleted, not re-created, not blank",
              h3.get("v") == "resolved" and h3.get("a") == "dm@cellfonzrus.com"
              and h3.get("b") == "Real-data review" and h2.get("resolved") == 1,
              json.dumps(h3) + " " + json.dumps(h2))
        check("H7 the row count did not shrink — retire is a status change, never a DELETE",
              h4.get("v") == str(len(real)) and h4.get("a") == str(len(real) - 1),
              json.dumps(h4))
        left = sql(f"select count(*) c from commcalc.flags where period = {lit(SENTINEL_PERIOD)}")
        check("H8 rolled back — nothing left in the live table", int(left[0]["c"]) == 0)
    except Exception as e:
        check("H  real-data recalculation", False, str(e)[:300])

# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\nI. the DM review ENDPOINT — span-gated, token-attributed, and money-free")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
# Exercised against a fake chain client (the convention harness_chargeback_flags_span_store.py uses),
# so the handler's real logic runs without writing a review into production.
import asyncio                                                          # noqa: E402
from types import SimpleNamespace                                       # noqa: E402


class _Q:
    def __init__(self, store, table):
        self.s, self.t, self.f, self.upd = store, table, [], None

    def select(self, *a, **k): return self
    def order(self, *a, **k): return self
    def limit(self, *a, **k): return self
    def eq(self, c, v): self.f.append((c, v)); return self
    def in_(self, c, v): self.f.append((c, list(v))); return self

    def update(self, body): self.upd = body; return self

    def _match(self, r):
        return all((r.get(c) in v) if isinstance(v, list) else (r.get(c) == v) for c, v in self.f)

    def execute(self):
        rows = [r for r in self.s.setdefault(self.t, []) if self._match(r)]
        if self.upd is not None:
            for r in rows:
                r.update(self.upd)
            self.s.setdefault("_writes", []).append(dict(self.upd))
        return SimpleNamespace(data=[dict(r) for r in rows])


class _Client:
    def __init__(self, store): self.store = store
    def schema(self, _n): return self
    def table(self, n): return _Q(self.store, n)


import app.modules.commcalc.router as CC                                # noqa: E402
import app.modules.storeops.router as SO                                # noqa: E402

_FLAG = {"id": "f-1", "org_id": HOUSE, "period": "June 2026", "flag_type": "PORT_OUT_30DAY",
         "store_code": "B-117", "store_address": "117 E Burnside Ave", "status": "open",
         "reviewed_by": None, "reviewed_at": None, "action_taken": None}
_OUT = dict(_FLAG, id="f-2", store_code="B-999", store_address="723 N Market St")
STORE = {"flags": [dict(_FLAG), dict(_OUT)]}
CC.sb = lambda: _Client(STORE)
CC._flag_reviewer_name = lambda authorization, fallback="": "dm@cellfonzrus.com"
SO.scope_keyset = lambda authorization="", org_id=HOUSE: ({"B-117"} if authorization == "Bearer dm" else None)


def _run(c):
    return asyncio.new_event_loop().run_until_complete(c)


try:
    out = _run(CC.review_flag("f-1", {"action_taken": "Coached the rep"},
                              authorization="Bearer dm", org_id=HOUSE))
    row = [r for r in STORE["flags"] if r["id"] == "f-1"][0]
    check("I1 a DM can review a flag INSIDE their span, and it lands on the row",
          out.get("ok") and row["reviewed_by"] == "dm@cellfonzrus.com"
          and row["action_taken"] == "Coached the rep" and row["reviewed_at"], json.dumps(row))
except Exception as e:
    check("I1 a DM can review a flag inside their span", False, str(e)[:160])

try:
    _run(CC.review_flag("f-2", {"action_taken": "nope"}, authorization="Bearer dm", org_id=HOUSE))
    check("I2 a DM canNOT review a flag OUTSIDE their span", False, "no 403 raised")
except Exception as e:
    check("I2 a DM canNOT review a flag OUTSIDE their span", "403" in str(e) or "span" in str(e).lower(),
          str(e)[:120])

try:
    _run(CC.review_flag("nope", {}, authorization="", org_id=HOUSE))
    check("I3 an unknown flag id is a clean 404, not a 500", False, "no error raised")
except Exception as e:
    check("I3 an unknown flag id is a clean 404, not a 500", "404" in str(e), str(e)[:120])

try:
    _run(CC.review_flag("f-1", {"clear": True}, authorization="", org_id=HOUSE))
    row = [r for r in STORE["flags"] if r["id"] == "f-1"][0]
    check("I4 a review can be cleared (a DM may change their mind)",
          row["reviewed_by"] is None and row["action_taken"] is None, json.dumps(row))
except Exception as e:
    check("I4 a review can be cleared", False, str(e)[:160])

_writes = STORE.get("_writes") or []
_MONEY = {"amount", "rebate_lost", "severity", "total_payout", "deduct", "rate", "tier",
          "paid", "earned", "store_code", "store_address", "flag_type", "status"}
check("I5 💰 the review endpoint writes ONLY the three review columns — no amount, no rate, no "
      "tier, no plan, no status, not even the store",
      all(set(w.keys()) <= {"reviewed_by", "reviewed_at", "action_taken"} for w in _writes)
      and not any(set(w.keys()) & _MONEY for w in _writes),
      "wrote " + json.dumps(sorted({k for w in _writes for k in w})))
check("I6 the reviewer is taken from the token, and a body-supplied reviewer cannot override it",
      "reviewed_by': _flag_reviewer_name(authorization" in RT
      or "'reviewed_by': _flag_reviewer_name(authorization" in RT)

print("\n" + "=" * 96)
print(f"RESULT  {len(PASS)} passed / {len(FAIL)} failed")
for f in FAIL:
    print("   FAILED: " + f)
sys.exit(1 if FAIL else 0)
