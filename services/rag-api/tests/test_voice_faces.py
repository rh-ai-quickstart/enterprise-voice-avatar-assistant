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
        assert client.get("/v1/voice/faces").json() == {"provider": "none", "default": None, "faces": []}
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
