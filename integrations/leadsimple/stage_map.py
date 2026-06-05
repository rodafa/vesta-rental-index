"""
Stage → Plain-English framing map for LeadSimple processes.

Pure-data module. Dict keyed by (process_type_uuid, stage_name) → plain-English
owner-facing status line. Uses actual API stage names.

If get_stage_framing returns None, the process is omitted from the owner note
entirely. The framing map IS the surfacing gate.
"""

# ── Process Type UUIDs ──────────────────────────────────────────────────────

_RENEWAL_UUID = "1865ad03-f740-4a99-b1a2-e2440430f3f4"
_RENEWAL_LEGACY_UUID = "44ac428c-30c3-4548-8a59-6cc5c687e5f8"

_MOVE_OUT_UUID = "e0dfad31-5459-4578-a0a1-b062180e769b"
_MOVE_OUT_LEGACY_UUID = "8311a377-6d68-48a0-855d-145fd1380ff4"

_ISSUES_UUID = "98e21401-58d3-4a6a-b6b0-f22dd11dc831"

_REHAB_UUID = "83f4af06-ad96-4ca7-9d21-5a1b00087ac6"

_MOVE_IN_UUID = "864f1bf2-53c8-4135-9196-9f6ecf15cf16"

_OWNER_ONBOARDING_UUID = "9b99ac28-0775-46cf-8537-12b4b08719b2"
_PROPERTY_ONBOARDING_UUID = "7e16b91a-bb9c-447e-937d-db3ce45f9588"

_MARKETING_UUID = "942fa5d5-6fc6-4495-8c94-bdb311525172"
_APPLICATION_UUID = "c9927e92-5c41-4c33-8a9e-8dbd6312eeb6"

_LATE_RENT_UUID = "75d01712-66f5-44fe-9595-f571e2449896"
_OFFBOARDING_UUID = "ee35a777-a854-47d4-82c1-87d022926783"


# ── Stage Framing Map ───────────────────────────────────────────────────────
# Key: (process_type_uuid, stage_name) → plain-English string
# If a stage is not in this map, the process is omitted from the note.

STAGE_FRAMING: dict[tuple[str, str], str] = {
    # ── 06 Lease Renewals ─────────────────────────────────────────────
    (_RENEWAL_UUID, "Upcoming"): "a renewal is coming up and our team is preparing it",
    (_RENEWAL_UUID, "Renewal Being Offered"): "a renewal has been prepared and offered to the resident",
    (_RENEWAL_UUID, "Tenant Renewing"): "the resident is renewing",
    (_RENEWAL_UUID, "Vesta Non Renewal"): "we've decided not to offer a renewal and are preparing for turnover",
    (_RENEWAL_UUID, "Tenant Not Renewing"): "the resident has chosen not to renew; we're preparing for turnover",
    (_RENEWAL_UUID, "Month To Month"): "the lease has moved to month-to-month",
    (_RENEWAL_UUID, "Lease Renewed"): "the renewal is complete and the lease is signed",

    # ── 070 Lease Renewals legacy (own framing, NOT copied from 06) ───
    (_RENEWAL_LEGACY_UUID, "Upcoming"): "a renewal is coming up and our team is preparing it",
    (_RENEWAL_LEGACY_UUID, "Send Lease"): "we've prepared and sent the renewal for signature",
    (_RENEWAL_LEGACY_UUID, "Non Renewal"): "the lease will not be renewed; we're preparing for turnover",
    (_RENEWAL_LEGACY_UUID, "Lease Renewed"): "the renewal is complete and the lease is signed",

    # ── 08 Move Out ───────────────────────────────────────────────────
    (_MOVE_OUT_UUID, "Scheduling - Pre Move Out"): "a move-out is scheduled and we're preparing for it",
    (_MOVE_OUT_UUID, "Breaking Lease"): "the resident is ending the lease early; we're managing the transition",
    (_MOVE_OUT_UUID, "Post Move Out"): "the resident has moved out and we're moving into turnover",
    (_MOVE_OUT_UUID, "Tenant Moved Out - Move Out 100% Done"): "the move-out is complete",

    # ── 09 Move Outs legacy ───────────────────────────────────────────
    (_MOVE_OUT_LEGACY_UUID, "Scheduling - Pre Move Out"): "a move-out is scheduled and we're preparing for it",
    (_MOVE_OUT_LEGACY_UUID, "30 Days or less to move out"): "a move-out is approaching within 30 days and we're preparing",
    (_MOVE_OUT_LEGACY_UUID, "Post Move Out"): "the resident has moved out and we're moving into turnover",
    (_MOVE_OUT_LEGACY_UUID, "Deposited Resolved Tenant 100% Closed Out"): "the move-out is complete and the deposit has been resolved",

    # ── Issues (exclude Maintenance/HVAC Filter/Other via filter_excluded_stages) ──
    (_ISSUES_UUID, "Issue Type - Payment Agreement"): "we've arranged a payment agreement with the resident",
    (_ISSUES_UUID, "Issue Type - Lease Violation"): "we're addressing a lease violation with the resident",
    (_ISSUES_UUID, "Issue Type - Insurance"): "we're handling an insurance matter on the property",
    (_ISSUES_UUID, "Issue Type - Tenant Support Ticket"): "we're assisting the resident with a support request",
    (_ISSUES_UUID, "Issue Type - Pet Addition"): "we're processing a pet request for the unit",

    # ── 09 Rehab to Turn ─────────────────────────────────────────────
    (_REHAB_UUID, "Vesta Managing Turn"): "we're managing the turn to get the property rent-ready",
    (_REHAB_UUID, "Owner Managing Turn"): "ownership is managing the turn on this property",
    (_REHAB_UUID, "Completed"): "the turn is complete and the property is rent-ready",

    # ── 05 Move Ins ───────────────────────────────────────────────────
    (_MOVE_IN_UUID, "Pre Move In"): "we're preparing for the resident's move-in",
    (_MOVE_IN_UUID, "Handoff"): "we're coordinating the move-in handoff",
    (_MOVE_IN_UUID, "Payment Pending - HACA / Vouchers"): "we're completing the final move-in steps, including any housing-voucher items",
    (_MOVE_IN_UUID, "Tenant Moved In"): "the new resident has moved in",

    # ── 01 Owner Onboarding ──────────────────────────────────────────
    (_OWNER_ONBOARDING_UUID, "Onboarding"): "we're onboarding the property and getting everything set up",

    # ── 02 Property Onboarding ───────────────────────────────────────
    (_PROPERTY_ONBOARDING_UUID, "Collecting Information"): "we're gathering the information needed to bring the property under management",
    (_PROPERTY_ONBOARDING_UUID, "Tenant Onboarding"): "we're onboarding the in-place resident",
    (_PROPERTY_ONBOARDING_UUID, "Vacant Onboarding"): "we're preparing the vacant property to bring it to market",
    (_PROPERTY_ONBOARDING_UUID, "Property Onboarded"): "the property is fully onboarded",

    # ── 03 Property Marketing ────────────────────────────────────────
    (_MARKETING_UUID, "Punch List Needed"): "the property needs punch-list work before marketing, which we're coordinating",
    (_MARKETING_UUID, "Going Live"): "we're preparing to bring the listing live",
    (_MARKETING_UUID, "Property Listed - Weekly Owner Updates"): "the property is listed and actively marketed",
    (_MARKETING_UUID, "45+ DOM Listed Properties"): "the property has been on the market over 45 days; we're reviewing pricing and the listing to improve results",

    # ── 04 Applications ──────────────────────────────────────────────
    (_APPLICATION_UUID, "Pending Owner Approval"): "an approved applicant is awaiting your decision",
    (_APPLICATION_UUID, "Lease Signed"): "we signed a new lease",
    (_APPLICATION_UUID, "Applicant Withdrew Application"): "an applicant withdrew; we're continuing to market the property",

    # ── 07 Late Rent ─────────────────────────────────────────────────
    (_LATE_RENT_UUID, "Late Rent - 5 Day Notice"): "rent is past due and we've served the required notice",
    (_LATE_RENT_UUID, "Negotiated Payment Agreement"): "we've arranged a payment agreement to bring the account current",
    (_LATE_RENT_UUID, "Non Payment - See Notes"): "the account remains unpaid and we're determining next steps",
    (_LATE_RENT_UUID, "Eviction"): "we've begun the eviction process and will keep you updated",
    (_LATE_RENT_UUID, "Payment Made in Full - During 5-Day Notice"): "the past-due rent has been paid in full",
    (_LATE_RENT_UUID, "Payment Made in Full - After 5 Day Notice"): "the past-due rent has been paid in full",
    (_LATE_RENT_UUID, "Balance Still Due - Surviving Until Next Month"): "a partial balance remains and is being carried into next month",

    # ── 10 Property Off Boarding ─────────────────────────────────────
    (_OFFBOARDING_UUID, "Notice to Offboard"): "we've received notice to off-board this property and have begun the transition",
    (_OFFBOARDING_UUID, "Financial & Data Closeout"): "we're completing the financial and records closeout",
    (_OFFBOARDING_UUID, "Physical & Final Handoff"): "we're completing the final handoff",
    (_OFFBOARDING_UUID, "Completed"): "the off-boarding is complete",
}


def get_stage_framing(process_type_uuid: str, stage_name: str) -> str | None:
    """
    Look up the plain-English framing for a process at a given stage.

    Returns None if no framing exists — caller should omit the process
    from the owner note entirely.
    """
    return STAGE_FRAMING.get((process_type_uuid, stage_name))
