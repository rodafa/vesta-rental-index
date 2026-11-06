"""
Selectors for the accounting app.

Pure query functions — no side-effects.
"""

import logging

from accounting.models import PortfolioStatement

logger = logging.getLogger(__name__)


def get_statement_for_period(portfolio, period_start, period_end):
    """
    Return the posted PortfolioStatement whose period matches exactly,
    or None if no match.

    Raises ValueError if more than one posted statement matches
    (indicates a data integrity issue — two statements for the same month).
    """
    stmts = list(
        PortfolioStatement.objects.filter(
            portfolio=portfolio,
            status="2",
            period_start=period_start,
            period_end=period_end,
        )
    )

    if len(stmts) == 0:
        return None

    if len(stmts) > 1:
        ids = [s.rentvine_statement_id for s in stmts]
        raise ValueError(
            f"Multiple posted statements for {portfolio.name} "
            f"period {period_start}\u2013{period_end}: {ids}"
        )

    return stmts[0]


def format_statement_financials(stmt):
    """
    Build the standard financials dict + formatted period label from a
    PortfolioStatement row.

    Returns dict with keys: period_start, period_end, statement_period,
    beginning_balance, total_income, total_expenses, total_adjustments,
    ending_balance, total_distribution.
    """
    return {
        "period_start": stmt.period_start,
        "period_end": stmt.period_end,
        "statement_period": (
            f"{stmt.period_start.strftime('%b %d')} \u2013 "
            f"{stmt.period_end.strftime('%b %d, %Y')}"
        ),
        "beginning_balance": stmt.beginning_balance,
        "total_income": stmt.total_income,
        "total_expenses": stmt.total_expenses,
        "total_adjustments": stmt.total_adjustments,
        "ending_balance": stmt.ending_balance,
        "total_distribution": stmt.total_distribution,
    }
