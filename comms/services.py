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

# Default voice guide text for maintenance — used to seed the VoiceGuide row
DEFAULT_MAINTENANCE_VOICE_GUIDE = """\
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
"""


def _load_selector(dotted_path):
    """Import a selector function from a dotted path like 'app.module.func'."""
    module_path, func_name = dotted_path.rsplit(".", 1)
    module = import_module(module_path)
    return getattr(module, func_name)


def _get_or_create_voice_guide(product_name):
    """Load the VoiceGuide for a product, creating the default if missing."""
    guide, created = VoiceGuide.objects.get_or_create(
        product=product_name,
        defaults={"instructions": DEFAULT_MAINTENANCE_VOICE_GUIDE},
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


def _call_anthropic(voice_guide_text, owner_name, week_start, week_end, data):
    """
    Call the Anthropic API for narrative summaries.

    Returns dict: {"intro": "...", "meld_summaries": {"<pm_id>": "...", ...}}
    """
    model = getattr(settings, "ANTHROPIC_MODEL", "claude-sonnet-4-6")

    open_payload = _build_meld_payload(data["open_melds"], "Open work orders")
    closed_payload = _build_meld_payload(
        data["closed_melds"], "Completed this week"
    )
    canceled_payload = _build_meld_payload(
        data["canceled_melds"], "Canceled this week"
    )

    user_prompt = (
        f"Write a maintenance email for {owner_name}.\n"
        f"Week: {week_start} to {week_end}.\n\n"
        f"{open_payload}\n\n"
        f"{closed_payload}\n\n"
        f"{canceled_payload}\n\n"
        "Write:\n"
        "1. A brief greeting intro (1-2 sentences summarizing counts)\n"
        "2. For each work order identified by its [ID], a concise 1-2 sentence "
        "summary. Do NOT include the ID in the summary text.\n\n"
        'Return valid JSON only: {"intro": "...", "meld_summaries": {"<id>": "..."}}'
    )

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
        return {"intro": raw_text[:300], "meld_summaries": {}}


def generate_drafts(product_name, owner_queryset, week_start, week_end):
    """
    Generate email drafts for a product and set of owners.

    1. Load registry config and voice guide.
    2. Call the selector per owner.
    3. Call Anthropic for narrative prose.
    4. Render the HTML template.
    5. Write EmailDraft rows.

    Returns dict: {generated, skipped, errors}.
    """
    if product_name not in PRODUCTS:
        raise ValueError(f"Unknown product: {product_name}")

    config = PRODUCTS[product_name]
    selector = _load_selector(config["selector"])
    voice_guide = _get_or_create_voice_guide(config["voice_guide_product"])
    template_name = config["template"]

    generated = 0
    skipped = 0
    errors = []

    for owner in owner_queryset:
        try:
            data = selector(owner, week_start, week_end)

            # Skip if no activity at all
            if (
                not data["open_melds"]
                and not data["closed_melds"]
                and not data["canceled_melds"]
            ):
                logger.info(
                    "comms_no_activity",
                    extra={"owner": owner.name, "product": product_name},
                )
                skipped += 1
                continue

            # Call Anthropic for narrative
            ai_result = _call_anthropic(
                voice_guide.instructions,
                data["owner_first_name"],
                week_start,
                week_end,
                data,
            )

            # Attach AI summaries to meld dicts
            summaries = ai_result.get("meld_summaries", {})
            for section in ("open_melds", "closed_melds", "canceled_melds"):
                for meld_dict in data[section]:
                    pm_id = meld_dict["property_meld_id"]
                    meld_dict["ai_summary"] = summaries.get(pm_id, "")

            # Render template
            week_label = (
                f"{week_start.strftime('%b %d')} – "
                f"{week_end.strftime('%b %d, %Y')}"
            )
            context = {
                "owner_first_name": data["owner_first_name"],
                "ai_intro": ai_result.get("intro", ""),
                "open_melds": data["open_melds"],
                "closed_melds": data["closed_melds"],
                "canceled_melds": data["canceled_melds"],
                "open_count": len(data["open_melds"]),
                "closed_count": len(data["closed_melds"]),
                "canceled_count": len(data["canceled_melds"]),
                "week_start": week_start,
                "week_end": week_end,
                "week_label": week_label,
            }
            body_html = render_to_string(template_name, context)
            subject = f"Weekly Maintenance Update — {week_label}"

            # Skip if a draft for this owner/week was already sent
            existing = EmailDraft.objects.filter(
                product=product_name,
                owner=owner,
                week_start=week_start,
            ).first()
            if existing and existing.status == "sent":
                logger.info(
                    "comms_draft_already_sent",
                    extra={"owner": owner.name, "draft_id": existing.pk},
                )
                skipped += 1
                continue

            EmailDraft.objects.update_or_create(
                product=product_name,
                owner=owner,
                week_start=week_start,
                defaults={
                    "subject": subject,
                    "body_html": body_html,
                    "week_end": week_end,
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
                    "open": len(data["open_melds"]),
                    "closed": len(data["closed_melds"]),
                    "canceled": len(data["canceled_melds"]),
                },
            )

        except Exception as exc:
            msg = f"Error generating draft for {owner.name}: {exc}"
            logger.exception(msg)
            errors.append(msg)

    return {
        "generated": generated,
        "skipped": skipped,
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
