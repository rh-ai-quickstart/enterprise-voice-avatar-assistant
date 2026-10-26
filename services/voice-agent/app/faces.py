"""Avatar faces and the voice each one speaks with.

The catalog of each avatar provider (AVATAR_FACES_<PROVIDER>, a JSON list of {id, name?, gender?,
voice?}; AVATAR_FACES for the default provider when it has no list of its own) is the one the RAG
API offers in the UI. The browser's choice arrives as the participant attributes "avatar_provider"
and "avatar_face"; the voice follows the face's declared gender (TTS_VOICE_FEMALE /
TTS_VOICE_MALE) unless the face pins a voice. Tavus publishes no gender for its faces, so the
operator declares it in the chart.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from . import avatars
from .config import settings

log = logging.getLogger("voice-agent.faces")

GENDERS = ("female", "male")
MAX_FACES = 4  # the same cap the RAG API applies, so both sides offer the same list
FACE_ATTRIBUTE = "avatar_face"


@dataclass(frozen=True)
class Face:
    id: str
    name: str
    gender: str | None = None
    voice: str | None = None


def _text(value: object) -> str:
    return str(value).strip() if value is not None else ""


def parse(raw: str) -> list[Face]:
    try:
        items = json.loads(raw or "[]")
    except json.JSONDecodeError:
        log.warning("AVATAR_FACES is not valid JSON; using the configured face only")
        return []
    faces: list[Face] = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        face_id = _text(item.get("id"))
        if not face_id:
            continue
        gender = _text(item.get("gender")).lower() or None
        if gender and gender not in GENDERS:
            log.warning("face %s: gender %r is not female or male; using the default voice", face_id, gender)
            gender = None
        faces.append(
            Face(
                id=face_id,
                name=_text(item.get("name")) or face_id,
                gender=gender,
                voice=_text(item.get("voice")) or None,
            )
        )
    if len(faces) > MAX_FACES:
        log.warning("AVATAR_FACES lists %d faces; only the first %d are offered", len(faces), MAX_FACES)
        faces = faces[:MAX_FACES]
    return faces


def catalog(provider: str | None = None) -> list[Face]:
    """The faces of a provider (the default one if not named)."""
    provider = provider or avatars.provider()
    own = getattr(settings, f"avatar_faces_{provider}", "") or ""
    if own.strip():
        return parse(own)
    return parse(settings.avatar_faces) if provider == avatars.provider() else []


def voice_for(face: Face | None) -> str:
    """The OpenAI-style (Kokoro) voice for a face; the configured default when nothing is declared."""
    if face is None:
        return settings.tts_voice
    if face.voice:
        return face.voice
    if face.gender == "female":
        return settings.tts_voice_female
    if face.gender == "male":
        return settings.tts_voice_male
    return settings.tts_voice


def select(requested: str | None, provider: str | None = None) -> Face | None:
    """The face for this session with a provider (the default one if not named): the requested one
    when it is in the provider's catalog (or the catalog is empty and the id is trusted as-is),
    otherwise the first catalog entry, otherwise None so the provider settings (TAVUS_FACE_ID) apply."""
    if not avatars.active(provider):
        # No avatar, no face: the session speaks with the default voice (models.tts.voice)
        return None
    faces = catalog(provider)
    requested = _text(requested) or None
    if requested:
        match = next((face for face in faces if face.id == requested), None)
        if match:
            return match
        if not faces:
            return Face(id=requested, name=requested)
        log.warning("requested face %s is not in the catalog; using the default face", requested)
    return faces[0] if faces else None


def _attribute(participant: object, name: str) -> str | None:
    attributes = getattr(participant, "attributes", None) or {}
    try:
        return _text(attributes.get(name)) or None
    except AttributeError:
        return None


def requested_face(participant: object) -> str | None:
    """The face id the browser put in its token, if any."""
    return _attribute(participant, FACE_ATTRIBUTE)


def requested_provider(participant: object) -> str | None:
    """The avatar provider the browser put in its token, if any."""
    return _attribute(participant, avatars.PROVIDER_ATTRIBUTE)
