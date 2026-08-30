"""HARNESS — the out-of-band (2FA) code reader that makes an UNATTENDED portal login possible.

The portal drivers stop at the code-entry screen by design: `begin_login()` reaches it, a HUMAN reads the
code out of their mail, and `complete_2fa(code)` finishes. So a scheduled pull can never complete without
a person — even after the WAF is cleared and the selectors are calibrated. `oob_code` closes that gap by
reading the code the portal just emailed, out of the mailbox the daily sweep already uses.

Everything proven here is PURE — no mailbox, no network.

  python3 backend/harness_oob_code.py
"""
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app.modules.commcalc import oob_code as oc  # noqa: E402

PASS = 0
FAIL = 0
NOW = datetime(2026, 8, 30, 22, 0, 0, tzinfo=timezone.utc)


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f"FAIL  {name}   {extra}")


def msg(body, mins_ago=1, frm="no-reply@vidapaycrm.com", subj="Your verification code"):
    return {"date": NOW - timedelta(minutes=mins_ago), "body": body, "from": frm, "subject": subj}


print("── A. extraction from realistic portal mail ──")
check("plain 'Your verification code is 123456'",
      oc.extract_code("Your verification code is 123456. It expires in 10 minutes.") == "123456")
check("code BEFORE its label ('482913 is your security code')",
      oc.extract_code("482913 is your security code") == "482913")
check("HTML mail — tags become spaces, not concatenated digits",
      oc.extract_code("<p>Your code is <b>246810</b></p>") == "246810")
check("split tags do NOT fuse into a fake code",
      oc.extract_code("<td>12</td><td>34</td>") is None,
      oc.extract_code("<td>12</td><td>34</td>"))
check("script/style contents ignored",
      oc.extract_code("<script>var x=999999</script>Your code: 314159") == "314159")
check("entities decoded", oc.extract_code("code&nbsp;is&nbsp;778899") == "778899")

print("── B. the false positives that would submit a WRONG code ──")
check("a phone number is not a code",
      oc.extract_code("Questions? Call 1-800-555-0199 about your account.") is None,
      oc.extract_code("Questions? Call 1-800-555-0199 about your account."))
check("a money amount is not a code",
      oc.extract_code("Your invoice total is 12,345.67 due now.") is None)
check("a bare year is not a code", oc.extract_code("Copyright 2026 VidaPay") is None)
check("BUT a year-shaped number next to 'code' still reads",
      oc.extract_code("Your code is 2026") == "2026")
check("a long order id is not sliced into a code",
      oc.extract_code("Order 9876543210987 shipped") is None,
      oc.extract_code("Order 9876543210987 shipped"))
check("two DIFFERENT keyword-adjacent numbers → refuse rather than guess",
      oc.extract_code("code 111111 ... security code 222222") is None)
check("two bare numbers, no keyword → refuse", oc.extract_code("aaa 4321 bbb 8765") is None)
check("empty / None body → None", oc.extract_code("") is None and oc.extract_code(None) is None)

print("── C. per-login config (a format change is config, never code) ──")
check("code_regex with a capture group wins",
      oc.extract_code("Token: AB-45678 (expires)", code_regex=r"AB-(\d{5})") == "45678")
check("code_regex with no group returns the whole match",
      oc.extract_code("PIN>>7788<<", code_regex=r"\d{4}") == "7788")
check("a BAD regex fails safe instead of crashing a login",
      oc.extract_code("code 123456", code_regex=r"([unclosed") is None)
check("explicit length=4 accepts a 4-digit code",
      oc.extract_code("Your code is 4821", length=4) == "4821")
check("explicit length=4 rejects a 6-digit number",
      oc.extract_code("Your code is 482100", length=4) is None)

print("── D. FRESHNESS — a stale code is refused, never replayed (security control) ──")
fresh, stale = msg("code 111111", 1), msg("code 222222", 60)
check("the newest fresh message wins",
      oc.pick_message([stale, fresh], now=NOW, max_age_seconds=300)["body"] == "code 111111")
check("everything stale → None, even though a match exists",
      oc.pick_message([stale], now=NOW, max_age_seconds=300) is None)
check("a message with NO date is refused (unknown age never passes an age check)",
      oc.pick_message([{"date": None, "body": "code 333333"}], now=NOW) is None)
check("a future-dated message beyond skew tolerance is refused",
      oc.pick_message([msg("code 444444", -60)], now=NOW, max_age_seconds=300) is None)
check("small clock skew is tolerated",
      oc.pick_message([msg("code 555555", -1)], now=NOW, max_age_seconds=300) is not None)

print("── E. end-to-end selection over a realistic mailbox ──")
mailbox = [
    msg("Your invoice 12,345.67 is ready", 3, "billing@vidapaycrm.com", "Invoice ready"),
    msg("Your verification code is 246813", 2),
    msg("code 999999", 240),                                    # stale
    msg("Newsletter 2026 highlights", 1, "news@elsewhere.com", "News"),
]
rules = {"from_contains": "vidapaycrm.com", "subject_contains": "verification", "max_age_seconds": 600}
res = oc.code_from_messages(mailbox, rules, now=NOW)
check("picks the right message and code", res.get("code") == "246813", res)
check("the result NEVER echoes the code in diagnostics",
      "246813" not in str(res.get("reason") or "") and res["message"]["digits"] == 6, res.get("message"))
check("sender filter excludes a lookalike from another domain",
      oc.code_from_messages([msg("code 121212", 1, "no-reply@evil.example", "Your verification code")],
                            rules, now=NOW).get("code") is None)
r2 = oc.code_from_messages([msg("code 777777", 999)], rules, now=NOW)
check("all-stale gives a REASON, not a code", r2.get("code") is None and "stale" in r2["reason"], r2)
check("no match at all gives its own reason",
      "filter" in oc.code_from_messages([], rules, now=NOW)["reason"])
r3 = oc.code_from_messages([msg("nothing numeric here", 1)], rules, now=NOW)
check("matching mail with no code explains how to fix it (code_regex)",
      r3.get("code") is None and "code_regex" in r3["reason"], r3)

print("── F. no code or body ever reaches a log/diagnostic string ──")
for r in (res, r2, r3):
    blob = str(r.get("reason") or "") + str(r.get("message") or "")
    check(f"reason/message carries no digits-as-code ({str(r.get('reason'))[:28]!r}…)",
          "246813" not in blob and "777777" not in blob)

print("── G. not_before — an UNATTENDED poll never reuses a PREVIOUS run's code ──")
login_started = NOW - timedelta(minutes=2)
prior = msg("code 111111", 5)      # inside the age window, but from BEFORE this login began
during = msg("code 222222", 1)     # arrived after we started
check("a code predating the login is refused even though it is 'fresh'",
      oc.pick_message([prior], now=NOW, max_age_seconds=600, not_before=login_started) is None)
check("a code that arrived after the login is accepted",
      oc.pick_message([during], now=NOW, max_age_seconds=600,
                      not_before=login_started)["body"] == "code 222222")
check("with both present the one from THIS login wins",
      oc.pick_message([prior, during], now=NOW, max_age_seconds=600,
                      not_before=login_started)["body"] == "code 222222")
check("not_before never LOOSENS the age window (older floor cannot widen it)",
      oc.pick_message([msg("code 333333", 45)], now=NOW, max_age_seconds=300,
                      not_before=NOW - timedelta(hours=5)) is None)
r4 = oc.code_from_messages([prior], {"max_age_seconds": 600, "not_before": login_started}, now=NOW)
check("the refusal reason says the login-start floor applied",
      r4.get("code") is None and "this login started" in r4["reason"], r4)

print("── H. login_unattended orchestration (driver I/O stubbed) ──")
import types
sys.modules.setdefault("playwright", types.ModuleType("playwright"))
from app.modules.commcalc import vidapay_sweep as vp

calls = {}
def _fake_begin(url, acc, user, pw, proxy_url=None):
    calls["begin"] = True
    return {"status": "needs_2fa", "_2fa_url": "https://x/2fa"}
def _fake_complete(url, pending, code, proxy_url=None):
    calls["code"] = code
    return {"status": "authenticated", "storage_state": {"ok": True}}
vp.begin_login, vp.complete_2fa = _fake_begin, _fake_complete
oc_read = oc.read_latest_code
oc.read_latest_code = lambda cfg, rules=None, **k: {"code": "654321"}
out = vp.login_unattended("u", "a", "user", "pw", {"imap_host": "h"}, {}, timeout_seconds=5)
check("unattended login reaches authenticated without a human",
      out.get("status") == "authenticated" and calls.get("code") == "654321", out)

vp.begin_login = lambda *a, **k: {"status": "authenticated", "storage_state": {}}
check("an already-trusted device skips the 2FA wait entirely",
      vp.login_unattended("u", "a", "user", "pw", {}, {}, timeout_seconds=5)["status"] == "authenticated")

vp.begin_login = _fake_begin
oc.read_latest_code = lambda cfg, rules=None, **k: {"code": None, "reason": "no message matched"}
try:
    vp.login_unattended("u", "a", "user", "pw", {}, {}, poll_seconds=1, timeout_seconds=2)
    check("timeout raises a named, actionable error", False, "no exception")
except Exception as e:
    check("timeout raises a named, actionable error",
          "No 2FA code arrived" in str(e) and "mig 307" in str(e), str(e)[:90])
oc.read_latest_code = oc_read

print()
print(f"{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
