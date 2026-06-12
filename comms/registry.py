"""
Product registry: each email product = a data selector + voice guide + template.

Adding a new product (e.g. owner_report) is a dict entry, a selector function,
and a template — no new plumbing.

Optional keys:
    subject_template — Python format string with {period_label} placeholder.
                       Falls back to "Weekly Maintenance Update — {period_label}".
"""

PRODUCTS = {
    "monthly_owner_notes": {
        "selector": "integrations.leadsimple.selectors.get_owner_pipeline_data",
        "prompt_builder": "comms.services._build_monthly_prompt",
        "context_builder": "comms.services._build_monthly_context",
        "voice_guide_product": "monthly_owner_notes",
        "template": "comms/emails/monthly_owner_notes.html",
        "subject_template": "Your {period_label} Owner Update — Vesta Property Management",
    },
    "owner_distributions": {
        "template": "comms/emails/distribution_envelope.html",
        "subject_template": "Your {month_name} distribution from Vesta",
    },
}
