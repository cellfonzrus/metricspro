"""THE ONE HOME of "refresh the denied-appeal recovery ledger for an org" (index §COMMCALC-41.2).

The same shape as `payables/refresh.py`, for the sibling snapshot found by the same question: a stored ledger
rebuilt by a button nobody knew to press. The endpoint (`POST /recovery/rebuild`) and the post-landing hook
both call `refresh` here, so a live rebuild and a pressed rebuild cannot be two different rebuilds.

`last_run_at` on `appeal_recovery_config` is part of what a rebuild means — the Appeal Recovery screen reads
it — so it is stamped inside the one home rather than by whichever caller remembered to.
"""


def refresh(client, org_id):
    from app.modules.recovery import engine
    from app.modules.recovery import router as _rt
    try:
        cfg = _rt._get_cfg(client, org_id)
        summary = engine.build_recovery_ledger(client, org_id, cfg, _rt._today())
    except Exception as e:
        return {"status": "error", "error": f"{type(e).__name__}: {e}"}
    out = {"status": "done", "summary": summary}
    if isinstance(summary, dict):
        for k in ("rows", "written", "lines"):
            if isinstance(summary.get(k), int):
                out["written"] = summary[k]
                break
    try:
        (client.schema("commcalc").table("appeal_recovery_config")
         .update({"last_run_at": _rt._now_iso()}).eq("org_id", org_id).execute())
    except Exception as e:
        out["warnings"] = [f"the last-run stamp was not updated: {type(e).__name__}: {e}"]
    return out
