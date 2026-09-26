"""Portal ticket actions against a real PostgreSQL (skipped unless TEST_DATABASE_URL is set)."""

import pytest
from conftest import ADMIN_HEADERS

from app import events, knowledge_gaps, notifications, tickets
from app.schemas import TicketCreate, TicketUpdate


@pytest.fixture
def workflow(monkeypatch):
    """Records what the portal tells WF4 instead of calling n8n."""
    calls = []

    def fake(ticket, decision, actor, note):
        calls.append((ticket.ticket_ref, decision, actor, note))
        return True

    monkeypatch.setattr(tickets, "notify_decision", fake)
    return calls


def pending_ticket(title="Install Visual Studio Code", session_id="s1", requester="Alex", channel="chat"):
    ticket = tickets.create(
        TicketCreate(
            title=title,
            category="software",
            requester=requester,
            session_id=session_id,
            payload={"channel": channel},
        ),
        actor=requester,
    )
    tickets.update(ticket.ticket_ref, TicketUpdate(status="classified", actor="assistant", note="software"))
    return tickets.update(
        ticket.ticket_ref,
        TicketUpdate(status="pending_approval", actor="assistant", note="awaiting approval"),
    )


def test_list_filters_pages_and_pending_age(database, admin_client):
    first = pending_ticket("Laptop for the new hire", channel="voice")
    second = pending_ticket("Access to the finance share")
    tickets.update(second.ticket_ref, TicketUpdate(status="approved", actor="n8n"))
    page = admin_client.get("/v1/admin/tickets", params={"status": "pending_approval"}).json()
    assert page["total"] == 1 and page["items"][0]["ticket_ref"] == first.ticket_ref
    item = page["items"][0]
    assert item["channel"] == "voice" and item["pending_since"] and 0 <= item["pending_minutes"] < 1
    assert admin_client.get("/v1/admin/tickets", params={"q": "finance"}).json()["total"] == 1
    assert admin_client.get("/v1/admin/tickets", params={"channel": "voice"}).json()["total"] == 1
    assert admin_client.get("/v1/admin/tickets", params={"requester": "ale"}).json()["total"] == 2
    oldest = admin_client.get("/v1/admin/tickets", params={"order": "oldest", "limit": 1}).json()
    assert oldest["items"][0]["ticket_ref"] == first.ticket_ref and oldest["total"] == 2
    assert admin_client.get("/v1/admin/tickets", params={"page": 3, "limit": 1}).json()["items"] == []


def test_detail_has_sla_conversation_card_and_actions(database, admin_client):
    ticket = pending_ticket()
    tickets.update(
        ticket.ticket_ref,
        TicketUpdate(
            payload={"slack": {"channel": "C1", "ts": "1.2", "permalink": "https://x.slack.com/p1"}}
        ),
    )
    detail = admin_client.get(f"/v1/admin/tickets/{ticket.ticket_ref}").json()
    assert detail["sla"]["level"] == "ok" and detail["sla"]["reminder_minutes"] == 60
    assert detail["slack"]["permalink"] == "https://x.slack.com/p1"
    assert detail["actions"] == ["approve", "reject", "cancel", "edit", "message"]
    assert [e["to_status"] for e in detail["events"]] == ["intake", "classified", "pending_approval"]
    assert detail["conversation"]["session_id"] == "s1"
    assert admin_client.get("/v1/admin/tickets/REQ-999999").status_code == 404


def test_approve_in_the_portal(database, admin_client, workflow):
    ticket = pending_ticket()
    response = admin_client.post(
        f"/v1/admin/tickets/{ticket.ticket_ref}/decision",
        json={"decision": "approved", "note": "fine"},
        headers=ADMIN_HEADERS,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["workflow_notified"] is True
    after = body["ticket"]
    assert after["status"] == "approved" and after["approver"] == "Dana"
    assert after["payload"]["decision"]["via"] == "portal" and after["payload"]["decision"]["by"] == "Dana"
    assert after["actions"] == ["cancel", "fulfil", "edit", "message"]
    assert workflow == [(ticket.ticket_ref, "approved", "Dana", "fine")]
    # The requester hears who decided
    notice = notifications.pending("s1")
    assert [n.kind for n in notice] == ["ticket_update"] and "approved by Dana" in notice[0].text
    # The feed and the audit log
    kinds = [e["kind"] for e in events.recent(limit=10)]
    assert (
        kinds[0] == "ticket.approved"
        and "ticket.approval_requested" in kinds
        and "ticket.classified" in kinds
    )
    audit = admin_client.get("/v1/admin/audit", params={"action": "ticket.approve"}).json()["items"][0]
    assert audit["before"]["status"] == "pending_approval" and audit["after"]["status"] == "approved"
    assert audit["target_id"] == ticket.ticket_ref and audit["actor"] == "Dana"
    # A second decision, from the portal or a late Slack click, is refused
    again = admin_client.post(
        f"/v1/admin/tickets/{ticket.ticket_ref}/decision",
        json={"decision": "rejected", "note": "no"},
        headers=ADMIN_HEADERS,
    )
    assert again.status_code == 409
    with pytest.raises(tickets.TicketError) as late:
        tickets.update(ticket.ticket_ref, TicketUpdate(status="rejected", actor="mo", via="slack"))
    assert late.value.status_code == 409


def test_reject_needs_a_reason_the_requester_hears(database, admin_client, workflow):
    ticket = pending_ticket()
    url = f"/v1/admin/tickets/{ticket.ticket_ref}/decision"
    assert admin_client.post(url, json={"decision": "rejected"}, headers=ADMIN_HEADERS).status_code == 422
    assert (
        admin_client.post(url, json={"decision": "rejected", "note": "  "}, headers=ADMIN_HEADERS).status_code
        == 422
    )
    response = admin_client.post(
        url, json={"decision": "rejected", "note": "use the licensed editor"}, headers=ADMIN_HEADERS
    )
    assert response.json()["ticket"]["status"] == "rejected"
    text = notifications.pending("s1")[0].text
    assert "rejected by Dana" in text and "use the licensed editor" in text
    assert workflow[0][1] == "rejected"


def test_cancel_fulfil_and_the_state_machine(database, admin_client, workflow):
    ticket = pending_ticket()
    ref = ticket.ticket_ref
    assert (
        admin_client.post(f"/v1/admin/tickets/{ref}/fulfil", json={}, headers=ADMIN_HEADERS).status_code
        == 409
    )
    tickets.update(ref, TicketUpdate(status="approved", actor="mo", via="slack"))
    fulfilled = admin_client.post(f"/v1/admin/tickets/{ref}/fulfil", json={}, headers=ADMIN_HEADERS).json()
    assert fulfilled["ticket"]["status"] == "fulfilled" and fulfilled["workflow_notified"] is None
    assert (
        admin_client.post(f"/v1/admin/tickets/{ref}/cancel", json={}, headers=ADMIN_HEADERS).status_code
        == 409
    )

    other = pending_ticket("Parking badge", session_id="s2")
    cancelled = admin_client.post(
        f"/v1/admin/tickets/{other.ticket_ref}/cancel", json={"note": "duplicate"}, headers=ADMIN_HEADERS
    ).json()
    assert cancelled["ticket"]["status"] == "cancelled" and cancelled["ticket"]["actions"] == ["message"]
    assert workflow == [(other.ticket_ref, "cancelled", "Dana", "duplicate")]
    assert "was cancelled" in notifications.pending("s2")[0].text


def test_edit_priority_and_category(database, admin_client):
    ticket = pending_ticket()
    url = f"/v1/admin/tickets/{ticket.ticket_ref}"
    assert admin_client.patch(url, json={}, headers=ADMIN_HEADERS).status_code == 422
    assert admin_client.patch(url, json={"category": "spaceship"}, headers=ADMIN_HEADERS).status_code == 422
    edited = admin_client.patch(
        url,
        json={"priority": "high", "category": "hardware", "note": "blocking a release"},
        headers=ADMIN_HEADERS,
    ).json()["ticket"]
    assert (
        edited["priority"] == "high"
        and edited["category"] == "hardware"
        and edited["status"] == "pending_approval"
    )
    assert (
        edited["events"][-1]["note"]
        == "priority normal → high; category software → hardware. blocking a release"
    )
    assert events.recent(kind="ticket.updated")[0]["actor"] == "Dana"
    # Edits are not announced to the requester
    assert notifications.pending("s1") == []


def test_message_to_the_requester(database, admin_client, workflow):
    ticket = pending_ticket()
    ref = ticket.ticket_ref
    admin_client.post(
        f"/v1/admin/tickets/{ref}/decision", json={"decision": "approved"}, headers=ADMIN_HEADERS
    )
    response = admin_client.post(
        f"/v1/admin/tickets/{ref}/message",
        json={"text": "  The laptop arrives on Monday. "},
        headers=ADMIN_HEADERS,
    )
    assert response.status_code == 200
    # The message does not supersede the decision notice, or the reverse
    notices = {n.kind: n.text for n in notifications.pending("s1")}
    assert set(notices) == {"ticket_update", "admin_message"}
    assert (
        notices["admin_message"]
        == f"A message from Dana about your request {ref}: The laptop arrives on Monday."
    )
    assert events.recent(kind="ticket.message")[0]["detail"] == "The laptop arrives on Monday."
    loner = tickets.create(TicketCreate(title="Filed by n8n"))
    assert (
        admin_client.post(
            f"/v1/admin/tickets/{loner.ticket_ref}/message", json={"text": "hi"}, headers=ADMIN_HEADERS
        )
    ).status_code == 409


def test_sla_escalation_goes_through_update(database):
    ticket = pending_ticket()
    result = knowledge_gaps.escalate_ticket(ticket.ticket_ref, "normal")
    assert result["new_priority"] == "high"
    after = tickets.get(ticket.ticket_ref)
    assert after.priority == "high" and after.events[-1].actor == "sla-escalation"
    event = events.recent(kind="ticket.escalated")[0]
    assert event["severity"] == "warning" and event["ref_id"] == ticket.ticket_ref


def test_overview_counts(database, admin_client):
    pending_ticket()
    stuck = pending_ticket("Monitor")
    tickets.update(stuck.ticket_ref, TicketUpdate(status="approved", actor="n8n"))
    from app import memory

    memory.run(
        "UPDATE tickets SET updated_at = now() - interval '10 minutes' WHERE ticket_ref = %s",
        (stuck.ticket_ref,),
    )
    overview = admin_client.get("/v1/admin/overview").json()
    assert overview["pending"]["count"] == 1 and overview["pending"]["oldest_minutes"] < 1
    assert overview["approved_not_fulfilled"] == {"count": 1, "oldest_ref": stuck.ticket_ref}
    assert overview["tickets_last_7_days"] == {"pending_approval": 1, "approved": 1}
    assert overview["recent_activity"][0]["kind"] == "ticket.approved"
    assert overview["sla"] == {"reminder_minutes": 60, "escalation_minutes": 240}
