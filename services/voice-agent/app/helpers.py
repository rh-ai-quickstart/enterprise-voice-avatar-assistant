"""Pure helpers, kept free of LiveKit imports so they are easy to test."""

import json
import re
from typing import Any

MARKER_RE = re.compile(r"\s*\[\d{1,2}\]")
MARKDOWN_RE = re.compile(r"[*_`#]+")
BULLET_RE = re.compile(r"^\s*(?:[-*]|\d+[.)])\s+", re.MULTILINE)
SPACES_RE = re.compile(r"[ \t]{2,}")
ROOM_PREFIX = "session-"
IDENTITY_PREFIX = "user-"
# a sentence ends at . ! or ?, possibly followed by closing Markdown or a citation marker, then whitespace
SENTENCE_END_RE = re.compile(r"[.!?][*_`)\]]*\s+")


def speakable(text: str) -> str:
    """Strip citation markers and Markdown so the text reads naturally when spoken."""
    text = MARKER_RE.sub("", text)
    text = MARKDOWN_RE.sub("", text)
    text = BULLET_RE.sub("", text)
    text = SPACES_RE.sub(" ", text)
    text = re.sub(r"\s+([.,;:!?])", r"\1", text)
    return text.strip()


class SentenceBuffer:
    """Turns a token stream into speakable sentences. Text is held back until a sentence ends, so
    citation markers and Markdown are stripped whole even when they arrive split across chunks."""

    def __init__(self) -> None:
        self._buffer = ""

    def feed(self, delta: str) -> list[str]:
        """Add a piece of text; returns the complete sentences it closed, each with a trailing space."""
        self._buffer += delta
        sentences: list[str] = []
        while True:
            match = SENTENCE_END_RE.search(self._buffer)
            if not match:
                break
            sentence, self._buffer = self._buffer[: match.end()], self._buffer[match.end() :]
            spoken = speakable(sentence)
            if spoken:
                sentences.append(spoken + " ")
        return sentences

    def flush(self) -> str:
        """The remaining text, once the stream has ended."""
        spoken = speakable(self._buffer)
        self._buffer = ""
        return spoken


def session_id_from_room(room_name: str) -> str:
    """The frontend names rooms session-<id>; the RAG API session is the <id> part."""
    return room_name.removeprefix(ROOM_PREFIX)


def user_id_from_identity(identity: str | None) -> str | None:
    if not identity:
        return None
    return identity.removeprefix(IDENTITY_PREFIX)


def display_name(participant: Any) -> str | None:
    """The name the person typed in the UI (the token's display name), or None for guests, whose
    token repeats the identity (user-guest) as the name."""
    name = str(getattr(participant, "name", "") or "").strip()
    identity = str(getattr(participant, "identity", "") or "").strip()
    if not name or name == identity or name.startswith(IDENTITY_PREFIX):
        return None
    return name


def first_name(name: str | None) -> str | None:
    parts = (name or "").split()
    return parts[0] if parts else None


def greeting_for(name: str | None, generic: str, named: str) -> str:
    first = first_name(name)
    return named.format(name=first) if first else generic


def last_user_text(chat_ctx: Any) -> str:
    """Return the text of the most recent user message in a LiveKit ChatContext (or anything shaped like it)."""
    items = getattr(chat_ctx, "items", None) or getattr(chat_ctx, "messages", None) or []
    for item in reversed(list(items)):
        if getattr(item, "role", None) != "user":
            continue
        text = getattr(item, "text_content", None)
        if not text:
            content = getattr(item, "content", None)
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                text = " ".join(part for part in content if isinstance(part, str))
        if text and text.strip():
            return text.strip()
    return ""


def citations_payload(reply: dict[str, Any], question: str | None = None, kind: str = "answer") -> bytes:
    """Data-channel message for the frontend: the transcribed question, the answer, and its citations.
    kind distinguishes answers from the greeting and from outcome notices."""
    return json.dumps(
        {
            "type": "assistant.answer",
            "kind": kind,
            "session_id": reply.get("session_id"),
            "question": question or "",
            "answer": reply.get("answer", ""),
            "blocked": bool(reply.get("blocked")),
            "citations": reply.get("citations", []),
            "ticket": reply.get("ticket"),
        }
    ).encode("utf-8")


# Per-turn timings LiveKit records on each chat item (MetricsReport), in the order they happen
TURN_TIMINGS = {
    "user": ("end_of_turn_delay", "transcription_delay"),
    "assistant": ("llm_node_ttft", "llm_node_ttfs", "tts_node_ttfb", "playback_latency", "e2e_latency"),
}


def turn_timings(role: str | None, metrics: Any) -> str:
    """The recorded timings of one chat item as 'name=0.12s ...', or '' when it has none."""
    if not metrics:
        return ""
    get = metrics.get if isinstance(metrics, dict) else lambda key: getattr(metrics, key, None)
    parts = [
        f"{key}={value:.2f}s"
        for key in TURN_TIMINGS.get(str(role), ())
        if isinstance(value := get(key), int | float)
    ]
    return f"{role} " + " ".join(parts) if parts else ""
