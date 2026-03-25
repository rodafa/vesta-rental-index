import json
import logging

import anthropic
from django.conf import settings

from onboarding.models import OwnerOnboard

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are Minerva, an intelligent assistant for Vesta Property Management. "
    "You have access to all owner onboarding submissions across the portfolio. "
    "Answer questions accurately and concisely based only on this data. "
    "If something isn't in the data say so clearly. Be helpful and direct."
)


def _serialize_onboards():
    """Return a focused JSON summary of all onboarding records."""
    records = []
    for o in OwnerOnboard.objects.all().order_by("property_address"):
        records.append({
            "id": o.id,
            "submitted_at": o.submitted_at.strftime("%Y-%m-%d"),
            "property_address": o.property_address,
            "property_city": o.property_city,
            "property_zip": o.property_zip,
            "property_type": o.property_type,
            "bedrooms": o.bedrooms,
            "bathrooms": o.bathrooms,
            "plan": o.plan,
            "owner1_name": o.owner1_name,
            "owner1_email": o.owner1_email,
            "owner1_phone": o.owner1_phone,
            "owner2_name": o.owner2_name,
            "owner2_email": o.owner2_email,
            "poc_name": o.poc_name,
            "poc_phone": o.poc_phone,
            "occupied": o.occupied,
            "transferring_pm": o.transferring_pm,
            "hoa": o.hoa,
            "warranty": o.warranty,
            "water_source": o.water_source,
            "water_in_rent": o.water_in_rent,
            "sewer_system": o.sewer_system,
            "sewer_in_rent": o.sewer_in_rent,
            "heating_type": o.heating_type,
            "heating_fuel": o.heating_fuel,
            "cooling_system": o.cooling_system,
            "electric_provider": o.electric_provider,
            "electric_in_rent": o.electric_in_rent,
            "trash_arrangement": o.trash_arrangement,
            "trash_in_rent": o.trash_in_rent,
            "internet_options": o.internet_options,
            "lawn_care_in_rent": o.lawn_care_in_rent,
            "fireplace": o.fireplace,
            "parking_arrangement": o.parking_arrangement,
            "pet_policy": o.pet_policy,
            "section_8": o.section_8,
            "tenant_names": o.tenant_names,
            "current_rent": o.current_rent,
            "lease_end_date": o.lease_end_date,
            "notes": o.notes,
        })
    return json.dumps(records, indent=2)


def handle_mention(user_text: str) -> str:
    data = _serialize_onboards()
    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": f"Onboarding data:\n{data}\n\nQuestion: {user_text}",
            }
        ],
    )
    return message.content[0].text
