-- 910 · Vision — employee activity: posture, movement, company, and floor coverage
--
-- OWNER DIRECTIVE 2026-08-22 (sanjot@), verbatim: "build the busy hours view , then the employee
-- behavior , sitting , yawning, not doing anything walking , talking to the customer etc whatever
-- we can pull from google". Asked which of three builds, the owner chose "everything on the list"
-- after being shown the accuracy, cost and Illinois/New York arguments against the face-state half.
-- That is a recorded decision, and this migration implements it — with the constraints below, which
-- are what make it implementable at all rather than a softening of the ask.
--
-- ══ WHAT GOOGLE CONTRIBUTES HERE: NOTHING ════════════════════════════════════════════════════════
-- The Smart Device Management API exposes CameraPerson, CameraMotion, CameraSound, DoorbellChime and
-- a per-event image. There is no posture, gaze, activity or identity in it. Every column below is
-- derived at the EDGE from frames the analyzer already decodes for the heat map, and those frames are
-- discarded there. Migration 907 (Google's events) is unaffected and remains the free, every-camera,
-- no-analyzer path; this is the opposite trade and the operator pays for it in compute per camera.
--
-- ══ THE LINE THIS MIGRATION DOES NOT CROSS ═══════════════════════════════════════════════════════
-- Migration 900's header commits the module to no video, no audio, NO FACE DESCRIPTORS and no
-- per-customer identity, and notes the platform already carries BIPA exposure through the kiosk face
-- path (docs/BIOMETRIC_RETENTION_POLICY.md). That commitment is kept here and it is the reason this
-- design looks the way it does:
--
--   * There is no face descriptor, no face template, no landmark set and no face crop in this
--     migration, and no column that could hold one. The face-state signal (below) is computed from
--     two distances on one frame at the edge and reduced to a COUNT before it is sent. A count of
--     episodes cannot match one face to another face; a geometry template can, and we store none.
--   * Because we do no face recognition, WE CANNOT TELL WHICH EMPLOYEE A TRACK IS. Attribution comes
--     from the time clock and only when it is unambiguous — see employee_id below. This is the
--     central design constraint of the whole feature, not a limitation to be engineered away later:
--     the engineering that would remove it is exactly the biometric collection we are declining.
--
-- ══ WHAT THE FACE-STATE SIGNAL IS AND IS NOT ═════════════════════════════════════════════════════
-- The owner asked for yawning. What is measurable is a SUSTAINED WIDE-MOUTH EPISODE: mouth opening
-- over width, held past a threshold for over a second. A yawn produces one. So does a laugh, a
-- shout across the shop floor, a deep breath, and a long vowel said loudly to a hard-of-hearing
-- customer. Nothing in the signal separates them and no threshold will, so the column is named
-- wide_mouth_episodes rather than yawns, the UI labels it the same way, and it is documented as a
-- prompt to go and look rather than a finding. Naming the column `yawns` would have made every
-- downstream reader treat a laugh as fatigue.
--
-- It gets its OWN switch (face_state_enabled, default FALSE) and its OWN consent scope
-- ('video_face'), separate from video analytics generally. A tenant can run posture and coverage
-- without ever turning it on, and an employee can consent to one and not the other. Bundling it into
-- the video switch would have meant the mildest signal and the most invasive one shared a checkbox.
--
-- ══ MONEY ════════════════════════════════════════════════════════════════════════════════════════
-- Touches NO payout, rate, plan or commission column. Same posture as vision_behavior_score: there is
-- no join path from anything here into a pay calculation, and adding one would need its own migration
-- and its own argument.
--
-- SECURITY: RLS on, zero policies, zero anon/authenticated grants (AGENT_CONTRACT §5). The backend
--           service role bypasses RLS; the edge analyzer authenticates with its per-agent HMAC secret.
-- SAFE: additive + idempotent. Re-runnable.

BEGIN;

CREATE SCHEMA IF NOT EXISTS core;

-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- 1. SWITCHES AND THRESHOLDS (RULE TWO — nothing about a tenant's program is hard-coded)
-- ══════════════════════════════════════════════════════════════════════════════════════════════

-- Both default FALSE, like audio_analytics_enabled and behavior_scoring_enabled before them: the
-- safe state and the default state are the same state, so the feature is off for every tenant the
-- moment the code deploys and turning it on is a deliberate, audited act.
ALTER TABLE core.vision_config
  ADD COLUMN IF NOT EXISTS activity_enabled BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE core.vision_config
  ADD COLUMN IF NOT EXISTS face_state_enabled BOOLEAN NOT NULL DEFAULT false;

-- Floor coverage — the one signal here with no per-person content at all (it says only "somebody was
-- on the floor" / "nobody was"). Defaults TRUE like traffic and heatmap, and like them it is inert
-- until the master switch is on. It is also the only one of the three that needs no consent, because
-- it names nobody and cannot be attributed to anybody.
ALTER TABLE core.vision_config
  ADD COLUMN IF NOT EXISTS coverage_enabled BOOLEAN NOT NULL DEFAULT true;

-- Consent policy for the video path, mirroring audio_consent_mode exactly. 'required' = an employee
-- with no signed row is never NAMED (their activity still rolls up unattributed, because the camera
-- cannot avoid seeing a person on the shop floor — what consent gates is putting a NAME on it).
-- 'off' exists only for a tenant holding its own recorded release and is audited when set.
ALTER TABLE core.vision_config
  ADD COLUMN IF NOT EXISTS video_consent_mode TEXT NOT NULL DEFAULT 'required';

-- Retention. Activity buckets are the most sensitive rows in the module, so they expire FASTEST of
-- anything except raw presence samples — 30 days, matching transcripts, which is long enough for a
-- monthly coaching conversation and too short to build a year-long dossier on somebody.
ALTER TABLE core.vision_config
  ADD COLUMN IF NOT EXISTS activity_retention_days INT NOT NULL DEFAULT 30;
-- Coverage carries no personal content, so it keeps the reporting horizon the heat map has.
ALTER TABLE core.vision_config
  ADD COLUMN IF NOT EXISTS coverage_retention_days INT NOT NULL DEFAULT 400;

-- Thresholds the operator tunes per tenant. Defaults mirror app/modules/vision/activity.py, which
-- documents what each one means and which way it fails.
ALTER TABLE core.vision_config
  ADD COLUMN IF NOT EXISTS activity_bucket_seconds INT NOT NULL DEFAULT 900;   -- 15-minute buckets
ALTER TABLE core.vision_config
  ADD COLUMN IF NOT EXISTS activity_sample_seconds REAL NOT NULL DEFAULT 2.0;  -- edge sampling rate
ALTER TABLE core.vision_config
  ADD COLUMN IF NOT EXISTS walk_speed REAL NOT NULL DEFAULT 0.05;  -- frame widths/sec above = walking
ALTER TABLE core.vision_config
  ADD COLUMN IF NOT EXISTS engage_distance REAL NOT NULL DEFAULT 0.12;  -- normalized "next to"
ALTER TABLE core.vision_config
  ADD COLUMN IF NOT EXISTS idle_after_seconds INT NOT NULL DEFAULT 120;  -- below this is just standing

-- POSTURE NEEDS AN EYE-LEVEL CAMERA, and getting this wrong is the loudest failure the feature has.
-- classify_posture() reads standing-vs-sitting out of image geometry: the thigh projects to roughly
-- the torso's length standing, and foreshortens towards zero seated. An OVERHEAD camera foreshortens
-- the standing thigh the same way, so it would report an entire store as seated all day. Default
-- FALSE per camera: an operator opts a camera in after looking at its picture, and a camera nobody
-- has marked produces NO posture rather than a confident wrong one.
ALTER TABLE core.vision_camera
  ADD COLUMN IF NOT EXISTS posture_capable BOOLEAN NOT NULL DEFAULT false;

-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- 2. PER-TRACK ACTIVITY
-- ══════════════════════════════════════════════════════════════════════════════════════════════

-- One row per TRACK per BUCKET. A "track" is the analyzer's anonymous, short-lived id for a blob it
-- is following — it does not survive an occlusion, it is not a person, and it is not stable across
-- buckets. That is deliberate: a stable per-person id derived from appearance is a re-identification
-- system, which is the thing we are not building.
CREATE TABLE IF NOT EXISTS core.vision_activity_bucket (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id        UUID NOT NULL,
  store_code    TEXT NOT NULL,
  camera_id     UUID,
  track_key     TEXT NOT NULL,          -- the analyzer's anonymous track id, unique within a bucket

  bucket_start  TIMESTAMPTZ NOT NULL,
  local_date    DATE NOT NULL,
  local_hour    SMALLINT NOT NULL CHECK (local_hour BETWEEN 0 AND 23),

  -- ── ATTRIBUTION ────────────────────────────────────────────────────────────────────────────────
  -- NULL is the NORMAL, EXPECTED value and most rows in a multi-staff store will carry it.
  --
  -- We do no face recognition, so we cannot tell which person a track is. The name comes from the
  -- TIME CLOCK and only when exactly ONE employee was clocked in at that store for that bucket AND
  -- has signed video consent. With two people on shift the honest statement about a sitting track is
  -- "one of these two was sitting", which is not a fact about either of them — so no name is stored.
  --
  -- attribution_reason records WHY, so the UI can print "unattributed — 3 on shift" rather than an
  -- unexplained blank, and so an operator can see that their two-person stores produce no per-person
  -- data at all before they build a process on top of it. app/modules/vision/activity.py
  -- attribute_bucket() is the only writer and harness_vision_activity.py proves it never names
  -- anybody when the candidate set is bigger than one.
  employee_id       UUID,
  attribution_reason TEXT NOT NULL DEFAULT 'nobody_on_shift',
      -- single_on_shift | nobody_on_shift | multiple_on_shift | consent_missing

  -- ── WHAT WAS OBSERVED ──────────────────────────────────────────────────────────────────────────
  -- seconds_observed is the DENOMINATOR and it counts unknown time too. Every reader must divide by
  -- it rather than by the sum of the confident categories: a rep readable for 4 minutes of an hour
  -- would otherwise show as "62% standing" with nothing to say the hour was mostly guesswork.
  samples                     INT  NOT NULL DEFAULT 0,
  seconds_observed            REAL NOT NULL DEFAULT 0,

  -- Posture. The unknown column is not a rounding error — behind a counter, far from the lens, or
  -- half-turned all land there, and in a real phone store it is routinely the largest of the three.
  seconds_standing            REAL NOT NULL DEFAULT 0,
  seconds_sitting             REAL NOT NULL DEFAULT 0,
  seconds_posture_unknown     REAL NOT NULL DEFAULT 0,

  seconds_walking             REAL NOT NULL DEFAULT 0,
  seconds_stationary          REAL NOT NULL DEFAULT 0,
  seconds_motion_unknown      REAL NOT NULL DEFAULT 0,

  -- NOT "serving a customer". The detector has ONE class, `person`, so this is two people standing
  -- close together: a rep with a customer, two reps talking, or a couple browsing. The column name
  -- says what is measured and the UI repeats the caveat. The reliable version of "was the rep
  -- engaging the customer" is the transcript path, which reads what was actually said.
  seconds_with_another_person REAL NOT NULL DEFAULT 0,

  -- See the header. A yawn produces one of these; so does a laugh, a shout, or a deep breath. NULL
  -- (not 0) when face_state_enabled is off, so "we did not look" is distinguishable from "we looked
  -- and saw none" — a zero in a column nobody was populating is a lie a manager would act on.
  wide_mouth_episodes         INT,

  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),

  -- The analyzer retries a failed post, and a retried batch must not double-count a shift.
  UNIQUE (org_id, camera_id, bucket_start, track_key)
);

CREATE INDEX IF NOT EXISTS vision_activity_store_day
  ON core.vision_activity_bucket (org_id, store_code, local_date, local_hour);
-- The per-employee read. Partial: rows with no name are the majority and never match this query.
CREATE INDEX IF NOT EXISTS vision_activity_employee
  ON core.vision_activity_bucket (org_id, employee_id, local_date)
  WHERE employee_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS vision_activity_retention
  ON core.vision_activity_bucket (org_id, bucket_start);

-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- 3. FLOOR COVERAGE — the store-level signal, which names nobody
-- ══════════════════════════════════════════════════════════════════════════════════════════════

-- Was there anybody on the floor to serve, and was anyone waiting when there wasn't. This needs no
-- pose model, no face, and no attribution — it falls straight out of the tracks the analyzer already
-- computes for the heat map — and it is the number a manager can act on with none of the caveats
-- that hang off the per-person columns above.
CREATE TABLE IF NOT EXISTS core.vision_coverage_bucket (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id        UUID NOT NULL,
  store_code    TEXT NOT NULL,
  camera_id     UUID,

  bucket_start  TIMESTAMPTZ NOT NULL,
  local_date    DATE NOT NULL,
  local_hour    SMALLINT NOT NULL CHECK (local_hour BETWEEN 0 AND 23),

  window_seconds            REAL NOT NULL DEFAULT 0,
  staffed_seconds           REAL NOT NULL DEFAULT 0,
  unstaffed_seconds         REAL NOT NULL DEFAULT 0,
  -- The one that matters: floor with nobody serving and somebody waiting.
  unstaffed_with_customers  REAL NOT NULL DEFAULT 0,
  -- How many people were on the floor at the busiest moment of the window — context for the above,
  -- so "unstaffed" during a closed hour is not read the same as during a rush.
  peak_people               INT  NOT NULL DEFAULT 0,

  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (org_id, camera_id, bucket_start)
);

CREATE INDEX IF NOT EXISTS vision_coverage_store_day
  ON core.vision_coverage_bucket (org_id, store_code, local_date, local_hour);
CREATE INDEX IF NOT EXISTS vision_coverage_retention
  ON core.vision_coverage_bucket (org_id, bucket_start);

-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- 4. RLS — on, with no policies and no grants (AGENT_CONTRACT §5)
-- ══════════════════════════════════════════════════════════════════════════════════════════════
ALTER TABLE core.vision_activity_bucket ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.vision_coverage_bucket ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON core.vision_activity_bucket FROM anon, authenticated;
REVOKE ALL ON core.vision_coverage_bucket FROM anon, authenticated;

COMMENT ON TABLE core.vision_activity_bucket IS
  'Per-track posture/movement/company over a time bucket, derived at the edge. NOT per-person '
  'unless the time clock made it unambiguous — see employee_id. No images, no landmarks, no face '
  'descriptors, here or at the edge.';
COMMENT ON COLUMN core.vision_activity_bucket.employee_id IS
  'NULL is normal. Set only when exactly one consenting employee was clocked in for the bucket; we '
  'do no face recognition, so there is no other way to know who a track is, and guessing would put '
  'a coin flip into a coaching conversation. attribution_reason says why it is NULL.';
COMMENT ON COLUMN core.vision_activity_bucket.wide_mouth_episodes IS
  'Sustained wide-mouth episodes. A yawn makes one; so does a laugh, a shout, or a deep breath. A '
  'prompt to go and look, never a finding, and never a tiredness score. NULL means face state was '
  'switched off for this bucket, which is not the same as zero.';
COMMENT ON COLUMN core.vision_activity_bucket.seconds_with_another_person IS
  'Two people close together — a rep with a customer, two reps talking, or a browsing couple. The '
  'detector has one class, person, so it cannot tell these apart. For genuine customer engagement '
  'use the transcript path, which reads what was actually said.';
COMMENT ON COLUMN core.vision_activity_bucket.seconds_observed IS
  'The denominator, and it INCLUDES the unknown columns. Divide by this, never by the sum of the '
  'confident categories, or a barely-observed hour reads as a confident one.';
COMMENT ON TABLE core.vision_coverage_bucket IS
  'Store-level floor coverage. Names nobody, needs no pose model and no consent. '
  'unstaffed_with_customers is the actionable number.';
COMMENT ON COLUMN core.vision_camera.posture_capable IS
  'Posture is read from image geometry and assumes an eye-level camera; an overhead camera '
  'foreshortens a standing thigh exactly as sitting does and would report a whole store as seated. '
  'Default false — an unmarked camera produces no posture rather than a wrong one.';

COMMIT;
