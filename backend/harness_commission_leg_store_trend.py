"""PROOF — the per-store, month-over-month month-of-life (M1..M12) commission report (index
§COMMCALC-11).

OWNER ASK, verbatim (2026-10-15): *"this is a good insight , this means we need to see these numbers as
a report sisde by sie as shown above for each store to analyze , create one"* — the M1..M12 rungs side
by side across months, PER STORE, so a store whose trailing legs are running down names itself.

WHAT THIS PROVES, and why each one is money and not decoration:

  A. THE PIVOT IS A PIVOT. Σ(per-store cells) == the company period total, to the cent, for every
     month. This is the whole claim that `/commission-leg-store-trend` is a SHAPE of the leg trend and
     not a second derivation of it: if the two could differ, the house would have two paths answering
     one question, which is the defect the index rules forbid.

  B. THE MA STORE GRAIN NEEDS NO SCHEMA CHANGE. `split_ma_components` is LINEAR in the component sums,
     so bucketing per-store sums and then adding == adding the sums and then bucketing. That is the
     proof that reading `raw_ma_commission`'s rows (which carry the processor account) gives the same
     money as `commission_leg_ma_rollup` (which pre-aggregates them per period and drops the account)
     — one grain finer, not one dollar different. The index note said the store axis was blocked on
     that rollup; it was blocked on the ROWS, and this is why no migration was needed.

  C. THE ACCUMULATOR HAS ONE HOME. The router increments a period row's buckets in exactly one
     function (`_leg_accumulate`), reached by both grains, and the MA path passes `with_ladder=False`.
     REGRESSION: the MA split reports its buckets AND its rungs as two read-outs of the same columns,
     so an accumulator that also added the rung would count that money twice — the harness reproduces
     that double count and requires the real call site to avoid it.

  D. A PARTIAL MONTH IS NOT A COLLAPSE. The window ends on the page's own period, normally the current
     calendar month. A half-collected month is marked and excluded from every delta. REGRESSION: the
     house org's real October 2026 (read 2026-10-15) was running at roughly a quarter of September;
     treated as an endpoint it would report every one of 28 stores as collapsing at once.

  E. MONEY WITH NO STORE IS ITS OWN ROW. Never spread across stores, never dropped, and it sorts last
     so it cannot be mistaken for a store.

  F. THE RUNGS COME FROM THE DATA. An M13 is a column the day it arrives; a rung a store has no money
     in is $0, not a hole; a rung nobody has is not invented. No `range(1, 13)`, no month COUNT.

  G. A DECLINE IS NAMED BY RUNG. `worst_rung` distinguishes the store losing M1 (shrinking now) from
     the store whose M1 holds while M4–M6 fall (lost its cohort months ago) — the distinction the
     report exists to make.

  H. ARITHMETIC THAT WOULD LIE IS REFUSED. A $0 baseline yields pct None, never 0 and never infinity;
     a store the window sees once gets delta None, not 0; a negative rung keeps its sign.

  I. A GAP IS REPORTED. When money reaches the company series that the store axis cannot place,
     `ties_to` says so per month rather than smoothing it over.

  J. RULE TWO. No carrier, tenant, product name, month count or rate in the pivot module.

PURE / DB-FREE: imports two modules and runs them over literal rows. No DB, no network, no fixtures.
Run: python3.11 harness_commission_leg_store_trend.py
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from app.modules.commcalc import commission_legs as legs                      # noqa: E402
from app.modules.commcalc import commission_leg_store_trend as pivot          # noqa: E402

FAILS = []


def check(name, ok, why=""):
    print(("  PASS  " if ok else "  FAIL  ") + name + ("" if ok else "   -- " + why))
    if not ok:
        FAILS.append(name)


def eq(name, got, want):
    check(name, got == want, f"got {got!r}, want {want!r}")


def code_only(src):
    """The EXECUTABLE text of a module: docstrings, string literals and comments removed, via the
    stdlib tokenizer (so the rule is Python's own idea of a string, not a regex's).

    Scanning raw source makes a file's own documentation trip its own checks — this harness's first
    draft failed F6 and J2 on the pivot module's DOCSTRINGS, which say in words that it writes no
    `range(1, 13)` and reads no `_datetime.now(...)`. A lock that cannot tell a prohibition from its
    own description is not a lock, so the prose comes out before the scan."""
    import io
    import tokenize
    lines = src.split("\n")
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type not in (tokenize.STRING, tokenize.COMMENT):
                continue
            # Blank the token's span IN PLACE, so every other line, column and identifier is
            # byte-identical to the file. (Re-joining tokens instead would glue `ladder_label` to
            # `(` with a newline and quietly break every substring check below.)
            (r1, c1), (r2, c2) = tok.start, tok.end
            for r in range(r1, r2 + 1):
                ln = lines[r - 1]
                a = c1 if r == r1 else 0
                b = c2 if r == r2 else len(ln)
                lines[r - 1] = ln[:a] + " " * (b - a) + ln[b:]
        return "\n".join(lines)
    except (tokenize.TokenError, IndentationError):
        # A fragment (the router slice) need not be a complete module; fall back to dropping the
        # comment and triple-quoted forms, which is all that slice contains.
        import re as _re
        src = _re.sub(r'(?s)""".*?"""', "", src)
        src = _re.sub(r"(?m)#.*$", "", src)
        return src


def accumulate_calls(src):
    """Every `_leg_accumulate(...)` call in `src`, whole, however many lines it spans.

    Paren-matched rather than line-matched: this harness's first draft used `[^\\n]*` and so saw only
    one of the two MA calls — the WRAPPED one was invisible to the very regression it exists to catch.
    A scan that silently misses a subject is the §CORE-10 defect in miniature."""
    out = []
    for m in re.finditer(r"(?<!def )_leg_accumulate\(", src):   # the DEFINITION is not a call site
        i, depth = m.end() - 1, 0
        while i < len(src):
            if src[i] == "(":
                depth += 1
            elif src[i] == ")":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        out.append(" ".join(src[m.start():i + 1].split()))
    return out


def cell(m1=0.0, m2_12=0.0, unsplit=0.0, ladder=None):
    lad = dict(ladder or {})
    return {"m1": m1, "m2_12": m2_12, "unsplit": unsplit,
            "total": round(m1 + m2_12 + unsplit, 2), "ladder": lad}


# ── A SHAPED LIKE THE REAL THING ──────────────────────────────────────────────────────────────────
# Three stores and one unplaceable line over four months, the last of which has not finished.
# NORTH  — M1 flat, M4/M5 falling: the cohort shrank months ago.
# SOUTH  — M1 falling, trailing flat: shrinking right now.
# EAST   — appears in one complete month only.
LABELS = ["July 2026", "August 2026", "September 2026", "October 2026"]
NOW_Y, NOW_M = 2026, 10

BY_STORE = {
    "July 2026": {
        "100 North Ave": cell(1000, 3000, 0, {"1": 1000, "4": 1600, "5": 1400}),
        "200 South St": cell(900, 600, 0, {"1": 900, "2": 600}),
        pivot.UNATTRIBUTED_KEY: cell(0, 0, 250, {legs.LADDER_UNKNOWN: 250}),
    },
    "August 2026": {
        "100 North Ave": cell(1000, 2200, 0, {"1": 1000, "4": 1200, "5": 1000}),
        "200 South St": cell(600, 600, 0, {"1": 600, "2": 600}),
        "300 East Rd": cell(500, 0, 0, {"1": 500}),
        pivot.UNATTRIBUTED_KEY: cell(0, 0, -40, {legs.LADDER_UNKNOWN: -40}),
    },
    "September 2026": {
        "100 North Ave": cell(1000, 1400, 0, {"1": 1000, "4": 500, "5": 900}),
        "200 South St": cell(300, 600, 0, {"1": 300, "2": 600}),
        pivot.UNATTRIBUTED_KEY: cell(0, 0, -300, {legs.LADDER_UNKNOWN: -300}),
    },
    # The partial month. Collected to the 15th, so every store reads as if it halved.
    "October 2026": {
        "100 North Ave": cell(500, 600, 0, {"1": 500, "4": 300, "5": 300}),
        "200 South St": cell(150, 300, 0, {"1": 150, "2": 300}),
    },
}
SERIES = [{"period": lab,
           "m1": round(sum(c["m1"] for c in BY_STORE[lab].values()), 2),
           "m2_12": round(sum(c["m2_12"] for c in BY_STORE[lab].values()), 2),
           "unsplit": round(sum(c["unsplit"] for c in BY_STORE[lab].values()), 2),
           "total": round(sum(c["total"] for c in BY_STORE[lab].values()), 2),
           "by_store": BY_STORE[lab]} for lab in LABELS]
META = {"100 North Ave": {"store": "100 North Ave", "store_code": "N1", "market": "NORTH"},
        "200 South St": {"store": "200 South St", "store_code": "S1", "market": "SOUTH"},
        "300 East Rd": {"store": "300 East Rd", "store_code": "E1", "market": "EAST"}}

PARTIALS = pivot.partial_months(LABELS, NOW_Y, NOW_M)
ROWS, RUNGS = pivot.pivot(LABELS, SERIES, META, PARTIALS)
TOTALS = pivot.totals_row(LABELS, ROWS, RUNGS, PARTIALS)
TIES = pivot.ties_to(LABELS, TOTALS, SERIES)
ROW = {r["store_key"]: r for r in ROWS}

print("\n§COMMCALC-11 — per-store month-of-life commission trend\n")

# ── A. the pivot is a pivot ───────────────────────────────────────────────────────────────────────
print("A. the store axis re-sums to the company figure")
for lab in LABELS:
    shown = round(sum(r["months"][lab]["total"] for r in ROWS), 2)
    want = round(next(p["total"] for p in SERIES if p["period"] == lab), 2)
    check(f"A1 {lab}: Σ(per-store) == company total", abs(shown - want) < 0.01,
          f"shown {shown}, company {want}")
for lab in LABELS:
    for k in ("m1", "m2_12", "unsplit"):
        shown = round(sum(r["months"][lab][k] for r in ROWS), 2)
        want = round(next(p[k] for p in SERIES if p["period"] == lab), 2)
        check(f"A2 {lab}: Σ(per-store {k}) == company {k}", abs(shown - want) < 0.01,
              f"shown {shown}, company {want}")
check("A3 every month of every row re-sums m1+m2_12+unsplit to its own total",
      all(abs(round(m["m1"] + m["m2_12"] + m["unsplit"], 2) - m["total"]) < 0.01
          for r in ROWS for m in r["months"].values()))
check("A4 the displayed footer agrees with the company series on every month",
      all(v["ok"] for v in TIES.values()),
      f"gaps: {[(k, v['gap']) for k, v in TIES.items() if not v['ok']]}")

# ── B. the MA store grain needs no schema change ──────────────────────────────────────────────────
print("\nB. MA/VidaPay per-store == MA per-period (so the rollup's missing account blocked nothing)")
# Two processor accounts resolving to two stores, with margins AND spiff legs — the real shape.
MA_RAW = [
    {"store": "100 North Ave", "spiff_m1": -20.00, "spiff_m2": -15.00, "rebate": -9.00},
    {"store": "100 North Ave", "spiff_m1": -17.50, "spiff_m4": -12.25, "device_margin": -4.75},
    {"store": "200 South St", "spiff_m1": -20.01, "spiff_m6": -30.00, "fees_margin": -1.10},
    {"store": "", "spiff_m1": -5.00},        # an account the store index does not know
]
COMPONENTS = ["device_margin", "consumer_margin", "consumer_financing", "rebate",
              "wallet_funding", "fees_margin",
              "spiff_m1", "spiff_m2", "spiff_m3", "spiff_m4", "spiff_m5", "spiff_m6"]
cls = legs.default_classifier()


def _sum(rows):
    return {c: sum(float(r.get(c) or 0) for r in rows) for c in COMPONENTS}


whole = cls.ma(_sum(MA_RAW), COMPONENTS)
groups = {}
for r in MA_RAW:
    groups.setdefault(r["store"], []).append(r)
parts = {s: cls.ma(_sum(rs), COMPONENTS) for s, rs in groups.items()}
for b in ("m1", "trailing", "unsplit"):
    got = round(sum(p["buckets"][b] for p in parts.values()), 2)
    eq(f"B1 Σ(per-store {b}) == per-period {b}", got, round(whole["buckets"][b], 2))
eq("B2 Σ(per-store total) == per-period total",
   round(sum(p["total"] for p in parts.values()), 2), round(whole["total"], 2))
lad_parts = {}
for p in parts.values():
    for k, v in p["leg_ladder"].items():
        lad_parts[legs.ladder_key(k)] = round(lad_parts.get(legs.ladder_key(k), 0.0) + v, 2)
lad_whole = {legs.ladder_key(k): round(v, 2) for k, v in whole["leg_ladder"].items()}
eq("B3 Σ(per-store rung ladder) == per-period rung ladder", lad_parts, lad_whole)
check("B4 the margins are in the unsplit bucket AND in the Unlabelled rung — two read-outs of the "
      "SAME columns, which is why the accumulator must not add both",
      round(whole["buckets"]["unsplit"], 2) == round(lad_whole[legs.LADDER_UNKNOWN], 2)
      and round(whole["buckets"]["unsplit"], 2) != 0.0,
      f"unsplit {whole['buckets']['unsplit']}, Unlabelled rung {lad_whole.get(legs.LADDER_UNKNOWN)}")
# The double count C guards against, reproduced here so the regression is a number and not a worry.
_double = round(whole["buckets"]["m1"] + whole["buckets"]["trailing"]
                + whole["buckets"]["unsplit"] + sum(lad_whole.values()), 2)
check("B5 REGRESSION: adding buckets AND rungs would double this money exactly",
      abs(_double - 2 * round(whole["total"], 2)) < 0.01,
      f"double {_double}, 2x total {2 * round(whole['total'], 2)}")

# ── C. the accumulator has one home, and the MA path opts out of the ladder ───────────────────────
print("\nC. one accumulator, reached by both grains")
RT = open(os.path.join(HERE, "app", "modules", "commcalc", "router.py"), encoding="utf-8").read()
START = RT.index("def _leg_blank_period(")
END = RT.index('@router.get("/commission-leg-labels")')
BODY = RT[START:END]
eq("C1 `_leg_accumulate` is defined exactly once", BODY.count("def _leg_accumulate("), 1)
check("C2 the trend compute never increments a bucket key outside the accumulator",
      not re.search(r"(out|target|comp_series)\[\w+\]\['(m1|m2_12|unsplit|total)'\]\s*=", BODY),
      "a bucket is assigned outside `_leg_accumulate` — second copy of the accumulation")
check("C3 both grains go through it: the company row AND a per-store cell",
      "_leg_accumulate(target," in BODY and "_leg_accumulate(_leg_store_cell(target," in BODY)
CALLS = accumulate_calls(code_only(BODY))
# The DECLARED roster of accumulate call sites, each named with the reason it is or is not
# ladder-bearing (§CORE-10: a derived subject set is compared with a declared one, never counted
# against a lower bound — a site that leaves must name itself).
ACCUMULATE_SITES = {
    "label/company: the ePay + comp rollup's company grain — its rung is in the label",
    "label/store: the same labelled dollar, one grain finer — same rung",
    "ma/company: the MA bucket, company grain — rung merged from leg_ladder instead (B5)",
    "ma/store: the same MA bucket, one grain finer — rung merged from leg_ladder instead (B5)",
}
eq("C4a every accumulate call site is accounted for by the declared roster",
   len(CALLS), len(ACCUMULATE_SITES))
ladderless = [c for c in CALLS if "with_ladder=False" in c]
laddered = [c for c in CALLS if "with_ladder=False" not in c]
check("C4 REGRESSION: both MA accumulate calls opt OUT of the ladder (B5's double count)",
      len(ladderless) == 2 and all("None, _amt, None" in c for c in ladderless),
      f"with_ladder=False at: {ladderless}")
check("C4b ... and both label calls keep the ladder, so a labelled rung is never lost",
      len(laddered) == 2 and all("leg," in c for c in laddered),
      f"ladder-bearing: {laddered}")
check("C5 the MA path merges the split's OWN leg_ladder instead of re-deriving rungs",
      "res.get('leg_ladder')" in BODY and "_commission_legs.ladder_key(_lk)" in BODY)
check("C6 the store AXIS is OFF by default, so /commission-leg-trend's payload shape and its "
      "unfiltered numbers are unchanged (its FILTERED numbers deliberately change: MA money that "
      "the filter used to EXCLUDE is now attributed \u2014 the class fix, stated not hidden)",
      "by_store=False" in BODY and 'def commission_leg_trend(' in BODY
      and "_commission_leg_trend_compute(sb(), org_id, period, months, market, store)" in BODY)
check("C7 the per-store endpoint CALLS that compute — it does not re-read",
      "_commission_leg_trend_compute(sb(), org_id, period, months, market, store, by_store=True)"
      in RT)
check("C8 both the store trend and the trend are org-scoped through require_org",
      BODY.count("require_org(org_id)") >= 1 and
      RT[RT.index('@router.get("/commission-leg-store-trend")'):].split("return")[0]
      .count("require_org(org_id)") == 1)

# ── D. a partial month is not a collapse ─────────────────────────────────────────────────────────
print("\nD. the unfinished month is marked and excluded from every delta")
eq("D1 only the current calendar month is partial", PARTIALS, ["October 2026"])
eq("D2 a window that ends before today has no partial month",
   pivot.partial_months(["June 2026", "July 2026"], NOW_Y, NOW_M), [])
eq("D3 a window reaching into the future marks those months too",
   pivot.partial_months(["September 2026", "October 2026", "November 2026"], NOW_Y, NOW_M),
   ["October 2026", "November 2026"])
eq("D4 the partial month is still REPORTED on the row (shown, not hidden)",
   ROW["100 North Ave"]["months"]["October 2026"]["total"], 1100.0)
eq("D5 it is flagged as partial", ROW["100 North Ave"]["months"]["October 2026"]["partial"], True)
eq("D6 the delta's last endpoint is the last COMPLETE month",
   ROW["100 North Ave"]["last"], "September 2026")
eq("D7 complete_total excludes it", ROW["100 North Ave"]["complete_total"],
   round(4000 + 3200 + 2400, 2))
# The regression, as the real numbers: NORTH fell 40% over three complete months. Reading the
# half-month as the endpoint would call it a 72.5% collapse.
eq("D8 REGRESSION: NORTH's decline is measured on complete months",
   ROW["100 North Ave"]["delta_pct"], -40.0)
check("D9 REGRESSION: the partial month as an endpoint would have read -72.5% instead",
      round((1100 - 4000) / 4000 * 100, 1) == -72.5)

# ── E. money with no store is its own row ────────────────────────────────────────────────────────
print("\nE. unplaceable money is named, never spread and never dropped")
check("E1 the unattributed row exists", pivot.UNATTRIBUTED_KEY in ROW)
eq("E2 it is flagged", ROW[pivot.UNATTRIBUTED_KEY]["unattributed"], True)
eq("E3 it is NAMED, not blank", ROW[pivot.UNATTRIBUTED_KEY]["store"], pivot.UNATTRIBUTED_LABEL)
eq("E4 it sorts last, after every real store", ROWS[-1]["store_key"], pivot.UNATTRIBUTED_KEY)
eq("E5 its money is intact, sign and all",
   ROW[pivot.UNATTRIBUTED_KEY]["complete_total"], round(250 - 40 - 300, 2))
check("E6 no real store absorbed any of it",
      all(r["months"][lab]["unsplit"] == 0.0 for r in ROWS if not r["unattributed"]
          for lab in LABELS))

# ── F. the rungs come from the data ──────────────────────────────────────────────────────────────
print("\nF. the rung columns are the data's, not a range()")
eq("F1 exactly the rungs the fixture carries, ordered, Unlabelled last",
   RUNGS, ["1", "2", "4", "5", legs.LADDER_UNKNOWN])
check("F2 M3 and M6 are NOT columns — nobody has money in them",
      "3" not in RUNGS and "6" not in RUNGS)
eq("F3 a store with no M4 reads $0, not a hole",
   ROW["200 South St"]["months"]["August 2026"]["ladder"].get("4", 0.0), 0.0)
_m13 = [{"period": "July 2026", "total": 10.0,
         "by_store": {"X": cell(0, 10, 0, {"13": 10})}}]
_r13, _k13 = pivot.pivot(["July 2026"], _m13, {}, [])
eq("F4 an M13 the feed carries is a column the day it arrives", _k13, ["13"])
eq("F5 and it is labelled M13, never folded into M12", legs.ladder_label("13"), "M13")
SRC = open(os.path.join(HERE, "app", "modules", "commcalc",
                        "commission_leg_store_trend.py"), encoding="utf-8").read()
CODE = code_only(SRC)
check("F6 the pivot module writes no month COUNT and no range over rungs",
      not re.search(r"range\(", CODE) and not re.search(r"max_month", CODE),
      "a month count in the pivot would make a rung the feed carries invisible")
check("F7 it spells no rung key, label or column itself — all from commission_legs",
      not re.search(r"['\"]unknown['\"]", CODE) and not re.search(r'["\']M["\']', CODE)
      and "ladder_label(" in CODE and "ladder_column(" in CODE and "months_present(" in CODE)

# ── G. a decline is named by rung ────────────────────────────────────────────────────────────────
print("\nG. which rung ran down — the question the report exists to answer")
eq("G1 NORTH's hardest-falling rung is M4 (cohort lost months ago)",
   ROW["100 North Ave"]["worst_rung"]["label"], "M4")
eq("G2 ... and its M1 did not move at all", ROW["100 North Ave"]["m1_delta"], 0.0)
eq("G3 ... so the decline is entirely in the trailing legs",
   ROW["100 North Ave"]["m2_12_delta"], -1600.0)
eq("G4 SOUTH's hardest-falling rung is M1 (shrinking now)",
   ROW["200 South St"]["worst_rung"]["label"], "M1")
eq("G5 ... and its trailing legs are flat", ROW["200 South St"]["m2_12_delta"], 0.0)
check("G6 the two stores are therefore distinguishable by rung alone",
      ROW["100 North Ave"]["worst_rung"]["key"] != ROW["200 South St"]["worst_rung"]["key"])
eq("G7 the rung read-out carries its own from/to, so it can be shown as a figure",
   (ROW["100 North Ave"]["worst_rung"]["from"], ROW["100 North Ave"]["worst_rung"]["to"]),
   (1600.0, 500.0))

# ── H. arithmetic that would lie is refused ──────────────────────────────────────────────────────
print("\nH. no invented trend, no flipped sign")
eq("H1 a store the window sees in ONE complete month gets delta None",
   (ROW["300 East Rd"]["last"], ROW["300 East Rd"]["delta"]), (None, None))
eq("H2 ... and its money is still reported", ROW["300 East Rd"]["complete_total"], 500.0)
eq("H3 a $0 baseline yields pct None — never 0, never infinity",
   pivot._delta({"x": 0.0}, {"x": 500.0}, "x"), (500.0, None))
eq("H4 a negative rung keeps its sign; a decline is never flipped to look better",
   ROW[pivot.UNATTRIBUTED_KEY]["months"]["September 2026"]["ladder"][legs.LADDER_UNKNOWN], -300.0)
eq("H5 the unattributed row's own decline is reported as a decline",
   ROW[pivot.UNATTRIBUTED_KEY]["delta"], -550.0)
check("H6 a store with no decline anywhere has worst_rung None, not a fabricated rung",
      pivot.pivot(["July 2026", "August 2026"],
                  [{"period": "July 2026", "by_store": {"Z": cell(100, 0, 0, {"1": 100})}},
                   {"period": "August 2026", "by_store": {"Z": cell(200, 0, 0, {"1": 200})}}],
                  {}, [])[0][0]["worst_rung"] is None)

# ── I. a gap is reported, not smoothed ───────────────────────────────────────────────────────────
print("\nI. money the store axis cannot place is reported as a gap")
_gap_series = [{"period": "July 2026", "total": 500.0,
                "by_store": {"A": cell(100, 0, 0, {"1": 100})}}]
_gr, _gk = pivot.pivot(["July 2026"], _gap_series, {}, [])
_gt = pivot.totals_row(["July 2026"], _gr, _gk, [])
_gties = pivot.ties_to(["July 2026"], _gt, _gap_series)
eq("I1 the month is flagged not-ok", _gties["July 2026"]["ok"], False)
eq("I2 the gap is stated as a number", _gties["July 2026"]["gap"], -400.0)
eq("I3 ... and both sides are shown",
   (_gties["July 2026"]["shown"], _gties["July 2026"]["company"]), (100.0, 500.0))

# ── J. RULE TWO ──────────────────────────────────────────────────────────────────────────────────
print("\nJ. RULE TWO — config, never code")
VOCAB = ("boost", "vidapay", "epay", "luxelink", "luxlink", "likewize", "t-mobile", "metro",
         "springfield", "philadelphia")
hits = [w for w in VOCAB if re.search(r"\b" + re.escape(w) + r"\b", CODE.lower())]
# The module's docstring names the FEEDS it pivots, which is documentation of provenance, not a
# branch. What must not exist is a carrier/tenant name in a conditional.
branchy = [w for w in hits if re.search(r"(if|elif|==|!=|in)\s*[^\n]{0,40}"
                                        + re.escape(w), CODE.lower())]
eq("J1 no carrier or tenant name appears in a branch in the pivot module", branchy, [])
check("J2 the pivot module reaches no DB, no client and no clock",
      not re.search(r"\b(sb\(|client|supabase|requests|datetime|now\()", CODE),
      "a pure pivot that reads a clock cannot be tested against a date")
eq("J3 its only app import is the rung one-home",
   sorted(set(re.findall(r"from app\.[\w.]+ import (\w+)", CODE))), ["commission_legs"])

print()
if FAILS:
    print("FAILED (%d): %s" % (len(FAILS), "; ".join(FAILS)))
    sys.exit(1)
print("OK — the store axis is a shape of the leg trend, to the cent, and says what it cannot place.")
