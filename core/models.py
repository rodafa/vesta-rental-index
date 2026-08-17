from django.db import models

# Unit.property (ForeignKey) shadows the built-in; alias for decorator use.
_property = property


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
    property_meld_id = models.IntegerField(
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
    property_meld_id = models.IntegerField(
        unique=True, null=True, blank=True, db_index=True
    )

    rentengine_status = models.CharField(
        max_length=50,
        blank=True,
        default="",
        db_index=True,
        help_text=(
            "Raw listing status from RentEngine's /units endpoint. Free text, "
            "not a choices enum — RentEngine's vocabulary is larger than what "
            "appears in our data and can change. Current state only; not historical."
        ),
    )
    rentengine_advertised_rent = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text=(
            "Advertised listing rent from RentEngine's /units endpoint. "
            "Distinct from target_rental_rate, which comes from RentVine "
            "and is the target rent, not the advertised price. "
            "Null means unknown; never zero."
        ),
    )

    property = models.ForeignKey(
        Property, on_delete=models.CASCADE, related_name="units"
    )

    name = models.CharField(max_length=255, blank=True)

    # Address (may differ from parent property for multi-unit)
    address_line_1 = models.CharField(max_length=500, blank=True)
    unit_number = models.CharField(max_length=255, blank=True)
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

    zillow_url = models.URLField(max_length=500, blank=True, default="")

    is_active = models.BooleanField(default=True)

    raw_data = models.JSONField(default=dict, blank=True)
    source_created_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @_property
    def display_address(self):
        """
        Canonical display address at unit-level grain.

        Single-family: "123 Main St" (unit_number is empty).
        Multi-unit:    "123 Main St - Unit A" (unit_number holds the designator).
        Falls back to name if unit_number is empty but name differs from
        the base address (e.g. name="B" on a duplex).

        Dedup rules applied to name before using it as suffix:
        1. name == unit_number -> suppress (kills "588 Ray Hill Rd - D - D")
        2. name words are subset of address_line_1 words -> suppress
           (kills "555 Baldwin Ave - Baldwin Avenue 555")
        3. name == address_line_1 (exact, casefold) -> suppress
        """
        base = (
            self.address_line_1
            or self.property.address_line_1
            or str(self.property)
        )
        suffix = (self.unit_number or "").strip()
        if not suffix:
            name = (self.name or "").strip()
            if name:
                unit_num = (self.unit_number or "").strip()
                # 1. name duplicates unit_number
                if unit_num and name.casefold() == unit_num.casefold():
                    pass
                # 2. name is a word-subset of address_line_1
                elif set(name.casefold().split()) <= set(base.casefold().split()):
                    pass
                # 3. exact match
                elif name.casefold() == base.casefold():
                    pass
                else:
                    suffix = name
        if suffix:
            return f"{base} - {suffix}"
        return base

    def __str__(self):
        return self.display_address


class Tenant(models.Model):
    """Tenant contact from RentVine (contactTypeID=2)."""

    rentvine_contact_id = models.IntegerField(unique=True, db_index=True)
    name = models.CharField(max_length=255)
    first_name = models.CharField(max_length=100, blank=True)
    last_name = models.CharField(max_length=100, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=50, blank=True)
    is_active = models.BooleanField(default=True)

    raw_data = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name


class Lease(models.Model):
    """
    Lease record from RentVine. Represents a signed lease agreement
    between Vesta and one or more tenants for a specific unit.
    """

    rentvine_id = models.IntegerField(unique=True, db_index=True)

    unit = models.ForeignKey(
        Unit,
        on_delete=models.CASCADE,
        related_name="leases",
    )
    property = models.ForeignKey(
        Property,
        on_delete=models.CASCADE,
        related_name="leases",
    )
    tenants = models.ManyToManyField(Tenant, related_name="leases", blank=True)

    # Status
    PRIMARY_LEASE_STATUS_CHOICES = [
        (1, "Pending"),
        (2, "Active"),
        (3, "Closed"),
    ]
    primary_lease_status = models.IntegerField(
        choices=PRIMARY_LEASE_STATUS_CHOICES, null=True, blank=True
    )
    lease_status_id = models.IntegerField(null=True, blank=True)

    MOVE_OUT_STATUS_CHOICES = [
        (1, "None"),
        (2, "Active"),
        (3, "Completed"),
    ]
    move_out_status = models.IntegerField(
        choices=MOVE_OUT_STATUS_CHOICES, null=True, blank=True
    )

    # Key dates
    move_in_date = models.DateField(null=True, blank=True)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    closed_date = models.DateField(null=True, blank=True)
    notice_date = models.DateField(null=True, blank=True)
    expected_move_out_date = models.DateField(null=True, blank=True)
    move_out_date = models.DateField(null=True, blank=True)
    deposit_refund_due_date = models.DateField(null=True, blank=True)

    # Financial
    rent_amount = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        help_text="Gross monthly rent from active recurring charges where account.isRent",
    )
    pet_rent_amount = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        help_text="Pet rent portion (subset of rent_amount) from Pet Rent account charges",
    )
    lease_return_charge_amount = models.DecimalField(
        max_digits=10, decimal_places=2, default=0
    )

    # Insurance
    renters_insurance_company = models.CharField(max_length=255, blank=True)
    renters_insurance_policy_number = models.CharField(
        max_length=100, blank=True
    )
    renters_insurance_expiration_date = models.DateField(null=True, blank=True)

    # Move-out details
    move_out_reason_id = models.IntegerField(null=True, blank=True)
    move_out_tenant_remarks = models.TextField(blank=True)

    # Forwarding
    forwarding_name = models.CharField(max_length=255, blank=True)
    forwarding_address = models.CharField(max_length=500, blank=True)
    forwarding_city = models.CharField(max_length=100, blank=True)
    forwarding_state = models.CharField(max_length=2, blank=True)
    forwarding_postal_code = models.CharField(max_length=20, blank=True)
    forwarding_email = models.EmailField(blank=True)
    forwarding_phone = models.CharField(max_length=50, blank=True)

    # Renewal tracking
    is_renewal = models.BooleanField(default=False, db_index=True)
    previous_lease = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="renewals",
    )

    # Application link
    rentvine_application_id = models.IntegerField(null=True, blank=True)

    raw_data = models.JSONField(default=dict, blank=True)
    source_created_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Lease #{self.rentvine_id} - {self.unit}"
