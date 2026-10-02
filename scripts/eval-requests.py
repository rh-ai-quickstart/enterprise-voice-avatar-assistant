"""Request handling on the real language model, run inside the RAG API pod by scripts/eval-requests.sh.

Two checks, with the RAG API code found in the working directory (the deployed one, or a checkout
copied next to it):
- intent: does the classifier tell service requests from questions and follow-ups?
- answers: when a message reaches the answer step, does the answer claim to have logged, filed or
  ordered something? Only the ticket path files requests, so any such claim is false.

It calls the classifier, the retrieval and the language model directly: no conversation, ticket,
knowledge gap or document is read from or written to the database.

Usage (inside the pod): python - <label> [runs per answer case]
"""

import re
import sys

from app import clients, intent, rag, retrieval
from app.config import settings

LABEL = sys.argv[1] if len(sys.argv) > 1 else "code"
RUNS = int(sys.argv[2]) if len(sys.argv) > 2 else 3

# What the ticket path says after filing a request, and what the model said instead on the demo cluster
FILED = (
    "Joe, I've logged your request REQ-000002: Replace non-booting laptop. It's a hardware request with high "
    "priority and needs approval. I'll let you know here as soon as it's decided."
)
CLAIMED = (
    "I've logged a new request for you in the IT service portal. Since you're eligible for a standard-profile "
    "device, the request will be fulfilled without further approval."
)

# (message, the assistant's previous message, expected)
INTENT_CASES = [
    ("I need a new laptop, mine no longer boots.", None, "request"),
    ("I want to order a new laptop.", None, "request"),
    ("I want to order a new laptop.", CLAIMED, "request"),  # the case seen on the demo cluster
    ("Can you order me a second monitor?", None, "request"),
    ("Please log a request for a new laptop.", None, "request"),
    ("I'd like access to the finance share.", None, "request"),
    ("My password expired, please reset it.", None, "request"),
    ("Could you install Adobe Acrobat on my laptop?", None, "request"),
    ("Book me a meeting room for Thursday afternoon.", None, "request"),
    ("My docking station is broken, I need a replacement.", None, "request"),
    ("I'd like a new headset for calls.", None, "request"),
    ("How many days of annual leave do I get?", None, "question"),
    ("How often must administrator passwords be rotated?", None, "question"),
    ("How do I request a new laptop?", None, "question"),
    ("What is the laptop replacement cycle?", None, "question"),
    ("Am I eligible for a new laptop?", None, "question"),
    ("What happens after ten failed sign-in attempts?", None, "question"),
    ("And what is the minimum length?", None, "question"),
    ("Thanks!", None, "question"),
    ("Great, thanks.", FILED, "question"),
    ("Yes, go ahead and submit it.", FILED, "question"),
    ("How long will the approval take?", FILED, "question"),
]

# (message, mode, history): what the answer step gets when the classifier says "question"
HISTORY = [
    {"role": "user", "content": "I need a new laptop, mine no longer boots."},
    {"role": "assistant", "content": FILED},
]
ANSWER_CASES = [
    ("I want to order a new laptop.", "voice", HISTORY),
    ("I want to order a new laptop.", "voice", []),
    ("Can you order me a second monitor?", "text", HISTORY),
    ("Please get me access to the HR system.", "text", []),
    ("What is the laptop replacement cycle?", "voice", HISTORY),
]

# "I've logged…", "I'll submit…", "your request has been created…"; a sentence naming the request the
# ticket path really filed (REQ-000002) is not counted
CLAIM_RE = re.compile(
    r"\b(?:I(?:'ve| have|'ll| will)|we(?:'ve| have|'ll| will))\s+(?:just\s+|now\s+|already\s+|also\s+)?"
    r"(?:(?:go|gone) ahead and\s+)?(?:log|logged|file|filed|submit|submitted|create|created|place|placed|order|"
    r"ordered|raise|raised|open|opened|book|booked)\b"
    r"|\b(?:your|the|a)\s+(?:new\s+)?(?:request|order|ticket)\b[^.]{0,40}?\b(?:has been|was|is being|is now)\s+"
    r"(?:logged|filed|submitted|created|placed|raised|opened)",
    re.IGNORECASE,
)


def claims(text: str) -> list[str]:
    sentences = re.split(r"(?<=[.!?])\s+", text)
    return [s for s in sentences if CLAIM_RE.search(s) and "REQ-000002" not in s]


def short(text: str, n: int = 150) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1] + "…"


print(f"=== intent ({LABEL} code, model {settings.llm_model}) ===")
correct = 0
for message, previous, expected in INTENT_CASES:
    got = intent.detect(message, previous)
    correct += got == expected
    after = (
        " (after a filed request)" if previous == FILED else " (after the false claim)" if previous else ""
    )
    mark = "ok  " if got == expected else "MISS"
    print(f"  {mark} {got:8s} {message}{after}" + ("" if got == expected else f"  [expected {expected}]"))
print(f"  intent: {correct}/{len(INTENT_CASES)} correct")

print(f"=== answers ({LABEL} code), {RUNS} run(s) per case ===")
total = claimed = 0
for message, mode, history in ANSWER_CASES:
    hits = retrieval.search(rag.retrieval_query(message, history))
    messages = rag.build_messages(message, hits, history, mode, None, "Joe")
    max_tokens = settings.voice_max_tokens if mode == "voice" else settings.answer_max_tokens
    where = "after a filed request" if history else "new conversation"
    print(f"  {message} ({mode}, {where})")
    for _ in range(RUNS):
        completion = clients.llm().chat.completions.create(
            model=settings.llm_model,
            messages=messages,
            temperature=settings.llm_temperature,
            max_tokens=max_tokens,
        )
        text = (completion.choices[0].message.content or "").strip()
        found = claims(text)
        total += 1
        claimed += bool(found)
        print(f"    {'CLAIM' if found else 'ok   '} {short(text)}")
        for sentence in found:
            print(f"          claim: {short(sentence, 120)}")
print(f"  answers: {claimed} of {total} claimed to have logged or ordered something")
