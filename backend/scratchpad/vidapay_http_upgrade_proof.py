"""Proof for the VidaPay plain-http-redirect fix (branch agent/commission/vidapay-http-upgrade).

Covers the two testable pieces of vidapay_sweep.py WITHOUT the live portal (Cloudflare 403s this
datacenter IP):

  1. _https_upgrade_url  — the pure scheme-swap helper the _new_context route calls: http -> https,
     query string + percent-encoded chars preserved verbatim, port/userinfo preserved, and
     localhost/loopback excluded (returns None so local test servers + Playwright internals stay http).

  2. _looks_like_proxy_error / _classify == 'proxy_error' — detection of the Decodo/squid egress-proxy
     rejection page against a saved snippet of the real squid "ERROR: The requested URL could not be
     retrieved … (squid)" HTML, and that it does NOT false-positive on an ordinary login page.
     Plus _proxy_error_message names the failing URL + the egress proxy (not "session expired").

Run: python3 backend/scratchpad/vidapay_http_upgrade_proof.py
Loads the module by file path (its only top-level import is datetime — playwright is lazy), so it runs
with zero app/playwright deps.
"""
import importlib.util
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_MOD = os.path.join(_HERE, "..", "app", "modules", "commcalc", "vidapay_sweep.py")
_spec = importlib.util.spec_from_file_location("vidapay_sweep_proof_target", _MOD)
vp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vp)

_fail = 0
_ok = 0


def check(name, cond):
    global _fail, _ok
    if cond:
        _ok += 1
        print("  ok   %s" % name)
    else:
        _fail += 1
        print("  FAIL %s" % name)


# ── a saved snippet of the real Squid egress-proxy rejection page ────────────────────────────────
# This is exactly the page the owner's screenshot showed: squid refusing the portal's plain-http
# absolute-form ?returnto redirect. Note the URL it echoes back is the /Default.aspx?returnto=... hop.
SQUID_HTML = """<!DOCTYPE html PUBLIC "-//W3C//DTD HTML 4.01 Transitional//EN">
<html><head>
<title>ERROR: The requested URL could not be retrieved</title>
<meta charset="utf-8">
</head><body id=ERR_INVALID_URL>
<div id="titles"><h1>ERROR</h1><h2>The requested URL could not be retrieved</h2></div>
<hr>
<div id="content">
<p>The following error was encountered while trying to retrieve the URL:
<a href="/Default.aspx?returnto=http%3a%2f%2fwww.vidapaycrm.com%2fMain+Panel.aspx">
/Default.aspx?returnto=http%3a%2f%2fwww.vidapaycrm.com%2fMain+Panel.aspx</a></p>
<blockquote id="error"><p><b>Invalid URL</b></p></blockquote>
<p>Some aspect of the requested URL is incorrect.</p>
<p>Your cache administrator is <a href="mailto:webmaster">webmaster</a>.</p>
</div>
<hr>
<div id="footer"><p>Generated Wed, 16 Jul 2026 00:54:11 GMT by localhost (squid)</p></div>
</body></html>"""

NORMAL_LOGIN_HTML = """<html><head><title>Sign In</title></head><body>
<h1>SIGN IN</h1><form><input id="AccountId" type="number">
<input id="Username"><input id="Password" type="password">
<button id="btnClick">Sign In</button></form></body></html>"""


class FakePage:
    """Minimal stand-in for a Playwright Page for the text-only classify paths (proxy_error is checked
    FIRST in _classify and only reads title()+content(), so no DOM/frame plumbing is exercised)."""
    def __init__(self, html, url="", title=""):
        self._html = html
        self.url = url
        self._title = title
        self.frames = [self]

    def title(self):
        return self._title

    def content(self):
        return self._html

    def query_selector(self, sel):
        return None

    def query_selector_all(self, sel):
        return []

    def evaluate(self, *a, **k):
        return []


print("== 1. _https_upgrade_url (scheme swap) ==")

# The exact VidaPay bounce URL from the owner's diagnosis — the OUTER scheme upgrades, the
# percent-encoded inner ?returnto=http%3a%2f%2f… is preserved byte-for-byte.
bounce = "http://www.vidapaycrm.com/Default.aspx?returnto=http%3a%2f%2fwww.vidapaycrm.com%2fMain+Panel.aspx"
up = vp._https_upgrade_url(bounce)
check("vidapay bounce URL upgrades outer scheme to https",
      up == "https://www.vidapaycrm.com/Default.aspx?returnto=http%3a%2f%2fwww.vidapaycrm.com%2fMain+Panel.aspx")
check("encoded inner ?returnto=http%3a%2f%2f is preserved (NOT double-upgraded)",
      "returnto=http%3a%2f%2fwww.vidapaycrm.com%2fMain+Panel.aspx" in up and up.count("https://") == 1)
check("only ONE scheme swap happened (single leading https://)", up.startswith("https://www.vidapaycrm.com/"))

check("plain http host upgrades", vp._https_upgrade_url("http://www.vidapaycrm.com/x") == "https://www.vidapaycrm.com/x")
check("query string + '+' and '&' preserved",
      vp._https_upgrade_url("http://h.com/a?b=c+d&e=f%20g") == "https://h.com/a?b=c+d&e=f%20g")
check("port preserved", vp._https_upgrade_url("http://h.com:8080/a") == "https://h.com:8080/a")
check("userinfo (user:pass@) preserved",
      vp._https_upgrade_url("http://u:p@h.com/a") == "https://u:p@h.com/a")
check("scheme match is case-insensitive (HTTP://)",
      vp._https_upgrade_url("HTTP://h.com/a") == "https://h.com/a")
check("bare host, no path", vp._https_upgrade_url("http://h.com") == "https://h.com")

# already-https / other schemes -> None (not upgraded)
check("https:// URL is left alone (None)", vp._https_upgrade_url("https://h.com/a") is None)
check("ws:// scheme is left alone (None)", vp._https_upgrade_url("ws://h.com/a") is None)
check("empty/None input -> None", vp._https_upgrade_url("") is None and vp._https_upgrade_url(None) is None)

# localhost / loopback exclusion
check("http://localhost:8000 excluded", vp._https_upgrade_url("http://localhost:8000/x") is None)
check("http://127.0.0.1 excluded", vp._https_upgrade_url("http://127.0.0.1/x") is None)
check("http://127.0.0.5 (127/8) excluded", vp._https_upgrade_url("http://127.0.0.5:9/x") is None)
check("http://0.0.0.0 excluded", vp._https_upgrade_url("http://0.0.0.0/x") is None)
check("http://[::1] (IPv6 loopback) excluded", vp._https_upgrade_url("http://[::1]:9/x") is None)
check("http://foo.localhost excluded", vp._https_upgrade_url("http://foo.localhost/x") is None)
check("a NON-loopback host that merely contains 'localhost' text is upgraded",
      vp._https_upgrade_url("http://localhostish.com/x") == "https://localhostish.com/x")

print("== 2. proxy-error detection (squid page) ==")
check("_looks_like_proxy_error TRUE on the squid page", vp._looks_like_proxy_error(FakePage(SQUID_HTML)) is True)
check("_classify == 'proxy_error' on the squid page", vp._classify(FakePage(SQUID_HTML)) == "proxy_error")
check("_looks_like_proxy_error FALSE on a normal login page", vp._looks_like_proxy_error(FakePage(NORMAL_LOGIN_HTML)) is False)
check("_classify does NOT return 'proxy_error' on a normal login page",
      vp._classify(FakePage(NORMAL_LOGIN_HTML)) != "proxy_error")
# each individual squid marker on its own trips detection
check("marker 'requested url could not be retrieved' alone trips it",
      vp._looks_like_proxy_error(FakePage("<p>The requested URL could not be retrieved</p>")) is True)
check("marker '(squid)' alone trips it",
      vp._looks_like_proxy_error(FakePage("<p>generated by localhost (squid)</p>")) is True)
check("marker 'your cache administrator' alone trips it",
      vp._looks_like_proxy_error(FakePage("<p>Your cache administrator is webmaster.</p>")) is True)
# an unrelated generic error page must NOT trip it (no false positive)
check("a generic 'Server Error 500' page does NOT trip proxy detection",
      vp._looks_like_proxy_error(FakePage("<h1>Server Error</h1><p>500 Internal Server Error</p>")) is False)

print("== 3. _proxy_error_message wording ==")
fail_url = "http://www.vidapaycrm.com/Default.aspx?returnto=http%3a%2f%2fwww.vidapaycrm.com%2fMain+Panel.aspx"
msg_p = vp._proxy_error_message(fail_url, "http://user:pass@isp.decodo.com:10001")
check("message names the failing URL", "Default.aspx?returnto=" in msg_p)
check("message states it died AT THE EGRESS PROXY", "AT THE EGRESS PROXY" in msg_p)
check("message names the proxy server (host:port, no creds leaked)",
      "isp.decodo.com:10001" in msg_p and "user:pass" not in msg_p and "pass@" not in msg_p)
check("message does NOT claim the session expired",
      "expired" not in msg_p.lower() and "session/2fa problem" in msg_p.lower())
msg_np = vp._proxy_error_message(fail_url, None)
check("message works with no proxy configured (still names egress proxy)", "AT THE EGRESS PROXY" in msg_np)

print("\n%d ok, %d fail" % (_ok, _fail))
raise SystemExit(1 if _fail else 0)
