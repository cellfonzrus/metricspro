"""Proof harness for the vision enablement gates (mod-vision, migration 900).

Run: python3 backend/harness_vision_gate.py   (no network, no DB — an in-memory fake client)

The module can watch a live camera in a store and transcribe what an employee says. The whole
defensibility of that rests on the gate table in app/modules/vision/config.py, so this proves it:

  1. Every tenant is OFF by default, and OFF collapses every sub-switch — no lower-level value
     re-opens a gate a higher level shut.
  2. Pre-migration (table absent) resolves OFF and available=False — the two are told apart.
  3. The deployment-level audio kill switch beats a tenant that has audio switched ON.
  4. Consent: a signed employee is allowed; missing consent is refused; a DECLINED or WITHDRAWN
     employee is refused EVEN under audio_consent_mode='off'.
  5. Per-camera switches AND onto the tenant answer, and traffic needs an entrance camera.
  6. Retention cutoffs are computed from the tenant's own day counts, and 0 days disables the purge.
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.modules.vision import config as C   # noqa: E402

PASS, FAIL = [], []


def check(label, cond):
    (PASS if cond else FAIL).append(label)
    print(("  ok   " if cond else "  FAIL ") + label)


# ── an in-memory stand-in for the Supabase client, deliberately schemaless ────────────────────────
class _Q:
    def __init__(self, rows):
        self._rows = rows

    def select(self, *_a, **_k):
        return self

    def eq(self, *_a):
        return self

    def limit(self, *_a):
        return self

    def execute(self):
        return type("R", (), {"data": list(self._rows)})()


class _Schema:
    def __init__(self, tables):
        self._t = tables

    def table(self, name):
        if name not in self._t:
            raise RuntimeError(f"relation core.{name} does not exist")   # migration 900 not run
        return _Q(self._t[name])


class FakeClient:
    def __init__(self, tables):
        self._t = tables

    def schema(self, _name):
        return _Schema(self._t)


ORG = "org-1"


def cfg_for(row=None, audio_kill=True):
    C.AUDIO_GLOBALLY_DISABLED = audio_kill
    tables = {"vision_config": [row] if row else []}
    return C.resolve_config(FakeClient(tables), ORG)


print("\n(1) Default state — every tenant is OFF, and OFF collapses the sub-switches")
c = cfg_for(None)
check("no config row  -> enabled False", c["enabled"] is False)
check("no config row  -> available True (table exists, tenant simply has not opted in)", c["available"])
check("master off collapses live_view", c["live_view_enabled"] is False)
check("master off collapses traffic", c["traffic_enabled"] is False)
check("master off collapses heatmap", c["heatmap_enabled"] is False)
check("feature_enabled('live_view') False", C.feature_enabled(c, "live_view") is False)

# A row that says every sub-switch is on but the master is off must still be OFF everywhere.
c = cfg_for({"enabled": False, "live_view_enabled": True, "traffic_enabled": True,
             "heatmap_enabled": True, "audio_analytics_enabled": True,
             "behavior_scoring_enabled": True})
check("sub-switches cannot re-open a closed master (live_view)", C.feature_enabled(c, "live_view") is False)
check("sub-switches cannot re-open a closed master (audio)", C.feature_enabled(c, "audio_analytics") is False)

print("\n(2) Pre-migration degrades CLOSED, and is distinguishable from 'turned off'")
C.AUDIO_GLOBALLY_DISABLED = True
c = C.resolve_config(FakeClient({}), ORG)          # table missing entirely
check("missing table -> enabled False", c["enabled"] is False)
check("missing table -> available False (so the UI can say 'not installed')", c["available"] is False)
check("missing table -> full default set still present", set(C.DEFAULT_CONFIG) <= set(c))

print("\n(3) The deployment audio kill switch beats a tenant that turned audio ON")
row = {"enabled": True, "audio_analytics_enabled": True, "behavior_scoring_enabled": True,
       "live_view_enabled": True, "heatmap_enabled": True, "traffic_enabled": True}
c = cfg_for(row, audio_kill=True)
check("kill switch on  -> audio_analytics forced False", c["audio_analytics_enabled"] is False)
check("kill switch on  -> behavior_scoring forced False", c["behavior_scoring_enabled"] is False)
check("kill switch on  -> heatmap UNAFFECTED (video path is separate)", c["heatmap_enabled"] is True)
check("kill switch on  -> live_view UNAFFECTED", c["live_view_enabled"] is True)
check("kill switch reported to the caller", c["audio_kill_switch"] is True)

c = cfg_for(row, audio_kill=False)
check("kill switch off -> tenant's audio switch is honoured", c["audio_analytics_enabled"] is True)

print("\n(4) Consent — a recorded refusal is absolute")
c_on = cfg_for(row, audio_kill=False)                    # tenant audio ON, kill switch off
allowed, why = C.consent_ok(c_on, {"status": "signed"})
check("signed        -> allowed", allowed and why == "consent_signed")
allowed, why = C.consent_ok(c_on, None)
check("no record     -> refused (consent_missing)", not allowed and why == "consent_missing")
allowed, why = C.consent_ok(c_on, {"status": "declined"})
check("declined      -> refused", not allowed and why == "consent_declined")
allowed, why = C.consent_ok(c_on, {"status": "withdrawn"})
check("withdrawn     -> refused", not allowed and why == "consent_withdrawn")

c_off = cfg_for({**row, "audio_consent_mode": "off"}, audio_kill=False)
allowed, why = C.consent_ok(c_off, None)
check("consent_mode 'off' + no record -> allowed (operator holds their own release)",
      allowed and why == "consent_mode_off")
allowed, why = C.consent_ok(c_off, {"status": "declined"})
check("consent_mode 'off' does NOT override a DECLINE", not allowed and why == "consent_declined")
allowed, why = C.consent_ok(c_off, {"status": "withdrawn"})
check("consent_mode 'off' does NOT override a WITHDRAWAL", not allowed and why == "consent_withdrawn")

c_kill = cfg_for({**row, "audio_consent_mode": "off"}, audio_kill=True)
allowed, why = C.consent_ok(c_kill, {"status": "signed"})
check("kill switch beats even a signed consent", not allowed and why == "audio_kill_switch")

print("\n(5) Per-camera switches AND onto the tenant answer")
C.AUDIO_GLOBALLY_DISABLED = False
c = cfg_for(row, audio_kill=False)
entrance = {"enabled": True, "analytics_enabled": True, "is_entrance": True,
            "audio_enabled": True, "supports_audio": True}
check("entrance camera counts traffic", C.camera_allows(c, entrance, "traffic") is True)
check("non-entrance camera counts NO traffic",
      C.camera_allows(c, {**entrance, "is_entrance": False}, "traffic") is False)
check("analytics-off camera contributes no heatmap",
      C.camera_allows(c, {**entrance, "analytics_enabled": False}, "heatmap") is False)
check("analytics-off camera can still be watched live",
      C.camera_allows(c, {**entrance, "analytics_enabled": False}, "live_view") is True)
check("disabled camera is invisible to every path",
      C.camera_allows(c, {**entrance, "enabled": False}, "live_view") is False)
check("audio needs the camera's own switch",
      C.camera_allows(c, {**entrance, "audio_enabled": False}, "audio_analytics") is False)
check("audio needs the hardware to have a mic",
      C.camera_allows(c, {**entrance, "supports_audio": False}, "audio_analytics") is False)
check("audio allowed when tenant + camera + hardware all agree",
      C.camera_allows(c, entrance, "audio_analytics") is True)

print("\n(6) Retention cutoffs come from the tenant's own numbers")
now = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)
c = cfg_for({**row, "transcript_retention_days": 14, "presence_retention_days": 0})
cuts = C.retention_cutoffs(c, now=now)
check("transcript cutoff is now - 14d",
      cuts["transcript"] == (now - timedelta(days=14)).isoformat())
check("0 days disables that purge entirely (operator opted into keeping it)",
      cuts["presence"] is None)
check("a default is used when the column is absent/garbage",
      C.retention_cutoffs({"heat_retention_days": "oops"}, now=now)["heat"] ==
      (now - timedelta(days=C.DEFAULT_CONFIG["heat_retention_days"])).isoformat())

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
for f in FAIL:
    print("  FAILED: " + f)
sys.exit(1 if FAIL else 0)
