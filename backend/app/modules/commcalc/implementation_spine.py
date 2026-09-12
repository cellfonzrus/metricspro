"""THE TENANT IMPLEMENTATION SPINE — one ordered flow, scoped to the carriers the tenant runs. PURE.

OWNER COMPLAINT 2026-09-12, verbatim:
    "Similar to the Cash Deposit Workflow, we need to organize the set up of a new tenant in an
     organized way, right now we have too many modules which do not have a flow and one thing leads
     to the other by links on their respective pages to a different module altogether, the
     implementation wizard should only give options relevant to the carrier they are working with
     with an option to add a carrier and then surfacing their respective upload links and automation
     links, the automation links could be linked to the upload links."

THE COMPLAINT IS THE SIBLING WIZARDS, SO THIS ADDS NONE. There were already five setup wizards
(/pos/onboarding, /vision/onboarding, /commcalc/onboarding, /commcalc/upload/wizard, /hr/onboarding)
plus /commcalc/implementation. A sixth would be the defect, not the fix. `/commcalc/onboarding` is
ALREADY the adaptive questionnaire whose answers tailor which later steps appear, with
`onboarding_state` persistence — so it is THE SPINE and this module is the ordering + carrier-scoping
it was missing. `/commcalc/implementation` becomes a STAGE inside that flow rather than a sibling
linked from it.

WHY A PURE MODULE AND NOT MORE ROUTER CODE. Two readers must agree on one order:
  · the backend (this file) — which steps the wizard emits, and in which sequence; and
  · frontend/src/lib/flowcharts.tsx — the runbook the user is TRAINED on and the `WorkflowNext`
    prompt at the foot of each screen.
That is a cross-LANGUAGE duplication that neither `tsc` nor a Python linter can see drifting, which
is precisely the failure `harness_workflow_stacking.py` §A exists to stop between the closing tile
and its TypeScript. `backend/harness_tenant_implementation.py` §A is the equivalent pin here: it
parses SPINE out of this file and the runbook stages out of the TSX and requires them equal, in
order. Being DB-free and stdlib-only is what makes that provable offline.

RULE TWO — CONFIG, NEVER CODE. Not one carrier, vendor, processor or tenant name appears in this
module, and none may. "Which uploads and automations belong to a carrier" is answered entirely by
rows the platform already has:

  · `commcalc.carrier`                (mig 038) — the carriers this tenant runs. Adding one is a row
                                       via the EXISTING `POST /commcalc/carriers`, never code.
  · `commcalc.report_definitions`      (mig 039) — every report that exists, with `carrier_id`
                                       (mig 291) saying whose it is and `upload_endpoint` saying
                                       where it is uploaded by hand.
  · `commcalc.connector_instances`     (mig 039) — every vendor portal, i.e. THE AUTOMATION, with its
                                       own `carrier_id`, `sweep_kind`, `enabled` and `automatable`.

AND THAT IS ALSO WHERE THE OWNER'S LAST CLAUSE IS ALREADY ANSWERED. "The automation links could be
linked to the upload links" needs no new table and no new join: `report_definitions.connector_id`
ALREADY points a report at the connector that fetches it. The binding has existed since mig 039 and
had simply never been rendered. Inventing a second one would be the drift the house rules forbid.

HOUSE DEFAULTS, TENANT OVERRIDES. The seeded rows live under the house org
(`00000000-0000-0000-0000-000000000001`); a tenant's own rows are read in place of them by every
caller, which is the inheritance shape `report_pull_map` and `connector_route_policy` already use.
A carrier a tenant does not run is not branched around — its rows simply do not match.

NEVER SILENT. A carrier with no reports registered is REPORTED as exactly that, with the action that
fixes it, rather than being dropped from the flow. An empty flow that looks complete is the
confident-but-wrong statement this codebase avoids; "nothing is registered for this carrier yet" is
the true and useful thing to say.
"""

# ── THE ORDER ───────────────────────────────────────────────────────────────────────────────────
# One definition, pinned against the TypeScript runbook by harness_tenant_implementation.py §A.
#
# WHY THIS ORDER. It is the order the work can actually be done in, and each step exists because the
# next one cannot start without it:
#   1 you cannot scope anything to a carrier before the tenant says which carriers they run;
#   2 you cannot map a report before the registry knows the report exists and who fetches it;
#   3 you cannot match a store name before rows carrying store names have landed;
#   4 you should not automate a feed before ONE file has been mapped and proven to land — turning on
#     a sweep whose columns are unmapped schedules a recurring failure, so "prove it by hand once,
#     then automate" is deliberate and is why automation sits AFTER the first ingest, not beside it;
#   5 you cannot pay anyone off data that is not yet arriving.
#
# `href` is a NAV href in frontend/src/lib/rbac.ts, because the prompt that offers it gates on the
# destination's OWN nav entry (the shared `useCanOpen`). An href with no NAV entry would gate to
# false for every viewer and silently render as plain text — so it is refused rather than guessed.
SPINE = [
    {
        "key": "carriers",
        "href": "/commcalc/onboarding",
        "label": "Setup Wizard",
        "who": "Implementation lead",
        "does": "Say who this tenant is and which carriers they sell. Add a carrier here.",
        "handoff": "the carrier list every step below is scoped to",
    },
    {
        "key": "connectors",
        "href": "/commcalc/connectors",
        "label": "Connectors",
        "who": "Implementation lead",
        "does": "Register each carrier's source portal and the reports it provides.",
        "handoff": "a named source for each report, and which of them can be automated",
    },
    {
        "key": "mapping",
        "href": "/commcalc/implementation",
        "label": "Implementation Wizard",
        "who": "Implementation lead",
        "does": "Per carrier: map each report's columns, then load the first file by hand.",
        "handoff": "mapped columns and one proven ingest per report",
    },
    {
        "key": "store_match",
        "href": "/commcalc/store-match",
        "label": "Store Matching",
        "who": "Implementation lead",
        "does": "Attach the store names the feed uses to the tenant's own stores.",
        "handoff": "rows that land under a named store instead of nowhere",
    },
    {
        "key": "automation",
        "href": "/commcalc/email-imports",
        "label": "Email & Portal Logins",
        "who": "Implementation lead",
        "does": "Turn on the automation for each report you just proved, so the file arrives by itself.",
        "handoff": "feeds that keep arriving without anyone uploading",
    },
    {
        "key": "pay",
        "href": "/commcalc/commission-plans",
        "label": "Incentive Plans",
        "who": "Owner / Implementation lead",
        "does": "Define how people are paid from the data that is now flowing.",
    },
]

SPINE_HREFS = [s["href"] for s in SPINE]
SPINE_KEYS = [s["key"] for s in SPINE]

# The one screen that can map AND import ANY registered report key (it posts to /upload-mapped, the
# documented any-carrier ingest route). It is therefore the correct fallback when a report declares
# no usable upload route of its own — never a guessed per-vendor page.
MAPPING_HREF = "/commcalc/implementation"
# Where an automation is configured. One page owns the connector registry, so this needs no per-vendor
# lookup table — which would be a code branch on a vendor name in all but spelling.
AUTOMATION_HREF = "/commcalc/connectors"


def _s(v):
    """A trimmed string from anything, including None. Never raises."""
    return ("" if v is None else str(v)).strip()


def carrier_visible(row, carriers):
    """Is this registry row shown to a tenant running `carriers`?

    THE ONE PREDICATE. `router._carrier_visible` delegates here rather than keeping a second copy —
    the sweep, the Connectors page and this flow must never disagree about whose report a row is.

    A row with no `carrier_id` is CARRIER-AGNOSTIC (distributor invoices, POS inventory, daily
    closing are not carrier concepts) and always shows. An empty `carriers` means the carrier lookup
    failed or the tenant has registered none — read as "do not filter", because a lookup failing must
    never hide a report somebody needs to upload.
    """
    cid = (row or {}).get("carrier_id")
    if not cid or not carriers:
        return True
    return cid in carriers


def upload_href(rdef):
    """Where this report is uploaded BY HAND.

    `report_definitions.upload_endpoint` is operator-entered free text and live rows carry three
    shapes: a rooted path ('/commcalc/ma-upload'), an unrooted fragment ('commcalc/upload/sales'),
    and a non-route token ('custom'). Only a ROOTED PATH is treated as a destination; anything else
    falls back to the one screen that can map and import any registered report key.

    Guessing a route by prefixing a '/' onto a fragment would manufacture an href that may not exist
    — the same refusal ScreenLink makes ("an href with no NAV entry is refused rather than guessed").
    """
    ep = _s((rdef or {}).get("upload_endpoint"))
    if ep.startswith("/") and len(ep) > 1:
        return ep
    return MAPPING_HREF


def automation_for(rdef, conn_by_id):
    """THE BINDING the owner asked for: the automation that feeds THIS upload.

    `report_definitions.connector_id` -> `connector_instances`. Existing config, mig 039; nothing new
    is stored and no second join is invented.

    Returns None when the report names no connector — that is a real, reportable state ("no source
    registered"), not something to paper over with a link to a page that cannot help.
    """
    cid = (rdef or {}).get("connector_id")
    if not cid:
        return None
    c = (conn_by_id or {}).get(cid)
    if not c:
        return None
    automatable = c.get("automatable") is not False
    enabled = bool(c.get("enabled")) and automatable
    return {
        "connector_id": cid,
        "vendor": _s(c.get("label")) or _s(c.get("vendor_name")),
        "sweep_kind": _s(c.get("sweep_kind")) or None,
        "href": AUTOMATION_HREF,
        "automatable": automatable,
        "enabled": enabled,
        # `auto` is the REPORT's own pull flag; a connector can be on while one of its reports is not.
        "report_auto": bool((rdef or {}).get("auto")),
        "state": ("on" if enabled and bool((rdef or {}).get("auto"))
                  else "off" if automatable else "manual_only"),
        # Said plainly, because "manual only" is a vendor instruction or a blocked 2FA, not a fault
        # the operator can fix by clicking harder.
        "note": (None if automatable else
                 "This source cannot be automated — its reports arrive by upload or email."),
    }


def feed_items(carrier_id, defs, conn_by_id, readiness=None, mapped_reports=None):
    """Every report registered for ONE carrier, each paired with the automation that feeds it.

    `readiness` is the EXISTING `/column-mapping/readiness` shape ({report_key: {required,
    required_mapped, ready}}) — reused rather than recomputed, so the flow and the Implementation
    Wizard can never disagree about whether a report is mapped.

    `mapped_reports` is the set of report keys that have at least one saved column_mapping rule. It
    is the honest signal for PHASE 3's question ("which sample reports did this carrier supply, and
    how confidently did each map?") without a new table: a report with rules has had a sample put
    through it; `required_mapped / required` is the confidence. Nothing here acts on that yet.
    """
    readiness = readiness or {}
    mapped_reports = mapped_reports or set()
    out = []
    for d in (defs or []):
        if _s(d.get("carrier_id")) != _s(carrier_id):
            continue
        rk = _s(d.get("report_key"))
        if not rk:
            continue
        r = readiness.get(rk) or {}
        req = int(r.get("required") or 0)
        got = int(r.get("required_mapped") or 0)
        out.append({
            "report_key": rk,
            "label": _s(d.get("label")) or rk,
            "target_table": _s(d.get("target_table")) or None,
            # `fallback_href` is not belt-and-braces: `upload_endpoint` is operator-entered and some
            # live rows hold an API route rather than a page (mig 1004 seeds
            # '/commcalc/upload-mapped', which is the ingest endpoint, not a screen). The UI gates
            # the primary on its own NAV entry and falls back to the screen that can map and import
            # any registered report key — so a row pointing somewhere unopenable still gets the user
            # to a place that works, instead of a greyed-out button.
            "upload": {"href": upload_href(d), "fallback_href": MAPPING_HREF, "label": "Upload"},
            "automation": automation_for(d, conn_by_id),
            "mapping": {
                "required": req,
                "required_mapped": got,
                "ready": bool(r.get("ready")),
                # Phase-3 hook: a sample has been through this report's mapper.
                "sample_seen": rk in mapped_reports,
                "confidence": (round(got / req, 3) if req else None),
            },
            "sort_order": int(d.get("sort_order") or 100),
        })
    out.sort(key=lambda x: (x["sort_order"], x["label"].lower()))
    return out


def carrier_blocks(carriers, defs, conns, readiness=None, mapped_reports=None):
    """One block per carrier the tenant RUNS — the carrier-scoped half of the flow.

    `carriers` is the `commcalc.carrier` rows. A carrier with nothing registered still gets a block
    saying so: dropping it would make an unfinished implementation look finished.

    Carrier-agnostic rows (`carrier_id` NULL — distributor, POS, closing) are returned ONCE in their
    own block rather than repeated under every carrier, because repeating them would tell a
    two-carrier tenant to upload the same file twice.
    """
    conn_by_id = {c.get("id"): c for c in (conns or []) if c.get("id")}
    blocks = []
    for c in (carriers or []):
        cid = c.get("id")
        items = feed_items(cid, defs, conn_by_id, readiness, mapped_reports)
        blocks.append({
            "carrier_id": cid,
            "carrier_name": _s(c.get("name")),
            "carrier_code": _s(c.get("code")) or None,
            "is_default": bool(c.get("is_default")),
            "items": items,
            "ready": sum(1 for i in items if i["mapping"]["ready"]),
            "total": len(items),
            "automated": sum(1 for i in items
                             if (i["automation"] or {}).get("state") == "on"),
            "empty_reason": (None if items else
                             "No reports are registered for this carrier yet."),
            "empty_next": (None if items else
                           "Register the reports this carrier provides on Connectors."),
        })
    shared = feed_items(None, defs, conn_by_id, readiness, mapped_reports)
    if shared:
        blocks.append({
            "carrier_id": None,
            "carrier_name": "Every carrier",
            "carrier_code": None,
            "is_default": False,
            "items": shared,
            "ready": sum(1 for i in shared if i["mapping"]["ready"]),
            "total": len(shared),
            "automated": sum(1 for i in shared
                             if (i["automation"] or {}).get("state") == "on"),
            "empty_reason": None,
            "empty_next": None,
            "shared": True,
        })
    return blocks


def carrier_options(carriers):
    """The 'Carrier(s) you sell' pick-list, FROM CONFIG.

    It used to be the literal list ["Boost", "Total", "Other"] in the step catalog — a carrier name
    in code, i.e. a RULE TWO violation, and one that could never satisfy "only steps relevant to the
    carrier appear" because the answers were free text unrelated to `commcalc.carrier`. The options
    are now the tenant's own carrier rows, so the answer IS a carrier the rest of the flow can scope
    on, and adding one is `POST /commcalc/carriers` rather than an edit to this file.
    """
    return [{"id": c.get("id"), "label": _s(c.get("name")),
             "code": _s(c.get("code")) or None, "is_default": bool(c.get("is_default"))}
            for c in (carriers or []) if _s(c.get("name"))]


def spine_progress(blocks):
    """Flow-level counts. Honest about the difference between 'nothing to do' and 'nothing set up'."""
    total = sum(b["total"] for b in blocks)
    return {
        "carriers": sum(1 for b in blocks if not b.get("shared")),
        "feeds": total,
        "feeds_mapped": sum(b["ready"] for b in blocks),
        "feeds_automated": sum(b["automated"] for b in blocks),
        "carriers_without_feeds": [b["carrier_name"] for b in blocks
                                   if not b.get("shared") and not b["total"]],
    }


def upload_scope_map(defs, conns, carriers, carrier_code=None):
    """WHICH UPLOAD TILES BELONG TO WHICH CARRIER — as data, replacing a TypeScript union.

    OWNER 2026-09-12, on a real Verizon RQ commission file: a new tenant could not be offered their
    own carrier's report at all, because `commcalc/upload/page.tsx` typed its tiles
    `carrier?: 'boost' | 'total'` — a literal union of two carrier names. Any third carrier was
    unrepresentable, which is RULE TWO failing in the most concrete way possible: not a style
    problem, a tenant who cannot upload their file.

    The replacement is the mechanism mig 291 was built for. This returns, per registry key, the
    carrier that owns it:
      · `report_definitions.report_key` -> its `carrier_id`  (the manual upload tiles)
      · `connector_instances.sweep_kind` -> its `carrier_id` (the auto-import sources)
    Both are rows the platform already keeps, both are per-tenant, and a THIRD carrier needs no code.

    `carrier_code` is injected (report_labels.normalize_carrier_code) rather than implemented here:
    that function already MIRRORS the frontend's `rbac.carrierCode()`, and a second spelling of
    "which code is this carrier" would let the page's active-carrier lens and the backend disagree.

    A KEY THAT IS NOT REGISTERED IS ABSENT FROM THIS MAP, and the caller must read that as
    carrier-agnostic — show it. That is the same refusal `carrier_visible` makes: failing to classify
    a tile must never hide a report somebody needs to upload. It is also why this cannot silently
    "clean up" the page — an unregistered tile stays visible until a row says otherwise.
    """
    code = carrier_code or (lambda v: _s(v).lower())
    by_id = {}
    for c in (carriers or []):
        if c.get("id"):
            by_id[c["id"]] = {"carrier_id": c["id"], "carrier_name": _s(c.get("name")),
                              "carrier_code": code(_s(c.get("code")) or _s(c.get("name")))}
    out = {}
    for d in (defs or []):
        rk = _s(d.get("report_key"))
        cid = d.get("carrier_id")
        if rk and cid and cid in by_id:
            out[rk] = by_id[cid]
    for c in (conns or []):
        sk = _s(c.get("sweep_kind"))
        cid = c.get("carrier_id")
        if sk and cid and cid in by_id and sk not in out:
            out[sk] = by_id[cid]
    return out


def build(carriers, defs, conns, readiness=None, mapped_reports=None):
    """The whole carrier-scoped payload the Setup Wizard renders under its profile step."""
    visible = {c.get("id"): c.get("name") for c in (carriers or []) if c.get("id")}
    defs = [d for d in (defs or []) if carrier_visible(d, visible)]
    blocks = carrier_blocks(carriers, defs, conns, readiness, mapped_reports)
    return {
        "spine": SPINE,
        "carrier_options": carrier_options(carriers),
        "blocks": blocks,
        "progress": spine_progress(blocks),
        "add_carrier": {"endpoint": "/commcalc/carriers", "method": "POST",
                        "label": "Add a carrier"},
    }
