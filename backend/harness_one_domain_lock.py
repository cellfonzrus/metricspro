#!/usr/bin/env python3
"""LOCK — ONE DOMAIN: the browser talks only to the customer-facing site (owner 2026-09-28, index §40).

Owner: "we are using metricspro-five.vercel.app/login to log in, we should use metricspro.tech/xxxxx as
customer facing and mask all urls so nobody knows how to hack in".

THE DEFECT. Fifteen frontend files each read `process.env.NEXT_PUBLIC_API_URL` and built
`${API_URL}/api/v1/...`, so the browser called the backend host directly (network tab, JS bundle, CSP),
and "where is the API" had fifteen answers. THE CLASS: the location of the backend — and of the
customer-facing site — is a fact with ONE home, `frontend/src/lib/apiBase.ts`, dereferenced by every
caller; the routing policy built from it (proxy rewrites, canonical-host redirect, CSP) is installed by
`frontend/next.config.ts` from that home and `frontend/site-routing.ts`.

THIS FAILS THE BUILD WHEN:
  1. any frontend file other than src/lib/apiBase.ts reads NEXT_PUBLIC_API_URL, NEXT_PUBLIC_API_DIRECT_ORIGIN,
     BACKEND_ORIGIN or NEXT_PUBLIC_SITE_URL from process.env (dot, bracket or destructuring form);
  2. a literal `railway.app` or `vercel.app` appears anywhere under frontend/src, or `railway.app` in the
     frontend config files (next.config.ts / site-routing.ts);
  3. a frontend file prefixes an interpolated origin onto a backend path (`${X}/api/v1…`) instead of asking
     apiUrl() — the pattern the fifteen copies used;
  4. next.config.ts stops installing the rewrites / the canonical-host redirect / the derived connect-src
     from the two homes, or the homes stop exporting them;
  5. apiBase.ts stops proxying /api/v1 and /health, or apiUpload stops asking for the DIRECT class;
  6. a backend endpoint that launches a browser (`require_browser_service()`) — synchronous work that can
     outrun the platform proxy's 120 s limit — is not matched by apiBase.ts's DIRECT_ROUTES. The set is
     READ from the backend routers, so an endpoint added tomorrow is covered without touching this file.
  7. next.config.ts stops asserting the PRODUCTION configuration, or apiBase.ts stops exporting the rules
     (index §40.10, owner incident 2026-10-03). Every fallback in apiBase.ts is written to keep a build
     working when a variable is missing, which means a production build with no backend address silently
     proxies the whole API to `http://localhost:8000` and ships: green deploy, rendering pages, every API
     call dead. The fallbacks are right for `next dev` and for a preview and they stay; a PRODUCTION build
     must assert what it cannot work without and FAIL. Without this rule the gate can be deleted and the
     patchwork quietly returns.
Each rule has a negative control below: the rule is run on a synthetic violation and must fire.

Stdlib only, DB-free, no network: `python3 backend/harness_one_domain_lock.py`.
"""
import ast
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
FRONTEND = os.path.join(ROOT, "frontend")
HOME = "src/lib/apiBase.ts"                     # the ONE home (relative to frontend/)
ROUTING = "site-routing.ts"
CONFIG = "next.config.ts"
LOCATION_VARS = ("NEXT_PUBLIC_API_URL", "NEXT_PUBLIC_API_DIRECT_ORIGIN", "BACKEND_ORIGIN", "NEXT_PUBLIC_SITE_URL")
SKIP_DIRS = {"node_modules", ".next", "out", "build", "scratchpad"}
EXTS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts")

PASS = FAIL = 0


def check(name, cond, detail=None):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}")
        for d in (detail or [])[:20]:
            print(f"          {d}")


def frontend_files():
    """{path relative to frontend/: text} for every source file the app builds from (src + root configs).
    Root-level prove_* harnesses are tests, not the app, and carry fixture hostnames on purpose."""
    out = {}
    for dp, dns, fns in os.walk(FRONTEND):
        dns[:] = [d for d in dns if d not in SKIP_DIRS]
        rel_dir = os.path.relpath(dp, FRONTEND)
        for fn in fns:
            if not fn.endswith(EXTS):
                continue
            rel = os.path.normpath(os.path.join(rel_dir, fn)).replace(os.sep, "/")
            if rel_dir == "." and fn.startswith(("prove_", "harness_")):
                continue
            with open(os.path.join(dp, fn), encoding="utf-8", errors="replace") as f:
                out[rel] = f.read()
    return out


# ── rule 1: only the home reads the location env vars ──────────────────────────────────────────────
_ENV_DOT = re.compile(r"process\.env(?:\.|\[\s*['\"`])(" + "|".join(LOCATION_VARS) + r")\b")
_ENV_DESTRUCT = re.compile(r"\{([^}]*)\}\s*=\s*process\.env\b")


def env_readers(files):
    bad = []
    for rel, text in sorted(files.items()):
        if rel == HOME:
            continue
        for m in _ENV_DOT.finditer(text):
            bad.append(f"{rel}: reads process.env.{m.group(1)} — import it from {HOME} instead")
        for m in _ENV_DESTRUCT.finditer(text):
            for v in LOCATION_VARS:
                if re.search(rf"\b{v}\b", m.group(1)):
                    bad.append(f"{rel}: destructures {v} from process.env — import it from {HOME} instead")
    return bad


# ── rule 2: no platform hostnames in the app's source ──────────────────────────────────────────────
def host_literals(files):
    bad = []
    for rel, text in sorted(files.items()):
        in_src = rel.startswith("src/")
        for i, line in enumerate(text.splitlines(), 1):
            if in_src and ("railway.app" in line or "vercel.app" in line):
                bad.append(f"{rel}:{i}: platform hostname literal in the app source: {line.strip()[:120]}")
            elif rel in (CONFIG, ROUTING) and "railway.app" in line:
                bad.append(f"{rel}:{i}: backend hostname literal in the site config: {line.strip()[:120]}")
    return bad


# ── rule 3: nobody prefixes an origin onto a backend path ──────────────────────────────────────────
_PREFIXED = re.compile(r"\$\{[^}]+\}/(?:api/v1|health)\b")


def origin_prefixers(files):
    bad = []
    for rel, text in sorted(files.items()):
        if rel == HOME:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if _PREFIXED.search(line) and not line.lstrip().startswith(("//", "*", "#")):
                bad.append(f"{rel}:{i}: builds `${{origin}}/api/v1…` itself — use apiUrl()/absoluteApiUrl(): {line.strip()[:120]}")
    return bad


# ── rule 4 + 5: the config installs the policy from the homes; the homes export it ─────────────────
def config_wiring(files):
    bad = []
    cfg, home, routing = files.get(CONFIG, ""), files.get(HOME, ""), files.get(ROUTING, "")
    client = files.get("src/lib/client.ts", "")
    need = [
        (cfg, r"from\s+['\"]\./src/lib/apiBase['\"]", f"{CONFIG} does not import the one home ({HOME})"),
        (cfg, r"from\s+['\"]\./site-routing['\"]", f"{CONFIG} does not import {ROUTING}"),
        (cfg, r"async\s+rewrites\s*\(\s*\)\s*\{\s*return\s+apiRewrites\(\s*\)", f"{CONFIG}: rewrites() must return apiRewrites()"),
        (cfg, r"async\s+redirects\s*\(\s*\)\s*\{\s*return\s+canonicalHostRedirects\(", f"{CONFIG}: redirects() must return canonicalHostRedirects(...)"),
        (cfg, r"connectSrc\(\s*directOrigin\(\s*\)\s*\)", f"{CONFIG}: the CSP connect-src must be connectSrc(directOrigin())"),
        (home, r"export\s+function\s+apiRewrites\b", f"{HOME} no longer exports apiRewrites"),
        (home, r"export\s+function\s+apiUrl\b", f"{HOME} no longer exports apiUrl"),
        (home, r"BACKEND_PATH_PREFIXES[^=]*=\s*\[[^\]]*'/api/v1'", f"{HOME}: BACKEND_PATH_PREFIXES must proxy '/api/v1'"),
        (home, r"BACKEND_PATH_PREFIXES[^=]*=\s*\[[^\]]*'/health'", f"{HOME}: BACKEND_PATH_PREFIXES must proxy '/health'"),
        (routing, r"export\s+function\s+canonicalHostRedirects\b", f"{ROUTING} no longer exports canonicalHostRedirects"),
        (routing, r"VERCEL_ENV\s*!==\s*'production'\)\s*return\s*\[\]", f"{ROUTING}: the redirect must be production-only (previews/localhost untouched)"),
        (routing, r"permanent:\s*true", f"{ROUTING}: the canonical-host redirect must be permanent (308)"),
        (client, r"apiUrl\(\s*withOrgScope\(path\)\s*,\s*'direct'\s*\)", "src/lib/client.ts: apiUpload must ask apiUrl(..., 'direct') — uploads must not ride the proxy"),
        # Rule 7 — the production configuration gate (index §40.10).
        (home, r"export\s+function\s+productionConfigProblems\b",
         f"{HOME} no longer exports productionConfigProblems — a misconfigured production build would ship silently again"),
        (home, r"if\s*\(isLocalHost\(backend\)\)",
         f"{HOME}: productionConfigProblems must refuse a backend origin no browser can reach"),
        (cfg, r"productionConfigProblems\(\s*API_ENV\s*,\s*process\.env\.VERCEL_ENV\s*===\s*[\"']production[\"']\s*\)",
         f"{CONFIG} must call productionConfigProblems(API_ENV, VERCEL_ENV === 'production')"),
        (cfg, r"CONFIG_PROBLEMS\.length\s*\)\s*\{\s*\n?\s*throw\s+new\s+Error",
         f"{CONFIG} must THROW on a production config problem — reporting it without failing the build ships the dark deploy anyway"),
    ]
    for text, pattern, msg in need:
        if not re.search(pattern, text):
            bad.append(msg)
    if re.search(r"connect-src[^\"\n]*\*\.up\.", cfg):
        bad.append(f"{CONFIG}: a wildcard backend host is back in connect-src")
    return bad


# ── rule 6: every browser-launching endpoint is DIRECT ─────────────────────────────────────────────
def direct_routes(home_text):
    m = re.search(r"DIRECT_ROUTES[^=]*=\s*\[(.*?)\n\]", home_text, re.S)
    if not m:
        return []
    return re.findall(r"^\s*'([^']+)'", m.group(1), re.M)


def _mount_prefixes(main_text):
    """{module path 'app/modules/x/router.py': mount prefix} from main.py's imports + include_router."""
    alias_to_mod = {}
    for mod, alias in re.findall(r"^from\s+(app\.modules\.[\w.]+)\s+import\s+router\s+as\s+(\w+)", main_text, re.M):
        alias_to_mod[alias] = mod.replace(".", "/") + ".py"
    out = {}
    for alias, prefix in re.findall(r"app\.include_router\(\s*(\w+)\s*,\s*prefix=\"([^\"]*)\"", main_text):
        if alias in alias_to_mod:
            out[alias_to_mod[alias]] = prefix
    return out


def browser_endpoints(router_texts, main_text):
    """[(full path, 'file:function')] for every route handler that calls require_browser_service()."""
    mounts = _mount_prefixes(main_text)
    out = []
    for rel, text in sorted(router_texts.items()):
        tree = ast.parse(text)
        prefix = ""
        for node in ast.walk(tree):
            if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
                    and getattr(node.value.func, "id", "") == "APIRouter"):
                for kw in node.value.keywords:
                    if kw.arg == "prefix" and isinstance(kw.value, ast.Constant):
                        prefix = kw.value.value
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            calls = any(isinstance(n, ast.Call) and getattr(n.func, "id", "") == "require_browser_service"
                        for n in ast.walk(fn))
            if not calls:
                continue
            for dec in fn.decorator_list:
                if (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)
                        and dec.func.attr in ("get", "post", "put", "patch", "delete")
                        and dec.args and isinstance(dec.args[0], ast.Constant)):
                    path = mounts.get(rel, "/api/v1") + prefix + dec.args[0].value
                    out.append((path, f"{rel}:{fn.name}"))
    return out


def uncovered(endpoints, patterns):
    res = [re.compile(p) for p in patterns]
    bad = []
    for path, where in endpoints:
        concrete = re.sub(r"\{[^}]+\}", "x", path)
        if not any(r.search(concrete) for r in res):
            bad.append(f"{path} ({where}) launches a browser but is not in DIRECT_ROUTES ({HOME}) — "
                       "it would ride the platform proxy and die at its 120 s limit")
    return bad


def backend_routers():
    out = {}
    mods = os.path.join(HERE, "app", "modules")
    for dp, _dns, fns in os.walk(mods):
        for fn in fns:
            if fn.endswith(".py"):
                p = os.path.join(dp, fn)
                with open(p, encoding="utf-8") as f:
                    t = f.read()
                if "require_browser_service()" in t:
                    out[os.path.relpath(p, HERE).replace(os.sep, "/")] = t
    return out


def main():
    files = frontend_files()
    with open(os.path.join(HERE, "app", "main.py"), encoding="utf-8") as f:
        main_text = f.read()
    routers = backend_routers()
    patterns = direct_routes(files.get(HOME, ""))
    endpoints = browser_endpoints(routers, main_text)

    print("1. only the one home reads the backend / site location")
    check(f"no frontend file but {HOME} reads {', '.join(LOCATION_VARS)}", not env_readers(files), env_readers(files))
    check("the home exists and reads them", HOME in files and all(f"process.env.{v}" in files[HOME] for v in LOCATION_VARS))
    print("2. no platform hostnames in the app source")
    check("no `railway.app` / `vercel.app` literal under frontend/src; no `railway.app` in the site config",
          not host_literals(files), host_literals(files))
    print("3. nobody prefixes an origin onto a backend path")
    check("no `${origin}/api/v1…` outside the home", not origin_prefixers(files), origin_prefixers(files))
    print("4/5. the config installs the policy from the homes")
    check("rewrites, canonical redirect, connect-src, upload class all wired", not config_wiring(files), config_wiring(files))
    print("6. every browser-launching endpoint is DIRECT")
    check("the backend declares browser-launching endpoints (the scan is not empty)", len(endpoints) >= 10,
          [f"found {len(endpoints)}"])
    check("DIRECT_ROUTES parsed from the home", len(patterns) >= 1, [f"found {len(patterns)}"])
    check("every require_browser_service() endpoint is matched by DIRECT_ROUTES", not uncovered(endpoints, patterns),
          uncovered(endpoints, patterns))
    check("the long synchronous payables rebuild is DIRECT", any(re.search(p, "/api/v1/payables/rebuild") for p in patterns))

    print("N. negative controls (each rule fires on a synthetic violation)")
    check("control 1: a page reading process.env.NEXT_PUBLIC_API_URL is caught",
          env_readers({"src/app/x/page.tsx": "const A = process.env.NEXT_PUBLIC_API_URL || ''"}) != [])
    check("control 1b: bracket and destructuring reads are caught",
          len(env_readers({"src/a.ts": "process.env['BACKEND_ORIGIN']",
                           "src/b.ts": "const { NEXT_PUBLIC_SITE_URL: s } = process.env"})) == 2)
    check("control 1c: the home itself is allowed",
          env_readers({HOME: "process.env.NEXT_PUBLIC_API_URL"}) == [])
    check("control 2: a railway literal in a component is caught",
          host_literals({"src/components/X.tsx": "fetch('https://x.up.railway.app/api/v1/y')"}) != [])
    check("control 2b: a vercel.app literal in src is caught, but site-routing.ts may name the platform host",
          host_literals({"src/lib/y.ts": "const h = 'metricspro-five.vercel.app'"}) != []
          and host_literals({ROUTING: "const PLATFORM_HOST_RE = '(?:[a-z0-9-]+\\\\.)*vercel\\\\.app'"}) == [])
    check("control 3: `${API_URL}/api/v1/…` is caught",
          origin_prefixers({"src/app/p.tsx": "fetch(`${API_URL}/api/v1/core/me`)"}) != [])
    check("control 3b: apiUrl(`/api/v1/${id}`) is fine",
          origin_prefixers({"src/app/p.tsx": "fetch(apiUrl(`/api/v1/referral/${id}`))"}) == [])
    broken = dict(files)
    broken[CONFIG] = files.get(CONFIG, "").replace("return apiRewrites()", "return []")
    check("control 4: a next.config.ts whose rewrites() stops returning apiRewrites() is caught",
          any("rewrites()" in v for v in config_wiring(broken)))
    broken = dict(files)
    broken[CONFIG] = files.get(CONFIG, "") + "\n// \"connect-src 'self' https://*.up.railway.app\"\n"
    check("control 4b: a wildcard backend host back in connect-src is caught",
          any("wildcard" in v for v in config_wiring(broken)))
    broken = dict(files)
    broken[CONFIG] = files.get(CONFIG, "").replace("throw new Error", "console.warn")
    check("control 7: a next.config.ts that only WARNS instead of failing the build is caught",
          any("must THROW" in v for v in config_wiring(broken)))
    broken = dict(files)
    broken[CONFIG] = re.sub(r"productionConfigProblems\([^)]*\)", "[]", files.get(CONFIG, ""))
    check("control 7b: a next.config.ts that stops calling the gate at all is caught",
          any("productionConfigProblems(API_ENV" in v for v in config_wiring(broken)))
    broken = dict(files)
    broken[HOME] = files.get(HOME, "").replace("export function productionConfigProblems", "function productionConfigProblems")
    check("control 7c: a home that stops exporting the gate is caught",
          any("no longer exports productionConfigProblems" in v for v in config_wiring(broken)))
    fake_router = ('router = APIRouter(prefix="/widgets")\n'
                   '@router.post("/{wid}/scrape")\n'
                   'def scrape(wid: str):\n'
                   '    require_browser_service()\n')
    fake_main = ('from app.modules.widgets.router import router as widgets_router\n'
                 'app.include_router(widgets_router, prefix="/api/v1")\n')
    eps = browser_endpoints({"app/modules/widgets/router.py": fake_router}, fake_main)
    check("control 6: a NEW browser-launching endpoint is found by the scan",
          eps == [("/api/v1/widgets/{wid}/scrape", "app/modules/widgets/router.py:scrape")], eps)
    check("control 6b: …and flagged when DIRECT_ROUTES does not cover it", uncovered(eps, patterns) != [])

    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
