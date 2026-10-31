"""Unit: how far back a sale may be reopened depends on WHO is asking (index §POS-12.9).

Owner, 2026-10-31, asked for the edit window and gave two numbers rather than one: *"30 days nback
for the admin and same day for teh sales rep or store manager"*.

**THE CLASS, NOT THE INSTANCE.** Not "add a second setting for admins". §POS-12 shipped the window
as ONE declared number for the whole org, which encodes an assumption that was never stated: that
how far back a record may be rewritten is a property of the TENANT alone. It is not. It is a
property of the tenant AND the standing of the person asking, and the same shape recurs anywhere a
record can be reopened. So the window becomes a function of a RUNG, with one home answering it, and
every caller passes the rung it resolved rather than deciding the number itself.

**THE RUNG IS A GRANT, NEVER A ROLE NAME.** RULE TWO: "admin", "sales rep" and "store manager" are
words a tenant chooses, and a tenant's words must not appear in a branch. What the code knows is
whether the caller holds a POS permission key — `ELEVATED_GRANT` — which `_pos_grant` already
answers for any key, defaulting to a full-scope admin when nothing is seeded. So the owner's ask
lands with no configuration at all, and a tenant that wants a particular role to hold it can grant
the key in the Roles screen afterwards.

**A WIDER ORDINARY WINDOW NEVER OUTRUNS THE ELEVATED ONE.** The two numbers are independent
declarations, so an org could declare 45 ordinary days and leave the elevated one at the house 30 —
which would make an admin's reach SHORTER than a cashier's. That is never what a declaration like
that means, so the one home takes the greater of the two for the elevated rung. Tested below,
because it is the kind of thing a second caller would re-derive differently.

**AN UNREADABLE DECLARATION NEVER WIDENS.** Already true of the ordinary window and now asserted of
both: a setting that will not parse falls back to the house number, and the house number is the
floor, not "forever".
"""
import pytest

from app.modules.pos import sale_amendment as SA

NOW = "2026-10-31T18:00:00Z"
SALE = {"id": "s-1", "transaction_id": 4605, "status": "completed",
        "created_at": "2026-10-31T14:00:00Z", "total": 210.13, "balance": 0}


def _aged(days):
    """The same sale, rung `days` whole days before NOW."""
    from datetime import datetime, timedelta, timezone
    at = datetime(2026, 10, 31, 14, 0, tzinfo=timezone.utc) - timedelta(days=days)
    return dict(SALE, created_at=at.isoformat())


# ── the two house numbers are the owner's two numbers ────────────────────────────────────────────
def test_the_ordinary_house_window_is_the_same_day_only():
    """"same day for teh sales rep or store manager" — zero days back, not one."""
    assert SA.HOUSE_WINDOW_DAYS == 0


def test_the_elevated_house_window_is_thirty_days():
    assert SA.HOUSE_ELEVATED_WINDOW_DAYS == 30


def test_each_window_has_its_own_named_setting_so_neither_is_a_literal_at_a_caller():
    assert SA.WINDOW_SETTING == "sale_reopen_window_days"
    assert SA.ELEVATED_WINDOW_SETTING == "sale_reopen_window_days_admin"
    assert SA.WINDOW_SETTING != SA.ELEVATED_WINDOW_SETTING


def test_the_elevated_rung_is_named_by_a_grant_key_not_a_role_name():
    """A role name is a tenant's own word. What the code may know is a permission key."""
    key = SA.ELEVATED_GRANT
    assert key == "pos_amend_any_day"
    for tenant_word in ("admin", "manager", "rep", "owner", "cashier"):
        assert tenant_word not in key.replace("pos_amend_any_day", "")


# ── window_for: the one home for "how far back may THIS caller reach" ────────────────────────────
def test_an_ordinary_caller_gets_the_ordinary_window():
    assert SA.window_for(False) == 0


def test_an_elevated_caller_gets_the_elevated_window():
    assert SA.window_for(True) == 30


def test_a_declared_ordinary_window_is_honoured():
    assert SA.window_for(False, declared=7) == 7


def test_a_declared_elevated_window_is_honoured():
    assert SA.window_for(True, declared_elevated=90) == 90


def test_the_ordinary_declaration_does_not_narrow_the_elevated_rung():
    """An org declaring 45 ordinary days must not leave its admins reaching only 30."""
    assert SA.window_for(True, declared=45) == 45
    assert SA.window_for(False, declared=45) == 45


def test_the_elevated_rung_is_never_shorter_than_the_ordinary_one():
    assert SA.window_for(True, declared=60, declared_elevated=10) == 60


def test_an_unreadable_declaration_falls_back_and_never_widens():
    assert SA.window_for(False, declared="nonsense") == SA.HOUSE_WINDOW_DAYS
    assert SA.window_for(True, declared_elevated="nonsense") == SA.HOUSE_ELEVATED_WINDOW_DAYS
    assert SA.window_for(True, declared="", declared_elevated=None) == SA.HOUSE_ELEVATED_WINDOW_DAYS


def test_a_negative_declaration_cannot_reach_below_zero():
    assert SA.window_for(False, declared=-5) == 0
    assert SA.window_for(True, declared_elevated=-5) == SA.HOUSE_WINDOW_DAYS


# ── the refusal, which is what a person actually reads ───────────────────────────────────────────
def test_a_rep_may_edit_todays_sale():
    assert SA.refusal(_aged(0), now=NOW, window_days=SA.window_for(False)) is None


def test_a_rep_may_not_edit_yesterdays_sale():
    assert SA.refusal(_aged(1), now=NOW, window_days=SA.window_for(False)) is not None


def test_an_admin_may_edit_a_sale_from_three_weeks_ago():
    assert SA.refusal(_aged(21), now=NOW, window_days=SA.window_for(True)) is None


def test_an_admin_may_not_edit_a_sale_from_thirty_one_days_ago():
    assert SA.refusal(_aged(31), now=NOW, window_days=SA.window_for(True)) is not None


def test_a_refused_rep_is_told_an_admin_can_still_do_it_rather_than_hitting_a_dead_end():
    """The sentence a cashier reads should name the way forward. It only says so when an admin
    COULD: on a sale older than the elevated window too, saying it would be a false promise."""
    within = SA.refusal(_aged(5), now=NOW, window_days=SA.window_for(False),
                        elevated_window_days=SA.window_for(True))
    assert within is not None and "administrator" in within.lower()
    beyond = SA.refusal(_aged(400), now=NOW, window_days=SA.window_for(False),
                        elevated_window_days=SA.window_for(True))
    assert beyond is not None and "administrator" not in beyond.lower()


def test_an_elevated_caller_is_never_told_to_ask_an_administrator():
    """They ARE the administrator; the sentence must not send them to themselves."""
    said = SA.refusal(_aged(400), now=NOW, window_days=SA.window_for(True),
                      elevated_window_days=SA.window_for(True))
    assert said is not None and "administrator" not in said.lower()


def test_the_same_day_refusal_reads_as_the_same_day_not_as_zero_days():
    """"within 0 days" is not English. A window of nothing-but-today says so."""
    said = SA.refusal(_aged(2), now=NOW, window_days=0)
    assert said is not None
    assert "0 day" not in said and "same day" in said.lower()


def test_the_window_sentence_still_agrees_in_number_for_one_day():
    said = SA.refusal(_aged(4), now=NOW, window_days=1)
    assert "1 day" in said and "1 days" not in said


def test_the_elevated_window_is_optional_so_every_existing_caller_still_works():
    """§POS-12's own callers pass `window_days` alone. Adding the second number must not make the
    one home refuse to answer the question it already answered."""
    assert SA.refusal(_aged(0), now=NOW, window_days=0) is None
    assert SA.refusal(_aged(9), now=NOW, window_days=1) is not None


# ── the plan carries the rung, so a screen never has to guess ───────────────────────────────────
def test_the_plan_reports_which_window_applied_and_whether_the_caller_is_elevated():
    out = SA.plan(_aged(0), [], [], now=NOW, window_days=SA.window_for(True), elevated=True)
    assert out["window_days"] == 30
    assert out["elevated"] is True


def test_the_plan_defaults_to_not_elevated_rather_than_omitting_the_fact():
    """A screen reading a missing key as falsy is the same bug as a $0.00 tender: absence must be
    stated, not inferred."""
    out = SA.plan(_aged(0), [], [], now=NOW, window_days=0)
    assert out["elevated"] is False
    assert out["window_days"] == 0


@pytest.mark.parametrize("days,elevated,allowed", [
    (0, False, True), (1, False, False), (30, False, False),
    (0, True, True), (1, True, True), (30, True, True), (31, True, False),
])
def test_the_grid_the_owner_asked_for(days, elevated, allowed):
    said = SA.refusal(_aged(days), now=NOW, window_days=SA.window_for(elevated))
    assert (said is None) is allowed
