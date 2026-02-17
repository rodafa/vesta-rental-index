from django.db import models


class DailyUnitSnapshot(models.Model):
    """
    Daily snapshot of a unit's market state. One row per unit per day.
    Mirrors the old InventoryLog Google Sheet. Stores price, DOM, status,
    and unit details at the time of capture for permanent historical tracking.
    """

    unit = models.ForeignKey(
        "properties.Unit",
        on_delete=models.CASCADE,
        related_name="daily_snapshots",
    )
    snapshot_date = models.DateField(db_index=True)

    # Market data at time of snapshot
    listed_price = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )
    days_on_market = models.IntegerField(null=True, blank=True)

    STATUS_CHOICES = [
        ("active", "Active"),
        ("leased_pending", "Leased Pending"),
        ("occupied", "Occupied"),
        ("make_ready", "Make Ready"),
    ]
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, blank=True)

    # Unit details frozen at snapshot time
    bedrooms = models.IntegerField(null=True, blank=True)
    bathrooms = models.DecimalField(
        max_digits=3, decimal_places=1, null=True, blank=True
    )
    square_feet = models.IntegerField(null=True, blank=True)

    date_listed = models.DateField(null=True, blank=True)
    date_off_market = models.DateField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ["unit", "snapshot_date"]
        ordering = ["-snapshot_date"]
        indexes = [
            models.Index(fields=["snapshot_date", "status"]),
        ]

    def __str__(self):
        return f"{self.unit} - {self.snapshot_date}"


class DailyMarketStats(models.Model):
    """
    Aggregated daily market statistics across all active units.
    Mirrors the old DailyStats Google Sheet tab. One row per day.
    """

    snapshot_date = models.DateField(unique=True, db_index=True)

    active_unit_count = models.IntegerField(default=0)
    average_dom = models.IntegerField(default=0)
    average_price = models.DecimalField(
        max_digits=10, decimal_places=2, default=0
    )
    count_30_plus_dom = models.IntegerField(default=0)

    # Average signed lease amount for occupied units (distinct from average_price which tracks list prices)
    average_portfolio_rent = models.DecimalField(
        max_digits=10, decimal_places=2, default=0
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = "daily market stats"
        ordering = ["-snapshot_date"]

    def __str__(self):
        return f"Market Stats - {self.snapshot_date}"


class DailyLeasingSummary(models.Model):
    """
    Daily leasing activity summary per unit. Mirrors the old DailySummary
    Google Sheet. Aggregates leads, showings, missed showings, and
    applications for a single unit on a single day.
    """

    summary_date = models.DateField(db_index=True)
    unit = models.ForeignKey(
        "properties.Unit",
        on_delete=models.CASCADE,
        related_name="daily_leasing_summaries",
    )

    leads_count = models.IntegerField(default=0)
    showings_completed_count = models.IntegerField(default=0)
    showings_missed_count = models.IntegerField(default=0)
    applications_count = models.IntegerField(default=0)

    # Property info frozen at summary time for Slack messages
    property_display_name = models.CharField(max_length=500, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ["summary_date", "unit"]
        verbose_name_plural = "daily leasing summaries"
        ordering = ["-summary_date"]

    def __str__(self):
        return f"{self.unit} - {self.summary_date}"


class WeeklyLeasingSummary(models.Model):
    """
    Weekly leasing activity summary per unit. Mirrors the old
    WeeklySummary Google Sheet. Includes conversion ratios.
    """

    week_ending = models.DateField(db_index=True)
    unit = models.ForeignKey(
        "properties.Unit",
        on_delete=models.CASCADE,
        related_name="weekly_leasing_summaries",
    )

    leads_count = models.IntegerField(default=0)
    showings_completed_count = models.IntegerField(default=0)
    showings_missed_count = models.IntegerField(default=0)
    applications_count = models.IntegerField(default=0)

    # Conversion metrics
    lead_to_show_rate = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True
    )
    show_to_app_rate = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True
    )

    property_display_name = models.CharField(max_length=500, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ["week_ending", "unit"]
        verbose_name_plural = "weekly leasing summaries"
        ordering = ["-week_ending"]

    def __str__(self):
        return f"{self.unit} - week ending {self.week_ending}"


class MonthlyMarketReport(models.Model):
    """
    Monthly aggregated market report. Mirrors the old monthly Slack report.
    Averages daily stats and leasing totals over the calendar month.
    """

    report_month = models.DateField(unique=True, db_index=True)

    # Market averages (from DailyMarketStats)
    average_dom = models.IntegerField(default=0)
    average_price = models.DecimalField(
        max_digits=10, decimal_places=2, default=0
    )
    average_30_plus_dom_count = models.DecimalField(
        max_digits=5, decimal_places=1, default=0
    )

    # Leasing totals for the month
    total_leads = models.IntegerField(default=0)
    total_showings = models.IntegerField(default=0)
    total_missed_showings = models.IntegerField(default=0)
    total_applications = models.IntegerField(default=0)

    # Conversion metrics
    lead_to_show_rate = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True
    )
    show_to_app_rate = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-report_month"]

    def __str__(self):
        return f"Monthly Report - {self.report_month.strftime('%B %Y')}"


class DailySegmentStats(models.Model):
    """
    Segmented daily market statistics. One row per (date, segment_type,
    segment_value) combination. Enables comparisons like "your 3BR in 92840
    vs the portfolio average for that segment."

    Segment types include zip_code, bedrooms, property_type, portfolio, and
    price_band. New dimensions can be added without schema changes.
    """

    snapshot_date = models.DateField(db_index=True)

    SEGMENT_TYPE_CHOICES = [
        ("zip_code", "Zip Code"),
        ("bedrooms", "Bedrooms"),
        ("property_type", "Property Type"),
        ("portfolio", "Portfolio"),
        ("price_band", "Price Band"),
    ]
    segment_type = models.CharField(
        max_length=30, choices=SEGMENT_TYPE_CHOICES, db_index=True
    )
    segment_value = models.CharField(max_length=100, db_index=True)

    active_unit_count = models.IntegerField(default=0)
    average_dom = models.IntegerField(default=0)
    average_price = models.DecimalField(
        max_digits=10, decimal_places=2, default=0
    )
    count_30_plus_dom = models.IntegerField(default=0)

    # Leasing activity for this segment
    leads_count = models.IntegerField(default=0)
    showings_count = models.IntegerField(default=0)
    applications_count = models.IntegerField(default=0)

    # Conversion metrics
    lead_to_show_rate = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True
    )
    show_to_app_rate = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ["snapshot_date", "segment_type", "segment_value"]
        verbose_name_plural = "daily segment stats"
        ordering = ["-snapshot_date"]
        indexes = [
            models.Index(fields=["segment_type", "segment_value"]),
        ]

    def __str__(self):
        return f"{self.segment_type}={self.segment_value} - {self.snapshot_date}"


class PriceDrop(models.Model):
    """
    Tracks downward price changes on units over time.
    Only records drops (new_price < previous_price).
    Detected by comparing consecutive DailyUnitSnapshot records.
    """

    unit = models.ForeignKey(
        "properties.Unit",
        on_delete=models.CASCADE,
        related_name="price_drops",
    )

    previous_price = models.DecimalField(max_digits=10, decimal_places=2)
    new_price = models.DecimalField(max_digits=10, decimal_places=2)
    drop_amount = models.DecimalField(max_digits=10, decimal_places=2)
    drop_percent = models.DecimalField(max_digits=5, decimal_places=2)

    detected_date = models.DateField(db_index=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-detected_date"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(new_price__lt=models.F("previous_price")),
                name="price_drop_must_be_downward",
            ),
        ]

    def __str__(self):
        return f"Price drop ${self.drop_amount} - {self.unit}"


class ListingCycle(models.Model):
    """
    Bridges RentEngine market data to RentVine lease outcomes.
    One record per unit listing cycle — from listed to lease signed.
    """

    unit = models.ForeignKey(
        "properties.Unit",
        on_delete=models.CASCADE,
        related_name="listing_cycles",
    )

    listed_date = models.DateField(db_index=True)
    leased_date = models.DateField(null=True, blank=True)
    lease_start_date = models.DateField(null=True, blank=True)

    original_list_price = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )
    final_list_price = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )
    signed_lease_amount = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )

    total_dom = models.IntegerField(null=True, blank=True)
    total_price_drops = models.IntegerField(default=0)
    total_drop_amount = models.DecimalField(
        max_digits=10, decimal_places=2, default=0
    )

    list_to_lease_ratio = models.DecimalField(
        max_digits=5, decimal_places=4, null=True, blank=True,
        help_text="signed_lease_amount / original_list_price",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-listed_date"]
        indexes = [
            models.Index(fields=["unit", "listed_date"]),
        ]

    def save(self, *args, **kwargs):
        if self.signed_lease_amount and self.original_list_price:
            self.list_to_lease_ratio = (
                self.signed_lease_amount / self.original_list_price
            )
        super().save(*args, **kwargs)

    def __str__(self):
        return f"ListingCycle {self.unit} - listed {self.listed_date}"
