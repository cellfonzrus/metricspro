"""AUTO-FIX PIPELINE — Phase 1 (mig 718). Owner-approved in chat 2026-07-30.

Design note: docs/designs/auto-fix-pipeline.md (§2b registry · §2c API · §2e cost · §2f board).
This file builds §2c + §2e. It does NOT build §2d (the scheduled triage routine — that lives OUTSIDE the
app, a Claude Code routine the operator sets up) and it does NOT build §4 (Phase 2's in-app Approve
button — explicitly deferred to a separate owner decision).

THE LOOP THIS SERVES
    core.failure_log ──► triage agent ──► module agent builds PARKED ──► Gate 1 (operator, in chat)
                                                                              │
                                                            Gate 2: owner says "push" IN CHAT ──► merge

HARD RULE OF PHASE 1 — NOTHING HERE CAN DEPLOY ANYTHING.
  * Every automated step is read-only against prod or parked on an unpushed branch.
  * `status` can never reach 'pushed' without having passed 'approved' (pipeline_status_change, plus the
    mig-718 DB trigger as a second, independent belt).
  * 'approved' requires a SUPER-ADMIN browser request — it RECORDS the owner's in-chat Gate 2, it does not
    grant one. The board UI has NO approve control at all in Phase 1.
  * A row classified `money_touching` can never be advanced into a build by automation (AGENT_CONTRACT §7:
    money-touching is owner-first, always).

AUTH — TWO DOORS, DIFFERENT KEYS (§2c)
  BROWSER: a verified Supabase JWT whose login is a platform SUPER-ADMIN. Nothing less; a tenant admin
    gets 403. (The board is a platform surface, like /admin/tenants.)
  AGENT:   the `x-fix-pipeline-secret` header == settings.FIX_PIPELINE_SECRET (the NOTIFY_RUN_SECRET
    precedent — an env secret the operator sets on Railway, never in code or logs). It is LEAST-PRIVILEGE:
    scoped to feed-read + registry read/write ONLY (SECRET_CAPS). It cannot write config (token rates),
    cannot approve, cannot push, and — because no other endpoint in the app reads that header — it unlocks
    nothing else anywhere. A request bearing ONLY the secret to any non-pipeline path is rejected by
    tenant_middleware with 401 before a handler ever sees it (proved in harness_fix_pipeline.py).

WHY EVERY ROUTE HERE SELF-GATES
  `/api/v1/core/fix-pipeline` is on tenant_middleware's public-prefix allowlist, because the agent door
  carries no JWT (exactly like the dual-auth /core/tenants/sync and the */run-due sweeps). Consequence:
  for these paths the middleware performs NO auth check and NO org_id rewrite. So every route in this
  module MUST call _authorize() itself, default-DENY, and resolve its own org. harness_fix_pipeline.py
  asserts route-level coverage so a future endpoint added here cannot silently ship ungated.

MULTI-TENANT (RULE ONE)
  org_id is an explicit query param on every read AND every write, and every INSERT stamps it (the
  write-side trap: scoping reads without stamping inserts silently drops rows). A fix_request's org_id is
  the OWNING org — the platform/house org for the triage loop, or the tenant that filed it — and
  `affected_orgs` records every tenant the signature was seen in, with counts (same semantics mig 716
  documents for support_fix_request; a code bug spans tenants). `all_orgs=1` is an EXPLICIT, platform-only
  (super-admin or service secret) cross-tenant scope for the feed/board, never a default.

DEGRADES GRACEFULLY: mig 718 un-run ⇒ every endpoint returns an honest empty payload + `hint`, and the
  board shows an empty state. No unrelated page is affected. Mirrors mig 112 / 716 / 717 style.

NOT MONEY-TOUCHING: nothing here reads or writes a rate, plan, tier, payout, commission or P&L row.
  `cost_usd` is INTERNAL AI-spend reporting for the owner — not a payable, not in any P&L feed.
"""
import hmac
import re
from datetime import date, datetime, timezone

from fastapi import APIRouter, HTTPException, Header

from app.core.config import settings
from app.core.database import get_supabase

# NO extra prefix beyond "/fix-pipeline": this sub-router is mounted ONTO core/router.py's `router`
# (which already carries "/core"), so main.py — a SHARED file — needs no change. Final paths:
# /api/v1/core/fix-pipeline/…
router = APIRouter(prefix="/fix-pipeline", tags=["Core / Fix pipeline"])

ORG_ID = "00000000-0000-0000-0000-000000000001"   # house/platform org
MIG_HINT = "The fix pipeline is not set up yet — run migration 718 (core.fix_requests + core.token_rates)."


def sb():
    return get_supabase()


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


# ══ Status machine (§2b) ══════════════════════════════════════════════════════════════════════════
FIX_STATUSES = ("reported", "triaged", "building", "gate1_parked", "approved", "pushed",
                "rejected", "not_code")
FIX_CLASSIFICATIONS = ("code_bug", "config", "data", "transient", "duplicate", "money_touching")

# Statuses a caller may CREATE a row in. Deliberately excludes every state at or past a build (and both
# approval states), so the lifecycle is always WALKED through PATCH — and therefore audited — rather than
# jumped into. This closes the creation-side bypass Gate 1 caught on the mig-716 pipeline.
FIX_CREATE_STATUSES = ("reported", "triaged", "not_code")

# The legal moves. 'pushed' is reachable ONLY from 'approved' — that is the whole point of Phase 1.
FIX_TRANSITIONS = {
    "reported":     ("triaged", "building", "rejected", "not_code"),
    "triaged":      ("building", "gate1_parked", "rejected", "not_code"),
    "building":     ("gate1_parked", "rejected", "not_code"),
    "gate1_parked": ("approved", "building", "rejected"),   # Gate-1 rework returns it to building
    "approved":     ("pushed", "rejected"),
    "pushed":       (),                                     # terminal, forever
    "rejected":     ("reported",),                          # super-admin may reopen for a re-triage
    "not_code":     ("reported",),
}
# Only a super-admin (browser) may set these. The service secret can never reach them.
FIX_SUPER_ONLY_TARGETS = ("approved", "pushed")
# Advancing a money-touching row into a build/park/approval is owner-first work (AGENT_CONTRACT §7):
# automation is refused outright, a super-admin may do it deliberately.
FIX_MONEY_GUARDED_TARGETS = ("building", "gate1_parked", "approved", "pushed")
FIX_TERMINAL = ("pushed",)


def pipeline_status_change(current, target, *, is_super_admin=False, actor_kind="user",
                          classification=None, has_approval=False):
    """PURE decision for a core.fix_requests status transition. Returns (ok, reason).

    Rules, in order (all default-DENY):
      1. target must be a known status;
      2. the move current→target must be in FIX_TRANSITIONS ('pushed' ONLY from 'approved');
      3. 'approved'/'pushed' require a super-admin — the service secret can never approve or push;
      4. 'pushed' additionally requires the approval audit trail to already exist (approved_by/at);
      5. a `money_touching` row may not be advanced into a build/park/approval by AUTOMATION.
    Unit-proven in harness_fix_pipeline.py; mirrored by the mig-718 DB trigger for rule 2/4."""
    cur = str(current or "").strip().lower()
    tgt = str(target or "").strip().lower()
    if tgt not in FIX_STATUSES:
        return (False, f"invalid status '{target}'")
    if cur not in FIX_STATUSES:
        return (False, f"unknown current status '{current}'")
    if tgt == cur:
        return (True, "")                                    # idempotent no-op move
    allowed = FIX_TRANSITIONS.get(cur, ())
    if tgt not in allowed:
        if cur in FIX_TERMINAL:
            return (False, f"'{cur}' is terminal — a pushed fix cannot change status")
        return (False, f"illegal transition {cur} → {tgt} (allowed: {', '.join(allowed) or 'none'})")
    if tgt in FIX_SUPER_ONLY_TARGETS and not is_super_admin:
        return (False, f"only a platform super-admin may set '{tgt}' "
                       "(the owner's push approval is given in chat and recorded here)")
    if tgt == "pushed" and not has_approval:
        return (False, "'pushed' requires the approval audit trail (approved_by + approved_at) — "
                       "a fix can never be recorded as pushed without having passed 'approved'")
    if (str(classification or "").lower() == "money_touching"
            and tgt in FIX_MONEY_GUARDED_TARGETS and actor_kind != "user"):
        return (False, "money-touching fixes are never advanced by automation — owner-first "
                       "(AGENT_CONTRACT §7)")
    return (True, "")


# ══ Signature (§2b) — one bug = one row ══════════════════════════════════════════════════════════
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
_HEXREF_RE = re.compile(r"^[0-9a-f]{6,}$", re.I)
_NUM_RE = re.compile(r"^\d+$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}(-\d{2})?$")
_PERIOD_RE = re.compile(r"^[A-Za-z]{3,9}[ _%+-]?\d{4}$")      # 'July 2026', 'July%202026', 'July_2026'


def normalize_path(path):
    """A request path → a stable TEMPLATE, so 50 occurrences on different ids collapse to one signature.
    Replaces per-request segments (uuid, numeric id, hex ref, YYYY-MM[-DD], 'July 2026') with {id}/{period}.
    Keeps the leading method token if the caller passed 'GET /x' (failure_log.source's shape). PURE."""
    raw = str(path or "").strip()
    if not raw:
        return ""
    method = ""
    m = re.match(r"^([A-Z]{3,7})\s+(/.*)$", raw)
    if m:
        method, raw = m.group(1) + " ", m.group(2)
    raw = raw.split("?", 1)[0].split("#", 1)[0]
    out = []
    for seg in raw.split("/"):
        if not seg:
            out.append(seg)
            continue
        s = seg.strip()
        if _UUID_RE.match(s) or _NUM_RE.match(s) or _HEXREF_RE.match(s):
            out.append("{id}")
        elif _DATE_RE.match(s) or _PERIOD_RE.match(re.sub(r"%20", " ", s)):
            out.append("{period}")
        else:
            out.append(s)
    return method + "/".join(out)


def fix_signature(path, exc_type):
    """The dedupe key: normalized path template + '|' + exception type (NOT the per-occurrence ref).
    Blank inputs degrade to 'unknown' rather than colliding on the empty string. PURE."""
    p = normalize_path(path) or "unknown"
    e = str(exc_type or "").strip() or "unknown"
    return f"{p}|{e}"


def signature_of_failure(row):
    """Derive (signature, path, exc_type) from a core.failure_log row. A mig-112 `system_error` row carries
    detail={ref,method,path,exc_type,traceback} (HardeningMiddleware); every other category has no path, so
    its `source` + `category` stand in — which keeps ONE universal signature rule for all failure kinds.
    PURE."""
    d = row.get("detail") if isinstance(row.get("detail"), dict) else {}
    path = d.get("path") or row.get("source") or ""
    method = d.get("method") or ""
    if method and path.startswith("/"):
        path = f"{method} {path}"
    exc = d.get("exc_type") or row.get("category") or "unknown"
    return (fix_signature(path, exc), path, exc)


def _traceback_of(row):
    d = row.get("detail") if isinstance(row.get("detail"), dict) else {}
    return d.get("traceback") or None


def _ref_of(row):
    d = row.get("detail") if isinstance(row.get("detail"), dict) else {}
    return d.get("ref") or None


def build_feed(failure_rows, registered_signatures, kind_meta=None):
    """PURE. Fold unreviewed failure_log rows into SIGNATURE candidates, dropping any signature that is
    already registered as a fix_request (safety rail §3.5 — one bug never spawns duplicate builds).
    Returns (candidates, skipped_registered_count). Sorted most-occurrences first, then most recent."""
    reg = {str(s) for s in (registered_signatures or [])}
    sev_rank = {"error": 3, "warning": 2, "info": 1}
    cand, skipped = {}, 0
    for r in (failure_rows or []):
        sig, path, exc = signature_of_failure(r)
        if sig in reg:
            skipped += 1
            continue
        c = cand.get(sig)
        if c is None:
            meta = (kind_meta or {}).get(r.get("category")) or {}
            c = cand[sig] = {
                "signature": sig, "sample_path": path, "exc_type": exc,
                "category": r.get("category"), "module_hint": meta.get("module") or "admin",
                "label": meta.get("label") or (r.get("category") or "Unknown"),
                "count": 0, "latest_at": None, "first_ref": None, "severity": "info",
                "failure_ids": [], "affected_orgs": {}, "sample_message": r.get("message"),
                "sample_traceback": None,
            }
        c["count"] += 1
        if r.get("id"):
            c["failure_ids"].append(r["id"])
        oid = r.get("org_id")
        if oid:
            c["affected_orgs"][oid] = c["affected_orgs"].get(oid, 0) + 1
        ca = r.get("created_at")
        if ca and (c["latest_at"] is None or str(ca) > str(c["latest_at"])):
            c["latest_at"] = ca
        if sev_rank.get(r.get("severity"), 0) > sev_rank.get(c["severity"], 0):
            c["severity"] = r.get("severity") or c["severity"]
        if c["first_ref"] is None:
            c["first_ref"] = _ref_of(r)
        if c["sample_traceback"] is None:
            c["sample_traceback"] = _traceback_of(r)
    out = []
    for c in cand.values():
        c["failure_ids"] = c["failure_ids"][:500]
        c["affected_orgs"] = [{"org_id": o, "count": n} for o, n in c.pop("affected_orgs").items()]
        out.append(c)
    out.sort(key=lambda x: (x["count"], str(x["latest_at"] or "")), reverse=True)
    return (out, skipped)


# ══ Cost accounting (§2e) ════════════════════════════════════════════════════════════════════════
# Rates are DATA (core.token_rates), never code. There is NO fallback rate anywhere in this module: when
# no rate row matches, cost_usd is None and cost_basis records WHY — the board then shows "—", not a
# fabricated number. That is deliberate (RULE TWO + "never hard-code rates outside the config table").
def blended_rate(rate_row):
    """$/MTok for a rate row, blending in/out by its own output_share. Agent completion metadata reports a
    single TOTAL token count (no in/out split), so a blend is the honest arithmetic — and the share is
    per-model config, surfaced on the board as an explicit caveat. PURE."""
    if not rate_row:
        return None
    try:
        rin = float(rate_row.get("usd_per_mtok_in"))
        rout = float(rate_row.get("usd_per_mtok_out"))
    except (TypeError, ValueError):
        return None
    try:
        share = float(rate_row.get("output_share", 0.20))
    except (TypeError, ValueError):
        share = 0.20
    share = max(0.0, min(1.0, share))
    return rin * (1.0 - share) + rout * share


def rate_for(rate_rows, model, org_id=None, house_org=ORG_ID, on_date=None):
    """Resolve the rate row that applies to `model`: the tenant's own row wins over the house/platform
    row, and within a scope the NEWEST effective_date that is <= on_date wins (rate history). Inactive
    rows are ignored. Returns None when nothing matches (→ no $ shown). PURE."""
    m = str(model or "").strip().lower()
    if not m:
        return None
    ref = str(on_date or date.today().isoformat())[:10]
    best = None
    best_key = None
    for r in (rate_rows or []):
        if str(r.get("model") or "").strip().lower() != m:
            continue
        if not r.get("is_active", True):
            continue
        eff = str(r.get("effective_date") or "")[:10]
        if eff and eff > ref:
            continue                                   # a future rate does not price today's spend
        scope = 1 if (org_id and r.get("org_id") == org_id and org_id != house_org) else 0
        key = (scope, eff)
        if best_key is None or key > best_key:
            best, best_key = r, key
    return best


def compute_cost(tokens_total, rate_row, *, model=None):
    """(cost_usd, cost_basis) for a token total. cost_usd is None when no rate applies — never a guess.
    cost_basis is stored on the row so the $ on the board can always be explained. PURE."""
    try:
        toks = int(tokens_total or 0)
    except (TypeError, ValueError):
        toks = 0
    if toks <= 0:
        return (0.0 if rate_row else None,
                {"tokens": toks, "model": (model or (rate_row or {}).get("model")),
                 "reason": "zero tokens recorded" if rate_row else "no rate row for this model"})
    rate = blended_rate(rate_row)
    if rate is None:
        return (None, {"tokens": toks, "model": model,
                       "reason": "no active core.token_rates row matches this model — set one at "
                                 "/admin/fix-requests to price it"})
    cost = round(toks / 1_000_000.0 * rate, 6)
    return (cost, {
        "tokens": toks, "model": rate_row.get("model"), "rate_id": rate_row.get("id"),
        "usd_per_mtok_in": rate_row.get("usd_per_mtok_in"),
        "usd_per_mtok_out": rate_row.get("usd_per_mtok_out"),
        "output_share": rate_row.get("output_share", 0.20),
        "blended_usd_per_mtok": round(rate, 6),
        "effective_date": rate_row.get("effective_date"),
        "method": "blended: total_tokens x (in x (1-output_share) + out x output_share) — agent metadata "
                  "reports one total, not an in/out split",
    })


def tokens_total(row):
    """Sum of the three per-stage counters on a fix_request row. PURE."""
    def n(v):
        try:
            return int(v or 0)
        except (TypeError, ValueError):
            return 0
    return n(row.get("tokens_triage")) + n(row.get("tokens_build")) + n(row.get("tokens_review"))


def rollup(rows):
    """PURE period rollup for the board tile — computed over the rows the CALLER already filtered, so the
    tile always agrees with the table and with the export (RULE FIVE: filters drive everything)."""
    out = {"fixes": 0, "tokens": 0, "cost_usd": 0.0, "priced": 0, "unpriced": 0,
           "by_status": {}, "shipped": 0, "parked": 0}
    for r in (rows or []):
        out["fixes"] += 1
        out["tokens"] += tokens_total(r)
        c = r.get("cost_usd")
        if c is None:
            out["unpriced"] += 1
        else:
            try:
                out["cost_usd"] += float(c)
                out["priced"] += 1
            except (TypeError, ValueError):
                out["unpriced"] += 1
        st = r.get("status") or "reported"
        out["by_status"][st] = out["by_status"].get(st, 0) + 1
        if st == "pushed":
            out["shipped"] += 1
        elif st in ("gate1_parked", "approved"):
            out["parked"] += 1
    out["cost_usd"] = round(out["cost_usd"], 4)
    return out


# ══ Auth (§2c) — two doors, least privilege, default DENY ═════════════════════════════════════════
SECRET_HEADER = "x-fix-pipeline-secret"
# EXACTLY what the agent service secret may do. Anything not listed here needs a super-admin browser
# session. Keep this tuple as the single source of truth — the harness asserts against it.
SECRET_CAPS = frozenset({"feed_read", "registry_read", "registry_write"})
ALL_CAPS = frozenset({"feed_read", "registry_read", "registry_write", "config_read", "config_write"})


def _secret_ok(presented):
    """Constant-time compare against FIX_PIPELINE_SECRET. An UNSET secret can never match (so the agent
    door is closed until the operator sets it) — mirrors the NOTIFY_RUN_SECRET guards."""
    want = (settings.FIX_PIPELINE_SECRET or "").strip()
    got = (presented or "").strip()
    if not want or not got:
        return False
    return hmac.compare_digest(want, got)


def _authorize(need, *, authorization="", x_active_org="", secret=""):
    """THE gate for every route in this module (default DENY). Returns an actor dict:
        {"kind": "secret"|"user", "actor": <label>, "super_admin": bool, "caps": frozenset}

    Order matters: the secret is checked FIRST and, when valid, the request is treated as automation with
    SECRET_CAPS only — presenting a valid secret can never escalate to a super-admin capability. A browser
    request must resolve to a platform SUPER-ADMIN; a tenant admin is 403 (this is a platform surface).

    NOTE: `/api/v1/core/fix-pipeline` is middleware-allowlisted (the agent door has no JWT), so there is
    NO ambient auth and NO org rewrite on these paths — this function is the only thing standing there."""
    if need not in ALL_CAPS:
        raise HTTPException(500, f"unknown capability '{need}'")   # programmer error, fail loudly
    # Normalize the header values defensively: anything that is not a real string (a direct in-process
    # call that left a FastAPI Header default in place, a None) counts as ABSENT rather than as a
    # malformed credential — so a caller can never accidentally take the secret branch with a non-secret.
    secret = secret if isinstance(secret, str) else ""
    authorization = authorization if isinstance(authorization, str) else ""
    x_active_org = x_active_org if isinstance(x_active_org, str) else ""
    if secret:
        if not _secret_ok(secret):
            raise HTTPException(401, "invalid fix-pipeline secret")
        if need not in SECRET_CAPS:
            raise HTTPException(403, "the fix-pipeline service secret is scoped to the feed and the fix "
                                     f"request registry — it cannot '{need}'")
        return {"kind": "secret", "actor": "fix-pipeline-agent", "super_admin": False,
                "caps": SECRET_CAPS}
    # Browser door. _require_super_admin is the SAME gate /admin/tenants and /core/super-admins use
    # (platform definition: the flag on ANY membership, plus the house-admin bootstrap) — so the
    # operator's super-admin path into this board is provably the one that already works, and switching
    # active tenant with the tenant picker cannot lock them out.
    from app.modules.core.router import _uid_from_token, _require_super_admin
    uid = _uid_from_token(authorization)
    if not uid:
        raise HTTPException(401, "not authenticated")
    try:
        u = _require_super_admin(authorization, x_active_org) or {}
    except HTTPException:
        raise HTTPException(403, "The fix pipeline is a platform surface — super-admins only.")
    return {"kind": "user", "actor": (u.get("email") or u.get("role") or "super_admin"),
            "super_admin": True, "caps": ALL_CAPS, "org_id": u.get("org_id")}


def _scope(actor, org_id, all_orgs):
    """Resolve the read/write org scope. org_id is the explicit QUERY PARAM (RULE ONE); `all_orgs=1` is an
    EXPLICIT platform-wide scope available to both platform doors (super-admin, service secret) — never a
    default, and every returned row still carries its own org_id. A non-super browser caller never gets
    here (_authorize 403s first); the clamp is kept as defence in depth if that gate is ever relaxed."""
    org = (org_id or "").strip() or ORG_ID
    if actor.get("kind") == "user" and not actor.get("super_admin"):
        org = actor.get("org_id") or org
        return (org, False)
    return (org, bool(all_orgs))


# ══ Registry helpers ═════════════════════════════════════════════════════════════════════════════
def _rate_rows(client, org_id, extra_orgs=()):
    """Rate rows visible to the given org(s): their own + the house/platform defaults. ONE round trip
    (`extra_orgs` lets the board price a cross-tenant page without a query per row). Best-effort — an
    un-run mig 718 yields [] and the board simply shows no $."""
    orgs = list(dict.fromkeys([ORG_ID, org_id, *(o for o in extra_orgs if o)]))
    try:
        return (client.schema("core").table("token_rates").select("*")
                .in_("org_id", orgs).execute().data) or []
    except Exception:
        return []


def _price(row, rate_rows, org_id):
    """Recompute cost_usd/cost_basis for a fix_request row from the CURRENT rates. Pure w.r.t. the DB."""
    rr = rate_for(rate_rows, row.get("model"), org_id=org_id)
    return compute_cost(tokens_total(row), rr, model=row.get("model"))


def _audit(row, *, actor, actor_kind, frm, to, note=""):
    """Append one entry to the append-only audit array (never rewrites history). Returns the new list."""
    trail = row.get("audit")
    trail = list(trail) if isinstance(trail, list) else []
    trail.append({"at": _now_iso(), "actor": actor, "actor_kind": actor_kind,
                  "from": frm, "to": to, "note": (note or "")[:500]})
    return trail[-200:]        # bounded so one pathological row can't grow without limit


def _fetch_request(client, rid, org_id, all_orgs=False):
    q = client.schema("core").table("fix_requests").select("*").eq("id", rid)
    if not all_orgs:
        q = q.eq("org_id", org_id)
    rows = q.limit(1).execute().data or []
    return rows[0] if rows else None


# ══ Endpoints ════════════════════════════════════════════════════════════════════════════════════
@router.get("/feed")
async def pipeline_feed(org_id: str = ORG_ID, all_orgs: int = 0, reviewed: str = "false",
                        limit: int = 800, authorization: str = Header(default=""),
                        x_active_org: str = Header(default=""),
                        x_fix_pipeline_secret: str = Header(default="")):
    """Unreviewed core.failure_log rows NOT yet folded into a fix_request, grouped by SIGNATURE.

    This is the triage agent's inbox (and the board's "what's not registered yet" panel). READ-ONLY: it
    writes nothing, changes no failure row's reviewed state, and dispatches nothing. A signature that
    already has a fix_request is excluded and counted in `skipped_already_registered` (safety rail §3.5:
    one bug never spawns duplicate builds)."""
    actor = _authorize("feed_read", authorization=authorization, x_active_org=x_active_org,
                       secret=x_fix_pipeline_secret)
    org, cross = _scope(actor, org_id, all_orgs)
    from app.modules.core.router import _fetch_failures, _house_kind_docs, _merge_kind_docs
    client = sb()
    try:
        rows = _fetch_failures(client, org_id=(None if cross else org), reviewed=reviewed,
                               limit=min(max(int(limit or 1), 1), 3000))
    except Exception as e:
        return {"candidates": [], "skipped_already_registered": 0, "scanned": 0,
                "org_id": org, "all_orgs": cross,
                "hint": f"failure_log unavailable (run migration 112?): {e}"}
    try:
        q = client.schema("core").table("fix_requests").select("signature,status,org_id")
        if not cross:
            q = q.eq("org_id", org)
        reg_rows = q.limit(5000).execute().data or []
        hint = ""
    except Exception:
        reg_rows, hint = [], MIG_HINT
    kind_meta = _merge_kind_docs(_house_kind_docs(client))
    candidates, skipped = build_feed(rows, [r.get("signature") for r in reg_rows], kind_meta)
    return {"candidates": candidates, "skipped_already_registered": skipped, "scanned": len(rows),
            "registered_signatures": len(reg_rows), "org_id": org, "all_orgs": cross,
            "actor_kind": actor["kind"], "hint": hint}


@router.get("/requests")
async def list_pipeline_requests(org_id: str = ORG_ID, all_orgs: int = 0, status: str = "",
                                 classification: str = "", module_agent: str = "",
                                 date_from: str = "", date_to: str = "", limit: int = 500,
                                 authorization: str = Header(default=""),
                                 x_active_org: str = Header(default=""),
                                 x_fix_pipeline_secret: str = Header(default="")):
    """The BOARD payload. Filters (RULE FIVE: status / classification / module / period) narrow the rows,
    and the `rollup` tile is computed over exactly those rows so the tile, the table and the export can
    never disagree. cost_usd is recomputed from the CURRENT core.token_rates on every read, so editing a
    rate re-prices the whole board (the stored value is a cache, not the truth)."""
    actor = _authorize("registry_read", authorization=authorization, x_active_org=x_active_org,
                       secret=x_fix_pipeline_secret)
    org, cross = _scope(actor, org_id, all_orgs)
    client = sb()
    try:
        q = client.schema("core").table("fix_requests").select("*")
        if not cross:
            q = q.eq("org_id", org)
        if status:
            q = q.eq("status", status)
        if classification:
            q = q.eq("classification", classification)
        if module_agent:
            q = q.eq("module_agent", module_agent)
        if date_from:
            q = q.gte("created_at", date_from)
        if date_to:
            q = q.lte("created_at", date_to)
        rows = q.order("created_at", desc=True).limit(min(max(int(limit or 1), 1), 2000)).execute().data or []
        hint = ""
    except Exception:
        rows, hint = [], MIG_HINT
    # Price every row against ITS OWN org (so a tenant rate override applies on a cross-tenant page too),
    # from ONE rate query covering house + every org present.
    rates = _rate_rows(client, org, extra_orgs=[r.get("org_id") for r in rows])
    for r in rows:
        cost, basis = _price(r, rates, r.get("org_id") or org)
        r["cost_usd"] = cost
        r["cost_basis"] = basis
        r["tokens_total"] = tokens_total(r)
    return {"fix_requests": rows, "rollup": rollup(rows), "statuses": list(FIX_STATUSES),
            "classifications": list(FIX_CLASSIFICATIONS), "transitions":
                {k: list(v) for k, v in FIX_TRANSITIONS.items()},
            "org_id": org, "all_orgs": cross, "phase": 1,
            "approval_note": ("Phase 1: approval is given by the owner IN CHAT and recorded here by a "
                              "super-admin. There is no approve action in the app."),
            "cost_note": ("$ uses a BLENDED input/output rate from core.token_rates because agent "
                          "completion metadata reports one total token count, not an in/out split."),
            "hint": hint}


@router.get("/requests/{rid}")
async def get_pipeline_request(rid: str, org_id: str = ORG_ID, all_orgs: int = 0,
                              authorization: str = Header(default=""),
                              x_active_org: str = Header(default=""),
                              x_fix_pipeline_secret: str = Header(default="")):
    """One fix request WITH the folded core.failure_log rows — including `detail.traceback`, which is the
    whole point: the traceback existed in the DB since mig 112 but no UI ever rendered it (design §2a)."""
    actor = _authorize("registry_read", authorization=authorization, x_active_org=x_active_org,
                       secret=x_fix_pipeline_secret)
    org, cross = _scope(actor, org_id, all_orgs)
    client = sb()
    try:
        row = _fetch_request(client, rid, org, cross)
    except Exception:
        raise HTTPException(500, MIG_HINT)
    if not row:
        raise HTTPException(404, "fix request not found")
    row_org = row.get("org_id") or org        # price against the row's OWN org (tenant rate override)
    cost, basis = _price(row, _rate_rows(client, row_org), row_org)
    row["cost_usd"], row["cost_basis"] = cost, basis
    row["tokens_total"] = tokens_total(row)
    ids = [str(i) for i in (row.get("failure_ids") or []) if i][:200]
    failures = []
    if ids:
        from app.modules.core.router import _fetch_failures
        try:
            failures = _fetch_failures(client, org_id=(None if cross else org), reviewed="",
                                       ids=ids, limit=200)
        except Exception:
            failures = []
    return {"fix_request": row, "failures": failures,
            "tracebacks": [{"id": f.get("id"), "ref": _ref_of(f), "created_at": f.get("created_at"),
                            "message": f.get("message"), "traceback": _traceback_of(f)}
                           for f in failures],
            "org_id": org}


@router.post("/requests")
async def create_pipeline_request(body: dict, org_id: str = ORG_ID,
                                  authorization: str = Header(default=""),
                                  x_active_org: str = Header(default=""),
                                  x_fix_pipeline_secret: str = Header(default="")):
    """Register a problem (or FOLD another occurrence into the existing row for that signature).

    Dedupe is the contract: (org_id, signature) is UNIQUE, so a repeat POST for a known signature does NOT
    create a second row — it bumps occurrence_count, unions failure_ids/affected_orgs and refreshes the
    sample. That is what stops one bug spawning parallel duplicate builds (§3.5).

    A row can only be CREATED in a pre-build status (FIX_CREATE_STATUSES); the lifecycle is always walked
    through PATCH so every step lands in the audit trail. org_id is STAMPED on the insert (RULE ONE
    write side)."""
    actor = _authorize("registry_write", authorization=authorization, x_active_org=x_active_org,
                       secret=x_fix_pipeline_secret)
    org, _ = _scope(actor, org_id, 0)
    sig = (body.get("signature") or "").strip()
    if not sig:
        path, exc = body.get("sample_path") or body.get("path") or "", body.get("exc_type") or ""
        sig = fix_signature(path, exc) if (path or exc) else ""
    if not sig:
        raise HTTPException(422, "signature (or sample_path + exc_type) is required")
    status = str(body.get("status") or "reported").strip().lower()
    if status not in FIX_CREATE_STATUSES or status not in FIX_STATUSES:
        status = "reported"                      # default-DENY: never create at/after a build
    cls = str(body.get("classification") or "").strip().lower() or None
    if cls and cls not in FIX_CLASSIFICATIONS:
        raise HTTPException(422, f"classification must be one of {', '.join(FIX_CLASSIFICATIONS)}")
    ids = [str(i) for i in (body.get("failure_ids") or []) if i][:500]
    affected = body.get("affected_orgs")
    affected = affected if isinstance(affected, list) else []
    client = sb()
    try:
        existing = (client.schema("core").table("fix_requests").select("*")
                    .eq("org_id", org).eq("signature", sig).limit(1).execute().data) or []
    except Exception:
        raise HTTPException(500, MIG_HINT)
    now = _now_iso()
    if existing:
        cur = existing[0]
        merged_ids = list(dict.fromkeys([*(cur.get("failure_ids") or []), *ids]))[:500]
        counts = {a.get("org_id"): int(a.get("count") or 0)
                  for a in (cur.get("affected_orgs") or []) if isinstance(a, dict) and a.get("org_id")}
        for a in affected:
            if isinstance(a, dict) and a.get("org_id"):
                counts[a["org_id"]] = counts.get(a["org_id"], 0) + int(a.get("count") or 0)
        occ = int(body.get("occurrence_count") or 0) or max(len(merged_ids), 1)
        patch = {
            "occurrence_count": max(int(cur.get("occurrence_count") or 0), occ),
            "failure_ids": merged_ids,
            "affected_orgs": [{"org_id": o, "count": n} for o, n in counts.items()],
            "updated_at": now,
            "audit": _audit(cur, actor=actor["actor"], actor_kind=actor["kind"],
                            frm=cur.get("status"), to=cur.get("status"),
                            note=f"folded {len(ids)} more occurrence(s) into this signature"),
        }
        for f in ("sample_path", "exc_type", "title", "first_ref"):
            if not cur.get(f) and body.get(f):
                patch[f] = body[f]
        (client.schema("core").table("fix_requests").update(patch)
         .eq("org_id", org).eq("id", cur["id"]).execute())
        return {"ok": True, "id": cur["id"], "deduped": True, "signature": sig,
                "occurrence_count": patch["occurrence_count"], "status": cur.get("status")}
    row = {
        "org_id": org,                                  # RULE ONE: stamp the tenant on the INSERT
        "signature": sig,
        "first_ref": (body.get("first_ref") or None),
        "occurrence_count": max(int(body.get("occurrence_count") or 0) or len(ids) or 1, 1),
        "sample_path": (body.get("sample_path") or body.get("path") or None),
        "exc_type": (body.get("exc_type") or None),
        "failure_ids": ids,
        "affected_orgs": [a for a in affected if isinstance(a, dict) and a.get("org_id")],
        "title": (str(body.get("title") or sig)[:300]),
        "status": status,
        "classification": cls,
        "module_agent": (body.get("module_agent") or None),
        "triage_summary": (body.get("triage_summary") or None),
        "model": (body.get("model") or None),
        "created_by": actor["actor"],
        "created_at": now, "updated_at": now,
        "audit": [{"at": now, "actor": actor["actor"], "actor_kind": actor["kind"],
                   "from": None, "to": status, "note": "registered"}],
    }
    try:
        r = client.schema("core").table("fix_requests").insert(row).execute()
    except Exception as e:
        raise HTTPException(500, f"could not register the fix request — run migration 718 first: {e}")
    return {"ok": True, "id": (r.data[0]["id"] if r.data else None), "deduped": False,
            "signature": sig, "status": status}


# Fields a PATCH may set that are NOT the status (each is plain evidence/metadata about a parked build).
_PATCHABLE = ("classification", "module_agent", "branch", "commit_sha", "worktree", "triage_summary",
              "proofs_summary", "model", "title", "sample_path", "exc_type", "first_ref",
              "tokens_triage", "tokens_build", "tokens_review", "occurrence_count", "pushed_commit")
_INT_FIELDS = ("tokens_triage", "tokens_build", "tokens_review", "occurrence_count")


@router.patch("/requests/{rid}")
async def patch_pipeline_request(rid: str, body: dict, org_id: str = ORG_ID,
                                 authorization: str = Header(default=""),
                                 x_active_org: str = Header(default=""),
                                 x_fix_pipeline_secret: str = Header(default="")):
    """Advance the lifecycle and/or record evidence + per-stage token counts.

    EVERY call appends to the audit trail (who/what/when). The status move is validated by
    pipeline_status_change: 'pushed' only from 'approved'; 'approved'/'pushed' only for a super-admin
    browser session (the service secret can never approve or push); a money_touching row is never advanced
    into a build by automation. cost_usd is recomputed from core.token_rates whenever tokens or the model
    change — never from a constant."""
    actor = _authorize("registry_write", authorization=authorization, x_active_org=x_active_org,
                       secret=x_fix_pipeline_secret)
    org, _ = _scope(actor, org_id, 0)
    client = sb()
    try:
        cur = _fetch_request(client, rid, org, False)
    except Exception:
        raise HTTPException(500, MIG_HINT)
    if not cur:
        raise HTTPException(404, "fix request not found")

    patch = {}
    for f in _PATCHABLE:
        if f not in body:
            continue
        v = body[f]
        if f in _INT_FIELDS:
            try:
                v = max(int(v or 0), 0)
            except (TypeError, ValueError):
                raise HTTPException(422, f"{f} must be a whole number")
        elif f == "classification":
            v = str(v or "").strip().lower() or None    # "" clears it rather than storing a blank
            if v and v not in FIX_CLASSIFICATIONS:
                raise HTTPException(422, f"classification must be one of {', '.join(FIX_CLASSIFICATIONS)}")
        patch[f] = v

    target = str(body.get("status") or "").strip().lower()
    note = str(body.get("note") or "")
    if target:
        eff_class = patch.get("classification", cur.get("classification"))
        ok, why = pipeline_status_change(
            cur.get("status"), target, is_super_admin=bool(actor.get("super_admin")),
            actor_kind=actor["kind"], classification=eff_class,
            has_approval=bool(cur.get("approved_by") and cur.get("approved_at")))
        if not ok:
            raise HTTPException(403 if "super-admin" in why or "automation" in why else 409, why)
        patch["status"] = target
        if target == "approved":
            patch["approved_by"] = actor["actor"]
            patch["approved_at"] = _now_iso()
        if target == "pushed":
            patch["pushed_commit"] = (body.get("pushed_commit") or cur.get("pushed_commit")
                                      or patch.get("pushed_commit"))
            patch["pushed_at"] = _now_iso()

    if not patch:
        raise HTTPException(400, "nothing to update")

    # Re-price whenever the token counts or the model changed (rates always come from core.token_rates).
    if any(k in patch for k in ("tokens_triage", "tokens_build", "tokens_review", "model")):
        merged = {**cur, **patch}
        row_org = cur.get("org_id") or org
        cost, basis = _price(merged, _rate_rows(client, row_org), row_org)
        patch["cost_usd"] = cost
        patch["cost_basis"] = basis

    patch["updated_at"] = _now_iso()
    patch["audit"] = _audit(cur, actor=actor["actor"], actor_kind=actor["kind"],
                            frm=cur.get("status"), to=(target or cur.get("status")), note=note)
    try:
        (client.schema("core").table("fix_requests").update(patch)
         .eq("org_id", org).eq("id", rid).execute())
    except Exception as e:
        raise HTTPException(500, f"could not update the fix request: {e}")
    return {"ok": True, "id": rid, "status": patch.get("status", cur.get("status")),
            "cost_usd": patch.get("cost_usd", cur.get("cost_usd"))}


@router.get("/token-rates")
async def list_token_rates(org_id: str = ORG_ID, authorization: str = Header(default=""),
                           x_active_org: str = Header(default=""),
                           x_fix_pipeline_secret: str = Header(default="")):
    """The editable $/MTok rate table (super-admin only — the service secret has NO config capability).
    Also returns the models the registry has actually seen, so the editor is pick-don't-type (RULE THREE)
    rather than a free-text model box."""
    _authorize("config_read", authorization=authorization, x_active_org=x_active_org,
               secret=x_fix_pipeline_secret)
    client = sb()
    rows = _rate_rows(client, org_id or ORG_ID)
    models = []
    try:
        seen = (client.schema("core").table("fix_requests").select("model")
                .eq("org_id", org_id or ORG_ID).limit(2000).execute().data) or []
        models = sorted({str(r.get("model")).strip() for r in seen if r.get("model")})
    except Exception:
        models = []
    known = sorted({str(r.get("model")).strip() for r in rows if r.get("model")})
    for r in rows:
        r["blended_usd_per_mtok"] = blended_rate(r)
    return {"token_rates": sorted(rows, key=lambda r: (str(r.get("model") or ""),
                                                       str(r.get("effective_date") or ""))),
            "models_in_use": models, "models_known": known,
            "org_id": org_id or ORG_ID,
            "hint": "" if rows else MIG_HINT,
            "blend_note": ("Cost = total tokens x (input x (1 - output share) + output x output share). "
                           "Agent completion metadata reports ONE total token count, not an input/output "
                           "split, so the output share is an assumption you control per model.")}


@router.put("/token-rates")
async def upsert_token_rate(body: dict, org_id: str = ORG_ID, authorization: str = Header(default=""),
                            x_active_org: str = Header(default=""),
                            x_fix_pipeline_secret: str = Header(default="")):
    """Create/update ONE rate row, keyed by (org_id, model, effective_date) — so editing today's rate is an
    update while a future price change is a NEW dated row (rate history, no destructive edit).
    SUPER-ADMIN ONLY: the service secret is refused here, because a secret that can rewrite the rate table
    could silently misreport spend."""
    actor = _authorize("config_write", authorization=authorization, x_active_org=x_active_org,
                       secret=x_fix_pipeline_secret)
    model = str(body.get("model") or "").strip()
    if not model:
        raise HTTPException(422, "model is required")
    try:
        rin = float(body.get("usd_per_mtok_in"))
        rout = float(body.get("usd_per_mtok_out"))
    except (TypeError, ValueError):
        raise HTTPException(422, "usd_per_mtok_in and usd_per_mtok_out must be numbers")
    if rin < 0 or rout < 0:
        raise HTTPException(422, "rates cannot be negative")
    try:
        share = float(body.get("output_share", 0.20))
    except (TypeError, ValueError):
        raise HTTPException(422, "output_share must be a number between 0 and 1")
    if not (0.0 <= share <= 1.0):
        raise HTTPException(422, "output_share must be between 0 and 1")
    eff = str(body.get("effective_date") or date.today().isoformat())[:10]
    row = {"org_id": (org_id or ORG_ID),          # RULE ONE: stamp the org on the write
           "model": model, "label": (body.get("label") or None),
           "usd_per_mtok_in": rin, "usd_per_mtok_out": rout, "effective_date": eff,
           "output_share": share, "is_active": bool(body.get("is_active", True)),
           "notes": (body.get("notes") or None),
           "updated_by": actor["actor"], "updated_at": _now_iso()}
    try:
        (sb().schema("core").table("token_rates")
         .upsert(row, on_conflict="org_id,model,effective_date").execute())
    except Exception as e:
        raise HTTPException(500, f"could not save the rate — run migration 718 first: {e}")
    return {"ok": True, "model": model, "effective_date": eff,
            "blended_usd_per_mtok": blended_rate(row)}
