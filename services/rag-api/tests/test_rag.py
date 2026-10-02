from app import rag
from app.retrieval import Hit


def hits():
    return [
        Hit(
            doc_id="d1",
            source="password-policy.md",
            text="Standard user passwords must be rotated every 180 days.",
            score=0.8,
            page=None,
        ),
        Hit(
            doc_id="d1",
            source="password-policy.md",
            text="Administrator passwords must be rotated every 90 days.",
            score=0.75,
            page=2,
        ),
    ]


def test_context_is_numbered_with_sources():
    context = rag.build_context(hits())
    assert context.startswith("[1] (source: password-policy.md)")
    assert "[2] (source: password-policy.md, page 2)" in context


def test_messages_include_history_and_voice_style():
    messages = rag.build_messages(
        "How often?", hits(), [{"role": "user", "content": "hi"}], "voice", {"team": "IT"}
    )
    assert messages[0]["role"] == "system"
    assert "spoken aloud" in messages[0]["content"]
    assert "team: IT" in messages[0]["content"]
    assert messages[1] == {"role": "user", "content": "hi"}
    assert messages[-1] == {"role": "user", "content": "How often?"}


def test_messages_name_the_person_when_known():
    named = rag.build_messages("How often?", hits(), [], "text", None, "Joe Bloggs")
    assert "You are talking to Joe Bloggs. Address them as Joe" in named[0]["content"]
    anonymous = rag.build_messages("How often?", hits(), [], "text", None, None)
    assert "You are talking to" not in anonymous[0]["content"]
    assert rag.first_name("  Joe Bloggs ") == "Joe" and rag.first_name("   ") is None


def test_every_answer_forbids_claiming_actions(monkeypatch):
    from app.config import NO_ACTIONS, settings

    for mode in ("text", "voice"):
        assert NO_ACTIONS.strip() in rag.build_messages("Order me a laptop", hits(), [], mode)[0]["content"]
    # a deployment that replaces the system prompt keeps the rule
    monkeypatch.setattr(settings, "system_prompt", "You are {assistant_name}.")
    assert NO_ACTIONS.strip() in rag.build_messages("Order me a laptop", hits(), [], "text")[0]["content"]


def test_sentences_join_back_into_the_text():
    text = "Laptops are replaced every 36 months [1]. Ask the desk! Why? Because"
    assert rag.sentences(text) == [
        "Laptops are replaced every 36 months [1]. ",
        "Ask the desk! ",
        "Why? ",
        "Because",
    ]
    assert "".join(rag.sentences(text)) == text


def test_an_answer_that_claims_an_action_is_cut_there():
    known = {"REQ-000002"}
    invented = (
        "Laptops are replaced every 36 months [1]. Joe, I've logged your request REQ-000003: Monitor. "
        "It needs approval."
    )
    assert (
        rag.drop_false_claims(invented, known) == "Laptops are replaced every 36 months [1]. " + rag.NO_CLAIM
    )
    for claim in (
        "I've logged a new request for you in the IT service portal.",
        "I'll go ahead and log your request in the HR system.",
        "Let me order that monitor for you.",
        "I'm going to submit it now.",
    ):
        assert rag.drop_false_claims(claim, known) == rag.NO_CLAIM, claim


def test_true_references_and_plain_answers_are_kept():
    known = {"REQ-000002"}
    for text in (
        "I've logged your request REQ-000002 and it is waiting for approval [1].",
        "I can't log requests myself. Laptops are replaced every 36 months [1].",
        "I'll let you know here as soon as it's decided.",
        "You can order a laptop through the IT service portal [2].",
    ):
        assert rag.drop_false_claims(text, known) == text, text


def test_citation_markers_are_extracted_within_range():
    assert rag.cited_numbers("Every 90 days [2]. Also [1][7].", max_n=2) == {1, 2}


def test_retrieval_query_expands_short_followups():
    history = [
        {"role": "user", "content": "How often must admin passwords be rotated?"},
        {"role": "assistant", "content": "90 days"},
    ]
    assert (
        rag.retrieval_query("And for service accounts?", history)
        == "How often must admin passwords be rotated? And for service accounts?"
    )
    assert rag.retrieval_query("And for service accounts?", []) == "And for service accounts?"
    long = "What is the exact procedure for resetting a forgotten password when the portal is down?"
    assert rag.retrieval_query(long, history) == long


def test_the_text_answer_keeps_requests_this_conversation_filed(monkeypatch):
    from types import SimpleNamespace

    from app import guardrails, intent, knowledge_gaps, memory, retrieval
    from app.guardrails import Verdict
    from app.schemas import ChatRequest

    filed = "Joe, I've logged your request REQ-000002: Replace laptop. It needs approval."
    monkeypatch.setattr(memory, "ensure_conversation", lambda *a, **k: None)
    monkeypatch.setattr(
        memory,
        "history",
        lambda *a, **k: [
            {"role": "user", "content": "My laptop broke"},
            {"role": "assistant", "content": filed},
        ],
    )
    monkeypatch.setattr(memory, "append", lambda *a, **k: None)
    monkeypatch.setattr(knowledge_gaps, "record", lambda *a, **k: None)
    monkeypatch.setattr(guardrails, "check_input", lambda text: Verdict(True, "none"))
    monkeypatch.setattr(guardrails, "check_output", lambda user, answer: Verdict(True, "none"))
    monkeypatch.setattr(intent, "detect", lambda message, previous=None: "question")
    monkeypatch.setattr(retrieval, "search", lambda *a, **k: [])
    replies = iter(
        [
            "I've logged your request REQ-000002 and it is waiting for approval.",
            "Joe, I've logged your request REQ-000003: Monitor.",
        ]
    )

    def create(**kwargs):
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=next(replies)))])

    llm = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr(rag.clients, "llm", lambda: llm)
    first = rag.answer(ChatRequest(message="How long will the approval take?", session_id="s1"))
    assert first.answer == "I've logged your request REQ-000002 and it is waiting for approval."
    second = rag.answer(ChatRequest(message="And can you order me a monitor?", session_id="s1"))
    assert second.answer == rag.NO_CLAIM
