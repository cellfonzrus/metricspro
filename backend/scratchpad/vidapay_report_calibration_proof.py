"""Proof for `agent/commission/vidapay-report-calibration`.

OWNER BUG REPORT 2026-07-28 (verbatim): "need to fixx the report pulling from vida pay", pasted with
the live "🔧 What the pull saw" diagnostic. Two defects live in that diagnostic:

DEFECT 1 — THE SELF-CONTRADICTING OPTION MATCH. Two of five reports failed with
    "'Activation SIM Assignment Report' is not one of the reports this portal login offers
     — it offers: -- SELECT --, Activation SIM Assignment Report, MA - Commission Details, ..."
The wanted name is printed INSIDE the offered list. Root cause (vidapay_sweep.py, pristine
`_select_report`):
        t = (o.inner_text() or "").strip().lower()
        if t == target or (target and (target in t or t in target)) and t:
  * `.strip().lower()` is the WHOLE normalisation. An INTERNAL no-break space (U+00A0), zero-width
    space, soft hyphen or en/em dash inside the portal's own option text renders EXACTLY like a plain
    space/hyphen — so the printed list looks identical to the configured name while the bytes differ.
    (`.strip()` removes a leading/trailing nbsp; nothing removed an internal one.)
  * the list the failure PRINTED was captured ONCE, before any report ran
    (`_pull_all_reports_on_page`: `options = report_options(page)`), while the comparison ran against
    the LIVE dropdown at attempt time. A stale capture next to a live failure reads as a
    contradiction even when both are individually true.
  Neither side was ever shown in repr(), so an invisible mismatch was undiagnosable from the UI.

DEFECT 2 — "RAN, RETURNED NO ROWS" ON REPORTS THAT NEVER PRODUCED ANYTHING. Three reports reported
0 rows for a 2-month window while the same data demonstrably arrives by mailbox. Root causes:
  (a) `_fill_param_fields` typed dates with `el.click(); el.fill(""); el.type(val)` — Playwright's
      `type()` never dispatches `change`, and `change` on a text input otherwise fires on blur, which
      never happened. An ASP.NET/jQuery datepicker commits on `change`, so the portal ran its DEFAULT
      range instead of the requested month.
  (b) start/end COLLIDED: `_find_input(want=toks)` matches ANY token, so "End Date" (end, date)
      matched the *StartDate* box first in DOM order — both boundaries into one field, window
      collapsed to a day.
  (b2) hidden params were unreachable: `kinds=(...,"hidden")` but the finder required `is_visible()`,
      so a hidden SessionId was NEVER filled.
  (c) the Run/Submit click used `_click_submit`, whose selector is
      `button, input[type=submit], input[type=button], a[role=button]` — it cannot click an ASP.NET
      LinkButton `<a href="javascript:__doPostBack(...)">Submit</a>`. THE EXACT GAP fixed for the
      Reports NAVIGATION on 2026-07-27, still present on the run button. A report that is never
      submitted is indistinguishable from one that returned nothing.
  (d) THE PRIME SUSPECT — the results were read ONCE, after `networkidle` (which resolves immediately
      when the page is already idle) plus a flat 2s. A jqx/ASP.NET grid populates ASYNCHRONOUSLY;
      reading it too early yields an empty grid indistinguishable from a true empty result — and
      `_pull_one_report` returned ok:True regardless, so it aggregated into
      "the reports ran but returned nothing for the last 2 month(s)".

PURE — no real portal, no real Chromium, no DB, no credentials, NO NETWORK. Fake page/frame/element
objects implement only the Playwright surface the driver touches. Run:
    cd backend && python3 scratchpad/vidapay_report_calibration_proof.py
"""
import json
import os
import sys
import tempfile
import types

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.modules.commcalc import vidapay_sweep as vp        # noqa: E402
from app.modules.commcalc import report_pull as rp          # noqa: E402

PASS, FAIL, LINES = 0, 0, []

# ── virtual clock ───────────────────────────────────────────────────────────────────────────────
# The driver's waits are wall-clock bounded. A fake page whose wait_for_timeout returns instantly
# would let a "90s budget" elapse in microseconds and prove nothing about the bound. Every fake wait
# ADVANCES this clock instead, so the bounded waits behave exactly as they will in production while
# the proof still runs in milliseconds.
class _Clock:
    def __init__(self):
        self.t = 100000.0

    def advance(self, sec):
        self.t += float(sec)


CLK = _Clock()
_time_mod = __import__("time")
_time_mod.time = lambda: CLK.t


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        LINES.append("  ok   " + name)
    else:
        FAIL += 1
        LINES.append("  FAIL " + name + ((" :: " + str(extra)[:200]) if extra else ""))


# ════════════════════════════════════════════════════════════════════════════════════════════════
# Fake Playwright surface
# ════════════════════════════════════════════════════════════════════════════════════════════════
NBSP, ZWSP, ENDASH, SHY = " ", "​", "–", "­"


class El:
    def __init__(self, tag, text="", value="", attrs=None, visible=True, on_click=None):
        self.tag = tag
        self.text = text
        self.value = value
        self.attrs = dict(attrs or {})
        self.visible = visible
        self.on_click = on_click
        self.clicks = 0
        self.events = []
        self.typed = ""
        self._options = []

    def is_visible(self):
        return self.visible

    def inner_text(self):
        return self.text

    def get_attribute(self, k):
        if k == "value":
            return self.value or None
        return self.attrs.get(k)

    def click(self, *a, **k):
        self.clicks += 1
        if self.on_click:
            self.on_click()

    def fill(self, v):
        if not self.visible:
            raise RuntimeError("element is not visible — Playwright refuses to fill it")
        self.value = v
        self.events += ["input", "change"]        # what Playwright's fill() really dispatches

    def type(self, v, delay=None):
        if not self.visible:
            raise RuntimeError("not visible")
        self.value = (self.value or "") + v
        self.events += ["keydown", "input", "keyup"]     # NOTE: no 'change' — the (a) root cause

    def input_value(self):
        return self.value or ""

    def evaluate(self, js, arg=None):
        j = str(js)
        if "e.value=v" in j.replace(" ", ""):
            self.value = arg
            return None
        if "dispatchEvent" in j:
            self.events += ["input", "change", "keyup", "blur"]
            return None
        if "e => e.value" in j or "e=>e.value" in j:
            return self.value
        return None

    # <select>
    def query_selector_all(self, sel):
        if "option" in sel:
            return list(self._options)
        return []

    def select_option(self, value=None, label=None):
        self.value = value if value is not None else label
        self.events.append("change")


class Opt(El):
    def __init__(self, text, value=None):
        super().__init__("option", text=text, attrs={"value": value if value is not None else text})


def mk_select(name, options):
    s = El("select", attrs={"name": name, "id": name})
    s._options = [Opt(o) for o in options]
    return s


class Frame:
    """A LIVE frame: `elements` and `body_text` are read from the page on every access, exactly like a
    real Playwright Frame re-queries the DOM. A snapshot-holding fake would hide the very bug this
    proof is about (a dropdown that vanishes mid-pull while an old handle still 'sees' it)."""

    def __init__(self, page, elements=None, body_text=None):
        self.page = page
        self._fixed = elements
        self._fixed_text = body_text

    @property
    def elements(self):
        return list(self._fixed if self._fixed is not None else self.page.current_elements())

    @property
    def body_text(self):
        return (self._fixed_text if self._fixed_text is not None else self.page.body_text())

    def query_selector_all(self, sel):
        """A real (tiny) CSS matcher: `tag[attr=value]` parts, comma separated."""
        out = []
        for part in [p.strip().lower() for p in str(sel).split(",") if p.strip()]:
            if "[" in part:
                tag, pred = part.split("[", 1)
                key, _, want = pred.rstrip("]").partition("=")
            else:
                tag, key, want = part, None, None
            for e in self.elements:
                if e.tag != tag:
                    continue
                if key:
                    have = e.get_attribute(key)
                    if have is None or (want and str(have).lower() != want.strip("\"'")):
                        continue
                out.append(e)
        seen, uniq = set(), []
        for e in out:
            if id(e) not in seen:
                seen.add(id(e))
                uniq.append(e)
        return uniq

    def query_selector(self, sel):
        got = self.query_selector_all(sel)
        return got[0] if got else None

    def evaluate(self, js, *a):
        j = str(js)
        if "querySelectorAll('table tr')" in j:
            return self.page.grid_rows()
        if "document.body.innerText" in j:
            return self.body_text
        if "h1,h2,h3" in j:
            return ["Reports"]
        if "input,button,select" in j:
            return [{"tag": e.tag, "type": e.attrs.get("type", ""), "name": e.attrs.get("name", ""),
                     "id": e.attrs.get("id", ""), "ph": "", "val": e.text, "vis": e.visible}
                    for e in self.elements]
        if "=> ({" in j:
            return {}
        return []


class DL:
    def __init__(self, content, name):
        fd, p = tempfile.mkstemp(suffix="_" + name)
        with os.fdopen(fd, "wb") as fh:
            fh.write(content)
        self._p = p
        self.suggested_filename = name

    def path(self):
        return self._p


class _DLCtx:
    def __init__(self, page):
        self.page = page

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    @property
    def value(self):
        if self.page.download_content is None:
            raise RuntimeError("no download started")
        return DL(self.page.download_content, "export.csv")


class Portal:
    """A VidaPay-shaped Reports page.

    after_submit ∈ export_now | grid_late | empty_marker | never | grid_no_export | generic_export
    grid_delay   = how many wait ticks before the async grid/export appears
    submit_kind  ∈ button | anchor | none
    """

    def __init__(self, option_names, after_submit="export_now", grid_delay=0, submit_kind="button",
                 hidden_session=True, extra=(), drop_select_after=None, layout_rows=0,
                 export_always=False):
        self.url = "https://portal/Reports.aspx"
        self.after_submit = after_submit
        self.grid_delay = grid_delay
        self.drop_select_after = drop_select_after
        self.layout_rows = layout_rows          # classic ASP.NET chrome: <table>s everywhere
        self.export_always = export_always      # portal that shows "Export to: CSV" on the empty form
        self.submitted_at = None
        self.ticks = 0
        self.navigations = 0
        self.submits = 0
        self.download_content = b"Date,Time,IMEI\n07/01/2026,10:00,111\n07/02/2026,11:00,222\n"
        self.sel = mk_select("ddlReport", option_names)
        self.start = El("input", attrs={"name": "txtStartDate", "id": "txtStartDate", "type": "text"})
        self.end = El("input", attrs={"name": "txtEndDate", "id": "txtEndDate", "type": "text"})
        self.session = El("input", attrs={"name": "SessionId", "id": "SessionId", "type": "hidden"},
                          visible=not hidden_session)
        self.logout = El("button", text="Logout")
        self.submit = None
        if submit_kind == "button":
            self.submit = El("button", text="Submit", on_click=self._on_submit)
        elif submit_kind == "anchor":
            self.submit = El("a", text="Submit",
                             attrs={"href": "javascript:__doPostBack('ctl00$Submit','')"},
                             on_click=self._on_submit)
        self.export_csv = El("a", text="Export to: CSV", attrs={"href": "Export.aspx?fmt=csv"})
        self.export_generic = El("a", text="Export", attrs={"href": "Export.aspx"})
        self.extra = list(extra)

    def _on_submit(self):
        self.submits += 1
        self.submitted_at = self.ticks

    # ── state ──
    def _ready(self):
        return (self.submitted_at is not None
                and (self.ticks - self.submitted_at) >= self.grid_delay)

    def grid_rows(self):
        base = self.layout_rows
        if not self._ready():
            return base
        if self.after_submit in ("grid_late", "grid_no_export"):
            return base + 8
        if self.after_submit == "export_now":
            return base + 5
        return base

    def body_text(self):
        if self._ready() and self.after_submit == "empty_marker":
            return "Report results\nNo records found for the selected criteria."
        if self.grid_rows():
            return "Report results\n8 rows"
        return "Reports"

    def current_elements(self):
        els = [self.start, self.end, self.session, self.logout] + self.extra
        if not (self.drop_select_after is not None and self.submits >= self.drop_select_after):
            els.insert(0, self.sel)      # else: the results page replaced the report form
        if self.submit is not None:
            els.append(self.submit)
        if self.export_always or self._ready():
            if self.export_always or self.after_submit in ("export_now", "grid_late"):
                els.append(self.export_csv)
            elif self.after_submit == "generic_export":
                els.append(self.export_generic)
        return els

    @property
    def frames(self):
        return [Frame(self)]

    # ── Playwright page surface ──
    def title(self):
        return "VidaPay CRM — Reports"

    def content(self):
        return "<html>reports</html>"

    def wait_for_load_state(self, *a, **k):
        self.ticks += 1
        CLK.advance(0.05)

    def wait_for_timeout(self, ms=1500, *a, **k):
        self.ticks += 1
        CLK.advance((ms or 1500) / 1000.0)

    def goto(self, *a, **k):
        self.navigations += 1

    def reload(self, *a, **k):
        self.navigations += 1

    def query_selector_all(self, sel):
        return self.frames[0].query_selector_all(sel)

    def query_selector(self, sel):
        return self.frames[0].query_selector(sel)

    def evaluate(self, js, *a):
        return self.frames[0].evaluate(js, *a)

    def screenshot(self, **k):
        return b"jpeg"

    def expect_download(self, timeout=None):
        return _DLCtx(self)

    @property
    def viewport_size(self):
        return {"width": 1366, "height": 900}


class FakeSB:
    def __init__(self):
        self.inserted = []

    def schema(self, s):
        return self

    def table(self, t):
        self._t = t
        return self

    def select(self, *a, **k):
        self._op = "select"
        return self

    def insert(self, rows):
        self.inserted += list(rows)
        return self

    def delete(self):
        return self

    def eq(self, *a):
        return self

    def gte(self, *a):
        return self

    def lte(self, *a):
        return self

    def in_(self, *a):
        return self

    def limit(self, n):
        return self

    def order(self, *a, **k):
        return self

    def execute(self):
        return types.SimpleNamespace(data=[])


def with_specs(specs, fn):
    saved = rp.resolve_report_specs
    rp.resolve_report_specs = lambda *a, **k: [dict(s) for s in specs]
    try:
        return fn()
    finally:
        rp.resolve_report_specs = saved


def pull(page, specs, months_back=1):
    return with_specs(specs, lambda: vp._pull_all_reports_on_page(
        page, FakeSB(), "orgA", "src1", "car1", months_back, {"account_id": "169024"}))


# The REAL vocabulary the owner's portal printed on 2026-07-28, in the portal's own order.
OWNER_OPTIONS = ["-- SELECT --", "Activation SIM Assignment Report", "MA - Commission Details",
                 "MA - Marketplace Handset Fulfillment Orders", "MA Daily Tx SubMA",
                 "PR Activation Details"]
# …the SAME list as the portal really serves it: invisible characters the operator cannot see.
OWNER_OPTIONS_INVISIBLE = [
    "-- SELECT --",
    "Activation SIM" + NBSP + "Assignment Report",          # internal nbsp  → old code failed
    "MA " + ENDASH + " Commission Details",                  # en dash        → old code failed
    "MA - Marketplace Handset  Fulfillment Orders",          # doubled space  → old code failed
    "MA Daily Tx SubMA" + ZWSP,                              # trailing ZWSP
    "PR" + SHY + " Activation Details",                      # soft hyphen
]

SPEC_ONE = {"report_key": "ma_commission", "display_name": "MA - Commission Details",
            "target_table": "raw_ma_commission", "export_pref": "csv",
            "column_map": {"Date": {"col": "tx_date", "type": "date"}, "IMEI": "imei"},
            "param_spec": {"date_col": "tx_date", "period_from": "tx_date", "has_period": True,
                           "iterate_months": True, "interval_months": 1, "max_months_back": 12,
                           "results_wait_s": 15, "submit_timeout_s": 300,
                           "fields": [
                               {"name": "Start Date", "kind": "date", "role": "start",
                                "format": "%m/%d/%Y"},
                               {"name": "End Date", "kind": "date", "role": "end",
                                "format": "%m/%d/%Y"},
                               {"name": "SessionId", "kind": "static", "source": "session_id"}]}}


print("[1] DEFECT 1 — the option match is now invisible-character-proof (PURE matcher)")
check("nbsp INSIDE the name matched (the owner's failing report #1)",
      vp.match_report_option("Activation SIM Assignment Report", OWNER_OPTIONS_INVISIBLE)
      == (1, "normalized"))
check("en dash vs hyphen matched",
      vp.match_report_option("MA - Commission Details", OWNER_OPTIONS_INVISIBLE) == (2, "normalized"))
check("doubled internal whitespace matched",
      vp.match_report_option("MA - Marketplace Handset Fulfillment Orders", OWNER_OPTIONS_INVISIBLE)
      == (3, "normalized"))
check("trailing zero-width space matched",
      vp.match_report_option("MA Daily Tx SubMA", OWNER_OPTIONS_INVISIBLE) == (4, "normalized"))
check("soft hyphen matched (the owner's failing report #2)",
      vp.match_report_option("PR Activation Details", OWNER_OPTIONS_INVISIBLE)[1]
      in ("normalized", "punctuation"))
check("a clean list still matches EXACT-first (no fuzzy tier is entered needlessly)",
      vp.match_report_option("MA Daily Tx SubMA", OWNER_OPTIONS) == (4, "exact"))
check("the '-- SELECT --' placeholder can never be selected",
      vp.match_report_option("select", OWNER_OPTIONS)[0] == -1)
check("a TRULY missing name still FAILS (the matcher did not just become permissive)",
      vp.match_report_option("Zzz Commission Export", OWNER_OPTIONS) == (-1, "not_listed"))
check("an ambiguous containment REFUSES rather than guessing a money report",
      vp.match_report_option("MA - Commission Details Extended",
                             ["MA - Commission Details Extended Summary",
                              "MA - Commission Details Extended Detail"])[0] == -1)
check("case + separator drift still matches (punctuation tier)",
      vp.match_report_option("ma_commission_details", ["MA - Commission Details"])
      == (0, "punctuation"))
check("CONFIG aliases are honoured (param_spec.name_aliases — no code change per tenant)",
      vp.match_report_option("Commission Detail", OWNER_OPTIONS,
                             aliases=["MA - Commission Details"]) == (2, "exact"))
check("_spec_aliases reads them off the spec, absent key ⇒ ()",
      vp._spec_aliases({"param_spec": {"name_aliases": ["A", "B"]}}) == ["A", "B"]
      and vp._spec_aliases({"param_spec": {}}) == [])
f = vp.label_forensics("Activation SIM" + NBSP + "Assignment Report")
check("label_forensics spells the invisible character out by NAME",
      "\\xa0" in f["repr"] and any("NO-BREAK SPACE" in c for c in f["odd_chars"]))

print("[2] the failure message can no longer contradict itself")
p = Portal(OWNER_OPTIONS)
bad = dict(SPEC_ONE, display_name="Zzz Commission Export")
res = pull(p, [bad])
r0 = res["reports"][0]
check("a genuinely missing name keeps the operator-facing sentence",
      "is not one of the reports this portal login offers" in (r0.get("error") or ""))
check("…and now PRINTS THE REPR of both sides so an invisible mismatch is visible",
      "compared after normalising invisible characters" in (r0.get("error") or "")
      and "'Zzz Commission Export'" in (r0.get("error") or ""), r0.get("error"))
check("…and names the nearest offered candidate in repr()",
      "'MA - Commission Details'" in (r0.get("error") or "")
      or (r0.get("match_debug") or {}).get("nearest_offered") is not None, r0.get("error"))
check("…similarity is recorded for the operator",
      isinstance((r0.get("match_debug") or {}).get("similarity"), float))
p2 = Portal(OWNER_OPTIONS_INVISIBLE)
res2 = pull(p2, [SPEC_ONE])
r2 = res2["reports"][0]
check("THE OWNER'S CASE: the portal's invisible characters no longer break selection",
      r2.get("reason") != "report_not_listed", r2.get("error"))
check("…and the pull records HOW it matched, so the drift is visible not magic",
      (r2.get("name_match") or {}).get("tier") == "normalized")

print("[2b] a MISSING dropdown is no longer reported as a wrong report name")
# THE MID-PULL DRIFT CASE: report 1 runs fine, its results page replaces the form, and report 2's
# turn finds no dropdown at all. Before today that was reported as "your report name is wrong" and
# printed the option list captured BEFORE report 1 ran — the self-contradiction.
ONE_WIN = dict(SPEC_ONE, param_spec=dict(SPEC_ONE["param_spec"], iterate_months=False))
drift = Portal(OWNER_OPTIONS, after_submit="export_now", drop_select_after=1)
res3 = pull(drift, [ONE_WIN, dict(ONE_WIN, report_key="ma_daily_tx",
                                  display_name="MA Daily Tx SubMA",
                                  target_table="raw_ma_daily_tx")])
r3 = res3["reports"][1]
check("report 1 still ran normally", res3["reports"][0].get("rows_ingested", 0) > 0,
      res3["reports"][0].get("outcome"))
check("no report <select> at report 2's turn ⇒ reason 'report_select_missing'",
      r3.get("reason") == "report_select_missing", r3.get("reason"))
check("…and the sentence blames the page state, NOT the configured name",
      "page-state problem" in (r3.get("error") or "")
      and "not a wrong report name" in (r3.get("error") or ""), r3.get("error"))
check("…so the operator is not sent to Report mapping to fix a correct name",
      "Report mapping" not in (r3.get("error") or ""))
check("a bare frame with no <select> returns the same honest reason (unit level)",
      vp._select_report_detail(Frame(drift, []), "MA - Commission Details").get("reason")
      == "report_select_missing")
check("the LIVE options are what the failure prints (not the pre-pull capture)",
      "portal_options_captured" in r0 and "options_changed" in r0)

print("[3] DEFECT 2(c) — the Run control is clicked even as an ASP.NET LinkButton")
pa = Portal(OWNER_OPTIONS, submit_kind="anchor")
fr = pa.frames[0]
check("pristine _click_submit CANNOT click an <a> LinkButton Submit (the bug)",
      vp._click_submit(fr, ("submit", "run")) is False and pa.submits == 0)
check("_click_run CAN", vp._click_run(pa.frames[0], pa) == "submit" and pa.submits == 1)
plo = Portal(OWNER_OPTIONS, submit_kind="none")
plo.logout = El("button", text="Logout")
check("_click_run NEVER clicks Logout even though 'go' is a substring of 'logout'",
      vp._click_run(plo.frames[0], plo) is None and plo.logout.clicks == 0)
check("…and 'go' still works as a WHOLE word",
      vp._click_run(Portal(OWNER_OPTIONS, submit_kind="none",
                           extra=[El("button", text="Go")]).frames[0], None) == "go")
check("an Export control is never mistaken for the Run control",
      vp._click_run(Portal(OWNER_OPTIONS, submit_kind="none",
                           extra=[El("button", text="Export to: CSV")]).frames[0], None) is None)

print("[4] DEFECT 2(d) — the async grid is WAITED for, and the outcome is honest")
late = Portal(OWNER_OPTIONS, after_submit="grid_late", grid_delay=4)
res4 = pull(late, [SPEC_ONE])
check("a grid that populates after N ticks is scraped, not called empty",
      res4["delivered"] is True and res4["rows_ingested"] > 0, res4["status"])
check("…the wait is TIME, not requests: no navigation/reload happened during it",
      late.navigations == 0)
check("…and the report was submitted ONCE per window (no re-fire)",
      late.submits == len(res4["reports"][0]["windows"]), late.submits)

never = Portal(OWNER_OPTIONS, after_submit="never")
res5 = pull(never, [SPEC_ONE])
r5 = res5["reports"][0]
check("a grid that NEVER populates is a SCRAPE TIMEOUT, not 'returned no rows'",
      r5.get("ok") is False and r5.get("reason") == "results_never_rendered", r5.get("reason"))
check("…the per-report sentence says so in words",
      "scrape timeout" in (r5.get("outcome") or "") and "NOT an empty result" in (r5.get("outcome") or ""))
check("…the aggregate status refuses to claim the portal has no data",
      "scraping failure" in res5["status"] and "returned nothing" not in res5["status"],
      res5["status"])
check("…and it still delivers nothing (last_run_at semantics untouched)",
      res5["delivered"] is False and res5["rows_ingested"] == 0)
check("…a hard failure does NOT re-run the remaining month windows (paced-request budget)",
      never.submits == 1, never.submits)

emp = Portal(OWNER_OPTIONS, after_submit="empty_marker", grid_delay=2)
res6 = pull(emp, [SPEC_ONE], months_back=1)
r6 = res6["reports"][0]
check("an EXPLICIT 'No records found' is an honest empty result",
      r6.get("ok") is True and r6.get("empty_confirmed") is True, r6.get("outcome"))
check("…named as the portal's own answer, not our failure",
      "portal's own answer" in res6["status"] and res6["reason"] == "portal_reported_empty",
      res6["status"])
check("…and the honest-empty status carries no failure keyword (no false 'not importing' alarm)",
      "fail" not in res6["status"].lower() and "error" not in res6["status"].lower())
check("…still delivered=False (an empty month never advances last_run_at)",
      res6["delivered"] is False)

ngx = Portal(OWNER_OPTIONS, after_submit="grid_no_export", grid_delay=1)
res7 = pull(ngx, [SPEC_ONE])
r7 = res7["reports"][0]
check("rows on screen but nothing downloadable ⇒ export_link_missing (a scrape gap)",
      r7.get("ok") is False and r7.get("reason") == "export_link_missing", r7.get("reason"))
check("…and it is NOT reported as an empty month",
      "no export control" not in (res7["status"] or "").lower()
      or "returned nothing" not in res7["status"])

gen = Portal(OWNER_OPTIONS, after_submit="generic_export")
res8 = pull(gen, [SPEC_ONE])
check("a portal whose link is just 'Export' (no CSV/Excel word) is now used",
      res8["delivered"] is True, res8["status"])

nosub = Portal(OWNER_OPTIONS, submit_kind="none")
res9 = pull(nosub, [SPEC_ONE])
r9 = res9["reports"][0]
check("no Run control at all ⇒ 'run_control_missing', never 'returned no rows'",
      r9.get("ok") is False and r9.get("reason") == "run_control_missing", r9.get("reason"))
check("…and the sentence says the report was never actually run",
      "never actually run" in (r9.get("outcome") or ""))

print("[5] DEFECT 2(a)(b)(b2) — the date window is really submitted")
ok = Portal(OWNER_OPTIONS, after_submit="export_now")
res10 = pull(ok, [SPEC_ONE], months_back=2)
wins = res10["reports"][0]["windows"]
w, wlast = wins[0], wins[-1]
fields = {f["name"]: f for f in wlast["fields"]}          # the LAST window is what the DOM still holds
check("start and end land in DIFFERENT boxes (the collision is gone)",
      ok.start.value and ok.end.value and ok.start.value != ok.end.value,
      (ok.start.value, ok.end.value))
check("…start really went to txtStartDate and end to txtEndDate",
      fields["Start Date"]["value"] == ok.start.value
      and fields["End Date"]["value"] == ok.end.value,
      (fields["Start Date"]["value"], ok.start.value, fields["End Date"]["value"], ok.end.value))
check("…both were READ BACK to prove what the portal received",
      fields["Start Date"]["readback_ok"] and fields["End Date"]["readback_ok"])
check("a CHANGE event was dispatched on each date box (ASP.NET/jQuery commit)",
      "change" in ok.start.events and "change" in ok.end.events, ok.start.events)
check("…and a BLUR too (datepickers that commit on blur)",
      "blur" in ok.start.events and "blur" in ok.end.events)
check("the HIDDEN SessionId field is now filled (it was unreachable before)",
      ok.session.value == "169024" and fields["SessionId"]["found"] is True, ok.session.value)
check("…and its VALUE is never echoed into the diagnostic (only its length)",
      "value" not in fields["SessionId"] and fields["SessionId"]["value_len"] == 6)
check("the requested window is recorded per month, so 'was 2 months submitted?' is answerable",
      len(w["requested"]) == 2 and w["requested"][0] < w["requested"][1], w["requested"])
# months_back=2 means "the last ~62 days", which SPANS three calendar months near month-end; each is
# submitted as its own ≤1-month window (VidaPay caps the MA Commission range at 1 month).
check("a 2-month back-range submits a window per calendar month it spans (never today-only)",
      len(wins) >= 2 and len({x["window"] for x in wins}) == len(wins), [x["window"] for x in wins])
check("…each window's dates are inside its own month",
      all(x["requested"][0][:7] == x["window"] for x in wins), [x["requested"] for x in wins])
check("rows landed and the pull reports delivered",
      res10["delivered"] is True and res10["rows_ingested"] > 0)

print("[5b] hardening — layout tables and a pre-existing Export link cannot fake a result")
lay = Portal(OWNER_OPTIONS, after_submit="never", layout_rows=40)
res_lay = pull(lay, [SPEC_ONE])
r_lay = res_lay["reports"][0]
check("40 rows of ASP.NET layout chrome are NOT mistaken for a populated grid",
      r_lay.get("reason") == "results_never_rendered", r_lay.get("reason"))
check("…because the row count is a DELTA from the pre-submit baseline",
      (r_lay.get("windows") or [{}])[0].get("rows_seen") in (0, None),
      (r_lay.get("windows") or [{}])[0])
check("…and the bounded wait really elapsed (virtual clock ≈ the configured budget)",
      15 <= float((r_lay.get("windows") or [{}])[0].get("waited_s") or 0) <= 30,
      (r_lay.get("windows") or [{}])[0].get("waited_s"))

pre = Portal(OWNER_OPTIONS, after_submit="grid_late", grid_delay=3, export_always=True)
res_pre = pull(pre, [SPEC_ONE])
check("an export control that pre-dates the run is NOT clicked before the results move",
      res_pre["delivered"] is True
      and (res_pre["reports"][0]["windows"][0].get("rows_seen") or 0) > 0,
      res_pre["reports"][0]["windows"][0])
check("…and the download is not flagged as a stale-export risk",
      res_pre["reports"][0]["windows"][0].get("stale_export_risk") in (False, None))

stale = Portal(OWNER_OPTIONS, after_submit="never", export_always=True)
# budget 60s > the 30s stale-export settle, so the fallback window is actually reachable here
# (with results_wait_s=15 the settle IS the whole budget and the honest timeout wins — as it should).
res_stale = pull(stale, [dict(SPEC_ONE, param_spec=dict(SPEC_ONE["param_spec"],
                                                        results_wait_s=60, iterate_months=False))])
check("…but after the settle window it STILL exports (capability preserved, not regressed)",
      res_stale["delivered"] is True, res_stale["status"])
check("…flagged stale_export_risk so a suspicious 0-row export is explainable",
      res_stale["reports"][0]["windows"][0].get("stale_export_risk") is True,
      res_stale["reports"][0]["windows"][0])

print("[6] the owner's five configured DEFAULTS all select against the real portal vocabulary")
specs = rp.default_specs("vidapay")
names = [s["display_name"] for s in specs]
check("the 5 shipped defaults are exactly the portal's 5 report names",
      sorted(names) == sorted([o for o in OWNER_OPTIONS if o != "-- SELECT --"]), names)
for want in names:
    idx, tier = vp.match_report_option(want, OWNER_OPTIONS_INVISIBLE)
    check("default '%s' selects even through the portal's invisible characters (%s)" % (want, tier),
          idx > 0, tier)
p5 = Portal(OWNER_OPTIONS_INVISIBLE, after_submit="export_now")
five = [dict(SPEC_ONE, report_key=s["report_key"], display_name=s["display_name"],
             target_table=s["target_table"]) for s in specs]
res11 = pull(p5, five)
check("ALL FIVE select on one pass — 5/5, no 'not one of the reports this portal offers'",
      all(r.get("reason") not in ("report_not_listed", "ambiguous") for r in res11["reports"]),
      [(r["report_key"], r.get("reason")) for r in res11["reports"]])
check("…and the pull delivers rows for all five", res11["delivered"] is True)

print("[7] contract + safety")
drv = open(os.path.join(os.path.dirname(__file__), "..", "app", "modules", "commcalc",
                        "vidapay_sweep.py"), encoding="utf-8").read()
body = drv.split("DEFAULT_REPORT_SPECS")[0]
check("RULE TWO: no report/tenant name is hard-coded in the DRIVER (names come from config only)",
      all(n not in drv.replace("# ", "").split("def _norm_label")[1]
          for n in ("raw_ma_commission\"", "'raw_ma_commission'")),
      "driver references a target table literally")
check("…report names appear in the driver ONLY inside comments documenting the bug",
      all(not ln.strip().startswith(("\"", "'")) or "MA - Commission Details" not in ln
          for ln in drv.splitlines() if "MA - Commission Details" in ln and "#" not in ln))
check("normalisation is PURE (no page, no client, no I/O)",
      vp._norm_label("A" + NBSP + "B") == "a b" and vp._squash_label("A - B") == "ab")
check("_norm_label is idempotent", vp._norm_label(vp._norm_label("MA " + ENDASH + " X")) == "ma - x")
check("empty/None inputs never raise",
      vp._norm_label(None) == "" and vp.match_report_option(None, OWNER_OPTIONS)[0] == -1
      and vp.match_report_option("x", [])[1] == "no_options")
check("the results wait is bounded by config (param_spec.results_wait_s), not a constant",
      vp._RESULTS_WAIT_S == 90 and "results_wait_s" in
      __import__("inspect").getsource(vp._pull_one_report))
check("_wait_for_results never navigates (grep: no goto/reload in its source)",
      all(t not in __import__("inspect").getsource(vp._wait_for_results)
          for t in ("goto(", "reload(", "click(")))

print("[8] the new forensics SURVIVE onto the data_source row (else the modal shows nothing later)")
from app.modules.commcalc import router as R                                          # noqa: E402

pay_fail = R._pull_diag_payload(res)              # the mis-named-report pull from [2]
pr0 = pay_fail["reports"][0]
check("_pull_diag_payload keeps the honest per-report `outcome`", bool(pr0.get("outcome")))
check("…and the repr() forensics (match_debug)", bool((pr0.get("match_debug") or {}).get("wanted")))
check("…and the options_changed / portal_options evidence",
      "options_changed" in pr0 and pr0.get("portal_options"))
pay_ok = R._pull_diag_payload(res10)              # the delivering pull from [5]
w0 = (pay_ok["reports"][0].get("windows") or [{}])[0]
check("…and a bounded per-window trail (what was requested vs what came back)",
      w0.get("window") and w0.get("requested") and w0.get("state") == "exported", w0)
fnames = {f["name"]: f for f in (w0.get("fields") or [])}
check("…including the date actually submitted for that month",
      fnames.get("Start Date", {}).get("value", "").endswith(("2026", "2025")), fnames)
blob = json.dumps(R._pull_diag_payload(res10))
check("the stored diagnostic carries NO credential and no session-id VALUE",
      "169024" not in blob and "password" not in blob.lower(), blob[:200])
check("delivered/last_run_at semantics are untouched by any new outcome",
      R._pull_delivered(res5) is False and R._pull_delivered(res6) is False
      and R._pull_delivered(res10) is True)

print()
print("\n".join(LINES))
print()
print("%d passed, %d failed" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
