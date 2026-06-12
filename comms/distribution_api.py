"""
Dashboard API for owner distribution emails.

Snapshot listing:
    GET  /api/reports/distribution-snapshots?month=YYYY-MM

Send endpoints:
    GET  /api/reports/distribution-sends/recipients?month=YYYY-MM
    GET  /api/reports/distribution-sends/recipients/{email}/preview
    POST /api/reports/distribution-sends/recipients/{email}/send
    POST /api/reports/distribution-sends/recipients/{email}/test-send
"""

import logging

from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_POST

from .models import EmailDraft, PortfolioDistributionSnapshot
from .portfolio_api import _parse_month, _period_from_month, _require_access as _base_require_access
from .services import (
    _normalize_email,
    assemble_owner_distribution_email,
    send_distribution_email,
)

logger = logging.getLogger(__name__)

PRODUCT = "owner_distributions"


def _require_access(request):
    """Check login and role access for owner_distributions."""
    if not request.user.is_authenticated:
        return JsonResponse({"error": "Authentication required"}, status=401)
    if not request.user.can_access(PRODUCT):
        return JsonResponse({"error": "Forbidden"}, status=403)
    return None


@require_GET
def list_snapshots(request):
    """GET /api/reports/distribution-snapshots?month=YYYY-MM"""
    err = _require_access(request)
    if err:
        return err

    parsed = _parse_month(request)
    if not parsed:
        return JsonResponse([], safe=False)

    year, month = parsed
    period_start, _ = _period_from_month(year, month)

    snapshots = PortfolioDistributionSnapshot.objects.filter(
        period_type="monthly",
        period_start=period_start,
    ).select_related("portfolio").order_by("portfolio__name")

    result = [
        {
            "portfolio_id": s.portfolio.pk,
            "portfolio_name": s.portfolio.name,
            "distribution_amount": str(s.distribution_amount),
            "distribution_date": str(s.distribution_date) if s.distribution_date else None,
            "line_items_count": len(s.line_items),
            "period_start": str(s.period_start),
            "period_end": str(s.period_end),
        }
        for s in snapshots
    ]

    return JsonResponse(result, safe=False)


@require_GET
def list_recipients(request):
    """GET /api/reports/distribution-sends/recipients?month=YYYY-MM

    Only includes owners who have at least one portfolio with
    distribution_amount > 0 and distribution_date set.
    """
    from core.models import Owner, Portfolio

    err = _require_access(request)
    if err:
        return err

    parsed = _parse_month(request)
    if not parsed:
        return JsonResponse([], safe=False)

    year, month = parsed
    period_start, _ = _period_from_month(year, month)

    # Get all active owners with non-blank email
    owners = Owner.objects.filter(
        is_active=True,
    ).exclude(
        email=""
    ).exclude(
        email__isnull=True
    ).prefetch_related("portfolios")

    # Group by normalized email
    email_groups = {}
    for owner in owners:
        email = _normalize_email(owner.email)
        if not email:
            continue
        email_groups.setdefault(email, []).append(owner)

    recipients = []
    for norm_email, group_owners in sorted(email_groups.items()):
        # Union portfolios
        portfolio_pks = set()
        for owner in group_owners:
            for pk in owner.portfolios.values_list("pk", flat=True):
                portfolio_pks.add(pk)

        if not portfolio_pks:
            continue

        rep_owner = min(group_owners, key=lambda o: o.pk)
        owner_name = rep_owner.first_name or (rep_owner.name or "Owner").split()[0]

        # Check each portfolio's distribution snapshot
        portfolios = Portfolio.objects.filter(
            pk__in=portfolio_pks, is_active=True,
        ).order_by("name")

        qualifying_count = 0
        portfolio_info = []

        for portfolio in portfolios:
            snapshot = PortfolioDistributionSnapshot.objects.filter(
                portfolio=portfolio,
                period_type="monthly",
                period_start=period_start,
            ).first()

            if snapshot and snapshot.distribution_amount > 0 and snapshot.distribution_date:
                qualifying_count += 1
                portfolio_info.append({
                    "id": portfolio.pk,
                    "name": portfolio.name,
                    "distribution_amount": str(snapshot.distribution_amount),
                })

        # Only include owners with qualifying distributions
        if qualifying_count == 0:
            continue

        # Check if already sent
        is_sent = EmailDraft.objects.filter(
            product=PRODUCT,
            recipient_email=norm_email,
            period_type="monthly",
            period_start=period_start,
            status="sent",
        ).exists()

        recipients.append({
            "recipient_email": norm_email,
            "owner_name": owner_name,
            "portfolio_count": len(portfolio_pks),
            "qualifying_count": qualifying_count,
            "portfolios": portfolio_info,
            "is_sent": is_sent,
        })

    return JsonResponse(recipients, safe=False)


@require_GET
def preview_recipient(request, email):
    """GET /api/reports/distribution-sends/recipients/{email}/preview"""
    err = _require_access(request)
    if err:
        return err

    parsed = _parse_month(request)
    if not parsed:
        return JsonResponse({"error": "month param required"}, status=400)

    year, month = parsed
    period_start, _ = _period_from_month(year, month)

    assembled = assemble_owner_distribution_email(email, period_start, period_type="monthly")

    if assembled is None:
        return JsonResponse({"body_html": "", "portfolios": []})

    return JsonResponse({
        "owner_name": assembled["owner_name"],
        "body_html": assembled["body_html"],
        "portfolios": assembled["portfolios"],
        "subject": assembled["subject"],
    })


@require_POST
def send_recipient(request, email):
    """POST /api/reports/distribution-sends/recipients/{email}/send"""
    err = _require_access(request)
    if err:
        return err

    parsed = _parse_month(request)
    if not parsed:
        return JsonResponse({"error": "month param required"}, status=400)

    year, month = parsed
    period_start, period_end = _period_from_month(year, month)

    try:
        result = send_distribution_email(
            recipient_email=email,
            period_start=period_start,
            period_type="monthly",
            period_end=period_end,
            acting_user=request.user,
        )

        if result == "already_sent":
            return JsonResponse({"ok": True, "message": "Already sent — skipped."})
        if result == "no_activity":
            return JsonResponse({"ok": False, "error": "No qualifying distributions."}, status=400)

        return JsonResponse({
            "ok": True,
            "message": "Email sent.",
            "recipient": _normalize_email(email),
            "draft_id": result.get("draft_id"),
            "sendgrid_status": result.get("sendgrid_status"),
        })
    except ValueError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    except Exception as exc:
        logger.exception(
            "comms_distribution_send_failed",
            extra={"email": email},
        )
        return JsonResponse(
            {"ok": False, "error": f"Send failed: {exc}"}, status=500
        )


@require_POST
def test_send_recipient(request, email):
    """POST /api/reports/distribution-sends/recipients/{email}/test-send"""
    err = _require_access(request)
    if err:
        return err

    parsed = _parse_month(request)
    if not parsed:
        return JsonResponse({"error": "month param required"}, status=400)

    year, month = parsed
    period_start, period_end = _period_from_month(year, month)

    try:
        result = send_distribution_email(
            recipient_email=email,
            period_start=period_start,
            period_type="monthly",
            period_end=period_end,
            acting_user=request.user,
            recipient_override=request.user.email,
        )

        if result == "no_activity":
            return JsonResponse({"ok": False, "error": "No qualifying distributions."}, status=400)

        return JsonResponse({
            "ok": True,
            "message": f"Test email sent to {request.user.email}",
            "sendgrid_status": result.get("sendgrid_status") if isinstance(result, dict) else None,
        })
    except Exception as exc:
        logger.exception(
            "comms_distribution_test_send_failed",
            extra={"email": email},
        )
        return JsonResponse(
            {"ok": False, "error": f"Test send failed: {exc}"}, status=500
        )
