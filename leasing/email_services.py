"""
Shared leasing email assembly logic (Layer 2).

Used by both the management command and the dashboard views.
Does NOT send — only assembles EmailDraft rows.
"""

import logging

from django.utils import timezone

from comms.models import EmailDraft
from comms.services import (
    _format_period_label,
    _normalize_email,
    assemble_owner_leasing_email,
)
from core.models import Owner
from leasing.models import PortfolioLeasingNote

logger = logging.getLogger(__name__)


def get_leasing_recipient_emails(period_start, period_end, period_type="weekly"):
    """
    Return the deduplicated set of normalized owner emails that have
    at least one PortfolioLeasingNote for this period.
    """
    note_portfolio_pks = set(
        PortfolioLeasingNote.objects.filter(
            period_type=period_type,
            period_start=period_start,
        ).values_list("portfolio_id", flat=True)
    )
    if not note_portfolio_pks:
        return set()

    emails = (
        Owner.objects.filter(
            is_active=True,
            portfolios__pk__in=note_portfolio_pks,
        )
        .exclude(email="")
        .exclude(email__isnull=True)
        .values_list("email", flat=True)
    )
    return {_normalize_email(e) for e in emails if _normalize_email(e)}


def assemble_leasing_drafts(period_start, period_end, period_type="weekly", owner_email=None):
    """
    Assemble EmailDraft rows for leasing emails.

    Refuses to assemble if any PortfolioLeasingNote for the period is
    not approved — returns immediately with blocking_portfolios populated.

    Returns dict:
        {
            "created": int,
            "updated": int,
            "skipped": int,
            "errors": [str],
            "blocking_portfolios": [str],
        }
    """
    result = {"created": 0, "updated": 0, "skipped": 0, "errors": [], "blocking_portfolios": []}

    # Gate: ALL notes for this period must be approved
    notes_for_period = PortfolioLeasingNote.objects.filter(
        period_type=period_type,
        period_start=period_start,
    ).select_related("portfolio")

    blocking = [n.portfolio.name for n in notes_for_period if n.status != "approved"]
    if blocking:
        result["blocking_portfolios"] = blocking
        return result

    # Build target email set
    if owner_email:
        norm = _normalize_email(owner_email)
        if not norm or not Owner.objects.filter(is_active=True, email__iexact=norm).exists():
            result["errors"].append(f"No active owner with email {owner_email}")
            return result
        target_emails = {norm}
    else:
        target_emails = get_leasing_recipient_emails(period_start, period_end, period_type)

    if not target_emails:
        result["errors"].append("No recipients found")
        return result

    period_label = _format_period_label(period_type, period_start, period_end)
    subject = f"Weekly Leasing Update \u2014 {period_label}"
    now = timezone.now()

    for norm_email in sorted(target_emails):
        try:
            assembled = assemble_owner_leasing_email(
                norm_email, period_start, period_end, period_type=period_type,
            )

            if assembled is None:
                result["skipped"] += 1
                continue

            rep_owner = assembled["rep_owner"]

            draft, was_created = EmailDraft.objects.update_or_create(
                product="leasing",
                owner=rep_owner,
                period_type=period_type,
                period_start=period_start,
                defaults={
                    "recipient_email": norm_email,
                    "subject": subject,
                    "body_html": assembled["body_html"],
                    "generated_note": "",
                    "period_end": period_end,
                    "status": "draft",
                    "sent_at": None,
                    "sent_by": None,
                },
            )

            # Force-update generated_at to track assembly time.
            # auto_now_add doesn't update on save, so use queryset.update.
            EmailDraft.objects.filter(pk=draft.pk).update(generated_at=now)

            if was_created:
                result["created"] += 1
            else:
                result["updated"] += 1

            logger.info(
                "leasing_draft_assembled",
                extra={
                    "draft_id": draft.pk,
                    "recipient": norm_email,
                    "owner": str(rep_owner),
                    "action": "created" if was_created else "updated",
                },
            )

        except Exception as exc:
            result["errors"].append(f"{norm_email}: {exc}")
            logger.exception(
                "leasing_draft_assembly_failed",
                extra={"recipient": norm_email},
            )

    return result
