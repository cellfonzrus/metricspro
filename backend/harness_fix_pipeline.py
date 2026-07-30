"""Proof harness for the AUTO-FIX PIPELINE Phase 1 package (mig 718).

Runs the ACTUAL shipped handlers/helpers against a stateful fake Supabase client — no DB, no network —
plus a REAL ASGI HTTP smoke through app.main (so the /api/v1 last mile and the tenant middleware are
exercised, not assumed). Run from backend/:

    python3 harness_fix_pipeline.py

Proves:
  A. SIGNATURE / DEDUPE (pure)
     A1-A6  normalize_path collapses uuid / numeric / hex-ref / YYYY-MM[-DD] / 'July 2026' segments and
            keeps the method token; two different ids on the same route share ONE signature.
     A7-A9  signature_of_failure works for a mig-112 system_error row (detail.path/exc_type) AND for a
            non-HTTP failure kind (source + category) — one universal rule, no per-kind branch.
     A10-13 build_feed folds rows into signature candidates with counts / severity / affected_orgs /
            traceback sample, and DROPS any signature that already has a fix_request (§3.5 no duplicate
            builds), counting them in skipped_already_registered.
  B. STATUS MACHINE (pure) — the heart of Phase 1
     B1     every legal transition in FIX_TRANSITIONS is accepted;
     B2     EVERY illegal pair is refused (exhaustive over statuses x statuses);
     B3     'pushed' is refused from every status except 'approved' — including a direct
            reported → pushed jump;
     B4     'pushed' from 'approved' WITHOUT the approval audit trail is refused;
     B5     'approved' and 'pushed' are refused to a non-super-admin (the service secret can never
            approve or push);
     B6     a money_touching row cannot be advanced into a build/park/approval by AUTOMATION;
     B7     'pushed' is terminal; rejected/not_code may be reopened.
  C. COST ACCOUNTING (pure, §2e)
     C1-C3  blended_rate honours per-model output_share and clamps it; rate_for picks the newest
            effective_date <= today, ignores FUTURE and inactive rows, and prefers a tenant row over house;
     C4-C6  compute_cost is arithmetically right, and with NO matching rate returns cost None + a reason
            (never a fabricated fallback rate — the whole point of RULE TWO here);
     C7     rollup sums fixes/tokens/$ and counts unpriced rows separately.
  D. ENDPOINTS (fake client)
     D1     GET /feed performs ZERO writes (the fake raises on any write verb) and excludes registered
            signatures;
     D2     POST /requests STAMPS org_id on the INSERT (RULE ONE write side) and starts in a pre-build
            status even when the caller asks for 'approved'/'pushed';
     D3     a second POST for the SAME signature DEDUPES (no second row) and bumps occurrence_count +
            unions failure_ids;
     D4     PATCH walks the lifecycle, appends an audit entry EVERY time, and re-prices from token_rates;
     D5     the secret door cannot set 'approved' (403) — a super-admin can, and gets approved_by/at;
     D6     'pushed' without 'approved' is refused end-to-end through the handler;
     D7     reads are ORG-SCOPED: a request filed under tenant A is invisible when scoped to tenant B,
            and visible with the explicit all_orgs platform scope;
     D8     GET /requests/{id} returns the failure rows WITH detail.traceback (the design §2a gap);
     D9     the rollup on the board payload is computed over the returned rows.
  E. SECRET SCOPING (§2c / safety rail 4)
     E1     SECRET_CAPS is exactly {feed_read, registry_read, registry_write};
     E2     the secret is REFUSED for config_read/config_write (the rate table) — 403;
     E3     a wrong secret is 401; an UNSET server secret can never be matched (closed by default);
     E4     a valid secret never escalates: actor.super_admin stays False;
     E5     a browser caller who is NOT a platform super-admin gets 403 on the feed;
     E6     every route in the sub-router self-gates via _authorize (source-level coverage), so a future
            endpoint cannot silently ship ungated.
  F. MIDDLEWARE + ROUTING
     F1     _is_public allows /api/v1/core/fix-pipeline[/…] and NOT the pre-existing
            /api/v1/core/fix-requests (mig-716 support pipeline keeps full protection);
     F2     app.main exposes exactly the 7 new routes under /api/v1/core/fix-pipeline and resolves each
            to ITS OWN handler;
     F3     REAL ASGI: the secret on a NON-pipeline endpoint is rejected by the middleware (401) —
            it unlocks nothing outside the pipeline;
     F4     REAL ASGI: GET feed + POST/PATCH requests + GET token-rates over HTTP with the secret header
            behave exactly as the direct calls (incl. the 403 on the rate table).
  G. MIGRATION SQL SANITY (source assertions — the SQL is operator-run)
     G1     the push-gate trigger exists and requires the previous status to be approved + the audit
            stamps;
     G2     RLS enabled with ZERO anon/authenticated grants and ZERO policies (AGENT_CONTRACT §5);
     G3     both tables carry org_id NOT NULL + an index, and fix_requests is UNIQUE (org_id, signature);
     G4     the seeded rate INSERT has matching column/value arity on every row (the class of bug Gate 1
            caught on mig 715) and every rate row is dated;
     G5     the seed function is called from the entitlement sync path and SEED_VERSION was bumped.
  H. LOGIN / SEED PATH SAFETY (a SEED_VERSION bump is a change to the login path)
     H1-H3  SEED_VERSION is 7; the HOUSE sync pass calls core.seed_token_rates, a TENANT pass does not
            (rates are platform config, not per-tenant content);
     H4-H5  an UN-RUN mig 718 (the rpc raising) is a silent no-op — a login can never break on it — and
            the entitlement result is unchanged by the new seed step;
     H6-H7  needs_sync still reads the watermark, and NO new entitlement module was invented (the board
            is a platform surface, not a billable tenant module).
"""
import asyncio
import inspect
import os
import re
import sys
from types import SimpleNamespace

sys.path.insert(0, ".")
os.environ.setdefault("SUPABASE_URL", "https://example-harness.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "harness-dummy-service-key")
os.environ.setdefault("SUPABASE_ANON_KEY", "harness-dummy-anon-key")
# The pipeline package under test: enforcement ON (so the middleware really runs in the ASGI smoke) and a
# known service secret. Set BEFORE any app import so pydantic settings pick them up.
os.environ["MULTI_TENANT_ENFORCE"] = "1"
os.environ["FIX_PIPELINE_SECRET"] = "harness-fix-secret-1234567890"

import app.modules.core.router as core          # noqa: E402
import app.modules.core.fix_pipeline as fp      # noqa: E402
import app.core.tenant_middleware as tmw        # noqa: E402
from fastapi import HTTPException               # noqa: E402

SECRET = os.environ["FIX_PIPELINE_SECRET"]
PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS" if cond else "FAIL") + f"  {name}" + (f"  [{detail}]" if detail and not cond else ""))


HOUSE = "00000000-0000-0000-0000-000000000001"
TEN_A = "aaaaaaaa-0000-0000-0000-000000000001"
TEN_B = "bbbbbbbb-0000-0000-0000-000000000002"
_ID = {"n": 0}


def nid(pfx="id"):
    _ID["n"] += 1
    return f"{pfx}-{_ID['n']}"


# ══ fake supabase (same convention as harness_failure_triage.py) ══════════════════════════════════
class Q:
    def __init__(self, store, table, read_only=False):
        self.s, self.t, self.ro = store, table, read_only
        self.op, self.payload, self.on_conflict = "select", None, None
        self.filters = []

    def select(self, *a, **k): self.op = "select"; return self

    def _guard(self, verb):
        if self.ro:
            raise AssertionError(f"READ-ONLY VIOLATION: {verb} attempted on {self.t}")

    def insert(self, rows, **k): self._guard("insert"); self.op = "insert"; self.payload = rows; return self
    def update(self, patch, **k): self._guard("update"); self.op = "update"; self.payload = patch; return self

    def upsert(self, rows, on_conflict=None, **k):
        self._guard("upsert")
        self.op, self.payload, self.on_conflict = "upsert", rows, on_conflict
        return self

    def delete(self, **k): self._guard("delete"); self.op = "delete"; return self
    def eq(self, c, v): self.filters.append((c, "eq", v)); return self
    def in_(self, c, v): self.filters.append((c, "in", list(v))); return self
    def gte(self, c, v): self.filters.append((c, "gte", v)); return self
    def lte(self, c, v): self.filters.append((c, "lte", v)); return self
    def order(self, *a, **k): return self
    def limit(self, *a, **k): return self

    def _match(self, row):
        for c, kind, v in self.filters:
            rv = row.get(c)
            if kind == "eq" and rv != v: return False
            if kind == "in" and rv not in v: return False
            if kind == "gte" and not (rv is not None and str(rv) >= str(v)): return False
            if kind == "lte" and not (rv is not None and str(rv) <= str(v)): return False
        return True

    def execute(self):
        rows = self.s.setdefault(self.t, [])
        if self.op == "select":
            return SimpleNamespace(data=[dict(r) for r in rows if self._match(r)])
        if self.op == "insert":
            payload = self.payload if isinstance(self.payload, list) else [self.payload]
            out = []
            for r in payload:
                r = dict(r); r.setdefault("id", nid(self.t)); rows.append(r); out.append(dict(r))
            return SimpleNamespace(data=out)
        if self.op == "update":
            out = []
            for r in rows:
                if self._match(r):
                    r.update(self.payload); out.append(dict(r))
            return SimpleNamespace(data=out)
        if self.op == "upsert":
            payload = self.payload if isinstance(self.payload, list) else [self.payload]
            keys = [k.strip() for k in (self.on_conflict or "").split(",") if k.strip()]
            out = []
            for r in payload:
                r = dict(r); existing = None
                if keys:
                    for er in rows:
                        if all(er.get(k) == r.get(k) for k in keys):
                            existing = er; break
                if existing:
                    existing.update(r); out.append(dict(existing))
                else:
                    r.setdefault("id", nid(self.t)); rows.append(r); out.append(dict(r))
            return SimpleNamespace(data=out)
        if self.op == "delete":
            self.s[self.t] = [r for r in rows if not self._match(r)]
            return SimpleNamespace(data=[])
        return SimpleNamespace(data=[])


class FakeClient:
    """`read_only=True` makes EVERY write verb raise — used to prove the feed/board are read-only."""
    def __init__(self, store, read_only=False):
        self.store, self.ro = store, read_only

    def schema(self, _n): return self
    def table(self, name): return Q(self.store, name, self.ro)

    def rpc(self, name, params=None):
        return SimpleNamespace(execute=lambda: SimpleNamespace(data=[]))


def rate(model, rin, rout, eff="2026-01-01", org=HOUSE, share=0.20, active=True):
    return {"id": nid("rate"), "org_id": org, "model": model, "usd_per_mtok_in": rin,
            "usd_per_mtok_out": rout, "effective_date": eff, "output_share": share,
            "is_active": active, "label": model}


def flog(org, cat, *, path=None, exc=None, ref=None, tb=None, sev="error", reviewed=False,
         created="2026-07-30T10:00:00+00:00", msg="boom", source=None):
    detail = None
    if path or exc or ref or tb:
        detail = {"ref": ref, "method": "GET", "path": path, "exc_type": exc, "traceback": tb}
    return {"id": nid("flog"), "org_id": org, "category": cat, "severity": sev, "reviewed": reviewed,
            "created_at": created, "message": msg, "employee_name": None, "store_code": None,
            "status": "open", "detail": detail, "remediation": None,
            "source": source or (f"GET {path}" if path else "kiosk/clock-in")}


def fresh_store(**extra):
    st = {"app_users": [], "roles": [],
          "tenants": [{"org_id": HOUSE, "name": "House"}, {"org_id": TEN_A, "name": "Alpha"},
                      {"org_id": TEN_B, "name": "Bravo"}],
          "failure_log": [], "failure_kind_doc": [], "fix_requests": [],
          "token_rates": [rate("claude-opus-5", 5, 25)]}
    st.update(extra)
    return st


def wire(store, read_only=False):
    fake = FakeClient(store, read_only)
    fp.sb = lambda: fake
    core.get_supabase = lambda: fake
    core.sb = lambda: fake
    return fake


# Auth stubs: '' = no auth, 'Bearer super' = platform super-admin, 'Bearer tenant' = ordinary admin.
def stub_auth():
    core._uid_from_token = lambda auth: ({"Bearer super": "uid-super",
                                          "Bearer tenant": "uid-tenant"}.get(auth))

    def _req_super(authorization="", active_org=""):
        if authorization == "Bearer super":
            return {"org_id": HOUSE, "email": "owner@metricspro.tech", "role": "admin",
                    "super_admin": True}
        raise HTTPException(403, "super-admin only")
    core._require_super_admin = _req_super


stub_auth()
run = asyncio.run

print("\n══ A. SIGNATURE / DEDUPE (pure) ══")
check("A1 uuid segment → {id}",
      fp.normalize_path("/api/v1/core/fix-pipeline/requests/3f2504e0-4f89-11d3-9a0c-0305e82c3301")
      == "/api/v1/core/fix-pipeline/requests/{id}")
check("A2 numeric segment → {id}", fp.normalize_path("/api/v1/asset/charge-rows/4821")
      == "/api/v1/asset/charge-rows/{id}")
check("A3 hex ref segment → {id}", fp.normalize_path("/api/v1/core/failures/3bf51b4d")
      == "/api/v1/core/failures/{id}")
check("A4 human period 'July%202026' → {period}",
      fp.normalize_path("/api/v1/commcalc/team-snapshot/July%202026")
      == "/api/v1/commcalc/team-snapshot/{period}", fp.normalize_path("/api/v1/commcalc/team-snapshot/July%202026"))
check("A5 YYYY-MM / YYYY-MM-DD → {period}",
      fp.normalize_path("/x/2026-07") == "/x/{period}" and fp.normalize_path("/x/2026-07-30") == "/x/{period}")
check("A6a method token is preserved, query string dropped",
      fp.normalize_path("GET /api/v1/x/9?org_id=abc") == "GET /api/v1/x/{id}")
check("A6b two different ids on one route share ONE signature",
      fp.fix_signature("/api/v1/x/11", "KeyError") == fp.fix_signature("/api/v1/x/12", "KeyError"))
check("A6c a different exception on the same route is a DIFFERENT signature",
      fp.fix_signature("/api/v1/x/11", "KeyError") != fp.fix_signature("/api/v1/x/11", "ValueError"))
sig_sys, path_sys, exc_sys = fp.signature_of_failure(
    flog(TEN_A, "system_error", path="/api/v1/commcalc/team-snapshot/July%202026", exc="KeyError",
         ref="3bf51b4d", tb="Traceback (most recent call last):\n  KeyError: 'July 2026'"))
check("A7 system_error row → signature from detail.path + detail.exc_type",
      sig_sys == "GET /api/v1/commcalc/team-snapshot/{period}|KeyError", sig_sys)
sig_face, _, exc_face = fp.signature_of_failure(flog(TEN_A, "face_mismatch", source="kiosk/clock-in"))
check("A8 non-HTTP failure kind falls back to source + category (one universal rule)",
      sig_face == "kiosk/clock-in|face_mismatch", sig_face)
check("A9 blank-everything row degrades to 'unknown|unknown', never an empty collision key",
      fp.fix_signature("", "") == "unknown|unknown")

rows = [
    flog(TEN_A, "system_error", path="/api/v1/x/1", exc="KeyError", ref="r1", tb="TB-ONE"),
    flog(TEN_A, "system_error", path="/api/v1/x/2", exc="KeyError", ref="r2", sev="error",
         created="2026-07-30T12:00:00+00:00"),
    flog(TEN_B, "system_error", path="/api/v1/x/3", exc="KeyError", ref="r3", sev="warning"),
    flog(TEN_A, "system_error", path="/api/v1/y/1", exc="ValueError", ref="r4", sev="warning"),
]
cands, skipped = fp.build_feed(rows, [])
by_sig = {c["signature"]: c for c in cands}
kc = by_sig.get("GET /api/v1/x/{id}|KeyError")
check("A10 3 occurrences across 2 tenants fold into ONE candidate",
      kc is not None and kc["count"] == 3 and len(cands) == 2, [c["signature"] for c in cands])
check("A11 affected_orgs carries per-tenant counts",
      kc and sorted((o["org_id"], o["count"]) for o in kc["affected_orgs"]) == sorted([(TEN_A, 2), (TEN_B, 1)]))
check("A12 candidate carries latest_at, max severity, first ref and a sample traceback",
      kc and kc["latest_at"] == "2026-07-30T12:00:00+00:00" and kc["severity"] == "error"
      and kc["first_ref"] == "r1" and kc["sample_traceback"] == "TB-ONE")
cands2, skipped2 = fp.build_feed(rows, ["GET /api/v1/x/{id}|KeyError"])
check("A13 an ALREADY-REGISTERED signature is excluded and counted (no duplicate builds, §3.5)",
      len(cands2) == 1 and skipped2 == 3 and skipped == 0, (len(cands2), skipped2))

print("\n══ B. STATUS MACHINE (pure) ══")
legal = [(c, t) for c, ts in fp.FIX_TRANSITIONS.items() for t in ts]
bad_legal = [(c, t) for c, t in legal
             if not fp.pipeline_status_change(c, t, is_super_admin=True, has_approval=True)[0]]
check("B1 every declared legal transition is accepted (super-admin, approval present)",
      not bad_legal, bad_legal)
illegal = [(c, t) for c in fp.FIX_STATUSES for t in fp.FIX_STATUSES
           if c != t and t not in fp.FIX_TRANSITIONS.get(c, ())]
wrongly_ok = [(c, t) for c, t in illegal
              if fp.pipeline_status_change(c, t, is_super_admin=True, has_approval=True)[0]]
check(f"B2 EVERY illegal pair is refused ({len(illegal)} pairs checked, exhaustive)",
      not wrongly_ok, wrongly_ok)
push_from = [c for c in fp.FIX_STATUSES
             if fp.pipeline_status_change(c, "pushed", is_super_admin=True, has_approval=True)[0]
             and c != "pushed"]
check("B3 'pushed' is reachable ONLY from 'approved'", push_from == ["approved"], push_from)
check("B3b a direct reported → pushed jump is refused",
      fp.pipeline_status_change("reported", "pushed", is_super_admin=True, has_approval=True)[0] is False)
ok4, why4 = fp.pipeline_status_change("approved", "pushed", is_super_admin=True, has_approval=False)
check("B4 'pushed' without the approval audit trail is refused",
      ok4 is False and "approved" in why4, why4)
check("B5a 'approved' is refused to a non-super-admin (the service secret can never approve)",
      fp.pipeline_status_change("gate1_parked", "approved", is_super_admin=False,
                                actor_kind="secret")[0] is False)
check("B5b 'pushed' is refused to a non-super-admin",
      fp.pipeline_status_change("approved", "pushed", is_super_admin=False, actor_kind="secret",
                                has_approval=True)[0] is False)
check("B5c the working states ARE open to automation (reported → triaged → building → gate1_parked)",
      all(fp.pipeline_status_change(c, t, is_super_admin=False, actor_kind="secret")[0]
          for c, t in (("reported", "triaged"), ("triaged", "building"), ("building", "gate1_parked"))))
check("B6a money_touching cannot be advanced into a build by AUTOMATION",
      fp.pipeline_status_change("triaged", "building", is_super_admin=False, actor_kind="secret",
                                classification="money_touching")[0] is False)
check("B6b …but a super-admin (owner-first, deliberate) may",
      fp.pipeline_status_change("triaged", "building", is_super_admin=True, actor_kind="user",
                                classification="money_touching")[0] is True)
check("B6c money_touching may still be classified/rejected by automation (no dead end)",
      fp.pipeline_status_change("triaged", "rejected", is_super_admin=False, actor_kind="secret",
                                classification="money_touching")[0] is True)
check("B7a 'pushed' is terminal",
      all(fp.pipeline_status_change("pushed", t, is_super_admin=True, has_approval=True)[0] is False
          for t in fp.FIX_STATUSES if t != "pushed"))
check("B7b rejected / not_code may be reopened for a re-triage",
      fp.pipeline_status_change("rejected", "reported", is_super_admin=True)[0] is True
      and fp.pipeline_status_change("not_code", "reported", is_super_admin=True)[0] is True)
check("B7c an unknown target status is refused",
      fp.pipeline_status_change("reported", "banana", is_super_admin=True)[0] is False)
check("B7d approval states are NOT creatable (the lifecycle must be walked + audited)",
      "approved" not in fp.FIX_CREATE_STATUSES and "pushed" not in fp.FIX_CREATE_STATUSES
      and "building" not in fp.FIX_CREATE_STATUSES)

print("\n══ C. COST ACCOUNTING (pure) ══")
r_opus = rate("claude-opus-5", 5, 25, share=0.20)
check("C1 blended_rate = in*(1-share) + out*share", abs(fp.blended_rate(r_opus) - 9.0) < 1e-9,
      fp.blended_rate(r_opus))
check("C1b output_share is clamped to [0,1]",
      abs(fp.blended_rate(rate("m", 2, 10, share=5)) - 10.0) < 1e-9
      and abs(fp.blended_rate(rate("m", 2, 10, share=-3)) - 2.0) < 1e-9)
rates = [rate("claude-opus-5", 5, 25, eff="2026-01-01"),
         rate("claude-opus-5", 9, 45, eff="2099-01-01"),                       # FUTURE — must not apply
         rate("claude-opus-5", 7, 35, eff="2026-01-01", org=TEN_A),            # tenant override
         rate("claude-sonnet-5", 2, 10, eff="2026-01-01"),
         rate("claude-sonnet-5", 3, 15, eff="2026-06-01"),
         rate("claude-haiku-4-5", 1, 5, eff="2026-01-01", active=False)]       # inactive
check("C2a newest effective_date <= today wins (and the FUTURE row is ignored)",
      fp.rate_for(rates, "claude-sonnet-5", org_id=HOUSE, on_date="2026-07-30")["usd_per_mtok_in"] == 3
      and fp.rate_for(rates, "claude-opus-5", org_id=HOUSE, on_date="2026-07-30")["usd_per_mtok_in"] == 5)
check("C2b a past date resolves the rate that was in force then (rate history)",
      fp.rate_for(rates, "claude-sonnet-5", org_id=HOUSE, on_date="2026-03-01")["usd_per_mtok_in"] == 2)
check("C2c a tenant row overrides the house default for THAT tenant only",
      fp.rate_for(rates, "claude-opus-5", org_id=TEN_A, on_date="2026-07-30")["usd_per_mtok_in"] == 7
      and fp.rate_for(rates, "claude-opus-5", org_id=TEN_B, on_date="2026-07-30")["usd_per_mtok_in"] == 5)
check("C3 an inactive row never prices anything",
      fp.rate_for(rates, "claude-haiku-4-5", org_id=HOUSE, on_date="2026-07-30") is None)
cost, basis = fp.compute_cost(133_466, r_opus)
check("C4 133,466 tokens @ blended $9/MTok = $1.201194 (the real 2026-07-30 triage number)",
      abs(cost - 1.201194) < 1e-6, cost)
check("C4b cost_basis shows its work (rate, share, method) so the board $ is auditable",
      basis["blended_usd_per_mtok"] == 9.0 and basis["output_share"] == 0.20
      and "blended" in basis["method"] and basis["tokens"] == 133_466)
c_none, b_none = fp.compute_cost(50_000, None, model="claude-mystery-9")
check("C5 NO matching rate → cost None + an explanatory reason (never a hard-coded fallback rate)",
      c_none is None and "no active" in b_none["reason"], b_none)
check("C6 zero tokens with a rate → $0 (not None); zero tokens with no rate → None",
      fp.compute_cost(0, r_opus)[0] == 0.0 and fp.compute_cost(0, None)[0] is None)
roll = fp.rollup([
    {"tokens_triage": 100, "tokens_build": 200, "tokens_review": 0, "cost_usd": 1.5, "status": "pushed"},
    {"tokens_triage": 50, "tokens_build": 0, "tokens_review": 25, "cost_usd": None, "status": "gate1_parked"},
    {"tokens_triage": 0, "tokens_build": 0, "tokens_review": 0, "cost_usd": 0.25, "status": "approved"},
])
check("C7 rollup: 3 fixes / 375 tokens / $1.75, 1 unpriced, 1 shipped, 2 parked-or-approved",
      (roll["fixes"], roll["tokens"], roll["cost_usd"], roll["unpriced"], roll["shipped"], roll["parked"])
      == (3, 375, 1.75, 1, 1, 2), roll)

print("\n══ D. ENDPOINTS (fake client) ══")
st = fresh_store()
st["failure_log"] = [
    flog(TEN_A, "system_error", path="/api/v1/x/1", exc="KeyError", ref="r1", tb="TB-ALPHA"),
    flog(TEN_A, "system_error", path="/api/v1/x/2", exc="KeyError", ref="r2"),
    flog(TEN_B, "system_error", path="/api/v1/z/1", exc="TypeError", ref="r3"),
]
wire(st, read_only=True)          # ← every write verb now RAISES
feed = run(fp.pipeline_feed(org_id=HOUSE, all_orgs=1, authorization="", x_active_org="",
                            x_fix_pipeline_secret=SECRET))
check("D1a GET /feed performs ZERO writes (read-only fake would have raised)",
      len(feed["candidates"]) == 2 and feed["scanned"] == 3, feed.get("candidates"))
check("D1b the feed reports which door it served", feed["actor_kind"] == "secret")

wire(st)                          # writes allowed again
created = run(fp.create_pipeline_request(
    {"signature": "GET /api/v1/x/{id}|KeyError", "sample_path": "/api/v1/x/1", "exc_type": "KeyError",
     "first_ref": "r1", "failure_ids": [st["failure_log"][0]["id"]], "occurrence_count": 2,
     "classification": "code_bug", "module_agent": "mod-commission", "model": "claude-opus-5",
     "status": "pushed",                                    # ← must be clamped
     "affected_orgs": [{"org_id": TEN_A, "count": 2}]},
    org_id=HOUSE, authorization="", x_active_org="", x_fix_pipeline_secret=SECRET))
row = st["fix_requests"][0]
check("D2a POST /requests STAMPS org_id on the INSERT (RULE ONE write side)", row["org_id"] == HOUSE, row)
check("D2b a caller asking to be created 'pushed' is clamped to a pre-build status",
      created["status"] == "reported" and row["status"] == "reported", created)
check("D2c creation writes the first audit entry",
      len(row["audit"]) == 1 and row["audit"][0]["actor_kind"] == "secret"
      and row["audit"][0]["to"] == "reported", row["audit"])
dup = run(fp.create_pipeline_request(
    {"signature": "GET /api/v1/x/{id}|KeyError", "failure_ids": [st["failure_log"][1]["id"]],
     "affected_orgs": [{"org_id": TEN_B, "count": 1}]},
    org_id=HOUSE, authorization="", x_active_org="", x_fix_pipeline_secret=SECRET))
check("D3a a repeat POST for the SAME signature does NOT create a second row",
      len(st["fix_requests"]) == 1 and dup["deduped"] is True, len(st["fix_requests"]))
check("D3b …it unions failure_ids and merges affected_orgs",
      len(st["fix_requests"][0]["failure_ids"]) == 2
      and sorted(a["org_id"] for a in st["fix_requests"][0]["affected_orgs"]) == sorted([TEN_A, TEN_B]),
      st["fix_requests"][0])
check("D3c …and appends a fold entry to the audit trail (history is never rewritten)",
      len(st["fix_requests"][0]["audit"]) == 2 and "folded" in st["fix_requests"][0]["audit"][1]["note"])

rid = row["id"]
run(fp.patch_pipeline_request(rid, {"status": "triaged", "triage_summary": "period spelling",
                                    "tokens_triage": 133466, "note": "auto-triage"},
                              org_id=HOUSE, authorization="", x_active_org="",
                              x_fix_pipeline_secret=SECRET))
after = st["fix_requests"][0]
check("D4a PATCH advances the status and records per-stage tokens",
      after["status"] == "triaged" and after["tokens_triage"] == 133466)
check("D4b …re-prices from core.token_rates (133,466 @ blended $9 = $1.201194) — not from a constant",
      abs(float(after["cost_usd"]) - 1.201194) < 1e-6, after.get("cost_usd"))
check("D4c …and appends an audit entry on EVERY patch",
      len(after["audit"]) == 3 and after["audit"][-1]["note"] == "auto-triage", after["audit"])
run(fp.patch_pipeline_request(rid, {"status": "building"}, org_id=HOUSE, authorization="",
                              x_active_org="", x_fix_pipeline_secret=SECRET))
run(fp.patch_pipeline_request(rid, {"status": "gate1_parked", "branch": "agent/commission/x",
                                    "commit_sha": "abc1234", "worktree": "/workspaces/wt-x",
                                    "proofs_summary": "harness 41/41 · tsc 0", "tokens_build": 250000},
                              org_id=HOUSE, authorization="", x_active_org="",
                              x_fix_pipeline_secret=SECRET))
parked = st["fix_requests"][0]
check("D4d the parked evidence (branch/commit/worktree/proofs) is recorded",
      parked["branch"] == "agent/commission/x" and parked["commit_sha"] == "abc1234"
      and parked["proofs_summary"].startswith("harness"))

err = None
try:
    run(fp.patch_pipeline_request(rid, {"status": "approved"}, org_id=HOUSE, authorization="",
                                  x_active_org="", x_fix_pipeline_secret=SECRET))
except HTTPException as e:
    err = e
check("D5a the SECRET door cannot set 'approved' (403)",
      err is not None and err.status_code == 403 and "super-admin" in str(err.detail), err)
check("D5b …and the row is untouched", st["fix_requests"][0]["status"] == "gate1_parked")
err = None
try:
    run(fp.patch_pipeline_request(rid, {"status": "pushed", "pushed_commit": "dead111"}, org_id=HOUSE,
                                  authorization="Bearer super", x_active_org="",
                                  x_fix_pipeline_secret=""))
except HTTPException as e:
    err = e
check("D6 'pushed' straight from gate1_parked is refused end-to-end, even for a super-admin",
      err is not None and st["fix_requests"][0]["status"] == "gate1_parked", err)
run(fp.patch_pipeline_request(rid, {"status": "approved", "note": "owner said push it in chat"},
                              org_id=HOUSE, authorization="Bearer super", x_active_org="",
                              x_fix_pipeline_secret=""))
appr = st["fix_requests"][0]
check("D5c a super-admin CAN record the owner's chat approval, stamping approved_by/at",
      appr["status"] == "approved" and appr["approved_by"] == "owner@metricspro.tech"
      and appr["approved_at"], appr)
run(fp.patch_pipeline_request(rid, {"status": "pushed", "pushed_commit": "feed999"}, org_id=HOUSE,
                              authorization="Bearer super", x_active_org="",
                              x_fix_pipeline_secret=""))
pushed = st["fix_requests"][0]
check("D6b …and only THEN can it be recorded as pushed, with the commit + timestamp",
      pushed["status"] == "pushed" and pushed["pushed_commit"] == "feed999" and pushed["pushed_at"])
err = None
try:
    run(fp.patch_pipeline_request(rid, {"status": "building"}, org_id=HOUSE,
                                  authorization="Bearer super", x_active_org="",
                                  x_fix_pipeline_secret=""))
except HTTPException as e:
    err = e
check("D6c a pushed row is frozen (terminal) even for a super-admin",
      err is not None and st["fix_requests"][0]["status"] == "pushed", err)

# D7 org scoping
st2 = fresh_store()
wire(st2)
run(fp.create_pipeline_request({"signature": "sig-A|KeyError"}, org_id=TEN_A, authorization="",
                               x_active_org="", x_fix_pipeline_secret=SECRET))
run(fp.create_pipeline_request({"signature": "sig-B|KeyError"}, org_id=TEN_B, authorization="",
                               x_active_org="", x_fix_pipeline_secret=SECRET))
only_a = run(fp.list_pipeline_requests(org_id=TEN_A, authorization="", x_active_org="",
                                       x_fix_pipeline_secret=SECRET))
both = run(fp.list_pipeline_requests(org_id=TEN_A, all_orgs=1, authorization="", x_active_org="",
                                     x_fix_pipeline_secret=SECRET))
check("D7a reads are ORG-SCOPED by the explicit org_id param",
      len(only_a["fix_requests"]) == 1 and only_a["fix_requests"][0]["org_id"] == TEN_A)
check("D7b the platform-wide scope is EXPLICIT (all_orgs=1), never the default",
      len(both["fix_requests"]) == 2 and both["all_orgs"] is True and only_a["all_orgs"] is False)
a_id = only_a["fix_requests"][0]["id"]
err = None
try:
    run(fp.get_pipeline_request(a_id, org_id=TEN_B, authorization="", x_active_org="",
                                x_fix_pipeline_secret=SECRET))
except HTTPException as e:
    err = e
check("D7c tenant A's request is a 404 when scoped to tenant B (no cross-tenant read)",
      err is not None and err.status_code == 404)
err = None
try:
    run(fp.patch_pipeline_request(a_id, {"status": "triaged"}, org_id=TEN_B, authorization="",
                                  x_active_org="", x_fix_pipeline_secret=SECRET))
except HTTPException as e:
    err = e
check("D7d …and a cross-tenant PATCH is a 404 too (write side scoped as well)",
      err is not None and err.status_code == 404
      and st2["fix_requests"][0]["status"] == "reported")

# D8 traceback surfacing
st3 = fresh_store()
f1 = flog(TEN_A, "system_error", path="/api/v1/x/1", exc="KeyError", ref="3bf51b4d",
          tb="Traceback (most recent call last):\n  KeyError: 'July 2026'")
st3["failure_log"] = [f1]
wire(st3)
run(fp.create_pipeline_request({"signature": "GET /api/v1/x/{id}|KeyError", "failure_ids": [f1["id"]],
                                "model": "claude-opus-5", "title": "team_snapshot period crash"},
                               org_id=TEN_A, authorization="", x_active_org="",
                               x_fix_pipeline_secret=SECRET))
det = run(fp.get_pipeline_request(st3["fix_requests"][0]["id"], org_id=TEN_A, authorization="",
                                  x_active_org="", x_fix_pipeline_secret=SECRET))
check("D8 the detail payload carries the failure rows AND their detail.traceback (design §2a gap)",
      len(det["tracebacks"]) == 1 and "KeyError: 'July 2026'" in det["tracebacks"][0]["traceback"]
      and det["tracebacks"][0]["ref"] == "3bf51b4d", det.get("tracebacks"))
board = run(fp.list_pipeline_requests(org_id=TEN_A, authorization="Bearer super", x_active_org="",
                                      x_fix_pipeline_secret=""))
check("D9a the board payload carries a rollup computed over the returned rows",
      board["rollup"]["fixes"] == len(board["fix_requests"]))
check("D9b …the Phase-1 approval note (no in-app action) and the blended-rate caveat",
      "in chat" in board["approval_note"].lower() and "blended" in board["cost_note"].lower())
check("D9c …and the status/classification vocabularies for the UI",
      board["statuses"] == list(fp.FIX_STATUSES)
      and board["classifications"] == list(fp.FIX_CLASSIFICATIONS))

print("\n══ E. SECRET SCOPING ══")
check("E1 SECRET_CAPS is exactly {feed_read, registry_read, registry_write}",
      set(fp.SECRET_CAPS) == {"feed_read", "registry_read", "registry_write"}, sorted(fp.SECRET_CAPS))
check("E1b …and it is a strict subset of ALL_CAPS (config is out of reach)",
      fp.SECRET_CAPS < fp.ALL_CAPS and "config_write" not in fp.SECRET_CAPS)
for cap in ("config_read", "config_write"):
    err = None
    try:
        fp._authorize(cap, secret=SECRET)
    except HTTPException as e:
        err = e
    check(f"E2 the secret is REFUSED for {cap} (403)", err is not None and err.status_code == 403, err)
err = None
try:
    fp._authorize("feed_read", secret="wrong-secret")
except HTTPException as e:
    err = e
check("E3a a wrong secret is 401", err is not None and err.status_code == 401)
_saved = fp.settings.FIX_PIPELINE_SECRET
try:
    object.__setattr__(fp.settings, "FIX_PIPELINE_SECRET", "")
    check("E3b an UNSET server secret can never be matched (agent door closed by default)",
          fp._secret_ok("") is False and fp._secret_ok("anything") is False)
finally:
    object.__setattr__(fp.settings, "FIX_PIPELINE_SECRET", _saved)
actor = fp._authorize("registry_write", secret=SECRET)
check("E4 a valid secret never escalates to super-admin",
      actor["kind"] == "secret" and actor["super_admin"] is False and actor["caps"] == fp.SECRET_CAPS)
err = None
try:
    run(fp.pipeline_feed(org_id=HOUSE, authorization="Bearer tenant", x_active_org="",
                         x_fix_pipeline_secret=""))
except HTTPException as e:
    err = e
check("E5a a browser caller who is NOT a platform super-admin gets 403 on the feed",
      err is not None and err.status_code == 403, err)
err = None
try:
    run(fp.pipeline_feed(org_id=HOUSE, authorization="", x_active_org="",
                         x_fix_pipeline_secret=""))
except HTTPException as e:
    err = e
check("E5b no credential at all → 401", err is not None and err.status_code == 401, err)
sup = fp._authorize("config_write", authorization="Bearer super")
check("E5c a platform super-admin holds every capability, incl. the rate table",
      sup["kind"] == "user" and sup["super_admin"] is True and sup["caps"] == fp.ALL_CAPS)
ungated = []
for r in fp.router.routes:
    ep = getattr(r, "endpoint", None)
    if ep is None:
        continue
    try:
        src = inspect.getsource(ep)
    except (OSError, TypeError):
        src = ""
    if "_authorize(" not in src:
        ungated.append(getattr(r, "path", str(r)))
check(f"E6 EVERY route in the sub-router self-gates via _authorize ({len(fp.router.routes)} routes)",
      not ungated, ungated)

print("\n══ F. MIDDLEWARE + ROUTING ══")
check("F1a the pipeline prefix is middleware-allowlisted (the agent door carries no JWT)",
      tmw._is_public("/api/v1/core/fix-pipeline") and tmw._is_public("/api/v1/core/fix-pipeline/feed")
      and tmw._is_public("/api/v1/core/fix-pipeline/requests/abc-1"))
check("F1b the pre-existing /api/v1/core/fix-requests (mig-716 support pipeline) is NOT allowlisted — "
      "it keeps full middleware protection",
      tmw._is_public("/api/v1/core/fix-requests") is False
      and tmw._is_public("/api/v1/core/fix-requests/xyz") is False)
check("F1c no sloppy over-match: a lookalike sibling path is still protected",
      tmw._is_public("/api/v1/core/fix-pipelines") is False
      and tmw._is_public("/api/v1/core/failures") is False)

from starlette.routing import Match           # noqa: E402
from app.main import app as APP               # noqa: E402

want = [("GET", "/api/v1/core/fix-pipeline/feed", "pipeline_feed"),
        ("GET", "/api/v1/core/fix-pipeline/requests", "list_pipeline_requests"),
        ("POST", "/api/v1/core/fix-pipeline/requests", "create_pipeline_request"),
        ("GET", "/api/v1/core/fix-pipeline/requests/abc-1", "get_pipeline_request"),
        ("PATCH", "/api/v1/core/fix-pipeline/requests/abc-1", "patch_pipeline_request"),
        ("GET", "/api/v1/core/fix-pipeline/token-rates", "list_token_rates"),
        ("PUT", "/api/v1/core/fix-pipeline/token-rates", "upsert_token_rate")]
resolved = {}
for method, path, _ in want:
    scope = {"type": "http", "method": method, "path": path, "headers": [], "query_string": b"",
             "root_path": ""}
    resolved[(method, path)] = next((getattr(r, "name", str(r)) for r in APP.routes
                                     if r.matches(scope)[0] == Match.FULL), "NO MATCH")
mismatched = [(k, resolved[k], n) for (m, p, n) in want for k in [(m, p)] if resolved[k] != n]
check("F2a all 7 endpoints resolve under /api/v1 to THEIR OWN handlers (the /api/v1 last mile)",
      not mismatched, mismatched)
pipeline_routes = [r for r in APP.routes if "fix-pipeline" in getattr(r, "path", "")]
check("F2b exactly 7 pipeline routes are registered (no accidental extras)",
      len(pipeline_routes) == 7, [getattr(r, "path", "") for r in pipeline_routes])

from starlette.testclient import TestClient   # noqa: E402

st4 = fresh_store()
st4["failure_log"] = [flog(TEN_A, "system_error", path="/api/v1/x/1", exc="KeyError", ref="r9",
                           tb="TB-HTTP")]
wire(st4)
with TestClient(APP, raise_server_exceptions=False) as c:
    r = c.get("/api/v1/core/failures?limit=5", headers={fp.SECRET_HEADER: SECRET})
    check("F3 REAL ASGI: the secret on a NON-pipeline endpoint is rejected by the middleware (401) — "
          "it unlocks nothing outside the pipeline",
          r.status_code == 401, (r.status_code, r.text[:160]))
    r = c.get("/api/v1/core/fix-pipeline/feed?all_orgs=1", headers={fp.SECRET_HEADER: SECRET})
    check("F4a REAL ASGI: GET feed with the secret → 200 with candidates",
          r.status_code == 200 and len(r.json().get("candidates", [])) == 1,
          (r.status_code, r.text[:200]))
    r = c.get("/api/v1/core/fix-pipeline/feed?all_orgs=1")
    check("F4b REAL ASGI: the same path with NO credential → 401 (the handler is the gate)",
          r.status_code == 401, (r.status_code, r.text[:160]))
    r = c.post("/api/v1/core/fix-pipeline/requests?org_id=" + TEN_A,
               headers={fp.SECRET_HEADER: SECRET},
               json={"signature": "GET /api/v1/x/{id}|KeyError", "model": "claude-opus-5",
                     "failure_ids": [st4["failure_log"][0]["id"]]})
    new_id = r.json().get("id")
    check("F4c REAL ASGI: POST registers a row, org-stamped from the query param",
          r.status_code == 200 and st4["fix_requests"][0]["org_id"] == TEN_A,
          (r.status_code, r.text[:200]))
    r = c.patch(f"/api/v1/core/fix-pipeline/requests/{new_id}?org_id=" + TEN_A,
                headers={fp.SECRET_HEADER: SECRET},
                json={"status": "triaged", "tokens_triage": 133466})
    check("F4d REAL ASGI: PATCH advances + prices over HTTP",
          r.status_code == 200 and abs(float(r.json()["cost_usd"]) - 1.201194) < 1e-6,
          (r.status_code, r.text[:200]))
    for st_next in ("building", "gate1_parked"):
        r = c.patch(f"/api/v1/core/fix-pipeline/requests/{new_id}?org_id=" + TEN_A,
                    headers={fp.SECRET_HEADER: SECRET}, json={"status": st_next})
    check("F4e REAL ASGI: automation can walk the working states up to gate1_parked",
          r.status_code == 200 and st4["fix_requests"][0]["status"] == "gate1_parked",
          (r.status_code, r.text[:200]))
    # Now the ONLY thing standing between the secret and 'approved' is the privilege gate.
    r = c.patch(f"/api/v1/core/fix-pipeline/requests/{new_id}?org_id=" + TEN_A,
                headers={fp.SECRET_HEADER: SECRET}, json={"status": "approved"})
    check("F4f REAL ASGI: …but the secret cannot approve a PARKED fix over HTTP either (403)",
          r.status_code == 403 and "super-admin" in r.text
          and st4["fix_requests"][0]["status"] == "gate1_parked", (r.status_code, r.text[:200]))
    r = c.patch(f"/api/v1/core/fix-pipeline/requests/{new_id}?org_id=" + TEN_A,
                headers={fp.SECRET_HEADER: SECRET}, json={"status": "pushed"})
    check("F4g REAL ASGI: …and it certainly cannot mark it pushed (nothing here deploys anything)",
          r.status_code in (403, 409) and st4["fix_requests"][0]["status"] == "gate1_parked",
          (r.status_code, r.text[:200]))
    r = c.get("/api/v1/core/fix-pipeline/token-rates", headers={fp.SECRET_HEADER: SECRET})
    check("F4h REAL ASGI: the secret is refused on the rate table (403)",
          r.status_code == 403, (r.status_code, r.text[:200]))
    r = c.put("/api/v1/core/fix-pipeline/token-rates", headers={fp.SECRET_HEADER: SECRET},
              json={"model": "claude-opus-5", "usd_per_mtok_in": 0.01, "usd_per_mtok_out": 0.01})
    check("F4i REAL ASGI: the secret cannot rewrite a rate (403) — spend can't be silently misreported",
          r.status_code == 403, (r.status_code, r.text[:200]))

print("\n══ G. MIGRATION SQL SANITY ══")
SQL = open("../database/migrations/718_core_fix_pipeline.sql").read()
check("G1a the push-gate trigger exists on core.fix_requests",
      "CREATE TRIGGER fix_requests_guard_trg" in SQL and "core.fix_requests_guard()" in SQL)
check("G1b …it requires the previous status to be approved",
      re.search(r"NEW\.status = 'pushed'", SQL) and re.search(r"NOT IN \('approved', 'pushed'\)", SQL))
check("G1c …and refuses a row CREATED already pushed",
      "cannot be CREATED already pushed" in SQL)
check("G1d …and requires the approval audit stamps",
      "approved_by IS NULL OR NEW.approved_at IS NULL" in SQL)
check("G2a RLS is enabled on both new tables",
      SQL.count("ENABLE ROW LEVEL SECURITY") == 2)
sql_stmts = [ln for ln in SQL.splitlines() if not ln.strip().startswith("--")]
sql_code = "\n".join(sql_stmts)
check("G2b ZERO anon/authenticated grants and ZERO policies in the EXECUTED sql (AGENT_CONTRACT §5)",
      not re.search(r"(?i)grant[^;]*\bto\b[^;]*\b(anon|authenticated)\b", sql_code)
      and not re.search(r"(?i)create\s+policy", sql_code),
      [ln for ln in sql_stmts if re.search(r"(?i)anon|authenticated|create\s+policy", ln)])
check("G2c the service role is granted on both tables",
      SQL.count("TO service_role") >= 3)
check("G3a both tables carry org_id uuid NOT NULL (in the EXECUTED sql, not a comment)",
      len(re.findall(r"(?i)\borg_id\s+uuid\s+not\s+null", sql_code)) == 2,
      re.findall(r"(?i)\borg_id\s+uuid\s+not\s+null", sql_code))
check("G3b org_id is indexed on both tables",
      "fix_requests_org_idx" in SQL and "token_rates_org_idx" in SQL)
check("G3c the dedupe guarantee is a DB constraint, not just app code",
      "UNIQUE (org_id, signature)" in SQL)
check("G3d rate history is keyed (org, model, effective_date)",
      "UNIQUE (org_id, model, effective_date)" in SQL)
def split_top(s):
    """Split on TOP-LEVEL commas, respecting () and single-quoted strings (with '' escapes)."""
    out, buf, depth, q = [], [], 0, False
    i = 0
    while i < len(s):
        ch = s[i]
        if q:
            buf.append(ch)
            if ch == "'":
                if i + 1 < len(s) and s[i + 1] == "'":
                    buf.append(s[i + 1]); i += 2; continue
                q = False
        elif ch == "'":
            q = True; buf.append(ch)
        elif ch == "(":
            depth += 1; buf.append(ch)
        elif ch == ")":
            depth -= 1; buf.append(ch)
        elif ch == "," and depth == 0:
            out.append("".join(buf).strip()); buf = []
        else:
            buf.append(ch)
        i += 1
    if "".join(buf).strip():
        out.append("".join(buf).strip())
    return out


seed = re.search(r"INSERT INTO core\.token_rates\s*\((.*?)\)\s*VALUES(.*?)ON CONFLICT", SQL, re.S)
cols = split_top(seed.group(1)) if seed else []
groups = [g.strip() for g in split_top(seed.group(2).strip()) if g.strip()] if seed else []
arities = [len(split_top(g.strip()[1:-1])) for g in groups]
check(f"G4a the seeded rate INSERT has matching column/value arity on all {len(groups)} rows "
      f"({len(cols)} columns)",
      bool(seed) and len(groups) == 7 and all(a == len(cols) for a in arities),
      (len(cols), len(groups), arities))
check("G4b every seeded rate is DATED (so a price change is a new row, never a destructive edit)",
      len(re.findall(r"DATE '\d{4}-\d{2}-\d{2}'", seed.group(2) if seed else "")) == 7)
check("G4c the seed is idempotent (never clobbers an owner-edited rate)",
      "ON CONFLICT (org_id, model, effective_date) DO NOTHING" in SQL)
check("G4d the seed carries the owner-confirm warning (rates are a seed, not a source of truth)",
      "OWNER MUST CONFLICT" not in SQL and "OWNER MUST CONFIRM AT SHIP TIME" in SQL)
ENT = open("app/modules/core/entitlements.py").read()
check("G5a SEED_VERSION was bumped to 7", re.search(r"SEED_VERSION = 7\b", ENT) is not None)
check("G5b the rate seed is called from the entitlement sync path (house org)",
      'rpc("seed_token_rates"' in ENT)
check("G5c …best-effort, so an un-run mig 718 is a silent no-op",
      re.search(r'rpc\("seed_token_rates".*?\n\s*except Exception:\n\s*pass', ENT, re.S) is not None)
CFG = open("app/core/config.py").read()
check("G5d FIX_PIPELINE_SECRET is declared and DEFAULTS EMPTY (agent door closed until set)",
      re.search(r'FIX_PIPELINE_SECRET: str = ""', CFG) is not None)
MW = open("app/core/tenant_middleware.py").read()
check("G5e the middleware allowlist entry is the ONLY change to that file's behaviour "
      "(one prefix added, boundary-matched)",
      MW.count('"/api/v1/core/fix-pipeline"') == 1
      and 'path == p or path.startswith(p + "/")' in MW)

print("\n══ H. LOGIN / SEED PATH SAFETY (the SEED_VERSION bump is a login-path change) ══")
import app.modules.core.entitlements as ent      # noqa: E402

check("H1 SEED_VERSION is 7 in code (so every tenant re-syncs once on its next login)",
      ent.SEED_VERSION == 7, ent.SEED_VERSION)


class RpcSpy:
    """A client whose rpc() RECORDS the call; `boom` makes it raise (un-run mig 718)."""
    def __init__(self, store, boom=False):
        self.store, self.boom, self.calls = store, boom, []

    def schema(self, _n): return self
    def table(self, name): return Q(self.store, name)

    def rpc(self, name, params=None):
        self.calls.append((name, dict(params or {})))
        if self.boom:
            def _raise():
                raise RuntimeError('function core.seed_token_rates(uuid) does not exist')
            return SimpleNamespace(execute=_raise)
        return SimpleNamespace(execute=lambda: SimpleNamespace(data=[]))


spy = RpcSpy({"tenants": [{"org_id": HOUSE, "seed_version": 6}], "tenant_modules": [],
              "billing_plan": [], "module_catalog": []})
res = ent.sync_tenant(spy, HOUSE)
check("H2 the HOUSE sync pass calls core.seed_token_rates with the org (mig-076 seed pattern)",
      ("seed_token_rates", {"p_org": HOUSE}) in spy.calls, spy.calls)
spy_t = RpcSpy({"tenants": [{"org_id": TEN_A, "seed_version": 6}], "tenant_modules": [],
                "billing_plan": [], "module_catalog": []})
ent.sync_tenant(spy_t, TEN_A)
check("H3 a TENANT sync pass does NOT re-seed rates (they are house/platform config)",
      not any(n == "seed_token_rates" for n, _ in spy_t.calls), spy_t.calls)
boom = RpcSpy({"tenants": [{"org_id": HOUSE, "seed_version": 6}], "tenant_modules": [],
               "billing_plan": [], "module_catalog": []}, boom=True)
res_boom = ent.sync_tenant(boom, HOUSE)
check("H4 an un-run mig 718 (rpc raises) is a SILENT no-op — a login can never break on it",
      isinstance(res_boom, dict) and res_boom["org_id"] == HOUSE)
check("H5 …and the entitlement result is unchanged by the new seed step",
      sorted(res_boom["enabled_modules"]) == sorted(res["enabled_modules"]))
check("H6 needs_sync still reads the watermark (a tenant at 7 is up to date, at 6 is behind)",
      ent.needs_sync(RpcSpy({"tenants": [{"org_id": HOUSE, "seed_version": 7}]}), HOUSE) is False
      and ent.needs_sync(RpcSpy({"tenants": [{"org_id": HOUSE, "seed_version": 6}]}), HOUSE) is True)
check("H7 NO new entitlement module was invented (the board is a platform surface, not billable)",
      "fix_pipeline" not in ent.MODULE_CATALOG and "fix_requests" not in ent.MODULE_CATALOG
      and len(ent.ALL_MODULES) == 12, ent.ALL_MODULES)

print(f"\n{'='*92}\n{len(PASS)} passed, {len(FAIL)} failed" + (f"  → {FAIL}" if FAIL else "  ✅") + f"\n{'='*92}")
sys.exit(1 if FAIL else 0)
