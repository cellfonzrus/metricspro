"""Parse a Paramount Wireless 'MTD Sales Performance' report (HTML email body) into per-store KPI
values. Keyed by Door TSP (= store_code).

Delivery is HTML in the email body (owner 2026-08-15). The report has several sections (A..D), each an
HTML <table> whose first column is 'Door TSP'. Every column named in QUALIFIER_COLUMNS is read; the
Management-Incentive gates consume `zulu`, `tmr3` and `twp`, and the rest are KPI display values the
owner asked for by name (2026-09-25 "all of them").

WHICH COLUMN IS WHICH IS A MONEY DECISION, so it lives in `resolve_columns` — a pure function over a
header row, with no HTML parser behind it, provable by a stdlib-only harness
(backend/harness_paramount_kpi_lock.py). `parse_paramount_mtd_kpis` only walks the document.

Component counts (Edge / FWA=VHI-FIOS / TWP units) intentionally STAY on the raw_sales rep-pay basis
(owner decision 2026-08-15). Ingesting them here feeds KPI display and the QUALIFIER gate only; it
does NOT move the pay basis.
"""
import re
from html import unescape

# (metric_key, [EXACT headers, in priority order]). Matched on a key with ALL whitespace removed, so
# "Current TWP ALL %" and "Current TWP ALL%" are the same header, but "Current TWP+%" is NOT.
#
# WHY EXACT, NOT SUBSTRING (owner 2026-09-25, and a live defect this fixes). The old table matched the
# SUBSTRING "current twp" and took the FIRST header containing it. The real report carries TWO columns
# that both contain it — `Current TWP+%` and `Current TWP ALL%` — with TWP+ first, and they are
# different numbers (door 168872: 50.0% vs 67.0%). So the Management-Incentive qualifier was reading
# TWP+ while the owner's rule is TWP ALL, silently and with no error. A substring match on a report
# that grows a similarly-named column is a gate that changes meaning on its own; every header here is
# now exact, and an unrecognised one yields NO value (the gate reports the qualifier as pending)
# rather than the wrong one. Missing beats wrong on a money gate.
#
# The full column set is the owner's (2026-09-25 "all of them"). Ingesting a component count here
# feeds KPI display and the QUALIFIER gate only; it does NOT change the pay basis, which stays on
# raw_sales per the 2026-08-15 decision recorded in this module's docstring.
QUALIFIER_COLUMNS = [
    # Fallbacks are EXACT spellings too, never a looser match. They are safe for these two because
    # neither has a twin in the report: nothing else is called "Zulu" or "3MR". `twp` gets NO bare
    # fallback on purpose — "TWP%" alone is the ambiguity this table exists to refuse.
    ("zulu",         ["currentzulu%", "currentzulu", "zulu%", "zulu"]),
    ("tmr3",         ["finalized3mr%", "finalized3mr", "3mr%", "3mr"]),
    ("twp",          ["currenttwpall%", "currenttwpall"]),   # owner 2026-09-25: the qualifier is TWP ALL, not TWP+
    ("twp_plus",     ["currenttwp+%", "currenttwp+"]),       # its own metric, so the two can never be mixed
    ("acts",         ["currentacts"]),
    ("pacing_acts",  ["pacingacts"]),
    ("quota",        ["currentquota"]),
    ("pacing_quota", ["pacing%toquota"]),
    ("tablets",      ["currenttablets"]),
    ("fwa_acts",     ["currentfwaacts"]),
    ("upgrades",     ["currentupgrades"]),
    ("edge_apply",   ["currentedgeapply"]),
    ("edge_approve", ["currentedgeapprove"]),
    ("edge_acts",    ["currentedgeacts"]),
    ("autopay_ta",   ["currentautopayta%"]),
    ("autopay_all",  ["currentautopayall%"]),
]


def _hkey(s):
    """Header → comparison key: normalised, then ALL whitespace removed. Tolerates the spacing the
    export varies ("TWP ALL %" vs "TWP ALL%") while keeping ALL and + distinguishable."""
    return re.sub(r"\s+", "", _norm(s))

_DOOR_RE = re.compile(r"^\d{5,7}$")
_ONTRACK = ("", "-", "—", "n/a", "na", "ontrack", "✔ontrack", "✔ ontrack", "✔")


def _norm(s):
    return re.sub(r"\s+", " ", unescape(str(s or ""))).strip().lower()


def _num(s):
    """'85.1%' -> 85.1 · '9' -> 9.0 · blank / '✔ On Track' / '—' -> None."""
    raw = _norm(s)
    if raw in _ONTRACK:
        return None
    t = re.sub(r"[%,\s✔]", "", raw)
    if t in ("", "-", "—"):
        return None
    try:
        return float(t)
    except ValueError:
        return None


def resolve_columns(header):
    """A header row (list of cell texts) -> {metric_key: column index}. THE money decision, kept pure.

    Exact match on the whitespace-stripped key, in the priority order QUALIFIER_COLUMNS declares. A
    header this table does not recognise resolves to NOTHING — the metric is simply absent, and the
    gate reports it pending — rather than to a neighbouring column that merely looks similar.
    """
    hkeys = [_hkey(h) for h in (header or [])]
    col_for = {}
    for mk, wanted in QUALIFIER_COLUMNS:
        for w in wanted:
            idx = next((i for i, h in enumerate(hkeys) if h == w), None)
            if idx is not None:
                col_for[mk] = idx
                break
    return col_for


def door_column(header):
    """Index of the 'Door TSP' key column. Substring is right HERE and nowhere else: the key column is
    spelled 'Door TSP' / 'Door' / 'TSP' across sections, it carries no value, and picking the wrong one
    yields no store code at all (the row is skipped) rather than a wrong number."""
    return next((i for i, h in enumerate(header or []) if "door" in h or "tsp" in h), 0)


def parse_paramount_mtd_kpis(html):
    """HTML string -> {store_code: {metric_key: value}}. Robust to missing sections, extra columns, and
    the Pacing-vs-Finalized / Current-vs-Prior / TWP+-vs-TWP-ALL twin columns. Column choice is
    `resolve_columns`; this function only walks the document."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html or "", "html.parser")
    out = {}
    for table in soup.find_all("table"):
        trs = table.find_all("tr")
        if not trs:
            continue
        header = [_norm(c.get_text()) for c in trs[0].find_all(["th", "td"])]
        if not header:
            continue
        door_idx = door_column(header)
        col_for = resolve_columns(header)
        if not col_for:
            continue
        for tr in trs[1:]:
            cells = [_norm(c.get_text()) for c in tr.find_all(["td", "th"])]
            if len(cells) <= door_idx:
                continue
            code = re.sub(r"\D", "", cells[door_idx])
            if not _DOOR_RE.match(code):
                continue
            rec = out.setdefault(code, {})
            for mk, idx in col_for.items():
                if idx < len(cells):
                    v = _num(cells[idx])
                    if v is not None:
                        rec[mk] = v
    return out


# ── self-test (run: python -m app.modules.commcalc.paramount_kpi) ─────────────────────────────────
_SAMPLE = """
<h2>PARAMOUNT WIRELESS MTD</h2>
<h3>Section A: Current Sales Performance</h3>
<table>
 <tr><th>Door TSP</th><th>Address</th><th>City</th><th>Current Acts</th><th>Current Quota</th>
     <th>Current TWP+%</th><th>Current TWP ALL%</th><th>Current FWA Acts</th><th>Current Edge Apply</th>
     <th>Current Autopay TA%</th><th>Current Autopay all%</th></tr>
 <tr><td>168874</td><td>957 Pennsylvania Ave</td><td>Brooklyn</td><td>32</td><td>95</td>
     <td>48.0%</td><td>67.0%</td><td>0</td><td>1</td><td>0.0%</td><td>30.0%</td></tr>
 <tr><td>168876</td><td>21880 Hempstead Ave</td><td>Queens Village</td><td>26</td><td>76</td>
     <td>90.9%</td><td>12.5%</td><td>1</td><td>0</td><td>4.0%</td><td>0.0%</td></tr>
</table>
<h3>Section B: Quality Metrics</h3>
<table>
 <tr><th>Door TSP</th><th>Address</th><th>City</th><th>Pacing 3MR%</th><th>Finalized 3MR%</th><th>Finalized 4MR%</th></tr>
 <tr><td>168874</td><td>957 Pennsylvania Ave</td><td>Brooklyn</td><td>79.4%</td><td>85.1%</td><td>81.2%</td></tr>
 <tr><td>168876</td><td>21880 Hempstead Ave</td><td>Queens Village</td><td>79.6%</td><td>89.4%</td><td>90.4%</td></tr>
</table>
<h3>Section D: Misc</h3>
<table>
 <tr><th>Door TSP</th><th>Address</th><th>City</th><th>Current Edge Acts</th><th>Inventory Tier</th><th>Current Zulu%</th></tr>
 <tr><td>168874</td><td>957 Pennsylvania Ave</td><td>Brooklyn</td><td>1</td><td>9</td><td>11.0%</td></tr>
 <tr><td>168876</td><td>21880 Hempstead Ave</td><td>Queens Village</td><td>0</td><td>1</td><td>8.0%</td></tr>
</table>
"""

if __name__ == "__main__":
    got = parse_paramount_mtd_kpis(_SAMPLE)
    a, b = got.get("168874"), got.get("168876")

    # ── THE REGRESSION (owner 2026-09-25). Section A carries BOTH `Current TWP+%` and
    # `Current TWP ALL%`, TWP+ first. Under the old substring match on "current twp", `twp` took the
    # FIRST of them — TWP+ — so the incentive gate qualified on the wrong number with no error.
    # These two lines fail if that ever comes back.
    assert a["twp"] == 67.0, f"twp must be TWP ALL%, got {a['twp']}"
    assert a["twp_plus"] == 48.0, f"twp_plus must be TWP+%, got {a['twp_plus']}"
    assert b["twp"] == 12.5 and b["twp_plus"] == 90.9, b
    # ...and they are never the same column: TWP+ > TWP ALL on one door and the reverse on the other,
    # so a parser that collapsed them could not pass both rows.
    assert a["twp"] > a["twp_plus"] and b["twp"] < b["twp_plus"], (a, b)

    # 'Finalized 3MR%' beats 'Pacing 3MR%'; Section D supplies zulu.
    assert a["tmr3"] == 85.1 and a["zulu"] == 11.0, a
    assert b["tmr3"] == 89.4 and b["zulu"] == 8.0, b

    # The wider set the owner asked for ("all of them"), read from the same row.
    assert a["acts"] == 32.0 and a["quota"] == 95.0, a
    assert a["fwa_acts"] == 0.0 and a["edge_apply"] == 1.0, a
    assert a["autopay_ta"] == 0.0 and a["autopay_all"] == 30.0, a
    assert b["autopay_ta"] == 4.0 and b["autopay_all"] == 0.0, b

    # An UNRECOGNISED header yields NO value rather than a wrong one — missing beats wrong on a gate.
    odd = parse_paramount_mtd_kpis(
        '<table><tr><th>Door TSP</th><th>Current TWP SOMETHING%</th></tr>'
        '<tr><td>168874</td><td>99.0%</td></tr></table>')
    assert "twp" not in odd.get("168874", {}), odd

    assert len(got) == 2, got
    print("paramount_kpi self-test OK:", got)
