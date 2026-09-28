#!/usr/bin/env python3
"""PROOF — the API's CORS policy (app/core/cors_policy.py, owner 2026-09-28, index §40). DB-free, stdlib only.

What it proves, against the REAL module (loaded by path, so the app package — and fastapi — is never
imported):
  A. defaults     — CORS_ORIGINS unset ⇒ the marketing site's two origins + localhost + the app's own
                    canonical origin; NO allow-regex (the old default matched platform hostnames anyone
                    can register)
  B. env wins     — CORS_ORIGINS replaces the defaults, but the app's own canonical origin (APP_PUBLIC_URL)
                    is ALWAYS allowed — a typo cannot cut the app off from its own API
  C. no wildcard  — `*`, a path, a bare host or garbage are dropped, each with a note saying why
  D. regex        — an operator-set CORS_ORIGIN_REGEX is honoured when it only admits origins we own, and
                    refused (with a note) when it admits a canary: the old default, `.*`, an invalid one
  E. wiring       — main.py builds CORSMiddleware from cors_policy() and no longer spells the old default
                    regex or a literal origin list of its own
  N. negative controls — the canary check and the wildcard filter actually bite

Run: python3 backend/harness_cors_policy.py
"""
import importlib.util
import os
import re
import sys

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


spec = importlib.util.spec_from_file_location("cors_policy", os.path.join(HERE, "app", "core", "cors_policy.py"))
C = importlib.util.module_from_spec(spec)
spec.loader.exec_module(C)
cors_policy = C.cors_policy

CANON = "https://metricspro.tech"
OLD_DEFAULT_REGEX = r"https://metricspro[a-z0-9\-]*\.vercel\.app"

print("A. defaults")
o, rx, notes = cors_policy({}, "https://metricspro-five.vercel.app")
check("marketing apex allowed", "https://metricspro.tech" in o, o)
check("marketing www allowed", "https://www.metricspro.tech" in o, o)
check("local dev allowed", "http://localhost:3000" in o and "http://127.0.0.1:3000" in o, o)
check("the app's canonical origin (APP_PUBLIC_URL) allowed", "https://metricspro-five.vercel.app" in o, o)
check("NO allow-regex by default", rx is None, rx)
check("no notes for a clean default", notes == [], notes)
o2, _, _ = cors_policy({}, CANON + "/")
check("APP_PUBLIC_URL with a trailing slash is normalised and not duplicated", o2.count(CANON) == 1, o2)

print("B. env wins, canonical always allowed")
o, rx, notes = cors_policy({"CORS_ORIGINS": "https://www.metricspro.tech, https://partner.example "}, CANON)
check("CORS_ORIGINS replaces the defaults", "http://localhost:3000" not in o, o)
check("listed origins trimmed and kept", "https://partner.example" in o and "https://www.metricspro.tech" in o, o)
check("canonical origin present even though CORS_ORIGINS omitted it", CANON in o, o)
check("canonical origin is first (the app itself)", o[0] == CANON, o)

print("C. no wildcard")
o, rx, notes = cors_policy({"CORS_ORIGINS": "*,https://ok.example,https://x.example/path,x.example,ftp://y.example"}, CANON)
check("`*` dropped", "*" not in o, o)
check("an origin with a path dropped", "https://x.example/path" not in o, o)
check("a bare host (no scheme) dropped", "x.example" not in o, o)
check("a non-http scheme dropped", "ftp://y.example" not in o, o)
check("the good one kept", "https://ok.example" in o, o)
check("every drop is named in a note", len(notes) == 4 and all("dropped" in n for n in notes), notes)
o, _, _ = cors_policy({"CORS_ORIGINS": "http://192.168.1.20:8081"}, CANON)
check("an origin with a port is accepted (LAN dev)", "http://192.168.1.20:8081" in o, o)

print("D. allow-regex")
_, rx, notes = cors_policy({"CORS_ORIGIN_REGEX": r"https://metricspro-git-[a-z0-9-]+-cellfonzrus-team-7f3a\.vercel\.app"}, CANON)
check("a regex that admits only our own team-scoped hosts is honoured", rx is not None and notes == [], (rx, notes))
_, rx, notes = cors_policy({"CORS_ORIGIN_REGEX": OLD_DEFAULT_REGEX}, CANON)
check("the OLD default regex is refused (it admits metricspro-attacker.vercel.app)", rx is None and notes and "admits" in notes[0], (rx, notes))
_, rx, notes = cors_policy({"CORS_ORIGIN_REGEX": ".*"}, CANON)
check("`.*` is refused", rx is None and notes, (rx, notes))
_, rx, notes = cors_policy({"CORS_ORIGIN_REGEX": "https://metricspro.tech.*"}, CANON)
check("a regex admitting metricspro.tech.evil.example is refused", rx is None and notes, (rx, notes))
_, rx, notes = cors_policy({"CORS_ORIGIN_REGEX": "https://(unclosed"}, CANON)
check("an invalid regex is refused with a note", rx is None and notes and "invalid" in notes[0], (rx, notes))
_, rx, notes = cors_policy({"CORS_ORIGIN_REGEX": "   "}, CANON)
check("a blank regex is simply off", rx is None and notes == [], (rx, notes))

print("E. wiring in main.py")
main = open(os.path.join(HERE, "app", "main.py")).read()
check("main.py builds the policy from cors_policy(os.environ, APP_PUBLIC_URL)",
      re.search(r"cors_policy\(\s*os\.environ\s*,\s*_settings\.APP_PUBLIC_URL\s*\)", main) is not None)
mw = re.search(r"app\.add_middleware\(\s*CORSMiddleware,(.*?)\)\n", main, re.S)
check("CORSMiddleware reads CORS_ORIGINS / CORS_ORIGIN_REGEX from the policy",
      mw is not None and "allow_origins=CORS_ORIGINS" in mw.group(1) and "allow_origin_regex=CORS_ORIGIN_REGEX" in mw.group(1))
check("main.py no longer carries the old default regex", r"metricspro[a-z0-9\-]*\.vercel\.app" not in main)
check("main.py no longer reads CORS_ORIGINS / CORS_ORIGIN_REGEX from the env itself",
      'os.environ.get("CORS_ORIGINS"' not in main and 'os.environ.get("CORS_ORIGIN_REGEX"' not in main)
check('the CORSMiddleware call does not allow_origins=["*"]', mw is not None and 'allow_origins=["*"]' not in mw.group(1))

print("N. negative controls")
check("control: the old default regex really does match an attacker-registrable host",
      re.fullmatch(OLD_DEFAULT_REGEX, "https://metricspro-attacker.vercel.app") is not None)
check("control: the origin shape check really rejects `*`", C._ORIGIN.match("*") is None)
check("control: every canary is a string the canary check can test", all(isinstance(c, str) and c.startswith("https://") for c in C.REGEX_CANARIES))

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
