"""LiveKit access tokens for the browser. Rooms carry the chat session id so voice and text share memory."""

import secrets
from datetime import timedelta

from livekit import api

from .config import settings


def room_for_session(session_id: str) -> str:
    """session-<id>.<random>: a room of its own for every voice start. Reusing the name right after
    "End voice" met the previous avatar still in (or rejoining) the room under the same identity,
    and the new avatar failed to start. Session ids never contain a dot (schemas.SESSION_ID)."""
    return f"session-{session_id}.{secrets.token_hex(4)}"


def mint_token(
    identity: str, room: str, name: str | None = None, attributes: dict[str, str] | None = None
) -> str:
    token = (
        api.AccessToken(settings.livekit_api_key, settings.livekit_api_secret)
        .with_identity(identity)
        .with_name(name or identity)
        .with_ttl(timedelta(seconds=settings.voice_token_ttl_seconds))
        .with_grants(api.VideoGrants(room_join=True, room=room, can_publish=True, can_subscribe=True))
    )
    if attributes:
        # Participant attributes travel in the token; the agent reads them when it joins the room
        token = token.with_attributes(attributes)
    return token.to_jwt()
