"""ADDITIVE flag persistence — a district manager's review must survive the nightly recalculation.

OWNER DIRECTIVE 2026-08-08, verbatim:
    "DM review should not be erased and teh new data should only add the missing data if any"

THE DEFECT THIS REMOVES
───────────────────────
`_run_calculation` DELETES every `commcalc.flags` row for the period and re-inserts the whole set, and
`_do_dlar_sweep` recalculates Boost DAILY. `reviewed_by` / `reviewed_at` / `action_taken` exist on the
table and nothing preserves them, so a manager's decision is gone within 24 hours. Migration 285/286
routed flags to the right DM; that only means anything if the decision survives the night.

The owner did NOT ask for review state to be saved and restored around the wipe. He asked for the wipe
to STOP. So: this module INSERTS what is missing and REFRESHES what is already there. A changed amount
updates the same row. The human decision is never written by the merge at all.

THE STALE-FLAG PROBLEM — "only ever add" creates it, so this module also solves it
─────────────────────────────────────────────────────────────────────────────────
If flags are only ever added, a flag whose condition later clears (the sale gets matched, the
discrepancy is corrected, the port-out is reversed) would persist forever as a false accusation against
a rep and the queue would only grow. Hard-deleting those is exactly the behaviour being removed, so
they are RETIRED IN PLACE instead: `status` leaves 'open', `resolved_at` / `resolved_reason` record
when and why, `reviewed_by` / `reviewed_at` / `action_taken` are untouched, and the default queue
filters them out. Nothing is destroyed; everything stays auditable. A condition that RETURNS reopens
the same row and keeps the prior review — re-accusing a manager who already ruled is the same erasure.

IDENTITY (mirrored byte-for-byte by `commcalc.flag_key_material` in migration 287)
─────────────────────────────────────────────────────────────────────────────────
    material = 'v1|<YYYYMM>|<FLAG_TYPE>|<source>|<ident>|<REP>|<STORE>'
    flag_key = md5(material + '#' + <ordinal within identical material>)

  ident   first non-empty of  imei → mdn → subscriber_id → source_ref.  imei/mdn come FIRST on
          purpose: they are already persisted on today's rows, so migration 287's backfill computes
          the SAME key this module will compute on the next run and those flags are adopted, not
          churned. `key_basis` records which identifier won.
  REP     part of the identity deliberately — a flag ACCUSES a person, so if the accused changes it is
          a NEW accusation and a manager's ruling on the old one must not carry across.
  STORE   participates ONLY when there is no row-level ident (the store/rep-level aggregate flag types).
          Keeping it out otherwise means adding a store alias cannot silently re-key an existing flag.
  amount / description / severity / coaching_note and every display column are NOT in the material, so
          a changed amount refreshes the SAME row instead of creating a second one.

WHAT CANNOT BE KEYED, stated plainly
────────────────────────────────────
`key_basis='none'` means the row carried no identifier of any kind — no imei, no mdn, no subscriber_id,
no source_ref, no rep and no store. Its key is an ordinal inside its flag_type/source group, which is
reproducible only from an unchanged source multiset: if the underlying report changes shape, such a
flag is retired as 'superseded' and a fresh one appears, and any review on it does not carry over.
These rows are counted, not papered over — `GET /commcalc/flags-key-health/{period}` reports them.

MULTI-TENANT (contract §2): `org_id` is a required parameter on every call, is in the predicate of both
RPCs, and is stamped on every insert. It is never part of the hash, so a key can never cross tenants.

SAP-CONFIGURABLE (contract §3): nothing here branches on a carrier, tenant, store or flag_type name.

💰 MOVES NO MONEY. Flags are visibility/accusation records. This module writes no amount basis, rate,
tier, plan, schedule or paid/earned column, and no payout path reads anything it writes.
"""

from __future__ import annotations

import hashlib
import json
import uuid as _uuid

# The lifecycle states. 'superseded' is bookkeeping (the row predates the additive era or its identity
# changed); 'resolved' means a previous additive run produced the flag and the latest one did not.
STATUS_OPEN = "open"
STATUS_RESOLVED = "resolved"
STATUS_SUPERSEDED = "superseded"

# Columns the merge is allowed to send. Deliberately EXCLUDES reviewed_by / reviewed_at / action_taken:
# the human decision is not part of the computed payload and the RPC never sets it.
_ROW_FIELDS = (
    "flag_key", "key_basis", "period", "period_month", "period_year",
    "flag_type", "source", "severity",
    "store_address", "store_code", "epay_salesperson",
    "mdn", "imei", "subscriber_id", "source_ref",
    "amount", "description", "coaching_note",
    "days_active", "phone_model", "customer_plan", "rebate_lost",
    "transaction_date", "activation_date",
)

_INT_FIELDS = ("period_month", "period_year", "days_active")
_NUM_FIELDS = ("amount", "rebate_lost")


def _md5(s: str) -> str:
    try:
        return hashlib.md5(s.encode("utf-8"), usedforsecurity=False).hexdigest()
    except TypeError:                              # Python < 3.9
        return hashlib.md5(s.encode("utf-8")).hexdigest()


def _up(v) -> str:
    return str(v if v is not None else "").strip().upper()


def _lo(v) -> str:
    return str(v if v is not None else "").strip().lower()


def period_canon(row) -> str:
    """'202606' from period_month/period_year — spelling-proof, so 'June 2026' and '2026-06' produce
    the SAME identity. Falls back to the upper/trimmed raw string when the row has no month/year
    (never true for a commcalc-written flag, but foreign writers exist)."""
    try:
        mo = int(row.get("period_month") or 0)
        yr = int(row.get("period_year") or 0)
    except (TypeError, ValueError):
        mo = yr = 0
    if 1 <= mo <= 12 and yr > 0:
        return f"{yr:04d}{mo:02d}"
    return _up(row.get("period"))


def ident_of(row) -> str:
    """The row's strongest stable identifier. Order matters — see the module docstring."""
    for f in ("imei", "mdn", "subscriber_id", "source_ref"):
        v = _up(row.get(f))
        if v:
            return v
    return ""


def key_basis(row) -> str:
    """Which identifier the key rests on: imei|mdn|subscriber|ref|rep|store|none."""
    for f, label in (("imei", "imei"), ("mdn", "mdn"),
                     ("subscriber_id", "subscriber"), ("source_ref", "ref")):
        if _up(row.get(f)):
            return label
    if _up(row.get("epay_salesperson")):
        return "rep"
    if _up(row.get("store_address")) or _up(row.get("store_code")):
        return "store"
    return "none"


def _material(row) -> str:
    """The identity material. MUST stay byte-identical to `commcalc.flag_key_material` (mig 287);
    harness_flag_review_persistence.py section E asserts that against the live database."""
    ident = ident_of(row)
    store = "" if ident else (_up(row.get("store_address")) or _up(row.get("store_code")))
    return "|".join((
        "v1",
        period_canon(row),
        _up(row.get("flag_type")),
        _lo(row.get("source")),
        ident,
        _up(row.get("epay_salesperson")),
        store,
    ))


def _payload_digest(row) -> str:
    """A total, deterministic ordering over rows that share the same material, so the #2/#3 ordinals
    inside a collision group are assigned the same way on every run rather than by dict iteration
    order. Rows in such a group are identical in IDENTITY and differ only in display payload."""
    try:
        return _md5(json.dumps({k: row.get(k) for k in sorted(row.keys())},
                               sort_keys=True, default=str))
    except Exception:
        return _md5(repr(sorted((str(k), str(v)) for k, v in row.items())))


def assign_keys(rows) -> dict:
    """Stamp `flag_key` + `key_basis` on every row, IN PLACE. Returns a basis histogram.

    Rows that share a material (genuinely interchangeable accusations — e.g. two chargebacks on the
    same line in one month) are ordered by their payload digest and numbered 1..N, which keeps the
    assignment stable while the source set is unchanged."""
    groups: dict[str, list] = {}
    for r in rows or []:
        r["key_basis"] = key_basis(r)
        groups.setdefault(_material(r), []).append(r)
    hist: dict[str, int] = {}
    for mat, grp in groups.items():
        if len(grp) > 1:
            grp = sorted(grp, key=_payload_digest)
        for i, r in enumerate(grp, start=1):
            r["flag_key"] = _md5(f"{mat}#{i}")
            hist[r["key_basis"]] = hist.get(r["key_basis"], 0) + 1
    return hist


def _clean(row) -> dict:
    """Project a computed flag onto the columns the RPC accepts, JSON-safe."""
    out = {}
    for f in _ROW_FIELDS:
        v = row.get(f)
        if f in _INT_FIELDS:
            try:
                v = int(v) if v not in (None, "") else None
            except (TypeError, ValueError):
                v = None
        elif f in _NUM_FIELDS:
            try:
                v = float(v) if v not in (None, "") else None
            except (TypeError, ValueError):
                v = None
        elif v is not None and not isinstance(v, str):
            v = str(v)
        out[f] = v
    return out


class FlagPersistUnavailable(RuntimeError):
    """Migration 287 has not been applied — the caller falls back to its previous write path."""


def sync(client, org_id: str, rows, *, periods, sources, reason: str = "",
         chunk: int = 500, run_id: str = None) -> dict:
    """The additive merge. Insert missing, refresh existing, retire what is no longer produced.

    `periods`  every spelling of the period being recalculated (pass `_pvariants(period)`), used ONLY
               to bound the retire step.
    `sources`  the `source` values THIS writer owns. The retire step is scoped to them, so a commcalc
               recalculation can never retire an asset / payables / closing / account flag it did not
               produce — strictly safer than today's wholesale per-period DELETE.

    Raises FlagPersistUnavailable when the RPCs are missing, so the caller can degrade to its old path
    (contract §5: a feature that needs SQL must degrade gracefully until the SQL is run)."""
    if not org_id:
        raise ValueError("flag_persist.sync: org_id is required (multi-tenant rule)")
    rows = list(rows or [])
    rid = run_id or str(_uuid.uuid4())
    basis_hist = assign_keys(rows)

    totals = {"run_id": rid, "computed": len(rows), "inserted": 0, "updated": 0,
              "reopened": 0, "resolved": 0, "superseded": 0, "key_basis": basis_hist}

    for i in range(0, len(rows), chunk):
        payload = [_clean(r) for r in rows[i:i + chunk]]
        try:
            res = client.schema("commcalc").rpc(
                "flags_sync_batch",
                {"p_org_id": org_id, "p_run_id": rid, "p_rows": payload},
            ).execute()
        except Exception as e:
            if _looks_missing(e):
                raise FlagPersistUnavailable(str(e))
            raise
        d = res.data if isinstance(getattr(res, "data", None), dict) else {}
        totals["inserted"] += int(d.get("inserted") or 0)
        totals["updated"] += int(d.get("updated") or 0)
        totals["reopened"] += int(d.get("reopened") or 0)

    try:
        res = client.schema("commcalc").rpc(
            "flags_resolve_stale",
            {"p_org_id": org_id, "p_run_id": rid,
             "p_periods": list(periods or []), "p_sources": list(sources or []),
             "p_reason": reason or "the condition was not present in the latest recalculation"},
        ).execute()
        d = res.data if isinstance(getattr(res, "data", None), dict) else {}
        totals["resolved"] = int(d.get("resolved") or 0)
        totals["superseded"] = int(d.get("superseded") or 0)
    except Exception as e:
        if _looks_missing(e):
            raise FlagPersistUnavailable(str(e))
        raise

    return totals


def _looks_missing(e) -> bool:
    """True when the failure is 'migration 287 has not run', not a real error. Kept narrow on purpose:
    a genuine bug inside the RPC must NOT be swallowed into the legacy delete-then-insert path."""
    s = str(e).lower()
    return ("could not find the function" in s or "does not exist" in s
            or "pgrst202" in s or "42883" in s or "undefined function" in s)
