"""Carrier payout-template CLONER — universal, config-driven (mig 221).

A tenant (e.g. Luxelink / Total) needs the SAME carrier payout config the house already built — the
Total Wireless carrier + its multi-month payout_schedule/_line curves + the product_mrc catalog
(mig 078). This module CLONES that config into the target org: same shape, NEW UUIDs, FKs remapped,
every row re-stamped with the TARGET org_id. It is a CLONE, not a MOVE — the house's own Total pipeline
(VidaPay MA ingest + the mig-078 payout engine) still references the house rows; a move would break
house-side Total processing. Cloning gives the tenant its own independently-editable copy.

DESIGN INVARIANTS
-----------------
- MULTI-TENANT: the target org_id is the QUERY PARAM (from the caller's JWT via tenant_middleware). The
  ONLY cross-org read in the whole module is reading a SHAREABLE source template — gated on
  `commcalc.carrier.template_shared = true` (mig 221). A source that is not template_shared is REFUSED.
  No other query crosses org boundaries.
- SAP-CONFIGURABLE: nothing about "Total" / "Luxelink" is hard-coded. The cloner works for ANY carrier
  any org has marked shareable; the target is any org. The one house-specific fact (which carrier is a
  template) lives as DATA (the template_shared flag), set by the migration's seed UPDATE, not in code.
- IDEMPOTENT + RE-RUNNABLE: matching target rows (carrier by name; schedule by its natural key
  (company_id, activation_type); product_mrc by (lower(plan_pattern), match_op)) are SKIPPED — never
  duplicated, never clobbered. A hand-edited tenant copy survives a re-clone untouched.
- MONEY-SAFE: cloning only CREATES config rows. It NEVER touches rep_commissions and NEVER fires a calc.
  Pay changes only when the owner recalcs a period afterwards. calculator.py / commission_engine.py are
  not imported here.
- dry_run: returns the full would-create / would-skip manifest and writes nothing. The real run returns
  the same manifest shape with the created ids filled in.
"""
import uuid as _uuid
from fastapi import HTTPException

_SCHEMA = "commcalc"


def _rows(res):
    return (getattr(res, "data", None) or []) if res is not None else []


def _mrc_key(m):
    return ((m.get("plan_pattern") or "").strip().lower(), (m.get("match_op") or "equals"))


def _sched_key(s):
    # carrier-level template natural key. company_id is org-specific → clones are always company-NULL,
    # so the key is (None, activation_type); we compare existing target rows on the same shape.
    return (s.get("company_id") or None, (s.get("activation_type") or "*"))


# ── /sources ─────────────────────────────────────────────────────────────────────────────────────
def list_shared_sources(client, target_org_id):
    """Every carrier ANY org has marked shareable (template_shared=true), with its schedule / line /
    product_mrc counts — the pick list for the importer. This is the deliberate cross-org read, gated
    ENTIRELY by template_shared. Degrades to an empty, ready=false payload (never 500) if mig 221 isn't
    applied yet — so the feature is simply unavailable, never a leak."""
    try:
        carriers = _rows(
            client.schema(_SCHEMA).table("carrier").select("*").eq("template_shared", True).execute()
        )
    except Exception:
        return {"sources": [], "ready": False,
                "note": "Run migration 221_commission_carrier_template_shared.sql to enable carrier-template sharing."}
    out = []
    for c in carriers:
        cid, coid = c.get("id"), c.get("org_id")
        try:
            scheds = _rows(client.schema(_SCHEMA).table("payout_schedule").select("id")
                           .eq("org_id", coid).eq("carrier_id", cid).execute())
            sids = [s.get("id") for s in scheds if s.get("id")]
            line_ct = 0
            if sids:
                line_ct = len(_rows(client.schema(_SCHEMA).table("payout_schedule_line").select("id")
                                    .eq("org_id", coid).in_("schedule_id", sids).execute()))
            mrc = _rows(client.schema(_SCHEMA).table("product_mrc").select("id")
                        .eq("org_id", coid).eq("carrier_id", cid).execute())
        except Exception:
            scheds, line_ct, mrc = [], 0, []
        out.append({
            "source_org_id": coid, "source_carrier_id": cid,
            "carrier_name": c.get("name"), "carrier_code": c.get("code"),
            "is_own": coid == target_org_id,
            "schedule_count": len(scheds), "line_count": line_ct, "product_mrc_count": len(mrc),
        })
    out.sort(key=lambda x: ((x.get("carrier_name") or "").lower(), x.get("source_org_id") or ""))
    return {"sources": out, "ready": True}


# ── /clone ───────────────────────────────────────────────────────────────────────────────────────
def clone_carrier_template(client, *, target_org_id, source_org_id, source_carrier_id, dry_run=False):
    """Clone a SHAREABLE source carrier's payout config into target_org_id. See module docstring for the
    invariants. Raises HTTPException(403) if the source is not shared, HTTPException(400) if sharing
    isn't enabled (mig 221 unrun) or params are missing."""
    if not source_org_id or not source_carrier_id:
        raise HTTPException(400, "source_org_id and source_carrier_id are required")

    # 1) verify the source is an EXPLICITLY shareable template. The .eq('template_shared', True) filter
    #    both ENFORCES the gate and naturally raises when the column is absent (mig 221 not yet run).
    try:
        shared = _rows(client.schema(_SCHEMA).table("carrier").select("*")
                       .eq("org_id", source_org_id).eq("id", source_carrier_id)
                       .eq("template_shared", True).execute())
    except Exception:
        raise HTTPException(400, "Carrier-template sharing is not enabled yet — run migration "
                                 "221_commission_carrier_template_shared.sql.")
    if not shared:
        raise HTTPException(403, "That carrier is not a shared template (or does not exist). Only a "
                                 "carrier explicitly marked shareable can be imported.")
    src_carrier = shared[0]
    carrier_name = src_carrier.get("name")

    # 2) resolve the target carrier by NAME within the target org (create-or-match).
    try:
        existing_carrier = _rows(client.schema(_SCHEMA).table("carrier").select("*")
                                 .eq("org_id", target_org_id).eq("name", carrier_name).execute())
    except Exception:
        existing_carrier = []
    target_carrier_id = existing_carrier[0].get("id") if existing_carrier else None
    carrier_action = "match" if existing_carrier else "create"

    # 3) load the source config for this carrier.
    src_scheds = _rows(client.schema(_SCHEMA).table("payout_schedule").select("*")
                       .eq("org_id", source_org_id).eq("carrier_id", source_carrier_id).execute())
    sid_list = [s.get("id") for s in src_scheds if s.get("id")]
    src_lines = []
    if sid_list:
        src_lines = _rows(client.schema(_SCHEMA).table("payout_schedule_line").select("*")
                          .eq("org_id", source_org_id).in_("schedule_id", sid_list).execute())
    lines_by_sched = {}
    for ln in src_lines:
        lines_by_sched.setdefault(ln.get("schedule_id"), []).append(ln)
    src_mrc = _rows(client.schema(_SCHEMA).table("product_mrc").select("*")
                    .eq("org_id", source_org_id).eq("carrier_id", source_carrier_id).execute())

    # 4) REAL RUN — create the target carrier NOW if missing (so skip-detection sees a real carrier id).
    counts = {"carriers_created": 0, "schedules_created": 0, "lines_created": 0,
              "product_mrc_created": 0, "schedules_skipped": 0, "product_mrc_skipped": 0,
              "schedules_company_skipped": 0}
    if carrier_action == "create" and not dry_run:
        target_carrier_id = str(_uuid.uuid4())
        client.schema(_SCHEMA).table("carrier").insert({
            "id": target_carrier_id, "org_id": target_org_id, "name": carrier_name,
            "code": src_carrier.get("code"), "is_default": False, "template_shared": False,
        }).execute()
        counts["carriers_created"] = 1

    # 5) existing target rows (for skip-detection). None when the target carrier doesn't exist yet.
    existing_sched_keys, existing_mrc_keys = set(), set()
    if target_carrier_id:
        try:
            for s in _rows(client.schema(_SCHEMA).table("payout_schedule").select("*")
                           .eq("org_id", target_org_id).eq("carrier_id", target_carrier_id).execute()):
                existing_sched_keys.add(_sched_key(s))
            for m in _rows(client.schema(_SCHEMA).table("product_mrc").select("*")
                           .eq("org_id", target_org_id).eq("carrier_id", target_carrier_id).execute()):
                existing_mrc_keys.add(_mrc_key(m))
        except Exception:
            pass

    # 6) plan schedule create/skip. company-scoped source schedules are NOT cloned cross-tenant
    #    (company_id is org-specific); clones are always company-NULL, carrier-level.
    sched_create_manifest, sched_skip_manifest, company_skip_manifest = [], [], []
    new_sched_rows, new_line_rows = [], []
    for s in src_scheds:
        at = s.get("activation_type") or "*"
        line_ct = len(lines_by_sched.get(s.get("id"), []))
        if s.get("company_id"):
            company_skip_manifest.append({"activation_type": at, "reason": "company-scoped (not cloned cross-tenant)"})
            counts["schedules_company_skipped"] += 1
            continue
        key = (None, at)
        if key in existing_sched_keys:
            sched_skip_manifest.append({"activation_type": at, "num_months": s.get("num_months"),
                                        "lines": line_ct, "reason": "already exists in target"})
            counts["schedules_skipped"] += 1
            continue
        new_sid = str(_uuid.uuid4())
        sched_create_manifest.append({"activation_type": at, "num_months": s.get("num_months"),
                                      "lines": line_ct, "id": (new_sid if not dry_run else None)})
        counts["schedules_created"] += 1
        counts["lines_created"] += line_ct
        new_sched_rows.append({
            "id": new_sid, "org_id": target_org_id, "company_id": None, "carrier_id": target_carrier_id,
            "activation_type": at, "num_months": s.get("num_months") or 1,
            "gate_signal": s.get("gate_signal") or "paid_residual",
            "bypass_tier": bool(s.get("bypass_tier", True)), "is_active": bool(s.get("is_active", True)),
        })
        for ln in lines_by_sched.get(s.get("id"), []):
            new_line_rows.append({
                "id": str(_uuid.uuid4()), "org_id": target_org_id, "schedule_id": new_sid,
                "month_index": ln.get("month_index"), "payout_kind": ln.get("payout_kind") or "flat",
                "flat_amount": ln.get("flat_amount") or 0, "mrc_pct": ln.get("mrc_pct") or 0,
                "mrc_basis": ln.get("mrc_basis") or "commissionable_mrc",
                "requires_paid": bool(ln.get("requires_paid")),
            })

    # 7) product_mrc create/skip.
    mrc_create_manifest, mrc_skip_manifest, new_mrc_rows = [], [], []
    for m in src_mrc:
        key = _mrc_key(m)
        entry = {"plan_pattern": m.get("plan_pattern"), "match_op": m.get("match_op") or "equals",
                 "mrc": m.get("mrc")}
        if key in existing_mrc_keys:
            mrc_skip_manifest.append({**entry, "reason": "already exists in target"})
            counts["product_mrc_skipped"] += 1
            continue
        new_mid = str(_uuid.uuid4())
        mrc_create_manifest.append({**entry, "id": (new_mid if not dry_run else None)})
        counts["product_mrc_created"] += 1
        new_mrc_rows.append({
            "id": new_mid, "org_id": target_org_id, "carrier_id": target_carrier_id,
            "plan_pattern": m.get("plan_pattern"), "match_op": m.get("match_op") or "equals",
            "mrc": m.get("mrc") or 0, "priority": m.get("priority") if m.get("priority") is not None else 100,
            "is_active": bool(m.get("is_active", True)), "note": m.get("note"),
        })

    # 8) REAL RUN — write the new rows (schedules, then lines, then mrc). Chunked, org-stamped.
    if not dry_run:
        for i in range(0, len(new_sched_rows), 200):
            client.schema(_SCHEMA).table("payout_schedule").insert(new_sched_rows[i:i + 200]).execute()
        for i in range(0, len(new_line_rows), 500):
            client.schema(_SCHEMA).table("payout_schedule_line").insert(new_line_rows[i:i + 500]).execute()
        for i in range(0, len(new_mrc_rows), 200):
            client.schema(_SCHEMA).table("product_mrc").insert(new_mrc_rows[i:i + 200]).execute()

    return {
        "ready": True,
        "dry_run": bool(dry_run),
        "source": {"org_id": source_org_id, "carrier_id": source_carrier_id, "carrier_name": carrier_name},
        "target_org_id": target_org_id,
        "carrier": {"name": carrier_name, "action": carrier_action, "id": target_carrier_id},
        "schedules": {"create": sched_create_manifest, "skip": sched_skip_manifest,
                      "company_skipped": company_skip_manifest},
        "lines": {"create": counts["lines_created"]},
        "product_mrc": {"create": mrc_create_manifest, "skip": mrc_skip_manifest},
        "counts": counts,
    }
