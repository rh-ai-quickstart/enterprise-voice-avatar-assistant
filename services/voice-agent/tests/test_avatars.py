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


def test_openspline_needs_no_key_and_joins_through_the_in_cluster_livekit(monkeypatch):
    from pathlib import Path

    monkeypatch.setattr(settings, "avatar_provider", "openspline")
    monkeypatch.setattr(settings, "livekit_url", "ws://livekit:7880")
    monkeypatch.setattr(settings, "livekit_public_url", "wss://livekit.example.com")
    monkeypatch.setattr(settings, "openspline_url", "http://openspline:7860")
    assert avatars.missing_key() is None
    assert avatars.active()
    avatar = avatars.build("r3f4182ef554")  # no portrait for that id: the bundled default
    assert avatar.provider == "openspline"
    assert avatar.portrait == settings.openspline_portrait
    assert avatar.url == "ws://livekit:7880"
    assert avatar.client.url == "http://openspline:7860"
    assert avatar.quality == "low"
    assert Path(avatar.portrait).is_file()
    # its start() takes no livekit_url, so the public URL is never handed to it
    assert avatars.start_kwargs(avatar) == {}


def test_openspline_portrait_follows_the_face(monkeypatch, tmp_path):
    (tmp_path / "steve.jpg").write_bytes(b"jpeg")
    monkeypatch.setattr(settings, "avatar_portrait_dir", str(tmp_path))
    assert avatars.openspline_portrait("steve") == str(tmp_path / "steve.jpg")
    assert avatars.openspline_portrait("lisa") == settings.openspline_portrait  # no file
    assert avatars.openspline_portrait(None) == settings.openspline_portrait
    (tmp_path.parent / "secret.jpg").write_bytes(b"no")
    assert avatars.openspline_portrait("../secret") == settings.openspline_portrait


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


def test_several_providers_the_first_is_the_default(monkeypatch):
    monkeypatch.setattr(settings, "avatar_provider", "tavus")  # ignored once the list is set
    monkeypatch.setattr(settings, "avatar_providers", "openspline, Tavus,none,openspline")
    monkeypatch.setattr(settings, "tavus_api_key", "k")
    assert avatars.providers() == ["openspline", "tavus"] and avatars.provider() == "openspline"
    assert avatars.available() == ["openspline", "tavus"]
    monkeypatch.setattr(settings, "avatar_providers", "openspline,hologram")
    with pytest.raises(RuntimeError):
        avatars.providers()


def test_the_session_gets_the_requested_provider_when_it_is_offered(monkeypatch, caplog):
    monkeypatch.setattr(settings, "avatar_providers", "tavus,openspline")
    monkeypatch.setattr(settings, "tavus_api_key", "k")
    assert avatars.choose("openspline") == "openspline"
    assert avatars.choose(" TAVUS ") == "tavus"
    assert avatars.choose(None) == "tavus"  # nothing asked: the default
    with caplog.at_level("WARNING", logger="voice-agent.avatar"):
        assert avatars.choose("simli") == "tavus"  # not offered here
    assert "not offered" in caplog.text
    monkeypatch.setattr(settings, "tavus_api_key", None)  # Tavus without its key drops out
    assert avatars.available() == ["openspline"] and avatars.choose(None) == "openspline"
    assert avatars.choose("tavus") == "openspline"
    monkeypatch.setattr(settings, "avatar_providers", "tavus")
    assert avatars.choose(None) == "tavus"  # nothing available: start() then says the key is missing


def test_build_and_start_take_the_session_provider(monkeypatch):
    monkeypatch.setattr(settings, "avatar_providers", "openspline,tavus")
    monkeypatch.setattr(settings, "tavus_api_key", None)
    for field in ("tavus_face_id", "tavus_replica_id"):
        monkeypatch.setattr(settings, field, None)
    assert avatars.build("meera").provider == "openspline"  # the default
    with pytest.raises(RuntimeError, match="TAVUS_FACE_ID is required for avatar provider 'tavus'"):
        avatars.build(None, "tavus")
    assert not avatars.active("tavus") and avatars.active("openspline")
    monkeypatch.setattr(avatars, "_warned", set())
    assert asyncio.run(avatars.start(None, None, name="tavus")) is None  # no key: audio only
