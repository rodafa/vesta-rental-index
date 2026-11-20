from django.db import models


class Tag(models.Model):
    """
    Physical key tag on the key board. One tag = one ring = one hook.

    Normally assigned to a single unit. Rarely assigned to a property
    (e.g. commercial building master, package-room keys). Never both.
    """

    BOX_CHOICES = [("A", "Box A"), ("B", "Box B")]
    box = models.CharField(max_length=1, choices=BOX_CHOICES)
    number = models.PositiveSmallIntegerField()
    key_id = models.CharField(
        max_length=10,
        unique=True,
        editable=False,
        help_text='Derived from box + number, e.g. "A-038".',
    )

    unit = models.ForeignKey(
        "core.Unit",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="key_tags",
    )
    property = models.ForeignKey(
        "core.Property",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="key_tags",
    )

    ring_notes = models.TextField(
        blank=True,
        help_text='What is on the ring, e.g. "Schlage front / Kwikset back".',
    )

    STATUS_CHOICES = [
        ("on_hook", "On Hook"),
        ("checked_out", "Checked Out"),
        ("with_vendor", "With Vendor"),
        ("missing", "Missing"),
        ("retired", "Retired"),
    ]
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default="on_hook"
    )

    imported_address_raw = models.TextField(
        blank=True,
        help_text="Sheet address as imported — kept for audit.",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["box", "number"]
        constraints = [
            models.UniqueConstraint(
                fields=["box", "number"], name="keys_tag_box_number_uniq"
            ),
            models.CheckConstraint(
                condition=~(
                    models.Q(unit__isnull=False)
                    & models.Q(property__isnull=False)
                ),
                name="keys_tag_not_unit_and_property",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(number__gte=1) & models.Q(number__lte=200)
                ),
                name="keys_tag_number_range",
            ),
        ]

    def save(self, *args, **kwargs):
        self.key_id = f"{self.box}-{self.number:03d}"
        super().save(*args, **kwargs)

    def __str__(self):
        return self.key_id


class AccessNote(models.Model):
    """
    Access / lockbox note imported from an external source.

    Attached to a unit, a property, or both. Never neither.

    Property-level notes are stored once on the property (unit=NULL),
    never copied onto each unit. The two conditional unique constraints
    reflect this: one dedupes unit-level notes, the other dedupes
    property-level notes (where unit IS NULL — Postgres treats NULLs
    as distinct, so a single constraint covering both would not work).
    """

    unit = models.ForeignKey(
        "core.Unit",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="access_notes",
    )
    property = models.ForeignKey(
        "core.Property",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="access_notes",
    )

    SOURCE_CHOICES = [
        ("rentvine_unit_note", "Rentvine Unit Note"),
        ("rentvine_property_note", "Rentvine Property Note"),
        ("rentengine_internal_notes", "RentEngine Internal Notes"),
        ("rentengine_key_access", "RentEngine Key Access"),
        ("keybot", "Keybot (Slack)"),
    ]
    source = models.CharField(max_length=30, choices=SOURCE_CHOICES)

    source_ref = models.CharField(
        max_length=255,
        blank=True,
        help_text="Rentvine noteID or RentEngine unit id — for idempotent re-import.",
    )

    text = models.TextField()
    source_date = models.DateField(
        null=True,
        blank=True,
        help_text="Date the note was originally written.",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-source_date", "-created_at"]
        constraints = [
            # At least one of unit / property must be set.
            models.CheckConstraint(
                condition=(
                    models.Q(unit__isnull=False)
                    | models.Q(property__isnull=False)
                ),
                name="keys_accessnote_has_target",
            ),
            # Unit-level dedupe: source + source_ref + unit, when ref
            # is non-empty and unit is set.
            models.UniqueConstraint(
                fields=["source", "source_ref", "unit"],
                condition=(
                    ~models.Q(source_ref="") & models.Q(unit__isnull=False)
                ),
                name="keys_accessnote_unit_ref_uniq",
            ),
            # Property-level dedupe: source + source_ref + property,
            # when ref is non-empty and unit is NULL.
            models.UniqueConstraint(
                fields=["source", "source_ref", "property"],
                condition=(
                    ~models.Q(source_ref="") & models.Q(unit__isnull=True)
                ),
                name="keys_accessnote_property_ref_uniq",
            ),
        ]

    def __str__(self):
        target = self.unit or self.property or "?"
        return f"{self.get_source_display()} → {target}"


class KeyChange(models.Model):
    """
    Append-only audit log for key/tag changes.

    No update logic — rows are only ever inserted.
    """

    tag = models.ForeignKey(
        "keys.Tag",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="changes",
    )
    unit = models.ForeignKey(
        "core.Unit",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
    )
    field = models.CharField(
        max_length=50,
        help_text='Which field changed, e.g. "status", "unit".',
    )
    old_value = models.TextField(blank=True)
    new_value = models.TextField(blank=True)
    changed_by = models.CharField(
        max_length=100,
        help_text='Slack user id, dashboard username, or "import".',
    )
    reason = models.TextField(blank=True)
    changed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-changed_at"]

    def __str__(self):
        return f"{self.tag} .{self.field} → {self.new_value} ({self.changed_at:%Y-%m-%d})"


class Checkout(models.Model):
    """
    Key checkout / return record.

    Only one open checkout (returned_at IS NULL) per tag at a time.
    """

    tag = models.ForeignKey(
        "keys.Tag",
        on_delete=models.PROTECT,
        related_name="checkouts",
    )
    checked_out_by_slack_id = models.CharField(max_length=50)
    checked_out_by_name = models.CharField(max_length=150)
    checked_out_at = models.DateTimeField()
    returned_at = models.DateTimeField(null=True, blank=True)
    last_reminder_at = models.DateTimeField(null=True, blank=True)
    reminder_count = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["-checked_out_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["tag"],
                condition=models.Q(returned_at__isnull=True),
                name="keys_checkout_one_open_per_tag",
            ),
        ]

    def __str__(self):
        status = "open" if self.returned_at is None else "returned"
        return f"{self.tag} → {self.checked_out_by_name} ({status})"
