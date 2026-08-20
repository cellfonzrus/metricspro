"""Proof harness for the Google Smart Device Management client (mod-vision, migration 900).

Run: python3 backend/harness_vision_sdm.py   (no network — a scripted fake transport)

The SDM request shapes are the part of this module that cannot be checked by reading it; a wrong
command string or a dropped `params` key fails only against Google, in production, on a camera. So
the whole path runs here against a fake that ASSERTS on what it receives:

  1. The refresh-token exchange posts form-encoded credentials to Google's token endpoint.
  2. An invalid_grant is translated into "reconnect", not a retry loop.
  3. list_devices filters non-cameras and reads the live-stream protocol off the device's trait.
  4. GenerateWebRtcStream sends the viewer's offerSdp and returns the answer + mediaSessionId.
  5. GenerateRtspStream needs no offer and returns the tokenized URL + extension token.
  6. ExtendRtspStream returns a NEW extension token — the reason the caller must store what comes
     back rather than reusing the old one (the bug that breaks the SECOND extension, not the first).
  7. A WebRTC stream with no offer is refused locally instead of being bounced by Google.
  8. Expiry parsing and the extension lead time.
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.modules.vision import google_sdm as G   # noqa: E402

PASS, FAIL = [], []
SEEN = []


def check(label, cond):
    (PASS if cond else FAIL).append(label)
    print(("  ok   " if cond else "  FAIL ") + label)


CRED = {"project_id": "proj-123", "client_id": "cid", "client_secret": "csec",
        "refresh_token": "rtok"}


def transport(script):
    """A fake transport that records every call and replays a scripted (status, payload) per URL
    fragment. Anything unscripted is a hard failure — a silent 200 would hide a wrong request."""
    def _t(method, url, headers=None, json_body=None, data=None, timeout=None):
        SEEN.append({"method": method, "url": url, "headers": headers or {},
                     "json": json_body, "data": data})
        for frag, resp in script:
            if frag in url:
                return resp
        raise AssertionError(f"unscripted call: {method} {url}")
    return _t


print("\n(1) The refresh-token exchange")
SEEN.clear()
c = G.SdmClient(CRED, transport=transport([("oauth2.googleapis.com/token",
                                            (200, {"access_token": "at-1"}))]))
check("access token returned", c.access_token() == "at-1")
call = SEEN[0]
check("posted to Google's token endpoint", call["url"] == G.TOKEN_URL and call["method"] == "POST")
check("sent FORM data, not JSON (Google rejects JSON here)",
      call["json"] is None and isinstance(call["data"], dict))
check("grant_type is refresh_token", call["data"]["grant_type"] == "refresh_token")
check("all four credential fields sent",
      {"client_id", "client_secret", "refresh_token", "grant_type"} == set(call["data"]))
check("the token is cached for the instance (no second round trip)",
      c.access_token() == "at-1" and len(SEEN) == 1)

print("\n(2) A revoked grant is a re-authorization, not a retry")
c = G.SdmClient(CRED, transport=transport([("token", (400, {"error": "invalid_grant",
                                                            "error_description": "Token has been expired or revoked."}))]))
try:
    c.access_token()
    check("raises SdmError", False)
except G.SdmError as e:
    check("raises SdmError", True)
    check("the status is carried so the router can map it to a re-auth prompt", e.status == 400)
    check("Google's own explanation is preserved", "revoked" in str(e))

c = G.SdmClient({}, transport=transport([]))
check("an unconfigured tenant is refused locally with 428, no network call", not c.configured())
try:
    c.access_token()
    check("unconfigured raises", False)
except G.SdmError as e:
    check("unconfigured raises 428 (precondition)", e.status == 428)

print("\n(3) list_devices filters and reads the protocol off the trait")
DEVICES = {"devices": [
    {"name": "enterprises/proj-123/devices/cam-a", "type": "sdm.devices.types.CAMERA",
     "traits": {"sdm.devices.traits.Info": {"customName": "Front Counter"},
                "sdm.devices.traits.CameraLiveStream": {"supportedProtocols": ["WEB_RTC"]}},
     "parentRelations": [{"displayName": "Sales Floor"}]},
    {"name": "enterprises/proj-123/devices/cam-b", "type": "sdm.devices.types.CAMERA",
     "traits": {"sdm.devices.traits.CameraLiveStream": {"supportedProtocols": ["RTSP"]}},
     "parentRelations": [{"displayName": "Back Door"}]},
    {"name": "enterprises/proj-123/devices/door", "type": "sdm.devices.types.DOORBELL",
     "traits": {"sdm.devices.traits.CameraLiveStream": {"supportedProtocols": ["WEB_RTC"]}}},
    {"name": "enterprises/proj-123/devices/therm", "type": "sdm.devices.types.THERMOSTAT",
     "traits": {}},
]}
SEEN.clear()
c = G.SdmClient(CRED, transport=transport([("token", (200, {"access_token": "at"})),
                                           ("/devices", (200, DEVICES))]))
devs = c.list_devices()
check("the thermostat is filtered out", len(devs) == 3)
check("the doorbell is kept (it is a camera with a bell on it)",
      any(d["device_type"].endswith("DOORBELL") for d in devs))
a = [d for d in devs if d["device_name"].endswith("cam-a")][0]
b = [d for d in devs if d["device_name"].endswith("cam-b")][0]
check("WEB_RTC device -> webrtc", a["stream_protocol"] == "webrtc")
check("RTSP-only device -> rtsp", b["stream_protocol"] == "rtsp")
check("the operator's custom name wins", a["display_name"] == "Front Counter")
check("a device with no custom name falls back to its room", b["display_name"] == "Back Door")
check("the room is carried through", a["room"] == "Sales Floor")
check("the device list is a GET on the enterprise path",
      SEEN[1]["method"] == "GET" and SEEN[1]["url"].endswith("enterprises/proj-123/devices"))
check("the access token rides in the Authorization header",
      SEEN[1]["headers"]["Authorization"] == "Bearer at")

print("\n(4) GenerateWebRtcStream")
SEEN.clear()
c = G.SdmClient(CRED, transport=transport([
    ("token", (200, {"access_token": "at"})),
    (":executeCommand", (200, {"results": {"answerSdp": "ANSWER", "mediaSessionId": "ms-1",
                                           "expiresAt": "2026-08-19T12:05:00Z"}}))]))
res = c.generate_stream("enterprises/proj-123/devices/cam-a", "webrtc", offer_sdp="OFFER")
cmd = SEEN[1]["json"]
check("the executeCommand path uses the BARE device id, not the full resource name",
      SEEN[1]["url"].endswith("enterprises/proj-123/devices/cam-a:executeCommand"))
check("command is CameraLiveStream.GenerateWebRtcStream",
      cmd["command"] == "sdm.devices.commands.CameraLiveStream.GenerateWebRtcStream")
check("the viewer's offer is forwarded verbatim", cmd["params"] == {"offerSdp": "OFFER"})
check("the answer SDP comes back", res["answer_sdp"] == "ANSWER")
check("the mediaSessionId (the extension handle) comes back", res["media_session_id"] == "ms-1")
check("the expiry comes back", res["expires_at"] == "2026-08-19T12:05:00Z")

print("\n(5) GenerateRtspStream needs no offer")
SEEN.clear()
c = G.SdmClient(CRED, transport=transport([
    ("token", (200, {"access_token": "at"})),
    (":executeCommand", (200, {"results": {
        "streamUrls": {"rtspUrl": "rtsps://host/live?auth=tok1"},
        "streamExtensionToken": "ext-1", "streamToken": "tok1",
        "expiresAt": "2026-08-19T12:05:00Z"}}))]))
res = c.generate_stream("enterprises/proj-123/devices/cam-b", "rtsp")
check("command is GenerateRtspStream",
      SEEN[1]["json"]["command"] == "sdm.devices.commands.CameraLiveStream.GenerateRtspStream")
check("params is an empty object, not absent", SEEN[1]["json"]["params"] == {})
check("the tokenized URL comes back", res["rtsp_url"] == "rtsps://host/live?auth=tok1")
check("the extension token comes back", res["stream_extension_token"] == "ext-1")

print("\n(6) ExtendRtspStream issues a NEW token every time")
SEEN.clear()
c = G.SdmClient(CRED, transport=transport([
    ("token", (200, {"access_token": "at"})),
    (":executeCommand", (200, {"results": {"streamExtensionToken": "ext-2", "streamToken": "tok2",
                                           "streamUrls": {"rtspUrl": "rtsps://host/live?auth=tok2"},
                                           "expiresAt": "2026-08-19T12:10:00Z"}}))]))
res = c.extend_stream("enterprises/proj-123/devices/cam-b", "rtsp", "ext-1")
check("command is ExtendRtspStream",
      SEEN[1]["json"]["command"] == "sdm.devices.commands.CameraLiveStream.ExtendRtspStream")
check("the OLD token is what is sent", SEEN[1]["json"]["params"] == {"streamExtensionToken": "ext-1"})
check("a NEW token comes back — storing it is what makes the 2nd extension work",
      res["stream_extension_token"] == "ext-2")

SEEN.clear()
c = G.SdmClient(CRED, transport=transport([
    ("token", (200, {"access_token": "at"})),
    (":executeCommand", (200, {"results": {"expiresAt": "2026-08-19T12:10:00Z"}}))]))
res = c.extend_stream("enterprises/proj-123/devices/cam-a", "webrtc", "ms-1")
check("WebRTC extends by mediaSessionId", SEEN[1]["json"]["params"] == {"mediaSessionId": "ms-1"})
check("and keeps the same session id when Google echoes none", res["media_session_id"] == "ms-1")

print("\n(7) A WebRTC stream with no offer never reaches Google")
SEEN.clear()
c = G.SdmClient(CRED, transport=transport([("token", (200, {"access_token": "at"}))]))
try:
    c.generate_stream("enterprises/proj-123/devices/cam-a", "webrtc")
    check("refused locally", False)
except G.SdmError as e:
    check("refused locally with 400", e.status == 400)
    check("no executeCommand was attempted",
          not any(":executeCommand" in s["url"] for s in SEEN))

print("\n(8) Expiry handling")
now = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)
check("RFC3339 with Z parses",
      G.parse_expiry("2026-08-19T12:05:00Z") == now + timedelta(minutes=5))
check("a malformed expiry falls back INSIDE the 5-minute grant (extends early, never late)",
      (G.parse_expiry("not-a-date") - datetime.now(timezone.utc)) < timedelta(minutes=5))
check("needs_extension is True inside the lead window",
      G.needs_extension("2026-08-19T12:00:30Z", now=now, lead_seconds=60) is True)
check("and False outside it",
      G.needs_extension("2026-08-19T12:05:00Z", now=now, lead_seconds=60) is False)

print("\n(9) The one-time consent URL")
url = G.authorization_url("proj-123", "cid", "https://app.example.com/vision/callback")
check("uses Google's Device Access partner-connections host, not the generic OAuth endpoint",
      url.startswith("https://nestservices.google.com/partnerconnections/proj-123/auth"))
check("asks for offline access (that is what mints a refresh token)", "access_type=offline" in url)
check("forces the consent screen so a RE-link still yields a refresh token", "prompt=consent" in url)
check("requests exactly the SDM scope", "sdm.service" in url)

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
for f in FAIL:
    print("  FAILED: " + f)
sys.exit(1 if FAIL else 0)
