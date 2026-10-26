"""Avatar provider factory. Providers are imported lazily so unused ones cost nothing.

Several providers can be offered at once (AVATAR_PROVIDERS, the default first). Each voice session
uses the one the browser asked for, the participant attribute "avatar_provider", when it is offered
and has its key; otherwise the default.
"""

import logging
import re
from pathlib import Path
from typing import Any

import aiohttp

from .config import settings

log = logging.getLogger("voice-agent.avatar")

PROVIDERS = ("none", "openspline", "simli", "tavus", "hedra")
PROVIDER_ATTRIBUTE = "avatar_provider"  # LiveKit participant attribute carrying the chosen provider
TAVUS_API_URL = "https://tavusapi.com/v2"
# The key each provider cannot start without; the RAG API checks the same ones before offering faces.
# OpenSpline runs in the cluster and needs none.
KEYS = {"simli": "simli_api_key", "tavus": "tavus_api_key", "hedra": "hedra_api_key"}
_warned: set[str] = set()


def _checked(value: str | None) -> str:
    name = (value or "none").strip().lower()
    if name not in PROVIDERS:
        raise RuntimeError(f"unknown avatar provider {name!r}; expected one of {PROVIDERS}")
    return name


def providers() -> list[str]:
    """The configured providers in order, the default first (AVATAR_PROVIDERS, else AVATAR_PROVIDER)."""
    names: list[str] = []
    for item in (settings.avatar_providers.strip() or settings.avatar_provider or "none").split(","):
        name = _checked(item)
        if name != "none" and name not in names:
            names.append(name)
    return names


def provider() -> str:
    """The default provider: the first configured one, or none."""
    configured = providers()
    return configured[0] if configured else "none"


def missing_key(name: str | None = None) -> str | None:
    """The environment variable a provider (the default one if not named) needs and does not have."""
    attribute = KEYS.get(name or provider())
    return attribute.upper() if attribute and not getattr(settings, attribute) else None


def active(name: str | None = None) -> bool:
    """Whether a session with this provider (the default one if not named) gets an avatar: it is
    configured and its key is there. Without one (setup step 5 skipped, for example) voice sessions
    are audio-only with the default voice."""
    name = name or provider()
    return name != "none" and missing_key(name) is None


def available() -> list[str]:
    """The providers a session can use: configured, with their key."""
    return [name for name in providers() if missing_key(name) is None]


def choose(requested: str | None) -> str:
    """The provider for a session: the requested one when it is available, else the first available
    one, else the default (whose missing key start() then reports)."""
    offered = available()
    wanted = (requested or "").strip().lower()
    if wanted in offered:
        return wanted
    if wanted:
        log.warning(
            "avatar %r was requested but is not offered (%s); using the default",
            wanted,
            ", ".join(offered) or "none",
        )
    return offered[0] if offered else provider()


def _require(value: str | None, setting: str, name: str) -> str:
    if not value:
        raise RuntimeError(f"{setting} is required for avatar provider {name!r}")
    return value


def tavus_kwargs(face_id: str, pal_id: str | None, api_key: str, params: Any) -> dict[str, Any]:
    """Constructor arguments for the Tavus plugin; Tavus renamed replicas to faces and personas
    to PALs, and both plugin generations are supported."""
    if "face_id" in params:
        kwargs: dict[str, Any] = {"face_id": face_id, "api_key": api_key}
        if pal_id:
            kwargs["pal_id"] = pal_id
        return kwargs
    kwargs = {"replica_id": face_id, "api_key": api_key}
    if pal_id:
        kwargs["persona_id"] = pal_id
    return kwargs


def openspline_portrait(face_id: str | None) -> str:
    """The portrait OpenSpline animates: <face id>.jpg from the chart's portraits when the face has
    one, else the bundled default. The id comes from the browser, so only a plain name is looked up."""
    if face_id and re.fullmatch(r"[A-Za-z0-9_-]+", face_id):
        path = Path(settings.avatar_portrait_dir) / f"{face_id}.jpg"
        if path.is_file():
            return str(path)
    return settings.openspline_portrait


def build(face_id: str | None = None, name: str | None = None) -> Any | None:
    """Return an avatar session object for a provider (the default one if not named), or None for
    audio only. face_id overrides the configured Tavus face for this session, or picks OpenSpline's
    portrait."""
    name = _checked(name) if name else provider()
    if name == "none":
        return None
    if name == "openspline":
        # Self-hosted: the chart's GPU worker animates the portrait and joins the room itself
        # through the in-cluster LiveKit address this worker registers with
        from openspline import Openspline
        from openspline.integrations.livekit import AvatarSession

        quality = settings.openspline_quality
        return AvatarSession(
            openspline_portrait(face_id),
            quality=quality,
            client=Openspline(url=settings.openspline_url, quality=quality),
            url=settings.livekit_url,
            api_key=settings.livekit_api_key,
            api_secret=settings.livekit_api_secret,
        )
    if name == "simli":
        from livekit.plugins import simli

        return simli.AvatarSession(
            simli_config=simli.SimliConfig(
                api_key=_require(settings.simli_api_key, "SIMLI_API_KEY", name),
                face_id=_require(settings.simli_face_id, "SIMLI_FACE_ID", name),
            )
        )
    if name == "tavus":
        import inspect

        from livekit.plugins import tavus

        face = _require(face_id or settings.tavus_face_id or settings.tavus_replica_id, "TAVUS_FACE_ID", name)
        pal_id = settings.tavus_pal_id or settings.tavus_persona_id
        api_key = _require(settings.tavus_api_key, "TAVUS_API_KEY", name)
        params = inspect.signature(tavus.AvatarSession.__init__).parameters
        return tavus.AvatarSession(**tavus_kwargs(face, pal_id, api_key, params))
    if name == "hedra":
        from livekit.plugins import hedra

        return hedra.AvatarSession(
            avatar_image=_require(settings.hedra_avatar_image, "HEDRA_AVATAR_IMAGE", name),
            api_key=_require(settings.hedra_api_key, "HEDRA_API_KEY", name),
        )
    return None


def start_kwargs(avatar: Any) -> dict[str, Any]:
    """Extra arguments for the provider's start(): cloud providers must join the room through the
    public LiveKit URL, not the in-cluster address this worker registers with."""
    import inspect

    kwargs: dict[str, Any] = {}
    try:
        params = inspect.signature(avatar.start).parameters
    except (TypeError, ValueError):
        return kwargs
    if "livekit_url" in params and settings.livekit_public_url:
        kwargs["livekit_url"] = settings.livekit_public_url
    return kwargs


async def start(session: Any, room: Any, face_id: str | None = None, name: str | None = None) -> Any | None:
    """Build and start the avatar for this room with a provider (the default one if not named). The
    avatar publishes the agent's audio and its video."""
    name = name or provider()
    missing = missing_key(name)
    if missing:
        # Said once as a warning; every session says it plainly
        (log.warning if missing not in _warned else log.info)(
            "avatar provider %s is set but %s is empty: publishing audio only", name, missing
        )
        _warned.add(missing)
        return None
    avatar = build(face_id, name)
    if avatar is None:
        log.info("no avatar provider configured; publishing audio only")
        return None
    kwargs = start_kwargs(avatar)
    log.info(
        "starting avatar provider %s face=%s (livekit url for the provider: %s)",
        name,
        face_id or "configured default",
        kwargs.get("livekit_url", "default"),
    )
    await avatar.start(session, room=room, **kwargs)
    log.info("avatar provider %s started", name)
    return avatar


async def _end_tavus_conversation(conversation_id: str, api_key: str) -> int:
    async with (
        aiohttp.ClientSession() as http,
        http.post(
            f"{TAVUS_API_URL}/conversations/{conversation_id}/end",
            headers={"x-api-key": api_key},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp,
    ):
        return resp.status


async def stop(avatar: Any, name: str | None = None) -> None:
    """End the provider-side session when the room closes. The Tavus plugin never ends its
    conversation, which keeps billing minutes and, on single-stream plans, blocks the next session."""
    if avatar is None:
        return
    if (name or provider()) == "tavus":
        conversation_id = getattr(avatar, "conversation_id", None)
        if conversation_id and settings.tavus_api_key:
            try:
                status = await _end_tavus_conversation(conversation_id, settings.tavus_api_key)
                log.info("tavus conversation %s ended (http %s)", conversation_id, status)
            except (aiohttp.ClientError, TimeoutError, OSError) as exc:
                log.warning("could not end tavus conversation %s: %s", conversation_id, exc)
    aclose = getattr(avatar, "aclose", None)
    if callable(aclose):
        try:
            await aclose()
        except Exception as exc:  # noqa: BLE001 - provider-specific errors, never fatal at shutdown
            log.debug("avatar aclose failed: %s", exc)
