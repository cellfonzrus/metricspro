"""Harness for app/core/identity.py — the SSOT resolver (design blueprint Part 3e, proof #1).

Proves, with NO database (a fake Supabase client + the PURE builders), that the ONE resolver returns
the right stable entity for every store/employee drift case catalogued in blueprint Part 1C:

  * B-1115 orphan   — a storeops.stores row with NO store_mapping row still resolves by code AND by
                      address, and carries its market (LI). (The 1115-Liberty class.)
  * Rd / Road       — normalized-address fold resolves "1800 Great Neck Road" to the "…Rd" entity.
  * 26th / 26TH     — case drift resolves.
  * B- / T- twin    — a carrier_code ALIAS row resolves the twin spelling to the canonical entity
                      (twins are only auto-resolved when an EXPLICIT alias exists — bare twins are
                      staged, never merged; see harness_backfill_idempotent).
  * LUX twin        — a sales_file_spelling alias (LUX-NY-PENN) resolves to the short-code entity.
  * POS / roster    — an employee resolves from BOTH the POS name and the roster name to one person.

Run:  python harness_identity_resolver.py
"""

import sys
import types

from app.core import identity as I

_passed = 0
_failed = 0


def check(name, cond, got=None, want=None):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  PASS  {name}")
    else:
        _failed += 1
        extra = "" if got is None and want is None else f"   (got={got!r} want={want!r})"
        print(f"  FAIL  {name}{extra}")


# ── fake Supabase client (chained builder; (schema,table)-keyed fixtures) ──────────────────────────
class _FakeQ:
    def __init__(self, rows):
        self._rows = rows

    def select(self, *a, **k): return self
    def eq(self, *a, **k): return self
    def limit(self, *a, **k): return self

    def execute(self):
        return types.SimpleNamespace(data=list(self._rows), count=len(self._rows))


class _FakeClient:
    def __init__(self, tables):
        self._tables = tables
        self._schema = None

    def schema(self, s):
        self._schema = s
        return self

    def table(self, name):
        return _FakeQ(self._tables.get((self._schema, name), []))


ORG = "org-ssot"

# entity ids
E_1115 = "e-1115"          # B-1115 / 1115 Liberty / LI — the orphan (no store_mapping row)
E_GN = "e-greatneck"       # 1800 Great Neck Rd — the Rd/Road case
E_26 = "e-26th"            # 3735 26th Street — the 26th/26TH case
E_PENN = "e-penn"          # 957 Pennsylvania Ave — the LUX twin (canonical short code 957)

STORE_ROWS = [
    {"entity_id": E_1115, "store_code": "B-1115", "address": "1115 Liberty", "market": "LI",
     "timezone": "America/New_York", "is_active": True},
    {"entity_id": E_GN, "store_code": "B-1800", "address": "1800 Great Neck Rd", "market": "LI",
     "timezone": None, "is_active": True},
    {"entity_id": E_26, "store_code": "B-26", "address": "3735 26th Street", "market": "Chicago",
     "timezone": None, "is_active": True},
    {"entity_id": E_PENN, "store_code": "957", "address": "957 Pennsylvania Ave", "market": "NYC",
     "timezone": None, "is_active": True},
]
# store_mapping deliberately has NO row for B-1115 (the orphan), rows for the others.
MAPPING_ROWS = [
    {"store_code": "B-1800", "store_address": "1800 Great Neck Rd", "salesforce_id": "SF-1800",
     "market": "LI", "is_active": True},
    {"store_code": "957", "store_address": "957 Pennsylvania Ave", "salesforce_id": "SF-957",
     "market": "NYC", "is_active": True},
]
# explicit alias rows: the B-/T- carrier twin and the LUX sales-file spelling
ALIAS_ROWS = [
    {"alias_kind": "carrier_code", "alias_value": "T-1115", "entity_id": E_1115},
    {"alias_kind": "sales_file_spelling", "alias_value": "LUX-NY-PENN", "entity_id": E_PENN},
    {"alias_kind": "salesforce_id", "alias_value": "SF-1115", "entity_id": E_1115},
]

EMP_ROWS = [
    {"entity_id": "p-robert", "employee_id": "E45", "id": 45, "name": "Robert Smith",
     "home_store": "B-1115", "pay_rate": 20, "is_active": True, "epay_login": "rsmith",
     "epay_salesperson": ""},
    {"entity_id": "p-abdul", "employee_id": "E70", "id": 70, "name": "Abdul Kakar",
     "home_store": "B-26", "pay_rate": 18, "is_active": True, "epay_login": "akakar",
     "epay_salesperson": ""},
]
NAME_MAP_ROWS = [{"epay_login": "bsmith", "epay_salesperson": "Bob Smith", "storeops_name": "Robert Smith"}]
REP_ALIAS_ROWS = [{"alias": "Abdul K", "canonical": "Abdul Kakar"}]


def _sclient():
    return _FakeClient({
        ("storeops", "stores"): STORE_ROWS,
        ("commcalc", "store_mapping"): MAPPING_ROWS,
        ("storeops", "store_alias"): ALIAS_ROWS,
        ("storeops", "employees"): EMP_ROWS,
        ("commcalc", "name_map"): NAME_MAP_ROWS,
        ("commcalc", "rep_aliases"): REP_ALIAS_ROWS,
        ("storeops", "employee_alias"): [],
    })


def main():
    I.invalidate()
    c = _sclient()

    print("(1) B-1115 orphan — resolves by code AND address with NO store_mapping row, carries LI:")
    by_code = I.resolve_store(c, ORG, "B-1115")
    check("resolve by code B-1115 → entity e-1115", by_code and by_code.entity_id == E_1115,
          by_code and by_code.entity_id)
    check("orphan carries market LI (propagates despite no mapping row)",
          by_code and by_code.market == "LI", by_code and by_code.market)
    by_addr = I.resolve_store(c, ORG, "1115 Liberty")
    check("resolve by address '1115 Liberty' → entity e-1115", by_addr and by_addr.entity_id == E_1115,
          by_addr and by_addr.entity_id)

    print("\n(2) Rd/Road + 26th/26TH — normalized-address fold & case drift:")
    rd = I.resolve_store(c, ORG, "1800 Great Neck Road")
    check("'1800 Great Neck Road' → the '…Rd' entity via normalized fold", rd and rd.entity_id == E_GN,
          rd and rd.entity_id)
    c26 = I.resolve_store(c, ORG, "3735 26TH STREET")
    check("'3735 26TH STREET' → the '26th Street' entity (case drift)", c26 and c26.entity_id == E_26,
          c26 and c26.entity_id)

    print("\n(3) B-/T- carrier twin + LUX twin — resolve via EXPLICIT alias only:")
    tw = I.resolve_store(c, ORG, "T-1115")
    check("carrier twin 'T-1115' → canonical entity e-1115 (alias)", tw and tw.entity_id == E_1115,
          tw and tw.entity_id)
    lux = I.resolve_store(c, ORG, "LUX-NY-PENN")
    check("LUX twin 'LUX-NY-PENN' → short-code entity 957", lux and lux.entity_id == E_PENN,
          lux and lux.entity_id)
    sf = I.resolve_store(c, ORG, "SF-1115")
    check("salesforce_id 'SF-1115' → entity e-1115", sf and sf.entity_id == E_1115, sf and sf.entity_id)

    print("\n(4) unknown store resolves to None (never a coin-flip):")
    check("unknown '9 Nowhere Blvd' → None", I.resolve_store(c, ORG, "9 Nowhere Blvd") is None)

    print("\n(5) POS/roster employee names → one person:")
    pos = I.resolve_employee(c, ORG, "Bob Smith")
    check("POS 'Bob Smith' → entity p-robert (name_map bridge)", pos and pos.entity_id == "p-robert",
          pos and pos.entity_id)
    roster = I.resolve_employee(c, ORG, "Robert Smith")
    check("roster 'Robert Smith' → same entity p-robert", roster and roster.entity_id == "p-robert",
          roster and roster.entity_id)
    check("POS and roster resolve to the SAME person", pos and roster and pos.entity_id == roster.entity_id)
    biz = I.resolve_employee(c, ORG, "E45")
    check("business id 'E45' → p-robert", biz and biz.entity_id == "p-robert", biz and biz.entity_id)
    num = I.resolve_employee(c, ORG, "45")
    check("numeric id '45' → p-robert", num and num.entity_id == "p-robert", num and num.entity_id)
    abdul = I.resolve_employee(c, ORG, "Abdul K")
    check("rep_alias 'Abdul K' → p-abdul (canonical Abdul Kakar)", abdul and abdul.entity_id == "p-abdul",
          abdul and abdul.entity_id)

    print("\n(6) pure builder determinism (no I/O):")
    idx = I._build_store_index(STORE_ROWS, MAPPING_ROWS, ALIAS_ROWS)
    check("every store_code maps to exactly one entity", len(idx.entities) == 4, len(idx.entities))
    r1 = I._resolve_store_in_index(idx, "B-1115")
    r2 = I._resolve_store_in_index(idx, "B-1115")
    check("resolution is deterministic (same call → same entity)",
          r1 and r2 and r1.entity_id == r2.entity_id)

    print(f"\n==== {_passed} passed, {_failed} failed ====")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
