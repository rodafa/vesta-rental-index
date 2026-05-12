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
    "by the property owner. Be concise — owners want signal, not noise.\n\n"

    "Formatting rules — follow these exactly:\n"
    "- Use bullet points throughout\n"
    "- Nest sub-bullets where needed for clarity\n"
    "- No bold, no markdown headers, no dividing lines, no preamble\n"
    "- Do not start with \"For the period...\" or any greeting\n"
    "- Do not mention cancelled work orders\n"
    "- Do not include vendor names — refer to any outside vendor as "
    "\"one of our trusted vendors\"\n"
    "- When full detail exists beyond what belongs in the note, close with "
    "a pointer to PropertyMeld or the RentVine owner portal\n\n"

    "Structure — use this three-tier format:\n\n"

    "TIER 1 — Portfolio summary (always included):\n"
    "- Total billed, total collected, and outstanding balance if any\n"
    "- Reserve status only if the balance is below minimum\n"
    "- One sentence framing the month overall\n\n"

    "MULTI-UNIT PROPERTIES:\n"
    "When a property has multiple units, always identify the specific unit "
    "(e.g. \"Unit A\", \"Unit B\") when reporting financials, lease details, "
    "or maintenance. Never report these at the property level when unit-level "
    "data is available. Group information under each unit label within the "
    "property section. Pipeline processes (applications, move-ins, etc.) "
    "apply to the property as a whole — no unit attribution needed.\n\n"

    "TIER 2 — Property details (only when something is notable):\n"
    "Only include a section for a property if at least one of these is true:\n"
    "- Outstanding balance > $0 (billed but not yet collected)\n"
    "- Lease end date is within 90 days of the report period end date\n"
    "- An active pipeline process exists (application, move-in, renewal, "
    "late rent, move-out, or issue)\n"
    "- A maintenance item that is high priority, unresolved, or where a vendor "
    "was dispatched for a significant repair\n"
    "Do NOT include a property section if rent was collected in full, there is "
    "no overdue balance, no expiring lease, and no significant maintenance.\n"
    "Omit routine items entirely: quarterly walkthroughs, annual inspections, "
    "pending scheduling coordination — unless overdue or flagged.\n\n"

    "TIER 3 — Closing line (always included):\n"
    "If any properties have nothing notable to report:\n"
    "- 4 or fewer quiet properties: name them individually: "
    "\"[Property A], [Property B], and [Property C] are current on rent with "
    "no open maintenance items requiring your attention.\"\n"
    "- More than 4 quiet properties: summarize as \"The remaining [X] properties "
    "are current on rent with no open items requiring your attention.\"\n\n"

    "Filtering rules — apply these strictly:\n\n"

    "LEASE END DATE RULE:\n"
    "Only mention a lease end date if it falls within 90 days of the report "
    "period end date. Never show lease end dates more than 90 days out — "
    "they are not actionable.\n\n"

    "MAINTENANCE RULE:\n"
    "Only mention maintenance if:\n"
    "- The work order is high priority\n"
    "- A vendor was dispatched for a non-routine repair\n"
    "- The item is unresolved and has been open more than 14 days\n"
    "Never mention: quarterly preventative maintenance, annual inspections, "
    "pending scheduling coordination — unless overdue.\n\n"

    "FINANCIAL RULE PER PROPERTY:\n"
    "Only show the billed/collected/outstanding breakdown for a property if "
    "the outstanding amount is > $0. If a property collected in full, do not "
    "show its financials — it is already captured in the portfolio total.\n\n"

    "OUTSTANDING / LATE RENT TONE:\n"
    "When reporting outstanding balances or late rent, be direct but "
    "solution-focused. State the balance, state what action is underway, "
    "do not editorialize. Never use language like \"significant concern\" "
    "or \"unfortunately\" — just facts and next steps.\n\n"

    "PENDING / PROCESSING FUNDS:\n"
    "If any portion of collected rent is still processing or pending "
    "clearance, state the amount received, the amount clearing, and "
    "expected distribution timing. Never report pending funds as "
    "collected.\n\n"

    "TENANT NOTES:\n"
    "If recent tenant notes are provided (last 45 days), summarize any "
    "relevant context that adds value for the owner. Do not quote verbatim — "
    "weave key points into the narrative. Only include if the note contains "
    "actionable or notable information.\n\n"

    "PIPELINE PROCESSES:\n"
    "Write in Vesta's voice — never mention LeadSimple by name. Only include "
    "active records.\n\n"

    "Tone: warm, direct, and transparent. Every problem is paired with what is "
    "being done about it. Owners are investors — be clear and do not bury "
    "important information.\n\n"

    "Never self-correct, revise, or show reasoning mid-note. If data appears "
    "contradictory, use the figure from the most authoritative source "
    "(RentVine financial data takes precedence) without any commentary or "
    "correction language."
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


def _serialize_lease(lease_obj) -> dict:
    """Convert a Lease model instance into a serializable dict."""
    return {
        "rentvine_id": lease_obj.rentvine_id,
        "tenant_names": [t.name for t in lease_obj.tenants.all()],
        "rent_amount": float(lease_obj.rent_amount or 0),
        "pet_rent_amount": float(lease_obj.pet_rent_amount or 0),
        "start_date": lease_obj.start_date.isoformat() if lease_obj.start_date else None,
        "end_date": lease_obj.end_date.isoformat() if lease_obj.end_date else None,
        "notice_date": lease_obj.notice_date.isoformat() if lease_obj.notice_date else None,
        "expected_move_out": (
            lease_obj.expected_move_out_date.isoformat()
            if lease_obj.expected_move_out_date else None
        ),
        "move_out_status": lease_obj.move_out_status,
    }


def _serialize_upcoming_lease(lease_obj) -> dict:
    """Convert an upcoming Lease model instance into a serializable dict."""
    return {
        "rentvine_id": lease_obj.rentvine_id,
        "tenant_names": [t.name for t in lease_obj.tenants.all()],
        "rent_amount": float(lease_obj.rent_amount or 0),
        "move_in_date": lease_obj.move_in_date.isoformat() if lease_obj.move_in_date else None,
        "start_date": lease_obj.start_date.isoformat() if lease_obj.start_date else None,
    }


def _collect_single_unit_data(property_obj, owner, month_start, month_end, all_deals: list) -> dict:
    """Collect data for a single-unit property (original logic)."""
    data = {
        "address": property_obj.address_line_1,
        "is_multi_unit": False,
        "active_lease": None,
        "upcoming_lease": None,
        "financials": None,
        "melds": [],
        "pipeline": None,
        "tenant_notes": [],
    }

    active_lease = None
    try:
        active_lease = rv_source.get_active_lease(property_obj)
        if active_lease:
            data["active_lease"] = _serialize_lease(active_lease)
    except Exception:
        logger.warning("Failed to fetch active lease for property %s", property_obj.pk, exc_info=True)

    if not active_lease:
        try:
            data["vacancy_status"] = rv_source.get_vacancy_status(property_obj)
        except Exception:
            logger.warning("Failed to fetch vacancy status for property %s", property_obj.pk, exc_info=True)
            data["vacancy_status"] = "vacant"

    try:
        data["tenant_notes"] = rv_source.get_tenant_notes(active_lease)
    except Exception:
        logger.warning("Failed to fetch tenant notes for property %s", property_obj.pk, exc_info=True)

    try:
        upcoming_lease = rv_source.get_upcoming_lease(property_obj)
        if upcoming_lease:
            data["upcoming_lease"] = _serialize_upcoming_lease(upcoming_lease)
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


def _collect_multi_unit_data(property_obj, owner, month_start, month_end, all_deals: list) -> dict:
    """Collect per-unit data for a multi-unit property."""
    active_units = list(property_obj.units.filter(is_active=True))

    data = {
        "address": property_obj.address_line_1,
        "is_multi_unit": True,
        "units": [],
        "pipeline": None,
        "property_financials": None,
        "melds": [],  # unmatched melds
    }

    # Try unit-level financials; fall back to property-level
    unit_financials = None
    try:
        unit_financials = rv_source.get_financial_summary_by_unit(property_obj, month_start, month_end)
    except Exception:
        logger.warning("Failed to fetch unit-level financials for property %s", property_obj.pk, exc_info=True)

    if unit_financials is None:
        try:
            data["property_financials"] = rv_source.get_financial_summary(property_obj, month_start, month_end)
        except Exception:
            logger.warning("Failed to fetch property financials for property %s", property_obj.pk, exc_info=True)

    # Get all melds and partition by unit
    all_melds = []
    try:
        all_melds = pm_source.get_melds_for_period(property_obj, month_start, month_end)
    except Exception:
        logger.warning("Failed to fetch melds for property %s", property_obj.pk, exc_info=True)

    melds_by_unit = pm_source.match_melds_to_units(all_melds, active_units)
    data["melds"] = melds_by_unit.get(None, [])  # unmatched

    # Pipeline stays property-level
    try:
        data["pipeline"] = ls_source.get_property_pipeline_context(property_obj, all_deals)
    except Exception:
        logger.warning("Failed to fetch pipeline context for property %s", property_obj.pk, exc_info=True)

    # Per-unit data
    for unit in active_units:
        unit_data = {
            "unit_name": unit.display_address,
            "unit_rentvine_id": unit.rentvine_id,
            "active_lease": None,
            "upcoming_lease": None,
            "financials": None,
            "melds": melds_by_unit.get(unit.pk, []),
            "tenant_notes": [],
            "vacancy_status": None,
        }

        active_lease = None
        try:
            active_lease = rv_source.get_active_lease_for_unit(unit)
            if active_lease:
                unit_data["active_lease"] = _serialize_lease(active_lease)
        except Exception:
            logger.warning("Failed to fetch active lease for unit %s", unit.pk, exc_info=True)

        if not active_lease:
            try:
                unit_data["vacancy_status"] = rv_source.get_vacancy_status_for_unit(unit)
            except Exception:
                logger.warning("Failed to fetch vacancy status for unit %s", unit.pk, exc_info=True)
                unit_data["vacancy_status"] = "vacant"

        try:
            unit_data["tenant_notes"] = rv_source.get_tenant_notes(active_lease)
        except Exception:
            logger.warning("Failed to fetch tenant notes for unit %s", unit.pk, exc_info=True)

        try:
            upcoming_lease = rv_source.get_upcoming_lease_for_unit(unit)
            if upcoming_lease:
                unit_data["upcoming_lease"] = _serialize_upcoming_lease(upcoming_lease)
        except Exception:
            logger.warning("Failed to fetch upcoming lease for unit %s", unit.pk, exc_info=True)

        # Assign unit-level financials from partitioned data
        if unit_financials and unit.rentvine_id:
            unit_data["financials"] = unit_financials.get(unit.rentvine_id)

        data["units"].append(unit_data)

    return data


def collect_property_data(property_obj, owner, month_start, month_end, all_deals: list) -> dict:
    """
    Call all data sources for a property. Dispatches to multi-unit or single-unit
    collection based on property.is_multi_unit and number of active units.
    """
    if property_obj.is_multi_unit:
        active_unit_count = property_obj.units.filter(is_active=True).count()
        if active_unit_count > 1:
            return _collect_multi_unit_data(property_obj, owner, month_start, month_end, all_deals)

    return _collect_single_unit_data(property_obj, owner, month_start, month_end, all_deals)


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

    # Fetch accrual-based statement to get total_income (billed) vs total_collected (cash)
    statement_data = {}
    try:
        statement_data = rv_source.get_portfolio_statement(portfolio.rentvine_id)
    except Exception:
        logger.warning(
            "Failed to fetch statement for portfolio %s", portfolio.pk, exc_info=True
        )

    if portfolio_financials is not None:
        portfolio_financials["total_income"] = statement_data.get("total_income", 0.0)
        collected = portfolio_financials.get("total_collected", 0.0)
        income = portfolio_financials["total_income"]
        portfolio_financials["outstanding"] = max(income - collected, 0.0)
        portfolio_financials["_statement"] = statement_data

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


def _has_property_data(prop: dict) -> bool:
    """Check if a single property dict has any reportable data."""
    if prop.get("is_multi_unit"):
        # Multi-unit: check if any unit has data
        for unit in prop.get("units", []):
            if (
                unit.get("active_lease")
                or unit.get("upcoming_lease")
                or (unit.get("financials") or {}).get("has_data")
                or unit.get("melds")
            ):
                return True
        # Also check property-level pipeline and unmatched melds
        if prop.get("melds"):
            return True
        if any(
            (prop.get("pipeline") or {}).get(k)
            for k in ("applications", "move_ins", "renewals", "late_rent", "move_outs", "issues", "other")
        ):
            return True
        if (prop.get("property_financials") or {}).get("has_data"):
            return True
        return False
    else:
        return bool(
            prop.get("active_lease")
            or prop.get("upcoming_lease")
            or (prop.get("financials") or {}).get("has_data")
            or prop.get("melds")
            or any(
                (prop.get("pipeline") or {}).get(k)
                for k in ("applications", "move_ins", "renewals", "late_rent", "move_outs", "issues", "other")
            )
        )


def has_sufficient_data(payload: dict) -> bool:
    """Return False only if there is truly nothing to write about across all properties."""
    return any(_has_property_data(prop) for prop in payload["properties"])


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


def _format_lease_financials(al: dict, fin: dict, lines: list):
    """Format lease and financial data lines (shared between single/multi-unit)."""
    rent_due = al["rent_amount"]
    pet_rent = al["pet_rent_amount"]
    total_income = fin["total_income"] if fin else 0.0
    total_collected = fin["total_collected"] if fin else 0.0
    prop_outstanding = fin.get("outstanding", 0.0) if fin else 0.0
    all_time_overdue = fin.get("all_time_overdue", 0.0) if fin else 0.0

    lease_line = f"Lease ends {al['end_date'] or 'unknown date'}. "
    if pet_rent > 0:
        lease_line += (
            f"Total rent due: ${rent_due:,.2f}/mo (includes ${pet_rent:,.2f} pet rent). "
        )
    else:
        lease_line += f"Total rent due: ${rent_due:,.2f}/mo. "
    lease_line += f"Total billed: ${total_income:,.2f}. Collected: ${total_collected:,.2f}."
    lines.append(lease_line)

    if prop_outstanding > 0:
        lines.append(
            f"Outstanding this period: ${prop_outstanding:,.2f} "
            "(flag — billed but not yet collected)"
        )

    if all_time_overdue > 0:
        lines.append(
            f"Current overdue balance: ${all_time_overdue:,.2f} "
            "(flag this; cross-reference any active late rent pipeline process)"
        )

    if al.get("notice_date"):
        lines.append(f"Notice to vacate given: {al['notice_date']}")
    if al.get("expected_move_out"):
        lines.append(f"Expected move-out: {al['expected_move_out']}")


def _format_tenant_notes(tenant_notes: list, lines: list):
    """Format tenant notes lines."""
    if tenant_notes:
        lines.append(
            "Recent tenant notes from the management portal (last 45 days) — "
            "summarize relevant context, do not quote verbatim:"
        )
        for tn in tenant_notes:
            lines.append(f"  - [{tn['created_at']}] {tn['note_text']}")


def _format_vacancy(vacancy_status: str, lines: list):
    """Format vacancy status lines."""
    if vacancy_status == "active":
        lines.append(
            "No active lease — currently vacant and actively being marketed. "
            "Report in leasing/vacancy section (section 6)."
        )
    else:
        lines.append(
            "No active lease — currently vacant. "
            "No active marketing at this time."
        )


def _format_upcoming_lease(ul: dict, lines: list):
    """Format upcoming lease lines."""
    if ul:
        tenants = ", ".join(ul["tenant_names"]) or "unknown"
        lines.append(f"Upcoming lease — tenant: {tenants}")
        if ul.get("move_in_date"):
            lines.append(f"  Move-in date: {ul['move_in_date']}")
        if ul.get("rent_amount"):
            lines.append(f"  New rent: ${ul['rent_amount']:,.2f}/mo")


def _format_pipeline(pipe: dict, lines: list):
    """Format pipeline process lines."""
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


def _format_melds(melds: list, lines: list):
    """Format maintenance/meld lines."""
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


def _format_single_unit_property(prop: dict, lines: list):
    """Format a single-unit property section in the prompt."""
    lines.append(f"\n=== {prop['address']} ===")

    al = prop.get("active_lease")
    fin = prop.get("financials")

    if al:
        _format_lease_financials(al, fin, lines)
        _format_tenant_notes(prop.get("tenant_notes") or [], lines)
    else:
        _format_vacancy(prop.get("vacancy_status", "vacant"), lines)

    _format_upcoming_lease(prop.get("upcoming_lease"), lines)
    _format_pipeline(prop.get("pipeline") or {}, lines)
    _format_melds(prop.get("melds") or [], lines)


def _format_multi_unit_property(prop: dict, lines: list):
    """Format a multi-unit property section in the prompt."""
    lines.append(f"\n=== {prop['address']} (multi-unit) ===")

    # Property-level financials fallback
    pf = prop.get("property_financials")
    if pf and pf.get("has_data"):
        lines.append(
            f"Property-level financials (unit-level breakdown unavailable): "
            f"Total billed: ${pf['total_income']:,.2f}. "
            f"Collected: ${pf['total_collected']:,.2f}."
        )
        if pf.get("outstanding", 0) > 0:
            lines.append(
                f"Outstanding this period: ${pf['outstanding']:,.2f} "
                "(flag — billed but not yet collected)"
            )
        if pf.get("all_time_overdue", 0) > 0:
            lines.append(
                f"Current overdue balance: ${pf['all_time_overdue']:,.2f}"
            )

    # Per-unit sections
    for unit in prop.get("units", []):
        # Extract unit label from display_address (e.g. "11 Chestnut - Unit A" -> "Unit A")
        unit_name = unit["unit_name"]
        # Use just the suffix if the address contains the property address
        prop_addr = prop["address"]
        if unit_name.startswith(prop_addr) and " - " in unit_name:
            unit_label = unit_name.split(" - ", 1)[1]
        else:
            unit_label = unit_name
        lines.append(f"\n--- {unit_label} ---")

        al = unit.get("active_lease")
        fin = unit.get("financials")

        if al:
            _format_lease_financials(al, fin, lines)
            _format_tenant_notes(unit.get("tenant_notes") or [], lines)
        else:
            _format_vacancy(unit.get("vacancy_status") or "vacant", lines)

        _format_upcoming_lease(unit.get("upcoming_lease"), lines)
        _format_melds(unit.get("melds") or [], lines)

    # Unmatched melds (property-level)
    unmatched_melds = prop.get("melds") or []
    if unmatched_melds:
        lines.append("\nMaintenance (could not assign to a specific unit):")
        for m in unmatched_melds:
            lines.append(_format_meld(m))

    # Pipeline is property-level
    _format_pipeline(prop.get("pipeline") or {}, lines)


def build_prompt(payload: dict) -> str:
    """Build the user-turn message from the collected portfolio payload."""
    properties = payload["properties"]
    month_start_str = payload["month_start"]
    month_end_str = payload["month_end_display"]

    ms = date.fromisoformat(month_start_str)
    me = date.fromisoformat(month_end_str)
    period_start = f"{ms.strftime('%B')} {ms.day}, {ms.year}"
    period_end = f"{me.strftime('%B')} {me.day}, {me.year}"

    word_limit = min(250 + (len(properties) - 1) * 100, 600)

    lines = [
        f"Reporting period: {period_start} through {period_end}",
        f"Owner: {payload['owner_name']}",
        f"Portfolio: {payload['portfolio_name']}",
    ]

    # ── 1. PORTFOLIO-LEVEL FINANCIALS ─────────────────────────────────────────
    pf = payload.get("portfolio_financials")
    if pf:
        lines.append(f"Portfolio total income this period: ${pf['total_income']:,.2f}")
        lines.append(f"Portfolio total collected this period: ${pf['total_collected']:,.2f}")
        outstanding = pf.get("outstanding", 0.0)
        if outstanding > 0:
            lines.append(
                f"Outstanding balance (billed but not yet collected): ${outstanding:,.2f} "
                "— flag this for the owner"
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
        if prop.get("is_multi_unit"):
            _format_multi_unit_property(prop, lines)
        else:
            _format_single_unit_property(prop, lines)

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


def _upsert_report(owner_id: str, portfolio_name: str, report_month: date, **fields) -> OwnerReportLog:
    """
    Create or update an OwnerReportLog entry for the given owner+portfolio+month.

    - If an existing row has status pending/approved/failed/skipped: overwrite it.
    - If the only existing row has status 'sent': create a new row (re-send scenario).
    - If no row exists: create a new one.
    """
    existing = (
        OwnerReportLog.objects
        .filter(
            owner_id=owner_id,
            portfolio_name=portfolio_name,
            report_month=report_month,
        )
        .exclude(status="sent")
        .order_by("-created_at")
        .first()
    )

    if existing:
        for k, v in fields.items():
            setattr(existing, k, v)
        existing.save(update_fields=list(fields.keys()))
        return existing

    return OwnerReportLog.objects.create(
        owner_id=owner_id,
        portfolio_name=portfolio_name,
        report_month=report_month,
        **fields,
    )


def run_monthly_report(
    month: date,
    owner_id: str = None,
    portfolio_name: str = None,
    property_id: int = None,
    dry_run: bool = False,
    start_date: date = None,
    end_date: date = None,
    debug_data: bool = False,
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
    end_display = month_end - timedelta(days=1)
    period_label = (
        f"{month_start.strftime('%B')} {month_start.day} \u2013 "
        f"{end_display.strftime('%B')} {end_display.day}, {end_display.year}"
    )

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

                if debug_data:
                    print(f"\n{'─' * 60}")
                    print(f"[DEBUG DATA] {label}")
                    print(f"  Period: {month_start} to {month_end - timedelta(days=1)}")
                    pf_debug = payload.get("portfolio_financials") or {}
                    print(f"  Portfolio total_income:    ${pf_debug.get('total_income', 0):,.2f}")
                    print(f"  Portfolio total_collected: ${pf_debug.get('total_collected', 0):,.2f}")
                    print(f"  Portfolio outstanding:     ${pf_debug.get('outstanding', 0):,.2f}")
                    for prop in payload["properties"]:
                        addr = prop.get("address", "?")
                        if prop.get("is_multi_unit"):
                            print(f"  [{addr}] (multi-unit)")
                            pf_fallback = prop.get("property_financials") or {}
                            if pf_fallback:
                                print(f"    property-level fallback financials:")
                                print(f"      total_income:  ${pf_fallback.get('total_income', 0):,.2f}")
                                print(f"      total_collected: ${pf_fallback.get('total_collected', 0):,.2f}")
                                print(f"      outstanding:   ${pf_fallback.get('outstanding', 0):,.2f}")
                            for unit in prop.get("units", []):
                                unit_fin = unit.get("financials") or {}
                                unit_al = unit.get("active_lease") or {}
                                unit_rent = unit_al.get("rent_amount", 0) + unit_al.get("pet_rent_amount", 0)
                                print(f"    [{unit.get('unit_name', '?')}]")
                                print(f"      rent_due (lease):       ${unit_rent:,.2f}")
                                print(f"      total_income (period):  ${unit_fin.get('total_income', 0):,.2f}")
                                print(f"      total_collected (period): ${unit_fin.get('total_collected', 0):,.2f}")
                                print(f"      outstanding (period):   ${unit_fin.get('outstanding', 0):,.2f}")
                                print(f"      all_time_overdue:       ${unit_fin.get('all_time_overdue', 0):,.2f}")
                                print(f"      melds: {len(unit.get('melds', []))}")
                            unmatched = prop.get("melds", [])
                            if unmatched:
                                print(f"    unmatched melds: {len(unmatched)}")
                        else:
                            fin = prop.get("financials") or {}
                            al = prop.get("active_lease") or {}
                            rent_due = al.get("rent_amount", 0) + al.get("pet_rent_amount", 0)
                            print(f"  [{addr}]")
                            print(f"    rent_due (lease):     ${rent_due:,.2f}")
                            print(f"    total_income (period):  ${fin.get('total_income', 0):,.2f}")
                            print(f"    total_collected (period): ${fin.get('total_collected', 0):,.2f}")
                            print(f"    outstanding (period):   ${fin.get('outstanding', 0):,.2f}")
                            print(f"    all_time_overdue:       ${fin.get('all_time_overdue', 0):,.2f}")
                    print(f"{'─' * 60}")

                if not has_sufficient_data(payload):
                    print(f"\n[SKIPPED] {label} — no data found")
                    counters["skipped"] += 1
                    if not dry_run:
                        _upsert_report(
                            owner_id=str(owner.rentvine_contact_id),
                            report_month=month_start,
                            portfolio_name=portfolio.name,
                            owner_name=owner.name,
                            status="skipped",
                            report_data=payload,
                            error_message="",
                            generated_note="",
                        )
                    continue

                note = generate_note(payload)

                # Reuse statement data already fetched in collect_portfolio_data()
                pf_data = payload.get("portfolio_financials") or {}
                statement_data = pf_data.pop("_statement", {})

                print(f"\n{DIVIDER}")
                print(f"OWNER: {owner.name}")
                print(f"PORTFOLIO: {portfolio.name} ({len(properties)} propert{'ies' if len(properties) != 1 else 'y'})")
                print(f"PERIOD: {period_label}")
                print(DIVIDER)
                print(note)
                print(DIVIDER)

                counters["generated"] += 1

                if not dry_run:
                    _upsert_report(
                        owner_id=str(owner.rentvine_contact_id),
                        report_month=month_start,
                        portfolio_name=portfolio.name,
                        owner_name=owner.name,
                        status="pending",
                        report_data=payload,
                        generated_note=note,
                        error_message="",
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
                    _upsert_report(
                        owner_id=str(owner.rentvine_contact_id),
                        report_month=month_start,
                        portfolio_name=portfolio.name,
                        owner_name=owner.name,
                        status="failed",
                        error_message="Unhandled exception — see server logs",
                        generated_note="",
                    )

    return counters
