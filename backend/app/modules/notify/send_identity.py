"""WHICH REPORT does this send record name? — the ONE home (owner 2026-10-05).

**The defect, as a class.** A `notify.send_log` row is the history an employee reads after sending
themselves a report, and it did not say WHICH report it carried. Every writer had its own idea of
what to put in `report_key`:

  * `/notify/send` + `/notify/send-to-designated` wrote the machine key (`closing_envelope_report`),
    which the history then rendered raw — the registry already holds a human label for every report
    and nothing dereferenced it;
  * `/notify/send-file` — the UNIVERSAL export-bar path every page's "📤 Send" button uses, i.e.
    the path employees actually use to send themselves a report — wrote the literal
    `"(client-export)"` for EVERY report and DISCARDED the `title` the browser had already sent;
  * the no-login download row wrote `"(download)"` on the same terms.

So the fix is not "put the title in the client-export row". It is: a send record names its report,
for every writer and every reader, by dereferencing this module. Readers get a label even for rows
written before the `report_label` column existed, because the resolution is a FUNCTION of the row.

PURE: no IO, no app imports, no DB. `labels` is always passed in by the caller (the registry's
`{key: label}` map) so this module stays a leaf and provable offline
(`backend/harness_notify_send_identity.py`).
"""

# Sentinel keys for sends that are not a registered report. They are RECORDS OF A ROUTE, not report
# names — which is exactly why a row carrying one still needs a label of its own.
CLIENT_EXPORT_KEY = "(client-export)"
DOWNLOAD_KEY = "(download)"
_SENTINELS = (CLIENT_EXPORT_KEY, DOWNLOAD_KEY)

FALLBACK_LABEL = "Report"

# The row keys this module owns. `_insert_log` strips them one at a time when the migration adding
# them has not been run yet, so the name of the column lives here too.
LABEL_COLUMN = "report_label"


def _clean(v) -> str:
    """A label is one line of display text, trimmed, never longer than the column allows."""
    s = " ".join(str(v or "").split())
    return s[:200]


def report_label(report_key=None, title=None, labels=None) -> str:
    """The human name of the report a send record carried.

    A REGISTERED key wins (one spelling of a report's name, the registry's), because a browser can
    pass any title it likes and two spellings of one report is the duplicate defect. Otherwise the
    caller-supplied title is the name. Neither → `FALLBACK_LABEL`, never a machine key dressed up
    as a name.
    """
    key = _clean(report_key)
    reg = _clean((labels or {}).get(key)) if key and key not in _SENTINELS else ""
    return reg or _clean(title) or FALLBACK_LABEL


def log_identity(report_key=None, title=None, labels=None) -> dict:
    """The report-identity columns of ONE `notify.send_log` / `notify.send_artifact` row.

    Every writer spreads this into its row — that is what the lock in
    `harness_notify_send_identity.py` checks, so a new send path cannot forget the label.
    """
    key = _clean(report_key) or CLIENT_EXPORT_KEY
    return {"report_key": key, LABEL_COLUMN: report_label(key, title, labels)}


def display_label(row, labels=None) -> str:
    """What the Notify history shows in its "Report" column, for ANY row.

    A row written since the `report_label` column exists carries its own name. A row written BEFORE
    it (or by a path whose label was stripped because the migration is un-run) is resolved the same
    way it would have been written — registry label, then the stored title/filename, then the
    fallback. The history therefore never shows a machine key, with or without the migration.
    """
    row = row or {}
    stored = _clean(row.get(LABEL_COLUMN))
    if stored:
        return stored
    return report_label(row.get("report_key"), row.get("title") or row.get("filename"), labels)


def stamp_display(rows, labels=None) -> list:
    """Read-side stamp: every returned row carries a resolved `report_label` (§13b.1 pattern — a
    stored value is never trusted to be a display name, the read resolves it)."""
    out = []
    for r in list(rows or []):
        d = dict(r or {})
        d[LABEL_COLUMN] = display_label(d, labels)
        out.append(d)
    return out
