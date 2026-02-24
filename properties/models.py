from django.db import models


class Portfolio(models.Model):
    """Owner portfolio from RentVine. Groups properties under a single ownership entity."""

    rentvine_id = models.IntegerField(unique=True, db_index=True)
    name = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)
    reserve_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    additional_reserve_amount = models.DecimalField(
        max_digits=12, decimal_places=2, default=0
    )
    additional_reserve_description = models.TextField(blank=True)
    fiscal_year_end_month = models.IntegerField(null=True, blank=True)
    hold_distributions = models.BooleanField(default=False)

    slug = models.SlugField(max_length=255, unique=True, blank=True)

    raw_data = models.JSONField(default=dict, blank=True)
    source_created_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        if not self.slug:
            from django.utils.text import slugify

            base = slugify(self.name) or "portfolio"
            slug = base
            n = 1
            while Portfolio.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug = f"{base}-{n}"
                n += 1
            self.slug = slug
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class Owner(models.Model):
    """Property owner from RentVine contacts (contactTypeID=1)."""

    rentvine_contact_id = models.IntegerField(unique=True, db_index=True)
    name = models.CharField(max_length=255)
    first_name = models.CharField(max_length=100, blank=True)
    last_name = models.CharField(max_length=100, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=50, blank=True)
    is_active = models.BooleanField(default=True)

    portfolios = models.ManyToManyField(
        Portfolio, related_name="owners", blank=True
    )

    raw_data = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name


class Property(models.Model):
    """
    Canonical property record cross-referenced across RentVine and RentEngine.
    RentVine = what we manage (portfolio). RentEngine = market activity.
    """

    rentvine_id = models.IntegerField(
        unique=True, null=True, blank=True, db_index=True
    )
    rentengine_id = models.IntegerField(
        unique=True, null=True, blank=True, db_index=True
    )

    portfolio = models.ForeignKey(
        Portfolio,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="properties",
    )

    name = models.CharField(max_length=255, blank=True)

    PROPERTY_TYPE_CHOICES = [
        ("single_family", "Single Family Home"),
        ("apartment", "Apartment"),
        ("condo", "Condo"),
        ("townhouse", "Townhouse"),
        ("duplex", "Duplex"),
        ("multiplex", "Multiplex"),
        ("loft", "Loft"),
        ("mobile_home", "Mobile Home"),
        ("commercial", "Commercial"),
        ("garage", "Garage"),
    ]
    property_type = models.CharField(
        max_length=20, choices=PROPERTY_TYPE_CHOICES, blank=True
    )
    is_multi_unit = models.BooleanField(default=False)

    SERVICE_TYPE_CHOICES = [
        ("full_management", "Full Management"),
        ("leasing_only", "Leasing Only"),
        ("maintenance_only", "Maintenance Only"),
    ]
    service_type = models.CharField(
        max_length=20, choices=SERVICE_TYPE_CHOICES, default="full_management"
    )

    # Address
    street_number = models.CharField(max_length=20, blank=True)
    street_name = models.CharField(max_length=255, blank=True)
    address_line_1 = models.CharField(max_length=500, blank=True)
    address_line_2 = models.CharField(max_length=255, blank=True)
    city = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=2, blank=True)
    postal_code = models.CharField(max_length=20, blank=True)
    country = models.CharField(max_length=2, default="US")
    latitude = models.DecimalField(
        max_digits=10, decimal_places=7, null=True, blank=True
    )
    longitude = models.DecimalField(
        max_digits=10, decimal_places=7, null=True, blank=True
    )
    county = models.CharField(max_length=100, blank=True)

    year_built = models.IntegerField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    # RentVine management details
    management_fee_setting_id = models.IntegerField(null=True, blank=True)
    maintenance_limit_amount = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    reserve_amount = models.DecimalField(
        max_digits=12, decimal_places=2, default=0
    )
    date_contract_begins = models.DateField(null=True, blank=True)
    date_contract_ends = models.DateField(null=True, blank=True)
    date_insurance_expires = models.DateField(null=True, blank=True)
    date_warranty_expires = models.DateField(null=True, blank=True)

    raw_data = models.JSONField(default=dict, blank=True)
    source_created_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "properties"

    def __str__(self):
        return self.name or self.address_line_1 or f"Property #{self.pk}"


class Unit(models.Model):
    """
    Canonical unit record. Single-unit properties have one unit;
    multi-unit properties have many. Cross-referenced across both systems.
    """

    rentvine_id = models.IntegerField(
        unique=True, null=True, blank=True, db_index=True
    )
    rentengine_id = models.IntegerField(
        unique=True, null=True, blank=True, db_index=True
    )

    property = models.ForeignKey(
        Property, on_delete=models.CASCADE, related_name="units"
    )

    name = models.CharField(max_length=255, blank=True)

    # Address (may differ from parent property for multi-unit)
    address_line_1 = models.CharField(max_length=500, blank=True)
    address_line_2 = models.CharField(max_length=255, blank=True)
    city = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=2, blank=True)
    postal_code = models.CharField(max_length=20, blank=True)
    latitude = models.DecimalField(
        max_digits=10, decimal_places=7, null=True, blank=True
    )
    longitude = models.DecimalField(
        max_digits=10, decimal_places=7, null=True, blank=True
    )

    # Unit details
    bedrooms = models.IntegerField(null=True, blank=True)
    full_bathrooms = models.IntegerField(null=True, blank=True)
    half_bathrooms = models.IntegerField(null=True, blank=True)
    square_feet = models.IntegerField(null=True, blank=True)

    # Pricing
    target_rental_rate = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )
    deposit = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )

    is_active = models.BooleanField(default=True)

    # RentEngine syndication link
    multifamily_property = models.ForeignKey(
        "MultifamilyProperty",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="units",
    )

    raw_data = models.JSONField(default=dict, blank=True)
    source_created_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name or self.address_line_1 or f"Unit #{self.pk}"


class MultifamilyProperty(models.Model):
    """
    RentEngine multifamily property record used for paid advertising
    syndication (Zillow, Apartments.com, etc.).
    """

    rentengine_id = models.IntegerField(unique=True, db_index=True)
    name = models.CharField(max_length=255, blank=True)
    text_address = models.CharField(max_length=500, blank=True)

    raw_data = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "multifamily properties"

    def __str__(self):
        return self.name or self.text_address or f"MF #{self.rentengine_id}"


class Floorplan(models.Model):
    """RentEngine floorplan associated with a multifamily property for syndication."""

    rentengine_id = models.IntegerField(unique=True, db_index=True)
    multifamily_property = models.ForeignKey(
        MultifamilyProperty,
        on_delete=models.CASCADE,
        related_name="floorplans",
        null=True,
        blank=True,
    )
    name = models.CharField(max_length=255, blank=True)

    raw_data = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name or f"Floorplan #{self.rentengine_id}"
