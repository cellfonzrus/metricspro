"""DM-verification AUDIT TRAIL — pure revision logic (owner directive 2026-09-02, mig 935).

The owner's words, verbatim: "under Dm verification when the dm changes the data in the field
after verifying the management is not able to see the modified data when exported after selecting
the date range, the user should be able to see the original data entered by the store, the picture
of the envelope and the modified data by the DM."

WHAT WAS ACTUALLY WRONG (measured 2026-09-02, live LuxeLink org 854f6d7b-…)
---------------------------------------------------------------------------
The ORIGINAL store-entered figures are NOT overwritten — they live untouched on the per-rep
`commcalc.daily_closing` rows, and the DM's corrections land in the SEPARATE `dm_*` columns of
`commcalc.daily_closing_verification` (mig 029; POST /closing/verify upserts them). Two real
defects remained:

  1. EXPORT VISIBILITY. The two date-range exports management uses never carried the DM's
     modified figures next to the originals:
       • GET /closing/submissions (the Daily Closing dashboard detail table + its export)
         returned only `dm_verified`/`dm_verified_by`/`dm_verified_at` from the verification row —
         the six `dm_*` corrected values and the DM note were fetched and then DROPPED;
       • GET /closing/summary (the DM Verify page + its "Store Summary" export) OVERLAYS the DM
         corrections onto the store totals in place (verified_overlay.apply_overlay), so the
         export showed ONE set of numbers — corrected when verified — and the store-entered
         ORIGINAL totals were lost from the payload.
     Both endpoints now return original AND modified side by side (`totals_original` on the
     summary; the `dm_*` fields on submissions), and both exports carry an envelope-photo link
     (`envelope_view_url` → GET /closing/envelope-view, which signs the private-bucket path on
     click — a list endpoint never does per-row Storage round trips).

  2. NO REVISION HISTORY. `daily_closing_verification` is an UPSERT — when the DM saved a second
     correction, the previous `dm_*` values were overwritten in place with no record of what
     changed, when, or by whom. Mig 935 adds the append-only
     `commcalc.daily_closing_verification_audit`; POST /closing/verify writes one revision row per
     save that changed anything (built by `build_audit_row` below). History is preserved going
     FORWARD; pre-935 saves are unrecoverable (nothing ever recorded them).

Everything in this module is PURE (dict in, dict out — no DB, no framework) so
backend/harness_dm_verification_audit.py proves it stdlib-only.
"""

# The six DM-corrected money figures, in the exact column spelling of BOTH tables
# (daily_closing_verification and its _audit twin).
DM_FIELDS = ("dm_store_cash", "dm_store_cc", "dm_epay_cash", "dm_epay_cc", "dm_acc_sale", "dm_other")
# Non-money fields whose change alone still deserves a revision row.
META_FIELDS = ("verified", "verified_by", "note")


def _money_or_none(v):
    """Normalize a money value for comparison: None stays None (field not set by the DM);
    everything else rounds to cents. Garbage → None (an unset field, never a phantom 0)."""
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return None


def _norm_meta(k, v):
    if k == "verified":
        return bool(v)
    return (str(v).strip() if v is not None else "") or ""


def changed_fields(prior, body):
    """PURE: which fields this save actually changes vs the prior verification row (None ⇒ first
    save for the store-day: every non-empty field in `body` counts). Money fields compare at
    cent precision with None ('DM left it blank') distinct from 0.00 ('DM set zero')."""
    out = []
    p = prior or {}
    for k in DM_FIELDS:
        new_v, old_v = _money_or_none(body.get(k)), _money_or_none(p.get(k))
        if prior is None:
            if new_v is not None:
                out.append(k)
        elif new_v != old_v:
            out.append(k)
    for k in META_FIELDS:
        new_v, old_v = _norm_meta(k, body.get(k)), _norm_meta(k, p.get(k))
        if prior is None:
            if new_v not in (False, ""):
                out.append(k)
        elif new_v != old_v:
            out.append(k)
    return out


def build_audit_row(org_id, body, prior):
    """PURE: the append-only revision row POST /closing/verify inserts, or None when the save
    changes nothing (an idle re-save never writes noise). Carries the NEW values, the PRIOR
    values, the changed-field list, and the owner's exact scenario as a flag:
    `edited_after_verify` = the store-day was ALREADY verified and this save changed a money
    figure — i.e. "the dm changes the data in the field after verifying"."""
    ch = changed_fields(prior, body)
    if not ch:
        return None
    p = prior or {}
    row = {
        "org_id": org_id,
        "close_date": body.get("close_date"),
        "store_code": body.get("store_code"),
        "store_name": body.get("store_name"),
        "verified": bool(body.get("verified")),
        "verified_by": body.get("verified_by"),
        "note": body.get("note"),
        "changed_fields": ch,
        "edited_after_verify": bool(p.get("verified")) and any(k in DM_FIELDS for k in ch),
        "first_revision": prior is None,
    }
    for k in DM_FIELDS:
        row[k] = _money_or_none(body.get(k))
        row["prior_" + k] = _money_or_none(p.get(k)) if prior is not None else None
    row["prior_verified"] = bool(p.get("verified")) if prior is not None else None
    row["prior_verified_by"] = p.get("verified_by") if prior is not None else None
    row["prior_note"] = p.get("note") if prior is not None else None
    return row


def submission_dm_fields(ver):
    """PURE: the verification-row fields GET /closing/submissions surfaces PER daily_closing row
    so the date-range export shows the DM's modified data next to the store's original entry.
    `dm_corrected` mirrors verified_overlay.has_correction (verified AND at least one dm_* set)."""
    v = ver or {}
    out = {("dm_" + k[3:] if not k.startswith("dm_") else k): _money_or_none(v.get(k)) for k in DM_FIELDS}
    out["dm_note"] = v.get("note")
    out["dm_corrected"] = bool(v.get("verified")) and any(v.get(k) is not None for k in DM_FIELDS)
    return out
