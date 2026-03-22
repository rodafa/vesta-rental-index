from django.db import models


class OwnerOnboard(models.Model):
    submitted_at = models.DateTimeField(auto_now_add=True)

    # Property
    property_address = models.TextField()
    property_city = models.TextField(blank=True)
    property_zip = models.TextField(blank=True)
    property_type = models.TextField(blank=True)
    bedrooms = models.TextField(blank=True)
    bathrooms = models.TextField(blank=True)
    plan = models.TextField(blank=True)

    # Owner 1
    owner1_name = models.TextField(blank=True)
    owner1_email = models.TextField(blank=True)
    owner1_phone = models.TextField(blank=True)
    owner1_mailing = models.TextField(blank=True)

    # Owner 2
    owner2_name = models.TextField(blank=True)
    owner2_email = models.TextField(blank=True)
    owner2_phone = models.TextField(blank=True)
    owner2_mailing = models.TextField(blank=True)

    # Point of Contact
    poc_name = models.TextField(blank=True)
    poc_email = models.TextField(blank=True)
    poc_phone = models.TextField(blank=True)

    occupied = models.TextField(blank=True)
    transferring_pm = models.TextField(blank=True)

    # HOA / Warranty
    hoa = models.TextField(blank=True)
    hoa_contact = models.TextField(blank=True)
    warranty = models.TextField(blank=True)
    warranty_company = models.TextField(blank=True)

    # Water
    water_source = models.TextField(blank=True)
    water_provider = models.TextField(blank=True)
    water_in_rent = models.TextField(blank=True)

    # Sewer
    sewer_system = models.TextField(blank=True)
    sewer_provider = models.TextField(blank=True)
    sewer_in_rent = models.TextField(blank=True)

    # Septic
    septic_age = models.TextField(blank=True)
    septic_last_pumped = models.TextField(blank=True)
    septic_bedroom_rating = models.TextField(blank=True)

    # Heating
    heating_type = models.TextField(blank=True)
    heating_fuel = models.TextField(blank=True)
    heating_fuel_in_rent = models.TextField(blank=True)
    oil_tank_location = models.TextField(blank=True)
    oil_company = models.TextField(blank=True)
    oil_tank_size = models.TextField(blank=True)
    gas_levels_measured = models.TextField(blank=True)

    # Cooling
    cooling_system = models.TextField(blank=True)
    air_filters = models.TextField(blank=True)

    # Electric
    electric_provider = models.TextField(blank=True)
    electric_meter = models.TextField(blank=True)
    electric_in_rent = models.TextField(blank=True)

    # Trash
    trash_arrangement = models.TextField(blank=True)
    trash_company = models.TextField(blank=True)
    trash_day = models.TextField(blank=True)
    trash_in_rent = models.TextField(blank=True)

    # Internet
    internet_options = models.TextField(blank=True)
    internet_in_rent = models.TextField(blank=True)

    # Mail / Laundry
    mail_arrangement = models.TextField(blank=True)
    laundry_setup = models.TextField(blank=True)

    # Lawn
    lawn_care_in_rent = models.TextField(blank=True)
    lawn_care_vendor = models.TextField(blank=True)

    # Fireplace
    fireplace = models.TextField(blank=True)
    fireplace_type = models.TextField(blank=True)
    fireplace_works = models.TextField(blank=True)
    fireplace_last_inspection = models.TextField(blank=True)

    # Parking
    parking_arrangement = models.TextField(blank=True)
    parking_capacity = models.TextField(blank=True)

    # Pets
    pet_policy = models.TextField(blank=True)
    pet_restrictions = models.TextField(blank=True)

    # Tenants
    tenant_names = models.TextField(blank=True)
    tenant_contact = models.TextField(blank=True)
    tenants_notified = models.TextField(blank=True)

    # Lease / Financial
    current_rent = models.TextField(blank=True)
    lease_end_date = models.TextField(blank=True)
    deposit_amount = models.TextField(blank=True)
    deposit_location = models.TextField(blank=True)
    section_8 = models.TextField(blank=True)

    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-submitted_at"]

    def __str__(self):
        return f"{self.property_address} — {self.submitted_at:%Y-%m-%d}"
