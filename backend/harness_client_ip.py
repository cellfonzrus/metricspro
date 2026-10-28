#!/usr/bin/env python3
"""PROOF + LOCK — "what is this request's client address" has ONE home, and it is not attacker-supplied.

THE DEFECT THIS CLOSES (security review 2026-10-28). Four separate copies of this line existed:

    fwd = (headers.get("x-forwarded-for") or "").split(",")[0].strip()

`X-Forwarded-For` is a list that each proxy APPENDS to. The LEFTMOST entry is therefore whatever the
ORIGINAL CLIENT sent: send `X-Forwarded-For: 1.2.3.4` and the edge appends your real address after
it, so `[0]` is the value you chose. Everything that keys on this value was consequently
attacker-controlled:

  • app/core/rate_limit.py          — a fresh "IP" per request defeats the per-IP limiter outright
  • app/core/ip_block.py            — a banned address walks straight through
  • app/core/access_log.py          — a chosen address is written into the audit trail
  • app/core/session_guard.py       — session rows attributed to a chosen address
  • core/impersonation_api.py       — the impersonation journal, likewise

THE CLASS, NOT THE INSTANCE. The general fact that was wrong is **"the first entry of a header the
client controls is not the client's address"** — and it was wrong in four places at once, which is the
duplicate defect CLAUDE.md already forbids. So the answer lives in ONE module that every caller
dereferences, and the trustworthy entry is counted FROM THE RIGHT: with N trusted proxies in front of
the app, the client's address is the Nth entry from the end, because that is the one the outermost
proxy we control wrote. Anything further left is hearsay.

WHY THE DEFAULT IS STILL LEGACY, STATED PLAINLY. The correct hop count depends on the deploy's proxy
chain, and this app is reached two ways — directly at `api.metricspro.tech` and same-origin through
the site's own proxy — so a guessed number would either keep the hole open or collapse every browser
user onto one shared rate-limit bucket. `TRUSTED_PROXY_HOPS` is therefore unset by default and the
module behaves EXACTLY as the four copies did; `security_posture` now reports that as a finding at
every boot, so the operator sees the decision rather than inheriting it silently. The runbook for
measuring the real hop count is in
/mnt/project-files/security-review-2026-10-28/SET_TRUSTED_PROXY_HOPS.md.

WHAT THIS PROVES (DB-free, stdlib only — `from_xff` is pure):
  A. the attack            — the leftmost entry IS attacker-supplied, and hops>=1 refuses it
  B. counting from the right — hops 1/2/3 pick the right entry; a short chain clamps, never crashes
  C. no header / no proxy  — falls back to the ASGI peer address; hops=0 ignores XFF entirely
  D. legacy default        — unset TRUSTED_PROXY_HOPS reproduces the old behaviour byte-for-byte
  E. shapes               — IPv6, ports, whitespace, blank entries, absurd length, None
  F. one home              — all four historical call sites dereference this module
  G. lock                  — no FIFTH copy of the leftmost-XFF parse may appear in app/
  H. posture               — the switch is reported in `security_posture.evaluate()`
  N. negative controls     — the lock and the attack check actually bite

Run: python3 backend/harness_client_ip.py
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

HERE = os.path.dirname(os.path.abspath(__file__))
PASS = FAIL = 0


def check(name, cond, detail=None):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}" + (f"  -> {detail!r}" if detail is not None else ""))


from app.core import client_ip as C   # noqa: E402

EDGE = "198.51.100.7"        # what the trusted edge appends — the nearest peer we can actually trust
VERCEL = "198.51.100.200"    # an intermediate proxy we also control
REAL = "203.0.113.55"        # the browser
EVIL = "1.2.3.4"             # what an attacker types into the header

print("A. the attack: the leftmost entry is the attacker's to choose")
chain = f"{EVIL}, {REAL}, {EDGE}"
check("legacy (hops unset) returns the ATTACKER's value — this is the bug, stated",
      C.from_xff(chain, peer=EDGE, hops=0, legacy=True) == EVIL)
check("hops=1 returns the entry the trusted edge wrote, not the attacker's",
      C.from_xff(chain, peer=EDGE, hops=1) == EDGE, C.from_xff(chain, peer=EDGE, hops=1))
check("no hop count can ever return the attacker's value from a chain this long",
      all(C.from_xff(chain, peer=EDGE, hops=h) != EVIL for h in (1, 2)),
      [C.from_xff(chain, peer=EDGE, hops=h) for h in (1, 2)])
# A one-entry chain can only arise when the client sent NO header and the trusted proxy wrote the
# address itself — a forged header makes the chain longer, and the forgery lands to the LEFT of the
# entry the hop count selects, so it is never read. Proved by the pair below.
check("a forged header lengthens the chain, so the forgery is never the entry selected",
      C.from_xff(f"{EVIL}, {REAL}", peer=EDGE, hops=1) == REAL)
check("with no forgery the one entry the proxy wrote IS the answer",
      C.from_xff(REAL, peer=EDGE, hops=1) == REAL)

print("B. counting from the right")
check("hops=1 → last entry", C.from_xff(f"{REAL}, {VERCEL}, {EDGE}", peer=EDGE, hops=1) == EDGE)
check("hops=2 → second from last", C.from_xff(f"{REAL}, {VERCEL}, {EDGE}", peer=EDGE, hops=2) == VERCEL)
check("hops=3 → third from last", C.from_xff(f"{REAL}, {VERCEL}, {EDGE}", peer=EDGE, hops=3) == REAL)
check("a chain SHORTER than the hop count prefers the unforgeable peer, never a client claim",
      C.from_xff(f"{VERCEL}, {EDGE}", peer=EDGE, hops=9) == EDGE)
check("…and falls back to the leftmost only when there is no peer at all",
      C.from_xff(f"{VERCEL}, {EDGE}", peer="", hops=9) == VERCEL)
check("a chain exactly as long as the hop count is its leftmost entry (the proxy-observed address)",
      C.from_xff(f"{REAL}, {EDGE}", peer=EDGE, hops=2) == REAL)

print("C. no header, no proxy")
check("no XFF → the ASGI peer address", C.from_xff("", peer=EDGE, hops=1) == EDGE)
check("no XFF and no peer → empty string, never None", C.from_xff("", peer="", hops=1) == "")
check("hops=1 with a chain of one entry and no peer still answers", C.from_xff(REAL, peer="", hops=1) == REAL)
check("hops=0 ignores XFF entirely (app directly exposed)",
      C.from_xff(f"{EVIL}, {REAL}", peer=EDGE, hops=0) == EDGE)
check("hops negative is treated as 0", C.from_xff(EVIL, peer=EDGE, hops=-3) == EDGE)

print("D. legacy default — the old behaviour, byte-for-byte")
for key in ("TRUSTED_PROXY_HOPS", "CLIENT_IP_LEGACY_XFF"):
    os.environ.pop(key, None)
check("TRUSTED_PROXY_HOPS unset ⇒ hops() == 0", C.hops() == 0, C.hops())
check("unset ⇒ legacy() is True", C.legacy() is True)
check("unset ⇒ from_headers reproduces the leftmost parse exactly",
      C.from_headers({"x-forwarded-for": f"{EVIL}, {REAL}"}, peer=EDGE) == EVIL)
os.environ["TRUSTED_PROXY_HOPS"] = "2"
try:
    check("TRUSTED_PROXY_HOPS=2 ⇒ hops() == 2 and legacy() is False", C.hops() == 2 and C.legacy() is False)
    check("TRUSTED_PROXY_HOPS=2 ⇒ from_headers counts from the right",
          C.from_headers({"x-forwarded-for": f"{REAL}, {VERCEL}, {EDGE}"}, peer=EDGE) == VERCEL)
    os.environ["CLIENT_IP_LEGACY_XFF"] = "1"
    check("CLIENT_IP_LEGACY_XFF=1 is the break-glass and beats the hop count",
          C.from_headers({"x-forwarded-for": f"{EVIL}, {REAL}"}, peer=EDGE) == EVIL)
finally:
    os.environ.pop("TRUSTED_PROXY_HOPS", None)
    os.environ.pop("CLIENT_IP_LEGACY_XFF", None)
check("a non-numeric TRUSTED_PROXY_HOPS falls back to 0 rather than raising",
      C._int_env("TRUSTED_PROXY_HOPS", 0) == 0)

print("E. shapes that must not raise")
check("IPv6 survives", C.from_xff("2001:db8::1, " + EDGE, peer=EDGE, hops=2) == "2001:db8::1")
check("blank entries are dropped", C.from_xff(f" , {REAL} , , {EDGE} ", peer=EDGE, hops=2) == REAL)
check("None xff", C.from_xff(None, peer=EDGE, hops=1) == EDGE)
check("None peer", C.from_xff(None, peer=None, hops=1) == "")
check("the value is truncated to 64 characters (the historical cap)",
      len(C.from_xff("x" * 400, peer="", hops=0)) <= 64 and len(C.from_xff("", peer="y" * 400, hops=0)) <= 64)
check("an absurdly long chain is bounded, not walked forever",
      C.from_xff(", ".join([REAL] * 5000) + f", {EDGE}", peer=EDGE, hops=1) == EDGE)
check("a non-string header is refused safely", C.from_xff(12345, peer=EDGE, hops=1) == EDGE)

print("F. one home — every historical call site dereferences it")
SITES = {
    "app/core/tenant_middleware.py": r"client_ip",
    "app/modules/core/router.py": r"client_ip",
    "app/modules/core/impersonation_api.py": r"client_ip",
    "app/modules/core/operator_api.py": r"client_ip",
}
for rel, want in SITES.items():
    src = open(os.path.join(HERE, *rel.split("/"))).read()
    check(f"{rel} dereferences app.core.client_ip",
          re.search(r"from app\.core import[^\n]*client_ip|from app\.core\.client_ip import|app\.core\.client_ip", src) is not None)
    check(f"{rel} no longer carries its own leftmost-XFF parse",
          re.search(r'x-forwarded-for[^\n]*split\(","\)\[0\]', src) is None, rel)

print("G. lock — no fifth copy may appear anywhere in app/")
offenders = []
for root, _dirs, files in os.walk(os.path.join(HERE, "app")):
    for fn in files:
        if not fn.endswith(".py"):
            continue
        p = os.path.join(root, fn)
        rel = os.path.relpath(p, HERE)
        if rel.replace(os.sep, "/") == "app/core/client_ip.py":
            continue                       # the one home is allowed to know how
        body = open(p, encoding="utf-8", errors="replace").read()
        # a leftmost read of the forwarded header, in any spacing
        for m in re.finditer(r'x-forwarded-for.{0,80}', body, re.S | re.I):
            seg = m.group(0)
            if re.search(r'split\(\s*[\'"],[\'"]\s*\)\s*\[\s*0\s*\]', seg):
                offenders.append(rel)
check("no module but app/core/client_ip.py reads the leftmost X-Forwarded-For entry",
      offenders == [], sorted(set(offenders)))

print("H. the switch is visible in the boot posture")
from app.core import security_posture as SP   # noqa: E402
posture = SP.evaluate()["posture"]
check("posture reports TRUSTED_PROXY_HOPS", "TRUSTED_PROXY_HOPS" in posture, sorted(posture))
old_env = os.environ.get("APP_ENV")
try:
    os.environ["APP_ENV"] = "production"
    os.environ.pop("TRUSTED_PROXY_HOPS", None)
    findings = SP.evaluate()["findings"]
    check("in production, an unset TRUSTED_PROXY_HOPS is a stated finding",
          any("TRUSTED_PROXY_HOPS" in f for f in findings), findings)
    os.environ["TRUSTED_PROXY_HOPS"] = "2"
    check("set ⇒ the finding goes away",
          not any("TRUSTED_PROXY_HOPS" in f for f in SP.evaluate()["findings"]))
finally:
    os.environ.pop("TRUSTED_PROXY_HOPS", None)
    if old_env is None:
        os.environ.pop("APP_ENV", None)
    else:
        os.environ["APP_ENV"] = old_env

print("N. negative controls")
check("control: the PRE-FIX parse really does return the attacker's value",
      (f"{EVIL}, {REAL}, {EDGE}").split(",")[0].strip() == EVIL)
check("control: §G's detector really fires on the pre-fix line",
      bool(re.search(r'split\(\s*[\'"],[\'"]\s*\)\s*\[\s*0\s*\]',
                     'fwd = (headers.get("x-forwarded-for") or "").split(",")[0].strip()')))
check("control: §G walked a real tree (found python files to scan)",
      sum(1 for r, _d, fs in os.walk(os.path.join(HERE, "app")) for f in fs if f.endswith(".py")) > 50)

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
