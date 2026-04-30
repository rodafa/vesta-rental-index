import logging
from datetime import timedelta

import anthropic
from django.conf import settings
from django.db.models import Count
from django.db.models.functions import TruncMonth
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
        cutoff_48h = now - timedelta(hours=48)
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

        comms_gap_count = Meld.objects.filter(
            status__in=open_statuses,
            source_modified_at__isnull=False,
            source_modified_at__lt=cutoff_48h,
        ).count()

        unscheduled_aging_count = Meld.objects.filter(
            status__in=open_statuses,
            assigned_vendor_name="",
            scheduled_date__isnull=True,
            source_created_at__isnull=False,
            source_created_at__lte=cutoff_48h,
        ).count()

        past_due_count = Meld.objects.filter(
            status__in=open_statuses,
            scheduled_date__lt=today,
        ).count()

        stale_approvals_count = Meld.objects.filter(
            status__in=open_statuses,
            owner_approval_status="Requested",
            source_modified_at__isnull=False,
            source_modified_at__lt=cutoff_48h,
        ).count()

        return (
            f"[LIVE MELD DATA — {now.strftime('%Y-%m-%d %H:%M UTC')}]\n"
            f"New work orders today ({today}): {new_today}\n"
            f"New work orders in last 24h: {new_24h}\n"
            f"Total open work orders: {total_open}\n"
            f"Open emergencies: {emergencies}\n"
            f"Open by status:\n{status_lines}\n"
            f"Open by priority:\n{priority_lines}\n"
            f"Open work orders by creation month (still open, grouped by when created):\n{month_lines}\n"
            f"Open with no activity in last 2 business days: {stale_open}\n"
            f"Comms gap (open, 48h+ no activity): {comms_gap_count}\n"
            f"Unscheduled & aging (open, no vendor, no date, 48h+ old): {unscheduled_aging_count}\n"
            f"Past due (open, scheduled date passed): {past_due_count}\n"
            f"Stale owner approvals (open, approval requested, 48h+ waiting): {stale_approvals_count}\n"
        )
    except Exception:
        logger.exception("Could not fetch live meld snapshot for Vulcan")
        return ""


def _get_summary_detail(sections: set) -> str:
    """
    Build full item-by-item detail for one or more daily summary sections.
    `sections` is a set of section keys, e.g. {"comms_gap", "past_due"}.
    """
    try:
        from maintenance.models import Meld
        from integrations.property_meld.mappers import OPEN_STATUSES

        now = timezone.now()
        today = now.date()
        cutoff_48h = now - timedelta(hours=48)
        cutoff_90d = today - timedelta(days=90)
        open_statuses = list(OPEN_STATUSES)
        parts = []

        if "emergencies" in sections:
            melds = list(
                Meld.objects.filter(status__in=open_statuses, priority="EMERGENCY")
                .order_by("source_created_at")
            )
            if melds:
                lines = [f"[EMERGENCIES — {len(melds)} open]"]
                for m in melds:
                    addr = m.property_address or "No address"
                    desc = (m.brief_description or "")[:60]
                    lines.append(f"  • {addr} — {desc}")
            else:
                lines = ["[EMERGENCIES — 0 open]"]
            parts.append("\n".join(lines))

        if "unscheduled_aging" in sections:
            melds = list(
                Meld.objects.filter(
                    status__in=open_statuses,
                    assigned_vendor_name="",
                    scheduled_date__isnull=True,
                    source_created_at__isnull=False,
                    source_created_at__lte=cutoff_48h,
                ).order_by("source_created_at")
            )
            if melds:
                lines = [f"[UNSCHEDULED & AGING — {len(melds)} open melds, 48h+ old, no vendor]"]
                for m in melds:
                    delta = now - m.source_created_at
                    hours = int(delta.total_seconds() / 3600)
                    age = f"{hours}h" if hours < 48 else f"{delta.days}d"
                    addr = m.property_address or "No address"
                    lines.append(f"  • {addr} | {age} old | {m.status}")
            else:
                lines = ["[UNSCHEDULED & AGING — 0]"]
            parts.append("\n".join(lines))

        if "repeat_addresses" in sections:
            rows = list(
                Meld.objects.filter(
                    source_created_at__date__gte=cutoff_90d,
                    property_address__gt="",
                )
                .values("property_address")
                .annotate(meld_count=Count("id"))
                .filter(meld_count__gte=2)
                .order_by("-meld_count")
            )
            if rows:
                lines = [f"[REPEAT ADDRESSES — {len(rows)} addresses with 2+ melds in last 90 days]"]
                for r in rows:
                    lines.append(f"  • {r['property_address']} — {r['meld_count']} melds")
            else:
                lines = ["[REPEAT ADDRESSES — none in last 90 days]"]
            parts.append("\n".join(lines))

        if "keyword_flags" in sections:
            from integrations.management.commands.property_meld_daily_summary import _find_keyword_matches
            open_melds = list(
                Meld.objects.filter(status__in=open_statuses)
                .only("brief_description", "property_address", "source_created_at")
            )
            flagged = []
            for m in open_melds:
                kws = _find_keyword_matches(m.brief_description)
                if kws:
                    flagged.append((m, kws))
            if flagged:
                lines = [f"[KEYWORD FLAGS — {len(flagged)} open melds with risk keywords]"]
                for m, kws in flagged:
                    addr = m.property_address or "No address"
                    kw_str = ", ".join(kws[:3])
                    age = f"{(today - m.source_created_at.date()).days}d" if m.source_created_at else "?"
                    lines.append(f"  • [{kw_str}] {addr} — {age} open")
            else:
                lines = ["[KEYWORD FLAGS — none]"]
            parts.append("\n".join(lines))

        if "comms_gap" in sections:
            melds = list(
                Meld.objects.filter(
                    status__in=open_statuses,
                    source_modified_at__isnull=False,
                    source_modified_at__lt=cutoff_48h,
                ).order_by("source_modified_at")
            )
            if melds:
                lines = [f"[COMMS GAP — {len(melds)} open melds, 48h+ no activity]"]
                for m in melds:
                    days = (now - m.source_modified_at).days
                    addr = m.property_address or "No address"
                    vendor = m.assigned_vendor_name or "No vendor"
                    lines.append(f"  • {addr} | {days}d silent | {m.status} | vendor: {vendor}")
            else:
                lines = ["[COMMS GAP — 0]"]
            parts.append("\n".join(lines))

        if "stale_approvals" in sections:
            melds = list(
                Meld.objects.filter(
                    status__in=open_statuses,
                    owner_approval_status="Requested",
                    source_modified_at__isnull=False,
                    source_modified_at__lt=cutoff_48h,
                ).order_by("source_modified_at")
            )
            if melds:
                lines = [f"[STALE OWNER APPROVALS — {len(melds)} open melds waiting 48h+]"]
                for m in melds:
                    days = (now - m.source_modified_at).days
                    addr = m.property_address or "No address"
                    lines.append(f"  • {addr} — {days}d waiting")
            else:
                lines = ["[STALE OWNER APPROVALS — 0]"]
            parts.append("\n".join(lines))

        if "vendor_ghosting" in sections:
            rows = list(
                Meld.objects.filter(
                    status__in=open_statuses,
                    source_modified_at__isnull=False,
                    source_modified_at__lt=cutoff_48h,
                )
                .exclude(assigned_vendor_name="")
                .values("assigned_vendor_name")
                .annotate(meld_count=Count("id"))
                .order_by("-meld_count")
            )
            if rows:
                lines = [f"[VENDOR GHOSTING — {len(rows)} vendors with 48h+ open assigned melds]"]
                for r in rows:
                    lines.append(f"  • {r['assigned_vendor_name']} — {r['meld_count']} melds")
            else:
                lines = ["[VENDOR GHOSTING — 0]"]
            parts.append("\n".join(lines))

        if "past_due" in sections:
            melds = list(
                Meld.objects.filter(status__in=open_statuses, scheduled_date__lt=today)
                .order_by("scheduled_date")
            )
            if melds:
                lines = [f"[PAST DUE — {len(melds)} open melds past scheduled date]"]
                for m in melds:
                    overdue = (today - m.scheduled_date).days
                    addr = m.property_address or "No address"
                    vendor = m.assigned_vendor_name or "No vendor"
                    lines.append(f"  • {addr} — {overdue}d overdue | vendor: {vendor}")
            else:
                lines = ["[PAST DUE — 0]"]
            parts.append("\n".join(lines))

        return "\n\n".join(parts) + "\n" if parts else ""
    except Exception:
        logger.exception("Could not fetch summary detail for Vulcan")
        return ""


# Maps section keys to trigger phrases. Order matters for multi-word phrases — put longer ones first.
_SECTION_TRIGGERS = [
    ("unscheduled_aging", [
        "unscheduled and aging", "unscheduled aging", "unscheduled melds",
        "aging melds", "unscheduled", "no vendor assigned",
    ]),
    ("repeat_addresses", [
        "repeat addresses", "repeat address", "recurring address",
        "recurring melds", "chronic", "keeps coming up",
    ]),
    ("keyword_flags", [
        "keyword flags", "keyword flag", "risk keywords", "flagged melds",
        "flagged", "risk flag",
    ]),
    ("comms_gap", [
        "comms gap", "communication gap", "no activity", "stale melds",
        "no communication", "gone silent",
    ]),
    ("stale_approvals", [
        "stale approvals", "stale approval", "owner approval",
        "pending approval", "approval requested",
    ]),
    ("vendor_ghosting", [
        "vendor ghosting", "vendor ghost", "ghosting vendors", "ghosting",
    ]),
    ("past_due", [
        "past due", "past-due", "overdue melds", "overdue",
    ]),
    ("emergencies", [
        "emergencies", "emergency melds", "emergency",
    ]),
]

_ALL_SECTIONS = {key for key, _ in _SECTION_TRIGGERS}

# Trigger phrases that request the full summary expansion
_EXPAND_ALL_TRIGGERS = [
    "daily summary", "daily pulse", "maintenance summary",
    "expand maintenance", "expand the maintenance", "all sections",
    "full summary", "full list",
]


def _requested_sections(text: str) -> set:
    t = text.lower()
    if any(trigger in t for trigger in _EXPAND_ALL_TRIGGERS):
        return _ALL_SECTIONS
    sections = set()
    for section_key, triggers in _SECTION_TRIGGERS:
        if any(trigger in t for trigger in triggers):
            sections.add(section_key)
    return sections


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
    "- When a [DETAIL] block is present in the context, use it to provide the full item list.\n"
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
    sections = _requested_sections(user_text)
    detail = _get_summary_detail(sections) if sections else ""
    parts = [p for p in [live_data, detail, user_text] if p]
    content = "\n".join(parts)

    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    message = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": content}],
    )
    return message.content[0].text
