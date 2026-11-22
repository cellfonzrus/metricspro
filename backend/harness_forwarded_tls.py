"""Lock: this app knows it sits behind a TLS-terminating edge, and NEVER redirects a caller off TLS.

MEASURED ON PRODUCTION, 2026-11-22, access probe run 326, module `platform` (index §CORE-43):

    $ curl -s -o /dev/null -w '%{http_code} %{redirect_url}' https://api.metricspro.tech/api/v1/core/me/
    307 http://api.metricspro.tech/api/v1/core/me

`/api/v1/core/me` is the token-verified whoami. A client that reached it over TLS with one extra
trailing slash was told, by us, to resend that request — Authorization header and all — over
PLAINTEXT http. `/docs/` and `/redoc/` did the same.

THE CLASS, not the instance: the ASGI app did not know its own scheme. Gunicorn ran the uvicorn
worker with no `--forwarded-allow-ips`, so uvicorn's default of `127.0.0.1` discarded the edge's
`x-forwarded-proto: https`, `scope["scheme"]` stayed "http", and EVERY absolute URL the framework
builds — the router's trailing-slash 307, any RedirectResponse, url_for — named http://. Fixing the
one route that happened to be noticed would have left every other redirect generator downgrading.

The fix has two layers because the root cause lives outside Python:

  A. app/core/forwarded_tls.py is the ONE HOME for "what scheme did this request really arrive on"
     and for "keep a Location we generated on that scheme". Pure functions, no second copy.
  B. The Dockerfile grants the forwarded-proto trust, so `scope["scheme"]` is right at the ROOT and
     every generator is fixed, not just the ones we remembered.
  C. HardeningMiddleware dereferences the one home on the way out — defence in depth, so a deploy
     that loses the flag (B) cannot silently restore the downgrade.
  D. No second copy of the rewrite, no caller reading x-forwarded-proto for itself.
  E. NO LOCKOUT: a request that genuinely arrived over plain http is NOT forced to https, so a
     plain-HTTP staging box and the local dev server keep working.

Run: `python3 harness_forwarded_tls.py` from the backend dir. DB-free.
"""
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

PASS = FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")


from app.core import forwarded_tls as ft  # noqa: E402

APP = HERE / "app"

print("── A. effective_scheme — the edge is believed, the transport is the fallback ─────")
CASES = [
    # (asgi scheme, x-forwarded-proto, expected)
    ("http", "https", "https"),                 # the production shape
    ("http", "https, http", "https"),           # a chain: the CLIENT's hop is the first
    ("http", "HTTPS", "https"),                 # casing
    ("http", "  https  ", "https"),             # whitespace
    ("http", "http", "http"),                   # the edge says plaintext → plaintext
    ("http", None, "http"),                     # no edge → the transport's own scheme
    ("http", "", "http"),
    ("https", None, "https"),                   # TLS straight to the app (no edge at all)
    ("http", "gopher", "http"),                 # a junk value is not believed
    (None, None, "http"),                       # nothing known at all → never crash
]
for asgi, fwd, want in CASES:
    got = ft.effective_scheme(asgi, fwd)
    check(f"effective_scheme({asgi!r}, {fwd!r}) == {want!r}", got == want, f"got {got!r}")

# THE SELECTION IS NOT OURS. `x-forwarded-proto` is an appended list exactly like
# `x-forwarded-for`, so its leftmost entry is whatever the CLIENT sent. With one trusted proxy in
# front, the entry to believe is the LAST — which is `client_ip.trusted_entry`'s arithmetic, not a
# second rule here. A forged leading entry must not decide the scheme.
CHAIN = [
    # (x-forwarded-proto, hops, legacy, expected)
    ("https", 1, False, "https"),            # the real shape: one edge, one entry
    ("https, http", 1, False, "http"),       # client forged "https"; the EDGE said http → http
    ("http, https", 1, False, "https"),      # client forged "http"; the edge said https → https
    ("https", 0, False, "http"),             # no trusted proxy ⇒ believe no entry ⇒ transport
    ("https, http", 0, True, "https"),       # legacy (today's default) reads the leftmost, as before
]
for fwd, h, lg, want in CHAIN:
    got = effective = ft.effective_scheme("http", fwd, hops=h, legacy=lg)
    check(f"effective_scheme(http, {fwd!r}, hops={h}, legacy={lg}) == {want!r}", got == want, f"got {got!r}")
check("effective_scheme dereferences client_ip.trusted_entry (no second hop rule)",
      "trusted_entry" in (APP / "core/forwarded_tls.py").read_text(encoding="utf-8", errors="ignore"))

print("── B. secure_location — a Location we generated never leaves TLS ────────────────")
H = "api.metricspro.tech"
LOC = [
    # (location, scheme, host, expected)
    ("http://api.metricspro.tech/api/v1/core/me", "https", H,
     "https://api.metricspro.tech/api/v1/core/me"),                      # THE REGRESSION
    ("http://api.metricspro.tech/docs", "https", H, "https://api.metricspro.tech/docs"),
    ("http://API.METRICSPRO.TECH/docs", "https", H, "https://API.METRICSPRO.TECH/docs"),
    ("http://api.metricspro.tech:443/x", "https", "api.metricspro.tech:443",
     "https://api.metricspro.tech:443/x"),
    # NO LOCKOUT / least surprise: leave everything else exactly as it was
    ("http://api.metricspro.tech/docs", "http", H, "http://api.metricspro.tech/docs"),
    ("https://api.metricspro.tech/docs", "https", H, "https://api.metricspro.tech/docs"),
    ("/api/v1/core/me", "https", H, "/api/v1/core/me"),                  # relative: nothing to fix
    ("http://localhost:8000/docs", "https", H, "http://localhost:8000/docs"),  # NOT our host
    ("http://evil.example/steal", "https", H, "http://evil.example/steal"),    # never touch a 3rd party
    ("", "https", H, ""),
    (None, "https", H, None),
    ("http://api.metricspro.tech/docs", "https", None, "http://api.metricspro.tech/docs"),  # host unknown
]
for loc, scheme, host, want in LOC:
    got = ft.secure_location(loc, scheme=scheme, host=host)
    check(f"secure_location({loc!r}, {scheme!r}) == {want!r}", got == want, f"got {got!r}")

print("── C. the REAL app: a request behind the edge gets an https Location ────────────")
try:
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app, base_url="http://api.metricspro.tech")
    r = client.get("/docs/", headers={"x-forwarded-proto": "https"}, follow_redirects=False)
    loc = r.headers.get("location", "")
    check("GET /docs/ behind x-forwarded-proto: https → Location is https://",
          r.status_code in (301, 307, 308) and loc.startswith("https://"),
          f"{r.status_code} {loc!r}")

    r = client.get("/api/v1/core/me/", headers={"x-forwarded-proto": "https"}, follow_redirects=False)
    loc = r.headers.get("location", "")
    check("GET /api/v1/core/me/ behind the edge → Location is https:// (the measured regression)",
          r.status_code in (301, 307, 308) and loc.startswith("https://"),
          f"{r.status_code} {loc!r}")

    # NO LOCKOUT: no edge header at all ⇒ plain http is left alone, not forced to https.
    r = client.get("/docs/", follow_redirects=False)
    loc = r.headers.get("location", "")
    check("NO LOCKOUT: a genuine plain-http request keeps its http Location",
          r.status_code in (301, 307, 308) and loc.startswith("http://"), f"{r.status_code} {loc!r}")

    # NEGATIVE CONTROL: with the outbound layer disabled the PRE-FIX downgrade must reappear, or this
    # harness is measuring nothing. (Layer B is a Dockerfile flag, absent under TestClient.)
    os.environ["FORCE_HTTPS_LOCATION"] = "0"
    try:
        r = client.get("/docs/", headers={"x-forwarded-proto": "https"}, follow_redirects=False)
        loc = r.headers.get("location", "")
        check("control: disabling the outbound layer really does restore the downgrade",
              loc.startswith("http://"), f"{loc!r} — the control proves nothing")
    finally:
        os.environ.pop("FORCE_HTTPS_LOCATION", None)
except ImportError as e:
    check("app import (run with the backend's dependencies installed)", False, str(e))

print("── D. layer B: the Dockerfile grants the forwarded-proto trust at the root ──────")
dock = (HERE.parent / "Dockerfile")
dock = dock if dock.exists() else (HERE / "Dockerfile")
txt = dock.read_text(encoding="utf-8", errors="ignore") if dock.exists() else ""
check("a Dockerfile was found", bool(txt), str(dock))
check("the server is started with --forwarded-allow-ips (uvicorn's default 127.0.0.1 ignores the edge)",
      "--forwarded-allow-ips" in txt,
      "without it scope['scheme'] stays 'http' and EVERY generated URL downgrades")
check("the trust is overridable by env, never a bare literal only",
      re.search(r"--forwarded-allow-ips=\$\{FORWARDED_ALLOW_IPS", txt) is not None)

# RULE TWO applies to CODE. The docstrings here quote the production request that was measured, and
# that evidence must survive; it is the executable lines that must carry no host or tenant name. So
# strip every docstring and comment with the parser, then look at what is left.
import ast, io, tokenize  # noqa: E402


def _code_only(src: str) -> str:
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return src
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            d = ast.get_docstring(node, clean=False)
            if d is not None:
                docs.add(d)
    out = []
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.COMMENT:
            continue
        if tok.type == tokenize.STRING:
            try:
                if ast.literal_eval(tok.string) in docs:
                    continue
            except Exception:
                pass
        out.append(tok.string)
    return "\n".join(out)



print("── E. one home, dereferenced — no second copy (RULE: one fact, one home) ────────")
APP = HERE / "app"
readers, rewriters = [], []
RAW = re.compile(r"""x-forwarded-proto""", re.I)
REWRITE = re.compile(r"""replace\s*\(\s*["']http://["']""")
for p in APP.rglob("*.py"):
    t = p.read_text(encoding="utf-8", errors="ignore")
    rel = str(p.relative_to(HERE))
    if RAW.search(_code_only(t)) and rel != "app/core/forwarded_tls.py":
        readers.append(rel)
    if REWRITE.search(_code_only(t)) and rel != "app/core/forwarded_tls.py":
        rewriters.append(rel)
check("app/core/forwarded_tls.py is the ONLY reader of x-forwarded-proto under app/",
      readers == [], str(readers))
check("nothing else rewrites an http:// prefix for itself", rewriters == [], str(rewriters))
main = (APP / "main.py").read_text(encoding="utf-8", errors="ignore")
check("HardeningMiddleware dereferences the one home (it does not carry its own rule)",
      "forwarded_tls" in main and "secure_location" in main,
      "writing the registry without wiring the caller is not a fix")
ftsrc = (APP / "core/forwarded_tls.py").read_text(encoding="utf-8", errors="ignore")
NAMED = re.compile(r"(?i)metricspro|luxelink|cellfonz|vzone|ny ?logistics|boost")
check("the one home's CODE carries no tenant or host literal (docstrings keep the evidence)",
      not NAMED.search(_code_only(ftsrc)), "RULE TWO: no tenant/host names in code")
check("control: the purity check can still see a literal in code",
      NAMED.search(_code_only('x = "metricspro.tech"\n')) is not None,
      "the check is blind — it would pass anything")

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
