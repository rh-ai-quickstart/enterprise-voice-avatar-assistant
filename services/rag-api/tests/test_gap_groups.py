"""Grouping knowledge gaps by meaning, without a database."""

from datetime import UTC, datetime, timedelta

from app import knowledge_gaps

NOW = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)


def gap(id, question, vector=None, score=0.2, reason="low_score", minutes=0):
    return {
        "id": id,
        "session_id": f"s{id}",
        "question": question,
        "top_score": score,
        "hit_count": 1,
        "reason": reason,
        "status": "open",
        "created_at": NOW - timedelta(minutes=minutes),
        "embedding": vector,
    }


def test_groups_by_wording_then_by_meaning():
    reset = [1.0, 0.0, 0.0]
    near_reset = [0.95, 0.2, 0.0]  # cosine 0.98 with reset
    parking = [0.0, 1.0, 0.0]
    rows = [
        gap(1, "How do I reset my password?", reset, score=0.1, reason="no_hits", minutes=50),
        gap(2, "how do I reset my password", reset, minutes=40),
        gap(3, "How do I reset my  password?!", None, minutes=30),  # same wording, no embedding
        gap(4, "Password reset procedure", near_reset, score=0.4, minutes=20),
        gap(5, "Where can I park?", parking, minutes=10),
        gap(6, "Expense limits for travel", None, minutes=5),
    ]
    groups = knowledge_gaps.group(rows, threshold=0.85)
    assert [(g["count"], g["wordings"]) for g in groups] == [(4, 2), (1, 1), (1, 1)]
    top = groups[0]
    assert top["question"] in {
        "How do I reset my password?",
        "how do I reset my password",
        "How do I reset my  password?!",
    }
    assert top["ids"] == [4, 3, 2, 1] and top["best_score"] == 0.4
    assert top["reasons"] == ["low_score", "no_hits"]
    assert top["first_seen"] == NOW - timedelta(minutes=50) and top["last_seen"] == NOW - timedelta(
        minutes=20
    )
    assert "embedding" not in top["gaps"][0]
    assert {g["question"] for g in groups[1:]} == {"Where can I park?", "Expense limits for travel"}


def test_threshold_keeps_unrelated_questions_apart():
    rows = [gap(1, "reset password", [1.0, 0.0]), gap(2, "password reset", [0.8, 0.6])]  # cosine 0.8
    assert len(knowledge_gaps.group(rows, threshold=0.85)) == 2
    assert len(knowledge_gaps.group(rows, threshold=0.75)) == 1


def test_no_gaps_no_groups():
    assert knowledge_gaps.group([]) == []
