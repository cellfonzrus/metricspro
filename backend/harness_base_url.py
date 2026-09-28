"""Lock: an operator-typed service address has ONE normaliser and every caller goes through it (index §16).

Regression (live 2026-09-28): BROWSER_SERVICE_URL was pasted without "https://", so every browser endpoint proxied to
the sweeps worker failed with "Request URL is missing an 'http://' or 'https://' protocol" (surfaced by Supply →
Read catalog). The fix is the mechanism, not the one variable:

  A. base_url() — scheme added (https, or http for a private host), trailing slash dropped, unset stays unset.
  B. The REAL browser_service_url() and the REAL proxy handler: a schemeless address now reaches the worker.
  C. Settings: every *_URL field is normalised (config.model_post_init, by name).
  D. Source lock: nothing in app/ reads BROWSER_SERVICE_URL from the environment except service_role.py.

Run: `python3 harness_base_url.py` from the backend dir. DB-free.
"""
import asyncio
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


from app.core.base_url import base_url  # noqa: E402

print("── A. base_url() ──────────────────────────────────────────────────────────────")
CASES = [
    ("", ""), (None, ""), ("   ", ""),
    ("worker-production.up.railway.app", "https://worker-production.up.railway.app"),
    ("worker-production.up.railway.app/", "https://worker-production.up.railway.app"),
    ("  https://worker.example.com/  ", "https://worker.example.com"),
    ("http://worker.example.com", "http://worker.example.com"),
    ("HTTPS://Worker.Example.com", "HTTPS://Worker.Example.com"),
    ("//worker.example.com", "https://worker.example.com"),
    ("'worker.example.com'", "https://worker.example.com"),
    ("worker.railway.internal:8080", "http://worker.railway.internal:8080"),
    ("localhost:8000", "http://localhost:8000"),
    ("127.0.0.1:8000", "http://127.0.0.1:8000"),
    ("10.0.0.5:8000", "http://10.0.0.5:8000"),
    ("worker:8080", "http://worker:8080"),
    ("api.example.com/base/", "https://api.example.com/base"),
]
for raw, want in CASES:
    got = base_url(raw)
    check(f"base_url({raw!r}) == {want!r}", got == want, f"got {got!r}")

print("── B. the real browser_service_url() + proxy handler ────────────────────────────")
from app.core import service_role as SR  # noqa: E402

os.environ["BROWSER_SERVICE_URL"] = "worker-production.up.railway.app/"
check("schemeless BROWSER_SERVICE_URL comes back usable",
      SR.browser_service_url() == "https://worker-production.up.railway.app", SR.browser_service_url())
os.environ["BROWSER_SERVICE_URL"] = ""
check("unset stays unset (no proxy mode)", SR.browser_service_url() == "")

try:
    import httpx
    from starlette.requests import Request
    import app.main as M

    seen = {}

    class _Resp:
        status_code = 200
        content = b'{"ok":true}'
        headers = {"content-type": "application/json"}

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def request(self, method, url, headers=None, content=None):
            httpx.URL(url)                      # the real parser that raised on the live deploy
            if not re.match(r"^https?://", url):
                raise httpx.UnsupportedProtocol("Request URL is missing an 'http://' or 'https://' protocol.")
            seen["url"] = url
            return _Resp()

    async def _body():
        return {"type": "http.request", "body": b"", "more_body": False}

    scope = {"type": "http", "method": "POST", "path": "/api/v1/supply/vendors/v1/catalog/read",
             "query_string": b"org_id=o1", "headers": [(b"authorization", b"Bearer t")], "root_path": ""}
    real = httpx.AsyncClient
    httpx.AsyncClient = _Client
    os.environ["BROWSER_SERVICE_URL"] = "worker-production.up.railway.app"
    try:
        r = asyncio.run(M._proxy_browser_work(Request(scope, _body), SR.BrowserWorkProxy()))
    finally:
        httpx.AsyncClient = real
        os.environ.pop("BROWSER_SERVICE_URL", None)
    check("proxy forwards a schemeless worker address (was: 502 'missing protocol')", r.status_code == 200,
          f"status {r.status_code} {getattr(r, 'body', b'')[:160]!r}")
    check("…to https://<worker><path>?<query>",
          seen.get("url") == "https://worker-production.up.railway.app/api/v1/supply/vendors/v1/catalog/read?org_id=o1",
          seen.get("url"))
except ImportError as e:
    check("app imports (run with the backend's dependencies installed)", False, str(e))

print("── C. settings: every *_URL field is normalised ─────────────────────────────────")
try:
    from app.core.config import Settings
    url_fields = sorted(f for f in Settings.model_fields if f.endswith("_URL"))
    os.environ.update({f: "host-for-" + f.lower().replace("_", "-") + ".example.com/" for f in url_fields})
    s = Settings()
    for f in url_fields:
        v = getattr(s, f)
        check(f"Settings.{f} normalised", v.startswith("https://") and not v.endswith("/"), repr(v))
    for f in url_fields:
        os.environ.pop(f, None)
    check("defaults unchanged", Settings().API_PUBLIC_URL == "https://metricspro-production.up.railway.app")
except ImportError as e:
    check("settings import (run with the backend's dependencies installed)", False, str(e))

print("── D. source lock: one reader of BROWSER_SERVICE_URL ────────────────────────────")
READ = re.compile(r"""(environ(\.get)?|getenv)\s*[\(\[]\s*['"]BROWSER_SERVICE_URL['"]""")
readers = []
for p in (HERE / "app").rglob("*.py"):
    if READ.search(p.read_text(encoding="utf-8", errors="ignore")):
        readers.append(str(p.relative_to(HERE)))
check("only app/core/service_role.py reads BROWSER_SERVICE_URL from the environment",
      readers == ["app/core/service_role.py"], str(readers))
sr = (HERE / "app/core/service_role.py").read_text()
check("service_role.browser_service_url() returns base_url(...)",
      re.search(r"def browser_service_url\(\)[\s\S]{0,400}?return base_url\(", sr) is not None)
cfg = (HERE / "app/core/config.py").read_text()
check("config.py normalises every *_URL field through base_url()",
      re.search(r'endswith\("_URL"\)[\s\S]{0,160}base_url\(', cfg) is not None)

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
