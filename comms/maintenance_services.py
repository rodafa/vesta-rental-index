"""
Maintenance-specific service helpers.

Keeps maintenance dashboard logic out of the main comms/services.py, which
carries unrelated in-flight work.  Anything that only the maintenance
dashboard or its management commands need lives here.
"""

import threading
from datetime import date, timedelta

from .models import EmailDraft, PortfolioMaintenanceNote
from .services import _normalize_email


# ---------------------------------------------------------------------------
# Default week helper — single source of truth for "last full Mon-Sun week"
# ---------------------------------------------------------------------------

def default_maintenance_week_start(today=None):
    """Return the Monday that starts the most recent *complete* Mon-Sun week.

    Used by:
      - generate_maintenance_drafts management command (default --period-start)
      - maintenance_notes_page view (template context for the JS)
      - maintenance_api generate endpoint (if no explicit dates given)

    Python weekday(): Monday=0 … Sunday=6.
    Formula: today - (today.weekday() + 7) always lands on last week's Monday,
    regardless of what day "today" is — including Sunday.
    """
    if today is None:
        today = date.today()
    return today - timedelta(days=today.weekday() + 7)


# ---------------------------------------------------------------------------
# Concurrency guard for maintenance generation (independent of portfolio gen)
# ---------------------------------------------------------------------------

_maintenance_gen_lock = threading.Lock()
_maintenance_gen_started_at = None   # monotonic timestamp or None
_MAINTENANCE_GEN_TTL = 1800          # seconds — auto-expire a stuck lock


def note_has_sendable_content(note):
    """Return True when a PortfolioMaintenanceNote has renderable email content.

    Single source of truth used by both approval endpoints and the send loop.
    Keys on notes_html — the field assemble_owner_maintenance_email uses to
    build email fragments.
    """
    return bool(note.notes_html and note.notes_html.strip())


# ---------------------------------------------------------------------------
# Maintenance recipients
# ---------------------------------------------------------------------------

def get_maintenance_recipients_for_period(period_start, period_type="weekly"):
    """
    List all distinct recipient emails for a maintenance period, with readiness info.

    Fork of get_recipients_for_period() — queries PortfolioMaintenanceNote
    and checks EmailDraft with product="maintenance".

    Returns list of dicts (same shape as get_recipients_for_period):
        [{
            "recipient_email": str,
            "owner_name": str,
            "owner_names": [str, ...],
            "portfolio_count": int,
            "portfolios": [{"id": int, "name": str, "status": str}, ...],
            "all_approved": bool,
            "is_sent": bool,
        }, ...]
    """
    from core.models import Owner, Portfolio

    owners = Owner.objects.filter(
        is_active=True,
    ).exclude(
        email=""
    ).exclude(
        email__isnull=True
    ).prefetch_related("portfolios")

    email_groups = {}
    for owner in owners:
        email = _normalize_email(owner.email)
        if not email:
            continue
        email_groups.setdefault(email, []).append(owner)

    recipients = []
    for norm_email, group_owners in sorted(email_groups.items()):
        portfolio_pks = set()
        for owner in group_owners:
            for pk in owner.portfolios.values_list("pk", flat=True):
                portfolio_pks.add(pk)

        if not portfolio_pks:
            continue

        rep_owner = min(group_owners, key=lambda o: o.pk)
        owner_name = rep_owner.first_name or (rep_owner.name or "Owner").split()[0]

        portfolios = Portfolio.objects.filter(
            pk__in=portfolio_pks, is_active=True,
        ).order_by("name")
        portfolio_info = []
        all_approved = True
        has_any_note = False

        for portfolio in portfolios:
            note = PortfolioMaintenanceNote.objects.filter(
                portfolio=portfolio,
                period_type=period_type,
                period_start=period_start,
            ).first()

            if note:
                has_any_note = True
                portfolio_info.append({
                    "id": portfolio.pk,
                    "name": portfolio.name,
                    "status": note.status,
                })
                if note.status != "approved":
                    all_approved = False
            # Quiet portfolios (no note) are simply omitted —
            # they don't gate approval and don't appear in the list.

        # Skip owners with zero notes this period (nothing to send)
        if not has_any_note:
            continue

        is_sent = EmailDraft.objects.filter(
            product="maintenance",
            recipient_email=norm_email,
            period_type=period_type,
            period_start=period_start,
            status="sent",
        ).exists()

        recipients.append({
            "recipient_email": norm_email,
            "owner_name": owner_name,
            "owner_id": rep_owner.pk,
            "owner_names": [o.name for o in sorted(group_owners, key=lambda o: o.pk)],
            "portfolio_count": len(portfolio_info),
            "portfolios": portfolio_info,
            "all_approved": all_approved,
            "is_sent": is_sent,
        })

    return recipients
