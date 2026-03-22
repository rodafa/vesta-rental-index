from django.db import models


class OwnerOnboard(models.Model):
    PLAN_CHOICES = [
        ("Builder", "Builder"),
        ("Architect", "Architect"),
    ]

    submitted_at = models.DateTimeField(auto_now_add=True)

    # Property
    property_address = models.CharField(max_length=255)
    property_city = models.CharField(max_length=100, blank=True)
    property_zip = models.CharField(max_length=20, blank=True)
    property_type = models.CharField(max_length=100, blank=True)
    bedrooms = models.CharField(max_length=20, blank=True)
    bathrooms = models.CharField(max_length=20, blank=True)
    plan = models.CharField(max_length=50, choices=PLAN_CHOICES, blank=True)

    # Owner 1
    owner1_name = models.CharField(max_length=255, blank=True)
    owner1_email = models.CharField(max_length=255, blank=True)
    owner1_phone = models.CharField(max_length=50, blank=True)
    owner1_mailing = models.CharField(max_length=255, blank=True)

    # Owner 2
    owner2_name = models.CharField(max_length=255, blank=True)
    owner2_email = models.CharField(max_length=255, blank=True)
    owner2_phone = models.CharField(max_length=50, blank=True)
    owner2_mailing = models.CharField(max_length=255, blank=True)

    # Point of Contact
    poc_name = models.CharField(max_length=255, blank=True)
    poc_email = models.CharField(max_length=255, blank=True)
    poc_phone = models.CharField(max_length=50, blank=True)

    occupied = models.CharField(max_length=50, blank=True)
    transferring_pm = models.CharField(max_length=50, blank=True)

    # HOA / Warranty
    hoa = models.CharField(max_length=50, blank=True)
    hoa_contact = models.CharField(max_length=255, blank=True)
    warranty = models.CharField(max_length=50, blank=True)
    warranty_company = models.CharField(max_length=255, blank=True)

    # Water
    water_source = models.CharField(max_length=100, blank=True)
    water_provider = models.CharField(max_length=255, blank=True)
    water_in_rent = models.CharField(max_length=50, blank=True)

    # Sewer
    sewer_system = models.CharField(max_length=100, blank=True)
    sewer_provider = models.CharField(max_length=255, blank=True)
    sewer_in_rent = models.CharField(max_length=50, blank=True)

    # Septic
    septic_age = models.CharField(max_length=50, blank=True)
    septic_last_pumped = models.CharField(max_length=50, blank=True)
    septic_bedroom_rating = models.CharField(max_length=50, blank=True)

    # Heating
    heating_type = models.CharField(max_length=100, blank=True)
    heating_fuel = models.CharField(max_length=100, blank=True)
    heating_fuel_in_rent = models.CharField(max_length=50, blank=True)
    oil_tank_location = models.CharField(max_length=255, blank=True)
    oil_company = models.CharField(max_length=255, blank=True)
    oil_tank_size = models.CharField(max_length=50, blank=True)
    gas_levels_measured = models.CharField(max_length=50, blank=True)

    # Cooling
    cooling_system = models.CharField(max_length=100, blank=True)
    air_filters = models.CharField(max_length=255, blank=True)

    # Electric
    electric_provider = models.CharField(max_length=255, blank=True)
    electric_meter = models.CharField(max_length=100, blank=True)
    electric_in_rent = models.CharField(max_length=50, blank=True)

    # Trash
    trash_arrangement = models.CharField(max_length=100, blank=True)
    trash_company = models.CharField(max_length=255, blank=True)
    trash_day = models.CharField(max_length=50, blank=True)
    trash_in_rent = models.CharField(max_length=50, blank=True)

    # Internet
    internet_options = models.CharField(max_length=255, blank=True)
    internet_in_rent = models.CharField(max_length=50, blank=True)

    # Mail / Laundry
    mail_arrangement = models.CharField(max_length=255, blank=True)
    laundry_setup = models.CharField(max_length=255, blank=True)

    # Lawn
    lawn_care_in_rent = models.CharField(max_length=50, blank=True)
    lawn_care_vendor = models.CharField(max_length=255, blank=True)

    # Fireplace
    fireplace = models.CharField(max_length=50, blank=True)
    fireplace_type = models.CharField(max_length=100, blank=True)
    fireplace_works = models.CharField(max_length=50, blank=True)
    fireplace_last_inspection = models.CharField(max_length=50, blank=True)

    # Parking
    parking_arrangement = models.CharField(max_length=255, blank=True)
    parking_capacity = models.CharField(max_length=50, blank=True)

    # Pets
    pet_policy = models.CharField(max_length=50, blank=True)
    pet_restrictions = models.CharField(max_length=255, blank=True)

    # Tenants
    tenant_names = models.CharField(max_length=255, blank=True)
    tenant_contact = models.CharField(max_length=255, blank=True)
    tenants_notified = models.CharField(max_length=50, blank=True)

    # Lease / Financial
    current_rent = models.CharField(max_length=50, blank=True)
    lease_end_date = models.CharField(max_length=50, blank=True)
    deposit_amount = models.CharField(max_length=50, blank=True)
    deposit_location = models.CharField(max_length=255, blank=True)
    section_8 = models.CharField(max_length=50, blank=True)

    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-submitted_at"]

    def __str__(self):
        return f"{self.property_address} — {self.submitted_at:%Y-%m-%d}"
