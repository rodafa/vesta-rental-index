"""
Core logic for generating monthly AI-drafted owner notes.

Entry point: run_monthly_report(month, owner_id, property_id, dry_run)
"""
import logging
from datetime import date, timedelta
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
    "- Write as 'We' / 'our' (Vesta's voice). Be transparent about problems and what's being done.\n"
    "- Open with the reporting period (e.g., 'For the period March 1 through March 31, 2026, ...').\n"
    "- When covering a portfolio with multiple properties, address each property naturally in the flow.\n\n"
    "CORRECT FORMAT: \"For the period March 1 through March 31, 2026, your tenant at 123 Main Street "
    "is current on rent. We received $1,400, of which $400 is still processing. There is one open "
    "work order for an HVAC filter replacement assigned to the vendor on the 14th. Please check "
    "PropertyMeld for real-time status.\"\n\n"
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
        logger.debug("LEADSIMPLE_API_KEY not configured — pipeline context will be empty.")
    if not getattr(settings, "ANTHROPIC_API_KEY", ""):
        raise ImproperlyConfigured("ANTHROPIC_API_KEY is required.")


def _month_bounds(month: date):
    """Return (month_start, month_end) where month_end is the first day of the next month."""
    month_start = month.replace(day=1)
    if month.month == 12:
        month_end = date(month.year + 1, 1, 1)
    else:
        month_end = date(month.year, month.month + 1, 1)
    return month_start, month_end


def collect_property_data(property_obj, owner, month_start, month_end, all_deals: list) -> dict:
    """Call all three data sources; each section is wrapped in its own try/except."""
    data = {
        "address": property_obj.address_line_1,
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


def collect_portfolio_data(portfolio, owner, month_start, month_end, all_deals: list) -> dict:
    """
    Collect data for every active property in the portfolio and return a single
    payload dict that will be turned into one portfolio-level AI note.
    """
    properties = list(portfolio.properties.filter(is_active=True))
    month_end_display = (month_end - timedelta(days=1)).isoformat()  # "2026-03-31"

    prop_data = []
    for prop in properties:
        prop_data.append(collect_property_data(prop, owner, month_start, month_end, all_deals))

    return {
        "owner_id": str(owner.rentvine_contact_id),
        "owner_name": owner.name,
        "portfolio_name": portfolio.name,
        "month_start": month_start.isoformat(),
        "month_end_display": month_end_display,
        "properties": prop_data,
    }


def has_sufficient_data(payload: dict) -> bool:
    """Return False only if there is truly nothing to write about across all properties."""
    return any(
        prop.get("active_lease")
        or prop.get("upcoming_lease")
        or (prop.get("financials") or {}).get("has_data")
        or prop.get("melds")
        or any(
            (prop.get("pipeline") or {}).get(k)
            for k in ("applications", "move_ins", "renewals", "late_rent", "move_outs", "other")
        )
        for prop in payload["properties"]
    )


def build_prompt(payload: dict) -> str:
    """Build the user-turn message from the collected portfolio payload."""
    properties = payload["properties"]
    month_start_str = payload["month_start"]
    month_end_str = payload["month_end_display"]

    # Format dates as "March 1, 2026" / "March 31, 2026"
    ms = date.fromisoformat(month_start_str)
    me = date.fromisoformat(month_end_str)
    period_start = ms.strftime("%B %-d, %Y")
    period_end = me.strftime("%B %-d, %Y")

    word_limit = min(150 + (len(properties) - 1) * 75, 400)

    lines = [
        f"Reporting period: {period_start} through {period_end}",
        f"Owner: {payload['owner_name']}",
        f"Portfolio: {payload['portfolio_name']}",
    ]

    for prop in properties:
        lines.append(f"\n=== {prop['address']} ===")

        al = prop.get("active_lease")
        if al:
            tenants = ", ".join(al["tenant_names"]) or "unknown"
            rent = f"${al['rent_amount']:,.2f}/mo" if al.get("rent_amount") else ""
            lease_end = f"Lease ends: {al['end_date']}" if al.get("end_date") else ""
            summary_parts = [f"Current tenant: {tenants}"]
            if rent:
                summary_parts.append(f"Rent: {rent}")
            if lease_end:
                summary_parts.append(lease_end)
            lines.append(" | ".join(summary_parts))
            if al.get("notice_date"):
                lines.append(f"Notice given: {al['notice_date']}")
            if al.get("expected_move_out"):
                lines.append(f"Expected move-out: {al['expected_move_out']}")

        ul = prop.get("upcoming_lease")
        if ul:
            tenants = ", ".join(ul["tenant_names"]) or "unknown"
            lines.append(f"Upcoming tenant: {tenants}")
            if ul.get("move_in_date"):
                lines.append(f"Move-in date: {ul['move_in_date']}")
            if ul.get("rent_amount"):
                lines.append(f"New rent: ${ul['rent_amount']:,.2f}")

        fin = prop.get("financials")
        if fin and fin.get("has_data"):
            fin_line = f"Financial: charged ${fin['charged']:,.2f}, paid ${fin['paid']:,.2f}"
            if fin["outstanding_balance"] > 0:
                fin_line += f", outstanding ${fin['outstanding_balance']:,.2f}"
            lines.append(fin_line)

        notes = prop.get("tenant_notes") or []
        if notes:
            lines.append(f"Tenant communications (last 45 days):")
            for n in notes[:5]:
                lines.append(f"  [{n['created_at']}] {n['note_text']}")

        melds = prop.get("melds") or []
        if melds:
            lines.append(f"Maintenance (last 30 days) — {len(melds)} item(s):")
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

        pipe = prop.get("pipeline") or {}
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
            lines.append("Active pipeline deals:")
            lines.extend(pipeline_items)

    lines.append(
        f"\nWrite a single flowing owner update note covering ALL properties above. "
        f"Open with the reporting period. Scale length with property count (~{word_limit} words)."
    )

    return "\n".join(lines)


def generate_note(payload: dict) -> str:
    """Call Claude to produce the owner note."""
    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=800,
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
    Main entry point. Generates AI-drafted monthly notes at the portfolio level.
    One note per portfolio (covering all its properties) per owner.

    Args:
        month:       First day of the report month.
        owner_id:    If given, restrict to this RentVine contact ID.
        property_id: If given, restrict to only the portfolio containing this Django Property PK.
        dry_run:     Skip DB writes; only print.

    Returns:
        {"generated": N, "failed": N, "skipped": N}
    """
    _validate_settings()

    month_start, month_end = _month_bounds(month)
    period_label = f"{month_start:%B %-d} \u2013 {(month_end - timedelta(days=1)):%B %-d, %Y}"

    owners_qs = Owner.objects.filter(is_active=True).prefetch_related("portfolios")
    if owner_id:
        owners_qs = owners_qs.filter(rentvine_contact_id=owner_id)

    owners = list(owners_qs)
    if not owners:
        print("No owners found matching criteria.")
        return {"generated": 0, "failed": 0, "skipped": 0}

    # Fetch all LeadSimple deals once to avoid per-property API hammering
    all_deals = ls_source.fetch_all_active_deals()
    if all_deals:
        print(f"LeadSimple: {len(all_deals)} active deals fetched.")

    counters = {"generated": 0, "failed": 0, "skipped": 0}
    DIVIDER = "=" * 70

    for owner in owners:
        for portfolio in owner.portfolios.filter(is_active=True):
            properties = list(portfolio.properties.filter(is_active=True))
            if not properties:
                continue

            if property_id:
                if not any(p.pk == property_id for p in properties):
                    continue

            label = f"{owner.name} / {portfolio.name}"
            try:
                payload = collect_portfolio_data(
                    portfolio, owner, month_start, month_end, all_deals
                )

                if not has_sufficient_data(payload):
                    print(f"\n[SKIPPED] {label} — no data found")
                    counters["skipped"] += 1
                    if not dry_run:
                        OwnerReportLog.objects.create(
                            owner_id=str(owner.rentvine_contact_id),
                            owner_name=owner.name,
                            report_month=month_start,
                            portfolio_name=portfolio.name,
                            status="skipped",
                            report_data=payload,
                        )
                    continue

                note = generate_note(payload)

                print(f"\n{DIVIDER}")
                print(f"OWNER: {owner.name}")
                print(f"PORTFOLIO: {portfolio.name} ({len(properties)} propert{'ies' if len(properties) != 1 else 'y'})")
                print(f"PERIOD: {period_label}")
                print(DIVIDER)
                print(note)
                print(DIVIDER)

                counters["generated"] += 1

                if not dry_run:
                    OwnerReportLog.objects.create(
                        owner_id=str(owner.rentvine_contact_id),
                        owner_name=owner.name,
                        report_month=month_start,
                        portfolio_name=portfolio.name,
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
                        portfolio_name=portfolio.name,
                        status="failed",
                        error_message="Unhandled exception — see server logs",
                    )

    return counters
