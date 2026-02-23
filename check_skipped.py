"""One-off script to inspect RentEngine data for skipped units."""
import django
import os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "vesta_rental_index.settings")
django.setup()

from integrations.rentengine.client import RentEngineClient
import json

client = RentEngineClient()
units = client.get_all("/units")

skipped = {23532, 23537, 23539, 23542, 23832, 24260, 24333, 25223, 25257, 27288, 27311, 34565, 35600, 35645, 39485}

for u in units:
    uid = u.get("id")
    if uid in skipped:
        addr = u.get("address") or {}
        print(f"RE_ID={uid}")
        print(f"  formatted: {addr.get('formatted_address', '')}")
        print(f"  street_number: {addr.get('street_number', '')}")
        print(f"  street_name: {addr.get('street_name', '')}")
        print(f"  unit: '{addr.get('unit', '')}'")
        print(f"  zip: {addr.get('zip_code', '')}")
        print(f"  name: {u.get('name', '')}")
        print(f"  status: {u.get('status', '')}")
        print(f"  bedrooms: {u.get('bedrooms', '')}")
        print(f"  sqft: {u.get('sqft', '')}")
        print(f"  extracted_from: {u.get('extracted_from', '')}")
        print(f"  all_keys: {list(u.keys())}")
        print("---")
