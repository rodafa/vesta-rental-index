"""
Field mapping functions: BoomPay/BoomScreen API JSON -> Django model field dicts.

Every mapper is defensive -- uses .get() with multiple fallback field names
and safe type coercion helpers that never raise.
"""

import logging

from integrations.rentvine.mappers import _get, _safe_datetime

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Status / type mapping tables
# ---------------------------------------------------------------------------

APPLICATION_STATUS_MAP = {
    # API value (lowercased) -> our ScreeningApplication.STATUS_CHOICES value
    "pending": "pending",
    "submitted": "pending",
    "new": "pending",
    "in_progress": "in_progress",
    "in progress": "in_progress",
    "processing": "in_progress",
    "under_review": "in_progress",
    "completed": "completed",
    "complete": "completed",
    "approved": "completed",
    "denied": "completed",
    "declined": "completed",
    "expired": "expired",
    "cancelled": "expired",
    "canceled": "expired",
}

REPORT_TYPE_MAP = {
    # API value (lowercased) -> our ScreeningReport.REPORT_TYPE_CHOICES value
    "credit": "credit",
    "credit_report": "credit",
    "credit report": "credit",
    "criminal": "criminal",
    "criminal_background": "criminal",
    "criminal background": "criminal",
    "background": "criminal",
    "eviction": "eviction",
    "eviction_history": "eviction",
    "eviction history": "eviction",
    "income": "income",
    "income_verification": "income",
    "income verification": "income",
    "employment": "income",
    "landlord_ref": "landlord_ref",
    "landlord_reference": "landlord_ref",
    "landlord reference": "landlord_ref",
    "landlord": "landlord_ref",
    "reference": "landlord_ref",
    "identity": "identity",
    "identity_verification": "identity",
    "identity verification": "identity",
    "id_verification": "identity",
}

DECISION_MAP = {
    # API value (lowercased) -> our ScreeningReport.DECISION_CHOICES value
    "pass": "pass",
    "passed": "pass",
    "approved": "pass",
    "accept": "pass",
    "accepted": "pass",
    "clear": "pass",
    "fail": "fail",
    "failed": "fail",
    "denied": "fail",
    "deny": "fail",
    "rejected": "fail",
    "decline": "fail",
    "declined": "fail",
    "review": "review",
    "needs_review": "review",
    "needs review": "review",
    "conditional": "review",
    "manual_review": "review",
    "pending": "pending",
    "in_progress": "pending",
    "processing": "pending",
}


# ---------------------------------------------------------------------------
# Mappers
# ---------------------------------------------------------------------------

def map_application(data):
    """
    Map BoomScreen application JSON to ScreeningApplication model fields.

    Accepts either a nested wrapper (e.g. {"application": {...}}) or the
    inner dict directly.
    Returns (boompay_id, defaults_dict).
    """
    if "application" in data and isinstance(data["application"], dict):
        raw_data = data
        data = data["application"]
    else:
        raw_data = data

    boompay_id = str(
        _get(data, "id", "applicationId", "application_id", default="")
    )
    if not boompay_id:
        raise ValueError(f"Application record missing ID: {data}")

    raw_status = str(
        _get(data, "status", "applicationStatus", "application_status", default="")
    ).lower().strip()
    status = APPLICATION_STATUS_MAP.get(raw_status, "pending")

    applicant_name = str(
        _get(data, "applicant_name", "applicantName", "name", "full_name", "fullName", default="")
    )
    # Try to build name from first/last if not found
    if not applicant_name:
        first = str(_get(data, "first_name", "firstName", default=""))
        last = str(_get(data, "last_name", "lastName", default=""))
        applicant_name = f"{first} {last}".strip()

    applicant_email = str(
        _get(data, "applicant_email", "applicantEmail", "email", default="")
    )

    defaults = {
        "applicant_name": applicant_name,
        "applicant_email": applicant_email,
        "status": status,
        "submitted_at": _safe_datetime(
            _get(data, "submitted_at", "submittedAt", "created_at", "createdAt", default=None)
        ),
        "completed_at": _safe_datetime(
            _get(data, "completed_at", "completedAt", default=None)
        ),
        "raw_data": raw_data,
    }

    # Try to extract unit cross-reference for FK matching
    unit_address = str(
        _get(data, "property_address", "propertyAddress", "address", default="")
    )
    unit_id = str(
        _get(data, "unit_id", "unitId", "property_id", "propertyId", default="")
    )

    return boompay_id, defaults, unit_address, unit_id


def map_report(data):
    """
    Map BoomScreen report JSON to ScreeningReport model fields.

    Accepts either a nested wrapper (e.g. {"report": {...}}) or the
    inner dict directly.
    Returns (boompay_id, defaults_dict).
    """
    if "report" in data and isinstance(data["report"], dict):
        raw_data = data
        data = data["report"]
    else:
        raw_data = data

    boompay_id = str(
        _get(data, "id", "reportId", "report_id", default="")
    )
    if not boompay_id:
        raise ValueError(f"Report record missing ID: {data}")

    raw_type = str(
        _get(data, "type", "report_type", "reportType", default="")
    ).lower().strip()
    report_type = REPORT_TYPE_MAP.get(raw_type, "credit")

    raw_decision = str(
        _get(data, "decision", "result", "outcome", "status", default="")
    ).lower().strip()
    decision = DECISION_MAP.get(raw_decision, "pending")

    # Extract report_data: the substantive report payload vs. raw_data (full response)
    report_data = data.get("report_data") or data.get("reportData") or data.get("details") or {}

    defaults = {
        "report_type": report_type,
        "decision": decision,
        "completed_at": _safe_datetime(
            _get(data, "completed_at", "completedAt", default=None)
        ),
        "report_data": report_data,
        "raw_data": raw_data,
    }

    return boompay_id, defaults
