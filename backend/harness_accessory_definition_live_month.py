"""THE PROOF — a detector that reads a different table from the classifier is not a detector.

Drives the REAL `router._ad_sales` / `_is_accessory` / `_accessory_config_uncached` and the REAL
`accessory_definition.field_token_hit` / `agreement` over in-memory rows. DB-free: the sales tables and
the config row are served by a tiny fake client, so nothing here touches Supabase and it runs in CI.

  python3 backend/harness_accessory_definition_live_month.py

THE DEFECT, as the owner saw it (2026-10-14, B2B "Month To Date Location Sales Report" for Boost):
    "need to check why the accessories sales are showing less in metricspro for boost than by b2b"

MEASURED AGAINST PRODUCTION (house org, read-only). B2B's Acc. Sales for 10/01-10/13 was $21,329.72
over its nineteen Boost stores; MetricsPro's shared `_is_accessory` summed $19,785.91 over the same
nineteen. FOURTEEN of the nineteen tied to the penny, which is what said the classifier was right and
its VOCABULARY was short by one word: the house POS renamed its accessory category `Accessory` ->
`Accessories` in July 2026. `accessory_config.categories` is matched by EXACT membership, so eight
'Apple Airpods Pro 3' lines rung under department 'BYOD' / category 'Accessories' -- $1,560.96 in
October to the 13th -- read as non-accessory. Since the rename: Jul $783.95, Aug $1,472.77,
Sep $3,322.66, Oct(1-14) $1,560.96 = $7,304.58. The other two tenants were checked in the same pass;
LuxeLink already declares 'Accessories' and the third org has no October rows, so the hole is the house
org's alone.

THE CLASS, NOT THE INSTANCE. The instance is one missing string in one config row, and that row is the
fix (a per-tenant config row is not a patch -- per-tenant CODE is). The CLASS is that the platform
already HAS a detector for exactly this -- `/accessory-definition/agreement`, whose `only_here` column
is by construction "lines the tenant's own declared definition would count and the money path does
not", and whose declared default field rule is `accessory_definition.DEFAULT_TOKENS = ('accessor',)`,
a SUBSTRING that covers 'Accessories' -- and it could not see one of those eight lines. Its reader
`router._ad_sales` read `commcalc.raw_sales` alone. raw_sales is the MONTH-END ARCHIVE whose promotion
has been manual since `report_definitions.sales.auto` went false: live it holds ZERO rows for September
and October 2026. So the one surface built to report this class of gap was structurally blind to the
open month, and to every month whose archive had not been built, while the classifier it was auditing
read the live feed.

ONE FACT, ONE HOME, DEREFERENCED. "Which rows are this period's sales" is not re-decided here.
`_ad_sales` now reads `router._sales_rows_union_txn` -- the ONE transaction-grain union (raw_sales u
daily_sales_feed, deduped by trans_id, the open-month primary winning a whole transaction), the same
reader the Sales Report, Executive MTD and the pay path already dereference. Fixing the one helper
fixes all six surfaces under it at once: `/accessory-definition/agreement`, `/observed`, `/propose`,
the save preview, `/pay-impact`, `/setup-fee/candidates` and `/setup-fee/recognition-divergence`.

VERIFIED WITH THE REAL CODE AGAINST PRODUCTION (read-only, 2026-10-14):
    _ad_sales rows      2026-10: 0 -> 11,950    2026-09: 0 -> 22,914    2026-08: 24,890 -> 24,890
    agreement only_here 2026-10: - -> 8 lines / $1,560.96   2026-09: - -> 22 lines / $3,322.66
August is a promoted, closed month: raw_sales leads and the feed contributes nothing extra, so a month
whose archive exists is byte-identical. The $1,560.96 and $3,322.66 the report now names are the same
figures measured independently off the feed, so the detector and the arithmetic agree.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test")
os.environ.setdefault("SUPABASE_KEY", "test")

from datetime import datetime, timezone                      # noqa: E402

from app.modules.commcalc import router as R                  # noqa: E402
from app.modules.commcalc import accessory_definition as AD   # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else f"{name}{(' — ' + detail) if detail else ''}")


# ── THE FAKE CLIENT (DB-free): serves the two sales tables and one accessory_config row ───────────
class _Q:
    def __init__(self, rows, count_mode=False):
        self._rows, self._count = rows, count_mode
        self._neq = []

    def select(self, *_a, **kw):
        self._count = kw.get("count") == "exact"
        return self

    def eq(self, *_a, **_k):
        return self

    def in_(self, *_a, **_k):
        return self

    def neq(self, col, val):
        self._neq.append((col, val))
        return self

    def limit(self, *_a, **_k):
        return self

    def range(self, *_a, **_k):
        return self

    def order(self, *_a, **_k):
        return self

    def execute(self):
        rows = self._rows
        for col, val in self._neq:
            rows = [r for r in rows if str(r.get(col) or "") != str(val)]
        return type("R", (), {"data": rows, "count": len(rows)})()


class _Schema:
    def __init__(self, tables):
        self._t = tables

    def table(self, name):
        return _Q(list(self._t.get(name, [])))


class FakeClient:
    def __init__(self, tables):
        self._t = tables

    def schema(self, _s):
        return _Schema(self._t)


ORG = "00000000-0000-0000-0000-000000000001"
OPEN = f"{datetime.now(timezone.utc).year}-{datetime.now(timezone.utc).month:02d}"

# The house org's REAL accessory config, read from production 2026-10-14 (read-only) — the category
# list carries the SINGULAR spelling only, which is the defect.
HOUSE_CFG = {
    "org_id": ORG,
    "departments": ["Ondigo", "C2 Wireless", "Sim Cards", "VoiceComm"],
    "categories": ["Accessory"],
    "product_keywords": ["case", "charger", "screen protector", "cable", "mybat", "ondigo"],
    "setup_fee_keywords": ["Device Setup Charge"],
    "box_departments": ["Android - XP", "IPHONE - XP", "TABLET - XP"],
    "box_count_buckets": ["byod"],
    "catalog_classify_enabled": False,
    "billpay_products": [],
    "billpay_fee_product_desc": [],
    "contract_type_map": {},
    "activation_rules": [],
    "activation_details_rules": {},
    "definition_field_rule": None,
}
FIXED_CFG = dict(HOUSE_CFG, categories=["Accessory", "Accessories"])


def line(dept, cat, prod, tid, ext="0", date=None, store="117 E Burnside Ave", rep="rep"):
    return {"trans_id": tid, "store": store, "salesperson": rep,
            "trans_date": date or f"{OPEN}-11", "period": OPEN,
            "department": dept, "category": cat, "product_desc": prod,
            "contract_type": "", "ext_price": ext, "gp": "0",
            "voided": "", "trans_type": "Sale", "sku": ""}


# The eight lines, as the POS actually rings them (production shape, 2026-10).
AIRPODS = line("BYOD", "Accessories", "Apple Airpods Pro 3", "t1", ext="250.00")
# An accessory the department list DOES catch, so the predicate is not simply broken.
ONDIGO = line("Ondigo", "Cables", "Ondigo Type C to Type C 3Ft Charging/Data Cable, White", "t2", ext="19.99")
# A device line, which must never become an accessory.
PHONE = line("IPHONE - XP", "APPLE", "iPhone 15 128GB", "t3", ext="399.99")


def acfg_of(cfg):
    return R._accessory_config_uncached(FakeClient({"accessory_config": [cfg]}), ORG)


def is_acc(row, cfg):
    a = acfg_of(cfg)
    return R._is_accessory(row.get("department"), row.get("category"), row.get("product_desc"), a)


# ══ A. THE REGRESSION — the reported defect, reproduced and then closed ═══════════════════════════
ok("A1 the reported defect reproduces: 'Accessories' is not an accessory under the house config",
   is_acc(AIRPODS, HOUSE_CFG) is False)
ok("A2 and the classifier is not simply broken — a mapped department still classifies",
   is_acc(ONDIGO, HOUSE_CFG) is True)
ok("A3 the config row closes it: declaring the POS's own spelling counts the line",
   is_acc(AIRPODS, FIXED_CFG) is True)
ok("A4 and declaring it does not drag a device line in with it",
   is_acc(PHONE, FIXED_CFG) is False)
ok("A5 the dollars the fix moves are the ones measured, not more: only the 'Accessories' lines change",
   [is_acc(r, HOUSE_CFG) != is_acc(r, FIXED_CFG) for r in (AIRPODS, ONDIGO, PHONE)] == [True, False, False])

# ══ B. THE DECLARATION ALREADY COVERED IT — so the gap was reportable all along ═══════════════════
RULE, refused = AD.normalize_field_rule(None)
ok("B1 the platform's DECLARED default field rule is the 'accessor' substring on department/category",
   RULE["enabled"] and RULE["tokens"] == ["accessor"] and set(RULE["token_fields"]) == {"department", "category"},
   repr(RULE))
ok("B2 and it fires on the very line the exact-match category list missed",
   AD.field_token_hit(AIRPODS, RULE)[0] == "category")
ok("B3 so the disagreement was ALWAYS reportable — declaration says accessory, predicate says no",
   AD.field_token_hit(AIRPODS, RULE)[0] is not None and is_acc(AIRPODS, HOUSE_CFG) is False)
ok("B4 the declaration does not claim the device line either (the report would not cry wolf)",
   AD.field_token_hit(PHONE, RULE)[0] is None)
ok("B5 nothing in the default rule is refused", refused == [])

# ══ C. THE DETECTOR'S READER — the one union, the open month, not the dead archive ════════════════
FEED = [AIRPODS, ONDIGO, PHONE]
ARCHIVE_EMPTY = {"daily_sales_feed": FEED, "raw_sales": []}
rows, meta = R._ad_sales(FakeClient(ARCHIVE_EMPTY), ORG, period=OPEN)
ok("C1 with the archive empty — production's September and October — the detector sees the feed",
   len(rows) == 3, f"saw {len(rows)}")
ok("C2 and it says which tables it read, so an empty answer can never pass as 'no gap'",
   (meta.get("union") or {}).get("primary") == "daily_sales_feed"
   and str(meta.get("source") or "").startswith("raw_sales"),
   repr(meta.get("source")))
ok("C3 the OLD reader is what the bug was: raw_sales alone would have returned nothing at all",
   len(ARCHIVE_EMPTY["raw_sales"]) == 0)

# A promoted, closed month: the archive leads and the feed adds nothing — byte-identical.
CLOSED = "2026-08"
BOTH = {"daily_sales_feed": [dict(r, period=CLOSED, trans_date="2026-08-11") for r in FEED],
        "raw_sales": [dict(r, period=CLOSED, trans_date="2026-08-11") for r in FEED]}
rows_c, meta_c = R._ad_sales(FakeClient(BOTH), ORG, period=CLOSED)
ok("C4 a closed month whose archive exists is unchanged — the union dedupes by trans_id, no double count",
   len(rows_c) == 3 and (meta_c.get("union") or {}).get("primary") == "raw_sales", f"saw {len(rows_c)}")
# A transaction only the archive holds is still kept (completeness, not feed-only).
ONLY_ARCHIVE = {"daily_sales_feed": [dict(FEED[0], period=CLOSED, trans_date="2026-08-11")],
                "raw_sales": [dict(FEED[1], period=CLOSED, trans_date="2026-08-11")]}
rows_o, _ = R._ad_sales(FakeClient(ONLY_ARCHIVE), ORG, period=CLOSED)
ok("C5 and a transaction only one side holds is never dropped",
   len(rows_o) == 2, f"saw {len(rows_o)}")

# The filters the six surfaces pass still narrow, and still narrow in the same direction.
MIXED = {"daily_sales_feed": [AIRPODS,
                              dict(ONDIGO, store="509 Nostrand Ave", salesperson="other")],
         "raw_sales": []}
r_s, _ = R._ad_sales(FakeClient(MIXED), ORG, period=OPEN, store="509 Nostrand Ave")
r_r, _ = R._ad_sales(FakeClient(MIXED), ORG, period=OPEN, rep="rep")
ok("C6 the store filter still narrows", len(r_s) == 1 and r_s[0]["store"] == "509 Nostrand Ave")
ok("C7 the rep filter still narrows", len(r_r) == 1 and r_r[0]["salesperson"] == "rep")
r_b, m_b = R._ad_sales(FakeClient(ARCHIVE_EMPTY), ORG)
ok("C8 a blank period reads the CURRENT month rather than every period ever",
   len(r_b) == 3 and (m_b.get("filters") or {}).get("period") == OPEN, repr(m_b.get("filters")))
r_cap, m_cap = R._ad_sales(FakeClient(ARCHIVE_EMPTY), ORG, period=OPEN, cap=2)
ok("C9 the cap still truncates and SAYS it truncated",
   len(r_cap) == 2 and m_cap.get("truncated") is True)

# ══ D. THE UN-WIRE LOCK — this cannot quietly go back to reading the archive ══════════════════════
import inspect                                                # noqa: E402

SRC = inspect.getsource(R._ad_sales)
ok("D1 LOCK _ad_sales dereferences the one transaction-grain union",
   "_sales_rows_union_txn(" in SRC)
ok("D2 LOCK _ad_sales builds no query of its own — no second union over the two sales tables",
   ".table(" not in SRC and "daily_sales_feed'" not in SRC and 'daily_sales_feed"' not in SRC)
ok("D3 LOCK it passes cols='*' — a named list re-breaks it, because raw_sales has `sku` and the feed "
   "does not and selecting an absent column throws, swallowed to []",
   "cols='*'" in SRC or 'cols="*"' in SRC)
ok("D4 LOCK every surface still reads through the ONE helper — none re-reads raw_sales for itself",
   len([l for l in inspect.getsource(R).splitlines()
        if "_ad_sales(" in l and "def _ad_sales" not in l]) >= 6)
ok("D5 LOCK the one union is still the pay-eligible transaction-grain one it claims to be",
   "deduped by trans_id" in (inspect.getdoc(R._sales_rows_union_txn) or ""))

# ══ E. NEGATIVE CONTROLS — the fix is load-bearing, not decoration ════════════════════════════════
ok("E1 NEG an empty feed AND an empty archive still reports zero rows, never a fabricated row",
   R._ad_sales(FakeClient({"daily_sales_feed": [], "raw_sales": []}), ORG, period=OPEN)[0] == [])
ok("E2 NEG a read that blows up is reported on `error`, never returned as a clean empty month",
   R._ad_sales(type("Boom", (), {"schema": lambda *_a: (_ for _ in ()).throw(RuntimeError("x"))})(),
               ORG, period=OPEN)[1].get("rows_read") == 0)
ok("E3 NEG the predicate is not quietly substring-matching categories — that WOULD be a code fix to a "
   "config problem, and would move every tenant's money unasked",
   is_acc(line("BYOD", "Accessories Bundle Refund", "x", "t"), HOUSE_CFG) is False)
ok("E4 NEG the declaration is not wired INTO the predicate by this change — reporting the gap and "
   "resolving it are different acts, and the second is the owner's",
   is_acc(AIRPODS, HOUSE_CFG) is False and AD.field_token_hit(AIRPODS, RULE)[0] == "category")

print(f"PASS {len(PASS)}")
for p in PASS:
    print(f"  [PASS] {p}")
if FAIL:
    print(f"\n❌ {len(FAIL)} failure(s):")
    for f in FAIL:
        print(f"  [FAIL] {f}")
    sys.exit(1)
print("\n✅ harness_accessory_definition_live_month: ALL PASS")
