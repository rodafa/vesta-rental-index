"""
Data migration: remove records where property_address is an email address
(corrupted form submissions), then import from merged_onboards.csv.
Safe to re-run — deduplicates by address before inserting.
"""
import csv
import os
import re

from django.db import migrations

EMAIL_RE = re.compile(r"^[^@]+@[^@]+\.[^@]+$")

IMPORTABLE_FIELDS = {
    "property_address", "property_city", "property_zip", "property_type",
    "bedrooms", "bathrooms", "plan", "owner1_name", "owner1_email",
    "owner1_phone", "owner1_mailing", "owner2_name", "owner2_email",
    "owner2_phone", "owner2_mailing", "poc_name", "poc_email", "poc_phone",
    "occupied", "transferring_pm", "hoa", "hoa_contact", "warranty",
    "warranty_company", "water_source", "water_provider", "water_in_rent",
    "sewer_system", "sewer_provider", "sewer_in_rent", "septic_age",
    "septic_last_pumped", "septic_bedroom_rating", "heating_type",
    "heating_fuel", "heating_fuel_in_rent", "oil_tank_location", "oil_company",
    "oil_tank_size", "gas_levels_measured", "cooling_system", "air_filters",
    "electric_provider", "electric_meter", "electric_in_rent",
    "trash_arrangement", "trash_company", "trash_day", "trash_in_rent",
    "internet_options", "internet_in_rent", "mail_arrangement", "laundry_setup",
    "lawn_care_in_rent", "lawn_care_vendor", "fireplace", "fireplace_type",
    "fireplace_works", "fireplace_last_inspection", "parking_arrangement",
    "parking_capacity", "pet_policy", "pet_restrictions", "section_8",
    "tenant_names", "tenant_contact", "tenants_notified", "current_rent",
    "lease_end_date", "deposit_amount", "deposit_location", "notes",
}


def clean_and_reimport(apps, schema_editor):
    OwnerOnboard = apps.get_model("onboarding", "OwnerOnboard")

    # Remove all records where property_address looks like an email
    bad_ids = [
        o.pk for o in OwnerOnboard.objects.only("pk", "property_address")
        if EMAIL_RE.match((o.property_address or "").strip())
    ]
    if bad_ids:
        OwnerOnboard.objects.filter(pk__in=bad_ids).delete()

    # Build set of already-present addresses to avoid duplicates
    existing = {
        a.lower().strip()
        for a in OwnerOnboard.objects.values_list("property_address", flat=True)
    }

    csv_path = os.path.join(os.path.dirname(__file__), "..", "..", "merged_onboards.csv")
    csv_path = os.path.normpath(csv_path)

    if not os.path.exists(csv_path):
        return

    to_create = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            address = (row.get("property_address") or "").strip()
            if not address or address.lower().strip() in existing:
                continue
            kwargs = {k: (v or "").strip() for k, v in row.items() if k in IMPORTABLE_FIELDS}
            to_create.append(OwnerOnboard(**kwargs))
            existing.add(address.lower().strip())

    if to_create:
        OwnerOnboard.objects.bulk_create(to_create)


def reverse_migration(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("onboarding", "0005_import_historical_onboards"),
    ]

    operations = [
        migrations.RunPython(clean_and_reimport, reverse_migration),
    ]
