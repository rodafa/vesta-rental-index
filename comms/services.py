"""
Comms engine: generate and send email drafts.

generate_drafts() — loads product config, calls selector per owner, sends data
to Anthropic for narrative prose, renders template, writes EmailDraft rows.

send_draft() — delivers a single EmailDraft via SendGrid with role-gating,
safety modes (sandbox / test-email / live), and structured logging.
"""

import json
import logging
from importlib import import_module

import anthropic
import sendgrid
from django.conf import settings
from django.template.loader import render_to_string
from django.utils import timezone
from sendgrid.helpers.mail import Mail, SandBoxMode, MailSettings

from .models import EmailDraft, VoiceGuide
from .registry import PRODUCTS

logger = logging.getLogger(__name__)

# Default voice guide texts — used to seed VoiceGuide rows on first run.

DEFAULT_VOICE_GUIDES = {
    "maintenance": """\
You write weekly maintenance email updates for property owners on behalf of \
Vesta Property Management.

Voice: Trustworthy, approachable, transparent. You are a knowledgeable property \
manager giving a clear, factual update — not a salesperson.

Rules:
- Write in first person plural ("We resolved the issue", "Our team scheduled")
- 1-2 sentences per work order, never more
- State facts: what happened, who did it, when
- For open items: present tense, mention vendor and scheduled date if known
- For completed items: past tense, mention vendor and completion date
- For canceled items: past tense, brief — just note it was canceled
- For items needing owner approval: flag clearly but not alarmingly
- No jargon, no filler, no speculation
- Do NOT include meld reference numbers or ticket IDs in your prose
- The intro paragraph should be 1-2 sentences summarizing the week's activity \
count (e.g. "This week there are 3 open work orders and 2 were completed.")
- Do not editorialize or add unnecessary reassurance\
""",
    "monthly_owner_notes": """\
You write monthly owner update emails for property investors on behalf of \
Vesta Property Management. Each email covers one owner's portfolio for the \
reporting month.

Voice: Trustworthy, approachable, transparent. You are a knowledgeable property \
manager giving a clear, factual operational update — not a salesperson. Warm \
and direct. Every problem is paired with what is being done about it.

Data categories you will receive (not all will be present every month):
- Lease Renewals: active renewal negotiations and completed renewals
- Move Outs: upcoming and completed tenant departures
- Move Ins: new tenant move-ins (completed milestones)
- Rehab to Turn: unit turnover and renovation work between tenants
- Issues: operational issues being tracked and resolved
- Onboarding: new owner or property onboarding processes

Rules:
- Write in first person plural ("We completed the renewal", "Our team \
coordinated the move-out")
- 1-2 sentences per process, never more
- State facts: what happened or is happening, current stage, next step
- For open items: present tense, mention current stage and what comes next
- For completed items: past tense, mention completion
- Group by category with a brief category heading
- The intro paragraph should be 2-3 sentences summarizing the month's \
activity by category count (e.g. "This month we processed 2 lease renewals \
and coordinated 1 move-out for your portfolio.")
- All dates in plain English format (e.g. "May 15, 2026") — never ISO format
- Do not mention internal system names, ticket IDs, or pipeline references
- Do not include financial details (rent amounts, costs, balances)
- Do not editorialize or add unnecessary reassurance
- No jargon, no filler, no speculation\
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
    """Load the VoiceGuide for a product, creating the default if missing."""
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


def _build_maintenance_prompt(data, owner_name, period_start, period_end):
    """Build the AI prompt for a maintenance email."""
    open_payload = _build_meld_payload(data["open_melds"], "Open work orders")
    closed_payload = _build_meld_payload(
        data["closed_melds"], "Completed this week"
    )
    canceled_payload = _build_meld_payload(
        data["canceled_melds"], "Canceled this week"
    )

    return (
        f"Write a maintenance email for {owner_name}.\n"
        f"Period: {period_start} to {period_end}.\n\n"
        f"{open_payload}\n\n"
        f"{closed_payload}\n\n"
        f"{canceled_payload}\n\n"
        "Write:\n"
        "1. A brief greeting intro (1-2 sentences summarizing counts)\n"
        "2. For each work order identified by its [ID], a concise 1-2 sentence "
        "summary. Do NOT include the ID in the summary text.\n\n"
        'Return valid JSON only: {"intro": "...", "meld_summaries": {"<id>": "..."}}'
    )


CATEGORY_LABELS = {
    "renewal": "Lease Renewals",
    "move_out": "Move Outs",
    "move_in": "Move Ins",
    "rehab_to_turn": "Rehab to Turn",
    "issues": "Issues",
    "onboarding": "Onboarding",
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
    """Build the AI prompt for a monthly owner notes email."""
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


# ---------------------------------------------------------------------------
# Per-product context builders
# ---------------------------------------------------------------------------


def _build_maintenance_context(data, ai_result, period_label):
    """Attach AI summaries to meld dicts and build the maintenance template context."""
    summaries = ai_result.get("meld_summaries", {})
    for section in ("open_melds", "closed_melds", "canceled_melds"):
        for meld_dict in data.get(section, []):
            pm_id = meld_dict["property_meld_id"]
            meld_dict["ai_summary"] = summaries.get(pm_id, "")

    return {
        "owner_first_name": data["owner_first_name"],
        "ai_intro": ai_result.get("intro", ""),
        "open_melds": data.get("open_melds", []),
        "closed_melds": data.get("closed_melds", []),
        "canceled_melds": data.get("canceled_melds", []),
        "open_count": len(data.get("open_melds", [])),
        "closed_count": len(data.get("closed_melds", [])),
        "canceled_count": len(data.get("canceled_melds", [])),
        "period_label": period_label,
    }


def _build_monthly_context(data, ai_result, period_label):
    """Attach AI summaries to process dicts and build the monthly template context."""
    summaries = ai_result.get("process_summaries", {})
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

    # 3. Resolve recipient
    recipient = recipient_override if recipient_override else draft.owner.email

    # Determine mode label for logging
    if sandbox:
        mode = "sandbox"
    elif recipient_override:
        mode = "test"
    else:
        mode = "live"

    # 4. Send via SendGrid
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
