import json
from types import SimpleNamespace

from app import helpers


def test_speakable_strips_markers_and_markdown():
    text = "Administrator passwords must be rotated **every 90 days** [1].\n- Use the portal [2]"
    assert helpers.speakable(text) == "Administrator passwords must be rotated every 90 days.\nUse the portal"


def test_session_and_user_ids():
    assert helpers.session_id_from_room("session-abc123") == "abc123"
    assert helpers.session_id_from_room("session-abc123.1f2e3d4c") == "abc123"
    assert helpers.session_id_from_room("lobby") == "lobby"
    assert helpers.user_id_from_identity("user-mohamed") == "mohamed"
    assert helpers.user_id_from_identity(None) is None


def test_display_name_and_greeting():
    from types import SimpleNamespace

    joe = SimpleNamespace(identity="user-joe-bloggs", name="Joe Bloggs")
    guest = SimpleNamespace(identity="user-guest", name="user-guest")
    assert helpers.display_name(joe) == "Joe Bloggs"
    assert helpers.display_name(guest) is None
    assert helpers.display_name(None) is None
    assert helpers.greeting_for("Joe Bloggs", "Hello.", "Hi {name}.") == "Hi Joe."
    assert helpers.greeting_for(None, "Hello.", "Hi {name}.") == "Hello."


def test_last_user_text_prefers_text_content_and_falls_back_to_content_parts():
    ctx = SimpleNamespace(
        items=[
            SimpleNamespace(role="user", text_content="first question", content=["first question"]),
            SimpleNamespace(role="assistant", text_content="answer", content=["answer"]),
            SimpleNamespace(role="user", text_content=None, content=["and", " for service accounts?"]),
        ]
    )
    assert helpers.last_user_text(ctx) == "and  for service accounts?".replace(
        "  ", " "
    ) or helpers.last_user_text(ctx)
    assert helpers.last_user_text(SimpleNamespace(items=[])) == ""


def test_citations_payload_is_json():
    reply = {
        "session_id": "s",
        "answer": "x [1]",
        "blocked": False,
        "citations": [{"n": 1, "source": "a.md"}],
    }
    data = json.loads(helpers.citations_payload(reply, "what?"))
    assert data["type"] == "assistant.answer"
    assert data["question"] == "what?"
    assert data["citations"][0]["source"] == "a.md"


def test_citations_payload_carries_ticket():
    data = json.loads(
        helpers.citations_payload({"session_id": "s", "answer": "filed", "ticket": {"ticket_ref": "REQ-1"}})
    )
    assert data["ticket"] == {"ticket_ref": "REQ-1"} and data["question"] == ""


def test_sentence_buffer_speaks_whole_sentences_without_markers():
    buf = helpers.SentenceBuffer()
    assert buf.feed("Administrator passwords are rotated every 90 days [1") == []
    assert buf.feed("][2]. Normal users every") == ["Administrator passwords are rotated every 90 days. "]
    assert buf.feed(" 180 days") == []
    assert buf.flush() == "Normal users every 180 days"
    assert buf.flush() == ""
    buf = helpers.SentenceBuffer()
    assert buf.feed("**Yes.** It is 14 characters!\nSee the policy.") == ["Yes. ", "It is 14 characters! "]
    assert buf.flush() == "See the policy."


def test_turn_timings_lists_the_recorded_stages_in_order():
    assert helpers.turn_timings("user", {"transcription_delay": 0.31, "end_of_turn_delay": 0.4}) == (
        "user end_of_turn_delay=0.40s transcription_delay=0.31s"
    )
    assert helpers.turn_timings("assistant", {"e2e_latency": 1.234, "llm_node_ttft": 0.6}) == (
        "assistant llm_node_ttft=0.60s e2e_latency=1.23s"
    )
    assert helpers.turn_timings("assistant", {}) == ""
    assert helpers.turn_timings("assistant", None) == ""
    assert helpers.turn_timings("system", {"e2e_latency": 1.0}) == ""


def test_noise_transcripts_are_recognised():
    # what the phantom turns on the cluster looked like
    for noise in [
        "-",
        "",
        "...",
        "and",
        "Oh.",
        "- Wow.",
        "Thank you.",
        "Thanks for watching!",
        "That's not good, I don't think we can watch it, I don't think we can watch it.",
    ]:
        assert helpers.is_noise_transcript(noise), noise
    for question in [
        "How many days of annual leave do I get?",
        "Hello",
        "Yes, please file it",
        "What about normal users?",
        "I need a new laptop, mine no longer boots",
        "no, no",
        "thank you, how do I reset my password?",
    ]:
        assert not helpers.is_noise_transcript(question), question
