"""Proof harness — P&L store/market filter + company-scope semantics (owner bugs 2026-09-02).

DB-free, pure-stdlib. Proves, against fixture data modeled on the LIVE failures:

  A. MARKET RESOLUTION (bug: "when you filter the market from the p&l it does not show any data")
     — a market is resolved through the canonical UNION index (core.scope.build_market_index shape),
       so a market that lives only on the storeops side, or differs in case, still binds its stores;
     — a member store matches by ANY known spelling (exact / squashed / unambiguous street number);
     — fail-closed: an unknown market binds nothing; an AMBIGUOUS street number never matches.

  B. COMPANY ATTRIBUTION (bug: "when you select the companies the proper information is not
     being displayed") — coa.build_company_matcher: exact match is byte-identical to the old rule;
     spelling drift ('1115 Liberty Ave Brooklyn, NY 11208' vs assignment '1115 Liberty Ave')
     attributes by unambiguous street number instead of leaking to Default Company; unassigned
     stores still land on the default company; ambiguity fails closed to the default.

  D. EXPLICIT STORE RESOLUTION (owner bug 2026-10-03: "device cost is not being added to the p&l
     of the following stores 5619 / 6149 / 6507 / 1710") — /core/filter-options offers the STORE
     CODE whenever storeops.stores carries no address, so the picker handed the filter 'B-5619'
     while the snapshot is keyed 'store:5619 N. Broad St.'. The explicit half matched exact
     spellings only, bound zero snapshots, and the whole statement rendered the $0 skeleton.
     store_key_expansion resolves the selection through the SAME canonical vocabulary the market
     half reads, and the $0 skeleton is proved to come back as the real amounts.

  C. COMPOSITION + LINEARITY — the filtered statement is the exact per-line SUM of the matched
     store snapshots (statement_filter.aggregate), company scope × market filter composes as AND,
     and company-wide lines stay $0 in a filtered view.

Run: python3 backend/harness_pl_filter_semantics.py   (exit 0 = all proofs hold)
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.account.statement_filter import (          # noqa: E402
    market_key_expansion, store_key_expansion, build_store_matcher, aggregate)
from app.modules.account.coa import build_company_matcher   # noqa: E402

FAIL = 0


def check(name, cond):
    global FAIL
    print(("  PASS  " if cond else "  FAIL  ") + name)
    if not cond:
        FAIL += 1


# ── A. market resolution over the canonical union index ─────────────────────────────────────────
# Fixture mirrors the live house org: 'PA' spelled in store_mapping ('1 S 60th street') while the
# sales snapshot spells it '1 S 60th St'; 'LI' knows B-1115 only via storeops ('1115 Liberty Ave');
# two stores share leading number '55' (ambiguous — must never match by number).
IDX = {
    "by_market": {
        "pa": {"market": "PA", "codes": {"B-1"},
               "keys": {"B-1", "1 S 60TH STREET"}},
        "li": {"market": "LI", "codes": {"B-1115"},
               "keys": {"B-1115", "1115 LIBERTY AVE"}},
        "nj": {"market": "NJ", "codes": {"B-55A", "B-55B"},
               "keys": {"B-55A", "B-55B", "55 MAIN ST", "55 BROAD AVE"}},
    },
    "addr_keys": {
        "B-1": {"1 S 60TH STREET"},
        "B-1115": {"1115 LIBERTY AVE"},
        "B-55A": {"55 MAIN ST"},
        "B-55B": {"55 BROAD AVE"},
    },
    "alias_keys": {"B-1115": {"LIBERTY AVENUE STORE"}},
    "code_groups": {},
    "key_index": {
        "B1": {"B-1"}, "1S60THSTREET": {"B-1"},
        "B1115": {"B-1115"}, "1115LIBERTYAVE": {"B-1115"}, "LIBERTYAVENUESTORE": {"B-1115"},
        "B55A": {"B-55A"}, "55MAINST": {"B-55A"},
        "B55B": {"B-55B"}, "55BROADAVE": {"B-55B"},
    },
    "stores": [
        {"store_code": "B-1", "address": "1 S 60th street", "market": "PA"},
        {"store_code": "B-1115", "address": "1115 Liberty Ave", "market": "LI"},
        {"store_code": "B-55A", "address": "55 Main St", "market": "NJ"},
        {"store_code": "B-55B", "address": "55 Broad Ave", "market": "NJ"},
    ],
}

print("A. market resolution (canonical union, every spelling, fail-closed)")
up, sq, nums = market_key_expansion(IDX, ["pa"])            # case-insensitive selection
m = build_store_matcher(set(), up, sq, nums)
check("case-insensitive market name binds ('pa' → PA)", m("1 S 60th street"))
check("variant snapshot spelling matches by unambiguous street number ('1 S 60th St')",
      m("1 S 60th St"))
check("other market's store does NOT match", not m("1115 Liberty Ave Brooklyn, NY 11208"))

up, sq, nums = market_key_expansion(IDX, ["LI"])
m = build_store_matcher(set(), up, sq, nums)
check("storeops-side member matches by code key ('B-1115' snapshot)", m("B-1115"))
check("… case-insensitively ('b-1115')", m("b-1115"))
check("sales spelling with city/zip suffix matches by street number "
      "('1115 Liberty Ave Brooklyn, NY 11208')", m("1115 Liberty Ave Brooklyn, NY 11208"))

up, sq, nums = market_key_expansion(IDX, ["nj"])
m = build_store_matcher(set(), up, sq, nums)
check("exact member spellings match", m("55 Main St") and m("55 Broad Ave"))
check("AMBIGUOUS street number never matches a third spelling ('55 Other Blvd')",
      not m("55 Other Blvd"))

up, sq, nums = market_key_expansion(IDX, ["chicago"])       # not in this org's vocabulary
m = build_store_matcher(set(), up, sq, nums)
check("unknown market binds NOTHING (fail-closed)",
      not any(m(a) for a in ("1 S 60th St", "B-1115", "55 Main St")))

m = build_store_matcher({"1 S 60th St"}, set(), set(), set())
check("explicit store filter is case-insensitive and exact (old behaviour kept)",
      m("1 s 60TH st") and not m("1 S 60th street"))

# ── B. company attribution ──────────────────────────────────────────────────────────────────────
print("B. store→company attribution (exact → squash → street number → default)")
ASSIGN = [
    {"store_address": "1115 Liberty Ave", "company_id": "SUPERNOVA"},
    {"store_address": "6507 Castor Avenue", "company_id": "PA-PHONE"},
    {"store_address": "4640-A W Diversey Ave", "company_id": "LUX"},
    {"store_address": "55 Main St", "company_id": "CO-A"},
    {"store_address": "55 Broad Ave", "company_id": "CO-B"},   # '55' ambiguous across companies
]
co = build_company_matcher(ASSIGN, "DEFAULT")
check("exact match byte-identical ('1115 Liberty Ave')", co("1115 Liberty Ave") == "SUPERNOVA")
check("case drift still exact-matches ('6507 CASTOR AVENUE')",
      co("6507 CASTOR AVENUE") == "PA-PHONE")
check("punctuation drift matches by squash ('4640A W Diversey Ave')",
      co("4640A W Diversey Ave") == "LUX")
check("LIVE BUG CASE — sales spelling with city/zip attributes by street number, not Default "
      "('1115 Liberty Ave Brooklyn, NY 11208')",
      co("1115 Liberty Ave Brooklyn, NY 11208") == "SUPERNOVA")
check("unassigned store books to the DEFAULT company ('2778 Mt Ephraim Ave Camden, NJ 08104')",
      co("2778 Mt Ephraim Ave Camden, NJ 08104") == "DEFAULT")
check("ambiguous street number fails CLOSED to default ('55 Other Blvd')",
      co("55 Other Blvd") == "DEFAULT")
check("blank store → default", co("") == "DEFAULT" and co(None) == "DEFAULT")

# ── C. filtered aggregation: linearity + company-wide-$0 + AND-composition ──────────────────────
print("C. filtered statement = exact sum of matched snapshots; company scope composes as AND")


def snap(rev_amt, wages_amt):
    return {"sections": [
        {"name": "Revenue", "type": "revenue",
         "lines": [{"key": "accessory_rev", "label": "Accessory sales revenue", "kind": "auto",
                    "amount": rev_amt, "detail": {}}]},
        {"name": "Operating Expenses", "type": "opex",
         "lines": [{"key": "wages", "label": "Wages / hourly payroll", "kind": "auto",
                    "amount": wages_amt, "detail": {}}]},
    ]}


CONSOLIDATED = {"sections": [
    {"name": "Revenue", "type": "revenue", "lines": [
        {"key": "mi_income", "label": "MI residual income", "kind": "auto", "amount": 28370.84,
         "detail": {}},   # company-wide — must read $0 in ANY filtered view
        {"key": "accessory_rev", "label": "Accessory sales revenue", "kind": "auto",
         "amount": 999.0, "detail": {}},
    ]},
    {"name": "Operating Expenses", "type": "opex", "lines": [
        {"key": "wages", "label": "Wages / hourly payroll", "kind": "auto", "amount": 999.0,
         "detail": {}},
    ]},
]}

STORES = {"1115 Liberty Ave Brooklyn, NY 11208": snap(3786.27, 1200.00),
          "1 S 60th St": snap(1000.50, 800.25),
          "55 Main St": snap(2000.00, 100.00)}

up, sq, nums = market_key_expansion(IDX, ["LI"])
mkt = build_store_matcher(set(), up, sq, nums)
in_co = (lambda a: co(a) == "SUPERNOVA")            # company scope predicate, AND-composed
picked = [p for a, p in STORES.items() if mkt(a) and in_co(a)]
agg = aggregate(picked, "pl", structure=CONSOLIDATED)
by_key = {ln["key"]: ln["amount"] for s in agg["sections"] for ln in s["lines"]}
check("LI market ∩ SUPERNOVA company picks exactly the Liberty store", len(picked) == 1)
check("filtered accessory_rev = that store's amount (3786.27)",
      by_key.get("accessory_rev") == 3786.27)
check("company-wide MI residual reads $0 in the filtered view (skeleton only)",
      by_key.get("mi_income") == 0.0)
check("net income is the exact linear sum (3786.27 − 1200.00)",
      agg["net_income"] == round(3786.27 - 1200.00, 2))

picked_all = [p for a, p in STORES.items()
              if build_store_matcher(set(), *market_key_expansion(IDX, ["LI", "pa", "NJ"]))(a)]
agg_all = aggregate(picked_all, "pl", structure=CONSOLIDATED)
rev_all = next(s["subtotal"] for s in agg_all["sections"] if s["type"] == "revenue")
check("multi-market union sums every matched store (linearity across snapshots)",
      rev_all == round(3786.27 + 1000.50 + 2000.00, 2))

agg_none = aggregate([], "pl", structure=CONSOLIDATED)
check("no matches → full $0 skeleton (never a missing-section crash)",
      agg_none["net_income"] == 0.0
      and {ln["key"] for s in agg_none["sections"] for ln in s["lines"]} >= {"mi_income", "wages"})


# ── D. explicit store selection resolves through the canonical vocabulary (owner bug 2026-10-03) ─
print()
print("D. explicit store selection (code / alias / variant spelling all bind the snapshot)")


def _match(sel):
    return build_store_matcher({sel}, *store_key_expansion(IDX, {sel}))


check("REGRESSION — a store picked by its CODE binds the snapshot address ('B-1115')",
      _match("B-1115")("1115 Liberty Ave"))
check("the exact snapshot address still binds (old behaviour kept)",
      _match("1115 Liberty Ave")("1115 Liberty Ave"))
check("a code selection does NOT bind another store ('B-1115' vs '1 S 60th street')",
      not _match("B-1115")("1 S 60th street"))
check("a POS alias binds the store it names ('Liberty Avenue Store')",
      _match("Liberty Avenue Store")("1115 Liberty Ave"))
check("punctuation / case drift binds by squash ('1115 LIBERTY AVE.')",
      _match("1115 LIBERTY AVE.")("1115 Liberty Ave"))
check("the sales spelling with city/zip binds by street number",
      _match("B-1115")("1115 Liberty Ave Brooklyn, NY 11208"))
check("AMBIGUOUS street number fails closed — '55' names two stores",
      not (_match("55")("55 Main St") or _match("55")("55 Broad Ave")))
check("an unknown spelling binds only itself, never a guess",
      not _match("999 Nowhere Rd")("1115 Liberty Ave"))

# the defect as the owner saw it: a code selection used to render the $0 skeleton
picked_code = [p for a, p in STORES.items() if _match("B-1115")(a)]
agg_code = aggregate(picked_code, "pl", structure=CONSOLIDATED)
by_code = {ln["key"]: ln["amount"] for s in agg_code["sections"] for ln in s["lines"]}
check("REGRESSION — a code-picked store's lines are its real amounts, not $0.00",
      by_code.get("accessory_rev") == 3786.27 and len(picked_code) == 1)

# a store selection and the market it belongs to must agree about the same store
check("store and market expansions agree on the same store (one vocabulary)",
      build_store_matcher(set(), *market_key_expansion(IDX, ["LI"]))("1115 Liberty Ave")
      and _match("B-1115")("1115 Liberty Ave"))

print()
if FAIL:
    print(f"{FAIL} proof(s) FAILED")
    sys.exit(1)
print("ALL PROOFS HOLD — P&L market/company filter semantics")
