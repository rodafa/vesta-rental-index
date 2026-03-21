import logging

import anthropic
from django.conf import settings

from maintenance.playbook import MAINTENANCE_PLAYBOOK

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are Vulcan, Vesta PM's internal maintenance assistant — named after the Roman god of fire "
    "and the forge. You help property management staff answer questions about work orders, vendors, "
    "maintenance procedures, and troubleshooting. Always answer based on the Vesta playbook first. "
    "Be concise, sharp, and practical. If something isn't covered in the playbook, say so clearly.\n\n"
    "When answering a question:\n"
    "- If your answer comes from the Vesta playbook, start your response with ✅ Vesta Policy:\n"
    "- If your answer is not covered in the playbook and you are drawing from general knowledge, "
    "start with ⚠️ General Guidance (not in Vesta playbook):\n"
    "- If something contradicts or goes beyond the playbook, flag it clearly\n\n"
    "Always be clear about which source you're drawing from.\n\n"
    "Here is the Vesta Maintenance Playbook:\n\n"
    + MAINTENANCE_PLAYBOOK
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
