"""
Comms engine: generate and send email drafts.

generate_drafts() — loads product config, calls selector per owner, sends data
to Anthropic for narrative prose, renders template, writes EmailDraft rows.

send_draft() — delivers a single EmailDraft via SendGrid with role-gating,
safety modes (sandbox / test-email / live), and structured logging.
"""

import copy
import json
import logging
import threading
from datetime import date as _date
from importlib import import_module

import anthropic
import sendgrid
from django.conf import settings
from django.template.loader import render_to_string
from django.utils import timezone
from sendgrid.helpers.mail import Cc, Mail, SandBoxMode, MailSettings

from .models import EmailDraft, PortfolioMaintenanceNote, PortfolioMonthlyNote, VoiceGuide
from .registry import PRODUCTS

logger = logging.getLogger(__name__)

# Default voice guide texts — used to seed VoiceGuide rows on first run.

DEFAULT_VOICE_GUIDES = {
    "maintenance": """\
You write weekly maintenance email updates for property owners on behalf of \
Vesta Property Management.

Voice: Trustworthy, approachable, transparent. You are a knowledgeable property \
manager giving a clear, factual update — not a salesperson.

Four sections: Open, Scheduled, Completed, Cancelled.

Rules:
- Write in first person plural ("We resolved the issue", "Our team scheduled")
- 1-2 sentences per work order, never more
- State facts: what happened, who did it, when
- For open items: present tense, state what was reported and any vendor assigned
- For scheduled items: present tense, state the scheduled date verbatim and vendor if known
- For completed items: past tense, mention vendor and completion date
- For cancelled items: past tense, brief — just note it was cancelled
- NEVER tell the owner to approve, authorize, or take action on any work order
- No jargon, no filler, no speculation
- Do NOT include work order numbers or ticket IDs in your prose
- NEVER expose dollar figures in your prose — costs are shown structurally
- The intro paragraph should be 1-2 sentences summarizing the week's activity \
count (e.g. "This week there are 3 open work orders and 2 were completed.")
- Do not editorialize or add unnecessary reassurance\
""",
    "monthly_owner_notes": """\
You are a RENDERER, not a reporter. You receive pre-structured facts about a \
single investment portfolio and produce a polished monthly owner update on \
behalf of Vesta Property Management. You do NOT discover or infer information — \
you render what you are given.

Voice: Trustworthy, approachable, transparent. Warm and direct. A knowledgeable \
property manager giving a clear, factual operational update — not a salesperson. \
Every problem is paired with what is being done about it.

Structure — choose based on unit count:

SINGLE-UNIT PORTFOLIO:
- Intro: when the portfolio had real activity, open with the month's most \
significant actual item — not a generic energy assessment. Do NOT use \
"active month" as a stock opener. When the portfolio was genuinely quiet, a \
brief stable/quiet summary is correct.
- Category-grouped bullets (Leasing & Occupancy / Property & Turns / \
Collections / Issues). Omit empty categories entirely.
- Maintenance line (from summary_line, if provided)

MULTI-UNIT PORTFOLIO:
- Intro: same rule as single-unit — lead with the most significant actual item.
- Per-unit sections, headed by property address (add unit number when the \
property has more than one unit). Order most-serious-first.
- Quiet roll-up: "The portfolio's remaining N units had no leasing or \
operational changes this month." Use "units" not "properties."
- Portfolio-level maintenance line

Input fields per process:
- status_line: the plain-English stage framing — this is your outcome source. \
Render it faithfully.
- activity_band: "lightly worked" or "actively worked" — use as color, NEVER \
expose raw task counts or work-order counts.
- surfaced_fragments: specific "what we did" phrases — weave naturally into prose.
- address + unit_number: refer to residents as "the resident at [address]" \
(add unit number when the property has more than one unit).

Rules:
- Write in first person plural ("We completed", "Our team coordinated")
- Length matches the fact: a single clause when that's all it needs, never \
padded. Never more than two sentences for one process.
- State facts: what happened or is happening, current status, next step
- NEVER expose numeric task counts, work-order counts, or dollar figures
- NEVER reference internal system names, ticket IDs, pipeline references, \
or process IDs
- NEVER address or greet the owner by name
- NEVER reference "your other properties" or any other portfolio
- All dates in plain English format (e.g. "May 15, 2026") — never ISO format
- No jargon, no filler, no speculation, no editorializing
- No effort- or activity-language. Never describe how hard the team worked. \
Banned phrasings include "requiring close attention," "considerable effort," \
"working diligently," "working this effort closely," and "actively working" \
used as filler. State what was done or what happens next — not effort.
- One fact, one clause. Match sentence length to the fact. A small fact gets a \
short sentence; never pad a single fact into two sentences.
- No run-on sentences. Say what needs saying and stop.
- For a single-property portfolio, you will be given a street address as the \
identifier instead of a portfolio name. Refer to the property by that address \
throughout (e.g. "Everything was quiet at 123 Main Street") — never by a \
portfolio or LLC name.\
""",
}

# Backward compat alias
DEFAULT_MAINTENANCE_VOICE_GUIDE = DEFAULT_VOICE_GUIDES["maintenance"]


def _load_selector(dotted_path):
    """Import a selector function from a dotted path like 'app.module.func'."""
    module_path, func_name = dotted_path.rsplit(".", 1)
    module = import_module(module_path)
    return getattr(module, func_name)


def _get_or_create_voice_guide(product_name):
    """Load the VoiceGuide for a product, seeding the default on first creation only."""
    default_text = DEFAULT_VOICE_GUIDES.get(product_name, DEFAULT_VOICE_GUIDES["maintenance"])
    guide, created = VoiceGuide.objects.get_or_create(
        product=product_name,
        defaults={"instructions": default_text},
    )
    if created:
        logger.info(
            "comms_voice_guide_created",
            extra={"product": product_name},
        )
    return guide


def _build_meld_payload(melds, section_label):
    """Format a list of meld dicts into structured text for the AI prompt."""
    if not melds:
        return f"{section_label}: none"

    lines = [f"{section_label} ({len(melds)}):"]
    for m in melds:
        parts = [
            f"  - [{m['property_meld_id']}]",
            f"Address: {m['unit_address']}",
            f"Issue: {m['brief_description']}",
        ]
        if m.get("category"):
            parts.append(f"Category: {m['category']}")
        if m.get("assigned_vendor_name"):
            parts.append(f"Vendor: {m['assigned_vendor_name']}")
        if m.get("priority") and m["priority"] in ("HIGH", "EMERGENCY"):
            parts.append(f"Priority: {m['priority']}")
        if m.get("scheduled_date"):
            parts.append(f"Scheduled: {m['scheduled_date']}")
        if m.get("completion_date"):
            parts.append(f"Completed: {m['completion_date']}")
        if m.get("owner_approval_status") == "Requested":
            parts.append("OWNER APPROVAL REQUESTED")
        lines.append(" | ".join(parts))
    return "\n".join(lines)


def _build_work_order_payload(address_groups, section_label):
    """Format address-grouped work-order dicts into structured text for the AI prompt.

    address_groups is a list of {"property_address", "work_orders", "show_unit"} dicts
    produced by the selector's _group_by_address().
    """
    if not address_groups:
        return f"{section_label}: none"

    total = sum(len(g["work_orders"]) for g in address_groups)
    lines = [f"{section_label} ({total}):"]
    for group in address_groups:
        lines.append(f"  {group['property_address']}:")
        for wo in group["work_orders"]:
            parts = [
                f"    - [{wo['work_order_number']}]",
                f"Issue: {wo['description'][:200]}",
            ]
            if wo.get("vendor_name"):
                parts.append(f"Vendor: {wo['vendor_name']}")
            if wo.get("cost"):
                parts.append(f"Cost: ${wo['cost']}")
            if wo.get("scheduled_start_date"):
                parts.append(f"Scheduled: {wo['scheduled_start_date']}")
            if wo.get("date_closed"):
                parts.append(f"Completed: {wo['date_closed']}")
            lines.append(" | ".join(parts))
    return "\n".join(lines)


def _build_maintenance_prompt_work_orders(
    data, portfolio_name, period_start, period_end
):
    """
    Build the AI prompt for a portfolio-grain maintenance note using WorkOrder data.

    Owner-agnostic — no owner name, no cross-portfolio references.
    AI returns: {"intro": "...", "wo_summaries": {"<work_order_number>": "..."}}
    """
    open_items = _build_work_order_payload(
        data["open"], "Open"
    )
    scheduled = _build_work_order_payload(
        data["scheduled"], "Scheduled"
    )
    completed = _build_work_order_payload(
        data["completed"], "Completed this week"
    )
    cancelled = _build_work_order_payload(
        data["cancelled"], "Cancelled this week"
    )

    return (
        f'Write a maintenance update for the portfolio "{portfolio_name}".\n'
        f"Period: {period_start} to {period_end}.\n\n"
        f"{open_items}\n\n"
        f"{scheduled}\n\n"
        f"{completed}\n\n"
        f"{cancelled}\n\n"
        "Write:\n"
        "1. A brief intro (1-2 sentences summarizing counts)\n"
        "2. For each work order identified by its [ID], a concise 1-2 sentence "
        "summary. Do NOT include the ID or dollar figures in the summary text.\n\n"
        'Return valid JSON only: {"intro": "...", "wo_summaries": {"<id>": "..."}}'
    )


def _format_period_label(period_type, period_start, period_end):
    """Build a human-readable label for the reporting period."""
    if period_type == "monthly":
        return f"{period_start.strftime('%B %Y')}"
    # weekly (default)
    return (
        f"{period_start.strftime('%b %d')} – "
        f"{period_end.strftime('%b %d, %Y')}"
    )


def _call_anthropic(voice_guide_text, user_prompt):
    """
    Call the Anthropic API with a voice guide and user prompt.

    Returns parsed JSON dict from the model response.
    """
    model = getattr(settings, "ANTHROPIC_MODEL", "claude-sonnet-4-6")

    client = anthropic.Anthropic()
    response = client.messages.create(
        model=model,
        max_tokens=2000,
        temperature=0.3,
        system=f"You are a property management communications assistant.\n\n{voice_guide_text}",
        messages=[{"role": "user", "content": user_prompt}],
    )

    raw_text = response.content[0].text.strip()

    # Strip markdown fences if present
    if raw_text.startswith("```"):
        lines = raw_text.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        raw_text = "\n".join(lines).strip()

    try:
        return json.loads(raw_text)
    except json.JSONDecodeError:
        logger.error(
            "comms_anthropic_json_parse_error",
            extra={"raw_text": raw_text[:500]},
        )
        return {"intro": raw_text[:300]}


# ---------------------------------------------------------------------------
# Per-product prompt builders
# ---------------------------------------------------------------------------


CATEGORY_LABELS = {
    "renewal": "Lease Renewals",
    "move_out": "Move Outs",
    "move_in": "Move Ins",
    "rehab_to_turn": "Rehab to Turn",
    "issues": "Issues",
    "onboarding": "Onboarding",
    "late_rent": "Late Rent",
    "offboarding": "Offboarding",
    "marketing": "Property Marketing",
    "application": "Applications",
}


def _build_process_payload(processes, category_label):
    """Format a list of process dicts into structured text for the AI prompt."""
    if not processes:
        return ""
    lines = [f"{category_label} ({len(processes)}):"]
    for p in processes:
        parts = [f"  - [{p['process_id']}]", f"Name: {p['name']}"]
        if p.get("address"):
            parts.append(f"Address: {p['address']}")
        if p.get("unit_number"):
            parts.append(f"Unit: {p['unit_number']}")
        if p.get("stage_name"):
            parts.append(f"Stage: {p['stage_name']}")
        if p.get("stage_status"):
            parts.append(f"Status: {p['stage_status']}")
        lines.append(" | ".join(parts))
    return "\n".join(lines)


def _build_monthly_prompt(data, owner_name, period_start, period_end):
    """
    Build the AI prompt for a monthly owner notes email.

    Accepts either the old shape (processes_by_category at top level)
    or the new shape (portfolio_sections list).
    """
    portfolio_sections = data.get("portfolio_sections")
    if portfolio_sections:
        return _build_monthly_prompt_portfolio(
            portfolio_sections, owner_name, period_start, period_end
        )

    # Legacy path: flat processes_by_category (maintenance weekly still uses this)
    sections = []
    by_category = data.get("processes_by_category", {})
    for cat_key, procs in by_category.items():
        label = CATEGORY_LABELS.get(cat_key, cat_key.replace("_", " ").title())
        payload = _build_process_payload(procs, label)
        if payload:
            sections.append(payload)

    processes_text = "\n\n".join(sections) if sections else "No processes to report."

    return (
        f"Write a monthly owner update email for {owner_name}.\n"
        f"Period: {period_start.strftime('%B %Y')}.\n\n"
        f"{processes_text}\n\n"
        "Write:\n"
        "1. A brief intro (2-3 sentences summarizing the month's activity by "
        "category count)\n"
        "2. For each process identified by its [ID], a concise 1-2 sentence "
        "summary. Do NOT include the ID in the summary text.\n\n"
        'Return valid JSON only: {"intro": "...", "process_summaries": {"<id>": "..."}}'
    )


def _build_monthly_prompt_portfolio(portfolio_sections, owner_name, period_start, period_end):
    """Build the AI prompt for a portfolio-organized monthly owner notes email."""
    parts = [
        f"Write a monthly owner update email for {owner_name}.",
        f"Period: {period_start.strftime('%B %Y')}.",
        "",
    ]

    for section in portfolio_sections:
        parts.append(f"=== Portfolio: {section['portfolio_name']} ===")

        # Financials are rendered separately — NOT passed to the AI prompt.

        # Maintenance summary
        maint = section.get("maintenance", {})
        if maint.get("_has_data"):
            parts.append(
                f"Maintenance: {maint['open_count']} open, "
                f"{maint['closed_count']} closed, "
                f"{maint['canceled_count']} canceled"
            )
        else:
            parts.append("Maintenance: No activity this period.")

        # Pipeline activity
        pipeline = section.get("pipeline", {})
        by_category = pipeline.get("processes_by_category", {})
        if by_category:
            for cat_key, procs in by_category.items():
                label = CATEGORY_LABELS.get(cat_key, cat_key.replace("_", " ").title())
                payload = _build_process_payload(procs, label)
                if payload:
                    parts.append(payload)
        else:
            parts.append("Pipeline: No processes to report.")

        parts.append("")

    parts.append(
        "Write:\n"
        "1. A brief intro (2-3 sentences summarizing the month's activity "
        "across all portfolios)\n"
        "2. For each process identified by its [ID], a concise 1-2 sentence "
        "summary. Do NOT include the ID in the summary text.\n\n"
        'Return valid JSON only: {"intro": "...", "process_summaries": {"<id>": "..."}}'
    )

    return "\n".join(parts)


def _build_single_portfolio_prompt(section, period_start):
    """
    Build an OWNER-AGNOSTIC AI prompt for a single portfolio's monthly note.

    The note covers only this portfolio's operations — no owner name, no
    cross-portfolio references, no "your other properties." Output is a
    self-contained fragment headed by the portfolio name, so concatenating
    several reads cleanly.

    Passes structured facts per process: status_line, activity_band,
    surfaced_fragments, address, unit_number.
    """
    unit_count = section.get("unit_count", 1)
    structure = "MULTI-UNIT" if unit_count > 1 else "SINGLE-UNIT"

    # Single-property portfolios: use the street address as the identifier
    # so the model writes "at 123 Main St" instead of an LLC name.
    distinct_addresses = sorted({
        u["address"] for u in section.get("units", []) if u.get("address")
    })
    identifier = (
        distinct_addresses[0] if len(distinct_addresses) == 1
        else section["portfolio_name"]
    )

    parts = [
        f"Render a monthly operational summary for the portfolio "
        f'"{identifier}" ({structure}, {unit_count} unit{"s" if unit_count != 1 else ""}).',
        f"Period: {period_start.strftime('%B %Y')}.",
        "",
        "IMPORTANT RULES:",
        "- Do NOT address or greet any owner by name.",
        '- Do NOT reference "your other properties" or any other portfolio.',
        "- Write a self-contained update for THIS portfolio only.",
        '- Use first person plural ("We", "Our team").',
        "- NEVER expose numeric task counts or work-order counts.",
        "- NEVER include dollar figures.",
        "",
    ]

    # Financials are rendered separately — NOT passed to the AI prompt.

    # Maintenance summary
    maint = section.get("maintenance", {})
    summary_line = maint.get("summary_line", "")
    if summary_line:
        parts.append(f"Maintenance: {summary_line}")
    elif maint.get("_has_data"):
        parts.append("Maintenance: Some work orders this period.")
    else:
        parts.append("Maintenance: No activity this period.")

    # Pipeline activity — structured facts
    pipeline = section.get("pipeline", {})
    by_category = pipeline.get("processes_by_category", {})
    if by_category:
        parts.append("")
        parts.append("=== Process Facts ===")
        for cat_key, procs in by_category.items():
            label = CATEGORY_LABELS.get(cat_key, cat_key.replace("_", " ").title())
            parts.append(f"\n--- {label} ---")
            for p in procs:
                proc_id = p.get("process_id", "")
                address = p.get("address", "")
                unit_num = p.get("unit_number", "")
                status_line = p.get("status_line", "")
                activity_band = p.get("activity_band", "")
                fragments = p.get("surfaced_fragments", [])

                loc = address
                if unit_num:
                    loc = f"{address}, Unit {unit_num}"

                fact_parts = [f"[{proc_id}] {loc}"]
                if status_line:
                    fact_parts.append(f"  Status: {status_line}")
                if activity_band:
                    fact_parts.append(f"  Activity: {activity_band}")
                if fragments:
                    fact_parts.append(f"  Details: {'; '.join(fragments)}")

                parts.append("\n".join(fact_parts))
    else:
        parts.append("Pipeline: No processes to report.")

    parts.append("")
    parts.append(
        "Write:\n"
        "1. A brief intro — lead with the most significant actual item, not a "
        "generic energy assessment. 2-3 sentences max, fewer if the month was "
        "quiet.\n"
        "2. For each process identified by its [ID], a summary as short as the "
        "fact allows — a single clause if that's all it needs; never padded. "
        "Render the status_line faithfully. Weave surfaced_fragments "
        "naturally. Use activity_band as color but NEVER expose raw counts.\n"
        "Do NOT include the ID in the summary text.\n\n"
        'Return valid JSON only: {"intro": "...", "process_summaries": {"<id>": "..."}}'
    )

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Per-product context builders
# ---------------------------------------------------------------------------


def _build_monthly_context(data, ai_result, period_label):
    """
    Attach AI summaries to process dicts and build the monthly template context.

    Handles both the old shape (processes_by_category at top level)
    and the new shape (portfolio_sections list).
    """
    summaries = ai_result.get("process_summaries", {})

    portfolio_sections = data.get("portfolio_sections")
    if portfolio_sections:
        return _build_monthly_context_portfolio(
            data, ai_result, period_label, summaries
        )

    # Legacy path: flat processes_by_category
    by_category = data.get("processes_by_category", {})

    categories = []
    for cat_key, procs in by_category.items():
        label = CATEGORY_LABELS.get(cat_key, cat_key.replace("_", " ").title())
        for proc in procs:
            pid = str(proc.get("process_id", ""))
            proc["ai_summary"] = summaries.get(pid, "")
        categories.append({
            "key": cat_key,
            "label": label,
            "processes": procs,
            "count": len(procs),
        })

    return {
        "owner_first_name": data["owner_first_name"],
        "ai_intro": ai_result.get("intro", ""),
        "categories": categories,
        "total_count": data.get("total_count", 0),
        "period_label": period_label,
    }


def _build_monthly_context_portfolio(data, ai_result, period_label, summaries):
    """Build template context for the portfolio-organized monthly owner notes."""
    portfolio_sections = data["portfolio_sections"]

    template_sections = []
    total_count = 0

    for section in portfolio_sections:
        # Financial data
        fin = section.get("financials", {})

        # Statement period label
        stmt_period = ""
        if fin.get("period_start") and fin.get("period_end"):
            stmt_period = (
                f"{fin['period_start'].strftime('%b %d')} – "
                f"{fin['period_end'].strftime('%b %d, %Y')}"
            )

        # Maintenance summary
        maint = section.get("maintenance", {})

        # Pipeline categories with AI summaries attached
        pipeline = section.get("pipeline", {})
        by_category = pipeline.get("processes_by_category", {})
        categories = []
        for cat_key, procs in by_category.items():
            label = CATEGORY_LABELS.get(cat_key, cat_key.replace("_", " ").title())
            for proc in procs:
                pid = str(proc.get("process_id", ""))
                proc["ai_summary"] = summaries.get(pid, "")
            categories.append({
                "key": cat_key,
                "label": label,
                "processes": procs,
                "count": len(procs),
            })
            total_count += len(procs)

        # Re-key maintenance dict to avoid underscore-prefixed keys
        # (Django templates forbid accessing _has_data)
        template_maint = {
            "has_data": maint.get("_has_data", False),
            "open_count": maint.get("open_count", 0),
            "closed_count": maint.get("closed_count", 0),
            "canceled_count": maint.get("canceled_count", 0),
        }

        template_sections.append({
            "portfolio_name": section["portfolio_name"],
            "has_financials": bool(fin),
            "statement_period": stmt_period,
            "total_income": fin.get("total_income"),
            "total_expenses": fin.get("total_expenses"),
            "total_distribution": fin.get("total_distribution"),
            "ending_balance": fin.get("ending_balance"),
            "maintenance": template_maint,
            "categories": categories,
        })

    return {
        "owner_first_name": data["owner_first_name"],
        "ai_intro": ai_result.get("intro", ""),
        "portfolio_sections": template_sections,
        "total_count": total_count,
        "period_label": period_label,
    }


def _build_generated_note(ai_result, data, product_name):
    """
    Concatenate AI prose into a plain-text note for dashboard editing.

    For maintenance: intro + meld summaries.
    For monthly_owner_notes: intro + process summaries grouped by category.
    """
    parts = []
    intro = ai_result.get("intro", "")
    if intro:
        parts.append(intro)

    if product_name == "monthly_owner_notes":
        summaries = ai_result.get("process_summaries", {})
        by_category = data.get("processes_by_category", {})
        for cat_key, procs in by_category.items():
            label = CATEGORY_LABELS.get(cat_key, cat_key.replace("_", " ").title())
            cat_lines = []
            for proc in procs:
                pid = str(proc.get("process_id", ""))
                summary = summaries.get(pid, "")
                if summary:
                    cat_lines.append(summary)
            if cat_lines:
                parts.append(f"{label}:\n" + "\n".join(f"- {s}" for s in cat_lines))
    else:
        # Maintenance or generic — meld summaries
        summaries = ai_result.get("meld_summaries", {})
        if summaries:
            parts.append(
                "\n".join(f"- {s}" for s in summaries.values() if s)
            )

    return "\n\n".join(parts)


def _fmt_money(value):
    """Format a numeric value as $X,XXX.XX."""
    try:
        return f"${float(value):,.2f}"
    except (TypeError, ValueError):
        return "$0.00"


def _render_financials_html(context):
    """
    Render the financials_html fragment for the SendGrid dynamic template.

    One styled block per portfolio with a financial summary table.
    Uses inline CSS matching the Vesta brand.
    """
    from django.utils.html import escape

    sections = context.get("portfolio_sections", [])
    if not sections:
        return ""

    parts = []
    for section in sections:
        name = escape(section["portfolio_name"])

        parts.append(
            f'<h2 style="margin:24px 0 8px; font-family:Helvetica,Arial,sans-serif; '
            f'font-size:20px; font-weight:700; color:#1E3D58; '
            f'border-bottom:3px solid #1E3D58; padding-bottom:8px;">'
            f'{name}</h2>'
        )

        if section.get("has_financials"):
            stmt_period = section.get("statement_period", "")

            parts.append(
                '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
                'style="background-color:#EFF5F9; border-radius:6px; margin:8px 0 16px;">'
                '<tr><td style="padding:16px;">'
            )

            if stmt_period:
                parts.append(
                    f'<p style="margin:0 0 8px; font-family:Helvetica,Arial,sans-serif; '
                    f'font-size:12px; color:#6EA5CD; text-transform:uppercase; '
                    f'letter-spacing:0.5px;">Statement Period: {escape(stmt_period)}</p>'
                )

            rows = [
                ("Income", _fmt_money(section.get("total_income")), "#1E3D58", False),
                ("Expenses", _fmt_money(section.get("total_expenses")), "#1E3D58", False),
                ("Distribution", _fmt_money(section.get("total_distribution")), "#059669", False),
                ("Ending Balance", _fmt_money(section.get("ending_balance")), "#1E3D58", True),
            ]

            parts.append(
                '<table role="presentation" width="100%" cellpadding="0" cellspacing="0">'
            )
            for label, value, color, is_total in rows:
                border = "border-top:1px solid #d1d5db; " if is_total else ""
                weight = "700" if is_total else "600"
                parts.append(
                    f'<tr>'
                    f'<td style="padding:4px 0; {border}'
                    f'font-family:Helvetica,Arial,sans-serif; font-size:14px; '
                    f'color:#555555;">{label}</td>'
                    f'<td style="padding:4px 0; {border}'
                    f'font-family:Helvetica,Arial,sans-serif; font-size:14px; '
                    f'font-weight:{weight}; color:{color}; text-align:right;">'
                    f'{value}</td>'
                    f'</tr>'
                )
            parts.append('</table>')
            parts.append('</td></tr></table>')
        else:
            parts.append(
                '<p style="margin:8px 0 16px; font-family:Georgia,serif; '
                'font-size:14px; color:#9ca3af; font-style:italic;">'
                'No financial statement available yet.</p>'
            )

    return "\n".join(parts)


def _render_notes_html(context):
    """
    Render the notes_html fragment for the SendGrid dynamic template.

    AI intro + portfolio-organized content with unit-grain structure:
    - Single unit: category-grouped bullets + maintenance line
    - Multi-unit: per-unit sections + quiet roll-up + maintenance line

    Inline CSS matching the Vesta brand.
    """
    from django.utils.html import escape

    parts = []

    # AI intro
    ai_intro = context.get("ai_intro", "")
    if ai_intro:
        parts.append(
            f'<p style="margin:0 0 16px; font-family:Georgia,serif; '
            f'font-size:15px; line-height:1.6; color:#555555;">'
            f'{escape(ai_intro)}</p>'
        )

    sections = context.get("portfolio_sections", [])
    for section in sections:
        name = escape(section["portfolio_name"])
        unit_count = section.get("unit_count", 1)
        is_multi_unit = unit_count > 1

        parts.append(
            f'<h2 style="margin:24px 0 8px; font-family:Helvetica,Arial,sans-serif; '
            f'font-size:20px; font-weight:700; color:#1E3D58; '
            f'border-bottom:3px solid #1E3D58; padding-bottom:8px;">'
            f'{name}</h2>'
        )

        categories = section.get("categories", [])

        if is_multi_unit and categories:
            # Multi-unit: group processes by address+unit, render per-unit
            units_with_activity = set()
            for category in categories:
                for process in category.get("processes", []):
                    addr = process.get("address", "")
                    unit_num = process.get("unit_number", "")
                    units_with_activity.add((addr, unit_num))

            # Render per-unit sections
            for addr, unit_num in sorted(units_with_activity):
                header = escape(addr)
                if unit_num:
                    header += f", Unit {escape(str(unit_num))}"
                parts.append(
                    f'<h3 style="margin:16px 0 8px; font-family:Helvetica,Arial,sans-serif; '
                    f'font-size:16px; font-weight:600; color:#1E3D58; '
                    f'border-bottom:2px solid #6EA5CD; padding-bottom:6px;">'
                    f'{header}</h3>'
                )

                for category in categories:
                    for process in category.get("processes", []):
                        p_addr = process.get("address", "")
                        p_unit = process.get("unit_number", "")
                        if (p_addr, p_unit) != (addr, unit_num):
                            continue
                        _render_process_block(parts, process, escape)

            # Quiet roll-up
            active_unit_count = len(units_with_activity)
            remaining = unit_count - active_unit_count
            if remaining > 0:
                parts.append(
                    f'<p style="margin:12px 0; font-family:Georgia,serif; '
                    f'font-size:14px; line-height:1.4; color:#9ca3af; font-style:italic;">'
                    f"The portfolio's remaining {remaining} "
                    f'unit{"s" if remaining != 1 else ""} had no leasing or '
                    f'operational changes this month.</p>'
                )

        elif categories:
            # Single-unit: category-grouped bullets
            for category in categories:
                label = escape(category["label"])
                parts.append(
                    f'<h3 style="margin:16px 0 8px; font-family:Helvetica,Arial,sans-serif; '
                    f'font-size:16px; font-weight:600; color:#1E3D58; '
                    f'border-bottom:2px solid #6EA5CD; padding-bottom:6px;">'
                    f'{label}</h3>'
                )
                for process in category.get("processes", []):
                    _render_process_block(parts, process, escape)

        # Maintenance line (portfolio-level)
        maint = section.get("maintenance", {})
        if maint.get("has_data"):
            parts.append(
                f'<div style="margin:12px 0; padding:8px 12px; '
                f'background-color:#fef3c7; border-radius:6px;">'
                f'<p style="margin:0; font-family:Helvetica,Arial,sans-serif; '
                f'font-size:13px; color:#92400e;">'
                f'Maintenance: {maint.get("open_count", 0)} open • '
                f'{maint.get("closed_count", 0)} closed • '
                f'{maint.get("canceled_count", 0)} canceled'
                f'</p></div>'
            )

        # No activity fallback
        if not maint.get("has_data") and not categories:
            parts.append(
                '<p style="margin:8px 0 16px; font-family:Georgia,serif; '
                'font-size:14px; color:#9ca3af; font-style:italic;">'
                'No operational activity to report for this portfolio.</p>'
            )

    return "\n".join(parts)


def _render_process_block(parts, process, escape):
    """Render a single process block with AI summary."""
    parts.append(
        '<div style="margin:4px 0 8px; padding:12px 16px; '
        'background-color:#fafafa; border-radius:6px; '
        'border-left:4px solid #6EA5CD;">'
    )

    if process.get("ai_summary"):
        summary = escape(str(process["ai_summary"]))
        parts.append(
            f'<p style="margin:0; font-family:Georgia,serif; '
            f'font-size:14px; line-height:1.4; color:#555555;">'
            f'{summary}</p>'
        )
    else:
        proc_name = escape(str(process.get("name", "")))
        if proc_name:
            parts.append(
                f'<p style="margin:0; font-family:Helvetica,Arial,sans-serif; '
                f'font-size:14px; color:#555555;">{proc_name}</p>'
            )

    parts.append('</div>')


def _render_notes_html_from_text(text):
    """
    Render plain-text generated_note as inline-styled HTML for the notes_html
    fragment. Used when an admin edits the note text in the dashboard.
    """
    from django.utils.html import escape

    if not text or not text.strip():
        return ""

    paragraphs = text.strip().split("\n\n")
    parts = []
    for p in paragraphs:
        escaped = escape(p).replace("\n", "<br>")
        parts.append(
            f'<p style="margin:0 0 12px; font-family:Georgia,serif; '
            f'font-size:15px; line-height:1.6; color:#555555;">{escaped}</p>'
        )
    return "\n".join(parts)


def _get_latest_statement(portfolio):
    """
    Fetch the latest posted PortfolioStatement for a portfolio.

    Returns a dict with financial fields, or {} if none exists.
    """
    from accounting.models import PortfolioStatement

    stmt = (
        PortfolioStatement.objects.filter(portfolio=portfolio, status="2")
        .order_by("-period_end")
        .first()
    )
    if stmt is None:
        return {}

    return {
        "period_start": stmt.period_start,
        "period_end": stmt.period_end,
        "beginning_balance": stmt.beginning_balance,
        "total_income": stmt.total_income,
        "total_expenses": stmt.total_expenses,
        "total_adjustments": stmt.total_adjustments,
        "ending_balance": stmt.ending_balance,
        "total_distribution": stmt.total_distribution,
    }


def build_portfolio_section(
    portfolio, period_start, period_end, *,
    prefetched_processes=None, prefetched_tasks_index=None,
):
    """
    Pure function. Returns structured data for one portfolio's section
    in the monthly owner email. Cacheable by portfolio.pk.

    When prefetched_processes/prefetched_tasks_index are provided, passes
    them through to the pipeline selector (fetch-once pattern).
    """
    from maintenance.selectors import get_portfolio_work_order_summary
    from integrations.leadsimple.selectors import get_portfolio_pipeline_data
    from core.models import Unit

    # Unit roster for single-unit vs multi-unit rendering
    units_qs = Unit.objects.filter(
        property__portfolio=portfolio, is_active=True,
    ).select_related("property")

    units_list = []
    for unit in units_qs:
        # Detect multi-unit property: >1 active unit on the same property
        sibling_count = Unit.objects.filter(
            property=unit.property, is_active=True,
        ).count()
        units_list.append({
            "address": unit.property.address_line_1,
            "unit_number": unit.unit_number or "",
            "property_id": unit.property_id,
            "is_multi_unit": sibling_count > 1,
        })

    return {
        "portfolio_name": portfolio.name,
        "financials": _get_latest_statement(portfolio),
        "maintenance": get_portfolio_work_order_summary(
            portfolio, period_start, period_end
        ),
        "pipeline": get_portfolio_pipeline_data(
            portfolio, period_start, period_end,
            prefetched_processes=prefetched_processes,
            prefetched_tasks_index=prefetched_tasks_index,
        ),
        "unit_count": len(units_list),
        "units": units_list,
    }


def generate_drafts(
    product_name, owner_queryset, period_start, period_end,
    period_type="weekly", dry_run=False,
):
    """
    Generate email drafts for a product and set of owners.

    1. Load registry config and voice guide.
    2. Call the selector per owner.
    3. Call Anthropic for narrative prose.
    4. Render the HTML template.
    5. Write EmailDraft rows (unless dry_run=True).

    If dry_run is True, the selector and Anthropic calls run normally but
    no EmailDraft rows are written. Useful for testing pipeline output.

    Returns dict: {generated, skipped, degraded, errors, error_details}.
    """
    if product_name not in PRODUCTS:
        raise ValueError(f"Unknown product: {product_name}")

    config = PRODUCTS[product_name]
    selector = _load_selector(config["selector"])
    prompt_builder = _load_selector(config["prompt_builder"])
    context_builder = _load_selector(config["context_builder"])
    voice_guide = _get_or_create_voice_guide(config["voice_guide_product"])
    template_name = config["template"]
    subject_template = config.get("subject_template")

    generated = 0
    skipped = 0
    degraded = 0
    errors = []

    for owner in owner_queryset:
        try:
            data = selector(owner, period_start, period_end)

            # Degraded: selector signals it cannot produce reliable data
            if data.get("_degraded", False):
                logger.warning(
                    "comms_selector_degraded",
                    extra={
                        "owner": owner.name,
                        "product": product_name,
                        "period_type": period_type,
                    },
                )
                degraded += 1
                continue

            # Skip if selector reports no data
            if not data.get("_has_data", True):
                logger.info(
                    "comms_no_activity",
                    extra={"owner": owner.name, "product": product_name},
                )
                skipped += 1
                continue

            # Call Anthropic for narrative
            user_prompt = prompt_builder(
                data, data["owner_first_name"], period_start, period_end
            )
            ai_result = _call_anthropic(voice_guide.instructions, user_prompt)

            # Render template
            period_label = _format_period_label(
                period_type, period_start, period_end
            )
            context = context_builder(data, ai_result, period_label)
            body_html = render_to_string(template_name, context)

            if subject_template:
                subject = subject_template.format(period_label=period_label)
            else:
                subject = f"Weekly Maintenance Update — {period_label}"

            # Build plain-text note from AI result
            generated_note = _build_generated_note(
                ai_result, data, product_name
            )

            if dry_run:
                logger.info(
                    "comms_draft_dry_run",
                    extra={
                        "owner": owner.name,
                        "product": product_name,
                        "note_length": len(generated_note),
                    },
                )
                generated += 1
                continue

            # Normalize recipient email for the field, but key on owner
            norm_email = (owner.email or "").strip().lower()

            # Skip if a draft for this owner/period was already sent or approved
            existing = EmailDraft.objects.filter(
                product=product_name,
                owner=owner,
                period_type=period_type,
                period_start=period_start,
            ).first()
            if existing and existing.status in ("sent", "approved"):
                logger.info(
                    "comms_draft_already_locked",
                    extra={
                        "owner": owner.name,
                        "draft_id": existing.pk,
                        "status": existing.status,
                    },
                )
                skipped += 1
                continue

            EmailDraft.objects.update_or_create(
                product=product_name,
                owner=owner,
                period_type=period_type,
                period_start=period_start,
                defaults={
                    "recipient_email": norm_email,
                    "subject": subject,
                    "body_html": body_html,
                    "generated_note": generated_note,
                    "period_end": period_end,
                    "status": "draft",
                    "sent_at": None,
                    "sent_by": None,
                },
            )
            generated += 1

            logger.info(
                "comms_draft_generated",
                extra={
                    "owner": owner.name,
                    "product": product_name,
                    "period_type": period_type,
                },
            )

        except Exception as exc:
            msg = f"Error generating draft for {owner.name}: {exc}"
            logger.exception(msg)
            errors.append(msg)

    return {
        "generated": generated,
        "skipped": skipped,
        "degraded": degraded,
        "errors": len(errors),
        "error_details": errors[:20],
    }


def _normalize_email(email):
    """Normalize email for dedup: strip + lower."""
    return (email or "").strip().lower()


def generate_monthly_notes(
    owner_queryset, period_start, period_end, dry_run=False,
):
    """
    Generate monthly owner notes email drafts — portfolio-organized,
    recipient-email-grain.

    Separate from generate_drafts() to avoid polluting the maintenance path.

    Steps:
        1. Query active Owners with non-blank email.
        2. Group by normalize(email).
        3. For each email group:
           - Union portfolios across owners, dedupe by PK.
           - Pick representative owner (lowest PK).
           - For each portfolio: build_portfolio_section() (cached).
           - Build AI prompt with all sections.
           - Render template.
           - Upsert EmailDraft with recipient_email.

    Returns dict: {generated, skipped, errors, error_details}.
    """
    product_name = "monthly_owner_notes"
    period_type = "monthly"
    subject_template = PRODUCTS[product_name]["subject_template"]

    voice_guide = _get_or_create_voice_guide(product_name)

    # 1. Delete existing drafts in 'draft' status for this period
    existing_locked = EmailDraft.objects.filter(
        product=product_name,
        period_type=period_type,
        period_start=period_start,
        status__in=("sent", "approved"),
    )
    if existing_locked.exists():
        logger.warning(
            "comms_monthly_locked_drafts_exist",
            extra={
                "count": existing_locked.count(),
                "period_start": str(period_start),
            },
        )

    if not dry_run:
        deleted_count = EmailDraft.objects.filter(
            product=product_name,
            period_type=period_type,
            period_start=period_start,
            status="draft",
        ).delete()[0]
        if deleted_count:
            logger.info(
                "comms_monthly_drafts_cleared",
                extra={"deleted": deleted_count, "period_start": str(period_start)},
            )

    # 2. Group owners by normalized email
    email_groups = {}
    for owner in owner_queryset:
        email = _normalize_email(owner.email)
        if not email:
            logger.info(
                "comms_monthly_skip_blank_email",
                extra={"owner": owner.name, "owner_id": owner.pk},
            )
            continue
        email_groups.setdefault(email, []).append(owner)

    generated = 0
    skipped = 0
    errors = []

    # Cache portfolio sections to avoid rebuilding for shared portfolios
    section_cache = {}

    for norm_email, owners in email_groups.items():
        try:
            # Skip if a locked draft exists for this email/period
            existing = EmailDraft.objects.filter(
                product=product_name,
                recipient_email=norm_email,
                period_type=period_type,
                period_start=period_start,
                status__in=("sent", "approved"),
            ).first()
            if existing:
                logger.info(
                    "comms_monthly_draft_locked",
                    extra={
                        "email": norm_email,
                        "draft_id": existing.pk,
                        "status": existing.status,
                    },
                )
                skipped += 1
                continue

            # Union portfolios across all owners sharing this email
            portfolio_pks = set()
            for owner in owners:
                for pk in owner.portfolios.values_list("pk", flat=True):
                    portfolio_pks.add(pk)

            if not portfolio_pks:
                logger.info(
                    "comms_monthly_no_portfolios",
                    extra={"email": norm_email},
                )
                skipped += 1
                continue

            # Representative owner (lowest PK)
            rep_owner = min(owners, key=lambda o: o.pk)

            # Build portfolio sections
            from core.models import Portfolio

            portfolios = Portfolio.objects.filter(
                pk__in=portfolio_pks, is_active=True,
            ).order_by("name")
            portfolio_sections = []
            has_any_data = False

            for portfolio in portfolios:
                if portfolio.pk in section_cache:
                    section = section_cache[portfolio.pk]
                else:
                    section = build_portfolio_section(
                        portfolio, period_start, period_end
                    )
                    section_cache[portfolio.pk] = section

                portfolio_sections.append(section)

                # Check if any section has data
                if (
                    section.get("financials")
                    or section.get("maintenance", {}).get("_has_data")
                    or section.get("pipeline", {}).get("_has_data")
                ):
                    has_any_data = True

            if not has_any_data:
                logger.info(
                    "comms_monthly_no_activity",
                    extra={"email": norm_email},
                )
                skipped += 1
                continue

            # Build data dict for prompt/context
            owner_first_name = rep_owner.first_name or (
                rep_owner.name or "Owner"
            ).split()[0]

            data = {
                "owner_first_name": owner_first_name,
                "portfolio_sections": portfolio_sections,
            }

            # Call Anthropic for narrative
            user_prompt = _build_monthly_prompt(
                data, owner_first_name, period_start, period_end
            )
            ai_result = _call_anthropic(voice_guide.instructions, user_prompt)

            # Render HTML fragments for SendGrid dynamic template
            period_label = _format_period_label(
                period_type, period_start, period_end
            )
            context = _build_monthly_context(data, ai_result, period_label)
            financials_html = _render_financials_html(context)
            notes_html = _render_notes_html(context)
            body_html = json.dumps({
                "financials_html": financials_html,
                "notes_html": notes_html,
            })

            subject = subject_template.format(period_label=period_label)

            # Build plain-text note
            generated_note = _build_generated_note(
                ai_result, data, product_name
            )

            if dry_run:
                logger.info(
                    "comms_monthly_dry_run",
                    extra={
                        "email": norm_email,
                        "portfolios": [s["portfolio_name"] for s in portfolio_sections],
                        "note_length": len(generated_note),
                    },
                )
                generated += 1
                continue

            EmailDraft.objects.update_or_create(
                product=product_name,
                recipient_email=norm_email,
                period_type=period_type,
                period_start=period_start,
                defaults={
                    "owner": rep_owner,
                    "subject": subject,
                    "body_html": body_html,
                    "generated_note": generated_note,
                    "period_end": period_end,
                    "status": "draft",
                    "sent_at": None,
                    "sent_by": None,
                },
            )
            generated += 1

            logger.info(
                "comms_monthly_draft_generated",
                extra={
                    "email": norm_email,
                    "owner": rep_owner.name,
                    "portfolios": [s["portfolio_name"] for s in portfolio_sections],
                },
            )

        except Exception as exc:
            msg = f"Error generating monthly draft for {norm_email}: {exc}"
            logger.exception(msg)
            errors.append(msg)

    return {
        "generated": generated,
        "skipped": skipped,
        "errors": len(errors),
        "error_details": errors[:20],
    }


# ---------------------------------------------------------------------------
# Portfolio-grain generation (Layer 1)
# ---------------------------------------------------------------------------

# In-process guard — prevents concurrent generate_portfolio_notes() runs.
# Uses a monotonic timestamp instead of a boolean so a stuck flag auto-expires.
_portfolio_gen_lock = threading.Lock()
_portfolio_gen_started_at = None  # monotonic timestamp or None
_PORTFOLIO_GEN_TTL = 1800  # seconds — auto-expire a stuck lock


def get_portfolio_generation_scope():
    """Return the queryset of active portfolios in generation scope.

    Shared by the management command, the progress endpoint, and the
    generate API so that "total" is computed identically everywhere.
    """
    from core.models import Portfolio

    return Portfolio.objects.filter(is_active=True).order_by("name")


def _build_single_portfolio_context(section, ai_result, period_label):
    """
    Build template context for a SINGLE portfolio's note rendering.

    Wraps the section in a single-element portfolio_sections list so the
    existing _render_financials_html / _render_notes_html can be reused.
    """
    summaries = ai_result.get("process_summaries", {})

    fin = section.get("financials", {})
    stmt_period = ""
    if fin.get("period_start") and fin.get("period_end"):
        stmt_period = (
            f"{fin['period_start'].strftime('%b %d')} – "
            f"{fin['period_end'].strftime('%b %d, %Y')}"
        )

    maint = section.get("maintenance", {})

    pipeline = section.get("pipeline", {})
    by_category = pipeline.get("processes_by_category", {})
    categories = []
    for cat_key, procs in by_category.items():
        label = CATEGORY_LABELS.get(cat_key, cat_key.replace("_", " ").title())
        for proc in procs:
            pid = str(proc.get("process_id", ""))
            proc["ai_summary"] = summaries.get(pid, "")
        categories.append({
            "key": cat_key,
            "label": label,
            "processes": procs,
            "count": len(procs),
        })

    template_maint = {
        "has_data": maint.get("_has_data", False),
        "open_count": maint.get("open_count", 0),
        "closed_count": maint.get("closed_count", 0),
        "canceled_count": maint.get("canceled_count", 0),
    }

    template_section = {
        "portfolio_name": section["portfolio_name"],
        "has_financials": bool(fin),
        "statement_period": stmt_period,
        "total_income": fin.get("total_income"),
        "total_expenses": fin.get("total_expenses"),
        "total_distribution": fin.get("total_distribution"),
        "ending_balance": fin.get("ending_balance"),
        "maintenance": template_maint,
        "categories": categories,
        "unit_count": section.get("unit_count", 1),
        "units": section.get("units", []),
    }

    return {
        "ai_intro": ai_result.get("intro", ""),
        "portfolio_sections": [template_section],
    }


def _build_single_portfolio_generated_note(ai_result, section):
    """
    Build plain-text generated_note for a single portfolio.

    Intro + process summaries grouped by category.
    """
    parts = []
    intro = ai_result.get("intro", "")
    if intro:
        parts.append(intro)

    summaries = ai_result.get("process_summaries", {})
    pipeline = section.get("pipeline", {})
    by_category = pipeline.get("processes_by_category", {})
    for cat_key, procs in by_category.items():
        label = CATEGORY_LABELS.get(cat_key, cat_key.replace("_", " ").title())
        cat_lines = []
        for proc in procs:
            pid = str(proc.get("process_id", ""))
            summary = summaries.get(pid, "")
            if summary:
                cat_lines.append(summary)
        if cat_lines:
            parts.append(f"{label}:\n" + "\n".join(f"- {s}" for s in cat_lines))

    return "\n\n".join(parts)


def generate_portfolio_notes(
    portfolio_queryset, period_start, period_end, dry_run=False, progress_cb=None,
):
    """
    Generate portfolio-grain monthly notes (Layer 1: PortfolioMonthlyNote).

    One Anthropic call per portfolio. Owner-agnostic — no owner name, no
    cross-portfolio references. The note is reused across every owner who
    shares the portfolio.

    Wipe-and-regenerate at portfolio grain: deletes DRAFT-status rows for the
    period, preserves APPROVED rows.

    Args:
        progress_cb: optional callable(index, total, portfolio_name) called
                     before processing each portfolio. Used by the management
                     command for visible progress.

    Returns dict: {generated, skipped, errors, error_details}.
    """
    global _portfolio_gen_started_at

    product_name = "monthly_owner_notes"

    # Enforce: never generate for inactive portfolios
    portfolio_queryset = portfolio_queryset.filter(is_active=True)
    period_type = "monthly"
    voice_guide = _get_or_create_voice_guide(product_name)

    # Log any existing approved rows that will be preserved
    existing_approved = PortfolioMonthlyNote.objects.filter(
        period_type=period_type,
        period_start=period_start,
        status="approved",
    )
    if existing_approved.exists():
        logger.warning(
            "comms_portfolio_gen_approved_exist",
            extra={
                "count": existing_approved.count(),
                "period_start": str(period_start),
            },
        )

    if not dry_run:
        deleted_count = PortfolioMonthlyNote.objects.filter(
            period_type=period_type,
            period_start=period_start,
            status="draft",
        ).delete()[0]
        if deleted_count:
            logger.info(
                "comms_portfolio_notes_cleared",
                extra={"deleted": deleted_count, "period_start": str(period_start)},
            )

    generated = 0
    skipped = 0
    errors = []

    # Fetch-once: pull all processes and tasks once, pass to each portfolio.
    # A missing or unreadable LeadSimple key is a HARD FAILURE — we refuse to
    # generate notes with zero pipeline data and report errors=0.
    from integrations.leadsimple.client import DEGRADED as _DEGRADED
    from integrations.leadsimple.client import fetch_processes, fetch_tasks
    from integrations.leadsimple.services import build_tasks_index

    all_processes = fetch_processes()
    if all_processes is _DEGRADED:
        raise RuntimeError(
            "LeadSimple API is unavailable or LEADSIMPLE_API_KEY is not "
            "configured. Refusing to generate portfolio notes without "
            "pipeline data."
        )

    all_tasks = fetch_tasks(period_start=period_start)
    if all_tasks is _DEGRADED:
        raise RuntimeError(
            "LeadSimple tasks fetch failed after retries. Refusing to "
            "generate portfolio notes without task data."
        )
    all_tasks_index = build_tasks_index(all_tasks)

    # Materialize to a list so we know the total for progress reporting
    portfolios = list(portfolio_queryset)
    total = len(portfolios)

    for idx, portfolio in enumerate(portfolios, 1):
        if progress_cb:
            progress_cb(idx, total, portfolio.name)
        try:
            # Skip if an approved row already exists
            existing = PortfolioMonthlyNote.objects.filter(
                portfolio=portfolio,
                period_type=period_type,
                period_start=period_start,
                status="approved",
            ).first()
            if existing:
                logger.info(
                    "comms_portfolio_note_locked",
                    extra={
                        "portfolio": portfolio.name,
                        "note_id": existing.pk,
                        "status": existing.status,
                    },
                )
                skipped += 1
                continue

            # Build portfolio section data (with fetch-once data)
            section = build_portfolio_section(
                portfolio, period_start, period_end,
                prefetched_processes=all_processes,
                prefetched_tasks_index=all_tasks_index,
            )

            # Check if this portfolio has any data worth reporting
            has_data = bool(
                section.get("financials")
                or section.get("maintenance", {}).get("_has_data")
                or section.get("pipeline", {}).get("_has_data")
            )
            if not has_data:
                logger.info(
                    "comms_portfolio_note_no_activity",
                    extra={"portfolio": portfolio.name},
                )
                skipped += 1
                continue

            # Owner-agnostic AI call (one per portfolio)
            user_prompt = _build_single_portfolio_prompt(section, period_start)
            ai_result = _call_anthropic(voice_guide.instructions, user_prompt)

            # Render HTML fragments for this single portfolio
            period_label = _format_period_label(period_type, period_start, period_end)
            context = _build_single_portfolio_context(section, ai_result, period_label)
            financials_html = _render_financials_html(context)
            notes_html = _render_notes_html(context)

            # Build plain-text note
            generated_note = _build_single_portfolio_generated_note(ai_result, section)

            if dry_run:
                logger.info(
                    "comms_portfolio_note_dry_run",
                    extra={
                        "portfolio": portfolio.name,
                        "note_length": len(generated_note),
                    },
                )
                generated += 1
                continue

            PortfolioMonthlyNote.objects.update_or_create(
                portfolio=portfolio,
                period_type=period_type,
                period_start=period_start,
                defaults={
                    "period_end": period_end,
                    "financials_html": financials_html,
                    "notes_html": notes_html,
                    "generated_note": generated_note,
                    "approved_generated_note": "",
                    "status": "draft",
                },
            )
            generated += 1

            logger.info(
                "comms_portfolio_note_generated",
                extra={"portfolio": portfolio.name},
            )

        except Exception as exc:
            msg = f"Error generating portfolio note for {portfolio.name}: {exc}"
            logger.exception(msg)
            errors.append(msg)

    _portfolio_gen_started_at = None

    return {
        "generated": generated,
        "skipped": skipped,
        "errors": len(errors),
        "error_details": errors[:20],
    }


# ---------------------------------------------------------------------------
# Portfolio-grain maintenance notes (Layer 1)
# ---------------------------------------------------------------------------


def _build_maintenance_prompt_portfolio(data, portfolio_name, period_start, period_end):
    """
    Build the AI prompt for a portfolio-grain maintenance note.

    Owner-agnostic — no owner name, no cross-portfolio references.
    AI returns: {"intro": "...", "meld_summaries": {"<property_meld_id>": "..."}}
    """
    open_payload = _build_meld_payload(data["open_melds"], "Open work orders")
    closed_payload = _build_meld_payload(
        data["closed_melds"], "Completed this week"
    )
    canceled_payload = _build_meld_payload(
        data["canceled_melds"], "Canceled this week"
    )

    return (
        f"Write a maintenance update for the portfolio \"{portfolio_name}\".\n"
        f"Period: {period_start} to {period_end}.\n\n"
        f"{open_payload}\n\n"
        f"{closed_payload}\n\n"
        f"{canceled_payload}\n\n"
        "Write:\n"
        "1. A brief intro (1-2 sentences summarizing counts)\n"
        "2. For each work order identified by its [ID], a concise 1-2 sentence "
        "summary. Do NOT include the ID in the summary text.\n\n"
        'Return valid JSON only: {"intro": "...", "meld_summaries": {"<id>": "..."}}'
    )


def _build_maintenance_generated_note(ai_result):
    """Build plain-text generated_note from AI result (intro + bullet summaries)."""
    parts = []
    intro = ai_result.get("intro", "")
    if intro:
        parts.append(intro)

    summaries = ai_result.get("wo_summaries", {})
    if summaries:
        parts.append(
            "\n".join(
                f"- [#{wo_num}] {s}" if wo_num.strip() else f"- {s}"
                for wo_num, s in summaries.items()
                if s
            )
        )

    return "\n\n".join(parts)


def generate_portfolio_maintenance_notes(
    portfolio_queryset, period_start, period_end,
    period_type="weekly", dry_run=False,
):
    """
    Generate portfolio-grain maintenance notes (Layer 1: PortfolioMaintenanceNote).

    One Anthropic call per portfolio. Owner-agnostic.
    Data source: maintenance.WorkOrder (RentVine-native).

    Wipe-and-regenerate at portfolio grain: deletes DRAFT-status rows for the
    period, preserves APPROVED rows and rows with approved_generated_note.

    Returns dict: {generated, skipped, errors, error_details}.
    """
    from maintenance.selectors import get_portfolio_work_order_data

    # Enforce: never generate for inactive portfolios
    portfolio_queryset = portfolio_queryset.filter(is_active=True)
    voice_guide = _get_or_create_voice_guide("maintenance")

    if not dry_run:
        deleted_count = PortfolioMaintenanceNote.objects.filter(
            portfolio__in=portfolio_queryset,
            period_type=period_type,
            period_start=period_start,
            status="draft",
            approved_generated_note="",
            is_edited=False,
        ).delete()[0]
        if deleted_count:
            logger.info(
                "comms_maintenance_notes_cleared",
                extra={"deleted": deleted_count, "period_start": str(period_start)},
            )

    generated = 0
    skipped = 0
    errors = []

    portfolios = list(portfolio_queryset)

    for portfolio in portfolios:
        try:
            # Skip if an approved row already exists
            existing = PortfolioMaintenanceNote.objects.filter(
                portfolio=portfolio,
                period_type=period_type,
                period_start=period_start,
            ).first()
            if existing and (
                existing.status == "approved"
                or existing.approved_generated_note
                or existing.is_edited
            ):
                logger.info(
                    "comms_maintenance_note_locked",
                    extra={
                        "portfolio": portfolio.name,
                        "note_id": existing.pk,
                        "status": existing.status,
                    },
                )
                skipped += 1
                continue

            # Gather portfolio-grain work-order data
            data = get_portfolio_work_order_data(
                portfolio, period_start, period_end
            )

            if not data["_has_data"]:
                logger.info(
                    "comms_maintenance_note_no_activity",
                    extra={"portfolio": portfolio.name},
                )
                skipped += 1
                continue

            # Owner-agnostic AI call
            user_prompt = _build_maintenance_prompt_work_orders(
                data, portfolio.name, period_start, period_end
            )
            ai_result = _call_anthropic(voice_guide.instructions, user_prompt)

            # Attach AI summaries to work-order dicts for template rendering
            summaries = ai_result.get("wo_summaries", {})
            for section in (
                "open",
                "scheduled",
                "completed",
                "cancelled",
            ):
                for group in data.get(section, []):
                    for wo_dict in group["work_orders"]:
                        wo_num = wo_dict["work_order_number"]
                        wo_dict["ai_summary"] = summaries.get(wo_num, "")

            # Render fragment template
            period_label = _format_period_label(
                period_type, period_start, period_end
            )
            context = {
                "ai_intro": ai_result.get("intro", ""),
                "open_items": data["open"],
                "scheduled": data["scheduled"],
                "completed": data["completed"],
                "cancelled": data["cancelled"],
                "open_count": sum(len(g["work_orders"]) for g in data["open"]),
                "scheduled_count": sum(len(g["work_orders"]) for g in data["scheduled"]),
                "completed_count": sum(len(g["work_orders"]) for g in data["completed"]),
                "cancelled_count": sum(len(g["work_orders"]) for g in data["cancelled"]),
                "period_label": period_label,
            }
            notes_html = render_to_string(
                "comms/emails/maintenance_fragment.html", context
            )

            # Build plain-text note
            generated_note = _build_maintenance_generated_note(ai_result)

            # Freeze structured payload for later card rebuilds
            work_order_snapshot = {
                "intro": ai_result.get("intro", ""),
                "open": data["open"],
                "scheduled": data["scheduled"],
                "completed": data["completed"],
                "cancelled": data["cancelled"],
            }

            if dry_run:
                logger.info(
                    "comms_maintenance_note_dry_run",
                    extra={
                        "portfolio": portfolio.name,
                        "note_length": len(generated_note),
                    },
                )
                generated += 1
                continue

            PortfolioMaintenanceNote.objects.update_or_create(
                portfolio=portfolio,
                period_type=period_type,
                period_start=period_start,
                defaults={
                    "period_end": period_end,
                    "notes_html": notes_html,
                    "generated_note": generated_note,
                    "approved_generated_note": "",
                    "work_order_snapshot": work_order_snapshot,
                    "status": "draft",
                    "is_edited": False,
                },
            )
            generated += 1

            logger.info(
                "comms_maintenance_note_generated",
                extra={"portfolio": portfolio.name},
            )

        except Exception as exc:
            msg = f"Error generating maintenance note for {portfolio.name}: {exc}"
            logger.exception(msg)
            errors.append(msg)

    return {
        "generated": generated,
        "skipped": skipped,
        "errors": len(errors),
        "error_details": errors[:20],
    }


def render_maintenance_notes_html_from_snapshot(snapshot):
    """Render the maintenance card fragment HTML from a frozen work_order_snapshot.

    Pure function — takes the JSON-deserialized snapshot dict, returns an HTML
    string identical to what generate_portfolio_maintenance_notes produces via
    its inline render_to_string call.

    Coercion: scheduled_start_date and date_closed are stored as ISO strings
    after JSON round-tripping; the Django ``date`` template filter requires
    real date objects, so they are parsed back.  cost and estimated_amount
    pass through ``floatformat`` which handles strings natively — left as-is.

    The input snapshot is NOT mutated (operates on a deep copy).
    """
    data = copy.deepcopy(snapshot)

    # Coerce ISO-string dates back to date objects for the |date filter
    for bucket in ("open", "scheduled", "completed", "cancelled"):
        for group in data.get(bucket, []):
            for wo in group.get("work_orders", []):
                for field in ("scheduled_start_date", "date_closed"):
                    val = wo.get(field)
                    if val and isinstance(val, str):
                        wo[field] = _date.fromisoformat(val)

    context = {
        "ai_intro": data.get("intro", ""),
        "open_items": data.get("open", []),
        "scheduled": data.get("scheduled", []),
        "completed": data.get("completed", []),
        "cancelled": data.get("cancelled", []),
        "open_count": sum(len(g["work_orders"]) for g in data.get("open", [])),
        "scheduled_count": sum(len(g["work_orders"]) for g in data.get("scheduled", [])),
        "completed_count": sum(len(g["work_orders"]) for g in data.get("completed", [])),
        "cancelled_count": sum(len(g["work_orders"]) for g in data.get("cancelled", [])),
    }

    return render_to_string("comms/emails/maintenance_fragment.html", context)


# ---------------------------------------------------------------------------
# Owner-email assembly (Layer 2 — the SINGLE source for preview/test/send)
# ---------------------------------------------------------------------------


def assemble_owner_email(recipient_email, period_start, period_type="monthly"):
    """
    Pure function. Assemble the content of a monthly owner email for one
    recipient from portfolio-grain PortfolioMonthlyNote rows.

    Returns dict:
        {
            "owner_name": str,
            "financials_html": str,
            "notes_html": str,
            "portfolios": [
                {"id": int, "name": str, "status": str}, ...
            ],
            "all_approved": bool,
        }

    For each portfolio, uses approved_generated_note (and re-rendered
    notes_html) when non-blank; otherwise uses the generated content.

    Orders portfolios ALPHABETICALLY by name.
    """
    from core.models import Owner

    norm_email = _normalize_email(recipient_email)
    if not norm_email:
        return {
            "owner_name": "Owner",
            "financials_html": "",
            "notes_html": "",
            "portfolios": [],
            "all_approved": False,
        }

    # Group active owners sharing this email, union their portfolios
    owners = list(
        Owner.objects.filter(
            is_active=True,
            email__iexact=norm_email,
        ).prefetch_related("portfolios")
    )
    if not owners:
        return {
            "owner_name": "Owner",
            "financials_html": "",
            "notes_html": "",
            "portfolios": [],
            "all_approved": False,
        }

    # Representative owner (lowest PK, deterministic)
    rep_owner = min(owners, key=lambda o: o.pk)
    owner_name = rep_owner.first_name or (rep_owner.name or "Owner").split()[0]

    # Union portfolios, dedupe by PK
    portfolio_pks = set()
    for owner in owners:
        for pk in owner.portfolios.values_list("pk", flat=True):
            portfolio_pks.add(pk)

    if not portfolio_pks:
        return {
            "owner_name": owner_name,
            "financials_html": "",
            "notes_html": "",
            "portfolios": [],
            "all_approved": False,
        }

    # Fetch portfolio notes for this period, ordered alphabetically
    from core.models import Portfolio

    portfolios = Portfolio.objects.filter(
        pk__in=portfolio_pks, is_active=True,
    ).order_by("name")

    financials_parts = []
    notes_parts = []
    portfolio_info = []
    all_approved = True

    for portfolio in portfolios:
        note = PortfolioMonthlyNote.objects.filter(
            portfolio=portfolio,
            period_type=period_type,
            period_start=period_start,
        ).first()

        if not note:
            portfolio_info.append({
                "id": portfolio.pk,
                "name": portfolio.name,
                "status": "missing",
            })
            all_approved = False
            continue

        portfolio_info.append({
            "id": portfolio.pk,
            "name": portfolio.name,
            "status": note.status,
        })

        if note.status != "approved":
            all_approved = False

        # Use approved content if available, otherwise generated
        if note.approved_generated_note:
            notes_html = _render_notes_html_from_text(note.approved_generated_note)
        else:
            notes_html = note.notes_html

        financials_parts.append(note.financials_html)
        notes_parts.append(notes_html)

    return {
        "owner_name": owner_name,
        "financials_html": "\n".join(p for p in financials_parts if p),
        "notes_html": "\n".join(p for p in notes_parts if p),
        "portfolios": portfolio_info,
        "all_approved": all_approved,
    }


def assemble_owner_maintenance_email(recipient_email, period_start, period_type="weekly"):
    """
    Pure function. Assemble a maintenance owner email for one recipient
    from portfolio-grain PortfolioMaintenanceNote rows.

    Returns dict or None:
        None — zero-activity owner (no notes for this period).
        {
            "owner_name": str,
            "body_html": str,          # Full HTML email (envelope + fragments)
            "portfolios": [
                {"id": int, "name": str, "status": str}, ...
            ],
            "all_approved": bool,
        }

    Uses notes_html AS-IS (card-based fragments — no re-render from text).
    Quiet portfolios (no note this period) are simply omitted.
    """
    from core.models import Owner, Portfolio

    norm_email = _normalize_email(recipient_email)
    if not norm_email:
        return None

    # Group active owners sharing this email, union their portfolios
    owners = list(
        Owner.objects.filter(
            is_active=True,
            email__iexact=norm_email,
        ).prefetch_related("portfolios")
    )
    if not owners:
        return None

    # Representative owner (lowest PK, deterministic)
    rep_owner = min(owners, key=lambda o: o.pk)
    owner_name = rep_owner.first_name or (rep_owner.name or "Owner").split()[0]

    # Union portfolios, dedupe by PK
    portfolio_pks = set()
    for owner in owners:
        for pk in owner.portfolios.values_list("pk", flat=True):
            portfolio_pks.add(pk)

    if not portfolio_pks:
        return None

    # Fetch portfolio notes for this period, ordered alphabetically
    portfolios = Portfolio.objects.filter(
        pk__in=portfolio_pks, is_active=True,
    ).order_by("name")

    # Collect (portfolio_name, notes_html) tuples for fragments that rendered
    fragments = []
    portfolio_info = []
    all_approved = True

    for portfolio in portfolios:
        note = PortfolioMaintenanceNote.objects.filter(
            portfolio=portfolio,
            period_type=period_type,
            period_start=period_start,
        ).first()

        if not note:
            # Quiet portfolio — simply omit (no "no activity" line)
            continue

        portfolio_info.append({
            "id": portfolio.pk,
            "name": portfolio.name,
            "status": note.status,
        })

        if note.status != "approved":
            all_approved = False

        # Use notes_html AS-IS (card-based fragments, never re-render from text)
        if note.notes_html:
            fragments.append((portfolio.name, note.notes_html))

    # Zero-activity owner — no notes found at all
    if not fragments:
        return None

    # Build fragments_html — label each portfolio when 2+ rendered
    if len(fragments) > 1:
        from django.utils.html import escape

        labeled_parts = []
        for idx, (name, html) in enumerate(fragments):
            margin_top = "0" if idx == 0 else "32px"
            header = (
                '<table role="presentation" width="100%" cellpadding="0" '
                f'cellspacing="0" style="margin:{margin_top} 0 8px;">'
                "<tr>"
                '<td style="border-bottom:2px solid #1E3D58; padding-bottom:8px;">'
                '<h2 style="margin:0; font-family:Helvetica, Arial, sans-serif; '
                'font-size:20px; font-weight:700; color:#1E3D58;">'
                f"{escape(name)}"
                "</h2>"
                "</td>"
                "</tr>"
                "</table>"
            )
            labeled_parts.append(header + "\n" + html)
        fragments_html = "\n".join(labeled_parts)
    else:
        fragments_html = fragments[0][1]

    # Render envelope with concatenated fragments
    # Read period_end from the notes assembled for this email; fall back to +6.
    _assembled_pks = [p["id"] for p in portfolio_info]
    _note_period_ends = list(
        PortfolioMaintenanceNote.objects.filter(
            portfolio_id__in=_assembled_pks,
            period_type=period_type,
            period_start=period_start,
        )
        .exclude(period_end__isnull=True)
        .values_list("period_end", flat=True)
        .distinct()
    )
    if len(_note_period_ends) > 1:
        logger.warning(
            "comms_period_end_mismatch",
            extra={
                "portfolio_pks": _assembled_pks,
                "distinct_period_ends": sorted(
                    str(d) for d in _note_period_ends
                ),
            },
        )
    period_end = max(_note_period_ends, default=None) or (
        period_start + __import__("datetime").timedelta(days=6)
    )
    period_label = _format_period_label(period_type, period_start, period_end)

    body_html = render_to_string(
        "comms/emails/maintenance_envelope.html",
        {
            "owner_first_name": owner_name,
            "period_label": period_label,
            "fragments_html": fragments_html,
        },
    )

    return {
        "owner_name": owner_name,
        "body_html": body_html,
        "portfolios": portfolio_info,
        "all_approved": all_approved,
    }


def get_recipients_for_period(period_start, period_type="monthly"):
    """
    List all distinct recipient emails for a given period, with readiness info.

    Returns list of dicts:
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
    from core.models import Owner

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

        # Check each portfolio's note status
        from core.models import Portfolio

        portfolios = Portfolio.objects.filter(
            pk__in=portfolio_pks, is_active=True,
        ).order_by("name")
        portfolio_info = []
        all_approved = True

        for portfolio in portfolios:
            note = PortfolioMonthlyNote.objects.filter(
                portfolio=portfolio,
                period_type=period_type,
                period_start=period_start,
            ).first()

            if note:
                portfolio_info.append({
                    "id": portfolio.pk,
                    "name": portfolio.name,
                    "status": note.status,
                })
                if note.status != "approved":
                    all_approved = False
            else:
                portfolio_info.append({
                    "id": portfolio.pk,
                    "name": portfolio.name,
                    "status": "missing",
                })
                all_approved = False

        # Check if already sent (EmailDraft with status=sent for this email/period)
        is_sent = EmailDraft.objects.filter(
            product="monthly_owner_notes",
            recipient_email=norm_email,
            period_type=period_type,
            period_start=period_start,
            status="sent",
        ).exists()

        recipients.append({
            "recipient_email": norm_email,
            "owner_name": owner_name,
            "owner_names": [o.name for o in sorted(group_owners, key=lambda o: o.pk)],
            "portfolio_count": len(portfolio_pks),
            "portfolios": portfolio_info,
            "all_approved": all_approved,
            "is_sent": is_sent,
        })

    return recipients


# ---------------------------------------------------------------------------
# Send path
# ---------------------------------------------------------------------------


def send_draft(
    draft: EmailDraft,
    acting_user,
    recipient_override: str | None = None,
    sandbox: bool = False,
) -> dict:
    """
    Send a single EmailDraft via SendGrid.

    Returns a dict with send metadata (draft_id, recipient, mode, status, etc.).
    Raises PermissionError, ValueError, or RuntimeError on failure.
    """
    # 1. Role gate
    if not acting_user.can_access(draft.product):
        raise PermissionError(
            f"User {acting_user.username} cannot access product '{draft.product}'"
        )

    # 2. State guards
    if draft.status == "sent":
        raise ValueError(f"Draft {draft.pk} has already been sent")
    if not draft.subject or not draft.subject.strip():
        raise ValueError(f"Draft {draft.pk} has an empty subject")
    if not draft.body_html or not draft.body_html.strip():
        raise ValueError(f"Draft {draft.pk} has an empty body")

    # 3. Resolve recipient — prefer recipient_email, fall back to owner.email
    if recipient_override:
        recipient = recipient_override
    elif draft.recipient_email:
        recipient = draft.recipient_email
    else:
        recipient = draft.owner.email

    # Determine mode label for logging
    if sandbox:
        mode = "sandbox"
    elif recipient_override:
        mode = "test"
    else:
        mode = "live"

    # 4. Send via SendGrid
    if draft.product == "monthly_owner_notes":
        # SendGrid dynamic template — inject HTML fragments
        template_id = getattr(
            settings, "COMMS_SENDGRID_MONTHLY_TEMPLATE_ID", ""
        )
        try:
            fragments = json.loads(draft.body_html)
            financials_html = fragments.get("financials_html", "")
            notes_html = fragments.get("notes_html", "")
        except (json.JSONDecodeError, TypeError):
            raise ValueError(
                f"Draft {draft.pk} has legacy HTML body and must be "
                f"regenerated before sending."
            )

        owner_name = draft.owner.first_name or (
            draft.owner.name or "Owner"
        ).split()[0]

        message = Mail(
            from_email=settings.COMMS_FROM_EMAIL,
            to_emails=recipient,
            subject=draft.subject,
        )
        message.template_id = template_id
        message.dynamic_template_data = {
            "owner_name": owner_name,
            "financials_html": financials_html,
            "notes_html": notes_html,
        }

        # CC accounting
        if not sandbox:
            cc_email = getattr(settings, "COMMS_CC_EMAIL", "")
            if cc_email:
                message.add_cc(Cc(cc_email))
    else:
        # Standard HTML send (maintenance, etc.)
        message = Mail(
            from_email=settings.COMMS_FROM_EMAIL,
            to_emails=recipient,
            subject=draft.subject,
            html_content=draft.body_html,
        )

    if sandbox:
        message.mail_settings = MailSettings(sandbox_mode=SandBoxMode(True))

    sg = sendgrid.SendGridAPIClient(settings.SENDGRID_API_KEY)
    try:
        response = sg.send(message)
    except Exception:
        logger.error(
            "comms_send_draft_failed",
            extra={
                "draft_id": draft.pk,
                "owner": str(draft.owner),
                "recipient": recipient,
                "sent_by": acting_user.username,
                "mode": mode,
            },
        )
        raise

    status_code = response.status_code
    if status_code < 200 or status_code >= 300:
        logger.error(
            "comms_send_draft_non_2xx",
            extra={
                "draft_id": draft.pk,
                "sendgrid_status": status_code,
                "mode": mode,
            },
        )
        raise RuntimeError(
            f"SendGrid returned {status_code} for draft {draft.pk}"
        )

    # Extract message ID from response headers
    sg_message_id = ""
    if hasattr(response, "headers") and response.headers:
        sg_message_id = response.headers.get("X-Message-Id", "")

    # 5. Mark sent ONLY on live send (not sandbox, not test-email)
    if not sandbox and recipient_override is None:
        draft.status = "sent"
        draft.sent_at = timezone.now()
        draft.sent_by = acting_user
        draft.save(update_fields=["status", "sent_at", "sent_by"])

    # 7. Structured log
    result = {
        "draft_id": draft.pk,
        "owner": str(draft.owner),
        "recipient": recipient,
        "sent_by": acting_user.username,
        "mode": mode,
        "sendgrid_status": status_code,
        "sendgrid_message_id": sg_message_id,
    }
    logger.info("comms_send_draft_ok", extra=result)

    return result


# ---------------------------------------------------------------------------
# Owner Distribution snapshots (Layer 1)
# ---------------------------------------------------------------------------


def build_distribution_snapshot(portfolio, month_start, month_end, transactions, *, balance_index=None):
    """
    Pure function. Calls selectors over pre-fetched transactions and
    assembles the snapshot payload for one portfolio.

    Returns dict ready for upsert_distribution_snapshot():
        {
            "portfolio": Portfolio,
            "period_start": date,
            "period_end": date,
            "distribution_amount": Decimal,
            "distribution_date": date | None,
            "line_items": [{"property_id", "property_address",
                            "expected", "collected", "tenant_balance"}],
        }
    """
    from datetime import date as date_type

    from .selectors import get_portfolio_distribution, get_rent_by_property

    rent_data = get_rent_by_property(
        portfolio, month_start, month_end, transactions
    )
    dist_data = get_portfolio_distribution(
        portfolio, month_start, month_end, transactions
    )

    # Convert Decimals to strings for JSON serialization
    line_items = [
        {
            "property_id": item["property_id"],
            "property_address": item["property_address"],
            "expected": str(item["expected"]),
            "collected": str(item["collected"]),
            "tenant_balance": str(balance_index.get(item["property_id"], 0))
                if balance_index else "0",
        }
        for item in rent_data
    ]

    # Parse distribution date string to date object
    dist_date = None
    if dist_data["date"]:
        try:
            dist_date = date_type.fromisoformat(dist_data["date"])
        except (ValueError, TypeError):
            pass

    return {
        "portfolio": portfolio,
        "period_start": month_start,
        "period_end": month_end,
        "distribution_amount": dist_data["amount"],
        "distribution_date": dist_date,
        "line_items": line_items,
    }


def upsert_distribution_snapshot(payload):
    """
    Thin atomic writer. Idempotent upsert keyed on
    (portfolio, period_type="monthly", period_start).

    Returns (snapshot_instance, was_created).
    """
    from .models import PortfolioDistributionSnapshot

    defaults = {
        "period_end": payload["period_end"],
        "distribution_amount": payload["distribution_amount"],
        "distribution_date": payload["distribution_date"],
        "line_items": payload["line_items"],
    }
    if "undeposited_amount" in payload:
        defaults["undeposited_amount"] = payload["undeposited_amount"]
    if "undeposited_as_of" in payload:
        defaults["undeposited_as_of"] = payload["undeposited_as_of"]

    snapshot, created = PortfolioDistributionSnapshot.objects.update_or_create(
        portfolio=payload["portfolio"],
        period_type="monthly",
        period_start=payload["period_start"],
        defaults=defaults,
    )

    action = "created" if created else "updated"
    logger.info(
        "comms_distribution_snapshot_upserted",
        extra={
            "portfolio": str(payload["portfolio"]),
            "period_start": str(payload["period_start"]),
            "action": action,
            "distribution_amount": str(payload["distribution_amount"]),
            "property_count": len(payload["line_items"]),
        },
    )

    return snapshot, created


# ---------------------------------------------------------------------------
# Owner Distribution email assembly + send (Layer 2)
# ---------------------------------------------------------------------------


def assemble_owner_distribution_email(recipient_email, period_start, period_type="monthly"):
    """
    Assemble an owner-grain distribution email from PortfolioDistributionSnapshot rows.

    Returns dict | None:
      - None if owner has zero qualifying portfolios (no distribution_amount > 0 with date set)
      - Otherwise: {"owner_name", "body_html", "portfolios", "subject"}

    Inclusion gate: a portfolio is included ONLY if distribution_amount > 0 AND distribution_date is set.
    """
    from datetime import date as date_type
    from decimal import Decimal

    from django.conf import settings

    from core.models import Owner, Portfolio

    from .models import PortfolioDistributionSnapshot

    norm_email = _normalize_email(recipient_email)
    if not norm_email:
        return None

    # Group active owners sharing this email, union their portfolios
    owners = list(
        Owner.objects.filter(
            is_active=True,
            email__iexact=norm_email,
        ).prefetch_related("portfolios")
    )
    if not owners:
        return None

    # Representative owner (lowest PK, deterministic)
    rep_owner = min(owners, key=lambda o: o.pk)
    owner_name = rep_owner.first_name or (rep_owner.name or "Owner").split()[0]

    # Union portfolios, dedupe by PK
    portfolio_pks = set()
    for owner in owners:
        for pk in owner.portfolios.values_list("pk", flat=True):
            portfolio_pks.add(pk)

    if not portfolio_pks:
        return None

    # Fetch portfolios sorted by name
    portfolios = Portfolio.objects.filter(
        pk__in=portfolio_pks, is_active=True,
    ).order_by("name")

    from django.utils import timezone as tz

    fragments = []
    portfolio_info = []
    grand_expected = Decimal("0")
    grand_collected = Decimal("0")
    grand_distribution = Decimal("0")
    show_balance_footnote = False

    for portfolio in portfolios:
        snapshot = PortfolioDistributionSnapshot.objects.filter(
            portfolio=portfolio,
            period_type=period_type,
            period_start=period_start,
        ).first()

        if not snapshot:
            continue

        if not snapshot.line_items:
            continue

        # Compute subtotals from line_items
        total_expected = sum(Decimal(item["expected"]) for item in snapshot.line_items)
        total_collected = sum(Decimal(item["collected"]) for item in snapshot.line_items)

        # Derive as-of date from snapshot.updated_at (auto_now=True, ET)
        balance_as_of = (
            tz.localtime(snapshot.updated_at)
            .strftime("%b %d, %Y")
            .replace(" 0", " ")
        )

        # Format line items for template — include tenant_balance only when > 0
        formatted_items = []
        for item in snapshot.line_items:
            fi = {
                "property_address": item["property_address"],
                "expected": f"{Decimal(item['expected']):,.2f}",
                "collected": f"{Decimal(item['collected']):,.2f}",
            }
            bal = Decimal(item.get("tenant_balance", "0"))
            if bal > 0:
                fi["tenant_balance"] = f"{bal:,.2f}"
                show_balance_footnote = True
            formatted_items.append(fi)

        has_distribution = (
            snapshot.distribution_amount > 0 and snapshot.distribution_date is not None
        )

        fragment_context = {
            "portfolio_name": portfolio.name,
            "line_items": formatted_items,
            "total_expected": f"{total_expected:,.2f}",
            "total_collected": f"{total_collected:,.2f}",
            "distribution_amount": f"{snapshot.distribution_amount:,.2f}",
            "has_distribution": has_distribution,
            "distribution_date": (
                snapshot.distribution_date.strftime("%b %d, %Y")
                if has_distribution
                else ""
            ),
            "balance_as_of": balance_as_of,
        }
        if snapshot.undeposited_amount and snapshot.undeposited_amount > 0:
            fragment_context["undeposited_amount"] = f"{snapshot.undeposited_amount:,.2f}"
            if snapshot.undeposited_as_of:
                fragment_context["undeposited_as_of"] = (
                    snapshot.undeposited_as_of.strftime("%b %d, %Y").replace(" 0", " ")
                )

        fragment_html = render_to_string(
            "comms/emails/distribution_fragment.html",
            fragment_context,
        )
        fragments.append(fragment_html)

        portfolio_info.append({
            "id": portfolio.pk,
            "name": portfolio.name,
        })

        grand_expected += total_expected
        grand_collected += total_collected
        grand_distribution += snapshot.distribution_amount

    # Zero qualifying fragments → no email
    if not fragments:
        return None

    # Compute period label and by-date for statement reminder
    import calendar

    period_label = _format_period_label(period_type, period_start, period_start)
    month_name = period_start.strftime("%B")

    # by_date = 5th of month after period_start
    if period_start.month == 12:
        next_month = date_type(period_start.year + 1, 1, 5)
    else:
        next_month = date_type(period_start.year, period_start.month + 1, 5)
    by_date = next_month.strftime("%B %d").replace(" 0", " ")

    portal_url = getattr(settings, "OWNER_PORTAL_URL", "")
    show_grand_total = len(fragments) > 1

    body_html = render_to_string(
        "comms/emails/distribution_envelope.html",
        {
            "owner_first_name": owner_name,
            "period_label": period_label,
            "fragments_html": "\n".join(fragments),
            "show_grand_total": show_grand_total,
            "grand_total_expected": f"{grand_expected:,.2f}",
            "grand_total_collected": f"{grand_collected:,.2f}",
            "grand_total_distribution": f"{grand_distribution:,.2f}",
            "month_name": month_name,
            "by_date": by_date,
            "portal_url": portal_url,
            "show_balance_footnote": show_balance_footnote,
        },
    )

    subject = f"Your {month_name} distribution from Vesta"

    return {
        "owner_name": owner_name,
        "body_html": body_html,
        "portfolios": portfolio_info,
        "subject": subject,
    }


def send_distribution_email(
    recipient_email, period_start, period_type, period_end,
    acting_user, recipient_override=None, sandbox=False,
):
    """
    Single guarded send helper — both CLI and dashboard route through this.

    Returns:
      - "already_sent" if EmailDraft with status="sent" already exists
      - "no_activity" if assemble returns None
      - dict with send result on success

    Raises ValueError if OWNER_PORTAL_URL is blank in live mode.
    """
    from django.conf import settings

    from core.models import Owner

    norm_email = _normalize_email(recipient_email)

    # 1. Already-sent skip
    already_sent = EmailDraft.objects.filter(
        product="owner_distributions",
        recipient_email=norm_email,
        period_type=period_type,
        period_start=period_start,
        status="sent",
    ).exists()
    if already_sent:
        return "already_sent"

    # 2. Assemble
    assembled = assemble_owner_distribution_email(
        norm_email, period_start, period_type=period_type,
    )
    if assembled is None:
        return "no_activity"

    # 3. OWNER_PORTAL_URL guard — live mode requires it
    portal_url = getattr(settings, "OWNER_PORTAL_URL", "")
    is_live = not sandbox and recipient_override is None
    if not portal_url and is_live:
        raise ValueError(
            "OWNER_PORTAL_URL is not set. Refusing live send without portal URL."
        )

    # 4. Upsert EmailDraft
    rep_owner = Owner.objects.filter(
        is_active=True,
        email__iexact=norm_email,
    ).order_by("pk").first()

    if not rep_owner:
        return "no_activity"

    draft, _ = EmailDraft.objects.update_or_create(
        product="owner_distributions",
        owner=rep_owner,
        period_type=period_type,
        period_start=period_start,
        defaults={
            "recipient_email": norm_email,
            "subject": assembled["subject"],
            "body_html": assembled["body_html"],
            "generated_note": "",
            "period_end": period_end,
            "status": "approved",
            "sent_at": None,
            "sent_by": None,
        },
    )

    # 5. Send
    result = send_draft(
        draft=draft,
        acting_user=acting_user,
        recipient_override=recipient_override,
        sandbox=sandbox,
    )

    return result
