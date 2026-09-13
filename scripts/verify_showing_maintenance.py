#!/usr/bin/env python
"""
Throwaway verification script — reads delivery pk 6426 from the DB,
runs the parse-and-format logic, and PRINTS the exact Slack text that
would be sent.  Does NOT send anything, does NOT create a dedupe row,
does NOT write to the database.

Usage:
    python manage.py shell < scripts/verify_showing_maintenance.py
"""

from leasing.models import RentEngineWebhookDelivery
from leasing.services import build_showing_maintenance_message

delivery = RentEngineWebhookDelivery.objects.get(pk=6426)
payload = delivery.raw_payload
data = payload.get("data", payload)
record = data.get("record")

print(f"delivery pk:  {delivery.pk}")
print(f"table:        {data.get('table')}")
print(f"type:         {data.get('type')}")
print(f"event_type:   {record.get('event_type')}")
print()

# Resolve unit label and prospect name from the persisted leasing event
le = delivery.resulting_event
if le and le.unit:
    unit_label = str(le.unit)
else:
    unit_label = "(unit unresolved)"

if le and le.prospect:
    prospect_name = str(le.prospect)
else:
    prospect_name = f"prospect {record.get('prospect')}"

print(f"unit_label:   {unit_label}")
print(f"prospect:     {prospect_name}")
print()

slack_text = build_showing_maintenance_message(record, unit_label, prospect_name)

if slack_text is None:
    print("SKIP: build_showing_maintenance_message returned None")
    raise SystemExit(0)

print("=" * 60)
print("SLACK TEXT THAT WOULD BE SENT:")
print("=" * 60)
print(slack_text)
print("=" * 60)
