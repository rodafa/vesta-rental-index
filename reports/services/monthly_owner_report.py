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
    "You are writing a monthly property update note for a real estate investor. "
    "The note will be pasted directly into a property management portal and read "
    "by the property owner.\n\n"

    "Formatting rules — follow these exactly:\n"
    "- Begin with the property address on its own line, nothing before it\n"
    "- Use bullet points throughout\n"
    "- Nest sub-bullets where needed for clarity\n"
    "- No bold, no markdown headers, no dividing lines, no preamble\n"
    "- Do not start with \"For the period...\" or any greeting\n"
    "- Only include information that is relevant and actionable\n"
    "- Do not mention cancelled work orders\n"
    "- Do not include vendor names — refer to any outside vendor as "
    "\"one of our trusted vendors\"\n"
    "- When full detail exists beyond what belongs in the note, close with "
    "a pointer to PropertyMeld or the RentVine owner portal\n\n"

    "Data sequence — present information in this order, omitting any section "
    "with no relevant data:\n"
    "1. Portfolio-level funds received and any pending/processing amounts\n"
    "2. Reserve balance reminder if below minimum\n"
    "3. Per-lease: end date, total rent due (including pet rent), rent received, "
    "overdue balance if any\n"
    "4. LeadSimple process updates (Applications, Move-Ins, Lease Renewals, "
    "Late Rent, Move-Outs, Issues) — active records only, in Vesta's voice, "
    "never mention LeadSimple by name\n"
    "5. Maintenance: all work orders active at any point during the period, "
    "excluding cancelled ones\n"
    "6. Leasing/vacancy updates if applicable\n\n"

    "Tone: warm, direct, and transparent. Every problem is paired with what is "
    "being done about it. Owners are investors — be clear and do not bury "
    "important information."
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
                "pet_rent_amount": float(active_lease.pet_rent_amount or 0),
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
        data["melds"] = pm_source.get_melds_for_period(property_obj, month_start, month_end)
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

    portfolio_financials = None
    try:
        portfolio_financials = rv_source.get_portfolio_financial_summary(
            portfolio, month_start, month_end
        )
    except Exception:
        logger.warning(
            "Failed to fetch portfolio financials for portfolio %s", portfolio.pk, exc_info=True
        )

    prop_data = []
    for prop in properties:
        prop_data.append(collect_property_data(prop, owner, month_start, month_end, all_deals))

    return {
        "owner_id": str(owner.rentvine_contact_id),
        "owner_name": owner.name,
        "portfolio_name": portfolio.name,
        "month_start": month_start.isoformat(),
        "month_end_display": month_end_display,
        "portfolio_financials": portfolio_financials,
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
            for k in ("applications", "move_ins", "renewals", "late_rent", "move_outs", "issues", "other")
        )
        for prop in payload["properties"]
    )


# Statuses that indicate a work order is resolved/closed (not cancelled — those
# are filtered at query time in propertymeld.py).
_MELD_CLOSED_STATUSES = {
    "COMPLETED",
    "COMPLETE",
    "CLOSED",
    "MAINTENANCE_COULD_NOT_COMPLETE",
    "VENDOR_COULD_NOT_COMPLETE",
}


def _classify_melds(melds: list) -> tuple[list, list]:
    """
    Split a list of meld dicts into (open_melds, completed_melds).
    Cancelled melds are already excluded before this point.
    """
    open_melds = [m for m in melds if m.get("status", "").upper() not in _MELD_CLOSED_STATUSES]
    completed_melds = [m for m in melds if m.get("status", "").upper() in _MELD_CLOSED_STATUSES]
    return open_melds, completed_melds


def _format_meld(m: dict) -> str:
    """Format a single meld dict into a compact prompt line. Vendor name is never included."""
    parts = [f"  - {m['description']} [{m['status']}]"]
    if m.get("category"):
        parts.append(f"category: {m['category']}")
    if m.get("priority") and m["priority"].upper() != "LOW":
        parts.append(f"priority: {m['priority']}")
    if m.get("has_vendor"):
        parts.append("handled by vendor")
    if m.get("scheduled_date"):
        parts.append(f"scheduled: {m['scheduled_date']}")
    if m.get("completed_date"):
        parts.append(f"completed: {m['completed_date']}")
    return ", ".join(parts)


def build_prompt(payload: dict) -> str:
    """Build the user-turn message from the collected portfolio payload."""
    properties = payload["properties"]
    month_start_str = payload["month_start"]
    month_end_str = payload["month_end_display"]

    ms = date.fromisoformat(month_start_str)
    me = date.fromisoformat(month_end_str)
    period_start = ms.strftime("%B %-d, %Y")
    period_end = me.strftime("%B %-d, %Y")

    word_limit = min(250 + (len(properties) - 1) * 100, 600)

    lines = [
        f"Reporting period: {period_start} through {period_end}",
        f"Owner: {payload['owner_name']}",
        f"Portfolio: {payload['portfolio_name']}",
    ]

    # ── 1. PORTFOLIO-LEVEL FINANCIALS ─────────────────────────────────────────
    pf = payload.get("portfolio_financials")
    if pf:
        lines.append(
            f"Portfolio total received this period: ${pf['total_received']:,.2f} "
            "(no clearing/pending status data available — report only the confirmed total received)"
        )
        reserve = pf["reserve_amount"] + pf["additional_reserve_amount"]
        if reserve > 0:
            lines.append(f"Portfolio reserve balance on file: ${reserve:,.2f}")
        else:
            lines.append(
                "Portfolio reserve balance: $0.00 "
                "(flag this — remind owner to ensure reserve requirements are met)"
            )
        if pf.get("hold_distributions"):
            lines.append("NOTE: distributions are currently on hold for this portfolio")
    else:
        lines.append("Portfolio financials: unavailable — omit portfolio-level section")

    # ── PER PROPERTY ──────────────────────────────────────────────────────────
    for prop in properties:
        lines.append(f"\n=== {prop['address']} ===")

        # ── 2. PER-LEASE FINANCIALS ───────────────────────────────────────────
        al = prop.get("active_lease")
        fin = prop.get("financials")

        if al:
            rent_due = al["rent_amount"]
            pet_rent = al["pet_rent_amount"]
            received = fin["paid"] if fin else 0.0
            all_time_overdue = fin.get("all_time_overdue", 0.0) if fin else 0.0

            lease_line = f"Lease ends {al['end_date'] or 'unknown date'}. "
            if pet_rent > 0:
                lease_line += (
                    f"Total rent due: ${rent_due:,.2f}/mo (includes ${pet_rent:,.2f} pet rent). "
                )
            else:
                lease_line += f"Total rent due: ${rent_due:,.2f}/mo. "
            lease_line += f"Received this period: ${received:,.2f}."
            lines.append(lease_line)

            if all_time_overdue > 0:
                lines.append(
                    f"Current overdue balance: ${all_time_overdue:,.2f} "
                    "(flag this; cross-reference any active late rent pipeline process)"
                )

            if al.get("notice_date"):
                lines.append(f"Notice to vacate given: {al['notice_date']}")
            if al.get("expected_move_out"):
                lines.append(f"Expected move-out: {al['expected_move_out']}")
        else:
            lines.append(
                "No active lease — property is currently vacant. "
                "Report in leasing/vacancy section (section 6)."
            )

        ul = prop.get("upcoming_lease")
        if ul:
            tenants = ", ".join(ul["tenant_names"]) or "unknown"
            lines.append(f"Upcoming lease — tenant: {tenants}")
            if ul.get("move_in_date"):
                lines.append(f"  Move-in date: {ul['move_in_date']}")
            if ul.get("rent_amount"):
                lines.append(f"  New rent: ${ul['rent_amount']:,.2f}/mo")

        # ── 3. LEADSIMPLE PIPELINE ────────────────────────────────────────────
        pipe = prop.get("pipeline") or {}
        pipeline_items = []
        for key, label in [
            ("applications", "Application in progress"),
            ("move_ins", "Move-in in progress"),
            ("renewals", "Lease renewal in progress"),
            ("late_rent", "Late rent / delinquency"),
            ("move_outs", "Move-out in progress"),
            ("issues", "Active issue"),
        ]:
            for deal in pipe.get(key, []):
                item = (
                    f"  - {label}: {deal['name']} "
                    f"(stage: {deal['stage_name']}, since: {deal['created_at']})"
                )
                if deal.get("comments"):
                    item += f"\n    notes: {str(deal['comments'])[:200]}"
                pipeline_items.append(item)

        if pipeline_items:
            lines.append(
                "Active pipeline processes (write in Vesta's voice — never mention LeadSimple):"
            )
            lines.extend(pipeline_items)

        # ── 4. MAINTENANCE ────────────────────────────────────────────────────
        melds = prop.get("melds") or []
        if melds:
            open_melds, completed_melds = _classify_melds(melds)
            total = len(melds)
            if total > 4:
                lines.append(
                    f"Maintenance — {total} work orders were active during this period "
                    f"({len(open_melds)} still open, {len(completed_melds)} completed). "
                    "Summarize the pattern briefly, then close with a pointer to PropertyMeld:"
                )
                for m in (open_melds + completed_melds)[:6]:
                    lines.append(_format_meld(m))
            else:
                lines.append(
                    f"Maintenance — {total} work order(s) active during this period:"
                )
                for m in open_melds + completed_melds:
                    lines.append(_format_meld(m))
        # If no melds: omit the maintenance section — do not say "no maintenance to report"

    # ── CLOSING INSTRUCTION ───────────────────────────────────────────────────
    property_addresses = [p["address"] for p in properties]
    addr_list = ", ".join(property_addresses)
    lines.append(
        f"\nWrite the owner note for: {addr_list}. "
        f"Follow all formatting rules: start with the property address on its own line, "
        f"bullet points throughout, no bold, no headers, no preamble. "
        f"Present data in order: portfolio funds → lease details → pipeline updates → "
        f"maintenance → vacancy/leasing. Omit any section with no relevant data. "
        f"Use only exact figures — do not round, estimate, or infer any numbers. "
        f"Do not mention vendor names. "
        f"Target approximately {word_limit} words."
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
    portfolio_name: str = None,
    property_id: int = None,
    dry_run: bool = False,
    start_date: date = None,
    end_date: date = None,
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

    if dry_run:
        print("\n--- SYSTEM PROMPT (for verification) ---")
        print(SYSTEM_PROMPT)
        print("--- END SYSTEM PROMPT ---\n")

    if start_date and end_date:
        month_start = start_date
        month_end = end_date + timedelta(days=1)  # exclusive upper bound
    else:
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
        portfolios_qs = owner.portfolios.filter(is_active=True)
        if portfolio_name:
            portfolios_qs = portfolios_qs.filter(name__iexact=portfolio_name)
        for portfolio in portfolios_qs:
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

                # Fetch RentVine owner statement for this portfolio
                statement_data = {}
                try:
                    statement_data = rv_source.get_portfolio_statement(
                        portfolio.rentvine_id
                    )
                except Exception:
                    logger.warning(
                        "Failed to fetch statement for portfolio %s",
                        portfolio.pk,
                        exc_info=True,
                    )

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
                        status="pending",
                        report_data=payload,
                        generated_note=note,
                        statement_period=statement_data.get("statement_period", ""),
                        beginning_balance=statement_data.get("beginning_balance", 0),
                        total_income=statement_data.get("total_income", 0),
                        total_expenses=statement_data.get("total_expenses", 0),
                        total_adjustments=statement_data.get("total_adjustments", 0),
                        ending_balance=statement_data.get("ending_balance", 0),
                        total_distribution=statement_data.get("total_distribution", 0),
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
