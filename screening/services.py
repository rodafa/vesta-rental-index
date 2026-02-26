"""
Scorecard auto-population engine.

Reads ScreeningReport records linked to a scorecard's ScreeningApplication
and maps BoomScreen data to scorecard fields. Non-destructive: only touches
fields that map from BoomScreen. Manual fields stay at defaults.
"""

import logging

logger = logging.getLogger(__name__)


def auto_populate_scorecard(scorecard):
    """
    Auto-populate scorecard fields from linked ScreeningReport records.
    Sets auto_populated=True and calls compute_score() when done.
    """
    app = scorecard.screening_application
    reports = {r.report_type: r for r in app.reports.all()}

    _populate_from_credit(scorecard, reports.get("credit"))
    _populate_from_criminal(scorecard, reports.get("criminal"))
    _populate_from_eviction(scorecard, reports.get("eviction"))
    _populate_from_income(scorecard, reports.get("income"))

    scorecard.auto_populated = True
    scorecard.compute_score()
    scorecard.save()

    logger.info(
        "Auto-populated scorecard %s (score=%d, rec=%s)",
        scorecard.pk,
        scorecard.total_score,
        scorecard.recommendation,
    )


def _populate_from_credit(scorecard, report):
    """Map credit report data to scorecard fields."""
    if not report:
        return
    data = report.report_data or {}

    # Credit score → tier
    score = data.get("credit_score") or data.get("score")
    if score is not None:
        try:
            score = int(score)
            scorecard.credit_score_raw = score
            if score >= 750:
                scorecard.credit_tier = "excellent"
            elif score >= 700:
                scorecard.credit_tier = "good"
            elif score >= 650:
                scorecard.credit_tier = "fair"
            elif score >= 600:
                scorecard.credit_tier = "poor"
            else:
                scorecard.credit_tier = "very_poor"
        except (ValueError, TypeError):
            pass

    # Bankruptcy
    bankruptcy = data.get("bankruptcy")
    if bankruptcy is not None:
        if not bankruptcy or bankruptcy == "none":
            scorecard.bankruptcy_status = "none"
        elif bankruptcy in ("active",):
            scorecard.bankruptcy_status = "active"
        else:
            scorecard.bankruptcy_status = "discharged_3plus"

    # Chargeoffs
    chargeoffs = data.get("active_chargeoffs") or data.get("chargeoffs")
    if chargeoffs:
        scorecard.credit_active_chargeoffs = True


def _populate_from_criminal(scorecard, report):
    """Map criminal report data to scorecard fields."""
    if not report:
        return
    data = report.report_data or {}

    felonies = data.get("felonies_5yr") or data.get("felonies")
    if felonies is not None:
        scorecard.legal_no_felonies_5yr = not bool(felonies)

    violent_felony = data.get("violent_felony")
    if violent_felony:
        scorecard.auto_deny_violent_felony = True


def _populate_from_eviction(scorecard, report):
    """Map eviction report data to scorecard fields."""
    if not report:
        return
    data = report.report_data or {}

    evictions = data.get("eviction_count") or data.get("evictions")
    if evictions is not None:
        try:
            count = int(evictions)
            if count == 0:
                scorecard.eviction_history = "none"
            else:
                scorecard.eviction_history = "recent"
                recent = data.get("recent_eviction") or data.get("within_5yr")
                if recent:
                    scorecard.auto_deny_recent_eviction = True
                elif count > 0:
                    # Has evictions but none recent
                    scorecard.eviction_history = "old"
        except (ValueError, TypeError):
            pass


def _populate_from_income(scorecard, report):
    """Map income verification data to scorecard fields."""
    if not report:
        return
    data = report.report_data or {}

    # Income ratio
    ratio = data.get("income_to_rent_ratio") or data.get("income_ratio")
    if ratio is not None:
        try:
            ratio = float(ratio)
            scorecard.income_ratio_numeric = ratio
            if ratio >= 3.0:
                scorecard.income_ratio = "3x_plus"
            elif ratio >= 2.5:
                scorecard.income_ratio = "2.5x_to_3x"
            elif ratio >= 2.0:
                scorecard.income_ratio = "2x_to_2.5x"
            else:
                scorecard.income_ratio = "below_2x"
        except (ValueError, TypeError):
            pass

    # DTI
    dti = data.get("dti") or data.get("debt_to_income")
    if dti is not None:
        try:
            dti = float(dti)
            scorecard.dti_numeric = dti
            if dti < 30:
                scorecard.dti_tier = "low"
            elif dti <= 45:
                scorecard.dti_tier = "moderate"
            else:
                scorecard.dti_tier = "high"
        except (ValueError, TypeError):
            pass

    # Employment verified
    emp_verified = data.get("employment_verified")
    if emp_verified is not None:
        scorecard.income_employment_verified = bool(emp_verified)
