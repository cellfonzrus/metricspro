"""MANAGER DIGEST FAN-OUT — the ONE home for "who gets alerted about a store, and once per what".

CLAUDE.md, "A fix is a DESIGN fix" / "One fact, one home, dereferenced — never copied": a second
alerting mechanism, a second dedup rule or a second recipient resolution is the duplicate defect the
index rules forbid. Before this file existed, `epay_alerts.plan_emails` held the only implementation
of a pattern that is not about ePay at all:

    per-store items  ->  the DM and every manager above the DM for that store
                     ->  ONE digest per manager (managers with no email skipped)
                     ->  a stable ref_key per (recipient, store, date, kind) so `storeops.alert_log`
                         can escalate a given finding ONCE per day.

That pattern is now HERE, and ePay dereferences it (`epay_alerts.plan_emails` is a thin wrapper that
supplies its own subject/HTML builder). A new alert KIND — the zero-sales alert, `zero_sales.py` —
is a new caller of THIS function, never a second fan-out.

WHAT IS PARAMETERISED, AND WHAT IS NOT
  Parameterised: the alert SCOPE name, which field of an item names the store, which fields make the
  item's identity inside a store-day, and how a digest's subject/HTML read. Those are per-alert.
  NOT parameterised: that recipients are DM ∪ above-DM, that a recipient with no email is skipped,
  that one recipient gets one digest, that an item reachable by two hierarchy paths is listed once,
  and the ref_key SPELLING. Those are the house rule, and a caller cannot vary them.

REF_KEY SPELLING (the dedup convention, one home):

    "<scope>|<today>|<lower(email)>|<part>|<part>|…"

  `scope` is also the `storeops.alert_log.scope` value the caller writes, so the row and the key
  agree by construction. The caller's `key_parts` supplies the per-alert tail (store, date, kind).

RULE TWO: no carrier, tenant, store or product name appears here. Pure stdlib — no DB, no framework,
no network — so `backend/harness_manager_digest.py` and the callers' own harnesses prove it DB-free.
Registered in docs/SYSTEM_DATA_FLOW_INDEX.md §15.
"""


def ref_key(scope, today, email, *parts):
    """THE dedup key spelling, for every alert kind. `parts` is the per-alert tail — typically the
    store, the date the finding is about, and the kind. Values are stringified as-is (None -> '');
    the email is lower-cased and trimmed so a recipient spelled two ways dedups to one key."""
    tail = "|".join("" if p is None else str(p) for p in parts)
    return "{s}|{d}|{e}|{t}".format(
        s=str(scope or "").strip(), d=str(today or ""),
        e=str(email or "").strip().lower(), t=tail)


def recipients_for(hierarchy):
    """The house recipient rule for ONE store: the District Manager(s) AND every manager ABOVE the
    DM. `hierarchy` is what `storeops.router._managers_above_dm` returns —
    {"dm": [{name, email, …}], "above": [{name, email, …}]}. A malformed/absent entry yields no
    recipients (an alert nobody can receive is silently dropped, never raised: an alerting bug must
    not take the sweep down)."""
    h = hierarchy if isinstance(hierarchy, dict) else {}
    return list(h.get("dm") or []) + list(h.get("above") or [])


def plan_digests(items, hierarchy_by_store, today, *, scope, build, key_parts,
                 store_of=lambda it: it.get("store_code"), kind=None):
    """PURE. Decide the emails to send for ONE tenant, for ONE alert scope.

      items                a flat list of per-store findings (any shape the caller's `build` reads).
      hierarchy_by_store   {store: {"dm": […], "above": […]}} — already resolved by the caller.
      today                the tenant-local date string the dedup is keyed on.
      scope                the alert scope, e.g. 'epay_discrepancy' / 'zero_sales'. Also the
                           storeops.alert_log scope the caller writes.
      build(name, items)   -> {"subject", "html"} for ONE recipient's digest.
      key_parts(item)      -> the tuple identifying this finding inside the store-day, e.g.
                           (store, date, kind).
      store_of(item)       -> the store this finding belongs to.
      kind                 the digest label put on each planned digest (defaults to scope).

    Returns {"digests": [{kind, to, to_name, subject, html, items:[{…, ref_key}]}]}, sorted by
    recipient so a caller's output is stable. Every item carries the ref_key the caller filters on
    BEFORE sending and records AFTER sending — the same two calls ePay already makes.
    """
    by_store = {}
    for it in (items or []):
        by_store.setdefault(store_of(it), []).append(it)

    mgr = {}   # lower(email) -> {"name", "email", "items": [...]}
    for store, store_items in by_store.items():
        for m in recipients_for(hierarchy_by_store.get(store)):
            em = str((m or {}).get("email") or "").strip()
            if not em:
                continue          # a manager with no email is skipped — the house rule, not a knob.
            slot = mgr.setdefault(em.lower(), {"name": (m or {}).get("name") or em, "email": em,
                                               "items": []})
            for it in store_items:
                slot["items"].append({**it, "ref_key": ref_key(scope, today, em, *key_parts(it))})

    digests = []
    for slot in mgr.values():
        # One recipient can oversee the same store through BOTH the DM node and an ancestor — dedup
        # by ref_key so a finding is never listed twice in one digest.
        seen, uniq = set(), []
        for it in slot["items"]:
            if it["ref_key"] in seen:
                continue
            seen.add(it["ref_key"])
            uniq.append(it)
        built = build(slot["name"], uniq)
        digests.append({"kind": kind or scope, "to": slot["email"], "to_name": slot["name"],
                        "subject": built["subject"], "html": built["html"], "items": uniq})
    digests.sort(key=lambda d: d["to"].lower())
    return {"digests": digests}
