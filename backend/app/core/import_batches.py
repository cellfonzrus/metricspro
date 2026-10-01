"""DDIA Phase 1 — import batches and idempotency.

THE PROBLEM THIS SOLVES. Until migration 732 there was no file hash anywhere in the upload path, so
uploading the same ePay/B2B export twice inserted its rows twice and the money was double-counted
until somebody noticed. `commcalc.upload_log` (7,926 rows) and `commcalc.upload_trace` (10,630)
RECORD every upload richly but PREVENT nothing. The highest-risk caller is not a human clicking twice
— it is the FTP/email sweeps, which re-fetch an attachment whenever a prior run ended non-terminal.

THE GUARD. `core.import_batches` carries a partial UNIQUE index on `(org_id, file_sha256)
WHERE status <> 'failed'`. We claim the batch row BEFORE parsing: if the same bytes already loaded
for this org, the INSERT is rejected by the database and the caller skips the import entirely. The
index is partial so a genuine retry after a parse error still works — a `failed` batch does not block
its own retry. It is scoped per ORG because two tenants legitimately upload byte-identical files (the
same carrier template, an empty export), and one tenant's load must never block another's.

IT DEGRADES OPEN, AND THAT IS DELIBERATE. Every function here swallows its exceptions and returns
`unavailable`, letting the upload proceed exactly as it did before. If migration 732 has not been
applied, or PostgREST has not reloaded its schema cache, a fail-closed guard would silently block
every data import in the platform at month end. A guard that can take the business offline is worse
than the duplicate it prevents. The one thing we never do is claim a batch and then lie about it:
`claim()` returning `unavailable` means no row exists, so nothing downstream tries to complete it.

NOT BUILT YET (called out so nobody assumes otherwise):
  * `import_batch_id` exists as a NULLABLE column on twelve raw tables and is NOT yet stamped onto
    inserted rows. That is per-table lineage — DDIA Phase 5 — and it touches money-module insert
    paths, so it is propose-first. The duplicate guard is independent of it and works today.
  * `format_version` is a placeholder (`<source>_v1`), not a real format registry. The registry with
    expected-columns and a FormatMismatch error is DDIA Phase 3.
"""

import hashlib
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from app.core.database import get_supabase

log = logging.getLogger(__name__)

_SCHEMA = "core"
_TABLE = "import_batches"

# Result states returned by claim(). Callers only ever branch on these three strings.
CLAIMED = "claimed"
DUPLICATE = "duplicate"
UNAVAILABLE = "unavailable"


def sha256_hex(content: bytes) -> str:
    """The batch identity: the raw bytes, before any parsing or encoding guess."""
    return hashlib.sha256(content or b"").hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_duplicate_error(exc: Exception) -> bool:
    """A unique-index rejection, however PostgREST chose to spell it today.

    Checked structurally first (`code`), then by message, because the client wraps the Postgres error
    differently across versions and a missed match here would turn a duplicate into `unavailable` —
    i.e. it would let the double-import through. This is the one place worth being generous.
    """
    code = getattr(exc, "code", None) or (exc.args[0].get("code") if exc.args and isinstance(exc.args[0], dict) else None)
    if str(code) == "23505":
        return True
    msg = str(getattr(exc, "message", "") or exc).lower()
    return "23505" in msg or "duplicate key" in msg or "already exists" in msg


def _prior(org_id: str, file_sha256: str) -> Optional[Dict[str, Any]]:
    """The batch that already owns these bytes — for the message the operator actually reads."""
    try:
        rows = (get_supabase().schema(_SCHEMA).table(_TABLE)
                .select("id,source,file_name,period,row_count,status,created_at,completed_at")
                .eq("org_id", org_id).eq("file_sha256", file_sha256)
                .neq("status", "failed")
                .order("created_at", desc=True).limit(1).execute().data) or []
        return rows[0] if rows else None
    except Exception:
        return None


def claim(*, org_id: str, source: str, content: bytes, file_name: Optional[str] = None,
          period: str = "", uploaded_by: Optional[str] = None,
          force: bool = False) -> Dict[str, Any]:
    """Claim the right to import these bytes for this org, BEFORE parsing them.

    Returns one of:
      {"state": "claimed",     "batch_id": <uuid>, "sha256": …}   → proceed with the import
      {"state": "duplicate",   "sha256": …, "prior": {…}}         → skip; these bytes already loaded
      {"state": "unavailable", "sha256": …, "reason": …}          → proceed; the guard is not usable

    `force=True` supersedes the prior batch instead of refusing — the same override the comp_report
    period check already uses. It is a deliberate, operator-typed act: the prior batch is marked
    'superseded' (not deleted, so the history of what was loaded when survives) and a fresh batch is
    claimed. The rows the prior batch loaded are NOT removed — force means "load it again", and
    whether that is right is the operator's call, not this module's.
    """
    sha = sha256_hex(content)
    base = {"sha256": sha}
    try:
        client = get_supabase().schema(_SCHEMA).table(_TABLE)

        if force:
            try:
                client.update({"status": "superseded", "completed_at": _now()}) \
                      .eq("org_id", org_id).eq("file_sha256", sha).neq("status", "failed").execute()
            except Exception as e:  # superseding is best-effort; the insert below is what matters
                log.warning("import_batches: could not supersede prior batch for %s: %s", sha[:12], e)

        row = {
            "org_id": org_id,
            "source": source,
            "format_version": f"{source}_v1",
            "file_name": (file_name or "")[:400] or None,
            "file_sha256": sha,
            "file_bytes": len(content or b""),
            "status": "parsing",
            "period": period or None,
            "uploaded_by": uploaded_by,
        }
        try:
            res = client.insert(row).execute()
        except Exception as e:
            if _is_duplicate_error(e):
                return {**base, "state": DUPLICATE, "prior": _prior(org_id, sha)}
            raise

        data = (getattr(res, "data", None) or [])
        if not data:
            # An insert that returns nothing is not a claim we can complete later. Treat it as the
            # guard being unusable rather than pretending we hold a batch.
            return {**base, "state": UNAVAILABLE, "reason": "insert returned no row"}
        return {**base, "state": CLAIMED, "batch_id": data[0].get("id")}

    except Exception as e:
        # Table missing (PGRST205 before migration 732 lands), schema cache stale, network — all of it
        # degrades open on purpose. See the module docstring.
        log.warning("import_batches: guard unavailable for org=%s source=%s: %s", org_id, source, e)
        return {**base, "state": UNAVAILABLE, "reason": str(e)[:300]}


def complete(batch_id: Optional[str], *, row_count: Optional[int] = None) -> None:
    """Mark a claimed batch loaded. Never raises — a bookkeeping failure must not fail the upload."""
    if not batch_id:
        return
    try:
        (get_supabase().schema(_SCHEMA).table(_TABLE)
         .update({"status": "loaded", "row_count": row_count, "completed_at": _now()})
         .eq("id", batch_id).execute())
    except Exception as e:
        log.warning("import_batches: could not mark batch %s loaded: %s", batch_id, e)


def fail(batch_id: Optional[str], *, error: Optional[str] = None) -> None:
    """Mark a claimed batch failed, which RELEASES the hash for a genuine retry (the unique index is
    partial on `status <> 'failed'`). This is why the upload path must call it on every error path —
    a batch left in 'parsing' would block the corrected re-upload of the same file."""
    if not batch_id:
        return
    try:
        (get_supabase().schema(_SCHEMA).table(_TABLE)
         .update({"status": "failed", "error_detail": (error or "")[:2000] or None,
                  "completed_at": _now()})
         .eq("id", batch_id).execute())
    except Exception as e:
        log.warning("import_batches: could not mark batch %s failed: %s", batch_id, e)


# ── A BATCH LEFT IN 'parsing' IS A RUN THAT DIED (owner directive 2026-09-30) ─────────────────────
# Owner: *"Why do we have so many errors in sweeping data - this is the lifeline of our system - need
# to make it foolproof"*.
#
# MEASURED FIRST. September 2026: 9,835 batches, 9,789 `loaded`, 2 `failed` — 99.55% success. The
# pipeline is not fragile. What is wrong is that the 44 failures it DID have were SILENT: no
# `completed_at`, no `row_count`, no `error_detail`, just an eternal `parsing`. The alerts fire for
# transient connector blips that the next hourly run fixes, and stay quiet for these.
#
# WHY A `finally` CANNOT FIX IT, and this is the whole reason this reaper exists. The one claim site
# (`commcalc/router._upload_file`) ALREADY closes the batch out in a `finally`, on both the error and
# the success path. A `finally` does not run when the process is killed — a hung parse reaped by a
# request timeout, an OOM, a deploy mid-run, a worker restart. So no in-process guard, context manager
# or try/except can close these out: the code that would write the status is gone. The only mechanism
# that survives process death is an out-of-band sweep of rows left behind.
#
# AND IT IS NOT MERELY COSMETIC. The duplicate guard's unique index is partial on `status <> 'failed'`,
# so a batch stranded in `parsing` BLOCKS THE RE-IMPORT OF ITS OWN FILE for ever. Reaping it is what
# releases the hash and lets the next sweep land the same attachment.
#
# WHAT THIS DOES NOT DO, stated so nobody reads more into it: it does not stop parses dying. Live
# concentration says that is two specific sources — `inventory_aging` (38 of 73) and `daily_sales`
# (31 of 73) — which is a parser defect to diagnose on its own evidence, not something to paper over
# here. This makes the death VISIBLE and the retry POSSIBLE. That is all it claims.
REAP_AFTER_HOURS_DEFAULT = 6


def stale_parsing(rows, *, now, hours=REAP_AFTER_HOURS_DEFAULT):
    """PURE. [batch row] → [{id, age_hours, reason}] for the rows a dead run left behind.

    A row qualifies only when it is still `parsing` AND older than `hours`. The window exists so a
    parse that is genuinely still running is never killed by its own bookkeeping: `hours` must be
    comfortably longer than the slowest legitimate parse, which is why the default is 6 and not 1.

    `created_at` is the only clock available (a `parsing` row has no `completed_at` by definition).
    A row whose timestamp cannot be read is LEFT ALONE and reported, never reaped on a guess — the
    §19.26 rule that missing beats wrong, applied to a destructive-ish write.
    """
    out = []
    for r in (rows or []):
        if (r or {}).get("status") != "parsing":
            continue
        raw = (r or {}).get("created_at")
        age = _age_hours(raw, now)
        if age is None:
            out.append({"id": r.get("id"), "age_hours": None, "skipped": "unreadable created_at",
                        "reason": None})
            continue
        if age <= hours:
            continue
        out.append({
            "id": r.get("id"),
            "age_hours": round(age, 2),
            "reason": (
                "the run that claimed this batch never finished: still 'parsing' %.1fh after it was "
                "claimed (source=%s, file=%s). A killed process cannot write its own status, so this "
                "was reaped out of band. The file was NOT imported, and the hash is now released so "
                "the next run can retry it." % (age, r.get("source") or "?", r.get("file_name") or "?")
            ),
        })
    return out


def _age_hours(raw, now):
    """Hours between `raw` (ISO) and `now`, or None when the stamp is unreadable."""
    if not raw:
        return None
    try:
        t = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return (now - t).total_seconds() / 3600.0
    except Exception:
        return None


def reap_stale(*, hours=REAP_AFTER_HOURS_DEFAULT, limit=500, client=None):
    """Mark every batch a dead run left in `parsing` as `failed`, with the reason on the row.

    Returns {'reaped': n, 'skipped': n, 'ids': [...], 'unavailable': bool}. Never raises: this runs on
    a scheduler tick and a bookkeeping failure must not take the tick down. Degrades open exactly as
    the rest of this module does — an unreadable table means nothing was reaped, not a crash.
    """
    res = {"reaped": 0, "skipped": 0, "ids": [], "unavailable": False}
    try:
        tbl = (client or get_supabase()).schema(_SCHEMA).table(_TABLE)
        rows = (tbl.select("id,status,created_at,source,file_name")
                .eq("status", "parsing").limit(limit).execute().data) or []
    except Exception as e:
        log.warning("import_batches: could not read stale batches: %s", e)
        res["unavailable"] = True
        return res
    for item in stale_parsing(rows, now=datetime.now(timezone.utc), hours=hours):
        if item.get("skipped"):
            res["skipped"] += 1
            log.warning("import_batches: batch %s left alone — %s", item.get("id"), item["skipped"])
            continue
        fail(item["id"], error=item["reason"])
        res["reaped"] += 1
        res["ids"].append(item["id"])
    if res["reaped"]:
        log.warning("import_batches: reaped %d batch(es) stranded in 'parsing' beyond %sh",
                    res["reaped"], hours)
    return res


def duplicate_response(file_type: str, claim_result: Dict[str, Any]) -> Dict[str, Any]:
    """The upload result for a refused duplicate.

    Shaped for BOTH readers: `status='skipped'` + `rows=0` + the named marker `duplicate_file` that
    `_sweep_ingest_outcome` classifies as terminal (retrying identical bytes can only be refused
    again), and a `reason` string a human can act on.
    """
    prior = claim_result.get("prior") or {}
    when = str(prior.get("created_at") or "")[:19].replace("T", " ")
    what = prior.get("file_name") or "an earlier upload"
    rows = prior.get("row_count")
    detail = f"already imported{f' as {what}' if what else ''}{f' on {when} UTC' if when else ''}"
    if rows is not None:
        detail += f" ({rows:,} rows)"
    return {
        "status": "skipped",
        "skipped": "duplicate_file",
        "file_type": file_type,
        "rows": 0,
        "duplicate": True,
        "prior_batch": prior or None,
        "note": f"This exact file was {detail}. Nothing was inserted.",
        "reason": (f"This exact file was {detail}. Nothing was inserted — importing it again would "
                   f"double-count it. Re-upload with force=true if you genuinely need to load it twice."),
    }
