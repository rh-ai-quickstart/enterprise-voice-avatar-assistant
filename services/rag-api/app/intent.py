"""Tells a service request apart from a question, so chat and voice can file tickets in-conversation."""

import logging
import re

from . import clients
from .config import settings

log = logging.getLogger("rag.intent")

SYSTEM = (
    "You classify the latest message from an employee talking to a company assistant. Answer with exactly one word.\n"
    "REQUEST: the employee asks the company to do, order or provide something for them: equipment, software, "
    "licences, access or permissions, a password or account reset, a booking, a purchase, a repair, an HR or "
    "facilities action; also when they describe a problem and want it fixed or replaced. Saying that they want, "
    "need or would like to order something counts as asking for it.\n"
    "QUESTION: anything else: asking for information or how something works, small talk, thanks, a follow-up on an "
    "answer, or asking how to request something without actually requesting it.\n"
    "Examples: 'I need a new laptop, mine no longer boots' -> REQUEST. 'I want to order a new laptop' -> REQUEST. "
    "'Can you order me a second monitor?' -> REQUEST. 'Please log a request for a new headset' -> REQUEST. "
    "'Can I get access to the finance share?' -> REQUEST. 'How often must passwords be rotated?' -> QUESTION. "
    "'How do I request a laptop?' -> QUESTION. 'Am I eligible for a new laptop?' -> QUESTION. "
    "'Thanks!' -> QUESTION.\n"
    "When the assistant's previous message is given, it logged a request (it mentions a REQ- reference): a reply "
    "that only confirms, thanks, or tells the assistant to go ahead, place or submit it is a follow-up -> QUESTION, "
    "not a new request."
)
# A request the ticket system filed; only such a message is shown to the classifier as the previous turn
TICKET_REF_RE = re.compile(r"\bREQ-\d+")
# "Please log a request for …" and the like, which answers tell people to say: filed without asking the
# model (Llama 3.2 3B took it for a question). "How do I log a request?" does not match.
EXPLICIT_REQUEST_RE = re.compile(
    r"^\s*(?:(?:hi|hello|hey|ok|okay|yes|so|please|kindly|could you|can you|would you|will you)[\s,]+)*"
    r"(?:log|file|open|raise|submit|create|place)\s+(?:a|an)\s+(?:new\s+)?(?:service\s+|support\s+)?"
    r"(?:request|ticket)\b",
    re.IGNORECASE,
)


def detect(message: str, previous_assistant: str | None = None) -> str:
    """Return 'request' or 'question'. Any failure counts as a question so answering never breaks.
    previous_assistant is the assistant's last message: when it filed a request (it carries a REQ-
    reference), a confirmation of that request is not filed again. Any other previous message is left
    out, so an answer that only talked about a request cannot make a new one look like a follow-up."""
    if not settings.request_intent_detection:
        return "question"
    if EXPLICIT_REQUEST_RE.match(message):
        return "request"
    content = message[:2000]
    if previous_assistant and TICKET_REF_RE.search(previous_assistant):
        content = f"Assistant's previous message: {previous_assistant[:600]}\n\nEmployee's latest message: {content}"
    try:
        completion = clients.llm().chat.completions.create(
            model=settings.llm_model,
            messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": content}],
            temperature=0,
            max_tokens=4,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("intent detection failed, treating as a question: %s", exc)
        return "question"
    word = (completion.choices[0].message.content or "").strip().upper()
    return "request" if word.startswith("REQUEST") else "question"
