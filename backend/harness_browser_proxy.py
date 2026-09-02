"""Harness for the browser-work proxy (VidaPay autonomous-pull restoration, 2026-09-02).

Drives the REAL require_browser_service() gate and the REAL app-level BrowserWorkProxy handler with
httpx stubbed. No network, no DB. What it proves:

  THE GATE (service_role.py) — config, never code
  • SERVICE_ROLE unset/sweeps → browser endpoints pass, nothing raised
  • SERVICE_ROLE=api, no BROWSER_SERVICE_URL → exactly the old 503 with the shipped message
  • SERVICE_ROLE=api + BROWSER_SERVICE_URL → BrowserWorkProxy raised instead (proxy mode)

  THE FORWARD (main.py handler)
  • method, path, query string and body arrive at the worker URL verbatim
  • Authorization and x-active-org travel; host/content-length/connection/accept-encoding do not
  • the worker's status code, body and content-type are relayed verbatim (non-200 included)
  • a dead worker yields a clean 502 "browser service unreachable", never a stack trace

Run: `python3 harness_browser_proxy.py` from the backend dir.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

_pass = 0
_fail = 0
FAILED = []


def check(name, cond):
    global _pass, _fail
    if cond:
        _pass += 1
        print(f"  PASS  {name}")
    else:
        _fail += 1
        FAILED.append(name)
        print(f"  FAIL  {name}")


def run():
    from fastapi import HTTPException
    from app.core import service_role as SR

    print("── 1. the gate: role × BROWSER_SERVICE_URL matrix ─────────────────────────────")
    os.environ["SERVICE_ROLE"] = "sweeps"
    os.environ.pop("BROWSER_SERVICE_URL", None)
    try:
        SR.require_browser_service()
        check("worker role: browser endpoints pass", True)
    except Exception:
        check("worker role: browser endpoints pass", False)

    os.environ["SERVICE_ROLE"] = "api"
    try:
        SR.require_browser_service()
        check("api role, no proxy URL: 503", False)
    except HTTPException as e:
        check("api role, no proxy URL: 503", e.status_code == 503 and e.detail == SR.BLOCKED_MESSAGE)
    except SR.BrowserWorkProxy:
        check("api role, no proxy URL: 503", False)

    os.environ["BROWSER_SERVICE_URL"] = "https://worker.example.test/"
    try:
        SR.require_browser_service()
        check("api role + proxy URL: BrowserWorkProxy raised", False)
    except SR.BrowserWorkProxy:
        check("api role + proxy URL: BrowserWorkProxy raised", True)
    check("proxy URL is normalized (trailing slash stripped)",
          SR.browser_service_url() == "https://worker.example.test")

    print("── 2. the forward: the original request reaches the worker, its answer returns ─")
    import httpx
    from starlette.requests import Request
    from app.main import _proxy_browser_work

    captured = {}

    class _FakeResp:
        status_code = 418
        content = b'{"ok":false,"detail":"teapot from worker"}'
        headers = {"content-type": "application/json"}

    class _FakeClient:
        def __init__(self, *a, **k):
            captured['timeout'] = k.get('timeout')

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def request(self, method, url, headers=None, content=None):
            captured.update(method=method, url=url, headers=dict(headers or {}), content=content)
            return _FakeResp()

    def _req(method='POST', path='/api/v1/commcalc/data-sources/s1/login', query=b'confirm=true',
             body=b'{"code":"123456"}'):
        scope = {'type': 'http', 'method': method, 'path': path, 'query_string': query,
                 'headers': [(b'host', b'prod.example'), (b'authorization', b'Bearer tok'),
                             (b'x-active-org', b'org-1'), (b'content-type', b'application/json'),
                             (b'content-length', str(len(body)).encode()),
                             (b'connection', b'keep-alive'), (b'accept-encoding', b'gzip')],
                 'scheme': 'https', 'server': ('prod.example', 443), 'client': ('1.2.3.4', 1)}
        sent = {'done': False}

        async def receive():
            if sent['done']:
                return {'type': 'http.disconnect'}
            sent['done'] = True
            return {'type': 'http.request', 'body': body, 'more_body': False}
        return Request(scope, receive)

    real_client = httpx.AsyncClient
    httpx.AsyncClient = _FakeClient
    try:
        resp = asyncio.run(_proxy_browser_work(_req(), SR.BrowserWorkProxy()))
        check("method/path/query forwarded verbatim",
              captured.get('method') == 'POST'
              and captured.get('url') == 'https://worker.example.test'
                                         '/api/v1/commcalc/data-sources/s1/login?confirm=true')
        check("body forwarded verbatim", captured.get('content') == b'{"code":"123456"}')
        hdrs = {k.lower(): v for k, v in (captured.get('headers') or {}).items()}
        check("auth + org headers travel",
              hdrs.get('authorization') == 'Bearer tok' and hdrs.get('x-active-org') == 'org-1')
        check("host/content-length/connection/accept-encoding are dropped",
              not ({'host', 'content-length', 'connection', 'accept-encoding'} & set(hdrs)))
        check("worker status + body + content-type relayed verbatim (non-200 included)",
              resp.status_code == 418 and resp.body == _FakeResp.content
              and resp.media_type == 'application/json')
        check("timeout is generous enough for an inline Chromium 2FA verify",
              (captured.get('timeout') or 0) >= 120)

        class _DeadClient(_FakeClient):
            async def request(self, *a, **k):
                raise httpx.ConnectError("boom")
        httpx.AsyncClient = _DeadClient
        resp = asyncio.run(_proxy_browser_work(_req(), SR.BrowserWorkProxy()))
        check("dead worker → clean 502, never a stack trace",
              resp.status_code == 502 and b'browser service unreachable' in resp.body)
    finally:
        httpx.AsyncClient = real_client
        os.environ.pop("BROWSER_SERVICE_URL", None)
        os.environ.pop("SERVICE_ROLE", None)

    print(f"\n{_pass} passed, {_fail} failed")
    if FAILED:
        print("FAILED:", *FAILED, sep="\n  - ")
        sys.exit(1)


if __name__ == "__main__":
    run()
