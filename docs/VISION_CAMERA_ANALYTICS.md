# Vision — Live Camera Feeds, Customer Heat Maps, Employee Coaching

**Module:** `vision` · **Migration band:** 900–949 (`900_vision_camera_analytics.sql`)
**Backend:** `backend/app/modules/vision/` · **Frontend:** `frontend/src/app/(platform)/vision/`
**Edge:** `backend/vision_edge_analyzer.py`

> Owner directive, 2026-08-19: *"pull the camera feed from the Google home server in live mode and
> give analytics for the employee behavior use their voice transcript, use the heat map based on the
> customers in and out of the store."*

---

## 1. What this is, in one paragraph

Store cameras registered in Google Home / Nest are reached through Google's **Smart Device Management
(SDM)** API. The platform brokers a **live view** in the browser (media flows browser ↔ Google, never
through our servers), and a small **edge analyzer** running beside each store holds the same feed and
turns it into three things: a **door count** of customers in and out, a **floor heat map** of where
people stood, and — only when a company switches it on and each employee has signed a consent —
**coaching numbers** derived from redacted transcripts of what the *employee* said.

No video, no audio, no face descriptors and no customer identity are ever stored.

---

## 2. Why the architecture is shaped this way

Two facts about Nest cameras drive every design decision:

1. **There is no local feed.** Modern Nest cameras expose no LAN RTSP. The Google Home app is itself a
   client of the SDM API — so "pull the feed from the Google Home server" means the SDM API, and it
   means a Device Access project, an OAuth grant, and a refresh token.
2. **A live-stream grant expires in about five minutes.** WebRTC sessions return a `mediaSessionId`
   and an `expiresAt`; RTSP returns a tokenized URL and a `streamExtensionToken` on the same clock.
   Both must be re-extended before they lapse.

### Which transport a camera speaks

**The app managing the camera decides this, not only the model.** Every Nest camera sold since 2021 is
WebRTC-only — but so is an older Nest Cam that has been *migrated into the Google Home app*. RTSP
survives only on devices still managed in the legacy Nest app. So the protocol is read from the
device's `CameraLiveStream.supportedProtocols` trait at sync time and stored per camera, never
inferred from a model name.

| Transport | Devices | Analyzer frame source |
|---|---|---|
| WebRTC | everything since 2021 (incl. Nest Cam indoor wired 2nd gen), and migrated legacy cameras | `WebRtcFrameSource` (aiortc) |
| RTSP | legacy cameras still in the Nest app, Dropcams | `RtspFrameSource` (OpenCV) |

Downstream of the frame source the two are identical — the same tracking, line-crossing and
heat-binning code runs for both, so adding a transport can never change what a crossing means.

So nothing holds a camera open by accident, and the pixel work does not run on Railway:

```
  Browser ──SDP offer──▶ MetricsPro API ──▶ Google SDM
     ▲                        │
     └──────── media ◀────────┴──────────── Google      (live view: we broker, never relay)

  Edge analyzer ──▶ /vision/edge/stream ──▶ Google SDM  (analyzer holds the stream at the store)
  Edge analyzer ──▶ /vision/edge/ingest                 (derived numbers only; frames destroyed)
```

---

## 3. What is stored, and what is deliberately not

| Stored | Not stored |
|---|---|
| Directional entrance-line crossings (`in` / `out`) with a short-lived local track id | Any video frame |
| Anonymous visits: entry time, exit time, dwell | Any audio |
| Person-seconds per heat-grid cell per hour | Face descriptors or any biometric identifier |
| Redacted transcript segments of **consenting employees** | The customer's speech (dropped at ingest) |
| Per-employee/day coaching scores | Any customer identity |

The platform already carries BIPA exposure through the kiosk face path
(`docs/BIOMETRIC_RETENTION_POLICY.md`). This module is designed **not to add to it** — the absence of
face and voice identification is a constraint, not an omission to be filled in later.

---

## 4. The gates, in the order they bind

Proven without a database by `backend/harness_vision_gate.py`.

| # | Gate | Where | Default |
|---|---|---|---|
| 1 | Deployment audio kill switch — env `VISION_AUDIO_ENABLED` | `config.AUDIO_GLOBALLY_DISABLED` | **off** (audio disabled platform-wide) |
| 2 | Company master switch | `vision_config.enabled` | **off** |
| 3 | Feature sub-switches (live view / traffic / heatmap / audio / scoring) | `vision_config.*_enabled` | audio + scoring **off** |
| 4 | Per-camera switches (`enabled`, `analytics_enabled`, `audio_enabled`, `is_entrance`) | `vision_camera` | new cameras have analytics **off** until assigned |
| 5 | Per-employee consent | `vision_consent.status = 'signed'` | **no record = no recording** |

Every gate is AND-ed. **No lower level can re-open a gate a higher level shut** — the precedence
mistake `storeops/face_recognition.py` documents at length. A `declined` or `withdrawn` employee is
never recorded, not even under `audio_consent_mode = 'off'`.

Everything **fails closed**: if migration 900 has not run, or the DB is unreachable, the module
resolves to *off* and reports `available: false` so the UI can say "not installed" instead of "you
turned it off".

---

## 5. Setup

### 5.1 Database
Run `database/migrations/900_vision_camera_analytics.sql` in the Supabase SQL editor. Additive and
idempotent; re-runnable.

### 5.2 Google Device Access (once per company)
1. Create a Device Access project at <https://console.nest.google.com/device-access> (one-time US$5).
2. Create an OAuth client (Web application) in the linked Google Cloud project. Add
   `https://<your-app>/vision/settings` as an authorized redirect URI.
3. In **Vision → Settings → 3 · Google**, paste the project id, client id and client secret, press
   *Save & get the Google consent link*, authorize with the Google account that owns the cameras, and
   paste back the `?code=` value.
4. Press **Sync cameras from Google**.

The client secret and refresh token are stored through `app.core.crypto` (`enc:v1:` envelope) and
there is **no endpoint that reads them back**.

> If Google returns no refresh token, it reused an existing grant — revoke the app at
> <https://myaccount.google.com/permissions> and link again.

### 5.3 Cameras
Assign each camera a **store code**. Mark exactly one per store as the **entrance** (it carries the
counting line). Enable **analytics** on the cameras that should feed the heat map. Draw zones with
`PUT /vision/cameras/{id}/zones`:

* `line` — the counting line across the doorway, plus `inward: left|right` naming which side is the
  store. Direction is the operator's choice; nothing in the code assumes a camera orientation.
* `polygon` — a named area (counter, accessory wall) for dwell reporting.
* `exclude` — an area to ignore entirely (a back office in frame, the pavement through the window).
  Exclusions are the difference between counting your customers and counting the street.

### 5.4 Edge analyzer
Register one per store in **Settings → 5 · Edge analyzers**. The signing secret is shown **once**;
there is no read-back, only rotation. Then, on the store box:

```bash
pip install requests opencv-python ultralytics aiortc   # aiortc: required for WebRTC cameras
python3 backend/vision_edge_analyzer.py \
  --api https://api.example.com \
  --agent-key va_xxxxxxxx --secret <the secret> \
  --tz-offset -420                                  # store's UTC offset in MINUTES
```

**Run `--probe` first, on site.** It connects to one camera, saves a single frame and exits —
proving the whole chain (agent secret → Google authorization → stream negotiation → decode) in one
command. The frame it writes is also the still needed for zone placement, so the install visit
produces that artifact instead of someone screenshotting a phone later:

```bash
python3 backend/vision_edge_analyzer.py --api … --agent-key … --secret … --probe
```

`--dry-run` is the weaker check: it authenticates and fetches config without opening a stream.

### 5.5 Voice transcripts (optional, and the one with legal weight)
1. Set `VISION_AUDIO_ENABLED=1` on the backend. This is deliberately a **deployment** change: most of
   the states these stores operate in require every party to a recorded conversation to consent.
2. Collect a **signed consent** per employee (Settings → 6 · Consent register).
3. Enable *Capture voice transcripts* and *Score behaviour* for the company, and `audio_enabled` on
   the specific cameras.

---

## 6. How the numbers are computed

### Door count — `heatmap.pair_visits`, `geometry.crossing_direction`
A **directional line crossing** (the track's foot point was on one side and is now on the other, and
the step actually intersected the drawn segment). Counting visible blobs instead would re-count one
customer every time the detector lost and re-acquired them.

Two rules keep it honest, both proven in `harness_vision_heatmap.py`:
* **An unpaired entry is still a visit** (`exited_at = NULL`, no dwell). Dropping those undercounts
  traffic on exactly the busiest days.
* **Visits outside the duration band are classified, not deleted** — `passerby`, `customer`,
  `staff_or_merged` — so an operator can see what was filtered and retune the band.

The running in-minus-out is reported as a **curve plus an explicit drift number**, never as "people
currently in the store": every missed exit pushes the running value up by one.

### Heat map — `geometry.foot_point`, `heatmap.aggregate_presence`
Occupancy is binned at each detection's **foot point** — the midpoint of the box's *bottom* edge, not
its centre. A box centre sits at chest height and lands roughly a metre behind where the person is
standing in an angled view, which puts the heat on the wall behind the counter instead of at it.

The map is normalised at the **95th percentile**, not the max: clipping at the max makes every store
look like one scorching cell at the register with everything else black.

Dead zones (cells at ≤5% of the ceiling) are reported alongside the hot cells — a display table
nobody walks past is invisible in a report that only ranks the busiest spots.

### Coaching — `behavior.score_interactions`
The score is **coverage, not volume**: for each rubric rule, what share of an employee's
*interactions* contained it. Saying "protection plan" nine times to one customer must not beat saying
it once to nine customers. Negative rules subtract; the total is clamped to 0–100.

The rubric (`core.vision_behavior_rule`) is **tenant data**, seeded from `behavior.DEFAULT_RULES`. A
store selling home internet needs a different checklist than one selling tablets and neither should
need a deploy.

**Attribution** comes from the time clock, not from the audio: telling employees apart by voice would
need enrolled voiceprints, which this module does not collect. `GET /vision/edge/config` returns
`attribution: "unambiguous"` only when exactly **one consented employee is clocked in** at that store;
anything else and the analyzer sends nothing.

**This is a coaching aid, not a performance rating.** The migration gives it no path into any payout
table, and the API returns a disclaimer that the UI prints.

---

## 7. API surface

| Route | Auth | Notes |
|---|---|---|
| `GET/PUT /vision/config` | JWT (+ `vision` settings to write) | tenant switches |
| `GET /vision/status` | JWT | every gate and what is behind it |
| `GET /vision/google/auth-url`, `POST/DELETE /vision/google/link` | JWT + settings | one-time OAuth link |
| `POST /vision/cameras/sync` | JWT + settings | additive; a vanished camera is marked offline, never deleted |
| `GET /vision/cameras`, `PATCH /vision/cameras/{id}` | JWT | scoped to the caller's reporting span |
| `GET/PUT /vision/cameras/{id}/zones` | JWT | whole-set replace; geometry validated on write |
| `POST /vision/cameras/{id}/stream` | JWT | WebRTC SDP broker; audited |
| `POST /vision/stream/{id}/extend` \| `/stop` | JWT | `stream_max_minutes` ceiling enforced server-side |
| `GET /vision/stream-sessions` | JWT, manager | **who watched which camera, when** |
| `GET /vision/traffic`, `/heatmap` | JWT | store-scoped |
| `GET /vision/behavior`, `POST /vision/behavior/recompute` | JWT, manager | |
| `GET /vision/behavior/mine` | JWT | **no manager role** — a person may always see their own numbers |
| `GET/POST /vision/consent` | JWT (self-service or manager) | withdrawal accepted from anyone about themselves |
| `GET/PUT /vision/rules` | JWT (+ settings to write) | the coaching rubric |
| `GET /vision/retention/plan`, `POST /vision/retention/purge` | JWT + settings | purge needs `confirm: true` |
| `POST /vision/edge/heartbeat`, `GET /vision/edge/config`, `POST /vision/edge/stream`, `/stream/extend`, `/ingest` | **HMAC** | no JWT — see below |

### The edge surface
`/api/v1/vision/edge/*` is on the tenant-middleware public-prefix allowlist (same shape as
`/core/fix-pipeline`): the caller is a machine with no login, so the JWT requirement would fire before
the handler could check the credential it *does* carry — HMAC-SHA256 over `timestamp.body` with a
±5-minute skew window. Every route self-gates and resolves `org_id` **from the agent record**, never
from the request. Unknown agent, disabled agent, bad signature and stale timestamp all return an
identical 401, so a probe learns nothing. Boundary-matched: `/vision/edge-agents` is *not* public.

---

## 8. Retention

| Data | Default | Why |
|---|---|---|
| Occupancy samples | 7 days | fuel for the aggregate, not a report |
| Voice transcripts | 30 days | long enough for the coaching conversation, no longer |
| Customer visits | 90 days | season-over-season dwell comparison |
| Heat cells (aggregate) | 400 days | no per-person detail; enables year-on-year |
| Behavior scores | 400 days | |

`POST /vision/retention/purge` is a **dry run by default**. `core.vision_audit` is never purged —
deleting the record of who watched which camera alongside the data it audits would defeat the point.

---

## 9. Proof harnesses

All offline — no network, no DB, no camera. Run them before touching any counting rule.

```bash
python3 backend/harness_vision_gate.py       # 36 checks — the enablement + consent table
python3 backend/harness_vision_geometry.py   # 34 checks — line crossing, zones, grid, foot point
python3 backend/harness_vision_heatmap.py    # 41 checks — visit pairing, traffic, aggregation
python3 backend/harness_vision_behavior.py   # 42 checks — redaction, rubric matching, scoring
python3 backend/harness_vision_sdm.py        # 45 checks — every Google request shape
python3 backend/harness_vision_ingest.py     # 46 checks — HMAC + what the analyzer may send
python3 backend/harness_vision_webrtc.py     # 18 checks — the WebRTC frame source (needs aiortc)
```

---

## 10. Known limits, stated rather than discovered

* **The WebRTC path is unverified against real hardware.** Both transports are implemented, but there
  is no Nest camera in the build environment, so `harness_vision_webrtc.py` proves the WebRTC source
  against a real aiortc peer standing in for Google — offer shape (both m-lines, recvonly), ICE
  completeness, decode to BGR, the overwrite-don't-queue frame slot, staleness, and failure
  reporting. What it cannot prove is that Google accepts this exact offer, that Nest's codecs decode
  in the field, or that ICE traverses a real store network. `--probe` is the on-site confirmation and
  it is a single command.
* **The audio path is not wired up in the reference analyzer.** OpenCV's capture discards the audio
  track, so transcripts need a separate ffmpeg demux + VAD + local ASR. The event contract the server
  enforces is documented in the analyzer docstring and in `app/modules/vision/ingest.py`.
* **The OpenCV HOG fallback detector under-counts.** It misses seated and heavily occluded people.
  It exists so the module produces numbers on hardware with no accelerator; the analyzer warns loudly
  at startup when it is in use, and production deployments should install `ultralytics`.
* **Redaction catches digit forms, not spelled-out ones.** An ASR transcript of "four one five…" is
  not caught by a regex. The mitigation that actually holds is the short transcript retention window.
* **FIFO visit pairing is wrong per customer and right in aggregate.** Visits closed that way carry
  `paired_by = 'fifo'` so a report can exclude them.
