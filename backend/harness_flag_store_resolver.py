"""HARNESS — flags DM routing: the resolved `commcalc.flags.store_code` (migration 285/286).

Owner directive 2026-08-07: "all flags need to be fed thru the dm, so yes route it thru the dm and
then visible to the scoped user." Owner ruling 2026-08-08: "flags - go with option a" — resolve the
store ON WRITE into a real column, not at read time.

WHAT THIS PROVES
────────────────
  A. build_index / resolve_code follow the SPAN KEYSET's vocabulary and priority exactly — including
     that they REFUSE a spelling the org has not recorded rather than guessing at it.
  B. A resolved `store_code` is matched by `in_keyset` for the manager whose span holds that store,
     and is NOT matched for a manager whose span does not — i.e. the value actually routes.
  C. The read filter is a strict SUPERSET of the old one: every row visible before is visible after.
  D. The MI-door fallback (salesforce_id → store_mapping) is unambiguous, and refuses ambiguity.
  E. The Python resolver and the SQL resolver `commcalc.flag_store_code_for` agree, key for key.
  F. Nothing that cannot be resolved is dropped — it lands in the unrouted queue.

Sections A–D and F are OFFLINE (pure, no database). Section E runs only when `tools/sbsql.py` and a
Supabase PAT are available, and it is READ-ONLY.

    python3 backend/harness_flag_store_resolver.py
"""

import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))

from app.modules.commcalc import flag_store_resolver as R   # noqa: E402
from app.core.scope import build_market_index, widen_codes_to_keys, in_keyset  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"   [{detail}]" if detail else ""))


# ── Fixture: a two-market org shaped like the real house data ────────────────────────────────────
# B-5619 : storeops has NO address (true of 25 of the house's 26 stores) — store_mapping carries it.
# B-3PL  : the POS writes "3 Palisade Ave Yonkers"; only store_aliases knows that.
# B-1115 : exists ONLY in storeops.stores (not in store_mapping) — the reverse gap.
# B-DEAD : an alias points at a store_code that does not exist → must be IGNORED.
MAPPING = [
    {"store_code": "B-5619", "store_address": "5619 N. Broad St.", "market": "PA",
     "salesforce_id": "0018000001LWwIiAAL"},
    {"store_code": "B-3PL", "store_address": "3 Palisade Ave", "market": "NYC",
     "salesforce_id": "0018000000caWUpAAM"},
    {"store_code": "B-1598", "store_address": "1598 Mount Ephraim Ave", "market": "PA",
     "salesforce_id": "0018000001flQk3AAE"},
    {"store_code": None, "store_address": "Cellular Services Dot net LLC", "market": "LI",
     "salesforce_id": None},
]
STORES = [
    {"store_code": "B-5619", "address": None, "market": "PA"},
    {"store_code": "B-3PL", "address": None, "market": "NYC"},
    {"store_code": "B-1598", "address": None, "market": "PA"},
    {"store_code": "B-1115", "address": "1115 Liberty Ave", "market": "LI"},
]
ALIASES = [
    {"alias": "3 Palisade Ave Yonkers", "store_code": "B-3PL"},
    {"alias": "2778 Ephraim Ave", "store_code": "B-1598"},
    {"alias": "somewhere else entirely", "store_code": "B-DEAD"},   # target is not a real store
]

IDX = R.build_index(MAPPING, STORES, ALIASES)


print("\nA. resolver follows the span keyset's vocabulary (and refuses to guess)")
check("A1 canonical store_mapping address resolves",
      R.resolve_code(IDX, "5619 N. Broad St.") == "B-5619")
check("A2 case / whitespace insensitive",
      R.resolve_code(IDX, "  5619 n. broad st.  ") == "B-5619")
check("A3 a store_code passed as the key resolves to itself",
      R.resolve_code(IDX, "b-3pl") == "B-3PL")
check("A4 an explicit store_aliases synonym resolves",
      R.resolve_code(IDX, "3 Palisade Ave Yonkers") == "B-3PL")
check("A5 a storeops-only address resolves (store absent from store_mapping)",
      R.resolve_code(IDX, "1115 Liberty Ave") == "B-1115")
check("A6 an UNRECORDED spelling resolves to NOTHING — no leading-number guess",
      R.resolve_code(IDX, "5619 N Broad St") is None,
      "store-unmatched's leading-number matcher would say B-5619; the KEYSET would not")
check("A7 an address with a city/state suffix is NOT guessed at",
      R.resolve_code(IDX, "5619 N. Broad St. Philadelphia, PA 19141") is None)
check("A8 an alias pointing at a NON-EXISTENT store_code is ignored",
      R.resolve_code(IDX, "somewhere else entirely") is None)
check("A9 blank / None / whitespace resolve to None",
      R.resolve_code(IDX, "") is None and R.resolve_code(IDX, None) is None
      and R.resolve_code(IDX, "   ") is None)
check("A10 a store_mapping row with no store_code contributes no key",
      R.resolve_code(IDX, "Cellular Services Dot net LLC") is None)
check("A11 first non-None of several candidate keys wins",
      R.resolve_code(IDX, None, "", "3 Palisade Ave") == "B-3PL")

# Priority: a string that is BOTH one store's code and another store's address must resolve to the
# CODE (priority 1 beats priority 3) — otherwise a code-shaped store name could hijack a real store.
COLLIDE = R.build_index(
    [{"store_code": "X1", "store_address": "X2", "salesforce_id": None},
     {"store_code": "X2", "store_address": "9 Main St", "salesforce_id": None}], [], [])
check("A12 priority: key-is-a-code beats key-is-an-address",
      R.resolve_code(COLLIDE, "X2") == "X2")
# An alias (priority 2) beats a store_mapping address (priority 3): the explicit human mapping wins.
ALIAS_WINS = R.build_index(
    [{"store_code": "A1", "store_address": "9 Main St", "salesforce_id": None},
     {"store_code": "A2", "store_address": "other", "salesforce_id": None}], [],
    [{"alias": "9 Main St", "store_code": "A2"}])
check("A13 priority: an explicit alias beats a store_mapping address",
      R.resolve_code(ALIAS_WINS, "9 Main St") == "A2")
# Determinism: two stores genuinely sharing one address spelling → alphabetically-first code, always.
DUP = R.build_index([{"store_code": "Z9", "store_address": "5 Same St", "salesforce_id": None},
                     {"store_code": "Z1", "store_address": "5 Same St", "salesforce_id": None}], [], [])
check("A14 a duplicated address resolves deterministically (min store_code)",
      R.resolve_code(DUP, "5 Same St") == "Z1")


print("\nB. a resolved store_code actually ROUTES — in_keyset matches it")
# The real span keyset, built the way core.scope builds it for a scope-'market' manager.
MKT_IDX = build_market_index(STORES, [{"store_code": m["store_code"],
                                       "store_address": m["store_address"],
                                       "market": m["market"]} for m in MAPPING], ALIASES)


class _FakeClient:
    """widen_codes_to_keys only needs market_index(); feed it the fixture instead of a database."""


def _keyset(codes):
    keys = {c.upper() for c in codes}
    span = frozenset(keys)
    for s in MKT_IDX["stores"]:
        sc = str(s.get("store_code") or "").upper()
        if sc in span:
            for a in MKT_IDX["addr_keys"].get(sc, set()):
                keys.add(a)
            for a in MKT_IDX["alias_keys"].get(sc, set()):
                keys.add(a)
    return keys


PA_KS = _keyset({"B-5619", "B-1598"})     # the PA district manager
NYC_KS = _keyset({"B-3PL"})               # the NYC district manager

check("B1 PA DM matches a flag resolved to one of her stores",
      in_keyset(PA_KS, "B-5619", None))
check("B2 NYC DM does NOT match that flag",
      not in_keyset(NYC_KS, "B-5619", None))
check("B3 a flag with a BLANK store_address but a resolved code now reaches its DM",
      in_keyset(PA_KS, "B-1598", "") and not in_keyset(NYC_KS, "B-1598", ""))
check("B4 an UNROUTED flag (store_code None, blank address) reaches no DM",
      not in_keyset(PA_KS, None, "") and not in_keyset(NYC_KS, None, ""))
check("B5 an unrouted flag is still visible to an unrestricted caller (keyset None)",
      in_keyset(None, None, ""))
check("B6 the resolved code cannot widen a DM into another store",
      not in_keyset(NYC_KS, "B-1598", None) and not in_keyset(PA_KS, "B-3PL", None))


print("\nC. the new read filter is a strict SUPERSET of the old one")
SAMPLE = [
    {"store_address": "5619 N. Broad St.", "store_code": "B-5619"},   # matched before AND after
    {"store_address": "3 Palisade Ave Yonkers", "store_code": "B-3PL"},
    {"store_address": "", "store_code": "B-1598"},                    # NEW: was invisible
    {"store_address": "5619 N Broad St", "store_code": None},         # unrecorded spelling
    {"store_address": "", "store_code": None},                        # unroutable
]
for ks_name, ks in (("PA", PA_KS), ("NYC", NYC_KS), ("admin", None)):
    old = [i for i, f in enumerate(SAMPLE) if in_keyset(ks, f["store_address"])]
    new = [i for i, f in enumerate(SAMPLE) if in_keyset(ks, f["store_code"], f["store_address"])]
    check(f"C-{ks_name} every row visible before is still visible after",
          set(old) <= set(new), f"old={old} new={new}")
check("C-gain the PA DM gains exactly the blank-address row her span owns",
      set(i for i, f in enumerate(SAMPLE) if in_keyset(PA_KS, f["store_code"], f["store_address"]))
      - set(i for i, f in enumerate(SAMPLE) if in_keyset(PA_KS, f["store_address"])) == {2})


print("\nD. the MI-door fallback (salesforce_id -> store_code)")
MI = [
    {"phone_number": "2125551000", "salesforce_id": "0018000001LWwIiAAL"},   # -> B-5619
    {"phone_number": "2125551001.0", "salesforce_id": "0018000000caWUpAAM"},  # -> B-3PL, '.0' artefact
    {"phone_number": "2125551002", "salesforce_id": "0018000001LWwIiAAL"},   # ambiguous ↓
    {"phone_number": "2125551002", "salesforce_id": "0018000000caWUpAAM"},   # same MDN, other door
    {"phone_number": "", "salesforce_id": "0018000001LWwIiAAL"},             # no MDN at all
    {"phone_number": "2125551003", "salesforce_id": "UNKNOWN-DOOR"},         # door not mapped
]
M2C = R.mdn_store_code_map(IDX, MI)
check("D1 an unambiguous MDN resolves to its door's store", M2C.get("2125551000") == "B-5619")
check("D2 the pandas '.0' artefact is normalised away", M2C.get("2125551001") == "B-3PL")
check("D3 an MDN seen at TWO doors is REFUSED, not coin-flipped", "2125551002" not in M2C)
check("D4 an unmapped salesforce_id yields nothing", "2125551003" not in M2C)
check("D5 a blank MDN is never a key", "" not in M2C)
check("D6 a tenant with no salesforce_id in store_mapping gets an empty map (no-op)",
      R.mdn_store_code_map(R.build_index([{"store_code": "Q1", "store_address": "q"}], [], []), MI) == {})

FLAGS = [
    {"source": "mi_report", "store_address": "5619 N. Broad St.", "mdn": "2125551001"},
    {"source": "mi_report", "store_address": "", "mdn": "2125551000"},
    {"source": "mi_report", "store_address": "", "mdn": "2125551002"},   # ambiguous -> unrouted
    {"source": "mi_report", "store_address": "", "mdn": ""},             # no identifier -> unrouted
    {"source": "sales", "store_address": "5619 N Broad St", "mdn": ""},  # unrecorded -> unrouted
    {"source": "sales", "store_address": "x", "mdn": "", "store_code": "PRESET"},
]
CNT = R.stamp(IDX, FLAGS, M2C)
check("D7 the SALES answer stays authoritative over the MI door",
      FLAGS[0]["store_code"] == "B-5619", "store string wins even though its MDN says B-3PL")
check("D8 a blank-store MI flag is rescued by its door", FLAGS[1]["store_code"] == "B-5619")
check("D9 ambiguous / identifier-less rows stay unrouted",
      FLAGS[2]["store_code"] is None and FLAGS[3]["store_code"] is None)
check("D10 an unrecorded spelling stays unrouted", FLAGS[4]["store_code"] is None)
check("D11 a value the caller already set is never overwritten", FLAGS[5]["store_code"] == "PRESET")
check("D12 counts are honest", CNT == {"total": 6, "by_store_string": 1, "by_mdn": 1, "unresolved": 3},
      json.dumps(CNT))
check("D13 EVERY row carries the key (uniform bulk-insert payload, nothing silently dropped)",
      all("store_code" in f for f in FLAGS))


print("\nG. the WRITE path: calc_portout_flags routes an MI flag with no sales match")
from app.modules.commcalc.portout_flags import calc_portout_flags  # noqa: E402

PO_MI = [
    # ported out, NO sales match, NO phone, NO imei — the 17,662-row class the backfill cannot reach
    {"subscriber_status": "PORTED-OUT", "phone_number": "", "device_serial": "",
     "salesforce_id": "0018000001LWwIiAAL", "base_mrc": 0},
    # ported out WITH a sales match — the sales store must stay authoritative
    {"subscriber_status": "PORTED-OUT", "phone_number": "2125550001", "device_serial": "",
     "salesforce_id": "0018000000caWUpAAM", "base_mrc": 25},
    # active + transferred out, no sales match
    {"subscriber_status": "ACTIVE", "phone_number": "", "device_serial": "",
     "residual_transfer_out_date": "2026-06-01", "salesforce_id": "0018000001flQk3AAE", "base_mrc": 10},
    # involuntary suspended, no sales match, door NOT in store_mapping
    {"subscriber_status": "INVOLUNTARY-SUSPENDED", "phone_number": "", "device_serial": "",
     "salesforce_id": "NOT-A-MAPPED-DOOR", "base_mrc": 5},
]
PO_SALES = [{"mdn": "2125550001", "salesperson": "Rana", "store": "3 Palisade Ave Yonkers",
             "serial_1": "", "product_desc": ""}]
PO = calc_portout_flags(PO_MI, PO_SALES, MAPPING, "June 2026", 6, 2026)
check("G1 four MI rows produce four flags", len(PO) == 4, str([f["flag_type"] for f in PO]))
check("G2 an identifier-less port-out is routed by its door", PO[0]["store_code"] == "B-5619")
check("G3 the sales match still owns store_address AND rep",
      PO[1]["store_address"] == "3 Palisade Ave Yonkers" and PO[1]["epay_salesperson"] == "Rana")
check("G4 the MI door does NOT override a sales match", PO[1]["store_code"] is None,
      "left for the store-string chain, which resolves the alias to B-3PL")
check("G5 a transfer-out with no sales match is routed", PO[2]["store_code"] == "B-1598")
check("G6 an unmapped door stays unrouted, never guessed", PO[3]["store_code"] is None)
_after = R.stamp(IDX, PO, R.mdn_store_code_map(IDX, PO_MI))
check("G7 stamp() then resolves the sales-matched row through the alias chain",
      PO[1]["store_code"] == "B-3PL")
check("G8 end to end: 3 of 4 routed, the unmapped door left in the unrouted queue",
      sum(1 for f in PO if f["store_code"]) == 3 and PO[3]["store_code"] is None)
check("G9 no flag lost a field it had before",
      all("store_address" in f and "epay_salesperson" in f and "mdn" in f for f in PO))


print("\nF. degradation — routing must never break a calculation")
check("F1 stamp() on an empty list is a no-op",
      R.stamp(IDX, []) == {"total": 0, "by_store_string": 0, "by_mdn": 0, "unresolved": 0})
check("F2 an empty index resolves nothing and raises nothing",
      R.resolve_code(R.build_index(), "anything") is None and R.resolve_code(None, "x") is None)
_rows = [{"store_address": "5619 N. Broad St."}]
check("F3 a resolver blow-up leaves the rows unrouted, not un-inserted",
      R.stamp_flags(object(), "org", _rows)["unresolved"] == 1 and _rows[0]["store_code"] is None)


# ── E. Python resolver == SQL resolver, over the org's REAL vocabulary (read-only) ───────────────
print("\nE. Python resolver agrees with commcalc.flag_store_code_for (live, READ-ONLY)")
SBSQL = "/workspaces/commcalc/tools/sbsql.py"


def sql(q):
    r = subprocess.run([sys.executable, SBSQL, q], capture_output=True, text=True, timeout=180)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[-400:])
    return json.loads(r.stdout)


if not os.path.exists(SBSQL):
    print("  SKIP  tools/sbsql.py not present — offline run")
else:
    try:
        orgs = [r["org_id"] for r in sql("select distinct org_id from commcalc.flags order by 1")]
        for org in orgs:
            mapping = sql("select store_code,store_address,salesforce_id from commcalc.store_mapping "
                          f"where org_id='{org}'")
            stores = sql(f"select store_code,address from storeops.stores where org_id='{org}'")
            aliases = sql(f"select alias,store_code from commcalc.store_aliases where org_id='{org}'")
            idx = R.build_index(mapping, stores, aliases)
            keys = [r["k"] for r in sql(
                "select distinct btrim(store_address) k from commcalc.flags "
                f"where org_id='{org}' and coalesce(btrim(store_address),'')<>'' order by 1")]
            # every distinct flag spelling + every key in the vocabulary itself
            keys += sorted({str(v) for row in (mapping + stores) for v in
                            (row.get("store_code"), row.get("store_address"), row.get("address")) if v}
                           | {str(a["alias"]) for a in aliases})
            keys = sorted(set(keys))
            if not keys:
                continue
            vals = ",".join("('" + k.replace("'", "''") + "')" for k in keys)
            got = sql(f"select k, commcalc.flag_store_code_for('{org}'::uuid, k) code "
                      f"from (values {vals}) t(k)")
            sqlmap = {r["k"]: r["code"] for r in got}
            bad = [(k, R.resolve_code(idx, k), sqlmap.get(k)) for k in keys
                   if (R.resolve_code(idx, k) or None) != (sqlmap.get(k) or None)]
            check(f"E-{org[:8]} python == sql over {len(keys)} real keys", not bad,
                  "; ".join(f"{k!r} py={p!r} sql={s!r}" for k, p, s in bad[:5]))
    except Exception as e:
        print(f"  SKIP  live comparison unavailable: {e}")


print(f"\n{'='*78}\n{len(PASS)} PASSED, {len(FAIL)} FAILED")
for f in FAIL:
    print(f"  FAILED: {f}")
sys.exit(1 if FAIL else 0)
