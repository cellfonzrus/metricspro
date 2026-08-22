"""Proves the Google-event push path offline — no network, no database, no Pub/Sub.

WHY THIS EXISTS. /api/v1/vision/google/events is PUBLIC: Google Pub/Sub carries no session of ours,
so the parsing below is reachable by anyone who finds the URL. Two properties therefore have to hold
and are asserted here rather than assumed:

  1. NOTHING in the request body can select a tenant. The body names a DEVICE; the org comes from
     our own vision_camera row. A payload-supplied org would be a cross-tenant write primitive.
  2. Malformed input NEVER raises. An exception on a public endpoint is a 500, and Pub/Sub retries
     a 500 forever — a single bad message would become a permanent redelivery loop.
"""
import base64
import json

from app.modules.vision import google_sdm as G
from app.core.tenant_middleware import _public_method_ok, _is_public as _is_public_path

PASS, FAIL = [], []


def check(name, ok):
    (PASS if ok else FAIL).append(name)
    print(f"  {'ok  ' if ok else 'FAIL'} {name}")


def push(payload):
    return {"message": {"data": base64.b64encode(json.dumps(payload).encode()).decode()},
            "subscription": "projects/p/subscriptions/s"}


PERSON = "sdm.devices.events.CameraPerson.Person"
MOTION = "sdm.devices.events.CameraMotion.Motion"
DEV = "enterprises/proj-1/devices/dev-1"


def event(**kw):
    base = {"eventId": "ev-1", "timestamp": "2026-08-21T14:02:00Z",
            "resourceUpdate": {"name": DEV, "events": {PERSON: {"eventId": "x"}}}}
    base.update(kw)
    return base


print("\n(1) A real push decodes")
ev = G.parse_event(G.decode_push(push(event())))
check("device name survives", ev.get("device_name") == DEV)
check("event id survives", ev.get("event_id") == "ev-1")
check("google's own timestamp is kept, not our receive time",
      ev.get("occurred_at") == "2026-08-21T14:02:00Z")
check("a person event is recognised", ev.get("kinds") == ["person"])

print("\n(2) One message, several events")
both = G.parse_event(G.decode_push(push(event(
    resourceUpdate={"name": DEV, "events": {PERSON: {}, MOTION: {}}}))))
check("both kinds are returned", both.get("kinds") == ["motion", "person"])
check("kinds are sorted, so the derived ids are stable across redelivery",
      both.get("kinds") == sorted(both.get("kinds")))

print("\n(3) Not every message is a camera event")
check("a trait update stores nothing", G.parse_event(G.decode_push(push(event(
    resourceUpdate={"name": DEV, "events": {"sdm.devices.traits.Info": {}}})))) == {})
check("an unknown event name is ignored, not stored under a guess",
      G.parse_event(G.decode_push(push(event(
          resourceUpdate={"name": DEV, "events": {"sdm.devices.events.Made.Up": {}}})))) == {})

print("\n(4) Malformed input returns {} and NEVER raises")
for name, bad in [
    ("not a dict", "hello"), ("empty dict", {}), ("None", None),
    ("no message key", {"subscription": "s"}),
    ("message not a dict", {"message": "x"}),
    ("no data", {"message": {}}),
    ("data not a string", {"message": {"data": 123}}),
    ("data not base64", {"message": {"data": "!!!not base64!!!"}}),
    ("base64 of non-JSON", {"message": {"data": base64.b64encode(b"not json").decode()}}),
    ("base64 of a JSON list", {"message": {"data": base64.b64encode(b"[1,2]").decode()}}),
]:
    try:
        check(f"decode_push: {name}", G.decode_push(bad) == {})
    except Exception as e:
        check(f"decode_push: {name} (RAISED {type(e).__name__})", False)

for name, bad in [
    ("not a dict", "hello"), ("None", None), ("empty", {}),
    ("no resourceUpdate", {"eventId": "e", "timestamp": "t"}),
    ("resourceUpdate not a dict", {"resourceUpdate": "x"}),
    ("events not a dict", {"resourceUpdate": {"name": DEV, "events": "x"}}),
    ("missing eventId", {"timestamp": "t", "resourceUpdate": {"name": DEV, "events": {PERSON: {}}}}),
    ("missing timestamp", {"eventId": "e", "resourceUpdate": {"name": DEV, "events": {PERSON: {}}}}),
]:
    try:
        check(f"parse_event: {name}", G.parse_event(bad) == {})
    except Exception as e:
        check(f"parse_event: {name} (RAISED {type(e).__name__})", False)

print("\n(5) The device name is validated, not trusted")
# It is used for a lookup, so a malformed one must never reach the query at all.
for bad in ["", "../../etc/passwd", "enterprises/p/devices", "devices/d", DEV + "/extra",
            "enterprises//devices/d", "http://evil/enterprises/p/devices/d"]:
    check(f"rejected: {bad!r}", G.parse_event(event(
        resourceUpdate={"name": bad, "events": {PERSON: {}}})) == {})
check("a well-formed name is accepted", G.parse_event(event()).get("device_name") == DEV)

print("\n(6) NOTHING in the body can choose a tenant")
# THE property this endpoint lives or dies on. The parser's whole output is asserted, so a field
# that could carry tenancy cannot be added later without failing here.
hostile = G.parse_event(G.decode_push(push(event(
    org_id="11111111-1111-1111-1111-111111111111",
    orgId="attacker", store_code="VICTIM-01", userId="u"))))
check("parse_event returns ONLY device/event/time/kinds",
      set(hostile) == {"device_name", "event_id", "occurred_at", "kinds"})
check("no org_id leaks through", "org_id" not in hostile and "orgId" not in hostile)
check("no store_code leaks through", "store_code" not in hostile)

print("\n(7) An oversized event id cannot blow up the column")
check("event id is bounded", len(G.parse_event(event(eventId="x" * 5000))["event_id"]) <= 200)

print("\n(8) The route is public for POST ONLY")
P = "/api/v1/vision/google/events"
check("the path is on the public allowlist", _is_public_path(P))
check("POST is public", _public_method_ok(P, "POST"))
for m in ("GET", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"):
    check(f"{m} is NOT public", not _public_method_ok(P, m))
# Boundary-matched, so a future sibling path is not public by inheritance.
check("a sibling path is not public by inheritance",
      not _is_public_path("/api/v1/vision/google/events/secret"))
check("the parent path is not public", not _is_public_path("/api/v1/vision/google"))

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
for f in FAIL:
    print("  FAILED: " + f)
raise SystemExit(1 if FAIL else 0)
