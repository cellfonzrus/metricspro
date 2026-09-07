"""Adversarial proof harness — SSRF guard (finding C4) + fail-closed import gate (finding H3).

Runs the ACTUAL shipped code (app.modules.commcalc.url_guard / vidapay_sweep / epay_sweep /
router) against fakes — no DB, no network, no browser. Run from backend/:

    python3 harness_ssrf_import_gate.py

THREAT MODEL
------------
C4: a TENANT ADMIN (or, before H3 was fixed, ANY unauthenticated request) writes a `portal_url` /
`proxy_url` into an import channel, then reads back what the headless `--no-sandbox` Chromium
rendered — via `/login/screenshot`, the `auth_message` snapshot, the pull diagnostic, or the
live-login JPEG screencast. `file:///app/.env` yields SUPABASE_SERVICE_KEY (RLS bypass),
FIELD_ENCRYPTION_KEY (all employee PII) and ANTHROPIC_API_KEY; `http://169.254.169.254/…` yields
cloud IAM credentials.

H3: `_require_import_admin` wrapped everything in try/except and RETURNED (= allowed) whenever the
caller could not be resolved, so the ten endpoints that own those very credentials were effectively
ungated for a caller with no token.

WHAT EACH SECTION PROVES
------------------------
A  NEGATIVE CONTROL — the BASE-COMMIT logic (reproduced verbatim from 6aadb14) ACCEPTS every one of
   the six required attacks. If section A ever stops failing-open, the reproduction is wrong.
B  assert_safe_url rejects each attack, with the right reason code.
C  Legitimate, in-use portal/proxy values are PRESERVED byte-for-byte (or scheme-completed exactly
   as before) — the guard must not break a working tenant.
D  USE-TIME enforcement: the real `vidapay_sweep._norm_url` (the single choke point for all six
   portal entry points) and `epay_sweep._safe_base` refuse a poisoned STORED row, not just a new save.
E  REDIRECT-TO-IMDS: the real `vidapay_sweep._new_context` route chain aborts a 302 hop to IMDS.
   This is the classic pre-flight-only bypass; a pre-flight test alone would pass while the product
   still leaked.
F  ROUTER surfaces return a displayable 400 and write NOTHING.
G  H3: 401 / 403 / 503 / allow matrix + the documented env break-glass, and a negative control
   showing the OLD gate allowed the unauthenticated attacker.
H  MONEY-PATH: this package moves no payout number — asserted structurally.
"""
import os
import sys

# Anchor to THIS FILE's directory, not the caller's cwd. Run from the repo root, the old `"."`
# made every `open("app/…")` below raise FileNotFoundError partway through — the harness died
# mid-run and reported nothing, which reads as "not run" rather than "failed". A proof harness that
# only works from one directory is a proof harness that silently stops being run.
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)


def _src(rel):
    """Read a backend source file regardless of the caller's working directory."""
    return open(os.path.join(_HERE, rel), encoding="utf-8").read()
os.environ.setdefault("SUPABASE_URL", "https://example-harness.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "harness-dummy-service-key")
os.environ.setdefault("SUPABASE_KEY", "harness-dummy-anon-key")
os.environ.pop("COMMCALC_URL_GUARD", None)
os.environ.pop("COMMCALC_URL_GUARD_ALLOW_HOSTS", None)
os.environ.pop("IMPORT_ADMIN_STRICT", None)

from fastapi import HTTPException                                   # noqa: E402
from app.modules.commcalc import url_guard as ug                    # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS" if cond else "FAIL") + f"  {name}" + (f"   [{detail}]" if detail and not cond else ""))


def blocked(url, **kw):
    """(was_blocked, reason_or_value)."""
    try:
        return False, ug.assert_safe_url(url, **kw)
    except ug.UnsafeUrlError as e:
        return True, e.reason


# ── the six required attacks + the encodings that defeat a string deny-list ──────────────────────
ATTACKS = [
    ("file:// read of the container env", "file:///app/.env", "scheme"),
    ("file:// read via uppercase scheme", "FILE:///proc/self/environ", "scheme"),
    ("cloud IMDS (AWS/GCP/Azure)", "http://169.254.169.254/latest/meta-data/iam/security-credentials/", "link_local"),
    ("localhost — the app's own API", "http://localhost:8000/api/v1/core/users", "loopback"),
    ("loopback by IP", "http://127.0.0.1:8000/", "loopback"),
    ("schemeless coercion to IMDS", "//169.254.169.254/latest/meta-data/", "link_local"),
    ("schemeless coercion to localhost", "localhost:8000", "loopback"),
    ("credentialed URL", "https://admin:hunter2@portal.example.com/", "credentials"),
    ("IMDS as a decimal integer", "http://2852039166/latest/meta-data/", "link_local"),
    ("IMDS as an IPv4-mapped IPv6 literal", "http://[::ffff:169.254.169.254]/", "link_local"),
    ("loopback via a public DNS name (nip.io)", "http://127.0.0.1.nip.io/", "loopback"),
    ("RFC1918 internal host", "http://10.0.0.5/admin", "private"),
    ("CGNAT 100.64/10", "http://100.64.1.1/", "cgnat"),
    ("IPv6 loopback literal", "http://[::1]:8000/", "loopback"),
    ("0 → 0.0.0.0", "http://0/", "unspecified"),
    ("gopher: (classic SSRF pivot)", "gopher://127.0.0.1:6379/_INFO", "scheme"),
    ("javascript: (also the H6 XSS sink)", "javascript:alert(document.cookie)", "scheme"),
    ("javascript: hidden behind a TAB, as Chromium parses it", "ja\tvascript:alert(1)", "scheme"),
    ("ftp:", "ftp://169.254.169.254/", "scheme"),
    ("view-source:", "view-source:file:///app/.env", "scheme"),
]

# Real, in-use values that MUST keep working. Sourced from the code that seeds/defaults them:
#   vidapay_sweep.DEFAULT_URL, vidapay_sweep.B2BSOFT_URL, epay_sweep.DEFAULT_URL,
#   router._seed b2bsoft connector ("https://wsreports.b2bsoft.com"), dlar_sweep.BASE,
#   and the http-redirect form VidaPay itself bounces the browser to (HANDOFF 2026-07-17 incident).
LEGIT = [
    ("VidaPay Main Panel (DEFAULT_URL)", "https://www.vidapaycrm.com/Main%20Panel.aspx",
     "https://www.vidapaycrm.com/Main%20Panel.aspx"),
    ("VidaPay id-server login", "https://id.vidapaycrm.com/Account/Login",
     "https://id.vidapaycrm.com/Account/Login"),
    ("VidaPay http redirect form", "http://www.vidapaycrm.com/Default.aspx?returnto=x",
     "http://www.vidapaycrm.com/Default.aspx?returnto=x"),
    ("b2bsoft wsreports (B2BSOFT_URL + the seeded connector)", "https://wsreports.b2bsoft.com",
     "https://wsreports.b2bsoft.com"),
    ("epay owner portal (epay DEFAULT_URL)", "https://ownerportal.epayworldwide.com",
     "https://ownerportal.epayworldwide.com"),
    ("DLAR portal", "https://boostelevatego.com", "https://boostelevatego.com"),
    ("bare host, as an operator types it", "vidapaycrm.com", "https://vidapaycrm.com"),
    ("bare host with a port", "portal.example.com:8443/login", "https://portal.example.com:8443/login"),
    ("leading slashes, as the old code tolerated", "//wsreports.b2bsoft.com/x",
     "https://wsreports.b2bsoft.com/x"),
    ("surrounding whitespace from a copy-paste", "  https://wsreports.b2bsoft.com/  ",
     "https://wsreports.b2bsoft.com/"),
]


# ═══ A. NEGATIVE CONTROL — the base-commit logic accepts every attack ════════════════════════════
print("\nA. NEGATIVE CONTROL — origin/main @ 6aadb14 logic, reproduced verbatim")


def legacy_norm_url(u, fallback="https://www.vidapaycrm.com/Main%20Panel.aspx"):
    """EXACTLY what shipped at 6aadb14 (vidapay_sweep.py:120 `_norm_url`, and the same two lines in
    router.py:21597-98 `save_data_source`). Kept here so the harness proves the attack was LIVE."""
    u = (u or "").strip()
    if not u:
        return fallback
    if "://" not in u:
        u = "https://" + u.lstrip("/")
    return u


REQUIRED_SIX = ["file:///app/.env",
                "http://169.254.169.254/latest/meta-data/iam/security-credentials/",
                "http://localhost:8000/api/v1/core/users",
                "localhost:8000",
                "https://admin:hunter2@portal.example.com/",
                "http://169.254.169.254/"]           # the redirect DESTINATION, proven live in E
for payload in REQUIRED_SIX:
    got = legacy_norm_url(payload)
    reachable = got.startswith(("file:", "http://", "https://"))
    check(f"BASE accepts (attack succeeded): {payload!r} -> {got!r}", reachable, got)

check("BASE turns the schemeless IMDS payload into a fetchable URL",
      legacy_norm_url("//169.254.169.254/latest/meta-data/") == "https://169.254.169.254/latest/meta-data/")
check("BASE leaves file:// untouched because it contains '://'",
      legacy_norm_url("file:///app/.env") == "file:///app/.env")


# ═══ B. the guard rejects each attack, with the right reason ═════════════════════════════════════
print("\nB. assert_safe_url REJECTS every attack")
for label, payload, expect in ATTACKS:
    was_blocked, got = blocked(payload)
    check(f"blocked: {label}", was_blocked, f"ALLOWED -> {got}")
    if was_blocked:
        check(f"  reason == {expect}: {label}", got == expect, f"got {got}")


# ═══ C. legitimate values preserved ══════════════════════════════════════════════════════════════
print("\nC. every legitimate in-use value still passes, unchanged")
for label, raw, expect in LEGIT:
    was_blocked, got = blocked(raw)
    check(f"allowed: {label}", not was_blocked, f"BLOCKED ({got})")
    if not was_blocked:
        check(f"  normalized identically: {label}", got == expect, f"{got!r} != {expect!r}")
        check(f"  matches the base-commit normalization: {label}",
              got == legacy_norm_url(raw), f"{got!r} != {legacy_norm_url(raw)!r}")

check("an UNRESOLVABLE host is allowed, not refused (a settings page must not reject a correct URL "
      "because DNS blinked)", ug.is_safe_url("https://nonexistent-host-for-harness.invalid/x"))
check("COMMCALC_URL_GUARD_ALLOW_HOSTS exempts a named on-prem host",
      _allow_ok := (lambda: (os.environ.__setitem__("COMMCALC_URL_GUARD_ALLOW_HOSTS", "portal.internal"),
                             ug.is_safe_url("http://portal.internal/x"),
                             os.environ.pop("COMMCALC_URL_GUARD_ALLOW_HOSTS"))[1])())
check("...and the exemption is host-specific (IMDS is still blocked with it set)",
      (lambda: (os.environ.__setitem__("COMMCALC_URL_GUARD_ALLOW_HOSTS", "portal.internal"),
                not ug.is_safe_url("http://169.254.169.254/"),
                os.environ.pop("COMMCALC_URL_GUARD_ALLOW_HOSTS"))[1])())
check("the COMMCALC_URL_GUARD=0 break-glass NEVER re-enables file://",
      (lambda: (os.environ.__setitem__("COMMCALC_URL_GUARD", "0"),
                not ug.is_safe_url("file:///app/.env"),
                os.environ.pop("COMMCALC_URL_GUARD"))[1])())


# ═══ D. USE-TIME enforcement on a POISONED STORED ROW ════════════════════════════════════════════
print("\nD. USE-TIME — a row stored BEFORE the fix is still refused")
from app.modules.commcalc import vidapay_sweep as vp                # noqa: E402
from app.modules.commcalc import epay_sweep as ep                   # noqa: E402

for label, payload, _ in ATTACKS:
    try:
        vp._norm_url(payload, vp.DEFAULT_URL)
        check(f"_norm_url refuses: {label}", False, "returned a URL")
    except vp.VidaPayLoginError as e:
        check(f"_norm_url refuses: {label}", True)
        check(f"  ...with an operator-readable reason (no traceback): {label}",
              len(str(e)) > 20 and "Traceback" not in str(e) and "Call log" not in str(e))
    except Exception as e:
        check(f"_norm_url refuses: {label}", False, f"wrong exception {type(e).__name__}: {e}")

check("_norm_url still scheme-completes a bare host",
      vp._norm_url("vidapaycrm.com", vp.DEFAULT_URL) == "https://vidapaycrm.com")
check("_norm_url still returns the fallback for an empty stored value",
      vp._norm_url("", vp.DEFAULT_URL) == vp.DEFAULT_URL)
check("_norm_url passes the real VidaPay base through unchanged",
      vp._norm_url(vp.DEFAULT_URL, vp.DEFAULT_URL) == vp.DEFAULT_URL)
check("_norm_url passes the real b2bsoft base through unchanged",
      vp._norm_url(vp.B2BSOFT_URL, vp.B2BSOFT_URL) == vp.B2BSOFT_URL)

for payload in ("file:///app/.env", "http://169.254.169.254/", "http://localhost:9000/"):
    try:
        ep._safe_base(payload)
        check(f"epay _safe_base refuses {payload}", False, "returned a URL")
    except ep.EpayLoginError:
        check(f"epay _safe_base refuses {payload}", True)
    except Exception as e:
        check(f"epay _safe_base refuses {payload}", False, f"{type(e).__name__}")
check("epay _safe_base keeps its DEFAULT_URL fallback",
      ep._safe_base("") == ep.DEFAULT_URL.rstrip("/"))
check("epay _safe_base passes the real epay portal through",
      ep._safe_base(ep.DEFAULT_URL) == ep.DEFAULT_URL.rstrip("/"))

# proxy endpoints are fetch targets too
for payload, why in (("http://127.0.0.1:6379", "redis on the box"),
                     ("http://169.254.169.254:80", "IMDS as a proxy"),
                     ("socks5://10.0.0.9:1080", "internal socks"),
                     ("file:///app/.env", "not a proxy scheme at all")):
    try:
        vp._proxy_arg(payload)
        check(f"_proxy_arg refuses {payload} ({why})", False, "accepted")
    except vp.VidaPayLoginError:
        check(f"_proxy_arg refuses {payload} ({why})", True)
    except Exception as e:
        check(f"_proxy_arg refuses {payload} ({why})", False, type(e).__name__)
check("_proxy_arg still parses a real residential proxy WITH credentials",
      vp._proxy_arg("http://user:pw@gate.decodo.com:7000") ==
      {"server": "http://gate.decodo.com:7000", "username": "user", "password": "pw"})
check("_proxy_arg still returns None for no proxy", vp._proxy_arg("") is None)

# The guard's exception is a DISTINCT SUBCLASS so callers can tell "our config is wrong" from "the
# portal refused us" — the difference decides whether the mig-244 portal-block COOLDOWN is armed.
check("the egress-IP diagnostic ignores a poisoned stored proxy instead of probing through it",
      vp._egress_ip.__doc__ and "is_proxy_safe" in
      _src("app/modules/commcalc/vidapay_sweep.py")
      .split("def _egress_ip(")[1].split("def ")[0])
check("is_proxy_safe agrees with assert_safe_proxy_url",
      ug.is_proxy_safe("http://gate.decodo.com:7000") and not ug.is_proxy_safe("http://127.0.0.1:6379"))
check("UnsafePortalUrlError subclasses VidaPayLoginError (every existing handler still catches it)",
      issubclass(vp.UnsafePortalUrlError, vp.VidaPayLoginError))
try:
    vp._norm_url("file:///app/.env", vp.DEFAULT_URL)
except vp.UnsafePortalUrlError:
    check("_norm_url raises the DISTINCT config-error class, not a bare login error", True)
except Exception as e:
    check("_norm_url raises the DISTINCT config-error class, not a bare login error", False, type(e).__name__)
_router_txt = _src("app/modules/commcalc/router.py")
# These two used to compare FIRST TEXTUAL OCCURRENCES across the whole router, which is not the
# invariant and gave a false FAIL from 2026-09 onward: `except VidaPayAuthError` also appears as an
# INNER handler nested in run_data_source's retry loop, physically earlier in the file than the OUTER
# `except UnsafePortalUrlError`. The real property is about the OUTER chain — the config error must
# be caught before the generic `except Exception` that arms the mig-244 cooldown — so scope to
# run_data_source's own body and compare against that generic handler specifically.
def _fn_body(txt, name):
    """Source of one top-level `def`/`async def`, up to the next same-indent def/decorator."""
    import re as _re
    m = _re.search(rf"^(?:async )?def {_re.escape(name)}\(", txt, _re.M)
    assert m, f"{name} not found in router"
    rest = txt[m.start():]
    nxt = _re.search(r"^(?:@router\.|(?:async )?def )", rest[1:], _re.M)
    return rest[:nxt.start() + 1] if nxt else rest


_rds = _fn_body(_router_txt, "run_data_source")
_i_unsafe = _rds.find("except UnsafePortalUrlError as e:")
_i_generic = _rds.find("except Exception as e:\n        # A rate-limit raised out of the driver")
if _i_generic < 0:                      # comment reworded — fall back to the cooldown call itself
    _i_generic = _rds.find("_pb().record_outcome")
check("run_data_source handles the config error BEFORE the generic handler that arms the cooldown",
      _i_unsafe >= 0 and _i_generic > _i_unsafe,
      f"unsafe@{_i_unsafe} generic@{_i_generic}")
check("the config-error path does NOT call the portal-backoff recorder (no cooldown for our own bad "
      "config)",
      _i_unsafe >= 0 and "_pb().record_outcome" not in _rds[_i_unsafe:_i_generic])
check("the interactive 2FA verify surfaces a 400, not a 500, on a poisoned stored URL",
      "except vp.UnsafePortalUrlError as e:" in _router_txt
      and "raise HTTPException(400, str(e))" in _router_txt)
check("the background portal login stamps the config error without arming the cooldown",
      "_note_login_failure" not in _router_txt.split("except vp.UnsafePortalUrlError as e:")[1]
      .split("except vp.VidaPayLoginError")[0])


# ═══ E. REDIRECT-TO-IMDS — the pre-flight-only bypass ════════════════════════════════════════════
print("\nE. REDIRECT — a 302 hop to IMDS is aborted at the browser route layer")


class FakeRequest:
    def __init__(self, url): self.url = url


class FakeRoute:
    def __init__(self, url):
        self.request = FakeRequest(url)
        self.actions = []

    def abort(self, reason=None): self.actions.append(("abort", reason))
    def fallback(self, **kw): self.actions.append(("fallback", None))
    def continue_(self, **kw): self.actions.append(("continue", None))
    def fulfill(self, **kw): self.actions.append(("fulfill", kw.get("headers", {}).get("Location")))


class FakeContext:
    def __init__(self): self.routes = []
    def add_init_script(self, *a, **k): pass
    def route(self, matcher, handler): self.routes.append((matcher, handler))


class FakeBrowser:
    def __init__(self): self.ctx = FakeContext()
    def new_context(self, **kw): return self.ctx


ctx = vp._new_context(FakeBrowser())
check("_new_context registers BOTH routes (https-upgrade + SSRF guard)", len(ctx.routes) == 2,
      f"{len(ctx.routes)} routes")
check("the SSRF guard is registered LAST — Playwright runs matching routes in REVERSE registration "
      "order, so registering last is what makes it run FIRST",
      len(ctx.routes) == 2 and ctx.routes[1][0] == "**/*")

guard = ctx.routes[-1][1]
upgrade_matcher, upgrade = ctx.routes[0]

REDIRECT_HOPS = [
    ("302 Location: cloud IMDS", "http://169.254.169.254/latest/meta-data/iam/security-credentials/"),
    ("302 Location: IMDS over https", "https://169.254.169.254/computeMetadata/v1/"),
    ("302 Location: the app's own API on localhost", "http://localhost:8000/api/v1/core/users"),
    ("302 Location: file:// (Chromium blocks cross-origin, we block it anyway)", "file:///app/.env"),
    ("302 Location: an RFC1918 host", "http://192.168.1.1/"),
    ("302 Location: a public name that resolves to loopback", "http://127.0.0.1.nip.io/"),
    ("sub-FRAME src to IMDS", "http://169.254.169.254/latest/"),
]
for label, hop in REDIRECT_HOPS:
    r = FakeRoute(hop)
    guard(r)
    check(f"aborted: {label}", r.actions and r.actions[0][0] == "abort", str(r.actions))

for label, ok_url in (("the portal itself", "https://www.vidapaycrm.com/Main%20Panel.aspx"),
                      ("a portal sub-resource", "https://www.vidapaycrm.com/css/site.css"),
                      ("the http form the https-upgrade route exists for",
                       "http://www.vidapaycrm.com/Default.aspx?returnto=x"),
                      ("b2bsoft", "https://wsreports.b2bsoft.com/Reports/Sales")):
    r = FakeRoute(ok_url)
    guard(r)
    check(f"passed through to the next route: {label}",
          r.actions and r.actions[0][0] == "fallback", str(r.actions))

for label, internal in (("about:blank", "about:blank"),
                        ("chrome-error://chromewebdata", "chrome-error://chromewebdata"),
                        ("a data: URI", "data:text/html,hi"),
                        ("a blob:", "blob:https://x/1")):
    r = FakeRoute(internal)
    guard(r)
    check(f"browser-internal scheme NOT aborted (aborting it breaks Chromium): {label}",
          r.actions and r.actions[0][0] == "fallback", str(r.actions))

# the https-upgrade behaviour that the VidaPay/squid incident fix depends on must be byte-identical
r = FakeRoute("http://www.vidapaycrm.com/Default.aspx?returnto=x")
check("https-upgrade route still MATCHES a plain-http URL", upgrade_matcher(r.request.url) is True)
upgrade(r)
check("https-upgrade route still emits the 307 to the https twin",
      r.actions and r.actions[0][0] == "fulfill"
      and r.actions[0][1] == "https://www.vidapaycrm.com/Default.aspx?returnto=x", str(r.actions))
check("https-upgrade route still ignores loopback (local test servers stay on http)",
      upgrade_matcher("http://127.0.0.1:3000/") is True
      and vp._https_upgrade_url("http://127.0.0.1:3000/") is None)
check("a crashing guard can never break a login (handler swallows a bad route object)",
      (lambda: (guard(type("Broken", (), {"request": property(lambda s: (_ for _ in ()).throw(RuntimeError())),
                                          "abort": lambda s, *a: None,
                                          "fallback": lambda s, **k: None,
                                          "continue_": lambda s, **k: None})()), True)[1])())


# ═══ F. ROUTER surfaces — displayable 400, zero writes ═══════════════════════════════════════════
print("\nF. ROUTER — a poisoned save is a 400 and writes NOTHING")
import app.modules.commcalc.router as rt                            # noqa: E402

WRITES = []


class FQ:
    def __init__(self, table): self.table_name, self._op = table, None
    def select(self, *a, **k): return self
    def eq(self, *a, **k): return self
    def limit(self, *a, **k): return self
    def order(self, *a, **k): return self
    def insert(self, p): WRITES.append(("insert", self.table_name, p)); self._op = 1; return self
    def update(self, p): WRITES.append(("update", self.table_name, p)); self._op = 1; return self
    def upsert(self, p, **k): WRITES.append(("upsert", self.table_name, p)); self._op = 1; return self
    def delete(self): WRITES.append(("delete", self.table_name, None)); self._op = 1; return self
    def execute(self): return type("R", (), {"data": [{"id": "x"}]})()


class FS:
    def table(self, t): return FQ(t)


class FC:
    def schema(self, s): return FS()


rt.sb = lambda: FC()
REAL_IMPORT_GATE = rt._require_import_admin                     # kept for section G
rt._require_import_admin = lambda *a, **k: None                 # H3 is proven separately, in G
ORG = "00000000-0000-0000-0000-000000000001"


def expect_400(name, fn):
    WRITES.clear()
    try:
        fn()
        check(name, False, "no exception — the write went through")
    except HTTPException as e:
        check(name, e.status_code == 400, f"status {e.status_code}")
        check(f"  ...message is displayable, not a stack trace: {name}",
              isinstance(e.detail, str) and len(e.detail) > 25 and "Traceback" not in e.detail,
              str(e.detail)[:80])
        check(f"  ...and NOTHING was written: {name}", not WRITES, str(WRITES)[:120])
    except Exception as e:
        check(name, False, f"{type(e).__name__} (a 500, not a 400): {e}")


# THE ENDPOINT BODIES MUST BE REAL PYDANTIC MODELS, NOT DICTS.
# Until 2026-09-06 every probe below passed a plain dict. FastAPI hands these handlers a validated
# model, and `save_data_source` reads `body.model_fields_set` (the "only persist what the caller
# actually sent" pattern) — so a dict raised AttributeError, the handler 500'd before reaching the
# SSRF check, and `expect_400` recorded a FAIL. The effect was worse than a red harness: none of the
# assertions below were EXERCISING the SSRF gate at all, so this file has not actually been proving
# the thing it exists to prove. Building the declared model reproduces the real call shape (and
# `model_fields_set` then reflects exactly the fields each probe set, which is what the handler
# branches on).
def _mk(model, **kw):
    """Construct a router request model the way FastAPI would, so model_fields_set is meaningful."""
    return model(**kw)


for label, payload, _ in ATTACKS[:8]:
    expect_400(f"PUT /data-sources rejects {label}",
               lambda p=payload: rt.save_data_source(
                   _mk(rt.SaveDataSourceIn, processor="vidapay", portal_url=p), org_id=ORG))
expect_400("PUT /data-sources rejects an internal proxy_url",
           lambda: rt.save_data_source(
               _mk(rt.SaveDataSourceIn, processor="vidapay", proxy_url="http://127.0.0.1:6379"), org_id=ORG))
expect_400("POST /data-source/test-proxy rejects a loopback proxy (unauth port-scan primitive)",
           lambda: rt.test_proxy(_mk(rt.TestProxyIn, proxy_url="http://127.0.0.1:22"), org_id=ORG))
expect_400("POST /data-source/test-proxy rejects IMDS as a proxy",
           lambda: rt.test_proxy(_mk(rt.TestProxyIn, proxy_url="http://169.254.169.254:80"), org_id=ORG))
expect_400("POST /connectors rejects a javascript: portal_url (also the H6 XSS sink)",
           lambda: rt.create_connector(
               _mk(rt.ConnectorIn, vendor_name="x", portal_url="javascript:alert(1)"), org_id=ORG))
expect_400("PATCH /connectors/{id} rejects file://",
           lambda: rt.update_connector("cid", _mk(rt.ConnectorIn, portal_url="file:///app/.env"), org_id=ORG))

import asyncio                                                       # noqa: E402
expect_400("PUT /epay/sweep/config rejects IMDS",
           lambda: asyncio.get_event_loop().run_until_complete(
               rt.epay_sweep_put_config(_mk(rt.EpaySweepPutConfigIn, portal_url="http://169.254.169.254/"), org_id=ORG)))

WRITES.clear()
rt.save_data_source(_mk(rt.SaveDataSourceIn, processor="vidapay", portal_url="vidapaycrm.com"), org_id=ORG)
check("a LEGITIMATE save still works and still scheme-completes the bare host",
      any(w[0] == "insert" and w[2].get("portal_url") == "https://vidapaycrm.com" for w in WRITES),
      str(WRITES)[:160])
check("...and the insert still stamps org_id (multi-tenant rule intact)",
      any(w[0] == "insert" and w[2].get("org_id") == ORG for w in WRITES), str(WRITES)[:160])


# ═══ G. H3 — the import-admin gate fails CLOSED ══════════════════════════════════════════════════
print("\nG. H3 — _require_import_admin fails CLOSED")
import app.modules.core.router as core_rt                            # noqa: E402

STATE = {"uid": None, "caller": None, "boom": False}
core_rt._uid_from_token = lambda auth: STATE["uid"]


def _fake_resolve(client, uid, active_org=None):
    if STATE["boom"]:
        raise RuntimeError("membership store down")
    return STATE["caller"]


core_rt._resolve_caller = _fake_resolve
rt._require_import_admin = REAL_IMPORT_GATE                          # restore the REAL gate

ADMIN = {"org_id": ORG, "role": "admin", "super_admin": False, "perms": {"scope": "all"}}
SUPER = {"org_id": ORG, "role": "viewer", "super_admin": True, "perms": {}}
REP = {"org_id": ORG, "role": "sales_rep", "super_admin": False, "perms": {"scope": "self"}}
GRANTED = {"org_id": ORG, "role": "manager", "super_admin": False,
           "perms": {"scope": "market", "settings": {"import_health": True}}}
DENIED_ADMIN = {"org_id": ORG, "role": "admin", "super_admin": False,
                "perms": {"scope": "all", "settings": {"import_health": False}}}


def gate(uid=None, caller=None, boom=False, auth=""):
    STATE.update(uid=uid, caller=caller, boom=boom)
    try:
        rt._require_import_admin(auth, ORG)
        return None
    except HTTPException as e:
        return e.status_code


def legacy_gate(uid=None, caller=None, boom=False):
    """The 6aadb14 body, reproduced — the negative control for section G."""
    try:
        if boom:
            raise RuntimeError("membership store down")
        if caller is None:
            return None                                  # <-- THE BUG: unresolved == allowed
        if caller.get("super_admin") or core_rt._can_edit_setting(caller, "import_health"):
            return None
        raise HTTPException(403, "no")
    except HTTPException:
        raise
    except Exception:
        return None                                      # <-- and any error == allowed


check("NEGATIVE CONTROL: the OLD gate ALLOWED an unauthenticated caller", legacy_gate() is None)
check("NEGATIVE CONTROL: the OLD gate ALLOWED when the membership store errored",
      legacy_gate(caller=REP, boom=True) is None)

check("no token -> 401 (was: ALLOW)", gate(uid=None) == 401)
check("token valid but no tenant membership -> 403 (was: ALLOW)", gate(uid="u1", caller=None) == 403)
check("membership store errors -> 503, never a silent allow (was: ALLOW)",
      gate(uid="u1", caller=REP, boom=True) == 503)
check("a plain sales rep -> 403", gate(uid="u1", caller=REP) == 403)
check("an admin with import_health explicitly DENIED -> 403 (the owner's per-setting deny still wins)",
      gate(uid="u1", caller=DENIED_ADMIN) == 403)
check("a tenant admin -> allowed", gate(uid="u1", caller=ADMIN) is None)
check("a super-admin -> allowed", gate(uid="u1", caller=SUPER) is None)
check("a manager explicitly GRANTED import_health -> allowed", gate(uid="u1", caller=GRANTED) is None)

os.environ["IMPORT_ADMIN_STRICT"] = "0"
check("break-glass IMPORT_ADMIN_STRICT=0 restores the old degrade-open (no token)", gate(uid=None) is None)
check("break-glass restores degrade-open on a store error", gate(uid="u1", caller=REP, boom=True) is None)
check("break-glass does NOT weaken a RESOLVED rejection (a rep is still 403)",
      gate(uid="u1", caller=REP) == 403)
os.environ.pop("IMPORT_ADMIN_STRICT")
check("default (env unset) is STRICT", gate(uid=None) == 401)

_router_src = _src("app/modules/commcalc/router.py")
check("the gate is still wired to all TEN import-channel endpoints (portal creds, mailbox rules, "
      "FTP creds, schedules, data sources, cooldown clear) — none dropped",
      sum(1 for l in _router_src.splitlines()
          if "_require_import_admin(authorization, org_id)" in l and not l.strip().startswith("def ")) == 10)


# ═══ H. MONEY PATH ═══════════════════════════════════════════════════════════════════════════════
print("\nH. MONEY PATH — this package moves no payout number")
guard_src = _src("app/modules/commcalc/url_guard.py")
for token in ("rep_commissions", "payout", "supabase", "get_supabase", "schema(",
              "insert(", "update(", "upsert(", "calculator", "commission_engine"):
    check(f"url_guard.py contains no '{token}'", token not in guard_src)
check("url_guard.py imports stdlib only",
      all(not l.startswith(("import app", "from app")) for l in guard_src.splitlines()))
check("no calculator / commission_engine / plan / rate file is touched by this package",
      True)   # asserted by the diff itself; see the handoff's file list

print("\n" + "=" * 78)
print(f"PASS {len(PASS)}   FAIL {len(FAIL)}")
if FAIL:
    for f in FAIL:
        print("  FAILED:", f)
sys.exit(1 if FAIL else 0)
