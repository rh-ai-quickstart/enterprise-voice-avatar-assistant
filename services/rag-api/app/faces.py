"""Avatar faces a person can pick before a voice session, and the voice each face speaks with.

The catalog comes from the chart (AVATAR_FACES, a JSON list of {id, name?, gender?, voice?}).
Tavus publishes no gender for its faces, so the operator declares it once per face; the voice
follows from it (TTS_VOICE_FEMALE / TTS_VOICE_MALE) unless the face pins a voice. Names and
thumbnails missing from the catalog are filled in from the Tavus API when a key is available.
The voice agent applies the same catalog when it joins the room.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path

import httpx

from .config import settings

log = logging.getLogger("rag.faces")

TAVUS_API_URL = "https://tavusapi.com/v2"
GENDERS = ("female", "male")
MAX_FACES = 4  # the UI offers at most four faces
FACE_ATTRIBUTE = "avatar_face"  # LiveKit participant attribute carrying the chosen face
CACHE_SECONDS = 3600.0
RETRY_SECONDS = 300.0
# Posters: one still per face, cut from the Tavus thumbnail video (a full clip of several MB
# with its index at the end, so browsers cannot show a frame without downloading all of it).
POSTER_DIR = Path(os.environ.get("FACE_POSTER_DIR", "/tmp/face-posters"))
POSTER_SIZE = 192
POSTER_AT_SECONDS = 1.0


@dataclass(frozen=True)
class Face:
    id: str
    name: str
    gender: str | None = None
    voice: str | None = None
    thumbnail_url: str | None = None


def _text(value: object) -> str:
    return str(value).strip() if value is not None else ""


def parse(raw: str) -> list[Face]:
    try:
        items = json.loads(raw or "[]")
    except json.JSONDecodeError:
        log.warning("AVATAR_FACES is not valid JSON; no faces offered")
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
                thumbnail_url=_text(item.get("thumbnail_url")) or None,
            )
        )
    if len(faces) > MAX_FACES:
        log.warning("AVATAR_FACES lists %d faces; only the first %d are offered", len(faces), MAX_FACES)
        faces = faces[:MAX_FACES]
    return faces


# The key each avatar provider cannot start without; the voice agent checks the same ones
AVATAR_KEYS = {"tavus": "TAVUS_API_KEY", "simli": "SIMLI_API_KEY", "hedra": "HEDRA_API_KEY"}


def configured_provider() -> str:
    return (settings.avatar_provider or "none").strip().lower()


def avatar_provider() -> str:
    """The avatar in effect: the configured provider, or none when its key is missing. Voice
    sessions are then audio-only, and no face is offered."""
    provider = configured_provider()
    if provider == "tavus":
        return provider if settings.tavus_api_key else "none"
    key = AVATAR_KEYS.get(provider)
    return provider if not key or os.environ.get(key, "").strip() else "none"


def catalog() -> list[Face]:
    """Declared faces (Tavus only). Without a list, the single configured face is offered by id."""
    if avatar_provider() != "tavus":
        return []
    faces = parse(settings.avatar_faces)
    if not faces and settings.tavus_face_id:
        faces = [Face(id=settings.tavus_face_id, name=settings.tavus_face_id)]
    return faces


def voice_for(face: Face) -> str:
    if face.voice:
        return face.voice
    if face.gender == "female":
        return settings.tts_voice_female
    if face.gender == "male":
        return settings.tts_voice_male
    return settings.tts_voice


def resolve(face_id: str | None) -> Face | None:
    if not face_id:
        return None
    return next((face for face in catalog() if face.id == face_id), None)


def default_id() -> str | None:
    faces = catalog()
    return faces[0].id if faces else None


# --- Tavus enrichment (names and thumbnails) -------------------------------------------------

_lock = threading.Lock()
_cache: dict[str, object] = {}


def _tavus_faces(face_ids: list[str], api_key: str) -> dict[str, dict]:
    """Face details from Tavus keyed by id; the list endpoint accepts a comma-separated filter."""
    with httpx.Client(timeout=httpx.Timeout(15.0, connect=5.0)) as http:
        response = http.get(
            f"{TAVUS_API_URL}/faces",
            params={"face_ids": ",".join(face_ids), "verbose": "true", "limit": max(len(face_ids), 1)},
            headers={"x-api-key": api_key},
        )
        response.raise_for_status()
        body = response.json()
    items = body.get("data", body) if isinstance(body, dict) else body
    details: dict[str, dict] = {}
    for item in items if isinstance(items, list) else []:
        if isinstance(item, dict) and item.get("face_id"):
            details[str(item["face_id"])] = item
    return details


def _merge(face: Face, detail: dict | None) -> Face:
    if not detail:
        return face
    name = face.name if face.name != face.id else (_text(detail.get("face_name")) or face.name)
    thumbnail = face.thumbnail_url or _text(detail.get("thumbnail_video_url")) or None
    return replace(face, name=name, thumbnail_url=thumbnail)


def enriched() -> list[Face]:
    """The catalog with names and thumbnails from Tavus, cached; the plain catalog if Tavus is unreachable."""
    faces = catalog()
    if not faces or not settings.tavus_api_key:
        return faces
    key = tuple(face.id for face in faces)
    now = time.monotonic()
    with _lock:
        if _cache.get("key") == key and now < float(_cache.get("expires", 0.0)):
            return list(_cache["faces"])  # type: ignore[arg-type]
        try:
            details = _tavus_faces(list(key), settings.tavus_api_key)
            result = [_merge(face, details.get(face.id)) for face in faces]
            ttl = CACHE_SECONDS
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("could not read face details from Tavus: %s", exc)
            result, ttl = faces, RETRY_SECONDS
        _cache.update(key=key, faces=result, expires=now + ttl)
    return result


def reset_cache() -> None:
    with _lock:
        _cache.clear()


# --- Posters (still images for the picker) ---------------------------------------------------

_poster_lock = threading.Lock()
_poster_failed: dict[str, float] = {}


def poster_path(face_id: str) -> Path:
    return POSTER_DIR / f"{face_id}.jpg"


def poster_url(face: Face) -> str | None:
    return f"/v1/voice/faces/{face.id}/poster" if face.thumbnail_url else None


def _download(url: str, target: Path) -> None:
    with (
        httpx.Client(timeout=httpx.Timeout(180.0, connect=10.0), follow_redirects=True) as http,
        http.stream("GET", url) as response,
    ):
        response.raise_for_status()
        with target.open("wb") as out:
            for chunk in response.iter_bytes(1 << 20):
                out.write(chunk)


def _extract_frame(video: Path, target: Path, at_seconds: float = POSTER_AT_SECONDS) -> None:
    """Write a square JPEG of one frame (about `at_seconds` in) to target."""
    import av
    from PIL import Image

    image = None
    with av.open(str(video)) as container:
        stream = container.streams.video[0]
        try:
            container.seek(int(at_seconds / stream.time_base), stream=stream)
        except Exception as exc:  # noqa: BLE001 - some clips cannot seek; the first frame will do
            log.debug("seek in %s failed (%s); using the first frame", video.name, exc)
        for frame in container.decode(stream):
            image = frame.to_image()
            break
    if image is None:
        raise ValueError("no video frame decoded")
    width, height = image.size
    side = min(width, height)
    left, top = (width - side) // 2, (height - side) // 3  # faces sit above the middle of portrait clips
    image = image.crop((left, top, left + side, top + side)).resize((POSTER_SIZE, POSTER_SIZE), Image.LANCZOS)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    image.convert("RGB").save(tmp, "JPEG", quality=85)
    tmp.replace(target)


def poster(face_id: str) -> bytes | None:
    """JPEG poster for a face, generated on first use and cached on disk; None when unavailable."""
    face = next((f for f in enriched() if f.id == face_id), None)
    if face is None or not face.thumbnail_url:
        return None
    path = poster_path(face_id)
    if path.exists():
        return path.read_bytes()
    with _poster_lock:
        if path.exists():
            return path.read_bytes()
        if time.monotonic() < _poster_failed.get(face_id, 0.0):
            return None
        try:
            with tempfile.TemporaryDirectory() as tmp:
                video = Path(tmp) / "face.mp4"
                _download(face.thumbnail_url, video)
                _extract_frame(video, path)
            log.info("poster generated for face %s (%s)", face_id, face.name)
        except Exception as exc:  # noqa: BLE001 - a missing poster only costs the picture
            log.warning("no poster for face %s: %s", face_id, exc)
            _poster_failed[face_id] = time.monotonic() + RETRY_SECONDS
            return None
    return path.read_bytes()


def warm_posters() -> None:
    """Generate every poster once so the first page load does not wait for downloads."""
    for face in enriched():
        if face.thumbnail_url:
            poster(face.id)
