"""Google Smart Device Management (SDM) client — the "Google Home server" side of the module.

WHAT GOOGLE ACTUALLY OFFERS (and what it does not)
──────────────────────────────────────────────────
Nest cameras — the cameras behind the Google Home app — are reachable programmatically only through
the **Smart Device Management API**, gated behind a Device Access project. There is no local RTSP on
the modern devices and no "server" on the LAN to pull from; the Google Home app itself is a client of
this same API. So "pull the camera feed from the Google Home server in live mode" concretely means:

  1. The operator creates a Device Access project (console.nest.google.com/device-access, one-time
     US$5 registration) and an OAuth client in a Google Cloud project.
  2. They authorize the Google account that owns the store cameras, once, granting the scope
     `https://www.googleapis.com/auth/sdm.service`. We keep only the refresh token, encrypted.
  3. `list_devices()` returns every camera/doorbell/display on that account.
  4. `generate_stream()` asks Google for a LIVE stream grant for one device.

THE PART THAT SHAPES THIS WHOLE MODULE: **a stream grant expires in about five minutes.**
WebRTC sessions return an `expiresAt` and a `mediaSessionId` that must be re-extended before it
lapses; RTSP returns a URL with an embedded token plus a `streamExtensionToken` on the same clock.
That is why nothing in this platform "holds a camera open": the backend issues a short grant, records
it in `core.vision_stream_session`, and re-extends only while a viewer is actually watching or an
edge analyzer is actually running. It is also why the analytics live at the edge — a FastAPI process
on Railway re-negotiating and decoding a dozen WebRTC video tracks is not a thing that would survive
contact with production.

PROTOCOL BY DEVICE
──────────────────
Battery Nest Cams and every camera released since 2021 are **WebRTC only**; older wired Nest Cam /
Dropcam models expose **RTSP**. The device's `CameraLiveStream` trait declares which, so
`device_stream_protocol()` reads it rather than guessing and failing at issue time.

TESTABILITY
───────────
Every network call goes through the injected `transport` callable
(`transport(method, url, headers=…, json=…, timeout=…) -> (status, dict)`), which defaults to
`requests`. `backend/harness_vision_sdm.py` drives the whole token-refresh / list / generate / extend
path against a scripted fake, so the request shapes are proven without a Google account.
"""
import re
from datetime import datetime, timedelta, timezone

SDM_BASE = "https://smartdevicemanagement.googleapis.com/v1"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SDM_SCOPE = "https://www.googleapis.com/auth/sdm.service"
AUTH_URL = "https://nestservices.google.com/partnerconnections/{project_id}/auth"

CAMERA_TYPES = ("sdm.devices.types.CAMERA", "sdm.devices.types.DOORBELL", "sdm.devices.types.DISPLAY")
STRUCTURE_INFO_TRAIT = "sdm.structures.traits.Info"
_STRUCTURE_RE = re.compile(r"/structures/([^/]+)")

# A Device Access project id is a UUID issued by console.nest.google.com/device-access. It is NOT
# the Google Cloud project id, and the two are easy to confuse because both are called "project id"
# and both appear during the same setup. See looks_like_device_access_project_id().
_DA_PROJECT_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
_GCP_PROJECT_RE = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")
LIVE_STREAM_TRAIT = "sdm.devices.traits.CameraLiveStream"
INFO_TRAIT = "sdm.devices.traits.Info"

DEFAULT_TIMEOUT = 20


class SdmError(RuntimeError):
    """A Google-side failure with the status attached, so the router can map 401/403 to a re-auth
    prompt ("your Google authorization expired") instead of a generic 500."""

    def __init__(self, message, status=None, payload=None):
        super().__init__(message)
        self.status = status
        self.payload = payload or {}


# ── transport ────────────────────────────────────────────────────────────────────────────────────
def _requests_transport(method, url, headers=None, json_body=None, data=None, timeout=DEFAULT_TIMEOUT):
    import requests
    r = requests.request(method, url, headers=headers, json=json_body, data=data, timeout=timeout)
    try:
        payload = r.json()
    except Exception:
        payload = {"raw": (r.text or "")[:500]}
    return r.status_code, payload


class SdmClient:
    """Stateless-per-request SDM client. Construct it with the tenant's decrypted credential dict:
        {project_id, client_id, client_secret, refresh_token}
    It exchanges the refresh token for an access token on first use and caches it for this instance
    only — access tokens live an hour and a request handler lives milliseconds, so there is nothing to
    gain (and a token-leak surface to lose) by caching them across requests in a module global."""

    def __init__(self, credential: dict, transport=None):
        cred = credential or {}
        self.project_id = (cred.get("project_id") or "").strip()
        self.client_id = (cred.get("client_id") or "").strip()
        self.client_secret = (cred.get("client_secret") or "").strip()
        self.refresh_token = (cred.get("refresh_token") or "").strip()
        self._transport = transport or _requests_transport
        self._access_token = None

    # ── auth ─────────────────────────────────────────────────────────────────────────────────────
    def configured(self) -> bool:
        return bool(self.project_id and self.client_id and self.client_secret and self.refresh_token)

    def access_token(self) -> str:
        if self._access_token:
            return self._access_token
        if not self.configured():
            raise SdmError("Google Device Access is not configured for this tenant.", status=428)
        status, payload = self._transport(
            "POST", TOKEN_URL,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={"client_id": self.client_id, "client_secret": self.client_secret,
                  "refresh_token": self.refresh_token, "grant_type": "refresh_token"},
            timeout=DEFAULT_TIMEOUT)
        if status != 200 or not payload.get("access_token"):
            # invalid_grant means the operator revoked the app, changed the Google password, or the
            # token aged out unused. That is a re-authorize, not a retry — say so.
            err = payload.get("error_description") or payload.get("error") or f"HTTP {status}"
            msg = f"Google token refresh failed: {err}"
            if payload.get("error") == "invalid_grant":
                # By far the most common cause, and the one nobody guesses: while the OAuth consent
                # screen is still in TESTING, Google expires every refresh token after SEVEN DAYS.
                # Cameras go dark once a week, on the dot, with a message that otherwise reads like a
                # random Google outage. Name it here — this is where the operator actually looks.
                msg += (". Reconnect Google in Vision Settings. If this comes back roughly every 7 "
                        "days, the cause is the OAuth consent screen still being in Testing: Google "
                        "expires test-mode refresh tokens weekly. Publish the app to Production in "
                        "the Google Cloud console to stop it.")
            raise SdmError(msg, status=status, payload=payload)
        self._access_token = payload["access_token"]
        return self._access_token

    def _call(self, method, path, body=None):
        status, payload = self._transport(
            method, f"{SDM_BASE}/{path.lstrip('/')}",
            headers={"Authorization": f"Bearer {self.access_token()}",
                     "Content-Type": "application/json"},
            json_body=body, timeout=DEFAULT_TIMEOUT)
        if status >= 400:
            err = ((payload.get("error") or {}).get("message")
                   if isinstance(payload.get("error"), dict) else payload.get("error"))
            raise SdmError(err or f"Google SDM returned HTTP {status}", status=status, payload=payload)
        return payload

    # ── structures (the "homes" in the Google Home app) ──────────────────────────────────────────
    def list_structures(self) -> list:
        """Every home on the authorized account.

        A Device Access grant is per GOOGLE ACCOUNT, and one account routinely owns several homes —
        four stores, or three stores and the operator's house. Listing them is what lets a company
        say which homes are ITS homes, instead of the platform assuming that everything the account
        can see belongs to whoever linked it."""
        payload = self._call("GET", f"enterprises/{self.project_id}/structures")
        out = []
        for st in payload.get("structures") or []:
            name = st.get("name") or ""
            sid = name.rsplit("/", 1)[-1]
            info = (st.get("traits") or {}).get(STRUCTURE_INFO_TRAIT) or {}
            out.append({"structure_id": sid,
                        "structure_name": info.get("customName") or sid,
                        "resource": name})
        return out

    # ── devices ──────────────────────────────────────────────────────────────────────────────────
    def list_devices(self) -> list:
        """Every device on the authorized account, normalized to the shape core.vision_camera stores.
        Non-camera devices (thermostats) are filtered out — this module has no use for them and
        listing them in a camera picker is a support ticket waiting to happen."""
        payload = self._call("GET", f"enterprises/{self.project_id}/devices")
        out = []
        for d in payload.get("devices") or []:
            dtype = d.get("type") or ""
            if dtype not in CAMERA_TYPES:
                continue
            traits = d.get("traits") or {}
            live = traits.get(LIVE_STREAM_TRAIT) or {}
            protocols = [str(p).upper() for p in (live.get("supportedProtocols") or [])]
            out.append({
                "device_name": d.get("name") or "",
                "device_type": dtype,
                "structure_id": _structure_of(d),
                "display_name": ((traits.get(INFO_TRAIT) or {}).get("customName")
                                 or _room_of(d) or _short_id(d.get("name"))),
                "room": _room_of(d),
                "stream_protocol": "rtsp" if ("RTSP" in protocols and "WEB_RTC" not in protocols)
                                   else "webrtc",
                "supported_protocols": protocols,
                # Nest exposes no "has a microphone" trait, so this is inferred from the device class:
                # cameras and doorbells carry mics, a Nest Hub display does too. The operator still has
                # to turn audio on per camera, and the employee still has to consent, so an optimistic
                # capability flag here cannot by itself cause anything to be recorded.
                "supports_audio": dtype in CAMERA_TYPES,
            })
        return out

    def device_id(self, device_name: str) -> str:
        """The bare device id from a full SDM resource path."""
        return (device_name or "").rsplit("/", 1)[-1]

    # ── live stream ──────────────────────────────────────────────────────────────────────────────
    def generate_stream(self, device_name: str, protocol: str = "webrtc", offer_sdp: str = None) -> dict:
        """Ask Google for a live stream grant.

        WebRTC needs the VIEWER's SDP offer — the media flows browser↔Google directly, and this
        backend only brokers the handshake, so no video ever transits Railway. RTSP returns a URL the
        edge analyzer opens; that URL embeds a token and must be treated as a secret.

        Returns a normalized dict:
          {protocol, answer_sdp|rtsp_url, media_session_id|stream_extension_token, expires_at}
        """
        did = self.device_id(device_name)
        proto = (protocol or "webrtc").strip().lower()
        if proto == "rtsp":
            res = self._execute(did, "sdm.devices.commands.CameraLiveStream.GenerateRtspStream", {})
            urls = res.get("streamUrls") or {}
            return {
                "protocol": "rtsp",
                "rtsp_url": urls.get("rtspUrl"),
                "stream_extension_token": res.get("streamExtensionToken"),
                "stream_token": res.get("streamToken"),
                "expires_at": res.get("expiresAt"),
            }
        if not offer_sdp:
            raise SdmError("A WebRTC stream requires the viewer's SDP offer.", status=400)
        res = self._execute(did, "sdm.devices.commands.CameraLiveStream.GenerateWebRtcStream",
                            {"offerSdp": offer_sdp})
        return {
            "protocol": "webrtc",
            "answer_sdp": res.get("answerSdp"),
            "media_session_id": res.get("mediaSessionId"),
            "expires_at": res.get("expiresAt"),
        }

    def extend_stream(self, device_name: str, protocol: str, token: str) -> dict:
        """Push the expiry out before the grant lapses. `token` is the mediaSessionId (WebRTC) or the
        streamExtensionToken (RTSP). Google issues a NEW extension token for RTSP each time, so the
        caller must store what comes back — reusing the old one fails on the second extension."""
        did = self.device_id(device_name)
        if (protocol or "webrtc").strip().lower() == "rtsp":
            res = self._execute(did, "sdm.devices.commands.CameraLiveStream.ExtendRtspStream",
                                {"streamExtensionToken": token})
            urls = res.get("streamUrls") or {}
            return {"protocol": "rtsp", "expires_at": res.get("expiresAt"),
                    "stream_extension_token": res.get("streamExtensionToken") or token,
                    "stream_token": res.get("streamToken"),
                    "rtsp_url": urls.get("rtspUrl")}
        res = self._execute(did, "sdm.devices.commands.CameraLiveStream.ExtendWebRtcStream",
                            {"mediaSessionId": token})
        return {"protocol": "webrtc", "expires_at": res.get("expiresAt"),
                "media_session_id": res.get("mediaSessionId") or token}

    def stop_stream(self, device_name: str, protocol: str, token: str) -> dict:
        """Hand the grant back early when a viewer closes the tab. Best-effort by nature — if this
        fails the grant simply expires on its own — so callers swallow the error."""
        did = self.device_id(device_name)
        if (protocol or "webrtc").strip().lower() == "rtsp":
            return self._execute(did, "sdm.devices.commands.CameraLiveStream.StopRtspStream",
                                 {"streamExtensionToken": token})
        return self._execute(did, "sdm.devices.commands.CameraLiveStream.StopWebRtcStream",
                             {"mediaSessionId": token})

    def _execute(self, device_id: str, command: str, params: dict) -> dict:
        payload = self._call("POST", f"enterprises/{self.project_id}/devices/{device_id}:executeCommand",
                             {"command": command, "params": params or {}})
        return payload.get("results") or {}


# ── helpers ──────────────────────────────────────────────────────────────────────────────────────
def _structure_of(device: dict) -> str:
    """The home this device sits in, read out of its parentRelations.

    SDM does not hand back a structure field; it hands back a room resource path shaped
    `enterprises/<p>/structures/<sid>/rooms/<rid>`, so the home id is parsed from that. A device with
    no usable parent returns "" and is therefore treated as belonging to NO assigned home — which,
    given the allowlist is fail-closed, means it does not import. That is the right default: a
    camera whose home cannot be established is exactly the one not to guess about."""
    for rel in device.get("parentRelations") or []:
        m = _STRUCTURE_RE.search(rel.get("parent") or "")
        if m:
            return m.group(1)
    return ""


def _room_of(device: dict) -> str:
    for rel in device.get("parentRelations") or []:
        if rel.get("displayName"):
            return rel["displayName"]
    return ""


def _short_id(name: str) -> str:
    return ((name or "").rsplit("/", 1)[-1] or "camera")[:12]


def looks_like_device_access_project_id(value) -> bool:
    """True if this is UUID-shaped, i.e. plausibly a Device Access project id."""
    return bool(_DA_PROJECT_RE.match(str(value or "").strip()))


def looks_like_cloud_project_id(value) -> bool:
    """True if this looks like a GOOGLE CLOUD project id ('metrics-pro-506103') rather than a
    Device Access one. Used only to give a specific error message — see project_id_problem()."""
    v = str(value or "").strip()
    return bool(_GCP_PROJECT_RE.match(v)) and not looks_like_device_access_project_id(v)


def project_id_problem(value):
    """None if the value is a usable Device Access project id, else a message saying what is wrong.

    WHY THIS EXISTS. Two different things in this setup are called "project id", they are issued by
    two different consoles minutes apart, and only one of them works here:

      Google Cloud project id     'metrics-pro-506103'                    -> holds the OAuth client
      Device Access project id    '32c4c2bc-fe0d-461b-b51c-f3885afff2f0'  -> what SDM addresses

    Pasting the Cloud one is the single most common setup mistake, and without this check it fails
    LATE and opaquely: the credential saves fine, the consent screen may even load, and then every
    device call 404s from a URL the operator never sees. Catching it at save time — and naming which
    id was pasted — turns a support call into a corrected copy-paste."""
    v = str(value or "").strip()
    if not v:
        return "A Device Access project id is required."
    if looks_like_device_access_project_id(v):
        return None
    if looks_like_cloud_project_id(v):
        return (f"'{v}' looks like your GOOGLE CLOUD project id, not your Device Access project id. "
                "They are different: the Cloud project holds the OAuth client, while Device Access "
                "issues a separate id that looks like 32c4c2bc-fe0d-461b-b51c-f3885afff2f0. Get it "
                "from console.nest.google.com/device-access (creating the project there is the "
                "one-time $5 step).")
    return ("That is not a Device Access project id. It should be a UUID like "
            "32c4c2bc-fe0d-461b-b51c-f3885afff2f0, from console.nest.google.com/device-access.")


def authorization_url(project_id: str, client_id: str, redirect_uri: str) -> str:
    """The Device Access partner-connections consent URL the operator visits ONCE to link the Google
    account that owns the store cameras. Google requires this specific host (not the generic OAuth
    endpoint) for SDM, because the consent screen is where the user picks WHICH devices to share."""
    from urllib.parse import urlencode
    q = urlencode({
        "redirect_uri": redirect_uri,
        "access_type": "offline",
        "prompt": "consent",           # forces a refresh_token even on a re-authorization
        "client_id": client_id,
        "response_type": "code",
        "scope": SDM_SCOPE,
    })
    return f"{AUTH_URL.format(project_id=project_id)}?{q}"


def exchange_code(client_id: str, client_secret: str, code: str, redirect_uri: str,
                  transport=None) -> dict:
    """Trade the one-time authorization code for the long-lived refresh token. Called once per tenant
    from the settings page's OAuth callback."""
    transport = transport or _requests_transport
    status, payload = transport(
        "POST", TOKEN_URL, headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={"client_id": client_id, "client_secret": client_secret, "code": code,
              "grant_type": "authorization_code", "redirect_uri": redirect_uri},
        timeout=DEFAULT_TIMEOUT)
    if status != 200 or not payload.get("refresh_token"):
        err = payload.get("error_description") or payload.get("error") or f"HTTP {status}"
        # No refresh_token on a 200 means Google reused an existing grant: the operator must revoke
        # the app at myaccount.google.com/permissions and link again, or the link is unusable.
        raise SdmError(f"Google authorization failed: {err}. If Google returned no refresh token, "
                       "revoke the app under your Google account permissions and link again.",
                       status=status, payload=payload)
    return payload


def parse_expiry(expires_at) -> datetime:
    """Google's RFC3339 `expiresAt` -> aware datetime. Falls back to now+4m — one minute inside the
    documented five-minute grant — so a malformed timestamp makes the extender run EARLY rather than
    letting a stream die silently mid-view."""
    if isinstance(expires_at, datetime):
        return expires_at if expires_at.tzinfo else expires_at.replace(tzinfo=timezone.utc)
    s = (expires_at or "").strip()
    if s:
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.now(timezone.utc) + timedelta(minutes=4)


def needs_extension(expires_at, now=None, lead_seconds: int = 60) -> bool:
    """True when the grant lapses within `lead_seconds`. The default lead is a full minute because a
    missed extension is not a retry — it drops the viewer and forces a fresh SDP negotiation."""
    now = now or datetime.now(timezone.utc)
    return parse_expiry(expires_at) - now <= timedelta(seconds=lead_seconds)
