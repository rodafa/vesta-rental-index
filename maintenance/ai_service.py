import logging

import anthropic
from django.conf import settings

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are Vulcan, Vesta PM's internal maintenance assistant — named after the Roman god of fire "
    "and the forge. You help property management staff answer questions about work orders, vendors, "
    "maintenance procedures, and troubleshooting. Be concise, sharp, and practical. If you don't "
    "know something, say so clearly rather than guessing."
)


def handle_mention(user_text: str, thread_ts: str, channel: str) -> str:
    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    message = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_text}],
    )
    return message.content[0].text
