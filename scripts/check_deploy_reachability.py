#!/usr/bin/env python3
"""DEPLOY-TIME REACHABILITY PROOF — the site can actually reach its backend (index §40.12).

OWNER INCIDENT 2026-10-03 (the SECOND outage that day). `BACKEND_ORIGIN` was set in the Vercel
production environment to a hostname that does not resolve. `next.config.ts` reads that variable at
BUILD time to construct the `/api/v1` and `/health` rewrites, so every build after it was set baked
the dead address in and every proxied call died at the edge with
`502 DNS_HOSTNAME_RESOLVE_FAILED`. The app rendered perfectly — Vercel serves the pages itself — and
every screen showed $0.00. Every user, at once, for 30 minutes.

WHY THE §40.10 BUILD GATE DID NOT CATCH IT, and cannot. `productionConfigProblems` is a PURE module
loaded by `next.config.ts` (`harness_one_domain_lock.py` holds it to that: no imports, erasable TS).
It can prove the origin is well-SHAPED and not a development address. It cannot prove the host
RESOLVES, because that is a fact about the world at deploy time, not about the string. §23d already
says reachability is a deploy-time fact read from `GET /health`; nothing was reading it.

THE CLASS: a configuration fact that only fails at RUNTIME must be proved against the RUNNING
deployment, not against the build that produced it. This script is that proof, and the workflow
`.github/workflows/deploy-reachability.yml` runs it on every successful production deployment.

WHAT IT PROVES, through the site's own origin (never the backend host directly — that is the whole
point of §40: the browser only ever talks to the customer-facing site):

  1. GET <site>/health            → 200 and a JSON body carrying `commit`. That payload is the
                                    BACKEND's, so reaching it proves the rewrite resolves, connects
                                    and returns the backend rather than the Next.js app or the edge.
  2. GET <site>/api/v1/core/auth-config
                                  → 200 and a JSON body carrying `rbac_enabled`. The `/api/v1`
                                    prefix is a SEPARATE rewrite rule from `/health`, so a prefix
                                    that is dead on its own is caught here. It is also the exact
                                    read whose failure rendered the app open on 2026-10-03, and it
                                    is public (GET-only, `_PUBLIC_EXACT` in tenant_middleware), so
                                    this needs no credentials and never touches tenant data.

A failure here means the deployment is DARK: it will render and serve nothing. That is a failed
check, loudly, within a minute of the deploy — not a report from whoever opens the app next.

Stdlib only, no credentials, read-only GETs:
  python3 scripts/check_deploy_reachability.py [--site https://app.metricspro.tech]
"""
import argparse
import json
import sys
import urllib.error
import urllib.request

DEFAULT_SITE = "https://app.metricspro.tech"
TIMEOUT = 30
# Transient edge/cold-start noise must not fail a good deploy; a dead ADDRESS fails all of them.
ATTEMPTS = 3
RETRY_SLEEP = 5

# (path, the key the body must carry, what reaching it proves)
PROBES = (
    ("/health", "commit",
     "the /health rewrite reaches the backend (the payload is the backend's own)"),
    ("/api/v1/core/auth-config", "rbac_enabled",
     "the /api/v1 rewrite reaches the backend (a separate rewrite rule from /health)"),
)


def _get(url):
    """Return (status, body_text). A non-2xx is returned, not raised — its status is the evidence."""
    req = urllib.request.Request(url, headers={"Accept": "application/json",
                                               "User-Agent": "metricspro-deploy-reachability"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.status, r.read(8192).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, (e.read(8192).decode("utf-8", "replace") if e.fp else "")


def probe(site, path, key):
    """One probe. Returns (ok: bool, detail: str) — detail names what was actually seen."""
    url = site.rstrip("/") + path
    last = ""
    for attempt in range(1, ATTEMPTS + 1):
        try:
            status, body = _get(url)
        except Exception as e:                       # DNS/TLS/connection — the dead-address case
            last = f"{type(e).__name__}: {e}"
        else:
            if status != 200:
                # 502 DNS_HOSTNAME_RESOLVE_FAILED lands here: the edge answered, the backend did not.
                last = f"HTTP {status}; body starts: {body.strip()[:160]!r}"
            else:
                try:
                    data = json.loads(body)
                except ValueError:
                    # HTML from the Next.js app = the rewrite did not install for this prefix.
                    last = f"HTTP 200 but the body is not JSON; starts: {body.strip()[:160]!r}"
                else:
                    if isinstance(data, dict) and key in data:
                        return True, f"HTTP 200, carries {key!r}"
                    last = f"HTTP 200 JSON but no {key!r} key; keys: {sorted(data)[:8] if isinstance(data, dict) else type(data).__name__}"
        if attempt < ATTEMPTS:
            import time
            time.sleep(RETRY_SLEEP)
    return False, last


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--site", default=DEFAULT_SITE,
                    help=f"the customer-facing site origin (default {DEFAULT_SITE})")
    args = ap.parse_args(argv)
    site = args.site.rstrip("/")

    print(f"Deploy reachability — proving {site} can reach its backend (index §40.12)\n")
    failures = []
    for path, key, proves in PROBES:
        ok, detail = probe(site, path, key)
        print(f"  {'PASS' if ok else 'FAIL'}  GET {path}\n        {detail}\n        proves: {proves}")
        if not ok:
            failures.append((path, detail))

    print()
    if failures:
        print("THIS DEPLOYMENT IS DARK — it renders, and it can reach nothing.")
        print("Every screen will show $0.00 and the app cannot read whether login is enforced.\n")
        for path, detail in failures:
            print(f"  {path}: {detail}")
        print("\nMost likely: BACKEND_ORIGIN in the production environment names a host that does not")
        print("resolve, or is malformed. next.config.ts reads it at BUILD time to build the rewrites,")
        print("so correcting it requires a REDEPLOY, not just a save. See index §40.8 / §40.12.")
        return 1
    print(f"OK — {len(PROBES)}/{len(PROBES)} probes reached the backend through {site}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
