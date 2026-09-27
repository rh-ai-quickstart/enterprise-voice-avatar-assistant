import json
from types import SimpleNamespace

from app import avatars, faces
from app.config import settings

CATALOG = json.dumps(
    [
        {"id": "r67d1c9cac37", "name": "Jackie", "gender": "female"},
        {"id": "r9d3aaaa1111", "name": "Nathan", "gender": "male"},
        {"id": "rpinned", "voice": "bf_emma", "gender": "other"},
    ]
)


def _voices(monkeypatch, catalog=CATALOG):
    monkeypatch.setattr(settings, "avatar_provider", "tavus")
    monkeypatch.setattr(settings, "tavus_api_key", "k")
    monkeypatch.setattr(settings, "avatar_faces", catalog)
    monkeypatch.setattr(settings, "tts_voice", "af_heart")
    monkeypatch.setattr(settings, "tts_voice_female", "af_bella")
    monkeypatch.setattr(settings, "tts_voice_male", "am_michael")


def test_voice_follows_the_face(monkeypatch):
    _voices(monkeypatch)
    jackie, nathan, pinned = faces.catalog()
    assert faces.voice_for(jackie) == "af_bella"
    assert faces.voice_for(nathan) == "am_michael"
    assert faces.voice_for(pinned) == "bf_emma" and pinned.gender is None
    assert faces.voice_for(None) == "af_heart"


def test_select_prefers_the_requested_catalog_face(monkeypatch):
    _voices(monkeypatch)
    assert faces.select("r9d3aaaa1111").name == "Nathan"
    assert faces.select(None).name == "Jackie"  # first entry is the default
    assert faces.select("rnope").name == "Jackie"  # unknown ids fall back to the default
    _voices(monkeypatch, catalog="[]")
    assert faces.select(None) is None  # provider settings (TAVUS_FACE_ID) apply
    assert faces.select("rany").id == "rany"  # no catalog: the browser's id is trusted


def test_without_an_avatar_there_is_no_face(monkeypatch):
    _voices(monkeypatch)
    monkeypatch.setattr(settings, "tavus_api_key", None)  # setup step 5 skipped
    assert faces.select("r9d3aaaa1111") is None and faces.select(None) is None
    assert faces.voice_for(faces.select(None)) == "af_heart"  # the default voice
    monkeypatch.setattr(settings, "avatar_provider", "none")
    assert faces.select("r9d3aaaa1111") is None


def test_catalog_is_capped_at_four_faces(monkeypatch):
    _voices(monkeypatch, catalog=json.dumps([{"id": f"r{n}"} for n in range(6)]))
    assert [f.id for f in faces.catalog()] == ["r0", "r1", "r2", "r3"]
    assert faces.select("r5").id == "r0"  # beyond the cap counts as unknown


def test_requested_face_reads_the_participant_attribute():
    assert faces.requested_face(SimpleNamespace(attributes={"avatar_face": " r1 "})) == "r1"
    assert faces.requested_face(SimpleNamespace(attributes={})) is None
    assert faces.requested_face(None) is None


def test_tavus_kwargs_use_the_session_face():
    new = avatars.tavus_kwargs("r1", "p1", "k", {"face_id": None, "pal_id": None})
    assert new == {"face_id": "r1", "api_key": "k", "pal_id": "p1"}
    old = avatars.tavus_kwargs("r1", None, "k", {"replica_id": None})
    assert old == {"replica_id": "r1", "api_key": "k"}
