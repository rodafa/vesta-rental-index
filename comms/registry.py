"""
Product registry: each email product = a data selector + voice guide + template.

Adding a new product (e.g. owner_report) is a dict entry, a selector function,
and a template — no new plumbing.
"""

PRODUCTS = {
    "maintenance": {
        "selector": "maintenance.selectors.get_owner_maintenance_data",
        "voice_guide_product": "maintenance",
        "template": "comms/emails/maintenance.html",
    },
}
