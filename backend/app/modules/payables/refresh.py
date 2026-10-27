"""THE ONE HOME of "refresh the device payable ledger for an org" (index §COMMCALC-41.2).

Before this module there was exactly one way to rebuild `commcalc.device_payable_ledger`: a person pressing
"↻ Rebuild ledger". Making the rebuild LIVE (owner 2026-10-27: *"the data should be live update as soon as
something changes"*) adds a second caller — the post-landing hook — and two callers rebuilding a ledger two
slightly different ways is the duplicate defect this house forbids. So the endpoint and the hook both call
`refresh` here, and neither knows how a rebuild is composed.

WHAT A REFRESH IS: `engine.build_ledger` (the per-carrier build) followed by `engine.sync_payable_flags` (the
discrepancy flags that read it). The flag sync was ALREADY part of what the button did — it is kept inside the
one home rather than restated at either call site.

WHAT THE INPUTS ARE: `input_tables` answers "which tables, landing, make this ledger stale" by reading the
DECLARED `payable_source_map` columns. It is the registry's `inputs_fn`, so declaring a new carrier — or
pointing one at a different feed — changes which landings refresh the ledger with no code change at all
(RULE TWO). The fixed inputs every carrier's build reads live in the registry beside it, named by identity.

A REFUSAL IS PROPAGATED, NEVER SWALLOWED, AND NEVER RETRIED. §PAYABLES-01 made the build refuse before it
deletes when the table cannot take the rows it would write. An automatic refresh that read that refusal as
"nothing to do" would restore exactly the silence that PR removed, so `blocked` travels out as
`status: "refused"` — which the hook records as a refusal and does not re-queue, because a block is a
schema fact and a retry cannot clear it. The discrepancy flags are not restated after a block either: they
read the ledger, and this run did not write it.

A FAILED REFRESH IS REPORTED, NEVER SWALLOWED. The return carries `status`, so the hook's `classify_build`
reads a verdict rather than inferring one from a row count (§COMMCALC-40): a build that legitimately wrote
zero rows is `done`, and a build that wrote zero because its insert failed is `error`. `engine.build_ledger`
is best-effort per carrier, so a per-carrier error is lifted into `warnings` here — the ledger is refreshed,
and the fact that one carrier did not is not lost in a log line.
"""
#: The columns of `commcalc.payable_source_map` that NAME A TABLE. Listed once, here, because this is the only
#: question anyone asks of them as a set; each is read individually by the engine at its own call site.
DECLARED_TABLE_COLUMNS = (
    "source_table",                 # the carrier's own per-device feed (asset_ledger / raw_ma_commission / …)
    "owed_price_table",             # the unit-price map a device's cost is looked up in (mig 1091)
    "sold_match_table",             # the feed that says a device left the shelf
    "reimbursement_match_table",    # the feed that says the rebate arrived (recovered_amount)
)


def input_tables(client, org_id):
    """Every table this org's payable builds DECLARE as a source. Raises on an unreadable config — the caller
    (`derived_builds.declared_inputs`) reports it rather than reading the silence as "nothing is declared"."""
    rows = (client.schema("commcalc").table("payable_source_map")
            .select(",".join(("carrier_id",) + DECLARED_TABLE_COLUMNS))
            .eq("org_id", org_id).eq("is_active", True).execute().data) or []
    out = []
    for r in rows:
        for col in DECLARED_TABLE_COLUMNS:
            t = str(r.get(col) or "").strip()
            if t and t not in out:
                out.append(t)
    return tuple(out)


def source_population(client, org_id, cfg):
    """How many rows the build SHOULD have written for this carrier: the source table's own count under the
    same filters the build applies (org, and `owed_field > 0` where the carrier declares one). Returns None
    when it cannot be counted — a number nobody could read is never reported as zero."""
    table = str(cfg.get("source_table") or "").strip()
    if not table:
        return None
    try:
        q = (client.schema("commcalc").table(table).select("org_id", count="exact")
             .eq("org_id", org_id).limit(1))
        owed = str(cfg.get("owed_field") or "").strip()
        if owed:
            q = q.gt(owed, 0)
        return q.execute().count
    except Exception:
        return None


def shortfall_warnings(client, org_id, built):
    """A BUILD THAT STOPPED PART-WAY AND REPORTED SUCCESS IS THE DEFECT, so the row count is checked against
    the population it was built from.

    Measured 2026-10-27: the 4 September build of the house ledger wrote exactly 11,000 rows — eleven pages of
    one thousand — and reported success. A per-carrier count that is short of its source population is the one
    cheap signal that says so, and it is checked HERE, outside the build, because a build cannot be the judge
    of whether it finished. A count that cannot be read yields no warning rather than a false one.
    """
    out = []
    try:
        cfgs = {str(r.get("carrier_id")): r for r in
                ((client.schema("commcalc").table("payable_source_map").select("*")
                  .eq("org_id", org_id).eq("is_active", True).execute().data) or [])}
    except Exception:
        return out
    for pc in (built.get("per_carrier") or []):
        if not isinstance(pc, dict) or pc.get("error") is not None or not isinstance(pc.get("rows"), int):
            continue
        cfg = cfgs.get(str(pc.get("carrier_id")))
        if not cfg:
            continue
        want = source_population(client, org_id, cfg)
        if want is None or pc["rows"] >= want:
            continue
        out.append(f"{cfg.get('label') or pc.get('carrier_id')}: the rebuild wrote {pc['rows']:,} rows but "
                   f"{want:,} rows in {cfg.get('source_table')} qualify — the build did not finish, so the "
                   f"figures for this carrier are incomplete. Do not read them as a smaller balance.")
    return out


def refresh(client, org_id, carrier_id=None):
    """Rebuild the payable ledger (all carriers, or one) and the flags that read it. THE one composition of a
    rebuild — the endpoint and the post-landing hook both arrive here."""
    from app.modules.payables import engine
    try:
        built = engine.build_ledger(client, org_id, carrier_id or None)
    except Exception as e:
        return {"status": "error", "error": f"{type(e).__name__}: {e}"}
    out = dict(built or {})
    warnings = [str(w) for w in (out.get("warnings") or [])]

    # A REFUSED REBUILD IS NOT A SUCCESS, AND NOT A RETRY EITHER (§PAYABLES-01, carried through here).
    # `blocked` names the carriers the write preflight refused BEFORE any delete — the database is behind
    # the code, so their rows are exactly as they were. Two consequences, and both matter more on the
    # automatic path than on the button:
    #   · the flags are NOT restated. They read the ledger, and a ledger this run did not write must not
    #     have its discrepancy flags recomputed off a refusal. That ordering lives HERE, in the one home
    #     that owns both steps, rather than in whichever caller remembered it.
    #   · `status` is `refused`, not `error`, so the hook records a REFUSAL and does not re-queue. A block
    #     is a schema fact: a retry cannot clear it, and retrying would burn the debounce window over and
    #     over until somebody applies a migration. The sentence an operator reads names the missing
    #     columns, which is the thing that actually has to happen.
    if out.get("blocked"):
        names = ", ".join(f"{b.get('label') or b.get('carrier_id')} "
                          f"({b.get('relation')}: {', '.join(b.get('missing_columns') or []) or 'see reason'})"
                          for b in out["blocked"] if isinstance(b, dict))
        out["status"] = "refused"
        out["error"] = (f"REFUSED: the database is behind the code, so the rebuild was not attempted for "
                        f"{names}. Nothing was deleted and every carrier is exactly as it was. Apply the "
                        f"migration that adds those columns, and this rebuilds itself on the next landing.")
        if warnings:
            out["warnings"] = warnings
        return out

    out["status"] = "done"
    for pc in (out.get("per_carrier") or []):
        if isinstance(pc, dict) and pc.get("error"):
            warnings.append(f"{pc.get('label') or pc.get('carrier_id')}: {pc['error']}")
    warnings += shortfall_warnings(client, org_id, out)
    try:
        out["flags_written"] = engine.sync_payable_flags(client, org_id)
    except Exception as e:
        out["flags_error"] = str(e)[:200]
        warnings.append(f"the discrepancy flags were not refreshed: {type(e).__name__}: {e}")
    if warnings:
        out["warnings"] = warnings
    return out
