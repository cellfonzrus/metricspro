"""Parse a Paramount Wireless 'MTD Sales Performance' report (HTML email body) into per-store KPI
values for the Management-Incentive qualifier gates. Keyed by Door TSP (= store_code).

Delivery is HTML in the email body (owner 2026-08-15). The report has several sections (A..D), each an
HTML <table> whose first column is 'Door TSP'. We pull ONLY the qualifier metrics the incentive gates on:
  zulu  <- Section D  'Current Zulu%'
  tmr3  <- Section B  'Finalized 3MR%'   ('finalized' preferred over 'pacing')
  twp   <- Section A  'Current TWP+%'
Component counts (Edge / FWA=VHI-FIOS / TWP units) intentionally STAY on the raw_sales rep-pay basis
(owner decision 2026-08-15) — this parser feeds only the qualifier values, never the paid counts.
"""
import re
from html import unescape

# (metric_key, [header substrings tried in priority order]). 'current' variants beat 'prior'/'pacing'.
QUALIFIER_COLUMNS = [
    ("zulu", ["current zulu", "zulu"]),
    ("tmr3", ["finalized 3mr", "3mr"]),
    ("twp",  ["current twp", "twp"]),
]

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


def parse_paramount_mtd_kpis(html):
    """HTML string -> {store_code: {metric_key: value}} for zulu/tmr3/twp. Robust to missing sections,
    extra columns, and the Pacing-vs-Finalized / Current-vs-Prior twin columns."""
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
        door_idx = next((i for i, h in enumerate(header) if "door" in h or "tsp" in h), 0)
        col_for = {}
        for mk, subs in QUALIFIER_COLUMNS:
            for sub in subs:
                idx = next((i for i, h in enumerate(header) if sub in h), None)
                if idx is not None:
                    col_for[mk] = idx
                    break
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
     <th>Current TWP+%</th><th>Current FWA Acts</th><th>Current Edge Apply</th></tr>
 <tr><td>168874</td><td>957 Pennsylvania Ave</td><td>Brooklyn</td><td>32</td><td>95</td>
     <td>48.0%</td><td>0</td><td>1</td></tr>
 <tr><td>168876</td><td>21880 Hempstead Ave</td><td>Queens Village</td><td>26</td><td>76</td>
     <td>90.9%</td><td>1</td><td>0</td></tr>
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
    assert got.get("168874") == {"twp": 48.0, "tmr3": 85.1, "zulu": 11.0}, got.get("168874")
    assert got.get("168876") == {"twp": 90.9, "tmr3": 89.4, "zulu": 8.0}, got.get("168876")
    # 'Finalized 3MR%' beat 'Pacing 3MR%'; 'Current TWP+%' not confused by other % columns.
    assert len(got) == 2, got
    print("paramount_kpi self-test OK:", got)
