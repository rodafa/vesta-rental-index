"""
Services for approving and sending monthly owner report emails
via SendGrid dynamic template.
"""
import logging

from django.conf import settings
from django.utils import timezone

from reports.models import OwnerReportLog

logger = logging.getLogger(__name__)


def approve_owner_report(report_id: int) -> OwnerReportLog:
    """Set status='approved' and record approval timestamp."""
    report = OwnerReportLog.objects.get(id=report_id)
    report.status = "approved"
    report.approved_at = timezone.now()
    report.save(update_fields=["status", "approved_at"])
    return report


def _build_financials_html(report: OwnerReportLog) -> str:
    """
    Render the financial statement as an HTML table.
    Styled with font-family: Georgia; color: #333; matching the Apps Script pattern.
    """
    def fmt(val):
        return "${:,.2f}".format(float(val))

    rows = [
        ("Statement Period", report.statement_period or "—"),
        ("Beginning Balance", fmt(report.beginning_balance)),
        ("Total Income", fmt(report.total_income)),
        ("Total Expenses", fmt(report.total_expenses)),
        ("Total Adjustments", fmt(report.total_adjustments)),
        ("Ending Balance", fmt(report.ending_balance)),
        ("Total Distribution", fmt(report.total_distribution)),
    ]

    cell_style = "padding:6px 12px;border-bottom:1px solid #e5e7eb;"
    row_html = "".join(
        '<tr>'
        '<td style="{style}">{label}</td>'
        '<td style="{style}text-align:right;">{value}</td>'
        '</tr>'.format(style=cell_style, label=label, value=value)
        for label, value in rows
    )

    return (
        '<table style="font-family:Georgia;color:#333;border-collapse:collapse;'
        'width:100%;max-width:480px;">'
        + row_html
        + "</table>"
    )


_TEXT_STYLE = (
    "font-family:Georgia,'Times New Roman',serif;"
    "font-size:14px;color:#333333;line-height:135%;"
)
_UL_STYLE = "margin:0;padding:0 0 0 15px;list-style:disc;"
_NESTED_UL_STYLE = "margin:0;padding:0 0 0 20px;list-style:disc;"
_LI_STYLE = f"{_TEXT_STYLE}padding:2px 0;"


def _note_to_html(text: str) -> str:
    """
    Convert the AI-generated plain-text note to semantic HTML.

    Handles:
    - Lines starting with "- " or "• " → <li> inside <ul>
    - Lines starting with 2+ spaces then "- " or "• " → nested <li> in child <ul>
    - Plain text lines → <p>
    - Empty lines → close any open list and continue
    """
    if not text:
        return ""

    html_parts = []
    in_list = False       # inside a top-level <ul>
    in_nested = False     # inside a nested <ul>

    def _close_nested():
        nonlocal in_nested
        if in_nested:
            html_parts.append("</ul></li>")
            in_nested = False

    def _close_list():
        nonlocal in_list
        _close_nested()
        if in_list:
            html_parts.append("</ul>")
            in_list = False

    for raw_line in text.split("\n"):
        line = raw_line.rstrip()

        # Empty line — close any open lists
        if not line.strip():
            _close_list()
            continue

        stripped = line.lstrip()

        # Detect bullet lines
        is_bullet = stripped.startswith("- ") or stripped.startswith("\u2022 ")
        indent = len(line) - len(stripped)
        is_nested_bullet = is_bullet and indent >= 2

        if is_nested_bullet:
            bullet_text = stripped[2:]  # skip "- " or "• "
            if not in_list:
                html_parts.append(f'<ul style="{_UL_STYLE}">')
                in_list = True
            if not in_nested:
                # Open a parent <li> that will contain the nested <ul>
                html_parts.append(f'<li style="{_LI_STYLE}"><ul style="{_NESTED_UL_STYLE}">')
                in_nested = True
            html_parts.append(f'<li style="{_LI_STYLE}">{bullet_text}</li>')

        elif is_bullet:
            bullet_text = stripped[2:]
            _close_nested()
            if not in_list:
                html_parts.append(f'<ul style="{_UL_STYLE}">')
                in_list = True
            html_parts.append(f'<li style="{_LI_STYLE}">{bullet_text}</li>')

        else:
            # Plain text line
            _close_list()
            html_parts.append(f'<p style="{_TEXT_STYLE}margin:0 0 4px 0;">{line}</p>')

    _close_list()
    return "".join(html_parts)


def send_owner_report(report_id: int) -> tuple:
    """
    Send the monthly owner report email via SendGrid dynamic template.

    Looks up the owner's email from the Owner model, builds the financials
    HTML table and converts the generated note to HTML, then POSTs to SendGrid
    via anymail using SG_OWNER_REPORT_TEMPLATE_ID with dynamic_template_data.

    Returns: (success: bool, message: str)
    """
    try:
        report = OwnerReportLog.objects.get(id=report_id)
    except OwnerReportLog.DoesNotExist:
        return False, f"Report {report_id} not found"

    if report.status == "sent":
        return True, "Already sent"

    # Resolve owner email from properties.Owner
    from properties.models import Owner
    owner = Owner.objects.filter(rentvine_contact_id=report.owner_id).first()
    if not owner or not owner.email:
        report.status = "failed"
        report.error_message = "Owner email not found"
        report.save(update_fields=["status", "error_message"])
        return False, "Owner email not found"

    recipient = owner.email
    financials_html = _build_financials_html(report)
    notes_html = _note_to_html(report.generated_note)

    template_id = getattr(settings, "SG_OWNER_REPORT_TEMPLATE_ID", "")
    cc_email = getattr(settings, "SG_OWNER_REPORT_CC", "accounting@vestapm.com")

    try:
        from anymail.message import AnymailMessage
    except ImportError:
        return False, "SendGrid not configured (SENDGRID_API_KEY missing)"

    try:
        msg = AnymailMessage(
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[recipient],
            cc=[cc_email],
        )
        msg.template_id = template_id
        msg.merge_global_data = {
            "owner_name": report.owner_name,
            "financials_html": financials_html,
            "notes_html": notes_html,
        }
        msg.send()

        msg_id = ""
        if hasattr(msg, "anymail_status"):
            msg_id = getattr(msg.anymail_status, "message_id", "") or ""

    except Exception as exc:
        logger.exception(
            "Failed to send owner report email to %s (report %s)", recipient, report_id
        )
        report.status = "failed"
        report.error_message = str(exc)[:500]
        report.save(update_fields=["status", "error_message"])
        return False, f"Email delivery failed: {exc}"

    report.status = "sent"
    report.sent_at = timezone.now()
    report.sendgrid_message_id = msg_id
    report.save(update_fields=["status", "sent_at", "sendgrid_message_id"])
    logger.info(
        "Owner report sent to %s (%s) — report %s", owner.name, recipient, report_id
    )
    return True, "Sent"
