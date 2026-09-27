"""Avatar provider factory. Providers are imported lazily so unused ones cost nothing."""

import logging
from typing import Any

import aiohttp

from .config import settings

log = logging.getLogger("voice-agent.avatar")

PROVIDERS = ("none", "simli", "tavus", "hedra")
TAVUS_API_URL = "https://tavusapi.com/v2"
# The key each provider cannot start without; the RAG API checks the same ones before offering faces
KEYS = {"simli": "simli_api_key", "tavus": "tavus_api_key", "hedra": "hedra_api_key"}
_warned: set[str] = set()


def provider() -> str:
    name = (settings.avatar_provider or "none").strip().lower()
    if name not in PROVIDERS:
        raise RuntimeError(f"unknown avatar provider {name!r}; expected one of {PROVIDERS}")
    return name


def missing_key() -> str | None:
    """The environment variable the configured provider needs and does not have, if any."""
    attribute = KEYS.get(provider())
    return attribute.upper() if attribute and not getattr(settings, attribute) else None


def active() -> bool:
    """Whether sessions get an avatar: a provider is configured and its key is there. Without one
    (setup step 5 skipped, for example) voice sessions are audio-only with the default voice."""
    return provider() != "none" and missing_key() is None


def _require(value: str | None, name: str) -> str:
    if not value:
        raise RuntimeError(f"{name} is required for avatar provider {provider()!r}")
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


def build(face_id: str | None = None) -> Any | None:
    """Return an avatar session object for the configured provider, or None for audio only.
    face_id overrides the configured Tavus face for this session."""
    name = provider()
    if name == "none":
        return None
    if name == "simli":
        from livekit.plugins import simli

        return simli.AvatarSession(
            simli_config=simli.SimliConfig(
                api_key=_require(settings.simli_api_key, "SIMLI_API_KEY"),
                face_id=_require(settings.simli_face_id, "SIMLI_FACE_ID"),
            )
        )
    if name == "tavus":
        import inspect

        from livekit.plugins import tavus

        face = _require(face_id or settings.tavus_face_id or settings.tavus_replica_id, "TAVUS_FACE_ID")
        pal_id = settings.tavus_pal_id or settings.tavus_persona_id
        api_key = _require(settings.tavus_api_key, "TAVUS_API_KEY")
        params = inspect.signature(tavus.AvatarSession.__init__).parameters
        return tavus.AvatarSession(**tavus_kwargs(face, pal_id, api_key, params))
    if name == "hedra":
        from livekit.plugins import hedra

        return hedra.AvatarSession(
            avatar_image=_require(settings.hedra_avatar_image, "HEDRA_AVATAR_IMAGE"),
            api_key=_require(settings.hedra_api_key, "HEDRA_API_KEY"),
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


async def start(session: Any, room: Any, face_id: str | None = None) -> Any | None:
    """Build and start the avatar for this room. The avatar publishes the agent's audio and its video."""
    missing = missing_key()
    if missing:
        # Said once as a warning; every session says it plainly
        (log.warning if missing not in _warned else log.info)(
            "avatar provider %s is set but %s is empty: publishing audio only", provider(), missing
        )
        _warned.add(missing)
        return None
    avatar = build(face_id)
    if avatar is None:
        log.info("no avatar provider configured; publishing audio only")
        return None
    kwargs = start_kwargs(avatar)
    log.info(
        "starting avatar provider %s face=%s (livekit url for the provider: %s)",
        provider(),
        face_id or "configured default",
        kwargs.get("livekit_url", "default"),
    )
    await avatar.start(session, room=room, **kwargs)
    log.info("avatar provider %s started", provider())
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


async def stop(avatar: Any) -> None:
    """End the provider-side session when the room closes. The Tavus plugin never ends its
    conversation, which keeps billing minutes and, on single-stream plans, blocks the next session."""
    if avatar is None:
        return
    if provider() == "tavus":
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
