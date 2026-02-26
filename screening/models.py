from django.db import models


class ScreeningApplication(models.Model):
    """
    Tenant screening application from BoomScreen. Minimal model with
    JSONField for raw data since full BoomScreen API schema is not yet
    available. Will be refined once detailed docs are provided.

    Note: The FK to leasing.Application is intentionally nullable because
    BoomScreen screenings don't always map 1:1 to RentVine applications.
    A prospect may be screened before a formal application exists, or a
    group application may generate multiple BoomScreen screenings. When
    no direct FK link exists, the linkage should be resolved through
    unit + applicant_email matching.
    """

    boompay_id = models.CharField(
        max_length=255, unique=True, null=True, blank=True, db_index=True
    )

    # Cross-references to our domain models
    application = models.ForeignKey(
        "leasing.Application",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="screening_applications",
    )
    unit = models.ForeignKey(
        "properties.Unit",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="screening_applications",
    )

    applicant_name = models.CharField(max_length=255, blank=True)
    applicant_email = models.EmailField(blank=True)

    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("in_progress", "In Progress"),
        ("completed", "Completed"),
        ("expired", "Expired"),
    ]
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default="pending"
    )

    submitted_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    # Full API response stored as JSON until schema is finalized
    raw_data = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Screening - {self.applicant_name or self.boompay_id}"


class ScreeningReport(models.Model):
    """
    Individual screening report/check from BoomScreen. Each screening
    application can produce multiple reports (credit, criminal, etc.).
    Uses JSONField for report_data since BoomScreen schema details
    are pending.
    """

    screening_application = models.ForeignKey(
        ScreeningApplication,
        on_delete=models.CASCADE,
        related_name="reports",
    )
    boompay_id = models.CharField(
        max_length=255, unique=True, null=True, blank=True, db_index=True
    )

    REPORT_TYPE_CHOICES = [
        ("credit", "Credit Report"),
        ("criminal", "Criminal Background"),
        ("eviction", "Eviction History"),
        ("income", "Income Verification"),
        ("landlord_ref", "Landlord Reference"),
        ("identity", "Identity Verification"),
    ]
    report_type = models.CharField(
        max_length=20, choices=REPORT_TYPE_CHOICES
    )

    DECISION_CHOICES = [
        ("pass", "Pass"),
        ("fail", "Fail"),
        ("review", "Needs Review"),
        ("pending", "Pending"),
    ]
    decision = models.CharField(
        max_length=10, choices=DECISION_CHOICES, default="pending"
    )

    completed_at = models.DateTimeField(null=True, blank=True)

    # Full report payload stored as JSON
    report_data = models.JSONField(default=dict, blank=True)
    raw_data = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.get_report_type_display()} - {self.screening_application}"


class ApplicantScorecard(models.Model):
    """
    Digitized leasing scorecard. Replaces the manual spreadsheet used
    after BoomScreen results come back. Auto-populated where possible
    from ScreeningReport data; manual entry for the rest.
    """

    screening_application = models.OneToOneField(
        ScreeningApplication,
        on_delete=models.CASCADE,
        related_name="scorecard",
    )

    # ── Income & Employment (max 30) ──────────────────────────────────────

    INCOME_RATIO_CHOICES = [
        ("3x_plus", "3x+ Rent (30 pts)"),
        ("2.5x_to_3x", "2.5x-3x Rent (20 pts)"),
        ("2x_to_2.5x", "2x-2.5x Rent (10 pts)"),
        ("below_2x", "Below 2x Rent (0 pts)"),
    ]
    income_ratio = models.CharField(
        max_length=20, choices=INCOME_RATIO_CHOICES, blank=True
    )
    income_ratio_numeric = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True,
        help_text="Actual income-to-rent ratio (e.g. 3.20)",
    )
    income_employment_verified = models.BooleanField(default=False)
    income_savings_verified = models.BooleanField(default=False)

    # ── Pets & ESA (max 10) ───────────────────────────────────────────────

    PET_STATUS_CHOICES = [
        ("no_pets", "No Pets (10 pts)"),
        ("low_risk", "Low Risk Pet (7 pts)"),
        ("medium_risk", "Medium Risk Pet (4 pts)"),
        ("high_risk", "High Risk Pet (0 pts)"),
    ]
    pet_status = models.CharField(
        max_length=20, choices=PET_STATUS_CHOICES, blank=True
    )

    # ── Credit & Financial (max 25) ───────────────────────────────────────

    CREDIT_TIER_CHOICES = [
        ("excellent", "750+ (15 pts)"),
        ("good", "700-749 (12 pts)"),
        ("fair", "650-699 (8 pts)"),
        ("poor", "600-649 (4 pts)"),
        ("very_poor", "Below 600 (0 pts)"),
    ]
    credit_tier = models.CharField(
        max_length=20, choices=CREDIT_TIER_CHOICES, blank=True
    )
    credit_score_raw = models.IntegerField(null=True, blank=True)

    BANKRUPTCY_CHOICES = [
        ("none", "No Bankruptcy (5 pts)"),
        ("discharged_3plus", "Discharged 3+ yrs (3 pts)"),
        ("discharged_recent", "Discharged < 3 yrs (1 pt)"),
        ("active", "Active Bankruptcy (0 pts)"),
    ]
    bankruptcy_status = models.CharField(
        max_length=20, choices=BANKRUPTCY_CHOICES, blank=True
    )
    credit_active_chargeoffs = models.BooleanField(default=False)

    DTI_TIER_CHOICES = [
        ("low", "DTI < 30% (5 pts)"),
        ("moderate", "DTI 30-45% (3 pts)"),
        ("high", "DTI > 45% (0 pts)"),
    ]
    dti_tier = models.CharField(
        max_length=20, choices=DTI_TIER_CHOICES, blank=True
    )
    dti_numeric = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True,
        help_text="Debt-to-income ratio (e.g. 35.50)",
    )

    # ── Rental History (max 20) ───────────────────────────────────────────

    rental_positive_ref = models.BooleanField(default=False)
    rental_would_rent_again = models.BooleanField(default=False)
    rental_no_complaints = models.BooleanField(default=False)

    EVICTION_CHOICES = [
        ("none", "No Evictions (10 pts)"),
        ("old", "Eviction 5+ yrs ago (5 pts)"),
        ("recent", "Eviction < 5 yrs (0 pts)"),
    ]
    eviction_history = models.CharField(
        max_length=20, choices=EVICTION_CHOICES, blank=True
    )
    rental_owes_landlord = models.BooleanField(default=False)

    # ── Legal History (max 10) ────────────────────────────────────────────

    legal_no_felonies_5yr = models.BooleanField(default=False)
    legal_nonviolent_felony_over_5yr = models.BooleanField(default=False)
    legal_drug_misdemeanor_3yr = models.BooleanField(default=False)
    legal_other_misdemeanor_3yr = models.IntegerField(
        default=0, help_text="Count of other misdemeanors in past 3 years"
    )
    legal_settled_small_claims_3yr = models.BooleanField(default=False)
    legal_open_small_claims = models.BooleanField(default=False)
    legal_settled_landlord_tenant = models.BooleanField(default=False)
    legal_unpaid_landlord_judgment = models.BooleanField(default=False)

    # ── Application (max 17) ──────────────────────────────────────────────

    app_completed = models.BooleanField(default=False)
    app_docs_verified = models.BooleanField(default=False)
    app_good_communication = models.BooleanField(default=False)
    app_on_time_appointment = models.BooleanField(default=False)

    # ── Co-Signer (max 15) ───────────────────────────────────────────────

    COSIGNER_CHOICES = [
        ("none", "No Co-Signer (0 pts)"),
        ("weak", "Weak Co-Signer (5 pts)"),
        ("moderate", "Moderate Co-Signer (10 pts)"),
        ("strong", "Strong Co-Signer (15 pts)"),
    ]
    cosigner_strength = models.CharField(
        max_length=20, choices=COSIGNER_CHOICES, blank=True
    )

    # ── Auto-deny flags ──────────────────────────────────────────────────

    auto_deny_false_info = models.BooleanField(default=False)
    auto_deny_recent_eviction = models.BooleanField(default=False)
    auto_deny_violent_felony = models.BooleanField(default=False)
    auto_deny_pet_not_disclosed = models.BooleanField(default=False)
    auto_deny_no_cosigner = models.BooleanField(default=False)

    # ── Computed / Output ─────────────────────────────────────────────────

    total_score = models.IntegerField(default=0)

    RECOMMENDATION_CHOICES = [
        ("platinum", "Platinum (100-110+)"),
        ("strong", "Strong (80-99)"),
        ("borderline", "Borderline (60-79)"),
        ("high_risk", "High Risk (40-59)"),
        ("reject", "Reject (Below 40)"),
        ("auto_deny", "Auto-Deny"),
    ]
    recommendation = models.CharField(
        max_length=20, choices=RECOMMENDATION_CHOICES, blank=True
    )

    # ── Audit ─────────────────────────────────────────────────────────────

    reviewed_by = models.CharField(max_length=100, blank=True)
    notes = models.TextField(blank=True)
    auto_populated = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Scorecard - {self.screening_application} ({self.total_score}pts)"

    def compute_score(self):
        """
        Calculate total_score from all category fields and set recommendation tier.
        NOT called automatically on save — caller is responsible.
        """
        score = 0

        # Income & Employment (max 30)
        income_pts = {
            "3x_plus": 30, "2.5x_to_3x": 20,
            "2x_to_2.5x": 10, "below_2x": 0,
        }
        score += income_pts.get(self.income_ratio, 0)

        # Pets & ESA (max 10)
        pet_pts = {
            "no_pets": 10, "low_risk": 7,
            "medium_risk": 4, "high_risk": 0,
        }
        score += pet_pts.get(self.pet_status, 0)

        # Credit & Financial (max 25)
        credit_pts = {
            "excellent": 15, "good": 12,
            "fair": 8, "poor": 4, "very_poor": 0,
        }
        score += credit_pts.get(self.credit_tier, 0)

        bankruptcy_pts = {
            "none": 5, "discharged_3plus": 3,
            "discharged_recent": 1, "active": 0,
        }
        score += bankruptcy_pts.get(self.bankruptcy_status, 0)

        dti_pts = {"low": 5, "moderate": 3, "high": 0}
        score += dti_pts.get(self.dti_tier, 0)

        # Rental History (max 20)
        if self.rental_positive_ref:
            score += 3
        if self.rental_would_rent_again:
            score += 4
        if self.rental_no_complaints:
            score += 3

        eviction_pts = {"none": 10, "old": 5, "recent": 0}
        score += eviction_pts.get(self.eviction_history, 0)

        # Legal History (max 10)
        legal_score = 10
        if not self.legal_no_felonies_5yr:
            legal_score -= 5
        if self.legal_drug_misdemeanor_3yr:
            legal_score -= 2
        legal_score -= min(self.legal_other_misdemeanor_3yr, 3)
        if self.legal_open_small_claims:
            legal_score -= 1
        if self.legal_unpaid_landlord_judgment:
            legal_score -= 2
        score += max(legal_score, 0)

        # Application (max 17)
        if self.app_completed:
            score += 5
        if self.app_docs_verified:
            score += 5
        if self.app_good_communication:
            score += 4
        if self.app_on_time_appointment:
            score += 3

        # Co-Signer (max 15)
        cosigner_pts = {
            "none": 0, "weak": 5, "moderate": 10, "strong": 15,
        }
        score += cosigner_pts.get(self.cosigner_strength, 0)

        self.total_score = score

        # Check auto-deny flags first
        if any([
            self.auto_deny_false_info,
            self.auto_deny_recent_eviction,
            self.auto_deny_violent_felony,
            self.auto_deny_pet_not_disclosed,
            self.auto_deny_no_cosigner,
        ]):
            self.recommendation = "auto_deny"
        elif score >= 100:
            self.recommendation = "platinum"
        elif score >= 80:
            self.recommendation = "strong"
        elif score >= 60:
            self.recommendation = "borderline"
        elif score >= 40:
            self.recommendation = "high_risk"
        else:
            self.recommendation = "reject"
