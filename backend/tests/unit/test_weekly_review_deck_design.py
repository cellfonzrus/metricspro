"""The deck is designed, and the design has one home (§COMMCALC-76.10).

Owner 2026-11-17, of the first in-app deck: *"this is not the same smart ppt as the firdst one"* —
comparing it with the hand-built review deck, which carries a theme, stat cards, charts and a
footer. These tests pin the two halves: the fold hands over cards and a chart as DATA, and the
renderer draws them without being told a colour.
"""
import pytest

from app.modules.commcalc import weekly_review as W
from app.modules.commcalc import weekly_review_deck as DK
from app.modules.notify import render as RD

pptx = pytest.importorskip("pptx")


def _item(**kw):
    item = {"key": "profit", "label": "Profit by store", "state": W.MEASURED, "figure": -1234.5,
            "unit": "money", "window": "period", "source": "GET /commcalc/gp",
            "narrative": ["A sentence the fold wrote."],
            "rows": [{"address": "A St", "revenue": 10.0, "net_profit": -9.0},
                     {"address": "B St", "revenue": 20.0, "net_profit": -4.0},
                     {"address": "C St", "revenue": 30.0, "net_profit": 7.0}]}
    item.update(kw)
    return item


# ── the fold ──────────────────────────────────────────────────────────────────────────────────────
def test_cards_never_respell_the_headline_figure():
    cards, _tone = DK.item_cards(_item())
    assert "$1,234.50" not in " ".join(c["value"] for c in cards)
    assert any(c["value"] == "3" for c in cards)              # rows behind it
    assert any(c["value"] == "month to date" for c in cards)  # the window's own word


def test_a_loss_reads_bad_and_a_profit_reads_good():
    assert DK.item_cards(_item(figure=-1.0))[1] == "bad"
    assert DK.item_cards(_item(figure=1.0))[1] == "good"
    assert DK.item_cards(_item(key="aging", unit="count", figure=5.0))[1] == "bad"
    assert DK.item_cards(_item(key="rep_watch", unit="count", figure=5.0))[1] == "plain"


def test_an_unmeasured_item_carries_nothing_to_draw():
    slide = DK._item_slide(_item(state=W.NOT_MEASURED, figure=None, reason="no rows"))
    assert slide["cards"] == [] and slide["chart"] is None and slide["table"] is None


def test_the_chart_is_the_table_s_rows_worst_first():
    chart = DK.item_chart(_item())
    assert chart["categories"] == ["A St", "B St", "C St"]
    assert chart["series"][0]["values"] == [-9.0, -4.0, 7.0]
    assert chart["type"] == "bar"


def test_a_chart_too_small_to_read_is_not_drawn():
    assert DK.item_chart(_item(rows=[{"address": "A", "net_profit": -1.0}])) is None
    assert DK.item_chart(_item(key="rep_watch")) is None


def test_every_slide_carries_the_same_shape():
    pack = {"week": {"label": "w", "week_ending": "2026-11-15"}, "period": "November 2026",
            "company": {"scope": {"label": "Co"}, "headline": ["h"], "items": [_item()]},
            "markets": [{"scope": {"label": "M"}, "headline": ["m"], "items": [_item()]}],
            "items": [{"label": "Profit by store", "window": "period"}]}
    payload = DK.deck_payload(pack, company_label="Co")
    assert payload["slides"]
    for slide in payload["slides"]:
        assert set(("kind", "cards", "chart", "table", "bullets")) <= set(slide)


# ── the renderer ──────────────────────────────────────────────────────────────────────────────────
def _render(slides):
    return RD.build_pptx({"title": "T", "subtitle": "S", "filename": "f", "slides": slides})


def test_a_deck_with_cards_and_a_chart_is_a_real_file():
    data = _render([{"kind": "title", "title": "T", "bullets": ["b"], "cards": [], "chart": None,
                     "table": None, "note": "n"},
                    DK._item_slide(_item())])
    assert data[:2] == b"PK" and len(data) > 20_000


def test_a_payload_from_before_this_change_still_renders():
    # No cards, no chart, no tone — the keys are additive and their absence is not an error.
    data = _render([{"kind": "item", "eyebrow": "E", "title": "Ti", "bullets": ["b"],
                     "table": {"headers": ["A", "B"], "rows": [["x", 1.0]]}, "note": "n"}])
    assert data[:2] == b"PK"


def test_the_renderer_draws_what_it_is_given_and_formats_no_figure():
    slide = DK._item_slide(_item())
    data = _render([slide])
    assert data[:2] == b"PK"
    # The title IS the fold's worded figure, not something the renderer re-spelled.
    assert slide["title"] == W.figure_words(_item())


def test_every_slide_but_the_title_is_numbered():
    from io import BytesIO
    from pptx import Presentation
    slides = [{"kind": "title", "title": "T", "bullets": [], "cards": [], "chart": None,
               "table": None, "note": ""},
              DK._item_slide(_item()), DK._item_slide(_item(key="payroll", unit="money"))]
    pres = Presentation(BytesIO(_render(slides)))
    texts = ["\n".join(sh.text_frame.text for sh in s.shapes if sh.has_text_frame)
             for s in pres.slides]
    assert "T" in texts[0] and "\n1" not in texts[0]
    # The number is a line of its own in the footer bar. It is not necessarily the LAST text on
    # the slide: the source note is drawn after the footer, so "ends with" would be a test of
    # shape order rather than of numbering.
    def _lines(t):
        return [x.strip() for x in t.splitlines()]
    assert "2" in _lines(texts[1]) and "3" in _lines(texts[2])


def test_a_chart_with_no_series_draws_nothing_rather_than_an_empty_frame():
    from io import BytesIO
    from pptx import Presentation
    pres = Presentation(BytesIO(_render([
        {"kind": "item", "title": "Ti", "bullets": [], "cards": [],
         "chart": {"type": "bar", "categories": [], "series": []}, "table": None, "note": ""}])))
    assert not any(sh.has_chart for sh in pres.slides[0].shapes)
