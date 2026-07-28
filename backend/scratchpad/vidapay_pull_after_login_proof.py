"""Proof for `agent/commission/vidapay-pull-after-login`.

OWNER REPORT 2026-07-27 (verbatim): "vidapay login shows logged in after the loive login page , shows
the commison module but does not import ay file."

Evidence from the owner's Payment-processor-sources screenshot, VidaPay row:
    ✅ Connected — until Jul 27, 10:24 PM
    pulled 0 rows across 0 report(s): —; calibration/diagnostic needed: ma_commission, ma_daily_tx,
    ma_marketplace_orders, ma_sim_assignment, ma_pr_activation

WHAT THAT STRING PROVES (and this file re-proves mechanically). `_pull_all_reports_on_page` lists a
report under "calibration/diagnostic needed" when it is `not ok` OR flagged `calibration`. Only
ma_sim_assignment / ma_pr_activation carry `calibration: True`; the other THREE can only appear via
`not ok`, and in the pristine `_pull_one_report` the sole `ok: False` return is the one taken when
`_select_report` fails. So the pull DID run, over all five configured reports, and could not select a
single one — i.e. it never got to the portal's Reports page.

ROOT CAUSE (classification (b)+(c), with an (a) component and a UI lie):
  (c/b) `_open_reports_page`'s own comment says it clicks "a Reports / Billing Manager LINK", but the
        only helper it had was `_click_submit`, whose selector is
        `button, input[type=submit], input[type=button], a[role=button]` — an ordinary ASP.NET menu
        anchor `<a href="Reports.aspx">Reports</a>` matches NONE of those. The navigation was a no-op,
        `_select_report` then failed for every report, and the failure was reported as
        "calibrate the display_name" — pointing at the wrong fix.
  (a)   Nothing pulled after a successful live login: `_on_authenticated` → `_post_auth_loop` idled
        waiting for a PULL command whose only producer was the operator clicking a SECOND button.
        `_POST_AUTH_IDLE_SECONDS` then closed the trusted session having imported nothing.
  (lie) `/run` returns ok:True whenever the pull RAN, the page rendered `✅ ${r.status}`, the per-report
        error + DOM diagnostic were returned over HTTP and dropped, and `import_audit.p_connectors`
        matched none of its keywords against "pulled 0 rows …" — so the admin popup stayed silent.

PURE — no real portal, no real Chromium, no DB, no credentials. Fake page/frame/select objects
implement just the Playwright surface the driver touches. Run:
    cd backend && python3 scratchpad/vidapay_pull_after_login_proof.py
"""
import json
import os
import sys
import time
import types

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.modules.commcalc import vidapay_sweep as vp        # noqa: E402
from app.modules.commcalc import live_login as ll           # noqa: E402
from app.modules.commcalc import import_audit as ia         # noqa: E402

PASS, FAIL, LINES = 0, 0, []
SECRET_PW = "Sup3rSecret!vidapay"


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        LINES.append("  ok   " + name)
    else:
        FAIL += 1
        LINES.append("  FAIL " + name)


# ════════════════════════════════════════════════════════════════════════════════════════════════
# Fake Playwright surface
# ════════════════════════════════════════════════════════════════════════════════════════════════
class El:
    """A clickable/queryable element. `tag` drives which query_selector_all buckets it lands in."""

    def __init__(self, tag, text="", value="", attrs=None, visible=True, on_click=None):
        self.tag = tag
        self.text = text
        self.value = value
        self.attrs = dict(attrs or {})
        self.visible = visible
        self.on_click = on_click
        self.clicks = 0
        self.filled = None
        self.selected = None
        self._options = []

    # ── Playwright-ish API ──
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
        self.filled = v

    def type(self, v, delay=None):
        self.filled = (self.filled or "") + v

    def evaluate(self, *a, **k):
        return None

    # <select> support
    def query_selector_all(self, sel):
        if "option" in sel:
            return list(self._options)
        return []

    def select_option(self, value=None, label=None):
        self.selected = value if value is not None else label


class Opt(El):
    def __init__(self, text, value=None):
        super().__init__("option", text=text, attrs={"value": value if value is not None else text})


def make_select(name, options, visible=True):
    s = El("select", attrs={"name": name, "id": name}, visible=visible)
    s._options = [Opt(o) for o in options]
    return s


class Frame:
    def __init__(self, page, elements=None, evaluate_payload=None):
        self.page = page
        self.elements = list(elements or [])
        self._eval = evaluate_payload or {}

    def query_selector_all(self, sel):
        """A REAL (tiny) CSS matcher: `tag` optionally followed by one `[attr=value]` / `[attr]`
        predicate, comma-separated. Approximating this with substring tests is exactly how a harness
        lies to itself — `"a[role=button]"` contains the substring `", a"`, so a sloppy matcher hands
        plain anchors to `_click_submit` and the root-cause repro silently passes."""
        out = []
        for part in [p.strip().lower() for p in str(sel).split(",") if p.strip()]:
            if "[" in part:
                tag, pred = part.split("[", 1)
                pred = pred.rstrip("]")
                key, _, want = pred.partition("=")
            else:
                tag, key, want = part, None, None
            for e in self.elements:
                if e.tag != tag:
                    continue
                if key:
                    have = e.get_attribute(key)
                    if have is None or (want and str(have).lower() != want.strip('"\'')):
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
        """Dispatch on the shape the caller asked for: the probes ask for ONE object, _snapshot asks
        for two ARRAYS (controls, headings). Returning the probe dict for every call is how the fake
        blew up _snapshot with 'str has no attribute get'."""
        j = str(js)
        # ORDER MATTERS: _snapshot's controls query also contains "=> ({" (it maps to an object per
        # element), so the ARRAY queries must be recognised BEFORE the single-object probe query.
        if "h1,h2,h3" in j:
            return ["Reports"]
        if "input,button,select" in j:
            return [{"tag": e.tag, "type": "", "name": e.attrs.get("name", ""), "id": e.attrs.get("id", ""),
                     "ph": "", "val": e.text, "vis": e.visible} for e in self.elements]
        if "=> ({" in j:
            return dict(self._eval)
        return []


class Page:
    """A tiny portal. `state` names the current screen; navigating swaps the frame set."""

    def __init__(self, screens, start, url="https://portal/Main%20Panel.aspx"):
        self.screens = screens        # {state: [element,...]}
        self.state = start
        self.url = url
        self.waits = 0
        self._probe_payload = {}

    # nav
    def go(self, state):
        self.state = state
        self.url = "https://portal/%s.aspx" % state

    @property
    def frames(self):
        return [Frame(self, self.screens.get(self.state, []), self._probe_payload)]

    def title(self):
        return "VidaPay CRM — " + self.state

    def content(self):
        return "<html>%s</html>" % self.state

    def wait_for_load_state(self, *a, **k):
        self.waits += 1

    def wait_for_timeout(self, *a, **k):
        self.waits += 1

    def query_selector_all(self, sel):
        return self.frames[0].query_selector_all(sel)

    def query_selector(self, sel):
        return self.frames[0].query_selector(sel)

    def evaluate(self, js, *a):
        return self.frames[0].evaluate(js, *a)

    def screenshot(self, **k):
        return b"jpeg"

    def expect_download(self, timeout=None):
        raise RuntimeError("no download in this fake")

    @property
    def viewport_size(self):
        return {"width": 1366, "height": 900}


REPORT_NAMES = ["-- select --", "MA - Commission Details", "MA Daily Tx SubMA",
                "MA - Marketplace Handset Fulfillment Orders", "Activation SIM Assignment Report",
                "PR Activation Details"]


def portal_with_link_nav():
    """The real-world shape: the landing page's Reports entry is a PLAIN <a href> menu link."""
    reports_screen = [make_select("ddlReport", REPORT_NAMES),
                      El("input", attrs={"name": "StartDate", "type": "text"}),
                      El("button", text="Submit")]
    page = Page({"reports": reports_screen}, "landing")
    landing = [El("a", text="Home", attrs={"href": "Main.aspx"}),
               El("a", text="Reports", attrs={"href": "Reports.aspx"},
                  on_click=lambda: page.go("reports")),
               El("a", text="Log out", attrs={"href": "Logout.aspx"},
                  on_click=lambda: page.go("loggedout")),
               El("button", text="Refresh")]
    page.screens["landing"] = landing
    page.screens["loggedout"] = [El("a", text="Sign in", attrs={"href": "Login.aspx"})]
    return page


# ════════════════════════════════════════════════════════════════════════════════════════════════
print("[1] ROOT CAUSE — the nav helper could not click a plain <a href> menu link")
p = portal_with_link_nav()
fr = p.frames[0]
check("pristine _click_submit CANNOT click the <a href> Reports link (the bug)",
      vp._click_submit(fr, ("reports",)) is False)
check("the page therefore never left the landing screen", p.state == "landing")
check("NEW _click_nav CAN click it", vp._click_nav(fr, ("reports",)) is True)
check("…and the portal navigated to the Reports page", p.state == "reports")

p2 = portal_with_link_nav()
check("_click_nav NEVER clicks a sign-out control even when asked for 'log'",
      vp._click_nav(p2.frames[0], ("log",)) is False or p2.state != "loggedout")
check("…and the logout link was left unclicked",
      [e for e in p2.screens["landing"] if e.text == "Log out"][0].clicks == 0)

p3 = portal_with_link_nav()
check("_open_reports_page now REACHES the reports frame via the anchor",
      vp._open_reports_page(p3) is not None and p3.state == "reports")

# a portal with no way to reach reports at all
p4 = Page({"landing": [El("a", text="Home", attrs={"href": "#"}), El("button", text="Refresh")]}, "landing")
check("_open_reports_page returns None (not the page) when Reports is unreachable",
      vp._open_reports_page(p4) is None)

# ════════════════════════════════════════════════════════════════════════════════════════════════
print("[2] the portal's OWN report vocabulary is captured (the calibration payload)")
p5 = portal_with_link_nav()
vp._open_reports_page(p5)
opts = vp.report_options(p5)
check("report_options() lists what the portal really offers",
      opts == REPORT_NAMES)
p5._probe_payload = {
    "links": [{"t": "Reports", "href": "Reports.aspx"}],
    "selects": [{"name": "ddlReport", "id": "ddlReport", "opts": REPORT_NAMES}],
    "buttons": [{"t": "Submit", "id": "btn", "name": "btn"}],
    "dates": [{"id": "StartDate", "name": "StartDate", "type": "text", "ph": ""}],
}
probe = vp.reports_probe(p5)
check("reports_probe carries nav links + selects + report_options",
      probe["nav_links"] and probe["selects"] and probe["report_options"] == REPORT_NAMES)
blob = json.dumps(probe)
check("probe contains NO credential (it reads names/ids/labels, never input values)",
      SECRET_PW not in blob and "password" not in blob.lower())

# ════════════════════════════════════════════════════════════════════════════════════════════════
print("[3] honest pull outcomes — a 0-row pull can never read as success")


class FakeSB:
    """Minimal supabase stub: records every schema/table/op with its filters."""

    def __init__(self):
        self.ops = []

    def schema(self, s):
        self._s = s
        return self

    def table(self, t):
        self._t = t
        return self

    def select(self, *a, **k):
        self._op = {"schema": self._s, "table": self._t, "op": "select", "filters": {}}
        self.ops.append(self._op)
        return self

    def update(self, row):
        self._op = {"schema": self._s, "table": self._t, "op": "update", "row": row, "filters": {}}
        self.ops.append(self._op)
        return self

    def insert(self, row):
        self._op = {"schema": self._s, "table": self._t, "op": "insert", "row": row, "filters": {}}
        self.ops.append(self._op)
        return self

    def delete(self):
        self._op = {"schema": self._s, "table": self._t, "op": "delete", "filters": {}}
        self.ops.append(self._op)
        return self

    def eq(self, k, v):
        self._op["filters"][k] = v
        return self

    def gte(self, k, v):
        return self

    def lte(self, k, v):
        return self

    def limit(self, n):
        return self

    def order(self, *a, **k):
        return self

    def execute(self):
        return types.SimpleNamespace(data=self._op.get("_data", []))


SPECS = [{"report_key": "ma_commission", "display_name": "MA - Commission Details",
          "target_table": "raw_ma_commission", "export_pref": "csv", "column_map": {},
          "param_spec": {"date_col": "tx_date", "fields": []}},
         {"report_key": "ma_daily_tx", "display_name": "MA Daily Tx SubMA",
          "target_table": "raw_ma_daily_tx", "export_pref": "csv", "column_map": {},
          "param_spec": {"date_col": "tx_date", "fields": []}}]


def with_specs(specs, fn):
    from app.modules.commcalc import report_pull as rp
    saved = rp.resolve_report_specs
    rp.resolve_report_specs = lambda *a, **k: [dict(s) for s in specs]
    try:
        return fn()
    finally:
        rp.resolve_report_specs = saved


# (a) reports page unreachable
res_a = with_specs(SPECS, lambda: vp._pull_all_reports_on_page(p4, FakeSB(), "orgA", "src1", "car1", 2, {}))
check("unreachable Reports page ⇒ delivered False", res_a["delivered"] is False)
check("…reason='no_reports_page'", res_a["reason"] == "no_reports_page")
check("…status is a WARNING, never 'pulled N rows'",
      res_a["status"].startswith("⚠️") and "pulled" not in res_a["status"])
check("…reports_page_reachable is False", res_a["reports_page_reachable"] is False)

# (b) reports page reached, but the configured names are not in the portal's list
BAD_SPECS = [dict(SPECS[0], display_name="Zzz Commission Export"),
             dict(SPECS[1], display_name="Zzz Daily")]
p6 = portal_with_link_nav()
res_b = with_specs(BAD_SPECS, lambda: vp._pull_all_reports_on_page(p6, FakeSB(), "orgA", "src1", "car1", 2, {}))
check("mis-named reports ⇒ delivered False", res_b["delivered"] is False)
check("…reason='report_not_listed' (NOT 'calibrate the display_name' guesswork)",
      res_b["reason"] == "report_not_listed")
check("…the status NAMES the portal's real options",
      "MA - Commission Details" in res_b["status"])
check("…calibration block lists configured vs unmatched",
      res_b["calibration"]["unmatched"] == ["Zzz Commission Export", "Zzz Daily"]
      and res_b["calibration"]["portal_report_options"] == REPORT_NAMES)
check("…per-report error tells the operator what the portal offers",
      "is not one of the reports this portal login offers" in (res_b["reports"][0]["error"] or ""))

# (c) happy path — rows land
from app.modules.commcalc import report_pull as rp                                    # noqa: E402


def happy():
    saved = (vp._submit_and_export, rp.parse_export_bytes, rp.apply_column_map, rp.ingest_report_rows)
    vp._submit_and_export = lambda page, frame, pref, t=300: (b"a,b\n1,2\n", "x.csv")
    rp.parse_export_bytes = lambda c, f: [{"a": 1}]
    rp.apply_column_map = lambda rows, spec, org, sid, cid: [{"org_id": org, "a": 1}]
    rp.ingest_report_rows = lambda *a, **k: 7
    try:
        p7 = portal_with_link_nav()
        return with_specs(SPECS, lambda: vp._pull_all_reports_on_page(p7, FakeSB(), "orgA", "src1", "car1", 1, {}))
    finally:
        (vp._submit_and_export, rp.parse_export_bytes, rp.apply_column_map, rp.ingest_report_rows) = saved


res_c = happy()
check("happy path ⇒ delivered True with rows", res_c["delivered"] is True and res_c["rows_ingested"] == 14)
check("…status says 'imported', not the old ambiguous 'pulled'",
      res_c["status"].startswith("imported ") and "⚠️" not in res_c["status"])

# (d) no reports switched on at all
p8 = portal_with_link_nav()
res_d = with_specs([], lambda: vp._pull_all_reports_on_page(p8, FakeSB(), "orgA", "src1", "car1", 2, {}))
check("zero configured reports ⇒ delivered False + its own reason",
      res_d["delivered"] is False and res_d["reason"] == "no_reports_configured")

# ════════════════════════════════════════════════════════════════════════════════════════════════
print("[4] 'delivered' predicates agree everywhere (no green tick on an empty import)")
from app.modules.commcalc import router as R                                          # noqa: E402

B2B = {"status": "⚠️ imported 0 rows — signed in to b2bsoft OK, but ...",
       "authenticated": True, "delivered": False, "rows_ingested": 0}
cases = [
    ("explicit delivered False", {"delivered": False, "rows_ingested": 0}, False),
    ("rows>0", {"rows_ingested": 5}, True),
    ("rows==0", {"rows_ingested": 0}, False),
    ("ok:False", {"ok": False, "error": "boom"}, False),
    ("b2bsoft session-verified (imported nothing)", B2B, False),
]
for label, res, want in cases:
    check(f"router._pull_delivered — {label} ⇒ {want}", R._pull_delivered(res) is want)
    check(f"live_login._delivered — {label} ⇒ {want}", ll._delivered(res) is want)
check("router._pull_delivered keeps 'unknown shape ⇒ True' (no driver regressed)",
      R._pull_delivered({"status": "ok"}) is True)

# ════════════════════════════════════════════════════════════════════════════════════════════════
print("[5] AUTO-PULL: authenticating now imports, without a second human click")


class _FakeP:
    def __enter__(self):
        return "P"

    def __exit__(self, *a):
        return False


def install_fake_playwright():
    saved = {k: sys.modules.get(k) for k in ("playwright", "playwright.sync_api")}
    m = types.ModuleType("playwright")
    s = types.ModuleType("playwright.sync_api")
    s.sync_playwright = lambda: _FakeP()
    m.sync_api = s
    sys.modules["playwright"] = m
    sys.modules["playwright.sync_api"] = s
    return saved


def restore(saved):
    for k, v in saved.items():
        if v is None:
            sys.modules.pop(k, None)
        else:
            sys.modules[k] = v


class LivePage:
    def __init__(self):
        self.url = "https://portal/Main.aspx"

    def goto(self, *a, **k):
        pass

    def wait_for_load_state(self, *a, **k):
        pass

    def wait_for_timeout(self, *a, **k):
        pass

    def query_selector(self, *a, **k):
        return None


class LiveCtx:
    def __init__(self):
        self._p = LivePage()

    def new_page(self):
        return self._p


_LAST_CTX = {}


class FakeVP:
    DEFAULT_URL = "https://portal/Main.aspx"
    B2BSOFT_URL = "https://b2b/"
    SESSION_TTL_HOURS = 8

    class VidaPayLoginError(Exception):
        pass

    _TRUST_PAGE_WORDS = ()

    @staticmethod
    def _launch(p):
        return types.SimpleNamespace(close=lambda: None)

    @staticmethod
    def _new_context(browser, proxy=None):
        ctx = LiveCtx()
        _LAST_CTX["ctx"] = ctx      # so the proof can assert page IDENTITY, not just "a page"
        return ctx

    @staticmethod
    def _proxy_arg(u):
        return None

    @staticmethod
    def _norm_url(u, d):
        return u or d

    @staticmethod
    def _goto_login(page, base):
        pass

    @staticmethod
    def _password_frame(page):
        return (object(), El("input"))

    @staticmethod
    def drive_typed_login(page, fr, el, acc, user, pw):
        pass

    @staticmethod
    def _wait_settle(page):
        pass

    @staticmethod
    def _shot_b64(page):
        return "SHOT"

    @staticmethod
    def capture_session_state(page, ctx):
        return {"cookies": [], "origins": []}

    @staticmethod
    def _classify(page):
        return "authenticated"

    @staticmethod
    def _code_field(page):
        return None

    @staticmethod
    def _page_text(page):
        return ""


ROW = {"portal_url": "https://portal/Main.aspx", "account_id": "169024", "username": "nova",
       "password": SECRET_PW, "processor": "vidapay", "id": "src1", "carrier_id": "car1",
       "months_back": 2}


def wait_for(fn, timeout=8):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if fn():
            return True
        time.sleep(0.03)
    return False


_saved_vp = ll._vp
ll._vp = lambda: FakeVP
_pw = install_fake_playwright()
try:
    # (a) auto-pull ON (default) with a pull that lands rows
    pulled, persisted = [], []
    sess = ll.LiveLoginSession("src1", "orgA", ROW,
                               persist=lambda u: None, persist_shot=lambda s: None,
                               pull_fn=lambda page: (pulled.append(page) or
                                                     {"status": "imported 9 rows across 1 report(s): ma_commission",
                                                      "rows_ingested": 9, "delivered": True}),
                               persist_pull=lambda res: persisted.append(res))
    sess.start()
    got = wait_for(lambda: len(pulled) == 1 and sess.snapshot_phase() == "authenticated")
    check("a successful login PULLS automatically — no ▶ Pull now click", got and len(pulled) == 1)
    check("…the pull ran on the SAME live browser page that just authenticated (identity, not a "
          "cold restore)", bool(pulled) and pulled[0] is _LAST_CTX["ctx"]._p)
    check("…the outcome was persisted through persist_pull", len(persisted) == 1)
    st = sess.state()
    check("…state.pull reports delivered True + the row count",
          st["pull"]["ran"] is True and st["pull"]["delivered"] is True and st["pull"]["rows"] == 9)
    check("…the human message says IMPORTED", st["message"].startswith("Imported:"))
    check("…no credential leaks into the polled state", SECRET_PW not in json.dumps(st))
    sess.cancel()

    # (b) auto-pull ON, but the pull imports nothing → the message must be a warning
    pulled2, persisted2 = [], []
    ZERO = {"status": "⚠️ imported 0 rows — none of the 5 configured report names exist in this "
                      "portal's Reports list (it offers: MA - Commission Details).",
            "rows_ingested": 0, "delivered": False, "reason": "report_not_listed",
            "calibration": {"portal_report_options": REPORT_NAMES}}
    sess2 = ll.LiveLoginSession("src2", "orgA", ROW,
                                persist=lambda u: None, persist_shot=lambda s: None,
                                pull_fn=lambda page: (pulled2.append(page) or ZERO),
                                persist_pull=lambda res: persisted2.append(res))
    sess2.start()
    wait_for(lambda: len(pulled2) == 1 and sess2.snapshot_phase() == "authenticated")
    st2 = sess2.state()
    check("a 0-row auto-pull says NOTHING IMPORTED, never 'Pulled:'",
          st2["message"].startswith("⚠️ Nothing imported") and "Pulled:" not in st2["message"])
    check("…state.pull.delivered is False with the reason + the portal's options",
          st2["pull"]["delivered"] is False and st2["pull"]["reason"] == "report_not_listed"
          and st2["pull"]["options"] == REPORT_NAMES)
    check("…the failing outcome was still persisted (an attempt is evidence too)", len(persisted2) == 1)
    sess2.cancel()

    # (c) the per-source switch really switches it off
    pulled3 = []
    sess3 = ll.LiveLoginSession("src3", "orgA", dict(ROW, auto_pull_after_login=False),
                                persist=lambda u: None, persist_shot=lambda s: None,
                                pull_fn=lambda page: pulled3.append(page) or {"rows_ingested": 1},
                                persist_pull=None)
    sess3.start()
    wait_for(lambda: sess3.snapshot_phase() == "authenticated")
    time.sleep(0.3)
    check("auto_pull_after_login=False ⇒ no automatic pull", pulled3 == [])
    check("…but the session is still authenticated and pullable on demand",
          sess3.snapshot_phase() == "authenticated" and sess3.can_pull() is True)
    sess3.run_pull_blocking(timeout=4)
    check("…and an explicit ▶ Pull now still works", len(pulled3) == 1)
    sess3.cancel()

    # (d) a pre-migration row (no column at all) behaves as ON — never a silent regression
    pulled4 = []
    row_nocol = {k: v for k, v in ROW.items()}
    sess4 = ll.LiveLoginSession("src4", "orgA", row_nocol,
                                persist=lambda u: None, persist_shot=lambda s: None,
                                pull_fn=lambda page: pulled4.append(page) or {"rows_ingested": 2},
                                persist_pull=None)
    sess4.start()
    check("a row WITHOUT the mig-242 column auto-pulls (degrades ON)",
          wait_for(lambda: len(pulled4) == 1))
    sess4.cancel()

    # (e) the auto-pull fires exactly once per session
    pulled5 = []
    sess5 = ll.LiveLoginSession("src5", "orgA", ROW,
                                persist=lambda u: None, persist_shot=lambda s: None,
                                pull_fn=lambda page: pulled5.append(page) or {"rows_ingested": 1},
                                persist_pull=None)
    sess5.start()
    wait_for(lambda: len(pulled5) == 1)
    time.sleep(0.4)
    check("auto-pull is once-per-session (no loop)", len(pulled5) == 1)
    sess5.cancel()
finally:
    ll._vp = _saved_vp
    restore(_pw)

# ════════════════════════════════════════════════════════════════════════════════════════════════
print("[5b] a long automatic pull is INTERRUPTIBLE — Close is not a 50-minute wait")
check("_takes_stop detects a 2-arg pull_fn", ll._takes_stop(lambda page, stop: None) is True)
check("…and leaves a legacy 1-arg pull_fn alone", ll._takes_stop(lambda page: None) is False)
check("…*args is treated as accepting the stop signal", ll._takes_stop(lambda *a: None) is True)
check("…something unintrospectable falls back to one-arg (safe)", ll._takes_stop(object()) is False)

stop_after = {"n": 0}


def stop_now():
    stop_after["n"] += 1
    return stop_after["n"] > 1        # let the first report run, then stop


p_stop = portal_with_link_nav()
res_stop = with_specs(SPECS, lambda: vp._pull_all_reports_on_page(
    p_stop, FakeSB(), "orgA", "src1", "car1", 1, {}, should_stop=stop_now))
check("the pull stops at the next report boundary when asked",
      res_stop["stopped"] is True and len(res_stop["reports"]) == 1)
check("…and says so in the status", res_stop["status"].startswith("stopped early"))
p_go = portal_with_link_nav()
res_go = with_specs(SPECS, lambda: vp._pull_all_reports_on_page(
    p_go, FakeSB(), "orgA", "src1", "car1", 1, {}, should_stop=lambda: False))
check("…and runs every report when nobody asks it to stop",
      res_go["stopped"] is False and len(res_go["reports"]) == 2)

print("[6] _record_pull_result — honest timestamps, durable diagnostic, org-scoped, evidence trail")
traces = []
_saved_trace = R._write_upload_trace
R._write_upload_trace = lambda org_id, **kw: traces.append({"org_id": org_id, **kw})
try:
    # delivered
    cli = FakeSB()
    res_ok = {"status": "imported 14 rows across 2 report(s): ma_commission, ma_daily_tx",
              "rows_ingested": 14, "delivered": True,
              "reports": [{"report_key": "ma_commission", "target_table": "raw_ma_commission",
                           "ok": True, "rows_ingested": 7, "months_covered": ["2026-07"]},
                          {"report_key": "ma_daily_tx", "target_table": "raw_ma_daily_tx",
                           "ok": True, "rows_ingested": 7, "months_covered": ["2026-07"]}],
              "probe": {"url": "https://portal/Reports.aspx", "report_options": REPORT_NAMES}}
    d = R._record_pull_result(cli, "src1", "orgA", res_ok)
    ups = [o for o in cli.ops if o["op"] == "update" and o["table"] == "data_source"]
    stamp = [o for o in ups if "last_run_at" in o["row"] or "last_attempt_at" in o["row"]][0]
    diagw = [o for o in ups if "last_pull_diag" in o["row"]][0]
    check("delivered pull ⇒ last_run_at advances", d is True and "last_run_at" in stamp["row"])
    check("…and last_attempt_at is NOT used for a success", "last_attempt_at" not in stamp["row"])
    check("…every write is org-scoped on id + org_id",
          all(o["filters"].get("org_id") == "orgA" and o["filters"].get("id") == "src1" for o in ups))
    check("…the diagnostic is written in its OWN update (can't disable last_attempt_at)",
          "last_attempt_at" not in diagw["row"] and "last_status" not in diagw["row"])
    check("…one upload_trace row per report that landed rows (mig-202 evidence)",
          len(traces) == 2 and {t["upload_type"] for t in traces} == {"ma_commission", "ma_daily_tx"})
    check("…upload_trace is stamped with the caller's org", all(t["org_id"] == "orgA" for t in traces))

    # not delivered
    traces.clear()
    cli2 = FakeSB()
    res_bad = {"status": "⚠️ imported 0 rows — none of the 5 configured report names exist …",
               "rows_ingested": 0, "delivered": False, "reason": "report_not_listed",
               "reports_page_reachable": True,
               "reports": [{"report_key": "ma_commission", "ok": False, "reason": "report_not_listed",
                            "error": "“MA - Commission Details” is not one of the reports …"}],
               "calibration": {"portal_report_options": REPORT_NAMES,
                               "configured": ["MA - Commission Details"], "unmatched": ["MA - Commission Details"]}}
    d2 = R._record_pull_result(cli2, "src1", "orgB", res_bad)
    ups2 = [o for o in cli2.ops if o["op"] == "update" and o["table"] == "data_source"]
    stamp2 = [o for o in ups2 if "last_attempt_at" in o["row"] or "last_run_at" in o["row"]][0]
    check("0-row pull ⇒ last_attempt_at only (freshness never faked)",
          d2 is False and "last_attempt_at" in stamp2["row"] and "last_run_at" not in stamp2["row"])
    check("…NO upload_trace evidence is invented for an empty import", traces == [])
    check("…the honest status text is what lands on the row",
          stamp2["row"]["last_status"].startswith("⚠️"))
    check("…the second tenant's writes are scoped to ITS org",
          all(o["filters"].get("org_id") == "orgB" for o in ups2))

    payload = R._pull_diag_payload(res_bad)
    check("the stored diagnostic keeps the portal's vocabulary for the UI",
          payload["calibration"]["portal_report_options"] == REPORT_NAMES)
    check("…the per-report reason survives", payload["reports"][0]["reason"] == "report_not_listed")
    check("…and it carries no credential", SECRET_PW not in json.dumps(payload))
finally:
    R._write_upload_trace = _saved_trace

# ════════════════════════════════════════════════════════════════════════════════════════════════
print("[7] the admin popup can no longer stay silent on the owner's exact row")
OWNER_STATUS = ("pulled 0 rows across 0 report(s): —; calibration/diagnostic needed: ma_commission, "
                "ma_daily_tx, ma_marketplace_orders, ma_sim_assignment, ma_pr_activation")
owner_row = {"id": "src1", "label": "Vida Pay", "processor": "vidapay", "enabled": True,
             "username": "nova", "account_id": "169024", "password": "x",
             "auth_status": "authenticated", "last_status": OWNER_STATUS,
             "last_run_at": None, "last_attempt_at": "2026-07-27T14:24:00Z"}
old_bad = (owner_row["auth_status"].lower() in ("needs_2fa", "error", "authenticating")
           or any(k in owner_row["last_status"].lower()
                  for k in ("error", "fail", "needs login", "not wired", "403")))
check("the PRE-EXISTING keyword check matched NOTHING on that row (why it was silent)", old_bad is False)
check("_signed_in_never_delivered fires on it", ia._signed_in_never_delivered(owner_row) is True)
check("…a login that HAS delivered stays quiet",
      ia._signed_in_never_delivered(dict(owner_row, last_status="imported 412 rows across 2 report(s)",
                                         last_run_at="2026-07-27T14:30:00Z")) is False)
check("…a not-yet-authenticated login is left to the existing branch",
      ia._signed_in_never_delivered(dict(owner_row, auth_status="needs_2fa")) is False)
check("…a brand-new login that has never been tried is not nagged about",
      ia._signed_in_never_delivered({"auth_status": "authenticated"}) is False)

# ════════════════════════════════════════════════════════════════════════════════════════════════
print("[7b] the live pull dispatches on the PROCESSOR (b2bsoft never drives the VidaPay report list)")
b2b_page = Page({"landing": [El("a", text="Sales Reports", attrs={"href": "Rep.aspx"})]}, "landing")
b2b_page._probe_payload = {"links": [{"t": "Sales Reports", "href": "Rep.aspx", "id": ""}],
                           "buttons": [], "dates": [], "selects": []}
pf_b2b = R._live_pull(FakeSB(), "orgA", {"id": "s9", "processor": "b2bsoft", "carrier_id": None})
out_b2b = pf_b2b(b2b_page)
check("a b2bsoft live session runs the b2bsoft path, not the VidaPay MA report list",
      out_b2b.get("reason") == "not_wired" and "b2bsoft" in out_b2b.get("status", ""))
check("…and reports it delivered nothing (never fakes freshness)",
      out_b2b["delivered"] is False and out_b2b["rows_ingested"] == 0
      and R._pull_delivered(out_b2b) is False)
check("…while it still hands back the probe that would wire the download",
      bool(out_b2b.get("report_probe")))
pf_vp = R._live_pull(FakeSB(), "orgA", {"id": "s1", "processor": "vidapay", "carrier_id": "c",
                                        "months_back": 1})
p_vp = portal_with_link_nav()
out_vp = with_specs(SPECS, lambda: pf_vp(p_vp))
# 2026-07-28: this fake portal has a Submit button that changes nothing — no grid, no export link.
# Since the results-wait fix that is no longer "no_rows" (a claim about the PORTAL'S DATA) but
# 'results_never_rendered' (a claim about OUR SCRAPE), which is the honest reading of this fixture.
# See vidapay_report_calibration_proof.py for the exhaustive coverage of the four outcomes.
check("a vidapay live session still runs the MA report pull",
      out_vp.get("reason") in ("no_rows", "report_not_listed", "results_never_rendered", None)
      and "reports" in out_vp)

print("[8] contract compliance — routes, org param, secrets")
import inspect                                                                        # noqa: E402

sig = inspect.signature(R.data_source_pull_diagnostic)
check("GET /pull-diagnostic takes org_id as a QUERY PARAM (contract §2)",
      "org_id" in sig.parameters and "body" not in sig.parameters)
srcp = inspect.getsource(R.data_source_pull_diagnostic)
check("…and filters on BOTH id and org_id (tenant isolation)",
      '.eq("id", sid)' in srcp and '.eq("org_id", org_id)' in srcp)
check("…and never selects a secret column",
      all(k not in srcp.split("select(")[1].split(")")[0] for k in ("password", "session_state")))
check("_strip_source_pw drops the diagnostic blob from the 3s-polled list",
      "last_pull_diag" in inspect.getsource(R._strip_source_pw)
      and 'row.pop("last_pull_diag"' in inspect.getsource(R._strip_source_pw))
stripped = R._strip_source_pw({"id": "s", "password": SECRET_PW, "session_state": {"c": 1},
                               "last_pull_diag": {"probe": {}}, "last_status": "x"})
check("…and still strips password + session_state",
      "password" not in stripped and "session_state" not in stripped
      and stripped["has_pull_diag"] is True and SECRET_PW not in json.dumps(stripped))

print()
print("\n".join(LINES))
print()
print(f"{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
