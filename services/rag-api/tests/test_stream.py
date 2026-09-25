import asyncio

from app import stream


def test_event_format():
    assert stream.format_event(
        {"id": 7, "kind": "ticket.approved", "ref_type": "ticket", "ref_id": "REQ-1", "x": 1}
    ) == (
        'id: 7\nevent: activity\ndata: {"id": 7, "kind": "ticket.approved", "ref_type": "ticket", "ref_id": "REQ-1"}\n\n'
    )


def test_a_stream_that_falls_behind_is_closed(monkeypatch):
    monkeypatch.setattr(stream, "QUEUE_SIZE", 2)

    async def scenario():
        hub = stream.Hub()
        slow = hub.subscribe()
        for n in range(1, 4):
            hub.publish({"id": n})
        return [slow.get_nowait() for _ in range(slow.qsize())], slow in hub._subscribers

    received, still_subscribed = asyncio.run(scenario())
    # The oldest event makes room for the end marker; the browser reconnects and replays from the table
    assert received == [{"id": 2}, None] and not still_subscribed
