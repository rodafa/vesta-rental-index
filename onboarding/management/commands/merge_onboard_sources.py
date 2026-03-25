"""
Merges all three historical onboarding CSVs into a single normalized CSV
ready for import_historical_onboards.

Usage:
    python manage.py merge_onboard_sources --output /app/merged_onboards.csv
"""
import csv
import os

from django.core.management.base import BaseCommand

from onboarding.models import OwnerOnboard

# All model fields that can be imported
MODEL_FIELDS = [f.name for f in OwnerOnboard._meta.get_fields()
                if hasattr(f, "column") and f.name not in ("id", "submitted_at")]

# ── File 1 & 3: JotForm exports (same column names) ──────────────────────────
JOTFORM_MAP = {
    "property_address": "Property Street Address",
    "property_city": "Property City",
    "property_zip": "Property Zip",
    "property_type": "Property Type",
    "bedrooms": "Number of Bedrooms",
    "bathrooms": "Number of Bathrooms",
    "plan": "Which Management Plan?",
    "owner1_name": "Owner Name(s) in Public Records. Please include all owners. ",
    "poc_name": "Point of Contact - Name",
    "poc_email": "Point of Contact - Email",
    "poc_phone": "Point of Contact - Phone",
    "hoa": "Does the Property have an HOA?",
    "hoa_contact": "(If Yes) HOA Name & Contact Info",
    "warranty": "Is there an active Home Warranty? ",
    "warranty_company": "(If Yes) Warranty Company ",
    "water_source": "Water Source?",
    "water_provider": "Water Provider Name - Who Do You Pay For Water? ",
    "water_in_rent": "Is water included in rent? ",
    "sewer_system": "Sewer System",
    "sewer_in_rent": "Sewer/Septic included in rent? ",
    "septic_age": "(If Septic) Estimated age of tank if known? ",
    "septic_last_pumped": "(If Septic) Date the tank was last pumped?",
    "septic_bedroom_rating": "(If Septic) If known, how many bedrooms is the tank rated for? ",
    "heating_type": "How is the property heated? ",
    "heating_fuel": "Heating Fuel Source",
    "heating_fuel_in_rent": "Is Heating Fuel included in Rent? ",
    "oil_tank_location": "(If Propane/Oil) Where is your oil tank?",
    "oil_tank_size": "(If Propane/Oil) How large is your tank? (Gallons)",
    "gas_levels_measured": "(If Propane/Oil) Were the levels of gas measured at the beginning of the tenancy of the current resident(s)? ",
    "cooling_system": "Cooling System",
    "air_filters": "If known, please number of air filters and filter size for your property. ",
    "electric_provider": "Electric Provider Name",
    "electric_meter": "Electric Meter",
    "electric_in_rent": "Is Electric included in Rent? ",
    "trash_arrangement": "Trash Arrangement",
    "trash_day": "When is trash & recycling picked up? ",
    "trash_company": "Who is the trash pick up company? ",
    "trash_in_rent": "Is the cost of trash removal included in rent?",
    "mail_arrangement": "How do residents get their mail? ",
    "lawn_care_in_rent": "Is lawn care included in rent? ",
    "lawn_care_vendor": "(If Yes) Do you have a preferred lawn care vendor? ",
    "laundry_setup": "Laundry Set Up",
    "fireplace": "Does the property have a fireplace?",
    "fireplace_type": "(If Yes) What type of fireplace is it?",
    "fireplace_works": "(If Yes) Does the fireplace work?",
    "fireplace_last_inspection": "Date fire place was last inspected? ",
    "pet_policy": "Pet Policy: Do you allow pets at this property? ",
    "pet_restrictions": "(If Yes) Do you have any restrictions? (eg: No cats, Dogs under 50lbs, etc.)",
    "parking_arrangement": "Parking Arrangement",
    "parking_capacity": "Parking for how many cars",
    "section_8": "Section 8 or Housing Assistance Programs Accepted?",
    "occupied": "Is the property occupied? ",
    "tenant_names": "(If Tenant) Tenant Name",
    "tenant_contact": "(If Tenant) Tenant Contact - Phone/Email",
    "tenants_notified": "(If Tenant) Have tenants been notified of the upcoming change? ",
    "current_rent": "(If Tenant) Monthly Rent",
    "lease_end_date": "(If Tenant) Lease End Date",
    "deposit_amount": "(If Tenant) Security Deposit Amount",
    "deposit_location": "(If Tenant) Who Holds the Security Deposit?",
    "transferring_pm": "Transferring from another PM company? ",
    "notes": "Anything you want to tell us about the property? ",
}

# Oil company has a slightly different label in File 3
JOTFORM_MAP_F3 = dict(JOTFORM_MAP)
JOTFORM_MAP_F3["oil_company"] = "(If /Propane/Oil) What company do you typically order from? "
JOTFORM_MAP["oil_company"] = "(If Gas/Propane/Oil) What company do you typically order from? "

# Internet label varies between files — try both
JOTFORM_INTERNET_LABELS = [
    "What internet options exist? (Charter, Starlink, AT&T, etc.)",
    "What internet options exist? (Charter, Starlink, AT\u2019T, etc.)",  # smart apostrophe
]

# ── File 2: Google Form export ────────────────────────────────────────────────
GOOGLE_MAP = {
    "owner1_email": "Email Address",
    "plan": "What management plan would you like to sign up for? \n(Click here for pricing breakdown)",
    "property_address": "Rental property address. Please write the full address including the street number, street name, town/city and zip code.  ",
    "owner1_name": "Owner name(s) in public records. Please include all owners.",
    "owner1_phone": "Owner phone number(s). Please include all owners.",
    "owner1_mailing": "Owner(s) mailing address. If there are multiple owners with different addresses, please include the mailing address for each owner. ",
    "property_type": "What describes your property best:",
    "water_source": "What is the water source for your property?",
    "water_provider": "Who is the water provider company? ",
    "water_in_rent": "Is the cost of water included in rent?",
    "sewer_system": "What is the sewer system in your property? ",
    "sewer_provider": "What is the sewerage treatment provider company?",
    "septic_last_pumped": "If there is a septic tank, what was the date of the last pump?",
    "sewer_in_rent": "Is the cost of the sewer/septic included in rent?",
    "heating_type": "What is the heating system type?",
    "heating_fuel": "Does the property use gas, propane, or heating oil? If yes, what type is it? ",
    "heating_fuel_in_rent": "Is the cost of gas/oil included in rent?",
    "oil_company": "Do you have a preferred company that you use for the gas/oil? ",
    "gas_levels_measured": "Were the levels of gas/oil measured at the beginning of the tenancy of the current resident(s)?",
    "cooling_system": "What is the cooling source for your property?",
    "electric_provider": "Who is the electric provider company?",
    "electric_meter": "What is the electric meter arrangement?",
    "electric_in_rent": "Is the cost of electric included in rent?",
    "internet_options": "What provider serves the property for phone/internet/cable?",
    "internet_in_rent": "Is the cost of phone/internet/cable included in rent? ",
    "mail_arrangement": "What is the mail arrangement?",
    "lawn_care_in_rent": "Is lawn care included in rent?",
    "trash_arrangement": "What is the trash arrangement?",
    "trash_company": "Who is the trash provider? What specific town/city provides trash pickup?",
    "trash_day": "What is the trash pick up day (please include recycling pick up)? ",
    "trash_in_rent": "Is the cost of trash pick up included in rent?",
    "parking_arrangement": "What is the parking arrangement? ",
    "parking_capacity": "What is the parking capacity?",
    "pet_policy": "Do you allow pets at your property?",
    "pet_restrictions": "Do you allow any other type of pets at your property? (Fish - Fish tanks, snakes, spiders, rabbits, farm animals, etc.) ",
    "occupied": "Is the property currently occupied?",
    "tenant_names": "(Property Occupied) Tenant Name(s)",
    "tenant_contact": "(Property Occupied) Tenant Email Address(es)",
    "warranty": "Is there an active Home Warranty on the property? ",
    "warranty_company": "If yes - please provide all relevant contact information for providers as needed. ",
    "laundry_setup": "What type of laundry setup is at the property? ",
    "air_filters": "If the heating/cooling system requires filters, please provide filter size. ",
    "hoa": "Is the property part of an HOA",
    "notes": "Is there any other information you would like to share with us? ",
}


def _map_row(row, field_map, extra_labels=None):
    """Map a CSV row dict to model field dict using the given field_map."""
    result = {f: "" for f in MODEL_FIELDS}
    for model_field, csv_label in field_map.items():
        val = row.get(csv_label, "").strip()
        if val:
            result[model_field] = val

    # Handle alternate internet labels for JotForm files
    if extra_labels and not result.get("internet_options"):
        for label in extra_labels:
            val = row.get(label, "").strip()
            if val:
                result["internet_options"] = val
                break

    return result


def _dedup_key(address):
    return address.lower().strip()


class Command(BaseCommand):
    help = "Merge all historical onboarding CSVs into one normalized import file"

    def add_arguments(self, parser):
        parser.add_argument(
            "--output",
            default="//app/merged_onboards.csv",
            help="Output path for merged CSV (default: /app/merged_onboards.csv)",
        )
        parser.add_argument(
            "--input-dir",
            default="//app/Old Owner Submissions",
            help="Folder containing the source CSV files",
        )

    def handle(self, *args, **options):
        input_dir = options["input_dir"]
        output_path = options["output"]

        file1 = os.path.join(input_dir, "Owner Submissions __ Onboard Form - Form responses.csv")
        file2 = os.path.join(input_dir, "Rental Property Onboard  (Responses) - Form Responses 1.csv")
        file3 = os.path.join(input_dir, "Vesta_PM_Owner_Onboard_Form2026-03-25_06_44_20.csv")

        records = {}  # keyed by normalized address for dedup
        skipped = 0

        def process_file(path, field_map, label, internet_labels=None):
            nonlocal skipped
            count = 0
            with open(path, newline="", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    mapped = _map_row(row, field_map, internet_labels)
                    addr = mapped.get("property_address", "").strip()

                    # Skip rows with no address or numeric placeholder rows (Google Form artifact)
                    if not addr:
                        skipped += 1
                        continue
                    try:
                        float(addr)
                        skipped += 1
                        continue
                    except ValueError:
                        pass

                    key = _dedup_key(addr)
                    if key not in records:
                        records[key] = mapped
                        count += 1
                    # If already seen: keep existing (first-seen wins)

            self.stdout.write(f"  {label}: {count} unique new records")

        self.stdout.write("Reading source files...")
        process_file(file1, JOTFORM_MAP, "JotForm v1 (Owner Submissions)", JOTFORM_INTERNET_LABELS)
        process_file(file2, GOOGLE_MAP, "Google Form (Rental Property Onboard)")
        process_file(file3, JOTFORM_MAP_F3, "JotForm v2 (2026 form)", JOTFORM_INTERNET_LABELS)

        self.stdout.write(f"\nTotal unique properties: {len(records)}")
        self.stdout.write(f"Skipped (no address / invalid): {skipped}")

        with open(output_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=MODEL_FIELDS)
            writer.writeheader()
            for rec in records.values():
                writer.writerow(rec)

        self.stdout.write(self.style.SUCCESS(f"\nWrote {len(records)} records to {output_path}"))
        self.stdout.write("Next step: python manage.py import_historical_onboards " + output_path)
