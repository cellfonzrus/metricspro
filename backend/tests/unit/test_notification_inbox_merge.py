"""THE COMPANY NOTIFICATION INBOX — one finding is ONE item, updated when it recurs.

Owner, 2026-11-24: *"Stop sending daily messages about data not uploading instead create a [one]
for each compay in their notifications inbox and overwrite if it is the same message everyday"*.

These pin the PURE merge rule, which is the whole of "overwrite instead of add":
  · the same message tomorrow updates the row it already has — it never adds a second one;
  · a message whose TEXT changed is news again, so it comes back unread;
  · a message that is byte-identical does NOT un-read itself, or the inbox nags exactly as the
    daily email did;
  · a finding that was resolved and came back is news again.
"""
import pytest

from app.modules.core import notification_inbox as NI

NOW = "2026-11-24T09:00:00+00:00"
LATER = "2026-11-25T09:00:00+00:00"


def _incoming(title="Payment detail is 2 day(s) behind", body="open Email Imports"):
    return {"title": title, "body": body, "severity": "warning",
            "deep_link": "/commcalc/email-imports", "affected": ["B-1115"]}


@pytest.mark.unit
def test_a_finding_nobody_has_seen_becomes_one_unread_row():
    row, state = NI.merge_row(None, _incoming(), NOW)
    assert state == "created"
    assert row["occurrences"] == 1
    assert row["first_seen_at"] == NOW and row["last_seen_at"] == NOW
    assert row["read_at"] is None and row["resolved_at"] is None


@pytest.mark.unit
def test_the_same_message_tomorrow_updates_the_row_and_does_not_un_read_it():
    first, _ = NI.merge_row(None, _incoming(), NOW)
    first["read_at"] = NOW                      # the admin read it
    row, state = NI.merge_row(first, _incoming(), LATER)
    assert state == "unchanged"
    assert row["occurrences"] == 2
    assert row["first_seen_at"] == NOW, "the first sighting is the age of the problem"
    assert row["last_seen_at"] == LATER
    assert row["read_at"] == NOW, "an identical message must not nag a second time"


@pytest.mark.unit
def test_a_message_whose_text_changed_is_news_again():
    first, _ = NI.merge_row(None, _incoming(), NOW)
    first["read_at"] = NOW
    row, state = NI.merge_row(first, _incoming(title="Payment detail is 3 day(s) behind"), LATER)
    assert state == "changed"
    assert row["title"] == "Payment detail is 3 day(s) behind"
    assert row["read_at"] is None
    assert row["occurrences"] == 2


@pytest.mark.unit
def test_a_resolved_finding_that_comes_back_is_unread_again():
    first, _ = NI.merge_row(None, _incoming(), NOW)
    first.update({"read_at": NOW, "resolved_at": NOW})
    row, state = NI.merge_row(first, _incoming(), LATER)
    assert state == "changed"
    assert row["resolved_at"] is None
    assert row["read_at"] is None


@pytest.mark.unit
def test_a_finding_that_no_longer_fires_is_resolved_and_the_rest_are_left_alone():
    rows = [{"inbox_key": "a", "resolved_at": None}, {"inbox_key": "b", "resolved_at": None},
            {"inbox_key": "c", "resolved_at": NOW}]
    assert NI.stale_keys(rows, ["a"]) == ["b"], "already-resolved rows are not resolved twice"
    assert NI.stale_keys(rows, ["a", "b"]) == []


@pytest.mark.unit
def test_an_item_with_no_stable_key_is_refused_rather_than_filed_under_a_date():
    with pytest.raises(ValueError):
        NI.identity("connector", "")
    assert NI.identity("connector", "connector:dlar_sweep_config:7") == "connector:dlar_sweep_config:7"
