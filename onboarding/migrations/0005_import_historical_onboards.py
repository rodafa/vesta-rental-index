"""
Data migration: import merged_onboards.csv into OwnerOnboard if the table is empty.
Safe to re-deploy — skips entirely if any records already exist.
"""
import csv
import os

from django.db import migrations


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


def import_onboards(apps, schema_editor):
    OwnerOnboard = apps.get_model("onboarding", "OwnerOnboard")

    if OwnerOnboard.objects.exists():
        return  # already populated — skip

    csv_path = os.path.join(os.path.dirname(__file__), "..", "..", "merged_onboards.csv")
    csv_path = os.path.normpath(csv_path)

    if not os.path.exists(csv_path):
        return  # file not present — skip silently

    to_create = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            address = (row.get("property_address") or "").strip()
            if not address:
                continue
            kwargs = {k: (v or "").strip() for k, v in row.items() if k in IMPORTABLE_FIELDS}
            to_create.append(OwnerOnboard(**kwargs))

    if to_create:
        OwnerOnboard.objects.bulk_create(to_create)


def reverse_import(apps, schema_editor):
    pass  # irreversible — no-op on reverse


class Migration(migrations.Migration):

    dependencies = [
        ("onboarding", "0004_alter_owneronboard_air_filters_and_more"),
    ]

    operations = [
        migrations.RunPython(import_onboards, reverse_import),
    ]
