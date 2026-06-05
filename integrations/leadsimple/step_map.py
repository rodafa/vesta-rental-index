"""
Step ID → Owner-facing fragment map for LeadSimple task steps.

Pure-data module. Dict keyed by step.id UUID → (group_key, fragment_text).
Group keys: multiple step IDs sharing a group_key emit the fragment only once.

Completed (non-skipped) tasks whose step.id appears in this map contribute
their fragment to the owner note, providing "what we did" color.
"""

from datetime import datetime

from .services import _parse_dt_et


# ── Step Fragments ──────────────────────────────────────────────────────────
# Key: step_id UUID → (group_key, fragment_text)
# group_key is used for deduplication: if multiple completed tasks share
# the same group_key, the fragment is emitted only once.
#
# UUIDs from Phase 0.6 discovery (GET /tasks on live data).

STEP_FRAGMENTS: dict[str, tuple[str, str]] = {
    # ── Applications ──────────────────────────────────────────────────
    "5c54d7c4": ("app_screening", "completed full applicant screening — credit, background, rental, and employment verification"),
    "08f75b64": ("app_screening", "completed full applicant screening — credit, background, rental, and employment verification"),
    "be98d65c": ("app_screening", "completed full applicant screening — credit, background, rental, and employment verification"),
    "297e9e64": ("app_screening", "completed full applicant screening — credit, background, rental, and employment verification"),
    "c5cbf5df": ("app_screening", "completed full applicant screening — credit, background, rental, and employment verification"),
    "669bccb4": ("app_screening", "completed full applicant screening — credit, background, rental, and employment verification"),
    "f47f6525": ("app_lease_sent", "prepared and sent the lease for signature"),
    "d97589c4": ("app_voucher_paperwork", "submitted the housing-voucher paperwork"),
    "85024796": ("app_voucher_approved", "the housing authority approved the property"),
    "0c2c2004": ("app_voucher_inspection", "passed the housing inspection"),

    # ── Move Ins ──────────────────────────────────────────────────────
    "f0abf2e6": ("mi_schedule", "scheduled the move-in and inspection"),
    "36be9235": ("mi_inspection", "completed the move-in inspection"),
    "12abbfda": ("mi_utilities", "coordinated the utility transfer"),
    "b94e7e66": ("mi_utilities", "coordinated the utility transfer"),
    "44d84e4e": ("mi_welcome", "welcomed the new resident with their move-in packet"),
    "014e3664": ("mi_checkin", "completed a new-resident check-in call"),
    "2fb9a86a": ("mi_owner_notice", "let you know once the resident had moved in"),

    # ── Property Marketing ────────────────────────────────────────────
    "3ff7fae5": ("pm_photos", "scheduled professional listing photography"),
    "b80aa40f": ("pm_listed", "brought the property live on the market"),
    "8140d554": ("pm_listed", "brought the property live on the market"),
    "d3e8b381": ("pm_pricing", "shared pricing and recent comps with you for approval"),
    "58987b97": ("pm_pricing", "shared pricing and recent comps with you for approval"),
    "bdaaefe0": ("pm_refresh", "reviewed the listing and refreshed it to improve leasing"),
    "33f99a70": ("pm_concession", "introduced a move-in concession to attract applicants"),

    # ── Lease Renewals ────────────────────────────────────────────────
    "2208f9a8": ("lr_cma", "completed a current market rent analysis"),
    "46e81533": ("lr_history", "reviewed the resident's payment and maintenance history"),
    "c8cc1151": ("lr_options", "presented renewal options to you"),
    "134df4b8": ("lr_options", "presented renewal options to you"),
    "6c832cc0": ("lr_inspection", "completed the annual property inspection"),
    "1e7c7dd3": ("lr_inspection", "completed the annual property inspection"),
    "22137d51": ("lr_confirm", "confirmed the resident's renewal decision"),
    "50f128d4": ("lr_addendum", "prepared and sent the renewal addendum"),
    "4565e645": ("lr_premoveout", "completed a pre-move-out walkthrough"),

    # ── Late Rent ─────────────────────────────────────────────────────
    "f358d621": ("late_notice", "served the required late-rent notice"),
    "b68b23be": ("late_eviction", "began the eviction process with our attorneys"),

    # ── Move Outs ─────────────────────────────────────────────────────
    "e5fda35f": ("mo_schedule", "scheduled the move-out"),
    "3ec54c6e": ("mo_inspection", "completed the move-out inspection"),
    "039c1b7c": ("mo_turn_assess", "assessed the unit's turn needs"),
    "c81e813e": ("mo_keys", "collected the keys on move-out day"),
    "02be81df": ("mo_deposit_review", "reviewed the move-out inspection with you regarding the deposit"),
    "cd89356b": ("mo_deposit_done", "completed the security-deposit disposition"),
    "7f1bce84": ("mo_notice", "notified you that the resident gave their move-out notice"),
    "a31b1f5a": ("mo_notice", "notified you that the resident gave their move-out notice"),
    "ec576795": ("mo_nextsteps", "updated you on next steps after the move-out"),
    "9dc2d473": ("mo_nextsteps", "updated you on next steps after the move-out"),

    # ── Owner Onboarding ──────────────────────────────────────────────
    "09e81905": ("oo_intake", "gathered your account information and documents to begin onboarding"),
    "39c47a93": ("oo_intake", "gathered your account information and documents to begin onboarding"),
    "6d2f1a21": ("oo_portal", "set up your owner-portal access"),
    "712e3a1b": ("oo_quarterly", "scheduled proactive quarterly maintenance and walkthroughs"),
    "5313db5b": ("oo_note", "sent a handwritten note welcoming you to Vesta"),

    # ── Property Onboarding (tenant info) ─────────────────────────────
    "80d99621": ("po_tenant_info", "confirmed the in-place resident's information"),

    # ── Issues ────────────────────────────────────────────────────────
    "d1e809e9": ("iss_approval", "requested and received your approval"),
    "02e0df36": ("iss_approval", "requested and received your approval"),
    "d1e202fb": ("iss_decision", "notified the resident of the decision"),
    "695c574a": ("iss_decision", "notified the resident of the decision"),
}


def get_surfaced_fragments(
    process, tasks_for_process, period_start_et, period_end_et,
):
    """
    Return deduplicated list of owner-facing text fragments for a process.

    Only includes fragments from tasks that:
    - Are completed (completed_at in period)
    - Are not skipped
    - Have a step.id that maps to a known fragment

    Group deduplication: multiple tasks sharing a group_key emit the
    fragment text only once.

    Args:
        process: classified process dict
        tasks_for_process: list of task dicts for this process
        period_start_et: datetime with ET tzinfo
        period_end_et: datetime with ET tzinfo

    Returns:
        list[str] of fragment texts, deduplicated by group_key
    """
    seen_groups = set()
    fragments = []

    for task in tasks_for_process:
        if task.get("skipped"):
            continue
        completed_str = task.get("completed_at")
        if not completed_str:
            continue
        completed_et = _parse_dt_et(completed_str)
        if completed_et is None:
            continue
        if not (period_start_et <= completed_et <= period_end_et):
            continue

        # Check each step in the task
        steps = task.get("steps") or []
        for step in steps:
            step_id = step.get("id") or ""
            if step_id not in STEP_FRAGMENTS:
                continue
            group_key, fragment = STEP_FRAGMENTS[step_id]
            if group_key in seen_groups:
                continue
            seen_groups.add(group_key)
            fragments.append(fragment)

    return fragments
