from app.events import CREATED, REMOVED, parse_s3_event


def test_records_are_parsed_and_keys_decoded():
    event = {
        "EventName": "s3:ObjectCreated:Put",
        "Key": "documents/policies/leave+policy.pdf",
        "Records": [
            {
                "eventName": "s3:ObjectCreated:Put",
                "s3": {"bucket": {"name": "documents"}, "object": {"key": "policies/leave+policy.pdf"}},
            }
        ],
    }
    assert parse_s3_event(event) == [(CREATED, "documents", "policies/leave policy.pdf")]


def test_removed_events_are_recognised():
    event = {"EventName": "s3:ObjectRemoved:Delete", "Records": [
        {"s3": {"bucket": {"name": "documents"}, "object": {"key": "old.pdf"}}}]}
    assert parse_s3_event(event) == [(REMOVED, "documents", "old.pdf")]


def test_top_level_key_is_a_fallback():
    assert parse_s3_event({"EventName": "s3:ObjectCreated:Put", "Key": "inbox/invoice.pdf"}) == [
        (CREATED, "inbox", "invoice.pdf")
    ]


def test_unrelated_events_are_ignored():
    assert parse_s3_event({"EventName": "s3:ObjectAccessed:Get", "Key": "documents/x.pdf"}) == []
