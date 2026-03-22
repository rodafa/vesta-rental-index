import logging

from ninja import Router
from ninja.errors import HttpError

from onboarding.models import OwnerOnboard
from onboarding.schemas import OwnerOnboardIn, OwnerOnboardOut
from onboarding.services.slack import post_slack_summary

logger = logging.getLogger(__name__)

router = Router(tags=["Onboarding"])


@router.post("/submit/", auth=None, response=OwnerOnboardOut)
def submit_onboard(request, payload: OwnerOnboardIn):
    if not payload.property_address.strip():
        raise HttpError(400, "property_address is required")

    instance = OwnerOnboard.objects.create(**payload.dict())

    try:
        post_slack_summary(instance)
    except Exception:
        logger.exception("Failed to post Slack summary for onboard #%d", instance.id)

    return {"status": "ok", "id": instance.id}
