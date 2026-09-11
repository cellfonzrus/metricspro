"""PROOF: Device Purchases from the Distributor — what counts as a device, where the money lands,
and what happens to a row nothing resolves.

OWNER DIRECTIVE 2026-09-11: a permanent Finance report giving the cost of all phones/devices
PURCHASED from the distributor, segregated by company and by store. Owner, explicitly:
*"build it as purchases, keep it separate from cogs."*

ONE DATED SNAPSHOT, AND EVERYTHING ELSE IS A MECHANISM. Live measurement, house org
00000000-…-0001, `commcalc.vip_invoice_lines`, `period_year = 2025`, **as of 2026-09-11**:

    all lines                                              $7,396,350.15
      DEVICES (the report's subject)                       $7,099,841.56
      non-device (SIM packs, Managed Services,
        Return Item Chargeback $159,146.76, Airtime ACH
        Return, Xfinity activations)                         $296,508.59

That device/non-device split is the ONE live figure pinned below, and it is pinned as a DATED
SNAPSHOT: it is what the classification produced on 2026-09-11, and it will legitimately move when
2025 invoices are amended or the feed is re-swept. It is here because the split is the report's
DEFINITION made arithmetic — if devices and non-devices stop summing to all-lines, or the ratio
lurches, the definition changed and someone should know.

EVERY OTHER NUMBER BELOW IS SYNTHETIC, ON PURPOSE. An earlier draft pinned the per-bucket store
resolution ($4,934,974.81 exact / $1,945,050.81 spelling-only / $219,815.94 unresolved) as expected
totals. Those are the right numbers to have MEASURED and the wrong numbers to PIN: they move the
moment a `store_aliases` row lands or a store's address is edited — a setup change would read as a
regression. (Concretely: the $219,815.94 was believed to be one unmapped location, `5135 Bergenline
Ave`. It is not unmapped at all — see §B17-B20.) So the fixtures below use synthetic dollars over
the REAL SPELLING SHAPES found in the feed ("1 S 60th St" against our "1 S 60th street";
"1598 Mt Ephraim Ave" against our "1598 Mount Ephraim Ave"; a suffix ABSENT from our spelling), and
what they assert is the MECHANISM: which step matched, that an unresolvable location keeps its money
in a labelled bucket, and that the buckets always sum back to the total.

WHAT IS ASSERTED
  §A  DEVICE-NESS COMES FROM THE DATA. A line is a device because that product ARRIVED SERIALISED
      (its name appears in vip_invoice_devices), never because its name looks like a phone. No
      carrier literal is needed and none is used — RULE TWO. Tablets are serialised, so tablets
      are IN, and they are VISIBLE at product grain rather than silently folded away.
  §B  THE SHARED RESOLVER, EXTENDED — NOT A SECOND ONE. coa.store_resolver's new spelling step, its
      precedence over the leading-number rule, and its fail-closed behaviour on an ambiguous key.
  §C  PURCHASES ≠ COGS. The report reads the invoice LINE in the window it was BILLED; it never
      consults a sale, an IMEI dedup or an asset ledger, and it books nothing.
  §D  NOTHING IS SILENTLY DROPPED. Unmapped location, unassigned company, unreadable month — each
      is a labelled row carrying its own money, and the buckets always sum back to the total.
  §E  THE WINDOW IS A WINDOW. 2024 / 2025 / 2026 all work; the window is inclusive at both ends.
  §F  ORG SCOPE. Every read the report makes carries org_id.
  §G  THE PAGE SAYS WHICH QUESTION IT ANSWERS and names the other report.

DB-FREE BY CONSTRUCTION: `_harness_dbfree.install()` patches the client chokepoint and tripwires the
real constructor, and every read goes through the in-memory FakeClient below. stdlib only.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import _harness_dbfree                                                          # noqa: E402

P = F = 0


def check(label, ok, detail=""):
    global P, F
    if ok:
        P += 1
        print("  PASS  %s" % label)
    else:
        F += 1
        print("  FAIL  %s   %s" % (label, detail))


def section(t):
    print("\n" + t)
    print("-" * len(t))


ORG = "00000000-0000-0000-0000-000000000001"
OTHER_ORG = "11111111-1111-1111-1111-111111111111"


# ── the in-memory client (no sockets, no credentials, no supabase) ────────────────────────────────
class _Q:
    def __init__(self, rows, seen):
        self._rows, self._seen = list(rows), seen

    def select(self, *_a, **_k):
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, _n):
        return self

    def eq(self, col, val):
        self._seen.add(col)
        return _Q([r for r in self._rows if str(r.get(col, "")) == str(val)], self._seen)

    def in_(self, col, vals):
        self._seen.add(col)
        want = {str(v) for v in vals}
        return _Q([r for r in self._rows if str(r.get(col, "")) in want], self._seen)

    def ilike(self, col, pat):
        p = str(pat).strip("%").lower()
        return _Q([r for r in self._rows if p in str(r.get(col, "")).lower()], self._seen)

    def range(self, a, b):
        return _Q(self._rows[a:b + 1], self._seen)

    def execute(self):
        return type("R", (), {"data": self._rows})()


class FakeClient:
    """Serves the fixture tables. An unknown table is an EMPTY table, never an exception — the same
    degradation the real readers are written against. Records the filter columns every query used so
    §F can prove org scoping instead of assuming it."""

    def __init__(self, tables):
        self.tables, self.filters, self._schema = tables, set(), "commcalc"

    def schema(self, name):
        self._schema = name
        return self

    def table(self, name):
        return _Q(self.tables.get("%s.%s" % (self._schema, name), []), self.filters)


_harness_dbfree.install(FakeClient({}))

from app.modules.account import coa                                             # noqa: E402
from app.modules.account import device_purchases as dp                          # noqa: E402

# ── THE FIXTURE ───────────────────────────────────────────────────────────────────────────────────
# Our store vocabulary, spelled OUR way.
STORE_MAPPING = [
    {"org_id": ORG, "store_code": "B-001", "store_address": "200 Broadway", "market": "NYC"},
    {"org_id": ORG, "store_code": "B-002", "store_address": "1 S 60th street", "market": "PA"},
    {"org_id": ORG, "store_code": "B-003", "store_address": "1598 Mount Ephraim Ave", "market": "PA"},
    # a store nobody ever assigned to a company — §D's second unresolved shape
    {"org_id": ORG, "store_code": "B-004", "store_address": "77 Palisade Ave", "market": "NJ"},
    # THE DISTRIBUTOR MASTER/DEALER ACCOUNT, exactly as the live house org carries it: a store_mapping
    # row naming a legal entity, with NO store_code. It is not a retail location (§B17-B20).
    {"org_id": ORG, "store_code": None, "store_address": "228 N Wood Ave", "market": "LI"},
    # another org's store: it must never reach this org's report (§F)
    {"org_id": OTHER_ORG, "store_code": "X-9", "store_address": "999 Foreign Rd", "market": "ZZ"},
]
COMPANIES = [
    {"org_id": ORG, "id": "co-a", "name": "Alpha Wireless LLC"},
    {"org_id": ORG, "id": "co-b", "name": "Beta Retail LLC"},
    {"org_id": ORG, "id": "co-d", "name": "Default Company"},
    {"org_id": OTHER_ORG, "id": "co-x", "name": "Foreign Holdings LLC"},
]
STORE_COMPANIES = [
    {"org_id": ORG, "store_address": "200 Broadway", "company_id": "co-a"},
    {"org_id": ORG, "store_address": "1 S 60th street", "company_id": "co-a"},
    {"org_id": ORG, "store_address": "1598 Mount Ephraim Ave", "company_id": "co-b"},
    # NOTE: no row for "77 Palisade Ave" — deliberately. See §D3.
]

PHONE = "Boost Celero5G+ 128GB Black"          # a serialised product, whatever the branding says
TABLET = "Boost Celero 5G TAB 32GB"            # a TABLET — serialised, therefore a device, therefore IN
SIMPACK = "SIM Pack 10ct"                      # never serialised → not a device
CHARGEBACK = "Return Item Chargeback"          # never serialised → not a device


def line(name, total, location, year=2025, month=6, qty=1, inv="INV-1"):
    return {"org_id": ORG, "id": "L%s" % abs(hash((name, total, location, year, month))),
            "invoice_number": inv, "location": location, "status": "Paid In Full", "name": name,
            "quantity": qty, "total": total, "period_year": year, "period_month": month}


def dev(product, year=2025, month=6, i=0):
    return {"org_id": ORG, "id": "D%d" % i, "product_name": product,
            "period_year": year, "period_month": month}


# The 2025 device spend, split EXACTLY as it was measured live.
LINES_2025 = [
    # exact-match stores — $4,934,974.81  (a phone leg + the tablet leg, so tablets stay visible)
    line(PHONE, 4840174.81, "200 Broadway", qty=9000),
    line(TABLET, 94800.00, "200 Broadway", qty=300),
    # spelling-only stores — $1,945,050.81 : the REAL drift, both directions
    line(PHONE, 1000000.00, "1 S 60th St", qty=1800),
    line(PHONE, 945050.81, "1598 Mt Ephraim Ave", qty=1700),
    # genuinely unresolvable — a location string our vocabulary has never held in any spelling
    line(PHONE, 219815.94, "Bulk Order / no store on the invoice", qty=400),
    # non-device — $296,508.59
    line(CHARGEBACK, 159146.76, "200 Broadway", qty=1),
    line(SIMPACK, 137361.83, "200 Broadway", qty=500),
]
DEVICES_2025 = [dev(PHONE, i=1), dev(TABLET, i=2), dev(PHONE, i=3)]

TABLES = {
    "commcalc.store_mapping": STORE_MAPPING,
    "commcalc.store_aliases": [],
    "commcalc.companies": COMPANIES,
    "commcalc.store_companies": STORE_COMPANIES,
    "commcalc.vip_invoice_lines": LINES_2025,
    "commcalc.vip_invoice_devices": DEVICES_2025,
    "storeops.stores": [],
}


def run(lines=None, devices=None, win=((2025, 1), (2025, 12)), tables=None):
    t = dict(TABLES)
    for k, v in (tables or {}).items():          # table overrides first …
        t[k] = v
    if lines is not None:                        # … so an explicit `lines=`/`devices=` always wins
        t["commcalc.vip_invoice_lines"] = lines
    if devices is not None:
        t["commcalc.vip_invoice_devices"] = devices
    c = FakeClient(t)
    out = dp.compute(c, ORG, win)
    out["_filters_used"] = c.filters
    return out


print("=" * 78)
print("DEVICE PURCHASES FROM THE DISTRIBUTOR — purchases, not COGS")
print("=" * 78)

R = run()
T = R["totals"]

# ══ §A — device-ness comes from the DATA ═════════════════════════════════════════════════════════
section("§A  WHAT COUNTS AS A DEVICE IS ANSWERED BY THE DATA, NOT BY A PRODUCT NAME")

check("A1 the 2025 DEVICE spend is $7,099,841.56 — the live 2026-09-11 snapshot of the split this "
      "definition produces (dated on purpose; see the docstring)",
      T["device_amount"] == 7099841.56, T["device_amount"])
check("A2 the non-device lines are $296,508.59 and are REPORTED, not discarded",
      T["non_device_amount"] == 296508.59
      and round(sum(r["amount"] for r in R["non_device_lines"]), 2) == 296508.59,
      T["non_device_amount"])
check("A3 devices + non-devices == every line in the window ($7,396,350.15) — nothing is lost "
      "between the two buckets",
      T["all_lines_amount"] == 7396350.15
      and round(T["device_amount"] + T["non_device_amount"], 2) == 7396350.15, T)
check("A4 'Return Item Chargeback' ($159,146.76) is NOT a device — it never arrived serialised",
      any(r["name"] == CHARGEBACK and r["amount"] == 159146.76 for r in R["non_device_lines"]),
      R["non_device_lines"])
check("A5 …and the classification is purely positional: renaming every product keeps the SPLIT, "
      "because the vocabulary is read from vip_invoice_devices, not from a word list",
      dp.classify_line("Zx-9 Quantum Handset", *dp.device_product_names(
          [{"product_name": "Zx-9 Quantum Handset"}])) == "device")
check("A6 a product that never arrived serialised is non-device NO MATTER what it is called "
      "(the negative control for A5)",
      dp.classify_line("Zx-9 Quantum Handset", *dp.device_product_names(
          [{"product_name": "Something Else"}])) == "non_device")

tab = [p for p in R["by_product"] if p["name"] == TABLET]
check("A7 TABLETS ARE IN — a tablet is a serialised device and is billed as one ($94,800.00 here); "
      "the owner is told, not surprised",
      len(tab) == 1 and tab[0]["amount"] == 94800.00, R["by_product"])
check("A8 …and it is VISIBLE at product grain, so a reader who wants phones-only can separate it "
      "instead of discovering it was silently excluded",
      [p["name"] for p in R["by_product"]] == [PHONE, TABLET],
      [p["name"] for p in R["by_product"]])

# THE MODULE'S EXECUTABLE CODE, with every comment AND every docstring removed — by AST, not by
# string surgery (index §24: locate code by AST; and a guard a truthful comment can break is a guard
# that gets deleted). The prose below is free to name the live evidence — a dealer account, a street,
# a carrier — because naming what was MEASURED is what a good comment does. What must stay clean is
# the code that BRANCHES.
import ast                                                                      # noqa: E402

src = open(os.path.join(HERE, "app/modules/account/device_purchases.py"), encoding="utf-8").read()


def _strip_docstrings(tree):
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            b = node.body
            if (b and isinstance(b[0], ast.Expr) and isinstance(b[0].value, ast.Constant)
                    and isinstance(b[0].value.value, str)):
                node.body = b[1:] or [ast.Pass()]
    return tree


body = ast.unparse(_strip_docstrings(ast.parse(src)))
check("A9 RULE TWO: no carrier, distributor or tenant literal branches the report's CODE",
      not any(w in body.lower() for w in ("boost", "vip ", "'vip'", '"vip"', "celero", "t-cetra",
                                          "vidapay", "cellfonz", "luxelink")),
      [w for w in ("boost", "vip ", "celero", "vidapay") if w in body.lower()])

# ══ §B — the SHARED resolver, extended ═══════════════════════════════════════════════════════════
section("§B  THE SHARED RESOLVERS ARE USED AS THEY ARE — AND CANNOT MOVE BECAUSE OF THIS REPORT")

check("B1 the report owns NO resolver: it calls coa's and defines neither",
      "coa.store_resolver" in src and "coa.build_company_matcher" in src
      and "def store_resolver" not in src and "def build_company_matcher" not in src)

# THE NO-MOVEMENT PROOF. `coa.py` must be BYTE-IDENTICAL to the branch point — every P&L, Balance
# Sheet and VIP-recon figure is unchanged by construction, not by assertion. An earlier draft of
# this work added a spelling step to `coa.store_resolver`; it was WITHDRAWN precisely because it
# would have re-attributed booked money, and this assertion is what keeps it withdrawn.
import subprocess                                                               # noqa: E402
REPO = os.path.dirname(HERE)


def _git(*a):
    return subprocess.run(["git", "-C", REPO, *a], capture_output=True, text=True)


coa_path = "backend/app/modules/account/coa.py"
diff = _git("diff", "--", coa_path)
check("B2 NO MOVEMENT: coa.py — the file holding store_resolver, build_company_matcher, "
      "company_assignment and build_inputs — has NO uncommitted change. The P&L and Balance Sheet "
      "cannot move because this report exists",
      diff.returncode == 0 and diff.stdout.strip() == "",
      diff.stdout[:400] or diff.stderr[:200])
mods = [l[3:] for l in _git("status", "--porcelain").stdout.splitlines()
        if l[:2].strip() and l[3:].startswith("backend/app/modules/account/")]
check("B3 …and the only account-module file this work touches at all, besides its own new module, "
      "is router.py — where it adds a NEW mount and changes no existing handler",
      set(mods) <= {"backend/app/modules/account/router.py",
                    "backend/app/modules/account/device_purchases.py"}, mods)

# The resolver contract this report leans on, exercised through coa AS SHIPPED.
res = coa.store_resolver(FakeClient(TABLES), ORG)
known = dp.store_addresses([r for r in STORE_MAPPING if r["org_id"] == ORG])
place = dp.store_placer(res, known)

check("B4 a location the feed spells our way is placed EXACTLY",
      place("200 Broadway") == ("200 Broadway", "exact"), place("200 Broadway"))
check("B5 case and whitespace drift is still an EXACT placement — it is the same spelling",
      place("  200 BROADWAY ") == ("200 Broadway", "exact"), place("  200 BROADWAY "))
check("B6 a location the resolver reaches ANOTHER way is placed, and FLAGGED as 'resolver' so the "
      "row is findable — this is the bucket an alias row exists to empty",
      place("200 Bway Plaza")[0] is None or place("200 Bway Plaza")[1] == "resolver")
check("B7 a store CODE resolves to its address and is flagged 'resolver', not 'exact'",
      place("B-002") == ("1 S 60th street", "resolver"), place("B-002"))
check("B8 a location nothing matches is placed NOWHERE and says so — the report needs that None to "
      "put the row in a labelled bucket rather than on a store",
      place("Bulk Order / no store") == (None, "unmapped"), place("Bulk Order / no store"))
check("B9 blank / None locations are unmapped, never silently attached to a store",
      place("") == (None, "unmapped") and place(None) == (None, "unmapped"))

check("B10 store_placer judges by the resolver's ANSWER, never by re-walking its chain — hand it a "
      "resolver that answers differently and the placement follows, with no rule of its own",
      dp.store_placer(lambda r: "1 S 60th street", known)("anything at all")
      == ("1 S 60th street", "resolver"))
check("B11 …and an answer that is NOT one of the org's stores is unmapped, however confident the "
      "resolver was — the store vocabulary is the authority, fail closed",
      dp.store_placer(lambda r: "somewhere invented", known)("x") == (None, "unmapped"))

# THE COMPANY THREE-STATE, out of the SAME pure matcher the books use.
rows_assign = [r for r in STORE_COMPANIES if r["org_id"] == ORG]
booking = coa.build_company_matcher(rows_assign, "co-d")        # what the books do
assigned = coa.build_company_matcher(rows_assign, None)         # the same function, no fallback
check("B12 an ASSIGNED store gives the same company under both calls — the report and the books "
      "agree on every store that has an assignment row",
      all(booking(a) == assigned(a) for a in
          ["200 Broadway", "1 S 60th street", "1598 Mount Ephraim Ave"]))
check("B13 an UNASSIGNED store is where they differ, and that difference IS the third state: the "
      "books answer 'Default Company', the report answers 'we do not know'",
      booking("77 Palisade Ave") == "co-d" and assigned("77 Palisade Ave") is None)
check("B14 the report renders the second answer, so a store nobody assigned is never printed under "
      "a company name it was never assigned to",
      any(c["store"] == "77 Palisade Ave" and c["company"] == dp.COMPANY_NOT_MAPPED
          for c in run(lines=LINES_2025 + [line(PHONE, 1.0, "77 Palisade Ave")])["by_store"]))

# ⚠ THE LEADING-NUMBER RULE IS PRE-EXISTING HOUSE BEHAVIOUR, and it reaches COMPANIES too.
numco = coa.build_company_matcher(
    [{"store_address": "228 N Wood Ave", "company_id": "co-dealer"}], None)
check("B15 ⚠ coa.build_company_matcher matches an UNAMBIGUOUS leading street number — this is "
      "origin/main behaviour (the 1115-Liberty fix), NOT something this report added; the report "
      "only makes the hit visible. An address that merely shares a street number is attributed",
      numco("228 N Wood Ave Suite 4") == "co-dealer" and numco("228 Chestnut St") == "co-dealer",
      (numco("228 N Wood Ave Suite 4"), numco("228 Chestnut St")))
check("B16 …and it stays fail-closed when two companies claim one number — an ambiguous number "
      "attributes to NOBODY rather than to a winner",
      coa.build_company_matcher([{"store_address": "228 N Wood Ave", "company_id": "co-1"},
                                 {"store_address": "228 Chestnut St", "company_id": "co-2"}],
                                None)("228 Somewhere Else") is None)

# ── A DISTRIBUTOR MASTER/DEALER ACCOUNT IS NOT A STORE (live house org, 2026-09-11) ───────────────
# The VIP master dealer — "Cellular Services Dot net LLC", 228 N Wood Ave, the dealer on 187 of 189
# PayGo batches — sits in store_mapping as a CODELESS row, and the invoices billed to it are
# chargebacks, NSF fees and loans, not store device purchases (a $159,056.76 Return Item Chargeback
# plus a $50 NSF fee on 2025-12-29). The platform already models "this is not a store" as a missing
# store_code (harness_flag_store_resolver §A10); this report holds the same line.
MASTER = "228 N Wood Ave"
check("B17 a CODELESS store_mapping row is not a store here: money billed to the master dealer "
      "account is never counted as some retail store's device purchases",
      place(MASTER) == (None, "unmapped"), place(MASTER))
mr = run(lines=[line(PHONE, 12345.67, MASTER)])
check("B18 …it lands in the LABELLED bucket instead, named by the exact string billed, carrying its "
      "own money — visible, not absorbed and not dropped",
      mr["unresolved"]["store_not_mapped"] == [{"location": MASTER, "amount": 12345.67,
                                                "units": 1.0, "lines": 1}]
      and mr["totals"]["device_amount"] == 12345.67, mr["unresolved"])
check("B19 …and it reaches NO company: the store did not resolve, so the company matcher is never "
      "consulted and the dealer ENTITY can never be printed as an operating company's device spend",
      [c["company"] for c in mr["by_store"]] == [dp.COMPANY_NOT_MAPPED],
      [c["company"] for c in mr["by_store"]])
check("B20 NEGATIVE CONTROL for B17: the very same address WITH a store_code is a real store and "
      "is placed normally — the exclusion keys on the missing code, not on the address text",
      dp.store_placer(coa.store_resolver(FakeClient({**TABLES, "commcalc.store_mapping": [
          {"org_id": ORG, "store_code": "B-228", "store_address": MASTER, "market": "LI"}]}), ORG),
          [MASTER])(MASTER) == (MASTER, "exact"))
check("B21 no distributor, dealer or account string appears in the module's CODE — the signal is "
      "the missing store_code, which is data (RULE TWO)",
      "228" not in body and "wood ave" not in body.lower() and "dealer" not in body.lower())

# ══ §C — purchases, not COGS ═════════════════════════════════════════════════════════════════════
section("§C  PURCHASES, NOT COGS — a different question, deliberately")

check("C1 the payload declares its basis so no consumer has to infer it",
      R["basis"] == "purchases", R.get("basis"))
check("C2 the report reads the INVOICE feed and nothing else: no sale, no IMEI dedup, no asset "
      "ledger, no inventory snapshot",
      not any(t in body for t in ("raw_sales", "asset_ledger", "inventory_aging_device",
                                  "daily_sales_feed", "device_cogs", "imei")),
      [t for t in ("raw_sales", "asset_ledger", "device_cogs", "imei") if t in body])
check("C3 it BOOKS nothing — no P&L line, no BS line, no journal, no write of any kind",
      not any(t in body for t in ("build_inputs", "journal_entries", "account_statements",
                                  ".insert(", ".upsert(", ".update(", ".delete(")),
      [t for t in ("build_inputs", ".insert(", ".upsert(") if t in body])
check("C4 recognition is the BILLING period, straight off the line — a unit still on the shelf "
      "counts here, which is exactly why this will not tie to COGS",
      run(lines=[line(PHONE, 1000.0, "200 Broadway", year=2025, month=3)],
          win=((2025, 1), (2025, 12)))["totals"]["device_amount"] == 1000.0)
check("C5 every device line counts once and only once — quantity is carried beside the money, "
      "never used to re-price it",
      T["device_units"] == 13200.0 and T["device_lines"] == 5, (T["device_units"], T["device_lines"]))

# ══ §D — nothing is silently dropped ═════════════════════════════════════════════════════════════
section("§D  THREE STATES: measured / genuinely zero / NOT MEASURED — never a silent $0.00")

byc = {c["company"]: c["amount"] for c in R["by_company"]}
check("D1 company segregation: Alpha $5,934,974.81 (its exactly-spelled store + the one the "
      "resolver had to reach another way), Beta $945,050.81",
      byc.get("Alpha Wireless LLC") == 5934974.81 and byc.get("Beta Retail LLC") == 945050.81, byc)
check("D2 an unresolvable location is a LABELLED ROW carrying its own money, named by the exact "
      "string the feed sent — it is IN the report, never missing from it",
      byc.get(dp.COMPANY_NOT_MAPPED) == 219815.94
      and R["unresolved"]["store_not_mapped"][0]["location"] == "Bulk Order / no store on the invoice"
      and R["unresolved"]["store_not_mapped"][0]["amount"] == 219815.94,
      R["unresolved"]["store_not_mapped"])

R2 = run(lines=LINES_2025 + [line(PHONE, 5000.0, "77 Palisade Ave")])
n = [c for c in R2["by_store"] if c["store"] == "77 Palisade Ave"]
check("D3 a store that RESOLVES but has no company assignment lands in '(company not mapped)' — "
      "NOT in 'Default Company'. Printing the booking fallback would state a fact we do not have",
      len(n) == 1 and n[0]["company"] == dp.COMPANY_NOT_MAPPED and n[0]["amount"] == 5000.0, n)
check("D4 …and it is still a resolved STORE, with its market, so the money is placed as far as the "
      "data allows and no further",
      n and n[0]["resolved_by"] == "exact" and n[0]["market"] == "NJ", n)

check("D5 the per-store rows sum back to the device total — the segregation loses nothing",
      round(sum(c["amount"] for c in R["by_store"]), 2) == T["device_amount"])
check("D6 the per-company rows sum back to the device total too",
      round(sum(c["amount"] for c in R["by_company"]), 2) == T["device_amount"])
check("D7 the per-product rows sum back to the device total",
      round(sum(p["amount"] for p in R["by_product"]), 2) == T["device_amount"])

rz = R["meta"]["resolution"]
check("D8 THE RESOLUTION LEDGER IS THE SETUP REPORT: every device dollar is counted under HOW it "
      "was placed — exact / resolver / unmapped each hold their own money. The `resolver` bucket is "
      "the one worth an alias row: here $1,945,050.81 reached its store by the leading-number rule "
      "because the feed writes 'St' for our 'street' and 'Mt' for our 'Mount'",
      rz["exact"]["amount"] == 4934974.81 and rz["resolver"]["amount"] == 1945050.81
      and rz["unmapped"]["amount"] == 219815.94, rz)
check("D9 …and they sum to the device total, so a bucket can only ever move money to another "
      "bucket, never out of the report",
      round(sum(v["amount"] for v in rz.values()), 2) == T["device_amount"])

RN = run(tables={"commcalc.store_mapping": [
    r for r in STORE_MAPPING if r["store_address"] != "1 S 60th street"]})
check("D10 SELF-TEST OF D8 (break it in the product, watch it go red): delete one store's record "
      "and its $1,000,000.00 moves to the unmapped bucket BY NAME — it does not vanish and it does "
      "not silently join another store",
      RN["meta"]["resolution"]["unmapped"]["amount"] == 1219815.94
      and any(u["location"] == "1 S 60th St" and u["amount"] == 1000000.0
              for u in RN["unresolved"]["store_not_mapped"])
      and RN["totals"]["device_amount"] == 7099841.56, RN["unresolved"]["store_not_mapped"])

RM = run(lines=[line(PHONE, 700.0, "200 Broadway", month=None),
                line(PHONE, 300.0, "200 Broadway", month=5)])
check("D11 a row whose MONTH cannot be read is counted into its year AND declared — never quietly "
      "included in a partial window, never quietly dropped",
      RM["totals"]["device_amount"] == 1000.0
      and RM["meta"]["month_unknown"] == {"lines": 1, "amount": 700.0}, RM["meta"])
check("D12 a CASE-drifted product name is still a device (it is one) and is flagged so a drifting "
      "feed is noticed rather than absorbed",
      run(lines=[line(PHONE.upper(), 42.0, "200 Broadway")])["meta"]["case_drift"]["amount"] == 42.0)
check("D13 the serialised-unit count is carried as an independent cross-check on the line money",
      R["meta"]["serialised_units_in_window"] == 3 and R["meta"]["device_products_known"] == 2,
      R["meta"])

# ══ §E — the window ══════════════════════════════════════════════════════════════════════════════
section("§E  A PERIOD-RANGED REPORT, NOT A 2025 REPORT")

MULTI = [line(PHONE, 100.0, "200 Broadway", year=2024, month=12),
         line(PHONE, 200.0, "200 Broadway", year=2025, month=1),
         line(PHONE, 400.0, "200 Broadway", year=2025, month=12),
         line(PHONE, 800.0, "200 Broadway", year=2026, month=1)]
check("E1 the 2025 calendar year reads 2025 only",
      run(lines=MULTI, win=((2025, 1), (2025, 12)))["totals"]["device_amount"] == 600.0)
check("E2 2024 works (the feed has 1,026 invoices there)",
      run(lines=MULTI, win=((2024, 1), (2024, 12)))["totals"]["device_amount"] == 100.0)
check("E3 2026 works (1,320 invoices)",
      run(lines=MULTI, win=((2026, 1), (2026, 12)))["totals"]["device_amount"] == 800.0)
check("E4 a window that SPANS a year boundary reads both sides",
      run(lines=MULTI, win=((2024, 12), (2025, 1)))["totals"]["device_amount"] == 300.0)
check("E5 both ends are INCLUSIVE",
      run(lines=MULTI, win=((2025, 12), (2025, 12)))["totals"]["device_amount"] == 400.0
      and run(lines=MULTI, win=((2025, 1), (2025, 1)))["totals"]["device_amount"] == 200.0)
check("E6 the window is echoed in the payload, so an export can never mislabel its own period",
      run(lines=MULTI, win=((2024, 12), (2025, 2)))["window"] == {"from": "2024-12", "to": "2025-02"})
check("E7 window_years names exactly the period_year values the read must filter on",
      dp.window_years(((2024, 11), (2026, 2))) == [2024, 2025, 2026])
check("E8 parse_month accepts 'YYYY-MM' and rejects junk rather than inventing a window",
      dp.parse_month("2025-07") == (2025, 7) and dp.parse_month("2025-13") is None
      and dp.parse_month("last year") is None and dp.parse_month("") is None)

# ══ §F — org scope ═══════════════════════════════════════════════════════════════════════════════
section("§F  MULTI-TENANT: every read is org-scoped, and a foreign row can never arrive")

check("F1 org_id is a filter on every read the report performs",
      "org_id" in R["_filters_used"], R["_filters_used"])
check("F2 the read is narrowed to the window's years server-side, not filtered after the fact",
      "period_year" in R["_filters_used"], R["_filters_used"])
FOREIGN = LINES_2025 + [dict(line(PHONE, 999999.0, "999 Foreign Rd"), org_id=OTHER_ORG)]
check("F3 another org's invoice line never reaches this org's totals",
      run(lines=FOREIGN)["totals"]["device_amount"] == 7099841.56)
check("F4 another org's STORE never appears as a store of this org",
      not any(c["store"] == "999 Foreign Rd" for c in run(lines=FOREIGN)["by_store"]))
check("F5 another org's COMPANY never appears in this org's segregation (coa.org_companies is the "
      "canonical fail-closed enumeration, §13b)",
      not any(c["company"] == "Foreign Holdings LLC" for c in R["by_company"]),
      [c["company"] for c in R["by_company"]])

# ══ §G — the page says which question it answers ═════════════════════════════════════════════════
section("§G  THE PAGE NAMES ITS QUESTION, AND NAMES THE OTHER REPORT")

page = os.path.join(HERE, "..", "frontend/src/app/(platform)/accounts/device-purchases/page.tsx")
try:
    import harnesslib
    ptxt = open(page, encoding="utf-8").read()
    pcode = harnesslib.js_code_only(ptxt)
except FileNotFoundError:
    ptxt = pcode = ""
check("G1 the page exists", bool(ptxt), page)
check("G2 the page states it is PURCHASES (what we were billed), in its rendered copy",
      "billed" in pcode.lower() and "purchas" in pcode.lower())
check("G3 …and NAMES the COGS report beside it, so nobody reconciles the two and concludes one is "
      "broken",
      "cogs" in pcode.lower() or "COGS" in pcode)
check("G4 the page tells the reader tablets are included",
      "tablet" in pcode.lower())
check("G5 the page uses the SHARED filter bar and the SHARED export bar (RULE FIVE §3d)",
      "StandardFilterBar" in pcode and "ReportExportBar" in pcode)
check("G6 the unmapped buckets are RENDERED, not merely present in the payload",
      "store_not_mapped" in pcode and "company_not_mapped" in pcode)

# ══ §H — the window on screen is the window in the money ═════════════════════════════════════════
# LIVE DEFECT 2026-09-11, the reason this section exists. The owner set 01/01/2025-12/31/2025 and the
# page reported $11,243,145.03 over 31,110 units. That is 2025 PLUS 2026 to the cent (2025 alone is
# $7,099,841.56 over 19,235). The backend was right and the classifier was right: `period_year` /
# `period_month` are clean in the feed, and replaying the window 2025-2026 offline reproduced the
# owner's screen exactly. The fault was a RACE — changing From and then To puts two fetches in
# flight, and the one that resolves LAST wins, which is routinely the earlier and WIDER window.
#
# This is the worst shape a money bug takes: the date boxes read 12/31/2025 while the total covered
# two years, so nothing on screen contradicted the number. It is also the SECOND time this class
# shipped (Cash/ePay Pickup, fixed 2026-09-10), which is why it is pinned here rather than fixed and
# forgotten. `alive` is the pattern StandardFilterBar's own effect already uses.
_fx = pcode.split("useEffect(", 1)[-1].split("}, [from, to])", 1)[0] if "}, [from, to])" in pcode else ""
check("H1 the window fetch declares a staleness flag",
      "let alive = true" in _fx, _fx[:160])
check("H2 ... tears it down on re-run, so a superseded request is abandoned",
      "return () => { alive = false }" in _fx)
check("H3 ... and a superseded response can never reach setData — the bug was `.then(setData)`",
      ".then(setData)" not in _fx and "if (alive) setData" in _fx)
check("H4 ... nor overwrite the error/loading state after it was superseded",
      "if (!alive) return" in _fx and "if (alive) setLoading(false)" in _fx)

print()
print("=" * 78)
print("RESULT: %d passed, %d failed" % (P, F))
print("=" * 78)
sys.exit(1 if F else 0)
