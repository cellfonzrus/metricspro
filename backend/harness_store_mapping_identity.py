"""PROOF: one physical store, one canonical key — the B-1800 / B-1115 split, reproduced and repaired.

Owner, 2026-10-02: B-1800's and B-1115's figures were each appearing under two keys.

DB-FREE. Runs the REAL `account.coa.store_resolver` over an in-memory fake seeded with the LIVE
house row shapes (read from the tenant on 2026-10-02, reproduced here as a fixture), and the REAL
`account.store_identity_audit`. Nothing here re-implements resolution, so a change to the resolver
that re-opens the split FAILS THIS HARNESS rather than passing a spelling check.

  §A  reproduce — the live rows, as they stand, split BOTH stores (and B-60TH, the sibling)
  §B  the repair — the runbook's three rows collapse every spelling to one key
  §C  negative controls — undo any one repair and its finding comes back
  §D  the audit's own truth table (placeholder rule, roster-without-mapping, spelling gather)
  §E  the invariant holds for the stores the repair does NOT touch (no collateral merge)

WHAT IS NOT CLAIMED. Two house rows carry a placeholder address whose real location is NOT in the
database (`B-2778`, `Cellular Services`). The audit reports them; this harness pins that they are
REPORTED and deliberately NOT repaired — inventing an address for a store would be exactly the
"write code that hides it" the house rules forbid.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

FAILED = []
PASSED = [0]


def ok(label, cond, extra=""):
    if cond:
        PASSED[0] += 1
        print("  ok   %s" % label)
    else:
        FAILED.append(label)
        print("  FAIL %s %s" % (label, extra))


# ── in-memory fake (commcalc schema only; coa._fetch_all pages with .range()) ─────────────────────
class _Res:
    def __init__(self, data):
        self.data = data


class FakeClient:
    """Serves commcalc.store_mapping / store_aliases from dicts, the way _fetch_all reads them."""

    def __init__(self, tables):
        self.tables = tables
        self._t, self._eq = None, []

    def schema(self, _s):
        return self

    def table(self, t):
        self._t, self._eq = t, []
        return self

    def select(self, _cols):
        return self

    def eq(self, c, v):
        self._eq.append((c, v))
        return self

    def in_(self, c, v):
        self._eq.append((c, list(v)))
        return self

    def ilike(self, *_a):
        return self

    def limit(self, _n):
        return self

    def range(self, a, b):
        self._range = (a, b)
        return self

    def execute(self):
        rows = list(self.tables.get(self._t, []))
        for c, v in self._eq:
            if isinstance(v, list):
                rows = [r for r in rows if r.get(c) in v]
            else:
                rows = [r for r in rows if r.get(c) == v]
        a, b = getattr(self, "_range", (0, 999))
        return _Res(rows[a:b + 1])


import _harness_dbfree  # noqa: E402  (must precede the app imports)

_harness_dbfree.install(FakeClient({}))

from app.modules.account.coa import store_resolver            # noqa: E402  THE REAL RESOLVER
from app.modules.account import store_identity_audit as audit_mod  # noqa: E402
from app.core import setup_notice as _sn                        # noqa: E402  the §19.38 key list

ORG = "00000000-0000-0000-0000-000000000001"

# ── the LIVE house rows, 2026-10-02 (the stores that bear on the split, verbatim shapes) ──────────
LIVE_MAPPING = [
    {"org_id": ORG, "store_code": "B-1800", "store_address": "B-1800"},            # ← placeholder
    {"org_id": ORG, "store_code": "1800GreatNeckRd", "store_address": "1800 Great Neck Rd"},
    {"org_id": ORG, "store_code": "B-1", "store_address": "1 S 60th street"},
    {"org_id": ORG, "store_code": "B-2778", "store_address": "B-2778"},            # ← placeholder, address unknown
    {"org_id": ORG, "store_code": "Cellular Services", "store_address": "Cellular Services"},
    {"org_id": ORG, "store_code": "B-103", "store_address": "103 Fulton Ave"},
    {"org_id": ORG, "store_code": "B-1598", "store_address": "1598 Mount Ephraim Ave"},
    {"org_id": ORG, "store_code": "B-3PL", "store_address": "3 Palisade Ave"},
    # B-1115: NO ROW AT ALL — that is the second half of the defect.
]
LIVE_STORES = [
    {"org_id": ORG, "store_code": "B-1800", "address": None, "market": "LI"},
    {"org_id": ORG, "store_code": "B-1115", "address": "1115 Liberty Ave", "market": "LI"},
    {"org_id": ORG, "store_code": "B-60TH", "address": "1 S 60th St, Philadelphia", "market": "PA"},
    {"org_id": ORG, "store_code": "B-103", "address": None, "market": "LI"},
    {"org_id": ORG, "store_code": "B-2778", "address": None, "market": "PA"},
]
LIVE_ALIASES = [
    {"org_id": ORG, "alias": "1800 Great Neck rd", "store_code": "B-1800"},
    {"org_id": ORG, "alias": "1115 Liberty Ave", "store_code": "B-1115"},
    {"org_id": ORG, "alias": "2778 Mount Ephraim Ave", "store_code": "B-1598"},
    {"org_id": ORG, "alias": "3 Palisade Ave Yonkers", "store_code": "B-3PL"},
]

# ── what the runbook writes (the repair, as rows) ─────────────────────────────────────────────────
# `database/runbooks/store_identity_merge_1800_1115.sql` — owner-run, steps 1 / 2 / 2b. There is
# exactly ONE repair path for this defect and this is it; the harness pins ITS statements, so the
# runbook and this proof cannot drift. (Steps 1 and 2 landed in #346; step 2b is the B-60TH sibling.)
REPAIRED_MAPPING = (
    [dict(r, store_address="1800 Great Neck Rd") if r["store_code"] == "B-1800" else dict(r)
     for r in LIVE_MAPPING]
    + [{"org_id": ORG, "store_code": "B-1115", "store_address": "1115 Liberty Ave"},
       {"org_id": ORG, "store_code": "B-60TH", "store_address": "1 S 60th street"}]
)


def resolver_for(mapping, aliases):
    return store_resolver(FakeClient({"store_mapping": mapping, "store_aliases": aliases}), ORG)


def findings_for(mapping, stores, aliases):
    return audit_mod.audit(resolver_for(mapping, aliases), mapping, stores, aliases)


def by_code(findings, code, kind=None):
    return [f for f in findings
            if f["store_code"] == code and (kind is None or f["kind"] == kind)]


print(__doc__.strip().splitlines()[0])
print()

# ════ §A reproduce the live defect ════════════════════════════════════════════════════════════════
print("§A reproduce — the live rows split one store into two keys")
r0 = resolver_for(LIVE_MAPPING, LIVE_ALIASES)
f0 = findings_for(LIVE_MAPPING, LIVE_STORES, LIVE_ALIASES)

ok("A1 B-1800 code and its real address resolve DIFFERENTLY (the reported defect)",
   r0("B-1800").lower() != r0("1800 Great Neck Rd").lower(),
   (r0("B-1800"), r0("1800 Great Neck Rd")))
ok("A2 B-1800 is reported as a split", by_code(f0, "B-1800", audit_mod.SPLIT_KEYS),
   audit_mod.format_findings(by_code(f0, "B-1800")))
ok("A3 B-1800's placeholder address is named as the cause",
   by_code(f0, "B-1800", audit_mod.PLACEHOLDER_ADDRESS))

ok("A4 B-1115 code and its address resolve DIFFERENTLY",
   r0("B-1115").lower() != r0("1115 Liberty Ave").lower(),
   (r0("B-1115"), r0("1115 Liberty Ave")))
ok("A5 B-1115's confirmed alias is INERT — it resolves to neither the code nor a mapped address",
   r0("1115 Liberty Ave").lower() == "1115 liberty ave", r0("1115 Liberty Ave"))
ok("A6 B-1115 is reported as a roster store with no mapping row",
   by_code(f0, "B-1115", audit_mod.ROSTER_WITHOUT_MAPPING))
ok("A7 B-1115 is reported as a split", by_code(f0, "B-1115", audit_mod.SPLIT_KEYS))

ok("A8 B-60TH is the SAME class and is also split (the sibling, found before shipping)",
   by_code(f0, "B-60TH", audit_mod.SPLIT_KEYS) and by_code(f0, "B-60TH", audit_mod.ROSTER_WITHOUT_MAPPING))
ok("A9 B-60TH's roster address already collapses onto B-1's address, but its CODE does not",
   r0("1 S 60th St, Philadelphia").lower() == "1 s 60th street"
   and r0("B-60TH").lower() != "1 s 60th street",
   (r0("1 S 60th St, Philadelphia"), r0("B-60TH")))

ok("A10 B-2778 and Cellular Services are REPORTED as placeholders, not silently tolerated",
   by_code(f0, "B-2778", audit_mod.PLACEHOLDER_ADDRESS)
   and by_code(f0, "Cellular Services", audit_mod.PLACEHOLDER_ADDRESS))

# ════ §B the repair ═══════════════════════════════════════════════════════════════════════════════
print("\n§B the repair — the runbook's three rows collapse every spelling to one key")
r1 = resolver_for(REPAIRED_MAPPING, LIVE_ALIASES)
f1 = findings_for(REPAIRED_MAPPING, LIVE_STORES, LIVE_ALIASES)

for label, spellings, expect in (
        ("B1 B-1800", ["B-1800", "1800 Great Neck Rd", "1800 Great Neck rd", "1800GreatNeckRd"],
         "1800 great neck rd"),
        ("B2 B-1115", ["B-1115", "1115 Liberty Ave", "1115 liberty ave"], "1115 liberty ave"),
        ("B3 B-60TH", ["B-60TH", "1 S 60th St, Philadelphia", "1 S 60th street", "B-1"],
         "1 s 60th street")):
    got = {r1(s).lower() for s in spellings}
    ok("%s — %d spellings → ONE key %r" % (label, len(spellings), expect),
       got == {expect}, sorted(got))

ok("B4 no store is left split", not [f for f in f1 if f["kind"] == audit_mod.SPLIT_KEYS],
   audit_mod.format_findings([f for f in f1 if f["kind"] == audit_mod.SPLIT_KEYS]))
ok("B5 no roster store is left without a mapping row",
   not [f for f in f1 if f["kind"] == audit_mod.ROSTER_WITHOUT_MAPPING])
ok("B6 the ONLY findings left are the two placeholders whose real address is not in the database",
   sorted(f["store_code"] for f in f1) == ["B-2778", "Cellular Services"],
   audit_mod.format_findings(f1))

ok("B7 the junk code 1800GreatNeckRd becomes a harmless code-alias, not a rival key",
   r1("1800GreatNeckRd").lower() == r1("B-1800").lower())
ok("B8 B-1115's confirmed alias is now LIVE (adding the mapping row activated it)",
   r1("1115 Liberty Ave").lower() == r1("B-1115").lower())

# ════ §C negative controls ════════════════════════════════════════════════════════════════════════
print("\n§C negative controls — undo any ONE repair and its finding comes back")
ctrl = [
    ("C1 B-1800 address back to the placeholder", "B-1800",
     [dict(r, store_address="B-1800") if r["store_code"] == "B-1800" else dict(r)
      for r in REPAIRED_MAPPING]),
    ("C2 B-1115 mapping row removed", "B-1115",
     [dict(r) for r in REPAIRED_MAPPING if r["store_code"] != "B-1115"]),
    ("C3 B-60TH mapping row removed", "B-60TH",
     [dict(r) for r in REPAIRED_MAPPING if r["store_code"] != "B-60TH"]),
]
for label, code, mapping in ctrl:
    fc = findings_for(mapping, LIVE_STORES, LIVE_ALIASES)
    ok("%s → %s is split again" % (label, code), by_code(fc, code, audit_mod.SPLIT_KEYS),
       audit_mod.format_findings(fc))

# ════ §D the audit's own truth table ══════════════════════════════════════════════════════════════
print("\n§D the audit's own rules")
ip = audit_mod.is_placeholder_address
ok("D1 address == code is a placeholder", ip("B-1800", "B-1800"))
ok("D2 case/punctuation-insensitive ('b1800' is still the code)", ip("B-1800", "b1800"))
ok("D3 a blank address is a placeholder", ip("B-1115", "") and ip("B-1115", None))
ok("D4 a real address is NOT a placeholder", not ip("B-1800", "1800 Great Neck Rd"))
ok("D5 an address that merely CONTAINS the code is not a placeholder",
   not ip("B-1", "1 S 60th street"))
ok("D6 no code to compare against → judged on the address alone",
   ip("", "") and not ip("", "103 Fulton Ave"))

sp = audit_mod.spellings_for_code("B-1800", REPAIRED_MAPPING, LIVE_STORES, LIVE_ALIASES)
ok("D7 spellings gather code + mapped address + alias, dedup case-insensitively, drop blanks",
   [s.lower() for s in sp] == ["b-1800", "1800 great neck rd"], sp)
ok("D8 a store known only to the roster still yields its spellings",
   len(audit_mod.spellings_for_code("B-1115", LIVE_MAPPING, LIVE_STORES, LIVE_ALIASES)) == 2)
ok("D9 known_codes unions all three tables",
   {"B-1115", "B-1800", "B-60TH", "B-1598", "B-1"}
   <= set(audit_mod.known_codes(LIVE_MAPPING, LIVE_STORES, LIVE_ALIASES)))
ok("D10 the audit is pure — a clean fixture yields NO findings",
   audit_mod.audit(lambda s: "x", [{"store_code": "B-9", "store_address": "9 Main St"}],
                   [{"store_code": "B-9", "address": "9 Main St"}], []) == [])
ok("D11 format_findings says OK on an empty list",
   "OK" in audit_mod.format_findings([]))
ok("D13 REGRESSION — a code DERIVED from its address by deleting the spaces "
   "('1800GreatNeckRd' / '1800 Great Neck Rd') is NOT a placeholder. An alnum-only compare "
   "called it one; this harness caught it before the audit shipped.",
   not ip("1800GreatNeckRd", "1800 Great Neck Rd"))
ok("D14 the same shape in reverse — a spaced code against a squashed address — is still a placeholder",
   ip("B 1800", "B-1800"))
ok("D12 every finding carries one of the enumerated kinds",
   all(f["kind"] in audit_mod.FINDING_KINDS for f in f0))

# ════ §E no collateral merge ═══════════════════════════════════════════════════════════════════════
print("\n§E the repair touches nothing else")
untouched = ["B-103", "103 Fulton Ave", "B-1598", "1598 Mount Ephraim Ave",
             "2778 Mount Ephraim Ave", "B-3PL", "3 Palisade Ave", "3 Palisade Ave Yonkers",
             "B-2778", "Cellular Services", "a store nobody has ever mapped"]
diff = {s: (r0(s), r1(s)) for s in untouched if r0(s) != r1(s)}
ok("E1 every store the runbook does not name resolves BYTE-IDENTICALLY before and after",
   diff == {}, diff)
ok("E2 B-1598 keeps its own address — the 2778 alias does not drag it onto B-2778",
   r1("2778 Mount Ephraim Ave").lower() == "1598 mount ephraim ave",
   r1("2778 Mount Ephraim Ave"))
ok("E3 an unmappable string is still returned as-is (fail-open on display, never invented)",
   r1("a store nobody has ever mapped") == "a store nobody has ever mapped")
ok("E4 two distinct stores sharing no number are never merged",
   r1("B-103").lower() != r1("B-3PL").lower())

# ════ §F the fixture is tied to the REAL runbook ═════════════════════════════════════════════════
# Without this, §B proves only that three invented rows would work. These checks read the actual
# owner-run file and fail if the repair it ships stops matching what §B models — the "lock it so it
# cannot un-wire" rule. There is ONE repair path, so there is ONE file to check.
print("\n§F the repair path on disk matches what §B proves")
RUNBOOK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "database", "runbooks",
                       "store_identity_merge_1800_1115.sql")
ok("F1 the runbook exists (the one repair path for this defect)", os.path.isfile(RUNBOOK), RUNBOOK)
_sql = ""
if os.path.isfile(RUNBOOK):
    with open(RUNBOOK, encoding="utf-8") as fh:
        _sql = fh.read()
_low = _sql.lower()

ok("F2 step 1 puts the real address on B-1800's placeholder row",
   "update commcalc.store_mapping" in _low and "'1800 great neck rd'" in _low
   and "store_code = 'b-1800'" in _low)
ok("F3 step 2 inserts the B-1115 mapping row with the address §B pins",
   "'b-1115'" in _low and "'1115 liberty ave'" in _low
   and "insert into commcalc.store_mapping" in _low)
ok("F4 step 2b inserts the B-60TH mapping row with the address §B pins",
   "'b-60th'" in _low and "'1 s 60th street'" in _low)
ok("F5 every repaired code §B models is named in the runbook",
   all(c.lower() in _low for c in ("B-1800", "B-1115", "B-60TH")))
ok("F6 each step is idempotent (guarded UPDATE / NOT EXISTS insert)",
   _low.count("not exists") >= 2 and "store_address = 'b-1800'" in _low)
ok("F7 the runbook carries REVERT notes for all three steps",
   "revert" in _low and _low.count("b-60th") >= 2)
ok("F9 REGRESSION (§19.38) — a finding's text never rides a MESSAGE-SHAPED key. These "
   "diagnoses name tables on purpose, and a message key's value can reach a tenant; the first "
   "draft used 'detail' and the infra-names lock caught it.",
   all(not _sn.is_message_key(k) for f in f0 for k in f)
   and "diagnosis" in (f0[0] if f0 else {}),
   sorted({k for f in f0 for k in f if _sn.is_message_key(k)}))
ok("F8 NO migration repeats the repair — one path, not two (the duplicate rule)",
   not [f for f in os.listdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                                           "database", "migrations"))
        if "store_mapping_identity" in f or "store_identity_repair" in f],
   "a migration re-writing these rows would drift from the runbook")

print("\n%d checks passed, %d failed" % (PASSED[0], len(FAILED)))
if FAILED:
    for f in FAILED:
        print("  FAILED: %s" % f)
    sys.exit(1)
print("STORE IDENTITY: one physical store, one canonical key — proven over the REAL resolver.")
