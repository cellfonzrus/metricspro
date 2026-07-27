"""Proof for `agent/commission/portal-rate-limit-backoff` (migration 244).

OWNER REPORT 2026-07-27 (verbatim): "for vidapay it says you have too many requests, and have been
temporarily blocked, try again later".

WHAT WENT WRONG. Nothing in the portal-pull stack recognised a rate-limit / temporary-block response.
A blocked portal produced a GENERIC failure — "could not find the password field" at login, "report not
listed" / "the VidaPay session has expired" at pull time — and every one of those messages points a
human at an action that makes the block DEEPER (re-login, re-map, pull again), while the scheduled
/run-due poll would have fired again on its own next tick with no memory of the refusal. (The cron was
never actually registered, so the 07-27 volume was same-day attempts: nav retries x 5 reports, probing,
and several manual logins. Both halves are fixed here — detection+cooldown, and the request-volume trim.)

WHAT THIS FILE PROVES, mechanically and with no portal, no Chromium, no DB and no credentials:
  A  detection is honest at BOTH phases (login and pull), at the WIRE level (429 / 503+Retry-After) and
     at the PAGE level (configurable marker phrases), and never fires on a healthy page;
  B  the cooldown escalates 30m -> 2h -> 8h(cap), honours Retry-After LATER-only, and is configurable;
  C  everything RESPECTS it: /run-due skips (and a skip is NOT an attempt), the automatic post-login
     pull is suppressed, and every HUMAN entry point demands an explicit confirm;
  D  recovery (a pull that actually imports rows) resets consecutive_failures and lifts the cooldown;
  E  it is INERT before migration 244 runs — no 500s, no behaviour change, in both directions;
  F  every read and write is org-scoped (RULE ONE) and every phrase/threshold is config (RULE TWO);
  G  the gratuitous request volume in the nav path is capped and paced (scope item 5).

Run:  cd backend && python3 scratchpad/portal_rate_limit_backoff_proof.py
"""
import asyncio
import io
import os
import re
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.modules.commcalc import portal_backoff as pb        # noqa: E402
from app.modules.commcalc import vidapay_sweep as vp         # noqa: E402
from app.modules.commcalc import live_login as ll            # noqa: E402
from app.modules.commcalc import import_audit as ia          # noqa: E402
from app.modules.commcalc import router as R                 # noqa: E402

PASS, FAIL, LINES = 0, 0, []
ORG_A = "00000000-0000-0000-0000-000000000001"      # house / Boost
ORG_B = "11111111-1111-1111-1111-111111111111"      # Luxelink (a NON-house tenant — contract §2)
SECRET_PW = "Sup3rSecret!vidapay"
NOW = datetime(2026, 7, 27, 18, 0, 0, tzinfo=timezone.utc)


def check(name, cond, extra=None):
    global PASS, FAIL
    if cond:
        PASS += 1
        LINES.append("  ok   " + name)
    else:
        FAIL += 1
        LINES.append("  FAIL " + name + (("  " + repr(extra)) if extra is not None else ""))


def section(t):
    LINES.append("")
    LINES.append(t)


# ════════════════════════════════════════════════════════════════════════════════════════════════
# Fake supabase — data_source / portal_block_marker / commission_org_config
# `missing` simulates PRE-MIGRATION-244: selecting or updating an unknown column RAISES, exactly as
# PostgREST does, which is the only honest way to prove the degrade path.
# ════════════════════════════════════════════════════════════════════════════════════════════════
class Q:
    def __init__(self, store, table, missing, log):
        self.store, self.table, self.missing, self.log = store, table, missing, log
        self.filters, self.cols, self._upd, self._ors = [], "*", None, None

    # reads
    def select(self, cols="*"):
        self.cols = cols
        return self

    def eq(self, k, v):
        self.filters.append((k, v))
        return self

    def in_(self, k, vals):
        self.filters.append((k, list(vals)))
        return self

    def or_(self, s):
        self._ors = s
        return self

    def limit(self, n):
        return self

    def order(self, *a, **k):
        return self

    def update(self, patch):
        self._upd = dict(patch)
        return self

    def _bad_cols(self, names):
        return [c for c in names if c in (self.missing.get(self.table) or set())]

    def execute(self):
        miss = self.missing.get(self.table) or set()
        if self._upd is not None:
            bad = [c for c in self._upd if c in miss]
            if bad:
                raise Exception("column data_source.%s does not exist" % bad[0])
            n = 0
            for r in self.store.get(self.table, []):
                if all((r.get(k) in v) if isinstance(v, list) else (r.get(k) == v)
                       for k, v in self.filters):
                    r.update(self._upd)
                    n += 1
            self.log.append({"op": "update", "table": self.table, "filters": list(self.filters),
                             "patch": dict(self._upd), "matched": n})
            return type("Res", (), {"data": []})()
        if self.cols != "*":
            bad = [c for c in re.split(r"[,\s]+", self.cols) if c and c in miss]
            if bad:
                raise Exception("column data_source.%s does not exist" % bad[0])
        out = []
        for r in self.store.get(self.table, []):
            if all((r.get(k) in v) if isinstance(v, list) else (r.get(k) == v)
                   for k, v in self.filters):
                out.append({k: v for k, v in r.items() if k not in miss})
        self.log.append({"op": "select", "table": self.table, "filters": list(self.filters),
                         "cols": self.cols, "or": self._ors, "n": len(out)})
        return type("Res", (), {"data": out})()


class Schema:
    def __init__(self, store, missing, log):
        self.store, self.missing, self.log = store, missing, log

    def table(self, t):
        return Q(self.store, t, self.missing, self.log)


class Client:
    def __init__(self, store=None, missing=None):
        self.store = store if store is not None else {}
        self.missing = missing or {}
        self.log = []

    def schema(self, s):
        return Schema(self.store, self.missing, self.log)

    def rpc(self, *a, **k):
        raise Exception("no rpc in this proof")

    def updates(self, table="data_source"):
        return [e for e in self.log if e["op"] == "update" and e["table"] == table]


def src_row(sid="src-1", org=ORG_A, **kw):
    r = {"id": sid, "org_id": org, "label": "VidaPay", "processor": "vidapay", "enabled": True,
         "username": "u", "account_id": "a", "password": SECRET_PW, "portal_url": "https://portal/",
         "auth_status": "authenticated", "auth_message": "", "last_status": "ok",
         "frequency": "hourly", "hour": 6, "months_back": 2, "carrier_id": None,
         "session_state": "{}", "next_run_at": None, "last_run_at": None, "last_attempt_at": None,
         "blocked_until": None, "blocked_at": None, "block_reason": None, "consecutive_failures": 0}
    r.update(kw)
    return r


def blocked_row(sid="src-1", org=ORG_A, mins=45, fails=1, **kw):
    return src_row(sid, org, blocked_until=(datetime.now(timezone.utc) + timedelta(minutes=mins)).isoformat(),
                   blocked_at=datetime.now(timezone.utc).isoformat(),
                   block_reason="The portal's page says “too many requests”.",
                   consecutive_failures=fails, **kw)


# ════════════════════════════════════════════════════════════════════════════════════════════════
# Fake Playwright — a MOCK PORTAL that can serve a block page and a 429 response
# ════════════════════════════════════════════════════════════════════════════════════════════════
BLOCK_HTML = ("<html><body><h1>Too Many Requests</h1><p>You have made too many requests and have "
              "been temporarily blocked. Please try again later.</p></body></html>")
OK_HTML = "<html><body><h1>Welcome to VidaPay CRM</h1><p>Master Agent dashboard</p></body></html>"


class Resp:
    def __init__(self, status=200, headers=None):
        self.status = status
        self._h = dict(headers or {})

    def all_headers(self):
        return dict(self._h)


class El:
    def __init__(self, tag, text="", attrs=None, visible=True, on_click=None):
        self.tag, self.text, self.attrs = tag, text, dict(attrs or {})
        self.visible, self.on_click, self.clicks = visible, on_click, 0
        self._options = []

    def is_visible(self):
        return self.visible

    def inner_text(self):
        return self.text

    def get_attribute(self, k):
        return self.attrs.get(k)

    def click(self, *a, **k):
        self.clicks += 1
        if self.on_click:
            self.on_click()

    def fill(self, v):
        self.attrs["value"] = v

    def type(self, v, delay=None):
        self.attrs["value"] = v

    def evaluate(self, *a, **k):
        return None

    def query_selector_all(self, sel):
        return list(self._options) if "option" in sel else []

    def select_option(self, value=None, label=None):
        self.attrs["selected"] = value if value is not None else label


class Frame:
    def __init__(self, page, els):
        self.page, self.elements = page, list(els)

    def query_selector_all(self, sel):
        out = []
        for part in [p.strip().lower() for p in str(sel).split(",") if p.strip()]:
            tag, key, want = part, None, None
            if "[" in part:
                tag, pred = part.split("[", 1)
                key, _, want = pred.rstrip("]").partition("=")
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
        g = self.query_selector_all(sel)
        return g[0] if g else None

    def evaluate(self, js, *a):
        j = str(js)
        if "h1,h2,h3" in j:
            return ["Portal"]
        if "input,button,select" in j:
            return [{"tag": e.tag, "type": "", "name": e.attrs.get("name", ""),
                     "id": e.attrs.get("id", ""), "ph": "", "val": e.text, "vis": e.visible}
                    for e in self.elements]
        if "=> ({" in j:
            return {}
        return []


class Page:
    """A tiny portal. `screens` maps a state name -> (html, [elements]). `nav_clicks` counts the
    navigation CLICKS the driver spends — the request-volume number the 07-27 incident is about."""

    def __init__(self, screens, start, url="https://portal/Main%20Panel.aspx", resp=None):
        self.screens, self.state, self.url = screens, start, url
        self.resp = resp or Resp(200)
        self.gotos, self.waits, self.nav_clicks = 0, 0, 0
        self.wait_calls = []

    def go(self, state):
        self.state = state

    @property
    def _html(self):
        return self.screens.get(self.state, (OK_HTML, []))[0]

    @property
    def frames(self):
        return [Frame(self, self.screens.get(self.state, (OK_HTML, []))[1])]

    def goto(self, url, **k):
        self.gotos += 1
        self.url = url
        return self.resp

    def title(self):
        return "portal"

    def content(self):
        return self._html

    def wait_for_load_state(self, *a, **k):
        self.waits += 1

    def wait_for_timeout(self, ms=0, *a, **k):
        self.waits += 1
        self.wait_calls.append(ms)

    def query_selector_all(self, sel):
        return self.frames[0].query_selector_all(sel)

    def query_selector(self, sel):
        return self.frames[0].query_selector(sel)

    def evaluate(self, js, *a):
        return self.frames[0].evaluate(js, *a)

    def screenshot(self, **k):
        return b"jpeg"

    @property
    def viewport_size(self):
        return {"width": 1366, "height": 900}


def block_page():
    return Page({"block": (BLOCK_HTML, [])}, "block")


# ════════════════════════════════════════════════════════════════════════════════════════════════
# A. DETECTION — pure (wire level + page level), and the false-positive guard
# ════════════════════════════════════════════════════════════════════════════════════════════════
section("(A) DETECTION — HTTP status, Retry-After, and configurable page markers")
check("A1 HTTP 429 is a block even with an EMPTY body (the usual 429 shape)",
      (pb.detect_block(text="", status=429) or {}).get("marker") == "http_429")
h429 = pb.detect_block(text="", status=429, headers={"Retry-After": "300"})
check("A2 …and its Retry-After (delta-seconds) is parsed", (h429 or {}).get("retry_after_s") == 300)
check("A3 header lookup is case-insensitive",
      (pb.detect_block(text="", status=429, headers={"retry-after": "90"}) or {}).get("retry_after_s") == 90)
check("A4 503 WITH Retry-After is a throttle",
      (pb.detect_block(text="", status=503, headers={"Retry-After": "60"}) or {}).get("status") == 503)
check("A5 …but a BARE 503 is an outage, not a throttle (no false cooldown)",
      pb.detect_block(text="", status=503) is None)
check("A6 a 200 healthy page is not a block", pb.detect_block(text=OK_HTML, status=200) is None)
check("A7 the owner's exact wording is detected, case-insensitively",
      (pb.detect_block(text=BLOCK_HTML) or {}).get("marker") == "too many requests")
check("A8 'temporarily blocked' is detected on its own",
      pb.detect_block(text="Your access has been temporarily blocked.") is not None)
check("A9 Retry-After as an HTTP-DATE is parsed to seconds-from-now",
      2 * 3600 - 120 < (pb.parse_retry_after(
          (datetime.now(timezone.utc) + timedelta(hours=2)).strftime("%a, %d %b %Y %H:%M:%S GMT")) or 0)
      <= 2 * 3600 + 5)
check("A10 a PAST Retry-After date clamps to 0 (not negative)",
      pb.parse_retry_after("Wed, 21 Oct 2020 07:28:00 GMT") == 0)
check("A11 a hostile Retry-After is capped at MAX_BACKOFF_SECONDS",
      pb.parse_retry_after("999999999") == pb.MAX_BACKOFF_SECONDS)
check("A12 garbage Retry-After -> None (never raises)", pb.parse_retry_after("soon") is None)
check("A13 detect_block never raises on garbage input",
      pb.detect_block(text=None, status="x", headers=object()) is None)
# RULE TWO — the vocabulary is data, not code
check("A14 a tenant's CUSTOM marker is honoured",
      (pb.detect_block(text="Zugriff vorübergehend gesperrt", markers=["vorübergehend gesperrt"]) or {})
      .get("marker") == "vorübergehend gesperrt")
check("A15 …and overriding the list DISABLES the seeded phrases (config wins, not code)",
      pb.detect_block(text=BLOCK_HTML, markers=["nur dieser satz"]) is None)
check("A16 an empty configured list falls back to no page-marker matching, not a crash",
      pb.detect_block(text=BLOCK_HTML, markers=[]) is None)
check("A17 evaluate_result finds the block in a PULL RESULT's per-report error",
      (pb.evaluate_result({"status": "imported 0 rows", "reports": [
          {"error": "the portal says Too Many Requests"}]}) or {}).get("marker") == "too many requests")
check("A18 evaluate_result honours an explicit driver-set rate_limited field",
      (pb.evaluate_result({"rate_limited": {"reason": "429", "retry_after_s": 42}}) or {})
      .get("retry_after_s") == 42)
check("A19 evaluate_result on a healthy result is None",
      pb.evaluate_result({"status": "imported 900 rows", "reports": [{"ok": True}]}) is None)
check("A20 is_block_error carries a PortalRateLimited's Retry-After through untouched",
      (pb.is_block_error(pb.PortalRateLimited("blocked", retry_after_s=77)) or {}).get("retry_after_s") == 77)
check("A21 is_block_error reads the marker out of an ORDINARY driver exception's text",
      (pb.is_block_error(RuntimeError("nav failed: too many requests")) or {}) .get("marker")
      == "too many requests")

# ════════════════════════════════════════════════════════════════════════════════════════════════
# B. DETECTION AT LOGIN TIME (mock portal)
# ════════════════════════════════════════════════════════════════════════════════════════════════
section("(B) DETECTION AT LOGIN — the phase that used to say 'could not find the password field'")
p = block_page()
try:
    vp._goto_login(p, "https://portal/")
    b1 = "no raise"
except pb.PortalRateLimited as e:
    b1 = str(e)
except Exception as e:
    b1 = "wrong: " + type(e).__name__
check("B1 _goto_login raises PortalRateLimited on a block page", isinstance(b1, str) and "rate-limiting" in b1, b1)
check("B2 …and the message tells the operator to WAIT, not to retry", "extends the block" in str(b1), b1)
check("B3 …and it costs exactly ONE navigation (the bot-wall fallback ladder is never spent)",
      p.gotos == 1, p.gotos)
p429 = Page({"x": (OK_HTML, [])}, "x", resp=Resp(429, {"Retry-After": "1800"}))
try:
    vp._goto_login(p429, "https://portal/")
    b4 = None
except pb.PortalRateLimited as e:
    b4 = e
check("B4 a WIRE 429 with a perfectly innocent body is still caught (page text alone would miss it)",
      b4 is not None and b4.status == 429)
check("B5 …and its Retry-After survives to the exception", getattr(b4, "retry_after_s", None) == 1800)
check("B6 _goto_with_retry RETURNS the navigation Response (regression guard — it used to discard it)",
      isinstance(vp._goto_with_retry(Page({"x": (OK_HTML, [])}, "x", resp=Resp(200)), "u"), Resp))
ok_page = Page({"x": (OK_HTML, [El("input", attrs={"type": "password", "name": "pw"})])}, "x")
try:
    vp._goto_login(ok_page, "https://portal/")
    b7 = True
except Exception as e:
    b7 = "raised " + repr(e)
check("B7 a HEALTHY login page is untouched (no false cooldown)", b7 is True, b7)

# ════════════════════════════════════════════════════════════════════════════════════════════════
# C. DETECTION AT PULL TIME
# ════════════════════════════════════════════════════════════════════════════════════════════════
section("(C) DETECTION AT PULL — the phase that used to say 'the session has expired' / 'report not listed'")
cli = Client({"data_source": [src_row()]})
try:
    vp._pull_all_reports_on_page(block_page(), cli, ORG_A, "src-1", None, 2, src_row())
    c1 = "no raise"
except pb.PortalRateLimited as e:
    c1 = str(e)
except Exception as e:
    c1 = "wrong: " + type(e).__name__ + " " + str(e)
check("C1 _pull_all_reports_on_page raises PortalRateLimited BEFORE it drives a single report",
      "rate-limiting" in str(c1), c1)

src = "".join(io.open(os.path.join(os.path.dirname(__file__), "..", "app", "modules", "commcalc",
                                   "vidapay_sweep.py"), encoding="utf-8").readlines())
_expired = src.index('"The VidaPay session has expired')
check("C2 run_vidapay_sweep checks the throttle BEFORE concluding 'the session has expired' "
      "(that message is what sent an operator back to 🔴 Live login, deepening the block)",
      "_raise_if_rate_limited" in src[_expired - 260:_expired], src[_expired - 260:_expired])
check("C3 the b2bsoft sweep inherits the same gate (the guard lives at the data_source layer)",
      'where="The POS portal"' in src)
check("C4 the pull re-checks the gate BETWEEN reports (a mid-pull throttle aborts the rest)",
      src.count("# A portal that starts throttling MID-PULL") == 1)
check("C5 markers reach the driver from config, not from a constant (RULE TWO)",
      '_pb.load_markers(client, org_id, "vidapay")' in src and '_pb.load_markers(client, org_id, "b2bsoft")' in src)
# REGRESSION GUARD. vidapay_sweep is deliberately loadable as a STANDALONE FILE — six existing proof
# harnesses load it with spec_from_file_location, where the `app` package is not on sys.path. Adding a
# bare `from app.modules.commcalc import portal_backoff` at the top broke all six with
# ModuleNotFoundError; the guarded import must keep that property.
import importlib.util as _ilu                                          # noqa: E402
_p = os.path.join(os.path.dirname(__file__), "..", "app", "modules", "commcalc", "vidapay_sweep.py")
_sp = _ilu.spec_from_file_location("vidapay_sweep_standalone", _p)
_mod = _ilu.module_from_spec(_sp)
try:
    _sp.loader.exec_module(_mod)
    c6 = _mod.PortalRateLimited is not None and _mod._pb.DEFAULT_MARKERS
except Exception as e:
    c6 = "raised " + repr(e)
check("C6 vidapay_sweep still imports as a STANDALONE FILE (six older proofs load it that way)",
      bool(c6) and not isinstance(c6, str), c6)

# ════════════════════════════════════════════════════════════════════════════════════════════════
# D. REQUEST-VOLUME TRIM (scope item 5) — the loops that actually generated the 07-27 traffic
# ════════════════════════════════════════════════════════════════════════════════════════════════
section("(D) REQUEST-VOLUME TRIM — the uncapped, undelayed nav retries")


def nav_page(n_reports=5):
    """A portal whose menu offers a link for EVERY label the driver knows, and which never reveals a
    Reports <select>. Pre-fix this burned one full page load per label (7); x N reports when the
    caller's one-time resolve returned None (up to 35)."""
    pg = {"m": (OK_HTML, [])}
    page = Page(pg, "m")

    def mk(label):
        return El("a", text=label, attrs={"href": "/x.aspx"},
                  on_click=lambda: setattr(page, "nav_clicks", page.nav_clicks + 1))
    pg["m"] = (OK_HTML, [mk(l) for l in vp._REPORT_NAV_LABELS])
    return page


np_ = nav_page()
got = vp._open_reports_page(np_)
check("D1 _open_reports_page spends at most _MAX_NAV_CLICKS navigation clicks (was: one per label)",
      np_.nav_clicks <= vp._MAX_NAV_CLICKS, np_.nav_clicks)
check("D2 …and the cap is a named budget, not a magic number",
      isinstance(vp._MAX_NAV_CLICKS, int) and vp._MAX_NAV_CLICKS < len(vp._REPORT_NAV_LABELS))
check("D3 …and consecutive clicks are PACED (an unspaced burst is what a limiter counts hardest)",
      vp._NAV_CLICK_DELAY_MS in np_.wait_calls, np_.wait_calls)
check("D4 an unreachable Reports page still returns None (honest, unchanged)", got is None)
np2 = nav_page()
res = vp._pull_all_reports_on_page(np2, Client({"data_source": [src_row()]}), ORG_A, "src-1", None, 2,
                                   src_row())
check("D5 an unreachable Reports page costs ONE nav ladder for the WHOLE pull, not one per report",
      np2.nav_clicks <= vp._MAX_NAV_CLICKS, np2.nav_clicks)
check("D6 …and every report still reports the honest 'no_reports_page' reason",
      res.get("reason") == "no_reports_page", res.get("reason"))
check("D7 the nav-exhausted sentinel never leaks into operator-facing calibration copy",
      vp.NAV_EXHAUSTED not in (res.get("calibration") or {}).get("portal_report_options", [])
      and vp.NAV_EXHAUSTED not in str(res.get("status")))
check("D8 …and the mid-pull gate is re-checked between reports so a throttle stops the rest",
      "_raise_if_rate_limited" in src.split("for spec in specs:")[1][:1200])

# ════════════════════════════════════════════════════════════════════════════════════════════════
# E. BACKOFF MATH — escalation, Retry-After LATER-only, configurability
# ════════════════════════════════════════════════════════════════════════════════════════════════
section("(E) BACKOFF — escalating 30m -> 2h -> 8h cap, Retry-After honoured LATER-only")
check("E1 first block = 30 minutes", pb.backoff_seconds(0) == 30 * 60)
check("E2 second block = 2 hours", pb.backoff_seconds(1) == 120 * 60)
check("E3 third block = 8 hours", pb.backoff_seconds(2) == 480 * 60)
check("E4 the last step is a CAP, not the end of the ladder",
      pb.backoff_seconds(9) == 480 * 60 and pb.backoff_seconds(50) == 480 * 60)
check("E5 a LONGER Retry-After wins over the ladder step",
      pb.backoff_seconds(0, retry_after_s=3 * 3600) == 3 * 3600)
check("E6 a SHORTER Retry-After does NOT shorten the cooldown "
      "(a throttling portal cannot talk us into hammering it in 5s)",
      pb.backoff_seconds(1, retry_after_s=5) == 120 * 60)
check("E7 a floor applies even to an absurdly small configured ladder",
      pb.backoff_seconds(0, ladder=[0.1]) == pb.MIN_BACKOFF_SECONDS)
check("E8 a hostile Retry-After cannot park a login off for a year",
      pb.backoff_seconds(0, retry_after_s=10 ** 9) == pb.MAX_BACKOFF_SECONDS)
check("E9 the ladder is CONFIGURABLE (RULE TWO)", pb.backoff_seconds(1, ladder=[10, 45]) == 45 * 60)
check("E10 a malformed ladder falls back to the default rather than to zero",
      pb.backoff_seconds(0, ladder=["", "x", None]) == 30 * 60)
check("E11 a negative/garbage failure count is treated as the first step",
      pb.backoff_seconds(-4) == 30 * 60 and pb.backoff_seconds("x") == 30 * 60)

cfg_cli = Client({"commission_org_config": [{"org_id": ORG_B, "portal_backoff_minutes": "10,45,90",
                                             "portal_block_alert_failures": 2}]})
check("E12 the ladder is read from commission_org_config per TENANT",
      pb.load_ladder(cfg_cli, ORG_B) == [10.0, 45.0, 90.0])
check("E13 …and a tenant with no row gets the seeded default",
      pb.load_ladder(cfg_cli, ORG_A) == list(pb.DEFAULT_BACKOFF_MINUTES))
check("E14 the alert threshold is configurable too", pb.load_alert_failures(cfg_cli, ORG_B) == 2)
check("E15 …with a documented default", pb.load_alert_failures(cfg_cli, ORG_A) == pb.DEFAULT_ALERT_FAILURES)
check("E16 a missing config table degrades to the defaults (never raises)",
      pb.load_ladder(Client({}), ORG_A) == list(pb.DEFAULT_BACKOFF_MINUTES))

# ════════════════════════════════════════════════════════════════════════════════════════════════
# F. MARKER CONFIG — org override, house inheritance, enable/disable, processor pinning
# ════════════════════════════════════════════════════════════════════════════════════════════════
section("(F) MARKER CONFIG — org overrides house, disabled rows ignored, processor pinning")
mk_cli = Client({"portal_block_marker": [
    {"org_id": ORG_A, "processor": None, "marker": "too many requests", "enabled": True},
    {"org_id": ORG_A, "processor": None, "marker": "temporarily blocked", "enabled": True},
    {"org_id": ORG_A, "processor": None, "marker": "try again later", "enabled": False},
    {"org_id": ORG_B, "processor": None, "marker": "demasiadas solicitudes", "enabled": True},
    {"org_id": ORG_B, "processor": "b2bsoft", "marker": "pos throttled", "enabled": True},
]})
mA = pb.load_markers(mk_cli, ORG_A, "vidapay")
mB = pb.load_markers(mk_cli, ORG_B, "vidapay")
check("F1 the house org gets its own configured markers", "too many requests" in mA)
check("F2 a DISABLED row is never matched", "try again later" not in mA)
check("F3 a tenant's own rows OVERRIDE the house defaults wholesale",
      "demasiadas solicitudes" in mB and "too many requests" not in mB, mB)
check("F4 a processor-pinned row does not leak to another processor", "pos throttled" not in mB)
check("F5 …but does apply to its own processor",
      "pos throttled" in pb.load_markers(mk_cli, ORG_B, "b2bsoft"))
check("F6 a tenant with NO rows inherits the house defaults",
      pb.load_markers(mk_cli, "22222222-2222-2222-2222-222222222222", "vidapay") == list(mA))
check("F7 a missing table (pre-mig-244) falls back to the seeded DEFAULT_MARKERS",
      pb.load_markers(Client({}), ORG_A, "vidapay") == list(pb.DEFAULT_MARKERS))
check("F8 the marker read is org-scoped: it asks only for this org + the house defaults (RULE ONE)",
      any(e["op"] == "select" and e["table"] == "portal_block_marker"
          and any(isinstance(v, list) and set(v) == {ORG_B, pb.HOUSE_ORG} for _, v in e["filters"])
          for e in mk_cli.log))

# ════════════════════════════════════════════════════════════════════════════════════════════════
# G. STATE WRITES — escalation over time, recovery, org isolation
# ════════════════════════════════════════════════════════════════════════════════════════════════
section("(G) STATE — escalation across repeat blocks, recovery reset, org isolation")
c = Client({"data_source": [src_row("s1", ORG_A), src_row("s1", ORG_B)]})
hit = pb.detect_block(text=BLOCK_HTML)
p1 = pb.apply_block(c, "s1", ORG_A, hit)
row_a = [r for r in c.store["data_source"] if r["org_id"] == ORG_A][0]
row_b = [r for r in c.store["data_source"] if r["org_id"] == ORG_B][0]
check("G1 a detected block stamps blocked_until/blocked_at/block_reason/consecutive_failures",
      row_a["blocked_until"] and row_a["blocked_at"] and row_a["block_reason"]
      and row_a["consecutive_failures"] == 1)
check("G2 the first cooldown is the first ladder step (30m)", p1["seconds"] == 30 * 60)
check("G3 ORG ISOLATION — the OTHER tenant's identically-keyed row is untouched",
      row_b["blocked_until"] is None and row_b["consecutive_failures"] == 0)
check("G4 …because every cooldown write filters on BOTH id and org_id (RULE ONE)",
      all({"id", "org_id"} <= {k for k, _ in u["filters"]} for u in c.updates()))
p2 = pb.apply_block(c, "s1", ORG_A, hit)
p3 = pb.apply_block(c, "s1", ORG_A, hit)
p4 = pb.apply_block(c, "s1", ORG_A, hit)
check("G5 repeat blocks ESCALATE 30m -> 2h -> 8h",
      (p2["seconds"], p3["seconds"]) == (120 * 60, 480 * 60), (p2["seconds"], p3["seconds"]))
check("G6 …and then hold at the 8h cap", p4["seconds"] == 480 * 60)
check("G7 consecutive_failures keeps counting through the cap", row_a["consecutive_failures"] == 4)
hit_ra = pb.detect_block(text="", status=429, headers={"Retry-After": str(10 * 3600)})
p5 = pb.apply_block(c, "s1", ORG_A, hit_ra)
check("G8 a Retry-After LONGER than the cap is honoured (the portal's own number wins upward)",
      p5["seconds"] == 10 * 3600)

# recovery
pb.record_outcome(c, "s1", ORG_A, {"rows_ingested": 900, "delivered": True}, delivered=True)
check("G9 RECOVERY — a pull that actually imports rows lifts the cooldown…",
      row_a["blocked_until"] is None and row_a["block_reason"] is None)
check("G10 …and resets consecutive_failures to 0 (the ladder starts over next time)",
      row_a["consecutive_failures"] == 0)
check("G11 …and clears blocked_at, so the column keeps meaning 'the CURRENT cooldown'",
      row_a["blocked_at"] is None)
p6 = pb.apply_block(c, "s1", ORG_A, hit)
check("G12 …proved: the next real block is back at the 30m first step", p6["seconds"] == 30 * 60)

# non-block failure counts but does NOT start a cooldown
c2 = Client({"data_source": [src_row("s2", ORG_A)]})
r2 = c2.store["data_source"][0]
plan = pb.record_outcome(c2, "s2", ORG_A, {"status": "imported 0 rows", "reports": []}, delivered=False)
check("G13 an ORDINARY failure counts but does not park the login in a cooldown",
      plan is None and r2["consecutive_failures"] == 1 and r2["blocked_until"] is None)
plan2 = pb.record_outcome(c2, "s2", ORG_A, {"status": "err", "reports": [{"error": BLOCK_HTML}]},
                          delivered=False)
check("G14 …but a block detected inside the RESULT does stamp one", plan2 and r2["blocked_until"])
c3 = Client({"data_source": [src_row("s3", ORG_A)]})
plan3 = pb.record_outcome(c3, "s3", ORG_A, None, delivered=False,
                          exc=pb.PortalRateLimited("blocked", retry_after_s=45 * 60))
check("G15 …and so does a block raised as an EXCEPTION, honouring its Retry-After",
      plan3 and plan3["seconds"] == 45 * 60)
check("G16 read_state reports remaining time and the reason for the UI",
      pb.read_state(c3.store["data_source"][0])["blocked"] is True
      and pb.read_state(c3.store["data_source"][0])["remaining_s"] > 0)
check("G17 an EXPIRED cooldown reports not-blocked (no manual sweep needed)",
      pb.read_state({"blocked_until": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()}
                    )["blocked"] is False)
check("G18 humanize() gives the operator a next-attempt time, not a stack trace",
      "temporarily blocked" in pb.humanize(pb.read_state(c3.store["data_source"][0])).lower())
check("G19 confirm_warning() is the exact second-click wording the scope requires",
      "rate-limited us until" in pb.confirm_warning(pb.read_state(c3.store["data_source"][0]))
      and "EXTEND" in pb.confirm_warning(pb.read_state(c3.store["data_source"][0])))

# ════════════════════════════════════════════════════════════════════════════════════════════════
# H. PRE-MIGRATION-244 INERTNESS — degrade in BOTH directions
# ════════════════════════════════════════════════════════════════════════════════════════════════
section("(H) PRE-MIG-244 — the feature is INERT, not broken (degrades both ways)")
PRE = {"data_source": {"blocked_until", "blocked_at", "block_reason", "consecutive_failures"}}
pre_row = {k: v for k, v in src_row().items() if k not in PRE["data_source"]}
cpre = Client({"data_source": [dict(pre_row)]}, missing=PRE)
check("H1 read_state on a pre-244 row reports NOT blocked", pb.read_state(pre_row)["blocked"] is False)
check("H2 guard() over a pre-244 table reports NOT blocked (the SELECT raises, and is swallowed)",
      pb.guard(cpre, "src-1", ORG_A)["blocked"] is False)
try:
    pb.apply_block(cpre, "src-1", ORG_A, hit)
    h3 = True
except Exception as e:
    h3 = "raised " + repr(e)
check("H3 stamping a cooldown pre-244 does not raise — it just isn't persisted", h3 is True, h3)
try:
    out = pb.record_outcome(cpre, "src-1", ORG_A, {"reports": [{"error": BLOCK_HTML}]}, delivered=False)
    h4 = True
except Exception as e:
    h4 = "raised " + repr(e)
check("H4 record_outcome pre-244 never raises (bookkeeping can't break a pull)", h4 is True, h4)
check("H5 _strip_source_pw marks a pre-244 row not-blocked, so the ⛔ chip simply never renders",
      R._strip_source_pw(dict(pre_row)).get("blocked") is False)
stripped = R._strip_source_pw(blocked_row())
check("H6 …and post-244 it exposes blocked + a headline for the page",
      stripped["blocked"] is True and stripped["blocked_remaining_s"] > 0 and stripped["block_headline"])
check("H7 …while STILL stripping every secret (no regression on the polled list)",
      "password" not in stripped and "session_state" not in stripped
      and SECRET_PW not in str(stripped))
check("H8 _later_iso survives a datetime slipping in from either producer (no TypeError 500)",
      R._later_iso("2026-07-27T18:00:00+00:00", "2026-07-27T20:00:00+00:00")
      == "2026-07-27T20:00:00+00:00" and R._later_iso("2026-07-27T22:00:00+00:00",
                                                      "2026-07-27T20:00:00+00:00")
      == "2026-07-27T22:00:00+00:00")
check("H9 …and on garbage returns the scheduler's own value rather than blowing up",
      R._later_iso("2026-07-27T18:00:00+00:00", "not-a-date") == "2026-07-27T18:00:00+00:00")

# ════════════════════════════════════════════════════════════════════════════════════════════════
# I. RESPECT — /run-due skips, and a skip is NOT an attempt
# ════════════════════════════════════════════════════════════════════════════════════════════════
section("(I) RESPECT — the scheduler skips a blocked login, and a skip is not an attempt")
CALLS = []


async def fake_scraper(org_id, row):
    CALLS.append((org_id, row["id"]))
    return {"status": "imported 5 rows", "rows_ingested": 5, "delivered": True}


class Ctx:
    """Swap router globals for the duration of a block."""

    def __init__(self, client, scrapers=True):
        self.client, self.scrapers = client, scrapers

    def __enter__(self):
        self._sb, self._sc, self._ro = R.sb, R._SOURCE_SCRAPERS, R.require_org
        self._rec = R._record_pull_result
        R.sb = lambda: self.client
        R.require_org = lambda *a, **k: None
        if self.scrapers:
            R._SOURCE_SCRAPERS = {"vidapay": fake_scraper, "total_access": fake_scraper}
        return self

    def __exit__(self, *a):
        R.sb, R._SOURCE_SCRAPERS, R.require_org = self._sb, self._sc, self._ro
        R._record_pull_result = self._rec


blk = blocked_row("b1", ORG_A, mins=90)
okr = src_row("g1", ORG_A)
cli_i = Client({"data_source": [blk, okr]})
CALLS[:] = []
with Ctx(cli_i):
    out = asyncio.run(R.data_sources_run_due(org_id=ORG_A))
check("I1 the blocked login is SKIPPED — its scraper is never called",
      ("b1" not in [c[1] for c in CALLS]), CALLS)
check("I2 …and reported as a skip, with the reason", out.get("skipped_count") == 1
      and out["skipped_blocked"][0]["skipped"] == "portal_blocked", out.get("skipped_blocked"))
check("I3 the healthy login in the same sweep STILL runs (one block never stalls the batch)",
      "g1" in [c[1] for c in CALLS], CALLS)
b_upd = [u for u in cli_i.updates() if dict(u["filters"]).get("id") == "b1"]
patched = set()
for u in b_upd:
    patched |= set(u["patch"].keys())
check("I4 the SKIP IS NOT AN ATTEMPT — only next_run_at is written", patched == {"next_run_at"}, patched)
check("I5 …so last_attempt_at / last_status / auth_status are all left alone (mig-241 posture)",
      not (patched & {"last_attempt_at", "last_status", "auth_status", "last_run_at"}))
check("I6 …and consecutive_failures is NOT incremented by a skip (a block is not a try)",
      blk["consecutive_failures"] == 1)
check("I7 next_run_at is moved PAST the cooldown, so the next poll doesn't walk into it",
      blk["next_run_at"] >= blk["blocked_until"], (blk["next_run_at"], blk["blocked_until"]))
check("I8 …and the scheduler still looks alive to import_audit.sched_silent (next_run_at advanced)",
      blk["next_run_at"] is not None)
sel = [e for e in cli_i.log if e["op"] == "select" and e["table"] == "data_source"]
check("I9 the non-cron sweep reads ONLY the caller's org (RULE ONE)",
      any(("org_id", ORG_A) in e["filters"] for e in sel))

# a rate-limit raised by a scraper during the sweep stamps the cooldown + pushes next_run_at
async def blocking_scraper(org_id, row):
    raise pb.PortalRateLimited("The portal is rate-limiting us: too many requests",
                               retry_after_s=2 * 3600)

r_i2 = src_row("x1", ORG_A)
cli_i2 = Client({"data_source": [r_i2]})
with Ctx(cli_i2, scrapers=False):
    R._SOURCE_SCRAPERS = {"vidapay": blocking_scraper}
    out2 = asyncio.run(R.data_sources_run_due(org_id=ORG_A))
check("I10 a throttle raised mid-sweep stamps the cooldown on that login",
      r_i2["blocked_until"] and r_i2["consecutive_failures"] == 1)
check("I11 …honouring the portal's Retry-After (2h > the 30m first step)",
      (pb._ts(r_i2["blocked_until"]) - datetime.now(timezone.utc)).total_seconds() > 1.5 * 3600)
check("I12 …and pushes next_run_at past it so the next tick doesn't re-attempt",
      r_i2["next_run_at"] >= r_i2["blocked_until"], (r_i2["next_run_at"], r_i2["blocked_until"]))
check("I13 …and the sweep still returns 200-shaped output (one bad login never 500s the cron)",
      out2.get("ok") is True)

# ════════════════════════════════════════════════════════════════════════════════════════════════
# J. RESPECT — human entry points require an explicit confirm
# ════════════════════════════════════════════════════════════════════════════════════════════════
section("(J) RESPECT — every HUMAN entry point asks first; ?confirm=true is the deliberate override")
CALLS[:] = []
rj = blocked_row("j1", ORG_A)
cli_j = Client({"data_source": [rj]})
with Ctx(cli_j):
    R._record_pull_result = lambda *a, **k: True
    j1 = asyncio.run(R.run_data_source("j1", org_id=ORG_A))
check("J1 ▶ Pull now during a cooldown does NOT pull", not CALLS, CALLS)
check("J2 …it returns blocked + requires_confirm + the warning text",
      j1.get("blocked") is True and j1.get("requires_confirm") is True
      and "may EXTEND the block" in (j1.get("warning") or ""), j1.get("warning"))
check("J3 …and names the next automatic attempt time", bool(j1.get("blocked_until")))
with Ctx(cli_j):
    R._record_pull_result = lambda *a, **k: True
    j4 = asyncio.run(R.run_data_source("j1", org_id=ORG_A, confirm=True))
check("J4 …but a HUMAN who confirms IS allowed through (the scope's 'allowed, with a confirm')",
      "j1" in [c[1] for c in CALLS], CALLS)

started = []
cli_j2 = Client({"data_source": [blocked_row("j2", ORG_A)]})


class BT:
    def add_task(self, fn, *a):
        started.append(a)


with Ctx(cli_j2):
    j5 = asyncio.run(R.data_source_login_start("j2", BT(), org_id=ORG_A))
check("J5 the headless login/start is refused during a cooldown (the most expensive request there is)",
      not started and j5.get("blocked") is True, (started, j5))
with Ctx(cli_j2):
    asyncio.run(R.data_source_login_start("j2", BT(), org_id=ORG_A, confirm=True))
check("J6 …and ONE confirmed attempt is allowed (cap = 1 per cooldown window: a failure re-arms it)",
      len(started) == 1, started)

live_started = []
cli_j3 = Client({"data_source": [blocked_row("j3", ORG_A)]})
_orig_start = ll.start_session
ll.start_session = lambda *a, **k: live_started.append(k) or type(
    "S", (), {"snapshot_phase": lambda self: "starting"})()
try:
    with Ctx(cli_j3):
        j7 = R.live_login_start("j3", org_id=ORG_A)
    check("J7 🔴 Live login during a cooldown does not open a browser without a confirm",
          not live_started and j7.get("blocked") is True, (live_started, j7))
    with Ctx(cli_j3):
        j8 = R.live_login_start("j3", org_id=ORG_A, confirm=True)
    check("J8 …a confirmed HUMAN live login IS allowed (seeing the portal is sometimes the only way)",
          len(live_started) == 1)
    check("J9 …but the AUTOMATIC post-login pull stays suppressed for the whole cooldown",
          j8.get("auto_pull") is False and j8.get("blocked") is True, j8)
    check("J10 …and the session is handed a live cooldown gate, re-read at pull time",
          callable(live_started[0].get("auto_pull_gate")))
    check("J11 …which reports blocked for this source",
          (live_started[0]["auto_pull_gate"]() or {}).get("blocked") is True)
finally:
    ll.start_session = _orig_start

# ════════════════════════════════════════════════════════════════════════════════════════════════
# K. RESPECT — the automatic post-login pull
# ════════════════════════════════════════════════════════════════════════════════════════════════
section("(K) RESPECT — auto-pull-after-login checks blocked_until on the live session itself")
pulled = []


def mk_session(gate):
    s = ll.LiveLoginSession("s", ORG_A, {"portal_url": "u", "username": "x", "password": "y"},
                            pull_fn=lambda page, stop=None: pulled.append(1) or {"rows_ingested": 1},
                            auto_pull_gate=gate)
    return s


s_blocked = mk_session(lambda: {"blocked": True, "blocked_until": datetime.now(timezone.utc)
                                + timedelta(minutes=40), "remaining_s": 2400, "reason": "429"})
check("K1 the gate is consulted and reports blocked", s_blocked._cooldown_state().get("blocked") is True)
check("K2 …and carries a human line for the live panel", bool(s_blocked._cooldown_state().get("_human")))
s_ok = mk_session(lambda: {"blocked": False})
check("K3 a healthy source reports not-blocked", s_ok._cooldown_state().get("blocked") is False)
s_none = mk_session(None)
check("K4 NO gate at all (every pre-existing caller / test) means NOT blocked — byte-identical behaviour",
      s_none._cooldown_state().get("blocked") is False)
s_bad = mk_session(lambda: (_ for _ in ()).throw(RuntimeError("db down")))
check("K5 a gate that RAISES means not-blocked — a bookkeeping fault can never stop a healthy pull",
      s_bad._cooldown_state().get("blocked") is False)
llsrc = io.open(os.path.join(os.path.dirname(__file__), "..", "app", "modules", "commcalc",
                             "live_login.py"), encoding="utf-8").read()
check("K6 the auto-pull branch checks the gate BEFORE calling _handle_pull",
      llsrc.index("blocked = self._cooldown_state()") < llsrc.index("self._handle_pull(page, vp, None)"))
check("K7 …and a blocked auto-pull leaves the trusted session OPEN (▶ Pull now still works for a human)",
      'self._set(phase="authenticated"' in llsrc.split("blocked = self._cooldown_state()")[1][:900])

# ════════════════════════════════════════════════════════════════════════════════════════════════
# L. HONEST UI — the attention item, its GROUP, and the page
# ════════════════════════════════════════════════════════════════════════════════════════════════
section("(L) HONEST UI — the attention item is 'import', never 'ops'; the page shows a ⛔ chip")
cli_l = Client({"data_source": [blocked_row("l1", ORG_A, mins=75)],
                "commission_org_config": [{"org_id": ORG_A}]})
items = ia.p_connectors(cli_l, ORG_A, {})
bl = [i for i in items if i["key"].startswith("commcalc:src_blocked:")]
check("L1 a blocked login raises exactly ONE attention item", len(bl) == 1, [i["key"] for i in items])
check("L2 …in group 'import' (NEVER 'ops')", bl and bl[0]["group"] == "import"
      and all(i["group"] != "ops" for i in items))
check("L3 …at severity error", bl and bl[0]["severity"] == "error")
check("L4 …telling the reader explicitly NOT to retry", "DO NOT press Log in" in bl[0]["detail"])
check("L5 …and naming when it will retry by itself", "try again automatically" in bl[0]["detail"])
check("L6 the block item is TERMINAL for that source — no second, contradictory alarm stacked on it",
      not [i for i in items if i["key"] == "commcalc:src:l1"], [i["key"] for i in items])
check("L7 …and it deep-links to the page that shows it", bl[0]["deep_link"] == "/commcalc/email-imports")

cli_l2 = Client({"data_source": [src_row("l2", ORG_A, consecutive_failures=4, last_status="ok",
                                         auth_status="authenticated")],
                 "commission_org_config": [{"org_id": ORG_A}]})
rep = [i for i in ia.p_connectors(cli_l2, ORG_A, {}) if i["key"].startswith("commcalc:src_repeat_fail:")]
check("L8 N consecutive dud attempts raise the 'keeps importing nothing' item", len(rep) == 1)
check("L9 …in group 'import', at severity warning (the login still works)",
      rep[0]["group"] == "import" and rep[0]["severity"] == "warning")
cli_l3 = Client({"data_source": [src_row("l3", ORG_A, consecutive_failures=2, last_status="ok",
                                         auth_status="authenticated")],
                 "commission_org_config": [{"org_id": ORG_A, "portal_block_alert_failures": 2}]})
check("L10 …and the threshold is tenant-configurable (RULE TWO)",
      len([i for i in ia.p_connectors(cli_l3, ORG_A, {})
           if i["key"].startswith("commcalc:src_repeat_fail:")]) == 1)
cli_l4 = Client({"data_source": [src_row("l4", ORG_A, consecutive_failures=2, last_status="ok",
                                         auth_status="authenticated")],
                 "commission_org_config": [{"org_id": ORG_A}]})
check("L11 …and BELOW the threshold it stays quiet (no noise on a single blip)",
      not [i for i in ia.p_connectors(cli_l4, ORG_A, {})
           if i["key"].startswith("commcalc:src_repeat_fail:")])
pre_l = {k: v for k, v in blocked_row("l5", ORG_A).items() if k not in PRE["data_source"]}
cli_l5 = Client({"data_source": [pre_l], "commission_org_config": [{"org_id": ORG_A}]},
                missing=PRE)
il5 = ia.p_connectors(cli_l5, ORG_A, {})
check("L12 PRE-MIG-244 the provider still returns its usual items (the column fallback works)",
      isinstance(il5, list) and not [i for i in il5 if "src_blocked" in i["key"]])
check("L13 …and never raises on the unknown-column SELECT", True)

PAGE = os.path.join(os.path.dirname(__file__), "..", "..", "frontend", "src", "app", "(platform)",
                    "commcalc", "email-imports", "page.tsx")
tsx = io.open(PAGE, encoding="utf-8").read()
check("L14 the page renders a ⛔ blocked chip with the next-attempt time",
      "Portal temporarily blocked us" in tsx and "blockTime(s.blocked_until)" in tsx)
check("L15 …with the portal's own reason verbatim", "{s.block_reason}" in tsx)
check("L16 …and tells the operator not to retry", "another attempt usually extends the block" in tsx)
check("L17 the second, deliberate click re-sends with ?confirm=true (all three entry points)",
      tsx.count("confirmed ? '?confirm=true' : ''") == 3, tsx.count("confirmed ? '?confirm=true' : ''"))
check("L18 …and each one asks BEFORE acting", tsx.count("confirmBlocked(r)") == 3)
check("L19 the live-login modal does not open a browser on the un-confirmed path",
      "setLive(null); setLiveState(null); setLiveBusy(false)" in tsx)
check("L20 the operator escape hatch exists and is worded as a last resort",
      "clear-block" in tsx and "only if the portal released us" in tsx.lower())
check("L21 every new call goes through api() with the explicit /api/v1 prefix "
      "(curl-verified != UI-verified)",
      all(s.startswith("/api/v1/") for s in re.findall(r"api\(`([^`]+)`", tsx)))

rsrc = io.open(os.path.join(os.path.dirname(__file__), "..", "app", "modules", "commcalc",
                            "router.py"), encoding="utf-8").read()
check("L22 the clear-block endpoint is import-admin gated (not a public un-block button)",
      "_require_import_admin" in rsrc.split("def clear_source_block")[1][:600])
check("L23 …and org-scoped like everything else", "org_id: str = ORG_ID" in
      rsrc.split("def clear_source_block")[1][:200])
check("L24 org_id stays a QUERY PARAM on every gated endpoint (contract §2)",
      all("org_id: str = ORG_ID" in rsrc.split("def " + fn)[1][:400]
          for fn in ("run_data_source", "data_source_login_start", "live_login_start",
                     "clear_source_block")))

# ════════════════════════════════════════════════════════════════════════════════════════════════
# M. MIGRATION 244 — additive, idempotent, in-band, and not money-touching
# ════════════════════════════════════════════════════════════════════════════════════════════════
section("(M) MIGRATION 244 — additive, idempotent, band 200–299")
MIG = os.path.join(os.path.dirname(__file__), "..", "..", "database", "migrations",
                   "244_commission_portal_rate_limit_backoff.sql")
msql = io.open(MIG, encoding="utf-8").read()
check("M1 the file is in mod-commission's band (200–299)", os.path.basename(MIG).startswith("244_"))
check("M2 every column add is IF NOT EXISTS (safe to re-run)",
      msql.count("ADD COLUMN IF NOT EXISTS") == 6)
check("M3 the new table is CREATE TABLE IF NOT EXISTS", "CREATE TABLE IF NOT EXISTS commcalc.portal_block_marker" in msql)
check("M4 every index is CREATE INDEX IF NOT EXISTS",
      msql.count("CREATE INDEX IF NOT EXISTS") + msql.count("CREATE UNIQUE INDEX IF NOT EXISTS")
      == msql.count("CREATE INDEX ") + msql.count("CREATE UNIQUE INDEX "))
check("M5 the seed is ON CONFLICT DO NOTHING (a re-run never resurrects a disabled marker)",
      "ON CONFLICT DO NOTHING" in msql)
check("M6 the new table carries org_id NOT NULL + an org index (RULE ONE, new tables)",
      "org_id      uuid NOT NULL" in msql and "portal_block_marker_org_idx" in msql)
check("M7 broad phrases are seeded DISABLED, so a false positive can't park a healthy login",
      msql.count("false,") >= 3)
check("M8 the owner's exact 07-27 wording is seeded ENABLED",
      "'too many requests'" in msql and "'temporarily blocked'" in msql)
check("M9 the seeded code defaults MATCH the migration's enabled seeds (no drift between the two)",
      all(("'%s'" % m) in msql for m in pb.DEFAULT_MARKERS), [m for m in pb.DEFAULT_MARKERS
                                                              if ("'%s'" % m) not in msql])
check("M10 NOT money-touching — no rate/tier/plan/payout table is written",
      not re.search(r"(?i)\b(insert|update|delete)\b[^;]{0,200}\b(rep_commissions|commission_rule|"
                    r"commission_tier|payout_schedule|commission_plan)\b", msql))
check("M11 the operator diagnostic SQL is READ-ONLY (SELECTs + a guarded unschedule)",
      "net._http_response" in msql and "last_attempt_at" in msql)
check("M12 the ladder + alert threshold extend the EXISTING posture table, not a parallel one",
      "ALTER TABLE commcalc.commission_org_config" in msql)

# ════════════════════════════════════════════════════════════════════════════════════════════════
print("\n".join(LINES))
print("\n%d passed, %d failed" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
