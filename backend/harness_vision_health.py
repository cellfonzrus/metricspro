#!/usr/bin/env python3
"""PROOF — app/modules/vision/health.py, the daily camera check.

WHAT IS ACTUALLY AT RISK HERE. This module runs unattended and decides whether to touch somebody's
Google account. Two failure shapes matter more than the rest:

  * calling a dead grant "retryable", which turns a weekly outage into a loop of refresh attempts
    against a user's Google account and never tells anybody; and

  * calling a transient 503 "revoked", which wakes a human at 3am to re-authorise something that
    would have healed by itself.

Those are the same branch read two ways, so most of section B exists to pin it from both sides.

Run:  cd backend && python3 harness_vision_health.py
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from app.modules.vision import health as H  # noqa: E402

_pass = _fail = 0


def check(name, cond, detail=""):
    global _pass, _fail
    if cond:
        _pass += 1
        print("  ok   %s" % name)
    else:
        _fail += 1
        print("  FAIL %s   %s" % (name, detail))


NOW = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)


def iso(dt):
    return dt.isoformat()


def snap(**kw):
    """A healthy estate, overridden per test. Healthy is the baseline on purpose: every finding
    below is then caused by exactly the one thing the test changed."""
    base = {
        "config": {"enabled": True},
        "google": {"linked": True, "status": "ok", "last_error": None, "last_error_at": None,
                   "token_issued_at": iso(NOW - timedelta(days=30)), "last_ok_at": iso(NOW)},
        "events": {"available": True, "last_7d": 400, "last_event_at": iso(NOW - timedelta(minutes=5))},
        "cameras": {"total": 21, "enabled": 21, "unassigned": 0, "entrances": 21,
                    "entrances_with_line": 21},
        "edge_agents": {"total": 1, "online": 1, "last_seen_at": iso(NOW - timedelta(minutes=1))},
        "consent": {},
    }
    for k, v in kw.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            base[k] = {**base[k], **v}
        else:
            base[k] = v
    return base


def codes(s):
    return [f["code"] for f in H.assess(s, now=NOW)]


print("=" * 78)
print("A. A healthy estate produces NOTHING. A monitor that cries daily is turned off.")
print("=" * 78)
check("a fully working estate yields no findings", H.assess(snap(), now=NOW) == [])
check("...and no severity badge", H.worst(H.assess(snap(), now=NOW)) is None)
check("...and nothing to auto-fix", H.auto_actions(H.assess(snap(), now=NOW)) == [])
check("the summary says so plainly", "All camera checks passed." == H.summarise([]))

print()
print("=" * 78)
print("B. THE GRANT — retryable vs dead, read from both sides")
print("=" * 78)
# The weekly signature: minted, worked, aged out inside 8 days.
weekly = snap(google={"status": "error", "last_error": "invalid_grant: Token has been expired",
                      "token_issued_at": iso(NOW - timedelta(days=7)),
                      "last_error_at": iso(NOW - timedelta(hours=1))})
f = H.assess(weekly, now=NOW)
check("B1 a grant that died inside 7 days is diagnosed as the Testing-mode expiry",
      "google_token_expired_testing" in [x["code"] for x in f])
check("B2 ...it is DOWN, not a warning", f[0]["severity"] == H.DOWN)
check("B3 ...it is NEVER auto-retried — no amount of retrying mints a revoked grant",
      all(x["auto"] is None for x in f if x["code"] == "google_token_expired_testing"))
check("B4 ...the remedy names PUBLISHING the consent screen, not just reconnecting",
      "PUBLISH" in [x for x in f if x["code"] == "google_token_expired_testing"][0]["remedy"])
check("B5 ...and warns it will recur weekly until that is done",
      "seven days" in [x for x in f if x["code"] == "google_token_expired_testing"][0]["remedy"])

# Same error, but the grant was months old: that is a real revocation, not the weekly expiry.
old = snap(google={"status": "error", "last_error": "invalid_grant",
                   "token_issued_at": iso(NOW - timedelta(days=200)),
                   "last_error_at": iso(NOW - timedelta(hours=1))})
check("B6 the SAME error on a 200-day-old grant is a revocation, not the Testing expiry",
      "google_grant_revoked" in codes(old) and "google_token_expired_testing" not in codes(old))
check("B7 ...also never auto-retried",
      H.auto_actions(H.assess(old, now=NOW)) == [])

# A transient failure IS a retry — the case the monitor is meant to fix silently.
tmp = snap(google={"status": "error", "last_error": "HTTP 503 backend error",
                   "last_error_at": iso(NOW - timedelta(minutes=5))})
ft = H.assess(tmp, now=NOW)
check("B8 a 503 is a RETRY, not a revocation", [x["code"] for x in ft] == ["google_transient_error"])
check("B9 ...and it is the auto-fix the monitor performs without telling anybody",
      H.auto_actions(ft) == ["retry_token"])
check("B10 ...rated DEGRADED, not DOWN — it usually heals itself", ft[0]["severity"] == H.DEGRADED)
check("B11 the original Google text is carried into the detail, not swallowed",
      "503" in ft[0]["detail"])

check("B12 no credential at all is a SETUP finding that stops further judgement", (lambda c: (
      c == ["google_not_linked"]))(codes(snap(google={"linked": False}))),
      "nothing below a missing grant can be assessed")

# A grant with no token_issued_at recorded (an older row) must not be guessed either way.
noissue = snap(google={"status": "error", "last_error": "invalid_grant",
                       "token_issued_at": None, "last_error_at": iso(NOW)})
check("B13 a missing token_issued_at falls back to 'revoked' rather than inventing the weekly story",
      "google_grant_revoked" in codes(noissue))

print()
print("=" * 78)
print("C. EVENTS — a push subscription that breaks never says so")
print("=" * 78)
stopped = snap(events={"last_event_at": iso(NOW - timedelta(hours=9))})
check("C1 nine hours of silence on a live estate is DOWN", (lambda f: (
      "events_stopped" in [x["code"] for x in f]
      and [x for x in f if x["code"] == "events_stopped"][0]["severity"] == H.DOWN))(
      H.assess(stopped, now=NOW)))
check("C2 ...and names the frozen report rather than just the plumbing",
      "Busy Hours" in [x for x in H.assess(stopped, now=NOW) if x["code"] == "events_stopped"][0]["detail"])
check("C3 a normal overnight gap is NOT an alert",
      "events_stopped" not in codes(snap(events={"last_event_at": iso(NOW - timedelta(hours=5))})))
check("C4 never-arrived is SETUP, not an outage — it never worked to begin with",
      "events_never_arrived" in codes(snap(events={"last_7d": 0, "last_event_at": None})))
check("C5 ...and points at the Pub/Sub publisher role, the step everyone misses",
      "sdm-publisher" in [x for x in H.assess(snap(events={"last_7d": 0, "last_event_at": None}),
                                              now=NOW) if x["code"] == "events_never_arrived"][0]["remedy"])
# THE ONE THAT MATTERS MOST: a dead grant explains the silence. Reporting both is noise that sends
# somebody to chase a Pub/Sub subscription that was never the problem.
both = snap(google={"status": "error", "last_error": "invalid_grant",
                    "token_issued_at": iso(NOW - timedelta(days=7)),
                    "last_error_at": iso(NOW - timedelta(hours=2))},
            events={"last_event_at": iso(NOW - timedelta(hours=20))})
check("C6 a dead grant SUPPRESSES the event-silence finding — one cause, one alert",
      "events_stopped" not in codes(both) and "google_token_expired_testing" in codes(both))

print()
print("=" * 78)
print("D. THE SILENT ZERO — an entrance with no line reports zero, which looks like a quiet day")
print("=" * 78)
noline = snap(cameras={"entrances": 21, "entrances_with_line": 0})
check("D1 entrances with no counting line are reported", "entrances_without_line" in codes(noline))
check("D2 ...counted, not just flagged", "21 entrance" in [
      x for x in H.assess(noline, now=NOW) if x["code"] == "entrances_without_line"][0]["title"])
check("D3 ...and the detail says plainly that zero looks like a quiet day",
      "quiet day" in [x for x in H.assess(noline, now=NOW)
                      if x["code"] == "entrances_without_line"][0]["detail"])
check("D4 ...pointing at the page that fixes it",
      [x for x in H.assess(noline, now=NOW)
       if x["code"] == "entrances_without_line"][0]["where"] == "/vision/lines")
check("D5 partially drawn is still reported, with the REMAINING count", (lambda f: (
      "3 entrances" in f[0]["title"]))([x for x in H.assess(
          snap(cameras={"entrances": 21, "entrances_with_line": 18}), now=NOW)
          if x["code"] == "entrances_without_line"]))
check("D6 all lines drawn -> silence", "entrances_without_line" not in codes(snap()))
check("D7 an older status payload with no line count does not invent a finding",
      "entrances_without_line" not in codes(snap(cameras={"entrances": 21,
                                                          "entrances_with_line": None})))
check("D8 one unassigned camera is DEGRADED and says its events are excluded", (lambda f: (
      f and f[0]["severity"] == H.DEGRADED and "excluded" in f[0]["detail"]))(
      [x for x in H.assess(snap(cameras={"unassigned": 2}), now=NOW)
       if x["code"] == "cameras_unassigned"]))

print()
print("=" * 78)
print("E. THE STORE PC — what breaks, and what keeps working while it is down")
print("=" * 78)
off = snap(edge_agents={"total": 1, "online": 0,
                        "last_seen_at": iso(NOW - timedelta(hours=14))})
fo = [x for x in H.assess(off, now=NOW) if x["code"] == "edge_agent_offline"]
check("E1 an offline store PC is DOWN", fo and fo[0]["severity"] == H.DOWN)
check("E2 ...with how long it has been gone", "14 hours" in fo[0]["detail"])
check("E3 ...saying what still works, so nobody assumes everything is dead",
      "Busy Hours keeps working" in fo[0]["detail"])
check("E4 ...and admits it cannot be fixed remotely",
      "cannot be fixed remotely" in fo[0]["remedy"])
check("E5 never auto-'fixed' — there is no remote action that powers on a PC",
      all(x["auto"] is None for x in fo))
check("E6 no agent enrolled at all is SETUP, not an outage",
      "no_edge_agent" in codes(snap(edge_agents={"total": 0, "online": 0, "last_seen_at": None})))
check("E7 ...and is NOT raised when the module is off", (lambda c: "no_edge_agent" not in c)(
      codes(snap(config={"enabled": False}, edge_agents={"total": 0, "online": 0,
                                                         "last_seen_at": None}))))

print()
print("=" * 78)
print("F. ORDERING, DEDUPE AND THE ALERT — what a person actually receives")
print("=" * 78)
messy = snap(google={"status": "error", "last_error": "HTTP 500",
                     "last_error_at": iso(NOW)},
             cameras={"unassigned": 1, "entrances": 21, "entrances_with_line": 2},
             edge_agents={"total": 1, "online": 0, "last_seen_at": iso(NOW - timedelta(hours=3))})
fm = H.assess(messy, now=NOW)
check("F1 the worst thing is first", fm[0]["severity"] == H.DOWN)
check("F2 severities are grouped, worst to least",
      [H._ORDER[x["severity"]] for x in fm] == sorted(H._ORDER[x["severity"]] for x in fm))
check("F3 the badge reports the worst", H.worst(fm) == H.DOWN)
# De-duplication, driven directly so the check cannot go vacuous when the finding set changes:
# two findings asking for the same action must produce ONE call. Re-listing devices twice in a tick
# spends Google's command budget for nothing, and that budget is already 63% consumed here.
twice = [{"code": "a", "severity": H.SETUP, "auto": "resync_devices"},
         {"code": "b", "severity": H.SETUP, "auto": "resync_devices"},
         {"code": "c", "severity": H.SETUP, "auto": "retry_token"},
         {"code": "d", "severity": H.SETUP, "auto": None}]
check("F4a two findings asking the same fix produce ONE call",
      H.auto_actions(twice) == ["resync_devices", "retry_token"])
check("F4b ...and a finding with no fix contributes nothing",
      "None" not in str(H.auto_actions(twice)))
# EVENTS STOPPED HAS NO AUTO-FIX ON PURPOSE: the Pub/Sub subscription lives in the customer's own
# Google Cloud. A fix that cannot address the cause would report a repair that never happened.
stopped_f = [x for x in H.assess(snap(events={"last_event_at": iso(NOW - timedelta(hours=9))}),
                                 now=NOW) if x["code"] == "events_stopped"]
check("F4c a broken Pub/Sub push is escalated, never 'fixed'", stopped_f[0]["auto"] is None)
check("F4c auto-fixes are de-duplicated in general",
      len(H.auto_actions(fm)) == len(set(H.auto_actions(fm))))
body = H.summarise(fm)
check("F5 every finding appears in the alert", all(x["title"] in body for x in fm))
check("F6 every block ends with an action, not just a diagnosis",
      body.count("What to do:") == len(fm))
check("F7 the alert is tagged so severity reads at a glance", "[DOWN]" in body)

d = "2026-10-02"
check("F8 the same problems on the same day dedupe to one alert",
      H.alert_ref("org", fm, d) == H.alert_ref("org", list(reversed(fm)), d))
check("F9 a NEW problem appearing later the same day does re-alert",
      H.alert_ref("org", fm, d) != H.alert_ref("org", fm[:-1], d))
check("F10 tomorrow is a new alert", H.alert_ref("org", fm, d) != H.alert_ref("org", fm, "2026-10-03"))
check("F11 a count changing does NOT re-alert all day — keyed on codes, not text",
      H.alert_ref("org", H.assess(snap(cameras={"unassigned": 3}), now=NOW), d)
      == H.alert_ref("org", H.assess(snap(cameras={"unassigned": 9}), now=NOW), d))

print()
print("=" * 78)
print("G. IT RUNS UNATTENDED, so malformed input must never throw")
print("=" * 78)
for bad in (None, {}, {"google": None}, {"google": {"linked": True}, "events": None},
            {"google": {"linked": True, "token_issued_at": "not-a-date",
                        "status": "error", "last_error": "invalid_grant"}},
            {"google": {"linked": True}, "cameras": {"total": "x", "entrances": None}},
            {"google": {"linked": True}, "edge_agents": {"total": 2, "online": 0,
                                                         "last_seen_at": "garbage"}}):
    try:
        H.assess(bad, now=NOW)
        ok = True
    except Exception as e:                                    # noqa: BLE001
        ok = False
        print("      raised:", type(e).__name__, e)
    check("G survives %s" % (str(bad)[:58]), ok)

print()
print("=" * 78)
print("%d passed, %d failed" % (_pass, _fail))
print("=" * 78)
sys.exit(1 if _fail else 0)
