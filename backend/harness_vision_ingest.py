"""Proof harness for the edge-analyzer ingest boundary (mod-vision, migration 900).

Run: python3 backend/harness_vision_ingest.py   (pure functions — no network, no DB)

The analyzer runs on hardware this platform does not control and posts numbers that end up in a
coaching conversation. Everything that decides what it may send is proven here:

  1. HMAC over `timestamp.body` — a tampered body, a wrong secret, a missing header and a REPLAYED
     old request all fail; only the exact request signed with the exact secret passes.
  2. A device this tenant has not registered is rejected — cross-tenant writes are impossible by
     construction, not by a filter someone has to remember.
  3. An agent pinned to a store may not write for another store.
  4. Traffic is refused from a camera the operator did not mark as the entrance.
  5. THE CUSTOMER'S SPEECH IS NEVER STORED — a segment whose speaker is not the employee is dropped.
  6. A transcript for an employee with no consent / a declined / a withdrawn one is dropped, and
     dropped again when the deployment audio kill switch is off.
  7. Redaction happens BEFORE the row is built — the unredacted string never leaves this function.
  8. Every rejection is COUNTED and returned, so an operator sees why nothing is being recorded.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.modules.vision import config as C   # noqa: E402
from app.modules.vision import ingest as I   # noqa: E402

PASS, FAIL = [], []


def check(label, cond):
    (PASS if cond else FAIL).append(label)
    print(("  ok   " if cond else "  FAIL ") + label)


SECRET = "s3cr3t-analyzer-key"
BODY = b'{"events":[{"kind":"traffic"}]}'

print("\n(1) HMAC authentication")
ts = str(int(time.time()))
sig = I.sign(SECRET, ts, BODY)
ok, why = I.verify(SECRET, ts, BODY, sig)
check("the exact request with the exact secret passes", ok and why == "ok")
ok, why = I.verify(SECRET, ts, b'{"events":[{"kind":"traffic"},{"kind":"evil"}]}', sig)
check("a TAMPERED body fails (the signature covers the body)", not ok and why == "bad_signature")
ok, why = I.verify("wrong-secret", ts, BODY, sig)
check("a wrong secret fails", not ok and why == "bad_signature")
ok, why = I.verify(SECRET, ts, BODY, "")
check("a missing signature fails", not ok and why == "missing_signature")
ok, why = I.verify(SECRET, "", BODY, sig)
check("a missing timestamp fails", not ok and why == "missing_signature")
ok, why = I.verify(SECRET, "not-a-number", BODY, sig)
check("a non-numeric timestamp fails", not ok and why == "bad_timestamp")

old = str(int(time.time()) - 3600)
ok, why = I.verify(SECRET, old, BODY, I.sign(SECRET, old, BODY))
check("a REPLAYED request signed an hour ago fails (bounded skew)",
      not ok and why == "stale_timestamp")
future = str(int(time.time()) + 3600)
ok, why = I.verify(SECRET, future, BODY, I.sign(SECRET, future, BODY))
check("a far-future timestamp fails too (skew is bounded both ways)",
      not ok and why == "stale_timestamp")
near = str(int(time.time()) - 120)
ok, _ = I.verify(SECRET, near, BODY, I.sign(SECRET, near, BODY))
check("a store box 2 minutes off NTP still works", ok)
check("the signature is deterministic for the same inputs",
      I.sign(SECRET, ts, BODY) == I.sign(SECRET, ts, BODY))
check("a different timestamp yields a different signature",
      I.sign(SECRET, ts, BODY) != I.sign(SECRET, str(int(ts) + 1), BODY))

# ── batch normalization ──────────────────────────────────────────────────────────────────────────
ORG = "org-1"
ENTRANCE = {"id": "cam-1", "device_name": "enterprises/p/devices/front", "store_code": "S1",
            "enabled": True, "analytics_enabled": True, "is_entrance": True,
            "audio_enabled": True, "supports_audio": True}
INTERIOR = {"id": "cam-2", "device_name": "enterprises/p/devices/counter", "store_code": "S1",
            "enabled": True, "analytics_enabled": True, "is_entrance": False,
            "audio_enabled": True, "supports_audio": True}
CAMS = {ENTRANCE["device_name"]: ENTRANCE, INTERIOR["device_name"]: INTERIOR}
AGENT = {"agent_key": "va_x", "store_code": "S1"}

FULL_ON = {"enabled": True, "live_view_enabled": True, "traffic_enabled": True,
           "heatmap_enabled": True, "audio_analytics_enabled": True,
           "behavior_scoring_enabled": True, "audio_consent_mode": "required"}


def batch(events, cfg=None, consents=None, agent=None, kill=False):
    C.AUDIO_GLOBALLY_DISABLED = kill
    return I.normalize_batch({"events": events}, CAMS, cfg or dict(FULL_ON),
                             consents or {}, ORG, agent or AGENT)


T_IN = {"kind": "traffic", "device_name": ENTRANCE["device_name"], "direction": "in",
        "occurred_at": "2026-08-19T14:02:00+00:00", "local_date": "2026-08-19", "local_hour": 14,
        "track_key": "t1", "confidence": 0.91}

print("\n(2) A device this tenant has not registered is rejected")
r = batch([{**T_IN, "device_name": "enterprises/p/devices/somebody-elses"}])
check("nothing accepted", r["accepted"] == 0)
check("counted as unknown_camera", r["rejected"] == {"unknown_camera": 1})

print("\n(3) A store-pinned agent cannot write for another store")
other = {ENTRANCE["device_name"]: {**ENTRANCE, "store_code": "S2"}}
C.AUDIO_GLOBALLY_DISABLED = False
r = I.normalize_batch({"events": [T_IN]}, other, dict(FULL_ON), {}, ORG, AGENT)
check("rejected as agent_store_mismatch", r["rejected"] == {"agent_store_mismatch": 1})
r = I.normalize_batch({"events": [T_IN]}, other, dict(FULL_ON), {}, ORG, {"agent_key": "va_x"})
check("an UNPINNED agent may speak for the camera's own store", r["accepted"] == 1)

print("\n(4) Traffic only from a camera marked as the entrance")
r = batch([T_IN])
check("entrance camera accepted", r["accepted"] == 1 and len(r["traffic"]) == 1)
check("the store comes from the CAMERA, never from the payload", r["traffic"][0]["store_code"] == "S1")
check("org_id is stamped server-side", r["traffic"][0]["org_id"] == ORG)
r = batch([{**T_IN, "device_name": INTERIOR["device_name"]}])
check("interior camera refused for traffic", r["rejected"] == {"traffic_not_enabled": 1})
r = batch([{**T_IN, "direction": "sideways"}])
check("a nonsense direction is refused", r["rejected"] == {"bad_direction": 1})
r = batch([T_IN], cfg={**FULL_ON, "traffic_enabled": False})
check("traffic switched off at the tenant refuses it", r["rejected"] == {"traffic_not_enabled": 1})

print("\n(5) Presence samples")
P = {"kind": "presence", "device_name": INTERIOR["device_name"],
     "sampled_at": "2026-08-19T14:02:00+00:00", "local_date": "2026-08-19", "local_hour": 14,
     "cells": [{"x": 3, "y": 4, "occupancy": 12.0}, {"x": 5, "y": 6, "occupancy": 3.0}]}
r = batch([P])
check("one event fans out to one row per cell", len(r["presence"]) == 2)
check("occupancy carried", r["presence"][0]["occupancy"] == 12.0)
r = batch([{**P, "cells": [{"x": "bad", "y": 4}]}])
check("a malformed cell is counted, not crashed on", r["rejected"] == {"bad_cell": 1})
r = batch([P], cfg={**FULL_ON, "heatmap_enabled": False})
check("heat map switched off refuses presence", r["rejected"] == {"heatmap_not_enabled": 1})

print("\n(6) THE CUSTOMER'S SPEECH IS NEVER STORED")
EMP = "emp-1"
SIGNED = {EMP: {"status": "signed"}}
TR = {"kind": "transcript", "device_name": INTERIOR["device_name"], "employee_id": EMP,
      "speaker": "employee", "text": "Welcome in! How can I help?",
      "started_at": "2026-08-19T14:02:00+00:00", "local_date": "2026-08-19", "local_hour": 14,
      "duration_s": 3.2, "elapsed_s": 8}
r = batch([TR], consents=SIGNED)
check("a consenting employee's speech is accepted", len(r["transcripts"]) == 1)
r = batch([{**TR, "speaker": "customer"}], consents=SIGNED)
check("speaker='customer' is DROPPED", not r["transcripts"] and r["rejected"] == {"not_employee_speech": 1})
r = batch([{**TR, "speaker": "other"}], consents=SIGNED)
check("speaker='other' is DROPPED too", r["rejected"] == {"not_employee_speech": 1})

print("\n(7) Consent gates the audio path")
r = batch([TR], consents={})
check("no consent record -> dropped", r["rejected"] == {"consent_missing": 1})
r = batch([TR], consents={EMP: {"status": "declined"}})
check("declined -> dropped", r["rejected"] == {"consent_declined": 1})
r = batch([TR], consents={EMP: {"status": "withdrawn"}})
check("withdrawn -> dropped", r["rejected"] == {"consent_withdrawn": 1})
r = batch([TR], consents=SIGNED, kill=True)
check("the deployment kill switch drops it even with a signed consent",
      r["rejected"] == {"audio_kill_switch": 1})
r = batch([TR], consents=SIGNED, cfg={**FULL_ON, "audio_analytics_enabled": False})
check("tenant audio switch off -> dropped", r["rejected"] == {"audio_not_enabled": 1})
r = batch([{**TR, "device_name": ENTRANCE["device_name"]}],
          consents=SIGNED, cfg={**FULL_ON})
check("a camera with audio ON is fine", len(r["transcripts"]) == 1)
mute = {ENTRANCE["device_name"]: {**ENTRANCE, "audio_enabled": False}}
C.AUDIO_GLOBALLY_DISABLED = False
r = I.normalize_batch({"events": [{**TR, "device_name": ENTRANCE["device_name"]}]},
                      mute, dict(FULL_ON), SIGNED, ORG, AGENT)
check("a camera with audio OFF drops the segment", r["rejected"] == {"audio_not_enabled": 1})
r = batch([{k: v for k, v in TR.items() if k != "employee_id"}], consents=SIGNED)
check("a segment with no employee attached is dropped", r["rejected"] == {"no_employee": 1})

print("\n(8) Redaction happens before the row is built")
r = batch([{**TR, "text": "call 415-555-0132 or sanjot@example.com about card 4111 1111 1111 1111"}],
          consents=SIGNED)
row = r["transcripts"][0]
check("the stored text has no phone number", "415-555-0132" not in row["text"])
check("the stored text has no email", "example.com" not in row["text"])
check("the stored text has no card number", "4111" not in row["text"])
check("the redaction count is recorded", row["redactions"] == 3)
check("speaker is forced to 'employee' on the stored row", row["speaker"] == "employee")
check("elapsed_s is carried into signals (the greeting window depends on it)",
      row["signals"]["elapsed_s"] == 8)
r = batch([{**TR, "text": "   "}], consents=SIGNED)
check("an empty segment is dropped rather than stored blank", r["rejected"] == {"empty_text": 1})

print("\n(9) Every rejection is counted and returned")
r = batch([T_IN,
           {**T_IN, "device_name": "unknown"},
           {**TR, "speaker": "customer"},
           {"kind": "nonsense", "device_name": INTERIOR["device_name"]},
           "not-a-dict"], consents=SIGNED)
check("the good event still lands", r["accepted"] == 1)
check("all four rejects are tallied by reason",
      r["rejected"] == {"unknown_camera": 1, "not_employee_speech": 1, "unknown_kind": 1,
                        "malformed": 1})
big = [T_IN] * (I.MAX_EVENTS_PER_BATCH + 10)
r = batch(big)
check("an oversized batch is truncated and SAYS SO, not silently dropped",
      r["rejected"].get("batch_truncated") == 1 and r["accepted"] == I.MAX_EVENTS_PER_BATCH)

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
for f in FAIL:
    print("  FAILED: " + f)
sys.exit(1 if FAIL else 0)
