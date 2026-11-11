"""A REPORT'S SETTINGS SURVIVE A COLUMN THAT MIGRATION NEVER ADDED (index §4b.1).

Measured against production 2026-11-11 04:46 UTC. `commcalc.report_definitions` for the house org
carries `refresh_days = 14` on the Comprehensive Comp row. The nightly sweep asked the portal for
`2026-11-10..2026-11-10` — ONE day — and had been doing so for 33 days, correctly receiving nothing
from a source that publishes in arrears, and reporting it as a quiet source.

Cause: `router._REGISTRY_SWEEP_COLS` names `arrears_days` and `empty_stale_after_days`. Neither
column exists in that database — migration `1042_report_arrears_window.sql` was written 2026-10-03
for this very defect, surfaced for approval and never applied. PostgREST answers the whole select
with 400 / 42703, and the reader's `except` re-reads `report_key,auto,refresh_months` only. So
`refresh_days`, `sweep_hour`, `sweep_minute`, `sweep_timezone` and `sweep_next_run_at` are dropped
together with the two that are genuinely absent, and `_expand_jobs` falls to
`DEFAULT_REFRESH_DAYS = 1`.

That is a COLUMN-BLOCK LADDER — the exact shape `app/core/column_tolerant.py` exists to forbid and
`harness_any_columns_lock.py` fails the build on. It escaped the lock because the column list is a
module-level constant referenced BY NAME, and the scan only resolved literals. Both are fixed here:
the reader probes and reports, and the scan resolves named constants.
"""
import pytest

from app.modules.commcalc import router
from app.core import column_tolerant as _ct


class _Q:
    """A PostgREST-shaped builder over a fake table that REFUSES a column it does not have, the way
    42703 does — the whole select, not the missing column."""

    def __init__(self, tbl):
        self.t = tbl
        self.cols = None
        self.filters = {}

    def select(self, cols):
        self.cols = [c.strip() for c in str(cols).split(",") if c.strip()]
        return self

    def eq(self, k, v):
        self.filters[k] = v
        return self

    def limit(self, n):
        return self

    def execute(self):
        if self.cols != ["*"]:
            missing = [c for c in self.cols if c not in self.t.columns]
            if missing:
                raise RuntimeError(f"column report_definitions.{missing[0]} does not exist")
        keep = self.t.columns if self.cols == ["*"] else self.cols
        rows = [{k: v for k, v in r.items() if k in keep} for r in self.t.rows
                if self.t.org is None or r.get("org_id") == self.filters.get("org_id")]
        return type("R", (), {"data": rows})()


class _Table:
    def __init__(self, columns, rows, org="o1"):
        self.columns, self.rows, self.org = list(columns), rows, org


class _Client:
    def __init__(self, table):
        self.t = table

    def schema(self, s):
        return self

    def table(self, name):
        return _Q(self.t)


HAVE = ["report_key", "auto", "refresh_months", "refresh_days", "sweep_hour", "sweep_minute",
        "sweep_timezone", "sweep_next_run_at", "org_id", "portal_report_id"]
ROW = {"org_id": "o1", "report_key": "comp_report", "auto": True, "refresh_months": 3,
       "refresh_days": 14, "sweep_hour": 23, "sweep_minute": 30, "sweep_timezone": None,
       "sweep_next_run_at": None, "portal_report_id": None}


def test_refresh_days_is_read_although_two_columns_are_absent():
    # THE DEFECT, as measured: the two mig-1042 columns are missing and `refresh_days` is not.
    cfg = router._registry_report_cfg(_Client(_Table(HAVE, [dict(ROW)])), "o1")
    assert cfg["comp_report"]["refresh_days"] == 14, \
        "one unapplied migration must not hide the setting that IS there"
    assert cfg["comp_report"]["sweep_hour"] == 23


def test_what_was_absent_is_reported_not_swallowed():
    cfg = router._registry_report_cfg(_Client(_Table(HAVE, [dict(ROW)])), "o1")
    missing = cfg["comp_report"].get("config_columns_missing")
    assert set(missing or []) == {"arrears_days", "empty_stale_after_days"}, \
        "a default silently standing in for a missing column is how this hid for 33 days"


def test_every_column_present_reads_exactly_as_before():
    full = HAVE + ["arrears_days", "empty_stale_after_days"]
    row = dict(ROW, arrears_days=7, empty_stale_after_days=None)
    cfg = router._registry_report_cfg(_Client(_Table(full, [row])), "o1")
    assert cfg["comp_report"]["arrears_days"] == 7
    assert cfg["comp_report"]["config_columns_missing"] == []


def test_the_absence_key_is_the_one_the_house_already_uses():
    assert "config_columns_missing" in _ct.ABSENCE_KEYS


def test_a_table_that_cannot_be_read_at_all_still_answers():
    class _Dead(_Client):
        def table(self, name):
            raise RuntimeError("relation does not exist")

    assert router._registry_report_cfg(_Dead(_Table(HAVE, [])), "o1") == {}


def test_the_reader_holds_no_column_block_ladder():
    import inspect
    src = inspect.getsource(router._registry_report_cfg)
    assert "_ct.present_columns(" in src or "_ct.select_list(" in src, \
        "the one reading rule is dereferenced, not re-derived"
    assert "refresh_months'" not in src.replace("_REGISTRY_SWEEP", ""), \
        "a narrower fallback column list is the ladder itself"


def test_the_lock_resolves_a_column_list_held_in_a_named_constant():
    import importlib.util, pathlib
    p = pathlib.Path(__file__).resolve().parents[2] / "harness_any_columns_lock.py"
    spec = importlib.util.spec_from_file_location("_anycols", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    src = (
        "WIDE = 'a,b,c,d'\n"
        "NARROW = 'a,b'\n"
        "def read(client):\n"
        "    try:\n"
        "        return client.table('t').select(WIDE).execute().data\n"
        "    except Exception:\n"
        "        return client.table('t').select(NARROW).execute().data\n"
    )
    kinds = {h[0] for h in mod.ladders_in(src, "synthetic.py")}
    assert "TRY-FALLBACK" in kinds, \
        "a ladder whose column lists are named constants is still a ladder"
