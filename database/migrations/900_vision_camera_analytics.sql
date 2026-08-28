-- 900_vision_camera_analytics.sql — Vision: live Google Nest camera feeds, customer heat maps,
-- and voice-transcript employee-behavior analytics.
--
-- OWNER DIRECTIVE 2026-08-19 (sanjot@): "pull the camera feed from the Google home server in live
-- mode and give analytics for the employee behavior use their voice transcript, use the heat map
-- based on the customers in and out of the store."
--
-- BAND: 900–949 is hereby RESERVED FOR VISION. Referral's header reserved 850–899; the highest number
-- applied anywhere before this is 863 (core_audit_worm). Vision picks the next free band so the two
-- can never collide on a migration number.
--
-- SCHEMA CHOICE — core.vision_*, NOT a `vision` schema. Identical reasoning to migrations 800 (CRM)
-- and 850 (Referral): PostgREST only serves schemas on the project's "Exposed schemas" dashboard list,
-- which is not reachable from here, so every `.schema("vision")` call would 404. `core` is exposed and
-- the vision_ prefix keeps this block legible next to crm_* / referral_*.
--
-- ══ WHAT THIS DOES AND DOES NOT STORE (read before adding a column) ═══════════════════════════════
-- NO VIDEO. NO AUDIO. NO FACE DESCRIPTORS. NO PER-CUSTOMER IDENTITY.
-- Google Nest live streams are short-lived (a WebRTC/RTSP grant expires in minutes) and are consumed
-- by an EDGE ANALYZER that runs beside the store's network, never by this API. The edge analyzer posts
-- only DERIVED NUMBERS — "a person crossed the entrance line inward at 14:02", "grid cell (4,7) was
-- occupied for 38 of the last 60 seconds", "this transcript segment contained a greeting" — and the
-- raw frames and raw audio are discarded at the edge. That is what makes this storable at all:
--   * a heat map is an occupancy COUNT per grid cell, so it cannot re-identify anyone;
--   * a customer "visit" is an anonymous track id with an entry/exit time, never a person;
--   * a transcript row is REDACTED TEXT of an EMPLOYEE who consented, never the customer's speech.
-- Anything that would make this a biometric system (face embeddings, customer identity, voiceprints)
-- is deliberately absent and must stay absent — the platform already carries BIPA exposure through the
-- kiosk face path (docs/BIOMETRIC_RETENTION_POLICY.md) and this module is designed NOT to add to it.
--
-- ══ CONSENT + THE MASTER SWITCH ══════════════════════════════════════════════════════════════════
-- Same shape as the kiosk face-recognition switch (migration 420), and for the same reason: the safe
-- state and the default state must be the same one. `vision_config.enabled` defaults FALSE, and
-- `audio_analytics_enabled` defaults FALSE SEPARATELY — a tenant can run the heat map (which records
-- no speech at all) without ever turning on transcript capture. Audio additionally requires a per
-- employee CONSENT row, because a majority of the states this operates in have two-party recording
-- consent statutes and an "assumed" consent is not a defence for a voice recording.
--
-- SAFE: additive + idempotent (create ... if not exists / on conflict do nothing). Re-runnable.
-- MONEY: touches NO payout, rate, plan, or commission column anywhere. Behavior scores are coaching
--        numbers; nothing in this migration can move a dollar.
-- SECURITY: RLS on, zero policies, zero anon/authenticated grants (AGENT_CONTRACT §5). The backend
--           service role bypasses RLS. The edge analyzer authenticates with a per-agent HMAC secret,
--           not the anon key, so it needs no policy either.

BEGIN;

CREATE SCHEMA IF NOT EXISTS core;

-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- 1. CONFIGURATION (RULE TWO — nothing about a tenant's vision program is hard-coded)
-- ══════════════════════════════════════════════════════════════════════════════════════════════

-- One row per org. Every switch, threshold and retention window lives here so an operator tunes the
-- program without a code change; backend resolve_config() mirrors these defaults so the module still
-- answers correctly before the row (or this migration) exists.
CREATE TABLE IF NOT EXISTS core.vision_config (
  org_id                    UUID PRIMARY KEY,

  -- MASTER SWITCH. Default FALSE: the feature is off for every tenant the moment the code deploys.
  enabled                   BOOLEAN     NOT NULL DEFAULT false,
  enabled_at                TIMESTAMPTZ,
  enabled_by                TEXT,

  -- Sub-switches. Each is independently off-able, because they carry very different legal weight.
  live_view_enabled         BOOLEAN     NOT NULL DEFAULT true,   -- operator watches the live feed
  traffic_enabled           BOOLEAN     NOT NULL DEFAULT true,   -- in/out door counts
  heatmap_enabled           BOOLEAN     NOT NULL DEFAULT true,   -- occupancy grid
  audio_analytics_enabled   BOOLEAN     NOT NULL DEFAULT false,  -- voice transcript capture (OFF)
  behavior_scoring_enabled  BOOLEAN     NOT NULL DEFAULT false,  -- scoring derived from transcripts

  -- Consent policy for the audio path. 'required' = an employee with no signed row is never recorded
  -- and their segments are refused at ingest. 'off' exists only for a jurisdiction/tenant where the
  -- operator has their own recorded release on file; it is NOT the default and is audited when set.
  audio_consent_mode        TEXT        NOT NULL DEFAULT 'required',  -- required | off

  -- Retention (days). Raw-ish rows expire fast; the aggregates a manager actually looks at live long.
  presence_retention_days   INT         NOT NULL DEFAULT 7,    -- per-sample occupancy
  visit_retention_days      INT         NOT NULL DEFAULT 90,   -- anonymous visit tracks
  transcript_retention_days INT         NOT NULL DEFAULT 30,   -- redacted employee transcripts
  heat_retention_days       INT         NOT NULL DEFAULT 400,  -- rolled-up heat cells (YoY compare)
  score_retention_days      INT         NOT NULL DEFAULT 400,  -- behavior scores

  -- Heat-map grid resolution. The edge analyzer bins normalized floor coordinates into
  -- grid_cols x grid_rows cells; changing this changes future rows only (old cells keep their grid).
  grid_cols                 INT         NOT NULL DEFAULT 24,
  grid_rows                 INT         NOT NULL DEFAULT 16,

  -- A visit shorter than this is noise (someone walking past the door), longer than this is staff.
  min_visit_seconds         INT         NOT NULL DEFAULT 20,
  max_visit_seconds         INT         NOT NULL DEFAULT 5400,

  -- Live-stream grants. Nest expires a stream in ~5 minutes; the backend re-extends while a viewer is
  -- watching, up to this ceiling, then makes them click again (so a forgotten open tab cannot hold a
  -- store's camera open all day).
  stream_max_minutes        INT         NOT NULL DEFAULT 30,

  created_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at                TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- 2. THE GOOGLE SIDE — Device Access credentials + the camera registry
-- ══════════════════════════════════════════════════════════════════════════════════════════════

-- Google Smart Device Management (the API behind Nest cameras / "Google Home") OAuth material.
-- The refresh token is stored through app.core.crypto (enc:v1:… envelope), never in the clear.
-- One row per org — a tenant registers ONE Device Access project and authorizes the Google account
-- that owns the store cameras.
CREATE TABLE IF NOT EXISTS core.vision_credential (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id              UUID NOT NULL,
  provider            TEXT NOT NULL DEFAULT 'google_sdm',   -- google_sdm (room for onvif/ring later)
  project_id          TEXT,            -- Device Access project id (the "enterprise" in the SDM path)
  client_id           TEXT,
  client_secret_enc   TEXT,            -- enc:v1:…
  refresh_token_enc   TEXT,            -- enc:v1:…  (the long-lived grant)
  google_account      TEXT,            -- which Google account authorized, for the operator's benefit
  scopes              TEXT,
  status              TEXT NOT NULL DEFAULT 'needs_setup',  -- needs_setup | ok | error | revoked
  last_ok_at          TIMESTAMPTZ,
  last_error          TEXT,
  last_error_at       TIMESTAMPTZ,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (org_id, provider)
);

-- One row per camera the tenant has told us about. `device_name` is the SDM resource path
-- (enterprises/<project>/devices/<id>) — the id Google addresses the device by.
CREATE TABLE IF NOT EXISTS core.vision_camera (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id             UUID NOT NULL,
  device_name        TEXT NOT NULL,          -- enterprises/<project>/devices/<device-id>
  device_type        TEXT,                   -- sdm.devices.types.CAMERA | DOORBELL | DISPLAY
  display_name       TEXT,                   -- what Google calls it ("Front Counter")
  label              TEXT,                   -- what the operator calls it (wins in the UI)
  store_code         TEXT,                   -- the store this camera watches (joins the whole app)
  room               TEXT,

  -- Which live-stream protocol this device supports. Battery/newer Nest cams are WebRTC-only; older
  -- wired cams expose RTSP. The backend picks from here instead of guessing and failing at issue time.
  stream_protocol    TEXT NOT NULL DEFAULT 'webrtc',  -- webrtc | rtsp
  supports_audio     BOOLEAN NOT NULL DEFAULT false,

  -- Per-camera opt-outs. A camera in a back office or a break room can be registered for LIVE VIEW
  -- but excluded from analytics entirely; a camera pointed at the door is the one that counts traffic.
  analytics_enabled  BOOLEAN NOT NULL DEFAULT true,
  audio_enabled      BOOLEAN NOT NULL DEFAULT false,
  is_entrance        BOOLEAN NOT NULL DEFAULT false,  -- carries the in/out counting line
  enabled            BOOLEAN NOT NULL DEFAULT true,

  status             TEXT NOT NULL DEFAULT 'unknown',  -- online | offline | unknown
  last_seen_at       TIMESTAMPTZ,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (org_id, device_name)
);
CREATE INDEX IF NOT EXISTS vision_camera_store ON core.vision_camera (org_id, store_code);

-- Zones and counting lines drawn on a camera's normalized image plane (0..1 on both axes, so a
-- resolution change never invalidates a drawing).
--   kind='line'    → geometry {"x1":..,"y1":..,"x2":..,"y2":..} plus `inward` naming which side is
--                    "into the store"; crossings become traffic events.
--   kind='polygon' → geometry {"points":[[x,y],…]}; dwell inside it becomes a zone statistic
--                    (counter, accessory wall, display table, waiting area).
--   kind='exclude' → a polygon the analyzer ignores completely (a back office in frame, a window
--                    showing the sidewalk) — this is how a tenant keeps the pavement out of the count.
CREATE TABLE IF NOT EXISTS core.vision_zone (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id       UUID NOT NULL,
  camera_id    UUID NOT NULL,
  kind         TEXT NOT NULL DEFAULT 'polygon',   -- line | polygon | exclude
  name         TEXT NOT NULL,
  zone_key     TEXT,                              -- stable slug for reporting (entrance, counter, …)
  geometry     JSONB NOT NULL DEFAULT '{}'::jsonb,
  inward       TEXT DEFAULT 'left',               -- line only: which side counts as INTO the store
  is_active    BOOLEAN NOT NULL DEFAULT true,
  sort_order   INT NOT NULL DEFAULT 100,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS vision_zone_camera ON core.vision_zone (org_id, camera_id);

-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- 3. THE EDGE ANALYZER — who is allowed to post derived events
-- ══════════════════════════════════════════════════════════════════════════════════════════════

-- One row per analyzer node (typically one small box per store, or one container per market). It
-- authenticates every ingest POST with HMAC-SHA256 over the raw body + a timestamp, using this secret.
-- The secret is shown ONCE at registration and stored encrypted; there is no "reveal", only "rotate".
CREATE TABLE IF NOT EXISTS core.vision_edge_agent (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id           UUID NOT NULL,
  agent_key        TEXT NOT NULL,          -- public id the analyzer sends in the X-Vision-Agent header
  label            TEXT,
  store_code       TEXT,                   -- an agent may be pinned to one store (recommended)
  secret_enc       TEXT NOT NULL,          -- enc:v1:…  HMAC signing secret
  enabled          BOOLEAN NOT NULL DEFAULT true,
  version          TEXT,
  last_seen_at     TIMESTAMPTZ,
  last_ingest_at   TIMESTAMPTZ,
  events_received  BIGINT NOT NULL DEFAULT 0,
  rotated_at       TIMESTAMPTZ,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (org_id, agent_key)
);

-- Every live-stream grant this backend issues, for audit. "Who watched which camera, when, and for
-- how long" is a question an employee is entitled to have answered, so it is recorded by construction.
CREATE TABLE IF NOT EXISTS core.vision_stream_session (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id         UUID NOT NULL,
  camera_id      UUID,
  device_name    TEXT,
  store_code     TEXT,
  protocol       TEXT,                    -- webrtc | rtsp
  viewer_email   TEXT,
  viewer_role    TEXT,
  purpose        TEXT,                    -- live_view | analyzer
  extension_token_enc TEXT,               -- enc:v1:… Google's stream extension token
  issued_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at     TIMESTAMPTZ,
  extended_count INT NOT NULL DEFAULT 0,
  stopped_at     TIMESTAMPTZ,
  ip             TEXT
);
CREATE INDEX IF NOT EXISTS vision_stream_session_org ON core.vision_stream_session (org_id, issued_at DESC);

-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- 4. CUSTOMER TRAFFIC + HEAT MAP  (anonymous counts only)
-- ══════════════════════════════════════════════════════════════════════════════════════════════

-- One row per entrance-line crossing. direction 'in' / 'out'. `track_key` is the analyzer's local,
-- short-lived tracking id — it exists only to pair an entry with its exit inside one session and is
-- meaningless across cameras or days. It is NOT an identity.
CREATE TABLE IF NOT EXISTS core.vision_traffic_event (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id      UUID NOT NULL,
  store_code  TEXT NOT NULL,
  camera_id   UUID,
  occurred_at TIMESTAMPTZ NOT NULL,
  local_date  DATE NOT NULL,        -- the store's local business date (analyzer sends it; tz-correct)
  local_hour  SMALLINT NOT NULL,
  direction   TEXT NOT NULL,        -- in | out
  track_key   TEXT,
  confidence  REAL,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS vision_traffic_day ON core.vision_traffic_event (org_id, store_code, local_date);
-- Makes a REPLAYED ingest batch harmless. The edge analyzer re-queues a batch whose POST failed
-- (a flaky store uplink is the normal case, not the exception), so the same crossing can legitimately
-- arrive twice; ingest upserts against this index with ignore_duplicates so the retry is a no-op
-- instead of either double-counting the door or failing forever on a conflict.
-- Deliberately NOT a partial index: Postgres already treats NULLs as distinct, so a `WHERE track_key
-- IS NOT NULL` predicate would change nothing about which rows collide — but it WOULD make the index
-- un-inferrable through PostgREST's on_conflict parameter, which is the whole point of having it.
CREATE UNIQUE INDEX IF NOT EXISTS vision_traffic_dedupe
  ON core.vision_traffic_event (org_id, store_code, camera_id, track_key, direction, occurred_at);

-- An anonymous visit: one person-track from the moment they crossed in to the moment they crossed out.
-- `served_by_employee_id` is filled only when the analyzer can associate the track with a staff
-- interaction (a transcript segment at the same time on the same store), and is a coaching link, not
-- an identity claim about the customer.
CREATE TABLE IF NOT EXISTS core.vision_visit (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id         UUID NOT NULL,
  store_code     TEXT NOT NULL,
  local_date     DATE NOT NULL,
  entered_at     TIMESTAMPTZ NOT NULL,
  exited_at      TIMESTAMPTZ,
  dwell_seconds  INT,
  track_key      TEXT,
  zones_visited  JSONB NOT NULL DEFAULT '[]'::jsonb,   -- [{"zone_key":"counter","seconds":140}, …]
  greeted_within_seconds INT,                          -- NULL = never greeted (from the audio path)
  served_by_employee_id  UUID,
  converted      BOOLEAN,                              -- set by the POS/CRM join, not by the camera
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (org_id, store_code, track_key, entered_at)
);
CREATE INDEX IF NOT EXISTS vision_visit_day ON core.vision_visit (org_id, store_code, local_date);

-- Per-sample occupancy. The analyzer bins each detected person's floor point into a grid cell once
-- per sample window and posts the counts. Short retention — this is the fuel for the aggregate below,
-- not a report anyone reads directly.
CREATE TABLE IF NOT EXISTS core.vision_presence_sample (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id      UUID NOT NULL,
  store_code  TEXT NOT NULL,
  camera_id   UUID,
  sampled_at  TIMESTAMPTZ NOT NULL,
  local_date  DATE NOT NULL,
  local_hour  SMALLINT NOT NULL,
  cell_x      SMALLINT NOT NULL,
  cell_y      SMALLINT NOT NULL,
  occupancy   REAL NOT NULL DEFAULT 0,   -- person-seconds observed in this cell in the window
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS vision_presence_day ON core.vision_presence_sample (org_id, store_code, local_date);

-- The rolled-up heat map a manager actually opens: person-seconds per cell per store per date-hour.
-- Upserted by the aggregator, so re-running it is idempotent.
CREATE TABLE IF NOT EXISTS core.vision_heat_cell (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id       UUID NOT NULL,
  store_code   TEXT NOT NULL,
  camera_id    UUID,
  local_date   DATE NOT NULL,
  local_hour   SMALLINT NOT NULL,
  cell_x       SMALLINT NOT NULL,
  cell_y       SMALLINT NOT NULL,
  grid_cols    SMALLINT NOT NULL,
  grid_rows    SMALLINT NOT NULL,
  occupancy    REAL NOT NULL DEFAULT 0,
  samples      INT NOT NULL DEFAULT 0,
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (org_id, store_code, camera_id, local_date, local_hour, cell_x, cell_y)
);
CREATE INDEX IF NOT EXISTS vision_heat_day ON core.vision_heat_cell (org_id, store_code, local_date);

-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- 5. EMPLOYEE BEHAVIOR FROM VOICE TRANSCRIPTS  (consent-gated, redacted, employee-only)
-- ══════════════════════════════════════════════════════════════════════════════════════════════

-- Per-employee consent for the audio path. Deliberately NOT the face module's "assumed on enable":
-- a voice recording in a two-party-consent state needs a real, dated, per-person record, so the only
-- ways to reach 'signed' are an explicit employee action or an HR-recorded release.
CREATE TABLE IF NOT EXISTS core.vision_consent (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id        UUID NOT NULL,
  employee_id   UUID NOT NULL,
  status        TEXT NOT NULL DEFAULT 'pending',   -- pending | signed | declined | withdrawn
  scope         TEXT NOT NULL DEFAULT 'audio',     -- audio | video_analytics
  signed_at     TIMESTAMPTZ,
  withdrawn_at  TIMESTAMPTZ,
  source        TEXT,                              -- self_service | hr_recorded
  recorded_by   TEXT,
  document_url  TEXT,
  note          TEXT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (org_id, employee_id, scope)
);

-- One redacted transcript segment of ONE consenting employee's speech. `text` has already been through
-- the redactor at ingest (phone numbers, emails, card-shaped digit runs, SSN-shaped runs replaced), and
-- no audio is retained anywhere. `speaker` is 'employee' or 'other'; segments attributed to 'other'
-- (i.e. the customer) are DROPPED at ingest and never reach this table — see ingest.py.
CREATE TABLE IF NOT EXISTS core.vision_transcript (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id        UUID NOT NULL,
  store_code    TEXT NOT NULL,
  employee_id   UUID,
  camera_id     UUID,
  started_at    TIMESTAMPTZ NOT NULL,
  ended_at      TIMESTAMPTZ,
  local_date    DATE NOT NULL,
  duration_s    REAL,
  speaker       TEXT NOT NULL DEFAULT 'employee',
  text          TEXT NOT NULL DEFAULT '',
  language      TEXT DEFAULT 'en',
  asr_confidence REAL,
  redactions    INT NOT NULL DEFAULT 0,
  signals       JSONB NOT NULL DEFAULT '{}'::jsonb,  -- the rubric hits found in this segment
  visit_id      UUID,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS vision_transcript_day ON core.vision_transcript (org_id, store_code, local_date);
CREATE INDEX IF NOT EXISTS vision_transcript_emp ON core.vision_transcript (org_id, employee_id, local_date);

-- The tenant's coaching rubric. RULE TWO: which behaviors count, what phrases evidence them, and how
-- much each is worth are ALL operator-editable rows, not constants in Python. The backend seeds these
-- defaults so the module scores something sensible on day one.
CREATE TABLE IF NOT EXISTS core.vision_behavior_rule (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id       UUID NOT NULL,
  rule_key     TEXT NOT NULL,        -- greeting | discovery | needs_probe | pitch_* | close | thanks
  label        TEXT NOT NULL,
  category     TEXT NOT NULL DEFAULT 'sales',   -- sales | service | compliance
  phrases      JSONB NOT NULL DEFAULT '[]'::jsonb,  -- ["welcome to", "how can i help", …] lowercased
  weight       REAL NOT NULL DEFAULT 10,
  polarity     TEXT NOT NULL DEFAULT 'positive',    -- positive | negative (negative subtracts)
  window_s     INT,                  -- e.g. greeting only counts inside N seconds of the visit start
  is_active    BOOLEAN NOT NULL DEFAULT true,
  sort_order   INT NOT NULL DEFAULT 100,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (org_id, rule_key)
);

-- One scored row per employee per store per local date.
CREATE TABLE IF NOT EXISTS core.vision_behavior_score (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id            UUID NOT NULL,
  store_code        TEXT NOT NULL,
  employee_id       UUID NOT NULL,
  local_date        DATE NOT NULL,
  segments          INT NOT NULL DEFAULT 0,
  talk_seconds      REAL NOT NULL DEFAULT 0,
  interactions      INT NOT NULL DEFAULT 0,     -- distinct visits this employee spoke during
  greeted           INT NOT NULL DEFAULT 0,
  missed_greetings  INT NOT NULL DEFAULT 0,
  score             REAL NOT NULL DEFAULT 0,    -- 0..100 after normalisation
  rule_hits         JSONB NOT NULL DEFAULT '{}'::jsonb,   -- {"greeting": 12, "close": 3, …}
  coaching          JSONB NOT NULL DEFAULT '[]'::jsonb,   -- the ranked "work on this" list
  source            TEXT NOT NULL DEFAULT 'rules',        -- rules | llm
  computed_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (org_id, store_code, employee_id, local_date)
);
CREATE INDEX IF NOT EXISTS vision_score_day ON core.vision_behavior_score (org_id, store_code, local_date);

-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- 6. AUDIT
-- ══════════════════════════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS core.vision_audit (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id      UUID NOT NULL,
  actor       TEXT,
  action      TEXT NOT NULL,     -- enable | disable | stream_issued | consent_signed | purge | …
  target      TEXT,
  detail      JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS vision_audit_org ON core.vision_audit (org_id, created_at DESC);

-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- 7. MODULE CATALOG — makes 'vision' selectable in tenant entitlements / billing pickers
-- ══════════════════════════════════════════════════════════════════════════════════════════════
INSERT INTO core.module_catalog (key, label, sort_order) VALUES
  ('vision', 'Vision / Store Camera Analytics', 140)
ON CONFLICT (key) DO NOTHING;

-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- 8. SECURITY — RLS on, zero policies, zero anon/authenticated grants (AGENT_CONTRACT §5)
-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- The backend service role bypasses RLS and needs no policy; the frontend anon key is auth-only and
-- must never reach these tables; the edge analyzer authenticates with a per-agent HMAC secret, not the
-- anon key, so it needs no policy either.
DO $$
DECLARE t RECORD;
BEGIN
  FOR t IN SELECT tablename FROM pg_tables
           WHERE schemaname = 'core' AND tablename LIKE 'vision\_%' LOOP
    EXECUTE format('ALTER TABLE core.%I ENABLE ROW LEVEL SECURITY', t.tablename);
    EXECUTE format('REVOKE ALL ON core.%I FROM anon, authenticated', t.tablename);
    EXECUTE format('GRANT ALL ON core.%I TO service_role', t.tablename);
  END LOOP;
END $$;

NOTIFY pgrst, 'reload schema';

COMMIT;
