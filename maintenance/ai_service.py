import logging
from datetime import timedelta

import anthropic
from django.conf import settings
from django.db.models import Count, TruncMonth
from django.utils import timezone

from maintenance.playbook import MAINTENANCE_PLAYBOOK
from maintenance.property_meld_docs import PROPERTY_MELD_DOCS

logger = logging.getLogger(__name__)


def _business_days_ago(n: int, from_dt=None):
    """Return a datetime n business days before from_dt (or now). Mon–Fri only."""
    dt = from_dt or timezone.now()
    count = 0
    while count < n:
        dt -= timedelta(days=1)
        if dt.weekday() < 5:  # Mon=0 … Fri=4
            count += 1
    return dt


def _get_live_snapshot() -> str:
    """Query current Meld stats and return a compact context block for the prompt."""
    try:
        from maintenance.models import Meld
        from integrations.property_meld.mappers import OPEN_STATUSES

        now = timezone.now()
        today = now.date()
        cutoff_24h = now - timedelta(hours=24)
        open_statuses = list(OPEN_STATUSES)

        cutoff_2bd = _business_days_ago(2, now)

        new_today = Meld.objects.filter(source_created_at__date=today).count()
        new_24h = Meld.objects.filter(source_created_at__gte=cutoff_24h).count()
        total_open = Meld.objects.filter(status__in=open_statuses).count()
        emergencies = Meld.objects.filter(status__in=open_statuses, priority="EMERGENCY").count()
        stale_open = Meld.objects.filter(
            status__in=open_statuses,
            source_modified_at__lt=cutoff_2bd,
        ).count()

        status_rows = (
            Meld.objects.filter(status__in=open_statuses)
            .values("status")
            .annotate(n=Count("id"))
            .order_by("-n")
        )
        priority_rows = (
            Meld.objects.filter(status__in=open_statuses)
            .values("priority")
            .annotate(n=Count("id"))
            .order_by("-n")
        )

        status_lines = "\n".join(f"  {r['status']}: {r['n']}" for r in status_rows)
        priority_lines = "\n".join(f"  {r['priority']}: {r['n']}" for r in priority_rows)

        # Open melds grouped by creation month (most recent first, up to 12 months)
        open_by_month = (
            Meld.objects.filter(status__in=open_statuses, source_created_at__isnull=False)
            .annotate(month=TruncMonth("source_created_at"))
            .values("month")
            .annotate(n=Count("id"))
            .order_by("-month")[:12]
        )
        month_lines = "\n".join(
            f"  {r['month'].strftime('%B %Y')}: {r['n']}" for r in open_by_month
        )

        return (
            f"[LIVE MELD DATA — {now.strftime('%Y-%m-%d %H:%M UTC')}]\n"
            f"New work orders today ({today}): {new_today}\n"
            f"New work orders in last 24h: {new_24h}\n"
            f"Total open work orders: {total_open}\n"
            f"Open emergencies: {emergencies}\n"
            f"Open by status:\n{status_lines}\n"
            f"Open by priority:\n{priority_lines}\n"
            f"Open work orders by creation month (still open, grouped by when created):\n{month_lines}\n"
            f"Open with no activity in last 2 business days (source_modified_at < {cutoff_2bd.strftime('%Y-%m-%d')}): {stale_open}\n"
        )
    except Exception:
        logger.exception("Could not fetch live meld snapshot for Vulcan")
        return ""

SYSTEM_PROMPT = (
    "You are Vulcan, Vesta PM's internal maintenance assistant — named after the Roman god of fire "
    "and the forge. You help property management staff answer questions about work orders, vendors, "
    "maintenance procedures, and troubleshooting.\n\n"
    "You have three knowledge sources:\n"
    "1. Live Meld data — real-time work order counts and statuses pulled from the database, "
    "provided at the top of each message inside [LIVE MELD DATA ...] block.\n"
    "2. The Vesta Maintenance Playbook — Vesta-specific policies, scripts, and procedures.\n"
    "3. Property Meld Help Documentation — official help docs on how to use Property Meld.\n\n"
    "When answering a question:\n"
    "- For questions about current counts, statuses, or today's activity, use the live data block.\n"
    "- If your answer comes from the Vesta playbook, start with ✅ Vesta Policy:\n"
    "- If your answer comes from the Property Meld help docs, start with 📖 Property Meld Docs:\n"
    "- If your answer draws from both sources, use both labels in your response.\n"
    "- If your answer is not covered by either source and you are drawing from general knowledge, "
    "start with ⚠️ General Guidance (not in Vesta playbook or Property Meld docs):\n"
    "- If something contradicts or goes beyond the known sources, flag it clearly.\n\n"
    "Be concise, sharp, and practical. Always be clear about which source you're drawing from.\n\n"
    "Here is the Vesta Maintenance Playbook:\n\n"
    + MAINTENANCE_PLAYBOOK
    + "\n\nHere is the Property Meld Help Documentation:\n\n"
    + PROPERTY_MELD_DOCS
)


def handle_mention(user_text: str, thread_ts: str, channel: str) -> str:
    live_data = _get_live_snapshot()
    content = f"{live_data}\n{user_text}" if live_data else user_text

    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    message = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": content}],
    )
    return message.content[0].text
