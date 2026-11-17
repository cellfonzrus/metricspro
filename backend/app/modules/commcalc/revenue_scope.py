"""DOES MONEY WE COULD NOT CLASSIFY COUNT AS THE DAY'S REVENUE — the one home. PURE (no DB).

THE OWNER'S ANSWER (Sanjot, 2026-11-17), choosing "Whole day's revenue counts" when asked how
$22,653.31 of his October report's unclassified product lines should count toward the day: they
count. And earlier, of his store's daily revenue gate: *"that is the total revenue for the day, which
ever sales rep generates the most revenue he get his proportional share of commission"*.

(The gate's own THRESHOLD is deliberately not written here, in prose or in code. It is a value on a
live `incentive_rule` row; quoting it would make this docstring a lie the day the owner edits it, and
`harness_revenue_scope` §G9 fails the build on any tenant figure appearing in this file. The one
figure below that IS quoted is a measured historical finding, which cannot drift.)

THE CLASS, named rather than the instance. The general fact that was wrong is this:

    "A day's revenue is the sum of the categories we could place."

It is false, and the falseness is not about one tenant or one report. Whether WE can classify a line
says nothing about whether the store sold it. Two different questions were being answered with one
number:

  1. "How much did CATEGORY X do?"      Only a placeable line can answer. Folding an unclassified line in
                                        would make a category read high — the guess `pos_report_feed`
                                        exists to refuse.
  2. "What was the DAY's revenue?"      EVERY line answers, placed or not. Dropping the unclassified ones
                                        makes the day read low, and a revenue gate then fails on
                                        a day the store had in fact cleared.

So the sum over placed categories is the right answer to (1) and the wrong answer to (2), and before
this module there was only one answer. `metric_catalog.gross_revenue` now takes the unclassified money as
a declared ADDEND: question (1)'s readers pass nothing and are unmoved, question (2)'s readers pass
it. A named payout still measures only its own categories — unclassified money is not evidence of
any category, and that has not changed.

WHY THIS IS DECLARED AND NOT HOUSE BEHAVIOUR. Reading unclassified money into revenue house-wide would
restate every tenant's gross revenue, and `gross_revenue` is what every revenue target and every
percent-of-amount payout measures. That is a money move nobody approved, which is the §STOREOPS-07
lesson (2026-11-12) in the place it costs the most. So the scope is a per-org DECLARATION, and
`SCOPE_CLASSIFIED_ONLY` — today's behaviour for every tenant — is what an unanswered question reads as.

AND WHY A DEFAULT IS LEGITIMATE HERE AT ALL. The 2026-10-30 rule bans a house default that is really
one tenant's VOCABULARY. This is not vocabulary: it names no category, no carrier, no product and no
tenant. It is a POLICY — a switch — and the house rules keep defaults right for exactly that. The
categories themselves remain the tenant's own, required and never defaulted.

A SCOPE IS NOT A REPAIR. Declaring `whole_day` does not excuse an unmapped line: the money counts
toward the day AND the line is still reported as unmapped, so the admin still maps it and the
category still gets its own figure. Counting it was never the alternative to classifying it, and a
tenant on `whole_day` whose report is fully mapped sees no difference at all.

Proof: `backend/harness_revenue_scope.py`, which arms the switch both ways against a fixture.
"""

# ── Vocabulary. These constants ARE the vocabulary; nothing else may spell them. ─────────────────
SCOPE_CLASSIFIED_ONLY = "classified_only"   # revenue is the categories we could place (today's behaviour)
SCOPE_WHOLE_DAY = "whole_day"       # revenue is everything the store took, classified or not
SCOPES = (SCOPE_CLASSIFIED_ONLY, SCOPE_WHOLE_DAY)
SCOPE_DEFAULT = SCOPE_CLASSIFIED_ONLY
SCOPE_LABELS = {
    SCOPE_CLASSIFIED_ONLY: "Only categories we can place count as revenue",
    SCOPE_WHOLE_DAY: "The whole day's takings count, classified or not",
}


def resolve(raw):
    """The declared scope. Blank/None reads as the house default — today's behaviour for every
    tenant, so an unanswered question moves nobody. Anything else is REFUSED rather than guessed: a
    mistyped scope that silently fell back would restate a tenant's revenue. PURE."""
    v = str(raw or "").strip().lower() or SCOPE_DEFAULT
    if v not in SCOPES:
        raise ValueError("revenue scope must be one of %s" % ", ".join(SCOPES))
    return v


def counts_unclassified(raw) -> bool:
    """Does this tenant's revenue include money no declared category claimed?"""
    return resolve(raw) == SCOPE_WHOLE_DAY


def unclassified_addend(raw, unclassified):
    """The money to ADD to a day's revenue under this scope: the unclassified total, or zero.

    Every caller that folds unclassified money into a revenue figure asks HERE. A caller that tests
    the scope itself next to the arithmetic is the second copy the house rules forbid — and it is the
    copy that would keep paying after the tenant changed its mind. PURE."""
    if not counts_unclassified(raw):
        return 0.0
    try:
        return round(float(unclassified or 0.0), 2)
    except (TypeError, ValueError):
        return 0.0


def scope_sentence(raw, *, unclassified=0.0, lines=0) -> str:
    """One line naming what this day's revenue DOES and DOES NOT include, for every surface that
    shows the figure. A revenue number shown without it cannot be reconciled against the report the
    operator is holding — which is how $22,653.31 went unnoticed. PURE."""
    s = resolve(raw)
    amt = round(float(unclassified or 0.0), 2)
    n = int(lines or 0)
    if s == SCOPE_WHOLE_DAY:
        if not n:
            return ("%s: the whole day's takings count as revenue. Every line of this day's report "
                    "was placed in a declared category." % s)
        return ("%s: the whole day's takings count as revenue, including $%s on %d report line(s) "
                "that no declared category claimed. Those lines are still reported as unmapped — "
                "mapping them gives each category its own figure and does not change the day's "
                "total." % (s, format(amt, ",.2f"), n))
    if not n:
        return ("%s: revenue is the categories we could place, and every line of this day's report "
                "was placed." % s)
    return ("%s: revenue counts only the categories we could place, so $%s on %d report line(s) no "
            "declared category claimed is NOT in it. Map those lines, or declare that the whole "
            "day's takings count." % (s, format(amt, ",.2f"), n))
