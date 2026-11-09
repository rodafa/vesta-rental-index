from django.conf import settings
from django.db import models


# -- Choices (plain tuples, per project convention) -----------------------

EXTRACTION_STATUS_CHOICES = [
    ("running", "Running"),
    ("succeeded", "Succeeded"),
    ("failed", "Failed"),
]

UTILITY_TYPE_CHOICES = [
    ("electric", "Electric"),
    ("water", "Water"),
    ("sewer", "Sewer"),
    ("well", "Well"),
    ("septic", "Septic"),
    ("gas", "Gas"),
    ("oil", "Oil"),
    ("propane", "Propane"),
    ("trash", "Trash"),
    ("internet", "Internet"),
    ("other", "Other"),
]

RESPONSIBILITY_CHOICES = [
    ("tenant", "Tenant"),
    ("owner", "Owner"),
    ("included_in_rent", "Included in Rent"),
    ("not_applicable", "Not Applicable"),
    ("unknown", "Unknown"),
]

BASIS_CHOICES = [
    ("stated", "Stated"),
    ("catch_all", "Catch-All"),
]

SOURCE_TYPE_CHOICES = [
    ("note", "Note"),
    ("listing_description", "Listing Description"),
]

SOURCE_LEVEL_CHOICES = [
    ("unit", "Unit"),
    ("property", "Property"),
]

PROPOSAL_STATUS_CHOICES = [
    ("pending", "Pending"),
    ("approved", "Approved"),
    ("rejected", "Rejected"),
    ("superseded", "Superseded"),
]


# -- Models ---------------------------------------------------------------

class ExtractionRun(models.Model):
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=20, choices=EXTRACTION_STATUS_CHOICES, db_index=True,
    )
    model_name = models.CharField(max_length=100)
    scope = models.TextField(blank=True)
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    notes_sent = models.PositiveIntegerField(default=0)
    notes_dropped = models.PositiveIntegerField(default=0)
    error_text = models.TextField(blank=True)
    triggered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-started_at"]

    def __str__(self):
        return f"ExtractionRun {self.pk} ({self.status})"


class UtilityProposal(models.Model):
    # -- Extraction run link --
    run = models.ForeignKey(
        ExtractionRun,
        on_delete=models.PROTECT,
        related_name="proposals",
    )

    # -- Location (Rentvine IDs, plain ints) --
    rentvine_property_id = models.PositiveIntegerField(db_index=True)
    rentvine_unit_id = models.PositiveIntegerField(db_index=True)

    # -- Utility fact --
    utility_type = models.CharField(
        max_length=20, choices=UTILITY_TYPE_CHOICES, db_index=True,
    )
    responsibility = models.CharField(
        max_length=20, choices=RESPONSIBILITY_CHOICES,
    )
    basis = models.CharField(max_length=20, choices=BASIS_CHOICES)
    provider = models.CharField(max_length=200, blank=True)
    arrangement = models.TextField(blank=True)
    extras = models.JSONField(default=dict, blank=True)

    # -- Evidence --
    evidence_quote = models.TextField()
    quote_verified = models.BooleanField(default=False)

    # -- Source --
    source_type = models.CharField(max_length=30, choices=SOURCE_TYPE_CHOICES)
    source_level = models.CharField(max_length=10, choices=SOURCE_LEVEL_CHOICES)
    source_note_id = models.PositiveIntegerField(null=True, blank=True)
    source_date = models.DateField(null=True, blank=True)
    source_text_hash = models.CharField(max_length=64, blank=True, db_index=True)

    # -- Deduplication --
    fingerprint = models.CharField(max_length=64, unique=True)

    # -- Conflict --
    has_conflict = models.BooleanField(default=False)
    conflict_note = models.TextField(blank=True)

    # -- Review --
    status = models.CharField(
        max_length=20,
        choices=PROPOSAL_STATUS_CHOICES,
        default="pending",
        db_index=True,
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_note = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["rentvine_unit_id", "utility_type"],
                name="idx_proposal_unit_utype",
            ),
            models.Index(
                fields=["status", "rentvine_property_id"],
                name="idx_proposal_status_prop",
            ),
        ]

    def __str__(self):
        return (
            f"Proposal {self.pk}: {self.utility_type} "
            f"@ unit {self.rentvine_unit_id} [{self.status}]"
        )


class PropertyUtility(models.Model):
    """Approved utility registry. One current answer per unit + utility type."""

    # -- Location (unit FK only; traverse unit.property when needed) --
    unit = models.ForeignKey(
        "core.Unit",
        on_delete=models.PROTECT,
        related_name="utilities",
    )

    # -- Utility fact --
    utility_type = models.CharField(
        max_length=20, choices=UTILITY_TYPE_CHOICES,
    )
    responsibility = models.CharField(
        max_length=20, choices=RESPONSIBILITY_CHOICES,
    )
    provider = models.CharField(max_length=200, blank=True)
    arrangement = models.TextField(blank=True)
    extras = models.JSONField(default=dict, blank=True)

    # -- Provenance --
    approved_from = models.ForeignKey(
        UtilityProposal,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["unit", "utility_type"],
                name="uq_unit_utility_type",
            ),
        ]
        ordering = ["unit", "utility_type"]

    def __str__(self):
        return (
            f"{self.get_utility_type_display()} @ unit {self.unit_id} "
            f"-> {self.responsibility}"
        )


class UnitNote(models.Model):
    """Free-text note attached to a unit, optionally scoped to a utility type."""

    unit = models.ForeignKey(
        "core.Unit",
        on_delete=models.PROTECT,
        related_name="utility_notes",
    )
    utility_type = models.CharField(
        max_length=20, choices=UTILITY_TYPE_CHOICES, blank=True,
    )
    text = models.TextField()
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        tag = self.utility_type or "general"
        return f"Note on unit {self.unit_id} [{tag}] @ {self.created_at}"
