-- 907 · Vision — Google's OWN person events
--
-- WHY THIS EXISTS
--
-- Nest cameras already run person detection, on Google's side, and the Smart Device Management API
-- will push a message every time one fires. We were ignoring all of it: google_sdm.py asked for the
-- CameraLiveStream trait and nothing else, so the platform pulled raw video across the internet and
-- re-derived, at 45 ms a frame, something the camera had already worked out for free.
--
-- What Google's events give, and what they do NOT:
--
--   they DO   say "the front camera saw a person at 14:02", for every camera, at no compute cost,
--             no bandwidth cost and no edge analyzer — which is enough for BUSY HOURS and for
--             activity levels per store.
--   they DON'T say which WAY the person walked. Footfall means people coming IN; a customer
--             leaving and a member of staff crossing the doorway produce an identical event. Nor do
--             they say how many people, or where they stood, or for how long.
--
-- So this table is deliberately NOT a replacement for vision_traffic_event. Directional counting
-- and the heat map still come from the analyzer, on one or two cameras per store. This covers every
-- other camera, free, and is what makes "one analyzer for the whole estate" a sane target instead of
-- a hardware problem.
--
-- PRIVACY. An event is a device name and a timestamp. Google also offers an image download per
-- event; we deliberately do not store one, do not fetch one, and there is no column here to put one
-- in. The migration 900 header explains why that is a design constraint rather than an aspiration.
--
-- Safe to re-run.

-- ── The per-tenant switch ───────────────────────────────────────────────────────────────────────
-- Defaults TRUE like traffic and heatmap, and like them it is inert until the company master switch
-- (vision_config.enabled, default FALSE) is on. config.DEFAULT_CONFIG derives its column list from
-- its own keys, so adding the column here and the key there is the whole wiring.
ALTER TABLE core.vision_config
  ADD COLUMN IF NOT EXISTS google_events_enabled BOOLEAN NOT NULL DEFAULT true;

-- ── The events ──────────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS core.vision_camera_event (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id          UUID NOT NULL,
  camera_id       UUID,                    -- resolved from device_name; NULL if the camera was removed
  device_name     TEXT NOT NULL,           -- enterprises/<project>/devices/<id>, as Google sends it
  store_code      TEXT,                    -- denormalised at write time: reports group by store

  event_type      TEXT NOT NULL,           -- person | motion | sound | chime
  occurred_at     TIMESTAMPTZ NOT NULL,    -- Google's own timestamp, not our receive time

  -- The STORE's local date and hour, resolved at write time from that store's timezone. Busy hours
  -- is a local-time question and a chain spanning zones cannot answer it from UTC after the fact.
  local_date      DATE NOT NULL,
  local_hour      SMALLINT NOT NULL CHECK (local_hour BETWEEN 0 AND 23),

  -- Google's event id. Pub/Sub guarantees AT LEAST ONCE delivery, so a redelivered message is
  -- normal operation rather than an error, and the unique index below is what makes the endpoint
  -- idempotent. Non-partial on purpose: PostgREST cannot infer a partial index for ON CONFLICT,
  -- which is the same trap migration 900's ingest hit.
  google_event_id TEXT NOT NULL,
  received_at     TIMESTAMPTZ NOT NULL DEFAULT now(),

  UNIQUE (org_id, google_event_id)
);

-- The busy-hours read: one store, a date range, grouped by local hour.
CREATE INDEX IF NOT EXISTS idx_vision_camera_event_busy
  ON core.vision_camera_event (org_id, store_code, local_date, local_hour);

-- Retention sweeps and per-camera drill-down.
CREATE INDEX IF NOT EXISTS idx_vision_camera_event_camera
  ON core.vision_camera_event (org_id, camera_id, occurred_at DESC);

COMMENT ON TABLE core.vision_camera_event IS
  'Person/motion events pushed by Google SDM. Presence and timing only — no direction, no count, '
  'no image. Directional footfall and heat maps come from the edge analyzer.';
COMMENT ON COLUMN core.vision_camera_event.google_event_id IS
  'Google''s event id. Pub/Sub redelivers; the unique index on (org_id, google_event_id) is what '
  'makes the push endpoint idempotent.';
