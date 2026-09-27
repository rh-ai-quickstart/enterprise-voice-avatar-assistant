import asyncio
from types import SimpleNamespace

import pytest

from app import avatars
from app.config import settings


def test_none_provider_builds_nothing(monkeypatch):
    monkeypatch.setattr(settings, "avatar_provider", "none")
    assert avatars.build() is None


def test_unknown_provider_is_rejected(monkeypatch):
    monkeypatch.setattr(settings, "avatar_provider", "hologram")
    with pytest.raises(RuntimeError):
        avatars.provider()


def test_simli_requires_credentials(monkeypatch):
    monkeypatch.setattr(settings, "avatar_provider", "simli")
    monkeypatch.setattr(settings, "simli_api_key", None)
    with pytest.raises(RuntimeError, match="SIMLI_API_KEY"):
        avatars.build()


def test_tavus_requires_face_and_key(monkeypatch):
    monkeypatch.setattr(settings, "avatar_provider", "tavus")
    for field in ("tavus_face_id", "tavus_replica_id", "tavus_api_key"):
        monkeypatch.setattr(settings, field, None)
    with pytest.raises(RuntimeError, match="TAVUS_FACE_ID"):
        avatars.build()
    monkeypatch.setattr(settings, "tavus_replica_id", "r3f4182ef554")  # old name still accepted
    with pytest.raises(RuntimeError, match="TAVUS_API_KEY"):
        avatars.build()


def test_start_kwargs_uses_public_livekit_url(monkeypatch):
    class Provider:
        async def start(self, session, *, room, livekit_url=None, livekit_api_key=None):
            pass

    class Legacy:
        async def start(self, session, *, room):
            pass

    monkeypatch.setattr(settings, "livekit_public_url", "wss://livekit.example.com")
    assert avatars.start_kwargs(Provider()) == {"livekit_url": "wss://livekit.example.com"}
    assert avatars.start_kwargs(Legacy()) == {}
    monkeypatch.setattr(settings, "livekit_public_url", None)
    assert avatars.start_kwargs(Provider()) == {}


def test_stop_ends_tavus_conversation(monkeypatch):
    monkeypatch.setattr(settings, "avatar_provider", "tavus")
    monkeypatch.setattr(settings, "tavus_api_key", "k")
    ended = []

    async def fake_end(conversation_id, api_key):
        ended.append((conversation_id, api_key))
        return 200

    monkeypatch.setattr(avatars, "_end_tavus_conversation", fake_end)
    asyncio.run(avatars.stop(SimpleNamespace(conversation_id="c123")))
    assert ended == [("c123", "k")]
    asyncio.run(avatars.stop(SimpleNamespace(conversation_id=None)))
    asyncio.run(avatars.stop(None))
    assert ended == [("c123", "k")]


def test_a_missing_key_means_audio_only(monkeypatch, caplog):
    monkeypatch.setattr(settings, "avatar_provider", "tavus")
    monkeypatch.setattr(settings, "tavus_api_key", None)
    monkeypatch.setattr(avatars, "_warned", set())
    assert avatars.missing_key() == "TAVUS_API_KEY" and not avatars.active()
    with caplog.at_level("INFO", logger="voice-agent.avatar"):
        assert asyncio.run(avatars.start(None, None)) is None
        assert asyncio.run(avatars.start(None, None)) is None
    levels = [r.levelname for r in caplog.records if "TAVUS_API_KEY is empty" in r.getMessage()]
    assert levels == ["WARNING", "INFO"]  # a warning once, then a plain line per session
    monkeypatch.setattr(settings, "tavus_api_key", "k")
    assert avatars.missing_key() is None and avatars.active()
    monkeypatch.setattr(settings, "avatar_provider", "none")
    assert avatars.missing_key() is None and not avatars.active()
