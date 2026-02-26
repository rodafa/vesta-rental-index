"""
Screening API — CRUD for scorecards and screening applications.
"""

from typing import Optional

from django.shortcuts import get_object_or_404
from ninja import Router, Schema

from .models import ApplicantScorecard, ScreeningApplication, ScreeningReport
from .services import auto_populate_scorecard

router = Router(tags=["Screening"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class ScreeningReportSchema(Schema):
    id: int
    boompay_id: Optional[str] = None
    report_type: str
    decision: str
    completed_at: Optional[str] = None


class ScreeningApplicationListSchema(Schema):
    id: int
    boompay_id: Optional[str] = None
    applicant_name: str
    applicant_email: str
    status: str
    submitted_at: Optional[str] = None
    completed_at: Optional[str] = None
    has_scorecard: bool


class ScreeningApplicationDetailSchema(Schema):
    id: int
    boompay_id: Optional[str] = None
    applicant_name: str
    applicant_email: str
    status: str
    submitted_at: Optional[str] = None
    completed_at: Optional[str] = None
    unit_id: Optional[int] = None
    application_id: Optional[int] = None
    reports: list[ScreeningReportSchema]


class ScorecardListSchema(Schema):
    id: int
    screening_application_id: int
    applicant_name: str
    applicant_email: str
    unit_address: Optional[str] = None
    total_score: int
    recommendation: str
    screening_status: str
    reviewed_by: str
    auto_populated: bool
    updated_at: str


class ScorecardDetailSchema(Schema):
    id: int
    screening_application_id: int
    applicant_name: str
    total_score: int
    recommendation: str
    reviewed_by: str
    notes: str
    auto_populated: bool
    # Income & Employment
    income_ratio: str
    income_ratio_numeric: Optional[float] = None
    income_employment_verified: bool
    income_savings_verified: bool
    # Pets
    pet_status: str
    # Credit
    credit_tier: str
    credit_score_raw: Optional[int] = None
    bankruptcy_status: str
    credit_active_chargeoffs: bool
    dti_tier: str
    dti_numeric: Optional[float] = None
    # Rental History
    rental_positive_ref: bool
    rental_would_rent_again: bool
    rental_no_complaints: bool
    eviction_history: str
    rental_owes_landlord: bool
    # Legal
    legal_no_felonies_5yr: bool
    legal_nonviolent_felony_over_5yr: bool
    legal_drug_misdemeanor_3yr: bool
    legal_other_misdemeanor_3yr: int
    legal_settled_small_claims_3yr: bool
    legal_open_small_claims: bool
    legal_settled_landlord_tenant: bool
    legal_unpaid_landlord_judgment: bool
    # Application
    app_completed: bool
    app_docs_verified: bool
    app_good_communication: bool
    app_on_time_appointment: bool
    # Co-Signer
    cosigner_strength: str
    # Auto-deny
    auto_deny_false_info: bool
    auto_deny_recent_eviction: bool
    auto_deny_violent_felony: bool
    auto_deny_pet_not_disclosed: bool
    auto_deny_no_cosigner: bool
    updated_at: str


class ScorecardCreateSchema(Schema):
    screening_application_id: int


class ScorecardUpdateSchema(Schema):
    reviewed_by: Optional[str] = None
    notes: Optional[str] = None
    income_ratio: Optional[str] = None
    income_ratio_numeric: Optional[float] = None
    income_employment_verified: Optional[bool] = None
    income_savings_verified: Optional[bool] = None
    pet_status: Optional[str] = None
    credit_tier: Optional[str] = None
    credit_score_raw: Optional[int] = None
    bankruptcy_status: Optional[str] = None
    credit_active_chargeoffs: Optional[bool] = None
    dti_tier: Optional[str] = None
    dti_numeric: Optional[float] = None
    rental_positive_ref: Optional[bool] = None
    rental_would_rent_again: Optional[bool] = None
    rental_no_complaints: Optional[bool] = None
    eviction_history: Optional[str] = None
    rental_owes_landlord: Optional[bool] = None
    legal_no_felonies_5yr: Optional[bool] = None
    legal_nonviolent_felony_over_5yr: Optional[bool] = None
    legal_drug_misdemeanor_3yr: Optional[bool] = None
    legal_other_misdemeanor_3yr: Optional[int] = None
    legal_settled_small_claims_3yr: Optional[bool] = None
    legal_open_small_claims: Optional[bool] = None
    legal_settled_landlord_tenant: Optional[bool] = None
    legal_unpaid_landlord_judgment: Optional[bool] = None
    app_completed: Optional[bool] = None
    app_docs_verified: Optional[bool] = None
    app_good_communication: Optional[bool] = None
    app_on_time_appointment: Optional[bool] = None
    cosigner_strength: Optional[str] = None
    auto_deny_false_info: Optional[bool] = None
    auto_deny_recent_eviction: Optional[bool] = None
    auto_deny_violent_felony: Optional[bool] = None
    auto_deny_pet_not_disclosed: Optional[bool] = None
    auto_deny_no_cosigner: Optional[bool] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _scorecard_to_list(sc):
    app = sc.screening_application
    unit = app.unit
    return {
        "id": sc.id,
        "screening_application_id": app.id,
        "applicant_name": app.applicant_name,
        "applicant_email": app.applicant_email,
        "unit_address": (
            unit.address_line_1 if unit else None
        ),
        "total_score": sc.total_score,
        "recommendation": sc.recommendation,
        "screening_status": app.status,
        "reviewed_by": sc.reviewed_by,
        "auto_populated": sc.auto_populated,
        "updated_at": sc.updated_at.isoformat(),
    }


def _scorecard_to_detail(sc):
    app = sc.screening_application
    result = {
        "id": sc.id,
        "screening_application_id": app.id,
        "applicant_name": app.applicant_name,
        "total_score": sc.total_score,
        "recommendation": sc.recommendation,
        "reviewed_by": sc.reviewed_by,
        "notes": sc.notes,
        "auto_populated": sc.auto_populated,
        "updated_at": sc.updated_at.isoformat(),
    }
    # Flatten all scorecard fields
    for field in [
        "income_ratio", "income_ratio_numeric", "income_employment_verified",
        "income_savings_verified", "pet_status", "credit_tier", "credit_score_raw",
        "bankruptcy_status", "credit_active_chargeoffs", "dti_tier", "dti_numeric",
        "rental_positive_ref", "rental_would_rent_again", "rental_no_complaints",
        "eviction_history", "rental_owes_landlord",
        "legal_no_felonies_5yr", "legal_nonviolent_felony_over_5yr",
        "legal_drug_misdemeanor_3yr", "legal_other_misdemeanor_3yr",
        "legal_settled_small_claims_3yr", "legal_open_small_claims",
        "legal_settled_landlord_tenant", "legal_unpaid_landlord_judgment",
        "app_completed", "app_docs_verified", "app_good_communication",
        "app_on_time_appointment", "cosigner_strength",
        "auto_deny_false_info", "auto_deny_recent_eviction",
        "auto_deny_violent_felony", "auto_deny_pet_not_disclosed",
        "auto_deny_no_cosigner",
    ]:
        result[field] = getattr(sc, field)
    return result


def _app_to_list(app):
    return {
        "id": app.id,
        "boompay_id": app.boompay_id,
        "applicant_name": app.applicant_name,
        "applicant_email": app.applicant_email,
        "status": app.status,
        "submitted_at": app.submitted_at.isoformat() if app.submitted_at else None,
        "completed_at": app.completed_at.isoformat() if app.completed_at else None,
        "has_scorecard": hasattr(app, "scorecard"),
    }


def _app_to_detail(app):
    reports = []
    for r in app.reports.all():
        reports.append({
            "id": r.id,
            "boompay_id": r.boompay_id,
            "report_type": r.report_type,
            "decision": r.decision,
            "completed_at": r.completed_at.isoformat() if r.completed_at else None,
        })
    return {
        "id": app.id,
        "boompay_id": app.boompay_id,
        "applicant_name": app.applicant_name,
        "applicant_email": app.applicant_email,
        "status": app.status,
        "submitted_at": app.submitted_at.isoformat() if app.submitted_at else None,
        "completed_at": app.completed_at.isoformat() if app.completed_at else None,
        "unit_id": app.unit_id,
        "application_id": app.application_id,
        "reports": reports,
    }


# ---------------------------------------------------------------------------
# Screening Applications
# ---------------------------------------------------------------------------


@router.get("/applications/", response=list[ScreeningApplicationListSchema])
def list_applications(request, status: Optional[str] = None):
    qs = ScreeningApplication.objects.select_related("unit").prefetch_related(
        "scorecard"
    )
    if status:
        qs = qs.filter(status=status)
    qs = qs.order_by("-created_at")
    return [_app_to_list(a) for a in qs]


@router.get("/applications/{app_id}/", response=ScreeningApplicationDetailSchema)
def get_application(request, app_id: int):
    app = get_object_or_404(
        ScreeningApplication.objects.prefetch_related("reports"),
        pk=app_id,
    )
    return _app_to_detail(app)


# ---------------------------------------------------------------------------
# Scorecards
# ---------------------------------------------------------------------------


@router.get("/scorecards/", response=list[ScorecardListSchema])
def list_scorecards(
    request,
    recommendation: Optional[str] = None,
    reviewed_by: Optional[str] = None,
):
    qs = ApplicantScorecard.objects.select_related(
        "screening_application", "screening_application__unit"
    ).order_by("-updated_at")
    if recommendation:
        qs = qs.filter(recommendation=recommendation)
    if reviewed_by:
        qs = qs.filter(reviewed_by__icontains=reviewed_by)
    return [_scorecard_to_list(sc) for sc in qs]


@router.get("/scorecards/{scorecard_id}/", response=ScorecardDetailSchema)
def get_scorecard(request, scorecard_id: int):
    sc = get_object_or_404(
        ApplicantScorecard.objects.select_related("screening_application"),
        pk=scorecard_id,
    )
    return _scorecard_to_detail(sc)


@router.post("/scorecards/", response=ScorecardDetailSchema)
def create_scorecard(request, data: ScorecardCreateSchema):
    app = get_object_or_404(ScreeningApplication, pk=data.screening_application_id)
    sc = ApplicantScorecard.objects.create(screening_application=app)
    auto_populate_scorecard(sc)
    sc.refresh_from_db()
    return _scorecard_to_detail(sc)


@router.put("/scorecards/{scorecard_id}/", response=ScorecardDetailSchema)
def update_scorecard(request, scorecard_id: int, data: ScorecardUpdateSchema):
    sc = get_object_or_404(
        ApplicantScorecard.objects.select_related("screening_application"),
        pk=scorecard_id,
    )
    for field, value in data.dict(exclude_unset=True).items():
        setattr(sc, field, value)
    sc.compute_score()
    sc.save()
    return _scorecard_to_detail(sc)


@router.post(
    "/scorecards/{scorecard_id}/auto-populate/", response=ScorecardDetailSchema
)
def auto_populate(request, scorecard_id: int):
    sc = get_object_or_404(
        ApplicantScorecard.objects.select_related("screening_application"),
        pk=scorecard_id,
    )
    auto_populate_scorecard(sc)
    sc.refresh_from_db()
    return _scorecard_to_detail(sc)
