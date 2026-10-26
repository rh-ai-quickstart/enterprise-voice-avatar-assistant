import base64
import json
import shutil
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from app import faces
from app.config import settings
from app.main import app

CATALOG = json.dumps(
    [
        {"id": "r67d1c9cac37", "name": "Jackie", "gender": "female"},
        {"id": "r9d3aaaa1111", "gender": "male"},
        {"id": "rpinned", "name": "Pinned", "voice": "bf_emma"},
        {"id": "", "name": "ignored"},
    ]
)


def _tavus(monkeypatch, catalog=CATALOG, api_key="k"):
    monkeypatch.setattr(settings, "avatar_provider", "tavus")
    monkeypatch.setattr(settings, "avatar_faces", catalog)
    monkeypatch.setattr(settings, "tavus_api_key", api_key)
    # Tavus has nothing to add unless a test says otherwise (no network in tests)
    monkeypatch.setattr(faces, "_tavus_faces", lambda face_ids, key: {})
    monkeypatch.setattr(settings, "tts_voice", "af_heart")
    monkeypatch.setattr(settings, "tts_voice_female", "af_bella")
    monkeypatch.setattr(settings, "tts_voice_male", "am_michael")
    faces.reset_cache()


def _claims(token: str) -> dict:
    payload = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))


def test_catalog_and_voice_selection(monkeypatch):
    _tavus(monkeypatch)
    items = faces.catalog()
    assert [f.id for f in items] == ["r67d1c9cac37", "r9d3aaaa1111", "rpinned"]
    assert [faces.voice_for(f) for f in items] == ["af_bella", "am_michael", "bf_emma"]
    assert items[1].name == "r9d3aaaa1111"  # no name declared: the id until Tavus fills it in
    assert faces.default_id() == "r67d1c9cac37"
    assert faces.resolve("rpinned").name == "Pinned"
    assert faces.resolve("nope") is None


def test_catalog_is_capped_at_four_faces(monkeypatch):
    many = json.dumps([{"id": f"r{n}", "name": f"Face {n}"} for n in range(6)])
    _tavus(monkeypatch, catalog=many)
    assert [f.id for f in faces.catalog()] == ["r0", "r1", "r2", "r3"]
    with TestClient(app) as client:
        assert len(client.get("/v1/voice/faces").json()["faces"]) == 4
        assert client.get("/v1/voice/token", params={"session_id": "s", "face_id": "r5"}).status_code == 400


def test_catalog_falls_back_to_the_single_face(monkeypatch):
    _tavus(monkeypatch, catalog="[]")
    monkeypatch.setattr(settings, "tavus_face_id", "rsingle")
    assert [f.id for f in faces.catalog()] == ["rsingle"]
    monkeypatch.setattr(settings, "avatar_provider", "none")
    assert faces.catalog() == []


def test_without_its_key_tavus_is_off(monkeypatch):
    """Tavus configured but no key (setup step 5 skipped): audio only, no face offered."""
    _tavus(monkeypatch, api_key=None)
    assert faces.configured_provider() == "tavus" and faces.avatar_provider() == "none"
    assert faces.catalog() == []
    with TestClient(app) as client:
        assert client.get("/v1/voice/faces").json() == {
            "provider": "none",
            "default": None,
            "faces": [],
            "providers": [],
        }
        voice = client.get("/v1/info").json()["voice"]
        assert voice["avatar_provider"] == "none" and voice["faces"] == 0
        plain = client.get("/v1/voice/token", params={"session_id": "abc", "identity": "mo"})
        assert plain.status_code == 200 and "attributes" not in _claims(plain.json()["token"])


def test_other_providers_follow_their_keys(monkeypatch):
    monkeypatch.setattr(settings, "avatar_provider", "simli")
    monkeypatch.delenv("SIMLI_API_KEY", raising=False)
    assert faces.avatar_provider() == "none"
    monkeypatch.setenv("SIMLI_API_KEY", "s")
    assert faces.avatar_provider() == "simli" and faces.catalog() == []
    monkeypatch.setattr(settings, "avatar_provider", "none")
    assert faces.avatar_provider() == "none"


def test_faces_endpoint_and_token_attribute(monkeypatch):
    _tavus(monkeypatch)
    with TestClient(app) as client:
        body = client.get("/v1/voice/faces").json()
        assert body["provider"] == "tavus" and body["default"] == "r67d1c9cac37"
        assert [(f["id"], f["voice"]) for f in body["faces"]] == [
            ("r67d1c9cac37", "af_bella"),
            ("r9d3aaaa1111", "am_michael"),
            ("rpinned", "bf_emma"),
        ]
        token = client.get(
            "/v1/voice/token", params={"session_id": "abc", "identity": "mo", "face_id": "r9d3aaaa1111"}
        ).json()
        assert token["face_id"] == "r9d3aaaa1111"
        assert _claims(token["token"])["attributes"] == {"avatar_face": "r9d3aaaa1111"}
        plain = client.get("/v1/voice/token", params={"session_id": "abc", "identity": "mo"}).json()
        assert plain["face_id"] is None and "attributes" not in _claims(plain["token"])
        unknown = client.get("/v1/voice/token", params={"session_id": "abc", "face_id": "rnope"})
        assert unknown.status_code == 400
        assert client.get("/v1/info").json()["voice"]["faces"] == 3


def test_enrichment_from_tavus_is_cached_and_tolerant(monkeypatch):
    _tavus(monkeypatch, api_key="k")
    calls = []

    def fake_tavus(face_ids, api_key):
        calls.append((tuple(face_ids), api_key))
        return {
            "r9d3aaaa1111": {
                "face_id": "r9d3aaaa1111",
                "face_name": "Nathan",
                "thumbnail_video_url": "https://cdn/n.mp4",
            },
            "r67d1c9cac37": {
                "face_id": "r67d1c9cac37",
                "face_name": "Jackie - Office",
                "thumbnail_video_url": "https://cdn/j.mp4",
            },
        }

    monkeypatch.setattr(faces, "_tavus_faces", fake_tavus)
    first = faces.enriched()
    assert first[0].name == "Jackie" and first[0].thumbnail_url == "https://cdn/j.mp4"  # declared name wins
    assert first[1].name == "Nathan" and first[1].thumbnail_url == "https://cdn/n.mp4"
    assert first[2].thumbnail_url is None
    faces.enriched()
    assert calls == [(("r67d1c9cac37", "r9d3aaaa1111", "rpinned"), "k")]

    faces.reset_cache()

    def failing(face_ids, api_key):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(faces, "_tavus_faces", failing)
    assert [f.name for f in faces.enriched()] == ["Jackie", "r9d3aaaa1111", "Pinned"]


def _make_clip(path: Path) -> None:
    """A 1.5 s solid-colour clip, encoded with PyAV so the test needs no ffmpeg binary."""
    import av
    from PIL import Image

    with av.open(str(path), "w") as container:
        stream = container.add_stream("mpeg4", rate=10)
        stream.width, stream.height, stream.pix_fmt = 64, 48, "yuv420p"
        for n in range(15):
            frame = av.VideoFrame.from_image(Image.new("RGB", (64, 48), (200, 40 + n, 40)))
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)


def test_poster_is_cut_from_the_thumbnail_and_cached(monkeypatch, tmp_path):
    _tavus(monkeypatch, api_key="k")
    monkeypatch.setattr(faces, "POSTER_DIR", tmp_path / "posters")
    faces._poster_failed.clear()
    clip = tmp_path / "clip.mp4"
    _make_clip(clip)
    monkeypatch.setattr(
        faces,
        "_tavus_faces",
        lambda ids, key: {
            "r67d1c9cac37": {"face_id": "r67d1c9cac37", "thumbnail_video_url": "https://cdn/j.mp4"}
        },
    )
    downloads = []

    def fake_download(url, target):
        downloads.append(url)
        shutil.copy(clip, target)

    monkeypatch.setattr(faces, "_download", fake_download)

    data = faces.poster("r67d1c9cac37")
    assert data and data[:2] == b"\xff\xd8"  # JPEG
    assert faces.poster("r67d1c9cac37") == data and downloads == ["https://cdn/j.mp4"]  # cached
    assert faces.poster("rpinned") is None  # no thumbnail declared or found
    with TestClient(app) as client:
        body = client.get("/v1/voice/faces").json()
        assert body["faces"][0]["poster_url"] == "/v1/voice/faces/r67d1c9cac37/poster"
        assert body["faces"][2]["poster_url"] is None
        image = client.get("/v1/voice/faces/r67d1c9cac37/poster")
        assert image.status_code == 200 and image.headers["content-type"] == "image/jpeg"
        assert client.get("/v1/voice/faces/rpinned/poster").status_code == 404


def test_poster_failure_is_remembered(monkeypatch, tmp_path):
    _tavus(monkeypatch, api_key="k")
    monkeypatch.setattr(faces, "POSTER_DIR", tmp_path / "posters")
    faces._poster_failed.clear()
    monkeypatch.setattr(
        faces,
        "_tavus_faces",
        lambda ids, key: {
            "r67d1c9cac37": {"face_id": "r67d1c9cac37", "thumbnail_video_url": "https://cdn/j.mp4"}
        },
    )
    attempts = []

    def failing(url, target):
        attempts.append(url)
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(faces, "_download", failing)
    assert faces.poster("r67d1c9cac37") is None
    assert faces.poster("r67d1c9cac37") is None
    assert attempts == ["https://cdn/j.mp4"]  # not retried within the back-off window


def test_openspline_faces_are_the_portraits_of_the_chart(monkeypatch, tmp_path):
    from PIL import Image

    portraits = tmp_path / "portraits"
    portraits.mkdir()
    for face_id in ("meera", "steve"):
        Image.new("RGB", (512, 512), "gray").save(portraits / f"{face_id}.jpg")
    monkeypatch.setattr(settings, "avatar_provider", "openspline")
    monkeypatch.setattr(settings, "avatar_portrait_dir", str(portraits))
    monkeypatch.setattr(settings, "tavus_api_key", "k")  # still in the secret: not used for these faces
    monkeypatch.setattr(
        faces, "_tavus_faces", lambda ids, key: (_ for _ in ()).throw(AssertionError("Tavus"))
    )
    monkeypatch.setattr(
        settings,
        "avatar_faces",
        json.dumps(
            [
                {"id": "meera", "name": "Meera", "gender": "female"},
                {"id": "lisa", "name": "Lisa", "gender": "female"},  # no portrait: not offered
                {"id": "steve", "name": "Steve", "gender": "male"},
                {"id": "../steve", "name": "Escape"},
            ]
        ),
    )
    monkeypatch.setattr(faces, "POSTER_DIR", tmp_path / "posters")
    monkeypatch.setattr(settings, "tts_voice_male", "am_michael")
    faces.reset_cache()

    assert [f.id for f in faces.enriched()] == ["meera", "steve"]
    assert faces.voice_for(faces.resolve("steve")) == "am_michael"
    data = faces.poster("steve")
    assert data and data[:2] == b"\xff\xd8"
    assert Image.open(faces.poster_path("steve")).size == (faces.POSTER_SIZE, faces.POSTER_SIZE)
    assert faces.poster("lisa") is None
    with TestClient(app) as client:
        body = client.get("/v1/voice/faces").json()
        assert body["provider"] == "openspline" and body["default"] == "meera"
        assert [f["poster_url"] for f in body["faces"]] == [
            "/v1/voice/faces/meera/poster",
            "/v1/voice/faces/steve/poster",
        ]
        assert client.get("/v1/voice/faces/meera/poster").status_code == 200


def _both(monkeypatch, tmp_path, tavus_key="k"):
    """OpenSpline (the default) and Tavus offered together, each with its own faces."""
    from PIL import Image

    portraits = tmp_path / "portraits"
    portraits.mkdir()
    Image.new("RGB", (512, 512), "gray").save(portraits / "meera.jpg")
    monkeypatch.setattr(settings, "avatar_provider", "none")
    monkeypatch.setattr(settings, "avatar_providers", "openspline,tavus")
    monkeypatch.setattr(settings, "avatar_portrait_dir", str(portraits))
    monkeypatch.setattr(settings, "avatar_faces", "[]")
    monkeypatch.setattr(
        settings,
        "avatar_faces_openspline",
        json.dumps([{"id": "meera", "name": "Meera", "gender": "female"}]),
    )
    monkeypatch.setattr(settings, "avatar_faces_tavus", CATALOG)
    monkeypatch.setattr(settings, "tavus_api_key", tavus_key)
    monkeypatch.setattr(faces, "_tavus_faces", lambda face_ids, key: {})
    monkeypatch.setattr(faces, "POSTER_DIR", tmp_path / "posters")
    monkeypatch.setattr(settings, "tts_voice_female", "af_bella")
    faces.reset_cache()


def test_several_avatars_each_with_its_faces(monkeypatch, tmp_path):
    _both(monkeypatch, tmp_path)
    assert faces.providers() == ["openspline", "tavus"] and faces.avatar_provider() == "openspline"
    assert [f.id for f in faces.catalog()] == ["meera"]
    assert [f.id for f in faces.catalog("tavus")] == ["r67d1c9cac37", "r9d3aaaa1111", "rpinned"]
    assert faces.resolve("meera", "tavus") is None and faces.default_id("tavus") == "r67d1c9cac37"
    with TestClient(app) as client:
        body = client.get("/v1/voice/faces").json()
        # the default provider's faces at the top level, as before; every provider in providers
        assert body["provider"] == "openspline" and [f["id"] for f in body["faces"]] == ["meera"]
        assert [(p["id"], p["name"], p["self_hosted"], p["default"]) for p in body["providers"]] == [
            ("openspline", "OpenSpline", True, "meera"),
            ("tavus", "Tavus", False, "r67d1c9cac37"),
        ]
        assert len(body["providers"][1]["faces"]) == 3
        assert client.get("/v1/info").json()["voice"]["avatar_providers"] == ["openspline", "tavus"]
        # posters of every provider's faces are served
        assert client.get("/v1/voice/faces/meera/poster").status_code == 200


def test_the_token_carries_the_chosen_avatar(monkeypatch, tmp_path):
    _both(monkeypatch, tmp_path)
    with TestClient(app) as client:
        params = {"session_id": "abc", "identity": "mo"}
        token = client.get(
            "/v1/voice/token", params={**params, "provider": "Tavus", "face_id": "r9d3aaaa1111"}
        ).json()
        assert token["provider"] == "tavus" and token["face_id"] == "r9d3aaaa1111"
        assert _claims(token["token"])["attributes"] == {
            "avatar_provider": "tavus",
            "avatar_face": "r9d3aaaa1111",
        }
        # a face of another provider, or a provider not offered, is refused
        assert (
            client.get(
                "/v1/voice/token", params={**params, "provider": "tavus", "face_id": "meera"}
            ).status_code
            == 400
        )
        assert client.get("/v1/voice/token", params={**params, "provider": "simli"}).status_code == 400
        # no provider: the default's faces apply, and the agent takes the default
        plain = client.get("/v1/voice/token", params={**params, "face_id": "meera"}).json()
        assert plain["provider"] is None and _claims(plain["token"])["attributes"] == {"avatar_face": "meera"}


def test_a_provider_without_its_key_is_not_offered(monkeypatch, tmp_path):
    _both(monkeypatch, tmp_path, tavus_key=None)
    assert faces.configured_providers() == ["openspline", "tavus"] and faces.providers() == ["openspline"]
    assert faces.catalog("tavus") == []
    with TestClient(app) as client:
        assert [p["id"] for p in client.get("/v1/voice/faces").json()["providers"]] == ["openspline"]
        assert (
            client.get("/v1/voice/token", params={"session_id": "abc", "provider": "tavus"}).status_code
            == 400
        )
    # the default without its key: the next provider becomes the default
    monkeypatch.setattr(settings, "avatar_providers", "tavus,openspline")
    assert faces.avatar_provider() == "openspline" and faces.default_id() == "meera"
