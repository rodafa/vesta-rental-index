"""
Core logic for generating monthly AI-drafted owner notes.

Entry point: run_monthly_report(month, owner_id, property_id, dry_run)
"""
import logging
from datetime import date
from calendar import monthrange

import anthropic
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from properties.models import Owner
from reports.models import OwnerReportLog
from reports.services.data_sources import rentvine as rv_source
from reports.services.data_sources import propertymeld as pm_source
from reports.services.data_sources import leadsimple as ls_source

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are a property manager at Vesta Property Management writing a monthly owner update note. "
    "This note will be pasted directly into RentVine's owner notes field.\n\n"
    "STRICT FORMATTING RULES:\n"
    "- Flowing paragraph sentences only. No bullet points, no dashes, no headers, no bold, no markdown.\n"
    "- No blank lines between topics. One continuous block of text.\n"
    "- No section labels or titles.\n"
    "- Include only relevant and actionable information.\n"
    "- When more detail exists than belongs, close with a pointer to PropertyMeld or the RentVine portal.\n"
    "- Write as 'We' / 'our' (Vesta's voice). Be transparent about problems and what's being done.\n\n"
    "CORRECT FORMAT: \"Your tenant at 123 Main Street is current on rent for the month. We received "
    "$1,400, of which $400 is still processing. There is one open work order for an HVAC filter "
    "replacement assigned to the vendor on the 14th. Please check PropertyMeld for real-time status.\"\n\n"
    "INCORRECT FORMAT: \"Rent Status:\\n- Received: $1,400\\nMaintenance:\\n• HVAC filter — open\""
)


def _validate_settings():
    rv = getattr(settings, "RENTVINE", {})
    if not all([rv.get("API_KEY"), rv.get("API_SECRET"), rv.get("SUBDOMAIN")]):
        raise ImproperlyConfigured(
            "RENTVINE settings incomplete — RENTVINE_API_KEY, RENTVINE_API_SECRET, "
            "and RENTVINE_SUBDOMAIN are all required."
        )
    ls = getattr(settings, "LEADSIMPLE", {})
    if not ls.get("API_KEY"):
        logger.warning("LEADSIMPLE_API_KEY not configured — pipeline context will be empty.")
    if not getattr(settings, "ANTHROPIC_API_KEY", ""):
        raise ImproperlyConfigured("ANTHROPIC_API_KEY is required.")


def _month_bounds(month: date):
    """Return (month_start, month_end) where month_end is the first day of the next month."""
    month_start = month.replace(day=1)
    last_day = monthrange(month.year, month.month)[1]
    month_end = month.replace(day=last_day + 1) if last_day < 31 else date(
        month.year + (month.month == 12), (month.month % 12) + 1, 1
    )
    # Cleaner: next month first day
    if month.month == 12:
        month_end = date(month.year + 1, 1, 1)
    else:
        month_end = date(month.year, month.month + 1, 1)
    return month_start, month_end


def collect_property_data(property_obj, owner, month_start, month_end, all_deals: list) -> dict:
    """Call all three data sources; each section is wrapped in its own try/except."""
    data = {
        "owner_id": str(owner.rentvine_contact_id),
        "owner_name": owner.name,
        "property_id": property_obj.pk,
        "property_address": property_obj.address_line_1,
        "report_month": month_start.isoformat(),
        "active_lease": None,
        "upcoming_lease": None,
        "financials": None,
        "tenant_notes": [],
        "melds": [],
        "pipeline": None,
    }

    active_lease = None
    try:
        active_lease = rv_source.get_active_lease(property_obj)
        if active_lease:
            data["active_lease"] = {
                "rentvine_id": active_lease.rentvine_id,
                "tenant_names": [t.name for t in active_lease.tenants.all()],
                "rent_amount": float(active_lease.rent_amount or 0),
                "start_date": active_lease.start_date.isoformat() if active_lease.start_date else None,
                "end_date": active_lease.end_date.isoformat() if active_lease.end_date else None,
                "notice_date": active_lease.notice_date.isoformat() if active_lease.notice_date else None,
                "expected_move_out": (
                    active_lease.expected_move_out_date.isoformat()
                    if active_lease.expected_move_out_date else None
                ),
                "move_out_status": active_lease.move_out_status,
            }
    except Exception:
        logger.warning("Failed to fetch active lease for property %s", property_obj.pk, exc_info=True)

    try:
        upcoming_lease = rv_source.get_upcoming_lease(property_obj)
        if upcoming_lease:
            data["upcoming_lease"] = {
                "rentvine_id": upcoming_lease.rentvine_id,
                "tenant_names": [t.name for t in upcoming_lease.tenants.all()],
                "rent_amount": float(upcoming_lease.rent_amount or 0),
                "move_in_date": upcoming_lease.move_in_date.isoformat() if upcoming_lease.move_in_date else None,
                "start_date": upcoming_lease.start_date.isoformat() if upcoming_lease.start_date else None,
            }
    except Exception:
        logger.warning("Failed to fetch upcoming lease for property %s", property_obj.pk, exc_info=True)

    try:
        data["financials"] = rv_source.get_financial_summary(property_obj, month_start, month_end)
    except Exception:
        logger.warning("Failed to fetch financials for property %s", property_obj.pk, exc_info=True)

    try:
        data["tenant_notes"] = rv_source.get_tenant_notes(active_lease)
    except Exception:
        logger.warning("Failed to fetch tenant notes for property %s", property_obj.pk, exc_info=True)

    try:
        data["melds"] = pm_source.get_recent_melds(property_obj)
    except Exception:
        logger.warning("Failed to fetch melds for property %s", property_obj.pk, exc_info=True)

    try:
        data["pipeline"] = ls_source.get_property_pipeline_context(property_obj, all_deals)
    except Exception:
        logger.warning("Failed to fetch pipeline context for property %s", property_obj.pk, exc_info=True)

    return data


def has_sufficient_data(payload: dict) -> bool:
    """Return False only if there is truly nothing to write about."""
    return bool(
        payload.get("active_lease")
        or payload.get("upcoming_lease")
        or (payload.get("financials") or {}).get("has_data")
        or payload.get("melds")
        or any(
            payload.get("pipeline", {}).get(k)
            for k in ("applications", "move_ins", "renewals", "late_rent", "move_outs", "other")
        )
    )


def build_prompt(payload: dict) -> str:
    """Build the user-turn message from the collected payload. Omits empty sections."""
    lines = [
        f"Property: {payload['property_address']}",
        f"Owner: {payload['owner_name']}",
        f"Report month: {payload['report_month'][:7]}",
    ]

    al = payload.get("active_lease")
    if al:
        tenants = ", ".join(al["tenant_names"]) or "unknown"
        lines.append(f"\nCurrent tenants: {tenants}")
        if al.get("rent_amount"):
            lines.append(f"Monthly rent: ${al['rent_amount']:,.2f}")
        if al.get("end_date"):
            lines.append(f"Lease end date: {al['end_date']}")
        if al.get("notice_date"):
            lines.append(f"Notice given: {al['notice_date']}")
        if al.get("expected_move_out"):
            lines.append(f"Expected move-out: {al['expected_move_out']}")

    ul = payload.get("upcoming_lease")
    if ul:
        tenants = ", ".join(ul["tenant_names"]) or "unknown"
        lines.append(f"\nUpcoming tenant: {tenants}")
        if ul.get("move_in_date"):
            lines.append(f"Move-in date: {ul['move_in_date']}")
        if ul.get("rent_amount"):
            lines.append(f"New rent: ${ul['rent_amount']:,.2f}")

    fin = payload.get("financials")
    if fin and fin.get("has_data"):
        lines.append(f"\nFinancial summary for the month:")
        lines.append(f"  Rent charged: ${fin['charged']:,.2f}")
        lines.append(f"  Rent paid: ${fin['paid']:,.2f}")
        if fin["outstanding_balance"] > 0:
            lines.append(f"  Outstanding balance: ${fin['outstanding_balance']:,.2f}")

    notes = payload.get("tenant_notes") or []
    if notes:
        lines.append("\nRecent tenant communications (last 45 days):")
        for n in notes[:5]:
            lines.append(f"  [{n['created_at']}] {n['note_text']}")

    melds = payload.get("melds") or []
    if melds:
        lines.append(f"\nRecent maintenance (last 30 days) — {len(melds)} item(s):")
        for m in melds[:8]:
            parts = [f"  - {m['description']} [{m['status']}]"]
            if m.get("category"):
                parts.append(f"category: {m['category']}")
            if m.get("priority") and m["priority"] != "LOW":
                parts.append(f"priority: {m['priority']}")
            if m.get("vendor"):
                parts.append(f"vendor: {m['vendor']}")
            if m.get("scheduled_date"):
                parts.append(f"scheduled: {m['scheduled_date']}")
            if m.get("completed_date"):
                parts.append(f"completed: {m['completed_date']}")
            lines.append(", ".join(parts))

    pipe = payload.get("pipeline") or {}
    pipeline_items = []
    for key, label in [
        ("applications", "Application in progress"),
        ("move_ins", "Move-in in progress"),
        ("renewals", "Renewal in progress"),
        ("late_rent", "Late rent / delinquency"),
        ("move_outs", "Move-out in progress"),
    ]:
        for deal in pipe.get(key, []):
            pipeline_items.append(
                f"  - {label}: {deal['name']} (stage: {deal['stage_name']}, "
                f"since: {deal['created_at']})"
            )

    if pipeline_items:
        lines.append("\nActive pipeline deals:")
        lines.extend(pipeline_items)

    lines.append(
        "\nUsing the data above, write a concise monthly owner update note in plain prose "
        "(no bullets, no headers, one paragraph). Maximum ~150 words."
    )

    return "\n".join(lines)


def generate_note(payload: dict) -> str:
    """Call Claude to produce the owner note."""
    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=500,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": build_prompt(payload)}],
    )
    return message.content[0].text.strip()


def run_monthly_report(
    month: date,
    owner_id: str = None,
    property_id: int = None,
    dry_run: bool = False,
) -> dict:
    """
    Main entry point. Generates AI-drafted monthly notes for all matching
    owner/property combinations, printing each note to stdout.

    Args:
        month:       First day of the report month.
        owner_id:    If given, restrict to this RentVine contact ID.
        property_id: If given, restrict to this Django Property PK.
        dry_run:     Skip DB writes; only print.

    Returns:
        {"generated": N, "failed": N, "skipped": N}
    """
    _validate_settings()

    month_start, month_end = _month_bounds(month)

    owners_qs = Owner.objects.filter(is_active=True).prefetch_related("portfolios")
    if owner_id:
        owners_qs = owners_qs.filter(rentvine_contact_id=owner_id)

    owners = list(owners_qs)
    if not owners:
        print("No owners found matching criteria.")
        return {"generated": 0, "failed": 0, "skipped": 0}

    # Fetch all LeadSimple deals once to avoid per-property API hammering
    print("Fetching LeadSimple pipeline deals …")
    all_deals = ls_source.fetch_all_active_deals()
    print(f"  {len(all_deals)} active deals fetched.")

    counters = {"generated": 0, "failed": 0, "skipped": 0}
    DIVIDER = "=" * 70

    for owner in owners:
        properties = rv_source.get_owner_properties(owner)
        if not properties:
            continue

        if property_id:
            properties = [p for p in properties if p.pk == property_id]

        for prop in properties:
            label = f"{owner.name} / {prop.address_line_1}"
            try:
                payload = collect_property_data(prop, owner, month_start, month_end, all_deals)

                if not has_sufficient_data(payload):
                    print(f"\n[SKIPPED] {label} — no data found")
                    counters["skipped"] += 1
                    if not dry_run:
                        OwnerReportLog.objects.create(
                            owner_id=str(owner.rentvine_contact_id),
                            owner_name=owner.name,
                            report_month=month_start,
                            property_address=prop.address_line_1,
                            status="skipped",
                            report_data=payload,
                        )
                    continue

                note = generate_note(payload)

                print(f"\n{DIVIDER}")
                print(f"OWNER: {owner.name}")
                print(f"PROPERTY: {prop.address_line_1}")
                print(f"MONTH: {month_start:%B %Y}")
                print(DIVIDER)
                print(note)
                print(DIVIDER)

                counters["generated"] += 1

                if not dry_run:
                    OwnerReportLog.objects.create(
                        owner_id=str(owner.rentvine_contact_id),
                        owner_name=owner.name,
                        report_month=month_start,
                        property_address=prop.address_line_1,
                        status="success",
                        report_data=payload,
                        generated_note=note,
                    )

            except Exception:
                logger.exception("Failed generating note for %s", label)
                counters["failed"] += 1
                if not dry_run:
                    OwnerReportLog.objects.create(
                        owner_id=str(owner.rentvine_contact_id),
                        owner_name=owner.name,
                        report_month=month_start,
                        property_address=prop.address_line_1,
                        status="failed",
                        error_message=f"Unhandled exception — see server logs",
                    )

    return counters
